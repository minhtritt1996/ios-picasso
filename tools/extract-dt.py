#!/usr/bin/env python3
"""Extract the device tree from an Apple IMG4 file (an .im4p from an IPSW).

  tools/extract-dt.py INPUT.im4p OUT.adt

The container is a sequence of length-prefixed strings (the magic, the type,
the tag name, the version, the compression) followed by the payload. When the
compression is "bvx2" the payload is an LZFSE stream, which is what the
device tree files use. The result is Apple's device tree (ADT) format, the
one tools/adt.py reads.
"""
import struct
import sys


def strings(buf):
    """The length-prefixed strings of the header, in order."""
    out = []
    p = 0
    while p + 4 <= len(buf):
        (n,) = struct.unpack_from('>I', buf, p)
        p += 4
        if n == 0 or p + n > len(buf):
            break
        out.append(buf[p:p + n].rstrip(b'\0').decode('utf-8', 'replace'))
        p += n
    return out, p


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(1)
    buf = open(sys.argv[1], 'rb').read()
    # The header is a sequence of length-prefixed strings; the payload starts
    # right after the compression name, which for a device tree is "bvx2".
    i = buf.find(b'bvx2')
    if i < 0:
        print(f'{sys.argv[1]}: no IMG4 payload found', file=sys.stderr)
        raise SystemExit(1)
    payload = buf[i:]
    if True:
        try:
            import liblzfse
        except ImportError:
            print('the payload is LZFSE compressed: pip install --user pyliblzfse',
                  file=sys.stderr)
            raise SystemExit(1)
        payload = liblzfse.decompress(payload)
    elif compression not in ('', 'raw'):
        print(f'unknown compression "{compression}"', file=sys.stderr)
        raise SystemExit(1)
    open(sys.argv[2], 'wb').write(payload)
    print(f'{sys.argv[1]}: {len(payload)} bytes -> {sys.argv[2]}', file=sys.stderr)


if __name__ == '__main__':
    main()
