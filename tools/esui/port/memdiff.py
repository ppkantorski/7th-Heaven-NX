"""tools/esui/port/memdiff.py

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
import sys, collections; S='/private/tmp/claude-501/-Volumes-NVME-Users-ppkantorski-Downloads-ff7-mods-Claude/03970759-279c-4189-a1cb-4b17a35998c6/scratchpad'
sys.path.insert(0,S); import esui_port as P
D=P.D; fn=int(sys.argv[1],16); addrs=[int(x,16) for x in sys.argv[2].split(',')]; key=[int(x,16) for x in sys.argv[3].split(',')] if len(sys.argv)>3 else []
cfg=P.cfg_for(fn); xs,xn=P.exes(cfg)
pokes = [None] + P.party_pokes() + cfg.get('pokes', []) + ([] if cfg.get('no_random') else P.random_pokes(fn, n=12, extra=cfg.get('poke_extra', ()))[1:])
pairs, tgt, stats = P.sample(fn, cfg, seeds=range(2), argsets=cfg.get('argsets',P.ARGSETS[:4]), pokes=pokes)
sites,bad,stores=P.fit_cfg(cfg,pairs,tgt); edits,end=P.build_all(cfg,sites,0xc2d220,stores)
D.NATIVE=set(cfg.get('native',()))|set(cfg.get('native_both',())); D.NATIVE_ARM=set(cfg.get('native_both',()))
seen=collections.Counter()
for seed in range(2):
  D.RETMODE[0]=seed
  for args in cfg.get('argsets',P.ARGSETS[:4]):
    for pk in pokes:
      D.POKES=pk or {}
      c1,w1,s1=D.run_x86(P.dyn(cfg,xn,pk),fn,seed,args)
      d1,v1,r1=D.run_arm(edits,P.DATA_NEW,fn,seed,args)
      for a in addrs:
        if w1.get(a)!=v1.get(a): seen[(tuple((pk or {}).get(k) for k in key), hex(a), w1.get(a), v1.get(a))]+=1
for k,v in sorted(seen.items(), key=str)[:40]: print(v,k)
