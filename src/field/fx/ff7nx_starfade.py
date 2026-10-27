#!/usr/bin/env python3
"""ff7nx_starfade.py -- nivl_e1's shooting star in HD truecolor, every
frame of its flight and of its fade kept. BUILD 672.

THE REPORT (hardware 10-14, nivl_e1 -- the night sky over the Nibelheim
well): "the shooting star fx ... looks like it should be true color, its a
bit pixellated".

WHAT IT IS. Entity `star` plays the star as tile frames, 2 frames each:
param 1 states 0..7, then param 2 states 0..2, then holds param 2 state 3
(3 records) and fades that held frame through palette 6:

    BGON 2,3; SET v = 66; STPAL 6 -> buf 0
    loop: IF v > 1 { v -= 3; MPPAL2 buf 0 -> buf 0x10 by (v, v, v) /64;
                     CPPAL entry 0; LDPAL buf 0x10 -> palette 6; BACK }
    BGOFF 2,3

All 107 records are palette 6 on the paletted FX page 15. Because the
palette is script-animated (Cosmos ships its 22 states) the build kept the
page paletted and 1x: the squares. ff7nx_palauto refuses it (the records
are tile-animated: param 1/2).

THE ROUTE (ff7nx_palstates, three specs on palette 6):
  * the flight frames (param 1, all states; param 2, states 0..2) become
    truecolor HD records under their OWN param and state bit -- Cosmos's
    sheet for palette 6 (the full-brightness one), so the tile animation
    runs exactly as before;
  * the held frame (param 2 state 3) becomes 23 levels, one free background
    parameter each (B + j shows v = 3j at v/64 -- the MPPAL2 multiply, which
    is linear), state 0;
  * the script, byte for byte in place (same length, no offset moves):
        SET v = B + 22; SKIP (BGON 2,3 / STPAL)  # v' = B + v/3
        loop: IF v > B { v -= 1; BGOFF var5[p]; BGON var5[v]; SET p = v;
                         SKIP; BACK }
        BGOFF var5[p]
    one pass per tick (BACK yields) of 7 opcodes, as vanilla's 6 -- the
    same 22 steps at the same pace, the old level off and the new one on in
    the same tick (the first pass shares the tick that switches the last
    flight frame off). p is a bank-5 byte nothing else touches; whatever it
    holds before the first pass, BGOFF of it can only reach a parameter
    that is already off. Palette 6 is no longer sampled; page 15's
    paletted art is dropped and its slot reused.

SEVENTH_NX_NO_STAR_FADE=1 disables. VERSION is in flevel.lgp's key.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os

import numpy as np

VERSION = 672
OFF_ENV = 'SEVENTH_NX_NO_STAR_FADE'
FIELDS = {'nivl_e1': {'q': 6, 'var': 8, 'v0': 66, 'step': 3}}
OLD = bytes.fromhex(
    'e0000203' '80500842' 'e5000600ff'
    '14500801021b' '78500803'
    'ea005550001008080 8ff'.replace(' ', '') +
    'e700001000' 'e6001006ff' '121e' 'e1000203' '00')


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def n_levels(spec):
    return spec['v0'] // spec['step'] + 1          # v = 0, 3, ..., 66: 23


def new_bytes(B, var, p, n):
    """The replacement for OLD (same length)."""
    top = B + n - 1
    # BGON 2,3 / SET / STPAL (13 bytes) -> SET v = B + 22; SKIP to the IF.
    # The held frame first shows at v = 63 (vanilla: one tick at the full
    # palette, then 63) in the tick that switches the last flight frame off:
    # WAIT, BGOFF 2,2, SET, SKIP, IF, MINUS, BGOFF, BGON -- 8 opcodes.
    out = bytes((0x80, 0x50, var, top))            # SET v = B + 22
    out += bytes((0x10, 0x08)) + b'\0' * 7          # SKIP 7 to the IF
    out += bytes((0x14, 0x50, var, B, 0x02, 0x1B))  # IF v > B, else -> end
    out += bytes((0x78, 0x50, var, 0x01))          # MINUS v, 1
    body = (bytes((0xE1, 0x50, p, 0x00)) + bytes((0xE0, 0x50, var, 0x00))
            + bytes((0x80, 0x55, p, var)))
    out += body + bytes((0x10, 0x07)) + b'\0' * 6  # SKIP 6 to the BACK
    out += bytes((0x12, 0x1E))                     # BACK to the IF
    out += bytes((0xE1, 0x50, p, 0x00)) + b'\0'    # BGOFF var5[p]; RET
    return out


def patch_script(sec0, B, var, p, n):
    if sec0.count(OLD) != 1:
        raise ValueError('star routine found %d times' % sec0.count(OLD))
    new = new_bytes(B, var, p, n)
    if len(new) != len(OLD):
        raise ValueError('patch length %d != %d' % (len(new), len(OLD)))
    return sec0.replace(OLD, new)


def plan_field(name, parts, vparts, art, mb_cap=35.0, raw_cap=None):
    """(sec0, sec9, info) -- raises on any doubt."""
    import diag_common as DC
    import ff7nx_palauto as PA
    import ff7nx_palstates as PS
    spec = FIELDS[name]
    q, var = spec['q'], spec['var']
    n = n_levels(spec)
    sec0 = bytes(parts[0])
    sec9 = parts[8]
    pl, _ts, _te, px = DC.parse_pages(sec9)
    pm = {g.slot: g for g in pl if g is not None}
    used = PA.used_params(sec0, sec9)
    free = [x for x in range(1, 64) if x not in used]
    block = PA._free_block(free, n)
    if block is None:
        raise ValueError('no %d consecutive free parameters' % n)
    B = block[0]
    p = PA._free_var(sec0, taken={var})
    # the flight frames, the held frame: the records the script switches
    flight1 = PS._targets(sec9, pm, q, 1, None, True)
    flight2 = PS._targets(sec9, pm, q, 2, {1, 2, 4}, True)
    held = PS._targets(sec9, pm, q, 2, {8}, True)
    total = len(PS._targets(sec9, pm, q, 1, None, True)) + len(
        PS._targets(sec9, pm, q, 2, None, True))
    if len(flight1) + len(flight2) + len(held) != total:
        raise ValueError('palette %d records outside params 1/2' % q)
    for tg in (flight1, flight2, held):
        if not tg:
            raise ValueError('palette %d: a record group is empty' % q)

    def full(tg):
        vc = PS._vanilla_cells(tg, vparts[8], with_state=True)
        return PS._static_art(art, name, q, vc, px)
    a1, a2, ah = full(flight1), full(flight2), full(held)
    levels = {c: [np.clip(img * (min(spec['step'] * j, 64) / 64.0), 0, 255)
                  for j in range(n)] for c, img in ah.items()}
    specs = [
        {'palette': q, 'mode': 'levels', 'twin_state': True, 'record_param': 1,
         'skip_other': True, 'params': (1,), 'states': (0,),
         'bit_from_record': True, 'art': {c: [img] for c, img in a1.items()}},
        {'palette': q, 'mode': 'levels', 'twin_state': True, 'record_param': 2,
         'record_bits': {1, 2, 4}, 'skip_other': True, 'params': (2,),
         'states': (0,), 'bit_from_record': True,
         'art': {c: [img] for c, img in a2.items()}},
        {'palette': q, 'mode': 'levels', 'twin_state': True, 'record_param': 2,
         'record_bits': {8}, 'skip_other': True,
         'params': tuple(block), 'states': tuple(range(n)),
         'art': levels},
    ]
    sec0n = patch_script(sec0, B, var, p, n)
    s0, s9, info = PS.plan_field(name, parts, vparts, art, mb_cap, raw_cap,
                                 specs=specs, sec0=sec0n)
    info['star'] = ('flight %d records (params 1/2 kept), held frame %d '
                    'records x %d levels on params %d..%d, var5[%d] the '
                    'shown one' % (len(flight1) + len(flight2), len(held), n,
                                   B, B + n - 1, p))
    return s0, s9, info


def apply_to_flevel(archive, payloads, art, encode=None, mb_cap=35.0,
                    raw_cap=None, log=lambda *_: None):
    import lgp
    total = {'names': [], 'refused': []}
    if disabled() or art is None or getattr(art, 'provider', None) is None:
        return total
    encode = encode or archive.encode_field
    for name in FIELDS:
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            van = archive.decompressed(entry)
            raw = lgp.lzs_decompress(p[4:]) if p else van
            parts = list(lgp.split_sections(raw))
            vparts = list(lgp.split_sections(van))
            parts[0], parts[8], info = plan_field(name, parts, vparts, art,
                                                  mb_cap, raw_cap)
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append((name, str(exc)[:120]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['names'].append('%s: %s; %.1f MB' % (name, info['star'],
                                                    info['mb']))
    return total


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  SHOOTING STAR (BUILD 672): truecolor HD, flight frames '
                   'and the fade kept (%s). %s=1 disables.'
                   % (' | '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! shooting star %s: %s' % (name, why))
    return '\n'.join(out)
