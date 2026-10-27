import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {14011776: (1409287136, 3573751839), 14011816: (1409286816, 3573751839), 14011856: (1409286496, 3573751839), 14011896: (1409286241, 335544323)}, 'sites': {}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}