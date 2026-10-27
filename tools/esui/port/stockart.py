"""tools/esui/port/stockart.py

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
from PIL import ImageFont, Image
_tt = ImageFont.truetype
ImageFont.truetype = lambda p, s: _tt('/System/Library/Fonts/Supplemental/Arial.ttf', s)
import preview2 as P2, render_arm as RA, lgp, ff7nx_esui as E
BUILT = '../../7th_heaven_nx/sdout/atmosphere/contents/0100A5B00BDC6000/romfs/ff7'
built_en = open(BUILT + '/resources/ff7_1.02/ff7_en', 'rb').read()
secs, _ = E._sections(open(BUILT + '/workingdir/data/lang-en/kernel/window.bin', 'rb').read()); w = secs[2][1][:256]
P2.pk[0xdc12dc] = 1; P2.idx = {}
P2.arc = lgp.Archive('dump/romfs/ff7/workingdir/data/menu/menu_us.lgp')   # STOCK sheets
P2.R.run = RA.run; RA.MODULE[0] = sys.argv[2]
im = P2.render(built_en, w, True, 'SWITCH ARM: your main + ff7_en + window.bin, but STOCK menu_us.lgp sheets')
im.convert('RGB').save(sys.argv[1])
