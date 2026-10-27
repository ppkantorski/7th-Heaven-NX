"""BUILD 785: Ninostyle Shiva colour correction.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install()
import colorsys  # noqa: E402
import os  # noqa: E402
import struct  # noqa: E402

import numpy as np  # noqa: E402

import ff7nx_shivacolor as C  # noqa: E402


def _px(rgb):
    return C.recolor_rgb(np, np.array([[rgb]], np.float32) / 255)[0, 0]


def _hue(c):
    return colorsys.rgb_to_hsv(*c)[0] * 360


def test_classes_move_toward_stock():
    gold = _px((200, 150, 30))
    assert np.ptp(gold) < 0.08 and gold.mean() > 0.6          # silver
    assert 205 < _hue(_px((125, 121, 190))) < 235             # skin bluer
    assert 270 < _hue(_px((160, 70, 150))) < 300              # pink calmer
    hair = _px((20, 60, 75))
    assert 170 < _hue(hair) < 195 and max(hair) > 75 / 255    # lighter aqua
    for keep in ((0, 0, 0), (255, 255, 255), (128, 128, 128)):
        assert np.allclose(_px(keep), np.array(keep) / 255, atol=1e-3)


def _tex(w, h, pix):
    hdr = bytearray(0xEC)
    struct.pack_into('<I', hdr, 0, 1)
    struct.pack_into('<II', hdr, 0x3C, w, h)
    struct.pack_into('<I', hdr, 0x68, 3)
    struct.pack_into('<I', hdr, 0x7C, 0xFF0000)
    return bytes(hdr) + pix


def test_tex_roundtrip_bgr_and_size():
    import tex
    if tex.HEADER_LEN != 0xEC:
        return
    pix = bytes([30, 150, 200]) * 4            # BGR gold
    d = _tex(2, 2, pix)
    if not tex.parse(d) or tex.parse(d)['bytes_per_pixel'] != 3:
        return
    n = C.recolor_tex(d)
    assert len(n) == len(d) and n[:0xEC] == d[:0xEC]
    b, g, r = n[0xEC:0xEC + 3]
    assert max(b, g, r) - min(b, g, r) < 20


def test_switch(monkeypatch):
    monkeypatch.setenv(C.ENV, '0')
    assert not C.enabled()
    monkeypatch.delenv(C.ENV)
    assert C.enabled()


def test_build_pass_only_when_the_mod_owns_shiva(tmp_path, monkeypatch):
    import build as B
    monkeypatch.setattr(B, 'SHIVACOLOR_CACHE', str(tmp_path / 'c'))
    monkeypatch.setattr(C, 'recolor_tex', lambda d: b'X' + d)
    src = tmp_path / 't'
    src.write_bytes(b'abc')
    m = {'cyvadat.t00': (str(src), 'Nino')}
    assert B._recolor_shiva('magic.lgp', m, print) == m       # no skeleton
    m['cyvadat.d'] = ('x', 'Nino')
    out = B._recolor_shiva('magic.lgp', m, print)
    assert open(out['cyvadat.t00'][0], 'rb').read() == b'Xabc'
    assert B._recolor_shiva('battle.lgp', m, print) == m
    monkeypatch.setenv(C.ENV, '0')
    assert B._recolor_shiva('magic.lgp', m, print) == m


NINO = '/mnt/user-data/uploads/Claude/7th_heaven_nx/attic/shiva/nino_cyvadat.t00'


def test_the_eye_opening_is_untouched_brow_one_solid_shade_lashes_indigo(
        monkeypatch):
    import pytest
    # the recolour itself; the 802h palette step after it is tested apart
    monkeypatch.setenv(C.PALETTE_ENV, '0')
    import tex
    if not os.path.isfile(NINO):
        pytest.skip('no Nino texture')
    d = open(NINO, 'rb').read()
    a = np.frombuffer(tex.parse(d)['pixels'], np.uint8).reshape(1024, 1024, 3)
    b = np.frombuffer(tex.parse(C.recolor_tex(d))['pixels'],
                      np.uint8).reshape(1024, 1024, 3)
    full = C._opening(np, 1024, 1024) >= 0.999        # iris and whites
    assert full.sum() > 400
    assert np.abs(a[full].astype(int) - b[full]).max() <= 1
    assert np.abs(a.astype(int) - b).mean() > 5          # the rest moved
    import colorsys
    brow = b[860:890, 30:130].reshape(-1, 3)[:, ::-1] / 255   # BGR -> RGB
    hues = [colorsys.rgb_to_hsv(*c)[0] * 360 for c in brow
            if max(c) - min(c) > 0.1]
    aqua = sum(160 < x < 205 for x in hues)
    purple = sum(250 < x < 320 for x in hues)
    # BUILD 785f: the stock face's single solid brow/lid shade (hue ~232),
    # no green and no pink left in it
    blue = sum(222 < x < 245 for x in hues)
    # ... and the brow line itself green-aqua like her hair (785h)
    assert blue > 500 and aqua > 50 and purple < blue / 20, \
        (blue, aqua, purple)
    lash = b[834:846, 40:125].reshape(-1, 3)[:, ::-1] / 255
    lh = [colorsys.rgb_to_hsv(*c)[0] * 360 for c in lash
          if max(c) - min(c) > 0.15]
    assert sum(225 < x < 255 for x in lh) > sum(100 < x < 200 for x in lh)
