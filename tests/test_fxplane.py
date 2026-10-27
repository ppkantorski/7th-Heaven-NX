"""BUILD 682: ff7nx_fxplane -- Refuse's spinning square scale word.

`python3 tests/test_fxplane.py` runs the synthetic checks; add a stock
exefs/main path to also check the real module's site is recognised.

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
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import ff7nx_fxplane as P                                      # noqa: E402


def synthetic(word=P.STOCK_WORD):
    img = bytearray(P.SITE + 0x100)
    va, words = P.ANCHOR
    for i, x in enumerate(words):
        struct.pack_into('<I', img, va + 4 * i, x)
    struct.pack_into('<I', img, P.SITE - 4, 0x94000000)
    struct.pack_into('<I', img, P.SITE, word)
    for i, x in enumerate(P.AFTER):
        struct.pack_into('<I', img, P.SITE + 4 + 4 * i, x)
    return img


def test_states():
    assert P.site_state(synthetic()) == 'stock'
    assert P.site_state(synthetic(P.movz_w8(P.SCALE))) == 'patched'
    assert P.site_state(synthetic(0x12345678)).startswith('unexpected')
    img = synthetic()
    struct.pack_into('<I', img, P.SITE + 4, 0)
    assert P.site_state(img).startswith('context')
    img = synthetic()
    struct.pack_into('<I', img, P.ANCHOR[0], 0)
    assert P.site_state(img).startswith('sprite record')


def test_scale():
    # covers a 16:9 corner at every angle: the square is 5/3 half heights
    # (exactly a 4:3 half diagonal) at x1; a 16:9 corner is sqrt(337)/9 out.
    need = (337 ** 0.5 / 9) / (5 / 3)
    assert P.SCALE / 4096 >= need * 1.05, (P.SCALE / 4096, need)
    assert P.movz_w8(P.SCALE) == 0x52800008 | (P.SCALE << 5)
    assert P.SCALE < 0x8000          # read back as a signed halfword


if __name__ == '__main__':
    test_states()
    test_scale()
    if len(sys.argv) == 2:
        import nxmap
        st = P.site_state(nxmap.Main(sys.argv[1]).img)
        assert st in ('stock', 'patched'), st
        print('module:', st)
    print('ok')
