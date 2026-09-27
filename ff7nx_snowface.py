"""
BUILD 547 -- Cloud's snowboard eyes: the RIGHT face object, lifted off his
skin. Nothing else about it changes.

WHICH OBJECT. Cloud's face in the snowboard game is object 14 of
`for_gs.tmd` (Gold Saucer) and of `for_ev.tmd` (Icicle Inn): 7 vertices,
5 textured triangles over `eyes.tex` (tpage 5, v 128..175). His head, hair
included, is object 0. `eyes.tex` is the light-blue eye with the orange
lash -- exactly what the screenshots show. (Builds 543-546 worked on object
279 / `tifaeye.tex`, a different face; that was wrong and is dropped.)

WHAT WAS WRONG, measured on a 0.05-unit grid: the decal is FLUSH with the
face. Its 7 vertices ARE 7 of object 0's vertices; depth difference 0.00
everywhere. The PlayStation drew it after the head in its ordering table;
this port depth-tests it, so the two tie -- pieces of each eye vanish, the
eyes look small, high and far apart, and they flicker (vanilla too).

FIX: every vertex moves 2 units toward the camera (z - 2). Measured
clearance from the skin (object 0's non-hair triangles) is then 2.00
everywhere; hair strands still pass in front. Same x/y, same UVs, same
triangles, same texture: the PS1's placement and size, exactly.

Each file is checked against its own stock bytes first; anything else
passes through untouched. Env SEVENTH_NX_SNOW_FACE=0 turns it off.
"""
from __future__ import annotations

import os
import struct

ENV = 'SEVENTH_NX_SNOW_FACE'
OBJECT = 14

STOCK_VERTS = ((-9, -12, -9), (-10, -25, -10), (0, -14, -13), (0, -23, -15),
               (5, -23, -13), (9, -12, -9), (10, -25, -10))
LIFT = 2
NEW_VERTS = tuple((x, y, z - LIFT) for x, y, z in STOCK_VERTS)
N_PRIMS = 5
STOCK_PRIMS = {
    'for_gs.tmd': (284, (
        '27a18078178305000cab0000808080000200040005000000',
        '27a180782886050017830000808080000200030004000000',
        '0c808078268605002ca10000808080000100030002000000',
        '0c8080782ca105000faa0000808080000100020000000000',
        '0cab80781783050009810000808080000500040006000000')),
    'for_ev.tmd': (273, (
        '2da380781882050007aa0000808080000200040005000000',
        '2da380782d84050018820000808080000200030004000000',
        '048080782c8305002ca30000808080000100030002000000',
        '048080782ca3050008ab0000808080000100020000000000',
        '07aa80781882050003800000808080000500040006000000')),
}
TMD_NAMES = tuple(STOCK_PRIMS)


def enabled():
    return os.environ.get(ENV, '1').strip().lower() not in ('0', 'off', 'no',
                                                            'false')


def _layout(data, nobj_want):
    ident, flags, nobj = struct.unpack_from('<III', data, 0)
    if ident != 0x41 or nobj != nobj_want:
        return None
    base = 12
    vt, nv, _nt, _nn, pt, npri, _sc = struct.unpack_from(
        '<IIIIIIi', data, base + 28 * OBJECT)
    if not flags:
        vt += base
        pt += base
    if nv != len(STOCK_VERTS) or npri != N_PRIMS:
        return None
    prims = []
    p = pt
    for _ in range(npri):
        olen, ilen, flg, mode = struct.unpack_from('<BBBB', data, p)
        if mode != 0x25 or ilen != 6:
            return None
        prims.append(p + 4)
        p += 4 + ilen * 4
    return vt, prims


def _read(data, lay):
    vt, prims = lay
    verts = tuple(struct.unpack_from('<hhh', data, vt + 8 * i)
                  for i in range(len(STOCK_VERTS)))
    raws = tuple(data[q:q + 24] for q in prims)
    return verts, raws


def state(name, data):
    """'stock' | 'patched' | 'unknown'"""
    spec = STOCK_PRIMS.get(name.lower())
    if not spec:
        return 'unknown'
    nobj, stock_hex = spec
    try:
        lay = _layout(data, nobj)
        if lay is None:
            return 'unknown'
        verts, raws = _read(data, lay)
    except struct.error:
        return 'unknown'
    stock = tuple(bytes.fromhex(h) for h in stock_hex)
    if verts == STOCK_VERTS and raws == stock:
        return 'stock'
    if verts == NEW_VERTS and raws == stock:
        return 'patched'
    return 'unknown'


def patch(name, data, revert=False):
    """Return (new bytes, note). Unknown input comes back unchanged."""
    st = state(name, data)
    if st == 'unknown':
        return data, '%s: face object not the stock layout -- left alone' % name
    if st == ('stock' if revert else 'patched'):
        return data, '%s: face already %s' % (name, 'stock' if revert
                                              else 'fixed')
    nobj, stock_hex = STOCK_PRIMS[name.lower()]
    vt, prims = _layout(data, nobj)
    out = bytearray(data)
    verts = STOCK_VERTS if revert else NEW_VERTS
    for i, v in enumerate(verts):
        struct.pack_into('<hhh', out, vt + 8 * i, *v)
    return bytes(out), ('%s: face restored to stock' % name if revert else
                        '%s: Cloud\'s eyes lifted 2 units off his face '
                        '(obj 14, placement and UVs stock)' % name)
