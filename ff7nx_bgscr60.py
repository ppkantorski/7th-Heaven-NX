#!/usr/bin/env python3
"""ff7nx_bgscr60.py -- scripted layer scrolls (BGSCR) at their 30 fps pace.

BUILD 618z (2nd round). woa_2, hardware 10-05 (video, every frame read):
"the green fx layer ... appears on the right, slides in then magically
appears on the left ... jumping across the screen unnaturally".

WHAT THE VIDEO SHOWS. The green band is woa_2's layer 4 (one 112-unit band
on a 640-wide layer), moved by the script. It enters at the right edge,
moves left 10 units every 60 Hz frame, and at the screen's middle jumps
240 units left (to the left margin), fades out there, and 11 frames later
enters at the right again. Cycle: 41 frames.

WHY.
* SPEED. BGSCR adds its speed (1/16 unit) to the layer position every frame.
  FFNx at 60 fps divides all four BGSCR operands by the frame multiplier
  (field.cpp: replace_call_function(execute_opcode_table[BGSCR] + 0x34/0x4D/
  0x68/0x81, ff7_opcode_divide_get_bank_value)); the port does not, so every
  scripted layer scroll runs at twice its 1997 speed (woa_2's band at 10
  units a frame instead of 5). Halving a CONSTANT operand in the script is
  exactly FFNx's integer divide, done at build time.
* THE JUMP. woa_2's loop is "move left 10/frame for 40 frames, then one
  frame of speed -400" -- the band sweeps 400 units and snaps back to its
  start. At 4:3 (320 wide) the band is off screen at both ends of that
  sweep; at 16:9 (427 wide, plus the band's 112) it is not, so the snap is
  seen -- with the 640-unit wrap it reads as a jump to the left margin.
  The layer is 640 wide (more than 427 + 112), so it does not need the snap:
  with the snap removed the band simply keeps travelling and wraps once per
  640 units, entering and leaving off screen. woa_3 has the same loop
  (20/frame for 42 frames, then -840) on a 1024-wide layer.

WHAT CHANGES (script section only, operands in place, same size):
* PERSISTENT fields: a layer whose every BGSCR is the same constant
  non-zero speed (an endless backdrop scroll -- snow, haze, clouds, the
  train-tunnel walls): that speed is halved (truncating, as FFNx does).
  Layers that start, stop or change speed are left alone: their end
  position depends on how the move is timed.
* SWEEP fields (woa_2, woa_3), layer 4: the loop's snap-back (a BGSCR
  followed directly by the loop's JMPB) becomes 0, and the travel speed is
  halved. The one-time start offset (a BGSCR followed by WAIT) is kept.
Every edit is checked: the operand must hold the vanilla value first.

SEVENTH_NX_NO_BGSCR60=1 disables.
"""
from __future__ import annotations

import collections
import os
import struct

OFF_ENV = 'SEVENTH_NX_NO_BGSCR60'
OP_BGSCR = 0x2D
OP_JMPB = 0x12
OP_WAIT = 0x24
SNAP_MIN = 2000                # |speed| at or above this is a snap, not travel

# from a scan of every field script (vanilla): a layer with exactly one
# distinct constant non-zero BGSCR and no stop
PERSISTENT = (
    'fship_1', 'fship_12', 'hyou1', 'hyou10', 'hyou11', 'hyou13_1', 'hyou2',
    'hyou3', 'hyou4', 'hyou5_1', 'hyou5_2', 'hyou5_4', 'hyou6', 'hyou7',
    'hyou8_1', 'hyou9', 'kuro_1', 'loslake1', 'move_d', 'move_f', 'move_i',
    'move_r', 'move_s', 'move_u', 'rcktin7', 'sky', 'trnad_1', 'trnad_2',
    'trnad_3', 'trnad_4', 'woa_1', 'woa_2', 'woa_3', 'zcoal_1', 'zcoal_2',
    'ztruck')
SWEEP = ('woa_2', 'woa_3')


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _half(v):
    return int(v / 2)                          # C: truncate toward zero


def _ops(sec):
    import echo_s_flevel as ES
    out = []
    for a, b in ES._routine_blocks(sec):
        stream, _ = ES._decode_block(sec, a, b)
        for i, (off, op, size) in enumerate(stream):
            if op != OP_BGSCR or size < 7:
                continue
            nxt = stream[i + 1][1] if i + 1 < len(stream) else None
            out.append((off, sec[off + 1], sec[off + 2] + 1,
                        struct.unpack_from('<hh', sec, off + 3), nxt))
    return out


def plan_field(name, sec):
    """(new script section, [(layer, old, new)]) -- raises when nothing to
    do or the script is not what the scan saw."""
    sec = bytearray(sec)
    ops = _ops(bytes(sec))
    if not ops:
        raise ValueError('no BGSCR')
    by_layer = collections.defaultdict(list)
    for o in ops:
        by_layer[o[2]].append(o)
    edits = []
    name = name.lower()
    for layer, lops in sorted(by_layer.items()):
        if name in SWEEP and layer == 4:
            for off, banks, _l, (sx, sy), nxt in lops:
                if banks:
                    raise ValueError('variable BGSCR in a sweep')
                if nxt == OP_JMPB and max(abs(sx), abs(sy)) >= SNAP_MIN:
                    new = (0, 0)
                elif (sx, sy) != (0, 0) and max(abs(sx), abs(sy)) < SNAP_MIN:
                    new = (_half(sx), _half(sy))
                else:
                    continue
                struct.pack_into('<hh', sec, off + 3, *new)
                edits.append((layer, (sx, sy), new))
            continue
        if name not in PERSISTENT:
            continue
        speeds = {(o[1],) + o[3] for o in lops}
        if len(speeds) != 1:
            continue
        banks, sx, sy = next(iter(speeds))
        if banks or (sx, sy) == (0, 0):
            continue
        new = (_half(sx), _half(sy))
        for off, _b, _l, _s, _n in lops:
            struct.pack_into('<hh', sec, off + 3, *new)
        edits.append((layer, (sx, sy), new))
    if not edits:
        raise ValueError('no constant scroll to scale')
    # the script must still decode to the same op stream
    if [o[0] for o in _ops(bytes(sec))] != [o[0] for o in ops]:
        raise ValueError('script no longer decodes the same')
    return bytes(sec), edits


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': []}
    if disabled():
        return st
    encode = encode or archive.encode_field
    for name in PERSISTENT:
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[0], edits = plan_field(name, parts[0])
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:90]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['names'].append('%s %s' % (name, ' '.join(
            'L%d %s->%s' % (l, '%d,%d' % a, '%d,%d' % b)
            for l, a, b in edits)))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  BGSCR 60 (BUILD 618z): constant scripted layer scrolls '
                   'halved to their 30 fps pace (FFNx divides BGSCR at 60), '
                   'woa_2/woa_3 band snap-back removed (%d field(s): %s). '
                   '%s=1 disables.' % (len(st['names']),
                                       '; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! bgscr60 %s: %s' % (name, why))
    return '\n'.join(out)
