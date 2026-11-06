#!/usr/bin/env python3
"""ff7nx_decallift.py -- eye and mouth decals lifted off the face (BUILD 891).

THE REPORT (11-05): "the shop keeper's eyes [in mds5_m] are constantly
flickering ... i dont know how extensive this bug is".

WHAT IT IS. Ninostyle Chibi Fixes re-eyes 230 field heads the same way. The
head is drawn twice: group 0 is an untextured copy, and the last group is the
textured head with exactly the same vertices. In between, groups 1..3 are the
eye and mouth quads. The stock blink (x86 0x649B50) treats the first textured
group as the left eye and the next one as the right, and FFNx's mouth is
group 3, which is why the untextured copy exists at all.

The quads are decals laid on the face. On the merchant (DFGD, head DFHD.P)
every visible texel of both eyes sits 0.0070..0.0073 model units in front of
the skin, under 0.1 % of the head. The textured head is drawn AFTER the eyes,
and the port depth-tests every model polygon. Wherever the depth buffer rounds
eye and skin to the same value, the skin wins. As the idle animation moves the
head, that rounding flips frame to frame and the eyes flash on and off.

This is the same tie BUILD 547 (Cloud's snowboard face) and BUILD 618 (Tifa's)
fixed: "the port depth-tests it, the two tie, and as she moves the tie flips
-- the eyes flash on and off".

WHY THIS FIELD SHOWS IT FIRST. mds5_m's camera zoom is 3139, 17th of 702
fields (median 546). A long lens puts the models far from the camera, where a
depth step covers the most model units. The same decal gap is invisible in an
ordinary field and flickers in a zoomed one. Tifa's eyes sit 0.0089 off her
face and her bar (zoom 2926) shows nothing, so the merchant is right at the
edge. The neighbouring shops mds5_i/mds5_w (4397) and fields up to 5863 are
worse.

THE FIX. Every decal group of every char.lgp .p is measured where its texture
is visible (not colour-key black). If all of it is in front of the face, or
exactly on it, it is translated along its own outward normal until the
nearest visible texel clears the face by CLEARANCE (0.04, which is where the
mod's own authors put Cloud's eyes: median 0.04). The largest move is about
0.08 units, under 1 % of a head. Shape, UVs, texture, render state and group
order are unchanged. Nothing is ever pushed into the face.

LEFT ALONE, and reported:
  * a decal part-buried in the face (some visible texel more than BURIED,
    0.005, behind the skin). The author hid that part on purpose; lifting
    would reveal it. A texel less deep than that sits in the same tie zone as
    the merchant's 0.007 and flickers, so that decal is lifted.
  * a decal that would pass in front of any other part of the model (hair,
    brim, glasses) within the lift.
  * a decal with no face under it, or no visible texel.
Running it again changes nothing: a lifted decal already clears.

SEVENTH_NX_DECAL_LIFT=0 turns it off; a number sets the clearance.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import re
import struct

import numpy as np

ENV = 'SEVENTH_NX_DECAL_LIFT'
VERSION = 'decallift-891'
CLEARANCE = 0.04
TOL = 0.002             # a decal within this of CLEARANCE counts as clear
BURIED = 0.005          # deeper than this behind the skin = hidden on purpose
                        # (shallower is inside the tie zone: it flickers)
MAX_DECAL_VERTS = 8
GRID = 16               # samples per triangle edge for the visible-texel test

HDR = ('version', 'off04', 'vertex_type', 'numverts', 'numnormals',
       'num_unk1', 'numtex', 'numvcol', 'numedges', 'numpolys', 'num_unk2',
       'num_unk3', 'numhundreds', 'numgroups', 'numbbox', 'normidx')


def clearance():
    v = os.environ.get(ENV, '').strip().lower()
    if v in ('0', 'off', 'no', 'false', 'stock'):
        return 0.0
    try:
        f = float(v)
        return f if f > 0 else 0.0
    except ValueError:
        return CLEARANCE


def enabled():
    return clearance() > 0


# ----------------------------------------------------------------- .p file
def layout(d):
    """Header and the byte offset of each section. Trailing bytes past the
    last section are tolerated (some mod .p files carry them)."""
    if len(d) < 128:
        raise ValueError('short .p')
    h = dict(zip(HDR, struct.unpack_from('<16I', d, 0)))
    if h['version'] != 1:
        raise ValueError('not a version-1 .p')
    off = {}
    o = 128
    for name, n, sz in (('verts', h['numverts'], 12),
                        ('normals', h['numnormals'], 12),
                        ('unk1', h['num_unk1'], 12),
                        ('tex', h['numtex'], 8), ('vcol', h['numvcol'], 4),
                        ('pcol', h['numpolys'], 4),
                        ('edges', h['numedges'], 4),
                        ('polys', h['numpolys'], 24),
                        ('unk2', h['num_unk2'], 24),
                        ('unk3', h['num_unk3'], 3),
                        ('hundreds', h['numhundreds'], 100),
                        ('groups', h['numgroups'], 56),
                        ('bbox', h['numbbox'], 28)):
        off[name] = o
        o += n * sz
    if o > len(d):
        raise ValueError('.p is %d bytes, needs %d' % (len(d), o))
    return h, off


def read_model(d):
    h, off = layout(d)
    V = np.frombuffer(d, '<f4', h['numverts'] * 3,
                      off['verts']).reshape(-1, 3).astype(np.float64)
    UV = np.frombuffer(d, '<f4', h['numtex'] * 2,
                       off['tex']).reshape(-1, 2).astype(np.float64)
    groups = []
    for i in range(h['numgroups']):
        f = struct.unpack_from('<14I', d, off['groups'] + 56 * i)
        tris = []
        for k in range(f[2]):
            q = struct.unpack_from('<4H', d, off['polys'] + 24 * (f[1] + k))
            tris.append((q[1], q[2], q[3]))
        T = np.array(tris, int).reshape(-1, 3)
        if len(T) and (T.max() >= f[4] or f[3] + f[4] > h['numverts']):
            raise ValueError('group %d indexes past its vertices' % i)
        tex_idx = None
        if i < h['numhundreds']:
            tex_idx = struct.unpack_from('<I', d, off['hundreds']
                                         + 100 * i + 16)[0]
        groups.append({
            'type': f[0], 'vstart': f[3], 'nverts': f[4], 'tstart': f[11],
            'textured': bool(f[12]), 'tex_idx': tex_idx,
            'tris': T + f[3], 'local': T,
        })
    return h, off, V, UV, groups


# ------------------------------------------------------------- geometry
def _ray_hits(o, n, A, B, C):
    """Distances t >= 0 at which o + t n meets the triangles (A,B,C)."""
    e1 = B - A
    e2 = C - A
    p = np.cross(n, e2)
    det = (e1 * p).sum(1)
    ok = np.abs(det) > 1e-12
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    s = o - A
    u = (s * p).sum(1) * inv
    q = np.cross(s, e1)
    v = (q * n).sum(1) * inv
    t = (e2 * q).sum(1) * inv
    hit = ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9)
    return t[hit & (t >= 0)]


def _samples(V, UV, g, mask):
    """[(point, outward-ish face normal)] over the decal, only where its
    texture is visible. `mask` None = every sample counts."""
    out = []
    steps = np.linspace(0.02, 0.96, GRID)
    for tri, loc in zip(g['tris'], g['local']):
        a, b, c = V[tri]
        n = np.cross(b - a, c - a)
        ln = np.linalg.norm(n)
        if ln == 0:
            continue
        n = n / ln
        if mask is not None and len(UV):
            idx = g['tstart'] + loc
            if idx.max() >= len(UV):
                uva = uvb = uvc = None
            else:
                uva, uvb, uvc = UV[idx]
        for u in steps:
            for v in steps:
                if u + v > 0.98:
                    continue
                if mask is not None and len(UV) and uva is not None:
                    uv = uva + u * (uvb - uva) + v * (uvc - uva)
                    th, tw = mask.shape
                    x = int(np.clip((uv[0] % 1.0) * tw, 0, tw - 1))
                    y = int(np.clip((uv[1] % 1.0) * th, 0, th - 1))
                    if not mask[y, x]:
                        continue
                out.append((a + u * (b - a) + v * (c - a), n))
    return out


def _gaps(samples, out_dir, A, B, C):
    """Signed distance of each sample in front of the surface (A,B,C),
    measured along `out_dir`; None where the line misses it."""
    g = []
    for p, _n in samples:
        front = _ray_hits(p, out_dir, A, B, C)
        back = _ray_hits(p, -out_dir, A, B, C)
        cand = [-t for t in front] + [t for t in back]
        if cand:
            g.append(min(cand, key=abs))
    return np.array(g)


# ------------------------------------------------------------- one model
def plan_model(d, mask_of=lambda tex_idx: None, floor=None):
    """(new bytes or None, [(group, verdict, detail)])."""
    floor = clearance() if floor is None else floor
    h, off, V, UV, groups = read_model(d)
    textured = [i for i, g in enumerate(groups) if g['textured']]
    if len(textured) < 2:
        return None, []
    head = max(textured, key=lambda i: len(groups[i]['tris']))
    hg = groups[head]
    hv = V[hg['vstart']:hg['vstart'] + hg['nverts']]
    base = {head} | {i for i, g in enumerate(groups)
                     if g['nverts'] == hg['nverts'] and i != head
                     and np.array_equal(V[g['vstart']:g['vstart'] + g['nverts']], hv)}
    decals = [i for i in textured if i not in base
              and 0 < groups[i]['nverts'] <= MAX_DECAL_VERTS]
    if not decals:
        return None, []
    # every group must own its own vertex range, or moving one moves another
    spans = sorted((g['vstart'], g['vstart'] + g['nverts']) for g in groups
                   if g['nverts'])
    if any(spans[k][1] > spans[k + 1][0] for k in range(len(spans) - 1)):
        return None, [(None, 'shared vertices', '')]
    HT = hg['tris']
    A, B, C = V[HT[:, 0]], V[HT[:, 1]], V[HT[:, 2]]
    centre = V[np.unique(HT)].mean(0)
    others = [groups[i]['tris'] for i in range(len(groups))
              if i not in base and len(groups[i]['tris'])]
    newV = V.copy()
    report = []
    for i in decals:
        g = groups[i]
        try:
            mask = mask_of(g['tex_idx'])
        except Exception:                                      # noqa: BLE001
            mask = None
        if mask is not None and not mask.any():
            report.append((i, 'invisible', 'no visible texel'))
            continue
        samples = _samples(V, UV, g, mask)
        if not samples:
            report.append((i, 'invisible', 'no visible texel'))
            continue
        nrm = np.zeros(3)
        for p, n in samples:
            nrm += n if np.dot(n, p - centre) >= 0 else -n
        if not np.linalg.norm(nrm):
            report.append((i, 'off face', 'no outward side'))
            continue
        nrm /= np.linalg.norm(nrm)
        gaps = _gaps(samples, nrm, A, B, C)
        if not len(gaps):
            report.append((i, 'off face', 'no face under it'))
            continue
        lo = float(gaps.min())
        if lo >= floor - TOL:
            report.append((i, 'clear', '%.4f' % lo))
            continue
        if lo < -BURIED:
            report.append((i, 'buried', '%.4f' % lo))
            continue
        delta = floor - lo
        # nothing else of the model may lie in the path
        blocked = False
        for T in others:
            if T is groups[i]['tris']:
                continue
            OA, OB, OC = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
            for p, _n in samples[::4] + samples[-1:]:
                t = _ray_hits(p, nrm, OA, OB, OC)
                if len(t) and ((t > 1e-6) & (t <= delta + 1e-6)).any():
                    blocked = True
                    break
            if blocked:
                break
        if blocked:
            report.append((i, 'blocked', '%.4f' % lo))
            continue
        s, e = g['vstart'], g['vstart'] + g['nverts']
        moved = (V[s:e] + delta * nrm).astype(np.float32).astype(np.float64)
        newV[s:e] = moved
        moved_samples = [(p + delta * nrm, n) for p, n in samples]
        after = _gaps(moved_samples, nrm, A, B, C)
        if not len(after) or after.min() < floor - TOL:
            newV[s:e] = V[s:e]
            report.append((i, 'refused', 'recheck %.4f' % (
                after.min() if len(after) else float('nan'))))
            continue
        report.append((i, 'lifted', '%.4f -> %.4f (+%.4f)'
                       % (lo, float(after.min()), delta)))
    if np.array_equal(newV, V):
        return None, report
    out = bytearray(d)
    out[off['verts']:off['verts'] + 12 * h['numverts']] = \
        newV.astype('<f4').tobytes()
    # grow the bounding box if a decal now reaches past it
    if h['numbbox']:
        bb = list(struct.unpack_from('<I6f', out, off['bbox']))
        mx = np.maximum(np.array(bb[1:4]), newV.max(0))
        mn = np.minimum(np.array(bb[4:7]), newV.min(0))
        if (mx >= mn).all():
            struct.pack_into('<I6f', out, off['bbox'], bb[0],
                             *mx.tolist(), *mn.tolist())
    out = bytes(out)
    layout(out)                                                # re-parse
    return out, report


# ------------------------------------------------------------- textures
def visible_mask(tex_bytes):
    """Bool [h, w]: texels that are not the colour key (exact black)."""
    import tex as T
    t = T.parse(tex_bytes)
    if t is None:
        return None
    w, h, bpp = t['width'], t['height'], t['bytes_per_pixel']
    px = np.frombuffer(t['pixels'], np.uint8, w * h * bpp)
    if bpp == 1:
        pal = np.frombuffer(t['palette'], np.uint8).reshape(-1, 4)
        if not len(pal):
            return None
        vis = pal[:, :3].astype(int).sum(1) > 0
        n = t['colors_per_palette'] or len(pal)
        return vis[np.minimum(px.reshape(h, w), min(n, len(vis)) - 1)]
    if bpp == 2:
        return (px.view('<u2').reshape(h, w) & 0x7FFF) != 0
    return px.reshape(h, w, bpp)[..., :3].astype(int).sum(-1) > 0


def rsd_textures(get, names):
    """{'xxxx.p': [[TEX name, ...] per rsd]} from every .rsd in the archive."""
    out = {}
    for n in names:
        if not n.endswith('.rsd'):
            continue
        data = get(n)
        if not data:
            continue
        txt = data.decode('latin1', 'replace')
        m = re.search(r'PLY=([\w.]+?)\.PLY', txt, re.I)
        if not m:
            continue
        texs = re.findall(r'TEX\[\d+\]=([\w.]+?)\.TIM', txt, re.I)
        out.setdefault(m.group(1).lower() + '.p', []).append(
            [t.lower() + '.tex' for t in texs])
    return out


def plan_char(get, names, floor=None, log=lambda *_: None):
    """{name: new bytes}, {name: report}."""
    floor = clearance() if floor is None else floor
    texs = rsd_textures(get, names)
    masks = {}

    def mask(tn):
        if tn not in masks:
            data = get(tn)
            try:
                masks[tn] = visible_mask(data) if data else None
            except Exception:                                  # noqa: BLE001
                masks[tn] = None
        return masks[tn]

    want, reports = {}, {}
    for n in names:
        if not n.endswith('.p'):
            continue
        d = get(n)
        if not d:
            continue
        lists = texs.get(n, [])

        def mask_of(ti, lists=lists):
            if ti is None or not lists:
                return None
            ms = [mask(L[ti]) for L in lists if ti < len(L)]
            ms = [m for m in ms if m is not None]
            if not ms:
                return None
            if len({m.shape for m in ms}) > 1:
                return None
            out = ms[0].copy()
            for m in ms[1:]:
                out |= m
            return out
        try:
            new, rep = plan_model(d, mask_of, floor)
        except (ValueError, struct.error, IndexError) as exc:
            reports[n] = [(None, 'unreadable', str(exc)[:60])]
            continue
        if rep:
            reports[n] = rep
        if new is not None and new != d:
            want[n] = new
    return want, reports


def apply_to_char(path, log=lambda *_: None):
    import lgp
    st = {'written': False, 'lifted': {}, 'reports': {}, 'refused': None,
          'floor': clearance()}
    if not enabled():
        return st
    try:
        a = lgp.Archive(path)

        def get(n):
            e = a.index.get(n)
            return e['payload'] if e else None
        want, st['reports'] = plan_char(get, a.names(), st['floor'], log)
        for name, data in sorted(want.items()):
            a.index[name]['payload'] = data
            st['lifted'][name] = [r for r in st['reports'][name]
                                  if r[1] == 'lifted']
        if want:
            tmp = path + '.decallift-tmp'
            a.write(tmp)
            os.replace(tmp, path)
            st['written'] = True
    except Exception as exc:                                   # noqa: BLE001
        st['refused'] = str(exc)[:160]
    return st


def summarise_char(st):
    if st.get('refused'):
        return ('  ! decal lift (BUILD 891): char.lgp not changed -- %s'
                % st['refused'])
    if not st.get('floor'):
        return '  decal lift (BUILD 891): off (%s=0)' % ENV
    counts = {}
    for rep in st.get('reports', {}).values():
        for _g, verdict, _d in rep:
            counts[verdict] = counts.get(verdict, 0) + 1
    lifted = sum(len(v) for v in st.get('lifted', {}).values())
    return ('  DECAL LIFT (BUILD 891): %d eye/mouth decal(s) on %d model(s) '
            'lifted to %.3f off the face so the depth test cannot tie them '
            '(%s); %d already clear, %d left alone (buried %d, blocked %d, '
            'refused %d). %s=0 disables.'
            % (lifted, len(st.get('lifted', {})), st['floor'],
               'this run' if st.get('written') else 'already applied'
               if not lifted else 'not written',
               counts.get('clear', 0),
               counts.get('buried', 0) + counts.get('blocked', 0)
               + counts.get('refused', 0),
               counts.get('buried', 0), counts.get('blocked', 0),
               counts.get('refused', 0), ENV))
