#!/usr/bin/env python3
"""Dump the PDR domain list that a running pd-mapper serves.

pd-mapper implements the QMI "servreg locator" service (0x40, instance 1).
Its GET_DOMAIN_LIST request (0x21) takes TLV 0x01 = the service name and
answers TLV 0x10 = total domains, 0x11 = database revision, 0x12 = an array
of location entries {name, instance, service_data_valid, service_data}.

Running this on a working Android/Droidian install gives the exact list the
modem expects, which is what the XNU port has to reproduce (see
tools/../xnu/patches/0090 PicassoModem pdm_rx).

Usage: sudo ./pdm-dump.py [service-name] [host]
"""
import socket
import struct
import subprocess
import sys

AF_QIPCRTR = 42
QMI_SERVREG_LOCATOR = 0x40
QMI_GET_DOMAIN_LIST = 0x21


def locator_address():
    """Find the (node, port) of the locator service via qrtr-lookup."""
    try:
        out = subprocess.run(["qrtr-lookup"], capture_output=True, text=True,
                             check=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError) as err:
        raise SystemExit("qrtr-lookup failed: %s" % err)
    for line in out.splitlines():
        f = line.split()
        if len(f) < 6:
            continue
        try:
            svc, ver, inst, node, port = (int(f[0]), int(f[1]), int(f[2]),
                                          int(f[3]), int(f[4]))
        except ValueError:
            continue
        if svc == QMI_SERVREG_LOCATOR:
            print("locator: service %d instance %d node %d port %d"
                  % (svc, inst, node, port), file=sys.stderr)
            return node, port
    raise SystemExit("no locator service in qrtr-lookup")


def qmi_request(sock, node, port, msg, tlvs, txn=1):
    body = b""
    for tlv_type, value in tlvs:
        body += bytes([tlv_type]) + struct.pack("<H", len(value)) + value
    pkt = bytes([0]) + struct.pack("<HHH", txn, msg, len(body) + 4) + body
    sock.sendto(pkt, (node, port))
    while True:
        data, _addr = sock.recvfrom(4096)
        if len(data) < 7:
            continue
        r_txn, r_msg, _r_len = struct.unpack_from("<HHH", data, 1)
        if r_txn == txn and r_msg == msg:
            return data[7:]


def parse_tlvs(body):
    out = {}
    i = 0
    while i + 3 <= len(body):
        t, ln = body[i], struct.unpack_from("<H", body, i + 1)[0]
        out.setdefault(t, []).append(body[i + 3:i + 3 + ln])
        i += 3 + ln
    return out


def main():
    service = sys.argv[1] if len(sys.argv) > 1 else "tms/pdr_enabled"
    node, port = locator_address()
    sock = socket.socket(AF_QIPCRTR, socket.SOCK_DGRAM)
    sock.settimeout(5)
    name = service.encode()
    resp = qmi_request(sock, node, port, QMI_GET_DOMAIN_LIST,
                       [(0x01, bytes([len(name)]) + name)])
    tlvs = parse_tlvs(resp)
    if 0x02 in tlvs:
        result, error = struct.unpack_from("<HH", tlvs[0x02][0])
        print("result %d error %d" % (result, error))
        if result:
            return 1
    total = struct.unpack_from("<H", tlvs[0x10][0])[0] if 0x10 in tlvs else 0
    rev = struct.unpack_from("<H", tlvs[0x11][0])[0] if 0x11 in tlvs else 0
    print("service %s: %d domain(s), db revision %d" % (service, total, rev))
    if 0x12 not in tlvs:
        return 0
    data = tlvs[0x12][0]
    i = 0
    while i < len(data):
        n = data[i]
        name_ = data[i + 1:i + 1 + n].decode(errors="replace")
        i += 1 + n
        inst = struct.unpack_from("<I", data, i)[0]
        i += 4
        valid = data[i]
        i += 1
        sdata = struct.unpack_from("<I", data, i)[0]
        i += 4
        print("  %-32s instance %-6u valid %d data 0x%x"
              % (name_, inst, valid, sdata))
    return 0


if __name__ == "__main__":
    sys.exit(main())
