"""verify the ported screens against the module apply_to_nso actually writes

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
import sys, os, io, contextlib, types
S = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, '.'); sys.path.insert(0, S)
import esui_port as P, ff7nx_esui as E, ff7nx_esui_ports as PP, capstone, struct
E.CURRENT = {'mod': types.SimpleNamespace(path='x'), 'folders': []}
built = os.path.join(S, 'built_main')
assert E.apply_to_nso('dump/exefs/main', built, print)
class Built(dict): pass
orig = P.D.run_arm
def run_arm(edits, *a, **k):
    if isinstance(edits, Built): return orig({}, *a, module=built, **k)
    return orig(edits, *a, **k)
P.D.run_arm = run_arm
bimg = P.D.module_image(built)
for screen in sys.argv[1].split(','):
    for fn in sorted(PP.PORTS[screen]):
        cfg = P.cfg_for(fn)
        pokes = [None] + P.party_pokes() + cfg.get('pokes', []) + ([] if cfg.get('no_random') else P.random_pokes(fn, n=12, extra=cfg.get('poke_extra', ()))[1:])
        P.sample(fn, cfg, seeds=range(2), argsets=cfg.get('argsets', P.ARGSETS[:4]), pokes=pokes)
        fs, fe = P.M.containing(fn); xs, xn = P.exes(cfg)
        cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        starts = [i.address for i in cs.disasm(P.EXE[P.OFF(fs):P.OFF(fe)], fs)]
        watch = sorted({max(s for s in starts if s <= va) for va in range(fs, fe) if P.EXE[P.OFF(va)] != xn[P.OFF(va)]})
        b = io.StringIO()
        with contextlib.redirect_stdout(b):
            P.verify2(fn, cfg, Built(), seeds=range(2), argsets=cfg.get('argsets', P.ARGSETS[:4]), watch=watch, pokes=pokes, verbose=3)
        print(screen, [l for l in b.getvalue().splitlines() if l.startswith('fn ')][0][:90])
        for l in b.getvalue().splitlines()[:6]:
            if not l.startswith('fn '): print('   ', l)
