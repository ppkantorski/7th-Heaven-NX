#!/usr/bin/env python3
"""ff7nx_siblingmargin.py -- give fship_22 the same view as the other
Highwind-bridge variants.

BUILD 617. The five fship_2X fields are one room. Four of them (fship_2,
23, 24, 25) have a 340-unit camera range, so the 16:9 camera travels
+-10 units and Cosmos painted the art out to x = +-224. fship_22 (the night
grade) ships a 320-unit range centred on -1: its camera never moves, so
Cosmos painted only what that fixed camera sees and left the last ~9 units
on each side OPAQUE BLACK in its DDS (x >= 215, x <= -216). The art itself
lines up with fship_24 to the pixel (gradient cross-correlation peaks at
dx = 0, dy = 0); only the camera and the missing strip differ. Hence the
"misalignment" when the variants are compared side by side.

This pass paints the missing strip from fship_24 -- the same room, same
records, same geometry -- converted to fship_22's night grade with a LOCAL
colour fit: for every tile row, a per-channel quadratic (R,G,B,R2,G2,B2,1)
fitted on the texels both fields light in the 48 units just inside the
strip (rows +-16 around it). A global fit is not good enough (error ~19);
the local one holds out at ~12/255 on the real art next to the strip.

Written ONLY into:
  * texels of fship_22 layer 1/2 static tiles in the outer two tile columns
    (x >= 192 or x <= -208) that belong to the opaque-black run reaching the
    outer edge of their row,
  * where the matched fship_24 tile (same layer, position, param/state) is
    opaque there,
  * in cells read by no other record.

Records, UVs, palettes, pages, slots and sizes are unchanged (checked).
`ff7nx_ws` then gives fship_22 fship_24's camera range, and camfit
re-measures the art and tightens it if any row was not completed -- so the
camera can only travel as far as there is art.

SEVENTH_NX_NO_SIBLING_MARGIN=1 disables both halves.
"""
from __future__ import annotations

import os
import struct

import numpy as np

import diag_common as DC
import field_bg_native as FN

OFF_ENV = 'SEVENTH_NX_NO_SIBLING_MARGIN'
TARGETS = {'fship_22': 'fship_24'}
UV_SCALE = 10_000_000
OUTER = 192            # tiles with x >= 192 or x + 16 <= -192
FIT_IN = 48            # fit zone: this many units inside the outer columns
BLACK = 9              # 0..255 max channel: black / NEAR_BLACK
DARK = 40              # the strip's own noise stays below this
STRIP = 10             # units: the black strip is x >= 214 and x < -214
SEAM = 2               # plus the dimmed columns Cosmos faded into it
FEATHER = 5            # units of cross-fade inside that
LIT = 12
MIN_PAIRS = 300
MAX_FIT_ERR = 40.0     # the icy pillar at the top differs most (~35)
SMOOTH_PASSES = 2      # [1,2,1] passes over the per-row coefficients

# Fields whose strip was completed in this build; ff7nx_ws reads it.
FILLED = set()


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _unpack(v):
    v = v.astype(np.int32)
    return np.stack((((v >> 11) & 31) * 255.0 / 31,
                     ((v >> 6) & 31) * 255.0 / 31,
                     (v & 31) * 255.0 / 31), -1)


def _pack(rgb):
    q = np.clip(np.rint(rgb * 31.0 / 255.0), 0, 31).astype(np.int32)
    v = ((q[..., 0] << 11) | ((q[..., 1] << 1) << 5) | q[..., 2]).astype(
        np.uint16)
    v[v == FN.EMPTY] = FN.NEAR_BLACK
    return v


def _fit(X, Y):
    """Quadratic colour map by ridge-regularised normal equations.

    Written with einsum and a 7x7 solve instead of lstsq and `@`: on macOS
    numpy's Accelerate-backed matmul raises spurious divide-by-zero /
    overflow / invalid RuntimeWarnings on large float arrays (seen in the
    build log). Returns (M, mean abs error) or (None, None) if the fit is
    not finite."""
    F = _feat(np.asarray(X, np.float64))
    Y = np.asarray(Y, np.float64)
    with np.errstate(all='ignore'):
        A = np.einsum('nk,nj->kj', F, F) + 1e-3 * len(F) * np.eye(F.shape[1])
        B = np.einsum('nk,nj->kj', F, Y)
        try:
            M = np.linalg.solve(A, B)
        except np.linalg.LinAlgError:
            return None, None
        if not np.isfinite(M).all():
            return None, None
        e = float(np.abs(np.einsum('nk,kj->nj', F, M) - Y).mean())
    if not np.isfinite(e):
        return None, None
    return M, e


def _feat(rgb):
    return np.concatenate([rgb, rgb * rgb / 255.0,
                           np.ones(rgb.shape[:-1] + (1,))], -1)


class _Sec:
    def __init__(self, sec9):
        self.s = sec9
        pl, ts, _te, self.px = DC.parse_pages(sec9)
        self.pages = {p.slot: p for p in pl if p is not None}
        self.rows = [(layer, off) for layer, offs in DC.walk_layers(
            sec9, sec9.find(b'BACK'), ts) for off in offs]
        self.step = self.px // 16
        self._arr = {}

    def arr(self, slot):
        if slot not in self._arr:
            p = self.pages[slot]
            self._arr[slot] = np.frombuffer(p.data, '<u2').reshape(p.px, p.px)
        return self._arr[slot]

    def cell(self, off, slot=None):
        slot = self.s[off + 32] if slot is None else slot
        p = self.pages.get(slot)
        if p is None or p.size_flag or p.depth != 2 or p.px != self.px:
            return None
        u, v = struct.unpack_from('<II', self.s, off + 42)
        return (slot, int(round(u / UV_SCALE * 16)) * self.step,
                int(round(v / UV_SCALE * 16)) * self.step)

    def block(self, c):
        slot, cx, cy = c
        return self.arr(slot)[cy:cy + self.step, cx:cx + self.step]

    def key(self, layer, off):
        s = self.s
        return (layer, struct.unpack_from('<hh', s, off + 2), s[off + 26],
                s[off + 27])

    def static(self, layer, off):
        s = self.s
        return layer in (1, 2) and not s[off + 28] and not s[off + 26] \
            and not s[off + 27]


def plan_field(sec9, sib9):
    """[(slot, py, px, value)] and stats."""
    st = {'texels': 0, 'cells': 0, 'rows': 0, 'fit_error': None,
          'unfilled': 0, 'why': []}
    own, sib = _Sec(sec9), _Sec(sib9)
    if own.px != sib.px:
        raise ValueError('page sizes differ (%d vs %d)' % (own.px, sib.px))
    step = own.step
    scale = own.px // 256
    readers = {}
    for layer, off in own.rows:
        for sl in ((sec9[off + 32], sec9[off + 34]) if sec9[off + 28]
                   else (sec9[off + 32],)):
            c = own.cell(off, sl)
            if c is not None:
                readers.setdefault(c, []).append(off)
    sib_at = {}
    for layer, q in sib.rows:
        if sib.static(layer, q):
            sib_at.setdefault(sib.key(layer, q), []).append(q)

    sib_pos = {}
    for layer, q in sib.rows:
        if sib.static(layer, q) and sib.cell(q) is not None:
            sib_pos.setdefault(struct.unpack_from('<hh', sib9, q + 2),
                               []).append((layer, q))

    def sib_composite(x, y):
        recs = sib_pos.get((x, y))
        if not recs:
            return None
        recs = sorted(recs, key=lambda r: (
            r[0], -struct.unpack_from('<I', sib9, r[1] + 38)[0]))
        out = np.zeros((step, step), np.uint16)
        for layer, q in recs:
            b = sib.block(sib.cell(q))
            if b.shape != (step, step):
                return None
            m = b != FN.EMPTY
            out[m] = b[m]
        return out

    # (side, layer, x, y, off, own_cell, own_blk, sib_blk, composite)
    tiles = []
    for layer, off in own.rows:
        if not own.static(layer, off):
            continue
        x, y = struct.unpack_from('<hh', sec9, off + 2)
        side = 1 if x >= OUTER - FIT_IN else (
            -1 if x + 16 <= -(OUTER - FIT_IN) else 0)
        if not side:
            continue
        cands = sib_at.get(own.key(layer, off), [])
        oc = own.cell(off)
        if oc is None:
            continue
        ob = own.block(oc)
        composite = False
        if len(cands) == 1 and sib.cell(cands[0]) is not None:
            sb = sib.block(sib.cell(cands[0]))
        else:
            # No record of this layer there in the sibling (fship_24 paints
            # that spot from another layer): take the sibling's static
            # picture at the same place instead, drawn opaque.
            sb = sib_composite(x, y)
            composite = True
            if sb is None:
                continue
        if ob.shape != (step, step) or sb.shape != (step, step):
            continue
        tiles.append((side, layer, x, y, off, oc, ob, sb, composite))
    if not tiles:
        return [], st

    edge_r = max(t[2] for t in tiles) + 16
    edge_l = min(t[2] for t in tiles)
    cols = np.arange(step) / float(scale)          # tile-local field units

    feathers = {}

    def black_run(ob, side, x):
        """The fixed-width strip Cosmos left dark at the outer edge, on the
        texel rows where it really is dark (>= 90% dark or keyed)."""
        fx = x + cols
        if side > 0:
            band, wide = fx >= edge_r - STRIP, fx >= edge_r - STRIP - SEAM
        else:
            band, wide = fx < edge_l + STRIP, fx < edge_l + STRIP + SEAM
        out = np.zeros(ob.shape, bool)
        if not band.any():
            feathers[id(ob)] = np.zeros(ob.shape, np.float32)
            return out
        o = _unpack(ob).max(-1)
        dark = (ob == FN.EMPTY) | (o <= DARK)
        rows = dark[:, band].mean(1) >= 0.9
        rows &= ((ob != FN.EMPTY) & (o <= BLACK))[:, band].any(1)
        out[np.ix_(rows, wide)] = True
        # Cross-fade zone just inside the strip, so the join between Cosmos's
        # own art and the completed strip is a ramp, not a straight line.
        if side > 0:
            start = edge_r - STRIP - SEAM - FEATHER
            wcol = np.clip((fx - start) / FEATHER, 0, 1)
        else:
            start = edge_l + STRIP + SEAM + FEATHER
            wcol = np.clip((start - fx) / FEATHER, 0, 1)
        wcol[wide] = 0
        fw = np.zeros(ob.shape, np.float32)
        fw[rows] = wcol
        feathers[id(ob)] = fw
        return out

    outer = [t for t in tiles if (t[2] >= OUTER if t[0] > 0
                                  else t[2] + 16 <= -OUTER)]
    runs = {t[4]: black_run(t[6], t[0], t[2]) for t in outer}
    # ONE smooth colour map per side, varying with height. A separate fit
    # per tile row showed as rectangles on hardware (each 16-unit block
    # its own tint); so fit at every tile row (pooled over layers, from the
    # rows around it), smooth the coefficients along y, and interpolate
    # them per texel row.
    anchors = {}
    errs = []
    for side in (1, -1):
        ys = sorted({t[3] for t in tiles if t[0] == side})
        fits = []
        for y in ys:
            for band in (24, 40, 64, 96):
                X, Y = [], []
                for u in tiles:
                    if u[0] != side or abs(u[3] - y) > band:
                        continue
                    m = (u[6] != FN.EMPTY) & (u[7] != FN.EMPTY)
                    if u[4] in runs:
                        m &= ~runs[u[4]]
                    if m.any():
                        X.append(_unpack(u[7])[m])
                        Y.append(_unpack(u[6])[m])
                if X and sum(len(v) for v in X) >= \
                        MIN_PAIRS * scale * scale * 2:
                    X, Y = np.concatenate(X), np.concatenate(Y)
                    M, e = _fit(X, Y)
                    if M is None:
                        continue
                    if e <= MAX_FIT_ERR:
                        fits.append((y + 8.0, M, e))
                        errs.append(e)
                        break
        if not fits:
            continue
        yc = np.array([f[0] for f in fits])
        Ms = np.stack([f[1] for f in fits])
        for _ in range(SMOOTH_PASSES):
            P = np.concatenate([Ms[:1], Ms, Ms[-1:]])
            Ms = (P[:-2] + 2 * P[1:-1] + P[2:]) / 4.0
        anchors[side] = (yc, Ms)

    def colour_rows(side, y):
        if side not in anchors:
            return None
        yc, Ms = anchors[side]
        yf = y + (np.arange(step) + 0.5) / scale
        out = np.empty((step,) + Ms.shape[1:])
        for i, v in enumerate(yf):
            j = np.searchsorted(yc, v)
            if j <= 0:
                out[i] = Ms[0]
            elif j >= len(yc):
                out[i] = Ms[-1]
            else:
                w = (v - yc[j - 1]) / (yc[j] - yc[j - 1])
                out[i] = Ms[j - 1] * (1 - w) + Ms[j] * w
        return out

    by_cell = {}
    for t in outer:
        side, layer, x, y, off, oc, ob, sb, composite = t
        run = runs[off]
        if not run.any():
            continue
        fill = run & (sb != FN.EMPTY)
        # A layer-2 cut-out: where the sibling's tile is transparent the
        # black is not art but a missing cut-out, so it becomes transparent
        # too and the layer beneath shows, as it does in the sibling.
        clear = run & (sb == FN.EMPTY) if (layer == 2
                                             and not composite) else None
        if not fill.any() and (clear is None or not clear.any()):
            continue
        Mrow = colour_rows(side, y)
        if Mrow is None:
            st['unfilled'] += 1
            st['why'].append((layer, x, y, 'fit'))
            continue
        new = ob.copy()
        if fill.any():
            f = _feat(_unpack(sb))                      # (step, step, 7)
            with np.errstate(all='ignore'):
                pred = np.einsum('rck,rkj->rcj', f, Mrow)   # per texel row
            pred = np.nan_to_num(pred, nan=0.0, posinf=255.0, neginf=0.0)
            new[fill] = _pack(np.clip(pred[fill], 0, 255))
            fw = feathers.get(id(ob))
            if fw is not None:
                fm = (fw > 0) & (ob != FN.EMPTY) & (sb != FN.EMPTY)
                if fm.any():
                    w = fw[fm][:, None]
                    new[fm] = _pack(np.clip(_unpack(ob)[fm] * (1 - w)
                                            + pred[fm] * w, 0, 255))
        if clear is not None:
            new[clear] = FN.EMPTY
        prev = by_cell.get(oc)
        if prev is not None and not np.array_equal(prev, new):
            by_cell[oc] = False
            continue
        by_cell[oc] = new
        st['rows'] += 1
    plans = []
    for c, new in by_cell.items():
        if new is False or len(readers.get(c, ())) != 1:
            st['unfilled'] += 1
            st['why'].append((c, 'shared'))
            continue
        slot, cx, cy = c
        old = own.block(c)
        for py, px in zip(*np.nonzero(new != old)):
            plans.append((slot, cy + py, cx + px, int(new[py, px])))
        st['cells'] += 1
    st['texels'] = len(plans)
    st['fit_error'] = round(float(np.mean(errs)), 1) if errs else None
    return plans, st


def apply_plans(sec9, plans):
    import ff7nx_fxseam as FS
    return FS.apply_plans(sec9, plans)


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    total = {'fields': 0, 'texels': 0, 'names': [], 'refused': []}
    FILLED.clear()
    if disabled():
        return total
    encode = encode or archive.encode_field

    def raw_of(name):
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            return None
        payload = payloads.get(name)
        return (lgp.lzs_decompress(payload[4:]) if payload
                else archive.decompressed(entry))

    for name, sib in TARGETS.items():
        try:
            raw, sraw = raw_of(name), raw_of(sib)
            if raw is None or sraw is None:
                continue
            parts = list(lgp.split_sections(raw))
            plans, st = plan_field(parts[8], lgp.split_sections(sraw)[8])
            if not plans:
                continue
            new9 = apply_plans(parts[8], plans)
            if len(new9) != len(parts[8]):
                raise ValueError('section 9 length changed')
            parts[8] = new9
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append(
                (name, '%s: %s' % (type(exc).__name__, str(exc)[:80])))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        FILLED.add(name)
        total['fields'] += 1
        total['texels'] += st['texels']
        total['names'].append('%s from %s: %d texels, %d cells, fit %.1f%s'
                              % (name, sib, st['texels'], st['cells'],
                                 st['fit_error'] or 0,
                                 ', %d left' % st['unfilled']
                                 if st['unfilled'] else ''))
    return total


def range_overrides(final):
    """{field: new range} for ff7nx_ws: a completed field takes its
    sibling's horizontal camera range (vertical kept)."""
    out = {}
    if disabled():
        return out
    for name in sorted(FILLED):
        sib = TARGETS.get(name)
        if name in final and sib in final:
            r = dict(final[name])
            r['left'] = int(final[sib]['left'])
            r['right'] = int(final[sib]['right'])
            r['width'] = r['right'] - r['left']
            out[name] = r
    return out


def summarise(st):
    out = []
    if st.get('fields'):
        out.append('  SIBLING MARGIN (BUILD 617): the opaque-black strip '
                   'Cosmos left at the 16:9 edges completed from the same '
                   'room in its sibling field, grade-matched per tile row '
                   '(%s); the camera range follows the sibling so the view '
                   'matches the other variants. %s=1 disables.'
                   % ('; '.join(st['names']), OFF_ENV))
    if st.get('refused'):
        out.append('  ! sibling margin: %s' % ', '.join(
            '%s: %s' % r for r in st['refused']))
    return '\n'.join(out)
