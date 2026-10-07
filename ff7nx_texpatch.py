#!/usr/bin/env python3
"""ff7nx_texpatch.py -- hand-painted texture edits, applied at build time.

BUILD 617. Some fixes are art, not rules (mds5_1's grate bars want a
wooden top face). This pass lets a painted PNG be the truth for a small
rectangle of one field's background.

A patch folder lives in `texture_edits/<name>/` next to build.py:

    manifest.json   {"field": "mds5_1", "box": [x0, y0, x1, y1]}
                    (field units, multiples of 16)
    base.png        the OPAQUE background in the box, as the console shows it
                    under everything else (layers 1 and 2, static tiles)
    glow.png        the ADDITIVE light drawn over it (static layer-2 fx
                    tiles). Black = no light. On screen: base + glow.
    composite.png   reference only (base + glow); never read back.
    *_paintable.png white where that layer can be painted; reference only.

Both PNGs are at the page's native resolution (3 pixels per field unit at
768). Export writes them from a built field; the build reads them back and,
for every pixel whose 565 value differs from what the field has at that
moment, writes the painted value into the tile that draws that pixel:
the topmost static tile for base.png, the static additive tile for
glow.png. A pixel is written only when exactly one tile draws there and
that tile's cell is read by no other record (so nothing elsewhere in the
field changes); anything else is counted and reported, never guessed.

    python3 ff7nx_texpatch.py export <flevel.lgp> <name> <field> x0 y0 x1 y1

SEVENTH_NX_NO_TEXPATCH=1 disables the build pass.
"""
from __future__ import annotations

import json
import os
import struct
import sys

import numpy as np

import diag_common as DC
import field_bg_native as FN

OFF_ENV = 'SEVENTH_NX_NO_TEXPATCH'
UV_SCALE = 10_000_000
UNIT = 16
HERE = os.path.dirname(os.path.abspath(__file__))
EDIT_DIR = os.path.join(HERE, 'texture_edits')


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _unpack(v):
    v = v.astype(np.int32)
    return np.stack((((v >> 11) & 31) * 255.0 / 31,
                     ((v >> 6) & 31) * 255.0 / 31,
                     (v & 31) * 255.0 / 31), -1)


def _pack(rgb):
    q = np.clip(np.rint(np.asarray(rgb, np.float64) * 31.0 / 255.0),
                0, 31).astype(np.int32)
    return ((q[..., 0] << 11) | ((q[..., 1] << 1) << 5) | q[..., 2]).astype(
        np.uint16)


def _region(sec9, box):
    """Canvases for the box: base/glow 565 values, and per pixel which
    (slot, py, px) writes it (or -1 when not exactly one exclusive tile)."""
    pages_l, tex_start, _te, page_px = DC.parse_pages(sec9)
    pages = {p.slot: p for p in pages_l if p is not None}
    scale = page_px // 256
    step = UNIT * scale
    x0, y0, x1, y1 = box
    W, H = (x1 - x0) * scale, (y1 - y0) * scale

    def cell_of(off, slot):
        page = pages.get(slot)
        if page is None or page.size_flag:
            return None
        u, v = struct.unpack_from('<II', sec9, off + 42)
        return (slot, int(round(u / UV_SCALE * 16)) * step,
                int(round(v / UV_SCALE * 16)) * step)

    rows = [(layer, off) for layer, offs in DC.walk_layers(
        sec9, sec9.find(b'BACK'), tex_start) for off in offs]
    readers = {}
    stat, glow = [], []
    for layer, off in rows:
        use_fx = sec9[off + 28]
        for sl in ((sec9[off + 32], sec9[off + 34]) if use_fx
                   else (sec9[off + 32],)):
            c = cell_of(off, sl)
            if c is not None:
                readers[c] = readers.get(c, 0) + 1
        if sec9[off + 26] or sec9[off + 27] or layer not in (1, 2):
            continue
        x, y = struct.unpack_from('<hh', sec9, off + 2)
        if x + UNIT <= x0 or x >= x1 or y + UNIT <= y0 or y >= y1:
            continue
        slot = sec9[off + 34] if use_fx else sec9[off + 32]
        c = cell_of(off, slot)
        if c is None or pages[slot].depth != 2 or pages[slot].px != page_px:
            continue
        z = struct.unpack_from('<I', sec9, off + 38)[0]
        if use_fx:
            if layer == 2 and sec9[off + 30] == 1:
                glow.append((x, y, c))
        else:
            stat.append((layer, z, x, y, c))

    def draw(recs, opaque):
        val = np.zeros((H, W), np.uint16)
        where = np.full((H, W, 3), -1, np.int32)
        count = np.zeros((H, W), np.int16)
        for x, y, (slot, cx, cy) in recs:
            b = np.frombuffer(pages[slot].data, '<u2').reshape(
                page_px, page_px)[cy:cy + step, cx:cx + step]
            dx, dy = (x - x0) * scale, (y - y0) * scale
            ys0, xs0 = max(dy, 0), max(dx, 0)
            ye, xe = min(dy + step, H), min(dx + step, W)
            if ye <= ys0 or xe <= xs0:
                continue
            sub = b[ys0 - dy:ye - dy, xs0 - dx:xe - dx]
            m = sub != FN.EMPTY if opaque else np.ones(sub.shape, bool)
            gy, gx = np.mgrid[ys0:ye, xs0:xe]
            val[ys0:ye, xs0:xe][m] = sub[m]
            excl = readers.get((slot, cx, cy)) == 1
            w = np.stack([np.full(sub.shape, slot if excl else -1),
                          cy + gy - dy, cx + gx - dx], -1)
            where[ys0:ye, xs0:xe][m] = w[m]
            if not opaque:
                count[ys0:ye, xs0:xe] += 1
        if not opaque:
            where[count != 1] = -1
        return val, where

    stat.sort(key=lambda r: (r[0], -r[1]))      # layer 1, then far to near
    base, bw = draw([(x, y, c) for _l, _z, x, y, c in stat], True)
    lite, gw = draw(glow, False)
    return base, bw, lite, gw, scale


def export(raw, name, field, box, out_dir=EDIT_DIR):
    import lgp
    from PIL import Image
    sec9 = lgp.split_sections(raw)[8]
    base, bw, lite, gw, _s = _region(sec9, box)
    d = os.path.join(out_dir, name)
    os.makedirs(d, exist_ok=True)
    b = _unpack(base)
    g = _unpack(lite) * (lite != FN.EMPTY)[..., None]
    for fn, a in (('base.png', b), ('glow.png', g),
                  ('composite.png', np.minimum(255, b + g))):
        Image.fromarray(np.rint(a).astype(np.uint8)).save(
            os.path.join(d, fn))
    # where each layer can be painted (white) -- elsewhere no tile of that
    # kind draws, or its cell is shared with another part of the field
    for fn, w in (('base_paintable.png', bw), ('glow_paintable.png', gw)):
        Image.fromarray(np.where(w[..., 0] >= 0, 255, 0).astype(np.uint8)
                        ).save(os.path.join(d, fn))
    with open(os.path.join(d, 'manifest.json'), 'w') as fh:
        json.dump({'field': field, 'box': list(box)}, fh, indent=1)
    return d


def plan(sec9, folder):
    """[(slot, py, px, value)] and stats for one patch folder."""
    from PIL import Image
    with open(os.path.join(folder, 'manifest.json')) as fh:
        man = json.load(fh)
    box = tuple(man['box'])
    base, bw, lite, gw, _s = _region(sec9, box)
    st = {'base': 0, 'glow': 0, 'skipped': 0}
    plans = []
    for fn, cur, where, key in (('base.png', base, bw, False),
                                ('glow.png', lite, gw, True)):
        path = os.path.join(folder, fn)
        if not os.path.exists(path):
            continue
        img = np.asarray(Image.open(path).convert('RGB')).astype(np.float64)
        if img.shape[:2] != cur.shape:
            raise ValueError('%s is %dx%d, expected %dx%d' % (
                fn, img.shape[1], img.shape[0], cur.shape[1], cur.shape[0]))
        new = _pack(img)
        if key:
            new[img.max(-1) < 4] = FN.EMPTY
        else:
            new[new == FN.EMPTY] = FN.NEAR_BLACK
        old = cur.copy()
        if key:
            old[_unpack(old).max(-1) < 4] = FN.EMPTY
        diff = new != old
        ok = diff & (where[..., 0] >= 0)
        st['skipped'] += int((diff & ~ok).sum())
        for yy, xx in zip(*np.nonzero(ok)):
            slot, py, px = where[yy, xx]
            plans.append((int(slot), int(py), int(px), int(new[yy, xx])))
        st['glow' if key else 'base'] += int(ok.sum())
    return man['field'], plans, st


def folders():
    if not os.path.isdir(EDIT_DIR):
        return []
    return sorted(os.path.join(EDIT_DIR, d) for d in os.listdir(EDIT_DIR)
                  if os.path.exists(os.path.join(EDIT_DIR, d,
                                                 'manifest.json')))


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    import ff7nx_fxseam as FS
    total = {'patches': [], 'refused': []}
    if disabled():
        return total
    encode = encode or archive.encode_field
    for folder in folders():
        name = os.path.basename(folder)
        try:
            with open(os.path.join(folder, 'manifest.json')) as fh:
                field = json.load(fh)['field']
            entry = archive.index.get(field)
            if entry is None or not archive.is_field(entry):
                raise ValueError('no field %s' % field)
            payload = payloads.get(field)
            raw = (lgp.lzs_decompress(payload[4:]) if payload
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            _f, plans, st = plan(parts[8], folder)
            if plans:
                parts[8] = FS.apply_plans(parts[8], plans)
                payloads[field] = encode(lgp.join_sections(parts))
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append(
                (name, '%s: %s' % (type(exc).__name__, str(exc)[:80])))
            continue
        total['patches'].append('%s (%s): %d base + %d glow pixel(s)%s'
                                % (name, field, st['base'], st['glow'],
                                   ', %d not writable' % st['skipped']
                                   if st['skipped'] else ''))
    return total


def summarise(st):
    out = []
    if st.get('patches'):
        out.append('  TEXTURE EDITS (BUILD 617): %s. %s=1 disables.'
                   % ('; '.join(st['patches']), OFF_ENV))
    if st.get('refused'):
        out.append('  ! texture edits: %s' % ', '.join(
            '%s: %s' % r for r in st['refused']))
    return '\n'.join(out)


if __name__ == '__main__':
    if len(sys.argv) != 9 or sys.argv[1] != 'export':
        sys.exit(__doc__)
    import lgp
    arc = lgp.Archive(sys.argv[2])
    field = sys.argv[4]
    d = export(arc.decompressed(arc.index[field]), sys.argv[3], field,
               tuple(int(v) for v in sys.argv[5:9]))
    print('wrote', d)
