#!/usr/bin/env python3
"""
ff7nx_fieldwide.py -- the field half of 16:9, done the way FFNx does it.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
This module implements the two field-side pieces of FFNx's widescreen:

  1. the parallax layers' clip and wrap points, widened from a 320-unit
     view to a 427-unit one, as five in-place word patches;
  2. the camera-travel clamp, tightened from half_width 160 to 213, done
     as a per-field DATA transform instead of 24 code caves.

Both are COMPENSATION TERMS for a widened viewport. Neither reveals content
on its own, and the clamp visibly reduces camera travel if shipped alone.
They are off by default and should stay off until the framing works. See
README-widescreen-v5-clamp-direction.md for the measurement that says so.

WHY THERE IS NO "UNLOCK THE CONTENT" STEP HERE
----------------------------------------------
There is nothing to unlock for the main background. Measured, not assumed:

  * `field_layer1_pick_tiles` (main +0xA06DE0) and `field_layer2_pick_tiles`
    (+0xA059D0) contain no horizontal clip at all. Between them, 1,524
    instructions and 28 compares, every one of which is either the
    `field_special_y_offset > 0 && bg_position.y <= 6` guard or the loop
    counter's sign/overflow emulation. No tile x is ever compared against
    a bound.
  * `add_page_tile` (+0xA06870, 348 instructions) has ZERO compares. It is
    straight-line vertex writing and cannot clip.
  * `field_pick_tiles_make_vertices` (+0x9F2E60) is pure dispatch: its only
    tests are the `do_draw_layer3/4` booleans.

So every tile of layers 1 and 2 already reaches the vertex buffer, including
the parts outside the 4:3 crop. Only the PARALLAX layers (3 and 4) cull, and
that is what part 1 below widens.

FFNx REFERENCE
--------------
    src/ff7/field/background.cpp:126   field_layer3_shift_tile_position
    src/ff7/field/background.cpp:174   field_layer3_pick_tiles cull
    src/ff7/field/background.cpp:249   field_layer4_shift_tile_position
    src/ff7/field/background.cpp:296   field_layer4_pick_tiles cull
    src/ff7/field/background.cpp:417   field_clip_with_camera_range_float
    src/ff7/field/background.cpp:481   float_sub_643628
    src/ff7/widescreen.cpp:~385        Widescreen::initParamsFromConfig
    src/widescreen.h                   wide_viewport_x/width
"""
import os
import struct

FIELD_WIDE_ENV = 'SEVENTH_NX_FIELD_WIDE'

# ---------------------------------------------------------------- constants
#
# All four come straight out of FFNx and are exact, not rounded:
#
#   wide_viewport_x     = -107 = -(854 - 640) / 2
#   wide_viewport_width =  854 = 640 * 4/3
#   half_width          =  213 = ceil(854 / 4)   -- half the 427-unit view
#   left_offset         =  459 = 352 + |-107|
#
# 160 is half the 4:3 view (320 units) exactly as 213 is half the 16:9 one.
GAME_WIDTH = 640
WIDE_VIEWPORT_X = -107
WIDE_VIEWPORT_WIDTH = 854

HALF_WIDTH_43 = 160
HALF_WIDTH_169 = WIDE_VIEWPORT_WIDTH // 4              # 213
HALF_WIDTH_CAP_DELTA = HALF_WIDTH_169 - HALF_WIDTH_43  # 53, FFNx's min() cap

LEFT_OFFSET_43 = 352
LEFT_OFFSET_169 = LEFT_OFFSET_43 + abs(WIDE_VIEWPORT_X)   # 459

# A field is "wide" iff its camera range can accommodate the 427-unit view
# with travel left over. FFNx evaluates this once per field, at load, in
# Widescreen::initParamsFromConfig, and stores it as WM_EXTEND_WIDE.
WIDE_FIELD_MIN_RANGE = GAME_WIDTH // 2 + abs(WIDE_VIEWPORT_X)   # 427


# ======================================================================
# PART 1 -- the parallax layers, five in-place words
# ======================================================================
#
# Located by walking FFNx's own chain through ff7_en and mapping the result
# with nxmap; the chain is self-checking (eight name-encoded constants fall
# out of it and all eight match). See README-widescreen-v6-pick-tiles.
#
#     field_sub_6388EE 0x6388EE
#       +0x11 -> field_draw_everything          0x63A60B
#       +0xC9 -> field_pick_tiles_make_vertices 0x640F22
#                  +0x12 -> field_layer3_pick_tiles 0x640F95 -> +0xA07780
#                  +0x5F -> field_layer4_pick_tiles 0x641358 -> +0xA08630
#
# Every replacement immediate fits the 12-bit add/sub field, so these are
# single-word rewrites: no caves, no displaced instructions, no cave budget.
# Encodings were round-tripped through capstone, not derived on paper.
#
# NOT PATCHED, deliberately:
#   #0x100 (256, top_offset)  and  #0x70 (112, half_height)
# belong to `enable_uncrop`, which is a separate feature. They are distinct
# immediates so there is no risk of catching them by accident.
PARALLAX_PATCHES = [
    {
        'name': 'layer3 left_offset 352 -> 459',
        'va': 0x0A07CFC,
        'expect': '09 81 05 51',      # sub w9, w8, #0x160
        'set':    '09 2D 07 51',      # sub w9, w8, #0x1cb
    },
    {
        'name': 'layer3 half_width 160 -> 213',
        'va': 0x0A07DB4,
        'expect': '08 81 02 51',      # sub w8, w8, #0xa0
        'set':    '08 55 03 51',      # sub w8, w8, #0xd5
    },
    {
        'name': 'layer4 left_offset 352 -> 459 (a)',
        'va': 0x0A08B44,
        'expect': '09 81 05 51',      # sub w9, w8, #0x160
        'set':    '09 2D 07 51',      # sub w9, w8, #0x1cb
    },
    {
        'name': 'layer4 half_width 160 -> 213',
        'va': 0x0A08BFC,
        'expect': '08 81 02 51',      # sub w8, w8, #0xa0
        'set':    '08 55 03 51',      # sub w8, w8, #0xd5
    },
    {
        'name': 'layer4 left_offset 352 -> 459 (b)',
        'va': 0x0A08CDC,
        'expect': '09 81 05 51',      # sub w9, w8, #0x160
        'set':    '09 2D 07 51',      # sub w9, w8, #0x1cb
    },
]

# KNOWN GAP, stated rather than papered over.
#
# FFNx also moves `right_offset` from 0 to |wide_viewport_x| = 107, in both
# shift helpers and both pick-loop culls. At zero there is no immediate to
# rewrite -- the test is against the bare register -- so matching it needs an
# inserted instruction, i.e. a cave, at four sites.
#
# Consequence of leaving it: parallax is extended to the LEFT by 107 units
# and not to the right. On a field with a scrolling sky that is visible as
# asymmetry once the viewport is actually widened. It is not worth a cave
# until the framing exists to see it against.
RIGHT_OFFSET_NOT_PATCHED = True


def parallax_spec():
    """A patch spec for nso_patcher, which verifies every original byte."""
    return {
        'name': 'field parallax 4:3 -> 16:9 clip/wrap points',
        'patches': [dict(p) for p in PARALLAX_PATCHES],
    }


# ======================================================================
# PART 2 -- the camera clamp, as data
# ======================================================================
#
# THE CODE PATH WE ARE NOT TAKING
# -------------------------------
# FFNx raises `half_width` from 160 to 213 inside two functions:
#
#     field_clip_with_camera_range        x86 0x6438F6 -> main +0xA11530
#     field_layer3_clip_with_camera_range x86 0x643628 -> main +0xA108A0
#
# Doing that in code means 24 hook sites (4 in the first, 20 in the second,
# because float_sub_643628 is two projection branches that each use
# half_width three times), and every one of them needs the per-field gate,
# which needs `left` and `right` together -- neither of which is in a
# register at most sites. That is a lot of cave for an arithmetic identity.
#
# THE IDENTITY
# ------------
# Both functions only ever use camera_range through `left + half_width` and
# `right - half_width`. So narrowing the RANGE by the same amount, and
# leaving half_width at its stock 160, produces identical arithmetic:
#
#     left'  = left  + (half_width - 159)
#     right' = right - (half_width - 159)
#
# with the stock code's `left' + 160` and `right' - 160` then landing exactly
# on FFNx's `left + 1 + half_width` and `right - 1 - half_width`.
#
# The per-field gate becomes free: a field that does not qualify is simply
# not transformed. And the whole thing is inspectable and reversible, because
# it is 8 bytes of section 8 rather than 24 patched instructions.
#
# THE ONE-UNIT CAVEAT, STATED PLAINLY
# -----------------------------------
# FFNx's two functions do not agree with each other. field_clip applies
# `left += 1; right -= 1` before computing the size; float_sub_643628 does
# not. On a wide field that makes their effective bounds differ by exactly 1
# unit. A single data delta cannot reproduce both.
#
# This module matches `field_clip_with_camera_range` exactly, because that is
# the clamp the player feels on every field. `float_sub_643628` -- the layer3
# projection used only where field_14[0] is 1 or 2 -- ends up 1 unit tighter
# than FFNx. `equivalence_report()` proves the first and quantifies the
# second across every field in a real flevel.lgp.

CAMERA_RANGE_OFF = 0x0C          # into section 8; left, top, right, bottom
CAMERA_RANGE_FMT = '<hhhh'
SECTION8_MIN_LEN = CAMERA_RANGE_OFF + 8
INT16_MIN, INT16_MAX = -32768, 32767


def half_width(range_size):
    """
    FFNx's half_width for a camera range of `range_size` units.

    background.cpp:433 --
        half_width = 160 + std::min(53, cameraRangeSize / 2 - 160)

    C++ integer division truncates toward zero; `range_size` is positive
    everywhere this is called, so `//` matches.
    """
    return HALF_WIDTH_43 + min(HALF_WIDTH_CAP_DELTA, range_size // 2 - HALF_WIDTH_43)


def is_wide(left, right):
    """
    Does this field qualify as WM_EXTEND_WIDE?

    widescreen.cpp, Widescreen::initParamsFromConfig --
        right - left >= game_width / 2 + abs(wide_viewport_x)

    This is the real per-field gate. The `min()` in half_width() is a cap at
    213, not a gate -- a distinction that matters, because treating the min
    as the gate applies half_width < 160 to narrow fields, which FFNx never
    does.
    """
    return (right - left) >= WIDE_FIELD_MIN_RANGE


def ffnx_clip_bounds(left, right):
    """
    The horizontal bounds field_clip_with_camera_range_float would clamp to.

    Returns (lo, hi). Mirrors background.cpp:417 including the +/-1
    adjustment ("prevent scrolling stopping one pixel too early").
    """
    if not is_wide(left, right):
        return left + HALF_WIDTH_43, right - HALF_WIDTH_43
    l, r = left + 1, right - 1
    h = half_width(r - l)
    return l + h, r - h


def ffnx_layer3_bounds(left, right):
    """
    The bounds float_sub_643628 uses (background.cpp:481).

    Same shape, but WITHOUT the +/-1 -- this is the disagreement documented
    above, reproduced here rather than smoothed over so the test can measure
    it instead of assuming it.
    """
    if not is_wide(left, right):
        return left + HALF_WIDTH_43, right - HALF_WIDTH_43
    h = half_width(right - left)
    return left + h, right - h


def range_delta(left, right):
    """
    How far to pull each edge in, so stock code lands on FFNx's bounds.

    0 for a field that does not qualify.
    """
    if not is_wide(left, right):
        return 0
    lo, _ = ffnx_clip_bounds(left, right)
    return lo - HALF_WIDTH_43 - left


def narrowed_range(left, right):
    """
    (left', right') or None if this field is not transformed.

    None is returned for a field that does not qualify, and also for the
    cases where the result would not fit int16 or would leave no travel.

    Both of those guards are unreachable for anything that passes the gate:
    the transform only moves edges INWARD, so int16 cannot be exceeded, and
    `r - l` bottoms out at exactly 320 (at a 428-unit range, where the delta
    is 54). They are kept as belt-and-braces, and
    test_guards_are_unreachable_by_construction() proves the property rather
    than testing the guards with impossible inputs -- so if the gate is ever
    loosened, the failure lands there instead of in the field data.
    """
    d = range_delta(left, right)
    if d <= 0:
        return None
    l, r = left + d, right - d
    if not (INT16_MIN <= l <= INT16_MAX and INT16_MIN <= r <= INT16_MAX):
        return None
    if r - l < 2 * HALF_WIDTH_43:
        return None                     # would leave no travel at all
    return l, r


def read_camera_range(section8):
    """(left, top, right, bottom) from a field's section 8, or None."""
    if len(section8) < SECTION8_MIN_LEN:
        return None
    return struct.unpack_from(CAMERA_RANGE_FMT, section8, CAMERA_RANGE_OFF)


def widen_section8(section8):
    """
    Apply the transform to one field's Triggers section.

    Returns new bytes, or None if the field is left alone. Only the four
    camera_range shorts are touched; every other byte is copied verbatim.
    """
    cr = read_camera_range(section8)
    if cr is None:
        return None
    left, top, right, bottom = cr
    got = narrowed_range(left, right)
    if got is None:
        return None
    l, r = got
    out = bytearray(section8)
    struct.pack_into(CAMERA_RANGE_FMT, out, CAMERA_RANGE_OFF, l, top, r, bottom)
    return bytes(out)


# ======================================================================
# switching and application
# ======================================================================

def enabled():
    """Off unless explicitly switched on. Never defaults to true."""
    raw = os.environ.get(FIELD_WIDE_ENV, '').strip().lower()
    return raw in ('1', 'true', 'on', 'yes')


def apply_to_nso(src, dest, log=lambda *_: None):
    """
    Apply part 1 to `main` at `src`, writing `dest`. True on success.

    MUST RUN AFTER apply_fps_patches AND ON ITS OUTPUT. The 60 FPS pass
    rewrites exefs/main; a later pass that starts from the stock module
    silently reverts all of it. That is a bug this project has already been
    bitten by once.

    Every original byte is verified by nso_patcher before anything is
    written, so a different game version fails loudly rather than producing
    a module patched in the wrong place.
    """
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import nso_patcher
    except ImportError as exc:
        log(f'! field-wide: cannot import nso_patcher ({exc})')
        return False
    from pathlib import Path
    try:
        nso = nso_patcher.read_nso(Path(src))
        applied = nso_patcher.apply_spec(nso, parallax_spec())
        Path(dest).write_bytes(nso_patcher.rebuild(nso))
    except Exception as exc:
        log(f'! field-wide: {exc}')
        return False
    for line in applied:
        log('  ' + line)
    log('  parallax: %d words (clip/wrap 352->459, 160->213)'
        % len(PARALLAX_PATCHES))
    log('  right_offset 0 -> 107 NOT applied (needs a cave; see module docs)')
    return True


def transform_archive_fields(archive, log=lambda *_: None, encode=None,
                             progress=None):
    """
    Apply part 2 across an lgp.Archive of flevel.lgp, in place.

    Yields (name, before, after) for each field changed, so a caller can
    report or diff. Fields that do not qualify are not touched at all --
    not re-encoded, not rewritten.

    `encode(archive, raw) -> payload` overrides the encoder. Pass
    `build._encode_field_cached` and the LZS pass -- which is pure Python and
    costs real minutes over 341 fields -- is paid once and cached by content
    thereafter. Defaults to `archive.encode_field`, which is correct but
    uncached, so this module stays usable without build.py.

    `progress(done, total, name)` is called per transformed field.
    """
    import lgp
    if encode is None:
        def encode(arch, raw):
            return arch.encode_field(raw)

    candidates = [e for e in archive.entries if archive.is_field(e)]
    changed = skipped = 0
    for entry in candidates:
        try:
            raw = archive.decompressed(entry)
            sections = lgp.split_sections(raw)
        except Exception:
            skipped += 1
            continue
        before = read_camera_range(sections[7])
        new8 = widen_section8(sections[7])
        if new8 is None:
            continue
        sections[7] = new8
        entry['payload'] = encode(archive, lgp.join_sections(sections))
        after = read_camera_range(new8)
        changed += 1
        if progress:
            progress(changed, len(candidates), entry['name'])
        yield entry['name'], before, after
    log('  camera range narrowed on %d field(s)%s'
        % (changed, ', %d unparsed' % skipped if skipped else ''))


# ======================================================================
# reporting / self-check
# ======================================================================

def equivalence_report(fields):
    """
    Prove the data transform reproduces FFNx, over real field data.

    `fields` is an iterable of (name, left, right). Returns a dict. The two
    numbers that matter are `clip_mismatch`, which must be 0, and
    `layer3_off_by_one`, which is the documented disagreement between FFNx's
    own two functions and is reported rather than hidden.
    """
    out = {'total': 0, 'wide': 0, 'clip_mismatch': 0, 'layer3_exact': 0,
           'layer3_off_by_one': 0, 'layer3_worse': 0, 'refused': 0,
           'travel_lost': 0}
    for _name, left, right in fields:
        out['total'] += 1
        if not is_wide(left, right):
            continue
        out['wide'] += 1
        got = narrowed_range(left, right)
        if got is None:
            out['refused'] += 1
            continue
        l, r = got
        # what the STOCK code computes, given the transformed data
        stock = (l + HALF_WIDTH_43, r - HALF_WIDTH_43)
        if stock != ffnx_clip_bounds(left, right):
            out['clip_mismatch'] += 1
        want3 = ffnx_layer3_bounds(left, right)
        diff = max(abs(stock[0] - want3[0]), abs(stock[1] - want3[1]))
        if diff == 0:
            out['layer3_exact'] += 1
        elif diff == 1:
            out['layer3_off_by_one'] += 1
        else:
            out['layer3_worse'] += 1
        out['travel_lost'] += (right - left - 2 * HALF_WIDTH_43) - (r - l - 2 * HALF_WIDTH_43)
    return out


def survey(flevel_path):
    """
    (name, left, right) for every field in an flevel.lgp.

    Accepts a zip containing one -- `game_data_zips/field.zip` is where this
    project actually keeps it, so requiring a loose file would mean everyone
    unpacks 141 MB by hand before they can run anything.
    """
    import lgp
    import tempfile
    import zipfile
    tmp = None
    if zipfile.is_zipfile(flevel_path):
        with zipfile.ZipFile(flevel_path) as z:
            member = next((n for n in z.namelist()
                           if os.path.basename(n).lower() == 'flevel.lgp'),
                          None)
            if member is None:
                raise ValueError('%s contains no flevel.lgp' % flevel_path)
            tmp = tempfile.TemporaryDirectory()
            z.extract(member, tmp.name)
            flevel_path = os.path.join(tmp.name, member)
    a = lgp.Archive(flevel_path)
    rows = []
    for e in a.entries:
        if not a.is_field(e):
            continue
        try:
            s8 = lgp.split_sections(a.decompressed(e))[7]
        except Exception:
            continue
        cr = read_camera_range(s8)
        if cr is None:
            continue
        rows.append((e['name'], cr[0], cr[2]))
    return rows


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        sys.exit('usage: ff7nx_fieldwide.py <flevel.lgp | field.zip>')
    rows = survey(sys.argv[1])
    rep = equivalence_report(rows)
    print('fields                       %d' % rep['total'])
    print('qualify as wide (>= %d)     %d  (%.1f%%)'
          % (WIDE_FIELD_MIN_RANGE, rep['wide'],
             100.0 * rep['wide'] / max(rep['total'], 1)))
    print('refused (unsafe transform)   %d' % rep['refused'])
    print('field_clip bounds mismatched %d   <- must be 0' % rep['clip_mismatch'])
    print('layer3 bounds exact          %d' % rep['layer3_exact'])
    print('layer3 bounds off by one     %d   <- FFNx disagrees with itself here'
          % rep['layer3_off_by_one'])
    print('layer3 bounds off by more    %d   <- must be 0' % rep['layer3_worse'])
    print('total camera travel given up %d units across %d fields'
          % (rep['travel_lost'], rep['wide']))
