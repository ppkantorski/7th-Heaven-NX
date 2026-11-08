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
    on = next(l for l in lines if l.startswith('Built on '))
    zone = B._zone(time.localtime(when))
    assert on == 'Built on Friday, November 6, 2026 at 2:05 PM %s.' % zone
    assert zone and ' ' not in zone
    assert '2 mods enabled, highest priority first.' in lines
    W = B.width_of(text)
    assert B.WIDTH <= W <= B.MAX_WIDTH
    assert all(len(l) <= W for l in lines)
    text.encode('ascii')
    head = next(l for l in lines if l.startswith('1. '))
    assert head.endswith('v1.24') and len(head) == W
    assert any(l.startswith('2. Cosmos Gaia') and l.endswith('v1.02')
               for l in lines)
    auto = next(l for l in lines if 'Auto Text Advance' in l)
    assert auto.endswith(' No'), 'the value AS BUILT, not the default'
    assert '    [Battle Menu Options]' in lines
    assert next(l for l in lines if 'Battle Grunts' in l).endswith(' On')
    assert next(l for l in lines if l.strip().startswith('An option')
                ).endswith(' On')
    # 11-07: installed-but-disabled mods are not listed at all
    assert 'Installed but not enabled:' not in lines
    assert not any('New Threat' in l for l in lines)


def test_widens_so_long_options_fit():
    """11-07: 'Cosmos Limit Bre... .. Upscaled + Extended Backgrounds
    (Recommended)' -- the file widens instead of cutting the name."""
    m = _mod('Cosmos Gaia', 'v1.02', [
        iro.Option('BG', 'Cosmos Limit Break Backgrounds',
                   [(0, 'Off'), (1, 'Upscaled + Extended Backgrounds '
                                    '(Recommended)')], 1)], 'CosmosGaia.iro')
    text = B.render('1.1.1', [m], {}, [m], time.time())
    line = next(l for l in text.splitlines() if 'Limit Break' in l)
    assert 'Cosmos Limit Break Backgrounds ..' in line
    assert line.endswith('Upscaled + Extended Backgrounds (Recommended)')
    assert B.width_of(text) == len(line) > B.WIDTH
    # a short file stays at the usual width
    s = _mod('Cosmos Gaia', 'v1.02', [], 'CosmosGaia.iro')
    assert B.width_of(B.render('1.1.1', [s], {}, [s])) == B.WIDTH


def test_zone_names():
    class LT:
        tm_isdst = 0
        def __init__(self, zone, off):
            self.tm_zone, self.tm_gmtoff = zone, off
    assert B._zone(LT('PST', -8 * 3600)) == 'PST'
    assert B._zone(LT('Pacific Standard Time', -8 * 3600)) == 'PST'
    assert B._zone(LT('W. Europe Standard Time', 3600)) == 'WEST'
    assert B._zone(LT('+0530', 5 * 3600 + 1800)) == 'UTC+05:30'
    assert B._zone(LT('', -3 * 3600)) == 'UTC-03:00'


def test_write_replaces_the_file(tmp_path):
    mods, settings, installed = _sample()
    p = B.write(str(tmp_path), '1.1.0', mods, settings, installed)
    assert p == os.path.join(str(tmp_path), 'atmosphere', 'contents',
                             '0100A5B00BDC6000', 'build.txt')
    first = open(p).read()
    B.write(str(tmp_path), '1.1.1', mods, settings, installed)
    assert 'v1.1.1.' in open(p).read() and first != open(p).read()
    assert os.listdir(os.path.dirname(p)) == ['build.txt']
    assert os.listdir(str(tmp_path)) == ['atmosphere']


def test_title_id_matches_the_build():
    import build
    assert B.TITLE_ID == build.TITLE_ID


def test_build_registers_it_before_prune():
    src = open(os.path.join(_nx_root, '7th_heaven_nx.py'),
               encoding='utf-8').read()
    w = src.index('ff7nx_buildinfo.write(')
    assert w < src.index('build.prune_stale(SDOUT_DIR, produced, log)')
    assert 'produced.append(ff7nx_buildinfo.write(' in src
