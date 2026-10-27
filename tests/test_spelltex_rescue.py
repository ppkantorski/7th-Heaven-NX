#!/usr/bin/env python3
"""BUILD 807: magic.lgp spell-texture routing -- folder first, like FFNx.

Runs against the real dump and the real .iro.

    python3 tests/test_spelltex_rescue.py

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import atexit
import os
import re
import shutil
import struct
import tempfile

import ff7nx_spelltex as S
import iro
import lgp

HERE = _nx_root
MAGIC = os.path.join(HERE, 'dump', 'romfs', 'ff7', 'workingdir',
                     'data', 'battle', 'magic.lgp')
IRO = os.path.join(HERE, 'mods',
                   '[Tsunamods].SYW.Unified.Spells.Textures.V1.03.iro')
STEMS = {'ground_1', 'rock_1', 'club', 'dia', 'heart', 'joker', 'spade', '663', '882', 'exp_1',
         'moto1', 'flea_1', 'elec1', 'smoke', 'honou4', 'nova_3', 'cosmo_1'}
PASS = [0]


def ok(value, message):
    if not value:
        raise AssertionError(message)
    PASS[0] += 1


archive = lgp.Archive(MAGIC)
entries = archive.entries

# ---- 1. the conflict table is 0-based (the 807 off-by-one) --------------
blob = archive.middle[S.LOOKUP_TABLE_LEN:]
count = struct.unpack_from('<H', blob, 0)[0]
off, seen = 2, 0
for k in range(count):
    n = struct.unpack_from('<H', blob, off)[0]
    off += 2
    for _ in range(n):
        off += 128
        idx = struct.unpack_from('<H', blob, off)[0]
        off += 2
        ok(entries[idx]['conflict'] == k + 1,
           'conflict list %d index %d names an entry of that list' % (k, idx))
        seen += 1
F = S.conflict_folders(archive)
ok(len(F) == seen == 2450, 'every conflict index is labelled (%d)' % len(F))
for i, f in F.items():
    ok(entries[i]['conflict'] != 0, 'toc %d labelled %s has a conflict id' % (i, f))
ok(F[686].endswith('special/club1') and F[687].endswith('special/club2'),
   'club.tex 686/687 are club1/club2')
ok(F[1209].endswith('blue/beata') and F[1214].endswith('limit2/beast'),
   'exp_1 1209/1214 are beata/beast')

# ---- 2. the mod folder is the game folder FFNx builds --------------------
for path, want in (('FF7SYWU/Base/magic/ff7/data/battle/magic/deth',
                    'ff7/data/battle/magic/deth'),
                   ('ff7/data/battle/magic/deth', 'ff7/data/battle/magic/deth'),
                   ('magic/ff7/data/battle/special/club1',
                    'ff7/data/battle/special/club1'),
                   ('FF7SYWU/Langs/E/magic', ''), ('', '')):
    ok(S._mod_subfolder(path) == want, '_mod_subfolder(%r) == %r' % (path, want))

# ---- 3. routing ----------------------------------------------------------
WORK = tempfile.mkdtemp(prefix='spelltex-807-')
atexit.register(shutil.rmtree, WORK, True)
files = []
for x in (y.replace('\\', '/') for y in iro.list_entries(IRO)):
    low = x.lower()
    if '/magic/' not in low or '/langs/' in low or not low.endswith('.dds'):
        continue
    m = re.match(r'^(.*)_(\d+)\.dds$', os.path.basename(low))
    if not m or m.group(1) not in STEMS:
        continue
    rel = x.split('/magic/', 1)[1]
    dst = os.path.join(WORK, 'magic', rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, 'wb') as f:
        f.write(iro.read_one(IRO, x.replace('/', '\\')))
    files.append(('magic/' + rel, dst))
ok(len(files) > 50, 'test subset extracted (%d files)' % len(files))


def run(folder):
    saved = os.environ.get(S.FFNX_FOLDER_ENV)
    os.environ[S.FFNX_FOLDER_ENV] = folder
    try:
        return S.route(archive, files)
    finally:
        if saved is None:
            os.environ.pop(S.FFNX_FOLDER_ENV, None)
        else:
            os.environ[S.FFNX_FOLDER_ENV] = saved


t1, r1 = run('1')
t0, r0 = run('0')


def src(t, i):
    return S._mod_subfolder(os.path.dirname(
        os.path.relpath(t[i][0], WORK).replace(os.sep, '/')))


ok(r1.get('ffnx_folder', 0) == len(t1),
   'every target came from its own folder (%d of %d)'
   % (r1.get('ffnx_folder', 0), len(t1)))
for i in t1:
    ok(src(t1, i) == (F.get(i) or ''),
       'toc %d (%s) takes %s, its own folder %s'
       % (i, entries[i]['name'], src(t1, i), F.get(i)))
    ok(S._fits(t1[i], S._palettes_needed(entries[i]))
       and S._convertible(entries[i]['payload']),
       'toc %d is convertible with a full palette set' % i)

# The ones checked by eye in 807 (Claude/_scratch/807 swaps*.png, exp1.png,
# moto1.png): content routing had every one of these on the wrong entry.
for i in (686, 687, 891, 892, 1743, 1744, 2468, 2469, 4600, 4601,
          1478, 1490, 2202, 2214, 1209, 1214, 1226, 1252, 1269,
          3555, 3558, 3564, 3570, 3573, 3576, 1359, 1094, 1098, 3699):
    ok(i in t1, 'toc %d (%s) gets its own folder art' % (i, entries[i]['name']))

# Content-only fallback still works and still refuses gross mismatches.
ok(len(t0) > 20 and not r0.get('ffnx_folder'),
   'SEVENTH_NX_SPELL_FFNX_FOLDER=0 routes by content only (%d)' % len(t0))
for i in t0:
    if i in set(S._overrides()):
        continue
    s = S.agreement(entries[i]['payload'], open(t0[i][0], 'rb').read())
    ok(s is None or s >= S.ABS_SHAPE_FLOOR,
       'content-only toc %d clears the floor (%s)' % (i, s))
# ---- 4. the two 16-bit mesh entries (ff7nx_placeholdertex) -------------
import ff7nx_placeholdertex as P
import tex
_c = tempfile.mkdtemp(prefix='spelltex-807-d16-')
atexit.register(shutil.rmtree, _c, True)
_saved = os.environ.get(S.CAP_ENV)
os.environ[S.CAP_ENV] = '768'
try:
    _d16 = P.payloads(archive, files, _c)
finally:
    if _saved is None:
        os.environ.pop(S.CAP_ENV, None)
    else:
        os.environ[S.CAP_ENV] = _saved
# BUILD 811: 3x like every other spell TEX (807c's 4x/512 broke the rule)
for i, (w, h) in ((1534, (384, 768)), (4059, (384, 384))):
    t = tex.parse(_d16.get(i, b''))
    ok(t and t['palette_flag'] and t['bytes_per_pixel'] == 1
       and (t['width'], t['height']) == (w, h),
       '16-bit toc %d rebuilt paletted at %dx%d' % (i, w, h))
    ok(struct.unpack_from('<I', _d16[i], tex.O_COLORKEY)[0] == 0,
       'toc %d keeps its (absent) colour key' % i)
    ok(tex.logical_scale(_d16[i]) is None, 'toc %d is unmarked (mesh UVs)' % i)
ok(i not in t1, 'route() itself never targets a 16-bit entry')

print('test_spelltex_rescue: %d checks passed (%d by folder, %d content-only)'
      % (PASS[0], len(t1), len(t0)))
