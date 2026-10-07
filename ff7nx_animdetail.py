#!/usr/bin/env python3
"""ff7nx_animdetail.py -- give back the motion of an animated background
that Cosmos painted as one frame.

BUILD 618m. sea (the party on the wrecked Tiny Bronco, after `sky`),
hardware 10-03: the water barely moves. It shows a faint one-frame change
every 8 frames instead of rippling.

MEASURED:
  * The ripples are layer 2 param 1: 6 states (bits 2..7) of 128 records,
    each with its own cell and its own palette (5..10). The script cycles
    them with BGON/BGOFF every 8 frames.
  * In vanilla about 3% of the water's pixels change strongly between
    frames.
  * Cosmos ships one sheet per page and painted the six frames as the same
    picture. Its cells for a position's six states are byte-identical, so
    FFNx with Cosmos shows still water as well.
  * The build's six HD cells differ only by quantisation noise (no pixel
    changes by more than 12/255). That is the faint flicker.

FIX: detail transfer. For each animated position the HD cell is the base
picture, and the motion is taken from vanilla:
    HD_k = HD_base + upsample(V_k - V_ref)
  * V_k is the vanilla frame k at 1x, with its own palette.
  * V_ref is the vanilla frame that best matches the HD art downsampled
    (what Cosmos painted).
  * The difference is upsampled with a bicubic filter, so the ripples are as
    soft as the art around them.
The new cells go on depth-2 pages in free opaque slots, and only the
animated records are repointed. Positions, states, z and every other record
are untouched. Refused (field byte-identical) unless the HD frames really
are near-identical and the vanilla frames really differ, and the field stays
inside the memory and raw caps.

FIELDS is the measured list; SEVENTH_NX_NO_ANIM_DETAIL=1 disables.
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

import diag_common as DC
import field_bg_native as FN
import field_bg_repack as FR

OFF_ENV = 'SEVENTH_NX_NO_ANIM_DETAIL'
FIELDS = {'sea': (2, 1), 'ithill': (2, 1)}   # field: (layer, param)
# BUILD 618p. ithill (hardware 10-03, "alternating between 2 frames, slow"):
# the water is layer 2 param 1, 3 states cycled by the script (none 25f ->
# s0 5f -> s1 5f -> s1+s2 15f -> s1 10f -> s0 10f). In vanilla 2-5% of the
# pixels change between states; Cosmos's three cells are identical (0.01%),
# so only "water on / water off" was left. Cosmos also widened the water by
# 68 cells (16:9 sides) that have no vanilla frames: their motion is the
# vanilla motion MIRRORED across the nearest vanilla column of the same row
# (MIRROR_FIELDS), then blurred with the rest, so the sides ripple with no
# seam at the old 4:3 edge.
MIRROR_FIELDS = frozenset({'ithill'})
# Fields whose "no state on" phase shows a static layer underneath. Cosmos
# painted ithill's overlay paler than its layer-1 water, so even vanilla's
# 25-frame "none" phase popped (12% of the pixels at once, vanilla 2.3%).
# The new frames are built on the HD layer-1 cell instead: HD_k = HD_under +
# blur/upsample(V_k - V_under).
UNDER = {'ithill': 1}
MIRROR_MATCH = 40                 # max channel difference, 0..255
GRID = 16
UV_CELL = 625000
OPAQUE_SLOTS = list(range(1, 15)) + [26, 27, 28]
BLUR = 0.8                                   # gaussian sigma, 1x texels
BLUR_FIELD = 1.6          # `under` fields: 1x highlights read as blocks at 3x
CEIL = 236                # ...and its ceiling: never flat white (618r)


def _erode(mask, n):
    out = mask.copy()
    for _ in range(n):
        m = out.copy()
        m[1:] &= out[:-1]
        m[:-1] &= out[1:]
        m[:, 1:] &= out[:, :-1]
        m[:, :-1] &= out[:, 1:]
        out = m
    return out


def _dilate(mask, n):
    return ~_erode(~mask, n)


def _front_mask(vparts, parts, layer, param, box):
    """1x mask (H*16, W*16) of static same-layer records drawn in front
    of the animated ones, vanilla OR HD, dilated by one texel."""
    import ff7nx_framesim as FS
    from PIL import Image
    x0, y0, W, H = box
    out = np.zeros((H * 16, W * 16), bool)
    for pp in (vparts, parts):
        f = FS.Field(pp)
        s9 = f.sec9
        zs = [struct.unpack_from('<I', s9, o + 38)[0] for lay, o in f.tiles
              if lay == layer and s9[o + 26] == param]
        if not zs:
            continue
        zmin = min(zs)
        for lay, o in f.tiles:
            if lay != layer or s9[o + 26] or s9[o + 28]:
                continue
            if struct.unpack_from('<I', s9, o + 38)[0] >= zmin:
                continue
            x, y = struct.unpack_from('<hh', s9, o + 2)
            i0, j0 = y - y0, x - x0
            if not (0 <= i0 < H * 16 and 0 <= j0 < W * 16):
                continue
            blk = f._block(o, lay, 16)
            if blk is None:
                continue
            op = ~blk[1]
            if op.shape[0] != 16:
                op = np.asarray(Image.fromarray(op.astype(np.uint8) * 255)
                                .resize((16, 16), Image.BOX)) > 0
            out[i0:i0 + 16, j0:j0 + 16] |= op
    return _dilate(out, 1)


def _blur(img, sigma):
    """Separable gaussian blur of an (H, W, C) float image, numpy only."""
    r = max(1, int(3 * sigma + 0.5))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / float(sigma)) ** 2)
    k /= k.sum()
    out = img
    for axis in (0, 1):
        pad = [(0, 0)] * img.ndim
        pad[axis] = (r, r)
        p = np.pad(out, pad, mode='edge')
        acc = np.zeros_like(out)
        for i, w in enumerate(k):
            sl = [slice(None)] * img.ndim
            sl[axis] = slice(i, i + out.shape[axis])
            acc += w * p[tuple(sl)]
        out = acc
    return out


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _cells(parts, layer, param):
    """{(x, y): {state: (rgb float HxWx3, key HxW, offset)}}."""
    import ff7nx_framesim as FS
    f = FS.Field(parts)
    s9 = f.sec9
    out = collections.defaultdict(dict)
    for lay, o in f.tiles:
        if lay != layer or s9[o + 26] != param:
            continue
        x, y = struct.unpack_from('<hh', s9, o + 2)
        blk = f._block(o, lay, 16)
        if blk is None:
            raise ValueError('cell at (%d,%d) unreadable' % (x, y))
        out[(x, y)][s9[o + 27]] = (blk[0].astype(np.float64), blk[1], o)
    return out, f


def _under(parts, layer):
    """{(x, y): rgb float} of the static, fully opaque cells of `layer`."""
    import ff7nx_framesim as FS
    f = FS.Field(parts)
    s9 = f.sec9
    out = {}
    for lay, o in f.tiles:
        if lay != layer or s9[o + 26] or s9[o + 28]:
            continue
        blk = f._block(o, lay, 16)
        if blk is None or blk[1].any():
            continue
        out[struct.unpack_from('<hh', s9, o + 2)] = blk[0].astype(np.float64)
    return out


def plan_field(vparts, parts, layer, param, mb_cap=None, raw_cap=None,
               mirror=False, median_ref=False, under=None):
    """(new section 9, info) -- raises on any doubt."""
    from PIL import Image
    van, _fv = _cells(vparts, layer, param)
    hd, f = _cells(parts, layer, param)
    if under is not None:
        vund, hund = _under(vparts, under), _under(parts, under)
        if not set(hd) <= set(hund):
            raise ValueError('no layer-%d cell under every position' % under)
        for pos in set(van) - set(vund):          # ithill: 1 corner cell
            vund[pos] = np.mean([v[0] for v in van[pos].values()], 0)
        if len(set(van) - set(_under(vparts, under))) > len(van) // 20:
            raise ValueError('vanilla layer %d missing under the cells' % under)
    if set(van) != set(hd) and not (mirror and set(van) < set(hd)):
        raise ValueError('animated positions differ from vanilla')
    px = f.px
    cs = px // GRID
    # BUILD 618n: work on WHOLE pictures, not cell by cell. 618m upsampled
    # each 16x16 difference on its own, which left cell-edge seams and the
    # 1x palette dither of the vanilla frames in the water ("dirty /
    # pixellated", hardware 10-03). Now: one picture per state, ONE global
    # reference frame, the difference blurred (gaussian, BLUR texels) at 1x
    # and upsampled once with a smooth filter over the whole region.
    xs = [p[0] for p in hd]
    ys = [p[1] for p in hd]
    x0, y0 = min(xs), min(ys)
    W = (max(xs) - x0) // 16 + 1
    H = (max(ys) - y0) // 16 + 1
    states = sorted(next(iter(hd.values())))
    for pos in hd:
        if sorted(hd[pos]) != states or (pos in van
                                         and sorted(van[pos]) != states):
            raise ValueError('states differ at %r' % (pos,))
    keys = states + (['none'] if under is not None else [])
    vimg = {st: np.zeros((H * 16, W * 16, 3)) for st in keys}
    have = np.zeros((H * 16, W * 16), bool)
    vhave = np.zeros((H * 16, W * 16), bool)
    base = np.zeros((H * cs, W * cs, 3))
    hd_spread = []
    for (x, y), sts in hd.items():
        i0, j0 = (y - y0) // 16, (x - x0) // 16
        b = np.mean([v[0] for v in sts.values()], 0)
        hd_spread.append(max(np.abs(v[0] - b).max() for v in sts.values()))
        if under is not None:
            # BUILD 618p: the base is what shows with NO state on (the HD
            # cell underneath), so the "none" phase and the states share one
            # picture and differ only by the vanilla motion.
            b = hund[(x, y)]
        base[i0 * cs:(i0 + 1) * cs, j0 * cs:(j0 + 1) * cs] = b
        have[i0 * 16:(i0 + 1) * 16, j0 * 16:(j0 + 1) * 16] = True
        if (x, y) not in van:
            continue
        vhave[i0 * 16:(i0 + 1) * 16, j0 * 16:(j0 + 1) * 16] = True
        cell = (slice(i0 * 16, (i0 + 1) * 16), slice(j0 * 16, (j0 + 1) * 16))
        for st in states:
            rgb, key = van[(x, y)][st][0], van[(x, y)][st][1]
            if under is not None:
                rgb = np.where(key[..., None], vund[(x, y)], rgb)
            vimg[st][cell] = rgb
        if under is not None:
            vimg['none'][cell] = vund[(x, y)]
    mirrored = int((have & ~vhave).sum() // 256)
    if mirrored:
        # each 16-row band: mirror-repeat the vanilla run outwards
        for i0 in range(H):
            band = slice(i0 * 16, (i0 + 1) * 16)
            cols = np.flatnonzero(vhave[i0 * 16])
            miss = np.flatnonzero(have[i0 * 16] & ~vhave[i0 * 16])
            if not len(miss):
                continue
            if not len(cols) or len(cols) != cols[-1] - cols[0] + 1:
                raise ValueError('band %d: vanilla run not contiguous' % i0)
            a, b = cols[0], cols[-1] + 1
            n = b - a
            for c in miss:
                k = (c - a) % (2 * n)          # symmetric (mirror) repeat
                src = a + k if k < n else a + 2 * n - 1 - k
                for st in keys:
                    vimg[st][band, c] = vimg[st][band, src]
    small = np.asarray(Image.fromarray(np.clip(base, 0, 255).astype(
        np.uint8)).resize((W * 16, H * 16), Image.BOX)).astype(np.float64)
    if under is not None:
        vref, ref = vimg['none'], 'none'
    elif median_ref:
        # BUILD 618p: the motion is taken against the per-pixel MEDIAN frame.
        # ithill's states are overlays (a patch shows in one state only); a
        # single reference frame carrying such a patch turned it into a
        # negative (magenta) patch in every other state.
        vref = np.median(np.stack([vimg[st] for st in states]), 0)
        ref = None
    else:
        ref = min(states,
                  key=lambda st: np.abs(vimg[st] - small)[vhave].mean())
        vref = vimg[ref]
    # Mirrored (16:9-only) pixels take motion only where the picture under
    # them matches the vanilla picture the motion was mirrored from: water
    # ripples land on water, never on Cosmos's rocks or beams.
    gate = np.ones(have.shape)
    if mirrored and under is not None:
        same = np.abs(small - vimg['none']).max(-1) < MIRROR_MATCH
        if under is not None:
            # 618q: mirrored motion only onto water-coloured HD (green over
            # red): a mirrored tide line once landed on a wood plank
            same &= (small[..., 1] - small[..., 0]) > 12
        gate[have & ~vhave] = same[have & ~vhave]
    if under is not None:
        # BUILD 618q (ithill, hardware 10-03). Two more places where the
        # vanilla motion must not land:
        #  * wherever the HD picture underneath differs from the vanilla one
        #    (Cosmos moved an edge, or painted wood where vanilla had water):
        #    the vanilla change there belongs to a different picture;
        #  * under and around the static records drawn IN FRONT of the
        #    water (the beams): vanilla's motion runs up to vanilla's beam
        #    edge, so on the HD beam it drew a coloured outline.
        same = np.abs(small - vimg['none']).max(-1) < MIRROR_MATCH
        gate *= same
        gate *= ~_front_mask(vparts, parts, layer, param, (x0, y0, W, H))
        gate = _erode(gate > 0.5, 1).astype(np.float64)
        gate = _blur(gate[..., None], 0.7)[..., 0]
    v_spread = []
    new = {}
    for st in states:
        d = (vimg[st] - vref) * gate[..., None]
        v_spread.append(float((np.abs(d).max(-1) > 24)[vhave].mean()))
        bl = BLUR_FIELD if under is not None else BLUR
        d = _blur(d * have[..., None], bl) / np.maximum(
            _blur(have[..., None].astype(np.float64), bl), 1e-3)
        up = np.stack([np.asarray(Image.fromarray(d[..., c].astype(
            np.float32), 'F').resize((W * cs, H * cs), Image.BICUBIC))
            for c in range(3)], -1)
        if under is not None:
            # 618r: a brightening change may only use the headroom between
            # the HD picture and CEIL, and approaches it smoothly. A hard
            # clip at white (618p) and a shoulder on the sum (618q) both
            # left flat white 1x-shaped blocks (hardware 10-03); the
            # picture itself (the "no state" phase) is never darkened.
            room = np.maximum(0.0, CEIL - base)
            pos = up > 0
            up = np.where(pos, room * np.tanh(np.where(pos, up, 0)
                                              / np.maximum(room, 1.0)), up)
        frame = base + up
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        for (x, y), sts in hd.items():
            i0, j0 = (y - y0) // 16, (x - x0) // 16
            # 618r: with `under`, the frame IS the picture underneath plus
            # the motion, so Cosmos's transparent pixels are made opaque.
            # Kept, they let the bright layer-1 cell show through as flat
            # white blocks inside a darker ripple (hardware 10-03).
            key = (np.zeros_like(sts[st][1]) if under is not None
                   else sts[st][1])
            new[((x, y), st)] = (frame[i0 * cs:(i0 + 1) * cs,
                                       j0 * cs:(j0 + 1) * cs], key)
    if np.mean(hd_spread) > 24:
        raise ValueError('HD frames already differ (%.1f); nothing to do'
                         % np.mean(hd_spread))
    if np.mean(v_spread) < 0.005:
        raise ValueError('vanilla frames do not move')
    order = sorted(new)
    need = -(-len(order) // (GRID * GRID))
    sec9 = parts[8]
    plist, _t0, _t1 = FN.parse_texture_block(sec9, px)
    present = {p.slot for p in plist if p is not None}
    free = [s for s in OPAQUE_SLOTS if s not in present]
    if len(free) < need:
        raise ValueError('need %d opaque slots, %d free' % (need, len(free)))
    slots = free[:need]
    pages = {s: np.zeros((px, px, 4), np.uint8) for s in slots}
    buf = bytearray(sec9)
    for i, (pos, st) in enumerate(order):
        s = slots[i // (GRID * GRID)]
        n = i % (GRID * GRID)
        nx, ny = n % GRID, n // GRID
        rgb, key = new[(pos, st)]
        pages[s][ny * cs:(ny + 1) * cs, nx * cs:(nx + 1) * cs, :3] = rgb
        pages[s][ny * cs:(ny + 1) * cs, nx * cs:(nx + 1) * cs, 3] = \
            np.where(key, 0, 255)
        o = hd[pos][st][2]
        if buf[o + 28]:
            raise ValueError('animated record is an FX tile')
        buf[o + 32] = s
        struct.pack_into('<hh', buf, o + 10, nx * 16, ny * 16)
        struct.pack_into('<II', buf, o + 42, nx * UV_CELL, ny * UV_CELL)
    plist, t0, t1 = FN.parse_texture_block(bytes(buf), px)
    for s, img in pages.items():
        plist[s] = FN.Page(s, 0, 2, FR.rgba_to_565_buf(
            img.tobytes(), px * px, width=px), px)
    # drop pages nothing references any more
    _pl, ts, _te, _px = DC.parse_pages(bytes(buf))
    used = set()
    for _lay, offs in DC.walk_layers(bytes(buf), bytes(buf).find(b'BACK'),
                                     ts):
        for o in offs:
            used.add(buf[o + 32])
            if buf[o + 28]:
                used.add(buf[o + 34])
    dropped = [p.slot for p in plist if p is not None and p.slot not in used]
    for s in dropped:
        plist[s] = None
    run = sum(FR._page_bytes(p.px, p.depth) for p in plist if p is not None)
    if mb_cap is not None and run > mb_cap * 1048576.0:
        raise ValueError('%.2f MB over the %.0f MB cap'
                         % (run / 1048576.0, mb_cap))
    out = FN.replace_texture_block(bytes(buf), plist, t0, t1)
    other = sum(len(x) for x in parts) - len(sec9)
    if raw_cap is not None and other + len(out) > raw_cap:
        raise ValueError('raw %d over cap %d' % (other + len(out), raw_cap))
    import field_bg_pagecap as PC
    counts = PC.effective_counts(out, px)
    over = {k: v for k, v in counts.items() if v > PC.MAX_TILES_PER_PAGE}
    if over:
        raise ValueError('binding-page cap: %s' % over)
    return out, {'cells': len(order), 'slots': slots, 'dropped': dropped,
                 'hd_spread': round(float(np.mean(hd_spread)), 1),
                 'vanilla_moving': round(float(np.mean(v_spread)), 4),
                 'mb': round(run / 1048576.0, 2), 'mirrored': mirrored,
                 'gated': round(float(gate[have & ~vhave].mean()), 3)
                 if mirrored else None}


def apply_to_flevel(archive, payloads, vanilla, encode=None, mb_cap=None,
                    raw_cap=None, log=lambda *_: None):
    """`vanilla`: an lgp.Archive of the untouched game flevel."""
    import lgp
    total = {'names': [], 'refused': []}
    if disabled() or vanilla is None:
        return total
    encode = encode or archive.encode_field
    for name, (layer, param) in FIELDS.items():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry) \
                or name not in vanilla.index:
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            vparts = list(lgp.split_sections(vanilla.decompressed(
                vanilla.index[name])))
            parts[8], info = plan_field(vparts, parts, layer, param,
                                        mb_cap=mb_cap, raw_cap=raw_cap,
                                        mirror=name in MIRROR_FIELDS,
                                        under=UNDER.get(name))
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append((name, str(exc)[:90]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['names'].append('%s: %d animated cell(s) on pages %s (%.2f MB)'
                              % (name, info['cells'], info['slots'],
                                 info['mb']))
    return total


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  ANIMATED DETAIL (BUILD 618m): motion of single-frame '
                   'Cosmos animations restored from vanilla (%s). %s=1 '
                   'disables.' % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! animated detail %s: %s' % (name, why))
    return '\n'.join(out)
