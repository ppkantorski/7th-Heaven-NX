"""find ARM sites that build 32-bit constants (movz/movk or orr) equal to targets

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
import sys,struct
sys.path.insert(0,'.')
import nxmap
M=nxmap.Main(sys.argv[1]); t=M.text
targets={int(x,16) for x in sys.argv[2:]}
n=len(t)//4
words=struct.unpack('<%dI'%n,t[:n*4])
lo={}  # reg -> (addr,val)
for i,w in enumerate(words):
    a=i*4
    if (w&0x7f800000)==0x52800000: # movz w/x
        hw=(w>>21)&3; imm=(w>>5)&0xffff; rd=w&31
        lo[rd]=(a,imm<<(16*hw))
        if imm<<(16*hw) in targets: print('movz',hex(a),hex(imm<<(16*hw)))
    elif (w&0x7f800000)==0x72800000: # movk
        hw=(w>>21)&3; imm=(w>>5)&0xffff; rd=w&31
        if rd in lo:
            a0,v=lo[rd]; v=(v&~(0xffff<<(16*hw)))|(imm<<(16*hw))
            lo[rd]=(a0,v)
            if v in targets and a-a0<64: print('movk',hex(a0),hex(a),hex(v))
