"""vdiff.py fn hashA hashB : x86 bytes inside fn that differ between two variants (after all HEXT)

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
import sys, json, os, glob
sys.path.insert(0, '.')
import exe_patch, nxmap, capstone
SC = os.path.dirname(os.path.abspath(__file__))
v = json.load(open(os.path.join(SC, 'variants.json')))[sys.argv[1]]
M = nxmap.Main('dump/exefs/main'); fn = int(sys.argv[1], 16); fs, fe = M.containing(fn)
EXE = open('dump/romfs/ff7/resources/ff7_1.02/ff7_en', 'rb').read(); pe = exe_patch.parse_pe(EXE)
def mem(h):
    x = [a for a in v if a['hash'] == h][0]
    b = {}
    for f in x['folders']:
        for p in sorted(glob.glob('tools/esui/x/' + f + '/hext/*.txt')):
            for va, by in exe_patch.parse_hext(open(p, encoding='utf-8', errors='replace').read()):
                for k, c in enumerate(by): b[va + k] = c
    return x, b
xa, a = mem(sys.argv[2]); xb, b = mem(sys.argv[3])
print(xa['settings'], '\n', xb['settings'])
print('folders only A', sorted(set(xa['folders']) - set(xb['folders'])), 'only B', sorted(set(xb['folders']) - set(xa['folders'])))
cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
def img(m):
    buf = bytearray(EXE[exe_patch.va_to_offset(pe, fs):exe_patch.va_to_offset(pe, fe)])
    for va, c in m.items():
        if fs <= va < fe: buf[va - fs] = c
    return bytes(buf)
ia, ib = img(a), img(b)
diffs = [i for i in range(len(ia)) if ia[i] != ib[i]]
print(len(diffs), 'bytes differ')
starts = sorted({i.address for i in cs.disasm(ia, fs)} | {i.address for i in cs.disasm(ib, fs)})
shown = set()
for d in diffs:
    s = max(x for x in starts if x <= fs + d)
    if s in shown: continue
    shown.add(s)
    for nm, im in (('A', ia), ('B', ib)):
        for i in cs.disasm(im[s - fs:s - fs + 16], s):
            print('  %s %x %s %s' % (nm, i.address, i.mnemonic, i.op_str)); break
