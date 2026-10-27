"""tools/esui/port/badinfo.py

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
import sys; S='/private/tmp/claude-501/-Volumes-NVME-Users-ppkantorski-Downloads-ff7-mods-Claude/03970759-279c-4189-a1cb-4b17a35998c6/scratchpad'
sys.path.insert(0,S); import esui_port as P
fn=int(sys.argv[1],16); cfg=P.cfg_for(fn)
pokes = [None] + P.party_pokes() + cfg.get('pokes', []) + ([] if cfg.get('no_random') else P.random_pokes(fn, n=12, extra=cfg.get('poke_extra', ()))[1:])
pairs, tgt, stats = P.sample(fn, cfg, seeds=range(2), argsets=cfg.get('argsets',P.ARGSETS[:4]), pokes=pokes)
sites,bad,stores=P.fit_cfg(cfg,pairs,tgt)
for k,v in bad.items():
    print(k if isinstance(k,tuple) else hex(k), [(j, d) for j,d in v])
    if not isinstance(k,tuple):
        for j,_ in v:
            for w in P.SJW[(k,j)]: print('   writer', hex(w) if w else w, sorted(set(P.WPAIRS[w])))
