import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {}, 'sites': {8150760: (6008904, {5: ('add', 16), 6: ('add', 11)}, [])}, 'hooks': [], 'replace': [(8152496, [185074952, 1394571528]), (8152656, [1394438409, 1258815784])], 'skip_calls': [], 'store_const': [], 'stores': {}}