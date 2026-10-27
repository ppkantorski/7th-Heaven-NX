"""which screens are ported for which ESUI options (after genmerge2)

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
import sys, os, json, itertools, types, importlib
sys.path.insert(0, '.')
import iro, build
import ff7nx_esui as E
mod = build.Mod('mods/Enhanced Stock UI-rel2.iro', 'cache'); mod.ensure_extracted()
base = json.load(open('/Volumes/NVME/Users/ppkantorski/Downloads/ff7_mods/7th_heaven_nx/settings.json'))['Enhanced Stock UI-rel2.iro']['options']
axes = {'battleview': [3, 1, 2, 4, 5], 'ba': [0, 1, 2], 'tbb': [1, 0], 'nbb': [0, 1], 'hbb': [0, 1, 2],
        'icons': [2, 0, 1], 'bars': [2, 0, 1], 'ECHOS': [1, 0], 'nt': [0, 1]}
def screens_for(s):
    E.CURRENT = E.plan_for(mod, s); E._SCREEN_PORTS.clear()
    return tuple(sc for sc in ('load', 'shop', 'battle', 'menus') if E.screen_ports(sc))
print('Patrick:', screens_for(base))
for k, vals in axes.items():
    for v in vals:
        if v == base.get(k): continue
        print('%s=%s:' % (k, v), screens_for(dict(base, **{k: v})))
defaults = {o.id: o.default for o in mod.manifest.options}
print('ESUI defaults:', screens_for(defaults))
