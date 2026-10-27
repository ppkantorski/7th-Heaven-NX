"""prototype: align x86 constant events with ARM64 constant events per function

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
import sys,os,json,collections,struct,pickle,bisect; sys.path.insert(0,'.')
import exe_patch, capstone, nxmap
from capstone import x86 as X, arm64 as A64

EXE='dump/romfs/ff7/resources/ff7_1.02/ff7_en'
exe=bytes(open(EXE,'rb').read()); pe=exe_patch.parse_pe(exe)
def off(va): return exe_patch.va_to_offset(pe,va)
M=nxmap.Main('dump/exefs/main')
cx=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_32); cx.detail=True
ca=capstone.Cs(capstone.CS_ARCH_ARM64,capstone.CS_MODE_ARM); ca.detail=True

def s32(v): v&=0xffffffff; return v-(1<<32) if v&0x80000000 else v

def x86_events(fs,fe,buf=exe):
    o=off(fs); ev=[]; ins=list(cx.disasm(buf[o:o+fe-fs],fs))
    for i in ins:
        for k,op in enumerate(i.operands):
            if op.type==X.X86_OP_IMM:
                if i.mnemonic.startswith('j') or i.mnemonic=='call': continue
                ev.append((i.address,k,'imm',s32(op.imm),i.size))
            elif op.type==X.X86_OP_MEM and op.mem.disp!=0:
                ev.append((i.address,k,'disp',s32(op.mem.disp),i.size))
    return ins,ev

def arm_events(a0,a1):
    ins=list(ca.disasm(M.text[a0:a1],a0)); ev=[]
    pend={}
    for i in ins:
        m=i.mnemonic; ops=i.operands
        def reg(n): return i.reg_name(ops[n].reg) if ops[n].type==A64.ARM64_OP_REG else None
        imms=[op for op in ops if op.type==A64.ARM64_OP_IMM]
        if m in ('mov','movz') and len(ops)==2 and ops[1].type==A64.ARM64_OP_IMM:
            v=ops[1].imm; 
            if ops[1].shift.value: v<<=ops[1].shift.value
            pend[reg(0)]=len(ev); ev.append([i.address,'mov',s32(v),[i.address]])
        elif m=='movn' and imms:
            v=~(imms[0].imm<<(ops[1].shift.value or 0)); pend[reg(0)]=len(ev); ev.append([i.address,'mov',s32(v),[i.address]])
        elif m=='movk' and imms and reg(0) in pend:
            e=ev[pend[reg(0)]]; sh=ops[1].shift.value or 0
            v=(e[2]&~(0xffff<<sh))|(imms[0].imm<<sh); e[2]=s32(v); e[3].append(i.address)
        elif m in ('add','sub','adds','subs','cmp','cmn') and imms:
            v=imms[-1].imm
            if imms[-1].shift.value: v<<=imms[-1].shift.value
            neg = m in ('sub','subs','cmn')
            ev.append([i.address,m,s32(-v if neg else v),[i.address]])
        elif m in ('and','orr','eor','ands','tst') and imms:
            ev.append([i.address,m,s32(imms[-1].imm),[i.address]])
    return ins,ev

def eq(xv,av,kind):
    if xv==av: return True
    if (xv&0xffff)==(av&0xffff) and abs(xv)<0x10000 and abs(av)<0x10000: return True
    return False

def align(xe,ae):
    n,m=len(xe),len(ae)
    import numpy as np
    # LCS via DP rows (int16 ok up to 32767)
    D=np.zeros((n+1,m+1),np.int32)
    av=[a[2] for a in ae]
    for i in range(1,n+1):
        xv=xe[i-1][3]
        row=D[i]; prev=D[i-1]
        mt=np.array([1 if eq(xv,a,None) or eq(-xv,a,None) else 0 for a in av],np.int32)
        cand=prev[:-1]+mt
        # row[j]=max(prev[j], row[j-1], prev[j-1]+mt)
        r=np.maximum(prev[1:],cand)
        # need running max for row[j-1]
        row[1:]=np.maximum.accumulate(r)
    # backtrack
    pairs={}; i,j=n,m
    while i>0 and j>0:
        xv=xe[i-1][3]; a=ae[j-1][2]
        if (eq(xv,a,None) or eq(-xv,a,None)) and D[i,j]==D[i-1,j-1]+1:
            pairs[i-1]=j-1; i-=1; j-=1
        elif D[i-1,j]>=D[i,j-1]: i-=1
        else: j-=1
    return pairs

CALLEE=set(['w%d'%i for i in range(19,29)])
def arm_events2(a0,a1):
    """arm_events plus uses of callee-saved registers that hold one constant
    for the whole function (hoisted by the recompiler)."""
    ins=list(ca.disasm(M.text[a0:a1],a0))
    writes=collections.defaultdict(list)
    for i in ins:
        rr,ww=i.regs_access()
        for r in ww:
            n=i.reg_name(r).replace('x','w')
            writes[n].append(i)
    const={}
    for r in CALLEE:
        ws=writes.get(r,[])
        vals=set()
        ok=bool(ws)
        for i in ws:
            ops=i.operands
            if i.mnemonic in ('mov','movz') and len(ops)==2 and ops[1].type==A64.ARM64_OP_IMM:
                vals.add(s32(ops[1].imm<<(ops[1].shift.value or 0)))
            elif i.mnemonic in ('movk',):
                ok=False  # 32-bit constants: handled as 'mov' pair; keep simple
            elif i.mnemonic in ('ldp','stp','ldr') and i.address in (ins[0].address,):
                ok=False
            else:
                # prologue/epilogue saves are stp/ldp of x-regs: ldp writes -> restore at exit is fine
                if i.mnemonic=='ldp' : continue
                ok=False
        if ok and len(vals)==1: const[r]=vals.pop()
    _,ev=arm_events(a0,a1)
    extra=[]
    for i in ins:
        if i.mnemonic in ('mov','movz','movk','stp','ldp'): continue
        rr,ww=i.regs_access()
        rd=[i.reg_name(r).replace('x','w') for r in rr]
        for r in rd:
            if r in const:
                extra.append([i.address,'ruse:'+i.mnemonic,const[r],[i.address],r])
    allev=sorted([e+[None] if len(e)==4 else e for e in ev]+extra,key=lambda e:e[0])
    return ins,allev,const
