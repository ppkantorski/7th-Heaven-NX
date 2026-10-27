"""tools/esui/port/pwords.py

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
import ast, sys, capstone, struct
cs = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
p = ast.literal_eval(open(sys.argv[1]).read())
for va, (o, n) in sorted(p['words'].items()):
    a = next(cs.disasm(struct.pack('<I', o), va)); b = next(cs.disasm(struct.pack('<I', n), va))
    print('%x  %s %s  ->  %s %s' % (va, a.mnemonic, a.op_str, b.mnemonic, b.op_str))
for s, w in p.get('replace', []):
    print('replace %x' % s, [next(cs.disasm(struct.pack('<I', x), s)).op_str for x in w])
