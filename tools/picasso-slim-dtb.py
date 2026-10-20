#!/usr/bin/env python3
"""Build a small device tree for the shim out of the phone's own tree.

  tools/picasso-slim-dtb.py IN.dtb OUT.dtb

The shim needs a handful of nodes (the RAM banks the bootloader fills in, the
carve-outs, the GIC, the timer, the UFS host, USB, the console UART, the
display pipe and the CPU MPIDRs). The full device tree of the phone is 1.4 MB,
and this bootloader refuses to `fastboot boot` an image larger than 256 KB,
so the tree has to be pruned to fit next to the shim.

Everything that is dropped is still in the original tree; this is a copy for
the shim, not a replacement for the phone's own tree.
"""
import struct
import sys

sys.path.insert(0, __import__('os').path.dirname(__file__))
from picasso_fdt import parse, Node  # noqa: E402

FDT_MAGIC = 0xD00DFEED
FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9

# Nodes we keep, by the "compatible" property.
KEEP_COMPATIBLE = (
    b'arm,gic-v3', b'arm,gic-v4', b'arm,armv8-timer', b'qcom,ufshc',
    b'snps,dwc3', b'qcom,msm-geni-console', b'qcom,sde-kms',
)
# Properties that must survive on an ancestor (cell counts, the name).
KEEP_PROPS = (b'#address-cells', b'#size-cells', b'compatible', b'name',
              b'device_type', b'reg', b'bootargs', b'linux,initrd-start',
              b'linux,initrd-end', b'ranges', b'phandle')


def has_compatible(node, want):
    c = node.get('compatible')
    if c is None:
        return False
    return want in [x.rstrip(b'\0') for x in c.split(b'\0') if x]


def mark_all(node, keep):
    """Keep a node and everything below it."""
    keep.add(id(node))
    for c in node.children:
        mark_all(c, keep)


def mark(node, keep):
    """Mark the nodes we want to keep; returns True if this node is kept."""
    kept = False
    # whole subtrees
    if node.name in ('reserved-memory', 'cpus'):
        mark_all(node, keep)
        return True
    if node.name in ('memory', 'chosen'):
        kept = True
    if node.get('device_type') == b'memory\0':
        kept = True
    if any(has_compatible(node, c) for c in KEEP_COMPATIBLE):
        kept = True
    if node.get('qcom,mdss-dsi-panel-width') is not None:
        kept = True
    for c in node.children:
        if mark(c, keep):
            kept = True
    if kept:
        keep.add(id(node))
    return kept


def collect(node, keep, out, root=False):
    """Copy the kept part of the tree into `out`."""
    for c in node.children:
        if id(c) not in keep:
            continue
        child = Node(c.name)
        # A kept node keeps all its properties; an ancestor only the ones
        # that describe the path (cell counts, the name).
        for k, v in c.props.items():
            if id(c) in keep or k in KEEP_PROPS:
                child.props[k] = v
        collect(c, keep, child)
        out.children.append(child)


def emit(root, rsvmap):
    """Serialize the tree as a flattened device tree."""
    strings = bytearray()
    stroff = {}

    def stroff_of(name):
        if name not in stroff:
            stroff[name] = len(strings)
            strings.extend(name.encode() + b'\0')
        return stroff[name]

    struct_block = bytearray()

    def emit_node(node):
        struct_block.extend(struct.pack('>I', FDT_BEGIN_NODE))
        nm = node.name.encode() + b'\0'
        struct_block.extend(nm)
        struct_block.extend(b'\0' * ((-len(nm)) & 3))
        for k, v in node.props.items():
            if k == 'name':
                continue
            struct_block.extend(struct.pack('>III', FDT_PROP, len(v), stroff_of(k)))
            struct_block.extend(v)
            struct_block.extend(b'\0' * ((-len(v)) & 3))
        for c in node.children:
            emit_node(c)
        struct_block.extend(struct.pack('>I', FDT_END_NODE))

    emit_node(root)
    struct_block.extend(struct.pack('>I', FDT_END))

    rsv = bytearray()
    for base, size in rsvmap:
        rsv.extend(struct.pack('>QQ', base, size))
    rsv.extend(struct.pack('>QQ', 0, 0))

    hdr_size = 40
    off_rsv = hdr_size
    off_struct = off_rsv + len(rsv)
    off_strings = off_struct + len(struct_block)
    total = off_strings + len(strings)

    hdr = struct.pack('>10I', FDT_MAGIC, total, off_struct, off_strings, off_rsv,
                      17, 16, 0, len(strings), len(struct_block))
    return bytes(hdr) + bytes(rsv) + bytes(struct_block) + bytes(strings)


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(1)
    buf = open(sys.argv[1], 'rb').read()
    root, meta = parse(buf)
    rsvmap = []
    p = meta['rsvmap']
    while p + 16 <= len(buf):
        base, size = struct.unpack('>QQ', buf[p:p + 16])
        p += 16
        if base == 0 and size == 0:
            break
        rsvmap.append((base, size))

    keep = set()
    mark(root, keep)
    out = Node(root.name)
    for k, v in root.props.items():
        out.props[k] = v
    collect(root, keep, out, root=True)

    blob = emit(out, rsvmap)
    open(sys.argv[2], 'wb').write(blob)
    print('%s: %d bytes (from %d)' % (sys.argv[2], len(blob), len(buf)), file=sys.stderr)


if __name__ == '__main__':
    main()
