#!/bin/sh
# Make launchd start dropbear, and do it with binaries that are actually executable.
#
# Every binary this image grew through hfsplus-tool.py was created 0644, and launchd
# will not start a job whose Program is not executable - which is why the cache entry
# for /private/var/root/sweetsh never produced a process even after it was keyed
# correctly. The tool now creates files 0755, and this pass installs
# /private/var/root/xdropbear (static dropbear, -R mints its own host keys) together
# with a known-good authorized_keys and the launchd cache that runs it:
#   com.picasso.sshd -> /private/var/root/xdropbear -R -p 22   (RunAtLoad, root)
#
# All nine ranges come from /tmp/pl-*.bin, made by writing into ios8.img; each is
# md5-checked on the device before the next one.
set -eu
REMOTE=${REMOTE:-root@100.121.254.115}
REMOTE_PASS=${REMOTE_PASS:-k60}
SERIAL=${SERIAL:-f9f3d7b0}

remote() { sshpass -p "$REMOTE_PASS" ssh -o StrictHostKeyChecking=no "$REMOTE" "$@"; }

remote "adb -s $SERIAL get-state" >/dev/null 2>&1 || {
	echo "phone is not in recovery on $REMOTE" >&2
	exit 1
}

for spec in \
	"/tmp/pl-cat-hdr.bin 0 129" \
	"/tmp/pl-leaf-a.bin 183682 2" \
	"/tmp/pl-leaf-b.bin 1761792 2" \
	"/tmp/pl-leaf-c.bin 1763522 2" \
	"/tmp/pl-cat-cache.bin 185210 2" \
	"/tmp/pl-cache.bin 1966687 194" \
	"/tmp/pl-data.bin 3062447 70" \
	"/tmp/pl-keys.bin 3062517 1"
do
	set -- $spec
	file=$1; blk=$2; cnt=$3
	want=$(md5sum "$file" | cut -d' ' -f1)
	sshpass -p "$REMOTE_PASS" scp -o StrictHostKeyChecking=no -q "$file" "$REMOTE:/tmp/blk.bin"
	got=$(remote "adb -s $SERIAL push /tmp/blk.bin /tmp/blk.bin >/dev/null 2>&1;
		adb -s $SERIAL shell 'dd if=/tmp/blk.bin of=/dev/block/bootdevice/by-name/darwin bs=4096 seek=$blk conv=notrunc 2>/dev/null; sync;
		dd if=/dev/block/bootdevice/by-name/darwin bs=4096 skip=$blk count=$cnt 2>/dev/null | md5sum'" | cut -d' ' -f1)
	[ "$got" = "$want" ] || { echo "$file @ $blk: $got != $want" >&2; exit 1; }
	echo "  $blk x$cnt ok"
done

remote "adb -s $SERIAL reboot"
echo "booting: launchd should start com.picasso.sshd, then ssh root@192.168.7.2"
