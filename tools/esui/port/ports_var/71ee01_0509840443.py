import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {14927824: (285550872, 285524248), 14927928: (285550872, 285524248), 14928244: (285550872, 285524248), 14928420: (285546776, 285519128), 14928652: (285548824, 285524248)}, 'sites': {14927888: (7298060, {0: ('add', 19)}, []), 14927992: (7298060, {0: ('add', 19)}, []), 14928308: (7314500, {0: ('add', 18)}, []), 14928480: (7297795, {0: ('add', 37)}, []), 14928716: (7298060, {0: ('add', 16)}, [])}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}