"""tools/esui/port/shopprev.py

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
import sys, os, json, types
sys.path.insert(0, '.'); sys.path.insert(0, 'tools/esui'); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import ImageFont, Image
_tt = ImageFont.truetype
ImageFont.truetype = lambda p, s: _tt('/System/Library/Fonts/Supplemental/Arial.ttf', s)
import preview2 as P2, render_arm as RA, lgp, ff7nx_esui as E, exe_patch
BUILT = '../../7th_heaven_nx/sdout/atmosphere/contents/0100A5B00BDC6000/romfs/ff7'
stock_en = open('dump/romfs/ff7/resources/ff7_1.02/ff7_en', 'rb').read()
pe = exe_patch.parse_pe(stock_en)
E.CURRENT = {'mod': types.SimpleNamespace(path='x'), 'folders': []}
esui_en, _ = E.patch_exe(stock_en, lambda va: exe_patch.va_to_offset(pe, va))
secs, _ = E._sections(open(BUILT + '/workingdir/data/lang-en/kernel/window.bin', 'rb').read()); w_esui = secs[2][1][:256]
w_stock = open('tools/esui/w_stock.bin', 'rb').read()
P2.R.run = RA.run; RA.FN = 0x71aaa3
P2.arc = lgp.Archive(BUILT + '/workingdir/data/menu/menu_us.lgp')
idx2x = json.load(open(sys.argv[3]))
state = int(sys.argv[4]); shop = int(sys.argv[5]) if len(sys.argv) > 5 else 15
P2.pk.update({0x92565c: state, 0xdc130c: 0, 0xdd4734: 1, 0xdd4724: shop, 0xdd4710: 0x925660,
              0xdd6bb0: 0, 0xdd6be8: 0, 0xdd6c20: 0, 0xdd6b94: 0, 0xdd6ba4: 0, 0xdd6bcc: 0, 0xdd6bdc: 0})
ims = []
for mod, en, w, idx, esui, t in (('dump/exefs/main', stock_en, w_stock, {}, False, 'stock (now)'),
                                  (sys.argv[1], esui_en, w_esui, idx2x, True, 'BUILD 625: ESUI shop')):
    RA.MODULE[0] = mod; RA._M.clear(); P2.idx = idx
    ims.append(P2.render(en, w, esui, t, S=2))
W = Image.new('RGB', (ims[0].width * 2 + 16, ims[0].height))
W.paste(ims[0], (0, 0)); W.paste(ims[1], (ims[0].width + 16, 0))
W.save(sys.argv[2])
