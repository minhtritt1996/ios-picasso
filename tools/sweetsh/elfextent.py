#!/usr/bin/env python3
"""Print how far an ELF image's *memory* reaches, relative to the link base.

The shell is linked as a flat image and wrapped in a single Mach-O segment whose
vmsize must cover .bss as well as the file bytes: a store past vmsize is a
protection fault (the shell died with SIGBUS at far = base + filesize before this
was handled). Only SHF_ALLOC sections are mapped.
"""
import subprocess
import sys

BASE = 0x100000000


def main():
    readelf, elf = sys.argv[1], sys.argv[2]
    out = subprocess.run([readelf, '-S', '-W', elf], capture_output=True, text=True).stdout
    top = 0
    import re
    pat = re.compile(r'^\s*\[\s*\d+\]\s+(\S+)\s+(\S+)\s+([0-9a-fA-F]+)\s+'
                     r'([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+(\S+)\s*(\S*)')
    for line in out.splitlines():
        m = pat.match(line)
        if not m:
            continue
        flags = m.group(7)
        if 'A' not in flags:          # only mapped sections count
            continue
        addr = int(m.group(3), 16)
        size = int(m.group(5), 16)
        top = max(top, addr + size)
    print(top - BASE if top > BASE else 0)


if __name__ == '__main__':
    main()
