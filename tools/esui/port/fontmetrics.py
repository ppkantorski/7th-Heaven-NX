"""ink rows of glyph cells (in 24-unit quad space) from the built sheets

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
import sys, numpy as np
sys.path.insert(0, '.')
import lgp, tex
BUILT = '../../7th_heaven_nx/sdout/atmosphere/contents/0100A5B00BDC6000/romfs/ff7/workingdir/data/menu/menu_us.lgp'
_A = lgp.Archive(BUILT)
_cache = {}
def sheet(name):
    if name not in _cache:
        d = _A.index[name]['payload']; t = tex.parse(d)
        pal = np.frombuffer(t['palette'], np.uint8).reshape(-1, t['colors_per_palette'], 4)
        ix = np.frombuffer(t['pixels'], np.uint8).reshape(t['height'], t['width'])
        _cache[name] = (pal, ix, t['width'], t['height'])
    return _cache[name]
def ink(name, u0, v0, u1, v1, p=14):
    pal, ix, W, H = sheet(name)
    x0, x1, y0, y1 = int(round(u0 * W)), int(round(u1 * W)), int(round(v0 * H)), int(round(v1 * H))
    cell = pal[min(p, len(pal) - 1)][ix[y0:y1, x0:x1]]
    a = (cell[:, :, 3] > 0) & (cell[:, :, :3].max(2) > 40) & (ix[y0:y1, x0:x1] > 0)
    rows = np.where(a.any(1))[0]
    if not len(rows): return None
    h = y1 - y0
    return rows.min() * 24.0 / h, (rows.max() + 1) * 24.0 / h      # top, bottom in quad units
