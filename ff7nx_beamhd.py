#!/usr/bin/env python3
"""ff7nx_beamhd.py -- whitein's searchlight beams from Cosmos's HD frames.
BUILD 618z.

whitein, hardware 10-05: "the light beams are extremely low quality ...
pixellated with lots of contour patches of color ... these beams are low fps
anyways, im confused why we cant safely cover more colors / fades".

WHAT THE BEAMS ARE. Four additive beams (param 1, states 1/2/4/8, all shown
at once) on 1x paletted FX pages, each on its own palette 9..12. The script
pulses each one by MULTIPLYING its palette (MPPAL, var/64, var 0..62 and
back, two frames a step) -- a 63-level fade on entries 0..127. A palette
pulse can only live on a paletted page, and the HD alternative (one
truecolor copy per brightness level) does not fit: 4 beams x ~580 lit cells
x even 8 levels is 18 pages, against the 2 free slots of the FX band.

WHY THEY LOOK BAD. 618z copied the 1997 cells (to get the missing squares
back) -- and the 1997 beam is a posterised ramp: blocky bands across the
beam. Cosmos's frames are smooth.

THE FIX (FIELDS only; exclusive cells; nothing but palettes 9..12 entries
0..127 and the beam cells change):
  * Cosmos ships ~60 hash frames per (page, beam palette) -- the beam at
    each pulse level. The BRIGHTEST is the beam at full strength; it is
    resampled to the 1x page and scaled to the 1997 beam's own brightness
    (sum over the cells both light), which is the palette's base level the
    script multiplies from.
  * each beam palette's 127 animated entries are rebuilt as a k-means ramp
    of that beam's own colours (entry 0 stays transparent black), and each
    cell is quantised into it with a 4x4 ordered dither of one 5-bit step,
    so the 15-bit palette's coarse steps read as a smooth gradient instead
    of bands.
  * the one static record that shares palette 11 is re-quantised into the
    new entries, so it keeps its look.
The pulse is untouched: the script still multiplies the same 128 entries.

SEVENTH_NX_NO_BEAM_HD=1 disables.
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_BEAM_HD'
FIELDS = {'whitein': (9, 10, 11, 12)}
ANIMATED = 128            # STPAL/LDPAL ... 7F: entries 0..127 pulse
BAYER = (np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9],
                   [15, 7, 13, 5]], np.float32) + 0.5) / 16.0 - 0.5


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _rgb15(c):
    c = np.asarray(c, np.int64)
    return np.stack([c & 31, (c >> 5) & 31, (c >> 10) & 31], -1).astype(
        np.float32) * 8.0


def _brightest(art, name, page, q):
    import dds_decode
    import field_bg_repack as FR
    prov = art.provider
    recs = prov.state_slots.get((name.lower(), page, q))
    if not recs:
        return None
    best = None
    for path, entry in recs:
        r = prov.readers.get(path)
        if r is None:
            r = prov.readers[path] = FR.IroReader(path)
        rgba, w, h = dds_decode.decode_dds(r.read(entry))
        a = np.frombuffer(rgba, np.uint8).reshape(h, w, 4).astype(np.float32)
        b = float((a[..., :3] * a[..., 3:] / 255.0).mean())
        if best is None or b > best[0]:
            best = (b, rgba, w, h)
    _b, rgba, w, h = best
    small = FR.resample_rgba(rgba, w, h, 256)
    a = np.frombuffer(small, np.uint8).reshape(256, 256, 4).astype(np.float32)
    return a[..., :3] * (a[..., 3:] / 255.0)


def _kmeans(x, k, iters=16, seed=3):
    rng = np.random.default_rng(seed)
    if len(x) <= k:
        return np.unique(x, axis=0)
    # seed along the brightness ramp so the clusters cover it evenly
    order = np.argsort(x.sum(1))
    c = x[order[np.linspace(0, len(x) - 1, k).astype(int)]].copy()
    for _ in range(iters):
        d = ((x[:, None, :] - c[None]) ** 2).sum(-1) if len(x) < 40000 else None
        if d is None:
            lab = np.empty(len(x), np.int64)
            for s in range(0, len(x), 20000):
                lab[s:s + 20000] = ((x[s:s + 20000, None, :] - c[None]) ** 2
                                    ).sum(-1).argmin(1)
        else:
            lab = d.argmin(1)
        for j in range(k):
            m = lab == j
            if m.any():
                c[j] = x[m].mean(0)
            else:
                c[j] = x[rng.integers(len(x))]
    return c


def plan_field(name, parts, vparts, art):
    """(new section 3, new section 9, info) -- raises on any doubt."""
    import field_bg_native as FN
    import ff7nx_fxrestore as R
    import ff7nx_marginblack as MB
    pals = FIELDS[name.lower()]
    s9 = parts[8]
    recs, pm, px = R._fx_records(s9)
    cols, hdr, npg, cpp = MB.palette_colours(parts[3])
    pal = cols.astype(np.int64).reshape(npg, cpp).copy()
    vs9 = vparts[8]
    vrecs, vpm, _vpx = R._fx_records(vs9)
    vcols, _vh, vnpg, vcpp = MB.palette_colours(vparts[3])
    vpal = vcols.astype(np.int64).reshape(vnpg, vcpp)
    vmap = collections.defaultdict(list)
    for layer, o in vrecs:
        vmap[(layer,) + R._key(vs9, o)].append(o)
    users = collections.defaultdict(list)
    for layer, o in recs:
        p = pm.get(s9[o + 34])
        if p is None:
            continue
        cx, cy, _k = R._cell(s9, o, p, px)
        users[(p.slot, cx, cy)].append((layer, o))
    # target cells per palette
    cells = collections.defaultdict(list)      # q -> [(slot,cx,cy,vpg,vsx,vsy)]
    others = []                                # non-beam users of a beam palette
    for cellkey, us in users.items():
        qs = {s9[o + 22] for _l, o in us}
        if not qs & set(pals):
            continue
        p = pm[cellkey[0]]
        if p.depth != 1:
            raise ValueError('beam cell on a truecolor page')
        if len(us) != 1:
            if all(s9[o + 26] == 0 for _l, o in us):
                others.append((cellkey, us))
            continue
        layer, o = us[0]
        q = s9[o + 22]
        if s9[o + 26] == 0:
            others.append((cellkey, us))
            continue
        vo = vmap.get((layer,) + R._key(s9, o))
        if not vo or len(vo) != 1:
            continue                           # margin / orphan: later pass
        vo = vo[0]
        vsx, vsy = struct.unpack_from('<hh', vs9, vo + 14)
        cells[q].append(cellkey + (vs9[vo + 34], vsx // 16, vsy // 16))
    if not cells:
        raise ValueError('no beam cells')
    frames = {}
    data = {}
    stats = {}
    for q in pals:
        if q not in cells or q >= npg or q >= vnpg:
            continue
        tgt, van = [], []
        for slot, cx, cy, vpg, vcx, vcy in cells[q]:
            if (vpg, q) not in frames:
                frames[(vpg, q)] = _brightest(art, name, vpg, q)
            fr = frames[(vpg, q)]
            if fr is None:
                raise ValueError('no Cosmos frames for page %d palette %d'
                                 % (vpg, q))
            tgt.append(fr[vcy * 16:vcy * 16 + 16, vcx * 16:vcx * 16 + 16])
            va = np.frombuffer(vpm[vpg].data, np.uint8, count=65536).reshape(
                256, 256)[vcy * 16:vcy * 16 + 16, vcx * 16:vcx * 16 + 16]
            van.append(_rgb15(vpal[q][va]))
        tgt = np.stack(tgt)
        van = np.stack(van)
        both = (tgt.max(-1) > 2) & (van.max(-1) > 2)
        if both.sum() < 256:
            raise ValueError('palette %d: Cosmos and 1997 beams do not '
                             'overlap' % q)
        scale = float(np.clip(van[both].sum() / max(tgt[both].sum(), 1.0),
                              0.3, 4.0))
        tgt = np.clip(tgt * scale, 0, 248)
        lit = tgt.max(-1) >= 4
        xs = tgt[lit].reshape(-1, 3)
        if len(xs) > 60000:
            xs = xs[np.random.default_rng(1).choice(len(xs), 60000, False)]
        cen = _kmeans(xs, ANIMATED - 1)
        q5 = np.clip(np.rint(cen / 8.0), 0, 31).astype(np.int64)
        q5[q5.sum(1) == 0] = [1, 1, 1]
        codes = (q5[:, 0] | (q5[:, 1] << 5) | (q5[:, 2] << 10))
        codes = np.unique(codes)
        newpal = np.zeros(ANIMATED, np.int64)
        newpal[1:1 + len(codes)] = codes[:ANIMATED - 1]
        prgb = _rgb15(newpal)
        prgb[0] = 1e6                          # never chosen by colour
        old = pal[q].copy()
        pal[q][:ANIMATED] = newpal
        # quantise with an ordered dither of one 5-bit step
        for (slot, cx, cy, _vpg, _vcx, _vcy), t in zip(cells[q], tgt):
            if slot not in data:
                data[slot] = np.frombuffer(pm[slot].data, np.uint8,
                                           count=65536).reshape(256, 256).copy()
            tt = t + np.tile(BAYER, (4, 4))[..., None] * 8.0
            # black is a candidate, so the beam's dim edge dithers out
            cand = np.concatenate([np.zeros((1, 3), np.float32),
                                   prgb[1:1 + len(codes)]])
            d = ((tt[:, :, None, :] - cand[None, None]) ** 2).sum(-1)
            idx = d.argmin(-1).astype(np.uint8)
            idx[t.max(-1) < 1] = 0
            data[slot][cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16] = idx
        # everything else that reads palette q: re-quantise into it
        for (slot, cx, cy), us in others:
            if not any(s9[o + 22] == q for _l, o in us):
                continue
            if len({s9[o + 22] for _l, o in us}) != 1:
                raise ValueError('palette %d shared across palettes' % q)
            if slot not in data:
                data[slot] = np.frombuffer(pm[slot].data, np.uint8,
                                           count=65536).reshape(256, 256).copy()
            a = data[slot][cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16]
            c = _rgb15(old[a])
            d = ((c[:, :, None, :] - prgb[None, None, :]) ** 2).sum(-1)
            idx = d.argmin(-1).astype(np.uint8)
            idx[(old[a] & 0x7FFF) == 0] = 0
            data[slot][cy * 16:cy * 16 + 16, cx * 16:cx * 16 + 16] = idx
        stats[q] = (len(cells[q]), round(scale, 2), len(codes))
    # every other record on these slots must not read the beam palettes'
    # rebuilt entries through another palette: checked by construction
    # (cells were exclusive or re-quantised above)
    sec3 = bytearray(parts[3])
    for q in pals:
        if q < npg:
            struct.pack_into('<%dH' % cpp, sec3, hdr + 2 * q * cpp,
                             *[int(v) & 0xFFFF for v in pal[q]])
    plist, t0, t1 = FN.parse_texture_block(s9, px)
    for slot, d in data.items():
        qq = plist[slot]
        plist[slot] = FN.Page(slot, qq.size_flag, 1, d.tobytes(), qq.px)
    return bytes(sec3), FN.replace_texture_block(s9, plist, t0, t1), stats


def apply_to_flevel(archive, payloads, art, encode=None, log=lambda *_: None):
    import lgp
    import ff7nx_fxrestore as R
    st = {'names': [], 'refused': []}
    if disabled() or art is None or getattr(art, 'provider', None) is None:
        return st
    encode = encode or archive.encode_field
    for name in FIELDS:
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            vparts = list(lgp.split_sections(archive.decompressed(entry)))
            parts[3], parts[8], info = plan_field(name, parts, vparts, art)
            try:            # the margins continue the NEW beam
                parts[8], _n = R.margin_palettes(name, parts, vparts)
            except ValueError:
                pass
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:90]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['names'].append('%s %s' % (name, info))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  BEAM HD (BUILD 618z): searchlight beams rebuilt from '
                   "Cosmos's brightest frame into dithered 127-entry beam "
                   'palettes, pulse untouched (%s; palette: cells, scale, '
                   'entries). %s=1 disables.' % ('; '.join(st['names']),
                                                  OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! beam HD %s: %s' % (name, why))
    return '\n'.join(out)
