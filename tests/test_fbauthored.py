#!/usr/bin/env python3
"""tests/test_fbauthored.py -- k scales the source, never the texture.

BUILD 356. `fb_tex.w` IS the created texture's pixel width (+0x10D6E6C picks
it over tex_format.width for version-100 headers), and Kujata's disc
addresses that texture with UVs hard-coded to absolute texels 1..255. So a
k-scaled `fb_tex.w` does not give the field more pixels -- it shrinks the
disc's reach to the top-left 256x256 corner of a 1024x1024 texture, which is
1/16 of the capture blown up over the whole field.

The property these tests exist for: **at k = 1 the image must not move by one
byte**, and at every legal k the three words must agree with each other about
what k is.

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

import ff7nx_fbauthored as AU                                  # noqa: E402


def _find_dump():
    here = os.path.join(_HERE, 'dump', 'exefs', 'main')
    if os.path.exists(here):
        return here
    sib = os.path.join(os.path.dirname(os.path.dirname(_HERE)),
                       '7th_heaven_nx', 'dump', 'exefs', 'main')
    return sib if os.path.exists(sib) else here


DUMP = _find_dump()


@pytest.fixture(autouse=True)
def _pin_k1(monkeypatch):
    """Other modules in this suite move the surface scale; pin it."""
    import ff7nx_fbsurf
    monkeypatch.setenv(ff7nx_fbsurf.SURF_ENV, '1')
    monkeypatch.setenv(AU.AUTHORED_ENV, '1')
    importlib.reload(ff7nx_fbsurf)
    importlib.reload(AU)
    yield
    importlib.reload(AU)


def test_it_is_off_by_default_and_the_shipped_surface_is_still_k4(monkeypatch):
    """BUILD 358, and it is a retraction.

    Hardware, from the operator, comparing k = 1 against k = 4: "alignment +
    scale, it's exactly the same. just super low res." So k does NOT move the
    picture -- it is a pure density dial and it always worked. The whole of
    356/357 rested on it moving the picture, and it does not.

    These words must therefore stay off, and the shipped surface stays 4.
    """
    monkeypatch.delenv(AU.AUTHORED_ENV, raising=False)
    importlib.reload(AU)
    assert AU.enabled() is False
    assert AU.cancels_surface_scale() is False
    assert AU.column_numerator(640) == 640
    import ff7nx_fbsurf
    monkeypatch.delenv(ff7nx_fbsurf.SURF_ENV, raising=False)
    importlib.reload(ff7nx_fbsurf)
    assert ff7nx_fbsurf.scale() == 4


def test_the_gate_follows_this_module_and_never_assumes(monkeypatch):
    """A gate carrying surf_shift() while fb_tex.w no longer does matches
    NOTHING, which silently disengages every resample body."""
    import ff7nx_fbsurf
    import ff7nx_fbresample as R
    monkeypatch.setenv(ff7nx_fbsurf.SURF_ENV, '4')
    for on, want in (('1', 0), ('0', 2)):
        monkeypatch.setenv(AU.AUTHORED_ENV, on)
        importlib.reload(ff7nx_fbsurf)
        importlib.reload(AU)
        importlib.reload(R)
        assert R.gate_shift() == want, on
        assert R.gate_shift_h() == want, on


def test_k1_is_the_stock_image_verbatim():
    assert AU.words_for(1) == {AU.ROW_SITE: AU.ROW_STOCK,
                               AU.HDIV_SITE: AU.HDIV_STOCK,
                               AU.WDIV_SITE: AU.WDIV_STOCK}


def test_every_legal_k_shifts_all_three_by_log2_k():
    """Decoded from the encodings, not asserted from memory."""
    def lsr_shift(w):
        assert (w & 0xFFC0FC00) == 0xD340FC00, hex(w)       # LSR Xd,Xn,#sh
        return (w >> 16) & 0x3F

    def add_shift(w):
        assert (w & 0xFFE00000) == 0x8B000000, hex(w)       # ADD Xd,Xn,Xm,LSL
        assert ((w >> 16) & 31, (w >> 5) & 31, w & 31) == (9, 11, 11), hex(w)
        return (w >> 10) & 0x3F

    for k, s in AU.SHIFT.items():
        w = AU.words_for(k)
        assert lsr_shift(w[AU.WDIV_SITE]) == AU.WDIV_BASE + s, k
        assert lsr_shift(w[AU.HDIV_SITE]) == AU.HDIV_BASE + s, k
        assert add_shift(w[AU.ROW_SITE]) == s, k
        assert (1 << s) == k


def test_the_two_divides_exactly_cancel_the_scaled_numerator():
    """This is the arithmetic the fix rests on: fbsurf multiplies by 640k and
    480k, so dividing by 640k and 480k must return the caller's own w and h
    for every value they can take. Reciprocal magic included."""
    for k in AU.SHIFT:
        for n in list(range(0, 1024)) + [1279, 1280, 2559, 2560]:
            # +0x10DBC7C/80  add w9,w21,w21,lsl #2 ; lsl w9,w9,#(7+log2 k)
            w9 = (n * 5 << (7 + AU.SHIFT[k])) & 0xFFFFFFFF
            assert w9 == n * 640 * k, (k, n)                # no 32-bit wrap
            # +0x10DBC84/88  umull x9,w9,w12 ; lsr x9,x9,#(0x29+log2 k)
            got_w = (w9 * 0xCCCCCCCD) >> (AU.WDIV_BASE + AU.SHIFT[k])
            assert got_w == n, (k, n, got_w)
            # +0x10DBC64/68/6C  mul w10,w19,w13 ; umull ; lsr #(0x28+log2 k)
            w10 = (n * 480 * k) & 0xFFFFFFFF
            assert w10 == n * 480 * k, (k, n)
            got_h = (w10 * 0x88888889) >> (AU.HDIV_BASE + AU.SHIFT[k])
            assert got_h == n, (k, n, got_h)


def test_the_column_step_grows_with_k_and_is_640_at_k1(monkeypatch):
    import ff7nx_fbsurf
    for k in (1, 2, 4, 8):
        monkeypatch.setenv(ff7nx_fbsurf.SURF_ENV, str(k))
        importlib.reload(ff7nx_fbsurf)
        importlib.reload(AU)
        assert AU.column_numerator(640) == 640 * k


def test_the_resample_reproduces_the_stock_constants_at_k1(monkeypatch):
    """The whole point of `640 * k`: at k = 1 the shipped magic/shift pair
    must be bit-identical to what it always was."""
    import ff7nx_fbsurf
    monkeypatch.setenv(ff7nx_fbsurf.SURF_ENV, '1')
    importlib.reload(ff7nx_fbsurf)
    importlib.reload(AU)
    import ff7nx_fbcapture as FB
    import ff7nx_fbresample as R
    importlib.reload(R)
    assert R._reciprocal() == FB.reciprocal()


def test_the_three_words_can_never_disagree_about_k():
    """A half-applied image is MIXED, not silently accepted."""
    img = bytearray(0x10DBD00)
    for va, w in AU.words_for(4).items():
        struct.pack_into('<I', img, va, w)
    assert AU.read_state(img) == 'source-scaled k=4'
    struct.pack_into('<I', img, AU.ROW_SITE, AU.words_for(2)[AU.ROW_SITE])
    assert AU.read_state(img).startswith('MIXED')


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_against_the_real_image():
    import nxmap
    m = nxmap.Main(DUMP)
    assert AU.verify(m.img) == []
    assert AU.read_state(m.img) == 'source-scaled k=1'


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_the_anchors_and_the_size_choice_are_really_in_the_image():
    """Including +0x10D6E6C, the csel that makes fb_tex.w the texture's real
    width -- the fact this whole module rests on."""
    import nxmap
    m = nxmap.Main(DUMP)
    for table in (AU.ANCHORS, AU.SIZE_CHOICE):
        for va, want in sorted(table.items()):
            got = struct.unpack_from('<I', m.img, va)[0]
            assert got == want, ('+0x%X' % va, hex(got), hex(want))


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_plan_is_nothing_at_k1_and_three_words_at_k4(monkeypatch):
    import nxmap
    import ff7nx_fbsurf
    m = nxmap.Main(DUMP)
    patches, _, problems = AU.plan(m)
    assert problems == [] and patches == []
    monkeypatch.setenv(ff7nx_fbsurf.SURF_ENV, '4')
    importlib.reload(ff7nx_fbsurf)
    importlib.reload(AU)
    patches, notes, problems = AU.plan(m)
    assert problems == [] and len(patches) == 3
    got = {int(p['va'], 16): bytes.fromhex(p['set']) for p in patches}
    for va, w in AU.words_for(4).items():
        assert got[va] == struct.pack('<I', w)
    assert any('854' in n for n in notes)


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_it_refuses_when_an_anchor_moves():
    import nxmap
    m = nxmap.Main(DUMP)
    img = bytearray(m.img)
    va = sorted(AU.ANCHORS)[2]
    struct.pack_into('<I', img, va, 0xDEADBEEF)
    assert AU.verify(img)

    class M:
        pass
    fake = M()
    fake.img = img
    patches, _, problems = AU.plan(fake)
    assert patches == [] and problems


@pytest.mark.skipif(not os.path.exists(DUMP), reason='needs the game dump')
def test_fbsurf_no_longer_anchors_the_two_shifts_it_now_moves():
    """If both modules claimed these words, one would always refuse."""
    import ff7nx_fbsurf
    assert AU.WDIV_SITE not in ff7nx_fbsurf.ANCHORS
    assert AU.HDIV_SITE not in ff7nx_fbsurf.ANCHORS
    assert AU.WDIV_SITE not in ff7nx_fbsurf.words_for(4)
    assert AU.HDIV_SITE not in ff7nx_fbsurf.words_for(4)
