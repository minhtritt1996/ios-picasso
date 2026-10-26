#!/bin/sh
# Put sweetsh in place of a daemon launchd reliably runs, then reboot into the
# already-flashed kernel.
#
# Why a running daemon and not the plist cache or launchd_cache_loader:
#  - an entry added to /System/Library/xpc/launchd.plist is not spawned (launchd
#    drops cache entries whose plist file is missing), and
#  - replacing launchd_cache_loader makes launchd panic: "boot task is not a
#    platform binary ... handshake not initialized".
# A normal daemon has no platform-binary requirement, and replacing a binary's
# *content* keeps HFS+ untouched (same size, same blocks, same catalog record).
#
#   tools/picasso-install-daemon.sh runningboardd     # default target
#   SHELL_VARIANT=signed tools/picasso-install-daemon.sh
#
# Needs the phone in recovery (adb) and a boot image already flashed.
set -eu
target=${1:-runningboardd}
cd "$(dirname "$0")/.."

case "$target" in
runningboardd) block=2136925 blocks=18 size=72352 ;;   # /usr/libexec/runningboardd
containermanagerd) block=2103293 blocks=14 size=54112 ;;
*) echo "unknown target $target" >&2; exit 2 ;;
esac
serial=${SERIAL:-f9f3d7b0}
part=/dev/block/bootdevice/by-name/darwin
shell_bin=tools/sweetsh/sweetsh-${SHELL_VARIANT:-plain}
[ -x "$shell_bin" ] || { echo "build the shell first: tools/sweetsh/build.sh" >&2; exit 1; }
shell_size=$(stat -c %s "$shell_bin")
[ "$shell_size" -le "$size" ] || { echo "shell ($shell_size B) does not fit in $size B" >&2; exit 1; }

region=backup/$target.region
mkdir -p backup
if [ ! -f "$region" ]; then
	adb -s "$serial" exec-out dd if=$part bs=4096 skip=$block count=$blocks 2>/dev/null |
	    head -c $((blocks * 4096)) > "$region"
	echo "backed up $region ($(md5sum "$region" | cut -d' ' -f1))"
fi

python3 - "$shell_bin" "$region" write/$target.region "$size" <<'PY'
import sys
shell, orig, out, size = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
data = open(shell, 'rb').read()
old = open(orig, 'rb').read()
new = data + b'\0' * (size - len(data)) + old[size:]
assert len(new) == len(old)
open(out, 'wb').write(new)
print('  region %d B (shell %d B)' % (len(new), len(data)))
PY

want=$(md5sum write/$target.region | cut -d' ' -f1)
adb -s "$serial" push write/$target.region /tmp/daemon.region >/dev/null
adb -s "$serial" shell "dd if=/tmp/daemon.region of=$part bs=4096 seek=$block conv=notrunc" >/dev/null 2>&1
got=$(adb -s "$serial" exec-out dd if=$part bs=4096 skip=$block count=$blocks 2>/dev/null |
    head -c $((blocks * 4096)) | md5sum | cut -d' ' -f1)
[ "$got" = "$want" ] || { echo "readback mismatch: $got != $want" >&2; exit 1; }
echo "  $target replaced, readback $got (match)"

adb -s "$serial" reboot
echo "rebooting; the shell should answer on 192.168.7.2:4444 as soon as the link is up"
