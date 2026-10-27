"""tools/esui/qdiff.py

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
import sys, os
sys.path.insert(0, '.'); sys.path.insert(0, 'tools/esui'); sys.path.insert(0, os.path.dirname(__file__))
import preview2 as P2, render_x86 as RX, render_arm as RA, ff7nx_esui as E
BUILT = '../../7th_heaven_nx/sdout/atmosphere/contents/0100A5B00BDC6000/romfs/ff7'
built_en = open(BUILT + '/resources/ff7_1.02/ff7_en', 'rb').read()
secs, _ = E._sections(open(BUILT + '/workingdir/data/lang-en/kernel/window.bin', 'rb').read()); w = secs[2][1][:256]
P2.pk[0xdc12dc] = 1
RA.MODULE[0] = sys.argv[1]
a, _ = RA.run(built_en, w, P2.pk, names=P2.names)
b, _ = RX.run(P2.esui_exe, w, P2.pk, names=P2.names)
qa = [(e[1], e[2]) for e in a if e[0] == "quad"]
qb = [(e[1], e[2]) for e in b if e[0] == "quad"]
print(len(qa), len(qb))
n = 0
for i, (x, y) in enumerate(zip(qa, qb)):
    if x != y:
        print(i, 'arm', x, 'x86', y); n += 1
        if n > 30: break
