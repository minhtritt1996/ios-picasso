#!/bin/sh
# Read the 4 KiB SWEETSTAT block the modem driver writes below the log ring.
#   tools/picasso-status-pull.sh [-s SERIAL]
# Needs the phone in recovery (or any adb-root shell) and reads one block:
# (nblocks - 1026) * 512 of the "darwin" partition.
set -eu
serial=${SERIAL:-f9f3d7b0}
[ "${1:-}" = "-s" ] && { serial=$2; }
ssh_run() { sshpass -p "${REMOTE_PASS:-k60}" ssh -o StrictHostKeyChecking=no \
	-o ConnectTimeout=10 "${REMOTE:-root@100.121.254.115}" "$@"; }
ssh_run "adb -s $serial shell 'N=\$(( \$(blockdev --getsize64 /dev/block/bootdevice/by-name/darwin) / 512 )); dd if=/dev/block/bootdevice/by-name/darwin bs=512 skip=\$((N-1026)) count=1 2>/dev/null'" | tr -d '\r'
