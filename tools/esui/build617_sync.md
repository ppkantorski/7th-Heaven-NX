# Build 617: Great Glacier left-edge band (for real) and the 16:9 FX margin

## What happened in 616 (ChatGPT session)

The 616 "edge" fix added 240 copied snow/wind tiles per Glacier field on two
**new** pages. Those pages were depth-2 pages placed in slots outside the
additive FX band (15..23), so the console drew the snow art **opaque**: the
dark blue textured blocks in `blizzard_regression.mp4`. It also rested on a
wrong premise (a 566-unit "pick window" as the cause). The pass was withdrawn
in the Claude copy before the session died; `ff7nx_parallaxwide.py` in the
Claude copy is byte-identical to the pre-616 version (the user folder still
has the 616 version and must be overwritten). The other 616 changes (snow
window lights at a steady 61, day/night list additions) are kept; their tests
pass (test_snow_lights, test_daynight 83/83, test_time_hoist).

## 1. The Great Glacier band - actual cause

Read off the port's recompiled `field_update_background_positions`
(ARM +0x9F9114 and +0x9FA92C, disassembled with capstone):

    bg4_pos_x %= bg4_width*16             sdiv/msub  -> truncating
    bg.x = ((bg4_pos_x/16 + speed*dx/256) % bg4_width) + 320 - offset.x - shake

C's `%` truncates, so the layer position spans **(-W, +W)**, two periods.
FFNx uses `remainder()` (+-W/2) and keeps the 352 header for the position.
`ff7nx_trnad4` writes 704 into the header, so on the port bg.x sweeps
(-544, +864). With BGSCR's -320 x speed each cycle spends its last ~37 units
at bg.x < -506, where the stored grid x -176..496 leaves the left 16:9 margin
uncovered after the single wrap: the strip that grows in from the left once
per cycle (every 36 frames in the video) and vanishes at the wrap.

**Fix (`ff7nx_trnad4.apply_edge_to_flevel`, last background pass):** the five
highest columns (x 368..496, 80 records) are rewritten at x-704. Same residue
of the 704 wrap, 352-periodic art: identical pixels, no added tile, page, UV,
palette, header or layer change. Executed against the layer-4 shift + cull
(459/107/213): zero gap and zero doubled tile for bg.x in [-666, 1018],
containing the whole truncating range with >=120 units each side (before:
[-506, 1178]). Verified on all 20 fields (hyou1..hyou13_1, move_d/f/i/r/s/u):
only layer-4 dst_x bytes change, idempotent, refuses anything but the exact
22x16 grid. `SEVENTH_NX_NO_GLACIER_EDGE=1` disables. Vertical axis not
touched (not reported; its constants are less certain).

## 2. sninn_2 FX layer (and the same defect elsewhere)

Cosmos authored 60 additive layer-2 tiles at x -224..-176 on page 15. Their
cells hold either nothing or unrelated 1997 art (the misaligned blocks in the
video); only the Cosmos DDS has the real margin light. The build-161/581
repair (`ff7nx_fxart`, the sinbil_1 / ghotel family) skipped them because
(a) 29 cells were not blank, (b) palrange had re-seated Cosmos's off-table
palette 6 onto scenery palette 0, which cannot hold the light. In las0_2,
las4_0, nivl_b2, nivl_b22 and sininb34 the margin FX pages stayed paletted
because fxpages' budget ran out, and fxart only fills pages with an animated
palette.

**Withdrawn from the first 617 hand-off:** the HD-clone pass in
`ff7nx_fxsplit` (it put 768px margins beside 256px 4:3 art). `ff7nx_fxsplit.py`
is back to its original bytes and build.py no longer calls it.

**Fix: `ff7nx_fxart.apply_margin_617`**, called from fxart's own flevel loop
(last art pass, after the repack). Same shape as sinbil_1: bytes of existing
cells on the existing 256px paletted page.
- Only cells referenced by nothing but additive FX records wholly outside 4:3.
- Rewritten only if blank and Cosmos visibly lights it (block error >= 6), or,
  for a palette static in both the script and Cosmos, if it draws far from
  Cosmos (>= 24). Art under an animated palette is never overwritten
  (jail2's flicker lights would have been frozen otherwise).
- Donor = the DDS FFNx loads (record palette, else 0), premultiplied, the
  same additive encoding as build 139 / fxsplit / fxrequant.
- Palette: the record's own if it holds the cell within 10/255; else the
  nearest 4:3 FX record's palette on the same page and param/state (an
  animated one only if the script scales it as a whole block); a blank cell
  in bright art may take the best candidate up to 16/255 if that removes
  3/4 of the gap. Animated records are never moved off their palette.
- sinbil_1 and ghotel (hardware-proven) are untouched.
  `SEVENTH_NX_NO_FX_ART_617=1` disables it.

Because sninn_2's ray margin now shares the rays' palette 3, the existing
fxrequant pass (ghotel's) rebuilds palette 3 from Cosmos and the 4:3 rays and
margin become one picture: the dark notch at (-160,-120) and the seam go too.
Same for mtcrl_1 palette 6.

Archive-wide (all 711 fields), changed: sninn_2 46 cells, mtcrl_1 44,
mtcrl_8 24, las0_2 440, las2_2 7, las4_0 480, nivl_b2 262, nivl_b22 262,
sininb34 322, ujunon4 72, ujunon5 72. Every one: no 4:3 tile changes, margin
error vs Cosmos drops (e.g. sninn_2 83 -> 3.7, las4_0 23.5 -> 1.9,
nivl_b2 59.7 -> 3.5). Not changed: cosmo2 (all-animated palettes), las4_4 /
las4_42 (1 cell each, unrepresentable), jail1/jail2/blin70_1 (animated).

Mixed resolution note: where fxpages DID convert a field's 4:3 FX pages but
not its margin pages (budget), the margin is painted at 256 beside 768 art of
the same effect. Soft effects (fog/fire) hide this; it is still better than
the missing light it replaces.

## 3. After the build-382 hardware test

**sninn_2 upper-left patch and the "pixelated squares".** One cause. On a
paletted page only the palettes the page's own 4:3 records use render
correctly on the console (FINDINGS-74). The margin tiles that palrange had
re-seated onto palette 0 drew as nothing (the missing patch) or as a
patchwork of wrong 256px cells (the squares). Rule added to
`apply_margin_617`: candidate palettes are restricted to the page's 4:3 FX
palettes; a record on any other palette counts as missing; if no page palette
holds the cell but fxrequant rebuilds the nearest one from Cosmos, the record
moves onto it (verified with `ff7nx_fxrequant.plan_field`, otherwise rerun
without that move). Replay: every lit sninn_2 margin tile is now on palette
3/2/5; the 4 left on palette 0 have empty Cosmos art. Nothing goes HD and the
glow keeps pulsing.

**ghotel regression.** Caused by BUILD 608, not by 617; it became visible
because flevel was rebuilt. 608 keeps a page paletted only if Cosmos ships
state frames for (field, page, palette). ghotel page 16 draws the fog through
palette 16, which Cosmos only animates on page 15, so 608 converted page 16
to static truecolor. fxrequant (BUILD 582) then no longer saw the fog on
both pages and the 4:3/strip seam came back. Fix (`ff7nx_fxpages.
field_live_palettes`, used by fxpages and fxsplit): a palette Cosmos animates
on ANY FX page of the field keeps every page it is drawn on paletted.
Replay: ghotel pages 15 and 16 are both d1 again and fxrequant takes palette 8
(348 cells, error 3.6 -> 1.5). This also restores las2_1 (palette 11) and
sinbil_1 (palette 8). `tests/test_build582.py` checks this on a built archive.

**mtcrl_1 was corrupt in build 382, and the 617 code did not do it.** Log:
`lost detail: mtcrl_1 ParseError` and `stack order: mtcrl_1 left as built`.
The stored LZS stream in flevel.lgp has exactly 16,384 zero bytes (one
Apple-silicon memory page) at stream offset 1,490,912. The field decodes 18 KB
short, so its section 9 runs past the data. The build's own encode cache held
the correct bytes for that field (`cache/_fieldlzs/c4751e89...`): it decodes
and parses, and it matches fxart's output exactly. So the texture passes
produced a good field and the bytes were lost in storage or memory after that.
A full scan of the 382 archive finds no other corrupt field. Guards in
build.py:
- `_encode_field_cached` now decodes every cache hit and compares it with the
  raw bytes. A mismatch is re-encoded and rewritten.
- `field_payload_integrity` runs before flevel is written and logs
  `! FIELD INTEGRITY` with the field names if any payload is inconsistent.
  That takes about 7 s for 711 fields.

## 4. fship_22 (and a check of every fship_2X)

Every background record of fship_2, _22, _23, _24 and _25 was compared with
Cosmos's art for its own 1997 page and cell (`_scratch/s617/fsverify.py`).
fship_2, _23, _24 and _25 are clean on layers 1, 2 and 3. fship_22 had two
real defects:

**Checkerboard sky (parallax, layer 3).** The dense repack laid fship_22's
five 256px sky pages (slots 6..10) out with their cells transposed and the
records' UVs swapped to match: 280 of 320 records sample cell (v, u); the 40
on the diagonal are unaffected. BUILD 174's `ff7nx_palettedart` then wrote
Cosmos's row-major raster into those pages, so every off-diagonal cell showed
another cell's clouds. State 1 has no 256px page and was clean, which is why
the sky blinked between smooth and checkered with the cloud cycle. Fix: the
page's own pixels say which layout it uses. `_atlas_layout` compares the page
with Cosmos in both layouts (fship_22's pre-pass page: 17.0 vs 1.8; the other
four: 1.1 vs 11-18) and requantises in that layout, refusing when neither
clearly matches. Verified on fship_22's real pre-pass state from your cache:
all 480 sky records match Cosmos.

**Low-quality squares (consoles, upper-left glass).** Every variant ends the
repack with 74 cells on one 256px page. In the other four those cells are
layer 1 on slot 0, and `ff7nx_staticpage` already promotes that page. In
fship_22 they are opaque layer-2 cells repacked onto slot 2, and staticpage
skipped the field. `_promote_layer2_page` rebuilds slot 2 as a 768px
truecolour page cell by cell. Each record's 1997 page and cell (the first
1322 records are the vanilla records, in order) gives the Cosmos cell; the
colour key is Cosmos's alpha. That is the same exact-art result fship_24 got
for these 74 cells. Same slot, same page count, and fship_22 now weighs
35.31 MiB like the other four.

**Not changed: the dark strip at the far left and right of fship_22.**
Cosmos's own fship_22 art is near-black in the outer ~12 units of both
margins: the margin cells average RGB 4-18, against 50-100 in fship_24. That
dark strip is in the mod's art, not something the port adds.

## 5. trnad_2 sky: grey blob and specks (and the same in 6 other fields)

The dense conversion keys every texel the 1997 page leaves at index 0, on
layer 3 as on the other layers. trnad_2's 1997 sky has index-0 pixels in one
cell (page 3, 64,0). Cosmos paints opaque cloud there, but we keyed 78 units,
plus three 1-2 unit specks. Layer 3 is the bottom of the picture, so those
holes show the clear colour: the grey blob in your video. Parallax-fill copies
on slot 1 carried the same holes.

Fix: a layer-3 arm in `ff7nx_lostdetail` (`_plan_backdrop`). A unit is
restored from the Cosmos image when:
- every texel of the unit is keyed on our page;
- every texel of Cosmos's unit is opaque;
- the cell is read only by opaque layer-3 records, with no FX record on it;
- the field has no layer 4.

Byte-identical parallax copies of a repaired cell get the same repair. The
lost-detail size limit does not apply here, because nothing can be behind the
backdrop. `SEVENTH_NX_NO_BACKDROP_KEY=1` disables it.

Archive-wide on the 382 archive:

| Field | Units restored |
|---|---|
| trnad_2 | 169 (0 keyed texels left) |
| blin66_2 | 446 |
| blin66_6 | 446 |
| md8_b2 | 261 |
| mtcrl_5 | 214 |
| sango2 | 11,950 (black holes in the valley backdrop) |
| rcktin7 | 20,921 |

las1_1 has a layer 4, so it is skipped.

**Not changed: the blue rim where trnad_2's sky meets the rocks.** It is in
Cosmos's art. The sky page is deep blue (33, 105, 212) and is lit by the
layer-2 additive haze, but the haze stops just short of the rock silhouette.
The rocks themselves have no partial alpha to blend. A Cosmos-only composite
with FFNx-style blending shows the same rim.

## 6. After the build-383 test

Confirmed good on hardware: fship_22, trnad_2's sky, ghotel. mtcrl_1 is
clean, and `tests/test_build582.py` now passes on the built archive.

**sninn_2's last patch.** The translucent teal squares at the left edge,
around field x -224..-176, y -40..-8, were four margin FX records still on
palette 0, which page 15's 4:3 records do not use. Their cells are blank, and
Cosmos draws nothing there either, so the 617 margin pass skipped them ("Cosmos
draws nothing here"). On hardware, a blank cell through a palette the page
cannot draw is not nothing: it is the FINDINGS-74 patchwork.
`apply_margin_617` now moves such a record onto its 4:3 neighbour's palette
(same param/state first) and makes sure the cell is blank, so it draws exactly
nothing. On the 383 archive this changes:

| Field | Records |
|---|---|
| sninn_2 | 4 (the patch) |
| nivl_b2 | 106 |
| nivl_b22 | 106 |
| icedun_2 | 5 |
| mtcrl_1 | 2 |
| spipe_1 | 2 |

The log line reports them as "blank cell(s) on a palette the page cannot
draw".

**Light that stops short of a silhouette (`ff7nx_fxseam`, new).** This is
trnad_2's blue rim on the rock tops. The sky's light comes from a layer-2
additive haze masked to the rocks. Cosmos's mask fades to zero over the last
2-4 native pixels, but the rock is cut hard, so a ring of raw sky shows.

The pass carries the light already present just outside that band inward, ring
by ring, up to the silhouette. It changes a texel only when all of these hold:
- the texel is on a truecolor page and only increases;
- it belongs to an exclusive static additive layer-2 cell;
- it lies within 3 native pixels of an opaque layer-2 cut-out and outside it;
- the carried light is at least 16/255 and at least 24/255 above what is there.

It touches 103 fields on the 383 archive, largest white1, sininb36, sinin2_1,
mrkt2, mrkt1, games, trnad_2 and mds5_1. Before/after renders of trnad_2,
white1, sininb36, mrkt2 and games show the ring or dark edge gone and nothing
else changed. `SEVENTH_NX_NO_FX_SEAM=1` disables it.

mds5_1's screen: I found where the 1997 animated screen art is drawn but
Cosmos is transparent, but at the reported screen no static tile sits behind
it, so keying it would open holes. That arm (`plan_anim_clear`) ships OFF
(`SEVENTH_NX_ANIM_CLEAR=1` enables it) until I have a hardware picture of what
looks wrong there.

## 7. fship_22's black right edge: the camera, not the art

Cosmos rendered fship_22 exactly one 16:9 window wide. Its lit art spans
x -213.5..213.5 (427 units, window 426.67); the last ~10 units of every
margin tile are opaque black. The other four variants are lit to +/-224.
FFNx pins this camera, so it never shows the black. Ours did not:

- `ff7nx_camfit.art_run` measured the art edge per tile from the first
  live column to the last.
- Four margin tiles have one stray lit native pixel in their outermost
  column, beyond the black filler.
- So the run read -224..224, the written camera range stayed -170..170
  (+/-10 units of travel), and the camera drifted right into the black.

Fix: a margin tile's live span is now the run of columns contiguous with
the picture (`_inner_run`). A speck separated by filler is not the edge.
fship_22 now measures -215..214, and `fit` pins its camera at -1: window
-214.3..212.3, inside the art.

Guard: if the stricter reading would make a field shorter than the window
(which would take it out of the fit and give MORE travel), the old reading
is kept. Only tightening is possible.

On the 383 archive, 40 fields read a narrower run:
- In about 25 of them, the camera clamp tightens by 2-10 units where
  stray specks sat beyond black filler (e.g. datiao_8, goson, sinin2_1,
  mds7plr1).
- The rest are shorter than the window either way and unchanged.
- I considered borrowing the missing strip from fship_24 (same records)
  and withdrew it: layer 2 of the two fields differs too much for a colour
  map (fit error 40+).

## 8. mds5_1 after the fxseam build (user report, pre-rebuild)

**The glowing grate's four bars looked ragged.** That was fxseam. The unlit
band above each bar is not a rim here: it is the bars' own dark thickness in
Cosmos's art, and lighting it thinned the bars down to their ragged cut.

I measured three generic rules for telling this apart from trnad_2's rocks and
games' lamp posts, where the same fill is right:
- layer 1 under the band;
- a fade versus a hard cut;
- band colour versus object colour.

None of them separates the cases. So mds5_1 is named in `SKIP_FIELDS`, and its
grate returns to Cosmos's picture (straight, thick bars). games (2037 texels)
and trnad_2 (1852) are unchanged.

**The big screen's dirty dark edge.** The screen's animation states keep their
1997 pixels at 3x, and the 1997 screen outline laps past the opening Cosmos cut
into the HD bezel. `plan_anim_clear` is now ON. It keys an animated layer-2
texel only when all of these hold:
- every Cosmos image of that page, in every palette and state, is transparent
  there;
- a static tile drawn behind covers it (here the bezel, z 60000 behind the
  screen's 10000);
- it is within 3 native pixels of the mod's own outline in that cell;
- the cell's own art is present, and at most 35% of its drawn texels are
  trimmed.

Nothing is painted. The screen's edge now follows the bezel. Across the
archive this is a small edge trim in ~290 fields; itown2's windmill blade tips
lose their dark specks the same way. `SEVENTH_NX_NO_ANIM_CLEAR=1` disables it.

**Bug found on the way.** Byte 29 of a tile record is a separate flag.
`use_fx` is byte 28 alone. fxseam and the new arms now read it that way;
before, 64 opaque layer-2 records in the checked fields were misread as FX.
One of them was mds5_1's bezel, which is why the screen arm first saw nothing
behind the screen.

## 9. las4_0's rising steam: an effect left half-converted

The steam is one additive effect with four animation states (param 1,
states 1/2/4/8) spread over seven FX pages:
- pages 15-18 were converted to 768 truecolor (complete Cosmos pages);
- pages 19-21 stayed 256px paletted. They hold the effect's bottom rows
  inside 4:3 (page 19) and its whole 16:9 margin in every state (pages 19,
  20, 21).

On hardware that gave dirty squares along the bottom (Cosmos art through
one 256-colour palette), and a right margin that showed a still frame in
some states and nothing or a darker patch in others.

**Why pages 19-21 stayed paletted.** `ff7nx_fxpages` runs BEFORE the dense
repack, and budgets against a projection that reserves 3 + 2 truecolor
pages (~17 MiB) for scenery the field does not have yet. las4_0 finished at
21.8 MiB of a 35 MiB cap, so the reserve went unused. Nothing revisited the
vetoed FX pages.

**Why the 617 work missed it.** The margin pass took "stayed paletted"
as final and painted Cosmos art into those 256px pages (las4_0 was in its
list). My check compared offline renders, and offline the margin animates
correctly. The offline renderer does not model how the console draws
paletted additive pages. That is the same blind spot as sninn_2's palette-0
tiles, so "matches offline" was not enough for a paletted FX page.

**Fix: `ff7nx_fxpages.complete_mixed_effects`.** It runs in build.py right
after the static-page promotion, once the page decision is final. It picks
the d1 FX pages whose records share an animation group (param) with an FX
page that was already converted. It converts them through the same
`upgrade_section9` and every one of its vetoes (animated palette, base use,
blend mode, missing art), against the REAL remaining budget
(FIELD_MB_CAP minus the field's actual pages).

las4_0: pages 19, 20 and 21 converted, 21.8 -> 31.0 MiB. All four states now
match Cosmos in the margin and along the bottom.

On the 384 archive it converts 20 more fields:

| Pages converted | Fields |
|---|---|
| All leftover pages | hyou10, rootmap, ujunon3 |
| Some leftover pages (budget ran out) | clsin2_1, corel3, del3, gaia_1/2/31, gaiin_4, las0_2/3, nivl_b2/b22, sininb34, ujunon2/4/5 |

All of them stay at or under 35.0 MiB. About 40 other mixed fields are
correctly refused (animated palettes, art, base use).
`SEVENTH_NX_NO_FX_COMPLETE=1` disables it.

## 10. Latest hardware report (after the fxseam build): las4_0 right edge, fship_22, mds5_1, mktpb

### las4_0: the right-edge steam was on a page the console cannot draw it from
The effect completion (section 9) converted pages 19-21 correctly -- the
right-margin cells hold the steam. The cause was elsewhere. `field_bg_pagecap`
counts the raw texture id (offset 32) and splits a page past its cap onto a
byte copy. las4_0's 1680 additive tiles all name paletted page 0 as texture
id, although they draw through their fx pages (see `effective_counts`). So
224 of them -- the tail of the list, i.e. the right margin -- were moved to
a copy on slot 1. That copy is named only by 16:9 margin tiles, so none of
its palettes are live on hardware (FINDINGS-74) and every tile on it drew
nothing. The one rectangle that flickered is exactly the 16 state-1 tiles
(4 rows x 4 columns, upper right) still on page 0; measured in the video,
the bright block is the top 4 rows, every 4th state.

Fix: `cap_section9` leaves tiles that draw through a present fx page out of
the texture-id split (`FX_TEXID_EXEMPT`, env
`SEVENTH_NX_PAGECAP_SPLIT_FX_TEXID=1` restores the old split). Fields that
change: las4_0, ancnt2, las0_2, nivl_b2, nivl_b22, sininb34, ujunon4,
ujunon5 (margin tiles back on page 0, one page fewer each), md_e1 and sky
(4:3 tiles back on page 0, as Cosmos has them). Cosmos puts every one of
these tiles on page 0.

### fship_22: the art is aligned; the strip is truly black; the camera is fixed
Gradient cross-correlation of fship_22 against fship_24 peaks at dx = dy = 0:
no misalignment. fship_22 is the one variant with a 320-unit camera range
(fixed camera at -1); the others have 340 (+-10 travel). Cosmos painted only
what that fixed camera sees, and its DDS is opaque black for the last 10
units per side. `ff7nx_siblingmargin` (new) paints that strip from fship_24
(same room, same records, pixel-aligned), graded to the night palette per
tile row (quadratic colour fit on the 48 units inside the strip; mean fit
error 10.8/255, the icy pillar at the top worst). `ff7nx_ws` then gives
fship_22 fship_24's horizontal camera range, and camfit re-measures the art
(now -224..224) -- if a row were not completed it would tighten the camera
back. `SEVENTH_NX_NO_SIBLING_MARGIN=1` disables both halves.

### mds5_1 screen: slivers between picture and bezel
The animated picture (param 1) is 1997 art at 3x; after the section-8 trim
its outline still showed the static layer in thin slivers -- the bezel's
white highlight above its lower edge and a dark notch/tip on its left edge.
`ff7nx_fxseam.plan_anim_close`: per state, the picture mask is closed
(1 native px), grown 1 native px over the sliver and lightly smoothed
(sigma 0.7), only within 1 native px of the outline, only where a static
tile behind covers and the cell is exclusive; grown texels take the
picture's interior colour, and the old darkened outline ring next to them
is repainted. mds5_1 param 1 only. `SEVENTH_NX_NO_ANIM_CLOSE=1` disables.

### mktpb TV: stepped-off corners
The TV programmes are additive on a paletted page Cosmos swaps by hash, so
it stays 256px. The 1997 picture rectangle has its top-right and
bottom-left corners stepped off. `ff7nx_tvnotch` (new) gives those texels --
dark and connected to the rectangle's border, never interior detail -- the
palette index of the nearest lit picture texel. 82 texels over 4 states.
`SEVENTH_NX_NO_TV_NOTCH=1` disables.

### Follow-up (after the section-10 build)
- fship_22 strip looked patchy (rectangles): each tile row had its own colour
  fit. Now one colour map per side, fitted at every tile row from the rows
  around it (layers pooled), coefficients smoothed along y ([1,2,1] x2) and
  interpolated per texel row. Tiles fship_24 paints from a different layer
  (layer 2 at x 192/208 y 16, layer 1 at x -224 y 16) now take the
  sibling's static picture at that spot instead of staying black.
- mds5_1 grate: the glow cut around each bar had a 1-2 texel rim 2-3x
  brighter than the glow behind it (186-210 vs ~90-150) -- the glowing seam
  on the bar tops. `ff7nx_fxseam.plan_rim_despike`: rim texels within 3
  texels of the cut are lowered to 0.6/0.8/0.95 of the glow 4-7 texels
  behind them. Only decreases, exclusive static additive cells, mds5_1 box
  only. `SEVENTH_NX_NO_RIM_DESPIKE=1` disables. Log: `RIM DE-SPIKE (BUILD 617)`.

- Second follow-up: the fship_22 join still showed a straight vertical line
  where Cosmos's own art meets the completed strip (x = +-212). A 5-unit
  cross-fade (FEATHER) now blends own art into the graded sibling art just
  inside the strip. The grate rim pass now shapes 2 native px (6 texels)
  next to each cut (taper 0.55..1.0 of the glow 7-10 texels back, lowering
  only): the bright line plus the lip one texel above it are gone. Setting
  those texels outright (raising the dips too) was tried and rejected --
  it hazed the glow and left specks along the ragged cut.

- Third follow-up (grate): the bars were dark slots in the glow with no
  top surface. `ff7nx_fxseam.plan_grate_faces` finds the four bars (dark
  static slot under a glow hole, followed along its fitted axis through the
  hole), fits each bar's top edge as a straight line, and paints a flat
  wood-brown top face 7 texels (~2.3 units) deep above it on the visible
  static tile: lighter front edge, slightly darker toward the back, a 45°
  bevel at the free left end, anti-aliased back edge, the glow keyed over
  the face. The rim pass then fades the glow into the new edge. 4 bars,
  2116 texels. `SEVENTH_NX_NO_GRATE_FACES=1` disables. Log:
  `GRATE TOP FACES (BUILD 617)`.

- Fourth follow-up (grate): the painted top faces looked muddy on review, so
  plan_grate_faces is now OFF (opt-in SEVENTH_NX_GRATE_FACES=1). Instead,
  `ff7nx_texpatch` (new) applies hand-painted PNGs: texture_edits/<name>/
  holds manifest.json (field + box), base.png (opaque static layers),
  glow.png (static additive layer); every pixel whose 565 value differs from
  the field at build time is written into the one exclusive tile drawing it.
  Runs after fxseam, before stack order. texture_edits/ is hashed into the
  flevel fingerprints, so repainting a PNG rebuilds flevel.
  Exported: texture_edits/mds5_1_grate (box -160,-16,-64,80; 288x288 px);
  whole base is paintable, glow only where glow tiles exist.
  Off: SEVENTH_NX_NO_TEXPATCH=1. Log: `TEXTURE EDITS (BUILD 617)`.

- Fifth follow-up (grate): hand-painting withdrawn at the user's request
  (texture_edits/ moved to _scratch/s617/retired; ff7nx_texpatch stays wired
  but does nothing without a folder). The actual complaint was the dark
  fringes: the Cosmos glow has one-to-two-texel dark streaks running along
  the bars inside the light. plan_rim_despike now also applies a 5x5
  grey-scale closing to the lit glow (GLOW_CLOSE): valleys narrower than 5
  texels fill, the bar cuts (9+ texels) and the peaks stay, keyed texels
  stay keyed, nothing is lowered by it. 6945 texels in the mds5_1 box.

### mds5_1 grate glow (superseded by the follow-up above)
Ours renders identically to the Cosmos/FFNx composite: the additive glow
washes the grey-blue plates behind the bars (the "flat top" surfaces) to
bright teal, with a hard edge where the plate ends. A glow-occlusion edit
over those plates is prototyped (`_scratch/s617/grate_prev.png`); waiting
for the user's call because it changes the art.

## 11. Snowboard: day/night on the story run, and a mouth for Cloud

### Day/night (ff7nx_daynight, exefs/main)
One frame loop serves both snowboard games: x86 0x72381C (ARM +0xE4C9A0)
draws sky and mountains (0x72DAF0), runs the state functions (course
blocks queued), draws the models (0x735220), flushes the course lists and
in-world sprites (0x72D0C0, through 0x66E641), then the HUD (0x724DBF) and
the 2D lists (0x72D64E). Tint ON before the sky call (+0xE4CA74), OFF
before 0x72D0C0's own last call, 0x72D661 (+0xE568C4 -> +0xE929F0), which
draws the fade quad and the 2D lists -- the TIME / SPEED text is drawn and
cleared there (617a put OFF after 0x72D0C0 returned and the text came out
tinted). Ordinary build_ui_cave sites, as Fort Condor's. Driver mode 10 (snowboard_enter's own `mov w0,#10; bl
set_driver_mode` at +0xE4A120). The flip cave ticks the clock and decides
the colour into ARMED only when `[0xDD865C]` is 1 -- snowboard_enter writes
`mode == 8`: 1 for the story run (FF7_MODE_SNOWBOARD, course table
0x939CE0), 0 for the Wonder Square arcade (SNOWBOARD2, 0x949F00), which
arms white and never ticks. No outdoor bit (always outdoors). Every site,
callee and the mode word is re-checked at install. 614 of 768 words of the
day/night dead-space part. SEVENTH_NX_DAYNIGHT_SNOW=0 leaves it out.
tests/test_daynight.py: 87 pass (story brackets scene only, arcade stays
white and frozen, sites re-derived), also with DAYNIGHT_TEST_DEADSPACE=1.

### Mouth (ff7nx_snowmouth, snowboard-us.lgp)
Six vertices appended to face object 14 at x -3/0/+3, y -10/-7 on the
head's own surface, 2 units toward the camera (the BUILD 547 lift, >=1.5
checked, interior sampled); four raw-textured triangles using the eyes'
own CLUT/tpage, UVs in the free bottom-right of the 56x48 region
(u 31..55, v 166..175), outside the eye triangles' footprint. eyes.tex
gets a 4 x 0.6 unit warm line (centre y -8.3, midway eyes-chin) with softer underside/corners in two new
palette entries (16-colour CLUT kept). Drawn in TEX space, so the port's
region squeeze (BUILD 548) is the same correction the eyes get. Object
14's blocks are appended at the end of the TMD and only its table entry
is repointed. All three files or none. SEVENTH_NX_SNOW_MOUTH=0 disables.

Eye symmetry: the stock face maps the right eye with three triangles
around an extra vertex (5,-23) and UVs a texel off the left's; with the
stretched texture the right eye came out a pinch smaller and lower (user
report). The left eye's two triangles are mirrored onto the right with the
same UVs (winding kept, vertex 4 unused).

### After the first hardware test (617a)

- Mouth not drawn: the patched TMDs shipped, but the mouth triangles wound
  opposite to the eyes (`++++----`) and the port culls back faces. They
  are now oriented to the eyes' winding (all eight `+`); a test checks it.
- TIME / SPEED text tinted: OFF moved into 0x72D0C0, before 0x72D661 (above).
- White band under the mountains: the frame's clear colour.
  0x722CCF (+0xE4BFE0) starts every frame with `0x722E0F(1.0, 1.0, 1.0, 0)`
  (r, g, b, a floats packed as the bgra clear colour through 0x66075C), so
  wherever the course leaves a gap the screen is white. New cave
  `snow_clear` on that call (+0xE4C098): if ARMED is not white it scales
  the three pushed floats (guest esp +4/+8/+12, translated one by one) by
  the armed tint, then makes the call. The pushes, `mov w20,#1.0` and the
  call are checked at install. The arcade and daytime clear stay white.
  tests/test_daynight.py: 90 pass (also with DAYNIGHT_TEST_DEADSPACE=1).

## 12. sninn_2 at night: the window glow's missing patch and 4:3 step

The lower-left window light is a static additive sheet (layer 2, mode 1,
param 0) on the paletted page 15, palette 2. The 1997 cells have a hole
stepped out of the bottom (a snow mound the light stopped at). The
Cosmos-painted 16:9 margin continues it with a different outline and a
different brightness, so the light steps at x = -160. By day, white glow
over white snow hides it. At night the snow is tinted and shows through as
a flat patch with rough edges (hardware report and video).

ff7nx_glowfill (after tvnotch) re-draws the region x -224..-48,
y -24..120 from Cosmos's own sheet for those cells. That sheet is smooth,
has no 4:3 step, and agrees with the current cells within 12/255. The pass
then fills the hole with the glow's peak brightness, as the user asked: from
each column's brightest texel down, blurred mostly across columns so it
forms one sheet, feathered in, and only ever brighter. The result is
quantised to palette 2. All 92 cells are exclusive to one static record.
The corner record on the script-animated palette 5 is moved to palette 2
(FINDINGS-74: a palette the page's 4:3 records use). The 4:3-edge step
drops from about 64 to about 5 (0..255). Re-running the pass writes
nothing. SEVENTH_NX_NO_GLOW_FILL=1 disables it. Without the provider it
falls back to the cells' own light, with the margin faded into the 4:3 side.

## 13. games (Wonder Square): the lamp heads

Each lamp post stands in a static additive halo (layer 2, mode 1, param 0)
on truecolor page 15. Cosmos cut the lamp's silhouette out of the halo, so
the two egg-shaped heads read as dark holes in the middle of their own
light. ff7nx_lampfill (after fxseam) lights the HEADS only, not the poles
or arms (user direction):

- A texel is "dark" if it is under 0.55 of the halo, where the halo is a
  grey closing 41 texels wide.
- The dark mask is opened with a 9-texel square, which removes the poles,
  arms and hub, then grown back 3 texels over each head's soft rim.
- A blob counts as a head only if it has 400-8000 texels, is wider than
  tall (x1.2), and sits in a warm halo that covers at least 60% of its
  ring. This excludes the WONDER sign's cyan glow and the dark pillar
  bracket beside the front-left lamp.
- Where two heads touch and swallow the pole, the pole's columns below the
  blob are carried up through it and left dark.
- Every head gets ONE colour: the brightest texel of any head's fringe,
  (214, 255, 156). The rim blends up to it over 3 texels, and texels only
  ever get brighter.
- 8 heads, 17843 texels, all in cells no other record reads. A second run
  writes nothing. SEVENTH_NX_NO_LAMP_FILL=1 disables it.

## 14. After the 617b hardware test

### Snowboard TIME / SPEED still tinted -> identify the HUD quads themselves
Moving the OFF point twice (after 0x72D0C0 in 617a, before 0x72D661 in 617b)
did not untint the HUD, because the HUD is not drawn at a call site that can
be bracketed. The gameplay state function (x86 0x723F60) builds TIME, SPEED
and their gauges (0x72D864 -> 0x72D6C8 text, 0x72F26C, 0x72F680) as screen
quads through a single helper, 0x72D880. That helper writes every vertex with
z = 0.0 and rhw = 1.0, and those quads are drawn by the sprite flush inside
0x72D0C0, alongside the in-world sprites.

The block cave therefore decides per draw. A draw is white when all of these
hold: driver mode 10; outside the sky call (the BSS_SNOW_SKY flag, +0x28,
which the snow_on wrapper sets for the length of that call); the 2D ortho
matrix (_34 == 0, _44 == 1.0); and a first vertex in x22, the helper's
32-byte TLVERTEX array, with z == 0.0 and rhw == 1.0 exactly. Projected
sprites, the course and the models all carry real depth, so they keep the
tint; nothing but the HUD layer changes. SEVENTH_NX_DAYNIGHT_SNOW_HUD=0 turns
the gate off. Dead space is about 720 of 768 words. test_daynight: 92 pass,
also with DAYNIGHT_TEST_DEADSPACE=1.

### Mouth floating in front of the face
Each mouth vertex was 2 units in front of the skin; on the slope between
the nose tip (0,-12,-15) and the chin, that sat further forward than the
nose. Each vertex is now the nearest whole unit at least 0.5 in front of
the head (clearance 0.96-1.33, about half of before). The interior clearance
check still passes on both TMDs.

### mds7st1 closes the game (no crash report), on entry and on loading a save
The static data is sound. Its tiles per page are at most 256, and the AKAO
table, the models and the script structure all check out. Echo-S's script
edits are cosmetic (windows, KAWAI removed, pickups). What is unique to the
field is its ambience. Cosmo Memory maps field_144 (mds7st1), field_145
(mds7st2), bat_3 and bat_392..403 to 1525.ogg, which is 192 kHz. It is the
only loop of the 105 above 96 kHz; 42 are 96 kHz and one is 88.2 kHz.
A loop's PCM ring is rate × channels × 6 bytes, so this one is 2,304,000
bytes from the fixed 32 MB g_WaveBufferAllocator pool. That is twice any
loop proven on hardware, on top of the station's music and three voice
lines. When the pool cannot serve a request, SoundBufferImpl calls exit(),
so no report is written. This is the same failure as BUILD 523's New Game
abort.

The fix is in ff7nx_ambient: by default, loops above 96 kHz are brought down
to 96 kHz, the rate 42 loops already play at on hardware. For 1525 that is an
exact 2:1 step and a 1,152,000-byte ring, the same size as every proven loop.
The output length is exact, and the loop tail matches the source (last 10 ms:
RMS 0.0361 against the source's 0.0359, correlation 0.9986). The 10 ms fade at
the wrap that the old 48 kHz conversion showed does not occur.
That affects 1525 only; every other loop still ships byte for byte.
SEVENTH_NX_AMBIENT_RATE=native restores the old behaviour, and =48k still
converts every loop above 48 kHz. Exposed: 2 fields and 13 battle scenes.

## 15. colne_6's laser cones, and a longer snowboard draw distance

### colne_6 (ff7nx_beams, after lampfill)
The room has three projector beams: static additive layer-2 sheets on the
256px paletted page 15, using palette 5 (yellow-green), 9 (magenta) and 10
(yellow-green). In the 1997 art they stop dead at x = ±160, and Cosmos's
page-15 DDS is empty.

For each cone:
- Both half-maximum edges are straight lines; the mean residual is 0.4–0.8
  texel. Where the two edges meet is the projector.
- Its level is the 75th percentile of the trusted part of the beam (away
  from the tip and the old cut), times the cone's own colour.

THE WHOLE CONE IS REPLACED, not just its edges. Shape: constant along the
beam, a Gaussian across it (FWHM = the measured width, wings let down to zero
by 1.7σ).

Why the steps showed: a 16-bit page is 5 bits per channel, so the yellow beam
(peak ~52/255) has only ~6 levels above black, and any fade is ~6 hard bands.
Review history: ordered dither = grain, along-beam ramp = contours, flat cone =
no fade, Gaussian at 5 bits = abrupt bands.

The fix is blend mode 3 (screen + ¼ × texture), stock FF7 on a depth-1 page
(the +18 alias FINDINGS-194 leaves untouched): each palette step adds ~2/255,
about 25 levels for the yellow fade.
- Page 15 stays the stock 256px paletted page (1× texels; cones box-filtered
  from 4×). Palettes 5/9/10 (beam-only) become 31-level ramps of each cone's
  colour.
- Each (position, cone) gets a record; a tile is mode 3 when its light fits 31
  quarter-levels: both yellow cones everywhere and the magenta wings. The
  magenta core (~110/255) stays mode 1, because two stacked mode-3 records
  there would break the 256-record cap.
- 617e: ONE LAYER, PREMIXED.
  - 617c (one record per cone) lost the upper beam wherever the lower
    beam's invisible faint wing had texels: a stepped black wedge on
    hardware.
  - 617d merged only the tiles where cones met, dropped the faint wings
    elsewhere and widened the wings to 2.5σ. Its tile edges disagreed
    (blocks), and the user wanted 617c's tight shape back.
  - Now every cone is summed into one light field first (the two yellows as
    one yellow), then cut into tiles, one record per position. Crossing beams
    add, and every tile carries all the light there is.
  - Yellow-only texels use the yellow ramp (palette 5, indices 1..31);
    magenta-only texels use the magenta ramp (palette 9). Mixed texels use the
    exact 5-bit colour qa·Y + qb·M, stored in the free entries of palette 10
    and in 32..255 of palettes 5 and 9.
  - One global floor of 1.5 quarter-levels (~3/255).
  - Shape back to 617c: wings to 1.7σ.
- Result: 168 → 207 records, one per position (34 mixed), 207 cells,
  reaching both screen edges.
- SEVENTH_NX_NO_BEAMS=1 disables it. SEVENTH_NX_BEAMS_MODE=1 uses the earlier
  3× truecolor (depth-2, mode 1) page instead.
- HARDWARE CHECK: confirm that the beams are smooth and as bright as the
  preview. Mode 3 on a depth-1 fx page is stock behaviour, but it is new for
  this field.

### Snowboard draw distance (ff7nx_snowdraw, ff7_en + exefs/main)
BUILD 542 lengthened only the Gold Saucer course. The Icicle Inn story run
(routes 0x939CE0: 7 routes and 601 blocks; spawns 0x9661B0) is now done too,
using the same rule and the same three budgets:
- block list: at most 14
- render queue: at most 96 of 128
- object pool: at most 20 of 32

Its per-block object counts come from a static count of calls to the story
allocator. On the Gold Saucer course, that counter reproduces the
hand-derived table on 635 of 636 blocks. Wherever the count is UNKNOWN on
the story course, it is taken as 6.

The far plane (16384 → 32768) is now on by default. BUILD 544 had turned it
off because of eye flicker, but the eye decal is now lifted off the face
(BUILD 547), and depth precision near the camera is set by the near plane.
The new limits are a 30000-unit reach and +8 blocks; the block-list cap of 12
ahead is what binds. Median lookahead goes from 5–6 to 12 blocks on every
grown route except story route 4, which stops at 8 because of the pool.
SEVENTH_NX_SNOW_FAR=0 restores the BUILD 544 limits and the stock plane, and
SEVENTH_NX_SNOW_DRAW_STORY=0 leaves the story course stock.
tests/test_snowdraw.py: 7 pass.

## Files

- build.py: Glacier edge pass, cache-hit verification, field integrity check
- ff7nx_trnad4.py
- ff7nx_fxart.py: margin pass with the page-palette rule
- ff7nx_fxpages.py and ff7nx_fxsplit.py: field-wide live palettes (ghotel)
- ff7nx_parallaxwide.py (616 revert)
- ff7nx_palettedart.py: sky atlas layout (fship_22 checkerboard)
- ff7nx_staticpage.py: fship_22 layer-2 page
- ff7nx_lostdetail.py: layer-3 backdrop holes (trnad_2 sky)
- ff7nx_fxseam.py (NEW): light carried up to the silhouette; animated-overlay edge trim
- ff7nx_camfit.py: art edge ignores specks beyond filler (fship_22 camera)
- ff7nx_fxpages.py: complete_mixed_effects (las4_0 steam) + build.py call
- field_bg_pagecap.py: fx-bound tiles left out of the texture-id split (las4_0)
- ff7nx_siblingmargin.py (NEW) + ff7nx_ws.py range hook: fship_22 strip and camera
- ff7nx_tvnotch.py (NEW): mktpb TV corners
- ff7nx_glowfill.py (NEW) + build.py call: sninn_2 window glow
- ff7nx_lampfill.py (NEW) + build.py call: games lamp heads
- ff7nx_beams.py (NEW) + build.py call: colne_6 laser cones
- ff7nx_snowdraw.py: story course + far plane default on
- ff7nx_daynight.py: snowboard OFF moved, snow_clear cave, snow_on wrapper + 2D gate in the block cave (617b)
- ff7nx_snowmouth.py: mouth winding; mouth on the skin (617b)
- ff7nx_ambient.py: loops above 96 kHz converted by default (mds7st1)
- ff7nx_fxseam.py: plan_anim_close (mds5_1 screen slivers)
- tests/test_build617.py

## Build

flevel.lgp changes, so archive reuse must be off for it once:

    SEVENTH_NX_REUSE_BATTLE=1 SEVENTH_NX_REUSE_MAGIC=1 SEVENTH_NX_REUSE_WORLD=1 SEVENTH_NX_REUSE_HIGH=1 SEVENTH_NX_REUSE_CHAR=1 SEVENTH_NX_REUSE_MENU=1 python3 7th_heaven_nx.py --cli

Log lines to look for:
- `Great Glacier snow/wind left edge (BUILD 617): 20 field(s), 1600 record(s)`
- `FX MARGIN ART (BUILD 617)`
- `PALETTED ART: 25 animated sky page(s) in 5 Highwind field(s) ... Cell-transposed atlas layout kept (BUILD 617): fship_22.`
- `STATIC RESOLUTION COMPLETION: 373 layer-1/2 cell(s) across 8 field(s)` (was 299 across 7)
- In the `animated FX requantised` line: `ghotel(pal 8`
- `BACKDROP KEY (BUILD 617): ... trnad_2:169 ...`
- `FX SEAM (BUILD 617): ... trnad_2:1852 ...`
- `FX MARGIN ART (BUILD 617)` now ends with `... blank cell(s) on a palette the page cannot draw moved onto a page palette)`

- `SIBLING MARGIN (BUILD 617): ... fship_22 from fship_24: 89555 texels, 62 cells`
- `widescreen: fship_22 camera range -161..159 -> -170..170 (sibling margin completed)`
- `GLOW FILL (BUILD 617): ... sninn_2:... in 92 cell(s) from Cosmos's sheet, 1 record(s) onto the static palette`
- `1 loop(s) above 96 kHz staged at 96 kHz (192000 Hz x1)` in the ambient lines
- `BEAMS (BUILD 617c): ... colne_6: 3 cones, 207 records (+39), texture id holds 207/256`
- `snowboard route story 0 lookahead median 6 -> 12 blocks` (and routes story 1..6) and `snowboard far plane 16384 -> 32768`
- `LAMP FILL (BUILD 617): lamp heads lit inside their own halo (games:8 head(s), ...)`
- `TV NOTCH (BUILD 617): 82 cut-corner texel(s) ... mktpb:82 in 4 state(s)`
- `ANIMATED OVERLAY EDGE CLOSE (BUILD 617): ... mds5_1:...`
- the page-split line should report fewer pages than the previous build

These lines must not appear:
- `lost detail ... mtcrl_1`
- `stack order: mtcrl_1 left as built`
- `FIELD INTEGRITY`
- `complementary columns` (that line was 616)

Open follow-ups: cosmo2 (off-table palette, all animated), blin70_1
(animated). corel3 and gaia_1 need a hardware check.

## 16. junbin21 chair cut-out (BUILD 618, ff7nx_cutout, after beams)
The chair-back in the middle of the office window is a near-rectangular block
in the Cosmos art: dark surround on its sides and top, plus an additive orange
rim (fx page 16) traced round the rectangle. Over the parallax sea it reads as
a smeared box. The user trimmed it by hand in a screenshot. That trim is
embedded as a 65×62 mask at the screenshot's 3 px per unit, which is the HD
page's own texel pitch.

Registration: matching against a render of the built field puts mask (0,0)
at field (-13, -91.67), i.e. origin (-39, -275) in thirds of a unit. Moving
it 1 px in any direction gives a worse match.

The pass keys out (sets to 0) every texel on a trimmed pixel in eight records:
- the four solid chair records (z 9850260 at (-16|0, -96|-80), pages 26/27)
- the four additive rim records on page 16

It cuts 576 texels. Every cell is used only by its own record. If a record
fails to match, the field is left untouched, and a second run refuses.
SEVENTH_NX_NO_CUTOUT=1 disables it. Tests: tests/test_build618.py.

## 17. Debug warp (BUILD 618, ff7nx_debugwarp) — OFF unless set
`SEVENTH_NX_DEBUG_WARP=ujunon1=cosmo2[,src=dst...]` makes entering `src` show
`dst`. It exists for fields that are hard to reach: cosmo2 has no gateway in
at all, and only md_e1 and blackbg6 MAPJUMP to it, in the ending.

How it works:
- The archive entry of `src` gets the finished data of `dst`. This is the
  last flevel pass, so `dst` carries every fix.
- Every gateway into `src` (for ujunon1: jumin, prisila, ujun_w and ujunon2)
  has its arrival point moved onto `dst`'s walkmesh. It uses the triangle
  nearest the middle; for cosmo2 that is (-698, -1331), triangle 215.

The world map and save files keep their own arrival point, so walk in from a
neighbouring field. `dst`'s own scripts still run.

The log prints `!!! DEBUG WARP ... ACTIVE`. Always rebuild flevel with the
variable unset afterwards; REUSE_FLEVEL would otherwise keep the warp.

### Snowboard draw distance: verified in the 10-01 build
- ff7_en lookahead tables equal the 617c plan on every grown route.
- Gold Saucer routes 1/3/4 average 11.5/10.3/11.6 blocks, up from about
  9.0/8.3/8.8 in the BUILD 544 build. That is about 15,400 units ahead on route 1.
  Routes 0 and 2 are 8–9-block start segments and are left alone.
- Story course: all 7 routes reach a median of 12 (route 4: 8), up from a
  stock 5–6.
- The far-plane word in main is 52A8E008 (32768).
- The 14-block list (12 ahead) is now the binding limit.

### Debug-warp test plan (lower Junon hub: ujunon1's four exits)
Source slots: ujunon2, prisila, ujun_w, jumin. Enter Junon from the world map
(ujunon1 stays real), then take each exit. Target fields have their own
scripts and exits; if one strands you, reload.
- Batch 1: ujunon2=cosmo2, prisila=fr_e, ujun_w=las0_2, jumin=kuro_1
  - cosmo2 and fr_e: palette index past the field's palette count (240 and
    141 tiles)
  - las0_2: dead margin palettes (462) and a mixed fx param (3)
  - kuro_1: the worst dead-palette margin (606 tiles)
- Batch 2: ancnt2 (502 dead-palette margin), blin70_1 (animated, open), corel3
  and gaia_1 (hardware check owed)
- Batch 3: las2_2 and junbin5 (4 mixed fx params each), nivl_b2 (390 margin),
  nvdun2 (375 margin)
- Batch 4: blin673b, mds5_2, hyou5_3 (fx texid margin-only), junonr1 (256
  margin)
- Batch 5: losin1, onna_52, onna_2, colne_2 (dark margin tiles)

## 18. colne_6 beams 617f — the cause, reproduced
The 617e hardware screenshot showed a dark triangular gap above the magenta
beam and a rectangular edge on the upper beam. I registered it exactly
against the field (crop = shot x 0..259, y 229..344; shot origin = field
(-213.33, -145)) and tested render models:
- Exact quarter for mode 3: mean error 5.03.
- Mode 3 as k>>2: 5.04.
- Mode 3 at ×1, ×0.5 or ×0.375: 11–41.
- **Every blended palette channel shown ONE 5-BIT LEVEL DARKER, clamped at 0
  (mode 3 = that, quartered): 1.84.** Per-tile channel ratios are about 1.00
  for M1, X1, X3 and Y3. Magenta's G and yellow's B (1 level each) read 0 on
  hardware.

That one offset produces both artefacts:
- Every beam loses its level-1 fringe, which leaves the gap between the
  beams.
- Where yellow is 1 level under mode 1 but 4 or more quarter-levels under
  mode 3, it vanishes only in the mode-1 tiles. That is the rectangle at
  the mode-3/mode-1 tile boundary, and 617c's wedge too.

Fix (on 617e's premixed layer): every lit palette channel is stored c+1
(0 stays 0), and the mode-3 cut is at 30 quarter-levels so c+1 ≤ 31. Under
the measured model the result matches the intended light (mean 0.61, p99
5.7, against 617e's 1.7 and 13.4), with no rectangle and no gap.
(The 617f all-mode-1 attempt is kept in _scratch/colne/ff7nx_beams.617f_allmode1.py,
never shipped.)

## 19. Tifa's snowboard face (BUILD 618, ff7nx_snowtifa)
for_gs.tmd only:
- face 264 on head 249: Tifa, the brown eye, left half of tifaeye.tex
- face 279 on head 265: the blue eye, right half

Every face vertex IS a head vertex. The decal is flush with the skin and
loses the port's depth test intermittently, so she blinks (Cloud's BUILD
547 finding). Both faces are lifted 2 units (z-2); the look is otherwise
unchanged.

Mouth on face 264, Cloud-style: a 6×3 patch at x ±3, y -9/-6, 0.5+ in front
of head 249, mapped to free tifaeye.tex u 53..68 × v 44..53, with two new
16-colour palette entries.

It runs after _snowboard_mouth in build.py. SEVENTH_NX_SNOW_TIFA=0 turns it
all off; SEVENTH_NX_SNOW_TIFA_MOUTH=0 keeps the lift without the mouth.

## 20. cosmo2 margins (BUILD 618, ff7nx_fxpalfix)
cosmo2's animated sky is layer 2, param 1, on paletted pages 15..18:
- state 1: additive glow, palettes 2/3 (white ramps)
- state 2: subtractive shade, palettes 4/5 (index 0 = 0x8000, the rest 0
  at file state)

The field script animates those palettes at runtime. Cosmos's chunk.9 adds
240 margin tiles for it on margin-only cells of pages 17/18, all on PALETTE
6, but the field has only 6 palettes (0..5). FFNx ignores the index because
it draws the DDS; the Switch applies it. That produced the wrong colours in
the margins and the step at the 4:3 edge. clamp_palettes missed these tiles
because it keys by base texid and source cell, not by FX page and UV.

Fix, for each such tile:
- Palette: the one used by centre tiles of the same mode/param/state; the
  nearest tile's palette wins ties within 2/255.
- Cell, for the state-2 group: centre cells are uniformly index 1, so the
  margins copy that.
- Cell, for the state-1 group: requantised from Cosmos's cosmo2_17/18_00
  art into that palette (mean error 2.5/255).

Result: 120 mask tiles and 120 art tiles, which now animate with the picture.
fr_e's out-of-range tiles sit on a truecolor page, so they are harmless and
skipped.

LIMIT: this layer stays 1x paletted. Palette animation cannot live on a
truecolor page.

## 21. md_e1 model height (BUILD 618, ff7nx_fieldshift)
The background moves up 3 units (every layer, 2135 tiles), so every model
sits 3 units lower on it. The camera range (section 7, +0x0C) ends at 312
and the tiles reach 320, leaving up to 8 units of spare. Anything that would
expose the edge is refused.
SEVENTH_NX_FIELD_SHIFT=md_e1=N overrides the amount; =0 turns it off.

## 22. bugin3 meteor — diagnosed (fixed in §24)
The planetarium's planets, stars and Meteor are layer-2 effects on 7
paletted 1x pages (15..21; every user is FX mode 1 on base texid 0). Cosmos
ships one HD sheet per page (_00); its per-palette animation frames are in
the inactive "AA REMOVED" folder (_05 and _08 have 20–30 frame hashes each).

Checked against the built cells:
- the _00 sheets match most palette groups (median 5/255);
- palette 4 is about 67/255 off on pages 19/20, and palette 5 cells are
  partly missing in the HD art;
- palettes 5 and 8 are animated.

A truecolor conversion would freeze or recolour them, so this is left for a
dedicated pass, for example per-palette truecolor clones with relocated
cells.

## 23. BUILD 618b — corrections after the 617f/618 hardware test

### colne_6 beams (617g)
On hardware 617f still showed tile-edge steps, so the +1 model was wrong.

Measured transfer: every margin texel of the 617f screenshot whose stored
value is known (about 60k samples, both modes) gives, per channel,
drawn = max(0, 8.67·c − 13.4), ×1 for mode 1 and ×¼ for mode 3. Rendering
617f through that reproduces the screenshot to ±1 on both sides of the edge.

Even with T inverted per mode, mode-1 tiles (8.7/255 steps) and mode-3
tiles (2.2/255 steps) round the same light differently, so they cannot meet
without a step. Fix: ONE blend mode for every tile, mode 3. Colours are T's
inverse; 0 is chosen when the nearest drawable value is 0.

Mode 3's ceiling is 0.25·T(31) = 64/255. The magenta core (fitted peak
about 110) and the overlaps roll off into it with a soft knee starting at
60% of the ceiling.

Options: SEVENTH_NX_BEAMS_FORCE_MODE=1 puts every tile on mode 1 (full
magenta, coarse bands); =0 goes back to per-tile modes.

### md_e1 (fieldshift rewritten as a camera pan)
618a moved the background, which the user did not want. Now the camera
block's pan_y (section 1, +0x22) is set to +3.

The engine adds it to the 3D world position and NOT to the background:
x86 0x6447B0 reads `movsx eax, word ptr [camdata+0x22]` into
world_pos_y (0xCFF56C), and FFNx's set_world_and_background_positions does
the same. The background is drawn at 232 − bg_pos and the world at
112 − bg_pos + pan, so +pan moves the models down. Vanilla pan is (0,0) in
all 711 fields.

SEVENTH_NX_FIELD_PAN=md_e1=N overrides the amount.

### cosmo2 (ff7nx_cosmosrestore)
The real cause of the low-quality margins is in the base layer. The 120
layer-1 margin tiles on HD page 27 held a low-detail fill (3×3-block std
0.76 against 5–8 in 4:3), 73/255 away from Cosmos's sheet. The sheet has
their art at the cells chunk.9 names (std 3.7); the repack relocated them.

Fix: each built margin tile is matched to its chunk.9 twin by
layer/x/y/z, and that cell is copied from the sheet (120 cells). The layer-2
effect is invisible in the screenshot: state 1 off, state 2 palettes
transparent.

Follow-up: the same restore may apply to other fields' margins. Audit
before widening FIELDS.

## 24. BUILD 618c — cosmo2 4:3 seam, bugin3 meteor

### cosmo2 residual seam (ff7nx_cosmosrestore)
After 618b a faint step remained at the 4:3 edge. Cause: the restored
margin cells were re-encoded with `rgba_to_565_buf`, while the 4:3 cells
come from the repack's own packed page (`PageArt.buf`; built page 26 is
byte-identical to it). The two packings differ by about 3/255 in red and
blue. Fix: copy the packed 565 straight from `provider.open(name)(page, 0)`
at the chunk.9 cell; the RGBA re-encode is kept only as a fallback.
Measured mean signed jump across the edge went from
[-1.8,-0.3,-1.3]/[2.7,1.1,2.1] to [0.1,-0.2,-0.1]/[0.4,0.7,0.2], which is
the size of an ordinary neighbouring-column step.

### bugin3 meteor (ff7nx_fxhdlift, new)
Meteor is the param-4 animated layer: 15 FX tiles, blend mode 1, on the
1x paletted pages 15..18, palette 4, so it drew as 16×16 blocks. The module
copies each cell from Cosmos's HD sheet (that page and palette) onto a new
depth-2 768 page in free slot 22. Slots 15..23 are drawn additively for
mode 1 (FINDINGS-194). Alpha is premultiplied and the page is encoded with
black_ok, so black adds nothing. Each tile's +34, +14 src2 and +42 UV are
re-pointed. Param/state are untouched, so the script still toggles it.
Existing pages are unchanged. A second pass refuses. The software render
(`_scratch/cos/meteor.png`) shows the HD meteor in place. FIELDS =
{'bugin3': (4,)}; SEVENTH_NX_NO_FX_HDLIFT=1 disables.

## 25. BUILD 618d — ancnt2 light overlay, colne_6 square

### ancnt2 (ff7nx_fxpcstatic, new)
The overlay is layer 2, all FX blend mode 1, on pages 15..21:
- static tiles (param 0) on palettes 5/6/7, plus Cosmos's widescreen strips;
- four light-shaft groups (param 1, states 1/2/4/8) on palettes 8..11.
  BGON switches them all on once, and MPPAL+LDPAL pulse their brightness.

All seven pages were still 256px paletted, which caused the dither holes,
the 1997 resolution, the overlay ending at the 4:3 edges and the stepped
missing patch.
- fxpages vetoed the pages. Cosmos ships 61 state frames for palettes 8..11,
  but only in `AA REMOVED`, which the mod does not install. The installed
  folder has one `ancnt2_NN_00` per page, and FFNx falls back to that for
  every palette, so PC shows one static HD picture.
- fxsplit needed one clone per page and the field has only 2 free band
  slots.
- Cosmos's strips name palette 12. The build re-seated them on 11, which
  is a pulsed palette, so the margins and the top patch drew only when that
  shaft group was bright.

The software render of the built field reproduces all of it.

Fix: every FX page becomes the Cosmos picture in place (depth 2, 768,
fxpages' premultiplied encoding). Slots, records, UVs, palettes and states
are unchanged. Runtime is 34.06 MB (cap 35) and raw is 12.0 MB (cap 13.4).
The check is all-or-nothing and runs after every page decision.

Cost: the shafts no longer pulse in brightness, the same as PC.
SEVENTH_NX_NO_FX_PCSTATIC=1 disables.

### colne_6 square (ff7nx_beams)
617g packed beam colours into palettes 5/9/10 first-fit in scan order. When
those palettes were full, it drew 53 of 207 tiles with the nearest existing
colour. Tile (0,0) on the bed, where yellow and magenta cross, lost its
yellow; that is the visible square.

Fix: colours are now packed once all tiles are known, with best-fit
decreasing. If that does not fit, the rarest colour is merged into a
neighbour one stored level away. A colour that has absorbed others is never
merged itself, so every texel stays within 1 level (about 2/255 drawn in
mode 3). colne_6 needs 474 merges, max 1 level, and no tile is aliased.
Shape, records (207) and cells are unchanged. The build log line reports
merges and aliasing.

## 26. BUILD 618e — md_e1 models only; fship_2 bridge camera held still

### md_e1 (ff7nx_fieldshift rewritten again)
618b's camera pan (pan_y +3) moved the whole field on the Switch, leaving a
black bar at the top.
- On the PC exe, pan_y goes only into the 3D world coordinate (0x6447B0 ->
  0xCFF56C).
- That coordinate is also the viewport origin handed to the renderer
  (0x640EDF -> 0x661B68 -> 0x67CCDE / engine_set_game_engine_world_coord).

618e leaves pan at (0,0) and tilts the camera down instead.
- The pitch is about 0.24°, solved on the int16/int32 values the engine
  reads.
- md_e1's camera sits at floor height, so the whole walkmesh projects onto
  one screen line. The tilt moves it 3.08 units down; x drift is under 0.1
  unit.
- The background, its scroll and the 2D world coordinate are untouched.
- Only md_e1 is changed. md0 has a byte-identical camera block and is not
  touched.
- SEVENTH_NX_FIELD_PAN=md_e1=N sets the amount; =0 disables.

### fship_2 / 22 / 23 / 24 / 25 (ff7nx_ws.hpin_overrides)
The written range -170..170 gave ±10 units of horizontal camera travel.
That is what FFNx's clip arithmetic gives for a 448-unit range, but the
user's PC comparison shows the camera still. The horizontal range is now
pinned to exactly 320 written units around its centre (-160..160), so the
16:9 window sits over the middle of the 448-unit art. Vertical travel is
unchanged. SEVENTH_NX_NO_HPIN=1 disables.

### Fields still worth checking (survey of the 10-01 build)
31 fields have 1× paletted FX pages that Cosmos animates only in AA REMOVED
(the ancnt2 cause). The largest are:
- bugin3, las2_1, md_e1, prisila, kuro_1, las2_3, hyou5_2, cosmo2
- junbin5, md8_3, woa_2, mtnvl3, icedun_2, woa_1, mogu_1

Do not touch ghotel, sinbil_1 and sninn_2: their fog/glow animation was
confirmed on hardware.

203 more fields keep 1× FX where Cosmos really animates or has no art. The
largest are:
- crater_2, trnad_1, psdun_4, ujunon2, uutai1, nivl_b1/_b12, las0_2
- whitein, mds7plr1, las2_2

Script: `_scratch/c6/survey.py`.

## 27. BUILD 618f — hyou5_2 blizzard (ff7nx_fxpcstatic composite page)

Hardware video: the blizzard on hyou5_2 looked grainy, and a strip up to
32 units wide flashed in at the left edge for single frames, sampled every
5-25 frames. That is the same symptom as the fixed Glacier band.

All 20 edge-fixed fields have the same layer-4 grid, already relocated
(columns -336..336), and the same BGSCR (-320/-80). Each draws it from TWO
truecolor pages (256 + 96 tiles, one palette each). hyou5_2 is the only
exception: its 256-tile page 18 stayed 256px paletted, with half the tiles
on palette 11 and half on 12.
- Cosmos ships one sheet per palette (`_18_11_<hash>`, `_18_12_<hash>` in
  LIMIT BREAK AA, one state each).
- The field script animates neither palette (it animates 2/3/7).
- fxpages refused the page because the two palettes load different sheets.
- fxsplit only splits pages with an animated palette.

The two palettes sample disjoint cells (32 + 32 of the 8×8 size-flag page).
fxpcstatic now builds one depth-2 768 page from them, taking each cell from
the sheet FFNx loads for its palette. The page stays in place, records are
byte-identical, and runtime is 28.25 MB. FIELDS = {'ancnt2': all FX pages,
'hyou5_2': (18,)}.

hyou5_2's pages 15..17 (layer 2, palettes 2/3/7) have 10 Cosmos states each
in the installed AA folder and stay paletted.

Render: before is speckled with misaligned rows; after has continuous wind
streaks (`_scratch/c6/h52_cmp.png`). The left-edge flash is attributed to the
paletted page, since every field with an all-truecolor blizzard is clean on
hardware. Confirm on hardware.

## 28. BUILD 618g — kuro_1 light, las2_1 / las2_3 effects, hyou5_2 status

### kuro_1 (ff7nx_trnad4: exact repeat with overlay records)
Hardware video: patches of the purple light pop in and out in different
regions instead of drifting smoothly.

Cause: layer 4 is the same authored 352×256 grid as the Glacier (11×8,
X0 -176, Y0 -128) plus two animated overlay records (param 63 at (-16,-16),
palettes 9/10).
- The overlay made trnad4's exact-repeat proof refuse it.
- ff7nx_parallaxwide then tiled it to 808 records over 44 columns, with the
  header still 352×256.
- Copies one and two periods apart fold onto each other through the
  352-unit wrap.

Fix:
- kuro_1 is added to trnad4. The overlay records are carried through the
  2×2 repeat: 360 records, header 704×512.
- The edge pass moves the five highest columns by -704, as on the Glacier.
- kuro_1's camera also travels vertically (range ±256), so the two highest
  rows are moved by -512. That is proved against the shipped vertical
  shift/cull (ptop4 264, pbottom4 16, picture -232..8) over bg.y -448..400.
- parallaxwide skips the field, since the header is now ≥427.
- The pipeline was checked on vanilla data: parallaxfill (+112 records),
  then trnad4 removes 112 and writes 360. The hyou3 result is
  byte-identical to before.

### las2_1 green glow / las2_3 pond (ff7nx_fxpcstatic)
- las2_1: same case as ancnt2. FX pages 15..19 convert in place to the
  installed `_NN_00` sheets (27.3 MB). The tile-state animation runs; the
  palette 7/11 pulse stops, as on PC.
- las2_3: page 15 (static palettes 10/13/15, one sheet each, disjoint cells)
  is composited. The pond animation is pages 16..18 (palettes 8/12/14, with
  43/23 Cosmos frames installed). It stays paletted: a truecolor page would
  freeze it.

### hyou5_2 left strip — still open
Video measurement: a strip of 4..32 units, full height, at the left edge,
appears for one 30 fps frame roughly once per 35-frame wrap cycle.
Differential check against hyou3 (fixed):
- The layer-4 records are byte-identical except palette and FX page.
- bg4 header and BGSCR (-320/-80) are the same; horizontal camera range is
  the same.
- The only other differences are vertical range (±128 vs ±120, so ±8 of
  camera travel) and slot numbers.
- Since 618f both layer-4 pages are truecolor, as on hyou3.

The shipped shift/cull model covers bg.x -666..1018 with no gap. The bg.x
range needed is -384..320. Also ruled out:
- one-frame lag between shift and cull;
- boundary-inclusivity variants;
- picture-edge offsets;
- the add_page_tile per-page list cap (0x1804 = 256 entries, no bounds
  check; never exceeded).

Needed from hardware: does hyou3 (or hyou5_1) show the same single-frame
flash?

### hyou5_2 strip — further checks (10-02, after the second report)
- The flashes repeat once per 704-unit wrap cycle (frames 10, 45, 80, 115,
  150 at 30 fps), with widths 4..32 units: one column, at the wrap.
- The built exefs/main has all five PARALLAX_PATCHES applied (layer-4 left
  459, half 213); pright4a/b = 107, ptop4 264, pbottom4 16.
- Registration of the hardware frame against layer 1 puts the screen at
  field x -213.3..213.3, so the camera is centred.
- None of these models produces a once-per-wrap single-column gap with the
  relocated grid: stateless or persistent (in-place) shift; shift/cull/
  position on previous vs current bg (16 combinations, lag ±10..30);
  boundary inclusivity; picture-edge offsets up to 120.
- Script: hyou5_2 has no scroll, shake or BG opcodes beyond the shared
  BGSCR; its extra opcodes are the ice-floe puzzle and its palette
  animation (2/3/7).
- Conclusion: the port's real per-frame layer-4 behaviour differs from the
  617 model, and the data is the same as hyou3's. A hardware comparison with
  hyou3 decides whether the 617 Glacier fix is incomplete everywhere or
  something is specific to hyou5_2.

### Reference fields trnad_4 and woa_2 (10-02)
- trnad_4 (confirmed good) is layer 3, exact repeat 704×512, columns
  -176..496 with no edge relocation. Its BGSCR x speed is +480 (positive),
  so bg.x sweeps the other half of the truncating range from the Glacier's
  -320. Camera travel is ±80 vertically, 0 horizontally.
- woa_2 is untouched by any pass: vanilla 352×256 grids on layers 3 and 4,
  header speed 0, several BGSCR. The 617 model predicts 75..132-unit gaps
  on both layers for every bg.x. If woa_2 shows no gap on hardware, the
  model (not just hyou5_2) is wrong.
- The port's code was checked against the model:
  - ARM +0x9F9000: per frame, bg3/bg4 pos += BGSCR speed (16-bit), then
    pos %= width*16 (sdiv/msub, truncating), stored as int16.
  - x86 layer4_pick 0x641358: vertical shift first, then horizontal shift
    (x <= bg-L or x >= bg+R -> x ± W by the bg-H test), then horizontal-only
    cull. All arithmetic is 16-bit. The port has L 459, H 213, R 107.
  - The model matches this arithmetic.

## 29. BUILD 618h — woa_2 / woa_3 white wind haze (ff7nx_trnad4)

Hardware (woa_3): the white haze flows right to left, and empty gaps keep
opening on its right side. The green FX, lightning and steam are fine.

Cause: the haze is layer 3, the same authored 11×8 grid (352×256) plus two
param-63 overlay records. It is a pinned layer (header speed 0) scrolled by
BGSCR at +640 x / +80 y.
- trnad4 never treated it: speed 0 failed its header test, and the overlay
  failed its proof.
- parallaxwide skips 320-range fields.
- So the 427-unit view ran past one 352 period on the incoming side.
- The 617 model predicts exactly this, which validates it on a field with
  no prior fix.

Fix:
- The header test accepts speed (0,0) as well as (256,256). The repeat
  maths does not involve the camera term.
- woa_2 / woa_3 are added as layer-3 overlay targets: exact repeat with
  360 records and header 704×512. Layer 4 (lightning) is untouched.
- No edge relocation: the speed is positive, as on trnad_4 (layer 3,
  confirmed good). The original columns cover bg.x 160..864 with no gap.

Other 352×256 layer-3 fields with BGSCR (sky, trnad_1, zcoal_1, zcoal_3)
fail the exact-grid proof (irregular grids / horizontal fill) and are
unchanged. trnad_2 passes but is hardware-confirmed as is and is left alone.

Note on hyou5_2: the gap is always on the side tiles flow in from (woa:
right, flow leftward; hyou5_2: left, flow rightward). The hyou3 comparison
is still pending.

### Glacier left strip: hardware says hyou3 has it too (10-02)
The 617 relocation is therefore incomplete on every Glacier field, not
something specific to hyou5_2. kuro_1 looks fine; it scrolls at -0.5
units/frame, so it wraps only every ~47 s.

Diagnostic for the next build (no default changes):
`SEVENTH_NX_GLACIER_EDGE_COLS=hyou5_2=7,hyou5_1=0` sets the number of
columns moved one period left per field.
- hyou3 stays at 5 (the current fix).
- hyou5_2 = 7 extends left coverage to bg.x -730. Each extra column gives
  32 more units on the incoming side; the full-range proof still passes.
- hyou5_1 = 0 is the 617-era layout, as a control; the model predicts the
  classic ~37-unit growing band.

Reading the result:
- hyou5_2 clean → the real bg.x range or picture extends further left than
  modelled; apply 7 to all fields.
- hyou5_2 still flashes like hyou3 → the flash is not a coverage shortfall
  (per-frame timing at the wrap). Next step: the port's draw order.

## 30. BUILD 618i — the Glacier "strip" was the debug warp

Frame analysis of the 10-02 videos (60 fps):
- The snow moves 20 units x and 5 units y every 60 fps frame (60/14 px at
  3 px/unit), so the 704 period wraps every 35.2 frames.
- In hyou3 the strip shows on two consecutive frames per wrap (frames 10-11
  and 46-47), about 7 then 30 units wide, then disappears.
- That is exactly the pre-617 layout: coverage fails below bg.x -507 and bg
  bottoms at -544, so the gap is about 6 units then 26 units on the last two
  frames before the wrap.

Cause: in the built archive the WARPED copies (ujunon2←hyou3,
prisila←hyou5_2, jumin←hyou5_1) carry layer-4 columns -176..496, which is
unrelocated. The real hyou3 / hyou5_2 entries are relocated (-336..336 /
-400..272). ff7nx_debugwarp ran BEFORE texpatch, stackorder, the Glacier
edge pass and the widescreen range bake, so probed fields missed all four.
Every Glacier probe since 617 showed the unfixed layout.

Fix: the debug warp now runs after the range bake, the last pass before the
archive is written. Warped source names are excluded from the camera-range
verify (their range is now the target's). A test pins the order.

Other probe results that may be affected:
- Glacier strip (hyou3, hyou5_2): yes.
- kuro_1: also missed its edge relocation; it still looked fine because it
  scrolls slowly.
- The other probed fixes (cosmo2, ancnt2, colne_6, bugin3, las2_1/3, woa_3)
  happen before the warp and were seen correctly.
- Camera ranges matched for the sources used (none has its own config
  entry).

## 31. BUILD 618j — crater_2 lifestream glow: blocks and ~19 fps

Hardware (10-02 video, walked in normally): the whole glow overlay draws as
flat, posterised 16×16 blocks and bands that flash with the mist animation.
The field runs at about 19 fps (165 distinct frames in 8.55 s of 60 fps
capture).

Measured on the built field:
- Layer 2 has 1656 FX records, all blend mode 1, on pages 15..21. All seven
  pages were still 256px paletted, and together they cover the whole picture.
- param 1 runs a 4-frame mist animation: states 1/2/4/8, 8 frames each,
  one palette per state.
- A second routine pulses the brightness EVERY frame: STPAL 3, then MPPAL
  by a variable walking 50..62, then seven LDPALs into palettes
  3/4/5/6/7/8/10.
- That is seven palette writes per frame into palettes sampled by seven
  paletted pages. ancnt2 and las2_1 have the same per-frame pulse and the
  same FX layout (FX texid 0, 768 truecolor base), and both run fine since
  their FX pages became truecolor.
- fxpages vetoed crater_2 because Cosmos ships the pulse in an INSTALLED
  folder (LIMIT BREAK AA):
  - Each palette has 12 brightness levels named by FFNx palette hash, plus
    the unpulsed first frame.
  - Each sheet holds only the cells that palette samples.
  - Every palette of a page uses the same 12 hashes, because LDPAL makes
    those palettes identical.
- Cells are disjoint between palettes (0 clashes).

Fix (ff7nx_fxpcstatic, `PULSE_FIELDS = {'crater_2'}`):
- Each FX page is composited at ONE pulse level. For each hash shared by all
  of the page's palettes, every cell comes from its owning palette's sheet.
- The level kept is the one whose premultiplied luminance is the median of
  the 12, about 81% of the unpulsed `_NN_00` art and the middle of what PC
  cycles through.
- All seven pages become depth-2 additive in place. Records, UVs, palettes
  and states are byte-identical.
- The mist animation still runs; the ±10% brightness pulse stops.
- Section 9 grows from 2.99 to 10.79 MB; the raw field is 10.84 MB against
  the 13.42 MB cap.
- Software render: the bands and blocks are gone.
- Test: `test_fxpcstatic_crater_2_pulse_frozen_at_median`.
- SEVENTH_NX_NO_FX_PCSTATIC=1 disables it, as before.

FPS: no paletted page samples the pulsed palettes any more, so the
per-frame LDPALs have nothing to rewrite. This matches the ancnt2/las2_1
configuration; hardware confirmation is pending.

## 32. BUILD 618k — whole-archive parallax audit; trnad_1, sky, zcoal_1/3, trnad_2/3, woa_1, crater_1

Reported on hardware (10-02):
- trnad_1: the green layer has black regions and seams, plus a gap at the top.
- sky: black on the left and repeated strips.
- zcoal_1: a black notch at the top left, and the mountains scroll with strips.
- zcoal_3: a black bar and a gap in the widescreen region.
- crater_1: the wall is low quality, its edges are ragged, it stops short of
  the left edge, and a copy of the wall hangs at the top.

New tools:
- **ff7nx_paraudit.py** models every layer 3/4 of an archive against the
  port's wrap as shipped:
  - x: shift 459/107/213; layer 4 then culls; layer 3 has no cull.
  - y: shift 264/16; layer 3 direction 120, layer 4 direction 16.
  - Reachable positions come from the header speed × camera range plus every
    BGSCR in the field's scripts, with C's truncating modulo.
  - Reference is vanilla, or Cosmos's chunk.9 when it ships one, which is
    what FFNx draws.
  - It flags any layer that covered the 4:3 picture at every reachable
    position but does not cover 16:9 in the built archive.
- **ff7nx_framesim.py** renders a 16:9 frame of the built background with
  the same rules. Gaps are painted magenta.
- Validation: the model reproduces every reported symptom, and gives 0 for
  the fields already fixed and confirmed (hyou*, move_*, kuro_1, trnad_4,
  woa_2, woa_3).

Audit of the build before 618k (all 96 layer-3/4 instances in 84 fields):
- Seven defects, all the same class: exact seamless grids, scrolled by BGSCR
  or the camera, that ff7nx_trnad4's hand-made list never included.
  - L3 352×256: sky, trnad_1, zcoal_1, zcoal_3, trnad_2.
  - L4 384×256: trnad_3.
  - L3 384×256: woa_1.
- FFNx needs no list here: it applies the 2x2 repeat to every layer 3/4
  (background.cpp, field_layer3_pick_tiles / field_layer4_pick_tiles).
- No other layer is worse than its FFNx reference. las1_1, mds5_4, md8_b2,
  fship_3 and ghotel are at the Cosmos chunk's own extent.

Fix 1 (ff7nx_trnad4):
- `AUTO_TARGETS` adds the seven fields; `GRID` gives the 384-wide period.
- For these fields, fill output outside the authored period is dropped. That
  includes parallaxfill's pinned-layer edge cells, which sample other cells.
- The edge relocation is planned per field (`plan_auto_edges`):
  - The smallest move of whole columns/rows by one period that gives zero
    gap and zero doubled tiles at every integer bg in the field's reach
    ±24.
  - Results: zcoal_1 and sky move 2 columns; zcoal_3 moves 2 columns and
    1 row; trnad_1, trnad_2, trnad_3 and woa_1 move nothing.
- The old Glacier/move_*/kuro_1 relocation is unchanged (EDGE_TARGETS
  excludes the auto set).
- The trnad_1 top gap was layer 3's 256 period. Under the shipped vertical
  wrap it leaves a periodic 8-unit band. The 512 period closes it, so the
  green layer now fills the band above the cave edge, as in FFNx.

Fix 2 (crater_1):
- parallaxfill copied the wall (L4, header speed 32) every 320 units. The
  copied ragged top edge is the strip at the top of the screen.
- Cosmos's chunk.9 already lays out L3/L4 for 16:9. The wall's exact reach
  (rows -139..160) is covered.
- crater_1 L3/L4 join NON_TILEABLE_OVERLAYS.
- New ff7nx_chunkparallax redraws L3/L4 from the chunk layout and the Cosmos
  HD sheets:
  - 201 cells on 4 depth-2 pages in the slots L3/L4 own.
  - The field is at 33.75 MB, under the 35 MB cap.
  - It refuses (field byte-identical) on any mismatch.
- Framesim at five camera extremes shows no gap and every page HD. Before
  the fix it reproduced the photographed left strip and top copy.

Tests (tests/test_build618.py): paraudit class detection, the seven auto
targets (0 uncovered, 0 doubled), existing targets unchanged, and crater_1.
Result: 55 passed, 6 skipped across 617+618.

Not changed:
- trnad_51: the two black frames with a striped dome before Cloud floats sit
  on the script's FADE to black. Models are still drawn over the fade, which
  is believed to be stock behaviour.
- zcoal_1: the trestle is the 5-state layer-4 animation. Its layout is
  identical to Cosmos's chunk (what FFNx draws), so its cut end and stepping
  are authored.

## 33. BUILD 618l — coal train layer 4, trnad_1 transition copy and top band

Hardware (10-03, after 618k): crater_1 is fine. The remaining reports were
zcoal_1, zcoal_3 and trnad_1. Every frame of each video was analysed, using
consecutive-frame differences and phase-correlation shifts.

**zcoal_1 / zcoal_3 — FFNx's h_offset on layer 4 (ff7nx_l4offset):**
- Cosmos's widescreen config gives exactly two fields an h_offset: zcoal_1
  (+56) and zcoal_3 (-54).
- FFNx adds the offset to the camera point and subtracts it from every
  layer-4 tile (background.cpp field_layer4_shift_tile_position). The port's
  range bake reproduces the camera; nothing reproduced the layer-4 half.
- Result before the fix:
  - zcoal_1: the trestle (L4, x -40..120) started in mid-air beside the
    locomotive. It belongs at -96..64: it starts under the locomotive and
    ends at the wagon.
  - zcoal_3: the rock wall (L4, x -32..160) stopped at the 4:3 edge. It
    belongs at 22..214, reaching the 16:9 edge.
- The pass moves the stored x by -h_offset. It is proved per field that the
  port's shift + cull of the new x equals FFNx's shift + offset + cull of
  the old x, at every bg.x in reach ±64.
- Runs inside _bake_widescreen_ranges, using the same config.

**trnad_1 transition copy (ff7nx_trnad1fx, the user chose "HD, no fade"):**
- Layer 2 param 63:
  - State 1 is the cave: 466 records on HD pages, widened by Cosmos.
  - State 2 is the copy: 840 FX records (add/sub) on paletted pages 15..18,
    4:3 width. ADPAL ramps palettes 1/2/5/8 every frame.
- FFNx follows the ramp with 26 hash frames; the port cannot.
- State 2 is now an opaque HD copy of the state-1 cave, recoloured by a
  per-channel quadratic fitted from cave → copy at full strength (R² 0.89/
  0.91/0.91). It covers the full 16:9 footprint. 466 records go on 2 new
  depth-2 pages (slots 1, 2); page 18 is dropped.
- The remaining paletted FX pages (15..17, the param-0 light shafts, MPPAL
  pulsed every frame) become depth-2 additive at each palette's median
  Cosmos frame. That removes the last per-frame palette rewrite of a
  paletted page. The capture showed the haze stepping at 30 fps for
  stretches; this is the crater_2 cost.
- Field total: 30.69 MB.

**trnad_1 top band (ff7nx_scrvclip):**
- The script parks the camera with SCR2DL y=24. With p = -y, that is p = -24.
- Camera range and art are both -136..136. The stop is flush in the
  224-unit view (needs -24) and 8 units past the art in the 240-unit view
  (needs -16): the letterbox-sized band. FFNx only clamps scripted cameras
  vertically for fields whose config opts in, so it shows the same band.
- The pass rewrites a constant SCR2D/SCR2DC/SCR2DL y target only when:
  - the stop is legal in the 224-unit view, and
  - it shows past the layer-1/2 art in the 240-unit view, and
  - the fix is ≤8 units.
- It skips variable targets, over-range reveals, art shorter than 240, and
  movie fields (MVCAM / movie composites, e.g. southmk2). The op length never
  changes.
- 96 stops in 30 fields are affected, including trnad_1 (24→16 twice).
- Runs after the range bake, before the debug warp.

**Not changed:**
- The haze's internal discontinuity (a pattern break near x≈112 and a row
  band) is in the 1997 art itself. Vanilla shows it at 1x and Cosmos
  reproduces it, so FFNx shows it too.
- The haze scrolls 30 units/frame on x and 5 on y (BGSCR 480/80), as
  authored.

Tests: test_l4offset_matches_ffnx_on_the_coal_train,
test_scrvclip_trnad_1_top_band_and_safety and
test_trnad1fx_transition_copy_hd_and_full_width.
Result: 62 passed, 6 skipped (617 + 618 + 581).

## 34. BUILD 618m — sea water, trnad_1 vanish, crater_1 edges/mound, coal-train shake

Hardware (10-03, after 618l). Every frame was analysed.

**sea — still water (ff7nx_animdetail):**
- The ripples are L2 param 1: 6 states × 128 records, each with its own
  cell and palette (5..10). BGON cycles them every 8 frames.
- In vanilla, about 3% of the water's pixels change strongly between frames.
- Cosmos ships one sheet whose six frames are byte-identical cells, so FFNx
  with Cosmos also shows still water.
- The build's frames differed only by quantisation noise (no pixel > 12/255),
  which is the faint one-frame flicker reported.
- Fix (detail transfer): HD_k = HD_base + bicubic(V_k − V_ref), where V_ref
  is the vanilla frame that best matches the downsampled HD art.
  - 768 cells on 3 depth-2 pages; page 14 dropped; 23.6 MB.
  - Moving pixels now 2.4–4.0%, vanilla-like.
- It refuses unless the HD frames are near-identical and the vanilla frames
  move.

**trnad_1 — the field must vanish (ff7nx_trnad1fx rewritten; 618l was wrong):**
- The copy (state 2) is shown for about 12 s.
- STPAL 1 → ADPAL (offset −1 → −32, every frame) → LDPAL 1/2 fades it to
  black, so only the haze shows and Tifa runs "on nothing". It fades back
  in at the end.
- 618l's opaque HD copy never faded.
- Now the copy is an additive layer on palette 1, matching vanilla's
  mechanism:
  - 466 records over the full 16:9 cave footprint on 2 paletted pages in the
    FX band (slots 18, 19).
  - Cell colour = (fitted cave recolour − haze mean), 16 px per tile.
  - Palette 1 (and 2, which the script loads from the same buffer) is a
    255-colour k-means palette of those colours. Palettes 1/2 are used only
    by the copy.
- Ramp simulated: full ghost → fades → gone (equal to haze) → back.
- Light-shaft pages 15..17 stay HD.

**crater_1 (ff7nx_chunkparallax now redraws L1..L4 from the chunk):**
- Rough dotted edges where the snowy rock (L2) crosses the wall. The L2
  pages came through field_bg_dense's vanilla-shape edge refinement, which
  left stair-stepped dark texels along the silhouette.
- All four layers are now cut straight from the Cosmos chunk layout and HD
  sheets: 1534 cells on 10 pages, the same 33.75 MB as before. The edge now
  matches Cosmos's own art.
- The "clipped piece" on the wall ridge (x ≈ -144, y ≈ -75) is a mound
  Cosmos painted with a vertical cut; vanilla has no mound there.
  ART_PATCHES makes the mound's texels above the ridge line (cols 48..96,
  rows 103→111 of sheet 8 cell (0,4)) transparent. The patch is skipped if
  the mound is not found where measured.

**zcoal_1 shake gap (ff7nx_shakepad):**
- SHAKE type 2, y amplitude 4. The L1/L2 art ends at y=128, exactly the
  bottom of the 240-unit view (vanilla's 224 view had 8 units of slack).
- The bottom L1/L2 row (92 records) is copied 16 units down onto a new
  depth-2 page holding byte copies of its cells. The source pages are full
  (256 bindings), so they cannot take the copies.
- FIELDS = zcoal_1, zcoal_2 (71 records).
- 50 other shaking fields have flush art; plan() lists them but they are
  not changed.

Tests: test_trnad1fx_transition_copy_fades_and_is_full_width,
test_animdetail_sea_water_moves_like_vanilla,
test_chunkparallax_crater_1_all_layers_and_mound_patch and
test_shakepad_coal_train_bottom_row.
Result: 65 passed, 6 skipped (617 + 618 + 581).

## 35. BUILD 618n — sea water quality, trnad_1 light watermark, coal-train lowering

Hardware (10-03): crater_1 looks great.

**sea — the water looked dirty and pixellated (ff7nx_animdetail reworked):**
- 618m upsampled each 16×16 difference on its own. That left cell-edge
  seams and carried the 1x palette dither of the vanilla frames into the HD
  water.
- Now the transfer works on whole pictures:
  - one picture per state and ONE global reference frame;
  - the difference is gaussian-blurred (σ 0.8 texel) at 1x, normalised at
    the region edge, and upsampled once with a bicubic filter.
- The render matches the clean HD water with the reflections moving:
  1.4–3.0% of pixels change between frames; vanilla is 1.5%.

**trnad_1 — the fixed shaft "watermark" while the field has vanished:**
- Averaging 300 frames of the 10-03 capture leaves a static vertical shaft
  at the upper centre. The same average of the 618k capture (light pages
  still paletted) shows none.
- Cause: 618l/m froze the light-shaft pages 15..17 HD at their median frame.
  The script drives palettes 6/7 and they do not show during the vanish; a
  truecolor page cannot follow.
- ff7nx_trnad1fx.LIGHTS_HD = False: the shafts stay paletted. The fading
  copy (618m) is unchanged.

**zcoal_1 — "garbage" at the bottom during the shake (ff7nx_trainlower; the user's design):**
- 618m's copied row (ff7nx_shakepad) does not continue the picture; that is
  the garbage. ff7nx_shakepad is now OFF (FIELDS = ()).
- Measured bounce on the 10-02 capture: 0, ±1, ±2, ±3 units, symmetric. The
  script amplitude is 4.
- Layers 1/2/4 (train, wagon, trestle) move down 4 units. The camera is
  tilted (ff7nx_fieldshift.plan_camera, the md_e1 mechanism) so the models
  move down 4.02 units, with at most 1.46 units of x drift at the walkmesh
  extremes. Mountains (L3) and the camera range are unchanged.
- At the highest point of a bounce the bottom row of the train is exactly
  on screen. No art is added.
- zcoal_2 has two camera blocks and is refused (left as vanilla).

Tests:
- test_trainlower_zcoal_1_never_reveals_the_bottom (replaces the shakepad
  test).
- The trnad1fx test now expects no light conversion.
- The animdetail test still passes.
- Result: 65 passed, 6 skipped.

## 36. BUILD 618o — zcoal_2 lowered like zcoal_1

Hardware (10-03): zcoal_1, trnad_1 and sea are good; zcoal_2 showed the
same bottom strip during the shake.

- zcoal_2 has SHAKE y-amplitude 4. Its art: L1/L2 down to y=128, the L2
  wheel rows animated (param 1). L3 is the scrolling mountains; there is
  no L4.
- Section 1 holds TWO identical 38-byte cameras, which is why 618n refused
  it. ff7nx_trainlower now tilts every camera in section 1 independently
  and requires each tilt to succeed: models 3.95 units down, x drift 0.12.
- Layers 1/2 move down 4 units (309 records); L3 and the camera range are
  unchanged.
- Render result: at a 3–4 unit bounce, the bottom screen row now shows the
  bottom-most art row (partly transparent below the wheels, as authored)
  instead of nothing.
- Test: test_trainlower_zcoal_2_two_cameras.

## 37. BUILD 618p — las0_3 fog copies (and its class), ithill water

Hardware (10-03): qb, zcoal_2 and ithill fine; las0_3 shows flat-edged fog
bands at the top; ithill's water "alternates between 2 frames, slowly".

### las0_3 and five other camera-locked layers (ff7nx_parallaxfill)
- las0_3 L4 (the animated fog, FX blend 1) has header speed 256/256 and a
  1024x1024 period: it moves 1:1 with the camera like layers 1/2 and can
  never wrap inside the view. The parallax fill still copied its rows
  upward (y -88 -> -280, 372 -> 740 records): the flat-edged bands.
- Whole archive: exactly six layers are camera-locked with a 1024 period
  and were grown by the fill — las0_3 L4, del3 L4, junair L3/L4 (the plane
  and searchlights drawn 2–3 times), junonl2 L3, junonr2 L3 (the Rufus
  float; the L3 half of the 618 junonl2/junonr2 banner bug).
- New rule `camera_locked_overlay(hdr, layer)` skips them. FFNx fills no
  layer, so these now draw exactly what FFNx draws. Render check: no new
  layer-1/2 hole in any of the five fields (junair's 32-row black band at
  the top camera stop is vanilla/FFNx too).

### ithill water (ff7nx_animdetail)
- Layer 2 param 1, 3 states. Script loop (70 frames): none 25 -> s0 5 ->
  s1 5 -> s1+s2 15 -> s1 10 -> s0 10. The 60 fps capture changes at
  frames 21, 111, 162, 252, 303, 393, 444 (gaps 90/51 = 45/25 game
  frames): only "on" and "off" were visible.
- Vanilla: each phase changes 1–5% of the screen. Build: 0% between
  states (Cosmos's three cells are identical) and 13.6% at the on/off step
  (Cosmos painted the overlay paler than its layer-1 water).
- Fix: animdetail with two new options. `under=1`: the new frames are the
  HD layer-1 cell plus the blurred, upsampled vanilla motion (V_k -
  V_under), so the off phase and the states share one picture. `mirror`:
  Cosmos's 68 extra 16:9 cells take the vanilla motion mirror-repeated
  from the same row, gated to pixels whose picture matches the source
  (water onto water, never onto rocks; 24% pass).
- Result: phase changes 1.5% / 1.8% / 1.1% (vanilla 2.3 / 2.3 / 1.1);
  the on/off brightness step is mean 6.1 (vanilla 8.8, build was 11.7).
  30.38 MB. sea is byte-identical to 618n.
- The slow 2-step rhythm itself is authored: vanilla holds "off" 25
  frames and "on" 45.

### Answered, no change
- zcoal mountains (L3): already on 768px truecolor pages built from
  Cosmos's 1024px sheets. The field render target tops out at 3x
  (1280x720), which resolves exactly 768px pages; 1024px pages would need
  a 4x target that does not exist. No safe quality gain.
- colne_b3, qa, spipe_1: re-measured per state; the build moves as much
  as vanilla or more. Not frozen (the 618 audit metric over-flagged them).
- desert1: really frozen in 2 of its 7 phases (vanilla 3%, build 0.03%),
  but it stacks two records per position/state; animdetail does not
  handle that yet.

Tests: test_parallaxfill_skips_camera_locked_overlays,
test_animdetail_ithill_mirror_under, test_animdetail_sea_unchanged_by_618p.
Result: 65 passed, 6 skipped (618 + 617 files).

## 38. BUILD 618q — ithill water clean-up, las0_3 sky order and gap, del3/las0_3 last FX page

Hardware (10-03, 618p): junair fine. ithill: blocky highlights, water on
wood in the bottom-right corner, a yellow outline round a beam. del3: rough
shore edge, a pixellated patch on the far right, grainy sand. las0_3 (the
animation at the top of the screen): moves backwards part way; one frame
dirty/blurry. All three videos were analysed frame by frame.

### ithill (ff7nx_animdetail, `under` fields only)
- Motion is now gated everywhere (not only in the mirrored strips) to
  pixels whose HD picture underneath matches vanilla's (max channel < 40),
  and removed under and one texel around the static layer-2 records drawn
  in front of the water (the beams; `_front_mask`, vanilla OR HD). The
  gate is eroded one texel and feathered (sigma 0.7).
- Mirrored strips: motion only onto water-coloured HD (G - R > 12). The
  corner spot was a mirrored tide line on a plank at game (178, 66).
- Highlights: blur 1.1 texels (was 0.8) and a soft shoulder above 216
  instead of a hard clip at 255 (the clipped plateaus were the blocks).
- Phase changes 1.3 / 1.6 / 1.0% (vanilla 2.3 / 2.3 / 1.1). sea unchanged.

### las0_3 sky, layer 2 param 2 (ff7nx_underfill, new)
- Capture: the top changes at 80, 90, ..., 150, 159 (period 79 = WAIT 4 +
  7 x 5 frames). Frames 150-158 = the 4-frame gap with no state on, where
  Cosmos's layer-1 sky (bluish, mottled) shows: the dirty frame.
- Order: the moving part of each frame (static picture removed,
  normalised correlation). Vanilla s0..s5 is a smooth cycle (0.77-0.87
  between neighbours). Cosmos frame 3 = frame 0 (0.98), and the stored
  order steps through -0.49 ... -0.92: it jumps back twice a loop. By
  phase Cosmos's frames are 6,1,2,0/3,4,5; states are re-pointed to that
  (the best cyclic order of all 720; sum of neighbour correlation -0.70
  -> +1.79).
- Gap: each static layer-1 record under the 99 animated positions gets
  state 0 composited over its own cell, in 99 free cells of page 13. No
  page added, memory unchanged; gap == state 0.

### del3 / las0_3 last FX page (ff7nx_fxtrial, new) — HARDWARE TRIAL
- del3 FX page 23 (surf, far right) and las0_3 FX page 22 (bottom mist
  rows in frames 4-7) were the only paletted 1x FX pages left: converting
  them passes the 35.0 MB ceiling (both fields 34.38 MB). No duplicates
  to free a page (del3 2 of 448 cells).
- Converted through the normal ff7nx_fxpages path under a 37.5 MB
  per-field trial ceiling: both 37.44 MB, raw 13.18 MB (cap 13.42).
- 35.0 was never a measured failure (BUILD 130 picked it to admit the
  heaviest fields then known; fship_2 runs at 35.3). If it is too much the
  symptom is black squares in these two fields only.
  SEVENTH_NX_NO_FX_TRIAL=1 reverts.

### del3, answered
- Rough shore edge: the foam's edge is Cosmos's (the built page equals the
  Cosmos sheet resampled; its alpha is binary, the build already bakes the
  resampled alpha into the additive colour). Not changed.
- Grainy sand: Cosmos's painted stipple band. The build's anti-banding
  dither adds about half a 565 step of fine noise on top (all fields).
  Not changed.

Tests: test_animdetail_ithill_618q_no_motion_on_wood,
test_underfill_las0_3_reorder_and_gap. 71 passed, 6 skipped
(618 + 617 + 581).

## 39. BUILD 618r — ithill highlight blocks, del3 surf outline, las0_3 stretch

Hardware (10-03, 618q): las0_3 much better; junonl2 fine (parade not
triggered by the warp); del3 good but the water edge still choppy/dirty;
ithill better but blocky white patches remain.

### ithill (ff7nx_animdetail, `under` fields)
- The white blocks were Cosmos's TRANSPARENT texels in the overlay frames:
  the bright layer-1 cell showed through them, crisp-edged, inside a
  darker vanilla ripple. With `under` the new frame already is the
  layer-1 picture plus the motion, so the new cells are fully opaque.
- Brightening is now limited to the headroom between the HD picture and
  CEIL=236 with a smooth tanh approach (618q's shoulder on the sum could
  darken the picture and still flattened at 248). Blur 1.6 texels.
- Phase changes 1.2 / 1.3 / 0.8% (vanilla 2.3 / 2.3 / 1.1).

### del3 surf outline (ff7nx_fxsmooth, new)
- The foam is near-white right up to Cosmos's binary-alpha staircase, with
  one dithered half-lit row: on the sand the additive white saturates and
  that is the choppy/dirty line.
- Per FX cell (atlas neighbours are not screen neighbours: a whole-page
  pass drew lines along the atlas rows), the band within 2 texels of the
  boundary between lit art and a LARGE unlit area (holds a 21x21 square)
  is replaced by a gaussian (sigma 1.2, edges replicated) copy,
  re-quantised without dither. 0.1-0.6% of each page changes by more than
  16/255; the dappled water pattern is untouched.

### las0_3 "Cloud looks stretched on the right" — authored perspective
- las0_3's camera zoom is 190 (ithill 597, del3 381): a very wide-angle
  lens. A model at screen x gets about sqrt(1 + (x/zoom)^2) wider: 1.31 at
  the old 4:3 edge in vanilla, 1.50 at the 16:9 edge. FFNx shows the same.
  The camera has to match the pre-rendered background, so it cannot be
  narrowed. Not a bug; field closed.

Tests: test_fxsmooth_del3_outline_only (ithill tests updated in place).
72 passed, 6 skipped (618 + 617 + 581).

## 40. BUILD 618s — del3 sea footsteps, mrkt2 steam, junbin5 glow, ujunon1 smoke (answered)

Hardware (10-03, 618r): ithill fine, junonr2 fine, del3 good enough
(sand steps on the sea), junbin5 blocky light effects (not the cone).
Raised from a save: mrkt2 pixellated steam; ujunon1 smoke "like a broken
record".

### del3 footsteps (ff7nx_stepsextra, new)
- Not the probe and not our mapping: Cosmo Memory gives del3 one
  field-wide route ([del3_159] = 5110-5115, its world-map DESERT set) and
  no triangle overrides; FFNx + Cosmo plays sand on the sea too.
- Cosmo's world-map RIVER CROSSING set (walkmap 4, wading) is 5130-5135
  (also its qa_159). del3's walkmesh triangles 259-332 are the sea block;
  projected through the camera, the 71 whose centre is at background
  y >= +16 (the surf's reach) route to 5130-5135. Appended after the mod's
  configs as Cosmo-grammar text; no new audio payload (the sequence is
  already in the pool). SEVENTH_NX_NO_STEPS_EXTRA=1 removes it.

### mrkt2 steam, junbin5 glow (ff7nx_fxpcstatic)
- mrkt2: FX page 15 (steam, param 31) stayed 256px paletted because its
  palette 9 is pulsed (ADPAL+LDPAL flicker); its static palettes were
  already split off. Cosmos ships the pulse as 8 hash frames: pulse
  composite at the median level (crater_2's method). 31.00 -> 34.06 MB.
- junbin5: FX pages 15/18/19 paletted. 18/19 = multi-sheet (palettes 3-6
  `_00`, 7/8 single hash sheets): hyou5_2-style composite. 15 = the same
  plus pulsed palette 9: new MIXED composite (static palettes from their
  sheets, palette 9 from its median pulse frame). 25.81 -> 35.00 MB,
  junbin5 capped at 35.3 (fship_2's proven level).
- Renders: the blocky 16x16 glow and the pixellated steam become the HD
  Cosmos pictures; the tile-state animations keep running; the small
  palette-9 flicker stops (as in crater_2).

### ujunon1 smoke — answered, not changed
- No frames are missing: two plumes, each a chain of three tile params
  (8 + 8 + 4 states, 4 frames each) = an 80-frame (2.7 s) loop, all 40
  states present and truecolor.
- The smoke is drawn by palette tricks: palettes 10-13 are permutations of
  the same 11 grey levels. Cosmos's single page image matches the vanilla
  frames for palettes 10/11/13 (mean diff 1.6-5.0) but not palette 12
  (34.2): the middle of each plume (param 2 states 4-7, param 3 state 0,
  param 5) uses palette 12, which inverts the ramp (index 0 = grey 72).
  Cosmos painted those frames as ordinary plumes, so that part of the loop
  changes about half as much as the rest and the 2.7 s repeat is easier
  to see. Reproducing palette 12 literally would draw grey squares around
  the plume. Left as Cosmos/FFNx show it.

Tests: test_stepsextra_del3_sea_routes, test_fxpcstatic_mrkt2_junbin5.
74 passed, 6 skipped (618 + 617 + 581).

## 41. BUILD 618t — archive-wide FX audit and pass; mtnvl3, icedun_2, junbin5

Hardware (10-03, 618s): mrkt2 "much better"; del3 perfect (footsteps);
junbin5 "like 1/4 of the glow"; icedun_2 and mtnvl3 broken FX layers. The
user asked how many fields share these problems and for a way to fix them
with confidence rather than one field at a time.

### Audit (whole archive, _scratch/c6/fxaudit.py, fxclass.py)
- 432 fields with FX records; 219 still have 256px paletted FX pages
  (341 pages); 81 have converted cells that do not match vanilla.
- Why each paletted page is paletted:
    pulse > 2.7x (real fades)            158 pages  keep
    pulse <= 2.7x (mrkt2 class)           88 pages  convert
    non-additive blend                    30 pages  keep
    no Cosmos art (.xone etc.)            15 pages  keep
    multi-sheet / single (budget, set once) 32 pages  convert
    palette cycles / permutations, base pages, Cosmos-animated  keep

### Root causes of the three reports
- mtnvl3: ff7nx_fxrequant (BUILD 582) requantised palette 10 into its own
  entries, but the script loads palette 9's buffer INTO palette 10
  (STPLS 9 -> ADPAL -> LDPAL 9 and 10): at run time those indices drew with
  palette 9's colours = the washed-out rectangle with a bridge ghost. Fixed
  in fxrequant: a whole-palette load from another palette refuses the
  field (12 fields affected; 4 had actually been requantised: gidun_1,
  las0_8, mtnvl3, trnad_1 -- trnad_1 is confirmed and keeps its own pass).
- icedun_2: FX pages 15/16 multi-sheet (palettes 11/12 each have their own
  Cosmos sheet; set once by ADPAL at load) stayed paletted; page 24 (110
  average-blend records) is a FULLY TRANSPARENT sheet in Cosmos (FFNx
  draws nothing) but the 1997 art drew dark squares and lines. Pages 15/16
  converted; page 24's records removed (BLANK).
- junbin5: palettes 3..6 are permutations of one grey ramp; the glow
  animates by state through them (palette 5 lights index 0 = the whole
  cell). Cosmos painted every cell with the palette-3/4 ramp. New
  ff7nx_fxpcstatic.ramp_remap maps each cell from the ramp Cosmos used to
  its own palette's ramp, and fades the lifted background over one cell
  towards sides with no neighbour, so the bloom is back without the 1997
  squares.

### ff7nx_fxcomplete (new) — the archive-wide pass
Only hardware-proven conversions (mrkt2/crater_2 median pulse frame,
hyou5_2 composite, single sheet), page by page, refusing:
fades > 2.7x; a palette refused anywhere refuses every page it is on;
palette permutations without their own Cosmos sheets; palette rotation;
non-additive blends; base pages; the memory cap; confirmed fields
(CONFIRMED), woa_1/woa_2/mtcrl_8/blin70_1 (HOLD, render review).
Dry run on the 618s archive: 88 pages in 73 fields (now 71 with the two
holds), all <= 35 MB. Every converted field was rendered old vs new at 3
camera positions (contact sheets _scratch/c6/fxsheet_*.png) and the
FX-contribution compared; the remaining flags are pulse fields whose
stored palette is brighter than any runtime state.

Tests: test_fxrequant_refuses_cross_palette_loads,
test_fxcomplete_mtnvl3_icedun_2_and_vetoes, test_fxpcstatic_junbin5_ramp_bloom.
77 passed, 6 skipped (618 + 617 + 581).

## 42. BUILD 618u — what 618t got wrong, and the fixes

Hardware (10-03, 618t): nivl_b1 fine; mtnvl3 much better but two squares
in the bottom-left corner; icedun_2 unchanged; junbin5 lamp glow animates
only in its lower half; REGRESSION: eals_1's waterfall broken.

### Why the 618t dry run did not match the build
The verification used an art provider over EVERY folder of the Cosmos
.iro. The build only installs the folders 7th Heaven activates (`limit
break/`, `limit break aa/`, `limit break aaf/` -- not `AA REMOVED`). So the
dry run saw hash frames the build never had: icedun_2 and mtnvl3 looked
convertible but were refused in the real build (hardware archive: their FX
pages still 256px), and the build's frame sets (e.g. eals_1's) were not the
ones reviewed. From now on every check uses `_scratch/c6/buildart.py`,
which builds the provider exactly as build.py does (settings.json +
iro.active_folders), and tests use it too.

### eals_1 (and the class) — ff7nx_fxcomplete
The waterfall is a palette CYCLE: 152 installed Cosmos frames of nearly
equal brightness, so the brightness test (<= 2.7x) took it for a pulse and
froze it. New rule: a pulse must be at most 64 frames whose pictures are
scaled copies of each other (structure residual <= 0.10; crater_2 0.02,
cycles 0.16-0.67). Refused by it, against the 618t build: eals_1 15,
junonr1 16, mtnvl2 15, zz6 16, semkin_4, trnad_4 and the high-residual
pulses (snmin2, blin65_1, niv_w, nvmin1_1, losin2/3, anfrst_1 16, chorace,
chorace2, junpb_1, smkin_1). These return to their animated 1x pages.

### junbin5 lamps — ff7nx_fxpcstatic KEEP_PALETTED
The orange lamp glow is palette 9 (script pulse, Cosmos-animated). Its
lower rows were on paletted pages 16/17 (still pulsing), its upper 10 rows
on page 15, made truecolor in 618s = frozen. Those 10 records now move,
cells copied, to free cells of page 16 (paletted, already palette 9): the
whole lamp pulses (render: three palette levels change every lamp cell).

### icedun_2 — fxcomplete `set_once`
Palettes 11/12 are written once at load (STPLS/ADPAL/LDPAL before a BGON
loop that never reloads) and Cosmos installs no frames for them, so FFNx
draws the shared `_00` sheet: pages 15/16 truecolor is exactly PC. Plus the
blank page 24's 110 records removed (dark squares/lines).

### mtnvl3 corner — ff7nx_fxrequant palette group
The mist is palettes 9 and 10, both loaded from palette 9's buffer (no
Cosmos frames installed). The 4:3 cells were 1997 art and Cosmos's
widescreen strips Cosmos art, so the bottom-left strip cells (palette 9,
no 1997 counterpart, bright) and the dark 1997 cell beside them showed as
two squares. `single_source_group` requantises ALL 496 mist cells from
Cosmos art into palette 9's entries (and copies them to palette 10); the
script still pulses it. Error vs Cosmos 9.3 -> 2.2. Only for fields listed
in GROUP_FIELDS (mtnvl3).

Tests: test_fxcomplete_618u_rules, test_fxrequant_mtnvl3_group,
test_fxpcstatic_junbin5_lamp_stays_paletted (+ updated). 79 passed,
6 skipped.

## 43. BUILD 618v — 512px paletted pages as an opt-in experiment

**Why.** The 618u leftovers (junbin5 lamps pixelated, mtnvl3 top steps, the eals_1 waterfall edge, and the eals_1 flow reading as shimmer) are all effects the script animates through their palette. A truecolor page has no palette, so these effects must stay on depth-1 pages, and depth-1 pages are 256px (1x).

**The switch.** `SEVENTH_NX_FIELD_BG_D1_PX=512`, which has existed since build 108. It patches the module to read every paletted page at 512 and lifts every paletted page in the archive (`_lift_depth1_payloads`). The lift is all-or-nothing, because the page size is an engine constant. Leave the variable unset and the build is byte-identical to 618u.

**Bug fixed in the switch.** The lift used to sit about 45 passes too early. Since build 108, every pass after it (fshipart … trainlower: fxpcstatic, fxcomplete, fxtrial, fxsmooth, texpatch, stackorder …) has been added below it, and all of them parse depth-1 pages at 256. With the switch on, they would have read 512px pages as 256px ones. The lift now runs after the last page pass, just before the debug warp. A test pins that order.

**New: `ff7nx_fxd1art`.** During the lift, each 16x16 cell of an FX page (slots 15–23) becomes a 32x32 block of indices, still drawn through the same palette, so the animation is kept.
- **Reference art:** the Cosmos 512 block whose colours match the cell's, searched by content so relocated cells still match.
- **Static or uniformly animated palettes:** any palette index may be used.
- **Cycles or partial writes** (MPPAL2 0xDF, RTPAL; this is eals_1): only the indices in the cell's 3x3 neighbourhood may be used, so the cycle layout is preserved.
- **Drift gate:** a block that ends up worse than replication falls back to replication.

Shadow art (Cosmos margins) is applied first and is keyed by content, so passes that ran after it was recorded cannot make it stale. `SEVENTH_NX_NO_FX_D1_ART=1` keeps the 512 lift and turns off this new art.

**Measured on the 618u hardware sections.**

| Field | Cells | Mode |
|---|---|---|
| junbin5 | 13 (2 no match) | full |
| mtnvl3 | 496 | full |
| eals_1 | 49 | neighbourhood |

After the lift, every section re-parses at 512 with identical pages. Truecolor pages are byte-identical, and only FX pages differ from 2x replication.

**Risks.**
1. **Memory.** A paletted page grows from 0.31 to 1.31 MB, so heavy fields rise. The heaviest (the fship_2 family) reach about 40 MB, against 37.44 MB proven on hardware. The build log line `! DEPTH-1 LIFT MEMORY` names every field above 37.44. If a field fails, the failure is black squares in that field.
2. **Archive-wide engine change.** The 512 lift has not been run on hardware since around build 108.
3. **Raw-cap margin.** The largest lifted field is about 13.36M against a 13.42M raw cap. A refusal stops the build; it does not ship half-lifted.

### 43a. Outcome (hardware, 618v)
Not truecolor: the 512 pages are still 8-bit paletted, just 2x the texels. FPS dropped badly. The likely cause is that the engine re-converts and re-uploads a paletted page whenever a palette changes, and at 512 that is 4x the work per page, every frame, in every field. (FFNx fixed the same cost on PC: "avoid texture reupload in case of palette swap".) The experiment stays opt-in and off.

## 44. BUILD 618w — truecolor palette animation as tile states (junbin5, eals_1)

**Idea.** The engine cannot swap a texture per palette state, but it can switch tiles on and off (BGON/BGOFF). Cosmos ships one HD picture per palette state (the `_<hash>` frames in LIMIT BREAK AA), which is what FFNx swaps on PC. `ff7nx_palstates` (new, after fxcomplete) does three things:
- puts each state on truecolor FX pages as its own cells;
- gives each state its own copies of the effect's records, under unused params with one state bit each;
- replaces the field script's palette ops in place with BGCLR/BGOFF/BGON, driven by the same variables and timing.

The effect's paletted pages are dropped once nothing else reads them.

**What the three scripts do (decoded with kujata's opcode layouts).**
- **junbin5 lamp, palette 9:** var5[0x29] walks FF..FA..FF (WAIT 3) and ADPAL adds it (-1..-6, 5-bit units). Cosmos ships 6 frames per page.
- **eals_1 spray, palette 9:** a random 1–3 step dip of the multiplier var5[4] (66/62/58/54, units /64). Cosmos ships 4 frames.
- **eals_1 waterfall, palette 8:** every frame, two 11-entry windows of palette 8 are brightened (MPPAL 0xDF, x130/64 and x162/64). Their starts step 12 (wraps after 9 phases) and 9 (wraps after 16 phases), so there are 144 states. Cosmos ships 152 frames: all 144, plus the first lap's 8 off-grid phases (var3 starts at 71). Rendering the 1x page through this arithmetic and matching it to Cosmos's frames labels 150 of the 152.

**Levels (junbin5 lamp, eals_1 spray).** Exact: frame k by brightness is state k.
- junbin5: the walk becomes 07..02 and the 15-byte ADPAL+LDPAL becomes BGCLR 8 + BGON 8,var(x3).
- eals_1 spray: var4 runs 3..0 and each 16-byte MPPAL+LDPAL becomes BGCLR 28, BGON 28,var4, STPAL (harmless), BGON 28,var4.

**Waterfall: base + A + B.** 144 full frames would need 17 pages. Instead, per texel, least squares over Cosmos's frames fits `frame(a, b) = base + A[a] + B[b]`. A and B are shifted non-negative and drawn additively.
- Fit error 2.4 against 10.3 of frame-to-frame motion. Rendered against Cosmos's frames, the mean error is 3.6/255, including 565 quantisation.
- The rest is where the two windows overlap, since the second MPPAL overwrites the first.
- The 70-byte loop becomes a 49-byte loop over params 3..11 (A) and 12..27 (B): BGOFF the shown params, step and wrap, BGON the new params, WAIT 1. The remaining 21 bytes are RET, never reached.

**Result (planned on the 618u hardware sections).**

| Field | Records | Pages | Memory |
|---|---|---|---|
| junbin5 | 15 → 90 lamp records | onto free cells of truecolor pages 15/19; paletted pages 16/17 dropped | 34.38 MB (was 35.0) |
| eals_1 | 49 → 856 records | page 15 becomes truecolor, cells on 16/17, new pages 18/19; no paletted FX page left | 27.3 MB |

Every page binds 256 tiles or fewer. Stack order leaves both fields unchanged. Neither field rewrites a palette every frame any more, so the per-frame re-upload is gone too (the FPS cost from 43a).

**Off switches.** `SEVENTH_NX_NO_PAL_STATES=1` disables the pass; `SEVENTH_NX_PAL_STATES_ONLY=a,b` limits it to the named fields.

**Tests.** test_palstates_script_patches_apply_to_vanilla and test_palstates_plan_moves_effects_to_truecolor.

## 45. BUILD 618x — palette states found and proven automatically (13 more fields)

`ff7nx_palauto` (new, right after ff7nx_palstates) applies the junbin5 method wherever a field's palette effect has the common shape: one byte variable drives one brightness op (ADPAL add, MPPAL2 multiply, or a whole-palette MPPAL), followed by `[CPPAL S->D] LDPAL q`. Every writer of q must have that identical shape.

**What it proves before changing anything.**
1. **Script reading.** Operand layouts for every opcode come from kujata's parser (generated into `ff7nx_opbanks.py`). Every instruction touching the variable must be one that can be renumbered: SETBYTE literal, PLUS/MINUS (incl. the saturating forms), INC/DEC, or IFUB against a literal. Anything else refuses the effect.
2. **States from the script's own bounds.** The lattice of values starts at a set literal, with the gcd of the steps as its step. Each comparison bounds the range by what it guards:
   - `v < c` guarding PLUS: the top is the last passing value plus the step;
   - `v > c` guarding MINUS: the bottom is the first passing value minus the step;
   - a reset guard: values that pass are reset before they are shown;
   - `==`: the literal itself.
3. **One-to-one with Cosmos.** The frame count must equal the state count, with two exceptions:
   - a starting value at one end that is never loaded shares its neighbour's frame;
   - with at least 85% of the states captured, a monotone brightness alignment fills the missing ones from their neighbours.

   Frames are paired with states by brightness order, and brightness must be monotone in the op's value.
4. **Right cells.** On the brighter half of the states, each Cosmos frame must correlate with its state's 1x render, and beat the same frame with its cells rolled by one.

**Rewrite.**
- **≤ 8 states:** one parameter. The variable is renumbered to 0..n-1, and each value op + LDPAL becomes `BGCLR P, BGON P,var`.
- **More states:** one parameter per state, a block at the top of 1..63. The variable is renumbered onto the parameter ids, and each site becomes `BGOFF var5[prev]; BGON var5[v]; var5[prev] = v`. Here `prev` is a temporary no instruction uses.

FFNx's background code (`anim_group && !(background_sprite_layer[anim_group] & bitmask)`) confirms that parameter 0 always draws and that the state table has exactly 64 entries. Field temporaries start at 0, so the first `BGOFF 0` is harmless.

**Result on the 618w build:** 13 fields (blin1, blin70_1, elmin1_1, gldelev, jetin1, las4_2, las4_3, mds5_5, sbwy4_6, semkin_3, smkin_2, smkin_3, smkin_4).
- 2 to 59 states each.
- All within 35 MB, the 256-binding cap and the raw cap.
- Stack order leaves all 13 unchanged, and the patched scripts decode.
- The state ramps were reviewed as renders.

**Not converted, and why** (126 candidate fields):
- 54: another palette pattern (rotations, windows, multi-variable);
- about 25: the effect also uses tile animation (record param ≠ 0), which can't be combined;
- 11: ADPAL2 (0xEF), semantics unknown;
- 3: CPPAL2 / RTPAL;
- the rest: Cosmos frames don't cover the states, or KAWAI near the variable.

`SEVENTH_NX_NO_PAL_AUTO=1` disables the pass. `SEVENTH_NX_PAL_AUTO_SKIP=a,b` excludes fields, and `SEVENTH_NX_PAL_AUTO_ONLY=a,b` limits it to the named fields.

**Tests:** test_opbanks_layouts_match_known_ops, test_palauto_renumber_keeps_comparisons, test_palauto_reproduces_junbin5_by_itself, test_palauto_gldelev_one_param_per_state.

**Beams (colne_6), asked about.** Not converted on purpose. A truecolor page stores 5 bits per channel, which gives the dim yellow beam about 6 brightness steps. The current mode-3 paletted page has about 4x finer steps. Being soft gradients, the beams gain nothing from texel resolution.

## 46. BUILD 618y — las4_2: background movie fills 16:9, cut-out edges cleaned

Hardware (10-04, 618x):
- **Fine:** sbwy4_6, smkin_2..5, gldelev's lights.
- **las4_2 "most messed up":** 4:3 with bars, and dirty pixels around the stones.
- **gldelev:** rough edges on the elevator.
- **crcin_2:** its fading beam shows colour contours.

**las4_2 is a movie.** The waterfall is not field art. Section 9 holds only the stepping stones (layer 2, opaque) and the water drops (palette 3, made truecolor by 618x). The script plays movie 20 (PMVIE 0x14 / MOVIE) as the background.
- Cosmos's `movie_config.toml` gives exactly four background movies `mode = 4`: last4_2, last4_3, lastmap, mtcrl. That is WM_FILL: FFNx (gl.cpp `gl_draw_movie_quad_common`) draws the movie quad across the whole wide viewport.
- The field's own entry is `mode = 1` (extend only, -160..160), so the stones stay where they are.
- On the port, ff7nx_moviebars painted the 4:3 bars because its gate is "any movie playing". Its notes named this exact case (§5, the BGMOVIE field) as the open question.

**Fix (`SEVENTH_NX_MOVIE_FILL`, on by default; =0 restores the old caves exactly).**
- **ff7nx_moviealign:** its hook at +0x10DE8F0 (all four quad vertices written) now also reads the guest's `modules_global_object + 0x3A` (BGMOVIE_flag, 0xCC0DC2). ff7nx_camclamp already reads that flag on hardware. The read goes through the translator, with x0..x18 and x30 saved. When the flag is set, the quad's x becomes -M..640+M, with M = 640·(1/WS_SCALE − 1)/2: the 16:9 width.
- **ff7nx_moviebars:** after its movie gate, the same flag skips every bar.

Ordinary FMVs (flag 0) are unchanged. The bars verifier executes the cave: 34 checks pass. The quad cave was disassembled from a stock module.

**las4_2 stones (`ff7nx_edgeclean`, new).** Cosmos cut the stones out of frames with the green waterfall behind them, so their soft edges carry green, and half-transparent specks sit outside the shape. FFNx blends them away; the hard key turns them into dots. Composed in field space:
- islands under 40 texels are keyed;
- the 2-texel rim takes the mean colour of the solid interior (9x9 window);
- thin whiskers with no interior behind them are keyed.

On las4_2 that is 414 specks, 2392 whisker texels and 10009 rim texels, in 145 exclusive cells. `SEVENTH_NX_NO_EDGE_CLEAN=1` disables it.

**gldelev elevator.** Not background art: the field's models are AAAA (Cloud), FWAE and GRCC, and the elevator is model GRCC. Its rough edges are the 1997 model texture. Ninostyle (Chibi and Fixes) has AAAA and FWAE but no GRCC, so no installed mod has an HD elevator. Listed as a model-texture item.

**crcin_2 beam.** Palette 6 fades through 51 levels (MPPAL by 16..66/64) over 34 cells. That is about 1730 HD cells: more than the FX band holds and over the memory cap, so palauto refuses it. The contours are the 5-bit palette fade itself. Options: about 13 coarser HD levels (a script step change), or leave it.

**Tests:** test_edgeclean_las4_2_removes_specks_keeps_inside, test_moviefill_cave_and_bars_gate.

## 47. BUILD 618y (second round) — las4_2 zoom and HD cut, trnad_3, whitein, woa_1, elevator

Hardware (10-04, 618y). Both videos were reviewed frame by frame (trnad_3 690 frames, whitein 885).

**las4_2.** The movie fill stretched, which left bands top and bottom, and the stone edges were still rough with blue on their dark sides.
- **Zoom.** The movie-quad cave now also scales y about the picture's centre by the same factor as x (1.3375 at this scale), as FFNx's WM_ZOOM does.
- **HD cut.** `ff7nx_edgeclean` now cuts each stone cell with a vanilla twin by Cosmos's own alpha (>= 50%). That alpha is smooth at 3x, where the build had the 1997 page's 1x stair steps. The colour inside comes from Cosmos too. The one-texel rim takes the nearest solid interior colour, so no matte or blue survives.

**trnad_3.** Gaps, light squares, and the haze layer jumping on the right. The 618k exact repeat for layer 4 was never applied in the build: `apply_to_sections` refused it on the binding check, because an unrelated base page (13) binds 275 tiles in total. That page is within the per-screen limit: `window_over` is empty in all 711 fields, and 25 fields have such a page. The 384x256 layer stayed on parallaxfill copies: 35% of the 16:9 picture uncovered at worst, and copies sampling the wrong cells (the light squares). The check now judges only pages the pass changes, plus the per-screen limit. trnad_3: 396 records, 0 uncovered, 0 doubled. The authored grid is a clean seamless wind texture.

**whitein.** The searchlight beams (param 1, states 1/2/4/8, palettes 9..12, paletted 1x pages) lost whole squares.
- **Cause.** field_bg_compact moved fx cells from page 16 to 15 at the same coordinate. A later pass looks Cosmos art up by the slot a record now names, and wrote Cosmos's page-15 sheet over them: built page 15 is blank exactly where Cosmos's page 15 is.
- **Scale.** Across the 618x build, 1051 FX records in 118 fields draw nothing where vanilla drew light. Some of that is Cosmos's own choice (mtnvl6, sininb41, junone5: Cosmos is blank too). The rest is loss: games_2 149, whitein 60, snow 50, mds6_22 46, white1 42, …
- **`ff7nx_fxrestore` (new, last texture pass).** A cell is refilled from Cosmos's cell for the vanilla twin's page and palette when it draws less than 40% of vanilla's light and of Cosmos's. It is quantised into the record's palette on a paletted page, encoded additively on a truecolor one. Only exclusive cells; fields with a dedicated effect pass or confirmed on hardware are skipped.
- **Still open in whitein.**
  - A few cells shared with healthy records.
  - Margin tiles Cosmos added: no vanilla twin, and some state-2 ones carry palette 9.
  - Thin staircase lines under the purple ribbon: 1997-shape holes in the glass, not yet located.

**woa_1.** "Small batches of missing pixels." The wind haze (layer 3, paletted 1x pages) has palette-index-0 dashes inside the light, repeated by the haze grid. **`ff7nx_fxholes`** (new) fills an unlit group of at most 12 texels on paletted additive effect pages when its whole ring is lit (mean >= 40). That is 2104 texels in 44 fields, woa_1 47. Truecolor pages are left alone: their dropouts are under a third of a native pixel, and del3's dappled surf is confirmed.

"The FX layer overlaps the field in the widescreen part": Cosmos's margin art has the wind haze baked in (visible with the haze layer off), so the moving haze adds a second layer there. Not fixed yet.

**gldelev elevator.** The texture is model GRCC's `grcf`, one of Cosmos's 76 prop textures, and it is already HD in the build (ff7nx_chardds, build 580: 591 KB TEX, the same as the gondola). The roughness is Cosmos's upscale itself: black squiggles in the light strip just under the box's top rim (rows 22..40 of the 1024px sheet), which the rim samples. `ff7nx_chardds.PREPROCESS` now cleans that strip (texels darker than 55% of a 41-wide horizontal median take the median), and VERSION char-dds-2 rebuilds the cache.

**Tests:** test_fxrestore_whitein_beams_come_back, test_fxholes_woa_1_dashes_filled, test_trnad4_trnad_3_repeat_now_applies, test_chardds_grcf_strip_clean_only_touches_its_band, test_moviefill_zooms_y_too.

## 48. BUILD 618z — las4_2 blue stones, whitein beams and glass lines, woa_1 glyph

Hardware 10-04, build 386 (log 386). The video (1,757 frames, all whitein, one camera) was reviewed frame by frame: the beam cycle repeats about every 15 s; the right beam is checkered in every lit phase, there is a lone bright square near the right 4:3 edge, the left beam is blocky in the margin, and thin dark stair lines cross the glass along the ribbon band in every frame.

**las4_2: blue stones.** The port widens 565 to 1555 as `((v & 0xF800) >> 1) | ((v & 0x07E0) >> 1) | (v & 0x1F)`, so green's low bit lands in blue's top bit (+128 blue). That is why field_bg_repack keeps green even. 618y's `ff7nx_edgeclean` wrote Cosmos colours with odd green, and opaque black as 0x0020. A scan of every truecolor page in build 386 found odd-green texels in las4_2 only (123,689 of them; ujunon2 is the warp copy). Fixed: green on the 31-level grid, opaque black is 0x0841. This was also the "blue on the dark side" of the round before.

**whitein: beams.** Cosmos ships the beam palettes as hash frames (all 12 beam slots are ambiguous): one frame per palette state. The frame the provider picks paints the state-2 beam at about 13% of vanilla, which gives the black squares. One cell, (176,-16), is 5x too bright, which is the lone square. Because the runtime palette animates the indices, the vanilla cell is the truth. `ff7nx_fxrestore` now copies vanilla's indices, remapped into the built palette, where a beam cell draws under 60% or over 1.8x + 8 of vanilla: 147 cells on the 618x input.

This path is scoped to `ANIM_FIELDS = ('whitein',)`. A dry run over build 386 had it fire in 50 fields (uutai1 758 cells, las0_8, semkin_8, mtnvl2, ...). Rendered, those were fine in the build and became blown-out 1997 squares.

**whitein: glass lines.** In the 1997 art, layer 1 is black inside the ribbon band and a static additive overlay (Cosmos HD, pages 19-21) paints the glass on top. On hardware the line is 77% of the light on either side, 1 px wide, and follows layer 1's 1x hole edge exactly. Cause: layer 1's black bleeds through filtering, and the keyed overlay's edge pulls in. A hard-texel software render is clean.

New `ff7nx_bandbake` bakes the overlay's colour into layer 1 wherever layer 1 is black under it and clears the overlay there. Black + overlay = overlay, so the picture is unchanged (software render max diff 8/255, the old 0x0841 lift). It skips texels within 2 of layer 2's opaque art (the spire tips have the overlay landing on them) and only touches exclusive cells. In whitein that is 28,374 texels.

It is scoped to whitein and ujun_w: in a dry run it would hit 114 fields, where the overlay is mostly a light in front of characters. (618z first overwrote the existing 618q `ff7nx_underfill.py` by name; that file was restored from the user's folder, and the new pass is a separate module.)

**woa_1: glyph.** The black "9" over the waterfall is a hole in steam frames 1/2: about 30 texels of index 109, which palette 2 (and vanilla's) maps to colour 0. Vanilla hides it over a light wall; Cosmos's darker wall shows it. `ff7nx_fxholes` now fills enclosed holes up to 48 texels on paletted pages, but a hole over 12 texels only if every texel is a nonzero index. Without that rule, zz6's sparkle would have lost the index-0 wedges between its rays. Dry run: +5,112 texels in 30 fields (ujunon4/5, md_e1, games_2.xone, zz6 0, cosmo2, ...); the renders checked show only holes and dither specks.

**Not done.** woa_1's bottom-right haze cut (it would need a scissor following the floor's edge). whitein's margin beam records with no vanilla twin (state 2 on palette 9) stay as Cosmos drew them.

**Debug warp:** `jumin=gldelev` replaces `jumin=trnad_3` (trnad_3 confirmed fine).

**Tests:**
- new: test_edgeclean_las4_2_green_lsb_clear_no_blue_cast, test_fxholes_618z_glyph_filled_wedges_kept, test_fxrestore_618z_whitein_animated_beams_from_vanilla, test_bandbake_whitein_same_picture;
- 618: 67 passed, 2 skipped; 617+581: 36 passed, 4 skipped; build_guard/582/608: 19 passed.

### 48b. 618z (cont.) — whitein's margin beams and the woa_1 floor cut

**whitein: the whole beam from vanilla.** With only the dark cells copied, the beams mixed vanilla cells with Cosmos-frame cells at 60-95% of their brightness. That still read as a staircase of squares, most visibly the left beams in the left margin. In `ANIM_FIELDS`, every lit twinned beam cell now comes from vanilla (`ANIM_ALL`): 530 cells on the 618x input. Re-measured per state against vanilla, no lit cell is dim any more.

**whitein: the margin beam continued.** The 28 state-2 margin records Cosmos added (x 192-240, no vanilla twin) carried palette 9, state 1's palette, so the beam's end pulsed on state 1's timing. Their art was a cut of one dim hash frame.

`ff7nx_fxrestore.margin_palettes` now handles this:
- **Palette.** Each orphan record takes its state's palette.
- **Art.** Its cell is the beam continued along the beam's own direction: the principal axis of the state's lit texels in the 64 units next to the margin, slope 1.17 for state 2 and 0.69 for state 1. Each margin texel takes the index of the last in-picture texel on that line, so the runtime palette animates it with the beam.
- **New records.** Where the band leaves the old rows before it leaves the screen, new records are added on free cells of FX page 18: (224,96), (240,96) and (240,112) for state 2. Before, the beam was cut at y = 96.
- **Fallback.** A state with no clear band only gets its colours re-quantised into the state's palette.

**woa_1: floor cut (new `ff7nx_floorclip`).** In the 1997 field the floor is layer 2, drawn after the haze, and stops at the 4:3 edge (x = 160). Cosmos's margin floor is on layer 1, under the haze, so with the haze on the floor was clear inside 4:3 and hazed in the margin, with a vertical seam.

The margin's layer-1 art is split along the floor's edge:
- rock: lum < 44 (the cliff behind the waterfall is 48-66), connected to layer 2's edge column or the bottom row, no higher than layer 2's floor top minus 8 units;
- flat unpainted cells are skipped, and the floor runs down to the bottom;
- the corner cell Cosmos filled with a black 1x filler takes its left neighbour.

The rock is added as 10 layer-2 records on free cells of page 27 (186 + 10 bindings). Layer 2's own keyed edge texels beside them take the margin colour (45 texels), which closes a 1-texel haze line. Rendered in the port's order (layer 1, haze, layer 2), the margin floor is clear and nothing above it changes.

**Tests:** test_fxrestore_618z_whitein_margin_palettes, test_floorclip_woa_1_floor_in_front_of_haze. 618: 69 passed, 2 skipped; 617/581/guard/582/608: 55 passed, 4 skipped.

## 49. BUILD 618z (2nd round) — whitein HD beams, BG-movie fields at 60, woa_3, trnad_4

Hardware 10-05 (after 48b): woa_1 confirmed, scissor included. Next probe: woa_2 instead of woa_1.

**whitein: beams (new `ff7nx_beamhd`).** "Extremely low quality, contours, discontinuous at the widescreen part, going off at a different angle." The 1997 beam is a posterised ramp, so copying vanilla cells (48b) brought the bands back. The beams have to stay paletted (the script pulses them with MPPAL on entries 0..127), and a truecolor level set does not fit (4 beams × ~580 cells × levels, against 2 free FX slots).
- Each beam palette's 127 animated entries are rebuilt as a k-means ramp of the beam's own colours, taken from Cosmos's brightest hash frame for that page and palette and scaled to the 1997 brightness. Scales are 1.12–1.18, and 48–60 distinct 15-bit entries are used.
- Each cell is quantised with a 4×4 ordered dither of one 5-bit step. Black is a candidate too, so the dim edge dithers out.
- The one static record that reads palette 11 is re-quantised into the new entries.
- Entries 128+ and every other palette are untouched. The pulse is unchanged.
- Runs after fxrestore/fxholes. `SEVENTH_NX_NO_BEAM_HD=1` disables it.

**whitein: margin continuation (fxrestore `margin_palettes`).**
- Where the beam's top and bottom edges fit lines over the last 64 columns, the margin continues the beam as the cone it is: each texel sits at its relative place across the beam.
- Colour comes from the mean of the last 4 in-picture columns, with the fade continuing at half the in-picture rate. Measured cross-section: 4112 → 3376 across the edge, and still lit at the 16:9 edge.
- Re-dithered into the state's lit indices, so it pulses with the beam.
- Bug fixed: the add-record loop read `xe` from the wrong tuple slot.

**whitein: ribbon line.** The bandbake is all-or-nothing per connected lit component (the seam at x = −96).

**las4_2 / las4_3: BG-movie fields at 60 (new `ff7nx_moviepace`, exefs).**
- `fw_movie_update` (+0x10F1590) blocks until the next decoded frame is ready. In field mode with `BGMOVIE_flag` (0xCC0DC2) set, the wait cave now takes the not-ready path (redraw the last frame) instead of waiting.
- The field limiter (+0x9E5C14) is also enabled while BGMOVIE is set.
- The field renders at 60 and the movie plays at its own rate. `SEVENTH_NX_BGMOVIE_60=0` disables.

**las4_3: fill and edges.**
- New `ff7nx_bgmoviefield`: black layer-1 margin placeholders outside 4:3 in BGMOVIE fields are removed, so the movie shows through (las4_3 152 records, blackbge 384).
- `ff7nx_edgeclean` adds las4_3 with a smoothed mask (σ 1.6), with no Cosmos path (its page is fully opaque).

**Overlay-cutscene FMVs: not changed.** FFNx also ticks the field at the movie rate. Scripts and the 60fps-mod scalers are per-tick constants, so running logic at 60 would change pacing. A correct fix needs a logic/draw split (logic at the movie rate, models and camera interpolated at 60). That is a separate design.

**woa_3.**
- `ff7nx_floorclip` adds woa_3's bottom-right patch: 2 records, 1060 texels.
- The red arrow: `ff7nx_pointers` also hides gateways chained (shared endpoint, same destination) to a hidden one. 137 → 148 hidden; woa_3 {1, 2}.

**trnad_4 (new `ff7nx_ghostedge`).** Pale layer-1 rim texels outside layer 2's cut, and small pale specks near the rocks, are removed unless vanilla shows them: 2451 texels.

**Tests:**
- new: test_beamhd_whitein_smooth_beams_continue_to_edge, test_floorclip_woa_3_patch, test_pointers_618z_chained_exit_segments, test_ghostedge_trnad_4_only_near_rocks, tests/test_moviepace.py;
- margin test now checks continuity;
- 618: 73 passed, 2 skipped; moviepace + pointers: 21 passed; 617/581/guard/582/608: 55 passed, 4 skipped.

## 50. BUILD 618z (3rd round) — FMV pace, woa_2 band and floor, whitein centred

Hardware 10-05, video of woa_2 (1,798 frames at 60 fps, all read programmatically plus contact sheets; the band tracked per frame) and whitein screenshots. gldelev confirmed, so it leaves the debug warp.

**Background movie at double speed (`ff7nx_moviepace`, exefs).** The decoder is not wall-clock paced. It decodes the next frame as soon as the last one is consumed, so the field at 60 consumed 60 movie frames a second.
- The wait cave now consumes a ready frame only when it is due: at least one period (1/30 s, less 4 ms) after the last. The due time advances by exactly one period, and resyncs after a 2-period stall. Otherwise the stock not-ready path redraws the shown frame (`ldr w1,[x21,#0x14]; b +0x10F1660`).
- The clock is the field frame's start (guest 0xCFF8D8), so the 3x booster scales it too.
- 8 bytes of module BSS hold the due time (`AC.pack`, growth 8 + tail slack).
- Emulated: two seconds of 60 Hz frames consume 60 movie frames.
- No re-encode is needed.

**woa_2 green band (new `ff7nx_bgscr60`).** The band is layer 4: one 112-unit band on a 640-wide layer, moved by BGSCR.
- *Video:* the band moves 10 units a frame. After 40 frames a speed of -400 for one frame snaps it back 400 units; with the 640 wrap that reads as a 240-unit jump to the left margin. Cycle 41 frames.
- *Cause 1:* at 4:3 the snap happens off screen; at 16:9 (427 + 112 > 400) it does not.
- *Cause 2:* the port does not divide BGSCR at 60 (FFNx does, field.cpp 336-339), so every scripted layer scroll runs at 2x.
- *Fix, sweep fields (woa_2, woa_3):* the loop's snap-back becomes 0 and the travel speed is halved, so the band wraps once per 640 units (entering and leaving off screen) at its 1997 pace.
- *Fix, persistent fields:* every layer with one constant scroll speed and no stop is halved (truncating, as FFNx does): 36 fields, including hyou*, move_*, sky, trnad_1-4, woa_1-3, zcoal_1/2, fship_1/12, kuro_1 and ztruck. Layers that start or stop are left alone.
- `SEVENTH_NX_NO_BGSCR60=1` disables.
- The band covers the full height except a 16-unit vertical wrap gap (tiles 240 tall on a 256 layer), as in 1997.

**woa_2 floor (`ff7nx_floorclip`).**
- woa_2 added with dark 100.0 (the mossy rock is brighter).
- The floor's own page (26) is full, so the pass now takes any truecolor page with room (27): 4 records, 4,590 texels.
- The seam fix now edits the edge record's own page: 11 texels.

**whitein dirty pixels (`ff7nx_bandbake`).** The rows of dark specks in the ribbon's left loop were margin texels baked one by one: Cosmos's dark sky has scattered texels at black.
- Only solid holes are baked now (within 3 texels of a 7x7-opening core), and only inside 4:3.
- 25,328 → 24,631 texels; same software picture (max diff 8).

**whitein centred (new `ff7nx_wishift`).** The art is 448 wide (-192..256) against a 427 view, and Cosmos's range (-128..192) pins the view centre at 32. The city's centre is -6.
- Moving the city tiles alone would detach Cloud and the walkmesh, so the camera moves instead: range -166..154 (centre -6).
- The strip uncovered left of -192 is filled:
  - layer 1: the two edge cells mirrored, 27 cells (1x paletted ones on a paletted page);
  - beams: each beam crossing the edge is continued as a cone, using the margin code mirrored: states 1/2/4/8 get 2/11/6/12 cells on FX page 18.
- Nothing inside the old view changes.
- `SEVENTH_NX_NO_WI_SHIFT=1` disables.

**whitein truecolor beams: not done.** The pulse is MPPAL on palette entries, so it has to stay paletted. Truecolor would need one page set per brightness level (634 cells × N levels, against 2 free FX slots), or an engine change that draws HD tiles with a per-palette brightness factor as vertex colour. That is possible, but it is a separate exefs project.

**Tests:**
- new: test_bandbake_618z2_no_margin_specks, test_bgscr60_woa_2_band_keeps_travelling, test_floorclip_woa_2_floor_in_front, test_wishift_whitein_city_centred;
- moviepace rewritten (4 tests: stock movies, the clock, a 60 Hz sequence giving 30 fps, the gate);
- 618: 77 passed, 2 skipped; moviepace/pointers/617/581/guard/582/608: 78 passed, 4 skipped; worldfar 18 passed.

## 51. BUILD 618z (4th round) — whitein centring redone, BG movie clock

Hardware 10-05: woa_2 is perfect. las4_2 and las4_3 still play at 2x. whitein centred looks right, but:
- the lights moved on screen and new ones shoot off at other angles in the left strip;
- the black edge repeats as a column;
- there is an odd corner at the top left;
- the mirrored side shows bright fringes.

**whitein (`ff7nx_wishift`, redone).**
- **Beams.** Every param-1 record moves -38 with the camera, so the beams stay exactly where they were on screen. Their art then spans the new view exactly (-219.5..207.5). The cone continuation is removed. Verified: the beams-only render at the new camera equals the old one.
- **Black edge and fringe.** Layer 1's own left border is 12 units of black (-192..-180) followed by a 2-4 unit bright fringe. The v1 mirror about -192 copied both: the black column, the corner (the paletted top-left cell) and the light column.
- **Mirror axis.** The ocean is now mirrored about x = -176, one cell for one cell: -192 ← flip(-176), -208 ← flip(-160), -224 ← flip(-144). The old -192 record (border and fringe) is rewritten in place as the flipped copy.
- **Rows.** Only rows that have ocean at the edge are filled; the black regions stay black.
- **Ribbon.** The glass ribbon's black cut-out in the source cells is refilled from the nearest ocean texel before mirroring (no black ghost of the ribbon). The ribbon's own cut-out at -192 is kept under it.
- **Order.** The pass runs after beamhd and before the band bake, which needs the overlay still there.
- **Pages.** Copies stay off the FX pages (15..25). The v1 build put 26 base tiles on truecolor FX page 21, which fails the 608b gate (test_built_archive_has_no_base_tile_on_a_truecolor_fx_page fails on the current build until it is rebuilt). floorclip's fallback page now skips that band as well.

**BG movie still 2x (`ff7nx_moviepace`).** The clock-paced cave was installed (checked in the shipped main), so its clock was the fault. Guest 0xCFF8D8 is never named directly in the module: it was an inference from the fieldpace doc, and evidently it is not a per-frame clock in this mode.
- The clock is now `mrs cntpct_el0`, the 19.2 MHz generic timer that ff7nx_frameprobe reads on hardware. It is real time and independent of the field loop and of how often the update runs per frame.
- Rate: 30 (Cosmos FMV30's last4_2/3 are 39 frames over 1.3 s, the PC loop length). `SEVENTH_NX_BGMOVIE_FPS` overrides it for a hardware check.

**Debug warp:** prisila → woa_3 (next woa field: its band snap removed and its floor patch), jumin → trnad_1 (BGSCR halved; a field already seen on hardware).

**Tests:** wishift test rewritten (beam records exactly -38, layer 1 right of the axis shifted 1:1, strip drawn with no black or fringe column, 608b gate); moviepace on cntpct. 618: 77 passed, 2 skipped; moviepace/pointers 23 passed; 617/581/guard/582/608/worldfar: 72 passed, 4 skipped. The one failure is test_built_archive_has_no_base_tile_on_a_truecolor_fx_page, which checks the current build: the v1 whitein layout put tiles on FX page 21, and this round's code no longer does that.

### 51b. Voice acting lost in build 387 — moviepace's BSS ran too early

Log 387: `! Echo-S voice runtime: ValueError: Echo-S BSS needs +0x3FEF990..+0x3FF031F but the page the pre-existing BSS ended in stops at +0x3FEFFFF (800 byte(s) short)`, then `the dialogue runtime is not installed`. That means no voice line triggers.

**Cause.** 618z (2nd) made `ff7nx_moviepace` grow BSS by 8 + 0x328 (tail slack) = 0x330 bytes. It ran inside `apply_field_frame`, before `apply_echo_voice`. The voice block must end inside the page the old BSS ended in (a store past that page aborted on hardware in build 325). In 386 it ended at +0x3FEFFF4 with 12 bytes to spare; 0x330 earlier pushed it out.

**Fix.** moviepace is now its own stage, `build.apply_moviepace`, called from `7th_heaven_nx.py` right after `apply_pointers`: after voice and facial, before the audio pool. Its anchors are touched by no other pass. The voice layout is back to 386's. Nothing else differs between the 386 and 387 warning lists except the expected whitein camera range, which the widescreen verifier now accepts as wishift's intended shift (wanted + shift) instead of reporting `VERIFY FAILED`.

## 52. BUILD 618z (5th round) — las4 movie measured, whitein waits for painted art, las4_3 specks

Hardware 10-05 (build 388): trnad_1 and woa_3 fine. Voice acting is back: the dialogue runtime installs at +0x3FEF660 and moviepace's BSS now comes after it.

**las4_2 / las4_3 movie: measured on the video, correct.** 1,791 frames at 60 fps were read programmatically. In a movie-only crop the picture changes on every second frame (diffs 2-15, then ~0.0), so the movie updates 30 times a second. The loop repeats every 78 display frames (best match among 10..600): 39 movie frames in 1.30 s, the same loop length as PC (20 frames at 15 fps = 1.33 s). Models and FX change on the in-between frames too, so the field draws at 60. `SEVENTH_NX_BGMOVIE_FPS=15` halves the movie if wanted.

**whitein: painted strip (`ff7nx_wishift` v3).** The mirrored fill reflected layer 1's own painted light rays and the ribbon's shading (hardware: a curved texture tracing the staircase in the opposite direction, rays running the opposite way).
- The strip x -224..-176 is now painted art: `assets/whitein_left_fill.png` (`SEVENTH_NX_WI_FILL` overrides the path).
  - It is the whole layer-1 picture, plus layer 2's static art and the ribbon, at 3 px/unit (1440x1536, field x -224..256, y -256..256).
  - Exported by `python3 ff7nx_wishift.py export <dir>` (the files are in assets/whitein_paint).
  - Only the strip is read back. The ribbon tip's own texels keep the 1997 cut-out under the overlay.
  - A cell that stays black and had no record is left as the clear colour.
- Without the file nothing changes: no camera shift, so whitein keeps Cosmos's framing.
- The beams still move with the camera (same place on screen).

**las4_3 specks (new `ff7nx_specks`).** Two 1997 debris specks that Cosmos repainted as bright yellow-green blobs on opaque layer 2 (page 26) are made transparent:
- x 101, y -30: the whole content of cell (96,-32);
- x 61, y -20: a nub on the big rock's edge, in cell (48,-32).
Hardware preview note ("a little dirtiness to the edge"):
- floating fragments under 40 texels are dropped;
- the edge there is closed and opened with a 3x3 element, and added texels take the nearest rock colour.
That is 90 texels in total, exclusive cells only, and the pass refuses if the counts differ from those measured. `SEVENTH_NX_NO_SPECKS=1` disables it.

**Tests:**
- new: test_specks_las4_3;
- test_wishift_whitein_city_centred rewritten: no file → refused; a flat stand-in painting lands in the strip; the rest of layer 1 shifts 1:1; the 608b gate holds.
- 618: 78 passed, 2 skipped; moviepace/guard: 9 passed.

**Debug warp:** prisila → zcoal_1, jumin → hyou2 (BGSCR halved: the train tunnel and the snowfall).

### 52b. whitein: the painted strip is in (assets/whitein_left_fill.png)

The user's ChatGPT painting was 1215x1295, the export's aspect (0.9375) scaled by 0.84.

**Not 1:1.** ECC affine registration against the export (fitted on x ≥ 150 px) gives x scale 1.0066, shear -0.0028, offset (-2.6, +1.2) px, correlation 0.994. After alignment the unpainted area still differs by a mean 6/255: ChatGPT re-rendered it, softer, with slightly shifted tones.

**What is used.** Only the strip, x < 144 px (field x < -176). Everything else stays the original HD art. That is sharper, and it is what the ribbon and the models are aligned to. `assets/whitein_paint/prep_fill.py` builds the file:
- registration;
- per-row tone match at the seam, ramped in over 48 px;
- the original's fine grain added (high-pass of the mirrored neighbour, σ 1.5, strong edges such as the ribbon outline removed, clipped ±6). The painting's column gradient was 1.07 against the original's 3.39 (result 2.42), and a straight copy of the mirrored detail showed a ghost of the ribbon.

**Rendered.** No seam (column gradient at -176: 1.4 against ~2.5 either side); the light rays continue at their own angle; no ghost ribbon; 44 strip cells; beams unchanged on screen. The previews were sent to the user (top and city views, with and without beams).

**Tests:** test_wishift_painted_strip_asset (the shipped file loads at 1440x1536 and lands).

**52c. Seam polish (user, 10-05: a faint vertical line where a cyan band met the join, the band offset across it).** prep_fill.py step 5: the last 40 px of the strip cross-fade (cosine ramp) into the original mirrored about the join (reflected from column 145, so the join column is not doubled). The join is then continuous by construction, and the mirrored part is too shallow to read as a reflection. The column gradient across the join is now flat (2.2-2.3 on every column 120..140; before, 0.9 against 2.2 with a step). The asset was re-committed after a stale copy was found on the device (checked by pixel statistics: jump at the join 0.56 against 4.3 between neighbouring columns).

**52d. The reflected ribbon (user, 10-05: "why am i seeing a repeat of the bridge here?").** Caused by 52c. Its 40 px seam fade mirrored the original at x 145..289, and the glass ribbon crosses that band (rows 316..637, tip at x 130). So the strip got a mirror image of the ribbon's edge, which formed an X with the real tip, plus a pale column beside it. The 52b grain step also read the mirrored neighbour. prep_fill.py now copies nothing from the original into the strip:
- **Step 3.** ChatGPT's own ribbon edge sits a few px off ours. Only where the painting is clearly brighter than an ocean estimate inside a 13 px ring around the ribbon (2,359 px) is it inpainted (Telea).
- **Step 4.** Grain comes from the same rows further right. It is translated, never mirrored, and taken only from pixels at least 15 px from the ribbon and 3 px from any strong edge.
- **Step 5.** A Poisson join replaces the mirror fade. The strip keeps the painting's gradients, the original column x = 144 is the boundary value, and the ribbon is a free (Neumann) boundary. Rows that are near-black on both sides (≤ 24) are not tied either, so the 8/255 opaque-black cells don't bleed in as a grey blob.

Measured: the step across the join averages 1.84 against 1.47 between the original's own neighbouring columns. Bright-lavender pixels in the strip outside the ribbon mask: 0, against 101 in the 52c file. That leaves 36 non-black strip cells. The 52c file had 44, and the 8 extra were mirrored near-black squares under the ocean. The ribbon mask is now shipped as assets/whitein_paint/whitein_ribbon_mask.png.

**Tests:** new test_wishift_strip_has_no_reflected_ribbon (fails on the 52c file). wishift tests: 3 passed.

**52e. Lines across the central spire (user, 10-05: "weird horizontal lines where these texture regions meet ... they get thinner or thicker depending on where i am on the screen").** Measured on the screenshot, which matches the field picture 1:1 at offset (16, 300): both lines are the last pixel row of a tile, at picture rows 671 and 719 (field y = -32 and -16 tile edges). They are 20-35/255 darker than the art; the art itself has no line there. Cause: `ff7nx_bandbake` only baked layer-1 cells on truecolor pages. Under the spire at y = -32, layer 1 is a 1x black/clear filler cell on paletted page 1, shared by three records. That row stayed additive overlay over black while the rows above and below were baked. Baked art meeting additive art on a tile edge is the filtered seam the bake exists to remove. The overlay's keyed edge pulls in by half a texel, so the line's width follows the camera's sub-pixel position. The all-or-nothing patch rule missed it because the filler row was never in the hole map.

Fix (`_promote_fillers`, runs before the bake). A layer-1 record on a paletted page qualifies when its cell is all black or clear, it is inside 4:3, and it is under a static additive truecolor overlay. Each such record gets its own cell on a truecolor page outside the 608b FX band: clear stays 0x0000 and black becomes 0x0841. The bake then treats the row like any other hole. In whitein, 4 records are promoted. Baked texels go from 24,631 to 28,136, the spire's y = -32 overlay is fully baked (0 texels left), and the software render is identical to the 52d build (max diff 0; ≤ 8 against the unbaked field). Before/after map: Claude outputs/whitein_spire_bake_before_after.png (red = overlay still additive).

**Tests:** new test_bandbake_618z5_spire_filler_row_baked. test_build618: 81 passed, 2 skipped (lz4 had to be installed on the test machine for two tests that import nxmap).

## 53. BUILD 618z (6th round) — the las4 movie loop; FMVs at 60

**Hardware 10-05 (build 389).** zcoal_1/2/3 and hyou2 are fine. The user suspected that las4_2/las4_3's movie runs at 2x.

**Measured on the video (1,337 frames at 60).** In las4_3 with the camera still, the background changes on every second captured frame, so 30 times a second. Its autocorrelation minimum is at exactly 78 frames (1.30 s), with the next at 156. las4_2 is the same. Reference: the port's own last4_2/3 are 20 frames, and every other movie on the cartridge is 15 fps, so the original loop is 20/15 = 1.333 s. The Switch file itself is tagged 30 fps, which plays the loop in 0.667 s. So the build plays the original pace, not 2x. Comparison clip: Claude outputs/las4_2_movie_speed_compare.mp4.

**Real defect found: the loop's wrap.** Cosmos FMV30 is 2N-1 = 39 frames: vanilla frame k is Cosmos frame 2k, with an interpolated frame between each pair, except across the wrap. The wrap is a double step: 4.8 against ~2.5 per frame in the file, and a 15.2 spike against a median of 8.0 every 78 frames in the capture. `movies.loop_close` adds the mean of the last and first frames (in the source's own yuv420p10le, so no colour shift; an RGB round trip moved R/B by 1.6). Result: 40 frames, 1.333 s at 30 fps, wrap steps 2.5/2.9 against a median of 2.9. Only last4_2/last4_3, and only for a 39-frame 30 fps source. The cache key gets `|loopclose1`, so no other movie re-encodes. `SEVENTH_NX_NO_LOOP_CLOSE=1` disables it. Test: test_moviepace::test_loop_close_618z6.

**FMVs at 60 (user request): assessment, not implemented.**
- Every *playable* movie field (BGMOVIE_flag: las4_2, las4_3, and any other BGMOVIE field) already runs at 60 with the movie at its own rate (ff7nx_moviepace).
- Cutscene FMVs with composited models are different. In vanilla the field ticks once per movie frame (15/s), and the cutscene scripts are authored against that clock. The 60 FPS set scales every per-tick clock by 2 (WAIT ×2, movement, animation, MVIEF /2, moviecam ×2 records), which is exactly right at today's 30 movie ticks a second. Ticking the field at 60 during a movie, whether by a 60 fps movie set or by not waiting for the decoder, makes all those clocks 4× vanilla's movie clock. Every composited model would then move and cue twice too fast against the picture. Doubling the movie files (the simple idea) therefore breaks the cutscenes, besides doubling the storage.
- The only correct route is a logic/draw split: the field logic stays at 30 during a movie, and a 60 Hz in-between frame redraws the models at interpolated positions and animation frames. That is a new renderer-side subsystem (per-model previous/current transforms, interpolation in the field draw, hooks at the logic gate), not a cave tweak.
- The camera should NOT go to 60. The camera that projects the models must match the picture it is composited over, and the picture changes 30 times a second. A 60 Hz camera over a 30 Hz picture makes models swim half a step against the background (the BUILD 522 problem in reverse). So the visible gain is limited to the models' own movement, while the camera (most of their on-screen motion in a cutscene) stays locked to the video.
- Recommendation: not worth the risk until a specific cutscene shows model stepping that bothers the user; then scope the split to that case with a hardware probe first.

## 54. BUILD 618z (7th round) — las4 timing measured, whitein ring, psdun_4 glow edge, las0_2 HD steam

**Hardware 10-05 (build 390).** whitein looks good except a dirty spot on the ribbon. psdun_4: the glow's edges against the rock are jagged. las0_2: the steam is blocky, has missing pieces, and breaks at the 4:3 edges. las4: is the movie really 30 fps with the field at 60?

**las4_2 / las4_3, answered by identifying every captured frame.** The background was matched against the movie's own 40 frames (correlation 0.997-0.999, runner-up 0.92-0.99).
- Every picture is held for exactly two 60 Hz frames, and frames 0..38 play in order. So the movie runs at 30 fps and the field and models at 60.
- Frame 39, the wrap frame added in §53, is never shown. The engine restarts the loop after decoded frame 38 whatever the file's length (the 39-frame file also showed 39 pictures per loop).
- 618z7 fix: the 39 frames are re-timed to cover one whole cycle evenly. Frame j is the picture at t = j·40/39 along the closed cycle, made by optical-flow interpolation (DIS on luma, warped in yuv420p10le).
- Wrap step: last4_2 4.9 → 2.7, last4_3 10.6 → 6.3. Sharpness unchanged.
- The frames are made offline (`assets/movies/retime_loop.py`) and shipped as lossless assets `assets/movies/last4_{2,3}.loop39.mkv`, used only when the source is the exact Cosmos file (SHA-1). Cache tag `loopclose2`. Test: test_moviepace::test_loop_close_618z7.

**whitein: the dotted ring** (x -160..-156, y -86..-82). The 4:3 cut in `ff7nx_bandbake` split one piece of overlay-on-black: 115 texels inside 4:3 were baked and 357 outside were not. The baked island's outline became the filtered seam the bake exists to remove. A baked patch whose piece continues unbaked outside 4:3 is now left additive (here, just that one patch). Test: test_bandbake_618z7_no_island_split_by_43_cut.

**psdun_4: the glow's edge** (new `ff7nx_fxcut`).
- The glow is 876 additive 1x records: 6 states, paletted pages 15-18, palette brightness driven by the script (ADPAL on var5[6]; the hardware shot matches about -4 to -5 in 5-bit units).
- The user chose to keep it animated, so it stays 1x. The rock's HD silhouette is found near the glow's 1x outline (smoothed outline, plus a colour decision only where decisive). The glow is grown and brightened up to it, and the rock is redrawn over the glow on layer 2: 52 cells on page 14, depth just in front of the glow.
- The rock's outer ring is anti-aliased (up to 60% of the glow it covers), and the silhouette is grown by one HD pixel so no dark gaps remain.
- First version (user: "very rough"): pure colour decisions wobbled on the dark cliffs, and the edge was a hard key. Both are fixed.
- Not fixed: brightness steps inside the glow (the pillar, the leaf). They are the 1x glow's own shading and need true colour, which freezes the brightness script. Cosmos's DDS for pages 15-18 fails the art check, so it would have to be drawn. A "dim glow = rock" variant left static/live seams along 1x texel lines and was not shipped (`_scratch/r7/fxcut.v3_dim.py`).
- Test: test_fxcut_618z7_psdun_4_glow_hd_edge.

**las0_2: HD steam** (`ff7nx_fxtrial`, user: "we want to use all the hd steam effects ... 35 isnt an exact limit").
- The steam's 16:9 margin (718 records, paletted pages 21-23) had three problems:
  - it was 1x;
  - the 4:3 seam was up to 107/255 off;
  - 190 cells were blank where the Cosmos art has steam (the vertical cut at x = 192 and the grey block at the top right).
- Cosmos's DDS for those pages has the whole effect, and the normal fxpages path converts all three. Seam ≤ 21/255 on every frame, and no blank cells.
- The right column fades out over x 196..208 instead of ending in a line.
- Cost: texture memory 35.0 → 44.19 MB (per-field TRIALS = 45). The field decompresses to 15.58 MB, past the 12.8 MB raw cap, so las0_2 gets its own raw ceiling. The field decompression buffer grows 16 → 32 MB: build.py raises FIELD_BG_MAX_RAW from fxtrial's `max_raw`, and the log line "fx trial: largest field is now ..." shows it.
- Risks:
  - Black squares in las0_2 if 44 MB is too much for the console.
  - The extra 16 MB of guest heap for the buffer, which applies to the whole game.
  - `SEVENTH_NX_NO_FX_TRIAL_LAS0_2=1` puts both back.
- Test: test_fxtrial_618z7_las0_2_hd_steam.

**Tests:** test_build618: 84 passed, 2 skipped; test_moviepace: 5 passed.

## 55. BUILD 618z (8th round) — psdun_4 glow in true colour, las0_2 steam polish, no shipped art or video

**Hardware 10-05 (build 391).**
- whitein: "looks good enough".
- psdun_4: "very rough choppy edges", and the glow makes the rocks under it look blocky. "Can that be true color and animated safely? ... i dont want to lose frames".
- las0_2: the steam is "lifted off" the rock with a flat edge, a jet creeps over the rock looking "dirty", and there is a mirrored half at the right 4:3 edge.
- las4 / whitein: "i dont want to be distributing my own video assets ... im not trying to distribute artwork i dont have rights to".

**psdun_4: the glow redrawn in true colour, frames kept** (new `ff7nx_glowhd`, replaces `ff7nx_fxcut` for this field).
- All 876 glow records keep their page, cell, param and state, so the 6-state animation is untouched. Only pages 15-18 change, from paletted to truecolor 768 (additive blend is wired for truecolor slots 15..23).
- Each state's glow is put together at 1x across the whole field (so there are no cell seams), turned to RGB through its palette, then upscaled 3x with a smooth bicubic filter. The light's gradients and its inner bright/dim edges (the pillar, the leaf) are smooth instead of 3-pixel steps.
- The outer edge is extended 2 texels before the upscale, then cut along the rock's HD silhouette (`ff7nx_fxcut.rock_mask`) with a 0.8 px soft edge. The light ends at the rock, not in a staircase over it, which is what made the rock corners look blocky.
- Cost 1, the brightness drift: the palette no longer drives this glow. The script slowly dims it through ADPAL (measured -2 and later -6 in 5-bit units on hardware); the true colour glow keeps one level, -4, the middle of the two.
- Cost 2, memory: texture memory goes 15.4 → 27.6 MB, and the field is 9.66 MB raw (under the 12.8 MB cap).
- Checked in renders against psdun4_shot.png: colour and brightness match, the edges are smooth, and the frame-to-frame difference between states is the same as before (20.56 against 20.60).
- `SEVENTH_NX_NO_GLOW_HD=1` brings back the 1x glow with 618z7's fxcut edge. fxcut runs only when glowhd did not.
- Test: test_glowhd_618z8_psdun_4_truecolor_glow_keeps_frames.

**las0_2: steam polish** (`ff7nx_fxtrial`, after the HD conversion and edge fade).
- *Mirror* (`_las0_2_polish`, 1): in 8 plume frames, Cosmos's art right of x = 160 is an exact mirror of the 4:3 art (error 2.7/255 against 20-50 elsewhere). Beyond a 3-unit seam band those cells are rebuilt from the mirror squeezed to 0.6, so the join is unchanged but the right half is a different, narrower shape. The reflection's far side at x 176/192 is cleared.
- *Dirty creep* (3): the low jet's faint haze over the dark rock (box x -144..-64, y -64..-32, 169 cells) is cleared below 16/255 and fades in up to 64/255.
- *Flat edge* (`_las0_2_bottom`): the left margin's steam had no records below y = -160, so it stopped in a straight line above the boulder.
  - The boulder's top edge is found in layer 1 (y -163.5 .. -147.4).
  - The last row is cut softly at that edge.
  - A new row of 20 records at y = -160 carries the steam's last rows smeared down to the edge, on free cells of truecolor FX page 23 (binds ≤ 256).
- Test: test_fxtrial_618z8_las0_2_polish_and_bottom.

**las4_2 / las4_3: the re-timed loop is made at build time** (`movies.loop_close`). No video is shipped any more, and `assets/movies` is gone.
- The same 39-frame re-time (t = j·40/39 over the closed cycle) is made from the user's own Cosmos file with ffmpeg alone. The first two frames are appended, the timestamps put the wrap as the one double step, and `minterpolate` (mci/aobmc/bidir) samples at 39 fps on a 40/s clock. The first 39 frames are kept at 30 fps, ffv1 10-bit.
- Against 618z7's optical-flow frames: PSNR 49-56 dB (last4_2) and 36-56 dB (last4_3). Wrap step 10.9 / 23.8 against 10.6 / 25.3, with the loop's median at 9.7 / 22.7.
- The result is cached in the movie cache (`<stem>.loopclose3.<sha>.mkv`) and only made for the measured Cosmos file (SHA-1). The cache tag `loopclose3` forces the two movies to re-encode once.
- Test: test_moviepace::test_loop_close_618z8_built_not_shipped.

**whitein: only the user's own strip is shipped.**
- The asset was the whole 1440×1536 picture, Cosmos's art included. It is now `assets/whitein_left_strip.png`: 144×1536, the painted strip alone.
- The ribbon's 961 pixels in it are black, because ff7nx_wishift takes the game's own 1997 texels there at build time.
- Build output byte-for-byte identical to 618z7 (section 7 and section 9 compared).
- prep_fill.py now writes the strip. Its inputs (the painting and the exported reference) are authoring files on the user's machine and are not in assets. They were moved to `_scratch/r8/whitein_authoring`.
- Test: test_wishift_618z8_ships_only_own_strip. It also checks that assets/ holds no full picture and no movies.

**Tests:** test_build618: 87 passed, 2 skipped; test_moviepace: 5 passed; test_build617: 32 passed, 4 skipped.

## 56. BUILD 618z (9th round) — las0_2 steam frame by frame, psdun_4 glow edge as a curve

**Hardware 10-05 (build 392).**
- whitein: fine.
- las4_2 / las4_3: OK.
- psdun_4: "much better", but the edges should be cleaner, "with no overlap of the edge and the fx layer". During the round the user added: "this edge should be a smooth curve", "there is what looks like a thorn on it".
- las0_2:
  - the base of the left plume "randomly drifts up";
  - a "ghost trail (or 2nd tail)";
  - a cone shape that "shouldnt even be there";
  - smoke creeping over the rock.

**las0_2** (`ff7nx_fxtrial._las0_2_frames`, after polish and bottom). The 21 steam frames (param 1 ×8, param 2 ×8, param 3 ×5) were identified on the hardware shots: the good shot is 2/1, the worst 3/1, the cone 1/2.
- *Left margin* (Cosmos's 16:9 art): its lower half changes shape every frame (mean |frame − median| 6-21/255). Inside 4:3 the frames follow the 1997 art.
  - The lower margin (weight 0 above y −212, 1 below −188; 0 at the 4:3 seam, 1 from x −176) is pulled to the per-texel median of all 21 frames.
  - The base no longer moves, and the stray second tail is gone. The top still animates.
- *Boulder band*: from x −224 to −112, a band from 10 units above to 12 below the boulder's top edge (found in layer 1) is also pulled to the median, soft over 8 units. The steam meets the rock the same way in every frame.
  - Cutting the steam at the rock was tried first. It gave a hard, unnatural silhouette (the 1997 steam does drape over the boulder), so it was not used.
  - Measured: variation in the margin's base band drops by more than 80%.
- *The cone*: frames 1/1, 1/2 and 1/4 are the right puff dying away as a narrow jet centred on the 4:3 edge. Cosmos completed it by mirroring, hence the symmetric teardrop.
  - In those frames the right plume (x ≥ 96, y < −150, soft edges) is now the last big puff (3/16) at 40%, 18% and 6%, so the puff dissolves.
- *Haze over the dark rock*: the low jet's HAZE_BOX now starts at y −80 (was −64). The hardware crop showed haze above −64.
- Records: 225 all-black padding records dropped (they draw nothing); 43 added where a frame needed steam it had no record for. No new pages, no memory change.
- Test: test_fxtrial_618z9_las0_2_frames.

**psdun_4** (`ff7nx_glowhd.rock_alpha`).
- *Where the edge goes*: rock_mask decides in whole pixels from the smoothed 1x outline, and it is off by 1-2 px.
  - Within 3.5 px of it, each pixel is unmixed against the local rock and background colours (means over the sure parts nearby). A dark outline darker than both is treated as background where the rock is the brighter side.
  - Each decision becomes a smooth curve: the colour one is blurred 2 px, the outline-only one 7 px (stair steps and thorns go). Then both are re-sharpened to a ~1.5 px anti-aliased edge.
- *Dotted line*: the 1997 glow's outermost texels are dimmer, and upscaled they made a dotted dark line along the platform. Within 2 texels of the edge, a texel under 0.8 × its nearest inner texel takes that texel's colour. This is used only where the colours say the pixel is surely background and the rock is the brighter side; elsewhere it put glow onto the dark cliffs.
- *Result*:
  - the dotted line under the spire is gone;
  - the platform edge is a clean line;
  - the cyan sliver between the left cliffs has a smooth curved edge with no thorn;
  - there is no glow deeper than 6 px into the rock in any state.
- Test: test_glowhd_618z8 (overlap and anti-aliased ring checks added).

**Next probes.**
- blackbge is the only other field whose script runs a background movie (BGMOVIE 1, movie 23). The full scan of flevel found las4_2, las4_3 and blackbge. It replaces las4_2.
- seto1 replaces whitein.

**Tests:** test_build618: 88 passed, 2 skipped; test_build617 + test_moviepace: 37 passed, 4 skipped.

## 57. BUILD 618z (10th round) — las0_2: two frames at once, the fill triangle, grain

**Hardware 10-06 (build 393).**
- seto1: "perfect".
- psdun_4: done.
- blackbge: a debug field (black screen, green side bands), never entered in play. It is dropped from the probes.
- las0_2 still needs:
  - a lingering steam trail at the top, against the right 4:3 edge, ending in a straight line;
  - a sharp point where the steam cuts into the boulder;
  - "a weird out of place triangle of white that is always sitting there" (left margin, x -213..-190, y -166..-148);
  - grain and banding.

**The script shows two frames at once.** The steam loop is BGON n+1 / WAIT 3 / BGOFF n-1, so the screen always shows frame n plus frame n+1. Every hardware shot matches such a pair better than any single frame. This explains why the hardware is brighter than single-frame renders, and the "second tail" wherever two neighbouring Cosmos frames disagree. All checks now render pairs.

**Fixes (`_las0_2_frames`).**
- **Triangle**: it was 618z8's `_las0_2_bottom` fill, smeared down to the boulder and identical in every frame. It is no longer used.
  - The first replacement, a fall-off above y -160, read as an indent (user, 10-06).
  - Now the margin's steam continues only a short way below -160, fading over 4 units and stopping at the rock with a soft 4-unit edge.
- **Top trail**: at the right 4:3 edge (y < -150), wherever the margin column is under half the 4:3 side, the 4:3 side fades over 32 units down to the margin's level at the edge.
- **Sharp point**: after the boulder band is pulled to the median, it is blurred by 3 units. The blur may only darken, so it rounds off hard bright points without spreading a haze onto the rock.
- **Grain and banding**:
  - Every frame is smoothed: 1.6 HD px, and 4 px where the steam is faint (under 10 in 5-bit units), since the thin haze is where the grain showed. It is kept to where the frame already has steam.
  - No dither under 1.5, so there are no lone specks on the dark rock.
  - Each frame is then written with a 4×4 ordered dither in field coordinates, its pattern inverted on alternate frames. The two frames on screen carry complementary rounding, so their sum is about one bit finer than two equal steps.
- **Records**: blur spill is kept to cells some frame already uses. 208 blank records are dropped and 16 added (the cone frames). No memory change.
- **Test**: test_fxtrial_618z9_las0_2_frames, rewritten.

**Tests:** test_build618: 88 passed, 2 skipped.

## 58. BUILD 618z10 — mds7pb_1: the user's own retouch (ff7nx_artpatch)

**Hardware 10-06.**
- Every probed field is fine: las0_2, nvdun31, gaiin_2, kuro_9.
- mds7pb_1 (Seventh Heaven): "the L shaped edge always looked out of place, and the texture creates a peaking triangular edge along the side of the wood". This is the bright L right of the TV and the shelf edge below it, in layer 1.

**Mechanism (new `ff7nx_artpatch`, general).**
- **Export**: `ff7nx_artpatch.py export <field> out.png [flevel]` exports a layer-1 region at 3 px per unit, from the user's own build. For mds7pb_1 that is x -224..-96, y -112..16 (384×384).
- **Make**: the user paints, and `make` keeps only the texels that changed. Those are differences above 6/255, plus any softer change within 4 px of one (the brush's feathered edge), and only where the 16-bit result actually differs.
- **What is shipped is a patch, not a picture.** `assets/artpatch/<field>.npz` holds three things, like an IPS/xdelta ROM patch:
  - the mask of those texels;
  - the XOR of the installed 16-bit texel with the user's;
  - a SHA-1 of the installed texels under the mask.
  The delta is noise without the original art; viewed as an image it is coloured static. No picture is shipped, neither Cosmos's nor the user's.
- **At build time** (after ghostedge):
  - The patch is applied only if the installed texels hash identically. The result is the user's edit bit for bit, or nothing (logged).
  - A cell shared with another place is first copied to a free truecolor cell (not on the FX band).
- `SEVENTH_NX_NO_ART_PATCH=1` disables it.

**mds7pb_1.**
- 2318 texels in 6 cells (5.1 KB). Patched texels equal the user's edit exactly, and all others are untouched.
- In a render at the hardware camera (-35, -17), exactly those pixels change.
- Test: test_artpatch_618z10_only_painted_pixels.

## 59. BUILD 618z11 — ztruck zoomed like FFNx, its scroll made whole; per-scene models (no sword on Cloud's back, Zack wears it)

**Hardware 10-06.**
- mds7pb_1, kuro_7, mtcrl_9 and all of fship: fine. (mds7pb_1 was seen without the retouch: ff7nx_artpatch and its asset were not in the user's folder. They ship with this round.)
- ztruck: "the scrolling artwork is all screwed up, not filling the screen, textures are popping into view"; "the field is bouncing slightly, revealing a black region on the top"; "a black region below the truck".
- midgal and ztruck: Cloud's dynamic weapon must not be on his back (he receives the sword in midgal). Later: in ztruck Zack should carry the Buster Sword on his back; in midgal it is in his hand, not on his back.

**ztruck — what was wrong.**
- Layers 3 (sky, far desert) and 4 (near desert) are 11 columns of 32-unit tiles, a 352-unit period scrolled by BGSCR (speeds 5 / 50 after bgscr60). The engine wraps each tile once at the header width (352), so a 427-unit view always has a 75-unit empty stretch sliding across it. The generic wide fill had added copies at x -592/-560/528/560 that fold through the wrap onto other columns (the popping).
- FFNx shows ztruck with **WM_ZOOM**: Cosmos's config has `mode = 2, left -175, right 175`, so 350 units across (inside the 352 of art), and FFNx widens the camera range to y -142..142 for the shorter zoomed view. The port did not zoom, but had the widened range: the camera rested at y +22, 23 units below the art.
- Scan of all fields (`_scratch/r13/scan2.py`): ztruck is the only scrolling layer narrower than 427 not already fixed, the only field whose widened camera range shows empty art, and the only mode-2 (zoom) field. tin_1..3 (mode 3) and utapb (mode 4) are the only other non-default modes.

**Fix.**
- `ff7nx_fieldzoom`: the zoom factor is per field. The BSS flag now holds the factor's float bits (0 = off) and the draw hook multiplies by it. ztruck: 426.67 / 350 = 1.219 (FFNx's 350-unit window); convil_2 keeps 4/3. `SEVENTH_NX_FIELD_ZOOM=name:factor` overrides. A debug-warp door showing a zoomed field is zoomed too. Default list: convil_2, ztruck.
- New `ff7nx_hrepeat` (flevel, after specks, before the debug warp): layers 3/4 keep exactly their authored 11 columns per row, the same records are added one period to the right, and the header width becomes 704. The picture repeats every 352 units, so nothing changes visually; nothing folds and nothing is missing. Refused unless every row holds the 11 columns, 32×32 and unanimated. The camera y range becomes -104..136 (240 tall, so it is pinned, as vanilla's -120..120 is): y +16, the zoomed view -82..114, and the 4-unit shake stays inside the art (bottom-left ends at 120).
- Measured with framesim over every scroll position of a full period: zoom alone leaves 16–24 % undrawn; zoom + hrepeat leaves nothing for camera y -20..20.
- `SEVENTH_NX_NO_HREPEAT=1` disables.

**Per-scene models (new `ff7nx_scenemodel`).**
- Ninostyle Chibi Fixes gates its folders on the live FieldID / PPV: `Dynamic Weapons\Cloud\Field` (AAAA.HRC with the extra chest part AAAD1 = the equipped weapon on the back) is off in FieldID 782 = midgal; `Dynamic Weapons\Zack\Field` (EJDC.HRC without EJDF1, the Buster Sword on Zack's back) is on only at PPV 1185..1186. A static archive cannot read either.
- Done per field at build time instead. The model loader's HRC name is pointed at a variant built from the user's own char.lgp, with the same skeleton and bone count (every animation still fits) and only one bone's parts list changed:
  - ztruck: AAAA → ZNAA (chest without AAAD1); IBAD (sitting Zack) → ZNIB (chest + EJDF1, Ninostyle's field Buster Sword as EJDC wears it).
  - midgal: AAAA → ZNAA; BHFF → ZNBH (r_hand BHJB1 → ZNBS.rsd, naming BHJC1.P, the static Buster Sword; BHJB1 asks for Z00C.., which the runtime turns into the equipped weapon); EJDC → ZNEJ (chest without EJDF1).
- char.lgp: the five entries are added to the finished archive after the archive stage, so it also works with `SEVENTH_NX_REUSE_CHAR=1`. The pass is idempotent and re-signs the reuse record. flevel: the 20 eye textures of IBAD/EJDC are copied to the new names, because the facial runtime looks up `eye_<hrc>_<n>`.
- `SEVENTH_NX_NO_SCENE_MODEL=1` disables.

**Tests.**
- test_fieldzoom: 8 passed, including per-field factors run in the emulator.
- test_build618: 93, including test_hrepeat_618z11_ztruck_scroll_fills_the_zoomed_view and test_scenemodel_618z11_variants_and_loader.
- test_build617 + test_moviepace: 37 passed, 4 skipped.
- reuse / lgp_add / facial / model_remap / build_guard / flevel_reuse_record: 126 passed, 2 skipped.
- apply_to_char was run twice on a copy of the built char.lgp: 5 entries added, then none; the archive round-trips and its lookup table is consistent.

## 60. BUILD 618z12 — ztruck edges, the mds7pb_1 blue specks, Zack's sword in midgal

**Hardware 10-06 (618z11).**
- ztruck: the scroll and camera fixed, and the swords right in both scenes. zmind2 fine, nvmkin1 "perfect".
- Still open:
  - ztruck: the scrolling layer's edges "super super jagged/rough", the truck's edges staircase-like, and a hole in a rock.
  - mds7pb_1: the retouch "creating all this blue artifacts".
- Request: in midgal, Zack carries the sword on his back until he runs off the bottom of the screen, and comes back with it in his hand.

**mds7pb_1, blue specks.**
- Cause: `ff7nx_artpatch._enc` rounded green to 6 bits. The port converts depth-2 texels with `((in & 0x07E0) >> 1)`, so an odd green lands on blue's top bit (+16/31 blue). That hit 25 % of the 2318 patched texels; the installed art has 0 % (field_bg_native, "THE GREEN LSB MUST BE ZERO").
- Fix:
  - `_enc` now writes green as 5 bits shifted left (low bit clear), rounded onto the level×8 grid like `rgb_to_565`; 0 becomes 0x0001.
  - `_rgb` decodes the way the engine shows a texel.
  - `plan_field` refuses a patch that would write an odd green or a 0.
- The asset was re-made from the user's edited image: 2265 texels, 6 cells, no odd green in the region. The error against the user's image is at most 4/255 under the mask (5-bit quantisation).

**ztruck, edges (new `ff7nx_edgesmooth`, after hrepeat).**
- Cause: depth-2 texels have no alpha. Cosmos's cut-outs keep the 1x transparency: only 42 % of layer 4's edge blocks are partial; the rest are whole 3×3 steps. On top of that, the rocks have a dark matte rim. The truck's cut is already texel-smooth; its stepped dark rim is what reads as a staircase.
- The hole is the layer-4 period seam. The art's first 2 texel columns are transparent in ~25 rows where the last column is rock, so a 2-texel sky slit appears at every wrap.
- Per layer, one canvas of the static truecolor records (layer 4 over exactly its 352 period, processed wrapped):
  - The seam gap (≤6 texels, art on both sides, within 8 of the seam) is filled.
  - The opacity is blurred (gaussian σ 2 texels) and cut at 0.5.
  - Added texels take the nearest original texel's colour; removed texels become 0.
  - Rim texels (≤2 from the edge) darker than a ratio of the texel 4 inside take that colour: 0.55 on layer 4, 0.9 on the truck (layer 2).
  - Layer 2's stacked tiles (the cab) are composited by z: a removed texel is cleared in the whole stack, and an added one goes to the depth that supplied its colour.
  - Nothing changes where layer 1 is behind, or in a cell another record uses.
  - Every written texel is an existing one: green stays even and no texel becomes 0.
- Layer 4: 434 texels added, 355 removed, 98 seam texels, 553 fringe texels, 22 records changed. Layer 2: 146 added, 61 removed, 2227 fringe texels, 64 records changed. Edge blocks that are partial: 301 → 507 of ~700.
- Still binary: an edge against a scrolling layer cannot be pre-blended. Steps go from 3 texels to 1. `SEVENTH_NX_NO_EDGE_SMOOTH=1` disables.

**midgal, Zack's sword.** The script:
- `zax` (char 3 = EJDC) carries Cloud, sets him down and moves off the bottom of the screen to (-82, -390), then goes invisible.
- `zaxs` (char 2 = IBGD) appears at that same point with the sword in his hand.
- So EJDC is no longer swapped in midgal: it keeps EJDF1 on its back. IBGD has no back sword.
- `ZNEJ` was removed. The old znej.hrc in a kept char.lgp is unused.
- Other Zack scenes (EJDC in the Nibelheim flashback, nvmkin22, sininb34, trackin2; IBGD in sininb34) keep Ninostyle's general models. Ninostyle's only other Zack switch is the PPV 1185..1186 one (this flashback).

**Tests.**
- test_build618: 94, including test_edgesmooth_618z12_ztruck_curves_seam_and_green. That test checks: partial edge blocks more than 1.5×; the seam closed; changes only within 6 texels of an edge; every new texel an existing value with green even.
- test_fieldzoom, test_build617, test_moviepace: pass.

## 61. BUILD 618z13 — ztruck: the truck anti-aliased

**Hardware 10-06 (618z12).**
- Confirmed fine: mds7pb_1 ("the tv ... looks perfect now"), midgal ("exactly what i wanted"), bugin2 and Cosmo Canyon, tin_1.
- ztruck "good" apart from the truck: "a little bit of anti aliasing ... on those diagonal edges", and the door outline as a smooth line.

**What limits a real anti-aliased edge.**
- A depth-2 texel is a colour or 0; there is no alpha.
- The port blends depth-2 pages only additively, and only in slots 15..23 (FINDINGS-194 ladder). Every other slot is opaque.
- An average-blended edge ring would need a new depth-2 average slot in exefs. Not done.

**What is done (ff7nx_edgesmooth, ztruck layer 2).**
- **The ring** (revised the same day after the first renders still showed steps).
  - The camera is pinned (y +16, x 0), so the truck sits still on screen while layers 3/4 scroll behind it.
  - `background_mean` renders each of layers 3/4 (framesim, the pinned camera) at 32 offsets over the 704 period. The mean is cov4 · mean4 + (1 − cov4) · mean3, valid only where a layer always draws.
  - The silhouette's alpha is the cut layer's opacity blurred at σ 3 texels and steepened ×1.6: a ~2.5-texel ramp that averages out the art's 3-texel steps. The first version used σ 2 coverage, which still followed the steps.
  - Parts under 4.5 texels thick (the mirror arms) keep the σ 2 coverage, so they do not fade.
  - Texels with alpha 0.25..0.94 near the edge take alpha · truck + (1 − alpha) · mean; fainter texels are left transparent, since they would be mostly the mean colour.
  - The result is opaque and exact against the average background. Where the background differs from its mean (a rock behind the hood), the ramp leans slightly toward the mean.
  - Not where layer 1 is behind.
- **The lines.**
  - Long dark painted lines are found by a black-hat of 40 over a 9-texel closing, components at least 60 texels long and 80 texels in size: the door outline, the window frame, the hood line, the handle.
  - They are redrawn as alpha · line colour + (1 − alpha) · the paint around them. Alpha is (gaussian σ 1 of the line − 0.15) / 0.6.
  - Both sides are the truck's own texels, so this is real anti-aliasing.
- Every written texel is green-even and non-zero.
- Counts: ring 2444 outside + 4590 inside, lines 12962 texels (4 lines), 86 records changed.

**Tests.** test_edgesmooth_618z13_truck_antialias: ring and lines present; texels green-even; the silhouette grows by at most the ring.

**Probes.** ztruck, plus three fields whose 1x effects were never seen on hardware: mtnvl3, icedun_2, las2_2.

## 62. BUILD 618z14 — an easter egg: the port developer's orange cat in Tsuna's crate cage

**Request (10-06).** With Echo-S, the crate cage in Sector 7 where Tsuna (the Echo-S modder) talks about Echo-S (mds7_w2) gets one of the orange cats from the Wutai cat house (utmin1). It lies facing forward and licks its paws, "nice in the center" of the crate to the right of where Cloud stands. The orange cat is the user's developer profile.

**New `ff7nx_easteregg`** (flevel, after scenemodel). Everything comes from the user's install.
- **Model.**
  - utmin1 has two cat models. BDGA ("animal_cat1") is the orange one: mean vertex colour RGB 142/83/31. EZCC is grey/white.
  - Its loader record is copied with all six animations (FAAC, EZBF, EZCA, HFEF, EZCB, FAAB) and scale 800, the same ratio to Cloud's 512 as in Wutai.
  - Its 30-byte light block is replaced by Cloud's from mds7_w2, so the cat is lit like the room.
  - It is appended as model 4; the loader count and section 1's model count each go up by 1.
- **Where.**
  - The user's crate crop was matched against the field render (NCC 0.87 at 3 px/unit): its top face is centred at screen (13, -41).
  - The field camera (section 2: axes/4096, position, zoom 2926; verified by projecting the walkmesh onto the background) puts that point at world (129, 64) at z = 156.
  - In this field the crates are walkable: z 78 is one crate high, 156 is two. Triangle 2 contains the point at z 156.
- **Facing.**
  - FF7's direction byte was fitted on 637 gateway arrivals across the vanilla archive: world angle = dir × 360/256 − 90°, with 83 % of arrivals within 25°.
  - The camera looks along +x+y, so facing it is 225°, which is dir 224.
- **Animation.**
  - Forward kinematics of the Wutai clips:
    - FAAB: sitting low, the right foreleg raised to the head, head moving (159 frames) = licking its paw.
    - EZCB: lying flat, head on its paws (asleep).
    - FAAC: sitting, tail swishing.
  - No clip both lies down and licks; the default is FAAB. `SEVENTH_NX_EASTER_CAT_ANIM=lie` uses EZCB.
- **Script.** Entity "nekodev" is appended last, so no entity index moves:
  `char 4; xyzi 0 0 129 64 156 2; dir 0 224; tlkon 01; ret; dfanm <anim> 1; ret`.
  - Scripts 1..31 point to a `ret`.
  - The cat is solid, so Cloud bumps into it on the crate, and cannot be talked to.
- **Section 1 rewrite.**
  - One name (8 bytes) and one script row (64 bytes) go before the code; the new code goes after the last script.
  - The string table, AKAO offsets and script entries move by those amounts.
  - The result must re-parse and decode. Existing scripts disassemble identically and the text/AKAO bytes are unchanged; anything else is refused.
- **Refusals.** Refused unless the field's script is Echo-S's (an entity named Tsuna and his name in the text), and unless triangle 2 is the crate top at z 156 and contains the point.
- `SEVENTH_NX_NO_EASTER_CAT=1` disables.

**Test.** test_easteregg_618z14_cat_on_tsunas_crate. Fixtures: `_scratch/c6/mds7_w2_394.pkl`, `utmin1_394.pkl`.

## 63. BUILD 618z14 (2nd) — ztruck's wheel no longer stirs the paint; las2 pond ripples in true colour

**Hardware 10-06 (618z13).**
- ztruck is "good enough". icedun_2 and nvmkin23 are fine.
- ztruck: "this spinning tire is causing pixel distortions on the paint", in the shape of the wheel arch. "the wheel effect should just be cleanly applied to all the dark regions".
- las2_2 / las2_3: the water ripples become "super pixellated" as they fade out. The user leans towards true colour.

**ztruck wheel (new `ff7nx_wheelfix`, after edgesmooth).**
- The rear wheel is 10 layer-2 tiles (x -112..-48, y 88..104), each with 3 animated states (param 1, states 1/2/4) over static copies of the same place.
- Every state tile is a whole 16-unit square, Cosmos-upscaled frame by frame. Across frames the paint differs by 8–16 levels on most texels, and the arch's cut-out edge moves.
- **The tyre region** is taken from the static copy:
  - the whitewall ring is fitted as a circle (iterative fit, 4-texel inlier band, r ≈ 33 texels) to locate the wheel;
  - the tyre is then the dark (lum < 100) and grey (saturation < 40) region reached from inside 1.65 r, holes filled, hub included;
  - saturation separates it from the arch's orange paint even in shadow.
- Every state texel outside the tyre is made transparent (31,473 texels), so the static paint shows in every frame.
- Rendered at the pinned camera: texels that change between frames drop from 17,321 to 8,415, and only 31 of those lie on saturated paint.
- `SEVENTH_NX_NO_WHEEL_FIX=1` disables.

**las2 ripples (new `ff7nx_ripplefade`, after palauto).**
- Three entities per field (ring1..3) play ripple frames on paletted 1x additive FX pages. Each has its own palette (las2_2: 8/10/14; las2_3: 8/12/14).
- The last frame is held while a loop fades it: `if v > 0 { v--; MPPAL A->B ×v/64; CPPAL; LDPAL B->q }`, from v = 66. The palette is restored afterwards.
- A palette fade cannot be done on a truecolor page, so the build had left them at 1x.
- **Records.**
  - All ripple records become truecolor additive records on the FX band, using Cosmos's brightest per-palette-state frame of that sheet.
  - The build's four 16:9 margin records have no vanilla twin; they use their own 1x cell upscaled.
  - The held frame becomes L copies, one free background parameter each (contiguous runs below 64), each the HD frame × ((j + ½)·D)/64 (D = ceil(66/L)).
  - L is the largest of 16/12/10/8 that fits: 10 in las2_2 (28.6 MB), 12 in las2_3 (27.9 MB). Both are within the raw cap.
- **Script.**
  - The loop's MPPAL + CPPAL + LDPAL (20 bytes) become a long jump (`lskip`) to a stub appended after the last script: BGCLR the levels; `set tmp = v`; `div tmp, D`; `plus tmp, base`; `bgon tmp, 0`; `lback` to the loop's IF. The loop keeps one backward jump per pass.
  - The restoring LDPAL becomes a jump to a stub that clears the levels, does that LDPAL, and jumps back.
  - tmp is a bank-5 byte whose value appears nowhere in the code (249 / 244).
  - Skipped bytes are RET (unreachable). Only the string-table and AKAO offsets move.
- **Measured.** Light added on screen: level L−1 = 1.01–1.05 × the paletted held frame; levels L/2 and 1 match their factors within 0.01. Ripple frames: the same light as the paletted ones.
- **Other fields.** A scan of every field for this loop finds only las2_2 and las2_3. las2_4's pond is already truecolor with no fade; the other variable-driven MPPAL fields are lights, not ripples.
- `SEVENTH_NX_NO_RIPPLE_FADE=1` disables; `SEVENTH_NX_RIPPLE_LEVELS=n` forces L.

**Tests.** test_wheelfix_618z14_paint_steady, test_ripplefade_618z14_levels_and_script. Fixtures: `_scratch/c6/las2_2_394.pkl`, `las2_3_394.pkl`.

**618z14 (2nd) — the cat, after hardware.**
- The user wants the cat lying coiled, not sitting, and centred.
- EZCB is now the default animation: lying, curled, head on its paws. `SEVENTH_NX_EASTER_CAT_ANIM=groom` brings back FAAB.
- Position (127, 62, 156): the crate's top-face centre measured on the hardware screenshot (13, -40), matched against the render with NCC 0.88.
- The sitting pose had looked about 4 units right of its anchor. The lying pose's joints centre on the anchor, within 0.4 model units of FAAB horizontally.


## 64. BUILD 619 — folder mods and [Tsunamods] Idle Animations (60fps)

**Asked (10-06).** The user extracted `Tsunamods__Idle_Animations_2.2_60fps` (a .7z) into `mods/` as a folder: "make it read by the gui as an iro when its in a folder like that, and have it respect priority settings and display the correct options just like 7th heaven pc", then make the mod work with every other enabled mod. Later: "our mod priority is 1:1 with 7th heaven pc ... this is a new mod though so it needs to be handled accordingly". Cave space is tight, so nothing may touch the cave.

**Folder mods (iro.py, build.Mod, 7th_heaven_nx.discover_mods, field_bg_repack.IroReader).**
- A directory in `mods/` with a `mod.xml` at its root is a mod, as in 7th Heaven PC.
- `iro.list_entries` / `read_one` (case-insensitive) / `extract` understand folders, and `IroReader` reads from them.
- `build.Mod` on a folder: the folder IS the cache. It is never extracted, written to or deleted. The stem is the whole folder name (`..._2.2_60fps` has no extension).
- **Manifest fixes found on this mod:**
  - Header fields are now first-wins, as 7th Heaven's SelectSingleNode reads them. The mod comments out only the TAGS of an old `A-Pose_Fix` ConfigOption, so its inner `<Name>A-Pose Fix</Name>` sat in the header, and last-wins showed the mod as "A-Pose Fix".
  - A non-GUID `<ID>` (that stray `A-Pose_Fix`) is not taken as the mod id.
  - Every other mod's name, id, category, folders and options are unchanged (checked on all 16).

**Priority (7th_heaven_nx._name_key).**
- 7th Heaven sorts names within a category with .NET's culture-aware comparer: whitespace, then punctuation, then symbols, then digits, then letters.
- `casefold()` order agreed with that for every mod already installed. It disagrees on "[Tsunamods] Idle Animations - 60fps" against "60/30 FPS Gameplay" (both Animations): 7th Heaven puts the idle mod first (higher priority), code-point order put it second.
- With the new key, the order of the existing 16 mods is identical to the saved `mod_load_order`, and the idle mod sits directly above 60 FPS Gameplay.

**What the mod does on PC.**
- `<char>N/char/<clip>.a` replaces each character's 1–3-frame standing pose BY NAME with a 700–2000-frame idle clip: ADCB Barret, ABCD Tifa, AVBF Aerith, AEAE Red, AEHA Cait, ABIE Cid, AFDF Vincent, ACFB Yuffie.
  - These names collide with 60 FPS Gameplay's interpolated poses.
  - Each is gated by a `<Conditional>` on RuntimeVar FieldID ranges. Excluded fields (mds7, mds7pb_1/2, semkin_5/6, datiao_6, trnad_4, niv_cl, ...) fall through to the next mod.
- `cloudN/char/ORIC.a` is a NEW name.
- `cloudanimflevels{,echo,sac,threat}/flevel.lgp/<field>.chunk.3` holds whole model-loader sections (~670 fields) that differ from vanilla only in Cloud's AAAA.HRC slot-0 animation → `ORIC`.
  - The copies are gated on exe bytes; Echo-S is 0x719C60 = 233.
  - Applied wholesale, they would also put back vanilla NPC models; e.g. mds7's std_man10 is ASBF there, but FRGD in the build.
- Bone counts of every clip equal the clip it replaces (Cloud 21 = ACFE).

**ff7nx_idleanim (data only — no exe, NSO or cave change).**
- The generic pipeline gets nothing from this mod (`Mod.files_for` → []).
- `build_plan` evaluates the options. Echo-S enabled answers the exe-byte gate, so the echo loader variant is chosen.
- **Character clips.**
  - Each clip is added to char.lgp under a new name: OIBA OITI OIAE OIRE OICA OICI OIVI OIYU.
  - In each field whose maplist index passes the folder's FieldID gate, every loader animation entry naming the old clip gets its 4 letters replaced (same length, extension and case kept).
  - In excluded fields the old name stays, so 60 FPS Gameplay's pose plays there: the PC fall-through, decided at build time.
- **Cloud.**
  - `ORIC.a` is added to char.lgp.
  - Per field, the selected variant's chunk.3 is READ, not shipped, to learn which AAAA records get ORIC. Only that entry changes in the BUILT loader, spelled "ORIC.aki" (same length as "ACFE.aki"; the mod writes bare "ORIC").
  - Records are matched by index when the counts agree, else by record name, else all-or-nothing, else refused.
  - ZNAA (ztruck/midgal scene variants) are not AAAA and are left alone.
- **Placement.**
  - The flevel pass runs after scenemodel and the cat, before the depth-1 lift and the debug warp.
  - The char.lgp pass runs after scenemodel's, is idempotent, and re-signs the archive with `_archive_restat`.
  - The flevel key includes `ff7nx_idleanim.fingerprint()` (clip contents, gates, variant).
- **Dry run on the user's built flevel (defaults, Echo-S):** 675 field loaders changed — ORIC ×657, OITI ×499, OICI ×479, OIBA ×314, OIRE ×245, OIYU ×238, OIAE ×225, OICA ×220, OIVI ×191. 0 refused.
- **Without Echo-S** (base variant): 1 refused, ealin_2, whose AAAA records differ.
- **Memory:** the clips are 0.26–0.75 MB of guest heap each, loaded with the field's models, against the 256 MB heap. char.lgp grows by about 3.3 MB.
- **Faithful quirk:** the mod's Echo-S conditionals list `CloudAnim = 8` twice and no 9, so with Echo-S CloudAnim 9 has no loader variant. ORIC is then unused, as on PC, and the build log says so.
- `SEVENTH_NX_NO_IDLE_ANIM=1` disables.

**Tests.** tests/test_build619.py:
- folder mod read-only and iro-like;
- generic folder mod;
- 7H name order;
- plan, loader repointing and gates;
- bone check;
- the real mod's defaults.


## 65. BUILD 619b — ripple flicker, the las2_2 blob, the tyre rim, the cat sitting

**Hardware (10-06 evening).**
- Idle animations at 60 fps are "perfect".
- las2 ripples: "once it reaches the end, it flickers out of view ... the image constantly appears and vanishes"; also "a weird glowing blob appears on the right side by the other ripple".
- ztruck: "a thin white outline around the edge of the tire between the tire and the truck's paint ... constantly under distortion".
- Cat: "sitting upright but not doing any gestures except wagging its tail ... facing the camera".

**Ripple flicker: my 618z14 stub.**
- Measured on the capture: the region's brightness during the fade goes on, on, off, repeating. At the low levels it goes on, off, off. Each fade pass takes 3 frames, against 1 for vanilla.
- The field interpreter runs at most 8 opcodes per entity per tick. The stub was 18 opcodes (IF, DEC, LSKIP, 10 × BGCLR, SET, DIV, PLUS, BGON, LBACK):
  - tick 1 cleared levels 0–4;
  - tick 2 cleared levels 5–9;
  - tick 3 switched the new level on.
- A tick model of the ring entity with that limit (`_scratch/c7/ticksim.py`) reproduces the pattern exactly: `..##.##.##`.
- **Fix:** the pass is written in place of the 20 bytes of MPPAL, CPPAL and LDPAL. It is 7 opcodes, one tick:
  `IF v>0; DEC v; SET t=v; DIV t,9; BGCLR lvl; BGON lvl,t; BACK`
- The levels are the 8 states of one free parameter. State t shows v in [9t, 9t+9) at (t+½)·9/64 of the held frame. Rendered ratios: 0.06 / 0.21 / 0.35 / 0.48 / 0.62 / 0.77 / 0.91 / 0.99.
- The held frame's BGON becomes a SKIP straight into the body, so state 7 appears in the same tick the last ripple frame goes off. The restoring LDPAL becomes `BGCLR lvl; SKIP 1`.
- Section 1 keeps its size and every offset.
- Tick model for all six ring entities (las2_2 and las2_3), at limits of 8, 7 and 6: no gap tick. The fade lasts 68 ticks (vanilla 66).

**The blob.**
- The right ripple uses palettes 12, 13 and 14. Seven of its palette-13 frames (p5 s5/s6/s7 and p6 s0, at x 160–240, y 112–128) sample page-15 cells whose indices are not their vanilla art. An earlier page pass handed those cells to palette-14 frames. Through palette 13 the art is a flat bright rectangle.
- It is present in the build-394 fixture as well, so it predates the fade work. The 618z14 conversion of palette 14 left it as the only user of those cells.
- `repair_cells` restores the vanilla 16×16 index block in place, only when every user of the cell wants the same block. las2_2: 7 cells; las2_3: none.
- The render at the user's camera (registered on the capture at (41.5, 12), NCC 0.84) now matches vanilla there.

**ztruck rim.**
- Each animated frame's rim sits 1–2 texels off the static copy's. Mean |frame − static| by depth inside the tyre mask: 11–12 at 1 texel, 5–6 at 2, 3.5 at 3, about 3 (the tread turning) further in.
- All the light texels between tyre and paint are within 3 texels of the edge.
- **Fix:** EDGE = 3. The mask is eroded by 3 texels (image borders not eroded) and the hub is kept.
- Frame-to-frame change within 3 render pixels of the orange paint: 487 px before, 16 now. Frame-to-frame standard deviation in the rim band: 2.02 before, 0.61 now. The tread and letters turn as before.

**Cat.**
- FAAC is utmin1's sitting clip. Forward kinematics on vanilla and on the built 60 fps copy (59 frames): only o1, o2 and o3 (the tail) move. The pose is FAAB's frame 0 (back upright, forepaws under the chest, hind legs folded).
- The head and paws point towards model −Z, the same as Cloud's feet in ACFE.
- DIR 224 turns that to 225°: −x−y, straight back along the field camera's view axis (0.556, 0.556, −0.617).
- EZCB, the lying clip, twists dou1 by 47–55°, which is why it did not read as facing the camera.
- `SEVENTH_NX_EASTER_CAT_ANIM=lie` or `=groom` picks EZCB or FAAB instead.

**Tests.**
- test_ripplefade_619_levels_and_script: in-place opcodes, level ratios, and the blob region matches vanilla.
- test_wheelfix_619_rim_static.
- easteregg now expects FAAC / `dfanm 00 01`.


## 66. BUILD 620 — Double Input Bugfix on ARM64; nvmkin21/22 art for a retouch

**Asked (10-06 evening).**
- Make "Double Input Bugfix" (a folder mod) apply safely when enabled: "if you click down and a at the same time ... it triggers down 2x".
- "Give me the full field texture for nvmkin21 / nvmkin22. i want to make a texture patch to the staircase just like we did for the tv in the bar."

**The mod (v1.2, ID 22ed7aef-...).** Options: difix (default 1), rate (default 50), delay (default 200). Only non-default values ship a folder. All of it is HEXT against ff7.exe `.text`:
- difix: `0041B09E = 34 87`, `0041B0AA = C6`
- rate n: `0041B15F = BA <n> 90` (`mov edx, n`)
- delay n: `0041B128 = B8 <n>` (`mov eax, n`)
- "Infinity" is 0xFFFFFFFF.

`build_exe` skips `.text` HEXT whole, because exefs/main is an AOT ARM64 recompilation. So the mod could not work as shipped.

**What the x86 does** (from the dump's ff7_en):
- held(m) = [0x9A85D4] & m (0x41AB67)
- pressed(m) = [0x9A85E0] & m (0x41AB74)
- check(m) (0x41B099): if [0x9A85E4] (rate) == 0, return held(m); otherwise return held(m) ? [0x9A872C] : 0.
- The repeat tick (0x41B108) sets [0x9A8734] = 0 on a fresh press (timer = delay [0x9A85C8]) and to 1 on each repeat (timer = rate [0x9A85E4]).
- The fix makes check() test [0x9A8734] and call pressed() in that branch. Until key repeat actually fires, a button counts once, on the frame it goes down.

**ARM64.** Translated 0x41B099 is at +0x8C360 and 0x41B108 at +0x895F0. held() is inlined into check(), behind the port's own `== 3` held/pressed selector.

| Site | Stock word | Patched word | Effect |
|---|---|---|---|
| +0x8C39C | `add w0, w19, #0x10` (0x11004260) | `add w0, w19, #0x160` (0x11058260) | w19 = 0x9A85D4, so the compared word becomes 0x9A8734 |
| +0x8C520 | `mov w0, w19` (0x2A1303E0) | `add w0, w19, #0xC` (0x11003260) | In the no-repeat branch, the held() pick reads pressed |
| +0x8966C | `ldr w22, [x0]` | `mov w22, #delay` | Delay |
| +0x89730 | `ldr w22, [x0]` | `mov w22, #rate` | Rate; Infinity is `movn w22, #0` |

- The pressed pick at +0x8C48C is already `add #0xC`. The +0x8C520 site is reached through the branch's own `b.ne`/`b` targets, not by address order, because the repeat branch's picks sit between them.
- Every site is found by signature: the base movz/movk, the guest-address add or sub, the translate `bl 0x10FC3A0`, then the load.
- Each word must be stock or already patched, otherwise nothing is written.
- Only those words change in the image (checked by a full image diff), and applying twice is a no-op.
- Wiring:
  - `build_plan` classifies the mod's active HEXT files, sets `ff7nx_difix.CURRENT` and drops them from the exe HEXT list.
  - `apply_difix` runs after shadowdepth, using the shadowdepth pattern.
  - `ff7nx_difix.py` is in MAIN_ONLY_MODULES.
  - `SEVENTH_NX_NO_DIFIX=1` disables.
- No cave, BSS or archive is touched.

**nvmkin21/22.**
- The staircase is layer 1. Layer 2 holds the capsules and the railings, with 137 stacked positions.
- Layer 1 of the two fields is identical except nvmkin21's one animated tile, which is not in the export. So artpatch gets REGIONS nvmkin21/nvmkin22 = (-256, -256, 512, 512) and `ASSET_OF = {'nvmkin22': 'nvmkin21'}`: one edit, one asset, both fields.
- Exported from the user's current build: `nvmkin21_layer1_EDIT.png` (1536×1536, to edit), plus `nvmkin21_full_REFERENCE.png` and `nvmkin22_full_REFERENCE.png` (both layers, as drawn).
- Round trip checked with a fake edit: 10,000 px changed in 9 cells in each field, nothing else.

**Tests (test_build619).**
- test_difix_620_classify
- test_difix_620_arm64_words
- test_difix_620_plan_from_folder_mod
- test_artpatch_620_nvmkin_shared

**620b: the staircase export (the user: "all of the field images for nvkim are missing squares ... all i care about is the full staircase, not the overlapping stuff that is added to the field").**
- **What the black squares were:**
  - In the layer-1 file: the 128 positions where layer 1 has no tile and only layer 2 draws.
  - In the reference: the animated tiles that were left out (the door at the top of the stairs, one light).
- **The new export,** artpatch mode `stairs`:
  - Region x -160..96, y -144..256 (768×1200 px).
  - Layer 1, plus a layer-2 tile only where layer 1 has none. That is the stair art layer 1 lacks: the top flight by the door, and a bottom step.
  - No overlays.
  - 9 px are left undrawn.
- **Where edits go:** each edited pixel is written into the tile that draws it. A cell shared with another tile is copied to a free truecolor cell first.
- **Two assets from one edit:** nvmkin22's copies of 13 cells (capsules, the upper right of the flight, one bottom step) differ from nvmkin21's by exactly one 5-bit level, so a shared SHA-1 would refuse it. `make` therefore also writes `nvmkin22.npz` from the same edit (`ALSO`, `make_also`), and both fields end with the same edited colours.
- **Test:** test_artpatch_620_nvmkin_stairs.

**620c: the "garbage squares" (the user, on the stairs export: "is there supposed to be garbage squares").**
- **They were my export's fault.** 21 of nvmkin21's records (13 of nvmkin22's) carry a stale source x/y at +10. The engine draws from the UV at +42, but the export read +10.
- **Fix:** artpatch now reads every cell from the UV (`_src`), and counts cell users the same way. Relocation already rewrote both +10 and +42.
- **New export:** the stair area exactly as the game draws it at load (mode `composite`):
  - all static tiles of layers 1 and 2;
  - the door entity's init state (BGON 1,0; BGCLR 2): the door closed.
- **Check:** the export equals framesim's render of the same area pixel for pixel (0 px differ, both fields).
- **The shipped mds7pb_1 asset is unaffected:** the 618 artpatch test still applies it.

**620d: the stairs replaced from the user's image.**
- **The user's image:** a ChatGPT upscale of the 620c export. It is identical to the export outside the stairs (local correlation 1.00), so the changed region (>6 levels, opened, closed, holes filled) is the staircase alone: one component, both flights, 182,055 px. It was blended in with a 1-px Gaussian edge.
- **The patches:** `nvmkin21.npz` changes 167,328 px in 142 cells; `nvmkin22.npz` changes 167,595 px in 144 cells.
- **Checks, against the user's build:**
  - 0 px change outside the stairs.
  - The patched export equals the user's image (encoded) exactly inside them.
  - Before/after renders at two cameras for each field: `_scratch/c7/nvm/*_before_after_cam*.png`.
- **What the assets hold:** XOR deltas only, applied only when the installed art matches its SHA-1.

## 67. BUILD 620e — the cat centred on its crate, wearing sunglasses

**Hardware of 620d:** the nvmkin staircase "looks great"; las2_2 ripples, ztruck, las2_3 and las2_4 fine.

**Centring (ff7nx_easteregg):** the cat's outline sat right of and below the crate face's centre. Averaging body and swinging tail, it moves −10/−7 px: POS (127, 62, 156) → (128, 71, 156), still walkmesh triangle 2.

**Sunglasses (new ff7nx_catshades), this cat only:**
- char.lgp gets three new entries, made at build time from its own BDGA cat: NKDA.hrc (BDGA with only the head bone `atama`'s RSD → NKDB), NKDB.rsd (PLY/MAT/GRP → NKDC), NKDC.p (BDHA plus the glasses). BDGA and every other model are untouched.
- The glasses come from the user's FBX, reduced to a flat pixel-art mesh (`assets/catshades/dealwithit_glasses.npz`: geometry only, 174+16 vertices, 412 triangles): black frame/lenses, white pixel highlights. In NKDC they are 824 triangles (both windings, flat normals, vertex colours) in the head's single group.
- Fit: the head bone's matrix at FAAC frame 0 gives the head's right/up/forward; the yellow eye vertices (194,194,29) give the centre and the eyes' front plane. Width 2.3 head units (head 2.27, eyes 1.44), flush with the eyes (GAP 0), 0.06 down. Renders front, ¾, from above and below: no eye texel shows.
- Wiring: char post-pass after idleanim (forces char.lgp into the targets when the easter cat is on; works with REUSE_CHAR). It sets `ff7nx_catshades.READY`; the flevel pass runs after it and the cat record names NKDA only when READY, else BDGA. Any failure fitting the glasses ships a plain copy, so NKDA always exists once written. flevel key gains ('catshades', wanted).
- `SEVENTH_NX_NO_CAT_SHADES=1` disables; `SEVENTH_NX_NO_EASTER_CAT=1` disables both.

**Fade survey:** the exact ripple loop is only in las2_2/las2_3. Other variable MPPAL fades on 1x paletted FX are listed in FIELD-TRACKER for eyeball checks (blin68_2 first).

**Tests:** test_catshades_620e_glasses_cover_the_eyes, test_catshades_620e_easter_cat_names_nkda_only_when_ready; easteregg test expects xyzi 128,71,156. A copy of the built char.lgp: 3 entries added, second run idempotent.

## 68. BUILD 620f — the cat without Tsuna, beam strobe, blin68_2's glow in HD

**Hardware of 620e:** in mds7_w2 scenes without Tsuna's NPC, a dark Tsuna stood where the cat sits.

**Why:** field models are handed out in the order entities execute CHAR (the argument is not an index). Echo-S's Tsuna runs `if var2[0x6C] ... char 03` and skips it when he is absent, so the cat — the next CHAR — received model 3, Tsuna, lit with the cat's light block.

**Fix (ff7nx_easteregg):** `char_gates` collects, for every entity before the cat, the forward IFs that jump over its CHAR (anything else branching before a CHAR is refused). Both halves of the cat's s0 (init: CHAR/XYZI/DIR/TLKON; main: DFANM) are prefixed with copies of those IFs, re-aimed at that half's RET. In mds7_w2 that is Tsuna's IF alone: the cat exists exactly when he does. Also: DIR 224 → 212 (17° toward Tsuna, screen left; checked through the field camera), glasses LIFT −0.06 → +0.04.

**anfrst_1 strobe (new ff7nx_destrobe):** `klight` loops ADPAL −1 → LDPAL 12/13 → WAIT 1 → ADPAL 0 → LDPAL → BACK. Palettes 12/13 draw 111 additive FX tiles still 256px paletted, and the capture shows a 3-frame bright/dim period (20 Hz). The rule: a BACK loop containing only constant ADPAL, LDPAL and WAIT ≤ 2 frames, whose offsets alternate between one value and zero. An archive scan finds anfrst_1/2/3/5, jail1, jailin1, jailpb (plus the warp copy). The non-zero offsets become 0, in place (3–6 bytes). Only anfrst_1 and jail1 draw it paletted; the others are truecolor and unchanged visually. Slow ramps (nvdun1, sbwy4_5), fires and TVs don't match.

**blin68_2 (ff7nx_palstates spec):**
- The tank glow is palette 13 (93 FX records, param 0), pulsed by `light2` (MPPAL × v/64, v 40↔63 one per tick).
- Cosmos ships 25 frames per page (v 40..63 + the opening 66).
- The FX band fits 16 levels (31.6 MB) but not 24. 12 levels are used, Cosmos's own frames at v = 40 + round(23k/11), on params 8..19.
- The pulse loop becomes `IF; BGOFF v; v±1; BGON v; WAIT 1; BACK` (v now is the param, 8..19): 2 ticks per level, a 22-tick ramp against vanilla's 23, 5 ops per pass.
- 93 → 1029 records, slots 18–22, 28.25 MB.
- The beaker event (palettes 6/7, params 1/2) is untouched, still paletted.

**anfrst_4 (diagnosed, not changed):** layer 4 scrolls at bg4_speed_x 128 (½ camera) in whole field units. Measured: walking moves the camera one unit (3 px) every other frame, so layer 4 moves every 4th frame (15 Hz steps); running moves the camera two units a frame, so layer 4 steps every frame. Fixing it needs sub-unit parallax in the renderer.

**Tsuna facial animation:** not possible as is. The runtime swaps a model's eye/mouth texture slots by HRC name, and Tsuna's head (ZZAF1/ZZAF2) carries only Zack's face texture EJEF, with no eye/mouth slots (Zack's own head EJED has EJEF + EJFA/EJFB + Z.TIM).

**Tests:** test_easteregg_620f_cat_only_with_tsuna, test_catshades_620f_glasses_higher, test_destrobe_620f_anfrst_1, test_palstates_620f_blin68_2_script, test_palstates_620f_blin68_2_plan.

## 69. BUILD 620g — voice behind the controller applet, blin68_2's glow brightness, the parallax diagnosis

**Hardware of 620f:** all probes fine (cat with Tsuna only, anfrst_1 and jail1 steady, blin68_2 in HD). Two remarks: the blin68_2 glow is "a bit fainter / less dispersed" than the 1x version, and anfrst_4's beams step when walking slowly. New report: a controller disconnect during a voiced Echo-S line loops the line on the reconnect screen until the game resumes.

**Voice loop (ff7nx_voice):**
- Every clip is staged with LOOPSTART, the only shape the Switch decoder streams. The field service ends it at a real-time deadline (`_emit_deadline_stop`).
- With the controller-support applet up, the game loop is parked and the service doesn't run, while the native worker thread keeps refilling the ring and rewinding.
- The fix has two parts:
  - The voice services (field, world, battle all share one block) write `CNTPCT` to `VOICE_HEARTBEAT_OFF` (VOICE_PAGE + 80, dead tail, no new BSS) each tick.
  - The worker loop cave (0x34B4, already hooked for MAPJUMP) ends OUR player through the same proven stop route (P+0x50 = 0, P+0x98 = 8, WORKER_STOP_UNLOCK). It does so only when all of these hold: the stream is looping, X19 is our player, P+0x68·num_samples − P+0x74 ≤ 0 (it has wrapped into the staged 100 ms of leading silence), and no service has ticked for 200 ms (3.84 M ticks).
- In normal play the last condition never holds, so the deadline path is unchanged. A line in a menu or any other loop that stops the service is also ended instead of repeating.
- Cost: worker cave 22 → 50 laid-out words, services +3/+4. The fully composed 620f module has 347 free words. The size-creep guard passes.
- `_emit_native_stop_async` is unchanged. A re-check under the mutex would close a microsecond race (the service resuming exactly as the worker stops) but cost 12+ words across the services.
- Emulated (arm64emu): stops only in the frozen, wrapped case; the MAPJUMP latch keeps its behaviour; W2/W8/X9/W10 are intact on the stock routes.

**blin68_2 glow (ff7nx_palstates `match_1x`):**
- Cosmos's frames for palette 13 are opaque and carry 0.25–0.65× the light of the 1997 palette at the same MPPAL value, and are bluer.
- Each level is now scaled per channel so the effect's total light equals the palette's at that level's v (v/64, records counted as drawn, 1x cells read at the UV). Gains run from 3.5/4.1/2.6 at v40 to 2.9/2.6/1.5 at v63.
- Measured afterwards: within 10 % of the 1x light, now clipped at 255. 1044 records, 28.25 MB.
- `SEVENTH_NX_PAL_STATES_COSMOS_LEVELS=1` ships Cosmos's frames unscaled.

**Parallax steps (diagnosed, not changed):**
- Layer 3/4 position = pos/16 + speed·cam/256 + scroll, truncated to whole units.
- The camera itself moves in whole units: anfrst_4's capture shows 3 px every 2nd frame walking, 6 px every frame running.
- At half speed the layer therefore moves every 4th frame walking, and every frame running.
- 22 fields scroll a layer at a fraction of the camera (list in FIELD-TRACKER 2h). The slowest (speed 32: crater_1, wcrimb_1/2, mtcrl_5) step every 8 camera units.
- Mapped so far: the draw at 0x9F2E60 passes guest 0xCC1620/24 to the layer-4 pick (0xA08630); 0x9C6460 rebases 0xCC1618..24 by (160, 112).
- Still needed: the writer of the speed·camera term, and the vertex path that turns tile positions into screen coordinates, so a fractional part can be carried.

## 70. BUILD 620h — 7th Heaven's floor hides the weapon on the pinball lift

**Report:** riding the lift in mds7pb_1, Cloud's back weapon (Ninostyle dynamic weapon, kept in this field on PC too: FieldIDs 154/155 are not in its exclusion list) pokes through the floor, under some squares and over others. The user's direction: keep the weapon and the vanilla rise spot, and give the floor squares priority.

**Measured:**
- Layer 1 (601 squares, the floor among them) has ID 4095 and IDBig 0, and is never depth-tested.
- Layer 2 is depth-tested at z = IDBig/1e7. Its 316 squares include the artists' occluders around the shaft.
- Each layer-2 ID is the camera-space depth of the floor under it / 4 (ratio 3.9–4.1). IDBig follows z = A − B/ID with A 1.01627, B 130.09 (max error 3×10⁻⁶).
- The walkmesh has the floor at z 0, a pinball platform at 25, and lift stops at −127 and −255 under x −78..−3, y 148..338.

**ff7nx_floordepth:**
- Every layer-1 square whose rays all meet the floor is moved to layer 2 (width/height 16, ID, IDBig). Its ID is the deepest visible surface over 5×5 rays, + 2: the floor plane, or the opening's walls/bottom (box to −60) where it looks into a lift opening.
- A moved square is never nearer than any layer-2 square it overlaps (hatch, rugs, tables).
- Records only move between the two layers: count, section size, texture block and page bindings are unchanged. 590 squares move; 11 stay (they don't see the floor, e.g. walls above the horizon).

**Verified:**
- Framesim renders are pixel-identical at three cameras.
- 0 of 33,120 points on or above the walkable floor are newly hidden.
- Under-floor points beside the shaft: visible 1303 → 600. The remainder is seen through the rim squares, which must stay as deep as the shaft they show.
- Points inside the shaft (Cloud's body on the ride): hidden count unchanged.
- Test: test_floordepth_620h_mds7pb_1. `SEVENTH_NX_NO_FLOOR_DEPTH=1` disables.

## 71. BUILD 620i — voice pauses with the game, blin68_2 every frame, ghotel fog dithered

**Hardware of 620g:**
- Voice behind the reconnect screen played on quietly; the user wants it paused and resumed.
- blin68_2 looked uneven, with frames missing.
- ghotel's bottom fog shows contour bands.
- ancnt4 and jailin1 are fine.

**Voice (ff7nx_voice):**
- The native renderer (P+0x80) has Pause at vtbl +0x50 (state 3, SetVoicePlayState Paused) and Resume at vtbl +0x48 (state 2, Started).
- `_build_worker_pause` runs at the worker loop head 0x3474 (stock `ldr w8,[x19,#0x98]`, player mutex held). It fires only when all of these hold: X19 is our player, it is playing, it isn't already paused, and no service has ticked for 100 ms (CNTPCT heartbeat). It then records HOST_TICK_NOW in VOICE_PAUSE_T0, calls Pause, and sets VOICE_PAUSED last.
- `_build_service_resume` is one subroutine each service calls with `bl` after its heartbeat. If VOICE_PAUSED is set, it adds (now − T0) to the stop deadline, clears the flag and calls Resume.
- 620g's end-at-wrap is removed and the MAPJUMP worker cave is back to stock.
- Size: services +5/+6, pause 50, resume 38 laid-out words.
- Emulated (arm64emu gains native `blr`): the worker pauses only when frozen. The resume moves the deadline by exactly the paused ticks.

**blin68_2 (ff7nx_palstates):**
- Levels: all 24 pulse values, params 8..31, one per tick.
- `animate_cells: 'auto'` (`plan_field_auto`): 58 of 93 tiles animate (98.7 % of the pulse); 35 faint tiles hold their middle frame.
- `match_1x` now solves each level and channel by bisection through a soft knee (above 192) instead of a hard clip: within 1 %, even steps. 1421 records, 31.62 MB.

**ghotel (ff7nx_fxrequant):** DITHER_FIELDS = {ghotel} uses Floyd–Steinberg (serpentine, per cell, key texels untouched) when the fog is requantised. The palette pulse keeps every frame. Other fields keep their hardware-confirmed pages.

**Parallax (still open):**
- Layer-4 pick 0xA08630 (x86 0x641358) computes (320 − bg.x)·[0xCFF1F0]. It converts tile x/y to float and stores them at 0xA09518/0xA09488 before add_page_tile 0xA06870 (x86 0x6464BA).
- bg.x comes from guest 0xCC1620/24, re-centred in 0x9C6460.
- The speed·camera/256 producer is unidentified. Every mul-then-÷256 site checked is an interpolation.


## 72. BUILD 620j — blin68_2 even glow, ghotel fog split, the pinball machine left alone, sub-unit parallax

**Hardware of 620i (user, 10-07):**
- Voice pause behind the reconnect screen is fixed.
- blin68_2 is a bit too bright and flickers. The steps are fast instead of gradual: one part brightens, holds, then spreads out, then steps up again.
- ghotel's fog is still flat contour bands; the 620i dithering only added noise and square seams.
- mds7pb_1: the 620h floor depth let the wall and floor squares beside, under and behind the pinball lift cover the machine as it comes back up. Only that region needed to stay as it was.
- anfrst_4's parallax still steps.

**blin68_2 (ff7nx_palstates, `even_light`):**
- 620i's per-channel gains (~3x) pushed Cosmos's core, already 255 at its top frame, into the ceiling. That made a flat plateau that grew level by level, and blue clipped first, so the core turned cyan.
- Now there is one halo lift for every frame: `tone_gamma 0.7` on each texel's brightest channel, with the hue kept. The peak is 236, under the 565 ceiling, so no texel ever flattens.
- The 24 levels sit at equal light steps, blended between Cosmos frames 0 and 23 only (`even_frames`). Cosmos's own frames step 4–10 % apart, its 25th jumps 11 %, and they change shape (6 % of texels dimmed while their neighbours brightened). With two ends only, every texel rises monotonically, as the 1997 uniform multiply does.
- The 565 dither is keyed to screen position (`_encode_cells` origins), so neighbouring cells no longer show seams.

**ghotel (ff7nx_palstates `split`; ff7nx_fxrequant back to nearest-colour):**
- The fog is palette 8: 348 one-cell records on two paletted pages, pulsed by light2 (MPPAL × v/64, v 40..62..40).
- 23 truecolor levels would need 8,004 cells, and the FX band holds 2,304.
- Split instead, into two parts:
  - A static truecolor HD copy of Cosmos's fog at `max(0, F·40/64 − 4)` (no palette, no bands).
  - The pulse `F·(v−40)/64`, drawn by the original paletted records in blend mode 3 (background + texture/4; vanilla uses it in colne_3, fship_5, min71, prisila and others).
- light2's variable is rebased to `2·(v−40)` = 0..44 (GHOTEL_STEP 2). Each pulse entry is chosen through the engine's own floor arithmetic, within 1.75/255 of the line at every value.
- A 5-bit step of the pulse layer is now 2/255 on screen, against 8/255 before. Base-vs-Cosmos error mean 1.54, worst 2.71 (cap SPLIT_MAX_ERR 12).
- Software preview at v = 40/51/62: no bands. File: `_scratch/c8/ghotel_prev.png`.

**mds7pb_1 (ff7nx_floordepth `keep_out` / `protect`):**
- A square with any ray landing in world box x −160..80, y ≥ 254 stays a plain layer-1 square, as vanilla. That box covers the pinball shaft, the raised platform in front of it, the floor either side and the back wall behind it.
- `protect` covers the machine's whole travel (x −85..3, y 282..344, z −285..180). Sampled through it, no point is newly hidden against vanilla.
- The rest of the floor keeps the 620h priority. Points under the floor beside Cloud's lift go from 1303 visible to 753 (620h: 600; the difference is the strip left vanilla).

**anfrst_4 parallax (new ff7nx_subparallax, exefs/main only):**
- Background: FIELD-TRACKER 2h. `t = speed·delta; local = sbfx(t,8,16) + (pos>>4)` drops the fraction, so a half-speed layer moved one unit every four frames. FFNx computes `pos/16 + speed·delta/256` in float.
- Capture caves: the 8 `sbfx w8,w8,#8,#16` sites (two camera paths × layers 3/4 × x/y: 0x9F94D8, 0x9F955C, 0x9F96B4, 0x9F9734, 0x9FA648, 0x9FA6C8, 0x9FA820, 0x9FA8A0) call a 4-word cave. It saves the dropped low byte in a 4-byte BSS block, then shifts.
- Store caves: the 4 tile float stores before add_page_tile (layer 3 y/x 0xA08500/0xA08590; layer 4 y/x 0xA09488/0xA09518) call a cave that subtracts `((byte + (pos&15)·16)·mult)/256` from s8.
- The integer position still drives the wrap and cull, and a sub-unit shift cannot expose an edge (port wraps are ≥ 8 units outside the view).
- Size: 12 sites, 224 laid-out words in verified padding. BSS grows by 4 bytes plus tail slack, so the stage `apply_subparallax` runs right after apply_moviepace, after the voice runtime.
- Applied to the user's 620i main: installs, is idempotent and every site is stock. BSS end 0x4C1ED00 → 0x4C1F02C.
- Every cave was executed in arm64emu for all 2,400 cases: the stored floats are exact and registers, sp and x30 are preserved.
- Hardware unverified. SEVENTH_NX_SUBPARALLAX=0 disables it.

**ripplefade (las2_2/las2_3):** the same screen-keyed dither as blin68_2. Re-check on hardware that the ripples still look right.

**whitein:** nothing changed. "Repeat of the bridge" was already fixed in 52d and the strip-only asset in 618z8 (user: OK in 391/392). A redo from an older session was discarded: assets/ is identical to the user's folder.

**Tests:**
- New in test_build619: test_floordepth_620j_pinball_machine_never_hidden, test_palstates_620j_blin68_2_levels_rise_evenly_without_ceiling, test_palstates_620j_dither_is_keyed_to_the_screen, test_palstates_620j_ghotel_split, test_subparallax_620j_caves_execute.
- Updated: 620f script (even_light) and 620h threshold (0.6). 620g gains test retired.
- test_build619: 29 passed. test_build618 + test_build617: 128 passed, 6 skipped. moviepace / voice_runtime / build_guard / pointers: 55 passed, 1 skipped.

## 73. BUILD 620k — the pinball plate on its rim, the table takes priority, the ride up, ghotel's margin seam

**Hardware of 620j (user, 10-07):** blin68_2 "looks great"; anfrst_4 "perfect"; ancnt1 and ancnt2 fine; ghotel much better, with one abrupt break in the fog left of the graves. On mds7pb_1:
- the pinball machine's floor panel peeks over the wood to the right of the machine;
- Cloud pokes through the table area while going down;
- he goes through the floor riding up.

The user asked for him to ride up as close to the machine as he can stand, and still step off.

**mds7pb_1 — what the panel is.** The pinball machine is field model CZED ("fieldbg_pinbl", one bone): cabinet, backglass and a floor plate 23.4 × 43 model units. It rides between an UP position (-35, 322, 0) (entity s0/s7 XYZI, s6 LADER) and a DOWN position (-42, 316, -280). On the user's screenshot, the plate's right edge overhangs the opening's yellow rim, and a strip of the orange hole wall shows inside the rim on the left (about 7.8 units) and at the front (about 5 units). Those match the 7 / 6 units between the two positions, so the plate fits the opening only at the down position.

**Fix 1: script (ff7nx_floordepth `plan_script`, section 0, 7 words).**
- The UP position becomes (-42, 316): XYZI ×2 and LADER ×1, with exact `a5/c2 00 00 + args` patterns.
- The ride up was (-35, 182, -255) → (-31, 152, 0) (cloud s3, and tifa s15 when she leads). That is a slant ending 4 units from the opening's front edge, so his body crossed under the floor in front of it. It is now straight up at (-35, 188), ending on walkmesh triangle 30.
  - The cabinet's front legs, back-projected from the screenshot, stand at y ≈ 214. That is about 209 after the move, which leaves room for his body.
  - He still walks off to (-31, 102).
- Every pattern must be found exactly as often as expected, or the script is left alone. Already-patched scripts pass through unchanged. The test covers both vanilla and the built script.
- SEVENTH_NX_NO_PINLIFT=1 disables it.

**Fix 2: relayer (ff7nx_floordepth `_relayer`).**
- The poke-through square is cell (64, 48). Its 1997 layer-2 floor records have IDs 1376 and 1360 (param 4), while the floor they show is at 1358 (deepest of 5×5 rays, +2). The body sinking just under the floor drew over them.
- Inside screen box x 0..176, y 0..144, every 16×16 cell gets the same treatment when:
  - all its IDs are < 4000;
  - it is not in keep_out and not protected;
  - all its rays see the floor or the opening.
- In those cells, records deeper than the floor become floor depth + k. Their order within the cell is kept, they stay behind any nearer record of the cell (the tables), and IDBig follows the field's curve. 28 cells.
- The moved layer-1 squares are then placed against the new IDs.
- Checks:
  - background renders are pixel-identical (states with and without param 4);
  - 0 of 44,160 walkable-floor points (h 0..140) are newly hidden;
  - 0 of 59,220 points over the machine's whole travel are newly hidden;
  - 0 of 4,160 points inside the opening are newly hidden;
  - under-floor points visible in front of the opening: 573 (vanilla) → 170 (620j) → 92. All 92 are within the +2 ID margin, in cell (16,16) and in cell (48,48).

**ghotel: the margin seam (ff7nx_palstates `edge_feather`).**
- The break is at x = -160, where the 16:9 margin (x ≤ -176, ground records ID 4090 + the fog) meets the 4:3 picture.
- The tombstone animation (param 2, states 1..64) exists only in 4:3. Its resting frame (state 1) differs from the ground under the leftmost column. Vanilla does too: 42–89 % of its texels equal the ground, and the bone at y 168..200 is darker. So with state 1 showing, the picture stepped by up to 55/255 at the margin.
- Each param-2 record in a 4:3 edge column (x -160 and 144) that has a margin cell beside it is faded into the ground record under it across that column. The fade is a smoothstep over 48 HD px: pure ground at the margin's edge, the frame's own look at the inner edge.
- Only truecolor cells no other record reads are written, and key texels are kept. 16 cells.
- Edge jumps per row with state 1: 6, 3, 55, 9, 22, 5 → 0.3, 1.2, 2.7, 1.5, 4.2, 1.4. These now equal the plain ground's (state 2). Row 232's 12.9 / 10.5 is the margin art itself, the same in every state.
- SEVENTH_NX_NO_EDGE_FEATHER=1 disables it.

**Parallax:** ff7nx_subparallax already covers both camera paths, layers 3 and 4, x and y, in every field. All 22 fields of FIELD-TRACKER 2h have it; nothing per-field was needed. Three of them are probed: mtcrl_4 (x), las1_1 (L3 + L4), sango2 (y).

**Tests:**
- New: test_floordepth_620k_pinball_lift_script, test_floordepth_620k_squares_by_the_table_take_the_floor_depth, test_palstates_620k_ghotel_state_cells_meet_the_margin.
- test_build619: 32 passed. test_build617/618 palstates/floordepth/fxrequant/ripple subset: 7 passed.

## 74. BUILD 620k2 — ghotel's glow at vanilla's swing, one fog edge softened, the plate on the rim, mtcrl_4 dirt, the build-log warning

**Hardware of 620k (user, 10-07):** all probes fine; mds7pb_1's three fixes confirmed, but the rim's yellow line still shows along the plate's front edge. ghotel has one discontinuity in the glowing fog under the bones (the user drew a white line from x 105 to 185 at y ~586), and the glow is "significantly dimmer than vanilla ... the effect on the far right is very subtle". The mtcrl_4 track and cross bracing are "rough along the edges ... dirty spots"; the user doesn't want it made worse. The build log shows numpy RuntimeWarnings from ff7nx_floordepth:166.

**Build-log warnings.**
- The warnings came from `C = (R @ P.T).T + o` in `_protected`. numpy on macOS (Accelerate BLAS) raises divide-by-zero / overflow / invalid warnings from matmul even on finite input. Our container's OpenBLAS doesn't, which is why the tests never showed them.
- Every matmul in floordepth is now an einsum (its own loop, no BLAS). The protected points are checked finite and in front of the camera.
- Result: 469 moved and 28 relayered, as before, and clean under `python3 -W error`.

**mds7pb_1.** The machine's UP position is now (-42, 313): x as before, 3 units forward of the down position's y. Along the front edge the plate stopped about 2 units short of the outer edge of the painted rim. Script test updated.

**ghotel: the line.** It is in Cosmos's fog itself, not in tiling:
- The split's static base has a +8/255 step over 2 texels, running from (165, 583) to (110, 594) on screen, exactly under the user's line.
- The 1997 pulse layer has a 5-bit contour at the same place.
- `_soften_split` now assembles the static fog in field space from every record's Cosmos cell, blurs it (Gaussian σ 7 HD px ≈ 2.3 units, normalised over the fog's own cells so its border keeps its level) and cuts it back per record.
- The step is now spread over ~10 texels (e.g. 46 → 54 across 2 px becomes 46 → 54 across ~10).

**ghotel: the glow.**
- 618b measured the port drawing every blended palette channel one 5-bit level darker: `8.67·c − 13.4`, clamped at 0, quartered in blend 3.
- 620j fitted the pulse entries to the nominal `8·c/4`. On screen, the first values of every faint entry showed nothing, and the swing shrank.
- Modelled through the transfer over 244 cells (green channel):
  - vanilla goes 4.8 → 9.4 (×2);
  - 620k went 11.3 → 14.9 (×1.3), the "very subtle" pulse.
- `_pulse_entries_hw` now picks each entry and channel through the transfer, targeting vanilla's own on-screen swing `hw_light(8E, v) − hw_light(8E, 40)`, with `hw_light(f, v) = max(0, 8.67/8·f·v/64 − 13.4 − 4.33)`.
- The trough (base) is unchanged; nobody reported it as too dim or too bright. The modelled light now goes 11.3 → 18.6.
- Fit error: rms 0.92 and worst 4.3 /255, the worst being the first values of the faint entries, which cannot light. The bound for this mode is 6.
- The colour palette's pulse entries are still checked against the 5-bit multiply.

**Correction to §73.** Param-2 state 1 is the SPEED grave's open-coffin frame, not the resting graves. The bone's darker right half in both screenshots is the art. The 620k edge fade still removes the margin step while that frame shows.

**mtcrl_4 (ff7nx_edgeclean `OPTS`).**
- Tried first:
  - Cosmos's alpha as the cut (the las4_2 path) thinned the 2..4-texel braces until the crosses went faint;
  - the rim recolour turned the braces' dark outlines into interior colour (washed out);
  - the whisker rule would key every brace, since a brace has no interior two texels in.
- So for mtcrl_4 the silhouette and outlines stay exactly as built. Only dirt changes:
  - qualifying texels are opaque, at least one inside the edge, 28..80 luminance below the 7×7 median of the opaque texels around them, and at least 45 luminance themselves (near-black rail slots and gaps are drawing, and stay);
  - they must form islands of at most 60 texels;
  - each takes that median colour.
- Result: 10,788 texels plus 24 speck texels, over 621 exclusive cells.
- Checked on the user's view (cam -261, 0): the braces are cleaner and keep their outline, and the rail slots are all kept. No texel became opaque; the only texels made transparent are the specks.

**Tests:**
- New: test_palstates_620k2_ghotel_pulse_through_the_port_transfer, test_palstates_620k2_soften_keeps_the_fog_level, test_edgeclean_620k2_mtcrl_4_dirt_only.
- Updated: the ghotel split bound (6) and the lift script pattern.
- test_build619: 35 passed. The edgeclean/palstates/ghotel/las4 subset of test_build617/618: 7 passed.

## 75. BUILD 620k3 — the plate fully on the rim, mtcrl_4 edges, semkin_4 lasers in HD, las4_42 halo cut along the pyramids

**Hardware of 620k2 (user, 10-07):** ghotel "good enough now"; datiao_8 fine. mds7pb_1: a small part of the rim's yellow still shows along the plate's front edge. mtcrl_4 is still rough, with stray orange pixels above the track ("could any of this also be made smoother with AA without ... regressing?"). semkin_4: the red lasers look low quality ("lots of dots, rough edges along the path ... are they true color?"). las4_42, the field before the FMV: pixelated, slightly dirty outlines around the pyramids' straight edges and the yellow glow.

**mds7pb_1.** The machine's UP position is now (-42, 311): 316 → 313 (620k2) → 311, measured on the user's screenshot (the remaining yellow was ~1.5 units deep). Both the `a5` and the `c2` patterns carry `D6FF370100000000`.

**mtcrl_4 (ff7nx_edgesmooth, after edgeclean).** Layer 2, static records.
- The orange pixels are **1x stubs**: whole 3×3 blocks of a single colour (channel range ≤ 1) left by the 1997 cut-out, sticking out of the rails into open space. `_stubs` removes any such block with at least 2 sides on exterior empty space (an empty region of > 200 blocks, so the lattice's holes don't count): 90 stubs.
- Edge AA with the **thick-only rule**: the staircase smoothing (mask blurred at σ 1.4, cut at 0.5) may only add texels where the shape is at least `THICK` 4 HD px thick nearby, and only remove texels where the background is that wide. A plain blur filled the lattice's corners (+19.5k texels); this rule keeps every strut and hole.
- Result: 583 records, +2,341 / −851 texels (under 1 % of the opaque area each way). Checked on the user's view (cam −245, 0): the stubs are gone, the rail edges are smoother, and the lattice is unchanged.

**semkin_4 (new ff7nx_laserhd).** No, the lasers were not true colour. They are static additive records (palette 9, blend 1) on the paletted 1x FX pages 15/16. The "dots" are the 1x texels, plus the palette's RTPAL shimmer crawling along them.
- The beam texels are composed at 1x. Each beam is found by Hough transform and fitted by total least squares; its cross profile is measured and smoothed along the beam (6 units).
- Each beam is redrawn at 3x as a Gaussian core (σ 0.45 units) plus a halo (σ 1.4, 25 % share), and the light is kept: ×0.97.
- The 59 records move to free cells on truecolor FX slot 17. Positions, IDs, blend and record count are unchanged, and no page is added.
- Cost: the RTPAL shimmer along the beams is gone; they are now steady lines. `SEVENTH_NX_NO_LASER_HD=1` keeps the 1x beams.

**las4_42 (new ff7nx_halohd).** The yellow-green dome is 261 additive records (palette 2, blend 1, ID 256) on paletted pages 15/16, drawn over the crystal pyramids (opaque Cosmos art, ID 288). Its own 1997 cut-out leaves the pyramids dark, so every pyramid edge is outlined by the halo's 1x staircase.
- The lit records are composed at 1x at the light the port shows at the pulse's middle: `hw_light(·, 59)`, through the 618b transfer. The `shade1` entity pulses palette 2 between 56/64 and 62/64.
- The halo is grown 3 px under the pyramids, upscaled 3x (bicubic), and cut by the pyramids' HD opacity (softened 0.8 HD px). The halo's edge is now the Cosmos silhouette, anti-aliased.
- Page 16 becomes truecolor; the 150 lit halo records on page 15 move to its free cells. Light ×1.01. Memory 4.31 → 7.38 MB; the MB and raw caps are checked.
- Compared like for like (both sides through the port transfer) at cam (0, 0): the staircase along every pyramid edge is gone, and nothing changed elsewhere.
- Cost: the halo's ±5 % breathing holds at its middle. `SEVENTH_NX_NO_HALO_HD=1` keeps the 1x halo.
- **Left alone: palette 3**, the cyan highlight rims on the centre pyramid (60 records). It pulses strongly (MPPAL 0x10 → 0x3E, ×4) and is visible, so making it truecolor would freeze it. A split (an HD static base plus a paletted pulse, as ghotel) would leave the pulse part at 1x anyway. It stays as is until the hardware test says it still reads as dirty.

**Wiring.** build.py runs `ff7nx_laserhd` and then `ff7nx_halohd` right after floordepth: the last page passes, on the same section 9 the 620k2 extracts were taken from.

**Tests:**
- New: test_edgesmooth_620k3_mtcrl_4_stubs_and_thick_edges_only, test_edgesmooth_620k3_stub_detector, test_laserhd_620k3_semkin_4_beams, test_halohd_620k3_las4_42_halo, test_halohd_620k3_off_switch.
- Updated: the lift script (311).
- test_build619: 40 passed. The edgesmooth/floordepth/palstates/edgeclean/ztruck subset of test_build617/618: 9 passed.

## 76. BUILD 620k4 — the pinball machine turned onto the lip, mtcrl_4 rails anti-aliased, the lasers even, las4_42 redone (pulse and fade kept)

**Hardware of 620k3 (user, 10-08):** semkin_4 "looks fine now". mds7pb_1: the yellow line is reduced but still there; the plate overlaps it on the right and not on the left ("partially over the wood, partially under it"). mtcrl_4 "looks good" but could be smoother on some track edges. The semkin_4 lasers go thin → thick (good) → thin again near the ends. las4_42 "needs a bit more work": rough edges, staircase patterns, "weird dirty stuff floating, sides not smooth".

**mds7pb_1 — the machine turned one step (ff7nx_floordepth script).**
- The user's view of 620k3: the plate's front edge crosses the opening's lip at a slant. On the left it stops short and the opening's lit interior shows as a yellow sliver (about 2 units, 4 px); on the right it covers the lip. Moving the machine (316 → 313 → 311) was not the fix.
- Rejected this round: darkening that strip of the opening's art (a first 620k4 draft). Previewed on the user's screenshot it read as a black line; the user rejected it, and it was removed before shipping.
- Now the machine is turned one direction step, DIR 0 → 1 (1.4°). FF7's dir d faces d·360/256 − 90°, counter-clockwise seen from above, so the left end of the front edge comes forward.
- The turn is pivoted on the plate's front-right corner (model (+11.67, −43.24) × 3.73): the left end comes 2.0 units forward and the right end stays where it is.
- Positions: UP (−42, 311) → (−46, 310). DOWN (−42, 316) → (−46, 315), moved by the same (−4, −1) so the ride stays straight. 10 script words changed; the protect volume starts at x −92.
- Caveat: the screenshot can't pin the plate's angle exactly (a model fit to the legs and corners carries ±3 units), so this is the smallest step the engine can turn. If the left still shows a sliver, or the right now overhangs, the next options are 2 steps or a different pivot.

**mtcrl_4 (ff7nx_edgesmooth).**
- Staircase blur σ 1.4 → 2.4: the rails' top-edge bumps are rounded off. The thick-only rule still keeps the lattice; under 1 % of the opaque texels change each way.
- New swept AA ring: the camera pans x −277..277 over a screen-fixed sky (layer 4) and parallax rocks (layer 3, speed 96). At 33 pan positions it records what each layer-2 texel has behind it: mean and std per channel.
- The art's own outer-edge texels (alpha 0.45..0.94, parts ≥ 4.5 texels thick, open space ≥ 12 texels deep beside them, background std ≤ 40) are blended toward that mean: 2,714 texels.
- No texel outside the art is touched. That version showed a pale fringe at other pans, because the sky changes with the camera.
- Checked at the user's camera (−232, 0) and at +100: the top edge of the rails is soft, with no fringe and no change in the holes.

**semkin_4 (ff7nx_laserhd).** The beam's light along its length varies 2–4× in patches in the 1997 art, which at a fixed width read as thickness. Each beam's profile is now its best monotone fit (rising or falling, whichever fits better) in one colour, with a 3-unit soft fade at the art's two ends. Light ×0.99.

**las4_42 (ff7nx_halohd, reworked).** The 620k3 module froze the halo, and that was wrong on two counts:
- `shade1` FADES THE HALO OUT once var5[4] is set (the FMV's lead-in), so the frozen HD halo would have stayed lit through the fade.
- Palette 3 (60 records) is not a pulse to keep 1x: `shade2` steps its multiplier through just five values (66, 51, 36, 21, 6). It is what showed as the cyan staircases along the pyramid edges and as the flat teal 16-unit rectangles (crops A, B, C, E of the user's screenshot, which the software render reproduces).

Now:
- **Pyramid edges.** Behind layer 2 there is only black sky, with the halo drawn over it. The layer-2 alpha (blur σ 3, steepened ×2, parts under 4.5 texels thick stay binary) becomes a real anti-aliased edge:
  - edge texels = alpha × art (over black), and the halo there is weighted (1 − alpha);
  - the art's own dark 1x-stepped rim (3 HD px) takes its colour from 4 texels in;
  - only where no layer-1 art lies within 6 HD px. Without that rule, an L2 tile ending against layer 1 got a dark seam.
  - 6,043 texels in 49 exclusive records.
- **Halo.**
  - Fixes in the 1x composite: one-texel dark notches and small holes filled (72 texels; the "floating dirt" at (160, 48) was a 72 among 192s); its own dim 1997 edge (4 texels before the pyramids) re-grown from the full halo.
  - Built as before: upscaled, weighted by (1 − alpha), drawn by 183 HD copies (param 1) on new truecolor slots 17/18.
  - The 261 original records stay on pages 15/16 under param 2.
  - `shade1`'s pulse loop is now: each tick BGON 1, BGOFF 2, var5[2] = 59, BACK. Its fade branch is entered through BGOFF 1, BGON 2, SKIP into vanilla's own fade loop. So the lead-in fade runs exactly as vanilla, on the 1x halo (~2 s), and the HD halo shows the rest of the time.
- **Highlights.** For each of the five values:
  - the 1x light (hw_light at that value) is grown under the art, upscaled, and softened (σ 4.5) inside the records' footprint, normalised over the art so it does not fade toward the silhouette;
  - it fades out over 6 px at the footprint's outer edge;
  - it is cut to the art (layer-2 alpha, layer 1).
  - Records per level: 0/40/55/59/58, on params 3..7. The 60 1x records are removed.
  - `shade2` becomes blin68_2's loop: IF; BGOFF v; v ± 1; BGON v; BACK, with v = 3 + level, 7 at start. That is one step a tick, as before.
- Light ×1.02. Memory 11.1 MB of the 35 cap. Bindings ≤ 256. A second run refuses.
- Not changed: the halo's straight lower edge where layer 1 begins (crop B) is the 1997 halo's own extent over layer 1's black area. Extending it would need new halo records there.

**Tests:**
- New: test_halohd_620k4_las4_42, test_halohd_620k4_script_patterns_are_exact, test_laserhd_620k4_beams_never_dip_back, test_edgesmooth_620k4_mtcrl_4_ring_only_on_the_outer_edge.
- Updated: the lift-script test (DIR 1, (−46, 310) / (−46, 315)).
- test_build619: 43 passed. 617/618 edgesmooth/floordepth/palstates/edgeclean/ztruck subset: 9 passed.

## 77. BUILD 620k5 — the pinball plate's yellow line was depth, not position; las4_4 ridges; mtcrl_4's black matte

**Hardware of 620k4 (user, 10-08):** mds7pb_1: the turn did not fix it. The right side looks worse and the front-left line is still there. "Could it be our ordering? Or simply our alignment", without regressing the floor fixes (Cloud must not show through the floor). The user asked for a proper preview before the next build. semkin_4: perfect, no more testing. las4_4: decent, but a couple of weird dips and some jagged edges. mtcrl_4 west: very rough edges, little black outlines, cross bars not smooth, dirty pixels and dirty cutouts.

**mds7pb_1 — position and ordering (ff7nx_floordepth).**
- Reference (user, 10-08): a screenshot of the 1997 position with two outlines drawn on it, where the plate was and where it must go. The second is the opening's own outline in the background: render and outline agree along the left, front and right rims.
- At the 1997 position (−35, 322) the plate's edges, measured column by column on that screenshot:
  - one rim band short at the front: the yellow band is 6–7 px across;
  - one rim band short on the left: ~5 px;
  - ~6 px past the opening on the right. This is the "peeking above the floor to the right" of 10-07.
- The same plate 5 units left and 6 forward lands on that outline. The screen shift is (−12.4, +0.8) px against the user's drawn (−12, +4.5) px, all three edges within ~2 px.
- So the machine stands UP at **(−40, 316)**. DOWN stays (−42, 316), so the ride down is nearly straight. The model is not touched.
- 620k..620k4 had gone to (−42, 313/311) and turned it: 2 units too far left and up to 5 too far forward, which put the front over the planks.
- Ordering, as found in this round:
  - the three lip squares at tiles (48, 0), (48, 16), (64, 16) (IDs 1370–1372) are nearer than the plate's left half, whose depth rises ~10 IDs from the right front corner to the left one;
  - so they drew over it — the yellow line along the left half wherever the plate stood ("partially over, partially under");
  - their six records go to (deepest point of the left 60 % of the front face, or of the left face's first 38 units) + FACE_MARGIN 4 → 1375..1383, order kept;
  - the planks at the right corner (tiles (80, *)) stay in front, so where the plate's right corner runs under them the planks' edge is the straight edge.
- Ruled out: back-face culling of the plate's sides (all 8 plate triangles are wound like its top).
- Tried and dropped after the user's review:
  - enlarging the plate's geometry instead of moving it (the user: alignment, not altered geometry);
  - texture-composited previews.
- Regression check: points of Cloud's ride newly visible are 65, all within 30 units below the floor (the first moments of the ride down, behind the front-left lip). Nothing standing on the floor is newly hidden.
- Preview (PREVIEW-620k5-mds7pb_1.png): the plate's edges at the 1997 position (red) and at (−40, 316) (cyan) over the user's screenshot and outline (white). No texture is painted.
- FACE_MARGIN 4 is calibrated on the 620k3 shot (engine depth ≈ camera z/4 + 4).

**las4_4 (ff7nx_edgesmooth).**
- The crystal pyramids: layer 2, 109 truecolor records on slot 26. The 321 blended glow records on the same layer are not touched.
- Their silhouette is re-cut (blur σ 2.6, cut at 0.5), and their own edge texels are blended toward the aurora's colour along the ridges.
- What is behind is the aurora effect, not background tiles, so the colour was measured on the user's 620k4 shot: (134, 255, 254), the median 4..14 px above every ridge.
- Art texels only (thick_only), so a darker moment of the aurora can at most leave a ridge texel half-way to cyan.
- Layer 1 (the floor blocks) is guarded.
- 71 records, +115/−147 texels, 6,841 blended.
- The deepest notch on the ridge line (x 553..556, a 6 px dip) is 3 px after the re-cut.
- An outside ring (both sides blended) was tried. In the simulation it showed a white outline, because the glow records add over it, so it was dropped.

**mtcrl_4 west (ff7nx_edgesmooth, new step `deoutline`).**
- The 1997 cut-out kept a black matte along the bars: of the layer-2 canvas's 145k cut-edge texels, 99.7k are near black (lum < 40), 1..2 texels thick. It is mostly under the beams and along the braces, and it is the "little black outlines" and much of the roughness.
- A near-black texel (lum < 50) within 2 texels of the cut takes the colour of the nearest wood texel (lum ≥ 60), if one is at most 4 texels away.
- Kept as they are:
  - parts that are dark through and through (the rails);
  - the edges of the rails' cut-out holes (only where the transparent side opens ≥ 4 texels).
- 65,623 texels recoloured; no texel changes transparency and no new colour is made.
- Not fixed this round (user, 10-08): the bumps along the rails' top curve and the stair-stepped braces. Tried and dropped, no visible change on the preview: an opening/closing of the rail silhouette near rail cores, and a narrow anti-alias ring on thin parts.
- Tried and dropped: (1) recolouring toward the local mean of the wood around, which washed out the curled broken rail ends and roughened the rail holes; (2) inner-texel references only, which left almost all braces untouched (they have no inner texels).
- The curled ends lose a little of their top outline. At normal size they still read as dark curls.

**Tests:**
- New: test_floordepth_620k5_plate_front_left_lip_squares, test_edgesmooth_620k5_mtcrl_4_black_matte_recoloured, test_edgesmooth_620k5_las4_4_pyramids.
- Updated: the lift-script test (UP (−40, 316), 7 words); 620h's shaft check (only the first 35 units behind the front-left lip may show); 620k's under-floor check is measured without the 620k5 rule.
- test_build619: 46 passed.


## 78. BUILD 620k5b — pinball: one unit forward and the lip squares split; las4_4's tile lines gone; mtcrl_4 edges re-cut along their direction

**Hardware of 620k5 (user, 10-08):**
- mds7pb_1: after riding up the plate looks right; entering the field from outside, a thin yellow line shows along the bottom edge. Riding down, Cloud and his sword draw over the wood in front of the opening.
- las4_4: a thin staircase line across the screen; edges still pixelated.
- las4_42 good. blin61 fine.
- mtcrl_4: dots sticking out on the track's curve; the lattice "should be 2 diagonal straight bars each way inside a box"; choppy edges.

**mds7pb_1 (ff7nx_floordepth).**
- UP (−40, 316) → **(−40, 315)**. Entering from outside the camera scroll differs, and the 1 px of rim left at 316 showed. One unit forward puts the front edge 0.75 px further over the rim. The bottom corner runs under the planks, which stay in front.
- New `_lip_split`. The three lip squares pushed behind the plate (620k5) also carry the wood in front of the opening; pushed whole, that wood no longer hid Cloud sinking behind it.
  - Each is split along the opening's outer rim (cfg `lip_split`, a polygon in background tile units, measured on the user's screenshot).
  - The pushed record keeps only texels inside the rim: the hole and its lit rim, which the plate covers.
  - A copy at the old ID and IDBig, in a free cell of the same truecolor page, keeps the wood outside.
  - The UV pair (+42) is set as well, since the port samples it, not +10.
  - 3 records added. The background renders pixel-identical at three cameras.
  - User review of the split preview (10-08): the corner piece left of the opening's left vertex has to stay in front too, ending in a flat edge level with that vertex. That wood belongs to the floor's own squares (moved to layer 2 at ID 1385). Their texels outside the rim, below the vertex (screen-unit y ≥ 14.0, cfg `lip_extra_below`), that are the topmost texel there (nearer or later records excluded; param 4 is never switched on — no BGON 4 in the script) go to a copy at the lip's old ID. +1 record (4 in all). Still pixel-identical.
  - The rim's bright outer highlight within 2 units (6 texels) outside the outline stays with the hole, behind the plate. Only the plain wood goes forward.
  - At rest (machine up, 0..4 units down, three cameras) neither the rim nor any wood copy covers a pixel of the machine (static depth model calibrated on the user's shot). The resting picture is unchanged.
- Ride-down check (static depth, Cloud's whole travel area in front of the cabinet), points visible:
  - no rule: 19,778;
  - lip pushed whole (620k5): 20,578;
  - split: 19,956 — the extra 178 lie inside the hole's rim.
- Preview `PREVIEW-620k5b-mds7pb_1-split.png` shows which texels go behind the plate (red) and which stay in front (green).

**las4_4 (ff7nx_edgesmooth).**
- The line across the screen was 620k5's: its sky blend treated every place where layer 2 ends and layer 1 continues as a silhouette edge. Reproduced in the simulation: thin cyan stair-stepped rectangles along those tile edges.
- Now only art texels within 3 of OPEN SKY are blended. Layer 1's cover counts as art for the ramp and the re-cut.
- The art's dark 1x-stepped rim (2 texels) takes its colour from 4 texels in (2,903 texels): that rim carried the stair look in its colour, not its shape. At texel level the silhouette is already 1 texel per step.
- Deeper ramp: art texels from alpha 0.15 are blended toward the measured aurora colour, 3,717 texels.
- Still art texels only: the aurora dims at the FMV lead-in, and a texel outside the art would show there as a cyan line.
- Containment: 'sky' mode is las4_4 only. ztruck and mtcrl_4 use the pinned-camera and swept modes, unchanged.

**mtcrl_4 (ff7nx_edgesmooth, new `orient_smooth`).**
- Each edge texel is re-decided by the coverage averaged ALONG the local edge direction: structure tensor of the opacity (σ 1 / 3), a 15-texel line, 24 angles.
- So a straight bar's edge becomes straight, and a nub or notch shorter than the line is averaged away. This takes care of the dots on the rail top.
- Only where the edge has one clear direction (coherence ≥ 0.55): crossings and box corners keep their shape.
- +23.3k / −3.7k texels. The black matte pass follows (62.5k texels).
- The curled broken rail ends lose a little of their dark outline.
- The layer-2 art exported for the user: `MTCRL_4-track-layer2-ORIGINAL.png` (as shipped by the mod) and `MTCRL_4-track-layer2-620k5b.png` (after this pass). Both are RGBA, 3 texels per field unit, top-left at field (−544, −88). A cleaner hand-made texture can be patched in the same way.

**Tests:** new test_floordepth_620k5b_lip_split_keeps_the_wood_in_front (4 records), test_edgesmooth_620k5b_orient_smooth_straightens_a_wobbly_bar. Updated: the lift script (−40, 315), 620h size (+3 records), the 620k3 mtcrl_4 gain bound (1.5 %). test_build619: 48 passed.


### 620k5c — Tifa stuck running in place after the lift

- **Report (hardware, 10-08).** When Tifa comes up on the pinball lift and runs off, she gets stuck running in place.
- **Cause.** When the machine is up, it is solid, with a collision radius of 120 around its position (`SLIDR 0x78` in Pinball s0).
  - Since 620k the rider lands at (−35, 188). That is 127 from the machine at (−40, 315).
  - A character is blocked inside the sum of the two radii.
  - Cloud's radius is 0 (`SLIDR 0` in Cloud s0), so he walked off. Tifa's is not 0, so her walk-off `MOVE` to (−31, 102) never left the circle.
  - Only Cloud (s4) and Tifa (s15) ride this lift (searched: the two XYZI/LADER pairs). Barret and Marlene's LADERs elsewhere in the field are untouched.
- **Fix (ff7nx_floordepth script).** Three changes:
  - **Rise point.** The ride is still straight up, now at **(−35, 170)**: XYZI in shaft triangle 1 (it was set to triangle 0, the wrong one for this point), LADER to floor triangle 20.
    - This point is still inside the opening, so the body stays clear of the front edge at y ≈ 148.
    - It is 145 from the machine.
  - **Machine radius.** 120 → **100**, so a rider radius up to 45 fits (this field sets 0, 30 or 48).
  - **Cabinet clearance.** The machine's circle still reaches y 215, and the cabinet's front face is at y ≈ 219.
  - **Walk-off path.** The MOVE to (−31, 102) crosses triangles 20 → 67 over an open edge.
- 8 script words now (was 7). The script loader takes raw byte patterns for the one-byte-bank SLIDR. The lift test checks the radius sum. 48 tests pass.

## 79. BUILD 621 — Enhanced Stock UI, screen 1: the main menu (your ESUI options)

**How it is switched on.** Enable `[Tsunamods] Enhanced Stock UI` in the mod list, with your options. build.py hands the mod to the new `ff7nx_esui.py` (`handles()`, the same pattern Idle Animations uses):
- Nothing of it is emplaced file by file.
- Its HEXT leaves the exe list.
- Its options decide what is applied.
- `SEVENTH_NX_ESUI=0` turns it off; `=main` picks screens.

**Why the first attempt refused flevel (10-08).** With the mod enabled, the generic path emplaced its Field/FieldESUI flevel entries, so flevel's INPUTS changed and `SEVENTH_NX_REUSE_FLEVEL=1` refused, as designed. ESUI now contributes nothing to flevel until the field-dialog screen is carried, so flevel reuse works again.

**What the main menu gets, for the options as set:**
- **Code: 114 ARM64 words in exefs/main, edited in place, no caves.**
  - `00-Main` (106 words, 10 functions).
  - `03-Battle`'s two words in the shared number draw (the other digit-spacing branch, 0xE → 0xB): +4 words.
  - **Main Menu Width = On** (folder `Fullmenu`): command box X 0x1F6, width 0x8A, text X 0x215 at all 8 sites (5 ARM words, one hoisted out of the loop into w24), cursor X 0x1EB at both copies.
  - One `.rodata` word: the location box X is read from a per-language table; only the English entry changes.
  - Every word is verified before it is written.
  - Checked differentially (unicorn), stock vs ESUI x86 against port vs edited ARM64: Fullmenu 0/45 differing runs, all 10 changed instructions covered.
- **Data:** 10 patches into ff7_en: `_h` → `_l` names, menu font scale 2.0, "Next Level" / "Limit Level".
- **Art: 22 menu sheets rebuilt as native TEX from ESUI's DDS,** last active folder winning, as in 7th Heaven. So Font Brightness (Gradient), Cursor (Buster Sword), Items/Materia icons, Expanded Icons, Thinnest Borders, Avatar Set, EXP bars and Numbers pick exactly their PC art. The sheets: usfont_a/b_l, btl_win_a/b/c/d_l, coloa/b/c, zeni_h, and 12 avatars.
  - **Multi-palette sheets use a joint index map** (new `joint_tex`). ESUI draws different things in different palettes of one sheet: btl_win_b has its slot reels only in palette 11, stars in 3..10, digits in all of them. The old converter keyed the map to one palette and lost the rest. Now each texel is described by its colour in all 16 palettes and the 255 slots are k-means clusters of those descriptions. Mean colour error is 6.5/255.
  - **Avatars keep ESUI's transparency** (colour key 1). Before, they were premultiplied to a black square.
  - **Controller icons:** ESUI's text set draws Xbox buttons over 14 ordinary characters. Those cells keep the game's characters. The Switch button glyphs are the port's own `data/png/buttons.png`, untouched.
  - The title-screen sheet (buster) is left for the title screen.
  - Native size (proven layout). `SEVENTH_NX_ESUI_TEXSCALE=2` builds them at 2x with the logical-UV bridge, which is an experiment: enlarged multi-palette TEX are not hardware-proven here.
- **Font widths:** ESUI's character-width table goes into window.bin's width section, which is where the game reads it from, so 00-Spacing's cave is not needed. The table is 00-Spacing's own, with the cave values your active options patch (Echo-S's expanded icons, the avatar set's mini avatars).
- **Echo-S calendar:** `ff7nx_calendar` carries ESUI's `ECHOS/MAIN/05 - ESUI Theme` layout as its ESUI profile, and the ESUI pass leaves its 15 words to it. The separator sits at the day's Y + 2.
- **`ff7nx_ddstex` fix:** the keyed branch's area reduction passed a 3-D mask to PIL and raised. It is reduced per channel now. That path always raised before, so nothing that worked changes.
- **Not carried yet:**
  - every other screen;
  - kernel icon names;
  - field dialog style;
  - ESUI's Time/Gil label colour file (logged as "not carried").

**Preview** (`PREVIEW-621-esui-main-menu.png`): rendered from the x86 code itself. The game's own text code runs in the emulator and every glyph quad it builds is drawn from the real sheets:
- **left:** stock `_h` sheets, stock widths, scale 1.667;
- **right:** the ESUI `_l` sheets built above, ESUI widths, scale 2.0, with 00-Main + Fullmenu + the Theme.
- Window frames are approximated, and stats are placeholders (the savemap is synthetic).

**Files:**
- `ff7nx_esui.py` (NEW);
- `ff7nx_calendar.py`;
- `ff7nx_ddstex.py`;
- `build.py`: `files_for`, `build_plan`, `apply_plan` (sheets into menu_us.lgp), `apply_esui` (module, ff7_en, window.bin), cache lists;
- `7th_heaven_nx.py`;
- `tests/test_build621.py` (NEW). 17 + test_calendar pass.

**Build:** the mod stays enabled with your options. menu_us.lgp has to be rebuilt once, so leave `SEVENTH_NX_REUSE_MENU` off:

    SEVENTH_NX_REUSE_FLEVEL=1 SEVENTH_NX_REUSE_BATTLE=1 SEVENTH_NX_REUSE_MAGIC=1 SEVENTH_NX_REUSE_WORLD=1 SEVENTH_NX_REUSE_HIGH=1 SEVENTH_NX_REUSE_CHAR=1 python3 7th_heaven_nx.py --cli

**Log lines:**
- `[Tsunamods] Enhanced Stock UI: screens main, 45 active folder(s); applied by ff7nx_esui`;
- `Enhanced Stock UI (BUILD 621): 22 menu sheet(s) built from its DDS at 1x -> menu_us.lgp`;
- `ESUI profile (BUILD 621)`;
- `100 words in exefs/main` (115 less the calendar's 15);
- `10 data patch(es) in ff7_en`;
- `character widths: ESUI's table`;
- `not carried: ECHOS/MAIN/06 - ESUI Colours.txt (2 patch(es) in main-menu code)`.
