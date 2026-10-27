import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {14385892: (285308182, 285278486), 14386172: (285308182, 285278486), 14386552: (285308181, 285278485), 14386732: (285240599, 285221143), 14386900: (285240599, 285227287), 14387084: (285240980, 285227668), 14387356: (285247764, 285244692), 14387400: (285291796, 285279508), 14387664: (285318420, 285314324), 14387708: (285291796, 285279508)}, 'sites': {}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}