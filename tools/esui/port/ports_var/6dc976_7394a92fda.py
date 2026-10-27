import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {14100380: (1384129544, 1384130088), 14100408: (285585684, 285635860), 14100444: (840765416, 1384123368), 14100856: (1384133576, 1384133736), 14100884: (285585687, 285635863), 14100920: (840830952, 1384121704)}, 'sites': {}, 'hooks': [], 'replace': [(14100404, [185074952, 1394438408]), (14100880, [185074952, 1394438408])], 'skip_calls': [], 'store_const': [], 'stores': {}}