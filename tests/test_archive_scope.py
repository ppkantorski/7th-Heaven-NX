#!/usr/bin/env python3
"""tests/test_archive_scope.py -- a setting rebuilds only what it changes.

BUILD 803. Every archive's cache key used to take every SEVENTH_NX_* variable
the neutral lists did not exclude, so changing Movie quality, the Title
screen or 360-degree movement rebuilt flevel.lgp. `ARCHIVE_SCOPED_ENV` names
the archives such a setting reaches; anything named nowhere still reaches
every archive. These tests hold both halves: a scoped setting moves only its
own archives' keys, an unknown one still moves them all, and every variable
the GUI writes has been given a place.

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
import importlib.util
import json
import os
import re

import pytest

_HERE = nxpaths.ROOT
BUILD_PY = nxpaths.src_file('build.py')
TOP = os.path.join(_HERE, '7th_heaven_nx.py')
pytestmark = pytest.mark.skipif(not os.path.exists(BUILD_PY),
                                reason='needs build.py')

# The archives a key is computed for (the fingerprinted ones that matter).
ARCHIVES = ('flevel.lgp', 'battle.lgp', 'magic.lgp', 'char.lgp',
            'world_us.lgp', 'menu_us.lgp', 'high-us.lgp', 'coaster.lgp')

# Variables the GUI writes that DO reach every archive (or that nobody has
# shown reach fewer). Adding a name here is the deliberate "rebuild them all"
# choice; the alternative is an entry in build.ARCHIVE_SCOPED_ENV.
EVERY_ARCHIVE = frozenset((
    'SEVENTH_NX_WIDESCREEN',             # flevel, battle overlays, ...
    'SEVENTH_NX_FIELD_FRAME',            # flevel's 16:9 frame data
    'SEVENTH_NX_FIELD_TEX_CAP',          # flevel, char and the minigames
    'SEVENTH_NX_FIELD_BG_PAGE_PX',       # flevel (locked in user mode)
    'SEVENTH_NX_FIELD_BG_D1_PX',
    'SEVENTH_NX_FIELD_BG_D1_ART',
    'SEVENTH_NX_FIELD_BG_BUDGET_MB',
    'SEVENTH_NX_FIELD_BG_MAX_TOTAL_PAGES',
    'SEVENTH_NX_FIELD_BG_D2_SLOTS_PER_GROUP',
    'SEVENTH_NX_MARGIN_BLACK',
    'SEVENTH_NX_MARGIN_ART',
    'SEVENTH_NX_ESUI_TEXSCALE',          # menu art (locked in user mode)
))


@pytest.fixture(scope='module')
def b():
    spec = importlib.util.spec_from_file_location('_build_under_test', BUILD_PY)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


def _keys(b, van):
    return {name: (b._archive_fingerprint(name, str(van), {}, 0),
                   b._archive_inputs_fingerprint(name, str(van), {}, 0))
            for name in ARCHIVES}


def _moved(b, van, monkeypatch, env, a='1', z='2'):
    monkeypatch.setenv(env, a)
    before = _keys(b, van)
    monkeypatch.setenv(env, z)
    after = _keys(b, van)
    return {n for n in ARCHIVES if before[n] != after[n]}, before, after


def test_a_scoped_setting_moves_only_its_own_archives(b, tmp_path,
                                                      monkeypatch):
    van = tmp_path / 'van.lgp'
    van.write_bytes(b'v' * 8)
    for env, scope in sorted(b.ARCHIVE_SCOPED_ENV.items()):
        moved, before, after = _moved(b, van, monkeypatch, env)
        want = set(scope) & set(ARCHIVES)
        assert moved == want, (env, sorted(moved), sorted(want))
        for n in want:   # both halves move, so reuse still refuses
            assert before[n][0] != after[n][0], (env, n)
            assert before[n][1] != after[n][1], (env, n)
        monkeypatch.delenv(env)


def test_an_unclassified_setting_still_rebuilds_everything(b, tmp_path,
                                                           monkeypatch):
    """The safe default: a setting nobody declared costs time, never a stale
    archive."""
    van = tmp_path / 'van.lgp'
    van.write_bytes(b'v' * 8)
    env = 'SEVENTH_NX_SOMETHING_NOBODY_HAS_CLASSIFIED'
    assert env not in b.ARCHIVE_SCOPED_ENV
    moved, _a, _z = _moved(b, van, monkeypatch, env)
    assert moved == set(ARCHIVES)


def test_how_the_build_was_started_is_not_an_input(b, tmp_path, monkeypatch):
    van = tmp_path / 'van.lgp'
    van.write_bytes(b'v' * 8)
    assert 'SEVENTH_NX_DEPS_CHILD' in b.SCHEDULING_ENV
    monkeypatch.delenv('SEVENTH_NX_DEPS_CHILD', raising=False)
    before = _keys(b, van)
    monkeypatch.setenv('SEVENTH_NX_DEPS_CHILD', '1')
    assert _keys(b, van) == before


def test_the_user_settings_that_used_to_rebuild_flevel_no_longer_do(b):
    for env in ('SEVENTH_NX_MOVIE_QUALITY', 'SEVENTH_NX_MOVIE_FIT',
                'SEVENTH_NX_MOVIE_COLOUR', 'SEVENTH_NX_TITLE_SCREEN',
                'SEVENTH_NX_ANALOG_360', 'SEVENTH_NX_LIMITER_FPS',
                'SEVENTH_NX_GFX_POOL_MB', 'SEVENTH_NX_TEX_CACHE',
                'SEVENTH_NX_BATTLE_BG_TEX_CAP', 'SEVENTH_NX_SPELL_TEX_CAP',
                'SEVENTH_NX_WORLD_TEX_CAP', 'SEVENTH_NX_MOVIE_30FPS'):
        assert not b._env_in_archive_key(env, 'flevel.lgp'), env


def test_the_scope_names_are_real_archives(b):
    known = set(b.ARCHIVES) | {'moviecam.lgp'}
    for env, scope in b.ARCHIVE_SCOPED_ENV.items():
        assert env.startswith('SEVENTH_NX_'), env
        assert set(scope) <= known, (env, scope)
        assert env not in b.ARCHIVE_NEUTRAL_ENV, \
            '%s is in a neutral list AND scoped -- pick one' % env


def _gui_written_env(b):
    """Every variable 7th_heaven_nx.py assigns, resolved to its name."""
    src = open(TOP, encoding='utf-8').read()
    ns = {'build': b, 'ff7nx_esui': b.ff7nx_esui}
    for name, value in re.findall(r"^(_HEADLESS_\w+_ENV) = '([^']+)'", src,
                                  re.M):
        ns[name] = value
    out = set()
    for expr in set(re.findall(r"os\.environ\[([^\]]+)\]\s*=(?!=)", src)):
        out.add(eval(expr, ns))                         # noqa: S307
    return out


def test_every_variable_the_gui_writes_has_a_place(b):
    """A new GUI setting must be classified: neutral (no archive), scoped (some
    archives) or EVERY_ARCHIVE above. Left unclassified it would quietly
    rebuild flevel on every change, which is this file's whole subject."""
    missing = []
    for env in sorted(_gui_written_env(b)):
        if (env in b.ARCHIVE_NEUTRAL_ENV or env in b.SCHEDULING_ENV
                or env in b.ARCHIVE_SCOPED_ENV or env in EVERY_ARCHIVE
                or env in (b.FPS_ENV, b.FPS_MODE_ENV, b.DW_HRC_ENV)):
            continue
        missing.append(env)
    assert not missing, ('classify these in build.ARCHIVE_SCOPED_ENV (or a '
                         'neutral list, or EVERY_ARCHIVE): %s' % missing)


def test_a_miss_names_the_setting_that_moved(b, tmp_path, monkeypatch):
    van = tmp_path / 'van.lgp'
    van.write_bytes(b'v' * 8)
    monkeypatch.setattr(b, 'ARCHIVE_FP_CACHE', str(tmp_path / 'fp'))
    os.makedirs(b.ARCHIVE_FP_CACHE)
    env = 'SEVENTH_NX_FIELD_TEX_CAP'
    monkeypatch.setenv(env, '512')
    dest = tmp_path / 'built.lgp'
    dest.write_bytes(b'b' * 8)
    fp = b._archive_fingerprint('char.lgp', str(van), {}, 0)
    ifp = b._archive_inputs_fingerprint('char.lgp', str(van), {}, 0)
    b._archive_cache_store('char.lgp', str(dest), fp, '', ifp)
    monkeypatch.setenv(env, '768')
    fp2 = b._archive_fingerprint('char.lgp', str(van), {}, 0)
    ifp2 = b._archive_inputs_fingerprint('char.lgp', str(van), {}, 0)
    lines = b._rebuild_reasons('char.lgp', fp2, ifp2)
    assert any(env in l and '512' in l and '768' in l for l in lines), lines
    # only the code moved: said so, not a list of inputs
    lines = b._rebuild_reasons('char.lgp', 'some-other-key', ifp)
    assert lines == ["the same mods and settings; the manager's own code "
                     "changed"], lines
    # nothing recorded yet
    assert b._rebuild_reasons('magic.lgp', fp, ifp) == [
        'no record of an earlier build of it']


def test_the_model_loop_explains_and_records_every_archive():
    src = open(BUILD_PY, encoding='utf-8').read()
    assert "_log_rebuild_reasons(name, fp, fp_inputs, log)" in src
    assert "_log_rebuild_reasons('flevel.lgp', ffp, ffp_inputs, log)" in src
    # both keys of a model archive hash the one tuple
    assert src.count('plan.archive_files[name], _fp_extra)') == 2
