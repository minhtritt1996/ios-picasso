#!/usr/bin/env python3
"""Summarise a kernel ring: boot line, spawn targets, last activity, driver state.

  tools/ring-report.py RING_FILE_OR_IMAGE [--partition-offset BLOCK]

Pulling the ring in recovery is one dd; this turns it into the few facts that
matter, in order:

  * `boot`  - the boot-args line, which names the payload (`sweetboot=...`);
  * `spawn` - every `exec <proc> argv <a0> <a1>` in order (patch 0160 prints the
    target, so a launchd spawn stall is named instead of guessed);
  * `boot tasks` - which `Doing boot task` entries ran;
  * `last` - the final non-heartbeat lines, i.e. where the boot stopped;
  * `driver` - the last SWEET HB / USBSTATE line: lock owner, flusher age, event
    thread stage, rx/tx counters.
"""
import re
import struct
import sys

RING_MAGIC = b'SWEETLOG'


def load(path):
    d = open(path, 'rb').read()
    if d[:8] == RING_MAGIC:
        _ver, size, total, flush = struct.unpack('<IIII', d[8:24])
        return d[24:24 + min(total, size - 24)], flush
    return d, None


def main():
    path = sys.argv[1]
    body, flush = load(path)
    text = body.decode('latin-1')
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    boot = [l for l in lines if 'sweetboot=' in l or 'rd=ufsroot0' in l]
    if boot:
        print('boot:', boot[-1][:200])
    if flush is not None:
        print('ring: %d bytes of log, flush counter %d' % (len(body), flush))

    spawn = [l for l in lines if re.match(r'^exec \S+ argv', l)]
    print('spawn (%d):' % len(spawn))
    for l in spawn[-30:]:
        print('  ', l[:150])

    tasks = [l for l in lines if 'Doing boot task' in l]
    if tasks:
        print('boot tasks (%d): %s' % (len(tasks), ', '.join(
            sorted({re.sub(r'.*Event: ', '', l).split(']')[-1].strip() or l[-30:] for l in tasks}))[:200]))

    noise = ('SWEET HB', 'SWEET KICK', 'SWEET USBSTATE', 'launchd.log: fopen')
    rest = [l for l in lines if not l.startswith(noise)]
    print('last activity (%d lines total, showing 20):' % len(rest))
    for l in rest[-20:]:
        print('  ', l[:150])

    for tag in ('SWEET HB', 'SWEET USBSTATE'):
        got = [l for l in lines if l.startswith(tag)]
        if got:
            print('%s (last): %s' % (tag, got[-1][:220]))


if __name__ == '__main__':
    main()
