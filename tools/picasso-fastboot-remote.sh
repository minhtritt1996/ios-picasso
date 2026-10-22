#!/bin/sh
# Build a picasso boot image here and flash it with fastboot on the machine the
# phone is attached to. Works whenever the phone is in the bootloader, so it
# needs neither recovery nor adb.
#
#   tools/picasso-fastboot-remote.sh KERNEL "sweetusbstep=3 sweetusbsmmu=2"
#
# Environment: REMOTE (root@100.121.254.115), REMOTE_PASS (k60).
set -eu

kernel=${1:?usage: picasso-fastboot-remote.sh KERNEL [EXTRA_CMDLINE]}
extra=${2:-}
REMOTE=${REMOTE:-root@100.121.254.115}
REMOTE_PASS=${REMOTE_PASS:-k60}
cd "$(dirname "$0")/.."

base_cmdline="debug=0x14e serial=3 cpus=8 keepsyms=1 -noprogress rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 wdt_test=no_user_checkin ufsrw=1 rootrw=1 ufsroot=darwin launchd_unsecure_cache=1 wdt=-1 sweetfb_noconsole=1 sweetfb_hz=30 sweetfb_keepon=1 sweettouch=0 sweetusb=2 sweetusblog=0 sweethidtest=5"
cmdline="$base_cmdline${extra:+ $extra}"

stamp=$(date +%H%M%S)
payload="shim/out/payload-picasso-$stamp.bin"
python3 tools/mkpayload.py "$kernel" shim/out/picasso-clean.adt "$payload" \
	--cmdline "$cmdline" >/dev/null
bash shim/build.sh "$(cd "$(dirname "$payload")" && pwd)/$(basename "$payload")" --picasso ${SHIM_EXTRA:+--extra "$SHIM_EXTRA"} ${SHIM_CFLAGS:+--shim-cflags "$SHIM_CFLAGS"} >/dev/null 2>&1

img=shim/out/shim-boot.img
echo "cmdline : $cmdline"
echo "image   : $img ($(stat -c %s "$img") bytes, md5 $(md5sum "$img" | cut -d' ' -f1))"

q() { sshpass -p "$REMOTE_PASS" ssh -o StrictHostKeyChecking=no "$REMOTE" "$1"; }
state=$(q 'fastboot devices' | head -1)
echo "fastboot: $state"
[ -n "$state" ] || { echo "no device in fastboot mode" >&2; exit 1; }

sshpass -p "$REMOTE_PASS" scp -o StrictHostKeyChecking=no -q "$img" "$REMOTE:/tmp/shim-boot.img"
q 'fastboot flash boot /tmp/shim-boot.img' 2>&1 | tail -3
q 'fastboot reboot' 2>&1 | tail -1
