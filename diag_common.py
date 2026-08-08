#!/usr/bin/env python3
"""
diag_common.py -- shared section-9 reading for the diag_* tools.

THE PAGE SIZE IS NOT 256
========================
`field_bg_native.parse_texture_block` takes the depth-2 page size as an
argument and defaults to the vanilla 256. A build that shipped 512px or
768px truecolor pages therefore fails to parse with

    slot 27 has depth 10501

which is not a corrupt archive -- it is the walk having desynchronised
because each depth-2 page was 9x bigger than the parser expected, so the
"depth" it read was really pixel data.

`parse_pages()` below tries every size the build can emit and takes the one
that consumes the block exactly. The parser already refuses a wrong size
(it requires the walk to land on the end of the block), so this cannot pick
a wrong answer silently -- at worst it finds none and says so.
"""
from __future__ import annotations

import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, '..')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import field_bg_native as fbn                                   # noqa: E402

SECTION8 = 7                  # zero-based; section 8 is triggers/gateways
SECTION9 = 8
TILE_PX = 16
TILE_DST_X = 2                # a tile record opens with a 2-byte BLANK
TILE_DST_Y = 4

# Everything ff7nx_fieldbg can ship, vanilla first so the common case is
# one attempt.
PAGE_PX_CANDIDATES = (256, 512, 768, 1024)


class ParseError(ValueError):
    pass


def parse_pages(sec9):
    """(pages, tex_start, tex_end, page_px) -- page size detected, not assumed."""
    last = None
    for px in PAGE_PX_CANDIDATES:
        try:
            pages, s, e = fbn.parse_texture_block(sec9, px)
        except Exception as exc:                               # noqa: BLE001
            last = exc
            continue
        return pages, s, e, px
    raise ParseError('no page size in %s parses this section (%s)'
                     % (', '.join(str(p) for p in PAGE_PX_CANDIDATES), last))


def survey(sec9):
    """Like field_bg_native.survey, but with the page size detected."""
    pages, tex_start, tex_end, px = parse_pages(sec9)
    present = [p for p in pages if p is not None]
    return {'pages': present, 'n_pages': len(present),
            'depth1': sum(1 for p in present if p.depth == 1),
            'depth2': sum(1 for p in present if p.depth == 2),
            'tex_start': tex_start, 'tex_end': tex_end, 'page_px': px,
            'back_start': sec9.find(b'BACK')}


def walk_layers(sec9, back_start, tex_start):
    """
    [(layer_number, [byte offsets of its tile records])].

    The same structural walk `field_bg_native._layer_tile_spans` does, but
    keeping the layer boundaries: layer 1 is the background proper, 3 and 4
    are parallax backdrops whose extents mean something different.
    """
    out = []
    o = back_start + 4                       # "BACK"
    _w, _h, n1, _d, _b = struct.unpack_from('<HHHHH', sec9, o)
    o += 10
    out.append((1, [o + i * fbn.TILE_SIZE for i in range(n1)]))
    o += n1 * fbn.TILE_SIZE + 2
    for layer, unused in ((2, 16), (3, 10), (4, 10)):
        if o >= tex_start:
            break
        flag = sec9[o]
        o += 1
        if flag == 0:
            continue
        if flag != 1:
            raise ParseError('layer flag %d at %d' % (flag, o - 1))
        _w, _h, n = struct.unpack_from('<HHH', sec9, o)
        o += 6 + unused + 2
        out.append((layer, [o + i * fbn.TILE_SIZE for i in range(n)]))
        o += n * fbn.TILE_SIZE + 2
    if o != tex_start:
        raise ParseError('layer walk ended at %d, TEXTURE at %d'
                         % (o, tex_start))
    return out


def tile_extents(sec9, surv=None):
    """Per-layer and overall x/y extents in field-space (tile) units."""
    surv = surv or survey(sec9)
    layers = {}
    all_x, all_y = [], []
    for layer, offs in walk_layers(sec9, surv['back_start'],
                                   surv['tex_start']):
        xs = [struct.unpack_from('<h', sec9, o + TILE_DST_X)[0] for o in offs]
        ys = [struct.unpack_from('<h', sec9, o + TILE_DST_Y)[0] for o in offs]
        if not xs:
            continue
        layers[layer] = {'n': len(xs),
                         'x': (min(xs), max(xs) + TILE_PX),
                         'y': (min(ys), max(ys) + TILE_PX)}
        if layer in (1, 2):
            all_x += xs
            all_y += ys
    if not all_x:
        return None
    return {'n': sum(v['n'] for v in layers.values()), 'layers': layers,
            'x': (min(all_x), max(all_x) + TILE_PX),
            'y': (min(all_y), max(all_y) + TILE_PX),
            'pages': surv['n_pages'], 'page_px': surv['page_px']}
