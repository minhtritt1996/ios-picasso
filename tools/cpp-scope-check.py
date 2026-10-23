#!/usr/bin/env python3
"""Catch the cheap structural mistakes in a C/C++ source without a compiler.

It strips comments and string/char literals, tracks brace depth, and reports:
  * a function definition header seen at depth > 0 (the "function definition
    is not allowed here" error, which is what a bad text insertion produces),
  * unbalanced braces at EOF,
  * a declaration that names a function and is followed by "{" but sits
    inside another function.

Usage: cpp-scope-check.py FILE...
Exit status 1 if anything was reported.
"""
import re
import sys


def strip(src):
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                if src[i] == "\n":
                    out.append("\n")
                i += 1
            i += 2
        elif c in "\"'":
            quote = c
            out.append(" ")
            i += 1
            while i < n and src[i] != quote:
                if src[i] == "\\":
                    i += 1
                if src[i] == "\n":
                    break
                i += 1
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


# A definition header: type-ish words, then name(args), optionally "static".
DEF = re.compile(r"^\s*(?:static\s+)?(?:inline\s+|extern\s+\"C\"\s+)?"
                 r"(?:[A-Za-z_][\w:<>,\s\*&]*?)\b([A-Za-z_]\w*)\s*\([^;]*\)\s*$")

# A header that carries no return type on its own line (the usual style here:
# "static uint64_t" then "name(args)"), plus the single-line form above.
BARE = re.compile(r"^\s*([A-Za-z_]\w*)\s*\([^;]*\)\s*$")

SKIP = {"if", "for", "while", "switch", "return", "sizeof", "catch", "else"}


def check(path):
    src = strip(open(path, errors="replace").read())
    lines = src.splitlines()
    depth = 0
    problems = []
    for no, line in enumerate(lines, 1):
        m = DEF.match(line) or BARE.match(line)
        if m and m.group(1) not in SKIP and depth > 0:
            nxt = ""
            for k in range(no, min(no + 3, len(lines))):
                if lines[k].strip():
                    nxt = lines[k].strip()
                    break
            if nxt.startswith("{"):
                problems.append((no, "function definition at depth %d: %s"
                                 % (depth, line.strip()[:70])))
        depth += line.count("{") - line.count("}")
        if depth < 0:
            problems.append((no, "brace depth went negative"))
            depth = 0
    if depth != 0:
        problems.append((len(lines), "unbalanced braces, final depth %d" % depth))
    return problems


def main():
    bad = 0
    for path in sys.argv[1:]:
        problems = check(path)
        for no, msg in problems:
            print("%s:%d: %s" % (path, no, msg))
        bad += len(problems)
        if not problems:
            print("%s: ok" % path)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
