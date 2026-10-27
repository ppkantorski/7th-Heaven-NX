#!/usr/bin/env python3
"""ff7nx_hillfix.py -- hill / hill2: clean cut-out edges and a sky that
reaches the top of the frame. BUILD 710.

Hardware after build 709 (hill2, the morning before the crater):

  * "floating black squares" and "black jagged pixels" all round the
    Highwind (layer 4);
  * "dirtiness" along the grass horizon (layers 1/2 against the sky);
  * "a small black lip at the top" while the camera is on the airship.

MEASURED on the user's built 709 flevel against Cosmos's own art at the
same field positions (texels at 3 per unit):

  hill2 layer 4    3,647 texels drawn where Cosmos paints NOTHING
                   (native alpha 0 across the whole texel footprint);
                   almost none near-black, so dense's MODCLEAR_KEY
                   (black-only on parallax, it computes no cover for
                   layers 3/4) leaves every one of them. They are the
                   1997 silhouette, one unit = 3 texels a step, sticking
                   out of Cosmos's outline: the squares and the steps.
  hill2 layers 1/2   1,327 such texels along the horizon.

Cosmos's own edge is soft (page 7, the ship: ~6,400 texels with alpha
between 1 and 254). FFNx blends them; a truecolor page here has no alpha,
so each was drawn at full strength in Cosmos's colour, which along the
silhouette is a dark outline. That is the "dirty" rim.

WHAT THIS DOES, per field in FIELDS, on the final payload:

  1. EDGES (layers in `edges`, truecolor pages only). Each record is
     matched with Cosmos's records of the same layer and the same
     animation group (param, state), and Cosmos's alpha is laid out in
     field space for that group.
       * a texel Cosmos paints nowhere (native max alpha 0) is keyed;
       * where `sky` is given, a texel with Cosmos alpha a in
         [AA_LO, AA_HI) that has nothing but sky behind it is blended
         a * colour + (1 - a) * sky -- what FFNx draws, against the sky's
         measured colour. A texel another layer draws behind is never
         blended (keyed or left only).
     A cell shared by records that would want different results is left
     as it was.
  2. THE OPENING SHOT STARTS LOWER (hill2, START_LOWER). The shot of the
     airship opened with ~9 units of nothing above the sky: the port shows
     240 rows where 1997 showed 224. The fix is the camera, as on every
     other field with this problem: the shot starts lower. Here the sky
     and the ship are screen-pinned layers placed by BGSCR, so "the
     camera" is that setup move; see START_LOWER. No art is added.

  3. NEAR-BLACK SPECKS ON THE SKYLINE (hill2, PEEL; BUILD 712). Hardware
     on 710b: "a little black on the edge, and this little black protruding
     part" on the ridge you walk on, right of Cloud. Both are in Cosmos's
     own art at full alpha (a near-black lump standing on the ridge line,
     and a dotted near-black rim under it), so steps 1/2 kept them. Inside
     PEEL's box, a near-black run (mean RGB < lum) that touches the open
     sky and is small (<= max texels, so the black margin columns are never
     touched) is keyed. The sky shows through, and the ground edge is the
     colour of the ground.

SEVENTH_NX_NO_HILL_FIX=1 disables.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_HILL_FIX'
TILE_SIZE = 52
AA_LO = 0.25
AA_HI = 0.97
# layer -> sky colour (RGB) for the AA blend, or None for keying only.
# hill2's colours are the median sky in the user's 709 screenshots: around
# the airship, and just above the horizon.
FIELDS = {
    'hill2': {'edges': {4: (56, 102, 112), 1: (70, 115, 115),
                        2: (70, 115, 115)}},
    'hill': {'edges': {4: None, 1: None, 2: None}},
}


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _rgbf(t):
    t = t.astype(np.int64)
    return np.stack([((t >> 11) & 31) << 3, ((t >> 6) & 31) << 3,
                     (t & 31) << 3], -1).astype(np.float32)


def _enc(c):
    """RGB (0..255) -> 565 with green's low bit clear, never 0."""
    q = np.clip(np.rint(c / 8.0), 0, 31).astype(np.int64)
    v = (q[..., 0] << 11) | (q[..., 1] << 6) | q[..., 2]
    return np.where(v == 0, 1, v).astype(np.uint16)


def _recs(s9, layer):
    import ff7nx_parallaxfill as PF
    back, tex = s9.find(b'BACK'), s9.find(b'TEXTURE')
    for lay, count_at, first, n in PF._layers(s9, back, tex):
        if lay == layer:
            return count_at, first, n
    return None


UV_SCALE = 10_000_000
T_SRC_X_BIG = 42


def _cell(s9, o, page):
    """The record's source cell origin in 256-space units, from the u/v the
    engine samples with (render_field's rule)."""
    grid = 8 if page.size_flag else 16
    u, v = struct.unpack_from('<II', s9, o + T_SRC_X_BIG)
    return (int(round(u / UV_SCALE * grid)) * (256 // grid),
            int(round(v / UV_SCALE * grid)) * (256 // grid))


def _size(s9, o, page):
    if page is not None and page.size_flag:
        return 32
    n = struct.unpack_from('<H', s9, o + 18)[0]
    return n if n in (16, 32) else 16


def cosmos_alpha(name, layer, chunk9, art_open):
    """{(param, state): (x0, y0, alpha, amax)} -- Cosmos's coverage of one
    layer in field space, texels at 3 per unit, one canvas per animation
    group. `art_open(field)` -> f(page, palette) -> PageArt."""
    import diag_common as DC
    import ff7nx_marginblack as MB
    cs = DC.survey(chunk9)
    cp = {p.slot: p for p in cs['pages']}
    f = art_open(name)
    groups = {}
    for t in MB.read_tiles(chunk9, cs, cp):
        if t.layer != layer:
            continue
        g = (chunk9[t.off + 26], chunk9[t.off + 27])
        groups.setdefault(g, []).append(t)
    out = {}
    for g, ts in groups.items():
        n_of = {t.off: (32 if getattr(cp.get(t.slot), 'size_flag', 0)
                        else 16) for t in ts}
        x0 = min(t.dx for t in ts)
        y0 = min(t.dy for t in ts)
        w = max(t.dx + n_of[t.off] for t in ts) - x0
        h = max(t.dy + n_of[t.off] for t in ts) - y0
        al = np.zeros((h * 3, w * 3), np.float32)
        am = np.zeros((h * 3, w * 3), np.uint8)
        ok = True
        for t in ts:
            art = f(t.slot, t.pal)
            if art is None:
                for q in sorted(getattr(f, '__self__', None).palettes(t.slot)
                                if hasattr(f, '__self__') else ()):
                    art = f(t.slot, q)
                    if art is not None:
                        break
            if art is None or art.alpha is None or art.amax is None:
                ok = False
                break
            a = np.asarray(art.alpha)
            m = np.asarray(art.amax)
            kk = a.shape[0] // 256
            n = n_of[t.off]
            ba = a[t.sy * kk:(t.sy + n) * kk, t.sx * kk:(t.sx + n) * kk]
            bm = m[t.sy * kk:(t.sy + n) * kk, t.sx * kk:(t.sx + n) * kk]
            T = n * 3
            if ba.shape != (n * kk, n * kk):
                ok = False
                break
            if kk != 3:
                idx = (np.arange(T) * kk) // 3
                ba, bm = ba[idx][:, idx], bm[idx][:, idx]
            Y, X = (t.dy - y0) * 3, (t.dx - x0) * 3
            al[Y:Y + T, X:X + T] = np.maximum(al[Y:Y + T, X:X + T],
                                              ba.astype(np.float32) / 255.)
            am[Y:Y + T, X:X + T] = np.maximum(am[Y:Y + T, X:X + T], bm)
        if ok:
            out[g] = (x0, y0, al, am)
    return out


def _behind(s9, pm, px, layer, x0, y0, w, h):
    """Where a lower static layer (1 for 2) or this layer's other records
    could draw: True = not sky. Only layers 1 and 2 are judged; parallax
    (3/4) is the sky."""
    m = np.zeros((h, w), bool)
    if layer != 2:
        return m
    r = _recs(s9, 1)
    if r is None:
        return m
    _c, first, n = r
    k = px // 256
    for i in range(n):
        o = first + i * TILE_SIZE
        p = pm.get(s9[o + 32])
        if p is None:
            continue
        x, y = struct.unpack_from('<hh', s9, o + 2)
        sz = _size(s9, o, p)
        Y, X, T = (y - y0) * 3, (x - x0) * 3, sz * 3
        if Y >= h or X >= w or Y + T <= 0 or X + T <= 0:
            continue
        if p.depth == 2:
            sx, sy = _cell(s9, o, p)
            d = np.frombuffer(p.data, '<u2').reshape(p.px, p.px)[
                sy * k:sy * k + T, sx * k:sx * k + T] != 0
        else:
            d = np.ones((T, T), bool)
        ya, xa = max(Y, 0), max(X, 0)
        yb, xb = min(Y + T, h), min(X + T, w)
        m[ya:yb, xa:xb] |= d[ya - Y:yb - Y, xa - X:xb - X]
    return m


def clean_edges(name, s9, chunk9, art_open, layers):
    """(new s9, {layer: stats}) -- see the module docstring, step 1."""
    import diag_common as DC
    import field_bg_native as FN
    pl, ts, _te, px = DC.parse_pages(s9)
    pm = {p.slot: p for p in pl if p is not None}
    if px // 256 != 3:
        raise ValueError('pages are %d px, need 768' % px)
    k = 3
    datas = {}
    info = {}
    for layer, sky in layers.items():
        r = _recs(s9, layer)
        if r is None:
            continue
        _c, first, n = r
        cos = cosmos_alpha(name, layer, chunk9, art_open)
        if not cos:
            info[layer] = {'refused': 'no Cosmos art for the layer'}
            continue
        st = {'keyed': 0, 'blended': 0, 'conflict': 0, 'nogroup': 0,
              'paletted': 0}
        want = {}            # (slot, sx, sy, size) -> new block
        orig = {}
        behind = {}
        skyc = None if sky is None else np.asarray(sky, np.float32)
        for i in range(n):
            o = first + i * TILE_SIZE
            p = pm.get(s9[o + 32])
            if p is None:
                continue
            if p.depth != 2:
                st['paletted'] += 1
                continue
            g = (s9[o + 26], s9[o + 27])
            c = cos.get(g)
            if c is None:
                st['nogroup'] += 1
                continue
            cx0, cy0, al, am = c
            x, y = struct.unpack_from('<hh', s9, o + 2)
            sx, sy = _cell(s9, o, p)
            sz = _size(s9, o, p)
            T = sz * 3
            Y, X = (y - cy0) * 3, (x - cx0) * 3
            if Y < 0 or X < 0 or Y + T > al.shape[0] or X + T > al.shape[1]:
                st['nogroup'] += 1
                continue
            a = al[Y:Y + T, X:X + T]
            mx = am[Y:Y + T, X:X + T]
            key = (p.slot, sx, sy, sz)
            if key not in orig:
                if p.slot not in datas:
                    datas[p.slot] = np.frombuffer(p.data, '<u2').reshape(
                        p.px, p.px).copy()
                orig[key] = datas[p.slot][sy * k:sy * k + T,
                                          sx * k:sx * k + T].copy()
            blk = orig[key].copy()
            drawn = blk != 0
            kill = drawn & (mx == 0)
            blk[kill] = 0
            if skyc is not None:
                if g not in behind:
                    behind[g] = _behind(s9, pm, px, layer, cx0, cy0,
                                        al.shape[1], al.shape[0])
                bh = behind[g][Y:Y + T, X:X + T]
                soft = drawn & ~kill & (a >= AA_LO) & (a < AA_HI) & ~bh
                if soft.any():
                    col = _rgbf(blk[soft])
                    w = a[soft][:, None]
                    blk[soft] = _enc(w * col + (1 - w) * skyc)
            prev = want.get(key)
            if prev is None:
                want[key] = blk
            elif prev is not False and not np.array_equal(prev, blk):
                want[key] = False
        for key, blk in want.items():
            slot, sx, sy, sz = key
            T = sz * 3
            if blk is False:
                st['conflict'] += 1
                continue
            o_ = orig[key]
            st['keyed'] += int(((o_ != 0) & (blk == 0)).sum())
            st['blended'] += int(((o_ != 0) & (blk != 0) & (blk != o_)).sum())
            datas[slot][sy * k:sy * k + T, sx * k:sx * k + T] = blk
        info[layer] = st
    if not any(v.get('keyed') or v.get('blended') for v in info.values()):
        return s9, info
    plist, t0, t1 = FN.parse_texture_block(s9, px)
    for slot, d in datas.items():
        q = plist[slot]
        plist[slot] = FN.Page(slot, q.size_flag, 2, d.tobytes(), q.px)
    return FN.replace_texture_block(s9, plist, t0, t1), info


# hill2's opening shot of the airship. The director moves layers 3 and 4
# (the sky and the ship; both pinned to the screen, speed 0, so the field
# camera does not move them) with BGSCR during the fade-in:
#     L3/L4 -40 for 30 ticks, -5 for 5        (1997, 30 ticks a second)
# ff7nx_bgscr60 halves that for 60 ticks a second (-20 / -2). The shot opens
# at the end of that move, and there the uncropped 240-row view shows
# 9.6 units above the top of the sky's art (measured: bar = -net - 32, with
# net = 35 - 75 - 1.25 units). The camera has to start lower.
#
# START_LOWER makes the setup move shorter by the same amount on both layers,
# so the sky and the ship keep their relation and the shot simply opens
# lower: -20 -> -16 is 15 units less travel, about 6 units of sky above the
# top of the view instead of 9.6 of nothing. The move happens while the screen
# is black, so its speed is never seen; nothing after it changes.
# field -> {layer: (speed after ff7nx_bgscr60, new speed)}; the 1997 values
# (when ff7nx_bgscr60's timed halving is off) are scaled by 2.
START_LOWER = {'hill2': {3: (-20, -16), 4: (-20, -16)}}


def lower_start(name, script):
    """(new script, [(layer, old_y, new_y)]) -- raises unless exactly one
    constant (0, old) BGSCR per listed layer is found."""
    import ff7nx_bgscr60 as B
    want = START_LOWER[name]
    sec = bytearray(script)
    edits = []
    for layer, (old, new) in sorted(want.items()):
        hits = [(off, sy) for off, banks, lay, (sx, sy), _n in B._ops(script)
                if lay == layer and not banks and sx == 0
                and sy in (old, 2 * old)]
        if len(hits) != 1:
            raise ValueError('layer %d: %d setup moves, want 1'
                             % (layer, len(hits)))
        off, sy = hits[0]
        ny = new if sy == old else 2 * new
        struct.pack_into('<h', sec, off + 5, ny)
        edits.append((layer, sy, ny))
    return bytes(sec), edits


# field -> the skyline region to clean, in field units (x0, y0, x1, y1), the
# layers, the near-black threshold (mean RGB) and the largest run keyed.
# The box is the right-hand ridge (x >= 128); the dark rocks on the left
# ridge are part of the painting and stay.
PEEL = {'hill2': {'box': (128, 64, 224, 112), 'layers': (1, 2),
                  'lum': 40, 'max': 150}}


def _dil(m):
    q = m.copy()
    q[1:] |= m[:-1]
    q[:-1] |= m[1:]
    q[:, 1:] |= m[:, :-1]
    q[:, :-1] |= m[:, 1:]
    return q


def _label(m):
    """8-connected labels of a bool mask."""
    from collections import deque
    lab = np.zeros(m.shape, np.int32)
    h, w = m.shape
    k = 0
    for y0, x0 in zip(*np.nonzero(m)):
        if lab[y0, x0]:
            continue
        k += 1
        lab[y0, x0] = k
        q = deque([(y0, x0)])
        while q:
            y, x = q.popleft()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if (0 <= yy < h and 0 <= xx < w and m[yy, xx]
                            and not lab[yy, xx]):
                        lab[yy, xx] = k
                        q.append((yy, xx))
    return lab


def peel_specks(s9, cfg):
    """(new s9, {layer: stats}) -- see the module docstring, step 3."""
    import diag_common as DC
    import field_bg_native as FN
    pl, _ts, _te, px = DC.parse_pages(s9)
    pm = {p.slot: p for p in pl if p is not None}
    if px // 256 != 3:
        raise ValueError('pages are %d px, need 768' % px)
    bx0, by0, bx1, by1 = cfg['box']
    datas = {}
    info = {}
    for layer in cfg['layers']:
        r = _recs(s9, layer)
        if r is None:
            continue
        _c, first, n = r
        recs = []
        for i in range(n):
            o = first + i * TILE_SIZE
            p = pm.get(s9[o + 32])
            if p is None or p.depth != 2:
                continue
            x, y = struct.unpack_from('<hh', s9, o + 2)
            sx, sy = _cell(s9, o, p)
            recs.append(((s9[o + 26], s9[o + 27]), x, y, p, sx, sy,
                         _size(s9, o, p)))
        st = {'keyed': 0, 'conflict': 0}
        want = {}
        orig = {}
        for g in sorted(set(r_[0] for r_ in recs)):
            rr = [r_ for r_ in recs if r_[0] == g]
            x0 = min(r_[1] for r_ in rr)
            y0 = min(r_[2] for r_ in rr)
            w = (max(r_[1] + r_[6] for r_ in rr) - x0) * 3
            h = (max(r_[2] + r_[6] for r_ in rr) - y0) * 3
            # one texel of padding that is never "open": the layer's own
            # border is not sky
            cv = np.zeros((h + 2, w + 2), np.uint16)
            for _g, x, y, p, sx, sy, sz in rr:
                if p.slot not in datas:
                    datas[p.slot] = np.frombuffer(p.data, '<u2').reshape(
                        p.px, p.px).copy()
                T = sz * 3
                blk = datas[p.slot][sy * 3:sy * 3 + T, sx * 3:sx * 3 + T]
                Y, X = (y - y0) * 3 + 1, (x - x0) * 3 + 1
                reg = cv[Y:Y + T, X:X + T]
                reg[blk != 0] = blk[blk != 0]
            drawn = cv != 0
            bh = np.zeros_like(drawn)
            bh[1:-1, 1:-1] = _behind(s9, pm, px, layer, x0, y0, w, h)
            openm = ~drawn & ~bh
            openm[0, :] = openm[-1, :] = openm[:, 0] = openm[:, -1] = False
            dark = drawn & (_rgbf(cv).mean(-1) < cfg['lum'])
            fy = y0 + (np.arange(h + 2) - 1) // 3
            fx = x0 + (np.arange(w + 2) - 1) // 3
            inbox = (((fy >= by0) & (fy < by1))[:, None]
                     & ((fx >= bx0) & (fx < bx1))[None, :])
            lab = _label(dark)
            peel = np.zeros_like(drawn)
            for L in set(np.unique(lab[dark & _dil(openm)])) - {0}:
                m = lab == L
                if m.sum() <= cfg['max']:
                    peel |= m
            peel &= inbox
            for _g, x, y, p, sx, sy, sz in rr:
                T = sz * 3
                Y, X = (y - y0) * 3 + 1, (x - x0) * 3 + 1
                key = (p.slot, sx, sy, sz)
                if key not in orig:
                    orig[key] = datas[p.slot][sy * 3:sy * 3 + T,
                                              sx * 3:sx * 3 + T].copy()
                kill = peel[Y:Y + T, X:X + T] & (orig[key] != 0)
                prev = want.get(key)
                if prev is None:
                    want[key] = kill
                elif prev is not False and not np.array_equal(prev, kill):
                    want[key] = False
        for key, kill in want.items():
            slot, sx, sy, sz = key
            if kill is False:
                st['conflict'] += 1
                continue
            if not kill.any():
                continue
            T = sz * 3
            blk = orig[key].copy()
            blk[kill] = 0
            st['keyed'] += int(kill.sum())
            datas[slot][sy * 3:sy * 3 + T, sx * 3:sx * 3 + T] = blk
        info[layer] = st
    if not any(v['keyed'] for v in info.values()):
        return s9, info
    plist, t0, t1 = FN.parse_texture_block(s9, px)
    for slot, d in datas.items():
        q = plist[slot]
        plist[slot] = FN.Page(slot, q.size_flag, 2, d.tobytes(), q.px)
    return FN.replace_texture_block(s9, plist, t0, t1), info


def plan(name, s9, chunk9, art_open):
    cfg = FIELDS[name]
    info = {}
    s9, info['edges'] = clean_edges(name, s9, chunk9, art_open,
                                    cfg.get('edges', {}))
    if name in PEEL:
        s9, info['peel'] = peel_specks(s9, PEEL[name])
    return s9, info


def apply_to_flevel(archive, payloads, dds_sources=(), encode=None,
                    log=lambda *_: None):
    import lgp
    import field_bg_repack as R
    import ff7nx_cosmosrestore as CR
    import diag_common as DC
    st = {'names': [], 'refused': []}
    if disabled() or not dds_sources:
        return st
    encode = encode or archive.encode_field
    provs = {}
    for name in FIELDS:
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            path = os.path.join(CR.CHUNK_DIR, name + '.chunk.9')
            with open(path, 'rb') as fh:
                chunk9 = fh.read()
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            # the art at the section's own page size (the setting's value
            # in a build; read off the pages so a test needs no setting)
            px = DC.parse_pages(parts[8])[3]
            if px not in provs:
                provs[px] = R.ArtProvider(list(dds_sources), px,
                                          lambda *_a: None)
            prov = provs[px]
            parts[8], info = plan(name, parts[8], chunk9, prov.open)
            if name in START_LOWER:
                parts[0], info['start'] = lower_start(name, parts[0])
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:100]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        bits = []
        for l_, v in sorted(info['edges'].items()):
            if v.get('refused'):
                bits.append('layer %d refused (%s)' % (l_, v['refused']))
            else:
                bits.append('layer %d %d keyed, %d blended%s' % (
                    l_, v['keyed'], v['blended'],
                    ', %d shared cells left' % v['conflict']
                    if v['conflict'] else ''))
        pk = sum(v['keyed'] for v in info.get('peel', {}).values())
        if pk:
            bits.append('%d near-black skyline texels keyed' % pk)
        for l_, a_, b_ in info.get('start', ()):
            bits.append('opening shot layer %d %d->%d' % (l_, a_, b_))
        st['names'].append('%s: %s' % (name, ', '.join(bits)))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  HILL FIX (BUILD 710): stray 1997 edge texels keyed, '
                   'edges blended toward the sky, hill2 opens lower, '
                   'near-black skyline specks keyed (BUILD 712) (%s). '
                   '%s=1 disables.' % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! hill fix %s: not applied -- %s' % (name, why))
    return '\n'.join(out)
