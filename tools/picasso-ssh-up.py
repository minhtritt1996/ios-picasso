#!/usr/bin/env python3
"""Bring SSH up on the phone once the UDP shell answers, and prove it works.

The shell runs as a launchd daemon (SpringBoard's carrier) with root, so it can
start dropbear itself:

  1. dropbearkey -t ed25519 -f /private/var/root/dropbear_ed25519_host_key
  2. dropbear -r .../dropbear_ed25519_host_key -p 22      (daemonises, so the
     shell's exec-and-wait returns immediately)

/private/var/root/.ssh/authorized_keys already holds this workstation's public key
(baked into the image), and the stored dropbear was built with pw_shell
/private/var/root/sh - so the last step is a real ssh login.

  tools/picasso-ssh-up.py [--key ~/.ssh/picasso_key] [--timeout 60]
"""
import argparse
import os
import subprocess
import socket
import sys
import time

PHONE = ("192.168.7.2", 4444)


HERE = os.path.dirname(os.path.abspath(__file__))


def ecm_up():
    subprocess.run(["python3", os.path.join(HERE, "picasso-ecm.py"), "up"],
                   capture_output=True)


def shell(cmd, timeout=8):
    ecm_up()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(cmd.encode(), PHONE)
        data, _ = s.recvfrom(4096)
        return data.decode("latin-1")
    except socket.timeout:
        return None
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=os.path.expanduser("~/.ssh/picasso_key"))
    ap.add_argument("--timeout", type=int, default=60)
    args = ap.parse_args()

    if shell("help") is None:
        print("shell does not answer on %s:%d - nothing to do" % PHONE)
        return 1

    steps = [
        "/private/var/root/dropbearkey -t ed25519 -f /private/var/root/dropbear_ed25519_host_key",
        "ls /private/var/root",
        "/private/var/root/dropbear -r /private/var/root/dropbear_ed25519_host_key -p 22",
    ]
    for cmd in steps:
        out = shell(cmd)
        print("$ %s\n%s" % (cmd, (out or "[no reply]")[:400]))
        if out is None:
            return 1
        time.sleep(1)

    print("--- ssh ---")
    t0 = time.time()
    while time.time() - t0 < args.timeout:
        r = subprocess.run(["ssh", "-o", "StrictHostKeyChecking=no",
                            "-o", "UserKnownHostsFile=/dev/null",
                            "-o", "ConnectTimeout=5", "-i", args.key,
                            "root@192.168.7.2", "id; /private/var/root/sh -c 'echo hello from the phone'"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            print(r.stdout.strip())
            print("SSH WORKS")
            return 0
        time.sleep(3)
    print("ssh did not come up in %ds: %s" % (args.timeout, r.stderr.strip()[:200]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
