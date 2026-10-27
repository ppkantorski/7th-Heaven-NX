"""materia menu: materia info box. The port merges case tails (one text call for several
x86 cases with different ESUI deltas), so every ESUI value is fitted at call sites.

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
FNS.setdefault(0x70b092, {})
FNS[0x70b092]['no_align'] = True
_MATP = {0xdd12ac: 0xdd0000, 0xdd0000: 0x00000402, 0xdd0004: 0x1234,
         0xdd0018: 0x05040000, 0xdd001c: 0x0201ff00, 0xdd0020: 0x00030007, 0xdd0024: 0x0000ff00}
FNS[0x70b092]['pokes'] = [{**_MATP, 0xc068b4: k} for k in (0, 1, 2, 3, 5)]
FNS[0x70b092]['argsets'] = [[0, 0, 0, 0], [1, 2, 3, 0], [2, 0, 1, 1], [3, 1, 0, 2],
                            [4, 0, 0, 0], [5, 0, 0, 0], [0x10, 0, 0, 0], [0xffffff20, 0, 0, 0]]
