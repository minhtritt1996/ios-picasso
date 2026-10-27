#!/usr/bin/env python3
"""Install a file on the phone through the kernel control channel.

`put <path> <offset> <base64>` creates or patches the file (no truncation, no
fsync per chunk), and `putend <path> <size>` sets the final size and flushes once -
so a 285 KB dropbear installs over the link without touching this port's flaky UFS
path hard enough to wedge it.

  picasso-put.py LOCAL REMOTE_PATH

Runs from the directory that holds picasso-ecm.py (they are copied to the host the
phone is plugged into).
"""
import base64
import os
import pathlib
import subprocess
import sys

CHUNK = 800          # base64 stays inside the 1200-byte command buffer
HERE = os.path.dirname(os.path.abspath(__file__))


def ctl(cmd, timeout=30):
    r = subprocess.run(["python3", os.path.join(HERE, "picasso-ecm.py"), "l2"] + cmd.split(),
                       capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "").strip()


def main():
    local, remote = sys.argv[1], sys.argv[2]
    blob = pathlib.Path(local).read_bytes()
    for off in range(0, len(blob), CHUNK):
        part = base64.b64encode(blob[off:off + CHUNK]).decode()
        reply = ctl("put %s 0x%x %s" % (remote, off, part))
        if "written" not in reply:
            print("offset 0x%x failed: %s" % (off, reply))
            return 1
        if (off // CHUNK) % 32 == 0:
            print("  %d/%d bytes" % (min(off + CHUNK, len(blob)), len(blob)), flush=True)
    print("  putend:", ctl("putend %s %d" % (remote, len(blob))))
    print("  stat:", ctl("stat %s" % remote))
    return 0


if __name__ == "__main__":
    sys.exit(main())
