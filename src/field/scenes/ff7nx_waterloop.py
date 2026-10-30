#!/usr/bin/env python3
"""mogu_1's water: a 16-frame loop with no held frames. BUILD 776, redone 778.

HARDWARE 10-21. The stream at the bottom right of mogu_1 "always pauses
after it animates, then animates again". MEASURED on the capture (the water
alone, 60 fps): it steps every 10 frames, but twice per 159-frame cycle a
frame is held for 20.

WHY. The water is 16 frames of average-blended layer-2 tiles, params 1 and
2, states 1..128 each, all at one depth (ID 4094). The script (mogu_1,
entity block 0x678) keeps TWO consecutive frames on -- BGOFF k-1, BGON k+1,
WAIT 5 -- meaning a cross-fade. On the console two equal-depth tiles do not
mix: the FIRST record drawn wins where it is opaque and the other only fills
what it leaves uncovered. Per tile the records run 2:7..2:4, 1:7..1:4,
2:3..2:0, 1:3..1:0, so at two of the sixteen pairs the winner is the frame
that was already showing (held).

BUILD 776 TRIED ONE FRAME PER STEP. WRONG, hardware 10-22: "this water fx
layer is completely missing this portion". A single frame covers only part
of the stream; the PAIR is what covers all of it. The pair stays.

FIX (BUILD 778), the pair kept and the WINNER made the newer frame:

  1. records: each tile's 16 water records are re-dealt over the same 16
     record slots newest-first (2:7..2:0, 1:7..1:0), so in every pair
     (k, k+1) the newer frame k+1 is drawn first and shows; the older one
     only fills under it -- exactly the coverage the cross-fade had.
  2. the one pair that cannot follow that order is the wrap (2:7, 1:0).
     A COPY of frame 1:0 (param 4, state bit 0) is drawn ahead of every
     water record and the wrap step turns the copy on instead of 1:0
     (`BGON 1 0` -> `BGON 4 0`), so the wrap shows 1:0's picture over 2:7.
  3. at the loop head the copy is swapped back for the real 1:0 (BGOFF 4 0,
     BGON 1 0) so the next pair (1:0, 1:1) shows 1:1 in its turn. Those 8
     bytes take the place of the loop's four LDPALs (palettes 2/3/10/11),
     which only the water records use and which are dead once pages 24/25
     are truecolor -- checked at build time, refused otherwise. The other
     12 bytes are NOPs, so no jump target moves.

Every frame now shows once per cycle in order; the cycle length is
unchanged. SEVENTH_NX_NO_WATERLOOP=1 disables.

BUILD 860c: ONE FRAME AT A TIME, AND WHY 776 WAS RIGHT AFTER ALL. Hardware
10-29 (a 60 fps capture, the water matched picture by picture against the
frame sim): with 778's pairs the console showed 1:0, 1:4 and 2:4 for 20
frames each, never showed 1:3 or 2:3, and flashed 1:1 for one frame before
1:0 -- the stream still paused three times a cycle. Which of two equal-depth
frames wins on the console is not the record order 778 assumed. And the
reason 776's one-frame loop was abandoned ("a whole portion of the water
missing", 10-22) was not the loop: it was the driver's field depth copy
drawing through the screen colour target (ff7nx_depthcopy, BUILD 860h),
present with any loop until Mog walks out. Every one of the 16 frames covers the whole stream
(measured on the built pages: the same coverage per tile in every frame),
so ONE frame on at a time needs no winner at all:

  init      BGON 1:0, BGON 1:0           (was BGON 1:0, BGON 1:1)
  step m    BGOFF frame m, BGON frame m+1 (was BGON frame m+2)

16 pictures, each on for exactly 5 ticks, in order, every cycle; the cycle
is the vanilla 80 ticks. In-place operand edits only (same opcodes, same
size, no jump target moves). The records, the palettes and the LDPALs are
left exactly as the archive has them; no copy records.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_WATERLOOP'
FIELDS = ('mogu_1',)
FRAMES = [(1, i) for i in range(8)] + [(2, i) for i in range(8)]
COPY = (4, 0)            # 778's duplicate of frame 1:0 (gone since 860c;
#                          round_corner still clears it if a payload has it)
WATER_PAGES = (24, 25)
WATER_PALS = (2, 3, 10, 11)
REC = 52


def _op(code, param, bit):
    return bytes((code, 0, param, bit))


def _loop_pairs(wrap):
    """The vanilla 16-step loop body: BGOFF k / BGON k+2 / WAIT 5 (no WAIT
    last); `wrap` is what step 14 turns on."""
    out = b''
    for m in range(16):
        on = FRAMES[(m + 2) % 16]
        if m == 14:
            on = wrap
        out += _op(0xE1, *FRAMES[m]) + _op(0xE0, *on)
        if m < 15:
            out += b'\x24\x05\x00'
    return out


def _loop_one():
    """BUILD 860c: BGOFF k / BGON k+1 / WAIT 5 (no WAIT last)."""
    out = b''
    for m in range(16):
        out += _op(0xE1, *FRAMES[m]) + _op(0xE0, *FRAMES[(m + 1) % 16])
        if m < 15:
            out += b'\x24\x05\x00'
    return out


LOOP_OLD, LOOP_ONE = _loop_pairs(FRAMES[0]), _loop_one()
# the loop head: LDPAL 1->2, 3->3, 5->10, 7->11 (16 colours each), WAIT 4
HEAD_OLD = (bytes.fromhex('e60001020f' 'e60003030f' 'e600050a0f'
                          'e600070b0f') + b'\x24\x04\x00')
# the entity's init: BGON 1:0, BGON 1:1, RET, then the first STPAL
INIT_OLD = _op(0xE0, 1, 0) + _op(0xE0, 1, 1) + bytes.fromhex('00e5000200')
INIT_ONE = _op(0xE0, 1, 0) + _op(0xE0, 1, 0) + bytes.fromhex('00e5000200')
assert len(LOOP_OLD) == len(LOOP_ONE) and len(INIT_OLD) == len(INIT_ONE)


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def fix_script(sec0):
    """(section 0, changed). Refuses unless both sites are there once."""
    if (sec0.count(HEAD_OLD + LOOP_ONE) == 1
            and sec0.count(INIT_ONE) == 1):
        return sec0, False                          # already done
    blk = HEAD_OLD + LOOP_OLD
    if sec0.count(blk) != 1:
        raise ValueError('the water loop is not where expected')
    if sec0.count(INIT_OLD) != 1:
        raise ValueError('the water init is not where expected')
    out = sec0.replace(blk, HEAD_OLD + LOOP_ONE).replace(INIT_OLD, INIT_ONE)
    if len(out) != len(sec0):
        raise ValueError('size changed')
    return out, True


# BUILD 780. User, 10-22, on the frame-sim picture of the stream: "i would
# like this corner to be less sharp, a little bit more rounded" -- the water's
# upper-left corner, where its straight top edge (field y ~112.5) meets its
# 45-degree left end (x = -31.7 - (y - 113)), a 135-degree point at about
# (-31.2, 112.5) in every one of the 16 frames (measured on the built pages).
# Every water texel outside a circular fillet of radius CORNER_R tangent to
# both edges is cleared (0x0000, the cut-out key), in each frame's own cell.
CORNER = {'apex': (-31.2, 112.5), 'u1': (1.0, 0.0),
          'u2': (-0.70710678, 0.70710678)}
CORNER_R = 18.0


def _in_tri(x, y, a, b, c):
    def side(p, q):
        return (q[0] - p[0]) * (y - p[1]) - (q[1] - p[1]) * (x - p[0])
    s1, s2, s3 = side(a, b), side(b, c), side(c, a)
    return ((s1 >= 0) & (s2 >= 0) & (s3 >= 0)) | \
        ((s1 <= 0) & (s2 <= 0) & (s3 <= 0))


def round_corner(sec9):
    """(section 9, texels cleared). Water cells under the corner only."""
    import diag_common as DC
    import ff7nx_floordepth as FD
    import field_bg_native as FN
    pl, _ts, _te, px = DC.parse_pages(sec9)
    pages = {p.slot: p for p in pl if p is not None}
    k = px // 16
    ax, ay = CORNER['apex']
    u1, u2 = np.array(CORNER['u1']), np.array(CORNER['u2'])
    b = (u1 + u2) / np.linalg.norm(u1 + u2)
    half = np.arccos(np.clip(u1 @ u2, -1, 1)) / 2.0
    cxy = np.array([ax, ay]) + b * (CORNER_R / np.sin(half))
    t1 = np.array([ax, ay]) + u1 * (CORNER_R / np.tan(half))
    t2 = np.array([ax, ay]) + u2 * (CORNER_R / np.tan(half))
    users = {}
    recs = []
    for _lay, _c, first, n in FD._rows(sec9):
        for i in range(n):
            o = first + i * REC
            r = sec9[o:o + REC]
            u, v = struct.unpack_from('<II', r, 42)
            pg = r[34] if r[28] else r[32]
            key = (pg, u // 625000, v // 625000)
            pos = struct.unpack_from('<hh', r, 2)
            users.setdefault(key, set()).add(pos)
            if r[28] and r[34] in WATER_PAGES and r[26] in (1, 2, COPY[0]):
                recs.append((key, pos))
    datas = {}
    cleared = 0
    t = (np.arange(k) + 0.5) * 16.0 / k
    done = set()
    for key, (x, y) in recs:
        if key in done:
            continue
        done.add(key)
        fx = x + t[None, :]
        fy = y + t[:, None]
        dx, dy = fx - cxy[0], fy - cxy[1]
        tri = _in_tri(fx, fy, (ax, ay), t1, t2)
        out = tri & (dx * dx + dy * dy > CORNER_R * CORNER_R)
        if not out.any():
            continue
        if len(users[key]) != 1:
            raise ValueError('water cell %r drawn at two places' % (key,))
        pg, cx, cy = key
        if pages[pg].depth != 2:
            raise ValueError('water page %d is not truecolor' % pg)
        if pg not in datas:
            datas[pg] = np.frombuffer(pages[pg].data, '<u2').reshape(
                px, px).copy()
        blk = datas[pg][cy * k:(cy + 1) * k, cx * k:(cx + 1) * k]
        hit = out & (blk != 0)
        cleared += int(hit.sum())
        blk[hit] = 0
    if not cleared:
        return sec9, 0
    plist, t0, t1_ = FN.parse_texture_block(sec9, px)
    for pg, d in datas.items():
        q = plist[pg]
        plist[pg] = FN.Page(pg, q.size_flag, 2, d.tobytes(), q.px)
    return FN.replace_texture_block(sec9, plist, t0, t1_), cleared


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': []}
    if disabled():
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
            new0, changed = fix_script(parts[0])
            if changed:
                parts[0] = new0
            try:
                parts[8], c = round_corner(parts[8])
            except Exception as exc:                           # noqa: BLE001
                c = 0
                st['refused'].append((name + ' corner', str(exc)[:100]))
            st['corner'] = st.get('corner', 0) + c
            if not changed and not c:
                continue
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:100]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['names'].append(name)
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  WATER LOOP (BUILD 860c): %s -- one frame on at a time, '
                   'each of the 16 for 5 ticks in order. %s=1 disables.'
                   % (', '.join(st['names']), OFF_ENV))
        if st.get('corner'):
            out.append('  WATER CORNER (BUILD 780): %d texels outside the '
                       'rounded upper-left corner cleared.' % st['corner'])
    for name, why in st.get('refused', ()):
        out.append('  ! water loop %s: %s' % (name, why))
    return '\n'.join(out)
