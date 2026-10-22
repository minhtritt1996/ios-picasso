#!/bin/sh
# Build, flash and boot one picasso payload without touching CI.
#
#   tools/picasso-boot.sh xnu/out-0088-run31/kernel.development.sweet "sweetusbstep=0"
#   tools/picasso-boot.sh xnu/out-0088-run31/kernel.development.sweet   # full path
#
# The kernel is packaged with tools/mkpayload.py using the standard bring-up
# cmdline; the second argument is appended to it (boot-arg bisection, e.g.
# sweetusbstep=N or sweetusb=1). The boot image is built with shim/build.sh
# --picasso, written to by-name/boot over recovery ADB, the readback MD5 is
# checked, and the phone is rebooted.
set -eu

kernel=${1:?usage: picasso-boot.sh KERNEL [EXTRA_CMDLINE]}
extra=${2:-}
serial=${SERIAL:-f9f3d7b0}
cd "$(dirname "$0")/.."

base_cmdline="debug=0x14e serial=3 cpus=8 keepsyms=1 -noprogress rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 wdt_test=no_user_checkin ufsrw=1 rootrw=1 ufsroot=darwin launchd_unsecure_cache=1 wdt=-1 sweetfb_noconsole=1 sweetfb_hz=30 sweetfb_keepon=1 sweettouch=0 sweetusb=2 sweetusblog=0 sweethidtest=5"
cmdline="$base_cmdline${extra:+ $extra}"

stamp=$(date +%H%M%S)
payload="shim/out/payload-picasso-$stamp.bin"
python3 tools/mkpayload.py "$kernel" shim/out/picasso-clean.adt "$payload" \
	--cmdline "$cmdline" >/dev/null
bash shim/build.sh "$(cd "$(dirname "$payload")" && pwd)/$(basename "$payload")" --picasso >/dev/null 2>&1

img=shim/out/shim-boot.img
size=$(stat -c %s "$img")
want=$(md5sum "$img" | cut -d' ' -f1)
echo "payload : $payload"
echo "cmdline : $cmdline"
echo "image   : $img ($size bytes, md5 $want)"

adb -s "$serial" push "$img" /tmp/shim-boot.img >/dev/null
adb -s "$serial" shell "dd if=/tmp/shim-boot.img of=/dev/block/bootdevice/by-name/boot bs=4M" >/dev/null 2>&1
got=$(adb -s "$serial" shell "dd if=/dev/block/bootdevice/by-name/boot bs=$size count=1 2>/dev/null | md5sum" | cut -d' ' -f1 | tr -d '\r')
if [ "$got" != "$want" ]; then
	echo "readback mismatch: got $got" >&2
	exit 1
fi
echo "readback: $got (match)"
adb -s "$serial" reboot
