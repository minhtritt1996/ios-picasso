#!/bin/sh
# Watch for the phone and finish the job by whichever route it offers, unattended.
#
#  - recovery comes up: install the shell into amfid's 35-block slot, reboot, wait for
#    UDP 4444, then mint the dropbear host key and start the daemon (bringup-shell.sh).
#  - a normal boot comes up with a live L2 command channel: the channel's own
#    put/putend can write /usr/libexec/amfid from the kernel (patch 0153), which needs
#    no recovery at all; reboot and continue the same way.
#
# Both end in tools/picasso-ssh-up.py, which proves `ssh root@192.168.7.2` works.
#
#   tools/picasso-auto-shell.sh [HOURS]
set -u
cd "$(dirname "$0")/.." || exit 1
HOURS=${1:-6}
BIN=tools/sweetsh/sweetsh-udp-signed
KEY="${KEY:-/root/.ssh/picasso_key}"

onhost() {
	sshpass -p "${REMOTE_PASS:-k60}" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 \
		"${REMOTE:-root@100.121.254.115}" "$@"
}

# Everything the host side needs, pushed once.
hostprep() {
	sshpass -p "${REMOTE_PASS:-k60}" scp -o StrictHostKeyChecking=no -q \
		tools/picasso-ecm.py tools/picasso-ssh-up.py tools/picasso-install-binary-over-l2.py \
		"$BIN" "${REMOTE:-root@100.121.254.115}:/tmp/" 2>/dev/null
}

wait_shell() {
	i=0
	while [ $i -lt 40 ]; do
		i=$((i + 1))
		if [ "$(onhost 'cd /tmp; python3 picasso-ecm.py up >/dev/null 2>&1; python3 - <<"PY" 2>/dev/null
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(2)
try:
    s.sendto(b"help", ("192.168.7.2", 4444)); s.recvfrom(2048); print(1)
except OSError:
    print(0)
PY' 2>/dev/null | tail -1)" = 1 ]; then
			return 0
		fi
		sleep 20
	done
	return 1
}

finish() {
	echo "=== shell is up, starting dropbear and checking SSH"
	onhost 'cd /tmp; python3 picasso-ssh-up.py 2>&1 | tail -12'
}

echo "=== $(date +%T) watching, ${HOURS}h"
hostprep
i=0
while [ $i -lt $((HOURS * 240)) ]; do
	i=$((i + 1))

	if onhost "adb devices 2>/dev/null | grep -q recovery"; then
		echo "=== $(date +%T) recovery: installing the carrier"
		tools/picasso-bringup-shell.sh 2>&1 | tail -20
		finish
		exit 0
	fi

	# Normal boot with a live kernel command channel: replace amfid from the kernel.
	if onhost 'cd /tmp; python3 picasso-ecm.py up >/dev/null 2>&1; python3 picasso-ecm.py l2 info 2>/dev/null | grep -q "configured"'; then
		echo "=== $(date +%T) L2 channel answers: writing the shell over /usr/libexec/amfid"
		onhost "cd /tmp && python3 picasso-install-binary-over-l2.py --binary $(basename "$BIN") --dest /usr/libexec/amfid --chunk 384 --reboot 2>&1 | tail -8"
		sleep 30
		if wait_shell; then finish; exit 0; fi
		echo "=== shell did not answer after the L2 install"
	fi

	if wait_shell; then finish; exit 0; fi
	sleep 15
done
echo "=== $(date +%T) gave up after ${HOURS}h"
