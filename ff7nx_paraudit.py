#!/usr/bin/env python3
"""ff7nx_paraudit.py -- whole-archive audit of the scrolling backdrop layers.

BUILD 618k. Four fields reported from hardware in one sitting (trnad_1, sky,
zcoal_1, zcoal_3) had the same defect: a 352x256 layer-3 backdrop that the
SCRIPT scrolls (BGSCR; the header speed is 0) drew black bars, gaps and
repeated strips in 16:9. The exact-repeat fix (ff7nx_trnad4) existed, but
only for a hand-made list of fields. FFNx needs no list: for EVERY field it
doubles the layer-3/4 height (uncrop), doubles the width when it is under
427 units (widescreen), and draws the tile set again at (0,H), (W,0), (W,H)
(background.cpp, field_layer3_pick_tiles / field_layer4_pick_tiles).

This module answers, for every layer 3/4 in an archive, without hardware:
  * can it move, and over which bg positions (header speed x camera range,
    and every BGSCR the field's scripts issue for that layer -- constant
    speeds sweep one half-period in their direction, variable speeds both);
  * is it a full backdrop in vanilla (its base tiles, param 0, cover the 4:3
    picture at every reachable position under the stock wrap constants);
  * does the BUILT layer cover the 16:9 picture (427x240) at every reachable
    position under the port's wrap constants as shipped, and how many tiles
    are drawn twice.

A layer that was a full backdrop at 4:3 and is not one at 16:9 is a defect
of the class reported, whatever field it is in.

The wrap model (all verified against the port before, see ff7nx_trnad4 and
ff7nx_wsclamp): x -- shift once by +-W if x <= bg-459 or x >= bg+107
(direction x >= bg-213); layer 4 then culls to bg-459 < x < bg+107; layer 3
has no cull. y -- shift once by +-H if y <= bg-264 or y >= bg+16; layer 3
direction y >= bg-120, layer 4 direction y >= bg+16; no y cull. Position:
t = pos/16 + speed*camera/256, t %= W (truncating), bg.x = t + 320 - 160,
bg.y = t_y + 232 - 120. Picture: tile - bg in [-373.5, 53.5] x [-232, 8].
"""
from __future__ import annotations

import collections
import struct

import numpy as np

import diag_common as DC
import ff7nx_parallaxfill as PF

OP_BGSCR = 0x2D

PORT = {'L': 459, 'R': 107, 'HW': 213, 'T': 264, 'B': 16, 'HH': 120}
STOCK = {'L': 352, 'R': 0, 'HW': 160, 'T': 256, 'B': 0, 'HH': 112}
PIC_169 = (-373.5, 53.5, -232.0, 8.0)
PIC_43 = (-320.0, 0.0, -224.0, 0.0)
OFF_X, OFF_Y = 160, 120
CELL = 4


def bgscr_ops(script_section):
    """{layer(3|4): [(x_speed|None, y_speed|None)]} -- None = variable."""
    import echo_s_flevel as ES
    out = collections.defaultdict(list)
    try:
        blocks = ES._routine_blocks(script_section)
    except Exception:                                          # noqa: BLE001
        return out
    for a, b in blocks:
        stream, _ = ES._decode_block(script_section, a, b)
        for off, op, size in stream:
            if op != OP_BGSCR or size < 7:
                continue
            banks, layer = script_section[off + 1], script_section[off + 2]
            sx, sy = struct.unpack_from('<hh', script_section, off + 3)
            if layer not in (2, 3):
                continue
            out[layer + 1].append((None if banks >> 4 else sx,
                                   None if banks & 15 else sy))
    return out


def layer_tiles(sec9, layer, base_only=True):
    """[(x, y, size)] of a layer's records (param 0 only by default)."""
    _pl, ts, _te, _px = DC.parse_pages(sec9)
    for lay, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        if lay != layer:
            continue
        out = []
        for o in offs:
            if base_only and sec9[o + 26]:
                continue
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            size = struct.unpack_from('<H', sec9, o + 18)[0] or 32
            out.append((x, y, size))
        return out
    return []


def _t_range(width, pos, speed, cam_lo, cam_hi, ops_axis):
    """Reachable t (before + 320 - off) on one axis as (lo, hi)."""
    vals = [pos / 16.0 + speed * c / 256.0 for c in (cam_lo, cam_hi)]
    lo, hi = min(vals), max(vals)
    for s in ops_axis:
        if s is None:
            lo, hi = min(lo, -width + 1), max(hi, width - 1)
        elif s > 0:
            hi = max(hi, width - 1)
            lo = min(lo, 0)
        elif s < 0:
            lo = min(lo, -width + 1)
            hi = max(hi, 0)
    # The truncating modulo keeps |t| < W whatever the camera does.
    lo, hi = max(lo, -width + 1), min(hi, width - 1)
    return lo, hi


def reach(hdr, layer, ops):
    """((bgx_lo, bgx_hi), (bgy_lo, bgy_hi), moves)."""
    w, h = hdr['bg%d_w' % layer], hdr['bg%d_h' % layer]
    cx = sorted((hdr['cam_left'] + 160, hdr['cam_right'] - 160))
    cy = sorted((hdr['cam_bottom'] + 112, hdr['cam_top'] - 112))
    if cx[0] > cx[1]:
        cx = [sum(cx) / 2.0] * 2
    if cy[0] > cy[1]:
        cy = [sum(cy) / 2.0] * 2
    tx = _t_range(w, hdr['bg%d_pos_x' % layer], hdr['bg%d_speed_x' % layer],
                  cx[0], cx[1], [o[0] for o in ops])
    ty = _t_range(h, hdr['bg%d_pos_y' % layer], hdr['bg%d_speed_y' % layer],
                  cy[0], cy[1], [o[1] for o in ops])
    moves = (tx[1] - tx[0] > 0.5) or (ty[1] - ty[0] > 0.5)
    return ((tx[0] + 320 - OFF_X, tx[1] + 320 - OFF_X),
            (ty[0] + 232 - OFF_Y, ty[1] + 232 - OFF_Y), moves)


def drawn(tiles, layer, bgx, bgy, w, h, k):
    """Drawn (x, y, size) arrays after the shift (and layer-4 x cull)."""
    x = tiles[:, 0].astype(np.float64)
    y = tiles[:, 1].astype(np.float64)
    sx = (x <= bgx - k['L']) | (x >= bgx + k['R'])
    x = np.where(sx, np.where(x >= bgx - k['HW'], x - w, x + w), x)
    sy = (y <= bgy - k['T']) | (y >= bgy + k['B'])
    if layer == 3:
        down = y >= bgy - k['HH']
    else:
        down = y >= bgy + k['B']
    y = np.where(sy, np.where(down, y - h, y + h), y)
    keep = np.ones(len(x), bool)
    if layer == 4:
        keep = (x > bgx - k['L']) & (x < bgx + k['R'])
    return x[keep], y[keep], tiles[keep, 2]


def coverage(tiles, layer, bgx, bgy, w, h, k, pic):
    """(uncovered fraction of the picture, doubled-cell fraction)."""
    x, y, s = drawn(tiles, layer, bgx, bgy, w, h, k)
    x0, x1, y0, y1 = bgx + pic[0], bgx + pic[1], bgy + pic[2], bgy + pic[3]
    nx = int(np.ceil((x1 - x0) / CELL))
    ny = int(np.ceil((y1 - y0) / CELL))
    m = np.zeros((ny, nx), np.int16)
    for xi, yi, si in zip(x, y, s):
        # a cell counts as covered when its CENTRE is inside the tile
        j0 = int(max(0, np.ceil((xi - x0) / CELL - 0.5)))
        j1 = int(min(nx, np.ceil((xi + si - x0) / CELL - 0.5)))
        i0 = int(max(0, np.ceil((yi - y0) / CELL - 0.5)))
        i1 = int(min(ny, np.ceil((yi + si - y0) / CELL - 0.5)))
        if j1 > j0 and i1 > i0:
            m[i0:i1, j0:j1] += 1
    return float((m == 0).mean()), float((m > 1).mean())


def _samples(lo, hi, step):
    if hi - lo < 0.5:
        return [lo]
    n = max(2, int((hi - lo) / step) + 1)
    return list(np.linspace(lo, hi, n))


def sweep(tiles, layer, rx, ry, w, h, k, pic, step=4):
    """Worst (uncovered, doubled, bgx, bgy) over the reachable box."""
    arr = np.array(tiles, np.int32).reshape(-1, 3)
    if not len(arr):
        return (1.0, 0.0, None, None)
    worst = (0.0, 0.0, None, None)
    for by in _samples(ry[0], ry[1], max(step, 8)):
        for bx in _samples(rx[0], rx[1], step):
            u, d = coverage(arr, layer, bx, by, w, h, k, pic)
            if (u, d) > worst[:2]:
                worst = (u, d, bx, by)
    return worst


def audit_field(name, vparts, bparts):
    """[row] for each layer 3/4 of a field (vanilla parts, built parts)."""
    rows = []
    vh = PF.trigger_header(vparts[7])
    bh = PF.trigger_header(bparts[7])
    ops = bgscr_ops(vparts[0])
    for layer in (3, 4):
        vt = layer_tiles(vparts[8], layer)
        if not vt:
            continue
        bt = layer_tiles(bparts[8], layer)
        vrx, vry, moves = reach(vh, layer, ops.get(layer, []))
        brx, bry, _ = reach(bh, layer, ops.get(layer, []))
        vw, vhh = vh['bg%d_w' % layer], vh['bg%d_h' % layer]
        bw, bhh = bh['bg%d_w' % layer], bh['bg%d_h' % layer]
        v = sweep(vt, layer, vrx, vry, vw, vhh, STOCK, PIC_43, step=8)
        b = sweep(bt, layer, brx, bry, bw, bhh, PORT, PIC_169, step=4)
        rows.append({
            'field': name, 'layer': layer, 'moves': moves,
            'bgscr': ops.get(layer, []), 'vanilla_wh': (vw, vhh),
            'built_wh': (bw, bhh), 'tiles': (len(vt), len(bt)),
            'vanilla_uncovered': round(v[0], 4),
            'built_uncovered': round(b[0], 4), 'built_doubled': round(b[1], 4),
            'worst_at': (b[2], b[3]),
            'defect': v[0] == 0.0 and b[0] > 0.0})
    return rows


def audit_archives(vanilla, built, names=None, log=lambda *_: None,
                   chunk_reader=None):
    """`chunk_reader`: an IroReader on CosmosLimitBreak.iro. Its chunk.9,
    when it ships one, replaces the vanilla section 9 -- that widened layout
    is what FFNx draws, so it is the reference."""
    import lgp
    out = []
    for name in (names or built.names()):
        try:
            if name not in vanilla.index or not built.is_field(
                    built.index[name]):
                continue
            vp = list(lgp.split_sections(vanilla.decompressed(
                vanilla.index[name])))
            bp = list(lgp.split_sections(built.decompressed(
                built.index[name])))
            if chunk_reader is not None:
                c9 = chunk_reader.read('limit break/flevel.lgp/%s.chunk.9'
                                       % name)
                if c9:
                    vp[8] = c9
            if vp[8].find(b'BACK') < 0:
                continue
            out.extend(audit_field(name, vp, bp))
        except Exception as exc:                               # noqa: BLE001
            log('! paraudit %s: %s' % (name, exc))
    return out
