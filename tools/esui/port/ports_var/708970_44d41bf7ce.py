import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {14540260: (838862824, 1384120456), 14540352: (285619477, 285623573), 14540440: (1384120872, 1384120712), 14540628: (285216024, 285217048), 14541544: (285248795, 285256987), 14542904: (285230377, 285232425), 14543028: (285337915, 285246779), 14541408: (185078024, 185074952), 14541412: (1394571528, 1394438408), 14542892: (185078024, 185074952), 14542896: (1394571528, 1394438408), 14541528: (185080072, 1384124096), 14541532: (1394571528, 453016840), 14543016: (185080072, 1384124096), 14543020: (1394571528, 453016840)}, 'sites': {}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [(14542756, 7)], 'stores': {}}