"""tools/esui/inv.py

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
import sys,collections; sys.path.insert(0,'.')
exec(open('tools/esui/reach.py').read().split("# recursive descent")[0])
mn=collections.Counter(); per=collections.Counter(); fx=collections.defaultdict(int)
for fs in funs:
    s,e=M.containing(fs); seen=set(); work=[s]
    while work:
        a=work.pop()
        while a not in seen and s<=a<e or (a not in seen and a>=0x900000):
            seen.add(a); o=off(a); ins=next(cx.disasm(new[o:o+16],a),None)
            if ins is None: break
            m=ins.mnemonic; mn[m]+=1
            if m.startswith('f'): fx[s]+=1
            ops=ins.operands
            if m.startswith('j'):
                if ops[0].type==X.X86_OP_IMM: work.append(ops[0].imm)
                else:
                    # jump table
                    mem=ops[0].mem; t=mem.disp
                    for k in range(64):
                        v=int.from_bytes(new[off(t+4*k):off(t+4*k)+4],'little')
                        if s<=v<e: work.append(v)
                        else: break
                if m=='jmp': break
            if m in ('ret','retn'): break
            a+=ins.size
print(sorted(mn.items(),key=lambda x:-x[1]))
print('x87 functions',len(fx),sorted(((v,hex(k)) for k,v in fx.items()),reverse=True))
