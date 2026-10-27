import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {14821796: (285236502, 285264150), 14821896: (285531446, 285736246), 14822740: (285236502, 285264150), 14824820: (285262100, 285280532), 14824984: (285262100, 285280532)}, 'sites': {}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}