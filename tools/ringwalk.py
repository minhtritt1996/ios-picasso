#!/usr/bin/env python3
"""Walk the kernel console ring over the USB control channel.

The driver copies out of the ring with `logback <n>` (n bytes behind the write
position) and reports the position with `ringpos`, both on the private
EtherType 0x88b6 channel. Reading the whole boot log this way avoids a recovery
round trip, which mattered because the ring tail hid launchd's opinion of the
injected shell job.

Run on the flash host (the machine the phone's ECM interface is attached to):
  ringwalk.py > ring.txt          # whole ring
  ringwalk.py | grep -i sweetsh
"""
import socket
import struct
import sys
import time

OUR_MAC = bytes.fromhex("02005e77ee01")
DEV_MAC = bytes.fromhex("02005e77ee02")
TYPE = 0x88b6
STEP = 1100          # < the 1200-byte window the driver returns


def main():
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    s.bind(("usb0", 0))
    s.settimeout(2.0)

    def ask(cmd):
        s.send(DEV_MAC + OUR_MAC + struct.pack("!H", TYPE) + cmd)
        end = time.time() + 2.0
        while time.time() < end:
            try:
                d = s.recv(2048)
            except socket.timeout:
                return None
            if len(d) > 14 and struct.unpack("!H", d[12:14])[0] == TYPE:
                return d[14:]
        return None

    reply = ask(b"ringpos")
    if reply is None:
        sys.exit("no reply to ringpos: is the kernel running with patch 0133?")
    pos = int(reply.decode().split()[1])
    print("# ringpos %d" % pos, file=sys.stderr)
    chunks = []
    back = 1200
    while back < pos + 1200:
        r = ask(b"logback %d" % back)
        if r is None:
            print("# no reply at back %d, stopping" % back, file=sys.stderr)
            break
        if r:
            chunks.append((pos - back, r))
        back += STEP
    chunks.sort()
    out = b"".join(c for _, c in chunks)
    sys.stdout.buffer.write(out)


if __name__ == "__main__":
    main()
