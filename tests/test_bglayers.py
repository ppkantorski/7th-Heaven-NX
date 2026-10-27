#!/usr/bin/env python3
"""tests/test_bglayers.py -- the battleground layer-hide diagnostic.

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
import shutil
import struct
import sys

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ff7nx_bglayers as B                                     # noqa: E402
import nxmap                                                   # noqa: E402

MAIN = os.path.join(_HERE, 'game_data_files', 'exefs', 'main')
pytestmark = pytest.mark.skipif(
    not os.path.exists(MAIN), reason='needs game_data_files/exefs/main')


@pytest.fixture(scope='module')
def img():
    return nxmap.Main(MAIN).img


def test_the_stock_word_is_the_mask_seven_encoding(img):
    """The positive control: the word this module synthesises for mask 7 must
    BE the stock word. If the encoder is wrong, this fails before anything is
    written."""
    assert struct.unpack_from('<I', img, B.HOOK)[0] == B.STOCK
    assert B.WORDS[7] == B.STOCK
    assert B.verify(img) == []
    assert B.read_state(img) == 'mask 7'


def test_every_mask_applies_and_reverts_byte_exactly(tmp_path, monkeypatch):
    for m in (0, 1, 3, 7):
        monkeypatch.setenv(B.LAYERS_ENV, str(m))
        out = tmp_path / ('main%d' % m)
        shutil.copy(MAIN, out)
        assert B.apply_all(out, log=lambda *_: None) == 0
        assert B.read_state(nxmap.Main(str(out)).img) == 'mask %d' % m
        assert B.apply_all(out, log=lambda *_: None) == 0      # idempotent
        assert B.apply_all(out, revert=True, log=lambda *_: None) == 0
        assert out.read_bytes() == open(MAIN, 'rb').read()


def test_mask_seven_is_a_no_op(monkeypatch):
    monkeypatch.setenv(B.LAYERS_ENV, '7')
    assert B.enabled() is False
    monkeypatch.setenv(B.LAYERS_ENV, '0')
    assert B.enabled() is True


def test_a_nonsense_mask_falls_back_rather_than_writing_garbage(monkeypatch):
    for bad in ('5', '9', 'banana', ''):
        monkeypatch.setenv(B.LAYERS_ENV, bad)
        assert B.mask() in B.WORDS
