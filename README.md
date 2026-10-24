# iOS 16 (XNU) on the Redmi K30 5G — picasso / SM7250

XNU (Darwin 22, iOS 16 userland) running on a Qualcomm Snapdragon 765G phone.
This file is the short version; `NOTES.md` is the full engineering log and is
authoritative for measurements.

## Status at a glance

| Area | State |
|---|---|
| Boot chain (ABL -> shim -> XNU) | **works** |
| Kernel + iOS userland (launchd, SpringBoard) | **works** |
| UFS root on `userdata`/darwin, persistent log ring | **works** |
| Framebuffer console, display always-on | **works** |
| Touch (FocalTech FTS), battery, buttons | partial (see `NOTES.md`) |
| USB device mode / USB networking | **does not work** (DMA on the apps SMMU resets the phone) |
| Modem PAS boot (TrustZone) | **works** (`auth_and_reset` ret 0, MODEM READY) |
| GLINK / QRTR / name server | **works** |
| pd-mapper with the real PDR database | **works** (six domains from the modem partition's `*.jsn`) |
| tftp service (announced, serves `image/*`) | works as a server, never called by the modem yet |
| rmtfs (EFS over QMI) | **first read served** (`iovec ... copied`); a later packet freezes the machine |
| WLAN (`wlan_pd` -> WLFW -> WMI/HTC/HTT) | **not reached**: the modem never starts its WLAN PD |

So: iOS boots and runs on the phone, the modem link and its host services are up,
and Wi-Fi is the remaining frontier.

## What the WLAN path needs (and where it stands)

The WLAN lives *inside* the modem on this SoC, so the AP only supplies host
services and then drives the firmware:

1. rails + ARC corners -- done (all `rc 0`)
2. `pd-mapper` answering `tms/pdr_enabled` with the vendor domain list -- done
3. `tftp` announced with the reference triple `(4096, 1, 0)` -- done
4. `rmtfs` serving EFS -- first read works; the link still dies on a following packet
5. modem starts `wlan_process`, fetches `wlanmdsp.mbn` over tftp -- never observed yet
6. WLFW service `0x45` and the attach sequence
   (`ind_register -> msa_info -> msa_ready -> cap -> host_cap`) -- client is written,
   never exercised
7. host driver: HTC/WMI/HTT + scan/join + `ifnet`/Apple80211 -- not started here;
   the sibling port **ios-sapphire** (SM6225, same modem-hosted design) has this
   working in its patches `0129`/`0130`, which is the intended reference

## Hard-won facts worth keeping

* The modem keeps the `ALLOC_BUFF` address in **32 bits** and echoes it back in
  `phys_offset`, so the rmtfs buffer must be **below 4 GiB**. `shim/build.sh`
  hard-codes the kernel in the high bank (`0x140000000`), so the buffer comes
  from a fixed pool at **`0xE0000000`** (the trick ios-topaz/ios-sapphire use).
* That pool must be mapped **cache-inhibited**; a write-combining mapping hangs
  the SoC on the first store.
* SCM `assign_mem` of ordinary RAM returns `-2 / 0x56` (TrustZone refuses); the
  reference port sees the same and does not need it.
* Keep UFS out of the GLINK receive path: the log thread shares the UFS lock.
* `ufslogsecs=1` makes the ring flush every second, which is the only reliable
  witness when the console screen is stale or the font tiny.
* Console photos can show a previous boot (the shim does not clear the
  framebuffer); the ring's own `boot-args` line says which payload ran.

## Build and flash

* Kernels are built by GitHub Actions (`xnu.yml`, branch `omp`); artifacts are
  `xnu-kernel` (the Mach-O) and `xnu-buildtree` (for patch validation).
* Validate a patch against the real CI tree before pushing:
  `tools/check-patch.sh PATCH buildtree.tgz`.
* Package and flash:
  `tools/mkpayload.py KERNEL shim/out/picasso-clean.adt PAYLOAD --cmdline "..."`
  then `shim/build.sh PAYLOAD --picasso` and either
  `fastboot flash boot shim/out/shim-boot.img` (most reliable) or, from recovery,
  `dd if=shim/out/shim-boot.img of=/dev/block/bootdevice/by-name/boot bs=4M`
  after clearing `misc`.
* One-shot test cycle: `tools/bisect-modem.sh <variant>...` (flashes, proves the
  phone really rebooted, pulls the ring).
* Logs: `tools/sweet-log-pull.sh -p darwin out.txt`.
* Local structural check before spending CI time: `tools/cpp-scope-check.py`.

## Next steps

1. Finish the rmtfs serving: stop the freeze that appears right after the first
   served read (suspects: UFS lock contention, reply path, console lock).
2. Watch for `tftp RRQ /readonly/firmware/image/wlanmdsp.mbn` and `wlan_pd`.
3. Exercise the WLFW attach client once service `0x45` appears.
4. Port the WLAN host driver from ios-sapphire (HTC/WMI/HTT, scan, join) and add
   the `ifnet`/Apple80211 glue and DHCP.

Longer term: USB device mode would remove the flash/reboot loop entirely; the
DMA failure on the apps SMMU is the blocker there. A hardware UART
(`0x988000`, as UniXNU-picasso uses) would give a live console if a UART adapter
is available.
