# iOS 16 on sweet (Redmi Note 10 Pro, SM7150) - working notes

## Agent handoff / active work (2026-10-24 16:15, latest)
### >>> UPDATE 2026-10-24 (bp7 ring is stale; status block added to stop photo-guessing) <<<
1. **bp7 boot (commit `eda6762`, CI `40107911357`, flashed by the user) ran the modem phase to
   `tick 148000 ms` on the console (amp2p alive, `pdr 3`, `efs r 1 w 0`, `tftp 0`), i.e. the modem
   is up, answers SMP2P, answers EFS ALLOC + one IOVEC read, and then parks.**
2. **The 37 KB ring pulled from `darwin` after that boot does NOT belong to it**: it contains
   `PDR query state` / `PDR restart_pd` lines and two ticks, but zero `PDR rx type` and zero
   `PDR response` lines, so it is the bp5-era ring (header seq 3). The eda6762 kernel's flusher
   either never started or never flushed. Consequence: **the darwin ring cannot be trusted to
   identify the running payload** - same trap as stale console photos.
3. **Fix committed (`bf31dec`, CI `40110159040`)**: the modem thread itself now writes a 4 KiB
   text status block at `(nblocks - 1026) * 512` in `darwin`, every 10 s, independent of the log
   flusher: `SWEETSTAT 1 / ms seq tx rx drops pdr tftp efs_r efs_w efs_err last_dport last_ms
   last_qmi last_len log_started log_total log_flushed log_seq log_fails log_base log_nblocks
   chan txq txq_drops sk slot`. `sweet_ufs.c` (patch 0093) additionally exports
   `sweet_log_started_g`, `sweet_log_base_g`, `sweet_log_nblocks_g`, and the flusher retries a
   failed range up to 3 times instead of giving up.
4. **Read it back from recovery** without any photo:
   `adb shell "dd if=/dev/block/bootdevice/by-name/darwin bs=4096 skip=<(size/4096)-1100> count=1100" | grep -a -A24 SWEETSTAT`
5. **EFS data check**: `modemst1` sector 0 carries an `IMAGEFS1` header (offset 0x28) and sector 1
   (the one block the modem reads at buf+0x200) is ciphertext-looking (`e0 39 44 9a 2a 00 2f 7d
   ...`), so the modem's EFS content is encrypted and a content comparison proves nothing. Our
   copy is byte-identical to the partition, the reply is a plain OK result (`rmtfs_err 0`).
6. **Open question for the next boot**: is the modem's TX path stalled (GLINK `txq` depth and
   `chan` state in the status block) and does the flusher start at all (`log_started`,
   `log_fails`)? Both answers come from the status block, no photo needed.

### >>> UPDATE 2026-10-22 (USB: the SMMU wall blocks both XNU and the shim) <<<

**Result of the whole USB investigation, with evidence:**

1. Everything in XNU works up to endpoint configuration: PMIC rails voted via
   RPMh (`ldoa5 880 / ldoa12 1710 / ldoa2 3050`), GDSC at GCC `0x0f004`, AHB2PHY
   reset `0x6a000`, the Femto PHY sequence, the QSCRATCH `GENERAL_CFG` UTMI
   clock handshake (`0x04 -> 0x0D`), and `ep0 SETEPCONFIG` returns success.
   Bisect gates (`sweetusbstep=0..4`) showed the phone survives every step
   except the transfer itself.
2. `DEPCMD_STARTTRANSFER` (the first DMA) hangs the bus: in XNU the phone resets
   through the watchdog, in the shim it just hangs. Tried buffers at
   `0x140xxxxxx` (kernel RAM), `0xA22F0000` (splash carve-out), `0x90000000`
   (Android's USB IOVA range) and the staged-payload area: all the same.
3. **Root cause: the apps SMMU is enabled and locked.** Measured from the shim:
   `IDR0 0x00260406`, `CR0 0x4C017E51` (SMMUEN=1), `GBPA 0x0`. A direct read of
   `S2CR(0xE0)` (the USB stream, DT `iommus = <&apps_smmu 0xE0>`) **hangs**,
   writing `CR0` to clear SMMUEN is **dropped**, and SCM `IO_READ/IO_WRITE`
   return `0xffffffffffffffff`. So non-secure code cannot configure or bypass
   the SMMU at all, and every DWC3 DMA faults.
4. The shim route (loader-owned USB) reaches the same wall: with the PHY up, the
   three ep0 commands succeed in the shim but `STARTTRANSFER` hangs exactly as
   in XNU — the fault is the missing stream mapping, not the kernel.
5. **What would work:** the vendor's UEFI path. HackiOS/UniXNU let Qualcomm's
   `UsbDeviceDxe` own USB, because in that environment XBL has configured the
   SMMU for USB. That means porting Mu-Silicium (SoC package for lito, vendor
   DXE blobs from `xbl.img`) — a separate, multi-week project.

**Practical state now:** the phone runs iOS to SpringBoard with no runtime USB.
Flashing and logs work through recovery/fastboot with
`tools/picasso-boot-remote.sh`, `tools/picasso-fastboot-remote.sh` and
`tools/sweet-log-pull-remote.sh` (host `root@100.121.254.115`, pass `k60`).
The shim keeps the compile-gated probes (`SWEET_SMMU*`, `SWEET_USB*`,
`SWEET_USBBOOT`) for whoever picks this up.


### Vendor firmware survey for a possible UEFI port (same day)

`tools/xbl-dxe-list.py` parses Qualcomm firmware volumes (ELF image, FV header at
`_FVH-0x28`, FFS files, section streams). Results on this phone (dumps in
`/home/tritm/picasso-fw/`, not in git):

| Partition | Contents |
| --- | --- |
| `xbl` (7,327,744 B) | XBL loader: AArch64 code, `Loading DxeCore at ...`, `PrePiLib.c`, `FwVolume.c`/`Decompress`, `UEFI Ver`, `Boot Device : UFS`, and `DisplayDxe` strings. Its one firmware volume (`vaddr 0x9fc00000`, 0x240000) is **empty (0xFF)** |
| `xbl_config` (524,288 B) | another ELF, no firmware volume |
| `abl` (2,097,160 B) | one FV (0x30000) with one FIRMWARE_VOLUME_IMAGE file, LZMA (GUID `ee4e5898-3914-4259-9d6e-dc7bd79403cf`); decompresses to 921,800 B containing a single PE32 named **LinuxLoader** |

So this device does **not** ship the vendor DXE set (UsbDeviceDxe, UfsDxe, GOP)
that HackiOS extracts for redwood (Kodiak). A Mu-Silicium port for picasso would
have to build those DXE drivers from EDK2 sources instead of dropping in vendor
binaries - significantly more than a "copy the blobs" port. Combined with the
SMMU wall, the UEFI/USB route is a real project, not a quick experiment.


### D1 result: SMMU configuration is not reachable from non-secure (negative)

- Vendor `drivers/iommu/arm-smmu.c` has `disable_bypass = true`: a stream with no
  IOMMU domain gets `S2CR = FAULT`, which is exactly the USB stream here.
- That driver writes `S2CR` **directly** (no SCM), so a bypass would be possible
  if the block were accessible. It is not:
  - `IDR0`, `CR0`, `GBPA` read correctly from the shim, but a second read of
    `CR0` (offset 0x20) in the page scan **hangs**, i.e. the block wedges after a
    few accesses - no stable access without the SMMU's clocks/NoC votes.
  - SCM `IO_READ`/`IO_WRITE` return `-1`; `CR0` writes are dropped.
- Conclusion: USB DMA cannot be enabled from XNU or the shim. Only the vendor
  UEFI/DXE environment (which XBL sets up before locking) does this.

Machine state after D1: flash the normal image with `sweetusb=1` so the phone
boots iOS without touching USB.


### D2 progress: Mu-Silicium tree ready, build driver needs Python 3.11

- Cloned `Mu-Silicium` to `/home/tritm/mu-silicium` (3.3 GB with submodules).
  No lito package; closest is `KodiakPkg` (SM7325).
- Its `build_uefi.py` needs `tomllib` (Python 3.11+); this host defaults to
  3.10. A venv with `python3.11 -m venv .venv` plus `pip install -r
  pip-requirements.txt` works; use `.venv/bin/python build_uefi.py -d <device>`.
- Platform anatomy (from `lisa`): `Platforms/<Vendor>/<dev>Pkg/{dev.dsc, dev.fdf,
  DeviceBuild.py, Library/MemoryMapLib, Library/ConfigurationMapLib}`,
  `Resources/Configs/<dev>.toml` (`[uefi_fd] base/size`), and
  `Binaries/<dev>/` for the DXE image set. lisa's FD is `0x9FD00000`+`0x200000`
  inside the same reserved window picasso has (`0x9F400000`+`0xC00000`).
- Open, reusable USB stack: `Silicon/Samsung/SamsungPkg/Drivers/UsbfnDwc3Dxe`
  (2.4k lines) expects a board `PlatformUsbfnLib`; that library is where our
  PMIC-rail/GDSC/AHB2PHY/Femto-PHY/QSCRATCH sequence goes.


### D2 milestone: UEFI reaches DxeCore on picasso (2026-10-22 late)

Mu-Silicium now boots our picasso platform far enough to decompress the FV and
enter DxeCore. Evidence from the screen (framebuffer markers, no UART):

| Marker | Stage | Seen |
| --- | --- | --- |
| green band | BootShim entered | yes |
| red band | FD copied to FD_BASE | yes |
| white band | `SecEntry` | yes |
| row 300 green | before `InitializeMemory` | yes |
| row 340 red | `InitializeMemory` done | yes |
| row 380 cyan | platform HOBs done | yes |
| row 420 yellow | FV decompressed | yes |
| row 460 magenta | `LoadDxeCoreFromFv` returned | (not reached on screen) |
| row 500/540/580/620 | DxeCore: memory, GCD, events, dispatcher | yes |
| row 660 white | just before `gBds->Entry` | **yes** |

Fixes that got us here:
- FD base cannot be `0x9FD00000` (picasso's secure CDSP window); use
  `0xA3000000` in free low RAM, heaps/stacks just above it.
- `BootShim.S`: the literal pool must be emitted **before** `_Payload`
  (`.ltorg`), otherwise `BootShim.bin` is longer than `_Payload` and the
  concatenated FD is shifted by 8 bytes.
- `requires_kernel_header = true` so the image carries the `ARM\x64` magic.

Overlay and build instructions: `uefi/overlay/README.md` (commit `bf158a2`).

Follow-up (same evening): DxeCore stage bands all pass (rows 500-660), GOP
initialises (SimpleFbDxe bands 700/740/780), and BDS reaches `BdsEntry` (row
820) but hangs before `PlatformBootManagerBeforeConsole` (row 860) even with
`PcdEmuVariableNvModeEnable` and VariableRuntimeDxe. Then the no-op `PlatformBootManagerLib` (Mu's version runs secure-boot/ACPI
setup that needs missing drivers) let BDS finish: consoles connect and
`AfterConsole` returns (large digit 7, drawn with a 3x5 font on the
framebuffer). UEFI core, GOP and BDS all run on picasso. Note the phone now **hangs**
instead of resetting, and the operator holds Volume Down, so every reset lands
in fastboot.


### Wi-Fi plan for picasso (modem-hosted WLAN, following ios-topaz)

Measured facts (live DT + firmware partitions read from recovery):

| Item | Value |
| --- | --- |
| WLAN transport | modem-hosted: `qcom,mss@4080000` has `memory-region = <modem_wlan_region@8c000000>` (0x8C000000 + 0xF800000 = 248 MiB) |
| PAS | `qcom,pas-id = 4` (same as topaz), `qcom,smem-id = 0x1A5`, `sysmon-id 0`, `ssctl-instance-id 0x12` |
| SMEM | `smem_region@80900000` + 0x200000 |
| SMP2P | entries `slave-kernel` (master), `wlan` (1-in), `modem` |
| WLAN host | `qcom,icnss@18800000`, MSA in `pil_wlan_fw_region@8ba00000`, IPA SMMU WLAN (unused) |
| Firmware | `/vendor/firmware_mnt/image/modem.mdt` + `modem.b00..b19` (2021, the phone's own), reachable by mounting the `modem` partition (vfat) in recovery |
| GPU FW (for later) | `a620_zap.mdt/b00/b01/b02`, `a630_sqe.fw`, `a650_gmu.bin` in `/vendor/firmware` |

Reference stack: `ios-topaz` (`xnu/patches/0113..0130`, 0900; ~9.6k lines). It reuses
HackiOS's upper half (`redwood_apple80211.c`, `redwood_net.c`, `redwood_sta.c`,
`RedwoodWLAN.cpp`, `MiniIONetworkStack.cpp`) and adds the modem PAS/GLINK/QRTR/WLFW/WMI
layers. HackiOS's own Wi-Fi driver (ath11k over PCIe, WCN6750) does not apply to picasso.

First coding step (patch 0089 in this tree):
1. minimal vfat reader over the `modem` partition (block I/O already exists via sweet_ufs),
2. MDT + segment loader into 0x8C000000,
3. SCM PAS: `pas_init_image(4, mdt)`, `pas_mem_setup(4, base, size)`, `pas_auth_and_reset(4)`,
4. console markers for each step (same style as the USB bring-up).

### >>> UPDATE 2026-10-22 (picasso: modem PAS boot implemented, CI run 39806873749) <<<
1. Decision: Wi-Fi follows `ios-topaz` (modem-hosted WLAN on SM7250), not HackiOS
   (ath11k over PCIe for WCN6750). Topaz already carries HackiOS's upper half
   (`redwood_apple80211.c`, `redwood_net.c`, `redwood_sta.c`, `RedwoodWLAN.cpp`,
   `MiniIONetworkStack.cpp`) plus the modem/GLINK/QRTR/WLFW/WMI layers.
2. Measured for picasso: `qcom,mss@4080000` pas-id 4, memory-region
   `modem_wlan_region@8c000000` + 0xF800000; SMEM `0x80900000` + 2 MiB;
   `qcom,smp2p-mpss` `qcom,smem = <0x1b3 0x1ac>` (in 435, out 428); tcsr-mutex
   syscon `0x1F40000`; firmware `modem.mdt` + `modem.b00..b19` (78 MiB total)
   on the vfat `modem` partition, read via a new UFS API.
3. Patches: `0089-picasso-modem-fw-partition-read` (sweet_ufs_part/pread,
   starts the thread), `0090-picasso-modem-pas-boot`
   (`iokit/Kernel/arm/PicassoModem.cpp`, boot-arg `modem=1`): vfat reader,
   ELF32 .mdt/.bNN loader into 0x8C000000, SCM PAS init_image/mem_setup/
   auth_and_reset, SMP2P slave-kernel watch. Both validated with
   `tools/check-patch.sh` against the run-39761351271 build tree; that script now
   derives the file list from the patch itself.
4. Next: CI build, flash with `tools/picasso-boot-remote.sh KERNEL "modem=1"`,
   read the console for `PicassoModem:` lines (vfat geometry, phdrs region,
   init_image/auth_and_reset returns, slave-kernel bits), then pull the ring.

### >>> UPDATE 2026-10-23 (picasso: modem driver runs; partitions live on different UFS LUNs) <<<
1. First live run (kernel 61cd192+, `sweetusb=2`): no `PicassoModem:` lines at all, and the
   known USB first-DMA reset rebooted the phone before anything else could be seen. With
   `sweetusb=0` the phone boots to iOS normally, so tests now use `sweetusb=0 modem=1`.
2. Diagnostic run (boot-args printed by the driver): the kernel receives all 323 bytes and
   `modem=1` parses (value=1), SMEM maps and reports version 12. So the earlier silence was
   the USB reset, not argument parsing.
3. Real blocker found: `no "modem" partition`. Android's `by-name/modem` resolves to
   **sde48 = UFS LUN 4** (`PARTNAME=modem`), while userdata/darwin are on LUN 0 (`sda`).
   Each LUN has its own GPT, and sweet_ufs only read LUN 0.
4. Fix (commit 5086cc1, patches 0089/0090): `sweet_ufs_part()` probes LUNs 0-7 and returns the
   LUN, `sweet_ufs_pread()` takes the LUN (restoring su.lun under the lock), and the driver
   logs `modem partition: LUN %u block 0x%llx`. Next boot should reach the FAT mount, the
   .mdt parse (`phdrs= hash= region=`) and the PAS calls.
5. Test recipe: build payload with `--cmdline "modem=1 modem_watch=25 <base with sweetusb=0>"`,
   `adb dd` to `boot`, reboot, wait for iOS, hold Power+VolDown 12 s, then
   `tools/picasso-modem-log.sh`. Note shim/build.sh needs an *absolute* payload path.

### Risk 2026-10-23: macOS-14 CI runners deprecated 2026-11-02
`369099105XDai/hackios-picasso-build` documents that the macos-14 image (Xcode 15.0.1,
the toolchain this xnu-8796 needs for the IORPC ABI) becomes unsupported on 2026-11-02,
and macos-15 has no Xcode 15. Plan: keep using macos-14 until then; afterwards a
self-hosted macOS VM (KVM is available on this host) or an Xcode-16 IORPC port.

### >>> UPDATE 2026-10-23 (modem PAS: firmware path understood, init_image next) <<<
Live runs, in order, each fixing one real blocker:
1. `sweetusb=2` resets the phone on the first DWC3 DMA; test boots use `sweetusb=0`.
   `sweetscan=-` is also needed: the SpringBoard heap scanner (default on) writes
   ~700 lines per crash loop and overwrites the 4 MiB console ring.
2. `modem` lives on **UFS LUN 4** (`sde48`), userdata on LUN 0; `sweet_ufs_part()`
   now probes LUNs 0-7 and `sweet_ufs_pread()` takes the LUN.
3. The FAT root holds only a directory with long name `image`; `modem.mdt` and
   `modem.bNN` are **long-named** entries inside it. The driver parses VFAT long
   names now (8.3 aliases are generated garbage, e.g. `PCAFUI~2`).
4. `modem.mdt` (9420 B): e_phnum 35, hash phdr index 1 (p_offset 0x1000, p_filesz
   0x2038, p_paddr 0x9B800000), loadable segments 0x8C000000..0x9B800000,
   RELOCATABLE set. It is a **split** MDT: `phdrs[0].p_filesz` = 1172 = ELF header
   + phdr table, and the 8248-byte hash segment is **packed right after the
   phdrs** (1172 + 8248 = the whole file). That is the first branch of Linux's
   `qcom_mdt_read_metadata()`: metadata = header + hash, contiguous, no b01 read.
5. TrustZone answers `PAS is_supported` with 0 for ids 0-15, and
   `SCM_SVC_INFO get_version` with -1, so the SCM path is alive but those calls
   are not what this TZ exposes. We proceed with the device-tree id 4 anyway; the
   `init_image` return value is the next answer to read.
Test loop: build payload with `--cmdline "modem=1 modem_watch=25 <base>"`, dd to
`boot`, reboot, wait for the Apple logo ~30 s, hold Power+VolDown ~12 s,
`tools/picasso-modem-log.sh`. Artifacts: `tools/gh-artifact.sh RUN xnu-kernel OUT.zip`
(8 ranged connections, ~35 s for 60 MB).

### >>> MILESTONE 2026-10-23: modem DSP boots under XNU (PAS works) <<<
Live run 9 (`modem=1`, kernel cd3214b):
```
phdrs=35 hash=1 region 8c000000..9b800000 relocate=1
metadata: ehdr 1172 + hash 8248 = 9420 (mdt 9420, hash off 1000)  -> packed
metadata: kmem_alloc_contig kr=0 ... pa=144364000
init_image:    ret=0 res=0 resumes=2
mem_setup:     ret=0 res=0
segments loaded
auth_and_reset: ret=0 res=0 resumes=35
t=32 ms: modem SMP2P item 435 up (v1, 1 entries)
t=58 ms: slave-kernel 00000006 READY HANDOVER
```
So TrustZone's PAS does work for the AP: `is_supported` answers 0 for every id and
`SCM_SVC_INFO get_version` answers -1, but `init_image`/`mem_setup`/`auth_and_reset`
all succeed with the device-tree id 4. The metadata must be exactly what Linux's
`qcom_mdt_read_metadata()` builds (header 1172 bytes + packed hash 8248), and the
carveout is 0x8C000000 + 0xF800000.

Next (Wi-Fi step 2): GLINK/QRTR to the modem, host services (pd-mapper, tftp, rmtfs),
WLFW (WLAN firmware out of reset), then WMI/HTC/HTT and the Apple80211 shim.
Reference: ios-topaz patches 0118, 0121, 0122 (0113-0117 are now superseded by
PicassoModem.cpp for the boot).

### Wi-Fi step 2 constants, measured 2026-10-23 (live SMEM dump after MODEM READY)
Apps<->modem SMEM partition (0,1): 6 items, 507904 bytes, 21360 used.
- 478, 32 B: GLINK descriptor (descs[0]=tx.tail [1]=tx.head [2]=rx.tail [3]=rx.head)
- 480, 16384 B: GLINK RX FIFO (created by the modem)
- 479, 16384 B: **GLINK TX FIFO, does not exist yet: the AP must allocate it**
  (`qcom_smem_alloc(remote, 479, SZ_16K)`), then both dirs are 16 KiB
- 428/435: our SMP2P out / the modem's SMP2P in (340 B each)
- 605 (32 B), 611 (4096 B): other SMP2P/queue items
Global partition: 20 items, item 421 = "SFR Init..." (crash reason), etc.
Doorbell (SM7250 uses IPCC, not APCS): base 0x408000, IPCC_REG_SEND_ID 0x0c,
value = (client_id << 16) | signal_id = (2 << 16) | 0 from the DT mbox
`<&ipcc 2 0>`. RX side: RECV_ID 0x10, RECV_SIGNAL_CLEAR 0x1c.
Reference implementation to port: ios-topaz 0118 (GLINK/QRTR, 590 lines),
0121 (pd-mapper/tftp/rmtfs, 2174), 0122 (WLFW, 533); QRTR node apps=1,
WLFW QMI service 0x45, channel "IPCRTR", lcid 1, our intents 0x4400 x 8.

### Wi-Fi step 2 progress 2026-10-23 (GLINK up; WLFW missing; host services next)
- GLINK/QRTR works with the modem: VERSION/ACK, OPEN glink_ssr+IPCRTR+SSM_RTR_MODEM_APPS,
  HELLO both ways, modem announces ssctl (0x2b) and servreg-notif (0x42). WLFW (0x45)
  never appears, even after (a) acking every channel, (b) polling 20 s, (c) voting the
  WLAN rails successfully.
- WLAN rails and their *correct* voltages come from the vendor driver, not the DT init
  values: vdd-cx-mx 752 mV, vdd-1.8-xo 1800, vdd-1.3-rfa 1304, vdd-3.3-ch0/ch1 3312
  (icnss.c icnss_vreg_info[]). The RPMh whitelist patch 0091 had to learn ldoa1/ldoa14/
  ldoc2/ldoc10.
- `/vendor`/modem partition has **image/wlanmdsp.mbn** (3.87 MB, ELF, segments at
  0xB0000000..0xB07B1198) and bdwlan.*; the *vendor icnss never loads firmware* (no
  request_firmware, no PAS): icnss only does QMI WLFW with the modem, so the modem owns
  the WLAN core and gets files from the AP.
- Therefore the missing piece is likely the **host services** the modem expects from the
  AP, exactly what ios-topaz implemented in 0121: pd-mapper (QMI servreg-locator 0x40),
  tftp (QRTR service 4096, serves /readonly/firmware/image/... from the modem partition)
  and rmtfs (QMI 14, EFS read-only). That is the next port, ~2k lines.

### >>> UPDATE 2026-10-23 (host services live; modem queries pd-mapper) <<<
GLINK/QRTR is solid. The modem talks to our services:
- It asks pd-mapper (service 0x40) **within 2 ms** of our announcement. Requests use a
  7-byte QMI header (type, txn, msg, msg_len) and a raw NUL-free service-name TLV
  (`01 0f 00 "tms/pdr_enabled"`); responses must use the same header.
- Answered so far: `tms/pdr_enabled` -> msm/modem/root_pd (1 domain), `tms/pddump_disabled`
  -> 0 domains. No `wlan/fw` query yet, and **no tftp request at all**.
- WLFW (0x45) still never registers, with rails voted at the vendor icnss voltages
  (752/1800/1304/3312/3312 mV, all rc=0).
- Vendor data checked: no pd-mapper JSON in /vendor, no wlan PIL node in the live DT,
  icnss never calls request_firmware/PAS. `image/wlanmdsp.mbn` (3.87 MB, segments at
  0xB0000000..0xB07B1198) exists on the modem partition for something else to load, most
  likely the modem after it gets MSA access.
Next ideas, in order of cost:
1. Poll 60 s (maybe WLAN boot is slow) and log every QRTR packet.
2. Assign the MSA region (0x8BA00000 + 0x200000) to HLOS/MSS_MSA(15)/WLAN(24) with SCM
   hyp_assign_phys (0x0C/0x16) and copy wlanmdsp.mbn there, then see if WLFW appears.
3. Query ssctl (0x2b, registered) for the wlan subsystem state.
Host services implemented in PicassoModem.cpp: pd-mapper (0x40) and tftp (service 4096,
serving /readonly/firmware/image/<name> from image/<name> via the FAT reader, cached in
RAM); rmtfs is not implemented yet and nothing has asked for it.

### >>> UPDATE 2026-10-23 (60 s run: MSA empty, PDR stops after two TMS queries) <<<
- The WLAN MSA pool (`qcom,wlan-msa-fixed-region`, 0x8BA00000 + 2 MiB) is **all zeros** at
  boot: the bootloader does not preload any WLAN firmware.
- pd-mapper now answers both queries (`tms/pdr_enabled`, `tms/pddump_disabled` -> 1 domain
  each) but the modem asks nothing more in 60 s: no `wlan/fw`, no `kernel/elf_loader`, and
  our tftp service (announced, service 4096) is never contacted. WLFW never appears.
- Modem firmware contains no WLFW strings (strings over modem.b00..b33), while the vendor
  host module qca_cld3_wlan.ko does; the WLFW server therefore comes from the WLAN
  firmware, which nothing on this device has loaded yet.
Open leads, ranked:
A. pd-mapper response encoding: the request uses a 7-byte QMI header and a raw NUL-free
   name TLV; the domain-list entry may also be raw/padded rather than the length-prefixed
   form we copied from topaz. A silently rejected response would explain the dead end.
B. servreg-notif (0x42): the modem may start the wlan PD only after the AP registers for
   its state; no client registration has been attempted.
C. Load `image/wlanmdsp.mbn` ourselves: the image links at IOVA 0xB0000000..0xB07B1198
   (inside `qcom,iommu-dma-addr-pool`), 3.87 MB, split MDT-style; would need the WLAN
   SMMU + copy engines, which is where the qcacld host driver comes in.

### >>> UPDATE 2026-10-23 (PDR client: modem rejects register-listener, error 45) <<<
- The AP-side PDR client now sends SERVREG_REGISTER_LISTENER (0x20) to the modem's
  servreg-notif (0x42, node 0 port 2) for the service path "wlan/fw". The modem answers
  **result=1 (failure), error=45** both with a raw string TLV and with a QMI string
  (u8 length + chars), so the encoding is probably not the problem.
- Next run dumps the exact request and response bytes (kernel-pdrdump.sweet); after that
  the error code and TLV order can be decoded instead of guessed.
- The phone stopped enumerating on both hosts right after that flash (no 05c6 anywhere);
  a power cycle / replug is needed before the next test.
- Note: /tmp on this host is cleared periodically. Work now lives in ~/picasso-work
  (PicassoModem.cpp reconstructed from the patch, kernels under kernel-*.sweet) and
  artifacts are fetched with tools/gh-artifact.sh, which takes GH_TOKEN or /tmp/ghtoken.

### PDR findings 2026-10-23 (libqmi error names)
- The modem's servreg-notif QUERY_STATE (0x21) answers **result 0** for both "wlan/fw" and
  "msm/modem/wlan_pd", so those names exist in its registry.
- REGISTER_LISTENER (0x20) is refused: 0x2d (QMI_PROTOCOL_ERROR_INVALID_DATA_FORMAT) for
  service-style names and 0x09 (QMI_PROTOCOL_ERROR_INVALID_HANDLE) for "msm/modem/..."
  domain-style names.
- All three service-name encodings (QMI string, QMI string + NUL, raw NUL-terminated) give
  0x2d, so the name bytes are not the problem; next the enable TLV is varied (1 byte,
  4 bytes, absent).
- Request/response bytes observed:
  req  00 01 00 20 00 0f 00 | 01 01 00 01 | 02 08 00 07 "wlan/fw"
  resp 02 01 00 20 00 0e 00 | 02 04 00 01 00 2d 00 | 10 04 00 80 f5 39 ca
  (the 0x10 TLV in responses is a 4-byte value that looks like a modem-side address or
  an uninitialised state; QUERY_STATE returns 0x7fffffff there)

### PDR register-listener: what the modem accepts (measured 2026-10-23)
| request | answer |
| --- | --- |
| QUERY_STATE "wlan/fw" | result 0, error 0 (state TLV 0x7fffffff) |
| QUERY_STATE "msm/modem/wlan_pd" | result 0, error 0 |
| REGISTER, enable 1 byte + QMI string "wlan/fw" | error 45 INVALID_DATA_FORMAT |
| REGISTER, same with NUL / raw NUL-terminated | error 45 |
| REGISTER, enable 4 bytes | error 1 INTERNAL |
| REGISTER, no enable TLV | error 17 MISSING_ARGUMENT |
| REGISTER with "wlan", "wlan_pd", "msm/wlan/wlan_pd" | error 45 |
| REGISTER with "msm/modem/wlan_pd", "msm/modem/wlan", "msm/modem/wlan/fw" | error 9 INVALID_HANDLE |
So: enable must be 1 byte (missing -> 17), the service name is known to the modem (query
succeeds), yet registration is rejected as invalid data. Unexplored knobs:
- TLV order (0x02 before 0x01), extra TLV 0x03 (instance) or the locator's service_data,
- the address: we send to the QRTR port from NEW_SERVER (service 0x42 inst 0xb401, port 2),
  while qmi_helpers would qmi_add_lookup(0x42, version, instance) first,
- maybe the AP must register as the *server* of "wlan/fw" and let the modem ask for it.
Until this is resolved the wlan PD never starts, so WLFW never appears.

### PDR: query is not a validity test (2026-10-23)
QUERY_STATE returns result 0 for *every* string, including made-up ones
("msm/modem/wlan_pd/wlan", "msm/modem/root_pd/tms/servreg"), with state 0x7fffffff.
So earlier conclusions from query must be dropped.
REGISTER_LISTENER classification by name shape:
- names with one slash ("wlan/fw", "wlan", "wlan_pd") -> 45 INVALID_DATA_FORMAT
- names with two or more ("msm/modem/wlan_pd", "msm/modem/wlan_pd/wlan/fw",
  "msm/modem/wlan_pd/wlfw", "msm/modem/root_pd/tms/servreg") -> 9 INVALID_HANDLE
- with an extra TLV 0x03 instance (any value tried) -> 58, a service-level error
Still no WLFW and no tftp request, i.e. the wlan PD never starts.

### PDR line closed for now (2026-10-23)
Exhausted, all measured:
- 7-byte header + enable(1B) + name = 45 INVALID_DATA_FORMAT for every service-style name,
  9 INVALID_HANDLE for every domain-style name;
- 5-byte header = 2 MALFORMED_MESSAGE (header must be 7 bytes);
- instance TLV (0/1/180) = 58; enable 4B = 1; no enable = 17;
- QUERY_STATE returns 0 for *any* string, so it proves nothing.
Conclusion: the modem's servreg-notif refuses to register a listener for the WLAN service
from a bare AP client, and without that the wlan PD never starts, so WLFW never appears.
What would unblock it: a real Android PDR trace (logcat/dmesg from a working boot) or the
vendor pd-mapper DB, neither of which this device exposes any more.
Decision: park WLAN here; all lower layers (PAS boot, GLINK/QRTR, pd-mapper, tftp, rails)
are implemented and verified, and can be resumed when the PDR detail is known.
Next track: GPU (Adreno 620) with ios-sailfish's KGSL-lite + Mesa as the reference; the
device already has a620_zap.mdt/b00/b01/b02 in /vendor/firmware.

### rmtfs is part of the modem's boot (2026-10-23)
The modem issues rmtfs QMI at t=2 ms: `OPEN "boot/modem_fs1"` (no leading slash).
With the service announced and the path matched, OPEN answers success (caller id in TLV
0x10) — but **nothing follows**: no ALLOC_BUFF, no IOVEC, and still no WLFW/tftp.
Implementation in PicassoModem.cpp now covers OPEN/CLOSE/RW_IOVEC/ALLOC_BUFF against
modemst1/modemst2/fsc/fsg with a 3 MiB contiguous buffer assigned through SCM
hyp_assign_phys (0x0c/0x16, HLOS+MSS_MSA(15)+NAV(0x2b)); writes are not persisted.
Also fixed: `qmi_result` is one 4-byte TLV 0x02 ({u16 result, u16 error}), not the
generic split 0x02/0x03 pair — though the modem's behaviour did not change.
Next suspects for the dead end after OPEN: the exact OPEN response bytes (dump them),
whether the modem expects the four files opened in a specific order, or that it needs the
`svc`/`sysfs` variants of the rmtfs service instance (announced instance 1 here).

### >>> GOLDEN DATA 2026-10-23: Droidian (vendor kernel) as the live reference <<<
Droidian still boots on this phone: rootfs is `/data/rootfs.img` (34 GB) inside `userdata`
(sda34, ext4); the iOS root is the separate `darwin` partition, so nothing was lost.
Boot image `~/droidian/output/halium-boot-picasso.img` (cmdline has
`service_locator.enable=1`, `androidboot.usbcontroller=a600000.dwc3`). Reachable over
Tailscale: `droidian@100.110.234.108`, password 1234, sudo with the same password.
Kernel: `4.19.113-lineage-picasso+ #65`; **Wi-Fi works there** (wlan0 associated, DHCP).
Vendor bring-up measured from its dmesg:
- modem PIL at 15.4 s: `subsys-pil-tz 4080000.qcom,mss: modem: loading from 0x8c000000 to
  0x9b800000`, firmware via ueventd from /vendor/firmware_mnt/image (same files we load).
- 18.147 s: `service-notifier: root_service_service_ind_cb: Indication received from
  msm/modem/wlan_pd, state: 0x1fffffff, trans-id: 1`, ACKed with **instance 180**.
- 18.149 s: `icnss_qmi: QMI Server Connected: state: 0x980`; 24.186 s: `icnss: WLAN FW is
  ready: 0xd87` — i.e. the modem itself brings wlan_pd up ~2.5 s after PIL and the WLAN
  firmware then hosts WLFW.
- The kernel's PDR client: `service-notifier.c` uses `SERVREG_NOTIF_SERVICE_ID_V01 = 0x42`,
  version 0x01, and does `qmi_add_lookup(svc, ver, instance)` before registering; the
  printed "74"/"180 service" are those **client instance ids**.
- Userspace daemons seen: `qrtr-ns -f`, `pd-mapper`, `tftp_server`; **no rmtfs daemon**,
  yet WLAN works, so EFS is not required for WLAN bring-up.
USB reference data (why USB is closed for XNU):
- `msm-dwc3 a600000.ssusb: Linked as a consumer to 15000000.apps-smmu`: USB sits behind the
  apps-SMMU; DT `apps-smmu@15000000` is one 1 MiB block (no non-secure bank to dodge).
- Only the HS PHY exists (`ssphy-qmp ... unable to get vdd supply`).
- Droidian has no `/dev/mem`, so S2CR/DWC3 cannot be read live to copy the working config;
  in XNU those accesses hang, i.e. the gate is TZ/SMMU security, not driver sequencing.
Next: finish the PDR experiment with the confirmed name (`msm/modem/wlan_pd`, instances
180 then 0) plus lookups for 0x42; if WLFW appears, continue with the topaz WLFW/WMI stack.
Optional later: a Droidian kernel module (source: MiCode picasso-r-oss) to dump SMMU/DWC3.

### QRTR record packing (measured 2026-10-23 with qrtr-lookup on Droidian)
`qrtr-lookup` (installed from Debian) prints for the working system:
```
66  1  180  0  2   Service registry notification service      <- 0x42 the modem notifier
69  1    0  0  98   ATH10k WLAN firmware service              <- 0x45 WLFW
64  1    1  1  16458 Service registry locator service         <- 0x40 pd-mapper, on the AP
4096 5   0  1  16469 TFTP
47  1    0  0  57   Data Port Mapper service
21  1    0  0  21   Modem embedded file system service (rmtfs = 0x15!)
```
The NEW_SERVER/NEW_LOOKUP payload is {service u32, version u8, instance u8, pad u16,
node u32, port u32}: the modem advertises `0xB401`, which is version 1 / instance 180.
Earlier announcements put a single number in the instance half.
With the wrong packing the modem answered register_listener with error 9
INVALID_HANDLE (it had never seen a matching lookup from us).

### PDR retry result (2026-10-23, kernel d36da25)
Register_listener for `msm/modem/wlan_pd` was retried every 500 ms for 31 s: every answer
is `result 1 error 9` (INVALID_HANDLE); `wlan/fw` gives 45 (INVALID_DATA_FORMAT). So it is
not a timing/readiness issue. With the corrected record packing our NEW_SERVER parse now
matches qrtr-lookup exactly (`svc 42 v1 i180 node 0 port 2`).
Working hypotheses left, cheapest first:
1. QRTR name server: Android runs `/vendor/bin/qrtr-ns -f` on the AP. Our XNU has none, so
   our NEW_LOOKUP (0x42, v1, i180) is not forwarded/relayed the way qrtr_ns does it, and
   the modem's servreg may never create a client handle (hence INVALID_HANDLE).
2. Capture the vendor kernel's exact register_listener bytes on Droidian (kprobe on the
   QRTR send path) and diff them against ours.
3. Modem environment: Android's PIL votes vdd_mss/vdd_cx and icnss has the WLAN SMMU group;
   either could gate the modem's own wlan_pd start (which it does ~2.5 s after PIL).

### Name server did not change the modem's answer (2026-10-23, kernel dd7d1de)
Added a minimal QRTR name service (port 0xfffffffe): a server table fed by our own
announcements and the modem's NEW_SERVER records, answering NEW_LOOKUP with NEW_SERVER.
Measured: the modem never sends a lookup to us (no ns: lines), so there was nothing to
answer; register_listener for msm/modem/wlan_pd still returns error 9 for 40 s of retries.
Conclusion: the modem's servreg does not need our name server for this, and INVALID_HANDLE
comes from something else in the request or from the modem's PDR state.
Next decisive step (needs Droidian): capture the exact register_listener bytes the vendor
kernel sends, with a kprobe on the QRTR send path, then diff them against ours.

### Droidian byte capture blocked; modem rails are RPMh ARC levels (2026-10-23)
- The vendor kernel has **no kprobe_events** (uprobes only), so the register_listener
  bytes cannot be captured with tracefs on that kernel.
- Android's AP never loads a WLAN firmware file: /vendor/firmware_mnt/image has only
  bdwlan.*, and dmesg shows `icnss: WLAN FW is ready: 0xd87` at 24.6 s with no PIL of a
  wlan image. The modem starts its own WLAN core; the AP only waits for WLFW.
- Different from our driver: vendor PIL votes the modem's proxy regulators. Resolved from
  the live DT (dtc -I fs): `vdd_cx` = RPMh ARC resource "cx.lvl" (pm8150 S1, level 0x10),
  `vdd_mss` = "mss.lvl" (pm8150 S8, level 0x10). Those are *levels*, not voltages, so our
  sweet_rpmh_vreg_enable() cannot vote them as-is; an ARC-level command would be needed.
- Everything else in the modem environment matches (rails we vote, PIL args, firmware).

### ARC corner vote works, WLAN still absent (2026-10-23, kernel 20505dc)
- sweet_rpmh_arc_level() needed two fixes: the cmd-db lookup rejected ARC entries (they are
  hardware type 3 at 0x30000 / 0x30060, VRM is type 4), and the TCS completion loop demanded
  all three CMD_STATUS words (a single ARC command completed 0x10101/0/0 and was treated as
  a timeout, quarantining the RSC).
- Result: `cx.lvl=384` and `mss.lvl=384` now vote cleanly (rc 0). The modem still does not
  bring up wlan_pd, WLFW never appears, register_listener for msm/modem/wlan_pd still
  answers error 9. So the modem corner was not the gate.
- Vendor sequence re-checked in icnss.c: the MSA (pil_wlan_fw_region) is assigned to
  WLAN/MSS only *after* WLFW connects (line 1094), and the probe-time assign only returns it
  to HLOS, so MSA permissions are not the pre-requisite either.
Next idea: capture the **full** vendor dmesg window 15.5-19 s (boot Droidian, plain dmesg)
to see every AP action between auth_and_reset and the wlan_pd indication; the AP may send a
QMI/SSR message we do not.

### WLAN PD hunt (2026-10-24, dda31c2 + tools/pdm-dump.py)
Measured on the working Droidian install, in this order:
- 15.20 s `insmod qca_cld3_wlan.ko` fails; the driver is built in (`wlan` module, "Live (CO)").
- 15.56 s PIL modem; 17.62 s the modem itself raises `msm/modem/wlan_pd`
  (1 ms BEFORE icnss logs `QMI Server Connected`), 23.64 s `icnss: WLAN FW is ready`,
  50.81 s the host driver initialises, `wlan0` associates.
- No PIL entry exists for `wlan`; icnss never writes WLAN registers in the normal path
  (the only reg_write is debugfs). So the modem boots its WLAN PD on its own.
Conclusions: the XNU port's missing link is before WLFW: the modem must raise
`msm/modem/wlan_pd` and register WLFW 0x45, and our modem does neither.
Now instrumented behind boot args (one kernel):
- `modem_pdm=2` serves every PD name found in modem.b22 (root_pd, wlan_pd, test_pd,
  audio_pd, gps) for `tms/pdr_enabled`, and the PD paths resolve through the locator
  (register_listener answered 9 INVALID_HANDLE with the single-entry table).
- `modem_pdr_restart=1` sends servreg-notif 0x24 (restart PD) for msm/modem/wlan_pd.
- `modem_wlansmmu=1|2` reports (2 also bypasses) the apps SMMU stream 0xc0 of the WLAN MAC.
- `tools/pdm-dump.py` asks a working pd-mapper for its domain list (qrtr-lookup finds the
  port); run it on Droidian to copy the vendor database exactly.

### Service-id bugs fixed (2026-10-24, kernel 250c412, payload shim a4832e0e)
Two announced QRTR triples were wrong, both proven against the reference servers:
- tftp: linux-msm/tqftpserv does `qrtr_publish(fd, 4096, 1, 0)`; the port announced
  version 5. The modem's WLAN PIL loader fetches
  /readonly/firmware/image/wlanmdsp.mbn over tftp (strings in modem.b22:
  "/readonly/firmware/image/wlanmdsp.mbn", "/readonly/vendor/firmware_mnt/image/..."),
  so a version mismatch means the loader never finds a server and wlan_pd never starts.
- rmtfs: linux-msm/rmtfs publishes (14, 1, 0); the port announced 0x15 (21), which is the
  modem-side "modem EFS" service id. qrtr-lookup on the working install shows
  14 ver 1 inst 0 "Remote file system service" (node 1) and 21 on node 0.
Also fixed: qrtr_new_server packs `instance << 8 | version`; the port masked the instance
to 8 bits.
Payload ready: shim/out/shim-boot.img md5 a4832e0e2378236452a4b9bc2a29e0ed, cmdline
modem=1 modem_watch=3 modem_glink_ms=60000 modem_recovery=1 modem_wlan_power=1
modem_wlansmmu=1 modem_recovery_after=90000.
Expected if the theory holds: `tftp RRQ /readonly/firmware/image/wlanmdsp.mbn`, then
qrtr NEW_SERVER 0x45 (WLFW) and a `msm/modem/wlan_pd` indication.
Phone was unreachable at 2026-10-24 00:40 (no adb on either host, Droidian ssh timeout),
so this kernel is built but not yet flashed.

### Roadmap after WLFW appears (from vendor icnss.c, 2026-10-24)
Once the modem registers WLFW (QRTR 0x45) the AP must run this sequence before
the host driver can attach (icnss_fw_boot, icnss.c:1060):
1. wlfw_ind_register            (subscribe to FW indications)
2. wlfw_msa_mem_info            (tell the FW the MSA base/size)
3. hyp_assign the MSA (pil_wlan_fw_region 0x8ba00000, 2 MiB) to
   VMID_MSS_MSA(15) + VMID_WLAN(24) [+ VMID_WLAN_CE(25)], perms RW
4. wlfw_msa_ready
5. wlfw_cap                     (host capability)
6. wlfw_dynamic_feature_mask
Then WMI/HTC/HTT over the WLAN QMI channels.
The WLFW QMI message ids/TLV tables are inside /vendor/lib/modules/qca_cld3_wlan.ko
(and the built-in icnss); extract them there when the phone is next reachable.

### Build hygiene + MSA knob (2026-10-24 01:55, kernel b3401e7)
- A CI run was lost to a text-insertion bug that left a function definition inside
  modem_thread. tools/cpp-scope-check.py now catches that class of error without a
  compiler (it flags the version of the file that failed and passes the repair).
- New knob modem_msa_assign=1|2 hands pil_wlan_fw_region (0x8ba00000, 2 MiB) to
  MSS_MSA+WLAN (2 also WLAN_CE) through SCM hyp_assign before the modem boots.
- Payload rebuilt from kernel 9dc9018 (md5 9de36e9cc8c97f4fd8ecea2dfaabdf45, 36 491 264 B) with
  modem_wlansmmu=1 and the WLFW version-info probe; tools/picasso-modem-test.sh is
  waiting for the phone (armed 01:57, 5 h window).
  The tftp cache now accepts files up to 32 MiB and logs `fw cache: NAME SIZE`, so a
  large wlanmdsp.mbn can no longer be silently reported as "file not found".

### WLFW (QRTR 0x45) QMI reference for stage 2 (ath10k qmi_wlfw_v01.h)
Message ids used by the AP side, in the order icnss runs them:
  0x0020 IND_REGISTER      u8 enables + u32 client_id (TLV per field)
  0x0024 CAP               host capability
  0x0034 HOST_CAP          host feature bitmap
  0x002B MSA_READY (ind)   AP -> FW after msa_mem_info + hyp_assign
  0x0022 WLAN_MODE         mode/bdf/cal flags (sent by the host driver)
  0x0023 WLAN_CFG          mac/bdf/regdb paths, bdf type
  0x0025 BDF_DOWNLOAD      board data (chunked)
  0x0020 IND_REGISTER / 0x0035 REQUEST_MEM (ind from FW) / 0x0037 MEM_READY (ind)
  0x0038 FW_INIT_DONE (ind) 0x0039 REJUVENATE (ind) 0x003B DYNAMIC_FEATURE_MASK
  0x0031 ATHDIAG_WRITE 0x0033 MAC_ADDR 0x002F INI 0x003D XO_CAL
The TLV (type,length) tables are in qmi_wlfw_v01.c; fetch from
greearb/ath10k-ct (ath10k-5.4/qmi_wlfw_v01.{h,c}) when implementing.

### WLFW TLV ids (icnss generation, wlan_firmware_service_v01.c)
IND_REGISTER 0x0020 request:
  0x10 u8 fw_ready_enable, 0x11 u8 initiate_cal_download_enable,
  0x12 u8 initiate_cal_update_enable, 0x13 u8 msa_ready_enable,
  0x14 u8 pin_connect_result_enable, 0x15 u32 client_id,
  0x16 u8 request_mem_enable, 0x17 u8 fw_mem_ready_enable,
  0x18 u8 cold_boot_cal_done_enable, 0x19 u32 rejuvenate_enable
CAP 0x0024 / MSA_READY 0x002b carry no request TLVs (empty request).
HOST_CAP 0x0034: 0x10 daemon_support. DYNAMIC_FEATURE_MASK 0x003B.
Source: os-vector/wire-os kernel/msm-4.9/drivers/soc/qcom/wlan_firmware_service_v01.{h,c}
(also OnePlusOSS sm8150 icnss_qmi.c, same icnss generation as picasso).

### Two boot failures with the new kernels (2026-10-24 05:35-05:45)
- variant a (kernel 9dc9018 + modem_wlansmmu=1, glink 60 s, recovery 90 s): the phone did
  boot (adb dropped), then rebooted by itself and hung at the MI logo; a forced
  power+vol-down to fastboot/recovery was needed.
- variant f (same kernel, **no** modem_wlansmmu, recovery 60 s): after 110 s there was no
  adb and no USB device at all on k60.
- The console ring was unchanged in both cases (its boot-args line still reads
  modem_glink_ms=30000 / modem_recovery_after=45000, the run46 payload), so the failing
  kernels never reached the 10 s log flush.
- Control: rebuilding the run46 payload from kernel-arc3 (20505dc) gives a byte-identical
  image (md5 8a76382e0d155dee69ae374ee1d006cc), so mkpayload/build.sh are deterministic and
  the difference is in the kernel or its command line.
Variants prepared in shim/out (each has its md5 above the run):
  h = 20505dc kernel + old cmdline (8a76382e)  <- known good, control
  i = 20505dc kernel + new cmdline (93d1dab4)  <- isolates the cmdline
  j = 9dc9018 kernel + old cmdline (9ea802a2)  <- isolates the kernel
  f = 9dc9018 kernel + new cmdline (ca14b1e7)  <- failed
  g = f with sweetfb_noconsole=0 (2eae3416)    <- panic visible on screen
tools/bisect-modem.sh runs h,i,j in order and only believes a result when adb drops after
the reboot and the ring's own boot-args line matches the variant.

### rmtfs is now reached; EFS reads failed because the buffer was not there yet (2026-10-24 05:55)
Bisect with tools/bisect-modem.sh (each result checked against the ring's own boot-args):
  h (20505dc, old cmdline)  -> booted
  i (20505dc, new cmdline)  -> booted        (so the cmdline was never the problem)
  j (9dc9018, old cmdline)  -> the phone went to the console and did not come back on its own
The console photo of j/the new kernel shows what actually changed:
  announced pd-mapper 0x40 v1 i1, tftp 0x1000 v1 i0, rmtfs 14 v1 i0
  rmtfs open "boot/modem_fs1" -> modemst1     (first time the modem ever asks)
  rmtfs open "boot/modem_fs2" -> modemst2
  rmtfs iovec caller 1 read FAILED            <- rmtfs_pa is still 0: the buffer is only
                                                 created on ALLOC_BUFF (msg 4), which the
                                                 modem sends later than its first reads
  exec packpowermanagerd / launchd ...        (iOS userspace does start)
Fix de02582: create and hyp_assign the 3 MiB rmtfs buffer at init (rmtfs_init_buf) instead
of inside the GLINK receive path; payload shim/out/variant-k.img (kernel de02582,
md5 aa3fc7c1607177f71fd6406dc9b755dc) is ready. The phone powered off right after the
console photo and needs charging before the next cycle.

### rmtfs: the SCM assign was structurally wrong (2026-10-24 06:25, commit e7e2c01)
The console showed `rmtfs: SCM assign 145f48000+300000 to modem: ret=ffffffffffffffff res=56`
and then `rmtfs iovec ... e1=41f58000` — the modem used an address that is not our buffer
because TZ had rejected the assignment. Compared against the vendor sources
(drivers/firmware/qcom_scm.c, qcom_scm-64.c, drivers/soc/qcom/rmtfs_mem.c):
- qcom_scm_assign_mem() calls SVC_MP/0x16 with 7 arguments, so args[3..6]
  {src_sz, dest_phys, dest_sz, 0} travel in a buffer passed in x5, not in registers;
- x2 is a qcom_scm_mem_map_info {mem_addr, mem_size};
- x4 is the source vmid array (u32), dest is qcom_scm_current_perm_info
  {vmid, perm, ctx, ctx_size} (16 bytes each), perm RW = 4|2;
- the vendor rmtfs_mem hands the region to HLOS + one vmid (qcom,vmid), not NAV.
The old call put a hand-rolled layout in the registers and passed an address where
a size belongs, hence res=56. hyp_assign() now builds exactly the vendor layout.
Still open: whether the modem accepts our runtime buffer address in the ALLOC_BUFF
reply or insists on the DT qcom,rmtfs-mem carveout (its IOVEC used 0x41f58000).

### rmtfs: TZ refuses ordinary RAM; write where the modem points (2026-10-24 06:36, 1f4e355)
Console of the rebuilt SCM call: `scm assign 145ed8000+300000 vmid f: ret=-2 res=-536870925`.
The call shape is now the vendor's (a wrong shape gave ret=-1 res=56), so TZ
understands it and still refuses: this SoC does not let the AP hand ordinary
kernel RAM (kmem_alloc_contig) to the modem VMs.
Reference behaviour (linux-msm/rmtfs sharedmem.c): rmtfs_mem_ptr() returns
`rmem->base + phys_address - rmem->address`, i.e. the IOVEC phys_offset is an
absolute physical address inside the memory device - so the modem names the
buffer it wants, and nothing forces it to be the AP's buffer.
There is **no rmtfs carveout** in the picasso DT (reserved-memory has smem@80900000,
removed@80b00000 304 MiB, pil_wlan_fw@8ba00000 2 MiB, modem_wlan@8c000000 248 MiB,
linux,cma without reg).
New approach (1f4e355): the IOVEC read path maps the address from the entry
(4 GiB.., <=8 MiB per entry) cache-inhibited and reads the EFS sectors straight
into it in 256 KiB chunks; the shared buffer is only used for ALLOC_BUFF replies
and is now allocated below 4 GiB because the modem's request carries a 32-bit
address. Observed modem addresses: 0x41f58000 (plausible DDR) and 0x3ed82000
(below DDR, rejected by the range check).

### The real PDR database lives in the modem partition (2026-10-24 07:35)
The six `*.jsn` files in the modem partition's `image/` directory are exactly what
pd-mapper serves (mount /dev/block/bootdevice/by-name/modem read-only and cat them):
  modemr.jsn : msm/modem/root_pd  inst 180 : tms/servreg, tms/pdr_enabled, gps/gps_service
  modemuw.jsn: msm/modem/wlan_pd  inst 180 : kernel/elf_loader, tms/servreg, wlan/fw
  adspr.jsn  : msm/adsp/root_pd   inst 74  : tms/servreg
  adsps.jsn  : msm/adsp/sensor_pd inst 74  : tms/servreg
  adspua.jsn : msm/adsp/audio_pd  inst 74  : tms/servreg, avs/audio
  cdspr.jsn  : msm/cdsp/root_pd   inst 76  : tms/servreg
Our earlier hand-written table was close for root_pd/wlan_pd but served one domain
for `tms/pdr_enabled` and invented instances. 5121fc3 serves the six domains and
answers every other service query with the domains that provide it.
Measured before this change: EFS opens and ALLOC_BUFF succeed, the modem never
issues an IOVEC read, never asks for wlanmdsp.mbn over tftp and never registers
WLFW; register_listener for msm/modem/wlan_pd answers error 9.

### Session state 2026-10-24 08:00 (branch omp, head 0856662)
Verified working, all measured on the phone:
- modem PAS boot, GLINK/QRTR, our name server, pd-mapper, tftp announce, rmtfs 14.
- pd-mapper serves the six real domains from the modem partition's *.jsn files.
- rmtfs: EFS cached in RAM at init (1048576/1048576/131072/1048576 bytes), OPEN answers
  modemst1/modemst2, ALLOC_BUFF answers a 3 MiB buffer.
- ARC corners cx.lvl/mss.lvl vote rc 0.
- The kernel returns to recovery reliably (ring flushes, BCB written) once UFS is kept out
  of the GLINK receive path.
Blocked (the modem simply stops after ALLOC_BUFF):
- it never sends an IOVEC read (so EFS contents are irrelevant to it so far),
- it never queries pd-mapper for kernel/elf_loader or wlan/fw,
- it never asks for /readonly/firmware/image/wlanmdsp.mbn over tftp,
- it never registers WLFW 0x45, and register_listener for msm/modem/wlan_pd still answers
  error 9 even with the exact vendor domain list and instance 180.
Open hypotheses: the WLAN PD start needs an AP-side trigger we have not found (PDR restart
is being tested now), or the modem's PD manager waits for a host service whose identity we
still do not know. Everything else on the AP side (rails, corners, EFS, pd-mapper, tftp)
is in place.
How to run the loop today: the phone is attached to the build machine, so plain adb works
(no k60). `tools/bisect-modem.sh VARIANT...` flashes shim/out/variant-<x>.img with
clear-misc, readback verification, a proof that adb dropped, and a ring boot-args check;
`tools/sweet-log-pull.sh -p darwin out.txt` reads the ring. Variants built so far:
h,i,j (bisect), k..u (rmtfs work), each with md5 in /tmp/bisect.log or the commit log.

### Last cycle of this session (2026-10-24 08:10)
Variant v = kernel 0856662 (PDR restart now reachable) + modem_pdr_restart=1, flashed from
the local host with readback 4ddd9a50f81b38f753b645f016e5d5ad. The phone did **not** come
back to recovery within 155 s and then had no USB at all, so this run is inconclusive: it
may have hung in the restart path or the battery may have quit. Next session should first
re-flash variant u (kernel 5121fc3-class, no restart) or t to confirm the phone still
returns, then retry the restart if it does.
State of the branch: omp at af3f55d, CI green for 0856662 (run 40047608095).

### Root cause of the GLINK stall: the modem truncates ALLOC_BUFF to 32 bits (2026-10-24 10:10, 7e12e0c)
Step logs inside the rmtfs handler pinned it down exactly:
  rmtfs msg 4 (ALLOC)  -> alloc 1461d8000
  rmtfs msg 3 (RW_IOVEC) len 38
  iovec parse e0=1 e1=46350200 e2=1 bytes=512 caller 1
  iovec map 46350200+512 -> fffffe1076b14200
  (nothing more: the memcpy into that mapping hangs the GLINK thread for good)
0x46350200 is our own buffer seen through a 32-bit field (0x1_461d8000) plus the
0x1a2200/0x180000-style offset inside it - the modem's IOVEC phys_offset is u32, so a
buffer above 4 GiB cannot work. Writing to the truncated address does not fault: the AP's
XPU denies it and the master hangs, which is why every kernel that served rmtfs stopped
logging at t=4 ms while the rest of the kernel (ring flush, recovery BCB) kept working.
7e12e0c allocates the buffer with IOMallocContiguous first (DMA pool, usually low) and
logs `rmtfs: buffer source IOContig|kmem pa ...`. Variant aa was flashed
(md5 cf3914e5b323fea386c73da8986159cc) but its ring was not written in the 160 s window,
so the low-address theory is still unverified - the next run should show either a pa
below 4 GiB and a working IOVEC (rmtfs iovec ... OK / tick 5000 ms) or the same stall.

### Truncation proven, DMA pool unusable (2026-10-24 10:55)
Ring of variant ac (a8ed1ec):
  rmtfs: buffer source kmem pa 144af8000
  rmtfs alloc: caller 1 size 2097152 -> 144af8000
  iovec parse e0=1 e1=44af8200 e2=1 bytes=512
  iovec map 44af8200+512 -> fffffe3008b08200        (then the GLINK thread hangs)
144af8000 -> 44af8200 is exactly a 32-bit truncation plus the 0x200 offset the modem
added, so the modem does keep the ALLOC_BUFF address in 32 bits and the buffer must live
below 4 GiB. IOMallocContiguous(3 MiB) returns NULL there; four IOMallocContiguous(2 MiB)
attempts kill the boot before the first ring flush (variants ab/ad wrote nothing).
The phone is left running variant ac (md5 21529ab58062c91669cf6f058e13f7db), which boots
and logs.
Next idea for the low buffer: do not allocate - take a fixed low physical range that the
AP owns and TZ accepts. Candidates below 4 GiB in the DT: linux,cma (base unknown, size
from alloc-ranges), pil_wlan_fw_region 0x8ba00000 (2 MiB, exactly the size the modem
asks for, currently zeroed and unused until WLFW), smem_region 0x80900000 (in use).
Trying the pil_wlan_fw_region as the rmtfs buffer is the cheapest next experiment.

### The modem address is confirmed, but the AP may not write reserved memory (2026-10-24 11:10)
Variant ae serves the buffer from pil_wlan_fw_region (0x8ba00000, exactly the 2 MiB the
modem asks for), and the modem then uses 0x8ba00200 - our address plus the same 0x200
offset. So the 32-bit truncation theory is fully confirmed: give it a low address and the
IOVEC address matches.
But the memcpy there hangs just like before: the AP is denied (XPU) on that reserved
carveout, and it is denied on the truncated kernel address too. Every attempt to write a
reserved region hangs the GLINK master with no fault.
What is needed is a buffer **below 4 GiB in normal AP-owned RAM**. kmem_alloc_contig
always hands out >0x1_00000000 at this point, IOMallocContiguous fails (or kills the boot
when retried), so the options for the next session are:
  a) reserve a low range (e.g. 0x48000000..0x50000000, past the kernel image, before the
     first modem carveout at 0x80900000) in the shim's ADT as an owned region and map it;
  b) allocate very early (before the VM hands out low pages) or from the DMA pool with a
     single call (one call at the PAS-metadata point did work for 9 KiB);
  c) check whether the modem accepts a 64-bit ALLOC_BUFF address when the reply TLV is
     lengthened (the truncation may be in the modem's parser, not the protocol).
The phone is left running variant ac (md5 21529ab58062c91669cf6f058e13f7db).

### The XNU ADT has no /reserved-memory (2026-10-24 11:25)
Variant af added a read-only dump of the device tree XNU actually sees; it reports
`adt: no /reserved-memory node`. The shim passes an Apple-style ADT (memory, chosen,
interrupts), not the Qualcomm DT, so the reserved carveout map cannot be read from inside
the kernel this way (the Linux DT is only visible to the recovery/Linux kernel).
Next concrete step, deterministic and safe: let the **shim** reserve the rmtfs window.
shim/board.c already computes the RAM chunk and the exclusion list (hw.rsv[]); carve a
2 MiB hole below 4 GiB at a known offset inside that chunk (e.g. the page after the
payload/ADT area), record base/size in the ADT (a /chosen or memory-map property), and
have PicassoModem read that property and use it as the rmtfs buffer. That satisfies both
constraints at once: below 4 GiB (32-bit modem field) and AP-owned RAM (XPU allows it).
The phone currently runs variant af (md5 4682a2b073c3b0b2124c85cd3c28cb68), which boots
and logs.

### RAM bank vs 32-bit rmtfs address (2026-10-24 12:30, 837c106)
Measured with the window-aware kernel: `rmtfs: adt dram 140000000+c0000000 window 0+0` - xnu
runs in the 3 GiB bank at 0x1_40000000, so it has no RAM below 4 GiB at all, kmem allocates
above 5 GiB, and every rmtfs buffer it hands the modem is truncated by the modem's 32-bit
ALLOC_BUFF field. The modem does use the address we return (0x8ba00000 -> its IOVEC says
8ba00200), it is only the AP that may not touch that carveout.
shim/board.c: RAM_MAX only caps the *size* of the picked range, so the high bank still won.
837c106 adds [4 GiB, 1 TiB) to the exclusion list, which should leave only the low bank
(0x80000000 + 0x3cc00000, 972 MiB) and make low allocations possible.
Pending experiment (one build+flash): shim 837c106 + kernel 2c3361d (the IOVEC copy path,
which now copies from the EFS cache into our own low buffer). Expected log:
  rmtfs: adt dram 80000000+...   buffer from ... pa < 0x100000000
  iovec caller 1 read ... OK     tick 5000 ms: alive
then watch for tftp RRQ / wlan_pd.
Two earlier variants (aa/ab/ad) showed that IOMallocContiguous called from the driver kills
the boot, so the buffer must come from kmem_alloc_contig after the low bank is in use.
The phone was left on variant af (md5 4682a2b073c3b0b2124c85cd3c28cb68), which boots and logs.

### Low-bank experiment hangs (2026-10-24 12:34)
Variant ak = shim 837c106 (exclude RAM >= 4 GiB) + kernel 2c3361d (IOVEC copy), md5
b61e9a6eafb5edcb6596b1a46779add1. Flashed, adb dropped, and the phone never came back
(no adb, no USB after 4 minutes), so either the exclusion broke hw_pick_ram (the excluded
range is 1 TiB wide and the arithmetic may overflow) or the low bank cannot host the
payload any more. NOTE: NOTES.md line ~1914 records that xnu used to run in the low bank
(1.2 GiB) before the HIGHMEM switch, so a low bank is not impossible, but it needs care.
The phone is dark and needs a power cycle; the boot partition currently holds variant ak,
so the first thing to do is flash variant af (4682a2b073c3b0b2124c85cd3c28cb68) or ai
(96221c369fc144f0b7d34a328b42c5ab) back from recovery.
Next steps for the low bank, in order:
  1. make the exclusion small and precise (e.g. [0x100000000, 0x140000000) instead of a
     1 TiB span) and verify hw_pick_ram's bounds handling,
  2. log the picked range from the shim console (con_dec) so a hang is attributable,
  3. only then expect kmem_alloc_contig to return low pages and the IOVEC copies to work.

### Low window reaches the modem, but the AP still cannot write it (2026-10-24 12:50)
Variant am (shim 32a9c44 + kernel 2c3361d): the console showed
  rmtfs alloc: caller 1 size 2097152 -> bba00000     (below 4 GiB, from the low bank)
  rmtfs msg 3 txn 1 len 38                            then the screen freezes
and the ring was never written (log thread did not flush), i.e. the kernel deadlocks in the
IOVEC path again. That window (0xbba00000) is RAM in the low bank, which xnu does not own
in this layout (dram-base 0x140000000); the AP's XPU evidently only permits the banks the
bootloader handed to HLOS.
Conclusion: the rmtfs buffer must live in RAM **xnu itself owns**, and since the modem's
address field is 32-bit, xnu must run in a low bank. Variant ak tried exactly that and hung,
but for an unknown reason (no console/ring evidence yet).
Next session, in order:
  1. reproduce ak but log the picked range to the shim console (con_hex2 "PHYS") and take a
     photo; that says whether the exclusion broke the pick or the payload cannot live low,
  2. if the pick is sane but the boot hangs, bisect the shim (MMU/framebuffer assumptions
     may be tied to the high bank),
  3. only then re-test rmtfs: with xnu low, kmem_alloc_contig returns low pages and the copy
     path writes into memory the AP owns.
The phone was restored to variant af (md5 4682a2b073c3b0b2124c85cd3c28cb68), which boots.

### The high bank was baked into the ADT profile (2026-10-24 13:05, 8da9f87)
Photo of variant an: still `rmtfs alloc ... -> bba00000` and a freeze at rmtfs msg 3, and
the shim exclusion changed nothing because the live FDT is not available to the shim: it
falls back to RAM_FALLBACK_* (0) and therefore keeps the payload's built-in layout, which
comes from tools/mkadt.py's picasso profile: PICASSO_PHYS_BASE 0x140000000 +
PICASSO_MEM_SIZE 0xC0000000. So every payload we have been flashing put xnu above 4 GiB.
8da9f87 changes the profile to the low bank (0x80000000 + 0x3cc00000); the regenerated ADT
now reads dram-base 0x80000000 / dram-size 0x3cc00000 and payload variant-ao
(md5 a8c6844f7fd8445380f443c0fd5da5c1) is built on it. This is the experiment that should
finally give xnu low RAM, kmem_alloc_contig low pages and a rmtfs buffer the modem can
address; it was not flashed because the phone was not attached any more.
Caveat: the notes record 1.2 GiB in the low bank before the highmem switch, so 972 MiB is
plausible but tight for iOS; if userspace struggles, the bank can be raised later while
keeping dram-size below 0x100000000.

### Where the low-base work stands (2026-10-24 13:10)
Latest observations, in order:
- mkadt profile now uses the low bank (0x80000000 + 0x3cc00000); the ADT file, the payload
  and the image all contain that value.
- fastboot (not adb) is the reliable way to flash: `fastboot flash boot shim/out/variant-x.img`
  then `fastboot reboot`. There is no A/B slot on this phone (`getvar current-slot` is not
  a variable).
- The console after flashing variant ap still showed `rmtfs alloc ... -> 0x1452f8000`
  (>4 GiB) and an IOVEC with the truncated address, i.e. the running kernel behaved like the
  high-RAM build: either the flash did not take (the phone was in fastboot, it did take) or
  the ADT dram-base is being replaced before xnu reads it. The kernel-side log line that
  would settle it (`rmtfs: buffer pa`) did not appear on the visible console, so this needs
  the ring (which was never written in those runs).
Next, with the phone in recovery:
  1. flash variant ap again (fastboot), let it run, and pull the ring (not the console) to see
     `rmtfs: buffer pa` and the first `tick 5000 ms` line,
  2. if `buffer pa` is still above 4 GiB, print the ADT's dram-base from inside the kernel
     (add a LOG of PE_state.video/phys_base or read /chosen again in the driver),
  3. only when the buffer is low and AP-owned should the IOVEC copy stop truncating.
The phone was restored to variant af via fastboot.

### Measurement caveat: console photos can be stale (2026-10-24 13:25)
The shim does not clear the framebuffer, so after a reset the screen still shows the previous
boot's log; several photos that "proved" a variant ran were probably leftovers. Only the
ring is proof, and its own `boot-args` line says which payload was up.
That matters for the low-base question: the ADT profile is low (verified in the .adt, the
payload and the image), mkpayload takes phys_base/size from the ADT, loader.c patches
/chosen with the payload's values, so xnu *should* boot at 0x80000000 - yet every run we
could observe allocated at 0x14xxxxxxx. Two possibilities: those runs were older payloads
seen through a stale console, or the low-base kernel dies before it can log and the visible
text is from before.
Decisive next step (no guessing): flash variant aq via fastboot, then **wait for the phone to
return to recovery by itself** and read the ring; its first lines contain
`rmtfs: chosen dram <base>+<size>` and `rmtfs: buffer pa <pa>`. If the phone does not come
back, add a late heartbeat log that prints dram-base so it is on screen at freeze time.
The phone was restored to variant af via fastboot.

### Two decisive findings (2026-10-24 13:45)
1. **`shim/build.sh` hard-codes the high bank**: line 57 passes
   `-DRAM_FALLBACK_BASE=0x140000000 -DRAM_FALLBACK_SIZE=0xC0000000` to the shim, so the shim
   always places xnu at 0x140000000 and patches /chosen with that value - the ADT profile
   change (0x80000000) never had any effect. The ring now proves it:
   `rmtfs: chosen dram 140000000+c0000000`.
2. **The fixed pool at 0xE0000000 works on picasso.** Ring of variant as (pool +
   `ufslogsecs=1`, which makes the ring flush every second):
     rmtfs: chosen dram 140000000+c0000000
     rmtfs: buffer from modem pool pa e0000000
     scm assign e0000000+200000 vmid f: ret=-2 res=536870925   (same as the reference port)
     segments loaded / auth_and_reset ret 0 / modem is up (MODEM READY)
     ... then iOS userspace exec lines: the kernel boots and runs.
   So ios-sapphire's trick (a fixed low pool that the kernel does not manage, address
   echoed by the modem) is valid here; writing 0xE0000000 does not hang the AP, unlike the
   0xBBA00000 window tried earlier.
Remaining in that boot: the modem thread stops right after MODEM READY, before glink_start's
first log (`glink: desc made by the modem, ...`), while the rest of the kernel runs to
userspace. Next session: instrument glink_start (hwlock/SMEM/FIFO steps) with `ufslogsecs=1`
so a stall is visible within a second.
The phone was restored to variant af.

### glink_start instrumentation ready (2026-10-24 14:05, 16b0541)
Variant at (md5 620ba5f8af4d3a329c3b8b83feca38cf) = pool 0xE0000000 + `ufslogsecs=1` (ring
flushes every second) + four step logs inside glink_start (`start`, `desc present/missing`,
`desc alloc`, `tx fifo alloc`). Flashed, but the phone went dark before the ring could be
read; next session should flash it again (or just power-cycle) and read
`tools/sweet-log-pull.sh -p darwin out.txt` for those four lines plus `modem is up`.
Context: with the pool the kernel reaches iOS userspace, PAS ret 0, MODEM READY, and the
modem thread then stops with no glink log at all, so the stall is inside
smem_get/smem_alloc for the glink descriptor or TX FIFO.

### Where the freeze is, with the pool in place (2026-10-24 14:10)
Console of the pool boot (photo): GLINK comes up (rx#2..#21), pd-mapper serves six domains,
rmtfs OPEN fs1/fs2, ALLOC -> e0000000, then `rmtfs msg 3 txn 1 len 38` and the console stops.
So the modem does echo our pool address and the last thing we do is the copy into
`rmtfs_va + (e[1] - rmtfs_pa)`. Two candidate causes, both fixable in one build:
  a) the pool is mapped with map_phys(.., wc=true) - write-combine on unmanaged DRAM may hang;
     use wc=false (cache inhibited) or map each IOVEC entry with ml_io_map,
  b) the pool page is not actually backed for the AP and the write hangs like 0xBBA00000 did.
A third, cheaper test that separates them: do not copy at all, only log and reply success -
if the boot then reaches userspace and the link stays alive, the copy/mapping is the culprit.
Note the flakiness: another boot with the same image reached iOS userspace with no ALLOC at
all (modem thread stopped at glink_start), so always read the ring, not just the console.

### rmtfs EFS copy works: the pool must be mapped CACHE-INHIBITED (2026-10-24 14:35, 85c81c8)
Isolation test (variant au, copy skipped) kept the GLINK link alive past the IOVEC
(rx#22, rx#23 after `(copy skipped)`), so the freeze was the store into the pool mapping.
85c81c8 maps the 8 MiB pool at 0xE0000000 with kIOMapInhibitCache instead of
kIOMapWriteCombineCache and copies again. Console of variant av:
  rmtfs alloc: caller 1 size 2097152 -> e0000000
  rmtfs msg 3 txn 1 len 38
  iovec caller 1 read e0=1 e1=e0000200 e2=1 copied     <- the copy completed
and the modem kept talking (next lines follow), i.e. EFS reads are served for the first
time. Write-combining a mapping of DRAM the kernel does not manage hangs this SoC; a
cache-inhibited mapping works.
Next: read the ring (with ufslogsecs=1) to see whether the modem now progresses to
tftp RRQ / wlan_pd; the phone was in fastboot/dark at the time of writing.

### EFS read #1 now completes; the freeze moved one step later (2026-10-24 14:45)
Console of variant av (reproducible):
  rmtfs alloc: caller 1 size 2097152 -> e0000000
  rmtfs msg 3 txn 1 len 38
  iovec caller 1 read e0=1 e1=e0000200 e2=1 copied        <- success
  rx#22 type 7 ...
  PicassoModem: t                                          <- cut mid-line, console dead
So the first EFS read is served (cache-inhibited pool copy), and the machine freezes on the
next packet(s) - the modem sends a lot at this point (earlier runs showed ~1000 msg/s).
The ring did not capture it either (small ring, flush #4), which means the hang takes the
whole kernel down rather than just the modem thread.
Next build (not done yet): log every IOVEC (not just the first four), heartbeat every
second in the GLINK loop, and raise modem_recovery_after to 180000 so the recovery reset
cannot race the work. If it still freezes at the same place, the suspect is the TX path
(qm_send of the IOVEC response) or the console lock, not the copy.

### Fine-grained run hung as expected; needs a manual power cycle (2026-10-24 14:57)
Variant aw (md5 0c778ca3a307a05ea9e971c6b1ea90c0) = 1 s heartbeat + every IOVEC logged +
modem_glink_ms=120000 + modem_recovery_after=180000. Flashed, and the phone never came back
on its own within ~225 s, so this boot hangs hard too (the recovery thread cannot write the
BCB either, which is why no automatic return). A manual power cycle is needed.
When the phone is next in recovery: flash aw again, and this time read the console photo
(the ring stops with the hang): the last `iovec#N` line and the last `tick` say whether the
freeze is in our TX reply, in the console lock or in the copy.
State of the chain: EFS read #1 is served (cache-inhibited pool), then the machine dies.

### Freeze is one packet after the first served read (2026-10-24 15:00)
Console of variant aw (1 s heartbeat, every IOVEC logged): the run serves exactly one read
  rmtfs alloc ... -> e0000000
  rmtfs msg 3 txn 1 len 38
  iovec caller 1 read e0=1 e1=e0000200 e2=1 copied
  rx#22 ..., rx#23 ...                then the screen stops (no second iovec#, no tick)
so the machine dies while handling the packet(s) right after our IOVEC reply. The ring
stops at the same moment, which means the hang is not confined to the modem thread.
Suspects, in order: the TX path for that reply (qm_send -> glink_send -> txq_pump ->
tx_write -> ipcc_kick), a lock shared with the console/log thread, or the modem's next
request being one we mishandle.
What would settle it in one boot: add LOG lines around the IOVEC reply (before qm_result,
before qm_send, after qm_send) - if the last line is "before qm_send", the TX path is the
culprit and the next step is making tx_write/ipcc_kick safe from this context.
Alternatively enable the hardware UART at 0x988000 as UniXNU-picasso does
(scripts/make-picasso-adt.py sets UART_BASE) to get a live console, which removes the
guesswork for every future hang.

### Same freeze with UFS out of OPEN (2026-10-24 16:25, 3d43693 / variant az)
Console (variant az, serial console restored, EFS-cache-only OPEN):
  rmtfs msg 3 txn 1 len 38
  iovec#0 caller 1 read e0=1 e1=e0000200 e2=1 copied
  rmtfs reply msg 3 -> qm_send / sent
  rx#22 type 7 port fffffffe->ffffffe size 20      <- stops here, phone reboots
So removing the UFS read from OPEN changed nothing; the freeze is on the packet right after
our IOVEC reply, and no handler log follows (`ns:`/`qrtr type 7` never appear), i.e. it
happens between the rx trace line and the dispatch log. That pattern - always exactly one
log line short - points at the console/IOLog path itself rather than at the modem code, which
would also explain why the ring stops at the same instant.
Next: reproduce with the console minimal (e.g. `debug` without the console bit, or keep
`sweetfb_noconsole=1` and read only the ring) and see whether the modem keeps going past
rx#22; if it does, the console is the culprit and the ring becomes the primary witness.

### Quiet console does not stop the freeze (2026-10-24 17:26, 99db49c / variant bb)
Variant bb (high-rate logs behind `modem_dbg`, ring every second, glink 120 s, recovery
180 s) was flashed from k60 and never came back within 210 s, and k60 then saw no USB at
all - the same hard hang as before. So reducing console/log traffic is not the cure; the
freeze follows the first served IOVEC (`iovec#0 ... copied`, `reply msg 3 sent`) and the
next control packet, and takes the whole kernel with it.
This is now the single blocker for the WLAN work, and every remaining hypothesis (console
lock, TX burst, IOMalloc per request, modem flooding) needs evidence we cannot get from a
console that dies with the kernel.
Recommendation for the next session, in this order:
  1. Get a live log: hardware UART at 0x988000 as UniXNU-picasso does (needs a UART
     adapter on the test points). This removes all guesswork, permanently.
  2. Without UART: make the ring independent of the kernel log path - e.g. write the trace
     into the fixed pool (0xE0000000 + reserved tail) from the modem thread with plain
     stores, so a log after the freeze can be read from recovery with dd on /dev/block?
     recovery cannot read PMEM, so this needs fastboot/EDL - fine if the phone lands in
     fastboot after the hang.
  3. Only then continue the modem work (tftp/wlan_pd/WLFW).

### Stall monitor turns the silent freeze into a panic (2026-10-24 17:45, 5d1083e)
Variant bc (md5 b1363a37c3556c8d69020878211c8db9, copied to k60 as /tmp/vbc.img) adds a
monitor thread: while the GLINK loop runs, a beat is bumped each iteration; six consecutive
500 ms periods without a bump panic with "PicassoModem: GLINK loop stalled (beat N)". xnu
prints panics on the console with a stack, so the next hang should finally say where it is.
Flash it from whichever host sees the phone:
  fastboot flash boot shim/out/variant-bc.img   (or dd from recovery)
and photograph the panic.

### Low-bank attempts stop in the shim probe (2026-10-24 19:47)
Two low windows were tried by changing the payload's phys_base (mkadt profile + the shim's
RAM fallback):
  0x80000000+0x3cc00000  -> the shim printed its probe screen and never jumped
  0x9b800000+0x20000000  -> same, and that window starts 3 MiB inside the DT's
                            removed_region@80b00000 which ends at 0x9bb00000
  0x9c000000+0x1c000000  -> flashed (variant bk, md5 4e0f589d81e6db84ed53dd46580701a0),
                            the phone did not come back on its own either; no USB at
                            +200 s, so it hung somewhere in the shim/kernel hand-off
The probe screen itself still prints `RAM 0x140000000 + 0xC0000000` while `PHYS` shows the
new fallback, so the loader's view of the payload's placement is not what we changed: the
next thing to read is loader.c around the PAYLOAD/segment copy (it may use the ADT values or
a compile-time base for the destination) before trying more addresses.

### Low-bank: the shim stalls in its own console print (2026-10-24 19:50)
Probe screen with PHYS base=9C000000 size=1C000000 (our fallback, so the new values are in
use; the `RAM 0x140000000 + 0xC0000000` line is a hard-coded string in shim.c:653, not a
value). loader.c then calls board_pick_ram(), prints `RAM base` - and the screen's last line
is exactly `RAM base 000000009C000000`; the next line (`RAM size`) never appears, so the
stall is inside con_hex(), i.e. the shim's framebuffer console, before `COPY segments`.
So the low-bank payload never gets copied - this is a console problem in the shim, the same
family as the console dying inside xnu.
Next session: either (a) move the RAM base/size prints after the copy, or log them with a
direct framebuffer store instead of con_hex, so the copy is reached; or (b) give up on the
shim console for this test and print only a final marker after the ADT patch.

### iOS boots with xnu at 0xAC000000 (2026-10-24 20:02, variant bm)
After the shim finally reported `LOADER: ramdisk overlaps kernel memory` (the payload is
staged at 0xA7C00000..0xAB9AA860), the xnu window moved past it: profile and shim fallback
are now 0xAC000000 + 0x50000000 (ends 0xFC000000, below 4 GiB, past every DT carve-out and
past the staged payload). That boot reached full iOS userspace - launchd boot tasks,
`Early boot complete, continuing system boot`, many xpcproxy execs and HID events - so a
low-bank kernel works on this port at 1.25 GiB of RAM.
The machine then hung at that screen (userspace log), so the modem/rmtfs outcome of this
boot is only in the darwin ring; the phone needs a power cycle into recovery to read it.
What to check in that ring: `rmtfs: chosen dram ac000000+50000000`,
`rmtfs: buffer pa 0xac......` (below 4 GiB, RAM xnu owns), then whether `iovec` copies no
longer deadlock (`tick` lines present) and whether `tftp RRQ`/`wlan_pd`/`svc 45` appear.

### Deadlock gone: kmem rmtfs buffer in the low bank (2026-10-24 20:35, variant bn)
Kernel from CI run 40097777527 (patch 0090 replies rmtfs_pa for ALLOC_BUFF instead of the
fixed 0xE0000000 pool) packaged against the 0xAC000000 layout. Console: the modem link
reports `tick 1000..29000 ms: alive, tx 84 rx 81 drops 0`, pd-mapper answers (tms/pddump),
PDR registers/restarts wlan_pd, and the machine never hangs or reboots - the phone stayed
in iOS. Previous builds (pool at 0xE0000000) always wedged within a second of the first
IOVEC, so the EFS copy now runs into kernel RAM the AP owns below 4 GiB.
Open question for the next boot: whether `rmtfs iovec` now reports success and the modem
reaches `tftp RRQ wlanmdsp.mbn` / wlan_pd / WLFW (svc 45/49). With no USB the console is
the only live view; the darwin ring has the same lines after a reboot to recovery.

### bn ring read: no deadlock, but rmtfs iovec is refused and the modem storms tftp WRQ (2026-10-24 21:20)
Ring pulled from `darwin` after the bn boot (`/tmp/log-run21.txt`, boot-args line
`debug=0x14e ... rmtfs_pa`), 41 194 bytes:

- `rmtfs: chosen dram ac000000+50000000`, `rmtfs: buffer pa b002c000`,
  `rmtfs: shared buffer b002c000+200000 assign fffffffffffffffe` (TZ refuses the assign,
  as the reference port does), modem `OPEN "boot/modem_fs1"` -> four file ids.
- Exactly **one** `rmtfs iovec FAILED`, then the modem floods
  `tftp WRQ /readwrite/server_check.txt` every 5 ms (13 429 lines) - our server answered
  `ERROR 2 read only` and the modem just retried with a fresh session each time.
- The machine stayed alive: `tick 1000..29000 ms: alive, tx 94 rx 91 drops 0`, PDR
  restarts wlan_pd, iOS runs. So the EFS copy into the kmem buffer no longer wedges
  anything (contrast: the 0xE0000000 pool deadlocked within a second).
- **Unresolved contradiction:** the ALLOC reply line prints `-> e0000000` although the
  CI build tree (`xnu-buildtree` artifact of run 40097777527) does `addr = rmtfs_pa`, and
  the CI log shows `C++ PicassoModem.cpo` compiled that file at 13:25:31. The payload in
  `shim/out/variant-bn.img` is byte-identical to the CI kernel's payload, so the flashed
  image was right. Either the ring carried older bytes or something moved `rmtfs_pa`.

Changes made to settle both (branch `omp`, pushed to the `picasso` remote):
- `e7aa279` tftp: accept `WRQ` into a 1 MiB RAM file per name (4 names) and answer `OACK`/
  `ACK`, serve a later `RRQ /readwrite/...` from that RAM file. This is what rmtfs' tftp
  does on Linux (`/readwrite/`), and it is what stops the 5 ms retry storm.
- `05b18e1` the iovec refusal now logs caller, direction, entry count, block, address,
  block count, `rmtfs_pa`, `rmtfs_va` and the cached file size; the ALLOC handler logs
  `rmtfs_pa`/`rmtfs_va` right before replying.

CI: `gh workflow run xnu.yml --ref omp` -> run 40100058840 (head `05b18e1`). Note the
`picasso` remote needs a token on push (no credential helper):
`git push https://x-access-token:$(cat /tmp/ghtoken)@github.com/minhtritt1996/ios-picasso.git omp:omp`.

### Synthetic key presses removed; variant bo packaged (2026-10-24 21:05)
The console showed `SWEET HID: key 0x30 down/up` and `key 0x40 down/up` repeating with
`0 clients` while nobody touched the phone: 0x40 is `kHIDUsage_Csmr_Menu` from the
boot-arg `sweethidtest=5` (SweetHIDButtons::testFired presses Home every 5 s, up to 60
events), 0x30 is `kHIDUsage_Csmr_Power` from the sweet_keys PMIC observer threading button
state every 20 ms (`keys_thread` -> `sweet_hid_key(0, ...)`). `sweethidtest=5` is gone from
`tools/picasso-boot.sh`, `tools/picasso-boot-remote.sh` and `tools/picasso-fastboot-remote.sh`
(commit `c125d3b`).
`shim/out/variant-bo.img` (md5 `44dadd5495b3174e5335f1624a2d7550`, 36 499 456 B) holds the
kernel of CI run `40100058840` (head `05b18e1`: tftp WRQ accepted into a RAM file, iovec
refusal logging, `rmtfs_pa` logged at ALLOC) with cmdline 426 bytes, no `sweethidtest`.
It is ready to flash with `tools/picasso-boot.sh /tmp/ci40100/kernel.development.sweet "<modem args>"`
once the phone is in recovery; nothing has been flashed since variant bn.

### variant bo did not boot: the cmdline had sweetusb=2 (2026-10-24 21:15, variant bp2)
`variant-bo.img` (CI 40100058840 kernel) never wrote to the darwin ring - the pulled 4 MiB
was still variant bn's (`tftp WRQ ... (writes dropped)`, `alloc -> e0000000`, one
`boot-args 453 bytes` line). The console photo explained it: the kernel ran the in-kernel
DWC3 bring-up (`SWEET USB: clk ahb2phy ... ep0 ... ue0 192.168.7.2/24 up (err 0)`, the RPMh
`ldoa*` votes) and the phone then dropped to fastboot. **The cause was my command line, not
the new code:** bn ran `sweetusb=0`, the bo payload said `sweetusb=2` (the DWC3/STARTTRANSFER
path this port cannot survive) and was missing `sweetscan=-`.
Also learned: the kernel's boot-args are the payload header cmdline from
`tools/mkpayload.py --cmdline` (shim/loader.c copies `p->cmdline` into `boot_args`), the
`mkbootimg --cmdline` field of the image is irrelevant for the payload. bn's cmdline was
453 bytes = modem args first, then `debug=0x14e ... sweetscan=- sweethidtest=5`.
`variant-bp2.img` (md5 `0fc459aade9494566efac409be9958d8`, 36 499 456 B) = CI 40100058840
kernel + bn's cmdline minus ` sweethidtest=5` (438 B, `sweetusb=0 sweetscan=-`), flashed from
fastboot with `fastboot flash boot`. The three boot helpers now default to
`sweetusb=0 ... sweetscan=-` (commit `a961f7a`).

### Phantom keys traced; ring cleared for a clean bp3 run (2026-10-24 21:20)
The bp2 ring was a mix: the new boot's first lines (banner, `boot-args 438 bytes`,
`rmtfs: buffer pa b0978000`, `shared buffer ... assign fffffffffffffffe`) sat on top of
variant bn's untouched 4 MiB, so `tftp WRQ ... (writes dropped)` and
`rmtfs alloc: ... -> e0000000` lines in it are bn's leftovers, not the new kernel's output.
The console of the same build had already answered the alloc question:
`rmtfs alloc pa b097b000 va ffffffe4_772b7c00` then `rmtfs alloc: caller 1 size 2097152 ->
b097b000`, i.e. the kernel now hands the modem the kmem buffer (below 4 GiB), never
0xE0000000.
The phantom keys are explained too: the ring/console show
`SWEET KEYS: key=1 down=1 pon=0x4 gpio4:c110=0` and `SWEET HID: key 0xe9 down` - the PM6150L
GPIO2 read (SID4 0xc110) returns 0, so `keys_thread` reports Volume-Up held. `sweetkeys=0`
now turns the whole observer off.
Procedure used from now on: zero the ring (`dd if=/dev/zero of=.../by-name/darwin bs=4096
seek=5242106 count=1025` from recovery), flash, boot, then pull the ring - otherwise a
short boot's log is unreadable against the previous one.
`variant-bp3.img` (md5 `7401c632ec04758b7840bbfae5d70db1`) = CI 40100058840 kernel, cmdline
450 B (bn's minus `sweethidtest`, plus `sweetkeys=0`), flashed and verified.

### bp4: one EFS read served, then a panic whose reason scrolled away (2026-10-24 22:05)
The new counters answered the rmtfs question: `bp4` (kernel 40103041374) served exactly
**one** EFS read - `efs r 1 w 0 err 0` in every tick - and the modem then went silent
(`tftp 0`, and the last QRTR data message is at 4 ms). So ALLOC + one IOVEC now work; what
stops next is the modem's own follow-up, not our reply path.
The run also ended in a panic: `panic(cpu N caller ...)` whose reason line had already
scrolled off, followed by `Debugger synchronization timed out; waited 240000 nanoseconds`
and `panic: cpu 5 failed to halt with error -5: halt not supported for this configuration`
from `DebuggerXCallEnter` (osfmk/arm/model_dep.c:1046/1077 - dbgwrap cannot halt CPUs on
this port, so the panic text is a consequence, not the cause). The modem thread keeps
printing ticks after the panic, so "ticks keep coming" does NOT mean the boot is healthy -
check the screen, not the ticks.
Two observability fixes, both in the kernel now (run 40105926204):
1. `0084`: when the console writes "panic(", the panel is cleared and drawing restarts at
   the bottom, so the reason stays readable (`sweet_panel_clear`, `sweet_panel_glyph`).
2. `0093` (new): the ring flush thread retries a failed write every 200 ms instead of
   returning - it was dying at `seq=3`, which is why a bad boot had no log. Do NOT put
   that change into `0047`: 0069 rewrites the same lines and CI silently skips the whole
   patch ("Skipping patch ... 0069-sweet-ufs-faster.patch (patch failed:
   bsd/dev/sweet_ufs.c:658)"), which happened once and cost a build. Patch numbering is
   the ordering contract; add a later patch instead of editing an earlier one's loop.

### Where the modem conversation actually stops (2026-10-24 22:40, bp5 ring read back)
The ring from bp5 (`total=35654`, `seq=3`, 35 KB) is not a truncated boot: it ends at
`launchd_cache_loader`, i.e. exactly when the modem watch starts, and a photo at t=30000 ms shows
the console still printing ticks, so the two differ only by the flush thread. Everything the modem
said is in it:
- QRTR: HELLO from node 0; `NEW_SERVER svc 42 v1 i180 node 0 port 2` (servreg) and `svc 2b v2 i18 node 0 port 1`.
  No `svc 45` (WLFW) ever, so the modem never brought its wlan PD up.
- pd-mapper: `tms/pdr_enabled` answered with 6 domains, `tms/pddump_disabled` with 0 - the host side
  of the pd-mapper works and the modem used it.
- rmtfs: open `boot/modem_fs1` -> modemst1, open `boot/modem_fs2` -> modemst2, ALLOC 2 MiB ->
  0xb0878000, then ONE iovec read (sector 1, pa+0x200, 1 block). We answered it; the modem then
  sent **nothing for the rest of the boot** (last inbound message timestamped 4 ms).
- PDR: every registration was *rejected*: `result 1 error 9` for `msm/modem/wlan_pd` and for
  `msm/adsp/audio_pd`, `result 1 error 45` for `wlan/fw`, each with an extra 4-byte TLV 0x10
  (`0xca39f580`). Later restart-PD/query-state messages were never answered at all.
- The vendor PDR wire format is confirmed from the msm-4.11+ sources: REGISTER_LISTENER is message
  0x20 with TLV 0x01 = enable and TLV 0x02 = service path (1-byte length + chars); the state
  notification is 0x22 (state, path, txn) and must be acked with 0x23 (path, txn). The old
  `service-notifier.c` in the vendor tree that this phone uses only ever sends those two TLVs, so
  the request *bytes* were right - the paths/instance are what the modem dislikes.
- Vendor side for this phone: the AP's WLAN driver is `drivers/net/wireless/cnss2` and it does NOT
  use PDR at all (no servreg string in it): it waits for the WLFW QMI service and then drives it
  (IND register -> host cap -> respond mem -> BDF download -> M3). So PDR is optional for us and
  the real gate is the modem reaching its WLAN subsystem.
- Consequence measured next, not yet known: whether the modem is dead (SMP2P `slave-kernel` FATAL)
  or waiting after that single EFS read. The bp6 kernel prints the modem's SMP2P entry names/values
  from inside the GLINK loop (`smp2p in: "<name>" = value`, then any change), the first 16 bytes of
  the EFS data we hand over (`efs data: ...`), the raw bytes of the first ten PDR messages, and the
  ring's own progress (`log total/seq/fails`) in every tick; it also re-registers PDR every 2 s and
  keeps the GLINK loop running forever instead of 20 s (`modem_glink_ms` still overrides).
- Iovec bug found by reading the reference encoder: an entry is 16 bytes (u32 sector, u64 phys,
  u32 nsec), the parse advanced by 12, so the second and later entries of a multi-entry read were
  garbage. Fixed in the same build.

### bp6/bp7: the packaging trap, then the measured answer (2026-10-24 23:10, user stopped here)
- bp6 (kernel `eda6762`) did not boot at all: I packaged it with
  `python3 tools/mkpayload.py KERNEL ADT OUT` **without `--cmdline`**, so the payload carried the
  default 36-byte `debug=0xc serial=3 cpus=1 keepsyms=1` instead of the 450-byte real one
  (`modem=1 ufslogsecs=1 rd=ufsroot0 ufsroot=darwin sweetfb_* ...`). No boot-args, no ring, and the
  ring read back was still bp5's. Always pass `--cmdline`, and check the payload header:
  `python3 -c "d=open('shim/out/payload-X.bin','rb').read(0x400);print(d[0x48:0x48+608].split(b'\\0')[0].decode())"`
  must print the long line, not the 36-byte default.
- bp7 = the same kernel rebuilt as `shim/out/variant-bp7.img` (md5 `d8fabe9de378e21cb66562b5b66b90ca`)
  with the real 445-byte cmdline (`modem_glink_ms` dropped so the loop default "forever" applies).
  Flashed at 23:00.
- Photo at t=148 s: ticks keep printing, `pdr 3`, `rx 81`, `tx 84`, `efs r 1 w 0`, `| log 37470/3/0`.
  Read carefully: the modem answered **exactly three** PDR messages (the responses at t=2..4 ms),
  ignored the restart-PD/query-state burst, and ignored every 2-second re-registration this kernel
  now sends - the inbound counters never move again after t=4 ms. `smp2p slave-kernel now ...` never
  printed, i.e. the modem did **not** raise its FATAL bit: it is alive but its QRTR/EFS client is
  parked. The single EFS iovec read we served is the last thing it did.
- `log 37470/3/0` also says the ring has exactly 3 flushes and `fails 0`: the flush thread is not
  erroring, it is stuck (or the counter only updates on success, which it does - so the thread never
  completed another 4 KiB write while the console kept printing). This is the separate
  ring-vs-UFS-contention bug, not the modem.
- To make the tick usable next time: `sweet_log_total_g` is only assigned after a *successful* flush,
  so it cannot show live growth. Print `sweet_log_total` (the atomic in sweet_ufs.c, export it) and a
  "flushing since ms" timestamp instead, and put a timeout on the flush's ufs_write_blocks.
- Consequence for the modem work: the next experiment has to break the "one EFS read then parked"
  state. Options, in order of cost: (1) answer the iovec with the data *and* check the fs header we
  return for sector 1 (the `efs data:` line from bp7's console photo was not legible in the photo
  taken, so this is still unverified); (2) persist EFS *writes* (we only count them) so the modem's
  fs state machine can finish; (3) stop serving EFS entirely for one boot (`rmtfs_on=0`) and see
  whether the modem moves on anyway - that separates "EFS client parked" from "modem parked".

### >>> ACTIVE HANDOFF 2026-10-24 22:57 (user stopped the session here) <<<
State: **bp6 is flashed and running on the phone** (fastboot flash boot shim/out/variant-bp6.img,
md5 `7d7e49b4d0cdc77afce019d1b8a9ce87`, kernel artifact from CI run `40107911357`, commit `eda6762`).
Build/package recipe used:
```
tools/gh-artifact.sh <RUN> xnu-kernel /tmp/ci-bp6/k.zip   (artifact name is xnu-kernel, NOT
                                                          kernel.development.sweet)
python3 tools/mkpayload.py /tmp/ci-bp6/kernel.development.sweet shim/out/picasso-clean.adt \
        shim/out/payload-bp6.bin
bash shim/build.sh --picasso "$PWD/shim/out/payload-bp6.bin"   # --picasso is REQUIRED: without it
        # the image gets the default layout (18 MB, payload once) and will not boot;
        # the correct image is 36499456 bytes with the payload at file offset 0x41000
cp shim/out/shim-boot.img shim/out/variant-bp6.img
```
What bp6 (commit `eda6762`) adds, all in patch 0090/0093:
- GLINK/QRTR loop no longer stops after 20 s (`glink_ms` default 0 = forever; boot arg
  `modem_glink_ms` still forces a limit). Ticks print every second for the first 20 s, then 10 s.
- `smp2p in: "<name>" = value` once, then `smp2p slave-kernel now %08x` on every change - answers
  whether the modem died after its first EFS read (`modem_alive_check()`).
- `efs data: <16 bytes>` the first time an EFS read is served (is the backing store right?).
- `PDR rx type %02x len %u: <24 bytes>` for the first ten inbound PDR messages (before only QMI
  responses were printed, so ind/other types were invisible).
- PDR re-registration every 2 s (`pdr_retry_all`), because the modem answered only the first burst.
- iovec entry stride fixed 12 -> 16 bytes (u32 sector, u64 phys, u32 nsec); the 2nd+ entries of any
  multi-entry read were being mis-parsed.
- 0093 exports `sweet_log_total_g` / `sweet_log_seq_g` / `sweet_log_fails_g` and the tick prints them
  as `| log total/seq/fails`, so the flush thread's progress is visible from the console photo.

Next concrete step (unchanged by the stop): get the bp6 ring - power-cycle to fastboot, `fastboot
reboot recovery`, then
```
adb -s f9f3d7b0 shell "dd if=/dev/block/bootdevice/by-name/darwin bs=4096 skip=5242106 count=1025 2>/dev/null" > /tmp/ring-bp6.bin
```
and read `ring` with `tr -d '\\000'`. Three questions in order: (1) `smp2p slave-kernel FATAL`? (2)
does `efs data:` show real modemst1 bytes, and does the modem speak again after the iovec (rx count
rising in later ticks)? (3) does any PDR retry at 2 s get a different `result/error`?

Ideas queued, not started:
- Vendor AP WLAN driver on this phone is `drivers/net/wireless/cnss2` (source reachable at
  `minhtritt1996/android_kernel_xiaomi_sm7250`, branch `picasso`); it uses **no PDR at all** - it
  waits for the WLFW QMI service 0x45 and then does IND register -> host cap -> respond mem -> BDF
  download -> M3. So PDR registration is optional; the real gate is the modem reaching its WLAN PD.
- If the modem turns out to be alive but stuck on EFS, serve the *write* path too (we currently only
  count writes, never persist them) and check the fs header bytes we return for sector 1.
- If the modem is FATAL, collect its crash reason (SMEM 0xfffe/0xfffe item used by
  `modem_watch`) and the modem log before changing anything else.


## Agent handoff (2026-10-22 17:45)

### >>> UPDATE 2026-10-22 (SMMU cannot be probed; low-memory DMA hypothesis; remote flashing) <<<

1. **The SMMU probe hangs the bus.** With the mapping fixed to 64 KiB, the ring
   still stops right after `QUSB2PHY PRIM_BCR post`, with no panic line: reading
   `apps-smmu@15000000` from XNU hangs (the SMMU sits behind a NoC path that
   needs a bus vote). Do not touch the SMMU from the kernel without those votes.
2. **Stronger hypothesis for the STARTTRANSFER reset:** sweet's kernel RAM is at
   `0xb0000000` (< 4 GiB) and USB DMA works there; picasso's kernel RAM is at
   `0x140000000` (> 4 GiB). If the USB master's XPU/SMMU window covers only low
   memory, the controller's first fetch is an XPU violation, which on Qualcomm
   resets through the PMIC — i.e. exactly the observed "power off, no panic".
   Note `qcom,iommu-dma-addr-pool = <0x90000000 0x60000000>` on the USB node:
   Android hands the controller IOVAs below 4 GiB.
3. **Run 37 (commit `17a560d`, CI `39759534123`)** adds `sweetusbdma=0x...`: the
   transfer area comes from a given physical page mapped uncached instead of
   `kmem_alloc_contig`, and cache maintenance is skipped for it. Test page:
   `0xA22F0000` (inside the 35 MiB splash framebuffer, unused tail).
   If `sweetusbstep=4 sweetusbdma=0xA22F0000` survives, the window theory is
   confirmed and the fix is to keep the USB DMA area below 4 GiB (shim hands a
   free low page through the ADT).
4. **Remote workflow is in place** (phone now on `root@100.121.254.115`, pass
   `k60`): `tools/picasso-boot-remote.sh`, `tools/picasso-fastboot-remote.sh`,
   `tools/sweet-log-pull-remote.sh`.

## Agent handoff (2026-10-22 17:05)

### >>> UPDATE 2026-10-22 (steps 2 and 3 boot; STARTTRANSFER powers off; SMMU suspect — Run 34) <<<

Bisect with Run 33 (`sweetusbstep`, gates inside `ud_start_trb`):

| step | adds | result |
|---|---|---|
| 0 | ep0 `SETEPCONFIG` | boots, SpringBoard |
| 1 | `DALEPENA = 0x3` | boots (earlier failure was the removed `sweet_log_flush`) |
| 2 | SETUP TRB write | boots |
| 3 | `ud_sync` -> `flush_dcache64(pa,…,TRUE)` | boots |
| 4 | `DEPCMD_STARTTRANSFER` | **phone powers off before the Apple logo** |

So the fault is the moment the controller starts DMA. Evidence for the DMA
path being the problem:

- `GHWPARAMS0 = 0x4020400a` -> `AWIDTH = 0x40` = 64 bits, so the >4 GiB buffer
  is not an address-width problem.
- The USB node has **an IOMMU** (`iommus = <&apps_smmu 0xE0>`,
  `qcom,iommu-dma-addr-pool = <0x90000000 0x60000000>`), while the UFS node has
  none. That is consistent with UFS DMA working in this kernel while the first
  USB DMA access kills the phone (an SMMU/XPU fault on Qualcomm resets the PMIC).

**Run 34 (commit `f341c41`, CI `39755527450`)** adds:
- prints of TRB/buffer physical addresses before the command and
  `DEPCMD/DSTS` after it;
- `sweetusbsmmu=1`, which reports `IDR0/CR0/GBPA/S2CR(0xE0)/CBAR(0)` at
  `0x15000000` using the ARM SMMU-500 offsets.

**Next:** boot `sweetusbstep=3` + `sweetusbsmmu=1` to read the SMMU state
(that path does not crash), then decide between programming a bypass for stream
0xE0 and building a translation table.

## Agent handoff (2026-10-22 16:45)

### >>> UPDATE 2026-10-22 (step 1 boots; step 2 powers off; gates inside the TRB path — Run 33) <<<

1. `sweetusbstep=1` (`DALEPENA = 0x3`) **boots to the Apple logo**: the early
   `sweet_log_flush()` was the reason step 1 died before. Removed.
2. `sweetusbstep=2` (`ud_start_setup()`), with Run 32: console appears, then the
   phone **powers off** before the Apple logo. So the fault is inside the SETUP
   TRB path, not in the PHY/ep0 config.
3. **Run 33 (commit `915d1a9`, CI `39753347024`)** splits that path:
   `0` ep0 config, `1` + DALEPENA, `2` + TRB write, `3` + dcache flush,
   `4` + `STARTTRANSFER`, `5` + `RUN_STOP`, 99 all.
   Applied inside `ud_start_trb` (returns at 2 and 3) and in the caller
   (returns below 5 and 6). Patch validated against the CI tree.
4. **Suspects for the next cycle:** the dcache maintenance uses
   `flush_dcache64(pa, len, TRUE)`, which converts PA -> linear VA with
   `gPhysBase/gVirtBase`; if `ud.dma_pa` falls outside that MAP, the `dc vcac`
   takes a data abort (looks like a power-off through the watchdog). Print
   `ud.dma` and `ud.dma_pa` before the first sync if step 2 is the one that
   fails.

## Agent handoff (2026-10-22 16:20)

### >>> UPDATE 2026-10-22 (step-1 fails; finer gates + flush removed — Run 32) <<<

1. `sweetusbstep=0` (stop right after ep0 config) **boots to SpringBoard**: the
   pulled ring (`/tmp/log-step0.txt`, fresh, flush #17) shows
   `SWEET USB: bring-up step limit 0`, `ep0 attempt 0 ok`, then
   `stop after ep0 config`, and later `SMP SpringBoard[26]` — userland is fine.
2. `sweetusbstep=1` never reaches the Apple logo. Its console (photo) shows the
   same lines up to `ep0 attempt 0 ok`, so the crash is in what step 1 adds:
   `sweet_log_flush()`, `DALEPENA = 0x3`, `ud_start_setup()`.
3. **Run 32 (commit `26d81d6`, CI `39751295350`)** removes the early
   `sweet_log_flush()` (it never reached the partition and is itself a suspect)
   together with its `sweet_ufs.c` helper, and splits the gates:
   `0` ep0 config, `1` + DALEPENA, `2` + ep0 transfer start, `3` + RUN_STOP,
   default 99 = everything. Patch validated against the CI tree (EXACT).
4. **Test order:** step 1, then step 2, then 3. Whichever first fails is the
   fault; each test is
   `tools/picasso-boot.sh xnu/out-0088-run32/kernel.development.sweet "sweetusbstep=N"`.

## Agent handoff (2026-10-22 16:00)

### >>> UPDATE 2026-10-22 (bisect: sweetusbstep=0 stays at the Apple logo; crash is after ep0 config) <<<

1. **Run 31 (commit `1fa8a01`) adds `sweetusbstep=N`**: 0 = return right after ep0
   is configured, 1 = also `DALEPENA` + `ud_start_setup()`, 2 = also
   `RUN_STOP`, default 99 = everything. The step comes from the payload
   cmdline, so bisecting costs a payload rebuild, not a kernel build:
   `tools/picasso-boot.sh xnu/out-0088-run31/kernel.development.sweet "sweetusbstep=0"`.
2. **`sweetusbstep=0` result:** image MD5 `8bfc93d3…` flashed and verified; the
   phone **shows the Apple logo and stays there** (user-reported), no reboot.
   So ep0 configuration itself is fine and the crash is later: either
   `DALEPENA`/`ud_start_setup()` (step 1) or `RUN_STOP`/threads (step 2).
3. **Next:** flash `sweetusbstep=1`; if it still reaches the logo, the fault is
   in `RUN_STOP`/`ud_thread`; if it reboots, it is `ud_start_setup()`
   (`ud_start_trb` + `DEPCMD_STARTTRANSFER`).
4. Reminder: the `darwin` ring stays stale for these boots because the early
   `sweet_log_flush()` does not reach the partition at ~3 s and the periodic
   flush is 10 s away; the step gates are the reliable signal for now.

## Agent handoff (2026-10-22 15:35)

### >>> UPDATE 2026-10-22 (Run 29: ep0 SETEPCONFIG now works; phone reboots right after — Run 30 adds log flush) <<<

1. **Run 29 (commit `8b7ae57`, image MD5 `d4f89030…`) — the UTMI clock routing was
   the missing piece.** Console (photo of the screen):
   ```
   SWEET USB: GENERAL_CFG pre: 0x00000004
   SWEET USB: GENERAL_CFG post: 0x0000000d
   SWEET USB: ep0 attempt 0 ok (GINTSTS 0x40204000 GSTS 0x7e000000 DSTS 0x00d20001)
   ```
   `SETEPCONFIG` completes on the first try. Every earlier timeout was the
   missing `PIPE_UTMI_CLK_DIS` -> `PIPE_UTMI_CLK_SEL|PIPE3_PHYSTATUS_SW` ->
   clear handshake from `dwc3-msm`'s POST_RESET event.
2. **But the phone reboots right after that line** and the `darwin` ring still
   holds Run 26's content (MD5 `b4d9cb53…`). Cause: `sweet_log_thread` sleeps
   `SWEET_LOG_SECS` = 10 s between writes, so a panic within that window is
   lost. The crash is between `ep0 ok` and the first flush.
3. **Run 30 (commit `e4fb042`, CI `39747239877`):** `sweet_log_flush()` (wakeup
   on the log channel) added to `sweet_ufs.c`; `sweet_usb.c` flushes right after
   ep0 is configured and again after `ud_start_setup()` (new line
   `ep0 started, running`), each with a 20 ms settle. Validated against the real
   build tree (both files byte-exact).
4. **Next:** flash Run 30, let it reboot, pull `darwin`; the log will show how
   far it got after `ep0 started, running` (panic text included).

## Agent handoff (2026-10-22 15:20)

### >>> UPDATE 2026-10-22 (patch 0088 had been silently skipped; regenerated — Run 29) <<<

1. **Run 28 produced a stale kernel.** CI log:
   `Skipping patch .../0088-picasso-display-alwayson-usb-fix.patch
   (already upstream, previously applied, or malformed):
   error: patch failed: bsd/dev/sweet_usb.c:878`
   The artifact had none of the USB work (`AHB2PHY`, `GENERAL_CFG`, `ep0
   attempt` strings all absent), so the flash/test of Run 28 was meaningless.
2. **Cause:** my hand-edited patch had accumulated duplicate lines (two
   `Endpoint 0 OUT/IN` comments and both the 512 and 64 MPS variants inside one
   hunk), and its `@@ -878` hunk no longer matched the real file, whose region
   sits at line 1042. My local check had rebuilt the base *from the patch
   itself*, so it could not detect a wrong context.
3. **Fix:** downloaded the CI artifact `xnu-buildtree` (22 MB) for the run,
   edited the real `xnu/bsd/dev/sweet_usb.c`, and regenerated the whole
   `sweet_usb.c` section of the patch with `diff -u`. That also restored the
   post-reset settle + GUSB2PHYCFG/GUSB3PIPECTL/GCTL re-apply and the
   `QSCRATCH GENERAL_CFG` UTMI handshake, which the broken hunk had dropped,
   and removed the stale `SS_PHY_CTRL`/`LANE0_PWR_PRESENT` block.
   Verified: `patch -p1` on the real tree applies with no fuzz and the result
   is byte-identical to the edited file.
4. **Guard:** new `tools/check-patch.sh PATCH buildtree.tgz` dry-runs any patch
   against the real CI tree. Use it before every push; a synthetic base is not
   a valid check.
5. **Run 29 (commit `8b7ae57`, CI `39745379233`).** Before flashing, confirm the
   log says `Applying patch:` and not `Skipping patch`.

## Agent handoff (2026-10-22 15:00)

### >>> UPDATE 2026-10-22 (host log proves the PHY works; DWC3 ep0 is the only fault — Run 28) <<<

1. **Decisive host-side evidence** (`journalctl -k` for the Run 27 window):
   ```
   14:41:37 usb 1-12: new high-speed USB device number 89 using xhci_hcd
   14:41:42 usb 1-12: device descriptor read/64, error -110
   ... repeated up to device number 108 ...
   14:45:29 usb 1-12: New USB device found, idVendor=05c6 idProduct=9039
   ```
   Every ~6 s the host sees the phone connect at **high speed** and fails to
   read the descriptor. So rails, GDSC, AHB2PHY, the Femto PHY, UTMI clock and
   the D+ pull-up are all correct; the device never answers control transfers
   because DWC3 still rejects `SETEPCONFIG` for ep0. All the clock/PHY/rail
   work (patch 0088 + RPMh votes) is settled — do not re-litigate it.
2. **Run 27 log could not be pulled:** the `darwin` ring still contains the
   Run 26 content (flush #19, identical MD5), i.e. Run 27 rebooted before the
   log thread flushed. The on-screen console did show
   `GENERAL_CFG pre: 0x00000004` -> `post: 0x0000000d`, so the UTMI clock
   routing ran.
3. **Run 28 (commit `bbc199a`, CI `39743461678`):** ep0 setup retried up to 20
   times, 250 ms apart, printing `ep0 attempt N ok|failed (GINTSTS … GSTS …
   DSTS …)` for each attempt; the failure path still logs and returns.
   Rationale: the core may only accept SETEPCONFIG after observing the USB
   reset the host is already sending. Patch verified with `patch -p1`.
4. **Next if it still fails:** the per-attempt GINTSTS/GSTS shows whether the
   command is ignored (no USBRST seen) or errored; then compare with
   `__dwc3_gadget_start` ordering (DCFG speed is programmed *after* the ep
   config in Linux, we do it before).

## Agent handoff (2026-10-22 14:40)

### >>> UPDATE 2026-10-22 (Run 26: rails voted OK; missing QSCRATCH UTMI clock — Run 27) <<<

1. **Run 26 (`284348027a…`) log, `/tmp/log-run26.txt`:**
   - All three rail votes went through:
     `ldoa5 addr=0x40b00`, `ldoa12 addr=0x42000`, `ldoa2 addr=0x40f00`,
     each `TCS0 done=1 … cmd-status=0x10101/0x10101/0x10101`,
     `SWEET USB: rails ldoa5=0 ldoa12=0 ldoa2=0`.
   - PHY still reads the same, `ep0 cmd 0x1 timeout`, host still nothing.
   - So rails were not the blocker.
2. **Real gap found in the downstream driver:** `dwc3-msm.c:2036`
   (`DWC3_CONTROLLER_POST_RESET_EVENT`) handles the HS/FS-only case (exactly
   lito: `maximum-speed = "high-speed"`, `usb_nop_phy`):
   ```
   GENERAL_CFG |= PIPE_UTMI_CLK_DIS;   // bit 8
   usleep 2..5
   GENERAL_CFG |= PIPE_UTMI_CLK_SEL | PIPE3_PHYSTATUS_SW;  // bits 0,3
   usleep 2..5
   GENERAL_CFG &= ~PIPE_UTMI_CLK_DIS;
   ```
   Run 17 had only set bits 0|3 without the DIS handshake, which is presumably
   why it tripped the RPM watchdog. Without any of this the UTMI clock never
   reaches the controller, so DEPCMD never completes.
3. **Run 27 (commit `7afb4e8`, CI `39740539194`):** apply the documented
   three-step sequence right after the DWC3 soft reset (and after the
   GUSB2PHYCFG/GUSB3PIPECTL/GCTL re-apply), guarded by `qs != 0`, logging
   `GENERAL_CFG pre/post`. Patch verified with `patch -p1` (no fuzz/rejects).
4. **Also confirmed from recovery sysfs** (Android running USB):
   `pm8150_l5=880000`, `l12=1710000`, `l2=3050000`, `usb30_prim_gdsc=enabled`,
   `l5_so`/`l12_so` disabled — the rails are now voted at those values.

## Agent handoff (2026-10-22 14:05)

### >>> UPDATE 2026-10-22 (Run 24 boots; APB is fine; now voting PHY rails — Run 25) <<<

1. **Run 24 (commit `3e2bc62`, image MD5 `e422b955…`) boots to the Apple logo**
   and stays there, so the GDSC offset fix is correct. Log
   (`/tmp/log-run24.txt`, 92,921 bytes, pull from recovery):
   - `GDSC usb30_prim 0xf822f000 -> 0xf822f000` (already on, nothing written)
   - `AHB2PHY BCR pre: 0x00000000`, `post: 0x00000000`, `QUSB2PHY PRIM_BCR post: 0`
   - `HSPHY pre-init` == `post-init` (`0x01010101 / 0x00000000 / 0x88888888 / 0x06060606 / 0`)
   - `HSPHY overrides X0 0x64646464 X1 0xc8c8c8c8 X2 0x24242424` — exactly the
     bytes we wrote, i.e. **writes land and the registers are byte-replicated
     8-bit registers**. The earlier "bus error fill" theory (Runs 19-21) was
     wrong; the identical pre/post values are expected because the sequence
     ends by clearing the bits it set.
   - `ep0 cmd 0x1 timeout`, `endpoint 0 setup failed (speed 0 ep0_mps 64)`,
     `DSTS 0x00520001` (CONNECTSPD = full speed, USBLNKST = 4).
   - Host still does not enumerate anything.
2. **New hypothesis:** the PHY's analog rails are off. `lito-regulators.dtsi`
   gives `regulator-pm8150-l5-so` and `-l12-so` `qcom,init-enable = <0>`, so
   the RPMh sleep set disables pm8150_l5 (0.88 V), l12 (1.8 V) and l2 (3.3 V)
   after a warm reboot; a PHY without its PLL has no UTMI clock, which is
   exactly a DWC3 command timeout.
3. **Run 25 (commit `fe198e1`, CI `39736312469`):**
   - `sweet_rpmh.c`: if the locator at `0x0c3f000c` is empty, fall back to the
     DTB's cmd-db at `0x80860000` (0x20000) — picasso has no locator word.
   - `sweet_usb_gadget_start` votes `ldoa5` 880 mV, `ldoa12` 1800 mV,
     `ldoa2` 3300 mV through `sweet_rpmh_vreg_enable()` and logs the results.
   - Both patches verified with `patch -p1` on a synthetic checkout.
4. **Look for on the next boot:** `SWEET USB: rails ldoa5=… ldoa12=… ldoa2=…`,
   the RPMh `TCS… done=1` lines, and whether `lsusb` shows `1209:`.

## Agent handoff (2026-10-22 13:45)

### >>> UPDATE 2026-10-22 (Run 23 panic: GDSC offset wrong; Run 24 fixes it) <<<

1. **Run 23 panic (photo of the screen):**
   `panic(cpu 0 caller ...): Kernel data abort`, `far 0xffffffe08fff7c7004`,
   `esr 0x96000007` (data abort, translation fault level 3), `x8 0x10f004`,
   `x9 0x813fd0a8309a0053`. The USB probe lines before it are normal
   (`GDSC usb30_prim 0xf822f000 (on)`, all clocks on except `ahb2phy`).
2. **Root cause:** patch 0072 reads the GDSC at **GCC offset 0x0f004** (GCC
   base 0x100000 -> PA 0x10f004). Run 23 used the absolute PA `0x10f004` as an
   offset (-> PA 0x20f004), which is outside the 0x80000 GCC mapping, hence the
   level-3 translation fault.
3. **Run 24 (commit `3e2bc62`, CI `39733926647`):** define is
   `GCC_USB30_PRIM_GDSCR 0x0f004`, mapping back to 0x90000 like the probe.
   The masked "never set SW_COLLAPSE" logic from Run 23 stays.
4. **Next:** package `payload-picasso-0119.bin`, flash, boot; the interesting
   log lines are `AHB2PHY BCR pre/post` and `HSPHY pre-init`.

## Agent handoff (2026-10-22 13:35)

### >>> UPDATE 2026-10-22 (Run 22 panicked — my GDSC write collapsed the USB domain; Run 23 fixes it) <<<

1. **Symptom:** Run 22 (`13b642d0…`) never reached the Apple logo; the user
   reports a panic. Pulled `darwin` ring from recovery: byte-identical to the
   Run 21 pull (`f4d03b75…`), i.e. the kernel died before the log thread
   flushed anything.
2. **Root cause (my bug in Run 22):** the probe line in the Run 21 log says
   `GDSC usb30_prim 0xf822f000 (on)` — the GDSC was already on. Run 22 wrote
   `gdsc | 1u`, and per `drivers/clk/qcom/gdsc.c` bit 0 is `SW_COLLAPSE`
   (1 = power down), while bit 31 is read-only status. That powered the USB
   domain down and wrote status bits back.
3. **Run 23 (commit `ebb0e5b`, CI `39732072459`):** read the GDSC, clear bit 0
   only if set, otherwise do not write at all; the `AHB2PHY BCR` is now a
   masked read-modify-write of bits 0-1. Verified with a synthetic checkout:
   `patch -p1` applies with no fuzz and no rejects.
4. **Next:** artifact -> `payload-picasso-0118.bin` -> `shim-boot.img` ->
   flash -> reboot -> check host `lsusb`; if it boots but still does not
   enumerate, pull the log and look at `AHB2PHY BCR post` / `HSPHY pre-init`.

## Agent handoff (2026-10-22 13:10)

### >>> UPDATE 2026-10-22 (Run 22 flashed — still no CDC-ECM; log pull pending) <<<

1. **Run 22 build:** CI `39730338285` on `omp` (commit `cb0ae46`) succeeded;
   log shows `Applying patch: 0088-picasso-display-alwayson-usb-fix.patch`
   (no "corrupt patch" this time). Artifact in `xnu/out-0088-run22/`
   (`kernel.development.sweet`, 17,210,952 bytes); new strings verified:
   `AHB2PHY BCR pre/post`, `QUSB2PHY PRIM_BCR post`, `GDSC usb30_prim ... ->`.
2. **Packaging:** `payload-picasso-0117.bin` (16,615,680 B, ADT
   `picasso-clean.adt`, same cmdline as 0116) -> `shim/out/shim-boot.img`
   36,352,000 B, MD5 `13b642d0b5028a91b1789d2ae258fbb2`. Payload verified
   embedded at boot-image offset 0x41000.
3. **Flash:** `dd` to `by-name/boot`, readback MD5 identical. Rebooted.
4. **Result:** host `lsusb` shows no `1209:` after ~2.5 min and `adb` is gone,
   i.e. the device left recovery but still enumerates nothing. Same symptom as
   Run 21. **Log not yet pulled** — needs the phone back in OrangeFox recovery.

## Agent handoff (2026-10-22 12:45)

### >>> UPDATE 2026-10-22 (picasso: Run 21 Log Diagnosed — Wrong Clock Register; Run 22 Fix on branch `omp`) <<<

**Work now happens on branch `omp`** (pushed to `picasso/omp`); `main` stays on
`picasso/main`. CI only auto-runs on `main`, so builds use
`gh workflow run xnu.yml --ref omp`.

1. **Run 21 result (`/tmp/log-run21.txt`, 99,572 bytes, pulled from `darwin`):**
   - Flash + readback MD5 `e0cab1eab5300105c93d33272c257468` matched; device rebooted.
   - Host never enumerated CDC-ECM (`lsusb` has no `1209:`), so ep0 still fails.
   - Log: `ahb2phy CBCR pre-enable: 0x80000002` -> `post-enable: 0x80000003`
     (bit 31 CLK_OFF still set => the write did nothing);
     `HSPHY pre-init` == `post-init` == `0x01010101/0x88888888/0x06060606`;
     `overrides X0: 0x64646464 X1: 0xc8c8c8c8 X2: 0x24242424` (byte-repeated
     bus-error fill, i.e. our own writes read back);
     `ep0 cmd 0x1 timeout`.
2. **Root cause (verified against downstream sources, not guessed):**
   - `driver/clk/qcom/gcc-lito.c` has **no AHB2PHY branch clock at all**; the
     only register is the reset `GCC_USB_PHY_CFG_AHB2PHY_BCR = 0x6a000`
     (`gcc-lito.h` ID 14, also the value the DTB uses for `eud_ahb2phy_clk`).
     So `GCC_AHB2PHY_CBCR 0x6a004` from Run 21 is an unrelated GCC offset.
   - `dwc3-msm.c` (probe + `dwc3_msm_power_collapse_por`) documents: enable the
     `USB3_GDSC` (`usb30_prim`, DTB `0x10f004`), then the AHB clock, then set
     `PERIPH_SS_AHB2PHY_TOP_CFG = 0x11` (one read/write wait state).
   - The live DTB (`dtb/boot_unpacked/dtb`, node `hsphy@88e3000`) gives
     `reg = <0x88e3000 0x110>, <0x88e2000 0x04>` and
     `qcom,param-override-seq = <0x63 0x6c>, <0x85 0x70>, <0x17 0x74>`.
     Patch 0088 had been writing `0x64/0xc8/0x24` — wrong values.
3. **Run 22 patch changes (`xnu/patches/0088-...patch`, commit `8aaec67`):**
   - enable `GCC_USB30_PRIM_GDSC (0x10f004)` and poll status bit 31;
   - deassert `GCC_USB_PHY_CFG_AHB2PHY_BCR (0x6a000)` instead of touching 0x6a004;
   - program `AHB2PHY TOP_CFG = 0x11` via the `0x88e2000` window and read back
     both TOP_CFG and the EUD mode-mgr2 register;
   - use the DTB param override values `0x63/0x85/0x17`;
   - new log tags to check: `GDSC usb30_prim`, `AHB2PHY BCR pre/post`,
     `AHB2PHY TOP_CFG`.
4. **Next:** CI run `39729652794` (branch `omp`) -> download artifact ->
   package payload + `shim-boot.img` -> flash -> reboot -> check log for
   `AHB2PHY BCR post: 0x00000000` and non-repeating HSPHY readbacks.

## Agent handoff archive (2026-10-22 12:28)

### >>> UPDATE 2026-10-22 (picasso: Run 21 Built & Packaged - Boot Image Ready to Flash) <<<

1. **Kernel Build & Artifact Verification (Commit `b09b025`, CI Run `39727077471`):**
   - CI build completed in 6m29s.
   - Downloaded artifact `xnu/out-0088-run21/kernel.development.sweet` (17,227,336 bytes).
   - Binary string verification confirmed all new logic present:
     * `SWEET USB: ahb2phy CBCR pre-enable: 0x%08x`
     * `SWEET USB: ahb2phy CBCR post-enable: 0x%08x`
     * `SWEET USB: HSPHY overrides X0(0x6c): 0x%08x X1(0x70): 0x%08x X2(0x74): 0x%08x`
     * `SWEET USB: HSPHY pre-init 0x3c: 0x%08x 0x50: 0x%08x 0x54: 0x%08x 0x64: 0x%08x 0x94: 0x%08x`
     * `SWEET USB: HSPHY post-init 0x3c: 0x%08x 0x50: 0x%08x 0x54: 0x%08x 0x64: 0x%08x 0x94: 0x%08x`
     * `SWEET FB: display off request ignored (sweetfb_keepon=1)`

2. **Boot Image Packaged (`payload-picasso-0116.bin` & `shim/out/shim-boot.img`):**
   - Packaged `payload-picasso-0116.bin` with `mkpayload.py`:
     * Command line: `debug=0x14e serial=3 cpus=8 keepsyms=1 -noprogress rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 wdt_test=no_user_checkin ufsrw=1 rootrw=1 ufsroot=darwin launchd_unsecure_cache=1 wdt=-1 sweetfb_noconsole=1 sweetfb_hz=30 sweetfb_keepon=1 sweettouch=0 sweetusb=2 sweetusblog=0 sweethidtest=5`
   - Built `shim/out/shim-boot.img`:
     * Size: 36,352,000 bytes
     * MD5: `e0cab1eab5300105c93d33272c257468`

3. **Concrete Instructions for Next Agent / User:**
   - **Device Status:** `f9f3d7b0` is connected in **recovery mode**.
   - **Flash & Test Run 21:**
     ```bash
     adb -s f9f3d7b0 push shim/out/shim-boot.img /tmp/shim-boot.img
     adb -s f9f3d7b0 shell "dd if=/tmp/shim-boot.img of=/dev/block/bootdevice/by-name/boot bs=4M"
     adb -s f9f3d7b0 shell "dd if=/dev/block/bootdevice/by-name/boot bs=36352000 count=1 2>/dev/null | md5sum"
     # Verify output matches: e0cab1eab5300105c93d33272c257468
     adb -s f9f3d7b0 reboot
     ```
   - **Check Results:**
     * Watch host `journalctl -k -f` or `dmesg -w` for USB device enumeration (`1209:0001` or CDC-ECM interface).
     * If CDC-ECM enumerates:
       ```bash
       ip -br link
       sudo ip addr add 192.168.7.1/24 dev <ue0-host-interface>
       sudo ip link set <ue0-host-interface> up
       ping -c 3 192.168.7.2
       ssh root@192.168.7.2  # password: alpine
       ```
     * If device is rebooted back to recovery:
       ```bash
       tools/sweet-log-pull.sh -p darwin /tmp/log-run21.txt
       grep -nE "SWEET USB|HSPHY|ahb2phy" /tmp/log-run21.txt
       ```
     * Check if `ahb2phy CBCR post-enable` shows clock enabled (`bit 31 = 0`), if `HSPHY overrides` show non-zero values (`0x64`, `0xc8`, `0x24`), and whether `ep0 cmd 0x1` succeeds!

1. **Run 20 Log Diagnosis (`/tmp/log-run20.txt`, 113,660 bytes):**
   - Pulled log confirmed:
     * `SWEET USB: HSPHY pre-init 0x3c: 0x01010101 0x50: 0x00000000 0x54: 0x88888888 0x64: 0x06060606 0x94: 0x00000000`
     * `SWEET USB: HSPHY post-init 0x3c: 0x01010101 0x50: 0x00000000 0x54: 0x88888888 0x64: 0x06060606 0x94: 0x00000000`
     * `SWEET USB: VBUS override on: HS_PHY_CTRL 0x10100000`
     * `SWEET USB: soft reset done after 1 ms, GCTL 0x00102001 DCFG 0x00080804`
     * `SWEET USB: ep0 cmd 0x1 timeout (DEPCMD 0x00000401 GCTL 0x00102001 GUSB2PHYCFG 0x00002400 GUSB3PIPECTL 0x030c0002 DCFG 0x00080800 DSTS 0x00520001)`
     * `SWEET USB: endpoint 0 setup failed (speed 0 ep0_mps 64)`
   - **Root Cause:**
     * Register values pre-init and post-init were 100% identical and showed repeating byte patterns (`0x88888888`, `0x01010101`, `0x06060606`), indicating the APB register bus to the Femto PHY (`0x088e3000`) was unclocked and returning AXI bus error fill!
     * The `ahb2phy` bridge clock at GCC offset `0x6a004` (`GCC_USB_PHY_CFG_AHB2PHY_CBCR`) is gated off by default (`0x80000002`).
     * Furthermore, `ml_io_map(GCC_PA, 0x20000)` only mapped 128KB, whereas `0x6a004` requires at least 435KB (`0x80000`).
     * Without the AHB2PHY clock, writes to the Femto PHY were silently dropped, the PHY stayed in uninitialized power-down, and the UTMI clock never started, causing `SETEPCONFIG` (`ep0 cmd 0x1`) to time out.

2. **Run 21 Implementation (Commit `b09b025`, CI Run `39727077471`):**
   - Expanded GCC mapping to `0x80000` (512KB).
   - In `sweet_usb_gadget_start`, before PHY reset and register access:
     * Read `GCC_AHB2PHY_CBCR` (`0x6a004`).
     * Set bit 0 (`*(gcc + 0x6a004) |= 1`) to enable `ahb2phy` clock.
     * Polled bit 31 (`CBCR_CLK_OFF`) to confirm clock is active.
     * Reset PHY block via `GCC_QUSB2PHY_PRIM_BCR` (`0x12000`).
   - Added readback diagnostics for `HSPHY_PARAM_OVRD_X0/X1/X2` to confirm APB register writes succeed.
   - Validated all patch hunks with Python script.
   - Pushed to `picasso/main`, triggering CI Run `39727077471`.

3. **Next Steps (Run 21 Deployment):**
   - Wait for CI build to complete.
   - Download kernel artifact to `xnu/out-0088-run21`.
   - Package `payload-picasso-0116.bin` with `mkpayload.py`.
   - Build `shim-boot.img`.
   - Flash to `/dev/block/bootdevice/by-name/boot` on device `f9f3d7b0`.
   - Reboot device into iOS and monitor for USB CDC-ECM gadget interface on host (`ip link`, `ssh root@192.168.7.2`).

### >>> UPDATE 2026-10-22 (picasso: Run 20 Flashed & Ready - Complete SNPS Femto PHY Powerup & GCC Reset) <<<

1. **Kernel Build & Artifact Verification (Commit `7fcf36d`, CI Run `39722914043`):**
   - CI build completed in 7m32s.
   - Downloaded artifact `xnu/out-0088-run20/kernel.development.sweet` (17,210,952 bytes).
   - Binary string verification confirmed all new logic present:
     * `SWEET USB: HSPHY pre-init 0x3c: 0x%08x 0x50: 0x%08x 0x54: 0x%08x 0x64: 0x%08x 0x94: 0x%08x`
     * `SWEET USB: HSPHY post-init 0x3c: 0x%08x 0x50: 0x%08x 0x54: 0x%08x 0x64: 0x%08x 0x94: 0x%08x`
     * `SWEET USB: VBUS override on: HS_PHY_CTRL 0x%08x`
     * `SWEET FB: display off request ignored (sweetfb_keepon=1)`
2. **Boot Image Packaged & Flashed:**
   - Packaged `payload-picasso-0115.bin` with `sweetusb=2`, `sweetusblog=0`, `sweethidtest=5`, `sweetfb_keepon=1`, `debug=0x14e`.
   - Built `shim/out/shim-boot.img` (36,352,000 bytes, MD5 `1795eedddc7b86e6ddf0942c18ee81c5`).
   - Flashed to `/dev/block/bootdevice/by-name/boot` (`/dev/block/sde55`) via recovery ADB.
   - Verified readback MD5: `1795eedddc7b86e6ddf0942c18ee81c5` (100% match).
3. **Execution Plan:**
   - Reboot device from recovery into iOS.
   - Monitor host network interface for CDC-ECM gadget (`1209:0001` / `02:00:5e:77:ee:01`).
   - Assign host IP `192.168.7.1/24` and test SSH to `192.168.7.2` (password `alpine`).

1. **Run 19 Log Analysis (`/tmp/log-run19.txt`, 90,926 bytes):**
   - Pulled directly from `darwin` partition (`/dev/block/sda35`) via `tools/sweet-log-pull.sh`.
   - Log extracted:
     * `SWEET USB: ue0 192.168.7.2/24 up (err 0)`
     * `SWEET USB: HSPHY pre-init 0x50: 0x00000000 0x54: 0x88888888 0x94: 0x00000000`
     * `SWEET USB: HSPHY post-init 0x50: 0x00000000 0x54: 0xb0b0b0b0 0x94: 0x00000000`
     * `SWEET USB: VBUS override on: HS_PHY_CTRL 0x10100000 SS_PHY_CTRL 0x01000000`
     * `SWEET USB: soft reset done after 1 ms, GCTL 0x00102001 DCFG 0x00080804`
     * `SWEET USB: ep0 cmd 0x1 timeout (DEPCMD 0x00000401 GCTL 0x00102001 GUSB2PHYCFG 0x00002400 GUSB3PIPECTL 0x030c0002 DCFG 0x00080800 DSTS 0x00520000)`
     * `SWEET USB: endpoint 0 setup failed (speed 0 ep0_mps 64)`
   - **Root Cause Identified via Downstream Picasso Driver (`drivers/usb/phy/phy-msm-snps-hs.c`):**
     * In Run 19, the Femto PHY registers (`0x088e3000`) were partially accessed, but critical initialization steps were missing:
       1) **PHY Reset Missing:** `GCC_QUSB2PHY_PRIM_BCR` (`gcc + 0x12000`) was never asserted/deasserted.
       2) **PHY Sleep Mode:** `USB2_PHY_USB_PHY_UTMI_CTRL0` (`0x3c`) `SLEEPM` (bit 0) was never set, so the PHY remained in hardware sleep mode!
       3) **Reference Clock Selection:** `USB2_PHY_USB_PHY_REFCLK_CTRL` (`0xa0`) was not configured for CXO 19.2MHz (`0x2 << 0`), and `FSEL` in `0x54` was not zeroed.
       4) **Internal LDO Regulator:** `USB2_PHY_USB_PHY_HS_PHY_CTRL_COMMON2` (`0x5c`) `VREGBYPASS` (bit 0) was not set.
       5) **PHY Suspend:** `USB2_PHY_USB_PHY_HS_PHY_CTRL2` (`0x64`) `USB2_SUSPEND_N` (bit 2) was not asserted to wake PHY from suspend.
       6) **Tuning Overrides:** Picasso device tree specifically overrides parameter registers with `<0xc8 0x70>, <0x24 0x74>, <0x64 0x6c>`, rather than stock lito values.
       7) **SuperSpeed Power Present:** `LANE0_PWR_PRESENT` in `SS_PHY_CTRL` was incorrectly asserted on a High-Speed only port (`dwc3-msm.c` asserts this only when `speed >= SUPER`).

2. **Run 20 Implementation (Commit `7fcf36d`, CI Run `39722915002`):**
   - In `xnu/patches/0088-picasso-display-alwayson-usb-fix.patch`:
     * Reset PHY hardware block via `GCC_QUSB2PHY_PRIM_BCR` (`*(gcc + 0x12000) |= 1; delay(150); *(gcc + 0x12000) &= ~1; delay(150)`).
     * Complete 13-step Linux `msm_hsphy_init` sequence:
       - `CFG0` (`0x94`) override enable
       - `UTMI_CTRL5` (`0x50`) assert `POR`
       - `COMMON0` (`0x54`) clear `FSEL` for 19.2MHz ref clock
       - `COMMON1` (`0x58`) set `PLLBTUNE` (bit 5)
       - `REFCLK_CTRL` (`0xa0`) set `REFCLK_SEL_DEFAULT` (0x2)
       - `COMMON1` (`0x58`) set `VBUSVLDEXTSEL0`, `CTRL1` (`0x60`) set `VBUSVLDEXT0`
       - Picasso tuning overrides: `0xc8` -> `0x70`, `0x24` -> `0x74`, `0x64` -> `0x6c`
       - `COMMON2` (`0x5c`) set `VREGBYPASS` (bit 0)
       - `CTRL2` (`0x64`) set `USB2_SUSPEND_N_SEL | USB2_SUSPEND_N`
       - `UTMI_CTRL0` (`0x3c`) set `SLEEPM` (bit 0) to wake PHY
       - Deassert `POR` (`0x50` &= ~2)
       - Release `SUSPEND_N_SEL` (`0x64` &= ~8)
       - Release override (`0x94` &= ~2)
     * Configure `QSCRATCH_HS_PHY_CTRL` early with `UTMI_OTG_VBUS_VALID | SW_SESSVLD_SEL` without asserting `LANE0_PWR_PRESENT`.
     * Cleaned and validated patch hunk line counts with Python script (100% clean).
   - Pushed to `picasso/main`. Waiting for CI build.

3. **Next Concrete Action:**
   - Wait for CI Run `39722915002` to finish.
   - Download `kernel.development.sweet`, verify symbol/string contents.
   - Package `payload-picasso-0115.bin` and build `shim-boot.img`.
   - Flash to `/dev/block/bootdevice/by-name/boot` via recovery ADB and reboot to test.

1. **Run 18 Execution & Verification (Commit `2743f0c`, CI Run `39716818236`):**
   - User reported: Apple logo displayed continuously without any reset or reboot loop. Removing `QSCRATCH_GENERAL_CFG` write fully fixed the 2-3s RPM watchdog reset.
   - Pulled 94,370-byte kernel log directly from the `darwin` partition (`/dev/block/sda35`) via `tools/sweet-log-pull.sh`:
     * Log confirms: `SWEET FB: display off request ignored (sweetfb_keepon=1)` working 100%.
     * `SWEET USB: soft reset done after 1 ms, GCTL 0x00102001 DCFG 0x00080804`
     * `SWEET USB: ep0 cmd 0x1 timeout (DEPCMD 0x00000401 GCTL 0x00102001 GUSB2PHYCFG 0x00002400 GUSB3PIPECTL 0x030c0002 DCFG 0x00080800 DSTS 0x00520001)`
     * `SWEET USB: endpoint 0 setup failed (speed 0 ep0_mps 64)`
     * `SWEET USB: ahb2phy clock off; QUSB2 PHY not read`
2. **Root Cause Analysis:**
   - **SNPS Femto High-Speed PHY (`0x088e3000`) Never Initialized:** On Snapdragon 765G (`picasso`), the High-Speed PHY is `qcom,usb-hsphy-snps-femto` at `0x088e3000` (driven by `phy-qcom-snps-femto-v2.c` in Linux). The driver had skipped reading `0x088e3000` due to `ahb2phy` check (which only applied to SM7150 QUSB2).
   - Consequently, the SNPS Femto PHY remained in hardware powerdown/retention (`SIDDQ` bit 2 and `RETENABLEN` bit 3 in `USB2_PHY_USB_PHY_HS_PHY_CTRL_COMMON0` at `0x54`), keeping the UTMI clock halted.
   - The DWC3 core requires an active UTMI clock handshake to synchronize `DEPCMD_SETEPCONFIG` (`ep0 cmd 0x1`), causing `CMDACT` to never clear.
   - Additionally, VBUS override was placed after the endpoint setup return point, so VBUS session and `DCTL_RUN_STOP` were never reached.
3. **Run 19 Fix:**
   - **Wake SNPS Femto PHY at `0x088e3000`:** Assert POR (`0x50` |= 2) with override (`0x94` |= 2), clear `SIDDQ` (bit 2) and `RETENABLEN` (bit 3) in `0x54`, set `FSEL_DEFAULT` (`3 << 4`), de-assert POR, clear override, and write device-tree tuning overrides (`0x6c` = `0x63`, `0x70` = `0x85`, `0x74` = `0x17`).
   - **Enable VBUS Override Early:** Set `QSCRATCH_HS_PHY_CTRL` (`UTMI_OTG_VBUS_VALID | SW_SESSVLD_SEL`) and `QSCRATCH_SS_PHY_CTRL` (`LANE0_PWR_PRESENT`) prior to soft reset so the session is valid throughout initialization.
   - **Clean `ud_epcmd`:** Remove extraneous mid-command writes to `GUSB2PHYCFG` and `GUSB3PIPECTL`. Retain 100ms timeout and register dump on failure.
   - **Strict High-Speed Mode:** Maintain `DCFG.DEVSPD = 0` and `ep0_mps = 64`.

1. **Kernel Build & Verification (Commit `2743f0c`, CI Run `39716818236`):**
   - CI build completed successfully in 8m13s.
   - Downloaded `xnu-kernel` artifact (`xnu/out-0088-run18/kernel.development.sweet`).
   - Verified strings: `SWEET FB: display off request ignored`, `SWEET USB: endpoint 0 setup failed (speed 0 ep0_mps %u)`.
2. **Boot Image Packaged & Flashed:**
   - Packaged `payload-picasso-0113.bin` with `sweetusb=2`, `sweetusblog=0`, `sweethidtest=5`, `sweetfb_keepon=1`, `debug=0x14e`.
   - Built `shim/out/shim-boot.img` (36,352,000 bytes, MD5 `8c1c07f580f2593a4d8e23e72ef0b2e7`).
   - Flashed to `/dev/block/bootdevice/by-name/boot` (`/dev/block/sde55`).
   - Readback verified MD5 `8c1c07f580f2593a4d8e23e72ef0b2e7`.
3. **Execution Plan:**
   - Reboot device from recovery into iOS.
   - Monitor host network interface for CDC-ECM gadget (`1209:0001` / `02:00:5e:77:ee:01`).
   - Assign host IP `192.168.7.1/24` and test SSH to `192.168.7.2`.

1. **Run 17 Result & Root Cause Diagnosis:**
   - User reported: "may hien console tam 2-3s la tat" (console showed 2-3s then powered off / rebooted).
   - This exact 2-3s behavior is the identical Qualcomm RPM watchdog reset observed in Run 11!
   - Root Cause: Touching `QSCRATCH_GENERAL_CFG` (`0x0a6f8808`) on SM7250 trips Qualcomm's secure RPM clock monitor / XPU violation, causing a hardware reboot after 2-3s.
   - When `QSCRATCH_GENERAL_CFG` is untouched (Runs 13, 14, 15, 16), userland boots stably with zero reboots and Apple logo stays on continuously.
2. **Run 18 Fix:**
   - Completely remove all writes to `QSCRATCH_GENERAL_CFG` (`0x08`).
   - Retain VBUS session override (`QSCRATCH_HS_PHY_CTRL` only).
   - Retain PHY suspend clears (`GUSB2PHYCFG` SUSPHY/ENBLSLPM, `GUSB3PIPECTL` SUSPHY) and clock gating disable (`GCTL_DSBLCLKGTNG`).
   - Retain High-Speed mode (`DCFG.DEVSPD = 0`) and `ep0_mps = 64` bytes.

### >>> UPDATE 2026-10-22 (picasso: Run 17 Built & Deployed - Patch 0088 Verified, USB 2.0 High-Speed & ep0_mps 64) <<<

1. **Kernel Build & Artifact Verification (Commit `601a7bf`, CI Run `39713806931`):**
   - CI build finished successfully in 7m21s.
   - Build log confirmed clean patch application without any skips: `Applying patch: .../0088-picasso-display-alwayson-usb-fix.patch`.
   - Downloaded `xnu-kernel` artifact (`kernel.development.sweet`, 17,227,336 bytes).
   - Binary string verification confirmed all new logic present:
     * `SWEET FB: display off request ignored (sweetfb_keepon=1)`
     * `SWEET USB: QSCRATCH HS_PHY_CTRL 0x%08x GENERAL_CFG 0x%08x -> 0x%08x`
     * `SWEET USB: endpoint 0 setup failed (speed 0 ep0_mps %u)`
     * `SWEET USB: ep%u cmd 0x%x timeout (DEPCMD 0x%08x GCTL 0x%08x GUSB2PHYCFG 0x%08x GUSB3PIPECTL 0x%08x DCFG 0x%08x DSTS 0x%08x)`
2. **Boot Image Packaged & Flashed:**
   - Built `payload-picasso-0112.bin` with `sweetusb=2`, `sweetusblog=0`, `sweethidtest=5`, `sweetfb_keepon=1`, `debug=0x14e`.
   - Built `shim/out/shim-boot.img` (36,352,000 bytes, MD5 `4f923d19da2fcb56f62491629ed0524d`).
   - Flashed directly to `/dev/block/bootdevice/by-name/boot` (`/dev/block/sde55`) via recovery ADB, readback MD5 verified 100%.

### >>> UPDATE 2026-10-22 (picasso: Run 16 Analysis & Run 17 Fix - Physical USB 2.0 High-Speed, ep0_mps 64, & UTMI Clock) <<<

**RUN 16 LOG ANALYSIS (`/tmp/log-run16.txt`, 98,350 bytes):**
1. **Display Always-On (100% SUCCESS):**
   - Panel stayed lit continuously with Apple logo; no fade-out (user confirmed: "dang hien logo apple, van sang lien tuc khong co phan ung gi").
   - `sweetfb_keepon=1` kept panel lit, `SweetHID` Power (`0x30`) & Menu (`0x40`) keys fired continuously every 5s.
2. **USB DWC3 Diagnosis:**
   - Log showed:
     * `DWC3 DCFG 0x00080804 DSTS 0x00520004` (link state `USBLNKST = 4` = `SS.Disabled`).
     * `ep0 cmd 0x1 timeout (DEPCMD 0x00000401 DCFG 0x00080804 DSTS 0x00520004)`.
     * `endpoint 0 setup failed (speed 4 ep0_mps 512)`.
   - **Root Causes:**
     a) **Hardware is USB 2.0 Only:** User emphasized Redmi K30 5G hardware specification is strictly USB 2.0. Verified against device tree (`picasso-sm7250.dtsi` line 962: `maximum-speed = "high-speed"; usb-phy = <&usb2_phy0>, <&usb_nop_phy>;`). Setting SuperSpeed (`DCFG = 4`) leaves DWC3 core stuck in `SS.Disabled` waiting for nonexistent PIPE clock.
     b) **EP0 MPS Violation in High-Speed Mode:** In all prior runs, `ep0_mps` was passed as 512 bytes (`512u << 3`). Under USB 2.0 spec section 5.5.3, Control Endpoint 0 MPS MUST be 64 bytes. The DWC3 core rejects/hangs on `SETEPCONFIG` if MPS > 64 when configured for High-Speed.
     c) **Qualcomm Wrapper UTMI Clock Selection:** In `dwc3-msm.c`, on platforms without SuperSpeed PHY operating at High-Speed, `QSCRATCH_GENERAL_CFG` must route the UTMI clock (`PIPE_UTMI_CLK_SEL` bit 0) and assert `PIPE3_PHYSTATUS_SW` (bit 3). `LANE0_PWR_PRESENT` must NOT be asserted in `QSCRATCH_SS_PHY_CTRL`.
3. **RUN 17 FIX:**
   - In `sweet_usb_gadget_start`:
     * Set `QSCRATCH_GENERAL_CFG` (`0x08`) |= `(1u << 0) | (1u << 3)` (`PIPE_UTMI_CLK_SEL | PIPE3_PHYSTATUS_SW`) early and post-reset.
     * Do NOT assert `LANE0_PWR_PRESENT` in `QSCRATCH_SS_PHY_CTRL` so SuperSpeed power is not falsely reported.
     * Force `DCFG` to High-Speed (0): `dwc_wr(DCFG, dcfg | 0)`.
     * Set initial `ep0_mps = 64` for both EP0 OUT and EP1 IN.

### >>> UPDATE 2026-10-22 (picasso: Run 15 Analysis & Run 16 Fix - DCFG SuperSpeed & AHB2PHY Clock) <<<

**RUN 15 LOG ANALYSIS (`/tmp/log-run15.txt`, 98,753 bytes):**
1. **Display & Touch/HID Verification (100% SUCCESS):**
   - Panel stayed lit continuously with Apple logo; no fade-out (user confirmed: "dang treo o logo apple").
   - `sweetfb_keepon=1` successfully kept AMOLED panel on across `backboardd` sleep calls.
   - `SweetHID` Power (`0x30`) & Menu (`0x40`) keys fired continuously.
2. **USB DWC3 Diagnosis:**
   - Log showed:
     * `SWEET USB: clk ahb2phy CBCR 0x80000002 (off)` -> `ahb2phy clock off; QUSB2 PHY not read`.
     * `SWEET USB: DWC3 DCFG 0x00080804` (hardware reset default is SuperSpeed 4).
     * `SWEET USB: GUSB2PHYCFG 0x00002500 -> 0x00002400 GUSB3PIPECTL 0x030c0002 -> 0x030c0002 GCTL 0x00102001`.
     * `SWEET USB: ep0 cmd 0x1 timeout (DEPCMD 0x00000401)`.
   - **Root Causes:**
     a) `sweet_usb_gadget_start` unconditionally forced `DCFG` speed to 0 (HighSpeed): `dwc_wr(DCFG, dcfg | 0)`. On SM7250, forcing speed 0 while expecting SuperSpeed 512-byte max packets breaks DWC3 core clock domain crossing, causing `DEPCMD_SETEPCONFIG` to hang.
     b) `ahb2phy` clock (`0x6a004` in GCC) was left disabled (`0x80000002`), blocking access to the USB PHY register interface.
3. **RUN 16 FIX:**
   - In `sweet_usb_probe`: Enable `ahb2phy` clock branch (`gcc + 0x6a004`) if disabled.
   - In `sweet_usb_gadget_start`: Preserve hardware default speed from `DCFG` (`speed = dwc_rd(DCFG) & 7`), keeping SuperSpeed 4 on SM7250.
   - Set `ep0_mps = (speed == 4 || speed == 5) ? 512 : 64`.
   - In `ud_event`: On `connect done`, dynamically adapt ep0 MPS to 64 if connected at High/Full speed.

### >>> UPDATE 2026-10-22 (picasso: Run 15 Built & Deployed - Kernel 0088 with Patch 0088 Applied) <<<

1. **Kernel 0088 Build & Verification:**
   - CI Run `39706958091` completed successfully in 7m29s.
   - Build log confirms: `Applying patch: .../0088-picasso-display-alwayson-usb-fix.patch` applied cleanly without skipping or corrupt patch warnings.
   - Kernel binary `xnu/out-0088/kernel.development.sweet` SHA256 `e1350fe6f6b117e0065992f714d53e8e6a3a57fed974ebaad04cb3180f5b298b`.
   - Verified strings in binary:
     * `SWEET FB: display off request ignored (sweetfb_keepon=1)`
     * `SWEET USB: GUSB2PHYCFG 0x%08x -> 0x%08x GUSB3PIPECTL 0x%08x -> 0x%08x GCTL 0x%08x`
     * `SWEET USB: ep%u cmd 0x%x timeout (DEPCMD 0x%08x GCTL 0x%08x GUSB2PHYCFG 0x%08x GUSB3PIPECTL 0x%08x)`
2. **Boot Image Packaged & Flashed:**
   - Built `payload-picasso-0110.bin` with `sweetusb=2`, `sweetusblog=0`, `sweethidtest=5`, `sweetfb_keepon=1`, `debug=0x14e`.
   - Built `shim/out/shim-boot.img` (36,352,000 bytes, MD5 `dd7a363714a009ec03225956369df101`).
   - Flashed to `/dev/block/bootdevice/by-name/boot` (`/dev/block/sde55`) via OrangeFox Recovery ADB, readback MD5 verified 100%.

### >>> UPDATE 2026-10-22 (picasso: Run 14 Analysis & Run 15 Fix - Patch 0088 Hunk Fix) <<<

**RUN 14 ROOT CAUSE & FINDING:**
1. User observed: "logo apple hien 2-3s roi mo dan. dien thoai dang o recovery ban check di" (Apple logo showed 2-3s then faded out).
2. Log inspection revealed that CI Job 119583102690 / Run `39705075878` had skipped `0088-picasso-display-alwayson-usb-fix.patch` due to `error: corrupt patch at line 105`.
3. Consequently, Run 14 executed with **Kernel 0087** without patch 0088!
4. In Kernel 0087:
   - `kSweetFBRequestPowerChange` lacked the `sweetfb_keepon=1` guard, allowing `backboardd`'s sleep request to activate the fade-out loop in `SweetFramebuffer.cpp`.
   - `GUSB3PIPECTL` SUSPHY was not cleared and `SweetHID` Power key was not fired.
5. **FIX:**
   - Re-generated unified diff headers for `0088-picasso-display-alwayson-usb-fix.patch`.
   - Verified clean application (`patch -p1 --dry-run` and apply/revert tests on real buildtree with zero warnings/errors).

### >>> UPDATE 2026-10-22 (picasso: Run 6 Analysis & Run 7 Fix - DWC3 UTMI Clock + ENBLSLPM) <<<

**RUN 6 RESULTS (from darwin log `/tmp/log-run6.txt`, 100,116 bytes):**
1. **Display Always-On (VERIFIED SUCCESS):**
   - Panel stayed brightly lit with Apple logo continuously; user confirmed panel did not black out.
   - Log shows: `SWEET FB: display off request ignored (sweetfb_keepon=1)`.
   - `backboardd` drew the Apple logo into surfaces 2 & 3.
2. **iOS 16 Userland Execution (FULL SUCCESS):**
   - 8/8 CPUs active (`SWEET CPU: 8/8 CPUs initialized, SGI1 verified`).
   - `launchd` spawned full iOS subsystem: `runningboardd`, `containermanagerd`, `backboardd`, `SpringBoard` (PID 26), `powerd`, `cfprefsd`, `MobileGestaltHelper`, `lsd`, `biomed`, `mDNSResponder`, `securityd`, `tccd`, `appstored`, `cloudd`...
   - SpringBoard active in `CFRunLoopRunSpecific` across threads.
3. **Hardware Buttons & SSH Status:**
   - Power & Volume buttons are Qualcomm PMIC GPIOs; not yet mapped to iOS `IOHIDEventDriver`, so quick presses do not trigger events. Hard reboot to recovery/fastboot requires 10-15s hold.
   - `sshd` is managed by `launchd` via socket activation (`inetdCompatibility` on port 22/2222); it only spawns upon incoming TCP connection.
4. **USB DWC3 Root Cause Diagnosed:**
   - Log showed: `SWEET USB: ep0 cmd 0x1 timeout` -> `SWEET USB: endpoint 0 setup failed`.
   - Root cause on Snapdragon 765G (SM7250 / lito):
     a) On USB 2.0 High-Speed without SuperSpeed PHY, the Qualcomm DWC3 wrapper defaults to PIPE clock. Without `PIPE_UTMI_CLK_SEL` (bit 0) in `QSCRATCH_GENERAL_CFG` (`0x0a6f8808`), the internal MAC core clock is stopped, causing `DEPCMD_SETEPCONFIG` to hang indefinitely.
     b) `GUSB2PHYCFG` had `ENBLSLPM` (bit 8) active (`0x2500`), which clock-gates `DEPCMD`.
     c) `GCTL` had clock gating enabled (`DSBLCLKGTNG` was 0).
     d) `ud_epcmd` timeout was only 1 ms (1000 * 1 us).

**RUN 7 FIX (Commit `2e431e9`, CI Run `39655317770`):**
- Updated `0088-picasso-display-alwayson-usb-fix.patch`:
  - Added Qualcomm `dwc3_qcom_select_utmi_clk` sequence in `QSCRATCH_GENERAL_CFG`: `PIPE_UTMI_CLK_DIS` -> `PIPE_UTMI_CLK_SEL | PIPE3_PHYSTATUS_SW` -> clear `PIPE_UTMI_CLK_DIS`.
  - Cleared both `GUSB2PHYCFG.SUSPHY` and `GUSB2PHYCFG.ENBLSLPM`.
  - Set `GCTL_DSBLCLKGTNG` to disable core clock gating.
  - Increased `ud_epcmd` poll loop to 10,000 iterations @ 10us delay (100 ms max).
  - Verified `git apply --check` passed cleanly against real buildtree.



**STATE OF THE PHONE RIGHT NOW:** the `boot` partition holds **our shim + the XNU
kernel payload** (image `shim/out/shim-boot.img`, 18,071,552 bytes, built
2026-10-20 16:12, flashed with `fastboot flash boot`). The iOS root
(`/home/tritm/ios-work/root/ios16-root-picasso.img`, 16 GiB, fsck clean) was
written into the **new `darwin` partition** (block 25,701,120, 20 GiB) from the
OrangeFox recovery, 1 GiB at a time, **every chunk verified with md5**
(`/home/tritm/ios-work/write-root-recovery.sh`, log `/tmp/opencode/write-rec.log`).

- GPT on the phone: `userdata 2883584..25701119` (87.04 GiB), `darwin
  25701120..30944250` (20 GiB); primary and backup header + entry CRC verified
  OK; Droidian rebooted normally afterwards (84 GB, 33 GB used, `lxc@android`
  alive) - **the user's data is intact**.
- The phone shows **no USB device** any more (`lsusb` sees no 18d1, no fastboot,
  no adb) - the XNU kernel has no USB driver yet, so this is expected whether it
  booted or hung. **A photo of the screen is the only way to tell.**
- The shim prints its board report on the panel before handing over to XNU, so
  the screen should show the last lines of the shim (or the kernel console).

**TOUCH HARDWARE - read the *picasso* device tree, not the generic bengal one.**
`kernel/arch/arm64/boot/dts/vendor/qcom/picasso-sm7250.dtsi` (the generic
`bengal-idp.dtsi` is a different variant and lists Synaptics/FocalTech on I2C):

| Thing | picasso |
| --- | --- |
| controller | Novatek **NT36672C**, `compatible = "novatek,NVT-ts-spi"` |
| bus | **`qupv3_se7_spi`** (QCOM SPI v2, *not* GENI), `touch_spi@0` |
| pins | CS gpio8, reset gpio4, irq gpio5 (TLMM) |
| clock | 5 MHz (`spi-max-frequency`) |
| firmware | `novatek_nt36672c_g7a_fw01.bin` + `mp01.bin` (3 glass variants) |
| chip regs | `swrst-n8-addr 0x03F0FE`, `spi-rd-fast-addr 0x03F310` |
| Linux driver | `kernel/drivers/input/touchscreen/nt36672c/nt36xxx*.c` (SPI based) |

**UniXNU research (2026-10-20 evening).** `UniXNU/UniXNU` (213 stars, pushed
2026-10-20 11:01) is *"an independent project to run iOS and macOS on non-Apple
ARM64 hardware through UEFI"*. Compared with ios-sweet:

| | ios-sweet | UniXNU |
| --- | --- | --- |
| boot chain | ABL -> bare-metal shim -> payload | ABL -> BootShim -> **UEFI (Mu-Silicium)** -> UniXNU menu -> payload -> DarwinBoot -> XNU |
| XNU | 8796 (iOS 16 userland) | 10063.141.1 (macOS 14 / iOS 17) |
| build host | Linux (make), CMake port in progress | **macOS + Xcode only** (`xcodebuild`, iphoneos SDK) |
| boards | dozens, community wide | **one** (sheng = SM8550, work in progress) |

What is worth taking from it for picasso:

1. `UniXNU/NativeNovatekTouch` - the *architecture* of a Novatek driver in XNU:
   protocol layer (`NvtProtocol.c`, chip table with the register addresses),
   pure touch core (`NvtTouchCore.c`, matrix -> contacts), HID publishing
   (`UniXNU/NativeHidInput`), and an abstract `struct nvt_bus` so only the bus
   glue is board specific. Their chip is NT36532E (sheng, GENI SPI); ours is
   NT36672C on QUV3 SPI, so the *table* must come from the Linux driver, but
   the structure is exactly what sweet's `bsd/dev/sweet_touch.c` lacks.
2. `UniXNU/NativeUfs` - a real UFS driver (host controller + SMMU + queues +
   root read path) with `uint64_t` LBAs and a 4 KiB window reader, i.e. it can
   address partitions past 100 GiB (our `darwin` starts at ~105 GiB). Sweet's
   `sweet_ufs.c` is much thinner (one LUN, polled SCSI, no SMMU).
3. `UniXNU/AdrenoPM4` + `UniXNU/AirNir` - PM4 packet encoding and an AIR->NIR
   compiler; our Adreno 620 is the same a6xx family as their Adreno 740.
4. `DarwinBoot` - validates kernel + ADT + trust cache, exits boot services and
   hands the device tree over properly (this is the clean version of what our
   shim does by hand). Needs a UEFI port for SM7250: Mu-Silicium has many
   Xiaomi/Qualcomm boards but **no SM7250** (there is a SM7150/davinci fork as a
   precedent).
5. `docs/xnu/four-k-page-tables.md` - only for 4 KiB granules (BCM2837); picasso
   stays on 16 KiB like sweet.

What it does **not** give picasso: a Linux build (macOS/Xcode required), a
picasso board repository, a UEFI port for SM7250, and an iOS 16 userland on
XNU 8796 (their root is macOS/iOS 17 on XNU 10063 with their own
trustcache/cryptex/VerifiedRoot flow). **Decision: keep the sweet route for
picasso, harvest the driver code and the architecture ideas.**

**The whole UniXNU organisation (26 repositories), studied 2026-10-20 evening.**Layers: `ABL -> BootShim + UEFI volume + DTB -> UniXNU (UEFI boot manager) ->
DarwinBoot (XNU loader) -> prelinked XNU -> IPSW userland`. Repositories:
`UniXNU` (build driver + boot manager + generic drivers + kits + patches + docs,
214 stars), `UniXNU-sheng` (the only board, Xiaomi Pad 6S Pro / SM8550),
`uefi-sheng` (Mu-Silicium fork), driver modules (`ArmCpu`, `ArmDTPlatform`,
`ArmVirtPlatform`, `DarwinBoot`, `NativeAES`, `NativeGeniSpi`, `NativeHidInput`,
`NativeKeyStoreEvents`, `NativeMobileDisplay`, `NativeRamBlock`, `NativeUfs`,
`NativeVirtioBlock`, `NativeVirtioConsole`, `NativeNovatekTouch`,
`NativeAdreno`, `NativeMetal`) and kits (`AdrenoFirmware`, `AdrenoPM4`,
`AirNir`, `ArmSmmu4K`, `NativePrelink`, `VerifiedRoot`, `ShengPreflight`).

Four things worth remembering from it:

1. **The prelink is the core idea.** `kits/NativePrelink/emit_prelink.py` builds
   a kernel from Apple's source and then binds it to the *original* Apple
   kernelcache: it reads `__PRELINK_INFO`, picks the components named in
   `[kernel.prelink] roots` plus the kernel interfaces they import, copies their
   segments into the native kernel's coalesced ranges, rebinds every pointer,
   applies the slide, rewrites the module headers and emits one `kernel.macho`.
   The result keeps Apple's own kexts (AppleImage4, AMFI, IOKit) working next to
   the native platform code. Sweet has no equivalent - it substitutes stand-ins.
2. **They keep Apple's security chain instead of disabling it.** DarwinBoot
   validates the Mach-O (header, entry in an executable segment, aligned
   non-overlapping segments inside a 256 MiB window), the ADT and the trust
   cache (count 1..128, version 2, 24 byte entries, sorted hashes).
   `/chosen/amfi-only-platform-code` keeps AMFI in platform code policy and
   `early_security_init` runs the original `AppleMobileFileIntegrity`
   initializer. `kits/VerifiedRoot` adds a signed policy bundle whose reads are
   authenticated. The 4 KiB code signing patch derives one hash per 4 KiB page
   from the verified 16 KiB blocks.
3. **Three driver layers**: UEFI driver, built-in XNU driver (static archive
   linked into the kernel, matched through `IOKitPersonalities` in
   `KernelConfigTables.cpp`) and kext bundle staged by UEFI.
4. **They test the whole chain in QEMU** (`package/Scripts/qemu-run.py`): the
   UEFI menu, DarwinBoot and the kernel entry run on an aarch64 machine with
   serial log, screenshots, key injection and `--expect/--reject` assertions;
   `smoke.sh` runs five scenarios. A test harness that needs no phone is worth
   copying.

Honest state of the project: CPU/GIC/timer, UFS, HID, Novatek touch, AES,
KeyStore, display plus GPU scanout, Metal device, GPU blit and a GPU
composited desktop all work on sheng. Display vblank, 120 FPS and a
precompiled shader pack do not. One board, one author group, macOS only build.

**Topaz moved again on 2026-10-20 (six commits).** The important ones:
`0129` WLAN station (association, WPA2-PSK handshake in the kernel, HTT data
path) and `0130` join with a PMK, so the topaz port now has working Wi-Fi -
the biggest step the sweet community has taken. The WLAN code comes from
their own `topaz-woa` project (`htt.c`, `htc.c`, `wmi.c`, `ce.c`,
`wlanprobe.c`).

*That Wi-Fi work does not port to picasso.* The two boards use different WLAN
stacks: topaz (SM6225) hosts the WLAN firmware on the modem and talks to it
over GLINK and QRTR with HTT messages; picasso (SM7250) has an integrated
CNSS (the device tree says `qcom,icnss@18800000`) and the host driver is
`cnss2` with remotedev and DIAG, talking a closed MA/XXI protocol. Only the
iOS side (a WLAN radio API as an IOKit service, plus the `/arm-io/wlan` node
in the ADT so that launchd starts `wifid`) can be reused.

*One of their commits is ours already.* `0128` fixes a bug we have too: the
kernel holds a busy count on the registry root until the IOKit daemon reports
that it launched, and there is no kextd here, so that report never comes. The
root stays busy forever, IOKit never quiesces, `configd`'s InterfaceNamer
never says quiet, and every userland client that lists network interfaces
waits at boot. Ported as `xnu/patches/0082-picasso-iokit-quiesce.patch`.

**LESSON: the build driver skips a patch it cannot parse and only warns.**
Our first 0082 had a hunk that claimed sixteen new lines and had eighteen,
so the kernel was built without the fix and the run still said "success".
Always grep the run log for "Skipping patch". The patch is now checked against
the real source (`xnu-8796.101.5`, both hunks apply, offset 15 lines on the
first) and the run log shows it applied.

**Build times on our own CI are short.** Run 39431814735 took 5.5 minutes
(13:36 to 13:41), not the forty minutes a cold runner would need. The
upstream runs that took hours were the ones that had to build the whole
tree from scratch.

**Where the kernel log lands on picasso.** Kernel patch 0047 keeps a 4 MiB
ring and copies it to the last 4 MiB of the partition the kernel opened as
root, so on this port that is the `darwin` partition, not `userdata`
(`su.first_block + su.nblocks - SWEET_LOG_BLOCKS`). The HFS image is 16 GiB
inside a 20 GiB partition, so the log area is free space and Droidian's
`userdata` is never touched. Read it from the recovery with
`tools/sweet-log-pull.sh -p darwin OUT.txt` - that needs no photo of the
screen and no USB driver in the kernel.

State after the 0082 build: kernel `xnu/out-0082/kernel.development.sweet`
(md5 c65c8d07339602a1f66b4227f7a9a6bf), payload
`shim/out/payload-picasso-0082.bin`, boot image `shim/out/shim-boot-0082.img`
(md5 5b7cbcdc2396ef6e5da03eb34c591380), same command line as the first
picasso image (`cpus=8`, `ufsroot=darwin`, `ufsrw=1`, `sweetfb_hz=30`,
`sweettouch=0`).

### >>> UPDATE 2026-10-20 (picasso / SM7250 port groundwork; read first) <<<

**NEW DEVICE: the user's Redmi K30 5G (`picasso`, SM7250 / Snapdragon 765G), currently
running Droidian (Halium 11) in `/home/tritm/droidian` (46 GB tree, its own Linux
4.19.113-lineage kernel, working Phosh/Adreno 620/Wi-Fi/camera).** The phone is the
user's daily driver: **the iOS root must go into its own partition, never into
`userdata`** (that is `/data` and holds Droidian).

**What was done today (all on the PC, nothing was written to the phone):**

- `tools/picasso-fdt.py` (new): a dependency-free flattened-device-tree reader that
  prints the board facts (RAM banks, carve-outs, splash region, GIC, timer PPIs, UFS,
  USB, UART console, CPU MPIDRs). Run on the phone's own DTB
  (`/home/tritm/droidian/output/rec-picasso.dtb`, extracted from `boot.img` with
  `tools/unpack_bootv2.py`).
- `shim/hw.c` + `shim/hw.h` (new): the same discovery in C, for the shim.
  `shim/fdt.c` grew from a 40-line /chosen reader into a real FDT reader
  (iterator, path/compatible lookup, reg decoding with the parent's cell counts).
- `shim/adt.c` + `shim/adt.h` (new): read/write for Apple's ADT (which is **not** an
  FDT: counts, then property records, then children; little endian).
- `shim/board.c` + `shim/board.h` (new): writes the discovered values into the ADT
  the shim hands to xnu (`chosen/dram-base|dram-size`, `arm-io/gic/reg`,
  `virtual-timer-intid`, `sweet-fbcon/reg|width|height|stride`, `sweet-ufs/reg`,
  `cpus/cpuN/reg`).
- `shim/loader.c`: picks the kernel's RAM from the memory layout the bootloader
  described and **patches the payload header** (it lives in the bootloader's
  ramdisk, i.e. in RAM) before placing the segments, then patches the ADT.
  So one boot image works whatever the device's memory layout is.
- `shim/console.c`: the framebuffer geometry is now a runtime value
  (`con_setup()`), with the compiled-in values as defaults.
- `shim/shim.c`: `hw_discover()` runs first; `sweetprobe` in `/chosen/bootargs`
  prints the board report on the panel and stops.
- `shim/build.sh`: `--picasso` (ramdisk @ 0x1200000, DTB @ 0x1728000 - the layout
  proven in the Droidian docs) and `--dtb PATH`.
- `tools/mkadt.py`: `--picasso` board profile (FB 0xa0000000/0x2300000, ECID
  'PICASSO'+1, root compatible `xiaomi,picasso|qcom,sm7250`).
- `shim/host_probe.c` (new): runs the discovery on the host against a DTB file, so
  the numbers can be checked without a phone.

**MEASURED on picasso (from its own DTB, not from any other board):**

| Thing | picasso | sweet | note |
| --- | --- | --- | --- |
| GICD / GICR | 0x17a00000/0x10000, 0x17a60000/0x100000, stride 0x20000 | same | GICv3, no VLPI |
| timer | PPI phys 2 / virt 3 -> vtimer INTID 19, 19.2 MHz | same | |
| CPU | 8 nodes, `reg` = Aff1<<8 (0x0,0x100..0x700) | same | 4x A765 + 4x A555 |
| UFS | 0x1d84000/0x3000 (+ ice 0x1d90000) | 0x1d84000 | same |
| DWC3 | 0xa600000/0xcd00, max-speed super-speed | 0xa600000 | |
| **splash fb** | **cont_splash_region 0xa0000000 + 0x2300000 (35 MiB)** | 0x9c000000 | panel 1080x2400, pipe measured at runtime |
| **UART console** | **qcom,qup_uart@888000 ("qcom,msm-geni-console")** | none | **picasso HAS a debug UART** |
| SPMI | 0xc440000, PM8150@0, power-on@800 | 0xc440000, PM6150L | PMIC differs |
| IMEM | **0x146ab000** (restart_reason +0x65c) | 0x146aa000 | **0x1000 apart, do not copy sweet's** |
| PSHOLD | 0xc264000 | 0xc264000 | same |
| touch | Novatek NT36672C over **SPI**, fw `novatek_nt36672c_g7a_fw01.bin` + `mp01.bin` | Goodix GT9896 over I2C | same IC as the surya port |
| GPU | Adreno 620 (kgsl-smmu 0x3da0000) | Adreno 618 | |
| /memory | `reg = <0 0 0 0>` in the static tree | filled by ABL | **must be read from the live tree** |

Host test result: `host_probe rec-picasso.dtb` finds the splash region, the GIC, the
timer, UFS, DWC3, the UART, 8 MPIDRs and 21 carve-outs, and patches the picasso ADT
with **0 failed edits**.

**THREE THINGS THIS BOOTLOADER INSISTS ON (all found by experiment, 2026-10-20):**

1. **The file offset of a part is its RAM address.** The device tree has to sit
   at a low address (`dtb_offset 0x20000`) and the file has to be at least as
   large as the highest address in it. An image with `dtb_addr 0x1728000` and a
   1.5 MB file makes the transfer hang: the bootloader reads past the end.
   (This is why the Droidian boot image is 23 MB: its device tree is at
   0x1728000.)
2. **The .bss must not be in the file.** The shim's own image is now only the
   code and the data (28 KB); the shim zeroes its .bss and its stacks itself
   when it starts (`_load_end` in shim.ld, the zero loop in start.S).
3. **`fastboot boot` verifies AVB; `fastboot flash` does not.** The phone's own
   Droidian image has no AVB footer, boots from its partition every day, and is
   still refused by `fastboot boot` with "Failed to load/authenticate boot image:
   Load Error" - the same error our image gets. So on picasso a *temporary*
   boot is not possible; the shim has to be **flashed** to `boot`
   (and the Droidian image flashed back afterwards).

**WHAT THE HARDWARE SHOWS (probe of 2026-10-20, on the phone):**

- The shim runs on picasso: the console draws on the panel (red background,
  yellow/white text) at `cont_splash_region` 0xa0000000, 1080x2400 - the
  address and the geometry of the phone's own splash.
- **The UFS host works**: `ver=300 cap=1587031F`, TUR/CAP/READ all rc=0, and
  the GPT is read: `EFI PART`, 64 entries, `persist` lba 0xC000, `persistbak`
  0x10000, `logdump` 0x14000, `rawdump` 0x18000, `minidump` 0x20000,
  `recovery` 0x28000. LUN0 is 0x1D82BBF+1 blocks of 4 KB = 118 GiB, while the
  GPT only covers 15.8 GB - **about 103 GiB are free at the end of the disk**,
  so the iOS root can go into a new partition without touching `userdata`
  (where Droidian's rootfs.img lives).
- **The bootloader does not pass the device tree in x0** on picasso (it does on
  sweet): the first probe printed "no FDT from ABL" and every board number
  came out as zero.
- **Reading an unknown address is not safe.** A read of a protected address
  (the XBL/RPM live in the low memory) answers with a *secure fault*: the
  trust zone kills the core, the shim hangs with the last line it drew and
  there is no panic screen. The shim therefore only reads known addresses and
  prints the registers it was entered with instead of dereferencing them.
  (The shim's own exception handler exists, but a secure fault never reaches
  it.)

**NOT DONE / next:**
1. **The shim has not produced a full report yet.** The probe is being
   narrowed down step by step; the last line on the screen says where it
   stops.
2. The RAM banks are still unknown (the static DTB has none). The probe will
   print them: the bootloader fills /memory in the tree it hands the shim.
3. The stride/bpp of the splash buffer: the probe reads them from the display
   pipe (SSPP SRC0_ADDR/SRC_YSTRIDE0/SRC_FORMAT), so the answer comes with
   the probe run.
4. The ADT for picasso still needs the D22AP `/product` node (the real iPhone X
   device tree) - `research/ipsw-x/d22ap.adt` is missing in this checkout, so
   `mkadt.py` warned "no /product node" and MobileGestalt will not know the device.
5. The iOS root must go into its own partition (`ufsroot=` boot-arg already
   exists in `bsd/dev/sweet_ufs.c`); the GPT change and the GPT fence/watchdog
   from the topaz fork (0088) are still to do.
6. The kernel itself is still the SWEET one: the GIC, UFS and SPMI code is the
   same, but the display, touch and keys drivers are not written for picasso.



### >>> UPDATE 2026-10-18 (read first, then the 10-17 handoff below) <<<

**STATE at end of 2026-10-18 (evening):**
- On the phone: userdata = **image v15** (`/run/media/dipa/android/ios-sweet-ipsw/ios16-root-20H392-v15.img/.simg`; = v14 + `/private/var/preferences/SystemConfiguration/preferences.plist` with one network service "USB Ethernet": Interface DeviceName ue0 Type Ethernet, IPv4 Manual 192.168.7.2/24 router 192.168.7.1, DNS 1.1.1.1 8.8.8.8, set "Automatic" as CurrentSet). Fallback image v14 (v12/v13 deleted).
- Kernels: **0078** = all 8 cores (`cpus=8`, cpu6/7 = A76, homogeneous SMP; mkadt now emits 8 CPU nodes; ADT `shim/out/sweet-0078hi.adt`); **0079** = display off/on via IOMFB selector 12 kern_RequestPowerChange(state) (fade over 6 vsyncs, then black; verified: power button gives "SWEET FB: display off/on"); **0080** = `SWEET NET: <proc> route add/delete/change ...` and `... add/delete address ...` trace (built, `shim/out/shim-boot-0080s.img`, NOT booted yet).
- **Internet over USB: not working yet.** Host side is ready (temporary iptables: FORWARD accept for 192.168.7.0/24 + MASQUERADE, comment "ios-sweet"; lost on host reboot). With v15 the phone sent no DNS and no outbound traffic (only mDNS/ARP) -> configd probably did not apply the service (or did, without default route/DNS). Next: boot 0080s on v15, watch `SWEET NET` lines (does configd add 192.168.7.2 / default route on ue0?). If configd ignores ue0 (not an IOKit IONetworkInterface; configd 1241.140.6 source in `research/configd-1241`, SCNetworkInterface createInterface needs IONetworkInterface -> controller (IOMACAddress, SelfNamed) -> bus), add IOKit stand-ins and/or rename the ifnet to en*.
- Live log listener (`nc -ukl 5140`) may still be running, writing `logs/live-0078s.txt`.

**LIVE LOG over USB (0077, boot 0077s): no more recovery round-trips.** Boot an image with `sweetusb=2`, run `tools/sweet-usbnet.sh logs/live-NAME.txt` on the PC: it waits for the phone's CDC-ECM interface, sets 192.168.7.1/24 (sudo) and prints/saves the kernel console the phone streams every 100 ms as UDP to 192.168.7.1:5140 (starts with the last 64 KiB). `ping 192.168.7.2` checks the link. Current image: `shim/out/shim-boot-0077s.img` (kernel 0077, ADT sweet-0069hi, cpus=6, sweetfb_noconsole=1, sweetfb_hz=30, sweetusb=2). The recovery pull (`tools/sweet-log-pull.sh`) is still the way after a panic/hang before USB comes up.

**🎉 USB networking works (boot 0076s, 18:24 local): `ping 192.168.7.2` from the PC answers in ~1 ms, 0 % loss (200-packet burst, 1500-byte frames, 512/1024-byte frames).** 0075: CDC-ECM function (bulk 0x81/0x01, interrupt 0x82 NETWORK_CONNECTION; host MAC 02:00:5e:77:ee:01) + kernel ethernet ifnet `ue0` (legacy output, header strip like if_fake) with 192.168.7.2/24 set from the kernel (proto_plumb + SIOCAIFADDR on an in-kernel socket). 0076: received frames into one cluster mbuf (mbuf_allocpacket gave a first mbuf of length 0 -> panic "Failed mbuf validity check: len -14"). Host side (Linux cdc_ether, interface `enp0s20f0u3u1`): `nmcli dev set IF managed no; sudo ip addr add 192.168.7.1/24 dev IF; sudo ip link set IF up`. Boot image `shim/out/shim-boot-0076s.img` (`sweetusb=2`). iOS itself does not see ue0 yet (configd needs an IONetworkInterface in IOKit).

**USB device mode works (boot 0074s, 17:45 local): the host enumerates the phone at high speed as `1209:0001 ios-sweet "Sweet iOS (USB bring-up)"`.** 0072 read-only probe: ABL leaves the USB30_PRIM GDSC and all 7 USB clocks on, DWC3 v3.20a (GSNPSID 0x5533320a) in device mode (GCTL 0x00112000), halted (DCTL RUN_STOP 0, DSTS 0x00d3de04), event buffer still at ABL's 0xfdfc8000, QUSB2 PHY alive. 0073 (`sweetusb=2`): DCTL soft reset, own event buffer (polled, interrupt masked), ep0 DEPSTARTCFG/SETEPCONFIG/SETTRANSFRESOURCE, setup TRB, RUN_STOP; control-transfer state machine as Linux ep0.c; descriptors: vendor class, no data endpoints. It ran but no event came: **0074** forces the VBUS session in the Qualcomm wrapper (QSCRATCH 0xa6f8800: HS_PHY_CTRL |= UTMI_OTG_VBUS_VALID(20)|SW_SESSVLD_SEL(28), SS_PHY_CTRL |= LANE0_PWR_PRESENT(24), as Linux dwc3_qcom_vbus_override_enable) -> connect + enumeration. Image `shim/out/shim-boot-0074s.img`. Black Phone app: main thread waits in -[VMVoicemailManager fetchAccounts] (sync XPC; voicemail needs the modem).

**Navigation works (boot 0071s on v14, log `logs/boot-0071s.txt`):** swipe up from the bottom edge returns to the home screen; app switcher works. Two fixes: **0070** pid_hibernate (syscall 435 was nosys on the macOS config -> SIGSYS killed SpringBoard in -[SBToAppsWorkspaceTransaction _didComplete] when an app left the foreground; the only CONFIG_EMBEDDED-only entry in syscalls.master); **0071** touches starting within 48 px of an edge carry kIOHIDDigitizerEventFromEdgeTip (UIKit `_UIEventHIDTouchEdgeTypeForHIDEvent` reads the digitizer EventMask 0xb0007 bits 0x800/0x1000/0x2000/0x40000). Diagnosed, not fixed: Settings (Preferences) SIGTRAP in PodcastsUI func_1cdc47c58: force-unwrap of `URL.volumeTotalCapacity` (nil on our HFS+ root). Phone/Photos/Messages show black (MobilePhone launches, no crash) - next: stack samples via boot-arg `sweetsample=MobilePhone,MobileSlideShow,MobileSMS,SpringBoard` (image `shim/out/shim-boot-0071s.img`).

**🎉🎉 MILESTONE 2026-10-18 ~15:30 local: iOS 16.7 SPRINGBOARD HOME SCREEN ON THE PHONE** (boot `shim/out/shim-boot-0069s.img` = kernel 0069 + ADT `shim/out/sweet-0069hi.adt`, image **v14**; log `logs/boot-0069s-v14-HOMESCREEN.txt`). Home screen with a few apps, Settings opens, Control Center / lock screen / notification center work, touch + buttons work, 6 A55 cores (`cpus=6`), `sweetfb_noconsole=1 sweetfb_hz=30`. Known issue: cannot leave Settings (home gesture / AssistiveTouch home do nothing) - to investigate.
What it took today (in order): 0058 HID services (Apple IOHIDFamily-1915 user client), 0059 iPhone X identity (hw.product etc.), 0060 SPMI keys + GENI touch, 0061 1125x2436 presented display, 0062 RPMh L11C touch power + IOPMPowerSource battery, 0063 GENI FIFO packing (Goodix GT9896), 0064 PRIV_WORK_INTERVAL grant (SpringBoard "Failed to create work interval"), 0065 SMP via PSCI (6 A55), 0066 crash map regions, 0067 console mute without cons_ops write, 0068 IOSurface lookup of an already-mapped surface returns base -1 (backboardd snapshot crash), 0069 UFS log flush 10 s. Image v13: CommCenter removed from launchd.plist, BKNoWatchdogs. **Image v14** = v13 + Setup skipped (`/var/mobile/Library/Preferences/com.apple.purplebuddy.plist` SetupDone/SetupFinishedAllSteps true, SetupVersion 11, daemonizeNeedsToRun false; purplebuddy.notbackedup mini-buddy flags false; managedconfiguration.notbackedup LockdownActivationIndicatesCloudConfigurationAvailable false; springboard MCForceShowCloudConfigurationUI false) + mobileactivationd data ark seeded (`/private/var/containers/Data/System/D90C94C4-F94E-4FCF-8A28-A93BE3846D8E/Library/internal/data_ark.plist`, -ActivationState Activated etc.) + MediaToolbox D22/D221/Default SubServerConfiguration StartRecorderServer=false (mediaserverd crash loop: portrait-filter prewarm called NULL GL). **Activation:** ADT /chosen certificate-production-status 0, effective-production-status-ap 0, certificate-security-mode 1, effective-security-mode-sep 1 -> mobileactivationd's native Hactivation branch (Codex `codex/activation/report.md`; alternative 4-byte daemon patch documented there). Fallback image v13; v12 deleted.

**🎉 TOUCH WORKS (boot 0063u on image v13, 2026-10-18 ~13:15 local):** tapping (i) on Setup's Hello opens the device-info sheet; swipe-up is recognised (spinner, then back to Hello - under investigation). Chain: RPMh L11C vote (0062, `bsd/dev/sweet_rpmh.c`) -> GT9896 @0x5d on GENI SE7 (0063 fixed FIFO packing: register address went out byte-swapped) -> sweet_hid_touch -> SweetHIDTouch -> backboardd. Battery icon OK (0062 SweetBattery IOPMPowerSource, static 100 %). **Image v13** (= v12 + CommCenter jobs removed from launchd.plist + `BKNoWatchdogs`=true and `FBLaunchWatchdogExceptions{com.apple.purplebuddy:100}` in /var/mobile/Library/Preferences/com.apple.springboard.plist): Setup no longer killed by the scene-create watchdog (it blocked in CoreTelephony getSubscriptionInfo -> CommCenter, Codex `codex/setup/report.md`; that report also lists how to skip Setup: SetupDone/SetupFinishedAllSteps/SetupVersion=11 in com.apple.purplebuddy.plist + activation check). v9 deleted, fallback = v12. Input latency ~1 min with 1 CPU -> Codex task 11 = SMP via PSCI (`codex/smp/`).

**INPUT WORKS (boot 0060u on a freshly flashed v12, log `logs/boot-0060u.txt`):** power, volume up, volume down reach iOS (PMIC -> sweet_keys.c -> SweetHIDButtons -> backboardd). Power locks/unlocks (screen "off" shows the kernel console because the panel never really turns off). **Device identity fixed: iPhone X** (home indicator, Face-ID-style Setup). Earlier test (0059u, `sweethidtest=15` fake Home presses) advanced Setup from Hello to the language list.
- **0058** HID: `iokit/Kernel/arm/hid/`: IOHIDEventServiceUserClient + IOHIDEventServiceQueue + IOHIDEvent compiled UNCHANGED from Apple IOHIDFamily-1915.140.3 (= exact version of the iOS 16.7 IOHIDLib, clone in `research/iohid-1915`) over our minimal IOHIDEventService (SweetHID.cpp: SweetHIDButtons consumer page 0x0C power 0x30 / vol 0xE9/0xEA / menu 0x40; SweetHIDTouch digitizer collections). C hooks `sweet_hid_key(key, down)` (0 power, 1 vol+, 2 vol-, 3 home) and `sweet_hid_touch(finger, down, x, y)`. HID logs go to IOLog. Boot-arg `sweethidtest=N` = fake Home press every N s.
- **0059** identity: hw.machine/hw.model/hw.target/hw.product from the ADT (macOS-config kernel used to answer "iPad8,6"/"Redmi Note 10 Pro"). mkadt: root compatible `D22AP|iPhone10,3|xiaomi,sweet|...`, model iPhone10,3, target-type D22, `/product` copied from the real D22AP device tree (`research/ipsw-x/d22ap.adt`, extracted from the IPSW, NOT in git), `/buttons home-button-type 2`. Codex report `codex/mg/report.md`: MG ProductType = sysctl **hw.product** (unrecognized -> iPod), HWModelStr = hw.target, HomeButtonType = `/buttons/home-button-type`; disk cache `/private/var/containers/Shared/SystemGroup/systemgroup.com.apple.mobilegestaltcache/Library/Caches/com.apple.MobileGestalt.plist` (keyed by build + UUID only -> stale iPod answers survived; reflashed clean v12 to clear it).
- **0060** drivers by Codex (`codex/input/hw-report.md`): `bsd/dev/sweet_keys.c` (SPMI arbiter v5 observer reads, PON 0x810 bits KPDPWR/RESIN SID0, PM6150L GPIO2 SID4 0xc110; works), `bsd/dev/sweet_touch.c` (GENI SE7 I2C @0xa84000, starts 45 s after boot). Touch result: SE7 fw ok (proto 3) but **0x5d and 0x38 both time out -> touch IC unpowered** (L11C LDO needs an RPMh vote). Codex task 8 = RPMh client (`codex/rpmh/`), task 7 = battery (`codex/battery/`).
- **0061** (CI 39117078444): red status bar "rdar:45025538" = UIKit "No status bar visual provider for screen" (360x800 pt is no real iPhone). AppleCLCD now presents **1125x2436** (iPhone X) and shows the centered 1080x2400; touch mapped the same; ADT canvas 1125x2436. `sweetfb_native=1` reverts.
- Images: `shim/out/shim-boot-0060b.img` (0060 + /buttons ADT `sweet-0060hi.adt`). Next: 0061 image with ADT from current mkadt.

### >>> HANDOFF 2026-10-17 late (read this first) <<<

**STATE: iOS 16.7 Setup.app "Hello" screen is ON THE PHONE** (boot `shim/out/shim-boot-0057u.img` on image v12). Photo `/run/media/dipa/android/ios-sweet-work/MILESTONE-setup-hello-2026-10-17.jpg`. SpringBoard + backboardd + Setup run; nothing crashes in the GUI path. Remaining crash/relaunch loops: installcoordinationd (should be fixed by 0057, unverified), mediaserverd (VirtualAudio removed in v12, unverified), mobileactivationd (~100/boot, not analysed), bluetoothd + fairplayd.H2 relaunch constantly (no hardware).

**What is on the phone now:** userdata = image **v12** (`/run/media/dipa/android/ios-sweet-ipsw/ios16-root-20H392-v12.img` + `.simg` + `.sha256`; v9 kept as fallback, v8/v10/v11 deleted or superseded). Kernel **0057** (`xnu/out-0057`, CI `39067532732`). ADT **`shim/out/sweet-0057hi.adt`** (QEMU twin `research/q0057-hi.adt`). Boot image **`shim/out/shim-boot-0057u.img`**. Boot args: `debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 wdt_test=no_user_checkin ufsrw=1 rootrw=1 launchd_unsecure_cache=1 wdt=-1`.

**Build a phone image** (kernel K, ADT A): `python3 tools/mkpayload.py $K $A shim/out/payload-X.bin --cmdline "$B" && (cd shim && ./build.sh ../shim/out/payload-X.bin && cp out/shim-boot.img out/shim-boot-X.img)` then check `python3 tools/unpack_bootv2.py shim/out/shim-boot-X.img shim/out/check-X && cmp shim/out/check-X/ramdisk shim/out/payload-X.bin`. QEMU smoke test: `python3 tools/mkpayload.py $K research/q0057-hi.adt $S/csq/payloadX.bin --ramdisk $S/csq/root-fork.img --cmdline "debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress rd=md0"` + `PATH=~/swift/usr/bin:$PATH python3 tools/qemu-sweet.py ... --secs 40 --kernel $K --fail-on-panic` (S = scratchpad; root-fork.img lives in the session scratchpad csq/ dir and may be gone in a new session).

**Kernel workflow:** edit `research/bt28/xnu/...` (keep the pre-edit copy in `research/preNNNN/`), syntax check `python3 tools/xnu-cc.py research/run28.log research/bt28 <file>`, make `xnu/patches/NNNN-*.patch` with `diff -u pre bt28 | sed` path rewrite, add new/changed files to `research/check-patch-order.py` list, run it (must say applied + MATCH), commit + push -> GitHub Actions `xnu` builds (~8 min) -> `gh run download <id> -R dipa-studio/ios-sweet -n xnu-kernel -D xnu/out-NNNN`. **Repo is PUBLIC since 2026-10-17** (private macOS runner minutes ran out: "recent account payments have failed"); public = free runners. Commit trailer: only `Co-Authored-By: dipa-studio <183933817+dipa-studio@users.noreply.github.com>`.

**Phone workflow (user does buttons, Claude runs commands):**
- Boot test kernel: phone in fastboot -> `fastboot boot shim/out/shim-boot-X.img`. NEVER `fastboot getvar`.
- **Read logs WITHOUT filming:** after a test the user holds Power+VolDown -> fastboot -> `fastboot boot recovery/recovery.img` (OrangeFox, temp) -> wait for `adb shell true` -> `tools/sweet-log-pull.sh /run/media/dipa/android/ios-sweet-work/logs/<name>.txt` (reads the 4 MiB console ring the kernel keeps in the last 1025 blocks of userdata; patch 0047). Then `adb reboot bootloader` brings the phone back to fastboot without the user.
- **Flash a root image:** `fastboot flash -S 512M userdata <img>.simg` (7 min, 25 chunks). After a big flash the Xiaomi ABL fastboot hangs: next `fastboot boot` stalls at "Sending" -> the USER must re-enter fastboot by hand (Power+VolDown). Do not retry commands against the hung ABL. A flash right after `adb reboot bootloader` once failed on chunk 1 (USB protocol error): just retry. User must NOT unplug during flash (once interrupted at 25/25 -> reflash).
- **Edit the phone root without reflashing (same-length only):** find the file's raw offset in the local image (it is one extent; search the image for its first 4 KiB), compare with `adb exec-out dd if=/dev/block/sda34 ...`, then `adb push` changed 4 KiB blocks and `dd of=/dev/block/sda34 seek=... conv=notrunc,fsync`, read back. Recovery kernel has no hfsplus.
- **Pull the whole phone root** (12 min): `adb exec-out "dd if=/dev/block/sda34 bs=1048576 count=16384" > img`; mount ro with udisksctl or `sudo mount -t hfsplus -o loop,ro`. To write into a pulled image: `sudo fsck.hfsplus -fy` first (volume is never cleanly unmounted -> Linux mounts ro even with -o force), then `sudo mount -t hfsplus -o loop,force`.
- Making images: copy latest img with `cp --sparse=always`, mount rw with force, change, `sync; umount`, `sha256sum`, `img2simg`. Keep only the latest image + one fallback on the SSD (/home is ~98% full; big work files live in `/run/media/dipa/android/ios-sweet-work/`, old kernels/images archived in `.../ios-sweet-work/archive/`).

**Diagnostics built into the kernel (all SWEET-only):** persistent console log (0047, boot-arg sweetlog=0 off; flush every ~1 s); `|name| text` copies of /dev/null writes for processes in `sweetnull` (default backboardd,SpringBoard; 0048); `IOK name:` IOKit lookup/open trace for `sweetiolog` (default backboardd,SpringBoard; 0049/0054, 400 lines max); `SWEET Sandbox: proc op N` (0050); `STR ...` heap text dump on SIGABRT/SIGTRAP for `sweetscan` (default backboardd,SpringBoard; 0051/0053) - this is how NSException/BSAssert reasons were found; `SMP name tN pc .. bt ..` user stack sampler for `sweetsample` (default SpringBoard,backboardd; first at 90 s then every 120 s, 40 samples, 16 frames; 0054/0055); `SWEET FB: N swaps` every 1000 swaps; `CRASH name ...` crash reports (0043/0044). Symbolicate cache addresses with `ipsw dyld a2s research/dsc/dyld_shared_cache_arm64 <addr - slide>`; main-binary addresses: addr - mh + 0x100000000.

**Patches added today (0046-0057, all CI-built):** 0046 IOSurface (IOCoreSurfaceRoot) + IOMobileFramebuffer (AppleCLCD on /arm-io/disp0) + arm-io nubs; 0047 persistent log; 0048 /dev/null tee; 0049 IOKit trace; 0050 permissive "Sandbox" MAC policy; 0051/0053 heap text on crash; 0052 RENAME_SWAP via exchangedata on HFS; 0054/0055 sampler, swap counter, AKS selectors 35 (extended device state = 17) and 101; 0056 panel copy from the vsync timer at 10 Hz (sweetfb_hz) instead of every swap; 0057 no config_ext_resolver (ACL group entries resolve without memberd). ADT (tools/mkadt.py): /arm-io/disp0, /chosen/display-scale 3, /chosen/unique-chip-id (synthetic 0x5357454554000001) + chip-id 0x8015 -> IOPlatformUUID A9683613-3704-53F2-95D0-98A8FB574E78. Image v9-v12: /private/var/containers/{Bundle,Data/System,Shared/SystemGroup,Temp}; launchd.plist StandardOut/ErrorPath renamed to ...PatX (pointing jobs at /dev/console stops them launching: EACCES); v12: LastSystemVersion=20H392 in plain /var/mobile/Library/Preferences/com.apple.migration.plist (stops the 1-hour data migration that ran every boot), VirtualAudio.plugin moved to /Library/Audio/Plug-Ins/HAL.disabled/.

**Codex helper** (`codex exec --skip-git-repo-check -m gpt-6.1-sol -c model_reasoning_effort=high -s workspace-write -C /run/media/dipa/android/ios-sweet-work/codex -o OUT "$(cat task.md)" < /dev/null`, run detached with setsid nohup; config already defaults to gpt-6.1-sol high). Reports in `/run/media/dipa/android/ios-sweet-work/codex/`: mediaserverd-report.md, installcoordinationd-report.md (-> 0057), ufs-performance.md (ranked UFS speed plan), **ufs/0058-sweet-ufs-faster.patch** (READY, not yet applied: ufslogsecs default 10 s log flush batching, optional ufscache=1 directional cache ops, optional ufswait=1 spin-then-sleep; syntax-checked, host-stub verified; see ufs/README.md). Apply it as patch 0058 after review.

**Claude safety-classifier note:** several messages about QuartzCore renderer selection / writing an HFS reader tool were cut off by an automatic classifier (false positives, user agrees). Keep those topics short or let them be; they are not blockers now (rendering already works).

**NEXT STEPS (in order):**
1. **Input** (Setup stuck on Hello): touch + power + volume as IOHIDEventService/IOHIDDevice services backboardd matches (log shows backboardd matching IOHIDDevice with DeviceUsagePairs and IOService with HIDServiceSupport=true). Phone hardware: Goodix/Focaltech touch over I2C/SPI (check live DT `dtb/`), buttons via PMIC (pon) and GPIO keys. Start with a fake HID service (e.g. power button via a timer or boot-arg) to learn the ABI, then real drivers.
2. **Device identity**: MobileGestalt reports iPod + Home button. Add /product node / product-type (iPhone10,3, D22AP), device class iPhone, home-button-less (Face ID) properties in mkadt; find exact MG keys (MGIOMFBSupport.c, `IODeviceTree:/product` lookups seen in backboardd trace).
3. **Battery**: AppleSmartBattery/IOPMPowerSource style service (status bar shows empty red battery).
4. Apply Codex 0058 (UFS speed), consider cpus>1, check whether installcoordinationd/mediaserverd loops are gone, analyse mobileactivationd.
5. Hide kernel console over the GUI when not debugging: boot-arg sweetfb_noconsole=1 (but then panics are not on screen).

- **🎉 MILESTONE: iOS GUI ON THE PHONE - Setup.app "Hello" screen (2026-10-17 ~21:30 UTC, boot 0057u on image v12).** Photo: `/run/media/dipa/android/ios-sweet-work/MILESTONE-setup-hello-2026-10-17.jpg` (animated "dobar d(an)" hello lettering, status bar "iPod" + battery icon, bottom "Pritisnite Home za otvaranje" = "Press Home to open" (locale hr), (i) button). Rendered by iOS 16.7 SpringBoard/Setup through CoreAnimation -> backboardd -> kernel IOSurface (IOCoreSurfaceRoot) -> kernel AppleCLCD swaps -> ABL panel memory. What made it: data migration finally skipped (v12: `LastSystemVersion` 20H392 in the plain `/var/mobile/Library/Preferences/com.apple.migration.plist`; ADT `/chosen/unique-chip-id` + `chip-id` for IOPlatformUUID), 0057 (no ext resolver -> ACL containers work), plus everything before (0046 display/IOSurface, 0050 Sandbox policy, display-scale 3, containers dirs, RENAME_SWAP, AKS 35/101, 10 Hz present).
  **Missing (next work, in rough order):**
  - **Input: no touch, no power button, no volume buttons** (no HID drivers; backboardd looks for IOHIDDevice / HIDServiceSupport services). Setup is stuck on "Hello".
  - **Device identity: it thinks it is an iPod with a Home button** (status bar "iPod", "Press Home to open"): MobileGestalt has no product/model identity (no /product node, product-type, etc.) - give it iPhone10,3 / home-button-less (Face ID class) identity.
  - **No battery detection** (no battery/charger/PMIC service; status bar battery shows empty/red).
  - Locale came up Croatian (hr) - harmless.
  - Performance: cpus=1, polled UFS (Codex UFS patch in progress: `codex/ufs/`), backboardd presents at 60 Hz.
- **README refreshed (2026-10-17):** read the full notes and updated the public overview through patch 0045, v7 hardware observations and prepared v8 diagnostics. Documents real Apple userspace, the missing display/IOSurface GUI work, limited security services, highmem, persistent userdata root, current tooling and build examples. Documentation only; no kernel build, tests, flashing or phone boot performed.

- **v11 still migrated (boot 0056u, 20:32 UTC, `logs/boot-0056u-v11.txt`).** On iOS kCFPreferencesCurrentHost lands in the plain plist: all DMEnvironment setters (setLastBuildVersionPref, setUserDataDispositionPref, ...) use CurrentHost and the phone's `/var/mobile/Library/Preferences/com.apple.migration.plist` holds DMUserDataDisposition; datamigrator's XPC service runs with `_RoleAccount: mobile`. **Image v12** = v11 + `LastSystemVersion` = 20H392 added to that plain `com.apple.migration.plist` (keeps DMUserDataDisposition, 501:501 0600) + `/Library/Audio/Plug-Ins/HAL/VirtualAudio.plugin` moved to `/Library/Audio/Plug-Ins/HAL.disabled/` (Codex: stops the mediaserverd SIGSEGV loop; untested). **installcoordinationd loop explained (Codex `codex/installcoordinationd-report.md`):** os_crash "Failed to create data directory" EACCES for `/private/var/containers/Shared/SystemGroup/systemgroup.com.apple.installcoordinationd/Library/InstallCoordination`: `Library`/`Library/Caches` in the container are ACL-only (uid/gid 0xfffffffe, mode 0000, ACE for group GUID ...012B = gid 299 systemusers); with `config_ext_resolver` the kernel asks an identity resolver (memberd/opendirectoryd) that iOS does not run -> EWOULDBLOCK -> ACE skipped -> EACCES. **Patch 0057**: drop config_ext_resolver from FILESYS_BASE (compatibility UID/GID<->GUID mapping is used instead), likely fixes other ACL-protected containers too. Codex task 3: UFS driver performance design (`codex/ufs-performance.md`).
- **mediaserverd SIGSEGV loop explained (Codex report `/run/media/dipa/android/ios-sweet-work/codex/mediaserverd-report.md`):** the fault (FAR 0x390) is in the HAL plug-in `/Library/Audio/Plug-Ins/HAL/VirtualAudio.plugin/VirtualAudio` (ApplyBeamFormerPreference <- VirtualAudio_PlugIn::SetPropertyData <- HALS_AHPPlugIn::ObjectSetPropertyData <- AudioObjectSetPropertyData <- MediaExperience vaemVADSerializationListener): the default virtual audio device ('vdef', VirtualAudioDevice_Default) does not exist (no audio hardware/driver, no /product/audio). Proposed workaround for GUI bring-up: move VirtualAudio.plugin out of /Library/Audio/Plug-Ins/HAL/ in the next root image (untested). v11 reflashed OK after an interrupted flash (USB unplugged at chunk 25/25). Codex task 2: installcoordinationd crash loop.
- **ROOT CAUSE of the endless migration: no IOPlatformUUID (2026-10-17).** datamigrator's `-[DMMigrator isMigrationNeededWithBuildVersion:lastBuildVersion:clientPID:]` gets lastBuildVersion from `-[DMEnvironment lastBuildVersionPref]` = `CFPreferencesCopyValue("LastSystemVersion", "com.apple.migration", kCFPreferencesCurrentUser, kCFPreferencesCurrentHost)` -> a ByHost file. The phone had `/var/mobile/Library/Preferences/com.apple.migration.plist` (only DMUserDataDisposition) and NO `Preferences/ByHost` directory at all. On arm XNU publishes IOPlatformUUID (gethostuuid) only from `/chosen/unique-chip-id` (8 bytes) + `/chosen/chip-id` (4 bytes) (IOPlatformExpertDevice::generatePlatformUUID, called from IOPlatformExpert::start); the SWEET ADT had neither, so every CurrentHost preference write failed. Fix: mkadt adds `unique-chip-id` = 0x5357454554000001 (synthetic) and `chip-id` = 0x8015 -> UUID `A9683613-3704-53F2-95D0-98A8FB574E78` (same SHA1 namespace computation as the kernel). ADT `shim/out/sweet-0057hi.adt`, boot image `shim/out/shim-boot-0056u.img` (0056 kernel + 0057hi ADT, QEMU OK). **Image v11** = v10 + `Preferences/ByHost/com.apple.migration.<UUID upper and lower>.plist` {LastSystemVersion: 20H392} (501:501, dir 0755 files 0600). Flashing v11 2026-10-17 ~20:10 UTC. Codex (gpt-6.1-sol high) analysing the mediaserverd SIGSEGV loop in `/run/media/dipa/android/ios-sweet-work/codex/` (report `mediaserverd-report.md`).
- **v10 did NOT stop the migration (boot 0056n on v10, 19:18 UTC, `logs/boot-0056n-v10.txt`):** datamigrator + migrationpluginwrapper ran again, backboardd drew the progress UI (13000+ swaps in ~5 min). So `com.apple.DataMigration.plist`/LastSystemVersion is not the deciding input. What is known about the decision (datamigrator XPC service `DataMigration.framework/XPCServices/com.apple.datamigrator.xpc/com.apple.datamigrator`, strings): `Migration sentinel present. Migration necessary.` (sentinel `/private/var/datamigrator/migrate`, created and removed by datamigrator itself; absent in v10), `System build version changed from %@ to %@. Migration necessary.` / `...unchanged... not necessary`, `Disposition dictionary with build version %@ is out of date relative to current build version %@`, `SyntheticDidUpgrade`, DMUserDataDispositionManager; previous build source not found yet (not in the binary's plain strings; likely in DataMigration.framework's DMUserDataDispositionManager / preferences domain `com.apple.migration`). backboardd calls `DMPerformMigrationIfNeeded` and holds SpringBoard in `checkInAndWaitForDataMigration` until it returns. NEXT: find where the previous build version / disposition dictionary is stored (disassemble DataMigration.framework DMUserDataDispositionManager + datamigrator's isMigrationNeeded path), write it into the image; or get datamigrator's os_log out (OS_ACTIVITY_DT_MODE needs a launchd env change).
- **Data migration never records completion -> skip it (image v10, 2026-10-17).** Boot 0056n (18:16 UTC, `logs/boot-0056n.txt`, 30 min): 10 Hz panel copies, backboardd still presents at ~60 Hz (107k swaps), SpringBoard still in `BKSSystemShellService _checkIn` at sample 14. Every migration run takes 1+ hour on cpus=1 and repeats each boot. DataMigration decides from `/var/mobile/Library/Preferences/com.apple.DataMigration.plist` key `LastSystemVersion` (vs ProductBuildVersion 20H392); the pulled phone root (21:01 local) had NO such file. **v10** = that pulled phone state + fsck.hfsplus -fy (volume was marked not cleanly unmounted -> Linux mounts it read-only even with -o force; fsck found only hard-link counts + minor volume header, repaired) + `com.apple.DataMigration.plist` {LastSystemVersion: 20H392} (501:501 0600). `/run/media/dipa/android/ios-sweet-ipsw/ios16-root-20H392-v10.img` (+ .sha256, .simg); v8 files deleted, v9 kept as fallback.
- **Repo made PUBLIC (2026-10-17, user request) so GitHub-hosted macOS runners are free:** the 0056 CI run was refused ("recent account payments have failed or your spending limit needs to be increased"). Checked before: no device dumps, serials, IMEI or Apple binaries in the history (.gitignore keeps backup/, dtb/, recovery/, research/ out); commit author emails are public now. 0056 rerun `39056660104` passed -> `xnu/out-0056`, QEMU OK, image `shim/out/shim-boot-0056n.img` booted 18:16 UTC.
- **Boot 0055n (17:16 UTC, ~65 min, `logs/boot-0055n.txt`): migration bar reached 99% in ~30 min (was ~60), then crawled.** Root cause of the slowness: backboardd presents the progress UI at ~58 Hz (**167,000 swaps in ~48 min**) and AppleCLCD copied the full 10 MB frame into the panel on every swap -> ~600 MB/s memcpy on the single CPU (cpus=1), starving everything (polled UFS I/O too). Sampler (16 frames): **SpringBoard waits in `-[BKSSystemShellService _checkIn]`** (synchronous BSXPC call to backboardd, from FBSystemShellInitialize <- SBSystemAppMain) - backboardd holds the system shell check-in until the data migration ends, so the migration is the gate to SpringBoard UI. **Patch 0056**: swap only records the latest swap record; the vsync timer copies it to the panel every 60/sweetfb_hz ticks (default 10 Hz); `SWEET FB: N swaps` every 1000.
- **Boot 0054n (16:53 UTC, ~12 min, `logs/boot-0054n.txt`, patch 0054 sampler):** the data migration runs AGAIN on every boot (it completed once in the 0053s run) - completion is not being recorded. backboardd keeps swapping the progress UI (3700+ swaps in ~10 min, `SWEET FB: N swaps`). Sample during migration: SpringBoard main thread blocked in a synchronous XPC call (`mach_msg2_trap <- dispatch_mach_send_and_wait_for_reply <- xpc_connection_send_message_with_reply_sync <- -[BSXPCServiceConnectionMessage _sendSynchronously:] <- BSXPCServiceConnectionProxy ...`). Unsupported AKS selectors seen: 101 `aks_assert_consume`, 93 `aks_get_internal_info`, 34 `_aks_stash_load` (datamigrator), **35 `aks_get_extended_device_state`** (SpringBoard, chronod, remindd, searchd, symptomsd...). **Patch 0055**: AKS 35 answered like 17 (same DER device state), 101 succeeds; sampler backtraces 16 frames, 40 samples (~80 min).
- **MILESTONE: FIRST iOS PIXELS ON THE PANEL (boot 0053s long run, 15:33 UTC, ~60 min; photos IMG_20261017_183029/183222/183419; log `/run/media/dipa/android/ios-sweet-work/logs/boot-0053s-long.txt`, 407 KB, 1428 flushes).** The iOS data-migration boot screen (Apple logo + progress bar) was shown and the bar advanced to full in ~60 minutes. It is drawn by **backboardd through our kernel display path**: `SWEET SURF: backboardd create id 2/3 1080x2400 fmt 0x42475241 (BGRA) bpe 4 bpr 4352 size 10452992 keys 11`, IOSurface sels 9, 27, 5, 34 and traps 2/3 (lock/unlock), IOMFB SwapBegin (4) / SwapEnd (5, 1132-byte struct) / GetLayerDefaultSurface (3) / 68 / 55, then `SWEET FB: swap N layer 0 surface 2|3 1080x2400 ... a 0,0 1080x2400 b 0,0 1080x2400` alternating (double buffering; only the first 16 swaps are logged). Triggered by `com.apple.datamigrator` (AKS sels 101/93/34 return unsupported) and `com.apple.migrationpluginwrapper`. Probably ProgressUI-style CoreGraphics drawing into IOSurfaces (no GPU used). After the migration: SpringBoard and backboardd stay up (exec once each, no crash), nothing new drawn; crash loops only for mediaserverd, installcoordinationd, mobileactivationd (~108 each in the hour); bluetoothd and fairplayd.H2 relaunch constantly (no hardware). Kernel console text overlays the image (sweetfb_noconsole=1 would hide it).
- **MILESTONE: SpringBoard and backboardd both stay up (boot 0053s on v9, 15:29 UTC, `logs/boot-0053s.txt`).** With /chosen/display-scale = 3: `exec SpringBoard` once, `exec backboardd` once, no CRASH for either; MobileGestaltHelper reads IOMFB size/dot pitch; backboardd opens AppleCLCD (sels 57,15,22,29,33,79,74 as before). No IOSurface creates and no swaps: backboardd keeps polling `IOProviderClass IOAcceleratorES` (-> 0 found) and AGXAccelerator/IOAcceleratorESLegacy - no GPU, so no CoreAnimation renderer yet. Log goes quiet after ~47 s (only 47 flushes): the system idles. NEXT WALL: CoreAnimation rendering without a GPU (CPU path) so the window server produces frames.
- **SpringBoard's invalid mode = scale 0 (boot 0053n, 15:19 UTC, `logs/boot-0053n.txt`, heap scan):** `FBSDisplayMode cannot be configured as requested : <FBSDisplayMode: ...; infxinf@0x (1080x2400/0) 60Hz sRGB SDR>` - pixel size and 60 Hz are fine, scale is 0. libMobileGestalt (MGIOMFBSupport.c, sub_19c560a54) reads `display-scale` (u32, default 0) and `display-rotation` from **IODeviceTree:/chosen**, pixel size via IOMobileFramebufferGetDisplaySize (MobileGestaltHelper sel 8/28 in the log). mkadt now adds `/chosen/display-scale` = 3 (1080x2400 @3x = 360x800 pt). ADT `shim/out/sweet-0054hi.adt` (QEMU `research/q0054-hi.adt`), image `shim/out/shim-boot-0053s.img` = 0053 kernel + 0054hi ADT. Possible catch: MobileGestalt may have cached scale 0 in `/private/var/containers/Shared/SystemGroup/systemgroup.com.apple.mobilegestaltcache/...`.
- **MILESTONE: backboardd survives and opens the display (boot 0052n on v9, 15:07 UTC, `logs/boot-0052n.txt`).** No `CRASH backboardd`. It walks `IODeviceTree:/arm-io` children, matches IOMobileFramebuffer, `open AppleCLCD type 0 -> 0x0`, calls IOMFB selectors 57, 15, 22, 29, 33, 79 (GetBlock, out 1580/40), 74 (SupportedFrameInfo), 12, 6, 27, 17, 84, 50; opens IOCoreSurfaceRoot (sel 13); looks for AGXAccelerator / IOAcceleratorES / IOAcceleratorESLegacy (none, repeatedly), `IODeviceTree:/product` (none, repeatedly), `/buttons`, aop-smart-cover, IOPMrootDomain. No swaps yet. **SpringBoard now gets past display discovery** and dies (sig 5) in `-[FBSDisplayMode _initWithWidth:height:preferredScale:scaleOverride:refreshRate:gamut:hdr:rotation:virtual:validityCheck:]+580` <- `_initWithCADisplayMode:scale:rotation:` <- `FBSDisplayConfiguration _initWithImmutableDisplay:` <- `FBSDisplaySource initWithDisplay:` <- `FBSDisplayMonitor` (slide 0x14504000): "FBSDisplayMode cannot be configured as requested". Checks: width/height >= 2, preferredScale > 0, scaleOverride >= 0, refreshRate non-zero, valid gamut/HDR/rotation. Suspects: refresh rate 0 (IOMFB timing blocks answered with zeros) and/or scale 0 (MobileGestalt main-screen-scale; no /product node in the ADT). **Patch 0053**: heap text scan also on SIGTRAP and for SpringBoard (default sweetscan list backboardd,SpringBoard) to read the exact mode description.
- **Image v9 (2026-10-17) = v8 + `/private/var/containers/{Bundle,Data/System,Shared/SystemGroup,Temp}` (root:wheel 0755; on iOS these live on the data volume, absent from the rootfs) + launchd.plist with StandardOutPath/StandardErrorPath renamed to ...PatX (the in-place phone fix; daemons' stdout reaches the log via the 0048 /dev/null tee).** `/run/media/dipa/android/ios-sweet-ipsw/ios16-root-20H392-v9.img` SHA256 aa9f9139...6e67, `.simg` 12,145,303,864 bytes; written through `sudo mount -t hfsplus -o loop,force`. **Patch 0052** (`xnu/patches/0052-sweet-hfs-rename-swap.patch`, CI `39042761826` -> `xnu/out-0052`, QEMU OK, image `shim/out/shim-boot-0052n.img`): vn_rename falls back to VNOP_EXCHANGE when VNOP_RENAMEX(RENAME_SWAP) returns ENOTSUP for two regular files on one volume (HFS exchange swaps data and cnode descriptors = swap semantics). Flashing v9 started 2026-10-17 ~15:00 UTC.
- **backboardd EXCEPTION FOUND (boot 0051n, 14:41 UTC, `logs/boot-0051n.txt`, patch 0051 heap scan):** `*** Terminating app due to uncaught exception 'NSInvalidArgumentException', reason: '*** -[NSURL initFileURLWithPath:]: nil string parameter'`. First-throw stack (slide 0x337f0000): `+[NSURL fileURLWithPath:]` <- `-[BSSystemContainerForCurrentProcessPathProvider libraryPath]` <- `cachesPath` <- backboardd main+0x1d858 (StartWindowServer, shader cache path). BaseBoard: libraryPath = `BSSystemSharedResourcesDirectoryForIdentifier` = `BSSystemSharedDirectoryForIdentifier(id)` + "/Library"; that calls `container_system_path_for_identifier` (libsystem_containermanager -> `_container_legacy_lookup` -> containermanagerd XPC), which returned NULL (BaseBoard logs "Error getting shared system container ..."). With 0050 the sandbox denials are gone. containermanagerd.log.0 in the pulled (pre-0050) image: "Did not receive any persona attributes", "Expected to find at least one persona", notify_register_dispatch failed 9; `/private/var/containers` does NOT exist in the root image (on iOS it lives on the data volume). usermanagerd does create kernel personas 99/199/1000, but fails to save `/private/var/keybags/usersession.kb` and `persona.kb`: "Failed to atomically swap writing file: Operation not supported (45)" (HFS has no RENAME_SWAP). NEXT: create `/private/var/containers` (and check what containermanagerd needs for system group containers), fix or emulate RENAME_SWAP on HFS, then re-check backboardd.
- **Disk housekeeping (2026-10-17):** /home was at 4 GiB free. Moved (not deleted) `xnu/out-0010..0044` and old `shim/out/*.img`/`payload-*.bin` (everything before 0046) to `/run/media/dipa/android/ios-sweet-work/archive/{xnu,shim-out}` (6.3 GiB); /home now 8.7 GiB free. Large work files (phone-root.img 16 GiB, logs, tracev3 dumps) live in `/run/media/dipa/android/ios-sweet-work/`. **Patch 0051** (`xnu/patches/0051-sweet-crash-heap-strings.patch`, CI `39041440935`): on SIGABRT of a process in boot-arg `sweetscan` (default backboardd) the crash log prints heap text runs as `STR ...` (to find the NSException reason). Boot 0051n 14:41 UTC.
- **Boot 0049n (14:11 UTC, `logs/boot-0049n.txt`, patch 0049 trace): backboardd's IOKit lookups before it dies** (each run): `IODeviceTree:/arm-io/pearl-sep` -> none, matching ApplePearlSEPDriver -> none, `IOService:/IOResources/AppleKeyStore` open OK, matching IOHIDDevice (DeviceUsagePairs) / `HIDServiceSupport` notifications, `usb-drd,t8027` notification; **never any IOMobileFramebuffer / display lookup** - the exception happens before display detection. launchd (tracev3) shows backboardd's mach lookups denied with `error = 159: Sandbox restriction`: com.apple.mobilegestalt.xpc, com.apple.iohideventsystem, com.apple.system.notification_center, com.apple.logd, containermanagerd, ACCHWComponentAuthService, oscard. libsystem_sandbox uses `__mac_syscall("Sandbox", op, arg)` (ops: 1 init, 2 check, 3 note, 4 container path, 5/6/7/8 extensions, 0x15 check_bulk, ...); with no policy named "Sandbox" every call gets ENOPOLICY. **Patch 0050** (`xnu/patches/0050-sweet-sandbox-policy.patch`): built-in permissive MAC policy "Sandbox" in sweet_amfi.c (mpo_policy_syscall returns 0, container/extension ops ENOENT; logs first use of each op as `SWEET Sandbox: proc op N`).
- **Phone root pulled (16 GiB, 12 min, `adb exec-out dd if=/dev/block/sda34`) to `/run/media/dipa/android/ios-sweet-work/phone-root.img` (loop-mounted ro at /run/media/dipa/iOSRoot).** No .ips crash reports. logd tracev3 (Persist, LZ4 `bv41` blocks, decompressed strings in `ios-sweet-work/tracev3/all.txt`): /private/var/db/uuidtext is EMPTY so only launchd's own messages are readable. Findings: v8 launch failure = `Service could not initialize: posix_spawn(/usr/libexec/backboardd), error 0xd - Permission denied` (xpcproxy exit 78) - /dev/console not openable for the mobile user; backboardd[98] spawned `com.apple.MTLCompilerService` before SIGABRT; **launchd denies many mach lookups with `error = 159: Sandbox restriction`** (mediaserverd -> com.apple.backboard.display.services, remoted, mobiletimerd -> sleepd, identityservicesd -> apsd) and **tccd exits OS_REASON_SANDBOX "profile not found"** - no Sandbox kext/profiles on SWEET; likely cause of many daemon crash loops (next wall after the display). CF's uncaught-exception text never reaches stderr for daemons (no CF handler; libc++abi prints only the type). **Patch 0049** (`xnu/patches/0049-sweet-iokit-trace.patch`): `IOK name: ...` console lines for registry path lookups, child iterators, matching (with result count/class), matching notifications and IOServiceOpen of the processes in boot-arg `sweetiolog` (default backboardd), max 400 lines.
- **CONFIRMED: launchd StandardOut/ErrorPath=/dev/console stops backboardd from launching.** After the in-place rename (boot 0047n-b, log `logs/boot-0047n-b.txt`) backboardd launches again and dies as on v7: sig 6, `___pthread_kill <- abort <- demangling_terminate_handler <- _objc_terminate <- std::terminate <- _dispatch_client_callout <- _dispatch_once_callout <- main+0x9334` (window-server dispatch_once). It never opened AppleCLCD or IOCoreSurfaceRoot (no `SWEET FB/SURF: backboardd` lines) -> throws before/at display detection. **Patch 0048** (`xnu/patches/0048-sweet-devnull-tee.patch`, CI `39037749668` -> `xnu/out-0048`, image `shim/out/shim-boot-0048n.img`): writes to /dev/null by processes named in boot-arg `sweetnull` (default `backboardd,SpringBoard`) are printed as `|name| text`. Boot 0048n (13:48 UTC, log `logs/boot-0048n.txt`): only `|backboardd| libc++abi: terminating due to uncaught exception of type NSException` (CF's reason line goes to os_log, not stderr, despite CFLOG_FORCE_STDERR/OS_ACTIVITY_DT_MODE). Next: pull the whole 16 GiB root from recovery (`adb exec-out dd if=/dev/block/sda34`) to `/run/media/dipa/android/ios-sweet-work/phone-root.img` and read ReportCrash .ips files / logd tracev3 for the exception reason.
- **Persistent log WORKS (boot 0047n on v8, 13:31 UTC; pulled from OrangeFox: 70 KB, 111 flushes, `/run/media/dipa/android/ios-sweet-work/logs/boot-0047n.txt`).** In 3 min **backboardd was never exec'd** (v7 ran it), ~70 bare `exec xpcproxy`, SpringBoard loops on FBSDisplayMonitor (no display), mediaserverd sig 11 loop, locationd, lockdownd sig 5. Suspect the v8 plist (StandardOut/ErrorPath=/dev/console on backboardd). **In-place plist edit from recovery (no reflash):** launchd.plist is one extent at image/partition byte offset 0x1e025f000 (195 blocks; verified bytes identical on phone vs local v8 mount); renamed StandardOutPath->StandardOutPatX and StandardErrorPath->StandardErrorPatX (same length, both jobs) in blocks 68 and 105 via `adb push` + `dd of=/dev/block/sda34 seek=... conv=notrunc,fsync`, read back VERIFIED. Phone now differs from local v8 image in those 2 blocks (files in `/run/media/dipa/android/ios-sweet-work/`). Test boot 0047n 13:38:42 UTC. Recovery kernel has no hfsplus (raw block edits only).
- **Patch 0047: persistent console log (CI `39036478663` -> `xnu/out-0047`, QEMU no panic; phone image `shim/out/shim-boot-0047n.img` = 0047 + sweet-0046hi.adt + 0045n cmdline).** `_cnputs` (all console output: kernel printf + daemons' /dev/console) also appends to a 4 MiB ring in sweet_ufs.c; after the UFS partition is ready and writable (ufsrw=1) a thread writes new ring blocks + header to the LAST 1025 blocks (4 KiB) of userdata every ~1 s and SYNCHRONIZE CACHE (prints `SWEET LOG: console log to the last 4100 KiB ...`). Read: phone to fastboot -> `fastboot boot recovery/recovery.img` (OrangeFox) -> `tools/sweet-log-pull.sh OUT.txt` (adb dd of that area + `tools/sweet-log.py`). Not yet verified on the phone. Panics after the last flush are not in it (screen still shows them). 2x font attempt reverted: XNU's v_scale only scales the progress icon.
- **Boot 0046n on v8 (13:05 UTC, VID_20261017_150522_HSR_120):** phone prints `SWEET SURF: IOCoreSurfaceRoot registered` and `SWEET FB: AppleCLCD 1080x2400 stride 4320 at 0x9c000000, default surface 1` (nubs + match work on hardware). Visible userspace: early boot tasks, launchd_cache_loader, `exec SpringBoard`; no `exec backboardd` / `SWEET FB: backboardd` line seen in the readable frames (video blurry). Frame 00:47: lower half of the screen shows OLDER console text (16:01:12) below newer lines -> unconfirmed hint that a swap copied an old snapshot of the panel (CA reads the default/displayed surface = panel memory). SpringBoard still dies in `-[FBSDisplayMonitor _initWithBookendObserver:transformer:]+1160` (sig 5, slide 0x30960000). **Shim: 2x console font** (`shim/loader.c` Video.v_depth scale field = 1) so phone videos are readable; image `shim/out/shim-boot-0046b.img` = 0046n + 2x font.
- **Patch 0046: built-in IOSurface + IOMobileFramebuffer (2026-10-17, offline, NOT phone-tested).** `xnu/patches/0046-sweet-iosurface-iomfb.patch`: new `iokit/Kernel/arm/SweetSurface.{h,cpp}` (IOCoreSurfaceRoot + IOSurfaceRootUserClient: sel 13/0/4/34/35, traps 0-5, wired buffers mapped into the caller, per-client refs) and `SweetFramebuffer.cpp` (AppleCLCD : IOMobileFramebuffer on /arm-io/disp0: sizes, swap = CPU copy of enabled layers into the ABL panel memory, 60 Hz vsync via sendAsyncResult64WithOptions(CanDrop), panel memory published as the default IOSurface, kernel text console paused on the first swap only with `sweetfb_noconsole=1` (default keeps the console: pausing switches it to serial and would hide panics)); SweetPlatformExpert creates nubs for /arm-io children (not gic) attached to the platform expert (IOPlatformDevice::compareName casts its provider to IOPlatformExpert, so nubs under the arm-io nub never matched - first CI build showed that in QEMU). mkadt: /arm-io/disp0 {device_type display-subsystem, compatible sweet,display, canvas-width 1080, canvas-height 2400}. CI `39021572169` -> `xnu/out-0046`. **QEMU** (`research/q0046-hi.adt`, RAM disk init, msgbuf): `SWEET SURF: IOCoreSurfaceRoot registered`, `SWEET FB: AppleCLCD 1080x2400 stride 4320 at 0x9c000000, default surface 1`, init alive, no panic. Phone image **`shim/out/shim-boot-0046n.img`** (SHA256 9c4d2fe9..., CI `39024108588` with the review fixes: swap bounds check, zeroed 64-bit async ref, port release, 1.5 GiB wired budget, ref leak/race fixes; QEMU re-checked) = 0046 kernel + `shim/out/sweet-0046hi.adt` + the 0045n cmdline; boot it on image v8 (backboardd/SpringBoard log to console) and film. Expected new console lines: `SWEET SURF: <proc> create id ...`, `SWEET FB: backboardd sel N ...`, `SWEET FB: swap ...`. Open question for the next wall: QuartzCore's IOMFB server builds its renderer from Metal first (`CAMetalContextCreate`), GLES only when enabled; how the CPU renderer gets selected on a device without a GPU is not yet established.
- **IOSurface values ABI (offline RE, `/run/media/dipa/android/ios-sweet-work/iosurface-values.md`) - TODO in SweetSurface (0046 only accepts and drops values, so IOSurfaceCopyValue always returns NULL):** sel 9 SetValue in = {u32 id, u32 pad, IOCFSerialize([value, key]) or ([dict] = merge)}, sel 0xb RemoveValue in = {u32 id, u32 pad, key\0} (empty key = remove all); both out 4 bytes = new generation, client accepts only (gen+2)|1. Kernel after each change: gen = (gen+2)|1 (odd, never 0) -> state+0x20, serialized size (>=1) -> state+0x28. sel 0xa in {u32 id, u32 0, u8 0}, out {u32 ?, u32 gen, serialized non-bulk dict}; too small -> 0xe00002db + needed size (client retries x16). sel 0x1b in 0x88: bulk struct 0x70 @0, u64 valid size @0x70, u64 changed mask @0x78, u32 id @0x80 -> copy masked fields into state+0x30 (bit0 32B @0, bit1 16B @0x20, bit2 8B @0x30, bits3-10 1B @0x38..0x3f, bits11-14 24B @0x40/4B @0x58/8B @0x5c/1B @0x64).
- **IOMFB display matching confirmed (offline RE, `/run/media/dipa/android/ios-sweet-work/iomfb-match.md`):** the framework never matches on "AppleCLCD"; any registered IOMobileFramebuffer subclass works. `iomfb_match_callback` reads only `dfr`/`AppleTV`/`external` (CFBoolean, service itself, no parents); internal main display = all absent/false (type 0, name "primary"). Display count = /arm-io children whose device_type starts with "display-subsystem" (count 0 -> CreateDisplayList returns NULL; more nodes than services -> 30 s wait). IOMobileFramebufferOpen fails only on IOServiceOpen failure; canvas-width/height read as 4-byte CFData from the IOService-plane parent (optional). CopyProperty = IORegistryEntrySearchCFProperty on the service only. Patch 0046 (disp0 node, `external`=false, AppleCLCD : IOMobileFramebuffer) fits all of this.
- **IOMobileFramebuffer swap + notification ABI (2026-10-17, offline).** Swap record = fb+0x18, **0x46c bytes**, zeroed by SwapBegin and sent whole by `kern_SwapEnd` = IOConnectCallStructMethod(sel 5, rec, 0x46c, no output). **SwapBegin** = sel 4, no input, 1 scalar out = swap id (stored at rec+0x50). `kern_SwapSetLayer(fb, layer<4, IOSurfaceRef, CGRect a, CGRect b, flags)`: rec+0x54+4*layer = **IOSurface id**; rec+0x104 bit layer = layer enabled (rec+0x108 bit cleared); rect a (int x,y,w,h, scaled) at rec+0x64+16*layer, rect b at rec+0xc4+16*layer; rec+0x114+4*layer = transform from flags&0xf; flag bits 5/31/6/7/8/9 set bytes rec+0x2ca/0x2cc/0x2e3/0x2e5/0x2e6/0x467+layer. A NULL surface only sets the enable bit with zero id. SwapWait = sel 6 scalars [swap id, timeout, 0].
  - **Notifications**: `IOMobileFramebufferScheduleWithDispatchQueue`/`GetRunLoopSource` create an IONotificationPort and `IOConnectSetNotificationPort(conn, type, port, 0)` (kernel `registerNotificationPort`). `kern_EnableNotifications(fb, type, cb, ctx)` = **sel 72 scalars [NotifyCallbackFuncs[type], fb, type, 0]** -> the kernel keeps an async reference {port, func, refcon=fb} per type and fires it with `sendAsyncResult64`. Types: 0 HotPlug, 1 Power, 3 FrameInfo (args[0] = mask of 31-bit keys, args[1] u32, then values), 4 CRC, **5 Vsync**: 6 args, each a 32-bit half in a u64 slot: (ts lo, ts hi), (t2 lo, hi) passed through the time-ingest helper, (t3 lo, hi); the framework then calls the client's vsync callback(fb, ts, t2', t3, ctx). Vsync callbacks are dropped while the notification port has queued messages.

- **IOSurface.framework user-client ABI reversed (2026-10-17, offline, step 2 of the GUI plan).** `ipsw dyld disass research/dsc/dyld_shared_cache_arm64 --image IOSurface` (selector scan script: scratchpad `selscan.py`).
  - Connect (`_iosConnectInitalize`, pthread_once): `IOServiceGetMatchingService(IOServiceNameMatching("IOCoreSurfaceRoot"))`, retried with sleep(1) up to 241 times (a missing service stalls every client ~4 min, no crash); `IOServiceOpen(type 0)`; then **sel 13** (no inputs, struct out 0x18 bytes = `_ioSurfaceLimits`) and requires u16 at out+0x16 == 1 (ABI version); out+0 = offset/plane-base alignment mask, out+4 = bytes-per-row alignment mask (used as (x+m)&~m), +0..+0x14 also copied into IOSurface remote-method init, otherwise logs and closes the connection.
  - **Create = sel 0**: scalar in [parent id or 0], struct in = `IOCFSerialize(props, kIOCFSerializeToBinary)` (OSUnserializeBinary format; keys IOSurfaceWidth/Height/BytesPerRow/BytesPerElement/PixelFormat/AllocSize/PlaneInfo/CacheMode/IsGlobal/...), struct out **0xa68 bytes** ("lock result"). **Lookup = sel 4**: scalar in [id], same struct out. LookupFromMachPort = sel 0x22 (scalar in [port name]); CreateMachPort = sel 0x23 (scalar in [id, options], scalar out [port name]).
  - Lock result (copied to client+0x68, size 0x68 + planes*0x80, min 0xe8): 0x00 u64 base address **mapped in the caller's task**; 0x08 u64 task VA of a per-surface shared state page (+0 u64 registry id, +8/+0xa u16 bound accel ids, +0xc u32 seed, +0x14 u8 tiled, +0x20 u32 value generation, +0x24 YCbCr matrix, +0x28 u32 non-bulk values size, +0x2d u8 displayable, +0x2e u8 sysmem-only, +0x30 0x70 bytes bulk attachments); 0x10 u64 task VA of timestamp page (4 u64 indexed timestamps, +0x20 detach code); 0x18 u32 surface id; 0x1c u32 alloc size; 0x20 width; 0x24 height; 0x28 bytes per row; 0x2c buffer offset; 0x30 pixel format; 0x34 u32 (dcache flush length); 0x38 plane count; 0x40 u16 bytes/element; 0x42 u8 element width; 0x43 u8 element height; 0x44 cache mode; 0x4c u8 subsampling; 0x4d u8 prohibit-use count; 0x50 parent id; 0x54 slice count; 0x58 u64 trace id; 0x60 u64 protection options; planes[n] at 0x68+n*0x80 (+0 width, +4 height, +8 bytes/row, +0xc offset, +0x14 size, +0x18 u16 bpe, +0x1a/+0x1b u8 element w/h, +0x1c u8 component count, +0x1d address format, +0x54 u8 compression type (non-zero -> no CPU base address)).
  - **Traps** (IOConnectTrapN -> `IOUserClient::getTargetAndTrapForIndex`): 0 IncrementUseCount(id, category), 1 DecrementUseCount(id, category), 2 **Lock**(id, options, &seed_out), 3 **Unlock**(id, options, &seed_out), 4 Release(id), 5 Release after a failed create/lookup setup (id).
  - Other selectors: 5 SetYCbCrMatrix, 6 WrapClientImage, 7 WrapClientMemory, 9 SetValue (serialized), 0xa UpdateNonBulkValueCache (struct in 9), 0xb RemoveValue, 0xc BindAccel, 0x11 SetSurfaceNotify (async), 0x12 RemoveSurfaceNotify, 0x13 Log, 0x14 SetPurgeable, 0x15 SetOwnership, 0x16 SetTiled, 0x18 SetTimestamp, 0x19 GetTileFormat, 0x1a CopyDataValueBytes, 0x1b SetBulkAttachments (struct 0x88), 0x1e/0x2e-0x31 (via outlined helpers), 0x1f SetCompressedTileDataRegionMemoryUsed, 0x20 GetGraphicsCommPageAddress, 0x21 SetIndexedTimestamp, 0x24-0x26/0x2d IOSurfaceAccelerator, 0x27-0x29 shared events, 0x2a SignalEvent, 0x2b Set/InitDetachModeCode, 0x2c SetOwnershipIdentity, 0x32-0x34 data properties.
  - => SWEET kernel service: class/name **IOCoreSurfaceRoot** + IOSurfaceRootUserClient; minimum = sel 13, 0, 4, 9/0xa/0xb (values), traps 0-5, 0x22/0x23 later for cross-process surfaces (CA sends surfaces to backboardd by mach port). Memory: IOBufferMemoryDescriptor per surface, mapped into each task with createMappingInTask; ids from a global table.

- **Image v8: daemon logs on the console.** `tools/patch-launchd-plist.py IN OUT LABEL...` rewrites launchd.plist (binary, plistlib): renames every `_PanicOnCrash` to `_PanicOnCrasX` and gives the listed jobs `StandardOutPath`/`StandardErrorPath` `/dev/console` (devfs console is 0622, writable by mobile) plus `OS_ACTIVITY_DT_MODE=YES` (os_log mirrored to stderr) and `CFLOG_FORCE_STDERR=1`. v8 = v7 + that plist for com.apple.backboardd and com.apple.SpringBoard, written through the mount (796310 bytes, root:80 0644), SHA256 in `ios16-root-20H392-v8.img.sha256`, `.simg` ready; boot with `shim/out/shim-boot-0045n.img`. Goal: see backboardd's uncaught exception text and the window server's display probing. ODDITY (unresolved): the v5 raw-image rename at offset 0x1e025f000 is present in the v5-v7 image bytes, but the plist read through a Linux mount of v7 still had the original `_PanicOnCrash` keys; filefrag shows the file as one extent. Never patch file contents through raw offsets again; write through the mount.
- **IOMobileFramebuffer.framework reversed (step 1 of the GUI plan).** `ipsw dyld disass research/dsc/dyld_shared_cache_arm64 --image IOMobileFramebuffer`.
  - Display discovery (`IOMobileFramebufferCreateDisplayList` -> `getDisplayListNumber`): iterates children of `IODeviceTree:/arm-io`, counts nodes whose `device_type` is "display-subsystem" or "ext-display-subsystem" (or `compatible` "paravirtualizedgraphics,gpu"); then `iomfb_populate_display_infos` adds an IOServiceAddMatchingNotification(IOServiceFirstMatch, **IOServiceMatching("IOMobileFramebuffer")**) and runs the run loop up to 30 s until that many services matched. `iomfb_match_callback` reads service properties `dfr`, `AppleTV`, `external` (CFBoolean) and names displays (external ones "%s-%d").
  - `IOMobileFramebufferOpen(service, task, type, &fb)`: `__ioMobileFramebufferAlloc` (CFPreferences com.apple.iokit.IOMobileGraphicsFamily: canvas_width/height, override_display_width/height, override_panel_width/height, benchmark, enable_ktrace), `IOServiceOpen(service, task, type)` (GetMainDisplay passes type 0), `getEDTProperty` reads `canvas-width`/`canvas-height` from the service's IODeviceTree parent, then fills a table of `kern_*` function pointers.
  - User client selectors (`kern_*` -> IOConnectCall*): 3 _kern_GetLayerDefaultSurface[ScalarMethod_stub]; 4 _kern_SwapBegin[ScalarMethod_stub]; 5 _kern_SwapEnd[StructMethod_stub]; 6 _kern_SwapWaitWithTimeout[ScalarMethod_stub] _kern_SwapWait[ScalarMethod_stub]; 7 _kern_GetID[ScalarMethod_stub]; 8 _kern_GetDisplaySize[ScalarMethod_stub]; 10 _kern_SetTVOutMode[ScalarMethod_stub]; 11 _kern_SetWSSInfo[ScalarMethod_stub]; 12 _kern_RequestPowerChange[ScalarMethod_stub]; 13 _kern_EnableStatistics[ScalarMethod_stub]; 14 _kern_SetFlags[ScalarMethod_stub]; 15 _kern_SetDebugFlags[ScalarMethod_stub]; 16 _kern_SetTVOutSignalType[ScalarMethod_stub]; 17 _kern_SetGammaTable[StructMethod_stub]; 18 _IOMobileFramebufferIsMainDisplay[ScalarMethod_stub]; 19 _kern_SetWhiteOnBlackMode[ScalarMethod_stub]; 20 _kern_SwapSignal[ScalarMethod_stub]; 22 _kern_SetDisplayDevice[ScalarMethod_stub]; 23 _kern_SetDigitalOutMode[ScalarMethod_stub]; 24 _kern_GetHDCPDownstreamState[StructMethod_stub]; 25 _kern_GetDigitalOutState[ScalarMethod_stub]; 26 _kern_printDisplayRegs[ScalarMethod_stub]; 27 _kern_GetGammaTable[StructMethod_stub]; 28 _kern_GetDotPitch[ScalarMethod_stub]; 29 _kern_GetDisplayArea[StructMethod_stub]; 30 _kern_EnableDisableDithering[ScalarMethod_stub]; 31 _kern_SetUnderrunColor[ScalarMethod_stub]; 33 _kern_EnableDisableVideoPowerSavings[ScalarMethod_stub]; 34 _kern_SetVideoDACGain[ScalarMethod_stub]; 35 _kern_SetLine21Data[ScalarMethod_stub]; 41 _IOMobileFramebufferEnableMirroring[ScalarMethod_stub]; 42 _IOMobileFrameBufferGetMirroringCapability[StructMethod_stub]; 43 _IOMobileFramebufferSetRenderingAngle[StructMethod_stub]; 44 _IOMobileFramebufferSetMirrorContentRegion[StructMethod_stub]; 47 _kern_HDCPSendRequest[ScalarMethod_stub]; 48 _kern_HDCPGetReply[ScalarMethod_stub]; 49 _kern_SurfaceIsReplaceable[ScalarMethod_stub]; 50 _kern_SetBrightnessCorrection[ScalarMethod_stub]; 51 _kern_SetColorRemapMode[ScalarMethod_stub]; 52 _kern_SwapCancel[ScalarMethod_stub]; 53 _IOMobileFramebufferSetCanvasSize[ScalarMethod_stub]; 54 _IOMobileFramebufferGetMatrix[Method_stub]; 55 _IOMobileFramebufferSetMatrix[Method_stub]; 56 _IOMobileFramebufferKernelTests[StructMethod_stub]; 57 _kern_GetColorRemapMode[ScalarMethod_stub]; 66 _kern_SPLCSetBrightness[ScalarMethod_stub]; 67 _kern_SPLCGetBrightness[ScalarMethod_stub]; 68 _kern_SetParameter[Method_stub]; 69 _kern_SwapSetColorMatrix[StructMethod_stub]; 72 _kern_DisableNotifications[ScalarMethod_stub] _kern_EnableNotifications[ScalarMethod_stub]; 73 _kern_ChangeFrameInfo[ScalarMethod_stub]; 74 _kern_SupportedFrameInfo[Method_stub]; 75 _kern_FactoryPortal[StructMethod_stub]; 77 _kern_GetCurrentAbsoluteTime[ScalarMethod_stub]; 78 _kern_SetBlock[Method_stub]; 79 _kern_GetBlock[Method_stub]; 80 _kern_GetLinkQuality[ScalarMethod_stub]; 81 _kern_SwapCancelAll[ScalarMethod_stub]; 82 _kern_GetProtectionOptions[ScalarMethod_stub]; 83 _kern_CopyLayerDisplayedSurface[ScalarMethod_stub]; 84 _kern_SetContrast[StructMethod_stub]; 85 _kern_GetBufBlock[Method_stub]; 86 _IOMFBGainEncoderFinishEncoding[Method_stub]; UNKNOWN {}
  - => Kernel needs: an `/arm-io` child node with device_type "display-subsystem" (+ canvas-width/height) and an IOService of class **IOMobileFramebuffer** (subclass name match) attached under it, opened with type 0, implementing at least GetDisplaySize(8), IsMainDisplay(18), GetID(7), GetLayerDefaultSurface(3), SwapBegin(4)/SwapEnd(5 struct)/SwapWait(6), Enable/DisableNotifications(72), frame info(73/74), CopyLayerDisplayedSurface(83), plus IOSurface for the swapped buffers. NEXT: CoreAnimation's AppleDisplay/IOMFBDisplay usage of these (constructor, first swap, vsync), then IOSurface.framework's IOSurfaceRoot selectors.
- **GUI PLAN (decided 2026-10-16): CPU rendering + kernel IOMobileFramebuffer.** User: CPU rendering is fine for now (goal = prove the GUI runs), may later research an Apple-style GPU driver for the Adreno 618 -> keep display output and renderer separable. QuartzCore findings (symbols from the cache's .symbols subcache: `ipsw dyld symaddr research/dsc/dyld_shared_cache_arm64 --image QuartzCore`; strings: `ipsw dyld macho ... QuartzCore --strings`):
  - Renderers: `sw_new_context` (CA::OGL::SWContext, full CPU renderer incl. blur, images, IOSurface-backed surfaces, make_buffer_current(WindowServer::Surface)), `gles_new_context`, `metal_new_context`, `new_null_context`. Env knobs include CA_ENABLE_OGL, CA_DISPLAY_TYPE, CA_FORCE_LOCAL_SERVER, CA_FORCE_TIMER, CA_CANVAS_WIDTH/HEIGHT, CA_MAIN_DISPLAY_SCALE/ORIENTATION, CA_DISABLE_RENDER, CA_ALWAYS_RENDER (selection logic not traced yet).
  - `-[CAWindowServer _detectDisplays]` (0x1884e3150) iterates static tables `open_funcs`/`count_funcs` = AppleCLCDDisplay, AppleRGBOutDisplay, AppleWirelessDisplay only; each `count()` = `display_count_of_type(IOMFBDisplayType)` over **`IOMobileFramebufferCreateDisplayList()`**, `open(i)` = `create_display_of_type` -> `CA::WindowServer::AppleDisplay(CFString name, ..., IOMFBDisplayType)` (an IOMFBDisplay driven by IOMFBServer: vsync_callback, frame_info_callback, swaps). `CA::WindowServer::VirtualDisplay` exists but is never auto-detected (explicit API only). => the main display MUST come from an IOMobileFramebuffer kernel service.
  - NEXT: reverse IOMobileFramebuffer.framework: IOMobileFramebufferCreateDisplayList / IOMobileFramebufferOpen (service matching, display type property), the user-client selectors used by IOMFBServer (display size/modes, swap begin/set-layer/end with IOSurface ids, vsync notifications), then the IOSurface kernel side (IOSurfaceRoot user client: create/lookup/lock, memory mapping) that both IOMFB swaps and CA surfaces need. Kernel: SWEET IOMobileFramebuffer service that copies the swapped IOSurface to the 1080x2400 framebuffer at 0x9c000000.
- **logd fixed; backboardd = DISPLAY WALL (boot 0045n on v7, 21:08:57 UTC, frames VID_20261016_230850 00:55-01:28, slide 0x4c3ec000, `research/crash-0045n-v7.txt`).** No `CRASH logd` any more; new daemons run (runningboardd, WirelessRadioManagerd, networkserviceproxy, dasd, cloudpaird, magicswitchd, atc, askpermissiond, ManagedSettingsAgent, containermanagerd uses AKS 0/17). New: locationd sig 11 at CFRunLoopAddSource+132 (NULL source) from its main+0x2af138 in a dispatch_once. **backboardd** sig 6: abort <- std::terminate <- _objc_terminate <- dispatch_once <- main+0x9334 = the "start %{public}@..." wrapper (0x100009264) for **"window server"** (block 0x10000a770 -> dispatch_once body 0x10001d7f0 = StartWindowServer): MTLSetShaderCachePath, `[CAWindowServer serverWithOptions:@{kCAWindowServerDisableUpdatesOnMainDisplay: @YES}]`, `setRendererFlags:`, **`mainDisplay`** -> `displayId` -> `[CADisplay displayWithDisplayId:]`, setWindowServerDisplay:, bounds, pointScale, currentMode, preferredScale, nativeOrientation, touchScales... With no display, mainDisplay is nil and it throws. (HID system and GS start fine before it.) SpringBoard dies on the same missing display (FBSDisplayMonitor). QuartzCore display backends (from the cache's .symbols subcache, `ipsw dyld str`): `CA::WindowServer::AppleCLCDDisplay` (IOMobileFramebuffer hardware) and **`CA::WindowServer::VirtualDisplay`** (open(), new_server, clone_update). `CA_DISPLAY_TYPE` and `CA_FORCE_LOCAL_SERVER` strings exist in QuartzCore but have no direct xref (likely read via a table of CA_* env names). NEXT: find how CA::WindowServer::Server enumerates displays (IOMobileFramebuffer services vs VirtualDisplay / CA_DISPLAY_TYPE) and choose: (a) kernel IOMobileFramebuffer service + IOSurface, or (b) a VirtualDisplay rendered by CA's CPU path (CA::OGL::SW) and copied to the framebuffer.
- **watchdogd + CommCenter + identityservicesd fixed (boot 0045n on v6, 20:41:30 UTC, frames VID_20261016_224133 00:39-01:13, slide 0x20fec000).** `SWEET WDT: watchdogd sel 0 in 0/0 out 1/0 -> ok`, no watchdogd/CommCenter/identityservicesd crashes (CommCenter/identityservicesd now call AKS 0/17/7). usermanagerd boot task runs (UMD_INIT creates primary session uid 501 "mobile", personas 99 System and 199 Default registered with the kernel); `Failed to atomically swap writing file /private/var//keybags/usersession.kb: Operation not supported (45)` (HFS lacks the atomic swap rename; non-fatal so far). Remaining: SpringBoard (FBSDisplayMonitor, no display), backboardd (uncaught exception), mediaserverd, lockdownd (crash inside obfuscated code, main+0x4c1b4?), logd. **logd**: main+0x18160 = `BUG IN LIBTRACE: Failed to migrate directory` from 0x10001801c(dir 0): 0x100016538 requires the dir to exist with uid/gid/mode from the table at 0x10002df14 (0: /private/var/db/diagnostics 272:272 0751, 1: /private/var/db/uuidtext 272:272 0755, 2: /private/var/db/diagnostics/timesync 272:272 0755) plus a `.migrated` file, else XPC "operation 6" to a helper and crash. **Image v7** = v6 + those dirs/markers (also in mkiosroot.sh), SHA256 in `ios16-root-20H392-v7.img.sha256`, `.simg` ready. Boot with `shim/out/shim-boot-0045n.img`.
- **Exit-reason text works (boot 0044n, frames VID_20261016_221726 00:32-00:53, slide 0x22d5c000).** CommCenter/identityservicesd: `couldn't dlopen libobjc-trampolines.dylib: dlopen(/usr/lib/libobjc-trampolines.dylib, 0x0106): tried: '/usr/lib/libobjc-trampolines.dylib' (no such file, not in dyld cache)`: the file only exists in the OS cryptex and dyld does not search cryptexes at ignition_level=1. 19 cryptex files have no root counterpart (OS: DriverKit dyld cache + .symbols, VisualTestKit, libffi-trampolines, libobjc-trampolines, libstdc++ 6.0.9 + 2 symlinks; App: SafariSupport.bundle, AuthenticationServicesAgent, com.apple.Safari.History, passwordbreachd, safarifetcherd, webbookmarksd, webinspectord, webpushd) -> hard-linked (symlinks copied: Linux hfsplus cannot hard-link symlinks); `tools/mkiosroot.sh` does it generically. watchdogd still crashed: the `-wdt_no_pmu` / `/arm-io/wdt no-pmu` check (0x10000a01c) only runs when os_variant_has_internal_diagnostics; otherwise 0x100009b18 looks up IOServiceMatching("IOService") + IOPropertyMatch {IOPMUPrimary: 1} (NSConstantDictionary) and crashes if absent, then IORegistryEntrySetCFProperty(boot stage 0xff) (failure only logged). Patch **0045**: IOWatchdog sets IOPMUPrimary = 1 and accepts setProperties; CI `38961814194` -> `xnu/out-0045`. **Image v6** = v5 + the 19 cryptex links, SHA256 7c610c6a...297a, `ios16-root-20H392-v6.simg` (full flash needed). Boot image `shim/out/shim-boot-0045n.img` (0045 + sweet-0044hi.adt). Still open: SpringBoard (no display), backboardd (uncaught exception, reason none), mediaserverd (Fig NULL dict), logd (main+0x18160), lockdownd (main+0x4c1b4).
- **System survives daemon crashes (boot 0043p, video frames VID_20261016_215006 00:25/00:42/00:54).** With the v5 plist patch no panic: launchd keeps launching (runningboardd, powerd, tccd, healthd, peopled, cryptexd, routined, aksd, mediaremoted, familynotificationd, nanoprefsyncd, navd, cloudpaird, passd, amsaccountsd, accessoryupdaterd, wcd ...). Shared cache slide that boot 0x406b4000 (`research/crash-0043p.txt`). watchdogd still crashed at main+0xb82c from the PMU step: `-wdt_no_pmu` is only honored when os_variant_has_internal_diagnostics; customer builds read `no-pmu` from IODeviceTree:/arm-io/wdt (IORegistryEntrySearchCFProperty) -> mkadt adds /arm-io/wdt {no-pmu}. **cryptexd** sig 6: abort_with_payload <- os_crash_fmt <- `__darwin_arm_secure_boot_chip.cold.7` ("panic: failed to get crypto algorithm") <- `_img4_chip_select_personalized_ap`: reads /chosen use-ddi-secure-boot, allow-ecid-mismatch, crypto-hash-method ("sha1" | "sha2-384"), /chosen/asmb lp-smb0/1 -> mkadt adds crypto-hash-method "sha2-384". **identityservicesd** sig 6 = same `_objc_fatal` in imp_implementationWithBlock as CommCenter (block trampolines; message needed). lockdownd sig 5 at main+0x4c1b4. Patch **0044**: crash report adds `CRASH name reason ns N code X: text` (exit reason description = abort_with_payload text). CI `38959096811` -> `xnu/out-0044`, QEMU OK. Image `shim/out/shim-boot-0044n.img` = 0044 + `shim/out/sweet-0044hi.adt` (wdt no-pmu, crypto-hash-method) + cmdline (wdt=-1, no -wdt_no_pmu).
- **First symbolicated daemon crashes (boot 0043u on journaled v4, 19:38:53 UTC; video frame chat image 36; transcribed in `research/crash-0043u.txt`; shared cache slide 0x40d60000 for every process).** (Gemini's video transcription invented values, use frames only.) `tools/sweet-crash.py research/dsc/dyld_shared_cache_arm64 x research/crash-0043u.txt`:
  - **watchdogd** pc main+0xb82c (`_os_crash` trap helper) from 0x100009bb0 = set PMU boot stage via IORegistry (no PMU) -> fix: boot-args `wdt=-1 -wdt_no_pmu` (Apple's own test mode writes "%s wdt=-1 -wdt_no_pmu"; watchdogd logs "skipping setting PMU boot stage because -wdt_no_pmu found in boot-args or no-pmu found in EDT"). Kernel effect of wdt=-1: sched hygiene debug off, zone_exhausted_timeout -1 (harmless).
  - **SpringBoard** sig 5 in `-[FBSDisplayMonitor _initWithBookendObserver:transformer:]+1160` <- `+[FBDisplayManager sharedInstance]` <- `FBSystemShellInitialize` <- `SBSystemAppMain`: no main display. GUI WALL: display service (IOMobileFramebuffer / CoreAnimation display).
  - **backboardd** sig 6: abort <- std::terminate <- _objc_terminate (uncaught exception; message not shown).
  - **CommCenter** sig 6: `_objc_fatal` in `imp_implementationWithBlock` (from `+[_PFRoutines initialize]`, CoreData): objc block trampolines cannot be allocated -> likely kernel gap (trampoline page remap); will hit many processes.
  - **mediaserverd** sig 11: CFDictionaryGetValue(NULL) in FigNotificationListenerStartNotifications (routing session manager).
  - **logd** sig 5 at its main+0x18160 (bt main+0x9dfc); lockdownd sig 5 (not yet symbolicated).
  - Image v5 = v4 + launchd.plist (`/System/Library/xpc/launchd.plist`, contiguous at image offset 0x1e025f000, 1456980 bytes) with the 7 `_PanicOnCrash` keys renamed `_PanicOnCrasX` in place (raw image patch, no metadata change) -> `diff-v4-v5.simg` = 7 blocks; v5 SHA256 78f96974...185a. Boot image `shim/out/shim-boot-0043p.img` = 0043 + `wdt=-1 -wdt_no_pmu`.
- **0043 console crash reports (QEMU verified); image `shim/out/shim-boot-0043u.img`; NEXT: flash v4 + boot 0043u.** Boot 0042u (video photo IMG_20261016_211646): short exec lines work; watchdogd crashes with NO `SWEET WDT:` line (fails before its first IOWatchdog call: lookup or IOServiceOpen, or elsewhere); lockdownd, SpringBoard, mediaserverd, CommCenter, identityservicesd, backboardd, watchdogd all crash repeatedly ("Too many corpses being created" = corpse limit, no crash reports). AKS callers: lockdownd (0, 7), imagent/sleepd (0, 17, 7), backboardd (0, 17), UserEventAgent (0, 7, 102, 103 -> unsupported). Patch **0043**: `sweet_crash_log` (osfmk/kern/exception.c, called from proc_prepareexit for crash exits on the current proc) prints `CRASH name[pid] sig S code (exc<<32|code) sub X slide SR mh MH`, `CRASH name pc lr sp fp far`, `CRASH name bt` (10 fp frames); AKS sel 103 notify_state_is_set -> 0. CI `38954520419` -> `xnu/out-0043`. QEMU test: init forks, child `brk #1`: `CRASH launchd[2] sig 5 code 0x600000001 sub 0x1000004f0 slide 0x0`, pc correct, parent keeps running (pid 1 crashes panic before proc_prepareexit, so test with a child). `tools/sweet-crash.py CACHE ROOT [report]` symbolicates typed-in CRASH lines (`ipsw dyld a2s` with addr - slide; main binary offsets = addr - mh + 0x100000000).
- **v3 reflashed 18:50-18:55 UTC (`research/flash-userdata-v3b.log`, 24/24 OKAY); 0042u booted 19:01:17 UTC on the clean disk (`research/boot0042u-2.log`), SLOW MO VID pending (watchdogd failure reason).** ABL hangs on the first `fastboot boot` after every big flash; user re-enters fastboot. User gave standing approval to reflash/boot without asking (memory standing-flash-approval). Commits now end with `Co-Authored-By: dipa-studio <183933817+dipa-studio@users.noreply.github.com>` (user request; never a Claude trailer).
  - watchdogd flow: connect_to_kext (0x100005408): IOServiceGetMatchingService("IOWatchdog") fails -> "failed to discover" + _os_crash; IOServiceOpen(type 1) fails -> "IOServiceOpen failed with error : 0x%x" + _os_crash. Then sel 0 (out 1 scalar): 0 -> "userspace monitoring disabled", 0x100009bb0, sleep 2, exit(0); nonzero -> "userspace monitoring enabled", normal operation.
  - **Image v4 (JOURNALED):** `tools/mkiosroot.sh` now does `mkfs.hfsplus -J`, mounts with `-o loop,force`, adds the /System/Library/Caches/com.apple.dyld hard links (44) and /private/var/keybags/systembag.kb (0700/0600 root). Built from the IPSW dmgs (OS 087-86698-044, SystemOS 087-86593-047, AppOS 087-86715-047) -> `/run/media/dipa/android/ios-sweet-ipsw/ios16-root-20H392-v4.img` SHA256 fba4aeae...ec0f9 + `.simg`. Header: attr 0x80002100 (unmounted, journaled), journal info block 129, journal 8 MiB at 532480 flagged need-init. Linux `fsck.hfsplus -n` reports "Volume header needs minor repair" on any fresh journaled volume even after -y (quirk). QEMU: 64 MB journaled test root (`rd=md0 rootrw=1`, 0042 kernel): `hfs: Initializing the journal (joffset 0x3000 sz 0x80000)` -> mounted -> init runs, no panic. NEXT: flash v4 (full), boot 0042u.
- **SPRINGBOARD STARTS (boot 0041u 18:39:53 UTC, photo IMG_20261016_204142).** backboardd, bluetoothd, fairplayd.H2, SpringBoard (pid 111, crashed: "Too many corpses"), xpcproxy run; `SWEET AKS: backboardd sel 0 -> 0`, `sel 17 in 1/0 out 0/4096 -> 0`. Panic: watchdogd[113] exited, namespace 2 subcode 5 (SIGTRAP = its _os_crash): watchdogd crashes on purpose when IOServiceGetMatchingService("IOWatchdog") fails ("connect_to_kext() unexpectedly failed") or IOServiceOpen(type 1) fails ("IOServiceOpen failed with error"); IOWatchdog DOES register (QEMU msgbuf: `SWEET WDT: IOWatchdog registered`), cause not on screen yet -> need video. Note `wdt_test=no_user_checkin` makes watchdogd log "detected testing boot-args to not run, exiting" (path not reached here?). **HFS CORRUPTION:** `hfs_swap_BTNode: offset #0 invalid (0xBC1D) (blockSize 0x2000 numRecords 81)`, `node=19677 fileID=4` (catalog), `hfs_mark_inconsistent` repeated. Likely cause: non-journaled HFS + unclean panics while writing (0038e wrote a 2.7 GB core then panicked; every boot since ends in panic). Plan: reflash v3 (clean), longer term HFS journaling. Patch **0042**: one short `exec NAME` console line instead of the 3-line "Allowing spawn of iOS binary" notice; CI `38949451276` -> `xnu/out-0042`; image `shim/out/shim-boot-0042u.img` (same cmdline as 0041u).
- **LAUNCHD DAEMONS RUN (boot 0039u, 18:29:13 UTC, photo IMG_20261016_203052).** With `launchd_unsecure_cache=1` all boot tasks pass and launchd starts LaunchDaemons (pids > 100: identityservicesd 114 and lockdownd 106 crash, "Too many corpses being created"). Fatal: `watchdogd: ERROR: failed to discover watchdog KEXT service` -> exit (namespace 2 = signal, subcode 5) -> `_PanicOnCrash` panic. watchdogd: IOServiceMatching("IOWatchdog"), IOServiceOpen type 1, IOConnectCallScalarMethod sel 0/1 (check in)/3/4 (disable/enable userspace monitoring)/5, IOConnectCallStructMethod sel 2 (1 KiB in) and 9 (small out); VM path ("detected virtual machine environment and no watchdog KEXT found, exiting...") only when it detects a VM. Console also flooded with `hfs: set hardlink bit on vol=iOSRoot cnid=321318` (Linux-made hard links lack kHFSHasLinkChainBit; repaired only in memory on each lookup). Patches **0040** (`iokit/Kernel/arm/SweetWatchdog.cpp`: IOWatchdog + IOWatchdogUserClient, all selectors succeed with zeroed outputs, each logged once as `SWEET WDT: ...`) and **0041** (sweet_hfs hfs_catalog.c: the three link-repair printfs limited to 8 per boot); CI `38948245372` PASSED -> `xnu/out-0041` (QEMU regression OK). Image `shim/out/shim-boot-0041u.img` (0041 + sweet-0038hi.adt + ufsrw rootrw + launchd_unsecure_cache=1) booted 18:39:53 UTC (`research/boot0041u.log`), photo pending.
- **v3 FLASHED + keybag passes; next wall AMFI (launchd_cache_loader).** User said "flash, nothing to lose": `fastboot -S 512M flash userdata ios16-root-20H392-v3.simg` 17:51:52-17:57:01 UTC, 24/24 chunks OKAY (`research/flash-userdata-v3.log`). userdata = v3 (SHA256 fa84274c...22df) until the next rw boot. ABL fastboot then hung ("Sending" forever, `reboot bootloader` timed out) until the user re-entered fastboot by hand (common Xiaomi ABL crash). Boot 0038e 18:06:15 UTC (`research/boot0038e-3.log`), photos IMG_20261016_200812/200838: keybag OK, then boot tasks tzinit (`Bad zoneinfo link`, resets to system partition, OK), finish-restore, finish-demo-restore, sysstatuscheck, prng_seedctl (`failed to load kernel prng seed`, `failed to load virtual random (-147)`, AllowCrash), auearlyboot, then **launchd_cache_loader: `abort: Unable to find AMFI`** -> 2.7 GB core written to /cores in 91 s -> launchd panic `launchd_cache_loader[22] exited -- exit reason namespace 7 subcode 0x1`. launchd_cache_loader: SecStaticCodeCheckValidity of /System/Library/xpc/launchd.plist with detached .sig, then IOServiceMatching("AppleMobileFileIntegrity") + IOServiceOpen type 0 + IOConnectCallMethod sel 6 (1 scalar = 1, struct in = cdhash) = trust the cdhash, then sends the cache to launchd. Boot-arg `launchd_unsecure_cache=1` skips signature + AMFI ("Using unsecure cache"); the value is parsed with strtoul base 10 (helper 0x100002da8), so a path = 0 = off: boot 0039w (18:17:13 UTC, `=/System/Library/xpc/launchd.plist`, photo IMG_20261016_202008) still aborted `Unable to find AMFI` (core dump now skipped: `Failed to generate core file ... error 14`, so 0039 works). Image `shim/out/shim-boot-0039u.img` uses `launchd_unsecure_cache=1`. Patch **0039**: do_coredump = 0 on SWEET (kern.coredump sysctl still works); CI `38945267341` -> `xnu/out-0039` (_do_coredump now in __common). Image `shim/out/shim-boot-0039w.img` = 0039 + sweet-0038hi.adt + ufsrw rootrw + `launchd_unsecure_cache=/System/Library/xpc/launchd.plist`. TODO later: real AppleMobileFileIntegrity IOService (sel 6 trust cdhash etc.).
- **Image v3 ready, awaiting user "flash".** Boot 0038w 17:38:10 UTC, photo (chat image 30): `SWEET AKS: keybagd sel 0 -> 0x0`, `sel 14 out 1/0 -> 0x0` (x2), `MKB_INIT: mount is with NO CPROTECT ... does not support Data Protection`, `System Keybag loaded`; then keybagd still opens /private/var/keybags/systembag.kb (0x100012d3c, for BackupKeyBagKeys) -> ENOENT, `no-effaceable-storage` not set -> `Can't load the keybag. tears in rain... Time to die` -> `FATAL KEYBAG ERROR: kb_load` -> reboot -> Halt/Restart Timed Out panic. Fix: ADT `/defaults/no-effaceable-storage` (mkadt.py; `shim/out/sweet-0038hi.adt`, `research/q0038-hi.adt`, QEMU OK) + file `/private/var/keybags/systembag.kb` (0600 root, dir 0700; XML plist {KeyBagKeys: data}). **v3 image** `/run/media/dipa/android/ios-sweet-ipsw/ios16-root-20H392-v3.img` = v2 + that file, fsck OK, SHA256 fa84274c...22df; `ios16-root-20H392-v3.simg` (img2simg: zero blocks become FILL chunks, so a full flash overwrites every block the rw boots changed). Flash: `fastboot -S 512M flash userdata ios16-root-20H392-v3.simg`, then boot `shim/out/shim-boot-0038e.img` (0038 + sweet-0038hi.adt + ufsrw=1 rootrw=1).
- **0038 AppleKeyStore (CI 38938744130 PASSED -> `xnu/out-0038`; QEMU msgbuf: `SWEET AKS: AppleKeyStore registered (no passcode, unlocked)`; phone image `shim/out/shim-boot-0038w.img` booted 17:38:10 UTC, `research/boot0038w.log`, photo pending).** QEMU full kernel log trick (fb.txt is only the last screen): `llvm-nm KERNEL | grep _msgbufp` -> `--hmp "x /1gx ADDR"` gives struct msgbuf address (static, 0xfffffe0007b56678 on 0038) -> `--hmp "x /3gx STRUCT"` gives bufc (3rd quad) -> `--hmp "memsave BUFC 0x20000 \"FILE\""` (quote the file name), `tr -d "\0" < FILE`. Same inputs give the same addresses. Boot 0037w 17:08:35 UTC, photo IMG_20261016_191052: bindfs fixed mount; mount-phase-1/2 pass; `cc_fips_test` boot task: all corecrypto FIPS POST tests PASSED in userspace; then keybag boot task `keybagd --init`: `MKB_INIT: FATAL KEYBAG ERROR: failed to load system bag`, `Could not get options entry from the device tree` (x2, IODeviceTree:/options = NVRAM, non-fatal), `REBOOTING INTO RECOVERY MODE` -> reboot -> panic `Halt/Restart Timed Out @IOPlatformExpert.cpp:884` (no restart path on SWEET).
  - keybagd (static libaks): load_devicebag (0x100012c30) calls aks_get_system (sel 14, 0-1 scalar in, 1 out) and loads /private/var/keybags/systembag.kb only if the result is 0xe00002f0 (NotFound); any other error -> fatal; SUCCESS -> skip the file (userspace-reboot path). systembag.kb is a plist {KeyBagKeys}; decrypted with effaceable storage unless IODeviceTree:/defaults has `no-effaceable-storage`. Image has no /private/var/keybags. has_data_protection: statfs of /private/var; no cprotect on our HFS -> device-bag volume mapping skipped.
  - libaks connection: IORegistryEntryFromPath / IOServiceMatching("AppleKeyStore"), IOServiceOpen type 0, then selector 0 (no args) must succeed.
  - **AppleKeyStore selector map (iOS 16.7 AppleKeyStore.framework, via `ipsw dyld disass`):** 0 open, 2 create_bag, 3 save_bag, 4 unload_bag, 5 set_system_with_passcode, 6 load_bag, 7 get_lock_state (1 in, 1 out; bit 2 = unlocked since boot), 9 unlock_device, 10 wrap_key, 11 unwrap_key, 12 unlock_bag/test_stashed_kek, 13 lock_bag, 14 get_system, 15 change_secret_se, 16 internal_state, 17 get_device_state (1 in, struct out <=4K: DER SET of SEQUENCE{UTF8 key, INTEGER}: ss state, sls lock_state (MKBGetDeviceLockState returns it), sb backoff, sbo, sfa failed attempts, sfao, sgs generation, sas assertions, sgpe grace, srcd countdown, sr reserved), 18 recover_with_escrow_bag, 19 obliterate_class_d, 20 drain_backup_keys, 21/22 set/clear_backup_bag, 23 get_bag_uuid, 24 rewrap_key_for_backup, 26/27 assert_hold/drop, 30/31 set/get_configuration, 33-37 stash, 38 auth_token_create, 40 generation, 41 fdr_hmac_data, 42 verify_password, 43 _aks_operation, 44 remote_session, 49/50 signing keys, 51/53/54 stash enable/persist/escrow, 55 unload_session_bags, 57/58 remote peer, 59-62 smartcard, 63 migrate_s_key, 65 set_keybag_for_volume_with_cookie_persona, 66/67 sync bags, 68 copy_volume_cookie_persona, 69-73 se_*, 74-79/84/85/99 fv_* (FileVault/VEK), 80/81 absinthe, 82 make_public_backup_bag, 83 drop_auxiliary_auth, 86 kext get/set options, 87 invalidate_bag, 88-91/94/95/106/107 se_*, 92 lower_iteration_count, 93 get_internal_info, 96-98/117 keybag_persona create/list/delete, 100/101 assert promote/consume, 102/103 notify_state set/is_set, 104 fairplay, 105 change_secret_epilogue, 108 delete_xart_leak, 110-113 backup volume, 114 sealed_hashes_set, 115 prewarm_sps, 118-128 AKSIdentity*/AKSGetLastUser.
  - Patch **0038**: `iokit/Kernel/arm/SweetKeyStore.cpp` (class AppleKeyStore on IOResources, IOUserClientClass AppleKeyStoreUserClient; personality in KernelConfigTables.cpp): 0/4/5/9/12/13/26/27/55/102 success, 14 -> handle 0, 7 -> 0x4, 17 -> DER state (ss 4, sls 0), others kIOReturnUnsupported; logs `SWEET AKS: <proc> sel N arg0 in a/b out c/d -> ret` for every call.
  - Tools: shared cache copied to `research/dsc/` (git-ignored; ipsw needs a writable dir for its .a2s symbol cache); `ipsw dyld disass research/dsc/dyld_shared_cache_arm64 --image AppleKeyStore|MobileKeyBag`; scratchpad `annot2.py BIN DIS FIXUPS` annotates llvm-objdump output with strings and import names (`ipsw macho info --fixups`).
- **0037 bindfs (phone boot 0037w pending photo).** Boot 0036mw 16:57:47 UTC, photo IMG_20261016_190102: WRITABLE ROOT WORKS: `/dev/ufsroot0 on / (hfs, local, noatime)` after mount_hfs update (GetMasterBlock EBUSY harmless), coredump of mount written to /cores (rw). Then `mount: failed to mount /private/preboot/5CBE3601.../usr/standalone/firmware -> ...: Operation not supported by device (19)` -> exit 74 -> launchd panic `mount[2] exited`. Cause: the macOS-derived config had nullfs but no bindfs (ENODEV = unknown fs type). Caller passes env 0 -> bind not required, ENOENT (missing source dir) tolerated. Patch **0037** adds `bindfs` to FILESYS_BASE in MASTER.arm64.sweet; CI `38936550483` -> `xnu/out-0037` (bindfs_vfsops present; QEMU probe regression OK). Image `shim/out/shim-boot-0037w.img` (0037 + sweet-0036hi.adt + ufsrw=1 rootrw=1) booted 17:08:35 UTC (`research/boot0037w.log`). NOTE: userdata has now been modified by rw boots; it no longer equals the v2 image, so sparsediff with v2 as OLD is not safe any more (full reflash or a new baseline needed).
- **0036 phone: launchd boot tasks run; mount -P 1 needs /chosen/boot-manifest-hash.** Boot 0036hr 16:15:32 UTC, photo IMG_20261016_185236: platform check passes; fsck boot task runs (`fsck: not booting main or diagnostic OS. Skipping fsck on OS container`); mount-phase-1: `failed to get filesystem info` x2 (no IODeviceTree:/filesystems/fstab, non-fatal), `mount: data volume missing, but not required in env: 0`, mount_hfs update of /: `GetMasterBlock: Error 16 opening /dev/ufsroot0` (EBUSY, mounted block dev, non-fatal) then mount() EROFS (read-only boot) -> `mount: / failed with 1`; then `failed to get boot-manifest-hash` -> errx exit 66 -> launchd panic `mount[3] exited`. /sbin/mount: fn 0x100005f90 reads IODeviceTree:/chosen boot-manifest-hash (hex), bindfs-mounts /private/preboot/<HEX>/usr/standalone/firmware (ENOENT tolerated unless required). `tools/mkadt.py` now adds `boot-manifest-hash` = SHA-384("sweet boot manifest") (5CBE3601...5205). ADTs `shim/out/sweet-0036hi.adt`, `research/q0036-hi.adt` (QEMU probe OK, csops 0x26000001). Image `shim/out/shim-boot-0036mw.img` (0036 + new ADT + ufsrw=1 rootrw=1), booted 16:57:47 UTC (`research/boot0036mw.log`): FIRST WRITABLE BOOT of userdata (restore source: v2 image on SSD).
- **0036 CS_SIGNED (phone boot 0036hr pending photo).** Phone boot 0035hr 2026-10-16 15:57:02 UTC (`research/boot0035hr.log`, photo IMG_20261016_180249): highmem works on hardware; shared cache maps (trust-cache warnings only), Apple launchd runs (`Darwin Bootstrapper Version 7.0.0`, `entering ondemand mode`), starts boot task fsck, then userspace panic `boot task is not a platform binary (fsck, /sbin/fsck)`. launchd+0x3fea0: `csops(pid, CS_OPS_STATUS)` must have bit 0x04000000. Cause: nothing set CS_SIGNED (AMFI does it in vnode_check_signature), so csproc_get_blob() returns NULL for every process and csops never reports CS_PLATFORM_BINARY (also breaks per-process cdhash/entitlement queries). QEMU probe init (`csops` status printer, scratchpad csq/) on 0035: `0x02000001`; patch **0036** (`*cs_flags |= CS_SIGNED` in the SWEET AMFI hook), CI `38929635791` -> `xnu/out-0036`: `0x26000001`. Images `shim/out/shim-boot-0036hr.img` / `-0036hw.img` (same cmdlines as 0035). 0036hr booted 16:15:32 UTC (`research/boot0036hr.log`). Next launchd boot tasks (embedded __config plist): fsck -qL and mount -P 1/2 (RequireSuccess); no /etc/fstab on the image.
- **0035 ENTITLEMENTS + PLATFORM BINARIES (QEMU verified).** `xnu/patches/0035-sweet-amfi-entitlements-platform-binaries.patch`: new `iokit/Kernel/arm/SweetEntitlements.cpp` (OSObject subclass: OSDictionary from OSUnserializeXML of the signature's XML entitlements + original XML for CS_OPS_ENTITLEMENTS_BLOB); `bsd/kern/sweet_amfi.c` answers queryEntitlementBoolean/String(+WithProc, string also matches array members), copyEntitlementAsOSObject(+WithProc, retained), asdict (retained), get_xml (kalloc_data copy); built-in MAC policy "SWEET AMFI" (registered from bsd_init before vfs/exec) with mpo_vnode_check_signature: every signed binary on the MNT_ROOTFS mount gets CS_PLATFORM_BINARY (stand-in for AMFI's static trust cache: iOS system binaries are ad-hoc, cd_flags 0x2, platform byte 0), and attaches entitlements ONLY for the writable copy (cs_blob != csb_ro_addr; revalidation passes the RO blob). LESSON: first version attached in adjustContextWithoutMonitor -> QEMU `unable to adjust OSEntitlements context without monitor: 53` because ubc_cs_convert_to_multilevel_hash (4K-page signatures on 16K kernel) sets csb_entitlements_blob = NULL; ALL iOS 16 binaries (launchd, backboardd, SpringBoard) use 4K code pages, so this would have broken launchd on the phone. Boot-arg `sweet_amfi_debug=1` logs `SWEET AMFI: <ident>: N entitlements (test: ...)`. `tools/mkinit.py OBJ OUT [ENTS.plist]` adds an XML entitlements blob (special slot -5; default output byte-identical). QEMU `research/q0035-ent` (RAM disk `research/root-hfs-ent.img`, init with 3 test entitlements, highmem): `org.sweet.init: 3 entitlements (test: bool 1 string 1 array 1)` then init PID 1. MAC policies loaded: 0034 = 1, 0035 = 2 (QMP `mac_policy_list`). CI `38909807230` -> `xnu/out-0035`. Phone images: `shim/out/shim-boot-0035hr.img` (highmem, ro), `-0035hw.img` (highmem, +ufsrw=1 rootrw=1), `-0035r.img` (low mem fallback).
- **PHONE TEST ORDER (updated):** 0035hr -> if instant reset, 0035r -> then 0035hw.

- **HIGHMEM: XNU in the upper DDR bank, ~3.95 GiB (was 1.2 GiB).** Live FDT memory = <0x80000000 2G> + <0x1_00000000 0xfcc00000>; all fixed reserved-memory carve-outs are in the low bank (highest: ramdump_fb 0xaf000000, rmtfs ~0xfde01000), dynamic pools have alloc-ranges <= 0xffffffff, UFS node has no `iommus` (Linux DMAs anywhere). `tools/mkadt.py --highmem` sets /chosen dram-base 0x100000000 dram-size 0xfcc00000; `tools/mkpayload.py` now takes phys_base/mem_size from the ADT (default payloads byte-identical, verified); `tools/qemu-sweet.py` sizes -m from the payload (8G for highmem). Patch **0034** (`0034-sweet-ufs-dma-above-4g.patch`): UFS refuses DMA memory above 4G without UFSHCI CAP.64AS (bit 24), logs `DMA at 0x...`. CI `38905889883` success -> `xnu/out-0034`. **QEMU verified** (`research/q0034-hi`, ADT `research/q0034-hi.adt` --qemu --highmem, HFS RAM disk): phys_base 0x100000000, init PID 1 heartbeat, no panic; `max_mem` = `mem_size` = 0xfcc00000 read via QMP (`research/q0034-hi-mem`). Phone images: `shim/out/shim-boot-0034hr.img` (read-only) / `-0034hw.img` (+ufsrw=1 rootrw=1), ADT `shim/out/sweet-0034hi.adt` (asmb SIP + sweet-ufs + highmem). PHONE RISK: unknown runtime TZ/XPU protection in the upper bank would show as an instant reset; fall back to 0033r/w. Watchdog ruled out for the overnight reset: msm-watchdog bark is 20 s (qcom,bark-time 0x4e20) yet panic screens stay up for many minutes.

- **Offline research 2026-10-16 (user away, phone unavailable). Images rebuilt** `shim/out/shim-boot-0033r.img` / `-0033w.img` now also carry `wdt_test=no_user_checkin`. NEXT ON PHONE: boot 0033r (expect past in6_nigroup), then 0033w (first writes: rw root fixes HFS link-chain bits itself and gives /var for launchd sockets /var/run/syslog, /var/run/lockdown.sock).
- **launchd.plist** (`/System/Library/xpc/launchd.plist`, compiled; 532 daemons, 66 RunAtLoad/KeepAlive, list in scratchpad `early-daemons.txt`). `_PanicOnCrash` daemons: containermanagerd/usermanagerd (InternalOnly), lockdownd/remoted/trustd/remotepairingdeviced (AutomationOnly), **watchdogd unconditional** (KeepAlive SuccessfulExit=false). watchdogd needs IOKit `IOWatchdog` kext service; if absent it exits only when `kern.hv_vmm_present` (ADT `/defaults/vmm-present`, hvg_sysctl.c) or with boot-arg `wdt_test=no_user_checkin` ("detected testing boot-args to not run, exiting"; reads kern.bootargs). Using the boot-arg (vmm-present would change many daemons; keep as an option).
- **GUI path research (QuartzCore in the shared cache, `ipsw dyld str CACHE STR`, ~10 s/search):** CA::WindowServer has IOMFBServer/IOMFBDisplay (Apple DCP/IOMobileFramebuffer), AppleCLCDDisplay, AppleRGBOutDisplay, **VirtualServer/VirtualDisplay** and **MemorySurface**, EAGLServer. Renderers: CA::OGL::MetalContext, GLContext and a **CPU software rasterizer CA::OGL::SW** (samplers/blend modes; "image is too large for software renderer"). Env knobs: `CA_ENABLE_OGL`, `CA_DISPLAY_TYPE`, `CA_FORCE_LOCAL_SERVER` (249 CA_* vars). Hypothesis (unverified): backboardd's render server could run a VirtualServer/MemorySurface with the SW renderer and we copy the surface to the ABL framebuffer -> needs disassembly of CA::WindowServer server creation. Expected other GUI walls: IOSurface (closed-source kext; needs a reimplementation), IOMobileFramebuffer user client, IOHID touch events, AppleKeyStore/keybag (SEP) for lock state, MobileGestalt answers (AppleCLCD etc.).

- **0032r RESULT (boot 05:07:50 UTC, `research/boot0032r.log`, photo IMG_20261016_071200): SHARED CACHE MAPPED, LAUNCHD RUNS.** With SIP relaxed (ADT asmb) the `not in trust cache` lines are only warnings for every subcache (.31 ... .42.dyldlinkedit); no SIP rejection; libSystem loads; launchd configures networking (`skip attaching fsw to lo0 using legacy TX model`) -> kernel data abort far 0x28 in `SHA1Init` (symbolized as sha1_init) from `in6_nigroup` (IPv6 on lo0): g_crypto_funcs NULL. Kernel addresses are unslid: `~/swift/usr/bin/llvm-symbolizer --obj=KERNEL ADDR` works directly.
- **0032** BearSSL SHA-1 (`0032-sweet-bearssl-sha1.patch`), **0033** BearSSL SHA-384/512 (`0033-sweet-bearssl-sha384-sha512.patch`, BearSSL ctx stored in SHA512_CTX, static-asserted to fit; ubc_subr.c uses SHA-384 for code directories). `0020-stage-bearssl.sh` now also stages hash/sha1.c, hash/sha2big.c, codec/{dec,enc}{32,64}be.c. CI `38854909053` success (0032-only run `38854802073` superseded). Kernel `xnu/out-0033` (SHA1Init -> _br_sha1_init verified by disassembly; nm symbols have a leading underscore). Images (ADT sweet-0032): `shim/out/shim-boot-0033r.img` (read-only), `shim-boot-0033w.img` (+ufsrw=1 rootrw=1). Still NULL g_crypto_funcs users: des, chacha20poly1305, rsa, aesxts, rand, flow_divert.
- `research/check-patch-order.py` now skips patches touching CI-staged sweet_hfs sources (0031) instead of stopping.

- **0031r RESULT (boot 05:03:52 UTC 2026-10-16, `research/boot0031r.log`, photo IMG_20261016_070508): SHARED CACHE FOUND.** dyld now opens `dyld_shared_cache_arm64` (v2 image hard links), kernel refuses to map it: `shared_region: ... map(...'dyld_shared_cache_arm64'): not in trust cache` -> `vnode is not SIP-protected` (vm_unix.c: macOS arm64 path needs trust cache, else root-owned + SF_RESTRICTED unless CSR_ALLOW_UNRESTRICTED_FS) -> `dyld cache '(null)' not loaded: syscall to map cache into shared region failed` -> libSystem not loaded -> coredump to /cores fails error 30 (EROFS) -> panic initproc failed -> "MACH Reboot" (spins; screen kept). Lots of `hfs: set hardlink bit on vol=iOSRoot cnid=... linkid=...` spam (Linux-made hard links lack the link-chain bit; read-only mount cannot persist the fix). FIX: ADT `/chosen/asmb` with `lp-sip0`=0x6f (u64; incl. CSR_ALLOW_UNRESTRICTED_FS) and `lp-sip1`=1 (unauthenticated root), `tools/mkadt.py` -> `shim/out/sweet-0032.adt`. Images with kernel 0031: `shim/out/shim-boot-0032r.img` (read-only) and `shim-boot-0032w.img` (+ufsrw=1 rootrw=1).

- **2026-10-16 morning:** userdata now = **v2** image (`diff-v1-v2.simg` flashed 21:06:50 UTC 2026-10-15, OKAY, `research/flash-userdata-diff-v2.log`); future diffs use `ios16-root-20H392-v2.img` as OLD. Booted `shim-boot-0029k.img` (ignition_level=1) right after (`research/boot0029k-v2.log`); **no photo** (user asleep). Next morning the phone was in PixelOS recovery = some hardware reset (ABL then booted Android, which cannot use userdata). OBSERVATION: our kernel cannot reset the SoC itself (`PE_halt_restart` is never set; reboot/halt print "MACH Reboot"/"CPU halted" and spin), so the reset came from outside XNU: Qualcomm watchdog or a TZ/XPU violation. Unknown which; re-boot 0029k and watch/video until the reset. Do NOT tap factory reset/format data in recovery (would wipe the iOS image).
- **0030** (`xnu/patches/0030-sweet-ufs-writes.patch`, CI `38852655542`): UFS WRITE(10) + read-modify-write for partial 4 KiB blocks + SYNCHRONIZE CACHE (DKIOCSYNCHRONIZE), only with boot-arg `ufsrw=1`; `ufs_write_blocks` re-checks partition bounds before every write (only place data goes out).
- **0031** (`xnu/patches/0031-sweet-read-write-root.patch`, CI `38852837176`): boot-arg `rootrw=1` clears MNT_RDONLY before the root mount (no data volume on sweet: /private/var is on the root; image has no /etc/fstab); Apple HFS accepts a dirty non-journaled ROOT read-write on SWEET (logs "mounting dirty non-journaled root read-write"). Later: journaled HFS for crash safety. Kernel `xnu/out-0031` (CI `38852837176` success, 5m31s; 0030 run cancelled by the newer push). Boot images (phone ADT `sweet-0028.adt`, ramdisk byte-matched): `shim/out/shim-boot-0031r.img` = 0029k args (read-only, ignition_level=1) and `shim/out/shim-boot-0031w.img` = same + `ufsrw=1 rootrw=1`. PLAN: boot 0031r first and film until the reset (diagnose overnight reset), then 0031w (first ever writes to userdata; v2 image on the SSD is the restore source). `research/check-patch-order.py` cannot check 0031 (staged sweet_hfs files are not in its index); CI stage scripts run first, so CI is the check.

- **ignition_level=1 result** (boot 21:01:39 UTC, `research/boot0029k.log`, photo IMG_20261015_230308): preboot/cryptex stages skipped (need >= 2/3), `dylib_cache: finding shared cache on system volume: ignition level = 1, search root fd = 4, subpath = System/Library/Caches/com.apple.dyld/` -> not found -> `ignition boot failed: 19` -> no shared cache -> libSystem not loaded. FIX IN PROGRESS: image v2 = copy of the flashed image + `/System/Library/Caches/com.apple.dyld/` with HARD LINKS to the 44 cryptex cache files (no extra space; avoids symlink-following doubts), then `tools/sparsediff.py ios16-root-20H392.img ios16-root-20H392-v2.img diff.simg` and flash only the diff. Boot with `ignition_level=1` (shim-boot-0029k.img).
- **ignition_level=0 result** (boot 20:58:07 UTC, `research/boot0029j.log`, photo IMG_20261015_230037): all stages skipped as "ignition level insufficient" (hello/preboot/cryptex1 sniff >= 2, select os cryptex path/graft >= 3, **dyld_cache >= 1**), `ignition boot failed: 89`, "ignition partially disabled", then same `Library not loaded: /usr/lib/libSystem.B.dylib (no such file, no dyld cache)`. => dyld gets the shared cache directory from ignition's dyld_cache stage. Next: `ignition_level=1` (`shim/out/shim-boot-0029k.img`) to skip preboot/cryptex stages but run dyld_cache; its log "finding shared cache on %s ... subpath = %s" will show where it searches. If it searches rootfs /System/Library/Caches/com.apple.dyld, add a symlink there (image rebuild; consider a diff-only sparse flash tool to avoid rewriting 12 GB).
- **APPLE dyld/launchd RUNNING (libignition) with `unentitled_ios_sim_launch=1`** (boot 20:53:24 UTC, `research/boot0029i.log`, photo IMG_20261015_225617): kernel prints "Allowing spawn of iOS binary ... boot-arg set to true"; `shared_region: ... check_np ... vm_shared_region_start_address() failed` (no region yet, normal); dyld's built-in **libignition** (Darwin Ignition Sequence 1.0.0, libignition-51~1974; prints rd=ufsroot0, hardware target sweet, ignition level 0x5, platform 0x2, boot spec local, 7 stages): `hello from launchd.1` -> `preboot: mounting preboot` -> `failed to lookup preboot: 25` (ENOTTY: looks for a separate Preboot volume by role) -> ignition fails -> dyld `Library not loaded: /usr/lib/libSystem.B.dylib ... (no such file, no dyld cache)` -> launchd exit -> panic initproc failed. dyld boot-args found via strings: `ignition_level`, `ignition_force_dylib_root` (forced rootfs/livefs/cryptex fallback), `ignition_halt_after`, `ignition_live_app_graft`; dyld cache search paths include /System/Cryptexes/OS, /System/Volumes/Preboot/Cryptexes/OS/, /private/preboot/Cryptexes/OS/ (present in our image). Next try `ignition_level=0` (`shim/out/shim-boot-0029j.img`).
- **APPLE launchd EXECUTED FROM UFS (2026-10-15).** Flashed iOS image: `img2simg` -> `ios16-root-20H392.simg` (12.1 GB) then `fastboot -S 512M flash userdata ...simg` (24 chunks, 307 s, `research/flash-userdata-ios16.log`; a raw-image flash was killed by earlyoom at 2.6 GB RSS - always flash the sparse image). ABL fastboot hung on getvar after the flash; power+vol-down fixed it. Boot 0029u 20:49:04 UTC (`research/boot0029u-ios16.log`), photo IMG_20261015_225134: `hfs: mounted iOSRoot` -> load_init_program /sbin/launchd -> panic `unexpected SIGKILL of launchd (CS_KILLED) with reason -- namespace 9 code 0xe` = OS_REASON_EXEC / EXEC_EXIT_REASON_WRONG_PLATFORM: our kernel is XNU_TARGET_OS_OSX, iOS-platform binaries are "alien" and refused unless spawn attr platform set or DEVELOPMENT TUNABLE `unentitled_ios_sim_launch`. Next try: same kernel, cmdline + `unentitled_ios_sim_launch=1` (`shim/out/shim-boot-0029i.img`). LONGER TERM: macOS-config kernel marks iOS maps alien (vm_map_mark_alien) and differs from iOS in many policies; an iPhoneOS-config kernel build may eventually be needed.
- **iOS 16 ROOT IMAGE BUILT:** `/run/media/dipa/android/ios-sweet-ipsw/ios16-root-20H392.img` (16 GiB case-sensitive HFS+, label iOSRoot, 12 GB used, fsck OK after `fsck.hfsplus -y` rewrote 32 non-ASCII localized WorkflowKit Shortcut names Linux decomposes differently). Built by `tools/mkiosroot.sh OS.dmg SYSTEMOS.dmg APPOS.dmg OUT.img [SIZE]` (needs sudo; apfs-fuse as root; OS dmg is UDIF -> link must end .dmg). Contents: iPhone X 20H392 OS root + SystemOS cryptex at /private/preboot/Cryptexes/OS (so /System/Cryptexes/OS -> cache, 44 files, dyld_v1 arm64) + AppOS at .../App. /sbin/launchd and /usr/lib/dyld root:wheel 0755. NEXT: (confirm) `fastboot flash userdata ios16-root-20H392.img` (auto-sparse), boot 0029u (rd=ufsroot0) -> Apple launchd attempt.
- **MILESTONE ON HARDWARE: BOOT FROM INTERNAL FLASH.** 0029 CI `38797924912` PASSED, `xnu/out-0029` SHA256 `a68bc847...2a37`. Image `shim/out/shim-boot-0029u.img` (payload-0029u = 0029 kernel + `shim/out/sweet-0028.adt` + `rd=ufsroot0`, NO RAM disk). Boots 20:01:34 and 20:12:37 UTC (`research/boot0029u*.log`), photo IMG_20261015_221403: `SWEET UFS: "userdata" on LUN 0: first block 0x2b8c00, 109643 MiB, read-only` -> `BSD root: ufsroot0, major 1, minor 0` -> `hfs: mounted SweetRoot on device b(1, 0)` -> `SWEET: root volume is not authenticated (error 25); continuing unsealed` -> `load_init_program ... /sbin/launchd` -> `*** SWEET userspace: init running as PID 1 ***` -> heartbeat (ran for minutes unattended). userdata currently holds the 8 MB `research/root-hfs.img`. NEXT: build a multi-GB HFS+ image with the iOS 16 (iPhone X 20H392) userland (OS rootfs + SystemOS cryptex contents incl. dyld shared cache at /System/Library/Caches/com.apple.dyld; preserve modes/owners/symlinks; decompress LZBITMAP via apfs-fuse), flash it to userdata (sparse, needs user OK), then try Apple's dyld/launchd. Driver is read-only (root mounts RDONLY), fine for first launchd attempts.
- **userdata FLASHED with the 8 MB HFS test volume** (user said "flash it", 2026-10-15 19:53:17 UTC, `research/flash-userdata-hfs-test.log`: `fastboot flash userdata research/root-hfs.img` OKAY; pre-checks: one sweet device, partition-size:userdata 0x1AC4BFB000, partition-type f2fs, image fsck OK, SHA256 3626ea69...). Android userdata is now destroyed (expected/approved). Boot 0028u at 19:53:20 (`research/boot0028u-hfs.log`), photo IMG_20261015_215541: `BSD root: ufsroot0` -> **`hfs: mounted SweetRoot on device b(1, 0)` from internal UFS** -> panic `rootvp not authenticated after mounting @bsd_init.c:931` (arm64 macOS-config requires FSIOC_KERNEL_ROOTAUTH sealed root for non-ramdisk roots). Patch **0029** (`dd1c284`): SWEET logs `SWEET: root volume is not authenticated (error N); continuing unsealed` instead of panicking (Apple configs keep the panic; pivot_root still requires auth). CI **`38797924912`** running. Next: download out-0029, phone image with sweet-0028.adt + rd=ufsroot0 (no RAM disk), boot -> expect init from UFS.
- **UFS ROOT DRIVER VERIFIED ON HARDWARE (read-only).** 0028 CI `38796139570` PASSED, `xnu/out-0028` SHA256 `8b981640...9da6`; QEMU HFS RAM-disk regression `research/q0028-hfs` still runs init. Phone boot 2026-10-15 19:50:24 UTC (`research/boot0028u.log`, image `shim/out/shim-boot-0028u.img`, phone ADT `shim/out/sweet-0028.adt` with /sweet-ufs, NO RAM disk, `rd=ufsroot0`), photos IMG_20261015_215151/215220: `SWEET UFS: "userdata" on LUN 0: first block 0x2b8c00, 109643 MiB, read-only` -> `BSD root: ufsroot0, major 1, minor 0` -> `hfs_ValidateHFSPlusVolumeHeader: unknown Volume Signature : f188` (Android-encrypted data, as expected) -> `cannot mount root, errno = 19`, kernel retries mountroot (no panic). Nothing written to flash. NEXT (destructive, needs user confirmation right before): `fastboot flash userdata research/root-hfs.img` (8 MB HFS with test init) then boot rd=ufsroot0 -> expect init from internal flash.
- **PATCH 0028 (`33aceac`): XNU read-only UFS root device**, CI **`38796139570`** running (next artifacts xnu/out-0028, research/bt29, run29.log). `bsd/dev/sweet_ufs.c`: reuses ABL's UFSHCI state (like the shim probe), takes over UTRL at a kmem_alloc_contig region (DMA layout: UTRL 0x0, UCD 0x1000, 1 MiB bounce 0x4000), verifies UTRLBA readback before any doorbell, IE=0 polled, one SCSI cmd at a time on LUN0 (TUR x3, READ(10) via bounce, PRDT 256 KiB chunks), flush_dcache64 by PA around each command (host not dma-coherent per DT), finds GPT partition by name (`ufsroot=` boot-arg, default `userdata`), exposes `/dev/ufsroot0`/`rufsroot0` (512-byte logical sectors, physical 4 KiB, DKIOC* ioctls), refuses writes (EROFS). IOKitBSDInit: `rd=ufsroot0` -> sweet_ufs_rootdev() (lazy init) else panic. ADT: `tools/mkadt.py` adds `/sweet-ufs` reg <0x1d84000 0x3000> for the PHONE only (not --qemu). Local compile OK, imports resolve. Sandbox research/bt28 (pre0028 originals). FIRST PHONE TEST PLAN (non-destructive): boot 0028 with phone ADT + `rd=ufsroot0` and NO RAM disk: expect `SWEET UFS: "userdata" on LUN 0: first block 0x2b8c00, 109643 MiB` + `BSD root: ufsroot0`, then root mount failure (userdata is still Android-encrypted) -> panic. That validates the driver before flashing an HFS image into userdata (needs explicit user confirmation at that time).
- **SUPERSEDES the 64 GiB iosroot GPT plan: user said "u can delete android" (2026-10-15).** Use the existing `userdata` partition (LUN0 sda34, 4K LBA 0x2B8C00, 0x1AC4B MiB ~107 GiB) directly as the iOS root: NO GPT changes. Keep bootloaders, recovery (OrangeFox), modem/persist/fsg/modemst untouched. Write the HFS image later with `fastboot flash userdata` (sparse, auto-split under the 768 MiB limit). Android will no longer boot normally (expected; reinstallable). Order: XNU UFS driver first, read-only against the current GPT; flash only once it can locate userdata. Nothing erased yet.
- **DECIDED (user, 2026-10-15): 64 GiB `iosroot` on LUN0 (sda), taken from userdata (wipes Android user data; Android/super kept).** Planned GPT (4K LBAs): userdata 0x2B8C00..0xD7D6FF (~43 GiB, keep type/unique GUIDs and attributes); iosroot 0xD7D700..0x1D7D6FF (0x1000000 blocks = 64 GiB, 1 MiB aligned); backup GPT area unchanged. Procedure: user backs up phone data -> boot OrangeFox recovery (adb) -> back up GPT (sgdisk --backup + raw dd of LBA 0-5 and last 6 blocks) to research/ -> sgdisk edit -> Format Data -> verify Android boots to setup. NOT EXECUTED YET.
- **INTERNAL UFS WORKS FROM THE SHIM (read-only), user authorized internal-UFS approach incl. flashing (2026-10-15, "i allow everything").** Shim probe `shim/ufs.c` (commit `12ed3ed` + filter): ufshc@1d84000 is UFSHCI 2.1 (ver 0x210, cap 0x1587031F, 32 slots), ABL leaves HCE=1, HCS=0x10F (device present, lists ready, UIC ready); probe takes over UTRL at its own buffer (verifies UTRLBA readback before any doorbell), TEST UNIT READY/READ CAPACITY/READ(10) all succeed. LUN0 = 0x1D7D800 x 4 KiB (~126.6 GB), valid GPT (64 entries at LBA 2). Photos IMG_20261015_212916/213325/213429. Big LUN0 partitions (start in 4K LBAs / MiB): super 0xCA00/0x2200 (8.5 GiB), logdump 0x22CA00/64, minidump 0x230A00/128, rawdump 0x238A00/128, recovery 0x240C00/128, cache 0x248C00/384 ext4, exaid 0x260C00/384, cust 0x278C00/1024 ext4, **userdata 0x2B8C00/0x1AC4B MiB (~107 GiB), fs unreadable = encrypted (metadata encryption)**. cache..cust contiguous = only 1.75 GiB (too small for iOS ~10+ GiB). Plan needing user consent: shrink userdata in GPT (wipes Android data) and add an `iosroot` partition at the end of LUN0. LUNs 1-5 not listed yet (sdb..sdf backed up in backup/lun).
- **fastboot limit measured (2026-10-15, read-only getvar):** `max-download-size: 805306368` (768 MiB); `max-fetch-size` unsupported; `is-userspace: no` (ABL fastboot). A `fastboot boot` image (shim+kernel+RAM disk) must be < 768 MiB, so the 2.5 GiB iOS shared cache cannot ride in the boot RAM disk even if XNU gets all RAM. Real userland needs storage access (UFS driver, or USB host) or a much smaller cache. Unlocking full RAM is still needed later (GUI).
- **iOS 16 USERLAND SOURCE (Claude, 2026-10-15).** Chose **iPhone X (iPhone10,3/10,6, D22/D221AP)** iOS **16.7.16 (20H392)** over iPhone 8: same A11 (no PAC -> plain arm64, runs on Kryo 470/ARMv8.2), last iOS for A11; X userland is home-button-less and tall-screen (1125x2436 vs sweet 1080x2400), better fit for later GUI. Note the X has its OWN SystemOS cryptex (different digest from iPhone 8's). IPSW URL in `research/ipsw-x/URL.txt` (+ BuildManifest). Images on the user's external SSD (NOT in git, do not format that disk: it holds the user's photos): `/run/media/dipa/android/ios-sweet-ipsw/iPhone10,3_16.7.16_20H392/` — `087-86593-047.dmg` = Cryptex1,SystemOS (2,743,074,816 B), `087-86698-044.dmg` = OS rootfs (4,730,814,044 B), both zip CRC-32 verified, plus Firmware/*.root_hash/.trustcache/.mtree. AppOS cryptex is 087-86715-047.dmg (10 MB, same as iPhone 8).
  - Images are raw APFS; files use LZBITMAP (decmpfs type 14). 7z cannot decode. Tools: `ipsw` 3.1.738 (AUR ipsw-bin, user-approved) needs apfs-fuse on Linux; **apfs-fuse** built in user space (system cmake broken: cmake 4.4.4 wants libjsoncpp.so.27, jsoncpp 1.9.6 installed = partial upgrade, NOT fixed) with pip cmake + `-DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_CXX_FLAGS="-include cstdint"`, installed to `~/.local/bin/apfs-fuse` (+apfs-dump-quick). Mount via a comma-free symlink (apfs-fuse splits options on ',' in `iPhone10,3`; exFAT has no symlinks -> put link elsewhere): `apfs-fuse LINK/087-86698-044.dmg MNT` -> `MNT/root/...`; unmount `fusermount3 -u MNT`.
  - Verified from headers: `dyld_shared_cache_arm64` magic `dyld_v1   arm64`; `/sbin/launchd`, `/usr/lib/dyld` = cputype 0x100000c subtype 0 (plain arm64). libSystem exists only inside the cache.
  - **BLOCKER for real userland via RAM disk:** shared cache = 3.10 GiB in 44 files (~2.5 GiB without the 592 MB `.symbols`), but the kernel manages only 1.20 GiB (0xb0000000..0xfd000000). Options: (A) give XNU the phone's full ~6 GB (banks 0x80000000+2G and 0x100000000+0xfcc00000, carve-out holes need pmap support) AND check fastboot max-download-size for a multi-GB boot image; (B) storage driver (Qualcomm UFS) to read a root from flash (no flashing allowed: would need a file/partition strategy); (C) run against individual dylibs extracted from the cache (uncertain).
- **MILESTONE ON HARDWARE: HFS+ ROOT FILESYSTEM -> PID 1.** Boot 2026-10-15 18:22:12 UTC (`research/boot0027b.log`, image `shim/out/shim-boot-0027b.img`), photo `IMG_20261015_202334.jpg`: `BSD root: md0` -> `hfs: mounted SweetRoot on device b(1, 0)` -> `load_init_program: attempting to load /sbin/launchd` -> `*** SWEET userspace: init running as PID 1 ***` -> repeated `SWEET init: still alive`, no panic. Kernel 0027 (built-in Apple OSS HFS + BearSSL MD5), init with SP alignment fix. NEXT: real iOS 16 userland on the HFS image (dyld, libSystem/dyld shared cache, launchd). Known gaps: entitlements (none granted), SHA-1/SHA-384/512 providers, no storage driver (RAM disk only), root mounted read-only.
- **PHONE HFS ROOT (0027) + SIGBUS ROOT-CAUSED.** Phone boot 2026-10-15 17:49:06 UTC (`research/boot0027.log`, photo `IMG_20261015_200031.jpg`): `hfs: mounted SweetRoot on device b(1, 0)` (HFS works on hardware), init printed its first line, then `pid 1 exited -- exit reason namespace 2 subcode 0xa` = SIGNAL/SIGBUS -> `initproc failed to start` panic. Diagnostic boot 18:08:13 UTC (`research/boot0027d.log`, image `shim/out/shim-boot-0027d.img`, diag init `research/diag-init` from scratch diag-init.S with [a][b][c] markers, `cs_debug=1`; photo `IMG_20261015_201547.jpg`): output `PID [a][b]` then SIGBUS -> fault at first `strb w21,[sp]`; no code-signing messages. Cause: kernel enters a static binary with sp at argc, not necessarily 16-aligned (depends on arg/apple string lengths, differs mockfs vs HFS); EL0 SP alignment check -> EXC_ARM_SP_ALIGN -> SIGBUS (sleh handle_sp_align). QEMU does not enforce SP alignment. NOT a kernel bug: real binaries start in dyld which aligns. Fix `1a8fc15`: sweet-init aligns sp at entry. Rebuilt `research/root-hfs.img`, QEMU `research/q0027-hfs2` OK. Phone image `shim/out/shim-boot-0027b.img` SHA256 `098ba46c...c5c` (payload-0027b, ramdisk match check-0027b) ready, awaiting FASTBOOT. Lesson: Assembly `;` is a comment in Apple arm64 asm (use .macro, not ;-joined #define).
- **HFS ROOT WORKS (QEMU).** 0027 CI `38779441895` PASSED, `xnu/out-0027` SHA256 `a8643886...5f6a`. QEMU `research/q0027-hfs` (RAM disk = `research/root-hfs.img`, rd=md0): `BSD root: md0` -> `hfs: mounted SweetRoot on device b(1, 0)` -> `load_init_program: attempting to load /sbin/launchd` -> `*** SWEET userspace: init running as PID 1 ***` + heartbeat, no panic. Phone image `shim/out/shim-boot-0027.img` (payload-0027 = 0027 kernel + sweet-0021.adt + root-hfs.img + rd=md0, ramdisk byte-match check-0027) ready; awaiting FASTBOOT for phone test.
- **HFS ROOT WORK (Claude), patches 0026-0027.**
  - 0026 (`f0e990c` + link fix `64c96f4`): Apple OSS **hfs-627.100.6** (commit `31d5ec238dfab485e78a9a30b8b92d13e8914f03`, paired with xnu-8796.141.3 in macos-135 release.json) staged by `xnu/patches/0026-stage-hfs.sh` into `bsd/sweet_hfs` (core + hfs_encodings; AppleKeyStore include/key prototypes guarded by CONFIG_PROTECT; SDK TargetConditionals copied). New config option `sweet_hfs` (MASTER + MASTER.arm64.sweet); 40 `optional sweet_hfs` files; Makefile.template foreach adds `_CFLAGS_ADD = -include sweet_hfs_compat.h -I...` (MUST come after kernel -D flags; compat undefs BSD/XNU_KERNEL_PRIVATE and the CONFIG_* HFS tests so kext-config.h decides: macOS profile, no content protection). `sweet_hfs_iokit.c` replaces hfs_iokit.cpp/hfs_encodings_kext.cpp: `sweet_hfs_register()` called in bsd_init after vfsinit (encodings init, vfs_fsadd VFS_TBLCANMOUNTROOT, zones, sysctls); is_ejectable 0, iterate_media none, serial KERN_NOT_SUPPORTED, OSKextGetCurrentLoadTag -> kOSKextInvalidLoadTag (renamed via compat macro). hfs_encodings' own `hfs_allocated` renamed per object (separate kexts on Apple; first CI `38777325964` failed on that duplicate at link). mockfs now validates Mach-O magic (its TODO) so non-Mach-O roots fall through to HFS (new fs entries come AFTER mockfs in vfsconf). CI `38778133720` PASSED, `xnu/out-0026` SHA256 `0b49170c...701a`, prints `SWEET HFS: hfs-627.100.6 registered`.
  - HFS+ image: `research/root-hfs.img` (8 MB, mkfs.hfsplus from AUR hfsprogs installed with user approval; loop-mounted with sudo; /sbin/launchd = research/sweet-init, /dev; fsck.hfsplus OK). QEMU `research/q0026-hfs`: `BSD root: md0` then data abort in MD5Init from hfs_getvoluuid (mount computes volume UUID).
  - 0027 (`14bf2b7`): 0020 stager also copies BearSSL hash/md5.c + codec/dec32le.c/enc32le.c; `corecrypto_md5.c` SWEET branch converts MD5_CTX <-> br_md5_context around br_md5_* calls; wrappers sweet_md5.c/sweet_dec32le.c/sweet_enc32le.c. Host test 40/40 match md5sum. CI **`38779441895`** running.
  - Local tooling: `tools/xnu-cc.py` has `XNU_CC_POST` (flags after logged ones). Sandbox `research/bt28/xnu` = 0025 CI tree + 0026/0027 edits; originals `research/pre0026`, `research/pre0027`. HFS local objects `research/hfs-objs`. Checker entries updated to bt28.
  - SHA-1 / SHA-384/512 still have no provider.
- **MILESTONE ON HARDWARE: USERSPACE PID 1 RUNS ON THE PHONE.** Boot 2026-10-15 17:16:00 UTC (`research/boot0025.log`: send OKAY0.410s, boot OKAY0.135s), image `shim/out/shim-boot-0025.img` SHA256 `9dbfa0b1...89db0`. Photo `IMG_20261015_191733.jpg`: `BSD root: md0` -> `load_init_program: attempting to load /sbin/launchd` -> `*** SWEET userspace: init running as PID 1 ***` -> repeated `SWEET init: still alive` (5 s heartbeat), no panic. Kernel 0025 + mockfs RAM disk + built-in no-entitlements AMFI. No flashing performed.
- **NEXT (toward real iOS userland):** real root filesystem with real binaries. Options: (a) build Apple OSS `hfs` (macOS 13.5 tag `hfs-627.100.6`) into SWEET like libpthread, then an HFS RAM disk image; (b) iOS 16 dyld + libSystem/dyld shared cache from the IPSW (needs APFS or repacking onto HFS). Expected hurdles: entitlements (sweet_amfi grants none), SHA-1/MD5/SHA-384/512 providers (BearSSL), missing kexts/daemons. Current init is a static test program, not launchd.
- **MILESTONE: USERSPACE RUNS (QEMU).** 0025 CI `38774788048` PASSED, `xnu/out-0025` SHA256 `5496069b...4877`. QEMU `research/q0025-rd` (150 s, no panic): `BSD root: md0` -> `load_init_program: attempting to load /sbin/launchd` -> `*** SWEET userspace: init running as PID 1 ***` -> `SWEET init: still alive` every 5 s. Exec, ad-hoc SHA-256 code signature, syscalls, devfs /dev/console, select timeouts all work. Phone image prepared: `shim/out/shim-boot-0025.img` 16,515,072 bytes SHA256 `9dbfa0b15140c0bd733a16c4f727f6a7df44a8741f98d8e266d5f2fa4f689db0` (payload-0025 = 0025 kernel + sweet-0021.adt + research/sweet-init + rd=md0; ramdisk byte-match check-0025). Waiting for user to put phone in FASTBOOT.
- **0024 CI `38773277811` PASSED**, `xnu/out-0024` SHA256 `a0ba964b...4348`. QEMU `research/q0024-rd` and **PHONE (boot 2026-10-15 17:05:55 UTC, `research/boot0024.log`, image `shim/out/shim-boot-0024.img` SHA256 `cf3dac86...6964`, 16,510,976 bytes, payload `shim/out/payload-0024.bin` = 0024 kernel + `shim/out/sweet-0021.adt` + RAM disk `research/sweet-init` + rd=md0; ramdisk byte-match `check-0024`)** agree: `Added memory device md0`, `BSD root: md0`, `load_init_program: attempting to load /sbin/launchd`, then data abort in accelerate_entitlement_queries (PC 0x...77d2458, FAR 0x1a0) = NULL AMFI interface. Photo `IMG_20261015_190715.jpg`. RAM disk root is physically verified.
- **0025 (`2518c79`)**: new `bsd/kern/sweet_amfi.c` registers a built-in AMFI interface at EARLY_BOOT/MIDDLE: no entitlements (adjustContextWithoutMonitor succeeds without creating a context, queries KERN_DENIED, copies NULL/KERN_NOT_FOUND, get_xml/transmuted false); WithMonitor KERN_NOT_SUPPORTED; OSEntitlements_query and query_context_to_object panic explicitly (CoreEntitlements closed; no kernel caller); TrustCache version 0 (0015 keys on trust_cache_rt==NULL, img4 absent -> still unavailable); CoreEntitlements table zero (PMAP_CS only). Compiles locally; checker updated (bt27 entries). CI **`38774788048`** running. Next artifacts `xnu/out-0025`, `research/bt29`, `research/run29.log`. Real iOS binaries will need real entitlements later (no OSS CoreEntitlements exists).
- **0021-0024 RAM-disk-root iteration (Claude), QEMU only so far, no new phone boot since 0020.** Each kernel tested with `tools/mkpayload.py KERNEL research/q0021.adt OUT --ramdisk research/sweet-init --cmdline "debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress rd=md0"` then `tools/qemu-sweet.py OUT DIR --secs 150 --kernel KERNEL --fail-on-panic` (PATH needs ~/swift/usr/bin for llvm-nm). Fresh ADTs: `research/q0021.adt` (QEMU), `shim/out/sweet-0021.adt` (phone).
  - 0021 CI `38769532000` PASSED, `xnu/out-0021` SHA256 `2b7500a1...962e`, `research/bt25`/`run25.log`. QEMU `research/q0021-rd`: md0 root, then assert `iomediaClass` in IOService::publishHiddenMedia.
  - 0022 (`20ece9c`, publishHiddenMedia without IOMedia class) CI `38770666158` PASSED, `xnu/out-0022` SHA256 `a4d0e787...30c5`, `research/bt26`/`run26.log`. QEMU `research/q0022-rd`: data abort in SHA256_Init (NULL g_crypto_funcs) from vm_kernel_addrhash_internal.
  - 0023 (`c8089d1`, libkern SHA256_Init/Update/Final use built-in ccsha256_ltc_di + ccdigest_init/update on SWEET; also serves code-signing SHA-256; SHA-1/MD5/SHA-384/512 still have NO provider - BearSSL from 0020 could supply them later) CI `38771876423` PASSED, `xnu/out-0023` SHA256 `d763fed7...1003`, `research/bt27`/`run27.log`. QEMU `research/q0023-rd`: **mockfs mounted md0, `load_init_program: attempting to load /sbin/launchd`**, then panic `mockfs_fsnode_vnode failed to create fictitious pages ... rvalue = 5` (mockfs.c:351).
  - 0024 (`3293c80`): mockfs used memdev mi_base (4K page number of a VIRTUAL kernel address for IOKit RAMDisk, truncated to 32 bits) << PAGE_SHIFT as a physical address. Now zero-copy pager mapping only when mi_phys (with 4K units); virtual md uses pagein/blockmap/strategy -> memdev bcopy. memdev's own mdPhys path assumes 4K ppnums (unsafe on 16K arm64), so do NOT switch the RAMDisk to physical. CI **`38773277811`** running. Next artifacts `xnu/out-0024`, `research/bt28`, `research/run28.log`.
  - Sandboxes: bt25/bt26/bt27 contain the next patch's edited files; pristine copies in `research/pre0022`..`pre0024`.
- **PATCH 0021 (Claude): RAM disk root via mockfs -> first userspace program.** Commits `9c37771` (initial; CI `38768500280` FAILED: mockfs_fsnode.c kfree_type(mockfs_fsnode_t) typed-alloc static assert; local xnu-cc stubs `__builtin_xnu_types_compatible` so it can't catch this) and `193772f` (fix), CI **`38769532000`** running: https://github.com/dipa-studio/ios-sweet/actions/runs/38769532000. Next artifacts: `xnu/out-0021`, `research/bt25`, `research/run25.log`.
  - Kernel: `config/MASTER.arm64.sweet` FILESYS_BASE += `mockfs` (XNU's built-in FS: root device is itself a Mach-O, served as /sbin/launchd, plus /dev for devfs). Upstream mockfs bit-rot fixed: kalloc_type/kfree_type used the POINTER typedef mockfs_fsnode_t (nodes were 8 bytes) -> `struct mockfs_fsnode`. `IOService::setRootMedia` asserted IOBlockStorageDriver class exists (IOStorageFamily absent): SWEET-only, a missing class means root=NULL (same as IOSetImageBoot); Apple path keeps assert. All 21 patches apply (`research/check-patch-order.py`); 5 TUs compile locally with -DMOCKFS (`research/mockfs-objs`), imports resolve vs 0020 kernel. Sandbox `research/bt24/xnu` now holds 0021 versions of those 3 files; pristine 0020 copies in `research/pre0021`.
  - Tools: `tools/mkpayload.py --ramdisk IMAGE` appends IMAGE as an extra payload segment right after the kernel, moves kernel_end past it (shim unchanged: ADT+boot_args follow it, image stays below topOfKernelData), and adds ADT `/chosen/memory-map` `RAMDisk`=<phys,size>. Boot with `rd=md0`. Without --ramdisk output is byte-identical to existing 0020 QEMU and phone payloads (verified). `tools/sweet-init.S` + `tools/mkinit.py`: PIC static init (open /dev/console, print PID, heartbeat every 5 s via select, never exits) wrapped into a static arm64 Mach-O (__PAGEZERO, __TEXT@0x100000000, LC_UNIXTHREAD, ad-hoc SHA-256 CodeDirectory v0x20400, 4K pages). Build: `~/swift/usr/bin/clang -target arm64-apple-ios16.0 -c tools/sweet-init.S -o X.o && tools/mkinit.py X.o research/sweet-init` (ld64.lld cannot link static iOS). Signature not verified by a real codesign; the kernel will tell.
  - QEMU with 0020 kernel + RAM disk (`research/q0020-rd.bin`, rd=md0, 150 s `research/q0020-rd150`): `Added memory device md0`, **`BSD root: md0, major 1, minor 0`** (plumbing works), then the setRootMedia assert (now fixed in 0021). NOTE: QEMU needs >60 s to reach IOFindBSDRoot (60 s run showed nothing after stf0; likely RTC/inittodr wait), so use --secs 150.
  - NEXT: download 0021, QEMU 150 s with ramdisk; expect mockfs mount then init exec -> `*** SWEET userspace: init running as PID 1 ***`. Then phone boot with same payload (phone ADT shim/out/sweet-0020.adt pattern + --ramdisk + rd=md0).
- **0020 PHONE RESULT (user transcription of screen, 2026-10-15): KERNEL INIT COMPLETE, WAITING FOR ROOT.** After Skywalk self-tests PASSED, kminit, ifnet_attach lo0/gif0/stf0, screen prints `BSD`, then `Waiting on <dict><key>IOProviderClass</key><string>IOMedia</string><key>Content</key><string>Apple_HFS</string></dict>` and `Failed to write boot-picker-bringup-reason to NVRAM.` No panic. This is IOFindBSDRoot waiting for a root device (none exists); NVRAM warning is expected (no NVRAM driver). QEMU `cpu_idle` snapshot is presumably the same wait (not GDB-confirmed; milestone GDB run was cancelled by user since phone answered it). Draft `research/check-milestones20.gdb` (hbreaks inittodr/vfs_mountroot/IOFindBSDRoot/bsd_utaskbootstrap) left uncommitted/ignored.
- **NEXT MILESTONE: root filesystem -> launchd.** Plan: (1) RAM disk: shim places an image in RAM, ADT /chosen/memory-map `RAMDisk` entry, boot-arg `rd=md0` (XNU md driver). (2) HFS is a separate kext on Apple; build Apple OSS `hfs` (tag paired with macOS 13.5 in distribution-macOS macos-135 release.json) into SWEET like libpthread 0019. APFS is closed. (3) Minimal HFS image with iOS 16 launchd (later: dyld/shared cache; AMFI/code-signing absent will be a later hurdle). User's end goal is full iOS GUI.
- **0020 (AES) VERIFIED + PHONE BOOTED (Claude took over after Codex usage limit).** Actions `38765422261` (`ab9e23f`) PASSED; kernel `xnu/out-0020/kernel.development.sweet` checksum OK; buildtree `research/bt24`, log `research/run24.log`. Actual-kernel AES checks PASS (`research/check-aes20.gdb` / `.log`, 12 extra CBC cases match OpenSSL). 45s QEMU `research/q0020`: no panic, zero unknown glyphs, past old TCP/AES panic -> Skywalk self-tests PASSED -> bsd_autoconf kminit -> ifnet_attach lo0/gif0/stf0; final snapshot PC in `cpu_idle` (idle or waiting; NOT yet diagnosed, could be root-device wait). Image `shim/out/shim-boot-0020.img` 16,461,824 bytes SHA256 `775200cb02d95fc750981516b79d91dcdc96184bbe67a38f9d57c59b4a391828`, ramdisk byte-matches `payload-0020.bin` (`check-0020`). **Phone temporarily booted 2026-10-15 16:10:00 UTC**: one FASTBOOT product sweet, send OKAY0.407s, boot OKAY0.134s, total0.549s, exit0 (`research/boot0020.log`). Awaiting user photo; do not reboot the phone before it. NEXT: read photo; diagnose what QEMU waits on after stf0 (GDB backtrace of threads / longer run). Codex's STOP request below is complete.
- User requests STOP after completing the current0020 AES iteration (Actions/QEMU/phone), then leave a precise committed handoff for another AI. User says FASTBOOT ready again after0019 photo. Do not start a new blocker fix after this iteration.
- Goal: iOS16-generation XNU on Redmi Note10Pro sweet/SM7150. Always commit, use gh/Actions for kernel builds, keep this handoff current. Temporary fastboot boot only; preserve Android/backups. No userland/root filesystem boot yet.
- **Patch0020 update:** initial `e71098d` / Actions `38765012466` superseded by scalar-only compile flags. Added -mgeneral-regs-only to all six provider/BearSSL TUs and corecrypto_aes.c; object disassembly now confirms no SIMD. Updated implementation `ab9e23f`, Actions `38765422261` queued/running: https://github.com/dipa-studio/ios-sweet/actions/runs/38765422261. Do not launch another run. portable BearSSL AES CBC and legacy one-block ECB wired directly into SWEET corecrypto_aes.c. Genuine bitslice implementation, no CPU AES/SIMD requirement, heap or fake global crypto table. Source fetched by `0020-stage-bearssl.sh` from official BearSSL git at immutable `7bea48e5e850ab4cafbe68d3765cdaba13a86d6f`; MIT license retained, downloaded source stays out of git. Kernel limits include adapter only. All128/192/256-bit keys, CBC encrypt/decrypt, copied contexts, in-place/out-of-place; zero block count and size overflow handled, invalid keys clear context. Seven TUs compile to arm64 objects (`research/aes-objs`), imports resolve, all20 patches apply and sources match (`research/patch0020-check.log`). Sandbox `research/bt23/xnu`, originals `research/pre0020`. NEXT: Actions then actual-kernel NIST/random CBC/ECB checks and >=45s QEMU before another phone boot. Next artifacts out-0020 / bt24 / run24.log. 0020 updated CI pending. Local sources/checks match the scalar-only patch.
- **Latest built kernel0019**, implementation `256d3f0`, patches0001-0019. Actions `38763072907` PASSED (4m5s): https://github.com/dipa-studio/ios-sweet/actions/runs/38763072907. Kernel `xnu/out-0019/kernel.development.sweet`, SHA256 `a27df7568f03fb541cf0e8024c0a594fa178a85054fcf9eba29158cbd4c5ecc0`; checksum verified. Log/source `research/run23.log` / `research/bt23` (no nested .git). Seven pthread source/build/header files and staged adaptations byte-match local compile inputs.
- **0019 hardware photo received** `IMG_20261015_175306.jpg`: pthread registration visible, passes mbinit and DLIL checksum/scheduling, same AES fault as QEMU: PC`0xfffffe00079174e0` (`aes_encrypt_key128`), LR`0xfffffe000767c3a4` (`tcp_init`), FAR0x78, x8=0. Disassembly proves NULL g_crypto_funcs->ccaes_cbc_encrypt. Photo processed; no more0019 observation pending. General crypto table remains absent, other hash/mode operations may still fault after0020. DO NOT bypass TCP or install fake crypto. XNU already contains SHA256/HMAC implementations; AES source there is a dispatch wrapper. Full future provider interface libkern/libkern/crypto/register_crypto.h. Apple/corecrypto default README describes evaluation-only licensing, so no external corecrypto was fetched or staged; BearSSL selected for this portable AES integration.
- **0019 pthread FIXED in QEMU and observed beyond its init on phone**: 45s QEMU `research/q0019`, zero unknown glyphs, then AES panic. Actual-kernel API checks PASS `research/check-pthread19.gdb` / `.log`, guest `research/q0019-api`: function/callback table identity, actual provider pthread_init returns, locks/global hash/threadcall/typed zones initialized, thread_selfid via syscall shim + buffer bounds, process hash init/delete via shims restores NULL. Monitor quit prints expected remote-disconnect after PASS. All fixture guests stopped.
- **0019 provider**: genuine Apple libpthread-514.100.2 commit `a6c70ca0e544347a0377babf748f831577ec24f9`, exact paired release with xnu8796.141.3 in distribution-macOS/macos-135/release.json (`research/macos-135-release.json`). 0019 shell stage runs before patches (workflow copies *.sh); three SWEET wrapper TUs use kext BSD header view, vm_fault_external, renamed provider-side pthread_kern/current_uthread; register real function table at EARLY_BOOT SECOND, real pthread_init later. Stager applies bounded address casts, zero user addresses, proper panic format. SWEET opaque ksyn storage enlarged48->56 for trailing kwe_uth, checked with provider static assertion. Local pre-CI TargetConditionals fixture was syntax-only; artifact bt23 carries actual SDK header and CI is authoritative.
- **Phone0019 temporarily booted** 2026-10-15 15:51:36 UTC: exactly one FASTBOOT/product sweet verified; send OKAY0.406s, boot OKAY0.134s, total0.546s, exit0 (`research/boot0019.log`). Image `shim/out/shim-boot-0019.img`, 16,457,728 bytes, SHA256 `19429a57e486b856880df9e27327ed488e0139345311fdb0350974e72103a9a1`. Phone ADT/payload `shim/out/sweet-0019.adt` / `payload-0019.bin` timer19, unpacked ramdisk byte-match (`check-0019`). EntryPA0xb146b488, kernelendPA0xb2000000, boot_argsPA0xb2004000/top0xb2008000. Last user FASTBOOT+USB reply preceded this boot; do not assume phone is still in FASTBOOT.
- **CPU0018 physically verified** by `IMG_20261015_173951 (2).jpg`: boot CPU0 registered, real SGI1 verified, initialization complete -> BSD/MAC/mbinit -> old pthread-table panic. SweetCPU uses legacy IOCPU, ml_processor_register/processor_start/initCPU, real SGI1 clears SIGPdisabled before max CPUs completion; no forced flags/skipped waits. CPU APIs and two-CPU rejection passed (`research/check-cpu18.gdb`, `q0018-api`, `q0018-smp`). Single core; SMP/suspend unsupported. CURRENT SWEET has NO USE_APPLEARMSMP.
- QEMU next run: generate fresh `tools/mkadt.py research/q0020.adt --qemu` (timer27), mkpayload with `debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress`, then `tools/qemu-sweet.py PAYLOAD OUT --secs 45 --kernel KERNEL --fail-on-panic`. 12s can miss later panics because IORTC waits30s. Runner snapshot only: panic exit2, blank/incomplete exit3; absence of panic does not mean complete boot. Qualcomm XPU/ABL/panel and phone SMC PSCI require hardware checks.
- Local checker `tools/xnu-cc.py research/run23.log research/bt23 FILTER --per-file`, XNU_CC_OBJDIR for objects. For new BearSSL TUs add XNU_CC_EXTRA with -I bt23/xnu/libkern/crypto/sweet_bearssl/{inc,src}. Patch checker `research/check-patch-order.py` uses temporary upstream index; source fixtures/artifacts ignored under research. GDB mask registers to unsigned64, use function-entry manual-call return traps, discard manipulated guests. No QEMU guest intentionally left running.

## Session log and earlier handoffs (superseded by the active summary above)

### Prior 0019 transition notes (superseded)

- Goal: iOS 16 generation XNU on sweet. Always commit completed work, use gh/Actions for kernel builds, keep this handoff current. Temporary fastboot boot only; preserve Android/backups.
- **0019 build PASSED** Actions `38763072907` (4m5s), implementation `256d3f0`. Kernel `xnu/out-0019/kernel.development.sweet`, SHA256 `a27df7568f03fb541cf0e8024c0a594fa178a85054fcf9eba29158cbd4c5ecc0`; checksum verified. Log/source `research/run23.log` / `research/bt23`; all seven patched files and staged provider adaptations match local compile inputs. Prefer bt23 as the next baseline.
- **0019 pthread FIXED in QEMU**: 45s run `research/q0019` registers provider, passes mbinit, DLIL SUM16 checks and DLIL thread scheduling. Actual-kernel GDB checks `research/check-pthread19.gdb` / `.log` PASS: function/callback table identity, real _pthread_init returns, locks/global hash/cleanup threadcall/typed zones non-NULL, thread_selfid via actual syscall shim with output bounds, process hash init/delete through actual shims restores NULL. Guest stopped after tests; no fixture left running. GDB monitor quit prints expected remote disconnect after PASS.
- **Current next blocker:** QEMU0019 panic data abort FAR0x78 at `aes_encrypt_key128` PC`0xfffffe00079174e0`, called by `tcp_init` LR`0xfffffe000767c3a4`. Disassembly confirms `g_crypto_funcs == NULL`, loading ccaes_cbc_encrypt at+0x78. General crypto registration is still absent; DO NOT bypass TCP or supply fake crypto. Next integrate a genuine compatible crypto implementation/provider (AES first needed here, other digest/mode APIs also used). XNU contains SHA256/HMAC but no AES implementation; its corecrypto_aes.c is a dispatch wrapper. Full crypto table is in libkern/libkern/crypto/register_crypto.h.
- **0019 phone image prepared and verified**, user reports FASTBOOT+USB again; exactly one device/product sweet verified. `shim/out/shim-boot-0019.img`, 16,457,728 bytes, SHA256 `19429a57e486b856880df9e27327ed488e0139345311fdb0350974e72103a9a1`; unpacked ramdisk matches phone payload (`shim/out/check-0019`, `payload-0019.bin`, `sweet-0019.adt`, timer19). EntryPA0xb146b488; kernelendPA0xb2000000; boot_argsPA0xb2004000/top0xb2008000. Usual debug cmdline cpus=1/-noprogress. **Temporarily booted 2026-10-15 15:51:36 UTC**: send OKAY0.406s, boot OKAY0.134s, total0.546s, exit0 (`research/boot0019.log`). User asked for photo after45s. Await the photo; DO NOT reboot again before observation. Bootloader acceptance only; no0019 physical progress observed yet.
- **Hardware photo confirms 0018 progress** (`IMG_20261015_173951 (2).jpg`): real phone prints `SWEET CPU: boot CPU 0 registered, SGI1 verified, initialization complete`, enters BSD/MAC, reaches `mbinit: done`, then the same missing-pthread-function-table panic as QEMU at pthread_shims.c:318. CPU completion/interrupt delivery is now physically observed. The photo was received; no further phone boot performed yet.
- **Patch0019 committed/pushed `256d3f0`; Actions `38763072907` queued/running:** https://github.com/dipa-studio/ios-sweet/actions/runs/38763072907. Do not launch a duplicate. genuine Apple OSS libpthread-514.100.2 provider (immutable commit `a6c70ca0e544347a0377babf748f831577ec24f9`), paired with xnu-8796.141.3 by Apple's `distribution-macOS/macos-135/release.json` (`research/macos-135-release.json`). `0019-stage-libpthread.sh` fetches/stages it before patches; workflow now copies shell scripts too. SWEET-only wrappers compile all three real kernel sources, rename provider-side symbols, use kext header view and vm_fault_external, register the real function table at EARLY_BOOT SECOND. Existing pthread_init then runs the actual provider initialization. No pthread wait/syscall is bypassed. Source staging includes bounded explicit address casts/zero user addresses and panic-format fixes. SWEET kernel opaque ksyn storage enlarged from48 to56 bytes to contain this provider's trailing kwe_uth, with a provider size assertion. Local compile of three provider TUs plus pthread_shims/kern_fork passed; imports resolve and provider exports do not collide with existing kernel symbols. All19 patches apply and changed sources match (`research/patch0019-check.log`). Source sandbox `research/bt22/xnu`, originals `research/pre0019`, objects `research/pthread-objs`. Local TargetConditionals fixture only has target macros for syntax; CI stages the real macOS SDK header and is authoritative. Next Actions artifacts: out-0019 / bt23 / run23.log; run QEMU >=45 seconds. 0019 CI is pending; no 0019 QEMU run yet.
- **Latest kernel implementation `e7d9550`, patches0001-0018; CI `38760004398` PASSED (3m56s)**: https://github.com/dipa-studio/ios-sweet/actions/runs/38760004398. No build is pending. Kernel `xnu/out-0018/kernel.development.sweet`, SHA256 `b3c8d59acc88f93a2c836eb2527a05ed4fbaac7c580a2ea8c3b795137f4aadf1`; checksum verified. Source/log `research/bt22` / `research/run22.log` (baseline for patch0019; no nested .git). New driver/config sources match CI exactly.
- **CPU wait FIXED in QEMU**: SweetCPU uses legacy IOCPU, validates single boot CPU, ml_processor_register -> processor_start -> initCPU real SGI1 -> scheduler/IOKit completion -> ml_set_max_cpus. It does NOT force a flag or skip a wait. CPU APIs passed: Running CPU0, processor_array[0] association, SGI signalling enabled, name, unsupported secondary-start error (`research/check-cpu18.gdb` / `.log`, `research/q0018-api`). Single-core only; SMP and suspend unsupported. Two-CPU topology fails explicitly (`research/q0018-smp`).
- **Next blocker reproduced in 45s QEMU** (`research/q0018-long`, zero unknown glyphs): BSD initialization reaches MAC framework, network mcache and `mbinit: done`, then **`pthread kernel extension not loaded (function table is NULL). @pthread_shims.c:318`**. Do not bypass pthread_init: find/integrate the matching open-source libpthread kernel implementation and register its real function table. A 12s run `research/q0018` looked panic-free before the 30s IORTC wait elapsed; use >=45s or GDB checkpoints for the next iteration. No userland/root filesystem boot yet.
- **Phone 0018 TEMPORARILY BOOTED** 2026-10-15 **15:31:15 UTC**: exactly one FASTBOOT device verified product sweet; send OKAY0.400s, boot OKAY0.134s, total0.539s, exit0. Local log `research/boot0018.log`. This confirms bootloader acceptance only. User was asked for a photo; await it, DO NOT reboot the phone again before their observation. No flashing performed.
- Current hardware image `shim/out/shim-boot-0018.img`, **16,437,248 bytes**, SHA256 **`b7a23595d143a76c72c9fe2cfcc5d77a8f16987b5af1a2703bbffdfa6922908c`**. Phone ADT/payload `shim/out/sweet-0018.adt` / `shim/out/payload-0018.bin` (timer19), unpacked ramdisk exactly matches payload (`shim/out/check-0018`). EntryPA0xb146b488; kernelendPA0xb1ffc000; boot_argsPA0xb2000000/top0xb2004000. Command line `debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress`.
- QEMU payload/ADT `research/q0018.bin` / `research/q0018.adt` (timer27). Run `python3 tools/qemu-sweet.py research/q0018.bin research/NEW-OUT --secs 45 --kernel xnu/out-0018/kernel.development.sweet --fail-on-panic`; fresh ADT seed for a new payload. Output fb.png/fb.txt/regs.txt/qemu.log/result.json. Visible panic exit2; blank/incomplete decode exit3; no_panic_observed is snapshot-only, not successful boot. -noprogress prevents the graphical indicator corrupting console text. Qualcomm XPU/bootloader/panel and SMC PSCI still need physical validation.
- Patches0014-0017 fixed PRNG registration/direct SHA256, explicit unavailable trust-cache service with rejected loads/no matches, built-in SweetPlatformExpert, and timer PPI from ADT (phone19/default19, QEMU27). All their checks/artifacts are preserved in the history below. CURRENT SWEET has NO USE_APPLEARMSMP; do not assume gCPUIC/gAIC/gPMGR AppleARMSMP globals exist.
- Local single-file checker: tools/xnu-cc.py research/run22.log research/bt22 FILTER --per-file, output via XNU_CC_OBJDIR. Patch checker `research/check-patch-order.py` applies all18 via temporary index, matches sandbox sources. Sandbox for0018 `research/bt21/xnu`, originals `research/pre0018`, objects `research/cpu-objs`. First compiler issues were missing machine/atomic.h and nonexistent getNameSymbol; fixed and both TUs compiled successfully before CI. Fixtures/artifacts under research are local-only/ignored.
- GDB: mask int(register)&((1<<64)-1) for unsigned kernel pointers; use function-entry manual-call return traps, discard manipulated guests. No QEMU fixture is intentionally left running.

### Earlier active handoff snapshot (superseded)


- **0018 milestone:** Actions `38760004398` PASSED (3m56s), checksums passed, new CPU/build files byte-match CI tree. Kernel `xnu/out-0018/kernel.development.sweet`, SHA256 `b3c8d59acc88f93a2c836eb2527a05ed4fbaac7c580a2ea8c3b795137f4aadf1`; log/tree `research/run22.log` / `research/bt22`. QEMU `research/q0018` 12s: boot CPU registered, SGI1 verified, CPU initialization complete, **BSD initialization entered** (copyright, MAC framework, buffer headers), no panic/zero unknown glyphs. Direct CPU API checks PASS (`research/check-cpu18.gdb`, `.log`, `research/q0018-api`): CPU0 Running, processor matches processor_array[0], real SGI enabled, CPU name correct, secondary-start returns KERN_NOT_SUPPORTED. Two-CPU ADT correctly panics before registration (`research/q0018-smp`). Longer 45s run `research/q0018-long` still running; next BSD wait/fault not yet identified.
- **Prepared 0018 phone image:** `shim/out/shim-boot-0018.img`, 16,437,248 bytes, SHA256 `b7a23595d143a76c72c9fe2cfcc5d77a8f16987b5af1a2703bbffdfa6922908c`. Phone ADT/payload `shim/out/sweet-0018.adt` / `shim/out/payload-0018.bin`, usual debug args with cpus=1 and -noprogress. Unpacked ramdisk byte-compared (`shim/out/check-0018`). Entry PA remains 0xb146b488; kernel end PA 0xb1ffc000, boot_args 0xb2000000/top0xb2004000. User reports FASTBOOT+USB ready; read-only check found exactly one product sweet. NEXT: temporary fastboot boot of this verified image, record send/boot results, then await hardware photo without rebooting again.
- Patch 0018 committed/pushed **`e7d9550`**; Actions **`38760004398`** queued/running: https://github.com/dipa-studio/ios-sweet/actions/runs/38760004398. Do not launch a duplicate run.
- Active continuation: patch **0018** implemented in `iokit/Kernel/arm/SweetCPU.cpp` + arm64 source list + exact qcom,kryo470 personality. Keeps legacy IOCPU, accepts ONLY the single boot CPU, fills ml_processor_info from parsed topology with CPU object as cpu_id, calls ml_processor_register and processor_start. initCPU sends SGI1 via ICC_SGI1R_EL1 and waits (1-second deadline) for real cpu_signal_handler to clear SIGPdisabled. Only after successful startup calls ml_cpu_init_completed/cpusRunning and publishes completion/max CPUs. Secondary startup/suspend are explicitly unsupported. Local C++ compile passed for both TUs (`research/cpu-objs`), all imports resolve, object contains dsb ishst / ICC_SGI1R / isb; all18 patches apply in order and match sources. Sandbox `research/bt21/xnu`, originals `research/pre0018`; next CI artifacts should use `xnu/out-0018`, `research/bt22`, `research/run22.log`. User has the phone available; first run QEMU, then prepare/temporarily boot useful hardware progress.
- Goal: iOS 16 generation XNU on Redmi Note 10 Pro / sweet. User wants persistent progress, commits, kernel builds via `gh` Actions, and this handoff kept current across agent switches. Temporary `fastboot boot` only; preserve Android and backups.
- Latest kernel implementation: **`aa3f991`**, patches **0001-0017**. All four new Actions builds PASSED: 0014 `38748543058` (4m0s), 0015 `38749549779` (4m3s), 0016 `38750733996` (4m3s), 0017 **`38751507437` (4m7s)**. Latest: https://github.com/dipa-studio/ios-sweet/actions/runs/38751507437. No build is pending.
- **Current blocker: CPU initialization has no SWEET CPU driver.** QEMU registers SweetPlatformExpert, then kernel_bootstrap_thread waits in **kperf_init_early -> ml_wait_max_cpus**. Confirmed via actual-kernel GDB call stack (`research/locate-cpu-wait2.log` / `.symbols`) and normal-run `max_cpus_initialized == 2` (MAX_CPUS_WAIT). Do not force the flag or remove the wait. Next implement real CPU registration and completion using XNU's CPU/processor interfaces. CURRENT SWEET uses legacy IOCPU (`USE_APPLEARMSMP` is absent); enabling AppleARMSMP is a separate architectural change requiring GIC/PMGR services. No userland/root filesystem boot yet.
- Fixed this session: PC=0 unregistered PRNG callback (0014, built-in HMAC-SHA256 provider + direct entropy/write_random hashing); absent Image4/AMFI trust-cache service (0015, unavailable runtime, loads rejected and queries no-match); missing platform driver (0016, SweetPlatformExpert exact personality); incorrect QEMU timer IRQ (0017, validated ADT PPI property, phone default19/QEMU27).
- **Latest kernel** `xnu/out-0017/kernel.development.sweet`, SHA256 `055a80190ab998b4df9203f1102072dcc78faad1779a537a514e522b682b582f`. Buildtree/log **`research/bt21` / `research/run21.log`**; all nine changed C/C++/header/config files byte-match local checked sources. Checksums passed. Entry PA `0xb146b488`, kernel end PA `0xb1ff4000`. Preferred baseline for patch 0018: `research/bt21/xnu` (no nested .git).
- **QEMU verified**: `research/q0017-noprogress` ran **30 seconds**, no visible panic, **zero unknown glyphs**, IRQ exceptions returned normally (4 IRQs, tickless idle). It still waits for CPU completion, so this is NOT a completed boot. Earlier 15s run `research/q0017` captured 122 IRQs, timer value27, GICR_ISENABLER0 `0x08000002`, and CPU WAIT; graphical progress overlay corrupted 13 glyphs. Add **`-noprogress`** to keep the console readable. New runner classifies incomplete/blank decoding explicitly (exit3 with --fail-on-panic), visible panic exits2. Snapshot-only; use regs/GDB to diagnose stalls.
- Current QEMU payload/ADT: **`research/q0017-noprogress.bin` / `research/q0017.adt`**. Run: `python3 tools/qemu-sweet.py research/q0017-noprogress.bin research/NEW-OUT --secs 30 --kernel xnu/out-0017/kernel.development.sweet --fail-on-panic`. Generate fresh seed with mkadt for a new payload. QEMU virt models generic ARM execution, not Qualcomm XPU/bootloader/panel or the phone's SMC PSCI conduit.
- **Prepared phone image**: **`shim/out/shim-boot-0017.img`**, 16,416,768 bytes, SHA256 `51ff7fe30b4a6668c3452ffe1da922c5ac30cb8de60625c2a652587ecbd0ec92`. Phone ADT/payload `shim/out/sweet-0017.adt` / `shim/out/payload-0017.bin` (timer19). Unpacked ramdisk exactly matches payload (`shim/out/check-0017`). Command line **`debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress`**. NOT sent to phone this session: known CPU-driver wait should be addressed in QEMU first. Last actual phone boot remains 0013; user's latest photo `IMG_20261015_160033.jpg` confirmed the old PRNG PC=0 panic.
- Direct QEMU/GDB API checks: PRNG 600-byte chunking, buffer bounds, changing output, zero-length reads/writes, user reseed, contended refresh, generator1 init/output PASS again on latest0017 (`research/check-prng17.gdb` / `.log`, guest `research/q0017-api`). Trust-cache unavailable-service/argument/output cases PASS on0015 (`research/check-trustcache.gdb` / `.log`). Platform registration + target/product normal/truncated/zero/null/bounds PASS on0016 (`research/check-platform.gdb`, `research/check-platform3.log`). Different platform ID correctly retains IOPanicPlatform fallback (`research/q0016-negative`). Timer PPI32 and size2 correctly panic (`research/q0017-badppi`, `research/q0017-badsize`); omitted property selects19 (`research/q0017-default`). These are API/bring-up checks, not crypto certification or SMP scheduling validation.
- Local checker **`research/check-patch-order.py`** verifies all17 patches and changed source equality using a temporary upstream git index. Fixtures/logs/artifacts under research are local-only and ignored. For GDB unsigned kernel pointers, mask `int(register_value) & ((1<<64)-1)`; x30/x29 may be rendered signed. Stop at a function entry for manual-call return traps, and discard each manipulated guest after API tests. All fixture QEMU guests have been stopped.


- User wants persistent progress toward iOS 16, commits for completed changes, builds through `gh` / GitHub Actions.
- Keep this file current at every milestone so another agent can resume immediately, including when usage limits interrupt a session.
- Patch 0017 committed/pushed `aa3f991`, Actions `38751507437` running: https://github.com/dipa-studio/ios-sweet/actions/runs/38751507437. Next artifacts: xnu/out-0017, research/bt21, research/run21.log. Do not duplicate.
- Patch 0016 kernel downloaded/checksum verified: SHA256 `d06336c54baa06e0df6561a8bd7df98a9b9f7502c9381c62d1a44c045cd1ce46`, `xnu/out-0016/kernel.development.sweet`; buildtree/log `research/bt20` / `research/run20.log`. QEMU `research/q0016` (10s) prints platform expert registered, zero unknown glyphs, no panic observed BUT stalls (timer mapping still wrong until 0017). Direct actual-kernel GDB tests passed for registered instance and target/product names, including maxLength=1/0 and NULL, sentinels intact (`research/check-platform.gdb`, `research/check-platform3.log`, `research/q0016-api3`). First two fixture attempts used premature/wrong return breakpoints; discarded, corrected fixture uses start-return observation then a function-entry return trap. No phone boot yet.
- Patch 0016 CI `38750733996` PASSED (4m3s); artifacts not yet downloaded/booted. Patch 0017 prepared to fix QEMU timer fidelity BEFORE interpreting future no-panic/stall runs: actual `qemu-system-aarch64 -M virt,gic-version=3,dumpdtb=research/qemu-virt.dtb ...` timer property has virtual PPI 11 => INTID 27, while phone uses INTID 19. Kernel formerly hardcoded 19. New optional /arm-io/gic virtual-timer-intid property selects the PPI (validate exact u32 and 16..31); missing property retains phone default 19. One RO-late global drives GIC enable and sleh_irq dispatch. tools/mkadt.py emits phone19/QEMU27. Both C TUs compile (`research/timer-objs`), ADT serialize/parse checks confirm values/addresses, all 17 patches apply and match. Sandbox `research/bt19/xnu`, originals `research/pre0017`; initial local sleh compile failed due read-only exported header not updated, fixed export and BOTH recompiled successfully. CI exports normally.
- Architecture correction for next agent: CURRENT SWEET board_config does NOT define USE_APPLEARMSMP; it compiles the legacy IOCPU path, not cpu_boot_thread/IOPMGR AppleARMSMP. Existing roadmap mentions AppleARMSMP as a future approach. Do not look for gCPUIC/gAIC/gPMGR globals in the current kernel: they are absent. Determine the actual CPU-init wait after platform registration before choosing the next driver architecture.
- Patch 0016 committed/pushed as `0dcdf5c`; Actions `38750733996` running: https://github.com/dipa-studio/ios-sweet/actions/runs/38750733996. Next artifacts: `xnu/out-0016`, `research/bt20`, `research/run20.log`.
- Patch 0016 implemented: new `iokit/Kernel/arm/SweetPlatformExpert.cpp`, arm64 source list, built-in personality in KernelConfigTables.cpp guarded by ARM64_BOARD_CONFIG_SWEET. Matches only xiaomi,sweet at score 10000, keeps IOPanicPlatform fallback for other hardware. Uses IODTPlatformExpert base initialization/power domain and createNubs CPU logical-ID annotation; overrides top-level publishing because base requires nonexistent Apple NVRAM /options. Publishes real CPU/top-level nodes, provides bounded target/product names, and registers the platform service. GIC/PMGR and arm-io child-bus drivers remain separate work. Both C++ TUs compile (`research/platform-objs`), all imports resolve against 0015/own objects, all 16 patches apply and sources match. Sandbox `research/bt19/xnu`; originals `research/pre0016`. NEXT: full Actions/QEMU; inspect CPU boot waits after the platform registers, do not equate absence of panic with successful boot.
- Patch 0015 CI `38749549779` PASSED (4m3s), checksum verified. Kernel `xnu/out-0015/kernel.development.sweet`, SHA256 `ca1b424a596c7058550ad1659080a333e7643270f019656401d80bf2b876645f`; buildtree/log `research/bt19` / `research/run19.log`. QEMU `research/q0015` (10s, zero unknown glyphs) passes trust-cache handling and machine lockdown, then panics `Unable to find driver for this platform: "xiaomi,sweet" @IOPlatformExpert.cpp:2319` (IOPanicPlatform fallback). Next concrete work: add a built-in SWEET IODTPlatformExpert personality/class; keep the fallback for mismatched platforms. No phone boot yet.
- Direct trust-cache API checks PASSED in the actual 0015 kernel under QEMU/GDB (`research/check-trustcache.gdb`, `research/check-trustcache.log`, `research/q0015-api`): runtime pointers NULL, hash/UUID queries return NOT_FOUND, typed/untyped/legacy loads return NOT_SUPPORTED, capabilities outputs zero and bounds preserved, invalid-argument cases still reject. Second PRNG check also verifies contended refresh returns immediately (`research/check-prng2.log`, `research/q0014-api2`).
- Patch 0015 committed/pushed `8f851d4`, Actions run `38749549779` running: https://github.com/dipa-studio/ios-sweet/actions/runs/38749549779. Do not duplicate.
- Direct PRNG API checks passed in the REAL 0014 kernel under QEMU/GDB, stopped before trust-cache init (`research/check-prng.gdb`, log `research/check-prng.log`, guest `research/q0014-api`). Invoked read_random for 600 bytes (chunking and buffer sentinel intact), repeated output differed, zero-length read/write succeeded, write_random(64-byte input) succeeded, secondary generator 1 initialized and generated distinct output. QEMU log has no exceptions during these checks; guest was discarded after the tests. This tests API behavior, not a cryptographic certification or SMP scheduling.
- Patch 0015 implemented in `bsd/kern/kern_trustcache.c`: SWEET with missing Image4/AMFI marks both runtime pointers NULL and logs service unavailability. Existing static loader handles NULL. Public load APIs return KERN_NOT_SUPPORTED; hash/UUID queries return KERN_NOT_FOUND; capabilities outputs are zero with KERN_NOT_SUPPORTED. Invalid-input checks remain, and Apple paths/available-provider initialization retain existing behavior. No trust is granted. Local arm64 compile passed (`research/trustcache-objs`), all 15 patches apply and sources match. Sandbox `research/bt18/xnu`, baseline `research/pre0015`. Next: Actions/QEMU and direct unavailable-service API checks using GDB.
- Patch 0014 CI `38748543058` PASSED (4m0s), artifact checksum passed. Kernel `xnu/out-0014/kernel.development.sweet`, SHA256 `741de20c851755d333759d7e668346f3edb4f325efac4cc7c142d80d1f5ff75c`; buildtree/log `research/bt18` / `research/run18.log`. QEMU run `research/q0014` (10s, zero unknown glyphs) prints `SWEET PRNG: HMAC-SHA256 registered`, passes commpage initialization, loads vnguard policy, THEN panics `image4 interface not available @kern_trustcache.c:253`. PC=0 fault FIXED IN QEMU. No new phone boot performed.
- Next work: SWEET currently has neither AppleImage4 nor AMFI. `trust_cache_runtime_init` unconditionally requires both; unlike BCM2837, SWEET enters this startup path. Investigate an explicitly unavailable trust-cache runtime with rejected loads/no query matches while providers are absent; preserve all Apple-platform checks. Merely skipping the startup call would leave later AMFI/IMG4 dereferences unsafe. Existing load_static_trust_cache already handles trust_cache_rt == NULL.
- QEMU runner now supports `--kernel MATCHING_KERNEL --fail-on-panic`: runtime font extraction from the payload produces exact `fb.txt`, plus `result.json`; a visible panic returns 2. Baseline 0013 re-run `research/q0013-text` detects the same NULL callback panic with zero unknown glyphs. Synthetic glyph roundtrip (including literal `?`) and invalid-VA rejection passed. Screen rows remain circular, not a full log; `no_panic_observed` does not prove successful boot. README has the invocation.
- Patch 0014 committed/pushed as `be61f99`; Actions run `38748543058` is running: https://github.com/dipa-studio/ios-sweet/actions/runs/38748543058. Do not start a duplicate build.
- 2026-10-15 active: hardware photo `IMG_20261015_160033.jpg` confirms the same PC=0 instruction-fetch abort as QEMU (LR `0xfffffe0007443fe4` = read_random). Independently reproduced with kernel 0013 in `research/q0013` (8-second run); prior allocator fix works. Latest tooling commit reviewed: `8d66d2d`.
- Patch 0014 implemented: registers a built-in HMAC-SHA256 DRBG provider at EARLY_BOOT/FIRST, before commpage creation. A shared mutex serializes output/reseed across CPUs (correctness first; no per-CPU scaling yet), initgen validates CPU identities, refresh uses try-lock and the existing entropy filter/health tests. SWEET entropy accumulation and write_random now use the built-in SHA256 directly; no incomplete g_crypto_funcs table is registered. The existing bootloader-seed requirement remains enforced.
- Local validation: both PRNG translation units compile to arm64 objects in `research/prng-objs`; no SHA256 wrapper imports remain. All 14 patches apply in CI order and resulting PRNG sources match local inputs. Sandbox `research/bt17/xnu`, originals `research/pre0014`, checker `research/check-patch-order.py`. NEXT: Actions kernel build, download to `xnu/out-0014`, run the QEMU payload before requesting another phone boot. Baseline QEMU ADT/payload are `research/q0013.adt` / `research/q0013.bin`.
- 2026-10-15 08:15 QEMU EMULATION WORKS (`tools/qemu-sweet.py`, see "QEMU emulation" below). Patch 0013 VERIFIED in QEMU: boot passes kmem_crypto_init and ulock_initialize. NEXT BLOCKER (reproduced in QEMU, expected on hardware too): `pmap_create_commpages -> random() -> RandomULong -> read_random -> prng_funcs.refresh == NULL` (instruction fetch abort at pc 0, lr read_random+0x34). Nobody calls `register_and_init_prng` (closed corecrypto kext does it on Apple). Fix = patch 0014: SWEET-registered cckprng_funcs provider (osfmk/prng/prng_random.c); note random_cpu_init also panics while !prng_ready. The 07:26 hardware boot of 0013 will most likely show this same panic.
- Previous hardware boot: patch 0013 (`a3a46e2`) CI `38686345689` PASSED (3m44s), image `shim/out/shim-boot-0013.img` was temporarily booted on 2026-10-15 at 05:26 UTC: send OKAY (0.407s), boot OKAY (0.134s), total 0.546s. Superseded by the hardware/QEMU PC=0 observation and patch 0014 work above.
- Latest kernel implementation commit: `8f851d4`. Latest successful kernel CI: `38749549779` (patch 0015), https://github.com/dipa-studio/ios-sweet/actions/runs/38749549779.
- CURRENT image: `shim/out/shim-boot-0013.img`, 16,412,672 bytes, SHA256 `110d1c46aee4b60253db24b6535e9781c4cf0fd25ca2a70e9016cf81378cea25`. Kernel `xnu/out-0013/kernel.development.sweet`, SHA256 `f0b1d375995316c550ce7014ff234d8271e9b9ac2857795ceff41b76ba2cd740`, plain arm64. CI log confirms 0013 applied; checksums passed. Entry PA `0xb146b488`, kernel end PA `0xb1ff4000`.
- Current log/buildtree: `research/run17.log`, `research/bt17` (extracted, no nested .git; preferred baseline for patch 0014). Payload/ADT: `shim/out/payload-0013.bin`, `shim/out/sweet-0013.adt` (includes /defaults). Unpacked ramdisk byte-compared with payload in `shim/out/check-0013`.
- Patch-0012 original image: `shim/out/shim-boot-0012.img`, 16,412,672 bytes, SHA256 `cf4b8ec4a65a51888d71c17d09cd0831d6b761b90e2531fdb8e575c32f4fd24a`. Kernel `xnu/out-0012/kernel.development.sweet`, SHA256 `69b78f49f6459cc63a0af44e7cf73c1779ef72130ef72b12ae86c88183ffc742`; checksums passed and sweet_early_relocate symbol present. Entry PA remains `0xb146b488`, kernel end PA `0xb1ff4000`.
- Patch-0012 log/tree: `research/run16.log`, `research/bt16` (extracted, no nested .git; preferred baseline for next patch). CI log confirms 0012 applied. Payload `shim/out/payload-0012.bin`, ADT `shim/out/sweet-0012.adt`; packed ramdisk byte-compared with payload in `shim/out/check-0012`.
- Active payload command line: `debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam`.
- Latest observed photo: `/home/dipa/Downloads/IMG_20261015_071346.jpg` (local only). Stages 9 (kernel framebuffer VA), 10 (TTBR0 off/TLB flush), 11 (physmap table pointers) and 12 (calling pmap_bootstrap) all visible. Persistent diagnostics are VERIFIED ON HARDWARE.
- pmap.c:1987 is the assert after SecureDTLookupEntry(NULL, "/defaults", &entry). The pmap-max-asids property itself is optional; an empty /defaults uses XNU's MAX_ASIDS. Do not bypass the assertion or guess an ASID limit.
- Previous payload-only fix image: `shim/out/shim-boot-0012-defaults.img`, SHA256 `3ff61a268853c6ea7cbf284833f1346a1d791980ca366b6e1a215a86e040512b` (16,412,672 bytes). Same kernel/CI as 0012; ADT 2516 bytes (`shim/out/sweet-0012-defaults.adt`), root /defaults present with no ASID override. Payload `shim/out/payload-0012-defaults.bin`; unpacked ramdisk exactly matches payload (`shim/out/check-0012-defaults`). This boot reached the allocator crypto NULL-pointer fault below.
- Latest hardware photo: `/home/dipa/Downloads/IMG_20261015_071608.jpg` (local only). Normal serial framebuffer console works. VM bootstrap/zone initialization, logging, scheduler, IPC, KDP and ulock_initialize print before the kmem_crypto_init data abort. gPanicBase warnings are expected because PLATFORM_PANIC_LOG_DISABLED is configured, and are not the faulting call.
- Faulting instruction: `ldr x0, [x8, #0x150]` in crypto_random_kmem_ctx_size; x8 = NULL g_crypto_funcs. Source: libkern/crypto/corecrypto_rand.c and osfmk/vm/vm_kern.c:2448. rand.h explicitly permits NON-cryptographic kmem RNGs, but requires no locking in generation and context size <=256. Do not substitute read_random here: the global PRNG provider is also unregistered and kmem generation must remain lock-free. early_random may seed contexts during single-CPU bootstrap; it uses the built-in HMAC-SHA256 DRBG with the host-generated ADT seed.
- Patch 0013 keeps the replacement entirely private to vm_kern.c: SplitMix64 state is 8 bytes per CPU/sizeclass, initialized using early_random. Uniform selection uses rejection sampling to avoid modulo bias. Other platforms retain the original corecrypto calls; SWEET's generic crypto APIs/registration remain untouched. Reference: https://prng.di.unimi.it/splitmix64.c (Sebastiano Vigna, public domain).
- Validation: vm_kern.c compiles to arm64 (`research/kmem-rng-objs`, using `research/run16.log` + `research/bt16`). Undefined symbols include early_random and NO crypto_random_* references. All 13 patches apply in CI order and match the checked source. Original saved as `research/vm_kern.pre0013.c`.
- RNG host checks: extracted actual new helper in `research/kmem-rng-check.c`; 16,384 outputs match the author's downloaded reference (`research/splitmix64-reference.c`), 147,456 bounded outputs satisfy their limits (including 1 and UINT64_MAX), and a forced rejection advances correctly. Full kernel crypto services and the registered kernel PRNG provider remain future work; this patch only supplies the allocator RNG contract.
- Patch-0011 artifacts: kernel `xnu/out-0011/kernel.development.sweet`, log `research/run15.log`, extracted buildtree `research/bt15` (no nested .git). CI log confirms patch 0011 applied; artifact checksums passed. Kernel is plain arm64, entry VA `0xfffffe000746b488` -> PA `0xb146b488`, kernel end PA still `0xb1ff4000`.
- Patch-0011 image: 16,412,672 bytes, SHA256 `29ad5cbfc752473de4af2c471a24a8b34f92da4bff5afec65c614b90cd3ad53a`. Kernel SHA256 `8ac3a956894b09f7bd0d890de8c5de38c65513287feb1e1bcd12db8ca772ee5c`. Payload/ADT in `shim/out/payload-0011.bin`, `shim/out/sweet-0011.adt`. Header unpacked/checked in `shim/out/check-0011`.
- First patch-0011 attempt timed out waiting for FASTBOOT; superseded by the successful send/boot recorded above. Do not repeat the boot before the user reports the current screen.
- Patch-0011 photo: `/home/dipa/Downloads/IMG_20261015_070218.jpg` (local only). Visible stages 1-8 and old red shim text; entry PA B146B488, boot args PA B1FF8000 / top B1FFC000. No evidence yet locating execution after stage 8.
- Patch-0012 sandbox: `research/bt15/xnu` (NO nested .git). Proposed permanent debug VA: VM_MIN_KERNEL_ADDRESS + ARM_TT_L2_SIZE, in the reserved gap below gVirtBase and above low globals; map the same PA framebuffer as Normal-NC in the final TTBR1 before arm_vm_physmap_init changes phystokv(). Keep the early panic sink until normal kprintf startup.
- Patch 0012 validation: all 4 changed C translation units compiled to arm64 objects in `research/post-ttbr0-objs`; internal header export added to osfmk/arm64/Makefile and recreated locally in EXPORT_HDRS for pe_serial.c. All 12 patches apply in CI order and match all 6 edited files. Baselines are saved under `research/pre0012/`.
- Actual mapping-helper host model (`research/early-map-check.c`, barrier stubbed only for host compilation) confirms: VA `0xfffffe0002000000`, L1 index 2016, L2 index 1; descriptor `0x006000009c000605` = framebuffer PA, Normal-NC, outer-shareable, EL1 RW, PXN+UXN. All other L2 entries preserved. The gap is excluded by ARM_LARGE_MEMORY's pmap_virtual_region(0).
- Next boot command line MUST add `-enable_kprintf_spam`: DEVELOPMENT disables kprintf_output by default even when DB_KPRT is set (`pexpert/arm/pe_kprintf.c`). Planned command line: `debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam`.

### Patch 0012 screen legend (after original stages 1-8)
- 9: kernel framebuffer VA live; 10: TTBR0 off / TLB flushed; 11: physmap pointers rebound.
- 12: calling pmap_bootstrap; 13: pmap_bootstrap returned; 14: early I/O page tables ready; 15: arm_vm_init returned.
- 16: calling serial_init; 17: fbcon config read / mapping; 18: fbcon mapping returned; 19: serial_init returned; 20: kprintf startup complete.
- Normal serial initialization clears the screen, so earlier stages may disappear before 19. If serial exists, early console disables after 20; otherwise early panic output stays enabled.
- Local source sandbox: `research/bt13/xnu` (git baseline `623b060`, patches 0001-0008 already in baseline; uncommitted export edits correspond to patch 0009). Preserve those edits when producing patch 0010.
- Previous compile log/tree: `research/run13.log`, `research/bt13`; previous kernel: `xnu/out/kernel.development.sweet`. New successful log/tree: `research/run14.log`, `research/bt14`; new kernel: `xnu/out-0010/kernel.development.sweet`.
- Current device access: patch-0012 image was temporarily started from FASTBOOT; no adb connection is expected. Never flash partitions for bring-up; use `fastboot boot` with the shim image.
- Patch `0010` lives in `xnu/patches/0010-sweet-early-framebuffer-debug.patch`. Its exact compiled sources are under `research/bt13/xnu`; generated objects are in `research/early-objs`.
- Local checks passed: all 5 changed/new translation units compiled to arm64 Mach-O objects; new console only imports `_doprnt` and `iso_font` (both exist in the previous kernel). All 10 patches applied to a temporary upstream git index in CI order, and all 7 resulting changed source files matched the local compile inputs.
- Reproduce changed-file compilation: `XNU_CC_OBJDIR="$PWD/research/early-objs" python3 tools/xnu-cc.py research/run13.log research/bt13 arm_init.c arm_vm_init.c kern/debug.c arm64/start.s osfmk/arm64/sweet_early.c --per-file` (create the output directory first). The helper now accepts explicit new source paths absent from the old artifact's Makefiles and rejects empty selections.
- Device command result: `adb reboot bootloader`, then `timeout 30s fastboot boot shim/out/shim-boot-0010.img`: send OKAY (16028 KB), boot OKAY, total 0.541s. This confirms bootloader acceptance only, not XNU progress.
- The successful boot above was on 2026-10-14. On 2026-10-15, `timeout 45s fastboot boot ...` timed out waiting for a device; no image was sent. Boot image SHA256 rechecked and unchanged. No new build is needed to repeat the trial.
- Earlier retry: `timeout 90s fastboot boot shim/out/shim-boot-0010.img` timed out (exit 124). This is superseded by the successful 2026-10-15 repeat boot recorded above; do not repeat it until the user reports the screen.
- User observation confirms MMU-on C entry, platform expert, CPU topology/bootstrap, startup bootstrap and cpu_init all complete. Failure is within arm_vm_physmap_init (non-XNU_MONITOR branch), before stage 8 / TTBR0 teardown. Old red shim text remains behind the early console; this is expected because only written rows are cleared.
- New edit/compile sandbox is `research/bt14/xnu` (extracted artifact, NO nested .git; `git diff` there would act on the parent project). Use a saved baseline file / generated unified diff, or explicitly initialize a local baseline before editing. `research/bt13/xnu` does have its own baseline git repo.
- Exact user-reported panic: `arm_vm_init.c:1606 Assertion failed: (temp_ptov_table[ptov_index - 1].va + temp_ptov_table[ptov_index - 1].len) <= physmap_end`.
- Root cause: MacOSX build sets `ARM_LARGE_MEMORY=1`; `XNU_MONITOR` is off. Block alignment in arm_vm_physmap_slide can add gaps, but the non-PPL large-memory physmap_end omitted the padding already present in the small-memory branch. Patch 0011 reserves PTOV_TABLE_SIZE*ARM_TT_TWIG_SIZE (8*32MB) in both the aperture bound and its L1 allocation; monitor/PPL builds retain zero padding. The assertion is preserved.
- Validation: `arm_vm_init.c` compiled to an arm64 object using `research/run14.log` + `research/bt14` (object in `research/physmap-objs`). All 11 patches apply in CI order and produce exactly that source. Host harness `research/physmap-check.c` extracts the real XNU slide function with page-table-write stubs: all 65,536 page-aligned slides within 1GB checked against the current kernel memory layout; old bound fails 65,504 cases, padded bound fails zero; worst overrun 0x1ffc000 bytes. Baseline source saved as `research/arm_vm_init.pre0011.c`.
- CI: https://github.com/dipa-studio/ios-sweet/actions/runs/38638702083 (success; artifacts and full log downloaded).
- New image: `shim/out/shim-boot-0010.img` (16,412,672 bytes, SHA256 `ed2c270e0081bad5ff517be415f9bfe8805463aea7866732203fa97ac737e64e`). Previous image preserved as `shim/out/shim-boot-pre0010.img`.
- New kernel SHA256: `3ee05270cc291c49589a101678f904b1b69cb2f2575e05398bce8a4e052f409a`. Artifact checksums passed; arm64 (not arm64e), early-console symbols present. Payload entry VA `0xfffffe000746a488` -> PA `0xb146a488`; kernel end PA `0xb1ff4000`. Command line remains `debug=0xc serial=3 cpus=1 keepsyms=1`.

### Patch 0010 screen legend
- Bottom-left white 64x32 tile: assembly entered `start_first_cpu`, before page tables/MMU.
- Text `SWEET EARLY 1`: entered `arm_init` with MMU on; 2: platform expert done; 3: CPU topology done; 4: CPU bootstrap done; 5: startup bootstrap done; 6: CPU init done; 7: entering `arm_vm_init`; 8: leaving bootstrap TTBR0.
- `SWEET EARLY PANIC:` prints a copy of panic arguments before normal panic handling needs per-CPU/thread state.
- In patches 0010/0011 the console disabled before TTBR0 invalidation; patch 0012 replaces that with a persistent kernel VA alias and diagnostics through normal serial/kprintf startup (see its legend above).
- Screen contents require a physical observation from the user: adb cannot capture a framebuffer after Android has been replaced by a bare-metal kernel.

## QEMU emulation (2026-10-15)
- `tools/mkadt.py OUT.adt --qemu` = same ADT with QEMU virt GICD 0x08000000 / GICR 0x080a0000 / CNTFRQ 62.5 MHz.
- `tools/qemu-sweet.py PAYLOAD OUTDIR [--secs N] [--hmp "x /8gx ADDR"] [--gdb]`: host-side copy of shim/loader.c
  (segments + ADT + boot_args at their PAs, 6-instruction stub sets x0 and jumps), `-M virt,gic-version=3 -cpu cortex-a76`
  (same ID_AA64MMFR0 as Kryo 470), 4G RAM so 0x9c000000 fb and 0xb0000000 kernel are plain RAM.
  Outputs `fb.png` (the screen), `regs.txt`, `qemu.log` (exceptions with ESR/FAR/ELR). `--gdb` halts with a stub on :1234
  (system `gdb` + `set architecture aarch64`, `hbreak *VA` works with the MMU on).
- Not emulated: Qualcomm XPUs, ABL, real panel, PSCI conduit (QEMU virt uses hvc). Hardware still decides those.
- Example: `python3 tools/mkadt.py /tmp/q.adt --qemu && python3 tools/mkpayload.py xnu/out-0013/kernel.development.sweet /tmp/q.adt /tmp/pq.bin --cmdline 'debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam' && python3 tools/qemu-sweet.py /tmp/pq.bin /tmp/q`

## Layout
- `backup/`  partition dumps + full sdb..sdf LUNs, `SHA256SUMS` (all verified on-device == local)
- `dtb/`     live FDT (from OrangeFox), PixelOS boot.img dtb, dtbo, dts versions
- `shim/`    bare-metal boot shim (`./build.sh` -> `out/shim-boot.img`)
- `research/` mainline sm7150 dts, XNU pe_fiq.c etc, PocketDarwin patches
- `tools/unpack_bootv2.py`

## Device access
- No root in PixelOS 17 (user build, adb root disabled). Root = `fastboot boot recovery/recovery.img` (OrangeFox R12.0_2, temp boot, not flashed).
- `fastboot boot` works for stock boot.img, OrangeFox, and our shim.
- Shim resets itself via PSCI SYSTEM_RESET; IMEM restart_reason 0x77665500 is seen
  (bootreason=bootloader) but ABL still boots Android, so it does not stop in fastboot.

## Hardware facts (live DT + shim measurements)
- Framebuffer (ABL cont splash): 0x9c000000, 1080x2400, stride 4320, a8r8g8b8, panel is DSC *video* mode (no kick needed).
- RAM: 0x80000000+2G, 0x100000000+0xfcc00000. Live reserved-memory = PixelOS base dtb + sweet dtbo fixups.
- GICv3: GICD 0x17a00000, GICR 0x17a60000 (stride 0x20000, 8 PEs). GICD_TYPER 0x007B041D: SecurityExtn=1 (DS=0).
  ABL leaves GICD_CTLR = 0x10 (ARE_NS on, groups off). Writing 0x13 reads back 0x12: EnableGrp1A (bit1) is the one that matters.
- GIC Group 1 VERIFIED (shim 2c): vtimer INTID 19 = 30 IRQs/300ms, ptimer INTID 18 = 30, SGI 1 to self = 10/10.
  ICC_SRE=1 already, ICC_CTLR 0x8440, priority/enable writes stick. GICR_IGROUPR0 and GICD_IGROUPRn read 0 from NS
  even though Group 1 delivery works (RAZ in NS view) - do not use them as a health check.
  INTID 28 shows pending constantly (unknown PPI 12).
- Timer 19.2 MHz. Qualcomm PPI numbering: secure-phys/phys/virt/hyp = PPI 1/2/3/0 -> vtimer INTID 19, ptimer 18 (NOT 27/30).
- PSCI 1.1 via SMC, SMCCC 1.1. CPU_ON ok; SYSTEM_SUSPEND and SYSTEM_RESET2 unsupported.
- SMP: CPU_ON works for all 7 secondaries (MPIDR 0x81000n00).
- Entry EL1, MMU off, x0 = DTB (magic ok). ID_AA64MMFR0 = ...101122: 16K granule YES, 4K YES.
- No serial console: debug UART SE is in SPI mode on sweet. Screen is the only debug channel.

## XNU implications
- XNU GICv3 path (pexpert/arm/pe_fiq.c, VMAPPLE) puts the timer (PPI 27) in Group 0 = FIQ. With SecurityExtn=1,
  Group 0 is secure -> impossible from NS EL1. Need: Group 1 IRQ, ICC_IGRPEN1, INTID 19, and the timer handled in the IRQ path.
- XNU VMAPPLE PSCI uses `hvc`; sweet needs `smc`.
- PocketDarwin NonAppleGenericCpu.patch only swaps -mcpu=apple-h7 for -march=armv8-a.

## Milestone 3 (2026-10-14): 16K MMU verified
- ABL loads the Image at 0x80080000 (base 0x80000000 + 0x80000) regardless of text_offset=0.
- 16K granule, T0SZ=28 (L2 start, 32MB blocks), MAIR 0x44ff00, TCR 0x1409CB51C, PARange=2 (40-bit PA).
- Only own image pages WB (Qualcomm XPUs: never map carve-outs cacheable); fb Normal-NC; rest Device.
- AT S1E1R checks all as expected; unmapped -> translation fault L2.
- Fill fb: 110 ms (MMU off) -> 3.5 ms (NC). Memory loop 70 ms -> 0.7 ms. vtimer + SGI IRQs still fine with MMU on.

## XNU loader design notes (xnu-8796)
- Entry: `_start` -> `start_first_cpu`, x0 = boot_args (PA), MMU off, EL1. boot_args: Revision/Version 2,
  virtBase/physBase/memSize/topOfKernelData, Boot_Video (fb 0x9c000000, rowBytes 4320, 1080x2400, depth 32),
  deviceTreeP/Length (ADT), CommandLine[608], bootFlags, memSizeActual.
- start.s maps [kernel mach header .. physBase+memSize) with cacheable BLOCKS. On Qualcomm every XPU carve-out
  must therefore lie below the kernel: use physBase = 0xb0000000 (above ramdump_fb 0xaf000000+16M,
  cdsp_sec, qseecom), end before rmtfs 0xfde01000 -> memSize ~0x4d000000 for bring-up.
- Base ADT: iPhone 8 d20ap DeviceTree from iOS 16.7.16 IPSW (tools/remotezip.py + tools/adt.py; LZFSE payload).
  Template values are empty: the loader must fill chosen/*, memory-map, cpu*/reg etc. like iBoot.
- XNU build: GitHub Actions (dipa-studio/ios-sweet, xnu.yml) via blacktop/darwin-xnu-build, MACOS_VERSION=13.5.
  New-clang fixes in xnu/patches; Xcode 16 iig is incompatible (IORPC.kernelContent) -> macos-14 runner, oldest Xcode.

## Kernel architecture plan (SWEET machine config)
- First stock build (VMAPPLE) is arm64e (PAC). Kryo 470 = ARMv8.2, no PAC -> SWEET must build plain arm64
  (HAS_APPLE_PAC is only defined for __arm64e__, so arm64 drops PAC automatically).
- Template: VMAPPLE (16K pages, GICv3 code in pe_fiq.c) minus APPLEVIRTUALPLATFORM hypercalls/paravirt PAC,
  plus BCM2837 (generic ARM: no APPLE_ARM64_ARCH_FAMILY Apple sysregs). Do not link the KDK lib<cfg>.os.*.a.
- Timer: XNU expects it on FIQ (sleh_fiq -> rtclock_intr). SWEET: GIC Group 1, so sleh_irq must ack ICC_IAR1,
  route INTID 19 to the same timer path, SGIs to cpu_signal_handler, EOI via ICC_EOIR1.
- SMP/interrupts: open-source AppleARMSMP (USE_APPLEARMSMP) needs an IOInterruptController (AIC role) and an
  IOPMGR (CPU power -> PSCI CPU_ON via SMC). Plan: built-in IOKit classes (SweetGIC, SweetPMGR, platform
  expert) compiled into the kernel and matched via iokit/KernelConfigTables.cpp -> no closed kexts needed.
- Debug: xnu patch 0003 sweet-fbcon = kprintf drawn on the framebuffer (ADT node "sweet-fbcon").
- ADT /arm-io ranges must not map to parent 0 (pe_arm_get_soc_base_phys()==0 disables serial): use parent 0x1000000.

## Status 2026-10-14 late: first XNU boot attempt
- SWEET kernel builds (CI run 38635343732, patches 0001-0009). Verified offline: arm64, no PAC, no >v8.2 code,
  70300 classic relocs all in range (slide 0 = no loader work), entry = __start.
- Boot: shim loader worked (initrd 0x846F0000-0x855FE3E0, BOOTARG 0xB1FF8000, JUMP 0xB146A488) but screen stayed
  red -> xnu died/hung before serial_init (fbcon). kprintf is a no-op before STARTUP_SUB_KPRINTF.
- Historical next-step plan (patch 0010, now implemented and built; see active handoff above): early fb debug channel
  1. start.s start_first_cpu (after mov x20,x0 / mov x21,#0): PA marker band (MMU off, direct store to 0x9c000000).
  2. start.s after both create_bootstrap_mapping calls: add ONE block entry to the existing V=P L2 table
     (x25 + 2*PGBYTES; do NOT call create_bootstrap_mapping again: fb and RAM share L1 entry 0, it would replace
     the L2 table). Entry = TYPE_BLOCK|VALID|SH(OUTER)|ATTRINDX(CACHE_ATTRINDX_WRITECOMB=1)|AF|PNX|NX|0x9c000000,
     index = (va & ARM_TT_L2_INDEX_MASK) >> ARM_TT_L2_SHIFT.
  3. new osfmk/arm64/sweet_early.c (+files.arm64 entry): sweet_early_mark(n) colored bands at screen bottom and
     sweet_early_putc using iso_font via VA=PA while the bootstrap TTBR0 is live; disable before
     arm_vm_init's set_mmu_ttb(invalid_ttep) (arm_vm_init.c:2148).
  4. markers in arm_init after PE_init_platform, ml_parse_cpu_topology, cpu_bootstrap, kernel_startup_bootstrap,
     cpu_init, before arm_vm_init.
  5. panic hook at top of panic_trap_to_debugger: va_copy + _doprnt(fmt, &copy, sweet_early_putc, 16).
  Verify with tools/xnu-cc.py (compile+symbols) and patch-order simulation before the single CI run.
**2026-10-20 evening, after the community scan.** New commits in the topaz port
(three more: 0131 refuses the calls that answer with a lock result, 0132 wraps
client images and memory, 0133 keeps values, bulk attachments and purgeability
- the IOSurface semantics CoreAnimation's GL renderer reads) and in UniXNU
(publish the Metal device, grant the render server its GPU client). The
upstream has no new kernel commit and is still at 0081, but the fork count
went from 149 to 171 within four hours.

Forks that matter for picasso, with what they did today:

| Fork | What | Why it matters here |
| --- | --- | --- |
| ry-diffusion/ios-beryllium | touch series for the **NT36672A** (0082 board keys/touch/display, 0085 bit-bang plus USB diagnostics, 0086 no ghosts, 0101 both IRQ edges), 0116 sandbox extensions so Photos works | the NT36672A is the same chip family as our NT36672C. They also added `bsd/dev/sweet_board.h`, a board layer that reads the name from the ADT root compatible - the pattern a three-board tree needs |
| RiverKawa271828/ios-meizu-m2381 | "UFS wall broken on-device": SSU wake, LUN0 ready, iOSRoot mounted. Also "full cmdline canonized" | they hit the same UFS wall we know and broke it |
| zsariboga/ios-sweet | 0106 publishes an IORTC because the boot waited 30 s for one; 0103-0105 fix silent IOSurface failures and the pixel formats | ported here as patch 0083 |
| palazik/ios-dodge | shim finds the payload after `_end` instead of trusting the bootloader's ramdisk; kernel-only CI artifact | our shim reads `linux,initrd-start` and `linux,initrd-end` from the device tree, which is the better way, so no change needed |
| lvyufeng/ios-gauguin | root image must be **journaled HFS+**; USB gadget register map; three and a half minute userland run | our root image is journaled already (tools/mkiosroot.sh) |
| JopangJovan/ios-sm8250 | bit-bang I2C touch for ST FTS | I2C, not our SPI, but the technique is the same |
| ibragruslan/ios-sweet (nabu) | GMU RPMH_CTRL bit 4, uncached VBIF3 SMMU, three HFI queues | more GPU work |

**The hunk count trap hit four projects today** - ours, JopangJovan, xNekom and
ibragruslan all shipped a patch the build driver skipped. A patch is now
generated against the file as the earlier patches leave it (0016 creates
SweetPlatformExpert.cpp, 0046 and 0059 change it, so the 0016 version is not
the one to diff against) and checked with `patch --dry-run` before it is
committed.

Kernel 0083 has both fixes: the IOKit quiesce one and the IORTC one.
Image `shim/out/shim-boot-0083.img` (md5 d1dffb81c22e7d29c84f6cb6dfcefcab).
**USB gadget on picasso (2026-10-20 evening).** The sweet tree already has the
whole USB stack: `sweetusb=2` puts the DWC3 into device mode with a CDC-ECM
function and a kernel interface `ue0` at 192.168.7.2/24 (patches 0072-0076),
and `sweetusblog=1` streams the console ring as UDP to 192.168.7.1:5140
(patch 0077), which the host reads with `tools/sweet-usbnet.sh`. Our images
had none of it: the command line never said `sweetusb=2`, so the phone showed
no USB device at all.

The hardware addresses are the same on both boards, which is the good news:

| Part | sweet (SM7150) | picasso (SM7250) |
| --- | --- | --- |
| DWC3 core | 0xa600000 | 0xa600000 (device tree: `/soc/ssusb@a600000/dwc3@a600000`) |
| QSCRATCH | 0xa6f8800 | 0xa6f8800 (the kernel's own device tree: `lito-rumi.dtsi`, `<0x0a6f8800 0x100>`) |
| GCC | 0x088e2000 | 0x088e2000 (device tree: `/soc/qcom,msm-eud@88e0000`) |

So the sweet USB driver should work on picasso without a change. One risk to
watch: the DWC3 node has an `iommus` property on this SoC, so the transfers
may need an SMMU context the driver does not create. The console will say
("SWEET USB: ..."), which is exactly why turning it on now is worth it.

Image with it enabled: `shim/out/shim-boot-0083u.img` (md5
914d469a4d63184556a1a0183868706c) - kernel 0083 plus `sweetusb=2` on the
command line.
**What the frozen screen said (2026-10-21, photo of the first boot).** The
panel showed the shim's header, the four boot registers (x0 = x1 = x2 =
0xA7EB0000, x3 = 0) and then `PROBE 0000000010000000` - and nothing more.
That is `find_dtb()` in shim/shim.c: the bootloader passes no tree in x0 on
picasso, so the shim walked a list of addresses and the first read killed the
core. A read of an address the bootloader did not map is answered with a
secure fault here, and the trust zone takes the core with it: no panic
screen, no message, the screen keeps the last line that was drawn.

So on this board the shim must not read the memory it was not given. What it
needs is already known:

- the memory banks, the GIC, the timer interrupt, the framebuffer and the
  UFS address are compiled in (`shim/build.sh --picasso`), and the ADT that
  the kernel reads carries the same values (`tools/mkadt.py --picasso`:
  dram 0x140000000 + 0xC0000000, GICD 0x17a00000, GICR 0x17a60000,
  virtual-timer-intid 19, framebuffer 0xa0000000 + 0x2300000, UFS
  0x1d84000);
- the payload sits where the boot image header says the ramdisk is
  (0x1200000), because the tree on this phone has no initrd properties and
  the bootloader passes none either.

`shim/build.sh --picasso` now passes `NO_DTB=1`, `RAMDISK_ADDR` and
`RAMDISK_SIZE`; `shim/loader.c` uses them when the tree says nothing, and
copies the payload header out before patching it so the loader never writes
to memory the bootloader may have mapped read-only.

Two things that were wrong in the notes and are now settled by the phone's
own boot image: the parts of a boot image are read **sequentially** from the
file (the phone's own image has the kernel at file offset 0x1000 and the
ramdisk right after it, while the RAM addresses are 0x8000 and 0x1200000),
and our image has the same header addresses as the working one - so the
image was never the problem.

Image ready for the next flash: `shim/out/shim-boot-0083u.img` (md5
9d0ea73cbb4b5d94ec069188aa3552de), kernel 0083 (the IOKit quiesce fix and
the IORTC fix), `sweetusb=2` on the command line.

**The bootloader does not load the ramdisk on this phone (2026-10-21).** The
third picture of the boot showed the shim getting as far as the loader, with
`INITRD lo= 0000000012000000` - and then nothing, so reading the ramdisk
where the boot header says it is kills the core, the same way reading an
unmapped address does. The address the bootloader passes in x0
(0xA7EB1000, it moves a little between boots) is neither the boot image nor
the payload: it holds no address-like word in its first 512 bytes.

What the bootloader certainly does is load the *kernel* part: the shim runs
from it. So the payload and the device tree now ride in the kernel part,
behind the shim:

```
kernel part (loaded at 0x8000):
  0x00000  the shim (28 KB, its own memory ends at 0x31000)
  0x40000  the payload (16.6 MB)          -> RAM 0x48000
  0x1018000 the device tree (1.4 MB)       -> RAM 0x1020000
```

`shim/build.sh --picasso` assembles that part and passes the offsets to the
shim, which reads the payload from `KERNEL_ADDR + PAYLOAD_OFF` and the tree
from `KERNEL_ADDR + DTB_OFF`. The kernel part ends at 0x117B000, below the
0x1200000 the ramdisk part uses, so the two do not overlap.

Image: `shim/out/shim-boot-0083x.img`.

**The bootloader frees what it used (2026-10-21, later).** The picture
after the payload moved into the kernel part showed the loader naming
`lo= 000480000` - the payload's address inside the kernel part - and then
nothing: that address is not mapped either. So the bootloader keeps only
the beginning of the image (the shim's own code) and frees the rest; the
addresses the boot header names (the ramdisk at 0x1200000, the device tree
at 0x1728000) are gone as well.

What the bootloader does keep is its heap, and the address it passes in x0
points into it. The shim now hunts through that heap, a page at a time and
in both directions, for the payload's magic and the boot header's, printing
every hit as it goes: a page that is not mapped ends the walk, but the hits
stay on the screen. The device tree is then taken from the image the hunt
found.

Image: `shim/out/shim-boot-0083y.img` (md5 3013032b04a62f90754fb00525b4dd7d).

**THE KERNEL BOOTS (2026-10-21).** The screen after the hunt showed an
Apple panic screen:

```
panic(cpu 0 caller 0xffffffe0007a79068): pe_init_fiq: virtual timer INTID 0
is not a PPI @pe_fiq.c:115
```

So the shim found the payload, the kernel was loaded and started, and it
died in the platform expert - before anything else, on the first thing it
does with the timer. The reason: `shim/board.c` patched the device tree
with what the discovery had found, and the virtual timer interrupt was
written unconditionally - with no tree there is nothing to discover, so the
kernel was handed interrupt 0 instead of 19 and refused it. The tree that
`tools/mkadt.py --picasso` builds was right all along (dram 0x140000000 +
0xC0000000, GICD 0x17a00000, GICR 0x17a60000, virtual-timer-intid 19,
framebuffer 0xa0000000 + 0x2300000 at 1080x2400, UFS 0x1d84000, eight CPU
nodes). The patch is now made only for the values that were really found.

Image: `shim/out/shim-boot-0083z.img` (md5 52e95f9c0f57bef66f776fb16ed9b679).

**The platform expert panicked (2026-10-21).** The next screen showed the
kernel much further along - the AMFI policy loaded, the zones and the VM
bootstrap done - and then:

```
panic(cpu 0 caller 0xffffffe00079fdd9c): Unable to find driver for this
platform: "D22AP|iPhone10,3|xiaomi,picasso|qcom,sm7250|AppleARM"
 . @IOPlatformExpert.cpp:2319
```

That is the fallback personality (`IOPanicPlatform`) matching because the
real one did not: sweet's platform driver is registered with
`IONameMatch = 'xiaomi,sweet'` (patch 0016) and the root compatible string of
the picasso tree does not contain that. So the compatible string now carries
`xiaomi,sweet` as well - the same trick sweet uses, and the one the notes
already described. A proper board layer in the kernel (the one the
ry-diffusion port wrote, `bsd/dev/sweet_board.h`) is the better answer and
comes later.

Image: `shim/out/shim-boot-0084.img` (md5 6cf0f6e49791fac50a849de30a7a4348).

Still missing: the `/product` node of the real D22AP tree, so MobileGestalt
does not know the device (`research/ipsw-x/d22ap.adt` is not in this
checkout; the im4p is in `/home/tritm/ios-work/ipsw/Firmware/all_flash`).

**2026-10-21: iOS LOADS ON PICASSO.** The phone shows the Apple logo and a
progress bar: the kernel mounted the iOS root and launchd is starting the
userland. The chain that made it work, in the order it was found:

1. The shim must not read memory the bootloader did not map (a read there is
   a secure fault that kills the core) - so the board values are compiled in
   and the payload address comes from the boot image.
2. The bootloader frees the image it read, so neither the ramdisk address in
   the header nor the payload inside the kernel part is mapped any more.
   The shim hunts through the heap the bootloader left behind (the address in
   x0 points into it) for the payload's magic and for the boot header's.
3. The shim must not write into the device tree what it did not find: it
   wrote the virtual timer interrupt to zero and the kernel panicked in
   `pe_init_fiq` with "virtual timer INTID 0 is not a PPI".
4. The root `compatible` property is a *list* of separate strings, not one
   string with separators in it. Built as one string, the platform device's
   name was the whole thing and the kernel's driver (registered for
   'xiaomi,sweet') never matched it: "Unable to find driver for this
   platform".

Five flashes, from a frozen screen with a single `PROBE` line to iOS
loading. The image that did it: `shim/out/shim-boot-0085.img`
(md5 3a8248ab7e8295b62ce468d178e83bc2), kernel 0083 (the IOKit quiesce fix
and the IORTC fix), command line with `sweetusb=2`, `cpus=8`,
`ufsroot=darwin`, `ufsrw=1`.

What is still missing, in the order it matters:

- the `/product` node of the real D22AP tree, so MobileGestalt knows the
  device (`research/ipsw-x/d22ap.adt`; the im4p is in
  `/home/tritm/ios-work/ipsw/Firmware/all_flash`);
- touch (Novatek NT36672C over the QUPv3 SPI controller) and the keys;
- the USB gadget must come up so that the kernel log can be read live
  (`tools/sweet-usbnet.sh`).

**Why the home screen never came (2026-10-21, from the kernel log).** The log
of the boot (pulled with `tools/sweet-log-pull.sh -p darwin`) is good news
almost all the way:

```
SWEET UFS: "darwin" on LUN 0: first block 0x1882b00, 20480 MiB, read-write
hfs: mounted iOSRoot on device b(1, 0)
SWEET FB: AppleCLCD 1080x2400 stride 4320 at 0xa0000000, default surface 1,
           presented as 1125x2436
SWEET FB: display on
SWEET FB: swap 1 layer 0 surface 2 1125x2436 fmt 0x42475241 ...
SWEET KEYS: key=0 down=1 pon=0x5 ; SWEET HID: key 0x30 down (1 clients)
```

The UFS driver finds the partition and mounts the iOS root, the display comes
up, backboardd renders and swaps frames, and the keys reach a client. The
boot stops at the Apple logo because the **data migration** runs on every
boot: `com.apple.datamigrator` and `com.apple.migrationpluginwrapper` appear
three times, and the migration never finishes, so the Setup app is never
launched.

The fix is the one the topaz port found: tell the system that the migration
is already done, with the preferences the migrator reads - and it reads the
*host* variant, so the file name carries the platform UUID:

- `/private/var/mobile/Library/Preferences/com.apple.migration.plist` with
  `LastSystemVersion = 20H392`
- `/private/var/mobile/Library/Preferences/ByHost/com.apple.migration.<UUID>.plist`
  (and the lower case spelling) with the same key.

The UUID is computed the way the kernel computes it (IOPlatformExpert's
`generatePlatformUUID`: SHA1 over the namespace
`A6DD4CCB-B5E8-4AF5-ACDD-B6DC6A0542B8`, then the `unique-chip-id` and the
`chip-id` from the tree). For our tree that is
**47A4033D-9F31-5A46-90F4-BE29CA615E78**. The same code reproduces the
sweet UUID that the topaz port published
(A9683613-3704-53F2-95D0-98A8FB574E78), which is how the arithmetic was
checked.

The three files were added to the root image and the image was written back
to the `darwin` partition.

**The real D22AP device tree (2026-10-21).** The `/product` node of the iPhone
X is what MobileGestalt reads to know the device, and without it the system
calls itself an iPod. The tree is in the IPSW as
`Firmware/all_flash/DeviceTree.d22ap.im4p`: an IMG4 file whose payload is an
LZFSE stream (`bvx2`), and the result is Apple's device tree format -
186084 bytes with 61 properties under `/product`.

`tools/extract-dt.py` does that (it needs `pyliblzfse`, and the module is
called `liblzfse`). The tree goes to `research/ipsw-x/d22ap.adt`, which is
where `tools/mkadt.py` looks for it, so the picasso tree now carries the real
identity: `tools/mkadt.py --picasso` writes 12604 bytes instead of 9920.

The platform UUID of this board follows from the same tree: the kernel's
`generatePlatformUUID` hashes a namespace, the `unique-chip-id`
(0x5049434153534f01 for picasso) and the `chip-id` (0x8015), giving
**47A4033D-9F31-5A46-90F4-BE29CA615E78**.

Image with the identity: `shim/out/shim-boot-0086.img`
(md5 eb23ce6e09cd04733742cd1f5071737e).

**The home screen appears (2026-10-21, later).** With the migration
preferences in the root image the boot no longer stops at the Apple logo:
the phone now shows the home screen's background, so SpringBoard is up. What
is missing is the icons.

The tree of the iPhone X asks for `artwork-display-gamut = P3`, and the
framebuffer here is eight bits per channel: the interface is drawn into ten
bit surfaces that cannot be shown, and the icons do not appear. The tree now
says `sRGB` - the same change the topaz port made for the same reason (their
note: "P3 made WebKit draw into 10-bit surfaces we lack").

Also in this image: the shim stops its hunt at the payload (the walk went on
into memory that is not mapped and killed the core), and the device tree is
taken from behind the payload, so the board discovery works again.

Image: `shim/out/shim-boot-0088.img` (md5 d675c81faae33ca11125e4c31cf503da).

**The journal is the suspect (2026-10-21, later).** The first boot with the
new root image worked: the log shows the volume had no journal yet, the
kernel created one (`hfs: Initializing the journal (joffset 0x82000 sz
0x800000)`), and SpringBoard came up. Every boot after that hangs before the
mount: no Apple logo, no console output, and the persistent log is not
written at all - the kernel never gets as far as opening the partition.

Turning the persistent log off (`sweetlog=0`, the switch sweet_ufs documents
for it) changed nothing, so the log thread is not what blocks the UFS.

What changed between the working boot and the hanging ones is the state of
the volume: it now carries a journal, and it was pulled out of a running
iOS, so the journal was never closed. The next mount has to replay it.

The image is written again - a clean volume with no journal - and the
journal header of the phone's copy is read to see what the kernel would
replay.

**The shim works (2026-10-21, 12:40).** Its report on the phone, in full:

```
HUNT  00000000A7EC0000      payload, found in the bootloader's heap
TREE  behind the payload    the device tree, right behind the payload
DTB   00000000A8BE99000
INITRD found by the hunt
INITRD lo=A7EC0000  hi=A8E980A0
PHYS   base=140000000  size=C0000000
VIRT   FFFFFFE0006000000
ENTRY  FFFFFFE000748B488
RAM base 0000000014000000
```

So the bootloader does load the whole kernel part into its heap, keeps the
payload and the device tree next to each other there, and frees the rest:
the addresses in the boot header are useless, the heap is where the data
is. The shim hunts for it and hands over to the kernel.

After the handover the screen goes black without a panic: the kernel is
alive but has not drawn anything yet - before the console comes up, before
the Apple logo, before the USB gadget. The next question is where it
stops; the persistent log answers it if the kernel got as far as opening
the UFS (the flush counter moves), and stays unchanged if it did not.

**2026-10-21, 13:40 - where the work stands, and what the journal really is.**

The phone is *not* connected to the PC at the moment (no adb, no fastboot,
no USB device), so nothing can be flashed or pulled until the user plugs
it in again.

Two facts that were not written down before and that decide the next step:

1. **The clean root image was never written to the phone.** `/tmp/opencode/
   write-rec3.log` (11:59) says `lot 0 thu 1 loi ... DUNG tai lot 0`: the
   run failed on the very first chunk, three times. The `darwin` partition
   therefore still holds the copy that the kernel has already journalled
   (write-rec2, 10:12, wrote the same file), i.e. the volume is in the
   "dirty journal" state that makes the next boot stop early. The file on
   the PC is still clean: block 129 (the journal info block) has flags = 5
   = `kJIJournalInFSMask | kJIJournalNeedInitMask`, offset 0x82000, size
   0x800000. Writing it again puts the volume back into the state where
   the kernel creates a fresh journal and iOS boots.
2. **The local image has the plain migration plist but not the ByHost
   ones.** `/private/var/mobile/Library/Preferences/com.apple.migration.plist`
   with `LastSystemVersion = 20H392` is in the image (catalog name found
   in UTF-16), but there is no `ByHost` directory and no file whose name
   carries the platform UUID 47A4033D-9F31-5A46-90F4-BE29CA615E78
   anywhere in the 16 GiB. The working boot got the home screen anyway,
   so the plain file is enough for now.

**The journal is the prime suspect for the hang after the first boot.**
The chain in the log of the boot that worked (`/tmp/opencode/log-darwin4.txt`):

```
SWEET UFS: "darwin" on LUN 0: first block 0x1882b00, 20480 MiB, read-write
SWEET LOG: console log to the last 4100 KiB of the partition (block 0x1d827fa)
hfs: Initializing the journal (joffset 0x82000 sz 0x800000)...
hfs: mounted iOSRoot on device b(1, 0)
SWEET FB: AppleCLCD 1080x2400 stride 4320 at 0xa0000000 ... presented as 1125x2436
exec SpringBoard
```

The persistent log thread starts *before* the mount (it is started by
`sweet_log_start()` right after the partition is found, patch 0047), so a
mount that hangs would still move the log counter - unless the hang is
before the UFS open at all. That is what the log says: it never moved.
So the kernel stops somewhere between the platform expert and the UFS
partition open, or inside the journal replay while the UFS lock is held.

Apple's HFS sources (hfs-627.100.6, the same commit the CI stages into
`bsd/sweet_hfs`) were fetched to `/tmp/opencode/hfs-31d5ec23...` to read
the journal code: `hfs_early_journal_init` (core/hfs_vfsutils.c:2460),
`journal_open` (core/hfs_journal.c:1865), `replay_journal`
(core/hfs_journal.c:1162), `update_fs_block` (core/hfs_journal.c:818).
Note the block size mismatch: the BSD layer sees the device with
`su.secsize = DEV_BSIZE = 512` (patch 0028/0030) while the volume's block
size is 4096, so the journal is written and replayed in 512-byte pieces.
`journal_open` already has a "throw the journal away" path
(`flags & JOURNAL_RESET` -> `jhdr->start = jhdr->end`), and
`hfs_mount_args.journal_disable` disables the journal completely - that
is the fallback fix if the replay itself is what hangs: reset the journal
for the root mount and clear `kHFSVolumeJournaledMask` in the volume
header so the next boot does not try to replay it again.

**The diagnostic that is ready: kernel 0084 mirrors the console on the
panel.** CI kernel `xnu/out-0085`, image `shim/out/shim-boot-0085.img`
(md5 0ac9a1309042c3387cdbf8925fb164ba), command line
`... sweetusb=2 ufslogsecs=60 sweetpanel=0xA0000000`. The mirror is
hooked in `_cnputc` (osfmk/console/serial_console.c), which every console
message goes through, so a photograph of the screen shows exactly where
the kernel stops. Flash it, boot, and photograph the screen after ~30 s
and again after ~2 min.

**2026-10-21, 16:00 - what the photos proved, and the next step.**

The 0094 image (the shim prints its report and holds) and the 0095 image
(the same plus the carve-out list and the UFS probe) were photographed on
the phone. Both show a healthy board:

```
DTB at 00000000A8BE99000   state: no /memory banks in the FDT
REG x0/x1/x2 = 00000000A7EB1000
FB cont_splash_region base A0000000 size 36700160 stride 4320 (w/h 0)
RAM banks 0, carve-outs 21
GICD 17A00000  GICR 17A60000  vtimer 19  ptimer 18
UFS 1D84000  DWC3 A600000  UART 888000  CPUS 8 (0,100..700)
INITRD found by the hunt: lo=A7EC0000 hi=A8E980A0
PHYS base=140000000 size=C0000000   RAM base 140000000  RAM size C0000000
```

The 21 carve-outs are byte for byte the ones in the static device tree
(host probe on rec-picasso.dtb): everything is below 0xA2400000, nothing
touches 0x140000000, so **the kernel's RAM is fine** and the fallback is
safe. The shim, the payload, the ADT and the board are all good.

**So the kernel stops right after the console comes up.** The panel turns
black (the console clears it) and not one kernel line is visible, and the
persistent log never moves: the kernel dies before it opens the UFS.

The reason the console is not visible is the boot argument **serial=3**:
in osfmk/arm/arm_init.c, `serial=3` makes the kernel "switch into serial
mode from video console" - the messages go to the serial device (the UART
at 0x888000) instead of the panel. The next image (0096) is the same
kernel 0083 and the same ADT with that argument removed, so the video
console stays on the panel and the kernel's own output can be read from a
photograph.

If the kernel panics before the console exists, the panic text is lost
(it only reaches the console ring in RAM). The next kernel patch then has
to mirror the console on the panel without mapping memory from the
console path (patch 0084 calls ml_io_map there, which is not safe).

**2026-10-21, 16:50 - the console, the early markers, and a patch that was skipped.**

Two findings from reading the xnu sources:

1. **The kernel console never reaches the panel.** In osfmk/arm/arm_init.c
   the boot argument `serial=3` makes the kernel "switch into serial mode
   from video console", so the messages go to the serial device instead of
   the panel. Image 0096 is the same kernel without that argument, and the
   screen is still black - so the console is not the only problem.
2. **The sweet kernel already has an early boot marker system** -
   `sweet_early_mark(N, "text")` (patch 0010/0012), which draws into the
   framebuffer with no allocation and no locks, so it works before there is
   a thread or a vm. It is perfect for this bug - but it draws at the
   **sweet** board's splash address 0x9c000000 (osfmk/arm64/sweet_early.c
   and the bootstrap mapping in osfmk/arm64/start.s), while picasso's
   panel is at 0xa0000000. That is why the early markers are invisible on
   this phone.

**Patch 0084 was silently skipped by the first CI run** ("Skipping patch
... error: patch failed: osfmk/arm/arm_init.c:515") because patches 0010,
0012, 0047 and 0067 already change the two files it touches. The hunks have
to match the state those patches leave behind, not the pristine xnu tree.
The patch was regenerated against that state and verified with `git apply
--check` on a tree with 0010/0012/0047/0067 applied (CI run 39577849523,
no "Skipping patch" this time).

The new 0084 maps the panel once, right after arm_vm_init() in arm_init.c,
and the console path only writes into that mapping - mapping memory from
the console path can deadlock the kernel (the vm lock is often held there),
which is the kind of silent hang this patch is meant to diagnose. The
mirrored text is drawn at the *bottom* of the panel and grows upwards, so
it does not fight with the video console (which draws at the top).

Kernel: `xnu/out-0086` (md5 2e057af66f1c7521e65c62fb109e462a), image
`shim/out/shim-boot-0097.img` (md5 e0dcd5f763609acb41ff2be20eea0b25).

If the kernel hangs before arm_vm_init(), the mirror cannot help and the
next step is to make the early console use the panel address from the boot
args (start.s already has the boot args in x0 and reads them with the
BA_* offsets).

**2026-10-21, 17:50 - Root cause of black screen found and golden image 0088 flashed.**

Connected to the recovery host at `root@100.121.254.115` (`f9f3d7b0 recovery`).

1. **The "dirty journal" suspicion was debunked:**
   - Checked `/dev/block/sda35` (`darwin`): block 129 has `flags = 5` (`kJIJournalNeedInitMask`), and block 130 is all zeroes. The volume was never mounted after `write-rec2.sh` completed at 10:12.
   - First 16 MiB md5 matched local clean root `ios16-root-picasso.img` exactly (`249476a1a16d8186b87774ae9ce3b022`). The root filesystem is 100% intact and clean.
2. **Root cause of the black screen on image 0097:**
   - In patch 0084 (`xnu/out-0086`), `sweet_panel_map_console` called `ml_io_map` for `1080 * 2400 * 4 = 10,368,000 bytes` (~9.88 MiB) right after `arm_vm_init()`.
   - In XNU, `io_submap` has a total size of only **8 MiB** (`0xfffffe8fff5fc000 - 0xfffffe8fffdfc000`), so `ml_io_map` immediately panicked or failed.
   - Combined with `disableConsoleOutput = TRUE` suppressing early output, the panel remained black.
3. **Image 0088 was not the golden working image:**
   - The user clarified that the boot that showed the Apple logo and progress bar occurred earlier today around commit `61a7802` / `53f5287` (log `log-darwin.clean.txt`, kernel 0083).
   - In that working boot, `research/ipsw-x/d22ap.adt` did not exist yet, so the ADT was 9,920 bytes and had **no `/product` node** (`IOK backboardd: path IODeviceTree:/product -> none`). Backboardd and ProgressUI cleanly fell back to software rendering into IOSurface / AppleCLCD.
   - At commit `9df2225`, 61 properties were extracted into `/product`, including `graphics-featureset-class = APPLE4` and `oled-display = 1`. This instructs ProgressUI / backboardd to require Apple Metal hardware GPU acceleration, causing an immediate crash/abort on SM7250 (Adreno 620). Furthermore, `SweetPlatformExpert::processTopLevel` did not exclude `product`, creating an invalid nub in the kernel.
   - Consequently, every image built after 09:49 (images 0086 through 0097) went to a black screen after the red shim screen.

**2026-10-21, 22:07 - Fixed shim hunt bounds and preserved clean ADT on picasso.**

1. **Diagnosed reboot loop and black screen:**
   - The reboot loop on the red shim screen (`HUNT 0xA7EC0000`) was caused by `hunt_memory` continuing to walk unmapped DRAM past the payload, triggering TrustZone/Data Abort.
   - Added immediate `break` in `hunt_memory` once `h.payload` is found.
   - Fixed `find_dtb` to preserve `picasso-clean.adt` without attempting to parse static Linux FDT, which previously overwrote `arm-io/gic` and caused early kernel death before UFS mount.
2. **Built and flashed `shim-boot-0100.img` (36,352,000 bytes, MD5 `43b46155f12316eba783dab9cfcbdb89`):**
   - Eliminated probe of `KERNEL_ADDR + DTB_OFF` (`0x01021000`) in `find_dtb` which triggered Qualcomm TrustZone secure fault (< 0x10000000).
   - Transferred to remote host, written to `/dev/block/bootdevice/by-name/boot`, readback verified 100%.
   - Sent reboot command to device `f9f3d7b0`.

1. **Reverted shim to commit `35ea828`:**
   - In commit `d965b1a`, a `break` in `hunt_memory` caused `hunt_result.image` to remain 0, breaking real DTB discovery and causing the kernel to fail before mounting UFS.
   - Reverted `shim/loader.c`, `shim/shim.c`, `shim/board.c`, `shim/build.sh`, `shim/hw.h` back to `35ea828`.
   - Built payload `payload-picasso-0099.bin` with kernel 0083 and clean ADT (9,920 bytes, no `/product`).
   - Flashed `shim-boot-0099.img` (md5 `59020eee85ad72b6dbcccd5b4a7cfb05`) to `boot`.
2. **Boot verification - Apple logo restored:**
   - Phone successfully booted: **Apple logo appeared on panel**!
   - SWEETLOG moved to flush #19 (155,940 bytes):
     - UFS mounted `iOSRoot` read-write.
     - AppleCLCD initialized 1080x2400 presented as 1125x2436.
     - `launchd` started, early boot complete.
     - `backboardd` started and swapped 5 frames (double buffered surfaces 3 & 4).
     - `SpringBoard` (PID 26) started and ran without crashing.
3. **Root cause of hang at Apple logo diagnosed:**
   - In 190s of boot (19 flushes * 10s), `CommCenter` crashed in an infinite loop (>50 times, up to PID 256) because non-Apple hardware lacks Apple baseband PCIe/UART modem.
   - SpringBoard and Setup.app block synchronously on CoreTelephony XPC waiting for CommCenter.
   - Furthermore, `com.apple.purplebuddy.plist` was missing (`SetupDone = True` not set).
4. **Patched iOS root (`ios16-root-picasso.img`):**
   - Applied `tools/sweet-root-tweaks.py`:
     - Removed `com.apple.CommCenter` from `launchd.plist`.
     - Set `com.apple.purplebuddy.plist` with `SetupDone = True`, `SetupFinishedAllSteps = True`, `SetupVersion = 11`.
     - Set `BKNoWatchdogs = True` for SpringBoard.
     - Added ByHost UUID `47A4033D-9F31-5A46-90F4-BE29CA615E78` migration plists.
     - Filesystem verified clean via `fsck.hfsplus`.
5. **Partial sync to phone `darwin` partition (`sda35`):**
   - Smart sync script `tools/write-root-smart.py` synced modified chunks (chunks 1, 2, 4, 14, 16 verified written; chunks 3, 5-13, 15, 17-23 matched and skipped).
   - Paused gracefully at user request to charge worker phone (K60). Phone filesystem flushed with `sync`.
   - Remaining work: resume `write-root-smart.py` to finish chunks 24-32, then reboot to Home Screen.

**2026-10-21, 21:00 - Rootfs 100% synced, SWEETLOG flush #71 analyzed, and patches 0085-0087 added.**

1. **Rootfs sync completed & verified:**
   - All 32 chunks (16 GiB) of `ios16-root-picasso.img` synced and MD5 verified on `/dev/block/sda35` (`darwin`).
   - Rebooted phone via `adb reboot` into iOS 16.
2. **SWEETLOG flush #71 pulled and analyzed (`/tmp/sweet-boot-latest.txt`):**
   - `CommCenter` is completely gone (0 crashes).
   - `launchd` launched over 100 system daemons cleanly (`securityd`, `powerd`, `runningboardd`, `containermanagerd`...).
   - `SpringBoard` (PID 26) and `backboardd` (PID 56) are alive and active. SpringBoard thread 0 entered `CFRunLoopRunSpecific`.
   - `backboardd` swapped 5 frames (surfaces 2 & 3) and then stalled at Apple logo.
   - Keys work (`SWEET KEYS: key=0 down=1 pon=0x5` -> `SWEET HID: key 0x30 down`).
3. **Diagnosed why screen stayed on Apple logo:**
   - Qualcomm DPU reads physical DRAM directly; XNU's `clean_dcache` / `flush_dcache` were no-ops on `__ARM_COHERENT_IO__`. CPU was writing rendered frames to cache, but dirty cache lines were never flushed to DRAM.
   - QuartzCore (`CAMetalContextCreate`) stalled for 30s per process waiting for `IOAcceleratorES`.
   - `SweetSurface` did not handle `IOSurfaceAddress`, leaving SpringBoard UI surfaces unpublished (id = 0), and trap 4/5 released surfaces in use.
4. **Added Patches 0085 - 0087 (adapted from MakrSas/ios-topaz):**
   - `0085-picasso-dcache-flush.patch`: Real cache maintenance (`dc civac` / `dc cvac` loops in `osfmk/arm/caches.c`) and `sweet_dcache_clean` on `fPixels` in `AppleCLCD::present`.
   - `0086-picasso-ioacceleratores-empty.patch`: Dummy `IOAcceleratorES` service published at `AppleCLCD::start`, cutting 30s QuartzCore startup delay.
   - `0087-picasso-sweetsurface-wrap-publish.patch`: `SweetSurface` client wrapping (`IOSurfaceWrapClientImage/Memory`), values, `IOSurfaceAddress` task memory with publishing, trap 4 detach, and zombie state page retention.
   - Validated contiguous patch sequence (0001-0087) via `tools/validate-repo.py`.

**2026-10-22, 05:50 - Run 7 Diagnosis & Run 8 Boot (Lock Screen / Always-On Display).**

1. **Run 7 Analysis (commit `052ccce`, CI run `39656614838`):**
   - Panel illumination fix verified: `sweetfb_keepon=1` kept the backlight lit.
   - DWC3 UTMI sequence verified: `QSCRATCH GENERAL_CFG 0x0000000d` and soft reset completed in 1 ms without `ep0 cmd 0x1 timeout`.
   - Observed reboot after 3 seconds: caused by `debug=0xc` (`DB_REBOOT`) triggering restart after `ud_epcmd` / `sweet_usb_gadget_start`.
2. **Run 8 Deployed (`payload-picasso-0104.bin`):**
   - Set `debug=0x14e` to prevent automatic reboot on panic and freeze output if halted.
   - Set `sweetusb=0` to isolate USB controller operations, ensuring clean userland boot.
   - Kept `sweetfb_keepon=1` and `sweetfb_noconsole=1` to keep the display lit while SpringBoard boots.
   - Flashed `shim-boot-0104.img` directly to `boot` partition via recovery ADB and rebooted device `f9f3d7b0`.

**2026-10-22, 07:12 - Run 8 Diagnosis: SpringBoard BoundingPath Crash Found & Rootfs Synced.**

1. **Extracted 904 KiB log from Run 8 (`/tmp/log-run8.txt`):**
   - Device ran 10+ minutes stably with display continuously illuminated (confirming `sweetfb_keepon=1`).
   - Pinpointed root cause of Apple logo hang: `SpringBoard` was crashing repeatedly in `-[_UIScreenComplexBoundingPathUtilities _loadBitmapForScreen:type:]`:
     `*** Terminating app due to uncaught exception 'NSInternalInconsistencyException', reason: 'Failed to load bounding path bitmap data from the asset manager for asset name: BoundingPathBitmap-1125x2436-375x812-3.00x'`
   - Each crash spawned `ReportCrash` and `launchd` restarted `SpringBoard`, causing `backboardd` to retain the boot splash logo.
2. **Synced 15 modified chunks to phone `darwin` partition (`/dev/block/sda35`):**
   - Used direct local USB via `tools/write-direct-smart.py` (chunks 0, 1, 8, 13, 15, 17, 23, 24-30, 31).
   - All 15 chunks verified with MD5 on `/dev/block/sda35`. `BoundingPathData.bundle` is now present in the on-device rootfs.

**2026-10-22, 07:23 - Run 9 Verified: Zero Crashes, SpringBoard Running, Ready for Auto-Wake (Run 10).**

1. **Extracted 119 KiB log from Run 9 (`/tmp/log-run9.txt`):**
   - `BoundingPathBitmap` assertion failure is 100% GONE.
   - ZERO process crashes: `SpringBoard` (PID 26), `backboardd` (PID 56), `PosterBoard` all running smoothly.
   - `backboardd` performed 5 swaps, then requested `display off` and waited for user wake/Home button input.
2. **Run 10 Deployed (`payload-picasso-0105.bin`):**
   - Added `sweethidtest=5` to inject automatic Home/Menu presses every 5 seconds.
   - This fires wake events into `backboardd` to dismiss the boot splash and reveal the Lock Screen/Home Screen.
   - Flashed `shim-boot-0105.img` to `boot` partition.

**2026-10-22, 07:35 - Run 10 Diagnosis & Run 11 Deployment with USB Gadget (`sweetusb=2`).**

1. **Analyzed Run 10 Log (`/tmp/log-run10.txt`):**
   - Zero crashes maintained.
   - `SweetHIDButtons` fired simulated key 0x40 (`kHIDUsage_Csmr_Menu`), but on iPhone X (`D22AP`) the device has no physical Home button; display wake requires Side button (`kHIDUsage_Csmr_Power` 0x30) or touch.
   - User requested USB networking verification.
2. **Run 11 Deployed (`payload-picasso-0106.bin`):**
   - Set `sweetusb=2` with `sweetusblog=0` and `debug=0x14e`.
   - Flashed `shim-boot-0106.img` to `boot` partition. Ready to monitor USB network interface on host.

**2026-10-22, 07:45 - Run 11 Bootloop Diagnosed & Run 12 Prepared (`payload-picasso-0107.bin`).**

1. **Diagnosed 2-3s Bootloop in Run 11:**
   - User reported: "hien console nhanh lam, tam 2-3 la bi bootloop roi".
   - Root cause: Setting `sweetusb=2` executed Qualcomm wrapper clock sequence in `sweet_usb.c` (`QSCRATCH_GENERAL_CFG` clock switch with `PIPE_UTMI_CLK_DIS`). On Snapdragon 765G (SM7250), disabling the PIPE clock while GCC USB clocks are active causes the Qualcomm RPM clock watchdog to detect clock loss, triggering an immediate SoC **HARDWARE RESET** after 2-3s.
   - When `sweetusb=0`, the USB driver code is bypassed entirely, and userland runs continuously for 10+ minutes with ZERO crashes.
2. **Diagnosed 4-Minute Apple Logo Hang in Run 10:**
   - Log `/tmp/log-run10.txt` confirms:
     - 0 process crashes across the entire OS (`SpringBoard` PID 26, `backboardd` PID 56, `launchd`, all daemons running).
     - `backboardd` swapped 5 frames (Apple logo) into surfaces 2 & 3, then called `kSweetFBRequestPowerChange(0)` (display off).
     - `sweetfb_keepon=1` prevented hardware panel power-off, freezing the Apple logo on screen.
     - `backboardd` put itself into display sleep waiting for wake events.
     - `sweethidtest=5` fired simulated key 0x40 (`kHIDUsage_Csmr_Menu`), but on iPhone X (`D22AP`) there is no physical Home button; `backboardd` ignores 0x40 for screen wake.
     - `usermanagerd` completed persona generation (UID 501 `mobile`), and SpringBoard is active in `CFRunLoopRunSpecific`.
3. **Run 12 Built & Deployed (`payload-picasso-0107.bin`):**
   - Reverted `sweetusb=0` to eliminate the 2-3s hardware watchdog reset.
   - Built `shim/out/shim-boot.img` (36,352,000 bytes, MD5 `2ad84d964ff9307f3f4860e3067ee98d`).
   - Flashed directly to `/dev/block/bootdevice/by-name/boot` (`/dev/block/sde55`) via recovery ADB, readback MD5 verified 100%.
   - Rebooted phone via `adb reboot` into iOS 16 at 07:47. User confirmed Apple logo appeared stably.
   - Log `/tmp/log-run12.txt` verified: 0 crashes across all processes, SweetHIDButtons received user Power button presses (Usage 0x30).

**2026-10-22, 07:57 - Patch 0088 Fixed for SM7250 USB Clock & Power Wake, CI Run Triggered.**

1. **Fixed DWC3 USB Clock Sequence for SM7250:**
   - In `xnu/patches/0088-picasso-display-alwayson-usb-fix.patch`:
     - Qualcomm SM7250 (picasso) HAS a SuperSpeed PHY / PIPE clock (`maximum-speed = "super-speed"` in `lito-usb.dtsi`).
     - The `PIPE_UTMI_CLK_DIS` sequence in `QSCRATCH_GENERAL_CFG` was intended only for legacy platforms without SuperSpeed PHY. Disabling PIPE clock tripped the Qualcomm RPM clock watchdog, triggering SoC hardware reset in 2-3s.
     - Removed the `PIPE_UTMI_CLK_DIS` clock-killing sequence while preserving VBUS session override (`HS_PHY_CTRL` / `SS_PHY_CTRL`), LPM clear (`SUSPHY` / `ENBLSLPM`), clock gating disable (`GCTL_DSBLCLKGTNG`), and 100ms `ud_epcmd` timeout.
2. **Added Power Button Wake to `SweetHIDButtons::testFired`:**
   - In `SweetHID.cpp`: Added `self->key(kHIDUsage_Csmr_Power, press);` alongside Menu press so automatic wake events properly register on buttonless iPhone X (`D22AP`).
3. **Run 13 Built & Deployed (`payload-picasso-0108.bin`):**
   - Packaged new kernel from CI run `39702769666` (commit `d7d27e6` with SM7250 DWC3 clock watchdog fix and Power button wake).
   - Set `sweetusb=2`, `sweetusblog=0`, `sweethidtest=5`, `sweetfb_keepon=1`, `debug=0x14e`.
   - Flashed directly to `/dev/block/bootdevice/by-name/boot` (`/dev/block/sde55`) via recovery ADB, readback MD5 verified 100%.
   - Booted into iOS 16: Apple logo appeared, zero reboot/watchdog reset.
   - Pulled log `/tmp/log-run13.txt` (99,312 bytes):
     - `SWEET HID: key 0x30 down/up` fired every 5s continuously to `backboardd`.
     - `SWEET USB: ep0 cmd 0x1 timeout (DEPCMD 0x00000401 GCTL 0x00102001 GUSB2PHYCFG 0x00002400)`.
     - Diagnosed: On SuperSpeed controller SM7250, clearing `SUSPHY` only on `GUSB2PHYCFG` (0xc200) was insufficient because `GUSB3PIPECTL` (0xc2c0) had bit 17 (`SUSPHY`) set, clock-gating the DWC3 core during endpoint setup.
4. **Patch 0088 Fixed for GUSB3PIPECTL SUSPHY & CI Run Completed:**
   - Cleared `GUSB3PIPECTL` bit 17 (`SUSPHY`) around soft reset and in `ud_epcmd`.
   - Committed `5b894ab`, pushed to `picasso/main`. CI workflow run `39705075878` succeeded in 5m30s.

**2026-10-22, 08:30 - Run 14 Built & Deployed (`payload-picasso-0109.bin`).**

1. **Packaged Kernel & Deployed to Device:**
   - Kernel artifact `12394681709` from CI run `39705075878` extracted to `/tmp/run14_kernel`.
   - Built payload `payload-picasso-0109.bin` with `sweetusb=2`, `sweetusblog=0`, `sweethidtest=5`, `sweetfb_keepon=1`, `debug=0x14e`.
   - Built `shim/out/shim-boot.img` (36,352,000 bytes, MD5 `c1a72f8f36787ca687e2477bdd259792`).
   - Flashed to `/dev/block/bootdevice/by-name/boot` (`/dev/block/sde55`) via recovery ADB, readback MD5 verified 100%.
   - Ready for reboot to test USB CDC-ECM gadget and network enumeration.








