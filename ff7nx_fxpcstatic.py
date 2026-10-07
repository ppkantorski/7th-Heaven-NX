#!/usr/bin/env python3
"""ff7nx_fxpcstatic.py -- draw a field's whole additive FX effect from the
Cosmos pages the shipped mod actually uses.

BUILD 618d. ancnt2 (the Forgotten Capital altar): hardware showed the big
translucent light overlay at 1997 resolution, with dark one-texel dither
holes, ending at the 4:3 edges, and a stepped piece missing at the top.

MEASURED on the built field: the overlay is layer 2, every record FX blend
mode 1, on pages 15..21 -- static tiles (param 0, palettes 5/6/7 and
Cosmos's widescreen strips) and four light-shaft groups (param 1, states
1/2/4/8, palettes 8..11, all switched on once by BGON; MPPAL+LDPAL pulse
their brightness). All seven pages were still 256px paletted:
  * ff7nx_fxpages vetoed them (BUILD 608/617 rule) because Cosmos ships 61
    state frames for palettes 8..11 -- but only in its `AA REMOVED` folder,
    which the mod does not install. Its installed folder has ONE static
    `ancnt2_NN_00` per page, and FFNx falls back to that for every palette,
    so on PC the whole overlay is that HD picture.
  * ff7nx_fxsplit could not clone the static palettes: it needed one new
    page per source page and the field has two free band slots.
  * Cosmos's widescreen strips name palette 12 (cosmo2's fault, again); the
    build re-seated them on 11, a palette the script pulses -- so the
    margins and the stepped patch at the top drew only when that shaft
    group was bright.
Software render of the built field reproduces all three.

FIX: every FX page of the field becomes, IN PLACE, the depth-2 Cosmos
picture (alpha premultiplied, black adds nothing -- ff7nx_fxpages's own
encoding). Slots, records, UVs, palettes and tile states do not move; the
FINDINGS-194 ladder draws depth-2 15..23 additively. The shafts still switch
by state, but their brightness pulse stops -- exactly what PC with Cosmos
shows. All-or-nothing per field: every FX page must be FX-only, blend mode
1, have one shared Cosmos image across its palettes, and the field must stay
inside FIELD_MB_CAP and the raw cap, else it is left byte-identical.

BUILD 618f. hyou5_2 (Great Glacier): the blizzard (layer 4, the same
scrolling 704-unit snow/wind grid ff7nx_trnad4 fixes on every Glacier field)
is split over FX pages 18 and 19. Page 19 is truecolor; page 18 stayed 256px
paletted (grainy, and a 32-unit strip flashed in at the left edge for single
frames -- hyou3, whose two blizzard pages are both truecolor, does not do
it). fxpages refused page 18 because its two palettes load two DIFFERENT
Cosmos sheets (`_18_11_<hash>`, `_18_12_<hash>`, one picture each, no
animation; the script animates neither palette) and fxsplit only splits
pages with an animated palette. The palettes use disjoint cells (32 each),
so ONE truecolor page can hold exactly what FFNx draws: every cell from the
sheet FFNx loads for the palette that samples it. FIELDS may name the slots
to convert (hyou5_2: 18 only -- its pages 15..17 are Cosmos-animated and
stay paletted).

BUILD 618g. las2_1 (Northern Crater, the green lifestream glow): the same
case as ancnt2 -- FX pages 15..19 all 256px paletted because Cosmos's state
frames for palettes 7/11 exist only in AA REMOVED; the installed folder has
one static `_NN_00` per page. All five pages convert in place (27.3 MB);
the tile-state animation (params 2..5) still runs, the palette pulse stops
as on PC. las2_3 (the pond cave): page 15 only -- its static palettes 10/13/15
each load their own single sheet and sample disjoint cells, so it is
composited like hyou5_2's page 18. Pages 16..18 carry the pond animation
Cosmos ships as 43/23 installed frames (palettes 8/12/14): a truecolor page
would freeze it, so they stay paletted.

BUILD 618j. crater_2 (Northern Crater, the walk down through the lifestream
glow): hardware showed the whole glow overlay as flat posterised 16x16
blocks that flash in and out with the mist animation, and the field ran at
~19 fps (60 fps capture, 165 distinct frames in 8.55 s). MEASURED: layer 2
is 1656 FX records (blend mode 1) on pages 15..21, all still 256px paletted,
covering the whole picture -- a 4-frame mist animation (param 1, states
1/2/4/8, 8 frames each, one palette per state) whose brightness the script
pulses EVERY frame: STPAL 3, MPPAL by a variable that walks 50..62, then
seven LDPALs into palettes 3,4,5,6,7,8,10 -- seven palette writes per frame
into palettes sampled by seven paletted pages. ancnt2 and las2_1 pulse the
same way and stopped costing anything once their FX pages became truecolor
(truecolor pages have no palette to rewrite). fxpages vetoed crater_2
because Cosmos ships the pulse INSTALLED (LIMIT BREAK AA): per palette,
12 brightness levels named by FFNx palette hash (plus the unpulsed first
frame), each sheet holding only the cells that palette samples -- the same
12 hashes on every palette of a page, since LDPAL makes those palettes
identical. Cells are disjoint between palettes (0 clashes, 1656 records).

FIX: PULSE_FIELDS pages are composited like hyou5_2's page 18, at ONE pulse
level: for every hash common to all the page's palettes, each cell is taken
from its owning palette's sheet; the level whose premultiplied luminance is
the median of the 12 is kept (~81% of the unpulsed `_NN_00` art, the middle
of what PC cycles through). The mist animation still runs by tile state;
the +-10% brightness pulse stops, as on ancnt2/las2_1.

BUILD 618s. mrkt2 (Wall Market, the steam), hardware 10-03: "low quality
steam/smoke effects (pixellated)". The steam is layer 2 param 31 (6 tile
states), FX page 15 -- the one FX page left 256px paletted: its palette 9
is pulsed by ADPAL+LDPAL (a flicker), so fxpages vetoes it, and fxsplit
already moved the page's static palettes 8/10 to truecolor pages 16/17.
Cosmos ships palette 9 as 8 installed hash frames (the pulse): the page is
pulse-composited at the median level like crater_2. 31.0 -> 34.1 MB.

BUILD 618s. junbin5 (Junon, the Shinra building interior), hardware 10-03:
"blocky glow ... not the light cone, just some of the light effects".
FX pages 15, 18 and 19 stayed 256px paletted:
  * 18 and 19: static palettes 3..6 load `_NN_00`, 7/8 their own single
    hash sheet -> a multi-sheet page, composited like hyou5_2's page 18;
  * 15: the same plus palette 9, which the script pulses (6 hash frames):
    MIXED -- every static palette's cells from its sheet, palette 9's cells
    from its median pulse level.
The tile-state animations (params 3..6) keep running; palette 9's small
pulse stops. 25.81 -> 35.02 MB: junbin5 alone gets MB_OVERRIDE 35.3, the
level fship_2 already runs at on hardware.

FIELDS is the measured list; SEVENTH_NX_NO_FX_PCSTATIC=1 disables.
"""
from __future__ import annotations

import collections
import os
import re
import struct

import diag_common as DC
import field_bg_native as FN
import field_bg_repack as FR

OFF_ENV = 'SEVENTH_NX_NO_FX_PCSTATIC'
FIELDS = {'ancnt2': None, 'hyou5_2': (18,),     # None = every FX page
          'las2_1': None, 'las2_3': (15,), 'crater_2': None,
          'mrkt2': (15,), 'junbin5': (15, 18, 19)}
PULSE_FIELDS = frozenset(('crater_2', 'mrkt2'))   # freeze an MPPAL pulse
MIXED_FIELDS = frozenset(('junbin5',))   # 618s: pulse + static palettes
MB_OVERRIDE = {'junbin5': 35.3}          # 618s: fship_2's proven level
# BUILD 618t. junbin5 (hardware 10-03, 618s): "like 1/4 of the glow effect
# is happening". Palettes 3..6 are PERMUTATIONS of one grey ramp, and the
# glow animates by state through them: palette 5 maps index 0 (the
# transparent background in palettes 3/4) to grey 96, palette 6 its middle
# indices to the brightest greys, so in those states the whole cell lights
# up as a soft bloom. Cosmos painted every cell with the palette-3/4 ramp
# (its `_00` sheet: HD vs vanilla 1.9/2.6 for palette 3/4 cells, 39.5 for
# palette 5), so the bloom states were missing. RAMP_FIELDS: per cell, the
# reference palette Cosmos's art matches is found among the cell palette's
# ramp family, and the HD art is mapped through reference-level ->
# own-level (piecewise linear over the shared indices, per channel).
RAMP_FIELDS = frozenset(('junbin5',))
# BUILD 618u. junbin5's orange lamps (hardware 10-03: "the top half of the
# light circle is not being animated at all"): the lamp glow is palette 9,
# which the script pulses and Cosmos animates (6 installed frames). Its
# lower rows sit on paletted pages 16/17 and kept pulsing; its upper rows
# (10 records) were on page 15, which 618s made truecolor -- frozen. The
# palettes listed here are MOVED off the converted pages first: each of
# their cells is copied, still paletted, into a free cell of a paletted FX
# page that already holds the palette, and the records are repointed. The
# whole lamp pulses again (1x art, as the rest of the lamp always was).
KEEP_PALETTED = {'junbin5': (9,)}
HASH_RE = re.compile(r'_([0-9a-f]{6,16})\.dds$', re.I)
FX_LO, FX_HI = 0x0F, 0x18


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _owners(sec9, slot, page, refs):
    """{cell: palette} for the records sampling `slot`; raises on a clash."""
    grid = 8 if page.size_flag else 16
    owner = {}
    for o in refs:
        u, v = struct.unpack_from('<II', sec9, o + 42)
        cell = (int(round(u / 1e7 * grid)), int(round(v / 1e7 * grid)))
        q = sec9[o + 22]
        if owner.setdefault(cell, q) != q:
            raise ValueError('page %d cell %r sampled by two palettes'
                             % (slot, cell))
    return owner


def _rec_rgba(provider, rec, px):
    """One Cosmos sheet, resampled once to the page size (no 565 trip)."""
    import numpy as np
    import dds_decode
    path, entry = rec
    reader = provider.readers.get(path)
    if reader is None:
        reader = provider.readers[path] = FR.IroReader(path)
    blob = reader.read(entry)
    if not blob:
        return None
    rgba, w, h = dds_decode.decode_dds(blob)
    rgba = FR.resample_rgba(rgba, w, h, px)
    return np.ascontiguousarray(
        np.frombuffer(rgba, np.uint8).reshape(px, px, 4))


def _pulse_composite(art, name, sec9, slot, page, px, refs):
    """(RGBA page at the median pulse level, (hash, levels)). BUILD 618j."""
    import numpy as np
    provider = art.provider
    owner = _owners(sec9, slot, page, refs)
    cs = px // (8 if page.size_flag else 16)
    cells_of = collections.defaultdict(list)
    for cell, q in owner.items():
        cells_of[q].append(cell)
    pals = sorted(cells_of)
    byhash = collections.defaultdict(dict)
    for q in pals:
        recs = getattr(provider, 'state_slots', {}).get(
            (name.lower(), slot, q)) or ()
        if len(recs) < 2:
            raise ValueError('page %d palette %d is not pulse-animated'
                             % (slot, q))
        for rec in recs:
            m = HASH_RE.search(rec[1])
            if m is None:
                raise ValueError('page %d palette %d: unhashed frame %s'
                                 % (slot, q, rec[1][-40:]))
            byhash[m.group(1).lower()][q] = rec
    common = sorted(h for h, d in byhash.items() if len(d) == len(pals))
    if len(common) < 2:
        raise ValueError('page %d: %d pulse level(s) shared by palettes %s'
                         % (slot, len(common), pals))
    levels = []
    for h in common:
        comp = np.zeros((px, px, 4), np.uint8)
        for q in pals:
            img = _rec_rgba(provider, byhash[h][q], px)
            if img is None or img.shape != (px, px, 4):
                raise ValueError('page %d palette %d level %s unreadable'
                                 % (slot, q, h))
            for cu, cv in cells_of[q]:
                sl = (slice(cv * cs, (cv + 1) * cs),
                      slice(cu * cs, (cu + 1) * cs))
                comp[sl] = img[sl]
        a = comp[..., 3:].astype(np.float32) / 255.0
        levels.append((float((comp[..., :3] * a).mean()), h, comp))
    levels.sort(key=lambda t: (t[0], t[1]))
    _lum, h, comp = levels[len(levels) // 2]
    if not np.any(comp[..., 3] >= 8):
        raise ValueError('page %d: chosen pulse level is empty' % slot)
    return comp, (h, len(levels))


def _mixed_composite(art, name, sec9, slot, page, px, refs):
    """BUILD 618s: one RGBA page where each palette's cells come from its
    own sheet, and a pulse-animated palette (>= 2 installed hash frames)
    from its median-luminance frame."""
    import numpy as np
    import ff7nx_fxmargin as FXM
    import ff7nx_fxpages as FP
    provider = art.provider
    cs = px // (8 if page.size_flag else 16)
    owner = _owners(sec9, slot, page, refs)
    cells_of = collections.defaultdict(list)
    for cell, q in owner.items():
        cells_of[q].append(cell)
    out = np.zeros((px, px, 4), np.uint8)
    pulsed = 0

    def paste(img, cells):
        for cu, cv in cells:
            sl = (slice(cv * cs, (cv + 1) * cs), slice(cu * cs, (cu + 1) * cs))
            out[sl] = img[sl]

    for q, cells in sorted(cells_of.items()):
        recs = getattr(provider, 'state_slots', {}).get(
            (name.lower(), slot, q)) or ()
        if len(recs) >= 2:
            levels = []
            for rec in recs:
                img = _rec_rgba(provider, rec, px)
                if img is None or img.shape != (px, px, 4):
                    raise ValueError('page %d palette %d frame unreadable'
                                     % (slot, q))
                sub = np.concatenate([img[cv * cs:(cv + 1) * cs,
                                          cu * cs:(cu + 1) * cs]
                                      .reshape(-1, 4) for cu, cv in cells])
                a = sub[:, 3:].astype(np.float32) / 255.0
                levels.append((float((sub[:, :3] * a).mean()), rec[1], img))
            levels.sort(key=lambda t: (t[0], t[1]))
            paste(levels[len(levels) // 2][2], cells)
            pulsed += 1
            continue
        sel = FP._selected_palette(provider, name, slot, q)
        if sel is None:
            raise ValueError('page %d palette %d has no Cosmos sheet'
                             % (slot, q))
        img = FXM._provider_rgba(art, name, slot, sel, px)
        if img is None or img.shape != (px, px, 4):
            raise ValueError('page %d sheet %d unreadable' % (slot, sel))
        paste(img, cells)
    if not pulsed:
        raise ValueError('page %d: no pulse palette (use _composite)' % slot)
    return out


def _composite(art, name, sec9, slot, page, px, refs):
    """One RGBA page: each cell from the sheet FFNx loads for the palette
    sampling it. Raises unless every cell is sampled by one palette."""
    import numpy as np
    import ff7nx_fxmargin as FXM
    import ff7nx_fxpages as FP
    provider = art.provider
    cs = px // (8 if page.size_flag else 16)
    owner = _owners(sec9, slot, page, refs)
    out = np.zeros((px, px, 4), np.uint8)
    sheets = {}
    for (cu, cv), q in owner.items():
        sel = FP._selected_palette(provider, name, slot, q)
        if sel is None:
            raise ValueError('page %d palette %d has no Cosmos sheet'
                             % (slot, q))
        if (name.lower(), slot, sel) in getattr(provider, 'ambiguous_slots',
                                               ()):
            raise ValueError('page %d palette %d is animated' % (slot, q))
        if sel not in sheets:
            img = FXM._provider_rgba(art, name, slot, sel, px)
            if img is None or img.shape != (px, px, 4):
                raise ValueError('page %d sheet %d unreadable' % (slot, sel))
            sheets[sel] = img
        out[cv * cs:(cv + 1) * cs, cu * cs:(cu + 1) * cs] = \
            sheets[sel][cv * cs:(cv + 1) * cs, cu * cs:(cu + 1) * cs]
    if len(sheets) < 2:
        raise ValueError('page %d: not a multi-sheet page' % slot)
    return out


def _family(pal, q, cand):
    """palettes in `cand` whose 16 lowest entries are a permutation of
    palette q's (a ramp family)."""
    import numpy as np
    key = sorted(map(tuple, pal[q][:16].astype(int)))
    return [r for r in cand
            if sorted(map(tuple, pal[r][:16].astype(int))) == key]


def ramp_remap(img, sec9, slot, page, px, refs, pal, idx):
    """BUILD 618t. Map each cell's HD art from the ramp Cosmos painted it
    with to the ramp its own palette draws. `pal`: (npal, 256, 3) float,
    `idx`: the 256x256 index page. Returns (img, cells remapped)."""
    import numpy as np
    from PIL import Image
    owner = _owners(sec9, slot, page, refs)
    pals = sorted(set(owner.values()))
    cs = px // 16
    out = img.copy()
    done = 0
    # where each cell is drawn, and which neighbours its state group has:
    # the lifted background (a bloom) fades out over half a cell towards a
    # side with no neighbour, instead of ending in a square edge.
    at = {}
    group = collections.defaultdict(set)
    for o in refs:
        u, v = struct.unpack_from('<II', sec9, o + 42)
        cell = (int(round(u / 1e7 * 16)), int(round(v / 1e7 * 16)))
        x, y = struct.unpack_from('<hh', sec9, o + 2)
        g = (sec9[o + 26], sec9[o + 27])
        at.setdefault(cell, (x, y, g))
        group[g].add((x, y))
    ramp = np.linspace(0.0, 1.0, cs, dtype=np.float32)
    feather = np.clip((np.arange(cs, dtype=np.float32) + 0.5) / cs, 0, 1)
    feather = feather * feather * (3 - 2 * feather)
    for (cu, cv), q in owner.items():
        fam = _family(pal, q, pals)
        if len(fam) < 2:
            continue
        a = idx[cv * 16:(cv + 1) * 16, cu * 16:(cu + 1) * 16]
        if a.max() >= 16:
            continue
        hd = img[cv * cs:(cv + 1) * cs, cu * cs:(cu + 1) * cs].astype(
            np.float32)
        pm = hd[..., :3] * (hd[..., 3:] / 255.0)
        small = np.asarray(Image.fromarray(np.clip(pm, 0, 255).astype(
            np.uint8)).resize((16, 16), Image.BOX)).astype(np.float32)
        err = {r: float(np.abs(small - pal[r][a]).mean()) for r in fam}
        ref = min(err, key=err.get)
        if ref == q:
            continue
        lv = pal[ref][:16].mean(-1)
        order = np.argsort(lv, kind='stable')
        xs, ys = [], []
        for i in order:
            if xs and abs(lv[i] - xs[-1]) < 0.5:
                continue
            xs.append(float(lv[i]))
            ys.append(pal[q][i])
        ys = np.array(ys, np.float32)
        lum = pm.mean(-1)
        new = np.stack([np.interp(lum, xs, ys[:, c]) for c in range(3)], -1)
        lift = np.stack([np.interp(np.zeros(1), xs, ys[:, c])[0]
                         for c in range(3)])
        if (cu, cv) in at and lift.max() > 0:
            x, y, g = at[(cu, cv)]
            m = np.ones((cs, cs), np.float32)
            if (x - 16, y) not in group[g]:
                m = np.minimum(m, feather[None, :])
            if (x + 16, y) not in group[g]:
                m = np.minimum(m, feather[::-1][None, :])
            if (x, y - 16) not in group[g]:
                m = np.minimum(m, feather[:, None])
            if (x, y + 16) not in group[g]:
                m = np.minimum(m, feather[::-1][:, None])
            unlifted = np.clip(new - lift, 0, 255)
            new = m[..., None] * new + (1 - m[..., None]) * unlifted
        out[cv * cs:(cv + 1) * cs, cu * cs:(cu + 1) * cs, :3] = np.clip(
            new, 0, 255).astype(np.uint8)
        out[cv * cs:(cv + 1) * cs, cu * cs:(cu + 1) * cs, 3] = 255
        done += 1
    return out, done


def evict(sec9, from_slots, pals):
    """(new section 9, records moved): FX records on `from_slots` whose
    palette is in `pals` move to free cells of depth-1 FX pages (not in
    `from_slots`) that already carry that palette."""
    import numpy as np
    pages_l, ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pages_l if p is not None}
    used = collections.defaultdict(set)
    holds = collections.defaultdict(set)
    movers = []
    for _layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            refs = [(sec9[o + 32], o + 10)]
            if sec9[o + 28]:
                refs.append((sec9[o + 34], o + 14))
            for sl, so in refs:
                x, y = struct.unpack_from('<hh', sec9, so)
                used[sl].add((x // 16, y // 16))
            if sec9[o + 28]:
                fx = sec9[o + 34]
                holds[fx].add(sec9[o + 22])
                if fx in from_slots and sec9[o + 22] in pals:
                    movers.append(o)
    if not movers:
        return sec9, 0
    data = {s: bytearray(p.data) for s, p in pm.items() if p.depth == 1}
    buf = bytearray(sec9)
    moved = 0
    for o in movers:
        q = sec9[o + 22]
        dest = [s for s in sorted(data) if s not in from_slots
                and FX_LO <= s < FX_HI and q in holds[s]
                and not pm[s].size_flag]
        free = None
        for s in dest:
            for cy in range(16):
                for cx in range(16):
                    if (cx, cy) not in used[s]:
                        free = (s, cx, cy)
                        break
                if free:
                    break
            if free:
                break
        if free is None:
            raise ValueError('no free paletted cell for palette %d' % q)
        s, cx, cy = free
        used[s].add((cx, cy))
        src = sec9[o + 34]
        sx, sy = struct.unpack_from('<hh', sec9, o + 14)
        a = np.frombuffer(bytes(data[src]), np.uint8).reshape(256, 256)
        b = np.frombuffer(bytes(data[s]), np.uint8).reshape(256, 256).copy()
        b[cy * 16:(cy + 1) * 16, cx * 16:(cx + 1) * 16] = \
            a[sy:sy + 16, sx:sx + 16]
        data[s] = bytearray(b.tobytes())
        buf[o + 34] = s
        struct.pack_into('<hh', buf, o + 14, cx * 16, cy * 16)
        struct.pack_into('<II', buf, o + 42, cx * 625000, cy * 625000)
        moved += 1
    plist, t0, t1 = FN.parse_texture_block(bytes(buf), px)
    for s, d in data.items():
        q = plist[s]
        plist[s] = FN.Page(s, q.size_flag, q.depth, bytes(d), q.px)
    return FN.replace_texture_block(bytes(buf), plist, t0, t1), moved


def plan_field(name, sec9, art, mb_cap=None, other_bytes=0, raw_cap=None,
               slots=None, sec3=None):
    """(new section 9, slots converted) -- raises on any doubt."""
    import ff7nx_fxpages as FP
    if name.lower() in KEEP_PALETTED and slots:
        sec9, _n = evict(sec9, set(slots), set(KEEP_PALETTED[name.lower()]))
    pages_l, ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pages_l if p is not None}
    fx_pals = collections.defaultdict(set)
    fx_refs = collections.defaultdict(list)
    base = set()
    for _layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            base.add(sec9[o + 32])
            fx = sec9[o + 34]
            if fx and FX_LO <= fx < FX_HI and (slots is None
                                                or fx in slots):
                fx_refs[fx].append(o)
                if sec9[o + 30] != 1:
                    raise ValueError('page %d has blend mode %d'
                                     % (fx, sec9[o + 30]))
                fx_pals[fx].add(sec9[o + 22])
    todo = sorted(s for s in fx_pals
                  if s in pm and pm[s].depth == 1)
    if not todo:
        raise ValueError('no paletted FX pages')
    images = {}
    for s in todo:
        if s in base:
            raise ValueError('page %d is also a base page' % s)
        img, why = FP._page_image(art, name, s, fx_pals[s], px)
        if img is None and why == 'palette-specific DDS images differ':
            img = _composite(art, name, sec9, s, pm[s], px, fx_refs[s])
        if (img is None and why == 'DDS has multiple runtime states'
                and name.lower() in MIXED_FIELDS):
            img = _mixed_composite(art, name, sec9, s, pm[s], px,
                                   fx_refs[s])
        if (img is None and why == 'DDS has multiple runtime states'
                and name.lower() in PULSE_FIELDS):
            img, _lvl = _pulse_composite(art, name, sec9, s, pm[s], px,
                                         fx_refs[s])
        if img is None:
            raise ValueError('page %d: %s' % (s, why))
        if name.lower() in RAMP_FIELDS:
            import numpy as np
            import ff7nx_framesim as FS
            if sec3 is None:
                raise ValueError('ramp remap needs the palettes')
            pal, _key = FS._palettes(sec3)
            idx = np.frombuffer(pm[s].data, np.uint8).reshape(256, 256)
            img, _n = ramp_remap(img, sec9, s, pm[s], px, fx_refs[s], pal,
                                 idx)
        images[s] = img
    plist, t0, t1 = FN.parse_texture_block(sec9, px)
    for s, img in images.items():
        q = plist[s]
        plist[s] = FN.Page(s, q.size_flag, 2, FP._encode_additive(img, px),
                           px)
    if mb_cap is not None:
        run = sum(FR._page_bytes(p.px, p.depth) for p in plist
                  if p is not None)
        if run > mb_cap * 1048576.0:
            raise ValueError('%.2f MB over the %.0f MB cap'
                             % (run / 1048576.0, mb_cap))
    out = FN.replace_texture_block(sec9, plist, t0, t1)
    if raw_cap is not None and other_bytes + len(out) > raw_cap:
        raise ValueError('raw %d over cap %d' % (other_bytes + len(out),
                                                  raw_cap))
    return out, todo


def apply_to_flevel(archive, payloads, art, encode=None, mb_cap=None,
                    raw_cap=None, log=lambda *_: None):
    import lgp
    total = {'names': [], 'refused': []}
    if disabled() or art is None:
        return total
    encode = encode or archive.encode_field
    for name, slots in FIELDS.items():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            other = len(raw) - len(parts[8])
            cap = MB_OVERRIDE.get(name, mb_cap) if mb_cap else mb_cap
            parts[8], done = plan_field(name, parts[8], art, mb_cap=cap,
                                        other_bytes=other, raw_cap=raw_cap,
                                        slots=slots, sec3=parts[3])
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append((name, str(exc)[:80]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['names'].append('%s: pages %s' % (
            name, ','.join(str(s) for s in done)))
    return total


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  FX PC-STATIC (BUILD 618d): whole additive effect drawn '
                   'from the installed Cosmos pages (%s). %s=1 disables.'
                   % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! fx pc-static %s: %s' % (name, why))
    return '\n'.join(out)
