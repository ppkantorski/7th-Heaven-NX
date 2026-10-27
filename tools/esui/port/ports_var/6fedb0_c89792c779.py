import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {14372140: (1384123242, 1384123306), 14379708: (285568275, 285605139)}, 'sites': {14372208: (7254968, {0: ('add', 4)}, []), 14372340: (7297795, {0: ('add', 3)}, []), 14372824: (7297795, {1: ('add', -2)}, []), 14375312: (7240609, {1: ('add', 5)}, []), 14378104: (7254968, {0: ('add', -8), 1: ('add', -10)}, []), 14379392: (7297795, {0: ('add', 3)}, []), 14379520: (7297795, {0: ('add', 9)}, [])}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}