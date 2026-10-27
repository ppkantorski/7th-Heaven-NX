"""tools/esui/port/prev2x.py

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
sys.path.insert(0, '.'); sys.path.insert(0, 'tools/esui'); sys.path.insert(0, os.path.dirname(__file__))
from PIL import ImageFont, Image
_tt = ImageFont.truetype
ImageFont.truetype = lambda p, s: _tt('/System/Library/Fonts/Supplemental/Arial.ttf', s)
import preview2 as P2, render_arm as RA, lgp, ff7nx_esui as E
BUILT = '../../7th_heaven_nx/sdout/atmosphere/contents/0100A5B00BDC6000/romfs/ff7'
en = open(BUILT + '/resources/ff7_1.02/ff7_en', 'rb').read()
secs, _ = E._sections(open(BUILT + '/workingdir/data/lang-en/kernel/window.bin', 'rb').read()); w = secs[2][1][:256]
P2.pk[0xdc12dc] = 1
P2.R.run = RA.run; RA.MODULE[0] = sys.argv[1]
P2.arc = lgp.Archive(BUILT + '/workingdir/data/menu/menu_us.lgp')
ims = []
for idx, t in (({}, 'ESUI sheets at 1x (your build now)'), (json.load(open(sys.argv[2])), 'ESUI sheets at 2x')):
    P2.idx = idx
    _r = P2.render
    im = P2.render(en, w, True, t, S=3)
    ims.append(im.crop((150 * 3, 30 * 3, 680 * 3, 300 * 3)))
W = Image.new('RGB', (ims[0].width, ims[0].height * 2 + 20))
W.paste(ims[0], (0, 0)); W.paste(ims[1], (0, ims[0].height + 20))
W.save(sys.argv[3])
