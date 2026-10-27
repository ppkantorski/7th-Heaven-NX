#!/usr/bin/env python3
"""tests/test_facial_flag.py -- the facial-animation switch, and its honesty.

BUILD 441. `SEVENTH_NX_FACIAL=1` flips Ninostyle Fixes' `fb` option to its own
default ("On"). It is NOT a feature switch yet: flevel.lgp cannot take the 994
new textures, and the runtime that reads them is FFNx's, not the game's. A
build that sets it has to say both of those out loud.

The trap it must never fall into: the base Ninostyle Chibi mod uses the SAME
option id `fb` for "Chibi Field and World Models", where 1 means ON. Touching
that would turn the chibi models off.

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
import sys

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

CACHE = os.path.join(_HERE, 'cache')
FIXES = os.path.join(CACHE, 'Ninostyle Chibi Fixes and Additions v2.5',
                     'mod.xml')
BASE = os.path.join(CACHE, 'Ninostyle Chibi', 'mod.xml')


@pytest.fixture
def m():
    import iro
    return iro


def test_the_flag_is_off_unless_asked(m, monkeypatch):
    monkeypatch.delenv(m.FACIAL_ENV, raising=False)
    assert m.facial_animation_requested() is False
    for spelling in ('0', '', 'off', 'no', 'false'):
        monkeypatch.setenv(m.FACIAL_ENV, spelling)
        assert m.facial_animation_requested() is False, spelling
    for spelling in ('1', 'true', 'YES', 'On'):
        monkeypatch.setenv(m.FACIAL_ENV, spelling)
        assert m.facial_animation_requested() is True, spelling


def test_off_leaves_the_override_exactly_as_it_was(m, monkeypatch):
    monkeypatch.delenv(m.FACIAL_ENV, raising=False)
    assert m._overrides_for(m.NINOSTYLE_FIXES_ID) == {'fb': 1}


def test_on_selects_the_mods_own_on_value(m, monkeypatch):
    """For THIS mod's option, 0 is On and 1 is Off."""
    monkeypatch.setenv(m.FACIAL_ENV, '1')
    assert m._overrides_for(m.NINOSTYLE_FIXES_ID) == {'fb': 0}


def test_no_other_mod_is_touched(m, monkeypatch):
    monkeypatch.setenv(m.FACIAL_ENV, '1')
    assert m._overrides_for('some-other-guid') == {}


@pytest.mark.skipif(not os.path.exists(FIXES) or not os.path.exists(BASE),
                    reason='needs the Ninostyle mods')
def test_the_chibi_models_stay_on_either_way(m, monkeypatch):
    """
    The one that matters. Same option id, opposite meaning:

        Ninostyle Chibi        fb = "Chibi Field and World Models"  1 = On
        Ninostyle Fixes v2.5   fb = "Facial Animation"              0 = On
    """
    for value in (None, '1'):
        if value is None:
            monkeypatch.delenv(m.FACIAL_ENV, raising=False)
        else:
            monkeypatch.setenv(m.FACIAL_ENV, value)
        assert m.Manifest(BASE).defaults()['fb'] == 1, value
    monkeypatch.delenv(m.FACIAL_ENV, raising=False)
    assert m.Manifest(FIXES).defaults()['fb'] == 1
    monkeypatch.setenv(m.FACIAL_ENV, '1')
    assert m.Manifest(FIXES).defaults()['fb'] == 0


def test_the_build_says_what_the_flag_cannot_do(monkeypatch):
    """Silence here would ship an option that merely looks enabled."""
    import build
    import iro
    monkeypatch.setenv(iro.FACIAL_ENV, '1')

    class _Mod:
        cache = None

    out = []
    build._warn_facial_animation_is_not_installable({}, [_Mod()], out.append)
    assert out == []          # nothing staged, nothing to warn about

    monkeypatch.delenv(iro.FACIAL_ENV, raising=False)
    out = []
    build._warn_facial_animation_is_not_installable({}, [_Mod()], out.append)
    assert out == []          # and silent when the flag is off


def test_the_runtime_follows_the_saved_setting_not_just_the_env(monkeypatch):
    """
    BUILD 461's bug. `SEVENTH_NX_FACIAL=1` only moves a DEFAULT, and
    `_mod_options_from_saved` lets a value saved in settings.json win over it.
    So somebody with Facial Animation turned on in the GUI ships all 1,013
    textures -- and used to get no runtime, because the pass asked the
    environment instead of asking the build.
    """
    import build as b

    class Plan:
        archive_files = {'flevel.lgp': {'eye_aaaa_2.tex': ('x', 'y'),
                                        'c_eye2.tex': ('x', 'y')}}

    monkeypatch.delenv('SEVENTH_NX_FACIAL', raising=False)
    assert b.facial_art_is_shipping(None, None, plan=Plan())

    class Bare:
        archive_files = {'flevel.lgp': {'c_eye2.tex': ('x', 'y')}}

    assert not b.facial_art_is_shipping(None, None, plan=Bare())
    # and the environment still forces it, for a load order without the art
    monkeypatch.setenv('SEVENTH_NX_FACIAL', '1')
    assert b.facial_art_is_shipping(None, None, plan=Bare())


def test_the_art_probe_only_matches_the_indexed_set():
    """
    `c_eye2.tex` is a vanilla name the mod replaces in place and it ships
    whether or not Facial Animation is on, so matching it would turn the
    runtime on for everybody. Only `eye_<model>_<n>` means the indexed set.
    """
    import build as b
    for yes in ('eye_aaaa_2.tex', 'eye_aaaar_5.tex', 'eye_bk2h_3.tex'):
        assert b._FACIAL_ART_RE.match(yes), yes
    for no in ('c_eye2.tex', 'b_eye2r.tex', 'eye_aaaa_0.tex',
               'mouth_aaaa_2.tex', 'chi_red5_2.tex'):
        assert not b._FACIAL_ART_RE.match(no), no
