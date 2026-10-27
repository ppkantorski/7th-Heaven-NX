import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {13502828: (285243670, 285235478), 13503264: (285243673, 285235481), 13506024: (285243674, 285288730), 13506324: (285302041, 285343001), 13506872: (285243669, 285288725), 13507196: (285302036, 285342996), 13507500: (285293848, 285334808), 13507792: (285293844, 285334804), 13502904: (1384123703, 1384125111), 13502924: (840893432, 1384120472)}, 'sites': {13503668: (7299980, {4: ('add', 44), 5: ('add', 2)}, []), 13505204: (7299831, {3: ('add', 4)}, []), 13505700: (7299831, {3: ('add', 4)}, []), 13506556: (7299831, {3: ('add', 4)}, []), 13507540: (7306235, {4: ('add', 4), 6: ('add', 6)}, []), 13507832: (7306235, {4: ('add', 4), 6: ('add', 6)}, [])}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}