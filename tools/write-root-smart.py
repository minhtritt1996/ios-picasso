#!/usr/bin/env python3
import hashlib, os, subprocess, sys, time

IMG = '/home/tritm/ios-work/root/ios16-root-picasso.img'
FIRST_BLOCK = 25701120
CHUNK_MB = 512
BS = 4096

CHUNK_BLOCKS = (CHUNK_MB * 1024 * 1024) // BS
total_size = os.path.getsize(IMG)
total_blocks = total_size // BS
num_chunks = (total_blocks + CHUNK_BLOCKS - 1) // CHUNK_BLOCKS

env = dict(os.environ)
env['ADB_SERVER_SOCKET'] = 'tcp:localhost:5038'

def adb_cmd(cmd):
    return subprocess.run(['adb', '-s', 'f9f3d7b0'] + cmd, env=env, capture_output=True)

print(f"Total image size: {total_size / (1024*1024):.0f} MB in {num_chunks} chunks of {CHUNK_MB} MB")

with open(IMG, 'rb') as f:
    for c in range(num_chunks):
        blk_offset = c * CHUNK_BLOCKS
        count = min(CHUNK_BLOCKS, total_blocks - blk_offset)
        raw = f.read(count * BS)
        want_md5 = hashlib.md5(raw).hexdigest()

        # Check MD5 on phone
        dev_blk = FIRST_BLOCK + blk_offset
        check_cmd = f"dd if=/dev/block/sda bs={BS} skip={dev_blk} count={count} 2>/dev/null | md5sum"
        r = subprocess.run(['adb', '-s', 'f9f3d7b0', 'shell', check_cmd], env=env, capture_output=True, text=True)
        phone_md5 = r.stdout.split()[0] if r.stdout else ''

        if phone_md5 == want_md5:
            print(f"[{c+1}/{num_chunks}] {c*CHUNK_MB}MB: MATCH ({want_md5}) -> skip")
            continue

        print(f"[{c+1}/{num_chunks}] {c*CHUNK_MB}MB: DIFF (want {want_md5[:8]}, phone {phone_md5[:8]}) -> WRITING...")
        success = False
        for attempt in range(1, 4):
            write_cmd = f"dd of=/dev/block/sda bs={BS} seek={dev_blk} count={count} conv=notrunc,fsync 2>/dev/null"
            proc = subprocess.Popen(['adb', '-s', 'f9f3d7b0', 'shell', write_cmd], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            proc.communicate(input=raw)

            # Verify
            r = subprocess.run(['adb', '-s', 'f9f3d7b0', 'shell', check_cmd], env=env, capture_output=True, text=True)
            verify_md5 = r.stdout.split()[0] if r.stdout else ''
            if verify_md5 == want_md5:
                print(f"  Verified attempt {attempt}: OK ({verify_md5})")
                success = True
                break
            print(f"  Attempt {attempt} verify failed: got {verify_md5}, want {want_md5}")
            time.sleep(2)

        if not success:
            print(f"ERROR: Failed writing chunk {c}!")
            sys.exit(1)

print("=== ALL CHUNKS SYNCED SUCCESSFULLY ===")
