#!/bin/sh
# Back up what a shim test on picasso can break: the partition table of LUN0
# and the boot partition itself. Read only - nothing is written to the phone.
#
#   tools/picasso-backup.sh [OUTDIR]        (default /tmp/opencode/picasso-backup)
#
# Run this from a rooted shell (TWRP over adb). It needs a device with
# adb: the script is meant to be run on the PC with the phone attached.
set -eu
out=${1:-/tmp/opencode/picasso-backup}
mkdir -p "$out"

say() { echo "== $*"; }

say "device"
adb get-state
adb shell 'getprop ro.product.device; getprop ro.build.version.release'

say "partition list"
adb shell 'ls -l /dev/block/bootdevice/by-name/ 2>/dev/null | head -60' | tee "$out/by-name.txt"

say "LUN0 size"
total_kb=$(adb shell 'blockdev --getsize64 /dev/block/sda 2>/dev/null || cat /sys/block/sda/size' | tr -d '\r')
echo "LUN0 = $total_kb bytes" | tee "$out/lun0-size.txt"
# the size may come back in 512 byte blocks or in kibibytes; normalise to bytes
case "$total_kb" in
*[0-9]) ;;
*) total_kb=0 ;;
esac

say "GPT head and tail of LUN0"
adb shell 'dd if=/dev/block/sda bs=4096 count=6 2>/dev/null' > "$out/lun0-gpt-head.bin"
# /sys/block/sda/size counts 512 byte sectors; the tail lives in the last
# 4 KiB blocks.
sect=$(adb shell 'cat /sys/block/sda/size 2>/dev/null' | tr -d '\r')
if [ -n "$sect" ]; then
	skip=$((sect / 8 - 6))
	adb shell "dd if=/dev/block/sda bs=4096 skip=$skip count=6 2>/dev/null" > "$out/lun0-gpt-tail.bin"
	echo "LUN0 = $sect sectors of 512; tail: skip=$skip blocks of 4096" | tee "$out/lun0-tail.txt"
fi

say "partition table (sgdisk, if the recovery has it)"
adb shell 'sgdisk --print /dev/block/sda 2>/dev/null || fdisk -l /dev/block/sda 2>/dev/null' > "$out/gpt-print.txt" || true
head -40 "$out/gpt-print.txt"

say "the boot partition"
adb shell 'dd if=/dev/block/bootdevice/by-name/boot bs=4M 2>/dev/null' > "$out/boot-from-phone.img"
ls -l "$out"

say "checksums"
( cd "$out" && sha256sum * 2>/dev/null ) | tee "$out/SHA256SUMS"

say "done - the phone was only read from"
