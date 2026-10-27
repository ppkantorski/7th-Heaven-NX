#!/usr/bin/env python3
"""
ff7nx_timerpos.py -- the field countdown timer, moved out to the 16:9 edge.

    python3 ff7nx_timerpos.py <exefs/main | sdout>            --verify
    python3 ff7nx_timerpos.py <exefs/main | sdout> --show
    python3 ff7nx_timerpos.py <exefs/main | sdout> --apply
    python3 ff7nx_timerpos.py <exefs/main | sdout> --revert


WHAT IS WRONG
=============
The countdown timer (Reactor 1's bomb, the Shinra HQ escape, ...) is a field
window with special type 1.  nmkin_1 opens it with

    WSIZW  3  x=10 y=10 w=160 h=70
    WSPCL  3  type=1  x=10 y=10

and the window draw places it at game x/y 24 in 640 space.  That was square
to the corner of a 4:3 frame.  Under 16:9 the frame's left edge is game x
-106.67 (ff7nx_wsclamp: 320 - 320/0.75), so the timer sits 36 px from the top
and 196 px from the left at 1280x720 -- the old 4:3 corner, measured on
Patrick's capture.

WHERE THE POSITION COMES FROM
=============================
FF7's window draw, x86 0x6EBF2C (ARM64 +0xD96470 .. +0xD99480).  The Switch
build runs its second arm (the 640-space one, entered at +0xD97CF0), which is
the only arm with the digit call:

    [0xDC3CB4] = win.x * 2                      +0xD97DCC  window left
    x1 = [0xDC3CB4] + win(+8) - win(+0xC)       the per-window viewport
    w  = win(+0xC) * 2                          clamped to x1 >= 0, w <= 639
    gfx_set_viewport(x1, y1, w, h)              +0xD98298
    box draw, then if win(+0x1B) == 1:
        digits(x = [0xDC3CB4] + win(+0x28) * 2, ...)   +0xD985BC

So moving the timer is one subtraction from [0xDC3CB4], for timer windows
only.  The viewport is the other half: it is clamped at x1 >= 0, and
ff7nx_uiclip maps a window rect's x through the shader's 0.75 -- game x 0
lands at device 160, the 4:3 edge -- so a moved timer would be cut off by
its own window's clip.  gfx_drv_setviewport also cannot take a negative x
(it is unsigned: x = -82 comes back as 6710722, executed in ws_emu).

THE FIX
=======
For a window whose special type is 1, the viewport goes full width
(x1 = 0, w = 640: exactly the full rect's x edges, which ff7nx_uiclip leaves
untouched and whose matrix is the identity in x), and [0xDC3CB4] moves left by
MARGIN = round(320 / WS_SCALE - 320) = 107.  The box, the digits and any text
in the window all read [0xDC3CB4] after this point, so they move together.
y is not touched.  Every other window is untouched.

The cost is the timer window's horizontal open/close clip; its height clip
is kept.  The timer opens with no box (WMODE 1) and no text, so there is
nothing for that clip to hide.

THE CAVE
========
Hook +0xD98274 `ldr w22, [x0]`, the load of x1 just before it is pushed.  At
that point the guest stack already holds y and, under it, w.

     ldr   w22, [x0]                displaced: x1
     ebp+8 -> window array          (kept in the guest's edx slot, [x25,#8]:
     ebp-0x1C -> window index        edx is dead here and survives the call)
     win(+0x1B) == 1 ?  else out
     w22 = 0                        x1 = 0
     [esp+4] = 640                  the pushed width
     [0xDC3CB4] -= 107              the window's left edge
  out:
     ldr   w8, [x25,#0x10]          the cave's calls clobbered w8 (esp)
     b     +0xD98278

Every guest access goes through the recompiler's translator (+0x10FC3A0)
on a complete effective address, so it is correct under the paged model.
NZCV is dead at the hook: the next instruction that is not a load or store
is `bl +0x10FC3A0`.

16:9 ONLY, like ff7nx_uiclip.  At 4:3 the old position is the right one.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: run directly, find src/
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
if _nx_os.path.join(_nx_root, 'src') not in _nx_sys.path:
    _nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install()
import argparse
import hashlib
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, nxpaths.ROOT)
import a64 as A                                                # noqa: E402
import nso_patcher                                             # noqa: E402
import ff7nx_cave                                              # noqa: E402

SDOUT_MAIN = os.path.join('atmosphere', 'contents', '0100A5B00BDC6000',
                          'exefs', 'main')
TIMERPOS_ENV = 'SEVENTH_NX_TIMER_POS'

HOOK = 0xD98274                    # ldr w22, [x0]     x1 of the viewport
HOOK_STOCK = 0xB9400016
RETURN_VA = 0xD98278
XLATE = 0x10FC3A0                  # guest -> host translator

WIN_X = 0xDC3CB4                   # the current window's left edge (guest)
WIN_SIZE = 0x30
WIN_SPECIAL = 0x1B                 # special window type; 1 = countdown timer
TIMER_TYPE = 1
FULL_W = 640                       # gfx viewport width that is the full rect

WS_SCALE = 0.75                    # ff7nx_ws's vertex shader scale
MARGIN = int(round(320 / WS_SCALE - 320))   # 107: the 16:9 side margin

# Words that must be stock in both states.  The hook word itself is what the
# cave replaces; everything around it is what the cave's reasoning rests on.
ANCHORS = [
    (0xD97DCC, 0x531F7916, 'lsl w22, w8, #1         [0xDC3CB4] = win.x * 2'),
    (0xD98264, 0xB9401728, 'ldr w8, [x25,#0x14]     ebp'),
    (0xD98268, 0x51005100, 'sub w0, w8, #0x14       &x1'),
    (0xD9826C, 0x940D904D, 'bl +0x10FC3A0'),
    (0xD98270, 0xB9401328, 'ldr w8, [x25,#0x10]     esp'),
    (0xD98278, 0x51001100, 'sub w0, w8, #4          push x1'),
    (0xD98284, 0x940D9047, 'bl +0x10FC3A0           (NZCV dead from here)'),
    (0xD98298, 0x97F3A432, 'bl +0xA81360            gfx_set_viewport'),
    (0xD98460, 0x11006D00, 'add w0, w8, #0x1b       win(+0x1B)'),
    (0xD98490, 0x7100051F, 'cmp w8, #1              timer window?'),
    (0xD98594, 0x0B080536, 'add w22, w9, w8, lsl #1 digit x'),
    (0xD985BC, 0x97FFEA15, 'bl +0xD92E10            the digit draw'),
]


# ---------------------------------------------------------------------------
# the cave
# ---------------------------------------------------------------------------
def body_words_at(addr):
    """The cave, with `addr(i)` the real address of word i."""
    w = [
        HOOK_STOCK,                                   #  0  w22 = x1
        A.ldr(8, 25, 0x14),                           #  1  ebp
        A.add_imm(0, 8, 8),                           #  2  &arg0
        None,                                         #  3  bl XLATE
        A.ldr(9, 0),                                  #  4  window array
        A.str_(9, 25, 8),                             #  5  -> guest edx
        A.ldr(8, 25, 0x14),                           #  6
        A.sub_imm(0, 8, 0x1C),                        #  7  &index
        None,                                         #  8  bl XLATE
        A.ldrsh(9, 0),                                #  9  index
        A.add_reg_lsl(9, 9, 9, 1),                    # 10  * 3
        A.ldr(10, 25, 8),                             # 11  window array
        A.add_reg_lsl(0, 10, 9, 4),                   # 12  + index * 48
        A.add_imm(0, 0, WIN_SPECIAL),                 # 13  &win(+0x1B)
        None,                                         # 14  bl XLATE
        A.ldrb(9, 0),                                 # 15
        A.cmp_imm(9, TIMER_TYPE),                     # 16
        None,                                         # 17  b.ne OUT
        A.movz(22, 0),                                # 18  x1 = 0
        A.ldr(8, 25, 0x10),                           # 19  esp
        A.add_imm(0, 8, 4),                           # 20  &pushed width
        None,                                         # 21  bl XLATE
        A.movz(9, FULL_W),                            # 22
        A.str_(9, 0),                                 # 23  width = 640
        A.movz(0, WIN_X & 0xFFFF),                    # 24
        A.movk_hi(0, WIN_X >> 16),                    # 25  &[0xDC3CB4]
        None,                                         # 26  bl XLATE
        A.ldr(9, 0),                                  # 27
        A.sub_imm(9, 9, MARGIN),                      # 28
        A.str_(9, 0),                                 # 29  left edge -= 107
        A.ldr(8, 25, 0x10),                           # 30  OUT: esp back in w8
        None,                                         # 31  b RETURN_VA
    ]
    OUT = 30
    for i in (3, 8, 14, 21, 26):
        w[i] = A.bl(addr(i), XLATE)
    w[17] = A.bcond(addr(17), addr(OUT), A.NE)
    w[31] = A.b(addr(31), RETURN_VA)
    return w


N_WORDS = len(body_words_at(lambda i: 4 * i))


def build_patches(img, starts, log=print):
    """{va: word} for the cave and the hook, or None if it cannot be done."""
    img = bytearray(img)
    if w32(img, HOOK) != HOOK_STOCK:
        log('  ! hook +%#x is %08X, not `ldr w22,[x0]`' % (HOOK, w32(img, HOOK)))
        return None
    pool = ff7nx_cave.HolePool(img, starts=starts)
    entry, words = ff7nx_cave.emit_laid_out(pool, lambda _e, addr: body_words_at(addr))
    words[HOOK] = A.b(HOOK, entry)
    log('  16:9 timer position: %d words in padding, entry +%#x' % (N_WORDS, entry))
    log('    timer windows: left edge -%d (game x 24 -> -83), viewport full '
        'width' % MARGIN)
    return words


# ---------------------------------------------------------------------------
# module access
# ---------------------------------------------------------------------------
def resolve_main(path):
    if os.path.isdir(path):
        cand = os.path.join(path, SDOUT_MAIN)
        if not os.path.isfile(cand):
            raise SystemExit('timerpos: no %s under %s' % (SDOUT_MAIN, path))
        return cand
    return path


def _text(path):
    for seg in nso_patcher.read_nso(Path(path)).segments:
        if seg.name == '.text':
            return seg.data
    raise SystemExit('timerpos: no .text segment')


def w32(t, va):
    return struct.unpack_from('<I', t, va)[0]


def _fmt(word):
    return struct.pack('<I', word).hex(' ')


def _b_target(word, va):
    if (word & 0xFC000000) != 0x14000000:
        return None
    off = word & 0x3FFFFFF
    if off & 0x2000000:
        off -= 0x4000000
    return va + 4 * off


def check_anchors(t, log=print):
    bad = []
    for va, want, what in ANCHORS:
        got = w32(t, va)
        if got != want:
            bad.append(va)
            log('  ! anchor +%#x is %08X, want %08X  (%s)' % (va, got, want, what))
    return bad


def walk(t):
    """[(va, word)] of the installed cave in execution order, or None."""
    entry = _b_target(w32(t, HOOK), HOOK)
    if entry is None:
        return None
    out, va = [], entry
    for _ in range(4 * N_WORDS):
        word = w32(t, va)
        tgt = _b_target(word, va)
        if tgt == RETURN_VA:
            out.append((va, word))
            return out
        if tgt is not None:            # a chain link between padding runs
            va = tgt
            continue
        out.append((va, word))
        va += 4
    return None


def walk_physical(t):
    """Every padding word the cave occupies, chain links included."""
    entry = _b_target(w32(t, HOOK), HOOK)
    if entry is None:
        return []
    out, va = [], entry
    for _ in range(4 * N_WORDS):
        word = w32(t, va)
        out.append(va)
        tgt = _b_target(word, va)
        if tgt == RETURN_VA:
            return out
        if tgt is not None:
            va = tgt
            continue
        va += 4
    return []


def installed(t):
    return _b_target(w32(t, HOOK), HOOK) is not None


def cave_state(t):
    word = w32(t, HOOK)
    if word == HOOK_STOCK:
        return 'stock'
    if not installed(t):
        return 'foreign word %08X' % word
    wk = walk(t)
    if wk is None:
        return 'branch to an unwalkable cave'
    return 'installed (%d words)' % len(wk) if _matches(wk) else 'foreign cave'


def _matches(wk):
    if not wk or len(wk) != N_WORDS:
        return False
    vas = [va for va, _ in wk]
    return [w for _, w in wk] == body_words_at(lambda i: vas[i])


# ---------------------------------------------------------------------------
# verification -- the cave is EXECUTED, not read
# ---------------------------------------------------------------------------
EBP = 0x0019F000 - 0x40
ESP = 0x0019EF00
WIN_ARRAY = 0x00DC4A00
CASES = [
    # (name, special type, x1 in, width pushed, [0xDC3CB4] in) ->
    #        (x1 out, width out, left edge out)
    ('timer window (Reactor 1)', 1, 20, 320, 20, (0, FULL_W, 20 - MARGIN)),
    ('timer window, partly open', 1, 64, 120, 20, (0, FULL_W, 20 - MARGIN)),
    ('dialogue window', 0, 16, 506, 16, (16, 506, 16)),
    ('other special type', 2, 40, 200, 40, (40, 200, 40)),
]


def _run_cave(code, entry, special, x1, width, left, index=3, paged=True):
    import arm64emu
    mem = arm64emu.Mem()
    cpu = arm64emu.Cpu(mem, paged=paged)
    g = cpu.guest_to_host
    ctx = 0x5000_0000
    cpu.set(25, ctx)
    mem.setu(ctx + 0x10, ESP, 4)
    mem.setu(ctx + 0x14, EBP, 4)
    mem.setu(ctx + 0x08, 0x1234, 4)
    mem.setu(g(EBP + 8), WIN_ARRAY, 4)
    mem.setu(g(EBP - 0x1C), index, 2)
    mem.setu(g(EBP - 0x14), x1, 4)
    mem.setu(g(ESP + 4), width, 4)
    mem.setu(g(WIN_X), left, 4)
    for k in range(8):                 # every window's type, so a wrong
        mem.setu(g(WIN_ARRAY + k * WIN_SIZE + WIN_SPECIAL),   # index shows
                 special if k == index else 0x55, 1)
    cpu.set(0, g(EBP - 0x14))
    cpu.sp = 0x6000_0000
    out = cpu.run(entry, code)
    return {
        'exit': out,
        'x1': cpu.get(22, True),
        'w8': cpu.get(8, True),
        'width': mem.u(g(ESP + 4), 4),
        'left': arm64emu.s32(mem.u(g(WIN_X), 4)),
    }


def verify_words(code, entry, log=print):
    fails = []
    for name, special, x1, width, left, want in CASES:
        r = _run_cave(code, entry, special, x1, width, left)
        got = (r['x1'], r['width'], r['left'])
        if r['exit'] != RETURN_VA:
            fails.append('%s: left the cave at %#x' % (name, r['exit']))
        if r['w8'] != ESP:
            fails.append('%s: w8 is %#x on return, not esp' % (name, r['w8']))
        if got != want:
            fails.append('%s: got %s, want %s' % (name, got, want))
    return fails


def verify(t, log=print, verbose=True):
    fails = []
    if check_anchors(t, log):
        fails.append('anchors moved')
    if installed(t):
        wk = walk(t)
        if not _matches(wk or []):
            return fails + ['installed cave is not this version']
        code = {va: w32(t, va) for va in walk_physical(t)}
        fails += verify_words(code, _b_target(w32(t, HOOK), HOOK), log)
    elif w32(t, HOOK) != HOOK_STOCK:
        fails.append('hook +%#x holds %08X' % (HOOK, w32(t, HOOK)))
    return fails


def enabled(env=None) -> bool:
    """ON with 16:9, OFF at 4:3; SEVENTH_NX_TIMER_POS overrides for an A/B."""
    raw = env if env is not None else os.environ.get(TIMERPOS_ENV)
    if raw is not None:
        return str(raw).strip().lower() not in ('', '0', 'off', 'no', 'false')
    try:
        import ff7nx_ws
        return ff7nx_ws.enabled()
    except Exception:                                          # noqa: BLE001
        return False


def revert_patches(t, log=print):
    if not installed(t):
        return {}
    phys = walk_physical(t)
    if not phys or not _matches(walk(t) or []):
        log('  ! the timer cave cannot be walked; refusing to guess')
        return None
    out = {HOOK: HOOK_STOCK}
    for va in phys:
        out[va] = 0
    log('  16:9 timer position removed (%d word(s) of padding returned)'
        % len(phys))
    return out


def apply(main, revert=False, log=print) -> int:
    """0 on success (including "already in that state"), 1 if it refused."""
    import nxmap
    try:
        main = Path(resolve_main(str(main)))
        m = nxmap.Main(str(main))
        t = m.text
    except SystemExit as exc:
        log('  ! 16:9 timer position: %s' % exc)
        return 1
    if check_anchors(t, log):
        log('  refusing to write.')
        return 1
    if revert:
        words = revert_patches(t, log)
        if words is None:
            return 1
    elif installed(t):
        if _matches(walk(t) or []):
            log('  16:9 timer position: already installed')
            return 0
        log('  ! the timer hook branches to a cave that is not this version; '
            'refusing to replace it')
        return 1
    else:
        words = build_patches(m.img, set(m.arm_starts), log)
        if words is None:
            return 1
        fails = verify_words({va: w for va, w in words.items() if va != HOOK},
                             _b_target(words[HOOK], HOOK), log)
        if fails:
            for f in fails:
                log('  ! 16:9 timer position: ' + f)
            log('  refusing to write.')
            return 1
    if not words:
        log('  16:9 timer position: nothing to do')
        return 0

    patches = [{'name': 'ff7nx_timerpos +%#09X' % va,
                'va': hex(va),
                'expect': _fmt(w32(t, va)),
                'set': _fmt(word)}
               for va, word in sorted(words.items())]
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(nso, {'name': 'ff7nx_timerpos',
                                             'patches': patches}):
        log('    ' + line)
    blob = nso_patcher.rebuild(nso)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.timerpos-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(blob)
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  wrote %s  (%s)' % (main, hashlib.md5(blob).hexdigest()))

    t2 = _text(str(main))
    if revert:
        if w32(t2, HOOK) != HOOK_STOCK:
            log('  ! revert did not restore the hook. DO NOT BOOT THIS.')
            return 1
        return 0
    fails = verify(t2, log=log, verbose=False)
    if fails:
        for f in fails:
            log('  ! ' + f)
        log('  ! the WRITTEN timer cave does not verify. DO NOT BOOT THIS.')
        return 1
    log('    the written cave executes correctly on all %d case(s)' % len(CASES))
    return 0


def show(main, log=print):
    t = _text(resolve_main(main))
    log('  +%#x  %s  hook  %s' % (HOOK, _fmt(w32(t, HOOK)), cave_state(t)))
    bad = check_anchors(t, log)
    log('  anchors: %s' % ('OK' if not bad else '%d FAILED' % len(bad)))
    return 1 if bad else 0


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
    print('  %s  %s' % (path, cave_state(t)))
    fails = verify(t)
    for f in fails:
        print('  ! ' + f)
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
