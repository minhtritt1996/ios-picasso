#!/bin/sh
# Install sweetsh into the picasso rootfs and boot the instrumented kernel.
#
# The iOS rootfs has no shell (/bin/sh and /bin/bash are dangling links into
# /var/jb, which does not exist) and no sshd, so the shell is a freestanding
# arm64 Mach-O static binary (tools/sweetsh). launchd did not spawn a job added
# to its plist cache, so the shell replaces the *content* of a binary launchd
# always runs:
#
#   /usr/libexec/launchd_cache_loader   exec'd by launchd every boot, as root
#
# Replacing file content keeps HFS+ untouched (same size, same blocks, same
# catalog record), and every region is backed up before it is written.
#
# Requires the phone in recovery (adb) on the flash host, and a kernel image:
#   tools/picasso-install-shell.sh /tmp/rev13/kernel.development.sweet [CMDLINE]
set -eu

kernel=${1:?usage: picasso-install-shell.sh KERNEL [CMDLINE]}
cmdline=${2:-}
REMOTE=${REMOTE:-root@100.121.254.115}
REMOTE_PASS=${REMOTE_PASS:-k60}
serial=${SERIAL:-f9f3d7b0}
part=/dev/block/bootdevice/by-name/darwin
cd "$(dirname "$0")/.."

shell_bin=tools/sweetsh/sweetsh
[ -x "$shell_bin" ] || { echo "build the shell first: tools/sweetsh/build.sh" >&2; exit 1; }

ssh_run() {
	sshpass -p "$REMOTE_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$REMOTE" "$@"
}
adb_shell() {
	ssh_run "adb -s $serial shell '$1'"
}
# read BLOCK COUNT from the partition into a local file (exact byte count:
# toybox dd appends a stats line to stdout, so cut it off with head -c)
fetch_region() {
	block=$1; count=$2; out=$3
	ssh_run "adb -s $serial exec-out dd if=$part bs=4096 skip=$block count=$count 2>/dev/null" |
	    head -c $((count * 4096)) > "$out"
}
# write a local file of COUNT blocks at BLOCK, then read it back and compare
write_region() {
	block=$1; count=$2; file=$3
	want=$(md5sum "$file" | cut -d' ' -f1)
	sshpass -p "$REMOTE_PASS" scp -o StrictHostKeyChecking=no -q "$file" "$REMOTE:/tmp/region.bin"
	adb_shell "dd if=/tmp/region.bin of=$part bs=4096 seek=$block conv=notrunc" >/dev/null 2>&1
	got=$(ssh_run "adb -s $serial exec-out dd if=$part bs=4096 skip=$block count=$count 2>/dev/null" |
	    head -c $((count * 4096)) | md5sum | cut -d' ' -f1)
	[ "$got" = "$want" ] || { echo "readback mismatch at block $block: $got != $want" >&2; exit 1; }
	echo "  block $block ($count blocks): readback $got (match)"
}

# 1. the shell replaces launchd_cache_loader's content, padded to its size
size=$(stat -c %s "$shell_bin")
loader_size=72336
loader_blocks=18
loader_block=2111537
[ "$size" -le "$loader_size" ] || { echo "shell ($size) does not fit in $loader_size bytes" >&2; exit 1; }

echo "== 1. backup =="
mkdir -p backup
fetch_region $loader_block $loader_blocks backup/launchd_cache_loader.region
echo "  saved backup/launchd_cache_loader.region ($(stat -c %s backup/launchd_cache_loader.region) bytes)"

echo "== 2. build the replacement region =="
python3 - "$shell_bin" backup/launchd_cache_loader.region work_loader.region "$loader_size" <<'PY'
import sys
shell, orig, out, size = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
data = open(shell, 'rb').read()
old = open(orig, 'rb').read()
assert len(data) <= size, (len(data), size)
new = data + b'\0' * (size - len(data)) + old[size:]
assert len(new) == len(old)
open(out, 'wb').write(new)
print('  region %d bytes, shell %d bytes' % (len(new), len(data)))
PY

echo "== 3. write =="
write_region $loader_block $loader_blocks work_loader.region

echo "== 4. boot =="
if [ -n "$cmdline" ]; then
	BOOT_CMDLINE="$cmdline" tools/picasso-boot-remote.sh "$kernel" | tail -3
else
	tools/picasso-boot-remote.sh "$kernel" | tail -3
fi
