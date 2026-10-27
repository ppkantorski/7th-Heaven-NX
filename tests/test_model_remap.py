#!/usr/bin/env python3
"""tests/test_model_remap.py -- the pinball machine is a man, the sequel.

BUILD 445. An entity binds to a model by INDEX into section 3, so wherever
Echo-S edits that list its script and the Switch loader disagree. `mds7pb_1`
was fixed by importing Echo-S's section 3 alongside its script, guarded by a
NAME check -- this port keys models per field
(`mds7pb_1fieldbg_pinbl.char`), so a name it does not own means refuse.

`mds7pb_2`, the floor below, defeats that: Echo-S RENAMES the models there.

    stock  ... 6 sub_marine  7 nible_camera  8 fieldbg_mtra8  9 fieldbg_pinbl
    echo   ... 6 sub_marine  7 Pinball       8 Shake

Nothing matches by name, so the loader was refused and Echo-S's script asked
for model 7 and got `nible_camera` -- a person. The 8-byte HRC id is the match
the names are not: `Pinball` carries CZED.HRC, which IS `fieldbg_pinbl`, and
`Shake` carries CYIF.HRC, `nible_camera`.

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
import struct
import sys

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import echo_s_flevel as EF                                     # noqa: E402
import lgp                                                     # noqa: E402

VANILLA = os.path.join(_HERE, 'dump', 'romfs', 'ff7', 'workingdir', 'data',
                       'field', 'flevel.lgp')
ECHO = os.path.join(_HERE, 'cache', 'Echo-s', 'Echo-S', 'flevel.lgp')
pytestmark = pytest.mark.skipif(
    not os.path.exists(VANILLA) or not os.path.isdir(ECHO),
    reason='needs the dump and the Echo-S cache')


def _sections(field):
    archive = lgp.Archive(VANILLA)
    stock = lgp.split_sections(archive.decompressed(archive.index[field]))
    echo = lgp.split_sections(lgp.lzs_decompress(
        open(os.path.join(ECHO, field), 'rb').read()[4:]))
    return echo[2], stock[2]


def test_the_lower_pinball_floor_is_remapped_not_refused():
    echo3, stock3 = _sections('mds7pb_2')
    assert not EF._model_loader_is_portable(echo3, stock3), \
        'the name check must still refuse this -- that is the premise'
    out = EF.remap_model_loader(echo3, stock3)
    assert out is not None and out is not EF.EQUIVALENT_MODEL_LOADER
    names = [r[0] for r in EF.model_loader_records(out)]
    # Echo-S's script binds its Pinball entity to model 7 and Camera to 8
    assert names[7] == 'mds7pb_2fieldbg_pinbl.char', names
    assert names[8] == 'mds7pb_2nible_camera.char', names
    assert len(names) == len(EF.model_loader_records(echo3))


def test_the_remap_keeps_our_own_records_verbatim():
    """
    Echo-S strips the animation extensions (`CZFF` for our `CZFF.tor`), so the
    point is to take OUR record whole, not to rewrite Echo-S's.
    """
    echo3, stock3 = _sections('mds7pb_2')
    out = EF.remap_model_loader(echo3, stock3)
    ours = {r[1]: stock3[r[2][0]:r[2][1]] for r in
            EF.model_loader_records(stock3)}
    for _name, hrc, (start, end) in EF.model_loader_records(out):
        assert bytes(out[start:end]) == ours[hrc], hrc


def test_the_merge_actually_installs_it():
    archive = lgp.Archive(VANILLA)
    stock = archive.index['mds7pb_2']['payload']
    echo = open(os.path.join(ECHO, 'mds7pb_2'), 'rb').read()
    built, imported = EF.merge_field_payload(stock, echo,
                                             field_name='mds7pb_2')
    assert 3 in imported, imported
    names = EF.model_loader_names(lgp.split_sections(built)[2])
    assert names[7] == 'mds7pb_2fieldbg_pinbl.char', names
    assert 'mds7pb_2' in EF.REMAPPED_MODEL_LOADERS


def test_an_hrc_we_do_not_have_is_still_refused():
    """
    A field borrowing a model from ANOTHER field cannot be remapped -- the
    asset is not here to point at. Those must stay refused and reported, not
    silently matched to something else.
    """
    echo3, stock3 = _sections('mds6_2')
    out = EF.remap_model_loader(echo3, stock3)
    assert out is None
    assert EF._model_loader_is_portable(echo3, stock3) is False


def test_an_equivalent_loader_is_reported_as_such():
    """
    `blin67_2` names its models differently but every index already means the
    same asset here. Calling that "unportable" sent me looking for a model bug
    that does not exist.
    """
    echo3, stock3 = _sections('blin67_2')
    assert EF.remap_model_loader(echo3, stock3) is EF.EQUIVALENT_MODEL_LOADER


def test_mds7pb_1_is_untouched_by_the_new_path():
    """The floor that already works keeps working, by the old route."""
    echo3, stock3 = _sections('mds7pb_1')
    assert EF._model_loader_is_portable(echo3, stock3) is True
