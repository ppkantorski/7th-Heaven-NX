"""BUILD 801: the shop finger no longer covers the word before it.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os, sys
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import ff7nx_esui as E  # noqa: E402

FINGER = (13.67, 38.33)     # finger ink relative to the cursor x (hardware photo 10-24)
STEP = 0x42


def test_grids_match():
    full = E.SITE_TWEAKS['shop'][0x71AAA3]
    assert [dx for dx, _ in full[0xE32228][1][-1][1]] == [0, STEP, 2 * STEP]
    kinds = [dx for dx, _ in full[0xE31FD0][1][-1][1]]
    assert kinds[1] - kinds[0] == STEP
    tw = E.TWEAKS['shop']
    for va in (0xE32108, 0xE2E12C, 0xE28E1C):
        assert tw[va] == 0x52800849                      # mov w9, #0x42
    assert E.STORE_OVERRIDE['shop'][0x71AAA3][0xE31EF4] == ('add', 0)


def test_strings_split():
    d = {va: new for va, old, new, _ in E.DATA_SHOP}
    s = d[0x922D78]
    assert s[:5] == bytes.fromhex('2954454dff')          # "Item" ends
    assert s[6:14] == bytes.fromhex('2d415445524941ff')  # "Materia" at 0x922D7E


def test_finger_clears_previous_word():
    # word ink widths (units) measured on the photo: Buy 29.3, Sell 26.7, Item 31.3
    bx = 214                                             # Buy/Sell/Exit box x
    for k, prev_w in ((1, 1.33 + 29.33), (2, 1.67 + 26.67)):
        prev_right = bx + 0x11 + (k - 1) * STEP + prev_w
        finger_left = bx - 0x1A + k * STEP + FINGER[0]
        assert finger_left - prev_right >= 5
    bx = 220                                             # Item/Materia box x
    item_right = bx + 0x22 + 1.67 + 31.33
    assert bx - 8 + STEP + FINGER[0] - item_right >= 4


if __name__ == '__main__':
    for n, f in list(globals().items()):
        if n.startswith('test_'):
            f(); print('ok', n)
