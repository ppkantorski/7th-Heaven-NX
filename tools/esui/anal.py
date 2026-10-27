"""tools/esui/anal.py

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
import sys,os,json,collections,struct; sys.path.insert(0,'.')
import iro, exe_patch, capstone
m=iro.Manifest('tools/esui/mod.xml')
S=json.load(open(os.path.expanduser('~/mnt/7th_heaven_nx/settings.json')))['Enhanced Stock UI-rel2.iro']['options']
fol=iro.active_folders(m,S)
root='tools/esui/x'
allf={}
for dp,dn,fn in os.walk(root):
    for f in fn:
        p=os.path.join(dp,f); allf[os.path.relpath(p,root).lower()]=p
files=[]
for fo in fol:
    pre=fo.lower()+'/hext/'
    for k in sorted(allf):
        if k.startswith(pre) and k.endswith('.txt'): files.append((fo,allf[k]))
exe=open('dump/romfs/ff7/resources/ff7_1.02/ff7_en','rb').read()
pe=exe_patch.parse_pe(exe)
def sec(va):
    for name,sva,vsz,fo_,rsz in pe['sections']:
        if sva<=va<sva+vsz: return name
md=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_32); md.detail=True
tot=collections.Counter(); per=[]
for fo,p in files:
    txt=open(p,encoding='utf-8',errors='replace').read()
    pts=list(exe_patch.parse_hext(txt))
    c=collections.Counter(sec(va) for va,b in pts)
    per.append((fo,os.path.basename(p),len(pts),dict(c)))
    tot.update(c)
for r in per: print(r)
print('TOTAL',tot, 'files',len(files))
json.dump([(fo,p) for fo,p in files],open('tools/esui/active_hext.json','w'))
