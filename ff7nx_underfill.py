#!/usr/bin/env python3
"""ff7nx_underfill.py -- make the "no state on" gap of an animated overlay
look like its first frame.

BUILD 618q. las0_3 (the slope below the crashed plane), hardware 10-03:
"one of the frames looks kinda dirty / blurry" in the animation at the top
of the screen.

MEASURED:
  * The sky/snow animation is layer 2 param 2, 7 states. Script loop: WAIT 4
    with NO state on, then s0..s6 for 5 frames each (39 frames). In the
    60 fps capture the sky changes at frames 80, 90, ..., 150, 159 (period
    79), and frames 150-158 (the 4-frame gap) are the odd one out.
  * During the gap the static layer-1 record under every animated position
    shows. In vanilla it is painted like the states. Cosmos painted its
    layer-1 sky as a bluish, mottled surface quite unlike its pale, streaked
    overlay frames, so the gap read as one dirty frame per loop.

FIX: each static layer-1 record under an animated position gets a new HD
cell = the state-0 cell composited over the record's own cell (state 0
where it is opaque, the old cell where it is keyed). The gap therefore
looks like state 0 held 4 frames longer. The new cells go into free cells
of an existing 16-grid truecolor page, so no page and no byte of memory is
added. Only those layer-1 records are repointed; states, z and positions
are untouched. Refused (field byte-identical) unless every animated
position has exactly one static layer-1 record under it, enough free cells
exist on one page, and the binding-page cap holds.

REORDER (same build, same field): "it looks like it moves backwards part
way". Cosmos's seven frames are not in vanilla's order. Matching the moving
part of each frame (the static picture subtracted, normalised correlation):
  * vanilla s0..s5 is a smooth cycle (neighbours correlate 0.77-0.87);
  * Cosmos's frame 3 is a copy of its frame 0 (0.98), and the order
    0,1,2,3,4,5,6 steps through -0.49, -0.37, ..., -0.92 (B6 -> B0): the
    picture jumps back twice per loop;
  * by phase, Cosmos's frames line up with vanilla's as 6,1,2,0/3,4,5.
The states are re-pointed to Cosmos frames (6,1,2,0,3,4,5): neighbours now
correlate +0.35, -0.37, +0.19, +0.98 (the duplicate as a short hold), +0.32,
+0.14, +0.66. Only the cell pointers of the param records swap between
states at the same position; nothing is added.

SEVENTH_NX_NO_UNDER_FILL=1 disables both.
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_UNDER_FILL'
FIELDS = {'las0_3': (2, 2, 1)}          # field: (layer, param, state bit)
REORDER = {'las0_3': (2, 2, (6, 1, 2, 0, 3, 4, 5))}   # state k <- frame p[k]
GRID = 16
UV_CELL = 625000


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _used_cells(s9, f, slot):
    """{(cx, cy)} of 16-grid cells referenced on `slot`."""
    used = set()
    for _lay, o in f.tiles:
        refs = [(s9[o + 32], o + 42)]
        if s9[o + 28]:
            refs.append((s9[o + 34], o + 42))
        for sl, uo in refs:
            if sl != slot:
                continue
            u, v = struct.unpack_from('<II', s9, uo)
            cx, cy = int(round(u / UV_CELL)), int(round(v / UV_CELL))
            span = max(1, (max(struct.unpack_from('<HH', s9, o + 18)) or 16)
                       // 16)
            for dy in range(span):
                for dx in range(span):
                    used.add((cx + dx, cy + dy))
    return used


def plan_reorder(parts, layer, param, perm):
    """(new section 9, positions) -- state k shows the cells of state
    perm[k]; raises unless every position has every state once."""
    import ff7nx_framesim as FS
    f = FS.Field(parts)
    s9 = f.sec9
    bits = [1 << i for i in range(len(perm))]
    if sorted(perm) != list(range(len(perm))):
        raise ValueError('not a permutation')
    pos = collections.defaultdict(dict)
    for lay, o in f.tiles:
        if lay == layer and s9[o + 26] == param:
            if s9[o + 28]:
                raise ValueError('FX record in the animation')
            x, y = struct.unpack_from('<hh', s9, o + 2)
            st = s9[o + 27]
            if st not in bits or st in pos[(x, y)]:
                raise ValueError('state %d at %r' % (st, (x, y)))
            pos[(x, y)][st] = o
    buf = bytearray(s9)
    fields = ((10, 4), (22, 2), (32, 2), (42, 8))   # src, pal, tex, uv
    for p, recs in pos.items():
        if len(recs) != len(bits):
            raise ValueError('%r has %d of %d states' % (p, len(recs),
                                                         len(bits)))
        for k, b in enumerate(bits):
            src = recs[bits[perm[k]]]
            dst = recs[b]
            for off, n in fields:
                buf[dst + off:dst + off + n] = s9[src + off:src + off + n]
    return bytes(buf), len(pos)


def plan_field(parts, layer, param, bit):
    """(new section 9, info) -- raises on any doubt."""
    import ff7nx_framesim as FS
    import field_bg_native as FN
    import field_bg_repack as FR
    import diag_common as DC
    f = FS.Field(parts)
    s9 = f.sec9
    at = collections.defaultdict(list)
    anim = {}
    for lay, o in f.tiles:
        x, y = struct.unpack_from('<hh', s9, o + 2)
        if lay == layer and s9[o + 26] == param and s9[o + 27] == bit:
            anim[(x, y)] = o
        elif lay == 1 and not s9[o + 26] and not s9[o + 28]:
            at[(x, y)].append(o)
    if not anim:
        raise ValueError('no state-%d records' % bit)
    pairs = {}
    for pos, o in anim.items():
        under = at.get(pos, [])
        if len(under) != 1:
            raise ValueError('%d layer-1 records under %r' % (len(under), pos))
        pairs[pos] = (o, under[0])
    px = f.px
    cs = px // GRID
    cand = []
    for slot, p in f.pm.items():
        if p.depth != 2 or p.size_flag:
            continue
        used = _used_cells(s9, f, slot)
        free = [(cx, cy) for cy in range(GRID) for cx in range(GRID)
                if (cx, cy) not in used]
        cand.append((len(free), slot, free))
    cand.sort(reverse=True)
    if not cand or cand[0][0] < len(pairs):
        raise ValueError('need %d free cells, best page has %d'
                         % (len(pairs), cand[0][0] if cand else 0))
    _n, slot, free = cand[0]
    page = f.arr[slot].copy()
    buf = bytearray(s9)
    changed = 0
    for (pos, (oa, ou)), (cx, cy) in zip(sorted(pairs.items()), free):
        a = f._block(oa, layer, 16)
        u = f._block(ou, 1, 16)
        if a is None or u is None or a[0].shape[0] != cs \
                or u[0].shape[0] != cs:
            raise ValueError('cell at %r unreadable or not HD' % (pos,))
        rgb = np.where(a[1][..., None], u[0], a[0])
        rgba = np.concatenate([np.clip(rgb, 0, 255).astype(np.uint8),
                               np.full((cs, cs, 1), 255, np.uint8)], -1)
        cell = np.frombuffer(FR.rgba_to_565_buf(rgba.tobytes(), cs * cs,
                                                width=cs), '<u2')
        page[cy * cs:(cy + 1) * cs, cx * cs:(cx + 1) * cs] = \
            cell.reshape(cs, cs)
        buf[ou + 32] = slot
        struct.pack_into('<hh', buf, ou + 10, cx * 16, cy * 16)
        struct.pack_into('<II', buf, ou + 42, cx * UV_CELL, cy * UV_CELL)
        changed += 1
    plist, t0, t1 = FN.parse_texture_block(bytes(buf), px)
    old = plist[slot]
    plist[slot] = FN.Page(slot, old.size_flag, 2, page.astype('<u2').tobytes(),
                          px)
    out = FN.replace_texture_block(bytes(buf), plist, t0, t1)
    import field_bg_pagecap as PC
    counts = PC.effective_counts(out, px)
    over = {k: v for k, v in counts.items() if v > PC.MAX_TILES_PER_PAGE}
    if over:
        raise ValueError('binding-page cap: %s' % over)
    _pl, _ts, _te, _px = DC.parse_pages(out)
    return out, {'records': changed, 'slot': slot,
                 'free_left': len(free) - changed}


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    total = {'names': [], 'refused': []}
    if disabled():
        return total
    encode = encode or archive.encode_field
    for name, (layer, param, bit) in FIELDS.items():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            note = ''
            if name in REORDER:
                rl, rp, perm = REORDER[name]
                parts[8], npos = plan_reorder(parts, rl, rp, perm)
                note = '; frames reordered %s at %d positions' % (
                    ','.join(map(str, perm)), npos)
            parts[8], info = plan_field(parts, layer, param, bit)
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append((name, str(exc)[:90]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['names'].append('%s: %d layer-1 cell(s) = state %d over the old '
                              'cell, page %d%s' % (name, info['records'], bit,
                                                   info['slot'], note))
    return total


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  UNDER FILL (BUILD 618q): the "no state" gap of an '
                   'animated overlay shows its first frame (%s). %s=1 '
                   'disables.' % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! under fill %s: %s' % (name, why))
    return '\n'.join(out)
