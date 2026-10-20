#!/bin/bash
# Build the shim and wrap it in a boot.img v2 that mirrors the stock PixelOS header.
#
#   shim/build.sh [PAYLOAD] [--picasso] [--dtb PATH]
#
#   PAYLOAD      xnu payload (tools/mkpayload.py) to put in the ramdisk; without
#                it the shim runs its own hardware probe.
#   --picasso    Redmi K30 5G (SM7250): the boot image layout differs from
#                sweet's (the ramdisk and the DTB sit further out, because the
#                decompressed kernel is ~48 MB and would overwrite the ramdisk
#                at 16 MB). Proven on the device, see docs/02-boot-image-layout.md.
#   --dtb PATH   device tree to embed (default dtb/boot_unpacked/dtb).
set -euo pipefail
cd "$(dirname "$0")"

CC=aarch64-linux-gnu-gcc
OBJCOPY=aarch64-linux-gnu-objcopy
CFLAGS="-O2 -ffreestanding -nostdlib -fno-pic -mgeneral-regs-only -mstrict-align -fno-stack-protector -Wall -Wextra"

payload=""
dtb="../dtb/boot_unpacked/dtb"
ramdisk_offset=0x1000000
dtb_offset=0x1f00000

while [ $# -gt 0 ]; do
	case "$1" in
	--picasso)
		ramdisk_offset=0x1200000
		dtb_offset=0x1728000
		;;
	--dtb)
		dtb="$2"
		shift
		;;
	--dtb=*)
		dtb="${1#--dtb=}"
		;;
	*)
		payload="$1"
		;;
	esac
	shift
done

mkdir -p out
for f in start.S shim.c console.c mmu.c fdt.c loader.c ufs.c hw.c adt.c board.c; do
	$CC $CFLAGS -c $f -o out/${f%.*}.o
done
# The image runs wherever ABL puts it with no relocation pass: only the
# header's image_size (start.o, linked at 0) may be an absolute address.
abs=$(aarch64-linux-gnu-readelf -r out/shim.o out/console.o out/mmu.o out/fdt.o out/loader.o out/ufs.o out/hw.o out/adt.o out/board.o | grep -c ABS64 || true)
[ "$abs" = 0 ] || { echo "error: $abs absolute relocations in C objects" >&2; exit 1; }
# Some binutils do not know --no-warn-rwx-segments; drop it when it is refused.
LDFLAGS="-static -T shim.ld -Wl,--build-id=none -Wl,-N"
if $CC $LDFLAGS -Wl,--no-warn-rwx-segments -o /dev/null start.o 2>/dev/null; then
	LDFLAGS="$LDFLAGS -Wl,--no-warn-rwx-segments"
fi
$CC $CFLAGS $LDFLAGS \
	out/start.o out/shim.o out/console.o out/mmu.o out/fdt.o out/loader.o out/ufs.o \
	out/hw.o out/adt.o out/board.o -o out/shim.elf
$OBJCOPY -O binary out/shim.elf out/Image
# ABL copies the file; pad to image_size so .bss/stacks exist as zeroed bytes.
size=$(aarch64-linux-gnu-nm out/shim.elf | awk '$3=="_end"{print strtonum("0x"$1)}')
truncate -s "$size" out/Image
# Ramdisk: an xnu payload (tools/mkpayload.py) if given as $1, else an empty
# newc cpio (ABL dislikes a zero-size ramdisk on some builds) -> diagnostics.
if [ -n "${payload}" ]; then
	cp "$payload" out/ramdisk.bin
else
	(cd out && rm -rf rd && mkdir rd && cd rd && find . | cpio -o -H newc --quiet | gzip -9 > ../ramdisk.bin)
fi
dtb_arg=()
if [ -f "$dtb" ]; then
	dtb_arg=(--dtb "$dtb")
else
	echo "warning: no device tree at $dtb, building without one" >&2
	echo "         (the bootloader of these phones needs the DTB in the image)" >&2
fi
mkbootimg --header_version 2 --kernel out/Image --ramdisk out/ramdisk.bin \
	"${dtb_arg[@]}" --base 0 --kernel_offset 0x8000 --ramdisk_offset $ramdisk_offset \
	--tags_offset 0x100 --dtb_offset $dtb_offset --pagesize 4096 \
	--os_version 17.0.0 --os_patch_level 2035-06 \
	--cmdline "console=null" -o out/shim-boot.img
# Host render of the console layout.
gcc -O1 -Wall -o out/host_test console.c host_test.c && ./out/host_test
ls -l out/Image out/shim-boot.img
