#!/usr/bin/env python3
"""ff7nx_fxd1art.py -- 2x detail for PALETTE-ANIMATED effect pages, kept
paletted so the animation survives. Only active with the 512px depth-1
experiment (SEVENTH_NX_FIELD_BG_D1_PX=512).

BUILD 618v. Hardware 10-04: junbin5's pulsing lamps "look pixelated",
mtnvl3's mist has stair steps, eals_1's waterfall edge is rough. All three
are effects the field script animates through their PALETTE (pulses,
cycles). A truecolor page has no palette, so they must stay on depth-1
(8-bit indexed) pages, which this port stores at 256px: 1x detail.

THE ROUTE: build 108/109 (FINDINGS-223, HANDOFF-224) already taught the
module to read depth-1 pages at 512px (SEVENTH_NX_FIELD_BG_D1_PX=512), and
`field_bg_native.lift_depth1` takes an `art` callable that supplies a 512px
index page instead of 2x replication. Until now only `field_bg_shadow`
(Cosmos's margin art) fed it. This module feeds it for the additive FX band
(pages 15..23): every 16x16 cell becomes a 32x32 block of INDICES, still
drawn through the same palette, so every LDPAL/ADPAL/MPPAL keeps animating
it -- with Cosmos's detail.

PER CELL (sampled by FX records of one palette q):
  1. Reference: the Cosmos 32x32 block whose 2x2-averaged colours match the
     cell's current colours (pal_q[index], key = 0) best, searched over the
     field's FX sheets for palette q (exact sheet, else `_00`), so cells
     relocated by repack/evict still find their art. Rejected above
     MATCH_MAX (falls back to replication).
  2. Indices: each 2x pixel takes the index whose stored colour is nearest
     the Cosmos pixel, chosen among
       * the whole palette, when q is static or changed only by uniform
         whole-palette operations (fxrequant.uniform_palettes / groups):
         any index then animates exactly like any other;
       * otherwise (cycles, partial loads: eals_1's waterfall) only the
         indices in the 3x3 neighbourhood of the 1x texel, so the index
         layout the script cycles through is kept and only edges sharpen.
     The key (0) is allowed only where the 1x neighbourhood has it.
  3. Drift gate: the new block, 2x2-averaged, must be at least as close to
     the 1x cell as replication is to Cosmos, else replication.

SEVENTH_NX_NO_FX_D1_ART=1 disables (the 512px lift then replicates).
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_FX_D1_ART'
FX_LO, FX_HI = 0x0F, 0x18
MATCH_MAX = 18.0          # mean abs colour error, 0..255, cell vs Cosmos
_ART = None               # the build's DDS provider source, set by build.py
STATS = collections.Counter()


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def arm(art):
    global _ART
    _ART = art


def _palettes(sec3):
    import ff7nx_framesim as FS
    return FS._palettes(sec3)[0]


def _uniform(script):
    import ff7nx_fxrequant as RQ
    import ff7nx_palanim as PA
    scripted = set(PA.animated_palettes(script))
    if len(scripted) >= 256:
        return scripted, set()
    # MPPAL2 (0xDF) and the rotations rewrite PART of a palette from a
    # moving source offset: a cycle (eals_1's waterfall). fxrequant does not
    # audit 0xDF, so such a field gets no full-palette remapping here.
    import echo_s_flevel as ES
    try:
        for a, b in ES._routine_blocks(script):
            stream, _ = ES._decode_block(script, a, b)
            if any(op in (0xDF, 0xE8, 0xEE) for _o, op, _s in stream):
                return scripted, set()
    except Exception:                                          # noqa: BLE001
        return scripted, set()
    uni = set(RQ.uniform_palettes(script))
    g = RQ.single_source_group(script)
    if g:
        uni |= set(g[1])
    return scripted, uni


def _sheets(name, slots, pals):
    """{q: (N, 32, 32, 3) premultiplied 512px blocks, (N,16,16,3) means}."""
    import ff7nx_fxmargin as FXM
    import ff7nx_fxpages as FP
    import ff7nx_fxpcstatic as PS
    prov = getattr(_ART, 'provider', None)
    if prov is None:
        return {}
    out = {}
    cache = {}
    for q in pals:
        blocks = []
        for s in slots:
            recs = getattr(prov, 'state_slots', {}).get((name.lower(), s, q))
            img = None
            key = None
            if recs and len(recs) >= 2:
                key = ('frames', s, q)
                if key not in cache:
                    fr = [PS._rec_rgba(prov, r, 512) for r in recs]
                    fr = [f for f in fr if f is not None]
                    cache[key] = (np.median(np.stack(fr), 0).astype(np.uint8)
                                  if fr else None)
                img = cache[key]
            else:
                sel = FP._selected_palette(prov, name, s, q)
                if sel is None:
                    continue
                key = ('sheet', s, sel)
                if key not in cache:
                    cache[key] = FXM._provider_rgba(_ART, name, s, sel, 512)
                img = cache[key]
            if img is None or img.shape[:2] != (512, 512):
                continue
            pm = img[..., :3].astype(np.float32) * (
                img[..., 3:].astype(np.float32) / 255.0)
            b = pm.reshape(16, 32, 16, 32, 3).transpose(0, 2, 1, 3, 4)
            blocks.append(b.reshape(256, 32, 32, 3))
        if blocks:
            full = np.concatenate(blocks)
            mean = full.reshape(-1, 16, 2, 16, 2, 3).mean((2, 4))
            out[q] = (full, mean)
    return out


def _cell_owner(sec9, ts, slot):
    """{(cx, cy): palette} for FX records sampling `slot` (None on clash)."""
    import diag_common as DC
    own = {}
    for _layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            if not sec9[o + 28] or sec9[o + 34] != slot:
                continue
            sx, sy = struct.unpack_from('<hh', sec9, o + 14)
            if sx % 16 or sy % 16:
                continue
            c = (sx // 16, sy // 16)
            q = sec9[o + 22]
            if own.setdefault(c, q) != q:
                own[c] = None
    return {c: q for c, q in own.items() if q is not None}


def _neigh(a):
    """(16,16) -> (32,32,9) candidate indices from the 3x3 1x neighbourhood."""
    p = np.pad(a, 1, mode='edge')
    nb = np.stack([p[dy:dy + 16, dx:dx + 16] for dy in range(3)
                   for dx in range(3)], -1)
    return np.repeat(np.repeat(nb, 2, 0), 2, 1)


def _block(a, pal_q, ref, full_mode):
    """32x32 indices for one 16x16 cell `a` against Cosmos block `ref`."""
    cols = pal_q.astype(np.float32).copy()
    cols[0] = 0.0
    if full_mode:
        nonkey = np.arange(1, 256)
        d = ((ref[..., None, :] - cols[nonkey][None, None]) ** 2).sum(-1)
        idx = nonkey[d.argmin(-1)]
        nb = _neigh(a)
        keyok = (nb == 0).any(-1) & (ref.max(-1) < 4.0)
        idx = np.where(keyok, 0, idx)
        return idx.astype(np.uint8)
    nb = _neigh(a)
    d = ((cols[nb] - ref[..., None, :]) ** 2).sum(-1)
    pick = d.argmin(-1)
    return np.take_along_axis(nb, pick[..., None], -1)[..., 0].astype(
        np.uint8)


def compose(name, parts, base=None):
    """The `art` callable for field_bg_native.lift_depth1, or `base`."""
    if disabled() or _ART is None:
        return base
    import diag_common as DC
    sec9 = parts[8]
    try:
        _pl, ts, _te, _px = DC.parse_pages(sec9)
        pal = _palettes(parts[3])
        scripted, uni = _uniform(parts[0])
    except Exception:                                          # noqa: BLE001
        return base
    owners = {s: _cell_owner(sec9, ts, s) for s in range(FX_LO, FX_HI)}
    owners = {s: o for s, o in owners.items() if o}
    if not owners:
        return base
    pals = sorted({q for o in owners.values() for q in o.values()})
    state = {}

    def art(page, dst):
        out = base(page, dst) if base is not None else None
        if dst != 512 or page.px != 256 or page.slot not in owners:
            return out
        if 'sheets' not in state:
            try:
                state['sheets'] = _sheets(name, range(FX_LO, FX_HI), pals)
            except Exception:                                  # noqa: BLE001
                state['sheets'] = {}
        sheets = state['sheets']
        a = np.frombuffer(page.data, np.uint8, count=65536).reshape(256, 256)
        if out is None:
            o = np.repeat(np.repeat(a, 2, 0), 2, 1)
        else:
            o = np.frombuffer(out, np.uint8).reshape(512, 512).copy()
        changed = 0
        for (cx, cy), q in owners[page.slot].items():
            if q not in sheets or q >= pal.shape[0]:
                STATS['no_art'] += 1
                continue
            cell = a[cy * 16:(cy + 1) * 16, cx * 16:(cx + 1) * 16]
            cols = pal[q].astype(np.float32).copy()
            cols[0] = 0.0
            cur = cols[cell]
            full, mean = sheets[q]
            err = np.abs(mean - cur[None]).mean((1, 2, 3))
            k = int(err.argmin())
            if err[k] > MATCH_MAX:
                STATS['no_match'] += 1
                continue
            ref = full[k]
            fm = (q not in scripted) or (q in uni)
            blk = _block(cell, pal[q], ref, fm)
            new_c = cols[blk]
            rep_c = np.repeat(np.repeat(cur, 2, 0), 2, 1)
            e_new = np.abs(new_c - ref).mean()
            e_rep = np.abs(rep_c - ref).mean()
            drift = np.abs(new_c.reshape(16, 2, 16, 2, 3).mean((1, 3))
                           - cur).mean()
            if e_new >= e_rep or drift > max(e_rep, 6.0):
                STATS['gate'] += 1
                continue
            o[cy * 32:(cy + 1) * 32, cx * 32:(cx + 1) * 32] = blk
            changed += 1
            STATS['full' if fm else 'neigh'] += 1
        if changed:
            STATS['pages'] += 1
        return o.tobytes()

    STATS['fields'] += 1
    return art


def summarise():
    if not STATS.get('pages'):
        return ''
    return ('  FX DEPTH-1 ART (BUILD 618v): %d palette-animated effect page(s) '
            'in %d field(s) got 2x Cosmos detail, still paletted (%d cells '
            'full-palette, %d neighbourhood-only; %d no match, %d gated, %d '
            'no art). %s=1 disables.'
            % (STATS['pages'], STATS['fields'], STATS['full'],
               STATS['neigh'], STATS['no_match'], STATS['gate'],
               STATS['no_art'], OFF_ENV))
