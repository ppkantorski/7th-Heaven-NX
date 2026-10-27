import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {}, 'sites': {14622984: (7297795, {0: ('add', 8)}, []), 14623244: (7297795, {0: ('add', 8)}, []), 14623404: (7297795, {0: ('add', 13)}, []), 14623548: (7297795, {0: ('add', 13)}, []), 14623700: (7297795, {1: ('add', -3)}, []), 14623884: (7297795, {1: ('add', -4)}, [])}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}