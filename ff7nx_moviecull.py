#!/usr/bin/env python3
r"""
ff7nx_moviecull.py -- keep field models out of the 16:9 margin WHILE A MOVIE
PLAYS, by culling them instead of scissoring them.

    python3 ff7nx_moviecull.py <exefs/main | sdout> --verify
    python3 ff7nx_moviecull.py <exefs/main | sdout> --show
    python3 ff7nx_moviecull.py <exefs/main | sdout> --apply
    python3 ff7nx_moviecull.py <exefs/main | sdout> --revert

=============================================================================
0. WHY ff7nx_movieclip HAD TO GO, AND WHAT ITS FAILURE PROVED
=============================================================================
`ff7nx_movieclip` narrowed **glScissor** to the central 4:3 while a movie
played. It worked -- and that is the problem. A scissor is not a per-draw
filter; it is frame state. Everything drawn while it is narrow is clipped,
including the two draws that are supposed to paint the margins:

    the frame CLEAR      -> the margins are never cleared
    the letterbox fill   -> nothing repaints them

so the last field frame drawn before the movie started stays frozen in the
left and right margins for the whole FMV. That is the screenshot: reactor
wall on the left, lit windows on the right, movie in the middle.

The A/B that settled it, both states measured on hardware:

    movieclip           models in margin      margin contents
    ------------------  --------------------  ---------------------------
    ON                  clipped   (correct)   STALE field art  (wrong)
    OFF                 drawn over the movie  black            (correct)

Neither state is shippable, and no band arithmetic fixes that -- glScissor
cannot tell a model draw from a clear. **The requirement is exact: clip the
MODEL draws, and nothing else.**

HANDOFF-80 §5.0 called this "approach B" and set it aside as the fallback.
It is now the only approach left standing, and the reason it is better is
structural rather than a matter of taste.

=============================================================================
1. THE SITE -- field_do_draw_3d_model, WHICH ALREADY DOES EXACTLY THIS JOB
=============================================================================
`field_do_draw_3d_model` (x86 0x639252, ARM +0x9EC300..+0x9EC510) is called
once per model per frame and returns "draw / do not draw" from the model's
screen position. It touches no render state at all, so it cannot reach the
clear, the frame fill, or the margins. FFNx replaces the whole function for
the same reason (`widescreen.cpp:156` -> `ff7::field::ff7_field_do_draw_3d_model`).

`ff7nx_modelcull` already owns its two horizontal bounds:

    +0x9EC43C   sub w9, w8, #imm     x - left_offset     40 stock / 97 at 16:9
    +0x9EC49C   add w8, w8, #imm     x + right_offset   400 stock / 457 at 16:9

The 16:9 pair is what lets a model be drawn out in the widened margin, which
is right for the field and wrong for an FMV. During playback the picture IS
4:3 -- the movie quad is game x 0..640 -- so the correct bound during
playback is the stock 4:3 pair. **The values we want are already the values
the game shipped with.** Nothing is invented here; the patch chooses between
two bounds this project has already measured.

=============================================================================
2. THE GATE -- THE SAME THREE LOADS movieclip PROVED ON HARDWARE
=============================================================================
    [[0x12CE7C0]] + 0x1FC     movie_object->is_playing

set by `fw_movie_start` (+0x10F1554), zeroed by `fw_movie_stop` (+0x10F177C);
FFNx's `ff7_externals.movie_object->is_playing`.

This is not a fresh derivation. `ff7nx_movieclip` ran this exact three-load
chain, with the same three `cbz` guards, on hardware -- and the margins going
stale *only during an FMV* is itself the proof that the gate fires when a
movie plays and does not fire otherwise. The one part of movieclip that was
right is the part being reused.

=============================================================================
3. THE TWO CAVES
=============================================================================
Eleven words each. The not-playing branch is the DISPLACED WORD ITSELF, read
out of the module at apply time -- so this composes with whatever
`ff7nx_modelcull` chose rather than hardcoding 97/457 a second time and
inventing a way for the two modules to disagree.

    adrp x11, #0x12CE000
    ldr  x11, [x11, #0x7c0]
    cbz  x11, wide
    ldr  x11, [x11]
    cbz  x11, wide
    ldr  w11, [x11, #0x1fc]
    cbz  w11, wide
    sub  w9, w8, #40            <- a movie is playing: the 4:3 bound
    b    ret
  wide:
    sub  w9, w8, #97            <- the displaced word, verbatim
    b    ret

REGISTERS, CHECKED AGAINST THE DISASSEMBLY RATHER THAN ASSUMED
--------------------------------------------------------------
The left cave uses x11; the right cave uses x10. Both are AArch64 caller-
saved temporaries, so the recompiler never keeps a value in one across a
call, and at each site the register is written by the code that follows
before it is ever read:

    left  site +0x9EC43C:  w11 is written at +0x9EC450 `cset w11, eq`
                           (w10 is LIVE -- loaded +0x9EC438, used +0x9EC440)
    right site +0x9EC49C:  w10 is written at +0x9EC4A0 `sub w10, w9, w8`
                           (w9  is LIVE -- loaded +0x9EC498, used +0x9EC4A0)

NZCV is clobbered by the `cbz`s and that is safe: the next flag-setting
instruction after each site is the one that produces the flags the code
actually consumes (`subs w8, w10, w9` at +0x9EC440, `cmp w8, w11` at
+0x9EC4BC). Nothing reads flags across either hook.

WHAT THIS COSTS
---------------
Models POP at the boundary instead of being sliced. That is the known price
of culling over clipping, and it is why HANDOFF-80 ranked it second. During
a mostly static FMV it reads fine; a scissor that eats the margins does not.

WHAT THIS IS NOT
----------------
Not vertical. The movie quad and the model band are both device rows 24..696
after `ff7nx_letterbox` leg three, so there is nothing to close there, and
the stock `y - 120 / y + 460` pair is left exactly as FFNx leaves it.

ORDER
-----
Run AFTER `ff7nx_modelcull`, because the wide branch is read out of the
module. Running it first would bake the stock bound into both branches and
the cave would be a very expensive no-op -- `--verify` says so out loud
rather than leaving it to the log.
"""
from __future__ import annotations

import argparse
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, '7th_heaven_nx')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import a64 as A                                                # noqa: E402
import ff7nx_cave                                              # noqa: E402

MOVIECULL_ENV = 'SEVENTH_NX_MOVIE_CULL'
SDOUT_MAIN = os.path.join('atmosphere', 'contents',
                          '0100A5B00BDC6000', 'exefs', 'main')

# --------------------------------------------------------------- the sites
FUNC = 0x9EC300                 # field_do_draw_3d_model, x86 0x639252

LEFT_SITE = 0x9EC43C            # sub w9, w8, #imm
RIGHT_SITE = 0x9EC49C           # add w8, w8, #imm

STOCK_LEFT = 40                 # the 4:3 bounds -- what the game shipped
STOCK_RIGHT = 400
WIDE_LEFT = 97                  # ff7nx_modelcull's 16:9 bounds
WIDE_RIGHT = 457

# scratch register per site, dead at the hook (see the docstring)
LEFT_SCRATCH = 11
RIGHT_SCRATCH = 10

PAGE = 0x12CE000
MOVIE_PTR_OFF = 0x7C0
IS_PLAYING_OFF = 0x1FC

N_WORDS = 11

# Anchors: every one is read out of the stock dump, and each says something
# different. The first four prove the function; the last three prove the gate.
ANCHORS = [
    (0x9EC388, 0x5101E109, 'sub w9, w8, #0x78     y - 120, FFNx leaves it'),
    (0x9EC3E8, 0x11073108, 'add w8, w8, #0x1cc    y + 460, FFNx leaves it'),
    (0x9EC364, 0x529E4093, 'mov  w19, #0xf204  \\  0xCFF204, the viewport'),
    (0x9EC368, 0x72A019F3, 'movk w19, #0xcf,16 /  point, formed in the open'),
    (0x9EC438, 0xB9400A8A, 'ldr w10, [x20, #8]    w10 is LIVE at the left site'),
    (0x9EC498, 0xB9400689, 'ldr w9, [x20, #4]     w9 is LIVE at the right site'),
    (0x9EC450, 0x1A9F17EB, 'cset w11, eq          w11 is DEAD at the left site'),
    (0x9EC4A0, 0x4B08012A, 'sub w10, w9, w8       w10 is DEAD at the right site'),
    (0x10F1554, 0xF943E108, 'ldr x8, [x8, #0x7c0]  fw_movie_start'),
    (0x10F155C, 0xB941FD09, 'ldr w9, [x8, #0x1fc]  is_playing'),
    (0x10F177C, 0xA91FFD1F, 'stp xzr, xzr, [x8, #0x1f8]   fw_movie_stop'),
]

IMM12_MASK = 0xFFF << 10


def _imm12(word):
    return (word >> 10) & 0xFFF


def _set_imm12(word, value):
    if not 0 <= value <= 0xFFF:
        raise ValueError('%d does not fit in an imm12' % value)
    return (word & ~IMM12_MASK) | (value << 10)


def _is_addsub_imm(word):
    """ADD/SUB (immediate), 32-bit, shift == 0."""
    return ((word & 0x7F800000) in (0x11000000, 0x51000000)
            and ((word >> 22) & 1) == 0)


def _fmt(word):
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


def _text(path):
    import nso_tool
    return nso_tool.parse_nso(str(path))['segments']['.text']['data']


def w32(t, va):
    return struct.unpack_from('<I', t, va)[0]


# ------------------------------------------------------------------ the cave
def cave_words(addr, return_va, scratch, playing_word, wide_word):
    """
    The eleven words, laid out at addr(i).

    `playing_word` and `wide_word` are complete ADD/SUB instructions, so the
    destination register, the source register and the opcode all come from
    the site rather than from anything this file believes about it.
    """
    r = scratch
    wide = addr(9)
    w = [
        A.adrp(r, addr(0), PAGE),                 # adrp x?, #0x12ce000
        A.ldr64(r, r, MOVIE_PTR_OFF),             # ldr  x?, [x?, #0x7c0]
        A.cbz64(r, addr(2), wide),
        A.ldr64(r, r, 0),                         # ldr  x?, [x?]
        A.cbz64(r, addr(4), wide),
        A.ldr(r, r, IS_PLAYING_OFF),              # ldr  w?, [x?, #0x1fc]
        A.cbz(r, addr(6), wide),
        playing_word,                             # the 4:3 bound
        A.b(addr(8), return_va),
        wide_word,                                # the displaced word
        A.b(addr(10), return_va),
    ]
    assert len(w) == N_WORDS, 'N_WORDS is %d, body is %d' % (N_WORDS, len(w))
    return w


DISASM = [
    'adrp x?, #0x12ce000', 'ldr x?, [x?, #0x7c0]', 'cbz x?, #wide',
    'ldr x?, [x?]', 'cbz x?, #wide', 'ldr w?, [x?, #0x1fc]', 'cbz w?, #wide',
    '<4:3 bound>', 'b #ret', '<16:9 bound, displaced>', 'b #ret',
]


# --------------------------------------------------------------- state
def cave_state(t, va):
    """'stock' (an add/sub imm), 'patched' (a b into a cave), or 'unknown'."""
    got = w32(t, va)
    if _is_addsub_imm(got):
        return 'stock'
    if (got & 0xFC000000) == 0x14000000:
        return 'patched'
    return 'unknown'


def state(t):
    return {
        'left': cave_state(t, LEFT_SITE),
        'right': cave_state(t, RIGHT_SITE),
        'left_word': w32(t, LEFT_SITE),
        'right_word': w32(t, RIGHT_SITE),
    }


def installed(t):
    st = state(t)
    return st['left'] == 'patched' and st['right'] == 'patched'


def check_anchors(t, log=lambda *_: None):
    bad = []
    for va, want, what in ANCHORS:
        got = w32(t, va)
        if got != want:
            bad.append('+%#09x is %08X, expected %08X -- %s'
                       % (va, got, want, what))
    for va, name in ((LEFT_SITE, 'left'), (RIGHT_SITE, 'right')):
        s = cave_state(t, va)
        if s == 'unknown':
            bad.append('%s cull +%#09x is %08X -- neither an ADD/SUB '
                       'immediate nor a branch; refusing'
                       % (name, va, w32(t, va)))
    for b in bad:
        log('  ! ' + b)
    return bad


# ------------------------------------------------------------------ walking
def _b_target(word, va):
    """The target of an unconditional `b`, or None if it is not one."""
    if (word & 0xFC000000) != 0x14000000:
        return None
    imm = word & 0x03FFFFFF
    if imm & (1 << 25):
        imm -= 1 << 26
    return va + imm * 4


def walk(t, hook):
    """
    The cave's LOGICAL word list -- the eleven words as written, with the
    chaining branches `ff7nx_cave` inserts between padding holes removed.

    A cave is cut into 2-3 word runs and each run ends in a `b` to the next,
    so a raw linear read interleaves link branches with real instructions and
    nothing lines up. The discriminator is exact and needs no heuristic: a
    `b` to `hook + 4` is the cave's own RETURN and is part of the logic;
    every other `b` is a link and is followed silently.
    """
    tgt = _b_target(w32(t, hook), hook)
    if tgt is None:
        return None
    va, out, seen = tgt, [], set()
    while len(out) < N_WORDS:
        if va in seen:
            break
        seen.add(va)
        x = w32(t, va)
        b = _b_target(x, va)
        if b is not None and b != hook + 4:
            va = b                        # chain link -- not part of the logic
            continue
        out.append((va, x))
        va += 4
    return out


def walk_physical(t, hook):
    """
    Every ADDRESS the cave occupies, chaining branches included.

    `walk()` gives the logic; this gives the footprint, and revert needs the
    footprint -- zeroing only the logical words leaves the link branches
    behind as live code in someone else's padding, which is exactly the kind
    of residue that makes the next module's allocator skip a usable hole and
    the next apply->revert fail its byte-identity check.
    """
    tgt = _b_target(w32(t, hook), hook)
    if tgt is None:
        return []
    va, out, logical = tgt, [], 0
    while logical < N_WORDS and va not in out:
        x = w32(t, va)
        out.append(va)
        b = _b_target(x, va)
        if b is not None and b != hook + 4:
            va = b
            continue
        logical += 1
        va += 4
    return out


def bounds_in_module(t):
    """
    (playing_left, wide_left, playing_right, wide_right) as the SHIPPED
    module computes them -- read back out of the caves, or off the two
    single-word sites when nothing is hooked.
    """
    out = []
    for site in (LEFT_SITE, RIGHT_SITE):
        if cave_state(t, site) == 'stock':
            v = _imm12(w32(t, site))
            out += [None, v]
            continue
        got = walk(t, site) or []
        imms = [_imm12(x) for _, x in got if _is_addsub_imm(x)]
        out += [imms[0] if len(imms) > 0 else None,
                imms[1] if len(imms) > 1 else None]
    return tuple(out)


# ------------------------------------------------------------------ planning
def build_patches(img, starts, log=print):
    """
    {va: word} for both caves, or None if it cannot be done safely.

    `img` is the whole module image (nxmap.Main.img) and `starts` its ARM
    function starts -- the cave allocator needs both, and a mutable copy is
    made so the SECOND cave's allocator can see the holes the first one took.
    """
    img = bytearray(img)
    out = {}
    for site, scratch, stock, name in (
            (LEFT_SITE, LEFT_SCRATCH, STOCK_LEFT, 'left'),
            (RIGHT_SITE, RIGHT_SCRATCH, STOCK_RIGHT, 'right')):
        displaced = w32(img, site)
        if not _is_addsub_imm(displaced):
            log('  ! %s cull +%#09x is %08X, not an ADD/SUB immediate'
                % (name, site, displaced))
            return None
        wide_imm = _imm12(displaced)
        playing = _set_imm12(displaced, stock)
        if wide_imm == stock:
            log('  ! %s cull is already the 4:3 bound %d -- '
                'ff7nx_modelcull has not run, the cave would do nothing'
                % (name, stock))
            return None

        pool = ff7nx_cave.HolePool(img, starts=starts)
        entry, words = ff7nx_cave.emit_laid_out(
            pool,
            lambda entry_va, addr, _s=scratch, _p=playing, _w=displaced:
                cave_words(addr, site + 4, _s, _p, _w),
            span=0x80000)
        words[site] = A.b(site, entry)
        for va, word in words.items():
            struct.pack_into('<I', img, va, word)      # so run 2 sees run 1
        out.update(words)
        log('  %-5s cull cave: %d words in padding, entry +%#x  '
            '(playing %d, otherwise %d)'
            % (name, N_WORDS, entry, stock, wide_imm))
    return out


def revert_patches(t, log=print):
    """{va: word} that puts both sites back to a single ADD/SUB immediate."""
    out = {}
    for site, name in ((LEFT_SITE, 'left'), (RIGHT_SITE, 'right')):
        if cave_state(t, site) != 'patched':
            continue
        got = walk(t, site) or []
        wide = [x for _, x in got if _is_addsub_imm(x)]
        if len(wide) != 2:
            log('  ! %s cave does not contain exactly two ADD/SUB words; '
                'refusing to guess what to restore' % name)
            return None
        out[site] = wide[1]                      # the displaced word
        for va in walk_physical(t, site):        # footprint, links included
            if va != site:
                out[va] = 0                      # give the hole back
        log('  %-5s cull cave removed (%d word(s) of padding returned), '
            'site restored to %08X (offset %d)'
            % (name, len(walk_physical(t, site)), wide[1], _imm12(wide[1])))
    return out


# ------------------------------------------------------------------ emulation
def _emu():
    import ws_emu
    import arm64emu

    class Cpu(ws_emu.Cpu):
        def step(self, w, pc):
            if (w & 0x9F000000) == 0x90000000:              # adrp
                rd = w & 31
                imm = (((w >> 5) & 0x7FFFF) << 2) | ((w >> 29) & 3)
                if imm & (1 << 20):
                    imm -= (1 << 21)
                self.set(rd, ((pc & ~0xFFF) + (imm << 12)) & arm64emu.M64)
                return None
            return ws_emu.Cpu.step(self, w, pc)

    return Cpu, arm64emu


def emulate(site, scratch, stock, displaced, x, playing):
    """
    Execute a cave's real words. Returns the bound the site produces.

    `x` goes in w8, which is what both sites read; the left cave writes w9
    and the right cave writes w8, so both are read back.
    """
    Cpu, arm64emu = _emu()
    mem = arm64emu.Mem()
    base = PAGE + 0x40000
    slot, obj = 0x2100000, 0x2101000
    mem.setu(PAGE + MOVIE_PTR_OFF, slot, 8)
    mem.setu(slot, obj, 8)
    mem.setu(obj + IS_PLAYING_OFF, 1 if playing else 0, 4)
    playing_word = _set_imm12(displaced, stock)
    words = cave_words(lambda i: base + 4 * i, base + 4 * N_WORDS,
                       scratch, playing_word, displaced)
    cpu = Cpu(mem)
    cpu.set(8, x, True)
    cpu.set(9, 0x11111111, True)
    cpu.set(10, 0x22222222, True)
    cpu.run(base, words, stop_at=base + 4 * N_WORDS)
    dest = 9 if site == LEFT_SITE else 8
    other = 10 if site == LEFT_SITE else 9

    def s32(v):
        v &= 0xFFFFFFFF
        return v - (1 << 32) if v & 0x80000000 else v

    return s32(cpu.get(dest, True)), cpu.get(other, True) & 0xFFFFFFFF


# ------------------------------------------------------------------ verify
def verify(main=None, log=print):
    fails = []

    def ck(cond, what):
        log('    %s  %s' % ('ok  ' if cond else 'FAIL', what))
        if not cond:
            fails.append(what)

    log('  the cave, executed (ws_emu + ADRP), on a model at screen x:')
    log('')
    log('    site   x      is_playing=1        is_playing=0       live reg')
    for site, scratch, stock, wide, sign in (
            (LEFT_SITE, LEFT_SCRATCH, STOCK_LEFT, WIDE_LEFT, -1),
            (RIGHT_SITE, RIGHT_SCRATCH, STOCK_RIGHT, WIDE_RIGHT, +1)):
        displaced = _set_imm12(
            0x5100A109 if site == LEFT_SITE else 0x11064108, wide)
        for x in (0, 320, 1000):
            on, on_other = emulate(site, scratch, stock, displaced, x, True)
            off, off_other = emulate(site, scratch, stock, displaced, x, False)
            want_on = x + sign * stock
            want_off = x + sign * wide
            good = on == want_on and off == want_off
            live_ok = (on_other == (0x22222222 if site == LEFT_SITE
                                    else 0x11111111)
                       and off_other == on_other)
            ck(good, '%-5s x=%-5d playing -> %-6d (want %d), '
                     'not playing -> %-6d (want %d)'
               % ('left' if site == LEFT_SITE else 'right', x,
                  on, want_on, off, want_off))
            ck(live_ok, '%-5s x=%-5d the LIVE neighbour register is untouched'
               % ('left' if site == LEFT_SITE else 'right', x))

    log('')
    log('  arithmetic:')
    ck(WIDE_LEFT - STOCK_LEFT == WIDE_RIGHT - STOCK_RIGHT == 57,
       'the 16:9 pair is the 4:3 pair + 57 on each side (FFNx widescreen.cpp)')
    ck(_imm12(_set_imm12(0x5100A109, STOCK_LEFT)) == STOCK_LEFT
       and (_set_imm12(0x5100A109, STOCK_LEFT) & ~IMM12_MASK)
       == (0x5100A109 & ~IMM12_MASK),
       'setting the 4:3 immediate changes the immediate and nothing else')

    if main is None:
        log('')
        log('  %d failure(s)' % len(fails) if fails else '  all checks pass')
        return 1 if fails else 0

    t = _text(resolve_main(main))
    log('')
    log('  against %s:' % main)
    for b in check_anchors(t):
        ck(False, b)
    if not fails:
        ck(True, 'all %d anchors match the stock dump' % len(ANCHORS))

    st = state(t)
    log('    left  site  %s   %s' % (_fmt(st['left_word']), st['left']))
    log('    right site  %s   %s' % (_fmt(st['right_word']), st['right']))
    pl, wl, pr, wr = bounds_in_module(t)
    if installed(t):
        ck(pl == STOCK_LEFT and pr == STOCK_RIGHT,
           'the shipped caves use the 4:3 pair while playing (%s, %s)'
           % (pl, pr))
        ck(wl == WIDE_LEFT and wr == WIDE_RIGHT,
           'the shipped caves fall through to the 16:9 pair (%s, %s)'
           % (wl, wr))
        ck(_disasm_ok(t, log), 'both caves disassemble to the intended '
                               'sequence (capstone, from the WRITTEN module)')
    elif (wl, wr) == (STOCK_LEFT, STOCK_RIGHT):
        # A clean dump. Not a failure -- there is simply nothing to gate yet.
        log('    not installed, and the cull is stock 4:3 (%s / %s).' % (wl, wr))
        log('    This is the dump. ff7nx_modelcull must run first: with both')
        log('    branches at 40/400 the cave would be an expensive no-op, and')
        log('    build_patches() refuses rather than writing one.')
        import nxmap
        _m = nxmap.Main(resolve_main(main))
        ck(build_patches(_m.img, set(_m.arm_starts),
                         log=lambda *_: None) is None,
           'a stock module is REFUSED -- the cave is not written before '
           'ff7nx_modelcull has widened the bounds')
    else:
        log('    not installed; bounds in the module are left %s, right %s'
            % (wl, wr))
        ck(wl == WIDE_LEFT and wr == WIDE_RIGHT,
           'ff7nx_modelcull has run, so the wide branch would be 97/457')

    # mutation: the checks must bite
    for name, va, word in (('a moved y-120 anchor', 0x9EC388, 0xD503201F),
                           ('a moved is_playing load', 0x10F155C, 0xD503201F),
                           ('a left site that is neither add/sub nor b',
                            LEFT_SITE, 0xD503201F)):
        mut = bytearray(t)
        struct.pack_into('<I', mut, va, word)
        ck(bool(check_anchors(bytes(mut))), '%s is refused' % name)

    log('')
    log('  %d failure(s)' % len(fails) if fails else '  all checks pass')
    return 1 if fails else 0


def _disasm_ok(t, log=lambda *_: None):
    """Disassemble the WRITTEN caves with capstone, not with our encoder."""
    try:
        import capstone
    except ImportError:
        log('    (capstone not installed -- disassembly cross-check skipped)')
        return True
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    ok = True
    for site, scratch in ((LEFT_SITE, LEFT_SCRATCH),
                          (RIGHT_SITE, RIGHT_SCRATCH)):
        got = walk(t, site)
        if not got:
            return False
        seq = []
        for va, word in got:
            ins = list(md.disasm(struct.pack('<I', word), va))
            seq.append('%s %s' % (ins[0].mnemonic, ins[0].op_str)
                       if ins else '??')
        want_reg = 'x%d' % scratch
        checks = [
            len(seq) == N_WORDS,
            seq[0].startswith('adrp %s' % want_reg),
            seq[1] == 'ldr %s, [%s, #0x7c0]' % (want_reg, want_reg),
            seq[2].startswith('cbz %s' % want_reg),
            seq[3] == 'ldr %s, [%s]' % (want_reg, want_reg),
            seq[4].startswith('cbz %s' % want_reg),
            seq[5] == 'ldr w%d, [%s, #0x1fc]' % (scratch, want_reg),
            seq[6].startswith('cbz w%d' % scratch),
            seq[7].split()[0] in ('sub', 'add'),
            seq[8].startswith('b #'),
            seq[9].split()[0] in ('sub', 'add'),
            seq[10].startswith('b #'),
            # all three cbz go to the SAME place, and that place is word 9
            len({s.split(', ')[-1] for s in seq if s.startswith('cbz')}) == 1,
            (seq[2].split(', ')[-1]
             == '#%#x' % [va for va, _ in got][9]),
        ]
        if not all(checks):
            log('    ! cave at +%#x disassembles to:' % site)
            for s in seq:
                log('        %s' % s)
            ok = False
    return ok


# ------------------------------------------------------------------ plumbing
def enabled(env=None):
    """
    ON whenever the model cull is. The two are the same feature: `modelcull`
    widens the box for the field, this narrows it back for the one case where
    the picture is not wide.
    """
    raw = env if env is not None else os.environ.get(MOVIECULL_ENV)
    if raw is not None:
        return str(raw).strip().lower() not in ('', '0', 'off', 'no', 'false')
    try:
        import ff7nx_modelcull
        return ff7nx_modelcull.enabled()
    except Exception:
        return False


def resolve_main(path):
    if os.path.isdir(path):
        cand = os.path.join(path, SDOUT_MAIN)
        if os.path.exists(cand):
            return cand
        raise SystemExit('no %s under %s' % (SDOUT_MAIN, path))
    return path


def show(main, log=print):
    main = resolve_main(main)
    t = _text(main)
    st = state(t)
    pl, wl, pr, wr = bounds_in_module(t)
    log('  %s' % main)
    log('    +%#09X  %s  left   %s' % (LEFT_SITE, _fmt(st['left_word']),
                                       st['left']))
    log('    +%#09X  %s  right  %s' % (RIGHT_SITE, _fmt(st['right_word']),
                                       st['right']))
    if installed(t):
        log('    while a movie plays : x - %s .. x + %s   (4:3)' % (pl, pr))
        log('    otherwise           : x - %s .. x + %s' % (wl, wr))
    else:
        log('    always              : x - %s .. x + %s' % (wl, wr))
    bad = check_anchors(t, log)
    log('    anchors: %s' % ('OK' if not bad else '%d FAILED' % len(bad)))
    return 1 if bad else 0


def apply(main, revert=False, log=print):
    import nso_patcher
    import nxmap
    main = Path(resolve_main(main))
    m = nxmap.Main(str(main))
    t = m.text

    if check_anchors(t, log):
        log('  refusing to write.')
        return 1

    if revert:
        words = revert_patches(t, log)
        if words is None:
            return 1
    else:
        if installed(t):
            log('  movie model cull: already installed')
            return 0
        words = build_patches(m.img, set(m.arm_starts), log)
        if words is None:
            return 1
    if not words:
        log('  movie model cull: nothing to do')
        return 0

    patches = [{'name': 'ff7nx_moviecull +%#09X' % va,
                'va': hex(va),
                'expect': _fmt(w32(t, va)),
                'set': _fmt(word)}
               for va, word in sorted(words.items())]
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(nso, {'name': 'ff7nx_moviecull',
                                             'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.moviecull-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    t2 = _text(main)
    pl, wl, pr, wr = bounds_in_module(t2)
    log('  read back from the written module:')
    log('    movie playing : x - %s .. x + %s' % (pl, pr))
    log('    otherwise     : x - %s .. x + %s' % (wl, wr))
    if not revert and not _disasm_ok(t2, log):
        log('  ! the written caves do not disassemble to the intended '
            'sequence. DO NOT BOOT THIS.')
        return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.split('\n')[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('main', nargs='?')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--verify', action='store_true')
    a = ap.parse_args(argv)

    if a.show:
        if not a.main:
            ap.error('need a path to exefs/main')
        return show(a.main)
    if a.apply or a.revert:
        if not a.main:
            ap.error('need a path to exefs/main')
        return apply(a.main, revert=a.revert)
    print('ff7nx_moviecull -- cull the models, do not scissor the frame')
    print('')
    return verify(a.main, log=print)


if __name__ == '__main__':
    raise SystemExit(main())
