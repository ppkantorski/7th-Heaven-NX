#!/usr/bin/env python3
"""
ff7nx_uitop.py -- BUILD 684: attacks never draw over the battle UI.

THE SYMPTOM
===========
Neo Bahamut (and other close-to-camera effects, now and then): for 3-4
frames the effect's white polygons cover the ESUI battle windows and their
text, often cut along a triangle edge. The finger cursor stays on top.

WHY (x86, ff7_en)
=================
The battle frame (0x41BAB3) draws, in this order: the 3D scene and effects,
the game's deferred list (0x666DF4), the z-sorted list (0x6656D1), the
remaining layers, and LAST the battle menu (`call 0x6CEE84` at 0x41BE2B),
then end_scene. Nothing 3D is drawn after the menu.

The menu's polygons are pre-transformed (screen x, y) with a fixed depth z:
text 0.301, windows 0.401, other parts 0.1-0.4, and they are depth tested.
The depth buffer still holds the attack's depth. Where an effect polygon was
nearer the camera than the UI's z (an expanding sphere passing the near
plane is nearer than 0.4 across the whole screen), the UI fragment fails
the test and the attack shows "in front". The finger has a smaller z, which
is why it survives. The cut along a triangle edge is one effect polygon's
outline.

THE CHANGE (exefs/main)
=======================
Clear the depth buffer, and only the depth buffer, right before the battle
menu draws. The UI's parts still depth-test against each other exactly as
before (same z values, same order), so they look the same. The 3D picture is
untouched (colour is not cleared). Nothing after the menu uses depth: the
frame ends.

The recompiled `call 0x6CEE84` is a `bl` in the translation of 0x41BAB3.
It becomes `bl cave`, and the 9-word cave calls the driver's own clear
(native gfx_drv_clear, resolved by name; w0 = colour 0, w1 = depth 1, the
same call the game makes with (0, 1, 1) when it clears depth alone), then
branches on into the menu draw, which returns to the original return point.

BUILD 686: THE CLEAR MUST HIT THE BATTLE'S DEPTH BUFFER
=======================================================
One frame of Neo Bahamut still covered the UI with 684. In this port the
driver's gfx_drv_clear clears the depth target in GOT slot 0x598, which is
the screen's own depth (flip binds it with the screen's colour to present).
The scene is not drawn there: begin_scene binds its own depth target (slot
0x5e8) as the current one (slot 0x640), every draw rebinds the current
pair, and neither battle pass (7 for the 3D, 8 for the UI, which the menu
draw sets first) switches the depth. So 684's clear left the battle's depth
as it was.

686 keeps the 684 call (it also sets the depth state the driver uses for a
clear) and then clears the *current* depth target, at 1.0, through the
same device wrapper begin_scene uses to clear it at the start of the frame.
The slots and the wrapper are read out of the native gfx_drv_clear and
gfx_drv_begin_scene (resolved by name), so the pass fails closed if the
driver differs.

BUILD 692: CLEAR BEFORE THE WHOLE UI, NOT BEFORE THE MENU ALONE
================================================================
A draw-order probe on hardware (ff7nx_drawprobe, BUILD 689-691) showed, for
every one of ~3,500 Neo Bahamut battle frames with 686: nothing is drawn
after the menu, and the menu draws identically each frame (same pass, same
depth and colour targets, no pending screen copy). But the battle UI is two
calls, not one: 0x6CE8B3 runs first in the same loop, sets the UI pass 8
and draws the windows; 0x6CEE84 (the menu) follows with the cursor and the
rest. 686 cleared between them, so the menu (and the finger) was always on
top while 0x6CE8B3's windows still depth-tested against the summon -- the
one-frame white over the windows. The cave now sits on the `bl` to
0x6CE8B3. Every UI part is drawn after the one clear, so the parts still
depth-test against each other exactly as before.

SEVENTH_NX_UI_TOP=0 leaves it stock.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os as _nx_os, sys as _nx_sys  # BUILD 755: run directly, find src/
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
if _nx_os.path.join(_nx_root, 'src') not in _nx_sys.path:
    _nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install()
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

_HERE = nxpaths.ROOT
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import a64 as A  # noqa: E402

VERSION = 692
ENV = 'SEVENTH_NX_UI_TOP'
BATTLE_FRAME_X86 = 0x41BAB3     # the battle frame's draw function
MENU_DRAW_X86 = 0x6CEE84        # the battle menu's draw, called last
# BUILD 692: the battle UI's FIRST draw, 0x6CE8B3 (it sets the UI pass 8 and
# draws the windows); the menu draw 0x6CEE84 follows it. The depth is
# cleared before this one, so every UI part is drawn after the clear.
UI_DRAW_X86 = 0x6CE8B3
SITE_TARGET_X86 = UI_DRAW_X86
CLEAR_NAME = b'gfx_drv_clear'
BEGIN_NAME = b'gfx_drv_begin_scene'
# native gfx_drv_clear: colour flag in w0, depth flag in w1
CLEAR_ARGS = ((0x24, 0x2A0003F3), (0x2C, 0x2A0103F4))   # mov w19,w0 / mov w20,w1
# the guest `esp -= 4` the recompiler emits before every call
PRE = (0xB9401388, 0x51001108, 0xB9001388)   # ldr/sub/str w8,[x28,#0x10]
# BUILD 692: the call to 0x6CE8B3 is a branch target (the loop's other
# path joins at the `sub`), so only the `sub`/`str` pair is required
PRE2 = PRE[1:]

STP_FP_LR = 0xA9BF7BFD          # stp x29, x30, [sp, #-0x10]!
MOV_FP_SP = 0x910003FD          # mov x29, sp
LDP_FP_LR = 0xA8C17BFD          # ldp x29, x30, [sp], #0x10
FMOV_S0_ONE = 0x1E2E1000        # fmov s0, #1.0
MOV_V0_V8 = 0x4EA81D00          # mov v0.16b, v8.16b (begin_scene: s8 = 1.0)
LDR_X1_X8 = 0xF9400101          # ldr x1, [x8]
LDR_X0_X20 = 0xF9400280         # ldr x0, [x20]
LDR_X3_X21 = 0xF94002A3         # ldr x3, [x21]
LDR_X1_X21 = 0xF94002A1         # ldr x1, [x21]
STR_X3_X8 = 0xF9000103          # str x3, [x8]
# the device wrapper that clears one depth target: vtable slot 0xA8
DEPTH_WRAPPER = (0xF9400000, 0xF9400008, 0xF9405502, 0xD61F0040)
CAVE_WORDS = 17


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(ENV, '1').strip().lower() not in ('0', 'off', 'no', 'false')


def _w(img, va):
    return struct.unpack_from('<I', img, va)[0]


def _bl_target(word, va):
    if (word & 0xFC000000) != 0x94000000:
        return None
    off = word & 0x03FFFFFF
    if off & 0x02000000:
        off -= 0x04000000
    return va + 4 * off


def native_fn(m, name):
    """Addresses of the native driver functions named `name`."""
    img, out = m.img, []
    for k, v in m.rel.items():
        if v >= len(img) or img[v:v + len(name) + 1] != name + b'\0':
            continue
        fn = m.rel.get(k + 8)
        if fn is not None and fn < len(m.text):
            out.append(fn)
    return out


def native_clear(m):
    """Address of native gfx_drv_clear, from the driver's name table."""
    for fn in native_fn(m, CLEAR_NAME):
        if all(_w(m.img, fn + o) == x for o, x in CLEAR_ARGS):
            return fn
    return None


def _adrp_page(word, va):
    """Page an `adrp` loads, or None."""
    if (word & 0x9F000000) != 0x90000000:
        return None
    imm = (((word >> 5) & 0x7FFFF) << 2) | ((word >> 29) & 3)
    if imm & (1 << 20):
        imm -= 1 << 21
    return (va & ~0xFFF) + (imm << 12)


def _ldr64_off(word, rt, rn):
    """Offset of `ldr xRT, [xRN, #off]`, or None."""
    if (word & 0xFFC003FF) != (0xF9400000 | (rn << 5) | rt):
        return None
    return ((word >> 10) & 0xFFF) * 8


def _slot(img, va, rd):
    """GOT slot loaded by `adrp xRD, page` at va - 4 and `ldr xRD, [xRD, #off]` at va."""
    page = _adrp_page(_w(img, va - 4), va - 4)
    off = _ldr64_off(_w(img, va), rd, rd)
    if page is None or off is None or (_w(img, va - 4) & 31) != rd:
        return None
    return page + off


def depth_refs(m, clear):
    """(device slot, current-depth slot, depth-clear wrapper) or a reason.

    From gfx_drv_clear: its depth clear is `ldr x0, [x20]` (x20 = the device
    slot) / `ldr x8, [x8, #screen depth]` / `ldr x1, [x8]` / `fmov s0, #1.0`
    / `bl wrapper`. From gfx_drv_begin_scene: `ldr x3, [x21]` (the scene's
    depth) is stored through `ldr x8, [x8, #current]` / `str x3, [x8]`, and
    the same [x21] is cleared through the same wrapper."""
    img = m.img
    dev = wrap = None
    for va in range(clear, clear + 0x130, 4):
        if _w(img, va) != FMOV_S0_ONE or _w(img, va - 4) != LDR_X1_X8:
            continue
        if _w(img, va - 12) != LDR_X0_X20:
            continue
        wrap = _bl_target(_w(img, va + 4), va + 4)
        for back in range(va - 16, clear, -4):
            if _ldr64_off(_w(img, back), 20, 20) is not None:
                dev = _slot(img, back, 20)
                break
        break
    if dev is None or wrap is None:
        return 'depth clear not found in gfx_drv_clear +0x%X' % clear
    if tuple(_w(img, wrap + 4 * i) for i in range(4)) != DEPTH_WRAPPER:
        return 'depth-clear wrapper +0x%X changed' % wrap
    begins = native_fn(m, BEGIN_NAME)
    if len(begins) != 1:
        return '%d gfx_drv_begin_scene in the driver table' % len(begins)
    begin = begins[0]
    depth = cleared = None
    for va in range(begin, begin + 0x1C0, 4):
        w = _w(img, va)
        # adrp x8, page / ldr x3, [x21] / ldr x8, [x8, #off] / str x3, [x8]
        if (w == LDR_X3_X21 and depth is None
                and _w(img, va + 8) == STR_X3_X8
                and (_w(img, va - 4) & 31) == 8):
            page = _adrp_page(_w(img, va - 4), va - 4)
            off = _ldr64_off(_w(img, va + 4), 8, 8)
            if page is not None and off is not None:
                depth = page + off
        if (w == LDR_X1_X21 and _w(img, va + 4) == MOV_V0_V8
                and _bl_target(_w(img, va + 8), va + 8) == wrap):
            cleared = va
    if depth is None:
        return 'current depth slot not found in gfx_drv_begin_scene +0x%X' % begin
    if cleared is None:
        return 'gfx_drv_begin_scene +0x%X does not clear its depth through +0x%X' % (begin, wrap)
    return dev, depth, wrap


def cave(entry, addr, clear, menu, refs):
    dev, depth, wrap = refs
    return [STP_FP_LR, MOV_FP_SP,
            A.movz(0, 0), A.movz(1, 1), A.movz(2, 0), A.movz(3, 0),
            A.bl(addr(6), clear),                       # the 684 call
            A.adrp(8, addr(7), dev), A.ldr64(8, 8, dev & 0xFFF),
            A.ldr64(0, 8, 0),                           # x0 = device
            A.adrp(8, addr(10), depth), A.ldr64(8, 8, depth & 0xFFF),
            A.ldr64(1, 8, 0),                           # x1 = current depth
            FMOV_S0_ONE,
            A.bl(addr(14), wrap),                       # clear it to 1.0
            LDP_FP_LR,
            A.b(addr(16), menu)]


def _b_target(word, va):
    if (word & 0xFC000000) != 0x14000000:
        return None
    off = word & 0x03FFFFFF
    if off & 0x02000000:
        off -= 0x04000000
    return va + 4 * off


def is_cave(img, va, clear, menu, refs):
    """True if the cave, laid out over chained holes, starts at `va`."""
    addrs, p = [], va
    for _ in range(4 * CAVE_WORDS):
        if len(addrs) == CAVE_WORDS:
            break
        w = _w(img, p)
        t = _b_target(w, p)
        # a `b` is the chain link between holes, except the cave's last
        # word, which is the `b` into the menu draw
        if t is not None and (len(addrs) < CAVE_WORDS - 1 or t != menu):
            p = t
            continue
        addrs.append(p)
        p += 4
    if len(addrs) != CAVE_WORDS:
        return False
    want = cave(va, lambda i: addrs[i], clear, menu, refs)
    return all(_w(img, a) == x for a, x in zip(addrs, want))


def find_site(m):
    """(site va, menu draw va) or a reason string."""
    menu = m.x86_to_arm.get(SITE_TARGET_X86)
    if menu is None:
        return 'UI draw 0x%X not in the recompilation map' % SITE_TARGET_X86
    if BATTLE_FRAME_X86 not in m.x86_to_arm:
        return 'battle frame 0x%X not in the recompilation map' % BATTLE_FRAME_X86
    a, b = m.extent(BATTLE_FRAME_X86)
    img = m.img
    hits = []
    for va in range(a, b, 4):
        t = _bl_target(_w(img, va), va)
        if t is None:
            continue
        if t == menu:
            hits.append((va, 'stock'))
        elif t >= len(m.text) or not (a <= va < b):
            continue
        else:
            hits.append((va, t))
    stock = [va for va, t in hits if t == 'stock']
    if len(stock) == 1:
        va = stock[0]
        if tuple(_w(img, va - 8 + 4 * i) for i in range(2)) != PRE2:
            return 'context before +0x%X changed' % va
        return va, menu, 'stock'
    if not stock:
        # already patched: a bl from the same spot into a cave that ends in
        # `b menu`
        clear = native_clear(m)
        refs = depth_refs(m, clear) if clear is not None else None
        for va, t in hits:
            if (t != 'stock' and isinstance(refs, tuple)
                    and tuple(_w(img, va - 8 + 4 * i) for i in range(2)) == PRE2
                    and is_cave(img, t, clear, menu, refs)):
                return va, menu, 'patched'
        return 'no call to the menu draw in the battle frame'
    return '%d calls to the menu draw in the battle frame' % len(stock)


def plan(m):
    import cave_space
    import ff7nx_cave
    got = find_site(m)
    if isinstance(got, str):
        return [], [], [got]
    site, menu, state = got
    if state == 'patched':
        return [], ['    battle UI on top: already'], []
    clear = native_clear(m)
    if clear is None:
        return [], [], ['native gfx_drv_clear not found by name']
    refs = depth_refs(m, clear)
    if isinstance(refs, str):
        return [], [], [refs]
    pool = ff7nx_cave.HolePool(
        m.img, starts=set(m.arm_starts),
        named=cave_space.named_targets(m.img[cave_space.RODATA:]))
    try:
        entry, words = ff7nx_cave.emit_laid_out(
            pool, lambda e, addr: cave(e, addr, clear, menu, refs),
            span=ff7nx_cave.ANY_SPAN)
    except ff7nx_cave.NoRoom as exc:
        return [], [], ['UI on top cave: %s' % exc]
    words = dict(words)
    words[site] = A.bl(site, entry)
    patches = []
    for va, wd in sorted(words.items()):
        if _w(m.img, va) == wd:
            continue
        patches.append({'name': 'battle UI on top', 'va': hex(va),
                        'expect': struct.pack('<I', _w(m.img, va)).hex(),
                        'set': struct.pack('<I', wd).hex()})
    notes = ['    battle UI on top: battle depth cleared before the battle '
             'UI draws (+0x%X -> cave +0x%X, %d words in %d hole(s); clear +0x%X, '
             'then current depth slot +0x%X through +0x%X)'
             % (site, entry, len(words) - 1, len(pool.used), clear,
                refs[1], refs[2])]
    return patches, notes, []


def apply_to_nso(src, dest, log=print):
    """Write `src` patched to `dest`. True if dest was written."""
    import nso_patcher
    import nxmap
    m = nxmap.Main(str(src))
    patches, notes, problems = plan(m)
    for n in notes:
        log(n)
    if problems:
        for p in problems:
            log('  ! battle UI on top: ' + p)
        log('  battle UI on top: nothing written')
        return False
    if not patches:
        return False
    nso = nso_patcher.read_nso(Path(src))
    nso_patcher.apply_spec(nso, {'name': 'ui top', 'patches': patches})
    fd, tmp = tempfile.mkstemp(dir=str(Path(dest).parent), prefix='.uitop-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(dest))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  battle UI on top: %d word(s)' % len(patches))
    return True


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('src')
    ap.add_argument('dest')
    a = ap.parse_args()
    raise SystemExit(0 if apply_to_nso(a.src, a.dest) else 1)
