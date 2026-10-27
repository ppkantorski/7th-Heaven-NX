import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {13913868: (285589781, 285621525)}, 'sites': {13914296: (7306235, {0: ('add', -42), 1: ('add', 30)}, []), 13914552: (7306235, {0: ('add', -86), 1: ('add', 29)}, []), 13914948: (7306235, {0: ('add', -88), 1: ('add', 29)}, [])}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {13915256: ('add', -3735552), 13915292: ('add', 978944), 13915524: ('add', -3735552), 13915996: ('add', 369033216)}}