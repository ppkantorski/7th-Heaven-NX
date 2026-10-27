import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {14596800: (285550872, 285524248), 14596904: (285550872, 285524248), 14597220: (285550872, 285524248), 14597396: (285546776, 285519128), 14597628: (285548824, 285524248)}, 'sites': {14596864: (7298060, {0: ('add', 19)}, []), 14596968: (7298060, {0: ('add', 19)}, []), 14597284: (7314500, {0: ('add', 18)}, []), 14597456: (7297795, {0: ('add', 37)}, []), 14597692: (7298060, {0: ('add', 16)}, [])}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}