"""BUILD 883b: mds6_2 -- the gate's door edge stays behind the wall.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)

import os  # noqa: E402
import struct  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402

import ff7nx_gatewall as GW  # noqa: E402

RAW = _nx_os.path.join(_nx_root, 'attic', '_claude_tmp', 'mds6_2_b882.raw')


def test_scope():
    assert GW.FIELDS == {'mds6_2': [((128, -256), 1086, (112, -256), 1)]}


@pytest.mark.skipif(not os.path.exists(RAW), reason='needs the 882 mds6_2')
def test_mds6_2_square_front_picture_unchanged():
    import lgp
    import ff7nx_framesim as FS
    parts = list(lgp.split_sections(open(RAW, 'rb').read()))
    new, n = GW.plan('mds6_2', parts[8])
    assert n == 1 and len(new) == len(parts[8])
    o = GW._layer2(new)[(128, -256)][0]
    assert struct.unpack_from('<hI', new, o + GW.OFF_ID)[0] == 1
    assert struct.unpack_from('<I', new, o + GW.OFF_IDBIG)[0] == 10000
    q = list(parts)
    q[8] = new
    A, B = FS.Field(list(parts)), FS.Field(q)
    for cy in (-200, -150, 0):
        assert np.array_equal(A.render(cam=(0, cy), K=1, mark=True),
                              B.render(cam=(0, cy), K=1, mark=True)), cy
    assert GW.plan('mds6_2', new)[1] == 0          # idempotent
    bad = bytearray(parts[8])
    struct.pack_into('<h', bad, o + GW.OFF_ID, 900)
    with pytest.raises(ValueError):
        GW.plan('mds6_2', bytes(bad))              # unexpected layout refuses


def test_disabled_env(monkeypatch):
    monkeypatch.setenv(GW.OFF_ENV, '1')
    assert GW.apply_to_flevel(None, {}) == {'names': [], 'refused': []}
