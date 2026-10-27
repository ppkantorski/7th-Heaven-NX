import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {14611816: (285223192, 285224216), 14612368: (285427989, 285421845), 14612464: (1384120648, 1384120680), 14612552: (1384140104, 1384140264), 14612580: (1384120872, 1384120712), 14613352: (285427989, 285422869), 14611856: (1384121176, 1384121112)}, 'sites': {14612428: (7297795, {0: ('add', 17)}, []), 14613412: (7306235, {0: ('add', 17)}, [])}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {14612568: ('add', -1)}}