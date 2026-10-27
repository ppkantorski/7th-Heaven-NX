"""BUILD 721 -- the world map's grey wedge and grey frames.

A TL vertex closer than 2 units (rhw >= 0.5) kept the z the game wrote, so
its triangle was never near-clipped and smeared one texel over the screen.
tlmain_vv now gives every 3D world vertex the 591 depth (the near plane at
Z ~ 15.9 cuts it exactly), and drops a triangle whose vertex has no real rhw
or lands far off screen while near.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import os
import re
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SHADER = os.path.join(ROOT, 'shaders', 'wide_screen', 'tlmain_vv.glsl')
A, C = 0.99, 32.0


def _define(text, name):
    return float(re.search(r'#define\s+%s\s+(\S+)' % name, text).group(1))


def tl(x, y, rhw, far_off, far_rhw):
    """The shader's world branch in numpy: clip (x, y, z, w) before
    projectionMatrix, i.e. in the 640x480 pixel space."""
    off = max(abs(x - 320.0), abs(y - 240.0))
    if not rhw > 0.0 or (off > far_off and rhw >= far_rhw):
        return np.array([0.0, 0.0, -1.0e20, 1.0]), True
    w = 1.0 / rhw
    z = w * A * (1.0 - C * rhw) if rhw != 1.0 else 0.5 * w
    return np.array([x * w, y * w, z, w]), False


def clip_near(tri):
    """Sutherland-Hodgman against z >= -w (GL's near plane)."""
    out = []
    for i in range(len(tri)):
        a, b = tri[i], tri[(i + 1) % len(tri)]
        da, db = a[2] + a[3], b[2] + b[3]
        if da >= 0:
            out.append(a)
        if (da >= 0) != (db >= 0):
            t = da / (da - db)
            out.append(a + t * (b - a))
    return out


def screen_area(poly):
    if len(poly) < 3:
        return 0.0
    p = [(v[0] / v[3], v[1] / v[3]) for v in poly]
    return 0.5 * abs(sum(p[i][0] * p[i - 1][1] - p[i - 1][0] * p[i][1]
                         for i in range(len(p))))


class Shader(unittest.TestCase):
    def setUp(self):
        self.text = open(SHADER).read()
        self.far_off = _define(self.text, 'WS_FAR_OFF')
        self.far_rhw = _define(self.text, 'WS_FAR_RHW')

    def test_every_3d_vertex_gets_the_world_depth(self):
        self.assertNotIn('VertexCoord.w < 0.5', self.text)
        self.assertIn('rhw != 1.0', self.text)

    def test_a_vertex_at_the_eye_is_cut_at_the_near_plane(self):
        # ground at Z 400 and 600, one corner 1.2 units from the eye and far
        # off to the side -- the 720 shape: before, the whole wedge drew
        f = 300.0
        def proj(X, Y, Z):
            return 320 + f * X / Z, 240 + f * Y / Z, 1.0 / Z
        g = [proj(-100, 50, 400), proj(100, 50, 600), proj(60, 1, 1.2)]
        tri = [tl(*v, self.far_off, self.far_rhw)[0] for v in g]
        kept = clip_near(tri)
        # what survives lies at Z >= 15.9 (the near plane), none of it
        # nearer than the cut
        for v in kept:
            self.assertGreaterEqual(v[3], 15.8)

    def test_far_off_near_vertex_drops_its_triangle(self):
        g = [(300.0, 260.0, 1 / 400), (360.0, 250.0, 1 / 600),
             (60000.0, -9000.0, 1 / 20.0)]
        tri = [tl(*v, self.far_off, self.far_rhw)[0] for v in g]
        self.assertLess(screen_area(clip_near(tri)), 1e-6)

    def test_bad_rhw_drops(self):
        for rhw in (0.0, -0.01, float('nan')):
            self.assertTrue(tl(10, 10, rhw, self.far_off, self.far_rhw)[1])

    def test_2d_and_far_terrain_untouched(self):
        self.assertFalse(tl(639, 479, 1.0, self.far_off, self.far_rhw)[1])
        # the far field's sky quads and distant terrain far off screen
        self.assertFalse(tl(40000, 240, 1e-6, self.far_off, self.far_rhw)[1])
        self.assertFalse(tl(20000, 240, 1 / 5000, self.far_off, self.far_rhw)[1])


if __name__ == '__main__':
    unittest.main()
