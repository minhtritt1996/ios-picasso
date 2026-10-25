# Building the sweet/picasso XNU kernel on Linux (no macOS runner)

Everything below is measured on this workstation (Ubuntu 22.04, gcc/clang x86_64,
no root) against CI run 46 of `xnu.yml` (`/tmp/log46.txt`) and its
`xnu-buildtree` artifact extracted to `/home/tritm/ios/build`.

## What the macOS runner actually provides

Two things cannot be reproduced on Linux:

1. Apple clang's `__builtin_xnu_type_summary` / `__builtin_xnu_type_signature`
   / `__builtin_xnu_types_compatible` frontend builtins (used by
   `kalloc_type`). Replaced by constant macros; they only name kalloc_type
   zones and back DEBUG-only static asserts.
2. Xcode's `ld64`. Replaced by cctools-port `ld64-956.6` built from source
   (needs libdispatch + libBlocksRuntime stubs to build on Linux).

## Toolchain that works

```
clang   /home/tritm/toolchain/swift-5.10-RELEASE-ubuntu22.04/usr/bin/clang
ld      /home/tritm/toolchain/cctools/bin/ld        (wrapper: sets LD_LIBRARY_PATH)
ld64    /home/tritm/toolchain/cctools/bin/ld64      (cctools-port ld64-956.6)
libs    /home/tritm/toolchain/cctools/lib/{libcc_kext.a,libfirehose_kernel.a,
                                          libdispatch.so,libBlocksRuntime.so}
```

`ld64` is built at `/tmp/cctools-port` and has RUNPATH `/tmp/br`; the wrapper
`toolchain/cctools/bin/ld` exports `LD_LIBRARY_PATH=/home/tritm/toolchain/cctools/lib`
so it keeps working after `/tmp` is cleaned.

## The driver: `tools/xnu-local.py`

```
tools/xnu-local.py LOG TREE [--jobs N] [--objects] [--link] [--force]
```

* `LOG`  = `gh run view <id> --log` of a run whose make echoed every compile.
* `TREE` = extracted `xnu-buildtree` artifact (`xnu/`, `build/xnu.obj/`,
  `fakeroot/`). Patches go into `TREE/xnu`; the tree is used in place.

Object caching is timestamp based: only sources newer than their `.o` rebuild,
so a one-file kernel patch is a few seconds.

### Log-parsing rules learned the hard way

* A compile line starts with the compiler path right after the log prefix
  (`...Z /Applications/.../clang ...`). Diagnostics that merely mention
  `clang` must not match, so the regex is anchored on `^`/`Z `.
* The same source can appear more than once (a real compile and a wrapper
  line); keep the parse with the most tokens.
* Sources are found by extension (`-o` is not always present, and the source
  is normally the *last* token).
* Paths in the log are relative to different working directories
  (`build/xnu.obj/DEVELOPMENT_ARM64_SWEET/<component>/DEVELOPMENT`), and
  generated sources (`MIG` output, `*.iig.cpp`, `version.c`) are compiled with
  that object directory as cwd. They are resolved against
  `<obj>/<component>/DEVELOPMENT`, then `<obj>/<component>`, then
  `<obj>/osfmk/DEVELOPMENT`.
* `-I.` in the CI refers to the component object directory, which holds
  `meta_features.h` and the generated per-component headers; locally we add
  `-I<obj>/<component>` and `-I<obj>/<component>/DEVELOPMENT`.
* Host tools under `SETUP/` (config, kextsymboltool, parser.c, lexer.yy.c) are
  dropped: their output is already in the artifact.
* `-arch arm64` becomes `-target arm64-apple-macos14.0`; `-ivfsstatcache`,
  `-fdebug-compilation-dir`, `-x <lang>` and the SDK platform path
  (`.../MacOSX.sdk/System/Library/Frameworks/Kernel.framework/...` →
  `TREE/sdk-kernel-platform/platform`) are dropped/rewritten.
* `arm64_isa_compatibility.h` picks the modern `v0.4s` crypto syntax only when
  `__apple_build_version__` is defined; our clang needs it too, otherwise
  `sha256_compress_arm64.s` fails with "invalid operand for instruction" on
  `sha256su0 q0, q1`. The define is added for `.s`/`.S` compiles only.

### Linking

* The CI pipes a component-ordered object list (`link.filelist`) into clang++,
  plus `version.o lastkerneldataconst.o lastkernelconstructor.o nonlto.o`, to
  produce `kernel.development.sweet.unstripped`.
* Object names are flattened to `<component>/DEVELOPMENT/<basename>.o`, so the
  local object list is rebuilt by `(component, basename)` -- no collisions
  exist in this tree.
* All `-Wl,` options are unpacked for `ld` directly; `-nostdlib`, `-fapple-kext`,
  `-mmacosx-version-min`, `-DXNU_TARGET_OS_OSX` and `-lcc_kext` are dropped;
  `-F/System/...` is dropped; `-L.../fakeroot/usr/local/lib/kernel` is kept but
  empty (the stub archives live in the toolchain lib dir).
* `libfirehose_kernel.a` / `libcc_kext.a` are empty archives; `ar`-created
  8-byte archives are rejected by ld64 ("file too small ... for architecture
  arm64"), so they must contain at least one member or be dropped.

## Status (2026-10-25)

* 1093 of the kernel's objects compile locally from run 46's log (0 failures).
* 139 objects of that link list have **no compile line in the log**: the CI
  tree is incremental, so those were built in an earlier run. They must be
  recovered either from an older clean-run log or compiled with the flag set of
  a sibling in the same source directory.
* `ld` still needs the empty `libfirehose_kernel.a` question resolved (real
  member or drop the `-l` flags) before the link completes.
