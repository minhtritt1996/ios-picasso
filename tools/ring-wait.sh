#!/bin/bash
# Wait for the phone to come back to recovery, then pull the kernel ring and
# report whether it is fresh (contains the boot marker from the cmdline).
#   tools/ring-wait.sh /tmp/ring-bpN.txt [MARKER]
set -u
out=${1:?usage: ring-wait.sh OUT.txt [marker]}
marker=${2:-sweetboot=}
cd "$(dirname "$0")/.."
q() { sshpass -p k60 ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 root@100.121.254.115 "$1" 2>/dev/null; }
for i in $(seq 1 240); do
	if q 'adb devices' | grep -q recovery; then
		tools/sweet-log-pull-remote.sh "$out" -p darwin >/dev/null 2>&1 ||
			tools/sweet-log-pull.sh -p darwin "$out" >/dev/null 2>&1
		echo "pulled $(date +%H:%M:%S) md5 $(md5sum "$out" | cut -d' ' -f1)"
		echo "--- boot-args ---"
		strings -a "$out" | grep -m1 'boot-args' | tr ' ' '\n' | grep -E "^(cpus|sweetboot|sweetfb_off|modem|ufslogsecs|sweethangsecs)=" | tr '\n' ' '
		echo
		if strings -a "$out" | grep -q "$marker"; then
			echo "FRESH: ring carries $marker"
		else
			echo "STALE: no $marker in the ring (this boot wrote nothing)"
		fi
		exit 0
	fi
	sleep 15
done
echo "no recovery after 60 min"
exit 1
