"""BUILD 877: ESUI options never leave the battle screen's code stock under
ESUI's art (misplaced / missing battle text), and the EXP Bar Toggle reaches
the main menu.

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
import os
import tempfile

import pytest

import ff7nx_esui as E

ROOT = nxpaths.ROOT
MOD = os.path.join(ROOT, 'mods', 'Enhanced Stock UI-rel2')


def test_877_unverified_battle_values_fall_back():
    s = E.enforce_verified({'battleview': 2, 'ba': 1, 'tbb': 0, 'nbb': 1,
                            'bars': 0, 'icons': 0, 'fonts': 2})
    assert E.enforce_verified({'icons': 3}) == {'icons': 2}
    assert s == {'battleview': 3, 'ba': 0, 'tbb': 0, 'nbb': 0, 'bars': 0,
                 'icons': 0, 'fonts': 2}


def test_877_verified_values_are_kept():
    s = {'battleview': 3, 'ba': 0, 'tbb': 1, 'nbb': 0, 'bars': 2, 'icons': 1}
    assert E.enforce_verified(s) == s


def test_877_exp_bar_toggle_moves_the_main_menu_bars(monkeypatch):
    monkeypatch.setattr(E, 'CURRENT', {'mod': None, 'folders': ['EnhancedStock', 'EXP']})
    monkeypatch.setattr(E, 'screens', lambda: ('main',))
    code = {r[0]: r[2] for r in E._sets()['code']}
    assert code[0xD033FC] == 0x11152D1A           # add w26, w8, #0x54b
    assert code[0xD03784] == 0x11151D1A           # #0x547
    assert code[0xD03954] == 0x1115211A           # #0x548
    monkeypatch.setattr(E, 'CURRENT', {'mod': None, 'folders': ['EnhancedStock']})
    code = {r[0]: r[2] for r in E._sets()['code']}
    assert code[0xD033FC] == 0x11052D1A           # 00-Main's #0x14b


@pytest.mark.skipif(not os.path.isdir(MOD), reason='ESUI mod not present')
@pytest.mark.parametrize('opts', [{}, {'battleview': 2}, {'icons': 0, 'bars': 0},
                                  {'ba': 1, 'tbb': 0, 'nbb': 1}])
def test_877_every_battle_choice_builds_a_ported_battle(opts):
    import build
    m = build.Mod(MOD, tempfile.mkdtemp())
    m._load_manifest()
    o = dict(m.manifest.defaults())
    o.update(opts)
    E.CURRENT = None
    E._SCREEN_PORTS.clear()
    try:
        E.CURRENT = E.plan_for(m, o)
        for s in ('battle', 'menus', 'shop', 'load'):
            assert E.screen_ports(s), s
    finally:
        E.CURRENT = None
        E._SCREEN_PORTS.clear()


@pytest.mark.skipif(not os.path.isdir(MOD), reason='ESUI mod not present')
def test_878_every_unlocked_combination_has_a_ported_battle(monkeypatch):
    import itertools
    import build
    real, memo = E._folder_hext, {}
    def cached(folder, *a, **k):                   # each folder read once
        key = (folder.lower(), a, tuple(sorted(k.items())))
        if key not in memo:
            memo[key] = real(folder, *a, **k)
        return memo[key]
    monkeypatch.setattr(E, '_folder_hext', cached)
    m = build.Mod(MOD, tempfile.mkdtemp())
    m._load_manifest()
    keys = sorted(E.BATTLE_VERIFIED)
    bad = []
    try:
        for combo in itertools.product(*(E.BATTLE_VERIFIED[k] for k in keys)):
            o = dict(m.manifest.defaults())
            o.update(zip(keys, combo))
            E.CURRENT = E.plan_for(m, o)
            E._SCREEN_PORTS.clear()
            if not E.screen_ports('battle'):
                bad.append(dict(zip(keys, combo)))
    finally:
        E.CURRENT = None
        E._SCREEN_PORTS.clear()
    assert not bad, bad
