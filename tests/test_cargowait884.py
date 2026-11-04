"""BUILD 884: cargoin -- Cloud waits for Biggs's "Cloud!!" box.

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

import ff7nx_cargowait as CW  # noqa: E402

RAW = _nx_os.path.join(_nx_root, 'attic', '_claude_tmp', 'cargoin_b883.raw')


def test_scope():
    assert set(CW.FIELDS) == {'cargoin'}


def test_synthetic():
    pat, k = CW.FIELDS['cargoin']
    sec = b'\x00' * 10 + pat + b'\x00' * 10
    new, n = CW.fix_sec1('cargoin', sec)
    assert n == 1 and len(new) == len(sec)
    assert [i for i in range(len(sec)) if sec[i] != new[i]] == [10 + k]
    assert new[10 + k] == CW.REQEW
    assert CW.fix_sec1('cargoin', new) == (new, 0)        # idempotent
    with pytest.raises(ValueError):
        CW.fix_sec1('cargoin', sec + pat)                  # twice: refuse
    with pytest.raises(ValueError):
        CW.fix_sec1('cargoin', b'\x00' * 40)               # absent: refuse


@pytest.mark.skipif(not os.path.exists(RAW), reason='needs the 883 cargoin')
def test_real_cargoin_one_byte():
    import lgp
    parts = list(lgp.split_sections(open(RAW, 'rb').read()))
    new, n = CW.fix_sec1('cargoin', parts[0])
    assert n == 1 and sum(a != b for a, b in zip(new, parts[0])) == 1


def test_disabled_env(monkeypatch):
    monkeypatch.setenv(CW.OFF_ENV, '1')
    assert CW.apply_to_flevel(None, {}) == {'names': [], 'refused': []}
