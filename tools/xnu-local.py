#!/usr/bin/env python3
"""Build the sweet/picasso XNU kernel on Linux, from a CI log and build tree.

The macOS runner is only needed for two things we cannot reproduce here:
Apple's `__builtin_xnu_type_*` frontend builtins and Xcode's ld64. Both are
replaced: the builtins by macros (the type summary only names kalloc_type
zones and backs DEBUG-only static asserts), ld64 by cctools-port's ld 956.

  xnu-local.py LOG TREE [--jobs N] [--objects] [--link] [--file SRC ...]

LOG   `gh run view <id> --log` output of an xnu.yml run with verbose make
TREE  extracted xnu-buildtree artifact (xnu/, build/xnu.obj/, fakeroot/, ...)
      The tree is edited in place, so patches go straight into TREE/xnu.

Objects are cached by timestamp: only sources newer than their .o are
recompiled, which is what makes a one-file kernel change local work.

Env: XNU_LOCAL_CLANG  compiler (default: the Swift toolchain's clang fork)
     XNU_LOCAL_LD     linker (default: cctools-port ld64)
     XNU_LOCAL_JOBS   default job count
"""
import argparse
import concurrent.futures as cf
import os
import re
import shlex
import subprocess
import sys

CLANG = os.environ.get(
    'XNU_LOCAL_CLANG',
    '/home/tritm/toolchain/swift-5.10-RELEASE-ubuntu22.04/usr/bin/clang')
LD = os.environ.get('XNU_LOCAL_LD', '/home/tritm/toolchain/cctools/bin/ld')
LIBDIR = os.environ.get('XNU_LOCAL_LIBDIR', '/home/tritm/toolchain/cctools/lib')

# Apple-clang-only type builtins. A constant summary keeps every kalloc_type
# site consistent within one build; it only changes zone naming and the
# DEBUG-only layout asserts, never the allocation itself.
STUBS = [
    '-D__builtin_xnu_type_summary(x)=0',
    '-D__builtin_xnu_type_signature(x)=""',
    '-D__builtin_xnu_types_compatible(a,b)=1',
    '-Wno-builtin-macro-redefined',
]

CI_ROOT = re.compile(r'/Users/runner/work/[^/]+/[^/]+/dxb')
SDK_PLATFORM = re.compile(
    r'/Applications/Xcode[^ ]*?Kernel\.framework/Versions/A/PrivateHeaders/platform')
# One macOS-side compile: compiler, the -c source, and the -o object after it.
# The compiler path is first on the line (the log prefix is "job<TAB>step<TAB>time"),
# so a diagnostic line that merely mentions clang cannot match.
COMPILE = re.compile(r'(?:^|Z ) */(?P<cc>\S*/clang(?:\+\+)?) (?P<rest>[^\n]*)')
KERNEL = 'kernel.development.sweet'


def parse_log(path, tree):
    """Every macOS compile of a tree source, rewritten to run locally.

    The CI log carries the compiler first and no -o of its own (the Makefile
    writes <basename>.o into the directory it compiles from, which also holds
    the generated per-component headers). Objects therefore go to
    build/local/obj under the source's relative path, and the CI object
    directory stays on the include path.
    """
    seen = {}
    for line in open(path, errors='replace'):
        m = COMPILE.search(line)
        if not m:
            continue
        try:
            toks = shlex.split(m.group('rest').replace('\\=', '='))
        except ValueError:
            continue
        src_i = None
        for i, t in enumerate(toks):
            if t.endswith(('.c', '.cpp', '.s', '.S', '.cc')) and not t.startswith('-'):
                src_i = i
                break
        if src_i is None:
            continue
        src_tok = toks[src_i]
        if (src_tok.startswith('/') and '/xnu/' not in src_tok
                and '/xnu.obj/' not in src_tok):
            continue
        src = toks[src_i]
        args = toks[:src_i] + toks[src_i + 1:]
        # drop the compiler's own -o, -c and dep-file flags: the object name
        # is ours to choose and every compile runs in its own directory
        clean, i = [], 0
        while i < len(args):
            t = args[i]
            if t in ('-c', '-MD', '-MMD'):
                i += 1
                continue
            if t in ('-o', '-MF', '-MT'):
                i += 2
                continue
            clean.append(t)
            i += 1
        key = src
        prev = seen.get(key)
        if prev is None or len(clean) > len(prev[1]):
            seen[key] = (m.group('cc').endswith('++'), clean, src)
    out = []
    for cxx, toks, src in seen.values():
        fixed = [CLANG]
        i = 0
        while i < len(toks):
            t = toks[i]
            if t == '-ivfsstatcache':
                i += 2
                continue
            if t == '-arch':
                fixed += ['-target', toks[i + 1] + '-apple-macos14.0']
                i += 2
                continue
            if t in ('-x',):
                i += 2
                continue
            t = CI_ROOT.sub(tree, t)
            t = SDK_PLATFORM.sub(tree + '/sdk-kernel-platform/platform', t)
            if t == '-I.':
                continue  # the runner's cwd; the component dirs are added below
            if t and not t.startswith('-fdebug-compilation-dir='):
                fixed.append(t)
            i += 1
        src_l = CI_ROOT.sub(tree, src)
        obj_root = os.path.join(tree, 'build/xnu.obj/DEVELOPMENT_ARM64_SWEET')
        if not os.path.isabs(src_l):
            # generated sources compile with the component's object dir as cwd
            for base in (os.path.join(obj_root, 'iokit/DEVELOPMENT'),
                         os.path.join(obj_root, 'iokit'),
                         os.path.join(obj_root, 'bsd/DEVELOPMENT'),
                         os.path.join(obj_root, 'osfmk/DEVELOPMENT')):
                cand = os.path.join(base, src_l)
                if os.path.exists(cand):
                    src_l = cand
                    break
            else:
                continue
        if src_l.startswith(obj_root):
            # MIG output and other generated sources: their object name is the
            # path below the CI object directory
            rel = os.path.relpath(src_l, obj_root)
            component = rel.split('/')[0]
            cwd = os.path.join(tree, 'build/local/obj', os.path.dirname(rel))
            obj_l = os.path.join(cwd, os.path.splitext(os.path.basename(rel))[0] + '.o')
            gen = os.path.join(obj_root, component)
            if not os.path.isdir(gen):
                gen = obj_root
            out.append((fixed + ['-I' + gen, '-I' + os.path.join(gen, 'DEVELOPMENT')] +
                        STUBS + ['-c', src_l, '-o', obj_l], src_l, obj_l))
            continue
        rel = os.path.relpath(src_l, os.path.join(tree, 'xnu'))
        if rel.startswith('..'):
            rel = 'generated/' + os.path.relpath(src_l, obj_root)
        if rel.split('/')[0] == 'SETUP':
            continue  # host tools; the CI already generated their output
        component = rel.split('/')[0]
        gen = os.path.join(obj_root, component)
        if not os.path.isdir(gen):
            gen = obj_root
        cwd = os.path.join(tree, 'build/local/obj', os.path.dirname(rel))
        obj_l = os.path.join(cwd, os.path.splitext(os.path.basename(rel))[0] + '.o')
        # arm64_isa_compatibility.h picks the modern "v0.4s" crypto syntax
        # only for Apple clang; our clang is new enough to need it too
        asm = ['-D__apple_build_version__=15000100'] if src.endswith(('.s', '.S')) else []
        out.append((fixed + asm + ['-I' + gen, '-I' + os.path.join(gen, 'DEVELOPMENT')] +
                    STUBS + ['-c', src_l, '-o', obj_l], src_l, obj_l))
    return out


def makefile_sources(tree):
    """Every source the generated per-component Makefiles build.

    The CI log is not enough on its own: GitHub splits very long (C++) command
    lines and parallel make interleaves them, so some commands cannot be
    recovered (idea and code shape from ry-diffusion/Bergamota tools/xnu-cc.py).
    Returns absolute source paths, with the object directory of the component
    that builds them (cwd for the compile).
    """
    obj_root = os.path.join(tree, 'build/xnu.obj')
    out = []
    for root, _, files in os.walk(obj_root):
        if 'Makefile' not in files or 'meta_features.h' not in files:
            continue
        text = open(os.path.join(root, 'Makefile'), errors='replace').read()
        text = text.replace('\\\n', ' ')
        for var in ('CFILES', 'CXXFILES', 'SFILES'):
            m = re.search(r'^%s=(.*)$' % var, text, re.M)
            if not m:
                continue
            for f in m.group(1).split():
                f = f.replace('$(SOURCE_DIR)', os.path.join(tree, 'xnu'))
                if f.startswith('./'):
                    f = os.path.join(root, f[2:])
                out.append((f, root))
    return out


def complete_from_makefiles(tree, jobs):
    """Add a compile command for every source the Makefiles list but the log
    does not, borrowing the flags of a sibling of the same component and
    language (the per-component flag set is uniform in this tree)."""
    obj_root = os.path.join(tree, 'build/xnu.obj')
    have = {os.path.splitext(os.path.basename(j[1]))[0] + '|' + j[1] for j in jobs}
    by_src = {j[1] for j in jobs}
    templates = {}
    for args, src, obj in jobs:
        rel = os.path.relpath(src, os.path.join(tree, 'xnu'))
        comp = rel.split('/')[0] if '/' in rel else ''
        templates.setdefault((comp, os.path.splitext(src)[1]), (args, src, obj))
    added = 0
    for src, cwd in makefile_sources(tree):
        if src in by_src or not os.path.exists(src):
            continue
        if src.startswith(obj_root):
            # generated inside the CI object tree: component is the first
            # path element below it (never '..', which would pick iokit)
            comp = os.path.relpath(src, obj_root).split('/')[0]
            rel = os.path.relpath(src, obj_root)
        else:
            rel = os.path.relpath(src, os.path.join(tree, 'xnu'))
            comp = rel.split('/')[0] if '/' in rel else ''
        tpl = templates.get((comp, os.path.splitext(src)[1]))
        if tpl is None:
            continue
        args, tsrc, tobj = tpl
        args = list(args)
        args[args.index(tsrc)] = src
        args = ['-I' + cwd if a == '-I.' else a for a in args]
        obj_l = os.path.join(tree, 'build/local/obj',
                            os.path.splitext(rel)[0] + '.o')
        args[args.index(tobj)] = obj_l
        jobs.append((args, src, obj_l))
        by_src.add(src)
        added += 1
    return added


def run(todo, jobs, cwd):
    done, failed = 0, []
    os.makedirs(cwd, exist_ok=True)

    def one(job):
        args, src, obj = job
        os.makedirs(os.path.dirname(obj), exist_ok=True)
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
        return src, obj, r

    with cf.ThreadPoolExecutor(max_workers=jobs) as pool:
        for src, obj, r in pool.map(one, todo):
            if r.returncode != 0:
                errs = [l for l in r.stderr.split('\n') if ': error' in l or 'fatal' in l]
                failed.append((src, errs[:4] or r.stderr.split('\n')[:4]))
            else:
                done += 1
    return done, failed


def link(tree, log):
    """Recreate the CI kernel link with ld64 from cctools-port.

    The CI pipes a component-ordered object file list to clang++ together with
    four objects it builds in the object directory itself (version.o and the
    libsa last-* objects). Names here come from our own objects; ld runs from
    the object directory just like the runner.
    """
    objdir = os.path.join(tree, 'build/xnu.obj/DEVELOPMENT_ARM64_SWEET')
    jobs = parse_log(log, tree)
    complete_from_makefiles(tree, jobs)
    by_rel = {os.path.relpath(o, os.path.join(tree, 'build/local/obj')): o
              for _, _, o in jobs}
    by_name = {}
    for _, _, o in jobs:
        by_name.setdefault(os.path.basename(o), []).append(o)

    # the CI flattens the objects of a component into <component>/DEVELOPMENT,
    # so the object name is (component, basename) -- the relative path only
    # disambiguates nothing that is not already unique
    by_key = {}
    for rel, obj in by_rel.items():
        comp = rel.split('/')[0] if '/' in rel else ''
        by_key[(comp, os.path.basename(rel))] = obj

    def resolve(entry):
        rel = os.path.relpath(CI_ROOT.sub(tree, entry), objdir)
        obj = by_rel.get(rel)
        if obj:
            return obj
        comp = rel.split('/')[0] if '/' in rel else ''
        obj = by_key.get((comp, os.path.basename(rel)))
        if obj:
            return obj
        cand = by_name.get(os.path.basename(rel), [])
        return cand[0] if len(cand) == 1 else None

    wanted, missing = [], []
    with open(os.path.join(objdir, 'link.filelist')) as f:
        for entry in f:
            entry = entry.strip()
            if not entry:
                continue
            obj = resolve(entry)
            (wanted if obj else missing).append(obj or os.path.basename(entry))
    if missing:
        print(f'warning: {len(missing)} objects not built locally: {missing[:4]}')
    for extra in ('version.o', 'lastkerneldataconst.o',
                  'lastkernelconstructor.o', 'nonlto.o'):
        obj = resolve(extra)
        if obj:
            wanted.append(obj)
        else:
            print('warning: no local ' + extra)
    listfile = os.path.join(objdir, 'link.local.filelist')
    with open(listfile, 'w') as f:
        f.write(''.join(o + '\n' for o in wanted))

    args = None
    with open(log) as f:
        for line in f:
            if ' -filelist link.filelist ' in line:
                args = line.split('Z ', 1)[-1].strip().replace('\\=', '=')
                break
    if args is None:
        sys.exit('link command not found in the log')

    toks = shlex.split(args)
    fixed, i = [], 0
    while i < len(toks):
        t = toks[i]
        if t.endswith('/clang++') or t.endswith('/clang'):
            fixed.append(LD)
            i += 1
            continue
        if t in ('-nostdlib', '-fapple-kext', '-mmacosx-version-min=14.0',
                 '-DXNU_TARGET_OS_OSX', '-lcc_kext'):
            i += 1
            continue
        if t == '-filelist':
            fixed += ['-filelist', listfile]
            i += 2
            continue
        if t.startswith('-Wl,'):
            fixed += [CI_ROOT.sub(tree, x) for x in t[4:].split(',')]
            i += 1
            continue
        if t.startswith('-L'):
            fixed += ['-L' + CI_ROOT.sub(tree, t[2:])]
            i += 1
            continue
        if t.endswith('.o') and not t.startswith('/'):
            i += 1  # already in the file list
            continue
        if t.startswith('-F/System/'):
            i += 1
            continue
        t = CI_ROOT.sub(tree, t)
        if t.endswith('kernel.development.sweet.unstripped'):
            t = os.path.join(os.path.dirname(t), KERNEL + '.local.unstripped')
        fixed.append(t)
        i += 1
    fixed += ['-L' + LIBDIR]
    r = subprocess.run(fixed, cwd=objdir, capture_output=True, text=True)
    if r.returncode != 0:
        errs = [l for l in r.stderr.split('\n')
                if 'Undefined' in l or 'error' in l or 'warning' in l and 'filelist' in l]
        sys.exit('link failed:\n' + '\n'.join(errs[:40] or r.stderr.split(chr(10))[:40]))
    out = os.path.join(objdir, KERNEL + '.local.unstripped')
    if not os.path.exists(out):
        sys.exit('ld produced no output')
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('log')
    ap.add_argument('tree')
    ap.add_argument('--jobs', type=int,
                    default=int(os.environ.get('XNU_LOCAL_JOBS', os.cpu_count() or 4)))
    ap.add_argument('--objects', action='store_true')
    ap.add_argument('--link', action='store_true')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--file', action='append', metavar='SRC',
                    help='compile SRC (relative to the tree) with the flags of a '
                         'sibling of the same component, even if the log has no line')
    a = ap.parse_args()
    tree = os.path.abspath(a.tree)
    if not (a.objects or a.link):
        a.objects = a.link = True

    if a.objects:
        jobs = parse_log(a.log, tree)
    for extra in a.file or []:
        src = os.path.join(tree, 'xnu', extra)
        args, tsrc, tobj = next(j for j in jobs
                               if os.path.dirname(j[1]) == os.path.dirname(src)
                               and os.path.splitext(j[1])[1] == os.path.splitext(src)[1])
        args = list(args)
        args[args.index(tsrc)] = src
        obj_l = os.path.join(tree, 'build/local/obj',
                            os.path.splitext(extra)[0] + '.o')
        args[args.index(tobj)] = obj_l
        jobs = [j for j in jobs if j[1] != src] + [(args, src, obj_l)]
        n = complete_from_makefiles(tree, jobs)
        if n:
            print(f'{n} sources recovered from the component Makefiles')
        todo = [(x, s, o) for x, s, o in jobs
                if a.force or not os.path.exists(o) or os.path.getmtime(o) < os.path.getmtime(s)]
        print(f'{len(jobs)} compile commands, {len(todo)} to build '
              f'({len(jobs) - len(todo)} cached)')
        done, failed = run(todo, a.jobs, os.path.join(tree, 'build/xnu.obj'))
        print(f'compiled {done}, failed {len(failed)}')
        for src, errs in failed[:6]:
            print(f'--- {src}')
            for e in errs:
                print('   ', e)
        if failed:
            sys.exit(1)
    if a.link:
        print('linked:', link(tree, a.log))


if __name__ == '__main__':
    main()
