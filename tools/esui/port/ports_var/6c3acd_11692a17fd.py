import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
{'words': {13268672: (1384123593, 1384123273), 13268720: (285434132, 285446420), 13271996: (1384123593, 1384123273), 13272044: (285434132, 285446420), 13268816: (285496597, 285492501), 13269364: (285265173, 285266197), 13270148: (285245724, 285237532), 13270708: (285245724, 285237532), 13271296: (285245724, 285237532), 13270268: (285575445, 285565205), 13270828: (285575445, 285565205), 13271416: (285575445, 285565205), 13269492: (1384131656, 1384131336), 13273216: (285286677, 285288725), 13273260: (1384127560, 1384127720), 13273324: (1384132104, 1384131912), 13273416: (285483285, 285481237), 13273600: (1384121768, 1384121672), 13273980: (285221141, 285223189)}, 'sites': {}, 'hooks': [], 'replace': [], 'skip_calls': [], 'store_const': [], 'stores': {}}