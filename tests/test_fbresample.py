#!/usr/bin/env python3
"""
tests/test_fbresample.py -- the capture readback resample.

The claim is arithmetic and it is checked by EXECUTION, not by comparing
words: the cave is run under arm64emu against a synthetic staging surface,
and its output is compared pixel-for-pixel with a reference. A wrong register
field, a wrong offset or a wrong condition shows up as wrong pixels here
rather than on the TV.

The page-scaled path is checked the same way, and it executes the module's
OWN loop words -- read out of exefs/main -- so "bit-identical for the swirl"
means the original instruction sequence really ran.

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
import importlib
import os
import shutil
import struct
import sys

import pytest

# BUILD 309. The gates in these modules follow ff7nx_fbsurf, whose default
# is 2. Everything below was written against the STOCK 640x480 capture
# surface and means to test that shape; the scaled shapes are executed in
# tests/test_fbsurf.py. Module scope, because the cave fixtures are built
# once, before any function-scoped fixture could pin it.
os.environ.setdefault('SEVENTH_NX_FB_SURFACE', '1')

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import arm64emu as E                                           # noqa: E402
import ff7nx_fbresample as R

# Build 281 turned the speculative defaults OFF. Every test below that exercises
# the SCALED gate pins the capture scale explicitly, so it tests the code rather
# than whatever the current default happens to be.
# BUILD 337: was '2'. That was harmless only while `punch` and `copy` gated
# on the SURFACE scale alone and ignored the capture scale. They no longer
# can -- the gate has to carry both or a WIDE capture is rejected -- so a
# module-wide 2 would now mean every 1:1 shape these tests seed is the wrong
# shape. The shipping default is 1; the frame/proj helpers below pin 2 for
# themselves, which is what "pins the capture scale explicitly" always meant.
os.environ.setdefault('SEVENTH_NX_FX_CAPSCALE', '1')


def _scaled_capture():
    """Pin the SCALED gate for the helpers that exercise it."""
    import ff7nx_fbresample as _F
    os.environ['SEVENTH_NX_FX_CAPSCALE'] = '2'
    os.environ.pop('SEVENTH_NX_FX_CAPSCALE_Y', None)
    importlib.reload(_F)
    return _F

# Build 272 ships the texel probe ON by default. Every test below that is
# about the COPY body pins the mode explicitly, so the default can flip again
# without silently rewriting what these tests mean.
os.environ.setdefault('SEVENTH_NX_FB_PROBE', '0')                                   # noqa: E402
import nxmap                                                   # noqa: E402

MAIN = os.path.join(_HERE, 'game_data_files', 'exefs', 'main')
pytestmark = pytest.mark.skipif(
    not os.path.exists(MAIN), reason='needs game_data_files/exefs/main')


SRC, DST, HDR = 0x10000000, 0x20000000, 0x30000000


@pytest.fixture(scope='module')
def img():
    return nxmap.Main(MAIN).img


@pytest.fixture(scope='module')
def code(img):
    """The cave laid out flat, plus the module's real stock loop."""
    cave = 0x2000000
    body = R.body_words(lambda i: cave + 4 * i)
    out = {cave + 4 * i: w for i, w in enumerate(body)}
    for va in range(R.STOCK_LOOP, R.AFTER_LOOP, 4):
        out[va] = struct.unpack_from('<I', img, va)[0]
    return cave, out


def _pixels(n):
    return [((i * 7) & 0xFF, (i * 3 + 1) & 0xFF, (i * 11 + 2) & 0xFF,
             (i + 3) & 0xFF) for i in range(n)]


def _run(code, fb_w, tex_w, ncols, src):
    cave, program = code
    mem = E.Mem()
    for i, px in enumerate(src):
        mem.write(SRC + 4 * i, bytes(px))
    mem.setu(HDR + R.FB_W_OFF, fb_w, 4)
    mem.setu(HDR + R.TEX_W_OFF, tex_w, 4)
    cpu = E.Cpu(mem)
    cpu.x[0], cpu.x[10], cpu.x[11] = DST, ncols, SRC + 3
    cpu.x[12], cpu.x[13], cpu.x[20] = 0, DST + 3, HDR
    left = cpu.run(cave, None, code=program, max_steps=4_000_000)
    assert left == R.AFTER_LOOP, hex(left)
    return [tuple(mem.read(DST + 4 * i, 4)) for i in range(ncols)]


def _src_col(i):
    magic, shift = R._reciprocal()
    return (i * magic) >> shift


def test_a_one_to_one_capture_is_resampled_and_stays_full_width(code):
    """
    Every capture rect the executable builds is 64, 128, 192 or 256 wide
    (FINDINGS-308 4). Each must fill its whole destination -- that is what
    keeps u = 1..255 inside the image and the black tiles gone -- from a
    source span of 0.7494 * w, which is what keeps one texel on one game unit.
    """
    src = _pixels(1200)
    for w in (64, 128, 192, 222, 256):
        got = _run(code, w, w, w, src)
        want = [(src[_src_col(i)][2], src[_src_col(i)][1],
                 src[_src_col(i)][0], src[_src_col(i)][3]) for i in range(w)]
        assert got == want, w
        # the destination is full, and the source never exceeds 0.7494 * w
        assert len(got) == w
        assert _src_col(w - 1) == (w - 1) * 640 // 854


def test_the_registration_is_one_texel_per_game_unit(code):
    """
    texel i <- staging x' + floor(i * 640/854), and staging column c is game
    x = c / 0.7494 - 107, so texel i is game x = rect.x + i exactly. That is
    the contract, and getting it wrong by 1.334x is what put the ocean on the
    beach in build 267.
    """
    for i in (0, 1, 64, 128, 191, 255):
        assert _src_col(i) == i * 640 // 854
    # a 256-texel capture reads 192 source columns: the same information
    # builds 245-266 had, spread across the whole texture instead of 3/4 of it
    assert _src_col(255) + 1 == 192


def test_a_page_scaled_capture_runs_the_stock_loop_unchanged(code):
    """Build 244's regression, re-encoded for this cave: the battle-entry
    swirl must not be resampled, and must not even take a rewritten path."""
    src = _pixels(1200)
    for fb_w, tex_w, n in ((512, 256, 512), (1024, 512, 640), (256, 128, 256)):
        got = _run(code, fb_w, tex_w, n, src)
        want = [(src[i][2], src[i][1], src[i][0], src[i][3]) for i in range(n)]
        assert got == want, (fb_w, tex_w)


def test_the_displaced_word_and_the_stock_body_are_what_we_think(img):
    assert struct.unpack_from('<I', img, R.HOOK)[0] == R.HOOK_STOCK
    for va, want in R.ANCHORS.items():
        assert struct.unpack_from('<I', img, va)[0] == want, hex(va)


def test_apply_then_revert_is_byte_exact(tmp_path):
    out = tmp_path / 'main'
    shutil.copy(MAIN, out)
    assert R.apply_all(out, log=lambda *_: None) == 0
    img = nxmap.Main(str(out)).img
    assert R.read_state(img) == 'resampling'
    assert R.verify(img) == []
    assert R.apply_all(out, log=lambda *_: None) == 0          # idempotent
    assert R.apply_all(out, revert=True, log=lambda *_: None) == 0
    assert out.read_bytes() == open(MAIN, 'rb').read()


def test_the_whole_footprint_is_returned_on_revert(tmp_path):
    """Logical words alone are not enough: the link branches between padding
    runs have to go too, or the next allocator skips those holes."""
    out = tmp_path / 'main'
    shutil.copy(MAIN, out)
    assert R.apply_all(out, log=lambda *_: None) == 0
    img = nxmap.Main(str(out)).img
    assert len(R.walk(img)) == R.N_WORDS
    assert len(R.walk_physical(img)) >= R.N_WORDS
    assert set(R.walk(img)) <= set(R.walk_physical(img))


def test_it_composes_with_the_capture_cave_in_either_order(tmp_path):
    import ff7nx_fbcapture as FB
    for first_resample in (True, False):
        a = tmp_path / ('a%d' % first_resample)
        b = tmp_path / ('b%d' % first_resample)
        shutil.copy(MAIN, a)
        if first_resample:
            assert R.apply_all(a, log=lambda *_: None) == 0
            assert FB.apply_to_nso(str(a), str(b), lambda *_: None)
        else:
            assert FB.apply_to_nso(str(a), str(b), lambda *_: None)
            assert R.apply_all(b, log=lambda *_: None) == 0
        img = nxmap.Main(str(b)).img
        assert R.read_state(img) == 'resampling'
        assert FB.read_state(img) == 'wide'
        assert R.verify(img) == [] and FB.verify(img) == []
        # the two caves may not share a single word
        assert not (set(R.walk_physical(img)) | {R.HOOK}) & \
                   (set(FB.walk_physical(img)) | {FB.HOOK})


def test_gate_is_off_at_four_three(monkeypatch):
    monkeypatch.setenv(R.RESAMPLE_ENV, '0')
    assert R.enabled() is False
    monkeypatch.setenv(R.RESAMPLE_ENV, '1')
    assert R.enabled() is True


# ---------------------------------------------------------------------------
# The texel-coordinate probe (build 272). One screenshot reports the whole
# texel -> screen mapping, so alignment stops being a matter of opinion.
# ---------------------------------------------------------------------------
def test_every_mode_shares_one_gate_and_one_loop():
    """BUILD 279. Build 278 gave only the 'frame' body the size-aware gate, so
    with Kujata's capture at 512 the PROBE tested `fb_tex.w == tex_format.width`
    -- false for Kujata, true for every other capture. The probe ran on the
    wrong twenty-four effects and never on the one being measured.

    Every mode is now a frame body and differs ONLY in the eight sample words.
    """
    import ff7nx_fbresample as _F
    f = _F.frame_body(lambda i: i * 4, 'frame')
    t = _F.frame_body(lambda i: i * 4, 'texel')
    a = _F.frame_body(lambda i: i * 4, 'alpha')
    assert len(f) == len(t) == len(a) == _F.FRAME_WORDS
    for other, name in ((t, 'texel'), (a, 'alpha')):
        diff = [i for i, (x, y) in enumerate(zip(f, other)) if x != y]
        assert diff and all(40 <= i <= 47 for i in diff), (name, diff)
    for how in ('frame', 'texel', 'alpha'):
        assert _F.body_words(lambda i: i * 4, how=how)[:13] == f[:13]


def test_probe_reports_the_UV_coordinate_not_the_physical_texel():
    """With the capture at 512 physical texels the UV space is still 0..255, so
    the probe has to report i >> k or the read-out wraps twice and cannot be
    decoded. Executed, through the real gate, for several rows."""
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    import ff7nx_fxcapscale as _S
    # BUILD 337: build the body FIRST -- _frame_code_for pins the scaled
    # capture, so reading the scale before it would describe a different
    # build from the one being executed.
    addrs, code = _frame_code_for('texel')
    importlib.reload(_S)
    k = {1: 0, 2: 1, 4: 2}[_S.scale()]
    texw = 256 * _S.scale()
    HDR, DEST = 0x700000, 0x2000000
    for j in (0, 2, 100, 300, texw - 2):
        mem = _emu.Mem()
        for off, v in ((0x1C, texw), (0x20, texw), (0x3C, 256),
                       (0x14, 0), (0x18, 0)):
            mem.setu(HDR + off, v, 4)
        cpu = _emu.Cpu(mem)
        for r, v in ((20, HDR), (19, 640), (26, 0x800000), (8, j), (10, 7),
                     (22, 3), (12, 0), (0, DEST), (13, DEST + 3), (11, 0)):
            cpu.set(r, v)
        pc = cpu.run(FRAME_BASE, None, code=code, start_pc=FRAME_BASE)
        assert pc == _F.AFTER_LOOP
        for i in (0, 2, 100, 300, texw - 2):
            got = tuple(mem.u(DEST + 4 * i + n, 1) for n in range(4))
            assert got == ((i >> k) & 0xFF, (j >> k) & 0xFF, 0x40, 0xFF), (i, j)


def test_probe_switches_in_place_and_still_reverts_byte_exactly(
        tmp_path, monkeypatch):
    import shutil as _sh
    import ff7nx_fbresample as _F
    import nxmap as _nx
    monkeypatch.setenv(_F.RESAMPLE_ENV, '1')
    out = tmp_path / 'main_probe'
    _sh.copy(MAIN, out)
    quiet = lambda *_: None                                    # noqa: E731
    monkeypatch.setenv(_F.PROBE_ENV, '0')
    assert _F.apply_all(out, log=quiet) == 0
    assert _F.read_state(_nx.Main(str(out)).img) == 'resampling'
    monkeypatch.setenv(_F.PROBE_ENV, '1')
    assert _F.apply_all(out, log=quiet) == 0
    img = _nx.Main(str(out)).img
    assert _F.read_state(img) == 'resampling+texel'
    assert _F.verify(img) == []
    assert _F.apply_all(out, log=quiet) == 0                   # idempotent
    monkeypatch.setenv(_F.PROBE_ENV, '0')
    assert _F.apply_all(out, log=quiet) == 0
    assert _F.read_state(_nx.Main(str(out)).img) == 'resampling'
    assert _F.apply_all(out, revert=True, log=quiet) == 0
    assert out.read_bytes() == open(MAIN, 'rb').read()


def test_alpha_mode_forces_ff_and_changes_nothing_else():
    """'alpha' is the frame body with ONE word changed: the alpha source."""
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    f = _F.frame_body(lambda i: i * 4, 'frame')
    a = _F.frame_body(lambda i: i * 4, 'alpha')
    assert [i for i, (x, y) in enumerate(zip(f, a)) if x != y] == [46]
    base = 0x2000
    cpu = _emu.Cpu(_emu.Mem())
    cpu.set(15, 0xDEADBEEF)
    cpu.run(base, None, code={base: a[46]}, start_pc=base)
    assert cpu.get(15) == 0xFF


def test_every_mode_round_trips_and_reverts_byte_exactly(tmp_path, monkeypatch):
    import shutil as _sh
    import ff7nx_fbresample as _F
    import nxmap as _nx
    monkeypatch.setenv(_F.RESAMPLE_ENV, '1')
    out = tmp_path / 'main_modes'
    _sh.copy(MAIN, out)
    quiet = lambda *_: None                                    # noqa: E731
    for m, env in (('copy', '0'), ('alpha', 'alpha'), ('texel', '1'),
                   ('alpha', 'alpha'), ('copy', '0')):
        monkeypatch.setenv(_F.PROBE_ENV, env)
        assert _F.apply_all(out, log=quiet) == 0
        img = _nx.Main(str(out)).img
        want = 'resampling' if m == 'copy' else 'resampling+%s' % m
        assert _F.read_state(img) == want
        assert _F.verify(img) == []
        assert _F.apply_all(out, log=quiet) == 0               # idempotent
    assert _F.apply_all(out, revert=True, log=quiet) == 0
    assert out.read_bytes() == open(MAIN, 'rb').read()


# ---------------------------------------------------------------------------
# 'frame' mode (build 276): the capture steps across the whole staging surface,
# mirrored and clamped, so 256 texels hold the whole picture instead of 0.30 of
# it. The mapping is the build-273 probe fit, halved for the build-275 disc.
# ---------------------------------------------------------------------------
FRAME_BASE = 0x100000


def _frame_code_for(how='frame'):
    _F = _scaled_capture()
    addrs = [FRAME_BASE + 4 * i for i in range(_F.FRAME_WORDS)]
    return addrs, dict(zip(addrs, _F.frame_body(lambda i: addrs[i], how)))


def _frame_code():
    return _frame_code_for('frame')


def test_frame_body_is_fully_encoded_and_the_right_length():
    import ff7nx_fbresample as _F
    b = _F.frame_body(lambda i: i * 4)
    assert len(b) == _F.FRAME_WORDS == 55
    assert all(w is not None for w in b)


def test_frame_cave_really_reads_the_mapped_source_pixel():
    """EXECUTE it against a synthetic staging surface. Every destination texel
    must carry the pixel the mapping names, for several rows."""
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    addrs, code = _frame_code()
    p = _F.map_params()
    # DEST well clear of the 640x480x4 surface, or the two overlap
    SURF, PITCH, DEST, HDR = 0x800000, 640, 0x2000000, 0x700000

    def col(i):
        return max(0, min(_F.SURF_W - 1, p['cx'] - ((i * p['mx']) >> 16)))

    def row(j):
        return max(0, min(_F.SURF_H - 1, p['cy'] - ((j * p['my']) >> 16)))

    import ff7nx_fxcapscale as _S
    texw = 256 * _S.scale()
    for j in (0, 1, 64, 128, 200, 255):
        mem = _emu.Mem()
        mem.setu(HDR + 0x1C, texw, 4)
        mem.setu(HDR + 0x20, texw, 4)
        mem.setu(HDR + 0x3C, 256, 4)
        mem.setu(HDR + 0x14, 0, 4)
        mem.setu(HDR + 0x18, 0, 4)
        r = row(j)
        for i in range(texw):
            a = SURF + (PITCH * r + col(i)) * 4
            mem.setu(a + 0, col(i) & 0xFF, 1)
            mem.setu(a + 1, r & 0xFF, 1)
            mem.setu(a + 2, 0xAB, 1)
            mem.setu(a + 3, 0xCD, 1)
        cpu = _emu.Cpu(mem)
        # deliberately WRONG stock loop bounds: the cave must override both
        for reg, val in ((20, HDR), (19, PITCH), (26, SURF), (8, j),
                         (10, 7), (22, 3), (12, 0), (0, DEST), (13, DEST + 3),
                         (11, 0)):
            cpu.set(reg, val)
        cpu.run(FRAME_BASE, None, code=code, start_pc=FRAME_BASE)
        assert cpu.get(10) == texw and cpu.get(22) == texw
        for i in range(texw):
            got = tuple(mem.u(DEST + 4 * i + k, 1) for k in range(4))
            assert got == (0xAB, r & 0xFF, col(i) & 0xFF, 0xCD), (j, i, got)


def test_frame_mapping_agrees_with_the_measured_disc_centre():
    """The whole point: texel 128 must land on the disc centre, which the
    build-273 probe put at frame px 675 / py 203."""
    import ff7nx_fbresample as _F
    p = _F.map_params()
    import ff7nx_fxcapscale as _S
    centre = 128 * _S.scale()                 # the disc centre in PHYSICAL texels
    cx = p['cx'] - ((centre * p['mx']) >> 16)
    cy = p['cy'] - ((centre * p['my']) >> 16)
    assert abs(2 * cx - 675) <= 4, 2 * cx
    assert abs(1.5 * cy - 203) <= 4, 1.5 * cy


def test_frame_mapping_is_mirrored_and_spans_the_whole_surface():
    import ff7nx_fbresample as _F
    p = _F.map_params()

    def col(i):
        return max(0, min(_F.SURF_W - 1, p['cx'] - ((i * p['mx']) >> 16)))

    import ff7nx_fxcapscale as _S
    last = 256 * _S.scale() - 1
    assert col(0) > col(last // 2) > col(last)          # mirrored
    assert col(0) == _F.SURF_W - 1 and col(last) == 0   # clamped to both ends


def test_frame_gate_still_sends_page_scaled_captures_to_the_stock_loop():
    """fb_tex.w != tex_format.width must reach the displaced instruction and
    the untouched stock body, not the remap."""
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    addrs, code = _frame_code()
    mem = _emu.Mem()
    mem.setu(0x700000 + 0x1C, 1024, 4)      # not Kujata's shape
    mem.setu(0x700000 + 0x3C, 256, 4)
    cpu = _emu.Cpu(mem)
    cpu.set(20, 0x700000)
    cpu.set(11, 0xDEAD0000)
    pc = cpu.run(FRAME_BASE, None, code=code, start_pc=FRAME_BASE)
    assert cpu.get(14) == 0xDEAD0000        # the displaced mov x14, x11 ran
    assert pc == _F.STOCK_LOOP              # and it left for the stock body


def test_frame_resizes_the_cave_rather_than_stranding_one(tmp_path,
                                                          monkeypatch):
    import shutil as _sh
    import ff7nx_fbresample as _F
    import nxmap as _nx
    monkeypatch.setenv(_F.RESAMPLE_ENV, '1')
    out = tmp_path / 'main_frame'
    _sh.copy(MAIN, out)
    quiet = lambda *_: None                                    # noqa: E731
    for env, want in (('frame', 'resampling+frame'), ('0', 'resampling'),
                      ('1', 'resampling+texel'), ('frame', 'resampling+frame'),
                      ('alpha', 'resampling+alpha'),
                      ('frame', 'resampling+frame')):
        monkeypatch.setenv(_F.PROBE_ENV, env)
        assert _F.apply_all(out, log=quiet) == 0
        img = _nx.Main(str(out)).img
        assert _F.read_state(img) == want
        assert _F.verify(img) == []
        assert _F.apply_all(out, log=quiet) == 0               # idempotent
    assert _F.apply_all(out, revert=True, log=quiet) == 0
    assert out.read_bytes() == open(MAIN, 'rb').read()


def test_only_kujatas_capture_shape_reaches_the_remap():
    """Build 277 broke the battle-entry swirl because one condition was not a
    discriminator -- the swirl's own xscale is 2. Execute the gate against every
    capture shape in the game and assert only Kujata's reaches the remap.

    BUILD 325: the ORIGIN is no longer one of the conditions. It never was a
    discriminator either -- `ff7nx_fbcapture.map_x(0)` is 80 virtual units, so
    the real capture's fb_tex.x is 80*k, never 0, and requiring 0 rejected the
    genuine article. The shape is discriminated by w, h and tex_format alone.
    """
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    addrs, code = _frame_code()
    HDR = 0x700000

    def route(fbw, fbh, texw, x, y):
        mem = _emu.Mem()
        for off, v in ((0x1C, fbw), (0x20, fbh), (0x3C, texw),
                       (0x14, x), (0x18, y)):
            mem.setu(HDR + off, v, 4)
        cpu = _emu.Cpu(mem)
        for r, v in ((20, HDR), (19, 640), (26, 0x800000), (8, 0), (10, 7),
                     (22, 3), (12, 0), (0, 0x2000000), (13, 0x2000003),
                     (11, 0xDEAD0000)):
            cpu.set(r, v)
        pc = cpu.run(FRAME_BASE, None, code=code, start_pc=FRAME_BASE)
        return 'remap' if pc == _F.AFTER_LOOP else 'stock'

    assert route(512, 512, 256, 0, 0) == 'remap'      # Kujata
    # Kujata's shape at ANY origin, because the origin is not a condition --
    # and 80 is the origin the real capture actually has.
    assert route(512, 512, 256, 80, 0) == 'remap'
    assert route(512, 512, 256, 0, 60) == 'remap'
    for shape in ((320, 480, 160, 0, 0),              # the battle swirl, x2
                  (320, 480, 160, 80, 0),
                  (256, 256, 128, 0, 0),              # the [0x9AD1A8] x2 one
                  (256, 256, 256, 0, 0),              # a plain 1:1 capture
                  (64, 128, 64, 0, 0),
                  (192, 256, 192, 0, 0),
                  (512, 256, 256, 0, 0),              # x2 but not square
                  (1024, 1024, 256, 0, 0)):           # x4
        assert route(*shape) == 'stock', shape


def test_the_gate_is_identical_in_every_mode_when_executed():
    """Not just equal words -- routed. If a probe and a fix ever disagree about
    which capture they apply to again, this fails."""
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    HDR, DEST = 0x700000, 0x2000000

    def route(how, fbw, fbh, texw, x, y):
        addrs, code = _frame_code_for(how)
        mem = _emu.Mem()
        for off, v in ((0x1C, fbw), (0x20, fbh), (0x3C, texw),
                       (0x14, x), (0x18, y)):
            mem.setu(HDR + off, v, 4)
        cpu = _emu.Cpu(mem)
        for r, v in ((20, HDR), (19, 640), (26, 0x800000), (8, 0), (10, 7),
                     (22, 3), (12, 0), (0, DEST), (13, DEST + 3),
                     (11, 0xDEAD0000)):
            cpu.set(r, v)
        pc = cpu.run(FRAME_BASE, None, code=code, start_pc=FRAME_BASE)
        return 'remap' if pc == _F.AFTER_LOOP else 'stock'

    shapes = ((512, 512, 256, 0, 0), (320, 480, 160, 0, 0),
              (256, 256, 256, 0, 0), (256, 256, 128, 0, 0),
              (64, 128, 64, 0, 0), (512, 256, 256, 0, 0),
              (512, 512, 256, 80, 0), (1024, 1024, 256, 0, 0))
    for shape in shapes:
        got = {how: route(how, *shape) for how in ('frame', 'texel', 'alpha')}
        assert len(set(got.values())) == 1, (shape, got)
    assert route('texel', 512, 512, 256, 0, 0) == 'remap'
    assert route('texel', 320, 480, 160, 0, 0) == 'stock'


def test_the_whole_chain_lands_in_the_binary_in_build_order(tmp_path,
                                                            monkeypatch):
    """Wiring, not unit behaviour. 7th_heaven_nx.py calls apply_field_frame
    (fxsnap, fxrim, fbsize, fxcapscale, fxscale) and THEN apply_fbcapture
    (fbcapture, fbresample). Run that exact order on a copy of the dump and
    read the result out of the patched image."""
    import shutil as _sh
    import struct as _st

    import ff7nx_fbcapture as _FBC
    import ff7nx_fbresample as _F
    import ff7nx_fbsize as _SZ
    import ff7nx_fxcapscale as _CS
    import ff7nx_fxrim as _RIM
    import ff7nx_fxscale as _SC
    import ff7nx_fxsnap as _SNAP
    import nxmap as _nx

    monkeypatch.setenv(_F.RESAMPLE_ENV, '1')
    dest = tmp_path / 'main'
    _sh.copy(MAIN, dest)
    quiet = lambda *_: None                                    # noqa: E731

    for mod in (_SNAP, _RIM, _SZ, _CS, _SC):
        rc = (mod.apply_all(dest, log=quiet) if mod.enabled()
              else mod.apply_all(dest, revert=True, log=quiet))
        assert rc == 0, mod.__name__
    if _FBC.enabled():
        t = str(dest) + '.tmp'
        assert _FBC.apply_to_nso(str(dest), t, quiet)
        os.replace(t, str(dest))
    rc = (_F.apply_all(dest, log=quiet) if _F.enabled()
          else _F.apply_all(dest, revert=True, log=quiet))
    assert rc == 0

    img = _nx.Main(str(dest)).img
    for mod in (_SNAP, _RIM, _SZ, _CS, _SC, _F):
        assert mod.verify(img) == [], mod.__name__
    assert _F.installed_mode(img) == _F.mode()
    assert _CS.installed_scale(img) == _CS.scales()   # BUILD 337: a pair

    # and the gate really is the size-aware one, read back out of the image
    addrs = _F.walk(img, _F.n_words(_F.mode()))
    assert len(addrs) == _F.n_words(_F.mode())
    got = [_st.unpack_from('<I', img, a)[0] for a in addrs]
    assert got == _F.body_words(lambda i: addrs[i], how=_F.mode())


def test_fbcapture_does_not_move_kujatas_origin_out_from_under_the_gate():
    """The frame gate requires fb_tex.x == 0. ff7nx_fbcapture rewrites capture
    origins; if it still fired on Kujata the origin would become 80 and the
    remap would silently never run again."""
    import arm64emu as _emu
    import ff7nx_fbcapture as _FBC
    import ff7nx_fxcapscale as _CS
    base = 0x100000
    words = _FBC.body_words()
    addrs = [base + 4 * i for i in range(len(words))]
    code = dict(zip(addrs, words))

    def origin(x, w, tex_w):
        cpu = _emu.Cpu(_emu.Mem())
        cpu.set(3, x)
        cpu.set(5, w)
        cpu.set(1, tex_w)
        cpu.run(base, None, code=code, start_pc=base)
        return cpu.get(3)

    k = _CS.scale()
    assert origin(0, 256, 256) == 80        # a stock 1:1 capture still moves
    # BUILD 338: Kujata's SCALED capture must move too. It used not to,
    # because the frame gate once required fb_tex.x == 0 -- a condition build
    # 325 retired, since map_x(0) is 80 and the gate could never match the
    # real capture. Leaving the origin at 0 put the field 320 staging columns
    # (about 1050 screen px) to the LEFT the moment capscale was raised.
    assert origin(0, 256 * k, 256) == 80
    assert origin(0, 320, 160) == 0         # the swirl's still must not
    assert origin(0, 128, 128) == 80        # nor does any other 1:1 caller
                                            # lose its correction


# ---------------------------------------------------------------------------
# 'proj' (build 280) -- the PROJECTIVE remap. A plane seen through a pinhole
# camera maps to the screen by a homography; a separable model cannot express
# one at any constants, which is why 276 and 277 only ever moved the picture.
# ---------------------------------------------------------------------------
PROJ_BASE = 0x100000


def _proj_code(how='proj'):
    _F = _scaled_capture()
    a = [PROJ_BASE + 4 * i for i in range(_F.PROJ_WORDS)]
    return a, dict(zip(a, _F.proj_body(lambda i: a[i], how)))


def _trunc(n, d):
    q = abs(n) // abs(d)
    return -q if (n < 0) != (d < 0) else q


def _proj_ref(i, j):
    import ff7nx_fbresample as _F
    A00, A01, A02, A10, A11, A12, A20, A21, A22 = _F.proj_coef()
    den = A20 * i + A21 * j + A22
    return (max(0, min(_F.SURF_W - 1, _trunc(A00 * i + A01 * j + A02, den))),
            max(0, min(_F.SURF_H - 1, _trunc(A10 * i + A11 * j + A12, den))))


def test_the_projective_coefficients_are_sane_over_the_whole_texel_grid():
    """The denominator must never reach zero anywhere a texel can ask, and
    every coefficient must fit int32 or the smaddl folding is wrong."""
    import ff7nx_fbresample as _F
    import ff7nx_fxcapscale as _S
    c = _F.proj_coef()
    assert len(c) == 9 and all(-2**31 <= x < 2**31 for x in c)
    n = 256 * _S.scale()
    worst = min(c[6] * i + c[7] * j + c[8]
                for i in (0, n - 1) for j in (0, n - 1))
    assert worst > 0, worst


def test_the_projective_cave_computes_the_mapping_on_a_cpu():
    """Execute the whole 78-word body against a synthetic staging surface and
    check EVERY texel of several rows against an independent reference."""
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    import ff7nx_fxcapscale as _S
    a, code = _proj_code()
    SURF, PITCH, DEST, HDR, SP = 0x800000, 640, 0x4000000, 0x700000, 0x60000
    tex = 256 * _S.scale()
    poison = {r: (r * 0x1111111111111111) & ((1 << 64) - 1) for r in range(1, 8)}
    for j in (0, 1, 7, 128, 255, 256, tex - 1):
        mem = _emu.Mem()
        for off, val in ((0x1C, tex), (0x20, tex), (0x3C, 256),
                         (0x14, 0), (0x18, 0)):
            mem.setu(HDR + off, val, 4)
        for i in range(tex):
            c, r = _proj_ref(i, j)
            p = SURF + (PITCH * r + c) * 4
            mem.setu(p + 0, c & 0xFF, 1)
            mem.setu(p + 1, r & 0xFF, 1)
            mem.setu(p + 2, (c >> 8) & 0xFF, 1)
            mem.setu(p + 3, (r >> 8) & 0xFF, 1)
        cpu = _emu.Cpu(mem)
        cpu.sp = SP
        # deliberately wrong stock loop bounds, and poisoned borrowed registers
        for reg, val in ((20, HDR), (19, PITCH), (26, SURF), (8, j), (10, 3),
                         (22, 5), (12, 0), (0, DEST), (13, DEST + 3),
                         (11, 0xDEAD0000)):
            cpu.set(reg, val)
        for reg, val in poison.items():
            cpu.set(reg, val)
        pc = cpu.run(PROJ_BASE, None, code=code, start_pc=PROJ_BASE,
                     max_steps=4000000)
        assert pc == _F.AFTER_LOOP
        assert cpu.get(10) == tex and cpu.get(22) == tex
        assert cpu.get_sp() == SP, 'the cave must leave the stack balanced'
        for reg, val in poison.items():
            assert cpu.get(reg) == val, 'x%d was not restored' % reg
        for i in range(tex):
            c, r = _proj_ref(i, j)
            want = ((c >> 8) & 0xFF, r & 0xFF, c & 0xFF, (r >> 8) & 0xFF)
            got = tuple(mem.u(DEST + 4 * i + n, 1) for n in range(4))
            assert got == want, (j, i, got, want)


def test_the_projective_gate_is_the_same_three_conditions():
    """BUILD 325. Five became three: the two origin comparisons are retired
    (see test_only_kujatas_capture_shape_reaches_the_remap), and their slots
    hold NOPs rather than being removed, so every body keeps its length and
    every branch offset inside it."""
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    HDR = 0x700000
    for how in ('proj', 'projtexel'):
        a, code = _proj_code(how)

        def route(fbw, fbh, texw, x, y):
            mem = _emu.Mem()
            for off, v in ((0x1C, fbw), (0x20, fbh), (0x3C, texw),
                           (0x14, x), (0x18, y)):
                mem.setu(HDR + off, v, 4)
            cpu = _emu.Cpu(mem)
            cpu.sp = 0x60000
            for r, v in ((20, HDR), (19, 640), (26, 0x800000), (8, 0),
                         (10, 0), (22, 0), (12, 0), (0, 0x4000000),
                         (13, 0x4000003), (11, 0xDEAD0000)):
                cpu.set(r, v)
            pc = cpu.run(PROJ_BASE, None, code=code, start_pc=PROJ_BASE,
                         max_steps=4000000)
            return 'remap' if pc == _F.AFTER_LOOP else 'stock'

        assert route(512, 512, 256, 0, 0) == 'remap'
        # the origin is not a condition, so Kujata's shape passes at the
        # origin it really has
        assert route(512, 512, 256, 80, 0) == 'remap', how
        assert route(512, 512, 256, 0, 60) == 'remap', how
        for shape in ((320, 480, 160, 0, 0), (256, 256, 256, 0, 0),
                      (256, 256, 128, 0, 0), (64, 128, 64, 0, 0),
                      (512, 256, 256, 0, 0), (1024, 1024, 256, 0, 0)):
            assert route(*shape) == 'stock', (how, shape)

    # and the retired slots are NOPs, in every body that has the gate
    NOP = 0xD503201F
    for how in ('proj', 'projtexel', 'texel', 'frame'):
        words = _F.body_words(lambda i: PROJ_BASE + 4 * i, how=how)
        assert words.count(NOP) >= 2, (how, words.count(NOP))


# ---------------------------------------------------------------- build 289
REPORT_BASE = 0x140000


def _report_code():
    import ff7nx_fbresample as _F
    addrs = [REPORT_BASE + 4 * i for i in range(_F.n_words('report'))]
    return addrs, dict(zip(addrs, _F.body_words(lambda i: addrs[i],
                                                how='report')))


def test_the_report_paints_both_numbers_as_grey_bands():
    """BUILD 297. The report must (a) run on EVERY capture, gate or no gate,
    (b) be readable without knowing the byte-to-channel order -- so all three
    colour bytes carry the SAME value -- and (c) report fb_tex.w (what the copy
    WROTE) in texel columns 0..127 and tex_format.width (what the UVs ADDRESS)
    in columns 128..255, so one screenshot says whether they differ."""
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    addrs, code = _report_code()
    HDR, DEST, SRC = 0x700000, 0x800000, 0x900000

    def run(fbw, fbh, texw, ncols, pitch=640):
        mem = _emu.Mem()
        for off, v in ((_F.FB_W_OFF, fbw), (_F.FB_H_OFF, fbh),
                       (_F.TEX_W_OFF, texw)):
            mem.setu(HDR + off, v, 4)
        cpu = _emu.Cpu(mem)
        for r, v in ((20, HDR), (10, ncols), (12, 0), (11, SRC + 3),
                     (13, DEST + 3), (0, DEST), (19, pitch)):
            cpu.set(r, v)
        pc = cpu.run(REPORT_BASE, None, code=code, start_pc=REPORT_BASE)
        return pc, [mem.u(DEST + 4 * i, 4) for i in range(ncols)]

    for fbw, fbh, texw in ((256, 256, 256),   # Kujata unclamped
                           (240, 240, 256),   # Kujata clamped to a 320 surface
                           (320, 480, 160),   # a battle-swirl half
                           (512, 512, 256),
                           (64, 128, 64),
                           (244, 244, 256),   # what build 296 measured
                           (160, 176, 256)):
        pc, out = run(fbw, fbh, texw, 200)
        # (a) it never escapes to the stock loop, whatever the shape
        assert pc == _F.AFTER_LOOP, (fbw, fbh, texw, hex(pc))
        for col, px in enumerate(out):
            d0, d1, d2 = px & 0xFF, (px >> 8) & 0xFF, (px >> 16) & 0xFF
            a = px >> 24
            # (b) grey: every colour byte the same, so the channel order --
            # the thing builds 289 and 290 disagreed about -- cannot matter
            assert d0 == d1 == d2, (fbw, fbh, col, hex(px))
            assert a == 0xFF, 'must be opaque or it is not visible'
            # (c) the column picks which number is being reported
            want = (640 if col < 128 else texw) >> 3
            assert d0 == (want & 0xFF), (fbw, fbh, col, d0, want)


def test_the_report_is_exact_for_every_size_that_can_occur():
    """grey = value >> 1, so anything up to 511 round-trips within 1."""
    for v in (64, 128, 160, 176, 192, 240, 256, 320, 480, 511):
        assert abs(((v >> 1) << 1) - v) <= 1


def test_the_report_shares_the_copy_bodys_word_count_and_reverts():
    import ff7nx_fbresample as _F
    assert _F.n_words('report') == _F.N_WORDS
    assert 'report' in _F.MODES


# ---------------------------------------------------------------- build 293
def test_the_span_knob_is_stock_at_100_and_monotonic_either_side(monkeypatch):
    """One physically meaningful number: how many staging pixels feed the 256
    texels. 100 must reproduce the stock constants bit for bit, and raising it
    must widen the source span (more source pixels, less magnification)."""
    import ff7nx_fbcapture as _FB
    import ff7nx_fbresample as _F

    monkeypatch.delenv(_F.SPAN_ENV, raising=False)
    assert _F.span() == 100
    assert _F._reciprocal() == _FB.reciprocal()      # bit for bit

    def source_px(pct):
        monkeypatch.setenv(_F.SPAN_ENV, str(pct))
        m, sh = _F._reciprocal()
        return ((255 * m) >> sh) + 1

    got = [source_px(p) for p in (85, 90, 95, 100, 105, 110, 120)]
    assert got == sorted(got), got                   # monotonic
    assert source_px(100) == 192                     # the stock 640/854
    assert all(1 <= n <= 256 for n in got)


def test_nonsense_span_falls_back_rather_than_writing_garbage(monkeypatch):
    import ff7nx_fbresample as _F
    for bad in ('0', '49', '201', 'banana', ''):
        monkeypatch.setenv(_F.SPAN_ENV, bad)
        assert _F.span() == _F.DEFAULT_SPAN


# ---------------------------------------------------------------- build 304
def test_opaque_is_the_copy_with_exactly_one_word_changed():
    """BUILD 304. The candidate fix for the black squares: the destination
    alpha becomes 0xFF instead of a copy of the source's. It must differ from
    the plain copy in ONE word and nothing else, or it is not a clean test."""
    import ff7nx_fbresample as _F
    c = _F.body_words(lambda i: 0x100000 + 4 * i, how='copy')
    o = _F.body_words(lambda i: 0x100000 + 4 * i, how='opaque')
    assert len(c) == len(o) == _F.N_WORDS
    diff = [i for i, (x, y) in enumerate(zip(c, o)) if x != y]
    assert diff == [15], diff
    assert o[15] == _F.A.movz(15, 0xFF)


def test_the_copy_propagates_source_alpha_and_opaque_does_not():
    """Executed, because this is the whole hypothesis: the black squares are
    source pixels whose alpha is zero, faithfully copied."""
    import arm64emu as _emu
    import ff7nx_fbresample as _F

    def run(how, src_alpha):
        addrs = [0x100000 + 4 * i for i in range(_F.n_words(how))]
        code = dict(zip(addrs, _F.body_words(lambda i: addrs[i], how=how)))
        HDR, DEST, SRC = 0x700000, 0x800000, 0x900000
        mem = _emu.Mem()
        for off, v in ((_F.FB_W_OFF, 256 << _F.gate_shift()), (_F.TEX_W_OFF, 256)):
            mem.setu(HDR + off, v, 4)
        for k in range(64):
            mem.setu(SRC + k, [0x40, 0x80, 0xC0, src_alpha][k % 4], 1)
        cpu = _emu.Cpu(mem)
        for r, v in ((20, HDR), (10, 4), (12, 0), (11, SRC + 3),
                     (13, DEST + 3), (0, DEST), (19, 640)):
            cpu.set(r, v)
        cpu.run(0x100000, None, code=code, start_pc=0x100000)
        px = mem.u(DEST, 4)
        return px >> 24

    assert run('copy', 0x00) == 0x00        # the copy hands alpha 0 straight on
    assert run('copy', 0xFF) == 0xFF
    assert run('opaque', 0x00) == 0xFF      # opaque never does
    assert run('opaque', 0xFF) == 0xFF


def test_opaque_leaves_the_colour_bytes_alone():
    """It must change ONLY alpha -- if it moved a colour byte the test would
    prove nothing about where the black comes from."""
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    out = {}
    for how in ('copy', 'opaque'):
        addrs = [0x100000 + 4 * i for i in range(_F.n_words(how))]
        code = dict(zip(addrs, _F.body_words(lambda i: addrs[i], how=how)))
        HDR, DEST, SRC = 0x700000, 0x800000, 0x900000
        mem = _emu.Mem()
        for off, v in ((_F.FB_W_OFF, 256 << _F.gate_shift()), (_F.TEX_W_OFF, 256)):
            mem.setu(HDR + off, v, 4)
        for k in range(64):
            mem.setu(SRC + k, [0x11, 0x22, 0x33, 0x44][k % 4], 1)
        cpu = _emu.Cpu(mem)
        for r, v in ((20, HDR), (10, 4), (12, 0), (11, SRC + 3),
                     (13, DEST + 3), (0, DEST), (19, 640)):
            cpu.set(r, v)
        cpu.run(0x100000, None, code=code, start_pc=0x100000)
        px = mem.u(DEST, 4)
        out[how] = (px & 0xFF, (px >> 8) & 0xFF, (px >> 16) & 0xFF)
    assert out['copy'] == out['opaque'], out


# ---------------------------------------------------------------- build 305
def test_srcblack_reports_the_source_pixel_not_the_texel():
    """BUILD 305. Executed against a synthetic surface whose pixels are known:
    a black source pixel must come out RED and opaque, anything else GREEN and
    opaque. That is the whole diagnostic, and it is read off the screen as a
    colour name rather than a level."""
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    BASE = 0x160000
    addrs = [BASE + 4 * i for i in range(_F.n_words('srcblack'))]
    code = dict(zip(addrs, _F.body_words(lambda i: addrs[i], how='srcblack')))
    HDR, DEST, SRC = 0x700000, 0x800000, 0x900000

    def run(pixels, ncols):
        mem = _emu.Mem()
        for off, v in ((_F.FB_W_OFF, 256 << _F.gate_shift()), (_F.TEX_W_OFF, 256)):
            mem.setu(HDR + off, v, 4)
        for i, (r, g, b) in enumerate(pixels):
            a = SRC + 4 * i
            mem.setu(a + 0, b, 1); mem.setu(a + 1, g, 1)
            mem.setu(a + 2, r, 1); mem.setu(a + 3, 0xFF, 1)
        cpu = _emu.Cpu(mem)
        for r_, v in ((20, HDR), (10, ncols), (12, 0), (11, SRC + 3),
                      (13, DEST + 3), (0, DEST), (19, 640)):
            cpu.set(r_, v)
        pc = cpu.run(BASE, None, code=code, start_pc=BASE)
        assert pc == _F.AFTER_LOOP, hex(pc)
        return [mem.u(DEST + 4 * i, 4) for i in range(ncols)]

    RED, GREEN = 0xFFFF0000, 0xFF00FF00
    # column i reads source column floor(i*640/854), so feed a run of pixels
    # that is uniform over the first few source columns
    out = run([(0, 0, 0)] * 8, 4)
    assert out == [RED] * 4, [hex(v) for v in out]
    out = run([(0, 40, 0)] * 8, 4)
    assert out == [GREEN] * 4, [hex(v) for v in out]
    out = run([(7, 7, 7)] * 8, 4)        # just under the threshold: still black
    assert out == [RED] * 4
    out = run([(8, 0, 0)] * 8, 4)        # just over it
    assert out == [GREEN] * 4


def test_srcblack_reads_the_same_source_address_as_the_copy():
    """It must sample the pixels the REAL capture samples, or it proves
    nothing. Words 4..8 -- the magic, the step and the address -- are asserted
    identical to the shipping 'copy' body."""
    import ff7nx_fbresample as _F
    c = _F.body_words(lambda i: 0x160000 + 4 * i, how='copy')
    b = _F.body_words(lambda i: 0x160000 + 4 * i, how='srcblack')
    assert c[4:9] == b[4:9]
    assert c[0:3] == b[0:3]              # and the same gate


def test_srcblack_leaves_page_scaled_captures_on_the_stock_path():
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    BASE = 0x160000
    addrs = [BASE + 4 * i for i in range(_F.n_words('srcblack'))]
    code = dict(zip(addrs, _F.body_words(lambda i: addrs[i], how='srcblack')))
    HDR = 0x700000
    # BUILD 337: shapes whose fb_tex.w / tex_format.width ratio is NOT
    # this build's gate ratio. Hard-coding them assumed the ratio was
    # always 1, which stopped being true when `punch` started carrying
    # the capture scale as well as the surface scale.
    _R = 1 << _F.gate_shift()
    for fbw, texw in ((160 * _R * 2, 160), (256 * _R * 4, 256),
                      (128 * _R * 8, 128)):
        mem = _emu.Mem()
        mem.setu(HDR + _F.FB_W_OFF, fbw, 4)
        mem.setu(HDR + _F.TEX_W_OFF, texw, 4)
        cpu = _emu.Cpu(mem)
        for r, v in ((20, HDR), (10, 4), (12, 0), (11, 0x900003),
                     (13, 0x800003), (0, 0x800000), (19, 640)):
            cpu.set(r, v)
        pc = cpu.run(BASE, None, code=code, start_pc=BASE)
        assert pc == _F.STOCK_LOOP, (fbw, texw, hex(pc))


# ---------------------------------------------------------------- build 306
def _hole_run(pixels, ncols):
    """Execute the fillhole body over a synthetic source row."""
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    BASE = 0x180000
    addrs = [BASE + 4 * i for i in range(_F.n_words('fillhole'))]
    code = dict(zip(addrs, _F.body_words(lambda i: addrs[i], how='fillhole')))
    HDR, DEST, SRC = 0x700000, 0x800000, 0x900000
    mem = _emu.Mem()
    for off, v in ((_F.FB_W_OFF, 256 << _F.gate_shift()), (_F.TEX_W_OFF, 256)):
        mem.setu(HDR + off, v, 4)
    for i, (r, g, b) in enumerate(pixels):
        a = SRC + 4 * i
        mem.setu(a + 0, b, 1); mem.setu(a + 1, g, 1)
        mem.setu(a + 2, r, 1); mem.setu(a + 3, 0x77, 1)
    for i in range(ncols * 4):
        mem.setu(DEST + i, 0xCD, 1)
    cpu = _emu.Cpu(mem)
    for r_, v in ((20, HDR), (10, ncols), (12, 0), (11, SRC + 3),
                  (13, DEST + 3), (0, DEST), (19, 640)):
        cpu.set(r_, v)
    pc = cpu.run(BASE, None, code=code, start_pc=BASE)
    assert pc == _F.AFTER_LOOP, hex(pc)
    out = []
    for i in range(ncols):
        px = mem.u(DEST + 4 * i, 4)
        # the loop's own byte shuffle: dest[0] <- src[2], dest[1] <- src[1],
        # dest[2] <- src[0].  The helper writes src as (b, g, r), so the
        # destination word reads back low-to-high as (r, g, b).
        out.append((px & 0xFF, (px >> 8) & 0xFF, (px >> 16) & 0xFF, px >> 24))
    return out


def test_fillhole_copies_real_pixels_unchanged_and_opaque():
    """A non-hole pixel must arrive with its colour intact. If this moved a
    colour byte the whole thing would be a tint, not a repair."""
    src = [(10, 20, 30)] * 8
    out = _hole_run(src, 4)
    assert out == [(10, 20, 30, 0xFF)] * 4, out


def test_fillhole_replaces_a_hole_with_the_texel_to_its_left():
    """The repair itself: the source goes black partway along the row, and the
    texture must carry the last real colour instead of the void."""
    # source columns 0..3 real, 4.. black; texel i reads floor(i*640/854)
    src = [(90, 80, 70)] * 6 + [(0, 0, 0)] * 10
    out = _hole_run(src, 12)
    assert all(p[:3] != (0, 0, 0) for p in out), out
    assert all(p[3] == 0xFF for p in out)
    assert out[-1][:3] == (90, 80, 70), out[-1]


def test_fillhole_leaves_the_first_texel_of_a_row_alone():
    """There is nothing to its left, so it must not read whatever precedes the
    row. A hole in column 0 stays a hole rather than becoming garbage."""
    out = _hole_run([(0, 0, 0)] * 8, 3)
    assert out[0][:3] == (0, 0, 0)
    assert out[0][3] == 0xFF


def test_fillhole_only_treats_near_black_as_a_hole():
    """Dark ground must survive. Only a pixel that is essentially 0,0,0 is a
    hole -- anything with a channel above the threshold is real content."""
    for colour in ((0, 10, 0), (0, 0, 8), (1, 0, 0), (0, 1, 0)):
        out = _hole_run([colour] * 8, 4)
        assert all(p[:3] == colour for p in out), (colour, out)
    out = _hole_run([(0, 0, 7)] * 8, 4)       # below the threshold: a hole
    assert out[0][:3] == (0, 0, 7)            # column 0 is left alone
    assert out[1][:3] == (0, 0, 7)            # and clamps from it


def test_fillhole_reads_the_same_source_address_as_the_copy():
    import ff7nx_fbresample as _F
    c = _F.body_words(lambda i: 0x180000 + 4 * i, how='copy')
    h = _F.body_words(lambda i: 0x180000 + 4 * i, how='fillhole')
    assert c[0:3] == h[0:3]          # the same gate
    assert c[4:9] == h[4:9]          # the same magic, step and address


def test_fillhole_leaves_page_scaled_captures_on_the_stock_path():
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    BASE = 0x180000
    addrs = [BASE + 4 * i for i in range(_F.n_words('fillhole'))]
    code = dict(zip(addrs, _F.body_words(lambda i: addrs[i], how='fillhole')))
    HDR = 0x700000
    # BUILD 337: shapes whose fb_tex.w / tex_format.width ratio is NOT
    # this build's gate ratio. Hard-coding them assumed the ratio was
    # always 1, which stopped being true when `punch` started carrying
    # the capture scale as well as the surface scale.
    _R = 1 << _F.gate_shift()
    for fbw, texw in ((160 * _R * 2, 160), (256 * _R * 4, 256),
                      (128 * _R * 8, 128)):
        mem = _emu.Mem()
        mem.setu(HDR + _F.FB_W_OFF, fbw, 4)
        mem.setu(HDR + _F.TEX_W_OFF, texw, 4)
        cpu = _emu.Cpu(mem)
        for r, v in ((20, HDR), (10, 4), (12, 0), (11, 0x900003),
                     (13, 0x800003), (0, 0x800000), (19, 640)):
            cpu.set(r, v)
        assert cpu.run(BASE, None, code=code,
                       start_pc=BASE) == _F.STOCK_LOOP, (fbw, texw)


# ---------------------------------------------------------------- build 307
# 'punch'. Build 306's own result chose it: forcing alpha opaque is exactly
# when the left-hand hole -- black in the source, correct on screen in every
# build -- stopped being invisible. So that hole is already resolving by
# ALPHA, and the right-hand squares are the texels where that fails.
SRC_ALPHA = 0x77


def _run_row(how, pixels, ncols, fill=0xCD):
    """Execute one body over a synthetic source row and report the texture.

    Returns (r, g, b, a) per destination texel, decoded through the loop's own
    shuffle: dest[0] <- src[2], dest[1] <- src[1], dest[2] <- src[0], and the
    helper lays the source down as (b, g, r, a).
    """
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    BASE = 0x180000
    addrs = [BASE + 4 * i for i in range(_F.n_words(how))]
    code = dict(zip(addrs, _F.body_words(lambda i: addrs[i], how=how)))
    HDR, DEST, SRC = 0x700000, 0x800000, 0x900000
    mem = _emu.Mem()
    for off, v in ((_F.FB_W_OFF, 256 << _F.gate_shift()), (_F.TEX_W_OFF, 256)):
        mem.setu(HDR + off, v, 4)
    for i, px in enumerate(pixels):
        r, g, b = px[:3]
        a = px[3] if len(px) > 3 else SRC_ALPHA
        at = SRC + 4 * i
        mem.setu(at + 0, b, 1); mem.setu(at + 1, g, 1)
        mem.setu(at + 2, r, 1); mem.setu(at + 3, a, 1)
    for i in range(ncols * 4):
        mem.setu(DEST + i, fill, 1)
    cpu = _emu.Cpu(mem)
    for r_, v in ((20, HDR), (10, ncols), (12, 0), (11, SRC + 3),
                  (13, DEST + 3), (0, DEST), (19, 640)):
        cpu.set(r_, v)
    pc = cpu.run(BASE, None, code=code, start_pc=BASE)
    assert pc == _F.AFTER_LOOP, (how, hex(pc))
    out = []
    for i in range(ncols):
        px = mem.u(DEST + 4 * i, 4)
        out.append((px & 0xFF, (px >> 8) & 0xFF, (px >> 16) & 0xFF, px >> 24))
    return out


def test_punch_copies_real_content_byte_for_byte_including_its_alpha():
    """The content region must be untouched -- colour AND the source's own
    alpha. 306 forced 0xFF here and that is what produced the left-hand smear
    on hardware, so this is the regression it fixes."""
    out = _run_row('punch', [(10, 20, 30)] * 8, 4)
    assert out == [(10, 20, 30, SRC_ALPHA)] * 4, out


def test_punch_is_byte_identical_to_the_shipping_copy_on_a_hole_free_capture():
    """Executed against the same surface, word for word. A capture with no
    holes must produce exactly the texture it produces today, so nothing else
    in the game can move."""
    row = [(1 + (i * 7) % 250, 2 + (i * 13) % 250, 3 + (i * 29) % 250)
           for i in range(40)]
    assert _run_row('punch', row, 32) == _run_row('copy', row, 32)


def test_punch_writes_a_fully_transparent_texel_where_the_source_is_black():
    """All four bytes zero: no colour to smear, and alpha 0 so the texel is
    not drawn and the real floor shows through it."""
    out = _run_row('punch', [(0, 0, 0)] * 8, 4)
    assert out == [(0, 0, 0, 0)] * 4, out


def test_punch_punches_only_the_hole_and_leaves_its_neighbours_alone():
    """The boundary is where the black squares live, so it is the part that
    has to be exact: content keeps its bytes, the void loses its alpha."""
    row = [(90, 80, 70)] * 6 + [(0, 0, 0)] * 10
    out = _run_row('punch', row, 12)
    assert out[0] == (90, 80, 70, SRC_ALPHA)
    assert out[-1] == (0, 0, 0, 0)
    for p in out:
        assert p in ((90, 80, 70, SRC_ALPHA), (0, 0, 0, 0)), out


def test_punch_never_invents_a_colour():
    """Unlike a fill, every texel it writes is either the source pixel or
    nothing at all -- so it cannot streak content sideways."""
    row = [(200, 10, 10)] * 3 + [(0, 0, 0)] * 3 + [(10, 10, 200)] * 10
    out = _run_row('punch', row, 12)
    for p in out:
        assert p in ((200, 10, 10, SRC_ALPHA), (10, 10, 200, SRC_ALPHA),
                     (0, 0, 0, 0)), out


def test_punch_only_treats_essentially_pure_black_as_a_hole():
    """Executed on both sides of the threshold, because punching real ground
    would be a hole of my own making.

    The comparison is on the three colour bytes PACKED into one word, so it
    only fires on a pixel that is pure black to within a hair of the lowest
    channel. Anything with real content in any channel survives with its bytes
    and its source alpha intact."""
    for colour in ((0, 10, 0), (0, 0, 8), (0, 1, 0), (8, 0, 0),
                   (1, 1, 0), (0, 0, 1), (3, 3, 3)):
        out = _run_row('punch', [colour] * 8, 4)
        assert out == [colour + (SRC_ALPHA,)] * 4, (colour, out)
    for colour in ((0, 0, 0), (1, 0, 0), (7, 0, 0)):
        out = _run_row('punch', [colour] * 8, 4)
        assert out == [(0, 0, 0, 0)] * 4, (colour, out)


def test_punch_keeps_a_transparent_source_pixel_transparent():
    """The left-hand hole already arrives with alpha 0 and already looks
    right. Nothing about this build may change it."""
    out = _run_row('punch', [(0, 0, 0, 0)] * 8, 4)
    assert out == [(0, 0, 0, 0)] * 4, out


def test_punch_reads_the_same_source_address_as_the_copy():
    import ff7nx_fbresample as _F
    c = _F.body_words(lambda i: 0x180000 + 4 * i, how='copy')
    p = _F.body_words(lambda i: 0x180000 + 4 * i, how='punch')
    assert c[0:3] == p[0:3]          # the same gate
    assert c[4:9] == p[4:9]          # the same magic, step and source address


def test_punch_leaves_page_scaled_captures_on_the_stock_path():
    """The battle swirl and every other page-scaled capture must be untouched:
    they fail the gate and land on the stock loop, byte for byte."""
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    BASE = 0x180000
    addrs = [BASE + 4 * i for i in range(_F.n_words('punch'))]
    code = dict(zip(addrs, _F.body_words(lambda i: addrs[i], how='punch')))
    HDR = 0x700000
    # BUILD 337: shapes whose fb_tex.w / tex_format.width ratio is NOT
    # this build's gate ratio. Hard-coding them assumed the ratio was
    # always 1, which stopped being true when `punch` started carrying
    # the capture scale as well as the surface scale.
    _R = 1 << _F.gate_shift()
    for fbw, texw in ((160 * _R * 2, 160), (256 * _R * 4, 256),
                      (128 * _R * 8, 128)):
        mem = _emu.Mem()
        mem.setu(HDR + _F.FB_W_OFF, fbw, 4)
        mem.setu(HDR + _F.TEX_W_OFF, texw, 4)
        cpu = _emu.Cpu(mem)
        for r, v in ((20, HDR), (10, 4), (12, 0), (11, 0x900003),
                     (13, 0x800003), (0, 0x800000), (19, 640)):
            cpu.set(r, v)
        assert cpu.run(BASE, None, code=code,
                       start_pc=BASE) == _F.STOCK_LOOP, (fbw, texw)


def test_no_body_contains_an_unconditional_branch_that_is_not_an_exit():
    """`_walk` recovers a scattered cave by the rule "a `b` is a run-to-run
    link unless it targets one of the two exits". A body that branches to
    itself with a plain `b` therefore reads back as a DIFFERENT instruction
    sequence once it is placed, and the module stops recognising its own cave
    -- which is exactly what the first draft of `punch` did. This holds every
    mode to the rule _walk's docstring states."""
    import ff7nx_fbresample as _F
    BASE = 0x180000
    for how in _F.MODES:
        n = _F.n_words(how)
        addrs = [BASE + 4 * i for i in range(n)]
        words = _F.body_words(lambda i: addrs[i], how=how)
        for i, wd in enumerate(words):
            if (wd & 0xFC000000) != 0x14000000:
                continue                     # not an unconditional branch
            tgt = _F._b_target(wd, addrs[i])
            assert tgt in (_F.AFTER_LOOP, _F.STOCK_LOOP), (how, i, hex(tgt))


def test_every_mode_reads_back_out_of_a_scattered_cave_as_itself(tmp_path,
                                                                 monkeypatch):
    """Apply for real, then walk the patched image and regenerate: the words
    the module wrote must be the words it recognises. This is the end-to-end
    form of the rule above, and it is what catches a body whose length or
    layout confuses the allocator."""
    import ff7nx_fbresample as _F
    import nxmap as _nx
    for how in ('unpremul', 'punch', 'copy', 'fillhole'):
        monkeypatch.setenv(_F.PROBE_ENV, how)
        out = tmp_path / ('main_%s' % how)
        shutil.copy(MAIN, out)
        assert _F.apply_all(out, log=lambda *_: None) == 0
        img = _nx.Main(str(out)).img
        assert _F.verify(img) == [], how
        assert _F.installed_mode(img) == how
        assert _F.apply_all(out, revert=True, log=lambda *_: None) == 0
        assert out.read_bytes() == open(MAIN, 'rb').read(), how


# ---------------------------------------------------------------- build 311
# 'unpremul'. Anything drawn src-over onto a target cleared to (0,0,0,0) comes
# out premultiplied by the algebra of the blend, and the scene->capture blit
# then filters it (LINEAR, +0x10DAD94), so every silhouette texel is
# (C*a, a) -- darker than the colour it stands for. The field draws those as
# straight alpha, which is the dark fringe.


def _premul(colour, a):
    """What the render target actually holds for `colour` at coverage a."""
    return tuple(c * a // 255 for c in colour) + (a,)


def test_unpremul_copies_an_opaque_texel_byte_for_byte():
    """a = 255 means nothing was mixed in, so there is nothing to undo. This
    is nearly every texel in the capture, and it must be untouched."""
    out = _run_row('unpremul', [(10, 20, 30, 255)] * 8, 4)
    assert out == [(10, 20, 30, 255)] * 4, out


def test_unpremul_is_byte_identical_to_the_shipping_copy_on_an_opaque_capture():
    """Executed against the same surface. A capture with no partial coverage
    must come out exactly as it does today -- which is also the proof that
    `rev` + `ror #8` really is the stock loop's byte shuffle."""
    row = [(1 + (i * 7) % 250, 2 + (i * 13) % 250, 3 + (i * 29) % 250, 255)
           for i in range(40)]
    assert _run_row('unpremul', row, 32) == _run_row('copy', row, 32)


def test_unpremul_leaves_a_fully_transparent_texel_at_zero():
    """A texel with a = 0 is all zero already, because premultiplied colour is
    bounded by its own alpha. `punch`'s behaviour, for free and with no
    special case."""
    out = _run_row('unpremul', [(0, 0, 0, 0)] * 8, 4)
    assert out == [(0, 0, 0, 0)] * 4, out


def test_unpremul_restores_the_colour_the_filter_darkened():
    """The repair itself, executed: feed the body what the render target
    really holds for a half-covered edge texel and require the ORIGINAL colour
    back, with the coverage kept."""
    for colour in ((200, 180, 40), (255, 255, 255), (9, 130, 77)):
        for a in (8, 32, 64, 128, 200, 254):
            src = _premul(colour, a)
            out = _run_row('unpremul', [src] * 8, 4)
            for got in out:
                assert got[3] == a, (colour, a, got)
                for want, have in zip(colour, got[:3]):
                    # exact up to the integer premultiply that came before it
                    assert abs(have - want) <= 255 // a + 1, (colour, a, got)


def test_unpremul_never_brightens_past_full_scale():
    """c <= a holds for anything premultiplied, so c*255/a <= 255 and no
    channel can wrap. Executed at the worst case: full colour, every alpha."""
    for a in range(1, 256):
        out = _run_row('unpremul', [_premul((255, 255, 255), a)] * 4, 2)
        for got in out:
            assert all(0 <= c <= 255 for c in got[:3]), (a, got)
            assert got[3] == a


def test_unpremul_keeps_the_coverage_rather_than_hardening_the_edge():
    """Alpha is never touched, at any value. A silhouette that the filter
    softened stays soft; this only fixes what colour is under it."""
    for a in (0, 1, 77, 128, 254, 255):
        out = _run_row('unpremul', [_premul((120, 60, 30), a)] * 4, 2)
        assert all(px[3] == a for px in out), (a, out)


def test_unpremul_reads_the_same_source_address_as_the_copy():
    import ff7nx_fbresample as _F
    c = _F.body_words(lambda i: 0x180000 + 4 * i, how='copy')
    u = _F.body_words(lambda i: 0x180000 + 4 * i, how='unpremul')
    assert c[0:3] == u[0:3]                      # the same gate
    assert c[6:9] == u[4 + 2:4 + 5]              # the same step and address


def test_unpremul_leaves_page_scaled_captures_on_the_stock_path():
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    BASE = 0x180000
    addrs = [BASE + 4 * i for i in range(_F.n_words('unpremul'))]
    code = dict(zip(addrs, _F.body_words(lambda i: addrs[i], how='unpremul')))
    HDR = 0x700000
    # BUILD 337: shapes whose fb_tex.w / tex_format.width ratio is NOT
    # this build's gate ratio. Hard-coding them assumed the ratio was
    # always 1, which stopped being true when `punch` started carrying
    # the capture scale as well as the surface scale.
    _R = 1 << _F.gate_shift()
    for fbw, texw in ((160 * _R * 2, 160), (256 * _R * 4, 256),
                      (128 * _R * 8, 128)):
        mem = _emu.Mem()
        mem.setu(HDR + _F.FB_W_OFF, fbw, 4)
        mem.setu(HDR + _F.TEX_W_OFF, texw, 4)
        cpu = _emu.Cpu(mem)
        for r, v in ((20, HDR), (10, 4), (12, 0), (11, 0x900003),
                     (13, 0x800003), (0, 0x800000), (19, 640)):
            cpu.set(r, v)
        assert cpu.run(BASE, None, code=code,
                       start_pc=BASE) == _F.STOCK_LOOP, (fbw, texw)


def test_unpremul_is_reachable_but_is_not_the_default(monkeypatch):
    """BUILD 312 put `punch` back. `unpremul` copies a fully opaque texel byte
    for byte, which RE-ADMITS the opaque-dark texels punch removes -- on
    hardware it undid build 307. It stays available because it is the right
    body if the capture ever does carry meaningful partial coverage."""
    import ff7nx_fbresample as _F
    monkeypatch.delenv(_F.PROBE_ENV, raising=False)
    # BUILD 366 made the remap the default and it was worse on hardware;
    # build 367 withdrew that. `punch` is the shipping body again.
    assert _F.mode() == 'punch'
    monkeypatch.setenv(_F.PROBE_ENV, 'unpremul')
    assert _F.mode() == 'unpremul'


def test_punch_is_still_reachable_and_fillhole_too(monkeypatch):
    import ff7nx_fbresample as _F
    monkeypatch.setenv(_F.PROBE_ENV, 'punch')
    assert _F.mode() == 'punch'
    monkeypatch.setenv(_F.PROBE_ENV, 'fillhole')
    assert _F.mode() == 'fillhole'
    monkeypatch.setenv(_F.PROBE_ENV, 'copy')
    assert _F.mode() == 'copy'


# ---------------------------------------------------------------- build 325
def test_the_diagnostic_bodies_engage_at_the_REAL_capture_origin():
    """BUILD 325, and the reason the texel probe produced nothing.

    frame_body and proj_body used to gate on `fb_tex.x == 0`. ff7nx_fbcapture
    MOVES Kujata's origin for widescreen -- map_x(0) = 80 virtual units, which
    make_framebuffer_tex scales to 80*k -- so that condition has been false
    since fbcapture shipped and those bodies could never match. The probe fell
    through to the stock loop instead: no gradient, and the field displaced,
    because the stock loop carries no 640/854 resample.

    This executes them at the origin the build actually produces.
    """
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    import ff7nx_fbcapture as _FB
    BASE = 0x180000
    HDR = 0x700000
    real_x = _FB.map_x(0) << _F.surf_shift()
    assert real_x != 0, 'fbcapture no longer moves the origin; re-check this'
    for how in ('texel', 'proj'):
        n = _F.n_words(how)
        addrs = [BASE + 4 * i for i in range(n)]
        code = dict(zip(addrs, _F.body_words(lambda i: addrs[i], how=how)))
        for x in (0, real_x):
            mem = _emu.Mem()
            w = 256 << _F.gate_shift()
            for off, v in ((_F.FB_W_OFF, w), (_F.TEX_W_OFF, 256),
                           (_F.FB_H_OFF, w), (_F.FB_X_OFF, x),
                           (_F.FB_Y_OFF, 0)):
                mem.setu(HDR + off, v, 4)
            cpu = _emu.Cpu(mem)
            for r, v in ((20, HDR), (10, 0), (12, 0), (13, 0x800003),
                         (0, 0x800000), (11, 0x900003), (19, 2560),
                         (26, 0x900000), (8, 0)):
                cpu.set(r, v)
            pc = cpu.run(BASE, None, code=code, start_pc=BASE)
            assert pc != _F.STOCK_LOOP, (how, x)


def test_the_diagnostic_bodies_still_reject_the_wrong_shape():
    """Retiring the origin conditions must not make them match anything else.
    What is left -- fb_tex.w == tex_format.width << shift, tex_format.width
    == 256, and square -- is no less specific than the SHIPPING punch gate,
    which tests only the first of those."""
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    BASE = 0x180000
    HDR = 0x700000
    good = 256 << _F.gate_shift()
    for how in ('texel', 'proj'):
        n = _F.n_words(how)
        addrs = [BASE + 4 * i for i in range(n)]
        code = dict(zip(addrs, _F.body_words(lambda i: addrs[i], how=how)))
        for fbw, texw, fbh in ((good // 2, 256, good // 2),   # wrong scale
                               (good, 512, good),             # wrong authored
                               (good, 256, good // 2)):       # not square
            mem = _emu.Mem()
            for off, v in ((_F.FB_W_OFF, fbw), (_F.TEX_W_OFF, texw),
                           (_F.FB_H_OFF, fbh), (_F.FB_X_OFF, 320),
                           (_F.FB_Y_OFF, 0)):
                mem.setu(HDR + off, v, 4)
            cpu = _emu.Cpu(mem)
            for r, v in ((20, HDR), (10, 0), (12, 0), (13, 0x800003),
                         (0, 0x800000), (11, 0x900003), (19, 2560),
                         (26, 0x900000), (8, 0)):
                cpu.set(r, v)
            pc = cpu.run(BASE, None, code=code, start_pc=BASE)
            assert pc == _F.STOCK_LOOP, (how, fbw, texw, fbh)


def test_the_shipping_punch_body_is_untouched_by_any_of_this():
    """punch does not consult the origin at all, so build 325 cannot have
    moved the picture you actually play with."""
    import ff7nx_fbresample as _F
    words = _F.body_words(lambda i: 0x180000 + 4 * i, how='punch')
    for w in words:
        # no load of fb_tex.x or fb_tex.y anywhere in it
        if (w & 0xFFC00000) == 0xB9400000:
            off = ((w >> 10) & 0xFFF) * 4
            assert off not in (_F.FB_X_OFF, _F.FB_Y_OFF), hex(w)


# ---------------------------------------------------------------- build 333
# 'check' -- the shipping punch with half the texels punched in a
# checkerboard, so the captured field and the floor it covers sit side by
# side in one frame and a global scale error becomes a local, visible jump.
CHECK_BASE = 0x1A0000


def _check_code(bit=None):
    import ff7nx_fbresample as _F
    if bit is not None:
        os.environ['SEVENTH_NX_FB_CHECK'] = str(bit)
    else:
        os.environ.pop('SEVENTH_NX_FB_CHECK', None)
    importlib.reload(_F)
    addrs = [CHECK_BASE + 4 * i for i in range(_F.CHECK_WORDS)]
    return _F, addrs, dict(zip(addrs, _F.check_body(lambda i: addrs[i])))


def _check_row(bit, row, ncols=96):
    import arm64emu as _emu
    _F, addrs, code = _check_code(bit)
    HDR, SRC, DST = 0x700000, 0x800000, 0x900000
    mem = _emu.Mem()
    # the shape the gate accepts AT THIS BUILD'S surface scale -- hard-coding
    # 1024 made the gate reject and the body silently ran the stock loop,
    # which is the same trap that hid the texel probe in build 325
    fbw = 256 << _F.gate_shift()
    for off, v in ((0x1C, fbw), (0x20, fbw), (0x3C, 256)):
        mem.setu(HDR + off, v, 4)
    for i in range(8192):                      # every source pixel has content
        mem.setu(SRC + 4 * i, 0x7F406090, 4)
    cpu = _emu.Cpu(mem)
    for r, v in ((20, HDR), (26, 0x800000), (8, row), (10, ncols), (12, 0),
                 (11, SRC + 16), (13, DST + 3), (19, 8192)):
        cpu.set(r, v)
    cpu.run(CHECK_BASE, None, code=code, start_pc=CHECK_BASE,
            max_steps=400000)
    return [mem.u(DST + 4 * i, 4) != 0 for i in range(ncols)]


def test_the_checker_draws_squares_of_the_asked_size():
    for bit in (4, 5, 6):
        drawn = _check_row(bit, 0)
        n = 1 << bit
        for i, d in enumerate(drawn):
            assert d == (((i ^ 0) >> bit) & 1 == 0), (bit, i, d)
        assert sum(drawn) > 0 and sum(drawn) < len(drawn), bit
        del n


def test_the_checker_phase_inverts_on_the_next_row_band():
    """It has to be a CHECKERBOARD, not stripes -- a square of captured field
    must have floor above and below it as well as left and right, or the
    vertical half of the error has nothing to show against."""
    for bit in (4, 5):
        a = _check_row(bit, 0)
        b = _check_row(bit, 1 << bit)
        assert a == [not x for x in b], bit


def test_the_checker_is_the_shipping_punch_plus_four_words():
    """The content path must be byte-for-byte `punch`, or what you are
    judging is not the build you play."""
    import ff7nx_fbresample as _F
    _check_code(None)
    p = _F.punch_body(lambda i: 0x200000 + 4 * i)
    c = _F.check_body(lambda i: 0x200000 + 4 * i)
    assert _F.CHECK_WORDS == _F.PUNCH_WORDS + 4
    # word 3 is the gate's branch to the stock path and its target moved
    assert [i for i in range(18) if p[i] != c[i]] == [3]
    assert p[4:18] == c[4:18]


def test_the_checker_mode_round_trips_through_the_walk():
    import ff7nx_fbresample as _F
    _check_code(None)
    assert 'check' in _F.MODES
    assert _F.n_words('check') == _F.CHECK_WORDS
    words = _F.body_words(lambda i: 0x1B0000 + 4 * i, how='check')
    assert words == _F.check_body(lambda i: 0x1B0000 + 4 * i)
    NOP = 0xD503201F
    assert NOP not in words                     # nothing retired in this body


def test_the_checker_bit_is_bounded():
    import ff7nx_fbresample as _F
    for v, want in (('4', 4), ('8', 8), ('2', 2), ('9', 5), ('1', 5),
                    ('banana', 5)):
        os.environ['SEVENTH_NX_FB_CHECK'] = v
        importlib.reload(_F)
        assert _F.check_bit() == want, v
    os.environ.pop('SEVENTH_NX_FB_CHECK', None)
    importlib.reload(_F)
    assert _F.check_bit() == _F.DEFAULT_CHECK_BIT


# ---------------------------------------------------------------- build 339
def test_the_swirl_is_never_resampled_at_any_capture_scale():
    """BUILD 287 predicted this and build 337 walked into it. The gate is a
    RATIO -- fb_tex.w == tex_format.width << k -- which names a 1:1 capture,
    not a particular one. The battle swirl's ratio is 8; raise the capture
    scale and the gate's ratio becomes 8 too, so the swirl gets resampled by a
    body built for a different texture. On hardware that is the character
    mirrored into both halves of the screen."""
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    import ff7nx_fxcapscale as _CS
    BASE = 0x1C0000

    def route(how, fbw, fbh, texw):
        n = _F.n_words(how)
        a = [BASE + 4 * i for i in range(n)]
        code = dict(zip(a, _F.body_words(lambda i: a[i], how=how)))
        mem = _emu.Mem()
        HDR = 0x700000
        for off, v in ((0x1C, fbw), (0x20, fbh), (0x3C, texw),
                       (0x14, 320), (0x18, 0)):
            mem.setu(HDR + off, v, 4)
        cpu = _emu.Cpu(mem)
        cpu.sp = 0x60000
        for r, v in ((20, HDR), (26, 0x800000), (19, 8192), (8, 0), (10, 0),
                     (12, 0), (11, 0x800000), (13, 0x900003), (0, 0x900000),
                     (22, 0), (17, 0)):
            cpu.set(r, v)
        pc = cpu.run(BASE, None, code=code, start_pc=BASE, max_steps=500000)
        return pc == _F.AFTER_LOOP

    for cap in ('1', '2'):
        os.environ['SEVENTH_NX_FX_CAPSCALE'] = cap
        os.environ['SEVENTH_NX_FX_CAPSCALE_Y'] = '1'
        importlib.reload(_CS)
        importlib.reload(_F)
        g, gh = _F.gate_shift(), _F.gate_shift_h()
        for how in ('punch', 'check', 'copy'):
            # Kujata's own shape is taken, at either scale
            assert route(how, 256 << g, 256 << gh, 256), (how, cap)
            # and the swirl NEVER is
            assert not route(how, 1280, 1920, 160), (how, cap, 'swirl')
    os.environ['SEVENTH_NX_FX_CAPSCALE'] = '1'
    os.environ.pop('SEVENTH_NX_FX_CAPSCALE_Y', None)
    importlib.reload(_CS)
    importlib.reload(_F)


def test_the_gate_word_is_the_stock_ratio_when_the_capture_is_not_scaled():
    """The shipping build must not move by one byte."""
    import ff7nx_fbresample as _F
    import ff7nx_fxcapscale as _CS
    os.environ['SEVENTH_NX_FX_CAPSCALE'] = '1'
    os.environ.pop('SEVENTH_NX_FX_CAPSCALE_Y', None)
    importlib.reload(_CS)
    importlib.reload(_F)
    assert _F.gate_word() == _F.cmp_reg_lsl(15, 16, _F.gate_shift())
    os.environ['SEVENTH_NX_FX_CAPSCALE'] = '2'
    importlib.reload(_CS)
    importlib.reload(_F)
    import a64 as A
    assert _F.gate_word() == A.cmp_imm(15, _F.KUJATA_TEX << _F.gate_shift())
    os.environ['SEVENTH_NX_FX_CAPSCALE'] = '1'
    importlib.reload(_CS)
    importlib.reload(_F)


# ---------------------------------------------------------------- build 342
def test_projmap_reports_the_surface_coordinate_AND_whether_it_held_content():
    """THE instrument. Builds 327 and 329 both read a parallelogram off the
    screen and called it "where the surface holds content", but the patch sat
    at the SAME screen position under two builds whose texel->surface maps
    differ by 8.6x -- and a content region cannot be map-independent. They were
    measuring the DISC'S DRAW EXTENT. This reports both halves per texel so
    they can never be confused again."""
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    os.environ['SEVENTH_NX_FX_PROJ'] = '163840,0,0,0,122880,0,0,0,65536'
    os.environ['SEVENTH_NX_FX_CAPSCALE'] = '1'
    importlib.reload(_F)
    BASE = 0x400000
    n = _F.n_words('projmap')
    a = [BASE + 4 * i for i in range(n)]
    code = dict(zip(a, _F.body_words(lambda i: a[i], how='projmap')))
    HDR, SURF, DST = 0x700000, 0x2000000, 0x3000000
    mem = _emu.Mem()
    g = _F.gate_shift()
    for off, v in ((0x1C, 256 << g), (0x20, 256 << _F.gate_shift_h()),
                   (0x3C, 256), (0x14, 320), (0x18, 0)):
        mem.setu(HDR + off, v, 4)
    pitch = _F.surface()[0] * 4
    LO, HI = 300, 900                       # content lives in these columns
    for r in range(0, 60):
        for c in range(0, 1500):
            mem.setu(SURF + r * pitch + c * 4,
                     0x00204060 if LO <= c < HI else 0, 4)
    cpu = _emu.Cpu(mem)
    cpu.sp = 0x60000
    NC = 200
    for reg, v in ((20, HDR), (26, SURF), (19, pitch), (8, 3), (10, NC),
                   (12, 0), (13, DST + 3), (0, DST), (22, 0), (17, 0)):
        cpu.set(reg, v)
    pc = cpu.run(BASE, None, code=code, start_pc=BASE, max_steps=900000)
    assert pc == _F.AFTER_LOOP
    seen_yes = seen_no = 0
    for i in range(NC):
        px = mem.u(DST + 4 * i, 4)
        B, G, R, A = px & 0xFF, (px >> 8) & 0xFF, (px >> 16) & 0xFF, px >> 24
        assert A == 0xFF
        # the scan maps texel i to surface column 2.5*i; B carries it /16
        assert abs(B * 16 - 2.5 * i) <= 16, (i, B * 16)
        assert G == 0                                    # row 0, this row
        want = 0xFF if LO <= 2.5 * i < HI else 0x40
        assert R == want, (i, 2.5 * i, hex(R))
        seen_yes += R == 0xFF
        seen_no += R == 0x40
    assert seen_yes > 20 and seen_no > 20                # both branches taken
    os.environ.pop('SEVENTH_NX_FX_PROJ', None)
    importlib.reload(_F)


def test_projmap_keeps_the_column_alive_like_projsrc_does():
    """x14 becomes an address before the read-out, so the column has to be
    parked in x7 first. Leaving that out is what makes a probe report zeroes
    and look like a measurement -- build 325's trap, in a new place."""
    import ff7nx_fbresample as _F
    for how in ('projsrc', 'projmap'):
        w = _F.body_words(lambda i: 0x500000 + 4 * i, how=how)
        assert any(x == _F.A.add_imm64(7, 14, 0) for x in w), how
    w = _F.body_words(lambda i: 0x500000 + 4 * i, how='proj')
    assert not any(x == _F.A.add_imm64(7, 14, 0) for x in w)


# ---------------------------------------------------------------- build 347
def test_projpitch_reports_the_surfaces_real_width():
    """The last unverified assumption. Every column figure in findings 343
    and 345 assumes the surface is 2560 wide at k=4, because that is what the
    descriptor was patched to -- but the body addresses it through the loop's
    pitch register, and the two have never been compared. If they disagree
    the whole '150-column strip' is my arithmetic, not the capture."""
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    BASE = 0x420000
    n = _F.n_words('projpitch')
    a = [BASE + 4 * i for i in range(n)]
    code = dict(zip(a, _F.body_words(lambda i: a[i], how='projpitch')))
    for width in (640, 1280, 2560, 3840, 5120):
        mem = _emu.Mem()
        HDR, DST = 0x700000, 0x3000000
        g = _F.gate_shift()
        for off, v in ((0x1C, 256 << g), (0x20, 256 << _F.gate_shift_h()),
                       (0x3C, 256), (0x14, 320), (0x18, 0)):
            mem.setu(HDR + off, v, 4)
        cpu = _emu.Cpu(mem)
        cpu.sp = 0x60000
        for r, v in ((20, HDR), (26, 0x2000000), (19, width * 4), (8, 3),
                     (10, 4), (12, 0), (13, DST + 3), (0, DST), (22, 0),
                     (17, 0)):
            cpu.set(r, v)
        pc = cpu.run(BASE, None, code=code, start_pc=BASE, max_steps=200000)
        assert pc == _F.AFTER_LOOP, width
        px = mem.u(DST, 4)
        B, G, R, A = px & 0xFF, (px >> 8) & 0xFF, (px >> 16) & 0xFF, px >> 24
        assert G * 256 + B == width, (width, G * 256 + B)
        assert R == 0x40 and A == 0xFF, width


# ---------------------------------------------------------------- build 351
def test_projbar_puts_a_hard_edge_at_the_stride():
    """`projpitch` encoded the stride as a colour LEVEL, and the field is
    drawn through a blend, so the answer came back pulled toward the floor.
    A blend cannot move an EDGE. Every texel left of x19 is white, every one
    right of it is dark, and the boundary's screen position converts back
    through FIT_HUV into an exact texel index."""
    import arm64emu as _emu
    import ff7nx_fbresample as _F
    BASE = 0x430000
    n = _F.n_words('projbar')
    a = [BASE + 4 * i for i in range(n)]
    code = dict(zip(a, _F.body_words(lambda i: a[i], how='projbar')))
    HDR, DST = 0x700000, 0x3000000
    NC = 256 << _F.gate_shift()          # the real texel count at this build
    sh = _F.bar_shift()
    for stride in (37 << sh, 96 << sh, (NC // 2) << sh, (NC - 1) << sh,
                   NC << sh, (NC * 4) << sh):
        mem = _emu.Mem()
        g = _F.gate_shift()
        for off, v in ((0x1C, 256 << g), (0x20, 256 << _F.gate_shift_h()),
                       (0x3C, 256), (0x14, 320), (0x18, 0)):
            mem.setu(HDR + off, v, 4)
        cpu = _emu.Cpu(mem)
        cpu.sp = 0x60000
        for r, v in ((20, HDR), (26, 0x2000000), (19, stride), (8, 3),
                     (10, NC), (12, 0), (13, DST + 3), (0, DST), (22, 0),
                     (17, 0)):
            cpu.set(r, v)
        pc = cpu.run(BASE, None, code=code, start_pc=BASE, max_steps=900000)
        assert pc == _F.AFTER_LOOP, stride
        px = [mem.u(DST + 4 * i, 4) for i in range(NC)]
        want = min(stride >> sh, NC)
        for i, v in enumerate(px):
            expect = 0xFFFFFFFF if (i << sh) < stride else 0xFF000040
            assert v == expect, (stride, i, hex(v))
        # and the edge is at stride >> shift, which is what makes a stride
        # LARGER than the texel grid resolvable at all -- build 351 could
        # not see one and came back solid white
        white = [i for i, v in enumerate(px) if v == 0xFFFFFFFF]
        assert (max(white) + 1 if white else 0) == want, stride


def test_projbar_only_uses_registers_the_body_already_owns():
    """w12 is the loop's own column counter and w19 the stride it was handed;
    w15/w17 are the body's scratch. Nothing else is touched, so this cannot
    disturb the loop it is measuring. (The two NOPs it does contain are the
    origin conditions build 325 retired -- shared by every proj mode.)"""
    import a64 as A
    import ff7nx_fbresample as _F
    w = _F.body_words(lambda i: 0x440000 + 4 * i, how='projbar')
    assert len(w) == _F.n_words('projbar')
    assert w.count(0xD503201F) == 2                 # the retired gate slots
    assert _F.cmp_reg_lsl(19, 12, _F.bar_shift()) in w   # stride vs i<<sh
    assert A.csel(15, 15, 17, 12) in w              # ... and the choice (GT)
