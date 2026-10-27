"""tools/esui/cover2.py

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
import sys,collections,pickle,json,os; sys.path.insert(0,'.'); sys.path.insert(0,'tools/esui')
from xlate import *
files=json.load(open('tools/esui/active_hext.json'))
new=bytearray(exe)
for fo,p in files:
    if fo=='World/Pos': continue
    for va,b in exe_patch.parse_hext(open(p,encoding='utf-8',errors='replace').read()):
        o=off(va)
        if o is not None: new[o:o+len(b)]=b
recs=pickle.load(open('tools/esui/recs.pkl','rb'))
byf=collections.defaultdict(list)
for r in recs: byf[r[6]].append(r)
stat=collections.Counter(); out=[]
for fs,rs in sorted(byf.items()):
    _,fe=M.containing(fs); a0,a1=M.extent(fs)
    xi,xe=x86_events(fs,fe); _,ne=x86_events(fs,fe,bytes(new))
    ai,ae,const=arm_events2(a0,a1)
    pairs=align(xe,ae)
    nmap={(e[0],e[1],e[2]):e for e in ne}
    for r in rs:
        if r[5]!='same-op': stat['struct']+=1; out.append((fs,r,'struct',None)); continue
        ch=[(k,e) for k,e in enumerate(xe) if e[0]==r[2] and nmap.get((e[0],e[1],e[2])) and nmap[(e[0],e[1],e[2])][3]!=e[3]]
        if not ch: stat['noev']+=1; out.append((fs,r,'noev',None)); continue
        for k,e in ch:
            nv=nmap[(e[0],e[1],e[2])][3]
            if k in pairs:
                a=ae[pairs[k]]; stat['m:'+a[1]]+=1; out.append((fs,r,'ok',(e,nv,a)))
            else:
                stat['u:'+r[3].split()[0]]+=1; out.append((fs,r,'unmatched',(e,nv)))
print(sorted(stat.items(),key=lambda x:-x[1]))
pickle.dump(out,open('tools/esui/cover2.pkl','wb'))
