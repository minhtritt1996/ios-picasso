/*
 * Device-mode USB bring-up for the loader.
 *
 * The XNU driver cannot use the controller: the apps SMMU is enabled and
 * locked by the firmware (CR0.SMMUEN = 1, S2CR reads hang, SCM IO calls are
 * refused), so the controller's DMA faults and the phone resets. In the shim,
 * which runs right after ABL with the bootloader's clocks and rails still on,
 * the same command completes, so USB is owned here instead.
 *
 * This file does the whole sequence without threads: PHY init, controller
 * soft reset, UTEMI clock routing, ep0 configuration, a few SETUP answers and
 * a polled event loop. Called from shim_main under SWEET_USBBOOT.
 */
#include <stdint.h>
#include <stddef.h>

#include "console.h"

extern void delay_ms(uint32_t ms);
extern void con_skip(void);

#define DWC3_BASE	0x0a600000UL
#define QSCRATCH_BASE	0x0a6f8800UL
#define HSPHY_BASE	0x088e3000UL
#define GCC_BASE	0x00100000UL

/* DWC3 register offsets */
#define GCTL		0xc110
#define GINTSTS		0xc140
#define GEVNTADRLO	0xc400
#define GEVNTADRHI	0xc404
#define GEVNTSIZ	0xc408
#define GEVNTCOUNT	0xc40c
#define DCFG		0xc700
#define DCTL		0xc704
#define DEVTEN		0xc708
#define DSTS		0xc70c
#define DALEPENA	0xc720
#define DEPCMDPAR2(n)	(0xc800 + 0x10 * (n))
#define DEPCMDPAR1(n)	(0xc804 + 0x10 * (n))
#define DEPCMDPAR0(n)	(0xc808 + 0x10 * (n))
#define DEPCMD(n)	(0xc80c + 0x10 * (n))
#define GUSB2PHYCFG	0xc200
#define GUSB3PIPECTL	0xc2c0

#define DCTL_RUN_STOP	(1u << 31)
#define DCTL_CSFTRST	(1u << 30)
#define DEPCMD_CMDACT	(1u << 10)

/* QSCRATCH */
#define QS_GENERAL_CFG	0x08
#define PIPE_UTMI_CLK_SEL	(1u << 0)
#define PIPE3_PHYSTATUS_SW	(1u << 3)
#define PIPE_UTMI_CLK_DIS	(1u << 8)
#define QS_HS_PHY_CTRL	0x10
#define UTMI_OTG_VBUS_VALID	(1u << 20)
#define SW_SESSVLD_SEL	(1u << 28)

/* GCC */
#define GCC_USB_PHY_CFG_AHB2PHY_BCR	0x6a000
#define GCC_USB30_PRIM_GDSCR		0x0f004
#define GCC_QUSB2PHY_PRIM_BCR		0x12000

/* SNPS femto HS PHY (same sequence as the kernel driver) */
#define HSPHY_UTMI_CTRL0	0x3c
#define HSPHY_UTMI_CTRL5	0x50
#define HSPHY_COMMON0		0x54
#define HSPHY_COMMON1		0x58
#define HSPHY_COMMON2		0x5c
#define HSPHY_CTRL1		0x60
#define HSPHY_CTRL2		0x64
#define HSPHY_PARAM_OVRD_X0	0x6c
#define HSPHY_PARAM_OVRD_X1	0x70
#define HSPHY_PARAM_OVRD_X2	0x74
#define HSPHY_CFG0		0x94
#define HSPHY_REFCLK_CTRL	0xa0

static volatile uint32_t *dwc = (volatile uint32_t *)DWC3_BASE;
static volatile uint32_t *qs = (volatile uint32_t *)QSCRATCH_BASE;
static volatile uint32_t *phy = (volatile uint32_t *)HSPHY_BASE;
static volatile uint32_t *gcc = (volatile uint32_t *)GCC_BASE;

static uint32_t rd(volatile uint32_t *base, uint32_t off)
{
	uint32_t v = *(volatile uint32_t *)((uintptr_t)base + off);
	__asm__ volatile ("dsb sy" ::: "memory");
	return v;
}

static void wr(volatile uint32_t *base, uint32_t off, uint32_t v)
{
	*(volatile uint32_t *)((uintptr_t)base + off) = v;
	__asm__ volatile ("dsb sy" ::: "memory");
}

/* DMA area: identity mapped, so the VA is the physical address. */
static uint8_t dma[0x4000] __attribute__((aligned(0x4000)));

#define DMA_EVT		0x0000
#define DMA_EVT_SIZE	0x1000
#define DMA_TRB_OUT	0x1000
#define DMA_SETUP	0x1040
#define DMA_RX		0x1100

static int epcmd(unsigned ep, uint32_t cmd, uint32_t p0, uint32_t p1)
{
	wr(dwc, DEPCMDPAR0(ep), p0);
	wr(dwc, DEPCMDPAR1(ep), p1);
	wr(dwc, DEPCMDPAR2(ep), 0);
	wr(dwc, DEPCMD(ep), cmd | DEPCMD_CMDACT);
	for (int i = 0; i < 200; i++) {
		uint32_t v = rd(dwc, DEPCMD(ep));
		if (!(v & DEPCMD_CMDACT))
			return (int)((v >> 12) & 0xf);
		delay_ms(1);
	}
	return -1;
}

static void phy_init(void)
{
	uint32_t gdsc = rd(gcc, GCC_USB30_PRIM_GDSCR);

	if (gdsc & 1u) {	/* SW_COLLAPSE: make sure the domain is on */
		wr(gcc, GCC_USB30_PRIM_GDSCR, gdsc & ~1u);
		delay_ms(1);
	}
	{
		uint32_t bcr = rd(gcc, GCC_USB_PHY_CFG_AHB2PHY_BCR);
		if (bcr & 3u) {
			wr(gcc, GCC_USB_PHY_CFG_AHB2PHY_BCR, bcr & ~3u);
			delay_ms(1);
		}
	}
	wr(gcc, GCC_QUSB2PHY_PRIM_BCR, rd(gcc, GCC_QUSB2PHY_PRIM_BCR) | 1u);
	delay_ms(1);
	wr(gcc, GCC_QUSB2PHY_PRIM_BCR, rd(gcc, GCC_QUSB2PHY_PRIM_BCR) & ~1u);
	delay_ms(1);

	wr(phy, HSPHY_CFG0, rd(phy, HSPHY_CFG0) | (1u << 1));
	wr(phy, HSPHY_UTMI_CTRL5, rd(phy, HSPHY_UTMI_CTRL5) | (1u << 1));
	wr(phy, HSPHY_COMMON0, rd(phy, HSPHY_COMMON0) & ~(7u << 4));
	wr(phy, HSPHY_COMMON1, rd(phy, HSPHY_COMMON1) | (1u << 5));
	wr(phy, HSPHY_REFCLK_CTRL, (rd(phy, HSPHY_REFCLK_CTRL) & ~3u) | 2u);
	wr(phy, HSPHY_COMMON1, rd(phy, HSPHY_COMMON1) | (1u << 4));
	wr(phy, HSPHY_CTRL1, rd(phy, HSPHY_CTRL1) | 1u);
	wr(phy, HSPHY_PARAM_OVRD_X1, 0xc8);
	wr(phy, HSPHY_PARAM_OVRD_X2, 0x24);
	wr(phy, HSPHY_PARAM_OVRD_X0, 0x64);
	wr(phy, HSPHY_COMMON2, rd(phy, HSPHY_COMMON2) | 1u);
	wr(phy, HSPHY_CTRL2, rd(phy, HSPHY_CTRL2) | (1u << 3) | (1u << 2));
	wr(phy, HSPHY_UTMI_CTRL0, rd(phy, HSPHY_UTMI_CTRL0) | 1u);
	wr(phy, HSPHY_UTMI_CTRL5, rd(phy, HSPHY_UTMI_CTRL5) & ~(1u << 1));
	wr(phy, HSPHY_CTRL2, rd(phy, HSPHY_CTRL2) & ~(1u << 3));
	wr(phy, HSPHY_CFG0, rd(phy, HSPHY_CFG0) & ~(1u << 1));
	delay_ms(2);
}

void
shim_usb_boot(void)
{
	uint32_t v, gen;
	uint64_t pa = (uint64_t)(uintptr_t)dma;
	int st;

	con_str("USB", "device mode in the loader");
	con_hex("PHY 54", rd(phy, HSPHY_COMMON0));
	con_hex("GSNPSID", rd(dwc, 0xc120));
	con_skip();

	phy_init();
	con_hex("PHY 54 post", rd(phy, HSPHY_COMMON0));

	/* VBUS session override, high-speed only port. */
	wr(qs, QS_HS_PHY_CTRL, rd(qs, QS_HS_PHY_CTRL) | UTMI_OTG_VBUS_VALID | SW_SESSVLD_SEL);

	wr(dwc, GUSB2PHYCFG, rd(dwc, GUSB2PHYCFG) & ~((1u << 6) | (1u << 8)));
	wr(dwc, GUSB3PIPECTL, rd(dwc, GUSB3PIPECTL) & ~(1u << 17));
	wr(dwc, GCTL, rd(dwc, GCTL) | 1u);

	/* Soft reset, then route the UTMI clock (POST_RESET event precedent). */
	wr(dwc, DCTL, DCTL_CSFTRST);
	for (int i = 0; i < 500 && (rd(dwc, DCTL) & DCTL_CSFTRST); i++)
		delay_ms(1);
	con_hex("DCTL postreset", rd(dwc, DCTL));

	gen = rd(qs, QS_GENERAL_CFG);
	wr(qs, QS_GENERAL_CFG, gen | PIPE_UTMI_CLK_DIS);
	delay_ms(1);
	wr(qs, QS_GENERAL_CFG, rd(qs, QS_GENERAL_CFG) | PIPE_UTMI_CLK_SEL | PIPE3_PHYSTATUS_SW);
	delay_ms(1);
	wr(qs, QS_GENERAL_CFG, rd(qs, QS_GENERAL_CFG) & ~PIPE_UTMI_CLK_DIS);
	delay_ms(1);
	con_hex("GENERAL_CFG", rd(qs, QS_GENERAL_CFG));

	for (uint32_t i = 0; i < sizeof(dma); i += 4)
		*(volatile uint32_t *)(dma + i) = 0;
	__asm__ volatile ("dsb sy" ::: "memory");

	wr(dwc, GEVNTADRLO, (uint32_t)pa);
	wr(dwc, GEVNTADRHI, (uint32_t)(pa >> 32));
	wr(dwc, GEVNTSIZ, (1u << 31) | DMA_EVT_SIZE);
	wr(dwc, GEVNTCOUNT, rd(dwc, GEVNTCOUNT) & 0xffff);

	wr(dwc, DCFG, rd(dwc, DCFG) & ~7u);	/* high speed, address 0 */
	wr(dwc, DEVTEN, 0x7);

	st = epcmd(0, 0x9, 0, 0);	/* DEPSTARTCFG */
	con_hex("DEPSTARTCFG", (uint64_t)st);
	st = epcmd(0, 0x1, (64u << 3), (1u << 8) | (1u << 10) | (0u << 25));
	con_hex("SETEPCONFIG", (uint64_t)st);
	st = epcmd(0, 0x2, 1, 0);	/* SETTRANSFRESOURCE */
	con_hex("SETRESOURCE", (uint64_t)st);

	wr(dwc, DALEPENA, 0x1);

	/* Arm one SETUP TRB on ep0 OUT. */
	*(volatile uint32_t *)(dma + DMA_TRB_OUT + 0) = (uint32_t)(pa + DMA_SETUP);
	*(volatile uint32_t *)(dma + DMA_TRB_OUT + 4) = 0;
	*(volatile uint32_t *)(dma + DMA_TRB_OUT + 8) = 8;
	*(volatile uint32_t *)(dma + DMA_TRB_OUT + 12) = 0x20 | 1u | 2u | 0x800u;
	__asm__ volatile ("dsb sy" ::: "memory");
	st = epcmd(0, 0x6, 0, (uint32_t)(pa + DMA_TRB_OUT));
	con_hex("STARTTRANSFER", (uint64_t)st);

	wr(dwc, DCTL, rd(dwc, DCTL) | DCTL_RUN_STOP);
	for (int i = 0; i < 200 && (rd(dwc, DSTS) & (1u << 22)); i++)
		delay_ms(1);
	con_hex("DSTS", rd(dwc, DSTS));

	con_skip();
	con_str("USB", "polling events for 5 s; host should see a device");
	for (int i = 0; i < 500; i++) {
		v = rd(dwc, GINTSTS);
		if (v & (1u << 12))	/* USBRST */
			con_hex("event", v);
		if (v & (1u << 29))	/* DISCONNINT */
			con_hex("disconn", v);
		delay_ms(10);
	}
	con_hex("GINTSTS final", rd(dwc, GINTSTS));
	con_hex("DSTS final", rd(dwc, DSTS));
}
