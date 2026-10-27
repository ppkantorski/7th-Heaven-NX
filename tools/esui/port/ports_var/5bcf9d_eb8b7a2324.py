import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {8174328: (1384122985, 1384121897), 8174228: (1384121000, 1384120328)}, 'sites': {}, 'hooks': [(8174232, [('st8', 9552707, 1), ('st32', 14433496, 1)])], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}