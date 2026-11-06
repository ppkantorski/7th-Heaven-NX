#!/usr/bin/env python3
"""BUILD 894: sdout/build.txt (ff7nx_buildinfo).

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
import time
import types

import iro
import ff7nx_buildinfo as B


def _mod(name, version, options, filename):
    man = types.SimpleNamespace(version=version, options=options)
    return types.SimpleNamespace(display_name=name, manifest=man,
                                 filename=filename)


def _sample():
    a = _mod('[Tsunamods] Echo-S 7', '1.24', [
        iro.Option('Auto', 'Auto Text Advance', [(0, 'No'), (1, 'Yes')], 1),
        iro.Option('hdr', '======Battle Menu Options======', [(0, '0')], 0),
        iro.Option('Grunts', 'Battle Grunts', [(0, ''), (1, '')], 1),
        iro.Option('X', 'An option with a very long name that will not fit '
                   'in one line of the file at all', [(0, 'OFF'), (1, 'ON')],
                   1),
    ], 'Echo-s.iro')
    b = _mod('Cosmos Gaia', 'v1.02', [], 'CosmosGaia.iro')
    c = _mod('New Threat 2.0', '2.0999993', [], 'FF7NT2.0.iro')
    return [a, b], {'Echo-s.iro': {'Auto': 0, 'Grunts': 1}}, [a, b, c]


def test_render_is_neat_and_exact():
    mods, settings, installed = _sample()
    when = time.mktime((2026, 11, 6, 14, 5, 0, 0, 0, -1))
    text = B.render('1.1.0', mods, settings, installed, when)
    lines = text.splitlines()
    assert 'Built with 7th Heaven NX v1.1.0.' in lines
    assert 'Built on Friday, November 6, 2026 at 2:05 PM.' in lines
    assert '2 mods enabled, highest priority first.' in lines
    assert all(len(l) <= B.WIDTH for l in lines)
    text.encode('ascii')
    head = next(l for l in lines if l.startswith('1. '))
    assert head.endswith('v1.24') and len(head) == B.WIDTH
    assert any(l.startswith('2. Cosmos Gaia') and l.endswith('v1.02')
               for l in lines)
    auto = next(l for l in lines if 'Auto Text Advance' in l)
    assert auto.endswith(' No'), 'the value AS BUILT, not the default'
    assert '    [Battle Menu Options]' in lines
    assert next(l for l in lines if 'Battle Grunts' in l).endswith(' On')
    assert next(l for l in lines if l.strip().startswith('An option')
                ).endswith(' On')
    i = lines.index('Installed but not enabled:')
    assert '    New Threat 2.0' in lines[i:]
    assert '    Cosmos Gaia' not in lines[i:]


def test_write_replaces_the_file(tmp_path):
    mods, settings, installed = _sample()
    p = B.write(str(tmp_path), '1.1.0', mods, settings, installed)
    assert os.path.basename(p) == 'build.txt'
    first = open(p).read()
    B.write(str(tmp_path), '1.1.1', mods, settings, installed)
    assert 'v1.1.1.' in open(p).read() and first != open(p).read()
    assert os.listdir(str(tmp_path)) == ['build.txt']


def test_build_registers_it_before_prune():
    src = open(os.path.join(_nx_root, '7th_heaven_nx.py'),
               encoding='utf-8').read()
    w = src.index('ff7nx_buildinfo.write(')
    assert w < src.index('build.prune_stale(SDOUT_DIR, produced, log)')
    assert 'produced.append(ff7nx_buildinfo.write(' in src
