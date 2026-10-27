import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {14556884: (838862824, 1384120456), 14556976: (285619477, 285623573), 14557064: (1384120872, 1384120712), 14557252: (285216024, 285217048), 14557960: (285308187, 285256987), 14557864: (185078024, 185074952), 14557868: (1394571528, 1394438408)}, 'sites': {}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}