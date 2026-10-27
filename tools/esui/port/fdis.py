"""full ARM listing of a mapped function, with callee names (x86) annotated

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
import sys, struct; sys.path.insert(0, '.')
import nxmap, capstone
M = nxmap.Main(sys.argv[1]); A2X = {v: k for k, v in M.x86_to_arm.items()}
fn = int(sys.argv[2], 16); a0, a1 = M.extent(fn)
md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
for i in md.disasm(M.img[a0:a1], a0):
    s = '%8x  %-8s %s' % (i.address, i.mnemonic, i.op_str)
    if i.mnemonic in ('bl', 'b') and i.op_str.startswith('#'):
        t = int(i.op_str[1:], 16)
        if t in A2X: s += '    ; x86 %x' % A2X[t]
        elif t == 0x10fc3a0: s = s.replace('bl       #0x10fc3a0', 'bl T')
        elif t == 0xa550: s = s.replace('bl       #0xa550', 'bl Y')
    print(s)
