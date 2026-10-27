#!/usr/bin/env python3
"""
ff7nx_sfxcenter.py -- Cosmo Memory's "Center Battle SFX", on the Switch.

    python3 ff7nx_sfxcenter.py <exefs/main | sdout>            (verify)
    python3 ff7nx_sfxcenter.py <exefs/main | sdout> --apply
    python3 ff7nx_sfxcenter.py <exefs/main | sdout> --revert


WHAT WAS MISSING
================
Cosmo Memory's `Center` option (on by default) is not a folder. It is a line
in the mod's <FFNxConfig> block:

    <external_sfx_always_centered Center="1">true</external_sfx_always_centered>

7th Heaven writes that key into FFNx.toml when the option is 1, and FFNx then
plays every sound effect at pan 0 (`NxAudioEngine::playSFX` and
`setSFXPanning`, FFNx src/audio.cpp: "panning overridden because of
external_sfx_always_centered"). The mod's stereo recordings are made to be
heard that way. This port never read <FFNxConfig>, so the option did nothing
and the game's left/right placement stayed on.

WHERE THE PAN GOES
==================
Every SFX pan in the PC game ends in one wrapper, x86 0x74A82A
(dsound_set_pan: range-check -10000..10000, then IDirectSoundBuffer::SetPan).
Its eight callers are all SFX: sfx_set_panning (0x744B13, twice),
play_sfx_on_channel (0x745606), the channel-pan op (0x747F99, twice),
sfx_load_and_play_with_speed (0x748D69, twice) and the channel-6 effect
(0x749257). Music, movies, voice and Cosmo's ambience never come here: they
play through the port's own NativeOggPlayer / mp4 paths.

On the Switch the port's DirectSound shim is a table of native functions
(gfx_drv_table index 126); SetPan is

    +0x10D4630  str  x19, [sp, #-0x20]!
                ...
    +0x10D4644  mov  w19, w1        <- the pan argument
                mov  w1, w0
                ldr  x0, [x8]
                bl   handle -> buffer
                mov  w1, w19
                bl   +0x1580        stores the pan (as w1 * 1/10000) on the voice

`+0x1580` is called from nowhere else. Changing the one `mov w19, w1` to
`mov w19, wzr` hands every SFX buffer pan 0 -- DSBPAN_CENTER -- which is
exactly what FFNx does. Volume, frequency and looping are untouched, and the
shim table has no GetPan for anything to read the pan back through.

One in-place word, no cave, no BSS.

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

# FFNx's key, as Cosmo Memory's <FFNxConfig> names it.
FFNX_KEY = 'external_sfx_always_centered'
# Forces the patch on (1) or off (0) whatever the mod options say; unset
# follows them.
SFXCENTER_ENV = 'SEVENTH_NX_SFX_CENTER'
SDOUT_MAIN = os.path.join('atmosphere', 'contents', '0100A5B00BDC6000',
                          'exefs', 'main')

SITE = 0x10D4644
STOCK = 0x2A0103F3               # mov w19, w1
CENTRED = 0x2A1F03F3             # mov w19, wzr

# Words the reasoning rests on, stock in both states.
ANCHORS = [
    (0x10D4630, 0xF81E0FF3, 'str x19, [sp, #-0x20]!   SetPan entry'),
    (0x10D4648, 0x2A0003E1, 'mov w1, w0               the buffer handle'),
    (0x10D4650, 0x97BCB204, 'bl  +0xE60               handle -> buffer'),
    (0x10D4654, 0x2A1303E1, 'mov w1, w19              pan for the setter'),
    (0x10D4658, 0x97BCB3CA, 'bl  +0x1580              the pan setter'),
    (0x10D4660, 0x2A1F03E0, 'mov w0, wzr              DS_OK'),
]


def w32(t, va):
    return struct.unpack_from('<I', t, va)[0]


def _fmt(word):
    return struct.pack('<I', word).hex(' ')


def wanted(ffnx_flags, env=None):
    """Whether to centre: the env override, else the mods' FFNx flag."""
    env = os.environ if env is None else env
    force = env.get(SFXCENTER_ENV, '').strip().lower()
    if force in ('1', 'true', 'yes', 'on'):
        return True
    if force in ('0', 'false', 'no', 'off'):
        return False
    value = (ffnx_flags or {}).get(FFNX_KEY, '')
    return str(value).strip().lower() == 'true'


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
    return {STOCK: 'stock', CENTRED: 'installed'}.get(w32(t, SITE))


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
    raise SystemExit('sfxcenter: no .text segment')


def apply(main, revert=False, log=print):
    """0 on success (including "already in that state"), 1 if it refused."""
    try:
        main = resolve_main(str(main))
        t = _text(main)
    except SystemExit as exc:
        log('  ! centred SFX: %s' % exc)
        return 1
    if check_anchors(t, log):
        log('  refusing to write.')
        return 1
    st = state(t)
    want = 'stock' if revert else 'installed'
    if st == want:
        log('  centred SFX: already %s' % st)
        return 0
    if st is None:
        log('  ! centred SFX: the SetPan word is neither stock nor ours; '
            'refusing')
        return 1
    nso = nso_patcher.read_nso(main)
    spec = {'name': 'ff7nx_sfxcenter', 'patches': [{
        'name': 'ff7nx_sfxcenter SetPan pan -> 0', 'va': hex(SITE),
        'expect': _fmt(w32(t, SITE)),
        'set': _fmt(STOCK if revert else CENTRED)}]}
    for line in nso_patcher.apply_spec(nso, spec):
        log('    ' + line)
    blob = nso_patcher.rebuild(nso)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.sfxcenter-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(blob)
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    if state(_text(main)) != want:
        log('  ! the written SetPan word does not read back. DO NOT BOOT THIS.')
        return 1
    log('  centred SFX: %s (every SFX buffer at pan 0, as FFNx\'s %s)  %s'
        % ('reverted' if revert else 'on', FFNX_KEY,
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
    print('anchors: %s' % ('ok' if not bad else '%d bad' % len(bad)))
    print('state  : %s' % (state(t) or 'unknown'))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
