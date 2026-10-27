"""tools/esui/port/frameeval.py

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
import sys, json, types, struct; sys.path.insert(0, '.')
import ff7nx_esui as E, lgp, tex, numpy as np, ff7nx_ddstex as DT, iro
fol = json.load(open(sys.argv[1]))
E.CURRENT = {'mod': types.SimpleNamespace(path='mods/Enhanced Stock UI-rel2.iro'), 'folders': fol}
srcs = E.texture_sources()
van = lgp.Archive('dump/romfs/ff7/workingdir/data/menu/menu_us.lgp')
name = sys.argv[2] if len(sys.argv) > 2 else 'btl_win_c_l.tex'
v = van.index[name]['payload']
# capture imgs as build_texture makes them
cap = {}
orig = E.joint_tex
def spy(t, imgs, scale): cap['imgs'] = imgs; cap['t'] = t; return orig(t, imgs, scale)
E.joint_tex = spy
for wgt in map(float, sys.argv[3].split(',')):
    pass
    data, note = E.build_texture(name, v, srcs[name], 2)
    t = tex.parse(data); n = t['num_palettes']
    pal = np.frombuffer(t['palette'], np.uint8).reshape(n, 256, 4); ix = np.frombuffer(t['pixels'], np.uint8).reshape(-1)
    W, H = t['width'], t['height']; cov = 0; cov0 = 0; tot = 0
    for p in range(n):
        a = DT._resample_area(np.ascontiguousarray(cap['imgs'][p]), W, H)
        on = a[:, :, 3].reshape(-1) >= 128
        got = pal[p][ix][:, 3] > 0
        bad = (on != got).sum(); cov += bad; tot += on.size
        if p == 0: cov0 = bad
    print('w=%g %s coverage errors %d (palette 0: %d)' % (wgt, note.split(',')[-1], cov, cov0))
