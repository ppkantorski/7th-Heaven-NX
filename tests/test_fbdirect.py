#!/usr/bin/env python3
"""tests/test_fbdirect.py -- read the scene target instead of the capture.

BUILD 355. The capture surface is measurably 88% pure black while the blit
that fills it demonstrably covers the whole thing (dest rect, viewport,
scissor and quad UVs all read out of the binary). Whatever breaks it is
behind vtable thunks. So bypass it: the frame is already in the SCENE TARGET
at full display resolution, and the two surfaces' handles are installed in an
identical shape 0x10 apart, which makes the swap two words.

The safety property these tests exist for: **pixels and geometry must move
together.** Redirecting one without the other reads one surface's bytes with
another surface's stride, which is exactly the class of bug that produced
findings 348 and cost three builds.

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
import struct
import sys

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ff7nx_fbdirect as D                                     # noqa: E402

def _find_dump():
    """The operator's dump lives beside this working copy, not inside it."""
    here = os.path.join(_HERE, 'dump', 'exefs', 'main')
    if os.path.exists(here):
        return here
    sib = os.path.join(os.path.dirname(os.path.dirname(_HERE)),
                       '7th_heaven_nx', 'dump', 'exefs', 'main')
    return sib if os.path.exists(sib) else here


DUMP = _find_dump()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(D.DIRECT_ENV, raising=False)
    importlib.reload(D)
    yield
    importlib.reload(D)


def test_off_by_default():
    assert D.enabled() is False


def test_the_switch_spellings(monkeypatch):
    for yes in ('1', 'true', 'TRUE', 'yes', 'on', ' On '):
        monkeypatch.setenv(D.DIRECT_ENV, yes)
        assert D.enabled() is True, yes
    for no in ('0', 'false', 'no', 'off', '', 'banana'):
        monkeypatch.setenv(D.DIRECT_ENV, no)
        assert D.enabled() is False, no


def test_off_reproduces_the_stock_words_exactly():
    """The shipping build must not move by one byte."""
    assert D.words_for(False) == {D.PIX_SITE: D.PIX_STOCK,
                                  D.GEO_SITE: D.GEO_STOCK}


def test_the_two_words_are_the_offsets_we_think():
    """Decoded, not asserted from memory: both are `ldr Xt,[Xn,#imm]` and the
    ONLY thing that changes is the immediate, from the capture handle to the
    scene target handle 0x10 below it."""
    def decode(w):
        assert (w & 0xFFC00000) == 0xF9400000, hex(w)      # ldr Xt,[Xn,#imm]
        return ((w >> 10) & 0xFFF) * 8, (w >> 5) & 31, w & 31
    for stock, direct, drop in ((D.PIX_STOCK, D.PIX_DIRECT, 0x10),
                                (D.GEO_STOCK, D.GEO_DIRECT, 0x10)):
        o1, n1, t1 = decode(stock)
        o2, n2, t2 = decode(direct)
        assert (n1, t1) == (n2, t2)          # same registers
        assert o1 - o2 == drop               # only the handle moves
    assert decode(D.PIX_STOCK)[0] == 0x628   # capture object
    assert decode(D.PIX_DIRECT)[0] == 0x618  # scene target object
    assert decode(D.GEO_STOCK)[0] == 0x620   # capture id
    assert decode(D.GEO_DIRECT)[0] == 0x610  # scene target id


def test_pixels_and_geometry_can_never_disagree():
    """THE safety property. There is no combination of settings that moves
    one and not the other, and a half-patched image is reported as MIXED
    rather than silently accepted."""
    for direct in (False, True):
        w = D.words_for(direct)
        assert set(w) == {D.PIX_SITE, D.GEO_SITE}
        pix_direct = w[D.PIX_SITE] == D.PIX_DIRECT
        geo_direct = w[D.GEO_SITE] == D.GEO_DIRECT
        assert pix_direct == geo_direct == direct

    class Img(bytearray):
        pass
    img = Img(0x10D7200)
    for va, word in ((D.PIX_SITE, D.PIX_DIRECT), (D.GEO_SITE, D.GEO_STOCK)):
        struct.pack_into('<I', img, va, word)
    assert D.read_state(img).startswith('MIXED')


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_against_the_real_image():
    import nxmap
    m = nxmap.Main(DUMP)
    assert D.verify(m.img) == []
    assert D.read_state(m.img) == 'capture surface (stock)'


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_the_plan_is_two_patches_on_and_none_off(monkeypatch):
    import nxmap
    m = nxmap.Main(DUMP)
    patches, _, problems = D.plan(m)
    assert problems == [] and patches == []          # off: nothing to do
    monkeypatch.setenv(D.DIRECT_ENV, '1')
    importlib.reload(D)
    patches, notes, problems = D.plan(m)
    assert problems == []
    assert len(patches) == 2
    got = {int(p['va'], 16): bytes.fromhex(p['set']) for p in patches}
    assert got[D.PIX_SITE] == struct.pack('<I', D.PIX_DIRECT)
    assert got[D.GEO_SITE] == struct.pack('<I', D.GEO_DIRECT)
    for p in patches:                                 # and each expects stock
        va = int(p['va'], 16)
        assert bytes.fromhex(p['expect']) == struct.pack(
            '<I', D.PIX_STOCK if va == D.PIX_SITE else D.GEO_STOCK)


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_it_refuses_when_an_anchor_moves():
    import nxmap
    m = nxmap.Main(DUMP)
    img = bytearray(m.img)
    va = sorted(D.ANCHORS)[3]
    struct.pack_into('<I', img, va, 0xDEADBEEF)
    bad = D.verify(img)
    assert bad and ('+0x%X' % va) in bad[0]

    class M:
        pass
    fake = M()
    fake.img = img
    patches, notes, problems = D.plan(fake)
    assert patches == [] and problems


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_the_anchors_are_really_in_the_image():
    """Every anchor transcribed by hand is checked against the dump -- two of
    them were wrong when this module was written and this is what caught
    them."""
    import nxmap
    m = nxmap.Main(DUMP)
    for va, want in sorted(D.ANCHORS.items()):
        got = struct.unpack_from('<I', m.img, va)[0]
        assert got == want, ('+0x%X' % va, hex(got), hex(want))
