#!/usr/bin/env python3
"""ff7nx_wishift.py -- whitein: the city centred in 16:9. BUILD 618z.

whitein, hardware 10-05: "what i would really like is for the textures
overlayed on top of the background ocean (the staircase and the city and the
walk area) to be shifted to the right so that the city is centered perfectly
in the scene. the ocean and the light beams would remain the same."

WHY IT IS OFF CENTRE. whitein's art is 448 units wide in the build (1997's
-192..192 plus Cosmos's right margin to 256) against a 427-unit view, and
Cosmos's config (left -128, right 192) pins the view centre at x = 32. The
city's centre (the base ring) is at x = -6: 38 units (115 px at 720p) left
of the screen centre. FFNx shows it the same way.

HOW. The city, the stairs and the walk area share layer 1 with the ocean,
and Cloud is drawn by the camera, so it is the CAMERA that moves:
  * the camera range moves 38 units left (centre -6): city centred, Cloud on
    it, walkmesh untouched;
  * every searchlight-beam record (param 1) moves 38 left with it, so the
    beams stay exactly where they were ON SCREEN; their art then spans the
    new view exactly;
  * the view now reaches x = -219.5, past layer 1's left edge (-192, which
    is itself 12 units of black border and a 2-4 unit bright fringe). That
    strip, x -224..-176, is PAINTED ART supplied with the build:
    `assets/whitein_left_strip.png` (SEVENTH_NX_WI_FILL overrides the path),
    ONLY the strip, 144 x 1536 px (3 px per unit), the user's own painting.
    It holds no pixel of Cosmos's or Square's art: the glass ribbon's tip
    that crosses the strip is black in the file, and its texels stay the
    game's own 1997 cut-out (read from the field at build time) under the
    ribbon's additive overlay. Everything right of x = -176 is the field as
    installed. (618z8; before that the asset was the whole 1440 x 1536
    picture with Cosmos's art in it. Same build output, byte for byte.)
    A full-size picture is still accepted for SEVENTH_NX_WI_FILL.

History: 618z (2nd) continued the beams into the strip ("lights shoot off at
different angles"); 618z (3rd/4th) mirrored the ocean into it, which
mirrored layer 1's own painted light rays and the ribbon's shading ("a
weird curved texture tracing the staircase in the opposing direction ...
the light beams flowing in the opposing direction"). Mirroring cannot
continue a picture, so the strip is painted instead.

WITHOUT THE PAINTED FILE NOTHING CHANGES: whitein keeps Cosmos's framing.
`python3 ff7nx_wishift.py export <dir>` writes the picture to paint, from
the user's own install (whitein_left_fill_ME.png, its mask and a preview);
assets/whitein_paint/prep_fill.py turns a painting of it into the strip. SEVENTH_NX_NO_WI_SHIFT=1
disables.
"""
from __future__ import annotations

import collections
import os
import struct
import sys

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_WI_SHIFT'
FILL_ENV = 'SEVENTH_NX_WI_FILL'
FIELDS = {'whitein': -38}          # camera shift (units), centre 32 -> -6
EDGE = -192
STRIP = (-224, -176)               # painted strip, field x
PX = 3                             # pixels per field unit (= the 768 pages)
IMG_X0, IMG_X1 = -224, 256         # the exported picture's field extent
IMG_Y0, IMG_Y1 = -256, 256
FX_BAND = (15, 26)                 # copies stay off the FX pages (608b gate)
TILE = 52
UV_CELL = 625000
HERE = os.path.dirname(os.path.abspath(__file__))


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def fill_path():
    return os.environ.get(FILL_ENV) or os.path.join(
        HERE, 'assets', 'whitein_left_strip.png')


def _records(s9):
    import diag_common as DC
    pl, ts, _te, px = DC.parse_pages(s9)
    pm = {p.slot: p for p in pl if p is not None}
    rows = []
    for layer, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts):
        rows.append((layer, list(offs)))
    return rows, pm, px


def _usage(s9, rows):
    used = collections.defaultdict(set)
    binds = collections.Counter()
    for _l, offs in rows:
        for o in offs:
            for pg, so in ((s9[o + 32], 10),) + (
                    ((s9[o + 34], 14),) if s9[o + 28] else ()):
                sx, sy = struct.unpack_from('<hh', s9, o + so)
                used[pg].add((sx // 16, sy // 16))
                binds[pg] += 1
    return used, binds


def _rgb16(v):
    v = v.astype(np.uint32)
    return np.stack([((v >> 11) & 31) << 3, ((v >> 6) & 31) << 3,
                     (v & 31) << 3], -1).astype(np.float32)


def _enc16(rgb):
    q = np.clip(np.floor(rgb.astype(np.float32) / 8.0 + 0.5), 0,
                31).astype(np.uint32)
    v = (q[..., 0] << 11 | (q[..., 1] << 1) << 5 | q[..., 2]).astype(
        np.uint16)                                 # green LSB clear (1555)
    v[v == 0] = 0x0841                             # opaque black, not the key
    return v


def _cell_rgb(s9, o, pm, px, pal, so=10, pgoff=32):
    """(k x k x 3 float RGB at PX px/unit, k x k drawn mask) of a record."""
    p = pm.get(s9[o + pgoff])
    if p is None:
        return None
    k = 16 * PX
    sx, sy = struct.unpack_from('<hh', s9, o + so)
    if p.depth == 2:
        kk = px // 16
        a = np.frombuffer(p.data, '<u2').reshape(px, px)[
            sy // 16 * kk:sy // 16 * kk + kk, sx // 16 * kk:sx // 16 * kk + kk]
        idx = np.arange(k) * kk // k
        a = a[idx][:, idx]
        return _rgb16(a), a != 0
    a = np.frombuffer(p.data, np.uint8, count=65536).reshape(256, 256)[
        sy:sy + 16, sx:sx + 16]
    q = s9[o + 22]
    if q >= len(pal):
        return None
    c = pal[q][a]
    rgb = np.stack([c & 31, (c >> 5) & 31, (c >> 10) & 31], -1).astype(
        np.float32) * 8.0
    rgb = np.repeat(np.repeat(rgb, PX, 0), PX, 1)
    m = np.repeat(np.repeat((c & 0x7FFF) != 0, PX, 0), PX, 1)
    return rgb, m


def _overlay(s9, lay, pm, px, pal):
    """{(x, y): (rgb, mask)} of layer 2's STATIC additive overlay (the glass
    ribbon), summed per position."""
    out = {}
    for o in lay.get(2, ()):
        if not s9[o + 28] or s9[o + 26] or s9[o + 30] not in (1, 3):
            continue
        got = _cell_rgb(s9, o, pm, px, pal, so=14, pgoff=34)
        if got is None:
            continue
        xy = struct.unpack_from('<hh', s9, o + 2)
        rgb, m = got
        if s9[o + 30] == 3:
            rgb = rgb * 0.25
        if xy in out:
            out[xy] = (out[xy][0] + rgb, out[xy][1] | m)
        else:
            out[xy] = (rgb, m)
    return out


def picture(parts):
    """The layer-1 picture with layer 2's static art on it (no beams), at PX
    px/unit over IMG_X0..IMG_X1 x IMG_Y0..IMG_Y1; plus the ribbon mask."""
    import ff7nx_marginblack as MB
    s9 = bytes(parts[8])
    rows, pm, px = _records(s9)
    lay = {l: offs for l, offs in rows}
    cols, _h, npg, cpp = MB.palette_colours(parts[3])
    pal = cols.astype(np.int64).reshape(npg, cpp)
    W, H = (IMG_X1 - IMG_X0) * PX, (IMG_Y1 - IMG_Y0) * PX
    img = np.zeros((H, W, 3), np.float32)
    rib = np.zeros((H, W), bool)
    k = 16 * PX

    def put(x, y, rgb, m, mode):
        X, Y = (x - IMG_X0) * PX, (y - IMG_Y0) * PX
        if X < 0 or Y < 0 or X + k > W or Y + k > H:
            return
        reg = img[Y:Y + k, X:X + k]
        if mode == 'set':
            reg[:] = rgb
        elif mode == 'key':
            reg[m] = rgb[m]
        else:
            reg[m] = reg[m] + rgb[m]
            rib[Y:Y + k, X:X + k] |= m
    for o in lay.get(1, ()):
        if s9[o + 26] or s9[o + 28]:
            continue
        got = _cell_rgb(s9, o, pm, px, pal)
        if got:
            put(*struct.unpack_from('<hh', s9, o + 2), got[0], got[1], 'set')
    for o in lay.get(2, ()):
        if s9[o + 26] or s9[o + 28]:
            continue
        got = _cell_rgb(s9, o, pm, px, pal)
        if got:
            put(*struct.unpack_from('<hh', s9, o + 2), got[0], got[1], 'key')
    for (x, y), (rgb, m) in _overlay(s9, lay, pm, px, pal).items():
        put(x, y, rgb, m, 'add')
    return np.clip(img, 0, 255).astype(np.uint8), rib


def export(parts, out_dir):
    """whitein_left_fill_ME.png (strip black, the ribbon's tip kept),
    whitein_left_fill_MASK.png (white = paint here), and a preview."""
    from PIL import Image
    os.makedirs(out_dir, exist_ok=True)
    img, rib = picture(parts)
    X0, X1 = (STRIP[0] - IMG_X0) * PX, (STRIP[1] - IMG_X0) * PX
    me = img.copy()
    paint = np.zeros(rib.shape, bool)
    paint[:, X0:X1] = True
    paint &= ~rib
    me[paint] = 0
    Image.fromarray(me).save(os.path.join(out_dir, 'whitein_left_fill_ME.png'))
    Image.fromarray((paint * 255).astype(np.uint8)).save(
        os.path.join(out_dir, 'whitein_left_fill_MASK.png'))
    Image.fromarray(img).save(os.path.join(out_dir,
                                           'whitein_reference_full.png'))
    return me.shape


def load_fill(path=None):
    from PIL import Image
    path = path or fill_path()
    if not os.path.exists(path):
        return None
    im = Image.open(path).convert('RGB')
    W, H = (IMG_X1 - IMG_X0) * PX, (IMG_Y1 - IMG_Y0) * PX
    SW = (STRIP[1] - STRIP[0]) * PX
    if im.size == (SW, H):                    # the strip alone (618z8)
        full = np.zeros((H, W, 3), np.uint8)
        x0 = (STRIP[0] - IMG_X0) * PX
        full[:, x0:x0 + SW] = np.asarray(im)
        return full
    if im.size != (W, H):
        if abs(im.size[0] / im.size[1] - W / H) > 0.01:
            raise ValueError('painted whitein strip is %dx%d, expected %dx%d '
                             '(or the same aspect)' % (im.size + (W, H)))
        im = im.resize((W, H), Image.LANCZOS)
    return np.asarray(im)


def plan_field(name, parts, fill):
    """(new section 7, new section 9, info) -- raises on any doubt."""
    import field_bg_native as FN
    import ff7nx_marginblack as MB
    if fill is None:
        raise ValueError('no painted strip (%s) -- framing left as Cosmos '
                         'has it' % fill_path())
    shift = FIELDS[name.lower()]
    s7 = bytearray(parts[7])
    left, bottom, right, top = struct.unpack_from('<4h', s7, 0x0C)
    struct.pack_into('<4h', s7, 0x0C, left + shift, bottom, right + shift, top)
    s9 = bytes(parts[8])
    rows, pm, px = _records(s9)
    used, binds = _usage(s9, rows)
    k = px // 16
    cols, _h, npg, cpp = MB.palette_colours(parts[3])
    pal = cols.astype(np.int64).reshape(npg, cpp)
    lay = {l: offs for l, offs in rows}
    if any(struct.unpack_from('<hh', s9, o + 2)[0] < EDGE
           for _l, offs in rows for o in offs):
        raise ValueError('art already reaches left of %d' % EDGE)
    buf = bytearray(s9)
    info = {'beams': 0, 'cells': 0, 'kept': 0}
    # ---- the beams keep their place on screen
    for o in lay.get(2, ()):
        if s9[o + 26] == 1:
            x = struct.unpack_from('<h', s9, o + 2)[0]
            struct.pack_into('<h', buf, o + 2, x + shift)
            info['beams'] += 1
    # ---- the painted strip into layer 1
    l1 = {}
    for o in lay.get(1, ()):
        if s9[o + 26] or s9[o + 28]:
            continue
        l1[struct.unpack_from('<hh', s9, o + 2)] = o
    tpl_any = next((o for o in l1.values() if pm[s9[o + 32]].depth == 2),
                   None)
    if tpl_any is None:
        raise ValueError('no truecolor layer-1 record to copy')
    ov = _overlay(s9, lay, pm, px, pal)
    plan = []
    for y in range(IMG_Y0, IMG_Y1, 16):
        for x in range(STRIP[0], STRIP[1], 16):
            Y, X = (y - IMG_Y0) * PX, (x - IMG_X0) * PX
            cell = fill[Y:Y + 16 * PX, X:X + 16 * PX]
            old = l1.get((x, y))
            if old is None and cell.max() < 6:
                continue                       # black stays the clear colour
            if PX * 16 != k:
                from PIL import Image
                cell = np.asarray(Image.fromarray(cell).resize(
                    (k, k), Image.LANCZOS))
            v = _enc16(cell)
            if (x, y) in ov:                   # the ribbon's tip: keep the
                m = ov[(x, y)][1]              # 1997 cut-out under it
                if m.shape[0] != k:
                    idx = np.arange(k) * m.shape[0] // k
                    m = m[idx][:, idx]
                if old is not None and pm[s9[old + 32]].depth == 2:
                    sx, sy = struct.unpack_from('<hh', s9, old + 10)
                    orig = np.frombuffer(pm[s9[old + 32]].data, '<u2').reshape(
                        px, px)[sy // 16 * k:sy // 16 * k + k,
                                sx // 16 * k:sx // 16 * k + k]
                    v[m] = orig[m]
                else:
                    v[m] = 0x0841
                info['kept'] += int(m.sum())
            tpl = old if old is not None and pm[s9[old + 32]].depth == 2 \
                else l1.get((x + 16, y), l1.get((EDGE, y), tpl_any))
            if pm[s9[tpl + 32]].depth != 2:
                tpl = tpl_any
            plan.append((x, y, tpl, old, v))
    if not plan:
        raise ValueError('the painted strip is empty')
    datas = {}
    need = len(plan)
    slot = None
    for s_ in sorted(pm, key=lambda s: len(used[s])):
        p = pm[s_]
        if p.depth != 2 or p.px != px or FX_BAND[0] <= s_ < FX_BAND[1]:
            continue
        fr = [(cx, cy) for cy in range(16) for cx in range(16)
              if (cx, cy) not in used[s_]]
        if len(fr) >= need and binds[s_] + need <= 256:
            slot = s_
            break
    if slot is None:
        raise ValueError('no truecolor page with %d free cells' % need)
    d = np.frombuffer(pm[slot].data, '<u2').reshape(px, px).copy()
    datas[slot] = d
    new = []
    for i, (x, y, tpl, old, v) in enumerate(plan):
        cx, cy = fr[i]
        d[cy * k:(cy + 1) * k, cx * k:(cx + 1) * k] = v
        r = bytearray(s9[tpl:tpl + TILE])
        struct.pack_into('<hh', r, 2, x, y)
        struct.pack_into('<hh', r, 10, cx * 16, cy * 16)
        struct.pack_into('<II', r, 42, cx * UV_CELL, cy * UV_CELL)
        r[32] = slot
        r[26] = r[27] = r[28] = 0
        if old is not None:
            buf[old:old + TILE] = r
        else:
            new.append(bytes(r))
    info['cells'] = len(plan)
    s9 = bytes(buf)
    import diag_common as DC
    import ff7nx_parallaxfill as PF
    sv = DC.survey(s9)
    buf = bytearray(s9)
    if new:
        for layer, count_at, first, count in PF._layers(
                s9, sv['back_start'], sv['tex_start']):
            if layer != 1:
                continue
            end = first + count * TILE
            buf[end:end] = b''.join(new)
            struct.pack_into('<H', buf, count_at, count + len(new))
    s9n = bytes(buf)
    plist, t0, t1 = FN.parse_texture_block(s9n, px)
    for s_, dd in datas.items():
        q_ = plist[s_]
        plist[s_] = FN.Page(s_, q_.size_flag, 2, dd.tobytes(), q_.px)
    info['camera'] = (left + shift, right + shift)
    return bytes(s7), FN.replace_texture_block(s9n, plist, t0, t1), info


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': []}
    if disabled():
        return st
    encode = encode or archive.encode_field
    try:
        fill = load_fill()
    except Exception as exc:                                   # noqa: BLE001
        st['refused'].append(('whitein', str(exc)[:120]))
        return st
    for name in FIELDS:
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[7], parts[8], info = plan_field(name, parts, fill)
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:120]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['names'].append('%s: camera range %d..%d, %d painted strip '
                           'cell(s), %d beam record(s) kept on screen'
                           % (name, info['camera'][0], info['camera'][1],
                              info['cells'], info['beams']))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  WHITEIN CENTRE (BUILD 618z): the camera moves so the '
                   'city is centred in 16:9; the beams move with it (same '
                   'place on screen); the strip it uncovers on the left is '
                   'the painted art from %s (%s). %s=1 disables.'
                   % (os.path.relpath(fill_path(), HERE),
                      '; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  whitein centre %s: not applied -- %s' % (name, why))
    return '\n'.join(out)


if __name__ == '__main__':
    # python3 ff7nx_wishift.py export <out_dir> [flevel.lgp]
    if len(sys.argv) >= 3 and sys.argv[1] == 'export':
        import lgp
        src = sys.argv[3] if len(sys.argv) > 3 else os.path.join(
            HERE, 'game_data_files', 'field', 'flevel.lgp')
        a = lgp.Archive(src)
        print(export(list(lgp.split_sections(a.decompressed(
            a.index['whitein']))), sys.argv[2]))
    else:
        print(__doc__)
