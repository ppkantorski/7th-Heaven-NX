import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {14544284: (285218069, 285213973), 14544956: (1358962944, 285213952), 14545452: (1358962967, 285213975), 14546012: (285278485, 285279509), 14546056: (285245717, 285259029), 14546996: (285305109, 285304085), 14547040: (285245717, 285259029), 14546764: (285305109, 285304085)}, 'sites': {14546380: (7306235, {0: ('add', 10), 1: ('add', 1)}, []), 14548156: (7306235, {6: ('add', 6)}, [])}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}