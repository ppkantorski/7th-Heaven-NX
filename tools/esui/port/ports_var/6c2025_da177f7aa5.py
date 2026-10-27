import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {13247156: (285276441, 285274393), 13247212: (285483285, 285481237), 13247868: (285217045, 285221141), 13247912: (285249813, 285247765), 13251476: (285255957, 285260053), 13252260: (285442325, 285448469), 13252600: (285483285, 285481237)}, 'sites': {13246940: (7254968, {0: ('add', 12)}, []), 13247280: (7240609, {3: ('add', 2)}, []), 13251560: (7314500, {0: ('add', -2)}, []), 13252136: (7306235, {1: ('add', 2)}, []), 13252668: (7240609, {2: ('add', 1)}, [])}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {13248748: ('add', 12)}}