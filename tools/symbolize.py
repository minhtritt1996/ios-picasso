#!/usr/bin/env python3
"""Resolve kernel addresses against a Mach-O kernel (no llvm-nm, no dSYM).

    tools/symbolize.py KERNEL ADDRESS [ADDRESS ...]

XNU's development kernel keeps its LC_SYMTAB (71k symbols for 8796.141.3), but
the usual Linux binutils `nm` cannot read Mach-O and silently prints nothing, so
panics were being read as bare addresses.  The image is PIE, yet the addresses in
a panic report are the unslid link-time VM addresses, which is what this prints.
"""
import bisect
import struct
import sys

MH_MAGIC_64 = 0xfeedfacf
LC_SYMTAB = 0x2
N_STAB = 0xe0
N_TYPE = 0x0e
N_SECT = 0x0e
N_UNDF = 0x0


def load(path):
    with open(path, 'rb') as f:
        d = f.read()
    magic, = struct.unpack_from('<I', d, 0)
    if magic != MH_MAGIC_64:
        sys.exit("%s: not a 64-bit Mach-O" % path)
    ncmds, = struct.unpack_from('<I', d, 16)
    off = 32
    symtab = None
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', d, off)
        if cmd == LC_SYMTAB:
            symtab = struct.unpack_from('<IIII', d, off + 8)
        off += cmdsize
    if symtab is None or symtab[1] == 0:
        sys.exit("%s: no LC_SYMTAB (stripped kernel)" % path)
    symoff, nsyms, stroff, strsize = symtab
    strings = d[stroff:stroff + strsize]
    syms = []
    for i in range(nsyms):
        n_strx, n_type, n_sect, n_desc, n_value = struct.unpack_from(
            '<IBBHQ', d, symoff + 16 * i)
        if n_type & N_STAB or (n_type & N_TYPE) == N_UNDF or n_value == 0:
            continue
        if (n_type & N_TYPE) != N_SECT:
            continue
        end = strings.find(b'\0', n_strx)
        name = strings[n_strx:end].decode('latin-1')
        syms.append((n_value, name))
    syms.sort()
    return [s[0] for s in syms], syms


def resolve(addrs, syms):
    for a in addrs:
        i = bisect.bisect_right(addrs, a) - 1
        if i < 0:
            print("%#x: below the first symbol" % a)
            continue
        base, name = syms[i]
        print("%#x: %s+%#x" % (a, name, a - base))


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__.strip())
    addrs, syms = load(sys.argv[1])
    vals = []
    for arg in sys.argv[2:]:
        vals.append(int(arg, 0))
    resolve(vals, syms)


if __name__ == '__main__':
    main()
