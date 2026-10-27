"""tools/probes/nxdis.py

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
import struct, sys, lz4.block
from capstone import *

def nso_text(path):
    d=open(path,'rb').read()
    segs=[struct.unpack('<III',d[b:b+12]) for b in (0x10,0x20,0x30)]
    comp=struct.unpack('<III',d[0x60:0x6c]); flags=struct.unpack('<I',d[0xc:0x10])[0]
    raw=[]
    for i,(fo,mo,ds) in enumerate(segs):
        blob=d[fo:fo+comp[i]]
        raw.append(lz4.block.decompress(blob,uncompressed_size=ds) if flags&(1<<i) else blob[:ds])
    return raw, segs, d

raw,segs,d = nso_text(sys.argv[3] if len(sys.argv)>3 else 'dump/exefs/main')
text=raw[0]
md=Cs(CS_ARCH_ARM64,CS_MODE_LITTLE_ENDIAN)
start=int(sys.argv[1],16); n=int(sys.argv[2]) if len(sys.argv)>2 else 80
for i in md.disasm(text[start:start+4*n], start):
    print("+%08X  %08X  %-10s %s" % (i.address, struct.unpack('<I',bytes(i.bytes))[0], i.mnemonic, i.op_str))
