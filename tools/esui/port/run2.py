"""tools/esui/port/run2.py

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
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import esui_port as P, fns
import check as C, align as AL
fn = int(sys.argv[1], 16); cfg = P.cfg_for(fn)
pokes = P.random_pokes(fn, n=int(sys.argv[2]) if len(sys.argv) > 2 else 24, extra=cfg.get('poke_extra', ())) + cfg.get('pokes', []) + P.party_pokes()
pairs, tgt, stats = P.sample(fn, cfg, seeds=range(3), pokes=pokes)
sites, bad = P.fit_all(pairs, tgt)
BASE = 0xc2d1e0 + 0x40          # dead body of x86 0x6AF19C
edits, end = P.build_all(cfg, sites, BASE)
print('sites %d  cave words %d  bad %s' % (len(sites), (end - BASE) // 4, {hex(k): [j for j, _ in v] for k, v in bad.items()}))
xs, xn = P.exes(cfg)
AL.build_new = lambda pre: xn
# x86 instructions ESUI changes in this function = watch list
fs, fe = P.M.containing(fn)
watch = [va for va in range(fs, fe) if P.EXE[P.OFF(va)] != xn[P.OFF(va)]]
import capstone
cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
starts = [i.address for i in cs.disasm(P.EXE[P.OFF(fs):P.OFF(fe)], fs)]
ws = sorted({max(s for s in starts if s <= va) for va in watch})
P.verify2(fn, cfg, edits, seeds=range(2), argsets=P.ARGSETS[:4], watch=ws, pokes=pokes)
json.dump({'%x' % k: v for k, v in edits.items()}, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'edits_%x.json' % fn), 'w'))
