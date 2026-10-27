#!/bin/sh
# Acceptance check for the goal: a root shell (or SSH) over USB, no recovery involved.
#
#   tools/picasso-acceptance.sh [KEY]
#
# Prints one PASS/FAIL line per requirement and exits 0 only if the shell answers and is
# root. Run it right after a boot; it never touches the phone's disks.
set -u
cd "$(dirname "$0")/.." || exit 1
KEY=${1:-$HOME/.ssh/picasso_key}
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); printf 'PASS  %s\n' "$*"; }
bad()  { FAIL=$((FAIL+1)); printf 'FAIL  %s\n' "$*"; }

python3 tools/picasso-ecm.py up >/dev/null 2>&1 || true

# 1. link up
if ping -c 2 -W 2 192.168.7.2 >/dev/null 2>&1; then ok "ping 192.168.7.2"
else bad "ping 192.168.7.2 (IP path down - check 'ep0 rebuilt'/'configured' in the ring)"; fi

# 2. one shell session, three commands
out=$(timeout 20 python3 - <<'PY' 2>&1
import socket, sys, time
try:
    s = socket.create_connection(('192.168.7.2', 4444), timeout=5)
except OSError as e:
    print('CONNECT %s' % e); sys.exit(0)
s.settimeout(8)
for c in ('help', 'echo acceptance', 'cat /System/Library/CoreServices/SystemVersion.plist'):
    s.sendall((c + '\n').encode()); time.sleep(0.6)
    try:
        print(c, '=>', s.recv(600).decode('latin-1').strip().replace('\n', ' | ')[:90])
    except OSError as e:
        print(c, '=> ERR', e); break
s.close()
PY
)
case "$out" in *"sweetsh"*) ok "shell answers on 4444 ($(printf '%s' "$out" | head -1 | cut -c1-60))";;
*) bad "shell on 4444 ($(printf '%s' "$out" | head -1 | cut -c1-60))";; esac
case "$out" in *"acceptance"*) ok "shell ran 'echo'";; *) bad "shell ran 'echo'";; esac
case "$out" in *"BuildID"*|*"plist"*) ok "shell read a file";; *) bad "shell read a file";; esac

# 3. root: the shell is a launchd daemon / carrier, every command runs as uid 0
if [ -r "$KEY" ]; then
	if sh tools/picasso-test-ssh.sh "$KEY" 2>&1 | grep -q SSH_OVER_USB_OK; then
		ok "ssh root@192.168.7.2 (-i $(basename "$KEY"))"
	else
		bad "ssh root@192.168.7.2 (dropbear did not come up; shell + ioctl/log see NOTES)"
	fi
else
	printf 'SKIP  ssh (no key at %s)\n' "$KEY"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
