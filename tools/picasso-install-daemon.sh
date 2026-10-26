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
#   tools/picasso-install-daemon.sh [usermanagerd|tzinit|launchd|springboard|runningboardd]
#   SHELL_VARIANT=signed tools/picasso-install-daemon.sh
#
# Needs the phone in recovery (adb) and a boot image already flashed.
set -eu
target=${1:-tzinit}
cd "$(dirname "$0")/.."

case "$target" in
# launchd runs this every boot (RunAtLoad) as user mobile and we see it in the
# kernel exec trace ("exec SpringBoard"), so replacing its content is the most
# reliable way to get our binary executed. On-demand daemons (runningboardd) are
# only spawned when something looks up their Mach service.
# The kernel execs this as PID 1 (load_init_program), so no launchd, no plist
# cache and no "platform binary" handshake is involved: the shell runs as root
# with the console as stdio and can bind its own listener.
# Early launchd boot task: the ring shows "exec tzinit" and its exit on every
# boot, before the xpcproxy phase where this port's iOS sometimes stops, so it is
# the most reliable place to get our binary executed.
# launchd execs this early in this port's boot (the ring shows "exec
# usermanagerd") with RunAtLoad and as root, so it is the carrier that actually
# starts - unlike daemons that only run when their Mach service is demanded.
usermanagerd) block=2146394 blocks=171 size=700128 ;;  # /usr/libexec/usermanagerd
tzinit) block=2146349 blocks=31 size=126640 ;;          # /usr/libexec/tzinit
launchd) block=2094543 blocks=133 size=541600 ;;       # /sbin/launchd
springboard) block=296602 blocks=28 size=112960 ;;      # SpringBoard.app/SpringBoard
runningboardd) block=2136925 blocks=18 size=72352 ;;   # /usr/libexec/runningboardd
containermanagerd) block=2103293 blocks=14 size=54112 ;;
*) echo "unknown target $target" >&2; exit 2 ;;
esac
serial=${SERIAL:-f9f3d7b0}
part=/dev/block/bootdevice/by-name/darwin
shell_bin=tools/sweetsh/sweetsh-${SHELL_VARIANT:-signed}
[ -f "$shell_bin" ] || { echo "build the shell first: tools/sweetsh/build.sh" >&2; exit 1; }
shell_size=$(stat -c %s "$shell_bin")
[ "$shell_size" -le "$size" ] || { echo "shell ($shell_size B) does not fit in $size B" >&2; exit 1; }

region=backup/$target.region
mkdir -p backup
if [ ! -f "$region" ]; then
	adb -s "$serial" exec-out dd if=$part bs=4096 skip=$block count=$blocks 2>/dev/null |
	    head -c $((blocks * 4096)) > "$region"
	echo "backed up $region ($(md5sum "$region" | cut -d' ' -f1))"
fi

python3 - "$shell_bin" "$region" /tmp/$target.region "$size" <<'PY'
import sys
shell, orig, out, size = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
data = open(shell, 'rb').read()
old = open(orig, 'rb').read()
new = data + b'\0' * (size - len(data)) + old[size:]
assert len(new) == len(old)
open(out, 'wb').write(new)
print('  region %d B (shell %d B)' % (len(new), len(data)))
PY

want=$(md5sum /tmp/$target.region | cut -d' ' -f1)
adb -s "$serial" push /tmp/$target.region /tmp/daemon.region >/dev/null
adb -s "$serial" shell "dd if=/tmp/daemon.region of=$part bs=4096 seek=$block conv=notrunc" >/dev/null 2>&1
got=$(adb -s "$serial" exec-out dd if=$part bs=4096 skip=$block count=$blocks 2>/dev/null |
    head -c $((blocks * 4096)) | md5sum | cut -d' ' -f1)
[ "$got" = "$want" ] || { echo "readback mismatch: $got != $want" >&2; exit 1; }
echo "  $target replaced, readback $got (match)"

adb -s "$serial" reboot
echo "rebooting; the shell should answer on 192.168.7.2:4444 as soon as the link is up"
