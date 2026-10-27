"""stock ff7_en + every active ESUI HEXT, in 7th Heaven's application order
(declaration order: base EnhancedStock first, option folders after).

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
import sys, json, glob, os
sys.path.insert(0, '.')
import exe_patch
SC = os.path.dirname(os.path.abspath(__file__))
EXE = 'dump/romfs/ff7/resources/ff7_1.02/ff7_en'
def order():
    fol = json.load(open(os.environ.get('ESUI_FOLDERS', os.path.join(SC, 'folders_now.json'))))
    import re
    xml = open('tools/esui/mod.xml', encoding='utf-8', errors='replace').read()
    pos = {}
    for m in re.finditer(r'Folder="([^"]+)"', xml):
        pos.setdefault(m.group(1).replace('\\', '/').lower(), m.start())
    return sorted(fol, key=lambda f: pos.get(f.lower(), 1 << 30))
def files(skip=()):
    out = []
    for f in order():
        for p in sorted(glob.glob('tools/esui/x/' + f + '/hext/*.txt')):
            if any(s in p for s in skip):
                continue
            out.append((f, p))
    return out
def build(skip=()):
    buf = bytearray(open(EXE, 'rb').read())
    pe = exe_patch.parse_pe(bytes(buf))
    for f, p in files(skip):
        for va, b in exe_patch.parse_hext(open(p, encoding='utf-8', errors='replace').read()):
            o = exe_patch.va_to_offset(pe, va)
            if o is not None:
                buf[o:o + len(b)] = b
    return bytes(buf)
if __name__ == '__main__':
    for f, p in files(): print(f, os.path.basename(p))
