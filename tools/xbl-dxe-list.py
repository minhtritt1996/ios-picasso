#!/usr/bin/env python3
"""List (and optionally extract) the UEFI DXE drivers inside a Qualcomm xbl.img.

The xbl partition of a Qualcomm phone is an ELF loader followed by firmware
volumes. The vendor's UEFI driver set (UsbDeviceDxe, UfsDxe, DisplayDxe, ...)
lives inside those volumes, which is what a Mu-Silicium port needs under
Binaries/<codename>.

  tools/xbl-dxe-list.py xbl.img [--extract DIR]

No external tools: EFI_FIRMWARE_VOLUME_HEADER, the FFS file header and the
section stream are parsed by hand, and PE32 sections are written out so their
names can be read with strings.
"""
import argparse
import os
import struct
import sys
import uuid

FV_SIG = b"_FVH"
FFS_HEADER = 24
SECTION_HEADER = 4

# EFI_FV_FILETYPE_*
FILE_TYPES = {
    0x01: "RAW", 0x02: "FREEFORM", 0x03: "SECURITY_CORE", 0x04: "PEI_CORE",
    0x05: "DXE_CORE", 0x06: "PEIM", 0x07: "DRIVER", 0x08: "COMBINED_PEIM_DRIVER",
    0x09: "APPLICATION", 0x0A: "SMM", 0x0B: "FIRMWARE_VOLUME_IMAGE",
    0x0C: "COMBINED_SMM_DXE", 0x0D: "SMM_CORE", 0x0E: "MM_STANDALONE",
    0x0F: "MM_CORE_STANDALONE", 0xF0: "FFS_PAD",
}

# EFI_SECTION_*
SECTION_TYPES = {
    0x01: "COMPRESSION", 0x02: "GUID_DEFINED", 0x03: "DISPOSABLE",
    0x10: "PE32", 0x11: "PIC", 0x12: "TE", 0x13: "DXE_DEPEX",
    0x14: "VERSION", 0x15: "USER_INTERFACE", 0x16: "COMPATIBILITY16",
    0x17: "FIRMWARE_VOLUME_IMAGE", 0x18: "FREEFORM_SUBTYPE_GUID",
    0x19: "RAW", 0x1B: "PEI_DEPEX", 0x1C: "SMM_DEPEX",
}


def guids(data, off):
    return str(uuid.UUID(bytes_le=data[off:off + 16]))


def find_volumes(data):
    """Every FV header: 16 zero bytes, file-system GUID, length, then _FVH."""
    out = []
    i = 0
    while True:
        i = data.find(FV_SIG, i)
        if i < 0:
            break
        start = i - 0x28
        if start >= 0:
            length = struct.unpack_from("<Q", data, start + 0x20)[0]
            if 0x100 <= length <= len(data) - start:
                out.append((start, length))
        i += 4
    return out


def walk_sections(data, off, end, depth, out):
    while off + SECTION_HEADER <= end:
        size = data[off] | (data[off + 1] << 8) | (data[off + 2] << 16)
        stype = data[off + 3]
        if size == 0:
            return
        body = off + SECTION_HEADER
        if size == 0xFFFFFF:      # extended size follows
            size = struct.unpack_from("<I", data, off + 4)[0]
            body = off + 8
        if size < SECTION_HEADER or off + size > end:
            return
        name = SECTION_TYPES.get(stype, hex(stype))
        out.append((depth, name, body, off + size))
        if stype in (0x01, 0x02):  # compressed: children are inside, skip
            out.append((depth + 1, "compressed-data", body, off + size))
        off = (off + size + 3) & ~3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--extract", default=None)
    args = ap.parse_args()

    data = open(args.image, "rb").read()
    vols = find_volumes(data)
    if not vols:
        sys.exit("no firmware volume found")
    print(f"{args.image}: {len(data)} bytes, {len(vols)} volume(s)")

    if args.extract:
        os.makedirs(args.extract, exist_ok=True)

    total = 0
    for vi, (start, length) in enumerate(vols):
        hdr_len = struct.unpack_from("<H", data, start + 0x30)[0]
        print(f"\nFV{vi} at {start:#x} length {length:#x} header {hdr_len:#x}")
        off = start + hdr_len
        end = start + length
        while off + FFS_HEADER <= end:
            g = guids(data, off)
            ftype = data[off + 18]
            size = (data[off + 20] | (data[off + 21] << 8) | (data[off + 22] << 16))
            if data[off:off + 16] == b"\xff" * 16 or size < FFS_HEADER or off + size > end:
                break
            total += 1
            secs = []
            walk_sections(data, off + FFS_HEADER, off + size, 0, secs)
            kinds = ",".join(sorted({s[1] for s in secs})) or "-"
            print(f"  {g}  {FILE_TYPES.get(ftype, hex(ftype)):10s} {size:#8x}  {kinds}")
            if args.extract:
                base = os.path.join(args.extract, f"{vi:02d}-{g}")
                for depth, name, body, send in secs:
                    if name in ("PE32", "TE", "RAW", "FREEFORM_SUBTYPE_GUID"):
                        ext = ".efi" if name == "PE32" else "." + name.lower()
                        path = base + f"-{name.lower()}{ext}"
                        with open(path, "wb") as f:
                            f.write(data[body:send])
            off = (off + size + 7) & ~7
    print(f"\n{total} FFS file(s)")


if __name__ == "__main__":
    main()
