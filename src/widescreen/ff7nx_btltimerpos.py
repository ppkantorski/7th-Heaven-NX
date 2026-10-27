#!/usr/bin/env python3
"""
ff7nx_btltimerpos.py -- BUILD 731: the BATTLE countdown timer, moved out to
the 16:9 edge the way ff7nx_timerpos moved the field one.

    python3 ff7nx_btltimerpos.py <exefs/main | sdout>            --verify
    python3 ff7nx_btltimerpos.py <exefs/main | sdout> --apply
    python3 ff7nx_btltimerpos.py <exefs/main | sdout> --revert


WHAT IS WRONG
=============
While the countdown runs, battle draws it with the field timer's twin, x86
0x6DB7AB (ARM64 +0xD6E330; the same digit code as 0x6EB793, found by
instruction-shape match). Its only caller is battle code, x86 0x42D808,
gated on bit 1 of [0x9A88A6]:

    push [0xDC08BC]           +0xD3DF0   the time
    push 16                   +0xD3E08   y
    push 16                   +0xD3E1C   x
    push ctx + 0x40E4         +0xD3E48
    call 0x6DB7AB             +0xD3E58

x and y are in the battle overlay's 320x240 space -- NOT the field's 640
space: the digit routine steps x by 16 per digit, and Patrick's capture
shows a 48 px digit pitch at 1280x720, so 3 px a unit, with game x 0 at
device x 160 (the 4:3 picture centred). It draws the digits at y - 8, so the
digit tops are 3 * (16 - 8) = 24 px down, and the first digit's left edge is
160 + 3 * 16 = 208 px in.

BUILD 731 used the field's 640-space margin (x 16 - 107 = -91) and put the
first digit at 160 - 273 = -113 px: the minutes and the colon off screen,
the seconds at 7..103 px -- exactly what the capture shows ("18" at 29..100).

THE FIX (BUILD 732)
===================
Push x = round((24 - 160) / 3) = -45 instead: the first digit's left edge at
160 - 135 = 25 px, against the 24 px top. y is not touched. The digit routine draws sprites only (it
sets no viewport; the field timer's clip problem has no battle twin), and it
takes x as a float, so a negative x is just a position.

Both pushes store the same register (w21 = 16), so x needs its own value:
a three-word cave at the x store.

     movn  w9, #44            w9 = -45       (w9 is reloaded at +0xD3E28)
     str   w9, [x0]           the pushed x
     b     +0xD3E20

16:9 ONLY, gated by ff7nx_timerpos.enabled() so both timers move together.

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
import ff7nx_timerpos                                          # noqa: E402

HOOK = 0xD3E1C                     # str w21, [x0]      the pushed x
HOOK_STOCK = 0xB9000015
RETURN_VA = 0xD3E20

STOCK_X = 16
STOCK_Y = 16
PX_PER_UNIT = 3                    # 720 / 240: the battle overlay's 320 space
LEFT_4x3 = 160                     # device x of overlay x 0 at 1280x720
TOP_PX = PX_PER_UNIT * (STOCK_Y - 8)                     # 24: digits draw at y-8
NEW_X = int(round((TOP_PX - LEFT_4x3) / PX_PER_UNIT))    # -45

ANCHORS = [
    (0xD3DA0, 0x36080648, 'tbz w8, #1, ...          timer shown?'),
    (0xD3E04, 0x321C03F5, 'mov w21, #0x10           16'),
    (0xD3E08, 0xB9000015, 'str w21, [x0]            push y'),
    (0xD3E18, 0x9440A162, 'bl +0x10FC3A0            &x'),
    (0xD3E20, 0x1100F280, 'add w0, w20, #0x3c'),
    (0xD3E28, 0xB9400009, 'ldr w9, [x0]             w9 dead before'),
    (0xD3E58, 0x94326936, 'bl +0xD6E330             the timer draw'),
]


def movn(rd, imm16):
    return 0x12800000 | (imm16 << 5) | rd


def body_words_at(addr):
    return [
        movn(9, -NEW_X - 1),                          # w9 = NEW_X
        A.str_(9, 0),                                 # pushed x
        A.b(addr(2), RETURN_VA),
    ]


N_WORDS = len(body_words_at(lambda i: 4 * i))


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
    """[(va, word)] of the installed cave, chain links skipped, or None."""
    entry = _b_target(w32(t, HOOK), HOOK)
    if entry is None:
        return None
    out, phys, va = [], [], entry
    for _ in range(4 * N_WORDS + 4):
        word = w32(t, va)
        phys.append(va)
        tgt = _b_target(word, va)
        if tgt == RETURN_VA:
            out.append((va, word))
            return out, phys
        if tgt is not None:
            va = tgt
            continue
        out.append((va, word))
        va += 4
    return None


def _matches(wk):
    if not wk or len(wk[0]) != N_WORDS:
        return False
    vas = [va for va, _ in wk[0]]
    return [w for _, w in wk[0]] == body_words_at(lambda i: vas[i])


def installed(t):
    return _b_target(w32(t, HOOK), HOOK) is not None


def build_patches(img, starts, log=print):
    if w32(img, HOOK) != HOOK_STOCK:
        log('  ! hook +%#x is %08X, not `str w21,[x0]`' % (HOOK, w32(img, HOOK)))
        return None
    pool = ff7nx_cave.HolePool(bytearray(img), starts=starts)
    entry, words = ff7nx_cave.emit_laid_out(
        pool, lambda _e, addr: body_words_at(addr))
    words[HOOK] = A.b(HOOK, entry)
    log('  16:9 battle timer position: %d words in padding, entry +%#x'
        % (N_WORDS, entry))
    log('    battle timer x %d -> %d (%d px from the left, %d from the top)'
        % (STOCK_X, NEW_X, LEFT_4x3 + PX_PER_UNIT * NEW_X, TOP_PX))
    return words


GUEST_X = 0x0019EEF0               # where the translated push lands


def run_cave(code, entry):
    """Execute the cave; returns (exit va, the stored x as s32)."""
    import arm64emu
    mem = arm64emu.Mem()
    cpu = arm64emu.Cpu(mem, paged=True)
    host = cpu.guest_to_host(GUEST_X)
    mem.setu(host, 0x55555555, 4)
    cpu.set(0, host)
    cpu.set(21, STOCK_X)
    cpu.set(9, 0x1234)
    cpu.sp = 0x6000_0000
    out = cpu.run(entry, code)
    return out, arm64emu.s32(mem.u(host, 4)), cpu.get(21, True)


def verify_words(code, entry):
    out, x, w21 = run_cave(code, entry)
    fails = []
    if out != RETURN_VA:
        fails.append('left the cave at %#x' % out)
    if x != NEW_X:
        fails.append('pushed x is %d, want %d' % (x, NEW_X))
    if w21 != STOCK_X:
        fails.append('w21 changed (%d)' % w21)
    return fails


def verify(t, log=print):
    fails = []
    if check_anchors(t, log):
        fails.append('anchors moved')
    if installed(t):
        wk = walk(t)
        if not _matches(wk):
            return fails + ['installed cave is not this version']
        code = {va: w32(t, va) for va in wk[1]}
        fails += verify_words(code, _b_target(w32(t, HOOK), HOOK))
    elif w32(t, HOOK) != HOOK_STOCK:
        fails.append('hook +%#x holds %08X' % (HOOK, w32(t, HOOK)))
    return fails


def enabled(env=None):
    return ff7nx_timerpos.enabled(env)


def _text(path):
    for seg in nso_patcher.read_nso(Path(path)).segments:
        if seg.name == '.text':
            return seg.data
    raise SystemExit('btltimerpos: no .text segment')


def apply(main, revert=False, log=print):
    """0 on success (including "already in that state"), 1 if it refused."""
    import nxmap
    try:
        main = Path(ff7nx_timerpos.resolve_main(str(main)))
        m = nxmap.Main(str(main))
        t = m.text
    except SystemExit as exc:
        log('  ! 16:9 battle timer position: %s' % exc)
        return 1
    if check_anchors(t, log):
        log('  refusing to write.')
        return 1
    if revert:
        if not installed(t):
            return 0
        wk = walk(t)
        if not _matches(wk):
            log('  ! the battle timer cave cannot be walked; refusing to guess')
            return 1
        words = {HOOK: HOOK_STOCK}
        words.update({va: 0 for va in wk[1]})
    elif installed(t):
        if _matches(walk(t)):
            log('  16:9 battle timer position: already installed')
            return 0
        log('  ! the battle timer hook branches to a foreign cave; refusing')
        return 1
    else:
        words = build_patches(m.img, set(m.arm_starts), log)
        if words is None:
            return 1
        fails = verify_words({va: w for va, w in words.items() if va != HOOK},
                             _b_target(words[HOOK], HOOK))
        if fails:
            for f in fails:
                log('  ! 16:9 battle timer position: ' + f)
            log('  refusing to write.')
            return 1
    patches = [{'name': 'ff7nx_btltimerpos +%#09X' % va, 'va': hex(va),
                'expect': _fmt(w32(t, va)), 'set': _fmt(word)}
               for va, word in sorted(words.items())]
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(nso, {'name': 'ff7nx_btltimerpos',
                                             'patches': patches}):
        log('    ' + line)
    blob = nso_patcher.rebuild(nso)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.btltimerpos-')
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
    fails = verify(t2, log=log)
    if fails:
        for f in fails:
            log('  ! ' + f)
        log('  ! the WRITTEN battle timer cave does not verify. DO NOT BOOT THIS.')
        return 1
    log('    the written cave executes correctly')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('target', help='exefs/main, or an sdout directory')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--apply', action='store_true')
    g.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    path = ff7nx_timerpos.resolve_main(a.target)
    if a.apply or a.revert:
        sys.exit(apply(path, revert=a.revert))
    fails = verify(_text(path))
    for f in fails:
        print('  ! ' + f)
    print('  %s' % ('installed' if installed(_text(path)) else 'stock'))
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
