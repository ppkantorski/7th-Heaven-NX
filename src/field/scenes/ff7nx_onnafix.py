#!/usr/bin/env python3
"""ff7nx_onnafix.py -- the two caped women on floor 60 of the Shinra
building (blin60_1, groups KEIBIA / KEIBIB) stand in a pose made for them.
BUILD 717.

The user (10-16): "does nino style ship a correction in blin60_1 for
blin60_1shinra_onna.char? apparently the model was fucked up in the original
game ... can you appropriately implement the required fix to get it looking
like this?"

WHY THEY ARE MANGLED. Their model, EDEA.HRC (skeleton onna_sk), has 23
bones: hip, chest, head, none1, hair, then the arms and legs. Their only
animation, BOCB.aki, is the guards' arms-behind-the-back stand for the
21-bone default skeleton (no none1 / hair). Applied to EDEA, every rotation
from bone 3 on lands on the wrong bone: hair, cape and arms jut out. No mod
in the set fixes it: Ninostyle Chibi and its Fixes ship new EDEA parts and a
face but keep the 23-bone EDEA.HRC and the field's BOCB (Ninostyle's Lazaro
option has a 21-bone EDEA, which is not used here).

THE FIX (the one the "FFVII's Most Broken NPC" video arrives at). A new
clip, ONNA.a, is added to char.lgp: Aerith's stand (AVBF, the clip her
23-bone n_earith_sk plays in 219 fields, same bone layout) with the two
collar bones, which carry the cape, swung COLLAR_SWING degrees about the
model's left-right axis so the cape hangs down the sides, and the arms
hanging straight down, ARM_OUT degrees out from the body, so the hands come
out beside the dress. It is computed from the AVBF in the archive being
built, frame for frame. In blin60_1's model loader the EDEA records name
ONNA.aki instead of BOCB.aki. The guards and everyone else keep BOCB.

If the EDEA.HRC that ships is not 23 bones (a 21-bone replacement such as
Lazaro's), ONNA.a is a byte copy of BOCB.a, which already fits it, so the
field never names a clip that does not match. The loader is only touched
when char.lgp is confirmed to hold ONNA.a (READY).

SEVENTH_NX_NO_ONNA_FIX=1 disables.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import math
import os
import re
import struct

VERSION = 717
OFF_ENV = 'SEVENTH_NX_NO_ONNA_FIX'
HRC = 'edea'
OLD_ANIM, SRC_ANIM, NEW_ANIM = 'bocb', 'avbf', 'onna'
FIELDS = ('blin60_1',)
COLLAR_BONES = ('l_collar', 'r_collar')
ARMS = (('l_uparm', 'l_foarm', 'l_hand'), ('r_uparm', 'r_foarm', 'r_hand'))
COLLAR_SWING = -88.0    # degrees about model X: the cape hangs straight down
ARM_OUT = 5.0           # degrees out from straight down

# Set by apply_to_char once char.lgp holds ONNA.a; the flevel pass (after
# the char pass) repoints the loader only then.
READY = False


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def enabled():
    return not disabled()


# ------------------------------------------------------------- formats

def parse_hrc(data):
    """[(name, parent, length)] in file order."""
    t = data.decode('latin1').replace('\r', '')
    m = re.search(r':BONES\s+(\d+)', t)
    n = int(m.group(1)) if m else -1
    bones = []
    for b in t.split('\n\n')[1:]:
        L = [x.strip() for x in b.strip('\n').split('\n')]
        if len(L) >= 4:
            bones.append((L[0], L[1], float(L[2])))
    if n != len(bones):
        raise ValueError('%s.hrc: %d bones read, header says %d'
                         % (HRC, len(bones), n))
    return bones


def parse_a(data):
    _v, nf, nb = struct.unpack_from('<3I', data, 0)
    if len(data) != 36 + nf * (24 + 12 * nb):
        raise ValueError('animation size %d for %d frames x %d bones'
                         % (len(data), nf, nb))
    off, frames = 36, []
    for _ in range(nf):
        rr = struct.unpack_from('<3f', data, off)
        rt = struct.unpack_from('<3f', data, off + 12)
        br = [struct.unpack_from('<3f', data, off + 24 + 12 * i)
              for i in range(nb)]
        frames.append((rr, rt, br))
        off += 24 + 12 * nb
    return nb, frames


def write_a(header, nb, frames):
    out = bytearray(header[:36])
    struct.pack_into('<3I', out, 0, 1, len(frames), nb)
    for rr, rt, br in frames:
        out += struct.pack('<3f', *rr) + struct.pack('<3f', *rt)
        for r in br:
            out += struct.pack('<3f', *r)
    return bytes(out)


# ------------------------------------------------------------- pose math
# A bone's matrix is parent_end . Ry(y) . Rx(x) . Rz(z) (stored x, y, z in
# degrees); its children start at matrix . T(0, 0, -length).

def _mul(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def _tr(A):
    return [list(r) for r in zip(*A)]


def _R(axis, deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    if axis == 'x':
        return [[1, 0, 0], [0, c, -s], [0, s, c]]
    if axis == 'y':
        return [[c, 0, s], [0, 1, 0], [-s, 0, c]]
    return [[c, -s, 0], [s, c, 0], [0, 0, 1]]


def rot(r):
    x, y, z = r
    return _mul(_mul(_R('y', y), _R('x', x)), _R('z', z))


def decompose(M):
    x = math.degrees(math.asin(max(-1.0, min(1.0, -M[1][2]))))
    z = math.degrees(math.atan2(M[1][0], M[1][1]))
    y = math.degrees(math.atan2(M[0][2], M[2][2]))
    return tuple(float(a % 360.0) for a in (x, y, z))


def _world(bones, frame):
    """[(parent end rotation, parent end position, bone rotation)]."""
    rr, rt, br = frame
    ends = {'root': (rot(rr), list(rt))}
    out = []
    for i, (name, parent, ln) in enumerate(bones):
        PR, PP = ends[parent]
        M = _mul(PR, rot(br[i]))
        out.append((PR, PP, M))
        ends[name] = (M, [PP[k] - M[k][2] * ln for k in range(3)])
    return out


def _aim(d, side):
    """Rotation whose -Z (the bone's direction) is d, +X toward `side`."""
    n = math.sqrt(sum(v * v for v in d))
    z = [-v / n for v in d]
    x = [side, 0.0, 0.0]
    dot = sum(a * b for a, b in zip(x, z))
    x = [a - dot * b for a, b in zip(x, z)]
    n = math.sqrt(sum(v * v for v in x))
    x = [v / n for v in x]
    y = [z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2],
         z[0] * x[1] - z[1] * x[0]]
    return [[x[i], y[i], z[i]] for i in range(3)]


def pose_frame(bones, frame, swing=COLLAR_SWING, out=ARM_OUT):
    idx = {b[0]: i for i, b in enumerate(bones)}
    rr, rt, br = frame
    br = [tuple(b) for b in br]
    C = _R('x', swing)
    for name in COLLAR_BONES:
        i = idx[name]
        PR, _pp, M = _world(bones, (rr, rt, br))[i]
        br[i] = decompose(_mul(_tr(PR), _mul(C, M)))
    w = _world(bones, (rr, rt, br))
    hip_x = w[idx['hip']][1][0]
    t = math.radians(out)
    for up, fo, hand in ARMS:
        i = idx[up]
        side = 1.0 if w[i][1][0] >= hip_x else -1.0
        d = (side * math.sin(t), math.cos(t), 0.0)     # model +Y is down
        PR = _world(bones, (rr, rt, br))[i][0]
        br[i] = decompose(_mul(_tr(PR), _aim(d, side)))
        br[idx[fo]] = (0.0, 0.0, 0.0)
        br[idx[hand]] = (0.0, 0.0, 0.0)
    return (rr, rt, br)


# ------------------------------------------------------------- char.lgp

def plan_char(get):
    """({entry: bytes}, note). Raises when nothing safe can be written."""
    hrc, old = get(HRC + '.hrc'), get(OLD_ANIM + '.a')
    if hrc is None or old is None:
        raise ValueError('no %s.hrc or %s.a' % (HRC, OLD_ANIM))
    bones = parse_hrc(hrc)
    nb_old, _f = parse_a(old)
    if len(bones) == nb_old:
        return ({NEW_ANIM + '.a': old},
                '%s.hrc has %d bones like %s.a: %s.a is a copy of it'
                % (HRC, len(bones), OLD_ANIM, NEW_ANIM))
    src = get(SRC_ANIM + '.a')
    if src is None:
        raise ValueError('no %s.a' % SRC_ANIM)
    nb, frames = parse_a(src)
    names = [b[0] for b in bones]
    if nb != len(bones) or len(bones) != 23 or names[3:5] != ['none1',
                                                              'hair']:
        raise ValueError('%s.hrc %d bones %s, %s.a %d bones'
                         % (HRC, len(bones), names[3:5], SRC_ANIM, nb))
    new = [pose_frame(bones, f) for f in frames]
    return ({NEW_ANIM + '.a': write_a(src, nb, new)},
            '%s.a = %s with the cape hanging and the arms down (%d frames)'
            % (NEW_ANIM, SRC_ANIM, len(new)))


def apply_to_char(path, log=lambda *_: None):
    import lgp
    global READY
    READY = False
    st = {'written': False, 'added': [], 'note': '', 'refused': None}
    if disabled():
        return st
    try:
        a = lgp.Archive(path)

        def get(n):
            e = a.index.get(n)
            return e['payload'] if e else None
        want, st['note'] = plan_char(get)
        for name, data in sorted(want.items()):
            have = get(name)
            if have == data:
                continue
            if have is not None:
                a.index[name]['payload'] = data
            else:
                a.add(name, data)
            st['added'].append(name)
        if st['added']:
            tmp = path + '.onnafix-tmp'
            a.write(tmp)
            os.replace(tmp, path)
            st['written'] = True
        READY = True
    except Exception as exc:                                   # noqa: BLE001
        st['refused'] = str(exc)[:160]
    return st


def summarise_char(st):
    if st.get('refused'):
        return ('  ! floor-60 caped women (BUILD 717): char.lgp not changed '
                '-- %s' % st['refused'])
    if st.get('note'):
        return ('  FLOOR-60 CAPED WOMEN (BUILD 717): %s%s.'
                % (st['note'], '' if st.get('added') else ' (already there)'))
    return ''


# ------------------------------------------------------------- flevel.lgp

def patch_loader(sec3):
    """(model loader with the EDEA records' BOCB.aki -> ONNA.aki, count).
    Raises unless the walk ends exactly at the section end."""
    s = bytearray(sec3)
    _v, n, _s = struct.unpack_from('<HHH', s, 0)
    off, done = 6, 0
    for _ in range(n):
        ln = struct.unpack_from('<H', s, off)[0]
        off += 2 + ln + 2
        hrc = bytes(s[off:off + 8]).split(b'\0')[0].split(b'.')[0].lower()
        off += 8 + 4
        na = struct.unpack_from('<H', s, off)[0]
        off += 2 + 30
        for _a in range(na):
            al = struct.unpack_from('<H', s, off)[0]
            nm = bytes(s[off + 2:off + 2 + al]).split(b'\0')[0]
            if (hrc == HRC.encode() and
                    nm.split(b'.')[0].lower() == OLD_ANIM.encode()):
                s[off + 2:off + 6] = NEW_ANIM.upper().encode('ascii')
                done += 1
            off += 2 + al + 2
    if off != len(s):
        raise ValueError('model loader is %d bytes, walked %d'
                         % (len(s), off))
    return bytes(s), done


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'fields': [], 'records': 0, 'refused': [], 'skipped': ''}
    if disabled():
        return st
    if not READY:
        st['skipped'] = 'char.lgp does not hold %s.a' % NEW_ANIM
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
            new3, done = patch_loader(parts[2])
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:100]))
            continue
        if not done:
            continue
        parts[2] = new3
        payloads[name] = encode(lgp.join_sections(parts))
        st['fields'].append(name)
        st['records'] += done
    return st


def summarise(st):
    out = []
    if st.get('fields'):
        out.append('  FLOOR-60 CAPED WOMEN (BUILD 717): %s: %d model-loader '
                   'record(s) play %s instead of %s. %s=1 disables.'
                   % (', '.join(st['fields']), st['records'],
                      NEW_ANIM.upper(), OLD_ANIM.upper(), OFF_ENV))
    if st.get('skipped'):
        out.append('  floor-60 caped women: loader left as it was -- %s'
                   % st['skipped'])
    for name, why in st.get('refused', ()):
        out.append('  ! floor-60 caped women %s: not applied -- %s'
                   % (name, why))
    return '\n'.join(out)
