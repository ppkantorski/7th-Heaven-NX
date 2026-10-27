"""tools/esui/port/battle_align.py

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
import sys, os, io, contextlib, collections, json
sys.path.insert(0, '.'); sys.path.insert(0, 'tools/esui'); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import newexe, align, exe_patch
NEW = newexe.build()
align.build_new = lambda pre: NEW
stock = align.exe
# functions whose code differs between stock and NEW, restricted by file filter
want = sys.argv[1].split(',')
fns = collections.Counter()
for f, p in newexe.files():
    if not any(w in f + '/' + os.path.basename(p) for w in want): continue
    for va, b in exe_patch.parse_hext(open(p, encoding='utf-8', errors='replace').read()):
        if 0x401000 <= va < 0x7B562B:
            fns[align.M.containing(va)[0]] += 1
out = {}
for fn, n in sorted(fns.items()):
    align.EDITS = []; align.STRUCTS = []; align.GROUPS = {}
    sys.argv = ['align', hex(fn)]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            align.main(); align.resolve_groups()
    except Exception as e:
        print(hex(fn), 'ERR', e); continue
    txt = buf.getvalue()
    st = collections.Counter(e[0].split('(')[0].split('?')[0].split('+')[0] for e in align.EDITS)
    sb = txt.count('STRUCT-BLOCK'); s1 = txt.count('\nSTRUCT ') + txt.startswith('STRUCT ')
    um = txt.count('UNMAPPED')
    print('%x patches %3d  edits %s  struct-blocks %d struct %d unmapped %d' % (fn, n, dict(st), sb, s1, um))
    open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'align_%x.txt' % fn), 'w').write(txt)
    out[hex(fn)] = [list(map(lambda x: x if not isinstance(x, int) else x, e)) for e in align.EDITS]
json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'battle_edits.json'), 'w'))
