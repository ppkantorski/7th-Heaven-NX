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
    sources = [m['file'] for m in _load('mod_sources.json')['mods']
               if not m.get('builtin')]
    baked = _load('default_mod_settings.json')['mods']
    assert len(sources) == 18
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


def test_build902_double_input_gone_arranged_opt_in():
    """BUILD 902. Double Input Bugfix is built in (Settings > Controls), so it
    is not offered; Arranged Soundtrack is offered but starts off and
    unticked -- it replaces Echo-S's own soundtrack, so it is opt-in."""
    sources = [m for m in _load('mod_sources.json')['mods']
               if not m.get('builtin')]
    baked = _load('default_mod_settings.json')['mods']
    assert not any(m['id'] == '22ed7aef-254c-49cf-923f-32c146f1597f'
                   for m in sources)
    assert 'Double-input-fix' not in baked
    arr = [m for m in sources
           if m['id'] == 'ed5a8c57-56bf-4cf1-acfa-8e5347f27463']
    assert len(arr) == 1 and arr[0].get('selected') is False
    assert baked[arr[0]['file']]['enabled'] is False
    assert baked[arr[0]['file']]['options']['TSM Arranged'] == 1, \
        'turned on, it plays its music'
    assert all(m.get('selected', True) for m in sources if m is not arr[0]), \
        'every other mod still starts ticked'


def test_build905_double_input_keeps_its_tested_place():
    """BUILD 905. Taking Double Input Bugfix out of mod_sources.json made an
    installed copy an UNLISTED mod, so the category auto-sort put it at the
    top (Miscellaneous ranks first) and it became draggable. It is listed
    again, marked builtin: its place is the tested one, between Cosmo FMV and
    the idle animations, but load_sources() never offers it."""
    import ff7nx_download as dl
    full = dl.load_sources(include_builtin=True)
    files = [m['file'] for m in full]
    i = files.index('Double-input-fix')
    assert files[i - 1] == 'CosmosFMV30.iro'
    assert files[i + 1].startswith('Tsunamods__Idle_Animations')
    assert full[i].get('builtin') is True
    assert 'Double-input-fix' not in [m['file'] for m in dl.load_sources()]

    class M:
        def __init__(self, fn):
            self.filename, self.display_name, self.manifest = fn, fn, None
    mods = [M('Double-input-fix')] + [M(f) for f in files
                                      if f != 'Double-input-fix']
    order = _ordering()
    out = [m.filename for m in order.apply_reference_order(mods)]
    assert out == files, 'every listed mod, Double Input included, in place'
    assert 'Double-input-fix' in order.reference_listed(mods)


def _ordering():
    """apply_reference_order / reference_listed from 7th_heaven_nx.py, fed
    the FULL source list as the GUI's _reference_sources() does."""
    import ast
    import ff7nx_download as dl
    path = os.path.join(ROOT, '7th_heaven_nx.py')
    tree = ast.parse(open(path, encoding='utf-8').read())
    want = {'apply_reference_order', 'reference_listed', '_manifest_id',
            'mod_load_rank', 'mod_category', '_name_key'}
    nodes = [n for n in tree.body
             if (isinstance(n, ast.FunctionDef) and n.name in want)
             or (isinstance(n, ast.Assign) and any(
                 getattr(t, 'id', '') in ('MOD_LOAD_ORDER', 'UNKNOWN_ORDER')
                 for t in n.targets))]
    ns = {'json': json, 'os': os, 're': __import__('re'),
          'unicodedata': __import__('unicodedata'),
          '_reference_sources': lambda: dl.load_sources(include_builtin=True)}
    exec(compile(ast.Module(nodes, []), path, 'exec'), ns)
    return types.SimpleNamespace(**{k: ns[k] for k in want})
