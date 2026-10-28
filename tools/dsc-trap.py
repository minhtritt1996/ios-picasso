#!/usr/bin/env python3
"""Symbolicate an unslid crash PC against the on-device dyld shared cache.

Reads only the slices it needs through the kernel files server's ranged READ
(patch 0183: READ <path> <off> <len>) over the USB link, so a multi-GB cache
never has to be copied off the phone.

  tools/dsc-trap.py --pc 0x19b9eb8a4 [--around 64]

For every cache file (main + subcaches) it reads the 4 KiB header, uses the
mapping table (0x10/0x14) to find the file that maps the address, then the
imagesText table (0x88/0x90) to name the image, and finally the image's own
Mach-O LC_ID_DYLIB as a cross-check, printing a capstone disassembly around
the faulting instruction.
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
MAPPING_SIZE = 32
IMAGES_TEXT_SIZE = 16


def read_remote(remote, off, length, tag):
    """One ranged read through the files server; cached locally by tag."""
    os.makedirs(SCRATCH, exist_ok=True)
    local = os.path.join(SCRATCH, tag)
    if os.path.exists(local) and os.path.getsize(local) == length:
        return open(local, "rb").read()
    cmd = ("cd /tmp && python3 picasso-files.py read '%s' %d %d %s" % (remote, off, length, local))
    out = subprocess.run(SSH + [cmd], capture_output=True, text=True, timeout=240)
    if out.returncode:
        raise RuntimeError("read %s @0x%x: %s" % (remote, off, out.stdout.strip()[-160:]))
    subprocess.run(["sshpass", "-p", os.environ.get("K60_PW", "k60"), "scp",
                    "-o", "StrictHostKeyChecking=no", "-q",
                    "root@" + os.environ.get("K60_HOST", "100.121.254.115") + ":" + local, local],
                   check=True)
    return open(local, "rb").read()


def header(remote, tag):
    blob = read_remote(remote, 0, 4096, tag)
    if not blob.startswith(b"dyld_v1"):
        return None, blob
    h = {
        "mappingOffset": struct.unpack_from("<I", blob, H_MAPPING_OFF)[0],
        "mappingCount": struct.unpack_from("<I", blob, H_MAPPING_CNT)[0],
        "imagesTextOffset": struct.unpack_from("<Q", blob, H_IMAGES_TEXT_OFF)[0],
        "imagesTextCount": struct.unpack_from("<Q", blob, H_IMAGES_TEXT_CNT)[0],
    }
    return h, blob


def mappings(blob, h):
    out = []
    for i in range(min(h["mappingCount"], 64)):
        base = h["mappingOffset"] + i * MAPPING_SIZE
        if base + MAPPING_SIZE > len(blob):
            break
        addr, size, foff, maxp, initp = struct.unpack_from("<QQQII", blob, base)
        out.append((addr, size, foff, initp))
    return out


def images_text(remote, h, tag):
    count = min(h["imagesTextCount"], 4096)
    size = count * IMAGES_TEXT_SIZE + 64
    blob = read_remote(remote, h["imagesTextOffset"], size, tag)
    out = []
    for i in range(count):
        addr, tsize = struct.unpack_from("<QQ", blob, i * IMAGES_TEXT_SIZE)
        if addr:
            out.append((addr, tsize))
    return sorted(out)


def vm_to_file(blob, h, addr):
    for maddr, msize, mfoff, _ in mappings(blob, h):
        if maddr <= addr < maddr + msize:
            return mfoff + (addr - maddr)
    return None


def image_name(remote, blob, h, load_addr, tag):
    """LC_ID_DYLIB of the Mach-O at load_addr."""
    foff = vm_to_file(blob, h, load_addr)
    if foff is None:
        return None
    try:
        blob = read_remote(remote, foff, 4096, tag)
    except RuntimeError:
        return None
    if struct.unpack_from("<I", blob, 0)[0] != 0xfeedfacf:
        return None
    ncmds = struct.unpack_from("<I", blob, 16)[0]
    off = 32
    for _ in range(min(ncmds, 64)):
        cmd, csize = struct.unpack_from("<II", blob, off)
        if cmd == 0xD:  # LC_ID_DYLIB
            name_off = struct.unpack_from("<I", blob, off + 8)[0]
            return blob[off + name_off:].split(b"\0")[0].decode("latin1")
        off += csize
        if off + 8 > len(blob):
            break
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pc", help="unslid fault PC")
    ap.add_argument("--sub", help="fault PC as reported (slid), needs --slide")
    ap.add_argument("--slide", help="slide from the same crash line")
    ap.add_argument("--around", type=int, default=64)
    args = ap.parse_args()
    if args.pc:
        pc = int(args.pc, 0)
    elif args.sub and args.slide:
        pc = int(args.sub, 0) - int(args.slide, 0)
        print("unslid pc 0x%x" % pc)
    else:
        sys.exit("need --pc, or --sub with --slide")
    if pc < 0x100000000:
        sys.exit("pc 0x%x looks slid (unslid shared-cache addresses start at 0x180000000)" % pc)

    files = [(CACHE, "hdr-00.bin")] + \
            [("%s.%02d" % (CACHE, i), "hdr-%02d.bin" % i) for i in range(1, 64)]

    hit = None
    for remote, tag in files:
        try:
            h, blob = header(remote, tag)
        except RuntimeError:
            if remote == CACHE:
                sys.exit("cannot read the cache header")
            break
        if not h:
            break
        for addr, size, foff, initp in mappings(blob, h):
            if addr <= pc < addr + size:
                hit = (remote, tag, h, blob, addr, size, foff, initp)
                break
        if hit:
            break
    if not hit:
        sys.exit("no cache file maps 0x%x" % pc)

    remote, tag, h, blob, maddr, msize, mfoff, mprot = hit
    file_off = mfoff + (pc - maddr)
    print("file: %s  mapping vm 0x%x+0x%x -> file 0x%x prot %x"
          % (os.path.basename(remote), maddr, msize, mfoff, mprot))
    print("pc 0x%x -> file offset 0x%x (page 0x%x)" % (pc, file_off, pc & 0xfff))

    imgs = []
    for remote2, tag2 in files:
        try:
            h2, _ = header(remote2, tag2)
        except RuntimeError:
            continue
        if not h2:
            continue
        imgs.extend(images_text(remote2, h2, "it-%s.bin" % tag2.replace("hdr-", "")))
        if imgs and len(imgs) > 4000:
            break
    imgs.sort()
    img = None
    for i, (addr, tsize) in enumerate(imgs):
        if addr <= pc:
            nxt = imgs[i + 1][0] if i + 1 < len(imgs) else pc + 1
            if pc < nxt:
                img = (addr, nxt - addr)
                break
    if img:
        print("image: load 0x%x text 0x%x (pc+0x%x)" % (img[0], img[1], pc - img[0]))
        name = image_name(remote, blob, h, img[0], "mh-%x.bin" % img[0])
        if name:
            print("image path: %s" % name)

    start = max(0, file_off - args.around)
    start -= start % 4
    blob2 = read_remote(remote, start, (file_off - start) + args.around + 16, "c-%x.bin" % pc)
    try:
        import capstone
    except ImportError:
        sys.exit("bytes: %s" % blob2.hex())
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    md.skipdata = True
    for ins in md.disasm(blob2, start):
        mark = "  <== PC" if ins.address <= file_off < ins.address + ins.size else ""
        print("  %08x: %-10s %s %s%s" % (ins.address, ins.bytes.hex(), ins.mnemonic, ins.op_str, mark))


if __name__ == "__main__":
    main()
