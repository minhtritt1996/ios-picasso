# Pulled boot rings (picasso / SM7250 XNU port)

Every file is the kernel's 4 MiB log ring (patch 0047, `sweet_log_append`) read
back from the `darwin` partition with `tools/sweet-log-pull.sh`, then cut to the
amount the header says is valid:

```
dd if=/dev/block/sda35 of=/data/local/tmp/rNN.bin bs=4096 skip=5242106 count=1025
```

The first 4 KiB block holds `struct sweet_log_header` (magic `SWEETLOG`,
`total` = bytes written this boot, `seq`).  Parsing it matters: the file keeps
the *previous* boot's bytes past `total`, so reading to the last non-zero byte
mixes two boots together and makes a bounce-path boot look like a direct-DMA
one.

`*.txt.gz` = the valid bytes, gzipped (the raw 4 MiB dumps stay out of git).
`cmdline-bpNN.txt` = the exact boot-args the payload was packaged with.

| version | kernel | what it reached | result |
|---|---|---|---|
| bp29 | 0108 | direct-DMA strategy | strategy wedged inside `ufs_lock`, slot 0 latched (`ss 6`, `lk 4/1`, `sd 0`) |
| bp30 | 0109 | direct-DMA + heartbeat | same wedge, heartbeat shows the command never started (`sdt` = cmd age) |
| bp31 | 0111 (0110 bootstrap: bounce path default) | bounce path only (`dma 0/0`), `libignition: … ignition sequence complete`, launchd started | panic `__kmem_invalid_object_panic` right after `shared_region … check_np … failed` |
| bp32 | 0112 (`debug=0x146`, panic through `_cnputs`) | same stage | same panic; the ring stops mid-read, so the panic flush does not reach the disk from inside the UFS path |

Facts measured from these rings:

- UFS reads and writes complete (`ocs 0` / `ocs 15`, `done` = requested length)
  on the bounce path; the DMA stall of bp29/bp30 is gone.
- `PicassoModem: rmtfs: chosen dram ac000000+50000000` - xnu owns
  0xac000000..0xfc000000, above every picasso carve-out (smem 0x80900000,
  removed 0x80b00000..0x9bb00000, pil_wlan_fw 0x8ba00000, modem_wlan
  0x8c000000..0x9b800000).
- `debug_log_init: Error!! gPanicBase is still not initialized` - this port has
  no panic-log region, so panics never land in the panic zone; the ring is the
  only persistent channel.
- The remaining blocker is the `zone_require` panic during launchd start-up,
  i.e. memory corruption or a genuine userspace UAF, not the UFS path.
