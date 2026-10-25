#!/usr/bin/env python3
"""Wrap a flat arm64 image in a static Mach-O executable.

ld64.lld cannot emit a static (dyld-free) arm64 executable, and XNU accepts one
only with MH_DYLDLINK clear and an LC_UNIXTHREAD carrying the entry state (this
is a DEVELOPMENT kernel, where static binaries are allowed). So: link the code
with an ELF linker at the address it will live at, dump the raw bytes, and write
the Mach-O header by hand.

  mkmacho.py RAW_BIN ENTRY_OFFSET SIZE_OUT
"""
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


def main():
    raw_path, entry_off, out_path = sys.argv[1], int(sys.argv[2], 0), sys.argv[3]
    code = open(raw_path, 'rb').read()
    pc = BASE + LC_AREA + entry_off

    cmds = b''.join([
        seg('__PAGEZERO', 0, BASE, 0, 0, 0, 0),
        seg('__TEXT', BASE, LC_AREA + len(code), 0, LC_AREA + len(code),
            VM_PROT_READ | VM_PROT_EXECUTE, VM_PROT_READ | VM_PROT_EXECUTE),
        build_version(),
        unixthread(pc),
    ])
    if len(cmds) > LC_AREA:
        sys.exit('load commands do not fit: %d > %d' % (len(cmds), LC_AREA))

    header = struct.pack('<IiiIIIII', MAGIC, CPU_TYPE_ARM64, CPU_SUBTYPE_ARM64_ALL,
                         MH_EXECUTE, 4, len(cmds), MH_NOUNDEFS, 0)
    blob = header + cmds
    blob += b'\0' * (LC_AREA - len(blob))
    out = blob + code
    with open(out_path, 'wb') as f:
        f.write(out)
    print('%s: %d bytes, entry 0x%x (offset %d)' % (out_path, len(out), pc, entry_off))


if __name__ == '__main__':
    main()
