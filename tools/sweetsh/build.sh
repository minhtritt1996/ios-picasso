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

# every byte the program can touch must live inside the one __TEXT segment:
# a global in .bss would land past the image and fault on first use.
if $LLVM/llvm-nm sweetsh.elf | grep -qE ' [bB] '; then
	echo "refusing to build: .bss symbol outside the image" >&2
	$LLVM/llvm-nm sweetsh.elf | grep -E ' [bB] ' >&2
	exit 1
fi
python3 mkmacho.py sweetsh.bin 0 sweetsh
ls -l sweetsh
