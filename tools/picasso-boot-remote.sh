#!/bin/sh
# Same as picasso-boot.sh, but the phone hangs off another machine: build the
# boot image here, copy it there, and run adb on that host.
#
#   tools/picasso-boot-remote.sh KERNEL "sweetusbstep=3 sweetusbsmmu=1"
#
# Environment: REMOTE (default root@100.121.254.115), REMOTE_PASS (default k60),
# SERIAL (default f9f3d7b0).
set -eu

kernel=${1:?usage: picasso-boot-remote.sh KERNEL [EXTRA_CMDLINE]}
extra=${2:-}
REMOTE=${REMOTE:-root@100.121.254.115}
REMOTE_PASS=${REMOTE_PASS:-k60}
serial=${SERIAL:-f9f3d7b0}
cd "$(dirname "$0")/.."

base_cmdline="debug=0x14e serial=3 cpus=8 keepsyms=1 -noprogress rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 wdt_test=no_user_checkin ufsrw=1 rootrw=1 ufsroot=darwin launchd_unsecure_cache=1 wdt=-1 sweetfb_noconsole=1 sweetfb_hz=30 sweetfb_keepon=1 sweettouch=0 sweetusb=2 sweetusblog=0 sweethidtest=5"
cmdline="$base_cmdline${extra:+ $extra}"

stamp=$(date +%H%M%S)
payload="shim/out/payload-picasso-$stamp.bin"
python3 tools/mkpayload.py "$kernel" shim/out/picasso-clean.adt "$payload" \
	--cmdline "$cmdline" >/dev/null
bash shim/build.sh "$(cd "$(dirname "$payload")" && pwd)/$(basename "$payload")" --picasso ${SHIM_EXTRA:+--extra "$SHIM_EXTRA"} ${SHIM_CFLAGS:+--shim-cflags "$SHIM_CFLAGS"} >/dev/null 2>&1

img=shim/out/shim-boot.img
size=$(stat -c %s "$img")
want=$(md5sum "$img" | cut -d' ' -f1)
echo "cmdline : $cmdline"
echo "image   : $img ($size bytes, md5 $want)"

ssh_run() {
	sshpass -p "$REMOTE_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$REMOTE" "$@"
}
remote_adb() {
	# $1 is one shell string; it is evaluated by the remote shell.
	ssh_run "adb -s $serial $1"
}

echo "remote  : $REMOTE"
remote_adb "get-state" 2>/dev/null || true

sshpass -p "$REMOTE_PASS" scp -o StrictHostKeyChecking=no -q "$img" "$REMOTE:/tmp/shim-boot.img"
remote_adb "push /tmp/shim-boot.img /tmp/shim-boot.img >/dev/null"
remote_adb "shell 'dd if=/tmp/shim-boot.img of=/dev/block/bootdevice/by-name/boot bs=4M'" >/dev/null 2>&1
got=$(remote_adb "shell 'dd if=/dev/block/bootdevice/by-name/boot bs=$size count=1 2>/dev/null | md5sum'" | cut -d' ' -f1 | tr -d '\r')
if [ "$got" != "$want" ]; then
	echo "readback mismatch: got $got" >&2
	exit 1
fi
echo "readback: $got (match)"
remote_adb "reboot"
