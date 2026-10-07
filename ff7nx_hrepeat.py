#!/usr/bin/env python3
"""ff7nx_hrepeat.py -- horizontally scrolling layers that are narrower than
the 16:9 view, repeated exactly; and the camera kept where the art is.
BUILD 618z11.

ztruck (hardware 10-06, video): "the scrolling artwork is all screwed up,
not filling the screen, textures are popping into view like how the
railroad track had problems before" / "the field is bouncing slightly,
revealing a black region on the top" / "a black region below the truck".

SCROLL. Layers 3 (sky and far desert) and 4 (near desert) are 11 columns of
32-unit tiles, a seamless 352-unit period that the script scrolls with
BGSCR (x speed 10 and 100, halved to 5 and 50 by ff7nx_bgscr60) while the
header pins them (speed 0, width 352). The engine wraps every tile ONCE at
the header width, so the 427-unit view always has a 75-unit stretch with
nothing in it, sliding across the screen. The generic wide fill had added
four copies per row at x -592/-560/528/560 that fold through the 352 wrap
onto other columns (the popping). FFNx draws the set again one period to
the side; the port only has the header width, so -- as ff7nx_trnad4 does
for the 352x256 grids (trnad_4, zcoal, woa) -- the layer becomes one
704-unit period: the authored 11 columns of every row, plus the same
records one period to the right, header width 704. The art repeats every
352 units, so the picture is unchanged; nothing folds and nothing is
missing. Refused unless every row holds exactly the 11 authored columns.

ZOOM AND CAMERA. FFNx shows ztruck with WM_ZOOM (Cosmos's widescreen
config: mode = 2, x -175..175): 350 units across, inside the 352 the art
has, and it widens the camera range to y -142..142 to suit. The port now
zooms it the same way (ff7nx_fieldzoom, factor 426.67/350), so the view is
350 x 197 units. The port clamps the camera into [range + 120] whatever the
zoom, and the party sits low, so with -142..142 it rests at y +22 -- on
hardware, unzoomed, 23 units of nothing under the truck; zoomed, the view's
bottom row is y 120.4 and the art's bottom-left ends at 120. The range
becomes y -104..136 (240 tall, so the camera is pinned, as vanilla's
-120..120 pins it): y +16, the view -82..114, and the SHAKE (4 units) stays
inside the art both ways. Measured over every scroll position of a full
period: nothing undrawn in the zoomed view for camera y -20..20.

SEVENTH_NX_NO_HREPEAT=1 disables.
"""
from __future__ import annotations

import os
import struct

OFF_ENV = 'SEVENTH_NX_NO_HREPEAT'
TILE = 32
# field -> (layers, authored x0, authored period, new camera y range or None)
FIELDS = {'ztruck': ((3, 4), -176, 352, (-104, 136))}
REC = 52
HDR_W = {3: 0x18, 4: 0x1C}                 # bg3_w / bg4_w in section 7


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def plan(name, sec7, sec9):
    """(new section 7, new section 9, info) -- raises on any doubt."""
    import diag_common as DC
    import ff7nx_parallaxfill as PF
    layers, x0, period, cam_y = FIELDS[name]
    hdr = PF.trigger_header(sec7)
    s7 = bytearray(sec7)
    info = {'layers': {}}
    buf = bytes(sec9)
    for layer in layers:
        if hdr['bg%d_w' % layer] != period:
            raise ValueError('layer %d header width %d, expected %d'
                             % (layer, hdr['bg%d_w' % layer], period))
        if hdr['bg%d_speed_x' % layer] or hdr['bg%d_speed_y' % layer]:
            raise ValueError('layer %d has a header speed' % layer)
        sv = DC.survey(buf)
        rows_ = PF._layers(buf, sv['back_start'], sv['tex_start'])
        hit = [r for r in rows_ if r[0] == layer]
        if len(hit) != 1:
            raise ValueError('no single layer %d' % layer)
        _l, count_at, first, n = hit[0]
        recs = [buf[first + i * REC:first + (i + 1) * REC] for i in range(n)]
        cols = [x0 + i * TILE for i in range(period // TILE)]
        keep, rows = [], {}
        for r in recs:
            x, y = struct.unpack_from('<hh', r, 2)
            if struct.unpack_from('<HH', r, 18) != (TILE, TILE):
                raise ValueError('layer %d tile is not 32x32' % layer)
            if r[26]:
                raise ValueError('layer %d has animated records' % layer)
            if x in cols:
                if (x, y) in rows:
                    raise ValueError('two layer-%d records at %d,%d'
                                     % (layer, x, y))
                rows[(x, y)] = r
                keep.append(r)
        ys = sorted({y for _x, y in rows})
        if any((x, y) not in rows for y in ys for x in cols):
            raise ValueError('layer %d rows are not the full %d columns'
                             % (layer, len(cols)))
        copies = []
        for r in keep:
            c = bytearray(r)
            x = struct.unpack_from('<h', r, 2)[0]
            struct.pack_into('<h', c, 2, x + period)
            copies.append(bytes(c))
        block = b''.join(keep + copies)
        b = bytearray(buf)
        b[first:first + n * REC] = block
        struct.pack_into('<H', b, count_at, len(keep) + len(copies))
        buf = bytes(b)
        struct.pack_into('<h', s7, HDR_W[layer], period * 2)
        info['layers'][layer] = {'dropped': n - len(keep),
                                 'records': len(keep) + len(copies),
                                 'rows': len(ys)}
    if cam_y is not None:
        left, bottom, right, top = struct.unpack_from('<4h', s7, 0x0C)
        lo, hi = cam_y
        struct.pack_into('<4h', s7, 0x0C, left, lo, right, hi)
        info['cam_y'] = ((bottom, top), (lo, hi))
    # the result must still parse
    DC.parse_pages(buf)
    return bytes(s7), buf, info


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
            parts[7], parts[8], info = plan(name, parts[7], parts[8])
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:100]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['names'].append('%s: layers %s -> one %d-unit period%s' % (
            name, ','.join('%d (%d dropped)' % (l_, v['dropped'])
                           for l_, v in info['layers'].items()),
            FIELDS[name][2] * 2,
            '; camera y range %s -> %s' % info['cam_y']
            if 'cam_y' in info else ''))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  H-REPEAT (BUILD 618z11): scrolling layers narrower than '
                   '16:9 repeated exactly, camera kept on the art (%s). '
                   '%s=1 disables.' % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! h-repeat %s: not applied -- %s' % (name, why))
    return '\n'.join(out)
