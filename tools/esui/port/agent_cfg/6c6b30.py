"""Post-battle gil / items (6C6B30). BUILD 632: the cursor (dc1200 = selection:
0 Take everything, 1..4 items, 5 Exit) was never varied, so the fit missed
ESUI's cursor x 0 -> 7 and the item rows' (sel-1)*0x43 -> *0x16.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
FNS.setdefault(0x6c6b30, {})
_items = {0x99e2f0 + 6 * k: (k + 1) | (((k % 3) + 1) << 16) for k in range(4)}
FNS[0x6c6b30]["pokes"] = [{**_items, 0xdc1200: sel, 0xdc1128: f, 0xdc1300: 2, 0xdc127c: 0}
                          for sel in range(6) for f in (0, 1)]
# the port's own cursor code differs from the x86 (its stock args are "noise"
# to verify2), so ESUI's two cursor changes are carried by hand:
#   x 0 -> 7 for Take everything / Exit (the two strh wzr of [ebp-0x1c])
#   items: (sel - 1) * 0x43 -> * 0x16 (the port: sel*k - k; mov w9,#k is aligned)
FNS[0x6c6b30]['arm_struct'] = {0xcee734: A.sub_imm(8, 8, 0x16)}
FNS[0x6c6b30]['replace'] = [(0xcee568, [A.movz(8, 7), 0x79000008]),     # strh w8, [x0]
                            (0xcee6e8, [A.movz(8, 7), 0x79000008])]
# align had mapped ESUI's "mov [ebp-0x1c], 0 -> 7" onto these compares of the
# selection with 0 (they only set the guest flags): keep them
FNS[0x6c6b30]['arm_struct'].update({0xcee508: 0x7100011F, 0xcee650: 0x710002DF})
