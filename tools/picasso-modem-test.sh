#!/bin/bash
# Run one modem/WLAN test cycle on picasso from whichever host owns the phone.
#
#   tools/picasso-modem-test.sh IMG OUT.txt [WAIT_SECONDS]
#
# IMG is a boot image (shim/out/shim-boot.img). The phone must be in OrangeFox
# recovery on either this machine (local adb) or root@100.121.254.115 (k60).
# The script waits WAIT_SECONDS (default 900) for the device, clears misc,
# writes boot, verifies the readback MD5, reboots, waits for the phone to come
# back (the payload reboots to recovery by itself), pulls the 4 MiB ring from
# the darwin partition and prints the WLAN-relevant lines.
set -u

IMG=${1:?usage: picasso-modem-test.sh IMG OUT.txt [WAIT_SECONDS]}
OUT=${2:?usage: picasso-modem-test.sh IMG OUT.txt [WAIT_SECONDS]}
WAIT=${3:-900}
REMOTE=root@100.121.254.115
K60_PASS=${K60_PASS:-k60}

log() { echo "[$(date +%H:%M:%S)] $*"; }

find_host() {
	if adb devices | grep -q "recovery"; then
		echo local
		return 0
	fi
	if command -v sshpass >/dev/null &&
	   sshpass -p "$K60_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=6 \
		"$REMOTE" 'adb devices' 2>/dev/null | grep -q "recovery"; then
		echo remote
		return 0
	fi
	return 1
}

host_adb() {
	if [ "$HOST" = local ]; then
		adb "$@"
	else
		sshpass -p "$K60_PASS" ssh -o StrictHostKeyChecking=no "$REMOTE" "adb $*"
	fi
}

host_shell() {
	if [ "$HOST" = local ]; then
		bash -c "$1"
	else
		sshpass -p "$K60_PASS" ssh -o StrictHostKeyChecking=no "$REMOTE" "$1"
	fi
}

log "waiting for a phone in recovery (up to ${WAIT}s)"
deadline=$(( $(date +%s) + WAIT ))
while :; do
	HOST=$(find_host) && break
	[ "$(date +%s)" -ge "$deadline" ] && { log "no phone in recovery"; exit 2; }
	sleep 15
done
log "phone owned by $HOST"

size=$(stat -c %s "$IMG")
want=$(md5sum "$IMG" | cut -d' ' -f1)
log "image $size bytes md5 $want"

if [ "$HOST" = local ]; then
	adb push "$IMG" /tmp/shim-boot.img >/dev/null || exit 1
else
	sshpass -p "$K60_PASS" scp -q -o StrictHostKeyChecking=no "$IMG" "$REMOTE:/tmp/shim-boot.img" || exit 1
	host_adb push /tmp/shim-boot.img /tmp/shim-boot.img >/dev/null || exit 1
fi
host_adb -s f9f3d7b0 shell "'dd if=/dev/zero of=/dev/block/bootdevice/by-name/misc bs=32 count=1 2>/dev/null'" >/dev/null
host_adb -s f9f3d7b0 shell "'dd if=/tmp/shim-boot.img of=/dev/block/bootdevice/by-name/boot bs=4M'" 2>/dev/null | tail -1
got=$(host_adb -s f9f3d7b0 shell "'dd if=/dev/block/bootdevice/by-name/boot bs=$size count=1 2>/dev/null | md5sum'" | cut -d' ' -f1)
log "readback $got"
if [ "$got" != "$want" ]; then
	log "READBACK MISMATCH - not rebooting"
	exit 3
fi

log "rebooting into the payload"
host_adb -s f9f3d7b0 reboot >/dev/null

log "waiting for the payload to return to recovery"
deadline=$(( $(date +%s) + 240 ))
while :; do
	sleep 20
	if HOST2=$(find_host); then
		# the payload reboots itself; require a fresh recovery session
		[ "$(date +%s)" -gt $(( $(date +%s) )) ] || true
		break
	fi
	[ "$(date +%s)" -ge "$deadline" ] && { log "phone did not return"; exit 4; }
done
sleep 10
log "pulling the console ring"
if [ "$HOST2" = local ]; then
	tools/sweet-log-pull.sh -p darwin "$OUT" >/dev/null 2>&1
else
	tools/sweet-log-pull-remote.sh "$OUT" -p darwin >/dev/null 2>&1
fi
if [ ! -s "$OUT" ]; then
	log "log pull failed"
	exit 5
fi

log "results:"
grep -a -E "wlan smmu|tftp RRQ|tftp: |WLFW|0x45|wlan_pd|modem corners|wlan rails|PDR register resp|PDR restart|pd-mapper:|NEW_SERVER" "$OUT" | tail -40
