# XNU (iOS 16 userland) on the Redmi K30 5G — picasso / SM7250

Bring-up of XNU 8796.141.3 (Darwin 22, iOS 16 userland) on a Qualcomm
Snapdragon 765G phone, booted as a temporary `boot` image on top of the stock
Android bootloader. No partition table changes, no bootloader unlock beyond
fastboot, no vendor firmware replacement.

This file is the overview: scope, layout, build/flash pipeline and where every
log lives. `NOTES.md` is the authoritative engineering log (measurements,
run-by-run results, known-good and known-bad configurations); `logs/` holds the
kernel rings pulled from the device.

## Status

| Area | State | Evidence |
|---|---|---|
| Boot chain (ABL → shim → XNU) | works | `shim/out/shim-boot.img`, readback MD5 checked after every flash |
| Kernel + iOS userland (launchd, SpringBoard) | works | bp31/bp32 rings reach `libignition: ignition sequence complete` |
| UFS root (`darwin` partition), bounce and direct DMA | works | `ocs 0` completions in the ring; patch 0110/0108 |
| Persistent kernel log ring | works | patch 0047, `SWEETLOG` header, flush every `ufslogsecs` |
| Framebuffer console, display always-on | works | patch 0047/0062, `sweetfb_keepon=1` |
| Touch (FocalTech FTS), battery, side buttons | partial | `NOTES.md`, PMIC GPIO mapping missing for buttons |
| USB device mode / USB networking | blocked | apps SMMU left enabled and locked by XBL: DWC3 `STARTTRANSFER` resets the phone for every buffer address tried |
| Modem PAS boot (TrustZone `auth_and_reset`) | works | `ret 0`, `MODEM READY` |
| GLINK / QRTR / QRTR name server | works | service registry dump in `NOTES.md` |
| pd-mapper with the real PDR database | works | six domains parsed from the modem partition's `*.jsn` |
| tftp server (`image/*`) | announced, never called | reference triple `(4096, 1, 0)` accepted |
| rmtfs (EFS over QMI) | first read served, then the machine freezes | `iovec … copied` followed by a silent stop |
| WLAN (`wlan_pd` → WLFW → WMI/HTC/HTT) | not reached | the modem never starts its WLAN PD |

The WLAN on this SoC lives inside the modem, so the AP only supplies host
services (pd-mapper, tftp, rmtfs) and then drives the firmware. Steps 1–4 of that
sequence are done; the open items are listed under *Next steps*.

## Hardware facts that shape the port

* Kernel-managed RAM is chosen by the ADT (`/chosen` dram-base/size) and by a
  compile-time fallback inside the shim; the two must agree.
* The modem holds the rmtfs `ALLOC_BUFF` address in **32 bits** and echoes it
  back in `phys_offset`, so that buffer has to stay below 4 GiB.
* Vendor DXE set is absent (`xbl` has an empty FV, `abl` decompresses to a single
  LinuxLoader PE32), so USB cannot be delegated to a vendor UEFI stage either.
* The shim never clears the framebuffer: a photo may show an earlier boot. The
  ring's own `boot-args` line is what identifies the running payload.

## Repository layout

| Path | Contents |
|---|---|
| `xnu/patches/` | the port, as ordered patches applied to Apple's xnu 8796.141.3 (sweet/picasso board config) |
| `shim/` | bare-metal loader: DT fixups, payload copy, boot.img packer (`build.sh`) |
| `tools/` | build, packaging, flashing, patch validation and log tooling (see below) |
| `logs/` | rings pulled from the device, one set per boot label `bpNN`, plus the exact boot-args |
| `NOTES.md` | engineering log: every measurement, every regression, current blocker |

## Build pipeline

1. **Kernel** — GitHub Actions, `.github/workflows/xnu.yml`, builds xnu with the
   patches in `xnu/patches/` applied in filename order. It runs on push to
   `main` of the `ios-picasso` fork; a side branch needs an explicit
   `gh workflow run xnu.yml --ref <branch>`. Artifacts: `xnu-kernel` (the Mach-O,
   `kernel.development.sweet`) and `xnu-buildtree` (the exact tree, for patch
   validation). Download with `tools/gh-artifact.sh RUN_ID xnu-kernel out.zip`
   (parallel range requests).
2. **Patch validation** — `tools/check-patch.sh PATCH buildtree.tgz` applies the
   patch to the real CI tree and compares byte-for-byte. CI *skips* a patch whose
   context does not match and still uploads a kernel, so an unvalidated patch can
   silently cost a boot cycle.
3. **Payload** — `tools/mkpayload.py KERNEL ADT OUT --cmdline "..."` resolves the
   kernel's chained fixups and writes the `SWEETXNU` container.
4. **Boot image** — `shim/build.sh PAYLOAD --picasso [--extra "..."]` produces
   `shim/out/shim-boot.img` (boot.img v2, <256 KB on picasso).

## Flash paths

| Situation | Command |
|---|---|
| Bootloader | `fastboot flash boot shim/out/shim-boot.img` (no A/B slot; most reliable) |
| Recovery, local | `adb push` + `dd if=… of=/dev/block/bootdevice/by-name/boot bs=4M`, then verify the readback MD5 |
| Recovery, phone on another host | `tools/picasso-boot.sh` / `tools/picasso-boot-remote.sh` |
| Bootloader, phone on another host | `tools/picasso-fastboot-remote.sh KERNEL "boot-args"` |
| Batch bisect | `tools/bisect-modem.sh VARIANT…` (flashes, proves the payload really ran, pulls the ring) |

Always compare the image MD5 before flashing and the partition readback MD5
after; a stale image and a failed payload look identical on the screen.

## Where logs and artifacts live

| What | Location | How to produce / read it |
|---|---|---|
| Kernel ring, on the device | last 1025 × 4 KiB blocks of the `darwin` partition (`/dev/block/sda35`), `struct sweet_log_header` magic `SWEETLOG` | written by patch 0047; only the first `total` bytes are this boot |
| Ring pull, phone on this host | `tools/sweet-log-pull.sh -p darwin out.txt` (needs recovery ADB, `adb root`) | reads 4 MiB, cuts to `total` |
| Ring pull, phone on the remote host | `tools/sweet-log-pull-remote.sh out.txt -p darwin` (`REMOTE`, `REMOTE_PASS`, `SERIAL`) | same data over SSH |
| Manual read from recovery | `dd if=/dev/block/sda35 bs=4096 skip=5242106 count=1025` | pull before flashing the next payload — every boot overwrites the ring |
| Ring, in the repo | `logs/ring-bpNN.txt.gz` + `logs/cmdline-bpNN.txt` | valid bytes only, gzipped; raw 4 MiB dumps stay out of git |
| Ring index | `logs/README.md` | one row per boot: kernel patch level, stage reached, panic |
| CI build logs | `gh run view RUN_ID -R minhtritt1996/ios-picasso --log` | also `gh run list` for the verdict |
| CI artifacts (downloaded) | `xnu/out-<patch>-run<ID>/kernel.development.sweet`, `xnu-buildtree` unpacked under `/tmp/btb<ID>/` | working copies; not tracked |
| Payloads and boot images | `shim/out/payload-picasso-NNNN.bin`, `shim/out/picasso-clean.adt`, `shim/out/shim-boot.img` | regenerated by step 3–4 above |
| Console photos | outside the repo (`/tmp/*.jpg`) | only useful together with the ring's `boot-args` line |
| QEMU smoke test of the kernel | `docs/QEMU_TESTING.md` | `tools/qemu-sweet.py` |

The ring survives a dead machine: the buffer stays in RAM and keeps growing even
when the flusher stops, so a boot that wedged at 3 s can still be read back after
a reset into recovery. Treat it as the primary channel — the framebuffer console
is secondary and can be stale or too small to read.

## Debugging rules learned the hard way

* One writer: keep UFS out of the GLINK receive path, and out of any timer thread
  that is not holding the driver lock (patch 0116).
* Panics print on the console but the *reason* string only exists in
  `debugger_panic_str`; patch 0114 hands it to the ring from `panic_spin_forever`.
* `ufslogsecs=1` flushes the ring every second; without it a wedged boot loses the
  last seconds.
* Silent stops are deadlocks, not aborts: an actual abort prints a stack.

## Next steps

1. Close the rmtfs freeze that follows the first served read (UFS lock contention
   vs. the reply path, both instrumented in the ring).
2. Observe `tftp` `RRQ /readonly/firmware/image/wlanmdsp.mbn` and the `wlan_pd`
   start.
3. Exercise the WLFW attach client once service `0x45` appears.
4. Port the WLAN host driver (HTC/WMI/HTT, scan/join, `ifnet`/Apple80211) from the
   sibling port `ios-sapphire` (SM6225, same modem-hosted design), patches
   `0129`/`0130`.
5. USB device mode would remove the flash/reboot loop, but needs the apps SMMU
   out of the way — a SCM or bootloader-level change, not a kernel one.
