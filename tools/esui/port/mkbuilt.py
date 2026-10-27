"""built module + ff7_en for Patrick's settings; and compare the build's ff7_en data with DATA_NEW

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
import sys, os, json
S = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, '.')
import build, ff7nx_esui as E, exe_patch
mod = build.Mod('mods/Enhanced Stock UI-rel2.iro', 'cache'); mod.ensure_extracted()
base = json.load(open('/Volumes/NVME/Users/ppkantorski/Downloads/ff7_mods/7th_heaven_nx/settings.json'))['Enhanced Stock UI-rel2.iro']['options']
E.CURRENT = E.plan_for(mod, base)
assert E.apply_to_nso('dump/exefs/main', os.path.join(S, 'built_main'), print)
x = open('dump/romfs/ff7/resources/ff7_1.02/ff7_en', 'rb').read(); pe = exe_patch.parse_pe(x)
y, n = E.patch_exe(x, lambda va: exe_patch.va_to_offset(pe, va), print)
open(os.path.join(S, 'built_en'), 'wb').write(y)
print('data patches', n)
