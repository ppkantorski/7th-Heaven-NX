#!/usr/bin/env python3
"""tests/test_mod_defaults.py -- the baked mod settings.

data/default_mod_settings.json is what Install Mods > Restore defaults puts
back, and what a supported mod that settings.json does not list yet starts
with. It must cover exactly the mods in data/mod_sources.json, under the
same file names, and the GUI helpers must read it and seed only mods
settings.json does not mention.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import json
import os
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _gui():
    """The helpers from 7th_heaven_nx.py, without importing the GUI."""
    import ast
    path = os.path.join(ROOT, '7th_heaven_nx.py')
    tree = ast.parse(open(path, encoding='utf-8').read())
    want = {'default_mod_settings', 'seed_mod_defaults', 'saved_enabled',
            '_mod_options_from_saved', '_manifest_id'}
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)
           and n.name in want]
    assert {f.name for f in fns} == want
    ns = {'json': json, 'os': os, 'NEW_MOD_ENABLED': False,
          'DEFAULT_MOD_SETTINGS': os.path.join(
              ROOT, 'data', 'default_mod_settings.json')}
    exec(compile(ast.Module(fns, []), path, 'exec'), ns)
    return types.SimpleNamespace(**{k: ns[k] for k in want})


@pytest.fixture(scope='module')
def gui():
    return _gui()


def _load(name):
    with open(os.path.join(ROOT, 'data', name), encoding='utf-8') as fh:
        return json.load(fh)


def test_defaults_cover_exactly_the_supported_mods():
    sources = [m['file'] for m in _load('mod_sources.json')['mods']]
    baked = _load('default_mod_settings.json')['mods']
    assert len(sources) == 19
    assert list(baked) == sources, 'same mods, same order as mod_sources'
    for name, entry in baked.items():
        assert set(entry) == {'enabled', 'options'}, name
        assert isinstance(entry['enabled'], bool), name
        assert entry['options'] and all(
            isinstance(v, int) and not isinstance(v, bool)
            for v in entry['options'].values()), name


def test_default_mod_settings_reads_the_file(gui):
    got = gui.default_mod_settings()
    assert got == {k: {'enabled': v['enabled'], 'options': v['options']}
                   for k, v in _load('default_mod_settings.json')['mods']
                   .items()}
    # A copy each time: editing one result never edits the next.
    first = next(iter(got))
    got[first]['options']['__probe__'] = 1
    assert '__probe__' not in gui.default_mod_settings()[first]['options']


def test_default_mod_settings_missing_file(gui, tmp_path, monkeypatch):
    g = gui.default_mod_settings.__globals__
    monkeypatch.setitem(g, 'DEFAULT_MOD_SETTINGS',
                        str(tmp_path / 'absent.json'))
    assert gui.default_mod_settings() == {}
    bad = tmp_path / 'bad.json'
    bad.write_text('{not json')
    monkeypatch.setitem(g, 'DEFAULT_MOD_SETTINGS', str(bad))
    assert gui.default_mod_settings() == {}


def test_seed_only_fills_mods_settings_does_not_list(gui):
    factory = gui.default_mod_settings()
    names = list(factory)
    listed, unlisted = names[0], names[1]
    mods = [types.SimpleNamespace(filename=n)
            for n in (listed, unlisted, 'Some other mod.iro')]
    mine = {'enabled': False, 'options': {'x': 7}}
    saved = {'__global__': {'fps_60': True}, listed: dict(mine)}
    assert gui.seed_mod_defaults(saved, mods) is saved
    assert saved[listed] == mine, 'a listed mod keeps what the user chose'
    assert saved[unlisted] == factory[unlisted]
    assert 'Some other mod.iro' not in saved, 'unsupported mods stay new'
    assert saved['__global__'] == {'fps_60': True}


def test_seed_then_merge_keeps_new_manifest_options(gui):
    factory = gui.default_mod_settings()
    name = next(iter(factory))
    manifest = types.SimpleNamespace(
        defaults=lambda: {'__added_later__': 3,
                          **{k: 0 for k in factory[name]['options']}})
    mod = types.SimpleNamespace(filename=name, manifest=manifest)
    saved = gui.seed_mod_defaults({}, [mod])
    merged = gui._mod_options_from_saved(mod, saved)
    assert merged['__added_later__'] == 3
    for k, v in factory[name]['options'].items():
        assert merged[k] == v
    assert gui.saved_enabled(saved, mod) is factory[name]['enabled']
