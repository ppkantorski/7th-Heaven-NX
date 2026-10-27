"""one function's port -> ports_out/<fn>.py (repr); merged by genmerge.py

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
import sys, os, struct, pprint
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import esui_port as P, fns
if os.environ.get('TRACK_STACK'): P.D.TRACK_STACK[0] = True
fn = int(sys.argv[1], 16)
cfg = P.cfg_for(fn)
pokes = [None] + P.party_pokes() + cfg.get('pokes', []) + ([] if cfg.get('no_random') else P.random_pokes(fn, n=12, extra=cfg.get('poke_extra', ()))[1:])
pairs, tgt, st = P.sample(fn, cfg, seeds=range(2), argsets=cfg.get('argsets', P.ARGSETS[:4]), pokes=pokes)
sites, bad, stores = P.fit_cfg(cfg, pairs, tgt)
words = {va: (struct.unpack_from('<I', P.M.img, va)[0], new) for va, new in cfg.get('arm_struct', {}).items()}
posts = cfg.get('post', {})
port = {
    'words': words,
    'sites': {s: (c, {j: tuple(f) for j, f in tr.items()}, [tuple(p) for p in posts.get(s, [])]) for s, (c, tr) in sites.items()},
    'hooks': [(s, [tuple(o) for o in ops]) for s, ops in cfg.get('hooks', [])],
    'replace': [(s, list(w)) for s, w in cfg.get('replace', [])],
    'skip_calls': list(cfg.get('skip_calls', [])),
    'store_const': list(cfg.get('store_const', [])),
    'stores': {w: tuple(f) for w, f in sorted(stores.items())},
}
os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ports_out'), exist_ok=True)
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ports_out', '%x.py' % fn), 'w').write(repr(port))
print('%x: %d words, %d sites, %d stores, bad %s' % (fn, len(words), len(sites), len(stores), {str(k): [j for j, _ in v] for k, v in bad.items()}))
