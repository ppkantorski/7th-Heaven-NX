#!/usr/bin/env python3
"""ff7nx_destrobe.py -- stop the every-frame palette strobe on forest/mist
light beams.

BUILD 620f. anfrst_1 (the Sleeping Forest), hardware 10-07: "the lights
are constantly flickering ... forest lights ... looks very strange at
60hz". MEASURED on the capture: the beams alternate bright/dim with a
3-frame period at 60 fps (a 20 Hz strobe) while the rest of the picture is
flat. The script: entity `klight` stores palettes 12/13, then loops
    ADPAL -1,-1,-1 -> LDPAL 12/13 -> WAIT 1 -> ADPAL 0 -> LDPAL 12/13 -> BACK
-- a one-step brightness toggle every frame. On the port those palettes
draw 111 additive FX tiles still 256px paletted, so the toggle is visible.

The same loop (a scan of every field's scripts: a BACK loop whose palette
ops are constant ADPALs alternating between one offset and zero, <= 2
frames of WAIT per pass) exists in anfrst_1/2/3/5, jail1, jailin1 and
jailpb. Only anfrst_1 and jail1 draw it on paletted pages; the others'
beams are truecolor and already steady. Slower ramps (nvdun1, sbwy4_5,
ancnt2's pulse), fires and TVs do not match the rule and are untouched.

FIX, in place (section 1 size and every offset unchanged): the non-zero
ADPAL offsets in such a loop become 0, so every pass loads the unmodified
palette -- the beams hold their full, original brightness. The loop still
runs (it is two palette loads per frame, as before).
SEVENTH_NX_NO_DESTROBE=1 disables.
"""
from __future__ import annotations

import os
import struct

OFF_ENV = 'SEVENTH_NX_NO_DESTROBE'
FIELDS = ('anfrst_1', 'anfrst_2', 'anfrst_3', 'anfrst_5', 'jail1',
          'jailin1', 'jailpb')
OP_ADPAL, OP_LDPAL, OP_CPPAL, OP_STPAL, OP_WAIT = 0xE9, 0xE6, 0xE7, 0xE5, 0x24
OP_BACK, OP_BACKL = 0x12, 0x13
MAX_WAIT = 2


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def strobe_sites(s1):
    """[(loop start, back addr, [ADPAL addrs to zero])] -- every loop that
    toggles a palette between a constant offset and zero each pass."""
    import field_dis as FD
    meta, _names, entries = FD.parse(s1)
    end = meta['string_table']
    starts = sorted(set(o for r in entries for o in r) | {end})
    out, seen = [], set()
    for off in starts[:-1]:
        if off in seen:
            continue
        seen.add(off)
        nxt = min(x for x in starts if x > off)
        ws = FD.walk(s1, off, nxt)
        if any(w[4] for w in ws):
            continue
        for a, _n, size, raw, _e in ws:
            if not raw or raw[0] not in (OP_BACK, OP_BACKL):
                continue
            j = raw[1] if raw[0] == OP_BACK else struct.unpack_from(
                '<H', raw, 1)[0]
            tgt = a - j
            body = [(wa, bytes(wr)) for wa, _wn, _ws, wr, _we in ws
                    if tgt <= wa < a]
            if not body or body[0][0] != tgt:
                continue
            ops = {r[0] for _wa, r in body}
            if not ops <= {OP_ADPAL, OP_LDPAL, OP_WAIT}:
                continue
            adp = [(wa, r) for wa, r in body if r[0] == OP_ADPAL]
            if len(adp) < 2 or OP_LDPAL not in ops:
                continue
            if any(r[1] or r[2] or r[3] for _wa, r in adp):
                continue                          # variable-driven: a ramp
            wait = sum(struct.unpack_from('<H', r, 1)[0]
                       for _wa, r in body if r[0] == OP_WAIT)
            if wait > MAX_WAIT:
                continue
            offs = {r[6:9] for _wa, r in adp}
            if len(offs) != 2 or b'\0\0\0' not in offs:
                continue
            out.append((tgt, a, [wa for wa, r in adp if r[6:9] != b'\0\0\0']))
    return out


def plan(s1):
    """(new section 1, sites) -- raises when there is nothing to do."""
    sites = strobe_sites(bytes(s1))
    if not sites:
        raise ValueError('no palette strobe loop')
    out = bytearray(s1)
    for _t, _b, addrs in sites:
        for a in addrs:
            out[a + 6:a + 9] = b'\0\0\0'
    out = bytes(out)
    if strobe_sites(out):
        raise ValueError('strobe still present')
    if len(out) != len(s1):
        raise ValueError('size changed')
    return out, sites


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': []}
    if disabled():
        return st
    encode = encode or archive.encode_field
    for name in FIELDS:
        if name not in archive.index:
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(archive.index[name]))
            parts = list(lgp.split_sections(raw))
            parts[0], sites = plan(parts[0])
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:80]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['names'].append('%s (%d ADPAL)' % (
            name, sum(len(s[2]) for s in sites)))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  DESTROBE (BUILD 620f): every-frame palette strobe held '
                   'at full brightness: %s. %s=1 disables.'
                   % (', '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! destrobe %s: unchanged -- %s' % (name, why))
    return '\n'.join(out)
