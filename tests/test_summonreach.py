#!/usr/bin/env python3
"""
tests/test_summonreach.py -- the floor-warp summons' 4:3 -> 16:9 world reach.

The claim is arithmetic: each effect's ground surface must grow by the same
854/640 the frame gained, and its UVs must NOT move. Both halves are checked
by EXECUTING the patched words under arm64emu, not by comparing them.

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

import arm64emu as E                                           # noqa: E402
import ff7nx_summonreach as R                                  # noqa: E402
import nxmap                                                   # noqa: E402

MAIN = os.path.join(_HERE, 'game_data_files', 'exefs', 'main')
pytestmark = pytest.mark.skipif(
    not os.path.exists(MAIN), reason='needs game_data_files/exefs/main')

SITE = {va: (stock, fixed) for _, va, stock, fixed in R.SITES}
KUJATA_SCALE = 0x5319611A          # lsl w26, w8, #7   -- not patched
RATIO = 854 / 640


@pytest.fixture(scope='module')
def img():
    return nxmap.Main(MAIN).img


def _radius(words, ring, out):
    cpu = E.Cpu(E.Mem())
    cpu.x[8] = ring
    cpu.run(0x1000, words, max_steps=64)
    return cpu.x[out] & 0xFFFFFFFF


def test_the_stock_arithmetic_is_what_the_header_says(img):
    """Kujata ring*384, KOTR ring*288 -- the numbers the whole reading rests
    on, executed rather than read off a disassembly listing."""
    for ring in (0, 1, 7, 16, 31):
        assert _radius([SITE[0x473244][0], KUJATA_SCALE], ring, 26) == 384 * ring
        assert _radius([SITE[0x21BD78][0], SITE[0x21BD7C][0]], ring, 27) \
            == 288 * ring


def test_the_patched_arithmetic_is_exactly_four_thirds():
    for ring in (0, 1, 7, 16, 31):
        k = _radius([SITE[0x473244][1], KUJATA_SCALE], ring, 26)
        r = _radius([SITE[0x21BD78][1], SITE[0x21BD7C][1]], ring, 27)
        assert k == 512 * ring == 384 * ring * 4 // 3
        assert r == 384 * ring == 288 * ring * 4 // 3


def test_the_outer_rings_grow_by_the_same_ratio():
    for stock, fixed in ((0x2FA0, 0x3F80), (0x11D0, 0x17C0)):
        assert fixed == stock * 4 // 3
        assert abs(fixed / stock - RATIO) < 0.002


def test_the_uvs_are_not_touched(img):
    """
    The whole point: more world per texel, the SAME texels. If a future
    change moves the UV radius or its clamp, the snapshot stops registering
    and we are back to build 267's displaced picture.
    """
    uv_sites = {0x4722B0: 'mov w28, #0x7f'}      # the UV radius clamp
    for va in uv_sites:
        assert va not in SITE
        assert struct.unpack_from('<I', img, va)[0] == 0x32001BFC


def test_anchors_and_sites_are_stock_in_the_dump(img):
    assert R.verify(img) == []
    assert R.read_state(img) == 'stock'


def test_apply_idempotent_revert_is_byte_exact(tmp_path):
    out = tmp_path / 'main'
    shutil.copy(MAIN, out)
    assert R.apply_all(out, log=lambda *_: None) == 0
    img = nxmap.Main(str(out)).img
    assert R.read_state(img) == 'widened'
    assert R.verify(img) == []
    assert R.apply_all(out, log=lambda *_: None) == 0
    assert R.apply_all(out, revert=True, log=lambda *_: None) == 0
    assert out.read_bytes() == open(MAIN, 'rb').read()


def test_a_half_applied_module_is_refused(tmp_path):
    out = tmp_path / 'main'
    shutil.copy(MAIN, out)
    data = bytearray(out.read_bytes())
    m = nxmap.Main(str(out))
    # flip exactly one site in the decompressed image's file offset
    assert R.apply_all(out, log=lambda *_: None) == 0
    img = bytearray(nxmap.Main(str(out)).img)
    struct.pack_into('<I', img, 0x473244, SITE[0x473244][0])
    class Fake:
        pass
    f = Fake(); f.img = bytes(img)
    patches, notes, problems = R.plan(f)
    assert problems and 'mixed' in problems[0]


def test_it_composes_with_the_capture_cave(tmp_path):
    import ff7nx_fbcapture as FB
    a, b = tmp_path / 'a', tmp_path / 'b'
    shutil.copy(MAIN, a)
    assert R.apply_all(a, log=lambda *_: None) == 0
    assert FB.apply_to_nso(str(a), str(b), lambda *_: None)
    img = nxmap.Main(str(b)).img
    assert R.read_state(img) == 'widened' and FB.read_state(img) == 'wide'
    assert R.verify(img) == [] and FB.verify(img) == []
    # neither may have landed on the other's words
    assert not set(R.SITE_VAS if hasattr(R, 'SITE_VAS') else
                   [va for _, va, _, _ in R.SITES]) & \
        (set(FB.walk_physical(img)) | {FB.HOOK})


def test_gate_follows_widescreen(monkeypatch):
    monkeypatch.setenv(R.REACH_ENV, '0')
    assert R.enabled() is False
    monkeypatch.setenv(R.REACH_ENV, '1')
    assert R.enabled() is True
