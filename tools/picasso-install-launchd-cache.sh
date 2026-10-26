#!/bin/sh
# Install the launchd-cache launch path for the shell, and put every carrier binary
# back to stock, in one verified pass.
#
# Why: launchd starts daemons from the cache in /System/Library/xpc/launchd.plist,
# and a plist file with no cache entry is never started (ios-flame's HANDOFF says the
# same, and that keys outside /System/Library/LaunchDaemons are ignored). That cache
# is an ordinary file, so it was rebuilt offline with two jobs - com.picasso.sweetsh
# (/private/var/root/sweetsh, root, RunAtLoad) and com.picasso.sshd (dropbear -p 22) -
# at 792,086 bytes, inside its 794,624-byte extent, and it reads back with both keys.
#
#   tools/picasso-install-launchd-cache.sh
#
# Needs the phone in recovery on $REMOTE, and /tmp/ship-*.bin from the preparation
# step (dd of the pristine image for the carriers, of ios8.img for cache + catalog).
set -eu
REMOTE=${REMOTE:-root@100.121.254.115}
REMOTE_PASS=${REMOTE_PASS:-k60}
SERIAL=${SERIAL:-f9f3d7b0}

remote() { sshpass -p "$REMOTE_PASS" ssh -o StrictHostKeyChecking=no "$REMOTE" "$@"; }
putfile() { sshpass -p "$REMOTE_PASS" scp -o StrictHostKeyChecking=no -q "$1" "$REMOTE:$2"; }

remote "adb -s $SERIAL get-state" >/dev/null 2>&1 || {
	echo "phone is not in recovery on $REMOTE" >&2
	exit 1
}

# file            block     blocks
for spec in \
	"/tmp/ship-amfid.bin 2099019 35" \
	"/tmp/ship-safetyalertsd.bin 2136943 89" \
	"/tmp/ship-backboardd.bin 2101702 290" \
	"/tmp/ship-springboard.bin 296602 28" \
	"/tmp/ship-launchd-cache.bin 1966687 194" \
	"/tmp/ship-catalog.bin 185210 2"
do
	set -- $spec
	file=$1; blk=$2; cnt=$3
	want=$(md5sum "$file" | cut -d' ' -f1)
	putfile "$file" /tmp/blk.bin
	got=$(remote "adb -s $SERIAL push /tmp/blk.bin /tmp/blk.bin >/dev/null 2>&1;
		adb -s $SERIAL shell 'dd if=/tmp/blk.bin of=/dev/block/bootdevice/by-name/darwin bs=4096 seek=$blk conv=notrunc; sync;
		dd if=/dev/block/bootdevice/by-name/darwin bs=4096 skip=$blk count=$cnt 2>/dev/null | md5sum'" | cut -d' ' -f1)
	[ "$got" = "$want" ] || { echo "$file @ $blk: $got != $want" >&2; exit 1; }
	echo "  $(basename "$file") @ $blk x$cnt ok"
done

remote "adb -s $SERIAL reboot"
echo "rebooting: launchd should now start com.picasso.sweetsh as root;"
echo "then: python3 tools/picasso-ecm.py l2 cat /private/var/root/sweetsh.status 300"
