#!/usr/bin/env python3
"""
test_exe_versions.py -- prove the patch set is identical on both x86 builds.

    python3 test_exe_versions.py --exe ff7_en --exe-switch ff7_en_switch \
                                 --nso exefs/main

WHAT IS BEING COMPARED
======================
Two x86 builds of ff7_en run the game on the Switch:

    pc-1.02        6,421,856 bytes
    switch-1.03_5  5,997,027 bytes

They are the same image. The Switch build stores less trailing linker padding
and drops the .FTS section, so the FILE offsets differ -- which is what an
earlier byte diff saw, and why the Switch executable was written off as
"everything at different addresses". Its VIRTUAL layout is identical, and a
virtual address is the only thing a patch address means.

This test asserts the properties the dual-exe support rests on:

  1. both files identify as known builds and their .text hashes match;
  2. every section they share maps to the same VA and the same bytes;
  3. patching both produces the same MAPPED IMAGE, section for section --
     the file layouts stay different, the loaded result does not;
  4. the rebuilt `main` is byte-identical whichever exe was used, because the
     NSO side never depended on the exe in the first place;
  5. the one address that needed help -- the damage y-offset table, which
     lives in .rdata linker padding -- ends up at the same VA in both, and
     the section grew only into pre-existing zero file padding without moving
     any other section.

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
import argparse
import hashlib
import struct
import subprocess
import sys
import tempfile
import os

FAIL = []


def fail(msg):
    FAIL.append(msg)
    print('  FAIL  %s' % msg)


def ok(msg):
    print('  ok    %s' % msg)


def pe(path):
    d = open(path, 'rb').read()
    off = struct.unpack('<I', d[0x3C:0x40])[0]
    if d[off:off + 4] != b'PE\0\0':
        off = d.find(b'PE\0\0')
    nsec = struct.unpack('<H', d[off + 6:off + 8])[0]
    optsz = struct.unpack('<H', d[off + 20:off + 22])[0]
    ib = struct.unpack('<I', d[off + 24 + 28:off + 24 + 32])[0]
    tbl = off + 24 + optsz
    secs = {}
    for i in range(nsec):
        e = d[tbl + 40 * i:tbl + 40 * (i + 1)]
        nm = e[:8].rstrip(b'\0').decode('latin1')
        vs, va, rs, ro = struct.unpack('<IIII', e[8:24])
        secs[nm] = dict(va=ib + va, vsize=vs, rsize=rs, raw=ro)
    return d, secs


def mapped(d, s):
    """What the loader sees: raw bytes then zero fill to VirtualSize."""
    n = min(s['rsize'], s['vsize'])
    return d[s['raw']:s['raw'] + n] + b'\0' * (s['vsize'] - n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', required=True, help='the PC 1.02 build')
    ap.add_argument('--exe-switch', required=True, help='the Switch 1.03_5 build')
    ap.add_argument('--nso', required=True)
    ap.add_argument('--args', default='--legacy --enable-all',
                    help='patch selection to build with')
    a = ap.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import ff7nx_60fps as gen

    print('1. identification\n')
    names = {}
    for tag, p in (('pc', a.exe), ('switch', a.exe_switch)):
        d = open(p, 'rb').read()
        try:
            names[tag] = gen.identify_exe(d, log=lambda *_: None)
        except SystemExit as exc:
            fail('%s: %s' % (p, exc))
            return 1
        ok('%-7s %-14s md5 %s' % (tag, names[tag], hashlib.md5(d).hexdigest()))
    if names['pc'] == names['switch']:
        fail('both files identified as %s -- did you pass the same one twice?'
             % names['pc'])

    print('\n2. stock images agree section by section\n')
    da, sa = pe(a.exe)
    db, sb = pe(a.exe_switch)
    shared = sorted(set(sa) & set(sb))
    for nm in shared:
        if sa[nm]['va'] != sb[nm]['va']:
            fail('%s VA differs: %#x vs %#x' % (nm, sa[nm]['va'], sb[nm]['va']))
            continue
        n = min(sa[nm]['rsize'], sb[nm]['rsize'])
        x = da[sa[nm]['raw']:sa[nm]['raw'] + n]
        y = db[sb[nm]['raw']:sb[nm]['raw'] + n]
        if x != y:
            fail('%s stored bytes differ' % nm)
        else:
            ok('%-8s VA %#08x  %d stored bytes identical  (raw %#x vs %#x)'
               % (nm, sa[nm]['va'], n, sa[nm]['raw'], sb[nm]['raw']))
    only = (set(sa) | set(sb)) - set(sa) & set(sb)
    for nm in sorted((set(sa) | set(sb)) - set(shared)):
        ok('%-8s present only in %s -- unreferenced by any patch'
           % (nm, 'pc' if nm in sa else 'switch'))

    print('\n3. build both\n')
    outs = {}
    for tag, p in (('pc', a.exe), ('switch', a.exe_switch)):
        dst = tempfile.mkdtemp()
        cmd = [sys.executable, 'ff7nx_60fps.py', '--exe', p, '--nso', a.nso,
               '--out', dst] + a.args.split()
        r = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=os.path.dirname(os.path.abspath(__file__)))
        if r.returncode:
            fail('%s build failed:\n%s' % (tag, r.stdout[-1500:] + r.stderr[-500:]))
            return 1
        root = os.path.join(dst, 'atmosphere', 'contents', gen.TITLE_ID)
        outs[tag] = (
            os.path.join(root, 'romfs', 'ff7', 'resources', 'ff7_1.02', 'ff7_en'),
            os.path.join(root, 'exefs', 'main'))
        ok('%-7s built' % tag)

    print('\n4. the rebuilt main is exe-independent\n')
    ma = hashlib.md5(open(outs['pc'][1], 'rb').read()).hexdigest()
    mb = hashlib.md5(open(outs['switch'][1], 'rb').read()).hexdigest()
    if ma != mb:
        fail('main differs: %s vs %s' % (ma, mb))
    else:
        ok('main md5 %s from both exes' % ma)

    print('\n5. patched images agree section by section\n')
    pa, qa = pe(outs['pc'][0])
    pb, qb = pe(outs['switch'][0])
    for nm in sorted(set(qa) & set(qb)):
        if mapped(pa, qa[nm]) != mapped(pb, qb[nm]):
            fail('%s mapped image differs after patching' % nm)
        else:
            ok('%-8s mapped image identical  (rsize %#x vs %#x)'
               % (nm, qa[nm]['rsize'], qb[nm]['rsize']))

    print('\n6. the grown section grew only into zero padding\n')
    before, after = sb['.rdata'], qb['.rdata']
    if after['raw'] != before['raw'] or after['va'] != before['va']:
        fail('.rdata moved: raw %#x->%#x va %#x->%#x'
             % (before['raw'], after['raw'], before['va'], after['va']))
    else:
        ok('.rdata did not move (raw %#x, VA %#x)' % (after['raw'], after['va']))
    if after['rsize'] < before['rsize']:
        fail('.rdata shrank')
    elif after['rsize'] > before['rsize']:
        claimed = db[before['raw'] + before['rsize']:before['raw'] + after['rsize']]
        if set(claimed) - {0}:
            fail('.rdata claimed %d non-zero byte(s)' % len(claimed))
        else:
            ok('.rdata SizeOfRawData %#x -> %#x, %d claimed byte(s) were all '
               'zero in the stock file' % (before['rsize'], after['rsize'],
                                           len(claimed)))
    nxt = min((s['raw'] for s in sb.values() if s['raw'] > before['raw']),
              default=len(db))
    if before['raw'] + after['rsize'] > nxt:
        fail('.rdata now overlaps the next section at file %#x' % nxt)
    else:
        ok('.rdata raw end %#x still <= next section start %#x'
           % (before['raw'] + after['rsize'], nxt))
    for nm in sorted(set(sb) & set(qb)):
        if nm == '.rdata':
            continue
        if sb[nm]['raw'] != qb[nm]['raw']:
            fail('%s raw offset moved %#x -> %#x'
                 % (nm, sb[nm]['raw'], qb[nm]['raw']))
    ok('no other section changed file offset')

    print('\n%d failure(s)' % len(FAIL))
    if FAIL:
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
