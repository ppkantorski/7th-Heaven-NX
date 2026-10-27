#!/usr/bin/env python3
"""BUILD 645c: the battle Limit menu's names take ESUI's x (ff7nx_esui.limit_name_word).
BUILD 645e: the boxes take ESUI's Transparency Strength alpha (opacity_word).
BUILD 684: the names take ESUI's row step, matching the cursor (limit_step_word).

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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import ff7nx_esui as E

FAIL = []


def check(ok, what):
    print('  %s  %s' % ('ok  ' if ok else 'FAIL', what))
    if not ok:
        FAIL.append(what)


def with_x(x):
    E.hext_bytes = lambda: {E.LIMIT_NAME_HEXT + i: b for i, b in
                            enumerate((x & 0xFFFFFFFF).to_bytes(4, 'little'))}
    return E.limit_name_word(E.LIMIT_NAME_STOCK)


check(with_x(128) == 0x5280101B, 'x 128 -> mov w27, #0x80')
check(with_x(48) == 0x5280061B, 'x 48 -> mov w27, #0x30')
check(with_x(-64) == 0x128007FB, 'x -64 -> mov w27, #-0x40 (movn)')
check(with_x(0xB0) == E.LIMIT_NAME_STOCK, 'the stock x gives the stock word')
E.hext_bytes = lambda: {E.LIMIT_NAME_HEXT + i: b for i, b in enumerate(b'\x80\0\0\0')}
check(E.limit_name_word(0xD503201F) is None, 'an unexpected word is left alone')
E.hext_bytes = lambda: {}
check(E.limit_name_word(E.LIMIT_NAME_STOCK) is None, 'no ESUI x: nothing written')

for alpha, word in ((0xBF, 0x528017E8), (0x96, 0x528012C8)):
    E.hext_bytes = lambda a=alpha: {E.OPACITY_HEXT: a}
    check(E.opacity_word(E.OPACITY_STOCK) == word,
          'alpha 0x%X -> mov w8, #0x%X' % (alpha, alpha))
E.hext_bytes = lambda: {}
check(E.opacity_word(E.OPACITY_STOCK) is None, 'no Opacity folder: stock alpha kept')
E.hext_bytes = lambda: {E.OPACITY_HEXT: 0xBF}
check(E.opacity_word(0xD503201F) is None, 'an unexpected word is left alone')

E.hext_bytes = lambda: {E.LIMIT_STEP_HEXT: 0x18}
check(E.limit_step_word(E.LIMIT_STEP_STOCK) == 0x5280031A, 'step 24 -> mov w26, #0x18')
check(E.limit_step_word(0xD503201F) is None, 'step: an unexpected word is left alone')
E.hext_bytes = lambda: {E.LIMIT_STEP_HEXT: 0x1E}
check(E.limit_step_word(E.LIMIT_STEP_STOCK) == 0x528003DA, 'the stock step 30 gives mov w26, #0x1e')
E.hext_bytes = lambda: {}
check(E.limit_step_word(E.LIMIT_STEP_STOCK) is None, 'no ESUI step: nothing written')

print('\n%s' % ('FAILED: %d' % len(FAIL) if FAIL else 'all checks passed'))
sys.exit(1 if FAIL else 0)
