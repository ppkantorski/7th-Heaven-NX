import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {13283228: (285337879, 285341975), 13283840: (285345047, 285344023)}, 'sites': {13280640: (7297795, {1: ('affd', 11, 12, 124, 124), 3: ('add', 3)}, []), 13282424: (7297795, {1: ('affd', 11, 12, 124, 124)}, []), 13283928: (7254968, {1: ('affd', 11, 12, 128, 128)}, [])}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}