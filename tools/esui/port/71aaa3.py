"""BUILD 637: the sell list's cursor (dd6bb8 column, dd6bbc row; s=5 also draws the ghost cursor)
was never varied -- 634's sweep missed ESUI's 0x24 -> 0x18 row pitch there.

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
FNS[0x71aaa3]['pokes'] = FNS[0x71aaa3]['pokes'] + [
    {0x92565c: s, 0xdc130c: 0, 0xdd4734: 1, 0xdd4724: k, 0xdd6bb8: c, 0xdd6bbc: r, 0xdd6bcc: sc, 0xdd6be8: b}
    for s in (4, 5, 6) for k in (0, 6, 15) for c in (0, 1) for r in (0, 3, 9, 14) for sc in (0, 2) for b in (0, 1)]
