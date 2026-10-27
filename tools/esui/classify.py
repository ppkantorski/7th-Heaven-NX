"""tools/esui/classify.py

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
import sys,os,json,collections,struct,pickle; sys.path.insert(0,'.')
import exe_patch, capstone, nxmap
files=json.load(open('tools/esui/active_hext.json'))
exe=bytearray(open('dump/romfs/ff7/resources/ff7_1.02/ff7_en','rb').read())
pe=exe_patch.parse_pe(exe)
def off(va): return exe_patch.va_to_offset(pe,va)
def sec(va):
    for name,sva,vsz,fo_,rsz in pe['sections']:
        if sva<=va<sva+vsz: return name
M=nxmap.Main('dump/exefs/main')
md=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_32); md.detail=True
fcache={}
def insns(fstart,fend,buf):
    o=off(fstart); code=bytes(buf[o:o+(fend-fstart)])
    return list(md.disasm(code,fstart))
# apply patches sequentially to a "new" exe (last write wins)
new=bytearray(exe)
patches=[]
for fo,p in files:
    if fo=='World/Pos': continue
    txt=open(p,encoding='utf-8',errors='replace').read()
    for va,b in exe_patch.parse_hext(txt):
        patches.append((fo,os.path.basename(p),va,bytes(b)))
        o=off(va)
        if o is not None: new[o:o+len(b)]=b
textp=[q for q in patches if sec(q[2])=='.text']
# group by instruction
cls=collections.Counter(); recs=[]
byfun=collections.defaultdict(list)
for q in textp: 
    c=M.containing(q[2]); byfun[c].append(q)
bad=0
for (fs,fe),qs in byfun.items():
    old_i=insns(fs,fe,exe); 
    starts=[i.address for i in old_i]
    import bisect
    seen=set()
    for fo,fn,va,b in qs:
        for k in range(len(b)):
            a=va+k
            j=bisect.bisect_right(starts,a)-1
            ins=old_i[j]
            if ins.address in seen: continue
            seen.add(ins.address)
            o=off(ins.address); nb=bytes(new[o:o+ins.size])
            ni=list(md.disasm(nb,ins.address))
            if not ni or ni[0].size!=ins.size:
                kind='resync'
                recs.append((fo,fn,ins.address,ins.mnemonic+' '+ins.op_str,'??',kind,fs)); cls[kind]+=1; continue
            n=ni[0]
            if n.mnemonic==ins.mnemonic:
                kind='same-op'
            else: kind='op-change'
            recs.append((fo,fn,ins.address,ins.mnemonic+' '+ins.op_str,n.mnemonic+' '+n.op_str,kind,fs)); cls[kind]+=1
print(cls, 'instructions',len(recs),'functions',len(byfun))
mn=collections.Counter((r[3].split()[0],r[5]) for r in recs); print(mn.most_common(40))
pickle.dump(recs,open('tools/esui/recs.pkl','wb'))
for r in [r for r in recs if r[5]!='same-op'][:60]: print(hex(r[2]),r[3],'->',r[4],r[1])
