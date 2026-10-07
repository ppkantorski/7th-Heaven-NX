#!/usr/bin/env python3
"""ff7nx_fxrestore.py -- put back effect cells the pipeline lost. BUILD 618y.

whitein, hardware 10-04: "its light layers are missing squares all over the
place". The searchlight beams are one effect (param 1, states 1/2/4/8 on
palettes 9..12, paletted FX pages). Rendered per state against vanilla, the
build drew whole 16x16 squares of every beam black.

CAUSE (whitein): field_bg_compact may move an fx cell to another page at the
same coordinate. Passes after it that look Cosmos art up by the page SLOT a
record now names then read the wrong sheet: built page 15 is blank exactly
where Cosmos's page-15 sheet is blank, at the cells that came from page 16.

THE SCALE: across the 618x build, 1051 FX records in 118 fields draw nothing
where their vanilla twin drew light. Some of that is Cosmos's own choice (its
sheet is empty there too: mtnvl6, sininb41, junone5 -- FFNx draws nothing
there as well) and is left alone. The rest is loss: games_2 149, whitein 56,
snow 50, mds6_22 46, white1 42, ...

THE REPAIR (last texture pass; per FX record, all must hold):
  * the record's cell draws nothing, or less than DIM_RATIO of Cosmos's
    light for it (a cell holding another page's art);
  * its vanilla twin (same layer, param, state, destination, depth,
    palette) drew at least LIT texels of light;
  * Cosmos's sheet for the twin's page and palette (the one FFNx loads)
    paints that cell (at least LIT texels with alpha);
  * no other record shares the built cell, unless it is lost the same way.
The cell is then filled from Cosmos's cell: on a paletted page quantised
into the record's own palette (index 0 where the light is under 4/255), on a
truecolor page with the additive encoding. Nothing else changes: no record,
page, slot or palette.

SEVENTH_NX_NO_FX_RESTORE=1 disables.
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_FX_RESTORE'
LIT = 24
DARK = 4.0
MAX_ERR = 28.0
DIM_RATIO = 0.4          # restore when the cell draws < 40% of Cosmos
ANIM_RATIO = 0.6         # palette-animated art: restore under 60% of vanilla
HOT_RATIO, HOT_ADD = 1.8, 8.0   # ... or over 1.8x vanilla + 8
# The vanilla-copy path for palette-animated art is SCOPED. A dry run over
# build 386 had it fire in 50 fields (uutai1 758 cells, las0_8, semkin_8,
# mtnvl2, ...); rendered, those fields' animated FX (water, glows) are
# fine in the build and the copy pasted blown-out 1997 squares into them --
# their initial-palette brightness is not comparable with vanilla's.
# whitein's searchlight beams are the case verified cell by cell.
ANIM_FIELDS = ('whitein',)
ANIM_ALL = True          # in ANIM_FIELDS, every lit twinned cell (618z)
UV_CELL = 625000
FX_SLOTS = (15, 24)


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _key(s9, o):
    return (s9[o + 26], s9[o + 27]) + struct.unpack_from('<hh', s9, o + 2) \
        + (struct.unpack_from('<I', s9, o + 38)[0], s9[o + 22])


def _fx_records(s9):
    import diag_common as DC
    pl, ts, _te, px = DC.parse_pages(s9)
    out = []
    for layer, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts):
        for o in offs:
            if s9[o + 28]:
                out.append((layer, o))
    return out, {p.slot: p for p in pl if p is not None}, px


def _cell(s9, o, page, px):
    """(cx, cy, k): the record's cell on its fx page."""
    if page.depth == 1:
        sx, sy = struct.unpack_from('<hh', s9, o + 14)
        return sx // 16, sy // 16, 16
    u, v = struct.unpack_from('<II', s9, o + 42)
    return u // UV_CELL, v // UV_CELL, page.px // 16


def _bright(page, cx, cy, k, pal_row):
    """Mean drawn brightness (0..255) of the cell."""
    if page.depth == 1:
        a = np.frombuffer(page.data, np.uint8, count=65536).reshape(
            256, 256)[cy * 16:(cy + 1) * 16, cx * 16:(cx + 1) * 16]
        v = pal_row[a]
        rgb = np.stack([v & 31, (v >> 5) & 31, (v >> 10) & 31], -1) * 8
        return float(rgb.mean())
    a = np.frombuffer(page.data, '<u2').reshape(page.px, page.px)[
        cy * k:(cy + 1) * k, cx * k:(cx + 1) * k].astype(np.uint32)
    rgb = np.stack([(a >> 11) & 31, ((a >> 6) & 31), a & 31], -1) * 8
    return float(rgb.mean())


def _lit(page, cx, cy, k, pal_row):
    if page.depth == 1:
        a = np.frombuffer(page.data, np.uint8, count=65536).reshape(
            256, 256)[cy * 16:(cy + 1) * 16, cx * 16:(cx + 1) * 16]
        return int(((pal_row[a] & 0x7FFF) != 0).sum())
    a = np.frombuffer(page.data, '<u2').reshape(page.px, page.px)[
        cy * k:(cy + 1) * k, cx * k:(cx + 1) * k]
    return int((a != 0).sum() * 256 // (k * k))


def plan_field(name, parts, vparts, art):
    """(new section 9, info) or raises."""
    import field_bg_native as FN
    import ff7nx_fxmargin as FXM
    import ff7nx_fxpages as FP
    import ff7nx_marginart as MA
    import ff7nx_marginblack as MB
    s9 = parts[8]
    recs, pm, px = _fx_records(s9)
    cols, _h, npg, cpp = MB.palette_colours(parts[3])
    pal = cols.astype(np.int64).reshape(npg, cpp)
    vrecs, vpm, _vpx = _fx_records(vparts[8])
    vcols, _vh, vnpg, vcpp = MB.palette_colours(vparts[3])
    vpal = vcols.astype(np.int64).reshape(vnpg, vcpp)
    vs9 = vparts[8]
    vmap = collections.defaultdict(list)
    for layer, o in vrecs:
        vmap[(layer,) + _key(vs9, o)].append(o)
    users = collections.defaultdict(list)
    for layer, o in recs:
        p = pm.get(s9[o + 34])
        if p is None:
            continue
        cx, cy, k = _cell(s9, o, p, px)
        users[(p.slot, cx, cy)].append((layer, o))
    prov = art.provider
    amb = getattr(prov, 'ambiguous_slots', ()) or ()

    def animated(vslot, q):
        if name.lower() not in ANIM_FIELDS:
            return False
        sel = FP._selected_palette(prov, name, vslot, q)
        return sel is not None and (name.lower(), vslot, sel) in amb

    lost = {}
    for cellkey, us in users.items():
        p = pm[cellkey[0]]
        cx, cy = cellkey[1], cellkey[2]
        k = 16 if p.depth == 1 else p.px // 16
        twins = []
        for layer, o in us:
            q = s9[o + 22]
            if q >= npg:
                twins = None
                break
            vo = vmap.get((layer,) + _key(s9, o))
            if not vo or len(vo) != 1:
                twins = None
                break
            vo = vo[0]
            vp = vpm.get(vs9[vo + 34])
            if vp is None or vp.depth != 1 or q >= vnpg:
                twins = None
                break
            vsx, vsy = struct.unpack_from('<hh', vs9, vo + 14)
            if _lit(vp, vsx // 16, vsy // 16, 16, vpal[q]) < LIT:
                twins = None
                break
            twins.append((o, vp.slot, vsx, vsy, q))
        if twins:
            o0, vslot0, vsx0, vsy0, q0 = twins[0]
            have = _bright(p, cx, cy, k, pal[q0])
            want = _bright(vpm[vslot0], vsx0 // 16, vsy0 // 16, 16, vpal[q0])
            anim0 = p.depth == 1 and animated(vslot0, q0)
            ratio = ANIM_RATIO if anim0 else DIM_RATIO
            if have <= ratio * want:            # cheap pre-check vs vanilla
                lost[cellkey] = (twins, have)
            elif anim0 and ANIM_ALL:
                # every lit beam cell from vanilla: a beam mixing vanilla
                # cells with Cosmos-frame cells (60-95% as bright) reads as
                # a staircase of squares
                lost[cellkey] = (twins, have)
            elif anim0 and have >= HOT_RATIO * want + HOT_ADD:
                # another state's frame, far brighter than this state's
                # light: whitein's lone bright square (176,-16), 106 vs 20
                lost[cellkey] = (twins, have)
    if not lost:
        raise ValueError('no lost effect cells')
    sheets = {}
    data = {s: (np.frombuffer(pm[s].data, np.uint8, count=65536).reshape(
        256, 256).copy() if pm[s].depth == 1 else
        np.frombuffer(pm[s].data, '<u2').reshape(pm[s].px, pm[s].px).copy())
        for s in {c[0] for c in lost}}
    done = skipped = anim = 0
    for (slot, cx, cy), (twins, have) in sorted(lost.items()):
        o, vslot, vsx, vsy, q = twins[0]
        if len({(t[1], t[2], t[3], t[4]) for t in twins}) != 1:
            skipped += 1
            continue
        sel = FP._selected_palette(prov, name, vslot, q)
        if sel is None:
            skipped += 1
            continue
        p = pm[slot]
        if p.depth == 1 and animated(vslot, q):
            # BUILD 618z (whitein's beams, hardware 10-04 build 386): the
            # palette is ANIMATED, so Cosmos ships one hash frame per palette
            # state and any single frame is the light at one moment -- the
            # frame the provider picks paints the state-2 beam at 13% of
            # vanilla. The runtime palette animates the INDICES, so the
            # vanilla cell is the truth here: copy it (remapped into the
            # record's built palette when that differs).
            va = np.frombuffer(vpm[vslot].data, np.uint8, count=65536).reshape(
                256, 256)[vsy // 16 * 16:vsy // 16 * 16 + 16,
                          vsx // 16 * 16:vsx // 16 * 16 + 16]
            if np.array_equal(pal[q], vpal[q]):
                idx = va.copy()
            else:
                vc = vpal[q][va]
                vrgb = np.stack([vc & 31, (vc >> 5) & 31, (vc >> 10) & 31],
                                -1).astype(np.uint8) * 8
                prgb = MA.palette_rgb(pal[q].astype(np.uint16))
                idx = MA.quantise(vrgb, prgb).astype(np.uint8)
                clear = (vc & 0x7FFF) == 0
                idx[clear] = 0
                if (~clear).any() and float(np.abs(
                        prgb[idx].astype(np.float32)
                        - vrgb)[~clear].mean()) > MAX_ERR:
                    skipped += 1
                    continue
            data[slot][cy * 16:(cy + 1) * 16, cx * 16:(cx + 1) * 16] = idx
            done += 1
            anim += 1
            continue
        res = 256 if p.depth == 1 else p.px
        key = (vslot, sel, res)
        if key not in sheets:
            sheets[key] = FXM._provider_rgba(art, name, vslot, sel, res)
        img = sheets[key]
        if img is None:
            skipped += 1
            continue
        k = res // 16
        c = img[vsy // 16 * k:(vsy // 16 + 1) * k,
                vsx // 16 * k:(vsx // 16 + 1) * k].astype(np.float32)
        if int((c[..., 3] > 8).sum() * 256 // (k * k)) < LIT:
            skipped += 1                    # Cosmos leaves it empty too
            continue
        pmul = c[..., :3] * (c[..., 3:] / 255.0)
        if have > DIM_RATIO * float(pmul.mean()) and have > 0:
            continue                        # the cell draws its light
        if p.depth == 1:
            prgb = MA.palette_rgb(pal[q].astype(np.uint16))
            idx = MA.quantise(np.clip(pmul, 0, 255).astype(np.uint8), prgb)
            dark = pmul.max(-1) < DARK
            idx = np.where(dark, 0, idx).astype(np.uint8)
            lit = ~dark
            if lit.any():
                err = float(np.abs(prgb[idx].astype(np.float32)
                                   - pmul)[lit].mean())
                if err > MAX_ERR:
                    skipped += 1
                    continue
            data[slot][cy * 16:(cy + 1) * 16, cx * 16:(cx + 1) * 16] = idx
        else:
            rgba = np.zeros((k, k, 4), np.uint8)
            rgba[..., :3] = np.clip(np.rint(pmul), 0, 255).astype(np.uint8)
            rgba[..., 3] = 255
            import field_bg_repack as FR
            enc = np.frombuffer(FR.rgba_to_565_buf(rgba.tobytes(), k * k,
                                                   width=k, black_ok=True),
                                '<u2').reshape(k, k)
            data[slot][cy * k:(cy + 1) * k, cx * k:(cx + 1) * k] = enc
        done += 1
    if not done:
        raise ValueError('no lost effect cells (%d candidates)' % len(lost))
    plist, t0, t1 = FN.parse_texture_block(s9, px)
    for s, d in data.items():
        q = plist[s]
        raw = d.tobytes() if q.depth == 1 else d.astype('<u2').tobytes()
        plist[s] = FN.Page(s, q.size_flag, q.depth, raw, q.px)
    return FN.replace_texture_block(s9, plist, t0, t1), {
        'cells': done, 'skipped': skipped, 'lost': len(lost),
        'animated': anim}


def margin_palettes(name, parts, vparts):
    """BUILD 618z (whitein): the 16:9 margin records Cosmos added for an
    animated effect (no vanilla twin).

    whitein's 28 state-2 margin beam records carried palette 9 -- state 1's
    palette -- so the beam's margin end pulsed on state 1's timing, and
    their art is a cut of one Cosmos hash frame: dimmer blocks with a step
    at the 4:3 edge. Each such record now takes the palette every twinned
    record of its (param, state) uses, and its cell is the beam CONTINUED:
    a searchlight beam is a straight band, so each margin texel takes the
    index of the last in-picture texel on the line through it along the
    beam's own direction (principal axis of the state's lit texels in the
    64 units next to the margin). 618z+: where the beam's two edges fit
    lines, it is continued as the cone it is (each texel at its relative
    place across the beam), from the mean of the last 4 columns, fading at
    half the in-picture rate, re-dithered into the state's lit indices. The indices are the state's own, so the
    runtime palette animates the continuation with the beam. Records whose
    state shows no clear band fall back to re-quantising their old colours
    into the state's palette. Only exclusive cells; returns (section 9,
    records changed); raises if none."""
    import field_bg_native as FN
    import ff7nx_marginart as MA
    import ff7nx_marginblack as MB
    s9 = bytearray(parts[8])
    recs, pm, px = _fx_records(bytes(s9))
    cols, _h, npg, cpp = MB.palette_colours(parts[3])
    pal = cols.astype(np.int64).reshape(npg, cpp)
    vs9 = vparts[8]
    vrecs, _vpm, _vpx = _fx_records(vs9)
    vkeys = collections.Counter((layer,) + _key(vs9, o) for layer, o in vrecs)
    uses = collections.Counter()
    for layer, o in recs:
        p = pm.get(s9[o + 34])
        if p is not None:
            cx, cy, _k = _cell(s9, o, p, px)
            uses[(p.slot, cx, cy)] += 1
    by_state = collections.defaultdict(collections.Counter)
    twinned = collections.defaultdict(list)
    orphans = collections.defaultdict(list)
    for layer, o in recs:
        if not s9[o + 26]:
            continue
        st = (s9[o + 26], s9[o + 27])
        if (layer,) + _key(s9, o) in vkeys:
            by_state[st][s9[o + 22]] += 1
            twinned[st].append(o)
        else:
            orphans[st].append(o)
    data = {}

    def cell_of(o):
        p = pm.get(s9[o + 34])
        if p is None or p.depth != 1:
            return None
        cx, cy, _k = _cell(s9, o, p, px)
        if p.slot not in data:
            data[p.slot] = np.frombuffer(p.data, np.uint8, count=65536).reshape(
                256, 256).copy()
        return p.slot, cx, cy

    n = 0
    lines = {}
    for st, ors in orphans.items():
        want = by_state.get(st)
        if not want or len(want) != 1:
            continue
        q_new = next(iter(want))
        if q_new >= npg:
            continue
        # the state's in-picture beam, 1 texel per field unit
        pos = {}
        for o in twinned[st]:
            c = cell_of(o)
            if c is None:
                continue
            x, y = struct.unpack_from('<hh', s9, o + 2)
            pos[(x, y)] = data[c[0]][c[2] * 16:c[2] * 16 + 16,
                                     c[1] * 16:c[1] * 16 + 16]
        line = None
        oxs = [struct.unpack_from('<hh', s9, o + 2)[0] for o in ors]
        if pos and min(oxs) > max(x for x, _y in pos):
            xe = min(oxs)                                  # right margin
            xs_ = [x for x, _y in pos]
            ys_ = [y for _x, y in pos]
            X0, Y0 = min(xs_), min(ys_)
            W = max(xs_) + 16 - X0
            H = max(ys_) + 16 - Y0
            idx = np.zeros((H, W), np.uint8)
            cov = np.zeros((H, W), bool)
            for (x, y), a in pos.items():
                idx[y - Y0:y - Y0 + 16, x - X0:x - X0 + 16] = a
                cov[y - Y0:y - Y0 + 16, x - X0:x - X0 + 16] = True
            c = pal[q_new][idx]
            br = (np.stack([c & 31, (c >> 5) & 31, (c >> 10) & 31],
                           -1).mean(-1) * 8.0) * cov
            gx = np.arange(W) + X0
            strip = br * ((gx >= xe - 64) & (gx < xe))[None, :]
            yy, xx = np.nonzero(strip > 16)
            if len(yy) > 200:
                w_ = strip[yy, xx]
                mx = np.average(xx, weights=w_)
                my = np.average(yy, weights=w_)
                cov_m = np.cov(np.stack([xx - mx, yy - my]), aweights=w_)
                ev, evec = np.linalg.eigh(cov_m)
                v = evec[:, -1]
                if ev[-1] > 4 * max(ev[0], 1e-6) and abs(v[0]) > 0.2:
                    line = (v[1] / v[0], idx, cov, X0, Y0, xe)
                    # BUILD 618z+ (hardware 10-05: "going off in a different
                    # angle"): a searchlight beam is a CONE. Fit its two
                    # edges over the last 64 columns and continue each
                    # texel at its relative place across the beam, so the
                    # beam keeps widening at its own angle.
                    cols_, tops, bots = [], [], []
                    for c_ in range(max(0, xe - 64 - X0), xe - X0):
                        bcol = br[:, c_]
                        mxb = float(bcol.max())
                        if mxb < 16:
                            continue
                        rr = np.nonzero(bcol >= 0.25 * mxb)[0]
                        cols_.append(c_)
                        tops.append(rr.min())
                        bots.append(rr.max())
                    if len(cols_) >= 24:
                        a1, b1 = np.polyfit(cols_, tops, 1)
                        a2, b2 = np.polyfit(cols_, bots, 1)
                        if (a2 * (xe - X0) + b2) - (a1 * (xe - X0) + b1) > 4:
                            # hardware 10-05 (beam HD): the beam FADES along
                            # its axis (its cross-section sum drops ~5% a
                            # column near the 4:3 edge, vanilla too); a flat
                            # copy of the last column read as a tail with a
                            # step. The fade continues at the slope fitted
                            # over the same 64 columns (halved: a beam
                            # still crosses the screen edge, as in 4:3), so
                            # it dims smoothly instead of stopping.
                            Is = br.sum(0)[cols_]
                            s_, i_ = np.polyfit(cols_, Is, 1)
                            i_end = s_ * (xe - 1 - X0) + i_
                            fade = (float(-s_ / i_end)
                                    if s_ < 0 and i_end > 0 else 0.0)
                            lit_i = np.unique(idx[cov & (idx > 0)])
                            line = ('edges', float(a1), float(b1), float(a2),
                                    float(b2), idx, cov, X0, Y0, xe,
                                    0.5 * min(fade, 0.05),
                                    pal[q_new].copy(),
                                    lit_i)
                    lines[st] = (line, q_new, ors)
        for o in ors:
            c = cell_of(o)
            if c is None or uses[(c[0], c[1], c[2])] != 1:
                continue
            slot, cx, cy = c
            sl = (slice(cy * 16, cy * 16 + 16), slice(cx * 16, cx * 16 + 16))
            x, y = struct.unpack_from('<hh', s9, o + 2)
            if line is not None:
                data[slot][sl] = _extend(line, x, y)
            elif s9[o + 22] != q_new and s9[o + 22] < npg:
                a = data[slot][sl]
                cc = pal[s9[o + 22]][a]
                rgb = np.stack([cc & 31, (cc >> 5) & 31, (cc >> 10) & 31],
                               -1).astype(np.uint8) * 8
                ii = np.asarray(MA.quantise(
                    rgb, MA.palette_rgb(pal[q_new].astype(np.uint16))),
                    np.uint8)
                ii[(cc & 0x7FFF) == 0] = 0
                data[slot][sl] = ii
            else:
                continue
            s9[o + 22] = q_new
            n += 1
    # the band can leave the margin records' rows before it leaves the
    # screen (whitein's state-2 beam is cut at y = 96 near the right edge):
    # positions inside the margin the continued band lights get new records
    # (a copy of one of the state's margin records) on free cells of the
    # paletted FX page with most room
    add = []
    if lines:
        binds = collections.Counter()
        usedc = collections.defaultdict(set)
        import diag_common as DC
        _pl, ts_, _te, _px = DC.parse_pages(bytes(s9))
        for _layer, offs in DC.walk_layers(bytes(s9), bytes(s9).find(b'BACK'),
                                           ts_):
            for o in offs:
                for pg_, so in ((s9[o + 32], 10),) + (
                        ((s9[o + 34], 14),) if s9[o + 28] else ()):
                    sx, sy = struct.unpack_from('<hh', s9, o + so)
                    binds[pg_] += 1
                    usedc[pg_].add((sx // 16, sy // 16))
        have_xy = collections.defaultdict(set)
        for layer, o in recs:
            have_xy[(s9[o + 26], s9[o + 27])].add(
                struct.unpack_from('<hh', s9, o + 2))
        for st, (line, q_new, ors) in sorted(lines.items()):
            xe = line[9] if line[0] == 'edges' else line[5]
            tpl = ors[0]
            ys0 = [struct.unpack_from('<hh', s9, o + 2)[1] for o in ors]
            for x in range(xe, xe + 64, 16):
                for y in range(min(ys0) - 64, max(ys0) + 80, 16):
                    if (x, y) in have_xy[st]:
                        continue
                    out = _extend(line, x, y)
                    c = pal[q_new][out]
                    lit_ = (c & 0x7FFF) != 0
                    br = np.stack([c & 31, (c >> 5) & 31, (c >> 10) & 31],
                                  -1).mean(-1) * 8.0
                    if int((lit_ & (br > 4)).sum()) < 24:
                        if os.environ.get('FXR_DEBUG'):
                            print('skip', st, x, y, int(lit_.sum()))
                        continue
                    add.append((tpl, x, y, out))
        if os.environ.get('FXR_DEBUG'):
            print('lines', {k_: str(v_[0][0])[:6] for k_, v_ in lines.items()},
                  'add', [(a_[1], a_[2]) for a_ in add])
        if add:
            cands = sorted((s_ for s_, p_ in pm.items()
                            if p_.depth == 1 and FX_SLOTS[0] <= s_ < FX_SLOTS[1]
                            and binds[s_] + len(add) <= 256
                            and 256 - len(usedc[s_]) >= len(add)),
                           key=lambda s_: len(usedc[s_]))
            if not cands:
                add = []
            else:
                slot = cands[0]
                if slot not in data:
                    data[slot] = np.frombuffer(pm[slot].data, np.uint8,
                                               count=65536).reshape(
                        256, 256).copy()
                free = [(cx, cy) for cy in range(16) for cx in range(16)
                        if (cx, cy) not in usedc[slot]]
                import ff7nx_parallaxfill as PF
                sv = DC.survey(bytes(s9))
                rows_ = PF._layers(bytes(s9), sv['back_start'],
                                   sv['tex_start'])
                lay = None
                for r_ in rows_:
                    if r_[2] <= ors[0] < r_[2] + r_[3] * 52:
                        lay = r_
                if lay is None:
                    add = []
                else:
                    _l, count_at, first, count = lay
                    newb = []
                    for i, (tpl, x, y, out) in enumerate(add):
                        cx, cy = free[i]
                        data[slot][cy * 16:cy * 16 + 16,
                                   cx * 16:cx * 16 + 16] = out
                        r = bytearray(s9[tpl:tpl + 52])
                        struct.pack_into('<hh', r, 2, x, y)
                        struct.pack_into('<hh', r, 14, cx * 16, cy * 16)
                        struct.pack_into('<II', r, 42, cx * UV_CELL,
                                         cy * UV_CELL)
                        r[34] = slot
                        newb.append(bytes(r))
                    end = first + count * 52
                    s9[end:end] = b''.join(newb)
                    struct.pack_into('<H', s9, count_at, count + len(newb))
                    n += len(newb)
    if not n:
        raise ValueError('margin records already match')
    s9 = bytes(s9)
    plist, t0, t1 = FN.parse_texture_block(s9, px)
    for slot, d in data.items():
        q = plist[slot]
        plist[slot] = FN.Page(slot, q.size_flag, q.depth, d.tobytes(), q.px)
    return FN.replace_texture_block(s9, plist, t0, t1), n


_BAYER4 = (np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9],
                     [15, 7, 13, 5]], np.float64) + 0.5) / 16.0 - 0.5


def _extend(line, x, y):
    """One 16x16 cell at field (x, y) of the band continued past xe."""
    if line[0] == 'edges':
        (_t, a1, b1, a2, b2, idx, cov, X0, Y0, xe, fade, prow,
         lit_i) = line
        gy, gx = np.mgrid[0:16, 0:16]
        cx = (gx + x - X0).astype(np.float64)
        cy = (gy + y - Y0).astype(np.float64)
        top = a1 * cx + b1
        wid = np.maximum(a2 * cx + b2 - top, 1.0)
        u = (cy - top) / wid
        rgb = np.stack([prow & 31, (prow >> 5) & 31, (prow >> 10) & 31],
                       -1).astype(np.float64) * 8.0
        # source colour: the last 4 in-picture columns at the same place
        # across the beam (one column alone carries its dither / edge texel)
        acc = np.zeros((16, 16, 3))
        nacc = 0
        for cs in range(xe - 4 - X0, xe - X0):
            t0 = a1 * cs + b1
            w0 = a2 * cs + b2 - t0
            sy_ = np.rint(t0 + u * w0).astype(int)
            ok = (sy_ >= 0) & (sy_ < idx.shape[0])
            syc = np.clip(sy_, 0, idx.shape[0] - 1)
            v = idx[syc, cs]
            m = ok & cov[syc, cs]
            acc += rgb[v] * m[..., None]
            nacc += 1
        col = acc / max(nacc, 1)
        dx = cx - (xe - 1 - X0)
        col *= np.exp(-fade * np.maximum(dx, 0.0))[..., None]
        if lit_i is None or not len(lit_i):
            lit_i = np.arange(1, 128)
        # black (index 0) is a candidate too, so the dim end dithers out
        # instead of stopping at a ragged cut; distances are taken in a
        # luma/chroma space with chroma weighted x3, so dim texels keep the
        # beam's hue instead of snapping to a reddish/blue near-black entry
        ids = np.concatenate([[0], np.asarray(lit_i, np.int64)])
        cand = rgb[ids]
        cand[0] = 0.0

        def ycc(c):
            y_ = c @ np.array([0.299, 0.587, 0.114])
            return np.stack([y_, 3.0 * (c[..., 2] - y_) * 0.564,
                             3.0 * (c[..., 0] - y_) * 0.713], -1)
        tt = col + _BAYER4[gy % 4, gx % 4][..., None] * 8.0
        dd = ((ycc(tt)[:, :, None, :] - ycc(cand)[None, None]) ** 2).sum(-1)
        out = ids.astype(np.uint8)[dd.argmin(-1)]
        out[col.max(-1) < 1] = 0
        return out
    slope, idx, cov, X0, Y0, xe = line
    gy, gx = np.mgrid[0:16, 0:16]
    tx = gx + x - (xe - 1)                     # units past the edge
    sy_ = np.rint(gy + y - slope * tx).astype(int) - Y0
    sx_ = np.full_like(sy_, xe - 1 - X0)
    ok = (sy_ >= 0) & (sy_ < idx.shape[0])
    out = np.zeros((16, 16), np.uint8)
    out[ok] = idx[sy_[ok], sx_[ok]]
    out[ok & ~cov[np.clip(sy_, 0, idx.shape[0] - 1), sx_]] = 0
    return out

def _skip():
    """Fields whose effects a dedicated pass reworked on purpose, or that are
    confirmed on hardware: left exactly as built."""
    out = set()
    for mod, attr in (('ff7nx_fxpcstatic', 'FIELDS'),
                      ('ff7nx_palstates', 'SPECS'),
                      ('ff7nx_fxcomplete', 'CONFIRMED'),
                      ('ff7nx_fxtrial', 'FIELDS'),
                      ('ff7nx_fxsmooth', 'FIELDS')):
        try:
            out |= {str(x).lower() for x in getattr(__import__(mod), attr)}
        except Exception:                                      # noqa: BLE001
            pass
    return out


def apply_to_flevel(archive, payloads, art, encode=None, log=lambda *_: None):
    import lgp
    total = {'names': [], 'cells': 0, 'refused': 0}
    if disabled() or art is None or getattr(art, 'provider', None) is None:
        return total
    encode = encode or archive.encode_field
    skip = _skip()
    for name in archive.names():
        if name.lower() in skip:
            continue
        entry = archive.index.get(name)
        try:
            if entry is None or not archive.is_field(entry):
                continue
            p = payloads.get(name)
            if not p:
                continue                       # untouched: nothing was lost
            van = archive.decompressed(entry)
            raw = lgp.lzs_decompress(p[4:])
            parts = list(lgp.split_sections(raw))
            if parts[8].find(b'BACK') < 0:
                continue
            vparts = list(lgp.split_sections(van))
            st = None
            try:
                parts[8], st = plan_field(name, parts, vparts, art)
            except ValueError:
                if name.lower() not in ANIM_FIELDS:
                    raise
            if name.lower() in ANIM_FIELDS:
                try:
                    parts[8], nm = margin_palettes(name, parts, vparts)
                    total['margin'] = total.get('margin', 0) + nm
                    st = st or {'cells': 0}
                except ValueError:
                    pass
            if st is None:
                raise ValueError('nothing to do')
        except Exception:                                      # noqa: BLE001
            total['refused'] += 1
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['names'].append('%s:%d' % (name, st['cells']))
        total['cells'] += st['cells']
    return total


def summarise(st):
    if not st.get('names'):
        return ''
    return ('  FX RESTORE (BUILD 618y): %d lost effect cell(s) put back from '
            'Cosmos in %d field(s) (%s). %s=1 disables.'
            % (st['cells'], len(st['names']),
               ', '.join(st['names'][:30])
               + (' ...' if len(st['names']) > 30 else ''), OFF_ENV)
            + (' Margin records re-paletted to their state: %d.'
               % st['margin'] if st.get('margin') else ''))
