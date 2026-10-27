"""tools/esui/port/survey_all.py

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
import sys, os, io, contextlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import esui_port as P, fns
if os.environ.get('TRACK_STACK'): P.D.TRACK_STACK[0] = True
for x in sys.argv[1].split(','):
    fn = int(x, 16)
    cfg = P.cfg_for(fn)
    pokes = [None] + P.party_pokes() + cfg.get('pokes', []) + ([] if cfg.get('no_random') else P.random_pokes(fn, n=12, extra=cfg.get('poke_extra', ()))[1:])
    try:
        pairs, tgt, stats = P.sample(fn, cfg, seeds=range(2), argsets=cfg.get('argsets', P.ARGSETS[:4]), pokes=pokes)
        sites, bad, stores = P.fit_cfg(cfg, pairs, tgt)
        edits, end = P.build_all(cfg, sites, 0xc2d220, stores)
        xs, xn = P.exes(cfg)
        fs, fe = P.M.containing(fn)
        import capstone
        cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        starts = [i.address for i in cs.disasm(P.EXE[P.OFF(fs):P.OFF(fe)], fs)]
        watch = sorted({max(s for s in starts if s <= va) for va in range(fs, fe) if P.EXE[P.OFF(va)] != xn[P.OFF(va)]})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            nb = P.verify2(fn, cfg, edits, seeds=range(2), argsets=cfg.get('argsets', P.ARGSETS[:4]), watch=watch, pokes=pokes, verbose=400)
        out = buf.getvalue().strip().splitlines()
        print('%x native=%s stats=%s sites=%d stores=%d words=%d badargs=%s' % (fn, [n if isinstance(n, tuple) else hex(n) for n in cfg['native']], dict(stats), len(sites), len(stores), (end - 0xc2d220) // 4,
              {(hex(k) if isinstance(k, int) else (k[0], hex(k[1]))): [j for j, _ in v] for k, v in bad.items()}))
        for l in out: print('    ' + l)
    except Exception as e:
        import traceback; print('%x ERROR %s' % (fn, e)); traceback.print_exc()
    sys.stdout.flush()
