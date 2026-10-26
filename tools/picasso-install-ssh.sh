#!/bin/sh
# One recovery trip: flash the kernel, install the boot-task shell carrier and put
# SpringBoard back to stock - every write verified by md5 on the device.
#
#   tools/picasso-install-ssh.sh [BOOT_IMG] [CARRIER_BIN]
#
# Defaults: shim/out/shim-boot.img and /tmp/ssc.bin (a shell image signed with
# --ident com.apple.sysstatuscheck, 27 blocks, written over /usr/libexec/sysstatuscheck
# at block 2144605). Both live on the host the phone is plugged into (REMOTE=k60).
#
# Why a boot task and not SpringBoard: launchd on this port stops at the xpcproxy
# phase, so a daemon never runs; a boot task runs early, and the kernel only checks
# that the signature's identifier matches the expected name ('code signing identity
# mismatch for a boot-task ... expected=com.apple.sysstatuscheck').
set -eu
img=${1:-shim/out/shim-boot.img}
carrier=${2:-/tmp/ssc.bin}
REMOTE=${REMOTE:-root@100.121.254.115}
REMOTE_PASS=${REMOTE_PASS:-k60}
SERIAL=${SERIAL:-f9f3d7b0}
CARRIER_BLOCK=${CARRIER_BLOCK:-2144605}
CARRIER_BLOCKS=${CARRIER_BLOCKS:-27}
SPRINGBOARD_BLOCK=${SPRINGBOARD_BLOCK:-296602}
SPRINGBOARD_BLOCKS=${SPRINGBOARD_BLOCKS:-28}
SPRINGBOARD_STOCK=${SPRINGBOARD_STOCK:-/home/tritm/dump/backup/springboard.orig.region}
# Optional catalog patch (a file record whose logicalSize must match the carrier
# image, e.g. after replacing a binary with a shorter one).
CATALOG_FILE=${CATALOG_FILE:-}
CATALOG_BLOCK=${CATALOG_BLOCK:-0}
CATALOG_BLOCKS=${CATALOG_BLOCKS:-2}
cd "$(dirname "$0")/.."

remote() { sshpass -p "$REMOTE_PASS" ssh -o StrictHostKeyChecking=no "$REMOTE" "$@"; }
putfile() { sshpass -p "$REMOTE_PASS" scp -o StrictHostKeyChecking=no -q "$1" "$REMOTE:$2"; }

remote "adb -s $SERIAL get-state" >/dev/null 2>&1 || {
	echo "phone is not in recovery on $REMOTE" >&2
	exit 1
}

# 1. kernel
want=$(md5sum "$img" | cut -d' ' -f1)
echo "kernel  $img ($want)"
putfile "$img" /tmp/shim-boot.img
got=$(remote "adb -s $SERIAL push /tmp/shim-boot.img /tmp/shim-boot.img >/dev/null 2>&1;
	adb -s $SERIAL shell 'dd if=/tmp/shim-boot.img of=/dev/block/bootdevice/by-name/boot bs=4M; sync;
	dd if=/dev/block/bootdevice/by-name/boot bs=4096 count=8921 2>/dev/null | md5sum'" | cut -d' ' -f1)
[ "$got" = "$want" ] || { echo "kernel readback mismatch: $got != $want" >&2; exit 1; }

# 2. boot-task carrier
want=$(md5sum "$carrier" | cut -d' ' -f1)
echo "carrier $carrier ($want) -> block $CARRIER_BLOCK x$CARRIER_BLOCKS"
putfile "$carrier" /tmp/carrier.bin
got=$(remote "adb -s $SERIAL push /tmp/carrier.bin /tmp/carrier.bin >/dev/null 2>&1;
	adb -s $SERIAL shell 'dd if=/tmp/carrier.bin of=/dev/block/bootdevice/by-name/darwin bs=4096 seek=$CARRIER_BLOCK conv=notrunc; sync;
	dd if=/dev/block/bootdevice/by-name/darwin bs=4096 skip=$CARRIER_BLOCK count=$CARRIER_BLOCKS 2>/dev/null | md5sum'" | cut -d' ' -f1)
[ "$got" = "$want" ] || { echo "carrier readback mismatch: $got != $want" >&2; exit 1; }

# 2b. catalog patch, when the carrier is shorter than the file it replaces
if [ -n "$CATALOG_FILE" ]; then
	want=$(md5sum "$CATALOG_FILE" | cut -d' ' -f1)
	echo "catalog $CATALOG_FILE ($want) -> block $CATALOG_BLOCK x$CATALOG_BLOCKS"
	putfile "$CATALOG_FILE" /tmp/catalog.bin
	got=$(remote "adb -s $SERIAL push /tmp/catalog.bin /tmp/catalog.bin >/dev/null 2>&1;
		adb -s $SERIAL shell 'dd if=/tmp/catalog.bin of=/dev/block/bootdevice/by-name/darwin bs=4096 seek=$CATALOG_BLOCK conv=notrunc; sync;
		dd if=/dev/block/bootdevice/by-name/darwin bs=4096 skip=$CATALOG_BLOCK count=$CATALOG_BLOCKS 2>/dev/null | md5sum'" | cut -d' ' -f1)
	[ "$got" = "$want" ] || { echo "catalog readback mismatch: $got != $want" >&2; exit 1; }
fi

# 3. SpringBoard back to stock - but never when SpringBoard *is* the carrier, or this
#    step silently overwrites the carrier that was just written.
if [ -f "$SPRINGBOARD_STOCK" ] && [ "$CARRIER_BLOCK" != "$SPRINGBOARD_BLOCK" ]; then
	want=$(md5sum "$SPRINGBOARD_STOCK" | cut -d' ' -f1)
	echo "springboard stock ($want)"
	putfile "$SPRINGBOARD_STOCK" /tmp/sb.bin
	got=$(remote "adb -s $SERIAL push /tmp/sb.bin /tmp/sb.bin >/dev/null 2>&1;
		adb -s $SERIAL shell 'dd if=/tmp/sb.bin of=/dev/block/bootdevice/by-name/darwin bs=4096 seek=$SPRINGBOARD_BLOCK conv=notrunc; sync;
		dd if=/dev/block/bootdevice/by-name/darwin bs=4096 skip=$SPRINGBOARD_BLOCK count=$SPRINGBOARD_BLOCKS 2>/dev/null | md5sum'" | cut -d' ' -f1)
	[ "$got" = "$want" ] || { echo "springboard readback mismatch: $got != $want" >&2; exit 1; }
fi

remote "adb -s $SERIAL reboot"
echo "flashed, rebooting; watch: tools/picasso-ecm.py up && tools/picasso-ecm.py l2 log"
