#!/usr/bin/env python3
"""ff7nx_lightning.py -- sichi's lightning in HD truecolor, the 1997 fade
kept. BUILD 668.

WHAT WAS WRONG (hardware 10-13, sichi -- the Midgar Zolom on the spike):
the four bolts (bg param 1, states 1/2/4/8, palettes 10..13, additive, on
the 256px paletted FX page 15) were blurry purple smears with square cells
cut out of them. Each bolt flashes and fades: its routine sets var5[k] = 66
and, one frame at a time, MPPAL multiplies its palette by var/64 while var
steps down by 3 to 0. Palette-animated, so the build kept the page paletted
and 1x -- requantised from Cosmos's PER-PALETTE frames (`sichi_15_1x_<hash>`),
which are soft AI glows that are not the bolts (the crisp bolts and their
glow are gone), drawn cell by cell, so a glow cell beside a cell that has
none ends in a hard square edge.

Cosmos's own base sheet `sichi_15_00.dds` is the 1997 page, 4x, crisp: it
matches every bolt cell under the record's own palette to 1.3/255 (mean
abs, 167 cells). That is the picture to draw.

THE ROUTE (ff7nx_palstates' 'levels' mode). Each bolt is drawn at 9 light
levels as truecolor HD cells = sichi_15_00 x v/64 on the additive FX pages
(11 levels would be 37.4 MB, over the 35 MB field cap; 9 is 34.1). Level j
is background parameter 2 + j (unused by sichi); each copy keeps its bolt's
state bit. The routines' palette ops are replaced, byte for byte in place:
var5[k] now walks 10 -> 2, each step BGOFF var,k; var -= 1; BGON var,k;
WAIT 1; BACK -- two frames a level, 18 frames (1997: 22, v 63 -> 0 by 3 a
frame); level j shows the mean of the same straight fade over its frames.
Bolt 0's opening flicker (1997: 8/64) uses the level nearest 8/64. The
palettes are no longer sampled by anything; page 15's paletted art is
dropped and its slot reused.

SEVENTH_NX_NO_LIGHTNING=1 disables.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_LIGHTNING'
P0, LEVELS = 2, 9                          # params 2..10 (11 levels do not
                                           # fit the 35 MB field cap: 37.4)
HOLD = 2                                   # frames per level

# field -> (FX page, ((palette, state index, var5 index), ...))
FIELDS = {
    'sichi': (15, ((10, 0, 0), (11, 1, 1), (12, 2, 2), (13, 3, 3))),
}


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def level_v(j):
    """The fade value level j (0 = dimmest) stands for. 1997: v = 63 - 3t,
    one value a frame, 22 frames. Here HOLD frames a level, LEVELS levels,
    the same straight line over LEVELS * HOLD frames, each level the mean
    of the frames it is shown."""
    i = LEVELS - 1 - j                     # shown i-th, the top first
    t = HOLD * i + (HOLD - 1) / 2.0
    return 63.0 * (1.0 - t / (HOLD * LEVELS))


def dim_level():
    """bolt 0's opening flicker: 1997 shows it at 8/64"""
    return min(range(LEVELS), key=lambda j: abs(level_v(j) - 8.0))


def _h(*parts):
    return bytes.fromhex(''.join(parts))


def _loop(var, st):
    """IFUB var > P0 / BGOFF var,st / MINUS var 1 / BGON var,st / WAIT 1 /
    BACK, then BGOFF var,st and RET."""
    body = _h('e150%02x%02x' % (var, st), '8750%02x01' % var,
              'e050%02x%02x' % (var, st), '240100')
    jump = len(body) + 2 + 1            # past the BACK, from the jump byte
    ifub = _h('1450%02x%02x02%02x' % (var, P0, jump))
    back = bytes((0x12, len(ifub) + len(body)))
    return ifub + body + back + _h('e150%02x%02x' % (var, st), '00')


def patches():
    """[(old bytes, new bytes, count)] for sichi's four bolt routines."""
    top = P0 + LEVELS - 1
    out = []
    # bolt 0: STPAL, WAIT 1, MPPAL x8, CPPAL, LDPAL; sound; flicker; sound;
    # BGON; the fade loop
    snd0 = 'f2000000283f3302000000000000'
    old0 = _h('80500042', 'e5000a007f', '240100',
              'ea00000000080a0a0a7f', 'e700000800', 'e600080a7f', snd0,
              'e0000100', '240200', 'e1000100', '240200', 'f10033023f',
              'e0000100', '14500000021b', '78500003',
              'ea00555000080000007f', 'e700000800', 'e600080a7f', '121e',
              'e1000100', '00')
    new0 = (_h('805000%02x' % top, '240100', snd0,
               'e000%02x00' % (P0 + dim_level()), '240200',
               'e100%02x00' % (P0 + dim_level()),
               '240200', 'f10033023f', 'e0500000', '24%02x00' % HOLD)
            + _loop(0, 0))
    out.append((old0, new0 + b'\0' * (len(old0) - len(new0)), 1))
    for k, pal in ((1, 0x0B), (2, 0x0C), (3, 0x0D)):
        snd = 'f2000000%02x3f3302000000000000' % (0x28 + k)
        buf = 0x10 * k
        old = _h('805%03x42' % k, snd, 'e00001%02x' % k,
                 'e500%02x%02x7f' % (pal, buf),
                 '1450%02x00021b' % k, '7850%02x03' % k,
                 'ea005550%02x%02x%02x%02x%02x7f' % (buf, buf + 8, k, k, k),
                 'e700%02x%02x00' % (buf, buf + 8),
                 'e600%02x%02x7f' % (buf + 8, pal), '121e',
                 'e10001%02x' % k, '00')
        new = (_h('8050%02x%02x' % (k, top), snd,
                  'e050%02x%02x' % (k, k), '24%02x00' % HOLD) + _loop(k, k))
        out.append((old, new + b'\0' * (len(old) - len(new)), 1))
    return out


def patch_script(sec0):
    out = bytes(sec0)
    for old, new, count in patches():
        if len(old) != len(new):
            raise ValueError('patch length %d != %d' % (len(old), len(new)))
        if out.count(old) != count:
            raise ValueError('script bytes %s... found %d times, expected %d'
                             % (old[:6].hex(), out.count(old), count))
        out = out.replace(old, new)
    return out


def level_art(art, name, page, vcells, px):
    """{vcell: [level 0 img, ...]} -- Cosmos's base sheet (palette 0, the
    1997 page at 4x) x v_j/64, premultiplied float32 (k, k, 3)."""
    import ff7nx_palstates as PS
    prov = art.provider
    rec = prov.slots.get((name.lower(), page, 0))
    if rec is None:
        raise ValueError('no Cosmos base sheet for %s page %d' % (name, page))
    cells = sorted({c[1:] for c in vcells})
    fr = PS._cells_of(prov, rec, cells, px)
    out = {}
    for j, c in enumerate(cells):
        base = fr[j]
        out[(page,) + c] = [np.clip(base * (level_v(lv) / 64.0), 0, 255)
                            for lv in range(LEVELS)]
    return out


def plan_field(name, parts, vparts, art, mb_cap=35.0, raw_cap=None):
    """(sec0, sec9, info) -- raises on any doubt."""
    import diag_common as DC
    import ff7nx_palstates as PS
    page, bolts = FIELDS[name]
    sec0 = patch_script(parts[0])
    pl, _ts, _te, px = DC.parse_pages(parts[8])
    pm = {p.slot: p for p in pl if p is not None}
    specs = []
    for pal, st, _var in bolts:
        tg = PS._targets(parts[8], pm, pal, 1)
        if any(r[27] != 1 << st for _l, _i, r in tg):
            raise ValueError('palette %d records are not all state %d'
                             % (pal, st))
        vc = PS._vanilla_cells(tg, vparts[8])
        if any(c[0] != page for c in vc.values()):
            raise ValueError('palette %d: vanilla cells off page %d'
                             % (pal, page))
        specs.append({'palette': pal, 'mode': 'levels',
                      'params': tuple(range(P0, P0 + LEVELS)),
                      'states': tuple(range(LEVELS)), 'record_param': 1,
                      'bit_from_record': True,
                      'art': level_art(art, name, page, set(vc.values()), px)})
    return PS.plan_field(name, parts, vparts, art, mb_cap, raw_cap,
                         specs=specs, sec0=sec0)


def apply_to_flevel(archive, payloads, art, encode=None, mb_cap=35.0,
                    raw_cap=None, log=lambda *_: None):
    import lgp
    total = {'names': [], 'refused': []}
    if disabled() or art is None or getattr(art, 'provider', None) is None:
        return total
    encode = encode or archive.encode_field
    for name in FIELDS:
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            van = archive.decompressed(entry)
            raw = lgp.lzs_decompress(p[4:]) if p else van
            parts = list(lgp.split_sections(raw))
            vparts = list(lgp.split_sections(van))
            parts[0], parts[8], info = plan_field(name, parts, vparts, art,
                                                  mb_cap, raw_cap)
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append((name, str(exc)[:120]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['names'].append('%s: %d records -> %d, pages %s, dropped %s, '
                              '%.1f MB' % (name, info['removed'],
                                           info['added'], info['slots'],
                                           info['dropped'], info['mb']))
    return total


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  LIGHTNING (BUILD 668): bolts drawn from Cosmos\'s crisp '
                   'base sheet in truecolor, the 1997 fade as %d levels (%s). '
                   '%s=1 disables.' % (LEVELS, ' | '.join(st['names']),
                                       OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! lightning %s: %s' % (name, why))
    return '\n'.join(out)
