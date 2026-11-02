"""BUILD 879: holu_1 -- models go under the empty 16:9 margin.

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

import ff7nx_voidmask as VM  # noqa: E402

RAW = _nx_os.path.join(_nx_root, 'attic', '_claude_tmp', 'holu_1_b878.raw')


def test_scope():
    assert VM.FIELDS == {'holu_1': {}}
    assert VM.SLOT == 28 and VM.BLACK == 0x0841


@pytest.mark.skipif(not os.path.exists(RAW), reason='needs the 878 holu_1')
def test_holu_1_margin_masked_art_untouched():
    import lgp
    import diag_common as DC
    parts = list(lgp.split_sections(open(RAW, 'rb').read()))
    new, n, nc = VM.plan(parts[7], parts[8])
    assert (n, nc) == (174, 3)
    # every original record and page byte-identical (plan's own _verify)
    VM._verify(parts[8], new, n, n * VM.REC)
    pl, _s, _e, _px = DC.parse_pages(new)
    pm = {p.slot: p for p in pl if p is not None}
    before, _ = VM._coverage(parts[8], {p.slot: p for p in DC.parse_pages(
        parts[8])[0] if p is not None}, 3, -216, -200, 432, 400)
    after, _ = VM._coverage(new, pm, 3, -216, -200, 432, 400)
    xs = np.arange(432 * 3) / 3 - 216
    wide = (xs < -160) | (xs >= 160)
    assert after[:, wide].all()                       # the margin is covered
    assert (after[:, ~wide] == before[:, ~wide]).all()  # 4:3 picture as was
    # the new records are front-most (ID 1) and all on slot 28
    for lay, _c, first, cnt in VM._rows(new):
        if lay != 2:
            continue
        for i in range(cnt - n, cnt):
            r = new[first + i * VM.REC:first + (i + 1) * VM.REC]
            assert r[32] == 28 and struct.unpack_from('<h', r, 24)[0] == 1
    with pytest.raises(ValueError):
        VM.plan(parts[7], new)                        # never applied twice


def test_disabled_env(monkeypatch):
    monkeypatch.setenv(VM.OFF_ENV, '1')
    assert VM.apply_to_flevel(None, {}) == {'names': [], 'refused': []}
