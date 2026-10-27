"""BUILD 785: colour-correct a mod's Shiva texture toward the stock model.

Ninostyle's Shiva (magic.lgp cyvadat.t00, 1024x1024 truecolor) reads purple
in game where the stock model -- untextured, vertex coloured -- is periwinkle
blue skin, pale aqua hair and lavender-pink clothes. The atlas separates
cleanly by hue (measured on Nino's file):

    skin     215..258   s .37 v .67   -> hue ~215, more saturated (stock
                                          vertex colour 218 / s .56)
    clothes  258..330   violet + pink -> hue ~280 compressed, a touch lighter
    hair     150..215   s .69 v .29   -> hue ~182, much lighter
    gold      15..70    s .83         -> silver (cool grey, brighter)

Low-saturation pixels (black, white highlights, the eye whites) are kept.
Only the hue/saturation/value of each class moves; the shading inside a
class is kept, so the art is the same, recoloured.

BUILD 802 (atlas cleanup, hardware 10-24 "stomach looks discontinuous on
the sides", "dark fringes on the hair"):

  - Skin saturation is lifted toward the stock level instead of multiplied
    by 1.6. The multiply stretched Nino's soft light-to-shadow shading into a
    hard pale/deep-blue split, so the pelvis and chest parts no longer
    matched where they meet at her sides. The skin/clothes hand-over is also
    wider (240..280 instead of 250..266) so the purple-shaded side of a skin
    panel turns gradually instead of in blotches.
  - Islands are cleaned at their edges: the 1-3 texel dark falloff Nino's
    art has against the black gutter is replaced with the island's own
    colour where it is darker, and every gutter texel is filled with the
    nearest island colour. Filtering and the downscale used to pull that
    black into every edge -- the dark lines along the hair whips.
  - A 2x2 box prefilter, so the converter's nearest-neighbour 1024->512
    step lands on an area average instead of skipping every other texel.

BUILD 802c (hardware 10-24, against the posed model; tools in build802/):

  - Legs: as on the stock model (its trading-card render): blue thighs,
    and from just below the knee a light lavender down to the feet -- not
    Nino's dark, muddy purple. The calves turn over their top fifth.
    SEVENTH_NX_SHIVA_SKIN_LEGS=0 keeps his legs.
  - Hair whips: each whip is five segments laid down one long strip of the
    atlas, head end at the bottom. Nino's strip darkens (v .33 -> .19) and
    drifts toward blue (hue 186 -> 207, the skin boundary) toward that end,
    so the head-end segments came out darker with blue specks: dark bands
    across the whips. The strip and the tip strip are evened row by row to
    one hair tone; the strand detail across a row is kept.
  - Thighs: the thigh panels (p24/p28) and the hip piece's leg openings
    (the disk inside p22's briefs band, the tops of the thighs) are evened
    to the belly's light blue (s .30, v .76 against Nino's .41/.63), and
    the calves start from that tone at the knee.
  - BUILD 802d: thighs brighter (v .84) with half of Nino's shading kept;
    the calves fade from the knee colour to lavender over their whole
    length; and every limb joint (elbow, wrist, knee, ankle) is colour
    matched across the seam on the posed model (JOINTS), so forearms and
    hands continue the upper arm and the calves continue the thigh.
  - BUILD 802e: one ramp runs from the knee to the toes across calf and
    foot, driven by height on the model (LEG_RAMP), stepping through
    periwinkle with low saturation instead of turning purple mid-shin.
  - BUILD 802f: the dark crease Nino paints behind the knee (top of the
    calf panel) is flattened (KNEE_PIT), so the back of the calf is smooth;
    and the briefs' back centre line is shaded again (BRIEFS_SEAM), the
    crease Nino's gutter used to draw there.
  - BUILD 802g: the feet are the 802c purple again (the ramp reaches it at
    the ankle), the thighs and calves lose most of Nino's mid-scale
    bright/dark blotches (LEG_BLOTCH_KEEP), the patches behind the knees,
    and the briefs crease is a thin line like Nino's.
  - BUILD 802h: the briefs are evened into one piece of fabric (BRIEFS:
    mid-scale blotches and saturation spread out), and the crease is a thin
    line of even depth down the whole seam; the purple Nino drew inside the
    leg openings' skin triangles is skin (SKIN_TRIS_P22); the leg blend runs
    knee to foot in even steps; and the atlas's 255 battle colours are
    chosen here (palette255), so gradients stop breaking into flat bands.
  - BUILD 802i: where skin meets clothes each side keeps its own colour up
    to the line (no dark outline, no discoloured rim), and body panels lose
    isolated dark dots (clean_clothes_edges).
  - BUILD 802j: the edge colour is smoothed along the line (no patches),
    the briefs' broad blotches and saturation are evened further
    (BRIEFS_SHADE, BRIEFS_SAT), and the crease fades out before it reaches
    the waistband (SEAM_TOP).
  - BUILD 802k: the edge clean-up also covers each UV island's own edge
    (EDGE_G texels from Nino's gutter: the dark line inside the briefs'
    waistband and leg openings), and a texel's side is the majority round
    it, so pale streaks inside the briefs' edge become briefs.
  - BUILD 802l: the briefs are lit by the shape they wrap (BRIEFS_LIGHT,
    baked from the mesh's smoothed normals): each cheek is lightest on its
    crown and shades off to the sides, the leg openings and the under-curve,
    with a soft shadow either side of the thin crease (SEAM_BROAD). The
    crease's top no longer turns blue: Nino's dark halo and gutter along the
    back seam take the crease's purple, and the seam is recoloured texel by
    texel so the skin island next to it doesn't bleed in.
  - BUILD 802m: a thin dark hem wherever the clothes meet skin (CLOTHES_HEM,
    the edges taken from the mesh: waistband, leg openings, the top and its
    halter), continued a texel past the edge so it filters cleanly; and
    the briefs and the top are each one hue and saturation after the
    recolour (briefs_tone), so only light and dark vary across the fabric.
  - BUILD 802n: the halter's emblem is back: the edge clean-up had painted
    its gold frame over as stray texels. Gold is recoloured to silver texel
    by texel, and the knot (HALTER: emblem, trim, the skin in the V) is
    left to Nino's art. The mesh's own skin/clothes layout (SIDES) repaints
    purple blots and violet patches on the skin beside the top, and the
    gutter between takes the nearer side. The hem keeps one width on the
    model (per-edge scale from texel density), never spills onto skin,
    and lifts Nino's dark rim beside it so the two don't stack.
  - BUILD 802o: the cups' top edge toward the arms is painted through
    triangles, not on a mesh edge, so it had lost its outline. SIDES now
    marks those triangles (3); there each texel follows Nino's paint, his
    ragged edge smoothed by majority, and painted_hem draws the hem along
    it. Hems are a little thinner (HEM_W 2.5) and their per-edge scale is
    held to HEM_SCALE, so no edge reads thicker than the rest.
  - BUILD 802p: that painted hem was cut texel by texel, so it stepped
    along the edge. It now follows a smooth signed distance to Nino's
    skin (his skin mask blurred twice), with a one-texel antialiased
    blend, so the cup edge's outline reads as one smooth line.
  - BUILD 802q: the line still jogged where the 90% rule had called a
    triangle skin though Nino's fabric edge ran a few texels into it;
    texels within MIXED_REACH of his mixed triangles now follow his paint
    too, so the edge runs on unbroken. Skin beside the hem keeps its own
    tone instead of a lighter one filled from further off.
  - Arms: Nino's forearms (p34/p46) and hands (p35/p47) are painted darker
    and more saturated (s .35-.42, v .65) than the upper arms (s .24,
    v .78), so they darkened past the elbow. Both panels are evened to the
    upper arm's tone at the elbow; skin hues only, so the hair tie (p03)
    sharing the hand panel and the dark gaps between fingers stay.
  - Eyelashes: the closed-eye lash sprite (atlas x 2..37, y 655..732, Nino
    blue) came out bright cyan through the hair correction. Now a dark
    purple with Nino's light and dark kept, as on the stock model; the gutter
    around it too, so filtering doesn't pull cyan back in.
  - (802b's belly transfer is gone: the hip piece's own colours read more
    natural on hardware than the torso's copied onto it.)

SEVENTH_NX_SHIVA_RECOLOR=0 ships Nino's colours unchanged.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os
import struct

ENV = 'SEVENTH_NX_SHIVA_RECOLOR'
VERSION = 'SHIVACOLOR-V47'
# BUILD 802: the skin/clothes hand-over and the skin saturation (see above).
SKIN_TO = (240, 280)
SKIN_S = (0.15, 1.1)       # s' = 0.15 + 1.1 s (was 1.6 s): same mean, calmer
# Atlas gutter: texels darker than this are background, not art (22% of
# Nino's atlas is exactly 0,0,0; none of the face or eye art is under it).
GUTTER_V = 0.03
RIM = 3                    # texels of edge falloff at 1024
# BUILD 802b: the leg panels of Nino's atlas, in 1024 texels (x0, y0, x1,
# y1), and the value each is brought to (the thigh's median is .63).
LEGS_ENV = 'SEVENTH_NX_SHIVA_SKIN_LEGS'
# BUILD 802m: a thin dark hem wherever the clothes meet skin on the model.
# The edges come from the mesh (p00, p22): every edge of a clothes triangle
# whose neighbour is skin, or that has none and is not on the mirror plane.
# Each is (ax, ay, bx, by, wt) -- the ends in half texels of the 1024 atlas,
# with the clothes on its left (y down), and wt / 10 the width scale that
# keeps the hem the same width on the model wherever the texel density
# differs (802n). A triangle is clothes or skin when 60% of it is; Nino's
# gold trim counts as clothes, so the emblem gets no hem round it. HEM_W texels wide, x (1 - HEM_DARK) at the
# edge, and HEM_OUT texels past it take the hem's colour where they are
# gutter (never skin: the skin strip in the halter's V stays skin).
CLOTHES_HEM = (
    (725, 490, 646, 436, 15), (754, 265, 813, 394, 11), (781, 147, 754, 265, 15),
    (793, 528, 725, 490, 10), (813, 394, 914, 433, 9), (875, 58, 781, 147, 15),
    (895, 489, 793, 528, 12), (914, 433, 999, 431, 8), (999, 431, 1089, 394, 9),
    (1010, 498, 895, 489, 9), (1043, 30, 875, 58, 12), (1089, 394, 1165, 333, 10),
    (1106, 491, 1010, 498, 10), (1165, 333, 1220, 229, 11), (1184, 101, 1043, 30, 11),
    (1195, 481, 1106, 491, 11), (1220, 229, 1184, 101, 11), (1298, 475, 1195, 481, 10),
    (1349, 428, 1298, 475, 11), (1394, 427, 1349, 428, 12), (1420, 458, 1431, 402, 11),
    (1431, 402, 1466, 329, 12), (1466, 329, 1535, 280, 13), (1500, 599, 1580, 660, 9),
    (1518, 511, 1500, 599, 8), (1535, 280, 1621, 268, 13), (1539, 710, 1470, 656, 10),
    (1555, 702, 1539, 710, 10), (1571, 426, 1543, 469, 7), (1579, 749, 1555, 702, 12),
    (1580, 660, 1609, 693, 15), (1605, 776, 1579, 749, 11), (1621, 268, 1702, 295, 10),
    (1621, 685, 1668, 716, 11), (1632, 407, 1636, 426, 10), (1636, 426, 1580, 437, 6),
    (1668, 716, 1733, 761, 8), (1683, 392, 1687, 413, 12), (1687, 413, 1632, 407, 7),
    (1689, 821, 1605, 776, 10), (1702, 295, 1781, 313, 8), (1723, 377, 1677, 380, 9),
    (1733, 761, 1806, 795, 9), (1742, 411, 1723, 377, 11), (1761, 864, 1689, 821, 10),
    (1781, 313, 1828, 325, 10), (1783, 400, 1742, 411, 6), (1806, 795, 1876, 817, 11),
    (1826, 456, 1783, 400, 8), (1828, 325, 1887, 347, 8), (1840, 876, 1773, 849, 11),
    (1876, 817, 1929, 846, 9), (1879, 494, 1826, 456, 7), (1887, 347, 1960, 380, 7),
    (1891, 910, 1840, 876, 11), (1936, 482, 1879, 494, 7), (1960, 380, 2019, 342, 6),
    (1977, 477, 1936, 482, 7), (2040, 481, 1977, 477, 5),
)
HEM_W, HEM_DARK = 2.5, 0.5
HEM_SCALE = (7, 12)   # the per-edge width scale's range, in tenths (802o)
# texels of a skin or clothes triangle this close to one Nino paints both
# on follow his paint too: the 90% rule can call a triangle skin while
# his fabric edge still runs a few texels into it (802q)
MIXED_REACH = 8
HEM_OUT = 1.5      # texels past the edge that take the hem's colour

# BUILD 802n: which texels the mesh draws as skin (1) or clothes (2) on the
# hip (p22) and chest (p00) pieces, 0 for gutter, 3 for a triangle Nino
# paints both on (the cups' top edge and the leg openings run through
# triangles, 802o) -- a triangle counts as clothes or skin when 90
# percent of it is. Over the
# 1024 atlas's x 318..1023, y 0..511; zlib, base64.
SIDES_ORIGIN, SIDES_SHAPE = (318, 0), (512, 706)
# BUILD 802n: the halter's knot -- the emblem, its light trim and the skin
# in the V above it, all painted on a few small triangles: Nino's art is
# only recoloured there, texel by texel, and no edge pass touches it.
HALTER = (728, 276, 800, 372)
# skin there bluer than hue 216 by 10 (Nino's violet shading under the
# straps) or darker than SIDES_DARK of the skin round it is repainted,
# within SIDES_NEAR texels of the clothes
SIDES_SKIN_HUE, SIDES_DARK = 226, 0.85
SIDES_NEAR = 14   # texels from the clothes this applies within
SIDES = (
    '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'
    'Ehcx1SCBDVMIEhgxJSCEEEIIIYQQMiD/AZypyms='
)

PALETTE_ENV = 'SEVENTH_NX_SHIVA_PALETTE'   # 802h: '0' = the median cut's own
CALF = (420, 782, 723, 1014)       # p25/p29
FOOT = (244, 471, 408, 705)        # p26/p30
THIGH_HSV = (235.0, 0.24, 0.84)    # 802d: brighter than the belly's median
THIGH_KEEP = 0.5                   # how much of Nino's thigh shading stays
THIGHS = ((726, 767, 1021, 1020), (340, 12, 606, 224))   # p24/p28, p22 disk
# BUILD 802e: calves and feet blend from the thigh's blue at the knee to a
# light lavender at the toes, by height on the model (not atlas position):
# model height (y down) = a*x + b*y + c over each panel, fitted to the
# posed mesh; knee and toe heights measured on it. The ramp is in source
# space (the recolour then maps hue 235 to the skin's ~217 and 268 to
# lavender ~258) and steps through periwinkle in even steps from the knee
# to the top of the foot (t .75), then holds the 802c foot purple.
LEG_H_CALF = (-1.466, 0.102, -402.8)    # p25/p29
LEG_H_FOOT = (0.084, -1.144, -185.6)    # p26/p30
LEG_KNEE_H, LEG_TOE_H = -1320.0, -720.0
LEG_RAMP_T = (0.0, 0.15, 0.32, 0.48, 0.62, 0.75, 1.0)
# BUILD 802f: Nino paints a dark crease (and a pale ridge beside it) at the
# top of the calf, the hollow behind the knee: on the model it reads as a
# dark patch just below the back of the knee. Ellipse (cx, cy, rx, ry) in
# 1024 texels, where its mid-scale shading is flattened.
KNEE_PIT = (614, 832, 30, 42)
LEG_RAMP = ((235.0, 0.24, 0.84), (238.0, 0.25, 0.83), (245.0, 0.29, 0.81),
            (254.0, 0.36, 0.78), (262.0, 0.44, 0.75), (268.0, 0.50, 0.72),
            (268.0, 0.50, 0.72))
# BUILD 802g: what the thigh and calf panels keep of Nino's mid-scale
# blotches (bright and dark patches 8-80 texels across, behind the knee
# above all) after evening; the fine grain under 8 texels stays.
LEG_BLOTCH_KEEP = 0.2
# BUILD 802c: the hair whip strip and the tip strip, and the tone their rows
# are evened to (source space; Nino's middle rows)
HAIR_STRIPS = ((436, 383, 561, 776), (349, 869, 421, 1022))
HAIR_HSV = (187.0, 0.69, 0.32)
# BUILD 802c: forearm and hand panels, evened to the upper arm (p33/p45)
# at the elbow (source space, measured)
ARMS = ((9, 5, 323, 228), (3, 354, 238, 592))
# BUILD 802f/g: the back of the briefs is mirrored, and its centre line is
# the right edge of the briefs band in p22's panel (x ~547-697, y 9-220).
# Nino's black gutter used to bleed into that edge, which read as the
# crease between the glutes; the gutter fill (802) took it away, so it is
# painted back as a thin line: x SEAM_DARK at the edge, fading over SEAM_W
# texels measured from the edge as a polyline (it runs almost sideways at
# the bottom), and SEAM_TOP times as deep at the waistband end.
BRIEFS_SEAM = (540, 6, 712, 222)
HALO_V = 0.30
# BUILD 802l: the briefs are lit by the mesh's own smoothed normals (two
# lights, front and back, 31 degrees above), baked at 1/8 scale over the
# BRIEFS rect (value / 200 = light; 1.0 faces a light); BRIEFS_LIGHT_GAIN
# of the texel's tone follows it.
BRIEFS_LIGHT_CELL, BRIEFS_LIGHT_SHAPE = 8, (32, 48)
BRIEFS_LIGHT_GAIN = 0.85
BRIEFS_LIGHT = (
    'eNqFlPlXElgUx1PTTmaZeMw8mhWW0KQOFm65IqngkJimiLihggsIyL4jOyouqIzmqIU5rlMJ'
    'KKDIJoj+WfNoHE+dmTPz/flzv+9+73vv3ktISIi59qOiYm+nPHiSgyxF1ze391Fp6omJiS3r'
    'X7r3L/y1mFvJ6U9yCyvq8K1dlKFR7f/x0TchGU/zCisxjW09g0y+TD1t2vkv/npCSiYsv6S6'
    'vpnUO8yRqgwzV/yjtLS0+Oh/+j+AvyyvbWjppND5Y/qp+aW19Z29A6fTGeFvx0VH/Rg47m4G'
    '7EU5polEHmQKVca5pdX1zd0rHnIrPuFG1A8DupuenV9W19Q5wORKNNOLK5bN3S82xyV/D5Ka'
    'kRTzvX1iWlZeMQrX1jfCl6gMpmXL7zufrRF7Z2k+HJqR8RSWGvtd+0nQ3KIqTGM7hSlUqMen'
    'zKsbEdxx5Pb6SvNz4FlwBOLhd6Gjk/MqMI2E7sFRsVI/OTO/bNmKuB97Trw+dBkyNxdZXPQs'
    '9eZVgqikPDQeTH5UrBqfNi0srW3s7Udw/6kf8CUFxeUVVUhY6lXkqDvwSnx7L40n10/PLy6v'
    'Wra+2J1HJ/7TkN+He11ZUY1GY6sK4Slxf/O3Hhf90t7PEKqNc4u/rVk2dvcPj058wbOz8Dke'
    'W1NXj8E24WpKsiHXL/mb6YiaNipbZpg1v1+zfNrcs7nc3sBZOHx+3tyAwzfiGjuIzdhiaPzl'
    'ATfuP696R+WrjSbz+1XLxvbnA9B8ENAXF4Tmd60teMIApbsNi4Bczig2BVaK7+MoDcaZuaWV'
    '9a2vDrcvEAL0xUU7kQS8u1ksGoVYDU+K/Zb5OiSroLa1jymSK8dNSx+2rS5gf3YeUWcXmUxq'
    '7ecKOIzBLlxB5p1IRUwi9GUVtrmDMsJXTZhXd2xH3kAofB7pn9xLpXYT+zgSIYfFoBLQiMzE'
    'uOiYxKxCFAbf2tHPkupMqzv2Y98psAfzCVOow8OUrh6mVCbicZgDHW9K4anxcZDsIhSmobm9'
    'l85TT3/jQdqzYCgcHhweYdAoZJpIKRcL2PR+Qh0SmhwPyS6srKlvbCMPjcqMKzt2tz8UPgsG'
    'wA3QR1gsJp06xFeqFBI+a6izoexZ2p0k6ItXqNo3LV1UhgTwB+5ACOD+QCjEHOWw2aP0YY5C'
    'q1aIuYy+d+j8x8mQhznIsmpsE6mfKTWu7B2eBILBgM/rOw1yuDwul8tmsGU6nVouYg+R6kvg'
    'aSmR715RiydS2IqZD58dJ/6A3weeZ+CULxDw+XwehyPVjetVMj6D/BaFeHQ//REMUQQ+DIWj'
    'ml8H1+v1eT1u8IYCIpFIKBQIBXyp1qDXjInZA0RsMSwj/QH0J2RFPYHC05ktu1an23PidjmP'
    'PF6pVCIWi8QikVSlUStB5JGet6ifH2dCs3OQ5VjCgGj814/bXw+Pjo9djgOXxyeXy0AJkFQx'
    'ppDLJCIurauhPBf2HFFYVo0jjcgmFz9uf7E7XK5Du93lDwIKYEBypUqtAUfI+PRufGVBwauq'
    'ugbioAAshw+bf+zbDw/tNpvLd6pSq5SgZEyp1o1PGqeME1qFALRUg3qNa+keFmqMC8uRXbJv'
    's9msNqc3oNZqNSqlSqMzTEzNzs3PzU5qZTxaTwu+qWOAJdZMm1ciu+TrvtVmBbzHp9Ub9NoI'
    'bZwxzS+YF+aMeqWYTevt6KHxZLqZxbVPW8DdarXbQYHD7fkTT0lX/A=='
)
SEAM_DARK, SEAM_W, SEAM_TOP = 0.55, 5.0, 0.0
# BUILD 802l: and a soft shadow either side of it, (width, depth), where
# the cheeks curve in toward the crease; it fades out with the line.
SEAM_BROAD = (24.0, 0.15)
# BUILD 802h: the briefs band (front and back, p22's panel) read as patchy
# after the 512 palette: Nino's mottling (value .27-.78 across one piece of
# fabric) quantised into flat contours. Its mid-scale blotches are mostly
# taken out (BRIEFS_KEEP kept) and its saturation pulled toward the
# band's median, before the crease is drawn.
# BUILD 802h: skin triangles of the hip piece (p22) whose texels Nino
# painted partly purple (the briefs' dark edge drawn inside the leg
# opening), as atlas (u0, v0, u1, v1, u2, v2) at 1024, measured from the
# mesh: on the model that purple sits on her skin as jagged dark patches.
SKIN_TRIS_P22 = (
    (478, 55, 542, 58, 516, 25), (597, 114, 568, 91, 547, 123),
    (547, 123, 562, 142, 597, 114), (540, 81, 568, 91, 582, 56),
    (582, 56, 542, 58, 540, 81), (582, 56, 568, 91, 597, 114),
    (536, 189, 573, 162, 562, 142), (597, 114, 562, 142, 573, 162),
    (438, 39, 439, 73, 478, 55), (516, 25, 438, 39, 478, 55),
    (516, 25, 542, 58, 582, 56), (500, 215, 536, 189, 513, 162),
    (377, 132, 406, 197, 407, 135), (438, 39, 390, 74, 439, 73),
    (407, 135, 390, 74, 377, 132), (505, 249, 505, 271, 543, 271),
    (697, 214, 675, 214, 681, 291), (681, 291, 675, 214, 649, 238),
    (649, 238, 594, 268, 595, 291), (598, 241, 553, 245, 543, 271),
    (553, 245, 505, 249, 543, 271), (505, 249, 446, 265, 505, 271),
    (594, 268, 598, 241, 543, 271), (598, 241, 594, 268, 649, 238),
    (454, 297, 446, 265, 397, 264), (323, 218, 370, 284, 363, 245),
    (440, 154, 406, 197, 457, 217), (500, 215, 477, 155, 457, 217),
    (542, 58, 478, 55, 516, 25), (568, 91, 597, 114, 547, 123),
    (562, 142, 547, 123, 597, 114), (568, 91, 540, 81, 582, 56),
    (542, 58, 582, 56, 540, 81), (568, 91, 582, 56, 597, 114),
    (573, 162, 536, 189, 562, 142), (562, 142, 597, 114, 573, 162),
    (439, 73, 438, 39, 478, 55), (438, 39, 516, 25, 478, 55),
    (542, 58, 516, 25, 582, 56), (536, 189, 500, 215, 513, 162),
    (406, 197, 377, 132, 407, 135), (390, 74, 438, 39, 439, 73),
    (390, 74, 407, 135, 377, 132), (505, 271, 505, 249, 543, 271),
    (675, 214, 697, 214, 681, 291), (675, 214, 681, 291, 649, 238),
    (594, 268, 649, 238, 595, 291), (553, 245, 598, 241, 543, 271),
    (505, 249, 553, 245, 543, 271), (446, 265, 505, 249, 505, 271),
    (598, 241, 594, 268, 543, 271), (594, 268, 598, 241, 649, 238),
    (446, 265, 454, 297, 397, 264), (370, 284, 323, 218, 363, 245),
    (406, 197, 440, 154, 457, 217), (477, 155, 500, 215, 457, 217),
)
# BUILD 802i: panels where her skin meets her clothes (p22's hip piece and
# the torso), and the body panels that are despeckled. Within EDGE_R texels
# of the other side, a texel that is darker than, or off the hue of, its own
# side's colour just inside takes that colour: Nino draws a dark outline
# round the clothes and a dark rim on the skin there, which read in game as
# a purple outline and discoloured skin. SPECK: an isolated texel this much
# darker than its 3x3 median is a dot and takes the median.
CLOTHES_EDGE = ((318, 0, 716, 410), (700, 0, 1024, 510))
BODY = ((318, 0, 716, 410), (700, 0, 1024, 510), (726, 515, 1021, 1020),
        (420, 782, 723, 1014), (244, 471, 408, 705), (9, 5, 323, 228))
EDGE_R, EDGE_G, EDGE_SPLIT, SPECK = (10, 6), 9, 240.0, 0.08   # (skin, clothes)
BRIEFS = (318, 0, 702, 262)
BRIEFS_HUES = (255, 320)
BRIEFS_KEEP, BRIEFS_S_KEEP = 0.1, 0.3
# BUILD 802i: and its broad light and dark (over ~50 texels: the dark
# blotch low on the back) is pulled most of the way to the band's median,
# its saturation x BRIEFS_SAT; the fine folds and the front's pale trim stay.
BRIEFS_SHADE, BRIEFS_SAT = 0.45, 0.88
HAIR_TIE = (105, 347, 201, 441)    # p03, inside the hand panel
# BUILD 802d: the limb joints, as matching texel pairs on the posed model
# (frame 0; tools/seams.py): parent-side points, child-side points, the child
# panel, and how far (texels) the colour match fades into it. Each joint's
# child panel is scaled per channel so its seam colour equals the parent's,
# fully at the seam and fading out; chained elbow -> wrist, knee -> ankle.
JOINTS = (
    ('elbow', ((984, 605), (981, 635), (985, 670), (1015, 739), (1017, 528)),
     ((244, 149), (265, 115), (274, 81), (292, 207), (295, 26)),
     (9, 5, 323, 228), 200),
    ('wrist', ((9, 68), (9, 166), (31, 150), (42, 58), (59, 80), (62, 139),
               (67, 111)),
     ((60, 586), (204, 578), (87, 558), (189, 559), (170, 555), (111, 550),
      (139, 562)),
     (3, 354, 238, 592), 150),
    ('knee', ((783, 891), (782, 853), (775, 828), (760, 916), (740, 787),
              (761, 1009), (759, 950)),
     ((673, 887), (674, 851), (675, 821), (675, 932), (675, 1013), (679, 782),
      (678, 959)),
     (420, 782, 723, 1014), 140),
    ('ankle', ((443, 782), (443, 1013), (443, 959), (443, 821), (443, 932),
               (443, 851), (443, 887)),
     ((321, 640), (325, 620), (329, 656), (335, 617), (340, 656), (346, 640)),
     (244, 471, 408, 705), 100),
)
ARM_HSV = (234.0, 0.24, 0.78)
# BUILD 802c: the lash sprite, and its colour (final space; value follows
# Nino's shading)
LASHES = (0, 648, 42, 740)
LASH_HSV = (268.0, 0.55)
# Nino's 1024 atlas (the face patch is upside down): the eye opening keeps
# its pixels; the brow/liner region around it takes the hair colour.
# Fractions of the texture: (cx, cy, rx, ry, feather).
EYE = (83 / 1024, 829 / 1024, 30 / 1024, 7 / 1024, 3 / 1024)
OPENING = ((50, 822), (70, 820), (92, 821), (110, 827), (116, 836),
           (112, 838), (92, 834), (70, 831), (52, 827))
LASH = (83 / 1024, 830 / 1024, 50 / 1024, 15 / 1024, 4 / 1024)
LINER = (86 / 1024, 834 / 1024, 54 / 1024, 20 / 1024, 4 / 1024)
CREASE = (90 / 1024, 835 / 1024, 52 / 1024, 44 / 1024, 2 / 1024)
LIPS = (63 / 1024, 727 / 1024, 32 / 1024, 22 / 1024, 5 / 1024)
GEM = (22 / 1024, 365 / 1024, 14 / 1024, 22 / 1024, 3 / 1024)
BROW = (85 / 1024, 846 / 1024, 78 / 1024, 46 / 1024, 10 / 1024)


def enabled():
    return os.environ.get(ENV, '1').strip().lower() not in (
        '0', 'off', 'false', 'no')


def _rgb2hsv(np, px):
    mx = px.max(-1)
    mn = px.min(-1)
    c = mx - mn
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    cc = np.where(c > 1e-6, c, 1)
    h = np.where(mx == r, ((g - b) / cc) % 6,
                 np.where(mx == g, (b - r) / cc + 2, (r - g) / cc + 4)) * 60
    h = np.where(c > 1e-6, h, 0)
    s = np.where(mx > 0, c / np.where(mx > 0, mx, 1), 0)
    return h, s, mx


def _hsv2rgb(np, h, s, v):
    h = (h % 360) / 60
    i = np.floor(h)
    f = h - i
    p = v * (1 - s)
    q = v * (1 - s * f)
    t = v * (1 - s * (1 - f))
    i = i.astype(int) % 6
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], -1)


def _box(np, a, r):
    """Mean over a (2r+1)^2 window, edges clamped."""
    for ax in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[ax] = (r + 1, r)
        c = np.cumsum(np.pad(a, pad, mode='edge'), axis=ax)
        hi = c.take(range(2 * r + 1, c.shape[ax]), axis=ax)
        lo = c.take(range(0, c.shape[ax] - 2 * r - 1), axis=ax)
        a = (hi - lo) / (2 * r + 1)
    return a


def recolor_rgb(np, rgb, smooth=0):
    """rgb: float array (..., 3) in 0..1. Returns the corrected array."""
    h, s, v = _rgb2hsv(np, rgb)
    # Soft class weights: the panels that straddle a boundary (Nino's
    # shaded violet) blend between two corrections instead of splitting.
    lit = np.clip((v - 0.04) / 0.04, 0, 1) * np.clip((s - 0.04) / 0.08, 0, 1)
    wh = np.interp(h, [140, 150, 205, 222], [0, 1, 1, 0]) * lit
    ws = np.interp(h, [205, 222, SKIN_TO[0], SKIN_TO[1]], [0, 1, 1, 0]) * lit
    wc = np.interp(h, [SKIN_TO[0], SKIN_TO[1], 330, 345], [0, 1, 1, 0]) * lit
    wg = (np.interp(h, [10, 18, 62, 72], [0, 1, 1, 0])
          * np.clip((s - 0.2) / 0.1, 0, 1) * (v > 0.06))
    if smooth and rgb.ndim == 3:
        # Per-pixel hue is noisy where Nino painted a gradient between two
        # materials; a small box blur of the class weights turns the speckle
        # into an even blend (BUILD 785b, "some of those textures look very
        # weird").
        wh, ws, wc = (_box(np, w_, smooth) * lit for w_ in (wh, ws, wc))
    rest = np.clip(1 - wh - ws - wc - wg, 0, 1)

    # hair: pale aqua; skin: periwinkle; clothes: lavender pink; gold: silver
    hh, hs, hv = 182 + (h - 195) * 0.4, s * 0.6, np.minimum(1, v * 1.9)
    sh, ss, sv = (216 + (h - 235) * 0.4,
                  np.minimum(SKIN_S[0] + s * SKIN_S[1], 0.8), v)
    ch, cs, cv = 270 + (h - 280) * 0.3, s * 0.68, v * 0.78 + 0.22
    gh, gs, gv = np.full_like(h, 210), np.full_like(s, 0.06), \
        np.minimum(1, v * 1.35 + 0.12)

    nh = wh * hh + ws * sh + wc * ch + wg * gh + rest * h
    ns = wh * hs + ws * ss + wc * cs + wg * gs + rest * s
    nv = wh * hv + ws * sv + wc * cv + wg * gv + rest * v
    return _hsv2rgb(np, nh, np.clip(ns, 0, 1), np.clip(nv, 0, 1))


def _fill(np, rgb, known):
    """Every unknown texel gets the colour of the nearest known ones: a few
    rings of neighbour averaging first (exact next to the edge), then
    push-pull for whatever is still far from any island."""
    known = known.astype(np.float32)
    col = rgb * known[..., None]
    k = known.copy()
    for _ in range(8):
        pc = np.pad(col, ((1, 1), (1, 1), (0, 0)))
        pk = np.pad(k, 1)
        sc = sum(pc[1 + dy:pc.shape[0] - 1 + dy, 1 + dx:pc.shape[1] - 1 + dx]
                 for dy in (-1, 0, 1) for dx in (-1, 0, 1))
        sk = sum(pk[1 + dy:pk.shape[0] - 1 + dy, 1 + dx:pk.shape[1] - 1 + dx]
                 for dy in (-1, 0, 1) for dx in (-1, 0, 1))
        new = (k == 0) & (sk > 0)
        if not new.any():
            break
        col = np.where(new[..., None], sc / np.maximum(sk, 1e-6)[..., None],
                       col)
        k = np.where(new, 1.0, k)
    if k.min() > 0:
        return col
    # push-pull for the rest
    levels = [(col * k[..., None], k)]
    c, kk = levels[0]
    while kk.min() == 0 and max(kk.shape) > 1:
        ph, pw = kk.shape[0] % 2, kk.shape[1] % 2
        c2 = np.pad(c, ((0, ph), (0, pw), (0, 0)), mode='edge')
        k2 = np.pad(kk, ((0, ph), (0, pw)), mode='edge')
        c = c2[0::2, 0::2] + c2[1::2, 0::2] + c2[0::2, 1::2] + c2[1::2, 1::2]
        kk = k2[0::2, 0::2] + k2[1::2, 0::2] + k2[0::2, 1::2] + k2[1::2, 1::2]
        c = c / np.maximum(kk, 1e-6)[..., None] * np.minimum(kk, 1)[..., None]
        kk = np.minimum(kk, 1)
        levels.append((c, kk))
    c, kk = levels[-1]
    out = c / np.maximum(kk, 1e-6)[..., None]
    for c, kk in reversed(levels[:-1]):
        hh, ww = kk.shape
        up = np.repeat(np.repeat(out, 2, 0), 2, 1)[:hh, :ww]
        out = c / np.maximum(kk, 1e-6)[..., None] * kk[..., None] \
            + up * (1 - kk[..., None])
    return out


def clean_edges(np, rgb):
    """BUILD 802. rgb (h, w, 3) in 0..1 -> the same art with its gutters
    filled and its dark edge falloff lifted (see the module notes). Texels
    well inside an island come back unchanged."""
    hgt, w = rgb.shape[:2]
    fg = rgb.max(-1) >= GUTTER_V
    r = max(1, RIM * w // 1024)
    inner = _box(np, fg.astype(np.float32), r) > 0.999
    fill = _fill(np, rgb, inner)
    lum = np.array([0.299, 0.587, 0.114], np.float32)
    lp, lf = _dot3(np, rgb, lum), _dot3(np, fill, lum)
    # only where the edge is DARKER than the island: highlights and drawn
    # light edges stay
    t = np.clip((lf - lp) / (0.25 * lf + 1e-4), 0, 1) * (fg & ~inner)
    t = np.where(fg, t, 1.0)[..., None]
    return rgb * (1 - t) + fill * t


def _rect(np, w, hgt, r, feather=4):
    yy, xx = np.mgrid[0:hgt, 0:w].astype(np.float32)
    x0, y0, x1, y1 = (r[0] * w / 1024, r[1] * hgt / 1024,
                      r[2] * w / 1024, r[3] * hgt / 1024)
    f = feather * w / 1024
    return (np.clip((xx - x0) / f + 0.5, 0, 1)
            * np.clip((x1 - xx) / f + 0.5, 0, 1)
            * np.clip((yy - y0) / f + 0.5, 0, 1)
            * np.clip((y1 - yy) / f + 0.5, 0, 1))


def _even_panel(np, rgb, r, hsv, target=None, hues=None, keep_shade=0.0):
    """Bring one atlas panel to a tone: hue and saturation to `hsv` (Nino's
    variation around the panel's median kept, compressed), and the
    large-scale value divided out to hsv[2] -- or to `target`, a per-texel
    (hue, sat, value) map. Gutter texels -- and with `hues`, texels outside
    that hue range (a neighbouring panel inside the rectangle) -- are left
    alone."""
    hgt, w = rgb.shape[:2]
    h, s, v = _rgb2hsv(np, rgb)
    keep = v >= GUTTER_V
    if hues:
        keep &= (h >= hues[0]) & (h < hues[1])
    m = _rect(np, w, hgt, r)
    ins = (m > 0.5) & keep
    if not ins.any():
        return rgb
    fi = ins.astype(np.float32)
    rad = max(2, 24 * w // 1024)
    lo = _box(np, v * fi, rad) / np.maximum(_box(np, fi, rad), 1e-3)
    th, ts, tv = target if target is not None else hsv
    nv = np.clip(v * (tv / np.maximum(lo, 0.05))
                 * (np.maximum(lo, 0.05) / tv) ** keep_shade, 0, 1)
    nh = th + (h - np.median(h[ins])) * 0.15
    ns = np.clip(ts + (s - np.median(s[ins])) * 0.5, 0, 1)
    m = (m * keep)[..., None]
    return rgb * (1 - m) + _hsv2rgb(np, nh, ns, nv) * m


def legs_to_skin(np, rgb):
    """BUILD 802b/c/e. Thigh blue at the knee, easing to lavender toes."""
    if os.environ.get(LEGS_ENV, '1').strip().lower() in (
            '0', 'off', 'false', 'no'):
        return rgb
    hgt, w = rgb.shape[:2]
    yy, xx = np.mgrid[0:hgt, 0:w].astype(np.float32) * (1024 / w)
    for r in THIGHS:
        rgb = _even_panel(np, rgb, r, THIGH_HSV, hues=(215, 252),
                          keep_shade=THIGH_KEEP)
    for r, (a, b, c) in ((CALF, LEG_H_CALF), (FOOT, LEG_H_FOOT)):
        t = np.clip((a * xx + b * yy + c - LEG_KNEE_H)
                    / (LEG_TOE_H - LEG_KNEE_H), 0, 1)
        target = tuple(np.interp(t, LEG_RAMP_T, ch) for ch in zip(*LEG_RAMP))
        rgb = _even_panel(np, rgb, r, THIGH_HSV, target=target)
    for r, hues in [(r, (215, 252)) for r in THIGHS] + [(CALF, (215, 290))]:
        rgb = _flatten(np, rgb, 1 - LEG_BLOTCH_KEEP, r, hues)   # not briefs
    cx, cy, rx, ry = (c * w / 1024 for c in KNEE_PIT)
    d = np.hypot((xx * w / 1024 - cx) / rx, (yy * w / 1024 - cy) / ry)
    m = np.clip((1.4 - d) / 0.6, 0, 1)
    return _flatten(np, rgb, m * m * (3 - 2 * m), CALF)


def skin_openings(np, rgb):
    """BUILD 802h. Purple texels inside the hip piece's skin triangles
    (SKIN_TRIS_P22, shrunk by a texel so the briefs' own edge is kept)
    take the colour of the skin around them."""
    hgt, w = rgb.shape[:2]
    k = w / 1024
    yy, xx = np.mgrid[0:hgt, 0:w].astype(np.float32) + 0.5
    inside = np.zeros((hgt, w), bool)
    for t in SKIN_TRIS_P22:
        (ax, ay), (bx, by), (cx, cy) = ((t[i] * k, t[i + 1] * k)
                                        for i in (0, 2, 4))
        x0, x1 = int(min(ax, bx, cx)), int(max(ax, bx, cx)) + 2
        y0, y1 = int(min(ay, by, cy)), int(max(ay, by, cy)) + 2
        X, Y = xx[y0:y1, x0:x1], yy[y0:y1, x0:x1]
        sgn = np.sign((bx - ax) * (cy - ay) - (by - ay) * (cx - ax))
        ok = np.ones(X.shape, bool)
        for (px, py), (qx, qy) in (((ax, ay), (bx, by)), ((bx, by), (cx, cy)),
                                   ((cx, cy), (ax, ay))):
            ln = max(np.hypot(qx - px, qy - py), 1e-6)
            ok &= sgn * ((qx - px) * (Y - py) - (qy - py) * (X - px)) / ln >= 1
        inside[y0:y1, x0:x1] |= ok
    h, _, v = _rgb2hsv(np, rgb)
    bad = inside & (v >= GUTTER_V) & (h >= 255) & (h < 320)
    if not bad.any():
        return rgb
    known = inside & (v >= GUTTER_V) & ~bad
    return np.where(bad[..., None], _fill(np, rgb, known), rgb)


def even_briefs(np, rgb):
    """BUILD 802h. One piece of fabric: the briefs band's blotches and
    saturation spread are evened (BRIEFS); its broad shading stays."""
    hgt, w = rgb.shape[:2]
    rgb = _flatten(np, rgb, 1 - BRIEFS_KEEP, BRIEFS, BRIEFS_HUES)
    h, s, v = _rgb2hsv(np, rgb)
    m = ((v >= GUTTER_V) & (h >= BRIEFS_HUES[0]) & (h < BRIEFS_HUES[1])
         & (_rect(np, w, hgt, BRIEFS) > 0.5))
    if not m.any():
        return rgb
    ns = np.median(s[m]) + (s - np.median(s[m])) * BRIEFS_S_KEEP
    rgb = np.where(m[..., None], _hsv2rgb(np, h, ns, v), rgb)
    tone = (float(np.median(h[m])), float(np.median(s[m])) * BRIEFS_SAT,
            float(np.median(v[m])))
    return _even_panel(np, rgb, BRIEFS, tone, hues=BRIEFS_HUES,
                       keep_shade=BRIEFS_SHADE)


def briefs_volume(np, rgb):
    """BUILD 802l. Light the briefs band by the shape it wraps (BRIEFS_LIGHT):
    each cheek lit on its crown, shading off toward the sides, the leg
    openings and the under-curve, so the fabric shows her shape."""
    import base64
    import zlib
    hgt, w = rgb.shape[:2]
    k = w / 1024
    gh, gw = BRIEFS_LIGHT_SHAPE
    lt = np.frombuffer(zlib.decompress(base64.b64decode(''.join(BRIEFS_LIGHT))),
                       np.uint8).reshape(gh, gw).astype(np.float32) / 200
    x0, y0 = BRIEFS[0], BRIEFS[1]
    yy, xx = np.mgrid[0:hgt, 0:w].astype(np.float32)
    u = np.clip((xx / k - x0) / BRIEFS_LIGHT_CELL - 0.5, 0, gw - 1)
    v = np.clip((yy / k - y0) / BRIEFS_LIGHT_CELL - 0.5, 0, gh - 1)
    u0, v0 = np.minimum(u.astype(int), gw - 2), np.minimum(v.astype(int), gh - 2)
    fu, fv = u - u0, v - v0
    light = ((lt[v0, u0] * (1 - fu) + lt[v0, u0 + 1] * fu) * (1 - fv)
             + (lt[v0 + 1, u0] * (1 - fu) + lt[v0 + 1, u0 + 1] * fu) * fv)
    h, s, val = _rgb2hsv(np, rgb)
    m = ((val >= GUTTER_V) & (h >= BRIEFS_HUES[0]) & (h < BRIEFS_HUES[1])
         & (_rect(np, w, hgt, BRIEFS) > 0.5))
    if not m.any():
        return rgb
    sh = (1 - BRIEFS_LIGHT_GAIN) + BRIEFS_LIGHT_GAIN * light
    sh = sh / np.median(sh[m])
    return np.where(m[..., None], np.clip(rgb * sh[..., None], 0, 1), rgb)


def briefs_seam(np, rgb, broad=True, fade=True):
    """BUILD 802f/g. Shade the briefs' back centre line (BRIEFS_SEAM). Runs
    on Nino's atlas before the gutter fill, which then carries the shade
    into the gutter, so both mirrored halves darken toward the seam."""
    hgt, w = rgb.shape[:2]
    k = w / 1024
    x0, y0, x1, y1 = (round(c * k) for c in BRIEFS_SEAM)
    h, s, v = _rgb2hsv(np, rgb[y0:y1, x0:x1])
    clothes = (v >= GUTTER_V) & (h >= 255) & (h < 300)
    edge = clothes[:, :-1] & (v[:, 1:] < GUTTER_V)
    rows = np.nonzero(edge.any(1))[0]
    if len(rows) < 2:
        return rgb
    xs = np.arange(x1 - x0)
    ex = np.where(edge, xs[:-1], -1).max(1)[rows]          # rightmost per row
    yy, xx = np.mgrid[0:y1 - y0, 0:x1 - x0].astype(np.float32)
    d = np.full(yy.shape, np.inf, np.float32)
    for (ax, ay), (bx, by) in zip(zip(ex[:-1], rows[:-1]),
                                  zip(ex[1:], rows[1:])):
        dx, dy = float(bx - ax), float(by - ay)
        t = np.clip(((xx - ax) * dx + (yy - ay) * dy) / (dx * dx + dy * dy),
                    0, 1)
        d = np.minimum(d, np.hypot(xx - ax - t * dx, yy - ay - t * dy))
    dp = d / k
    d = d / (SEAM_W * k)
    up = np.clip((yy - rows[0]) / max(rows[-1] - rows[0], 1), 0, 1)
    up = np.clip((up - 0.6) / 0.4, 0, 1) * fade  # fades over the top 40%
    depth = (1 - SEAM_DARK) * (1 - (1 - SEAM_TOP) * up * up * (3 - 2 * up))
    f = np.where(clothes & (d < 1), 1 - depth * (1 - d) ** 2, 1)
    if broad:
        bw, bd = SEAM_BROAD
        db = np.clip(dp / bw, 0, 1)
        f = f * np.where(clothes, 1 - bd * (1 - up * up * (3 - 2 * up)) * (1 - db) ** 2, 1)
    out = rgb.copy()
    out[y0:y1, x0:x1] *= f[..., None]
    return out


def _flatten(np, rgb, m, r, hues=(215, 290)):
    """BUILD 802f/g. Divide mid-scale shading (8-80 texels) out of panel r,
    weighted by m. Only the panel's own skin-to-lavender texels are changed
    or counted: the gutter fill around it is darker, and beside the calf
    it carries the hair strip's teal."""
    hgt, w = rgb.shape[:2]
    k = w / 1024
    h, _, v = _rgb2hsv(np, rgb)
    f = ((v >= GUTTER_V) & (h >= hues[0]) & (h < hues[1])
         & (_rect(np, w, hgt, r) > 0.5)).astype(np.float32)

    def blur(r):
        r = max(1, round(r * k))
        return _box(np, v * f, r) / np.maximum(_box(np, f, r), 1e-3)
    gain = np.clip(blur(40) / np.maximum(blur(4), 0.02), 0.7, 1.4)
    return np.clip(rgb * (1 + (gain - 1) * m * f)[..., None], 0, 1)


def even_arms(np, rgb):
    """BUILD 802c. Forearms and hands in the upper arm's tone (see notes)."""
    for r in ARMS:
        rgb = _even_panel(np, rgb, r, ARM_HSV, hues=(215, 252))
    return rgb


def match_joints(np, new):
    """BUILD 802d. Each limb panel continues its parent's colour across the
    joint (see JOINTS). new: the recoloured atlas, 1024 square."""
    hgt, w = new.shape[:2]
    if (w, hgt) != (1024, 1024):
        return new
    out = new.copy()
    yy, xx = np.mgrid[0:hgt, 0:w].astype(np.float32)

    def mean_at(pts):
        acc = []
        for x, y in pts:
            win = out[max(0, y - 3):y + 4, max(0, x - 3):x + 4].reshape(-1, 3)
            win = win[win.max(1) > 0.05]
            if len(win):
                acc.append(win.mean(0))
        # median: a seam point on Nino's dark panel rim (the upper arm's
        # corners) must not drag the match down
        return np.median(acc, 0) if acc else None

    for name, ppts, cpts, r, fade in JOINTS:
        pc, cc = mean_at(ppts), mean_at(cpts)
        if pc is None or cc is None:
            continue
        ratio = np.clip(pc / np.maximum(cc, 1e-3), 0.6, 1.6)
        d = np.full((hgt, w), np.inf, np.float32)
        for x, y in cpts:
            d = np.minimum(d, np.hypot(xx - x, yy - y))
        wt = np.clip(1 - d / fade, 0, 1)
        wt = wt * wt * (3 - 2 * wt) * _rect(np, w, hgt, r)
        if name == 'wrist':
            wt = wt * (1 - _rect(np, w, hgt, HAIR_TIE))
        gain = 1 + (ratio[None, None, :] - 1) * wt[..., None]
        out = np.clip(out * gain, 0, 1)
    return out


def even_hair(np, rgb):
    """BUILD 802c. The whip strips evened to one hair tone (see notes)."""
    for r in HAIR_STRIPS:
        rgb = _even_panel(np, rgb, r, HAIR_HSV, hues=(140, 225))
    return rgb


def purple_lashes(np, src, new):
    """BUILD 802c. src: the cleaned source, new: the recoloured atlas (same
    shape); the lash sprite and the gutter round it go dark purple."""
    hgt, w = src.shape[:2]
    h, s, v = _rgb2hsv(np, src)
    x0, y0, x1, y1 = (LASHES[0] * w // 1024, LASHES[1] * hgt // 1024,
                      LASHES[2] * w // 1024, LASHES[3] * hgt // 1024)
    hh, ss, vv = h[y0:y1, x0:x1], s[y0:y1, x0:x1], v[y0:y1, x0:x1]
    lash = (hh >= 185) & (hh < 228) & (ss > 0.3)
    if not lash.any():
        return new
    m = _box(np, lash.astype(np.float32), max(1, w // 512)).clip(0, 1)
    col = _hsv2rgb(np, np.full_like(hh, LASH_HSV[0]),
                   np.full_like(ss, LASH_HSV[1]),
                   np.clip(0.12 + 0.4 * vv, 0, 1))
    out = new.copy()
    out[y0:y1, x0:x1] = new[y0:y1, x0:x1] * (1 - m[..., None]) \
        + col * m[..., None]
    return out


def prefilter(np, rgb):
    """BUILD 802. 2x2 box, anchored so tex.py's nearest-neighbour halving
    (source texel 2x, 2y) picks the area average of each 2x2 block."""
    p = np.pad(rgb, ((0, 1), (0, 1), (0, 0)), mode='edge')
    return (p[:-1, :-1] + p[1:, :-1] + p[:-1, 1:] + p[1:, 1:]) / 4


def _opening(np, w, hgt):
    """The eye opening (iris and whites) as a 0..1 mask: an almond polygon
    inside Nino's liner, feathered by about a pixel."""
    from PIL import Image, ImageDraw
    k = 4
    im = Image.new('L', (w * k // 4, hgt * k // 4), 0)
    pts = [(x * w / 1024 * k / 4, y * hgt / 1024 * k / 4) for x, y in OPENING]
    ImageDraw.Draw(im).polygon(pts, fill=255)
    m = np.asarray(im, np.float32) / 255
    return _box(np, m, max(1, w // 1024))


def _med3(np, x):
    p = np.pad(x, [(1, 1), (1, 1)] + [(0, 0)] * (x.ndim - 2), mode='edge')
    st = np.stack([p[1 + dy:p.shape[0] - 1 + dy, 1 + dx:p.shape[1] - 1 + dx]
                   for dy in (-1, 0, 1) for dx in (-1, 0, 1)])
    return np.median(st, 0)


def clean_clothes_edges(np, new, gutter=None, keep=None, seam=None):
    """BUILD 802i/j. Skin and clothes each keep their own colour right up to
    their edges (CLOTHES_EDGE), and body panels lose isolated dark dots
    (BODY, SPECK). new: the recoloured atlas; gutter: Nino's empty texels
    (the edges of each UV island are edges on the model too); keep: texels
    left alone (the briefs crease)."""
    hgt, w = new.shape[:2]
    k = w / 1024
    body = np.zeros((hgt, w), bool)
    for r in BODY:
        body |= _rect(np, w, hgt, r) > 0.5
    # dots: texels much darker than everything round them; three rounds,
    # so a clump of a few is eaten from its edge in
    out = new
    for _ in range(3):
        med = np.stack([_med3(np, out[..., c]) for c in range(3)], -1)
        dot = body & (out.max(-1) < med.max(-1) - SPECK) & (med.max(-1) > 0.2)
        if not dot.any():
            break
        out = np.where(dot[..., None], med, out)
    h, _, v = _rgb2hsv(np, out)
    zone = np.zeros((hgt, w), bool)
    for r in CLOTHES_EDGE:
        zone |= _rect(np, w, hgt, r) > 0.5
    if gutter is None:
        gutter = v < GUTTER_V
    live = zone & ~gutter & (v >= GUTTER_V)
    skin = live & (h >= 195) & (h < EDGE_SPLIT)
    cloth = live & (h >= EDGE_SPLIT) & (h < 330)
    # which side a texel is on: the majority of the live texels round it,
    # so Nino's stray pale or dark edge texels follow their surroundings
    r5 = max(1, round(5 * k))
    nc = _box(np, cloth.astype(np.float32), r5)
    ns = _box(np, skin.astype(np.float32), r5)
    side_c = zone & (nc > ns) & (nc + ns > 0)
    # a gutter texel goes with the island nearest it: that is the texel
    # filtering mixes into the island's edge on the model
    ind = np.stack([cloth, skin, skin], -1).astype(np.float32)
    near = _fill(np, ind, skin | cloth)
    side_c = np.where(gutter, zone & (near[..., 0] > near[..., 1]), side_c)
    side_s = zone & ~side_c & ((nc + ns > 0) | gutter)
    rg = max(1, round(EDGE_G * k))
    near_g = _box(np, (zone & gutter).astype(np.float32), rg) > 0
    free = np.ones((hgt, w), bool) if keep is None else ~(keep & ~gutter)
    for mine, raw, other, er in ((side_s, skin, side_c, EDGE_R[0]),
                                 (side_c, cloth, side_s, EDGE_R[1])):
        rr = max(1, round(er * k))
        near_o = _box(np, other.astype(np.float32), rr) > 0
        band = mine & (near_o | near_g)
        inner = mine & raw & ~band
        if not (band.any() and inner.any()):
            continue
        ref = _fill(np, out, inner)
        # the colour just inside, smoothed along the edge so the line is
        # one even colour rather than patches of its nearest texels
        rr2 = max(1, round(3 * k))
        f = mine.astype(np.float32)
        ref = (np.stack([_box(np, ref[..., c] * f, rr2) for c in range(3)], -1)
               / np.maximum(_box(np, f, rr2), 1e-6)[..., None])
        rh, _, rv = _rgb2hsv(np, ref)
        dh = np.abs((h - rh + 180) % 360 - 180)
        off = band & ((v < rv * 0.97) | (dh > 6) | ~raw)
        out = np.where((off & free)[..., None], ref, out)
    seam = keep if seam is None else seam
    if seam is not None and (seam & gutter).any():
        # the gutter along the crease: the shaded edge it borders, so the
        # filtering on the model sees the crease and nothing darker
        out = np.where((seam & gutter)[..., None],
                       _fill(np, out, seam & ~gutter), out)
    return out


def _dot3(np, a, m):
    """a (..., 3) times m (3,) or (j, 3) transposed: a @ m.T, but summed
    channel by channel. Matmul goes through BLAS, and macOS's Accelerate
    raises false divide-by-zero / overflow / invalid flags on it (numpy
    RuntimeWarnings on Apple silicon, with every input in 0..1)."""
    m = np.asarray(m, np.float32)
    if m.ndim == 1:
        return a[..., 0] * m[0] + a[..., 1] * m[1] + a[..., 2] * m[2]
    return (a[..., 0, None] * m[:, 0] + a[..., 1, None] * m[:, 1]
            + a[..., 2, None] * m[:, 2])


def _oklab(np, c):
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    m1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                   [0.2119034982, 0.6806995451, 0.1073969566],
                   [0.0883024619, 0.2817188376, 0.6299787005]], np.float32)
    m2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                   [1.9779984951, -2.4285922050, 0.4505937099],
                   [0.0259040371, 0.7827717662, -0.8086757660]], np.float32)
    return _dot3(np, np.cbrt(_dot3(np, c, m1)), m2).astype(np.float32)


def palette255(np, rgb8, n=255, iters=12):
    """BUILD 802h. Pick the atlas's 255 colours here, so the battle
    conversion's median cut (tex.convert_for_battle) only has to keep them:
    with no more colours than it has entries, each of its boxes is one
    colour. Its own cut is weighted by texel count, so the big flat panels
    took the entries and every smaller gradient -- the knee-to-foot blend,
    the briefs -- was cut into a few wide flat contours. This is k-means in
    OKLab with each colour weighted by the square root of its count, so a
    gradient gets entries in proportion to its spread."""
    if os.environ.get(PALETTE_ENV, '1').strip().lower() in (
            '0', 'off', 'false', 'no'):
        return rgb8
    flat = rgb8.reshape(-1, 3).astype(np.int64)
    key = (flat[:, 0] << 16) | (flat[:, 1] << 8) | flat[:, 2]
    uk, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
    if len(uk) <= n:
        return rgb8
    cols = np.stack([(uk >> 16) & 255, (uk >> 8) & 255, uk & 255],
                    1).astype(np.float32) / 255
    lab = _oklab(np, cols)
    wts = np.sqrt(cnt.astype(np.float64))
    rng = np.random.default_rng(802)

    def nearest(cen):
        out = np.empty(len(lab), np.int64)
        c2 = (cen ** 2).sum(1)[None]
        for i in range(0, len(lab), 16384):
            blk = lab[i:i + 16384]
            out[i:i + 16384] = (c2 - 2 * _dot3(np, blk, cen)).argmin(1)
        return out
    pick = [rng.choice(len(lab), p=wts / wts.sum())]      # k-means++
    d = ((lab - lab[pick[0]]) ** 2).sum(1)
    while len(pick) < n:
        q = d * wts
        pick.append(rng.choice(len(lab), p=q / q.sum()))
        d = np.minimum(d, ((lab - lab[pick[-1]]) ** 2).sum(1))
    cen = lab[pick].copy()
    for _ in range(iters):
        a = nearest(cen)
        sw = np.bincount(a, wts, n)
        for ch in range(3):
            m = np.bincount(a, wts * lab[:, ch], n)
            cen[:, ch] = np.where(sw > 0, m / np.maximum(sw, 1e-12),
                                  cen[:, ch])
    a = nearest(cen)
    sw = np.maximum(np.bincount(a, wts, n), 1e-12)
    pal = np.stack([np.bincount(a, wts * cols[:, ch], n) / sw
                    for ch in range(3)], 1)
    pal8 = np.clip(np.rint(pal * 255), 0, 255).astype(np.uint8)
    return pal8[a][inv.ravel()].reshape(rgb8.shape)


def true_sides(np, new, keep=None, orig=None):
    """BUILD 802n. Skin the mesh draws as skin is skin-coloured, and clothes
    are clothes-coloured (SIDES): purple blots and dark patches left on the
    skin beside the clothes, and skin-blue specks inside them, take the
    colour round them; the gutter between takes the nearer side's colour.
    keep: texels left alone."""
    import base64
    import zlib
    hgt, w = new.shape[:2]
    if w != hgt:
        return new
    k = w / 1024
    sm = np.frombuffer(zlib.decompress(base64.b64decode(''.join(SIDES))),
                       np.uint8).reshape(SIDES_SHAPE)
    yy, xx = np.mgrid[0:hgt, 0:w]
    sy = yy / k - SIDES_ORIGIN[1]
    sx = xx / k - SIDES_ORIGIN[0]
    ok = (sy >= 0) & (sy < SIDES_SHAPE[0]) & (sx >= 0) & (sx < SIDES_SHAPE[1])
    cls = np.where(ok, sm[np.clip(sy.astype(int), 0, SIDES_SHAPE[0] - 1),
                          np.clip(sx.astype(int), 0, SIDES_SHAPE[1] - 1)], 0)
    r = max(1, round(k))
    h, _, v = _rgb2hsv(np, new)
    keep = np.zeros((hgt, w), bool) if keep is None else keep
    reach = (_box(np, (cls == 3).astype(np.float32),
                  max(1, round(MIXED_REACH * k))) > 0) & (cls != 0)
    out = new
    cores = []
    for side in (1, 2):
        core = cls == side
        if side == 2:
            core = _box(np, core.astype(np.float32), r) > 0.999
        cores.append(core)
        if not core.any():
            continue
        if side == 1:
            # violet on skin, or a patch darker than the skin round it
            fi = core.astype(np.float32)
            rad = max(2, round(20 * k))
            lv = _box(np, v * fi, rad) / np.maximum(_box(np, fi, rad), 1e-3)
            # only near the clothes: the navel and the body's own shading
            # stay
            by = _box(np, (cls == 2).astype(np.float32),
                      max(1, round(SIDES_NEAR * k))) > 0
            bad = core & by & (((h >= SIDES_SKIN_HUE) & (h < 340))
                               | (v < lv * SIDES_DARK))
        else:
            bad = core & (h >= 150) & (h < 240)
        if orig is not None:
            bad &= ~reach
        bad = (_box(np, (bad & ~keep).astype(np.float32), 1) > 0) & core & ~keep
        if orig is not None:
            bad &= ~reach
        if bad.any():
            out = np.where(bad[..., None], _fill(np, out, core & ~bad), out)
    if orig is not None:
        # in the triangles Nino paints both on, each texel follows Nino:
        # his skin stays skin-coloured, his fabric fabric-coloured (802o);
        # his ragged edge is taken by majority, so it comes out smooth
        oh, os_, ov = _rgb2hsv(np, orig)
        o_skin = _box(np, ((oh >= 195) & (oh < 255) & (ov > 0.25))
                      .astype(np.float32), 2 * r) > 0.5
        o_cloth = ((oh >= 255) & (oh < 335) & (os_ > 0.2) & (ov > 0.1)
                   & ~o_skin)
        mixed = ((_box(np, (cls == 3).astype(np.float32), r) > 0) | reach) & ~keep
        h, _, v = _rgb2hsv(np, out)
        fi = (o_skin & (h < 240)).astype(np.float32)
        rad = max(2, round(8 * k))
        lv = _box(np, v * fi, rad) / np.maximum(_box(np, fi, rad), 1e-3)
        for want, wrong in ((o_skin, ((h >= 240) & (h < 340))
                             | (v < lv * SIDES_DARK)),
                            (o_cloth, (h >= 150) & (h < 245))):
            bad = mixed & want & wrong
            good = want & ~wrong & (_box(np, mixed.astype(np.float32),
                                         round(6 * k)) > 0)
            if bad.any() and good.any():
                out = np.where(bad[..., None], _fill(np, out, good), out)
    # the gutter between a skin and a clothes island: the nearer one's colour
    skin, cloth = cores
    gap = ((cls == 0) & ~keep
           & (_box(np, skin.astype(np.float32), round(6 * k)) > 0)
           & (_box(np, cloth.astype(np.float32), round(6 * k)) > 0))
    if gap.any():
        near = _fill(np, np.stack([cloth, skin, skin], -1).astype(np.float32),
                     skin | cloth)
        to_cloth = gap & (near[..., 0] > near[..., 1])
        out = np.where(to_cloth[..., None], _fill(np, out, cloth), out)
        out = np.where((gap & ~to_cloth)[..., None], _fill(np, out, skin), out)
    return out


def briefs_tone(np, new, keep=None):

    """BUILD 802m. After the recolour the briefs, and the top (the chest
    panel's clothes), are each one hue and one saturation (their median);
    only the light and dark vary, so no hue or saturation contours run
    across the fabric."""
    hgt, w = new.shape[:2]
    h, s, v = _rgb2hsv(np, new)
    for r in (BRIEFS, CLOTHES_EDGE[1]):
        wt = (np.clip((h - BRIEFS_HUES[0] + 10) / 12, 0, 1)
              * (h < BRIEFS_HUES[1]) * (v > 0.12)
              * (_rect(np, w, hgt, r) > 0.5))
        if keep is not None:
            wt = wt * ~keep
        m = wt > 0.99
        if m.any():
            h = h + (float(np.median(h[m])) - h) * wt
            s = s + (float(np.median(s[m])) - s) * wt
    return _hsv2rgb(np, h, s, v)


def clothes_hem(np, new, gutter=None, keep=None):
    """BUILD 802m. Darken a thin line on the clothes' side of each
    CLOTHES_HEM edge, so the fabric has a clean hem where it meets skin.
    gutter: where the hem may continue past the edge; keep: texels it
    leaves alone (the emblem's trim)."""
    hgt, w = new.shape[:2]
    k = w / 1024
    f = np.full((hgt, w), 9, np.float32)
    out = np.zeros((hgt, w), bool)
    h, _, _ = _rgb2hsv(np, new)
    cloth = (h >= BRIEFS_HUES[0] - 10) & (h < BRIEFS_HUES[1] + 15)
    for seg in CLOTHES_HEM:
        ax, ay, bx, by = (c * k / 2 for c in seg[:4])
        hw = HEM_W * k * min(max(seg[4], HEM_SCALE[0]), HEM_SCALE[1]) / 10
        x0 = max(0, int(min(ax, bx) - 2.5 * hw - 2))
        x1 = min(w, int(max(ax, bx) + 2.5 * hw + 2))
        y0 = max(0, int(min(ay, by) - 2.5 * hw - 2))
        y1 = min(hgt, int(max(ay, by) + 2.5 * hw + 2))
        if x0 >= x1 or y0 >= y1:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32) + 0.5
        dx, dy = bx - ax, by - ay
        ll = max(dx * dx + dy * dy, 1e-6)
        t = np.clip(((xx - ax) * dx + (yy - ay) * dy) / ll, 0, 1)
        d = np.hypot(xx - ax - t * dx, yy - ay - t * dy)
        inner = (dx * (yy - ay) - dy * (xx - ax)) / np.sqrt(ll) > -0.5 * k
        u = np.where(inner & cloth[y0:y1, x0:x1], d / hw, 9)
        f[y0:y1, x0:x1] = np.minimum(f[y0:y1, x0:x1], u)
        o = ~inner & (d < HEM_OUT * k)
        out[y0:y1, x0:x1] |= o
    # f: distance to the nearest edge in hem widths. Nino's own dark rim
    # along the edge is lifted to the fabric's tone first, so the hem is
    # one even line instead of hem plus rim
    fi = (cloth & (f >= 2.5)).astype(np.float32)
    rad = max(2, round(10 * k))
    den = np.maximum(_box(np, fi, rad), 1e-3)
    ref = np.stack([_box(np, new[..., c] * fi, rad) / den for c in range(3)],
                   -1)
    near = f < 2.5
    if keep is not None:
        near &= ~keep
    new = np.where(near[..., None] & (fi[..., None] == 0),
                   np.maximum(new, ref * 0.92), new)
    u = np.clip(f, 0, 1)
    f = 1 - HEM_DARK * (1 - u * u * (3 - 2 * u))
    hem = f < 0.999
    # just past the edge the hem continues, so filtering on the model draws
    # a clean line instead of a staircase of skin and hem texels
    out &= ~hem
    if gutter is not None:
        out &= gutter
    if keep is not None:
        f = np.where(keep, 1, f)
        out &= ~keep
        hem &= ~keep
    new = new * f[..., None]
    if hem.any() and out.any():
        new = np.where(out[..., None], _fill(np, new, hem), new)
    return new


def painted_hem(np, new, orig, keep=None):
    """BUILD 802o. Where Nino paints the fabric's edge through a triangle
    (SIDES 3: the cups' top edge toward the arms, parts of the leg
    openings) the mesh has no edge to hem, so the hem follows his paint:
    a smoothed line along his skin/fabric edge, the fabric beside it
    darkening like CLOTHES_HEM's (802p: no staircase)."""
    import base64
    import zlib
    hgt, w = new.shape[:2]
    if w != hgt:
        return new
    k = w / 1024
    sm = np.frombuffer(zlib.decompress(base64.b64decode(''.join(SIDES))),
                       np.uint8).reshape(SIDES_SHAPE)
    yy, xx = np.mgrid[0:hgt, 0:w]
    sy, sx = yy / k - SIDES_ORIGIN[1], xx / k - SIDES_ORIGIN[0]
    ok = (sy >= 0) & (sy < SIDES_SHAPE[0]) & (sx >= 0) & (sx < SIDES_SHAPE[1])
    cls = np.where(ok, sm[np.clip(sy.astype(int), 0, SIDES_SHAPE[0] - 1),
                          np.clip(sx.astype(int), 0, SIDES_SHAPE[1] - 1)], 0)
    mixed = ((_box(np, (cls == 3).astype(np.float32), max(1, round(k))) > 0)
             | ((_box(np, (cls == 3).astype(np.float32),
                      max(1, round(MIXED_REACH * k))) > 0) & (cls != 0)))
    oh, os_, ov = _rgb2hsv(np, orig)
    o_skin = ((oh >= 195) & (oh < 255) & (ov > 0.25)).astype(np.float32)
    # his edge is a staircase texel to texel (and ragged): the edge drawn
    # is the half-way line of a blurred skin field, s / |grad s| texels
    # from it, so it runs smooth, and it is antialiased over one texel
    r = max(1, round(2 * k))
    sf = _box(np, _box(np, o_skin, r), r) - 0.5
    gy = np.zeros_like(sf)
    gx = np.zeros_like(sf)
    gy[1:-1] = (sf[2:] - sf[:-2]) / 2
    gx[:, 1:-1] = (sf[:, 2:] - sf[:, :-2]) / 2
    d = sf / np.maximum(np.hypot(gx, gy), 0.02)      # + skin, - fabric
    sel = mixed & (ov > GUTTER_V) & (np.abs(d) < HEM_W * k + 2)
    if keep is not None:
        sel &= ~keep
    if not sel.any():
        return new
    skin = mixed & (d > 1.5)
    cloth = mixed & (d < -HEM_W * k - 1)
    sc = _fill(np, new, skin) if skin.any() else new
    # skin already skin-coloured keeps its own tone (a far fill left a
    # light fleck where the edge meets a mesh edge)
    nh, _, _ = _rgb2hsv(np, new)
    sc = np.where(((nh >= 195) & (nh < SIDES_SKIN_HUE))[..., None], new, sc)
    cc = _fill(np, new, cloth) if cloth.any() else new
    u = np.clip(-d / (HEM_W * k), 0, 1)
    hem = cc * (1 - HEM_DARK * (1 - u * u * (3 - 2 * u)))[..., None]
    a = np.clip(d + 0.5, 0, 1)[..., None]              # skin's share
    return np.where(sel[..., None], sc * a + hem * (1 - a), new)


def recolor_tex(data):
    """A truecolor (24/32-bit) TEX with Shiva's colours corrected, or None."""
    import numpy as np
    import tex
    t = tex.parse(data)
    if not t or t['bytes_per_pixel'] not in (3, 4) or t['palette_size']:
        return None
    w, hgt, bpp = t['width'], t['height'], t['bytes_per_pixel']
    # FF7 truecolor TEX: the channel shifts are in the header (red mask at
    # +0x7C); this file family stores B,G,R(,A) in memory.
    red_mask = struct.unpack_from('<I', data, 0x7C)[0]
    order = [2, 1, 0] if red_mask == 0xFF0000 else [0, 1, 2]
    px = np.frombuffer(t['pixels'], np.uint8).reshape(hgt, w, bpp)
    rgb = px[..., order].astype(np.float32) / 255
    orig = rgb
    if min(w, hgt) >= 256:          # Nino's atlas, not a stand-in
        gutter = rgb.max(-1) < GUTTER_V
        # Nino's dark halo around the clothes' islands counts as gutter
        zone = sum(_rect(np, w, hgt, r, 1) for r in CLOTHES_EDGE) > 0.5
        near = _box(np, gutter.astype(np.float32), max(1, round(4 * w / 1024))) > 0
        halo = zone & near & (rgb.max(-1) < HALO_V)
        # Nino's gold trim (the halter's emblem): recoloured to silver texel
        # by texel and left alone by the edge passes after (802n)
        gh, gs, gv = _rgb2hsv(np, rgb)
        gold = _box(np, (zone & (gh >= 20) & (gh < 75) & (gs > 0.25)
                         & (gv > 0.12)).astype(np.float32),
                    max(1, round(w / 1024))) > 0
        gold |= _rect(np, w, hgt, HALTER, 1) > 0.5
        rgb = briefs_volume(np, even_briefs(np, skin_openings(np, rgb)))
        # where the crease is drawn deep enough to show (not its faded top)
        crease, seam = (_box(np, (briefs_seam(np, rgb, False, f).sum(-1)
                              < rgb.sum(-1) * 0.97).astype(np.float32),
                             max(1, round(r * w / 1024))) > 0
                        for f, r in ((True, 2), (False, 5)))
        rgb = clean_edges(np, briefs_seam(np, rgb))
        # the seam's dark halo and gutter take the crease's own purple, so
        # the recolour's smoothing sees no skin or black beside it
        hs = (halo | gutter) & seam
        h, _, _ = _rgb2hsv(np, rgb)
        src = (seam & (h >= BRIEFS_HUES[0]) & (h < BRIEFS_HUES[1])
               & ~(_box(np, hs.astype(np.float32), max(1, round(2 * w / 1024))) > 0))
        if src.any():
            rgb = np.where(hs[..., None], _fill(np, rgb, src), rgb)
        rgb = legs_to_skin(np, rgb)
        rgb = even_hair(np, rgb)
        rgb = even_arms(np, rgb)
    new = recolor_rgb(np, rgb, smooth=max(2, w // 170))
    if min(w, hgt) >= 256:
        # the seam's purple is recoloured texel by texel: the class blur
        # would pull the skin island beside it into the crease (a blue line)
        sz = ((_box(np, (hs | src).astype(np.float32), 1) > 0) | gold)[..., None]
        new = np.where(sz, recolor_rgb(np, rgb), new)
    # The face (BUILD 785b, against the PS1 face): brows and liner take the
    # hair's pale aqua instead of Nino's purple; only the eye opening (iris
    # and whites) keeps Nino's exact pixels.
    if min(w, hgt) >= 256:          # the face pass needs Nino's atlas
        yy, xx = np.mgrid[0:hgt, 0:w].astype(np.float32)

        def _ell(e):
            cx, cy, rx, ry, fe = (e[0] * w, e[1] * hgt, e[2] * w, e[3] * hgt,
                                  e[4] * w)
            dist = np.sqrt(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2)
            return np.clip(1 - (dist - 1) * min(rx, ry) / fe, 0, 1)
        h, s, v = _rgb2hsv(np, rgb)
        # BUILD 785d: one continuous makeup ramp instead of three regions, so
        # nothing bands. Inside the lid area every saturated (makeup) pixel is
        # recoloured by its darkness alone: dark -> indigo lashes, light ->
        # lavender lid. Beyond the lid only the dark brow stroke changes, to
        # aqua. In the eye opening only the iris (cyan) and the whites (grey)
        # keep Nino's pixels -- his teal liner there is makeup like the rest.
        makeup = (v > 0.06) * np.clip((s - 0.18) / 0.1, 0, 1)
        makeup = _box(np, makeup.astype(np.float32), max(1, w // 400))  # 785f: no speckle
        lt = np.clip((v - 0.28) / 0.45, 0, 1)
        # The face patch is upside down: atlas rows below the eye are the lid
        # (light makeup -> lavender), rows above it are under the eye (light
        # makeup -> plain skin; the stock face has no colour there).
        side = np.clip((yy - EYE[1] * hgt) / (8 * hgt / 1024), 0, 1)
        # BUILD 785f: the stock face (PC texture) has one solid brow/lid
        # shade -- hue ~232, s .42, v .64 -- over an indigo lash line, and
        # nothing under the eye. Dark makeup -> indigo; everything else on
        # the lid side -> that solid shade; under the eye -> plain skin.
        lash_h, lash_s = 266.0, 0.55     # 802c: darker purple (stock)
        solid = (232.0, 0.42, 0.64)
        ramp_h = (lash_h + (solid[0] - lash_h) * lt * side
                  + (225 - lash_h) * lt * (1 - side))
        ramp_s = (lash_s + (solid[1] - lash_s) * lt * side
                  + (0.16 - lash_s) * lt * (1 - side))
        ramp_v = ((0.22 + 0.3 * v) * (1 - lt)
                  + lt * ((0.22 + 0.62 * v) * side + v * (1 - side)))
        ramp = _hsv2rgb(np, ramp_h, ramp_s, np.minimum(1, ramp_v))
        near = _ell(LINER)
        far = (_ell(BROW) - near).clip(0, 1)
        # one hue and saturation, but Nino's own light/dark kept, so the
        # brow strokes stay ("ruining the details of her eyebrow")
        flat = _hsv2rgb(np, np.full_like(h, solid[0]),
                        np.full_like(s, solid[1]),
                        np.clip(0.22 + 0.62 * v, 0, 1))
        # BUILD 785h: the brow itself (Nino's outermost dark stroke) is the
        # hair's green-aqua, with its own light and dark; the lid crease
        # inside it stays the blue shade.
        # BUILD 785k: the brow is Nino's outermost dark line -- the dark
        # pixels with bare skin just beyond them (toward the forehead: +y in
        # this upside-down patch, or -x along the tail). Everything else in
        # the zone is lid. Picked by that shape, not by an ellipse, so the
        # line is green along its whole length and nothing else is.
        bare = (v > 0.85) & (s < 0.2)
        beyond = np.zeros_like(bare)
        k = max(1, (13 * w) // 1024)
        for d in range(1, k + 1):
            beyond[:-d, :] |= bare[d:, :]
            beyond[:, d:] |= bare[:, :-d]
        stroke = (np.clip((0.55 - v) / 0.12, 0, 1) * beyond
                  * np.clip((s - 0.25) / 0.1, 0, 1))
        outer = _box(np, (stroke * side * _ell(BROW)).astype(np.float32),
                     max(1, w // 1024)).clip(0, 1) * far
        browc = _hsv2rgb(np, np.full_like(h, 182), np.full_like(s, 0.45),
                         np.clip(0.3 + 0.65 * v, 0, 1))
        w_ramp = (near * makeup)[..., None]
        w_brow = (outer * makeup)[..., None]
        w_flat = ((far - outer).clip(0, 1) * makeup)[..., None]
        new = (w_ramp * ramp + w_flat * flat + w_brow * browc
               + (1 - w_ramp - w_flat - w_brow) * new)
        # Lips a little darker, the forehead gem purple (stock face).
        lips = (_ell(LIPS) * makeup)[..., None]
        lipc = _hsv2rgb(np, np.full_like(h, 250), np.full_like(s, 0.45),
                        v * 0.72)
        new = lips * lipc + (1 - lips) * new
        gem = (_ell(GEM) * (v > 0.3) * np.clip((s - 0.3) / 0.1, 0, 1)
               * (h > 160) * (h < 230))[..., None]
        gemc = _hsv2rgb(np, np.full_like(h, 242), np.full_like(s, 0.5),
                        np.minimum(1, 0.28 + 0.3 * v))   # stock diamond, darker (785i)
        new = gem * gemc + (1 - gem) * new
        # BUILD 785l: the forehead diamond itself -- half of it sits on the
        # face patch's mirror edge (atlas x < 28, y 850..915), light blue in
        # Nino's art. Stock: hue 238, s .4, v ~.65.
        diam = ((xx < 28 * w / 1024) & (yy > 850 * hgt / 1024)
                & (yy < 915 * hgt / 1024) & (h > 195) & (h < 232)
                & (v > 0.3)).astype(np.float32) * np.clip((s - 0.3) / 0.08, 0, 1)
        diam = diam[..., None]
        diamc = _hsv2rgb(np, np.full_like(h, 238), np.full_like(s, 0.42),
                         np.clip(0.42 + 0.27 * v, 0, 1))
        new = diam * diamc + (1 - diam) * new
        new = purple_lashes(np, rgb, new)
        if os.environ.get(LEGS_ENV, '1').strip().lower() not in (
                '0', 'off', 'false', 'no'):
            new = match_joints(np, new)
        # prefilter before the eye opening goes back in, so the iris and
        # whites stay Nino's exact pixels (785)
        new = clean_clothes_edges(np, new, gutter, crease | gold, seam)
        new = clothes_hem(np, briefs_tone(np, new, gold), gutter | halo, gold)
        new = true_sides(np, new, gold, orig)
        new = painted_hem(np, new, orig, gold)
        new = prefilter(np, new)
        keep = _opening(np, w, hgt)[..., None]
        new = keep * rgb + (1 - keep) * new
    out = px.copy()
    rgb8 = np.clip(np.rint(new * 255), 0, 255).astype(np.uint8)
    if min(w, hgt) >= 256:
        rgb8 = palette255(np, rgb8)
    out[..., order] = rgb8
    head = len(data) - len(t['pixels'])
    return data[:head] + out.tobytes()
