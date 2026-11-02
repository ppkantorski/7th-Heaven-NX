#!/usr/bin/env python3
"""ff7nx_fxddssync.py -- additive FX cells on a truecolor page drawn the way
FFNx draws them: from Cosmos's DDS, never from the 1x paletted page.

BUILD 871. Hardware 11-01, corel3 (the Corel flashback fire): "weird
misplaced squares on its fire effect layer" -- a flat beige block in the sky
and hard-edged fire squares.

MEASURED. corel3's FX pages 17-22 are already 768px truecolor, but 19 of
their 413 cells do not hold Cosmos's art. Cosmos ships only `_00` (palette 0)
DDS for these pages and the tiles name palettes 7..10. FFNx falls back to
palette 0 unconditionally (saveload.cpp `load_normal_texture`), so on PC every
one of those cells is the `_00` DDS. field_bg_dense borrows palette 0 only
when the palettes are close (BORROW_MAX_DIST) and otherwise promotes the cell
FROM THE PALETTED PAGE -- Cosmos's own 1x chunk art. Where the DDS is empty
and the 1x cell is not (corel3 page 19 cell (1,7): DDS mean 0, built 28),
that is a square of old fire where PC draws nothing; where both have art it
is a 1x cell inside HD fire, i.e. a hard-edged square.

That borrow rule is right for opaque base pages (FINDINGS-151's brown side:
a borrowed palette changes the colour). It is wrong for blend-mode-1 FX on a
depth-2 page: the band draws additively (FINDINGS-194), the palette is never
applied, and FFNx's picture IS the `_00` DDS.

RULE (per cell; any doubt leaves it byte-identical):
  * the page is depth 2 in the additive band (ff7nx_fxpages FX_LO..FX_HI);
  * every record sampling the cell is an FX record with blend mode 1, and
    they all resolve to the same DDS image (exact palette, else `_00`, the
    FFNx order);
  * that (page, palette) has ONE Cosmos state (no hash frames: a pulse is
    ff7nx_fxcomplete's median-frame business, not this pass's);
  * the cell differs from the DDS by more than MIN_DIFF (mean 0..255 per
    channel) -- cells already equal are not rewritten.
The cell is then re-encoded from the DDS exactly as ff7nx_fxpages encodes a
page (alpha premultiplied into additive colour, one R5G6B5 write).

SCOPED TO FIELDS. The same rule finds cells in ~30 other fields, and many
of them differ ON PURPOSE or were accepted on hardware as they are: las0_2
(fxtrial's polish and edge fade), the pulse composites (median frame,
borrowed brightness), del3 (fxsmooth), the fxmargin fields. A blanket
rewrite would undo those. Measured candidates on build 870, for later
hardware review one at a time: eals_1 191, smkin_4 200, icedun_2 223,
junbin5 157, semkin_4 82, spipe_1 76, blin70_1 46, las2_3 46, sininb34 63,
mds7st32/33 23, mrkt2 22, games 20, ancnt2 18, las2_1 17, mogu_1 15.

SEVENTH_NX_NO_FX_DDS_SYNC=1 disables.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import collections
import os
import struct

OFF_ENV = 'SEVENTH_NX_NO_FX_DDS_SYNC'
MIN_DIFF = 6.0
FIELDS = frozenset(('corel3',))
BLEND_ADDITIVE = 1


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _dec565(c):
    import numpy as np
    c = c.astype(np.uint32)
    return np.stack([((c >> 11) & 31) * 255 // 31,
                     ((c >> 5) & 63) * 255 // 63,
                     (c & 31) * 255 // 31], -1).astype(np.float32)


def plan_field(name, sec9, art):
    """(new section 9, {slot: cells rewritten}, [(x, y, slot, cell, diff)]).
    Raises ValueError('nothing to sync') when no cell changes."""
    import numpy as np
    import diag_common as DC
    import field_bg_native as FN
    import ff7nx_fxmargin as FXM
    import ff7nx_fxpages as FXP
    provider = getattr(art, 'provider', None)
    if provider is None:
        raise ValueError('no DDS provider')
    pl, ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pl if p is not None}
    # every record sampling each (slot, cell), FX or not
    users = collections.defaultdict(list)
    for _layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            fx = bool(sec9[o + 28])
            slot = sec9[o + 34] if fx else sec9[o + 32]
            p = pm.get(slot)
            if p is None or p.depth != 2:
                continue
            g = 8 if p.size_flag else 16
            if fx:
                u, v = struct.unpack_from('<II', sec9, o + 42)
                cell = (int(round(u / 1e7 * g)), int(round(v / 1e7 * g)))
            else:
                sx, sy = struct.unpack_from('<hh', sec9, o + 10)
                cell = (sx * g // 256, sy * g // 256)
            users[(slot, cell)].append((fx, sec9[o + 30], sec9[o + 22], o))
    images = {}

    def dds(slot, pal):
        key = (slot, pal)
        if key not in images:
            img = None
            states = getattr(provider, 'state_slots', {}).get(
                (name.lower(), slot, pal)) or ()
            if len(states) <= 1:
                img = FXM._provider_rgba(art, name, slot, pal, px)
            images[key] = (img, len(states))
        return images[key]

    datas = {}
    changed = collections.Counter()
    report = []
    for (slot, cell), recs in sorted(users.items()):
        if not (FXP.FX_LO <= slot < FXP.FX_HI):
            continue
        if not all(fx and bl == BLEND_ADDITIVE for fx, bl, _q, _o in recs):
            continue
        srcs = set()
        for _fx, _bl, q, _o in recs:
            img, nstates = dds(slot, q)
            if img is None and nstates == 0:
                img, nstates = dds(slot, 0)
                srcs.add((0, nstates, img is None))
            else:
                srcs.add((q, nstates, img is None))
        if len(srcs) != 1:
            continue
        src_pal, nstates, missing = srcs.pop()
        img = images[(slot, src_pal)][0]
        if missing or img is None or nstates > 1:
            continue
        p = pm[slot]
        g = 8 if p.size_flag else 16
        cs = p.px // g
        cu, cv = cell
        if p.px != px or img.shape[:2] != (px, px):
            continue
        if slot not in datas:
            datas[slot] = np.frombuffer(p.data, '<u2').reshape(
                p.px, p.px).copy()
        cur = datas[slot][cv * cs:(cv + 1) * cs, cu * cs:(cu + 1) * cs]
        ref = img[cv * cs:(cv + 1) * cs, cu * cs:(cu + 1) * cs]
        want = ref[..., :3].astype(np.float32) * ref[..., 3:4] / 255.0
        diff = float(np.abs(want - _dec565(cur)).mean())
        if diff <= MIN_DIFF:
            continue
        enc = np.frombuffer(FXP._encode_additive(
            np.ascontiguousarray(ref), cs), '<u2').reshape(cs, cs)
        cur[...] = enc
        changed[slot] += 1
        x, y = struct.unpack_from('<hh', sec9, recs[0][3] + 2)
        report.append((x, y, slot, cell, round(diff, 1)))
    if not changed:
        raise ValueError('nothing to sync')
    plist, t0, t1 = FN.parse_texture_block(sec9, px)
    for slot, d in datas.items():
        if not changed[slot]:
            continue
        q = plist[slot]
        plist[slot] = FN.Page(slot, q.size_flag, 2, d.tobytes(), q.px)
    return FN.replace_texture_block(sec9, plist, t0, t1), dict(changed), \
        report


def apply_to_flevel(archive, payloads, art, encode=None, names=None,
                    log=lambda *_: None):
    import lgp
    total = {'names': [], 'cells': 0, 'refused': []}
    if disabled() or art is None:
        return total
    for name in (names or sorted(FIELDS)):
        if name.lower() not in FIELDS:
            continue
        entry = archive.index.get(name)
        if entry is None:
            continue
        try:
            if not archive.is_field(entry):
                continue
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            if parts[8].find(b'BACK') < 0:
                continue
            parts[8], changed, _rep = plan_field(name, parts[8], art)
        except ValueError as exc:
            if not str(exc).startswith('nothing to sync'):
                total['refused'].append((name, str(exc)[:80]))
            continue
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append((name, str(exc)[:80]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        n = sum(changed.values())
        total['cells'] += n
        total['names'].append('%s %d' % (name, n))
    return total


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  FX DDS SYNC (BUILD 871): %d additive FX cell(s) in %d '
                   'field(s) redrawn from Cosmos\'s DDS, as FFNx draws them '
                   '(%s). %s=1 disables.'
                   % (st['cells'], len(st['names']),
                      ', '.join(st['names'][:12])
                      + (' ...' if len(st['names']) > 12 else ''), OFF_ENV))
    for name, why in st.get('refused', ())[:6]:
        out.append('  ! fx dds sync %s: %s' % (name, why))
    return '\n'.join(out)
