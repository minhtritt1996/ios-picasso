#pragma once
#include <stdint.h>

/*
 * Board facts, read from the device tree the bootloader handed us.
 *
 * Everything the shim and the kernel need to know about the board is
 * discovered at boot instead of being compiled in: the RAM banks, the
 * carved-out regions, the splash framebuffer, the GIC, the timer PPIs, the
 * UFS host, USB and the console UART. That way one shim works for several
 * boards (sweet/SM7150 and picasso/SM7250) and survives firmware updates
 * that move carve-outs around.
 */

struct range {
	uint64_t base, size;
};

#define HW_MAX_MEM	16
#define HW_MAX_RSV	160
#define HW_MAX_CPUS	8

struct hw {
	uintptr_t dtb;

	/* Splash framebuffer: the bootloader keeps scanning this out. */
	uint64_t fb_base, fb_size;
	uint32_t fb_w, fb_h, fb_stride, fb_bpp;
	const char *fb_src;

	/* GICv3 */
	uint64_t gicd, gicd_size, gicr, gicr_size, gicr_stride;
	uint32_t vtimer_intid, ptimer_intid;

	uint64_t ufs, ufs_size;		/* UFS host controller */
	uint64_t dwc3, dwc3_size;	/* USB controller */
	uint64_t uart, uart_size;	/* debug console UART */
	uint32_t uart_speed;

	uint32_t ncpus;
	uint64_t mpidr[HW_MAX_CPUS];

	uint32_t nmem;
	struct range mem[HW_MAX_MEM];
	uint32_t nrsv;
	struct range rsv[HW_MAX_RSV];

	/* /chosen/bootargs, and the DSI panel named in it */
	const char *bootargs;
	char panel[64];
};

extern struct hw hw;

/* Fill hw from the device tree; returns 0 or a short reason for failure. */
const char *hw_discover(uintptr_t dtb);

/*
 * Largest RAM range that is free of every reserved region and of the extra
 * exclusions, with both ends aligned to align (xnu maps its RAM in 32 MB
 * blocks: a partial block would map a neighbour carve-out cacheable).
 */
int hw_pick_ram(const struct range *excl, int nexcl, uint64_t align, uint64_t max, struct range *out);
