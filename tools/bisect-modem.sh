#!/bin/bash
# Flash prepared modem payload variants one by one, proving each one really ran.
#
#   tools/bisect-modem.sh VARIANT... [--wait SECONDS]
#
# VARIANT is a file shim/out/variant-VARIANT.img. For each one the script
# clears misc, writes boot, verifies the readback, reboots, requires adb to
# disappear (proof the phone really left recovery) and come back, then checks
# the console ring's own "boot-args" line against the variant's command line so
# a stale ring cannot be mistaken for a result. Every step is logged to
# /tmp/bisect.log.
set -u

REMOTE=${REMOTE:-root@100.121.254.115}
K60_PASS=${K60_PASS:-k60}
SERIAL=${SERIAL:-f9f3d7b0}
HERE=$(cd "$(dirname "$0")" && pwd)
LOG=/tmp/bisect.log

variants=()
WAIT=1800
while [ $# -gt 0 ]; do
	case "$1" in
	--wait) shift; WAIT=${1:-1800} ;;
	*) variants+=("$1") ;;
	esac
	shift
done
[ ${#variants[@]} -gt 0 ] || { echo "usage: $0 VARIANT..." >&2; exit 2; }

say() { echo "$*" | tee -a "$LOG"; }

# Prefer a phone attached to this machine; fall back to the SSH host.
LOCAL=0
q() {
	if [ "$LOCAL" = 1 ]; then
		bash -c "$1"
	else
		sshpass -p "$K60_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 "$REMOTE" "$1"
	fi
}
have_device() {
	if adb devices 2>/dev/null | grep -q "$SERIAL"; then LOCAL=1; return 0; fi
	LOCAL=0
	q "adb devices" 2>/dev/null | grep -q "$SERIAL"
}

say "=== $(date +%F' '%T) waiting for the phone in recovery"
deadline=$(( $(date +%s) + WAIT ))
while ! have_device; do
	[ "$(date +%s)" -ge "$deadline" ] && { say "timeout waiting for recovery"; exit 2; }
	sleep 20
done

for v in "${variants[@]}"; do
	img="$HERE/../shim/out/variant-$v.img"
	if [ ! -f "$img" ]; then
		say "$v: missing $img"
		continue
	fi
	want=$(md5sum "$img" | cut -d' ' -f1)
	size=$(stat -c %s "$img")
	say "$v: flash $want ($size B)"
	if [ "$LOCAL" = 1 ]; then
		cp "$img" /tmp/v.img || { say "$v: copy failed"; continue; }
	else
		sshpass -p "$K60_PASS" scp -q -o StrictHostKeyChecking=no "$img" "$REMOTE:/tmp/v.img" || { say "$v: scp failed"; continue; }
	fi
	q "adb -s $SERIAL push /tmp/v.img /tmp/v.img >/dev/null" || { say "$v: push failed"; continue; }
	q "adb -s $SERIAL shell dd if=/dev/zero of=/dev/block/bootdevice/by-name/misc bs=4096 count=1" >/dev/null 2>&1
	q "adb -s $SERIAL shell dd if=/tmp/v.img of=/dev/block/bootdevice/by-name/boot bs=4M" >/dev/null 2>&1
	got=$(q "adb -s $SERIAL shell 'dd if=/dev/block/bootdevice/by-name/boot bs=$size count=1 2>/dev/null | md5sum'" | tr -d '\r' | cut -d' ' -f1)
	if [ "$got" != "$want" ]; then
		say "$v: READBACK MISMATCH ($got) - not rebooting"
		continue
	fi
	q "adb -s $SERIAL reboot" >/dev/null 2>&1
	say "$v: rebooted, waiting for adb to drop"

	gone=0
	for _ in $(seq 1 15); do
		sleep 3
		if ! have_device; then gone=1; break; fi
	done
	if [ "$gone" != 1 ]; then
		say "$v: adb never dropped - the phone did not leave recovery; stop"
		exit 3
	fi
	say "$v: adb dropped, waiting for recovery to come back"

	back=0
	deadline=$(( $(date +%s) + 240 ))
	while [ "$(date +%s)" -lt "$deadline" ]; do
		sleep 15
		if have_device; then back=1; break; fi
	done
	if [ "$back" != 1 ]; then
		say "$v: did not return to recovery within 240 s (hang)"
		say "$v: RESULT hang"
		exit 4
	fi
	sleep 5
	out=/tmp/ring-$v.txt
	"$HERE/sweet-log-pull-remote.sh" "$out" -p darwin >/dev/null 2>&1
	if [ ! -s "$out" ]; then
		say "$v: ring pull failed"
		continue
	fi
	args=$(grep -a -m1 "PicassoModem: init: boot-args" "$out" | sed 's/.*boot-args //')
	tftp=$(grep -ac "tftp RRQ" "$out")
	wlfw=$(grep -ac "NEW_SERVER svc 45" "$out")
	pd=$(grep -ac "PDR register resp txn .*error 9" "$out")
	say "$v: ring boot-args: ${args:0:140}"
	say "$v: tftp RRQ=$tftp wlfw=$wlfw pdr_err9=$pd"
	say "$v: RESULT booted"
done
say "=== $(date +%F' '%T) done"
