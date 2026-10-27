"""tools/esui/port/texcmp.py

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
import sys, json, types, os
sys.path.insert(0, '.')
import ff7nx_esui as E, lgp, tex, numpy as np, ff7nx_ddstex as DT
from PIL import Image
fol = json.load(open(sys.argv[1])); name = sys.argv[2]; out = sys.argv[3]
E.CURRENT = {'mod': types.SimpleNamespace(path='mods/Enhanced Stock UI-rel2.iro'), 'folders': fol}
srcs = E.texture_sources()
van = lgp.Archive('dump/romfs/ff7/workingdir/data/menu/menu_us.lgp')
v = van.index[name]['payload']
cap = {}
orig = E.joint_tex
def spy(t, imgs, scale, **kw): cap['imgs'] = imgs; return orig(t, imgs, scale, **kw)
E.joint_tex = spy
data, note = E.build_texture(name, v, srcs[name], 2)
print(note); print({p: s for p, s in srcs[name].items()})
t = tex.parse(data); n = t['num_palettes']; W, H = t['width'], t['height']
pal = np.frombuffer(t['palette'], np.uint8).reshape(n, 256, 4); ix = np.frombuffer(t['pixels'], np.uint8).reshape(H, W)
rows = []
for p in range(n):
    if 'imgs' in cap:
        a = DT._resample_area(np.ascontiguousarray(cap['imgs'][p]), W, H).astype(np.uint8)
    else:
        a = np.zeros((H, W, 4), np.uint8)
    b = pal[p][ix][:, :, [2, 1, 0, 3]]
    b = b.copy(); b[:, :, 3] = np.where(b[:, :, 3] > 0, 255, 0)
    rows.append(np.concatenate([a, b], 1))
    if 'imgs' in cap:
        on = a[:, :, 3] >= 128
        d = np.abs(a[:, :, :3].astype(int) - b[:, :, :3].astype(int)).sum(2)
        print('pal', p, 'on', on.sum(), 'mean err', round(float(d[on].mean()), 1) if on.any() else 0, 'max', int(d[on].max()) if on.any() else 0, 'cover mismatch', int((on != (b[:, :, 3] > 0)).sum()))
img = np.concatenate(rows, 0)
bg = np.zeros_like(img); bg[:, :, 2] = 80; bg[:, :, 3] = 255
al = img[:, :, 3:4] / 255.0
comp = (img[:, :, :3] * al + bg[:, :, :3] * (1 - al)).astype(np.uint8)
Image.fromarray(comp).save(out)
