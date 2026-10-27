"""for a HEXT file: list each patch with stock bytes, and whether it differs

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
import sys,re; sys.path.insert(0,'.')
import exe_patch
buf=open('dump/romfs/ff7/resources/ff7_1.02/ff7_en','rb').read()
pe=exe_patch.parse_pe(buf)
def off(va):
    for name,sva,vs,ro,rs in pe['sections']:
        if sva<=va<sva+rs: return ro+va-sva
cmt=''
for line in open(sys.argv[1],errors='replace'):
    line=line.strip()
    if line.startswith('{'): cmt=line[1:60]; continue
    m=re.match(r'^([0-9A-Fa-f]+)\s*=\s*([0-9A-Fa-f ]+)$',line)
    if not m: continue
    va=int(m.group(1),16); new=bytes.fromhex(m.group(2).replace(' ',''))
    o=off(va); old=buf[o:o+len(new)] if o is not None else b''
    print('%6x %-24s %-24s %s %s'%(va,old.hex(),new.hex(),'SAME' if old==new else '    ',cmt))
