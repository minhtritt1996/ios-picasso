#!/usr/bin/env python3
"""Wrap a flat arm64 image in a static Mach-O executable.

ld64.lld cannot emit a static (dyld-free) arm64 executable, and XNU accepts one
only with MH_DYLDLINK clear and an LC_UNIXTHREAD carrying the entry state (this
is a DEVELOPMENT kernel, where static binaries are allowed). So: link the code
with an ELF linker at the address it will live at, dump the raw bytes, and write
the Mach-O header by hand.

  mkmacho.py RAW_BIN ENTRY_OFFSET SIZE_OUT
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
CSMAGIC_EMBEDDED_SIGNATURE = 0xfade0cc0
CSMAGIC_CODEDIRECTORY = 0xfade0c02
CSSLOT_CODEDIRECTORY = 0
CS_ADHOC = 0x2
ARM_THREAD_STATE64 = 6
VM_PROT_READ = 1
VM_PROT_EXECUTE = 4

BASE = 0x100000000
LC_AREA = 0x1000              # space reserved for the load commands


def seg(name, vmaddr, vmsize, fileoff, filesize, maxprot, initprot):
    return struct.pack('<II16sQQQQIIII', LC_SEGMENT_64, 72, name.encode(),
                       vmaddr, vmsize, fileoff, filesize, maxprot, initprot, 0, 0)


def unixthread(pc):
    x = [0] * 29
    fp = lr = sp = 0
    cpsr = 0
    state = struct.pack('<29Q4QII', *x, fp, lr, sp, pc, cpsr, 0)
    payload = struct.pack('<II', ARM_THREAD_STATE64, len(state) // 4) + state
    return struct.pack('<II', LC_UNIXTHREAD, 8 + len(payload)) + payload


def build_version():
    # platform iossimulator=7, minos 14.0, sdk 14.0
    return struct.pack('<IIIIII', LC_BUILD_VERSION, 24, 7, 14 << 16, 14 << 16, 0)


def adhoc_signature(image, ident):
    """Ad-hoc SuperBlob + CodeDirectory (SHA-256, 4 KiB pages) for `image`.

    arm64 iOS refuses unsigned code on enforcing configurations; an ad-hoc
    signature (no authority, CS_ADHOC) is the cheapest way to look signed.
    """
    page = 4096
    paginated = image + b'\0' * ((-len(image)) % page)
    slots = [hashlib.sha256(paginated[i:i + page]).digest()
             for i in range(0, len(paginated), page)]
    ident_b = ident.encode() + b'\0'
    # CS_CodeDirectory, version 0x20100:
    #   magic length version flags hashOffset identOffset nSpecialSlots
    #   nCodeSlots codeLimit  (9 * 4)  hashSize hashType platform pageSize (4)
    #   spare2 scatterOffset  (2 * 4)  -> identifier at 48
    ident_offset = 48
    hash_offset = ident_offset + len(ident_b)
    cd_len = hash_offset + 32 * len(slots)
    cd = b''.join([
        struct.pack('>IIIIIIIII', CSMAGIC_CODEDIRECTORY, cd_len, 0x20100, CS_ADHOC,
                    hash_offset, ident_offset, 0, len(slots), len(image)),
        struct.pack('>BBBB', 32, 2, 0, 12),     # hashSize, hashType=sha256, platform, pageSize
        struct.pack('>I', 0),                   # spare2
        struct.pack('>I', 0),                   # scatterOffset
        ident_b,
        b''.join(slots),
    ])
    assert len(cd) == cd_len, (len(cd), cd_len)
    blob = struct.pack('>III', CSMAGIC_EMBEDDED_SIGNATURE, 20 + len(cd), 1) + \
        struct.pack('>II', CSSLOT_CODEDIRECTORY, 20) + cd
    return blob


def main():
    raw_path, out_path = sys.argv[1], sys.argv[2]
    entry_off = 0
    sign = len(sys.argv) > 3 and sys.argv[3] == '--sign'
    code = open(raw_path, 'rb').read()
    pc = BASE + LC_AREA + entry_off

    image = b''.join([
        struct.pack('<II', 0, 0),           # placeholder replaced below
    ])
    segs = [
        seg('__PAGEZERO', 0, BASE, 0, 0, 0, 0),
        seg('__TEXT', BASE, LC_AREA + len(code), 0, LC_AREA + len(code),
            VM_PROT_READ | VM_PROT_EXECUTE, VM_PROT_READ | VM_PROT_EXECUTE),
        build_version(),
        unixthread(pc),
    ]
    cmds = b''.join(segs)
    if len(cmds) > LC_AREA:
        sys.exit('load commands do not fit: %d > %d' % (len(cmds), LC_AREA))

    ncmds = 4 if not sign else 5
    sizeofcmds = len(cmds) + (16 if sign else 0)
    header = struct.pack('<IiiIIIII', MAGIC, CPU_TYPE_ARM64, CPU_SUBTYPE_ARM64_ALL,
                         MH_EXECUTE, ncmds, sizeofcmds, MH_NOUNDEFS, 0)
    blob = header + cmds
    blob += b'\0' * (LC_AREA - len(blob))
    if sign:
        # reserve the 16 bytes of LC_CODE_SIGNATURE inside the load-command area
        blob = blob[:32] + cmds + b'\0' * 16 + blob[32 + len(cmds):]
    body = blob + code

    if sign:
        # The signature size depends only on dataoff (one hash per 4 KiB page)
        # and dataoff only on the bytes before it, so a single pass is enough.
        dataoff = len(body) + ((-len(body)) % 16)
        image = body + b'\0' * (dataoff - len(body))
        sig = adhoc_signature(image, 'com.picasso.sweetsh')
        lc = struct.pack('<IIII', LC_CODE_SIGNATURE, 16, dataoff, len(sig))
        image = image[:32] + cmds + lc + image[32 + len(cmds) + 16:]
        assert len(image) == dataoff, (len(image), dataoff)
        sig = adhoc_signature(image, 'com.picasso.sweetsh')     # LC bytes final
        body = image + sig

    with open(out_path, 'wb') as f:
        f.write(body)
    print('%s: %d bytes, entry 0x%x%s' % (out_path, len(body), pc, ', ad-hoc signed' if sign else ''))


if __name__ == '__main__':
    main()
