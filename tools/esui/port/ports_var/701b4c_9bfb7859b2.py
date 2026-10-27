import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {14414796: (1384120744, 1384120712), 14414908: (285258005, 285303061), 14414952: (1384133928, 1384136136), 14415168: (285259032, 285303064), 14415212: (1384133800, 1384136008), 14415460: (1384139033, 1384139577), 14415916: (285635865, 285664537), 14416148: (285635863, 285664535), 14416396: (285588744, 285582600), 14416800: (285588744, 285582600)}, 'sites': {}, 'hooks': [], 'replace': [(14414864, [1384122281, 453606677, 1392869045])], 'skip_calls': [], 'store_const': [], 'stores': {}}