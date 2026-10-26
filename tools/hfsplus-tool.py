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


AHEAD = 512 * 1024      # one adb round trip per half megabyte, not per block


def remote_fetch(img, off, n):
    """Pull [off, off+n) from the phone into the image file.

    The catalog walk reads leaf node after leaf node, so fetching exactly the
    requested block costs one adb round trip per node and a full walk takes
    minutes. Reading a window instead turns that into a handful of calls.
    """
    if not REMOTE:
        return
    block = 4096
    base = (off // AHEAD) * AHEAD
    start = base
    count = min(((((off + n) - base) + block - 1) // block), AHEAD // block)
    if count * block < n:
        count = (n + block - 1) // block
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
        # REMOTE only adds the on-demand fetching; the local sparse file is
        # still where an edit is computed, so writes stay allowed.
        self.writable = os.access(path, os.W_OK)
        self.f = open(path, 'r+b' if self.writable else 'rb')
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
        self.vh = hdr
        node = self.fork_read(self.catalog, self.catalog_size, 0, self.blocksize)
        self.node_size = struct.unpack('>H', node[32:34])[0] or 4096
        self.first_leaf = struct.unpack('>I', node[24:28])[0]

    def write(self, off, data):
        if not self.writable:
            raise SystemExit('%s is not writable' % IMAGE)
        self.f.seek(off)
        self.f.write(data)

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



# ---------------------------------------------------------------- creation ---
# Creating a file needs three on-disk structures the reader never touches: free
# allocation blocks, catalog B-tree records, and the volume header counters. All
# are plain data, so a file can be created offline and only the changed blocks
# shipped to the phone (see the block list printed at the end).

CAT_FILE, CAT_FOLDER, CAT_FOLDER_THREAD, CAT_FILE_THREAD = 2, 1, 3, 4
VH_FILE_COUNT, VH_FOLDER_COUNT, VH_FREE_BLOCKS, VH_NEXT_CNID = 32, 36, 48, 64


def _node_unpack(raw):
    fwd, back, kind, height, num = struct.unpack('>IIbBH', raw[0:12])
    offs = [struct.unpack('>H', raw[len(raw) - 2 * (i + 1):len(raw) - 2 * i])[0]
            for i in range(num + 1)]
    return fwd, back, kind, height, [raw[offs[i]:offs[i + 1]] for i in range(num)]


def _node_pack(fwd, back, kind, height, recs, node_size):
    buf = bytearray(node_size)
    struct.pack_into('>IIbBH', buf, 0, fwd, back, kind, height, len(recs))
    pos = 14
    offs = []
    for rec in recs:
        if len(rec) % 2:
            rec = rec + b'\0'              # records (and keys) are 2-byte aligned
        offs.append(pos)
        buf[pos:pos + len(rec)] = rec
        pos += len(rec)
    if pos + 2 * (len(recs) + 1) > node_size:
        raise SystemExit('catalog node overflow: records need %d of %d bytes'
                         % (pos, node_size))
    offs.append(pos)
    for i, o in enumerate(offs):
        struct.pack_into('>H', buf, node_size - 2 * (i + 1), o)
    return bytes(buf)


def _catalog_key(parent, name):
    chars = name.encode('utf-16-be')
    key = struct.pack('>IH', parent, len(chars) // 2) + chars
    return struct.pack('>H', len(key)) + key


DATE = 0xDC000000            # plausible HFS+ date (seconds since 1904)


def _perms():
    # HFSPlusBSDInfo: ownerID, groupID, adminFlags, ownerFlags, fileMode, special
    return struct.pack('>IIBBHI', 0, 0, 0, 0, 0o100644, 0)


def _fork(logical, nblocks, extents):
    out = struct.pack('>QII', logical, 4096, nblocks)
    for i in range(8):
        if i < len(extents):
            out += struct.pack('>II', extents[i][0], extents[i][1])
        else:
            out += struct.pack('>II', 0, 0)
    return out


def _file_record(cnid, size, extents):
    body = struct.pack('>HHII', CAT_FILE, 0, 0, cnid)
    body += struct.pack('>IIIII', DATE, DATE, DATE, DATE, 0)
    body += _perms()
    body += b'\0' * 32                                  # userInfo + finderInfo
    body += struct.pack('>II', 0, 0)                      # textEncoding, reserved
    body += _fork(size, sum(c for _, c in extents), extents)
    body += _fork(0, 0, [])
    return body


def _folder_record(cnid, valence, parent, name):
    body = struct.pack('>HHII', 1, 0, valence, cnid)      # type, flags, valence, id
    body += struct.pack('>IIIII', DATE, DATE, DATE, DATE, 0)
    body += _perms()
    body += b'\0' * 32                                    # userInfo + finderInfo
    body += struct.pack('>II', 0, 0)
    return _catalog_key(parent, name) + body


def _folder_thread(cnid, parent, name):
    body = struct.pack('>HHI', 3, 0, parent)
    body += struct.pack('>H', len(name)) + name.encode('utf-16-be')
    return _catalog_key(cnid, '') + body


def _file_thread(cnid, parent, name):
    body = struct.pack('>HHI', CAT_FILE_THREAD, 0, parent)
    body += struct.pack('>H', len(name)) + name.encode('utf-16-be')
    return body


def _merge(blocks):
    out = []
    for b in blocks:
        if out and b == out[-1][1] + 1:
            out[-1][1] = b
        else:
            out.append([b, b])
    return out


def _alloc_run(vol, nblocks):
    """Find and claim a contiguous run of nblocks in the allocation bitmap."""
    alloc_blob = vol.vh[0x70:0x70 + 80]
    extents = Vol.fork(alloc_blob)
    size = struct.unpack('>Q', alloc_blob[0:8])[0]
    bitmap = bytearray(vol.fork_read(extents, size, 0, size))
    start = 0
    while start < vol.totalblocks:
        byte = start // 8
        bit = 7 - (start % 8)
        if bitmap[byte] & (1 << bit):
            start += 1
            continue
        run = 0
        while run < nblocks and start + run < vol.totalblocks:
            b = (start + run) // 8
            i = 7 - ((start + run) % 8)
            if bitmap[b] & (1 << i):
                break
            run += 1
        if run == nblocks:
            for k in range(nblocks):
                b = (start + k) // 8
                i = 7 - ((start + k) % 8)
                bitmap[b] |= (1 << i)
            return start, bytes(bitmap), extents, size
        start += max(run, 1)
    raise SystemExit('no contiguous run of %d blocks' % nblocks)


def _insert(vol, records):
    """Insert (key, body) records into the catalog B-tree leaves, in order."""
    _insert.changed = set()
    node_size = vol.node_size
    header = vol.fork_read(vol.catalog, vol.catalog_size, 0, node_size)
    fwd, back, kind, height, recs = _node_unpack(header)
    hdr = bytearray(recs[0])
    leaf_records = struct.unpack('>I', hdr[6:10])[0]

    # every leaf, in link order, with its keys
    leaves = []
    node_no = vol.first_leaf
    while node_no:
        raw = vol.fork_read(vol.catalog, vol.catalog_size, node_no * node_size, node_size)
        fwd_n, back_n, kind_n, height_n, recs_n = _node_unpack(raw)
        leaves.append({'no': node_no, 'fwd': fwd_n, 'back': back_n,
                       'kind': kind_n, 'height': height_n, 'recs': recs_n})
        node_no = fwd_n
    if not leaves:
        raise SystemExit('no catalog leaves')

    def key_bytes_of(rec):
        keylen = struct.unpack('>H', rec[0:2])[0]
        return rec[2:2 + keylen]

    def order_key(key):
        """(parentID, case-folded name) - HFS+'s ordering, not raw bytes.

        Comparing the raw key would put the 2-byte keyLength first, sorting by name
        length, so the kernel's binary search lands elsewhere and returns ENOENT
        even though the record is on disk.
        """
        parent, = struct.unpack('>I', key[0:4])
        namelen = struct.unpack('>H', key[4:6])[0]
        name = key[6:6 + 2 * namelen]
        return (parent, bytes((c | 0x20) if 0x41 <= c <= 0x5a else c for c in name))

    dirty = {}
    for key, body in records:
        rec = key + body
        want = order_key(key_bytes_of(rec))   # strip the 2-byte keyLength first
        target = None
        for leaf in leaves:
            keys = [order_key(key_bytes_of(r)) for r in leaf['recs']]
            if not keys or keys[-1] >= want:
                target = leaf
                break
        if target is None:
            target = leaves[-1]
        keys = [order_key(key_bytes_of(r)) for r in target['recs']]
        pos = len(keys)
        for i, k in enumerate(keys):
            if k >= want:
                pos = i
                break
        if os.environ.get('HFSCREATE_DEBUG'):
            print('# insert %r into leaf %d at %d of %d, leaf first %r last %r' % (
                want, target['no'], pos, len(keys),
                keys[0] if keys else None, keys[-1] if keys else None), file=sys.stderr)
        target['recs'].insert(pos, rec)
        leaf_records += 1
        dirty[target['no']] = target

    for node_no, leaf in dirty.items():
        packed = _node_pack(leaf['fwd'], leaf['back'], leaf['kind'],
                            leaf['height'], leaf['recs'], node_size)
        _fork_write(vol, vol.catalog, vol.catalog_size, node_no * node_size, packed,
                    _insert.changed)

    struct.pack_into('>I', hdr, 6, leaf_records)
    recs[0] = bytes(hdr)
    _fork_write(vol, vol.catalog, vol.catalog_size, 0,
                _node_pack(fwd, back, kind, height, recs, node_size), _insert.changed)


def _fork_write(vol, extents, size, offset, data, changed=None):
    pos = 0
    for start_block, count in extents:
        extent_len = count * vol.blocksize
        if offset < pos + extent_len:
            inner = offset - pos
            take = min(len(data), extent_len - inner)
            vol.write(start_block * vol.blocksize + inner, data[:take])
            if changed is not None:
                for b in range(start_block + inner // vol.blocksize,
                               start_block + (inner + take + vol.blocksize - 1) // vol.blocksize):
                    changed.add(b)
            data = data[take:]
            offset += take
            if not data:
                return
        pos += extent_len
    raise SystemExit('fork too small for %d bytes' % len(data))


def create(vol, folder_path, files):
    """Create files inside folder_path: [(name, local_path), ...]."""
    if not vol.writable:
        raise SystemExit('%s is not writable' % IMAGE)
    got = find(vol, folder_path)
    if not got or got[1] is None:
        raise SystemExit('no such folder: %s' % folder_path)
    folder_id = struct.unpack('>I', got[1][8:12])[0]
    if os.environ.get('HFSCREATE_DEBUG'):
        print('# folder %s -> record type %d body[0:16] %s id %d' % (
            folder_path, struct.unpack('>H', got[1][0:2])[0], got[1][:16].hex(' '), folder_id),
            file=sys.stderr)
    cnid = struct.unpack('>I', vol.vh[VH_NEXT_CNID:VH_NEXT_CNID + 4])[0]
    free_blocks = struct.unpack('>I', vol.vh[VH_FREE_BLOCKS:VH_FREE_BLOCKS + 4])[0]
    file_count = struct.unpack('>I', vol.vh[VH_FILE_COUNT:VH_FILE_COUNT + 4])[0]
    records = []
    changed = set()
    for name, local in files:
        blob = open(local, 'rb').read()
        nblocks = (len(blob) + vol.blocksize - 1) // vol.blocksize
        start, bitmap, alloc_extents, alloc_size = _alloc_run(vol, nblocks)
        vol.write(start * vol.blocksize,
                  blob + b'\0' * (nblocks * vol.blocksize - len(blob)))
        _fork_write(vol, alloc_extents, alloc_size, 0, bitmap, changed)
        for b in range(start, start + nblocks):
            changed.add(b)
        records.append((_catalog_key(folder_id, name),
                        _file_record(cnid, len(blob), [(start, nblocks)])))
        records.append((_catalog_key(cnid, ''),
                        _file_thread(cnid, folder_id, name)))
        print('  %-28s %8d bytes at block %d (%d blocks) cnid %d'
              % (name, len(blob), start, nblocks, cnid))
        cnid += 1
        free_blocks -= nblocks
        file_count += 1
    _insert(vol, records)
    changed |= _insert.changed
    changed.add(1024 // vol.blocksize)          # the volume header lives there
    vol.write(1024 + VH_FREE_BLOCKS, struct.pack('>I', free_blocks))
    vol.write(1024 + VH_FILE_COUNT, struct.pack('>I', file_count))
    vol.write(1024 + VH_NEXT_CNID, struct.pack('>I', cnid))
    return changed

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
    elif cmd == 'setsize':
        # Patch one file record's data-fork logicalSize. Needed when a file on the
        # device has a fixed size (an existing binary we replace) and the
        # replacement is a different length: the extent stays, the size changes.
        path, newsize = sys.argv[3], int(sys.argv[4])
        folder, _, name = path.rpartition('/')
        got = find(vol, folder or '/')
        if not got or got[1] is None:
            sys.exit('no such folder: %s' % folder)
        folder_id = struct.unpack('>I', got[1][8:12])[0]
        node_size = vol.node_size
        node_no = vol.first_leaf
        done = False
        while node_no and not done:
            raw = vol.fork_read(vol.catalog, vol.catalog_size, node_no * node_size, node_size)
            fwd, back, kind, height, recs = _node_unpack(raw)
            for i, rec in enumerate(recs):
                keylen = struct.unpack('>H', rec[0:2])[0]
                key = rec[2:2 + keylen]
                parent, = struct.unpack('>I', key[0:4])
                nl = struct.unpack('>H', key[4:6])[0]
                rname = key[6:6 + 2 * nl].decode('utf-16-be', 'replace')
                if parent == folder_id and rname == name:
                    body = bytearray(rec[2 + keylen:])
                    if struct.unpack('>H', body[0:2])[0] != 2:
                        sys.exit('%s is not a file record' % path)
                    old, = struct.unpack('>Q', body[88:96])
                    struct.pack_into('>Q', body, 88, newsize)
                    recs[i] = rec[:2 + keylen] + bytes(body)
                    packed = _node_pack(fwd, back, kind, height, recs, node_size)
                    _fork_write(vol, vol.catalog, vol.catalog_size, node_no * node_size, packed)
                    print('# %s: logicalSize %d -> %d' % (path, old, newsize))
                    # Map the catalog's *logical* node offset through its extents to
                    # the block on disk; printing the logical offset and shipping it
                    # writes an unrelated block.
                    loff = node_no * node_size
                    pos = 0
                    for start_block, count in vol.catalog:
                        span = count * vol.blocksize
                        if loff < pos + span:
                            print('  bs=4096 skip=%d count=%d' % (
                                start_block + (loff - pos) // vol.blocksize,
                                (node_size + vol.blocksize - 1) // vol.blocksize))
                            break
                        pos += span
                    done = True
                    break
            node_no = fwd
        if not done:
            sys.exit('record not found: %s' % path)
    elif cmd == 'unjournal':
        # A journaled volume replays its journal at mount, which silently undoes
        # any offline edit (the records were on disk, yet XNU answered ENOENT and
        # the volume stayed consistent). Clearing the journaled bit and marking the
        # volume cleanly unmounted makes XNU mount it as a plain HFS+ volume, so
        # offline catalog work survives.
        vh = bytearray(vol.read(1024, 512))
        attr, = struct.unpack('>I', vh[4:8])
        print('# attributes 0x%08x (journaled %s, unmounted %s)' % (
            attr, bool(attr & 0x2000), bool(attr & 0x100)))
        attr = (attr & ~0x2000) | 0x100
        struct.pack_into('>I', vh, 4, attr)
        vol.write(1024, bytes(vh))
        vol.f.flush()
        print('# attributes 0x%08x (journaling off, marked unmounted)' % attr)
        print('  bs=4096 skip=0 count=1')
    elif cmd == 'mkd':
        # Create a folder. iOS needs /private/var/log/com.apple.xpc.launchd to exist
        # before launchd can log its spawn decisions, and that log is the only way
        # to see why a spawn stalls.
        folder, name = sys.argv[3], sys.argv[4]
        got = find(vol, folder)
        if not got or got[1] is None:
            sys.exit('no such folder: %s' % folder)
        parent_id = struct.unpack('>I', got[1][8:12])[0]
        cnid = struct.unpack('>I', vol.vh[VH_NEXT_CNID:VH_NEXT_CNID + 4])[0]
        folder_count = struct.unpack('>I', vol.vh[VH_FOLDER_COUNT:VH_FOLDER_COUNT + 4])[0]
        _insert(vol, [(_catalog_key(parent_id, name), _folder_record(cnid, 0, parent_id, name)),
                      (_catalog_key(cnid, ''), _folder_thread(cnid, parent_id, name))])
        vol.write(1024 + VH_FOLDER_COUNT, struct.pack('>I', folder_count + 1))
        vol.write(1024 + VH_NEXT_CNID, struct.pack('>I', cnid + 1))
        vol.f.flush()
        print('# folder %s/%s created, cnid %d' % (folder, name, cnid))
        for b in sorted(_insert.changed | {1024 // vol.blocksize}):
            pass
        print('  bs=4096 skip=%d count=1' % (1024 // vol.blocksize))
        for lo, hi in _merge(sorted(_insert.changed)):
            print('  bs=4096 skip=%d count=%d' % (lo, hi - lo + 1))
    elif cmd == 'create':
        folder = sys.argv[3]
        files = [tuple(a.split('=', 1)) for a in sys.argv[4:]]
        if not files:
            sys.exit('usage: hfsplus-tool.py IMAGE create FOLDER NAME=LOCAL ...')
        changed = create(vol, folder, files)
        vol.f.flush()
        print('# %d blocks changed; ranges to write back:' % len(changed))
        blocks = sorted(changed)
        run = [blocks[0], blocks[0]]
        out = []
        for b in blocks[1:] + [None]:
            if b is not None and b == run[1] + 1:
                run[1] = b
                continue
            out.append(tuple(run))
            if b is not None:
                run = [b, b]
        for first, last in out:
            print('  bs=4096 skip=%d count=%d' % (first, last - first + 1))
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
