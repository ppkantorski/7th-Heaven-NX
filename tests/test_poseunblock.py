"""BUILD 648: no field script waits on a stand pose a mod made long.

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

HERE = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, HERE)
import ff7nx_poseunblock as PU   # noqa: E402

DUMP = os.path.join(HERE, 'dump', 'romfs', 'ff7', 'workingdir', 'data', 'field')


def _need(*names):
    for n in names:
        if not os.path.isfile(os.path.join(DUMP, n)):
            pytest.skip('needs the dump')


def _anim(path, frames):
    with open(path, 'wb') as f:
        f.write(struct.pack('<III', 1, frames, 21) + bytes(12))


def test_long_poses_only_where_a_stock_stand_pose_got_long(tmp_path):
    _need('char.lgp')
    long_, short, real = tmp_path / 'acfe.a', tmp_path / 'aafe.a', tmp_path / 'aaff.a'
    _anim(long_, 234)
    _anim(short, 1)
    _anim(real, 600)                    # stock aaff is already 30 frames: not a pose
    files = {'acfe.a': (str(long_), 'm'), 'aafe.a': (str(short), 'm'),
             'aaff.a': (str(real), 'm')}
    assert PU.long_poses(files, os.path.join(DUMP, 'char.lgp')) == ['acfe']


def test_shopkeeper_talk_no_longer_waits_on_the_pose():
    _need('flevel.lgp')
    import lgp as L
    ar = L.Archive(os.path.join(DUMP, 'flevel.lgp'))
    raw = ar.decompressed(ar.index['mds5_i'])
    new, changed = PU.unblock(raw, ['acfe'])
    assert new is not None and len(new) == len(raw)
    assert [c[0] for c in changed] == ['oyaji'] and all(c[2] == 0xBA for c in changed)
    diff = [i for i in range(len(raw)) if raw[i] != new[i]]
    # 662: play and go BACK TO THE IDLE (ANIME2), never hold the last frame
    assert len(diff) == len(changed) and all(new[i] == 0xAE for i in diff)
    # the weapon shop never blocked: untouched
    assert PU.unblock(ar.decompressed(ar.index['mds5_w']), ['acfe']) == (None, [])
    # nothing long: nothing changes
    assert PU.unblock(raw, []) == (None, [])


def test_unblocked_pose_returns_to_the_idle_not_a_frozen_frame():
    """662: on animation 0 (the idle) the hold-the-last-frame plays become
    ANIME2 0xAE (state 5: play, then back to the idle) -- 648's ANIMX1 0xAF
    held the long idle's last frame and froze the model."""
    assert PU.TO_IDLE == {0xBA: 0xAE, 0xAF: 0xAE}
    assert PU.TWIN == {0xBA: 0xAF, 0xA3: 0xAE}
    assert PU.VERSION == 662


def test_to_idle_only_on_animation_0_without_another_idle():
    _need('flevel.lgp')
    import lgp as L
    ar = L.Archive(os.path.join(DUMP, 'flevel.lgp'))
    raw = ar.decompressed(ar.index['mds5_i'])
    # oyaji's `ANIMX2 0 1` (snap to the stand pose, animation 0) -> ANIME2
    new, changed = PU.unblock(raw, ['acfe'])
    diff = [i for i in range(len(raw)) if raw[i] != new[i]]
    assert [c[2] for c in changed] == [0xBA] and [new[i] for i in diff] == [0xAE]
    # oyaji's ANIME1 on animation 2 (a gesture made long): non-blocking,
    # already back to the idle
    new2, changed2 = PU.unblock(raw, ['cafb'])
    diff2 = [i for i in range(len(raw)) if raw[i] != new2[i]]
    assert changed2 and all(c[2] == 0xA3 for c in changed2)
    assert all(new2[i] == 0xAE for i in diff2)
    # an entity that sets its own idle (0xA2) keeps 648's hold: patch the
    # oyaji's first ANIME1 into a SET-IDLE and look again
    s1 = bytearray(raw)
    k = raw.find(bytes([0xA3, 0x02, 0x01]))
    s1[k] = PU.SET_IDLE
    new3, changed3 = PU.unblock(bytes(s1), ['acfe'])
    diff3 = [i for i in range(len(raw)) if s1[i] != new3[i]]
    assert [new3[i] for i in diff3] == [0xAF]
