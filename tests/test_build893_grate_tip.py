#!/usr/bin/env python3
"""BUILD 893: mds5_1 grate -- the bright glow tip at the third bar's right
end is faded (ff7nx_fxseam LOCAL SPIKES). Needs a built sdout flevel.lgp
(skipped otherwise; game data is never in the repository).

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)

import os

import pytest

import ff7nx_fxseam as S

FLEVEL = os.path.join(nxpaths.ROOT, 'sdout', 'atmosphere', 'contents',
                      '0100A5B00BDC6000', 'romfs', 'ff7', 'workingdir',
                      'data', 'field', 'flevel.lgp')


def _sec9():
    if not os.path.isfile(FLEVEL):
        pytest.skip('no built flevel.lgp')
    import lgp
    ar = lgp.Archive(FLEVEL)
    return lgp.split_sections(ar.decompressed(ar.index['mds5_1']))[8]


def test_spike_pass_only_lowers_and_is_idempotent():
    sec9 = _sec9()
    plans, st = S.plan_rim_despike('mds5_1', sec9)
    new = S.apply_plans(sec9, plans) if plans else sec9
    again, st2 = S.plan_rim_despike('mds5_1', new)
    assert st2.get('spikes', 0) == 0 and not again
    lum = lambda w: max((w >> 11) & 31, (w >> 6) & 31, w & 31)  # noqa: E731
    import struct
    import diag_common as DC
    pages = {p.slot: p for p in DC.parse_pages(sec9)[0] if p is not None}
    for slot, py, px, val in plans:
        p = pages[slot]
        old = struct.unpack_from('<H', p.data, (py * p.px + px) * 2)[0]
        assert lum(val) <= lum(old), 'the pass never brightens'


def test_spike_pass_switch(monkeypatch):
    monkeypatch.setenv(S.RIM_OFF_ENV, '1')
    assert S.plan_rim_despike('mds5_1', b'')[0] == []
