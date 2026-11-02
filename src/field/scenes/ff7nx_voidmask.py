"""ff7nx_voidmask.py -- models stay hidden in the empty 16:9 margin. BUILD 879.

HARDWARE 11-02, holu_1 (Holzoff's hut): "when my character model walks into
the door on the right, parts of the character texture peek into the
blackened expanded region. in that region, textures should go under the
black not over, so masked."

WHY. holu_1's background is the hut alone: every texel outside its outline
is the colour key, and the frame behind is cleared to black. Nothing is
drawn in the 16:9 margin at all (the art ends at x = -184 and x = 184). A
model is depth-tested against background texels only, so wherever there is
no texel there is nothing to go behind, and a body walking out through the
right-hand door is drawn over the black. In 4:3 the frame edge (x = +-160)
cut it off, which is what the scene was composed for.

FIX. Reproduce that edge: in the widened part of the frame (|x| >= 160)
every texel no background tile paints becomes opaque near-black (0x0841,
the art's own black, which HD_BLACK_POINT shows as 0) at the field's
front-most depth (ID 1, the door frame's), on new layer-2 records that draw
from a new truecolor page in slot 28 (a slot vanilla loads for truecolor
pages). Where any tile paints a texel the mask is transparent there, so no
art is covered: the picture is unchanged pixel for pixel, and only what is
drawn over the empty margin goes under it.

SAFETY. Only the fields listed in FIELDS. The pass refuses (and leaves the
field as it was) unless: the camera is fixed horizontally on the 4:3 frame
(range x -160..160); slot 28 is free; no animated or effect tile lies in
the masked columns; the new page stays inside the 256-records-per-page
limit. Existing records and pages are copied byte for byte, and the result
is re-parsed and compared before it is kept.

SEVENTH_NX_NO_VOIDMASK=1 disables.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_VOIDMASK'
FIELDS = {'holu_1': {}}
REC = 52
TILE = 16
HALF_43 = 160                  # the 4:3 frame's half width, tile units
WIDE_HALF = 427.0 / 2          # the 16:9 frame's
UNCROP_EXTRA = 8               # 240-unit view against the 224 the range assumes
SLOT = 28                      # opaque truecolor band, loaded by vanilla
BLACK = 0x0841                 # field_bg_native.NEAR_BLACK, the art's black
MAX_PER_PAGE = 256
UV = 10_000_000


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _rows(s9):
    import ff7nx_floordepth as FD
    return FD._rows(s9)


def _coverage(s9, pm, k, x0, y0, w, h):
    """(bool [h*k, w*k] of texels some tile paints, [(x, y) of animated or
    effect tiles]) over tile units x0..x0+w, y0..y0+h."""
    cov = np.zeros((h * k, w * k), bool)
    moving = []
    arrs = {}
    for _lay, _c, first, n in _rows(s9):
        for i in range(n):
            r = s9[first + i * REC:first + (i + 1) * REC]
            x, y = struct.unpack_from('<hh', r, 2)
            tw, th = struct.unpack_from('<HH', r, 18)
            if r[28] or r[26] or r[27]:
                moving.append((x, y, max(tw, th, TILE)))
            p = pm.get(r[32])
            if p is None:
                raise ValueError('tile at %s draws from missing slot %d'
                                 % ((x, y), r[32]))
            u, v = struct.unpack_from('<II', r, 42)
            n_ = max(tw, th) if max(tw, th) in (16, 32) else TILE
            if p.depth == 2:
                pk = p.px // 256
                if p.slot not in arrs:
                    arrs[p.slot] = np.frombuffer(p.data, '<u2').reshape(
                        p.px, p.px)
                px0 = int(round(u / UV * 256)) * pk
                py0 = int(round(v / UV * 256)) * pk
                m = arrs[p.slot][py0:py0 + n_ * pk, px0:px0 + n_ * pk] != 0
                if pk != k:
                    raise ValueError('page %d is %dx, expected %dx'
                                     % (p.slot, pk, k))
            else:
                if p.slot not in arrs:
                    arrs[p.slot] = np.frombuffer(p.data, np.uint8).reshape(
                        256, 256)
                px0 = int(round(u / UV * 256))
                py0 = int(round(v / UV * 256))
                m = arrs[p.slot][py0:py0 + n_, px0:px0 + n_] != 0
                m = np.repeat(np.repeat(m, k, 0), k, 1)
            # clip to the window
            ox, oy = (x - x0) * k, (y - y0) * k
            a0, b0 = max(0, -oy), max(0, -ox)
            a1 = min(m.shape[0], h * k - oy)
            b1 = min(m.shape[1], w * k - ox)
            if a1 > a0 and b1 > b0:
                cov[oy + a0:oy + a1, ox + b0:ox + b1] |= m[a0:a1, b0:b1]
    return cov, moving


def plan(sec7, s9):
    """(new section 9, records added, distinct mask cells)."""
    import diag_common as DC
    import field_bg_native as FN
    import ff7nx_parallaxfill as PF
    hdr = PF.trigger_header(sec7)
    if (hdr['cam_left'], hdr['cam_right']) != (-HALF_43, HALF_43):
        raise ValueError('camera range x %d..%d is not the fixed 4:3 frame'
                         % (hdr['cam_left'], hdr['cam_right']))
    plist, t0, t1, px = DC.parse_pages(s9)
    pm = {p.slot: p for p in plist if p is not None}
    if pm.get(SLOT) is not None:
        raise ValueError('slot %d is in use' % SLOT)
    d2 = [p for p in pm.values() if p.depth == 2]
    if not d2:
        raise ValueError('no truecolor page to match')
    k = d2[0].px // 256
    rows = _rows(s9)
    lay2 = [r for r in rows if r[0] == 2]
    if not lay2:
        raise ValueError('no layer 2')
    # tile grid phase, from the art itself
    phases = set()
    tmpl = None
    for lay, _c, first, n in rows:
        for i in range(n):
            r = s9[first + i * REC:first + (i + 1) * REC]
            x, y = struct.unpack_from('<hh', r, 2)
            phases.add((x % TILE, y % TILE))
            idv = struct.unpack_from('<h', r, 24)[0]
            if (lay == 2 and not (r[26] or r[27] or r[28])
                    and pm[r[32]].depth == 2
                    and struct.unpack_from('<HH', r, 18) == (TILE, TILE)
                    and (tmpl is None or idv < tmpl[0])):
                tmpl = (idv, bytes(r))
    if len(phases) != 1 or tmpl is None:
        raise ValueError('tile grid phases %s / no layer-2 template'
                         % sorted(phases))
    gx, gy = phases.pop()
    # the frame the player can see, in tile units
    vx0 = hdr['cam_left'] + HALF_43 - WIDE_HALF
    vx1 = hdr['cam_right'] - HALF_43 + WIDE_HALF
    ylo = min(hdr['cam_top'], hdr['cam_bottom']) - UNCROP_EXTRA
    yhi = max(hdr['cam_top'], hdr['cam_bottom']) + UNCROP_EXTRA
    cx0 = int(np.floor((vx0 - gx) / TILE)) * TILE + gx
    cy0 = int(np.floor((ylo - gy) / TILE)) * TILE + gy
    cx1 = int(np.ceil((vx1 - gx) / TILE)) * TILE + gx
    cy1 = int(np.ceil((yhi - gy) / TILE)) * TILE + gy
    W, H = cx1 - cx0, cy1 - cy0
    cov, moving = _coverage(s9, pm, k, cx0, cy0, W, H)
    xs = (np.arange(W * k) / k) + cx0
    wide = (xs < -HALF_43) | (xs >= HALF_43)
    for mx, my, mn in moving:
        if mx < -HALF_43 or mx + mn > HALF_43:
            raise ValueError('animated / effect tile at %s in the margin'
                             % ((mx, my),))
    cells = {}           # pattern bytes -> cell index
    adds = []            # (x, y, cell index)
    for y in range(cy0, cy1, TILE):
        for x in range(cx0, cx1, TILE):
            a, b = (y - cy0) * k, (x - cx0) * k
            m = ~cov[a:a + TILE * k, b:b + TILE * k] & wide[b:b + TILE * k][None, :]
            if not m.any():
                continue
            key = np.packbits(m).tobytes()
            if key not in cells:
                cells[key] = (len(cells), m)
            adds.append((x, y, cells[key][0]))
    if not adds:
        return s9, 0, 0
    if len(adds) > MAX_PER_PAGE:
        raise ValueError('%d records is over the per-page limit' % len(adds))
    if len(cells) > 256:
        raise ValueError('too many distinct mask cells')
    side = TILE * k
    page = np.zeros((256 * k, 256 * k), np.uint16)
    for _key, (ci, m) in cells.items():
        cy_, cx_ = divmod(ci, 16)
        page[cy_ * side:(cy_ + 1) * side, cx_ * side:(cx_ + 1) * side] = (
            np.where(m, BLACK, 0))
    blob = bytearray()
    for x, y, ci in adds:
        cy_, cx_ = divmod(ci, 16)
        r = bytearray(tmpl[1])
        struct.pack_into('<hh', r, 2, x, y)
        struct.pack_into('<HH', r, 10, cx_ * TILE, cy_ * TILE)
        r[32] = SLOT
        struct.pack_into('<II', r, 42, int(round(cx_ * TILE * UV / 256.0)),
                         int(round(cy_ * TILE * UV / 256.0)))
        blob += r
    _lay, count_at, first, n = lay2[0]
    end = first + n * REC
    out = bytearray(s9)
    out[end:end] = blob
    struct.pack_into('<H', out, count_at, n + len(adds))
    shift = len(blob)
    plist2 = list(plist)
    while len(plist2) <= SLOT:
        plist2.append(None)
    plist2[SLOT] = FN.Page(SLOT, 0, 2, page.astype('<u2').tobytes(),
                           256 * k)
    out = FN.replace_texture_block(bytes(out), plist2, t0 + shift, t1 + shift)
    _verify(s9, out, len(adds), shift)
    return out, len(adds), len(cells)


def _verify(old, new, nadd, shift):
    """Every original record and page is still there, byte for byte."""
    import diag_common as DC
    r0, r1 = _rows(old), _rows(new)
    if [r[0] for r in r0] != [r[0] for r in r1]:
        raise ValueError('layer set changed')
    for a, b in zip(r0, r1):
        want = a[3] + (nadd if a[0] == 2 else 0)
        if b[3] != want:
            raise ValueError('layer %d has %d records, expected %d'
                             % (a[0], b[3], want))
        if old[a[2]:a[2] + a[3] * REC] != new[b[2]:b[2] + a[3] * REC]:
            raise ValueError('layer %d records changed' % a[0])
    p0, _s0, _e0, _ = DC.parse_pages(old)
    p1, _s1, _e1, _ = DC.parse_pages(new)
    for s, (a, b) in enumerate(zip(p0, p1)):
        if s == SLOT:
            continue
        if (a is None) != (b is None) or (a is not None and a.data != b.data):
            raise ValueError('page %d changed' % s)
    if p1[SLOT] is None or p0[SLOT] is not None:
        raise ValueError('mask page missing')
    if old[:r0[0][2]] != new[:r1[0][2]]:
        raise ValueError('section head changed')
    if old[_e0:] != new[_e1:]:
        raise ValueError('section tail changed')
    if new[r1[-1][2] + r1[-1][3] * REC:_s1] != old[
            r0[-1][2] + r0[-1][3] * REC:_s0]:
        raise ValueError('bytes between records and TEXTURE changed')


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': []}
    if disabled():
        return st
    encode = encode or archive.encode_field
    for name in FIELDS:
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[8], n, nc = plan(parts[7], parts[8])
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:120]))
            continue
        if n:
            payloads[name] = encode(lgp.join_sections(parts))
            st['names'].append('%s: %d margin square(s), %d mask cell(s)'
                               % (name, n, nc))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  VOID MASK (BUILD 879): models go under the empty 16:9 '
                   'margin -- %s. %s=1 disables.'
                   % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! void mask %s: %s' % (name, why))
    return '\n'.join(out)
