# Bergamota (Poco F1 / SDM845) research, and what transfers to picasso

Bergamota = four projects by ry-diffusion, one per layer of "iOS 16/18 on a
non-Apple Qualcomm phone". This is a study of the parts that can be reused on
the Redmi K30 5G (picasso, SM7250), plus the concrete actions it implies.
Evidence is file/line in that repo; every claim about *our* port is marked
measured (hardware, ring/console) or inferred.

## 1. What Bergamota is

| Part | Directory | Role |
|---|---|---|
| Enxerto | `enxerto/` | `boot.img` payload: charger init, USB power-up, its own DWC3 **fastboot**, MMU+caches on, inflate payload, build ADT+boot_args, jump to XNU |
| Heartwood | `xnu/patches`, `xnu/patches-11215` | XNU 8796 (iOS 16) / 11215 (iOS 18) + ~130-240 port patches, built by `blacktop/darwin-xnu-build` |
| Renegade | `tools/renegade/` | arm64e -> ARMv8.0 Mach-O rewriter (kernel, userland, 4.8 GB dyld shared cache) + HFS+ root image builder |
| Bergamota | `apps/Bergamota` | macOS control plane (fastboot queue, UDP console, metrics, flash) |

Their boot chain:

```
ABL (stock) -> enxerto (boot.img) -> [payload in ABL ramdisk: SWEETXNU v1/v2]
    -> mmu_enable (16K, T0SZ=28, 36-bit VA; image WB, fb Normal-NC, rest Device)
    -> inflate/copy each segment to phys_base+(vmaddr-virt_base), caches ON
    -> ADT behind kernel, boot_args behind ADT, x0=boot_args PA, MMU off
  -> XNU: start.s ... launchd -> backboardd -> SpringBoard
```

## 2. Mapping onto our port

| Bergamota piece | picasso equivalent | State |
|---|---|---|
| `enxerto/` boot.img + `SWEETXNU` payload | `shim/` (`shim-boot.img`) + `tools/mkpayload.py` | ours; no fastboot, no charger screen |
| `tools/mkpayload.py --compress` v2 (deflate) | `tools/mkpayload.py` (uncompressed only) | payload 17 MB, fits ABL's 86/56 MiB window - **not needed yet** |
| `mkadt`/loader `boot_args` build | `tools/mkadt.py` + `shim/loader.c` | ours |
| `bsd/dev/sweet_usb.c` DWC3 gadget + CDC-ECM | `build/xnu/bsd/dev/sweet_usb.c` (patches 0072-0081) | ours; ep0 SETEPCONFIG works, **DMA never started** |
| `bsd/dev/sweet_smmu.c` (patch 0091/0094) | **new patch 0123** | added this round, untested on hardware |
| `sweet_dma_sync_va()` (patch 0102) | absent from our tree | **added to sweet_smmu.c** (local `dc civac`) - the USB TRBs still use the no-op `flush_dcache64()` |
| `tools/xnu-cc.py` (log replay) | `tools/xnu-local.py` | ours now recovers missing sources from the component Makefiles |
| Renegade arm64e rewriter + 0131 ptrauth grading | not applicable | picasso runs iOS 16 arm64, no PAC in play |
| `tools/sweet-log-pull.sh`, `sweetmetrics`, `sweetshot` | same scripts (sweet lineage) | ours already has them |

## 3. The eight transferable findings

1. **USB DMA needs the SMMU, not a lucky buffer address.** Their gadget only
   enumerates because `sweet_smmu_map_sid(0x740)` runs *before* the first
   endpoint command. Same SoC SMMU block (base `0x15000000`, IDR0/IDR1 at
   `0x20/0x24`, SMR `0x800+4i`, S2CR `0xc00+4i`, CB at `0x80000`), same
   constraint: under the Qualcomm hypervisor a stage-2 write is forced to
   FAULT (readback `0x00020000`), so the only legal shape is **S1 translate +
   S2 bypass** with the hypervisor's own stage 2 layered behind.
2. **Register order matters**: SCTLR=0 while programming, `TLBIALL`+`TLBSYNC`,
   then `SCTLR = 0x67`, then **S2CR first, SMR second**, then read S2CR back to
   prove `type==0 && cbndx==cb`. Printing that readback is what turns "DMA
   hangs" into "REFUSED by the hypervisor" or "mapped, so the fault is
   elsewhere".
3. **Our previous "USB is impossible on picasso" measurement was wrong.** The
   probe read S2CR at `0xc00 + 4*0xe0` = `0xc380` and SMR at `0xb80` - i.e. it
   used the stream ID as an *index*, past the 128-entry arrays. The hang was an
   out-of-range read, not evidence about the SMMU. Nothing in that conclusion
   ("CR0 writes dropped, SCM IO_READ returns -1, therefore no DMA") survives;
   what survives is that ep0 fully works and the first STARTTRANSFER wedges.
4. **`__ARM_COHERENT_IO__` makes `flush_dcache64()`/`clean_dcache64()` a bare
   `dsb sy`** (`osfmk/arm64/loose_ends.c:96-113`) - verified in this tree, so
   our SMMU page table and every USB TRB/buffer were reaching the device with
   stale cache lines. Their fix is `sweet_dma_sync_va()`: `dc civac` over
   `CTR_EL0.DminLine`, bracketed by `dsb sy`. Patch 0102 has it; our series
   does not.
5. **QUSB2 HS-PHY power-up is what makes endpoint RAM commands work.**
   `PWR_CTRL1` (offset `0x210`, byte replicated) reads `0x23 =
   POWER_DOWN|CLAMP_N_EN|VREF_SUPPLY_TRIM`; the fix writes `(0x23 & ~1) | 0x02`
   = `0x22`, waits 250 us and polls `PLL_STATUS` (`0x1a0`) bit 5. Our driver
   does the UTMI clock handshake in QSCRATCH but never touches PWR_CTRL1 -
   measurable difference if TRBs still fault.
6. **Boot-arg bitmask instead of one-off patches**: `sweetusbfix` bit0 PHY
   power-up, bit1 Linux `dwc3_core_soft_reset()`, bit2 sdm845 quirks (value 1
   expands to 7). One boot arg turns a whole class of USB experiments into
   boot-arg changes instead of five CI runs. Our driver has `usbstep` and
   `sweetsmmu`, but no such mask.
7. **Local compile replay beats CI round-trips.** Their `xnu-cc.py` replays
   per-file clang lines from a CI log; when a line is missing (GH truncates
   long C++ commands, make interleaves them) it **re-derives the flags from the
   generated per-component Makefile** (`CFILES=`, `CXXFILES=`, `SFILES=`,
   `$(SOURCE_DIR)/...`). Our `tools/xnu-local.py` now does the same and adds
   real cache/`dc` maintenance; 1100 compile commands, so a syntax error costs
   seconds instead of a 7-minute workflow.
8. **A 4 MiB ring at the end of the root partition plus UDP log over the
   gadget** is their entire observability stack, and it is ours too - but their
   USB never depends on the ring, and ours cannot depend on UDP until the
   gadget works. When USB is dead the ring is still reachable through recovery
   adb (`tools/sweet-log-pull.sh`); their repo has **no fastboot read path**
   either (only `oem hash` + `upload` of hashes), so ring-over-recovery is the
   only channel and must keep working.

## 4. What does not transfer

- Stream IDs and stream indices: SM7325 `0xe0`, SDM845 `0x740`, picasso `0xe0`
  (DT `iommus=&apps_smmu 0xe0`) - only the value moves, the code does not.
- Renegade and the 0131/0135-0143/0154-0160 patch set: those exist to run
  arm64e iOS 18 on a PAC-less SoC. Nothing in the picasso path needs them.
- Their QUSB2/GDSC/GCC offsets are SDM845-specific; ours (GCC `0x100000`,
  GDSC `0x0f004`, AHB2PHY `0x6a000`, femto PHY `0x88e3000`) stay.
- Enxerto's fastboot/flash/charge stack solves "how do I recover when USB
  works"; on picasso USB is exactly what does not work, so porting it is a
  large amount of code for a channel we cannot yet reach.

## 5. Next actions (in order)

1. **Boot patch 0123** with `sweetusbsmmu` reporting, and read the SMMU lines
   from the ring: `IDR0/IDR1/CR0/GBPA`, then `sid 0xe0 -> smr N s2cr 0x... 0x...
   (translate|REFUSED)`. Success criterion: `translate`, and `DEPCMD
   STARTTRANSFER` no longer wedges.
2. If it still faults: add `sweetusbfix` semantics (PHY `PWR_CTRL1` 0x22 +
   `PLL_STATUS` poll) as **patch 0124**, then re-test; that is the documented
   remaining difference from the working gadget.
3. If TRBs complete but no enumeration: bring `sweet_dma_sync_va()` into
   `sweet_usb.c` (patch 0102 equivalent) for every TRB and data buffer.
4. Only after enumeration: the Bergamota observability stack (UDP 5140 log,
   `sweetmetrics`, `sweetshot`) already exists here and can be switched on.
5. Optional, when payload size or boot speed becomes a problem: enxerto's
   compressed `SWEETXNU` v2 and cache-on inflate.
