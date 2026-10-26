#!/usr/bin/env python3
"""Install a binary over the kernel's L2 command channel - no recovery, no AMFI.

The channel's `put` writes base64 at an offset with O_CREAT|O_RDWR (no truncate,
mode 0755) and `putend` sets the final size and flushes, which is exactly a chunked
install (patch 0153). Because the write happens in the kernel, an unvalidated binary
lands on disk even while stock amfid is the thing enforcing signatures.

The case that matters: replacing /usr/libexec/amfid with the shell. Stock amfid is
what kills every binary this port starts at boot, so with the shell in its place the
launchd job that is already in the cache can start /private/var/root/sweetsh as root.

  tools/picasso-install-binary-over-l2.py --binary tools/sweetsh/sweetsh-udp-signed \\
      --dest /usr/libexec/amfid [--chunk 512] [--reboot]

Runs on the machine the phone is plugged into. Every chunk is acknowledged by the
kernel, `stat` confirms the size and `cat` compares the first frame, because the
channel has no offset read. A bad install is recoverable: the channel is kernel-side,
so it keeps working even when the binary it just replaced is broken.
"""
import argparse
import base64
import hashlib
import importlib.util
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def load_ecm():
    spec = importlib.util.spec_from_file_location(
        "picasso_ecm", os.path.join(HERE, "picasso-ecm.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--chunk", type=int, default=768)
    ap.add_argument("--reboot", action="store_true")
    args = ap.parse_args()

    data = open(args.binary, "rb").read()
    ecm = load_ecm()
    iface = ecm.cmd_up()
    ecm.IFACE = iface          # ctl_ask reads the module global for our MAC
    sock = ecm.ctl_socket(iface)

    off = 0
    while off < len(data):
        piece = data[off:off + args.chunk]
        cmd = "put %s %d %s" % (args.dest, off,
                                base64.b64encode(piece).decode())
        reply = ecm.ctl_ask(sock, cmd, timeout=3.0)
        text = (reply or b"").decode("latin-1", "replace").strip()
        if "written" not in text:
            print("off %d: %s" % (off, text or "[no reply]"))
            return 1
        off += len(piece)
        if off % 8192 < args.chunk:
            print("  %d/%d bytes" % (off, len(data)))

    reply = ecm.ctl_ask(sock, "putend %s %d" % (args.dest, len(data)), timeout=5.0)
    print("putend:", (reply or b"").decode("latin-1", "replace").strip())

    # `cat` has no offset and the reply fits one frame, so the check is the size
    # from `stat` plus the first frame of content.
    st = (ecm.ctl_ask(sock, "stat %s" % args.dest, timeout=5.0) or b"").decode("latin-1", "replace")
    print("stat:", st.strip())
    if str(len(data)) not in st:
        print("size not confirmed")
        return 1
    head = ecm.ctl_ask(sock, "cat %s %d" % (args.dest, min(1200, len(data))), timeout=5.0)
    if not head or head[:len(data[:min(1200, len(data))])] != data[:min(1200, len(data))]:
        print("head mismatch: %r" % (head[:64] if head else None))
        return 1
    print("head ok (%d bytes)" % len(head))

    if args.reboot:
        print("reboot:", (ecm.ctl_ask(sock, "reboot", timeout=2.0) or b"").decode(
            "latin-1", "replace").strip() or "[no reply, expected]")
    print("installed %s (%d bytes)" % (args.dest, len(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
