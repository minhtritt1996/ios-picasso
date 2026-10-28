#!/usr/bin/env python3
"""Ship only the blocks that differ between two rootfs images to the phone.

Full-image writes are ~10 minutes because 16 GiB goes over USB. When the phone
already holds BASE.img, only the 4 KiB blocks that differ from TARGET.img need
to be written - normally a few MB (a binary, the launchd cache, catalog/bitmap).

    tools/ios-delta-ship.py BASE.img TARGET.img [--dry-run] [--verify-full]

BASE and TARGET must have the same size. The phone must be in recovery; the
script uses `adb dd seek=<block> conv=notrunc` per run and compares the md5 of
every run it writes. --verify-full reads the whole partition back afterwards.

This is safe for the catalog only because the images are built offline with
hfsplus-tool.py/sweet-root-tweaks.py (consistent trees) and the delta is taken
against the EXACT image that is on the phone - never against a "same-ish" one.
"""
import argparse
import hashlib
import os
import subprocess
import sys
import time

BLK = 4096
CHUNK = 1 << 20          # diff granularity before refining to blocks
PART = "darwin"


def adb(serial, *args, **kw):
    return subprocess.run(["adb", "-s", serial, *args], capture_output=True, text=True, **kw)


def diff_runs(base, target):
    fa, fb = open(base, "rb"), open(target, "rb")
    runs, off = [], 0
    pending = None           # [start_chunk, nchunks]
    while True:
        a = fa.read(CHUNK)
        b = fb.read(CHUNK)
        if not a and not b:
            break
        if a != b:
            if pending is None:
                pending = [off // CHUNK, 1]
            else:
                pending[1] += 1
        else:
            if pending is not None:
                runs.append(pending)
                pending = None
        off += CHUNK
    if pending is not None:
        runs.append(pending)
    # refine each 1 MiB chunk range to 4 KiB block runs
    out = []
    for start, count in runs:
        base_off = start * CHUNK
        fa.seek(base_off)
        fb.seek(base_off)
        n = count * CHUNK
        cur = None
        for i in range(0, n, BLK):
            a = fa.read(BLK)
            b = fb.read(BLK)
            if a != b:
                blk = (base_off + i) // BLK
                if cur and cur[0] + cur[1] == blk:
                    cur[1] += 1
                else:
                    if cur:
                        out.append(tuple(cur))
                    cur = [blk, 1]
            else:
                if cur:
                    out.append(tuple(cur))
                    cur = None
        if cur:
            out.append(tuple(cur))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("target")
    ap.add_argument("--serial", default=os.environ.get("SERIAL", "f9f3d7b0"))
    ap.add_argument("--partition", default=PART)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify-full", action="store_true")
    ap.add_argument("--skip-base-check", action="store_true")
    a = ap.parse_args()

    sb, st = os.path.getsize(a.base), os.path.getsize(a.target)
    if sb != st:
        sys.exit("sizes differ: %d vs %d" % (sb, st))

    t0 = time.time()
    runs = diff_runs(a.base, a.target)
    blocks = sum(n for _, n in runs)
    print("%d runs, %d blocks (%.1f MiB) differ, diff took %.0fs"
          % (len(runs), blocks, blocks * BLK / 1048576, time.time() - t0))
    for start, n in runs[:20]:
        print("  blk %d x%d (%.1f KiB)" % (start, n, n * BLK / 1024))
    if len(runs) > 20:
        print("  ... %d more" % (len(runs) - 20))
    if a.dry_run:
        return

    out = adb(a.serial, "shell", "get-state")
    if "recovery" not in adb(a.serial, "devices").stdout and "device" not in out.stdout:
        sys.exit("phone is not in recovery on this host")

    if not a.skip_base_check:
        # the phone must still hold BASE outside the delta runs, otherwise a
        # partial write would leave a mix of two images
        import random
        random.seed(1)
        nblocks = sb // BLK
        # blocks 0-2 are the HFS volume header: the kernel rewrites them on
        # every mount (dirty/journal flags), and they are always in the delta
        sample = sorted({random.randrange(3, nblocks) for _ in range(303)} | {3, 4})
        fbase = open(a.base, "rb")
        bad = []
        for b in sample:
            fbase.seek(b * BLK)
            want = hashlib.md5(fbase.read(BLK)).hexdigest()
            got = adb(a.serial, "shell",
                      "dd if=/dev/block/bootdevice/by-name/%s bs=%d skip=%d count=1 2>/dev/null | md5sum"
                      % (a.partition, BLK, b)).stdout.split()
            if not got or got[0] != want:
                bad.append(b)
        print("base check: %d/%d sampled blocks match" % (len(sample) - len(bad), len(sample)))
        if bad:
            sys.exit("phone no longer matches BASE at blocks %s - do a full write of TARGET"
                     % bad[:8])

    ft = open(a.target, "rb")
    for i, (start, n) in enumerate(runs, 1):
        ft.seek(start * BLK)
        data = ft.read(n * BLK)
        want = hashlib.md5(data).hexdigest()
        open("/tmp/iosdelta.bin", "wb").write(data)
        adb(a.serial, "push", "/tmp/iosdelta.bin", "/data/local/tmp/iosdelta.bin")
        got = adb(a.serial, "shell",
                  "dd if=/data/local/tmp/iosdelta.bin of=/dev/block/bootdevice/by-name/%s "
                  "bs=%d seek=%d conv=notrunc 2>/dev/null; sync; "
                  "dd if=/dev/block/bootdevice/by-name/%s bs=%d skip=%d count=%d 2>/dev/null | md5sum"
                  % (a.partition, BLK, start, a.partition, BLK, start, n)).stdout.split()
        if not got or got[0] != want:
            sys.exit("run %d (blk %d x%d): %s != %s" % (i, start, n, got[:1], want))
        print("  %d/%d blk %d x%d ok" % (i, len(runs), start, n), flush=True)

    if a.verify_full:
        want = hashlib.md5(open(a.target, "rb").read()).hexdigest()
        got = adb(a.serial, "shell",
                  "dd if=/dev/block/bootdevice/by-name/%s bs=4M count=%d 2>/dev/null | md5sum"
                  % (a.partition, st // (4 << 20))).stdout.split()
        print("full readback: %s (expected %s)" % (got[:1], want))
        if not got or got[0] != want:
            sys.exit("full readback mismatch")
    print("delta shipped: %d blocks" % blocks)


if __name__ == "__main__":
    main()
