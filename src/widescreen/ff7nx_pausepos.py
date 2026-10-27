#!/usr/bin/env python3
"""
ff7nx_pausepos.py -- the battle "Pause" window, vertically centred.

    python3 ff7nx_pausepos.py <exefs/main | sdout>            (verify)
    python3 ff7nx_pausepos.py <exefs/main | sdout> --apply
    python3 ff7nx_pausepos.py <exefs/main | sdout> --revert


WHAT IS WRONG
=============
Pausing a battle opens a small "Pause" window that sits too high in the space
it belongs to: the gap between the top message window (Comet, ...) and the
bottom command/status windows. On Patrick's 1280x720 capture:

    top window, last border row         y  78
    bottom windows, first border row    y 577      gap centre 327.5
    Pause box rows 243..296                        box centre 269.5

so it belongs 58 px lower. (Build 737's first try centred it on the whole
screen, 360, which Patrick found sat too close to the bottom windows.)

WHERE THE POSITION COMES FROM
=============================
battle_menu_update (x86 0x6CE8B3) draws it while [0xDC0E6C] (the latched
g_is_battle_paused) is set, through one of two functions picked by
[0xDC130C], the same switch that picks the 640- or 320-space battle menu:

    0x6D84C4  640 space   text  (0x140 + 8 - w/2, 0xB2)          ARM64 +0xD5E240
                          box   (0x140 - 4 - w/2, 0xA6, w+0x14, 0x30)
    0x6D864C  320 space   text  (.., 0x59)                        ARM64 +0xD5E4E0
                          box   (.., 0x53, .., 0x18)

Each y is a plain `mov w8, #imm` stored to the guest stack as the pushed
argument. Both functions land on the same pixels (178 * 1.5 = 89 * 3 = 267),
so both are moved down by 58 px worth: 39 units in 640 space (1.5 px each,
58.5 px), 19 in 320 (3 px each, 57 px) -- each within a pixel of the centre.
x, width and height are untouched.

Four in-place words, no cave.

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
import nso_patcher                                             # noqa: E402

PAUSEPOS_ENV = 'SEVENTH_NX_PAUSE_CENTER'
SDOUT_MAIN = os.path.join('atmosphere', 'contents', '0100A5B00BDC6000',
                          'exefs', 'main')

# measured on Patrick's 1280x720 captures
GAP_TOP_PX = 78                  # top message window, last border row
GAP_BOTTOM_PX = 577              # bottom windows, first border row
BOX_CENTRE_PX = 269.5            # Pause box rows 243..296
TARGET_PX = (GAP_TOP_PX + GAP_BOTTOM_PX) / 2                      # 327.5
SHIFT_640 = int(round((TARGET_PX - BOX_CENTRE_PX) / 1.5))         # 39
SHIFT_320 = int(round((TARGET_PX - BOX_CENTRE_PX) / 3))           # 19


def movz_w8(imm):
    return 0x52800008 | (imm << 5)


# (va, stock y, shift, what)
SITES = [
    (0xD5E324, 0xB2, SHIFT_640, '640: text y'),
    (0xD5E3D8, 0xA6, SHIFT_640, '640: box y'),
    (0xD5E5C4, 0x59, SHIFT_320, '320: text y'),
    (0xD5E678, 0x53, SHIFT_320, '320: box y'),
]

# Words the reasoning rests on, stock in both states.
ANCHORS = [
    (0xD5E328, 0xB9000008, 'str w8, [x0]             push text y'),
    (0xD5E34C, 0x52802908, 'mov w8, #0x148           0x140 + 8: text x'),
    (0xD5E380, 0x940E16AC, 'bl                       the text draw'),
    (0xD5E394, 0x321C07E8, 'mov w8, #0x30            box height'),
    (0xD5E3DC, 0xB9000008, 'str w8, [x0]             push box y'),
    (0xD5E400, 0x52802788, 'mov w8, #0x13c           0x140 - 4: box x'),
    (0xD5E450, 0x97FCF3A8, 'bl                       the box draw'),
    (0xD5E634, 0x321D07E8, 'mov w8, #0x18            320: box height'),
]


def w32(t, va):
    return struct.unpack_from('<I', t, va)[0]


def _fmt(word):
    return struct.pack('<I', word).hex(' ')


def check_anchors(t, log=print):
    bad = []
    for va, want, what in ANCHORS:
        if w32(t, va) != want:
            bad.append(va)
            log('  ! anchor +%#x is %08X, want %08X  (%s)'
                % (va, w32(t, va), want, what))
    return bad


def state(t):
    """'stock', 'installed', or None for anything else."""
    got = [w32(t, va) for va, _, _, _ in SITES]
    if got == [movz_w8(y) for _, y, _, _ in SITES]:
        return 'stock'
    if got == [movz_w8(y + d) for _, y, d, _ in SITES]:
        return 'installed'
    return None


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(PAUSEPOS_ENV, '1').strip().lower() not in (
        '0', 'false', 'no', 'off')


def resolve_main(target):
    p = Path(target)
    if p.is_dir():
        p = p / SDOUT_MAIN
    if not p.is_file():
        raise SystemExit('no exefs/main at %s' % p)
    return p


def _text(path):
    for seg in nso_patcher.read_nso(Path(path)).segments:
        if seg.name == '.text':
            return seg.data
    raise SystemExit('pausepos: no .text segment')


def apply(main, revert=False, log=print):
    """0 on success (including "already in that state"), 1 if it refused."""
    try:
        main = resolve_main(str(main))
        t = _text(main)
    except SystemExit as exc:
        log('  ! battle pause window: %s' % exc)
        return 1
    if check_anchors(t, log):
        log('  refusing to write.')
        return 1
    st = state(t)
    want = 'stock' if revert else 'installed'
    if st == want:
        log('  battle pause window: already %s' % st)
        return 0
    if st is None:
        log('  ! battle pause window: the y words are neither stock nor ours; '
            'refusing')
        return 1
    patches = []
    for va, y, d, what in SITES:
        new = y if revert else y + d
        patches.append({'name': 'ff7nx_pausepos %s' % what, 'va': hex(va),
                        'expect': _fmt(w32(t, va)), 'set': _fmt(movz_w8(new))})
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(nso, {'name': 'ff7nx_pausepos',
                                             'patches': patches}):
        log('    ' + line)
    blob = nso_patcher.rebuild(nso)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.pausepos-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(blob)
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    if state(_text(main)) != want:
        log('  ! the written pause window words do not read back. '
            'DO NOT BOOT THIS.')
        return 1
    log('  battle pause window: %s (y +%d in 640 space, +%d in 320; centred '
        'between the top and bottom windows)  %s'
        % ('reverted' if revert else 'moved', SHIFT_640, SHIFT_320,
           hashlib.md5(blob).hexdigest()))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('target', help='exefs/main, or an sdout directory')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--apply', action='store_true')
    g.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    if a.apply or a.revert:
        sys.exit(apply(a.target, revert=a.revert))
    t = _text(resolve_main(a.target))
    bad = check_anchors(t)
    print('  %s' % (state(t) or 'UNKNOWN'))
    sys.exit(1 if bad or state(t) is None else 0)


if __name__ == '__main__':
    main()
