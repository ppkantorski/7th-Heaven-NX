"""tools/esui/port/vports.py

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
import sys, os, struct, io, contextlib
sys.path.insert(0, '.'); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import esui_port as P, ff7nx_esuicave as EC, ff7nx_esui_ports as PP, capstone
screen = sys.argv[1]
rw = lambda va: struct.unpack_from('<I', P.M.img, va)[0]
lo, hi = P.M.extent(EC.DEAD_X86); at = lo + EC.DEAD_GUARD
for fn, port in sorted(PP.PORTS[screen].items()):
    ed, at2 = EC.build(port, rw, P.M.x86_to_arm, at); at = at2
    cfg = P.cfg_for(fn)
    pokes = [None] + P.party_pokes() + cfg.get('pokes', []) + ([] if cfg.get('no_random') else P.random_pokes(fn, n=12, extra=cfg.get('poke_extra', ()))[1:])
    fs, fe = P.M.containing(fn); xs, xn = P.exes(cfg)
    cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    starts = [i.address for i in cs.disasm(P.EXE[P.OFF(fs):P.OFF(fe)], fs)]
    watch = sorted({max(s for s in starts if s <= va) for va in range(fs, fe) if P.EXE[P.OFF(va)] != xn[P.OFF(va)]})
    P.sample(fn, cfg, seeds=range(2), argsets=cfg.get('argsets', P.ARGSETS[:4]), pokes=pokes)   # the port's own noise
    b = io.StringIO()
    with contextlib.redirect_stdout(b):
        P.verify2(fn, cfg, ed, seeds=range(2), argsets=cfg.get('argsets', P.ARGSETS[:4]), watch=watch, pokes=pokes, verbose=1)
    print(b.getvalue().strip().splitlines()[-1] if 'struct edits' not in b.getvalue().splitlines()[-1] else b.getvalue().strip().splitlines()[-2])
print('cave bytes', at - lo)
