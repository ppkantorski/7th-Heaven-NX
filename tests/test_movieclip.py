#!/usr/bin/env python3
"""
Tests for ff7nx_movieclip (v3).

Layer 0 is the one that matters, and it exists because two builds were spent
on vtable slots. It does NOT check a slot -- it checks that the hook sits on
the chain that provably ends in glScissor, which is a property of the module
rather than of whichever renderer class happens to be constructed:

    +0x1133FE8  mov w1, w8         <- the hook
    +0x1133FEC  b   +0x11521C0     <- PLT stub
                GOT +0xE68 = glScissor   (named from .rela.dyn + DT_SYMTAB)

Layer 3 is the other lesson: with is_playing = 0 the box must come out
BYTE-IDENTICAL, on every rect. The ungated probe cut the save-select screen
in half, and that is exactly what this must never do.

    python3 tests/test_movieclip.py [path/to/main]

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
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import ff7nx_movieclip as mc                                     # noqa: E402

FAIL, N, SKIPS = [], [0], []
TAIL_GAP = (0x1152660, 0x1153000)
MAIN_CANDIDATES = ('dump/exefs/main', 'exefs/main')
GOT_BASE, GLSCISSOR_SLOT = 0x12CD000, 0xE68


def check(c, what):
    N[0] += 1
    if not c:
        FAIL.append(what)


def eq(got, want, what):
    check(got == want, '%s: got %r want %r' % (what, got, want))


def find_main(argv):
    if len(argv) > 1:
        return argv[1]
    for c in MAIN_CANDIDATES:
        p = os.path.join(ROOT, c)
        if os.path.exists(p):
            return p
    return None


# --------------------------------------------------------------------- 0
def test_the_hook_reaches_glscissor(m):
    print('\n0. the hook is on the glScissor chain (not a vtable slot)')
    W = lambda va: struct.unpack_from('<I', m.text, va)[0]      # noqa: E731

    eq(mc.HOOK_VA, 0x1133FE8, 'the hook is at +0x1133FE8')
    eq(W(mc.HOOK_VA), mc.HOOK_ORIG, 'and it holds `mov w1, w8`')

    # +0x1133FEC must be `b` to the PLT stub
    w = W(mc.HOOK_VA + 4)
    eq(w & 0xFC000000, 0x14000000, 'the next word is a plain `b` (tail call)')
    off = w & 0x03FFFFFF
    off = off - (1 << 26) if off & (1 << 25) else off
    stub = mc.HOOK_VA + 4 + 4 * off
    eq(stub, 0x11521C0, 'it tail-calls the stub at +0x11521C0')

    # the stub loads a GOT slot; that slot must be named glScissor
    eq(W(stub) & 0x9F000000, 0x90000000, 'the stub starts with adrp')
    imm = (((W(stub) >> 5) & 0x7FFFF) << 2) | ((W(stub) >> 29) & 3)
    if imm & (1 << 20):
        imm -= (1 << 21)
    page = (stub & ~0xFFF) + (imm << 12)
    got_off = ((W(stub + 4) >> 10) & 0xFFF) * 8
    eq(page + got_off, GOT_BASE + GLSCISSOR_SLOT,
       'and loads GOT +0x%X' % GLSCISSOR_SLOT)

    # ... named from the dynamic symbol table, not from a comment
    mod0 = struct.unpack('<I', m.img[4:8])[0]
    dyn = mod0 + struct.unpack('<i', m.img[mod0 + 4:mod0 + 8])[0]
    tags, p = {}, dyn
    while True:
        tag, val = struct.unpack('<qQ', m.img[p:p + 16])
        if tag == 0:
            break
        tags.setdefault(tag, val)
        p += 16
    SYMTAB, STRTAB, SYMENT = tags[6], tags[5], tags.get(11, 24)

    def sym(idx):
        o = struct.unpack('<I', m.img[SYMTAB + idx * SYMENT:
                                      SYMTAB + idx * SYMENT + 4])[0]
        e = m.img.find(b'\0', STRTAB + o)
        return m.img[STRTAB + o:e].decode()

    names = {}
    for base, size in ((tags[7], tags[8]), (tags.get(23), tags.get(2))):
        if not base:
            continue
        for i in range(size // tags[9]):
            o, info, _a = struct.unpack('<QQq', m.img[base + i * tags[9]:
                                                      base + (i + 1) * tags[9]])
            names[o] = sym(info >> 32)
    eq(names.get(GOT_BASE + GLSCISSOR_SLOT), 'glScissor',
       'GOT +0xE68 is glScissor, per .rela.dyn')

    check(mc.HOOK_VA not in (0x10D9F34, 0x10D9F50),
          'and it is NOT either of the two vtable-slot sites v1/v2 used')


# --------------------------------------------------------------------- 1
def test_encodings():
    print('\n1. the cave encodes what it says it encodes')
    check(mc.check_encoding(lambda s: FAIL.append(s)),
          'every word round-trips through capstone')
    base = 0x1000
    words = mc.cave_words(lambda i: base + 4 * i, base + 0x100)
    eq(len(words), mc.N_WORDS, 'the cave is N_WORDS long')
    eq(words[mc.I_SKIP], mc.HOOK_ORIG,
       'the displaced `mov w1, w8` is the last word before the return branch')
    skip = base + 4 * mc.I_SKIP
    for i in (2, 4, 6):
        off = (words[i] >> 5) & 0x7FFFF
        off = off - (1 << 19) if off & (1 << 18) else off
        eq(base + 4 * i + 4 * off, skip, 'guard at word %d -> SKIP' % i)
    # nothing in the body may write x1: it is the caller's rect pointer and
    # the displaced instruction is what is allowed to overwrite it.
    for i, w in enumerate(words[:mc.I_SKIP]):
        check((w & 31) != 1, 'body word %d does not write x1/w1' % i)


# --------------------------------------------------------------------- 2
def test_band():
    print('\n2. the band, EXECUTED, on real glScissor rects')
    for x, w, what in mc.BOXES:
        got = mc.emulate(x, 48, w, 672, playing=True)
        wx, ww = mc.band(x, w)
        eq((got['x'], got['w']), (wx, ww),
           '%d,%d -> %d,%d  (%s)' % (x, w, wx, ww, what))
        eq((got['y'], got['h']), (48, 672), '%d,%d leaves y and h alone'
           % (x, w))
        # exact for widths that are a multiple of 8; one pixel of integer
        # truncation otherwise, which is the whole error budget of two shifts
        drift = (got['x'] + got['w'] // 2) - (x + w // 2)
        check(abs(drift) <= 1,
              '%d,%d keeps the centre (drift %d px)' % (x, w, drift))
        if w % 8 == 0:
            eq(drift, 0, '%d,%d is a multiple of 8, so the centre is exact'
               % (x, w))
    got = mc.emulate(0, 48, 1280, 672, playing=True)
    eq((got['x'], got['x'] + got['w']), (160, 1120),
       'the shipping build: x 160..1120, the 4:3 core of a 1280 buffer')
    eq(mc.band(0, 1280), (160, 960),
       'which is where WS_SCALE 0.75 puts game x 0..640')


# --------------------------------------------------------------------- 3
def test_gated_off_is_byte_identical():
    print('\n3. with no movie playing NOTHING may move (the probe lesson)')
    for x, w, what in mc.BOXES:
        for y, h in ((48, 672), (0, 720), (0, 1080)):
            got = mc.emulate(x, y, w, h, playing=False)
            eq((got['x'], got['y'], got['w'], got['h']), (x, y, w, h),
               'is_playing=0: %d,%d,%d,%d untouched (%s)'
               % (x, y, w, h, what))
    for playing in (True, False):
        got = mc.emulate(0, 48, 1280, 672, playing=playing)
        eq(got['w1'], 48,
           'is_playing=%d: w1 = y on the way out, so glScissor gets its '
           'second argument' % playing)


# --------------------------------------------------------------------- 4
def test_against_the_module(m, path):
    print('\n4. the real module: anchors, placement, and the walked chain')
    for va, want, what in mc.ANCHORS:
        eq(struct.unpack_from('<I', m.text, va)[0], want,
           '+0x%07X %s' % (va, what.split('  ')[0]))
    eq(mc.state(m.text), 'stock', 'the dump is unpatched')

    words = mc.cave_patches(m.img, set(m.arm_starts), lambda *_: None)
    check(mc.HOOK_VA in words, 'the hook site is patched')
    for va in words:
        check(not (TAIL_GAP[0] <= va < TAIL_GAP[1]),
              '+0x%07X is outside the 60 FPS tail gap' % va)
        if va != mc.HOOK_VA:
            eq(struct.unpack_from('<I', m.img, va)[0], 0,
               '+0x%07X was zero padding' % va)

    img = bytearray(m.img)
    for va, word in words.items():
        struct.pack_into('<I', img, va, word)

    def W(va):
        return struct.unpack_from('<I', img, va)[0]

    h = W(mc.HOOK_VA)
    eq(h & 0xFC000000, 0x14000000, 'the hook site holds a plain `b`')
    o = h & 0x03FFFFFF
    o = o - (1 << 26) if o & (1 << 25) else o
    pc, ran, skips = mc.HOOK_VA + 4 * o, [], set()
    for _ in range(400):
        w = W(pc)
        if (w & 0xFC000000) == 0x14000000:
            o = w & 0x03FFFFFF
            o = o - (1 << 26) if o & (1 << 25) else o
            nxt = pc + 4 * o
            if nxt == mc.HOOK_VA + 4:
                ran.append((pc, w))
                break
            pc = nxt
            continue
        ran.append((pc, w))
        if (w & 0x7E000000) == 0x34000000 or (w & 0xFF000000) == 0x54000000:
            o = (w >> 5) & 0x7FFFF
            o = o - (1 << 19) if o & (1 << 18) else o
            skips.add(pc + 4 * o)
        pc += 4
    else:
        FAIL.append('the cave never returned in 400 steps')

    eq(len(ran), mc.N_WORDS, 'the walk executes every cave word')
    eq(len(skips), 1, 'every guard branches to ONE address')
    addrs = [a for a, _ in ran]
    ref = mc.cave_words(lambda i: addrs[i], mc.HOOK_VA + 4)
    eq([w for _, w in ran], ref,
       'every word is identical to cave_words() for its real address')
    if skips:
        eq(list(skips)[0], addrs[mc.I_SKIP],
           'the guards land on the displaced instruction')

    import tempfile
    import nxmap
    with tempfile.TemporaryDirectory() as td:
        tmp = os.path.join(td, 'main')
        ok = mc.apply_to_nso(path, tmp, lambda *_: None)
        check(ok, 'apply_to_nso writes a module')
        if ok:
            m2 = nxmap.Main(tmp)
            eq(mc.state(m2.text), 'patched', 'it reads back as patched')
            check(mc.verify_anchors(m2.text, lambda *_: None),
                  'and its anchors still pass')
            back = os.path.join(td, 'back')
            check(mc.revert(tmp, back, lambda *_: None), 'revert works')
            m3 = nxmap.Main(back)
            eq(mc.state(m3.text), 'stock', 'and puts the hook site back')


def main(argv):
    print('ff7nx_movieclip v3 -- HANDOFF-80 §5.0')
    path = find_main(argv)
    m = None
    if path:
        import nxmap
        m = nxmap.Main(path)
        print('main    : %s' % path)
        test_the_hook_reaches_glscissor(m)
    else:
        SKIPS.append('the module tests (no exefs/main found)')
    test_encodings()
    test_band()
    test_gated_off_is_byte_identical()
    if m is not None:
        test_against_the_module(m, path)

    print('')
    for f in FAIL:
        print('FAIL  %s' % f)
    for s in SKIPS:
        print('SKIP  %s' % s)
    print('%d checks passed, %d failed, %d group(s) skipped'
          % (N[0] - len(FAIL), len(FAIL), len(SKIPS)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
