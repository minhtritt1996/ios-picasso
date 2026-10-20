/*
 * Host test for the board discovery: read a device tree file and print what
 * the shim would find on the phone. No phone needed.
 *
 *   cc -o /tmp/host_probe host_probe.c hw.c fdt.c adt.c board.c -I.
 *   /tmp/host_probe rec-picasso.dtb [adt-file]
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "adt.h"
#include "board.h"
#include "hw.h"

int main(int argc, char **argv)
{
	if (argc < 2) {
		fprintf(stderr, "usage: %s DTB [ADT]\n", argv[0]);
		return 2;
	}
	FILE *f = fopen(argv[1], "rb");
	if (!f) {
		perror(argv[1]);
		return 1;
	}
	fseek(f, 0, SEEK_END);
	long n = ftell(f);
	fseek(f, 0, SEEK_SET);
	uint8_t *buf = malloc((size_t)n);
	if (!buf || fread(buf, 1, (size_t)n, f) != (size_t)n) {
		fprintf(stderr, "short read\n");
		return 1;
	}
	fclose(f);

	const char *why = hw_discover((uintptr_t)buf);
	printf("=== %s (%ld bytes)\n", argv[1], n);
	printf("discovery    : %s\n", why ? why : "ok");

	printf("framebuffer  : base 0x%llx size 0x%llx  (%s)\n",
	    (unsigned long long)hw.fb_base, (unsigned long long)hw.fb_size,
	    hw.fb_src ? hw.fb_src : "-");
	printf("panel        : %ux%u stride %u\n", hw.fb_w, hw.fb_h, hw.fb_stride);
	printf("gicd/gicr    : 0x%llx / 0x%llx stride 0x%llx\n",
	    (unsigned long long)hw.gicd, (unsigned long long)hw.gicr,
	    (unsigned long long)hw.gicr_stride);
	printf("timer        : vtimer %u ptimer %u\n", hw.vtimer_intid, hw.ptimer_intid);
	printf("ufs          : 0x%llx+0x%llx\n", (unsigned long long)hw.ufs, (unsigned long long)hw.ufs_size);
	printf("dwc3         : 0x%llx+0x%llx\n", (unsigned long long)hw.dwc3, (unsigned long long)hw.dwc3_size);
	printf("uart console : 0x%llx+0x%llx\n", (unsigned long long)hw.uart, (unsigned long long)hw.uart_size);
	printf("panel node   : %s\n", hw.panel[0] ? hw.panel : "-");
	printf("bootargs     : %s\n", hw.bootargs ? hw.bootargs : "-");

	printf("cpus         : %u\n", hw.ncpus);
	for (uint32_t i = 0; i < hw.ncpus; i++)
		printf("  cpu%u mpidr 0x%llx\n", i, (unsigned long long)hw.mpidr[i]);

	printf("memory banks : %u\n", hw.nmem);
	for (uint32_t i = 0; i < hw.nmem; i++)
		printf("  0x%llx + %llu MiB\n", (unsigned long long)hw.mem[i].base,
		    (unsigned long long)(hw.mem[i].size >> 20));
	printf("carve-outs   : %u\n", hw.nrsv);
	for (uint32_t i = 0; i < hw.nrsv && i < 40; i++)
		printf("  0x%llx + %llu MiB\n", (unsigned long long)hw.rsv[i].base,
		    (unsigned long long)(hw.rsv[i].size >> 20));

	uint64_t base = 0, size = 0;
	if (board_pick_ram(&base, &size, 0, 0)) {
		printf("kernel RAM   : base 0x%llx size %llu MiB (32 MB aligned)\n",
		    (unsigned long long)base, (unsigned long long)(size >> 20));
	} else {
		printf("kernel RAM   : none found (the device tree has no usable banks)\n");
	}

	/* Optionally patch an ADT and read the values back. */
	if (argc > 2) {
		FILE *a = fopen(argv[2], "rb");
		if (!a) {
			perror(argv[2]);
			return 1;
		}
		fseek(a, 0, SEEK_END);
		long m = ftell(a);
		fseek(a, 0, SEEK_SET);
		uint8_t *adt = malloc((size_t)m);
		if (!adt || fread(adt, 1, (size_t)m, a) != (size_t)m) {
			fprintf(stderr, "short read\n");
			return 1;
		}
		fclose(a);
		int bad = board_patch_adt((uintptr_t)adt, base, size);
		printf("adt          : %d edits failed\n", bad);
		uint32_t v32 = 0;
		uint64_t v64[4] = { 0 };
		if (adt_get_u64((uintptr_t)adt, "chosen", "dram-base", &v64[0]))
			printf("  chosen/dram-base  = 0x%llx\n", (unsigned long long)v64[0]);
		if (adt_get_u64((uintptr_t)adt, "chosen", "dram-size", &v64[1]))
			printf("  chosen/dram-size  = 0x%llx\n", (unsigned long long)v64[1]);
		if (adt_get_u32((uintptr_t)adt, "arm-io/gic", "virtual-timer-intid", &v32))
			printf("  arm-io/gic/vtimer = %u\n", v32);
		if (adt_get_u64s((uintptr_t)adt, "arm-io/gic", "reg", v64, 4) == 4)
			printf("  arm-io/gic/reg    = 0x%llx 0x%llx 0x%llx 0x%llx\n",
			    (unsigned long long)v64[0], (unsigned long long)v64[1],
			    (unsigned long long)v64[2], (unsigned long long)v64[3]);
		if (adt_get_u64s((uintptr_t)adt, "sweet-fbcon", "reg", v64, 2) == 2)
			printf("  sweet-fbcon/reg   = 0x%llx 0x%llx\n",
			    (unsigned long long)v64[0], (unsigned long long)v64[1]);
		if (adt_get_u32((uintptr_t)adt, "sweet-fbcon", "width", &v32))
			printf("  sweet-fbcon/width = %u\n", v32);
		if (adt_get_u32((uintptr_t)adt, "sweet-ufs", "reg", &v32))
			printf("  sweet-ufs/reg[0]  = 0x%x\n", v32);
	}
	return 0;
}
