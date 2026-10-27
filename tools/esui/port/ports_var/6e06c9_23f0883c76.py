import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {13912124: (1384135560, 1384135592), 13912144: (1384131656, 1384132360), 13913148: (285581592, 285600024)}, 'sites': {13913208: (7297795, {0: ('add', -64)}, []), 13913312: (7208037, {0: ('add', -1), 1: ('add', 22)}, [])}, 'hooks': [], 'replace': [(13912408, [1384120467, 445843059]), (13913136, [185074952, 1394438408])], 'skip_calls': [], 'store_const': [], 'stores': {}}