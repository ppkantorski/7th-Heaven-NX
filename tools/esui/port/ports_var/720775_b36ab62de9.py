import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {14941460: (285226267, 285232411), 14941088: (1384123130, 1384123354), 14941508: (285250843, 285254939), 14941768: (285274388, 285269268), 14941960: (285230362, 285231386), 14942004: (285333786, 285329690), 14942212: (285228314, 285229338), 14942256: (285311258, 285297946), 14942464: (285228313, 285229337), 14942508: (285346073, 285337881), 14942688: (285259033, 285260057), 14942732: (285246745, 285247769), 14942856: (285270292, 285264148), 14943024: (285230356, 285233428), 14942296: (2549726230, 2549780338), 13627944: (1385154515, 1386074067), 13627948: (1923088563, 1923090131)}, 'sites': {14942296: (7313209, {3: ('add', 1)}, []), 14943104: (7297795, {0: ('add', 8)}, [])}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}