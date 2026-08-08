#!/usr/bin/env python3
"""
diag_rects.py -- HANDOFF-66 §6 step 2, finished: take a capture whose field
and camera position are known, and ask WHICH TILE RECORDS the black
rectangles sit on.

Every pure-black 16x16 destination cell of the capture is mapped back to the
tile records that draw there, and the texture slot / palette / layer of those
records is tabulated against the records under the cells that drew CORRECTLY.
If the rectangles are a page that failed to allocate (HANDOFF-64), the two
tabulations separate by SLOT. If they are §5's palette overshoot, they
separate by PALETTE_ID. If they separate by neither, both leads are dead.
"""
from __future__ import annotations
import os, struct, sys
import numpy as np
from collections import Counter
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path: sys.path.insert(0, _HERE)
import lgp, diag_common as DC, locate_field as LF

TILE, T_DSTX, T_DSTY, T_PAL, T_TEX = 16, 2, 4, 22, 32


def cells_from_capture(path, black=14, frac=0.5):
    from PIL import Image
    img = np.asarray(Image.open(path).convert('RGB'))
    Hc, Wc = img.shape[:2]
    s = Wc / 1280.0
    x, y, w, h = (int(round(v * s)) for v in LF.PIC_1280)
    crop = np.asarray(Image.fromarray(img[y:y+h, x:x+w])
                      .resize((LF.PIC_W, LF.PIC_H), Image.BILINEAR))
    dark = (crop.max(axis=2) <= black)
    g = dark[:224//TILE*TILE, :320//TILE*TILE]
    g = g.reshape(14, TILE, 20, TILE).mean(axis=(1, 3))
    return (g >= frac), crop


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('capture'); ap.add_argument('field')
    ap.add_argument('camx', type=int); ap.add_argument('camy', type=int)
    ap.add_argument('--flevel', default='/sessions/determined-adoring-ride/mnt/uploads/flevel.lgp')
    a = ap.parse_args()

    blackcell, crop = cells_from_capture(a.capture)
    print('capture: %d of %d cells pure black (%.0f%%)'
          % (blackcell.sum(), blackcell.size, 100*blackcell.mean()))

    arc = lgp.Archive(a.flevel)
    raw = arc.decompressed(arc.index[a.field])
    parts = lgp.split_sections(raw)
    sec9 = parts[8]
    surv = DC.survey(sec9)
    pages = {p.slot: p for p in surv['pages']}
    # palette page count
    import ff7nx_marginblack as MB
    hdr, npg, cpp = MB.palette_block(parts[3])
    print('field %s: %d texture page(s) [slots %s], %d palette page(s)'
          % (a.field, len(pages), sorted(pages), npg))

    black_recs, good_recs = [], []
    for layer, offs in DC.walk_layers(sec9, surv['back_start'], surv['tex_start']):
        for o in offs:
            dx = struct.unpack_from('<h', sec9, o+T_DSTX)[0]
            dy = struct.unpack_from('<h', sec9, o+T_DSTY)[0]
            # capture cell this tile lands in
            cx = (dx - a.camx) // TILE
            cy = (dy - a.camy) // TILE
            if not (0 <= cx < 20 and 0 <= cy < 14):
                continue
            rec = (layer, sec9[o+T_TEX], sec9[o+T_PAL],
                   pages[sec9[o+T_TEX]].depth if sec9[o+T_TEX] in pages else -1)
            (black_recs if blackcell[cy, cx] else good_recs).append(rec)

    def tab(name, recs, key, label):
        c = Counter(key(r) for r in recs)
        print('  %-10s %s' % (name, ', '.join('%s=%d' % kv for kv in
                                              sorted(c.items()))))
    print('\ntile records under BLACK cells: %d   under DRAWN cells: %d'
          % (len(black_recs), len(good_recs)))
    for label, key in (('layer', lambda r: r[0]), ('slot', lambda r: r[1]),
                       ('palette', lambda r: r[2]), ('depth', lambda r: r[3])):
        print(' by %s' % label)
        tab('BLACK', black_recs, key, label)
        tab('DRAWN', good_recs, key, label)
    over = [r for r in black_recs if r[2] >= npg]
    print('\nBLACK-cell records whose palette_ID >= %d (section 3 page count): %d'
          % (npg, len(over)))
    over_g = [r for r in good_recs if r[2] >= npg]
    print('DRAWN-cell records whose palette_ID >= %d: %d' % (npg, len(over_g)))

if __name__ == '__main__':
    main()
