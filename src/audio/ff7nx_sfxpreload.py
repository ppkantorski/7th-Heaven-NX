#!/usr/bin/env python3
"""
ff7nx_sfxpreload.py -- BUILD 665. A field's sound effects are decoded while
the field is still black, not the first time a script plays one.

WHAT WAS MEASURED
=================
PROBE-664 (664c, mds7plr1): the field's one bad frame was 27.4 ms, and
16.5 ms of it was inside `sfx_load` (x86 0x745CF3) -- the first play of a
sound reading it out of audio.dat and converting it. That is the stutter when
the guards / the people in del2 / the runners out of the bar appear: their
scripts start a sound that has not been loaded yet. Once a channel holds it,
the next play is cheap, which is why going back in and out is smooth.

HOW THE STOCK GAME PLAYS A SOUND
================================
`play_sfx_on_channel` (x86 0x745160): when the archive row is in the sound
cache (guest 0xDE0128) it DUPLICATES the cached buffer (x86 0x74A5B7) --
cheap; otherwise it calls `sfx_load(id, &channel_buffer)`, which reads and
converts the row -- the 16 ms. The world map fills the cache for its sounds
up front (x86 0x77D901); fields never do.

WHY NOT SIMPLY FILL THE CACHE
=============================
A cached row never reaches `sfx_load` again, and `sfx_load` is where the
sequential-SFX bridge (ff7nx_sfxshuffle) picks the next variant -- 57 rows
rotate, among them the sounds del2 and mds7plr1 actually play (25, 28, 15).
Caching them would freeze every rotation on one variant.

WHAT THIS DOES
==============
native/sfxpre.c + two hooks, the cache itself left exactly as the game uses
it:

  SITE  ARM 0x948E48, the field loop's call to field_update_models_positions.
        On the first pass of a new field (field id or file pointer changed):
        sp_plan walks the field's script and lists every literal sound id it
        can play (SE 0xF1; AKAO 0xF2 / AKAO2 0xDA play commands 0x20..0x2F),
        releases the buffers held for the previous field that this one does
        not use, and loads each new id with the stock `sfx_load(id, 0)` --
        once per variant for a rotating row. After each load sp_take moves
        the buffer OUT of the cache word into its own table, keyed by
        (row, audio.dat offset of the variant), and clears the cache word.
  MISS  ARM 0xE6FC4C, inside sfx_load after its cache check and length check
        (and after the sequential bridge chose this play's variant): sp_find
        looks up (row, offset). Held -> duplicate it with the same stock call
        the cached path uses, store the copy where sfx_load would have stored
        its fresh load, return 1. Not held -> the stock load, unchanged.

FOOTSTEPS (665e). PROBE 665d measured the stutters themselves: they were
the player's footsteps. ff7nx_fieldsteps plays every step through reserved
row 744, rewriting its descriptor to one of Cosmo's appended variants and
clearing its cache word -- so every step is a disk read, ~1 ms when the
variant was read before and 3..17 ms the first time each variant is read
(del2: 10 variants, the 16-17 ms ones exactly where the frame drops were).
The plan also reads the field's CMFS trailer (the per-triangle footstep
routes ff7nx_fieldsteps_data appends to the field file), loads each variant it
can select with the descriptor set the way fieldsteps sets it (sp_prep), and
keys it (744, offset) like any other row; plus the stock row 159 a field with
no route steps on. The payload base and stride are read out of the installed
fieldsteps cave (field_steps_route) and checked against the build plan.

So the first play of a preloaded sound costs what every later play costs,
rotations keep rotating, and the game's cache is never touched. Held: at most
48 buffers, rows up to SEVENTH_NX_SFX_PRELOAD_KB of ADPCM (default 128),
1.5 MB of ADPCM per field (SEVENTH_NX_SFX_PRELOAD_BUDGET_KB); never the
looping rows (the ambient beds start with the field anyway, and the long ones
are what fills the sound pool -- ff7nx_audiopool), never the reserved rows
731..750 (footstep / battle-route bridges).

SEVENTH_NX_SFX_PRELOAD=0 leaves fields exactly as they were.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import nxpaths  # BUILD 755: project paths (src/nxpaths.py)
import os
import struct

import a64 as A
import ff7nx_audio_cave as AC
import ff7nx_deadspace as DS

HERE = nxpaths.ROOT
BLOB = os.path.join(nxpaths.APP, 'native', 'sfxpre.bin')
ENTRY = os.path.join(nxpaths.APP, 'native', 'sfxpre.entry')
ENV = 'SEVENTH_NX_SFX_PRELOAD'
CAP_ENV = 'SEVENTH_NX_SFX_PRELOAD_KB'
BUDGET_ENV = 'SEVENTH_NX_SFX_PRELOAD_BUDGET_KB'
DEFAULT_CAP_KB = 128
DEFAULT_BUDGET_KB = 1536

SITE = 0x948E48                  # bl field_update_models_positions
TARGET = 0x9D6900
MISS = 0xE6FC4C                  # sfx_load, x86 0x745D4F (out-pointer test)
MISS_WORD = 0xB9401688           # ldr w8, [x20, #0x14]
MISS_EPILOGUE = 0xE6FC88         # x86 0x745DB7: mov esp, ebp; pop ebp; ret
SFX_LOAD = 0xE6FAE0              # x86 0x745CF3 sfx_load(id, out)
SFX_LOAD_WORD = 0xF81C0FF7
DUPLICATE = 0xF05820             # x86 0x74A5B7 duplicate_sound_buffer(buf)
DUPLICATE_WORD = 0xF81D0FF5
RELEASE = 0xF033F0               # x86 0x74A606 release_sound_buffer(buf)
RELEASE_WORD = 0xA9BE4FF4
GUEST_PAGES_GOT = 0x12CE8C0
CTX_GOT = 0x12CE2B0
GUEST_TRANSLATE = AC.GUEST_TRANSLATE
RESERVED_LO = 731

# State (native/sfxpre.c)
STATE_BYTES = 0x4A0
MAX_ENT = 48
ST_NREL, ST_NLOAD, ST_NENT, ST_PLANS, ST_TAKES, ST_HITS = 8, 12, 16, 20, 24, 28
ST_REL = 32
ST_LOAD = ST_REL + 4 * MAX_ENT
ST_SLOT = ST_LOAD + 4 * MAX_ENT
ST_OFF = ST_SLOT + 4 * MAX_ENT
ST_BUF = ST_OFF + 4 * MAX_ENT
ST_LOADOFF = ST_BUF + 4 * MAX_ENT              # 665e: footstep variant offsets
FOOT_SLOT = 743                                # row 744 (ff7nx_fieldsteps)

T_OP, T_SPECIAL, T_VAR, T_CAP, T_BUDGET = 0, 256, 272, 648, 652
T_FOOT_PB, T_FOOT_STR = 656, 660
TAB_BYTES = 664

SITE_FRAME = 0x90
SNAP = 0x50                      # 0x30 bytes of guest registers + conditions


def enabled(env=None):
    env = os.environ if env is None else env
    # BUILD 667c: OFF by default. It was built for a stutter that turned out
    # to be the first blink (666), and its release of the previous field's
    # buffers crashed loading a save into Aerith's house (hardware: abort
    # under RELEASE 0xF033F0 from the stub, the sound system already reset).
    return env.get(ENV, '0').strip().lower() in ('1', 'on', 'yes', 'true')


def _kb(env, name, default, lo, hi):
    env = os.environ if env is None else env
    try:
        kb = int(env.get(name, default))
    except ValueError:
        kb = default
    return max(lo, min(kb, hi)) * 1024


def cap_bytes(env=None):
    return _kb(env, CAP_ENV, DEFAULT_CAP_KB, 8, 1024)


def budget_bytes(env=None):
    return _kb(env, BUDGET_ENV, DEFAULT_BUDGET_KB, 64, 8192)


def entries():
    out = {}
    for line in open(ENTRY):
        k, v = line.split()
        out[k] = int(v, 16)
    return out


def op_tables():
    """(256 sizes, 16 SPECIAL sizes for sub-ops 0xF0..0xFF) from the table
    field_dis uses. 0 = unknown: the walk stops there. KAWAI (0x28) and
    SPECIAL (0x0F) are sized in C."""
    import pyff7_path
    pyff7_path.ensure(nxpaths.APP)
    from PyFF7.field import OP, SPECIAL_OP_CODES
    ops = bytearray(256)
    for op in range(256):
        if op in (0x0F, 0x28):
            continue
        n = OP[op][1]
        if n is not None and 0 <= n < 255:
            ops[op] = n + 1
    spc = bytearray(16)
    for sub, (_name, n) in SPECIAL_OP_CODES.items():
        if 0xF0 <= sub <= 0xFF and 0 <= n < 254:
            spc[sub - 0xF0] = n + 2
    return bytes(ops), bytes(spc)


def _word(text, va):
    return struct.unpack_from('<I', text, va)[0]


def _b_target(va, w):
    if (w & 0xFC000000) != 0x14000000:
        return None
    off = w & 0x3FFFFFF
    if off & 0x2000000:
        off -= 0x4000000
    return va + 4 * off


def shuffle_routes(text, segs=None, raw=None):
    """{sound id: variant count} the sequential-SFX bridge rotates, read out
    of the installed module (its cave at AC.LOAD_HOOK: adrp/add x21 = route
    table, movz w24 = route count; route word = slot | count << 10). {} when
    the bridge is absent."""
    pc = _b_target(AC.LOAD_HOOK, _word(text, AC.LOAD_HOOK))
    if pc is None:
        return {}
    page = table = count = None
    for _ in range(64):
        w = _word(text, pc)
        t = _b_target(pc, w)
        if t is not None:
            pc = t
            continue
        if (w & 0x9F00001F) == 0x90000015:                    # adrp x21
            imm = ((w >> 29) & 3) | (((w >> 5) & 0x7FFFF) << 2)
            if imm & (1 << 20):
                imm -= 1 << 21
            page = (pc & ~0xFFF) + (imm << 12)
        elif (w & 0xFFC003FF) == 0x910002B5 and page is not None:
            table = page + ((w >> 10) & 0xFFF)
        elif (w & 0xFFE0001F) == 0x52800018:                  # movz w24, #n
            count = (w >> 5) & 0xFFFF
        if table is not None and count is not None:
            break
        pc += 4
    if table is None or not count or count > 750:
        return {}

    def rd(va):
        if segs is not None and raw is not None:
            return AC.read_word(segs, raw, va)
        return _word(text, va)
    out = {}
    for i in range(count):
        w = rd(table + 8 * i)
        out[(w & 0x3FF) + 1] = (w >> 10) & 7
    return out


def field_steps_route(text):
    """(payload base, stride) ff7nx_fieldsteps' cave selects footstep variants
    with, read out of the installed module: the cave materialises
    `mov32 w24, stride; mov32 w23, payload_base` (its words may be split
    across padding runs joined by `b`). None when the bridge or its terrain
    routes are absent, or the read is ambiguous."""
    hook = _b_target(0x948E4C, _word(text, 0x948E4C))
    if hook is None:
        return None
    words = struct.unpack_from('<%dI' % (len(text) // 4), text)

    def nxt(i):
        i += 1
        for _ in range(4):
            t = _b_target(4 * i, words[i])
            if t is None:
                return i
            i = t // 4
        return i
    found = set()
    for i, w in enumerate(words):
        if (w & 0xFFE0001F) != 0x52800018:                    # movz w24
            continue
        j = nxt(i); k = nxt(j); m = nxt(k)
        if ((words[j] & 0xFFE0001F) == 0x72A00018 and
                (words[k] & 0xFFE0001F) == 0x52800017 and
                (words[m] & 0xFFE0001F) == 0x72A00017):
            stride = ((w >> 5) & 0xFFFF) | (((words[j] >> 5) & 0xFFFF) << 16)
            pb = (((words[k] >> 5) & 0xFFFF) |
                  (((words[m] >> 5) & 0xFFFF) << 16))
            if 0 < stride <= 0x40000 and pb >= 0x1000000:
                found.add((pb, stride))
    return found.pop() if len(found) == 1 else None


def table(routes, cap, budget, foot=None):
    ops, spc = op_tables()
    var = bytearray(376)
    for i in range(1, 751):
        k = 0 if i >= RESERVED_LO else min(max(routes.get(i, 1), 1), 15)
        var[i >> 1] |= k << (4 * (i & 1))
    pb, stride = foot or (0, 0)
    out = (ops + spc + bytes(var) + struct.pack('<II', cap, budget)
           + struct.pack('<II', pb, stride))
    assert len(out) == TAB_BYTES
    return out


def _gcall(e, a, target, nargs, args_from):
    """A translated cdecl call: unused return slot + `nargs` words at a new
    guest esp E (16-aligned, so all of them sit in one page and one
    translation covers them), `bl`, esp put back from w24. `args_from(i)`
    emits code leaving argument i in w9; w24 must hold the guest esp."""
    e(A.sub_imm(23, 24, 4 * (nargs + 1)))
    e(0x121C6EF7)                        # and w23, w23, #0xfffffff0
    e(A.mov_reg(0, 23))
    e(A.bl(a.pc(), GUEST_TRANSLATE))
    e(A.mov_reg64(10, 0))
    e(A.str_(A.WZR, 10, 0))
    for i in range(nargs):
        args_from(i)
        e(A.str_(9, 10, 4 + 4 * i))
    e(A.str_(23, 20, 0x10))
    e(A.bl(a.pc(), target))
    e(A.str_(24, 20, 0x10))


def site_stub(at, state, tab, fn):
    """`bl` here instead of SITE's `bl field_update_models_positions`; ends
    in a tail branch to it with x30 still pointing after SITE."""
    a = AC.Asm(at, lambda i: at + 4 * i)
    e = a.emit
    e(A.stp64_pre(29, 30, 31, -SITE_FRAME))
    e(A.stp64_off(19, 20, 31, 0x10))
    e(A.stp64_off(21, 22, 31, 0x20))
    e(A.stp64_off(23, 24, 31, 0x30))
    e(A.adrp(19, a.pc(), state & ~0xFFF))
    e(A.add_imm64(19, 19, state & 0xFFF))
    e(A.mov_reg64(0, 19))
    e(A.adrp(1, a.pc(), GUEST_PAGES_GOT & ~0xFFF))
    e(A.ldr64(1, 1, GUEST_PAGES_GOT & 0xFFF))
    e(A.adr(2, a.pc(), tab))
    e(A.bl(a.pc(), fn['sp_plan']))
    a.cbz(0, 'out')
    e(A.adrp(20, a.pc(), CTX_GOT & ~0xFFF))
    e(A.ldr64(20, 20, CTX_GOT & 0xFFF))   # x20 = guest context (as sfx_load)
    for k in range(0, 0x30, 8):
        e(A.ldr64(8, 20, k))
        e(A.str64(8, 31, SNAP + k))
    e(A.ldr(24, 20, 0x10))                # w24 = guest esp
    # releases
    e(A.movz(22, 0))
    a.label('rel')
    e(A.ldr(8, 19, ST_NREL))
    e(A.cmp_reg(22, 8))
    a.bcond('rel_done', A.HS)
    _gcall(e, a, RELEASE, 1, lambda i: (e(A.add_reg64_lsl(9, 19, 22, 2)),
                                        e(A.ldr(9, 9, ST_REL))))
    e(A.add_imm(22, 22, 1))
    a.b('rel')
    a.label('rel_done')
    # loads
    e(A.movz(22, 0))
    a.label('load')
    e(A.ldr(8, 19, ST_NLOAD))
    e(A.cmp_reg(22, 8))
    a.bcond('load_done', A.HS)
    e(A.mov_reg64(0, 19))                 # sp_prep(state, pages, tab, i):
    e(A.adrp(1, a.pc(), GUEST_PAGES_GOT & ~0xFFF))   # a footstep variant
    e(A.ldr64(1, 1, GUEST_PAGES_GOT & 0xFFF))        # sets row 744 first
    e(A.adr(2, a.pc(), tab))
    e(A.mov_reg(3, 22))
    e(A.bl(a.pc(), fn['sp_prep']))
    e(A.add_reg64_lsl(8, 19, 22, 2))
    e(A.ldr(21, 8, ST_LOAD))              # w21 = id
    _gcall(e, a, SFX_LOAD, 2,
           lambda i: e(A.mov_reg(9, 21)) if i == 0 else e(A.movz(9, 0)))
    e(A.mov_reg64(0, 19))
    e(A.adrp(1, a.pc(), GUEST_PAGES_GOT & ~0xFFF))
    e(A.ldr64(1, 1, GUEST_PAGES_GOT & 0xFFF))
    e(A.mov_reg(2, 21))
    e(A.bl(a.pc(), fn['sp_take']))
    a.cbz(0, 'load_next')
    e(A.mov_reg(21, 0))                   # a variant already held: release
    _gcall(e, a, RELEASE, 1, lambda i: e(A.mov_reg(9, 21)))
    a.label('load_next')
    e(A.add_imm(22, 22, 1))
    a.b('load')
    a.label('load_done')
    for k in range(0, 0x30, 8):
        e(A.ldr64(8, 31, SNAP + k))
        e(A.str64(8, 20, k))
    a.label('out')
    e(A.ldp64_off(23, 24, 31, 0x30))
    e(A.ldp64_off(21, 22, 31, 0x20))
    e(A.ldp64_off(19, 20, 31, 0x10))
    e(A.ldp64_post(29, 30, 31, SITE_FRAME))
    e(A.b(a.pc(), TARGET))
    return a.resolve()


def miss_cave(at, state, fn):
    """Branched to from MISS. x20 = guest context (sfx_load's own), x21 is
    live for the stock continuation; x0..x18 and the flags are dead here (the
    next stock instructions redefine them). Held -> duplicate, store, return
    1 through the stock epilogue. Otherwise replay MISS_WORD and go on."""
    a = AC.Asm(at, lambda i: at + 4 * i)
    e = a.emit
    e(A.stp64_pre(29, 30, 31, -0x30))
    e(A.stp64_off(19, 22, 31, 0x10))
    e(A.stp64_off(23, 24, 31, 0x20))
    e(A.adrp(19, a.pc(), state & ~0xFFF))
    e(A.add_imm64(19, 19, state & 0xFFF))
    e(A.mov_reg64(0, 19))
    e(A.adrp(1, a.pc(), GUEST_PAGES_GOT & ~0xFFF))
    e(A.ldr64(1, 1, GUEST_PAGES_GOT & 0xFFF))
    e(A.mov_reg64(2, 20))
    e(A.bl(a.pc(), fn['sp_find']))
    a.cbz(0, 'stock')
    e(A.mov_reg(22, 0))                   # w22 = held buffer
    e(A.ldr(24, 20, 0x10))
    _gcall(e, a, DUPLICATE, 1, lambda i: e(A.mov_reg(9, 22)))
    e(A.ldr(8, 20, 0))                    # guest eax = the duplicate
    a.cbz(8, 'stock')
    e(A.mov_reg64(0, 19))
    e(A.adrp(1, a.pc(), GUEST_PAGES_GOT & ~0xFFF))
    e(A.ldr64(1, 1, GUEST_PAGES_GOT & 0xFFF))
    e(A.mov_reg64(2, 20))
    e(A.bl(a.pc(), fn['sp_store']))
    e(A.ldp64_off(23, 24, 31, 0x20))
    e(A.ldp64_off(19, 22, 31, 0x10))
    e(A.ldp64_post(29, 30, 31, 0x30))
    e(A.b(a.pc(), MISS_EPILOGUE))
    a.label('stock')
    e(A.ldp64_off(23, 24, 31, 0x20))
    e(A.ldp64_off(19, 22, 31, 0x10))
    e(A.ldp64_post(29, 30, 31, 0x30))
    e(MISS_WORD)
    e(A.b(a.pc(), MISS + 4))
    return a.resolve()


def check(text):
    for va, want, what in ((SITE, A.bl(SITE, TARGET), 'field update call'),
                           (MISS, MISS_WORD, 'sfx_load miss path'),
                           (SFX_LOAD, SFX_LOAD_WORD, 'sfx_load entry'),
                           (DUPLICATE, DUPLICATE_WORD, 'duplicate entry'),
                           (RELEASE, RELEASE_WORD, 'release entry')):
        got = _word(text, va)
        if got != want:
            raise ValueError('sfx preload: +0x%X is %08X, not %08X (%s)'
                             % (va, got, want, what))


def apply_to_nso(src, dest, stock=None, log=lambda *_: None, region=None,
                 cap=None, budget=None, foot=None):
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    check(text)
    lo, hi = region if region else DS.part(src, 'sfxpre', stock)
    code = open(BLOB, 'rb').read()
    ent = entries()
    routes = shuffle_routes(text, segs, raw)
    cap = cap_bytes() if cap is None else cap
    budget = budget_bytes() if budget is None else budget
    found = field_steps_route(text)
    if foot and found and tuple(foot) != found:
        raise ValueError('sfx preload: footstep route %r from the plan, %r in '
                         'the module' % (tuple(foot), found))
    foot = tuple(foot) if foot else found
    tab_bytes = table(routes, cap, budget, foot)
    base = AC.scratch_base(blob, segs)
    state = (base + 15) & ~15
    growth = (state - base) + STATE_BYTES + AC.bss_tail_slack(segs)
    entry = (lo + 15) & ~15
    fn = {k: entry + v for k, v in ent.items()}
    tab = (entry + len(code) + 15) & ~15
    at = (tab + TAB_BYTES + 15) & ~15
    words = site_stub(at, state, tab, fn)
    miss_at = (at + 4 * len(words) + 15) & ~15
    mwords = miss_cave(miss_at, state, fn)
    end = miss_at + 4 * len(mwords)
    if end > hi:
        raise ValueError('sfx preload: %d bytes do not fit the %d-byte part'
                         % (end - lo, hi - lo))
    text[entry:entry + len(code)] = code
    text[tab:tab + TAB_BYTES] = tab_bytes
    for i, x in enumerate(words):
        struct.pack_into('<I', text, at + 4 * i, x)
    for i, x in enumerate(mwords):
        struct.pack_into('<I', text, miss_at + 4 * i, x)
    struct.pack_into('<I', text, SITE, A.bl(SITE, at))
    struct.pack_into('<I', text, MISS, A.b(MISS, miss_at))
    raw[0] = bytes(text)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(AC.pack(blob, raw, growth))
    log('  field sound preload: each field\'s script sounds are decoded at '
        'field entry and their first play duplicates them (%d KB per sound, '
        '%d KB per field, %d rotating row(s) keep rotating; footsteps %s); '
        'code +0x%X (%d bytes), state +0x%X'
        % (cap // 1024, budget // 1024, len(routes),
           ('%d-byte variants from +0x%X' % (foot[1], foot[0])) if foot
           else 'stock row 159 only', entry, end - lo, state))
    return {'code': entry, 'tab': tab, 'stub': at, 'miss': miss_at,
            'bytes': end - lo, 'state': state, 'bss': growth, 'entries': fn,
            'routes': routes, 'cap': cap, 'budget': budget, 'foot': foot}
