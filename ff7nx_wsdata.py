#!/usr/bin/env python3
"""
ff7nx_wsdata.py -- the DATA half of 16:9, which is the half nobody planned for.

WHY THIS EXISTS
===============
Three hardware tests were spent trying to buy widescreen from the graphics
driver. It cannot be bought there: the driver decides how an already-drawn
image lands on the screen, and no transform available to it can reveal a
pixel the game did not draw.

FFNx does not do it that way either. Its widescreen is per FIELD, decided at
field-load time, and a field with no extra painted background is LEFT AT 4:3
-- it never stretches as a fallback. `Widescreen::initParamsFromConfig()`
(src/ff7/widescreen.cpp:383):

    camera_range = field_triggers_header->camera_range;      // the field's own
    if (camera_range.right - camera_range.left
            >= game_width / 2 + abs(wide_viewport_x))        // 320 + 107 = 427
        widescreen_mode = WM_EXTEND_WIDE;
    else
        widescreen_mode = WM_DISABLED;
    ... then per-field overrides, by field NAME, from config.toml

So before any module patch is worth writing, two numbers have to exist:

  * how many of FF7's fields pass that gate on their own, and
  * how many only work because somebody hand-authored an override.

This module produces both, offline, out of `flevel.lgp` and a mod's
`config.toml`. No hardware, no module patch, no guessing.

WHERE THE CAMERA RANGE LIVES
============================
Field file, section 8 (Triggers), at the very start of the section body:

    +0x00  char[9]  field name (NUL padded)
    +0x09  s8       control direction
    +0x0A  s16      camera focus height
    +0x0C  s16      camera_range.left
    +0x0E  s16      camera_range.top
    +0x10  s16      camera_range.right
    +0x12  s16      camera_range.bottom

THE ORDER IS left, TOP, right, BOTTOM, and Y GROWS DOWNWARD, so `bottom` is
numerically the LARGER value. Two sources say otherwise and both are wrong:
PyFF7's `Triggers` reader labels them `left, bottom, right, top`, and
`HANDOFF-widescreen.md` §3 has a third arrangement again.

FFNx's own struct is the authority (`src/ff7.h:2370`):

    struct field_camera_range { short left; short top; short right;
                                short bottom; };

and that is the struct the game's code indexes. This was not academic: the
first bake run refused Cosmos's real config with `camera_range top (-160)
<= bottom (160)` -- the labels crossed, caught by the guard instead of by a
hardware test. Only `right - left` feeds the gate, so no coverage figure
moves, but anything touching the vertical pair (WM_ZOOM, v_offset) needs
this.
"""
import json
import os
import re
import struct
import sys

# --------------------------------------------------------------------------
# FFNx's constants, from src/widescreen.h and src/ff7/widescreen.h
# --------------------------------------------------------------------------
GAME_W, GAME_H = 640, 480           # game_obj[0x954]/[0x958]. Never changes.
WIDE_VIEWPORT_X = -107              # -(854-640)/2
WIDE_VIEWPORT_WIDTH = 854           # 640 * 4/3
WIDE_VIEWPORT_HEIGHT = 480

# 16:10 alternative, kept because Widescreen::init() switches to it wholesale
WIDE_16X10 = {'viewport_x': -64, 'viewport_width': 768}

# The gate. A field whose camera can travel at least this far horizontally
# has painted background outside the 4:3 crop for a wider camera to reveal.
WIDE_GATE = GAME_W // 2 + abs(WIDE_VIEWPORT_X)          # 320 + 107 = 427

# enum WIDESCREEN_MODE, src/ff7/widescreen.h:35
WM_DISABLED, WM_EXTEND_ONLY, WM_ZOOM, WM_EXTEND_WIDE, WM_FILL = range(5)
WM_NAME = {WM_DISABLED: 'disabled', WM_EXTEND_ONLY: 'extend_only',
           WM_ZOOM: 'zoom', WM_EXTEND_WIDE: 'extend_wide', WM_FILL: 'fill'}

# Keys Widescreen::initParamsFromConfig reads. Anything else in a mod's
# config.toml is not ours to interpret and is reported, not silently dropped.
FIELD_KEYS = ('left', 'right', 'bottom', 'top', 'h_offset', 'v_offset',
              'reset_vertical_pos', 'scripted_clip',
              'scripted_vertical_clip', 'mode')
MOVIE_KEYS = ('mode', 'movie_v_offset')

SECTION_TRIGGERS = 7                # section 8 of 9, zero-based
CACHE_VERSION = 'WSDATA-V1'


# --------------------------------------------------------------------------
# flevel.lgp -> per-field camera range
# --------------------------------------------------------------------------
def _lzss_head(data, limit):
    """
    FF7 LZSS, decompressed only as far as `limit` bytes and then stopped.

    PyFF7's `decompress_lzss` is correct but decompresses the WHOLE field --
    and section 9, the background, is most of it. All this module wants is
    the twenty bytes at the top of section 8, so decompressing past that is
    pure waste: it turned a one-minute job into one that had not finished in
    three. Stopping early makes the whole archive readable in seconds.

    Byte-for-byte identical to PyFF7's output over the range it produces;
    `tests/test_wsdata.py` checks that against the real archive rather than
    trusting it.
    """
    n = len(data)
    inpos = 4                       # skip the 4-byte compressed-size header
    out = bytearray()
    while inpos < n and len(out) < limit:
        control = data[inpos]
        inpos += 1
        for bit in range(8):
            if inpos >= n:
                break
            if control & (1 << bit):
                out.append(data[inpos])
                inpos += 1
            else:
                lo = data[inpos]
                hi = data[inpos + 1]
                inpos += 2
                length = (hi & 0x0F) + 3
                raw = ((hi & 0xF0) << 4) | lo
                tail = len(out)
                pos = tail - ((tail - 18 - raw) & 0x0FFF)
                if pos < 0:
                    chunk = bytearray(min(-pos, length))
                    pos += len(chunk)
                else:
                    chunk = bytearray()
                chunk += out[pos:pos + length - len(chunk)]
                out += chunk
                if len(chunk) < length:
                    # out-of-bounds reference: repeat what was copied
                    if not chunk:
                        out += b'\x00' * (length - len(chunk))
                    else:
                        for i in range(len(chunk), length):
                            out.append(chunk[i % len(chunk)])
        if len(out) >= limit:
            break
    return bytes(out)


def _pyff7():
    """PyFF7 is vendored in this repo; find it without assuming the cwd."""
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, 'PyFF7'), here):
        if os.path.isdir(os.path.join(cand, 'PyFF7')):
            if cand not in sys.path:
                sys.path.insert(0, cand)
            return True
    return False


def camera_ranges(flevel_path, cache=None, log=lambda *_: None):
    """
    {field_name: {'left','right','bottom','top','width','height'}} for every
    field in `flevel.lgp`.

    Decompressing 700+ LZSS field files takes about a minute, and the answer
    only changes when flevel.lgp does, so the result is cached against the
    archive's size and mtime.
    """
    if cache and os.path.exists(cache):
        try:
            with open(cache) as f:
                blob = json.load(f)
            st = os.stat(flevel_path)
            if (blob.get('version') == CACHE_VERSION
                    and blob.get('size') == st.st_size
                    and abs(blob.get('mtime', 0) - st.st_mtime) < 1):
                return blob['fields']
        except (ValueError, KeyError, OSError):
            pass

    if not _pyff7():
        raise RuntimeError('PyFF7 not found next to %s' % __file__)
    from PyFF7.lgp import LGP

    lgp = LGP(flevel_path)
    entries = list(lgp)
    out = {}
    bad = []
    for i, entry in enumerate(entries):
        name = entry['filename']
        if i % 100 == 0:
            log('    %d/%d' % (i, len(entries)))
        try:
            raw = lgp.load_toc_entry(entry)
            head = _lzss_head(raw, 42)
            nsec = struct.unpack('<I', head[2:6])[0]
            if nsec != 9:
                raise ValueError('%d sections' % nsec)
            starts = struct.unpack('<9I', head[6:42])
            body = starts[SECTION_TRIGGERS] + 4      # skip the section length
            data = _lzss_head(raw, body + 20)
            left, top, right, bottom = struct.unpack(
                '<4h', data[body + 12:body + 20])
            out[name] = {'left': left, 'right': right,
                         'bottom': bottom, 'top': top,
                         'width': right - left,
                         'height': bottom - top}
        except Exception as exc:                     # noqa: BLE001
            bad.append((name, str(exc)))
    if bad:
        log('    ! %d field(s) unreadable: %s'
            % (len(bad), ', '.join(n for n, _ in bad[:5])))

    if cache:
        st = os.stat(flevel_path)
        try:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache, 'w') as f:
                json.dump({'version': CACHE_VERSION, 'size': st.st_size,
                           'mtime': st.st_mtime, 'fields': out}, f)
        except OSError:
            pass
    return out


def gate(width):
    """FFNx's own test, and nothing else. True => this field can be widened."""
    return width >= WIDE_GATE


def default_mode(rng):
    """The mode a field gets with NO config entry at all."""
    return WM_EXTEND_WIDE if gate(rng['width']) else WM_DISABLED


def apply_zoom(rng):
    """
    WM_ZOOM crops vertically instead of extending horizontally
    (src/ff7/widescreen.cpp:420). Returns a new range.

    This is the reader that needs the bottom/top pair to be the right way
    round -- see the module docstring.
    """
    out = dict(rng)
    offset = 9 * (rng['right'] - rng['left']) // 16 - 240
    out['bottom'] = rng['bottom'] - offset // 2
    out['top'] = rng['top'] + offset // 2
    out['height'] = out['bottom'] - out['top']
    return out


# --------------------------------------------------------------------------
# config.toml / movie_config.toml
# --------------------------------------------------------------------------
def _parse_toml_subset(text):
    """
    The subset FFNx's widescreen configs actually use, with no dependency.

    `tomllib` is 3.11+ and this project has to run on whatever Python the
    user's machine has, so it is used when present and this is the fallback.
    Supported: [table] headers, int / bool / string scalars, and arrays of
    arrays of ints (which is what `movie_v_offset` is -- see
    src/ff7/widescreen.cpp:444, `keyframeArray->get(0)` / `get(1)`).
    """
    out = {}
    cur = None
    for raw in text.splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        m = re.match(r'^\[([^\]]+)\]$', line)
        if m:
            cur = out.setdefault(m.group(1).strip().strip('"\''), {})
            continue
        if cur is None or '=' not in line:
            continue
        key, _, val = line.partition('=')
        key = key.strip().strip('"\'')
        val = val.strip()
        if val.startswith('['):
            nums = [int(n) for n in re.findall(r'-?\d+', val)]
            cur[key] = [nums[i:i + 2] for i in range(0, len(nums) - 1, 2)]
        elif val.lower() in ('true', 'false'):
            cur[key] = val.lower() == 'true'
        elif re.match(r'^-?\d+$', val):
            cur[key] = int(val)
        else:
            cur[key] = val.strip('"\'')
    return out


def load_toml(path):
    """{table_name: {key: value}} or {} if the file is missing/unparseable."""
    if not path or not os.path.exists(path):
        return {}
    with open(path, 'rb') as f:
        raw = f.read()
    try:
        import tomllib
        return tomllib.loads(raw.decode('utf-8', 'replace'))
    except ImportError:
        pass
    except Exception:                                # noqa: BLE001
        pass
    return _parse_toml_subset(raw.decode('utf-8', 'replace'))


def find_configs(root):
    """
    (config.toml, movie_config.toml, alternates) under a mod directory.

    FFNx looks in `<basedir>/<external_widescreen_path>/config.toml`; mods
    ship it as `CONFIG/widescreen/`, so the name is searched for rather than
    assumed.

    A mod may ship SEVERAL. Cosmos Limit Break has both `CONFIG/widescreen/`
    and `CONFIG UPSCALES ONLY/widescreen/`, and picking whichever `os.walk`
    happened to reach first would make the coverage number depend on
    directory order. The plain `CONFIG/` one wins and the rest are returned
    so the caller can say what it ignored.
    """
    found = []
    mov_by_dir = {}
    for dirpath, _dirs, files in os.walk(root):
        low_dir = dirpath.lower()
        for f in files:
            low = f.lower()
            if low == 'config.toml' and 'widescreen' in low_dir:
                found.append(os.path.join(dirpath, f))
            elif low == 'movie_config.toml':
                mov_by_dir[dirpath] = os.path.join(dirpath, f)
    if not found:
        return None, None, []

    def rank(p):
        rel = os.path.relpath(p, root).lower()
        # prefer a plain CONFIG/widescreen over any decorated variant,
        # then prefer the shallowest, then alphabetical for determinism
        return (0 if os.sep + 'config' + os.sep in os.sep + rel else 1,
                rel.count(os.sep), rel)

    found.sort(key=rank)
    cfg = found[0]
    mov = mov_by_dir.get(os.path.dirname(cfg))
    if mov is None and mov_by_dir:
        mov = sorted(mov_by_dir.values(), key=rank)[0]
    return cfg, mov, found[1:]


# --------------------------------------------------------------------------
# Baking the config into the game's own data
#
# `field_clip_with_camera_range_float` (FFNx, background.cpp:417) reads the
# camera range like this:
#
#     auto camera_range = field_triggers_header_ptr->camera_range;
#     if (widescreen_enabled || enable_uncrop)
#         camera_range = widescreen.getCameraRange();     // the override
#     half_width = 160 + std::min(53, cameraRangeSize / 2 - 160);
#
# i.e. the clamp operates on the OVERRIDDEN range, not on the field's own.
# On PC the override lives in a runtime object fed by config.toml. We have
# no such object, and building one in the module would mean a per-field
# lookup table in a binary with ~31 KB of usable cave space.
#
# We do not need one. We rewrite `flevel.lgp` at build time already. Writing
# the overridden range straight into the field's section 8 makes
# `field_triggers_header->camera_range` simply correct at runtime, and the
# code cave then only has to do the `160 + min(53, ...)` arithmetic on data
# the game already holds. No table, no lookup, no cave space.
# --------------------------------------------------------------------------
RANGE_OFFSET = 12                   # into the section 8 BODY
RANGE_ORDER = ('left', 'top', 'right', 'bottom')
SECTION8_MIN_LEN = RANGE_OFFSET + 8


def read_section8_range(sec8):
    """{left,bottom,right,top} from a section 8 body."""
    if len(sec8) < SECTION8_MIN_LEN:
        raise ValueError('section 8 is %d bytes, need at least %d'
                         % (len(sec8), SECTION8_MIN_LEN))
    vals = struct.unpack('<4h', sec8[RANGE_OFFSET:RANGE_OFFSET + 8])
    out = dict(zip(RANGE_ORDER, vals))
    out['width'] = out['right'] - out['left']
    out['height'] = out['bottom'] - out['top']       # Y grows downward
    return out


def write_section8_range(sec8, rng):
    """
    Section 8 with its camera range replaced. Same length, always.

    Refuses anything that will not survive the round trip:
      * a value outside s16
      * an inverted or empty range (right <= left, top <= bottom)
    A silently-clamped or wrapped range would put the camera somewhere the
    background does not exist, and the failure would be a hardware test.
    """
    if len(sec8) < SECTION8_MIN_LEN:
        raise ValueError('section 8 too short to hold a camera range')
    vals = []
    for key in RANGE_ORDER:
        v = int(rng[key])
        if not -0x8000 <= v <= 0x7FFF:
            raise ValueError('camera_range.%s = %d does not fit in s16'
                             % (key, v))
        vals.append(v)
    left, top, right, bottom = vals
    if right <= left:
        raise ValueError('camera_range right (%d) <= left (%d)'
                         % (right, left))
    if bottom <= top:
        # Y grows downward, so bottom is the larger number. If this fires,
        # suspect the label order before suspecting the data.
        raise ValueError('camera_range bottom (%d) <= top (%d)'
                         % (bottom, top))
    out = bytearray(sec8)
    out[RANGE_OFFSET:RANGE_OFFSET + 8] = struct.pack('<4h', *vals)
    return bytes(out)


def bake_plan(ranges, config, movie_config=None):
    """
    {field: new_range} for every field whose range the config CHANGES.

    Fields the config leaves alone are absent, so a caller can skip
    re-encoding them -- which matters, because re-encoding a field costs a
    full LZSS pass and most of them do not need it.
    """
    resolved = resolve(ranges, config, movie_config)
    plan = {}
    for name, info in resolved.items():
        old = ranges[name]
        new = info['range']
        if any(new[k] != old[k] for k in RANGE_ORDER):
            plan[name] = {k: new[k] for k in RANGE_ORDER}
    return plan


def verify_bake(before, after, plan):
    """
    Every planned change landed, and nothing else moved.

    `before`/`after` are {field: range} read back out of the archive. This is
    the check that makes the bake falsifiable without a console: re-read the
    rebuilt archive and the numbers either are the config's or they are not.
    Returns (ok, problems).
    """
    problems = []
    for name, want in plan.items():
        got = after.get(name)
        if got is None:
            problems.append('%s: missing from the rebuilt archive' % name)
            continue
        for k in RANGE_ORDER:
            if got[k] != want[k]:
                problems.append('%s: %s is %d, wanted %d'
                                % (name, k, got[k], want[k]))
    for name, old in before.items():
        if name in plan:
            continue
        got = after.get(name)
        if got is None:
            problems.append('%s: lost from the archive' % name)
        elif any(got[k] != old[k] for k in RANGE_ORDER):
            problems.append('%s: changed but was not in the plan' % name)
    return (not problems), problems


NAME_LEN = 9                        # field_trigger_header.field_name[9]


def field_key(archive_name):
    """
    The name the MODULE will see, from the name the ARCHIVE uses.

    They are not always the same. flevel.lgp carries Xbox One duplicates as
    `crcin_1.XOne` alongside `crcin_1`, and the LGP conflict table is what
    keeps them apart -- but section 8 stores only 9 bytes of name and both
    entries hold the same one, `crcin_1`, with the same camera range.

    So a module-side matcher keyed on the archive name would be comparing
    against a string that is never in the field data, and 12 characters of
    it would not fit in the 9 bytes anyway. Key on the base name; the
    variants collapse onto each other, which is right, because only one of
    them is ever loaded.
    """
    return archive_name.split('.')[0].lower()


def repainted_fields(chunk_dir):
    """
    Which fields a 7th Heaven chunk mod repaints, from `<name>.chunk.<n>`.

    Cosmos Limit Break's `LIMIT BREAK/flevel.lgp` is a DIRECTORY, not an
    archive: 683 files named `<field>.chunk.9`, each replacing section 9
    (Background) of that field. Section 8 -- the triggers, and therefore the
    camera range the widescreen gate reads -- is NOT replaced.

    That is the whole reason `config.toml` has to exist. The pack paints
    extra background outside the 4:3 crop, but it has no way to say so in
    the field's own data, so it says so out of band instead.

    Returns {section_number: set(field_names)}.
    """
    out = {}
    if not chunk_dir or not os.path.isdir(chunk_dir):
        return out
    for name in os.listdir(chunk_dir):
        m = re.match(r'^(.+)\.chunk\.(\d+)$', name)
        if m:
            out.setdefault(int(m.group(2)), set()).add(m.group(1))
    return out


def find_chunk_dirs(root):
    """Every `*.lgp` DIRECTORY under a mod that holds .chunk.N files."""
    hits = []
    for dirpath, dirs, files in os.walk(root):
        if dirpath.lower().endswith('.lgp') and any(
                re.search(r'\.chunk\.\d+$', f) for f in files):
            hits.append(dirpath)
    return sorted(hits)


def resolve(ranges, config=None, movie_config=None):
    """
    The whole per-field decision, exactly as FFNx makes it.

    Returns {field: {'mode', 'mode_name', 'range', 'from_config',
                     'overrides', 'gated_in'}}.
    """
    config = config or {}
    # Field names are matched case-insensitively. The packer's `lgp.py`
    # lowercases archive names while PyFF7 preserves them, so flevel carries
    # both `md1_1.XOne` and `md1_1.xone` depending on who read it, and a
    # config written against either spelling has to hit the same field. A
    # miss here is silent: the field simply stays 4:3.
    lower = {}
    base = {}
    for key, val in config.items():
        if isinstance(val, dict):
            lower.setdefault(key.lower(), val)
            base.setdefault(field_key(key), val)
    out = {}
    for name, rng in ranges.items():
        # exact, then case-folded, then BASE NAME. The last one matters:
        # flevel carries `crcin_1.XOne` beside `crcin_1`, both holding the
        # same 9-byte section 8 name and the same camera range, and a config
        # written for `crcin_1` is plainly meant for both. Without this the
        # two halves of one field resolve to different modes, and the
        # emitted table -- which can only see the section 8 name -- has no
        # way to express the disagreement. It showed up as
        # "2 base name(s) resolve both ways: crcin_1, crcin_2".
        entry = (config.get(name)
                 or lower.get(name.lower())
                 or base.get(field_key(name))
                 or {})
        rng = dict(rng)
        mode = default_mode(rng)
        gated_in = mode == WM_EXTEND_WIDE
        overrides = []
        for key in ('left', 'right', 'bottom', 'top'):
            if key in entry:
                rng[key] = int(entry[key])
                overrides.append(key)
        rng['width'] = rng['right'] - rng['left']
        rng['height'] = rng['top'] - rng['bottom']
        for key in ('h_offset', 'v_offset', 'reset_vertical_pos',
                    'scripted_clip', 'scripted_vertical_clip'):
            if key in entry:
                overrides.append(key)
        if 'mode' in entry:
            mode = int(entry['mode'])
            overrides.append('mode')
        elif overrides:
            # ranges were overridden but no explicit mode: re-run the gate
            mode = default_mode(rng)
        if mode == WM_ZOOM:
            rng = apply_zoom(rng)
        out[name] = {'mode': mode, 'mode_name': WM_NAME.get(mode, str(mode)),
                     'range': rng, 'from_config': bool(entry),
                     'overrides': overrides, 'gated_in': gated_in}
    return out


def unknown_keys(config):
    """Keys a mod's config carries that FFNx's field reader does not read."""
    seen = set()
    for entry in config.values():
        if isinstance(entry, dict):
            seen |= set(entry) - set(FIELD_KEYS)
    return sorted(seen)


def exception_list(resolved):
    """
    (wide_default, exceptions, zoom) -- the module's whole per-field input.

    `is_fieldmap_wide()` is binary (`getMode() != WM_DISABLED`), so what has
    to reach `main` is one bit per field. With Cosmos installed 647 of 711
    fields are wide, so the compact encoding is DEFAULT WIDE plus a list of
    the ones that are not: 64 names instead of 711 bits, and no dependence
    on knowing a field's numeric id at runtime.

    `field_triggers_header` starts with the 9-byte field name and is already
    in hand at the clamp site, so the module can match on the name it
    already has.

    Defaulting to WIDE rather than to 4:3 is deliberate. It keeps the list
    small, and a field we forgot to name renders like the 647 that work
    rather than being silently singled out -- a visible bug beats an
    invisible one.
    """
    # Deduplicated by SECTION 8 name, because that is what the module can
    # compare against -- `crcin_1` and `crcin_1.XOne` are one field to it.
    # Counting archive names instead would skew the majority test and could
    # pick the longer list.
    exceptions = sorted({field_key(n) for n, i in resolved.items()
                         if i['mode'] == WM_DISABLED})
    wide = {field_key(n) for n, i in resolved.items()
            if i['mode'] != WM_DISABLED}
    zoom = sorted({field_key(n) for n, i in resolved.items()
                   if i['mode'] == WM_ZOOM})
    wide_default = len(exceptions) <= len(wide)
    return wide_default, exceptions, zoom


def emit_exception_table(resolved, source=''):
    """
    The per-field wide/not-wide decision as a fixed-width byte table.

    9 bytes per name, NUL padded, exactly as section 8 stores it, so the
    module compares against bytes it already holds rather than parsing
    anything.

    Emits whichever list is SHORTER. With Cosmos installed 647 fields are
    wide, so it names the 64 that are not. Without a config only 341 are
    wide, so it names those instead and flips the default. Returns
    (blob, text, info).
    """
    wide_default, exceptions, zoom = exception_list(resolved)
    wide = sorted({field_key(n) for n, i in resolved.items()
                   if i['mode'] != WM_DISABLED})
    narrow = sorted({field_key(n) for n, i in resolved.items()
                     if i['mode'] == WM_DISABLED})
    # A base name that appears on both sides would make the table ambiguous.
    both = sorted(set(wide) & set(narrow))
    listed = narrow if wide_default else wide
    too_long = [n for n in listed if len(n) > NAME_LEN]
    if too_long:
        raise ValueError('field name longer than %d bytes: %s'
                         % (NAME_LEN, ', '.join(too_long)))
    blob = b''.join(n.encode('ascii', 'replace').ljust(NAME_LEN, b'\0')
                    for n in listed)
    zoom = sorted({field_key(n) for n in zoom})
    info = {'wide_default': wide_default, 'listed': len(listed),
            'wide': len(wide), 'narrow': len(narrow), 'zoom': len(zoom),
            'ambiguous': both, 'bytes': len(blob)}
    lines = [
        '# GENERATED by ff7nx_wsdata.emit_exception_table -- do not edit.',
        '# source: %s' % (source or 'unknown'),
        '#',
        '# is_fieldmap_wide() is binary, so the module needs one bit per',
        '# field. This names the minority and defaults to the majority:',
        '#     %d wide, %d not wide, %d zoom' % (len(wide), len(narrow),
                                                 len(zoom)),
        '# Names are SECTION 8 names (<=%d bytes), not archive entry names.'
        % NAME_LEN,
        '',
        'WIDE_BY_DEFAULT = %r' % wide_default,
        'NAME_LEN = %d' % NAME_LEN,
        '# the listed names are the EXCEPTIONS to WIDE_BY_DEFAULT',
        'EXCEPTIONS = [',
    ]
    for n in listed:
        lines.append('    %r,' % n)
    lines += [']', '', 'ZOOM = %r' % zoom, '']
    if both:
        lines += ['# WARNING: these base names resolve both ways -- the',
                  '# table cannot express them and they follow the default:',
                  '# %r' % both, '']
    lines += ['# %d bytes, %d entries x %d' % (len(blob), len(listed),
                                               NAME_LEN),
              'BLOB = (']
    for i in range(0, len(blob), 12):
        lines.append('    %r' % blob[i:i + 12])
    lines += [')', '']
    return blob, '\n'.join(lines), info


def summarise(resolved):
    """Counts, for the build log and for diag_widescreen.py."""
    total = len(resolved)
    by_mode = {}
    gated = configured = overridden = 0
    for info in resolved.values():
        by_mode[info['mode_name']] = by_mode.get(info['mode_name'], 0) + 1
        gated += bool(info['gated_in'])
        configured += bool(info['from_config'])
        overridden += bool(info['overrides'])
    wide = total - by_mode.get('disabled', 0)
    return {'total': total, 'gated_in': gated, 'configured': configured,
            'overridden': overridden, 'wide': wide, 'by_mode': by_mode}
