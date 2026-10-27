#!/usr/bin/env python3
"""tests/test_lgp_add.py -- adding entries to an LGP.

BUILD 443. flevel.lgp could only ever have entries REPLACED, because the 3,600
bytes at the head of `middle` are the name LOOKUP TABLE -- a 30x30 grid of
{u16 first TOC index, u16 count} bucketed on the first two characters -- and
adding an entry shifts every index after it. That is why Ninostyle's facial
set (994 new textures) could not be installed.

The mapping was derived from the shipped archives, not from a spec, so the
test that matters is that `build_lookup_table` reproduces the tables that are
already in the game BYTE FOR BYTE. If that holds on 29,811 entries across five
archives with different shapes, the mapping is right.

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

import lgp                                                     # noqa: E402

DUMP = os.path.join(_HERE, 'dump', 'romfs', 'ff7', 'workingdir', 'data')
SHIPPED = ['field/flevel.lgp', 'field/char.lgp', 'battle/battle.lgp',
           'battle/magic.lgp', 'menu/menu_us.lgp', 'world/world_us.lgp']


def _present(rel):
    return os.path.exists(os.path.join(DUMP, *rel.split('/')))


def _toc_names(path):
    with open(path, 'rb') as handle:
        data = handle.read(16 + 4)
        handle.seek(12)
        count = struct.unpack('<i', handle.read(4))[0]
        names = []
        for _ in range(count):
            entry = handle.read(27)
            names.append(entry[:20].split(b'\0')[0].decode('ascii', 'replace'))
        return names, handle.read(lgp.LOOKUP_LEN)


@pytest.mark.parametrize('rel', SHIPPED)
def test_the_shipped_lookup_table_is_reproduced_exactly(rel):
    """THE test. A wrong character mapping cannot survive this."""
    path = os.path.join(DUMP, *rel.split('/'))
    if not os.path.exists(path):
        pytest.skip('no dump here')
    names, real = _toc_names(path)
    assert lgp.build_lookup_table(names) == real, rel


@pytest.mark.parametrize('rel', SHIPPED)
def test_every_bucket_is_one_unbroken_run(rel):
    """
    The invariant `add` has to preserve, and it is NOT sort order: flevel,
    battle, menu_us and magic are not sorted by name at all. What every
    shipped archive does have is each bucket occupying a single run, which is
    the only thing an {index, count} pair can describe.
    """
    path = os.path.join(DUMP, *rel.split('/'))
    if not os.path.exists(path):
        pytest.skip('no dump here')
    names, _ = _toc_names(path)
    last = {}
    broken = []
    for index, name in enumerate(names):
        bucket = lgp.Archive._bucket_of(name)
        if bucket is None:
            continue
        if bucket in last and last[bucket] != index - 1:
            broken.append(bucket)
        last[bucket] = index
    assert broken == [], rel


def _tiny(path, names):
    """A minimal valid LGP holding `names`, each with its name as payload."""
    entries = [(n, n.encode()) for n in names]
    toc = bytearray()
    offset = (lgp.CREATOR_LEN + 4 + len(entries) * lgp.TOC_ENTRY_LEN
              + lgp.LOOKUP_LEN + 2)
    for name, payload in entries:
        toc += struct.pack('<20sIBH', name.encode().ljust(20, b'\0'),
                           offset, 14, 0)
        offset += 24 + len(payload)
    with open(path, 'wb') as handle:
        handle.write(b'\0\0SQUARESOFT')      # the 12-byte creator
        handle.write(struct.pack('<i', len(entries)))
        handle.write(toc)
        handle.write(lgp.build_lookup_table([n for n, _ in entries]))
        handle.write(struct.pack('<H', 0))
        for name, payload in entries:
            handle.write(name.encode().ljust(20, b'\0'))
            handle.write(struct.pack('<I', len(payload)))
            handle.write(payload)
        handle.write(lgp.TERMINATOR)
    return path


def test_an_added_entry_joins_its_own_bucket_and_moves_nobody(tmp_path):
    src = _tiny(str(tmp_path / 'a.lgp'),
                ['aa1', 'aa2', 'ab1', 'zz9'])
    archive = lgp.Archive(src)
    before = [e['name'] for e in archive.entries]
    archive.add('aa3', b'new')
    after = [e['name'] for e in archive.entries]
    assert [n for n in after if n in before] == before
    # it landed at the end of the `aa` run, not appended to the file
    assert after == ['aa1', 'aa2', 'aa3', 'ab1', 'zz9']


def test_an_entry_in_a_brand_new_bucket_is_appended(tmp_path):
    src = _tiny(str(tmp_path / 'b.lgp'), ['aa1', 'ab1'])
    archive = lgp.Archive(src)
    archive.add('qq1', b'new')
    assert [e['name'] for e in archive.entries] == ['aa1', 'ab1', 'qq1']


def test_the_table_is_rebuilt_and_round_trips(tmp_path):
    src = _tiny(str(tmp_path / 'c.lgp'), ['aa1', 'ab1', 'zz9'])
    archive = lgp.Archive(src)
    for name in ('aa2', 'ab2', 'mm1'):
        archive.add(name, name.encode())
    out = str(tmp_path / 'c.out.lgp')
    archive.write(out)
    back = lgp.Archive(out)
    assert sorted(back.names()) == sorted(
        ['aa1', 'aa2', 'ab1', 'ab2', 'mm1', 'zz9'])
    names, real = _toc_names(out)
    assert lgp.build_lookup_table(names) == real
    assert back.middle == archive.middle
    for name in back.names():
        assert back.index[name]['payload'] == name.encode()


def test_a_conflict_table_refuses(tmp_path):
    src = _tiny(str(tmp_path / 'd.lgp'), ['aa1'])
    archive = lgp.Archive(src)
    archive.middle += b'\x01\x02\x03\x04'      # pretend conflicts
    with pytest.raises(lgp.ConflictTableInTheWay):
        archive.add('aa2', b'x')


def test_a_duplicate_and_an_overlong_name_refuse(tmp_path):
    src = _tiny(str(tmp_path / 'e.lgp'), ['aa1'])
    archive = lgp.Archive(src)
    with pytest.raises(ValueError):
        archive.add('aa1', b'x')
    with pytest.raises(ValueError):
        archive.add('a' * 21, b'x')


@pytest.mark.skipif(not _present('field/flevel.lgp'), reason='no dump here')
def test_the_real_flevel_takes_a_thousand_new_entries(tmp_path):
    """
    End to end on the archive this exists for: every original payload
    byte-identical, every original entry in its original relative position,
    and the rebuilt table self-consistent.
    """
    import hashlib
    src = os.path.join(DUMP, 'field', 'flevel.lgp')
    archive = lgp.Archive(src)
    # `index` is keyed lowercase while `name` is raw -- flevel has twelve
    # entries like `blackbgb.XOne`, which is exactly what caught this.
    before = {e['name'].lower(): hashlib.sha1(e['payload']).hexdigest()
              for e in archive.entries}
    order = [e['name'] for e in archive.entries]
    for i in range(400):
        archive.add('zzt_%03d.tex' % i, b'payload-%d' % i)
    out = str(tmp_path / 'flevel.lgp')
    archive.write(out)

    back = lgp.Archive(out)
    assert len(back.entries) == len(order) + 400
    for name, digest in before.items():
        assert hashlib.sha1(back.index[name]['payload']).hexdigest() == digest
    assert [e['name'] for e in back.entries
            if e['name'].lower() in before] == order
    names, real = _toc_names(out)
    assert lgp.build_lookup_table(names) == real
    assert back.middle[lgp.LOOKUP_LEN:] == b'\x00\x00'


# ---------------------------------------------------------- the build path

@pytest.mark.skipif(not _present('field/flevel.lgp'), reason='no dump here')
def test_build_flevel_adds_a_new_loose_asset_raw_and_refuses_a_new_field(
        tmp_path):
    """
    BUILD 444. `_build_flevel` used to skip an unknown name outright --
    `! flevel: no such entry <name>, skipped` -- which is how 994 of
    Ninostyle's facial textures went missing without the build saying so.

    A new LOOSE asset is now stored, and stored RAW: the storage decision
    everywhere else in that function is made by looking at how vanilla stores
    the same name, and a new name has no vanilla counterpart. Raw is right
    because these are textures, and that function's own docstring records
    that LZS-wrapping a texture crashes the game the moment it is used.

    A new FIELD is still refused. Nothing routes to a field flevel did not
    already have, so one appearing here is a routing bug, not an asset.
    """
    import struct as _struct
    import build

    src = os.path.join(DUMP, 'field', 'flevel.lgp')
    texture = tmp_path / 'EYE_TEST_4.TEX'
    texture.write_bytes(b'not a real tex but raw' * 64)
    field = tmp_path / 'zztestfield'
    field.write_bytes(b'\0' * 64)

    romfs = tmp_path / 'romfs'
    (romfs / 'field').mkdir(parents=True)
    lines = []
    out = build._build_flevel(
        src, {},
        {'eye_test_4.tex': (str(texture), None),
         'zztestfield': (str(field), None)},
        str(romfs), lines.append)
    assert out and os.path.exists(out)

    built = lgp.Archive(out)
    assert 'eye_test_4.tex' in built.index
    stored = built.index['eye_test_4.tex']['payload']
    assert stored == texture.read_bytes()
    # raw, not LZS-wrapped: a wrapped payload's first u32 is its own length
    assert _struct.unpack('<I', stored[:4])[0] != len(stored) - 4

    assert 'zztestfield' not in built.index
    assert any('new FIELD' in line for line in lines), lines

    names, real = _toc_names(out)
    assert lgp.build_lookup_table(names) == real
