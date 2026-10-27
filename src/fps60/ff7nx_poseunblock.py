#!/usr/bin/env python3
"""
ff7nx_poseunblock.py -- BUILD 648. Field scripts must not WAIT on a stand pose
that a mod turned into a long animation.

WHAT HAPPENED
=============
The Sector 5 item and materia shopkeepers (mds5_i, mds5_m) took ~4.5 s to
answer. Their talk script opens with ANIMX2 (0xBA) on animation 0 -- ACFE.a /
AAFE.a, the generic "stand" pose: ONE frame in the game. ff7-60fps-mod's
NinoFieldAnimation option ships both as a 234-frame idle, and 0xBA is the
blocking form (x86 0x6149A5: 0xAF continues at once, 0xBA holds the script
until the animation ends), so the line waited for the whole idle. The weapon
shop's keeper uses the non-blocking ANIME2 and answered at once.

28 fields do this with those poses (shopkeepers, innkeepers, Wutai's shops,
several Cloud moments), always as "snap to the stand pose, go on" -- which is
what the stock one-frame pose made it.

THE FIX
=======
Where the active mods turned a stand pose (stock <= 2 frames) into a long
animation (> 30), the blocking plays of THAT pose become non-blocking, one
opcode byte each, and the script runs on as it did with the stock pose.

BUILD 662: where it is the same pose (animation 0, no other idle set by
the entity -- see TO_IDLE), 0xBA AND the non-blocking 0xAF become ANIME2
0xAE -- play, do not wait, then BACK TO THE IDLE (animation state 5 -> 0, x86 0x61484A / the per-frame update
0x60D1D3). 648 turned 0xBA into ANIMX1 0xAF, which plays once and then
HOLDS THE LAST FRAME (state 6 -> 3): with a one-frame stock pose that is
"stand still", but with a 234-frame idle the model froze at its end -- the
shopkeeper stopped breathing after you talked to him, and the Idle
Animations clips stopped looping wherever a script snapped to the pose.
Back to the idle loops the model's idle animation (its stand pose) again,
which with the stock one-frame pose looks exactly like holding it. 0xBA and
0xAE take the same operands (animation, speed), set up by the same 0x6145FD.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import struct

# BUILD 662: blocking plays never wait; a play that would HOLD the pose's
# last frame (ANIMX2 0xBA, and the non-blocking ANIMX1 0xAF too) instead goes
# back to the idle -- only where that is the same pose: animation 0 (the
# idle every model starts with, x86 0x60C410) in an entity whose scripts
# never set another idle (opcode 0xA2, x86 0x614424 -> [0xCC0878 + model]).
# Elsewhere 0xBA keeps 648's ANIMX1 0xAF and 0xAF stays.
TWIN = {0xBA: 0xAF, 0xA3: 0xAE}       # 648: blocking -> non-blocking
TO_IDLE = {0xBA: 0xAE, 0xAF: 0xAE}    # 662: ... and back to the idle loop
SET_IDLE = 0xA2
VERSION = 662                         # in the flevel key: output changed
STOCK_MAX_FRAMES = 2
LONG_MIN_FRAMES = 30


def _frames(data):
    return struct.unpack_from('<I', data, 4)[0] if data and len(data) >= 12 else None


def long_poses(char_files, vanilla_char):
    """Stems ('acfe', ...) of stand poses the active mods made long.
    char_files: {lower name: (src, mod)} for char.lgp; vanilla_char: its
    vanilla archive path (or an lgp.Archive)."""
    import lgp
    if not char_files or not vanilla_char:
        return []
    van = lgp.Archive(vanilla_char) if isinstance(vanilla_char, str) else vanilla_char
    out = []
    for low, v in sorted(char_files.items()):
        if not low.endswith('.a'):
            continue
        e = van.index.get(low)
        if e is None or (_frames(e['payload']) or 99) > STOCK_MAX_FRAMES:
            continue
        src = v[0] if isinstance(v, (tuple, list)) else v
        try:
            with open(src, 'rb') as f:
                head = f.read(12)
        except OSError:
            continue
        n = _frames(head)
        if n is not None and n > LONG_MIN_FRAMES:
            out.append(low[:-2])
    return out


def _models(section3):
    """[[animation stem, ...] per model] from a field's model loader."""
    s, o = section3, 2
    n = struct.unpack_from('<H', s, o)[0]
    o += 4
    out = []
    for _ in range(n):
        nl = struct.unpack_from('<H', s, o)[0]
        o += 2 + nl + 2 + 8 + 4
        na = struct.unpack_from('<H', s, o)[0]
        o += 2 + 30
        anims = []
        for _ in range(na):
            al = struct.unpack_from('<H', s, o)[0]
            o += 2
            anims.append(s[o:o + al].decode('latin-1').lower().rsplit('.', 1)[0])
            o += al + 2
        out.append(anims)
    return out


def unblock(raw, stems):
    """(new raw field or None, [(entity, offset, old op)]) -- see the module."""
    import lgp
    import field_dis as FD
    if not stems:
        return None, []
    stems = set(stems)
    secs = lgp.split_sections(raw)
    s1 = bytearray(secs[0])
    try:
        models = _models(secs[2])
        meta, names, entries = FD.parse(bytes(s1))
    except Exception:                                       # noqa: BLE001
        return None, []
    code = FD.walk(bytes(s1), meta['code_start'], meta['string_table'])
    owner = {}
    for ai, row in enumerate(entries):
        for off in row:
            owner.setdefault(off, ai)
    model_of, cur = {}, None
    for off, n, size, raw_ins, err in code:
        cur = owner.get(off, cur)
        if n == 'char' and cur is not None and len(raw_ins) > 1:
            model_of.setdefault(cur, raw_ins[1])
    sets_idle, cur = set(), None
    for off, n, size, raw_ins, err in code:
        cur = owner.get(off, cur)
        if not err and raw_ins and raw_ins[0] == SET_IDLE and cur is not None:
            sets_idle.add(cur)
    changed, cur = [], None
    for off, n, size, raw_ins, err in code:
        cur = owner.get(off, cur)
        if err or size != 3 or not raw_ins or (
                raw_ins[0] not in TWIN and raw_ins[0] not in TO_IDLE):
            continue
        m = model_of.get(cur)
        if m is None or m >= len(models) or raw_ins[1] >= len(models[m]):
            continue
        if models[m][raw_ins[1]] not in stems:
            continue
        if raw_ins[0] in TO_IDLE and raw_ins[1] == 0 and cur not in sets_idle:
            new = TO_IDLE[raw_ins[0]]
        elif raw_ins[0] in TWIN:
            new = TWIN[raw_ins[0]]
        else:
            continue
        s1[off] = new
        changed.append((names[cur] if cur < len(names) else cur, off, raw_ins[0]))
    if not changed:
        return None, []
    # patch in place: only opcode bytes move, the payload keeps its size
    at = raw.find(secs[0])
    if at < 0:
        return None, []
    out = bytearray(raw)
    out[at:at + len(s1)] = s1
    return bytes(out), changed
