#!/bin/sh
# Restore the whole darwin partition from a coherent image, unattended.
#
# The image was built by hfsplus-tool.py from the pristine rootfs, so its catalog,
# allocation bitmap and file data all agree: /private/var/root/xsweetsh is 0755 and the
# launchd cache runs it. Shipping catalog pieces over the phone's own catalog is what
# produced the lock_rw panic; a whole-image write cannot.
#
#   tools/picasso-restore-rootfs.sh [IMAGE]
#
# Waits for recovery first, because the phone is expected to be panicked.
set -eu
IMG=${1:-/home/tritm/ios8.img}
REMOTE=${REMOTE:-root@100.121.254.115}
REMOTE_PASS=${REMOTE_PASS:-k60}
SERIAL=${SERIAL:-f9f3d7b0}

remote() { sshpass -p "$REMOTE_PASS" ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=20 "$REMOTE" "$@"; }

[ -f "$IMG" ] || { echo "no image: $IMG" >&2; exit 1; }
echo "=== $(date +%T) waiting for recovery"
while ! remote "adb devices 2>/dev/null | grep -q recovery"; do sleep 10; done
echo "=== $(date +%T) recovery up, writing $(basename "$IMG") ($(stat -c %s "$IMG") bytes)"
t0=$(date +%s)
sshpass -p "$REMOTE_PASS" ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=20 "$REMOTE" \
	"adb -s $SERIAL shell 'dd of=/dev/block/bootdevice/by-name/darwin bs=4M conv=fsync 2>&1'" < "$IMG" \
	| tail -3
echo "=== $(date +%T) wrote in $(( $(date +%s) - t0 ))s, verifying"
remote "adb -s $SERIAL shell 'sync; dd if=/dev/block/bootdevice/by-name/darwin bs=4096 skip=1966687 count=1 2>/dev/null | md5sum'" | tail -1
remote "adb -s $SERIAL reboot"
echo "=== $(date +%T) booting, waiting for the shell on UDP 4444"
for i in $(seq 1 40); do
	if remote "cd /tmp && python3 picasso-ecm.py up >/dev/null 2>&1 && python3 picasso-ssh-up.py --check" 2>/dev/null; then
		echo "=== $(date +%T) shell answers"
		remote "cd /tmp && python3 picasso-ecm.py l2 cat /private/var/root/sweetsh.status 300 2>&1 | tail -5"
		exit 0
	fi
	sleep 15
done
echo "=== $(date +%T) shell did not answer"
exit 1
