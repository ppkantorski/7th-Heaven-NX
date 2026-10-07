#!/usr/bin/env python3
"""ff7nx_palstates.py -- palette-animated effects made TRUECOLOR without
losing their animation, by turning the palette animation into tile-state
animation. BUILD 618w.

THE PROBLEM. An effect the field script animates through its palette
(junbin5's pulsing lamps, eals_1's flowing waterfall) has to stay on a 256px
paletted page: a truecolor page has no palette, so converting it freezes the
effect (BUILD 618t froze eals_1's waterfall exactly like that). On PC, FFNx
swaps one Cosmos HD picture per palette state; the Switch engine cannot swap
textures, it can only switch TILES on and off (BGON / BGOFF, the mechanism
every tile animation in the game uses).

THE ROUTE. For each such effect:
  1. every palette state Cosmos ships (the `_<hash>` frames in the installed
     LIMIT BREAK AA folder: one HD picture per palette state) becomes its own
     set of truecolor cells on the additive FX pages (slots 15..23);
  2. each state's cells get their own copies of the effect's records, under
     a background parameter the field does not use, one state bit each;
  3. the palette operations in the field script are replaced, byte for byte
     in place, by BGCLR / BGOFF / BGON on those parameters, driven by the SAME
     script variables and timing that drove the palette.
The palette the effect used is no longer sampled by anything, and its
paletted pages are dropped when nothing else uses them.

MODES
  levels  the palette takes N discrete states, one per script variable value
          (junbin5's lamp: ADPAL by -1..-6; eals_1's spray flicker: MPPAL by
          66/62/58/54). Cosmos ships exactly N frames; ordered by brightness,
          frame k is state k. Exact.
  ab      the palette is the product of two independent cycles (eals_1's
          waterfall: two brightened 11-entry windows sliding along the
          palette, periods 9 and 16 = 144 states; Cosmos ships all of them, 152
          frames incl. the first lap's off-grid phase). Storing 144 full
          frames would take 17 pages, so the frames are decomposed per texel
          as  frame(a, b) = base + A[a] + B[b]  (least squares over Cosmos's
          frames, A and B shifted non-negative): an additive base layer plus
          one additive layer per window phase, 25 extra states. Each frame is
          labelled (a, b) by rendering the 1x page through the script's own
          palette arithmetic and matching the Cosmos frame to it.

Everything is checked before anything is written: the records, the exact
script bytes being replaced, the free cells, the page binding cap (256), the
field memory cap and the raw cap. Any doubt leaves the field byte-identical.

SEVENTH_NX_NO_PAL_STATES=1 disables; SEVENTH_NX_PAL_STATES_ONLY=a,b limits it.
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_PAL_STATES'
ONLY_ENV = 'SEVENTH_NX_PAL_STATES_ONLY'
FX_LO, FX_HI = 0x0F, 0x18
UV_CELL = 625000
GRID = 16
REC = 52
MAX_BIND = 256
EMPTY = 1.5               # premultiplied 0..255: a cell below this everywhere
                          # is black and needs no record

# ----------------------------------------------------------------- per field
# param ids are ones the field does not use (vanilla goes up to 63).
SPECS = {
    'junbin5': (
        {'palette': 9, 'mode': 'levels', 'param': 8,
         'states': (2, 3, 4, 5, 6, 7)},
    ),
    # BUILD 620f. blin68_2 (Shinra HQ, Hojo's lab, the specimen tank): the
    # tank glow is palette 13, pulsed by entity light2 (MPPAL x v/64, v
    # 66 -> 40..63..40, one step per tick). Cosmos ships 25 frames per page
    # (v 40..63 + the opening 66); the FX band holds 12 (16 fit at 31.6 MB,
    # but only with a pace the 8-opcode tick can't keep -- see the patch).
    # Levels = Cosmos's own frames at v = 40 + round(k * 23 / 11).
    # BUILD 620i (hardware: 12 levels read as missing frames, "uneven as it
    # loops"): EVERY pulse value v 40..63 is its own level (Cosmos's frame
    # for it), one per tick as in vanilla. 24 x 93 cells do not fit the FX
    # band, so only the tiles that carry the pulse animate -- as many as fit,
    # brightest pulse first (the top 59 carry 98.7 % of it); the faint rest
    # hold their middle frame (animate_cells 'auto').
    'blin68_2': (
        {'palette': 13, 'mode': 'levels', 'params': tuple(range(8, 32)),
         'states': tuple(range(24)), 'cosmos_frames': 25,
         'animate_cells': 'auto',
         # BUILD 620g (hardware 10-07: "a bit fainter / less dispersed than
         # the non true color version") scaled each level per channel to the
         # 1997 palette's light (620i through a soft knee). Hardware 10-07 of
         # 620i: "too bright ... one part gets brighter, stays that
         # brightness, then that brightness spreads out ... then it steps up
         # again". Those gains (~3x) pushed Cosmos's core, already 255 at the
         # top frame, into the ceiling: a flat plateau that grew level by
         # level, and blue clipped first, so the core turned cyan.
         # BUILD 620j instead: Cosmos's frames, one halo lift for all of them
         # (tone_gamma on each texel's brightest channel, hue kept, peak 236
         # -- under the 565 ceiling, so no texel ever flattens), and the 24 levels placed at EQUAL light steps by
         # blending neighbouring Cosmos frames (_even_levels) -- Cosmos's own
         # frames step 4..10 % apart, and its last one jumps 11 %.
         # Levels blend only Cosmos's first frame and its 24th (the 25th
         # jumps 11 % and its core is flat at the ceiling): Cosmos's frames
         # also change shape, so 6 % of texels dimmed while their
         # neighbours brightened. Two ends only = every texel rises
         # monotonically, as the 1997 uniform multiply does.
         'even_light': True, 'tone_gamma': 0.7, 'even_frames': (0, 23)},
    ),
    # BUILD 620j. ghotel (Ghost Hotel graves): the low fog is palette 8,
    # 348 records, one cell each, on two 256px PALETTED pages, pulsed by
    # light2 (MPPAL x v/64, v 40..62..40, 3 ticks a step). Hardware 10-07:
    # flat contour bands (5-bit palette colour on a dark additive fog: ~8
    # steps across it) and, with 620i's per-cell dithering, square seams.
    # 23 truecolor levels x 348 cells is 8004 cells; the FX band holds 2304.
    # SPLIT instead. On hardware the pulse shows 8*floor(E*v/64) (MPPAL on
    # 5-bit channels): F*v/64 less ~4/255 per lit channel, in 5-bit steps.
    # Its smooth equal is max(0, F*v/64 - 4) = a STATIC truecolor HD copy of
    # Cosmos's fog, max(0, F*40/64 - 4) (no palette, no bands) + the pulse
    # F*(v-40)/64 drawn by the original paletted records, every value kept.
    # Those records draw in blend mode 3 (background + texture/4 -- vanilla
    # uses it in colne_3, fship_5, min71, nmkin_1, prisila, ujunon4/5), and
    # light2's variable runs 2*(v-40) = 0..44 in steps of 2; each pulse
    # entry is chosen through the engine's own floor arithmetic
    # (_pulse_entries: within 1.75/255 of the line at every value). A 5-bit
    # step of that layer is 2/255 on screen (8/255 before).
    'ghotel': (
        {'palette': 8, 'mode': 'split', 'base_v': 40, 'delta_blend': 3,
         'pulse': tuple(range(40, 63)), 'pulse_step': 2,
         # BUILD 620k2 (hardware 10-07 of 620k: "the light effect looks a
         # bit dim ... the effect on the far right is very subtle, hard to
         # tell it's even happening"). 620j matched the NOMINAL 1997 light,
         # but this port draws every blended palette channel one 5-bit
         # level darker (measured in 618b: 8.67*c - 13.4, clamped at 0;
         # x1/4 in blend 3). So vanilla's fog swings ~5 -> ~9.5/255 (about
         # x2) while 620j's static base sat at ~11 and its pulse, eaten by
         # that offset, added only ~3.6 (x1.3). The base (trough) stays;
         # the pulse entries are now fitted THROUGH that transfer to
         # vanilla's own on-screen swing, max(0, 8.67/8*F*v/64 - 17.7)
         # from v 40 to 62: the glow rises by as much as vanilla's does.
         'hw_match': True,
         # and the static fog softened (sigma 7 HD px, ~2.3 units): one
         # hard band edge in Cosmos's fog left of the graves
         'soften': 7.0},
    ),
    'eals_1': (
        {'palette': 9, 'mode': 'levels', 'param': 28,
         'states': (0, 1, 2, 3)},
        {'palette': 8, 'mode': 'ab', 'pa': 3, 'na': 9, 'pb': 12, 'nb': 16,
         # window: (step of its start per frame, multiplier /64, entries)
         'win_a': (12, 0x82, 11), 'win_b': (9, 0xA2, 11),
         'b_first': 71},
    ),
}


def _h(*parts):
    return bytes.fromhex(''.join(parts))


def _junbin5_patches():
    """The lamp: var5[0x29] walks FF..FA..FF (WAIT 3 each) and ADPAL adds it
    (signed, -1..-6) to palette 9. The walk now runs 07..02..07, and the
    ADPAL+LDPAL (15 bytes) become BGCLR 8 + BGON 8,var5[0x29] (x3, 15 bytes):
    state i is the level  i - 8."""
    p = 8
    return [
        (_h('805029ff'), _h('80502907'), 1),
        (_h('145029fa0005'), _h('145029020005'), 1),
        (_h('145029ff0005'), _h('145029070005'), 1),
        (_h('e90055502030292929ffe6003009ff'),
         _h('e400%02x' % p, 'e005%02x29' % p, 'e005%02x29' % p,
            'e005%02x29' % p), 1),
    ]


def _eals_1_patches():
    """Palette 9 (spray flicker): var5[4] steps 66/62/58/54 by 4 and MPPAL
    multiplies palette 9 by it. Now 3/2/1/0 by 1, and each MPPAL+LDPAL
    (16 bytes) becomes BGCLR 28, BGON 28,var5[4], STPAL 9 (harmless: the
    routine's own buffer), BGON 28,var5[4].

    Palette 8 (the waterfall): the loop (STPAL, CPPAL, two MPPALs, LDPAL,
    two counters, WAIT 1; 70 bytes) is rewritten as a loop over per-phase
    params: var5[0] walks 3..11 (window A, 9 phases), var5[3] walks 12..27
    (window B, 16 phases); each frame the shown params go off, the counters
    step and wrap, the new params go on, WAIT 1. 49 bytes + 21 bytes of RET
    the loop never reaches."""
    s9 = 0x1C
    pa, na, pb, nb = 3, 9, 12, 16
    loop = _h('e1500000', 'e1500300', '85500001', '85500301',
              '145000%02x0205' % (pa + na - 1), '805000%02x' % pa,
              '145003%02x0205' % (pb + nb - 1), '805003%02x' % pb,
              'e0500000', 'e0500300', '240100')
    loop += bytes((0x12, len(loop)))
    old_loop = _h('e5000800ffe7000010ff',
                  'df5000000010008282820a', 'df500000001003a2a2a20a',
                  'e6001008ff', '8550000c', '85500309',
                  '14500064020580500000', '1450038a020580500300',
                  '240100123f')
    assert len(old_loop) == 70, len(old_loop)
    loop = loop + b'\x00' * (len(old_loop) - len(loop))
    return [
        (_h('8050000080500347'),
         _h('805000%02x' % pa, '805003%02x' % (pb + 8)), 1),
        (old_loop, loop, 1),
        (_h('80500442'), _h('80500403'), 1),
        (_h('87500404'), _h('87500401'), 1),
        (_h('85500404'), _h('85500401'), 1),
        (_h('df055500203001040404fee6003009ff'),
         _h('e400%02x' % s9, 'e005%02x04' % s9, 'e5000920ff',
            'e005%02x04' % s9), 2),
    ]


def _blin68_2_patches():
    """light2's pulse. v (var5[0x0B]) walked 40..63..40, one step per tick,
    and MPPAL+CPPAL+LDPAL loaded palette 13 at v/64. v now IS the level's
    parameter: param = v - 32 (8..31, state 0), and each pass is
    BGOFF v; v +-= 1; BGON v; BACK -- one tick per value exactly as vanilla,
    5 opcodes (vanilla 6). The IFs' jumps and the outer BACK keep their
    offsets; the bytes after each new BACK are RET, never reached. The field
    starts at 63 (vanilla 66: its first 3 ticks, 66..64, show 63)."""
    lo, hi = 8, 31
    old = _h('80500b42', '00', 'e5000d20ff',
             '14500b3f031b', '85500b01', 'ea00555020300b0b0bff',
             'e700203000', 'e600300dff', '121e',
             '14500b28021b', '87500b01', 'ea00555020300b0b0bff',
             'e700203000', 'e600300dff', '121e', '1240', '00')
    up = _h('14500b%02x031b' % hi, 'e1500b00', '85500b01', 'e0500b00',
            '1212')
    down = _h('14500b%02x021b' % lo, 'e1500b00', '87500b01', 'e0500b00',
              '1212')
    new = (_h('80500b%02x' % hi, '00', 'e5000d20ff')
           + up + b'\x00' * (32 - len(up))
           + down + b'\x00' * (32 - len(down)) + _h('1240', '00'))
    assert len(new) == len(old) == 0x4D, (len(new), len(old))
    return [(old, new, 1)]


GHOTEL_STEP = 2


def _ghotel_patches():
    """light2: var5[4] walked 40..62..40 (+-1, WAIT 2) and MPPAL scaled
    palette 8 by it /64. It now walks 0..44..0 (+-2): the same 23 values
    and timing, as 2*(v-40) -- the multiplier of the pulse-only palette
    (SPECS 'ghotel'). It starts at the top, 44 (= vanilla 62; vanilla's
    opening 66..63 is brighter than its own loop and would overflow the
    pulse entries)."""
    st = GHOTEL_STEP
    top = st * 22
    mp = 'ea0055502030040404ff'
    tail = 'e700203000' 'e6003008ff' '240200' '1221'
    old = _h('e5000820ff', '1450043e031e', '85500401', mp, tail,
             '14500428021e', '87500401', mp, tail, '1246')
    new = _h('e5000820ff', '145004%02x031e' % top, '855004%02x' % st, mp,
             tail, '14500400021e', '875004%02x' % st, mp, tail, '1246')
    return [(old, new, 1), (_h('80500442'), _h('805004%02x' % top), 1)]


PATCHES = {'junbin5': _junbin5_patches, 'eals_1': _eals_1_patches,
           'blin68_2': _blin68_2_patches, 'ghotel': _ghotel_patches}


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def only():
    raw = os.environ.get(ONLY_ENV, '').strip()
    return frozenset(x.strip().lower() for x in raw.split(',') if x.strip())


# ------------------------------------------------------------------- script
def patch_script(name, sec0):
    """Section 0 with the field's palette ops replaced, or raises."""
    out = bytes(sec0)
    for old, new, count in PATCHES[name]():
        if len(old) != len(new):
            raise ValueError('patch length %d != %d' % (len(old), len(new)))
        if out.count(old) != count:
            raise ValueError('script bytes %s... found %d times, expected %d'
                             % (old[:6].hex(), out.count(old), count))
        out = out.replace(old, new)
    return out


# ---------------------------------------------------------------- records
MATCH_OFF_ENV = 'SEVENTH_NX_PAL_STATES_COSMOS_LEVELS'


def disabled_match():
    """=1 ships Cosmos's level frames as they are (no 1x light match)."""
    return os.environ.get(MATCH_OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


KNEE = 192.0


def _soft(y):
    """Identity up to KNEE, then an exponential approach to 255: no hard
    clip, so a brighter level is always brighter (620i: hard clipping at 255
    made the top levels uneven and non-monotonic)."""
    y = np.asarray(y, np.float32)
    span = 255.0 - KNEE
    over = np.maximum(y - KNEE, 0.0)
    return np.where(y <= KNEE, y, KNEE + span * (1.0 - np.exp(-over / span)))


def _match_1x(lv, tg, vc, sec9, pm, pal, k, v_levels):
    """Scale every level, per channel, so the effect's total light equals
    the 1x palette's at that level's MPPAL value (v / 64). Records are
    counted as drawn (a shared cell counts once per record); the 1x cell is
    read where the engine reads it (the UV at +42). 620i: the gain goes
    through a soft knee (_soft) and is solved per level and channel by
    bisection, so the stored light matches the target exactly."""
    import collections as _c
    s1 = np.zeros(3)
    L = len(v_levels)
    area = (k / 16.0) ** 2
    count = _c.Counter()
    for _l, _i, r in tg:
        p = pm[r[34]]
        u, v = struct.unpack_from('<II', r, 42)
        cx, cy = u // UV_CELL, v // UV_CELL
        a = np.frombuffer(p.data, np.uint8, count=65536).reshape(256, 256)
        idx = a[cy * 16:(cy + 1) * 16, cx * 16:(cx + 1) * 16].astype(
            np.int64)
        c = pal[r[22]][idx]
        rgb = np.stack([c & 31, (c >> 5) & 31, (c >> 10) & 31], -1) * 8.0
        rgb[idx == 0] = 0
        s1 += rgb.sum((0, 1)) * area
        count[vc[r]] += 1
        if len(lv[vc[r]]) != L:
            raise ValueError('match_1x: %d levels, %d values'
                             % (len(lv[vc[r]]), L))
    if not (s1 > 0).all():
        raise ValueError('match_1x: an empty channel')
    cells = sorted(lv)
    wts = np.array([count[c] for c in cells], np.float32)
    gains = []
    for j in range(L):
        stack = np.stack([lv[c][j] for c in cells])        # n,k,k,3
        g3 = []
        for ch in range(3):
            x = stack[..., ch]
            target = (v_levels[j] / 64.0) * s1[ch]

            def total(g):
                return float((_soft(x * g).sum((1, 2)) * wts).sum())
            lo, hi = 0.0, 16.0
            if total(hi) < target:
                raise ValueError('match_1x: level %d channel %d cannot '
                                 'reach the 1x light' % (j, ch))
            for _ in range(40):
                mid = (lo + hi) / 2.0
                if total(mid) < target:
                    lo = mid
                else:
                    hi = mid
            g3.append((lo + hi) / 2.0)
        gains.append(tuple(g3))
    if max(max(g) for g in gains) > 8.0:
        raise ValueError('match_1x: gain over 8 (%s)' % (gains[:2],))
    out = {}
    for c, imgs in lv.items():
        out[c] = [np.stack([_soft(imgs[j][..., ch] * gains[j][ch])
                            for ch in range(3)], -1)
                  for j in range(L)]
    return out, [tuple(round(x, 3) for x in g) for g in gains]


# equal steps of the light actually added on screen (channel sum): luma
# under-counts this glow, whose blue core is already at 255 on top frames
_LUMA = np.array([1.0, 1.0, 1.0], np.float32) / 3.0


TONE_PEAK = 236.0     # below 248, the 565 ceiling: no flat plateau anywhere


def tone_lift(img, gamma, peak=TONE_PEAK):
    """Hue-preserving halo lift: every texel is scaled so its brightest
    channel m becomes peak * (m / 255) ** gamma. gamma < 1 lifts faint
    texels most; Cosmos's 255 core lands at `peak`, under the ceiling."""
    img = np.asarray(img, np.float32)
    if gamma == 1.0 and peak == 255.0:
        return img
    m = img.max(-1, keepdims=True)
    mm = np.maximum(m, 1e-6)
    return np.where(m > 0, img * (peak * (mm / 255.0) ** gamma / mm), 0.0)


def _even_levels(full, tg, vc, n_levels, gamma, frames=None):
    """n_levels images per cell whose total light (records counted as
    drawn, after tone_lift) rises in EQUAL steps from Cosmos's first frame to
    its last. Level j is the blend of the two Cosmos frames around the
    fractional frame position whose light is the j-th target (bisection;
    the frames' light must rise monotonically or this refuses)."""
    import collections as _c
    cnt = _c.Counter(vc[r] for _l, _i, r in tg)
    cells = sorted(full)
    w = np.array([cnt[c] for c in cells], np.float32)
    st = np.stack([np.stack(full[c]).astype(np.float32) for c in cells])
    if frames is not None:
        st = st[:, list(frames)]
    nf = st.shape[1]

    def at(t):
        i = min(int(np.floor(t)), nf - 2)
        a = np.float32(t - i)
        return (1 - a) * st[:, i] + a * st[:, i + 1]

    def light(img):
        return float((tone_lift(img, gamma).sum((1, 2)) * w[:, None]).sum(0)
                     @ _LUMA)
    fl = [light(st[:, i]) for i in range(nf)]
    if nf == 2:
        # two ends: refuse if texels would dim more than a little
        lo_, hi_ = tone_lift(st[:, 0], gamma), tone_lift(st[:, 1], gamma)
        if float((hi_.sum(-1) < lo_.sum(-1) - 24).mean()) > 0.02:
            raise ValueError('even_light: the end frames disagree in shape')
    if not all(b > a for a, b in zip(fl, fl[1:])):
        raise ValueError('even_light: Cosmos frames do not brighten '
                         'monotonically')
    out = {c: [] for c in cells}
    for j in range(n_levels):
        target = fl[0] + (fl[-1] - fl[0]) * j / (n_levels - 1)
        k = int(np.searchsorted(fl, target))
        lo, hi = float(max(k - 1, 0)), float(min(k, nf - 1))
        if hi <= lo:
            t = lo
        else:
            for _ in range(30):
                mid = (lo + hi) / 2.0
                if light(at(mid)) < target:
                    lo = mid
                else:
                    hi = mid
            t = (lo + hi) / 2.0
        img = tone_lift(at(t), gamma)
        for n_, c in enumerate(cells):
            out[c].append(img[n_])
    return out


_LV_CACHE = {}
_MATCH_CACHE = {}


def _pulse_order(lv, tg, vc):
    """Cells, largest pulse first: (top level - bottom level) light summed
    over the cell, times the records that draw it."""
    import collections as _c
    n = _c.Counter(vc[r] for _l, _i, r in tg)
    score = {c: float((imgs[-1] - imgs[0]).sum()) * n[c]
             for c, imgs in lv.items()}
    return sorted(score, key=lambda c: (-score[c], c))


def plan_field_auto(name, parts, vparts, art, mb_cap=35.0, raw_cap=None):
    """plan_field, with every 'animate_cells': 'auto' spec given the
    largest cell count that fits (placement, binding cap, memory, raw)."""
    specs = SPECS[name]
    if not any(sp.get('animate_cells') == 'auto' for sp in specs):
        return plan_field(name, parts, vparts, art, mb_cap, raw_cap)
    last = None
    import diag_common as _DC
    _pl, _ts, _te, _px = _DC.parse_pages(parts[8])
    _pm = {g.slot: g for g in _pl if g is not None}
    hi = max(len(_targets(parts[8], _pm, sp['palette'])) for sp in specs
             if sp.get('animate_cells') == 'auto')
    for n_try in range(hi, 0, -1):
        trial = [dict(sp, animate_cells=n_try)
                 if sp.get('animate_cells') == 'auto' else sp
                 for sp in specs]
        try:
            return plan_field(name, parts, vparts, art, mb_cap, raw_cap,
                              specs=trial)
        except ValueError as exc:
            msg = str(exc)
            if not any(w in msg for w in ('do not fit', 'binding cap',
                                          'MB cap', 'raw ')):
                raise
            last = msg
    raise ValueError('no cell count fits: %s' % last)


def _layer_rows(sec9):
    import diag_common as DC
    import ff7nx_parallaxfill as PF
    sv = DC.survey(sec9)
    return PF._layers(sec9, sv['back_start'], sv['tex_start'])


def _targets(sec9, pm, q):
    """[(layer, index, record bytes)] of the FX records drawn with palette q."""
    out = []
    for layer, _c, first, n in _layer_rows(sec9):
        for i in range(n):
            r = sec9[first + i * REC:first + (i + 1) * REC]
            if not r[28] or r[22] != q or not (FX_LO <= r[34] < FX_HI):
                continue
            if r[30] != 1:
                raise ValueError('palette %d record blend %d' % (q, r[30]))
            if r[26] != 0:
                raise ValueError('palette %d record has param %d'
                                 % (q, r[26]))
            p = pm.get(r[34])
            if p is None or p.depth != 1 or p.size_flag:
                raise ValueError('palette %d record on page %d is not a '
                                 '16-grid paletted page' % (q, r[34]))
            sx, sy = struct.unpack_from('<hh', r, 14)
            if sx % 16 or sy % 16:
                raise ValueError('unaligned source cell')
            out.append((layer, i, bytes(r)))
    if not out:
        raise ValueError('no FX records on palette %d' % q)
    return out


def _key(r):
    return (struct.unpack_from('<hh', r, 2), struct.unpack_from('<I', r, 38)[0],
            r[22])


def _vanilla_cells(targets, vsec9, any_palette=False):
    """{record bytes: (vanilla FX page, cx, cy)} -- Cosmos's sheet layout is
    vanilla's, and the build may have moved cells (repack, evict).
    `any_palette` (620j, ghotel): twins match on position and depth only --
    Cosmos authored ghotel's widescreen fog at palette 16, which the build
    re-seats onto 8 (ff7nx_palrange); still one twin or refused."""
    key = (lambda r: _key(r)[:2]) if any_palette else _key
    vmap = collections.defaultdict(set)
    for _layer, _c, first, n in _layer_rows(vsec9):
        for i in range(n):
            r = vsec9[first + i * REC:first + (i + 1) * REC]
            if r[28] and (not any_palette or r[30] == 1):
                sx, sy = struct.unpack_from('<hh', r, 14)
                vmap[key(r)].add((r[34], sx // 16, sy // 16))
    out = {}
    for _l, _i, r in targets:
        got = vmap.get(key(r))
        if not got or len(got) != 1:
            raise ValueError('no unique vanilla twin for a palette-%d record '
                             'at %s' % (r[22], struct.unpack_from('<hh', r, 2)))
        out[r] = next(iter(got))
    return out


# --------------------------------------------------------------------- art
def _frames(art, name, vpage, q):
    recs = getattr(art.provider, 'state_slots', {}).get(
        (name.lower(), vpage, q)) or ()
    return list(recs)


def _cells_of(prov, rec, cells, px):
    """(ncell, k, k, 3) premultiplied float32 cells of one Cosmos frame."""
    import ff7nx_fxpcstatic as PS
    img = PS._rec_rgba(prov, rec, px)
    if img is None or img.shape != (px, px, 4):
        raise ValueError('Cosmos frame unreadable')
    k = px // GRID
    f = img.astype(np.float32)
    pm = f[..., :3] * (f[..., 3:] / 255.0)
    return np.stack([pm[cy * k:(cy + 1) * k, cx * k:(cx + 1) * k]
                     for cx, cy in cells])


def _levels(art, name, q, vcell, nstates, px):
    """{vcell: [state 0 image, ...]} ordered dark -> bright, per vanilla page."""
    byp = collections.defaultdict(list)
    for c in sorted(set(vcell.values())):
        byp[c[0]].append(c[1:])
    out = {}
    for vpage, cells in byp.items():
        recs = _frames(art, name, vpage, q)
        if len(recs) != nstates:
            raise ValueError('page %d palette %d: %d Cosmos frames, %d states'
                             % (vpage, q, len(recs), nstates))
        fr = [_cells_of(art.provider, r, cells, px) for r in recs]
        order = np.argsort([float(f.mean()) for f in fr], kind='stable')
        lum = sorted(float(f.mean()) for f in fr)
        if any(b - a < 1e-3 for a, b in zip(lum, lum[1:])):
            raise ValueError('page %d palette %d: frames not distinct in '
                             'brightness %s' % (vpage, q, np.round(lum, 2)))
        for j, c in enumerate(cells):
            out[(vpage,) + c] = [fr[i][j] for i in order]
    return out


def _static_art(art, name, q, vcell, px):
    """{vcell: (k,k,3)} premultiplied HD cells of the ONE Cosmos sheet
    FFNx draws for palette q (else palette 0, saveload.cpp's fallback;
    several hashed states -> the brightest, as fxrequant picks)."""
    prov = art.provider
    byp = collections.defaultdict(list)
    for c in sorted(set(vcell.values())):
        byp[c[0]].append(c[1:])
    out = {}
    for vpage, cells in byp.items():
        have = set(prov.by_page.get((name.lower(), vpage), ()))
        use = q if q in have else (0 if 0 in have else None)
        if use is None:
            raise ValueError('page %d has no Cosmos art for palette %d'
                             % (vpage, q))
        recs = list(_frames(art, name, vpage, use)) or [
            prov.slots.get((name.lower(), vpage, use))]
        if not recs or recs[0] is None:
            raise ValueError('page %d palette %d art missing' % (vpage, use))
        best = None
        for rec in recs:
            fr = _cells_of(prov, rec, cells, px)
            if best is None or float(fr.sum()) > float(best.sum()):
                best = fr
        for j, c in enumerate(cells):
            out[(vpage,) + c] = best[j]
    return out


TRUNC_OFFSET = 4.0     # 8-bit light MPPAL's floor (c*v >> 6, 5-bit) costs


def _pulse_depth(tg, pm, palq, base, vc, k, top):
    """Gain on the linear pulse F*d/64 so the split's light at the TOP of
    the pulse equals what hardware shows there (8*floor(E*top/64) summed
    over every record's texels); the bottom already matches (the base).
    The hardware pulse is mostly one jump near v 43 and then nearly flat;
    this keeps its range, spread evenly over every value."""
    e = np.stack([palq & 31, (palq >> 5) & 31, (palq >> 10) & 31],
                 -1).astype(np.int64)
    e[0] = 0
    real_top = lin_d = base_sum = 0.0
    d_top = top - 40
    for _l, _i, r in tg:
        sx, sy = struct.unpack_from('<hh', r, 14)
        a = np.frombuffer(pm[r[34]].data, np.uint8, count=65536).reshape(
            256, 256)[sy:sy + 16, sx:sx + 16]
        real_top += float((8 * ((e[a] * top) >> 6)).sum())
        lin_d += float((8.0 * e[a] * d_top / 64.0).sum())
        base_sum += float(base[vc[r]].sum()) / (k / 16.0) ** 2
    if lin_d <= 0:
        raise ValueError('split: no pulse')
    return max(0.0, min(1.0, (real_top - base_sum) / lin_d))


HW_GAIN, HW_OFF = 8.67, 13.4     # blended palette channel on screen (618b)
HW_FLOOR = 4.33                  # mean loss of MPPAL's c*v >> 6 floor


def hw_light(f8, v):
    """Smooth on-screen light of a vanilla blended palette texel of 8-bit
    colour f8 at MPPAL multiplier v (618b transfer, floor averaged)."""
    return np.maximum(0.0, HW_GAIN / 8.0 * f8 * v / 64.0
                      - HW_OFF - HW_FLOOR)


def _soften_split(base, vc, tg, k, sigma):
    """BUILD 620k2. {record: (k,k,3)} the split's static fog, assembled in
    field space from every record's Cosmos cell, blurred (Gaussian, sigma
    in HD px, normalised over the fog's own cells so its border keeps its
    level) and cut back per record. ghotel: Cosmos's fog has one hard
    band edge left of the graves (+8/255 over 2..4 px, hardware 10-07:
    "an abrupt discontinuity in the fog effect")."""
    from scipy.ndimage import gaussian_filter
    pos = {r: struct.unpack_from('<hh', r, 2) for _l, _i, r in tg}
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    x0, y0 = min(xs), min(ys)
    W = (max(xs) - x0 + 16) * k // 16
    H = (max(ys) - y0 + 16) * k // 16
    can = np.zeros((H, W, 3), np.float32)
    wt = np.zeros((H, W), np.float32)
    for r, (x, y) in pos.items():
        X, Y = (x - x0) * k // 16, (y - y0) * k // 16
        can[Y:Y + k, X:X + k] = base[vc[r]]
        wt[Y:Y + k, X:X + k] = 1.0
    bc = gaussian_filter(can, (sigma, sigma, 0), mode='constant')
    bw = gaussian_filter(wt, sigma, mode='constant')
    out = {}
    for r, (x, y) in pos.items():
        X, Y = (x - x0) * k // 16, (y - y0) * k // 16
        out[r] = (bc[Y:Y + k, X:X + k]
                  / np.maximum(bw[Y:Y + k, X:X + k], 1e-3)[..., None])
    return out


def _pulse_entries_hw(palq, step, pulse_lo, n_vals):
    """BUILD 620k2. Pulse entries fitted THROUGH the port's transfer:
    shown = max(0, 8.67*((e*step*d) >> 6) - 13.4) / 4 (blend 3) against
    vanilla's own on-screen pulse hw_light(8E, lo+d) - hw_light(8E, lo)."""
    e = np.stack([palq & 31, (palq >> 5) & 31, (palq >> 10) & 31], -1)
    d = np.arange(n_vals)
    cand = np.arange(32)
    c = (cand[:, None] * step * d[None, :]) >> 6
    shown = np.maximum(0.0, HW_GAIN * c - HW_OFF) / 4.0
    codes = np.array(palq, np.int64)
    errs, worst = [], 0.0
    for j in range(1, len(palq)):
        cc = int(palq[j])
        if not cc & 0x7FFF:
            continue
        ch = []
        for k in range(3):
            f8 = 8.0 * e[j, k]
            tgt = hw_light(f8, pulse_lo + d) - hw_light(f8, pulse_lo)
            se = ((shown - tgt[None, :]) ** 2).mean(1)
            b = int(se.argmin())
            ch.append(b)
            errs.append(float(se[b]))
            worst = max(worst, float(np.abs(shown[b] - tgt).max()))
        codes[j] = ch[0] | (ch[1] << 5) | (ch[2] << 10) | (cc & 0x8000)
    return codes, float(np.sqrt(np.mean(errs))), worst


def _pulse_entries(palq, step, pulse_lo, n_vals, gain=1.0):
    """Pulse-palette entries for the split: per entry and channel, the 5-bit
    value e whose on-screen pulse 2*((e*step*d) >> 6) (blend 3 = /4 of the
    8-bit 8*x) is closest, over every d = v - pulse_lo, to the 1997 entry's
    linear pulse 8*E*d/64. Returns (codes, rms, worst) in 8-bit units."""
    e = np.stack([palq & 31, (palq >> 5) & 31, (palq >> 10) & 31], -1)
    d = np.arange(n_vals)
    cand = np.arange(32)
    shown = 2 * ((cand[:, None] * step * d[None, :]) >> 6)
    codes = np.array(palq, np.int64)
    errs, worst = [], 0.0
    for j in range(1, len(palq)):
        c = int(palq[j])
        if not c & 0x7FFF:
            continue
        ch = []
        for k in range(3):
            tgt = 8.0 * e[j, k] * d / 64.0 * gain
            se = ((shown - tgt[None, :]) ** 2).mean(1)
            b = int(se.argmin())
            ch.append(b)
            errs.append(float(se[b]))
            worst = max(worst, float(np.abs(shown[b] - tgt).max()))
        codes[j] = ch[0] | (ch[1] << 5) | (ch[2] << 10) | (c & 0x8000)
    return codes, float(np.sqrt(np.mean(errs))), worst


SPLIT_MAX_ERR = 12.0     # mean |Cosmos 1x - paletted cell| per channel


def _split_check(base, vc, pm, palq, k):
    """Every HD Cosmos cell, box-filtered to 16x16, must be the picture
    its paletted cell shows (palette-decoded, key texels as 0): a moved or
    foreign cell differs by far more than requantisation does. Returns
    (mean, worst) error; raises past SPLIT_MAX_ERR."""
    rgb = np.stack([palq & 31, (palq >> 5) & 31, (palq >> 10) & 31],
                   -1).astype(np.float32) * 8.0
    errs = []
    for cell, img in base.items():
        slot, cx, cy = cell
        a = np.frombuffer(pm[slot].data, np.uint8, count=65536).reshape(
            256, 256)[cy * 16:(cy + 1) * 16, cx * 16:(cx + 1) * 16]
        shown = np.where(a[..., None] == 0, 0.0, rgb[a])
        small = img.reshape(16, k // 16, 16, k // 16, 3).mean((1, 3))
        errs.append(float(np.abs(small - shown).mean()))
    worst = max(errs)
    if worst > SPLIT_MAX_ERR:
        raise ValueError('split: a cell is not its Cosmos art (error %.1f)'
                         % worst)
    return round(float(np.mean(errs)), 2), round(worst, 2)


def _emulate_ab(spec, pal5, idx_cells):
    """{(a, b or ('x', k)): (ncell, 16, 16, 3)} 1x renders of every state."""
    sa, ma, na_ = spec['win_a']
    sb, mb, nb_ = spec['win_b']

    def frame(va, vb):
        d = pal5.copy()
        for st, m, n in ((va, ma, na_), (vb, mb, nb_)):
            for i in range(n):
                j = st + i
                if j < len(d):
                    d[j] = np.minimum(31, (pal5[j] * m) // 64)
        return (d[idx_cells] * 8).astype(np.float32)

    out = {}
    for a in range(spec['na']):
        for b in range(spec['nb']):
            out[(a, b)] = frame(sa * a, sb * b)
    k = 0
    vb = spec['b_first']
    while vb < sb * spec['nb']:
        out[('x', k)] = frame((sa * k) % (sa * spec['na']), vb)
        k += 1
        vb += sb
    return out


def _ab(art, name, spec, vcell, built_idx, pal5, px):
    """(base, A[na], B[nb]) per vanilla cell, each (k, k, 3), and the fit."""
    cells = sorted(set(vcell.values()))
    pages = {c[0] for c in cells}
    if len(pages) != 1:
        raise ValueError('ab effect spans pages %s' % sorted(pages))
    vpage = pages.pop()
    recs = _frames(art, name, vpage, spec['palette'])
    nst = spec['na'] * spec['nb']
    if len(recs) < nst:
        raise ValueError('%d Cosmos frames for %d states' % (len(recs), nst))
    xy = [c[1:] for c in cells]
    k = px // GRID
    fr = np.stack([_cells_of(art.provider, r, xy, px) for r in recs])
    small = fr.reshape(len(recs), len(xy), GRID, k // GRID, GRID, k // GRID,
                       3).mean((3, 5))
    idx = np.stack([built_idx[c] for c in cells])
    emu = _emulate_ab(spec, pal5, idx)
    keys = list(emu)
    E = np.stack([emu[kk] for kk in keys])
    d = ((small[:, None] - E[None]) ** 2).mean((2, 3, 4, 5))
    best = d.argmin(1)
    lab = [keys[i] for i in best]
    grid = [(i, kk) for i, kk in enumerate(lab) if kk[0] != 'x']
    seen = {kk for _i, kk in grid}
    if len(seen) < 0.9 * nst:
        raise ValueError('only %d of %d states matched' % (len(seen), nst))
    rows = len(grid)
    M = np.zeros((rows, 1 + spec['na'] + spec['nb']), np.float32)
    for r, (_i, (a, b)) in enumerate(grid):
        M[r, 0] = 1
        M[r, 1 + a] = 1
        M[r, 1 + spec['na'] + b] = 1
    X = fr[[i for i, _k in grid]].reshape(rows, -1)
    coef = np.linalg.lstsq(M, X, rcond=None)[0]
    A = coef[1:1 + spec['na']]
    B = coef[1 + spec['na']:]
    base = coef[0] + A.min(0) + B.min(0)
    A = A - A.min(0)
    B = B - B.min(0)
    pred = np.clip(base, 0, None)[None] + A[[a for _i, (a, b) in grid]] \
        + B[[b for _i, (a, b) in grid]]
    err = float(np.abs(pred - X).mean())
    var = float(np.abs(X - X.mean(0)).mean())
    shp = (len(cells), k, k, 3)
    base = np.clip(base, 0, None).reshape(shp)
    A = A.reshape((spec['na'],) + shp)
    B = B.reshape((spec['nb'],) + shp)
    out = {}
    for j, c in enumerate(cells):
        out[c] = (base[j], [A[a][j] for a in range(spec['na'])],
                  [B[b][j] for b in range(spec['nb'])])
    return out, {'frames': len(recs), 'states': len(seen),
                 'err': round(err, 2), 'motion': round(var, 2)}


# ------------------------------------------------------------------- pages
def _screen_noise(xs, ys):
    """field_bg_repack's dither hash (uniform -0.5..0.5) at texel (x, y)."""
    x = np.asarray(xs, np.uint32)
    y = np.asarray(ys, np.uint32)
    h = (x * np.uint32(374761393)) + (y * np.uint32(668265263))
    h = (h ^ (h >> np.uint32(13))) * np.uint32(1274126177)
    h = h ^ (h >> np.uint32(16))
    return (h & np.uint32(0xFFFF)).astype(np.float32) / 65536.0 - 0.5


def _encode_cells(cells, px, origins=None):
    """{slot-local n: (k,k,3) premultiplied} -> 565 page (uint16 array).

    BUILD 620j. `origins` {n: (dst x, dst y)}: the 565 dither is keyed to
    the texel's SCREEN position (the record's destination) instead of its
    place on the page. Every level of one record then gets the same noise,
    so a texel that brightens never dips by a dither step between levels.
    Keyed by page position (before), each level's copy -- stored at a
    different page cell -- got different noise, and 6 % of blin68_2's glow
    texels dimmed by up to a step on every level change: a fine shimmer
    over the pulse (hardware 10-07: "flickering a bit")."""
    import field_bg_repack as FR
    if origins is None:
        img = np.zeros((px, px, 4), np.uint8)
        k = px // GRID
        for n, c in cells.items():
            nx, ny = n % GRID, n // GRID
            img[ny * k:(ny + 1) * k, nx * k:(nx + 1) * k, :3] = np.clip(
                np.rint(c), 0, 255).astype(np.uint8)
        img[..., 3] = 255
        buf = FR.rgba_to_565_buf(img.tobytes(), px * px, width=px,
                                 black_ok=True)
        return np.frombuffer(buf, '<u2').reshape(px, px)
    k = px // GRID
    out = np.zeros((px, px), np.uint16)
    amp = FR.DITHER_AMPLITUDE if FR.dither_on() else 0.0
    ly, lx = np.mgrid[0:k, 0:k]
    for n, c in cells.items():
        nx, ny = n % GRID, n // GRID
        c8 = np.clip(np.rint(c), 0, 255).astype(np.float32)
        if amp > 0.0:
            dx, dy = origins[n]
            d = _screen_noise((dx + 4096) * k // 16 + lx,
                              (dy + 4096) * k // 16 + ly) * np.float32(amp)
        else:
            d = np.float32(0.0)

        def q(ch):
            return np.clip(np.floor(ch * 0.125 + d + 0.5), 0, 31).astype(
                np.uint16)
        out[ny * k:(ny + 1) * k, nx * k:(nx + 1) * k] = (
            (q(c8[..., 0]) << 11) | ((q(c8[..., 1]) << 1) << 5)
            | q(c8[..., 2]))
    return out


def plan_field(name, parts, vparts, art, mb_cap=35.0, raw_cap=None,
               specs=None, sec0=None):
    """(new section 0, new section 9, info) -- raises on any doubt.

    `specs`/`sec0` (BUILD 618x, ff7nx_palauto): an automatically derived
    effect list (each 'levels' spec carries its own state-ordered 'art') and
    the already patched script."""
    import diag_common as DC
    import field_bg_native as FN
    import field_bg_pagecap as PC
    import field_bg_repack as FR
    import ff7nx_marginblack as MB
    if sec0 is None:
        sec0 = patch_script(name, parts[0])
    sec9 = parts[8]
    pages_l, _ts, _te, px = DC.parse_pages(sec9)
    if px != 768:
        raise ValueError('page size %d' % px)
    pm = {p.slot: p for p in pages_l if p is not None}
    cols, _hdr, npg, cpp = MB.palette_colours(parts[3])
    pal = cols.astype(np.int64).reshape(npg, cpp)
    k = px // GRID
    new_recs = []                 # (layer, record template, param, bit, img)
    gone = set()                  # (layer, index)
    reblend = {}                  # (layer, index) -> blend mode (split)
    sec3 = None
    info = {'effects': []}
    for spec in (specs if specs is not None else SPECS[name]):
        q = spec['palette']
        tg = _targets(sec9, pm, q)
        if spec['mode'] == 'split':
            # cells as they sit now -- fxrequant (the pass before) drew
            # them from Cosmos's sheet at these very places; _split_check
            # proves nothing moved them since
            vc = {r: (r[34],) + tuple(v // 16 for v in
                                      struct.unpack_from('<hh', r, 14))
                  for _l, _i, r in tg}
        else:
            vc = _vanilla_cells(tg, vparts[8])
        if spec['mode'] == 'split':
            base = _static_art(art, name, q, vc, px)
            info['split_check'] = _split_check(base, vc, pm, pal[q], k)
            f = spec['base_v'] / 64.0
            hwm = spec.get('hw_match')

            def _base_of(img):
                # the trough stays 620j's (not reported too bright or dim)
                return np.maximum(img * f - TRUNC_OFFSET, 0.0)
            soft = {}
            if spec.get('soften'):
                soft = _soften_split(base, vc, tg, k, spec['soften'])
            for layer, i, r in tg:
                b_ = _base_of(soft.get(r, base[vc[r]]))
                new_recs.append((layer, r, 0, 0, b_))
                reblend[(layer, i)] = spec['delta_blend']
            sec3 = bytearray(parts[3] if sec3 is None else sec3)
            built = {c: _base_of(img) for c, img in base.items()}
            if hwm:
                depth = 1.0
                codes, rms, worst = _pulse_entries_hw(
                    pal[q], spec['pulse_step'], spec['pulse'][0],
                    len(spec['pulse']))
            else:
                depth = _pulse_depth(tg, pm, pal[q], built, vc, k,
                                     max(spec['pulse']))
                codes, rms, worst = _pulse_entries(
                    pal[q], spec['pulse_step'], spec['pulse'][0],
                    len(spec['pulse']), depth)
            info['split_depth'] = round(depth, 3)
            # through the port's transfer the first values of a faint
            # entry cannot light at all (c = 1 shows 0), hence the wider
            # bound there
            if worst > (6.0 if hwm else 3.0):
                raise ValueError('split: pulse entries off by %.1f' % worst)
            emax = max(int(((codes[1:] >> sh) & 31).max())
                       for sh in (0, 5, 10))
            if emax * spec['pulse_step'] * (len(spec['pulse']) - 1) >> 6 > 31:
                raise ValueError('split: pulse multiply overflows 5 bits')
            if name == 'ghotel' and spec['pulse_step'] != GHOTEL_STEP:
                raise ValueError('split: spec step != script step')
            for j in range(1, cpp):
                struct.pack_into('<H', sec3, _hdr + 2 * (q * cpp + j),
                                 int(codes[j]))
            info['split_pulse_err'] = (round(rms, 2), round(worst, 2))
            info['effects'].append(
                'palette %d: %d records split into a static truecolor base '
                '(v %d/64) + the paletted pulse in blend %d (step %d)'
                % (q, len(tg), spec['base_v'], spec['delta_blend'],
                   spec['pulse_step']))
            info['split'] = len(tg)
            continue
        if spec['mode'] == 'levels':
            lv = spec.get('art')
            if lv is None and ('pick' in spec or spec.get('even_light')):
                # BUILD 620f: N levels chosen from a longer Cosmos ladder
                ck = (name, q, spec['cosmos_frames'], id(art),
                      tuple(sorted(set(vc.values()))))
                full = _LV_CACHE.get(ck)
                if full is None:
                    full = _levels(art, name, q, vc, spec['cosmos_frames'],
                                   px)
                    _LV_CACHE.clear()
                    _LV_CACHE[ck] = full
                if spec.get('even_light'):
                    ek = ('even', ck, len(spec['states']),
                          spec.get('tone_gamma', 1.0),
                          spec.get('even_frames'), len(tg))
                    lv = _MATCH_CACHE.get(ek)
                    if lv is None:
                        lv = _even_levels(full, tg, vc, len(spec['states']),
                                          spec.get('tone_gamma', 1.0),
                                          spec.get('even_frames'))
                        _MATCH_CACHE.clear()
                        _MATCH_CACHE[ek] = lv
                    info['even_light'] = True
                else:
                    lv = {c: [v[i] for i in spec['pick']]
                          for c, v in full.items()}
            if lv is None:
                lv = _levels(art, name, q, vc, len(spec['states']), px)
            if spec.get('match_1x') and not disabled_match():
                mk = ('match', name, q, id(art), tuple(spec['match_1x']),
                      tuple(sorted(set(vc.values()))), len(tg))
                hit = _MATCH_CACHE.get(mk)
                if hit is None:
                    hit = _match_1x(lv, tg, vc, sec9, pm, pal, k,
                                    spec['match_1x'])
                    _MATCH_CACHE.clear()
                    _MATCH_CACHE[mk] = hit
                lv, gains = hit
                info['gains'] = gains
            n_anim = spec.get('animate_cells')
            if isinstance(n_anim, int):
                animated = set(_pulse_order(lv, tg, vc)[:n_anim])
            else:
                animated = None
            for layer, i, r in tg:
                gone.add((layer, i))
                if animated is not None and vc[r] not in animated:
                    imgs = lv[vc[r]]
                    new_recs.append((layer, r, 0, 0, imgs[len(imgs) // 2]))
                    continue
                for n_, (st, img) in enumerate(zip(spec['states'],
                                                   lv[vc[r]])):
                    if 'params' in spec:          # one parameter per state
                        new_recs.append((layer, r, spec['params'][n_], 1,
                                         img))
                    else:
                        new_recs.append((layer, r, spec['param'], 1 << st,
                                         img))
            info['effects'].append('palette %d: %d records x %d levels'
                                   % (q, len(tg), len(spec['states'])))
            if animated is not None:
                info['animated_cells'] = len(animated)
                info['static_cells'] = len(set(vc.values())) - len(animated)
        else:
            p5 = np.stack([pal[q] & 31, (pal[q] >> 5) & 31,
                           (pal[q] >> 10) & 31], -1)
            built = {}
            for _l, _i, r in tg:
                sx, sy = struct.unpack_from('<hh', r, 14)
                a = np.frombuffer(pm[r[34]].data, np.uint8,
                                  count=65536).reshape(256, 256)
                built[vc[r]] = a[sy:sy + 16, sx:sx + 16]
            ab, fit = _ab(art, name, spec, vc, built, p5, px)
            for layer, i, r in tg:
                gone.add((layer, i))
                base, A, B = ab[vc[r]]
                new_recs.append((layer, r, 0, 0, base))
                for a in range(spec['na']):
                    new_recs.append((layer, r, spec['pa'] + a, 1, A[a]))
                for b in range(spec['nb']):
                    new_recs.append((layer, r, spec['pb'] + b, 1, B[b]))
            info['effects'].append(
                'palette %d: %d records, %d+%d phases (Cosmos %d frames, %d '
                'states matched, fit error %.1f vs motion %.1f)'
                % (q, len(tg), spec['na'], spec['nb'], fit['frames'],
                   fit['states'], fit['err'], fit['motion']))
            info['fit'] = fit
    new_recs = [t for t in new_recs if float(t[4].max()) >= EMPTY]
    layers = {t[0] for t in new_recs} | {g[0] for g in gone}
    if len(layers) != 1:
        raise ValueError('effects span layers %s' % sorted(layers))
    layer = layers.pop()
    # ---- what stays: records, used cells, bindings
    rows = {row[0]: row for row in _layer_rows(sec9)}
    keep_all = []
    for lyr, _c, first, n in rows.values():
        for i in range(n):
            if (lyr, i) not in gone:
                keep_all.append(sec9[first + i * REC:first + (i + 1) * REC])
    used = collections.defaultdict(set)
    bind = collections.Counter()
    for r in keep_all:
        sx, sy = struct.unpack_from('<hh', r, 10)
        used[r[32]].add((sx // 16, sy // 16))
        if r[28]:
            fx = r[34]
            sx, sy = struct.unpack_from('<hh', r, 14)
            used[fx].add((sx // 16, sy // 16))
            bind[fx if fx in pm else r[32]] += 1
        else:
            bind[r[32]] += 1
    # ---- destination slots: existing truecolor FX pages, then emptied
    # paletted FX pages, then absent FX slots
    dests = []
    for s in range(FX_LO, FX_HI):
        p = pm.get(s)
        if p is not None and p.depth == 2 and not p.size_flag:
            dests.append((s, 'old'))
    for s in range(FX_LO, FX_HI):
        p = pm.get(s)
        if p is not None and p.depth == 1 and not used[s]:
            dests.append((s, 'reuse'))
    for s in range(FX_LO, FX_HI):
        if s not in pm:
            dests.append((s, 'new'))
    place = {}                  # index into new_recs -> (slot, n)
    page_cells = collections.defaultdict(dict)
    page_orig = collections.defaultdict(dict)
    todo = list(range(len(new_recs)))
    kinds = {}
    for s, kind in dests:
        if not todo:
            break
        free = [n for n in range(GRID * GRID)
                if (n % GRID, n // GRID) not in used[s]]
        room = min(len(free), MAX_BIND - bind[s])
        if room <= 0:
            continue
        kinds[s] = kind
        for n in free[:room]:
            if not todo:
                break
            j = todo.pop(0)
            place[j] = (s, n)
            page_cells[s][n] = new_recs[j][4]
            page_orig[s][n] = struct.unpack_from('<hh', new_recs[j][1], 2)
            bind[s] += 1
    if todo:
        raise ValueError('%d cells do not fit the FX band' % len(todo))
    # ---- records
    lyr, count_at, first, count = rows[layer]
    keep = []
    for i in range(count):
        if (layer, i) in gone:
            continue
        r = bytearray(sec9[first + i * REC:first + (i + 1) * REC])
        if (layer, i) in reblend:
            r[30] = reblend[(layer, i)]
        keep.append(bytes(r))
    if any(lk[0] != layer for lk in reblend):
        raise ValueError('split records span layers')
    added = []
    for j, (_l, r, param, bit, _img) in enumerate(new_recs):
        s, n = place[j]
        nx, ny = n % GRID, n // GRID
        b = bytearray(r)
        b[26], b[27] = param, bit
        b[28], b[30], b[34] = 1, 1, s
        struct.pack_into('<hh', b, 14, nx * 16, ny * 16)
        struct.pack_into('<II', b, 42, nx * UV_CELL, ny * UV_CELL)
        added.append(bytes(b))
    buf = bytearray(sec9)
    buf[first:first + count * REC] = b''.join(keep + added)
    struct.pack_into('<H', buf, count_at, len(keep) + len(added))
    plist, t0, t1 = FN.parse_texture_block(bytes(buf), px)
    for s, cells in page_cells.items():
        enc = _encode_cells(cells, px, page_orig[s])
        if kinds[s] == 'old':
            cur = np.frombuffer(plist[s].data, '<u2').reshape(px, px).copy()
            for n in cells:
                nx, ny = n % GRID, n // GRID
                sl = (slice(ny * k, (ny + 1) * k), slice(nx * k, (nx + 1) * k))
                cur[sl] = enc[sl]
            enc = cur
        plist[s] = FN.Page(s, 0, 2, enc.astype('<u2').tobytes(), px)
    # paletted FX pages nothing reads any more
    dropped = []
    for s in range(FX_LO, FX_HI):
        p = plist[s] if s < len(plist) else None
        if p is not None and p.depth == 1 and not used[s] and s not in kinds:
            plist[s] = None
            dropped.append(s)
    run = sum(FR._page_bytes(p.px, p.depth) for p in plist if p is not None)
    if run > mb_cap * 1048576.0:
        raise ValueError('%.2f MB over the %.1f MB cap'
                         % (run / 1048576.0, mb_cap))
    out = FN.replace_texture_block(bytes(buf), plist, t0, t1)
    other = sum(len(x) for x in parts) - len(sec9)
    if raw_cap is not None and other + len(out) > raw_cap:
        raise ValueError('raw %d over cap %d' % (other + len(out), raw_cap))
    over = {s: v for s, v in PC.effective_counts(out, px).items()
            if v > MAX_BIND}
    if over:
        raise ValueError('binding cap: %s' % over)
    info.update({'removed': len(gone), 'added': len(added),
                 'slots': {s: (kinds[s], len(c)) for s, c in
                           sorted(page_cells.items())},
                 'dropped': dropped, 'mb': round(run / 1048576.0, 2)})
    if sec3 is not None:
        info['sec3'] = bytes(sec3)
        MB.palette_colours(info['sec3'])
    return sec0, out, info


# BUILD 620k. ghotel (hardware 10-07: "an abrupt discontinuity in the fog
# effect"). The tombstone animation (param 2, seven states) exists only in
# the 4:3 picture; the 16:9 margin beside it carries the static ground. In
# state 1 (the resting graves) the 1997 frame differs from the ground under
# the leftmost column (the bone at y 168..200 is up to 55/255 darker), so
# the picture stepped at x = -160 where the margin starts. Each state record
# in a 4:3 edge column that has a margin beside it is faded, texel by texel,
# into the ground record under it across that one column (smoothstep, 0 at
# the margin's edge): the margin meets plain ground, the animation keeps its
# full look one column in. Only cells no other record reads are written.
EDGE_FEATHER = {'ghotel': {'param': 2, 'edges': (-160, 144)}}
FEATHER_OFF_ENV = 'SEVENTH_NX_NO_EDGE_FEATHER'


def _page_offsets(sec9):
    import diag_common as DC
    import field_bg_native as FN
    _pages, _ts, _te, px = DC.parse_pages(sec9)
    out = {}
    o = sec9.find(b'TEXTURE') + 7
    for slot in range(FN.BG_MAX_PAGES):
        present, = struct.unpack_from('<H', sec9, o)
        o += 2
        if not present:
            continue
        size_flag, depth = struct.unpack_from('<HH', sec9, o)
        o += 4
        side = px if depth == 2 else FN.D1_PAGE_PX
        out[slot] = (o, side, depth, size_flag)
        o += FN.stored_bytes(side, depth)
    return out, px


def edge_feather(name, sec9):
    """(new section 9, cells faded) -- refuses nothing; cells it cannot
    prove safe are left as they are."""
    cfg = EDGE_FEATHER.get(name)
    if cfg is None or os.environ.get(FEATHER_OFF_ENV, '').strip().lower() \
            in ('1', 'true', 'yes', 'on'):
        return sec9, 0
    import diag_common as DC
    s9 = bytearray(sec9)
    offs, px = _page_offsets(sec9)
    _pl, ts, _te, _px = DC.parse_pages(sec9)
    recs = []
    for layer, lo in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in lo:
            recs.append((layer, o))

    def cell(o):
        bl, fx = sec9[o + 28], sec9[o + 34]
        slot = fx if (bl and fx) else sec9[o + 32]
        pg = offs.get(slot)
        if pg is None or pg[2] != 2 or pg[3]:
            return None
        u, v = struct.unpack_from('<II', sec9, o + 42)
        cx, cy = int(round(u / 10_000_000 * 16)), int(round(v / 10_000_000 * 16))
        return (slot, cx, cy)
    readers = collections.Counter()
    for _l, o in recs:
        c = cell(o)
        if c is not None:
            readers[c] += 1
    at = collections.defaultdict(list)
    for layer, o in recs:
        if layer != 2 or sec9[o + 28]:
            continue
        x, y = struct.unpack_from('<hh', sec9, o + 2)
        if max(struct.unpack_from('<HH', sec9, o + 18)) not in (0, 16):
            continue
        at[(x, y)].append(o)
    k = px // 16
    t = (np.arange(k) + 0.5) / k
    ramp = t * t * (3 - 2 * t)
    done = 0
    for (x, y), lst in at.items():
        if x not in cfg['edges']:
            continue
        side = -16 if x == min(cfg['edges']) else 16
        if not at.get((x + side, y)):
            continue                     # no margin beside this cell
        ground = [o for o in lst if sec9[o + 26] == 0]
        states = [o for o in lst if sec9[o + 26] == cfg['param']]
        if not ground or not states:
            continue
        g = max(ground, key=lambda o: struct.unpack_from('<h', sec9, o + 24)[0])
        gc = cell(g)
        if gc is None:
            continue
        w = ramp if side < 0 else ramp[::-1]
        gpo, gside = offs[gc[0]][0], offs[gc[0]][1]
        G = np.frombuffer(sec9, '<u2', gside * gside,
                          gpo).reshape(gside, gside)[gc[2] * k:(gc[2] + 1) * k,
                                                     gc[1] * k:(gc[1] + 1) * k]
        for o in states:
            c = cell(o)
            if c is None or readers[c] != 1 or c == gc:
                continue
            po, pside = offs[c[0]][0], offs[c[0]][1]
            A = np.frombuffer(bytes(s9[po:po + pside * pside * 2]), '<u2'
                              ).reshape(pside, pside)
            a = A[c[2] * k:(c[2] + 1) * k, c[1] * k:(c[1] + 1) * k].astype(
                np.int64)

            def rgb(v):
                return np.stack([(v >> 11) & 31, (v >> 5) & 63, v & 31],
                                -1).astype(np.float64)
            ga = G.astype(np.int64)
            mix = rgb(a) * w[None, :, None] + rgb(ga) * (1 - w[None, :, None])
            q = np.rint(mix).astype(np.int64)
            nv = (q[..., 0] << 11) | (q[..., 1] << 5) | q[..., 2]
            nv[(nv == 0) & (a != 0)] = 0x0020
            keep = (a == 0) | (ga == 0)
            nv[keep] = a[keep]
            for row in range(k):
                ro = po + ((c[2] * k + row) * pside + c[1] * k) * 2
                s9[ro:ro + 2 * k] = nv[row].astype('<u2').tobytes()
            done += 1
    return bytes(s9), done


def apply_to_flevel(archive, payloads, art, encode=None, mb_cap=35.0,
                    raw_cap=None, log=lambda *_: None):
    import lgp
    total = {'names': [], 'refused': []}
    if disabled() or art is None or getattr(art, 'provider', None) is None:
        return total
    encode = encode or archive.encode_field
    want = only()
    for name in SPECS:
        if want and name not in want:
            continue
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            van = archive.decompressed(entry)
            raw = lgp.lzs_decompress(p[4:]) if p else van
            parts = list(lgp.split_sections(raw))
            vparts = list(lgp.split_sections(van))
            parts[0], parts[8], info = plan_field_auto(name, parts, vparts,
                                                       art, mb_cap, raw_cap)
            if info.get('sec3') is not None:
                parts[3] = info['sec3']
            parts[8], nf = edge_feather(name, parts[8])
            if nf:
                info['effects'].append('%d tombstone-state cells faded into '
                                       'the ground at the 16:9 seam' % nf)
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append((name, str(exc)[:100]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['names'].append('%s: %s; %d records -> %d, pages %s, dropped '
                              'paletted %s, %.1f MB'
                              % (name, '; '.join(info['effects']),
                                 info['removed'], info['added'],
                                 info['slots'], info['dropped'], info['mb']))
    return total


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  PALETTE STATES (BUILD 618w): palette-animated effects '
                   'made truecolor, animation kept as tile states (%s). %s=1 '
                   'disables.' % (' | '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! palette states %s: %s' % (name, why))
    return '\n'.join(out)
