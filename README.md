# XNU (iOS 16) + a Darwin userland on the Redmi K30 5G — picasso / SM7250

Bring-up of XNU 8796.141.3 (Darwin 22, iOS 16 userland) on a Snapdragon 765G
phone, booted as a temporary `boot` image on top of the stock Android bootloader.

The phone's LUN0 partition table **was modified once, deliberately**: `userdata`
was shrunk and a new `darwin` partition created to hold the iOS root (see
*Partitioning* below, and `tools/gpt-add.py`). `boot`, `recovery`, the firmware
partitions (`modem`, `persist`, …) and the backups are untouched, so the phone
still returns to a stock Android by flashing back the stock `boot` image.

This file is the overview: what works today, how it is built, flashed and
debugged, and what is still open. `NOTES.md` is the engineering log (every
measurement, every regression, current blocker); `logs/` holds the kernel rings.

## What works today

| Area | State | Evidence |
|---|---|---|
| Boot chain (ABL → shim → XNU) | works | `shim/out/shim-boot.img`, partition readback MD5 checked after every flash |
| Kernel + iOS userland (launchd, SpringBoard) | works, boot-phase flaky | rings reach `libignition: ignition sequence complete`; launchd sometimes stops in the `exec xpcproxy` phase instead of the daemon phase |
| UFS root (`darwin`), direct DMA | works | `ocs 0` completions in the ring |
| Persistent kernel log ring | works | patch `0047`; 4 MiB ring in the last 1025 blocks of `darwin` |
| **Reading that ring over USB, without recovery** | **works** | `tools/picasso-ecm.py l2 log` / `l2 logback N`; measured **379/379 replies over 180 s**, patch `0150` |
| **USB device mode + CDC-ECM networking** | **works** | apps-SMMU stream for SID `0xE0` attached to a stage-1 identity bank; host binds `cdc_ether` (`1209:0001`), `usb0` up, ping/ARP both ways |
| **Private kernel control channel over ECM** | **works** | EtherType `0x88b6`: `info`, `log`, `ringpos`, `stat`, `cat`, `put`, `reboot` |
| **Freestanding static arm64 userland binary** | **works** | `tools/sweetsh` — launchd execs it, it binds UDP 4444 and answers; `ls /` returns the real HFS+ root |
| **Cross-built Darwin userland (CI)** | **works** | `sh` (dash 0.5.12, 167,504 B), `dropbear` (285,184), `dbclient`, `dropbearkey` — Mach-O arm64, valid ad-hoc signatures |
| **Creating files in the HFS+ volume offline** | **works** | `tools/hfsplus-tool.py create` (catalog surgery); files read back byte-identical |
| Framebuffer console, display always-on | works | `sweetfb_keepon=1` |
| Touch, battery, side buttons | partial | PMIC GPIO mapping for the buttons is still missing |
| Modem PAS boot, GLINK/QRTR, pd-mapper, tftp | works | see `NOTES.md` |
| rmtfs (EFS over QMI) | first read served, then a freeze | UFS lock contention, instrumented |
| WLAN (`wlan_pd` → WLFW → WMI/HTC/HTT) | not reached | the modem never starts its WLAN PD |

The two things that still block end-to-end use are *not* USB: this port's
**VFS/UFS path wedges** under load (it kills the kernel command thread, and it is
why launchd often never reaches its daemon phase), and launchd's **platform-binary
handshake** rejects our binary as a *boot task* (a normal daemon is fine).

## Partitioning

The iOS root lives in its own GPT partition, added by shrinking `userdata`:

| Item | Value |
|---|---|
| Partition name | `darwin` (`/dev/block/by-name/darwin`, `sda35` = LUN0) |
| Extent | blocks 25,701,120 … 30,944,251 (4096-byte blocks, 21.5 GB) |
| Filesystem | HFSX, 4096-byte allocation blocks, 4,194,304 blocks = 16 GiB used |
| Mounted as | the root, via `rd=ufsroot0 ufsroot=darwin` |
| Kernel log ring | the **last 1025 blocks** of the partition (`skip=5242106`) |
| Written by | `tools/gpt-add.py add HEAD.bin TAIL.bin darwin <GiB> ...` — recomputes the GPT header and entry-array CRCs, so nothing else moves |
| Safety | `tools/picasso-backup.sh` first dumps the LUN0 GPT head/tail and the `boot` partition |

Everything else on LUN0 is still Android's `userdata`; the firmware partitions on
the other LUNs (e.g. `modem` on LUN4) were never touched.

## What is installed on the phone

Written with `tools/hfsplus-tool.py create` + `tools/hfsplus-ship.py` (only the
changed blocks are shipped, each md5-verified on the device):

```
/private/var/root/sweetsh                       the freestanding shell (UDP 4444)
/private/var/root/sh                            dash
/private/var/root/dropbear                      dropbear sshd
/private/var/root/dbclient                      dropbear client
/private/var/root/dropbearkey                   host key generator
/System/Library/LaunchDaemons/com.picasso.sweetsh.plist   RunAtLoad -> sweetsh
```

`/usr/local` does not exist on this rootfs and cannot be created from the kernel,
so the userland lives under `/private/var/root`.

## Building

1. **Kernel** — GitHub Actions, `.github/workflows/xnu.yml`, applies
   `xnu/patches/*` in filename order to Apple's xnu 8796.141.3.
   `gh workflow run xnu.yml --ref omp` (a side branch does not trigger on push).
   Artifacts: `xnu-kernel` (`kernel.development.sweet`) and `xnu-buildtree`.
   Always grep the log for `Applying patch …/NNNN-` — a patch whose context does
   not match is only a `[warning] Skipping patch`, and the build still succeeds.
2. **Payload** — `tools/mkpayload.py KERNEL shim/out/picasso-clean.adt OUT
   --cmdline "…"` resolves the chained fixups and writes the `SWEETXNU`
   container.
3. **Boot image** — `shim/build.sh PAYLOAD --picasso` → `shim/out/shim-boot.img`.
4. **Userland** — `.github/workflows/userland.yml` cross-builds dash and dropbear
   on the runner: `clang --target=arm64-apple-ios14.0 -isysroot <theos/sdks
   iPhoneOS SDK>`, `ld64.lld` with `-adhoc_codesign`. Artifact `userland-arm64`.
   `tools/userland/picasso_pw.h` is force-included: on iOS 16 `getpwnam()` goes
   through `opendirectoryd`, which does not run here, so it synthesises the root
   account (public-key auth only — there is no shadow database to check).

The boot line used for this work:

```
debug=0x14e serial=3 cpus=8 keepsyms=1 -noprogress rd=ufsroot0
unentitled_ios_sim_launch=1 ignition_level=1 wdt_test=no_user_checkin
ufsrw=1 rootrw=1 ufsroot=darwin launchd_unsecure_cache=1 wdt=-1
sweetfb_noconsole=0 sweetfb_hz=30 sweetfb_keepon=1 sweettouch=0
sweetusb=2 sweetusblog=0 sweetusbsmmu=1 sweetusbprobe=1 sweetscan=-
sweetvfsprobe=0 cs_enforcement_disable=1 amfi_get_out_of_my_way=1
```

## Flashing

| Situation | Command |
|---|---|
| Recovery, local | `adb push shim/out/shim-boot.img /tmp/sb.img` + `adb shell "dd if=/tmp/sb.img of=/dev/block/bootdevice/by-name/boot bs=4M; sync"` |
| Verify | `adb shell "dd if=/dev/block/bootdevice/by-name/boot bs=4096 count=8921 2>/dev/null \| md5sum"` **on the device** — `adb exec-out` of 36 MB is not byte-exact |
| Bootloader | `fastboot flash boot shim/out/shim-boot.img` |
| Phone on another host | `tools/picasso-boot-remote.sh KERNEL "…"`, `tools/picasso-fastboot-remote.sh` |
| Files into the rootfs | `tools/hfsplus-tool.py IMAGE create FOLDER NAME=LOCAL …` then `tools/hfsplus-ship.py IMAGE darwin START:COUNT …` |

Flashing a *new kernel* always needs a reboot into recovery or the bootloader. Every
other step — reading logs, poking the rootfs, running the shell — does not.

## Debugging, in order of preference

| What | How | Needs recovery? |
|---|---|---|
| Kernel state + rings | `tools/picasso-ecm.py up` then `l2 info`, `l2 log`, `l2 logback 200000`, `l2 ringpos` | no |
| Driver internals | `SWEET USBSTATE` lines in the ring (kick thread reports them every ~10 s) | no |
| Userland shell | UDP `192.168.7.2:4444` — `ls`, `cat`, `w`, `cp`, `mkdir`, `reboot` | no |
| Installing a binary | `tools/sweetsh-push.py IMAGE /target` (chunked `w`), or catalog surgery | no |
| Ring after a wedged boot | `tools/sweet-log-pull.sh -p darwin out.txt` | yes |
| Extra host interface address | `tools/picasso-ecm.py up` (re-applies `192.168.7.1/24` — the netdev is re-created on every re-enumeration) | no |

**Do not** issue `stat`/`cat`/`put` over the kernel channel casually: a command
that blocks in VFS used to take the whole channel down with it (patches `0151`
and `0152` make it survivable). The measured, reliable subset is the log and
state commands.

## The USB work, in one paragraph

Device mode on this SoC needs the apps-SMMU stream for SID `0xE0` attached to a
stage-1 identity context bank (patch `0123`). The DWC3 event ring here advances
**one dword per event** — the following dwords read as zero, so parameters must be
peeked, never consumed (`0141`). The single RX TRB must be armed only when the
controller does not own it (`0143`), a bus reset wipes the endpoint configuration
and must drop `ud.configured` so the next `SET_CONFIGURATION` rebuilds it (`0146`),
and the event thread can disappear entirely — so the kick thread (which keeps
printing through every wedge) drains the ring (`0147`/`0148`), taking its liveness
signal from the event thread's own loop counter (`0150`, the fix that made the
channel answer 379/379 over 180 s), and repairs the TX TRB (`0152`).

## Repository layout

| Path | Contents |
|---|---|
| `xnu/patches/` | the port, as ordered patches against Apple's xnu 8796.141.3 |
| `shim/` | bare-metal loader: DT fixups, payload copy, boot.img packer |
| `tools/` | build, package, flash, HFS+, USB/ECM and log tooling |
| `tools/sweetsh/` | the freestanding arm64 shell: `sweetsh.c`, `build.sh`, `mkmacho.py`, `elfextent.py` |
| `tools/userland/` | headers force-included when cross-building dropbear |
| `.github/workflows/` | `xnu.yml` (kernel), `userland.yml` (dash + dropbear) |
| `logs/` | rings pulled from the device, one set per boot label |
| `NOTES.md` | engineering log: measurements, regressions, current blocker |

## Writing a static Mach-O the kernel accepts

Each of these cost a boot and is enforced by `tools/sweetsh/mkmacho.py`:

* the entry stub aligns `sp` — XNU hands a static binary a stack aligned to 8;
* the single segment must be **rwx**; a store into a global of an r-x segment is
  SIGBUS at `far = base + filesize`;
* `vmsize` must cover `.bss` (`elfextent.py --memsize`), or the first store past
  the file bytes faults;
* the CodeDirectory must use this kernel's 16 KiB page shift, be at least
  `sizeof(CS_CodeDirectory)` = 108 bytes, and the signature blob must start on a
  16 KiB boundary; a blob that fails validation is dropped, the kernel then
  removes execute permission and the first instruction is SIGILL;
* a valid signature also earns `CS_PLATFORM_BINARY`, because the port's stub AMFI
  marks any signed rootfs binary as a platform binary.

## Next steps

1. **Fix this port's VFS/UFS write path - it blocks everything else.** Creating a
   file from the kernel (`put`, patch `0153`) hangs on the first `vnode_open`, and
   the same stall ages the ring flusher to 90 s (`fage` in the `SWEET HB` lines).
   Until it is understood, no userland can be installed, by any route: offline
   catalog surgery is undone by journal replay at mount, disabling the journal makes
   HFS+ hang in lookup, and VFS creation hangs outright. Everything else (shell,
   dash, dropbear, plist content, install tooling) is built and waiting.
2. Check the catalog records we write against Apple's own (permissions block,
   `textEncoding`, thread record), so XNU lists the files we create.
3. Start `dropbear` in place of `sweetsh` once the LaunchDaemon actually runs, with
   host keys made on the device by `dropbearkey` and an `authorized_keys`.
4. rmtfs freeze, then `tftp`/`wlan_pd`/WLFW, then the WLAN host driver — see the
   modem sections in `NOTES.md`.
