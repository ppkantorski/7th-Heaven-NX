#!/usr/bin/env python3
"""Cosmos Gaia's native-world texture-coordinate correction.

The stock world mesh stores UVs in *native texture pixels*.  The translated
renderer subtracts the texture's atlas origin and multiplies the result by
the loaded graphics object's inverse width/height.  Replacing a 64px TEX by a
256px TEX without changing that calculation samples only 0..63/256 -- the
upper-left quarter -- and stretches it across the polygon.  That is the
blocky, square, discontinuous terrain seen in builds 178 and 180.

``build._convert_world_dds`` emits Gaia terrain at one uniform factor relative
to native TEX. This pass multiplies the three U and three V terrain results by
that factor. Clouds, meteor, map markers, and the soft shadow instead preserve
their own source aspect and receive independent X/Y factors on their loaded
texture objects. Both halves are gated by internal values produced only by an
active Gaia build; vanilla and every non-Gaia world mod remain outside it.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import json
import struct
from pathlib import Path

import a64 as A
import ff7nx_cave
import nso_patcher
import nxmap


SCALE_ENV = 'SEVENTH_NX_WORLD_GAIA_SCALE'
SPECIAL_ENV = 'SEVENTH_NX_WORLD_GAIA_SPECIAL_SCALES'

# End of world texture initialization. The stock BL loads models after every
# relevant texture object has been created; replacing it with BL-to-cave lets
# us correct the objects and then tail-branch to the displaced call.
SPECIAL_HOOK = 0x00F77C10
SPECIAL_HOOK_WORD = 0x940003AC
SPECIAL_ORIGINAL_CALL = 0x00F78AC0
TRANSLATE = 0x010FC3A0

# Guest globals holding t_dx_sfx_e0 pointers. Their f_24/f_28 fields are the
# inverse texture width/height used by native pixel-space sprite UVs.
SPECIAL_GLOBALS = {
    'map': 0x00E2C3E4,
    'wm_kumo': 0x00E2C3EC,
    'meteo': 0x00E2C3F0,
    'shadow': 0x00E2C400,
}

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
    return (scale() != 1.0 or
            any(pair != (1.0, 1.0) for pair in special_scales().values()))


def special_scales():
    """Validated {name: (x_factor, y_factor)} from the archive converter."""
    try:
        raw = json.loads(os.environ.get(SPECIAL_ENV, '{}'))
    except (ValueError, TypeError):
        return {}
    out = {}
    if not isinstance(raw, dict):
        return out
    for name, pair in raw.items():
        if name not in SPECIAL_GLOBALS or not isinstance(pair, (list, tuple)):
            continue
        try:
            sx, sy = float(pair[0]), float(pair[1])
        except (IndexError, TypeError, ValueError):
            continue
        if 0.125 <= sx <= 16.0 and 0.125 <= sy <= 16.0:
            out[name] = (sx, sy)
    return out


def _hex(word):
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


def _fadd_d(rd, rn, rm):
    return 0x1E602800 | (rm << 16) | (rn << 5) | rd


def _fmul_d(rd, rn, rm):
    return 0x1E600800 | (rm << 16) | (rn << 5) | rd


def _ldr_s(rt, rn, imm=0):
    return 0xBD400000 | ((imm >> 2) << 10) | (rn << 5) | rt


def _str_s(rt, rn, imm=0):
    return 0xBD000000 | ((imm >> 2) << 10) | (rn << 5) | rt


def _fmov_w2s(sd, wn):
    return 0x1E270000 | (wn << 5) | sd


def _fmul_s(rd, rn, rm):
    return 0x1E200800 | (rm << 16) | (rn << 5) | rd


def _float_words(reg, value):
    bits = struct.unpack('<I', struct.pack('<f', value))[0]
    return A.movz_movk(reg, bits)


def _special_cave(pool, scales):
    """Patch loaded sprite objects' inverse dimensions, then call stock."""
    ordered = [(name, SPECIAL_GLOBALS[name], scales[name])
               for name in ('map', 'wm_kumo', 'meteo', 'shadow')
               if name in scales and scales[name] != (1.0, 1.0)]
    if not ordered:
        return {}, None

    def build(_entry, addr):
        words = [A.stp64_pre(29, 30, A.SP, -16)]
        skips = []

        def emit(word):
            words.append(word)

        def emit_bl(target):
            emit(A.bl(addr(len(words)), target))

        for _name, guest_global, (sx, sy) in ordered:
            words += A.movz_movk(0, guest_global)
            emit_bl(TRANSLATE)
            emit(A.ldr(8, 0))                  # guest texture-object pointer
            skip_i = len(words)
            emit(0)                            # cbz w8, next object
            emit(A.add_imm(0, 8, 0x24))        # guest &object->f_24
            emit_bl(TRANSLATE)
            emit(_ldr_s(0, 0, 0))
            words += _float_words(9, sx)
            emit(_fmov_w2s(1, 9))
            emit(_fmul_s(0, 0, 1))
            emit(_str_s(0, 0, 0))
            emit(_ldr_s(0, 0, 4))
            words += _float_words(9, sy)
            emit(_fmov_w2s(1, 9))
            emit(_fmul_s(0, 0, 1))
            emit(_str_s(0, 0, 4))
            skips.append((skip_i, len(words)))

        emit(A.ldp64_post(29, 30, A.SP, 16))
        emit(A.b(addr(len(words)), SPECIAL_ORIGINAL_CALL))
        for skip_i, next_i in skips:
            words[skip_i] = A.cbz(8, addr(skip_i), addr(next_i))
        return words

    entry, out = ff7nx_cave.emit_laid_out(pool, build)
    out[SPECIAL_HOOK] = A.bl(SPECIAL_HOOK, entry)
    return out, entry


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
    sprites = {name: pair for name, pair in special_scales().items()
               if pair != (1.0, 1.0)}
    if factor == 1.0 and not sprites:
        return {}
    if factor != 1.0:
        for va, expect, _ in SITES:
            got = struct.unpack_from('<I', main.img, va)[0]
            if got != expect:
                raise ValueError('Gaia UV site +0x%X expected %08X, found %08X'
                                 % (va, expect, got))
    if sprites:
        got = struct.unpack_from('<I', main.img, SPECIAL_HOOK)[0]
        if got != SPECIAL_HOOK_WORD:
            raise ValueError('Gaia sprite UV hook +0x%X expected %08X, '
                             'found %08X'
                             % (SPECIAL_HOOK, SPECIAL_HOOK_WORD, got))

    pool = ff7nx_cave.HolePool(main.img, starts=set(main.arm_starts))
    out = {}
    if factor != 1.0:
        for va, original, dest in SITES:
            # emit_hooked normally places the displaced word after the body.
            # The multiply must happen first, so include it explicitly and use
            # a NOP as the harmless displaced tail.
            words, entry = ff7nx_cave.emit_hooked(
                pool, va, NOP, [original] + _scale_words(dest, factor))
            out.update(words)
            log('  Gaia terrain UV +0x%X: %sx cave entry +0x%X'
                % (va, factor, entry))
    if sprites:
        words, entry = _special_cave(pool, sprites)
        out.update(words)
        log('  Gaia sprite UVs %s: cave entry +0x%X'
            % (', '.join('%s %.3gx/%.3gy' % (n, x, y)
                        for n, (x, y) in sorted(sprites.items())), entry))
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
                {'name': (('terrain UV hook' if va in {s[0] for s in SITES}
                           else 'Gaia sprite UV hook')
                          if va in ({s[0] for s in SITES} | {SPECIAL_HOOK})
                          else 'Gaia UV cave word'),
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
