#!/bin/sh
# Pull the SWEET console ring from a phone attached to another machine.
#   tools/sweet-log-pull-remote.sh OUT.txt [-p PARTITION]
# Environment: REMOTE (default root@100.121.254.115), REMOTE_PASS (k60),
# SERIAL (f9f3d7b0). The ring is the last 1025 4 KiB blocks of the partition,
# exactly what tools/sweet-log-pull.sh reads locally.
set -eu
out=${1:?usage: sweet-log-pull-remote.sh OUT.txt [-p PARTITION]}
part=darwin
[ "${2:-}" = "-p" ] && part=${3:?}
REMOTE=${REMOTE:-root@100.121.254.115}
REMOTE_PASS=${REMOTE_PASS:-k60}
serial=${SERIAL:-f9f3d7b0}
here=$(cd "$(dirname "$0")" && pwd)

q() { sshpass -p "$REMOTE_PASS" ssh -o StrictHostKeyChecking=no "$REMOTE" "$1"; }
dev=$(q "adb -s $serial shell 'readlink -f /dev/block/bootdevice/by-name/$part'" | tr -d '\r')
[ -n "$dev" ] || { echo "no $part partition on the phone" >&2; exit 1; }
bytes=$(q "adb -s $serial shell blockdev --getsize64 $dev" | tr -d '\r')
skip=$(( bytes / 4096 - 1025 ))
sshpass -p "$REMOTE_PASS" ssh -o StrictHostKeyChecking=no "$REMOTE" \
	"adb -s $serial exec-out 'dd if=$dev bs=4096 skip=$skip count=1025 2>/dev/null'" > "$out.bin"
echo "$dev: $bytes bytes, blocks $skip.. ($(stat -c %s "$out.bin") bytes)" >&2
python3 "$here/sweet-log.py" "$out.bin" "$out"
