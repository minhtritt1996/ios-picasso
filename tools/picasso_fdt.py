#!/usr/bin/env python3
"""Flattened device tree reader: the parser used by the picasso tools.

  tools/picasso-fdt.py DTB [--json]

The shim needs these numbers before it can boot XNU: where the RAM banks are,
which regions are carved out, where the bootloader's splash framebuffer lives
and with which stride, and the GIC/UFS/USB addresses. On picasso (SM7250)
the static DTB has `reg = <0 0 0 0>` for /memory, because the bootloader fills
it in at boot; run this against the *live* tree (pulled from a running device)
to see the real layout.

Pure Python, no dependencies. Also usable as a module by other tools.
"""
import json
import struct
import sys

FDT_MAGIC = 0xD00DFEED
BEGIN_NODE, END_NODE, PROP, NOP, END = 1, 2, 3, 4, 9


class Node:
    def __init__(self, name, parent=None):
        self.name = name
        self.parent = parent
        self.props = {}          # name -> bytes
        self.children = []

    def path(self):
        parts = []
        n = self
        while n is not None and n.name:
            parts.append(n.name)
            n = n.parent
        return '/' + '/'.join(reversed(parts))

    def get(self, name):
        return self.props.get(name)

    def u32(self, name, default=None):
        v = self.props.get(name)
        if v is None or len(v) % 4:
            return default
        return struct.unpack('>%dI' % (len(v) // 4), v)

    def u64(self, name, default=None):
        v = self.props.get(name)
        if v is None or len(v) % 8:
            return default
        return struct.unpack('>%dQ' % (len(v) // 8), v)

    def s(self, name, default=None):
        v = self.props.get(name)
        if v is None:
            return default
        return v.rstrip(b'\0').decode('utf-8', 'replace')

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def find(self, pred):
        for n in self.walk():
            if pred(n):
                return n
        return None

    def find_by_compatible(self, compat):
        want = compat.encode()
        for n in self.walk():
            c = n.get('compatible')
            if c is None:
                continue
            if want in [x.rstrip(b'\0') for x in c.split(b'\0') if x]:
                return n
        return None

    def cells(self, name, default=None):
        v = self.props.get(name)
        if v is None:
            return default
        return struct.unpack('>%dI' % (len(v) // 4), v)


def parse(buf):
    """Parse a flattened device tree into a Node tree."""
    if len(buf) < 40:
        raise SystemExit('too short to be a device tree')
    magic, totalsize, off_struct, off_strings, off_rsvmap, version, last_comp, boot_cpuid, size_strings, size_struct = \
        struct.unpack('>10I', buf[:40])
    if magic != FDT_MAGIC:
        raise SystemExit('bad magic %#x (not a device tree)' % magic)
    strings = buf[off_strings:off_strings + size_strings]

    def name_at(off):
        end = strings.index(b'\0', off)
        return strings[off:end].decode('utf-8', 'replace')

    root = Node('')
    cur = root
    p = off_struct
    end = off_struct + size_struct
    while p < end:
        (tok,) = struct.unpack('>I', buf[p:p + 4])
        p += 4
        if tok == BEGIN_NODE:
            endn = buf.index(b'\0', p)
            nm = buf[p:endn].decode('utf-8', 'replace')
            p = (endn + 4) & ~3
            node = Node(nm, cur)
            cur.children.append(node)
            cur = node
        elif tok == END_NODE:
            if cur.parent is None:
                break
            cur = cur.parent
        elif tok == PROP:
            plen, noff = struct.unpack('>II', buf[p:p + 8])
            p += 8
            data = buf[p:p + plen]
            p = (p + plen + 3) & ~3
            cur.props[name_at(noff)] = data
        elif tok == NOP:
            continue
        elif tok == END:
            break
        else:
            raise SystemExit('bad FDT token %d at %#x' % (tok, p - 4))
    return root, dict(magic=magic, totalsize=totalsize, version=version,
                      rsvmap=off_rsvmap, boot_cpuid=boot_cpuid)


def parse_rsvmap(buf, off):
    """[(base, size)] from the memory reservation block at `off`."""
    out = []
    p = off
    while p + 16 <= len(buf):
        base, size = struct.unpack('>QQ', buf[p:p + 16])
        p += 16
        if base == 0 and size == 0:
            break
        out.append((base, size))
    return out


def reg_entries(node, root=None):
    """[(base, size)] from a node's `reg`, using the parent's address/size cells."""
    if node is None:
        return []
    reg = node.get('reg')
    if reg is None:
        return []
    parent = node.parent
    ac = 2
    sc = 1
    if parent is not None:
        a = parent.cells('#address-cells')
        s = parent.cells('#size-cells')
        ac = a[0] if a else 2
        sc = s[0] if s else 1
    n = len(reg) // 4
    if ac + sc == 0 or n % (ac + sc):
        return []
    out = []
    for i in range(0, n, ac + sc):
        base = 0
        for j in range(ac):
            base = (base << 32) | struct.unpack('>I', reg[(i + j) * 4:(i + j) * 4 + 4])[0]
        size = 0
        for j in range(sc):
            size = (size << 32) | struct.unpack('>I', reg[(i + ac + j) * 4:(i + ac + j) * 4 + 4])[0]
        out.append((base, size))
    return out


