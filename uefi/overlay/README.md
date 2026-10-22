# Mu-Silicium overlay for picasso (lito, SM7250)

Copy these files over a Mu-Silicium checkout at commit `42fe563` (the revision
this was developed against):

    cp -r uefi/overlay/. /path/to/Mu-Silicium/

Build (the tree needs Python 3.11+ for `tomllib`, clang, and its NASM NuGet
dependency is only needed for x86 images):

    cd /path/to/Mu-Silicium
    python3.11 -m venv .venv && .venv/bin/pip install -r pip-requirements.txt
    mv Mu_Basecore/BaseTools/Bin/nasm_ext_dep.yaml{,.disabled}   # no mono/NuGet here
    PATH=$HOME/toolchain/clang+llvm-17.0.6-*/bin:$PATH \
        .venv/bin/python build_uefi.py -d picasso -r RELEASE
    # -> Mu-picasso.img (BootShim + FD + DTB), flash with `fastboot boot`

## What is in here

- `BootShim/BootShim.S`: framebuffer markers (green = shim entered, red = FD
  copied) and the literal-pool/.ltorg fix so `_Payload` is the last byte of
  `BootShim.bin` (otherwise the concatenated FD is shifted and SEC reads
  garbage).
- `Silicon/Silicium/SiliciumPkg/Sec/Sec.c`: white marker at SEC entry plus
  stage bands (300 green, 340 red, 380 cyan, 420 yellow, 460 magenta).
- `Platforms/Xiaomi/picassoPkg`: platform with a **minimal** driver set
  (DxeCore, GIC, timer, SimpleFbDxe, Bds) - no vendor DXE exists for this phone.
- `Silicon/Qualcomm/LitoPkg`: rename of KodiakPkg (no lito package upstream).
- `Resources/Configs/picasso.toml`: FD base `0xA3000000` + `0x200000`, boot image
  header v1, `requires_kernel_header = true`.
- `Resources/DTBs/picasso.dtb`: the phone's own DTB.

- `Mu_Basecore/MdeModulePkg/Core/Dxe/DxeMain/DxeMain.c`: stage bands inside
  DxeCore (500 orange memory services, 540 purple GCD, 580 spring green event
  services, 620 grey dispatcher, 660 white before `gBds->Entry`).

- `Silicon/Silicium/SiliciumPkg/Drivers/SimpleFbDxe/SimpleFb.c`: GOP bands
  (700 cyan/red = region found/missing, 740 magenta, 780 white).
- `Mu_Basecore/MdeModulePkg/Universal/BdsDxe/BdsEntry.c`: BDS bands
  (820 orange = BdsEntry start, 860 purple = BeforeConsole returned).
- Emulated variable store: `PcdEmuVariableNvModeEnable = TRUE` in the DSC and
  `VariableRuntimeDxe` in DXE.inc/APRIORI.inc (BDS needs variables early).

- `Platforms/Xiaomi/picassoPkg/Library/PlatformBootManagerLibNull`: no-op
  platform hooks (the Mu implementation runs secure-boot/ACPI setup that needs
  drivers this port does not ship and hangs before the console).
- `BdsEntry.c` also draws large hex digits: stage 1 -> A -> B -> 2 -> 3 -> 4 ->
  5 -> 6 (consoles connected) -> 7 (AfterConsole), using a 3x5 font scaled 12x.

- `PlatformBootManagerLibNull` now launches `BootManagerMenuApp` from
  `PlatformBootManagerUnableToBoot` (`EfiBootManagerGetBootManagerMenu` +
  `EfiBootManagerBoot`); UiApp and BootManagerMenuApp are in the FV. Bands
  950/980 confirm the call and the lookup. The app's HII UI does not draw
  because there is no SimpleTextIn driver yet (no button/keyboard driver).

- `Drivers/UsbfnDwc3Dxe` (adapted from the Samsung open driver: ChipInfo
  dependency removed, package references pointed at picassoPkg) and
  `Library/PlatformUsbfnLib` (picasso PHY: GDSC, AHB2PHY reset, Femto PHY,
  VBUS override, QSCRATCH UTMI handshake). Both build into the FD; BDS still
  completes (digit 7) with them present. They expose `EFI_USBFN_IO_PROTOCOL`,
  not `EFI_USB_DEVICE_PROTOCOL`, so a fastboot transport on UsbFnIo is the next
  piece (Mu has no open producer of the latter).

- `Applications/UsbEnumerate`: a minimal UsbFnIo client (starts the controller,
  answers device/config descriptors, then polls). Bands: 1100 protocol found,
  1140 controller started, 1180 endpoints configured, 1200..1300 the steps
  inside `ConfigureEnableEndpoints`.
- `UsbfnDwc3Dxe.c` carries those step bands locally.

## Gate result: USB DMA is not available in UEFI either

Measured on hardware: `StartController` succeeds (PHY init + DWC3 init, host
sees a high-speed connect), EP0 OUT/IN and both bulk endpoints configure, and
the phone stops exactly at `Dwc3Ep0OutStart` - the first transfer. This is the
same wall as XNU and the plain shim, so the SMMU is locked before any
non-secure code runs, including a UEFI image loaded by ABL. The vendor DXE sets
that HackiOS uses work because they run inside the vendor firmware chain.

## State reached (2026-10-22)

BootShim runs, copies the FD to 0xA3000000, SEC runs: memory map, HOBs and FV
decompression all succeed (yellow band). DxeCore then runs to completion: the white band at
row 660 (just before `gBds->Entry`) is the last thing on screen, so the
firmware core, dispatcher and BDS entry all execute. GOP initialises too (bands at 700/740/780). BDS now completes its
initialisation: consoles are connected and `PlatformBootManagerAfterConsole`
returns (digit 7 on screen). The firmware core, GOP and BDS all run on lito.
Next: the USB device DXE (UsbfnDwc3Dxe + a picasso PlatformUsbfnLib) and a
loader, or a visible boot menu (BootManagerMenuApp) if wanted first.
