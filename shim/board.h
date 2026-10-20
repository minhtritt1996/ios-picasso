#pragma once
#include <stdint.h>

/*
 * Write the board values we discovered from the live device tree into the
 * Apple device tree (ADT) the shim hands to xnu, so the kernel does not
 * need hard-coded addresses.
 *
 * Returns the number of edits that did not apply (0 is good).
 */
int board_patch_adt(uintptr_t adt, uint64_t phys_base, uint64_t mem_size);

/*
 * Pick the RAM the kernel gets: the largest free range of the banks the
 * bootloader described, 32 MB aligned, above the low memory where the
 * bootloader, the shim, the payload and the device tree live.
 *
 * The result is written into the payload header (which lives in the
 * bootloader's ramdisk, i.e. in RAM) before the segments are placed, so
 * one boot image works whatever the memory layout of the device is.
 */
int board_pick_ram(uint64_t *phys_base, uint64_t *mem_size,
    uint64_t excl_base, uint64_t excl_size);
