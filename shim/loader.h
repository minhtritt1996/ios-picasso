#pragma once
#include <stdint.h>

/* Boot the SWEETXNU payload found in the boot image; returns only on error.
 * info is what the bootloader passed in x0 (on picasso the image itself). */
const char *loader_boot(uintptr_t dtb, uintptr_t info);

/* Where the payload and the boot image were found in the memory the
 * bootloader left behind. */
struct hunt {
	uintptr_t image;		/* "ANDROID!" - the boot image */
	uintptr_t payload;		/* "SWEETXNU" - our payload */
};
extern struct hunt hunt_result;
struct hunt hunt_memory(uintptr_t x0);
