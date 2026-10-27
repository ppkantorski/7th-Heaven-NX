import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {14462848: (1358982423, 1359009047), 14465632: (1384122665, 1384122601), 14465864: (1384125001, 1384124937), 14466096: (1384127337, 1384127209), 14467904: (1384138955, 1384134539), 14467908: (1896422696, 1896281384), 14466260: (840896502, 1384121110), 14466264: (1384120887, 1384120599), 14463380: (1358978327, 1358981399), 14463560: (1358972663, 1358955255), 14463772: (1358972531, 1358955123), 14464000: (285272328, 285302024), 14464924: (285272328, 285302024), 14464768: (1384138952, 1384137928), 14464780: (310397128, 310396104), 14463844: (840896502, 1384121110), 14465456: (285223191, 285221143)}, 'sites': {}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}