import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {13910216: (840828904, 1384121096), 13910288: (1384135560, 1384126664), 13910308: (1384131656, 1384132360), 13910328: (1384120968, 1384120584), 13910868: (1358956809, 1358955785), 13911496: (285581626, 285600058), 13910652: (840699896, 1384125336)}, 'sites': {13911580: (7297795, {0: ('add', -10)}, []), 13911684: (7208037, {0: ('add', -312), 1: ('add', 22)}, [])}, 'hooks': [], 'replace': [(13910576, [1384120467, 445843059]), (13911488, [185074952, 1394438408])], 'skip_calls': [], 'store_const': [], 'stores': {}}