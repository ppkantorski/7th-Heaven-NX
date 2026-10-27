import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {14409968: (285268277, 285259061), 14410040: (1384122792, 1384122952), 14410060: (285227285, 285214997), 14410212: (285276437, 285282581), 14410256: (840793064, 1384120328), 14410484: (285274389, 285260053), 14410560: (1384122792, 1384122952), 14410576: (285232437, 285215029), 14410844: (285230358, 285226262), 14410900: (285347094, 285359382), 14411184: (285222165, 285225237), 14411240: (285251861, 285268245), 14411692: (285230357, 285226261), 14411748: (285347093, 285359381), 14412040: (285222165, 285225237), 14412096: (285251861, 285268245), 14412540: (1384122812, 1384122972), 14414040: (285230356, 285237524), 14413308: (840631291, 1384121787)}, 'sites': {}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}