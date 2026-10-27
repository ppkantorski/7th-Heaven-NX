"""BUILD 568 -- field_bg_dense.stack_hole_keys (convil_2's condor squares).

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
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import field_bg_dense as FD
from ff7nx_marginblack import Tile

S = 3
class Page:
    depth = 1; size_flag = 0; px = 256
class Art:
    def __init__(self, paint):
        self.px = 768
        self.hmask = paint.copy(); self.tmask = ~paint

def _setup(partner_clear=True, partner_group=(2, 2), partner_idx=5):
    sec9 = bytearray(4096)
    a = Tile(100, 2, 0, 0, 1, 0, 0, 2, 1)      # owner: src (0,0)
    b = Tile(200, 2, 0, 0, 1, 16, 0, 3, 1)     # partner: src (16,0)
    for t, g in ((a, (2, 2)), (b, partner_group)):
        sec9[t.off + 26], sec9[t.off + 27] = g
    idx = np.zeros((256, 256), np.uint8)
    idx[0:16, 16:32] = partner_idx              # partner draws it in vanilla
    paint = np.zeros((768, 768), bool)
    paint[0:48, 0:48] = True                    # Cosmos: owner paints
    if not partner_clear:
        paint[0:48, 48:96] = True
    art = Art(paint)
    keys = {(1, 0, 0, 2): {'tiles': [100]}, (1, 16, 0, 3): {'tiles': [200]}}
    pal = np.zeros((8, 256), np.uint16)
    return (bytes(sec9), {1: Page()}, {1: idx}, [a, b], keys,
            lambda s, p: art, None, pal, S)

def test_swapped_ownership_is_filled():
    r = FD.stack_hole_keys(*_setup())
    assert set(r) == {(1, 0, 0, 2)} and r[(1, 0, 0, 2)].all()

def test_partner_still_paints_no_fill():
    assert FD.stack_hole_keys(*_setup(partner_clear=False)) == {}

def test_other_group_is_not_a_partner():
    assert FD.stack_hole_keys(*_setup(partner_group=(2, 4))) == {}

def test_vanilla_hole_stays_a_hole():
    assert FD.stack_hole_keys(*_setup(partner_idx=0)) == {}

def test_inert_at_256():
    args = list(_setup()); args[-1] = 1
    assert FD.stack_hole_keys(*args) == {}
