#!/usr/bin/env python3
"""Find and extract hardlink content (dyld shared caches) from an HFS+ image.

The iOS rootfs stores /System/Library/Caches/com.apple.dyld/* as hardlink stubs
(0-byte catalog entries); the data lives in the invisible "HFS+ Private Data"
folder (CNID 18) as iNode<cnid> files. hfsplus-tool.py can read forks, so this
walks that folder and matches content by its first bytes.

  tools/dsc-from-image.py IMG list [--magic dyld_v1]
  tools/dsc-from-image.py IMG extract NAME OUT
"""
import importlib.util
import os
import struct
import sys

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hfsplus-tool.py")
PRIVATE_DIR = 18


def load_tool():
    spec = importlib.util.spec_from_file_location("hfsplus_tool", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def entries(vol, mod, folder_id):
    out = []
    for recs in mod.catalog_nodes(vol):
        for rec in recs:
            par, name, body = mod.parse_key(rec)
            if par != folder_id:
                continue
            kind = struct.unpack(">H", body[0:2])[0]
            if kind != 2:
                continue
            cnid = struct.unpack(">I", body[8:12])[0]
            forks, size = mod.fork_of(body)
            out.append((name, cnid, size, forks))
    return out


def read_fork(vol, forks, size, off, n):
    got = b""
    pos = 0
    for start, nblocks in forks:
        blk_bytes = nblocks * vol.blocksize
        if off + n <= pos + blk_bytes:
            data = vol.read(start * vol.blocksize, blk_bytes)
            return data[off - pos:off - pos + n]
        pos += blk_bytes
    return got


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    img, cmd = sys.argv[1], sys.argv[2]
    mod = load_tool()
    vol = mod.Vol(img)
    if cmd == "list":
        magic = sys.argv[sys.argv.index("--magic") + 1].encode() if "--magic" in sys.argv else b"dyld_v1"
        rows = entries(vol, mod, PRIVATE_DIR)
        print("%d entries in the private dir" % len(rows))
        for name, cnid, size, forks in rows:
            if size <= 0:
                continue
            head = read_fork(vol, forks, size, 0, 16)
            tag = "MATCH" if head.startswith(magic) else "     "
            if tag == "MATCH" or size > 20 * 1024 * 1024:
                print("%s %-24s cnid %-9d %12d bytes  %r" % (tag, name, cnid, size, head[:16]))
    elif cmd == "extract":
        name, out = sys.argv[3], sys.argv[4]
        rows = {n: (c, s, f) for n, c, s, f in entries(vol, mod, PRIVATE_DIR)}
        if name not in rows:
            sys.exit("no such entry: " + name)
        cnid, size, forks = rows[name]
        with open(out, "wb") as fh:
            pos = 0
            for start, nblocks in forks:
                blk_bytes = nblocks * vol.blocksize
                fh.write(vol.read(start * vol.blocksize, blk_bytes))
                pos += blk_bytes
        print("%s -> %s (%d bytes)" % (name, out, os.path.getsize(out)))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
