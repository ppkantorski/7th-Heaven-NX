"""per port function: distinct ESUI byte-variants of its code over every option combo.
writes variants.json {fn: [{'hash','settings','folders'}...]} (first = Patrick's settings)

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
import sys, json, itertools, re, os, glob, hashlib
sys.path.insert(0, '.')
import iro, exe_patch
SC = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SC)
man = iro.Manifest('tools/esui/mod.xml')
low = lambda f: f.replace('\\', '/').lower()
conds = {low(f): c for f, c in man.folders}
decl = {}
for i, (f, c) in enumerate(man.folders): decl.setdefault(low(f), i)
opts = {o.id: [v for v, _ in o.values] for o in man.options}
opts['ECHOS'] = sorted(set(opts.get('ECHOS', [])) | {0, 1, 2})
opts['ffnx_aspect_ratio'] = [0]
base = json.load(open('/Volumes/NVME/Users/ppkantorski/Downloads/ff7_mods/7th_heaven_nx/settings.json'))['Enhanced Stock UI-rel2.iro']['options']
base.setdefault('ffnx_aspect_ratio', 0)
HEXT = {}
for f, c in man.folders:
    ps = sorted(glob.glob('tools/esui/x/' + f.replace('\\', '/') + '/hext/*.txt'))
    if ps:
        HEXT[low(f)] = [w for p in ps for w in exe_patch.parse_hext(open(p, encoding='utf-8', errors='replace').read())]
import nxmap
EXE = open('dump/romfs/ff7/resources/ff7_1.02/ff7_en', 'rb').read()
PE = exe_patch.parse_pe(EXE)
import esui_port as P
fnf = {int(k, 16): v for k, v in json.load(open(os.path.join(SC, 'fn_folders.json'))).items()}
want = [int(x, 16) for x in sys.argv[1:]] or sorted(fnf)
out = {}
tot = 0
for fn in want:
    fl = fnf.get(fn, ['EnhancedStock'])
    fs, fe = P.M.containing(fn)
    ids = sorted({i for f in fl for i in re.findall(r'(\w+)\s*=', str(conds.get(low(f), '')))} - {'enhanced'})
    seen = {}
    combos = [dict(base)] + [dict(base, **dict(zip(ids, p))) for p in itertools.product(*[opts[i] for i in ids])]
    for s in combos:
        act = sorted((low(f) for f, c in man.folders if low(f) in HEXT and iro.evaluate(c, s)), key=lambda f: decl[f])
        act = list(dict.fromkeys(act))
        b = {}
        for f in act:
            for va, by in HEXT[f]:
                for k, x in enumerate(by):
                    if fs <= va + k < fe: b[va + k] = x
        h = hashlib.sha1(repr(sorted(b.items())).encode()).hexdigest()[:10]
        if h not in seen:
            seen[h] = {'hash': h, 'settings': {i: s[i] for i in ids}, 'folders': act, 'nbytes': len(b)}
    out['%x' % fn] = list(seen.values())
    tot += len(seen)
    if len(seen) > 1: print('%x' % fn, len(seen), ids, flush=True)
json.dump(out, open(os.path.join(SC, 'variants.json'), 'w'), indent=0)
print('total variants', tot, 'fns', len(out))
