import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {13502828: (285243670, 285245718), 13503264: (285243673, 285245721), 13506324: (285302041, 285297945), 13507196: (285302036, 285297940), 13507500: (285293848, 285289752), 13507792: (285293844, 285289748), 13502904: (1384123703, 1384123383)}, 'sites': {13503668: (7299980, {4: ('add', -10)}, []), 13507540: (7306235, {4: ('add', 4), 6: ('add', 6)}, []), 13507832: (7306235, {4: ('add', 4), 6: ('add', 6)}, [])}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}