#!/usr/bin/env python3
"""
test_mutations.py -- prove test_dispatch.py can fail.

A verification suite that never fails is indistinguishable from no suite at all.
Each mutation below is a plausible mistake; every one MUST be caught.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import importlib, os, shutil, subprocess, sys, tempfile, textwrap

MUTATIONS = [
    # Field-table mutations must be made in the GENERATED sites file, because
    # that is what the build actually consumes. Mutating the source table alone
    # would change the reference model and the cave together, and prove nothing.
    ('field_C offset 0xC -> 0xA in the generated sites (wrong field scaled)',
     'ff7nx_dispatch_sites.py', "'field_C'   : (0x0C, 16, 's')",
     "'field_C'   : (0x0A, 16, 's')"),
    ('n_frames width 16 -> 32 in the generated sites',
     'ff7nx_dispatch_sites.py', "'n_frames'  : (0x04, 16, 's')",
     "'n_frames'  : (0x04, 32, 's')"),
    ('effect10 stride 0x20 -> 0x28 in the generated sites',
     'ff7nx_dispatch_sites.py', "'stride'    : 0x20,", "'stride'    : 0x28,"),
    ('divide loses the toward-zero bias (rounds to -inf)',
     'ff7nx_dispatch.py', 'A.and_mask(16, 16, k),', 'A.and_mask(16, 16, 1) & ~0xFFFFFFFF | A.and_mask(16, 16, k) & 0,'),
    ('multiply shifts by 1 instead of k',
     'ff7nx_dispatch.py', 'return [ld(17, 0, off), A.lsl(17, 17, k), st(17, 0, off)]',
     'return [ld(17, 0, off), A.lsl(17, 17, 1), st(17, 0, off)]'),
    ('first-frame flag never cleared',
     'ff7nx_dispatch.py', 'w.append(A.strb(A.WZR, 17, 0))', 'w.append(A.add_imm(31, 31, 0))'),
    ('add_fn writes flag 0 instead of 1',
     'ff7nx_dispatch.py', 'A.movz(16, 1),', 'A.movz(16, 0),'),
    ('index not masked -> can escape the flag block',
     'ff7nx_dispatch.py', 'w.append(A.and_mask(16, 16, mask_bits))\n    w.append(A.adrp',
     'w.append(A.add_imm(16, 16, 0))\n    w.append(A.adrp'),
    # Both cave builders patch this jump, and they word it differently -- the
    # reference targets pc() at the point the body ends, the compact one
    # targets OUT directly. Mutating on the shared ', A.LE)' tail hits both.
    ('n_frames>1 guard inverted', 'ff7nx_dispatch.py',
     '4 * i, out, A.LE)', '4 * i, out, A.GE)'),
    # The same wrong turn in build_cave_reference. No build uses that
    # function, so test_dispatch.py cannot see it -- but if the reference
    # drifts, the shrink diff is comparing against a lie, so it is
    # test_dispatch_shrink.py that has to notice.
    ('n_frames>1 guard inverted in the shrink reference', 'ff7nx_dispatch.py',
     'guard_i, pc(), A.LE)', 'guard_i, pc(), A.GE)', 'test_dispatch_shrink.py'),
    ('divide loads the field unsigned (ldrh) instead of signed (ldrsh)',
     'ff7nx_dispatch.py', "ld = {16: A.ldrsh, 32: A.ldr}[width]",
     "ld = {16: A.ldrh, 32: A.ldr}[width]"),
    ('divide stores back through the wrong width',
     'ff7nx_dispatch.py', "st = {16: A.strh, 32: A.str_}[width]",
     "st = {16: A.strb, 32: A.str_}[width]"),
    ('cave returns to the hook instead of hook+4 (infinite loop)',
     'ff7nx_dispatch.py', "A.b(pc(), site['hook'] + 4))", "A.b(pc(), site['hook']))"),
    ('fn pointer not preserved across the translator call',
     'ff7nx_dispatch.py', 'w.append(A.ldr64(F, A.SP, 0))', 'w.append(A.add_imm(31, 31, 0))'),
]

def run(path, old, new, script='test_dispatch.py'):
    src = open(path).read()
    n = src.count(old)
    if n == 0:
        return None, 'pattern not found'
    # EVERY occurrence, not just the first. ff7nx_dispatch.py holds two cave
    # builders -- the compact one a build uses and `build_cave_reference`,
    # which exists only so test_dispatch_shrink.py can diff against it. They
    # share instruction-level source lines, and mutating the first occurrence
    # alone would land on the reference, leave the live cave untouched, and
    # report a real mutation as "not caught".
    open(path, 'w').write(src.replace(old, new))
    # Several mutations are SAME-LENGTH edits ('0xC' -> '0xA'). CPython
    # validates a cached .pyc on (source mtime, source size), so on a
    # filesystem with coarse mtime -- network shares, some FUSE mounts, and
    # the containers this gets run in -- a same-size edit made within the
    # mtime tick can leave the stale bytecode looking valid, the subprocess
    # silently runs the UNMUTATED module, and the mutation is reported as
    # "not caught". Giving each run a private, empty cache directory removes
    # the question entirely.
    cache = tempfile.mkdtemp(prefix='mutcache-')
    env = dict(os.environ, PYTHONPYCACHEPREFIX=cache)
    try:
        p = subprocess.run([sys.executable, script],
                           capture_output=True, text=True, timeout=300,
                           env=env)
        tail = (p.stdout + p.stderr).strip().splitlines()[-1]
        return p.returncode, '%s [%d site(s)]' % (tail, n)
    finally:
        open(path, 'w').write(src)
        shutil.rmtree(cache, ignore_errors=True)

bad = 0
print('each mutation must make test_dispatch.py FAIL\n')
for entry in MUTATIONS:
    desc, path, old, new = entry[:4]
    script = entry[4] if len(entry) > 4 else 'test_dispatch.py'
    rc, last = run(path, old, new, script)
    if rc is None:
        print('  ??  %-58s %s' % (desc, last)); bad += 1
    elif rc == 0:
        print('  !!  %-58s NOT CAUGHT' % desc); bad += 1
    else:
        print('  ok  %-58s caught' % desc)
print('\n%d/%d mutations caught' % (len(MUTATIONS) - bad, len(MUTATIONS)))
sys.exit(1 if bad else 0)
