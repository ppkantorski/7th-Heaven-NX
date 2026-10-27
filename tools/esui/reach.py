"""tools/esui/reach.py

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
import sys,pickle,collections; sys.path.insert(0,'.')
import exe_patch, capstone, nxmap
from capstone import x86 as X
exe=open('dump/romfs/ff7/resources/ff7_1.02/ff7_en','rb').read(); new=open('tools/esui/ff7_en.esui','rb').read()
pe=exe_patch.parse_pe(exe); off=lambda va: exe_patch.va_to_offset(pe,va)
M=nxmap.Main('dump/exefs/main')
cx=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_32); cx.detail=True
patches=pickle.load(open('tools/esui/patches.pkl','rb'))
funs=sorted({M.containing(va)[0] for _f,_n,va,_l in patches if 0x401000<=va<0x7B562B})
print('functions',len(funs))
# recursive descent from function start in NEW code
out=collections.Counter(); ext=[]; calls_tgt=collections.Counter(); indirect=collections.Counter(); x87=0; unk=collections.Counter()
for fs in funs:
    s,e=M.containing(fs)
    seen=set(); work=[s]; 
    while work:
        a=work.pop()
        while a not in seen:
            seen.add(a)
            o=off(a); ins=next(cx.disasm(new[o:o+16],a),None)
            if ins is None: unk['bad']+=1; break
            m=ins.mnemonic; ops=ins.operands
            if m.startswith('j'):
                if ops[0].type==X.X86_OP_IMM:
                    t=ops[0].imm
                    if not (s<=t<e): ext.append((hex(s),hex(a),m,hex(t),hex(M.containing(t)[0]) if M.containing(t) else None))
                    else: work.append(t)
                else: indirect[('jmp',ins.op_str[:30])]+=1
                if m=='jmp': break
            elif m=='call':
                if ops[0].type==X.X86_OP_IMM: calls_tgt[ops[0].imm in M.x86_to_arm]+=1
                else: indirect[('call',ins.op_str.split('[')[0])]+=1
            elif m in ('ret','retn'): break
            elif m.startswith('f'): x87+=1
            a+=ins.size
print('jumps leaving the function:',len(ext)); 
for r in ext[:40]: print(r)
print('direct call targets mapped?',calls_tgt); print('indirect',indirect.most_common(10)); print('x87 insns',x87)
