#!/bin/sh
# Open one TCP session to the shell and run the commands given (or `help`).
#
#   tools/picasso-shell.sh 'help' 'cat /System/Library/CoreServices/SystemVersion.plist'
#
# Keeps the session open for all commands, which is what the shell serves best: its
# listener is a single accept loop and the client's fd is what repl() reads.
set -eu
cd "$(dirname "$0")/.." || exit 1
python3 tools/picasso-ecm.py up >/dev/null 2>&1 || true
exec python3 - "$@" <<'PY'
import socket, sys, time
cmds = sys.argv[1:] or ['help']
try:
    s = socket.create_connection(('192.168.7.2', 4444), timeout=6)
except OSError as e:
    sys.exit('shell not reachable on 192.168.7.2:4444 (%s)' % e)
s.settimeout(8)
for c in cmds:
    s.sendall((c + '\n').encode())
    time.sleep(0.6)
    try:
        sys.stdout.write(s.recv(4000).decode('latin-1'))
    except OSError as e:
        sys.stdout.write('ERR %s\n' % e)
        break
s.close()
PY
