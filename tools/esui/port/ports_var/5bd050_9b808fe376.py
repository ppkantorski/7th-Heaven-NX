import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {8174788: (1384121929, 1384123529), 8175196: (1384121929, 1384123529), 8175324: (285233416, 285237512), 8175556: (185079060, 706675700)}, 'sites': {}, 'hooks': [], 'replace': [(8175076, [185079060, 1394571924]), (8175400, [185077000, 1394571528])], 'skip_calls': [], 'store_const': [], 'stores': {}}