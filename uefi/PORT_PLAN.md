# Mu-Silicium port for picasso (lito, SM7250) — plan

Goal: replace the shim with a UEFI that owns USB, so the loader can talk
fastboot while XNU runs. Motivation and the measurements behind it are in
`NOTES.md` (SMMU locked; the DWC3 DMA faults after ABL hands off).

## Findings that shape the plan

- Mu-Silicium has no lito/SM7250 package; the closest is `KodiakPkg` (SM7325).
  A SoC package is mostly `.dsc.inc` plus two libraries; the drivers come from
  `Binaries/<codename>`, i.e. the vendor's own DXE blobs.
- **This phone has no vendor DXE set**: `xbl` carries XBL/ABL code and an empty
  firmware volume, `xbl_config` has none, `abl` decompresses to a single
  `LinuxLoader` PE32 (`tools/xbl-dxe-list.py`, dumps in `~/picasso-fw`).
- Good news: Mu-Silicium contains an **open DWC3 device-mode DXE**,
  `Silicon/Samsung/SamsungPkg/Drivers/UsbfnDwc3Dxe` (2.4k lines, pure EDK2 plus
  a `PlatformUsbfnLib` for board init). That is the piece we would otherwise
  have had to write; it is portable to Qualcomm once the platform library does
  our PHY/QSCRATCH/GCC setup.

## Milestones

1. **Toolchain**: build Mu-Silicium for an existing device (`lisa`) unchanged.
   Proves the EDK2 build works on this host.
2. **picasso platform**: `Platforms/Xiaomi/picassoPkg` + `Silicon/Qualcomm/LitoPkg`
   with
   - memory map from the live DT (RAM at 0x80000000, three banks),
   - `FD_BASE`/`FD_SIZE`/`PAYLOAD_BASE` for the BootShim,
   - a GOP driver over the ABL framebuffer (0xa0000000, 1080x2400),
   - no UFS driver: the payload arrives over USB (fastboot stage), the root
     filesystem stays XNU's job.
3. **USB**: `UsbfnDwc3Dxe` + `picasso PlatformUsbfnLib` (PMIC rails, GCC GDSC
   and AHB2PHY reset, Femto PHY init, QSCRATCH UTMI clock) and a fastboot
   protocol on top (UsbFnIo -> simple block/transfer layer).
4. **Loader**: EFI app that stays resident, receives the XNU payload and log,
   boots it with the ADT (reuse `tools/mkpayload.py`/`mkadt.py`).
5. **Gate**: if the first DMA in UEFI also hangs, the SMMU is locked before any
   code of ours runs and this route dies with it - report instead of pushing on.

## Open questions

- Does ABL hand off with the SMMU still usable for the USB stream? The shim,
  which runs at the same point, could not access S2CR and its DMA hung. The UEFI
  test answers this; everything after milestone 3 depends on it.
- `fastboot boot` limit of 256 KB for the image (boot_shim + FD must fit or the
  FD has to be staged).
