"""tools/esui/port/tex2x.py

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
import sys, os, json, time, types
sys.path.insert(0, '.')
import ff7nx_esui as E
E.CURRENT = {'mod': types.SimpleNamespace(path='/Volumes/NVME/Users/ppkantorski/Downloads/ff7_mods/7th_heaven_nx/mods/Enhanced Stock UI-rel2.iro'),
             'folders': json.load(open('tools/esui/folders.json'))}
os.environ[E.SCALE_ENV] = sys.argv[1]
t = time.time()
out = E.build_textures('dump/romfs/ff7/workingdir/data/menu/menu_us.lgp', sys.argv[2], log=print)
print(len(out), 'sheets', '%.0fs' % (time.time() - t))
json.dump(out, open(os.path.join(sys.argv[2], 'index.json'), 'w'))
