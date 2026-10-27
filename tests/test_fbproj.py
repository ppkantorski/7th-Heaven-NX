#!/usr/bin/env python3
"""
tests/test_fbproj.py -- BUILD 327, the projective remap at the scale it ships.

tests/test_fbresample.py pins `SEVENTH_NX_FB_SURFACE=1` at import, because
everything in it was written against the stock 640x480 capture surface. The
projective body cannot be checked there: the two defects this build repairs
are only reachable at k > 1.

    the clamp was the STOCK surface     639 x 479, against a mapping that
                                        produces columns up to 2560
    the coefficients were a FIXED tuple fitted at k = 1, dial 100, and kept
                                        after all three moved

So this file pins k = 4 -- the configuration on the TV -- and checks the
whole chain by EXECUTION: the cave runs under arm64emu against a synthetic
capture surface in which every pixel carries its own coordinates, and the
destination texture is compared texel for texel against an independent
reference computed in Python from the same coefficients.

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
import sys

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import arm64emu as E                                           # noqa: E402
import ff7nx_fbresample as R                                   # noqa: E402

BASE = 0x100000

# `PROJ_WORDS` and every body in the module are generated at IMPORT time from
# the environment, and tests/test_fbresample.py pins the same variables to a
# different scale at ITS import. Setting them at module scope here would
# therefore decide what that file tests too, depending only on which one
# pytest happened to import first -- so the scale is pinned per test and put
# back afterwards, with a reload on both edges. `importlib.reload` refreshes
# the existing module object in place, so the other file's `R` is the same
# object and sees the restored state.
_PINNED = ('SEVENTH_NX_FB_SURFACE', 'SEVENTH_NX_FX_CAPSCALE',
           'SEVENTH_NX_FX_PROJ', 'SEVENTH_NX_FX_DISC', 'SEVENTH_NX_FB_PROBE')


@pytest.fixture(autouse=True)
def k4():
    was = {k: os.environ.get(k) for k in _PINNED}
    os.environ['SEVENTH_NX_FB_SURFACE'] = '4'
    os.environ['SEVENTH_NX_FX_CAPSCALE'] = '1'
    os.environ['SEVENTH_NX_FB_PROBE'] = 'proj'
    for k in ('SEVENTH_NX_FX_PROJ', 'SEVENTH_NX_FX_DISC'):
        os.environ.pop(k, None)
    importlib.reload(R)
    yield
    for k, v in was.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    importlib.reload(R)


def _code(how='proj'):
    a = [BASE + 4 * i for i in range(R.proj_words(how))]
    return a, dict(zip(a, R.proj_body(lambda i: a[i], how)))


def _trunc(n, d):
    q = abs(n) // abs(d)
    return -q if (n < 0) != (d < 0) else q


def _ref(i, j, coef=None):
    """The mapping, independently: truncating division, then the clamp the
    body is supposed to be using -- the REAL surface, not the stock one."""
    A00, A01, A02, A10, A11, A12, A20, A21, A22 = coef or R.proj_coef()
    den = A20 * i + A21 * j + A22
    sw, sh = R.surface()
    return (max(0, min(sw - 1, _trunc(A00 * i + A01 * j + A02, den))),
            max(0, min(sh - 1, _trunc(A10 * i + A11 * j + A12, den))))


# ---------------------------------------------------------------------------
# the numbers, before any code runs
# ---------------------------------------------------------------------------
def test_the_configuration_this_file_means_to_test():
    assert R.surface() == (2560, 1920)
    assert R.gate_shift() == 2
    # the probe was taken at the 94 dial and the tree now ships 100, so the
    # compensation must be live rather than 1.0 -- that factor is the whole
    # reason a re-dial does not invalidate the coefficients
    assert abs(R.disc_factor() - 100.0 / 94.0) < 0.001
    assert R.band()[1] > R.band()[0] and R.band()[3] > R.band()[2]


def test_every_coefficient_fits_int32_and_the_denominator_never_reaches_zero():
    """A zero denominator is a divide-by-zero on the console, and a
    coefficient over int32 silently changes sign inside smaddl."""
    c = R.proj_coef()
    assert len(c) == 9
    assert all(-2**31 <= x < 2**31 for x in c), c
    n = 256 << R.gate_shift()
    worst = min(c[6] * i + c[7] * j + c[8]
                for i in range(0, n, 7) for j in range(0, n, 7))
    assert worst > 0, worst


def test_the_integers_reproduce_the_measured_mapping_to_under_a_pixel():
    """The 9 ints are a quantisation of `proj_matrix()`. If the scale chosen
    by `proj_quantize` were too small the error would show up here, which is
    the only place it can be seen without a screenshot."""
    M = R.proj_matrix()
    c = R.proj_coef()
    n = 256 << R.gate_shift()
    sw, sh = R.surface()
    worst = 0.0
    for i in range(0, n, 8):
        for j in range(0, n, 8):
            d = M[2][0] * i + M[2][1] * j + M[2][2]
            fc = (M[0][0] * i + M[0][1] * j + M[0][2]) / d
            fr = (M[1][0] * i + M[1][1] * j + M[1][2]) / d
            if not (-4 <= fc <= sw + 4 and -4 <= fr <= sh + 4):
                continue                       # clamped anyway
            qd = c[6] * i + c[7] * j + c[8]
            qc = (c[0] * i + c[1] * j + c[2]) / qd
            qr = (c[3] * i + c[4] * j + c[5]) / qd
            worst = max(worst, abs(qc - fc), abs(qr - fr))
    assert worst < 1.0, worst


def test_the_mapping_aims_at_the_measured_band_not_the_whole_surface():
    """BUILD 366. The frame is not written across the whole surface -- it sits
    in a measured band (`band()`), and that is exactly what builds 327-330 got
    wrong. Every texel that lands anywhere must land in or beside that band,
    the centre of the disc must land INSIDE it, and a good share of the grid
    must too (the rest is the disc's off-screen skirt, which should not draw)."""
    n = 256 << R.gate_shift()
    x0, x1, y0, y1 = R.band()
    c, r = _ref(n // 2, n // 2)
    assert x0 < c < x1, (c, x0, x1)
    assert y0 < r < y1, (r, y0, y1)
    inside = sum(1 for i in range(0, n, 16) for j in range(0, n, 16)
                 if x0 <= _ref(i, j)[0] <= x1 and y0 <= _ref(i, j)[1] <= y1)
    total = len(range(0, n, 16)) ** 2
    assert inside > total // 3, (inside, total)
    # and the band is a small part of the surface, which is the finding
    sw, _ = R.surface()
    assert (x1 - x0) < 0.25 * sw, (x0, x1, sw)


def test_the_clamp_carries_the_real_surface_and_follows_fbsurf():
    """The regression that made build 280 unreadable: the body clamped every
    source column to 639 while the surface was 2560 wide."""
    words = R.proj_body(lambda i: i * 4, 'proj')
    import a64 as A
    assert A.movz(17, 2559) in words, 'the column clamp is not 2560-wide'
    assert A.movz(17, 1919) in words, 'the row clamp is not 1920-tall'
    assert A.movz(17, R.SURF_W - 1) not in words, 'stock clamp still present'


def test_the_shipping_write_is_the_punch_write_word_for_word():
    """`proj` differs from the build on the TV in the ADDRESSING and nothing
    else. Its write is lifted from punch_body, so an A/B changes one thing."""
    proj = R.proj_body(lambda i: i * 4, 'proj')
    punch = R.punch_body(lambda i: i * 4)
    write = punch[9:19]                       # ldurb .. stur32
    assert len(write) == 10
    for n in range(len(proj) - 10):
        if proj[n:n + 10] == write:
            break
    else:
        raise AssertionError('the punch write is not in the proj body')


def test_the_encoders_this_build_adds_round_trip_through_capstone():
    cs = pytest.importorskip('capstone')
    md = cs.Cs(cs.CS_ARCH_ARM64, cs.CS_MODE_LITTLE_ENDIAN)
    import struct
    for word, want in ((R.sxtw64(1, 1), 'sxtw x1, w1'),
                       (R.sxtw64(3, 3), 'sxtw x3, w3'),
                       (R.lsr64(15, 7, 4), 'lsr x15, x7, #4'),
                       (R.lsr64(15, 16, 4), 'lsr x15, x16, #4')):
        got = list(md.disasm(struct.pack('<I', word), 0))
        assert got, '%08X does not disassemble' % word
        text = '%s %s' % (got[0].mnemonic, got[0].op_str)
        assert text == want, (text, want)


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------
SURF, PITCH, DEST, HDR, SP = 0x8000000, 2560, 0x4000000, 0x700000, 0x60000


def _run(how, j, paint, columns=None):
    """Execute one destination row and return the four bytes of every texel.

    `paint(mem, col, row)` writes the synthetic source pixel. The borrowed
    registers are poisoned so a body that fails to restore one is caught.
    """
    a, code = _code(how)
    n = 256 << R.gate_shift()
    mem = E.Mem()
    for off, val in ((0x1C, n), (0x20, n), (0x3C, 256), (0x14, 320), (0x18, 0)):
        mem.setu(HDR + off, val, 4)
    for i in range(n):
        paint(mem, *_ref(i, j))
    cpu = E.Cpu(mem)
    cpu.sp = SP
    poison = {r: (r * 0x1111111111111111) & ((1 << 64) - 1) for r in range(1, 8)}
    for reg, val in ((20, HDR), (19, PITCH), (26, SURF), (8, j),
                     (10, 3), (22, 5), (12, 0), (0, DEST), (13, DEST + 3),
                     (11, 0xDEAD0000)):
        cpu.set(reg, val)
    for reg, val in poison.items():
        cpu.set(reg, val)
    pc = cpu.run(BASE, None, code=code, start_pc=BASE, max_steps=8000000)
    assert pc == R.AFTER_LOOP
    assert cpu.get(10) == n, 'the column count must come from fb_tex.w'
    assert cpu.get_sp() == SP, 'the cave must leave the stack balanced'
    for reg, val in poison.items():
        assert cpu.get(reg) == val, 'x%d was not restored' % reg
    return [tuple(mem.u(DEST + 4 * i + b, 1) for b in range(4))
            for i in range(n)]


def _paint_coords(mem, col, row):
    """A surface pixel that carries its own coordinates, in the byte order
    the stock loop uses: src[0..3] -> dest bytes 2, 1, 0, 3."""
    p = SURF + (PITCH * row + col) * 4
    mem.setu(p + 0, (col >> 4) & 0xFF, 1)      # -> dest byte 2
    mem.setu(p + 1, (row >> 4) & 0xFF, 1)      # -> dest byte 1
    mem.setu(p + 2, 0x7F, 1)                   # -> dest byte 0, never a void
    mem.setu(p + 3, 0xC0, 1)                   # alpha, copied verbatim


@pytest.mark.parametrize('j', [0, 1, 511, 512, 900, 1023])
def test_the_cave_samples_the_pixel_the_mapping_names(j):
    """Every texel of six rows, against the reference. A wrong register
    field, a wrong offset, a wrong clamp or a wrong coefficient lands as a
    wrong pixel here."""
    got = _run('proj', j, _paint_coords)
    n = 256 << R.gate_shift()
    for i in range(n):
        col, row = _ref(i, j)
        assert got[i] == (0x7F, (row >> 4) & 0xFF, (col >> 4) & 0xFF, 0xC0), \
            (j, i, got[i], col, row)


def test_a_texel_whose_source_carries_no_picture_is_not_drawn():
    """The punch half of the shipping write, on the remapped address: the
    sky is not in the capture surface at all, so the rings of the disc that
    fall beyond the horizon sample transparent black and must not be drawn."""
    mid = sum(R.band()[:2]) / 2.0              # inside the measured band

    def void(mem, col, row):
        p = SURF + (PITCH * row + col) * 4
        for b in range(4):
            mem.setu(p + b, 0, 1)
        if col > mid:                          # ... but not everywhere
            mem.setu(p + 0, 0x40, 1)
            mem.setu(p + 3, 0xFF, 1)
    got = _run('proj', 512, void)
    n = 256 << R.gate_shift()
    drawn = 0
    for i in range(n):
        col, row = _ref(i, 512)
        if col > mid:
            assert got[i] == (0, 0, 0x40, 0xFF), (i, got[i])
            drawn += 1
        else:
            assert got[i] == (0, 0, 0, 0), (i, got[i])
    assert drawn > 16, 'the test painted nothing'


def test_projsrc_reports_the_source_coordinates_in_16_pixel_units():
    """The verification probe. It must report the SAME numbers the reference
    computes, or a screenshot of it cannot be used to correct the fit."""
    got = _run('projsrc', 700, _paint_coords)
    n = 256 << R.gate_shift()
    for i in range(n):
        col, row = _ref(i, 700)
        assert got[i] == ((col >> 4) & 0xFF, (row >> 4) & 0xFF, 0x40, 0xFF), \
            (i, got[i], col, row)


def test_projtexel_still_reports_the_uv_grid_at_this_scale():
    """The probe that produced the fit, run through the projective body: the
    read-out has to stay the UV coordinate (B = u, G = v, R = 0x40) whatever
    the addressing does, because that is what the measurement assumed."""
    got = _run('projtexel', 512, _paint_coords)
    k = R.gate_shift()
    for i in range(0, 256 << k, 37):
        assert got[i] == (i >> k, 512 >> k, 0x40, 0xFF), (i, got[i])


def test_only_kujatas_shape_reaches_the_remap_at_this_scale():
    """fb_tex.w == 256 << 2, square, authored 256. The battle swirl, the
    1:1 captures and the page-scaled shapes must all execute the stock
    instruction instead."""
    for how in R.PROJ_MODES:
        a, code = _code(how)

        def route(fbw, fbh, texw):
            mem = E.Mem()
            for off, v in ((0x1C, fbw), (0x20, fbh), (0x3C, texw),
                           (0x14, 320), (0x18, 0)):
                mem.setu(HDR + off, v, 4)
            cpu = E.Cpu(mem)
            cpu.sp = SP
            for r, v in ((20, HDR), (19, PITCH), (26, SURF), (8, 0), (10, 0),
                         (22, 0), (12, 0), (0, DEST), (13, DEST + 3),
                         (11, 0xDEAD0000)):
                cpu.set(r, v)
            pc = cpu.run(BASE, None, code=code, start_pc=BASE,
                         max_steps=8000000)
            return 'remap' if pc == R.AFTER_LOOP else 'stock'

        assert route(1024, 1024, 256) == 'remap', how
        for shape in ((512, 512, 256),        # Kujata at k = 2
                      (256, 256, 256),        # a 1:1 capture
                      (320, 480, 160),        # the swirl's half
                      (1024, 1024, 512),      # square but not authored 256
                      (1024, 512, 256),       # not square
                      (2048, 2048, 256)):     # page-scaled
            assert route(*shape) == 'stock', (how, shape)


# ---------------------------------------------------------------------------
# the image
# ---------------------------------------------------------------------------
MAIN = os.path.join(_HERE, 'game_data_files', 'exefs', 'main')


@pytest.mark.skipif(not os.path.exists(MAIN),
                    reason='needs game_data_files/exefs/main')
@pytest.mark.parametrize('how', ['proj', 'projsrc'])
def test_apply_is_idempotent_and_revert_is_byte_identical(how, tmp_path):
    import shutil
    import nxmap
    work = tmp_path / 'main'
    shutil.copy(MAIN, work)
    before = work.read_bytes()
    os.environ['SEVENTH_NX_FB_PROBE'] = how
    importlib.reload(R)
    try:
        R.apply_all(str(work), log=lambda *a: None)
        after_one = work.read_bytes()
        assert after_one != before
        assert R.installed_mode(nxmap.Main(str(work)).img) == how
        R.apply_all(str(work), log=lambda *a: None)
        assert work.read_bytes() == after_one, 'apply is not idempotent'
        R.apply_all(str(work), revert=True, log=lambda *a: None)
        assert work.read_bytes() == before, 'revert is not byte-identical'
    finally:
        os.environ['SEVENTH_NX_FB_PROBE'] = 'proj'
        importlib.reload(R)
