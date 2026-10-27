import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {13904320: (838862824, 1384120456), 13904440: (1384139976, 1384128680), 13904460: (1384131656, 1384132360), 13904480: (1384120968, 1384120584), 13905496: (285577527, 285595959), 13905696: (285585687, 285606167), 13905992: (285585719, 285604151), 13906880: (285581595, 285600027)}, 'sites': {13905756: (7298060, {0: ('add', -145)}, []), 13906052: (7313209, {0: ('add', -147)}, []), 13906940: (7297795, {0: ('add', -95)}, [])}, 'hooks': [], 'replace': [(13904732, [1384120468, 445908628]), (13905488, [185074952, 1394438408]), (13905684, [185074952, 1394438408]), (13905984, [185074952, 1394438408]), (13906868, [185074952, 1394438408])], 'skip_calls': [13905556], 'store_const': [], 'stores': {}}