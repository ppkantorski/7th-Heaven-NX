#!/usr/bin/env python3
"""Regression tests for the page-neutral animated paletted-art pass."""
import numpy as np
import pytest

import ff7nx_palettedart as PA


class _Art:
    def __init__(self, rgb, alpha=True):
        rgb = np.asarray(rgb, np.uint8)
        self.px = rgb.shape[0]
        r = (rgb[..., 0].astype(np.uint16) >> 3) << 11
        g = (rgb[..., 1].astype(np.uint16) >> 2) << 5
        b = rgb[..., 2].astype(np.uint16) >> 3
        self.buf = np.ascontiguousarray(r | g | b, dtype='<u2').tobytes()
        self.tmask = np.zeros((self.px, self.px), bool)
        self.hmask = np.ones((self.px, self.px), bool)
        if not alpha:
            self.tmask[0, 0] = True
            self.hmask[0, 0] = False


def test_requantise_uses_existing_nonzero_index_capacity_deterministically():
    y, x = np.mgrid[:768, :768]
    rgb = np.stack((160 + x * 32 // 768,
                    170 + y * 28 // 768,
                    180 + (x + y) * 24 // 1536), axis=-1).astype(np.uint8)
    art = _Art(rgb)
    idx1, pal1, target1, represented1 = PA._requantise(art)
    idx2, pal2, target2, represented2 = PA._requantise(art)
    assert idx1.shape == (256, 256)
    assert idx1.min() == 1
    assert idx1.max() == len(pal1)
    assert 15 < len(pal1) <= 255
    assert np.array_equal(idx1, idx2)
    assert np.array_equal(pal1, pal2)
    assert np.array_equal(target1, target2)
    assert np.array_equal(represented1, represented2)


def test_requantise_refuses_coverage_it_cannot_represent():
    art = _Art(np.full((256, 256, 3), 180, np.uint8), alpha=False)
    with pytest.raises(PA.PalettedArtError, match='fully opaque'):
        PA._requantise(art)


def test_family_manifest_covers_every_highwind_bridge_variant():
    assert set(PA.TARGETS) == {
        'fship_2', 'fship_22', 'fship_23', 'fship_24', 'fship_25'}
    for hashes, states in PA.TARGETS.values():
        assert len(hashes) == len(states) == 5
        assert all(len(h) == 64 for h in hashes)


def test_summary_promises_only_page_neutral_changes():
    line = PA.summarise({'fields': 1, 'pages': 5, 'pixels': 327680,
                         'old_colours': 74, 'new_colours': 306,
                         'old_error': 2.7, 'new_error': 2.2})
    assert 'No page, record, animation state, UV, byte count' in line
