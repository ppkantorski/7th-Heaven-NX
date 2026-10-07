#!/usr/bin/env python3
"""ff7nx_trainlower.py -- lower the coal train (layers 1, 2 and 4, and the 3D
models) just enough that its shake never reveals the bottom edge.

BUILD 618n. zcoal_1, hardware 10-03.
  * The train shakes: SHAKE type 2, y amplitude 4. Measured on the 10-02
    capture: the picture moves by 0, ±1, ±2, ±3 units, symmetric around rest.
  * Layers 1/2 (the train) end at y=128, exactly the bottom of the 240-unit
    view, so at the top of each bounce a strip with nothing behind it showed.
  * 618m's extra copied row (ff7nx_shakepad) drew "garbage" there: a row
    copied from above does not continue the picture.

FIX (the user's design): layers 1, 2 and 4 (the train, the wagon and the
trestle) move DOWN by the shake amplitude (4 units, the script's own bound),
and the camera is tilted so the 3D models move down by the same amount
(ff7nx_fieldshift.plan_camera, the md_e1 mechanism confirmed on hardware).
  * Layer 3 (the mountains) and the camera range are unchanged.
  * At rest the bottom 4 units of the train are just below the screen; at
    the highest point of a bounce the bottom-most row is exactly visible.
  * No art is invented and no page is added.
Refused (field byte-identical) unless every camera in section 1 is an
unpanned camera and its tilt lands within 25% of the target.

BUILD 618o: zcoal_2 (the next train field; hardware 10-03 showed the same
bottom strip) added. Its section 1 holds two identical cameras; both are
tilted (models 3.95 units down, x drift 0.12).

SEVENTH_NX_NO_TRAIN_LOWER=1 disables.
"""
from __future__ import annotations

import os
import struct

import diag_common as DC
import ff7nx_parallaxfill as PF

OFF_ENV = 'SEVENTH_NX_NO_TRAIN_LOWER'
FIELDS = {'zcoal_1': 4, 'zcoal_2': 4}
LAYERS = (1, 2, 4)


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def plan(parts, d):
    """(new section 1, new section 9, info) -- raises on any doubt."""
    import ff7nx_fieldshift as FSH
    # BUILD 618o: section 1 may hold several 38-byte cameras (zcoal_2 has
    # two, identical); each is tilted on its own, all must succeed.
    cams = parts[FSH.SEC_CAM]
    if not cams or len(cams) % 38:
        raise ValueError('camera section is %d bytes' % len(cams))
    out1, dys, dxs = b'', [], []
    for k in range(len(cams) // 38):
        blk, dy_, dx_ = FSH.plan_camera(cams[k * 38:(k + 1) * 38], d,
                                        parts[FSH.SEC_WALK])
        out1 += blk
        dys.append(dy_)
        dxs.append(dx_)
    sec1, dy, dx = out1, min(dys), max(dxs)
    sec9 = parts[8]
    survey = DC.survey(sec9)
    buf = bytearray(sec9)
    moved = {}
    for layer, _c, first, n in PF._layers(sec9, survey['back_start'],
                                          survey['tex_start']):
        if layer not in LAYERS:
            continue
        for i in range(n):
            o = first + i * 52 + 4
            struct.pack_into('<h', buf, o, struct.unpack_from('<h', buf, o)[0]
                             + d)
        moved[layer] = n
    if 1 not in moved:
        raise ValueError('no layer 1')
    return sec1, bytes(buf), {'models_dy': round(dy, 2),
                              'models_dx': round(dx, 2), 'records': moved}


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    total = {'names': [], 'refused': []}
    if disabled():
        return total
    encode = encode or archive.encode_field
    for name, d in FIELDS.items():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[1], parts[8], info = plan(parts, d)
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append((name, str(exc)[:80]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['names'].append('%s: layers 1/2/4 +%d, models %.2f down'
                              % (name, d, info['models_dy']))
    return total


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  TRAIN LOWER (BUILD 618n): the shaking coal train lowered '
                   'by its shake amplitude, mountains unchanged (%s). %s=1 '
                   'disables.' % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! train lower %s: %s' % (name, why))
    return '\n'.join(out)
