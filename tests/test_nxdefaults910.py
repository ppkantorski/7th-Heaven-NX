"""BUILD 910: settings left at a default follow it across updates.

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

import copy  # noqa: E402
import json  # noqa: E402

import nxdefaults as D  # noqa: E402

OLD = {'__global__': {'voice_auto_delay': 500, 'scaler': 'hd_lite',
                      'widescreen': 'ws-3d'},
       'Echo-s.iro': {'enabled': True, 'options': {'Auto': 0, 'FA': 1}},
       'Mod.iro': {'enabled': False, 'options': {'x': 1}}}
NEW = {'__global__': {'voice_auto_delay': 200, 'scaler': 'hd',
                      'widescreen': 'ws-3d'},
       'Echo-s.iro': {'enabled': True, 'options': {'Auto': 1, 'FA': 1}},
       'Mod.iro': {'enabled': True, 'options': {'x': 2}}}


def test_untouched_settings_follow_the_new_defaults():
    saved = D.stamp({'__global__': {'voice_auto_delay': 500,
                                    'scaler': 'hd_lite', 'widescreen': 'off'},
                     'Echo-s.iro': {'enabled': True,
                                    'options': {'Auto': 0, 'FA': 0}},
                     'Mod.iro': {'enabled': False, 'options': {'x': 1}}},
                    OLD, '1.1.4')
    rec = saved[D.KEY]
    assert rec['global'] == {'voice_auto_delay': 500, 'scaler': 'hd_lite'}
    assert rec['mods']['Echo-s.iro'] == {'enabled': True,
                                         'options': {'Auto': 0}}
    saved = json.loads(json.dumps(saved))             # through the file
    changed = D.refresh(saved, NEW)
    g = saved['__global__']
    assert g == {'voice_auto_delay': 200, 'scaler': 'hd',
                 'widescreen': 'off'}                 # the user's choice kept
    assert saved['Echo-s.iro']['options'] == {'Auto': 1, 'FA': 0}
    assert saved['Mod.iro'] == {'enabled': True, 'options': {'x': 2}}
    assert len(changed) == 5


def test_a_change_after_the_save_is_kept():
    saved = D.stamp(copy.deepcopy(OLD), OLD)
    saved['__global__']['voice_auto_delay'] = 700     # hand edit / GUI
    saved['Echo-s.iro']['options']['Auto'] = 1
    D.refresh(saved, NEW)
    assert saved['__global__']['voice_auto_delay'] == 700
    assert saved['__global__']['scaler'] == 'hd'


def test_same_default_changes_nothing_and_is_idempotent():
    saved = D.stamp(copy.deepcopy(OLD), OLD)
    assert D.refresh(copy.deepcopy(saved), OLD) == []
    D.refresh(saved, NEW)
    assert D.refresh(saved, NEW) == []                # same record, no repeat


def test_legacy_file_without_a_record():
    saved = {'__global__': {'voice_auto_delay': 500, 'scaler': 'hd_lite'},
             'Echo-s.iro': {'enabled': True, 'options': {'Auto': 0}},
             'Enhanced Stock UI-rel2': {'enabled': True,
                                        'options': {'fdb': 0}},
             '[Tsunamods]-Arranged-Soundtrack.iro': {'enabled': True,
                                                     'options': {}}}
    factory = {'__global__': {'voice_auto_delay': 200, 'scaler': 'hd'},
               'Echo-s.iro': {'enabled': True, 'options': {'Auto': 1}},
               'Enhanced Stock UI-rel2': {'enabled': True,
                                          'options': {'fdb': 1}},
               '[Tsunamods]-Arranged-Soundtrack.iro': {'enabled': False,
                                                       'options': {}}}
    D.refresh(saved, factory)
    assert saved['__global__'] == {'voice_auto_delay': 200,
                                   'scaler': 'hd_lite'}   # not in LEGACY
    assert saved['Echo-s.iro']['options']['Auto'] == 1
    assert saved['Enhanced Stock UI-rel2']['options']['fdb'] == 0   # 0 was
    assert saved['[Tsunamods]-Arranged-Soundtrack.iro']['enabled'] is False


def test_junk_is_harmless():
    assert D.refresh({}, NEW) == []
    assert D.refresh({D.KEY: 'junk', '__global__': [],
                      'Echo-s.iro': 3}, NEW) == []
    assert D.stamp({'Echo-s.iro': {'options': None}}, NEW)[D.KEY]['mods'] \
        == {}


def test_911_black_point_and_restore_inputs():
    # data/default_settings.json now agrees with LOCKED (BUILD 822: 11)
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'nxs', _nx_os.path.join(_nx_root, 'src', 'core', 'nxsettings.py'))
    nxs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nxs)
    with open(_nx_os.path.join(_nx_root, 'data', 'default_settings.json')) \
            as fh:
        g = json.load(fh)['__global__']
    assert {k: v for k, v in nxs.LOCKED.items() if g.get(k, v) != v} == {}
    saved = {'__global__': {'hd_black_point': 12}}
    D.refresh(saved, {'__global__': {'hd_black_point': 11}})
    assert saved['__global__']['hd_black_point'] == 11
