#!/usr/bin/env python3
r"""
ff7nx_vclip.py -- the PER-FIELD opt-in for the scripted vertical camera clamp.

    python3 ff7nx_vclip.py <flevel.lgp> --show
    python3 ff7nx_vclip.py <flevel.lgp> --config <widescreen/config.toml> --verify
    python3 ff7nx_vclip.py <flevel.lgp> --config <cfg> --patch      # in place

`--patch` rewrites the archive in place so the flag can be tested without a
full build. It touches only the fields that want the flag -- six of 711 --
and every other payload is copied through byte for byte. The build path
(`apply_archive`, called from the archive stage) is the one that matters;
`--patch` exists because a 195 MB rebuild is a poor way to answer a yes/no
question about one byte.

WHAT THIS IS
============
`ff7nx_camclamp` clamps the SCRIPTED camera to the field's camera range,
which vanilla FF7 never does. On x that is unconditionally right -- FFNx does
it unconditionally too, and `ff7nx_ws` has already baked the widescreen
adjustment into the range so the stock +/-160 lands on FFNx's bound.

On y it is unconditionally WRONG, and this module is the gate.

FFNx, `ff7/field/background.cpp:559`:

    void field_uncropped_height_clip_with_camera_range(vector2<short>* point)
    {
        if(!widescreen.isScriptedClipEnabled()) return;
        point->y += widescreen.getVerticalOffset();
        if(widescreen.isScriptedVerticalClipEnabled())
        {
            if (point->y > camera_range.bottom - 120) point->y = ...;
            if (point->y < camera_range.top    + 120) point->y = ...;
        }
    }

Two different gates, two different defaults, and the asymmetry is the point:

    scripted_clip           defaults TRUE    -- 1 field in Cosmos turns it off
    scripted_vertical_clip  defaults FALSE   -- 5 fields opt in

`ff7nx_camclamp` v2 read that asymmetry, chose to ignore it, and wrote down
why:

    "The clamp is idempotent, so on a field whose script stays legal it is a
     no-op, and the per-field gate is already baked into the range data."

Both halves are false, and each one has a symptom on hardware.

  * NOT A NO-OP. A field script pans the camera to positions the range
    forbids -- that is what an elevator IS. Vanilla permitted it. Clamped, the
    camera stops at `top + 120`, holds while the elevator keeps moving, then
    releases when the script brings it back inside. Reported from hardware,
    2026-08-08, in exactly those words: "the camera gets stuck as the elevator
    moves up or down, then it gets un-stuck and continues."
  * NOTHING IS BAKED. `ff7nx_ws.clamped_range()` copies `top` and `bottom`
    through untouched -- HANDOFF-93 0.2 states it plainly. The x leg's gate
    really is in the data, which is why one sentence is true on one axis and
    false on the other. The reasoning was carried across an axis it does not
    survive.

MEASURED OVER THE BUILT ARCHIVE, 711 fields
===========================================
The ungated vertical clamp:

    198  range EXACTLY 240 units tall -> `top + 120 == bottom - 120`, so the
         camera is FROZEN at one y and a scripted vertical pan does nothing.
         `blinele` (the Shinra HQ elevator), `elminn_1`, `elmtow`, `junele2`.
     10  range under 240 -> lo > hi; `_clamp_block`'s guard skips these, so
         they were never corrupted, only pointless.
    503  real travel, and the camera sticks at the last units of it.

So the gate is not caution. It is the difference between clamping 5 fields
and clamping 700.

THE FLAG, AND WHY IT LIVES AT +0x30
===================================
One byte in `field_trigger_header`, which is section 8's body verbatim:

    byte  field_name[9]
    byte  control_direction
    short focus_height
    ...   camera_range          +0x0C  left, +0x0E top, +0x10 right, +0x12 bottom
    byte  field_14[4]           +0x14
    ...   bg3/bg4 dims, pos, speed
    short field_30[4]           +0x30  <- HERE
    ...   gateways[12]          +0x38

`field_30` is unnamed in FFNx and never touched by it. MEASURED across all
711 fields of the built archive: all eight bytes are zero in every one.

`field_14[4]` was the obvious candidate -- it sits directly after the range
the cave already reads -- and it is NOT free: 86 fields carry non-zero values
there. Checking that before writing is the whole reason this paragraph is
short.

Only byte +0x30 is used. The other seven stay zero and are not reserved for
anything; if a second per-field flag is ever wanted, measure again first.

WHY A DATA FLAG RATHER THAN A FIELD LIST IN THE CAVE
====================================================
A name-comparison cave would work -- `field_trigger_header` opens with
`field_name[9]` and the cave already holds a pointer to it -- and it was
rejected. It bakes a list into code, which is the thing that has to be
edited every time a mod changes, and it grows the cave by three words per
field. The flag is two words for any number of fields, and the list stays
where FFNx keeps it: in the mod's own config.

DEFAULT-OFF IS THE SAFE DIRECTION
=================================
An archive built WITHOUT this pass has the byte at zero everywhere, so the
vertical clamp never fires and the build behaves like FFNx's default and like
vanilla. A missing pass costs the Sector 8 band; it cannot freeze a camera.
The failure mode of the previous arrangement was the other way round.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ff7nx_wsdata as W                                        # noqa: E402

# field_trigger_header + 0x30 -- `short field_30[4]` in FFNx, measured zero in
# all 711 fields. Section 8's body starts at the header, so the offset is the
# same in both. `ff7nx_camclamp.VCLIP_FLAG_OFF` must agree and is asserted to.
FLAG_OFF = 0x30
FLAG_ON = 1
FLAG_OFF_VALUE = 0

# The key FFNx reads, spelled exactly as FFNx spells it.
CONFIG_KEY = 'scripted_vertical_clip'

# Fields this project has a MEASURED symptom for that the mod's config does
# not list. One entry, one reason, one capture -- do not add to this without
# all three.
#
#   md8_2  Sector 8, the camera panning down from the LOVELESS billboards
#          onto Aerith. Black band along the top at the extreme of the pan,
#          24 device rows at 720p. Range top -240 bottom 240 = 480 units,
#          layer-1 art 480 units, so the view is flush with the art at the
#          clamp and the script drives past it. IDENTIFIED BY RENDERING the
#          field out of the built archive and matching it against the capture
#          -- the LOVELESS 6/25 billboard, "THE VELVET NOIX" and the
#          CONGRATULATIONS/BAZAR panel are all in md8_2's layer 1.
#
# HANDOFF-93 SAID md8_3 AND md8_3 IS THE WRONG FIELD.
# ----------------------------------------------------
# md8_3's numbers fit beautifully: range 400, art 400, view flush at the
# clamp, and at +/-120 the camera travels y in [-80, +80] -- HANDOFF-93 0
# derived exactly that and concluded it was the LOVELESS pan. It is a
# different field entirely (a large circular vent structure; render it and
# look). md8_2 has the SAME property -- range 480, art 480, flush at the
# clamp -- because that property is common, not diagnostic.
#
# This is FINDINGS-85 2.4 for the third time: AN EXACT NUMERIC FIT IS NOT
# IDENTIFICATION. The first build after the fix went out with md8_3 flagged,
# md8_2 unflagged, and the band still on screen -- and the archive was
# correct the whole time. Identify the field by rendering it.
MEASURED_EXTRA = {
    'md8_2': 'the LOVELESS pan (Sector 8, onto Aerith) -- band at the top '
             'of the pan; field identified by render, not by arithmetic',
}


def wanted_fields(config, extra=True):
    """
    {field_name: reason} for every field whose scripted camera should be
    clamped vertically.

    `config` is the parsed widescreen config -- {field: {key: value}} -- or
    None, in which case only the measured list applies.
    """
    out = {}
    for name, keys in (config or {}).items():
        val = keys.get(CONFIG_KEY)
        if isinstance(val, str):
            val = val.strip().lower() in ('true', '1', 'yes')
        if val:
            out[name] = 'config: %s = true' % CONFIG_KEY
    if extra:
        for name, why in MEASURED_EXTRA.items():
            out.setdefault(name, why)
    return out


def read_flag(sec8: bytes) -> int | None:
    """The flag byte, or None if section 8 is too short to hold a header."""
    if len(sec8) <= FLAG_OFF:
        return None
    return sec8[FLAG_OFF]


def write_flag(sec8: bytes, on: bool) -> bytes:
    """
    Section 8 with the flag set or cleared. Same length, always.

    Refuses if the byte is not already 0 or 1. Anything else means the
    measurement that says `field_30` is free has stopped being true for this
    archive, and the right response is to stop rather than to overwrite
    whatever it is.
    """
    if len(sec8) <= FLAG_OFF:
        raise ValueError('section 8 is %d bytes, too short to hold '
                         'field_trigger_header + 0x%02X'
                         % (len(sec8), FLAG_OFF))
    cur = sec8[FLAG_OFF]
    if cur not in (FLAG_OFF_VALUE, FLAG_ON):
        raise ValueError('field_trigger_header + 0x%02X is 0x%02X, not 0 or 1'
                         ' -- this byte was measured free and is not; refusing'
                         % (FLAG_OFF, cur))
    out = bytearray(sec8)
    out[FLAG_OFF] = FLAG_ON if on else FLAG_OFF_VALUE
    return bytes(out)


def survey(archive, lgp):
    """
    {field: (flag, section8_len)} for every field in the archive.

    Used by --show and by the freeness check. Decompresses everything, so it
    is a diagnostic rather than something the build calls.
    """
    out = {}
    for name in archive.names():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            sec8 = lgp.split_sections(archive.decompressed(entry))[
                W.SECTION_TRIGGERS]
        except Exception:                                       # noqa: BLE001
            continue
        out[name] = (read_flag(sec8), len(sec8))
    return out


def apply_archive(archive, payloads, lgp, config, encode, log=print,
                  extra=True):
    """
    Set the flag on the wanted fields, in place in `payloads`.

    Same shape as `ff7nx_ws.apply_archive`'s writer loop and deliberately so:
    it reads a payload if one is already staged, otherwise the archive's own,
    and hands the re-encoded result back through `payloads` so passes compose.

    Returns a stats dict. Never raises for a single bad field -- one field
    that cannot be written is logged and skipped, because a camera clamp is
    not worth failing a build over.
    """
    want = wanted_fields(config, extra=extra)
    stats = {'wanted': len(want), 'written': 0, 'missing': [], 'failed': []}
    if not want:
        log('  vertical clip: no field asks for it -- the scripted camera is '
            'left alone on every field (FFNx default)')
        return stats

    for name in sorted(want):
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            stats['missing'].append(name)
            continue
        try:
            payload = payloads.get(name)
            raw = (lgp.lzs_decompress(payload[4:]) if payload
                   else archive.decompressed(entry))
            parts = lgp.split_sections(raw)
            parts[W.SECTION_TRIGGERS] = write_flag(
                parts[W.SECTION_TRIGGERS], True)
            payloads[name] = encode(lgp.join_sections(parts))
            stats['written'] += 1
        except Exception as exc:                                # noqa: BLE001
            stats['failed'].append((name, str(exc)))
            log('  ! vertical clip: %s not written (%s)' % (name, exc))

    log('  vertical clip: %d field(s) opted in, %d written'
        % (len(want), stats['written']))
    for name in sorted(want):
        mark = ('  <- NOT IN THIS ARCHIVE' if name in stats['missing'] else '')
        log('      %-14s %s%s' % (name, want[name], mark))
    log('    every other field keeps a scripted camera the clamp never '
        'touches -- elevators included')
    return stats


# ------------------------------------------------------------------- verify
def verify(flevel=None, config=None, log=print, extra=True):
    fails = []

    def ck(cond, label):
        log(('    ok    ' if cond else '    FAIL  ') + label)
        if not cond:
            fails.append(label)

    log('  the flag, and the module that reads it:')
    try:
        import ff7nx_camclamp
        ck(ff7nx_camclamp.VCLIP_FLAG_OFF == FLAG_OFF,
           'ff7nx_camclamp reads +0x%02X and this writes +0x%02X'
           % (ff7nx_camclamp.VCLIP_FLAG_OFF, FLAG_OFF))
        ck(ff7nx_camclamp.n_words(True) - ff7nx_camclamp.n_words(False) == 23,
           'the vertical leg is 23 words on top of the horizontal one '
           '(21 clamp + 2 gate)')
    except ImportError:
        ck(False, 'ff7nx_camclamp is importable')

    log('')
    log('  the round trip:')
    blank = bytes(0x40)
    ck(read_flag(blank) == 0, 'a zeroed header reads flag 0')
    ck(read_flag(write_flag(blank, True)) == 1, 'set -> 1')
    ck(read_flag(write_flag(write_flag(blank, True), False)) == 0,
       'set then clear -> 0, byte-exactly back')
    ck(write_flag(blank, True) != blank
       and len(write_flag(blank, True)) == len(blank),
       'the length never changes')
    dirty = bytearray(blank)
    dirty[FLAG_OFF] = 0x7F
    try:
        write_flag(bytes(dirty), True)
        ck(False, 'a non-0/1 byte at +0x%02X is refused' % FLAG_OFF)
    except ValueError:
        ck(True, 'a non-0/1 byte at +0x%02X is refused' % FLAG_OFF)
    try:
        write_flag(bytes(8), True)
        ck(False, 'a short section 8 is refused')
    except ValueError:
        ck(True, 'a short section 8 is refused')

    log('')
    log('  the field list:')
    want = wanted_fields(config, extra=extra)
    if config:
        from_cfg = [n for n, w in want.items() if w.startswith('config:')]
        ck(from_cfg != [], '%d field(s) from the config: %s'
           % (len(from_cfg), ', '.join(sorted(from_cfg))))
    else:
        log('    (no config supplied -- only the measured list is checked)')
    if extra:
        ck(all(n in want for n in MEASURED_EXTRA),
           'the measured list is present: %s' % ', '.join(MEASURED_EXTRA))
    ck(len(want) < 50,
       'the list is %d field(s) -- a gate, not a rename of "everything"'
       % len(want))

    if flevel:
        log('')
        log('  against the archive:')
        import lgp as L
        arch = L.Archive(flevel)
        seen = survey(arch, L)
        ck(len(seen) > 600, '%d field(s) read' % len(seen))
        bad = {n: f for n, (f, _) in seen.items() if f not in (0, 1)}
        ck(not bad,
           'field_trigger_header + 0x%02X is 0 or 1 in every field%s'
           % (FLAG_OFF,
              '' if not bad else ' -- %d are not: %s'
              % (len(bad), sorted(bad)[:5])))
        short = [n for n, (f, ln) in seen.items() if f is None]
        ck(not short, 'no field has a section 8 too short to hold the flag')
        missing = [n for n in want if n not in seen]
        ck(not missing,
           'every wanted field exists in this archive%s'
           % ('' if not missing else ' -- absent: %s' % sorted(missing)))
        on = sorted(n for n, (f, _) in seen.items() if f == 1)
        log('    flag currently SET on %d field(s)%s'
            % (len(on), (': ' + ', '.join(on)) if on else ''))

    log('')
    log('  %d failure(s)' % len(fails) if fails else '  all checks pass')
    return 1 if fails else 0


def patch_in_place(flevel, config, log=print, extra=True, backup=True):
    """
    Set the flag on the wanted fields in an existing flevel.lgp.

    The build does this through `apply_archive` on payloads it is already
    staging. This is the standalone form: read, edit the handful of fields
    that want it, write the whole archive back. Only the edited payloads are
    re-encoded; everything else is copied verbatim.

    A `.bak` is written first unless `backup=False`, because this rewrites a
    file the build takes minutes to produce.
    """
    import shutil
    import lgp as L

    arch = L.Archive(flevel)
    want = wanted_fields(config, extra=extra)
    log('  %s' % flevel)
    log('  %d field(s) want the vertical clip: %s'
        % (len(want), ', '.join(sorted(want))))

    payloads, skipped, already = {}, [], []
    for name in sorted(want):
        entry = arch.index.get(name)
        if entry is None or not arch.is_field(entry):
            skipped.append(name)
            log('      %-14s NOT IN THIS ARCHIVE -- skipped' % name)
            continue
        raw = arch.decompressed(entry)
        parts = L.split_sections(raw)
        if read_flag(parts[W.SECTION_TRIGGERS]) == FLAG_ON:
            already.append(name)
            log('      %-14s already set' % name)
            continue
        parts[W.SECTION_TRIGGERS] = write_flag(parts[W.SECTION_TRIGGERS], True)
        payloads[name] = arch.encode_field(L.join_sections(parts))
        log('      %-14s set    %s' % (name, want[name]))

    if not payloads:
        log('  nothing to write -- the archive is already in the wanted state')
        return 0

    if backup:
        bak = flevel + '.bak'
        if not os.path.exists(bak):
            shutil.copy2(flevel, bak)
            log('  backup -> %s' % bak)
        else:
            log('  backup already exists, left alone -> %s' % bak)

    arch.replace(payloads)
    arch.write(flevel)
    log('  wrote %s  (%d field(s) changed, %d already set, %d absent)'
        % (flevel, len(payloads), len(already), len(skipped)))

    # Read it back. A camera clamp that silently did not reach the archive is
    # exactly the failure this project keeps having, so the flag is confirmed
    # from the file on disk rather than from the fact that write() returned.
    back = L.Archive(flevel)
    bad = []
    for name in sorted(set(want) - set(skipped)):
        e = back.index.get(name)
        sec8 = L.split_sections(back.decompressed(e))[W.SECTION_TRIGGERS]
        if read_flag(sec8) != FLAG_ON:
            bad.append(name)
    if bad:
        log('  ! READ BACK FAILED on %s -- do not boot this' % ', '.join(bad))
        return 1
    log('  read back: the flag is set in the written archive on all %d'
        % len(set(want) - set(skipped)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('flevel', nargs='?')
    ap.add_argument('--config', help='widescreen/config.toml')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--patch', action='store_true',
                    help='set the flag in an existing flevel.lgp, in place')
    ap.add_argument('--no-backup', action='store_true')
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--no-extra', action='store_true',
                    help='follow the config alone; drop the measured list')
    a = ap.parse_args(argv)

    cfg = None
    if a.config:
        cfg = W.load_toml(a.config)

    if a.patch:
        if not a.flevel:
            ap.error('--patch needs a flevel.lgp')
        return patch_in_place(a.flevel, cfg, extra=not a.no_extra,
                              backup=not a.no_backup)

    if a.show:
        if not a.flevel:
            ap.error('--show needs a flevel.lgp')
        import lgp as L
        seen = survey(L.Archive(a.flevel), L)
        on = sorted(n for n, (f, _) in seen.items() if f == 1)
        print('  %s' % a.flevel)
        print('    %d field(s), vertical clip SET on %d' % (len(seen), len(on)))
        for n in on:
            print('      %s' % n)
        want = wanted_fields(cfg, extra=not a.no_extra)
        print('    the config + measured list wants %d: %s'
              % (len(want), ', '.join(sorted(want))))
        return 0

    return verify(a.flevel, cfg, extra=not a.no_extra)


if __name__ == '__main__':
    raise SystemExit(main())
