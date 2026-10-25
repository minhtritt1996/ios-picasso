#!/usr/bin/env python3
"""Minimal HFS+ (HFSX) reader for the picasso rootfs image.

The boot host has no hfsplus module and this workstation has no root, so the
rootfs is inspected with plain reads: parse the volume header, walk the catalog
B-tree, and read a file through its fork extents. Only what the bring-up needs:
list a directory, stat/print a file, and report a file's extents and offsets so
a byte-identical-size patch can be written back with dd.

  hfsplus-tool.py IMAGE ls <path>
  hfsplus-tool.py IMAGE cat <path> [out]
  hfsplus-tool.py IMAGE ext <path>
"""
import os
import struct
import subprocess
import sys

# When HFS_REMOTE is set, ranges that are not in the local image yet are read
# from the phone first (adb over ssh). Format: a command prefix that writes the
# raw partition bytes to stdout, e.g.
#   HFS_REMOTE="sshpass -p k60 ssh root@100.121.254.115 adb -s X exec-out dd if=/dev/block/by-name/darwin"
REMOTE = os.environ.get('HFS_REMOTE')
IMAGE = None
FETCHED = []          # merged, sorted byte ranges known to be in the image


def _load_ranges(img):
    try:
        with open(img + '.ranges') as f:
            for line in f:
                a, b = line.split()
                FETCHED.append([int(a), int(b)])
    except OSError:
        pass


def _save_ranges(img):
    with open(img + '.ranges', 'w') as f:
        for a, b in FETCHED:
            f.write('%d %d\n' % (a, b))


def _covered(off, n):
    end = off + n
    for a, b in FETCHED:
        if a <= off and end <= b:
            return True
    return False


def remote_fetch(img, off, n):
    """Pull [off, off+n) from the phone into the image file."""
    if not REMOTE:
        return
    block = 4096
    start = (off // block) * block
    count = ((off + n - start) + block - 1) // block
    cmd = '%s bs=%d skip=%d count=%d 2>/dev/null' % (REMOTE, block, start // block, count)
    print('# fetch %d bytes at %d' % (count * block, start), file=sys.stderr)
    out = subprocess.run(cmd, shell=True, capture_output=True)
    data = out.stdout[:count * block]
    if len(data) < count * block:
        print('# short fetch: %d of %d' % (len(data), count * block), file=sys.stderr)
    with open(img, 'r+b') as f:
        f.seek(start)
        f.write(data)
    FETCHED.append([start, start + len(data)])
    FETCHED.sort()
    _save_ranges(img)

BLOCK = 4096
CATALOG_NODE = None


class Vol:
    def __init__(self, path):
        global IMAGE
        IMAGE = path
        if REMOTE and not os.path.exists(path):
            with open(path, 'wb') as f:
                f.truncate(64 << 30)
            open(path + '.ranges', 'w').close()
        _load_ranges(path)
        self.f = open(path, 'r+b' if REMOTE else 'rb')
        hdr = self.read(1024, 512)
        magic = hdr[0:2]
        if magic not in (b'H+', b'HX'):
            sys.exit('not an HFS+ volume: magic %r' % magic)
        self.blocksize = struct.unpack('>I', hdr[0x28:0x2c])[0]
        self.totalblocks = struct.unpack('>I', hdr[0x2c:0x30])[0]
        cat = hdr[0x110:0x110 + 80]
        self.catalog = self.fork(cat)
        self.catalog_size = struct.unpack('>Q', cat[0:8])[0]
        ext = hdr[0x160:0x160 + 80]
        self.extents = self.fork(ext)
        self.extents_size = struct.unpack('>Q', ext[0:8])[0]

    def read(self, off, n):
        if REMOTE and not _covered(off, n):
            remote_fetch(IMAGE, off, n)
        self.f.seek(off)
        return self.f.read(n)

    @staticmethod
    def fork(blob):
        """First eight extents of a fork: [(start_block, block_count), ...]."""
        # HFSPlusForkData: logicalSize(8) clumpSize(4) totalBlocks(4) then the
        # eight extents, 8 bytes each (start, count) in allocation blocks.
        out = []
        for i in range(8):
            start, count = struct.unpack('>II', blob[16 + i * 8:16 + i * 8 + 8])
            if count:
                out.append((start, count))
        return out

    def fork_size(self, blob):
        return struct.unpack('>Q', blob[0:8])[0]

    def fork_read(self, extents, size, offset, n):
        """Read n bytes at offset from a fork given its extent list."""
        parts = []
        pos = 0
        for start_block, count in extents:
            extent_len = count * self.blocksize
            if offset < pos + extent_len:
                inner = offset - pos
                take = min(n, extent_len - inner)
                parts.append(self.read(start_block * self.blocksize + inner, take))
                offset += take
                n -= take
                if n == 0:
                    break
            pos += extent_len
        return b''.join(parts)


def catalog_nodes(vol):
    """Yield every leaf node of the catalog B-tree as a list of records."""
    # B-tree header node: node 0, header record at +14 (after node descriptor)
    node = vol.fork_read(vol.catalog, vol.catalog_size, 0, vol.blocksize)
    # HFSPlusHeaderRec: treeDepth(2) rootNode(4) leafRecords(4) firstLeafNode(4)
    # lastLeafNode(4) nodeSize(2) maxKeyLength(2) totalNodes(4) freeNodes(4) ...
    hdr = node[14:14 + 106]
    node_size = struct.unpack('>H', hdr[18:20])[0]
    first_leaf = struct.unpack('>I', hdr[10:14])[0]
    total_nodes = struct.unpack('>I', hdr[22:26])[0]
    if node_size == 0:
        node_size = 4096
    node_no = first_leaf
    seen = set()
    while node_no != 0 and node_no not in seen and node_no < total_nodes:
        seen.add(node_no)
        raw = vol.fork_read(vol.catalog, vol.catalog_size, node_no * node_size, node_size)
        if len(raw) < node_size:
            # a dump that does not cover the later extents yet: stop cleanly
            return
        fwd, back, kind, height, num, _res = struct.unpack('>IIbBHH', raw[0:14])
        recs = []
        offs = []
        for i in range(num + 1):
            offs.append(struct.unpack('>H', raw[node_size - 2 * (i + 1):node_size - 2 * i])[0])
        # offs[i] is the start of record i; the last entry fences the free area
        for i in range(num):
            start, end = offs[i], offs[i + 1]
            if end <= start or end > node_size:
                continue
            recs.append(raw[start:end])
        yield recs
        node_no = fwd


def parse_key(rec):
    keylen = struct.unpack('>H', rec[0:2])[0]
    key = rec[2:2 + keylen]
    parent, = struct.unpack('>I', key[0:4])
    namelen = struct.unpack('>H', key[4:6])[0]
    name = key[6:6 + 2 * namelen].decode('utf-16-be', 'replace')
    return parent, name, rec[2 + keylen:]


def fork_of(file_rec):
    body = file_rec
    data = body[88:88 + 80]
    return Vol.fork(data), struct.unpack('>Q', data[0:8])[0]


def find(vol, path):
    """Return (fileID, record) for path, walking from the root folder (id 2)."""
    parent = 2
    entry = None
    parts = [p for p in path.split('/') if p]
    if not parts:
        # the root folder has no catalog entry of its own: it is CNID 2
        return (2, None)
    for i, part in enumerate(parts):
        entry = None
        for recs in catalog_nodes(vol):
            for rec in recs:
                try:
                    par, name, body = parse_key(rec)
                except Exception:
                    continue
                if par == parent and name.rstrip('\r') == part:
                    entry = (struct.unpack('>H', body[0:2])[0], body)
                    break
            if entry:
                break
        if entry is None:
            return None
        parent = struct.unpack(">I", entry[1][8:12])[0]
    return entry


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    img, cmd = sys.argv[1], sys.argv[2]
    vol = Vol(img)
    print('# block %d total %d catalog extents %s' % (vol.blocksize, vol.totalblocks, vol.catalog))
    if cmd == 'ls':
        path = sys.argv[3] if len(sys.argv) > 3 else '/'
        got = find(vol, path)
        if not got:
            print('not found:', path)
            return
        folder_id = 2 if got[1] is None else struct.unpack(">I", got[1][8:12])[0]
        print('# %s -> folder id %d' % (path, folder_id))
        for recs in catalog_nodes(vol):
            for rec in recs:
                par, name, body = parse_key(rec)
                if par != folder_id:
                    continue
                kind = struct.unpack('>H', body[0:2])[0]
                if kind not in (1, 2):          # thread records are not entries
                    continue
                fid = struct.unpack(">I", body[8:12])[0]
                if kind == 2:
                    forks, size = fork_of(body)
                    print('f %-40s id %-9d %8d bytes ext %s' % (name, fid, size, forks))
                else:
                    print('d %-40s id %d' % (name, fid))
    elif cmd in ('cat', 'ext'):
        path = sys.argv[3]
        got = find(vol, path)
        if not got or got[1] is None or got[0] != 2:
            print('not a file:', path)
            return
        forks, size = fork_of(got[1])
        if cmd == 'ext':
            print('size %d bytes, extents %s' % (size, forks))
            for start, count in forks:
                print('  partition offset %d (block %d x %d)' % (start * vol.blocksize, start, count))
            return
        data = vol.fork_read(forks, size, 0, size)
        if len(sys.argv) > 4:
            open(sys.argv[4], 'wb').write(data)
            print('wrote %d bytes to %s' % (len(data), sys.argv[4]))
        else:
            sys.stdout.write(data.decode('utf-8', 'replace'))


if __name__ == '__main__':
    main()
