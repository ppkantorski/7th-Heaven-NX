"""BUILD 705: the field countdown timer's digits without the packed sheet's
grey hatching (ff7nx_esui.timer_digits).

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
import sys

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import ff7nx_esui as E                                        # noqa: E402


def test_705_hatching_in_the_timer_cells_goes_black_digits_stay():
    img = np.zeros((512, 512, 4), np.uint8)
    img[..., 3] = 200
    img[330, 200] = (90, 90, 90, 200)        # hatching inside a cell
    img[331, 200] = (230, 40, 230, 255)      # a digit stroke
    img[100, 200] = (90, 90, 90, 200)        # grey outside the cells
    img[330, 100] = (90, 90, 90, 200)        # left of the colon cells
    out = E.timer_digits(img)
    assert tuple(out[330, 200]) == (0, 0, 0, 200)
    assert tuple(out[331, 200]) == (230, 40, 230, 255)
    assert tuple(out[100, 200]) == (90, 90, 90, 200)
    assert tuple(out[330, 100]) == (90, 90, 90, 200)
    assert tuple(img[330, 200]) == (90, 90, 90, 200)          # input untouched


def test_705_cells_follow_the_sheet_size():
    for s in (256, 1024):
        img = np.full((s, s, 4), 90, np.uint8)
        out = E.timer_digits(img)
        k = s // 256
        assert out[160 * k, 64 * k, 0] == 0 and out[250 * k - 1, 256 * k - 1, 0] == 0
        assert out[160 * k - 1, 64 * k, 0] == 90 and out[160 * k, 64 * k - 1, 0] == 90


def _vanilla_tex(w=64, h=64, n_pal=4, seed=1):
    import struct
    import tex
    rng = np.random.default_rng(seed)
    hdr = bytearray(tex.HEADER_LEN)
    for off, v in ((tex.O_VERSION, 1), (tex.O_NUM_PALETTES, n_pal),
                   (tex.O_COLORS_PER_PAL, 16), (tex.O_BIT_DEPTH, 8),
                   (tex.O_WIDTH, w), (tex.O_HEIGHT, h), (tex.O_PITCH, w),
                   (tex.O_PAL_FLAG, 1), (tex.O_BITS_PER_INDEX, 8),
                   (tex.O_PAL_SIZE, n_pal * 16), (tex.O_COLORS_PER_PAL2, 16),
                   (tex.O_BITS_PER_PIXEL, 8), (tex.O_BYTES_PER_PIXEL, 1)):
        struct.pack_into('<I', hdr, off, v)
    pal = rng.integers(0, 256, (n_pal, 16, 4), dtype=np.uint8)
    pal[:, :, 3] = 255
    px = rng.integers(0, 16, (h, w), dtype=np.uint8)
    data = bytes(hdr) + pal.tobytes() + px.tobytes()
    assert tex.parse(data) is not None
    return data


def test_706_solo_cells_cost_the_other_palettes_nothing():
    import ff7nx_ddstex as DT
    van = _vanilla_tex()
    imgs = {p: DT.vanilla_palette_rgba(van, p, 64, 64) for p in range(4)}
    hd = dict(imgs)
    art = imgs[2].copy()
    yy, xx = np.mgrid[0:32, 0:32]
    art[16:48, 16:48, 0] = xx * 8                 # a gradient: 32 x 32 new colours
    art[16:48, 16:48, 1] = 0
    art[16:48, 16:48, 2] = yy * 8
    art[16:48, 16:48, 3] = 255
    hd[2] = art
    cells = (16, 16, 48, 48)
    base, _ = E.joint_tex(van, imgs)
    with_hd, _ = E.joint_tex(van, hd, solo=(2, cells, 16))
    assert E.sheet_drift(base, with_hd, cells) == 0.0
    import tex
    t = tex.parse(with_hd)
    pal = np.frombuffer(t['palette'], np.uint8).reshape(4, 256, 4)
    idx = np.frombuffer(t['pixels'], np.uint8).reshape(64, 64)
    got = pal[2][idx][16:48, 16:48][:, :, [2, 1, 0]].astype(int)
    assert np.abs(got - art[16:48, 16:48, :3].astype(int)).mean() < 12


def test_706_timer_hd_takes_only_the_cells():
    van = np.full((512, 512, 4), 7, np.uint8)
    art = np.zeros((512, 512, 4), np.uint8)
    art[...] = (230, 40, 230, 200)           # magenta: kept as is
    out = E.timer_hd(art, van)
    assert (out[320:500, 128:512] == art[320:500, 128:512]).all()
    assert (out[:320] == 7).all() and (out[500:] == 7).all() and (out[:, :128] == 7).all()


def test_715_chrome_goes_black_strokes_and_glow_stay():
    van = np.full((1024, 1024, 4), 7, np.uint8)
    art = np.zeros((1024, 1024, 4), np.uint8)
    art[...] = (120, 120, 120, 255)          # neutral chrome everywhere
    art[700, 400] = (253, 183, 255, 255)     # white-hot stroke core
    art[701, 400] = (230, 40, 230, 255)      # stroke
    art[702, 400] = (87, 75, 87, 255)        # faint glow over chrome
    art[813, 400] = (230, 40, 230, 255)      # strip between the rows
    out = E.timer_hd(art, van)
    assert tuple(out[650, 600]) == (0, 0, 0, 255)            # chrome -> black
    assert tuple(out[700, 400]) == (253, 183, 255, 255)
    assert tuple(out[701, 400]) == (230, 40, 230, 255)
    assert 0 < out[702, 400, 0] < 87                         # glow fades
    assert tuple(out[812, 600]) == (120, 120, 120, 255)      # strip untouched
    assert (out[:640] == 7).all()


def test_715_env_keeps_the_chrome():
    os.environ[E.TIMER_KEEP_CHROME_ENV] = '1'
    try:
        img = np.full((256, 256, 4), 120, np.uint8)
        assert (E.timer_unlit_off(img) == 120).all()
    finally:
        del os.environ[E.TIMER_KEEP_CHROME_ENV]
