import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {13938116: (285462811, 285479195)}, 'sites': {13936500: (7297795, {0: ('add', 16), 1: ('add', 16)}, []), 13936628: (7297795, {0: ('add', -44), 1: ('add', 16)}, []), 13936940: (7297795, {0: ('add', -80), 1: ('add', 8)}, []), 13937104: (7313209, {0: ('add', -146), 1: ('add', 21)}, []), 13937436: (7304711, {1: ('add', 10), 3: ('add', 12)}, []), 13938728: (7297795, {0: ('add', -15), 1: ('add', 16)}, [])}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {13936148: ('add', 16), 13939036: ('add', 16), 13939060: ('centre', 640, 2, 7296162), 13939188: ('add', 36)}}