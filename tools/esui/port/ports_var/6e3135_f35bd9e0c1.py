import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {13934288: (285544723, 285561107)}, 'sites': {13933328: (7304711, {1: ('add', 16)}, [])}, 'hooks': [(13933192, [('st8', 9552707, 2)])], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}