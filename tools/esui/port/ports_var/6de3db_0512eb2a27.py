import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {13900652: (285372726, 285245750), 13901620: (285571350, 285604118), 13902400: (285571350, 285604118), 13902496: (285372694, 285245718), 13902816: (285571350, 285604118), 13902908: (285372726, 285245750), 13903204: (1358979360, 1358977312), 13903328: (285571350, 285604118), 13903420: (285372726, 285245750), 13903808: (285575446, 285610262), 13903928: (285368598, 285242646), 13900272: (1384123161, 1384122649), 13900284: (840763388, 1384121052)}, 'sites': {13903968: (7306235, {6: ('add', 5)}, [])}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}