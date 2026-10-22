#!/bin/sh
# Pull the console ring from the phone's recovery and print the modem boot
# trace (iokit/Kernel/arm/PicassoModem.cpp), plus a compact verdict.
#
#   tools/picasso-modem-log.sh [-s SERIAL] [-p PARTITION] [OUT.txt]
#
# The phone must be back in recovery (adb). Defaults: serial f9f3d7b0,
# partition darwin.
set -eu
serial=f9f3d7b0
part=darwin
while [ $# -gt 0 ]; do
	case "$1" in
	-s) shift; serial=${1:?needs a serial}; shift ;;
	-p) shift; part=${1:?needs a partition}; shift ;;
	*) break ;;
	esac
done
cd "$(dirname "$0")/.."
out=${1:-/tmp/modem-log.txt}
tools/sweet-log-pull.sh -s "$serial" -p "$part" "$out" >/dev/null

echo "== PicassoModem =="
grep -a "PicassoModem:" "$out" | sed 's/\r$//' | tail -40
echo
echo "== verdict =="
if grep -qa "modem is up (MODEM READY)" "$out"; then
	echo "MODEM READY"
elif grep -qa "init_image: ret=0 res=0" "$out"; then
	echo "init_image ok, modem not ready (see slave-kernel / segments lines)"
elif grep -qa "auth_and_reset" "$out"; then
	echo "auth_and_reset reached (modem did not report ready)"
elif grep -qa "PicassoModem:" "$out"; then
	echo "driver ran, stopped before init_image (see the last line)"
else
	echo "no PicassoModem lines: booted without modem=1 or the driver did not run"
fi
echo
echo "full log: $out"
