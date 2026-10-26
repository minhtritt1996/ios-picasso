#!/usr/bin/env python3
"""Ship the blocks an offline HFS+ edit changed to the phone (in recovery).

`hfsplus-tool.py IMAGE create ...` prints the changed block ranges. This takes
the same image and those ranges, pushes each range's bytes and dd's them into the
partition, verifying every range with md5 on the device.

  tools/hfsplus-ship.py IMAGE PARTITION START:COUNT [START:COUNT ...]

Example (the ranges create printed for the sweetsh install):
  tools/hfsplus-ship.py /home/tritm/ios.img darwin \\
      0:129 183682:2 1763522:2 3062493:233
"""
import hashlib
import subprocess
import sys
import tempfile

BS = 4096


def adb(*args, **kw):
    return subprocess.run(["adb"] + list(args), capture_output=True, **kw)


def main():
    image, part = sys.argv[1], sys.argv[2]
    ranges = []
    for spec in sys.argv[3:]:
        start, count = spec.split(":")
        ranges.append((int(start), int(count)))
    if not ranges:
        sys.exit(__doc__)
    if b"recovery" not in adb("devices").stdout:
        sys.exit("phone is not in recovery")

    f = open(image, "rb")
    for start, count in ranges:
        f.seek(start * BS)
        blob = f.read(count * BS)
        want = hashlib.md5(blob).hexdigest()
        with tempfile.NamedTemporaryFile(suffix=".blk", delete=False) as tmp:
            tmp.write(blob)
            path = tmp.name
        adb("push", path, "/tmp/blk.bin")
        adb("shell", "dd if=/tmp/blk.bin of=/dev/block/bootdevice/by-name/%s "
            "bs=%d seek=%d conv=notrunc" % (part, BS, start))
        got = adb("shell", "dd if=/dev/block/bootdevice/by-name/%s bs=%d skip=%d "
                  "count=%d 2>/dev/null | md5sum" % (part, BS, start, count))
        got_md5 = got.stdout.decode().split()[0] if got.stdout.strip() else "?"
        status = "ok" if got_md5 == want else "MISMATCH"
        print("  block %-9d %3d blocks %s" % (start, count, status))
        if got_md5 != want:
            return 1
    adb("shell", "sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
