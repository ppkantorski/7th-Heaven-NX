#!/usr/bin/env python3
r"""
ff7nx_fieldzoom.py -- FFNx's WM_ZOOM for fields with no widescreen art
(BUILD 567). exefs/main only.

    SEVENTH_NX_FIELD_ZOOM=0              off
    SEVENTH_NX_FIELD_ZOOM=convil_2,...   the fields to zoom (default convil_2
                                         and ztruck; `name:1.25` sets a factor)

WHY
===
convil_2 -- Fort Condor's upstairs room and the view out of its window of
the condor on the reactor -- has no art outside 4:3: both background layers
span exactly -160..160, in Cosmos Limit Break and in the built archive, and
its camera range is 320, so FFNx itself shows it with side bars. FFNx's
answer for such a field is WM_ZOOM: scale the field 4/3 about the centre so
it fills the width, cropping equal bands top and bottom. The WS vertex scale
is 0.75, so 4/3 fills 1280 exactly; vertically 480 game px become 640, 80
cropped at each edge.

HOW (BUILD 568 -- BUILD 567's hook was in the wrong place)
===========================================================
The port submits draws immediately: every draw builds its BlockVertex
projection from the driver's viewport matrix at the moment it is issued
(+0x10D9D70, the one draw-submission helper -- ff7nx_daynight's notes). So
the zoom has to be in force WHILE the field scene is issued, and off again
before the text boxes are.

BUILD 567 scaled the matrix right after the field's viewport call at x86
0x63A9D1. That call is the LAST thing field_draw_everything does -- the scene
and the dialogue windows were already issued -- so the zoom never reached a
single draw, and the menu layer's own viewport reset it before the next
frame. Nothing zoomed.

Now it brackets the scene exactly where FFNx (and ff7nx_daynight) bracket
the time filter:

    ON   field_draw_everything, second word (+0x9E6E84; the first is
         ff7nx_daynight's): the field viewport from its own globals
         [0xCFF1E0..EC] is set in the driver (gfx_drv_setviewport,
         +0x10D6760) -- absolute, so it can never compound -- and the
         matrix's _11/_22/_41/_42 are scaled 4/3 (a zoom about the centre).
    OFF  the word after the gray-quads call (x86 0x63A96B -> 0x644E90,
         +0x9E7EEC; the word after it is daynight's, so +0x9E7EF4): the same
         field viewport is set again, unscaled. The dialogue windows
         (0x6F19F3, 0x6EBF2C, 0x6ECA68) come after this.

Both halves test the field (u16 at guest 0xCFF468, the id the field's
models belong to -- ff7nx_daynight's) against the list, so every other field
runs stock. The 3D projection is not rebuilt: models go through the same
driver matrix as the background (Fort Condor, BUILDs 566-567), so they zoom
with it.

BUILD 569 -- THE MATRIX IS REBUILT BY EVERY DRAW, SO THE ZOOM RIDES THE BLOCK
===========================================================================
568 installed and the field stayed 4:3. The draw-submission helper itself
(+0x10D9D70) answers why: with its w7 bit 0 set it RECOMPUTES the viewport
matrix at [*0x12CE668] +0xA8/+0xBC/+0xD8/+0xDC from the driver rect on
every draw (+0x10D9DC4..+0x10D9F2C) -- the same "rect x matrix" behaviour
Fort Condor's BUILDs 566-567 measured. A matrix scaled once at the top of
field_draw_everything was overwritten by the first draw after it.

So the zoom now lives where nothing can undo it: in the finished BlockVertex,
at the one word every draw passes (+0x10DA254, the join ff7nx_daynight's
BLOCK_JOINS pins; daynight's own tint hook is the word after, +0x10DA260).
The x and y rows of the column-major projectionMatrix (sp+0x28: elements
0,4,8,12 and 1,5,9,13) are multiplied by 4/3, i.e. clip x/y scaled about the
viewport centre. wide_screen/tlmain_vv.glsl then applies its 0.75 to clip.x,
and 0.75 x 4/3 = 1: the 4:3 art spans the full 16:9 width.

It is armed by a one-word BSS flag:
    ON   (field_draw_everything +4)   flag = (field id in the list)
    OFF  (after the gray quads)       flag = 0     -- dialogue is never zoomed
    DRAW (+0x10DA254)                 flag && port mode == 2 (FIELD)
The mode test means a frame that leaves the field between ON and OFF (a
battle, the menu) can never be drawn zoomed. No viewport call and no write to
the driver's matrix remain.
"""
from __future__ import annotations

import os
import struct

import a64 as A
from ff7nx_analog_cave import Asm

ENV = 'SEVENTH_NX_FIELD_ZOOM'
DEFAULT = ('convil_2', 'ztruck')
VERSION = 'fieldzoom-618z11'
# BUILD 618z11. Per-field factor. FFNx's WM_ZOOM shows 2 x (right - left)
# game px of the field's config range across the 16:9 width; the port's
# unzoomed 16:9 view is 320 / 0.75 = 426.67 units, so the factor is
# 426.67 / (right - left). convil_2 has no config range and keeps 4/3 (the
# 4:3 picture across the width). ztruck (Cosmos's config: mode = 2, left
# -175, right 175): 350 units across, as FFNx shows it -- the art is 352
# wide, so the unzoomed 427-unit view runs off it (hardware 10-06).
VIEW_W = 320.0 / 0.75
FACTORS = {'ztruck': VIEW_W / 350.0}

TRANSLATE = 0x10FC3A0
FIELD_ID = 0xCFF468                 # u16, the field the models belong to
#                                   (ff7nx_daynight's G_FIELD_ID)
FIELD_VP = 0xCFF1E0                 # x, y, w, h: the field viewport globals
DRV_SETVIEWPORT = 0x10D6760
MTX_GOT = 0x12CE668                 # -> driver viewport matrix block
MTX_WORDS = (0xA8, 0xBC, 0xD8, 0xDC)
ZOOM = 4.0 / 3.0                    # the default factor

ON_SITE = 0x9E6E84
ON_STOCK = 0xA9015FF8               # stp x24, x23, [sp, #0x10]
ON_BACK = 0x9E6E88
OFF_SITE = 0x9E7EF4
OFF_STOCK = 0x11002100              # add w0, w8, #8
OFF_BACK = 0x9E7EF8
ANCHORS = (
    (0x9E6E88, 0xA90257F6, 'stp x22, x21, [sp, #0x20]   prologue'),
    (0x9E6E90, 0xA9047BFD, 'stp x29, x30, [sp, #0x40]'),
    (0x9E7EEC, 0x94002DF9, 'bl x86 0x644E90   the gray quads'),
    (0x9E7EF8, 0x941C512A, 'bl translator'),
)
# BUILD 567's hook, recognised so a module carrying it is refused
OLD_SITE, OLD_STOCK = 0x9E80DC, 0xB94012A8
COND_NE = 1

DRAW_SITE = 0x10DA254               # adrp x24, 0x12ce000 -- every draw's join
DRAW_STOCK = 0x90000FB8
DRAW_BACK = 0x10DA258
DRAW_ANCHORS = (
    (0x10DA258, 0xF9428F18, 'ldr x24, [x24, #0x518]'),
    (0x10DA25C, 0x90000FBA, 'adrp x26, 0x12ce000'),
    (0x10DA290, 0x52800A06, 'mov w6, #0x50   the block size'),
)
DRAW_JOINS = (0x10DA094, 0x10DA548)  # both `b +0x10DA254`
MODE_SLOT = 0x12CE1F8               # -> the port's engine mode (u32)
MODE_FIELD = 2                      # ff7nx_daynight.DRIVER_FIELD
MATRIX_SP = 0x28                    # BlockVertex.projectionMatrix on the stack
XY_ROWS = (0, 4, 8, 12, 1, 5, 9, 13)
BSS_BYTES = 0x10


def enabled(env=None):
    """Only on a 16:9 build (the same gate as the minigame 16:9 work)."""
    if not fields(env):
        return False
    try:
        import ff7nx_minifade
        return ff7nx_minifade.enabled()
    except Exception:                                          # noqa: BLE001
        return False


def fields(env=None):
    raw = (os.environ if env is None else env).get(ENV, '').strip()
    if raw.lower() in ('0', 'off', 'no', 'false'):
        return ()
    if not raw or raw.lower() in ('1', 'on', 'yes', 'true'):
        return DEFAULT
    return tuple(f.split(':')[0].strip().lower() for f in raw.split(',')
                 if f.split(':')[0].strip())


def factor(name, env=None):
    """The zoom for one field: `name:1.25` in the variable, else FACTORS,
    else 4/3."""
    raw = (os.environ if env is None else env).get(ENV, '')
    for f in raw.split(','):
        if ':' in f and f.split(':')[0].strip().lower() == name:
            v = float(f.split(':', 1)[1])
            if not 1.0 <= v <= 2.0:
                raise ValueError('zoom %r for %s is outside 1..2' % (v, name))
            return v
    return FACTORS.get(name, ZOOM)


def _bits(v):
    return struct.unpack('<I', struct.pack('<f', v))[0]


def _pairs(ids):
    """[(field id, factor)] from ids or (id, factor) pairs."""
    out = []
    for i in ids:
        fid, z = (i if isinstance(i, (tuple, list)) else (i, ZOOM))
        out.append((int(fid), float(z)))
    return out


def maplist_index(flevel_path, names):
    """{name: maplist index} from the archive's own maplist."""
    import lgp
    arc = lgp.Archive(flevel_path)
    ml = [e for e in arc.entries if e['name'].lower() == 'maplist'][0]
    d = ml['payload']
    n = struct.unpack_from('<H', d, 0)[0]
    all_ = [d[2 + 32 * i:2 + 32 * i + 32].split(b'\0')[0].decode().lower()
            for i in range(n)]
    out = {}
    for nm in names:
        if nm not in all_:
            raise ValueError('field %r is not in the maplist' % nm)
        out[nm] = all_.index(nm)
    return out


def _movw(a, rd, v):
    a.emit(A.movz(rd, v & 0xFFFF))
    if v >> 16:
        a.emit(A.movk_hi(rd, v >> 16))


def _field_test(a, ids, miss):
    _movw(a, 0, FIELD_ID)
    a.emit(A.bl(a.pc(), TRANSLATE))
    a.emit(A.ldrh(8, 0))
    for fid in ids:
        a.emit(A.cmp_imm(8, fid))
        a.bcond('hit', 0)                          # b.eq
    a.b(miss)
    a.label('hit')


def _field_zoom(a, pairs, store):
    """w10 = the listed field's factor (float bits), or 0."""
    _movw(a, 0, FIELD_ID)
    a.emit(A.bl(a.pc(), TRANSLATE))
    a.emit(A.ldrh(8, 0))
    for k, (fid, _z) in enumerate(pairs):
        a.emit(A.cmp_imm(8, fid))
        a.bcond('hit%d' % k, 0)                    # b.eq
    a.emit(A.movz(10, 0))
    a.b(store)
    for k, (_fid, z) in enumerate(pairs):
        a.label('hit%d' % k)
        _movw(a, 10, _bits(z))
        a.b(store)


def _set_field_viewport(a):
    """gfx_drv_setviewport(field x, y, w, h) -- the four guest globals are
    read one translated address at a time and parked on the stack."""
    for i in range(4):
        _movw(a, 0, FIELD_VP + 4 * i)
        a.emit(A.bl(a.pc(), TRANSLATE))
        a.emit(A.ldr(8, 0))
        a.emit(A.str_(8, 31, 0x10 + 4 * i))
    for i in range(4):
        a.emit(A.ldr(i, 31, 0x10 + 4 * i))
    a.emit(A.bl(a.pc(), DRV_SETVIEWPORT))


def build_on(ids, bss):
    """flag = this field's zoom factor as float bits (0 = not listed).
    Recompiled code: x0, x8-x10 only, which the translator this word's
    neighbours call clobbers anyway."""
    pairs = _pairs(ids)

    def b(at, addr):
        a = Asm(at, addr)
        a.emit(A.stp64_pre(29, 30, 31, -0x20))
        _field_zoom(a, pairs, 'store')          # the translator eats x8-x10
        a.label('store')
        a.emit(A.adrp(9, a.pc(), bss & ~0xFFF))
        a.emit(A.add_imm64(9, 9, bss & 0xFFF))
        a.emit(A.str_(10, 9, 0))
        a.emit(A.ldp64_post(29, 30, 31, 0x20))
        a.emit(ON_STOCK)
        a.b('back')
        a.lab['back'] = ON_BACK
        return a.resolve()
    return b


def build_off(bss):
    """flag = 0. x9 is dead here: the next word but one is `bl` translator,
    which clobbers it."""
    def b(at, addr):
        a = Asm(at, addr)
        a.emit(A.adrp(9, a.pc(), bss & ~0xFFF))
        a.emit(A.add_imm64(9, 9, bss & 0xFFF))
        a.emit(A.str_(31, 9, 0))                    # wzr
        a.emit(OFF_STOCK)
        a.b('back')
        a.lab['back'] = OFF_BACK
        return a.resolve()
    return b


def build_draw(bss):
    """Native code, at the join. x24 and x26 are both reassigned by the next
    three stock words before anything reads them; s16/s17 are caller-saved
    and the `bl` five words later clobbers every FP register anyway. No
    instruction here other than `cmp` touches NZCV, and nothing downstream
    of the join reads the flags before setting them."""
    def b(at, addr):
        a = Asm(at, addr)
        a.emit(A.adrp(24, a.pc(), bss & ~0xFFF))
        a.emit(A.add_imm64(24, 24, bss & 0xFFF))
        a.emit(A.ldr(26, 24, 0))
        a.cbz(26, 'out')
        a.emit(A.adrp(24, a.pc(), MODE_SLOT & ~0xFFF))
        a.emit(A.ldr64(24, 24, MODE_SLOT & 0xFFF))
        a.emit(A.ldr(24, 24, 0))
        a.emit(A.cmp_imm(24, MODE_FIELD))
        a.bcond('out', COND_NE)
        a.emit(A.fmov_s_from_w(16, 26))         # the flag IS the factor
        for i in XY_ROWS:
            a.emit(A.ldr_s(17, 31, MATRIX_SP + 4 * i))
            a.emit(A.fmul_s(17, 17, 16))
            a.emit(A.str_s(17, 31, MATRIX_SP + 4 * i))
        a.label('out')
        a.emit(A.adrp(24, a.pc(), 0x12CE000))      # the displaced word
        a.b('back')
        a.lab['back'] = DRAW_BACK
        return a.resolve()
    return b


def _w(t, va):
    return struct.unpack_from('<I', t, va)[0]


def _hook(text, place, site, stock, bld):
    entry, placed = place(bld)
    for va, word in placed.items():
        struct.pack_into('<I', text, va, word)
    written = dict(placed)
    word = A.b(site, entry)
    struct.pack_into('<I', text, site, word)
    written[site] = word
    return written


def patch_text(text, place, ids, bss):
    t = bytes(text)
    bad = ['zoom anchor +%#x (%s) holds %08X' % (va, n, _w(t, va))
           for va, want, n in ANCHORS + DRAW_ANCHORS if _w(t, va) != want]
    for site, stock in ((ON_SITE, ON_STOCK), (OFF_SITE, OFF_STOCK),
                        (OLD_SITE, OLD_STOCK), (DRAW_SITE, DRAW_STOCK)):
        if _w(t, site) != stock:
            bad.append('zoom site +%#x holds %08X' % (site, _w(t, site)))
    for j in DRAW_JOINS:
        if _w(t, j) != A.b(j, DRAW_SITE):
            bad.append('zoom: +%#x no longer joins +%#x' % (j, DRAW_SITE))
    if bad:
        raise ValueError('; '.join(bad))
    if not ids or any(not 0 <= i < 4096 or not 1.0 <= z <= 2.0
                      for i, z in _pairs(ids)):
        raise ValueError('bad field ids %r' % (ids,))
    written = _hook(text, place, ON_SITE, ON_STOCK, build_on(ids, bss))
    written.update(_hook(text, place, OFF_SITE, OFF_STOCK, build_off(bss)))
    written.update(_hook(text, place, DRAW_SITE, DRAW_STOCK, build_draw(bss)))
    return written


def apply_to_nso(src, dest, ids, log=lambda *_: None, stock=None):
    import ff7nx_audio_cave as AC
    import ff7nx_deadspace as DS
    with open(src, 'rb') as fh:
        blob = fh.read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    lo, hi = DS.part(src, 'zoom', stock)
    bump = DS.Bump(lo, hi)

    def place(bld):
        before = dict(bump.placed)
        e = bump.put(bld)
        return e, {k: v for k, v in bump.placed.items() if k not in before}
    bss = AC.scratch_base(blob, segs)
    written = patch_text(text, place, ids, bss)
    raw[0] = bytes(text)
    out = AC.pack(blob, raw, BSS_BYTES)
    assert AC.segments(out)[0][0][2] == segs[0][2]
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as fh:
        fh.write(out)
    return {'words': len(written), 'bss': bss}
