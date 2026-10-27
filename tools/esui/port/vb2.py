"""vb2.py fn : verify one ported fn on the BUILT module + BUILT ff7_en (ARM) vs x86 ESUI (Patrick's folders)

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
S = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, S); sys.path.insert(0, '.')
import esui_port as P, capstone
built = os.path.join(S, 'built_main')
P.DATA_NEW = open(os.path.join(S, 'built_en'), 'rb').read()
class Built(dict): pass
orig = P.D.run_arm
def run_arm(edits, *a, **k):
    if isinstance(edits, Built): return orig({}, *a, module=built, **k)
    return orig(edits, *a, **k)
P.D.run_arm = run_arm
fn = int(sys.argv[1], 16)
cfg = P.cfg_for(fn)
pokes = [None] + P.party_pokes() + cfg.get('pokes', []) + ([] if cfg.get('no_random') else P.random_pokes(fn, n=12, extra=cfg.get('poke_extra', ()))[1:])
P.sample(fn, cfg, seeds=range(2), argsets=cfg.get('argsets', P.ARGSETS[:4]), pokes=pokes)
fs, fe = P.M.containing(fn); xs, xn = P.exes(cfg)
cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
starts = [i.address for i in cs.disasm(P.EXE[P.OFF(fs):P.OFF(fe)], fs)]
watch = sorted({max(s for s in starts if s <= va) for va in range(fs, fe) if P.EXE[P.OFF(va)] != xn[P.OFF(va)]})
b = io.StringIO()
with contextlib.redirect_stdout(b):
    P.verify2(fn, cfg, Built(), seeds=range(2), argsets=cfg.get('argsets', P.ARGSETS[:4]), watch=watch, pokes=pokes, verbose=60)
open(os.path.join(S, 'vb2', '%x.txt' % fn), 'w').write(b.getvalue())
print([l for l in b.getvalue().splitlines() if 'runs differ' in l][-1][:110])
