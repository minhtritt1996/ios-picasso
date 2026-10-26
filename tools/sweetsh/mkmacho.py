#!/usr/bin/env python3
"""Wrap a flat arm64 image in a static Mach-O executable.

ld64.lld cannot emit a static (dyld-free) arm64 executable, and XNU accepts one
only with MH_DYLDLINK clear and an LC_UNIXTHREAD carrying the entry state (this is
a DEVELOPMENT kernel, where static binaries are allowed). So: link the code with
an ELF linker at the address it will live at, dump the raw bytes, and write the
Mach-O header by hand.

Things this wrapper has to get right, each of which cost a boot:

* XNU hands a static binary a stack pointer that is only 8-byte aligned; the
  caller's entry stub aligns it before any compiler-generated frame code runs.
* The single segment must be **writable**: the shell keeps globals (reply buffer,
  counters) in the same flat image, and an r-x mapping turns the first store into
  SIGBUS (protection failure) at far = base + filesize.
* `vmsize` must cover .bss, which lives beyond the file bytes: pass the ELF's
  memory extent with --memsize (tools/sweetsh/elfextent.py).
* arm64 iOS uses 16 KiB pages (ARM_PGSHIFT 14): a CodeDirectory with the 4 KiB
  shift invalidates every page, and a signature blob that shares a page with code
  can never match its own hash - so the blob starts on a 16 KiB boundary.
* sizeof(CS_CodeDirectory) in XNU is 108 bytes (the struct carries the fields of
  every later version) and a shorter CodeDirectory is rejected as EBADEXEC, which
  silently drops the signature and makes the kernel remove execute permission.

  mkmacho.py RAW_BIN OUT [--memsize N] [--sign]
"""
import hashlib
import struct
import sys

MAGIC = 0xfeedfacf
CPU_TYPE_ARM64 = 0x0100000c
CPU_SUBTYPE_ARM64_ALL = 0
MH_EXECUTE = 2
MH_NOUNDEFS = 0x1
LC_SEGMENT_64 = 0x19
LC_UNIXTHREAD = 0x5
LC_BUILD_VERSION = 0x32
LC_CODE_SIGNATURE = 0x1d
ARM_THREAD_STATE64 = 6
VM_PROT_READ = 1
VM_PROT_WRITE = 2
VM_PROT_EXECUTE = 4
CSMAGIC_EMBEDDED_SIGNATURE = 0xfade0cc0
CSMAGIC_CODEDIRECTORY = 0xfade0c02
CSSLOT_CODEDIRECTORY = 0
CS_ADHOC = 0x2
PAGE = 16384                      # XNU arm64 iOS page size (ARM_PGSHIFT 14)
CD_MIN = 108                      # sizeof(CS_CodeDirectory) in this XNU

BASE = 0x100000000
LC_AREA = 0x1000                  # space reserved for the load commands
SEG_CMD_SIZE = 72


def seg(name, vmaddr, vmsize, fileoff, filesize, prot):
    return struct.pack('<II16sQQQQIIII', LC_SEGMENT_64, SEG_CMD_SIZE, name.encode(),
                       vmaddr, vmsize, fileoff, filesize, prot, prot, 0, 0)


def unixthread(pc):
    state = struct.pack('<29Q4QII', *([0] * 29), 0, 0, 0, pc, 0, 0)
    payload = struct.pack('<II', ARM_THREAD_STATE64, len(state) // 4) + state
    return struct.pack('<II', LC_UNIXTHREAD, 8 + len(payload)) + payload


def build_version():
    return struct.pack('<IIIIII', LC_BUILD_VERSION, 24, 7, 14 << 16, 14 << 16, 0)


def adhoc_signature(image, ident):
    """Ad-hoc SuperBlob + CodeDirectory (SHA-256, 16 KiB pages) for `image`."""
    page = PAGE
    paginated = image + b'\0' * ((-len(image)) % page)
    slots = [hashlib.sha256(paginated[i:i + page]).digest()
             for i in range(0, len(paginated), page)]
    ident_b = ident.encode() + b'\0'
    ident_offset = 48
    hash_offset = ident_offset + len(ident_b)
    cd_len = max(hash_offset + 32 * len(slots), CD_MIN)
    cd = b''.join([
        struct.pack('>IIIIIIIII', CSMAGIC_CODEDIRECTORY, cd_len, 0x20100, CS_ADHOC,
                    hash_offset, ident_offset, 0, len(slots), len(image)),
        struct.pack('>BBBB', 32, 2, 0, 14),          # sha256, 16 KiB pages
        struct.pack('>I', 0),                        # spare2
        struct.pack('>I', 0),                        # scatterOffset
        ident_b,
        b''.join(slots),
        b'\0' * (cd_len - hash_offset - 32 * len(slots)),
    ])
    body = struct.pack('>IIII', CSMAGIC_EMBEDDED_SIGNATURE, 20 + len(cd), 1,
                       CSSLOT_CODEDIRECTORY) + struct.pack('>I', 20) + cd
    return body


def main():
    raw_path, out_path = sys.argv[1], sys.argv[2]
    args = sys.argv[3:]
    sign = '--sign' in args
    memsize = int(args[args.index('--memsize') + 1], 0) if '--memsize' in args else 0
    code = open(raw_path, 'rb').read()
    pc = BASE + LC_AREA

    text_prot = VM_PROT_READ | VM_PROT_WRITE | VM_PROT_EXECUTE
    # Command layout depends only on whether the signature LC is reserved, so the
    # segment can be built with its final vmsize before the body exists.
    ncmds = 5 if sign else 4
    cmds_len = SEG_CMD_SIZE * 2 + 24 + (8 + 4 + 68 * 4) + (16 if sign else 0)
    filesize = LC_AREA + len(code)
    vmsize = max(filesize, memsize)
    vmsize += (-vmsize) % PAGE
    cmds = b''.join([
        seg('__PAGEZERO', 0, BASE, 0, 0, 0),
        seg('__TEXT', BASE, vmsize, 0, filesize, text_prot),
        build_version(),
        unixthread(pc),
    ])
    if sign:
        cmds += struct.pack('<IIII', LC_CODE_SIGNATURE, 16, 0, 0)
        filesize += 0                      # placeholder lives inside LC_AREA
    header = struct.pack('<IiiIIIII', MAGIC, CPU_TYPE_ARM64, CPU_SUBTYPE_ARM64_ALL,
                         MH_EXECUTE, ncmds, cmds_len, MH_NOUNDEFS, 0)
    blob = header + cmds
    if len(blob) > LC_AREA:
        sys.exit('load commands do not fit: %d > %d' % (len(blob), LC_AREA))
    body = blob + b'\0' * (LC_AREA - len(blob)) + code

    if sign:
        dataoff = len(body) + ((-len(body)) % PAGE)
        image = body + b'\0' * (dataoff - len(body))
        sig = adhoc_signature(image, 'com.picasso.sweetsh')
        lc_off = 32 + cmds_len - 16
        image = image[:lc_off] + struct.pack('<IIII', LC_CODE_SIGNATURE, 16,
                                            dataoff, len(sig)) + image[lc_off + 16:]
        sig = adhoc_signature(image, 'com.picasso.sweetsh')   # LC bytes final
        body = image + sig
        # __TEXT filesize must cover the hashed image, and vmsize the file
        if dataoff > filesize:
            filesize = dataoff
            text = seg('__TEXT', BASE, max(vmsize, filesize), 0, filesize, text_prot)
            body = body[:32 + SEG_CMD_SIZE] + text + body[32 + 2 * SEG_CMD_SIZE:]

    with open(out_path, 'wb') as f:
        f.write(body)
    print('%s: %d bytes, entry 0x%x, vmsize %d, filesize %d, %s%s' % (
        out_path, len(body), pc, vmsize, filesize,
        'rwx', ', ad-hoc signed' if sign else ''))


if __name__ == '__main__':
    main()
