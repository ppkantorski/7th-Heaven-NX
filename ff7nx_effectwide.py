"""
ff7nx_effectwide.py -- local 4:3 effect geometry that must fill 16:9.

WHAT THIS IS
============
Ultima's wash covered only the middle 640 x 360 of the frame.  KOTR's BG_1
star field built only three 256-wide columns and deliberately shortened its
second row to 76 units, making a 768 x 332 field.  This module widens both
effects to the full 854 x 480 frame.

It changes no shared global definition.  Every widened value is materialised
locally in the effect that consumes it, so the stored battle rect and all of
its other consumers stay stock.

KOTR is patched at its actual BG_1 consumer.  The earlier investigation
missed it because it censused calls to sub_68A115; this one uses the adjacent
resource accessor sub_68A155.  x86 0x481A4E fetches slot 0x12 from KOTR's
0xA853C8 resource table twice, once for each row.  The marker-texture hardware
run then matches the code exactly: three columns at scroll-256/scroll/
scroll+256 and rows ending at y=256 and y=332.


=======================================================================
GROUP 1 -- ULTIMA'S WASH.  x86 0x57A20A AND 0x57A38C.
=======================================================================

HOW IT WAS FOUND, AND WHY SIX EARLIER WALKS MISSED IT
-----------------------------------------------------
FFNx patches exactly one function of this shape:

    patch_code_int(shadow_flare_draw_white_bg_57747E + 0x18, wide_viewport_x);
    patch_code_int(shadow_flare_draw_white_bg_57747E + 0x1F, wide_viewport_width / 2);

`ff7nx_battlewide.apply_white_flash` already ships that patch and the user
confirms attack flashes and limit-break flashes are fixed by it.

0x57747E does NOT contain the constant 640.  It stores 0 and 0x13F (319) and
the renderer doubles every vertex word:

    0057749A  mov  [ebp-0x10], 0x13F     ; right = 319
    ...
    0057758C  mov  cx, [eax+0xC]         ; every vertex, both 16-bit halves
    00577590  shl  cx, 1                 ;   x *= 2, y *= 2
    ...
    005775E5  call 0x66A47E              ; submit the band

319 * 2 = 638.  THAT is the 4:3 region, and it is why HANDOFF-273's scan --
"only 2 functions in the whole battle range use both 640 and 332" -- could
not see this family.  The constant is 319, not 640.

So the executable was scanned for the SIGNATURE instead: a `mov [ebp-k], imm`
whose immediate has 0x013F in its low half, inside a function that calls the
quad submitter 0x66A47E.  Across all 217 functions that call 0x66A47E there
are exactly THREE hits, and no others anywhere in 0x401000..0x7B562B:

    0x57747E   0x57749A  [ebp-0x10] = 0x00013F     Shadow Flare  -- ALREADY PATCHED
    0x57A20A   0x57A229  [ebp-0x20] = 0x00013F     Ultima        -- this module
    0x57A38C   0x57A3AB  [ebp-0x24] = 0x5A013F     Ultima        -- this module
               0x57A549  [ebp-0x24] = 0x5A013F     Ultima        -- this module

The family is closed.  One member was already known to be this exact defect
and its fix is already shipping.  The other two are Ultima's.

THAT THEY ARE ULTIMA'S IS A SHORT, UNAMBIGUOUS CHAIN
-----------------------------------------------------
    effect 52 -> 0x579751          MAGIC-EFFECT-INVENTORY.md, verified 3 ways
      0x579751 +0x10  call 0x57976B
      0x57976B        spawns 0x5797B1 through 0x5BEC50
      0x5797B1        spawns 0x5797EF
      0x5797EF +0x25F spawns 0x579D4B and sets its record:
                          dword[rec+4] = 0
                          dword[rec+8] = 0x242424        <-- the wash colour
      0x579D4B +0x75  call 0x57A20A(obj, phase)          phase <  0x20
      0x579D4B +0xD9  call 0x57A38C(obj, phase - 0x20)   phase >= 0x20

Both builders take their colour from obj+0x44, which 0x579D4B fills from those
two record fields, and both end with `mov [0xC1EA68], eax` -- the same draw-list
tail 0x57747E writes.  0x242424 additive over a blend of (0.2, 1.0) is the
white layer in the user's screenshot; 0x57A20A opens it, 0x57A38C closes it.

WHAT IS CHANGED
---------------
Same two values FFNx writes into 0x57747E, in the same units (pre-doubling):

    left  =  -107     (wide_viewport_x)
    right =   427     (wide_viewport_width / 2)

FFNx writes -107 as a full 32-bit int, so the high half -- which is Y -- picks
up 0xFFFF and the left vertex sits one unit above the right.  Two pixels after
doubling, on a band 1068 wide.  `apply_white_flash` already ships exactly that
and it is not visible.  This module reproduces it rather than inventing tighter
numbers, because the shipped-and-confirmed form is the one with evidence
behind it.

0x57A38C carries Y = 90 in the high half of both constants, so its two words
cannot be a bare MOVZ.  The recompiler also CSE'd them into one register:

    00691F14  mov  w22, #0x5a0000              <-- one base for both pairs
    00691FA0  str  w22, [x0]                   ; [ebp-0x1C] = left
    00691FB0  add  w8, w22, #0x13f             ; [ebp-0x24] = right
    006927E4  str  w22, [x0]                   ; second half of the wash
    006927F4  add  w8, w22, #0x13f

w22 is dead before 0x691F14 and redefined at 0x692840, so its live range is
exactly these four sites.  One three-word cave rewrites the base to
0x005AFF95 (x = -107, y = 90) and the two ADDs become #0x216 (534 = 427 - -107).


"""
import os
import shutil
import struct
import tempfile
from pathlib import Path

import a64 as A
import ff7nx_cave
import nxmap

EFFECTWIDE_ENV = 'SEVENTH_NX_EFFECT_WIDE'

# The frame, in the 2D coordinates every battle overlay in this project uses.
WIDE_X = -107
WIDE_W = 854
WIDE_H = 480

# THE VERTICAL HALF, MEASURED AFTER THE FIRST HARDWARE TEST
# ---------------------------------------------------------
# Widening x alone made Ultima's wash reach both edges and stop above the UI,
# because the band walk is a second, independent 4:3 constant:
#
#   0x57A20A   step = obj+0x3C = 6,  count = obj+0x40 = 30   ->   y 0..180
#   0x57A38C   step = obj+0x3C = 3,  count = obj+0x40 = 30   ->   y 90 -+ 90
#
# Doubled, both cover y 0..360.  The frame is 0..480, and 360 = 480 * 0.75 --
# the same three-quarters the vertex shader applies horizontally.  So the wash
# was 4:3 in BOTH axes and x was only half the defect.
#
# The count is NOT the lever.  0x57A38C compares its band index against the
# phase argument (`cmp [ebp-0x28], [ebp+0xC]` at 0x57A3F0) to decide which
# bands have retracted yet, so raising the count would stretch the animation
# in time as well as space.  The STEP is the lever: taller bands, same number
# of them, same timing, and the same number of draw calls.
#
#   step 6 -> 8   gives 8 * 30  = 240 -> 480 doubled
#   step 3 -> 4   gives 4 * 30  = 120 each way from the centre
#   centre 90 -> 120 keeps the iris centred (90/180 == 120/240)
#
# Every number scales by exactly 4/3, which is what "fill the screen evenly"
# means here.
TALL_STEP_A = 8         # 0x57A20A, obj+0x3C
TALL_STEP_B = 4         # 0x57A38C, obj+0x3C
TALL_CENTRE = 120       # 0x57A38C, the y both its loops start from

# --- group 2: KOTR BG_1 star field ---------------------------------------
#
# x86 0x481867 writes x=scroll-256 and calls 0x481A4E three times, adding 256
# before each later call.  Each call submits two BG_1 quads.  The hardware
# marker therefore showed exactly 3 columns x 2 rows at a 256-game-unit pitch.
# A wide frame spans -107..747, so every scroll phase needs five columns:
#
#   scroll-512, scroll-256, scroll, scroll+256, scroll+512
#
# The first change moves the existing three-column run left by one tile.  A
# cave after its third submission runs the translated increment/call/store
# block twice.  Those are real extra draws with the original 256-unit pitch;
# no existing tile is widened and the texture is not stretched.  The cave
# stores its two-iteration counter temporarily in w21, then restores w21's
# live stock value before returning to the updater.
KOTR_X_START = 0x0023E72C            # sub w22, w8, #0x100
KOTR_X_START_STOCK = 0x51040116
KOTR_X_START_WIDE = A.sub_imm(22, 8, 0x200)

# 0x481A4E's second row starts at y=256.  Stock then overwrites its height
# with 0x4C, ending the field at 332 exactly.  480-256 = 224 (0xE0) extends
# the row's source/geometry rectangle to the frame bottom at the same scale.
KOTR_ROW2_HEIGHT = 0x0023EEB0         # mov w8, #0x4c
KOTR_ROW2_HEIGHT_STOCK = 0x52800988
KOTR_ROW2_HEIGHT_WIDE = 0x52801C08       # mov w8, #0xe0 (480 - 256)

# Hook immediately after the third stock column has updated [0xBFB2E0].  Its
# displaced MOVZ is position independent and is executed in the cave before
# returning to the following MOVK.
KOTR_EXTRA_HOOK = 0x0023E92C
KOTR_EXTRA_HOOK_STOCK = 0x5281CD80    # mov w0, #0xe6c
KOTR_DRAW_ARM = 0x0023EAB0            # translated x86 0x481A4E
KOTR_EXTRA_COLUMNS = 2
KOTR_W21_STOCK = 0x00A85450

# One translated "x += 256; draw column; store draw-list tail" block.  Every
# word is recorded so a changed recompiler layout is rejected rather than
# copied blindly.  BL words are relocated to their original target when laid
# out in the cave.
KOTR_TEMPLATE_START = 0x0023E7D4
KOTR_TEMPLATE_STOCK = (
    0x2A1403E0, 0x943AF6F2, 0x79400008, 0x2A1403E0,
    0x11040119, 0x790002F9, 0x943AF6ED, 0x79000019,
    0x2A1303E0, 0x943AF6EA, 0xB94012E8, 0xB9400019,
    0x51001100, 0xB90006F9, 0xB90012E0, 0x943AF6E4,
    0xB9000019, 0x2A1603E0, 0x943AF6E1, 0xB9400008,
    0x0B180119, 0xB94012E8, 0xB9000AF9, 0x51001100,
    0xB90012E0, 0x943AF6DA, 0xB9000019, 0xB94012E8,
    0x51001100, 0xB90012E0, 0x943AF6D5, 0xB9000014,
    0xB94012E8, 0x51001108, 0xB90012E8, 0x94000094,
    0xB94012E8, 0xB94002F9, 0x11003108, 0x2A1303E0,
    0xB90012E8, 0x943AF6CA, 0xB9000019,
)
KOTR_TEMPLATE_END = KOTR_TEMPLATE_START + 4 * len(KOTR_TEMPLATE_STOCK)

KOTR_ANCHORS = {
    0x0023E724: 0xB9400008,            # load scroll before KOTR_X_START
    0x0023E730: 0xB90002F6,            # scratch store after it
    0x0023E738: 0x79000016,            # store initial x to 0x7F5CE0
    0x0023E928: 0xB9000014,            # third draw-list tail stored
    0x0023E930: 0x72A01B80,            # displaced MOVZ's following MOVK
    0x0023EEAC: 0x943AF53D,            # translate obj+0x12
    0x0023EEB4: 0x79000008,            # store second-row height
}

# --- group 1: Ultima ------------------------------------------------------
# 0x57A20A, ARM 0x691740.  Y is 0 in both constants, so both are direct.
ULT_A_LEFT = 0x006917EC          # str wzr, [x0]        <- mov [ebp-0x18], 0
ULT_A_LEFT_STOCK = 0xB900001F
ULT_A_RIGHT = 0x006917FC         # mov w8, #0x13f       <- mov [ebp-0x20], 0x13F
ULT_A_RIGHT_STOCK = 0x528027E8
ULT_A_RIGHT_WIDE = 0x52803568    # mov w8, #427
STR_W8_X0 = 0xB9000008

# 0x57A38C, ARM 0x691EE0.  Y = 90 in both constants and the recompiler shares
# one base register across both halves of the wash.
ULT_B_BASE = 0x00691F14          # mov w22, #0x5a0000
ULT_B_BASE_STOCK = 0x52A00B56
ULT_B_SPAN = (0x00691FB0, 0x006927F4)    # add w8, w22, #0x13f
ULT_B_SPAN_STOCK = 0x1104FEC8
ULT_B_SPAN_WIDE = A.add_imm(8, 22, WIDE_W // 2 - WIDE_X)   # add w8, w22, #534
ULT_B_BASE_VALUE = (TALL_CENTRE << 16) | (WIDE_X & 0xFFFF)  # 0x0078FF95

# The two band-step stores in 0x579D4B (ARM 0x690A10).  Both are
# `orr w8, wzr, #imm` -- capstone prints them as `mov`, they are NOT MOVZ, and
# each is bracketed by `add w0, w8, #0x3c` before and `str w8, [x0]` after.
ULT_STEP_A = 0x00690CF8          # obj+0x3C = 6   for 0x57A20A
ULT_STEP_A_STOCK = 0x321F07E8    # orr w8, wzr, #6
ULT_STEP_B = 0x00690B98          # obj+0x3C = 3   for 0x57A38C
ULT_STEP_B_STOCK = 0x320007E8    # orr w8, wzr, #3

# Sites that must be stock for the group to be recognisable at all.  If the
# recompiler layout ever differs from the module this was measured on, these
# fail the build instead of writing into the wrong instruction.
#
# 0x00692840 is where w22 stops carrying the wash base.  capstone prints it as
# `mov w22, #-0xffffff`, but it is an ORR-with-bitmask-immediate, NOT a MOVZ --
# the same alias that has produced three wrong answers in this project.  The
# literal word is recorded here, never the mnemonic.
ULT_ANCHORS = {
    0x006917E8: 0x9429AAEE,      # bl 0x10FC3A0 feeding ULT_A_LEFT
    0x006917F0: 0xB9401668,      # ldr w8, [x19, #0x14]
    0x00691800: 0xB9000008,      # str w8, [x0]          after ULT_A_RIGHT
    0x00691FA0: 0xB9000016,      # str w22, [x0]         pair 1 left
    0x00691FB4: 0xB9000008,      # str w8, [x0]          pair 1 right
    0x006927E4: 0xB9000016,      # str w22, [x0]         pair 2 left
    0x006927F8: 0xB9000008,      # str w8, [x0]          pair 2 right
    0x00692840: 0x320823F6,      # orr w22, wzr, #0x1000001   -- w22 redefined
    # The band-step stores must sit in their measured brackets, or the step
    # replacement would land on some other constant.
    0x00690CF4: 0x9429ADAB,      # bl 0x10FC3A0   feeding ULT_STEP_A
    0x00690CEC: 0x1100F100,      # add w0, w8, #0x3c
    0x00690CFC: 0xB9000008,      # str w8, [x0]
    0x00690D1C: 0x321F0FE8,      # orr w8, wzr, #0x1e   count -- stays stock
    0x00690B94: 0x9429AE03,      # bl 0x10FC3A0   feeding ULT_STEP_B
    0x00690B8C: 0x1100F100,      # add w0, w8, #0x3c
    0x00690B9C: 0xB9000008,      # str w8, [x0]
    0x00690BBC: 0x321F0FE8,      # orr w8, wzr, #0x1e   count -- stays stock
}

def enabled() -> bool:
    """ON with the battle-wide gate, overridable for an A/B."""
    v = os.environ.get(EFFECTWIDE_ENV)
    if v is not None:
        return v.strip().lower() not in ('', '0', 'off', 'no', 'false')
    import ff7nx_battlewide
    return ff7nx_battlewide.enabled()






def enc_movz(rd, imm16):
    assert 0 <= imm16 <= 0xFFFF, imm16
    return (0x52800000 | (imm16 << 5) | rd) & 0xFFFFFFFF


def enc_movn(rd, imm16):
    """movn Wd, #imm16 -> Wd = ~imm16, i.e. the value -(imm16 + 1)."""
    assert 0 <= imm16 <= 0xFFFF, imm16
    return (0x12800000 | (imm16 << 5) | rd) & 0xFFFFFFFF


def enc_const(rd, value):
    """One word that puts a small signed constant in Wd."""
    return enc_movn(rd, -value - 1) if value < 0 else enc_movz(rd, value)


def _word(img, va):
    return struct.unpack('<I', img[va:va + 4])[0]


def _fmt(w):
    return struct.pack('<I', w).hex()


def _branch_target(va, word):
    if (word & 0xFC000000) != 0x14000000:
        return None
    imm = word & 0x03FFFFFF
    if imm & (1 << 25):
        imm -= 1 << 26
    return va + imm * 4


def _bl_target(va, word):
    """Target of one BL, or None.  Used to relocate the recorded KOTR block."""
    if (word & 0xFC000000) != 0x94000000:
        return None
    imm = word & 0x03FFFFFF
    if imm & (1 << 25):
        imm -= 1 << 26
    return va + imm * 4


def _walk_cave(img, hook, limit=24):
    """Addresses in one installed cave, including its return branch."""
    pc = _branch_target(hook, _word(img, hook))
    if pc is None:
        return None, 'hook +0x%X is not a branch' % hook
    out = []
    for _ in range(limit):
        if pc == hook + 4:
            return out, None
        out.append(pc)
        tgt = _branch_target(pc, _word(img, pc))
        pc = tgt if tgt is not None else pc + 4
    return None, 'cave from +0x%X did not return within %d words' % (hook, limit)


def _ult_left_body():
    """w8 = -107, store it.  Identical in shape to the white-flash cave."""
    return [enc_const(8, WIDE_X), STR_W8_X0]


def _ult_base_body():
    """w22 = 0x005AFF95 -- x = -107 in the low half, y = 90 in the high."""
    return A.movz_movk(22, ULT_B_BASE_VALUE)


def _anchor_problems(img, anchors, what):
    bad = []
    for va, expected in sorted(anchors.items()):
        have = _word(img, va)
        if have != expected:
            bad.append('%s anchor +0x%X is %08X, expected %08X'
                       % (what, va, have, expected))
    return bad


def _plain_swap(img, va, stock, wide, name, revert, ps, notes, problems):
    """One-word MOVZ-for-load/MOV replacement, with a stock check."""
    cur = _word(img, va)
    frm, to = (wide, stock) if revert else (stock, wide)
    if cur == to:
        return
    if cur != frm:
        problems.append('%s +0x%X is %08X, expected %08X or %08X'
                        % (name, va, cur, stock, wide))
        return
    ps.append({'name': name, 'va': hex(va),
               'expect': _fmt(frm), 'set': _fmt(to)})
    notes.append('    %-24s %s @ +0x%07X'
                 % (name, 'wide -> stock' if revert else 'stock -> wide', va))


def _cave_site(m, hook, stock, body, name, revert, ps, notes, problems,
               pool=None):
    """Install (or return) a cave that replaces a single displaced word.

    `pool` MUST be shared by every cave in one plan.  A HolePool decides what
    is free by reading the image, and nothing is written until the whole plan
    is accepted -- so two pools built from the same unmodified image hand out
    the same hole twice.  nso_patcher's expect check catches that, but only
    after the first cave is already in the spec.
    """
    img = m.img
    cur = _word(img, hook)
    if _branch_target(hook, cur) is not None:
        cave, why = _walk_cave(img, hook)
        if cave is None:
            problems.append('%s: %s' % (name, why))
            return
        logical = [_word(img, va) for va in cave
                   if _branch_target(va, _word(img, va)) is None]
        if logical != body:
            problems.append('%s cave is %s, expected %s'
                            % (name,
                               ' '.join('%08X' % x for x in logical),
                               ' '.join('%08X' % x for x in body)))
            return
        if revert:
            ps.append({'name': 'restore %s' % name, 'va': hex(hook),
                       'expect': _fmt(cur), 'set': _fmt(stock)})
            for va in cave:
                ps.append({'name': 'clear %s cave +0x%X' % (name, va),
                           'va': hex(va), 'expect': _fmt(_word(img, va)),
                           'set': '00000000'})
            notes.append('    %-24s wide -> stock (%d cave word(s) returned)'
                         % (name, len(cave)))
        return
    if cur != stock:
        problems.append('%s +0x%X is %08X, neither stock nor branch'
                        % (name, hook, cur))
        return
    if revert:
        return
    if pool is None:
        pool = ff7nx_cave.HolePool(img, starts=set(m.arm_starts))
    runs = pool.take(len(body) + 1)
    addrs = ff7nx_cave.slots(runs, len(body) + 1)
    words = body + [A.b(addrs[-1], hook + 4)]
    out = ff7nx_cave.link(runs, words)
    out[hook] = A.b(hook, addrs[0])
    for va in sorted(out):
        old = _word(img, va)
        if va != hook and old != 0:
            problems.append('%s cave +0x%X is %08X, not padding'
                            % (name, va, old))
            continue
        ps.append({'name': '%s +0x%X' % (name, va), 'va': hex(va),
                   'expect': _fmt(old), 'set': _fmt(out[va])})
    notes.append('    %-24s stock -> wide (%d words, entry +0x%X)'
                 % (name, len(out) - 1, addrs[0]))


def ultima_plan(m, revert=False):
    """Ultima's wash: 640 -> the full frame, both phases."""
    img = m.img
    ps, notes, problems = [], [], []
    problems += _anchor_problems(img, ULT_ANCHORS, 'ultima wash')
    if problems:
        return ps, notes, problems

    # ONE pool for both caves -- see _cave_site's docstring.
    pool = (None if revert
            else ff7nx_cave.HolePool(img, starts=set(m.arm_starts)))

    # 0x57A20A -- right edge is a direct MOV immediate.
    _plain_swap(img, ULT_A_RIGHT, ULT_A_RIGHT_STOCK, ULT_A_RIGHT_WIDE,
                'ultima wash right', revert, ps, notes, problems)
    # 0x57A20A -- x=0 was strength-reduced to STR WZR; materialise -107.
    _cave_site(m, ULT_A_LEFT, ULT_A_LEFT_STOCK, _ult_left_body(),
               'ultima wash left', revert, ps, notes, problems, pool)
    # 0x57A38C -- one shared base for both halves, then the two spans.
    _cave_site(m, ULT_B_BASE, ULT_B_BASE_STOCK, _ult_base_body(),
               'ultima wash base', revert, ps, notes, problems, pool)
    for va in ULT_B_SPAN:
        _plain_swap(img, va, ULT_B_SPAN_STOCK, ULT_B_SPAN_WIDE,
                    'ultima wash span', revert, ps, notes, problems)
    # The vertical half: taller bands, same count, same timing.
    _plain_swap(img, ULT_STEP_A, ULT_STEP_A_STOCK, enc_movz(8, TALL_STEP_A),
                'ultima band step A', revert, ps, notes, problems)
    _plain_swap(img, ULT_STEP_B, ULT_STEP_B_STOCK, enc_movz(8, TALL_STEP_B),
                'ultima band step B', revert, ps, notes, problems)

    if not problems and ps and not revert:
        notes.append('      x: left -107 / right 427 pre-doubling, exactly '
                     'what FFNx writes into shadow_flare_draw_white_bg_57747E')
        notes.append('      y: step %d/%d and centre %d -- 360 -> 480 doubled, '
                     'band count and timing unchanged'
                     % (TALL_STEP_A, TALL_STEP_B, TALL_CENTRE))
    return ps, notes, problems


def _kotr_template_problems(img):
    bad = []
    for i, expected in enumerate(KOTR_TEMPLATE_STOCK):
        va = KOTR_TEMPLATE_START + 4 * i
        have = _word(img, va)
        if have != expected:
            bad.append('KOTR column template +0x%X is %08X, expected %08X'
                       % (va, have, expected))
    return bad


def _kotr_extra_body(addrs):
    """Run one relocated KOTR 256-unit column submission twice.

    x19 still carries 0xBFB2E0 at the hook.  The third stock call has reused
    x20 and x22 for its result and object pointer, so restore the three
    callee-saved constants consumed by the recorded first-to-second-column
    block.  w21 is live across the hook but unused by the recorded block and
    preserved by its native calls, so it is a safe loop counter once its stock
    value is restored before returning.  Keeping one copy materially reduces
    the scattered cave's cold instruction footprint without changing a draw.
    """
    out = []

    def put(word):
        out.append(word)

    for word in A.movz_movk(20, 0x007F5CE0):
        put(word)
    for word in A.movz_movk(22, 0x00BE1128):
        put(word)
    put(A.movz(24, 0x406C))

    put(A.movz(21, KOTR_EXTRA_COLUMNS))
    loop_i = len(out)
    for i, word in enumerate(KOTR_TEMPLATE_STOCK):
        target = _bl_target(KOTR_TEMPLATE_START + 4 * i, word)
        put(A.bl(addrs[len(out)], target) if target is not None else word)
    put(A.sub_imm(21, 21, 1))
    put(A.cbnz(21, addrs[len(out)], addrs[loop_i]))
    for word in A.movz_movk(21, KOTR_W21_STOCK):
        put(word)
    return out


KOTR_EXTRA_BODY_WORDS = 5 + 1 + len(KOTR_TEMPLATE_STOCK) + 2 + 2
KOTR_CAVE_WALK_LIMIT = 2 * (KOTR_EXTRA_BODY_WORDS + 2) + 4


def _kotr_cave_site(m, revert, ps, notes, problems, pool=None):
    """Install/recognise/remove the two-column KOTR submission cave."""
    img = m.img
    cur = _word(img, KOTR_EXTRA_HOOK)
    if _branch_target(KOTR_EXTRA_HOOK, cur) is not None:
        cave, why = _walk_cave(img, KOTR_EXTRA_HOOK,
                               limit=KOTR_CAVE_WALK_LIMIT)
        if cave is None:
            problems.append('KOTR extra columns: %s' % why)
            return
        logical_addrs = [va for va in cave
                         if _branch_target(va, _word(img, va)) is None]
        expected = (_kotr_extra_body(logical_addrs[:KOTR_EXTRA_BODY_WORDS])
                    + [KOTR_EXTRA_HOOK_STOCK])
        logical = [_word(img, va) for va in logical_addrs]
        if logical != expected:
            problems.append('KOTR extra-column cave body does not match the '
                            'recorded two-iteration submission loop')
            return
        if revert:
            ps.append({'name': 'restore KOTR extra-column hook',
                       'va': hex(KOTR_EXTRA_HOOK), 'expect': _fmt(cur),
                       'set': _fmt(KOTR_EXTRA_HOOK_STOCK)})
            for va in cave:
                ps.append({'name': 'clear KOTR extra-column cave +0x%X' % va,
                           'va': hex(va), 'expect': _fmt(_word(img, va)),
                           'set': '00000000'})
            notes.append('    %-24s wide -> stock (%d cave word(s) returned)'
                         % ('KOTR extra columns', len(cave)))
        return

    if cur != KOTR_EXTRA_HOOK_STOCK:
        problems.append('KOTR extra-column hook +0x%X is %08X, neither stock '
                        'nor branch' % (KOTR_EXTRA_HOOK, cur))
        return
    if revert:
        return
    if pool is None:
        pool = ff7nx_cave.HolePool(img, starts=set(m.arm_starts))

    def build(_entry, addr):
        body_addrs = [addr(i) for i in range(KOTR_EXTRA_BODY_WORDS)]
        body = _kotr_extra_body(body_addrs)
        assert len(body) == KOTR_EXTRA_BODY_WORDS
        displaced_i = len(body)
        return_i = displaced_i + 1
        return (body + [KOTR_EXTRA_HOOK_STOCK,
                        A.b(addr(return_i), KOTR_EXTRA_HOOK + 4)])

    entry, out = ff7nx_cave.emit_laid_out(pool, build)
    out[KOTR_EXTRA_HOOK] = A.b(KOTR_EXTRA_HOOK, entry)
    for va in sorted(out):
        old = _word(img, va)
        if va != KOTR_EXTRA_HOOK and old != 0:
            problems.append('KOTR extra-column cave +0x%X is %08X, not '
                            'padding' % (va, old))
            continue
        ps.append({'name': 'KOTR extra columns +0x%X' % va,
                   'va': hex(va), 'expect': _fmt(old),
                   'set': _fmt(out[va])})
    notes.append('    %-24s stock -> wide (3 -> 5 columns, looped cave '
                 'entry +0x%X)'
                 % ('KOTR extra columns', entry))


def kotr_plan(m, revert=False):
    """KOTR BG_1: five 256-wide columns and rows through y=480."""
    img = m.img
    ps, notes, problems = [], [], []
    problems += _anchor_problems(img, KOTR_ANCHORS, 'KOTR BG_1')
    problems += _kotr_template_problems(img)
    if problems:
        return ps, notes, problems

    _plain_swap(img, KOTR_X_START, KOTR_X_START_STOCK, KOTR_X_START_WIDE,
                'KOTR first column x', revert, ps, notes, problems)
    _plain_swap(img, KOTR_ROW2_HEIGHT, KOTR_ROW2_HEIGHT_STOCK,
                KOTR_ROW2_HEIGHT_WIDE, 'KOTR second-row height', revert,
                ps, notes, problems)
    pool = (None if revert
            else ff7nx_cave.HolePool(img, starts=set(m.arm_starts)))
    _kotr_cave_site(m, revert, ps, notes, problems, pool)

    if not problems and ps and not revert:
        notes.append('      x: scroll-512 through scroll+512 at the original '
                     '256-unit pitch; two real BG_1 column submissions added')
        notes.append('      y: second row remains at 256 and grows 76 -> 224, '
                     'so the two rows end at 480 without stretching a tile')
    return ps, notes, problems










def _write(main, name, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(nso, {'name': name, 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.effectwide-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _run(main, planner, title, spec_name, revert, log, **kw):
    main = Path(main)
    m = nxmap.Main(str(main))
    patches, notes, problems = planner(m, revert=revert, **kw)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to write %s.' % title)
        return 1
    log('  %s:' % title)
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    _write(main, spec_name, patches, log)
    log('  %d %s word(s) written' % (len(patches), title))
    return 0


def apply_ultima(main, revert=False, log=print) -> int:
    return _run(main, ultima_plan, 'ultima full-frame wash',
                'ff7nx_effectwide_ultima', revert, log)


def apply_kotr(main, revert=False, log=print) -> int:
    return _run(main, kotr_plan, 'KOTR BG_1 full-frame field',
                'ff7nx_effectwide_kotr', revert, log)








def apply_all(main, revert=False, log=print) -> int:
    rc = 0
    rc |= apply_ultima(main, revert=revert, log=log)
    rc |= apply_kotr(main, revert=revert, log=log)
    return rc


def _verify(main, log=print) -> int:
    """Disassemble what was written and prove it says what it should."""
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    m = nxmap.Main(str(main))
    img, bad = m.img, 0

    def dis(va):
        return next(md.disasm(img[va:va + 4], va), None)

    def chk(cond, what):
        nonlocal bad
        log('  %s %s' % ('ok  ' if cond else 'FAIL', what))
        if not cond:
            bad += 1

    i = dis(ULT_A_RIGHT)
    chk(_word(img, ULT_A_RIGHT) in (ULT_A_RIGHT_STOCK, ULT_A_RIGHT_WIDE),
        'ultima right +0x%X is stock or 427: %s %s'
        % (ULT_A_RIGHT, i.mnemonic, i.op_str))
    for va in ULT_B_SPAN:
        i = dis(va)
        chk(_word(img, va) in (ULT_B_SPAN_STOCK, ULT_B_SPAN_WIDE),
            'ultima span +0x%X is stock or 534: %s %s'
            % (va, i.mnemonic, i.op_str))
    for hook, body, name in ((ULT_A_LEFT, _ult_left_body(), 'ultima left'),
                             (ULT_B_BASE, _ult_base_body(), 'ultima base')):
        w = _word(img, hook)
        if _branch_target(hook, w) is None:
            chk(w in (ULT_A_LEFT_STOCK, ULT_B_BASE_STOCK),
                '%s +0x%X still stock' % (name, hook))
            continue
        cave, why = _walk_cave(img, hook)
        chk(cave is not None, '%s cave walks back to the hook (%s)'
            % (name, why or 'ok'))
        if cave:
            logical = [_word(img, va) for va in cave
                       if _branch_target(va, _word(img, va)) is None]
            chk(logical == body, '%s cave body matches (%s)'
                % (name, ' '.join('%08X' % x for x in logical)))

    chk(_word(img, KOTR_X_START) in
        (KOTR_X_START_STOCK, KOTR_X_START_WIDE),
        'KOTR initial x is stock -256 or wide -512')
    chk(_word(img, KOTR_ROW2_HEIGHT) in
        (KOTR_ROW2_HEIGHT_STOCK, KOTR_ROW2_HEIGHT_WIDE),
        'KOTR second-row height is stock 76 or wide 224')
    hook_word = _word(img, KOTR_EXTRA_HOOK)
    if _branch_target(KOTR_EXTRA_HOOK, hook_word) is None:
        chk(hook_word == KOTR_EXTRA_HOOK_STOCK,
            'KOTR extra-column hook is stock')
    else:
        cave, why = _walk_cave(img, KOTR_EXTRA_HOOK,
                               limit=KOTR_CAVE_WALK_LIMIT)
        chk(cave is not None, 'KOTR extra-column cave returns (%s)'
            % (why or 'ok'))
        if cave:
            logical_addrs = [va for va in cave
                             if _branch_target(va, _word(img, va)) is None]
            expected = (_kotr_extra_body(
                        logical_addrs[:KOTR_EXTRA_BODY_WORDS])
                        + [KOTR_EXTRA_HOOK_STOCK])
            logical = [_word(img, va) for va in logical_addrs]
            chk(logical == expected,
                'KOTR cave contains the relocated two-iteration column loop')
    log('  %d check(s) failed' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('main')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--group', default='all',
                    choices=('all', 'ultima', 'kotr'))
    a = ap.parse_args()
    rc = 0
    if a.apply or a.revert:
        fn = {'all': apply_all, 'ultima': apply_ultima,
              'kotr': apply_kotr}[a.group]
        rc |= fn(a.main, revert=a.revert)
    if a.verify or not (a.apply or a.revert):
        rc |= _verify(a.main)
    raise SystemExit(rc)
