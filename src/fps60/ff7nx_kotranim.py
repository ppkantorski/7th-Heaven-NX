#!/usr/bin/env python3
"""ff7nx_kotranim.py -- BUILD 750: the Knights of the Round weapon flicker.

THE REPORT: after one knight's strike his weapon flickers back to a swung
pose on three frames out of every four, for several ticks. Vanilla (15 fps)
does not do it, and it predates every 60 fps decorator change.

WHAT IT IS
==========
The flicker is in the ANIMATION DATA, not the engine. The "60 FPS" mod's
KOTRAnimation60FPS folder replaces magic.lgp knight01..13.a00 with 4x
animations: frame 4i is vanilla key i, and 4i+1..4i+3 are the in-betweens
it generated. The knight scripts advance [model+0x7C] once per call. Under
the KOTR decorator that is once per rendered frame, so every in-between is
drawn (which is the whole point of the 4x files).

FF7 battle bones are Euler angles applied Y, X, Z (R = Ry*Rx*Rz) in 4096ths
of a turn. When X is +-90 degrees, Y and Z collapse into one degree of freedom:
[1024, 1469, 0] and [1024, 0, 2627] are the same pose. The mod's converter
produces those gimbal-equivalent forms in the in-betweens. In a few runs it
drops the Y/Z part altogether and writes [1024, 0, 0], which is a different
pose. knight06 bone 40 (the weapon arm) is a static key [1024, 1469, 0] for
ticks 15-46, but its in-betweens read [1024, 0, 0] on ticks 21 (j=1,3),
22 (j=2), 24, 25, 26 and 34 (j=1,3). Matched to kotr_8.mp4 (animation frame
= video frame + 9), that is exactly the observed pattern: frames 76, 78 and
81, then 88-90, 92-94 and 96-98 with the key frame between each group.
knight04 bone 40 (ticks 21-28) and knight11 bones 34/40 (ticks 0, 6, 12, 59,
61) are broken the same way.

THE REPAIR
==========
Every in-between is compared, per bone, with the quaternion slerp of its two
surrounding key frames. One that is more than 30 degrees off is replaced with
the slerp. When the keys are the same pose, the key's own values are copied
exactly. A pair of keys about 180 degrees apart has no unique shortest path,
so it is left alone. Key frames, translations and every other in-between are
untouched. The file is re-encoded by an encoder that reproduces all 13
vanilla knight .a00 files byte for byte (tests/test_kotranim.py).

SEVENTH_NX_KOTR_ANIM=stock leaves the mod's files as shipped.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import math
import os
import re
import struct

ENV = 'SEVENTH_NX_KOTR_ANIM'
NAME_RE = re.compile(r'^knight\d\d\.a00$')
DEV_DEG = 30.0          # an in-between this far from the slerp is broken
AMBIG_DEG = 150.0       # keys this far apart have no unambiguous path
SAME_DEG = 0.5          # keys this close are the same pose


def enabled():
    return os.environ.get(ENV, '').strip().lower() not in (
        'stock', '0', 'off', 'false', 'no')


# --------------------------------------------------------------- bitstream
class _R:
    def __init__(self, b):
        self.b, self.p = b, 0

    def u(self, n):
        v = 0
        for _ in range(n):
            byte = self.b[self.p >> 3]
            v = (v << 1) | ((byte >> (7 - (self.p & 7))) & 1)
            self.p += 1
        return v

    def sv(self, n):
        if n == 0:
            return 0
        v = self.u(n)
        return v - (1 << n) if v & (1 << (n - 1)) else v


class _W:
    def __init__(self):
        self.bits = []

    def u(self, v, n):
        for i in range(n - 1, -1, -1):
            self.bits.append((v >> i) & 1)

    def sv(self, v, n):
        if not -(1 << (n - 1)) <= v < (1 << (n - 1)):
            raise ValueError('%d does not fit %d signed bits' % (v, n))
        self.u(v & ((1 << n) - 1), n)

    def data(self):
        b = self.bits + [0] * ((-len(self.bits)) % 8)
        out = bytearray()
        for i in range(0, len(b), 8):
            x = 0
            for bit in b[i:i + 8]:
                x = (x << 1) | bit
            out.append(x)
        return bytes(out)


def decode(data, off=4):
    """(header, frames). frames[i] = (translation[3], rotations[nb][3])."""
    nb, nf, bl = struct.unpack_from('<III', data, off)
    nf2, alen, key = struct.unpack_from('<HHB', data, off + 12)
    if key not in (0, 2, 4):
        raise ValueError('key %d' % key)
    st = _R(data[off + 17:off + 17 + alen])
    t = [st.sv(16) for _ in range(3)]
    rots = [[st.sv(12 - key) << key for _ in range(3)] for _ in range(nb)]
    frames = [(t, rots)]
    for _fi in range(1, nf):
        pt, pr = frames[-1]
        t = [pt[i] + st.sv(16 if st.u(1) else 7) for i in range(3)]
        rots = []
        for bi in range(nb):
            r = []
            for c in range(3):
                if st.u(1):
                    dl = st.u(3)
                    if dl == 0:
                        d = -1
                    elif dl == 7:
                        d = st.sv(12 - key)
                    else:
                        d = st.sv(dl)
                        a = 1 << (dl - 1)
                        d = d - a if d < 0 else d + a
                    d <<= key
                else:
                    d = 0
                r.append(pr[bi][c] + d)
            rots.append(r)
        frames.append((t, rots))
    return dict(nb=nb, nf=nf, key=key, off=off), frames


def _rot_delta(w, d, key):
    if d == 0:
        w.u(0, 1)
        return
    w.u(1, 1)
    q = d >> key
    if q << key != d:
        raise ValueError('delta %d is not a multiple of 2^%d' % (d, key))
    if q == -1:
        w.u(0, 3)
        return
    for dl in range(1, 7):
        a = 1 << (dl - 1)
        v = q + a if q < 0 else q - a
        if -a <= v < a and ((q < 0) == (v < 0)):
            w.u(dl, 3)
            w.sv(v, dl)
            return
    w.u(7, 3)
    w.sv(q, 12 - key)


def encode(data, frames, off=4):
    """Rebuild `data` with `frames`; everything outside the bitstream is
    copied, with the two length fields updated."""
    nb, nf, bl = struct.unpack_from('<III', data, off)
    nf2, alen, key = struct.unpack_from('<HHB', data, off + 12)
    if len(frames) != nf:
        raise ValueError('frame count changed')
    w = _W()
    t, r = frames[0]
    for x in t:
        w.sv(x, 16)
    for b in r:
        for c in b:
            w.sv(c >> key, 12 - key)
    for fi in range(1, nf):
        pt, pr = frames[fi - 1]
        t, r = frames[fi]
        for i in range(3):
            d = t[i] - pt[i]
            if -64 <= d < 64:
                w.u(0, 1)
                w.sv(d, 7)
            else:
                w.u(1, 1)
                w.sv(d, 16)
        for bi in range(nb):
            for c in range(3):
                d = r[bi][c] - pr[bi][c]
                if not -2048 <= d >> key <= 2047:
                    # A bone angle is periodic in 4096; a delta past half a
                    # turn is written as its whole-turn equivalent. The
                    # running value then differs from `frames` by 4096, so
                    # carry that into the frame for the next delta.
                    d = ((d + 2048) % 4096) - 2048
                    r[bi][c] = pr[bi][c] + d
                _rot_delta(w, d, key)
    st = w.data()
    tail = data[off + 17 + alen:off + 12 + bl]
    return (bytes(data[:off]) + struct.pack('<III', nb, nf, bl - alen + len(st))
            + struct.pack('<HHB', nf2, len(st), key) + st + bytes(tail)
            + bytes(data[off + 12 + bl:]))


# ---------------------------------------------------------------- rotation
_U = 2.0 * math.pi / 4096.0


def _quat(xyz):
    """Quaternion (w,x,y,z) of R = Ry(y) * Rx(x) * Rz(z)."""
    def q(axis, a):
        h = 0.5 * a
        v = [0.0, 0.0, 0.0]
        v[axis] = math.sin(h)
        return (math.cos(h), v[0], v[1], v[2])
    x, y, z = (v * _U for v in xyz)
    return _qmul(_qmul(q(1, y), q(0, x)), q(2, z))


def _qmul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw)


def _angle(a, b):
    d = abs(sum(p * q for p, q in zip(a, b)))
    return math.degrees(2.0 * math.acos(min(1.0, d)))


def _slerp(a, b, t):
    d = sum(p * q for p, q in zip(a, b))
    if d < 0:
        b, d = tuple(-x for x in b), -d
    if d > 0.9995:
        r = tuple(p + (q - p) * t for p, q in zip(a, b))
    else:
        th = math.acos(d)
        s = math.sin(th)
        r = tuple((math.sin((1 - t) * th) * p + math.sin(t * th) * q) / s
                  for p, q in zip(a, b))
    n = math.sqrt(sum(x * x for x in r))
    return tuple(x / n for x in r)


def _euler(q):
    """12-bit (x, y, z) for R = Ry*Rx*Rz from a quaternion."""
    w, x, y, z = q
    m = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    sx = max(-1.0, min(1.0, -m[1][2]))
    ax = math.asin(sx)
    if abs(sx) < 0.99999:
        ay = math.atan2(m[0][2], m[2][2])
        az = math.atan2(m[1][0], m[1][1])
    else:
        az = 0.0
        ay = math.atan2(-m[2][0], m[0][0])
    return [int(round(v / _U)) % 4096 for v in (ax, ay, az)]


def _near(raw, ref):
    """Shift each 12-bit component of `raw` by whole turns to sit nearest
    `ref`, so the delta stream stays as small as the original's."""
    out = []
    for v, r in zip(raw, ref):
        v = r + ((v - r + 2048) % 4096) - 2048
        out.append(v)
    return out


def repair_frames(frames):
    """Fix in-betweens in place. Returns [(frame, bone, old, new)]."""
    fixes = []
    n = len(frames)
    if n < 5:
        return fixes
    nb = len(frames[0][1])
    # Key i is frame 4i (verified against vanilla for all 13 knights); a
    # file may carry a few held frames after its last key (knight11 has 288
    # = 4*72), which have no following key and are left alone.
    for i in range(0, n - 4, 4):
        ka, kb = frames[i][1], frames[i + 4][1]
        for bi in range(nb):
            qa, qb = _quat(ka[bi]), _quat(kb[bi])
            span = _angle(qa, qb)
            if span > AMBIG_DEG:
                continue
            for j in (1, 2, 3):
                cur = frames[i + j][1][bi]
                if span < SAME_DEG:
                    tgt = qa
                else:
                    tgt = _slerp(qa, qb, j / 4.0)
                if _angle(_quat(cur), tgt) <= DEV_DEG:
                    continue
                raw = list(ka[bi]) if span < SAME_DEG else _euler(tgt)
                # Same angles, but the whole-turn offset of the frame before,
                # so every delta in the stream stays inside its 12 bits.
                new = _near(raw, frames[i + j - 1][1][bi])
                fixes.append((i + j, bi, list(cur), new))
                frames[i + j][1][bi] = new
    return fixes


def is_4x_of(frames, vframes):
    """True when `frames` is a 4x expansion of `vframes`: every vanilla key
    i sits at frame 4i, the same pose bone for bone."""
    nv, n = len(vframes), len(frames)
    if nv < 2 or not 4 * (nv - 1) + 1 <= n <= 4 * nv:
        return False
    if len(frames[0][1]) != len(vframes[0][1]):
        return False
    for i in range(nv):
        for a, b in zip(frames[4 * i][1], vframes[i][1]):
            if any((p - q) % 4096 not in (0, 1, 4095) for p, q in zip(a, b)):
                return False
    return True


def repair(payload, vanilla):
    """(new payload, fixes), or (None, []) when nothing needed changing or
    the file is not a 4x expansion of the vanilla animation `vanilla`."""
    if struct.unpack_from('<I', payload, 0)[0] != 1 or \
            struct.unpack_from('<I', vanilla, 0)[0] != 1:
        return None, []
    _h, frames = decode(payload)
    _hv, vframes = decode(vanilla)
    if not is_4x_of(frames, vframes):
        return None, []
    fixes = repair_frames(frames)
    if not fixes:
        return None, []
    want = [[[v % 4096 for v in b] for b in f[1]] for f in frames]
    out = encode(payload, [(list(t), [list(b) for b in r]) for t, r in frames])
    _h2, check = decode(out)
    if [[[v % 4096 for v in b] for b in f[1]] for f in check] != want or \
            [f[0] for f in check] != [f[0] for f in frames]:
        raise ValueError('re-encode does not round-trip')
    return out, fixes


def apply(archive, spell_payloads, payloads, log=lambda *_: None):
    """Repair the knight animations a mod ships in magic.lgp. Only entries
    a mod replaced are considered -- vanilla 15 fps files have no
    in-betweens. Returns the number of entries changed."""
    if not enabled():
        return 0
    done = 0
    total = 0
    vanilla = {e['name'].lower(): e['payload'] for e in archive.entries
               if NAME_RE.match(e['name'].lower())}
    for name in sorted(payloads):
        if not NAME_RE.match(name.lower()) or name.lower() not in vanilla:
            continue
        try:
            out, fixes = repair(payloads[name], vanilla[name.lower()])
        except Exception as exc:                          # noqa: BLE001
            log('  ! KOTR animation: %s left as shipped (%s)' % (name, exc))
            continue
        if out is not None:
            payloads[name] = out
            done += 1
            total += len(fixes)
            bones = sorted({b for _f, b, _o, _n in fixes})
            log('  KOTR animation: %s -- %d broken in-between bone pose(s) '
                'replaced by the slerp of their key frames (bones %s)'
                % (name, len(fixes), ', '.join(map(str, bones))))
    if done:
        log('  KOTR animation: %d knight file(s) repaired, %d pose(s); '
            '%s=stock reverts' % (done, total, ENV))
    return done
