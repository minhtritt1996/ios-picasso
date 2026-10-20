#!/bin/bash
# Build the shim and wrap it in a boot.img v2 that mirrors the stock PixelOS header.
#
#   shim/build.sh [PAYLOAD] [--picasso] [--dtb PATH] [--extra "args"]
#
#   PAYLOAD      xnu payload (tools/mkpayload.py) to put in the ramdisk; without
#                it the shim runs its own hardware probe.
#   --picasso    Redmi K30 5G (SM7250): the boot image layout differs from
#                sweet's (the ramdisk and the DTB sit further out, because the
#                decompressed kernel is ~48 MB and would overwrite the ramdisk
#                at 16 MB). Proven on the device, see docs/02-boot-image-layout.md.
#   --dtb PATH   device tree to embed (default dtb/boot_unpacked/dtb).
#   --extra      extra kernel command line arguments (appended).
#
# Two things this bootloader insists on, both learned the hard way:
#   - the whole image must be under 256 KB ("fastboot boot" of anything larger
#     hangs the transfer), so the device tree has to be slimmed
#     (tools/picasso-slim-dtb.py);
#   - the image must not claim an OS version the anti-rollback index does not
#     allow, so on picasso the version fields are left empty.
set -euo pipefail
cd "$(dirname "$0")"

CC=aarch64-linux-gnu-gcc
OBJCOPY=aarch64-linux-gnu-objcopy
CFLAGS="-O2 -ffreestanding -nostdlib -fno-pic -mgeneral-regs-only -mstrict-align -fno-stack-protector -Wall -Wextra"
cflags=""

payload=""
dtb="../dtb/boot_unpacked/dtb"
ramdisk_offset=0x1000000
dtb_offset=0x1f00000
cmdline="console=null"
extra=""
osver=(--os_version 17.0.0 --os_patch_level 2035-06)

while [ $# -gt 0 ]; do
	case "$1" in
	--picasso)
		# The layout the phone's own boot image uses (proven on this
		# device): the kernel at 8 KB, the ramdisk and the device tree
		# far out, so the file is as big as the tree's address. The file
		# offset of a part is its RAM address, so the file has to reach
		# that far.
		ramdisk_offset=0x1200000
		dtb_offset=0x1728000
		osver=()
		# The splash framebuffer of this board, in case the device tree
		# cannot be read: without it the console would draw into the
		# wrong memory and the screen would keep the bootloader's logo.
		cflags="$cflags -DFB_BASE=0xa0000000UL"
		# The kernel's RAM, for the same reason: the bootloader does not
		# pass the tree in x0 here, and everything below 0x10000000 is
		# protected. These are the banks it hands the kernel.
		cflags="$cflags -DRAM_FALLBACK_BASE=0x140000000UL -DRAM_FALLBACK_SIZE=0xC0000000UL"
		# The bootloader reads a few things from the command line (which
		# USB controller to set up), so keep the stock one.
		cmdline="earlycon=msm_geni_serial,0x4a90000 androidboot.hardware=qcom androidboot.memcg=1 lpm_levels.sleep_disabled=1 msm_rtb.filter=0x237 service_locator.enable=1 swiotlb=2048 androidboot.usbcontroller=a600000.dwc3 loop.max_part=7 androidboot.usbconfigfs=true androidboot.selinux=permissive console=null"
		;;
	--dtb)
		dtb="$2"
		shift
		;;
	--dtb=*)
		dtb="${1#--dtb=}"
		;;
	--cmdline)
		cmdline="$2"
		shift
		;;
	--cmdline=*)
		cmdline="${1#--cmdline=}"
		;;
	--extra)
		extra="$2"
		shift
		;;
	--extra=*)
		extra="${1#--extra=}"
		;;
	--os-version)
		osver=(--os_version "$2")
		shift
		;;
	*)
		payload="$1"
		;;
	esac
	shift
done
[ -n "$extra" ] && cmdline="$cmdline $extra"

mkdir -p out
for f in start.S shim.c console.c mmu.c fdt.c loader.c ufs.c hw.c adt.c board.c; do
	$CC $CFLAGS $cflags -c $f -o out/${f%.*}.o
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
$CC $CFLAGS $cflags $LDFLAGS \
	out/start.o out/shim.o out/console.o out/mmu.o out/fdt.o out/loader.o out/ufs.o \
	out/hw.o out/adt.o out/board.o -o out/shim.elf
$OBJCOPY -O binary out/shim.elf out/Image
# The file holds only the code and the data; the .bss and the stacks are
# zeroed by the shim itself (this bootloader only takes a 256 KB image).
size=$(aarch64-linux-gnu-nm out/shim.elf | awk '$3=="_load_end"{print strtonum("0x"$1)}')
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
	"${osver[@]}" \
	--cmdline "$cmdline" -o out/shim-boot.img
# A temporary boot (fastboot boot) of an image larger than 256 KB hangs on
# this bootloader; flashing has no such limit.
img=$(stat -c %s out/shim-boot.img)
[ "$img" -le 262144 ] || echo "note: the boot image is $img bytes; it has to be flashed, not booted" >&2
# Host render of the console layout.
gcc -O1 -Wall -o out/host_test console.c host_test.c && ./out/host_test
ls -l out/Image out/shim-boot.img
