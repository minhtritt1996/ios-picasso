/*
 * Fill the ADT with the board facts discovered from the live device tree.
 *
 * tools/mkadt.py builds the ADT with placeholder values; the shim replaces
 * them with what the bootloader told us (RAM banks, GIC, splash framebuffer,
 * UFS, CPU MPIDRs) just before it jumps into xnu.
 */
#include "adt.h"
#include "board.h"
#include "hw.h"

/* Below this the bootloader, the shim, the payload and the device tree live. */
#define LOW_MEMORY_END	0x80000000UL
/* xnu maps its RAM in 32 MB blocks: a partial block would map a
 * neighbour carve-out cacheable. */
#define RAM_ALIGN	(32UL << 20)
#define RAM_MAX		(8ULL << 30)

/*
 * The RAM the kernel gets, when the device tree cannot be read.
 *
 * On picasso the bootloader does not pass the tree in x0 and everything
 * below 0x10000000 is protected (a read there is a secure fault), so the
 * shim cannot learn the banks by itself. These are the banks the
 * bootloader hands the kernel: 0x80000000+0x3cc00000, 0x140000000+3 GiB
 * and 0xc0000000+2 GiB; the middle one is the largest contiguous range
 * and no carve-out reaches it.
 */
#ifndef RAM_FALLBACK_BASE
#define RAM_FALLBACK_BASE	0
#endif
#ifndef RAM_FALLBACK_SIZE
#define RAM_FALLBACK_SIZE	0
#endif

static int
range_overlaps(uint64_t a, uint64_t as, uint64_t b, uint64_t bs)
{
	return as != 0 && bs != 0 && a < b + bs && b < a + as;
}

int board_pick_ram(uint64_t *phys_base, uint64_t *mem_size,
    uint64_t excl_base, uint64_t excl_size)
{
	struct range excl[4];
	int nexcl = 0;
	struct range out;

	excl[nexcl].base = 0;
	excl[nexcl].size = LOW_MEMORY_END;
	nexcl++;
	/* Keep xnu below 4 GiB (the modem's rmtfs ALLOC_BUFF field is 32 bits and
	 * the AP may only write banks the bootloader gave HLOS): exclude exactly
	 * the 3 GiB bank at 0x140000000, not a huge span. */
	excl[nexcl].base = 0x100000000ull;
	excl[nexcl].size = 0x100000000ull;
	nexcl++;
	if (excl_size) {
		excl[nexcl].base = excl_base;
		excl[nexcl].size = excl_size;
		nexcl++;
	}
	if (!hw_pick_ram(excl, nexcl, RAM_ALIGN, RAM_MAX, &out)) {
		if (!RAM_FALLBACK_SIZE)
			return 0;
		*phys_base = RAM_FALLBACK_BASE;
		*mem_size = RAM_FALLBACK_SIZE;
		return 1;
	}
	*phys_base = out.base;
	*mem_size = out.size;
	return 1;
}

int board_patch_adt(uintptr_t adt, uint64_t phys_base, uint64_t mem_size)
{
	int bad = 0;

	/* Keep the compiled-in placeholder if we could not pick a range. */
	if (phys_base && mem_size) {
		/*
		 * The modem keeps the rmtfs ALLOC_BUFF address in a 32-bit field,
		 * so its EFS buffer must live below 4 GiB. Carve 2 MiB off the top
		 * of the chunk when that top is inside the low 4 GiB and hand the
		 * window to the kernel in /chosen; xnu never sees it as RAM.
		 */
		/* xnu now runs in the low bank, so its own allocator can hand out
		 * memory below 4 GiB for the modem's rmtfs buffer; no window is
		 * published any more. */
		bad += !adt_set_u64(adt, "chosen", "dram-base", phys_base);
		bad += !adt_set_u64(adt, "chosen", "dram-size", mem_size);
	}

	if (hw.gicd && hw.gicr) {
		uint64_t gic[4] = { hw.gicd, hw.gicd_size, hw.gicr, hw.gicr_size };
		bad += !adt_set_u64s(adt, "arm-io/gic", "reg", gic, 4);
	}
	/*
	 * Only when it was found: without a device tree this is zero, and
	 * writing it would take the interrupt the kernel needs away - the
	 * platform expert panics on a virtual timer that is not a private
	 * peripheral interrupt.
	 */
	if (hw.vtimer_intid)
		bad += !adt_set_u32(adt, "arm-io/gic", "virtual-timer-intid", hw.vtimer_intid);

	if (hw.fb_base) {
		uint64_t fb[2] = { hw.fb_base, hw.fb_size ? hw.fb_size : 0x1700000 };
		bad += !adt_set_u64s(adt, "sweet-fbcon", "reg", fb, 2);
		if (hw.fb_w)
			bad += !adt_set_u32(adt, "sweet-fbcon", "width", hw.fb_w);
		if (hw.fb_h)
			bad += !adt_set_u32(adt, "sweet-fbcon", "height", hw.fb_h);
		if (hw.fb_stride)
			bad += !adt_set_u32(adt, "sweet-fbcon", "stride", hw.fb_stride);
	}

	if (hw.ufs) {
		uint64_t ufs[2] = { hw.ufs, hw.ufs_size ? hw.ufs_size : 0x3000 };
		bad += !adt_set_u64s(adt, "sweet-ufs", "reg", ufs, 2);
	}

	/* cpu0..cpu7 in device tree order; the boot CPU must be the first. */
	for (uint32_t i = 0; i < hw.ncpus && i < 8; i++) {
		char path[] = "cpus/cpu0";
		path[8] = '0' + i;
		bad += !adt_set_u32(adt, path, "reg", (uint32_t)(hw.mpidr[i] & 0xffffff));
	}
	return bad;
}
