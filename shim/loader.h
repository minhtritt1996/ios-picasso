#pragma once
#include <stdint.h>

/* Boot the SWEETXNU payload found in the boot image; returns only on error.
 * info is what the bootloader passed in x0 (on picasso the image itself). */
const char *loader_boot(uintptr_t dtb, uintptr_t info);
