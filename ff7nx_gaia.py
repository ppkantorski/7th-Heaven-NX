#!/usr/bin/env python3
"""Cosmos Gaia's native-world texture-coordinate correction.

The stock world mesh stores UVs in *native texture pixels*.  The translated
renderer subtracts the texture's atlas origin and multiplies the result by
the loaded graphics object's inverse width/height.  Replacing a 64px TEX by a
256px TEX without changing that calculation samples only 0..63/256 -- the
upper-left quarter -- and stretches it across the polygon.  That is the
blocky, square, discontinuous terrain seen in builds 178 and 180.

``build._convert_world_dds`` now emits every Gaia replacement at one uniform
factor relative to its native TEX.  This pass multiplies the three U and three
V results by that same factor, restoring the original 0..1 coverage while the
GPU samples the higher-resolution image.  It is gated by an internal env var
set only when the active build plan contains Gaia DDS replacements; vanilla
and every non-Gaia world mod remain byte-for-byte outside this pass.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

import a64 as A
import ff7nx_cave
import nso_patcher
import nxmap


SCALE_ENV = 'SEVENTH_NX_WORLD_GAIA_SCALE'

# fmul emitted by world_submit_draw_bg_meshes_75F68C for U0,V0,U1,V1,U2,V2.
# The destination alternates d0/d3; the following store consumes that exact
# register.  Each original word is fingerprinted before a hook is emitted.
SITES = (
    (0x00F5BA20, 0x1E600820, 0),
    (0x00F5BB48, 0x1E600823, 3),
    (0x00F5BC6C, 0x1E600820, 0),
    (0x00F5BD94, 0x1E600823, 3),
    (0x00F5BEB8, 0x1E600820, 0),
    (0x00F5BFE0, 0x1E600823, 3),
)

NOP = 0xD503201F


def scale():
    raw = os.environ.get(SCALE_ENV, '').strip()
    try:
        value = float(raw)
    except ValueError:
        return 1.0
    return value if value in (0.5, 1.0, 2.0, 3.0, 4.0) else 1.0


def enabled():
    return scale() != 1.0


def _hex(word):
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


def _fadd_d(rd, rn, rm):
    return 0x1E602800 | (rm << 16) | (rn << 5) | rd


def _fmul_d(rd, rn, rm):
    return 0x1E600800 | (rm << 16) | (rn << 5) | rd


def _scale_words(dest, factor):
    """Scale Ddest without touching integer registers or flags."""
    if factor == 0.5:
        return [0x1E6C101F,                  # fmov d31, #0.5
                _fmul_d(dest, dest, 31)]
    if factor == 2.0:
        return [_fadd_d(dest, dest, dest)]
    if factor == 3.0:
        return [_fadd_d(31, dest, dest),     # d31 = 2*x
                _fadd_d(dest, 31, dest)]     # dest = 2*x + x
    if factor == 4.0:
        return [_fadd_d(dest, dest, dest),
                _fadd_d(dest, dest, dest)]
    return []


def patch_words(main, log=lambda *_: None):
    """Return all verified hook/cave words for the active uniform factor."""
    factor = scale()
    if factor == 1.0:
        return {}
    for va, expect, _ in SITES:
        got = struct.unpack_from('<I', main.img, va)[0]
        if got != expect:
            raise ValueError('Gaia UV site +0x%X expected %08X, found %08X'
                             % (va, expect, got))

    pool = ff7nx_cave.HolePool(main.img, starts=set(main.arm_starts))
    out = {}
    for va, original, dest in SITES:
        # emit_hooked normally places the displaced word after the body.  The
        # multiply must happen first, so include it explicitly and use a NOP
        # as the harmless displaced tail.
        words, entry = ff7nx_cave.emit_hooked(
            pool, va, NOP, [original] + _scale_words(dest, factor))
        out.update(words)
        log('  Gaia terrain UV +0x%X: %sx cave entry +0x%X'
            % (va, factor, entry))
    return out


def apply_to_nso(src, dest, log=lambda *_: None):
    try:
        main = nxmap.Main(src)
        nso = nso_patcher.read_nso(Path(src))
        words = patch_words(main, log)
        if not words:
            return False
        applied = nso_patcher.apply_spec(nso, {
            'name': 'Cosmos Gaia native world UV normalisation',
            'patches': [
                {'name': ('terrain UV hook' if va in {s[0] for s in SITES}
                          else 'terrain UV cave word'),
                 'va': va,
                 'expect': _hex(struct.unpack_from('<I', main.img, va)[0]),
                 'set': _hex(word)}
                for va, word in sorted(words.items())],
        })
        data = nso_patcher.rebuild(nso)
    except Exception as exc:                                  # noqa: BLE001
        log('! Gaia world UV correction: %s' % exc)
        log('  nothing was written; the module is unchanged')
        return False
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(data)
    for line in applied:
        log('  ' + line)
    return True

