#!/usr/bin/env python3
r"""
ff7nx_wideflag.py -- `is_fieldmap_wide()`, PER FIELD, carried in the data.

    python3 ff7nx_wideflag.py <flevel.lgp> --show
    python3 ff7nx_wideflag.py --verify

WHAT THIS IS
============
`ff7nx_camclamp` installs a second cave on the SCR2D one-shot camera
placement (`field_init_scripted_bg_movement`) that clamps the camera to the
field's camera range at the moment it is initialised. Without it the port
leaves the resting position unclamped while the following smooth move IS
clamped, so switching camera mode snaps horizontally -- +/-162 px when Barret
comes through the door in `mds7pb_1`, +/-54 px on the chair.

FFNx does clamp there. `ff7/field/background.cpp:658`:

    world_pos = {-(*field_curr_delta_world_pos_x),
                 -(*field_curr_delta_world_pos_y)};

    if (is_fieldmap_wide())
        field_widescreen_width_clip_with_camera_range(&world_pos);

INSIDE `if (is_fieldmap_wide())`. The first version of the cave omitted that
test and clamped every field. This module is that test.

WHY THE OMISSION IS NOT A SMALL ONE
===================================
`field_curr_delta_world_pos_x` (guest `0xCC15F0`) is the variable the MVCAM
movie path reads every frame to re-anchor the field to the 4:3 centre
(FINDINGS-496):

    movsx ecx, word [0xCC15F0]     field_curr_delta_world_pos_x
    mov   edx, [0xCC1608]          field_bg_offset.x
    add   edx, ecx
    sub   eax, 0xA0

`southmk2` -- the Sector 7 pillar FMV -- resolves to `WM_DISABLED`. Our
widescreen never touches that field, so FFNx would never clamp it. Running
the ungated cave against `southmk2`'s real range (-192..192, admitting only
-32..32) moved that variable by up to **130 units**, and the movie came back
out of alignment. The two fixes were fighting over one variable:

    mds7pb_1   extend_wide   IS wide    -> clamp    (the 7th Heaven snap)
    southmk2   disabled      NOT wide   -> leave    (the FMV alignment)

Both are right, and the per-field answer is the only thing that gets both.

WHY THE ANSWER LIVES IN THE DATA
================================
FFNx asks `widescreen.getMode() != WM_DISABLED` at runtime. The port has no
such object: our widescreen mode is resolved at BUILD time by `ff7nx_ws` and
baked into each field's camera range. There is nothing left at runtime to
ask, so the answer has to travel with the field.

One byte in `field_trigger_header`, which is section 8's body verbatim:

    short field_30[4]           +0x30
             +0x30  vertical scripted-clip opt-in   (ff7nx_vclip)
             +0x31  is_fieldmap_wide                (HERE)
             +0x32..+0x37 still free

`field_30` is unnamed in FFNx and never touched by it, and all eight bytes
were MEASURED zero in all 711 fields of the built archive -- which is what
made the first flag safe and what makes the second one safe. `ff7nx_vclip`
says not to take a third byte without measuring again; that still holds.

DEFAULT-OFF IS THE SAFE DIRECTION, AND IT IS THE SAFE DIRECTION HERE TOO
=======================================================================
Zero means "not wide, do not clamp", which is vanilla's behaviour. An
archive built WITHOUT this pass therefore loses the initializer clamp
everywhere: the 7th Heaven camera snap comes back and nothing else changes.
It cannot move a camera that should not have moved, which is the failure the
ungated cave had.

That asymmetry is why the flag is not inverted to save re-encodes. 370 of
711 fields resolve to `disabled` and 341 to `extend_wide`, so "flag the
wide ones" and "flag the narrow ones" cost almost exactly the same; only
one of them fails safe.

COST
====
This pass re-encodes every wide field's section 8, so it is an ARCHIVE
change and `SEVENTH_NX_REUSE_ARCHIVES=1` will correctly refuse to reuse
flevel.lgp on the first build that includes it. Most of those fields are
already being re-encoded by `ff7nx_ws`'s own range write in the same
`payloads` dict, so the marginal cost is the fields whose range did not
change.
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ff7nx_wsdata as W                                        # noqa: E402

# field_trigger_header + 0x31. `ff7nx_camclamp.WIDE_FLAG_OFF` must agree and
# is asserted to in verify().
FLAG_OFF = 0x31
FLAG_ON = 1
FLAG_OFF_VALUE = 0

# The byte ff7nx_vclip owns. Named here only so this module can refuse to
# write it by accident.
VCLIP_FLAG_OFF = 0x30


def read_flag(sec8: bytes) -> int | None:
    """The flag byte, or None if section 8 is too short to hold a header."""
    if len(sec8) <= FLAG_OFF:
        return None
    return sec8[FLAG_OFF]


def write_flag(sec8: bytes, on: bool) -> bytes:
    """
    Section 8 with the wide flag set or cleared. Same length, always.

    Refuses if the byte is not already 0 or 1, for the same reason
    `ff7nx_vclip.write_flag` does: anything else means the measurement that
    says `field_30` is free has stopped being true for this archive, and the
    right response is to stop rather than to overwrite whatever it is.

    It never touches +0x30. The two flags share a block and not an owner.
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


def wide_fields(resolved):
    """
    The set of field names whose resolved widescreen mode is not WM_DISABLED.

    `resolved` is `ff7nx_ws.plan_ranges`'s second return value -- the same
    mapping the camera-fit stage already filters this way. Taking the
    resolved modes rather than re-deriving them is deliberate: the answer
    baked into the range data and the answer written into this byte have to
    come from one computation or they can disagree.
    """
    return {name for name, info in (resolved or {}).items()
            if info.get('mode') != W.WM_DISABLED}


def survey(archive, lgp):
    """{field: (flag, section8_len)}. Decompresses everything; diagnostic."""
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


def apply_archive(archive, payloads, lgp, wide, encode, log=print):
    """
    Set the wide flag on the wide fields, in place in `payloads`.

    `wide` is the set of field names that ARE wide (from `wide_fields`).

    IT ITERATES `wide`, NOT THE ARCHIVE, and that is a performance decision
    with teeth. Reading the flag back out of a field costs a FULL
    decompression -- section 9 is the background, megabytes of it -- so a
    loop over all 711 fields to check one byte would add minutes to every
    build for the 370 fields it would then leave alone. `ff7nx_ws` dodges
    the same cost with `_lzss_head`; this pass dodges it by only touching
    fields it has a reason to touch.

    There is therefore no clearing pass. Nothing needs one: `_build_flevel`
    always starts from the vanilla archive, where the byte is zero in all
    711 fields (measured), so "not in `wide`" and "left at zero" are the
    same state. If that ever stops being true, the loop has to grow a clear
    -- and `--show` is how you would find out.

    Same loop shape as `ff7nx_vclip.apply_archive` and `ff7nx_ws`'s writer:
    read a staged payload if one exists, otherwise the archive's own, and
    hand the re-encoded result back through `payloads` so passes compose.

    Returns a stats dict. Never raises for a single bad field -- one field
    that cannot be written keeps vanilla behaviour, which is safe.
    """
    wide = set(wide or ())
    stats = {'wide': len(wide), 'written': 0, 'unchanged': 0,
             'missing': [], 'failed': []}

    if not wide:
        log('  wide flag: no field resolves to a widescreen mode -- the '
            'SCR2D initializer clamp will not fire anywhere (vanilla '
            'behaviour)')
        return stats

    for name in sorted(wide):
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            stats['missing'].append(name)
            continue
        try:
            payload = payloads.get(name)
            raw = (lgp.lzs_decompress(payload[4:]) if payload
                   else archive.decompressed(entry))
            parts = lgp.split_sections(raw)
            sec8 = parts[W.SECTION_TRIGGERS]
            if read_flag(sec8) == FLAG_ON:
                stats['unchanged'] += 1
                continue
            parts[W.SECTION_TRIGGERS] = write_flag(sec8, True)
            payloads[name] = encode(lgp.join_sections(parts))
            stats['written'] += 1
        except Exception as exc:                                # noqa: BLE001
            stats['failed'].append((name, str(exc)))
            log('  ! wide flag: %s not written (%s)' % (name, exc))

    log('  wide flag: %d field(s) resolve wide, %d set, %d already set'
        % (len(wide), stats['written'], stats['unchanged']))
    log('    the SCR2D initializer clamp fires only on those -- every '
        'not-wide field (southmk2 and the other FMV anchors included) keeps '
        'its one-shot camera exactly where the script put it')
    if stats['missing']:
        log('    %d wanted field(s) are not in this archive: %s'
            % (len(stats['missing']), ', '.join(stats['missing'][:8])))
    if stats['failed']:
        log('    %d field(s) FAILED -- those keep vanilla behaviour'
            % len(stats['failed']))
    return stats


# ------------------------------------------------------------------- verify
def verify(flevel=None, log=print):
    fails = []

    def ck(cond, label):
        log(('    ok    ' if cond else '    FAIL  ') + label)
        if not cond:
            fails.append(label)

    log('  the flag, and the module that reads it:')
    try:
        import ff7nx_camclamp as C
        ck(C.WIDE_FLAG_OFF == FLAG_OFF,
           'ff7nx_camclamp reads +0x%02X and this writes +0x%02X'
           % (C.WIDE_FLAG_OFF, FLAG_OFF))
        ck(C.VCLIP_FLAG_OFF == VCLIP_FLAG_OFF and FLAG_OFF != VCLIP_FLAG_OFF,
           'the vertical opt-in keeps +0x%02X and this is a different byte'
           % VCLIP_FLAG_OFF)
        ck(FLAG_OFF < C.VCLIP_FLAG_OFF + C.VCLIP_FLAG_SITES,
           'the byte is inside the measured-zero block '
           '(+0x%02X..+0x%02X)'
           % (C.VCLIP_FLAG_OFF, C.VCLIP_FLAG_OFF + C.VCLIP_FLAG_SITES - 1))
        ck(C.init_n_words(False) - 2 in C.INIT_LEGACY_LENGTHS
           and C.init_n_words(True) - 2 in C.INIT_LEGACY_LENGTHS,
           'the gate costs 2 words and the ungated lengths are still '
           'revertable (%s)' % (C.INIT_LEGACY_LENGTHS,))
    except ImportError:
        ck(False, 'ff7nx_camclamp is importable')

    log('')
    log('  the round trip:')
    blank = bytes(0x40)
    ck(read_flag(blank) == 0, 'a zeroed header reads flag 0')
    ck(read_flag(write_flag(blank, True)) == 1, 'set -> 1')
    ck(read_flag(write_flag(write_flag(blank, True), False)) == 0,
       'set then clear -> 0, byte-exactly back')
    ck(write_flag(write_flag(blank, True), False) == blank,
       'the whole section comes back byte for byte')
    ck(len(write_flag(blank, True)) == len(blank),
       'the length never changes')
    ck(write_flag(blank, True)[VCLIP_FLAG_OFF] == 0,
       'setting the wide flag does not touch the vertical opt-in at +0x%02X'
       % VCLIP_FLAG_OFF)
    vset = bytearray(blank)
    vset[VCLIP_FLAG_OFF] = 1
    ck(write_flag(bytes(vset), True)[VCLIP_FLAG_OFF] == 1,
       'and does not clear it either')
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
    log('  the field set:')
    res = {'a': {'mode': W.WM_DISABLED}, 'b': {'mode': W.WM_EXTEND_WIDE},
           'c': {'mode': W.WM_ZOOM}, 'd': {'mode': W.WM_EXTEND_ONLY}}
    ck(wide_fields(res) == {'b', 'c', 'd'},
       'every non-DISABLED mode counts as wide, as FFNx tests it')
    ck(wide_fields({}) == set() and wide_fields(None) == set(),
       'no resolution means no field is wide -- fail safe')

    if flevel:
        log('')
        log('  against the archive:')
        import lgp as L
        seen = survey(L.Archive(flevel), L)
        ck(len(seen) > 600, '%d field(s) read' % len(seen))
        bad = {n: f for n, (f, _) in seen.items() if f not in (0, 1)}
        ck(not bad,
           'field_trigger_header + 0x%02X is 0 or 1 in every field%s'
           % (FLAG_OFF, '' if not bad else ' -- %d are not: %s'
              % (len(bad), sorted(bad)[:5])))
        short = [n for n, (f, ln) in seen.items() if f is None]
        ck(not short, 'no field has a section 8 too short to hold the flag')
        on = sorted(n for n, (f, _) in seen.items() if f == 1)
        log('    flag currently SET on %d of %d field(s)' % (len(on), len(seen)))
        if 'southmk2' in seen:
            ck(seen['southmk2'][0] == 0,
               'southmk2 is NOT flagged wide -- the FMV anchor keeps its '
               'unclamped one-shot camera')
        if 'mds7pb_1' in seen:
            ck(seen['mds7pb_1'][0] == 1,
               'mds7pb_1 IS flagged wide -- the 7th Heaven snap stays fixed')

    log('')
    log('  %d failure(s)' % len(fails) if fails else '  all checks pass')
    return 1 if fails else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('flevel', nargs='?')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--verify', action='store_true')
    a = ap.parse_args(argv)

    if a.show:
        if not a.flevel:
            ap.error('--show needs a flevel.lgp')
        import lgp as L
        seen = survey(L.Archive(a.flevel), L)
        on = sorted(n for n, (f, _) in seen.items() if f == 1)
        print('  %s' % a.flevel)
        print('    %d field(s), wide flag SET on %d' % (len(seen), len(on)))
        for n in on:
            print('      %s' % n)
        return 0

    return verify(a.flevel)


if __name__ == '__main__':
    raise SystemExit(main())
