#!/usr/bin/env python3
"""ff7nx_floordepth.py -- floor squares that hide what is below the floor.

BUILD 620h. mds7pb_1 (7th Heaven, the pinball lift), hardware 10-07: "when
going down the elevator ... cloud's dynamic weapon pokes through the floor
at certain points ... his sword goes under some squares, and above other
squares ... not all squares on the floor have the same ordering properties".
The user: Cloud keeps his weapon; the floor squares should take priority.

WHAT THE FIELD IS. Layer 1 (601 squares, the whole floor among them) is
drawn with no depth: ID 4095, IDBig 0, so a model is always painted over it.
Only layer-2 squares are depth-tested (z = IDBig / 1e7, x86 0x62BCB2): the
tables, stools, bar, the hatch art -- and the few squares the 1997 artists
placed around the shaft so that Cloud's BODY disappears as he sinks. The
Ninostyle weapon on his back reaches outside them, over plain floor.

MEASURED IN THIS FIELD. Every layer-2 square's ID is the camera-space depth
of the floor under it / 4 (ratio 3.9..4.1 over 207 squares; objects stand
a little above the floor), and IDBig follows the field's own perspective
curve z = A - B / ID exactly (A 1.01624, B 130.054, max error 3e-6).

WHAT THIS DOES. Each layer-1 square whose four corners all see the floor
plane (z = 0) outside the lift openings becomes a layer-2 square at the
depth of the FARTHEST floor point it covers, plus MARGIN IDs. A point on or
above the floor is always nearer than the floor behind it, so everything
standing on the floor draws as before; a point below the floor (the
sinking weapon) is farther than the floor in front of it and is hidden.
A square is never placed nearer than any existing layer-2 square it
overlaps, so rugs, shadows, tables and the hatch keep drawing on top. Where a
square looks into a lift opening, the surface it shows is the shaft's wall or
bottom (the openings are boxes from the walkmesh), so its depth is the
deepest of those: Cloud inside the shaft is nearer and stays visible, while
the part of his weapon that reaches past the shaft wall, under the floor, is
hidden. Depth is the deepest of 5x5 rays over the square.
Records only move from layer 1 to layer 2 (width/height 16, ID, IDBig set):
the record count, the texture block and every page binding are unchanged.

SEVENTH_NX_NO_FLOOR_DEPTH=1 disables.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import collections
import math
import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_FLOOR_DEPTH'
REC = 52
LIP_BRIGHT_REACH = 6    # texels (2 units) outside the rim outline
UV_CELL = 10_000_000 // 16     # a 16-unit cell in the records' UV pair (+42)
MARGIN = 2                      # IDs (x4 = camera depth units) past the floor
# field -> floor plane z, lift openings (x0, x1, y0, y1 in world units)
# Openings are boxes (x0, x1, y0, y1, bottom z) from the walkmesh: the
# lift footprints (Cloud/Barret's at -127/-255 under x -78..-3, y 148..254;
# the pinball's at -255 under y 288..338), walls straight down.
# 'keep_out': world boxes (x0, x1, y0, y1) on the floor plane. A square
# with ANY ray landing inside one stays a plain layer-1 square (BUILD 620j):
# mds7pb_1's box is the pinball machine's shaft, the raised platform in
# front of it, the floor beside it and the back wall behind it (rays over
# the wall land on the plane past y 338) -- hardware 10-07: "the textures
# beside and under / behind the pinball machine elevator ... affecting the
# back of the machine when it comes back up ... simply everything to the
# right and left and behind where we stand".
FIELDS = {
    'mds7pb_1': {'plane': 0.0,
                 'holes': ((-78, -3, 148, 254, -60),),
                 'keep_out': ((-160, 80, 254, 10000),),
                 # the pinball machine's whole travel (lift script: it sinks
                 # to z -280), padded: no square may hide any point of it
                 'protect': ((-85, 3, 282, 344, -285, 180),),
                 # BUILD 620k. The 1997 layer-2 floor squares in front of
                 # Cloud's lift and around the table (hardware 10-07: "my
                 # character poke[s] through the table region") carry IDs up
                 # to 26 deeper than the floor they show, so the sinking body
                 # drew over them. Inside this screen box (tile x0, x1, y0,
                 # y1) every such square is pulled up to the floor's depth;
                 # the squares sharing a cell keep their order.
                 'relayer': (0, 176, 0, 144),
                 # BUILD 620k5. The machine's floor plate (raised ~4 units)
                 # with the machine UP at (-40, 315) (script below), fitted
                 # to the user's screenshots (scale 3.44). The three lip
                 # squares at tiles (48, 0) (48, 16) (64, 16) (IDs 1370..1372,
                 # the floor's own depth) are nearer than the plate's left
                 # half (its depth rises ~10 IDs from the right front corner
                 # to the left one), so they drew over it: the yellow line
                 # stayed along the left half however the plate was moved
                 # (hardware 10-07..10-08: "partially over, partially
                 # under"). Each square covering a sampled point of the left
                 # 60 % of the front face, or of the first 38 units of the left
                 # face, goes to (deepest such point) + FACE_MARGIN. The
                 # planks at the right corner (tiles (80, *)) stay in front.
                 'plate_faces': (((-77.6, 148.5), (-29.4, 149.2)),
                                 ((-77.6, 148.5), (-78.1, 186.5))),
                 # BUILD 620k5b. The opening's outer rim in screen units
                 # (background tile coordinates, camera-independent),
                 # measured on the user's 10-08 screenshot (camera
                 # (-23.67, 45)) and grown by 0.7: the pushed lip squares
                 # keep only their texels inside it (the opening and its lit
                 # rim, which the plate covers); the wood outside goes to a
                 # copy at the old ID, so Cloud and his sword sinking behind
                 # the front edge stay under the wood (hardware 10-08).
                 'lip_split': ((52.1, 14.0), (88.5, 37.8), (139.3, 0.7),
                               (169.9, -21.6), (83.1, -8.6)),
                 # the floor squares' wood in the same tiles goes forward
                 # only below this screen-unit y -- the opening's left
                 # vertex, so the corner piece the user marked (10-08)
                 # ends in a flat edge level with that corner
                 'lip_extra_below': 14.0,
                 'plate_height': 4.1,
                 # BUILD 620k. Lift script (section 0), exact byte patterns
                 # preceded by their opcode (a5 XYZI, c2 LADER, banks 00 00).
                 'script': (
                     # The machine's UP position. 1997: (-35, 322). Measured on
                     # the user's 10-08 screenshot of the 1997 position and
                     # the outline they drew of where the plate must go (the
                     # opening's own outline in the background): the plate
                     # is one rim band short at the front (~7 px) and left
                     # (~5 px) and ~6 px past the opening on the right (the
                     # 10-07 "peeking above the floor to the right"). The
                     # same plate 5 units left and 6 forward lands on that
                     # outline: (-40, 316); 620k5b (-40, 315), one unit more:
                     # entering the field from outside (a different camera
                     # scroll) left a 1 px rim line at 316. Next to DOWN
                     # (-42, 316): the ride down is nearly straight. 620k..620k4 went to
                     # (-42, 313/311) and turned it: 2 units too far left and
                     # up to 5 too far forward (over the planks).
                     ('a5', 'DDFF420100000000', 'D8FF3B0100000000', 2),
                     ('c2', 'DDFF420100000000', 'D8FF3B0100000000', 1),
                     # Riding up (Cloud s4 / Tifa s15 -- the only two who
                     # ride this lift): vanilla rose slanted from
                     # (-35,182,-255) to (-31,152,0), ending at the opening's
                     # front edge, so the body crossed under the floor in
                     # front of it. 620k: straight up at (-35, 188),
                     # triangle 30.
                     # 620k5c (hardware 10-08: "when tifa comes up and tries
                     # to run off ... she gets stuck and starts running
                     # indefinitely in the same spot"). The machine is solid
                     # when up, with a collision radius of 120 (SLIDR in
                     # Pinball s0) around its position, (-40, 315) since
                     # 620k5: (-35, 188) is 127 from it. A character is
                     # blocked inside the sum of the two radii -- Cloud's is
                     # 0 (SLIDR 0 in Cloud s0), so he walked off; Tifa's is
                     # not, so her MOVE never got out. Now straight up at
                     # (-35, 170) (shaft triangle 1 -> floor triangle 20,
                     # still inside the opening,
                     # body clear of its front edge at y ~148), 145 from the
                     # machine, and the machine's radius 120 -> 100: room
                     # for a character radius up to 45 (the field sets 0,
                     # 30 or 48). Its circle still reaches y 215, the
                     # cabinet's front face is at y ~219. The walk-off MOVE
                     # to (-31, 102) crosses triangles 20 -> 67 (open edge).
                     ('a5', 'DDFFB60001FF0000', 'DDFFAA0001FF0100', 2),
                     ('c2', 'E1FF980000001400', 'DDFFAA0000001400', 2),
                     ('', 'c60078', 'c60064', 1),
                 )},
}
SCRIPT_OFF_ENV = 'SEVENTH_NX_NO_PINLIFT'

# BUILD 766/766b. games_2 (Wonder Square, hardware 10-20): field shadows
# painted over art that stands in front of the floor they lie on. Our shadow
# receiver is the floor's PLANE (FFNx's is the walkmesh triangles), and the
# art is layer 1 (ID 4095: no depth), so a shadow cast on the main floor
# showed through the 1P/2P console (a walkmesh hole), the machine's blue ring
# above it, and the raised pedestal in front (walkmesh z 32 / 65) where Cloud
# stands beside it. Only the console's 2P end was depth-tested (1997 layer-2
# tiles, IDs 248-250).
# Per TEXEL of each layer-1 square inside 'box' (tile x0, x1, y0, y1), its
# ray is classed:
#   floor   -- it sees main-floor walkmesh (|z| <= 3), or a walkmesh hole
#              within EDGE_KEEP of it (a heel at the floor's edge);
#   object  -- it sees raised walkmesh (the pedestal), or a hole further in.
# Object texels must HIDE a shadow cast on the main floor behind them (its
# plane 11 under, nudged HOLE_BIAS nearer by the shadow's depth bias), when a
# main-floor triangle is within RECEIVER_REACH of that point; on raised
# walkmesh they must SHOW feet and shadows standing on it (SOLE_SINK under).
# One depth (ID) per record: the square is refused if none fits. An object-
# only square moves to layer 2 at that depth (766); a mixed one (766b, the
# truecolor build) is split like 620k5b's lip: its object texels go to a
# copy in a free cell of the same page, at that depth in layer 2, and the
# record keeps only its floor texels in layer 1. Pixels are unchanged.
# Refused too: animated squares, a spot another layer-1 tile shares, a cell
# another record uses, and a layer-2 tile deeper than the depth overlapping.
HOLE_OFF_ENV = 'SEVENTH_NX_NO_HOLE_DEPTH'
HOLE_MARGIN = 1                 # IDs nearer than the deepest allowed
HOLE_BIAS = 8.0                 # camera units: the shadow's depth bias, padded
RECEIVER_REACH = 96.0           # a model on the floor shadows this far out:
                                # FFNx's 60-degree light, ~120 tall -> 69, + 20
EDGE_KEEP = 16.0                # a hole this close to the floor stays floor
LEGS = 25.0                     # feet/legs: never newly hidden (766b guard)
MIN_BAND = 2                    # units: the thinnest band of rows (766c)
LEG_CARVE = 3.0                 # units kept clear around a leg point (771)
HOLE_FIELDS = {
    'games_2': {'plane': 0.0, 'box': (0, 160, 80, 240),
                # BUILD 776: see _deep. BUILD 778 (hardware 10-22): x out to
                # 133 for the small rectangle right of it on the pedestal rim
                # (field ~125..133 x 179..182), off the walkmesh -- only
                # points 18+ units past its edge and sunk below the floor
                # are newly hidden.
                'deep': [{'poly': ((117.0, 178.0), (133.0, 177.5),
                                   (133.0, 183.0), (117.0, 183.5)),
                          'bias': 20.0}],
                # BUILD 780 (hardware 10-22): "the floor has a weird
                # triangular cutout on the shadow that should NOT exist ...
                # that little region on the actual floor shouldnt care about
                # our shadows". The plain floor below the pedestal's skirt
                # and right of the rail's right post (field ~124..150 x
                # 186..206) had front copies, so a shadow crossing it was cut
                # along their edge. No copy is made inside 'keep': the floor
                # there takes shadows like the rest of the floor. The post
                # (x <= 145.5 above y 187) and the skirt keep theirs.
                'keep': [((145.5, 176.0), (145.5, 187.0), (136.0, 187.0),
                          (130.0, 192.0), (124.0, 198.0), (118.0, 204.0),
                          (114.0, 210.0), (114.0, 216.0), (164.0, 216.0),
                          (164.0, 176.0))]},
    # BUILD 846 (hardware 10-28): Cloud's shadow peeked over the planks of
    # the right-hand table. The table top hides the floor BEHIND it, which
    # is walkable, so its squares carry that floor's depth (620h) -- and the
    # shadow is drawn ~34 units nearer than its floor (FFNx's offset at
    # IDs ~1350, _ffnx_shift), with hardware nearer still (776). The top's
    # screen outline (tile units, measured on the background: corners
    # left, back, right, front) is a 'solid': its texels get copies that
    # hide the floor shadow behind with `pad` units to spare. A body behind
    # the table goes under it (the leg guard does not apply inside the
    # outline); one in front, or at the sides, is unchanged.
    'mds7pb_1': {'plane': 0.0, 'box': (48, 176, 16, 96),
                 'ffnx_bias': True, 'pad': 28.0,
                 'solid': [((64.0, 40.0), (98.7, 17.0), (161.0, 63.0),
                            (128.0, 85.0))],
                 # BUILD 846b (hardware 10-28): the two copies at tiles
                 # (64, 16) and (80, 16) -- the top's back-left end, under
                 # the pinball plate's yellow lip and light -- drew yellow
                 # lip texels over the planks. That corner is left as it was.
                 'keep': [((48.0, 16.0), (96.0, 16.0), (96.0, 32.0),
                           (48.0, 32.0))]},
}


# BUILD 861b. games (Wonder Square's lobby), hardware 10-29: Cloud's shadow
# showed on the cylinder left of the stairs up to the TV station ("a region
# no one can even walk to ... the simplest solution would just be a shadow
# mask covering this entire portion"). That face is 1997 art with no depth:
# layer-1 squares (ID 4095) and, lower down, layer-2 ones at ID 4081. The
# stair and landing walkmesh (x -36..52) ends at its right edge, and a
# shadow cast from the stairs lands on the stair plane's extension, in front
# of it (BUILD 861 measured the sword's, camera (-42, 89.5)). Each square
# listed here goes to layer 2 at 'id' -- the depth of the cylinder's own
# 1997 squares beside it (384..388) -- so it hides those shadows (they lie
# at IDs 395..421 there, past FFNx's offset and a 28-unit pad) and draws
# exactly as before: nothing else is drawn at those squares but the lamp
# post (ID 4) and its glow (ID 0, blended), both still in front. Bodies:
# none standing anywhere on the walkmesh reaches rows 112; rows 80..96 are
# reached only by someone on the far left of the landing (IDs 430+), who
# stands behind the cylinder -- the 1997 square at (-144, 96), ID 384,
# already hides him the same way.
MASK_OFF_ENV = 'SEVENTH_NX_NO_SHADOW_MASK'
MASK_FIELDS = {
    'games': {'id': 384, 'tiles': ((-176, 80), (-160, 80), (-176, 96),
                                   (-160, 96), (-176, 112), (-160, 112))},
}


def mask_disabled():
    return os.environ.get(MASK_OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def plan_mask(name, sec9):
    """(new section 9, squares masked). Plain layer-1 squares (ID 4095) at
    the listed tiles move to layer 2 at cfg['id']; plain layer-2 ones at ID
    4081 take that ID. Refused if any other plain layer-2 square at those
    tiles is deeper than the new ID (the order inside a cell would change)
    or a layer-1 tile shares the spot. Idempotent."""
    cfg = MASK_FIELDS[name]
    nid, tiles = cfg['id'], set(cfg['tiles'])
    s9 = bytes(sec9)
    A, B = depth_curve(s9)
    big = int(round((A - B / nid) * 1e7))
    rows = {r[0]: r for r in _rows(s9)}
    if 1 not in rows or 2 not in rows:
        raise ValueError('no layer 1 or 2')
    _l, c1, f1, n1 = rows[1]
    _l, c2, f2, n2 = rows[2]
    if f2 <= f1:
        raise ValueError('layer order')
    out = bytearray(s9)
    n = 0
    for i in range(n2):
        o_ = f2 + i * REC
        x, y = struct.unpack_from('<hh', s9, o_ + 2)
        if (x, y) not in tiles:
            continue
        idv = struct.unpack_from('<h', s9, o_ + 24)[0]
        plain = not (s9[o_ + 26] or s9[o_ + 28])
        if plain and idv == 4081:
            struct.pack_into('<h', out, o_ + 24, nid)
            struct.pack_into('<I', out, o_ + 38, big)
            n += 1
        elif plain and nid < idv != 4081:
            raise ValueError('layer-2 square at %s is ID %d' % ((x, y), idv))
    spots = collections.Counter(
        struct.unpack_from('<hh', s9, f1 + i * REC + 2) for i in range(n1))
    move, keep = [], []
    for i in range(n1):
        r = bytearray(out[f1 + i * REC:f1 + (i + 1) * REC])
        xy = struct.unpack_from('<hh', r, 2)
        if (xy in tiles and not (r[26] or r[28])
                and struct.unpack_from('<h', r, 24)[0] == 4095):
            if spots[xy] != 1:
                raise ValueError('layer-1 tiles share %s' % (xy,))
            struct.pack_into('<HH', r, 18, 16, 16)
            struct.pack_into('<h', r, 24, nid)
            struct.pack_into('<I', r, 38, big)
            move.append(bytes(r))
        else:
            keep.append(bytes(r))
    if move:
        mid = bytes(out[f1 + n1 * REC:f2])
        new = (bytes(out[:f1]) + b''.join(keep) + mid
               + bytes(out[f2:f2 + n2 * REC]) + b''.join(move)
               + bytes(out[f2 + n2 * REC:]))
        if len(new) != len(s9):
            raise ValueError('size changed')
        out = bytearray(new)
        struct.pack_into('<H', out, c1, len(keep))
        shift = (n1 - len(keep)) * REC
        struct.pack_into('<H', out, c2 - shift, n2 + len(move))
        rows2 = {r[0]: r for r in _rows(bytes(out))}
        if rows2[1][3] != len(keep) or rows2[2][3] != n2 + len(move):
            raise ValueError('re-walk disagrees')
    return bytes(out), n + len(move)


def _in_poly(poly, x, y):
    """even-odd point-in-polygon, vectorised over x/y arrays"""
    inside = np.zeros(np.shape(x), bool)
    n = len(poly)
    for i in range(n):
        (xa, ya), (xb, yb) = poly[i], poly[(i + 1) % n]
        cross = (ya > y) != (yb > y)
        with np.errstate(divide='ignore', invalid='ignore'):
            xi = xa + (y - ya) * (xb - xa) / (yb - ya)
        inside ^= cross & (x < xi)
    return inside


def _deep(cfg, gx, gy, hi):
    """BUILD 776. `hi` (the depth a main-floor shadow is drawn at) pulled
    `bias` camera units nearer inside each 'deep' polygon (field units).

    Hardware 10-21 (games_2, after 771c): a small dark triangle of the
    standing NPC's (kabe's) floor shadow still showed on the pedestal's
    painted rail shadow, below the blue circle, at field (118..124,
    179..181). The texels there were hidden by copies 2-3 IDs in front of
    the modelled floor shadow (plane z -11, HOLE_BIAS); the art just left
    of it, 9-11 IDs in front, hides the same shadow -- so on hardware that
    shadow lies 3..9 IDs nearer than the model says there. 24 units (6 IDs)
    brings the small polygon to the left part's margin. It is the FLOOR
    shadow that is hidden deeper (not a raised walk level, the 771d zone),
    and every copy still passes the leg guard."""
    for z in cfg.get('deep', ()):
        m = _in_poly(z['poly'], gx, gy)
        if m.any():
            hi = np.where(m & np.isfinite(hi), hi - float(z['bias']), hi)
    # BUILD 780: no floor shadow is hidden inside a 'keep' polygon.
    for poly in cfg.get('keep', ()):
        m = _in_poly(poly, gx, gy)
        if m.any():
            hi = np.where(m, np.inf, hi)
    return hi

def _ffnx_shift(cfg, A, B, hi):
    """BUILD 846. With cfg['ffnx_bias'], `hi` is where the shadow REALLY
    lies: FFNx's field shadow offset (lmain_vv, 658: d' = 1.0075 d - 0.0075
    in window depth) moved through the field's own depth curve
    d = A - 4B / c (c = camera depth), less cfg['pad'] units. HOLE_BIAS (8)
    is that offset at games_2's depths (IDs ~250); at mds7pb_1's (~1350) it
    is ~34 camera units, so an 8-unit copy still let the shadow through."""
    if not cfg.get('ffnx_bias'):
        return hi
    fin = np.isfinite(hi)
    ts = np.where(fin, hi + HOLE_BIAS, 1.0)
    d = A - 4.0 * B / ts
    d2 = 1.0075 * d - 0.0075
    c2 = 4.0 * B / (A - d2)
    return np.where(fin, c2 - float(cfg.get('pad', 4.0)), hi)


def _solid(cfg, R, o, zoom, gx, gy, cls, hi, lo):
    """BUILD 846. Art standing IN FRONT of walkable floor -- mds7pb_1's
    right-hand table top. Its rays pass over the table and land on the floor
    behind it, so the walkmesh test calls those texels 'floor', and the
    table-top squares carry the floor's depth (620h): the shadow of a model
    standing behind the table, drawn ~34 units nearer than that floor (FFNx's
    offset), painted over the planks. Inside each cfg['solid'] screen polygon
    (tile units) every texel is an object hiding the floor shadow behind it
    (plane z -11 along its ray, HOLE_BIAS as elsewhere, then _ffnx_shift)."""
    polys = cfg.get('solid', ())
    if not polys:
        return cls, hi, lo, np.zeros(np.shape(gx), bool)
    ins = np.zeros(np.shape(gx), bool)
    for poly in polys:
        ins |= _in_poly(poly, gx, gy)
    if not ins.any():
        return cls, hi, lo, ins
    Ri = np.linalg.inv(R)
    b = np.einsum('ij,j->i', Ri, -o)
    D = np.einsum('ij,nj->ni', Ri, np.stack(
        [gx.ravel() / zoom, gy.ravel() / zoom, np.ones(gx.size)], 1))
    ts = ((-11.0 - b[2]) / D[:, 2]).reshape(np.shape(gx))
    cls = np.where(ins, 1, cls)
    hi = np.where(ins & (ts > 0), np.minimum(hi, ts - HOLE_BIAS), hi)
    lo = np.where(ins, -np.inf, lo)
    return cls, hi, lo, ins


def _drawn_pad(cfg):
    """how far behind `hi` the shadow is drawn (the fronts test). With
    ffnx_bias, `pad` is a SAFETY margin (hardware draws the shadow nearer
    than the model -- 776: 3..9 IDs), so a 1997 texel whose margin is under
    it is re-copied too: the test is against `hi` itself."""
    return 0.0 if cfg.get('ffnx_bias') else HOLE_BIAS - 3.0


SAMPLES = 5                     # rays per square side


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _camera(sec1):
    c = bytes(sec1)
    R = np.array(struct.unpack_from('<9h', c, 0), float).reshape(3, 3) / 4096.
    o = np.array(struct.unpack_from('<3i', c, 20), float)
    zoom = struct.unpack_from('<H', c, 36)[0]
    return R, o, zoom


def _floor_hit(R, o, zoom, sx, sy, plane):
    """(camera depth, world point) where screen (sx, sy) sees z = plane."""
    Ri = np.linalg.inv(R)
    a = np.einsum('ij,j->i', Ri, np.array([sx / zoom, sy / zoom, 1.0]))
    b = np.einsum('ij,j->i', Ri, -o)
    if abs(a[2]) < 1e-9:
        return None
    t = (plane - b[2]) / a[2]
    if t <= 0:
        return None
    return t, b + t * a


# BUILD 667c. The field places a model 10 units BELOW its walkmesh point
# (x86 0x6392BB: z - 10; FFNx's shadow receiver sits at -11 "just under the
# character's feet"), so the soles are at plane - 10, not on the plane. A
# floor square at the plane's own depth hid the bottom of every boot standing
# on it -- with a field shadow, a dark band at the toes that only this field
# (the only one whose floor has depth) showed. Floor (not shaft) depths are
# now taken SOLE_SINK below the plane: feet draw whole, anything that sinks
# deeper (the weapon on the lift) is still hidden.
SOLE_SINK = 12.0


def visible_depth(R, o, zoom, sx, sy, plane, holes, sink=SOLE_SINK):
    """Camera depth of the first surface the ray through (sx, sy) meets:
    the floor plane, or -- where it crosses the plane inside an opening --
    the opening's walls / bottom. None if the ray never meets the floor."""
    Ri = np.linalg.inv(R)
    a = np.einsum('ij,j->i', Ri, np.array([sx / zoom, sy / zoom, 1.0]))
    b = np.einsum('ij,j->i', Ri, -o)
    if abs(a[2]) < 1e-9:
        return None
    t = (plane - b[2]) / a[2]
    if t <= 0:
        return None
    p = b + t * a
    for x0, x1, y0, y1, zb in holes:
        if x0 <= p[0] <= x1 and y0 <= p[1] <= y1:
            exits = []
            for k, lo, hi in ((0, x0, x1), (1, y0, y1)):
                if abs(a[k]) > 1e-12:
                    for bound in (lo, hi):
                        tk = (bound - b[k]) / a[k]
                        if tk > t:
                            exits.append(tk)
            exits.append((zb - b[2]) / a[2])
            return min(e for e in exits if e > t)
    if sink:
        t2 = (plane - sink - b[2]) / a[2]
        if t2 > t:
            return t2
    return t


def _protected(R, o, zoom, cfg):
    """(screen x, screen y, depth ID) of points sampled through the
    'protect' volumes, or None."""
    pts = []
    for x0, x1, y0, y1, z0, z1 in cfg.get('protect', ()):
        g = np.mgrid[x0:x1 + 1:4, y0:y1 + 1:4, z0:z1 + 1:5]
        pts.append(g.reshape(3, -1).T.astype(float))
    if not pts:
        return None
    P = np.concatenate(pts)
    # BUILD 620k2. Not `R @ P.T`: numpy on macOS (Accelerate BLAS) raises
    # spurious divide-by-zero / overflow / invalid warnings from matmul on
    # finite input (seen in the user's 620k build log). einsum's own loop
    # never calls BLAS; the result is checked finite.
    C = np.einsum('ij,nj->ni', R, P) + o
    if not np.isfinite(C).all() or (C[:, 2] <= 0).any():
        raise ValueError('protected volume behind the camera')
    return np.stack([zoom * C[:, 0] / C[:, 2], zoom * C[:, 1] / C[:, 2],
                     C[:, 2] / 4.0], 1)


def _kept_out(R, o, zoom, x, y, steps, cfg):
    boxes = cfg.get('keep_out', ())
    if not boxes:
        return False
    for dx in steps:
        for dy in steps:
            h = _floor_hit(R, o, zoom, x + dx, y + dy, cfg['plane'])
            if h is None:
                continue
            px, py = h[1][0], h[1][1]
            for x0, x1, y0, y1 in boxes:
                if x0 <= px <= x1 and y0 <= py <= y1:
                    return True
    return False


def _rows(sec9):
    import ff7nx_ripplefade as RF
    return RF._rows(sec9)


def depth_curve(sec9):
    """(A, B) of IDBig/1e7 = A - B / ID over the field's layer-2 squares."""
    ids, zs = [], []
    for layer, _c, first, n in _rows(sec9):
        if layer != 2:
            continue
        for i in range(n):
            o = first + i * REC
            idv = struct.unpack_from('<h', sec9, o + 24)[0]
            big = struct.unpack_from('<I', sec9, o + 38)[0]
            if idv > 16 and big:
                ids.append(idv)
                zs.append(big / 1e7)
    if len(ids) < 20:
        raise ValueError('too few layer-2 squares to fit the depth curve')
    ids = np.array(ids, float)
    zs = np.array(zs)
    M = np.stack([np.ones_like(ids), -1.0 / ids], 1)
    co = np.linalg.lstsq(M, zs, rcond=None)[0]
    err = float(np.abs(np.einsum('ij,j->i', M, co) - zs).max())
    if err > 1e-4:
        raise ValueError('depth curve does not fit (max error %.2g)' % err)
    return float(co[0]), float(co[1])


def _relayer(cfg, R, o, zoom, A, B, s9):
    """(new section 9, cells changed). Layer-2 squares (16x16, ID < 4000)
    inside cfg['relayer'] whose IDs are deeper than the deepest floor point
    they show (+MARGIN) are moved up to it. Records sharing a cell keep
    their relative order and stay behind any nearer record of that cell."""
    box = cfg.get('relayer')
    if not box:
        return s9, 0
    x0, x1, y0, y1 = box
    out = bytearray(s9)
    cells = {}
    for layer, _c, first, n in _rows(s9):
        if layer != 2:
            continue
        for i in range(n):
            o_ = first + i * REC
            x, y = struct.unpack_from('<hh', s9, o_ + 2)
            w = max(struct.unpack_from('<HH', s9, o_ + 18)) or 16
            cells.setdefault((x, y, w), []).append(
                (struct.unpack_from('<h', s9, o_ + 24)[0], o_))
    prot = _protected(R, o, zoom, cfg)
    steps = np.linspace(0, 16, SAMPLES)
    changed = 0
    for (x, y, w), recs in cells.items():
        if w != 16 or x % 16 or y % 16 or not (x0 <= x < x1 and y0 <= y < y1):
            continue
        if any(idv >= 4000 or idv <= 16 for idv, _o in recs):
            continue
        if _kept_out(R, o, zoom, x, y, steps, cfg):
            continue
        ds = [visible_depth(R, o, zoom, x + dx, y + dy, cfg['plane'],
                            cfg['holes']) for dx in steps for dy in steps]
        if any(d is None for d in ds):
            continue
        d = int(math.ceil(max(ds) / 4.0)) + MARGIN
        near = [idv for idv, _o in recs if idv <= d]
        deep = sorted((idv, o_) for idv, o_ in recs if idv > d)
        if not deep:
            continue
        base = max([d] + [v + 1 for v in near])
        new_ids = [base + k for k in range(len(deep))]
        if new_ids[-1] >= deep[-1][0] and new_ids[0] >= deep[0][0]:
            continue
        if prot is not None:
            inside = ((prot[:, 0] >= x - 1) & (prot[:, 0] < x + 17)
                      & (prot[:, 1] >= y - 1) & (prot[:, 1] < y + 17))
            if inside.any() and (prot[inside, 2] > new_ids[0]).any():
                continue
        for (old_id, o_), nid in zip(deep, new_ids):
            nid = min(nid, old_id)
            struct.pack_into('<h', out, o_ + 24, nid)
            struct.pack_into('<I', out, o_ + 38,
                             int(round((A - B / nid) * 1e7)))
        changed += 1
    return bytes(out), changed


PUSHED = []             # (offset, old ID, new ID, old IDBig) of the last _plate_faces
FACE_MARGIN = 4        # IDs behind the plate's faces. Calibrated on the
                       # 620k3 shot: the engine's plate depth is ~4 IDs past
                       # camera z / 4; 3 leaves flecks of the lip while
                       # sinking, larger margins show more of the sinking
                       # plate below the lip (Cloud's ride column: 267
                       # points visible with no rule, 307 at margin 4).


def _plate_faces(cfg, R, o, zoom, A, B, s9):
    """(new section 9, squares pushed back). BUILD 620k5: no layer-2 square
    may draw over the machine's floor-plate side faces (cfg 'plate_faces',
    world segments on the floor, 'plate_height' tall). A square covering a
    sampled face point at depth ID <= that point's ID + FACE_MARGIN - 1 is
    moved to (deepest covered face point) + FACE_MARGIN; squares sharing a
    cell keep their order."""
    PUSHED.clear()
    segs = cfg.get('plate_faces')
    if not segs:
        return s9, 0
    hz = float(cfg.get('plate_height', 4.0))
    pts = []
    for (ax, ay), (bx, by) in segs:
        n = max(2, int(np.hypot(bx - ax, by - ay) / 1.0))
        for t in np.linspace(0, 1, n):
            for z in np.linspace(0.0, hz, 5):
                pts.append((ax + (bx - ax) * t, ay + (by - ay) * t, z))
    P = np.array(pts, float)
    C = np.einsum('ij,nj->ni', R, P) + o
    if not np.isfinite(C).all() or (C[:, 2] <= 0).any():
        raise ValueError('plate face behind the camera')
    sx = zoom * C[:, 0] / C[:, 2]
    sy = zoom * C[:, 1] / C[:, 2]
    fd = C[:, 2] / 4.0
    out = bytearray(s9)
    cells = {}
    for layer, _c, first, n in _rows(s9):
        if layer != 2:
            continue
        for i in range(n):
            o_ = first + i * REC
            if s9[o_ + 28]:
                continue
            x, y = struct.unpack_from('<hh', s9, o_ + 2)
            w = max(struct.unpack_from('<HH', s9, o_ + 18)) or 16
            cells.setdefault((x, y, w), []).append(
                (struct.unpack_from('<h', s9, o_ + 24)[0], o_))
    moved = 0
    PUSHED.clear()
    for (x, y, w), recs in cells.items():
        inside = (sx >= x) & (sx < x + w) & (sy >= y) & (sy < y + w)
        if not inside.any():
            continue
        need = int(math.ceil(fd[inside].max())) + FACE_MARGIN
        recs = sorted(recs)
        if all(idv >= need or idv >= 4000 or idv <= 16 for idv, _o in recs):
            continue
        k = 0
        for idv, o_ in recs:
            if idv >= 4000 or idv <= 16:
                continue
            nid = max(idv, need + k)
            k += 1
            if nid != idv:
                PUSHED.append((o_, idv, nid,
                               struct.unpack_from('<I', out, o_ + 38)[0]))
                struct.pack_into('<h', out, o_ + 24, nid)
                struct.pack_into('<I', out, o_ + 38,
                                 int(round((A - B / nid) * 1e7)))
                moved += 1
    return bytes(out), moved


def _lip_split(cfg, R, o, zoom, A, B, s9):
    """(new section 9, records split). BUILD 620k5b. A lip square pushed
    behind the plate by _plate_faces also carries the floor's wood in
    front of the opening; pushed whole, the wood no longer hid Cloud and
    his sword sinking just behind it (hardware 10-08). Split along the
    opening's outer rim (cfg 'lip_split', a polygon in screen units, i.e.
    background tile coordinates, measured on the user's 10-08 screenshot):
    the record keeps the pushed ID and only its texels inside (the opening
    and its lit rim, which the plate covers); a copy at the old ID, in a free cell of the same
    truecolor page, keeps only the texels outside (the wood). Only active
    (param 0) truecolor records whose cell no other record uses."""
    from scipy import ndimage as ND
    import diag_common as DC
    import field_bg_native as FN
    poly = cfg.get('lip_split')
    if not poly or not PUSHED:
        return s9, 0
    pl, ts, _te, px = DC.parse_pages(s9)
    pm = {p.slot: p for p in pl if p is not None}
    k = px // 16
    if k % 16:
        raise ValueError('pages are %d px' % px)
    K_ = k // 16
    Q = np.array(poly, float)

    area = sum(Q[i, 0] * Q[(i + 1) % len(Q), 1] - Q[(i + 1) % len(Q), 0]
               * Q[i, 1] for i in range(len(Q)))
    sgn = 1.0 if area > 0 else -1.0

    def inside(px_, py_):
        res = np.ones(px_.shape, bool)
        for i in range(len(Q)):
            ax, ay = Q[i]
            bx, by = Q[(i + 1) % len(Q)]
            res &= ((bx - ax) * (py_ - ay) - (by - ay) * (px_ - ax)) * sgn >= 0
        return res
    users = collections.defaultdict(set)
    for lay, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts):
        for o_ in offs:
            n_ = struct.unpack_from('<H', s9, o_ + 18)[0] or 16
            for po, so in ((32, 10), (34, 14)):
                if po == 34 and not s9[o_ + 28]:
                    continue
                sx, sy = struct.unpack_from('<hh', s9, o_ + so)
                for cy in range(sy // 16, (sy + n_ - 1) // 16 + 1):
                    for cx in range(sx // 16, (sx + n_ - 1) // 16 + 1):
                        users[(s9[o_ + po], cx, cy)].add(o_)
            # and the cell its UV pair names (what the port samples)
            u, v = struct.unpack_from('<II', s9, o_ + 42)
            pg_ = s9[o_ + 34] if s9[o_ + 28] and s9[o_ + 34] else s9[o_ + 32]
            users[(pg_, int(round(u / UV_CELL)), int(round(v / UV_CELL)))].add(o_)
    plist, t0, t1 = FN.parse_texture_block(s9, px)
    datas = {}
    adds = []
    jobs = [(o_, old, big0) for o_, old, _n, big0 in PUSHED]
    # 620k5b (user, 10-08: "this green region should also include the ...
    # little corner piece"): the floor's own squares in the same tiles
    # (moved to layer 2 at the floor depth by plan(), ID ~1385) carry wood
    # outside the rim too; that wood goes to a copy at the lip's old ID
    tiles = {}
    for o_, old, _n, big0 in PUSHED:
        r = s9[o_:o_ + REC]
        if not r[26] and not r[28]:
            tiles[struct.unpack_from('<hh', r, 2)] = (old, big0)
    done = {j[0] for j in jobs}
    for lay, _c, first, n in _rows(s9):
        if lay != 2:
            continue
        for i in range(n):
            o_ = first + i * REC
            r = s9[o_:o_ + REC]
            xy = struct.unpack_from('<hh', r, 2)
            if (o_ in done or xy not in tiles or r[26] or r[28]
                    or not tiles[xy][0] < struct.unpack_from('<h', r, 24)[0]
                    < 4000):
                continue
            jobs.append((o_,) + tiles[xy])
    extra = {j[0] for j in jobs} - done

    def tile_texels(xy, pgd):
        """[(ID, offset, opaque k x k)] of the layer-2 truecolor
        records drawn at tile xy (blended ones are not occluders)."""
        res = []
        for lay, _c, first, n in _rows(s9):
            if lay != 2:
                continue
            for i in range(n):
                q_ = first + i * REC
                rr = s9[q_:q_ + REC]
                # (param records: mds7pb_1's param 4 is never switched on by
                # its script -- no BGON 4 -- so they never occlude)
                if struct.unpack_from('<hh', rr, 2) != xy or rr[28] or rr[26]:
                    continue
                if pm.get(rr[32]) is None or pm[rr[32]].depth != 2:
                    continue
                if rr[32] not in pgd:
                    pgd[rr[32]] = np.frombuffer(plist[rr[32]].data,
                                                '<u2').reshape(px, px)
                ux, vy = struct.unpack_from('<hh', rr, 10)
                b_ = pgd[rr[32]][vy // 16 * k:vy // 16 * k + k,
                                 ux // 16 * k:ux // 16 * k + k]
                res.append((struct.unpack_from('<h', rr, 24)[0], q_,
                            b_ != 0))
        return res
    orig_pages = {}
    for o_, old, big0 in jobs:
        r = s9[o_:o_ + REC]
        if r[26] or r[28] or old <= 16:
            continue
        pg = r[32]
        if pm[pg].depth != 2 or struct.unpack_from('<H', r, 18)[0] not in (0, 16):
            continue
        sx, sy = struct.unpack_from('<hh', r, 10)
        u, v = struct.unpack_from('<II', r, 42)
        if (sx % 16 or sy % 16 or (u, v) != (sx // 16 * UV_CELL,
                                             sy // 16 * UV_CELL)
                or len(users[(pg, sx // 16, sy // 16)]) != 1):
            continue
        if pg not in datas:
            datas[pg] = np.frombuffer(plist[pg].data, '<u2').reshape(px, px).copy()
        d = datas[pg]
        # a cell no record draws from (its old texels are leftovers)
        free = [(cx, cy) for cy in range(16) for cx in range(16)
                if not users[(pg, cx, cy)]]
        if not free:
            continue
        x, y = struct.unpack_from('<hh', r, 2)
        t = (np.arange(k) + 0.5) / K_
        gx, gy = np.meshgrid(x + t, y + t)
        hole = inside(gx, gy)
        blk = d[sy // 16 * k:sy // 16 * k + k, sx // 16 * k:sx // 16 * k + k]
        # the rim's bright outer highlight just outside the outline stays
        # with the hole (behind the plate): only the plain wood goes forward
        v_ = blk.astype(np.int64)
        rr_, bb_ = ((v_ >> 11) & 31) << 3, (v_ & 31) << 3
        bright = (blk != 0) & (rr_ > 95) & (rr_ - bb_ > 55)
        near = ND.binary_dilation(hole, iterations=LIP_BRIGHT_REACH)
        hole = hole | (bright & near)
        if not (blk[hole] != 0).any() or not (blk[~hole] != 0).any():
            continue
        if o_ in extra:
            # only texels this record actually shows (nothing nearer is
            # opaque there): moved forward, they change no pixel
            me = struct.unpack_from('<h', r, 24)[0]
            top = np.ones((k, k), bool)
            for idv, q_, op in tile_texels((x, y), orig_pages):
                # nearer, or as near and drawn later (record order)
                if q_ != o_ and (idv < me or (idv == me and q_ > o_)):
                    top &= ~op
            # only the corner piece in front of the opening's left corner
            # (below its left vertex); wood further back stays at the
            # floor depth, or it would cover what stands on the floor
            hole = hole | ~top | (gy < cfg.get('lip_extra_below', 1e9))
            if not (blk[~hole] != 0).any():
                continue
        cx, cy = free[0]
        wood = np.where(hole, 0, blk)
        d[cy * k:(cy + 1) * k, cx * k:(cx + 1) * k] = wood
        blk[~hole] = 0
        users[(pg, cx, cy)].add(-1)
        c = bytearray(r)
        struct.pack_into('<hh', c, 10, cx * 16, cy * 16)
        # the port draws from the UV pair (+42), not from +10
        struct.pack_into('<II', c, 42, cx * UV_CELL, cy * UV_CELL)
        struct.pack_into('<h', c, 24, old)
        struct.pack_into('<I', c, 38, big0)
        adds.append(bytes(c))
    if not adds:
        return s9, 0
    rows = {r_[0]: r_ for r_ in _rows(s9)}
    _l, c2, f2, n2 = rows[2]
    buf = bytearray(s9[:f2 + n2 * REC]) + b''.join(adds) + s9[f2 + n2 * REC:]
    struct.pack_into('<H', buf, c2, n2 + len(adds))
    plist, t0, t1 = FN.parse_texture_block(bytes(buf), px)
    for pg, d in datas.items():
        q = plist[pg]
        plist[pg] = FN.Page(pg, q.size_flag, 2, d.tobytes(), q.px)
    out = FN.replace_texture_block(bytes(buf), plist, t0, t1)
    if {r_[0]: r_ for r_ in _rows(out)}[2][3] != n2 + len(adds):
        raise ValueError('re-walk disagrees after the lip split')
    return out, len(adds)


def plan_script(name, sec0):
    """(new section 0, replacements made) -- raises unless every pattern is
    found exactly as often as expected."""
    pats = FIELDS[name].get('script', ())
    s0 = bytearray(sec0)
    total = 0
    for op, old, new, count in pats:
        pre = (op + '0000') if op else ''      # op '' = raw bytes
        ob = bytes.fromhex(pre + old)
        nb = bytes.fromhex(pre + new)
        if len(ob) != len(nb):
            raise ValueError('pattern length')
        hits = []
        i = s0.find(ob)
        while i >= 0:
            hits.append(i)
            i = s0.find(ob, i + 1)
        if len(hits) != count:
            if bytes(s0).count(nb) == count:
                continue                       # already applied
            raise ValueError('%s %s found %d times, expected %d'
                             % (op, old, len(hits), count))
        for i in hits:
            s0[i:i + len(nb)] = nb
        total += len(hits)
    return bytes(s0), total


def plan(name, sec1, sec9):
    """(new section 9, stats) -- raises on any doubt."""
    cfg = FIELDS[name]
    s9 = bytes(sec9)
    R, o, zoom = _camera(sec1)
    A, B = depth_curve(s9)
    s9, relayered = _relayer(cfg, R, o, zoom, A, B, s9)
    rows = {r[0]: r for r in _rows(s9)}
    if 1 not in rows or 2 not in rows:
        raise ValueError('no layer 1 or 2')
    _l, c1, f1, n1 = rows[1]
    _l, c2, f2, n2 = rows[2]
    if f2 <= f1:
        raise ValueError('layer order')
    # existing layer-2 squares: screen box -> nearest-allowed ID floor
    l2 = []
    for i in range(n2):
        r = s9[f2 + i * REC:f2 + (i + 1) * REC]
        x, y = struct.unpack_from('<hh', r, 2)
        w = max(struct.unpack_from('<HH', r, 18)) or 16
        l2.append((x, y, w, struct.unpack_from('<h', r, 24)[0]))
    prot = _protected(R, o, zoom, cfg)
    move, keep = [], []
    for i in range(n1):
        r = bytearray(s9[f1 + i * REC:f1 + (i + 1) * REC])
        x, y = struct.unpack_from('<hh', r, 2)
        if r[26] or r[28] or struct.unpack_from('<h', r, 24)[0] != 4095:
            keep.append(bytes(r))
            continue
        steps = np.linspace(0, 16, SAMPLES)
        if _kept_out(R, o, zoom, x, y, steps, cfg):
            keep.append(bytes(r))
            continue
        ds = [visible_depth(R, o, zoom, x + dx, y + dy, cfg['plane'],
                            cfg['holes']) for dx in steps for dy in steps]
        if any(d is None for d in ds):
            keep.append(bytes(r))
            continue
        idv = int(math.ceil(max(ds) / 4.0)) + MARGIN
        for (lx, ly, lw, lid) in l2:
            if lx < x + 16 and x < lx + lw and ly < y + 16 and y < ly + lw \
                    and lid > 16:
                idv = max(idv, lid + 1)
        if not 16 < idv < 4095:
            keep.append(bytes(r))
            continue
        if prot is not None:
            inside = ((prot[:, 0] >= x - 1) & (prot[:, 0] < x + 17)
                      & (prot[:, 1] >= y - 1) & (prot[:, 1] < y + 17))
            if inside.any() and (prot[inside, 2] > idv).any():
                keep.append(bytes(r))
                continue
        big = int(round((A - B / idv) * 1e7))
        struct.pack_into('<HH', r, 18, 16, 16)
        struct.pack_into('<h', r, 24, idv)
        struct.pack_into('<I', r, 38, big)
        move.append(bytes(r))
    if not move:
        raise ValueError('no floor square qualifies')
    out = bytearray(s9)
    l1 = b''.join(keep)
    l2b = s9[f2:f2 + n2 * REC] + b''.join(move)
    # layer 1 block, its trailer, the layer-2 header up to its records
    mid = s9[f1 + n1 * REC:f2]
    new = (s9[:f1] + l1 + mid + l2b + s9[f2 + n2 * REC:])
    if len(new) != len(s9):
        raise ValueError('size changed')
    out = bytearray(new)
    struct.pack_into('<H', out, c1, len(keep))
    shift = (n1 - len(keep)) * REC
    struct.pack_into('<H', out, c2 - shift, n2 + len(move))
    out = bytes(out)
    rows2 = {r[0]: r for r in _rows(out)}
    if rows2[1][3] != len(keep) or rows2[2][3] != n2 + len(move):
        raise ValueError('re-walk disagrees')
    out, faces = _plate_faces(cfg, R, o, zoom, A, B, out)
    out, split = _lip_split(cfg, R, o, zoom, A, B, out)
    return out, {'moved': len(move), 'kept': len(keep), 'curve': (A, B),
                 'relayered': relayered, 'plate_faces': faces,
                 'lip_split': split}


def _walk_tris(sec5):
    """walkmesh triangles (n, 3, 3) from the walkmesh section"""
    n = struct.unpack_from('<I', sec5, 0)[0]
    out = np.zeros((n, 3, 3))
    for i in range(n):
        for j in range(3):
            out[i, j] = struct.unpack_from('<3h', sec5, 4 + 24 * i + 8 * j)
    return out


def _walk_depth(R, o, zoom, sx, sy, tris):
    """camera depth of the nearest walkmesh triangle the ray through screen
    (sx, sy) meets, or None (the walkmesh has a hole there)"""
    Ri = np.linalg.inv(R)
    d = np.einsum('ij,j->i', Ri, np.array([sx / zoom, sy / zoom, 1.0]))
    b = np.einsum('ij,j->i', Ri, -o)
    v0 = tris[:, 0]
    e1 = tris[:, 1] - v0
    e2 = tris[:, 2] - v0
    p = np.cross(d, e2)
    det = np.einsum('ij,ij->i', e1, p)
    ok = np.abs(det) > 1e-9
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    tv = b - v0
    u = np.einsum('ij,ij->i', tv, p) * inv
    q = np.cross(tv, e1)
    w = np.einsum('ij,j->i', q, d) * inv
    t = np.einsum('ij,ij->i', q, e2) * inv
    hit = ok & (u >= -1e-3) & (w >= -1e-3) & (u + w <= 1.001) & (t > 0)
    return float(t[hit].min()) if hit.any() else None


def _seg_d(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    t = np.clip(((px - ax) * dx + (py - ay) * dy) / np.where(ll > 0, ll, 1), 0, 1)
    return np.hypot(ax + t * dx - px, ay + t * dy - py)


def _floor_dist(tris, x, y):
    """distance in (x, y) from points to the nearest triangle of `tris`"""
    best = np.full(x.shape, np.inf)
    for v in tris:
        (ax, ay), (bx, by), (cx, cy) = v[0, :2], v[1, :2], v[2, :2]
        d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if d:
            u = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / d
            w = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / d
            inside = (u >= 0) & (w >= 0) & (u + w <= 1)
        else:
            inside = np.zeros(x.shape, bool)
        e = np.minimum(np.minimum(_seg_d(x, y, ax, ay, bx, by),
                                  _seg_d(x, y, bx, by, cx, cy)),
                       _seg_d(x, y, cx, cy, ax, ay))
        best = np.minimum(best, np.where(inside, 0.0, e))
    return best


def _texels(R, o, zoom, tris, gx, gy):
    """per screen point: (class 0 floor / 1 object / 2 raised, the depth
    a main-floor shadow there must be behind (inf: none), the depth feet on
    raised walkmesh need in front (-inf: none))"""
    Ri = np.linalg.inv(R)
    b = np.einsum('ij,j->i', Ri, -o)
    D = np.einsum('ij,nj->ni', Ri, np.stack(
        [gx.ravel() / zoom, gy.ravel() / zoom, np.ones(gx.size)], 1))
    v0 = tris[:, 0]
    e1 = tris[:, 1] - v0
    e2 = tris[:, 2] - v0
    first = np.full(len(D), np.inf)
    fz = np.zeros(len(D))
    for i in range(len(tris)):
        p = np.cross(D, e2[i])
        # (einsum, not @: see 620k2 -- macOS BLAS warns on finite input)
        det = np.einsum('nj,j->n', p, e1[i])
        ok = np.abs(det) > 1e-9
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        tv = b - v0[i]
        u = np.einsum('nj,j->n', p, tv) * inv
        q = np.cross(tv, e1[i])
        w = np.einsum('nj,j->n', D, q) * inv
        t = np.einsum('j,j->', q, e2[i]) * inv
        hit = ok & (u >= -1e-3) & (w >= -1e-3) & (u + w <= 1.001) & (t > 0) & (t < first)
        first = np.where(hit, t, first)
        fz = np.where(hit, tris[i, :, 2].mean(), fz)
    main = tris[np.abs(tris[:, :, 2]).max(1) <= 3]
    # the main floor's plane (z 0) and its shadow plane (z -11)
    t0 = (0.0 - b[2]) / D[:, 2]
    ts = (-11.0 - b[2]) / D[:, 2]
    X = b + t0[:, None] * D
    dist = _floor_dist(main, X[:, 0], X[:, 1])
    cls = np.zeros(len(D), int)
    hole = ~np.isfinite(first)
    cls[hole & (dist >= EDGE_KEEP)] = 1
    raised = np.isfinite(first) & (fz >= 16)
    cls[raised] = 2
    hi = np.where((cls > 0) & (dist <= RECEIVER_REACH) & (t0 > 0)
                  & (ts > np.where(raised, first, 0) + 8.0), ts - HOLE_BIAS, np.inf)
    tl = (fz - SOLE_SINK - b[2]) / D[:, 2]
    lo = np.where(raised, tl, -np.inf)
    sh = gx.shape
    return cls.reshape(sh), hi.reshape(sh), lo.reshape(sh)


def _texels_cons(R, o, zoom, tris, gx, gy, half):
    """_texels, with `hi` the NEAREST shadow depth anywhere in the texel
    (centre and four corners, +-half units), not only at its centre.
    BUILD 771c: a copy solved from texel centres left a sub-texel sliver
    of the floor shadow showing along one band edge (the user's red line,
    games_2 tile 128,176) -- the shadow's depth changes across a texel."""
    cls, hi, lo = _texels(R, o, zoom, tris, gx, gy)
    for dx, dy in ((-half, -half), (half, -half), (-half, half), (half, half)):
        _c, h2, _l = _texels(R, o, zoom, tris, gx + dx, gy + dy)
        hi = np.where(np.isfinite(hi), np.minimum(hi, h2), hi)
    return cls, hi, lo


def _legs(R, o, zoom, tris):
    """(screen x, y, camera depth) of feet and leg points of a body standing
    anywhere on the walkmesh: 91 spots per triangle (a 1/12 barycentric
    grid), the centre and rings of radius 6 and 12, 5 heights up to LEGS --
    what the 766b guard must never newly hide"""
    w = np.array([(a / 12, b / 12, (12 - a - b) / 12) for a in range(13)
                  for b in range(13 - a)])
    feet = np.einsum('sk,tkj->tsj', w, tris).reshape(-1, 3)
    ang = np.linspace(0, 2 * np.pi, 16, endpoint=False)
    ring = [(0.0, 0.0)] + [(rr * np.cos(a_), rr * np.sin(a_))
                           for rr in (6.0, 12.0) for a_ in ang]
    off = np.array([(dx, dy, h - 10.0) for h in np.linspace(3, LEGS, 5)
                    for dx, dy in ring])
    P = (feet[:, None, :] + off[None]).reshape(-1, 3)
    C = np.einsum('ij,nj->ni', R, P) + o
    C = C[C[:, 2] > 1]
    return zoom * C[:, 0] / C[:, 2], zoom * C[:, 1] / C[:, 2], C[:, 2]


def _dilate(m, r):
    """binary dilation by a (2r+1) square, no wrap"""
    out = m.copy()
    n0, n1 = m.shape
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if not dy and not dx:
                continue
            src = m[max(0, -dy):n0 - max(0, dy), max(0, -dx):n1 - max(0, dx)]
            out[max(0, dy):n0 - max(0, -dy), max(0, dx):n1 - max(0, -dx)] |= src
    return out


def plan_holes(name, sec1, sec5, sec9):
    """BUILD 766/766b/766c: (new section 9, records changed) -- see
    HOLE_FIELDS. Run to a fixed point (a band refused on one pass can fit
    once its neighbours have moved), so planning a planned section is a
    no-op."""
    total = 0
    for _ in range(6):
        sec9, n = _plan_holes_once(name, sec1, sec5, sec9)
        total += n
        if not n:
            return sec9, total
    raise ValueError('hole depth does not settle')


def _plan_holes_once(name, sec1, sec5, sec9):
    import diag_common as DC
    import field_bg_native as FN
    cfg = HOLE_FIELDS[name]
    s9 = bytes(sec9)
    R, o, zoom = _camera(sec1)
    A, B = depth_curve(s9)
    tris = _walk_tris(sec5)
    rows = {r[0]: r for r in _rows(s9)}
    if 1 not in rows or 2 not in rows:
        raise ValueError('no layer 1 or 2')
    _l, c1, f1, n1 = rows[1]
    _l, c2, f2, n2 = rows[2]
    if f2 <= f1:
        raise ValueError('layer order')
    l2 = []
    for i in range(n2):
        r = s9[f2 + i * REC:f2 + (i + 1) * REC]
        x, y = struct.unpack_from('<hh', r, 2)
        w = max(struct.unpack_from('<HH', r, 18)) or 16
        l2.append((x, y, w, struct.unpack_from('<h', r, 24)[0]))
    spots = collections.Counter(
        struct.unpack_from('<hh', s9, f1 + i * REC + 2) for i in range(n1))
    l2_spots = [(struct.unpack_from('<hh', s9, f2 + j * REC + 2),
                 s9[f2 + j * REC:f2 + (j + 1) * REC]) for j in range(n2)]
    pl, ts_, _te, px = DC.parse_pages(s9)
    pm = {p.slot: p for p in pl if p is not None}
    k = px // 16 if px % 16 == 0 else 0
    K_ = k / 16.0
    users = collections.defaultdict(set)          # (page, cell x, cell y)
    for lay, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts_):
        for o_ in offs:
            nn = struct.unpack_from('<H', s9, o_ + 18)[0] or 16
            for po, so in ((32, 10), (34, 14)):
                if po == 34 and not s9[o_ + 28]:
                    continue
                sx, sy = struct.unpack_from('<hh', s9, o_ + so)
                for cy in range(sy // 16, (sy + nn - 1) // 16 + 1):
                    for cx in range(sx // 16, (sx + nn - 1) // 16 + 1):
                        users[(s9[o_ + po], cx, cy)].add(o_)
            u, v = struct.unpack_from('<II', s9, o_ + 42)
            pg_ = s9[o_ + 34] if s9[o_ + 28] and s9[o_ + 34] else s9[o_ + 32]
            users[(pg_, int(round(u / UV_CELL)), int(round(v / UV_CELL)))].add(o_)
    plist = t0 = t1 = None
    datas = {}
    x0, x1, y0, y1 = cfg['box']
    lsx, lsy, ldep = _legs(R, o, zoom, tris)
    solid = {}                  # BUILD 846: (x, y) -> texels inside cfg['solid']

    def l2_opaque(xy, n, blended=False):
        """[(ID, n x n opaque)] of the truecolor layer-2 records at xy: the
        plain ones, or (blended=True) the blended / animated ones (766c)"""
        nonlocal plist, t0, t1
        res = []
        if not k or n != k or not tc:
            return res
        for j in range(n2):
            q_ = f2 + j * REC
            rr = s9[q_:q_ + REC]
            if struct.unpack_from('<hh', rr, 2) != xy:
                continue
            if bool(rr[26] or rr[28]) != blended:
                continue
            pg_ = rr[34] if rr[28] and rr[34] else rr[32]
            if pm.get(pg_) is None or pm[pg_].depth != 2:
                if blended:                        # unknown art: all of it
                    res.append((struct.unpack_from('<h', rr, 24)[0],
                                np.ones((n, n), bool)))
                continue
            if plist is None:
                plist, t0, t1 = FN.parse_texture_block(s9, px)
            uu, vv = struct.unpack_from('<II', rr, 42)
            cx_, cy_ = int(round(uu / UV_CELL)), int(round(vv / UV_CELL))
            pgd = np.frombuffer(plist[pg_].data, '<u2').reshape(px, px)
            res.append((struct.unpack_from('<h', rr, 24)[0],
                        pgd[cy_ * k:(cy_ + 1) * k, cx_ * k:(cx_ + 1) * k] != 0))
        return res

    def legs_at(x, y, n, idv):
        """n x n: texels where a foot/leg point lies behind idv and no
        1997 layer-2 texel already hides it"""
        out = np.zeros((n, n), bool)
        sel = (lsx >= x) & (lsx < x + 16) & (lsy >= y) & (lsy < y + 16)
        if not sel.any():
            return out
        tx = np.minimum(n - 1, ((lsx[sel] - x) * n / 16).astype(int))
        ty = np.minimum(n - 1, ((lsy[sel] - y) * n / 16).astype(int))
        dd = ldep[sel] / 4.0
        hid = dd > idv
        for lid, m in l2_opaque((x, y), n):
            hid &= ~(m[ty, tx] & (dd > lid))        # already hidden (1997)
        out[ty[hid], tx[hid]] = True
        # BUILD 846: a body behind a 'solid' (the table) is MEANT to go under
        # it -- the leg guard does not apply to those texels
        sm = solid.get((x, y))
        if sm is not None and sm.shape == out.shape:
            out &= ~sm
        return out

    def legs_hidden(x, y, n, mask, idv):
        """a foot/leg point newly hidden by `mask` (n x n) at idv?"""
        return bool((mask & legs_at(x, y, n, idv)).any())
    move, keep, adds = [], [], []
    for i in range(n1):
        r = bytearray(s9[f1 + i * REC:f1 + (i + 1) * REC])
        x, y = struct.unpack_from('<hh', r, 2)
        if (not (x0 <= x < x1 and y0 <= y < y1) or r[26] or r[28]
                or struct.unpack_from('<h', r, 24)[0] != 4095
                or spots[(x, y)] != 1):
            keep.append(bytes(r))
            continue
        tc = k and pm.get(r[32]) is not None and pm[r[32]].depth == 2
        n = k if tc else 16
        t = (np.arange(n) + 0.5) * 16.0 / n
        gx, gy = np.meshgrid(x + t, y + t)
        cls, hi, lo = _texels_cons(R, o, zoom, tris, gx, gy, 8.0 / n)
        cls, hi, lo, solid[(x, y)] = _solid(cfg, R, o, zoom, gx, gy, cls, hi, lo)
        hi = _ffnx_shift(cfg, A, B, _deep(cfg, gx, gy, hi))
        obj = cls > 0
        need = obj & np.isfinite(hi)       # texels a floor shadow lies behind
        if not need.any():
            keep.append(bytes(r))
            continue
        pg = r[32]
        sx, sy = struct.unpack_from('<hh', r, 10)
        blk = None
        if tc and not (sx % 16 or sy % 16):
            if plist is None:
                plist, t0, t1 = FN.parse_texture_block(s9, px)
            if pg not in datas:
                datas[pg] = np.frombuffer(plist[pg].data, '<u2').reshape(px, px).copy()
            blk = datas[pg][sy // 16 * k:sy // 16 * k + k, sx // 16 * k:sx // 16 * k + k]
        l2m = l2_opaque((x, y), n) if blk is not None else []
        # 766c: a blended or animated 1997 overlay is drawn OVER the layer-1
        # art; art moved to layer 2 could land on top of it. Texels under
        # one stay in layer 1, whatever the depths.
        over = np.zeros((n, n), bool)
        for _lid, m in (l2_opaque((x, y), n, True) if blk is not None else []):
            over |= m
        if blk is None and any(
                (lx_, ly_) == (x, y) and (rr_[26] or rr_[28])
                for (lx_, ly_), rr_ in l2_spots):
            over[:] = True
        need = need & ~over
        if not need.any():
            keep.append(bytes(r))
            continue

        def solve(mask, carve=True):
            """(texels, ID): the ID that hides every floor shadow behind the
            texels of `mask` it keeps and spares what stands on raised
            walkmesh there, or None"""
            for _ in range(4):
                h = hi[mask & need]
                if not h.size:
                    return None
                idv = int(math.floor(h.min() / 4.0)) - HOLE_MARGIN
                if blk is None:
                    break
                # 766c: a 1997 layer-2 texel deeper than the new one would
                # be covered by it -- leave the texels under it to that tile
                # (the layer-1 art there never shows; the tile occludes)
                under = np.zeros_like(mask)
                for lid, m in l2m:
                    if lid >= idv:
                        under |= m
                if not (under & mask).any():
                    break
                mask = mask & ~under
            else:
                return None
            lm = lo[mask]
            lm = lm[np.isfinite(lm)]
            if not 16 < idv < 4095 or (lm.size and idv <= lm.max() / 4.0 + 1):
                return None
            if blk is None and any(
                    lx < x + 16 and x < lx + lw and ly < y + 16 and y < ly + lw
                    and lid > 16 and lid >= idv for (lx, ly, lw, lid) in l2):
                return None
            if legs_hidden(x, y, n, mask, idv):
                if carve and blk is not None:
                    # BUILD 771: keep the texels clear of every leg point
                    # (dilated by LEG_CARVE units) instead of refusing the
                    # band -- the same guard, applied per texel
                    cut = _dilate(legs_at(x, y, n, idv),
                                  max(1, int(round(LEG_CARVE * n / 16))))
                    rest = mask & ~cut
                    if rest.any() and rest.sum() < mask.sum():
                        return solve(rest, False)
                return None                        # 766b: never cut a leg
            return mask, idv

        if blk is None:                            # paletted: whole squares
            got = solve(obj) if obj.all() else None
            if got is None:
                keep.append(bytes(r))
                continue
            idv = got[1]
            struct.pack_into('<HH', r, 18, 16, 16)
            struct.pack_into('<h', r, 24, idv)
            struct.pack_into('<I', r, 38, int(round((A - B / idv) * 1e7)))
            move.append(bytes(r))
            continue
        art = blk != 0
        cand = need & art
        if not cand.any():
            keep.append(bytes(r))
            continue
        got = solve(cand)
        if got is not None and not (art & ~got[0]).any():
            # 766: the whole square takes the depth
            idv = got[1]
            struct.pack_into('<HH', r, 18, 16, 16)
            struct.pack_into('<h', r, 24, idv)
            struct.pack_into('<I', r, 38, int(round((A - B / idv) * 1e7)))
            move.append(bytes(r))
            continue
        # 766b/766c: the texels that take a depth go to copies, each a band
        # of rows with its own ID. One 16-unit square spans ~10 IDs of floor
        # depth top to bottom, more than the window between hiding a floor
        # shadow and keeping a foot on a raised step (z 32: ~2); a band of
        # MIN_BAND units spans ~1. Bands are halved until each fits.
        pieces = []
        if got is not None:
            pieces.append(got)
        else:
            minrows = max(1, n * MIN_BAND // 16)

            def bands(m):
                rows_ = np.where(m.any(1))[0]
                if not rows_.size:
                    return
                r0, r1 = rows_[0], rows_[-1] + 1
                mm = np.zeros_like(m)
                mm[r0:r1] = m[r0:r1]
                v_ = solve(mm)
                if v_ is not None:
                    pieces.append(v_)
                elif r1 - r0 > minrows:
                    h_ = (r0 + r1) // 2
                    top = mm.copy()
                    top[h_:] = False
                    bot = mm.copy()
                    bot[:h_] = False
                    bands(top)
                    bands(bot)
            bands(cand)
        u, v = struct.unpack_from('<II', r, 42)
        if (not pieces or (u, v) != (sx // 16 * UV_CELL, sy // 16 * UV_CELL)
                or len(users[(pg, sx // 16, sy // 16)]) != 1):
            keep.append(bytes(r))
            continue
        for m, idv in pieces:
            # a cell no record draws from: on this page, else another
            # truecolor one (the build packs pages densely; the copy names
            # its own page)
            free = next(((q, cx, cy) for q in [pg] + sorted(
                p_.slot for p_ in pm.values() if p_.depth == 2 and p_.slot != pg)
                for cy in range(16) for cx in range(16)
                if not users[(q, cx, cy)]), None)
            if free is None:
                break
            q, cx, cy = free
            users[(q, cx, cy)].add(-1)
            if q not in datas:
                datas[q] = np.frombuffer(plist[q].data, '<u2').reshape(px, px).copy()
            datas[q][cy * k:(cy + 1) * k, cx * k:(cx + 1) * k] = np.where(m, blk, 0)
            blk[m] = 0
            c = bytearray(r)
            c[32] = q
            struct.pack_into('<hh', c, 10, cx * 16, cy * 16)
            struct.pack_into('<II', c, 42, cx * UV_CELL, cy * UV_CELL)
            struct.pack_into('<HH', c, 18, 16, 16)
            struct.pack_into('<h', c, 24, idv)
            struct.pack_into('<I', c, 38, int(round((A - B / idv) * 1e7)))
            adds.append(bytes(c))
        keep.append(bytes(r))
    # BUILD 771: 1997 layer-2 tiles BEHIND the shadow. Hardware 10-21: Cloud
    # beside the rail's right post cast a hard wedge on the pedestal's rim
    # (tile 128,176), where the art is not layer 1 at all but 1997 layer-2
    # tiles (IDs 232-246) -- and the shadow there lies NEARER than those
    # IDs. The layer-1 pass above leaves such texels alone ("the tile
    # occludes"); it does not. For each in-box square of plain layer-2
    # records: per texel, the front-most opaque record's ID and pixel; where
    # that ID does not hide the floor shadow, a copy of exactly the visible
    # pixel goes to a free cell at the depth that does -- the same bands,
    # raised-feet rule and leg guard (771 carve) as above. The 1997 records
    # are untouched, so the picture is unchanged.
    if k and cfg.get('fronts', True):
        tc = True                   # l2_opaque reads it: these pages are truecolor
        if plist is None:
            plist, t0, t1 = FN.parse_texture_block(s9, px)

        def cell_of(pg_, cx_, cy_):
            src_ = datas[pg_] if pg_ in datas else np.frombuffer(
                plist[pg_].data, '<u2').reshape(px, px)
            return src_[cy_ * k:(cy_ + 1) * k, cx_ * k:(cx_ + 1) * k]
        fr = collections.defaultdict(list)
        for j in range(n2):
            rr = s9[f2 + j * REC:f2 + (j + 1) * REC]
            xy = struct.unpack_from('<hh', rr, 2)
            if not (x0 <= xy[0] < x1 and y0 <= xy[1] < y1):
                continue
            fr[xy].append(rr)
        for (x, y), lst in sorted(fr.items()):
            if x % 16 or y % 16:
                continue
            plain = [rr for rr in lst if not (rr[26] or rr[28])]
            if not plain or any(
                    (max(struct.unpack_from('<HH', rr, 18)) or 16) != 16
                    or pm.get(rr[32]) is None or pm[rr[32]].depth != 2
                    for rr in plain):
                continue
            n = k
            t = (np.arange(n) + 0.5) * 16.0 / n
            gx, gy = np.meshgrid(x + t, y + t)
            cls, hi, lo = _texels_cons(R, o, zoom, tris, gx, gy, 8.0 / n)
            cls, hi, lo, solid[(x, y)] = _solid(cfg, R, o, zoom, gx, gy, cls,
                                                hi, lo)
            hi = _ffnx_shift(cfg, A, B, _deep(cfg, gx, gy, hi))
            need = (cls > 0) & np.isfinite(hi)
            if not need.any():
                continue
            fid = np.full((n, n), np.inf)
            pix = np.zeros((n, n), np.uint16)
            amb = np.zeros((n, n), bool)
            front = {}
            for rr in plain:
                lid = struct.unpack_from('<h', rr, 24)[0]
                if lid <= 16:
                    continue
                uu, vv = struct.unpack_from('<II', rr, 42)
                cpx = cell_of(rr[32], int(round(uu / UV_CELL)),
                              int(round(vv / UV_CELL)))
                op = cpx != 0
                tie = op & (fid == lid) & (pix != cpx)
                amb |= tie
                nearer = op & (lid < fid)
                fid[nearer] = lid
                pix[nearer] = cpx[nearer]
                front[lid] = rr
            over = np.zeros((n, n), bool)
            for _lid, m in l2_opaque((x, y), n, True):
                over |= m
            # the shadow is drawn at (hi + pad): hidden if fid < that / 4
            expo = (need & np.isfinite(fid) & ~over & ~amb
                    & (fid * 4.0 >= hi + _drawn_pad(cfg)))
            if not expo.any():
                continue

            def fsolve(mask, carve=True):
                h = hi[mask]
                if not h.size:
                    return None
                idv = int(math.floor(h.min() / 4.0)) - HOLE_MARGIN
                lm = lo[mask]
                lm = lm[np.isfinite(lm)]
                if not 16 < idv < 4095 or (lm.size and idv <= lm.max() / 4.0 + 1):
                    return None
                if (fid[mask] <= idv).any():
                    return None
                if legs_hidden(x, y, n, mask, idv):
                    if carve:
                        cut = _dilate(legs_at(x, y, n, idv),
                                      max(1, int(round(LEG_CARVE * n / 16))))
                        rest = mask & ~cut
                        if rest.any() and rest.sum() < mask.sum():
                            return fsolve(rest, False)
                    return None
                return mask, idv
            pieces = []
            minrows = max(1, n * MIN_BAND // 16)

            def fbands(m):
                rows_ = np.where(m.any(1))[0]
                if not rows_.size:
                    return
                r0, r1 = rows_[0], rows_[-1] + 1
                mm = np.zeros_like(m)
                mm[r0:r1] = m[r0:r1]
                v_ = fsolve(mm)
                if v_ is not None:
                    pieces.append(v_)
                elif r1 - r0 > minrows:
                    h_ = (r0 + r1) // 2
                    top = mm.copy()
                    top[h_:] = False
                    bot = mm.copy()
                    bot[:h_] = False
                    fbands(top)
                    fbands(bot)
            fbands(expo)
            tmpl = front[min(front)]
            for m, idv in pieces:
                free = next(((q, cx, cy) for q in sorted(
                    p_.slot for p_ in pm.values() if p_.depth == 2)
                    for cy in range(16) for cx in range(16)
                    if not users[(q, cx, cy)]), None)
                if free is None:
                    break
                q, cx, cy = free
                users[(q, cx, cy)].add(-1)
                if q not in datas:
                    datas[q] = np.frombuffer(plist[q].data, '<u2').reshape(px, px).copy()
                datas[q][cy * k:(cy + 1) * k, cx * k:(cx + 1) * k] = np.where(m, pix, 0)
                c = bytearray(tmpl)
                c[32] = q
                struct.pack_into('<hh', c, 10, cx * 16, cy * 16)
                struct.pack_into('<II', c, 42, cx * UV_CELL, cy * UV_CELL)
                struct.pack_into('<HH', c, 18, 16, 16)
                struct.pack_into('<h', c, 24, idv)
                struct.pack_into('<I', c, 38, int(round((A - B / idv) * 1e7)))
                adds.append(bytes(c))
    if not move and not adds:
        return s9, 0                               # nothing (left) to do
    mid = s9[f1 + n1 * REC:f2]
    new = (s9[:f1] + b''.join(keep) + mid + s9[f2:f2 + n2 * REC]
           + b''.join(move) + b''.join(adds) + s9[f2 + n2 * REC:])
    if len(new) != len(s9) + len(adds) * REC:
        raise ValueError('size changed')
    out = bytearray(new)
    struct.pack_into('<H', out, c1, len(keep))
    struct.pack_into('<H', out, c2 - len(move) * REC, n2 + len(move) + len(adds))
    out = bytes(out)
    if datas:
        plist, t0, t1 = FN.parse_texture_block(out, px)
        for pg, dd in datas.items():
            q = plist[pg]
            plist[pg] = FN.Page(pg, q.size_flag, 2, dd.tobytes(), q.px)
        out = FN.replace_texture_block(out, plist, t0, t1)
    rows2 = {r_[0]: r_ for r_ in _rows(out)}
    if rows2[1][3] != len(keep) or rows2[2][3] != n2 + len(move) + len(adds):
        raise ValueError('re-walk disagrees')
    return out, len(move) + len(adds)


def hole_disabled():
    return os.environ.get(HOLE_OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def script_disabled():
    return os.environ.get(SCRIPT_OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': [], 'script': [], 'holes': []}
    if (disabled() and script_disabled() and hole_disabled()
            and mask_disabled()):
        return st
    encode = encode or archive.encode_field
    for name in FIELDS:
        if name not in archive.index:
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(archive.index[name]))
            parts = list(lgp.split_sections(raw))
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:90]))
            continue
        changed = False
        if not disabled():
            try:
                parts[8], info = plan(name, parts[1], parts[8])
                changed = True
                st['names'].append(
                    '%s (%d floor squares depth-tested, %d squares pulled '
                    'up to the floor)' % (name, info['moved'],
                                          info['relayered']))
            except Exception as exc:                           # noqa: BLE001
                st['refused'].append((name, str(exc)[:90]))
        if not script_disabled() and FIELDS[name].get('script'):
            try:
                parts[0], n = plan_script(name, parts[0])
                changed = True
                st['script'].append('%s (%d words)' % (name, n))
            except Exception as exc:                           # noqa: BLE001
                st['refused'].append((name + ' script', str(exc)[:90]))
        if changed:
            payloads[name] = encode(lgp.join_sections(parts))
    # BUILD 846: the holes pass runs AFTER the 620h floor pass, so a field in
    # both (mds7pb_1) solves its copies against the depths that ship
    for name in HOLE_FIELDS:                                   # BUILD 766
        if hole_disabled() or name not in archive.index:
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(archive.index[name]))
            parts = list(lgp.split_sections(raw))
            parts[8], n = plan_holes(name, parts[1], parts[4], parts[8])
            if n:
                payloads[name] = encode(lgp.join_sections(parts))
            st['holes'].append('%s (%s)' % (name, '%d squares' % n if n
                                            else 'already'))
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name + ' holes', str(exc)[:90]))
    for name in MASK_FIELDS:                                   # BUILD 861b
        if mask_disabled() or name not in archive.index:
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(archive.index[name]))
            parts = list(lgp.split_sections(raw))
            parts[8], n = plan_mask(name, parts[8])
            if n:
                payloads[name] = encode(lgp.join_sections(parts))
            st.setdefault('masks', []).append(
                '%s (%s)' % (name, '%d squares' % n if n else 'already'))
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name + ' mask', str(exc)[:90]))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  FLOOR DEPTH (BUILD 620h): floor squares now hide what '
                   'is below the floor: %s. %s=1 disables.'
                   % (', '.join(st['names']), OFF_ENV))
    if st.get('script'):
        out.append('  PINBALL LIFT (BUILD 620k): the machine stands centred on '
                   'its opening and the ride up is straight, close to the '
                   'cabinet: %s. %s=1 disables.'
                   % (', '.join(st['script']), SCRIPT_OFF_ENV))
    if st.get('holes'):
        out.append('  HOLE DEPTH (BUILD 766b): art standing in front of the '
                   'floor (walkmesh holes, raised walkways) hides the field '
                   'shadows behind it: %s. %s=1 disables.'
                   % (', '.join(st['holes']), HOLE_OFF_ENV))
    if st.get('masks'):
        out.append('  SHADOW MASK (BUILD 861b): art nobody can stand behind '
                   'hides the field shadows behind it: %s. %s=1 disables.'
                   % (', '.join(st['masks']), MASK_OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! floor depth %s: unchanged -- %s' % (name, why))
    return '\n'.join(out)
