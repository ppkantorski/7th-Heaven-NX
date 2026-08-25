#!/usr/bin/env python3
"""
ff7nx_ws.py -- the ONE entry point for 16:9, and the build-time data half.

WHY THIS MODULE EXISTS
======================
Five things in this folder claimed a piece of widescreen and disagreed with
each other about the model:

    ff7nx_widescreen.py   attempts 1 and 3 (`stretch`, `fit`) -- known bad
    ff7nx_framing.py      the v7 viewport widening       -- known bad
    ff7nx_fieldwide.py    parallax words + the clamp identity
    ff7nx_field169.py     wired framing+fieldwide together as `field`
    ff7nx_wsbake.py       the section-8 config bake, standalone only

`field` was labelled "16:9 -- recommended" in the GUI and is exactly the
build README-widescreen-v8 recorded as a hardware REGRESSION (bars still
present, character misaligned with the background, walkmesh wrong). Anyone
picking the obvious-looking option got the known-bad one.

This module owns the decision instead. It resolves the mode, and it owns
the only part of the feature that is finished, verifiable offline, and
cannot regress a frame: the DATA.

WHAT THE DATA HALF IS
=====================
FFNx does not compute widescreen. For fields it looks the answer up, per
field, in `CONFIG/widescreen/config.toml`, and then clamps the camera with

    field_trigger_header* h = *field_triggers_header;
    auto camera_range = h->camera_range;
    if (widescreen_enabled || enable_uncrop)
        camera_range = widescreen.getCameraRange();     // the config
    if (is_fieldmap_wide()) {
        camera_range.left += 1; camera_range.right -= 1;
        half_width = 160 + std::min(53, (right - left) / 2 - 160);
    }

    -- src/ff7/field/background.cpp:417

The override is a runtime object fed by a TOML file. We have neither. What
we do have is the packer: `_build_flevel` already rewrites `flevel.lgp`,
because that is how Cosmos Limit Break's 683 repainted section-9 chunks get
in. Writing the config's range into each field's **section 8** makes
`field_triggers_header->camera_range` correct at runtime by construction --
no runtime object, no per-field table in the module, no cave space, and the
result is falsifiable offline by reading the rebuilt archive back.

Measured on the shipping mod (README-45 §9): 711 fields, 647 (91.0%)
widescreen with Cosmos's config against 341 (48.0%) from the gate alone.
The config is not metadata. It is half the feature.

THE TWO STAGES, AND WHY THEY ARE SEPARATE
=========================================
`STAGE_CONTENT`  Bake the config's camera ranges into section 8, and emit
                 the per-field wide/not-wide table. No module patch at all.
                 exefs/main is not opened, so the 60 FPS set, the analog
                 patches and the field-background patches cannot be
                 disturbed by this in any way.

`STAGE_FRAMING`  The module patches that actually widen the picture, plus
                 the clamp compensation that only makes sense alongside
                 them. NOT enabled by any GUI choice. It is reachable by
                 environment variable for a deliberate hardware test and it
                 is documented in README-46 as unproven.

The split is not caution for its own sake. `ff7nx_fieldwide`'s own module
docstring says the clamp "visibly reduces camera travel if shipped alone",
and four hardware builds have now been spent learning that a partial
framing set is a regression rather than partial progress. Content-only is
the one configuration where every claim can be checked before the SD card
comes out.

THE CLAMP COMPOSITION, AND A SIMPLIFICATION IT BUYS
===================================================
`ff7nx_fieldwide` implements the clamp as an arithmetic identity on the
data rather than as 24 code caves: both clip functions only ever use the
range through `left + half_width` and `right - half_width`, so pulling the
range in by `half_width - 160` and leaving the stock `#0xa0` immediates
alone produces bit-identical bounds.

That module decides "is this field wide?" with FFNx's GATE
(`right - left >= 427`). With a config present that is **wrong**:
`is_fieldmap_wide()` is `widescreen.getMode() != WM_DISABLED`, and 306 of
Cosmos's fields are switched on by an explicit `mode = N` key while their
range is left alone (README-45 §8.2). `clamp_delta()` below uses the
RESOLVED mode instead, which is what FFNx uses.

The simplification that falls out: because the delta is computed at build
time from the resolved mode, the mode is already baked into the data. **The
clamp needs no per-field table in the module.** The 63-name table is needed
only by the framing stage, which reads the bit at runtime.

WHAT IS DELIBERATELY NOT FAKED
==============================
`h_offset`, `v_offset` and `reset_vertical_pos` are applied by FFNx to the
camera POINT, inside the same function, before clamping. A point shift is
not expressible as a range edit -- shifting the range moves the bounds, not
the camera. Fields using them are counted and reported by
`config_report()` rather than silently approximated.
"""
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ff7nx_wsdata as W                                       # noqa: E402
import ff7nx_camfit as CF                                      # noqa: E402

WIDESCREEN_ENV = 'SEVENTH_NX_WIDESCREEN'

# The value the GUI persists for the supported pipeline. Kept short and
# distinct from the three historical values so an old settings.json cannot
# be read as this one: `stretch`, `fit` and `field` are all known bad, and
# silently promoting one of them to "the recommended path" is precisely the
# failure this module was written to remove.
MODE_WS = 'ws'

# The same pipeline plus the 2D framing patches. A separate value rather
# than a checkbox because the two are not independent: the framing needs the
# baked camera ranges under it, and shipping it without them would widen the
# view of fields whose camera still stops where the 4:3 crop used to be.
MODE_WS_2D = 'ws-2d'

# The 3D half on top: game_w 854 in all three setviewport copies, and the
# field's own mode-2 viewport widened to match. Supersedes `ws-2d`; that one
# is kept because it is the build the first screenshots came from and it is
# the cheapest way to isolate a 2D problem from a 3D one.
MODE_WS_3D = 'ws-3d'

# `ws-2d` is NOT in here. It was the 2D-only measurement build, `ws-3d`
# supersedes it entirely, and `mode()` below folds it in.
#
# Why it is aliased in the MODE RESOLVER rather than migrated in the GUI:
# the GUI migration only fires if 7th_heaven_nx.py is also replaced AND the
# settings dialog is opened. Two builds were spent discovering that a saved
# `ws-2d` kept selecting the old patch set while the dropdown looked like it
# offered a new one. Aliasing here means it cannot happen again regardless
# of which files were copied or which screens were visited.
MODES = (MODE_WS, MODE_WS_3D)
FRAMING_MODES = (MODE_WS_3D,)

# Historical values. `mode()` maps them to off, and `legacy_mode()` reports
# them so the build log can say why nothing happened.
LEGACY_MODES = ('stretch', 'fit', 'field')

STAGE_CONTENT = 'content'
STAGE_FRAMING = 'framing'

# Kept so a framing run can also be forced on top of plain `ws` from a
# script or a CI job without editing settings.json.
FRAMING_ENV = 'SEVENTH_NX_WS_FRAMING'

# Two experiments that are real but unproven, each its own variable. Both
# default OFF. HANDOFF-48 §10.2: every multi-word build this project shipped
# produced an unreadable result; every single-word test produced a fact.
UNCROP_ENV = 'SEVENTH_NX_WS_UNCROP'        # mode-2 viewport height 448 -> 480

# PARALLAX IS NOW ON BY DEFAULT, and this variable turns it OFF.
#
# It shipped off for four handoffs with the note "not worth it until the
# framing exists to see it against". The framing exists, and hardware
# 2026-08 reported the exact artefact it predicts: layers 3 and 4 do not
# cull past the right edge, they WRAP, and with the wrap point at its 4:3
# value of 0 every parallax tile in the right expanded margin is shifted a
# whole layer width and lands back inside the 4:3 picture -- "the expanded
# assets are pushed inwards and are under the 4:3 field portion".
#
# `SEVENTH_NX_WS_PARALLAX=0` reproduces the old build for an A/B.
PARALLAX_ENV = 'SEVENTH_NX_WS_PARALLAX'    # layers 3/4 clip & wrap points

# The field render target. This is NOT an experiment -- it is the other half
# of the framing, and without it 16:9 bands. See HANDOFF-51 and
# ff7nx_fieldbuf.py: the field is drawn into a hardcoded 320x240 offscreen so
# the pre-rendered background lands 1:1, and squeezing 853 game units through
# those same 320 pixels is what produced the vertical bands. Widening the
# buffer restores the 1:1 and DEFINES the shader scale -- `WS_SCALE` is
# 320n/width, not a constant.
#
# `SEVENTH_NX_WS_FIELDBUF` picks the supersample step: 1 = 428x240 (confirmed
# on hardware), 2 = 854x480, 3 = 1280x720 (exactly 16:9). 0 leaves the buffer
# stock, which is the pre-HANDOFF-51 build and is kept only so the bands can
# be reproduced deliberately.
FIELDBUF_ENV = 'SEVENTH_NX_WS_FIELDBUF'    # see ff7nx_fieldbuf.SCALE_ENV

# The vertex shaders that carry the framing. The module patch and these two
# files are ONE change: shipping the module words without the shaders leaves
# everything stretched by 4:3 -> 16:9, and shipping the shaders without the
# module words squashes the picture into three quarters of a 4:3 target.
WS_SHADERS = ('tlmain_vv.glsl', 'lmain_vv.glsl')
WS_SHADER_SET = os.path.join('custom_shaders', 'wide_screen')


def _flag(name):
    return os.environ.get(name, '').strip().lower() in ('1', 'true', 'on',
                                                        'yes')


def parallax():
    """True unless SEVENTH_NX_WS_PARALLAX explicitly says otherwise."""
    raw = os.environ.get(PARALLAX_ENV, '').strip().lower()
    if raw in ('0', 'false', 'off', 'no'):
        return False
    return True


def _install_shaders(sdout, log=lambda *_: None):
    """
    Copy the two widescreen vertex shaders into the SD output.

    Returns the paths written. These live in romfs/ff7/shaders alongside the
    user's own sets; the `hd` and `hd_fxaa` sets people install are PIXEL
    shaders with different filenames, so there is no collision.
    """
    import shutil
    import build
    here = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.join(here, WS_SHADER_SET)
    dest_dir = os.path.join(sdout, 'atmosphere', 'contents', build.TITLE_ID,
                            'romfs', 'ff7', 'shaders')
    out = []
    missing = [n for n in WS_SHADERS
               if not os.path.exists(os.path.join(src_dir, n))]
    if missing:
        log('! 16:9 framing: %s missing from %s. The module words are on the '
            'card WITHOUT the shader that completes them, which will look '
            'STRETCHED, not wrong-in-an-interesting-way.'
            % (', '.join(missing), WS_SHADER_SET))
        return out
    os.makedirs(dest_dir, exist_ok=True)
    want = ws_scale()
    for name in WS_SHADERS:
        src = os.path.join(src_dir, name)
        dest = os.path.join(dest_dir, name)
        scale = _shader_scale(src)
        if scale is None:
            log('! %s has no `#define WS_SCALE` -- refusing to ship a shader '
                'whose scale cannot be read back' % name)
            return out
        shutil.copy2(src, dest)
        # The scale is DERIVED from the field buffer width, so it cannot be
        # a constant baked into the .glsl any more. It is written here, from
        # the same number the extents were computed from, which is what makes
        # the two halves incapable of drifting apart -- the failure mode
        # HANDOFF-49 §3 spent two builds on.
        if _write_shader_scale(dest, want) is None:
            log('! %s: could not rewrite `#define WS_SCALE`; removing it '
                'rather than shipping the wrong scale' % name)
            os.remove(dest)
            return out
        out.append(dest)
        log('  shader      %s  (WS_SCALE %.8f)' % (name, want))
    return out


def _write_shader_scale(path, value):
    """Rewrite `#define WS_SCALE` in place. Returns the old value, or None."""
    import re
    text = open(path).read()
    pat = re.compile(r'^([ \t]*#define[ \t]+WS_SCALE[ \t]+)([0-9.]+)',
                     re.MULTILINE)
    m = pat.search(text)
    if not m:
        return None
    old = float(m.group(2))
    # ALWAYS keep a decimal point. `%g` renders 1.0 as `1`, and
    # `gl_Position.x *= 1` is an int/float mismatch that GLSL ES refuses to
    # compile -- and a shader that fails to build does not fall back to
    # something sane, it takes the draw with it.
    lit = '%.8f' % value
    new = pat.sub(lambda mm: mm.group(1) + lit, text, count=1)
    if new == text and abs(old - value) > 1e-9:
        return None
    tmp = path + '.tmp'
    open(tmp, 'w').write(new)
    os.replace(tmp, path)
    return old


def _shader_scale(path):
    """The `#define WS_SCALE` a shader actually carries, or None."""
    import re
    with open(path) as f:
        m = re.search(r'^\s*#define\s+WS_SCALE\s+([0-9.]+)', f.read(),
                      re.MULTILINE)
    return float(m.group(1)) if m else None


def ff7nx_wsclamp_scale():
    import ff7nx_wsclamp
    return ff7nx_wsclamp.WS_SCALE


def fieldbuf():
    """
    The field render target this build wants, as
    {'width', 'height', 'scale', 'ws_scale'}, or None to leave it stock.
    """
    import ff7nx_fieldbuf
    return ff7nx_fieldbuf.preset(
        ff7nx_fieldbuf.env_scale(default=ff7nx_fieldbuf.DEFAULT_SCALE))


def ws_scale():
    """
    The `#define WS_SCALE` this build must ship, and the number the tile
    window extents have to be computed from.

    It is DERIVED from the field buffer width -- `320n / width` -- because
    that is what puts one background texel on a whole buffer pixel. Treating
    it as a constant is what HANDOFF-51 §2 is about. With the buffer left
    stock it falls back to ff7nx_wsclamp's historical 0.75, which is the
    build that bands.
    """
    p = fieldbuf()
    return p['ws_scale'] if p else ff7nx_wsclamp_scale()


# --------------------------------------------------------------------------
# mode resolution
# --------------------------------------------------------------------------
def _raw():
    return os.environ.get(WIDESCREEN_ENV, '').strip().lower()


def mode():
    """One of MODES, or '' (off). Never returns a legacy or retired value."""
    raw = _raw()
    if raw == MODE_WS_2D:
        return MODE_WS_3D          # retired; see MODES
    return raw if raw in MODES else ''


def legacy_mode():
    """The historical value the environment holds, or '' -- for reporting."""
    raw = _raw()
    return raw if raw in LEGACY_MODES else ''


def enabled():
    """Is the supported 16:9 pipeline switched on, in either mode?"""
    return mode() in MODES


def stage():
    """`STAGE_CONTENT` or `STAGE_FRAMING`. Meaningless when off."""
    if not enabled():
        return ''
    if mode() in FRAMING_MODES or os.environ.get(
            FRAMING_ENV, '').strip().lower() in ('1', 'true', 'on', 'yes'):
        return STAGE_FRAMING
    return STAGE_CONTENT


def wants_bake():
    """Should the config's camera ranges go into section 8?"""
    return enabled()


def wants_clamp():
    """
    Should the clamp compensation be composed on top of the bake?

    Only with the framing. On its own it is a straight loss of camera travel
    -- see ff7nx_fieldwide's module docstring and README-widescreen-v5.
    """
    return stage() == STAGE_FRAMING


def wants_framing():
    """Should exefs/main be patched? Never in the content stage."""
    return stage() == STAGE_FRAMING


# --------------------------------------------------------------------------
# finding the config
# --------------------------------------------------------------------------
def find_config(roots):
    """
    (config.toml, movie_config.toml, alternates, root) across mod caches.

    `roots` is an ORDERED sequence of extracted-mod directories, later
    winning, matching the packer's "last mod wins" rule everywhere else. A
    mod with no widescreen config contributes nothing and does not displace
    an earlier one's.
    """
    best = (None, None, [], None)
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            cfg, mov, alts = W.find_configs(root)
        except OSError:
            continue
        if cfg:
            best = (cfg, mov, alts, root)
    return best


def load(config_path, movie_path=None):
    """(config, movie_config) as plain dicts. Missing files give {}."""
    return W.load_toml(config_path), W.load_toml(movie_path)


# --------------------------------------------------------------------------
# the clamp identity, with the mode taken from the config
# --------------------------------------------------------------------------
HALF_WIDTH_43 = 160                 # the stock `#0xa0` immediates
HALF_WIDTH_CAP = 53                 # FFNx's std::min cap; 160 + 53 = 213


def half_width(range_size):
    """
    FFNx's `half_width` for an already-adjusted range size.

    background.cpp:433 --
        half_width = 160 + std::min(53, cameraRangeSize / 2 - 160)

    `cameraRangeSize` is `right - left` AFTER the +/-1 adjustment. C++
    integer division truncates toward zero and the size is positive
    everywhere this is reached, so `//` matches. The result is deliberately
    allowed below 160: a field switched on by an explicit `mode` key with a
    narrow range gets a SMALLER half_width than stock, which lets the camera
    travel further, and that is what FFNx does.
    """
    return HALF_WIDTH_43 + min(HALF_WIDTH_CAP, range_size // 2 - HALF_WIDTH_43)


def clamp_bounds(rng, wide):
    """
    (lo, hi) -- the horizontal bounds FFNx would clamp the camera point to.

    `wide` is `is_fieldmap_wide()`, i.e. the resolved mode is not
    WM_DISABLED. This mirrors background.cpp:417 exactly, including the
    one-unit adjustment, and it is the reference the identity below is
    checked against.
    """
    left, right = int(rng['left']), int(rng['right'])
    if not wide:
        return left + HALF_WIDTH_43, right - HALF_WIDTH_43
    left += 1
    right -= 1
    hw = half_width(right - left)
    return left + hw, right - hw


def clamp_delta(rng, wide):
    """
    How far each edge must move so the STOCK `+/-160` code lands on FFNx's
    bounds. Positive pulls the edges in, negative pushes them out.

    The identity, restated: the stock functions only ever use the range as
    `left + 160` and `right - 160`. Writing `left + d` and `right - d` into
    section 8 therefore makes them compute `left + d + 160` and
    `right - d - 160`, which are FFNx's bounds exactly when
    `d = 1 + half_width - 160`.
    """
    if not wide:
        return 0
    lo, _hi = clamp_bounds(rng, True)
    return lo - HALF_WIDTH_43 - int(rng['left'])


def clamped_range(rng, wide):
    """
    A copy of `rng` with the clamp identity applied, or None to leave it.

    Returns None rather than a degenerate range in three cases, all of which
    would be silent on a console and loud here:
      * the delta is zero (nothing to do),
      * an edge would leave int16,
      * the result would invert or leave less than the stock 320 units of
        view, which would clamp the camera to a point.
    """
    d = clamp_delta(rng, wide)
    if d == 0:
        return None
    out = dict(rng)
    out['left'] = int(rng['left']) + d
    out['right'] = int(rng['right']) - d
    if not (-0x8000 <= out['left'] <= 0x7FFF
            and -0x8000 <= out['right'] <= 0x7FFF):
        return None
    # `<=`, NOT `<`. FINDINGS-177, and the docstring above already said why.
    #
    # "less than the stock 320 units of view, which would clamp the camera to
    # a point" -- but the stock functions use the range ONLY as
    # `left + 160 .. right - 160`, so a range of EXACTLY 320 gives
    # `0 .. 0`. That is the point, and `< 320` let it through.
    #
    # MEASURED on build 74's archive, first 400 fields: 36 fields are written
    # to exactly 320 and therefore have NO camera travel at all --
    #
    #     vanilla range -> written    count
    #        336 -> 320                 1
    #        368 -> 320                 3
    #        376 -> 320                 1
    #        384 -> 320                14     <- las4_1 is here
    #        400 -> 320                11
    #        416 -> 320                 3
    #
    # `las4_1` (bottom of the Northern Cave) is one of the 384s: vanilla
    # -192..192 gives the stock code -32..32, i.e. 64 units of travel, and we
    # replace it with -160..160, which gives 0..0. The field holds 448 units
    # of art and the camera can no longer reach any of it.
    #
    # AND THE IDENTITY CANNOT BE ACHIEVED FOR THESE FIELDS ANYWAY. FFNx widens
    # the VIEW by using a larger `half_width` (191 for a 384 range); the stock
    # port's viewport is hardwired to +/-160 and editing section 8 cannot
    # change it. Writing a narrower range does not widen the view -- it only
    # moves the clamp, and here it removes the travel vanilla had.
    #
    # So leave them exactly as the game shipped them: not widened, but whole.
    # `<`, NOT `<=`. FINDINGS-177 IS RETRACTED, AND IT COST TWO REGRESSIONS.
    #
    # FINDINGS-177 changed this to `<=` so that a range collapsing to exactly
    # 320 -- a camera pinned to a point -- was refused, on the reasoning that
    # pinning "removes the travel vanilla had". That reasoning assumed the
    # port's view is the 320 units its `#0xa0` code believes. It is not: the
    # framing stage shows 426.67. Every unit of "travel" it preserved is a
    # unit of scrolling into art that does not exist.
    #
    # MEASURED on Cosmos's config, all 649 wide fields:
    #
    #     clamp written normally      561
    #     REFUSED by the guard         88     <- every one of them
    #     of those, FFNx would PIN     88        pinned by FFNx
    #
    # `md8_1` (Sector 8, before Aerith) and `las4_1` (bottom of the Northern
    # Cave) are two of the 88, and both were reported from hardware as black
    # pillars that appear as soon as the camera moves. Their config says
    # -192..192, FFNx's `half_width` is then 191, and FFNx clamps them to
    # 0..0. The mod told us exactly what to do and this line discarded it.
    #
    # PINNING IS NOT A LOSS HERE, IT IS THE ANSWER. background.cpp:433's own
    # comment on that formula reads "This centers the background if
    # necessary", and `md8_1`'s own script scrolls to (0, 0) -- the framing
    # the reporter describes as correct. See FINDINGS-187.
    if out['right'] - out['left'] < 2 * HALF_WIDTH_43:
        return None
    out['width'] = out['right'] - out['left']
    return out


# --------------------------------------------------------------------------
# THE ONE DIAGONAL CAMERA RAIL. `ship_1`, AND ONLY `ship_1`, HAS
# field_trigger_header.field_14[0] == 2 IN THE VANILLA ARCHIVE.
#
# A normal field has a rectangular camera box. Tightening that box to the
# painted 16:9 canvas is safe: the player cannot need the camera outside the
# picture. A mode-2 field is different. The four range edges are the TWO END
# POINTS of a diagonal rail, and the walkmesh was authored against that rail.
# Replacing them with Cosmos's art-tight rectangle changes both the rail's
# length and its slope.
#
# Measured on ship_1:
#
#   vanilla trigger envelope       L -320  T -224  R 296  B 184
#   Cosmos + clamp identity        L -266  T -176  R 168  B 176
#   current lower-left / upper-right          (-106,56) / (8,-56)
#   vanilla envelope + identity lower-left              (-106,64)
#   wanted upper-right endpoint                          (82,-104)
#
# Build 173 restored all four edges and hardware proved why that is wrong:
# changing bottom 176 -> 184 moved the already-correct lower-left anchor from
# y=56 to y=64, revealing an eight-unit black band below the painted ship.
# FFNx's mode-2 formula makes the ownership explicit: left/bottom describe
# that lower-left endpoint, while right/top describe the upper-right one.
# Preserve the proven left/bottom pair and restore ONLY right/top.
#
# The walkmesh reaches projected x ~= 228. At x=8 the 427-unit 16:9 frame
# ends at x=221, so the controllable character genuinely crosses the screen
# edge. This is not a rounding error and not the normal clamp: the config has
# shortened the only diagonal rail in the game.
#
# The correction runs AFTER camera-fit, because camera-fit intentionally
# tightens to art and would otherwise put the bad endpoint back. It is gated
# by the field's own structural byte, not by its name, and is monotonic: a
# future config that already preserves at least the original upper-right
# endpoint is left alone. With one mode-2 field in the archive that means one
# field changes today and ordinary fields cannot regress.
DIAGONAL_RAIL_MODE = 2
DIAGONAL_RAIL_OFF_ENV = 'SEVENTH_NX_NO_DIAGONAL_RAIL'


def preserve_diagonal_rail(before, plan, resolved, field_modes):
    """
    Return (new_plan, changes), preserving a mode-2 field's original rail.

    `field_modes` is `{field: section8[0x14]}`. `changes` holds
    `(name, old_endpoint, new_endpoint)` and is deliberately sufficient for
    the build log and the regression test without re-reading the archive.
    """
    out = dict(plan)
    changes = []
    if os.environ.get(DIAGONAL_RAIL_OFF_ENV) == '1':
        return out, changes

    for name, flag in sorted(field_modes.items()):
        if flag != DIAGONAL_RAIL_MODE:
            continue
        if resolved.get(name, {}).get('mode') == W.WM_DISABLED:
            continue
        base = before.get(name)
        if base is None:
            continue
        target = clamped_range(base, True)
        if target is None:
            continue
        current = out.get(name, base)
        old_ep = (int(current['right']) - HALF_WIDTH_43,
                  int(current['top']) + 120)
        repaired = dict(current)
        repaired['right'] = int(target['right'])
        repaired['top'] = int(target['top'])
        new_ep = (int(repaired['right']) - HALF_WIDTH_43,
                  int(repaired['top']) + 120)
        # Mode 2 runs from lower-left to upper-right. Only replace a rail the
        # config made shorter on that end; never override a future, better
        # authored envelope.
        if new_ep[0] <= old_ep[0] and new_ep[1] >= old_ep[1]:
            continue
        out[name] = {k: int(repaired[k]) for k in W.RANGE_ORDER}
        changes.append((name, old_ep, new_ep))
    return out, changes


# --------------------------------------------------------------------------
# the plan
# --------------------------------------------------------------------------
def plan_ranges(before, config, movie_config=None, clamp=False):
    """
    ({field: new_range}, resolved, stats) for a {field: range} mapping.

    `before` is what is in the archive right now -- vanilla or already
    modded, it does not matter, because the config's values are absolute and
    the clamp identity is computed from the resolved range.

    Only fields whose four shorts actually CHANGE appear in the plan. Every
    entry in the plan costs a full LZSS pass at build time and most fields
    do not need one.
    """
    resolved = W.resolve(before, config or {}, movie_config or {})
    plan = {}
    n_config = n_clamp = 0
    for name, info in resolved.items():
        old = before[name]
        rng = dict(info['range'])
        changed_by_config = any(int(rng[k]) != int(old[k])
                                for k in W.RANGE_ORDER)
        if changed_by_config:
            n_config += 1
        if clamp:
            got = clamped_range(rng, info['mode'] != W.WM_DISABLED)
            if got is not None:
                rng = got
                n_clamp += 1
        if any(int(rng[k]) != int(old[k]) for k in W.RANGE_ORDER):
            plan[name] = {k: int(rng[k]) for k in W.RANGE_ORDER}
    stats = dict(W.summarise(resolved))
    stats['planned'] = len(plan)
    stats['from_config'] = n_config
    stats['from_clamp'] = n_clamp
    return plan, resolved, stats


def config_report(config):
    """
    What the config asks for that a range edit cannot express.

    Returned rather than logged so the caller decides where it goes, and
    counted rather than ignored so the gap is visible in the build log
    instead of showing up as "the camera is in the wrong place" later.
    """
    point_shift = []
    unknown = W.unknown_keys(config or {})
    for name, entry in (config or {}).items():
        if not isinstance(entry, dict):
            continue
        if any(k in entry for k in
               ('h_offset', 'v_offset', 'reset_vertical_pos')):
            point_shift.append(name)
    return {'point_shift': sorted(point_shift), 'unknown_keys': unknown}


# --------------------------------------------------------------------------
# applying it to a live lgp.Archive, inside the packer's own field loop
# --------------------------------------------------------------------------
def _section8_of(raw, lgp):
    return lgp.split_sections(raw)


def apply_to_flevel(archive, payloads, config, movie_config=None,
                    encode=None, clamp=False, table_path=None, log=print):
    """
    Bake the resolved camera ranges into `archive`, honouring `payloads`.

    `payloads` is the packer's {field: encoded bytes} of replacements
    already decided by the mod passes. A field present there is decoded,
    edited and re-encoded so the mod's own section 9 survives; a field
    absent is taken from the archive. Either way the result goes back into
    `payloads`, which is what `archive.replace()` is given.

    Returns a stats dict. Raises nothing: a field that cannot be read is
    reported and skipped, because a widescreen camera range is not worth
    failing a build over.
    """
    import lgp

    encode = encode or (lambda raw: archive.encode_field(raw))

    # Read the current range of every field. Decompressing only as far as
    # section 8 rather than through section 9 -- the background, and most of
    # the field -- is the difference between two seconds and thirty-five for
    # the whole archive.
    before = {}
    field_modes = {}
    for name in archive.names():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        payload = payloads.get(name, entry.get('payload'))
        if not payload:
            continue
        try:
            head = W._lzss_head(payload, 42)
            starts = struct.unpack('<9I', head[6:42])
            body = starts[W.SECTION_TRIGGERS] + 4      # skip section length
            # One byte beyond the four range shorts carries field_14[0]. It
            # is the engine's diagonal-rail discriminator and costs no full
            # section-9 decompression to retain here.
            data = W._lzss_head(payload, body + max(W.SECTION8_MIN_LEN, 0x15))
            before[name] = W.read_section8_range(
                data[body:body + W.SECTION8_MIN_LEN])
            field_modes[name] = data[body + 0x14]
        except Exception:                                      # noqa: BLE001
            continue

    plan, resolved, stats = plan_ranges(before, config, movie_config,
                                        clamp=clamp)
    stats['read'] = len(before)
    stats['written'] = 0

    # ------------------------------------------------------------ camfit
    # The clamp identity above is exact about the ARITHMETIC and blind about
    # the VIEW. `clamped_range` reproduces FFNx's bounds, but FFNx's bounds
    # were computed for FFNx's view; this build's framing stage shows 426.67
    # field units where the port's `#0xa0` code assumes 320. Fields whose art
    # stops inside that extra 106.67 show a black bar at the ends of the
    # scroll -- 108 of them on build 79, `las4_1` among them, reported from
    # hardware as "a black bar on the left, and on the right when I run
    # right". ff7nx_camfit measures the art in section 9 and tightens the
    # clamp until the window cannot leave it. It only ever tightens, so a
    # field that renders correctly today comes out a no-op.
    if clamp and not CF.disabled():
        final = dict(before)
        final.update(plan)
        wide = {n: r for n, r in final.items()
                if resolved.get(n, {}).get('mode') != W.WM_DISABLED}

        def _raw_of(name, _c={}):
            if name in _c:
                return _c[name]
            entry = archive.index.get(name)
            raw = None
            if entry is not None:
                try:
                    payload = payloads.get(name)
                    raw = (lgp.lzs_decompress(payload[4:]) if payload
                           else archive.decompressed(entry))
                except Exception:                              # noqa: BLE001
                    raw = None
            _c.clear()          # one field at a time; these are megabytes
            _c[name] = raw
            return raw

        try:
            fitted, fstats = CF.fit_plan(_raw_of, wide, log=log)
        except Exception as exc:                               # noqa: BLE001
            fitted, fstats = {}, None
            log('  ! widescreen: camera fit skipped (%s)' % exc)
        if fstats is not None:
            plan.update(fitted)
            stats['camfit'] = fstats
            log('  widescreen: camera fit -- %d field(s) tightened so the '
                '16:9 window cannot scroll off the art (worst bare band '
                '%d -> %d units); %d field(s) have less than %d units of art '
                'and were left alone (set SEVENTH_NX_NO_CAMFIT=1 to disable)'
                % (fstats['fitted'], fstats['worst_before'],
                   fstats['worst_after'], fstats['short'], CF.NEEDED))
            if fstats.get('fitted_y'):
                log('  widescreen: camera fit (VERTICAL) -- %d field(s) '
                    'fitted so the 240-unit frame cannot scroll off the art '
                    '(worst bare band %d -> %d units)'
                    % (fstats['fitted_y'], fstats['worst_y_before'],
                       fstats['worst_y_after']))
            if fstats.get('inverted'):
                log('    %d field(s) had INVERTED vertical bounds -- '
                    'top+120 > bottom-120, so the camera landed on whichever '
                    'of the two clamps ran last and the picture lost 8 units '
                    'at one edge or the other: %s'
                    % (len(fstats['inverted']),
                       ', '.join(sorted(fstats['inverted']))))
            for nm, band in fstats['scripted']:
                log('    %s: range is far wider than its art (%d units bare) '
                    '-- left alone, that range is not describing this '
                    'background' % (nm, band))

    # A rectangular art fit cannot describe the one diagonal camera rail.
    # Restore that rail from the field's own trigger envelope after every
    # generic range transform has finished. See preserve_diagonal_rail().
    plan, diagonal = preserve_diagonal_rail(before, plan, resolved,
                                            field_modes)
    stats['diagonal_rail'] = diagonal
    if diagonal:
        log('  widescreen: diagonal camera rail -- %d field(s) kept the '
            'playable upper-right endpoint the art-tight rectangle removed '
            '(set %s=1 to disable)' %
            (len(diagonal), DIAGONAL_RAIL_OFF_ENV))
        for name, old_ep, new_ep in diagonal:
            log('    %s: upper-right endpoint (%d,%d) -> (%d,%d)'
                % (name, old_ep[0], old_ep[1], new_ep[0], new_ep[1]))

    # The wide/not-wide table the FRAMING stage will need. Emitted here
    # because `resolved` exists here and nowhere else, and having it on disk
    # is what lets that work start without another full build. The content
    # stage does not read it -- the clamp carries the mode in the data.
    if table_path:
        try:
            info = emit_table(resolved, table_path,
                              source=stats.get('source', ''))
            stats['table'] = info
            log('  widescreen: per-field table -> %s  (default %s, %d named '
                'exception(s), %d bytes%s)'
                % (os.path.basename(table_path),
                   'WIDE' if info['wide_default'] else 'NOT WIDE',
                   info['listed'], info['bytes'],
                   ', %d ambiguous' % len(info['ambiguous'])
                   if info['ambiguous'] else ''))
        except Exception as exc:                               # noqa: BLE001
            log('  ! widescreen: per-field table not written (%s)' % exc)

    if not plan:
        log('  widescreen: %d field(s) read, the config changes none of '
            'their camera ranges' % len(before))
        return stats

    written = 0
    for name in sorted(plan):
        entry = archive.index.get(name)
        if entry is None:
            continue
        try:
            payload = payloads.get(name)
            raw = (lgp.lzs_decompress(payload[4:]) if payload
                   else archive.decompressed(entry))
            parts = _section8_of(raw, lgp)
            parts[W.SECTION_TRIGGERS] = W.write_section8_range(
                parts[W.SECTION_TRIGGERS], plan[name])
            payloads[name] = encode(lgp.join_sections(parts))
            written += 1
        except Exception as exc:                               # noqa: BLE001
            log('  ! widescreen: %s camera range not written (%s)'
                % (name, exc))
    stats['written'] = written
    # Kept so the caller can hand them to verify_flevel() after the archive
    # is written -- see that function for why re-deriving is not equivalent.
    stats['before'] = before
    stats['plan'] = plan
    return stats


def verify_flevel(path, before, plan):
    """
    Read a REBUILT archive back and check the plan landed and nothing else
    moved. Returns (ok, problems).

    Takes the plan rather than re-deriving it from the rebuilt file, and
    that distinction is load-bearing. The config bake is ABSOLUTE and so
    idempotent -- re-resolving a baked field gives the same numbers. The
    clamp is RELATIVE: it pulls each edge in by `half_width - 159`, and
    re-deriving it from an already-clamped range would pull them in a second
    time and then report the correct archive as wrong. `_build_flevel`
    always starts from the vanilla archive, so double application cannot
    happen in a build, but a verifier that re-derives would have made this
    look broken.

    This is the whole point of doing the data half as data: the check needs
    no console.
    """
    import lgp
    import ff7nx_wsbake as B

    after = B.ranges_from_archive(lgp.Archive(path))
    return W.verify_bake(before, after, plan)


# --------------------------------------------------------------------------
# THE 2D PROJECTION -- what five attempts were looking for
# --------------------------------------------------------------------------
#
# `tlmain_vv.glsl` (the port's TLVERTEX vertex shader, shipped as GLSL source
# in romfs/shaders/) is three lines long:
#
#     vec4 position = VertexCoord;
#     position.w = 1.0 / position.w;  position.xyz *= position.w;
#     gl_Position = projectionMatrix * position;
#
# So every 2D thing the game draws -- field backgrounds, menus, text, window
# boxes, movie quads -- reaches the screen through ONE uniform matrix. That
# matrix is FFNx's `backendProjMatrix`, and FFNx's entire widescreen framing
# is one line about it (`src/renderer.cpp:640`):
#
#     bx::mtxOrtho(backendProjMatrix,
#         widescreen_enabled ? wide_viewport_x                       : 0.0f,
#         widescreen_enabled ? wide_viewport_width + wide_viewport_x : game_width,
#         ...);
#
# i.e. the 2D projection spans game-space X from -107 to +747 instead of 0 to
# 640. The 4:3 region [0, 640] then sits exactly centred in it, which is why
# FFNx's menus stay 4:3 with no patch at all, and why its ~80 patch sites are
# all for full-screen OVERLAYS rather than for the UI.
#
# WHERE IT IS HERE
# ----------------
# +0x10D9D70 is the generic draw-submission helper (README-46 §2). At
# +0x10D9FF0 it tests the vertex type and, for type 3 -- TLVERTEX -- builds
# an orthographic matrix ON THE STACK from mov/movk immediates:
#
#     +0x10DA018  mov  w8, #0xcccd            \  0x3B4CCCCD = 2.0f/640.0f
#     +0x10DA01C  movk w8, #0x3b4c, lsl #16   /
#     +0x10DA020  str  w8, [sp, #0x28]           _11
#     +0x10DA024  mov  w8, #0x8889            \  0xBB888889 = -2.0f/480.0f
#     +0x10DA028  movk w8, #0xbb88, lsl #16   /
#     +0x10DA02C  stur x8, [sp, #0x3c]           _22, _23
#     +0x10DA030  mov  x8, #0x3f80000000000000   _32=0, _33=1.0
#     +0x10DA038  mov  x8, #-0x4080000000000000  _34=0, _41=-1.0
#     +0x10DA040  mov  x8, #0x3f800000        \  _42=1.0, _43=-0.0
#     +0x10DA044  movk x8, #0x8000, lsl #48   /
#     +0x10DA05C  str  w8, [sp, #0x64]           _44=1.0
#
# All sixteen floats are accounted for; it is exactly ortho(0, 640, 480, 0).
#
# **This is why every constant scan came back empty.** 0x3B4CCCCD is not in
# `.rodata` -- it is assembled in `.text` out of two 16-bit immediates. Five
# attempts looked for a projection and none of them found this, because the
# number they were searching for does not exist as a number anywhere in the
# file.
#
# THE PATCH
# ---------
# Three in-place words. No cave, no displaced instruction, no cave budget.
#
#   _11:  2/640 = 0.003125    ->  2/854 = 0.0023419204
#   _41:  -1.0                ->  -0.75
#
# `_41` wants -640/854 = -0.7494145, whose low 16 bits are 0xD9A1, and the
# single `movz x8, #imm, lsl #48` that writes it can only set the top 16.
# -0.75 is the nearest value it CAN encode, and the cost of the rounding is
# measured rather than waved at: the implied ortho becomes
# (-106.75, 747.25) -- still exactly 854 units wide -- and the whole image
# shifts left by 0.25 game units, which is **0.56 of one pixel** at 720p.
# `tests/test_ws.py` asserts the width is exactly 854 and the error is under
# one pixel, so a future edit cannot quietly turn this into a real offset.
#
# WHAT THIS DOES AND DOES NOT COVER
# ---------------------------------
# It is the whole 2D half. It is NOT the 3D half: models, the battle scene
# and the world map do not go through this matrix (type 0..2 take the branch
# at +0x10DA098, which uses the game's own projection composed with
# `d3dviewport_matrix`). With this patch and nothing else, 2D is correct and
# 3D is horizontally stretched by 854/640. See README-47 §3 for the 3D half.
GAME_W_43 = 640
WIDE_VIEWPORT_X = -107                  # FFNx src/widescreen.h
WIDE_VIEWPORT_WIDTH = 854               # 640 * 4/3

PROJECTION_2D_PATCHES = [
    {
        'name': '2D ortho _11: 2/640 -> 2/854 (lo half)',
        'va': 0x10DA018,
        'expect': 'A8 99 99 52',        # mov w8, #0xcccd
        'set':    'E8 5C 8F 52',        # mov w8, #0x7ae7
    },
    {
        'name': '2D ortho _11: 2/640 -> 2/854 (hi half)',
        'va': 0x10DA01C,
        'expect': '88 69 A7 72',        # movk w8, #0x3b4c, lsl #16
        'set':    '28 63 A7 72',        # movk w8, #0x3b19, lsl #16
    },
]

# `_41` decides WHERE the 854-unit span sits, and the two stages want it in
# different places. It is a separate patch for that reason.
#
#   STAGE_2D   ortho(-106.75, 747.25).  The 4:3 crop is centred, so the UI
#              is centred -- but the FIELD, whose own viewport is still
#              (0, 0, 640, 448), only paints the middle 4:3 and the margins
#              are empty. That is the build in the first screenshots.
#   STAGE_3D   ortho(0, 854), `_41` left stock. The FIELD viewport becomes
#              (0, 0, 854, 448) and lands exactly on the span; the UI, still
#              640 wide at x=0, sits 107 units left of centre until the
#              x += 107 cave lands (§4 below).
ORTHO_ORIGIN_PATCH = {
    'name': '2D ortho _41: -1.0 -> -0.75 (span -106.75..747.25)',
    'va': 0x10DA038,
    'expect': '08 F0 F7 D2',            # mov x8, #-0x4080000000000000
    'set':    '08 E8 F7 D2',            # mov x8, #-0x40c0000000000000
}

# --------------------------------------------------------------------------
# THE 3D HALF
# --------------------------------------------------------------------------
#
# Types 0..2 skip the ortho entirely and use the game's own projection
# composed with `d3dviewport_matrix`, which all three copies of
# `common_setviewport` build as
#
#     _11 = w / game_w
#     _41 = ((x + w/2) - game_w/2) / (game_w/2)
#
# With the frame now 854 units wide and `game_w` still 640, 3D fills
# NDC [-1, 1] = the whole 854 units, i.e. **stretched by 854/640 = 1.33x**.
# That is what the battle screenshot shows, and what the field models show.
#
# The fix is `game_w := 854`, and the reason it is the whole fix -- rather
# than needing a matching `x` offset -- is worth writing out, because the
# obvious version of this analysis says otherwise:
#
#   caller             x, w        _11            _41
#   UI / battle        0, 640      640/854=0.749  ((0+320)-427)/427 = -0.2506
#   field, widened     0, 854      854/854=1.000  ((0+427)-427)/427 =  0
#
# The field lands exactly right -- full width, centred, unstretched -- with
# no viewport `x` change at all, because 854/2 is exactly the centre of a
# 0..854 span. The UI keeps its correct 4:3 SCALE (0.749) but sits 107 units
# left of centre, and that is the one thing this set does not fix.
#
# Three in-place words. Each `ldr wN, [xM, #0x954]` is followed by
# `ldr wM', [xM, #0x958]` reading the SAME base register, and in all three
# the base is not the register being written, so removing the first load
# leaves the second intact. Checked, not assumed.
GAME_W_PATCHES = [
    {
        'name': 'game_w 640 -> 854, gfx_drv_setviewport',
        'va': 0x10D67F4,
        'expect': '2B 55 49 B9',        # ldr w11, [x9, #0x954]
        'set':    'CB 6A 80 52',        # mov w11, #0x356
    },
    {
        'name': 'game_w 640 -> 854, end_scene state restore (+0x10D9370)',
        'va': 0x10D9480,
        'expect': 'AF 55 49 B9',        # ldr w15, [x13, #0x954]
        'set':    'CF 6A 80 52',        # mov w15, #0x356
    },
    {
        'name': 'game_w 640 -> 854, per-draw helper (+0x10D9D70)',
        'va': 0x10D9E60,
        'expect': 'D0 55 49 B9',        # ldr w16, [x14, #0x954]
        'set':    'D0 6A 80 52',        # mov w16, #0x356
    },
]

# `field_set_mode` mode 2 -- the 2x path fields actually use. Both writes
# come off one `mov w24, #0x140`, so 0xCFF1F4 and 0xCFF1FC move together,
# which is what `field_apply_2D_translation_float_64314F` needs (v8's
# Error 2: the models were projecting through a 320 half-width while the
# background was drawn through 854).
FIELD_MODE2_PATCHES = [
    {
        'name': 'field mode-2 viewport width 640 -> 854',
        'va': 0x9298D4,
        'expect': '08 50 80 52',        # mov w8, #0x280
        'set':    'C8 6A 80 52',        # mov w8, #0x356
    },
    {
        'name': 'field mode-2 half-width 320 -> 427 (0xCFF1F4 and 0xCFF1FC)',
        'va': 0x929938,
        'expect': '18 28 80 52',        # mov w24, #0x140
        'set':    '78 35 80 52',        # mov w24, #0x1ab
    },
]


def ortho_2d(patched=True, shift_origin=True):
    """
    (left, right) of the 2D projection the module will build.

    Derived from the PATCH BYTES rather than restated from the comment, so a
    test checks the encoding and not a paraphrase of it.
    """
    import struct as _s

    def word(p):
        return _s.unpack('<I', bytes(int(b, 16)
                                     for b in p.split()))[0]
    if patched:
        lo = (word(PROJECTION_2D_PATCHES[0]['set']) >> 5) & 0xFFFF
        hi = (word(PROJECTION_2D_PATCHES[1]['set']) >> 5) & 0xFFFF
        m41 = ((word(ORTHO_ORIGIN_PATCH['set' if shift_origin else 'expect'])
                >> 5) & 0xFFFF) << 16
    else:
        lo = (word(PROJECTION_2D_PATCHES[0]['expect']) >> 5) & 0xFFFF
        hi = (word(PROJECTION_2D_PATCHES[1]['expect']) >> 5) & 0xFFFF
        m41 = ((word(ORTHO_ORIGIN_PATCH['expect']) >> 5) & 0xFFFF) << 16
    _11 = _s.unpack('<f', _s.pack('<I', (hi << 16) | lo))[0]
    _41 = _s.unpack('<f', _s.pack('<I', m41))[0]
    return (-1.0 - _41) / _11, (1.0 - _41) / _11


def projection_2d_spec():
    return {
        'name': '2D projection 0..640 -> -107..747 (16:9)',
        'patches': [dict(p) for p in PROJECTION_2D_PATCHES],
    }


# --------------------------------------------------------------------------
# VERTICAL UNCROP -- the bars above and below
# --------------------------------------------------------------------------
#
# The field's mode-2 viewport is 448 units tall inside a 480-unit frame.
# 448/480 = 93.3%, so 6.7% of the height is black: **48 px at 720p, split
# top and bottom.** Widening the frame horizontally does nothing to it,
# which is why those bars survived every build so far.
#
# FFNx calls this `enable_uncrop` and does it in the scissor
# (`Renderer::setScissor`, renderer.cpp:1668):
#
#     if (enable_uncrop && y == 16 && height == 448) { y = 0; height = 480; }
#
# Here it is the two immediates `field_set_mode` pushes for mode 2. Note
# both are `ORR wN, wzr, #imm` (a bitmask immediate), not `MOVZ` -- replacing
# them with `MOVZ` of the new value is equivalent and is what these patches
# do.
#
# This needs taller background art to reveal, exactly as the horizontal half
# needs wider art. Cosmos Limit Break ships it: its "Background Uncrop"
# option is this feature. With a mod that does NOT ship it, the extra 32
# units are whatever the field's own art has there -- usually more of the
# same, occasionally nothing.
#
# `ff7nx_fieldwide` deliberately left the parallax layers' `#0x100` (256,
# top_offset) and `#0x70` (112, half_height) alone, calling them "the
# enable_uncrop question". They are still untouched here, so layers 3 and 4
# keep their 4:3 vertical wrap; if a scrolling sky shows a seam at the top
# once this is on, those two are the reason.
UNCROP_PATCHES = [
    {
        'name': 'field mode-2 viewport height 448 -> 480 (uncrop)',
        'va': 0x9298BC,
        'expect': 'E8 0B 1A 32',        # orr w8, wzr, #0x1c0
        'set':    '08 3C 80 52',        # mov w8, #0x1e0
    },
    {
        'name': 'field mode-2 half-height 224 -> 240 (0xCFF200)',
        'va': 0x929964,
        'expect': 'E8 0B 1B 32',        # orr w8, wzr, #0xe0
        'set':    '08 1E 80 52',        # mov w8, #0xf0
    },
]


# --------------------------------------------------------------------------
# the framing stage -- environment only, and unproven
# --------------------------------------------------------------------------
def apply_module(sdout, dump, log=lambda *_: None, produced=()):
    """
    Patch exefs/main for the framing stage. Returns newly-produced paths.

    NOTHING happens unless BOTH `SEVENTH_NX_WIDESCREEN=ws` and
    `SEVENTH_NX_WS_FRAMING=1` are set. There is no GUI path to this.

    WHAT IT SHIPS
    -------------
    Two things that are one change, plus the field's tile window.

      A. `gfx_drv_init`'s logical width, 4:3 -> 16:9 (`ff7nx_widescreen`'s
         four words). Without this the render target stays 1440x1080 and
         the presentation blit at +0x10DAF60 pillarboxes it into
         0.125..0.875 of the screen -- the black bars, measured.
      B. `tlmain_vv.glsl` and `lmain_vv.glsl` with `gl_Position.x *= 0.75`.
         This is FFNx's `widescreenScale`, reproduced in the shader instead
         of the module, and it covers BOTH draw paths: the type-3 ortho and
         the game's own projection.
      C. `ff7nx_wsclamp`'s four extents, so the field background's tile loop
         actually emits tiles out to the edges of the wider frame.
      D. `ff7nx_fieldbuf`'s field render target, 320x240 -> 428x240. This is
         what stops the vertical bands (HANDOFF-51) and it is what DEFINES
         B's number: `WS_SCALE = 320n / width`, not a constant. Confirmed on
         hardware 2026-08. `SEVENTH_NX_WS_FIELDBUF=0` leaves it stock, which
         reproduces the banded build on purpose.

    A and B are inseparable. A alone stretches everything by 4:3 -> 16:9;
    B alone squashes the picture into three quarters of a 4:3 target.
    `_install_shaders` refuses to ship B if its `#define WS_SCALE` disagrees
    with the number C's extents were computed from.

    WHAT IT DELIBERATELY NO LONGER SHIPS
    ------------------------------------
    Three sets that hardware contradicted (HANDOFF-48 §9):

      * `game_w := 854` in the three `setviewport` copies. FFNx never
        changes `game_width`; it scales the game's own projection matrix.
        Shipping this stretched the entire UI on hardware. B does the same
        job correctly.
      * The 2D ortho words. Superseded by B -- shipping both would apply
        the scale twice.
      * The field mode-2 viewport group. `+0x9298D4` moved MODELS on
        hardware and left the background untouched; it is a model-placement
        rect, not a background extent.

    Two more are available but OFF, one environment variable each, because
    each needs its own hardware test: `SEVENTH_NX_WS_UNCROP` (mode-2
    viewport height) and `SEVENTH_NX_WS_PARALLAX` (layers 3 and 4).

    WHAT WILL STILL BE WRONG
    ------------------------
    Battle is letterboxed above the UI band; roughly fifty full-screen 2D
    overlay sites (swirl, battle enter/fade, summon flashes) have their
    640/320 hardcoded and will cover only the middle 4:3; and the margins
    are not cleared, so a bright sprite sliding over them can leave a trail.
    All three are stated in the build log too, so a hardware report can say
    "and this other thing" rather than re-finding them.
    """
    if not wants_framing():
        # Silence here cost a whole build. `ws` bakes camera ranges into
        # flevel and never opens exefs/main, so the console still shows 4:3
        # and the log said nothing about why. Say it.
        if enabled():
            log('')
            log('16:9: DATA STAGE ONLY -- the camera ranges are baked into '
                'flevel.lgp, but exefs/main was NOT touched, so the picture '
                'will still be 4:3 on the console.')
            log('     For real widescreen pick "16:9 widescreen" in '
                'Settings -> Display. This entry ("Data only") exists to '
                'test the data half on its own.')
        return []
    if dump is None or not getattr(dump, 'nso', None):
        log('! 16:9 framing: needs exefs/main from a full game dump; skipped')
        return []

    import build

    dest = os.path.join(sdout, 'atmosphere', 'contents', build.TITLE_ID,
                        'exefs', 'main')
    fresh = {os.path.normpath(os.path.abspath(p)) for p in produced}
    built = os.path.normpath(os.path.abspath(dest)) in fresh
    src = dest if built else dump.nso

    # Carried over from apply_fps_patches and apply_widescreen verbatim,
    # because the failure it prevents is the worst one this packer has had:
    # a pass that based on the dump's stock module reverted 110 word patches
    # and 24 code caves without a line in the log.
    if not built and os.path.exists(dest):
        try:
            same = (os.path.getsize(dest) == os.path.getsize(dump.nso)
                    and open(dest, 'rb').read() == open(dump.nso, 'rb').read())
        except OSError:
            same = False
        if not same:
            log(f'! 16:9 framing: {dest} already holds a module this build '
                f'did not produce. Basing on the dump would throw those '
                f'patches away, so nothing was written.')
            return []

    if _raw() == MODE_WS_2D:
        log('')
        log('note: settings.json still says "ws-2d". That was the 2D-only '
            'measurement build and it is retired -- running ws-3d instead.')
    log('')
    log('applying 16:9 FRAMING (HANDOFF-49) ...')
    log(f'  base main   {src}'
        + ('   (earlier pass output)' if built else '   (from dump)'))

    from pathlib import Path
    import nso_patcher
    import nxmap
    import ff7nx_widescreen
    import ff7nx_wsclamp
    import ff7nx_fieldbuf

    buf = fieldbuf()
    scale = ws_scale()
    if buf:
        log('  field buffer %dx%d  (%gx), WS_SCALE %.8f'
            % (buf['width'], buf['height'], buf['scale'], scale))
        extra = ff7nx_fieldbuf.memory_delta(buf['width'], buf['height'])
        log('  field render targets: %.2f MB (%d of them), %+.2f MB vs stock'
            % (ff7nx_fieldbuf.memory_cost(buf['width'], buf['height']),
               ff7nx_fieldbuf.TARGET_COUNT, extra))
        if extra > ff7nx_fieldbuf.MEMORY_WARN_MB:
            log('  ! that comes out of the same pool the field background')
            log('    PAGES allocate from, and the page budget was MEASURED')
            log('    at the stock 320x240 buffer. field_load_textures aborts')
            log('    the whole loop on the first page it cannot allocate, and')
            log('    every page after it keeps handle 0 and never draws -- so')
            log('    those tiles show whatever the buffer already held.')
            log('    If parts of a heavy field (7-8 pages: nmkin_*, nrthmk,')
            log('    nvmkin*) come out a flat colour, LOWER the field')
            log('    background memory budget or drop this to 1x.')
    else:
        log('  field buffer LEFT STOCK at 320x240 '
            '(SEVENTH_NX_WS_FIELDBUF=0) -- this is the build with the '
            'vertical bands. See HANDOFF-51.')

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + '.ws-tmp'
    try:
        nso = nso_patcher.read_nso(Path(src))
        applied = []
        # One transaction. nso_patcher verifies every original byte before
        # anything is written, so a mismatch anywhere leaves `dest` alone
        # rather than shipping a partial set -- and a partial set is what
        # every previous attempt turned out to be.
        #
        # A. The render target and the presentation blit. Four words. This is
        #    the only part that was ever measured correct on its own.
        applied += nso_patcher.apply_spec(nso, ff7nx_widescreen.spec())
        # B. The field background's tile window, all four sides. The two LEFT
        #    extents are hardware-confirmed; the RIGHT and BOTTOM biases are
        #    new and are what HANDOFF-48 §4 asked for.
        m = nxmap.Main(src)
        ff7nx_wsclamp.check_all(m.img)
        clamp_values = ff7nx_wsclamp.defaults(scale)
        # E. THE PARALLAX RIGHT EDGE. Layers 3 and 4 WRAP rather than cull,
        #    and their right-hand wrap point is 0 -- so every parallax tile in
        #    the right expanded margin is shifted a whole layer width and
        #    lands back inside the 4:3 picture. Reported from hardware
        #    2026-08. See ff7nx_wsclamp's 'pright*' sites.
        if parallax():
            pr = ff7nx_wsclamp.parallax_right(scale)
            knobs = list(ff7nx_wsclamp.PARALLAX_RIGHT_KNOBS)
            if _flag('SEVENTH_NX_WS_PARALLAX_NO_SHIFT'):
                # The A/B, and it reproduces every build up to 86: the layer-3
                # and layer-4 WRAP points stay at the 4:3 edge while the layer-4
                # cull moves, so the right margin of every parallax layer is
                # shifted a whole layer width back inside the 4:3 picture.
                knobs = [k for k in knobs
                         if k not in ff7nx_wsclamp.PARALLAX_SHIFT_KNOBS]
                log('  ! parallax SHIFT helpers EXCLUDED '
                    '(SEVENTH_NX_WS_PARALLAX_NO_SHIFT=1) -- the sky will pop '
                    'in and out along the right edge. A/B only.')
            for knob in knobs:
                clamp_values[knob] = pr
            log('  parallax right edge 0 -> %d units  (%s)'
                % (pr, ', '.join(knobs)))
            # F. THE PARALLAX BOTTOM EDGE -- the vertical twin of E, and the
            #    last piece of the Mt Corel fix. `bottom_offset` is 0 in
            #    stock while the picture runs to bg.y+16, so every parallax
            #    tile in that band tests as outside the wrap window and is
            #    TELEPORTED a whole layer height away. That is the "pops in
            #    and out as I move up and down" report, and the Honey Bee Inn
            #    keyhole mask stopping short at the bottom. FINDINGS-205.
            #
            #    There is no vertical CULL to pair these with and that is
            #    measured, not assumed: layer 3's pick loop has no position
            #    test at all and layer 4's tests x only. See the `pbottom3`
            #    block in ff7nx_wsclamp for the branch enumeration.
            #
            #    The three bottom knobs are OPTIONAL and verified separately,
            #    so a signature that stops matching costs the vertical fix and
            #    NOT the 16:9 stage. Build 97 lost viewport, scissor, fade
            #    quad and every camera cave to one extra that could raise
            #    inside this transaction; HANDOFF-204 s4b.
            if not _flag('SEVENTH_NX_WS_PARALLAX_NO_VERTICAL'):
                vvals = ff7nx_wsclamp.parallax_vertical_values(scale)
                ok = ff7nx_wsclamp.verified_optional(
                    m.img, ff7nx_wsclamp.PARALLAX_BOTTOM_KNOBS, log=log)
                for knob in ff7nx_wsclamp.PARALLAX_VERTICAL_KNOBS:
                    if (knob in ff7nx_wsclamp.PARALLAX_BOTTOM_KNOBS
                            and knob not in ok):
                        continue
                    clamp_values[knob] = vvals[knob]
                log('  parallax bottom edge 0 -> %d units  (%s)'
                    % (ff7nx_wsclamp.parallax_bottom(scale), ', '.join(ok)))
                log('    half_height 112 -> %d, top_offset %d (stock 256 + the 8 the centred origin moved)'
                    % (ff7nx_wsclamp.parallax_half_height(scale),
                       ff7nx_wsclamp.parallax_top(scale)))
                log('    (build 96 shipped top_offset 272 out of '
                    'ff7nx_fieldwide; that word is WITHDRAWN here -- the '
                    'extra 16 units are at the bottom, not the top.)')
            else:
                log('  ! parallax VERTICAL knobs EXCLUDED '
                    '(SEVENTH_NX_WS_PARALLAX_NO_VERTICAL=1) -- the sky and '
                    'the Honey Bee Inn keyhole will pop in and out along the '
                    'bottom edge. A/B only.')
        applied += nso_patcher.apply_spec(
            nso, ff7nx_wsclamp.spec(m.img, clamp_values,
                                    starts=set(m.arm_starts), log=log))
        # D. The field render target. Same transaction as everything else,
        #    so a module that does not match leaves `dest` untouched rather
        #    than shipping the buffer without the extents that go with it.
        if buf:
            bad = ff7nx_fieldbuf.verify_sites(m.img)
            if bad:
                raise RuntimeError('field render target: ' + '; '.join(bad))
            fb_spec = ff7nx_fieldbuf.spec(m.img, buf['width'], buf['height'])
            if fb_spec:
                applied += nso_patcher.apply_spec(nso, fb_spec)
        # C. Optional, off by default, one variable each. Both are real
        #    effects with real risk attached, and HANDOFF-48 §10.2 is
        #    explicit that shipping them together with B makes the result
        #    unreadable. Turn on one at a time.
        if _flag(UNCROP_ENV):
            # The mode-2 viewport is 448 of 480 units tall. Lifting it is
            # necessary for the vertical bars to go, but `halfh` also feeds
            # 0xCFF200, which the MODEL projection reads -- the vertical twin
            # of the trap HANDOFF-48 §9 error 2 fell into. Untested.
            applied += nso_patcher.apply_spec(nso, {
                'name': 'field vertical uncrop 448 -> 480 (EXPERIMENTAL)',
                'patches': [dict(p) for p in UNCROP_PATCHES]})
        if parallax():
            # The other half of the parallax fix: `left_offset` 352 -> 459 and
            # `half_width` 160 -> 213, five in-place words. These are the
            # immediates; the right edge above is the bare-register compare
            # that needed the caves. Shipping one without the other widens the
            # window on one side only, which is what produced the asymmetry
            # ff7nx_fieldwide's KNOWN GAP predicted.
            import ff7nx_fieldwide
            applied += nso_patcher.apply_spec(
                nso, ff7nx_fieldwide.parallax_spec())
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        os.replace(tmp, dest)
    except Exception as exc:                                   # noqa: BLE001
        log(f'! 16:9 framing: {exc}')
        log('  nothing was written; the module is unchanged')
        return []
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    for line in applied:
        log('  ' + line)
    produced_now = [dest] if not built else []
    produced_now += _install_shaders(sdout, log)

    for h in (720, 1080):
        log('  %dp: render target %d -> %d wide, presentation blit %.2f -> '
            '1.00 of the screen'
            % (h,
               ff7nx_widescreen.logical_width(h, False) * 3 // 2,
               ff7nx_widescreen.logical_width(h, True) * 3 // 2,
               ff7nx_widescreen.logical_width(h, False) / (h * 16.0 / 9.0)))
    log('')
    for line in ff7nx_wsclamp.describe(scale=scale):
        log(line)
    if buf:
        log('')
        for line in ff7nx_fieldbuf.describe(buf['width'], buf['height'],
                                            scale):
            log('  ' + line)
    log('')
    log('  WHAT TO LOOK FOR, and please report it in these terms:')
    log('    The picture should be the right SHAPE everywhere. Nothing')
    log('      stretched. If anything is stretched, WS_SCALE is not being')
    log('      applied -- check the two .glsl actually reached the card.')
    log('    FIELD: background art should now reach the LEFT edge (already')
    log('      confirmed) AND the RIGHT edge (new). The right bar of ~107')
    log('      game units should be gone.')
    log('    FIELD: the ~32-unit bar along the BOTTOM should be gone.')
    log('      The TOP was long asserted to be "already covered" with')
    log('      nothing measured; HANDOFF-93 found the counter-example')
    log('      (Sector 8, the camera panning down onto Aerith).')
    log('      RESOLVED: it is not this pass and not a half-height. The')
    log('      port already clamps vertically to 120 -- measured, four')
    log('      immediates in field_clip_with_camera_range, and no 112')
    log('      anywhere. A band at the extreme of a pan means the SCRIPTED')
    log('      camera, which bypasses that clamp entirely. ff7nx_camclamp')
    log('      now clamps both axes; if the band is back, check that its')
    log('      cave is 45 words (x and y) and not 24 (x only).')
    log('    Characters should sit ON the scenery, not beside it. If the')
    log('      background moved and the models did not, an ORIGIN got')
    log('      changed somewhere -- that is HANDOFF-48 §9 error 2 and it is')
    log('      NOT this patch set, which only moves extents.')
    log('    STILL EXPECTED TO BE WRONG: battle is letterboxed above the UI')
    log('      band, and ~50 full-screen 2D overlays (swirl, summon flashes)')
    log('      cover only the middle 4:3.')
    log('    NO LONGER TRUE: "the margins are not cleared". The flat margin')
    log('      colour was never the clear colour -- ff7nx_marginart and')
    log('      ff7nx_marginpal fixed it at the source, and the "Black 16:9')
    log('      margins" option that chased the clear is retired and removed.')
    return produced_now


# --------------------------------------------------------------------------
# the per-field table the FRAMING stage will need
# --------------------------------------------------------------------------
def emit_table(resolved, dest, source=''):
    """
    Write the wide/not-wide exception table as an importable Python module.

    The content stage does not consume this -- the clamp carries the mode in
    the data already. It is emitted anyway because it is free once `resolve`
    has run, and because the framing stage cannot be written without it.

    Returns the `info` dict `emit_exception_table` produces, so the caller
    can report the shape of the table without re-deriving it.
    """
    _blob, text, info = W.emit_exception_table(resolved, source=source)
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or '.', exist_ok=True)
    tmp = dest + '.part'
    with open(tmp, 'w') as f:
        f.write(text)
    os.replace(tmp, dest)
    return info
