"""tools/esui/port/where.py

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
import sys,bisect
sys.path.insert(0,'.')
import nxmap
M=nxmap.Main(sys.argv[1])
inv=sorted((v,k) for k,v in M.x86_to_arm.items())
av=[v for v,k in inv]
print('lowest arm fn', hex(av[0]), 'highest', hex(av[-1]))
for x in sys.argv[2:]:
    a=int(x,16); i=bisect.bisect_right(av,a)-1
    print(hex(a), '-> x86 fn', hex(inv[i][1]) if i>=0 else None, 'arm start', hex(inv[i][0]) if i>=0 else None)
