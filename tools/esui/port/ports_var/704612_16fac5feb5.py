import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {14452928: (285308182, 285262102), 14453208: (285308182, 285262102), 14453596: (285308181, 285262101), 14453776: (285239575, 285213975), 14453944: (285239575, 285220119), 14454124: (1384121161, 1384120553), 14454404: (285245716, 285242644), 14454448: (285291796, 285262100), 14454712: (285311252, 285308180), 14454756: (285291796, 285262100)}, 'sites': {}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}