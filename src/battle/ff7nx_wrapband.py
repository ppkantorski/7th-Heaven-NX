#!/usr/bin/env python3
"""ff7nx_wrapband.py -- the light line through Knights of the Round's curtain.
BUILD 748, corrected in BUILD 749.

WHAT DRAWS THE CURTAIN
======================
KOTR's resource slot 2 is CYLINDER.RSD, whose only texture is BG_2 (no other
.rsd uses it). The effect100 function x86 0x480DF9 calls 0x481096 for each of
its two layers, and 0x481096 draws the cylinder TWICE, end to end along its
axis (z_offset 0 and 2462 -- exactly one copy length), the stack moving with
the effect counter and wrapping every 2462 units. Alternate copies are
MIRRORED in V (struc_173 scroll_v = 2: v = 0.98046875 - v_src, x86 0x68D21A)
so the two copies meet texture-to-texture: the joint is a MIRROR SEAM, at
v = 1/256 on one wrap parity and at v = 250/256 on the other.

WHY THERE IS A LINE
===================
The joint samples the texture's own last art rows. Vanilla BG_2 has full art
down to row 252 of 256 (black only from 253). SYW's replacement FADES OUT
inside the sampled range: at the shipped 3x canvas the row brightness runs
69.6 at v = 248/256, 64.6 at 249, 49.5 at 250, 30.6 at 251, 0 by 254. Both
copies sample that fade on either side of the joint, so every other wrap the
mirror seam is a dark-faded band ~2.6/256 of a copy tall -- the line, drifting
with the conveyor. (Build 748 crossfaded those rows toward row 0 on the wrong
theory of a V wrap; that only changed the band's colour.)

THE FIX
=======
Rows from the first one (below v = 240/256) that is more than 4 % darker
than the row above it, down to the bottom, are replaced by the last full row (indices
copied, so the palette, colour key and every value stay the TEX's own). The
mirror seam then meets the same row on both sides. Vanilla: rows 253..255
(black) take row 252. SYW 3x: rows from ~v=249/256 take the last full row.
Header, palette, dimensions, logical-scale marker and every row above the
fade are byte-exact; idempotent.

SEVENTH_NX_NO_WRAP_BAND=1 disables.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

import tex

ENV_OFF = 'SEVENTH_NX_NO_WRAP_BAND'

#: Textures repaired, by lower-case entry name. BG_2 is referenced only by
#: CYLINDER.RSD (KOTR's curtain); see the module docstring.
NAMES = ('bg_2.tex',)

#: The search for the fade starts at this fraction of the height.
FROM_NUM, FROM_DEN = 240, 256
#: A row darker than this share of the row above it is where the fade
#: starts (art drifts ~1 % a row; SYW's fade drops 7 % in its first row).
FADE = 0.96


def enabled():
    return os.environ.get(ENV_OFF, '').strip() not in ('1', 'true', 'yes')


def _np():
    import numpy
    return numpy


def repair(payload):
    """Return ``payload`` with the faded rows at the bottom replaced by the
    last full art row, or None if this TEX is not a single-palette 8-bit
    texture (left alone: fail closed)."""
    np = _np()
    t = tex.parse(payload)
    if t is None or t['bytes_per_pixel'] != 1 or not t['palette_flag']:
        return None
    if t['num_palettes'] != 1:
        return None
    w, h = t['width'], t['height']
    pal = np.frombuffer(bytes(t['palette']), np.uint8).reshape(-1, 4)
    idx = np.frombuffer(bytes(t['pixels']), np.uint8).reshape(h, w).copy()
    if int(idx.max()) >= len(pal):
        return None
    lum = pal[:, :3].astype(np.float64).mean(1)[idx].mean(1)
    start = (h * FROM_NUM) // FROM_DEN
    r0 = None
    for r in range(start + 1, h):
        if lum[r] < FADE * lum[r - 1]:
            r0 = r
            break
    if r0 is None or r0 <= start:
        return bytes(payload)            # nothing fades: leave as is
    idx[r0:] = idx[r0 - 1]
    head = len(payload) - w * h
    return bytes(payload[:head]) + idx.tobytes()


# ---------------------------------------------------------------------------
# BUILD 779. The curtain's seams and its black pillars.
#
# HARDWARE 10-22 (KOTR video, every frame): "abrupt disconnections" in the
# scrolling curtain -- straight lines where the texture jumps -- and "weird
# black pillars".
#
# SEAMS. The curtain's material scrolls its U (field_8's u byte) and the
# aurora phase its V; since BUILD 749 the UVs are no longer wrapped per
# vertex, so the GPU's GL_REPEAT samples straight across the texture's own
# edges, and BG_2 does not tile: measured on the shipped (SYW 3x) canvas, the
# mean step across its U edge is 39.4 grey levels (inside the art: 3.3) and
# across its V edge 65.1 (inside: 1.2). That step is the line -- a vertical
# one moving across the curtain (frames 286..347 of the capture: a 1-px
# step of ~10 against ~1.5 around it, not a mirror seam), and a horizontal
# one where the texture's top meets its bottom (frames 349, 466).
# FIX: the last TILE_U of the columns fade into the first ones mirrored, and
# the last TILE_V of the rows the same, so the texture is periodic: the step
# across both edges becomes 0 and nothing above or left of the zones moves.
#
# BLACK PILLARS. SYW's BG_2 is far more contrasted than the original: 26.5 %
# of its texels are black (vanilla 1.2 %), a quarter of the art at grey <= 6
# where the original never goes below ~8..24. The curtain is drawn additive
# with a red tint, so those regions are holes -- the pillars. FIX: the low
# end is lifted to the original's tone curve, texel by texel at the same
# percentile (black ordered by its neighbourhood, so a lifted gap keeps a
# gradient): out = max(g, Q_vanilla(F(g))). The highlights are SYW's.
#
# Greyscale 8-bit textures only (BG_2 is, both canvases); the result is
# re-indexed onto a grey palette of the same size, header untouched.
# SEVENTH_NX_NO_KOTR_CURTAIN=1 disables.
CURTAIN_ENV = 'SEVENTH_NX_NO_KOTR_CURTAIN'
TILE_U = 0.10                    # share of the width blended into the start
TILE_V = 1.0 / 8                 # share of the height (BUILD 781: 1/16)


def curtain_enabled():
    return os.environ.get(CURTAIN_ENV, '').strip() not in ('1', 'true', 'yes')


def _grey(t):
    np = _np()
    pal = np.frombuffer(bytes(t['palette']), np.uint8).reshape(-1, 4)
    idx = np.frombuffer(bytes(t['pixels']), np.uint8).reshape(
        t['height'], t['width'])
    if int(idx.max()) >= len(pal):
        return None
    used = np.unique(idx)
    p = pal[used].astype(int)
    if (p[:, 0] != p[:, 1]).any() or (p[:, 1] != p[:, 2]).any():
        return None
    g = pal[idx, 0].astype(np.float64)
    g[pal[idx, 3] == 0] = 0.0
    return pal, idx, g


def _blur_wrap(a, sigma):
    """Separable gaussian with wrap-around edges (numpy only)."""
    np = _np()
    r = int(3 * sigma + 0.5)
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    out = a
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (r, r)
        p = np.pad(out, pad, mode='wrap')
        acc = np.zeros_like(out)
        for i, kv in enumerate(k):
            sl = [slice(None), slice(None)]
            sl[axis] = slice(i, i + out.shape[axis])
            acc += kv * p[tuple(sl)]
        out = acc
    return out


def _lift(g, gv):
    np = _np()
    key = g + 0.01 * _blur_wrap(g, 6.0)
    order = np.argsort(key.ravel(), kind='stable')
    p = np.empty(g.size)
    p[order] = (np.arange(g.size) + 0.5) / g.size
    qv = np.quantile(gv.ravel(), p.reshape(g.shape))
    return np.maximum(g, qv)


def _tile(a, bu, bv):
    """Periodic in U and V. BUILD 781: by a SMOOTH CORRECTION, not a blend.

    779/780 cross-faded the last columns/rows into the first ones mirrored.
    That made the edge continuous, but the zone itself became a soft ghost
    of two pictures -- on hardware (10-22, after 780) "a column of shaded
    texture" drifting through the aurora with the scroll. Now the step
    across each edge, per row (U) or per column (V), is measured and half
    of it is ramped into each side over `bu`/`bv` texels with a smoothstep.
    Every texel keeps its own detail; only a low-frequency offset changes,
    and the two edges meet exactly."""
    a = a.copy()
    h, w = a.shape
    ss = lambda x: x * x * (3 - 2 * x)
    c = a[:, 0] - a[:, -1]
    for i in range(bu):
        a[:, w - bu + i] += ss((i + 1) / bu) * c / 2
        a[:, i] -= ss((bu - i) / bu) * c / 2
    c = a[0, :] - a[-1, :]
    for j in range(bv):
        a[h - bv + j] += ss((j + 1) / bv) * c / 2
        a[j] -= ss((bv - j) / bv) * c / 2
    return a


def curtain(payload, vanilla=None):
    """BG_2 made periodic in U and V, its blacks lifted to the vanilla tone
    curve (when `vanilla` is given). None if not a greyscale 8-bit TEX."""
    np = _np()
    t = tex.parse(payload)
    if t is None or t['bytes_per_pixel'] != 1 or not t['palette_flag'] \
            or t['num_palettes'] != 1:
        return None
    got = _grey(t)
    if got is None:
        return None
    pal, idx, g = got
    h, w = g.shape
    out = g
    if vanilla is not None:
        tv = tex.parse(vanilla)
        gv = _grey(tv) if tv is not None and tv['bytes_per_pixel'] == 1 \
            else None
        if gv is not None:
            out = _lift(g, gv[2])
    out = _tile(out, max(1, int(round(w * TILE_U))),
                max(1, int(round(h * TILE_V))))
    v = np.clip(np.rint(out), 0, 255).astype(np.int64)
    n = len(pal) - 1                     # entry 0 stays the key, unused
    levels = np.unique(v)
    if len(levels) > n:
        levels = np.unique(np.rint(np.linspace(v.min(), v.max(), n)))
    pos = np.clip(np.searchsorted(levels, v), 0, len(levels) - 1)
    lower = np.clip(pos - 1, 0, len(levels) - 1)
    pick = np.where(np.abs(levels[lower] - v) < np.abs(levels[pos] - v),
                    lower, pos)
    nidx = (pick + 1).astype(np.uint8)
    alphas = pal[np.unique(idx), 3]
    alphas = alphas[alphas != 0]
    a = int(np.bincount(alphas).argmax()) if alphas.size else 255
    npal = np.zeros_like(pal)
    npal[1:len(levels) + 1, 0:3] = levels[:, None].astype(np.uint8)
    npal[1:len(levels) + 1, 3] = a
    npal[0] = pal[0]
    head = len(payload) - w * h - pal.size
    return (bytes(payload[:head]) + npal.tobytes() + nidx.tobytes())


# BUILD 780. Hardware 10-22 (after 779): "a slightly visible seam in kotr's
# scrolling field" -- now in the aurora phase: a faint vertical line where
# the aurora's bands step. The aurora is TUBE_1 (KOTR's tube, the same
# scrolled-UV draw as the curtain). The shipped canvas tiles nearly but not
# exactly: the step across its V edge is 3.1 against 0.7 inside the art (U:
# 6.6 against 1.6). The same edge blend makes it periodic; it is a colour
# palette, so the blended texels go back to the TEX's own nearest palette
# entry (mean error 0.17, max 4.6 of 255; the key entry 0 is never chosen).
TILE_NAMES = ('tube_1.tex',)


def tile_colour(payload):
    """A single-palette 8-bit TEX made periodic in U and V. None if not."""
    np = _np()
    t = tex.parse(payload)
    if t is None or t['bytes_per_pixel'] != 1 or not t['palette_flag'] \
            or t['num_palettes'] != 1:
        return None
    pal = np.frombuffer(bytes(t['palette']), np.uint8).reshape(-1, 4)
    w, h = t['width'], t['height']
    idx = np.frombuffer(bytes(t['pixels']), np.uint8).reshape(h, w)
    if int(idx.max()) >= len(pal) or (pal[idx, 3] == 0).any():
        return None                      # keyed texels: not a plain sheet
    rgb = pal[idx][..., :3].astype(np.float64)
    bu, bv = max(1, int(round(w * TILE_U))), max(1, int(round(h * TILE_V)))
    out = np.stack([_tile(rgb[..., c], bu, bv) for c in range(3)], -1)
    cand = np.nonzero(pal[:, 3] != 0)[0]
    P = pal[cand, :3].astype(np.float64)
    flat = out.reshape(-1, 3)
    nidx = np.empty(len(flat), np.uint8)
    for i in range(0, len(flat), 32768):
        d = ((flat[i:i + 32768, None, :] - P[None]) ** 2).sum(-1)
        nidx[i:i + 32768] = cand[d.argmin(1)]
    nidx = nidx.reshape(h, w)
    keep = np.zeros((h, w), bool)
    keep[bv:h - bv, bu:w - bu] = True
    nidx[keep] = idx[keep]               # outside the zones: byte-exact
    head = len(payload) - w * h
    return bytes(payload[:head]) + nidx.tobytes()


def apply(archive, spell_payloads, payloads, log=lambda *_: None):
    """Repair NAMES in place in whichever payload the archive will ship.

    ``spell_payloads`` is {toc index: bytes} (converted SYW art);
    ``payloads`` is {name: bytes} (native mod replacements, which outrank
    converted art). An entry neither replaces gets its vanilla bytes
    repaired and is added to ``spell_payloads`` by index. Returns the
    number of entries changed.
    """
    if not enabled():
        return 0
    done = 0
    for i, e in enumerate(archive.entries):
        name = e['name'].lower()
        if name not in NAMES:
            continue
        if name in payloads:
            src, put = payloads[name], lambda d, n=name: payloads.__setitem__(n, d)
        elif i in spell_payloads:
            src, put = spell_payloads[i], lambda d, k=i: spell_payloads.__setitem__(k, d)
        else:
            src, put = e['payload'], lambda d, k=i: spell_payloads.__setitem__(k, d)
        try:
            out = repair(src)
        except Exception as exc:                          # noqa: BLE001
            log('  ! wrap band: %s left as is (%s)' % (name, exc))
            continue
        if out is None:
            log('  ! wrap band: %s is not 8-bit single-palette, left as is'
                % name)
            continue
        if curtain_enabled():
            try:
                cur = curtain(out, e['payload'])
            except Exception as exc:                      # noqa: BLE001
                cur = None
                log('  ! KOTR curtain: %s left as is (%s)' % (name, exc))
            if cur is not None:
                out = cur
                log('  KOTR curtain: %s tiles in U and V, blacks lifted to '
                    'the original tone (BUILD 779)' % name)
        if out != src:
            put(out)
            done += 1
    if curtain_enabled():
        for i, e in enumerate(archive.entries):
            name = e['name'].lower()
            if name not in TILE_NAMES:
                continue
            if name in payloads:
                src, put = payloads[name], (
                    lambda d, n=name: payloads.__setitem__(n, d))
            elif i in spell_payloads:
                src, put = spell_payloads[i], (
                    lambda d, k=i: spell_payloads.__setitem__(k, d))
            else:
                src, put = e['payload'], (
                    lambda d, k=i: spell_payloads.__setitem__(k, d))
            try:
                out = tile_colour(src)
            except Exception as exc:                      # noqa: BLE001
                out = None
                log('  ! KOTR aurora: %s left as is (%s)' % (name, exc))
            if out is not None and out != src:
                put(out)
                done += 1
                log('  KOTR aurora: %s tiles in U and V (BUILD 780)' % name)
    if done:
        log('  wrap band: the faded art rows under BG_2 (the KOTR curtain '
            'copies\' mirror seam) now repeat the last full row (BUILD 749)')
    return done
