import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {14114288: (285219095, 1358956823), 14115324: (1358965015, 1358955799), 14116092: (1358965017, 1358955801), 14116660: (285274391, 285275415), 14116704: (285251863, 285256983), 14117656: (285298967, 285297943), 14117700: (285251863, 285256983), 14117940: (285298967, 285296919), 14117420: (285298967, 285297943), 14118508: (285264147, 285255955), 14118788: (285255955, 285247763)}, 'sites': {14117028: (7306235, {0: ('add', -5), 1: ('add', 1)}, []), 14118024: (7306235, {0: ('add', -5)}, []), 14118828: (7306235, {6: ('add', 6)}, [])}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}