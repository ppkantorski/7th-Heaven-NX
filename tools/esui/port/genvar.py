"""genvar.py fn hash : fit + verify one function under the ESUI folders in $ESUI_FOLDERS;
writes ports_var/<fn>_<hash>.py and prints one summary line

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
import sys, os, io, struct, contextlib
SC = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SC)
import esui_port as P, fns
if os.environ.get('TRACK_STACK'): P.D.TRACK_STACK[0] = True
fn = int(sys.argv[1], 16); h = sys.argv[2]
cfg = P.cfg_for(fn)
pokes = [None] + P.party_pokes() + cfg.get('pokes', []) + ([] if cfg.get('no_random') else P.random_pokes(fn, n=12, extra=cfg.get('poke_extra', ()))[1:])
args = cfg.get('argsets', P.ARGSETS[:4])
pairs, tgt, st = P.sample(fn, cfg, seeds=range(2), argsets=args, pokes=pokes)
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
edits, end = P.build_all(cfg, sites, 0xc2d220, stores)
xs, xn = P.exes(cfg)
fs, fe = P.M.containing(fn)
import capstone
cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
starts = [i.address for i in cs.disasm(P.EXE[P.OFF(fs):P.OFF(fe)], fs)]
watch = sorted({max(s for s in starts if s <= va) for va in range(fs, fe) if P.EXE[P.OFF(va)] != xn[P.OFF(va)]})
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    nb = P.verify2(fn, cfg, edits, seeds=range(2), argsets=args, watch=watch, pokes=pokes, verbose=40)
summ = [l for l in buf.getvalue().splitlines() if 'runs differ' in l]
od = os.path.join(SC, 'ports_var'); os.makedirs(od, exist_ok=True)
open(os.path.join(od, '%x_%s.py' % (fn, h)), 'w').write(repr(port))
open(os.path.join(od, '%x_%s.txt' % (fn, h)), 'w').write(buf.getvalue())
print('%x %s: bad %s | %s' % (fn, h, {str(k): [j for j, _ in v] for k, v in bad.items()}, summ[-1].strip() if summ else 'NO SUMMARY'))
