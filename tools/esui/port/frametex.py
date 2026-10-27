"""tools/esui/port/frametex.py

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
import ff7nx_esui as E, lgp, tex, numpy as np
fol = json.load(open(sys.argv[1]))
E.CURRENT = {'mod': types.SimpleNamespace(path='mods/Enhanced Stock UI-rel2.iro'), 'folders': fol}
srcs = E.texture_sources()
van = lgp.Archive('dump/romfs/ff7/workingdir/data/menu/menu_us.lgp')
v = van.index['btl_win_c_l.tex']['payload']
data, note = E.build_texture('btl_win_c_l.tex', v, srcs['btl_win_c_l.tex'], 2)
print(note, sorted(srcs['btl_win_c_l.tex']))
t = tex.parse(data); pal = np.frombuffer(t['palette'], np.uint8).reshape(-1, 256, 4); ix = np.frombuffer(t['pixels'], np.uint8).reshape(t['height'], -1)
for y in range(392, 398): print(y / 2, ix[y, 16], pal[0][ix[y, 16]])
for x in range(0, 16): print('x', x / 2, ix[408, x], pal[0][ix[408, x]])
open(sys.argv[2] if len(sys.argv) > 2 else '/dev/null', 'wb').write(data)
