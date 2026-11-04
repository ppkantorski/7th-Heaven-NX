"""BUILD 885: junin1a / nivgate window waits, cargoin light tile.

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

import pytest  # noqa: E402

import ff7nx_lighttile as LT  # noqa: E402
import ff7nx_winwait as WW  # noqa: E402

TMP = _nx_os.path.join(_nx_root, 'attic', '_claude_tmp')


def test_scope():
    assert set(WW.FIELDS) == {'junin1a', 'nivgate'}
    assert set(LT.FIELDS) == {'cargoin'}
    for name, patches in WW.FIELDS.items():
        for _what, old, new in patches:
            assert len(old) == len(new) and old != new


@pytest.mark.parametrize('name,ndiff', [('junin1a', 1), ('nivgate', 4)])
def test_real_fields(name, ndiff):
    raw = os.path.join(TMP, '%s_b884.raw' % name)
    if not os.path.exists(raw):
        pytest.skip('needs the 884 field')
    import lgp
    parts = list(lgp.split_sections(open(raw, 'rb').read()))
    new, n = WW.fix_sec1(name, parts[0])
    assert n == 1 and len(new) == len(parts[0])
    assert sum(a != b for a, b in zip(new, parts[0])) == ndiff
    assert WW.fix_sec1(name, new) == (new, 0)                 # idempotent
    with pytest.raises(ValueError):
        WW.fix_sec1(name, b'\x00' * 64)                       # absent


def test_real_cargoin_light_tile():
    raw = os.path.join(TMP, 'cargoin_b883.raw')
    if not os.path.exists(raw):
        pytest.skip('needs the 883 cargoin')
    import lgp
    parts = list(lgp.split_sections(open(raw, 'rb').read()))
    s9, n = LT.fix('cargoin', parts[0], parts[8])
    # 45 param-8 frame-0 records (885 + 885c) + 12 param-1 (885b)
    assert n == 57 and len(s9) == len(parts[8])
    diff = [i for i in range(len(s9)) if s9[i] != parts[8][i]]
    assert len(diff) == 57 and all(s9[i] == LT.NEVER for i in diff)
    assert all(parts[8][i] == 0x01 or parts[8][i - 1] == 1 for i in diff)
    assert LT.fix('cargoin', parts[0], s9)[1] == 0            # idempotent


def test_disabled_env(monkeypatch):
    monkeypatch.setenv(WW.OFF_ENV, '1')
    monkeypatch.setenv(LT.OFF_ENV, '1')
    assert WW.apply_to_flevel(None, {}) == {'names': [], 'refused': []}
    assert LT.apply_to_flevel(None, {}) == {'names': [], 'refused': []}
