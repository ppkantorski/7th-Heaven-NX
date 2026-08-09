#!/usr/bin/env python3
r"""
ff7nx_credits.py -- the credits fade quad is 4:3 wide.  HANDOFF-104.

    python3 ff7nx_credits.py <exefs/main | sdout>            --verify
    python3 ff7nx_credits.py <exefs/main | sdout> --show
    python3 ff7nx_credits.py <exefs/main | sdout> --apply
    python3 ff7nx_credits.py <exefs/main | sdout> --revert

EIGHT WORDS at four sites.  No cave, no relocation, no branch.
apply -> revert is byte-identical over the whole image.


WHAT IS WRONG
=============
The intro / prelude is FF7's CREDITS mode -- a still texture, music, and 2D
text.  Not an FMV, which is why `ff7nx_moviebars` never covered it.

`credits_submit_draw_fade_quad` (x86 0x7AA89B) draws ONE black quad whose
alpha is the fade counter.  Its rect comes from four guest globals:

    [0xF4F5A0]  left x        [0xF4F5A4]  top y
    [0xF4F5A8]  right x       [0xF4F5AC]  bottom y

left/right are 0 and 640 -- the 4:3 core.  At 16:9 the visible span is
-106.7 .. 746.7, so the quad covers only the middle and the side margins
are never repainted.  FF7 stages credit text off-screen and slides it in;
at 4:3 that staging area is off-screen, at 16:9 it is on screen, and since
nothing repaints the margins what lands there STAYS.  That is the smear.

FFNx fixes exactly this, by name, in src/ff7/widescreen.cpp:299:

    // Credits fix
    patch_code_dword(credits_submit_draw_fade_quad_7AA89B + 0x99,  &wide_viewport_x);
    patch_code_dword(credits_submit_draw_fade_quad_7AA89B + 0xE6,  &wide_viewport_x);
    patch_code_dword(credits_submit_draw_fade_quad_7AA89B + 0x133, &viewport_width_plus_x_widescreen_fix);
    patch_code_dword(credits_submit_draw_fade_quad_7AA89B + 0x180, &viewport_width_plus_x_widescreen_fix);

Left and right only.  y is untouched, so the quad stays FULL HEIGHT -- which
matches the reporter exactly: "the scene takes up exactly 4:3 (no top or
bottom bars, all the way from top of screen to bottom)".

We use -107 and 747, not FFNx's -107 and 750.  747 = -107 + 854, which is
the identical right edge `ff7nx_letterbox` already ships on its own
full-frame fade quad (x -107, w 854) and which is confirmed on hardware.
Matching a proven value in this tree beats matching FFNx's rounding.


THE SITES
=========
Located with `ff7nx_guestref.scan` over the ARM body +0x10A1B00..+0x10A23C0
-- constant propagation to a fixpoint, which is what that tool exists for.
Two loads each for left and right, one per vertex, exactly as the x86's four
vertices (L,T) (L,B) (R,T) (R,B) predict:

    +0x10A1D6C   guest 0xF4F5A0   left  x, v0   ->  -107
    +0x10A1F08   guest 0xF4F5A0   left  x, v1   ->  -107
    +0x10A20A0   guest 0xF4F5A8   right x, v2   ->   747
    +0x10A2230   guest 0xF4F5A8   right x, v3   ->   747

    +0x10A1DD0, +0x10A2100   guest 0xF4F5A4   top y     LEFT ALONE
    +0x10A1F6C, +0x10A2290   guest 0xF4F5AC   bottom y  LEFT ALONE

All four are the identical four-word idiom, byte-for-byte:

    +0    BD400000   ldr   s0, [x0]             the guest word
    +4    0F20A400   sshll v0.2d, v0.2s, #0     sign-extend int32 -> int64
    +8    B9406308   ldr   w8, [x24, #0x60]     unrelated, interleaved
    +C    5E61D800   scvtf d0, d0               int64 -> double

so the value reaches `scvtf` as a SIGN-EXTENDED 64-BIT INTEGER in d0.


THE PATCH, AND WHY IT NEEDS NO LIVENESS ANALYSIS
================================================
Two words are free at each site, and two words are exactly enough:

    +0    movn x8, #106     ; x8 = -107      (or  movz x8, #747)
    +4    fmov d0, x8

`x8` is clobbered -- and it is provably dead there, LOCALLY.  The very next
instruction, `+8 ldr w8, [x24, #0x60]`, writes w8, and a 32-bit destination
zeroes bits 63:32, so the incoming x8 is entirely dead by +8.  Nothing
between +0 and +8 reads it except the two words we are writing.  No global
analysis, no cave, no scratch hunting: the site kills the register for us.

THE TRAP, AND IT IS SILENT
==========================
It must be the 64-BIT movn/movz.  `movn w8, #106` gives
x8 = 0x00000000FFFFFF95, which `scvtf d0, d0` reads as +4294967189 and the
quad flies off screen.  Both forms assemble, and capstone prints them
almost identically:

    92800D48   mov x8, #-0x6b        <- correct
    12800D48   mov w8, #-0x6b        <- wrong, and reads the same

So `--verify` asserts the sf bit (31) on every emitted word rather than
comparing disassembly text.  Checking the printed string here would pass the
bug straight through, which is HANDOFF-101 s2.3's "never branch on an
AArch64 mnemonic" wearing different clothes.


WHAT THIS FIXES, AND WHAT IT MAY NOT
====================================
CERTAIN: the credits fade covering only the middle 4:3.

PROBABLE: the smear.  A full-width black quad repaints the margins on every
frame a fade is running, and FF7's credits are a near-continuous fade
sequence.  `ff7nx_scissor.py` named the cause: "the margins are also never
repainted between frames outside the 4:3 region, so what is drawn there
stays".  This makes something repaint them.

POSSIBLE REMAINDER: live text drawn AFTER the quad in a frame where the fade
alpha is low.  If text still bleeds once this lands, the second half is the
mode-gated pillarbox paint in HANDOFF-104 s5 -- but the smear will be gone,
so what remains will be readable instead of a mess.
"""
import argparse
import hashlib
import os
import struct
import sys

try:
    import capstone
except ImportError:                                          # pragma: no cover
    capstone = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nso_patcher                                            # noqa: E402

LEFT_X = -107
RIGHT_X = 747

STOCK = (0xBD400000, 0x0F20A400)        # ldr s0,[x0] ; sshll v0.2d,v0.2s,#0
TAIL = (0xB9406308, 0x5E61D800)         # ldr w8,[x24,#0x60] ; scvtf d0,d0


def movn_x(rd, imm16):
    return 0x92800000 | ((imm16 & 0xFFFF) << 5) | rd


def movz_x(rd, imm16):
    return 0xD2800000 | ((imm16 & 0xFFFF) << 5) | rd


def fmov_d_x(vd, rn):
    return 0x9E670000 | (rn << 5) | vd


def _pair(value):
    """(word0, word1) that leave `value` in d0 as a sign-extended int64."""
    if value < 0:
        w0 = movn_x(8, (-value) - 1)     # movn writes ~imm, so ~106 == -107
    else:
        w0 = movz_x(8, value)
    return w0, fmov_d_x(0, 8)


SITES = [
    ('credits fade quad  left  x, v0', 0x10A1D6C, LEFT_X),
    ('credits fade quad  left  x, v1', 0x10A1F08, LEFT_X),
    ('credits fade quad  right x, v2', 0x10A20A0, RIGHT_X),
    ('credits fade quad  right x, v3', 0x10A2230, RIGHT_X),
]

# ---------------------------------------------------------------------------
# LEG 2 -- clear the colour buffer on EVERY credits frame.
#
# The fade quad alone did not stop the ghosting on hardware, which says the
# margins are not being repainted at all, not merely painted 4:3 wide.
#
# gfx_drv_clear (+0x10D68D0) has TWO paths: w1 selects the depth clear, w0
# selects the COLOUR clear, and the colour path (+0x10D697C -> bl +0x1132150,
# colour in v0..v3) takes NO RECT -- it clears the whole target. So a colour
# clear would blacken the margins outright.
#
# The credits ask for it conditionally.  x86 0x7A7A33 +0x098:
#
#     mov eax, [0xF4F450] ; push eax ; push 1 ; push 0 ; push game_obj
#     call sub_66064A     -> driver_table[+0x14] = gfx_drv_clear(w0=eax, w1=1)
#
# [0xF4F450] is a flag the credits set to 1 only at 0x7AA464 and 0x7AA473 --
# transitions. Between them w0 is 0, only DEPTH is cleared, the backdrop
# redraw covers the 4:3 core, and the side margins keep whatever was last
# written there. That is the ghost.
#
# In ARM the flag is loaded straight into the argument register:
#
#     +0x10A08A0   B9400014   ldr w20, [x0]        <- the flag
#     +0x10A08A8   B90002B4   str w20, [x21]       <- pushed as arg 0
#
# so one word forces it. Same destination, same use, and the instruction we
# replace WRITES w20, so nothing is clobbered and no liveness question arises.
CLEAR_SITE = ('credits colour clear, every frame', 0x10A08A0,
              0xB9400014, 0x52800034)        # ldr w20,[x0] -> mov w20, #1
CLEAR_ANCHOR = (0x10A08A8, 0xB90002B4,
                'str w20, [x21]   the flag is arg 0 of the clear')


# The y sites. They must stay STOCK -- if one of these ever reads as patched
# the quad has been made taller and the intro will grow bars it never had.
Y_SITES = [
    ('top y, v0', 0x10A1DD0), ('top y, v2', 0x10A2100),
    ('bottom y, v1', 0x10A1F6C), ('bottom y, v3', 0x10A2290),
]


def _main_path(target):
    if os.path.isdir(target):
        p = os.path.join(target, 'atmosphere', 'contents',
                         '0100A5B00BDC6000', 'exefs', 'main')
        if not os.path.isfile(p):
            sys.exit('credits: no exefs/main under %s' % target)
        return p
    return target


def _text(nso):
    for seg in nso.segments:
        if seg.name == '.text':
            return seg.data
    sys.exit('credits: no .text segment')


def _w(text, va):
    return struct.unpack_from('<I', text, va)[0]


def _le(*words):
    return b''.join(struct.pack('<I', w) for w in words).hex(' ')


def check_anchors(text):
    """
    Keyed on what SURVIVES patching -- the two-word tail at every site, and
    the y sites in full. HANDOFF-101 s2.4 rule 1: discovery must work in both
    states, so never anchor on the words we rewrite.
    """
    bad = []
    for name, va, _ in SITES:
        for k, want in enumerate(TAIL):
            got = _w(text, va + 8 + k * 4)
            if got != want:
                bad.append('  +0x%07X  have %08X want %08X   %s tail+%d'
                           % (va + 8 + k * 4, got, want, name, k))
    for name, va in Y_SITES:
        for k, want in enumerate(STOCK + TAIL):
            got = _w(text, va + k * 4)
            if got != want:
                bad.append('  +0x%07X  have %08X want %08X   %s (must stay stock)'
                           % (va + k * 4, got, want, name))
    va, want, what = CLEAR_ANCHOR
    if _w(text, va) != want:
        bad.append('  +0x%07X  have %08X want %08X   %s'
                   % (va, _w(text, va), want, what))
    if bad:
        sys.exit('credits: REFUSED -- this is not the module these addresses\n'
                 'were read from, or something else has patched the credits\n'
                 'fade quad.\n' + '\n'.join(bad))


def state(text):
    _, cva, cstock, cpatch = CLEAR_SITE
    st = sum(1 for _, va, _ in SITES
             if (_w(text, va), _w(text, va + 4)) == STOCK) + (_w(text, cva) == cstock)
    ap = sum(1 for _, va, v in SITES
             if (_w(text, va), _w(text, va + 4)) == _pair(v)) + (_w(text, cva) == cpatch)
    if st == len(SITES) + 1:
        return 'stock'
    if ap == len(SITES) + 1:
        return 'applied'
    if st + ap == len(SITES) + 1:
        # Every leg is in a state we RECOGNISE, just not all the same one --
        # e.g. leg 1 applied by hand before leg 2 existed. Completable, not
        # corrupt, and refusing here would strand anyone who tested early.
        return 'partial'
    return 'mixed'


def _dis(word):
    if capstone is None:
        return ''
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    o = list(md.disasm(struct.pack('<I', word), 0))
    return (o[0].mnemonic + ' ' + o[0].op_str) if o else '??'


def verify(text, verbose=True):
    checks = 0
    fails = []

    def ok(cond, what):
        nonlocal checks
        checks += 1
        if not cond:
            fails.append(what)

    check_anchors(text)
    ok(True, 'anchors')

    for name, va, value in SITES:
        w0, w1 = _pair(value)

        # 1. THE TRAP. sf must be set: these are the 64-bit forms. Checking
        #    the printed text would pass movn w8 straight through.
        ok((w0 >> 31) & 1 == 1, '%s: movn/movz is 32-bit, not 64-bit' % name)
        ok(w0 & 0x1F == 8, '%s: destination is not x8' % name)
        ok((w1 >> 31) & 1 == 1, '%s: fmov is not the 64-bit form' % name)
        ok(w1 == 0x9E670100, '%s: fmov is not `fmov d0, x8`' % name)

        # 2. the immediate really encodes the value we intend
        imm = (w0 >> 5) & 0xFFFF
        got = (~imm & 0xFFFFFFFFFFFFFFFF) - (1 << 64) if value < 0 else imm
        ok(got == value, '%s: encodes %d, wanted %d' % (name, got, value))

        # 3. capstone agrees it is a 64-bit move of that value into x8
        d = _dis(w0)
        ok(d.startswith('mov x8,'), '%s: %r is not a 64-bit mov to x8' % (name, d))
        ok(_dis(w1) == 'fmov d0, x8', '%s: %r' % (name, _dis(w1)))

        # 4. x8 is dead: the next instruction writes w8, which zeroes x8[63:32]
        ok(_w(text, va + 8) == 0xB9406308,
           '%s: the word that kills x8 is not `ldr w8,[x24,#0x60]`' % name)

        # 5. the consumer is the 64-bit int->double convert
        ok(_w(text, va + 12) == 0x5E61D800,
           '%s: consumer is not `scvtf d0, d0`' % name)

    ok(state(text) in ('stock', 'applied', 'partial'),
       'module is MIXED: a site is in a state I do not recognise')

    # leg 2: the colour-clear force
    name, cva, cstock, cpatch = CLEAR_SITE
    ok((cpatch >> 31) & 1 == 0, '%s: must be the 32-bit movz' % name)
    ok(cpatch & 0x1F == 20, '%s: destination is not w20' % name)
    ok(((cpatch >> 5) & 0xFFFF) == 1, '%s: immediate is not 1' % name)
    ok(_dis(cpatch) == 'mov w20, #1', '%s: %r' % (name, _dis(cpatch)))
    ok(_w(text, CLEAR_ANCHOR[0]) == CLEAR_ANCHOR[1],
       '%s: w20 is not stored as the clear argument' % name)

    # 6. the quad must stay full height -- y sites untouched (also in anchors,
    #    asserted again here so a future edit cannot quietly drop it)
    for name, va in Y_SITES:
        ok((_w(text, va), _w(text, va + 4)) == STOCK,
           '%s has been modified; the intro would grow top/bottom bars' % name)

    if verbose:
        print('  %d check(s), %d failure(s)' % (checks, len(fails)))
        for f in fails:
            print('  ! ' + f)
    return fails


def _mutants(text):
    """
    A suite that does not bite is decor. Two families, because there are two
    kinds of thing that can be wrong here.

      ENCODER mutants -- what we would WRITE. These are the dangerous ones:
      the silent 32-bit movn, the wrong destination register, an off-by-one
      immediate. verify's checks 1-3 must catch every one.

      IMAGE mutants -- what we would write INTO. If the site is not the
      idiom we think it is, checks 4-6 and the anchors must refuse.
    """
    import types
    slipped = []
    orig_pair = globals()['_pair']

    def bad_pair(maker):
        def f(value):
            return maker(value)
        return f

    encoder_mutants = {
        'movn/movz as 32-bit (THE trap)':
            lambda v: ((0x12800000 | (((-v) - 1 if v < 0 else v) << 5) | 8),
                       fmov_d_x(0, 8)),
        'destination x9 instead of x8':
            lambda v: ((movn_x(9, (-v) - 1) if v < 0 else movz_x(9, v)),
                       fmov_d_x(0, 9)),
        'immediate off by one':
            lambda v: ((movn_x(8, (-v)) if v < 0 else movz_x(8, v + 1)),
                       fmov_d_x(0, 8)),
        'fmov into s0 (32-bit) not d0':
            lambda v: ((movn_x(8, (-v) - 1) if v < 0 else movz_x(8, v)),
                       0x1E270100),
    }
    for why, maker in encoder_mutants.items():
        globals()['_pair'] = bad_pair(maker)
        try:
            if not verify(text, verbose=False):
                slipped.append('ENCODER: ' + why)
        finally:
            globals()['_pair'] = orig_pair

    image_mutants = {
        'the x8-killing load is gone': (8, 0xD503201F),
        'the consumer is not scvtf':  (12, 0xD503201F),
        'a y site has been patched':  (None, None),
    }
    for why, (off, word) in image_mutants.items():
        t = bytearray(text)
        if off is None:
            struct.pack_into('<I', t, Y_SITES[0][1], movz_x(8, 747))
        else:
            struct.pack_into('<I', t, SITES[0][1] + off, word)
        try:
            if not verify(bytes(t), verbose=False):
                slipped.append('IMAGE: ' + why)
        except SystemExit:
            pass          # anchors refused outright -- that counts as caught
    return slipped


def show(path):
    nso = nso_patcher.read_nso(nso_patcher.Path(path))
    t = _text(nso)
    check_anchors(t)
    print('  %s' % path)
    print('  state: %s' % state(t))
    for name, va, value in SITES:
        a, b = _w(t, va), _w(t, va + 4)
        tag = 'stock' if (a, b) == STOCK else (
            'APPLIED %d' % value if (a, b) == _pair(value) else '???')
        print('    +0x%07X  %08X %08X  %-11s %s ; %s'
              % (va, a, b, tag, _dis(a), _dis(b)))
    name, cva, cstock, cpatch = CLEAR_SITE
    w = _w(t, cva)
    print('    +0x%07X  %08X           %-11s %s'
          % (cva, w, 'stock' if w == cstock else ('APPLIED' if w == cpatch else '???'),
             _dis(w)))
    print('  y sites (must stay stock -- the intro has no top/bottom bars):')
    for name, va in Y_SITES:
        print('    +0x%07X  %08X %08X  %s' % (va, _w(t, va), _w(t, va + 4), name))


def patch(path, direction, log=print):
    nso = nso_patcher.read_nso(nso_patcher.Path(path))
    t = _text(nso)
    check_anchors(t)
    st = state(t)
    if direction == 'apply' and st == 'applied':
        log('  already applied, nothing to do')
        return
    if direction == 'revert' and st == 'stock':
        log('  already stock, nothing to do')
        return
    if st == 'mixed':
        sys.exit('credits: REFUSED -- a site is in a state I do not recognise')
    if st == 'partial':
        log('  partial: some legs are already in the wanted state, '
              'doing only the rest')
    fails = verify(t, verbose=False)
    if fails:
        sys.exit('credits: REFUSED --\n  ' + '\n  '.join(fails))

    patches = []
    name, cva, cstock, cpatch = CLEAR_SITE
    ca, cb = (cstock, cpatch) if direction == 'apply' else (cpatch, cstock)
    if _w(t, cva) != cb:                      # skip if already where we want it
        patches.append({'name': '%s  %s' % (name,
                        'off -> every frame' if direction == 'apply'
                        else 'every frame -> off'),
                        'va': cva, 'expect': _le(ca), 'set': _le(cb)})
    for name, va, value in SITES:
        new = _pair(value)
        a, b = (STOCK, new) if direction == 'apply' else (new, STOCK)
        if (_w(t, va), _w(t, va + 4)) == b:
            continue
        arrow = ('stock -> %d' % value) if direction == 'apply' else ('%d -> stock' % value)
        patches.append({'name': '%s  %s' % (name, arrow), 'va': va,
                        'expect': _le(*a), 'set': _le(*b)})
    if not patches:
        log('  nothing to do')
        return
    for line in nso_patcher.apply_spec(nso, {'patches': patches}):
        log('    ' + line)
    blob = nso_patcher.rebuild(nso)
    with open(path, 'wb') as f:
        f.write(blob)
    log('  wrote %s  (%s)' % (path, hashlib.md5(blob).hexdigest()))

    t2 = _text(nso_patcher.read_nso(nso_patcher.Path(path)))
    log('  read back:')
    for name, va, value in SITES:
        got = (_w(t2, va), _w(t2, va + 4))
        want = _pair(value) if direction == 'apply' else STOCK
        log('    +0x%07X  %08X %08X  %s'
              % (va, got[0], got[1], 'OK' if got == want else 'MISMATCH'))
        if got != want:
            sys.exit('credits: read-back mismatch, do not boot this')


# ---------------------------------------------------------------------------
# build.py / GUI entry points
# ---------------------------------------------------------------------------
CREDITS_ENV = 'SEVENTH_NX_CREDITS'


def enabled() -> bool:
    """
    ON with 16:9, OFF at 4:3, overridable for an A/B.

    NO CHECKBOX -- same footing as `ff7nx_modelcull`, `ff7nx_battlewide`,
    `ff7nx_swirlscale` and `ff7nx_uiclip`. -107 and 747 are widescreen values;
    at 4:3 the visible span IS 0..640 and widening the fade quad would drag it
    off both edges of a frame that was never narrowed. It is not milder at
    4:3, it is wrong there.
    """
    v = os.environ.get(CREDITS_ENV)
    if v is not None:
        return v not in ('', '0', 'off', 'false')
    try:
        import ff7nx_ws
        return ff7nx_ws.enabled()
    except Exception:                                          # noqa: BLE001
        return False


def apply(main, revert=False, log=print) -> int:
    path = _main_path(str(main))
    try:
        t = _text(nso_patcher.read_nso(nso_patcher.Path(path)))
        check_anchors(t)
    except SystemExit as exc:
        log('  ! credits fade quad: %s' % exc)
        return 1
    st = state(t)
    if st == 'mixed':
        log('  ! credits fade quad: a site is in an unrecognised state; '
            'refusing to guess.')
        return 1
    if st == ('stock' if revert else 'applied'):
        log('  credits fade quad: already %s'
            % ('reverted' if revert else 'applied'))
        return 0
    fails = verify(t, verbose=False)
    if fails:
        for f in fails:
            log('  ! credits fade quad: ' + f)
        return 1
    try:
        patch(path, 'revert' if revert else 'apply', log=log)
    except SystemExit as exc:
        log('  ! credits fade quad: %s' % exc)
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('target')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--show', action='store_true')
    g.add_argument('--apply', action='store_true')
    g.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    path = _main_path(a.target)
    if a.show:
        show(path); return
    if a.apply or a.revert:
        patch(path, 'apply' if a.apply else 'revert'); return
    t = _text(nso_patcher.read_nso(nso_patcher.Path(path)))
    print('  %s' % path)
    print('  state: %s' % state(t))
    print('  leg 1  fade quad x: %d .. %d   (y untouched: full height)'
          % (LEFT_X, RIGHT_X))
    print('  leg 2  colour clear forced on every credits frame')
    fails = verify(t)
    slipped = _mutants(t)
    print('  mutation: %d of 7 mutant(s) slipped through' % len(slipped))
    for m in slipped:
        print('  ! not caught: ' + m)
    if fails or slipped:
        sys.exit(1)
    print('  OK -- safe to --apply')


if __name__ == '__main__':
    main()
