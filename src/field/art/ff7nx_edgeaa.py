#!/usr/bin/env python3
"""ff7nx_edgeaa.py -- jagged painted edges inside opaque art redrawn as
smooth, anti-aliased curves. BUILD 896.

HARDWARE 11-07, mds6_1: the wooden stakes laced across the crack in the
bottom slab (layer 2) have "dark jagged edges ... we want it cleaned up ...
antialiased if need be". Then: "we are not removing the shadow like dark
parts on these plank layers, just the jagged edges need to be smooth."

WHY. The stakes, their dark outlines and their shadows are Cosmos's art
(an upscale of the 1997 page). The 1997 outline is one pixel, so in the
upscale every dark/light boundary along a stake steps in whole 1997 pixels;
the 1024 px page then goes to the 768 px page (3 texels a unit). Against the
bright road marking each step reads as a black notch.

WHAT, inside each box of TARGETS (field units), on the layer's static
truecolor records laid out as one canvas (ff7nx_edgesmooth._canvas):
  * stray sharp black marks are removed first (hardware 11-07 on the first
    preview: "like sharp jagged black parts shouldnt be there ... but the
    point was to not just simply remove every shaded region"): a dark mark
    at most ~4 texels wide hugging a stake, NOT connected to any broad dark
    area, is repainted from its surroundings (clean_dirt). Shadows and the
    crack are broad and are never touched;
  * dark / light is decided LOCALLY: a texel is dark when it is below the
    midpoint of the darkest and brightest texel within WIN texels, and only
    where that window holds at least CONTRAST of difference -- so stake vs
    road, outline vs marking and shadow vs stake each split at their own
    level;
  * only near the stakes: within ZONE texels of warm, light texels (wood);
  * the dark mask is blurred (SIGMA) and re-cut through a narrow ramp
    (RAMP): the staircase becomes a curve with an anti-aliased edge of
    ~1.5 texels. Outlines at most 2 texels thick are zigzags of single
    texels -- the notches seen against the marking -- and are redrawn as a
    soft line of about the same weight (LINE_SIGMA, LINE_GAIN);
  * an edge texel becomes ramp x dark colour + (1 - ramp) x light colour,
    where its own side keeps its own colour and the other side's is the
    local average of that side. So every shadow and outline stays where it
    is, as dark as it was; only its outline is smoothed.
Only texels on an edge change. A record whose page cell another record also
uses is left alone. Applied once per build, on the final payload.

SEVENTH_NX_NO_EDGE_AA=1 disables.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import collections
import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_EDGE_AA'
VERSION = 'edgeaa-896'
K = 3
WIN = 7
CONTRAST = 40.0
SIGMA = 2.0
RAMP = 1.8
ZONE = 6
DIRT_HAT = 16.0         # stray marks: 3x3 black-hat depth (0..255)
BLACK = 75.0            # a black mark is never lighter than this ...
BLACK_RATIO = 0.6       # ... and is under this x the brightest texel within 3
BLACK_REACH = 3.0       # texels from a stake
BRIGHT_LUM = 80.0       # bright art a black outline can hug (within ZONE of a stake)
CRACK_LEN = 18          # a longer dark line mostly away from the stakes is a crack
BROAD_LUM = 80.0        # a broad dark area (shadow, crack) is below this
STAKE_WARM = 36.0       # stakes: R - B (road / slab 8..32, stakes 40..56)
STAKE_LUM = 65.0
PINCH_LIGHT = 85.0      # pinch: both sides at least this light
PINCH_RATIO = 0.82      # pinch: seam under this x the lighter-side level
PINCH_DROP = 20.0       # pinch: and at least this much darker
SPECK_RATIO = 0.78      # speck: under this x its surroundings (5x5 median, then its ring)
SPECK_LUM = 80.0        # speck: and under this luminance
SPECK_WOOD_WARM = 24.0  # speck: reach also measured from wood this warm (shaded stakes)
RIM_DARK = 0.72         # an outline texel on the object's border: under this x both sides
VALLEY_DARK = 0.6       # an outline texel is under this x its lit neighbours
SPECK_MAX = 8           # stray black specks: at most this many texels
STAKE_HALF = 7.0        # half-width (texels) above which light art is not a stake
LINE_SIGMA = 1.1       # thin outlines: blur ...
LINE_GAIN = 2.8        # ... and weight (a straight line keeps its darkness at the centre)
WOOD_WARM = 22.0        # R - B of the stakes
WOOD_LUM = 100.0
# field -> {'layer': n, 'boxes': [(x0, y0, x1, y1) field units]}
TARGETS = {
    # the two runs of stakes across the bottom slab's crack
    'mds6_1': {'layer': 2, 'boxes': [(-200, 224, -32, 280),
                                     (-296, 200, -196, 256)]},
}


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def stakes(C, valid):
    """The stakes: warm (R - B >= STAKE_WARM), not dark (lum >= STAKE_LUM --
    the ones in shade are ~80), and NARROW (the road marking is as warm and
    light but wide). By width, not connection: where a stake touches the
    marking the two merge in the mask."""
    from scipy import ndimage as ND
    lum = C @ np.array([0.3, 0.59, 0.11], np.float32)
    warm = valid & (C[..., 0] - C[..., 2] >= STAKE_WARM) & (lum >= STAKE_LUM)
    dt = ND.distance_transform_edt(warm)
    return warm & (ND.grey_dilation(dt, size=(9, 9)) <= STAKE_HALF)


def black_mask(C, valid, stake=None):
    """Black marks beside a stake: below BLACK and under BLACK_RATIO x the
    brightest texel within 3 (measured: the outline and the flecks are
    18..50 against 66..100), within BLACK_REACH of a stake. A grey edge --
    the slab's rim against the road, 65 against 85 -- is not black and is
    left exactly as it is (hardware 11-07: "you arent filtering the dark
    black pixels from the grey pixels")."""
    from scipy import ndimage as ND
    lum = C @ np.array([0.3, 0.59, 0.11], np.float32)
    if stake is None:
        stake = stakes(C, valid)
    # near anything bright: a stake, the knob between them (light grey, not
    # warm), the marking -- a thin black line hugging bright art is the 1997
    # outline whatever the art is
    ds = ND.distance_transform_edt(~stake)
    bright = valid & (((lum >= BRIGHT_LUM) & (ds <= ZONE)) | stake)
    near = ND.distance_transform_edt(~bright) <= BLACK_REACH
    around = ND.grey_closing(lum, size=(7, 7))
    return valid & ~bright & near & (lum < BLACK) & \
        (lum < BLACK_RATIO * around)


def clean_dirt(C, valid, inbox, hat=DIRT_HAT):
    """(new RGB, cleaned mask): the stray sharp black marks beside the
    stakes -- black (black_mask), thin (a 3x3 black-hat deeper than `hat`: 2 texels),
    and NOT part of a broad dark area (a shadow, the crack: dark 5x5 blocks
    under a 15x15 closing). Repainted from what surrounds them, never from
    the stake itself."""
    from scipy import ndimage as ND
    lum = C @ np.array([0.3, 0.59, 0.11], np.float32)
    stake = stakes(C, valid)
    blk = black_mask(C, valid, stake)
    # thin = at most 2 texels across (a 3x3 closing fills it): the 1997
    # one-pixel outline. Anything wider is shading and is only smoothed
    bh = ND.grey_closing(lum, size=(3, 3)) - lum
    T = blk & (bh > hat)
    # a shadow or the crack is really dark -- not the marking's dimmer part
    broad = valid & ~stake & (lum < BROAD_LUM) & \
        (lum < BLACK_RATIO * ND.grey_closing(lum, size=(15, 15)))
    broad = ND.binary_opening(broad, structure=np.ones((5, 5)))
    lab, n = ND.label(T, structure=np.ones((3, 3)))
    if not n:
        return C.copy(), np.zeros_like(T)
    idx = np.arange(1, n + 1)
    tb = ND.maximum(ND.binary_dilation(broad, iterations=1), lab, idx)
    # a long dark line that mostly runs away from the stakes is a painted
    # crack in the slab, not an outline
    ds = ND.distance_transform_edt(~stake)
    far = ND.mean((ds > BLACK_REACH).astype(np.float32), lab, idx)
    ext = np.array([max(sl[0].stop - sl[0].start, sl[1].stop - sl[1].start)
                    for sl in ND.find_objects(lab)])
    crack = (ext > CRACK_LEN) & (far > 0.5)
    dirt = np.isin(lab, idx[(tb == 0) & ~crack]) & inbox

    def g(x):
        return ND.gaussian_filter(x, 1.5, mode='nearest')
    lit = valid & ((lum >= BRIGHT_LUM) | stake)
    src = (valid & ~blk & ~lit).astype(np.float32)
    gs = g(src)
    fill = np.stack([g(C[..., i] * src) for i in range(3)], -1) / \
        np.maximum(gs, 1e-4)[..., None]
    src2 = (valid & ~blk).astype(np.float32)
    gs2 = g(src2)
    fill2 = np.stack([g(C[..., i] * src2) for i in range(3)], -1) / \
        np.maximum(gs2, 1e-4)[..., None]
    fill = np.where((gs > 0.08)[..., None], fill, fill2)
    out = C.copy()
    out[dirt] = fill[dirt]
    return out, dirt


def smooth_edges(C, valid, zone, sigma=SIGMA, ramp=RAMP, contrast=CONTRAST,
                 win=WIN):
    """(new RGB, changed mask) for an RGB float canvas."""
    from scipy import ndimage as ND
    lum = C @ np.array([0.3, 0.59, 0.11], np.float32)
    lo = ND.grey_erosion(lum, size=(win, win))
    hi = ND.grey_dilation(lum, size=(win, win))
    edge = valid & zone & ((hi - lo) >= contrast)
    dark = lum < (hi + lo) / 2
    d = dark.astype(np.float32)
    thick = ND.binary_opening(dark, structure=np.ones((3, 3)))
    thin = dark & ~thick
    c = ND.gaussian_filter(thick.astype(np.float32), sigma, mode='nearest')
    a = np.clip(0.5 + ramp * (c - 0.5), 0, 1)
    # a 1-2 texel outline IS the staircase (a zigzag of single texels):
    # redrawn as a soft line of about the same weight
    ct = ND.gaussian_filter(thin.astype(np.float32), LINE_SIGMA,
                            mode='nearest')
    a = np.maximum(a, np.clip(LINE_GAIN * ct, 0, 1))

    def g(x):
        return ND.gaussian_filter(x, 2.0, mode='nearest')
    wl = (~dark & valid).astype(np.float32)
    wd = (dark & valid).astype(np.float32)
    gl, gd = g(wl), g(wd)
    Lc = np.stack([g(C[..., i] * wl) for i in range(3)], -1) / \
        np.maximum(gl, 1e-4)[..., None]
    Dc = np.stack([g(C[..., i] * wd) for i in range(3)], -1) / \
        np.maximum(gd, 1e-4)[..., None]
    Dcol = np.where(dark[..., None], C, Dc)
    Lcol = np.where(dark[..., None], Lc, C)
    new = a[..., None] * Dcol + (1 - a[..., None]) * Lcol
    ch = edge & (np.abs(a - d) > 0.03) & (gl > 0.02) & (gd > 0.02)
    out = C.copy()
    out[ch] = new[ch]
    return out, ch


# BUILD 896b: the valley method (replaces clean_dirt + smooth_edges in plan)
def objects(C, valid, warm=36.0, wlum=65.0, half=7.0, olum=80.0, zone=6, minsz=20):
    from scipy import ndimage as ND
    L3=np.array([0.3,0.59,0.11],np.float32)
    lum=C@L3
    w=valid&(C[...,0]-C[...,2]>=warm)&(lum>=wlum)
    bright=valid&(lum>=140)
    marking=ND.binary_dilation(ND.binary_opening(bright,structure=np.ones((13,13))),iterations=6)&bright
    stake=w&~marking
    light=valid&(lum>=olum)&(ND.distance_transform_edt(~stake)<=zone)
    light&=~marking
    obj=stake|light
    lab,n=ND.label(obj)
    if n:
        sz=ND.sum(obj,lab,range(1,n+1)); obj=np.isin(lab,[i+1 for i in range(n) if sz[i]>=minsz])
    obj=ND.binary_opening(obj,structure=np.ones((2,2)))
    return obj
def _inpaint(img, mask, valid, keep_margin=12.0):
    """(out, taken) -- fill `mask` from the texels right around it.

    BUILD 896d (hardware 11-07: "it looks like its replaced with a whiter
    texture", "missing its shadow on its right side"): an outline texel takes
    the colour of its own NEIGHBOURS, not a far-side mean. Inside a soft
    shadow band the neighbours are shadow-toned, so the shadow stays; between
    a stake and the marking they are half of each, which is the AA. A masked
    texel that is not clearly darker than what it would become is left as
    painted (it is shading, not outline)."""
    from scipy import ndimage as ND
    L3 = np.array([0.3, 0.59, 0.11], np.float32)
    out = img.copy()
    src = valid & ~mask
    fill = out.copy()
    todo = mask.copy()
    for sig in (1.0, 1.6, 2.5, 4.0):
        if not todo.any():
            break
        w = src.astype(np.float32)
        gw = ND.gaussian_filter(w, sig, mode='nearest')
        f = np.stack([ND.gaussian_filter(out[..., i] * w, sig, mode='nearest')
                      for i in range(3)], -1) / np.maximum(gw, 1e-4)[..., None]
        ok = todo & (gw > 0.08)
        fill[ok] = f[ok]
        todo &= ~ok
    taken = mask & ~todo & ((img @ L3) < (fill @ L3) - keep_margin)
    out[taken] = fill[taken]
    return out, taken


def fix_outlines(C, valid, inbox, reach=3.0, far_lo=3.5, far_hi=7.0, drop=12.0, ratio=0.85, sig=1.2):
    from scipy import ndimage as ND
    L3=np.array([0.3,0.59,0.11],np.float32)
    lum=C@L3
    obj=objects(C,valid)
    d=ND.distance_transform_edt(~obj)
    g=lambda x,s: ND.gaussian_filter(x,s,mode='nearest')
    def mean_over(m,s):
        w=m.astype(np.float32); gw=g(w,s)
        return np.stack([g(C[...,i]*w,s) for i in range(3)],-1)/np.maximum(gw,1e-4)[...,None], gw
    oref,go=mean_over(obj&valid,2.0)
    fmask=valid&~obj&(d>=far_lo)&(d<=far_hi)
    fref,gf=mean_over(fmask,2.5)
    ol=oref@L3; fl=fref@L3
    valley=valid&~obj&(d<=reach)&(go>0.02)&(gf>0.02)&(lum<np.minimum(ol,fl)-drop)&(lum<ratio*fl)&inbox
    # BUILD 896c (hardware 11-07: "you completely removed some of the shade
    # effects that are on the right side edge of the straight textures"): a
    # stake's own shaded face is darker than its lit wood and than the road,
    # but it is WIDE and only moderately dark. The outline is at most 2
    # texels thick and under VALLEY_DARK x its lit neighbours.
    valley&=lum<VALLEY_DARK*np.maximum(ol,fl)
    # the outline's half-dark outer texels sit ON the object's border (warm
    # enough to count as object): taken too, but only where they are clearly
    # darker than both the object and what lies beyond -- so never against
    # the darker road, where the shaded faces are
    din=ND.distance_transform_edt(obj)
    # The object's own mean is pulled down by its outline and shaded faces, so
    # judge a border texel against the texels just inside it instead: an outline
    # is darker than both those and the far side; a shaded face is not darker
    # than the shaded texels right behind it, so it stays.
    iref,gi=mean_over(obj&valid&(din>=2)&(din<=4),1.5); il=iref@L3
    rim=obj&valid&inbox&(din<=1.5)&(gf>0.02)&(gi>0.02)&(lum<RIM_DARK*fl)&(lum<il-14)&(lum<0.85*il)
    valley|=rim
    valley&=~ND.binary_opening(valley,structure=np.ones((3,3)))
    shade=valid&~obj&(lum<np.minimum(ol,fl)-drop)&~valley
    shade=ND.binary_opening(shade,structure=np.ones((3,3)))
    out,valley=_inpaint(C,valley,valid)
    # smooth anti-aliased silhouette of the objects against what lies beyond
    cov=np.clip(0.5+1.6*(g(obj.astype(np.float32),sig)-0.5),0,1)
    band=valid&inbox&(d<=2.5)&(ND.distance_transform_edt(obj)<=2.5)&(go>0.02)&~shade
    edge=band&(np.abs(cov-obj.astype(np.float32))>0.05)
    # BUILD 896c: only where an outline was taken out -- everywhere else the
    # border is the art's own (its shaded faces included) and stays
    edge&=ND.binary_dilation(valley,iterations=2)&~valley
    beyond=np.where(valley[...,None],fref,out)  # non-object side: cleaned colour
    # colour on each side: object side = its own (or local object mean), outside = cleaned local outside mean
    om,_=mean_over(obj&valid,1.2); xm,_=mean_over(valid&~obj,1.2)
    xo=out.copy()
    # recompute outside mean from the cleaned image
    w=(valid&~obj).astype(np.float32); gw=g(w,1.2)
    xm=np.stack([g(out[...,i]*w,1.2) for i in range(3)],-1)/np.maximum(gw,1e-4)[...,None]
    inside=np.where(obj[...,None],out,om); outside=np.where(obj[...,None],xm,out)
    new=cov[...,None]*inside+(1-cov[...,None])*outside
    out[edge]=new[edge]
    # BUILD 896c: thin dark seams pinched between two LIGHT sides (a stake's
    # outline over the light marking, its dotted staircase): along some axis
    # the texel is far darker than the light art 1-2 texels away on BOTH
    # sides. Shaded faces are wide (a 3x3 opening keeps them) or sit against
    # the dark road (one side is not light), so they never qualify.
    l2=out@L3
    din2=ND.distance_transform_edt(obj)
    near=valid&inbox&((d<=3)|(din2<=3))
    best=np.zeros(l2.shape,np.float32); bfill=np.zeros_like(out)
    def sh(a,dy,dx):
        return np.roll(np.roll(a,dy,0),dx,1)
    for dy,dx in ((0,1),(1,0),(1,1),(1,-1)):
        sides=[]
        for sg in (1,-1):
            c1=sh(l2,sg*dy,sg*dx); c2=sh(l2,2*sg*dy,2*sg*dx)
            k1=sh(out,sg*dy,sg*dx); k2=sh(out,2*sg*dy,2*sg*dx)
            use1=c1>=c2
            sides.append((np.where(use1,c1,c2),np.where(use1[...,None],k1,k2)))
        lo=np.minimum(sides[0][0],sides[1][0])
        cand=lo>best
        best=np.where(cand,lo,best)
        bfill=np.where(cand[...,None],0.5*(sides[0][1]+sides[1][1]),bfill)
    pinch=near&(best>=PINCH_LIGHT)&(l2<PINCH_RATIO*best)&(l2<best-PINCH_DROP)
    pinch[:2,:]=pinch[-2:,:]=pinch[:,:2]=pinch[:,-2:]=False
    pinch&=~ND.binary_opening(pinch,structure=np.ones((3,3)))
    out,pinch=_inpaint(out,pinch,valid)
    valley=valley|pinch
    # BUILD 896c: stray black specks left anywhere near the objects -- tiny
    # (<= SPECK_MAX texels), far darker than around them, not part of shade
    l2=out@L3
    # BUILD 896e (hardware 11-07, the lattice in the right box): stakes in
    # deep shade are under the object test's warmth (R-B 24..32), so the
    # speck sweep measures its reach from any wood-toned texel as well
    wood=valid&(l2>=65)&(out[...,0]-out[...,2]>=SPECK_WOOD_WARM)
    dw=ND.distance_transform_edt(~(obj|wood))
    blk=valid&inbox&(l2<SPECK_LUM)&(l2<SPECK_RATIO*ND.median_filter(l2,size=(5,5)))&(dw<=10)
    lab,n=ND.label(blk,structure=np.ones((3,3)))
    speck=np.zeros_like(blk)
    if n:
        idx=np.arange(1,n+1)
        sz=ND.sum(blk,lab,idx)
        # a speck is far darker than the ring right around it (so a texel or
        # two of black in the dark backdrop counts too); a corner of graded
        # shading is not, its ring is nearly as dark
        ring=ND.binary_dilation(blk,iterations=2)&~blk&valid
        rl=ND.mean(l2,ND.grey_dilation(lab,size=(5,5))*ring,idx)
        ml=ND.mean(l2,lab,idx)
        speck=np.isin(lab,idx[(sz<=SPECK_MAX)&(ml<SPECK_RATIO*rl)])
        w=(valid&~blk&~obj).astype(np.float32); gw=g(w,1.5)
        fill=np.stack([g(out[...,i]*w,1.5) for i in range(3)],-1)/np.maximum(gw,1e-4)[...,None]
        # inside a stake: fill from the wood around it instead
        w2=(valid&~blk).astype(np.float32); gw2=g(w2,1.5)
        fill2=np.stack([g(out[...,i]*w2,1.5) for i in range(3)],-1)/np.maximum(gw2,1e-4)[...,None]
        fill=np.where((gw>0.05)[...,None],fill,fill2)
        out,speck=_inpaint(out,speck,valid,keep_margin=0.0)
    return out, valley|edge|speck, valley|speck


def plan(name, sec9):
    """(new section 9, info) -- raises on any doubt."""
    from scipy import ndimage as ND
    import diag_common as DC
    import field_bg_native as FN
    import ff7nx_edgesmooth as ES
    cfg = TARGETS[name]
    s9 = bytes(sec9)
    pl, ts, _te, px = DC.parse_pages(s9)
    pm = {p.slot: p for p in pl if p is not None}
    if px // 16 != 16 * K:
        raise ValueError('pages are %d px' % px)
    k = px // 16
    users = collections.defaultdict(set)
    for lay, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts):
        for o in offs:
            n = struct.unpack_from('<H', s9, o + 18)[0] or 16
            for po, so in ((32, 10), (34, 14)):
                if po == 34 and not s9[o + 28]:
                    continue
                sx, sy = struct.unpack_from('<hh', s9, o + so)
                for cy in range(sy // 16, (sy + n - 1) // 16 + 1):
                    for cx in range(sx // 16, (sx + n - 1) // 16 + 1):
                        users[(s9[o + po], cx, cy)].add(o)
    recs = ES._records(s9, ts, pm, cfg['layer'])
    datas = {}
    info = {'texels': 0, 'records': 0, 'shared': 0}
    for bx0, by0, bx1, by1 in cfg['boxes']:
        sel = []
        for o in recs:
            x, y = struct.unpack_from('<hh', s9, o + 2)
            n = struct.unpack_from('<H', s9, o + 18)[0] or 16
            if x < bx1 and x + n > bx0 and y < by1 and y + n > by0:
                sel.append(o)
        if not sel:
            raise ValueError('no records in box %s' % ((bx0, by0, bx1, by1),))
        tex, own, use, org, _at = ES._canvas(s9, sel, pm, px)
        C = ES._rgbf(tex)
        valid = tex != 0
        H, W = tex.shape
        yy, xx = np.mgrid[0:H, 0:W]
        fx = org[0] + (xx + 0.5) / K
        fy = org[1] + (yy + 0.5) / K
        inbox = (fx >= bx0) & (fx < bx1) & (fy >= by0) & (fy < by1)
        out, ch, valley = fix_outlines(C, valid, inbox)
        ch &= own >= 0
        info['outline'] = info.get('outline', 0) + int((valley & (own >= 0)).sum())
        enc = ES._enc(out)
        blocks = {}
        for py, px_ in zip(*np.nonzero(ch)):
            i = int(own[py, px_])
            x, y, n, o = use[i]
            if i not in blocks:
                sx, sy = struct.unpack_from('<hh', s9, o + 10)
                pg = s9[o + 32]
                src = datas[pg] if pg in datas else np.frombuffer(
                    pm[pg].data, '<u2').reshape(px, px)
                blocks[i] = src[sy // 16 * k:sy // 16 * k + n * K,
                                sx // 16 * k:sx // 16 * k + n * K].copy()
            blocks[i][py - (y - org[1]) * K, px_ - (x - org[0]) * K] = \
                enc[py, px_]
        for i, b_ in sorted(blocks.items()):
            x, y, n, o = use[i]
            pg = s9[o + 32]
            sx, sy = struct.unpack_from('<hh', s9, o + 10)
            mine = n // 16
            if any(len(users[(pg, cx, cy)]) > 1
                   for cy in range(sy // 16, sy // 16 + mine)
                   for cx in range(sx // 16, sx // 16 + mine)):
                info['shared'] += 1
                continue
            if pg not in datas:
                datas[pg] = np.frombuffer(pm[pg].data, '<u2').reshape(
                    px, px).copy()
            cur = datas[pg][sy // 16 * k:sy // 16 * k + n * K,
                            sx // 16 * k:sx // 16 * k + n * K]
            info['texels'] += int((cur != b_).sum())
            cur[:] = b_
            info['records'] += 1
    if not datas or not info['texels']:
        raise ValueError('nothing to change')
    plist, t0, t1 = FN.parse_texture_block(s9, px)
    for pg, d in datas.items():
        q = plist[pg]
        plist[pg] = FN.Page(pg, q.size_flag, 2, d.tobytes(), q.px)
    return FN.replace_texture_block(s9, plist, t0, t1), info


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': []}
    if disabled():
        return st
    encode = encode or archive.encode_field
    for name in TARGETS:
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[8], info = plan(name, parts[8])
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:100]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['names'].append('%s (%d texels in %d records%s)' % (
            name, info['texels'], info['records'],
            ', %d shared cells left' % info['shared'] if info['shared']
            else ''))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  EDGE AA (BUILD 896): jagged painted edges along the '
                   'stakes smoothed and anti-aliased, every shadow and '
                   'outline kept: %s. %s=1 disables.'
                   % (', '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! edge AA %s: unchanged -- %s' % (name, why))
    return '\n'.join(out)
