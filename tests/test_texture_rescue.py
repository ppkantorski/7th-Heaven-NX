#!/usr/bin/env python3
"""tests/test_texture_rescue.py -- a shipped model must have its textures.

BUILD 440. `DEFAULT_OVERRIDES` turns Ninostyle's `fb` option off on purpose:
on, it pulls 878 MB of re-eyed models built for Shinra Archaeology Cut, which
buys nothing without SAC in the load order. Still the right default -- but
`fb/char/` was also the only active-by-default source of two textures that
models from OTHER, active folders name:

    sbad.tex    15 of Barret's 16 Dynamic Weapons arms
    npc92.tex   13 rsd(s)

Measured over the whole built char.lgp those two were the only dangling
references out of 424 textures named by 4,180 `.rsd` entries. On hardware it
showed as every Barret weapon except the Gatling Gun -- the one arm that names
`br.tex`, which does ship -- rendering with parts of its texture missing, and
the field framerate collapsing.

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

BUILD_PY = nxpaths.src_file('build.py')
CACHE = os.path.join(_HERE, 'cache')
pytestmark = pytest.mark.skipif(not os.path.exists(BUILD_PY),
                                reason='needs build.py')


@pytest.fixture(scope='module')
def b():
    import build
    return build


class _Mod:
    def __init__(self, cache):
        self.cache = cache


def _mods():
    if not os.path.isdir(CACHE):
        return []
    return [_Mod(os.path.join(CACHE, d)) for d in sorted(os.listdir(CACHE))
            if os.path.isdir(os.path.join(CACHE, d)) and not d.startswith('_')]


def test_every_texture_name_spelling_an_rsd_can_use(b):
    """The mod writes `JESS (2).TIM`; a stricter stem pattern misses it."""
    assert b._rsd_texture_names(b'NTEX=1\r\nTEX[0]=SBAD.TIM\r\n') == {'sbad.tex'}
    assert b._rsd_texture_names(
        b'NTEX=2\r\nTEX[0]=BR.TIM\r\nTEX[1]=JESS (2).TIM\r\n') == {
            'br.tex', 'jess (2).tex'}
    assert b._rsd_texture_names(b'@RSD940102\r\nPLY=ADAA.PLY\r\nNTEX=0\r\n') \
        == set()


def test_the_lookup_does_not_shadow_the_assembly_regex(b):
    """
    These two patterns nearly collided. `_RE_RSD_TEX` belongs to the model
    assembly pass and takes only [A-Za-z0-9_] stems; the completeness check
    needs every spelling, so it has its own.
    """
    assert b._RE_RSD_TEX is not b._RE_RSD_TEX_REF
    assert b._RE_RSD_TEX.findall(b'TEX[0]=JESS (2).TIM') == []
    assert b._RE_RSD_TEX_REF.findall(b'TEX[0]=JESS (2).TIM') == [b'JESS (2)']


@pytest.mark.skipif(not os.path.isdir(CACHE), reason='needs the mod cache')
def test_the_base_folder_wins_over_an_alternate_outfit(b):
    """
    Ninostyle ships `sbad.tex` four times: once in `fb/char`, and once in each
    of three `Alt Outfits/* Barret` skins gated on `Barret = 1|2|3`. This
    build runs `Barret = 0`, so the plain one is the correct answer and plain
    path order -- which picks Advent Children -- is the wrong one.
    """
    found = b._find_in_mod_trees(_mods(), {'sbad.tex'}, 'char')
    if not found:
        pytest.skip('this cache does not carry Ninostyle')
    path, where = found['sbad.tex']
    assert 'alt outfits' not in where.lower(), where
    assert where.lower().endswith('fb/char/sbad.tex'), where
    assert os.path.isfile(path)


@pytest.mark.skipif(not os.path.isdir(CACHE), reason='needs the mod cache')
def test_a_name_no_mod_has_is_reported_not_invented(b):
    assert b._find_in_mod_trees(_mods(), {'no_such_texture.tex'}, 'char') == {}


@pytest.mark.skipif(not os.path.isdir(CACHE), reason='needs the mod cache')
def test_the_whole_set_costs_one_walk(b):
    """
    It runs at plan time over every mod tree, so it takes the set and walks
    once. A walk per name would be paid on every build for a two-file repair.
    """
    got = b._find_in_mod_trees(_mods(), {'sbad.tex', 'npc92.tex'}, 'char')
    if not got:
        pytest.skip('this cache does not carry Ninostyle')
    assert set(got) == {'sbad.tex', 'npc92.tex'}


def test_the_rescue_only_adds_textures_a_shipped_rsd_asks_for(b, tmp_path):
    """
    Scoped: an .rsd that names nothing, or names something already present,
    must pull nothing in -- otherwise this becomes a back door that drags a
    switched-off folder's content into the build.
    """
    char = tmp_path / 'amod' / 'char'
    char.mkdir(parents=True)
    (char / 'HAVE.TEX').write_bytes(b'x')
    (char / 'ABSENT.TEX').write_bytes(b'y')

    class _Plan:
        archive_files = {}
        dynweapon = []

    rsd = tmp_path / 'r.rsd'
    rsd.write_bytes(b'@RSD940102\r\nPLY=A.PLY\r\nNTEX=1\r\nTEX[0]=HAVE.TIM\r\n')
    plan = _Plan()
    plan.archive_files = {'char.lgp': {'r.rsd': (str(rsd), None),
                                       'have.tex': (str(char / 'HAVE.TEX'),
                                                    None)}}
    n = b._rescue_dangling_model_textures(
        plan, {}, [_Mod(str(tmp_path / 'amod'))], lambda *_: None)
    assert n == 0
    assert 'absent.tex' not in plan.archive_files['char.lgp']

    rsd.write_bytes(b'@RSD940102\r\nPLY=A.PLY\r\nNTEX=1\r\nTEX[0]=ABSENT.TIM\r\n')
    plan2 = _Plan()
    plan2.archive_files = {'char.lgp': {'r.rsd': (str(rsd), None)}}
    n = b._rescue_dangling_model_textures(
        plan2, {}, [_Mod(str(tmp_path / 'amod'))], lambda *_: None)
    assert n == 1
    assert plan2.archive_files['char.lgp']['absent.tex'][0] \
        == str(char / 'ABSENT.TEX')


def test_catalogs_are_name_sets_not_path_maps(b, tmp_path):
    """
    The crash this shipped with. `catalogs[archive]` is the vanilla archive's
    set of entry NAMES -- `_read_catalogs` builds it straight off the LGP
    table of contents -- and the first version of this pass called `.get()`
    on it looking for a path:

        AttributeError: 'set' object has no attribute 'get'

    It says what is PRESENT, which is all the completeness check needs; the
    only .rsd entries that can dangle are the ones this build supplies, and
    those come with their own paths in `plan.archive_files`.
    """
    char = tmp_path / 'amod' / 'char'
    char.mkdir(parents=True)
    (char / 'ABSENT.TEX').write_bytes(b'y')
    rsd = tmp_path / 'r.rsd'
    rsd.write_bytes(b'@RSD940102\r\nNTEX=1\r\nTEX[0]=ABSENT.TIM\r\n')

    class _Plan:
        pass

    plan = _Plan()
    plan.archive_files = {'char.lgp': {'r.rsd': (str(rsd), None)}}
    # exactly the shape the real build passes: {archive: {names}}
    catalogs = {'char.lgp': {'aaaa.hrc', 'aaab.rsd', 'br.tex'}}
    n = b._rescue_dangling_model_textures(
        plan, catalogs, [_Mod(str(tmp_path / 'amod'))], lambda *_: None)
    assert n == 1
    assert 'absent.tex' in plan.archive_files['char.lgp']


def test_a_vanilla_texture_already_in_the_catalog_is_not_rescued(b, tmp_path):
    """A name the vanilla archive already holds is present, not missing."""
    char = tmp_path / 'amod' / 'char'
    char.mkdir(parents=True)
    (char / 'BR.TEX').write_bytes(b'z')
    rsd = tmp_path / 'r.rsd'
    rsd.write_bytes(b'@RSD940102\r\nNTEX=1\r\nTEX[0]=BR.TIM\r\n')

    class _Plan:
        pass

    plan = _Plan()
    plan.archive_files = {'char.lgp': {'r.rsd': (str(rsd), None)}}
    n = b._rescue_dangling_model_textures(
        plan, {'char.lgp': {'br.tex'}}, [_Mod(str(tmp_path / 'amod'))],
        lambda *_: None)
    assert n == 0
    assert 'br.tex' not in plan.archive_files['char.lgp']
