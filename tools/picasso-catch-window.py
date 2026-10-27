#!/usr/bin/env python3
"""Catch the carrier's short window and leave dropbear running.

SpringBoard's shell is killed by watchdogd about a minute after it starts (it never
checks in), but dropbear daemonises and is not watched, so starting it inside that
window leaves SSH up for good. Polls fast, and on the first reply runs dropbearkey and
dropbear through the shell, then tries an ssh login.

  tools/picasso-catch-window.py [--seconds 900] [--key ~/.ssh/picasso_key]
"""
import argparse
import os
import socket
import subprocess
import sys
import time

PHONE = ("192.168.7.2", 4444)
HOSTKEY = "/private/var/root/dropbear_ed25519_host_key"


def ecm_up():
    subprocess.run(["python3", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "picasso-ecm.py"), "up"],
                   capture_output=True)


def ask(cmd, timeout=1.5):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(cmd.encode(), PHONE)
        data, _ = s.recvfrom(4096)
        return data.decode("latin-1")
    except OSError:
        return None
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=900)
    ap.add_argument("--key", default=os.path.expanduser("~/.ssh/picasso_key"))
    args = ap.parse_args()

    ecm_up()
    end = time.time() + args.seconds
    seen = False
    i = 0
    while time.time() < end:
        i += 1
        if i % 10 == 0:
            ecm_up()          # the gadget re-enumerates on every boot
        if ask("help", 1.0) is not None:
            seen = True
            print("window open at %s" % time.strftime("%T"), flush=True)
            break
        time.sleep(1.0)
    if not seen:
        print("never saw the shell")
        return 1

    # Every command has to land before watchdogd fires: keep each wait short and
    # do not stop on a single failure.
    steps = [
        "/private/var/root/dropbearkey -t ed25519 -f " + HOSTKEY,
        "/private/var/root/dropbear -r " + HOSTKEY + " -p 22",
    ]
    for cmd in steps:
        for _ in range(3):
            out = ask(cmd, 6.0)
            print("$ %s\n  %s" % (cmd, (out or "[no reply]").replace("\n", " ")[:160]),
                  flush=True)
            if out is not None:
                break
        time.sleep(0.5)

    print("--- ssh ---", flush=True)
    end = time.time() + 90
    while time.time() < end:
        r = subprocess.run(["ssh", "-o", "StrictHostKeyChecking=no",
                            "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=5",
                            "-i", args.key, "root@192.168.7.2",
                            "id; uname -a"], capture_output=True, text=True)
        if r.returncode == 0:
            print(r.stdout.strip())
            print("SSH WORKS")
            return 0
        time.sleep(3)
    print("ssh did not come up: %s" % (r.stderr.strip()[:200] if r.stderr else "?"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
