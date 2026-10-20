#!/usr/bin/env python3
"""Change the phone's partition table (LUN0): shrink one partition and add
another, without moving anything else.

  tools/gpt-add.py dump HEAD.bin TAIL.bin
  tools/gpt-add.py add  HEAD.bin TAIL.bin NAME SIZE_GIB UNITS [SHRINK_FROM]

HEAD.bin  blocks 1..3     the primary header (block 1) and its entries
TAIL.bin  the last 8 blocks, the backup header is in the last one

This disk's GPT counts in 4096 byte units: the primary header sits in block
1, its entries in blocks 2..3, the backup entries near the end and the
backup header in the very last block. Everything is therefore block aligned
and can be read and written with plain 4096 byte transfers - the UFS driver
in this phone's kernels only answers requests that cover a whole block.

The header CRC covers the first HeaderSize bytes of the header, the entry
array CRC covers the entry array; both are recomputed here.
"""
import struct
import sys
import zlib

BLOCK = 4096
ENTRY_SIZE = 128
TYPE_LINUX = 0x8300


def crc32(b):
    return zlib.crc32(b) & 0xFFFFFFFF


def parse_header(block):
    h = block[:512]
    if h[:8] != b'EFI PART':
        raise SystemExit('not a GPT (magic %r)' % h[:8])
    (sig, rev, hsize, hcrc, res, mylba, altlba, first, last) = \
        struct.unpack_from('<8sIIIIQQQQ', h, 0)
    (plba,) = struct.unpack_from('<Q', h, 72)
    (nents, esize, ecrc) = struct.unpack_from('<III', h, 80)
    return dict(rev=rev, hsize=hsize, hcrc=hcrc, mylba=mylba, altlba=altlba,
                first=first, last=last, plba=plba, nents=nents, esize=esize,
                ecrc=ecrc, raw=bytearray(h))


def check_header(h):
    """The header CRC covers the first HeaderSize bytes with the CRC field
    zeroed (not skipped)."""
    raw = bytearray(h['raw'])
    raw[16:20] = b'\0\0\0\0'
    calc = crc32(bytes(raw[:h['hsize']]))
    return h['hcrc'] == calc, calc


def read_entries(image, h, base_block):
    """The entry array of a header: image is the file the header lives in."""
    off = (h['plba'] - base_block) * BLOCK
    return bytearray(image[off:off + h['nents'] * h['esize']])


def table(entries, h):
    used = []
    for i in range(h['nents']):
        e = entries[i * h['esize']:(i + 1) * h['esize']]
        if e[:16] == b'\0' * 16:
            continue
        first, last = struct.unpack_from('<QQ', e, 32)
        name = e[56:128].decode('utf-16-le').rstrip('\0')
        typ, = struct.unpack_from('<I', e, 0)
        used.append((first, last + 1, name, typ, i))
    used.sort()
    return used


def show(used, units, h=None):
    if h is not None:
        print('header: myLBA %d altLBA %d first usable %d last usable %d (%d units of %d bytes)'
              % (h['mylba'], h['altlba'], h['first'], h['last'], units, BLOCK))
    for f, l, n, ty, _ in used:
        print('  %-16s %12d .. %-12d %8.2f GiB  type %#010x'
              % (n, f, l - 1, (l - f) * BLOCK / 2 ** 30, ty))


def make_header(h, mylba, altlba, first, last, ecrc):
    b = bytearray(h['raw'])
    struct.pack_into('<I', b, 16, 0)
    struct.pack_into('<Q', b, 24, mylba)
    struct.pack_into('<Q', b, 32, altlba)
    struct.pack_into('<Q', b, 40, first)
    struct.pack_into('<Q', b, 48, last)
    struct.pack_into('<I', b, 88, ecrc)
    struct.pack_into('<I', b, 16, crc32(bytes(b[:h['hsize']])))
    return bytes(b)


def build(head, tail, name, size_gib, units, shrink):
    ph = parse_header(head)
    # the backup header is in the last block of the tail file
    tail_blocks = len(tail) // BLOCK
    bh = parse_header(tail[(tail_blocks - 1) * BLOCK:])
    p_entries = read_entries(head, ph, 1)
    b_entries = read_entries(tail, bh, units - tail_blocks)

    show(table(p_entries, ph), units, ph)
    used = table(p_entries, ph)
    end = max(l for _, l, _, _, _ in used)
    align = 256                                  # 1 MiB

    if shrink:
        victim = [u for u in used if u[2] == shrink]
        if not victim:
            raise SystemExit('no partition named %s' % shrink)
        vf, vl, vname, vtyp, vslot = victim[0]
        need = int(size_gib * 2 ** 30) // BLOCK
        new_end = ((vl - need) // align) * align - 1
        if new_end <= vf:
            raise SystemExit('%s is too small' % shrink)
        first_new = new_end + 1
        # the new partition takes the space the shrunk one gives up, up to the
        # last usable block before the backup area
        last_new = units - 6
        if last_new < first_new:
            raise SystemExit('no room')
        print('shrink %s: %d .. %d -> %d .. %d (%.2f GiB kept)'
              % (shrink, vf, vl - 1, vf, new_end,
                 (new_end - vf + 1) * BLOCK / 2 ** 30))
        e = bytearray(p_entries[vslot * ph['esize']:(vslot + 1) * ph['esize']])
        struct.pack_into('<Q', e, 40, new_end)
        p_entries[vslot * ph['esize']:(vslot + 1) * ph['esize']] = e
        e = bytearray(b_entries[vslot * bh['esize']:(vslot + 1) * bh['esize']])
        struct.pack_into('<Q', e, 40, new_end)
        b_entries[vslot * bh['esize']:(vslot + 1) * bh['esize']] = e
    else:
        first_new = (end + align - 1) & ~(align - 1)
        size = int(size_gib * 2 ** 30) // BLOCK
        last_new = first_new + size - 1
    if last_new >= units - 5:
        raise SystemExit('no room: would end at %d of %d' % (last_new, units))
    print('new partition %s: %d .. %d (%.2f GiB)'
          % (name, first_new, last_new, (last_new - first_new + 1) * BLOCK / 2 ** 30))

    slot = None
    for i in range(ph['nents']):
        cur = p_entries[i * ph['esize']:(i + 1) * ph['esize']]
        if cur[:16] == b'\0' * 16:
            slot = i
            break
    if slot is None:
        raise SystemExit('no free entry slot')
    print('  entry slot %d' % slot)

    e = bytearray(ph['esize'])
    e[0:16] = bytes(range(0x30, 0x40))
    e[16:32] = bytes(range(0x40, 0x50))
    struct.pack_into('<I', e, 0, TYPE_LINUX)
    struct.pack_into('<QQ', e, 32, first_new, last_new)
    e[56:56 + len(name) * 2] = name.encode('utf-16-le')
    p_entries[slot * ph['esize']:(slot + 1) * ph['esize']] = e
    b_entries[slot * bh['esize']:(slot + 1) * bh['esize']] = e

    ecrc = crc32(bytes(p_entries))
    if crc32(bytes(b_entries)) != ecrc:
        raise SystemExit('the backup entries do not match the primary ones')
    last_usable = last_new                    # the backup area follows

    new_head = bytearray(head)
    new_head[0:512] = make_header(ph, 1, ph['altlba'], ph['first'], last_usable, ecrc)
    new_head[BLOCK:BLOCK + len(p_entries)] = bytes(p_entries)

    new_tail = bytearray(tail)
    boff = (bh['plba'] - (units - tail_blocks)) * BLOCK
    new_tail[boff:boff + len(b_entries)] = bytes(b_entries)
    new_tail[(tail_blocks - 1) * BLOCK:(tail_blocks - 1) * BLOCK + 512] = \
        make_header(bh, bh['mylba'], bh['altlba'], bh['first'], last_usable, ecrc)

    open('HEAD.new', 'wb').write(bytes(new_head))
    open('TAIL.new', 'wb').write(bytes(new_tail))
    print('wrote HEAD.new (%d bytes, %d blocks) and TAIL.new (%d bytes, %d blocks)'
          % (len(new_head), len(new_head) // BLOCK, len(new_tail), len(new_tail) // BLOCK))
    print('write HEAD.new at block 1, TAIL.new at block %d' % (units - tail_blocks))


def verify(backup_base):
    """Check the two files that were just written."""
    for fn, base in (('HEAD.new', 1), ('TAIL.new', backup_base)):
        img = open(fn, 'rb').read()
        blocks = len(img) // BLOCK
        h = parse_header(img[0:BLOCK]) if base == 1 else parse_header(img[(blocks - 1) * BLOCK:])
        ok, calc = check_header(h)
        entries = read_entries(img, h, base)
        eok = crc32(bytes(entries)) == h['ecrc']
        print('%s: header CRC %s (calc %#x), entries CRC %s'
              % (fn, 'OK' if ok else 'SAI', calc, 'OK' if eok else 'SAI'))
        for f, l, n, ty, _ in table(entries, h):
            if n in ('userdata', 'darwin'):
                print('   %-10s %d .. %d (%.2f GiB)' % (n, f, l - 1, (l - f) * BLOCK / 2 ** 30))


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        raise SystemExit(1)
    cmd, head_path, tail_path = sys.argv[1], sys.argv[2], sys.argv[3]
    if cmd == 'verify':
        verify(int(sys.argv[4]) if len(sys.argv) > 4 else 30944248)
        return
    head = open(head_path, 'rb').read()
    tail = open(tail_path, 'rb').read()
    if cmd == 'dump':
        ph = parse_header(head)
        entries = read_entries(head, ph, 1)
        show(table(entries, ph), int(sys.argv[4]) if len(sys.argv) > 4 else 30944256, ph)
        return
    if cmd == 'add':
        shrink = sys.argv[7] if len(sys.argv) > 7 else None
        build(head, tail, sys.argv[4], float(sys.argv[5]), int(sys.argv[6]), shrink)
        return
    raise SystemExit('unknown command %s' % cmd)


if __name__ == '__main__':
    main()
