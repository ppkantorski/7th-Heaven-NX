#!/usr/bin/env python3
"""
ff7nx_fxinterp.py -- BUILD 747. FFNx's InterpolationEffectDecorator: battle
effects drawn at 60 Hz between their 15 Hz logic steps.

WHAT FFNx DOES
==============
Every effect100 slot FFNx does not name, and every effect60 slot it does not
name, runs through InterpolationEffectDecorator: the function runs for real
once per battle tick and paused on the three frames between, and FFNx
replaces the game's two effect texture-draw routines
(battle_animate_material_texture 0x5D1BA4, battle_animate_texture_spt
0x5D1FFB) with copies that, inside such a call, SAVE what each draw used on
the real call -- the model matrix, and for a material its transparency, UV
scroll, the engine's secondary colour and seven palette_extra fields; for a
sprite its colour -- and on the paused calls move each draw 1/4, 2/4, 3/4 of
the way from the saved values toward the ones it is about to use. The game
draws before it advances, so that is a smooth slide from step k to k+1.

HOW IT IS DONE HERE
===================
  * the throttle caves (ff7nx_dispatch, `interp_cur`) publish the running
    decorated slot and its phase in a 16-byte record at +0x3FEC4A8 and clear
    it after the call (ff7nx_60fps._inject_effect_interpolation);
  * the two routines' ARM entries (+0x7853F0, +0x7875B0) branch to a stub
    that calls native/fxinterp.c with that record, this pass's BSS state,
    the guest page table and the guest context, then replays the displaced
    `str d8, [sp, #-N]!` and returns;
  * the C code keys each draw by (slot, guest return address, index within
    the call) -- FFNx's (return address, textureCallIdx) inside a per-slot
    map -- in a 256-entry table with a per-slot generation for FFNx's
    per-real-call clear. A draw that finds no room is drawn stock.

WHERE IT LIVES
==============
Code: the translated body of x86 0x4079B8, an archive-setup routine this
port never reaches (12,560 bytes): nothing branches to its ARM entry,
nothing relocates to it except the recompilation map's own record, no x86
code calls or jumps to it, and its address appears nowhere in ff7_en. All
four are re-proved on every build against the module being patched and the
stock exe. Blob 2 KB + two 11-word stubs.
State: BSS, ~20 KB (lighting already holds ~55 KB the same way).

BUILD 782: a paused draw first carries the saved matrix to the view drawn
now (the camera is interpolated on every frame since BUILD 384), so only the
effect's own motion is lerped -- see fxinterp.c. State 25,216 -> 33,408
bytes (the view saved per draw); FACIAL_AT 0x1000 -> 0x1800.

SEVENTH_NX_FX_INTERP=0 leaves the effects stepping at the game's rate.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import nxpaths  # BUILD 755: project paths (src/nxpaths.py)
import os
import struct

import a64 as A
import ff7nx_audio_cave as AC
import nxmap

HERE = nxpaths.ROOT
BLOB = os.path.join(nxpaths.APP, 'native', 'fxinterp.bin')
ENTRY = os.path.join(nxpaths.APP, 'native', 'fxinterp.entry')
# Full 30 FPS: the same source built with FREQ 2 -- one paused call per tick,
# drawn half way (native/build_fxinterp.sh builds both).
BLOB30 = os.path.join(nxpaths.APP, 'native', 'fxinterp30.bin')
ENTRY30 = os.path.join(nxpaths.APP, 'native', 'fxinterp30.entry')
ENV = 'SEVENTH_NX_FX_INTERP'

DEAD_X86 = 0x4079B8
ENTRY_GUARD = 0x40
#: BUILD 749: this pass owns [lo, lo + FACIAL_AT) of the dead body; the rest
#: holds the facial runtime's caves (ff7nx_facial), which run earlier and
#: used to come out of the padding pool -- 747's 20 extra pool words left
#: that pool without the 100-word window facial needs.
#: BUILD 782: 0x1000 -> 0x1800. The camera-aware lerp (fxinterp.c, BUILD 782)
#: grew the blob 3,584 -> 5,300 bytes; facial's caves take 3,552 of the
#: 6,416 bytes it keeps (measured with the voice flap on).
FACIAL_AT = 0x1800
#: BUILD 855: the top WORLD_TOP bytes of the body belong to ff7nx_fieldpace's
#: world map (its two limiter-sleep stubs and the 3x booster cave); facial
#: keeps [lo + FACIAL_AT, hi - WORLD_TOP): 4,880 bytes, of which it uses at
#: most 3,552 (measured with the voice flap on).
WORLD_TOP = 0x600
HOOKS = (
    # (x86 routine, ARM entry, displaced word, blob entry)
    (0x5D1BA4, 0x7853F0, 0xFC1C0FE8, 'fx_material'),   # str d8,[sp,#-0x40]!
    (0x5D1FFB, 0x7875B0, 0xFC190FE8, 'fx_spt'),        # str d8,[sp,#-0x70]!
)
INTERP_CUR = 0x3FEC4A8              # ff7nx_60fps.INTERP_CUR
GUEST_PAGES_GOT = 0x12CE8C0
GUEST_CTX_GOT = 0x12CE2B0
N_SLOT, N_ENT, ENT_BYTES = 160, 256, 132  # 749g: + vel[3], hv; 782: + the view; 783: + lh, lstep
N_UN, UN_BYTES = 12, 72                                      # BUILD 749c
STATE_BYTES = 2 * N_SLOT + 5 * N_SLOT + N_ENT * ENT_BYTES + N_UN * UN_BYTES   # 35,776 (783: + snumc, spend, lh, lstep)


def enabled():
    return os.environ.get(ENV, '1').strip().lower() not in (
        '0', 'off', 'false', 'no')


def entries(path=ENTRY):
    out = {}
    with open(path) as f:
        for line in f:
            name, off = line.split()
            out[name] = int(off, 16)
    return out


def _x86_unreferenced(exe_bytes, va):
    """'' if ff7_en neither holds `va` anywhere nor calls/jumps to it."""
    if struct.pack('<I', va) in exe_bytes:
        return 'its address appears in ff7_en'
    # .text: file offset 0x200 maps to VA 0x401000 (ff7_en_switch layout)
    text = exe_bytes[0x200:0x200 + 0x3B5000]
    for k in range(len(text) - 4):
        if text[k] in (0xE8, 0xE9):
            rel, = struct.unpack_from('<i', text, k + 1)
            if 0x401000 + k + 5 + rel == va:
                return 'x86 0x%X calls/jumps to it' % (0x401000 + k)
    return ''


def dead_region(src, exe_bytes):
    """(lo, hi) of the dead body, proven in `src` and in the exe."""
    import ff7nx_fieldbg
    m = nxmap.Main(src)
    why = ff7nx_fieldbg._liveness(m, DEAD_X86)
    if not why and exe_bytes is not None:
        why = _x86_unreferenced(exe_bytes, DEAD_X86)
    if why:
        raise ValueError('x86 0x%X is not dead: %s' % (DEAD_X86, why))
    return m.extent(DEAD_X86)


def _addr(w, reg, at, value):
    w.append(A.adrp(reg, at + 4 * len(w), value & ~0xFFF))
    w.append(A.add_imm64(reg, reg, value & 0xFFF))


def _got(w, reg, at, got):
    w.append(A.adrp(reg, at + 4 * len(w), got & ~0xFFF))
    w.append(A.ldr64(reg, reg, got & 0xFFF))


def stub(at, state, fn, hook, displaced):
    # BUILD 749d: the caller's identity is the ARM return address in x30, as
    # a module offset (w4). The translated x86 `call` only reserves the guest
    # return slot -- `sub esp, 4` -- it never stores the x86 address there, so
    # [esp] is stale stack and was a meaningless key in 747..749c.
    w = [A.stp64_pre(29, 30, 31, -0x10)]
    here = at + 4 * len(w)
    w.append(A.adr(4, here, here))                  # x4 = runtime `here`
    w.append(A.sub_reg(4, 30, 4))                   # w4 = w30 - w(here)
    w += A.movz_movk(5, here)
    w.append(A.add_reg(4, 4, 5))                    # + module offset of here
    _addr(w, 0, at, INTERP_CUR)
    _addr(w, 1, at, state)
    _got(w, 2, at, GUEST_PAGES_GOT)
    _got(w, 3, at, GUEST_CTX_GOT)
    w.append(A.bl(at + 4 * len(w), fn))
    w.append(A.ldp64_post(29, 30, 31, 0x10))
    w.append(displaced)
    w.append(A.b(at + 4 * len(w), hook + 4))
    return w


def check(m, text):
    for x86, arm, word, _fn in HOOKS:
        if m.x86_to_arm.get(x86) != arm:
            raise ValueError('x86 0x%X does not map to +0x%X' % (x86, arm))
        got, = struct.unpack_from('<I', text, arm)
        if got != word:
            raise ValueError('+0x%X holds %08X, expected %08X (already '
                             'hooked, or a different module)'
                             % (arm, got, word))


#: BUILD 811: fxinterp.c's CUR_CAM_INIT -- the throttle's cam_init, from Cur.
CUR_CAM_INIT = 0x668
KOTR_SEED_ADRP = 0x210104          # ff7nx_dispatch.KOTR_CAM_AT + 12


def kotr_cam_init(text):
    """BUILD 811: module offset of the cam_init KOTR's registration writes
    2 to, or None when this main has no such registration."""
    w = struct.unpack_from('<I', text, KOTR_SEED_ADRP)[0]
    if w & 0x9F00001F != 0x90000010:          # adrp x16
        return None
    imm = ((w >> 29) & 3) | (((w >> 5) & 0x7FFFF) << 2)
    if imm & (1 << 20):
        imm -= 1 << 21
    page = (KOTR_SEED_ADRP & ~0xFFF) + (imm << 12)
    two = A.movz(17, 2)
    for va in range(4, len(text) - 4, 4):
        if struct.unpack_from('<I', text, va - 4)[0] != two:
            continue
        x = struct.unpack_from('<I', text, va)[0]
        if x & 0xFFC003FF == 0x39000211:      # strb w17, [x16, #imm12]
            return page + ((x >> 10) & 0xFFF)
    return None


def check_kotr_setup(text, log=lambda *_: None):
    """The built main must keep cam_init exactly CUR_CAM_INIT past Cur."""
    at = kotr_cam_init(text)
    if at is None:
        log('  effect interpolation: no KOTR camera registration in this '
            'main -- the BUILD 811 setup-tick rule reads a byte nothing '
            'writes and keeps KOTR live')
        return
    if at - INTERP_CUR != CUR_CAM_INIT:
        raise ValueError('KOTR cam_init is +0x%X, %+#x from Cur; fxinterp.c '
                         'expects +%#x' % (at, at - INTERP_CUR, CUR_CAM_INIT))


def apply_to_nso(src, dest, stock=None, exe=None, log=lambda *_: None,
                 substeps=4):
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    m = nxmap.Main(src)
    check(m, text)
    check_kotr_setup(text, log)             # BUILD 811
    exe_bytes = open(exe, 'rb').read() if exe else None
    lo, hi = dead_region(src, exe_bytes)
    if stock is not None:
        if nxmap.Main(stock).text[lo:lo + FACIAL_AT] != \
                bytes(text[lo:lo + FACIAL_AT]):
            raise ValueError('the dead body at +0x%X was already written'
                             % lo)
    if substeps not in (2, 4):
        raise ValueError('fxinterp: %d sub-steps; 4 (60 FPS) or 2 (30 FPS)'
                         % substeps)
    code = open(BLOB if substeps == 4 else BLOB30, 'rb').read()
    ent = entries(ENTRY if substeps == 4 else ENTRY30)
    base = AC.scratch_base(blob, segs)
    state = (base + 15) & ~15
    growth = (state - base) + STATE_BYTES + AC.bss_tail_slack(segs)
    entry = (lo + ENTRY_GUARD + 15) & ~15
    at = (entry + len(code) + 15) & ~15
    stubs = []
    for x86, arm, word, name in HOOKS:
        words = stub(at, state, entry + ent[name], arm, word)
        stubs.append((at, arm, words))
        at += 4 * len(words)
    if at > min(hi, lo + FACIAL_AT):
        raise ValueError('fxinterp: %d bytes do not fit its %d-byte part of '
                         'the body' % (at - lo, min(hi - lo, FACIAL_AT)))
    text[entry:entry + len(code)] = code
    for s_at, arm, words in stubs:
        for i, x in enumerate(words):
            struct.pack_into('<I', text, s_at + 4 * i, x)
        struct.pack_into('<I', text, arm, A.b(arm, s_at))
    raw[0] = bytes(text)
    out = AC.pack(blob, raw, growth)
    _s2, raw2 = AC.segments(out)
    for s_at, arm, _w in stubs:
        if struct.unpack_from('<I', raw2[0], arm)[0] != A.b(arm, s_at):
            raise ValueError('hook +0x%X did not survive the repack' % arm)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(out)
    log('  effect interpolation (FFNx InterpolationEffectDecorator): code '
        '+0x%X..+0x%X in dead x86 0x%X (%d of %d bytes), state +0x%X '
        '(%d bytes BSS); hooks +0x%X material, +0x%X sprite'
        % (entry, at, DEAD_X86, at - lo, hi - lo, state, STATE_BYTES,
           HOOKS[0][1], HOOKS[1][1]))
    return {'code': entry, 'end': at, 'state': state, 'bss': growth,
            'stubs': [(s, a) for s, a, _w in stubs], 'entries': ent}
