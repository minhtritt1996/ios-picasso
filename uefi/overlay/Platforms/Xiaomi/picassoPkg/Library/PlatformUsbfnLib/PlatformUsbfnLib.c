/** @file
  picasso (SM7250) platform data and PHY bring-up for the generic DWC3
  UsbFn driver.

  The register sequences are the ones proven from the loader and from the XNU
  driver on this board: GDSC on, AHB2PHY block reset released, SNPS Femto PHY
  init, VBUS session override, then the QSCRATCH UTMI clock handshake. The PMIC
  rails are left as ABL set them for fastboot; the shim measurement showed they
  are on at this point.
**/
#include <Uefi.h>
#include <Library/IoLib.h>
#include <Library/PlatformUsbfnLib.h>
#include <Library/TimerLib.h>
#include <Library/BaseMemoryLib.h>
#include <Library/BaseLib.h>

#define DWC3_BASE      0x0A600000UL
#define QSCRATCH_BASE  0x0A6F8800UL
#define HSPHY_BASE     0x088E3000UL
#define GCC_BASE       0x00100000UL

#define GCC_USB_PHY_CFG_AHB2PHY_BCR  0x6A000
#define GCC_USB30_PRIM_GDSCR         0x0F004
#define GCC_QUSB2PHY_PRIM_BCR        0x12000

#define QS_GENERAL_CFG   0x08
#define PIPE_UTMI_CLK_SEL   (1u << 0)
#define PIPE3_PHYSTATUS_SW  (1u << 3)
#define PIPE_UTMI_CLK_DIS   (1u << 8)
#define QS_HS_PHY_CTRL     0x10
#define UTMI_OTG_VBUS_VALID (1u << 20)
#define SW_SESSVLD_SEL      (1u << 28)

#define HS_UTMI_CTRL0   0x3C
#define HS_UTMI_CTRL5   0x50
#define HS_COMMON0      0x54
#define HS_COMMON1      0x58
#define HS_COMMON2      0x5C
#define HS_CTRL1        0x60
#define HS_CTRL2        0x64
#define HS_OVRD_X0      0x6C
#define HS_OVRD_X1      0x70
#define HS_OVRD_X2      0x74
#define HS_CFG0         0x94
#define HS_REFCLK_CTRL  0xA0

STATIC
VOID
WriteReg (
  IN UINTN   Base,
  IN UINT32  Offset,
  IN UINT32  Value
  )
{
  MmioWrite32 ((UINTN)Base + Offset, Value);
  MemoryFence ();
}

STATIC
UINT32
ReadReg (
  IN UINTN   Base,
  IN UINT32  Offset
  )
{
  UINT32 Value = MmioRead32 ((UINTN)Base + Offset);
  MemoryFence ();
  return Value;
}

EFI_STATUS
UsbfnPlatformGetData (
  OUT USBFN_PLATFORM_DATA  *PlatformData
  )
{
  if (PlatformData == NULL) {
    return EFI_INVALID_PARAMETER;
  }

  ZeroMem (PlatformData, sizeof (*PlatformData));
  PlatformData->ControllerBase        = DWC3_BASE;
  PlatformData->MaximumSpeed          = UsbBusSpeedHigh;
  PlatformData->UtmiWidth             = UsbfnUtmiWidth8Bit;
  PlatformData->Usb2FreeClockExists   = FALSE;
  PlatformData->SuperSpeedPhyDisabled = TRUE;   /* the K30 port is USB 2.0 only */
  PlatformData->VendorId              = 0x1209;
  PlatformData->ProductId             = 0x0001;
  return EFI_SUCCESS;
}

EFI_STATUS
UsbfnPlatformPhyInit (
  VOID
  )
{
  UINT32 Value;

  /* GDSC usb30_prim: bit 0 is SW_COLLAPSE (1 = power down). */
  Value = ReadReg (GCC_BASE, GCC_USB30_PRIM_GDSCR);
  if ((Value & 1u) != 0) {
    WriteReg (GCC_BASE, GCC_USB30_PRIM_GDSCR, Value & ~1u);
    MicroSecondDelay (1000);
  }

  /* AHB2PHY block reset: bits 0-1 low. */
  Value = ReadReg (GCC_BASE, GCC_USB_PHY_CFG_AHB2PHY_BCR);
  if ((Value & 3u) != 0) {
    WriteReg (GCC_BASE, GCC_USB_PHY_CFG_AHB2PHY_BCR, Value & ~3u);
    MicroSecondDelay (1000);
  }

  /* PHY reset through QUSB2PHY_PRIM_BCR. */
  WriteReg (GCC_BASE, GCC_QUSB2PHY_PRIM_BCR, ReadReg (GCC_BASE, GCC_QUSB2PHY_PRIM_BCR) | 1u);
  MicroSecondDelay (1000);
  WriteReg (GCC_BASE, GCC_QUSB2PHY_PRIM_BCR, ReadReg (GCC_BASE, GCC_QUSB2PHY_PRIM_BCR) & ~1u);
  MicroSecondDelay (1000);

  /* SNPS Femto HS PHY sequence (same as Linux msm_hsphy_init). */
  WriteReg (HSPHY_BASE, HS_CFG0, ReadReg (HSPHY_BASE, HS_CFG0) | (1u << 1));
  WriteReg (HSPHY_BASE, HS_UTMI_CTRL5, ReadReg (HSPHY_BASE, HS_UTMI_CTRL5) | (1u << 1));
  WriteReg (HSPHY_BASE, HS_COMMON0, ReadReg (HSPHY_BASE, HS_COMMON0) & ~(7u << 4));
  WriteReg (HSPHY_BASE, HS_COMMON1, ReadReg (HSPHY_BASE, HS_COMMON1) | (1u << 5));
  WriteReg (HSPHY_BASE, HS_REFCLK_CTRL, (ReadReg (HSPHY_BASE, HS_REFCLK_CTRL) & ~3u) | 2u);
  WriteReg (HSPHY_BASE, HS_COMMON1, ReadReg (HSPHY_BASE, HS_COMMON1) | (1u << 4));
  WriteReg (HSPHY_BASE, HS_CTRL1, ReadReg (HSPHY_BASE, HS_CTRL1) | 1u);
  WriteReg (HSPHY_BASE, HS_OVRD_X1, 0xC8);
  WriteReg (HSPHY_BASE, HS_OVRD_X2, 0x24);
  WriteReg (HSPHY_BASE, HS_OVRD_X0, 0x64);
  WriteReg (HSPHY_BASE, HS_COMMON2, ReadReg (HSPHY_BASE, HS_COMMON2) | 1u);
  WriteReg (HSPHY_BASE, HS_CTRL2, ReadReg (HSPHY_BASE, HS_CTRL2) | (1u << 3) | (1u << 2));
  WriteReg (HSPHY_BASE, HS_UTMI_CTRL0, ReadReg (HSPHY_BASE, HS_UTMI_CTRL0) | 1u);
  WriteReg (HSPHY_BASE, HS_UTMI_CTRL5, ReadReg (HSPHY_BASE, HS_UTMI_CTRL5) & ~(1u << 1));
  WriteReg (HSPHY_BASE, HS_CTRL2, ReadReg (HSPHY_BASE, HS_CTRL2) & ~(1u << 3));
  WriteReg (HSPHY_BASE, HS_CFG0, ReadReg (HSPHY_BASE, HS_CFG0) & ~(1u << 1));
  MicroSecondDelay (2000);

  /* VBUS session override for a high-speed only port. */
  WriteReg (QSCRATCH_BASE, QS_HS_PHY_CTRL,
            ReadReg (QSCRATCH_BASE, QS_HS_PHY_CTRL) | UTMI_OTG_VBUS_VALID | SW_SESSVLD_SEL);

  /* UTMI clock handshake from dwc3-msm's POST_RESET event. */
  WriteReg (QSCRATCH_BASE, QS_GENERAL_CFG, ReadReg (QSCRATCH_BASE, QS_GENERAL_CFG) | PIPE_UTMI_CLK_DIS);
  MicroSecondDelay (2000);
  WriteReg (QSCRATCH_BASE, QS_GENERAL_CFG,
            ReadReg (QSCRATCH_BASE, QS_GENERAL_CFG) | PIPE_UTMI_CLK_SEL | PIPE3_PHYSTATUS_SW);
  MicroSecondDelay (2000);
  WriteReg (QSCRATCH_BASE, QS_GENERAL_CFG, ReadReg (QSCRATCH_BASE, QS_GENERAL_CFG) & ~PIPE_UTMI_CLK_DIS);
  MicroSecondDelay (2000);

  return EFI_SUCCESS;
}

EFI_STATUS
UsbfnPlatformPhyExit (
  VOID
  )
{
  WriteReg (GCC_BASE, GCC_QUSB2PHY_PRIM_BCR, ReadReg (GCC_BASE, GCC_QUSB2PHY_PRIM_BCR) | 1u);
  return EFI_SUCCESS;
}

BOOLEAN
UsbfnPlatformIsVbusPresent (
  VOID
  )
{
  return TRUE;
}
