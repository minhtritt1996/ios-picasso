# Working on ios-sweet

- Read `NOTES.md` before starting; it contains the project goal, hardware facts,
  current blocker, local artifact locations and the next concrete action.
- The user switches agents frequently. Update the active handoff in `NOTES.md`
  after each milestone and before stopping. Record commits, CI run IDs, exact
  build/boot results and unfinished work. Distinguish observations from guesses.
- Commit completed changes. Use `gh` for GitHub Actions kernel builds and logs.
  Local single-file cross-compiles are useful before spending a CI run.
- Device bring-up uses temporary `fastboot boot` images. Preserve the Android
  installation and partition backups. The user approved (2026-10-15) using
  internal UFS for iOS and said Android may be deleted: the iOS root goes
  into the existing `userdata` partition (no GPT changes). Keep bootloaders,
  recovery, modem, persist and the backups intact, and confirm each
  destructive step with the user before running it.
- Keep device dumps, serial numbers and downloaded Apple artifacts out of git.

## Hard-won rules for picasso / sweet XNU bring-up (2026-10-27)

These cost a full day. Follow them literally.

1. **Never trust a flash until the payload shows the cmdline.**
   `python3 tools/mkpayload.py KERNEL shim/out/picasso-clean.adt OUT --cmdline "$CL"`
   then `strings -n 6 OUT | grep -E "rd=ufsroot0|sweetusb=2"` MUST match (also
   `sweetusblog=1`, `sweetusbsmmu=1`, `sweetscan=-`). Twice, a build with different
   BOOT_CMDLINE produced a byte-identical `shim-boot.img`, so every "experiment" ran with the
   default cmdline (`debug=0xc serial=3 cpus=1 keepsyms=1`) - no root, no USB gadget. If the
   md5 of the new image equals the old one, the build did not use your payload.
2. **Verify every write by readback**, boot and data alike:
   `dd if=<img> of=.../boot bs=4M; sync; dd if=/dev/block/bootdevice/by-name/boot bs=<size> count=1 | md5sum`
   and for image ranges `dd ... skip=START count=COUNT | md5sum` per range.
3. **The HFS+ volume must not be journaled** before shipping raw blocks (`hfsplus-tool.py IMG
   unjournal`, attrs become 0x80000100). Otherwise the kernel replays the journal and your
   catalog edits silently disappear (`stat` then returns `lookup error 2`).
4. **The kernel ring is not reliable evidence.** Recent boots only updated the header (md5
   changed, content stale). Read the live console over the USB control channel instead:
   `python3 picasso-ecm.py l2 log` (also `info`, `ringpos`, `stat <path>`, `cat`, `put`).
5. **The phone normally hangs off k60** (`root@100.121.254.115`, password `k60`), not off this
   workstation: `sshpass -p k60 ssh root@k60 "adb devices -l"`. k60 has ~30 GB /data - delete
   old artifacts before copying 17 GB images, and prefer streaming/shipping only deltas.
6. **Installing files offline**: `hfsplus-tool.py IMG create FOLDER NAME=LOCAL` (or
   `writefile` for an existing file), then dd each printed range with
   `bs=4096 seek=START count=COUNT` and verify md5 per range. `create` writes mode 0755 for
   binaries. Known bug: a two-character name (`sh`) can be inserted but is not resolvable
   (`lookup error 2`) - key ordering; use longer names.
7. **Userland**: `tools/sweetsh/build.sh` produces `sweetsh-udp-signed` (self-installing PID 1,
   quiet build, forks `dropbear -R -p 22`; never run `dropbearkey`, O_CREAT on HFS+ wedges VFS).
   A LaunchDaemon `/System/Library/LaunchDaemons/000-sweetsh.plist` runs dropbear directly and
   is loaded because we boot with `launchd_unsecure_cache=1`. Booting with launchd as PID 1 and
   any job exiting panics with `initproc failed ... handshake structure not initialized`.
8. **Panic on the first userland syscall - patched by 0174 (built, flashed, untested at boot).**
   launchd loads the daemon, dropbear execs, then the kernel panicked:
   `Taking non-sleepable RW lock with preemption enabled: lock 0xfffffe1b486badf8
    caller 0xfffffe00075177bc lr 0xfffffe000751b754 tid 250 preempt 0 @lock_rw.c:1386`.
   RESOLVED with the symtab of the CI kernel (Mach-O nlist, python):
   `0xfffffe0007340744 = _lck_rw_lock_shared+0x78` (assert site),
   `0xfffffe00075177bc = _lookup_handle_found_vnode+0x1fc`,
   `0xfffffe000751b754 = _vfs_busy+0x50`.
   => `lookup_handle_found_vnode()` calls `vfs_busy()`, which takes
   `lck_rw_lock_shared(&mp->mnt_rwlock)`, but the mount lock was created NON-sleepable, so
   lock_rw.c:1386 asserts "non-sleepable RW lock with preemption enabled".
   Fix: initialise `mnt_rwlock` sleepable (`lck_rw_init(..., LCK_RW_TYPE_NORMAL)`, check
   `vfs_init`/`vfs_mount_alloc`) or make `vfs_busy` use the try/sleepable variant.

9. **The boot image MUST be built with `--picasso`.**
   `(cd shim && ./build.sh ../shim/out/payload-X.bin --picasso)` produces 36,573,184 B
   (md5 changes per payload). Without the flag you get the sweet layout, 18,190,336 B, and the
   phone **hangs at the Xiaomi logo** (built 2026-10-27 23:14 by accident, wasted a flash).
   Sanity gate before every dd: `stat -c %s` in 36.5-36.6 MB, and `strings payload | grep`
   for `rd=ufsroot0 sweetusb=2 sweetscan=-`.

10. **CI lives on the `picasso` remote, not `origin`.** `gh` resolves repos from `origin`
    (dipa-studio/ios-sweet), but the workflows run on `minhtritt1996/ios-picasso`:
    `git push picasso agy`, `gh workflow run xnu.yml --ref agy --repo minhtritt1996/ios-picasso`,
    `export GH_REPO=minhtritt1996/ios-picasso` before `gh api`/`tools/gh-artifact.sh`
    (GH_TOKEN is in `/tmp/ghtoken`; the keyring token is invalid). A dispatch fired before the
    push builds the *old* head - check `headSha` on the new run.

11. **Compile a kernel change locally before spending a CI run.**
    `tar xzf <run>/buildtree.tgz -C /tmp/btX` then
    `python3 tools/xnu-local.py /tmp/log46.txt /tmp/btX --file <src> --file <src>`
    (needs a CI log that contains compile lines). bsd/ code sees the **exported** headers, so
    when a change touches `osfmk/**/*.h` also refresh the copy in
    `build/xnu.obj/EXPORT_HDRS/` (`chmod u+w` first) - otherwise you get "undeclared function".
    Regenerate the patch with
    `diff -u --label a/F --label b/F /tmp/xnu-check/xnu/F build/xnu/F` (pristine = xnu-check)
    and `patch -p1 --dry-run` it there.
