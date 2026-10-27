"""tools/esui/combo.py

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
import diffemu as D, align as A, exe_patch, sys
H='tools/esui/x/EnhancedStock/hext/'
files=[H+'00-Main.txt',H+'00-Spacing.txt','tools/esui/calendar.txt','tools/esui/x/ECHOS/MAIN/hext/05 - ESUI Theme.txt']
new=bytearray(A.exe)
for p in files:
    for va,b in exe_patch.parse_hext(open(p,encoding='utf-8',errors='replace').read()):
        o=A.off(va)
        if o is not None: new[o:o+len(b)]=b
new=bytes(new)
fn=0x6ca346
D.NATIVE={0x6c9808,(0x913000,0x916000)}
seed=int(sys.argv[1]) if len(sys.argv)>1 else 0
D.RETMODE[0]=seed
args=[0,0,0,0]
c1,w1,s1=D.run_x86(new,fn,seed,args)
d1,v1,r1=D.run_arm({},A.exe,fn,seed,args,module='tools/esui/main.cal_esui')
print(s1,r1,len(c1),len(d1))
def show(c):
    return [(hex(t),[hex(x) if x>0xffff else x for x in a[:6]]) for t,a in c]
import difflib
X=show(c1); R=show(d1)
sm=difflib.SequenceMatcher(None,[str(x) for x in X],[str(x) for x in R],autojunk=False)
for tg,i1,i2,j1,j2 in sm.get_opcodes():
    if tg=='equal': print('  = %d calls equal'%(i2-i1)); continue
    for k in range(max(i2-i1,j2-j1)):
        a=X[i1+k] if i1+k<i2 else ''; b=R[j1+k] if j1+k<j2 else ''
        print('  x86 %-60s | arm %s'%(a,b))
    if k>40: break
