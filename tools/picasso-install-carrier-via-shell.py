#!/usr/bin/env python3
"""Install a carrier binary through the shell's own `w` command, no recovery.

sweetsh's `w <path> <offset> <hex>` writes up to 280 bytes per command into an
existing file, and a UDP round trip costs milliseconds, so a 33 KiB binary goes in
inside the window the carrier lives for. The target keeps its mode and its size, so
writing a Mach-O into the front of a larger file is enough.

  tools/picasso-install-carrier-via-shell.py BINARY /System/.../SpringBoard [--reboot]

Waits for the shell to answer, writes the image, reads it back with `cat` (the shell
returns the file from the start, which is exactly what was written) and compares.
"""
import argparse
import os
import socket
import subprocess
import sys
import time

PHONE = ("192.168.7.2", 4444)
CHUNK = 280


def ecm_up():
    subprocess.run(["python3", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "picasso-ecm.py"), "up"], capture_output=True)


def ask(cmd, timeout=2.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(cmd.encode(), PHONE)
        data, _ = s.recvfrom(8192)
        return data
    except OSError:
        return None
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("binary")
    ap.add_argument("dest")
    ap.add_argument("--seconds", type=int, default=600)
    ap.add_argument("--reboot", action="store_true")
    args = ap.parse_args()

    data = open(args.binary, "rb").read()
    ecm_up()
    if args.reboot:
        # The kernel channel can reboot even when no shell is running.
        s = subprocess.run(["python3", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                    "picasso-ecm.py"), "l2", "reboot"],
                           capture_output=True, text=True)
        print("reboot:", (s.stdout or s.stderr).strip()[:60])

    end = time.time() + args.seconds
    while time.time() < end:
        if ask("help", 0.8) is not None:
            break
        ecm_up()
        time.sleep(0.5)
    else:
        print("the shell never answered")
        return 1
    print("shell up, writing %d bytes to %s" % (len(data), args.dest))

    off = 0
    t0 = time.time()
    while off < len(data):
        piece = data[off:off + CHUNK]
        reply = ask("w %s %d %s" % (args.dest, off, piece.hex()), 2.0)
        if reply is None:
            print("no reply at offset %d after %d bytes" % (off, off))
            return 1
        off += len(piece)
    print("wrote %d bytes in %.1fs" % (off, time.time() - t0))

    got = ask("cat %s %d" % (args.dest, min(len(data), 1200)), 3.0)
    want = data[:min(len(data), 1200)]
    if got is None:
        print("readback: no reply")
        return 1
    if got[:len(want)] != want:
        print("readback mismatch: %r" % got[:32])
        return 1
    print("readback ok (%d bytes compared)" % len(want))
    return 0


if __name__ == "__main__":
    sys.exit(main())
