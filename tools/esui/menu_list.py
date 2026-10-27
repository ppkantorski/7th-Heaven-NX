"""tools/esui/menu_list.py

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
import sys,pickle,collections,json,os; sys.path.insert(0,'.')
import exe_patch
files=json.load(open('tools/esui/active_hext.json'))
want=[(fo,p) for fo,p in files if fo=='EnhancedStock' and os.path.basename(p).startswith(('00-','01-'))]
exe=open('dump/romfs/ff7/resources/ff7_1.02/ff7_en','rb').read(); pe=exe_patch.parse_pe(exe)
for fo,p in want:
    print('==',os.path.basename(p))
    for va,b in exe_patch.parse_hext(open(p,encoding='utf-8',errors='replace').read()):
        o=exe_patch.va_to_offset(pe,va); old=exe[o:o+len(b)] if o is not None else b''
        sec=[n for n,sva,vsz,fo_,rsz in pe['sections'] if sva<=va<sva+max(vsz,rsz)]
        print('  %x %s len %d  old %s  new %s'%(va,sec,len(b),old[:24].hex(),bytes(b)[:24].hex()))
