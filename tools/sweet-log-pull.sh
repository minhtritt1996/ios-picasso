#!/bin/sh
# Read the SWEET console log (kernel patch 0047) from a phone booted into
# OrangeFox recovery (fastboot boot recovery/recovery.img; adb root shell).
#   tools/sweet-log-pull.sh [-s SERIAL] [-p PARTITION] OUT.txt
# Only reads: the last 1025 blocks (4 KiB) of the partition the kernel
# logged into. The kernel writes to the end of the partition it opened as
# root, so on the picasso port that is "darwin", not "userdata".
set -eu

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
	echo "Usage: $(basename "$0") [-s SERIAL] [-p PARTITION] OUT.txt"
	echo "Pull the 4 MiB kernel log ring from PARTITION via recovery ADB."
	echo "Default partition: userdata"
	exit 0
fi

PART="userdata"
while [ $# -gt 1 ]; do
	case "$1" in
	-s) shift; SERIAL=${1:?Error: -s requires a device serial}; shift ;;
	-p) shift; PART=${1:?Error: -p requires a partition name}; shift ;;
	*) break ;;
	esac
done

ADB="adb"
if [ "${1:-}" = "-s" ]; then
	shift
	SERIAL=${1:?Error: -s requires a device serial}
	shift
	ADB="adb -s $SERIAL"
fi

out=${1:?usage: sweet-log-pull.sh [-s SERIAL] [-p PARTITION] OUT.txt}
command -v adb >/dev/null 2>&1 || { echo "Error: adb not found in PATH" >&2; exit 1; }

dev=$($ADB shell "for d in /dev/block/by-name/$PART /dev/block/bootdevice/by-name/$PART; do [ -e \$d ] && readlink -f \$d && break; done" 2>/dev/null | tr -d '\r')
[ -n "$dev" ] || { echo "$PART block device not found on connected device" >&2; exit 1; }
bytes=$($ADB shell blockdev --getsize64 "$dev" | tr -d '\r')
skip=$((bytes / 4096 - 1025))
$ADB exec-out "dd if=$dev bs=4096 skip=$skip count=1025 2>/dev/null" > "$out.bin"
echo "$dev: $bytes bytes, read blocks $skip.. ($(stat -c %s "$out.bin") bytes)" >&2
python3 "$(dirname "$0")/sweet-log.py" "$out.bin" "$out"
