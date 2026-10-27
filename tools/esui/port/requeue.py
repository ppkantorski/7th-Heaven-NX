"""move failing results of the given fns to ports_var_stale so vrun reruns them

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
import json, os, re, sys, shutil
S = os.path.dirname(os.path.abspath(__file__))
v = json.load(open(os.path.join(S, 'variants.json')))
n = 0
for fn in sys.argv[1:]:
    for x in v[fn]:
        b = os.path.join(S, 'ports_var', '%s_%s' % (fn, x['hash']))
        if not os.path.exists(b + '.txt'):
            continue
        m = re.findall(r'(\d+)/(\d+) runs differ', open(b + '.txt').read())
        if m and m[-1][0] == '0':
            continue
        for ext in ('.txt', '.py', '.err'):
            if os.path.exists(b + ext):
                shutil.move(b + ext, os.path.join(S, 'ports_var_stale', os.path.basename(b) + ext))
        n += 1
print('requeued', n)
