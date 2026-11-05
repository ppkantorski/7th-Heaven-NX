#!/usr/bin/env python3
"""BUILD 889: New Threat 2.0 (ff7nx_newthreat).

    python3 -m pytest tests/test_newthreat889.py

The mod-dependent tests skip when mods/FF7NT2.0.iro or the dump is absent.

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
import struct
import types

import pytest

import ff7nx_newthreat as N

NT_IRO = os.path.join(nxpaths.ROOT, 'mods', 'FF7NT2.0.iro')


def _mod(mod_id, options=(), folders=(), conds=None, cond_folders=()):
    man = types.SimpleNamespace(
        mod_id=mod_id, folders=list(folders), options=list(options),
        folder_conditions=conds or {}, conditional_folders=set(cond_folders))
    return types.SimpleNamespace(manifest=man, filename='x.iro',
                                 display_name='x', cache='/nonexistent')


def test_features_resolve_ffnx_global_offsets():
    text = ('..\\ff7.exe\n#Mod Identifier\n914B1A = 01\n'
            '#Gil\n+400C00\n31F14F = 1E\n-400C00\n'
            '#Prices\n+401600\n520CC6 = 0A 0B\n-401600\n')
    f = N.features(text)
    assert [x['name'] for x in f] == ['Mod Identifier', 'Gil', 'Prices']
    assert f[0]['patches'] == [(0x914B1A, b'\x01')]
    assert f[1]['patches'] == [(0x71FD4F, b'\x1e')]
    assert f[2]['patches'] == [(0x9222C6, b'\x0a\x0b')]


def test_nothing_happens_without_new_threat():
    other = _mod('09e81530-3f09-46b9-831b-df431b8f319c')
    read = object()
    assert N.find([other], {}) is None
    assert N.reader(read, [other], {}) is read          # same object
    assert N.plan_for([other], {}) is None
    assert N.dropped(other, 'anything') is None
    assert N.setting_conflicts([other], {}) == []


def test_core_option_off_is_not_new_threat():
    opt = types.SimpleNamespace(id='gameplay', default=1)
    nt = _mod(N.NT20_ID, options=[opt])
    assert N.find([nt], {'x.iro': {'gameplay': 1}}) is nt
    assert N.find([nt], {'x.iro': {'gameplay': 0}}) is None


def test_fieldid_conditionals_and_mls_are_left_out():
    nt = _mod(N.NT20_ID, cond_folders={'ConditionalVolcano'},
              conds={'ConditionalVolcano': ('var', 'FieldID', ('set', {507}))})
    import iro
    old = iro.runtime_vars
    iro.runtime_vars = lambda rt: ['FieldID']
    try:
        assert 'live field ID' in N.dropped(nt, 'ConditionalVolcano')
    finally:
        iro.runtime_vars = old
    N.CURRENT = {'mod': 'x.iro', 'mls_code': False}
    try:
        assert 'Multi-Linked' in N.dropped(nt, 'OptionMultiLinkedSlots\\Enabled')
        N.CURRENT['mls_code'] = True
        assert N.dropped(nt, 'OptionMultiLinkedSlots\\Enabled') is None
    finally:
        N.CURRENT = None


def _section1(strings):
    """A minimal field section 1: header, 0 actors, the string table, one
    AKAO block after the strings (to check its offset moves)."""
    body = b''.join(s + b'\xff' for s in strings)
    so = 32
    table = struct.pack('<H', len(strings))
    offs, pos = [], 2 + 2 * len(strings)
    for s in strings:
        offs.append(pos)
        pos += len(s) + 1
    table += struct.pack('<%dH' % len(strings), *offs)
    akao_at = so + len(table) + len(body) + 4      # +4: the AKAO table itself
    head = bytearray(32)
    head[2] = 0                                    # actors
    struct.pack_into('<H', head, 4, so + 4)        # string table offset
    struct.pack_into('<H', head, 6, 1)             # one AKAO block
    sec = bytes(head) + struct.pack('<I', akao_at) + table + body
    assert len(sec) == akao_at
    # (string table starts after the AKAO table here)
    return sec + b'AKAO' + b'\0' * 8


def test_pc_key_glyphs_become_switch_buttons():
    enc = lambda s: bytes(ord(c) - 0x20 for c in s)
    hold = (enc('Hold the ') + b'\xfe\xd3\x95\xfe\xd9\x00\x0f\xfe\xd5\xf9\xfe\xd9'
            + enc(' button'))
    alone = enc('Press ') + b'\xfe\xd2\x94\xfe\xd9'
    word = b'\xfe\xd2\xbe\xfe\xd9\x00\x0f\xfe\xd5' + enc('[UP]') + b'\xfe\xd9'
    sec = _section1([hold, alone, word])
    out, n = N._drop_key_glyphs(sec)
    assert n == 3
    so = struct.unpack_from('<H', out, 4)[0]
    strs = out[so + 2 + 6:].split(b'\xff')
    assert strs[0] == enc('Hold the ') + b'\xfe\xd5\xf9\xfe\xd9' + enc(' button')
    assert strs[1] == enc('Press ') + b'\xfe\xd2\xf6\x1a\xfe\xd9'
    assert strs[2] == b'\xfe\xd2\xf6\x17\xfe\xd9'
    # the AKAO offset followed the shorter strings
    akao = struct.unpack_from('<I', out, 32)[0]
    assert out[akao:akao + 4] == b'AKAO'


@pytest.mark.skipif(not os.path.exists(NT_IRO), reason='needs FF7NT2.0.iro')
def test_real_hext_split():
    import iro
    import ff7nx_pccode
    if ff7nx_pccode._translator() is None:
        pytest.skip('needs the dump and capstone')
    text = iro.read_one(NT_IRO, 'hext\\NT_01.txt').decode('utf-8', 'replace')
    exe = ff7nx_pccode._translator()[0]
    data, code = N.classify(text, exe)
    flat = {va: b for f in data for va, b in f['patches']}
    assert flat[N.FLAG_VA] == b'\x01'          # the 7th Heaven mod flag
    assert 0x9222C6 in flat                     # prices, offset resolved
    assert not any(0x913D00 <= va < 0x913E00 for va in flat), \
        'a code cave leaked into the data channel'
    names = [f['name'] for f in code]
    assert any('Octa' in n for n in names)
