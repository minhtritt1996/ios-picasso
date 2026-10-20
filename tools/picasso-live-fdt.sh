#!/bin/sh
# Pull the live device tree from a running picasso and print the board facts.
#
#   tools/picasso-live-fdt.sh [OUTDIR]
#
# The bootloader fills /memory in at boot, so the static DTB in the boot image
# still says `reg = <0 0 0 0>`. The running kernel exposes the tree it was
# given in /proc/device-tree, and that one has the real RAM banks.
#
# Needs the phone connected with adb (usb debugging enabled). Read only: it
# pulls files and writes them under OUTDIR (default /tmp/picasso-fdt).
set -eu
out=${1:-/tmp/picasso-fdt}
here=$(cd "$(dirname "$0")" && pwd)

adb get-state >/dev/null
mkdir -p "$out"

# /proc/device-tree is a directory of binary properties; tar it as-is.
adb exec-out 'tar -cf - -C /proc device-tree 2>/dev/null || cat /proc/device-tree/* > /dev/null' > "$out/dt.tar" 2>/dev/null || true
if [ ! -s "$out/dt.tar" ]; then
	echo "could not read /proc/device-tree over adb" >&2
	echo "is the phone running a system with /proc mounted (droidian/android)?" >&2
	exit 1
fi
tar -xf "$out/dt.tar" -C "$out" 2>/dev/null || true

# The kernel only exports the tree as a directory; rebuild a flat DTB from
# the properties we need if the kernel did not give us one.
if [ -f "$out/device-tree/__symbols__" ]; then
	echo "device tree pulled to $out/device-tree"
else
	echo "device tree pulled to $out (properties only, no flat DTB)" >&2
fi

# The live tree is a directory, so rebuild the numbers with the same reader
# the shim uses by asking the kernel for the values it published.
echo
echo "=== /proc/iomem (the memory the kernel got) ==="
adb shell 'cat /proc/iomem 2>/dev/null | head -60' || true
echo
echo "=== /proc/cmdline ==="
adb shell 'cat /proc/cmdline' || true
echo
echo "=== chosen from /proc/device-tree ==="
for p in bootargs linux,initrd-start linux,initrd-end; do
	if adb shell "test -e /proc/device-tree/chosen/$p" 2>/dev/null; then
		printf '%s: ' "$p"
		adb exec-out "cat /proc/device-tree/chosen/$p" 2>/dev/null | tr -d '\0'
		echo
	fi
done
echo
echo "=== memory banks from the live tree ==="
adb shell 'for d in /proc/device-tree/memory@*; do echo -n "$d: "; od -An -tx8 -N16 "$d/reg" 2>/dev/null; done' || true
