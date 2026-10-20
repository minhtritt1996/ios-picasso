#!/bin/sh
# Put Droidian back on the phone and read the memory layout from its kernel.
#
#   tools/picasso-raminfo.sh [--restore]
#
# --restore  flash the phone's own boot image back first (the shim probe
#            replaced it), then reboot and wait for adb.
#
# Read only: the flash is the phone's own image, and everything after that
# only reads /proc.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
droidian=${DROIDIAN_BOOT_IMAGE:-$HOME/droidian/output/halium-boot-picasso.img}

if [ "${1:-}" = "--restore" ]; then
	echo "== restoring the phone's own boot image"
	ls -l "$droidian"
	timeout 300 fastboot flash boot "$droidian"
	timeout 60 fastboot reboot
	echo "== waiting for the phone (adb)"
	i=0
	while [ $i -lt 60 ]; do
		if adb devices 2>/dev/null | grep -q "device$"; then break; fi
		sleep 5
		i=$((i + 1))
	done
	adb wait-for-device
	sleep 5
fi

echo "== /proc/cmdline"
adb shell 'cat /proc/cmdline' | tr -d '\r'

echo
echo "== /proc/device-tree/chosen/bootargs"
adb shell 'cat /proc/device-tree/chosen/bootargs 2>/dev/null' | tr -d '\r'

echo
echo "== memory banks (from the live device tree the bootloader handed the kernel)"
adb shell 'for d in /proc/device-tree/memory@*; do printf "%s: " "$d"; od -An -tx8 "$d/reg" 2>/dev/null; done' | tr -d '\r'

echo
echo "== /proc/iomem (first lines)"
adb shell 'head -40 /proc/iomem 2>/dev/null' | tr -d '\r'

echo
echo "== the tree the kernel got: is it in /proc/device-tree?"
adb shell 'ls /proc/device-tree/ 2>/dev/null | head -20' | tr -d '\r'
adb shell 'ls -l /proc/device-tree/reserved-memory 2>/dev/null | head -20' | tr -d '\r'
