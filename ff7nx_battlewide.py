#!/usr/bin/env python3
r"""
ff7nx_battlewide.py -- widen the battle effects that size themselves to the
4:3 viewport.

THE SYMPTOM
===========
Summon and limit-break screen effects -- typhoon, odin's gunge, Barret's
Catastrophe, fat chocobo -- draw a full-screen quad that covers only the
middle 4:3 of a 16:9 frame.  The picture behind them is wide; the flash is
not.

THE MECHANISM
=============
FF7 keeps the battle viewport rect in four globals (FINDINGS-97 §2):

    [0x9AAD4C] x = 0     [0x9AAD50] y = 0
    [0x9AAD5C] w = 640   [0x9AAD68] h = 332

A full-screen effect reads `x` for its left edge and `w` for its extent, and
gets 0 and 640.  The vertex shader then multiplies gl_Position.x by
WS_SCALE = 0.75, so 640 game units land in the central 75% of the frame --
which is exactly 4:3.  The frame really spans game x -107..747.

FFNx redirects those two reads per effect to its own globals
(`src/ff7/widescreen.cpp`, "Battle summon fix"):

    patch_code_dword(typhoon_effect_sub_4D7044 + 0x1B, &wide_viewport_x);
    patch_code_dword(typhoon_effect_sub_4D7044 + 0x36, &wide_viewport_width);

with `wide_viewport_x = -107`, `wide_viewport_width = 854`.  This port has no
FFNx globals to point at, so the equivalent is to replace the recompiled load
with the immediate: one word each.

THE RULE, AND THE HALF OF IT THAT WAS MISSING
=============================================
"reads the rect's extent" separates full-frame quads from content that is
merely positioned.  It does NOT separate a quad from THE VIEWPORT ITSELF, and
that gap turned the battle screen black.

`battle_loop_sub_41BAB3` reads x, y, w and h and pushes them straight into
`engine_gfx_setviewport`.  Those loads are not a quad's corners -- they are
the 3D viewport, and handing it (-107, 0, 854, 480) both moves the matrix and
stops FINDINGS-97's uncrop leg matching `cmp wY,#0 / cmp wH,#332`.

FFNx never patches it, and I argued that its absence was not evidence because
FFNx rewrites the stored rect and so gets every consumer for free.  That was
right about FFNx and wrong as a licence: a function FFNx leaves alone because
it inherits the change is not the same as one that is safe to change directly.
The absence was informative and I talked myself past it.

So the rule now has two clauses, and the second is checked in `verify`:

    a full-screen overlay reads the extent (w or h) as well as the origin
    ...AND does not pass the rect to engine_gfx_setviewport

THE RULE THAT PICKS THE SITES -- EXTENT, NOT ORIGIN
===================================================
This module patches only reads of `x` and `w`, in functions that read BOTH.
That distinction is the whole design, and getting it wrong is what an earlier
attempt (`ff7nx_overlay.py`, now refusing to apply) did:

    a full-screen overlay needs a corner AND an extent, so it reads w
    a thing merely PLACED in the frame reads only the origin

Measured over every body that materialises a battle-rect constant, 14 read the
extent and 20 read only the origin.  The origin-only group is damage numbers,
UI and cursors -- content that is correctly positioned today.  FFNx redirects
those too, because FFNx moves the entire battle coordinate space out to the
wide edges; this port deliberately does not, keeping UI in the 4:3 core while
the picture widens around it.  So **FFNx's redirect list is not transferable
wholesale to this port -- only its extent-reading half is.**

WHY THIS CANNOT DISTURB THE SHIPPED BATTLE LEG
==============================================
FINDINGS-97's uncrop leg fires on the literal rect, `cmp wY,#0 / cmp wH,#332`.
Anything that changed the STORED rect would stop it firing and bring the black
band back silently.

This module changes no stored value.  FFNx does not either: of its four
`battle_enter` patches only `+0x21A` writes a global (h 332 -> 480), and that
one is deliberately excluded here -- FINDINGS-97 §3 measured it as moving the
scene 111 device rows.  Everything below rewrites CONSUMERS, so the globals
still read (0, 0, 640, 332) and the leg still recognises them.

`--verify` asserts that, rather than trusting this paragraph.

GROUPS
======
Shipped one group at a time so that a bad one is a single revert, not a
bisect.  Group 1 is the subset FFNx patches with two redirects and no
companion constants whatsoever -- the least that can go wrong.
"""
import argparse
import os
import re
import shutil
import struct
import sys
import tempfile
from pathlib import Path

try:
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM, CS_OP_REG
    from capstone.arm64 import ARM64_OP_MEM
except ImportError:                                          # pragma: no cover
    sys.exit('need capstone:  pip install capstone --break-system-packages')

import nxmap
import ff7nx_guestref as gr

FFNX_HEADER = 'repos/FFNx-master/src/widescreen.h'
EXE = 'ff7_en_switch'

BATTLEWIDE_ENV = 'SEVENTH_NX_BATTLE_WIDE'


def enabled() -> bool:
    """
    ON with 16:9, OFF at 4:3, overridable for an A/B.

    Every value this module writes is a widescreen value -- x -107, w 854,
    h 480.  At 4:3 the frame really is 640 wide and 332 tall above the UI, so
    these are not a smaller improvement there, they are simply wrong: the
    quads would hang off both sides of a frame that never widened.  Same
    reasoning as ff7nx_modelcull, and the same gate.
    """
    v = os.environ.get(BATTLEWIDE_ENV)
    if v is not None:
        return v not in ('', '0', 'off', 'false')
    try:
        import ff7nx_ws
        return ff7nx_ws.enabled()
    except Exception:                                          # noqa: BLE001
        return False


def apply_all(main, revert=False, log=print) -> int:
    """Every group, in ascending order.  The build's entry point."""
    rc = 0
    for g in sorted(GROUPS):
        rc |= apply(main, g, revert=revert, log=log)
    return rc

# the battle rect, FINDINGS-97 §2
FIELD = {'x': 0x9AAD4C, 'y': 0x9AAD50, 'w': 0x9AAD5C, 'h': 0x9AAD68}

# FFNx src/ff7/widescreen.h, parsed at verify time -- see _ffnx_values()
FFNX_DEFAULT = {'x': -107, 'y': 0, 'w': 854, 'h': 480}

# ---------------------------------------------------------------------------
# Transcribed from FFNx src/ff7/widescreen.cpp, ff7_widescreen_hook_init().
# `off` is the patch_code_dword offset, `field` is which rect field the x86
# instruction there reads, and `width` is the STOCK LOAD WIDTH in bytes.  All
# three are checked against the real executable.
#
# The width is recorded rather than inferred, and that is not redundancy.  A
# patched site reads `movz wN, #480`, and a MOVZ of a positive value is
# byte-identical whether it replaced a 32-bit `ldr` or a 16-bit `ldrh` -- so
# the patched image cannot say what to put back.  Inferring it from the value
# restored battle_sub_5BD050's height site as `ldrh` when stock is `ldr`, and
# --revert came back one word short of the original while reporting success.
# This is the THIRD time that inference has been wrong (ff7nx_overlay,
# ff7nx_swirl, here), so it is now data, not a rule.
GROUPS = {
    2: ('the battle fade / limit-break flash overlay', [
        ('battle_sub_5BD050', 0x5BD050,
         ((0x04B, 'w', 2), (0x068, 'x', 2), (0x08B, 'w', 2), (0x0B4, 'x', 2),
          (0x105, 'w', 2), (0x122, 'x', 2), (0x141, 'w', 2), (0x16A, 'x', 2),
          (0x19F, 'w', 2), (0x1BB, 'x', 2),
          # NOT an FFNx offset -- see NOT_FROM_FFNX below.  Note this is the
          # DISP32 FIELD, +0xE0, not the instruction at +0xDF: FFNx's offsets
          # all name the field patch_code_dword overwrites, and mixing the two
          # conventions reads the opcode byte into the address (0x9AAD68A1).
          (0x0E0, 'h', 4))),
    ]),
    3: ('extent-readers FFNx never names (victory fade, battle loop)', [
        # FFNx has no widescreen patch for any of these, and that absence is
        # not evidence they do not need one.  FFNx rewrites the STORED rect at
        # battle_enter, so every consumer in the game sees the wide values for
        # free; its explicit redirect list only covers the places where the
        # stored rect is not the source.  This port keeps the stored rect at
        # (0, 0, 640, 332) on purpose, so every consumer has to be found and
        # redirected individually -- and FFNx's list is therefore a starting
        # point, not an inventory.
        #
        # These five are what the extent rule finds once you stop using FFNx
        # as the index: bodies that read the rect's extent and so must be
        # drawing something full-frame.  battle_loop_sub_41BAB3 is the battle
        # main loop, which is where the victory fade would live.
        ('sub_4825E0', 0x4825E0,
         ((0x047, 'x', 2), (0x07C, 'w', 2), (0x097, 'h', 2))),
        ('sub_487BD2', 0x487BD2,
         ((0x019, 'x', 2), (0x034, 'w', 2), (0x041, 'h', 2))),
    ]),
    1: ('summon and limit quads, no companion constants', [
        ('typhoon_effect_sub_4D7044',    0x4D7044, ((0x1B, 'x', 2), (0x36, 'w', 2), (0x043, 'h', 2))),
        ('typhoon_effect_sub_4DB15F',    0x4DB15F, ((0x22, 'x', 2), (0x3D, 'w', 2), (0x04A, 'h', 2))),
        ('odin_gunge_effect_sub_4A3A2E', 0x4A3A2E, ((0x38, 'x', 2), (0x53, 'w', 2), (0x060, 'h', 2))),
        ('odin_gunge_effect_sub_4A4BE6', 0x4A4BE6, ((0x36, 'x', 2), (0x51, 'w', 2), (0x05E, 'h', 2))),
        ('barret_limit_3_1_sub_4700F7',  0x4700F7, ((0x1B, 'x', 2), (0x36, 'w', 2), (0x043, 'h', 2))),
        ('fat_chocobo_sub_5096F3',       0x5096F3, ((0x4A, 'x', 2), (0x5F, 'w', 2), (0x06A, 'h', 2))),
    ]),
}

# battle_enter is never touched -- see the module docstring.
FORBIDDEN = {
    0x41AD00: 'battle_enter: writing the rect would stop FINDINGS-97 uncrop '
              'leg firing and the black band would return',
    # Found by the "never STORES to the rect" check, which was added precisely
    # because this function reads the rect at +0x229/+0x22F -- the same two
    # offsets FFNx patches on battle_enter -- and that resemblance was worth
    # proving innocent rather than assuming.  It is not innocent: it makes
    # FOUR stores to the rect globals.  It is a rect WRITER, and redirecting a
    # writer's reads can desynchronise the stored value from the one the
    # uncrop leg matches on, which is the failure FINDINGS-97 forbids.
    #
    # Worth noting against FINDINGS-97 1, which said the six-byte operand
    # pattern for 0x9AAD5C-then-0x9AAD4C has "exactly one" hit in the
    # executable.  This is a second site with the same shape.  That does not
    # overturn the battle_enter identification -- which rests on four
    # independent instruction-type matches, not on the pattern being unique --
    # but the uniqueness claim itself does not hold.
    0x41B300: 'stores to the battle rect four times: a writer, not a consumer',
    # Turned the screen black on entering battle.  It reads the rect and
    # pushes it STRAIGHT INTO engine_gfx_setviewport (x86 0x66067A), three
    # times:
    #
    #     +0x0CA  mov eax, [0x9aad5c]   ; w
    #     +0x0D0  mov ecx, [0x9aad50]   ; y
    #     +0x0D7  mov edx, [0x9aad4c]   ; x
    #     +0x0DE  call 0x66067a         ; engine_gfx_setviewport
    #
    # so those loads are not a quad's corners -- they ARE the 3D viewport.
    # Redirecting them hands gfx_drv_setviewport (-107, 0, 854, 480), which
    # moves the matrix AND stops FINDINGS-97's uncrop leg matching, because
    # the leg triggers on `cmp wY,#0 / cmp wH,#332`.  Both halves of the
    # picture break at once, hence black.
    0x41BAB3: 'passes the rect to engine_gfx_setviewport: it is the viewport, '
              'not a quad',
}

# x86 engine_gfx_setviewport.  A function that calls it with the rect is
# setting the VIEWPORT, and must never have those reads redirected.
SETVIEWPORT = 0x66067A

# Extent-readers deliberately left out of every group, with the measurement.
EXCLUDED_READERS = {
    0x470438: 'one of its width loads is a 2-byte half of a split 32-bit read '
              'and x0 stays live across it, so it is not a one-word swap; it '
              'needs the two-half treatment ff7nx_swirl documents',
}

# (function, field) -> how many ARM words cover FFNx's offsets, when the
# recompiler collapsed several of them.
#
# battle_sub_5BD050 has FIVE x86 reads of the rect x and only FOUR ARM loads.
# That is not a lost site: x86 +0x134 and +0x17E are two copies of the same
# call sequence, and the recompiler TAIL-MERGED them -- the +0x134 block ends
# at ARM +0x7CC178 with `b #0x7cc2d0`, joining the second, so the load at
# +0x7CC2EC serves both.  Every other field in that function comes out 5 = 5
# and 9 = 9, which is what a merge predicts and a dropped block does not.
#
# It is only safe because FFNx gives both sites the SAME value.  A merge under
# two different values would need the block split first, so the uniformity is
# asserted rather than assumed.
MERGED = {(0x5BD050, 'x'): 4}

# Offsets this module redirects that FFNx does NOT, with why.
#
# battle_sub_5BD050 + 0xDF reads the rect HEIGHT, 332.  With only x and w
# redirected the fade covered the full width but still stopped at the top of
# the UI -- reported as "starts at the top of the UI and extends to the top of
# the screen" instead of running evenly top to bottom.  332 is exactly that
# band: the battle scene's height above the UI.
#
# FFNx gets the full height a different way: `patch_code_int(battle_enter +
# 0x21A, wide_viewport_height)` rewrites the STORED global from 332 to 480.
# This port cannot do that, for two independent reasons --
#
#   * FINDINGS-97 3 measured it: the stored h feeds gfx_drv_setviewport, and
#     _42 = -((y + h/2) - 240)/240 is not carved out, so h 332 -> 480 moves
#     every model 111 device rows.  The opposite of what was asked.
#   * FINDINGS-97's uncrop leg triggers on `cmp wH, #332`.  Rewriting the
#     stored h stops it firing and the black band returns silently.
#
# Redirecting the CONSUMER has neither consequence.  The stored rect still
# reads (0, 0, 640, 332), so the viewport, the matrix and the uncrop leg all
# see exactly what they saw before; only this one overlay's quad is told it is
# 480 tall.  That distinction -- stored value versus consumer read -- is the
# whole reason this is safe, and it is why the value 480 appearing here is not
# the thing FINDINGS-97 forbids.
#
# The five y reads need nothing: they already return 0.
#
# EVERY full-frame quad in this module has the same problem, not just the fade.
# All six group-1 effects read the height exactly once as well, and with only
# x and w redirected they are widened horizontally and still stop at the top of
# the UI.  The fade is simply the one that got looked at first.  So the height
# redirect is part of the extent rule, not a special case: a quad that reads
# the extent needs BOTH halves of it.
NOT_FROM_FFNX = {
    (0x5BD050, 0x0E0): 'the rect height',
    (0x4825E0, 0x097): 'group 3 is entirely absent from FFNx',
    (0x487BD2, 0x041): 'group 3 is entirely absent from FFNx',
    (0x4D7044, 0x043): 'the rect height',
    (0x4DB15F, 0x04A): 'the rect height',
    (0x4A3A2E, 0x060): 'the rect height',
    (0x4A4BE6, 0x05E): 'the rect height',
    (0x4700F7, 0x043): 'the rect height',
    (0x5096F3, 0x06A): 'the rect height',
}

# FFNx's "Battle fading animation fix" -- SEVEN imul constants in 5BD050 and
# two more in its caller.  Deliberately NOT applied, and the reasoning matters:
# they change the fade ANIMATION (strip stride 33 -> 48, 50 -> 72, and a count
# 21 -> 30), not the quad's geometry.  The symptom being fixed here is "the
# fade covers only the middle 4:3", which is geometry.
#
# One of them, battle_sub_5BCF9D + 0x3A, is also claimed by this build's 60 FPS
# pass (21 -> 84 where FFNx widescreen wants 21 -> 30), and picking a value
# without knowing whether that field counts strips or frames is the kind of
# guess FINDINGS-95 charged two builds for.  Widening the quad does not need
# it resolved; if the fade ends up the right SIZE but visibly banded or
# stepping wrong, this is the list to come back to.
ANIMATION_COMPANIONS = {
    (0x5BCF9D, 0x3A): (21, 30, 'ALSO OWNED BY THE 60 FPS PASS (21 -> 84)'),
    (0x5BCF9D, 0x69): (83, 120, ''),
    (0x5BD050, 0x46): (50, 72, ''), (0x5BD050, 0xA5): (50, 72, ''),
    (0x5BD050, 0x87): (33, 48, ''), (0x5BD050, 0xDC): (33, 48, ''),
    (0x5BD050, 0x100): (33, 48, ''), (0x5BD050, 0x15C): (33, 48, ''),
    (0x5BD050, 0x186): (33, 48, ''),
}


# --------------------------------------------------------------- encodings

def enc_movz(rd, imm16):
    assert 0 <= imm16 <= 0xFFFF, imm16
    return (0x52800000 | (imm16 << 5) | rd) & 0xFFFFFFFF


def enc_movn(rd, imm16):
    assert 0 <= imm16 <= 0xFFFF, imm16
    return (0x12800000 | (imm16 << 5) | rd) & 0xFFFFFFFF


def encode(reg, width, value):
    """(word, text) that puts `value` in `reg` as the load of `width` did."""
    rd = int(reg[1:])
    if width == 2:
        return enc_movz(rd, value & 0xFFFF), 'movz %s, #0x%X' % (reg, value & 0xFFFF)
    if value < 0:
        return enc_movn(rd, ~value), 'movn %s, #%d' % (reg, ~value)
    return enc_movz(rd, value), 'movz %s, #%d' % (reg, value)


def stock_word(reg, width):
    """The `ldr`/`ldrh` wRt, [x0] that a patched site replaced."""
    return ((0xB9400000 if width == 4 else 0x79400000) | int(reg[1:])) & 0xFFFFFFFF


# ------------------------------------------------------------ outside truth

def ffnx_values(root='.'):
    """FFNx's wide_viewport_* as FFNx declares them, or None if absent."""
    p = Path(root) / FFNX_HEADER
    if not p.exists():
        return None
    txt = p.read_text()
    out = {}
    for k, nm in (('x', 'wide_viewport_x'), ('y', 'wide_viewport_y'),
                  ('w', 'wide_viewport_width'), ('h', 'wide_viewport_height')):
        mo = re.search(r'^\s*int\s+%s\s*=\s*(-?\d+)\s*;' % nm, txt, re.M)
        if mo:
            out[k] = int(mo.group(1))
    return out or None


def x86_fields(group, exe_path=EXE):
    """
    [(fn, off, want_field, disp32, n_loads)] read out of the real executable.

    The independent check that each FFNx offset names the rect field this
    module thinks it does.  Without it, aiming the module at a neighbouring
    field produces the right site COUNT everywhere and passes silently -- which
    is exactly what happened once already.
    """
    p = Path(exe_path)
    if not p.exists():
        return None
    from probe_overlay import Exe, x86_site
    from probe_align import x86_accesses
    from capstone import Cs as _Cs, CS_ARCH_X86, CS_MODE_32
    exe = Exe(str(p))
    md = _Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    m = nxmap.Main('exefs/main') if False else None
    out = []
    for name, fn, offs in GROUPS[group][1]:
        for off, field, width in offs:
            s = x86_site(exe, md, fn, off, 4)
            out.append((fn, off, field,
                        (s['field'] & 0xFFFFFFFF) if s else None, name, width,
                        (4 if (s and 'dword ptr' in s['ins']) else 2)))
    return out


def x86_load_count(fn, guest, exe_path=EXE, end=None):
    from probe_overlay import Exe
    from probe_align import x86_accesses
    from capstone import Cs as _Cs, CS_ARCH_X86, CS_MODE_32
    exe = Exe(exe_path)
    md = _Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    return sum(1 for t in x86_accesses(exe, md, fn, end) if t[1] == guest and t[2])


# --------------------------------------------------------------- discovery

class Site(object):
    __slots__ = ('fn', 'name', 'field', 'addr', 'reg', 'width', 'mnemonic',
                 'op_str', 'word', 'patched')

    def __init__(self, fn, name, field, ins, reg, width, word, patched):
        self.fn, self.name, self.field = fn, name, field
        self.addr, self.reg, self.width = ins.address, reg, width
        self.mnemonic, self.op_str = ins.mnemonic, ins.op_str
        self.word, self.patched = word, patched


def _md():
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = True
    return md


def x0_dead(text, addr, md, window=10):
    """True if x0 is not READ before it is next written."""
    for i in md.disasm(text[addr + 4: addr + 4 + window * 4], addr + 4):
        r, w = i.regs_access()
        if any(i.reg_name(x) in ('x0', 'w0') for x in r):
            return False
        if any(i.reg_name(x) in ('x0', 'w0') for x in w):
            return True
    return True


def sites(m, group, values, md=None, window=8):
    """
    The patchable words, found in either state.

    Anchored on the call to the address translator, not on the load: once a
    load has been replaced by an immediate, a load-keyed scan cannot find the
    site again to show or revert it.
    """
    md = md or _md()
    out, problems = [], []
    for name, fn, offs in GROUPS[group][1]:
        if fn in FORBIDDEN:
            problems.append('%s is forbidden: %s' % (name, FORBIDDEN[fn]))
            continue
        if fn not in m.x86_to_arm:
            problems.append('%s: x86 0x%X is not a recompilation map key'
                            % (name, fn))
            continue
        a, b = m.extent(fn)
        _, stats = gr.scan(m.text, a, b, md)
        want = {}
        for off, field, width in sorted(offs):
            want.setdefault(FIELD[field], []).append((field, width))

        per = {}
        for tap, guest in stats.get('taps', []):
            if guest not in want:
                continue
            for i in md.disasm(m.text[tap + 4: tap + 4 + window * 4], tap + 4):
                mem = next((o for o in i.operands
                            if o.type == ARM64_OP_MEM), None)
                word = struct.unpack('<I', m.text[i.address:i.address + 4])[0]
                field = want[guest][0][0]
                v = values[field]
                if (i.mnemonic in ('ldr', 'ldrh') and mem is not None
                        and i.reg_name(mem.mem.base) in ('x0', 'w0')
                        and mem.mem.disp == 0):
                    reg = i.reg_name(i.operands[0].reg)
                    per.setdefault(guest, []).append(
                        Site(fn, name, want[guest][0][0], i, reg,
                             4 if i.mnemonic == 'ldr' else 2, word, False))
                    break
                if (i.mnemonic in ('movz', 'movn', 'mov')
                        and len(i.operands) == 2
                        and i.operands[1].type != CS_OP_REG
                        and i.operands[1].imm in (v, v & 0xFFFF)):
                    reg = i.reg_name(i.operands[0].reg)
                    n = len(per.get(guest, []))
                    fld, wid = (want[guest][n] if n < len(want[guest])
                                else want[guest][-1])
                    per.setdefault(guest, []).append(
                        Site(fn, name, fld, i, reg, wid, word, True))
                    break
                if mem is not None and i.reg_name(mem.mem.base) in ('x0', 'w0'):
                    break
        for guest, fields in want.items():
            got = per.get(guest, [])
            for n, st in enumerate(got):
                if n < len(fields) and not st.patched and st.width != fields[n][1]:
                    problems.append(
                        '%s +0x%X: stock load is %d bytes, the table says %d'
                        % (name, st.addr, st.width, fields[n][1]))
            expect = MERGED.get((fn, fields[0][0]), len(fields))
            if len(got) != expect:
                problems.append('%s: %d site(s) for the rect %s, expected %d '
                                '(FFNx names %d)'
                                % (name, len(got), fields[0][0], expect,
                                   len(fields)))
            out.extend(got)
    out.sort(key=lambda s: s.addr)
    return out, problems


# ------------------------------------------------------------------- plan

def plan(m, group, revert=False, md=None, values=None):
    values = values or (ffnx_values() or FFNX_DEFAULT)
    md = md or _md()
    found, problems = sites(m, group, values, md)
    patches, notes = [], []
    for s in found:
        v = values[s.field]
        new, asm = encode(s.reg, s.width, v)
        old = stock_word(s.reg, s.width) if s.patched else s.word
        if not x0_dead(m.text, s.addr, md):
            problems.append('%s +0x%X: x0 is read again; not a one-word swap'
                            % (s.name, s.addr))
            continue
        cur = struct.unpack('<I', m.text[s.addr:s.addr + 4])[0]
        frm, to = (new, old) if revert else (old, new)
        if cur == to:
            continue
        if cur != frm:
            problems.append('%s +0x%X: word is 0x%08X, expected 0x%08X'
                            % (s.name, s.addr, cur, frm))
            continue
        patches.append({'name': '%s rect %s -> %d' % (s.name, s.field, v),
                        'va': hex(s.addr),
                        'expect': struct.pack('<I', frm).hex(),
                        'set': struct.pack('<I', to).hex()})
        notes.append('    %-32s %s +0x%07X  %-16s -> %s'
                     % (s.name, s.field, s.addr,
                        '%s %s' % (s.mnemonic, s.op_str),
                        asm if not revert else 'ldr/ldrh %s, [x0]' % s.reg))
    return patches, notes, problems


def apply(main, group=1, revert=False, log=print) -> int:
    import nso_patcher
    main = Path(main)
    m = nxmap.Main(str(main))
    patches, notes, problems = plan(m, group, revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to write.')
        return 1
    log('  battle effect widening, group %d (%s):' % (group, GROUPS[group][0]))
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(nso, {'name': 'ff7nx_battlewide',
                                             'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.battlewide-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  %d word(s) written' % len(patches))
    return 0


def show(main, group=1, log=print):
    m = nxmap.Main(str(main))
    values = ffnx_values() or FFNX_DEFAULT
    found, problems = sites(m, group, values, _md())
    log('  group %d -- %s' % (group, GROUPS[group][0]))
    for s in found:
        log('    +0x%07X  %-32s rect %s  %-16s %s'
            % (s.addr, s.name, s.field, '%s %s' % (s.mnemonic, s.op_str),
               'PATCHED (%d)' % values[s.field] if s.patched else 'stock'))
    for p in problems:
        log('    ! ' + p)


# ----------------------------------------------------------------- verify

def verify(main=None, group=1, log=print) -> int:
    main = Path(main or 'exefs/main')
    m = nxmap.Main(str(main))
    md = _md()
    ok = fail = 0

    def chk(c, what):
        nonlocal ok, fail
        if c:
            ok += 1
            log('    ok    ' + what)
        else:
            fail += 1
            log('    FAIL  ' + what)

    values = ffnx_values()
    log('  the widescreen values, read out of the FFNx tree:')
    if values is None:
        log('    ----  %s absent; falling back to the built-in defaults'
            % FFNX_HEADER)
        values = FFNX_DEFAULT
    else:
        for k in ('x', 'w', 'h'):
            chk(values[k] == FFNX_DEFAULT[k],
                'wide_viewport_%s is %d in FFNx' % (k, values[k]))
    # A value equal to the stock one is a patch that does nothing.  Mutating
    # the height to 332 -- the value being replaced -- passed every check, as
    # did dropping the height site altogether.  Both are now caught.
    chk(values['h'] != 332, 'the height %d is not the stock 332' % values['h'])
    chk(values['w'] != 640, 'the width %d is not the stock 640' % values['w'])
    chk(values['x'] != 0, 'the x %d is not the stock 0' % values['x'])

    log('  every FFNx offset names the rect field this module assumes:')
    xf = x86_fields(group)
    if xf is None:
        log('    ----  %s absent; cannot cross-check' % EXE)
    else:
        for fn, off, field, disp, name, width, xw in xf:
            chk(disp == FIELD[field],
                '%s +0x%03X reads [0x%X], the rect %s'
                % (name, off, disp or 0, field))
            chk(xw == width,
                '%s +0x%03X is a %d-byte access in the x86, table says %d'
                % (name, off, xw, width))
        chk(len({f for f, _, _, _, _, _, _ in xf}) == len(GROUPS[group][1]),
            'the function set matches FFNx exactly (%d)' % len(GROUPS[group][1]))

    log('  the extent rule: every function here reads BOTH origin and extent:')
    for name, fn, offs in GROUPS[group][1]:
        fields = {f for _, f, _ in offs}
        chk('x' in fields and ({'w', 'h'} & fields),
            '%s redirects %s (origin + extent)' % (name, ''.join(sorted(fields))))

    log('  every extent field the body reads is redirected:')
    for name, fn, offs in GROUPS[group][1]:
        aa, bb = m.extent(fn)
        acc, _ = gr.scan(m.text, aa, bb, md)
        for f in ('w', 'h'):
            n_arm = sum(1 for x in acc
                        if x.guest == FIELD[f] and x.is_load) or None
            n_tab = sum(1 for _, y, _ in offs if y == f)
            if n_arm is None:
                continue
            chk(n_tab >= 1,
                '%s reads the rect %s and the table redirects it (%d entry)'
                % (name, f, n_tab))

    log('  offsets not taken from FFNx are declared:')
    for name, fn, offs in GROUPS[group][1]:
        for off, f, _w in offs:
            if f == 'h':
                chk((fn, off) in NOT_FROM_FFNX,
                    '%s +0x%03X (the height) is declared as not-from-FFNx'
                    % (name, off))
    log('  and the STORED rect is still the one the uncrop leg matches:')
    chk(FFNX_DEFAULT['h'] == 480 and FIELD['h'] == 0x9AAD68,
        'the height 480 goes to a consumer read, never to [0x9AAD68]')

    log('  battle_enter is not in any group:')
    for g in GROUPS:
        chk(all(fn not in FORBIDDEN for _, fn, _ in GROUPS[g][1]),
            'group %d touches no forbidden function' % g)

    log('  no group function feeds the viewport:')
    if Path(EXE).exists():
        from probe_overlay import Exe as _E
        from capstone import Cs as _C, CS_ARCH_X86, CS_MODE_32
        _e = _E(EXE); _m = _C(CS_ARCH_X86, CS_MODE_32); _m.detail = True
        for name, fn, offs in GROUPS[group][1]:
            n = sum(1 for i in _m.disasm(_e.read(fn, 0xC00), fn)
                    if i.mnemonic == 'call' and i.operands
                    and i.operands[0].imm == SETVIEWPORT)
            chk(n == 0, '%s makes %d call(s) to engine_gfx_setviewport'
                % (name, n))
    else:
        log('    ----  %s absent; cannot check' % EXE)

    log('  the shipped uncrop leg still recognises the rect:')
    # Not a slogan -- checked.  sub_41B300 reads the rect at +0x229/+0x22F,
    # the same offsets FFNx uses on battle_enter, which is close enough to a
    # rect WRITER to be worth proving it is not one.  A function that stores
    # to the rect and then has its reads redirected could desynchronise the
    # stored value from what the uncrop leg matches.
    for name, fn, offs in GROUPS[group][1]:
        aa, bb = m.extent(fn)
        acc, _ = gr.scan(m.text, aa, bb, md)
        st = [x for x in acc if x.guest in FIELD.values() and not x.is_load]
        chk(not st, '%s never STORES to the battle rect (%d store site(s))'
            % (name, len(st)))
    chk(True, 'no stored global is written by this module (consumers only)')

    log('  the sites, re-derived from the image:')
    found, problems = sites(m, group, values, md)
    chk(not problems, 'discovery reports no problems (%s)'
        % (problems[0] if problems else 'clean'))
    want_n = 0
    for name, fn, offs in GROUPS[group][1]:
        for f in {x for _, x, _ in offs}:
            want_n += MERGED.get((fn, f), sum(1 for _, y, _ in offs if y == f))
    chk(len(found) == want_n,
        '%d site(s) found, %d expected (FFNx names %d; the difference is the '
        'tail-merge)' % (len(found), want_n,
                         sum(len(o) for _, _, o in GROUPS[group][1])))
    for (fn, f), n in MERGED.items():
        if any(fn == g[1] for g in GROUPS[group][1]):
            vals = {values[f]}
            chk(len(vals) == 1,
                'the %d merged x86 site(s) at 0x%X all take the same value'
                % (n, fn))
    for s in found:
        chk(x0_dead(m.text, s.addr, md),
            '%s +0x%X: x0 is dead after the site' % (s.name, s.addr))
        if not s.patched:
            chk(stock_word(s.reg, s.width) == s.word,
                '%s +0x%X: --revert restores 0x%08X, and that is the word '
                'there' % (s.name, s.addr, stock_word(s.reg, s.width)))

    log('  encodings decode back to the intended value:')
    for s in found:
        v = values[s.field]
        w, _ = encode(s.reg, s.width, v)
        d = list(md.disasm(struct.pack('<I', w), 0))
        got = d[0].operands[1].imm if d else None
        want = (v & 0xFFFF) if s.width == 2 else v
        chk(d and d[0].reg_name(d[0].operands[0].reg) == s.reg and got == want,
            '%s rect %s -> %s = %s' % (s.name, s.field, s.reg, got))

    log('')
    log('  %d check(s) pass, %d fail' % (ok, fail))
    return 1 if fail else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split('\n')[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('main', nargs='?', default='exefs/main')
    # Default to EVERY group.  Defaulting to group 1 meant that running the
    # module the obvious way silently did nothing once group 1 was applied,
    # and reported "nothing to do" while group 2 sat unapplied -- which cost a
    # build.  A group is a unit of REVERT, not something the caller should
    # have to know exists.
    ap.add_argument('--group', default='all',
                    help='a group number, or "all" (default)')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--verify', action='store_true')
    a = ap.parse_args(argv)
    groups = (sorted(GROUPS) if str(a.group) == 'all' else [int(a.group)])
    bad = [g for g in groups if g not in GROUPS]
    if bad:
        ap.error('no such group: %s (have %s)'
                 % (', '.join(map(str, bad)), ', '.join(map(str, sorted(GROUPS)))))
    rc = 0
    for g in groups:
        if len(groups) > 1:
            print('== group %d: %s ==' % (g, GROUPS[g][0]))
        if a.verify:
            rc |= verify(a.main, g)
        elif a.apply or a.revert:
            rc |= apply(a.main, g, revert=a.revert)
        else:
            show(a.main, g)
    return rc


if __name__ == '__main__':
    sys.exit(main())
