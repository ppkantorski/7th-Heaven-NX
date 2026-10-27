#!/usr/bin/env python3
"""
tests/test_fbcapture.py -- the framebuffer-capture rect transform.

The claims worth protecting are not "the patch applies". They are:

  1. the function this hooks is reachable from ONE place, so it cannot touch
     an ordinary texture;
  2. the thunk's register assignment -- w1 = width, w3 = x, w5 = width*xscale
     -- which is the contract the cave depends on;
  3. the surface it captures from really is 640x480, and the arithmetic it
     feeds really is the identity;
  4. the cave, EXECUTED: 1:1 rects are corrected, page-scaled rects
     (xscale > 1, the battle-entry swirl) come out bit-identical;
  5. the registers the cave uses are dead and it writes no flags between the
     `cmp` and the two `csel`s;
  6. apply/revert is byte-exact and the cave footprint is fully returned.

Point 4 is build 244's regression, encoded so it cannot come back.

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

import pytest


# BUILD 338: the cave's LENGTH now depends on the capture scale -- a scaled
# capture needs two more words to keep Kujata's origin correction. Every test
# in this file is about the STOCK cave, so pin the scale rather than inherit
# whatever another test module left in the environment.
@pytest.fixture(autouse=True)
def _stock_capture_scale(monkeypatch):
    import importlib
    import ff7nx_fbcapture as _FBC
    import ff7nx_fxcapscale as _CS
    monkeypatch.setenv('SEVENTH_NX_FX_CAPSCALE', '1')
    monkeypatch.delenv('SEVENTH_NX_FX_CAPSCALE_Y', raising=False)
    importlib.reload(_CS)
    importlib.reload(_FBC)
    yield

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import a64 as A                                                # noqa: E402
import ff7nx_fbcapture as FB                                   # noqa: E402

MAIN = os.path.join(_HERE, 'game_data_files', 'exefs', 'main')

pytestmark = pytest.mark.skipif(
    not os.path.exists(MAIN), reason='needs game_data_files/exefs/main')


@pytest.fixture(scope='module')
def module():
    import nxmap
    return nxmap.Main(MAIN)


@pytest.fixture(scope='module')
def img(module):
    return module.img


def word(img, va):
    return struct.unpack_from('<I', img, va)[0]


# ---------------------------------------------------------------- reachability
def test_make_framebuffer_tex_has_exactly_one_way_in(module):
    """
    +0x10DBB50 must be reached only by the tail branch this file hooks, whose
    own only caller is the recompiled guest sub_673F5C. That is what makes
    the hook safe: no ordinary texture load can see it.
    """
    t = module.text

    def branches_to(target):
        out = []
        for p in range(0, len(t) - 3, 4):
            w = struct.unpack_from('<I', t, p)[0]
            if (w >> 26) in (0b000101, 0b100101):
                off = w & 0x3FFFFFF
                if off & (1 << 25):
                    off -= 1 << 26
                if p + off * 4 == target:
                    out.append(p)
        return out

    assert branches_to(FB.RETURN_VA) == [FB.HOOK]
    assert branches_to(0x10F2380) == [0xB0166C]
    assert module.x86_to_arm.get(0x673F5C) == 0xB015C0


def test_the_thunk_is_the_contract(img):
    """w1 = width, w3 = x, w5 = width*xscale. The cave reads all three."""
    for va, w in sorted(FB.THUNK.items()):
        assert word(img, va) == w, hex(va)
    assert word(img, FB.HOOK) == FB.HOOK_STOCK
    # w5 = w8 * w1 with w8 = xscale, so w5 == w1 iff xscale == 1
    assert word(img, 0x10F2394) == A.mul(5, 8, 1)


def test_the_arithmetic_this_corrects_is_the_identity(img):
    """This file exists BECAUSE make_framebuffer_tex is the identity. If a
    game update makes it anything else the cave would double-correct, so it
    is an anchor, not a patch -- and it must stay stock after applying."""
    for va, w in sorted(FB.IDENTITY.items()):
        assert word(img, va) == w, hex(va)
    magic = 0xCCCCCCCD
    for n in (0, 1, 320, 640, 854, 4095):
        assert ((n * 640) * magic) >> 41 == n


def test_the_staging_surface_is_640x480(img):
    for va, w in sorted(FB.SURFACE.items()):
        assert word(img, va) == w, hex(va)
    assert (FB.STAGING_W, FB.STAGING_H) == (640, 480)


# ------------------------------------------------------------------ arithmetic
def test_geometry_comes_from_ff7nx_ws():
    import ff7nx_ws
    span, off = FB.geometry()
    assert span == ff7nx_ws.WIDE_VIEWPORT_WIDTH == 854
    assert off == -ff7nx_ws.WIDE_VIEWPORT_X == 107


def test_reciprocal_is_exact_not_approximate():
    magic, shift = FB.reciprocal()
    span = FB.geometry()[0]
    assert magic <= 0xFFFFFFFF
    for n in range(0, 65536):
        assert (n * magic) >> shift == (n * FB.STAGING_W) // span


# ------------------------------------------------------- the cave, EXECUTED
def test_one_to_one_captures_have_their_origin_corrected():
    body = FB.body_words()
    # Kujata's animated field and Titan's slab: the origin moves to the
    # staging column game x = 0 really lands on, and the width is untouched.
    assert FB.run_cave(body, 0, 256, 256) == (80, 256)
    assert FB.run_cave(body, 0, 512, 512) == (80, 512)
    # a sub-rect with a real origin moves by the same transform
    assert FB.run_cave(body, 200, 256, 256) == FB.map_rect(200, 256)
    # and the clamp keeps a rect that cannot be moved where it already is
    assert FB.run_cave(body, 0, 640, 640) == (0, 640)


def test_the_width_is_never_scaled():
    """
    FINDINGS-308. `fb_tex.w` IS the capture texture's pixel width (FFNx
    src/gl/texture.cpp:68; this port allocates w*h*4 at +0x10D70B8 and sizes
    the GPU target from it at +0x10D7264), and the effects' UVs are authored
    against the width they asked for. Shrinking it is what turned every
    texel past u = w' black -- on one side, horizontally only, in whole
    grid cells. Builds 245-266 did that; nothing may reintroduce it.
    """
    body = FB.body_words()
    for width in (128, 192, 222, 256, 512, 640):
        for x in (-107, 0, 160, 320, 480, 640, 747):
            assert FB.run_cave(body, x, width, width)[1] == width
    assert FB.map_rect(0, 256) == (80, 256)
    assert not hasattr(FB, 'map_w')            # the scaler itself is gone


def test_every_one_to_one_rect_is_filled_to_its_last_pixel():
    """
    The complement of the test above: with the origin clamped into
    0..640-w, the CPU branch's `min(surface_w, x+w) - x` covers the whole
    destination, so no row keeps an uninitialised tail. Every capture rect
    the executable builds is 128, 192, 222, 256 or 512 wide, so the entire
    class satisfies this.
    """
    body = FB.body_words()
    for width in (128, 192, 222, 256, 512, 640):
        for x in (-107, 0, 160, 320, 480, 640, 747):
            gx, gw = FB.run_cave(body, x, width, width)
            assert 0 <= gx <= gx + gw <= FB.STAGING_W, (x, width, gx, gw)


def test_page_scaled_captures_are_bit_identical():
    """
    Build 244's regression, encoded. xscale > 1 means PSX page arithmetic:
    the width/height fields are rounded up to powers of two and the rect is
    deliberately larger than the frame, with the effect's own UVs addressing
    the result. Scaling those moved every UV constant and made one half of
    the battle-entry swirl discontinuous with the other.
    """
    body = FB.body_words()
    for x, w, width in ((0, 512, 256),        # the swirl, tile A
                        (320, 512, 256),      # the swirl, tile B
                        (0, 1024, 512),
                        (64, 256, 128),
                        (0, 960, 320)):
        assert FB.run_cave(body, x, w, width) == (x & 0xFFFFFFFF, w), (x, w)


def test_arm64emu_executes_the_real_words_and_agrees():
    """
    The offline interpreter in ff7nx_fbcapture is a model. arm64emu decodes
    the ACTUAL ENCODED WORDS, so a wrong register field or a wrong condition
    code shows up here rather than on hardware. Scratch registers start full
    of garbage; w1/w5 must come back untouched.
    """
    import arm64emu
    body = FB.body_words()
    cases = [(0, 256, 256), (0, 512, 512), (200, 256, 256), (0, 640, 640),
             (-107, 854, 854), (0, 512, 256), (320, 512, 256),
             (0, 1024, 512), (64, 256, 128), (480, 128, 128),
             (600, 222, 222), (-107, 192, 192), (0, 128, 128)]
    for x, w, width in cases:
        cpu = arm64emu.Cpu(arm64emu.Mem())
        cpu.x[1] = width & 0xFFFFFFFF
        cpu.x[3] = x & 0xFFFFFFFF
        cpu.x[5] = w & 0xFFFFFFFF
        cpu.x[8] = 0xDEADBEEF
        cpu.x[9] = 0xCAFEBABE
        cpu.run(0x1000, body, max_steps=200)
        assert (cpu.x[3] & 0xFFFFFFFF, cpu.x[5] & 0xFFFFFFFF) == \
            FB.run_cave(body, x, w, width), (x, w, width)
        assert cpu.x[1] & 0xFFFFFFFF == width & 0xFFFFFFFF
        assert cpu.x[5] & 0xFFFFFFFF == w & 0xFFFFFFFF


def test_stock_arithmetic_does_overrun_the_surface():
    """The control for the test above. If this stops being true the premise
    is gone."""
    span, off = FB.geometry()
    assert ((-off) & 0xFFFFFFFF) > FB.STAGING_W        # unsigned -107
    assert (-off) + span > FB.STAGING_W                # 747 > 640


# -------------------------------------------------------------- the cave body
def test_body_is_branch_free_and_the_right_length():
    """walk_physical's rule -- a `b` is a run-to-run link, never logic --
    is only exact while the body contains no branch."""
    body = FB.body_words()
    assert len(body) == FB.N_BODY
    for w in body:
        assert (w & 0x7C000000) != 0x14000000, hex(w)   # b / bl
        assert (w & 0xFF000010) != 0x54000000, hex(w)   # b.cond
        assert (w & 0x7E000000) != 0x34000000, hex(w)   # cbz / cbnz


def test_body_words_decode_to_what_the_docstring_claims():
    capstone = pytest.importorskip('capstone')
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    magic, shift = FB.reciprocal()
    off = FB.geometry()[1]
    want = [
        'mov w9, #0x%x' % (magic & 0xFFFF),
        'movk w9, #0x%x, lsl #16' % (magic >> 16),
        'add w8, w3, #0x%x' % off,
        'umull x8, w8, w9',
        'lsr x8, x8, #0x%x' % shift,
        'mov w9, #0x%x' % FB.STAGING_W,
        'sub w9, w9, w5',
        'cmp w8, w9',
        'csel w8, w9, w8, gt',
        'cmp w8, wzr',
        'csel w8, wzr, w8, lt',
        'cmp w5, w1',
        'csel w3, w8, w3, eq',
    ]
    got = []
    for w in FB.body_words():
        d = list(md.disasm(struct.pack('<I', w), 0x1000))
        assert d, hex(w)
        got.append(('%s %s' % (d[0].mnemonic, d[0].op_str)).strip())
    assert got == want


def test_the_registers_the_cave_uses_are_dead_at_the_hook(img):
    """
    w8 was copied to w0 at +0x10F23AC and w9 consumed by the cset at
    +0x10F23A8, so both are free. w1/w3/w5 are live arguments and only w3 and
    w5 are written back. Asserted from the disassembly, not the comment.
    """
    capstone = pytest.importorskip('capstone')
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    md.detail = True
    insns = list(md.disasm(img[0x10F2380:FB.HOOK + 4], 0x10F2380))
    reads = {}
    for i in insns:
        for x in i.regs_access()[0]:
            reads.setdefault(md.reg_name(x), []).append(i.address)
    # w8's last consumer is the `mov w0, w8`; after it nothing reads w8 or w9,
    # so the cave -- which runs in their place, at the tail branch -- may use
    # both. They are outside w0..w7, so make_framebuffer_tex cannot want them.
    assert max(reads.get('w8', [0])) == 0x10F23AC
    for name in ('w8', 'x8', 'w9', 'x9'):
        assert not [a for a in reads.get(name, []) if a > 0x10F23AC]
    # the cave only writes w3, w5 and caller-saved scratch w8..w9
    written = set()
    for w in FB.body_words():
        if (w & 0xFFE00000) in (0x52800000, 0x72A00000):
            written.add(w & 31)
        elif (w & 0xFFC00000) == 0x11000000:
            written.add(w & 31)
        elif (w & 0xFFE00000) in (0x0B000000, 0x4B000000):
            written.add(w & 31)
        elif (w & 0xFFC00000) == 0x51000000:
            written.add(w & 31)
        elif (w & 0xFFE0FC00) == 0x9BA07C00:
            written.add(w & 31)
        elif (w & 0xFFC0FC00) == (0xD3400000 | (1 << 22) | (63 << 10)):
            written.add(w & 31)
        elif (w & 0xFFE00C00) == 0x1A800000:
            written.add(w & 31)
    # w5 (the width) is READ and never written -- that is FINDINGS-308.
    assert written == {3, 8, 9}


def test_each_csel_reads_the_cmp_immediately_before_it():
    """
    Three compares, three selects, strictly alternating at the end of the
    body. Nothing else in the body may write the flags, or a select would
    read someone else's result.
    """
    body = FB.body_words()
    is_cmp = [(w & 0xFFE0FC1F) == 0x6B00001F for w in body]
    is_csel = [(w & 0xFFE00C00) == 0x1A800000 for w in body]
    # every csel is immediately preceded by a cmp
    for i, sel in enumerate(is_csel):
        if sel:
            assert i > 0 and is_cmp[i - 1], i
    assert sum(is_cmp) == 3 and sum(is_csel) == 3
    # and nothing else sets the flags: no S-form add/sub anywhere
    for i, w in enumerate(body):
        if is_cmp[i]:
            continue
        assert (w & 0x1F000000) != 0x11000000 or (w & 0x20000000) == 0
        assert (w & 0xFFE00000) != 0x6B000000           # SUBS reg
        assert (w & 0xFFE00000) != 0x2B000000           # ADDS reg


# ------------------------------------------------------------------ round trip
def test_apply_then_revert_is_byte_exact(tmp_path):
    out = tmp_path / 'main'
    rev = tmp_path / 'main.rev'
    assert FB.apply_to_nso(MAIN, str(out), lambda *_: None)
    import nxmap
    m = nxmap.Main(str(out))
    assert FB.read_state(m.img) == 'wide'
    assert FB.installed(m.img)
    assert not FB.verify(m.img)
    # make_framebuffer_tex must still be stock -- the cave replaces it, not
    # the other way round
    for va, w in FB.IDENTITY.items():
        assert word(m.img, va) == w, hex(va)
    # idempotent
    assert not FB.apply_to_nso(str(out), str(out) + '.x', lambda *_: None)
    assert FB.apply_to_nso(str(out), str(rev), lambda *_: None, revert=True)
    assert open(MAIN, 'rb').read() == open(rev, 'rb').read()


def test_the_whole_cave_footprint_is_returned_on_revert(tmp_path):
    """Chaining branches included: leaving one behind is live code in
    someone else's padding."""
    out = tmp_path / 'main'
    assert FB.apply_to_nso(MAIN, str(out), lambda *_: None)
    import nxmap
    m = nxmap.Main(str(out))
    phys = FB.walk_physical(m.img)
    assert len(phys) >= FB.N_WORDS
    logical = [va for va, _ in FB.walk(m.img)]
    assert set(logical) <= set(phys)
    rev = FB.revert_patches(m.img, lambda *_: None)
    assert set(rev) == set(phys) | {FB.HOOK}
    assert rev[FB.HOOK] == FB.HOOK_STOCK
    assert all(rev[va] == 0 for va in phys)


def test_the_cave_tail_returns_to_make_framebuffer_tex(tmp_path):
    out = tmp_path / 'main'
    assert FB.apply_to_nso(MAIN, str(out), lambda *_: None)
    import nxmap
    m = nxmap.Main(str(out))
    wk = FB.walk(m.img)
    assert len(wk) == FB.N_WORDS
    tail_va, tail = wk[-1]
    assert tail == A.b(tail_va, FB.RETURN_VA)


def test_an_unrecognised_cave_is_refused(img):
    bad = bytearray(img)
    struct.pack_into('<I', bad, FB.HOOK, A.b(FB.HOOK, 0x20000))
    assert FB.read_state(bytes(bad)) == 'unknown'
    assert any('does not recognise' in b for b in FB.verify(bytes(bad)))
    with pytest.raises(ValueError):
        FB.spec(bytes(bad))


def test_gate_is_off_at_four_three(monkeypatch):
    monkeypatch.delenv(FB.FBCAP_ENV, raising=False)
    monkeypatch.setenv('SEVENTH_NX_WIDESCREEN', '')
    assert not FB.enabled()
    monkeypatch.setenv('SEVENTH_NX_WIDESCREEN', 'ws-3d')
    assert FB.enabled()
    monkeypatch.setenv(FB.FBCAP_ENV, '0')
    assert not FB.enabled()


def test_selftest_passes():
    assert FB.selftest(lambda *_: None)
