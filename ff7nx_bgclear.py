#!/usr/bin/env python3
"""
ff7nx_bgclear.py -- clear the field frame, so the 16:9 margins are BLACK
instead of whatever the buffer happened to hold.

    python3 ff7nx_bgclear.py <exefs/main> --show
    python3 ff7nx_bgclear.py <exefs/main> --apply
    python3 ff7nx_bgclear.py <exefs/main> --revert

WHY
===
MEASURED on the mod actually being played: 93 of its 682 fields have less
than 427 tile units of background art -- `mkt_ia` has 256, which does not
even fill 4:3 -- and the mod widened NONE of them. 27 of the 29 that carry a
config entry are marked `mode = 1` (extend_only), which is the mod saying
"do not put a wide background here". There is no art for those sides and no
setting can invent one.

What makes them look BROKEN rather than empty is that nothing clears the
frame. DISASSEMBLED, not assumed:

    +0x10D68C0   mov w0, #1 ; mov w1, #1 ; b +0x10D68D0      zero-arg thunk
    +0x10D68D0   gfx_drv_clear                               the real clear

and a whole-image scan for branches to either finds **zero callers**. The
clear is dead code in this build. So the margin keeps the last thing written
to those pixels, which is why it reads as the field's dominant palette
colour -- tan in Wall Market, green in the slums -- and why it used to show
torn fragments of the previous field's art back when more was being drawn
there.

WHERE THE HOOK GOES
===================
`+0x09F2E60` is the background draw. It is the single common parent of both
layer draws:

    +0x09F2F90   bl +0x0A06DE0     layer 1   (holds the left/right extents
    +0x09F3020   bl +0x0A059D0     layer 2    ff7nx_wsclamp tunes)

so clearing at its head happens once, before either layer paints, and the
art then covers everything it has art for. Nothing else in the frame is
touched: models and UI are drawn after this returns.

THE GUARD, AND WHY IT IS NOT OPTIONAL
=====================================
`gfx_drv_clear` takes its colour from `game_obj[0xa7c]` and resolves it
through `+0x10FC3A0`, which opens:

    cbz w0, +0x10FC3D0        ->  mov x0, xzr ; ret

i.e. **a colour index of 0 returns NULL**, and the clear then does
`ldp s8, s9, [x0, #8]` on it. Calling it unconditionally is a null
dereference waiting for the first frame where that field is not yet set up.
So the cave tests the game object and the colour index and skips the call if
either is zero. A frame that cannot be cleared safely is left alone, which
is the old behaviour, not a crash.

THE CAVE
========
The hook replaces the FIRST instruction of the function, which is a plain
`stp x22, x21, [sp, #-0x30]!` -- no branch target lands on it and it has no
dependencies -- and the cave re-executes it before returning. The function
takes no incoming argument that survives its own prologue (`w0` is computed
at +0x09F2E84 before its first use), so clobbering x0-x18 across the call is
safe. x29/x30 are saved because the cave makes a BL from a point where the
function has not yet saved the link register.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import a64 as A                                                  # noqa: E402
import nxmap                                                     # noqa: E402

# All DISASSEMBLED from md5 c5cbcec798ab854b828a149870deb473.
BG_DRAW = 0x09F2E60          # background draw, parent of both layer draws
BG_DRAW_FIRST = 0xA9BD57F6   # stp x22, x21, [sp, #-0x30]!
CLEAR_THUNK = 0x10D68C0      # mov w0,#1 ; mov w1,#1 ; b gfx_drv_clear
CLEAR_FN = 0x10D68D0
GAME_OBJ_PAGE = 0x12CE000    # adrp base used by gfx_drv_clear
GAME_OBJ_OFF = 0x3E8         # ldr x8, [x8, #0x3e8]
COLOUR_OFF = 0xA7C           # ldr w0, [x8, #0xa7c]

CAVE_WORDS = 11
X8, X9, X29, X30, SP = 8, 9, 29, 30, 31


def build_cave(entry_va, addr):
    """
    The cave, assembled against wherever its words actually land.

    It is not contiguous: the largest verified padding hole in this module is
    THREE words (7,531 holes, 7,672 usable words in total), so the body is
    scattered and chained by ff7nx_cave exactly as the 60 FPS pass scatters
    its own. `addr(i)` is the real address of the i'th word, which is why the
    two `cbz` and the `bl` are resolved through it rather than by arithmetic
    on a base.
    """
    SKIP = 8
    return [
        A.stp64_pre(X29, X30, SP, -0x10),      # 0  save FP/LR: we will BL
        A.adrp(X8, addr(1), GAME_OBJ_PAGE),    # 1
        A.ldr64(X8, X8, GAME_OBJ_OFF),         # 2  x8 = &game_obj
        A.ldr64(X8, X8, 0),                    # 3  x8 = game_obj
        A.cbz64(X8, addr(4), addr(SKIP)),      # 4  no game object -> skip
        A.ldr(X9, X8, COLOUR_OFF),             # 5  w9 = colour index
        A.cbz(X9, addr(6), addr(SKIP)),        # 6  index 0 resolves NULL
        A.bl(addr(7), CLEAR_THUNK),            # 7  clear the frame
        A.ldp64_post(X29, X30, SP, 0x10),      # 8  <- SKIP lands here
        BG_DRAW_FIRST,                         # 9  the displaced instruction
        A.b(addr(10), BG_DRAW + 4),            # 10 back into the function
    ]


def verify_base(text, log=print, want_stock_hook=True):
    """
    Refuse to touch a module that is not the one this was derived from.

    `want_stock_hook` is off when the cave is already installed -- the hook
    word is then a branch by design, and checking it against the stock
    prologue would report a correctly patched module as corrupt.
    """
    ok = True
    if want_stock_hook:
        got = struct.unpack_from('<I', text, BG_DRAW)[0]
        if got != BG_DRAW_FIRST:
            log('  ! +0x%07X background draw prologue: expected %08X, '
                'found %08X' % (BG_DRAW, BG_DRAW_FIRST, got))
            ok = False

    def word(off):
        return struct.unpack_from('<I', text, off)[0]

    checks = [
        (CLEAR_FN, 0x6DBB2BEB, 'gfx_drv_clear prologue'),
        (CLEAR_THUNK, 0x320003E0, 'clear thunk mov w0, #1'),
        (CLEAR_THUNK + 4, 0x320003E1, 'clear thunk mov w1, #1'),
        (CLEAR_THUNK + 8, A.b(CLEAR_THUNK + 8, CLEAR_FN), 'thunk -> clear'),
        (0x09F2F90, A.bl(0x09F2F90, 0x0A06DE0), 'layer 1 call'),
        (0x09F3020, A.bl(0x09F3020, 0x0A059D0), 'layer 2 call'),
    ]
    for off, want, what in checks:
        got = word(off)
        if got != want:
            log('  ! +0x%07X %s: expected %08X, found %08X'
                % (off, what, want, got))
            ok = False
    return ok


def find_installed(text):
    """The cave address if this is already patched, else None."""
    w = struct.unpack_from('<I', text, BG_DRAW)[0]
    if (w >> 26) != 0x05:                       # not a B
        return None
    imm = w & 0x03FFFFFF
    if imm & 0x02000000:
        imm -= 0x04000000
    return BG_DRAW + imm * 4


def installed_words(text, entry):
    """
    Every address the installed cave occupies, by WALKING it.

    Re-planning against the patched module does not work and must not be
    attempted: the holes this cave sits in are no longer zero, so the pool
    hands back a different layout and the revert would zero the wrong words.
    Following the chain reads what is actually there.
    """
    addrs, cur = [], entry
    for _ in range(64):
        if cur < 0 or cur + 4 > len(text):
            return None
        w = struct.unpack_from('<I', text, cur)[0]
        addrs.append(cur)
        if (w >> 26) == 0x05:                      # B
            imm = w & 0x03FFFFFF
            if imm & 0x02000000:
                imm -= 0x04000000
            tgt = cur + imm * 4
            if tgt == BG_DRAW + 4:
                return addrs
            cur = tgt
        else:
            cur += 4
    return None


def show(path, log=print):
    m = nxmap.Main(path)
    text = m.text
    log('module %s' % path)
    at = find_installed(text)
    if at is None:
        log('  background clear: NOT installed')
        log('  +0x%07X  %08X  stp x22, x21, [sp, #-0x30]!   (stock)'
            % (BG_DRAW, struct.unpack_from('<I', text, BG_DRAW)[0]))
    else:
        log('  background clear: INSTALLED, cave entry +0x%07X' % at)
    log('  callers of the clear thunk in this image: %d'
        % sum(1 for off in range(0, len(text) - 3, 4)
              if (struct.unpack_from('<I', text, off)[0] >> 26) == 0x25
              and off + (((struct.unpack_from('<I', text, off)[0] & 0x3FFFFFF)
                          ^ 0x2000000) - 0x2000000) * 4 == CLEAR_THUNK))
    log('  base module checks: %s'
        % ('pass' if verify_base(text, lambda *_: None, at is None)
           else 'FAIL'))


def _hex(word):
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


def plan(path):
    """(entry, {address: word}) for a fresh install, or None if no room."""
    import ff7nx_cave
    import cave_space
    m = nxmap.Main(path)
    starts = set(m.arm_starts)
    named = cave_space.named_targets(m.img[cave_space.RODATA:])
    pool = ff7nx_cave.HolePool(m.img, starts=starts, named=named)
    try:
        entry, placed = ff7nx_cave.emit_laid_out(pool, build_cave)
    except ff7nx_cave.NoRoom as exc:
        return None, str(exc)
    return (entry, placed), None


def build_spec(text, entry, placed, install=True):
    """
    A patch spec for nso_patcher, which verifies every ORIGINAL byte before
    it writes. A module another pass has since claimed these holes in fails
    loudly instead of being silently corrupted.
    """
    patches = []
    hook_now = struct.unpack_from('<I', text, BG_DRAW)[0]
    if install:
        patches.append({'name': 'background draw -> clear cave',
                        'va': '0x%X' % BG_DRAW,
                        'expect': _hex(BG_DRAW_FIRST),
                        'set': _hex(A.b(BG_DRAW, entry))})
        for va, w in sorted(placed.items()):
            if va == BG_DRAW:
                continue
            patches.append({'name': 'clear cave +0x%X' % va,
                            'va': '0x%X' % va,
                            'expect': _hex(0),
                            'set': _hex(w)})
    else:
        patches.append({'name': 'restore background draw prologue',
                        'va': '0x%X' % BG_DRAW,
                        'expect': _hex(hook_now),
                        'set': _hex(BG_DRAW_FIRST)})
        for va, w in sorted(placed.items()):
            if va == BG_DRAW:
                continue
            patches.append({'name': 'clear cave +0x%X' % va,
                            'va': '0x%X' % va,
                            'expect': _hex(w),
                            'set': _hex(0)})
    return {'name': 'field background frame clear', 'patches': patches}


def _write(path, out, spec, log, dry):
    from pathlib import Path
    import nso_patcher
    try:
        nso = nso_patcher.read_nso(Path(path))
        applied = nso_patcher.apply_spec(nso, spec)
        data = nso_patcher.rebuild(nso)
    except Exception as exc:                                   # noqa: BLE001
        log('! %s' % exc)
        log('  nothing was written; the module is unchanged')
        return False
    log('  %d word(s) verified and applied' % len(applied))
    if dry:
        log('  (dry run, nothing written)')
        return True
    with open(out, 'wb') as f:
        f.write(data)
    log('  wrote %s' % out)
    return True


def apply(path, out, log=print, dry=False):
    m = nxmap.Main(path)
    if find_installed(m.text) is not None:
        log('! already installed; run --revert first')
        return False
    if not verify_base(m.text, log):
        log('! this module is not the one the offsets were derived from; '
            'refusing to patch')
        return False
    got, err = plan(path)
    if got is None:
        log('! no room for the cave: %s' % err)
        return False
    entry, placed = got
    runs = _runs(sorted(placed))
    log('  cave entry +0x%07X, %d word(s) across %d hole(s)'
        % (entry, len(placed), len(runs)))
    log('  hook  +0x%07X  %08X -> b +0x%07X'
        % (BG_DRAW, BG_DRAW_FIRST, entry))
    return _write(path, out, build_spec(m.text, entry, placed, True), log, dry)


def _runs(addrs):
    out = []
    for a in addrs:
        if out and a == out[-1][-1] + 4:
            out[-1].append(a)
        else:
            out.append([a])
    return out


def revert(path, out, log=print, dry=False):
    m = nxmap.Main(path)
    at = find_installed(m.text)
    if at is None:
        log('  not installed; nothing to do')
        return True
    addrs = installed_words(m.text, at)
    if addrs is None:
        log('! the cave at +0x%07X does not walk back to the hook; '
            'restore the module from your backup instead' % at)
        return False
    log('  removing cave, entry +0x%07X, %d word(s)' % (at, len(addrs)))
    patches = [{'name': 'restore background draw prologue',
                'va': '0x%X' % BG_DRAW,
                'expect': _hex(A.b(BG_DRAW, at)),
                'set': _hex(BG_DRAW_FIRST)}]
    for va in addrs:
        patches.append({'name': 'clear cave +0x%X' % va,
                        'va': '0x%X' % va,
                        'expect': _hex(struct.unpack_from('<I', m.text, va)[0]),
                        'set': _hex(0)})
    return _write(path, out,
                  {'name': 'remove field background frame clear',
                   'patches': patches}, log, dry)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('main')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('-o', '--out', default=None)
    a = ap.parse_args(argv)
    out = a.out or a.main
    if a.revert:
        return 0 if revert(a.main, out, dry=a.dry_run) else 1
    if a.apply or a.dry_run:
        return 0 if apply(a.main, out, dry=a.dry_run) else 1
    show(a.main)
    return 0


if __name__ == '__main__':
    sys.exit(main())
