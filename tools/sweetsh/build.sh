#!/bin/sh
# Build the freestanding arm64 Mach-O shell.
#
# ld64.lld cannot link a static (dyld-free) arm64 executable, so the code is
# linked as ELF at the address it will live at, dumped flat, and wrapped in a
# hand-written Mach-O with LC_UNIXTHREAD (XNU allows static binaries on a
# DEVELOPMENT kernel and needs MH_DYLDLINK clear).
set -eu
LLVM=${LLVM:-/home/tritm/toolchain/clang+llvm-17.0.6-x86_64-linux-gnu-ubuntu-22.04/bin}
cd "$(dirname "$0")"

$LLVM/clang -target aarch64-linux-gnu -O2 -ffreestanding -nostdlib \
	-fno-builtin -fno-stack-protector -fno-pic -c sweetsh.c -o sweetsh.o
$LLVM/lld -flavor gnu -N -static -nostdlib -Ttext=0x100001000 -e _start \
	sweetsh.o -o sweetsh.elf
$LLVM/llvm-objcopy -O binary sweetsh.elf sweetsh.bin

# .bss is fine as long as the segment's vmsize covers it (the ELF's memory
# extent is passed to mkmacho as --memsize); anything past that would fault on
# the first store, which is exactly how the shell died at far = base + filesize.
bss_over() {
	$LLVM/llvm-nm "$1" | awk -v limit=$((0x100000000 + $2)) '
		/ [bB] / { if (strtonum("0x" $1) >= limit) { print "  " $0; bad = 1 } }
		END { exit bad ? 1 : 0 }'
}
# .bss is zero-fill beyond the file: the Mach-O segment has to cover it, so tell
# the wrapper how far the image's memory reaches (relative to the link base).
memsize() { python3 elfextent.py "$LLVM/llvm-readelf" "$1"; }
MEM=$(memsize sweetsh.elf)
echo "image memory extent: $MEM bytes"
bss_over sweetsh.elf "$MEM" || { echo "refusing to build: .bss outside the image extent" >&2; exit 1; }
python3 mkmacho.py sweetsh.bin sweetsh-plain --memsize "$MEM"
python3 mkmacho.py sweetsh.bin sweetsh-signed --memsize "$MEM" --sign

# quiet build: no /dev/console write, no UDP beacon (both went through kernel
# paths that have wedged the USB driver while the shell was running)
$LLVM/clang -target aarch64-linux-gnu -O2 -ffreestanding -nostdlib -fno-builtin \
	-fno-stack-protector -fno-pic -DSWEETSH_QUIET -c sweetsh.c -o sweetsh-q.o
$LLVM/lld -flavor gnu -N -static -nostdlib -Ttext=0x100001000 -e _start \
	sweetsh-q.o -o sweetsh-q.elf
$LLVM/llvm-objcopy -O binary sweetsh-q.elf sweetsh-q.bin
QMEM=$(memsize sweetsh-q.elf)
bss_over sweetsh-q.elf "$QMEM" || { echo "refusing to build: .bss outside the image extent" >&2; exit 1; }
python3 mkmacho.py sweetsh-q.bin sweetsh-quiet-plain --memsize "$QMEM"
python3 mkmacho.py sweetsh-q.bin sweetsh-quiet-signed --memsize "$QMEM" --sign
# UDP-only serving mode (see sweetsh.c): no TCP listener at all
$LLVM/clang -target aarch64-linux-gnu -O2 -ffreestanding -nostdlib -fno-builtin \
	-fno-stack-protector -fno-pic -DSWEETSH_QUIET -DSWEETSH_UDP_ONLY -c sweetsh.c -o sweetsh-u.o
$LLVM/lld -flavor gnu -N -static -nostdlib -Ttext=0x100001000 -e _start \
	sweetsh-u.o -o sweetsh-u.elf
$LLVM/llvm-objcopy -O binary sweetsh-u.elf sweetsh-u.bin
UMEM=$(memsize sweetsh-u.elf)
bss_over sweetsh-u.elf "$UMEM" || { echo "refusing to build: .bss outside the image extent" >&2; exit 1; }
python3 mkmacho.py sweetsh-u.bin sweetsh-udp-plain --memsize "$UMEM"
python3 mkmacho.py sweetsh-u.bin sweetsh-udp-signed --memsize "$UMEM" --sign
cp sweetsh-plain sweetsh
ls -l sweetsh-plain sweetsh-signed
