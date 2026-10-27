"""tools/esui/calls.py

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
from xlate import *
TR=0x10FC3A0
def x86_calls(fs,fe,buf):
    o=off(fs); ins=list(cx.disasm(buf[o:o+fe-fs],fs)); out=[]
    for k,i in enumerate(ins):
        if i.mnemonic=='call' and i.operands[0].type==X.X86_OP_IMM:
            args=[]; j=k-1
            while j>=0 and len(args)<16:
                p=ins[j]
                if p.mnemonic=='push':
                    op=p.operands[0]
                    args.append((p.address, s32(op.imm) if op.type==X.X86_OP_IMM else None))
                elif p.mnemonic in ('call','ret') or p.mnemonic.startswith('j'): break
                j-=1
            out.append((i.address,i.operands[0].imm,args))
    return out
def arm_calls(a0,a1):
    ins=list(ca.disasm(M.text[a0:a1],a0)); out=[]
    regval={}
    stores=[]   # (addr, value or None) stores to [x0] right after translate
    last_tr=None
    for k,i in enumerate(ins):
        m=i.mnemonic; ops=i.operands
        if m in ('mov','movz') and len(ops)==2 and ops[1].type==A64.ARM64_OP_IMM:
            regval[i.reg_name(ops[0].reg)]=(s32(ops[1].imm<<(ops[1].shift.value or 0)),i.address)
        elif m=='movk' and len(ops)==2:
            r=i.reg_name(ops[0].reg)
            if r in regval:
                v,a=regval[r]; sh=ops[1].shift.value or 0; regval[r]=(s32((v&~(0xffff<<sh))|(ops[1].imm<<sh)),a)
        if m=='bl':
            t=ops[0].imm
            if t==TR: last_tr=i.address; continue
            out.append((i.address,t,list(reversed(stores)))); stores=[]; regval={}
            continue
        if m=='str' and last_tr is not None and len(ops)==2 and ops[1].type==A64.ARM64_OP_MEM and i.reg_name(ops[1].mem.base)=='x0' and ops[1].mem.disp==0:
            r=i.reg_name(ops[0].reg)
            if r in ('wzr','xzr'): stores.append((i.address,0,None))
            elif r in regval: stores.append((i.address,regval[r][0],regval[r][1]))
            else: stores.append((i.address,None,None))
            last_tr=None
        if m in ('b','ret') : stores=[] if m=='ret' else stores
    return out
