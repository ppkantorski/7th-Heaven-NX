#!/usr/bin/env python3
"""tests/test_fxsnap.py -- Kujata's snapshot window, two words in place.

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
import shutil
import struct
import sys

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import a64 as A                                                # noqa: E402
import arm64emu                                                # noqa: E402
import ff7nx_fxsnap as F                                       # noqa: E402
import nxmap                                                   # noqa: E402

MAIN = os.path.join(_HERE, 'game_data_files', 'exefs', 'main')
pytestmark = pytest.mark.skipif(
    not os.path.exists(MAIN), reason='needs game_data_files/exefs/main')


@pytest.fixture(scope='module')
def img():
    return nxmap.Main(MAIN).img


def test_the_stock_words_and_anchors_are_what_the_module_claims(img):
    assert struct.unpack_from('<I', img, F.HOOK_X)[0] == F.STOCK_X
    assert struct.unpack_from('<I', img, F.HOOK_Y)[0] == F.STOCK_Y
    assert F.verify(img) == []
    assert F.read_state(img) == 'x=stock y=stock'


def test_the_encoder_agrees_with_a64(img):
    """Positive control. a64's MOVZ is covered by its own 181-form suite, so
    agreeing with it is evidence, not a tautology."""
    for rd in (F.REG_X, F.REG_Y):
        for imm in (0, 1, 112, 192, 255, 640, 0x7FFF, 0xFFFF):
            assert F._movz(rd, imm) == A.movz(rd, imm)
    assert F._movz(F.REG_X, -107) == A.movz(F.REG_X, (-107) & 0xFFFF)


def test_the_words_really_load_those_values_on_a_cpu():
    """Execute them, do not compare them."""
    for rd, imm in ((F.REG_X, 192), (F.REG_Y, 112), (F.REG_X, -107)):
        base = 0x1000
        cpu = arm64emu.Cpu(arm64emu.Mem())
        cpu.set(rd, 0xDEADBEEF)
        cpu.run(base, [F._movz(rd, imm)])
        assert cpu.get(rd) == (imm & 0xFFFF)


def test_the_translated_pointer_the_load_used_is_dead_afterwards(img):
    """The whole reason two in-place words are safe: each `bl TRANSLATE`
    result in x0 is overwritten by the very next instruction, so turning the
    load into a move leaves nothing dangling."""
    for hook in (F.HOOK_X, F.HOOK_Y):
        after = struct.unpack_from('<I', img, hook + 4)[0]
        # add w0, w19, #imm  -- Rd == 0
        assert (after & 0x1F) == 0
        assert (after & 0x7F800000) == 0x11000000


def test_apply_is_idempotent_and_reverts_byte_exactly(tmp_path, monkeypatch):
    for x, y in (('192', '112'), ('0', '0'), ('-107', '224'), ('stock', '112')):
        monkeypatch.setenv(F.X_ENV, x)
        monkeypatch.setenv(F.Y_ENV, y)
        out = tmp_path / ('main_%s_%s' % (x, y))
        shutil.copy(MAIN, out)
        assert F.apply_all(out, log=lambda *_: None) == 0
        got = nxmap.Main(str(out)).img
        want_x = 'x=stock' if x == 'stock' else 'x=%d' % (int(x, 0) & 0xFFFF)
        assert F.read_state(got).split()[0] == want_x
        assert F.verify(got) == []
        assert F.apply_all(out, log=lambda *_: None) == 0       # idempotent
        assert F.apply_all(out, revert=True, log=lambda *_: None) == 0
        assert out.read_bytes() == open(MAIN, 'rb').read()


def test_both_fields_stock_turns_the_module_off(monkeypatch):
    monkeypatch.setenv(F.X_ENV, 'stock')
    monkeypatch.setenv(F.Y_ENV, 'stock')
    assert F.enabled() is False
    monkeypatch.setenv(F.Y_ENV, '112')
    assert F.enabled() is True


def test_nonsense_falls_back_rather_than_writing_garbage(monkeypatch):
    for bad in ('banana', '0x1FFFF', '-40000'):
        monkeypatch.setenv(F.X_ENV, bad)
        assert F.snap_x() == F.DEFAULT_X


def test_it_refuses_when_the_neighbourhood_moved(tmp_path):
    out = tmp_path / 'main_tampered'
    data = bytearray(open(MAIN, 'rb').read())
    m = nxmap.Main(MAIN)
    bad = bytearray(m.img)
    struct.pack_into('<I', bad, F.HOOK_X, 0xD503201F)           # nop
    assert F.verify(bad) != []
    out.write_bytes(data)


# --------------------------------------------------------------------------
# BUILD 359 -- the defaults are DERIVED, so the derivation is a test.
# --------------------------------------------------------------------------
def _inv3(a):
    (a0, a1, a2, a3, a4, a5, a6, a7, a8) = a
    c = [a4 * a8 - a5 * a7, a2 * a7 - a1 * a8, a1 * a5 - a2 * a4,
         a5 * a6 - a3 * a8, a0 * a8 - a2 * a6, a2 * a3 - a0 * a5,
         a3 * a7 - a4 * a6, a1 * a6 - a0 * a7, a0 * a4 - a1 * a3]
    d = a0 * c[0] + a1 * c[3] + a2 * c[6]
    return [x / d for x in c]


def _uv_centre_on_screen():
    """Where the disc DRAWS texel (128,128), from the hardware fit."""
    import ff7nx_fbresample as R
    M = _inv3(R.FIT_HUV)
    d = M[6] * 128.0 + M[7] * 128.0 + M[8]
    return ((M[0] * 128.0 + M[1] * 128.0 + M[2]) / d,
            (M[3] * 128.0 + M[4] * 128.0 + M[5]) / d)


def test_the_defaults_are_what_the_fit_demands_at_every_surface_scale(
        monkeypatch):
    """The window that puts texel (128,128) under the disc's own centre.

    Derived through fbcapture's corrected origin and the resample's own step,
    at k = 1 and k = 4 independently -- if the two disagreed, the answer would
    be an artefact of the surface scale rather than a property of the disc.
    """
    import importlib
    import ff7nx_fbresample as R
    import ff7nx_fbsurf
    import ff7nx_fxsnap as SNAP
    qx, qy = _uv_centre_on_screen()
    got = []
    for k in (1, 4):
        monkeypatch.setenv('SEVENTH_NX_FB_SURFACE', str(k))
        importlib.reload(ff7nx_fbsurf)
        importlib.reload(R)
        magic, shift = R._reciprocal()
        fbw = 256 * k
        half_col = ((128 * fbw // 256) * magic) >> shift
        half_row = 128 * fbw // 256
        fx, fy = qx * 640 * k - half_col, qy * 480 * k - half_row
        got.append((fx * 854.0 / (640 * k) - 107.0, fy / float(k)))
        assert 0 <= fx and fx + fbw <= 640 * k, ('clamp bites in x', k, fx)
        assert 0 <= fy and fy + fbw <= 480 * k, ('clamp bites in y', k, fy)
    monkeypatch.delenv('SEVENTH_NX_FB_SURFACE', raising=False)
    importlib.reload(ff7nx_fbsurf)                    # leave nothing behind
    importlib.reload(R)
    assert abs(got[0][0] - got[1][0]) < 2.0, got      # k-independent
    assert abs(got[0][1] - got[1][1]) < 2.0, got
    # BUILD 360: the defaults are back to None -- the window was the wrong
    # object -- but the derivation is kept, because it becomes the right
    # question again once ff7nx_fxorigin has put the disc where it belongs.
    assert abs(round(got[1][0]) - 346) <= 1, got
    assert abs(round(got[1][1]) - 57) <= 1, got
    assert SNAP.DEFAULT_X is None and SNAP.DEFAULT_Y is None


def test_the_stock_window_really_is_misregistered():
    """The premise. If this ever comes out near zero the fix is unnecessary
    and the model is wrong -- so it is asserted, not assumed."""
    import ff7nx_fbresample as R
    qx, qy = _uv_centre_on_screen()
    magic, shift = R._reciprocal()
    px = (0 + ((128 * magic) >> shift)) / 640.0          # stock rect.x = 0
    py = (0 + 128) / 480.0                               # stock rect.y = 0
    assert abs(px - qx) * 1280 > 200, (px, qx)
    assert abs(py - qy) * 720 > 40, (py, qy)


def test_both_env_vars_still_win_over_the_new_defaults(monkeypatch):
    import importlib
    import ff7nx_fxsnap as SNAP
    monkeypatch.setenv(SNAP.X_ENV, 'stock')
    monkeypatch.setenv(SNAP.Y_ENV, 'stock')
    importlib.reload(SNAP)
    assert SNAP.snap_x() is None and SNAP.snap_y() is None
    assert SNAP.enabled() is False
    monkeypatch.setenv(SNAP.X_ENV, '100')
    monkeypatch.setenv(SNAP.Y_ENV, '-8')
    importlib.reload(SNAP)
    assert (SNAP.snap_x(), SNAP.snap_y()) == (100, -8)
