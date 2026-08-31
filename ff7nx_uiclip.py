#!/usr/bin/env python3
r"""
ff7nx_uiclip.py -- the 2D viewport rect never got WS_SCALE.  FINDINGS-103.

    python3 ff7nx_uiclip.py <exefs/main | sdout>            --verify
    python3 ff7nx_uiclip.py <exefs/main | sdout> --show
    python3 ff7nx_uiclip.py <exefs/main | sdout> --apply
    python3 ff7nx_uiclip.py <exefs/main | sdout> --revert


WHAT IS WRONG
=============
`ff7nx_ws` puts the widescreen scale in the VERTEX SHADER:

    tlmain_vv.glsl:   gl_Position.x *= WS_SCALE;     // 0.75

so 2D geometry lands at

    device px = 1.5 * game_x + 160          (measured, 12 captures)

Menu and dialogue windows set a per-window viewport before drawing
themselves.  `gfx_drv_setviewport` turns that into a device rect with a
hardcoded /640 and NO widescreen term -- executed, not argued, `ws_emu.run`
on the real driver words, target 1280x720, rect (80, 0, 120, 480):

    x1 = 160   x2 = 400        i.e. exactly 2 * game_x

So the window is CLIPPED on the unscaled 2x mapping while its parts are
DRAWN on the scaled 1.5x + 160 mapping.  The two agree only at game
x = 320 -- screen centre -- and diverge outward:

    left  border survives iff  1.5x + 160 >= 2x         ->  x     <= 320
    right border survives iff  1.5(x+w) + 160 <= 2(x+w) ->  x + w >= 320

A box straddling centre keeps both.  Wholly left of centre it loses its
RIGHT border; wholly right of centre it loses its LEFT border; an edge near
320 loses PART of one.  That model predicts 15 of 16 measured boxes,
including the partial, and three clip edges measured before the model
existed (396 vs 400 predicted, 758 vs 760, ~616 vs 620).

Confirmed on hardware twice: with WS_SCALE forced to 1.0 in tlmain_vv.glsl
-- nothing else changed, no rebuild -- every missing border came back.


WHY THIS FILE IS A CAVE AND NOT TWO WORDS
=========================================
The first version of this module REPLACED the viewport rect with the
full-screen rect, two words, no cave.  It fixed the borders on hardware and
it broke something else, exactly as its own docstring predicted it might:

    borders back, text bleeds out of a box  -> mechanism right, lever too
                                               blunt; the rect has to be
                                               SCALED, not replaced

That is what happened.  Dialogue text boxes began disappearing BEFORE their
text -- the text kept drawing over the field while the box shrank away --
because the per-window viewport is not only a clip that was wrong, it is
also the clip FF7 uses to hide a window's contents as the window opens and
closes.  Replacing it with the full screen deleted a clip that was doing a
real job.

So: keep the rect, and put the missing WS_SCALE on it.

    px' = 0.75 * px + tW/8          tW = the full rect's width

  0.75*px  is the shader's scale;  tW/8 = (1-0.75)*tW/2  re-centres it.
  Both halves are the shader's own transform, in integer arithmetic:
  (3*px)/4 + (tW >> 3).

Checked: a window at game x 80..200 is clipped to 280..460, and 1.5*80+160
= 280, 1.5*200+160 = 460.  The clip now lands exactly where the geometry
does, at both edges, at every x.


FULL-WIDTH RECTS, WITH NO LITERAL 640 ANYWHERE
================================================
The field, battle, menus and credits issue a full-screen viewport, and those
draws must not move -- scaling them would pillarbox the game.  They are
identified WITHOUT a magic number, by executing the real driver:

    rect passed to setviewport      viewport rect produced
    ------------------------------  ----------------------------
    full-screen (0,0,640,480)       (0,0)..(1280,720)  == the FULL rect
    window  x=80  w=120             160..400
    window  x=16  w=253             32..538

The exception is *definitionally* whether the viewport's two x edges equal
the full rect's x edges.  Comparing the complete packed x/y words is wrong:
battle effects use a full-width viewport that ends above the UI, and hardware
proved that Lower Junon also enters through a legitimate full-width,
partial-height field viewport.  A packed comparison classifies either as a
window and scales its x edges into 4:3.  Compare only the low 32-bit x halves
and leave both packed words untouched when x is already full width.  This is
global deliberately; no engine-mode inference is safe at this shared driver
hook, and y is never changed.

Build 211 tried a mode-aware split: x-only in battle and packed equality in
other modes.  It blanked Lower Junon deterministically on entry.  That failed
cave is recognized below only so an existing sdout migrates safely back to
the x-only form.

Builds 199 and 200 tried exactly that global promotion, first from unrelated
`w24` state and then by loading the engine mode pointer.  Both froze the world
image when entering a battle while battle music continued.  Those historical
caves are still recognized below so an existing sdout migrates safely, but
neither is emitted again.

That is why this survives a resolution change, a target-size change, and a
different WS_SCALE ratio at 4:3 -- there is no 640, no 1280 and no 160 in
the cave.  tW is read out of the full rect at run time.


THE CAVE
========
Hook at +0x10D9F48, return to +0x10D9F54, so the cave OWNS the three words
that set up the viewport call:

    +0x10D9F48  ldr x0, [x25]            <- hook; displaced, runs last
    +0x10D9F4C  ldr x1, [x26, #0x800]    \  the VIEWPORT rect  (dead once
    +0x10D9F50  ldr x2, [x26, #0x808]    /   hooked; left in place)
    +0x10D9F54  bl  +0x11320F0           <- return here

The displaced word is `ldr x0,[x25]`, chosen because it is the only one of
the three that can run LAST without being clobbered -- x0 is the call's
object argument and nothing in the cave touches it.  Hooking the x1 load
instead would put the unscaled rect back into x1 after the cave computed
the scaled one.  Nothing in the module branches into any of the three
words; that was checked by scanning all 4,540,824 words of .text, not
assumed.

Rects are PACKED: each 64-bit word is (y << 32) | x.  Only the low half is
touched, and `bfi` is what leaves the high half alone -- y is never scaled,
which is correct, because the shader only scales x.

     0  ldr  x1, [x26, #0x800]     viewport (x1,y1)
     1  ldr  x2, [x26, #0x808]     viewport (x2,y2)
     2  ldr  x3, [x26, #0x7f0]     full     (x1,y1)
     3  ldr  x4, [x26, #0x7f8]     full     (x2,y2)  -- low half is tW
     4  lsr  w5, w4, #3            tW/8
     5  add  w6, w1, w1, lsl #1    3*x1
     6  lsr  w6, w6, #2            /4
     7  add  w6, w6, w5            + tW/8
     8  add  w7, w2, w2, lsl #1    the same for x2
     9  lsr  w7, w7, #2
    10  add  w7, w7, w5
    11  add  x8, x1, #0            x8 = viewport (x1,y1)
    12  bfi  x8, x6, #0, #32       ...with x scaled, y untouched
    13  add  x9, x2, #0
    14  bfi  x9, x7, #0, #32
    15  cmp  w1, w3                \  Z set iff BOTH x edges are full-width
    16  ccmp w2, w4, #0, eq        /
    17  csel x1, x1, x8, eq        full-width -> untouched, else scaled
    18  csel x2, x2, x9, eq
    19  ldr  x0, [x25]             the displaced word
    20  b    +0x10D9F54

BRANCH-FREE by construction (`ccmp` is what makes the two-part comparison
fit without one), so `ff7nx_cave.emit_chained` can scatter it across padding
holes and `walk_physical` needs no special case: the ONE `b` the cave
contains is its own return.

x3-x9 are free at this site because a `bl` follows immediately -- anything
they held would be destroyed by the call regardless.  x25 and x26 are
callee-saved and are only read.

AND THIS IS A 16:9 PATCH, NOT A CLEANUP
=======================================
At 4:3 the geometry and the window clip are BOTH on the 2x mapping and
already agree.  Scaling the rect there would introduce the very error this
file removes.  `enabled()` following 16:9 is a hard requirement, not
caution.
"""
import argparse
import hashlib
import os
import struct
import sys
import shutil
import tempfile
from pathlib import Path

try:
    import capstone
except ImportError:                                          # pragma: no cover
    capstone = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import a64 as A                                                # noqa: E402
import nso_patcher                                             # noqa: E402
import ff7nx_cave                                              # noqa: E402

SDOUT_MAIN = os.path.join('atmosphere', 'contents', '0100A5B00BDC6000',
                          'exefs', 'main')

HOOK = 0x10D9F48                   # ldr x0, [x25]
HOOK_STOCK = 0xF9400320
RETURN_VA = 0x10D9F54              # the bl to gfx_drv_setviewport

VP_OFF = 0x800                     # viewport rect, (x1,y1) then (x2,y2)
FULL_OFF = 0x7F0                   # full rect, same shape
MODE_PTR_SLOT = 0x12CE1F8          # pointer to engine game mode; 3 = battle
MODE_PTR_PAGE = MODE_PTR_SLOT & ~0xFFF
MODE_PTR_OFF = MODE_PTR_SLOT & 0xFFF

# words that must be identical whether or not the patch is in.  Keyed on the
# calls and the base-register setup, never on anything the cave rewrites --
# HANDOFF-101 s2.4 rule 1: discovery must work in both states.  The two
# viewport loads are anchors too: the cave replicates them rather than
# editing them, so they must still be there, stock, in BOTH states.
ANCHORS = [
    (0x10D9EBC, 0xB0000FA9, 'adrp x9, 0x12CE000      engine game-mode slot page'),
    (0x10D9EC8, 0xF940FD29, 'ldr x9, [x9,#0x1F8]     pointer to engine mode'),
    (0x10D9ECC, 0xB9400129, 'ldr w9, [x9]            current engine mode'),
    (0x10D9ED0, 0x71000D3F, 'cmp w9,#3               battle-mode check'),
    (0x10D9EE4, 0xB0000FA8, 'adrp x8, 0x12CE000'),
    (0x10D9EE8, 0xF942A508, 'ldr x8, [x8,#0x548]      the render-state object'),
    (0x10D9EEC, 0xF943F901, 'ldr x1, [x8,#0x7F0]      FULL rect, top-left'),
    (0x10D9EF0, 0xF943FD02, 'ldr x2, [x8,#0x7F8]      FULL rect, bottom-right'),
    (0x10D9F3C, 0x94016069, 'bl +0x11320E0            the scissor'),
    (0x10D9F40, 0xB0000FBA, 'adrp x26, 0x12CE000'),
    (0x10D9F44, 0xF942A75A, 'ldr x26, [x26,#0x548]'),
    (0x10D9F4C, 0xF9440341, 'ldr x1, [x26,#0x800]     VIEWPORT rect, top-left'),
    (0x10D9F50, 0xF9440742, 'ldr x2, [x26,#0x808]     VIEWPORT, bottom-right'),
    (0x10D9F54, 0x94016067, 'bl +0x11320F0            the viewport'),
]


# ---------------------------------------------------------------------------
# the cave
# ---------------------------------------------------------------------------
def legacy_body_words():
    """The build-198 packed-rect cave, retained for migration only."""
    return [
        A.ldr64(1, 26, VP_OFF),                  #  0
        A.ldr64(2, 26, VP_OFF + 8),              #  1
        A.ldr64(3, 26, FULL_OFF),                #  2
        A.ldr64(4, 26, FULL_OFF + 8),            #  3
        A.lsr(5, 4, 3),                          #  4  tW/8
        A.add_reg_lsl(6, 1, 1, 1),               #  5  3*x1
        A.lsr(6, 6, 2),                          #  6  /4
        A.add_reg(6, 6, 5),                      #  7  + tW/8
        A.add_reg_lsl(7, 2, 2, 1),               #  8
        A.lsr(7, 7, 2),                          #  9
        A.add_reg(7, 7, 5),                      # 10
        A.add_imm64(8, 1, 0),                    # 11
        A.bfi64(8, 6, 0, 32),                    # 12
        A.add_imm64(9, 2, 0),                    # 13
        A.bfi64(9, 7, 0, 32),                    # 14
        A.cmp_reg64(1, 3),                       # 15
        A.ccmp_reg64(2, 4, 0, A.EQ),             # 16
        A.csel64(1, 1, 8, A.EQ),                 # 17
        A.csel64(2, 2, 9, A.EQ),                 # 18
    ]


def bad_w24_body_words():
    """The build-199 cave, retained only for byte-exact migration."""
    return legacy_body_words()[:15] + [
        # Compare only x.  A battle viewport is already full-width but is
        # deliberately only 332/480 high; comparing the packed x/y words made
        # it look like an ordinary window and re-clipped widened flashes to
        # the central 4:3 area.
        A.cmp_reg(1, 3),                          # 15  x1 == full.x1
        A.ccmp_reg32(2, 4, 0, A.EQ),              # 16  and x2 == full.x2
        A.csel64(1, 1, 8, A.EQ),                  # 17  full-x or scaled-x
        A.csel64(2, 2, 9, A.EQ),                  # 18
        A.ccmp_imm32(24, 3, 0, A.EQ),             # 19  bad guessed mode source
        A.csel64(1, 3, 1, A.EQ),                  # 20  full rect top-left
        A.csel64(2, 4, 2, A.EQ),                  # 21  full rect bottom-right
    ]


def bad_mode_body_words_at(addrs):
    """The build-200 mode-pointer cave, retained for byte-exact migration."""
    if len(addrs) < 25:
        raise ValueError('historical uiclip body needs 25 addresses')
    return legacy_body_words()[:15] + [
        A.cmp_reg(1, 3),                          # 15  x1 == full.x1
        A.ccmp_reg32(2, 4, 0, A.EQ),              # 16  and x2 == full.x2
        A.csel64(1, 1, 8, A.EQ),                  # 17  full-x or scaled-x
        A.csel64(2, 2, 9, A.EQ),                  # 18
        # Use the renderer's real game-mode source.  The ADRP must be
        # encoded from the physical address of this logical cave word.
        A.adrp(10, addrs[19], MODE_PTR_PAGE),      # 19
        A.ldr64(10, 10, MODE_PTR_OFF),             # 20  pointer to game mode
        A.ldr(10, 10),                             # 21  mode value
        A.ccmp_imm32(10, 3, 0, A.EQ),              # 22  full-x && battle
        A.csel64(1, 3, 1, A.EQ),                  # 23  full output TL
        A.csel64(2, 4, 2, A.EQ),                  # 24  full output BR
    ]


def xonly_body_words():
    """The current position-independent x-only window-clip body."""
    return legacy_body_words()[:15] + [
        A.cmp_reg(1, 3),                          # 15  x1 == full.x1
        A.ccmp_reg32(2, 4, 0, A.EQ),              # 16  and x2 == full.x2
        A.csel64(1, 1, 8, A.EQ),                  # 17  full-x or scaled-x
        A.csel64(2, 2, 9, A.EQ),                  # 18
    ]


def bad_modeaware_body_words_at(addrs):
    """The failed build-211 mode-aware body, retained for exact migration.

    Hardware proved that a legitimate Lower Junon field viewport can be
    full-width and partial-height outside battle.  Applying packed equality
    there blanked field entry deterministically, so this form must never be
    emitted again.
    """
    if len(addrs) < 29:
        raise ValueError('mode-aware uiclip body needs 29 addresses')
    return legacy_body_words()[:15] + [
        # Stable world/field/menu result: equality includes both packed y
        # halves.  A full-x partial-height rect is therefore recentred.
        A.cmp_reg64(1, 3),                        # 15
        A.ccmp_reg64(2, 4, 0, A.EQ),              # 16
        A.csel64(11, 1, 8, A.EQ),                 # 17  packed result TL
        A.csel64(12, 2, 9, A.EQ),                 # 18  packed result BR
        # Battle result: equality deliberately ignores y, preserving the
        # full-width partial-height viewport used by flashes and overlays.
        A.cmp_reg(1, 3),                          # 19
        A.ccmp_reg32(2, 4, 0, A.EQ),              # 20
        A.csel64(13, 1, 8, A.EQ),                 # 21  x-only result TL
        A.csel64(14, 2, 9, A.EQ),                 # 22  x-only result BR
        A.adrp(10, addrs[23], MODE_PTR_PAGE),      # 23
        A.ldr64(10, 10, MODE_PTR_OFF),             # 24  mode pointer
        A.ldr(10, 10),                             # 25  current mode
        A.cmp_imm(10, 3),                          # 26  battle == 3
        A.csel64(1, 13, 11, A.EQ),                # 27
        A.csel64(2, 14, 12, A.EQ),                # 28
    ]


def body_words_at(_addrs):
    """Current x-only body; full-width rects retain both original y bounds."""
    return xonly_body_words()


def body_words(base=0x200000):
    """A contiguous instance used by the emulator and mutation tests."""
    return body_words_at([base + 4 * i for i in range(19)])


N_BODY = 19
N_WORDS = N_BODY + 2               # + the displaced word + the return branch

# what each word must disassemble to.  Compared against capstone in verify()
# so an encoder change in a64.py cannot silently alter the cave.
EXPECT_ASM = [
    'ldr x1, [x26, #0x800]',   'ldr x2, [x26, #0x808]',
    'ldr x3, [x26, #0x7f0]',   'ldr x4, [x26, #0x7f8]',
    'lsr w5, w4, #3',
    'add w6, w1, w1, lsl #1',  'lsr w6, w6, #2',   'add w6, w6, w5',
    'add w7, w2, w2, lsl #1',  'lsr w7, w7, #2',   'add w7, w7, w5',
    'add x8, x1, #0',          'bfxil x8, x6, #0, #0x20',
    'add x9, x2, #0',          'bfxil x9, x7, #0, #0x20',
    'cmp w1, w3',              'ccmp w2, w4, #0, eq',
    'csel x1, x1, x8, eq',     'csel x2, x2, x9, eq',
    'ldr x0, [x25]',
]


# ---------------------------------------------------------------------------
# image helpers
# ---------------------------------------------------------------------------
def resolve_main(path):
    if os.path.isdir(path):
        cand = os.path.join(path, SDOUT_MAIN)
        if not os.path.isfile(cand):
            raise SystemExit('uiclip: no %s under %s' % (SDOUT_MAIN, path))
        return cand
    return path


def _text_of(nso):
    for seg in nso.segments:
        if seg.name == '.text':
            return seg.data
    raise SystemExit('uiclip: no .text segment')


def _text(path):
    return _text_of(nso_patcher.read_nso(Path(path)))


def w32(t, va):
    return struct.unpack_from('<I', t, va)[0]


def _fmt(word):
    return struct.pack('<I', word).hex(' ')


def _b_target(word, va):
    if (word & 0xFC000000) != 0x14000000:
        return None
    imm = word & 0x3FFFFFF
    if imm & 0x2000000:
        imm -= 0x4000000
    return va + imm * 4


def _disasm(word, va=0):
    if capstone is None:
        return None
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    out = list(md.disasm(struct.pack('<I', word), va))
    return (out[0].mnemonic + ' ' + out[0].op_str) if out else None


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------
def cave_state(t):
    """'stock', 'patched', or 'unknown'."""
    got = w32(t, HOOK)
    if got == HOOK_STOCK:
        return 'stock'
    if _b_target(got, HOOK) is not None:
        return 'patched'
    return 'unknown'


def installed(t):
    return cave_state(t) == 'patched'


def state(t):
    """The name build.py and ff7nx_status use."""
    return {'stock': 'stock', 'patched': 'applied'}.get(cave_state(t), 'unknown')


def check_anchors(t, log=lambda *_: None):
    bad = []
    for va, want, what in ANCHORS:
        got = w32(t, va)
        if got != want:
            bad.append('+%#09x is %08X, expected %08X -- %s'
                       % (va, got, want, what))
    if cave_state(t) == 'unknown':
        bad.append('hook +%#09x is %08X -- neither the stock word nor a '
                   'branch; refusing' % (HOOK, w32(t, HOOK)))
    for b in bad:
        log('  ! ' + b)
    return bad


def walk_physical(t):
    """
    Every ADDRESS the cave occupies, chaining branches included.

    revert needs the footprint, not the logic: zeroing only the logical words
    leaves link branches behind as live code in someone else's padding, which
    is what makes the next module's allocator skip a usable hole and the next
    apply->revert fail its byte-identity check.

    The rule -- a `b` that is not to RETURN_VA is a run-to-run link, never
    logic -- is exact here because the cave is branch-free by construction.
    """
    entry = _b_target(w32(t, HOOK), HOOK)
    if entry is None:
        return []
    va, out = entry, []
    while len(out) < 128 and va not in out:
        x = w32(t, va)
        out.append(va)
        b = _b_target(x, va)
        if b == RETURN_VA:
            return out
        if b is not None and b != RETURN_VA:
            va = b
            continue
        va += 4
    return out


def walk(t):
    """The cave's LOGICAL word list, chaining branches removed."""
    entry = _b_target(w32(t, HOOK), HOOK)
    if entry is None:
        return None
    va, out = entry, []
    seen = set()
    while len(out) < 128 and va not in seen:
        seen.add(va)
        x = w32(t, va)
        b = _b_target(x, va)
        if b is not None and b != RETURN_VA:
            va = b
            continue
        out.append((va, x))
        if b == RETURN_VA:
            return out
        va += 4
    return out


def _matches_fixed_body(wk, body):
    """True for a position-independent historical body and exact tail."""
    if not wk or len(wk) != len(body) + 2:
        return False
    have = [x for _, x in wk]
    return (have[:len(body)] == list(body) and
            have[len(body)] == HOOK_STOCK and
            _b_target(have[-1], wk[-1][0]) == RETURN_VA)


def _matches_current_cave(wk):
    """True for the current x-only body and exact tail."""
    if not wk or len(wk) != N_WORDS:
        return False
    addrs = [va for va, _ in wk[:N_BODY]]
    have = [x for _, x in wk]
    return (have[:N_BODY] == body_words_at(addrs) and
            have[N_BODY] == HOOK_STOCK and
            _b_target(have[-1], wk[-1][0]) == RETURN_VA)


def _matches_bad_modeaware_cave(wk):
    """True for the failed build-211 position-dependent cave."""
    old_n = 29
    if not wk or len(wk) != old_n + 2:
        return False
    addrs = [va for va, _ in wk[:old_n]]
    have = [x for _, x in wk]
    return (have[:old_n] == bad_modeaware_body_words_at(addrs) and
            have[old_n] == HOOK_STOCK and
            _b_target(have[-1], wk[-1][0]) == RETURN_VA)


def _matches_bad_mode_cave(wk):
    """True for the build-200 position-dependent promotion cave."""
    old_n = 25
    if not wk or len(wk) != old_n + 2:
        return False
    addrs = [va for va, _ in wk[:old_n]]
    have = [x for _, x in wk]
    return (have[:old_n] == bad_mode_body_words_at(addrs) and
            have[old_n] == HOOK_STOCK and
            _b_target(have[-1], wk[-1][0]) == RETURN_VA)


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------
def build_patches(img, starts, log=print):
    """{va: word} for the cave and the hook, or None if it cannot be done."""
    img = bytearray(img)
    displaced = w32(img, HOOK)
    if displaced != HOOK_STOCK:
        log('  ! hook +%#09x is %08X, not `ldr x0,[x25]`' % (HOOK, displaced))
        return None
    pool = ff7nx_cave.HolePool(img, starts=starts)

    def builder(_entry, addr):
        body = body_words_at([addr(i) for i in range(N_BODY)])
        return body + [HOOK_STOCK, A.b(addr(N_BODY + 1), RETURN_VA)]

    entry, words = ff7nx_cave.emit_laid_out(pool, builder)
    words[HOOK] = A.b(HOOK, entry)
    log('  2D viewport scale: %d words in padding, entry +%#x'
        % (N_WORDS, entry))
    log('    window rects  x -> (3x)/4 + tW/8   (the shader\'s own 0.75, recentred)')
    log('    full-width clips retain both original y bounds (no mode inference)')
    return words


def revert_patches(t, log=print):
    """{va: word} that puts the hook back and returns the padding."""
    if not installed(t):
        return {}
    phys = walk_physical(t)
    if not phys:
        log('  ! the hook is a branch but the cave cannot be walked')
        return None
    if HOOK_STOCK not in [w32(t, va) for va in phys]:
        log('  ! the cave does not contain the displaced word; refusing to '
            'guess what to restore')
        return None
    out = {HOOK: HOOK_STOCK}
    for va in phys:
        out[va] = 0
    log('  2D viewport scale removed (%d word(s) of padding returned)'
        % len(phys))
    return out


# ---------------------------------------------------------------------------
# verification -- the cave is EXECUTED, not read
# ---------------------------------------------------------------------------
PX = lambda g: int(1.5 * g + 160)       # noqa: E731  the measured 16:9 mapping

# (name, rect handed to the cave, rect wanted back).  Device px on a
# 1280x720 field buffer; the window cases are real boxes out of FINDINGS-103.
CASES = [
    ('full-screen  field/battle/credits', (0, 0, 1280, 720), (0, 0, 1280, 720)),
    ('window game  80..200   "..." box', (160, 0, 400, 720),
     (PX(80), 0, PX(200), 720)),
    ('window game  16..269   "Yes!! Welcome!!"', (32, 0, 538, 720),
     (PX(16), 0, PX(269), 720)),
    ('window game 380..460   "!" box', (760, 0, 920, 720),
     (PX(380), 0, PX(460), 720)),
    ('window game 120..310   dress box', (240, 0, 620, 720),
     (PX(120), 0, PX(310), 720)),
    ('window game   0..272   battle NAME', (0, 0, 544, 720),
     (PX(0), 0, PX(272), 720)),
    ('window game 392..458', (784, 0, 916, 720), (PX(392), 0, PX(458), 720)),
]

OBJ = 0x40001000


def _run_cave(words, vp, full=(0, 0, 1280, 720), game_mode=3,
              base=0x200000):
    """Execute the cave's logical words and return the rect it produced."""
    import arm64emu
    mem = arm64emu.Mem()
    mem.setu(OBJ + FULL_OFF, (full[1] << 32) | full[0], 8)
    mem.setu(OBJ + FULL_OFF + 8, (full[3] << 32) | full[2], 8)
    mem.setu(OBJ + VP_OFF, (vp[1] << 32) | vp[0], 8)
    mem.setu(OBJ + VP_OFF + 8, (vp[3] << 32) | vp[2], 8)
    mode_addr = 0x40002000
    mem.setu(MODE_PTR_SLOT, mode_addr, 8)
    mem.setu(mode_addr, game_mode, 4)
    cpu = arm64emu.Cpu(mem)
    cpu.set(26, OBJ)
    cpu.set(25, OBJ + 0x1000)
    cpu.sp = 0x50000000
    cpu.run(base, list(words), max_steps=400)
    a, b = cpu.get(1), cpu.get(2)
    return (a & 0xFFFFFFFF, (a >> 32) & 0xFFFFFFFF,
            b & 0xFFFFFFFF, (b >> 32) & 0xFFFFFFFF)


def verify(t=None, log=print, verbose=True, words=None):
    """
    Returns a list of failures. `words` lets the caller check a PLANNED cave
    before it is written; with none, the module's own body is checked.
    """
    fails = []
    checks = 0

    def ok(cond, what):
        nonlocal checks
        checks += 1
        if not cond:
            fails.append(what)

    body = list(body_words()) if words is None else list(words)

    # 1. every word disassembles to exactly what the docstring claims
    if capstone is not None:
        seq = body + [HOOK_STOCK]
        ok(len(EXPECT_ASM) == len(seq),
           'expected-asm table is %d rows, cave is %d words'
           % (len(EXPECT_ASM), len(seq)))
        for i, (word, want) in enumerate(zip(seq, EXPECT_ASM)):
            got = _disasm(word, 0x200000 + 4 * i)
            ok(got is not None and
               got.replace(' ', '') == want.replace(' ', ''),
               'word %d is "%s", expected "%s"' % (i, got, want))

    # 2. branch-free -- what walk_physical's rule depends on
    for i, word in enumerate(body):
        ok(_b_target(word, 0) is None, 'body word %d is a branch' % i)
        ok((word & 0xFF000010) != 0x54000000, 'body word %d is a b.cond' % i)
        ok((word & 0x7E000000) not in (0x34000000, 0x36000000),
           'body word %d is a cbz/tbz' % i)

    # 3. position-independent: this shared driver hook must not infer mode.
    pc_relative = []
    for i, word in enumerate(body):
        if (word & 0x1F000000) == 0x10000000:
            pc_relative.append(i)
    ok(pc_relative == [],
       'PC-relative words are %s, expected none' % pc_relative)

    # 4. no constant that ties this to one resolution or one WS_SCALE
    for i, word in enumerate(body):
        ok((word & 0xFF800000) not in (0x52800000, 0x52A00000),
           'body word %d is a movz -- this cave must contain no literals' % i)

    # 5. it does the right thing, EXECUTED
    for name, vp, want in CASES:
        got = _run_cave(body, vp)
        ok(got == want, '%s: cave gives %s, wanted %s' % (name, got, want))

    # Full-x partial-height clips are legitimate in battle and field paths.
    # Hardware proved Lower Junon blanks if packed equality narrows this rect.
    # Mode must therefore have no effect and y must remain byte-for-byte.
    partial = (0, 0, 1280, 498)
    got = _run_cave(body, partial, game_mode=2)
    ok(got == partial,
       'field mode narrowed a full-x partial-height clip: %s' % (got,))
    got = _run_cave(body, partial, game_mode=3)
    ok(got == partial,
       'battle mode narrowed a full-x partial-height clip: %s' % (got,))

    # 6. y is never touched, at two resolutions
    for full in ((0, 0, 1280, 720), (0, 0, 1920, 1080)):
        got = _run_cave(body, (100, 37, 400, 611), full)
        ok((got[1], got[3]) == (37, 611),
           'y changed at full=%s: %s' % (full, got))

    # 7. resolution independence: the recentring term follows tW, not 1280
    got = _run_cave(body, (240, 0, 600, 1080), (0, 0, 1920, 1080))
    ok(got[0] == int(0.75 * 240 + 240) and got[2] == int(0.75 * 600 + 240),
       'at 1920 wide the recentring is not tW/8: %s' % (got,))

    # 8. and the image, if we were given one
    if t is not None:
        bad = check_anchors(t)
        ok(not bad, 'anchors: ' + '; '.join(bad))
        st = cave_state(t)
        ok(st in ('stock', 'patched'), 'image is in an unknown state')
        if st == 'patched':
            wk = walk(t)
            ok(wk is not None and len(wk) == N_WORDS,
               'the installed cave walks to %s words, not %d'
               % (len(wk) if wk else None, N_WORDS))
            if wk and len(wk) == N_WORDS:
                have = [x for _, x in wk]
                addrs = [va for va, _ in wk[:N_BODY]]
                ok(have[:N_BODY] == body_words_at(addrs),
                   'the installed cave body differs from this module\'s')
                ok(have[N_BODY] == HOOK_STOCK,
                   'the installed cave does not carry the displaced word')
                ok(_b_target(have[N_BODY + 1], wk[N_BODY + 1][0]) == RETURN_VA,
                   'the installed cave does not return to +%#x' % RETURN_VA)
                # Behaviour is executed on the same body encoded contiguously
                # above.  The installed body is byte-compared against a
                # rebuild using every one of its real, scattered PCs, which
                # separately proves the ADRP reaches the correct page.

    if verbose:
        log('  %d check(s), %d failure(s)' % (checks, len(fails)))
        for f in fails:
            log('  ! ' + f)
    return fails


def _mutants(log=lambda *_: None):
    """A patch whose tests do not bite is a patch with no tests."""
    base = body_words()
    slipped = 0
    total = 0
    muts = [
        ('drop the recentring term', 7, A.add_reg(6, 6, 31)),
        ('scale by 1/2 not 3/4', 6, A.lsr(6, 6, 1)),
        ('recentre by tW/4', 4, A.lsr(5, 4, 2)),
        ('scale y as well as x', 12, A.bfi64(8, 6, 0, 64)),
        ('invert the full-x test', 17, A.csel64(1, 8, 1, A.EQ)),
        ('compare only half the rect', 16, A.ccmp_reg64(2, 4, 4, A.EQ)),
        ('scale x2 from x1', 8, A.add_reg_lsl(7, 1, 1, 1)),
        ('forget to copy y into x8', 11, A.add_imm64(8, 31, 0)),
        ('compare packed y as well as x', 16,
         A.ccmp_reg64(2, 4, 0, A.EQ)),
    ]
    for what, idx, word in muts:
        total += 1
        m = list(base)
        m[idx] = word
        try:
            caught = bool(verify(None, verbose=False, words=m))
        except Exception:                                      # noqa: BLE001
            caught = True          # a mutant that will not even run is caught
        if not caught:
            slipped += 1
            log('  ! mutant slipped through: %s' % what)
    return slipped, total


# ---------------------------------------------------------------------------
# build.py / GUI entry points
# ---------------------------------------------------------------------------
UICLIP_ENV = 'SEVENTH_NX_UI_CLIP'


def enabled(env=None) -> bool:
    """
    ON with 16:9, OFF at 4:3, overridable for an A/B.

    NO CHECKBOX, deliberately -- same footing as `ff7nx_modelcull`,
    `ff7nx_battlewide` and `ff7nx_swirlscale`. Under 16:9 there is no
    configuration in which you want a window clipped to a rect its own border
    is drawn outside of.

    The 4:3 half of the gate is a HARD requirement, not caution. At 4:3 the
    geometry and the window clip are both on the unscaled 2x mapping and
    therefore agree; scaling the rect there would CREATE the mismatch this
    file exists to remove.

    `SEVENTH_NX_UI_CLIP` exists so the A/B can be run without touching the
    16:9 dropdown; the GUI deliberately does NOT write it (FINDINGS-91 s6 --
    the GUI writes every env var it knows about on every save, so a module
    default is not a gate, but an unwritten variable is).
    """
    raw = env if env is not None else os.environ.get(UICLIP_ENV)
    if raw is not None:
        return str(raw).strip().lower() not in ('', '0', 'off', 'no', 'false')
    try:
        import ff7nx_ws
        return ff7nx_ws.enabled()
    except Exception:                                          # noqa: BLE001
        return False


def show(main, log=print):
    main = resolve_main(main)
    t = _text(main)
    log('  %s' % main)
    log('    +%#09X  %s  hook  %s' % (HOOK, _fmt(w32(t, HOOK)), cave_state(t)))
    if installed(t):
        phys = walk_physical(t)
        log('    cave: %d word(s) of padding at +%#x' % (len(phys), phys[0]))
        wk = walk(t) or []
        for i, (va, x) in enumerate(wk):
            log('      %2d  +%#09x  %08X  %s' % (i, va, x, _disasm(x, va) or ''))
        log('    window x-clips scale; full-width clips retain their y bounds:')
        body = body_words()
        if _matches_current_cave(wk):
            for name, vp, want in CASES:
                got = _run_cave(body, vp)
                log('      %-38s %s -> %s  %s'
                    % (name, vp, got, 'ok' if got == want else 'WRONG'))
    else:
        log('    not installed -- window clips stay on the unscaled 2x mapping')
    bad = check_anchors(t, log)
    log('    anchors: %s' % ('OK' if not bad else '%d FAILED' % len(bad)))
    return 1 if bad else 0


def apply(main, revert=False, log=print) -> int:
    """
    Returns 0 on success (including "already in that state"), 1 if it refused.
    Refusing is not fatal to the build: the module is left as it was.
    """
    import nxmap
    try:
        main = Path(resolve_main(str(main)))
        m = nxmap.Main(str(main))
        t = m.text
    except SystemExit as exc:
        log('  ! 2D viewport scale: %s' % exc)
        return 1

    if check_anchors(t, log):
        log('  refusing to write.')
        return 1

    if revert:
        words = revert_patches(t, log)
        if words is None:
            return 1
    else:
        if installed(t):
            wk = walk(t)
            if _matches_current_cave(wk):
                log('  2D viewport scale: already installed')
                return 0
            known_old = (_matches_fixed_body(wk, legacy_body_words()) or
                         _matches_fixed_body(wk, bad_w24_body_words()) or
                         _matches_bad_modeaware_cave(wk) or
                         _matches_bad_mode_cave(wk))
            if not known_old:
                log('  ! installed 2D viewport cave is neither this version '
                    'nor a known historical version; refusing to replace it')
                return 1
            # Reclaim the known old cave in a virtual image, allocate the new
            # one from that exact state, then express the migration as one
            # checked patch transaction.  This avoids an on-disk interval in
            # which the clip fix is absent and preserves idempotence.
            log('  2D viewport scale: migrating historical viewport cave')
            old = revert_patches(t, log)
            if old is None:
                return 1
            virtual = bytearray(m.img)
            for va, word in old.items():
                struct.pack_into('<I', virtual, va, word)
            new = build_patches(virtual, set(m.arm_starts), log)
            if new is None:
                return 1
            words = dict(old)
            words.update(new)
        else:
            fails = verify(t, log=log, verbose=False)
            if fails:
                for f in fails:
                    log('  ! 2D viewport scale: ' + f)
                log('  refusing to write.')
                return 1
            words = build_patches(m.img, set(m.arm_starts), log)
            if words is None:
                return 1
    if not words:
        log('  2D viewport scale: nothing to do')
        return 0

    patches = [{'name': 'ff7nx_uiclip +%#09X' % va,
                'va': hex(va),
                'expect': _fmt(w32(t, va)),
                'set': _fmt(word)}
               for va, word in sorted(words.items())]
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(nso, {'name': 'ff7nx_uiclip',
                                             'patches': patches}):
        log('    ' + line)
    blob = nso_patcher.rebuild(nso)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.uiclip-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(blob)
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  wrote %s  (%s)' % (main, hashlib.md5(blob).hexdigest()))

    t2 = _text(str(main))
    log('  read back from the written module:')
    log('    hook +%#09X is %s' % (HOOK, cave_state(t2)))
    if revert:
        if cave_state(t2) != 'stock':
            log('  ! revert did not restore the hook. DO NOT BOOT THIS.')
            return 1
        return 0
    fails = verify(t2, log=log, verbose=False)
    if fails:
        for f in fails:
            log('  ! ' + f)
        log('  ! the WRITTEN cave does not verify. DO NOT BOOT THIS.')
        return 1
    log('    the written cave executes correctly on all %d rect case(s)'
        % len(CASES))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('target', help='exefs/main, or an sdout directory')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--show', action='store_true')
    g.add_argument('--apply', action='store_true')
    g.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    path = resolve_main(a.target)

    if a.show:
        sys.exit(show(path))
    if a.apply or a.revert:
        sys.exit(apply(path, revert=a.revert))

    t = _text(path)
    print('  %s' % path)
    print('  state: %s' % state(t))
    fails = verify(t)
    slipped, total = _mutants(log=print)
    print('  mutation: %d of %d mutant(s) slipped through' % (slipped, total))
    if fails or slipped:
        sys.exit(1)
    print('  OK -- safe to --apply')


if __name__ == '__main__':
    main()
