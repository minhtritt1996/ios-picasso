#!/bin/sh
# Bring SSH up through the shell and prove it, once the phone has an IP.
#
#   1. open one session to the shell on 4444 and run its `ssh` builtin, which execs
#      /private/var/root/dropbear -R -p 22 (a build with a compiled-in host key, so no
#      file has to be created on an HFS+ path that wedges);
#   2. wait for port 22 and then log in with the key in ~/.ssh/picasso_key.
#
#   tools/picasso-test-ssh.sh [KEY]
set -eu
cd "$(dirname "$0")/.." || exit 1
KEY=${1:-$HOME/.ssh/picasso_key}
python3 tools/picasso-ecm.py up >/dev/null 2>&1 || true

echo "== starting dropbear through the shell"
sh tools/picasso-shell.sh 'ssh' 2>&1 | tail -4 || true

echo "== waiting for port 22"
i=0
while [ $i -lt 30 ]; do
	i=$((i+1))
	if python3 - <<'PY'
import socket, sys
try:
    socket.create_connection(('192.168.7.2', 22), timeout=2).close()
except OSError:
    sys.exit(1)
PY
	then
		break
	fi
	sleep 3
done

echo "== ssh as root"
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 \
	-i "$KEY" root@192.168.7.2 'id; uname -a; echo SSH_OVER_USB_OK'
