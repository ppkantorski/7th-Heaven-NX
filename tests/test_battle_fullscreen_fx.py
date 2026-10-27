#!/usr/bin/env python3
"""Regression checks for the 16:9 battle fade/flash/opening-reveal patch.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import hashlib
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import arm64emu as E
import build as Build
import exe_patch
import ff7nx_battlewide as B
import ff7nx_uiclip as U
import nso_patcher
import nxmap


def check(ok, what):
    if not ok:
        raise AssertionError(what)
    print('  ok  ' + what)


def w32(img, va):
    return struct.unpack_from('<I', img, va)[0]


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def set_count(main, old, new):
    words = {21: 0x528002A8, 84: 0x52800A88}
    nso = nso_patcher.read_nso(main)
    list(nso_patcher.apply_spec(nso, {'name': 'test 60fps composition',
        'patches': [{'name': 'strip count', 'va': hex(B.FADE_COUNT),
                     'expect': struct.pack('<I', words[old]).hex(),
                     'set': struct.pack('<I', words[new]).hex()}]}))
    Path(main).write_bytes(nso_patcher.rebuild(nso))


def cave_math(img, hook):
    cave, why = B._walk_stride_cave(img, hook)
    check(cave is not None, 'stride cave at +0x%X returns (%s)' % (hook, why or 'clean'))
    words = [w32(img, va) for va in cave
             if B._branch_target(va, w32(img, va)) is None]
    check(len(words) == 2, 'stride cave at +0x%X contains exactly two arithmetic words' % hook)
    for x in (0, 1, 2, 3, 7, 31, 0x1234, 0xFFFF):
        cpu = E.Cpu(E.Mem(), host_base=0x700000000)
        cpu.sp = 0x800000000
        cpu.x[8] = x
        rd = B.FADE_STRIDE_WORDS[hook] & 31
        cpu.run(0, words)
        check((cpu.x[rd] & 0xFFFFFFFF) == ((48 * x) & 0xFFFFFFFF),
              'stride +0x%X maps %d -> 48*x' % (hook, x))


def run_derived_bounds(width_word, x_word, height_word, y=0, stored=None):
    """
    Execute the real ARM right/bottom arithmetic in battle_enter.

    `stored` supplies (w, x, h) when the three input words are the STOCK
    loads, so the same arithmetic can be measured in both states: patched
    (build 199..202) and stock (what this module now ships).
    """
    base = 0x200000
    scratch = 0x40001000
    mem = E.Mem()
    cpu = E.Cpu(mem, host_base=0x700000000)
    cpu.sp = 0x800000000
    cpu.x[23] = scratch
    if stored is not None:
        # A stock `ldr w8,[x0]` reads the guest global the translate call put
        # in x0.  Point x0 at each stored value in turn; the arithmetic words
        # below stay byte-identical either way.
        w_val, x_val, h_val = stored
        y = stored[1] * 0 + y
        slot = scratch + 0x40
        mem.write(slot, struct.pack('<i', w_val))
        cpu.x[0] = slot
    cpu.run(base, [
        width_word,              # +0x8D464
        0xB90002E8,              # str w8,[x23]
    ])
    if stored is not None:
        mem.write(slot, struct.pack('<i', x_val))
        cpu.x[0] = slot
    cpu.run(base + 0x80, [
        x_word,                  # +0x8D474
        0xB94002E9,              # ldr w9,[x23]
        0x0B080129,              # add w9,w9,w8
        0x51000533,              # sub w19,w9,#1
    ])
    right = cpu.x[19] & 0xFFFFFFFF
    if stored is not None:
        mem.write(slot, struct.pack('<i', h_val))
        cpu.x[0] = slot
    cpu.run(base + 0x100, [
        height_word,             # +0x8D49C
        0xB90002E8,              # str w8,[x23]
    ])
    # The real y load uses a translator result in x0.  Feeding the literal y
    # is equivalent and keeps the tested arithmetic itself byte-identical.
    cpu.x[8] = y & 0xFFFFFFFF
    cpu.run(base + 0x200, [
        0xB94002E9,              # ldr w9,[x23]
        0x0B080129,              # add w9,w9,w8
        0x51000533,              # sub w19,w9,#1
    ])
    return right, cpu.x[19] & 0xFFFFFFFF


def run_ui_band_top(word, rect_y):
    """Execute the real `add w20, w8, #imm` with the menu rect y in w8."""
    cpu = E.Cpu(E.Mem(), host_base=0x700000000)
    cpu.sp = 0x800000000
    cpu.x[8] = rect_y & 0xFFFFFFFF
    cpu.run(0x200000, [word])
    return cpu.x[20] & 0xFFFFFFFF


def run_visible_ui_bottom(word):
    """Execute 0x6CF5C5's translated bottom-value instruction."""
    cpu = E.Cpu(E.Mem(), host_base=0x700000000)
    cpu.sp = 0x800000000
    cpu.x[22] = struct.unpack('<I', struct.pack('<f', 332.0))[0]
    cpu.run(B.UI_FADE_VISIBLE_BOTTOM, [word])
    bits = cpu.x[23] & 0xFFFFFFFF
    return struct.unpack('<f', struct.pack('<I', bits))[0]


def run_quad_vertex(load_word, add_word):
    """
    Execute battle_draw_quad_5BD473's own two words for one vertex.

    `add_word` is the function's stock `add w8,w8,#1`, taken from the image
    rather than typed here, so the +1 being relied on is the real one.
    """
    cpu = E.Cpu(E.Mem(), host_base=0x700000000)
    cpu.sp = 0x800000000
    cpu.run(0x200000, [load_word, add_word])
    return cpu.x[8] & 0xFFFFFFFF


def run_neo_scale(mov_word, mul_word, value, out_reg):
    """Execute one recompiled Neo Bahamut width/4 multiplication."""
    cpu = E.Cpu(E.Mem(), host_base=0x700000000)
    cpu.sp = 0x800000000
    cpu.x[8] = value & 0xFFFFFFFF
    cpu.run(0x200000, [mov_word, mul_word])
    return cpu.x[out_reg] & 0xFFFFFFFF


def main():
    stock = ROOT / 'game_data_files/exefs/main'
    check(stock.exists(), 'stock NSO exists')

    # Outside truth: the values and initialization order from FFNx itself.
    ws = (ROOT / 'repos/FFNx-master/src/ff7/widescreen.cpp').read_text()
    anim = (ROOT / 'repos/FFNx-master/src/ff7/battle/animations.cpp').read_text()
    ogl = (ROOT / 'repos/FFNx-master/src/ff7_opengl.cpp').read_text()
    for s in ('+ 0x3A, 30', '+ 0x69, 120', '+ 0x46, 72', '+ 0xA5, 72',
              '+ 0x87, 48', '+ 0xDC, 48', '+ 0x100, 48', '+ 0x15C, 48',
              '+ 0x186, 48'):
        check(s in ws, 'FFNx widescreen owns `%s`' % s)
    for s in ('+ 0x50, (uint32_t)&wide_viewport_x',
              '+ 0xA8, (uint32_t)&wide_viewport_x',
              '+ 0x105, (uint32_t)&wide_viewport_x',
              '+ 0x163, (uint32_t)&wide_viewport_x',
              '+ 0x111, (uint32_t)&wide_viewport_width',
              '+ 0x16F, (uint32_t)&wide_viewport_width'):
        check(s in ws, 'FFNx menu/endbattle fade owns `%s`' % s)
    for s in ('shadow_flare_draw_white_bg_57747E + 0x18, wide_viewport_x',
              'shadow_flare_draw_white_bg_57747E + 0x1F, wide_viewport_width / 2'):
        check(s in ws, 'FFNx battle white flash owns `%s`' % s)
    for s in (
            'neo_bahamut_effect_sub_490F2A + 0x58, wide_viewport_width / 4',
            'neo_bahamut_effect_sub_490F2A + 0x5D, (uint32_t)&wide_viewport_width',
            'neo_bahamut_effect_sub_490F2A + 0x6A, (uint32_t)&wide_viewport_x',
            'neo_bahamut_effect_sub_490F2A + 0x88, wide_viewport_width / 4',
            'neo_bahamut_effect_sub_490F2A + 0x1A2, (uint32_t)&wide_viewport_x',
            'neo_bahamut_effect_sub_490F2A + 0x1AF, (uint32_t)&wide_viewport_width',
            'run_bahamut_neo_main_48C2A1 + 0x140, (uint32_t)&wide_viewport_x',
            'run_bahamut_neo_main_48C2A1 + 0x15B, (uint32_t)&wide_viewport_width',
            'run_bahamut_neo_main_48C2A1 + 0x19B, (uint32_t)&wide_viewport_x',
            'run_bahamut_neo_main_48C2A1 + 0x1D1, (uint32_t)&wide_viewport_width',
            'run_bahamut_neo_main_48C2A1 + 0x20E, (uint32_t)&wide_viewport_x',
            'run_bahamut_neo_main_48C2A1 + 0x243, (uint32_t)&wide_viewport_width',
            'run_bahamut_neo_main_48C2A1 + 0x28A, (uint32_t)&wide_viewport_x',
            'run_bahamut_neo_main_48C2A1 + 0x2C0, (uint32_t)&wide_viewport_width',
            'run_bahamut_neo_main_48C2A1 + 0x2FC, (uint32_t)&wide_viewport_x',
            'run_bahamut_neo_main_48C2A1 + 0x332, (uint32_t)&wide_viewport_width'):
        check(s in ws, 'FFNx Neo Bahamut widescreen owns `%s`' % s)
    check('battle_sub_5BCF9D + 0x3A, battle_frame_multiplier' in anim,
          'FFNx 60 FPS also owns the strip count')
    check(ogl.index('ff7_widescreen_hook_init();') <
          ogl.index('ff7::battle::animations_hook_init();'),
          'FFNx composes 21 -> 30 -> 120 in that order')

    # Full-width partial-height viewports are legitimate in battle and field
    # paths.  Lower Junon hardware testing proved mode-aware packed equality
    # blanks field entry, so the shared driver hook must not infer mode.
    clip = (0, 0, 1280, 498)
    check(U._run_cave(U.body_words(), clip, game_mode=3) == clip,
          'battle full-x partial-height clip retains its original y bounds')
    check(U._run_cave(U.body_words(), clip, game_mode=2) == clip,
          'field full-x partial-height clip retains its original y bounds')

    # Exact ARM signatures, before any patch.
    m = nxmap.Main(str(stock))
    check(w32(m.img, B.FADE_COUNT) == 0x528002A8, 'stock strip count is 21')
    for va, (old, _new, name) in B.FADE_SIMPLE.items():
        check(w32(m.img, va) == old, '%s has its stock ARM word' % name)
    for va, old in B.FADE_STRIDE_WORDS.items():
        check(w32(m.img, va) == old, 'stride x33 signature at +0x%X' % va)
    for va, (_value, name) in B.MENU_FADE.items():
        check(w32(m.img, va) == B.MENU_FADE_STOCK,
              '%s has its stock ARM float load' % name)
    for va, (_value, name) in B.MENU_FADE_TRANSITION_BOTTOM.items():
        check(w32(m.img, va) == B.MENU_FADE_STOCK,
              '%s has its stock dynamic ARM float load before patching' % name)
    for va, name in B.UI_FADE_BOTTOM_SAFE.items():
        check(w32(m.img, va) == B.UI_FADE_BOTTOM_SAFE_STOCK,
              '%s has its stock post-translation load' % name)
    check(w32(m.img, B.UI_FADE_VISIBLE_BOTTOM) ==
          B.UI_FADE_VISIBLE_BOTTOM_STOCK,
          'measured UI-fade producer has stock 480 bottom arithmetic')
    check(run_visible_ui_bottom(B.UI_FADE_VISIBLE_BOTTOM_STOCK) == 480.0,
          'stock measured UI-fade producer computes 480.0f')
    for va, word in B.UI_FADE_VISIBLE_BOTTOM_ANCHORS.items():
        check(w32(m.img, va) == word,
              'measured UI-fade producer anchor +0x%X is stock' % va)
    check(w32(m.img, B.WHITE_FLASH_X) == B.WHITE_FLASH_X_STOCK,
          'stock battle white-flash left is str wzr,[x0]')
    check(w32(m.img, B.WHITE_FLASH_RIGHT) == B.WHITE_FLASH_RIGHT_STOCK,
          'stock battle white-flash half-width is 319')
    for va, (old, _value, name) in B.BATTLE_BOUND_INPUTS.items():
        check(w32(m.img, va) == old, '%s has its stock ARM load' % name)
    for va, word in B.BATTLE_BOUND_ANCHORS.items():
        check(w32(m.img, va) == word,
              'battle overlay-bound anchor +0x%X is stock' % va)
    for va, (_value, name) in B.QUAD_BOUND_INPUTS.items():
        check(w32(m.img, va) == B.QUAD_BOUND_STOCK,
              '%s has its stock ARM load' % name)
    for va, word in B.QUAD_BOUND_ANCHORS.items():
        check(w32(m.img, va) == word,
              'quad overlay-bound anchor +0x%X is stock' % va)
    check(w32(m.img, B.UI_BAND_TOP) == B.UI_BAND_TOP_STOCK,
          'stock UI-band backdrop top is add w20,w8,#166')
    for va, word in B.UI_BAND_ANCHORS.items():
        check(w32(m.img, va) == word, 'UI-band anchor +0x%X is stock' % va)
    for va, word in B.UI_FADE_BOTTOM.items():
        check(w32(m.img, va) == word,
              'battle UI fade-bottom word +0x%X is stock' % va)
    for va, word in B.UI_FADE_BOTTOM_ANCHORS.items():
        check(w32(m.img, va) == word,
              'battle UI fade-bottom anchor +0x%X is stock' % va)
    check(w32(m.img, B.ENGINE_FADE_W) == B.ENGINE_FADE_W_STOCK,
          'stock engine fade width is mov w8,#640')
    check(w32(m.img, B.ENGINE_FADE_X) == B.ENGINE_FADE_X_STOCK,
          'stock engine fade left is str wzr,[x0]')
    for va, word in B.ENGINE_FADE_ANCHORS.items():
        check(w32(m.img, va) == word, 'engine-fade anchor +0x%X is stock' % va)
    for va, (word, name) in B.NEO_BAHAMUT_SCALE.items():
        check(w32(m.img, va) == word, '%s is stock' % name)
    for va, word in B.NEO_BAHAMUT_SCALE_ANCHORS.items():
        check(w32(m.img, va) == word,
              'Neo Bahamut scale anchor +0x%X is stock' % va)
    g4_patches, _g4_notes, g4_problems = B.plan(m, 4, False)
    check(not g4_problems, 'Neo Bahamut rect consumers are discoverable')
    check(len(g4_patches) == 20,
          'all 20 Neo Bahamut Switch rect consumers require widening')
    g3_patches, _g3_notes, g3_problems = B.plan(m, 3, False)
    check(not g3_problems, 'legacy group 3 is discoverable for safe removal')
    g3_stock = {int(p['va'], 16): w32(m.img, int(p['va'], 16))
                for p in g3_patches}

    with tempfile.TemporaryDirectory(prefix='battle-fullscreen-') as td:
        work = Path(td) / 'main'
        shutil.copy2(stock, work)
        before = work.read_bytes()

        check(B.apply_all(work, log=lambda *_: None) == 0, 'full pass applies')
        once = sha(work)
        pm = nxmap.Main(str(work))
        g4_again, _g4_notes, g4_problems = B.plan(pm, 4, False)
        check(not g4_problems and not g4_again,
              'all Neo Bahamut rect consumers are wide after apply')
        import a64 as A
        neo_wide = {
            0x00280238: A.movz(9, B.NEO_BAHAMUT_SEGMENT),
            0x00280248: A.mul(8, 8, 9),
            0x002802C8: A.movz(9, B.NEO_BAHAMUT_SEGMENT),
            0x002802CC: A.mul(22, 8, 9),
        }
        for va, word in neo_wide.items():
            check(w32(pm.img, va) == word,
                  'Neo Bahamut segment word +0x%X is wide' % va)
        for value in (0, 1, 2, 7, 31, 0x1234, 0xFFFF):
            check(run_neo_scale(neo_wide[0x00280238],
                                 neo_wide[0x00280248], value, 8) ==
                  (value * 213) & 0xFFFFFFFF,
                  'Neo Bahamut first segment maps %d -> 213*x' % value)
            check(run_neo_scale(neo_wide[0x002802C8],
                                 neo_wide[0x002802CC], value, 22) ==
                  (value * 213) & 0xFFFFFFFF,
                  'Neo Bahamut second segment maps %d -> 213*x' % value)
        # Every feature below follows the stage ladder, so this suite verifies
        # the rung actually being built rather than one fixed expectation.
        if B.want('fade-anim'):
            check(w32(pm.img, B.FADE_COUNT) == 0x528003C8,
                  '15 FPS composition is 21 -> 30 strips')
            for va, (_old, new, name) in B.FADE_SIMPLE.items():
                check(w32(pm.img, va) == new, '%s is wide' % name)
            for hook in B.FADE_STRIDE:
                check(B._branch_target(hook, w32(pm.img, hook)) is not None,
                      'stride +0x%X is hooked' % hook)
                cave_math(pm.img, hook)
        else:
            check(w32(pm.img, B.FADE_COUNT) == w32(m.img, B.FADE_COUNT),
                  'strip count stays stock below stage 2')
            for va, (old, _new, name) in B.FADE_SIMPLE.items():
                check(w32(pm.img, va) == old, '%s stays stock' % name)
            for hook in B.FADE_STRIDE:
                check(w32(pm.img, hook) == w32(m.img, hook),
                      'stride +0x%X stays the stock x33 word' % hook)

        for hook, (value, name) in B.MENU_FADE.items():
            if not B.want('menu-fade'):
                check(w32(pm.img, hook) == B.MENU_FADE_STOCK,
                      '%s stays the stock dynamic load' % name)
                continue
            check(B._branch_target(hook, w32(pm.img, hook)) is not None,
                  '%s is hooked' % name)
            cave, why = B._walk_stride_cave(pm.img, hook, limit=32)
            check(cave is not None, '%s cave returns (%s)' % (name, why or 'clean'))
            logical = [w32(pm.img, va) for va in cave
                       if B._branch_target(va, w32(pm.img, va)) is None]
            check(logical == B._menu_fade_body(value),
                  '%s creates the exact wide integer bit-pattern' % name)
        # The two transition bottom vertices follow the stage ladder.  A
        # default build leaves them exactly as FFNx does -- dynamic -- and
        # only stage 7 installs the forced 480.  Assert whichever the current
        # stage asked for, so the suite proves the switch instead of pinning
        # one side of an unresolved question.
        want_tb = B.want('transition-bottom')
        for hook, (_value, name) in B.MENU_FADE_TRANSITION_BOTTOM.items():
            hooked = B._branch_target(hook, w32(pm.img, hook)) is not None
            check(hooked == want_tb,
                  '%s is %s (stage %d)'
                  % (name, 'hooked' if want_tb else 'stock, as FFNx leaves it',
                     B.stage()))
            if not want_tb:
                check(w32(pm.img, hook) == B.MENU_FADE_STOCK,
                      '%s is the byte-exact stock dynamic load' % name)
                continue
            cave, why = B._walk_stride_cave(pm.img, hook, limit=32)
            check(cave is not None,
                  '%s cave returns (%s)' % (name, why or 'clean'))
            logical = [w32(pm.img, va) for va in cave
                       if B._branch_target(va, w32(pm.img, va)) is None]
            check(logical == B._menu_fade_body(480),
                  '%s creates exact full-frame bottom 480' % name)
        for hook, name in B.UI_FADE_BOTTOM_SAFE.items():
            check(w32(pm.img, hook) == B.UI_FADE_BOTTOM_SAFE_STOCK,
                  '%s stays stock after build-208 experiment retirement' % name)
        if B.want('ui-fade-bottom'):
            check(w32(pm.img, B.UI_FADE_VISIBLE_BOTTOM) ==
                  B.UI_FADE_VISIBLE_BOTTOM_FIXED,
                  'measured full-resolution UI-fade producer is corrected')
            check(run_visible_ui_bottom(
                      w32(pm.img, B.UI_FADE_VISIBLE_BOTTOM)) == 444.0,
                  'one producer-local word computes exact visible bottom 444.0f')
        else:
            check(w32(pm.img, B.UI_FADE_VISIBLE_BOTTOM) ==
                  w32(m.img, B.UI_FADE_VISIBLE_BOTTOM),
                  'measured UI-fade producer stays stock below stage 5')
        for va, word in B.UI_FADE_VISIBLE_BOTTOM_ANCHORS.items():
            check(w32(pm.img, va) == word,
                  'measured UI-fade anchor +0x%X remains unchanged' % va)
        if B.want('white-flash'):
            check(B._branch_target(B.WHITE_FLASH_X,
                                   w32(pm.img, B.WHITE_FLASH_X)) is not None,
                  'battle white-flash left is hooked')
            cave, why = B._walk_stride_cave(pm.img, B.WHITE_FLASH_X, limit=32)
            check(cave is not None,
                  'battle white-flash left cave returns (%s)' % (why or 'clean'))
            logical = [w32(pm.img, va) for va in cave
                       if B._branch_target(va, w32(pm.img, va)) is None]
            check(logical == B._white_flash_x_body(),
                  'battle white-flash cave stores x=-107')
            check(w32(pm.img, B.WHITE_FLASH_RIGHT) == B.WHITE_FLASH_RIGHT_WIDE,
                  'battle white-flash half-width is 427')
        else:
            check(w32(pm.img, B.WHITE_FLASH_X) == B.WHITE_FLASH_X_STOCK,
                  'battle white-flash left stays str wzr,[x0]')
            check(w32(pm.img, B.WHITE_FLASH_RIGHT) == B.WHITE_FLASH_RIGHT_STOCK,
                  'battle white-flash half-width stays 319')
        # THE BUILD-202/204 WORLD-MAP FREEZE.  Neither battle_enter's shared
        # maxima nor the generic/boss-death submitter may carry experimental
        # wide right/bottom values.  The proven opening/flash strip producer
        # is patched separately by group 2.
        for va, (old, _value, name) in B.BATTLE_BOUND_INPUTS.items():
            check(w32(pm.img, va) == old,
                  '%s is still a stock load after apply -- [0x9AC108] and '
                  '[0x9AD198] reach every other consumer unchanged' % name)
        for va, word in B.BATTLE_BOUND_ANCHORS.items():
            check(w32(pm.img, va) == word,
                  'anchor +0x%X remains unchanged after apply' % va)
        # Stage 6 only.  Below it the four submitter-local loads stay stock, so
        # battle_draw_quad_5BD473's right/bottom keep agreeing with the stored
        # rect that its other 44 call sites still read.
        want_qb = B.want('quad-geometry')
        for va, (value, name) in B.QUAD_BOUND_INPUTS.items():
            check(w32(pm.img, va) == (B.enc_movz(8, value) if want_qb
                                      else B.QUAD_BOUND_STOCK),
                  '%s is %s (stage %d)'
                  % (name, 'widened in the local submitter' if want_qb
                     else 'a stock load', B.stage()))
        for va, word in B.QUAD_BOUND_ANCHORS.items():
            check(w32(pm.img, va) == word,
                  'quad anchor +0x%X remains unchanged after apply' % va)
        # Hardware build 205 proved this local submitter is shared by the
        # full fade and attack-flash paths.  The risky battle_enter globals
        # above remain stock; only these four consumer-local loads are wide.
        # Build 203's UI-band collapse was disproven on hardware; a normal
        # build must leave that function completely stock.
        check(w32(pm.img, B.UI_BAND_TOP) == B.UI_BAND_TOP_STOCK,
              'the disproven UI-band collapse is NOT installed')
        for va, word in B.UI_BAND_ANCHORS.items():
            check(w32(pm.img, va) == word,
                  'UI-band anchor +0x%X remains unchanged after apply' % va)

        # Both the failed build-205 translations and build-208's later load
        # hooks remain stock.  Hardware disproved both candidate producers.
        for va, word in B.UI_FADE_BOTTOM.items():
            check(w32(pm.img, va) == word,
                  'battle UI fade-bottom ARM word +0x%X remains stock' % va)
        for va, word in B.UI_FADE_BOTTOM_ANCHORS.items():
            check(w32(pm.img, va) == word,
                  'battle UI fade-bottom anchor +0x%X is unchanged' % va)

        # FFNx names 0x659532 highway_submit_fade_quad.  Build 204's mistaken
        # battle attribution is migrated out; normal battle builds leave it
        # stock.
        check(w32(pm.img, B.ENGINE_FADE_W) == B.ENGINE_FADE_W_STOCK,
              'highway fade width is restored to stock 640')
        check(w32(pm.img, B.ENGINE_FADE_X) == B.ENGINE_FADE_X_STOCK,
              'highway fade left is restored to stock 0')
        for va, word in B.ENGINE_FADE_ANCHORS.items():
            check(w32(pm.img, va) == word,
                  'engine-fade anchor +0x%X remains unchanged after apply' % va)
        # And the stock arithmetic that build 202 patched still derives the
        # 4:3 values, which is what every other reader now keeps seeing.
        check(run_derived_bounds(B.QUAD_BOUND_STOCK, B.QUAD_BOUND_STOCK,
                                 B.QUAD_BOUND_STOCK, stored=(640, 0, 332)) ==
              (639, 331),
              'battle_enter still derives the stock right=639/bottom=331')
        check(all(w32(pm.img, va) == word for va, word in g3_stock.items()),
              'unproven legacy group 3 remains stock in a normal build')

        # The final submit helper is part of group 2; these two left vertices
        # were the missing half of the previous caller-only port.
        x20, _ = B.encode('w20', 2, -107)
        x19, _ = B.encode('w19', 2, -107)
        if not B.want('quad-geometry'):
            check(w32(pm.img, 0x7CCFCC) == w32(m.img, 0x7CCFCC) and
                  w32(pm.img, 0x7CD0AC) == w32(m.img, 0x7CD0AC),
                  'stage 0 leaves both 5BD473 origins stock (build-197 state)')
        else:
            check(w32(pm.img, 0x7CCFCC) == x20, 'quad vertex 0 starts at x=-107')
            check(w32(pm.img, 0x7CD0AC) == x19, 'quad vertex 2 starts at x=-107')

        # THE REGRESSION THAT SHIPPED ONCE.  battle_draw_quad_5BD473 writes one
        # rectangle; widening its left while its right/bottom stay stock gives
        # a fade that is wide on the left and 4:3 on the right, which is what
        # hardware reported.  Assert all four corners agree, in BOTH states, so
        # no future stage split can separate them silently again.
        left_wide = w32(pm.img, 0x7CCFCC) != w32(m.img, 0x7CCFCC)
        right_wide = any(w32(pm.img, va) != B.QUAD_BOUND_STOCK
                         for va in B.QUAD_BOUND_INPUTS)
        check(left_wide == right_wide,
              'the 5BD473 fade quad is widened on all four corners or none '
              '(left_wide=%s right/bottom_wide=%s)' % (left_wide, right_wide))

        check(B.apply_all(work, log=lambda *_: None) == 0, 'second apply succeeds')
        check(sha(work) == once, 'second apply is byte-idempotent')
        check(B.apply_all(work, revert=True, log=lambda *_: None) == 0,
              'full pass reverts')
        check(work.read_bytes() == before, 'revert is byte-exact')

        # Repeat with the 60 FPS owner already applied.  Widescreen must turn
        # 84 into 120, and its revert must return to 84 rather than stock 21.
        set_count(work, 21, 84)
        fps_baseline = work.read_bytes()
        check(B.apply_all(work, log=lambda *_: None) == 0,
              'full pass composes with the 60 FPS count')
        fm = nxmap.Main(str(work))
        check(w32(fm.img, B.FADE_COUNT) == (0x52800F08 if B.want('fade-anim')
                                            else 0x52800A88),
              '60 FPS composition is %s'
              % ('84 -> 120 strips' if B.want('fade-anim')
                 else 'left at the 60 FPS 84 below stage 2'))
        check(B.apply_all(work, revert=True, log=lambda *_: None) == 0,
              'composed pass reverts')
        check(work.read_bytes() == fps_baseline,
              'revert preserves the pre-existing 60 FPS patch byte-exactly')

    # Migration regression: an existing sdout/main can be the input when the
    # GUI skips flevel.  First seed build 202's shared-bound state by itself --
    # build 203 deliberately refused to stack its replacement over that state.
    with tempfile.TemporaryDirectory(prefix='battle-legacy-migration-') as td:
        work = Path(td) / 'main'
        shutil.copy2(stock, work)
        check(B.apply_battle_bounds(work, log=lambda *_: None) == 0,
              'seed build-202 shared-bound experiment')
        check(B.apply_all(work, log=lambda *_: None) == 0,
              'normal pass migrates build-202 state')
        mm = nxmap.Main(str(work))
        for va, (old, _value, name) in B.BATTLE_BOUND_INPUTS.items():
            check(w32(mm.img, va) == old,
                  'migration restores %s' % name)
        for va, (value, name) in B.QUAD_BOUND_INPUTS.items():
            check(w32(mm.img, va) == (B.enc_movz(8, value) if B.want('quad-geometry')
                                      else B.QUAD_BOUND_STOCK),
                  'build-202 migration leaves local %s in its stage state' % name)
        check(all(w32(mm.img, va) == word
                  for va, word in B.UI_FADE_BOTTOM.items()),
              'build-202 migration leaves translator code stock')

    # Build 204 contains build 203's submitter values plus the misattributed
    # highway fade.  Seed that real compatible combination (and the retired
    # UI-top candidate) and prove the current pass removes all three.
    with tempfile.TemporaryDirectory(prefix='battle-204-migration-') as td:
        work = Path(td) / 'main'
        shutil.copy2(stock, work)
        check(B.apply_quad_bounds(work, log=lambda *_: None) == 0,
              'seed build-203 submitter-bound experiment')
        check(B.apply_ui_band(work, log=lambda *_: None) == 0,
              'seed build-203 UI-top experiment')
        check(B.apply_engine_fade(work, log=lambda *_: None) == 0,
              'seed build-204 highway-fade experiment')
        check(B.apply_ui_fade_bottom(work, log=lambda *_: None) == 0,
              'seed failed build-205 ARM UI-bottom experiment')
        historical_menu = dict(B.MENU_FADE)
        historical_menu.update(B.MENU_FADE_TRANSITION_BOTTOM)
        with patch.object(B, 'MENU_FADE', historical_menu), \
                patch.object(B, 'MENU_FADE_TRANSITION_BOTTOM', {}):
            check(B.apply_menu_fade(work, log=lambda *_: None) == 0,
                  'seed build-205 full-frame transition bottom vertices')
        check(B.apply_all(work, log=lambda *_: None) == 0,
              'normal pass migrates build-203/204/205 state')
        mm = nxmap.Main(str(work))
        # A build-203/205-shaped sdout must be migrated to WHATEVER the current
        # stage asks for -- including down, when the stage no longer wants the
        # submitter bounds or the forced transition bottoms.
        for va, (value, name) in B.QUAD_BOUND_INPUTS.items():
            check(w32(mm.img, va) == (B.enc_movz(8, value)
                                      if B.want('quad-geometry')
                                      else B.QUAD_BOUND_STOCK),
                  'migration brings local %s to its stage state' % name)
        check(w32(mm.img, B.UI_BAND_TOP) == B.UI_BAND_TOP_STOCK,
              'migration restores disproven UI top')
        check(w32(mm.img, B.ENGINE_FADE_W) == B.ENGINE_FADE_W_STOCK and
              w32(mm.img, B.ENGINE_FADE_X) == B.ENGINE_FADE_X_STOCK,
              'migration restores unrelated highway fade')
        check(all(w32(mm.img, va) == word
                  for va, word in B.UI_FADE_BOTTOM.items()),
              'migration restores both original guest translations')
        for hook, (_value, name) in B.MENU_FADE_TRANSITION_BOTTOM.items():
            hooked = B._branch_target(hook, w32(mm.img, hook)) is not None
            check(hooked == B.want('transition-bottom'),
                  'migration brings %s to its stage state' % name)
            if not hooked:
                check(w32(mm.img, hook) == B.MENU_FADE_STOCK,
                      'migration returns %s to the byte-exact stock load'
                      % name)
        migrated = sha(work)
        check(B.apply_all(work, log=lambda *_: None) == 0,
              'migrated pass reapplies cleanly')
        check(sha(work) == migrated,
              'migrated pass is byte-idempotent')

    # Build 207 added two producer-height caves with 332 and widened the
    # emulated x86 UI fade horizontally.  The hardware result was unchanged
    # fade overlap plus a world-map battle-entry regression.  Prove the ARM
    # half of that exact failed shape is recognized and removed in one pass;
    # the x86 half is exercised byte-exactly below.
    with tempfile.TemporaryDirectory(prefix='battle-207-migration-') as td:
        work = Path(td) / 'main'
        shutil.copy2(stock, work)
        failed_menu = dict(B.MENU_FADE)
        failed_menu.update(B.MENU_FADE_FAILED_HEIGHT)
        with patch.object(B, 'MENU_FADE', failed_menu), \
                patch.object(B, 'MENU_FADE_TRANSITION_BOTTOM', {}), \
                patch.object(B, 'MENU_FADE_FAILED_HEIGHT', {}):
            check(B.apply_menu_fade(work, log=lambda *_: None) == 0,
                  'seed failed build-207 y=332 menu-fade heights')
        check(B.apply_all(work, log=lambda *_: None) == 0,
              'normal pass migrates failed build-207 ARM state')
        mm = nxmap.Main(str(work))
        for hook, (_value, name) in B.MENU_FADE_TRANSITION_BOTTOM.items():
            hooked = B._branch_target(hook, w32(mm.img, hook)) is not None
            check(hooked == B.want('transition-bottom'),
                  'build-207 migration brings %s to its stage state' % name)
            if not hooked:
                check(w32(mm.img, hook) == B.MENU_FADE_STOCK,
                      'build-207 migration returns %s to the stock load' % name)
        check(all(w32(mm.img, va) == word
                  for va, word in B.UI_FADE_BOTTOM.items()),
              'build-207 migration keeps guest translations/loads stock')

    # Build 208's two post-translation caves did not affect the visible fade
    # and regressed world-map battle entry.  Seed that exact historical form,
    # prove a normal pass removes it, and prove the unrelated measured
    # producer receives only its direct one-word arithmetic correction.
    with tempfile.TemporaryDirectory(prefix='battle-208-migration-') as td:
        work = Path(td) / 'main'
        shutil.copy2(stock, work)
        check(B.apply_ui_fade_bottom_safe(work, log=lambda *_: None) == 0,
              'seed failed build-208 post-translation hooks')
        check(B.apply_all(work, log=lambda *_: None) == 0,
              'normal pass migrates failed build-208 state')
        migrated = sha(work)
        mm = nxmap.Main(str(work))
        for hook, name in B.UI_FADE_BOTTOM_SAFE.items():
            check(w32(mm.img, hook) == B.UI_FADE_BOTTOM_SAFE_STOCK,
                  'build-208 migration restores stock %s' % name)
        check(w32(mm.img, B.UI_FADE_VISIBLE_BOTTOM) ==
              (B.UI_FADE_VISIBLE_BOTTOM_FIXED if B.want('ui-fade-bottom')
               else B.UI_FADE_VISIBLE_BOTTOM_STOCK),
              'build-208 migration brings the measured producer to its stage')
        check(B.apply_all(work, log=lambda *_: None) == 0,
              'build-208 migrated pass reapplies')
        check(sha(work) == migrated,
              'build-208 migrated pass is byte-idempotent')

    # The retired build-205 x86 experiment remains safely reversible for
    # migration.  Prove apply, idempotence and byte-exact revert.
    stock_exe = ROOT / 'game_data_files/ff7_1.02/ff7_en'
    check(stock_exe.exists(), 'stock x86 ff7_en exists')
    with tempfile.TemporaryDirectory(prefix='battle-fade-exe-') as td:
        work = Path(td) / 'ff7_en'
        shutil.copy2(stock_exe, work)
        before = work.read_bytes()
        pe = exe_patch.parse_pe(before)
        off = exe_patch.va_to_offset(pe, B.EXE_UI_FADE_BOTTOM_VA)
        check(before[off:off + 4] == B.EXE_UI_FADE_BOTTOM_STOCK,
              'stock x86 UI-overlay bottom is 240.0f')
        check(B.apply_exe_ui_fade_bottom(work, log=lambda *_: None) == 0,
              'x86 data-only UI-bottom correction applies')
        check(work.read_bytes()[off:off + 4] == B.EXE_UI_FADE_BOTTOM_WIDE,
              'x86 UI-overlay bottom becomes exact 222.0f')
        once = sha(work)
        check(B.apply_exe_ui_fade_bottom(work, log=lambda *_: None) == 0,
              'x86 data-only correction reapplies')
        check(sha(work) == once, 'x86 data-only correction is idempotent')
        check(B.apply_exe_ui_fade_bottom(work, revert=True,
                                         log=lambda *_: None) == 0,
              'x86 data-only correction reverts')
        check(work.read_bytes() == before,
              'x86 data-only correction reverts byte-exactly')

        check(B.apply_exe_ui_fade_x(work, log=lambda *_: None) == 0,
              'x86 battle UI-fade X widening applies')
        widened = work.read_bytes()
        wide_pe = exe_patch.parse_pe(widened)
        for va, spec in B.EXE_UI_FADE_X.items():
            xoff = exe_patch.va_to_offset(wide_pe, va)
            check(widened[xoff:xoff + 4] == spec['wide'],
                  'battle UI-fade %s constant is exact' % spec['name'])
        once = sha(work)
        check(B.apply_exe_ui_fade_x(work, log=lambda *_: None) == 0,
              'x86 battle UI-fade X widening reapplies')
        check(sha(work) == once, 'x86 UI-fade X widening is idempotent')
        check(B.apply_exe_ui_fade_x(work, revert=True,
                                    log=lambda *_: None) == 0,
              'x86 battle UI-fade X widening reverts')
        check(work.read_bytes() == before,
              'x86 battle UI-fade X widening reverts byte-exactly')

    # Exercise the actual late GUI-build pass with both outputs marked fresh.
    # Main must keep the proven flash/world-entry paths, leave both failed
    # UI-bottom candidates stock, and change only the measured producer's
    # arithmetic immediate.  ff7_en remains entirely stock here.
    with tempfile.TemporaryDirectory(prefix='battle-build-integration-') as td:
        sdout = Path(td) / 'sdout'
        main_out = (sdout / 'atmosphere/contents' / Build.TITLE_ID /
                    'exefs/main')
        exe_out = (sdout / 'atmosphere/contents' / Build.TITLE_ID /
                   Build.ROMFS_FF7 / Build.EXE_REL)
        main_out.parent.mkdir(parents=True)
        exe_out.parent.mkdir(parents=True)
        shutil.copy2(stock, main_out)
        shutil.copy2(stock_exe, exe_out)
        env = {
            Build.FIELD_FRAME_ENV: '0',
            Build.MODEL_CULL_ENV: '0',
            Build.MOVIE_ALIGN_ENV: '0',
            Build.ff7nx_moviebars.MOVIEBARS_ENV: '0',
            Build.ff7nx_camclamp.CAMCLAMP_ENV: '0',
            Build.BATTLE_WIDE_ENV: '1',
            Build.SWIRL_SCALE_ENV: '1',
            Build.UI_CLIP_ENV: '1',
            Build.CREDITS_ENV: '1',
            Build.ff7nx_moviecull.MOVIECULL_ENV: '',
        }
        dump = SimpleNamespace(nso=str(stock), exe=str(stock_exe))
        with patch.dict(os.environ, env, clear=False):
            Build.apply_field_frame(str(sdout), dump,
                                    log=lambda *_: None,
                                    produced=(str(main_out), str(exe_out)))
        integrated = nxmap.Main(str(main_out))
        check(all(w32(integrated.img, va) == (B.enc_movz(8, value)
                                              if B.want('quad-geometry')
                                              else B.QUAD_BOUND_STOCK)
                  for va, (value, _name) in B.QUAD_BOUND_INPUTS.items()),
              'GUI build wires the quad bounds to the current stage')
        check(all(w32(integrated.img, va) == word
                  for va, word in B.UI_FADE_BOTTOM.items()),
              'GUI build keeps ARM guest translations/loads stock')
        for hook, (_value, name) in B.MENU_FADE_TRANSITION_BOTTOM.items():
            hooked = B._branch_target(hook,
                                      w32(integrated.img, hook)) is not None
            check(hooked == B.want('transition-bottom'),
                  'GUI build leaves %s in its stage state' % name)
        for hook, name in B.UI_FADE_BOTTOM_SAFE.items():
            check(w32(integrated.img, hook) == B.UI_FADE_BOTTOM_SAFE_STOCK,
                  'GUI build leaves retired %s hook stock' % name)
        if B.want('ui-fade-bottom'):
            check(w32(integrated.img, B.UI_FADE_VISIBLE_BOTTOM) ==
                  B.UI_FADE_VISIBLE_BOTTOM_FIXED,
                  'GUI build installs measured UI-fade bottom correction')
            check(run_visible_ui_bottom(
                      w32(integrated.img, B.UI_FADE_VISIBLE_BOTTOM)) == 444.0,
                  'GUI UI-fade bottom correction executes to exact 444.0f')
        else:
            check(w32(integrated.img, B.UI_FADE_VISIBLE_BOTTOM) ==
                  B.UI_FADE_VISIBLE_BOTTOM_STOCK,
                  'GUI build leaves the measured UI-fade producer stock')
        exe_data = exe_out.read_bytes()
        exe_pe = exe_patch.parse_pe(exe_data)
        exe_off = exe_patch.va_to_offset(exe_pe,
                                         B.EXE_UI_FADE_BOTTOM_VA)
        check(exe_data[exe_off:exe_off + 4] == B.EXE_UI_FADE_BOTTOM_STOCK,
              'GUI build keeps the x86 UI-overlay bottom at stock 240.0f')
        for va, spec in B.EXE_UI_FADE_X.items():
            xoff = exe_patch.va_to_offset(exe_pe, va)
            check(exe_data[xoff:xoff + 4] == spec['stock'],
                  'GUI build restores retired UI-fade %s X edit' %
                  spec['name'])

    print('PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
