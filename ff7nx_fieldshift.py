#!/usr/bin/env python3
"""ff7nx_fieldshift.py -- draw md_e1's 3D models lower; background untouched.

BUILD 618e. md_e1 (the ending: the crowd sheltering under the plate): the
characters stand a little above the floor painted in the background (user
report). The user wants WHERE THE CHARACTERS ARE DRAWN to slide down while
the background stays exactly where it is -- an intentional displacement, so
the models look natural on the picture. Only md_e1 is changed (fields that
look like it are not).

History:
  * 618a moved the background tiles up -- wrong, the background must not move.
  * 618b set the camera block's 2D pan (pan_y = +3). On the PC exe pan_y is
    added only to the 3D world coordinate (x86 0x6447B0 -> 0xCFF56C), but
    that coordinate is also the 3D viewport origin the engine hands to the
    renderer (0x640EDF -> 0x661B68 -> 0x67CCDE). On the Switch the whole
    field moved with it and a black bar opened at the top (hardware, 10-01).

HOW (618e). The 2D world coordinate is left alone; the CAMERA is tilted
down by a fraction of a degree instead. A pitch of theta about the camera's
own X axis moves a point's screen y by ~zoom*theta*(1+(y/zoom)^2), i.e. by
the same amount for every model standing on the floor (md_e1's camera sits
at floor height, so the whole floor projects onto one screen line), and its
screen x by well under a unit. theta is solved on the int16 (1/4096) camera
vectors and int32 translation the engine actually reads, so the walkmesh
moves down by the requested number of field units on screen (mean over its
triangles). The background, its scroll and every 2D coordinate are
untouched: backgrounds are not projected through the camera.

TILTS lists field -> units down; SEVENTH_NX_FIELD_PAN=md_e1=4 overrides (the
old variable name is kept), =0 turns it off. Only a 38-byte single-camera
block whose pan is (0,0) is changed.
"""
from __future__ import annotations

import os
import struct

import numpy as np

ENV = 'SEVENTH_NX_FIELD_PAN'
TILTS = {'md_e1': 3}           # screen units the models move DOWN
SEC_CAM = 1
SEC_WALK = 4
CAM_FMT = '<9hh3ihhh'          # vx vy vz, vz.z copy, ox oy oz, pan x/y, zoom


def pans(env=None):
    raw = (os.environ if env is None else env).get(ENV, '').strip().lower()
    if raw in ('0', 'off', 'no', 'false'):
        return {}
    if not raw:
        return dict(TILTS)
    out = {}
    for item in raw.split(','):
        if '=' in item:
            k, v = item.split('=', 1)
            try:
                out[k.strip()] = float(v)
            except ValueError:
                pass
    return {k: v for k, v in out.items() if v}


def _walk_points(walk):
    n = struct.unpack_from('<I', walk, 0)[0]
    if n == 0 or len(walk) < 4 + 24 * n:
        raise ValueError('no walkmesh')
    v = np.array([struct.unpack_from('<12h', walk, 4 + 24 * t)
                  for t in range(n)], np.float64).reshape(n, 3, 4)[:, :, :3]
    return v.mean(1)


def _project(R, T, f, pts):
    q = pts @ R.T + T
    z = np.where(q[:, 2] > 1, q[:, 2], np.nan)
    return f * q[:, 0] / z, f * q[:, 1] / z


def plan_camera(sec1, n, walk):
    """(new camera block, measured mean dy, max |dx|) -- raises on doubt."""
    if len(sec1) != 38:
        raise ValueError('camera block is %d bytes, not one camera' % len(sec1))
    if not 0 < abs(n) <= 16:
        raise ValueError('shift %r out of range' % n)
    c = list(struct.unpack_from(CAM_FMT, sec1, 0))
    if (c[13], c[14]) != (0, 0):
        raise ValueError('camera pan is (%d, %d), not (0, 0)' % (c[13], c[14]))
    R = np.array(c[0:9], np.float64).reshape(3, 3) / 4096.0
    T = np.array(c[10:13], np.float64)
    f = float(c[15])
    pts = _walk_points(walk)
    x0, y0 = _project(R, T, f, pts)
    if np.isfinite(y0).sum() < 3:
        raise ValueError('walkmesh not in front of the camera')
    yc = float(np.nanmedian(y0))
    th = n / (f * (1.0 + (yc / f) ** 2))
    for _ in range(4):
        cs, sn = np.cos(th), np.sin(th)
        rx = np.array([[1, 0, 0], [0, cs, sn], [0, -sn, cs]])
        Ri = np.rint(rx @ R * 4096.0)
        Ti = np.rint(rx @ T)
        x1, y1 = _project(Ri / 4096.0, Ti, f, pts)
        dy = float(np.nanmean(y1 - y0))
        if not np.isfinite(dy) or dy == 0:
            raise ValueError('tilt moved the walkmesh behind the camera')
        th *= n / dy
    dx = float(np.nanmax(np.abs(x1 - x0)))
    if abs(dy - n) > 0.25 * abs(n):
        raise ValueError('tilt reached %.2f units, wanted %s' % (dy, n))
    vals = ([int(v) for v in Ri.reshape(-1)] + [int(Ri[2, 2])]
            + [int(v) for v in Ti] + c[13:16])
    if max(abs(v) for v in vals[:10]) > 32767:
        raise ValueError('camera vector out of int16 range')
    out = bytearray(sec1)
    struct.pack_into(CAM_FMT, out, 0, *vals)
    return bytes(out), dy, dx


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'fields': [], 'refused': []}
    todo = pans()
    if not todo:
        return st
    encode = encode or archive.encode_field
    for name, n in sorted(todo.items()):
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[SEC_CAM], dy, dx = plan_camera(parts[SEC_CAM], n,
                                                 parts[SEC_WALK])
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:80]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['fields'].append('%s: models %.2f down (x drift <= %.2f)'
                            % (name, dy, dx))
    return st


def summarise(st):
    out = []
    if st.get('fields'):
        out.append('  FIELD MODEL SHIFT (BUILD 618e): camera tilted so the 3D '
                   'models draw lower on an unmoved background (%s). %s=0 '
                   'disables.' % ('; '.join(st['fields']), ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! field model shift %s: %s' % (name, why))
    return '\n'.join(out)
