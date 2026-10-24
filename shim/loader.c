/*
 * Milestone 4: act as iBoot for xnu.
 *
 * mkpayload.py already resolved the kernel's chained fixups and built the
 * ADT, so this only has to: find the payload in the ABL ramdisk, copy the
 * segments to phys_base + (vmaddr - virt_base), place the ADT and
 * boot_args behind the kernel, and jump to the entry point with the MMU
 * and caches off, x0 = boot_args (physical).
 */
#include <stdint.h>
#include "board.h"
#include "console.h"
#include "fdt.h"
#include "loader.h"

#define BOOT_LINE_LENGTH	608
#define PAGE_16K		0x4000UL

struct payload_seg {
	uint64_t vmaddr, vmsize, file_off, file_size;
};

struct payload {
	char magic[8];
	uint32_t version, nsegs;
	uint64_t virt_base, entry_va, adt_off, adt_size, kernel_end;
	uint64_t phys_base, mem_size;
	char cmdline[BOOT_LINE_LENGTH];
	struct payload_seg segs[];
};

/* pexpert/pexpert/arm64/boot.h, Revision/Version 2 */
struct boot_video {
	uint64_t v_baseAddr, v_display, v_rowBytes, v_width, v_height, v_depth;
};

struct boot_args {
	uint16_t Revision, Version;
	uint64_t virtBase, physBase, memSize, topOfKernelData;
	struct boot_video Video;
	uint32_t machineType;
	uint64_t deviceTreeP;
	uint32_t deviceTreeLength;
	char CommandLine[BOOT_LINE_LENGTH];
	uint64_t bootFlags, memSizeActual;
};

_Static_assert(__builtin_offsetof(struct payload, segs) == 0x2a8, "payload header layout");
_Static_assert(__builtin_offsetof(struct boot_args, Video) == 40, "boot_args layout");
_Static_assert(__builtin_offsetof(struct boot_args, CommandLine) == 108, "boot_args layout");
_Static_assert(sizeof(struct boot_args) == 736, "boot_args layout");

/* MMU off: everything is Device memory, so copy in aligned 8-byte words. */
static void copy64(uintptr_t dst, uintptr_t src, uint64_t n)
{
	volatile uint64_t *d = (volatile uint64_t *)dst;
	volatile const uint64_t *s = (volatile const uint64_t *)src;
	for (uint64_t i = 0; i < (n + 7) / 8; i++)
		d[i] = s[i];
}

/*
 * Where the bootloader put the ramdisk, when the device tree does not say.
 * The phone's own image carries a static tree (its /memory is still
 * `reg = <0 0 0 0>`) and it has no initrd properties, and the bootloader
 * does not pass one either - so the address from the boot image header is
 * the only place the payload can be. Both values are compiled in from the
 * image that was built (shim/build.sh).
 */
#ifndef RAMDISK_ADDR
#define RAMDISK_ADDR 0
#endif
#ifndef RAMDISK_SIZE
#define RAMDISK_SIZE 0
#endif
/* Where the payload and the device tree sit in the boot image, when the
 * bootloader leaves the whole image in memory (shim/build.sh measures it). */
#ifndef RAMDISK_FILE_OFFSET
#define RAMDISK_FILE_OFFSET 0
#endif
#ifndef DTB_FILE_OFFSET
#define DTB_FILE_OFFSET 0
#endif
/* Where the payload and the device tree sit inside the kernel part of the
 * boot image (shim/build.sh measures it). */
#ifndef PAYLOAD_OFF
#define PAYLOAD_OFF 0
#endif
#ifndef DTB_OFF
#define DTB_OFF 0
#endif
#ifndef KERNEL_ADDR
#define KERNEL_ADDR 0x8000
#endif

#define MAX_SEGS	32

/*
 * The payload header is copied out before it is changed: the bootloader may
 * map its ramdisk read-only, and the shim only ever needs to write two
 * fields in it (the RAM the kernel gets).
 */
static struct payload local_hdr;
static struct payload_seg local_segs[MAX_SEGS];

static void zero64(uintptr_t dst, uint64_t n)
{
	volatile uint64_t *d = (volatile uint64_t *)dst;
	for (uint64_t i = 0; i < n / 8; i++)
		d[i] = 0;
}

static int magic_ok(const char *m)
{
	static const char want[] = "SWEETXNU";
	for (int i = 0; i < 8; i++)
		if (m[i] != want[i])
			return 0;
	return 1;
}

static int ascii_ok(const char *m, const char *want)
{
	for (int i = 0; i < 8; i++)
		if (m[i] != want[i])
			return 0;
	return 1;
}

typedef void (*xnu_entry_t)(uint64_t boot_args_pa);

/*
 * Hunt for the payload in the memory the bootloader left behind.
 *
 * The bootloader keeps its heap (and the image it read) in ordinary memory
 * near the address it passes in x0. It frees most of it afterwards, which is
 * why the addresses the boot header names are not mapped any more: reading
 * them is answered with a secure fault that kills the core.
 *
 * So: walk the heap in both directions, a page at a time, and look for the
 * two magics. The scan prints every hit as it goes, so a page that is not
 * mapped ends the walk but leaves the hits on the screen.
 */
/* What the hunt found; the device tree is looked for in the image. */
struct hunt hunt_result;

#define SWEET_MAGIC0	0x45455753U	/* "SWEE" */
#define SWEET_MAGIC1	0x554e5854U	/* "TXNU" */
#define ANDROID_MAGIC0	0x52444e41U	/* "ANDR" */
#define ANDROID_MAGIC1	0x2144494fU	/* "OID!" */

#define HUNT_WINDOW	0x4000000UL	/* 64 MiB each way */

struct hunt hunt_memory(uintptr_t x0)
{
	struct hunt h = { 0, 0 };
	uintptr_t up = x0, down = x0;

	for (unsigned round = 0; round < 2 * (HUNT_WINDOW / 4096); round++) {
		uintptr_t a = (round & 1) ? down : up;
		uint32_t w0 = *(volatile uint32_t *)a;
		uint32_t w1 = *(volatile uint32_t *)(a + 4);

		if (w0 == SWEET_MAGIC0 && w1 == SWEET_MAGIC1) {
			h.payload = a;
			con_hex("HUNT", a);
			con_skip();
			/* The payload is what we came for: stop here, the rest of
			 * the heap may not be mapped and a read there kills the
			 * core. */
			break;
		} else if (w0 == ANDROID_MAGIC0 && w1 == ANDROID_MAGIC1) {
			h.image = a;
			con_hex("IMAGE", a);
			con_skip();
		}
		if (round & 1) {
			if (down <= x0 - HUNT_WINDOW || down < 0x10000000UL)
				break;
			down -= 4096;
		} else {
			if (up >= x0 + HUNT_WINDOW)
				break;
			up += 4096;
		}
	}
	hunt_result = h;
	return h;
}

const char *loader_boot(uintptr_t dtb, uintptr_t info)
{
	uint64_t rd_start, rd_end;
	struct hunt h = hunt_result;

	if (h.payload) {
		rd_start = h.payload;
		rd_end = rd_start + RAMDISK_SIZE;
		con_str("INITRD", "found by the hunt");
		con_skip();
	} else if (PAYLOAD_OFF) {
		/*
		 * The payload rides in the kernel part of the boot image: the
		 * bootloader loads that part for sure - the shim itself runs from
		 * it - and the payload is at a fixed offset inside it.
		 */
		rd_start = KERNEL_ADDR + PAYLOAD_OFF;
		rd_end = rd_start + RAMDISK_SIZE;
		con_str("INITRD", "in the kernel part");
		con_skip();
	} else if (info) {
		if (ascii_ok((const char *)info, "ANDROID!")) {
			rd_start = info + RAMDISK_FILE_OFFSET;
			rd_end = rd_start + RAMDISK_SIZE;
			con_str("INITRD", "inside the boot image");
			con_skip();
		} else if (magic_ok((const char *)info)) {
			rd_start = info;
			rd_end = info + RAMDISK_SIZE;
			con_str("INITRD", "at x0");
			con_skip();
		} else {
			rd_start = RAMDISK_ADDR;
			rd_end = rd_start + RAMDISK_SIZE;
			con_str("INITRD", "from the boot image header");
			con_skip();
		}
	} else if (!fdt_chosen_u64(dtb, "linux,initrd-start", &rd_start) ||
	    !fdt_chosen_u64(dtb, "linux,initrd-end", &rd_end)) {
		if (!RAMDISK_ADDR || !RAMDISK_SIZE)
			return "no initrd in /chosen";
		rd_start = RAMDISK_ADDR;
		rd_end = rd_start + RAMDISK_SIZE;
		con_str("INITRD", "from the boot image header");
		con_skip();
	}
	con_hex2("INITRD", "lo=", rd_start, "hi=", rd_end);

	struct payload *p = (struct payload *)rd_start;
	if (!magic_ok(p->magic))
		return "no SWEETXNU payload";
	if (p->version != 1 || (p->adt_off & 7) || (rd_start & 7))
		return "bad payload header";
	if (p->nsegs > MAX_SEGS)
		return "too many segments in the payload";

	/* Copy the header and the segment table out before anything is changed. */
	uint64_t hsz = sizeof(struct payload) + p->nsegs * sizeof(struct payload_seg);
	copy64((uintptr_t)&local_hdr, rd_start, hsz);
	copy64((uintptr_t)local_segs, rd_start + sizeof(struct payload),
	    p->nsegs * sizeof(struct payload_seg));
	const struct payload_seg *segs = local_segs;
	p = &local_hdr;

	uint64_t pa_of_va_base = p->phys_base - p->virt_base;	/* pa = va + this */
	con_hex2("PHYS", "base=", p->phys_base, "size=", p->mem_size);
	con_hex("VIRT", p->virt_base);
	con_hex("ENTRY", p->entry_va);

	/*
	 * Choose the kernel's RAM from the memory layout the bootloader
	 * described in the device tree, and patch the copy of the payload
	 * header before the segments are placed. Without this the board's RAM
	 * layout would have to be compiled into every boot image.
	 */
	uint64_t pbase = p->phys_base, msize = p->mem_size;
	if (board_pick_ram(&pbase, &msize, rd_start, rd_end - rd_start)) {
		p->phys_base = pbase;
		p->mem_size = msize;
		pa_of_va_base = p->phys_base - p->virt_base;
		/* Printed after the copy: a con_hex here has stalled the shim
		 * before COPY on the low-bank layouts. */
	}

	/* The payload must not overlap where the kernel goes. */
	if (rd_end > p->phys_base && rd_start < p->phys_base + p->mem_size)
		return "ramdisk overlaps kernel memory";

	con_str("COPY", "segments to the kernel memory");
	con_skip();
	for (uint32_t i = 0; i < p->nsegs; i++) {
		const struct payload_seg *s = &segs[i];
		uintptr_t dst = s->vmaddr + pa_of_va_base;
		copy64(dst, rd_start + s->file_off, s->file_size);
		if (s->vmsize > s->file_size)
			zero64(dst + ((s->file_size + 7) & ~7UL), s->vmsize - ((s->file_size + 7) & ~7UL));
	}
	con_str("COPY", "done");
	con_hex("RAM base", p->phys_base);
	con_hex("RAM size", p->mem_size);
	con_skip();
	con_hex2("SEGS", "n=", p->nsegs, "end=", p->kernel_end + pa_of_va_base);

	/* ADT, then boot_args, behind the kernel; everything below topOfKernelData is ours. */
	uintptr_t adt_pa = p->kernel_end + pa_of_va_base;
	copy64(adt_pa, rd_start + p->adt_off, p->adt_size);

	/* Fill the board values we discovered into the ADT before xnu reads it. */
	int bad = board_patch_adt(adt_pa, p->phys_base, p->mem_size);
	if (bad) {
		con_str("ADT", "edits did not apply");
		con_dec("failed", (uint64_t)bad);
	}
	uintptr_t ba_pa = (adt_pa + p->adt_size + PAGE_16K - 1) & ~(PAGE_16K - 1);
	uintptr_t top = ba_pa + PAGE_16K;

	zero64(ba_pa, PAGE_16K);
	volatile struct boot_args *ba = (volatile struct boot_args *)ba_pa;
	ba->Revision = 2;
	ba->Version = 2;
	ba->virtBase = p->virt_base;
	ba->physBase = p->phys_base;
	ba->memSize = p->mem_size;
	ba->topOfKernelData = top;
	ba->Video.v_baseAddr = FB_BASE;
	ba->Video.v_display = 1;
	ba->Video.v_rowBytes = FB_STRIDE;
	ba->Video.v_width = FB_W;
	ba->Video.v_height = FB_H;
	ba->Video.v_depth = 32;
	ba->deviceTreeP = adt_pa - pa_of_va_base;	/* xnu reads it with the MMU on: a VA */
	ba->deviceTreeLength = (uint32_t)p->adt_size;
	for (int i = 0; i < BOOT_LINE_LENGTH; i++)
		ba->CommandLine[i] = p->cmdline[i];
	ba->memSizeActual = p->mem_size;
	con_hex2("BOOTARG", "pa=", ba_pa, "top=", top);

	uintptr_t entry_pa = p->entry_va + pa_of_va_base;
	con_hex("JUMP", entry_pa);
	con_str("XNU", "jumping (fbcon clears screen)");

	/* Everything was written with the MMU and caches off; drop any stale I-cache lines too. */
	__asm__ volatile("dsb sy; ic iallu; dsb sy; isb" ::: "memory");
	((xnu_entry_t)entry_pa)(ba_pa);
	return "xnu returned";
}
