"""Regression checks for the build-250 warning-counter migration.

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
import json

import build_guard as G


def test_old_warning_definition_migrates_once_without_hiding_named_counters(tmp_path):
    path = tmp_path / 'counters.json'
    path.write_text(json.dumps({
        'label': '250',
        'counters': {'warning lines': 93, 'inplace pages': 10},
    }))
    emitted = []
    guard = G.CounterGuard(emitted.append, path=str(path), label='251')
    guard.log('IN-PLACE PARALLAX: 10 32-unit page(s) in 2 field(s)')
    guard.log('! one real warning under the corrected definition')
    before = len(emitted)
    guard.finish()
    generated = emitted[before:]
    assert any('extractor baseline migrated' in line for line in generated)
    assert not any('!! COUNTER GUARD' in line for line in generated)
    assert json.loads(path.read_text())['extract_version'] == 2


def test_current_warning_definition_still_detects_future_movement(tmp_path):
    path = tmp_path / 'counters.json'
    path.write_text(json.dumps({
        'label': '251', 'extract_version': G.COUNTER_EXTRACT_VERSION,
        'counters': {'warning lines': 0, 'inplace pages': 10},
    }))
    emitted = []
    guard = G.CounterGuard(emitted.append, path=str(path), label='252')
    guard.log('IN-PLACE PARALLAX: 10 32-unit page(s) in 2 field(s)')
    guard.log('! a newly introduced warning')
    before = len(emitted)
    guard.finish()
    generated = emitted[before:]
    assert any('!! COUNTER GUARD' in line for line in generated)
    assert any('warning lines: 0 -> 1' in line for line in generated)


def test_flevel_rebuild_presence_changes_are_input_driven(tmp_path):
    """A FAST-REUSE -> rebuild transition must not look like code drift."""
    path = tmp_path / 'counters.json'
    path.write_text(json.dumps({
        'label': '305', 'extract_version': G.COUNTER_EXTRACT_VERSION,
        'counters': {'warning lines': 89},
    }))
    emitted = []
    guard = G.CounterGuard(emitted.append, path=str(path), label='306')
    guard.log('building flevel.lgp ...')
    guard.log('PALETTE RANGE -- 20,537 tile(s) across 126 cell(s) in 9 field(s)')
    guard.log('margin art: 529,642 cell(s) of Cosmos art in 667 of 741 fields')
    guard.log('! a conversion warning')
    before = len(emitted)
    guard.finish()
    generated = emitted[before:]
    assert any('input counter(s) changed' in line for line in generated)
    assert not any('!! COUNTER GUARD' in line for line in generated)


def test_flevel_rebuild_still_detects_value_changes(tmp_path):
    """Once both builds measured flevel, ordinary counter drift stays strict."""
    path = tmp_path / 'counters.json'
    path.write_text(json.dumps({
        'label': '305', 'extract_version': G.COUNTER_EXTRACT_VERSION,
        'counters': {'margin art cells': 100, 'warning lines': 0},
    }))
    emitted = []
    guard = G.CounterGuard(emitted.append, path=str(path), label='306')
    guard.log('building flevel.lgp ...')
    guard.log('margin art: 200 cell(s) of Cosmos art in 3 of 741 fields')
    before = len(emitted)
    guard.finish()
    generated = emitted[before:]
    assert any('!! COUNTER GUARD' in line for line in generated)
    assert any('margin art cells: 100 -> 200' in line for line in generated)


def test_archive_cache_keep_counts_as_flevel_reuse():
    """BUILD 577: log 374 kept flevel through the archive cache, not FAST
    REUSE, and the guard printed 21 false alarms."""
    import build_guard as G
    assert G._flevel_archive_mode(
        '  flevel.lgp: unchanged since the last build, kept (1 bytes).') \
        == 'reused'
