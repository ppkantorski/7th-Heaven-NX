"""list every call/jmp from patched code into the ESUI data-area caves, and the cave code

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
import sys, os, collections
sys.path.insert(0, '.'); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import newexe, exe_patch, capstone
NEW = newexe.build(); pe = exe_patch.parse_pe(NEW)
off = lambda va: exe_patch.va_to_offset(pe, va)
cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
want = sys.argv[1].split(',')
sites = collections.defaultdict(list)
for f, p in newexe.files():
    if not any(w in f + '/' + os.path.basename(p) for w in want): continue
    for va, b in exe_patch.parse_hext(open(p, encoding='utf-8', errors='replace').read()):
        if 0x401000 <= va < 0x7B562B:
            for i in cs.disasm(NEW[off(va):off(va) + len(b) + 8], va):
                if i.mnemonic in ('call', 'jmp') and i.op_str.startswith('0x'):
                    t = int(i.op_str, 16)
                    if 0x900000 <= t < 0xA00000: sites[t].append((i.address, i.mnemonic, f))
                if i.address >= va + len(b): break
for t in sorted(sites):
    print('CAVE %x  from %s' % (t, ', '.join('%x %s' % (a, m) for a, m, f in sites[t])))
    for k, i in enumerate(cs.disasm(NEW[off(t):off(t) + 400], t)):
        print('    %x  %s %s' % (i.address, i.mnemonic, i.op_str))
        if i.mnemonic in ('ret', 'jmp') or k > 60: break
