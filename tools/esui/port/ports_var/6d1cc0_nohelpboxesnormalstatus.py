import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {}, 'sites': {}, 'hooks': [], 'replace': [(13799340, [3107985192, 285225216, ('T',), 960495627, 3107985192, 1359307008, ('T',), 2034237452, 1384120333, 1386212110, 1895849343, ('bcond', 4, 0), 285239692, 1384120749, 1384120622, 1385841536, 1923093024, ('T',), 2030043148, 3107985192, 1359307008, ('T',), 2030043149, 3107985192, 1359323392, ('T',), 2030043150, 1384140815, 1259078127, 302005743, 1392606703, 3107985192, 1359335680, ('T',), 2030043151, ('b', 13799504)])], 'skip_calls': [], 'store_const': [], 'stores': {}}