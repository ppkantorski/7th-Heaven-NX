"""tools/esui/newexe.py

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
import sys,os,json,collections,pickle,bisect; sys.path.insert(0,'.')
import exe_patch, capstone, nxmap
from capstone import x86 as X
files=json.load(open('tools/esui/active_hext.json'))
EXE='dump/romfs/ff7/resources/ff7_1.02/ff7_en'
exe=bytearray(open(EXE,'rb').read()); pe=exe_patch.parse_pe(exe)
def off(va): return exe_patch.va_to_offset(pe,va)
def sec(va):
    for name,sva,vsz,fo_,rsz in pe['sections']:
        if sva<=va<sva+vsz: return name
new=bytearray(exe); patches=[]
for fo,p in files:
    if fo=='World/Pos': continue
    for va,b in exe_patch.parse_hext(open(p,encoding='utf-8',errors='replace').read()):
        o=off(va)
        if o is None: continue
        new[o:o+len(b)]=b; patches.append((fo,os.path.basename(p),va,len(b)))
open('tools/esui/ff7_en.esui','wb').write(new)
pickle.dump(patches,open('tools/esui/patches.pkl','wb'))
