#!/bin/sh
# Bring the shell up as a launchd-started root daemon and, once it answers, start
# dropbear from it and prove SSH works - one pass, no second trip.
#
# The shell only survives the spawn while amfid is itself our shell, because an ad-hoc
# signature is not accepted and stock amfid kills the binary (every binary this port
# has run at boot followed that rule). The launchd cache entry and the plist are
# already in the image, so this script only has to put the carrier back.
#
#   tools/picasso-bringup-shell.sh
set -eu
REMOTE=${REMOTE:-root@100.121.254.115}
REMOTE_PASS=${REMOTE_PASS:-k60}
SERIAL=${SERIAL:-f9f3d7b0}
CARRIER=${CARRIER:-/tmp/carrier-sweetsh.bin}
HOSTKEY=/private/var/root/dropbear_ed25519_host_key

remote() { sshpass -p "$REMOTE_PASS" ssh -o StrictHostKeyChecking=no "$REMOTE" "$@"; }
onhost() { sshpass -p "$REMOTE_PASS" ssh -o StrictHostKeyChecking=no "$REMOTE" "cd /tmp; $*"; }

remote "adb -s $SERIAL get-state" >/dev/null 2>&1 || {
	echo "phone is not in recovery on $REMOTE" >&2
	exit 1
}

# 1. amfid becomes our shell: same 35-block slot, the shell in front and zeros after.
want=$(md5sum "$CARRIER" | cut -d' ' -f1)
sshpass -p "$REMOTE_PASS" scp -o StrictHostKeyChecking=no -q "$CARRIER" "$REMOTE:/tmp/blk.bin"
got=$(remote "adb -s $SERIAL push /tmp/blk.bin /tmp/blk.bin >/dev/null 2>&1;
	adb -s $SERIAL shell 'dd if=/tmp/blk.bin of=/dev/block/bootdevice/by-name/darwin bs=4096 seek=2099019 conv=notrunc 2>/dev/null; sync;
	dd if=/dev/block/bootdevice/by-name/darwin bs=4096 skip=2099019 count=35 2>/dev/null | md5sum'" | cut -d' ' -f1)
[ "$got" = "$want" ] || { echo "carrier: $got != $want" >&2; exit 1; }
echo "carrier in amfid @ 2099019 x35 ok"

remote "adb -s $SERIAL reboot"
echo "booting; waiting for the shell to answer on UDP 4444"

# 2. wait for the shell (launchd job or the carrier itself - either one binds 4444).
up=0
for i in $(seq 1 30); do
	r=$(onhost "python3 picasso-ecm.py up >/dev/null 2>&1; python3 - <<'PY'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(2)
try:
    s.sendto(b'help', ('192.168.7.2', 4444)); s.recvfrom(2048); print(1)
except OSError:
    print(0)
PY" 2>/dev/null | tail -1)
	if [ "$r" = "1" ]; then up=1; break; fi
	sleep 20
done
[ "$up" = 1 ] || { echo "shell did not answer: pull the ring with tools/sweet-log-pull.sh -p darwin" >&2; exit 1; }
echo "shell answers on UDP 4444"

# 3. make the dropbear host key and start the daemon from the shell itself, then prove
# SSH. keygen prints to stdout, so it writes the file with -f and we check for it after.
onhost "python3 picasso-ecm.py up >/dev/null 2>&1;
	python3 picasso-ecm.py l2 exec '/private/var/root/dropbearkey -t ed25519 -f $HOSTKEY' 120 2>/dev/null | tail -2"
onhost "python3 picasso-ecm.py up >/dev/null 2>&1;
	python3 picasso-ecm.py l2 exec '/private/var/root/dropbear -r $HOSTKEY -p 22' 120 2>/dev/null | tail -2"
sleep 5
onhost "python3 picasso-ecm.py up >/dev/null 2>&1;
	python3 picasso-ecm.py l2 cat $HOSTKEY 120 2>/dev/null | head -2" | head -2

echo "SSH check:"
onhost "python3 picasso-ssh-up.py" 2>&1 | tail -12
