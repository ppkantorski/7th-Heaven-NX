import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {14966576: (285567253, 285605141), 14960436: (1384123241, 1384123401)}, 'sites': {14957272: (7297795, {0: ('add', 1), 1: ('add', -3)}, []), 14960640: (7297795, {0: ('add', 1), 1: ('add', -3)}, []), 14961128: (7297795, {0: ('add', -4), 1: ('add', -1)}, []), 14961784: (7254968, {0: ('add', -8), 1: ('add', -10)}, []), 14964240: (7297795, {0: ('add', 1), 1: ('add', -21)}, []), 14964468: (7240609, {1: ('add', 3), 2: ('add', -19), 3: ('add', 6), 4: ('add', -7)}, []), 14964716: (7254968, {0: ('add', 8), 1: ('add', 4)}, []), 14966268: (7297795, {0: ('add', 1), 1: ('add', -3)}, []), 14966396: (7297795, {0: ('add', 9)}, []), 14966996: (7297795, {0: ('add', 1681)}, []), 14967132: (7240609, {1: ('add', 1708)}, []), 14967332: (7240609, {4: ('add', -7)}, [])}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}