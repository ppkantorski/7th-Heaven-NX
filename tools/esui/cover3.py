"""tools/esui/cover3.py

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
import sys,collections,pickle,json; sys.path.insert(0,'.'); sys.path.insert(0,'tools/esui')
from xlate import *; from calls import *
files=json.load(open('tools/esui/active_hext.json'))
new=bytearray(exe)
for fo,p in files:
    if fo=='World/Pos': continue
    for va,b in exe_patch.parse_hext(open(p,encoding='utf-8',errors='replace').read()):
        o=off(va)
        if o is not None: new[o:o+len(b)]=b
new=bytes(new)
out=pickle.load(open('tools/esui/cover2.pkl','rb'))
um=collections.defaultdict(list)
for fs,r,st,info in out:
    if st=='unmatched' and r[3].startswith('push'): um[fs].append((r,info))
stat=collections.Counter(); res=[]
for fs,lst in um.items():
    _,fe=M.containing(fs); a0,a1=M.extent(fs)
    xc=x86_calls(fs,fe,exe); nc={c[0]:c for c in x86_calls(fs,fe,new)}
    ac=arm_calls(a0,a1)
    # index push addr -> (call, position)
    pos={}
    for c in xc:
        for p,(pa,v) in enumerate(c[2]): pos[pa]=(c,p)
    for r,(e,nv) in lst:
        if r[2] not in pos: stat['push-not-before-call']+=1; continue
        c,p=pos[r[2]]
        tgt=M.x86_to_arm.get(c[1])
        cands=[a for a in ac if a[1]==tgt]
        if not cands: stat['no-arm-call']+=1; continue
        xv=[v for _,v in c[2]]
        def score(a):
            av=[s[1] for s in a[2]]
            return sum(1 for q in range(min(len(av),len(xv))) if av[q]==xv[q] and xv[q] is not None)
        best=max(cands,key=score); sc=score(best)
        if p<len(best[2]) and best[2][p][1]==e[3]:
            stat['match-by-call']+=1; res.append((fs,r,e,nv,best[2][p]))
        elif p<len(best[2]):
            stat['call-pos-mismatch val=%s'%('reg' if best[2][p][1] is None else 'other')]+=1
        else: stat['short']+=1
print(stat)
pickle.dump(res,open('tools/esui/cover3.pkl','wb'))
