#!/usr/bin/env python3
r"""
ff7nx_summonhold.py -- the summon pause guard. OFF, AND WRONG.

BUILD 376. KEPT ONLY SO THE NEGATIVE RESULT IS REPRODUCIBLE.

WHAT I THOUGHT
==============
`effect100-throttle` forces `g_is_battle_paused` on for three rendered frames
in four, and every one of the 35 routines FFNx registers begins

    x86 0x597242   mov  al, byte ptr [0xdc0e6c]     g_is_battle_paused
        0x597247   test eax, eax
        0x597249   jne  <the tail>

so on those frames the camera is not aimed and the model is not placed. Since
`summon-runtime` already NOPs exactly that branch for Shiva and Odin gunge --
and Shiva is the one summon that does not flicker -- releasing the guard in the
routines that do flicker looked like the same proven edit.

WHAT HARDWARE SAID
==================
    "ramuh is all screwed up. the camera freezes, i cant even see the full
     animation ... camera is freezing at weird angles, the summons look
     screwed up"

Releasing the guard lets the routine advance its camera script on every
rendered frame instead of every fourth, so the script runs out four times too
early and the camera stops on whatever angle it ended on. **The guard is not
the bug. It is the pause mechanism working.**

WHAT THE REAL FIX IS
====================
I had not read `CameraInterpolationEffectDecorator::callEffectFunction` when I
wrote the first version of this file. It keeps the guard, and after every call
-- advancing or paused -- it WRITES the camera itself:

    advancing frame   call fn; next = *g_battle_camera_position;
                      then write the interpolated value
    paused frame      pause; call fn; unpause;
                      then write the interpolated value, or `previous` if the
                      move was larger than the threshold, or `next` on the
                      last step

The routine returning early is fine. What is missing is that **nothing
re-applies the summon camera on the paused frames**, so the default battle
camera -- which runs every frame -- is what the viewer sees. That is the
strobe: summon camera, default camera, summon camera, default camera. The
model case is the same with `g_battle_model_state[3].modelPosition`.

The three addresses that fix needs are now established, each confirmed twice:

    g_battle_camera_position      guest 0xBF2158    vector3<short>
    g_battle_camera_focal_point   guest 0xBFB1A0    vector3<short>
        both read out of ff7_en at set_battle_camera_sub_5C22BD +0x17/+0x5E,
        and both appear as literals in the Ramuh camera's own ARM prologue
        (+0x714844 and +0x714854)

    g_battle_model_state          guest 0xBE1178, stride 0x1AEC,
                                  modelPosition at +0x166

The work is in the throttle's post-call cave, which already knows whether it
paused this frame (it stores that in `did`): on an advancing frame save the
camera pair, on a paused frame write it back. That is a HOLD, which is all the
strobe needs; interpolation on top of it is what makes the motion smooth.

Everything below is left intact and OFF. `SEVENTH_NX_FX_SUMMONHOLD=default`
re-arms the seven sites and `=all` all 35, for anyone who wants to reproduce
the freeze rather than take my word for it.
"""
import argparse
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import nxmap                                                   # noqa: E402

HOLD_ENV = 'SEVENTH_NX_FX_SUMMONHOLD'

NOP = 0xD503201F
PAUSED_GUEST = 0xDC0E6C            # g_is_battle_paused

# (name, site, stock, patched, the four words before + the one after)
# The context is the compiled `g_is_battle_paused` test; only the base
# register differs between sites, which is what makes it a signature.
DEFAULT_SITES = (
    ('ramuh_camera', 0x00714958, 0x34000288, 0x14000014,      # cbz -> b
     (0xB94002E8, 0x7100011F, 0x1A9F17E9, 0x39008EE9, 0xB94016E0)),
    ('phoenix_camera', 0x004C95B0, 0x34001148, 0x1400008A,
     (0xB9400308, 0x7100011F, 0x1A9F17E9, 0x39008F09, 0xB9401708)),
    ('chocomog_camera', 0x00498320, 0x34001048, 0x14000082,
     (0xB94002E8, 0x7100011F, 0x1A9F17E9, 0x39008EE9, 0xB94016E8)),
    ('fat_chocobo_camera', 0x004903E0, 0x34001148, 0x1400008A,
     (0xB94002E8, 0x7100011F, 0x1A9F17E9, 0x39008EE9, 0xB94016E8)),
    ('chocomog_move', 0x0049E080, 0x35001B68, NOP,            # cbnz -> nop
     (0xB94002C8, 0x7100011F, 0x1A9F17E9, 0x39008EC9, 0xB94016C8)),
    ('fat_chocobo_move', 0x00496D1C, 0x35000728, NOP,
     (0xB9400688, 0x7100011F, 0x1A9F17E9, 0x39008E89, 0x528C7493)),
    ('phoenix_move', 0x004D821C, 0x35001508, NOP,
     (0xB94002A8, 0x7100011F, 0x1A9F17E9, 0x39008EA9, 0xB94016A8)),
)

# Owned by `summon-runtime`; anchored so the two modules can never both claim
# a word and so a change to either is caught here.
FOREIGN_GUARDS = {
    0x006EF7B8: (0x3501DA68, 'run_shiva_camera_58E60D'),
    0x002C7C48: (0x3501D828, 'run_odin_gunge_camera_4A0F52'),
}

# The remaining guards, for `=all`. Every one was found by the same scan.
EXTRA_SITES = (
    ('ifrit_camera', 0x00701A20), ('alexander_camera', 0x00475C00),
    ('bahamut_camera', 0x0029CDA0), ('titan_camera', 0x00728C30),
    ('hades_camera', 0x00324A38), ('leviathan_camera', 0x0078DC40),
    ('odin_steel_camera', 0x002DC118), ('bahamut_neo_camera', 0x0026CF88),
    ('kujata_camera', 0x00454140), ('typhoon_camera', 0x003B37E0),
    ('bahamut_zero_camera', 0x002474B8), ('kotr_camera', 0x002102E0),
    ('barret_L4_camera', 0x001D28B0), ('aerith_L4_camera', 0x00203FC0),
    ('enemyatk_cam_439EE0', 0x00104D50), ('enemyatk_cam_44A7D2', 0x0014D8B8),
    ('enemyatk_cam_44EDC0', 0x00160400), ('enemyatk_cam_4522AD', 0x0016E938),
    ('enemyatk_cam_457C60', 0x001870C0),
    ('bahamut_move', 0x002AAA48), ('bahamut_neo_move', 0x002710C0),
    ('odin_gunge_move', 0x002DAA9C), ('odin_steel_move', 0x002E0264),
    ('bahamut_zero_move', 0x0026A2A8), ('shiva_move', 0x007003B8),
    ('alexander_move', 0x0048F274),
)


def _b(pc, target):
    """B <target>, the unconditional form of the same branch."""
    rel = (target - pc) >> 2
    assert -(1 << 25) <= rel < (1 << 25), (pc, target)
    return 0x14000000 | (rel & 0x3FFFFFF)


def _cb_target(word, pc):
    imm = (word >> 5) & 0x7FFFF
    if imm & (1 << 18):
        imm -= (1 << 19)
    return pc + imm * 4


def _release(word, pc):
    """The word that makes the body run whether paused or not."""
    kind = word & 0xFF000000
    if kind == 0x35000000:             # cbnz wN, tail -- jump over the body
        return NOP
    if kind == 0x34000000:             # cbz wN, body -- fall through to tail
        return _b(pc, _cb_target(word, pc))
    raise ValueError('+0x%X is %08X, not a cbz/cbnz' % (pc, word))


for _n, _s, _stock, _want, _ctx in DEFAULT_SITES:
    assert _release(_stock, _s) == _want, (_n, hex(_want))


def mode() -> str:
    # BUILD 376: OFF. Executed on hardware and it is WRONG. Releasing the
    # guard lets the routine advance its camera script on every rendered
    # frame instead of every fourth, so the script runs out four times too
    # early and the camera stops at whatever angle it ended on:
    #
    #   "ramuh is all screwed up. the camera freezes, i cant even see the
    #    full animation ... camera is freezing at weird angles"
    #
    # The guard is not a bug. It is the pause mechanism working, and the
    # thing that is missing is the other half -- FFNx supplies the camera and
    # model state itself on the paused frames instead of letting the routine
    # run. Nothing here should be enabled until that half exists.
    v = os.environ.get(HOLD_ENV)
    if v is None:
        return 'stock'
    v = v.strip().lower()
    if v in ('0', 'stock', 'off', 'no', 'false', ''):
        return 'stock'
    if v == 'all':
        return 'all'
    return 'default'


def enabled() -> bool:
    return mode() != 'stock'


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def sites(img):
    """(name, site, stock, patched) for the selected mode."""
    out = [(n, s, st, w) for n, s, st, w, _c in DEFAULT_SITES]
    if mode() == 'all':
        for n, s in EXTRA_SITES:
            st = _word(img, s)
            if st in (NOP,) or (st & 0xFF000000) not in (0x34000000,
                                                         0x35000000):
                continue                      # already released, or not a guard
            out.append((n, s, st, _release(st, s)))
    return out


def verify(img) -> list:
    bad = []
    for name, site, stock, want, ctx in DEFAULT_SITES:
        got = _word(img, site)
        if got not in (stock, want):
            bad.append('%s: +0x%X is %08X, neither the stock guard nor its '
                       'release' % (name, site, got))
        for off, w in zip((-16, -12, -8, -4, 4), ctx):
            have = _word(img, site + off)
            if have != w:
                bad.append('%s: +0x%X is %08X, expected %08X (the '
                           'g_is_battle_paused test)'
                           % (name, site + off, have, w))
    for va, (stock, who) in sorted(FOREIGN_GUARDS.items()):
        got = _word(img, va)
        if got not in (stock, NOP):
            bad.append('+0x%X is %08X, neither stock nor NOP -- %s is owned '
                       'by summon-runtime' % (va, got, who))
    return bad


def read_state(img) -> str:
    n = sum(1 for _n, s, st, w, _c in DEFAULT_SITES if _word(img, s) == w)
    return 'summon pause guards: %d of %d released' % (n, len(DEFAULT_SITES))


def plan(m, revert=False):
    img = m.img
    problems = verify(img)
    if problems:
        return [], [], problems
    release = enabled() and not revert
    patches = []
    for name, site, stock, want in sites(img):
        have = _word(img, site)
        target = want if release else stock
        if have == target:
            continue
        patches.append({'name': 'summon pause guard (%s)' % name,
                        'va': hex(site),
                        'expect': struct.pack('<I', have).hex(),
                        'set': struct.pack('<I', target).hex()})
    notes = []
    if patches:
        notes.append('    %s the `if (g_is_battle_paused) return` guard in '
                     '%d routines (mode %s)'
                     % ('releasing' if release else 'restoring',
                        len(patches), mode()))
        notes.append('    the same edit summon-runtime already makes for '
                     'Shiva and Odin gunge')
    return patches, notes, []


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'summon pause guards', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.summonhold-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def apply_all(main, revert=False, log=print):
    m = nxmap.Main(str(main))
    patches, notes, problems = plan(m, revert=revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to touch the summon pause guards.')
        return 1
    log('  summon pause guards (%s=%s):' % (HOLD_ENV, mode()))
    for n in notes:
        log(n)
    if not patches:
        log('    already %s' % read_state(m.img))
        return 0
    _write(main, patches, log)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--state', action='store_true')
    a = ap.parse_args(argv)
    if a.state:
        m = nxmap.Main(a.main)
        print(read_state(m.img))
        for p in verify(m.img):
            print('  ! ' + p)
        return 0
    return apply_all(a.main, revert=a.revert)


if __name__ == '__main__':
    sys.exit(main())
