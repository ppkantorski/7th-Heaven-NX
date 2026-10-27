"""BUILD 577: same-name, same-size, same-second option files get distinct
cache signatures; everything else keeps the old one.

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
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import build as B  # noqa: E402


def _mk(root, rel, data, t=1790247214):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'wb') as fh:
        fh.write(data)
    os.utime(p, (t, t))
    return p


def test_twins_split_and_everything_else_is_unchanged():
    root = os.path.realpath(tempfile.mkdtemp())
    saved = B._MOD_CACHE_ROOT
    B._MOD_CACHE_ROOT = root
    B._TWIN_INDEX.clear()
    B._TWIN_SUFFIX.clear()
    try:
        a = _mk(root, 'Nino/Buster/Battle/rtad', b'A' * 64)
        b = _mk(root, 'Nino/Buster (Alt)/Battle/rtad', b'B' * 64)
        c = _mk(root, 'Nino/Same/Battle/fsw0', b'S' * 64)
        d = _mk(root, 'Nino/Same (Alt)/Battle/FSW0', b'S' * 64)
        e = _mk(root, 'Nino/Solo/Battle/rtae', b'E' * 64)
        f = _mk(root, '_texconv/battle.lgp.rtad.x', b'X' * 64)
        old = '64-1790247214'
        assert B._sig(a) != B._sig(b)
        assert B._sig(a).startswith(old + '-c')
        assert B._sig(c) == B._sig(d) == old        # identical bytes
        assert B._sig(e) == old                     # no twin
        assert B._sig(f) == old                     # not a mod folder
        assert B._stat_sig(a)[0] == 64 and len(B._stat_sig(a)) == 2
    finally:
        B._MOD_CACHE_ROOT = saved
        B._TWIN_INDEX.clear()
        B._TWIN_SUFFIX.clear()


def test_not_condition_is_evaluated():
    """BUILD 579: <Not> used to make a folder unconditional."""
    import iro
    frag = ('<And><Option>LIMITBREAK = 2</Option><Not><Option>ATA = 0'
            '</Option></Not></And>')
    cond = iro._flatten_activewhen(frag)
    assert cond == '(LIMITBREAK = 2 AND NOT(ATA = 0))'
    assert not iro.evaluate(cond, {'LIMITBREAK': 1, 'ATA': 1})
    assert iro.evaluate(cond, {'LIMITBREAK': 2, 'ATA': 1})
    assert not iro.evaluate(cond, {'LIMITBREAK': 2, 'ATA': 0})
