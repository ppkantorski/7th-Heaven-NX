"""BUILD 551: the submarine's PlayStation grid (hidden on PC behind F2).

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
import os, shutil, struct, sys, tempfile
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import ff7nx_subgrid as G  # noqa: E402
import nso_patcher  # noqa: E402

STOCK = os.path.join(HERE, 'dump', 'exefs', 'main')
Q = lambda *a: None


def _text(path):
    nso = nso_patcher.read_nso(nso_patcher.Path(path))
    return next(bytes(s.data) for s in nso.segments if s.name == '.text')


def test_stock():
    t = _text(STOCK)
    assert G.check_text(t) == [] and G.state_text(t) == 'stock'
    assert G.text_colour(t) == G.STOCK_ARGB


def test_encodings_match_stock_words():
    assert G.movz(25, 0x7F7F) == 0x528FEFF9
    assert G.movk16(25, 0xFF7F) == 0x72BFEFF9


def test_round_trip_and_colour():
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, 'main')
        shutil.copyfile(STOCK, p)
        assert G.apply_nso(p, revert=False, argb=0xFFD03030, log=Q,
                           zbias=512) == 0
        t = _text(p)
        assert G.state_text(t) == 'grid FFD03030'
        stock = _text(STOCK)
        diff = {i for i in range(len(t)) if t[i] != stock[i]}
        allowed = set(range(G.GATE[0], G.GATE[0] + 4))
        for lo, hi in G.COLOUR_SITES:
            allowed |= set(range(lo, lo + 4)) | set(range(hi, hi + 4))
        for va, _s in G.ZSITES:
            allowed |= set(range(va, va + 4))
        for va, _s, _n in G.FILTER_WORDS:
            allowed |= set(range(va, va + 4))
        assert diff and diff <= allowed
        for va, s_, _n in G.FILTER_WORDS:
            assert struct.unpack_from('<I', t, va)[0] == s_   # default off
        assert struct.unpack_from('<I', t, G.GATE[0])[0] == 0xD503201F
        for va, _s in G.ZSITES:
            assert struct.unpack_from('<I', t, va)[0] == 0x510802F7
        # recolour in place, then idempotent
        assert G.apply_nso(p, revert=False, argb=0xFF7F7F7F, log=Q,
                           zbias=512) == 0
        assert G.state_text(_text(p)) == 'grid FF7F7F7F'
        t2 = _text(p)
        assert G.apply_nso(p, revert=False, argb=0xFF7F7F7F, log=Q,
                           zbias=512) == 0
        assert _text(p) == t2
        assert G.apply_nso(p, revert=True, log=Q) == 0
        assert _text(p) == stock
    finally:
        shutil.rmtree(d)


def test_env():
    assert G.enabled({}) and not G.enabled({G.ENV: '0'})
    assert G.colour({}) == 0xFFD03030
    assert G.colour({G.RGB_ENV: 'stock'}) == 0xFF7F7F7F
    assert G.colour({G.RGB_ENV: '#40ff40'}) == 0xFF40FF40
    assert G.colour({G.RGB_ENV: 'junk'}) == 0xFFD03030


def test_zero_bias_keeps_z_words():
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, 'main')
        shutil.copyfile(STOCK, p)
        assert G.apply_nso(p, revert=False, zbias=0, log=Q) == 0
        t = _text(p)
        for va, stock in G.ZSITES:
            assert struct.unpack_from('<I', t, va)[0] == stock
    finally:
        shutil.rmtree(d)


def test_bias_env():
    assert G.bias({}) == 0
    assert G.bias({G.BIAS_ENV: '512'}) == 512
    assert G.bias({G.BIAS_ENV: '9999'}) == 0


def test_filter_modes_are_stock_plus_submarine():
    for mode in range(0, 29):
        stock = ((mode - 3) & 0xFFFFFFFF) < 2
        new = (0x418 >> mode) & 1
        assert new == (stock or mode == 10)


def test_filter_env():
    assert not G.filter_enabled({}) and G.filter_enabled({G.FILTER_ENV: '1'})
