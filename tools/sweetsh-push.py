#!/usr/bin/env python3
"""Push a file to the phone through the sweetsh UDP shell.

The shell's `w <path> <offset> <hex>` builtin patches bytes of an existing file,
which is what `dd conv=notrunc` from recovery did. This sends a whole image in
560-byte chunks. The target file keeps its size, so the HFS+ catalog is never
touched and no recovery round trip is needed.

  tools/sweetsh-push.py IMAGE TARGET_PATH

Then, to make the new image the launchd carrier:
  tools/sweetsh-push.py sweetsh-signed /bin/ps   (a spare file)
  send "/bin/ps"                                  (it installs itself and reboots)
"""
import socket
import subprocess
import sys
import time

PHONE = ("192.168.7.2", 4444)
CHUNK = 560


def ecm_up():
    subprocess.run(["python3", "tools/picasso-ecm.py", "up"], capture_output=True)


def send(sock, cmd, tries=6):
    for _ in range(tries):
        ecm_up()
        try:
            sock.sendto(cmd, PHONE)
            data, _ = sock.recvfrom(2048)
            return data.decode("latin-1", "replace").strip()
        except socket.timeout:
            continue
    return None


def main():
    image, target = sys.argv[1], sys.argv[2]
    blob = open(image, "rb").read()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2)
    sent = 0
    for off in range(0, len(blob), CHUNK):
        part = blob[off:off + CHUNK]
        reply = send(sock, ("w %s 0x%x %s" % (target, off, part.hex())).encode())
        if reply != "ok":
            print("offset 0x%x failed: %r" % (off, reply))
            return 1
        sent += len(part)
        if (off // CHUNK) % 8 == 0:
            print("  %d/%d bytes" % (sent, len(blob)))
    print("wrote %d bytes to %s" % (sent, target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
