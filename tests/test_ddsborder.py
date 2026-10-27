"""BUILD 400 -- upscaler border garbage, and why the old wrap repair made it
worse.

Reported on Odin: "where these textures intersect with eachother, it creates
this weird gradient that spans the height of the texture."

`kiri_2`'s wrap was never broken. SYW's first column is upscaler garbage
(mean 167 against an interior of ~22), and `_reseam_to_first` anchored its
32-pixel taper on exactly that column -- so it dragged 32 columns of good art
toward a bright artifact. That systematic ramp is the gradient.

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
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ff7nx_ddstex as D                                       # noqa: E402

REVIEW = ROOT / 'attic' / 'dirs' / 'investigation_syw' / 'review_v29'
SRC = REVIEW / 'kiri_2.tex_source.png'
VAN = REVIEW / 'kiri_2.tex_native.png'
needs_art = pytest.mark.skipif(not SRC.exists(), reason='no kiri_2 dump')


def _png(p):
    from PIL import Image
    return np.array(Image.open(p).convert('RGBA'))


# ---------------------------------------------------------- the detector

def _synth(w=256, h=64, seed=0):
    """A smooth tiling field: column 0 continues from column w-1."""
    rng = np.random.default_rng(seed)
    x = np.linspace(0, 2 * np.pi, w, endpoint=False)
    y = np.linspace(0, 2 * np.pi, h, endpoint=False)
    f = (np.sin(x)[None, :] + np.cos(y)[:, None]) * 40 + 128
    f = f + rng.normal(0, 1.0, f.shape)
    a = np.empty((h, w, 4), np.uint8)
    a[:, :, :3] = np.clip(f, 0, 255).astype(np.uint8)[:, :, None]
    a[:, :, 3] = 255
    return a


def test_a_clean_tiling_texture_is_left_alone():
    a = _synth()
    assert D.border_outliers(a, 1) == (0, 0)
    assert D.border_outliers(a, 0) == (0, 0)
    out, done = D.repair_all_borders(a)
    assert done == {}
    assert np.array_equal(out, a)


@pytest.mark.parametrize('end', ['lead', 'trail'])
@pytest.mark.parametrize('axis', [0, 1])
def test_one_garbage_line_is_found_on_either_end_of_either_axis(axis, end):
    a = _synth()
    i = 0 if end == 'lead' else -1
    if axis == 1:
        a[:, i, :3] = 250
    else:
        a[i, :, :3] = 250
    lead, trail = D.border_outliers(a, axis)
    assert (lead, trail) == ((1, 0) if end == 'lead' else (0, 1)), (lead, trail)
    out, done = D.repair_all_borders(a)
    assert axis in done
    before = D._seam_stats(a, axis)[0]
    after = D._seam_stats(out, axis)[0]
    assert after < before, (before, after)


def test_it_never_peels_more_than_the_cap():
    a = _synth()
    a[:, :6, :3] = 250                      # a wide corrupt border
    lead, _trail = D.border_outliers(a, 1)
    assert lead <= D.BORDER_MAX_PEEL == 2


def test_a_repair_that_would_worsen_the_wrap_is_rejected():
    """The outer line is real art the wrap depends on."""
    a = _synth()
    # make column 0 a genuine strong edge that the wrap needs
    a[:, 0, :3] = a[:, -1, :3]
    a[:, 1:4, :3] = 10
    before = D._seam_stats(a, 1)[0]
    out, done = D.repair_all_borders(a)
    after = D._seam_stats(out, 1)[0]
    assert after <= before + D.BORDER_REGRESS_EPS, (before, after)


def test_repair_replicates_rather_than_inventing_pixels():
    a = _synth()
    a[:, 0, :3] = 250
    out, _done = D.repair_all_borders(a)
    assert np.array_equal(out[:, 0], a[:, 1]), 'col0 should become col1'
    assert np.array_equal(out[:, 2:], a[:, 2:]), 'interior must be untouched'


# ------------------------------------------------------------ kiri_2 itself

@needs_art
def test_kiri_2s_first_column_is_the_whole_seam():
    src = _png(SRC).astype(np.float64)
    colm = src[:, :, :3].mean(axis=(0, 2))
    interior = np.median(colm[4:-4])
    assert colm[0] > 150, colm[0]                  # the garbage column
    assert abs(colm[-1] - interior) < 30, colm[-1]  # the far edge is fine
    # and the width carries no brightness ramp: a fitted line is flat
    x = np.linspace(0, 1, len(colm))
    slope = np.polyfit(x, colm, 1)[0]
    assert abs(slope) < 5, 'there IS a horizontal ramp after all: %.1f' % slope


@needs_art
def test_the_repair_brings_kiri_2_to_vanillas_own_tiling_quality():
    src, van = _png(SRC), _png(VAN)
    vs, vt = D._seam_stats(van, 1)
    bs, _ = D._seam_stats(src, 1)
    assert bs > 100, bs                             # as shipped: broken
    fixed, done = D.repair_all_borders(src)
    assert done.get(1) == (1, 0), done
    fs, ft = D._seam_stats(fixed, 1)
    assert fs < 15, fs                              # 144.7 -> ~8.6
    assert fs < bs / 10
    # vanilla is the standard: seam no worse than its own neighbour step
    assert vs <= vt * 1.2, (vs, vt)


@needs_art
def test_the_old_taper_no_longer_fires_on_a_repaired_kiri_2():
    src, van = _png(SRC), _png(VAN)
    assert D.broken_wraps(van[:, :, :3], src) == [1], 'shipped should look broken'
    fixed, _ = D.repair_all_borders(src)
    assert D.broken_wraps(van[:, :, :3], fixed) == [], 'gross test still fires'


@needs_art
def test_the_gradient_was_the_taper_and_the_repair_removes_it():
    """The reported artefact, quantified: how much the taper rewrites.

    A systematic ~76-level correction across a 32-pixel band, full height, is
    a visible ramp. After the border repair the median correction is zero.
    """
    src = _png(SRC)
    fixed, _ = D.repair_all_borders(src)
    band = 32

    def correction(a):
        b = D._reseam_to_first(a, 1, edge_hold=2).astype(np.int32)
        return np.abs(b[:, -band:, :3] - a[:, -band:, :3].astype(np.int32))

    old, new = correction(src), correction(fixed)
    assert np.median(old) > 50, np.median(old)
    assert np.median(new) == 0, np.median(new)
    assert new.mean() < old.mean() / 5, (new.mean(), old.mean())


# ------------------------------------------------ the whole SYW pack

PACK = Path('/sessions/confident-jolly-lovelace/mnt/7th_heaven_nx/cache/'
            '[Tsunamods].SYW.Unified.Spells.Textures.V1.03/FF7SYWU/Base/magic')
needs_pack = pytest.mark.skipif(not PACK.is_dir(), reason='no SYW pack mounted')


@needs_pack
def test_across_the_pack_it_repairs_many_and_regresses_none():
    from PIL import Image
    names = sorted(f for f in os.listdir(PACK) if f.endswith('.dds'))
    assert len(names) > 1000, len(names)
    repaired = regressions = 0
    for f in names:
        try:
            a = np.array(Image.open(PACK / f).convert('RGBA'))
        except Exception:
            continue
        if min(a.shape[:2]) < 16:
            continue
        fixed, done = D.repair_all_borders(a)
        if not done:
            assert np.array_equal(fixed, a)
            continue
        repaired += 1
        for ax in (0, 1):
            if D._seam_stats(fixed, ax)[0] > D._seam_stats(a, ax)[0] + 0.5:
                regressions += 1
    assert repaired >= 30, repaired
    assert regressions == 0, regressions


# ---------------------------------------- end to end through convert_group
#
# BUILD 401. Build 400's unit tests all passed and the shipped texture was
# unchanged, because `ff7nx_spelltex.CONVERSION_VERSION` is part of the
# archive fingerprint and I did not bump it -- so the finished magic.lgp was
# reused verbatim. build.py's own comment predicts exactly that: "the version
# covers a CODE change ... the fix would appear to have done nothing."
#
# These tests go through the real converter, so they cannot pass while the
# shipped bytes are wrong.

VANILLA_TEX = Path('/sessions/confident-jolly-lovelace/mnt/7th_heaven_nx/cache/'
                   '_vanilla/magic.lgp/kiri_2.tex')
SYW_DDS = PACK / 'kiri_2_00.dds'
needs_pipeline = pytest.mark.skipif(
    not (VANILLA_TEX.exists() and SYW_DDS.exists()),
    reason='no vanilla magic.lgp / SYW pack mounted')


def _convert_kiri2():
    import struct
    import tex
    out = D.convert_group(VANILLA_TEX.read_bytes(),
                          {0: SYW_DDS.read_bytes()}, scale=3,
                          texture_name='kiri_2.tex')
    if isinstance(out, tuple):
        out = out[0]
    w = struct.unpack_from('<I', out, tex.O_WIDTH)[0]
    h = struct.unpack_from('<I', out, tex.O_HEIGHT)[0]
    psz = struct.unpack_from('<I', out, tex.O_PAL_SIZE)[0]
    off = tex.HEADER_LEN
    pal = np.frombuffer(out, np.uint8, psz * 4, off).reshape(psz, 4)
    idx = np.frombuffer(out, np.uint8, w * h, off + psz * 4).reshape(h, w)
    return pal[:, [2, 1, 0, 3]][idx][:, :, :3].astype(np.float64)


@needs_pipeline
def test_the_converted_texture_no_longer_carries_the_garbage_column():
    D.BORDER_REPAIRS.clear()
    rgb = _convert_kiri2()
    assert D.BORDER_REPAIRS == [('kiri_2.tex', {1: (1, 0)})], D.BORDER_REPAIRS
    colm = rgb.mean(axis=(0, 2))
    # the shipped build had col0 = 166.5 against an interior of ~22
    assert colm[0] < 40, 'column 0 is still upscaler garbage: %.1f' % colm[0]


@needs_pipeline
def test_the_taper_no_longer_ramps_the_tail_up_to_the_garbage():
    """The artefact, measured on the real output.

    Before: the final columns climbed 143 -> 149 -> 151 -> 165 -> 166.5,
    a full-height ramp at every tile join. After: they sit with the art.
    """
    rgb = _convert_kiri2()
    colm = rgb.mean(axis=(0, 2))
    interior = colm[4:-32]
    tail = colm[-32:]

    # 1. the last column must sit inside the art's own range, not above it.
    #    The shipped build ended at 166.5 against an interior spanning 13-76.
    assert tail[-1] <= interior.max() + 10, (
        'final column %.1f is outside the interior range %.1f..%.1f'
        % (tail[-1], interior.min(), interior.max()))

    # 2. the tail must not be a one-way ramp. A taper toward a bright column
    #    climbs almost every step; mist wanders both ways.
    rising = int((np.diff(tail) > 0).sum())
    assert rising < 24, '%d of %d tail steps rise -- still a ramp' % (
        rising, len(tail) - 1)

    # 3. and its spread must be no worse than the texture's own restlessness
    span = float(np.percentile(interior, 95) - np.percentile(interior, 5))
    assert tail.max() - tail.min() <= span * 1.2, (
        tail.max() - tail.min(), span)


@needs_pipeline
def test_the_wrap_still_actually_joins():
    rgb = _convert_kiri2()
    seam = float(np.abs(rgb[:, 0] - rgb[:, -1]).mean())
    neighbour = float(np.abs(np.diff(rgb, axis=1)).mean())
    assert seam <= neighbour, (seam, neighbour)


def test_the_conversion_version_was_bumped_for_this_change():
    """Without this the archive is reused and no fix can reach the game."""
    import ff7nx_spelltex as S
    v = S.CONVERSION_VERSION.decode()
    assert 'V40' not in v, 'still the version build 400 shipped under: %s' % v
    assert v.startswith('SPELLTEX-V'), v


def test_the_build_reports_what_the_border_repair_did():
    """Build 400 shipped with no log line, so 'no change' was unreadable."""
    import inspect
    import ff7nx_spelltex as S
    src = inspect.getsource(S)
    assert 'upscaler border repair' in src
    assert 'BORDER_REPAIRS' in src
