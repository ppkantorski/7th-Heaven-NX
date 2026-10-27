"""BUILD 802: Shiva atlas cleanup (gutter fill, edge falloff, prefilter,
calmer skin saturation).

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install()
import colorsys  # noqa: E402
import os  # noqa: E402

import numpy as np  # noqa: E402

import ff7nx_shivacolor as C  # noqa: E402


def _island():
    """64x1024 (the rim width scales with the atlas width): black gutter,
    a 32-texel-square teal island with a 2-texel dark rim."""
    a = np.zeros((64, 1024, 3), np.float32)
    a[16:48, 16:48] = (0.15, 0.4, 0.45)
    a[16:18, 16:48] *= 0.3
    a[46:48, 16:48] *= 0.3
    a[16:48, 16:18] *= 0.3
    a[16:48, 46:48] *= 0.3
    a[30, 30] = (0.9, 0.95, 1.0)          # a highlight, well inside
    return a


def test_interior_is_unchanged_and_gutter_takes_island_colour():
    a = _island()
    b = C.clean_edges(np, a)
    assert np.allclose(b[20:44, 20:44], a[20:44, 20:44])
    teal = np.array((0.15, 0.4, 0.45))
    assert np.abs(b[0, 900] - teal).max() < 0.02        # far gutter
    assert np.abs(b[15, 30] - teal).max() < 0.02        # next to the edge


def test_dark_rim_is_lifted_but_a_light_edge_is_kept():
    a = _island()
    b = C.clean_edges(np, a)
    assert b[16, 30].mean() > 0.9 * a[30, 20].mean()    # was 0.3x
    a[16:18, 16:48] = (0.9, 0.95, 1.0)                  # light drawn edge
    b = C.clean_edges(np, a)
    assert np.allclose(b[16, 30], (0.9, 0.95, 1.0))


def test_prefilter_lands_on_the_nearest_neighbour_halving():
    rng = np.random.default_rng(1)
    a = rng.random((8, 8, 3)).astype(np.float32)
    p = C.prefilter(np, a)
    half = a.reshape(4, 2, 4, 2, 3).mean((1, 3))
    assert np.allclose(p[0::2, 0::2], half, atol=1e-6)


def test_skin_saturation_is_lifted_not_stretched():
    def s(rgb):
        c = C.recolor_rgb(np, np.array([[rgb]], np.float32) / 255)[0, 0]
        return colorsys.rgb_to_hsv(*c)[1]
    pale, deep = s((210, 212, 236)), s((112, 110, 170))     # hue ~235
    assert 0.2 < pale < 0.35 and 0.45 < deep < 0.65
    assert deep - pale < 0.3          # 1.6x made this ~0.45


def test_purple_shaded_skin_turns_gradually():
    hues = []
    for h in range(236, 276, 2):
        r, g, b = colorsys.hsv_to_rgb(h / 360, 0.45, 0.5)
        c = C.recolor_rgb(np, np.array([[(r, g, b)]], np.float32))[0, 0]
        hues.append(colorsys.rgb_to_hsv(*c)[0] * 360)
    steps = np.diff(hues)
    assert steps.max() < 6, steps      # was ~5.5/deg across 250..266


# ---- BUILD 802b/c -------------------------------------------------------

def _hsv(c):
    h, s, v = colorsys.rgb_to_hsv(*c)
    return h * 360, s, v


def test_legs_blend_gradually_from_knee_to_toe(monkeypatch):
    a = np.zeros((1024, 1024, 3), np.float32)
    r, g, b = colorsys.hsv_to_rgb(270 / 360, 0.54, 0.3)     # Nino's foot
    a[C.FOOT[1]:C.FOOT[3], C.FOOT[0]:C.FOOT[2]] = (r, g, b)
    r, g, b = colorsys.hsv_to_rgb(260 / 360, 0.46, 0.45)    # calf
    a[C.CALF[1]:C.CALF[3], C.CALF[0]:C.CALF[2]] = (r, g, b)
    a[100:200, 100:200] = (r, g, b)                          # not a leg
    out = C.legs_to_skin(np, a)
    # down the calf (knee at x 675) and on to the foot's toe (top, y 480)
    hues = [_hsv(out[900, x])[0] for x in range(670, 430, -20)]
    hues += [_hsv(out[y, 330])[0] for y in range(640, 470, -20)]
    assert abs(hues[0] - C.LEG_RAMP[0][0]) < 4, hues
    assert hues[-1] > 262, hues                              # lavender toe
    steps = np.diff(hues)
    assert (steps > -1).all() and steps.max() < 8, hues      # no jump
    for x in range(430, 680, 20):                            # never more
        assert _hsv(out[900, x])[1] <= C.LEG_RAMP[-1][1] + 0.01  # than feet
    assert np.allclose(out[150, 150], a[150, 150])
    assert np.allclose(out[0, 0], 0)                         # gutter kept
    monkeypatch.setenv(C.LEGS_ENV, '0')
    assert C.legs_to_skin(np, a) is a


def test_hair_strip_rows_evened_and_the_pelvis_above_left_alone():
    a = np.zeros((1024, 1024, 3), np.float32)
    for y in range(383, 776):                  # darkens and drifts blue
        t = (y - 383) / 393
        a[y, 440:556] = colorsys.hsv_to_rgb((186 + 21 * t) / 360, 0.69,
                                            0.33 - 0.14 * t)
    skin = colorsys.hsv_to_rgb(235 / 360, 0.4, 0.7)
    a[383:400, 500:520] = skin                 # a neighbouring skin panel
    out = C.even_hair(np, a)
    top, bottom = _hsv(out[450, 500]), _hsv(out[760, 500])
    assert abs(top[2] - bottom[2]) < 0.03 and abs(top[0] - bottom[0]) < 5
    assert np.allclose(out[390, 510], skin)


def test_lashes_go_dark_purple():
    src = np.zeros((1024, 1024, 3), np.float32)
    src[660:730, 5:35] = colorsys.hsv_to_rgb(208 / 360, 0.6, 0.6)
    new = np.full_like(src, 0.5)
    out = C.purple_lashes(np, src, new)
    h, s, v = _hsv(out[700, 20])
    assert abs(h - C.LASH_HSV[0]) < 3 and v < 0.45
    assert np.allclose(out[300, 300], 0.5)


def test_forearms_and_hands_take_the_upper_arm_tone():
    a = np.zeros((1024, 1024, 3), np.float32)
    dark = colorsys.hsv_to_rgb(236 / 360, 0.42, 0.65)
    tie = colorsys.hsv_to_rgb(207 / 360, 0.5, 0.3)          # hair tie, p03
    for x0, y0, x1, y1 in C.ARMS:
        a[y0:y1, x0:x1] = dark
    a[360:430, 120:190] = tie
    out = C.even_arms(np, a)
    for y, x in ((100, 150), (500, 100)):
        h, s, v = _hsv(out[y, x])
        assert abs(v - C.ARM_HSV[2]) < 0.03 and abs(s - C.ARM_HSV[1]) < 0.05
    assert np.allclose(out[400, 150], tie)


def test_joints_carry_the_parent_colour_across():
    a = np.zeros((1024, 1024, 3), np.float32)
    a[515:762, 728:1018] = (0.55, 0.67, 0.84)      # upper arm
    a[5:228, 9:323] = (0.45, 0.56, 0.72)           # forearm, darker
    a[354:592, 3:238] = (0.40, 0.52, 0.70)         # hand, darker still
    out = C.match_joints(np, a)
    assert np.allclose(out[149, 244], (0.55, 0.67, 0.84), atol=0.02)
    assert np.allclose(out[586, 60], out[68, 9], atol=0.02)
    assert np.allclose(out[600, 900], a[600, 900])  # the parent is untouched
    far = out[116, 14]                              # wrist end of the forearm
    assert far.mean() < out[149, 244].mean()        # the match fades out


def test_knee_pit_crease_is_flattened():
    a = np.zeros((1024, 1024, 3), np.float32)
    r, g, b = colorsys.hsv_to_rgb(260 / 360, 0.46, 0.45)
    a[C.CALF[1]:C.CALF[3], C.CALF[0]:C.CALF[2]] = (r, g, b)
    cx, cy = C.KNEE_PIT[:2]
    a[cy - 20:cy + 20, cx - 8:cx + 8] *= 0.8                 # Nino's crease
    out = C.legs_to_skin(np, a)
    pit, beside = out[cy, cx].max(), out[cy, cx - 40].max()
    assert abs(pit - beside) < 0.02, (pit, beside)


def test_briefs_back_seam_is_shaded():
    a = np.zeros((1024, 1024, 3), np.float32)
    r, g, b = colorsys.hsv_to_rgb(275 / 360, 0.5, 0.5)       # briefs band
    a[30:220, 600:690] = (r, g, b)
    out = C.briefs_seam(np, a)
    edge, far = out[120, 689].max(), out[120, 620].max()
    assert edge < far * 0.7 and np.isclose(far, a[120, 620].max()), (edge, far)
    assert np.allclose(out[0, 0], 0) and np.allclose(out[120, 692], 0)


def test_palette255_keeps_gradients_within_255_colours():
    rng = np.random.default_rng(0)
    img = np.zeros((256, 256, 3), np.uint8)
    for k, c in enumerate(((60, 90, 160), (150, 90, 170), (40, 120, 110),
                           (200, 200, 230))):                # big panels
        img[k * 56:(k + 1) * 56] = np.clip(
            np.array(c) + rng.integers(-3, 4, (56, 256, 3)), 0, 255)
    ramp = np.linspace(0, 1, 256, dtype=np.float32)[None, :, None]
    img[224:] = np.rint(255 * ((1 - ramp) * (0.4, 0.5, 0.8)
                               + ramp * (0.6, 0.4, 0.8))).astype(np.uint8)
    out = C.palette255(np, img)
    assert len(np.unique(out.reshape(-1, 3), axis=0)) <= 255
    # the small gradient still gets many steps, close to the input
    assert len(np.unique(out[224:].reshape(-1, 3), axis=0)) >= 12
    assert np.abs(out[224:].astype(int) - img[224:]).max() <= 8


def test_skin_openings_turn_purple_inside_skin_triangles_to_skin():
    a = np.zeros((1024, 1024, 3), np.float32)
    t = C.SKIN_TRIS_P22[0]
    cx, cy = (t[0] + t[2] + t[4]) // 3, (t[1] + t[3] + t[5]) // 3
    skin = colorsys.hsv_to_rgb(236 / 360, 0.4, 0.7)
    a[cy - 40:cy + 40, cx - 40:cx + 40] = skin
    a[cy - 1:cy + 2, cx - 1:cx + 2] = colorsys.hsv_to_rgb(280 / 360, 0.6, 0.3)
    out = C.skin_openings(np, a)
    assert np.allclose(out[cy, cx], skin, atol=0.02), out[cy, cx]
    far = colorsys.hsv_to_rgb(280 / 360, 0.6, 0.3)
    a[900, 900] = far                                        # not a skin tri
    assert np.allclose(C.skin_openings(np, a)[900, 900], far)


def test_clothes_edges_lose_the_outline_and_dots():
    a = np.zeros((1024, 1024, 3), np.float32)
    skin = colorsys.hsv_to_rgb(216 / 360, 0.42, 0.75)
    cloth = colorsys.hsv_to_rgb(265 / 360, 0.42, 0.6)
    a[0:300, 400:700] = skin
    a[0:300, 550:700] = cloth
    a[:300, 548:552] = colorsys.hsv_to_rgb(262 / 360, 0.5, 0.3)   # outline
    a[100, 450] = colorsys.hsv_to_rgb(216 / 360, 0.42, 0.3)        # a dot
    out = C.clean_clothes_edges(np, a)
    for x in range(546, 554):            # each side's own colour, no line
        assert (np.allclose(out[150, x], skin, atol=0.03)
                or np.allclose(out[150, x], cloth, atol=0.03)), out[150, x]
    assert np.allclose(out[100, 450], skin, atol=0.03)
    assert np.allclose(out[150, 500], skin) and np.allclose(out[150, 650], cloth)


def test_briefs_are_lit_by_their_shape():
    """802l: one flat purple briefs band comes back lit: the back cheek's
    crown lighter than its under-curve by the leg opening, overall tone
    kept."""
    a = np.zeros((1024, 1024, 3), np.float32)
    x0, y0, x1, y1 = C.BRIEFS
    a[y0:y1, x0:x1] = colorsys.hsv_to_rgb(280 / 360, 0.45, 0.6)
    b = C.briefs_volume(np, a)
    v = b.max(-1)
    crown, under = v[150, 670], v[60, 610]
    assert crown > under + 0.08
    assert abs(np.median(v[y0 + 8:y1 - 8, x0 + 8:x1 - 8]) - 0.6) < 0.02
    assert np.array_equal(b[300:, :], a[300:, :])     # nothing outside


def test_clothes_get_a_thin_hem_where_they_meet_skin():
    """802m: along a CLOTHES_HEM edge the clothes side darkens in a thin
    line; texels well inside keep their colour."""
    a = np.zeros((1024, 1024, 3), np.float32)
    a[:] = colorsys.hsv_to_rgb(275 / 360, 0.35, 0.7)
    ax, ay, bx, by = (c / 2 for c in C.CLOTHES_HEM[0][:4])
    b = C.clothes_hem(np, a)
    mx, my = (ax + bx) / 2, (ay + by) / 2
    dx, dy = bx - ax, by - ay
    n = np.hypot(dx, dy)
    lx, ly = dy / n, -dx / n       # left of a->b with y down
    edge = b[int(my + ly * 0.8), int(mx + lx * 0.8)].max()
    inside = b[int(my + ly * 12), int(mx + lx * 12)].max()
    assert edge < 0.7 * (1 - C.HEM_DARK * 0.5)
    assert abs(inside - 0.7) < 1e-3


def test_briefs_tone_is_one_hue_and_saturation():
    """802m: hue and saturation contours across the briefs are evened."""
    a = np.zeros((1024, 1024, 3), np.float32)
    x0, y0, x1, y1 = C.BRIEFS
    for x in range(x0, x1):
        a[y0:y1, x] = colorsys.hsv_to_rgb((262 + (x - x0) % 16) / 360,
                                          0.3 + 0.01 * (x % 12), 0.6)
    b = C.briefs_tone(np, a)
    h, s, v = C._rgb2hsv(np, b[y0 + 8:y1 - 8, x0 + 8:x1 - 8])
    assert np.ptp(h) < 1 and np.ptp(s) < 0.01
    assert np.allclose(v, 0.6, atol=1e-3)


def test_halter_emblem_keeps_a_silver_frame():
    """802n: Nino's gold emblem frame comes back silver, not painted over."""
    import tex  # noqa: F401
    path = os.path.join(_nx_root, 'attic', 'shiva', 'nino_cyvadat.t00')
    if not os.path.isfile(path):
        import pytest
        pytest.skip('no Nino atlas here')
    d = open(path, 'rb').read()
    t = __import__('tex').parse(d)
    w, hgt = t['width'], t['height']
    px = np.frombuffer(t['pixels'], np.uint8).reshape(hgt, w, -1)[..., [2, 1, 0]]
    h, s, v = C._rgb2hsv(np, px.astype(np.float32) / 255)
    x0, y0, x1, y1 = C.HALTER
    gold = (h >= 20) & (h < 75) & (s > 0.4) & (v > 0.4)
    gold[:y0] = gold[y1:] = False
    gold[:, :x0] = gold[:, x1:] = False
    assert gold.sum() > 50
    out = C.recolor_tex(d)
    q = np.frombuffer(out[len(d) - len(t['pixels']):], np.uint8).reshape(
        hgt, w, -1)[..., [2, 1, 0]].astype(np.float32) / 255
    qh, qs, qv = C._rgb2hsv(np, q)
    assert np.median(qs[gold]) < 0.2 and np.median(qv[gold]) > 0.6
