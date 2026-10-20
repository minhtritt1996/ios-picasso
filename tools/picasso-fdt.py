#!/usr/bin/env python3
"""Read a (Linux/Apple) flattened device tree and print the SWEET/picasso board facts.

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


def hexs(v):
    return '0x%x' % v


def report(root, buf, verbose=False):
    lines = []

    def out(s=''):
        lines.append(s)

    out('=== identity')
    out('  model          : %s' % (root.s('model', '?')))
    out('  compatible     : %s' % ' '.join(
        x.rstrip(b'\0').decode('utf-8', 'replace')
        for x in (root.get('compatible') or b'').split(b'\0') if x))
    out('  board-id       : %s' % (root.u32('qcom,board-id') or ['?']))

    # --- memory ---------------------------------------------------------
    out()
    out('=== memory')
    mem = None
    for n in root.walk():
        if n.get('device_type') == b'memory\0':
            mem = n
            break
    banks = reg_entries(mem) if mem else []
    if banks and not any(b or s for b, s in banks):
        out('  /memory        : reg = <0 0 0 0>  (bootloader fills this in at boot)')
    else:
        for b, s in banks:
            out('  bank           : %s + %s (%s MiB)' % (hexs(b), hexs(s), s >> 20))
    rsv = []
    rm = root.find(lambda n: n.name == 'reserved-memory')
    if rm is not None:
        for c in rm.children:
            for b, s in reg_entries(c):
                rsv.append((b, s, c.name, c.get('no-map') is not None))
    for b, s, nm, nomap in sorted(rsv):
        out('  reserved       : %s + %-12s %-34s%s' % (hexs(b), hexs(s), nm, ' no-map' if nomap else ''))

    # --- splash / framebuffer ------------------------------------------
    out()
    out('=== display')
    fb = None
    for c in root.walk():
        if c.name.startswith('splash_region') or c.name.startswith('cont_splash'):
            fb = c
            break
    if fb is None:
        for c in root.walk():
            if 'splash' in c.name or 'framebuffer' in c.name:
                fb = c
                break
    if fb is None:
        sfb = root.find_by_compatible('simple-framebuffer')
        if sfb is not None:
            fb = sfb
    if fb is not None:
        for b, s in reg_entries(fb):
            out('  splash region  : %s + %s  (%s)' % (hexs(b), hexs(s), fb.path()))
    else:
        out('  splash region  : NOT in this tree (the bootloader may add it, or it')
        out('                    must be read from the display pipe registers)')
    panel = None
    for n in root.walk():
        w = n.u32('qcom,mdss-dsi-panel-width')
        h = n.u32('qcom,mdss-dsi-panel-height')
        if w and h:
            panel = (n, w[0], h[0], n.u32('qcom,mdss-dsi-panel-framerate'))
            break
    if panel:
        n, w, h, fr = panel
        out('  panel          : %dx%d%s  (%s)' % (
            w, h, (' @%d Hz' % fr[0]) if fr else '', n.path()))
    else:
        out('  panel          : no dsi timing node in this tree')

    # --- gic / timer / ufs / usb ---------------------------------------
    out()
    out('=== interrupt / timer')
    gic = root.find_by_compatible('arm,gic-v3') or root.find_by_compatible('arm,gic-v4')
    if gic is not None:
        regs = reg_entries(gic)
        stride = gic.cells('redistributor-stride')
        out('  gic            : %s  compat=%s' % (
            gic.s('compatible'), gic.path()))
        for b, s in regs:
            out('    reg          : %s + %s' % (hexs(b), hexs(s)))
        if stride:
            out('    redist stride : %#x' % stride[-1])
    else:
        out('  gic            : not found')
    tim = root.find_by_compatible('arm,armv8-timer')
    if tim is not None:
        irqs = tim.cells('interrupts') or []
        ppis = [irqs[i] for i in range(1, len(irqs), 3)]
        out('  timer          : clock %s Hz, PPI phys=%s virt=%s -> vtimer INTID %d' % (
            hexs((tim.u32('clock-frequency') or [0])[0]), ppis[1] if len(ppis) > 1 else '?',
            ppis[2] if len(ppis) > 2 else '?',
            16 + ppis[2] if len(ppis) > 2 else -1))

    out()
    out('=== storage / usb / misc')
    ufs = root.find_by_compatible('qcom,ufshc')
    if ufs is not None:
        for b, s in reg_entries(ufs):
            out('  ufshc          : %s + %s' % (hexs(b), hexs(s)))
    else:
        out('  ufshc          : not found')
    dwc = root.find_by_compatible('snps,dwc3')
    if dwc is not None:
        for b, s in reg_entries(dwc):
            out('  dwc3           : %s + %s' % (hexs(b), hexs(s)))
        ms = dwc.s('maximum-speed')
        if ms:
            out('    max-speed    : %s' % ms)
    else:
        out('  dwc3           : not found')
    spmi = root.find_by_compatible('qcom,spmi')
    if spmi is not None:
        for b, s in reg_entries(spmi):
            out('  spmi           : %s + %s' % (hexs(b), hexs(s)))
    gcc = root.find_by_compatible('qcom,gcc')
    if gcc is not None:
        for b, s in reg_entries(gcc):
            out('  gcc            : %s + %s' % (hexs(b), hexs(s)))
    uart = None
    for n in root.walk():
        if n.s('compatible') == 'qcom,msm-geni-console':
            uart = n
            break
    if uart is None:
        al = root.props.get('aliases')
        if al:
            pass
    if uart is not None:
        for b, s in reg_entries(uart):
            out('  uart console   : %s + %s  (%s)' % (hexs(b), hexs(s), uart.path()))
    else:
        out('  uart console   : no qcom,msm-geni-console node')

    # --- cpus -----------------------------------------------------------
    out()
    out('=== cpus')
    cpus = root.find(lambda n: n.name == 'cpus')
    if cpus is not None:
        n_cpus = 0
        for c in cpus.children:
            if c.get('device_type') != b'cpu\0':
                continue
            reg = c.cells('reg') or []
            n_cpus += 1
            if verbose:
                out('  %-10s reg = %s' % (c.name, ['%#x' % x for x in reg]))
        out('  %d cpu nodes, reg = Aff1<<8 (cluster/core per the tree)' % n_cpus)
    return lines


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    path = sys.argv[1]
    buf = open(path, 'rb').read()
    root, meta = parse(buf)
    rsv = parse_rsvmap(buf, meta['rsvmap'])
    lines = report(root, buf, verbose='--verbose' in sys.argv)
    if rsv:
        head = ['=== /memreserve/ (from the FDT reservation block)']
        for b, s in rsv:
            head.append('  %s + %s' % (hexs(b), hexs(s)))
        head.append('')
        lines = head + lines
    print('\n'.join(lines))
    if '--json' in sys.argv:
        print()
        print(json.dumps({'model': root.s('model'), 'total': meta['totalsize'],
                          'memreserve': rsv}, indent=2))


if __name__ == '__main__':
    main()
