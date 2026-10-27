import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {13930388: (285479227, 285498683)}, 'sites': {13928512: (7304711, {1: ('add', 13), 3: ('add', 6)}, [])}, 'hooks': [(13928324, [('st8', 9552707, 2)])], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}