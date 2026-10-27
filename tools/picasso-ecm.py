#!/usr/bin/env python3
"""Drive the picasso ECM link from the machine the phone is plugged into.

The phone's USB Ethernet gadget needs the host side addressed as 192.168.7.1/24,
and the kernel control channel speaks raw frames with the private EtherType
0x88b6. Both need privileges; grant them once to the interpreter instead of
running everything as root:

  sudo setcap cap_net_raw,cap_net_admin+eip $(readlink -f $(which python3))

Then:
  picasso-ecm.py up                 # find the gadget netdev, address it, bring it up
  picasso-ecm.py l2 info            # one control-channel command
  picasso-ecm.py ring [--grep X]    # walk the kernel console ring over the link
  picasso-ecm.py tcp 4444 "help"    # talk to a listener on the phone
  picasso-ecm.py udp 5140 20        # print UDP datagrams the phone sends for 20 s
"""
import fcntl
import socket
import struct
import sys
import time

OUR_IP = "192.168.7.1"
PHONE_IP = "192.168.7.2"
PHONE_MAC = "02:00:5e:77:ee:02"
CTL_TYPE = 0x88b6

SIOCSIFADDR = 0x8916
SIOCSIFNETMASK = 0x891c
SIOCGIFADDR = 0x8915
SIOCGIFFLAGS = 0x8913
SIOCSIFFLAGS = 0x8914
IFF_UP = 0x1
IFF_RUNNING = 0x40


def find_iface():
    """The gadget registers as usb0/ue0/enx... with the ios-sweet MAC prefix."""
    for name in sorted(__import__('os').listdir('/sys/class/net')):
        try:
            mac = open('/sys/class/net/%s/address' % name).read().strip()
        except OSError:
            continue
        if mac.lower() == "02:00:5e:77:ee:01":
            return name
        if name.startswith(('usb', 'enx')) and mac.lower().startswith('02:00:5e:77:ee'):
            return name
    return None


def set_addr(iface, addr, mask):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ifr = struct.pack('16sH2s4s8s', iface.encode()[:15], socket.AF_INET, b'\x02\x00',
                      socket.inet_aton(addr), b'\x00' * 8)
    fcntl.ioctl(s, SIOCSIFADDR, ifr)
    ifr = struct.pack('16sH2s4s8s', iface.encode()[:15], socket.AF_INET, b'\x00\x00',
                      socket.inet_aton(mask), b'\x00' * 8)
    fcntl.ioctl(s, SIOCSIFNETMASK, ifr)


def if_up(iface):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ifr = struct.pack('16sH14s', iface.encode()[:15], 0, b'\x00' * 14)
    flags = struct.unpack('16sH', fcntl.ioctl(s, SIOCGIFFLAGS, ifr)[:18])[1]
    ifr = struct.pack('16sH14s', iface.encode()[:15], flags | IFF_UP | IFF_RUNNING, b'\x00' * 14)
    fcntl.ioctl(s, SIOCSIFFLAGS, ifr)


def get_addr(iface):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ifr = struct.pack('16sH14s', iface.encode()[:15], 0, b'\0' * 14)
    res = fcntl.ioctl(s, SIOCGIFADDR, ifr)
    return socket.inet_ntoa(res[20:24])


def cmd_up():
    """Bring the gadget interface up with 192.168.7.1/24, verifying it stuck.

    The interface is re-created every time the phone re-enumerates (each boot,
    each recovery round trip), and an address set before that lands on a stale
    netdev - which then looks exactly like a dead link. Check and re-apply.
    """
    iface = find_iface()
    if not iface:
        sys.exit("no gadget interface found (is the phone running XNU with sweetusb=2?)")
    try:
        __import__('subprocess').run(['nmcli', 'device', 'set', iface, 'managed', 'no'],
                                     capture_output=True, timeout=3)
    except Exception:
        pass
    if_up(iface)
    try:
        have = get_addr(iface)
    except OSError:
        have = None
    if have != OUR_IP:
        try:
            set_addr(iface, OUR_IP, "255.255.255.0")
        except OSError as e:
            if e.errno != 17:           # EEXIST
                raise
        try:
            have = get_addr(iface)
        except OSError:
            have = None
    print("%s up, %s/24%s" % (iface, OUR_IP, "" if have == OUR_IP else " (ADDRESS MISSING)"))
    return iface


def ctl_socket(iface):
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    s.bind((iface, 0))
    s.settimeout(0.5)
    return s


def ctl_ask(s, cmd, timeout=2.0):
    our = bytes.fromhex(open('/sys/class/net/%s/address' % IFACE).read().strip().replace(':', ''))
    s.send(bytes.fromhex(PHONE_MAC.replace(':', '')) + our +
           struct.pack('!H', CTL_TYPE) + cmd.encode())
    end = time.time() + timeout
    while time.time() < end:
        try:
            d = s.recv(2048)
        except socket.timeout:
            continue
        if len(d) > 14 and struct.unpack('!H', d[12:14])[0] == CTL_TYPE:
            return d[14:]
    return None


def main():
    global IFACE
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == 'up':
        cmd_up()
        return
    if cmd == 'watch':
        # The host keeps losing 192.168.7.1 (only IPv6 is left) on this machine,
        # which made whole test runs look like the phone was unreachable. Re-add it
        # whenever it disappears; needs only cap_net_admin, which python3 has here.
        import time as _time
        while True:
            try:
                iface = find_iface()
                if iface is None:
                    _time.sleep(2)
                    continue
                try:
                    have = get_addr(iface)
                except OSError:
                    have = None
                if have != OUR_IP:
                    try:
                        __import__('subprocess').run(
                            ['nmcli', 'device', 'set', iface, 'managed', 'no'],
                            capture_output=True, timeout=3)
                    except Exception:
                        pass
                    if_up(iface)
                    set_addr(iface, OUR_IP, "255.255.255.0")
                    print('re-added %s on %s' % (OUR_IP, iface), flush=True)
            except Exception as e:               # never die on a transient netlink error
                print('watch: %s' % e, flush=True)
            _time.sleep(2)
    IFACE = find_iface()
    if not IFACE:
        sys.exit("no gadget interface found")
    if cmd == 'l2':
        s = ctl_socket(IFACE)
        r = ctl_ask(s, ' '.join(sys.argv[2:]) or 'info', timeout=5.0)
        sys.stdout.write((r.decode('latin-1') if r else '[no reply]\n'))
        if r and not r.endswith(b'\n'):
            print()
    elif cmd == 'ring':
        s = ctl_socket(IFACE)
        r = ctl_ask(s, 'ringpos')
        if not r:
            sys.exit('no reply to ringpos')
        pos = int(r.decode().split()[1])
        chunks = []
        back = 1200
        while back < pos + 1200:
            r = ctl_ask(s, 'logback %d' % back)
            if r is None:
                print('# no reply at back %d' % back, file=sys.stderr)
                break
            if r:
                chunks.append((pos - back, r))
            back += 1100
        chunks.sort()
        data = b''.join(c for _, c in chunks).decode('latin-1', 'replace')
        pattern = None
        if '--grep' in sys.argv:
            pattern = sys.argv[sys.argv.index('--grep') + 1]
        for line in data.split('\n'):
            if pattern is None or pattern.lower() in line.lower():
                print(line)
    elif cmd == 'tcp':
        port = int(sys.argv[2])
        lines = sys.argv[3:]
        s = socket.socket()
        s.settimeout(8)
        s.connect((PHONE_IP, port))
        print('[connected]')
        for line in lines or ['help']:
            s.sendall((line + '\n').encode())
            time.sleep(1.0)
            try:
                d = s.recv(4096)
            except socket.timeout:
                print('[no data]')
                break
            sys.stdout.write(d.decode('latin-1'))
        s.close()
    elif cmd == 'udp':
        port = int(sys.argv[2])
        secs = float(sys.argv[3]) if len(sys.argv) > 3 else 10.0
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((OUR_IP, port))
        s.settimeout(1.0)
        end = time.time() + secs
        n = 0
        while time.time() < end:
            try:
                d, a = s.recvfrom(2048)
            except socket.timeout:
                continue
            n += len(d)
            sys.stdout.write(d.decode('latin-1', 'replace'))
        print('# %d bytes' % n, file=sys.stderr)
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
