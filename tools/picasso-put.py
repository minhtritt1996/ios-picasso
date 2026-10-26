#!/usr/bin/env python3
"""Install a file on the phone through the kernel control channel.

The USB driver exports `put <path> <offset> <base64>` (vnode_open with O_CREAT),
so a file can be created from the host while the link is up - no recovery, no
rootfs surgery, and no launchd involvement. Used to install the shell and a real
LaunchDaemon plist.

  tools/picasso-put.py LOCAL REMOTE_PATH
"""
import base64
import pathlib
import subprocess
import sys

CHUNK = 800          # base64 stays inside the 1200-byte command buffer


def ctl(cmd, timeout=20):
    r = subprocess.run(["python3", "tools/picasso-ecm.py", "l2"] + cmd.split(),
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
        if (off // CHUNK) % 8 == 0:
            print("  %d/%d bytes" % (min(off + CHUNK, len(blob)), len(blob)))
    st = ctl("stat %s" % remote)
    print("wrote %s (%d bytes); %s" % (remote, len(blob), st))
    return 0


if __name__ == "__main__":
    sys.exit(main())
