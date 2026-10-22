/*
 * Milestone 2: one boot answers the questions that decide the XNU port.
 *   - CPU feature registers (16K granule, GIC sysreg interface)
 *   - PSCI/SMCCC version and which PSCI calls the Qualcomm TZ offers
 *   - SMP: PSCI CPU_ON for the 7 secondaries, each reports its MPIDR
 *   - GICv3 Group 1 IRQ from the virtual timer (INTID 19 on Qualcomm)
 * Any unexpected exception paints a blue panic screen.
 */
#include <stdint.h>
#include "board.h"
#include "console.h"
#include "fdt.h"
#include "hw.h"
#include "loader.h"
#include "mmu.h"
#include "ufs.h"

#define IMEM_RESTART_REASON	0x146aa65cUL	/* qcom,msm-imem restart_reason@65c */
#define RESTART_BOOTLOADER	0x77665500U
#define PSHOLD			0x0c264000UL

#define GICD_BASE		0x17a00000UL
#define GICR_BASE		0x17a60000UL
#define GICR_SIZE		0x100000UL
#define GICR_PE_SIZE		0x20000UL
#define GICR_TYPER		0x08
#define GICR_WAKER		0x14
#define GICR_SGI		0x10000
#define GICR_IGROUPR0		(GICR_SGI + 0x080)
#define GICR_ISENABLER0		(GICR_SGI + 0x100)
#define GICR_ICENABLER0		(GICR_SGI + 0x180)
#define GICR_IPRIORITYR		(GICR_SGI + 0x400)
#define GICR_ISPENDR0		(GICR_SGI + 0x200)
#define GICR_ISACTIVER0		(GICR_SGI + 0x300)
#define GICR_ICFGR1		(GICR_SGI + 0xc04)
#define VTIMER_INTID		19	/* DT: <GIC_PPI 3 ...>, not the usual 27 */
#define PTIMER_INTID		18	/* DT: <GIC_PPI 2 ...> */
#define SGI_INTID		1

#define PSCI_VERSION		0x84000000UL
#define PSCI_CPU_SUSPEND	0xc4000001UL
#define PSCI_CPU_OFF		0x84000002UL
#define PSCI_CPU_ON		0xc4000003UL
#define PSCI_MIGRATE_INFO_TYPE	0x84000006UL
#define PSCI_SYSTEM_RESET	0x84000009UL
#define PSCI_FEATURES		0x8400000aUL
#define PSCI_SYSTEM_SUSPEND	0xc400000eUL
#define PSCI_SYSTEM_RESET2	0xc4000012UL
#define SMCCC_VERSION		0x80000000UL

#define NR_CPUS			8

/* Where the bootloader puts the kernel part of the boot image, and where the
 * payload and the device tree sit inside it. */
#ifndef KERNEL_ADDR
#define KERNEL_ADDR 0x8000
#endif
#ifndef PAYLOAD_OFF
#define PAYLOAD_OFF 0
#endif

#define rsr(r) ({ uint64_t _v; __asm__ volatile("mrs %0, " r : "=r"(_v)); _v; })
#define wsr(r, v) __asm__ volatile("msr " r ", %0" : : "r"((uint64_t)(v)))
#define isb() __asm__ volatile("isb" ::: "memory")

static inline uint32_t rd32(uintptr_t a) { return *(volatile uint32_t *)a; }
static inline uint64_t rd64(uintptr_t a) { return *(volatile uint64_t *)a; }
static inline void wr32(uintptr_t a, uint32_t v) { *(volatile uint32_t *)a = v; }
static inline void wr8(uintptr_t a, uint8_t v) { *(volatile uint8_t *)a = v; }

extern char secondary_entry[];

static const char *stage;	/* set at runtime: no absolute pointers in data */
static volatile uint64_t sec_mpidr[NR_CPUS];
static volatile uint64_t sec_probe[4];	/* CPU1 redistributor write test */
static volatile uint32_t irq_count, irq_other, irq_last;
static volatile int irq_src;

/* ICC_SGI1R_EL1 value targeting the calling core only. */
static uint64_t sgi_self(unsigned id)
{
	uint64_t m = rsr("mpidr_el1");
	return ((m >> 16) & 0xff) << 32 |	/* Aff2 */
	       ((m >> 8) & 0xff) << 16 |	/* Aff1 */
	       (uint64_t)id << 24 |
	       (1U << (m & 0xf));		/* target list: Aff0 */
}

static uint64_t smc(uint64_t fn, uint64_t a1, uint64_t a2, uint64_t a3)
{
	register uint64_t x0 __asm__("x0") = fn;
	register uint64_t x1 __asm__("x1") = a1;
	register uint64_t x2 __asm__("x2") = a2;
	register uint64_t x3 __asm__("x3") = a3;
	__asm__ volatile("smc #0"
			 : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3)
			 :
			 : "x4", "x5", "x6", "x7", "x8", "x9", "x10", "x11",
			   "x12", "x13", "x14", "x15", "x16", "x17", "memory");
	return x0;
}

static uint64_t now(void)
{
	isb();
	return rsr("cntpct_el0");
}

static void delay_ms(unsigned ms)
{
	uint64_t end = now() + rsr("cntfrq_el0") / 1000 * ms;
	while (now() < end)
		;
}

static void __attribute__((noreturn)) reset(unsigned after_s)
{
	con_skip();
	con_str("RESET", after_s > 20 ? "in 40s" : "in 20s");
	delay_ms(after_s * 1000);
	wr32(IMEM_RESTART_REASON, RESTART_BOOTLOADER);
	__asm__ volatile("dsb sy" ::: "memory");
	smc(PSCI_SYSTEM_RESET, 0, 0, 0);
	wr32(PSHOLD, 0);
	for (;;)
		__asm__ volatile("wfe");
}

/* ---- exceptions ---- */

static void irq_handler(void)
{
	uint64_t iar = rsr("icc_iar1_el1");
	uint32_t id = iar & 0xffffff;

	if (id >= 1020 && id <= 1023)
		return;	/* spurious */
	irq_last = id;
	if (id == (uint32_t)irq_src)
		irq_count++;
	else
		irq_other++;
	if (id == VTIMER_INTID)
		wsr("cntv_tval_el0", rsr("cntfrq_el0") / 100);	/* 10 ms */
	else if (id == PTIMER_INTID)
		wsr("cntp_tval_el0", rsr("cntfrq_el0") / 100);
	wsr("icc_eoir1_el1", iar);
	isb();
}

void exc_handler(uint64_t slot, uint64_t *regs)
{
	if (slot == 5) {	/* IRQ from current EL, SPx */
		irq_handler();
		return;
	}
	con_clear(BLUE);
	switch (slot & 3) {
	case 0: con_str("PANIC", "SYNC"); break;
	case 1: con_str("PANIC", "IRQ"); break;
	case 2: con_str("PANIC", "FIQ"); break;
	default: con_str("PANIC", "SERROR"); break;
	}
	con_hex2("SLOT", "n=", slot, "cpu=", rsr("mpidr_el1"));
	con_str("STAGE", stage);
	con_hex("ESR", rsr("esr_el1"));
	con_hex("ELR", rsr("elr_el1"));
	con_hex("FAR", rsr("far_el1"));
	con_hex("SPSR", rsr("spsr_el1"));
	con_hex("X0", regs[0]);
	con_hex("X1", regs[1]);
	con_hex("X30", regs[29]);	/* stashed lr is just above the frame */
	reset(40);
}

/* ---- SMP ---- */

static uintptr_t find_gicr(void);

void secondary_main(uint64_t idx)
{
	sec_mpidr[idx] = rsr("mpidr_el1") | (1ULL << 63);
	if (idx == 1) {
		uintptr_t r = find_gicr();
		if (r) {
			sec_probe[0] = rd32(r + GICR_IGROUPR0);
			wr32(r + GICR_IGROUPR0, 0xffffffff);
			sec_probe[1] = rd32(r + GICR_IGROUPR0);
			wr8(r + GICR_IPRIORITYR + 19, 0xa0);
			sec_probe[2] = rd32(r + GICR_IPRIORITYR + 16);
		}
		sec_probe[3] = r ? 1 : 0xdead;
	}
	__asm__ volatile("dsb sy" ::: "memory");
	smc(PSCI_CPU_OFF, 0, 0, 0);
}

static void __attribute__((unused)) test_smp(void)
{
	stage = "smp";
	uint64_t self = rsr("mpidr_el1") & 0xffffff;
	int64_t ret[NR_CPUS];

	for (int i = 1; i < NR_CPUS; i++)
		ret[i] = smc(PSCI_CPU_ON, (uint64_t)i << 8, (uintptr_t)secondary_entry, i);
	delay_ms(500);
	for (int i = 1; i < NR_CPUS; i++) {
		char label[] = "CPU0";
		label[3] = '0' + i;
		if (((uint64_t)i << 8) == self)
			con_str(label, "is boot cpu");
		else
			con_hex2(label, "ret=", (uint32_t)ret[i], "mpidr=",
				 (uint32_t)sec_mpidr[i] | (sec_mpidr[i] ? 0 : 0xdead0000));
	}
}

/* ---- GIC + timer ---- */

static uintptr_t find_gicr(void)
{
	uint64_t aff = rsr("mpidr_el1");
	uint32_t want = (aff & 0xffffff) | ((aff >> 8) & 0xff000000);

	for (uintptr_t off = 0; off < GICR_SIZE; off += GICR_PE_SIZE) {
		uint64_t typer = rd64(GICR_BASE + off + GICR_TYPER);
		if ((uint32_t)(typer >> 32) == want)
			return GICR_BASE + off;
		if (typer & (1 << 4))	/* Last */
			break;
	}
	return 0;
}

static void gicd_wait_rwp(void)
{
	for (int i = 0; i < 1000000 && (rd32(GICD_BASE) & (1U << 31)); i++)
		;
}

/* Run one interrupt source for ~300 ms and report what the GIC saw. */
static void irq_window(const char *label, uintptr_t r, int src)
{
	uint64_t f = rsr("cntfrq_el0");
	uint32_t pend = 0, act = 0, isr = 0, hppi = 0x3ff, ist = 0;

	irq_count = irq_other = 0;
	irq_last = 0x3ff;
	irq_src = src;
	if (src == VTIMER_INTID) {
		wsr("cntv_tval_el0", f / 100);
		wsr("cntv_ctl_el0", 1);
	} else if (src == PTIMER_INTID) {
		wsr("cntp_tval_el0", f / 100);
		wsr("cntp_ctl_el0", 1);
	}
	isb();
	/* sample state with IRQs still masked, after the first period */
	uint64_t t = now() + f / 50;
	while (now() < t)
		;
	if (src == SGI_INTID)
		wsr("icc_sgi1r_el1", sgi_self(SGI_INTID));
	isb();
	ist = src == PTIMER_INTID ? rsr("cntp_ctl_el0") : rsr("cntv_ctl_el0");
	pend = rd32(r + GICR_ISPENDR0);
	act = rd32(r + GICR_ISACTIVER0);
	isr = rsr("isr_el1");
	hppi = rsr("icc_hppir1_el1");

	__asm__ volatile("msr daifclr, #2" ::: "memory");
	t = now() + f * 3 / 10;
	/* SGI: one already sent above, send 9 more 10 ms apart -> expect 10 */
	uint64_t next = now() + f / 100;
	for (int sent = 1; now() < t;)
		if (src == SGI_INTID && sent < 10 && now() >= next) {
			wsr("icc_sgi1r_el1", sgi_self(SGI_INTID));
			isb();
			sent++;
			next += f / 100;
		}
	__asm__ volatile("msr daifset, #2" ::: "memory");
	wsr("cntv_ctl_el0", 0);
	wsr("cntp_ctl_el0", 0);
	isb();

	con_hex2(label, "irqs=", irq_count, "other=", irq_other);
	con_hex2(" pre", "pend=", pend, "act=", act);
	con_hex2(" pre", "isr=", isr, "hppi=", hppi);
	con_hex2(" pre", "tctl=", ist, "last=", irq_last);
}

static void test_gic(void)
{
	stage = "gic sre";
	uint64_t sre = rsr("icc_sre_el1");
	if (!(sre & 1)) {
		wsr("icc_sre_el1", sre | 1);
		isb();
	}
	if (!(rsr("icc_sre_el1") & 1)) {
		con_str("GIC", "no sysreg iface, skip");
		return;
	}

	stage = "gic dist";
	uint32_t dctlr = rd32(GICD_BASE);
	/* Non-secure view (DS=0): bit0 EnableGrp1, bit1 EnableGrp1A, bit4 ARE_NS; Linux writes 0x13 */
	wr32(GICD_BASE, dctlr | 0x13);
	gicd_wait_rwp();
	con_hex2("GICD", "was=", dctlr, "now=", rd32(GICD_BASE));

	stage = "gic redist";
	uintptr_t r = find_gicr();
	if (!r) {
		con_str("GICR", "not found");
		return;
	}
	uint32_t waker = rd32(r + GICR_WAKER);
	if (waker & 2) {
		wr32(r + GICR_WAKER, waker & ~2U);
		for (int i = 0; i < 100000 && (rd32(r + GICR_WAKER) & 4); i++)
			;
	}
	con_hex2("GICR", "base=", r, "waker=", rd32(r + GICR_WAKER));

	stage = "gic config";
	uint32_t grp = rd32(r + GICR_IGROUPR0);
	wr32(r + GICR_IGROUPR0, 0xffffffff);	/* NS view: secure bits are WI */
	uint32_t en_before = rd32(r + GICR_ISENABLER0);
	uint32_t pri_before = rd32(r + GICR_IPRIORITYR + 16);	/* INTIDs 16..19 */
	for (int id = 0; id < 32; id++)
		wr8(r + GICR_IPRIORITYR + id, 0xa0);
	wr32(r + GICR_ISENABLER0, (1U << VTIMER_INTID) | (1U << PTIMER_INTID) | (1U << SGI_INTID));
	con_hex2("ENABLE", "was=", en_before, "now=", rd32(r + GICR_ISENABLER0));
	(void)grp;
	(void)pri_before;

	uint64_t ctlr = rsr("icc_ctlr_el1");
	wsr("icc_ctlr_el1", ctlr & ~2ULL);	/* EOImode 0: EOIR also deactivates */
	wsr("icc_pmr_el1", 0xf0);
	wsr("icc_bpr1_el1", 0);
	wsr("icc_igrpen1_el1", 1);
	isb();
	con_hex2("ICC", "ctlr=", ctlr, "grpen1=", rsr("icc_igrpen1_el1"));

	stage = "gic vtimer";
	irq_window("VTIMER", r, VTIMER_INTID);
	stage = "gic sgi";
	irq_window("SGI1", r, SGI_INTID);
	wr32(r + GICR_ICENABLER0, 0xffffffff);
	isb();
}

/* ---- main ---- */

static void __attribute__((unused)) psci_line(const char *label, uint64_t fn)
{
	con_hex2(label, "feat=", smc(PSCI_FEATURES, fn, 0, 0), 0, 0);
}

static uint64_t scratch[8192];	/* 64KB, lives in our (later cacheable) image */

/* Does /chosen/bootargs ask for the board report (and nothing else)? */
static int bootargs_have(const char *key)
{
	const char *b = hw.bootargs;

	if (!b)
		return 0;
	for (const char *s = b; *s; s++) {
		const char *k = key;
		while (*k && *s == *k)
			s++, k++;
		if (!*k)
			return 1;
	}
	return 0;
}

/* The registers the bootloader entered us with (start.S keeps them). */
uint64_t boot_regs[4];

static int ascii_ok(const char *m, const char *want)
{
	for (int i = 0; i < 8; i++)
		if (m[i] != want[i])
			return 0;
	return 1;
}

/* Where the device tree was found, and what the discovery made of it. */
static uintptr_t g_tree;
static const char *g_why;

/* The board facts, on the screen: this is the only log channel a board
 * without a UART has until the USB gadget works. */
static void probe_report(void)
{
	con_str("BOARD", "discovered from the device tree");
	con_skip();

	con_hex("DTB at", g_tree);
	if (g_why)
		con_str("  state", g_why);
	else
		con_str("  state", "read");
	con_skip();

	con_hex("REG x0", boot_regs[0]);
	con_hex("REG x1", boot_regs[1]);
	con_hex("REG x2", boot_regs[2]);
	con_hex("REG x3", boot_regs[3]);
	con_skip();

	/* What is at the addresses a boot image puts the tree at? */
#ifndef NO_DTB
	for (unsigned a = 0; a < 4; a++) {
		static const uintptr_t known[] = { 0x1728000, 0x1f00000, 0x1000000, 0x2000000 };
		con_hex("AT", known[a]);
		con_hex("  word", *(volatile uint32_t *)known[a]);
	}
	con_skip();
#endif

	con_str("FB", hw.fb_src ? hw.fb_src : "not found");
	con_hex("  base", hw.fb_base);
	con_dec("  size", hw.fb_size);
	con_dec("  width", hw.fb_w);
	con_dec("  height", hw.fb_h);
	con_dec("  stride", hw.fb_stride);
	con_hex("  pipe src", hw.fb_src_addr);
	con_hex("  format", hw.fb_format);
	con_skip();

	con_dec("RAM banks", hw.nmem);
	for (uint32_t i = 0; i < hw.nmem && i < 8; i++) {
		con_hex("  bank", hw.mem[i].base);
		con_dec("  size", hw.mem[i].size);
	}
	con_dec("carve-outs", hw.nrsv);
	con_skip();

	con_hex("GICD", hw.gicd);
	con_hex("GICR", hw.gicr);
	con_hex("  stride", hw.gicr_stride);
	con_dec("vtimer", hw.vtimer_intid);
	con_dec("ptimer", hw.ptimer_intid);
	con_skip();

	con_hex("UFS", hw.ufs);
	con_hex("DWC3", hw.dwc3);
	con_hex("UART", hw.uart);
	con_dec("CPUS", hw.ncpus);
	for (uint32_t i = 0; i < hw.ncpus && i < 8; i++)
		con_hex("  mpidr", hw.mpidr[i]);
	if (hw.panel[0]) {
		con_str("PANEL", hw.panel);
		con_dec("  w", hw.fb_w);
		con_dec("  h", hw.fb_h);
	}
}

/*
 * Find the device tree.
 *
 * The bootloader is supposed to pass its address in x0, and on sweet it
 * does. On picasso it does not: the first probe there printed "no FDT from
 * ABL" and every board number came out as zero.
 *
 * Only known addresses are tried. Reading an address we do not know is not
 * safe here: the protected regions of the boot chain (the XBL and the RPM
 * live in the low memory) answer a read with a *secure fault*, and the
 * trust zone kills the core - the shim then hangs with the last line it
 * drew, with no panic screen to show why.
 */
static uintptr_t find_dtb(uintptr_t x0)
{
#ifdef NO_DTB
	/*
	 * This board: reading an address the bootloader did not map answers
	 * with a secure fault that kills the core. Everything the shim needs is
	 * compiled in instead: the board values come from the build
	 * (shim/build.sh --picasso) and the payload address comes from the
	 * boot image header (shim/loader.c).
	 */
	(void)x0;
	con_str("DTB", "not read on this board");
	con_skip();
	return 0;
#else
	/*
	 * Only the memory above 0x10000000 can be read safely: the low
	 * memory holds the protected parts of the boot chain (XBL, AOP, RPM
	 * and the trust zone up to 0x0FFFFFFF), and a read there is answered
	 * with a secure fault that kills the core.
	 */
	static const uintptr_t known[] = {
		0x10000000,
		0x11000000,
		0x12000000,
		0x14000000,
		0x18000000,
		0x1c000000,
		0x20000000,
		0x40000000,
	};
	int i;

	/*
	 * The bootloader gave us 0xA7EB0000 in x0 and x1. It is plain memory
	 * (reading it does not fault) but it does not start with the device
	 * tree magic, so it is not the tree: it is probably the structure the
	 * bootloader keeps about the image, with the tree's address inside.
	 */
	if (x0 >= 0x10000000 && x0 < 0x80000000 && (x0 & 7) == 0) {
		con_str("DUMP", "at x0");
		con_skip();
		for (i = 0; i < 24; i++) {
			uint32_t w = *(volatile uint32_t *)(x0 + i * 8);
			con_hex("  w", ((uint64_t)w << 32) | (uint64_t)i);
		}
		con_skip();
	}

	for (i = 0; i < (int)(sizeof(known) / sizeof(known[0])); i++) {
		/* say where we are before every read: a read that faults kills
		 * the core and the screen keeps only what was drawn last */
		con_hex("PROBE", known[i]);
		con_skip();
		if (fdt_magic_ok(known[i]))
			return known[i];
	}
	return 0;
#endif
}

/*
 * The bootloader keeps a structure about the image it booted. It is in
 * ordinary memory (reading it does not fault) and it holds the addresses the
 * bootloader used: the image buffer, the ramdisk, the device tree. Print the
 * words that look like addresses - the loader needs the ramdisk address and
 * the tree is worth knowing about.
 */
static void dump_bootinfo(uintptr_t info)
{
	unsigned shown = 0;

	for (int i = 0; i < 64; i++) {
		uint64_t w = *(volatile uint64_t *)(info + i * 8);
		int low = w >= 0x1000 && w < 0x4000000;
		int high = w >= 0x100000000ULL && w < 0x400000000ULL;
		if (!low && !high)
			continue;
		con_hex2("BOOTINFO", "w", i, "=", w);
		if (++shown >= 12)
			break;
	}
}

/* Does the board facts, and the bootargs, come from the device tree? */
static uint64_t ticks_fill(uint32_t c)
{
	uint64_t t = now();
	con_clear(c);
	return now() - t;
}

static uint64_t ticks_mem(void)
{
	uint64_t t = now();
	for (int pass = 0; pass < 16; pass++)
		for (int i = 0; i < 8192; i++)
			scratch[i] = scratch[i] * 3 + i;
	return now() - t;
}

static uint32_t to_us(uint64_t ticks)
{
	return ticks * 10 / 192;	/* 19.2 MHz */
}

void shim_main(uint64_t dtb)
{
	struct mmu_info mi;

	/*
	 * Learn the board from the device tree the bootloader handed us: the
	 * RAM banks, the carve-outs, the splash framebuffer, the GIC, the
	 * timer PPIs, the UFS host, USB and the console UART. On picasso the
	 * static tree still has `reg = <0 0 0 0>` for /memory, so this is the
	 * only way to learn where the RAM is.
	 */
	stage = "discover";

	/*
	 * Draw first: the console must be up before anything can go wrong.
	 * The framebuffer is the board's default here; the device tree, if it
	 * can be found, only confirms it.
	 */
	con_clear(RED);
	con_str("SHIM", "picasso probe");
	con_skip();

	con_hex("REG x0", boot_regs[0]);
	con_hex("REG x1", boot_regs[1]);
	con_hex("REG x2", boot_regs[2]);
	con_hex("REG x3", boot_regs[3]);
	con_skip();

#ifdef NO_DTB
	/*
	 * The bootloader frees what it used, so the addresses in the boot
	 * header are not mapped any more. It does keep its heap, and the
	 * address it passes in x0 points into it: look there for the boot
	 * image and for the payload.
	 */
	for (int i = 0; i < 12; i++) {
		uint64_t w = *(volatile uint64_t *)(boot_regs[0] + i * 8);
		con_hex2("X0", "w", i, "=", w);
	}
	con_skip();
	hunt_memory((uintptr_t)boot_regs[0]);
	con_skip();
#endif

	uintptr_t tree = find_dtb(dtb);
	con_str("STEP", "tree found");
	con_skip();
	con_hex("DTB", tree);
	con_skip();

	const char *why = hw_discover(tree);
	con_str("STEP", "discovered");
	con_skip();

	g_tree = tree;
	g_why = why;
	if (why == 0 && hw.fb_base)
		con_setup((uintptr_t)hw.fb_base, hw.fb_w, hw.fb_h, hw.fb_stride);
#ifdef NO_DTB
	/*
	 * Without a tree every number in the board report is zero, and the
	 * report takes the whole screen - which is where the loader's output
	 * has to go. So: a short note with the compiled-in values instead, and
	 * the structure the bootloader keeps about the image, which holds the
	 * addresses it used. The screen is not cleared, so both stay visible.
	 */
	con_str("BOARD", "no tree; the values are compiled in");
	con_skip();
	con_str("RAM", "0x140000000 + 0xC0000000");
	con_skip();
	con_hex("FB", FB_BASE);
	con_hex("GICD", 0x17a00000UL);
	con_hex("GICR", 0x17a60000UL);
	con_dec("vtimer", 19);
	con_hex("UFS", 0x01d84000UL);
	con_skip();
	dump_bootinfo((uintptr_t)boot_regs[0]);
	con_skip();
#else
	probe_report();
	con_skip();
#endif

	/* "sweetprobe" in the bootargs: show the board report and stop. */
	if (bootargs_have("sweetprobe")) {
		/*
		 * The display pipe registers are only read now: a register
		 * block that is clock-gated aborts the read, and an abort
		 * before the console exists would leave the bootloader's logo
		 * on the screen with nothing to show.
		 */
		hw_read_display_pipe(0);
		con_clear(RED);
		probe_report();
		con_skip();
		con_str("SHIM", "probe mode: report above, not booting");
		delay_ms(600000);
		reset(20);
	}

	/*
	 * Optional SMMU report and bypass, for the USB bring-up. The shim maps
	 * the first 8 GiB Device (VA == PA), so the apps SMMU at 0x15000000 is
	 * reachable here while the clocks and NoC votes of the bootloader still
	 * apply; from XNU that read hangs.
	 */
#if defined(SWEET_SMMU)
	if (1) {
#else
	if (bootargs_have("sweetsmmu")) {
#endif
		volatile uint32_t *smmu = (volatile uint32_t *)0x15000000UL;
		uint32_t s2 = (0x8000u + (0xe0u << 3)) / 4;

		stage = "smmu";
		con_str("SMMU", "apps-smmu@15000000");
		con_hex("IDR0", smmu[0x0000 / 4]);
		con_hex("CR0", smmu[0x0020 / 4]);
		con_hex("GBPA", smmu[0x0044 / 4]);
#if defined(SWEET_SMMU_SCM)
		/*
		 * The SMMU registers look secure-locked: a direct S2CR read hangs and
		 * CR0 writes are dropped. Android's qcom_scm exposes SCM_IO_READ/WRITE
		 * (service 0x5, cmd 0x1/0x2) for exactly such registers.
		 */
		{
			static volatile uint64_t scm_read_val;
			uint64_t r = smc(0x0501, 0x15000000UL + 0x8000 + (0xe0UL << 3),
			    (uint64_t)&scm_read_val, 0);
			con_hex("SCM rd rc", r);
			con_hex("SCM rd val", scm_read_val);
			uint32_t bypass = ((uint32_t)scm_read_val & ~0x3fu) | 1u;
			r = smc(0x0502, 0x15000000UL + 0x8000 + (0xe0UL << 3), bypass, 0);
			con_hex("SCM wr rc", r);
			r = smc(0x0501, 0x15000000UL + 0x8000 + (0xe0UL << 3),
			    (uint64_t)&scm_read_val, 0);
			con_hex("SCM rd2 rc", r);
			con_hex("SCM rd2 val", scm_read_val);
		}
#endif
#if defined(SWEET_SMMU_DISABLE)
		/* SMMUEN = 0 stops translation; transactions pass through. S2CR reads
		 * hang on this part, but CR0 is readable/writable. */
		smmu[0x0020 / 4] &= ~1u;
		__asm__ volatile ("dsb sy" ::: "memory");
		con_hex("CR0 now", smmu[0x0020 / 4]);
#endif
		con_hex("S2CR e0", smmu[s2]);
#if defined(SWEET_SMMU_BYPASS)
		if (1) {
#else
		if (bootargs_have("sweetsmmubypass")) {
#endif
			smmu[s2] = (smmu[s2] & ~0x3fu) | 1u;	/* TYPE = BYPASS */
			__asm__ volatile ("dsb sy" ::: "memory");
			con_hex("S2CR now", smmu[s2]);
		}
		con_skip();
#if defined(SWEET_SMMU_STOP)
		if (1) {
#else
		if (bootargs_have("sweetsmmustop")) {
#endif
			con_str("SHIM", "smmu probe: report above, reset in ~3 min");
			delay_ms(600000);
			reset(20);
		}
	}

#if defined(SWEET_USB_DMA_PROBE)
	/*
	 * Feasibility probe for owning USB in the loader instead of XNU.
	 * Everything is identity mapped here, so the buffer's VA is its PA.
	 * We only ask the controller to start one ep0 control transfer; no PHY
	 * setup, so a clean command timeout is the expected good outcome, and a
	 * reset of the phone means DMA is refused to non-secure clients at this
	 * point of the boot as well.
	 */
	{
		static uint8_t uarea[0x1000] __attribute__((aligned(0x1000)));
		volatile uint32_t *dwc = (volatile uint32_t *)0x0a600000UL;
		uint64_t pa = (uint64_t)(uintptr_t)uarea;
		uint32_t i, cmd;

		stage = "usb dma probe";
		con_str("USB", "DWC3 DMA probe at 0x0a600000");
		con_hex("GSNPSID", dwc[0xc120 / 4]);
		for (i = 0; i < sizeof(uarea); i += 4)
			*(volatile uint32_t *)(uarea + i) = 0;
		__asm__ volatile ("dsb sy" ::: "memory");

		dwc[0xc700 / 4] = dwc[0xc700 / 4] & ~7u;	/* DCFG: high speed */
		dwc[0xc400 / 4] = (uint32_t)pa;			/* GEVNTADRLO */
		dwc[0xc404 / 4] = (uint32_t)(pa >> 32);
		dwc[0xc408 / 4] = (1u << 31) | 0x1000;		/* GEVNTSIZ */
		dwc[0xc40c / 4] = dwc[0xc40c / 4] & 0xffff;	/* GEVNTCOUNT */

		/* One SETUP TRB at +0x100, buffer at +0x140. */
		*(volatile uint32_t *)(uarea + 0x100) = (uint32_t)(pa + 0x140);
		*(volatile uint32_t *)(uarea + 0x104) = 0;
		*(volatile uint32_t *)(uarea + 0x108) = 8;
		*(volatile uint32_t *)(uarea + 0x10c) = 0x20 | 1u | 2u | 0x400u | 0x800u;
		__asm__ volatile ("dsb sy" ::: "memory");
		con_hex("TRB pa", pa + 0x100);

		dwc[0xc800 / 4] = 0;				/* PAR2 */
		dwc[0xc804 / 4] = (uint32_t)(pa + 0x100);	/* PAR1 */
		dwc[0xc808 / 4] = 0;				/* PAR0 */
		dwc[0xc80c / 4] = 6u | (1u << 10);		/* STARTTRANSFER */
		__asm__ volatile ("dsb sy" ::: "memory");
		for (i = 0; i < 50; i++) {
			cmd = dwc[0xc80c / 4];
			if (!(cmd & (1u << 10)))
				break;
			delay_ms(2);
		}
		con_hex("DEPCMD", cmd);
		con_hex("cmd status", (cmd >> 12) & 0xf);
		con_skip();
		con_str("USB", "dma probe done: reset in ~3 min if phone survives");
		delay_ms(600000);
		reset(20);
	}
#endif

	const char *lwhy = loader_boot(dtb, (uintptr_t)boot_regs[0]);
	con_str("LOADER", lwhy);
	con_skip();

	/* No payload: probe the internal UFS while the MMU is still off. */
	stage = "ufs";
	test_ufs();
	con_skip();
	/* Keep the probe results on screen (later tests clear it); reset after 3 min. */
	con_str("UFS", "probe done, reset in ~3 min");
	delay_ms(600000);
	reset(20);

	stage = "fill off";
	uint64_t fill_off = ticks_fill(RED);
	uint64_t mem_off = ticks_mem();

	stage = "mmu enable";
	int rc = mmu_enable(&mi);

	stage = "fill on";
	uint64_t fill_on = rc ? 0 : ticks_fill(RED);
	uint64_t mem_on = rc ? 0 : ticks_mem();
	if (rc)
		con_clear(RED);

	con_str("SHIM", "sweet milestone 3");
	con_skip();
	con_hex2("DTB", "at=", dtb, "magic=", dtb ? __builtin_bswap32(rd32(dtb)) : 0);
	con_hex2("IMAGE", "lo=", mi.img_lo, "hi=", mi.img_hi);
	con_hex2("MMU", "rc=", rc, "l3=", mi.n_l3);
	con_hex2("TTBR0", "pa=", mi.ttbr, "pa_hi=", mi.ttbr >> 32);
	con_hex("SCTLR0", mi.sctlr_before);
	con_hex("SCTLR1", mi.sctlr_after);
	con_hex("TCR", rsr("tcr_el1"));
	con_hex2("PARANGE", "mmfr0=", rsr("id_aa64mmfr0_el1") & 0xf, "tg16=", (rsr("id_aa64mmfr0_el1") >> 20) & 0xf);
	con_skip();
	con_hex("PAR img", mmu_probe((uintptr_t)shim_main));
	con_hex("PAR fb", mmu_probe(FB_PHYS + 0x1000));
	con_hex("PAR gic", mmu_probe(GICD_BASE));
	con_hex("PAR 12G", mmu_probe(0x300000000UL));
	con_skip();
	con_hex2("FILL us", "off=", to_us(fill_off), "on=", to_us(fill_on));
	con_hex2("MEM us", "off=", to_us(mem_off), "on=", to_us(mem_on));
	con_skip();

	stage = "psci";
	con_hex2("PSCI", "ver=", smc(PSCI_VERSION, 0, 0, 0), "smccc=", smc(SMCCC_VERSION, 0, 0, 0));
	con_skip();
	test_gic();

	stage = "done";
	reset(40);
}
