import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {14675608: (1384133992, 1384134760), 14675628: (1384124040, 1384124232), 14675668: (285223188, 285227284), 14676256: (1384120648, 1384120776), 14676316: (1384140104, 1384140264), 14676348: (1384120872, 1384120712), 14677016: (1358956841, 285213993), 14677088: (1358982426, 1358981402), 14676080: (185077000, 185074952), 14676084: (1394504968, 1394438408), 14676980: (185077000, 185074952), 14676984: (1394504968, 1394438408)}, 'sites': {}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}