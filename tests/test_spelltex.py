#!/usr/bin/env python3
"""End-to-end checks for the magic.lgp spell-texture path.

Runs against the real dump and the real .iro, so a pass here means the
routing, the conversion and the in-place rewrite all agree with the actual
data -- not with a fixture that was written to match the code.

    python3 tests/test_spelltex.py            # routing + conversion + archive
    python3 tests/test_spelltex.py --all      # also convert every entry (slow)

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
import collections
import os
import struct
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import numpy as np

import ff7nx_ddstex
import ff7nx_spelltex as S
import iro
import lgp
import tex

MAGIC = os.path.join(HERE, 'dump', 'romfs', 'ff7', 'workingdir',
                     'data', 'battle', 'magic.lgp')
IRO = os.path.join(HERE, 'mods',
                   '[Tsunamods].SYW.Unified.Spells.Textures.V1.03.iro')


def ok(value, message):
    if not value:
        raise AssertionError(message)


def render(data, p):
    """The picture a TEX shows through palette p: (rgb [h,w,3], alpha).

    Returns a 2-tuple; callers that only want the colour take ``[0]``.
    """
    t = tex.parse(data)
    n, c = t['num_palettes'], t['colors_per_palette']
    pal = np.frombuffer(t['palette'], dtype=np.uint8)[:n * c * 4]
    pal = pal.reshape(n, c, 4)
    idx = np.frombuffer(t['pixels'], dtype=np.uint8).reshape(
        t['height'], t['width'])
    return pal[p][:, [2, 1, 0]][idx].astype(np.int16), pal[p][:, 3][idx]


archive = lgp.Archive(MAGIC)
entries = archive.entries

# ---------------------------------------------------------------- the table
folders = S.conflict_folders(archive)
ok(len(folders) > 2000,
   'the conflict table parses (%d indices)' % len(folders))
ok(all(0 <= i < len(entries) for i in folders),
   'every conflict index is inside the TOC')
dup = collections.Counter(e['name'].lower() for e in entries)
ok(sum(1 for v in dup.values() if v > 1) > 600,
   'magic.lgp really does have hundreds of duplicate names')

# A truncated / unexpected table must yield {} rather than a partial map.
class _Bad:
    middle = archive.middle[:S.LOOKUP_TABLE_LEN] + b'\xff\xff' + b'\x00' * 8
ok(S.conflict_folders(_Bad()) == {}, 'a malformed conflict table is refused')

# ------------------------------------------------------------- name parsing
ok(S._parse_dds_name('alex1_00.dds') == ('alex1', 0), 'plain name + palette')
ok(S._parse_dds_name('alex2_00_01.dds') == ('alex2_00', 1),
   'the split is greedy, so alex2_00.tex wins over alex2.tex')
ok(S._parse_dds_name('smoke.dds') is None, 'no palette suffix is refused')
ok(S._parse_dds_name('a_x.dds') is None, 'a non-numeric suffix is refused')

# ------------------------------------------------------------------ routing
raw = [x.replace('\\', '/') for x in iro.list_entries(IRO)]
# One language only, the way active_folders() hands them to the build.
mag = [x for x in raw
       if '/magic/' in x.lower() and x.lower().endswith('.dds')
       and '/langs/' not in x.lower()]
ok(len(mag) > 2800, 'the mod ships the expected magic set (%d)' % len(mag))

# The router reads pixels now, so the files have to exist. Extracting all
# 2,601 would be ~2.5 GB; take every file for the stems that actually
# exercise the hard paths -- the duplicated names, the atlas that broke in
# build 225, and the multi-palette sets -- plus a deterministic sample.
import random as _random
import re as _re
import shutil
import atexit
import shutil
import tempfile

_stems = collections.defaultdict(list)
for x in mag:
    m = _re.match(r'^(.*)_(\d+)\.dds$', os.path.basename(x).lower())
    if m:
        _stems[m.group(1)].append(x)
_names = collections.Counter(e['name'].lower() for e in entries)
_dups = sorted(s for s in _stems if _names.get(s + '.tex', 0) > 1)
_rest = sorted(s for s in _stems if _names.get(s + '.tex', 0) == 1)
_random.Random(4).shuffle(_rest)
_want = set(_dups[:70]) | {'exp_1', 'thunde00', 'thunde02', 'kemuri',
                           'koudan00', 'alex1', 'ice', 'moon_1'} | set(_rest[:60])
_want &= set(_stems)

WORK = tempfile.mkdtemp(prefix='spelltex-test-')
# CLEAN UP. Every run before this one left its extracted DDS behind; by the
# time anyone looked there were 7.1 GB of spelltex-test-* directories on the
# work volume and the suite was failing with ENOSPC -- caused by itself.
atexit.register(shutil.rmtree, WORK, True)
files = []
for stem in sorted(_want):
    for x in _stems[stem]:
        rel = x.split('/magic/', 1)[1]
        dst = os.path.join(WORK, rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'wb') as f:
            f.write(iro.read_one(IRO, x.replace('/', '\\')))
        files.append((rel, dst))
ok(len(files) > 250, 'the test subset is representative (%d files)' % len(files))

targets, report = S.route(archive, files)

routed = report['matched']
ok(routed > 150, 'most DDS sets content-match an entry (%d)' % routed)
ok(report['unparsed'] == 0, 'every SYW magic file parses as name+palette')

# Routing must not depend on the order the files arrive in.
import random as _random
shuffled = list(files)
_random.Random(11).shuffle(shuffled)
t2, r2 = S.route(archive, shuffled)
ok(t2 == targets and r2 == report,
   'routing is reproducible regardless of input order')

# BUILD 225 REGRESSION: content decides, names only propose.
#
# magic.lgp holds five exp_1.tex entries and the mod ships six exp_1_NN.dds.
# Folder routing paired entry 1214 (conflict folder limit2/beast) with the
# `beast` file, which scores 12.6%; the file that actually belongs to it is
# the `beata` one, at 100.0%. Build 225 shipped that and clipped a full-frame
# explosion through a 4x4 animation atlas.
# The gate is RELATIVE to the best candidate for a name, with an absolute
# floor -- see route(). An absolute MIN_AGREEMENT here would fail on texture
# families where every candidate scores ~0.75, which is exactly the case
# (452.tex) that the relative gate exists to handle.
# BUILD 807: the reviewed table (data/spelltex_overrides.json) is the one
# exemption -- opaque surfaces the shape test cannot judge, placed by a
# person. Everything else must still clear the floor.
_reviewed = set(S._overrides())
ok(len(_reviewed) == 15, 'the reviewed override table loads (%d)' % len(_reviewed))
# BUILD 807b: and the FFNx-parity folder pass, which places by exact folder
# the way FFNx does on PC -- checked for that, not for shape.
_folders807 = S.conflict_folders(archive)
for idx in report.get('ffnx_folder_tocs', []):
    _sub = S._mod_subfolder(os.path.dirname(
        targets[idx][0].replace(os.sep, '/').split(WORK, 1)[-1].lstrip('/')))
    ok(_sub.endswith((_folders807.get(idx) or '')),
       'toc %d: folder pass used the entry\'s own folder' % idx)
for idx, pals in targets.items():
    if idx in report.get('ffnx_folder_tocs', []):
        continue
    if idx in _reviewed:
        ok(not (S._keyed_mask(entries[idx]['payload'])[1] is not None
                and S._keyed_mask(entries[idx]['payload'])[1].any()),
           'toc %d: an override never lands on a keyed entry' % idx)
        continue
    s_ = S.agreement(entries[idx]['payload'], open(pals[0], 'rb').read())
    ok(s_ is None or s_ >= S.ABS_SHAPE_FLOOR,
       '%s toc %d: shipped art clears the absolute floor (%.2f)'
       % (entries[idx]['name'], idx, -1 if s_ is None else s_))

# Every target must still hold a complete palette set.
for idx, pals in targets.items():
    ok(S._fits(pals, S._palettes_needed(entries[idx])),
       '%s toc %d has all %d palettes'
       % (entries[idx]['name'], idx, S._palettes_needed(entries[idx])))

# Duplicate names must not all collapse onto one TOC index.
by_name = collections.defaultdict(set)
for idx in targets:
    by_name[entries[idx]['name'].lower()].add(idx)
multi = {n: v for n, v in by_name.items() if len(v) > 1}
ok(len(multi) >= 10,
   'duplicate-named entries are addressed separately (%d names)' % len(multi))

# --------------------------------------------------------------- conversion
def group(idx):
    out = {}
    for p, path in targets[idx].items():
        with open(path, 'rb') as f:
            out[p] = f.read()
    return out


# UNIFORM SCALING: every texture moves by exactly k, whatever it looks like.
# The per-texture freezes (frame sheets, full-frame art, multi-palette held at
# 256, the 1x256 layout switch) are all gone -- they were attempts to work
# around unscaled frame tables, and the tables are scaled now.
by_pal = sorted(targets, key=lambda i: len(targets[i]))
picks = [by_pal[0], by_pal[len(by_pal) // 2], by_pal[-1]]
_saved = os.environ.get(S.CAP_ENV)
try:
    for cap_px, want_k in (('', 1), ('512', 2), ('768', 3)):
        os.environ[S.CAP_ENV] = cap_px
        ok(S.uniform_scale() == want_k,
           'cap %r gives a uniform factor of %d (got %d)'
           % (cap_px, want_k, S.uniform_scale()))
        for idx in picks:
            van = entries[idx]['payload']
            v = tex.parse(van)
            out, _n = ff7nx_ddstex.convert_group(van, group(idx), scale=want_k)
            c = tex.parse(out)
            ok((c['width'], c['height'])
               == (v['width'] * want_k, v['height'] * want_k),
               '%s scales exactly %dx (%dx%d -> %dx%d)'
               % (entries[idx]['name'], want_k, v['width'], v['height'],
                  c['width'], c['height']))
            # FINDINGS-248. `colors_per_palette` is now allowed to widen to
            # 256 -- that is the whole fix, and 92% of replaced entries were
            # posterised without it. Everything ELSE must still be vanilla:
            # num_palettes above all, because forcing it to 1 is what build
            # 226 did and it destroys palette cycling.
            for f in ('num_palettes', 'palette_flag', 'bytes_per_pixel'):
                ok(c[f] == v[f],
                   '%s keeps vanilla %s at %dx (%r vs %r)'
                   % (entries[idx]['name'], f, want_k, c[f], v[f]))
            ok(c['colors_per_palette'] in (v['colors_per_palette'], 256),
               '%s colours are vanilla or 256, got %r'
               % (entries[idx]['name'], c['colors_per_palette']))
            ok(c['palette_size'] == c['num_palettes'] * c['colors_per_palette'],
               '%s palette_size %r matches %d x %d'
               % (entries[idx]['name'], c['palette_size'],
                  c['num_palettes'], c['colors_per_palette']))
            ok(len(out) == tex.HEADER_LEN + c['palette_size'] * 4
               + c['width'] * c['height'],
               '%s payload length matches its own header'
               % entries[idx]['name'])

finally:
    if _saved is None:
        os.environ.pop(S.CAP_ENV, None)
    else:
        os.environ[S.CAP_ENV] = _saved

# The production converter applies the one visible cap to effects as well as
# model textures.  Odin and thunder are explicit regressions: they must be
# emitted enlarged and marked while every `.s` table remains byte-identical.
_critical = {i: targets[i] for i in targets
             if entries[i]['name'].lower() in
             {'moon_1.tex', 'thunde00.tex', 'thunde02.tex'}}
ok(any(entries[i]['name'].lower() == 'moon_1.tex' for i in _critical),
   'the production subset includes Odin moon_1')
ok(any(entries[i]['name'].lower().startswith('thunde') for i in _critical),
   'the production subset includes thunder effects')
_saved_cap = os.environ.get(S.CAP_ENV)
_saved_fx = os.environ.get(S.FX_CAP_ENV)
try:
    os.environ[S.CAP_ENV] = '1024'
    os.environ[S.FX_CAP_ENV] = '0'
    _critical_out, _critical_tables, _critical_stats = S.convert(
        archive, _critical, os.path.join(WORK, 'critical-cache'),
        log=lambda *a: None)
    ok(set(_critical_out) == set(_critical),
       'all Odin/thunder regression targets are rebuilt')
    ok(_critical_tables == {}, 'production never rewrites an .s table')
    for _i, _data in _critical_out.items():
        _native = tex.parse(entries[_i]['payload'])
        _built = tex.parse(_data)
        _sx = _built['width'] // _native['width']
        _sy = _built['height'] // _native['height']
        ok(max(_built['width'], _built['height']) <= 1024,
           '%s obeys the visible cap' % entries[_i]['name'])
        ok((_sx > 1 or _sy > 1) and tex.logical_scale(_data) == (_sx, _sy),
           '%s is enlarged and carries its exact per-axis scale'
           % entries[_i]['name'])
    # The retired FX value cannot change production output.
    os.environ[S.FX_CAP_ENV] = '768'
    _again, _again_tables, _ = S.convert(
        archive, _critical, os.path.join(WORK, 'critical-cache'),
        log=lambda *a: None)
    ok(_again == _critical_out and _again_tables == {},
       'the retired FX setting does not gate or resize production textures')
finally:
    for _key, _value in ((S.CAP_ENV, _saved_cap),
                         (S.FX_CAP_ENV, _saved_fx)):
        if _value is None:
            os.environ.pop(_key, None)
        else:
            os.environ[_key] = _value
print('  production cap: Odin and thunder enlarged and marked; retired FX '
      'setting has no effect; every .s table remains unchanged')

# ---------------------------------------------------------------------------
# FINDINGS-248: both switches are the revert
# ---------------------------------------------------------------------------
# With them off, every structural field is vanilla's again -- that is what
# makes the change safe to ship: one variable puts the old converter back.
_sv = {k: os.environ.get(k) for k in (ff7nx_ddstex.WIDE_PALETTE_ENV,
                                      ff7nx_ddstex.DDS_ALPHA_ENV)}
os.environ[ff7nx_ddstex.WIDE_PALETTE_ENV] = '0'
os.environ[ff7nx_ddstex.DDS_ALPHA_ENV] = '0'
try:
    ok(not ff7nx_ddstex.wide_palette() and not ff7nx_ddstex.dds_alpha(),
       'both FINDINGS-248 switches turn off')
    for idx in picks:
        van = entries[idx]['payload']
        v = tex.parse(van)
        out, _n = ff7nx_ddstex.convert_group(van, group(idx), scale=1)
        c = tex.parse(out)
        for f in ('num_palettes', 'colors_per_palette', 'palette_size',
                  'palette_flag', 'bytes_per_pixel'):
            ok(c[f] == v[f],
               '%s keeps vanilla %s with the switches off (%r vs %r)'
               % (entries[idx]['name'], f, c[f], v[f]))
finally:
    for _k, _val in _sv.items():
        if _val is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _val

# A vanilla texture the mod does not supply must upscale by the same factor,
# exactly, with its palette untouched.
_n_up = 0
for _e in entries:
    if not _e['name'].endswith('.tex'):
        continue
    _g = S.upscale_vanilla_tex(_e['payload'], 2)
    if _g is None:
        continue
    _a, _b = tex.parse(_e['payload']), tex.parse(_g)
    ok((_b['width'], _b['height']) == (_a['width'] * 2, _a['height'] * 2)
       and _b['palette'] == _a['palette']
       and _b['num_palettes'] == _a['num_palettes'],
       '%s upscales 2x with its palette intact' % _e['name'])
    _n_up += 1
ok(_n_up > 1000, 'nearly every texture upscales (%d)' % _n_up)

# ------------------------------------------------- EFFECT FRAME TABLES
#
# BUILD 230. magic.lgp's .s files address effect textures by ABSOLUTE TEXEL
# coordinate, so a resized texture needs its frame tables resized with it.
# That is what makes upscaling possible; without it every frame draws a crop
# of its cell, which is builds 224-229's discontinuous squares.
_s = [e for e in entries if e['name'].lower() == 'hit1.s']
ok(_s, 'hit1.s is present')
_recs = S.parse_frame_table(_s[0]['payload'])
ok(_recs and len(_recs) == 12, 'hit1.s parses as 12 frames (%r)' % (_recs and len(_recs),))

# BUILD 232 REGRESSION: a record is VARIABLE length -- u16 flag, u16 sub-count,
# then count x 20 bytes. Assuming a fixed 24 parsed only the files where every
# record holds one sub-entry: 685 of 806. The other 121 were skipped, so their
# textures were scaled and their frame tables were not, and the rectangles came
# back on exactly those effects.
#
# BUILD 236 REGRESSION, the other half: scale_frame_table used to CLAMP an
# extent to 255 rather than refuse, which writes a table that silently no
# longer covers its sprite while reporting success. Refusal must stay exact.
#
# FINDINGS-257 -- AND THIS IS WHY THE FUNCTION IS NOW UNUSED. The engine
# computes `quad_width = 8 * (byte)field_10` from the extent byte, so scaling
# it makes the sprite bigger on SCREEN rather than moving a texture
# coordinate. fx_tables() is off and no setting of it is correct.
ok(S.fx_tables() is False,
   'frame tables are NEVER rescaled -- the extent byte is the quad size')

# The height is the low byte of +18, not the byte at +17. exp_1 is the proof:
# its cells reach V+byte17 = 447 against a 256-tall texture, and V+byte18 =
# 256 exactly. The two differ in 2,597 of the 7,178 sub-entries, so this was
# not a harmless alias -- it fed every WHOLE/SUBRECT/FOREIGN decision.
ok(S.S_SUB_H == 18 and S.S_SUB_W == 16, 'extent bytes are +16 (w) and +18 (h)')
_ns = _nf = _cells = _refused = 0
for _e in entries:
    if not _e['name'].lower().endswith('.s'):
        continue
    _r = S.parse_frame_table(_e['payload'])
    if _r is None:
        _nf += 1
        continue
    _ns += 1
    _cells += len(_r)
    ok(all(c[3] == _e['payload'][_o + 18]
           for c, _o in zip(_r, S._iter_subs(_e['payload']))),
       '%s: parse_frame_table takes the height from +18' % _e['name'])
    _over = any(max(_e['payload'][_o + 16], _e['payload'][_o + 18]) * 2 > 0xFF
                for _o in S._iter_subs(_e['payload']))
    _g = S.scale_frame_table(_e['payload'], 2)
    if _over:
        ok(_g is None,
           '%s overflows the u8 extent and must be REFUSED, not clamped'
           % _e['name'])
        _refused += 1
    else:
        ok(_g is not None and len(_g) == len(_e['payload']),
           '%s rescales without changing length' % _e['name'])
ok(_nf == 0, 'EVERY .s file parses -- %d did not' % _nf)
ok(_ns == 806, 'all 806 frame tables parse (%d)' % _ns)
ok(_refused > 0, 'the u8 extent is still refused, never clamped (%d)' % _refused)
ok(_cells > 7000, 'they cover the expected number of cells (%d)' % _cells)

for _nm in ('ang_m2.s', 'aqua.s', 'ayasii.s', 'bokasi.s'):
    _x = [y for y in entries if y['name'].lower() == _nm][0]
    ok(S.parse_frame_table(_x['payload']),
       '%s (multi-entry records) parses' % _nm)
# The last cell used to read 64x63. That 63 was the byte at +17, which is the
# HIGH byte of field_10 and not a height at all -- the engine takes the low
# byte of field_12, at +18, which is 64. A clean 4-wide grid of 64px cells,
# which is what the sheet looks like. FINDINGS-257.
ok(_recs[:4] == [(0, 0, 64, 64), (64, 0, 64, 64), (128, 0, 64, 64),
                 (192, 0, 64, 64)],
   'hit1.s is a 4-wide grid of 64px cells at texel coordinates: %r' % (_recs[:4],))

_x2 = S.scale_frame_table(_s[0]['payload'], 2)
ok(_x2 is not None and len(_x2) == len(_s[0]['payload']),
   'a x2 rescale of hit1.s needs no split, so the byte length is unchanged')
_r2 = S.parse_frame_table(_x2)
ok(all(tuple(v * 2 for v in a) == b for a, b in zip(_recs, _r2)),
   'every texel coordinate and source extent is exactly doubled')
# hit1.s's 64px cells reach 256 at x4 -- one over the u8 extent. Build 236 and
# earlier CLAMPED that to 255 and reported success, which is the one thing not
# to do. Since FINDINGS-256 it is neither clamped nor refused: the cell is cut,
# so the sheet still covers its sprite exactly and the file gets longer.
ok(S.scale_frame_table(_s[0]['payload'], 4) is None,
   'x4 overflows hit1.s 64px cells and is refused, not clamped')
ok(S.scale_frame_table(_s[0]['payload'], 3) is not None,
   'the same table still rescales at x3, where 64 -> 192 fits')
ok(S.scale_frame_table(_s[0]['payload'], 1) == _s[0]['payload'],
   'x1 returns the payload untouched')

# The map is built by name AND verified by extent.
_tbl, _orphan = S.frame_tables_for(archive)
ok(len(_tbl) > 100, 'frame tables map to textures (%d textures)' % len(_tbl))
for _j, _ss in _tbl.items():
    _t = tex.parse(entries[_j]['payload'])
    for _i in _ss:
        ok(entries[_i]['name'].lower()[:-2] == entries[_j]['name'].lower()[:-4],
           'a table is only mapped to its same-named texture')
        _rr = S.parse_frame_table(entries[_i]['payload'])
        ok(max(u + w for u, _v2, w, _h in _rr) <= _t['width']
           and max(v + h for _u, v, _w, h in _rr) <= _t['height'],
           '%s fits inside %s' % (entries[_i]['name'], entries[_j]['name']))
print('  effect frame tables: parsed, rescaled and mapped (%d textures, '
      '%d unmatched tables)' % (len(_tbl), len(_orphan)))

shutil.rmtree(WORK, ignore_errors=True)

# ------------------------------------------------------ RECOLOURED DUPLICATES
#
# BUILD 230. `452.tex` and `551.tex` each exist four times in magic.lgp -- the
# same sprite under special/gas (blue), special/kaen and kaen2 (red, kaen =
# flame) and special/oil -- and SYW ships a correctly coloured DDS in each of
# those folders. Shape cannot separate them (all ~0.80), so without hue the
# pick was arbitrary and every entry got the same orange art: the Beam Gun
# rendered red instead of blue.
import tempfile as _tf
_W2 = _tf.mkdtemp(prefix='spelltex-colour-')
atexit.register(shutil.rmtree, _W2, True)
_cf = []
for _x in raw:
    _b = os.path.basename(_x).lower()
    if '/magic/' not in _x.lower() or not _b.endswith('.dds'):
        continue
    if not (_b.startswith('452_') or _b.startswith('551_')):
        continue
    _rel = _x.split('/magic/', 1)[1]
    _dst = os.path.join(_W2, _rel.replace('/', os.sep))
    os.makedirs(os.path.dirname(_dst), exist_ok=True)
    with open(_dst, 'wb') as _f:
        _f.write(iro.read_one(IRO, _x.replace('/', '\\')))
    _cf.append((_rel, _dst))
ok(len(_cf) == 8, 'the four colour variants of 452 and 551 are present (%d)'
   % len(_cf))
_ct, _cr = S.route(archive, _cf)
ok(len(_ct) == 8, 'all eight recoloured entries are routed (%d)' % len(_ct))
for _i, _p in _ct.items():
    _fold = (folders.get(_i) or '').split('/')[-1]
    _src = os.path.basename(os.path.dirname(_p[0]))
    ok(_fold == _src,
       '%s toc %d (%s) takes the art from its own folder, got %s'
       % (entries[_i]['name'], _i, _fold, _src))
    _col = S.colour_agreement(entries[_i]['payload'], open(_p[0], 'rb').read())
    ok(_col is not None and _col > 0.9,
       '%s toc %d matches its slot in hue (%.2f)'
       % (entries[_i]['name'], _i, -1 if _col is None else _col))
shutil.rmtree(_W2, ignore_errors=True)
print('  recoloured duplicates: all 8 routed to their own colour')

# --------------------------------------------------- MODEL vs FX CLASSING
#
# BUILD 235. magic.lgp holds two kinds of texture. A MODEL texture is named by
# an .rsd and drawn by a .p mesh whose UVs are floats in 0..1, so its size is
# encoded nowhere and it can be resized freely -- the same property that lets
# char.lgp field models ship at 512px. An FX texture is addressed by a .s
# frame table in ABSOLUTE TEXELS and cannot.
#
# Builds 232-234 scaled everything and broke Braver, whose limit-break folder
# holds both kinds. Only MODEL is resized now, so nothing indexed by texel
# changes size and no frame table is rewritten.
_cls = S.texture_classes(archive)
_by = collections.Counter(_cls.values())
_MESH = S.mesh_uv_textures(archive)
ok(len(_MESH) > 500,
   'the mesh-UV set is found (%d textures a .p addresses in floats)'
   % len(_MESH))
_CELLS, _SCALABLE_S = S.frame_cells(archive, _cls)
_SIZABLE = S.resizable_textures(archive, _cls, _MESH, _CELLS)

# The historical classifier may still label moon_1 as FOREIGN.  That label is
# diagnostic only now: the per-TEX logical-step bridge is what makes its
# 384x384 physical image retain the native 128x128 coordinate grid.
_moon1 = next((i for i, e in enumerate(entries)
               if e['name'].lower() == 'moon_1.tex'), None)
if _moon1 is not None:
    ok(_moon1 in _CELLS,
       'moon_1.tex remains classified for diagnostics')
_moon = next((i for i, e in enumerate(entries)
              if e['name'].lower() == 'moon.tex'), None)
if _moon is not None:
    ok(_CELLS.get(_moon) == S.CELL_WHOLE,
       'moon.tex is still WHOLE (got %r)' % _CELLS.get(_moon))
_bl = next((i for i, e in enumerate(entries)
            if e['name'].lower() == 'blaver00.tex'), None)
if _bl is not None:
    ok(_bl in _CELLS,
       'blaver00.tex still finds a table through the family fallback')

# ---------------------------------------------------------------------------
# The wrap repair is ON by default, but its production allowlist is targeted.
# ---------------------------------------------------------------------------
ok(ff7nx_ddstex.reseam(),
   'the targeted wrap repair defaults ON')
ok(ff7nx_ddstex.TARGETED_WRAP_REPAIRS == {'kiri_2.tex': (1,)},
   'only Odin 2 kiri_2 is allowed a production seam edit')
_flat = np.zeros((64, 64, 4), dtype=np.uint8)
_flat[:, :, 3] = 255
ok(ff7nx_ddstex.broken_wraps(_flat[:, :, :3], _flat) == [],
   'a flat texture reports no broken wrap')
_broken = _flat.copy()
_broken[:, :, 0] = np.arange(64, dtype=np.uint8)[None, :]
_broken[:, -1, 0] = 255
ok(1 in ff7nx_ddstex.broken_wraps(_flat[:, :, :3], _broken),
   'a replacement that breaks a wrap vanilla had is detected')
_fixed = ff7nx_ddstex._reseam_to_first(_broken, 1)
ok(abs(int(_fixed[:, 0, 0].mean()) - int(_fixed[:, -1, 0].mean())) <= 1,
   'the repair makes the two edges meet')
ok((_fixed[:, 0] == _broken[:, 0]).all(),
   'the repair preserves the first edge')
ok(_fixed.shape == _broken.shape, 'the repair does not resize the image')
_mid = _broken.shape[1] // 2
ok((_fixed[:, _mid] == _broken[:, _mid]).all(),
   'the repair leaves the middle of the image untouched')


# A vanilla texture the mod does not supply must upscale by the same factor,
# exactly, with its palette untouched.
_n_up = 0
for _e in entries:
    if not _e['name'].endswith('.tex'):
        continue
    _g = S.upscale_vanilla_tex(_e['payload'], 2)
    if _g is None:
        continue
    _a, _b = tex.parse(_e['payload']), tex.parse(_g)
    ok((_b['width'], _b['height']) == (_a['width'] * 2, _a['height'] * 2)
       and _b['palette'] == _a['palette']
       and _b['num_palettes'] == _a['num_palettes'],
       '%s upscales 2x with its palette intact' % _e['name'])
    _n_up += 1
ok(_n_up > 1000, 'nearly every texture upscales (%d)' % _n_up)

ok(len(_SIZABLE) > 500,
   'the content gate keeps a usable set (%d)' % len(_SIZABLE))
ok(_MESH <= set(range(len(entries))), 'mesh indices are TOC indices')
ok(all(_cls[_i] in S.MODEL_CLASSES or _CELLS.get(_i) == S.CELL_WHOLE
       for _i in _SIZABLE),
   'the gate admits a .s-named texture only when the file IS the cell')
ok(not any(_CELLS.get(_i) in (S.CELL_SUBRECT, S.CELL_FOREIGN)
           for _i in _SIZABLE),
   'the gate never admits a texture a .s addresses by sub-rectangle')
ok(_by['model'] > 400,
   'a substantial model-texture set exists (%d)' % _by['model'])
ok(_by['fx'] > 100, 'the fx set is identified (%d)' % _by['fx'])
ok(sum(_by.values()) == len([i for i, e in enumerate(entries)
                             if tex.parse(e['payload']) is not None]),
   'every texture is classified')

# The .p files those model textures are drawn by really do use float UVs.
_pmax = 0.0
_pn = 0
for _e in entries:
    # Only real .p models. A loose header test also matches TEX files and
    # reads their pixels as floats, which is how this assertion first
    # reported a max |UV| of 3.3e38.
    if not _e['name'].lower().endswith('.p'):
        continue
    _d = _e['payload']
    if len(_d) < 0x80:
        continue
    _f = struct.unpack_from('<16i', _d, 0)
    _nv, _nn, _nt = _f[3], _f[4], _f[6]
    if not (0 < _nv < 200000 and 0 <= _nn < 200000 and 0 < _nt < 200000):
        continue
    _off = 0x80 + _nv * 12 + _nn * 12
    if _off + _nt * 8 > len(_d):
        continue
    _uv = struct.unpack_from('<%df' % (_nt * 2), _d, _off)
    _pmax = max(_pmax, max(abs(x) for x in _uv))
    _pn += 1
ok(_pn > 500, 'the .p models parse (%d)' % _pn)
ok(_pmax <= 1.5,
   'every .p texture coordinate is normalised (max |UV| %.2f) -- this is WHY '
   'model textures may be resized' % _pmax)

_sv2 = os.environ.get(S.CAP_ENV)
_svfx = os.environ.get(S.FX_CAP_ENV)
os.environ[S.CAP_ENV] = '512'
os.environ[S.FX_CAP_ENV] = '0'
try:
    _o, _t, _s2 = S.convert(archive, {}, os.path.join(HERE, 'cache',
                                                      '_spell_dds'),
                            log=lambda *a: None)
    ok(_t == {}, 'NO frame table is rewritten (%d were)' % len(_t))
    # BUILD 239. With the mod supplying nothing and the FX cap down, NOTHING
    # is rewritten. Enlarging a texture the mod does not replace only
    # replicates its texels -- it adds no picture and costs k^2 the bytes --
    # and it is needed ONLY to keep a CELL_SUBRECT texture in step with a
    # frame table that moved. No table moves here, so nothing has to grow.
    ok(_o == {},
       'a texture with no mod art is not enlarged for its own sake (%d were)'
       % len(_o))

    # Raise the FX cap and exactly the CELL_SUBRECT set moves, because those
    # are the ones whose tables move with them.
    os.environ[S.FX_CAP_ENV] = '512'
    _o2, _t2, _ = S.convert(archive, {}, os.path.join(HERE, 'cache',
                                                      '_spell_dds'),
                            log=lambda *a: None)
    # FINDINGS-253. A CELL_SUBRECT texture is addressed BY TEXEL, so it may
    # only grow together with the `.s` table that addresses it. The tables
    # have not moved since build 240, so raising the FX cap must now move
    # NOTHING -- growing the texture alone is what drew a third of the sheet
    # in a hard rectangle and chopped the scrolling backgrounds.
    _stuck2 = ({entries[_j]['name'].lower().rpartition('.')[0]
                for _j in _SCALABLE_S
                if S.scale_frame_table(entries[_j]['payload'], 2) is None}
               if S.fx_tables() else set())
    _want2 = ({_i for _i, _k in _CELLS.items()
               if _k == S.CELL_SUBRECT
               and not S._stem_stuck(entries[_i]['name'], _stuck2)}
              if S.fx_tables() else set())
    ok(set(_o2) == _want2,
       'FX cap up: a texel-addressed texture grows only with its table '
       '(%d grew, %d expected, fx_tables=%s)'
       % (len(_o2), len(_want2), S.fx_tables()))
    for _i, _d2 in _o2.items():
        _a2, _b2 = tex.parse(entries[_i]['payload']), tex.parse(_d2)
        ok(_b2['width'] == _a2['width'] * 2,
           '%s is 2x' % entries[_i]['name'])
    ok(all(_j in _SCALABLE_S for _j in _t2),
       'no table outside the SUBRECT set moved')
    ok(S.fx_tables() or _t2 == {},
       'BUILD 240: no frame table is rewritten at all by default (%d were)'
       % len(_t2))
    os.environ[S.FX_CAP_ENV] = '0'

    # The gate itself, checked as a property rather than through a
    # conversion, so it holds whatever the mod happens to supply.
    _o = {_i: None for _i in _SIZABLE}
    # THE BUILD 237 REGRESSION, stated as a licence rather than as a name:
    # every resized texture must have ONE of the two properties that make
    # its size unobservable. Either a .p mesh addresses it in normalised
    # floats -- in which case a packed sheet is fine, its cells are
    # fractions of the canvas -- or its drawn area is a single connected
    # sprite covering its own quad. A texture with neither is exactly what
    # build 237 promoted and what clipped.
    for _i in _o:
        if _i in _MESH or _CELLS.get(_i) == S.CELL_WHOLE:
            continue
        _m = S.drawn_mask(entries[_i]['payload'])
        _dr = _m[1] if isinstance(_m, tuple) else _m
        # 0 is a blank entry (nothing drawn); 1 is a single blob. Only 2+
        # -- a packed sheet -- is disqualifying.
        ok(ff7nx_ddstex.sprite_count(_dr) <= 1,
           '%s is a packed sheet that no mesh addresses and must NOT be '
           'resized' % entries[_i]['name'])
    # And the licence has to be REAL, not just claimed: every .p that grants
    # it stores nothing but normalised coordinates.
    for _i in _MESH:
        ok(_cls.get(_i) is not None,
           'mesh_uv_textures only ever names a texture entry')
    for _nm in ('blaver00.tex', 'blaver01.tex', 'atomic00.tex', 'zibaku1.tex',
                'bomb_a.tex', 'baku1.tex', 'hit1.tex', 'a_max.tex'):
        _ix = [_j for _j, _e in enumerate(entries)
               if _e['name'].lower() == _nm]
        ok(_ix and not any(_j in _o for _j in _ix),
           '%s (a known-broken sheet) is held at vanilla size' % _nm)
    # A CELL_WHOLE texture must really be its own single cell: the file is
    # the frame, which is the entire licence for resizing it.
    _whole = [_i for _i, _k in _CELLS.items() if _k == S.CELL_WHOLE]
    ok(len(_whole) > 20, 'the CELL_WHOLE set is found (%d)' % len(_whole))
    for _i in _whole:
        ok(_i in _SIZABLE,
           '%s is CELL_WHOLE and must be resizable' % entries[_i]['name'])
    # A CELL_SUBRECT texture is the one case where the .s really does hold
    # this texture's own texels, so its extent must fit inside it AND its
    # cells must actually contain what the file draws. The second half is
    # what build 239's first pass was missing: "the cells fit" is nearly
    # free for a 256x256 file, because a PSX page is 256 wide.
    import re as _re2
    _tabs = collections.defaultdict(list)
    for _e4 in entries:
        _n4 = _e4['name'].lower()
        if _n4.endswith('.s'):
            _r4 = S.parse_frame_table(_e4['payload'])
            if _r4:
                _tabs[_n4[:-2]].append(_r4)

    def _cells4(_name):
        _st = _name.lower().rpartition('.')[0]
        _o4 = list(_tabs.get(_st, ()))
        _alt = _re2.sub(r'[_]?\d+$', '', _st)
        if _alt != _st:
            _o4 += list(_tabs.get(_alt, ()))
        _c4 = set()
        for _r in _o4:
            _c4.update(_r)
        return {_x for _x in _c4 if _x[2] > 0 and _x[3] > 0}

    for _i, _k in _CELLS.items():
        if _k != S.CELL_SUBRECT:
            continue
        ok(_i not in _SIZABLE,
           '%s is texel-addressed and must follow the FX cap, not the model '
           'cap' % entries[_i]['name'])
        _t4 = tex.parse(entries[_i]['payload'])
        _d4 = S.drawn_mask(entries[_i]['payload'])[1]
        _m4 = np.zeros((_t4['height'], _t4['width']), dtype=bool)
        for _u4, _v4, _w4, _h4 in _cells4(entries[_i]['name']):
            _m4[_v4:_v4 + _h4, _u4:_u4 + _w4] = True
        _cov4 = float(_d4[_m4].sum()) / max(1.0, float(_d4.sum()))
        ok(_cov4 >= S.CELL_COVERAGE,
           '%s is called SUBRECT but only %.1f%% of its art is inside its '
           'cells' % (entries[_i]['name'], 100 * _cov4))
    # The ones whose tables describe a different part of the page. Every one
    # of these was SUBRECT before the pixel check and would have been scaled
    # at FX 768 with its table -- build 234's defect, by name.
    for _nm in ('blaver00.tex', 'blaver01.tex',
                'enmaku.tex', 'faiga_1.tex', 'fir_1.tex', 'exp2.tex',
                'flame_a.tex'):
        _ix = [_j for _j, _e in enumerate(entries)
               if _e['name'].lower() == _nm]
        ok(_ix and all(_CELLS.get(_j) == S.CELL_FOREIGN for _j in _ix),
           '%s must be FOREIGN: its cells do not contain its art' % _nm)

    # zibaku1 AND mini_1 WERE IN THAT LIST, AND THEY DID NOT BELONG THERE.
    # Both have +17 = 0 and +18 = 64 / 32. Reading the height from +17 made
    # every cell zero-sized, which tripped frame_cells' "a zero cell is not a
    # rectangle" guard and forced them FOREIGN -- a guard written to paper
    # over a parser bug rather than to describe the data. Read from +18 they
    # are textbook SUBRECT: 64px cells tiling zibaku1's 256x256 sheet four
    # across, 32px cells tiling mini_1's 256x128. FINDINGS-257.
    for _nm, _w in (('zibaku1.tex', 64), ('mini_1.tex', 32)):
        _ix = [_j for _j, _e in enumerate(entries)
               if _e['name'].lower() == _nm]
        ok(_ix and all(_CELLS.get(_j) == S.CELL_SUBRECT for _j in _ix),
           '%s is SUBRECT once the height comes from +18' % _nm)
        _t = [_e for _e in entries
              if _e['name'].lower() == _nm[:-4] + '.s'][0]
        _cs = S.parse_frame_table(_t['payload'])
        ok(all(_c[3] > 0 for _c in _cs),
           '%s: no cell is zero-height any more' % _nm)
        ok(max(_c[3] for _c in _cs) == _w,
           '%s cells are %dpx tall, not zero-sized (got %d)'
           % (_nm, _w, max(_c[3] for _c in _cs)))
finally:
    for _e, _v in ((S.CAP_ENV, _sv2), (S.FX_CAP_ENV, _svfx)):
        if _v is None:
            os.environ.pop(_e, None)
        else:
            os.environ[_e] = _v
print('  retired gate audit: %d formerly eligible (%d mesh-addressed, %d '
      'single sprite), %d formerly held (%d model-class); production does '
      'not use this gate'
      % (len(_SIZABLE), len(_SIZABLE & _MESH), len(_SIZABLE - _MESH),
         len(_cls) - len(_SIZABLE),
         sum(1 for _i in _cls if _i not in _SIZABLE
             and _cls[_i] in S.MODEL_CLASSES)))

# ---------------------------------------------- RETIRED TWO-KNOB HELPERS
#
# These helpers remain import-compatible with old settings and diagnostics.
# Production `convert()` ignores the FX value and never rewrites a table; the
# regression above asserts the active one-cap contract with real art.
_svm, _svf = os.environ.get(S.CAP_ENV), os.environ.get(S.FX_CAP_ENV)
_cache = os.path.join(HERE, 'cache', '_spell_dds')
try:
    for _mc, _fc, _wm, _wf in ((512, 0, 2, 1), (0, 512, 1, 2),
                               (768, 768, 3, 3), (0, 0, 1, 1)):
        os.environ[S.CAP_ENV], os.environ[S.FX_CAP_ENV] = str(_mc), str(_fc)
        ok(S.uniform_scale() == _wm and S.fx_scale() == _wf,
           'caps %d/%d -> x%d/x%d' % (_mc, _fc, S.uniform_scale(), S.fx_scale()))
        _o3, _t3, _ = S.convert(archive, {}, _cache, log=lambda *a: None)

        # WHAT SHOULD MOVE, STATED AS A SET RATHER THAN AS PER-CLASS COUNTS.
        #
        # BUILD 239. The FX cap no longer governs a CLASS, it governs the
        # textures whose `.s` really holds their own texels -- CELL_SUBRECT.
        # A CELL_WHOLE texture is its own single frame and follows the MODEL
        # cap; a CELL_FOREIGN one is addressed in a page space this file does
        # not span and never moves. See S.frame_cells().
        _stuck3 = ({entries[_j]['name'].lower().rpartition('.')[0]
                    for _j in _SCALABLE_S
                    if _wf > 1
                    and S.scale_frame_table(entries[_j]['payload'],
                                            _wf) is None}
                   if S.fx_tables() else set())

        # NOTE the asymmetry, and it is deliberate. These runs pass NO
        # targets, so nothing has the mod's art. A model-class texture then
        # has no reason to grow -- replicating its own texels adds nothing.
        # A CELL_SUBRECT one grows ONLY when its frame table grows with it
        # (FINDINGS-253): it is addressed by texel, so a texture that moved
        # without its coordinates draws a fraction of itself.
        def _expect(_i3):
            if (_CELLS.get(_i3) == S.CELL_SUBRECT and _wf > 1
                    and S.fx_tables()
                    and not S._stem_stuck(entries[_i3]['name'], _stuck3)):
                return _wf
            return 1

        _want_set = {_i3 for _i3 in _cls if _expect(_i3) > 1}
        ok(set(_o3) == _want_set,
           'caps %d/%d: %d textures rebuilt, expected %d (%d unexpected, '
           '%d missing)'
           % (_mc, _fc, len(_o3), len(_want_set),
              len(set(_o3) - _want_set), len(_want_set - set(_o3))))
        for _i3, _d3 in _o3.items():
            _va, _vb = tex.parse(entries[_i3]['payload']), tex.parse(_d3)
            ok(_vb['width'] == _va['width'] * _expect(_i3),
               '%s (%s/%s) at caps %d/%d is %dx, wanted %dx'
               % (entries[_i3]['name'], _cls.get(_i3), _CELLS.get(_i3),
                  _mc, _fc, _vb['width'] // max(1, _va['width']),
                  _expect(_i3)))

        # The frame tables follow the FX knob, and ONLY the tables that
        # address a CELL_SUBRECT texture are allowed to move at all.
        _nsc = sum(1 for _e3 in entries
                   if S.parse_frame_table(_e3['payload']) is not None)
        # FINDINGS-255: the factor is chosen PER STEM -- the largest one that
        # stem's own cells can express, never a single global number -- so a
        # table moves by its own k, which may be below the FX factor.
        def _stem_k(_j):
            if _wf <= 1 or not S.fx_tables():
                return 1
            for _c in range(_wf, 1, -1):
                if S.scale_frame_table(entries[_j]['payload'], _c) is not None:
                    return _c
            return 1
        # A stem served by several tables moves by the SLOWEST of them, or
        # the tables would disagree with each other about the same texture.
        _sk = {}
        for _j in _SCALABLE_S:
            _st = entries[_j]['name'].lower().rpartition('.')[0]
            _sk[_st] = min(_sk.get(_st, _stem_k(_j)), _stem_k(_j))
        _moves = sum(
            1 for _j in _SCALABLE_S
            if _sk[entries[_j]['name'].lower().rpartition('.')[0]] > 1
            and (S.scale_frame_table(
                entries[_j]['payload'],
                _sk[entries[_j]['name'].lower().rpartition('.')[0]])
                 != entries[_j]['payload']))
        ok(len(_t3) == _moves,
           'caps %d/%d: %d frame tables rewritten, expected %d'
           % (_mc, _fc, len(_t3), _moves))
        ok(all(_j in _SCALABLE_S for _j in _t3),
           'caps %d/%d: no table outside the SUBRECT set was rewritten'
           % (_mc, _fc))
finally:
    for _e, _v in ((S.CAP_ENV, _svm), (S.FX_CAP_ENV, _svf)):
        if _v is None:
            os.environ.pop(_e, None)
        else:
            os.environ[_e] = _v
print('  retired FX compatibility helpers are isolated; production uses one '
      'cap and leaves all %d frame tables unchanged' % _nsc)

# ------------------------------------------------------------- COMPLETENESS
#
# The rule that would have caught build 232 and 233 in the test rather than on
# a TV: uniform scaling is only correct if it reaches EVERYTHING. Partial
# coverage is not a partial success, it is a guaranteed mismatch on whatever
# was missed -- 121 frame tables in build 232, 24 `.t00`-`.t11` textures and
# 2 truecolor ones in build 233. Every count below is an equality, never a
# "most of them".
_sv = os.environ.get(S.CAP_ENV)
_svf2 = os.environ.get(S.FX_CAP_ENV)
os.environ[S.CAP_ENV] = '512'
os.environ[S.FX_CAP_ENV] = '0'
try:
    _out, _tbl, _st = S.convert(archive, {}, os.path.join(HERE, 'cache',
                                                          '_spell_dds'),
                                log=lambda *a: None)
    _all_tex = [i for i, e in enumerate(entries)
                if tex.parse(e['payload']) is not None]
    # Completeness is now scoped to the MODEL class: every model texture must
    # be reached (by content, not by extension -- 24 are named .t00..t11 and
    # 2 are truecolor), and nothing else may be touched.
    _model = [i for i in _all_tex if i in _SIZABLE]
    # BUILD 239. This used to demand that EVERY gated texture come out at 2x
    # even with no mod art, because uniform scaling required it. It does not
    # any more: with no art there is nothing to enlarge but the texture's own
    # texels, and nothing indexes a model texture by texel. The equality that
    # matters now is the OTHER one -- nothing may be touched that did not
    # have to be.
    ok(_out == {},
       'with no mod art and the FX cap down, NOTHING is rewritten (%d were: '
       '%r)' % (len(_out), [entries[i]['name'] for i in list(_out)[:6]]))
    for _i in _out:
        _a, _b = tex.parse(entries[_i]['payload']), tex.parse(_out[_i])
        ok(_b and (_b['width'], _b['height'])
           == (_a['width'] * 2, _a['height'] * 2),
           '%s is exactly 2x' % entries[_i]['name'])
    _all_tab = [i for i, e in enumerate(entries)
                if S.parse_frame_table(e['payload']) is not None]
    ok(len(_all_tab) == 806,
       'every .s file parses as a frame table (%d of 806)' % len(_all_tab))
    ok(_tbl == {},
       'no frame table is rewritten at all, because no texel-addressed '
       'texture changes size (%d were)' % len(_tbl))
finally:
    for _e, _v in ((S.CAP_ENV, _sv), (S.FX_CAP_ENV, _svf2)):
        if _v is None:
            os.environ.pop(_e, None)
        else:
            os.environ[_e] = _v
print('  completeness: %d gated textures, %d rewritten with no mod art '
      '(must be 0), %d frame tables left untouched'
      % (len(_model), len(_out), len(_all_tab)))

