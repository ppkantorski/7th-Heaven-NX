"""tools/esui/port/armprev.py

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
import sys, os, struct, glob
sys.path.insert(0, '.'); sys.path.insert(0, 'tools/esui'); sys.path.insert(0, os.path.dirname(__file__))
from PIL import ImageFont
_tt = ImageFont.truetype
ImageFont.truetype = lambda p, s: _tt('/System/Library/Fonts/Supplemental/Arial.ttf', s)
import preview2 as P2, render_x86 as RX, render_arm as RA, lgp, ff7nx_esui as E
from PIL import Image
OUT = sys.argv[1]
BUILT = '../../7th_heaven_nx/sdout/atmosphere/contents/0100A5B00BDC6000/romfs/ff7'
stock_en = open('dump/romfs/ff7/resources/ff7_1.02/ff7_en', 'rb').read()
built_en = open(BUILT + '/resources/ff7_1.02/ff7_en', 'rb').read()
def wtab(p):
    secs, _ = E._sections(open(p, 'rb').read()); return secs[2][1][:256]
w_stock = wtab('dump/romfs/ff7/workingdir/data/lang-en/kernel/window.bin')
w_built = wtab(BUILT + '/workingdir/data/lang-en/kernel/window.bin')
P2.pk[0xdc12dc] = 1          # menu hi-res flag, as menu_enter sets it
stock_lgp = lgp.Archive('dump/romfs/ff7/workingdir/data/menu/menu_us.lgp')
built_lgp = lgp.Archive(BUILT + '/workingdir/data/menu/menu_us.lgp')
P2.idx = {}
def go(runner, module, exe, widths, esui, arc, title):
    RA.MODULE[0] = module; P2.arc = arc
    P2.R.run = runner
    im = P2.render(exe, widths, esui, title)
    return im
RX_run = RX.run
ims = [
  go(RA.run, 'dump/exefs/main', stock_en, w_stock, False, stock_lgp, 'SWITCH ARM, stock main + stock ff7_en/window.bin/sheets'),
  go(RA.run, sys.argv[2], built_en, w_built, True, built_lgp, sys.argv[3]),
  go(RX_run, None, P2.esui_exe, w_built, True, built_lgp, 'PC x86 with ESUI HEXT applied (reference), built sheets'),
]
W = Image.new('RGBA', (ims[0].width, sum(i.height for i in ims) + 20 * len(ims)), (0, 0, 0, 255))
y = 0
for i in ims:
    W.paste(i, (0, y)); y += i.height + 20
W.convert('RGB').save(OUT)
