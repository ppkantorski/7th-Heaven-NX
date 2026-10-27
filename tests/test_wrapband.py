"""BUILD 748: KOTR curtain (BG_2) V-wrap band.

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
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import numpy as np
import tex
import ff7nx_wrapband as W

VAN = os.path.join(ROOT, 'cache', '_vanilla', 'magic.lgp', 'bg_2.tex')


def _rgb(payload):
    t = tex.parse(payload)
    pal = np.frombuffer(bytes(t['palette']), np.uint8).reshape(-1, 4)[:, :3]
    idx = np.frombuffer(bytes(t['pixels']), np.uint8).reshape(
        t['height'], t['width'])
    return pal[idx].astype(int)


def _check(d):
    o = W.repair(d)
    assert o is not None and len(o) == len(d)
    hdr = 0xEC + tex.parse(d)['palette_size'] * 4
    assert o[:hdr] == d[:hdr]                    # header + palette exact
    assert W.repair(o) == o                      # idempotent
    a, b = _rgb(d), _rgb(o)
    h = a.shape[0]
    r0 = next(r for r in range(h) if (a[r] != b[r]).any())
    assert r0 >= h * W.FROM_NUM // W.FROM_DEN
    assert (a[:r0] == b[:r0]).all()              # art above the fade intact
    assert (b[r0:] == b[r0 - 1]).all()           # the last full row repeats
    lum = b.mean(2).mean(1)
    assert lum[r0:].min() >= 0.96 * lum[r0 - 1] - 1e-9   # no dark band left
    return a, b, r0


def test_vanilla_band_removed():
    d = open(VAN, 'rb').read()
    a, b, r0 = _check(d)
    assert (a[-3:].sum(2) == 0).all()            # stock had 3 black rows
    assert r0 == 253                             # and only those change


def test_marked_upscale_and_synthetic():
    d = open(VAN, 'rb').read()
    t = tex.parse(d)
    # 3x nearest upscale stands in for the SYW canvas; add SYW's fade.
    idx = np.frombuffer(bytes(t['pixels']), np.uint8).reshape(256, 128)
    big = np.repeat(np.repeat(idx, 3, 0), 3, 1)
    import struct
    hdr = bytearray(d[:0xEC])
    struct.pack_into('<I', hdr, tex.O_WIDTH, 384)
    struct.pack_into('<I', hdr, tex.O_HEIGHT, 768)
    struct.pack_into('<I', hdr, tex.O_PITCH, 384)
    big = big.copy()
    pal = np.frombuffer(bytes(t['palette']), np.uint8).reshape(-1, 4)
    lum = pal[:, :3].astype(int).sum(1)
    order = np.argsort(lum)                      # darkest .. brightest
    rank = np.empty_like(order); rank[order] = np.arange(len(order))
    for r in range(747, 768):                    # fade the bottom like SYW
        k = max(0, 1 - (r - 746) / 16)
        big[r] = order[(rank[big[r]] * k).astype(int)]
    pay = bytes(hdr) + bytes(t['palette']) + big.tobytes()
    pay = tex.mark_logical_scale(pay, 3)
    o = W.repair(pay)
    assert tex.logical_scale(o) == (3, 3)
    _a, _b, r0 = _check(pay)
    assert 744 <= r0 <= 748


def test_refuses_non_paletted():
    assert W.repair(b'not a tex') is None


class _A:
    def __init__(self, entries):
        self.entries = entries


def test_apply_routes_to_shipping_payload(monkeypatch=None):
    d = open(VAN, 'rb').read()
    other = {'name': 'cylinder.p', 'payload': b'x'}
    arch = _A([other, {'name': 'BG_2.TEX', 'payload': d}])
    sp, pl = {}, {}
    assert W.apply(arch, sp, pl) == 1 and set(sp) == {1}
    sp2 = {1: d}
    assert W.apply(arch, sp2, {}) == 1 and sp2[1] != d
    pl3 = {'bg_2.tex': d}
    sp3 = {}
    assert W.apply(arch, sp3, pl3) == 1 and not sp3 and pl3['bg_2.tex'] != d
    os.environ[W.ENV_OFF] = '1'
    try:
        assert W.apply(arch, {}, {}) == 0
    finally:
        del os.environ[W.ENV_OFF]


if __name__ == '__main__':
    for k, f in list(globals().items()):
        if k.startswith('test_'):
            f(); print('ok', k)


def _grey_of(payload):
    t = tex.parse(payload)
    pal = np.frombuffer(bytes(t['palette']), np.uint8).reshape(-1, 4)
    idx = np.frombuffer(bytes(t['pixels']), np.uint8).reshape(
        t['height'], t['width'])
    return pal[idx, 0].astype(float), idx


def test_779_curtain_tiles_and_lifts_the_blacks():
    import struct
    if not os.path.exists(VAN):
        import pytest
        pytest.skip('no vanilla bg_2.tex in cache')
    d = open(VAN, 'rb').read()
    t = tex.parse(d)
    # a high-contrast 3x stand-in for SYW: blacks crushed, 256 greys
    g, _ = _grey_of(d)
    big = np.repeat(np.repeat(g, 3, 0), 3, 1)
    big = np.clip((big - 30) * 2.2, 0, 255)
    hdr = bytearray(d[:0xEC])
    struct.pack_into('<I', hdr, tex.O_WIDTH, 384)
    struct.pack_into('<I', hdr, tex.O_HEIGHT, 768)
    struct.pack_into('<I', hdr, tex.O_PITCH, 384)
    struct.pack_into('<I', hdr, tex.O_PAL_SIZE, 256)
    struct.pack_into('<I', hdr, tex.O_COLORS_PER_PAL, 256)
    struct.pack_into('<I', hdr, tex.O_COLORS_PER_PAL2, 256)
    pal = np.zeros((256, 4), np.uint8)
    pal[1:, :3] = np.arange(1, 256)[:, None]
    pal[1:, 3] = 254
    idx = np.clip(np.rint(big), 1, 255).astype(np.uint8)
    idx[big < 0.5] = 0                              # SYW's black is the key
    pay = tex.mark_logical_scale(bytes(hdr) + pal.tobytes() + idx.tobytes(), 3)
    assert tex.parse(pay) is not None
    o = W.curtain(pay, d)
    assert o is not None and len(o) == len(pay)
    assert o[:0xEC] == pay[:0xEC] and tex.logical_scale(o) == (3, 3)
    a, ia = _grey_of(o)
    s, _ = _grey_of(pay)
    assert (ia != 0).all()                          # no keyed holes left
    inner_u = np.abs(np.diff(a, axis=1)).mean()
    assert np.abs(a[:, 0] - a[:, -1]).mean() <= inner_u   # U is periodic
    assert np.abs(a[0] - a[-1]).mean() <= np.abs(np.diff(a, axis=0)).mean()
    assert (s < 1).mean() > 0.15                    # the stand-in had holes
    assert np.percentile(a, 10) >= np.percentile(g, 10) - 1   # lifted
    bu, bv = int(round(384 * W.TILE_U)), int(round(768 * W.TILE_V))
    keep = (slice(bv, 768 - bv), slice(bu, 384 - bu))
    # outside the tiling zones nothing is darker, highlights stay SYW's
    assert (a[keep] >= s[keep] - 0.5).all()
    hi = s[keep] > 120
    assert np.abs(a[keep][hi] - s[keep][hi]).max() <= 0.5


def test_779_curtain_refuses_colour():
    d = open(VAN, 'rb').read() if os.path.exists(VAN) else None
    if d is None:
        import pytest
        pytest.skip('no vanilla bg_2.tex in cache')
    t = tex.parse(d)
    pal = bytearray(t['palette'])
    pal[4 * 3 + 0] ^= 0x40                          # entry 3 no longer grey
    hdr = len(d) - len(t['pixels']) - len(t['palette'])
    bad = d[:hdr] + bytes(pal) + bytes(t['pixels'])
    idx = np.frombuffer(bytes(t['pixels']), np.uint8)
    if (idx == 3).any():
        assert W.curtain(bad) is None


def test_780_aurora_tiles_and_keeps_the_art():
    van = os.path.join(ROOT, 'cache', '_vanilla', 'magic.lgp', 'tube_1.tex')
    if not os.path.exists(van):
        import pytest
        pytest.skip('no vanilla tube_1.tex in cache')
    d = open(van, 'rb').read()
    o = W.tile_colour(d)
    assert o is not None and len(o) == len(d)
    t = tex.parse(d)
    hdr = len(d) - len(t['pixels'])
    assert o[:hdr] == d[:hdr]                       # header + palette exact
    a, b = _rgb(d).mean(2), _rgb(o).mean(2)
    h, w = a.shape
    bu, bv = int(round(w * W.TILE_U)), int(round(h * W.TILE_V))
    assert (a[bv:h - bv, bu:w - bu] == b[bv:h - bv, bu:w - bu]).all()
    assert np.abs(b[:, 0] - b[:, -1]).mean() < np.abs(a[:, 0] - a[:, -1]).mean() / 4
    assert np.abs(b[0] - b[-1]).mean() < np.abs(a[0] - a[-1]).mean() / 4
