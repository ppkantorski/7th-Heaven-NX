#!/usr/bin/env python3
"""
build_guard.py -- name every counter that moved when it should not have.

WHY
===
HANDOFF-121 section 3.6, the process failure that cost that session four
builds:

    "Logs read for confirmation, not evidence. The user supplied every log.
     They were grepped for whatever theory was current instead of diffed
     against the previous build. The 3,267 -> 1,199 collapse was in plain text
     for four builds."

A change to one pass should move that pass's counters and nothing else. This
tees the build log, pulls the counters out of it, compares them against the
previous build's, and writes a `!!` line naming anything that moved outside the
set the current change is expected to touch. It never stops the build -- a
20-minute build is too expensive to throw away over a warning, and the whole
point is to put the evidence in front of the reader rather than to judge it.

It reads the log text, not the passes. That is deliberate: the counters come
from a dozen modules as formatted strings, and plumbing structured values out
of all of them is a bigger change than the one being guarded.

USAGE
=====
In the build:

    guard = build_guard.CounterGuard(log, expect={'page cap'})
    ...  run the build with guard.log in place of log  ...
    guard.finish()

Standalone, against two logs you already have:

    python3 build_guard.py latest_log_34.txt latest_log_35.txt
"""
from __future__ import annotations

import json
import os
import re
import sys

# Each entry: name -> (regex with one capture group, which change owns it).
# The owner tag is what `expect` matches against, so a page-cap change can be
# told "these are yours, everything else is a warning".
# ---------------------------------------------------------------------------
# WHAT THIS BUILD IS ALLOWED TO MOVE. SET IT DELIBERATELY, EVERY TIME.
#
# The owner tags are the third field of each COUNTERS entry: 'page cap',
# 'margin art', 'margin palette', 'margin page', 'transparency key',
# 'dense repack', 'field background'.
#
# Empty means "this build should move NOTHING", which is the right setting far
# more often than it looks -- a logging change, a diagnostic, a refactor. Build
# 36 changed one log line and the guard was still told to expect page-cap
# movement, so the one counter that mattered was reported alongside noise.
#
# Declaring it per build is the point. HANDOFF-121 3.6: "one variable per
# build, prediction written first". This is that prediction, in a form the
# build can check itself.
# Build 62: STOP RE-DYEING BORROWED ART WITH A PALETTE THAT CANNOT HOLD IT.
# FINDINGS-151, and this is the one that changes pixels.
#
# Builds 60 and 61 both "landed" and both looked identical on hardware. 61 got
# mds5_5's margin sky onto a truecolor page, 40/40 cells -- and the PIXELS were
# still (79.5, 67.8, 27.8) against the interior sky's (65.4, 65.4, 58.0). Right
# depth, wrong colour. I had verified DEPTH and never verified COLOUR.
#
# Cause: 314 of the 403 cells are BORROWED (art at palette 0, tile names
# palette 1) and `_detail_transfer` then takes the DETAIL from Cosmos and the
# COLOUR from pal_ref -- the paletted page through palette 1, bluest entry 41.
# The module argues the correct rule 200 lines earlier and never applied it:
# "depth 2 pixels are FINAL COLOUR, the palette is never applied -> borrowed
# art draws exactly as FFNx draws it. CORRECT."
#
# Now skipped for HUE-BROKEN cells only. MEASURED, full pipeline:
#     mds5_5 margin sky (79.5,67.8,27.8) -> (74.8,78.2,74.6), which is
#            byte-identical to Cosmos's own art for those cells
#     mds6_2, mkt_mens unchanged -- they are not hue-broken, so the brown
#            right-hand side that justified the recolour keeps it
#
# READ `hue kept art` FIRST. 0 means the recolour is still running.
#
# Build 61 (previous): CARRY THE ART ACROSS ff7nx_marginpage's SPLIT. FINDINGS-150.
#
# Build 60's fix landed (21,672 cells flagged) and changed NOTHING on the
# reported fields, because `marginpage` repacks margin cells onto pages Cosmos
# never shipped -- slot 1 (sx,sy) -> slot 3 (dx,dy) -- so `art_for` returned
# None and `hue_broken` scored 0.0, i.e. "sound". Measured: mds5_5's margin sky
# went 40/40 flagged BEFORE the split to 0/40 after it.
#
# `split_section9` already built that mapping and threw it away. It is now
# recorded as ff7nx_marginpage.ORIGIN and the art is read from the origin page
# at the origin coordinates, while the rendered side stays at the destination.
#
# VERIFIED ON THE FULL PIPELINE THIS TIME (fill_field -> split_section9 ->
# dense_repack, ceiling 16). Build 60 was verified without the split, which is
# why it passed here and failed on hardware:
#     mds5_5  sky 27/40 -> 40/40   cells 388->403   pages 2->2
#     mds6_3  sky  5/40 -> 32/40   cells 353->397   pages 2->2
#     ujunon3, mds5_i unchanged;  unmeasurable cells: 0
#
# Build 60 (previous): TRUE_BLACK STOPS PINNING CELLS IT CANNOT COLOUR. FINDINGS-149.
#
# One mechanism (chromaticity distance between Cosmos's art and what the
# paletted page renders) applied at three sites. Two of the three MEASURED
# INERT on the reported fields and are here because they are correct, not
# because they do anything yet -- they are counted separately so the log
# attributes the result to the right one:
#
#   margin palette : HUE VETO on the final palette choice   (inert: 0 pages
#                    on mds5_5 and mds6_3 -- a page that is 70% ground has a
#                    mean hue of ground)
#   dense repack   : hue-broken cells sorted FIRST          (inert alone:
#                    every eligible cell already fits the pages allotted)
#   dense repack   : hue-broken cells EXEMPT from TRUE_BLACK  <-- THE FIX
#
# The third is what moves. mds5_5's margin sky is >=25% opaque black, so
# TRUE_BLACK held it on a paletted page whose bluest palette entry is 41.
# MEASURED, at NO extra pages in either field:
#     mds5_5  sky 27/40 -> 40/40 truecolor,  margin  86/120 -> 101/120
#     mds6_3  sky  5/40 -> 32/40 truecolor,  margin  65/120 -> 103/120
#     ujunon3, mkt_mens unchanged
#
# `dense repack` and `page cap` are therefore EXPECTED to move this time --
# build 59 flagged them as unexpected for a change that predicted them, which
# was my error in setting this set, not a defect in the guard.
#
# Build 59 (previous): the ESCAPE's TEST changes from quantisation error to HUE.
# FINDINGS-148. Build 58's error-based escape was inert -- penalty p90 1.33
# against a threshold of 10, 6 escapes in 335 pages -- because error is a
# magnitude and mds6_3's dark roof is cheap to approximate in every palette.
# Chromaticity is scale-free and separates the two known-answer cases by a
# factor of ten (left 0.0000, right 0.0483). Only the escape's decision moves,
# so `margin palette` moves; `choose` runs inside marginart, so that moves
# too; dense repack and page cap consume the result and may follow.
# transparency key must be IDENTICAL -- it does not read the palette byte.
#
# Build 58 (previous): the LAYER-1 CONSTRAINT gets an ESCAPE. Build 54 applied it
# unconditionally -- right for mds6_3's left margin (olive art wearing a grey
# palette) and WRONG for its right margin, where the art really is a blue-grey
# roof and forcing olive turned it brown. Now a page keeps its unrestricted
# choice when the best layer-1 palette quantises more than LAYER1_MAX_PENALTY
# worse. `choose` runs inside marginart, so BOTH move; dense repack and page
# cap consume that and may follow. transparency key must be identical.
# BUILD 68 PREDICTION, written before the build (HANDOFF-155 s5.2).
#
# Fixes the build 67 WALL MARKET REGRESSION. ff7nx_palrange repointed every
# out-of-range palette byte to palette 0; in mrkt2 that turned 151 vanilla
# FILLER cells -- which are entirely index 0, and marginart's keep-0 rule
# protects index 0 so the art never lands -- into flat (224,168,104) tan.
# The palette is now chosen PER CELL by rendering that cell's actual indices
# through each valid palette and taking the one closest to Cosmos's art.
#
# MEASURED (rendered colour of the out-of-range cells vs Cosmos's art):
#     mrkt2  err 134.8 -> 11.9, tan cells 151 -> 6
#     mrkt1  err  76.8 -> 10.3      mrkt4   err 24.3 -> 2.6
#     mds5_3 err  41.8 -> 12.7      mds5_5  err 39.0 -> 1.3
# Every out-of-range tile is still fixed: 0 remain in all seven fields tested.
#
# Build 67 also moved 'margin page split', which I did not predict -- more
# filled cells means more margin cells to split. Included now.
# NOTE: the GROUP for the margin page split counters is 'margin page', not
# 'margin page split'.  Naming the counter instead of its group is why the
# guard still cried wolf on build 68 after I "added" it.  Check the third
# element of the COUNTERS tuple, not the counter's display name.
# ---------------------------------------------------------------- BUILD 70
# RECOVERY BUILD. Two things, both SUBTRACTIVE:
#
#   field_bg_dense.PROMOTE_FX_BASE  = False   (was True in build 69)
#   field_bg_dense.MULTI_PALETTE_VETO = True  (new)
#
# PROMOTE_FX_BASE False undoes build 69 entirely -- that is what broke the
# lighting, steam and smoke overlays. FINDINGS-162/164/165.
#
# MULTI_PALETTE_VETO stops promoting a cell drawn through more than one
# palette unless the mod ships exact art for every one of them. A depth-2 page
# has no palette, so promoting such a cell collapses every tile sharing it to
# one colour. Build 68 shipped 87 of these. FINDINGS-165.
#
# BOTH ONLY EVER REMOVE CELLS FROM PROMOTION. A cell that is not promoted
# renders exactly as the game drew it before, so neither can introduce content
# that was not already on screen. That is why this is safe to build.
#
# MEASURED, 113 fields, offline chain:
#   veto OFF reproduces the build-68 baseline with ZERO drift
#   veto ON:  truecolor -28 tiles, pages +0, memory +0.0 MB, 27 fields touched
#
# PREDICTION for the log:
#   dense repack   moves DOWN slightly (~87 cells archive-wide, ~-2k tiles)
#   page cap / field background / palette clamp  may move; listed deliberately
#   everything upstream of dense_repack MUST NOT MOVE:
#     margin art, margin palette, margin page, palette range, transparency key
#
# ON HARDWARE: this should look like build 68 with the build-69 lighting
# damage gone. Wall Market smoke and lighting, Aerith's house waterfall and
# light beams, and the bottom of the Northern Cave (las0_2) are the three to
# check -- the last one for the FINDINGS-128 crash, which the frame guard
# should already prevent.
# ------------------------------------------------- NOT BUILD 71. RETRACTED.
# THE TRUECOLOR PAGE CEILING HAS NOT BEEN THE BINDING CONSTRAINT FOR A LONG
# TIME, AND I ALMOST SPENT A BUILD PROVING IT. FINDINGS-168 s3.5.
#
# FINDINGS-168 is right about the mechanism: the port's texture loader stops
# at slot 29 (0x10DC4A4, `cmp x23, #0x1d`) where the x86 runs to 42, and that
# is why builds 52 and 55 drew black squares. `ff7nx_fieldbg._load_slots_word`
# now patches that bound -- but it DERIVES it from D2_OPAQUE_SLOTS and emits
# NOTHING while that is 3, so the module this build writes is byte-identical
# to build 70's. The machinery is insurance, not a change.
#
# What was wrong was the payoff. I proposed D2_OPAQUE_SLOTS 3 -> 7 as "the
# direct route to 512px truecolor everywhere". Then I measured what the page
# ceiling actually costs, with `_coverage_audit.py` over 162 fields drawn from
# three separate regions of the archive -- 162,613 tiles, 23% of the game:
#
#     truecolor    106,828   65.7%
#     TRUE_BLACK    20,702   12.7%   >=25% opaque black, kept paletted
#     FX            20,450   12.6%   carries/is an fx page
#     KEY_L1        14,592    9.0%   index 0 on layer 1
#     KEY_L2             0    0.0%   (already promoted)
#     BUDGET            41    0.025% <-- ran out of pages/slots/room
#
# 1,659 free low slots went unused. **BUDGET is 41 tiles.** The low-slot probe
# (LOW_SLOT_MAX_TC = 7, LOW_SLOT_TOP = 14) already routes around the ceiling
# wherever it would bind, which is why every field measured had 10-13 free
# slots below 15. Raising the ceiling would move 0.025% of tiles and would
# put pages into slots that have never rendered on hardware. Bad trade.
#
# THE REAL TARGETS, IN SIZE ORDER, AND ALL THREE ARE OUR RULES OR THE PORT'S
# BLEND LADDER -- NONE IS CAPACITY:
#   TRUE_BLACK  12.7%  ours. 0x0000 means transparent on a depth-2 page, so
#                      solid black takes a 0.9/255 lift and we refuse cells
#                      that are >=25% opaque black. Biggest single bucket and
#                      entirely within our control. START HERE.
#   FX          12.6%  structurally blocked: this port gives EVERY depth-2
#                      page blend 4 with no slot test, so a promoted animated
#                      frame draws opaque instead of additive. Needs a blend
#                      trampoline, not a flag. FINDINGS-168 s3.2.
#   KEY_L1       9.0%  ours. PROMOTE_LAYER1_KEY.
#
# ---------------------------------------------------------------- BUILD 71
# ONE VARIABLE.  field_bg_dense.TRUE_BLACK = 0.25 -> 1.0.  FINDINGS-169.
#
# A cell that is >=25% opaque black kept its paletted page so its black stayed
# exactly black instead of being lifted to NEAR_BLACK and drawing a seam
# against an unpromoted neighbour. That reasoning was written when NEAR_BLACK
# was 0x0001 (pure blue) and the HD shaders had no black point. Both changed:
# NEAR_BLACK is 0x0841 = RGB(8,8,8), and both shipped background scalers carry
# HD_BLACK_POINT = 0.03137 = 8/255, sized to cancel exactly that lift.
#
# MEASURED with `_seam.py`, which renders both sides of every 16-px tile
# boundary promotion changes and reports step_AFTER - step_BEFORE -- the pair
# measurement this tree never had (HANDOFF-167 s0.5). 18 real fields, 1,112
# newly promoted cells, 2,820 changed boundaries:
#
#     boundaries worse by     RAW surface      AS SHIPPED (graded)
#       > 2/255                 649 (23.0%)       8 (0.28%)
#       > 8/255                   4                2
#
# The raw column IS the artifact the rule was written to stop, and mkt_mens's
# worst raw boundary is exactly 8.000 -- the lift's signature -- on 41 of 108.
# Graded, mkt_mens has zero over 2/255 and a mean delta of -3.679: Men's Hall
# gets BETTER. So do sbwy4_3 (-9.29), jun_w (-8.96), junpb_3 (-6.09).
#
# 1.0 not 0.0: a 100% black cell gains nothing from promotion and keeping it
# paletted leaves truecolor page space for cells that use it.
#
# PREDICTION, MEASURED offline over 45 fields (sweep_repack, D2_OPAQUE_SLOTS
# still 3, nothing else touched):
#
#     truecolor tiles   20,715 -> 21,092   (+377, 66.9% -> 68.1%)
#     fields with truecolor DOWN                0     <- the hard one
#     fields with truecolor UP                 19
#     pages                                   -15 across 12 fields
#     fields that failed to repack        unchanged (astage_b, blackbgb --
#                                         both pre-existing, identical at 0.25)
#
# So: 'dense repack' moves UP, and 'field background' page/memory counters
# move DOWN. A page count going UP anywhere is NOT expected and should be read
# as a surprise. Everything upstream of dense_repack MUST NOT MOVE: margin art,
# margin palette, margin page, palette range, transparency key.
#
# WHAT WOULD FALSIFY IT ON HARDWARE: a visible edge between a dark promoted
# cell and its neighbour. The two boundaries that did get worse are named, so
# look at them first --
#     blin59    (2, -160, -64)|(2, -160, -48)   +10.324
#     blin63_1  (2, 128, 160)|(2, 128, 176)     + 8.754
# then mkt_mens (Men's Hall), which should look BETTER and is a long-standing
# open problem; then Wall Market and Aerith's house for regressions.
#
# NOT IN THIS BUILD, deliberately: D2_OPAQUE_SLOTS stays 3 (the page ceiling
# is worth 41 tiles, FINDINGS-168 s3.5), PROMOTE_FX_BASE stays off (it needs a
# blend trampoline, FINDINGS-168 s3.2), PROMOTE_LAYER1_KEY stays off.
#
# RUN `_fxpx.py` ON THE OUTPUT. Expect 0 px-mismatched fx pairs. 90 seconds,
# and it is the check that would have stopped build 69.
EXPECTED_MOVEMENT = frozenset({'dense repack', 'page cap', 'field background',
                               'palette clamp'})

COUNTERS = (
    # FINDINGS-158. Tiles naming a palette past the end of section 3. This
    # must NEVER be silently zero because the line vanished -- if the regex
    # stops matching, the counter reads None and the guard says "moved",
    # which is the trap build 64 fell into.
    ('palette range tiles',
     r'PALETTE RANGE -- ([\d,]+) tile\(s\)', 'palette range'),
    ('palette range fields',
     r'PALETTE RANGE -- [\d,]+ tile\(s\) across [\d,]+ cell\(s\) in ([\d,]+) field',
     'palette range'),
    ('margin art cells',
     r'margin art: ([\d,]+) cell\(s\) of Cosmos', 'margin art'),
    ('margin art fields',
     r'margin art: [\d,]+ cell\(s\).*? in ([\d,]+) of \d+ field', 'margin art'),
    ('atlas gap texels',
     r'ATLAS GAP: ([\d,]+) texel', 'margin art'),
    ('margin art refused',
     r'([\d,]+) REFUSED as wildly off-colour', 'margin art'),
    # FINDINGS-140. `keep0 dropped` is the fix landing; `keep0 kept` is the
    # cut-outs it must NOT have touched. If the second one moves, the layer /
    # blend-band classification changed and overlays are at risk.
    ('keep0 dropped',
     r'KEY DROPPED: ([\d,]+) texel', 'margin art'),
    ('keep0 cells',
     r'KEY DROPPED: [\d,]+ texel\(s\) in ([\d,]+) layer-1', 'margin art'),
    ('keep0 kept',
     r'([\d,]+) texel\(s\) in genuine cut-outs', 'margin art'),
    ('margin palette pages',
     r'margin palette: ([\d,]+) page\(s\)', 'margin palette'),
    # FINDINGS-142. The fix landing. If this is absent the change did not
    # reach the archive; if it is huge the constraint is over-firing.
    ('margin palette layer1 constrained',
     r'LAYER-1 CONSTRAINT: ([\d,]+) page', 'margin palette'),
    # FINDINGS-147. How many pages the restriction let go because the art is
    # genuinely not layer 1's colour. 0 means the escape never fired.
    ('margin palette layer1 escaped',
     r'ESCAPE: ([\d,]+) page', 'margin palette'),
    # FINDINGS-148. Of the escapes, how many the HUE gate drove. If this is 0
    # the chromaticity test is inert and the build is build 58 in disguise --
    # which is exactly what the error-only escape turned out to be.
    ('margin palette layer1 escaped hue',
     r'ESCAPE: [\d,]+ page\(s\)[^.]*?-- ([\d,]+) of them on HUE',
     'margin palette'),
    # FINDINGS-149. The fix landing. 0 means the final selection is still
    # pure error and mds5_5's sky is still flat olive.
    ('margin palette hue vetoed',
     r'HUE VETO: on ([\d,]+) page', 'margin palette'),
    ('margin page split cells',
     r'margin page split: ([\d,]+) cell\(s\) moved', 'margin page'),
    ('margin page split pages',
     r'margin page split: [\d,]+ cell\(s\) moved onto ([\d,]+) new',
     'margin page'),
    ('transparency key pages',
     r'transparency key: entry 0 de-fringed on ([\d,]+) palette page',
     'transparency key'),
    ('transparency key fields',
     r'de-fringed on [\d,]+ palette page\(s\) across ([\d,]+) field',
     'transparency key'),
    ('transparency key bright',
     r'across [\d,]+ field\(s\), ([\d,]+) of them previously a bright',
     'transparency key'),
    # This one moved 3,796 -> 3,847 between builds 33 and 34 and I missed it
    # by hand. It was benign -- the cap added 46 pages and a duplicate in an
    # additive band is skipped for the same reason its original is -- but
    # "benign" was a judgement made after the fact. Counted from now on.
    ('transparency key left alone',
     # FINDINGS-156: the log line was reworded to 'SKIPPED by the
     # de-fringe' and this regex stopped matching, so build 64 reported
     # this counter as None -- which reads as a moved counter and hides
     # a real regression.  Match BOTH wordings.
     r'([\d,]+) palette\(s\) were (?:LEFT ALONE|SKIPPED by the de-fringe)',
     'transparency key'),
    ('dense repack cells',
     r'DENSE REPACK .*?: ([\d,]+) cell\(s\) packed', 'dense repack'),
    ('dense repack pages',
     r'cell\(s\) packed onto ([\d,]+) page\(s\)', 'dense repack'),
    ('dense repack fields',
     r'packed onto [\d,]+ page\(s\) across ([\d,]+) field', 'dense repack'),
    ('dense repack borrowed',
     r'([\d,]+) borrowed,', 'dense repack'),
    ('dense repack exact',
     r'([\d,]+) exact from the mod', 'dense repack'),
    # FINDINGS-145. Absent means the probe never fired.
    # FINDINGS-149. The fix landing. 0 means this build is build 59.
    ('hue broken first cells',
     r'HUE-BROKEN FIRST: ([\d,]+) cell', 'dense repack'),
    ('hue broken first fields',
     r'HUE-BROKEN FIRST: [\d,]+ cell\(s\) across ([\d,]+) field',
     'dense repack'),
    # FINDINGS-150. Detector blindness, counted. Large = the origin map is not
    # reaching the repack and the fix is inert again.
    # FINDINGS-151. THE one that changes pixels. 0 = the borrow recolour is
    # still re-dyeing Cosmos's art with a palette that cannot hold it.
    ('hue kept art',
     r'KEPT THE ART: ([\d,]+) cell', 'dense repack'),
    ('hue broken unmeasurable',
     r'UNMEASURABLE: ([\d,]+) cell', 'dense repack'),
    ('low slot probe',
     r'LOW-SLOT PROBE: ([\d,]+) free slot', 'dense repack'),
    ('page cap fields',
     r"PAGE CAP .*?: ([\d,]+) field\(s\) had a page split", 'page cap'),
    ('page cap pages',
     r'had a page split, ([\d,]+) page\(s\) added', 'page cap'),
    ('page cap tiles',
     r'page\(s\) added, ([\d,]+) tile\(s\) repointed\. Worst', 'page cap'),
    ('page cap worst',
     r'Worst page held ([\d,]+) tiles', 'page cap'),
    # Build 34 called this "SINGLE-SCREEN HARD CAP"; FINDINGS-123 renamed it.
    # Both spellings are matched so a 34 -> 35 diff still lines up instead of
    # reporting the counter as vanished and reappeared.
    ('window cap fields',
     r'(?:WINDOW CAP|SINGLE-SCREEN HARD CAP): ([\d,]+) field\(s\)',
     'page cap'),
    ('window cap pages',
     r'(?:WINDOW CAP|SINGLE-SCREEN HARD CAP): [\d,]+ field\(s\), '
     r'([\d,]+) page\(s\) added', 'page cap'),
    ('uncappable fields',
     r'page cap: ([\d,]+) field\(s\) could not be capped', 'page cap'),
    ('palette clamp tiles',
     r'PALETTE CLAMP: ([\d,]+) tile\(s\) in', 'palette clamp'),
    ('palette clamp fields',
     r'PALETTE CLAMP: [\d,]+ tile\(s\) in ([\d,]+) field', 'palette clamp'),
    ('green lsb texels',
     r'GREEN-LSB BACKSTOP: ([\d,]+) truecolor texel', 'field background'),
    ('truecolor rescaled',
     r'([\d,]+) truecolor page\(s\) in [\d,]+ field\(s\) rescaled',
     'field background'),
    ('warning lines', None, 'any'),
)


def extract(text):
    """{counter name: int} for everything this log mentions."""
    out = {}
    for name, pat, _owner in COUNTERS:
        if pat is None:
            continue
        m = re.search(pat, text, re.S)
        if m:
            try:
                out[name] = int(m.group(1).replace(',', ''))
            except ValueError:
                pass
    # THE GUARD MUST NOT COUNT ITSELF. Its own findings are written with `!!`,
    # so a build where it fired reported three MORE warning lines than one
    # where it stayed quiet -- it inflated the very metric it monitors, and
    # then flagged the inflation. Seen between builds 35 and 36: 93 -> 96,
    # exactly the three `!!` lines it had just written.
    out['warning lines'] = len([
        ln for ln in re.findall(r'^\s*!.*$', text, re.M)
        if not ln.lstrip().startswith('!!')])
    return out


def _owner(name):
    for n, _p, o in COUNTERS:
        if n == name:
            return o
    return 'any'


def compare(old, new, expect=()):
    """
    (unexpected, expected) -- each a list of (name, old, new).

    A counter whose owner is in `expect` is allowed to move. Everything else
    is reported. A counter that appears or disappears entirely is reported
    either way, because that is usually a renamed log line and the reader
    needs to know the diff is no longer comparing like with like.
    """
    expect = set(expect)
    unexpected, expected = [], []
    for name in sorted(set(old) | set(new)):
        a, b = old.get(name), new.get(name)
        if a == b:
            continue
        (expected if _owner(name) in expect else unexpected).append(
            (name, a, b))
    return unexpected, expected


class CounterGuard:
    """Tees a log callable, then reports on the counters at `finish()`."""

    def __init__(self, log, expect=None, path=None, label=''):
        self._log = log
        self._lines = []
        self.expect = set(EXPECTED_MOVEMENT if expect is None else expect)
        self.path = path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'build_counters.json')
        self.label = label

    def log(self, text=''):
        self._lines.append(str(text))
        self._log(text)

    __call__ = log

    def text(self):
        return '\n'.join(self._lines)

    def finish(self):
        """Compare against the previous build and write this one's counters."""
        new = extract(self.text())
        if not new:
            return
        old = None
        try:
            with open(self.path) as fh:
                old = json.load(fh).get('counters')
        except Exception:                                      # noqa: BLE001
            old = None
        try:
            with open(self.path, 'w') as fh:
                json.dump({'label': self.label, 'counters': new}, fh, indent=1)
        except Exception:                                      # noqa: BLE001
            pass
        if not old:
            self._log('  counter guard: no previous build to compare against; '
                      'this build is now the baseline.')
            return
        unexpected, expected = compare(old, new, self.expect)
        if expected:
            self._log('  counter guard: expected movement -- '
                      + ', '.join(f'{n} {a} -> {b}' for n, a, b in expected))
        if not unexpected:
            self._log('  counter guard: every other counter identical to the '
                      'previous build.')
            return
        self._log('  !! COUNTER GUARD: %d counter(s) moved that this change '
                  'should not have touched.' % len(unexpected))
        for n, a, b in unexpected:
            self._log(f'  !!   {n}: {a} -> {b}')
        self._log('  !! This change is NOT isolated. Read these before '
                  'testing on hardware -- HANDOFF-121 3.6 is exactly this '
                  'failure, and it sat in the log for four builds.')


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print(__doc__.strip().split('USAGE')[-1])
        return 2
    old = extract(open(argv[0], errors='replace').read())
    new = extract(open(argv[1], errors='replace').read())
    unexpected, _ = compare(old, new, expect=())
    w = max((len(n) for n in set(old) | set(new)), default=10)
    print(f'{"counter":<{w}}  {"old":>12}  {"new":>12}')
    for name in sorted(set(old) | set(new)):
        a, b = old.get(name), new.get(name)
        flag = '' if a == b else '   <-- MOVED'
        print(f'{name:<{w}}  {str(a):>12}  {str(b):>12}{flag}')
    print(f'\n{len(unexpected)} counter(s) moved.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
