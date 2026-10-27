import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {13907408: (1384135560, 1384130408), 13907428: (1384131656, 1384132360), 13907448: (1384120968, 1384120584), 13908784: (285581624, 285600056), 13908832: (285255960, 285251864), 13909656: (285585689, 285606169), 13909704: (285362457, 285241625), 13907916: (1384125331, 1384123411)}, 'sites': {13909844: (7208037, {0: ('add', -198), 1: ('add', 22)}, [])}, 'hooks': [], 'replace': [(13907844, [1384120467, 445843059]), (13908776, [185074952, 1394438408]), (13909644, [185074952, 1394438408])], 'skip_calls': [], 'store_const': [], 'stores': {13908856: ('map', ())}}