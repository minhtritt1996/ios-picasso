#!/bin/sh
# Put the shell where launchd starts it as a daemon, and give SpringBoard back its own
# file, in one pass from recovery.
#
# Why this shape, measured on this port:
#  - launchd execs SpringBoard directly, so a shell placed in its 28-block file runs - but
#    SpringBoard is watched and stops answering seconds in, and it also holds port 4444.
#  - the launchd job (/System/Library/LaunchDaemons/000-sweetsh.plist, key present in the
#    launchd cache, Program /private/var/root/xsweetsh) is not watched. For it to own 4444,
#    the carrier must be stock.
#  - the shell file must be 0755 (launchd refuses a Program that is not executable) and its
#    record must agree with what is on disk (hfsplus-tool.py writes 0755 now).
#
#   tools/picasso-daemon-shell.sh [SHELL-IMAGE] [--no-springboard]
#
# SHELL-IMAGE defaults to the repo's TCP build (tools/sweetsh/sweetsh-signed). Needs the
# phone in recovery; every range is md5-checked on the device.
set -eu
cd "$(dirname "$0")/.." || exit 1
IMG=${SHELL_IMAGE:-tools/sweetsh/sweetsh-signed}
STOCK=${SPRINGBOARD_STOCK:-}
[ -n "$STOCK" ] || STOCK=/home/tritm/ios-work/root/ios16-root-picasso.img
SERIAL=${SERIAL:-f9f3d7b0}
DO_SB=1
[ "${1:-}" = "--no-springboard" ] && DO_SB=0 && shift
[ $# -gt 0 ] && IMG=$1

say() { printf '%s\n' "$*"; }
die() { say "$*" >&2; exit 1; }

adb -s "$SERIAL" get-state >/dev/null 2>&1 || die "phone is not in recovery ($SERIAL)"

# 1. the daemon file: /private/var/root/xsweetsh, 9 blocks at 3062518, mode 0755
say "== shell -> /private/var/root/xsweetsh"
adb -s "$SERIAL" push "$IMG" /tmp/sh.bin >/dev/null
want=$(md5sum "$IMG" | cut -d' ' -f1)
adb -s "$SERIAL" shell "dd if=/tmp/sh.bin of=/dev/block/bootdevice/by-name/darwin bs=4096 seek=3062518 conv=notrunc 2>/dev/null; sync" >/dev/null
got=$(adb -s "$SERIAL" shell "dd if=/dev/block/bootdevice/by-name/darwin bs=1 count=$(stat -c %s "$IMG") 2>/dev/null | md5sum" | cut -d' ' -f1)
[ "$got" = "$want" ] || die "shell readback $got != $want"
say "   ok"

# 2. SpringBoard back to the stock binary so the carrier stops competing for 4444
if [ "$DO_SB" = 1 ]; then
	[ -f "$STOCK" ] || die "no stock image at $STOCK (set SPRINGBOARD_STOCK)"
	say "== SpringBoard <- stock (28 blocks at 296602)"
	dd if="$STOCK" of=/tmp/sb.bin bs=4096 skip=296602 count=28 status=none
	adb -s "$SERIAL" push /tmp/sb.bin /tmp/sb.bin >/dev/null
	adb -s "$SERIAL" shell "dd if=/tmp/sb.bin of=/dev/block/bootdevice/by-name/darwin bs=4096 seek=296602 conv=notrunc 2>/dev/null; sync" >/dev/null
	say "   ok"
fi

say "== reboot; then: tools/picasso-shell.sh"
adb -s "$SERIAL" reboot
