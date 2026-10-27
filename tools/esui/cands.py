"""tools/esui/cands.py

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
import sys,pickle,collections,json,os,struct; sys.path.insert(0,'.')
import exe_patch, capstone, nxmap
from capstone import arm64 as A64, x86 as X
exe=open('dump/romfs/ff7/resources/ff7_1.02/ff7_en','rb').read(); new=open(os.environ.get('ESUI_NEW','tools/esui/ff7_en.esui'),'rb').read()
pe=exe_patch.parse_pe(exe); off=lambda va: exe_patch.va_to_offset(pe,va)
M=nxmap.Main('dump/exefs/main')
cx=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_32); cx.detail=True
ca=capstone.Cs(capstone.CS_ARCH_ARM64,capstone.CS_MODE_ARM); ca.detail=True
def s32(v): v&=0xffffffff; return v-(1<<32) if v&0x80000000 else v
def armvals(a0,a1):
    """[(addr, value, words, text)] for every immediate the ARM builds (mov/movk chains merged)."""
    ins=list(ca.disasm(M.text[a0:a1],a0)); out=[]; pend={}
    for i in ins:
        m=i.mnemonic; ops=i.operands
        if m in ('mov','movz','movn') and len(ops)==2 and ops[1].type==A64.ARM64_OP_IMM:
            v=ops[1].imm; r=ops[0].reg; e=[i.address,s32(v),[i.address],i.mnemonic+' '+i.op_str]; pend[r]=e; out.append(e)
        elif m=='movk' and ops[0].reg in pend:
            e=pend[ops[0].reg]; sh=ops[1].shift.value or 0
            e[1]=s32((e[1]&~(0xffff<<sh))|(ops[1].imm<<sh)); e[2].append(i.address); e[3]+=' ; movk'
        else:
            for op in ops:
                if op.type==A64.ARM64_OP_IMM and m not in ('b','bl','cbz','cbnz','tbz','tbnz','adrp','adr') and not m.startswith('b.'):
                    v=op.imm<<(op.shift.value or 0)
                    if m in ('sub','subs','cmn'): v=-v
                    out.append([i.address,s32(v),[i.address],m+' '+i.op_str])
    return out
if __name__=='__main__':
    va=int(sys.argv[1],16); fs,fe=M.containing(va); a0,a1=M.extent(fs)
    i_old=next(cx.disasm(exe[off(va)-8:off(va)+16],va-8))  # rough
    # exact instruction containing va
    for i in cx.disasm(exe[off(fs):off(fe)],fs):
        if i.address<=va<i.address+i.size: io=i; break
    inew=next(cx.disasm(new[off(io.address):off(io.address)+16],io.address))
    print('x86 %x: %s %s  ->  %s %s   (fn %x arm %x..%x)'%(io.address,io.mnemonic,io.op_str,inew.mnemonic,inew.op_str,fs,a0,a1))
    olds=[op.imm for op in io.operands if op.type==X.X86_OP_IMM]+[op.mem.disp for op in io.operands if op.type==X.X86_OP_MEM]
    for o in olds:
        o=s32(o); rel={o:'=',o+1:'+1',o-1:'-1',-o:'neg',-o+1:'-o+1',-o-1:'-o-1'}
        hits=[(a,v,t) for a,v,w,t in armvals(a0,a1) if v in rel]
        print('  value %#x: %d ARM hits'%(o,len(hits)))
        for a,v,t in hits[:40]: print('     %x %-4s %s'%(a,rel[v],t))
