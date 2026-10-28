#!/usr/bin/env python3
"""Symbolicate an unslid crash PC against the on-device dyld shared cache.

Reads only the slices it needs through the kernel files server's ranged READ
(patch 0182: READ <path> <off> <len>) on the USB link, so a multi-GB cache
never has to be copied off the phone.

  tools/dsc-trap.py --pc 0x19b9eb8a4 [--slide 0x3cf54000] [--around 64]

Prints the image (path), the segment mapping that covers the address, and a
capstone disassembly of the bytes around the PC offset.
"""
import argparse
import os
import struct
import subprocess
import sys

CACHE_DIR = "/System/Library/Caches/com.apple.dyld"
CACHE = CACHE_DIR + "/dyld_shared_cache_arm64"
SSH = ["sshpass", "-p", os.environ.get("K60_PW", "k60"), "ssh",
       "-o", "StrictHostKeyChecking=no", "root@" + os.environ.get("K60_HOST", "100.121.254.115")]
SCRATCH = "/tmp/dsc"

H_MAPPING_OFF, H_MAPPING_CNT = 0x10, 0x14
H_IMAGES_TEXT_OFF, H_IMAGES_TEXT_CNT = 0x88, 0x90
H_SUBCACHE_OFF, H_SUBCACHE_CNT = 0x184, 0x188
H_IMAGES_OFF, H_IMAGES_CNT = 0x1bc, 0x1c0
IMAGE_INFO_SIZE = 32
MAPPING_SIZE = 32
IMAGES_TEXT_SIZE = 16


def read_remote(remote, off, length, tag):
    """One ranged read through the files server; cached locally by tag."""
    os.makedirs(SCRATCH, exist_ok=True)
    local = os.path.join(SCRATCH, tag)
    if not os.path.exists(local):
        cmd = ("cd /tmp && python3 picasso-files.py read '%s' %d %d %s" % (remote, off, length, local))
        out = subprocess.run(SSH + [cmd], capture_output=True, text=True, timeout=180)
        if out.returncode or not os.path.exists(local):
            sys.exit("read %s @0x%x failed: %s %s" % (remote, off, out.stdout.strip()[-200:],
                                                       out.stderr.strip()[-200:]))
        subprocess.run(["sshpass", "-p", os.environ.get("K60_PW", "k60"), "scp",
                        "-o", "StrictHostKeyChecking=no", "-q",
                        "root@" + os.environ.get("K60_HOST", "100.121.254.115") + ":" + local, local],
                       check=True)
    return open(local, "rb").read()


def parse_header(blob):
    h = {}
    h["magic"] = blob[0:16].rstrip(b"\0").decode("latin1")
    h["mappingOffset"], h["mappingCount"] = struct.unpack_from("<II", blob, H_MAPPING_OFF)
    h["imagesTextOffset"], h["imagesTextCount"] = struct.unpack_from("<QQ", blob, H_IMAGES_TEXT_OFF)
    h["subCacheArrayOffset"], h["subCacheArrayCount"] = struct.unpack_from("<II", blob, H_SUBCACHE_OFF)
    h["imagesOffset"], h["imagesCount"] = struct.unpack_from("<II", blob, H_IMAGES_OFF)
    return h


def mappings_of(blob, h):
    out = []
    for i in range(h["mappingCount"]):
        base = h["mappingOffset"] + i * MAPPING_SIZE
        addr, size, foff, maxp, initp = struct.unpack_from("<QQQII", blob, base)
        out.append((addr, size, foff, initp))
    return out


def images_of(blob, h, cache_name):
    out = []
    for i in range(h["imagesCount"]):
        base = h["imagesOffset"] + i * IMAGE_INFO_SIZE
        addr, mtime, inode, pathoff = struct.unpack_from("<QQQI", blob, base)
        out.append((addr, cache_name, pathoff))
    return out


def images_text_of(blob, h, cache_name):
    out = []
    for i in range(h["imagesTextCount"]):
        base = h["imagesTextOffset"] + i * IMAGES_TEXT_SIZE
        addr, tsize = struct.unpack_from("<QQ", blob, base)
        out.append((addr, tsize, cache_name))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pc", required=True, help="unslid fault PC")
    ap.add_argument("--around", type=int, default=64, help="bytes of code to show either side")
    args = ap.parse_args()
    pc = int(args.pc, 0)

    main_hdr = read_remote(CACHE, 0, 4096, "hdr-main.bin")
    h = parse_header(main_hdr)
    print("cache: %s" % h["magic"])
    print("images %d, imagesText %d, subcaches %d"
          % (h["imagesCount"], h["imagesTextCount"], h["subCacheArrayCount"]))
    if pc < 0x1000000000:
        sys.exit("pc 0x%x looks slid; pass the unslid address" % pc)

    files = [(0, CACHE)]
    for i in range(min(h["subCacheArrayCount"], 64)):
        files.append((i + 1, "%s.%02d" % (CACHE, i + 1)))

    best = None
    for idx, remote in files:
        try:
            hdr = main_hdr if idx == 0 else read_remote(remote, 0, 4096, "hdr-%02d.bin" % idx)
            if not hdr.startswith(b"dyld_v1"):
                continue
            ih = parse_header(hdr)
        except SystemExit:
            continue
        for start, tsize, _ in images_text_of(hdr, ih, remote):
            if start <= pc < start + max(tsize, 0x1000):
                best = (remote, start, tsize)
        if best:
            break

    cands = []
    for idx, remote in files:
        try:
            hdr = main_hdr if idx == 0 else read_remote(remote, 0, 4096, "hdr-%02d.bin" % idx)
        except SystemExit:
            continue
        if not hdr.startswith(b"dyld_v1"):
            continue
        ih = parse_header(hdr)
        for img in images_of(hdr, ih, remote):
            cands.append(img)

    cands.sort()
    print("images in tables: %d" % len(cands))
    if not best:
        for i, (addr, remote, pathoff) in enumerate(cands):
            if addr <= pc:
                nxt = cands[i + 1][0] if i + 1 < len(cands) else pc + 1
                if pc < nxt:
                    best = (remote, addr, nxt - addr)
                    break
    if not best:
        sys.exit("no image covers 0x%x" % pc)
    remote, img_base, img_size = best

    hdr = main_hdr if remote == CACHE else read_remote(remote, 0, 4096, "hdr2.bin")
    ih = parse_header(hdr)
    name = None
    for addr, _, pathoff in cands:
        if addr == img_base:
            raw = read_remote(remote, pathoff & ~0xfff, 4096, "p-%x.bin" % img_base)
            name = raw[pathoff & 0xfff:].split(b"\0")[0].decode("latin1")
            break
    print("image: 0x%x + 0x%x %s" % (img_base, img_size, name or "?"))

    seg = None
    for addr, size, foff, initp in mappings_of(hdr, ih):
        if addr <= pc < addr + size:
            seg = (addr, size, foff, initp)
    if not seg:
        sys.exit("no mapping covers 0x%x in %s" % (pc, os.path.basename(remote)))
    addr, size, foff, initp = seg
    file_off = foff + (pc - addr)
    print("mapping: vm 0x%x + 0x%x -> file 0x%x (prot %x)" % (addr, size, foff, initp))
    print("code file offset 0x%x, image delta 0x%x" % (file_off, pc - img_base))

    start = max(0, file_off - args.around)
    blob = read_remote(remote, start, args.around * 2 + 16, "c-%x.bin" % pc)
    try:
        import capstone
    except ImportError:
        sys.exit("pip install capstone for disassembly; bytes at 0x%x: %s"
                 % (file_off, blob[:64].hex()))
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    base = start
    for ins in md.disasm(blob, base):
        mark = " <== PC" if ins.address <= file_off < ins.address + ins.size else ""
        print("  %08x: %-10s %s%s" % (ins.address, ins.bytes.hex(), ins.mnemonic + " " + ins.op_str, mark))


if __name__ == "__main__":
    main()
