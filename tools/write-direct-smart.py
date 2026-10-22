#!/usr/bin/env python3
import hashlib, os, subprocess, sys, time

IMG = '/home/tritm/ios-work/root/ios16-root-picasso.img'
DEVICE = '/dev/block/sda35'
CHUNK_MB = 512
BS = 4096

CHUNK_BLOCKS = (CHUNK_MB * 1024 * 1024) // BS
total_size = os.path.getsize(IMG)
total_blocks = total_size // BS
num_chunks = (total_blocks + CHUNK_BLOCKS - 1) // CHUNK_BLOCKS

DIFF_CHUNKS = [0, 1, 8, 13, 15, 17, 23, 24, 25, 26, 27, 28, 29, 30, 31]

print(f"Direct sync to {DEVICE}: {len(DIFF_CHUNKS)} chunks of {CHUNK_MB} MB to write.")

with open(IMG, 'rb') as f:
    for idx, c in enumerate(DIFF_CHUNKS, 1):
        blk_offset = c * CHUNK_BLOCKS
        count = min(CHUNK_BLOCKS, total_blocks - blk_offset)
        f.seek(blk_offset * BS)
        raw = f.read(count * BS)
        want_md5 = hashlib.md5(raw).hexdigest()

        # Check MD5 on phone first
        check_cmd = f"dd if={DEVICE} bs={BS} skip={blk_offset} count={count} 2>/dev/null | md5sum"
        r = subprocess.run(['adb', '-s', 'f9f3d7b0', 'shell', check_cmd], capture_output=True, text=True)
        phone_md5 = r.stdout.split()[0] if r.stdout else ''

        if phone_md5 == want_md5:
            print(f"[{idx}/{len(DIFF_CHUNKS)}] Chunk {c:2d} ({c*CHUNK_MB:5d}MB): MATCH ({want_md5[:8]}) -> skip")
            continue

        print(f"[{idx}/{len(DIFF_CHUNKS)}] Chunk {c:2d} ({c*CHUNK_MB:5d}MB): WRITING (want {want_md5[:8]}, was {phone_md5[:8]})...", flush=True)
        t0 = time.time()
        success = False
        for attempt in range(1, 4):
            write_cmd = f"dd of={DEVICE} bs={BS} seek={blk_offset} count={count} conv=notrunc,fsync 2>/dev/null"
            proc = subprocess.Popen(['adb', '-s', 'f9f3d7b0', 'shell', write_cmd], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            proc.communicate(input=raw)

            # Verify
            r = subprocess.run(['adb', '-s', 'f9f3d7b0', 'shell', check_cmd], capture_output=True, text=True)
            verify_md5 = r.stdout.split()[0] if r.stdout else ''
            if verify_md5 == want_md5:
                dt = time.time() - t0
                mb_s = (len(raw) / (1024*1024)) / dt if dt > 0 else 0
                print(f"  Verified OK ({verify_md5[:8]}) in {dt:.1f}s ({mb_s:.1f} MB/s)", flush=True)
                success = True
                break
            print(f"  Attempt {attempt} verify failed: got {verify_md5}, want {want_md5}")
            time.sleep(2)

        if not success:
            print(f"ERROR: Failed writing chunk {c}!")
            sys.exit(1)

print("Syncing filesystem buffer on device...")
subprocess.run(['adb', '-s', 'f9f3d7b0', 'shell', 'sync'])
print("=== ALL CHUNKS SYNCED SUCCESSFULLY ===")
