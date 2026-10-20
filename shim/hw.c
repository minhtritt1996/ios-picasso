/*
 * Board discovery from the live device tree.
 *
 * The bootloader hands us the tree it booted with; on picasso the static
 * tree in the boot image still has `reg = <0 0 0 0>` for /memory, because
 * the bootloader fills the RAM banks in at boot. Reading the tree we were
 * given is therefore the only reliable way to learn the memory layout.
 */
#include "fdt.h"
#include "hw.h"
#include <stddef.h>

struct hw hw;

/* The tree we were given, for the display pipe read later. */
static struct fdt hw_tree;
static int hw_tree_ok;

static int streq(const char *a, const char *b)
{
	while (*a && *a == *b)
		a++, b++;
	return *a == *b;
}

static int strprefix(const char *s, const char *p)
{
	while (*p) {
		if (*s != *p)
			return 0;
		s++, p++;
	}
	return 1;
}

static int strcontains(const char *s, const char *p)
{
	for (; *s; s++)
		if (strprefix(s, p))
			return 1;
	return 0;
}

static void add_rsv(uint64_t base, uint64_t size)
{
	if (!size)
		return;
	if (hw.nrsv == HW_MAX_RSV)
		return;
	hw.rsv[hw.nrsv].base = base;
	hw.rsv[hw.nrsv].size = size;
	hw.nrsv++;
}

static void copy_name(char *dst, unsigned cap, const char *src, char stop1, char stop2)
{
	unsigned i = 0;

	while (src[i] && src[i] != stop1 && src[i] != stop2 && i + 1 < cap) {
		dst[i] = src[i];
		i++;
	}
	dst[i] = 0;
}

/* The DSI panel the bootloader picked, from /chosen/bootargs. */
static void find_panel_name(void)
{
	const char *key = "msm_drm.dsi_display0=";

	hw.panel[0] = 0;
	if (!hw.bootargs)
		return;
	for (const char *s = hw.bootargs; *s; s++)
		if (strprefix(s, key)) {
			copy_name(hw.panel, sizeof(hw.panel), s + sizeof(key) - 1, ':', ' ');
			return;
		}
}

/*
 * The splash buffer's real address and stride, from the display pipe.
 *
 * The tree gives the MDSS window and the offset of every SSPP; the pipe that
 * scans the bootloader's splash has its source address inside the splash
 * region. Reading the pipe is the only way to be sure about the stride: the
 * panel is 1080 pixels wide, but the pipe may fetch it with a different
 * pitch, and a wrong pitch shows a skewed picture.
 *
 * This reads hardware, so it is called only after the console is up: a
 * register block that is clock-gated aborts the read, and an abort before
 * the console exists leaves the screen showing the bootloader's logo.
 */
void hw_read_display_pipe(const struct fdt *unused)
{
	const struct fdt *f = &hw_tree;
	struct fdt_node n;
	uint32_t ac, sc;
	uint64_t mdss, size;

	(void)unused;
	if (!hw_tree_ok)
		return;
	if (!fdt_find_compatible(f, "qcom,sde-kms", &n))
		return;
	fdt_reg_cells_of(f, &n, &ac, &sc);
	if (!fdt_reg(f, n.props, ac, sc, 0, &mdss, &size))
		return;

	uintptr_t val;
	uint32_t len;
	if (!fdt_prop(f, n.props, "qcom,sde-sspp-off", &val, &len))
		return;
#ifdef HOST_NO_MMIO
	(void)mdss;	/* host test: there are no display registers here */
#else
	for (uint32_t i = 0; i + 4 <= len; i += 4) {
		uint32_t off = __builtin_bswap32(*(volatile uint32_t *)(val + i));
		uintptr_t sspp = (uintptr_t)(mdss + off);
		uint32_t src = *(volatile uint32_t *)(sspp + 0x14);	/* SRC0_ADDR */
		uint32_t stride = *(volatile uint32_t *)(sspp + 0x24);	/* SRC_YSTRIDE0 */
		uint32_t fmt = *(volatile uint32_t *)(sspp + 0x30);	/* SRC_FORMAT */
		if (src == 0)
			continue;
		if (hw.fb_base && (src < hw.fb_base || src >= hw.fb_base + hw.fb_size))
			continue;	/* not the splash pipe */
		hw.fb_src_addr = src;
		hw.fb_pipe = off;
		hw.fb_format = fmt;
		if (stride)
			hw.fb_stride = stride;
		hw.fb_bpp = 4;		/* a8r8g8b8 / xrgb8888 */
		if (!hw.fb_base) {
			hw.fb_base = src;
			hw.fb_src = "display pipe";
		}
	}
#endif
}

static void find_panel_size(const struct fdt *f)
{
	struct fdt_iter it;
	struct fdt_node n;
	int depth = -1;

	hw.fb_w = 0;
	hw.fb_h = 0;
	if (!hw.panel[0]) {
		/* No name from the bootargs: take the first DSI timing node. */
		fdt_iter_start(f, &it);
		while (fdt_iter_next(f, &it, &n)) {
			uint32_t w, h;
			if (fdt_prop_u32(f, n.props, "qcom,mdss-dsi-panel-width", &w) &&
			    fdt_prop_u32(f, n.props, "qcom,mdss-dsi-panel-height", &h) &&
			    w >= 320 && h >= 320 && w <= 4096 && h <= 4096) {
				hw.fb_w = w;
				hw.fb_h = h;
				return;
			}
		}
		return;
	}
	fdt_iter_start(f, &it);
	while (fdt_iter_next(f, &it, &n)) {
		if (depth >= 0 && it.depth <= depth)
			break;		/* left the panel's subtree */
		if (depth < 0) {
			if (!streq(n.name, hw.panel))
				continue;
			depth = it.depth;
		}
		uint32_t w, h;
		if (fdt_prop_u32(f, n.props, "qcom,mdss-dsi-panel-width", &w) &&
		    fdt_prop_u32(f, n.props, "qcom,mdss-dsi-panel-height", &h) &&
		    w >= 320 && h >= 320 && w <= 4096 && h <= 4096) {
			hw.fb_w = w;
			hw.fb_h = h;
			return;
		}
	}
}

/* The splash region: what the panel is scanning out right now. */
static void find_framebuffer(const struct fdt *f)
{
	struct fdt_node n;
	uint32_t ac, sc;
	int pass;

	hw.fb_base = 0;
	hw.fb_size = 0;
	hw.fb_src = NULL;

	for (pass = 0; pass < 2 && !hw.fb_base; pass++) {
		struct fdt_iter it;
		fdt_iter_start(f, &it);
		while (fdt_iter_next(f, &it, &n)) {
			const char *nm = n.name;
			int hit = pass == 0 ?
			    (strprefix(nm, "splash_region") || strprefix(nm, "cont_splash")) :
			    (strcontains(nm, "splash") || strcontains(nm, "framebuffer"));
			if (!hit)
				continue;
			fdt_reg_cells_of(f, &n, &ac, &sc);
			uint64_t base, size;
			if (fdt_reg(f, n.props, ac, sc, 0, &base, &size)) {
				hw.fb_base = base;
				hw.fb_size = size;
				hw.fb_src = nm;
				return;
			}
		}
	}
	if (!hw.fb_base && fdt_find_compatible(f, "simple-framebuffer", &n)) {
		uint32_t a2, s2;
		fdt_reg_cells_of(f, &n, &a2, &s2);
		uint64_t base, size;
		if (fdt_reg(f, n.props, a2, s2, 0, &base, &size)) {
			hw.fb_base = base;
			hw.fb_size = size;
			hw.fb_src = "simple-framebuffer";
		}
	}
}

static void find_memory(const struct fdt *f)
{
	struct fdt_iter it;
	struct fdt_node n;
	uint32_t ac, sc;
	int rm_depth = -1;

	fdt_iter_start(f, &it);
	while (fdt_iter_next(f, &it, &n)) {
		if (rm_depth >= 0 && it.depth <= rm_depth)
			rm_depth = -1;		/* left /reserved-memory */
		if (streq(n.name, "memory")) {
			const char *dt = fdt_prop_str(f, n.props, "device_type");
			if (dt && streq(dt, "memory")) {
				fdt_reg_cells_of(f, &n, &ac, &sc);
				for (unsigned i = 0; i < 8; i++) {
					uint64_t base, size;
					if (!fdt_reg(f, n.props, ac, sc, i, &base, &size))
						break;
					if (!size)
						continue;
					if (hw.nmem == HW_MAX_MEM)
						break;
					hw.mem[hw.nmem].base = base;
					hw.mem[hw.nmem].size = size;
					hw.nmem++;
				}
			}
			continue;
		}
		if (streq(n.name, "reserved-memory")) {
			rm_depth = it.depth;
			continue;
		}
		if (rm_depth >= 0 && it.depth == rm_depth + 1) {
			/*
			 * Every static carve-out counts, no-map or not: under
			 * Gunyah some of them belong to other VMs, and xnu has no
			 * idea what Linux would have shared.
			 */
			fdt_reg_cells_of(f, &n, &ac, &sc);
			uint64_t base, size;
			if (fdt_reg(f, n.props, ac, sc, 0, &base, &size))
				add_rsv(base, size);
		}
	}
}

/* Architected timer PPIs: interrupts = <type num flags> x4. */
static void find_timer(const struct fdt *f)
{
	struct fdt_node n;

	hw.vtimer_intid = 19;	/* PPI 3: the usual Qualcomm layout */
	hw.ptimer_intid = 18;
	if (!fdt_find_compatible(f, "arm,armv8-timer", &n))
		return;
	uintptr_t val;
	uint32_t len;
	if (!fdt_prop(f, n.props, "interrupts", &val, &len) || len < 12)
		return;
	uint32_t ptype = __builtin_bswap32(*(volatile uint32_t *)(val + 4));
	uint32_t pnum = __builtin_bswap32(*(volatile uint32_t *)(val + 8));
	uint32_t vnum = __builtin_bswap32(*(volatile uint32_t *)(val + 20));
	if (ptype == 1 && pnum < 16)
		hw.ptimer_intid = 16 + pnum;
	if (vnum < 16)
		hw.vtimer_intid = 16 + vnum;
}

static void find_gic(const struct fdt *f)
{
	struct fdt_node n;
	uint32_t ac, sc;

	if (!fdt_find_compatible(f, "arm,gic-v3", &n) &&
	    !fdt_find_compatible(f, "arm,gic-v4", &n))
		return;
	fdt_reg_cells_of(f, &n, &ac, &sc);
	uint64_t base, size;
	if (fdt_reg(f, n.props, ac, sc, 0, &base, &size)) {
		hw.gicd = base;
		hw.gicd_size = size;
	}
	if (fdt_reg(f, n.props, ac, sc, 1, &base, &size)) {
		hw.gicr = base;
		hw.gicr_size = size;
	}
	/* redistributor-stride is <0x0 0x20000>: take the last cell */
	uintptr_t val;
	uint32_t len;
	if (fdt_prop(f, n.props, "redistributor-stride", &val, &len) && len >= 4) {
		uint32_t last = __builtin_bswap32(*(volatile uint32_t *)(val + len - 4));
		if (last)
			hw.gicr_stride = last;
	}
}

static void find_ufs(const struct fdt *f)
{
	struct fdt_node n;
	uint32_t ac, sc;

	if (!fdt_find_compatible(f, "qcom,ufshc", &n))
		return;
	fdt_reg_cells_of(f, &n, &ac, &sc);
	uint64_t base, size;
	if (fdt_reg(f, n.props, ac, sc, 0, &base, &size)) {
		hw.ufs = base;
		hw.ufs_size = size;
	}
}

static void find_usb(const struct fdt *f)
{
	struct fdt_node n;
	uint32_t ac, sc;

	if (!fdt_find_compatible(f, "snps,dwc3", &n))
		return;
	fdt_reg_cells_of(f, &n, &ac, &sc);
	uint64_t base, size;
	if (fdt_reg(f, n.props, ac, sc, 0, &base, &size)) {
		hw.dwc3 = base;
		hw.dwc3_size = size;
	}
}

static void find_uart(const struct fdt *f)
{
	struct fdt_node n;
	uint32_t ac, sc;

	if (!fdt_find_compatible(f, "qcom,msm-geni-console", &n))
		return;
	fdt_reg_cells_of(f, &n, &ac, &sc);
	uint64_t base, size;
	if (fdt_reg(f, n.props, ac, sc, 0, &base, &size)) {
		hw.uart = base;
		hw.uart_size = size;
	}
}

static void find_cpus(const struct fdt *f)
{
	struct fdt_iter it;
	struct fdt_node n;
	uint32_t ac, sc;

	fdt_iter_start(f, &it);
	while (fdt_iter_next(f, &it, &n)) {
		const char *dt = fdt_prop_str(f, n.props, "device_type");
		if (!dt || !streq(dt, "cpu"))
			continue;
		if (hw.ncpus == HW_MAX_CPUS)
			break;
		fdt_reg_cells_of(f, &n, &ac, &sc);
		uint64_t base, size;
		if (fdt_reg(f, n.props, ac, sc, 0, &base, &size))
			hw.mpidr[hw.ncpus++] = base;
	}
}

const char *hw_discover(uintptr_t dtb)
{
	struct fdt f;
	struct fdt_node n;

	hw.dtb = dtb;
	if (!fdt_init(dtb, &f))
		return "no FDT from ABL";
	hw_tree = f;
	hw_tree_ok = 1;
	if (fdt_find_path(&f, "/chosen", &n))
		hw.bootargs = fdt_prop_str(&f, n.props, "bootargs");
	find_panel_name();
	find_memory(&f);
	find_framebuffer(&f);
	find_panel_size(&f);
	find_gic(&f);
	find_timer(&f);
	find_ufs(&f);
	find_usb(&f);
	find_uart(&f);
	find_cpus(&f);
	if (!hw.nmem)
		return "no /memory banks in the FDT";
	if (!hw.gicd || !hw.gicr)
		return "no GIC in the FDT";
	return 0;
}

static int overlaps(uint64_t a, uint64_t as, uint64_t b, uint64_t bs)
{
	return a < b + bs && b < a + as;
}

int hw_pick_ram(const struct range *excl, int nexcl, uint64_t align, uint64_t max, struct range *out)
{
	out->base = 0;
	out->size = 0;
	for (uint32_t m = 0; m < hw.nmem; m++) {
		uint64_t ms = hw.mem[m].base, me = ms + hw.mem[m].size;
		/* candidate starts: the bank start and the end of every blocker */
		for (int c = -1; c < (int)(hw.nrsv + nexcl); c++) {
			uint64_t p = ms;
			if (c >= 0) {
				const struct range *r = c < (int)hw.nrsv ? &hw.rsv[c] : &excl[c - hw.nrsv];
				p = r->base + r->size;
				if (p <= ms || p >= me)
					continue;
			}
			uint64_t e = me;
			int blocked = 0;
			for (int k = 0; k < (int)(hw.nrsv + nexcl); k++) {
				const struct range *r = k < (int)hw.nrsv ? &hw.rsv[k] : &excl[k - hw.nrsv];
				if (!r->size)
					continue;
				if (r->base <= p && p < r->base + r->size) {
					blocked = 1;
					break;
				}
				if (r->base > p && r->base < e)
					e = r->base;
			}
			if (blocked)
				continue;
			uint64_t s = (p + align - 1) & ~(align - 1);
			e &= ~(align - 1);
			if (e <= s)
				continue;
			uint64_t size = e - s > max ? max : e - s;
			if (size > out->size) {
				out->base = s;
				out->size = size;
			}
		}
	}
	/* paranoia: never return anything that touches a blocker */
	for (uint32_t k = 0; k < hw.nrsv; k++)
		if (overlaps(out->base, out->size, hw.rsv[k].base, hw.rsv[k].size))
			return 0;
	for (int k = 0; k < nexcl; k++)
		if (overlaps(out->base, out->size, excl[k].base, excl[k].size))
			return 0;
	return out->size != 0;
}
