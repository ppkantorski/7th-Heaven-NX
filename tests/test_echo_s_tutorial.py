"""Structural tests for Echo-S's field-scoped tutorial redirect.

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
import tempfile

import a64 as A
import echo_s_tutorial as tutorial
from ff7nx_audio_cave import segments as _segments


def _branch_target(pc, word):
    imm = word & 0x03FFFFFF
    if imm & 0x02000000:
        imm -= 0x04000000
    return pc + (imm << 2)


def test_redirect_cave_contains_exact_index_and_field_gate():
    assert tutorial.MDS7PB_1_FIELD_ID == 0x09A
    base = 0x200000
    words = tutorial._build_redirect_cave(base, lambda index: base + index * 4)
    assert words[0] == tutorial.PMVIE_INDEX_ORIG
    assert A.cmp_imm(8, tutorial.REQUESTED_INDEX) in words
    assert A.cmp_imm(9, tutorial.MDS7PB_1_FIELD_ID) in words
    assert A.movz(8, tutorial.REDIRECT_INDEX) in words
    assert _branch_target(base + 4 * (len(words) - 1), words[-1]) == \
        tutorial.PMVIE_INDEX_RESUME


def test_patch_composes_with_stock_main_when_dump_is_present():
    source = os.path.join('dump', 'exefs', 'main')
    if not os.path.isfile(source):
        return
    with tempfile.TemporaryDirectory() as tmp:
        dest = os.path.join(tmp, 'main')
        report = tutorial.patch_main(source, dest)
        with open(dest, 'rb') as handle:
            _segs, raw = _segments(handle.read())
        hook, = struct.unpack_from('<I', raw[0], tutorial.PMVIE_INDEX_HOOK)
        assert _branch_target(tutorial.PMVIE_INDEX_HOOK, hook) == report['entry']
        assert report['requested_index'] == 20
        assert report['redirect_index'] == 1
        assert report['field_id'] == 0x09A
