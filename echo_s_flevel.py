"""Echo-S's field-script port for the Switch flevel archive.

Echo-S changes the field-script/dialogue section (section 1).  Its copies of
sections 3 and 6 are PC model/tile payloads, not dependencies of the voice or
text scripts.  They must remain Switch-native: for example, Echo-S's PC
``mrkt2`` tile section is 588,645 bytes while the Switch's is 20,206 bytes.
Injecting that foreign payload leaves the script's initial ``MUSIC 0`` call
byte-identical yet prevents the Switch music path from starting.

The normal port therefore imports section 1 only.  A future non-voice feature
may request another section explicitly, but that is an audited per-field
operation rather than a default compatibility assumption.
"""
from __future__ import annotations

import os
import sys
import struct

import lgp
import pyff7_path

# WHY THIS GOES THROUGH A HELPER AND NOT A try/except.
#
# The obvious spelling here is `try: from PyFF7.field import ... except
# ModuleNotFoundError: from PyFF7.PyFF7.field import ...`, and it was.  It
# works, and it breaks the build.
#
# `build.py` imports this module at the top, long before `ensure_pyff7` puts
# the bundled package directory on `sys.path`.  So the first spelling fails --
# but not before Python has resolved the name `PyFF7` against the project
# directory, found the CLONE ROOT (`7th_heaven_nx/PyFF7/`, which has no
# `__init__.py`), and cached it in `sys.modules` as a namespace package.  The
# fallback then succeeds, because the real package is one level further down.
#
# The damage is done by then.  `ensure_pyff7`'s own `from PyFF7.lgp import
# pack_lgp` is a cached no-op that looks in the clone root, where there is no
# `lgp.py`, and every model archive fails to pack:
#
#     ModuleNotFoundError: No module named 'PyFF7.lgp'
#
# Importing a module must not decide what `PyFF7` means for the rest of the
# process.  `pyff7_path` makes the name resolve to the real package before
# anybody asks, whoever asks first.
pyff7_path.ensure(os.path.dirname(os.path.abspath(__file__)))
from PyFF7.field import instruction_size                       # noqa: E402


SCRIPT_UNIT_SECTIONS = frozenset((1,))
PC_SPEAKER_PREFIX = b'\xfe\xd6'
PC_SPEAKER_SUFFIX = b'\xfe\xd9'
# Echo-S's PC field files use these controls to colour names in a number of
# item-acquisition strings.  The Switch field renderer does not share that
# convention.  ``FE DE`` is deliberately *not* included: it is a dynamic
# value macro used by valid acquisition messages such as a quantity display.
PC_COLOUR_OPENERS = frozenset(range(0xd2, 0xd9))
OBTAINED_PREFIX = bytes.fromhex('2f 42 54 41 49 4e 45 44 00')
LOST_PREFIX = bytes.fromhex('2c 4f 53 54 00')
KEY_ITEM_PREFIX = bytes.fromhex('2b 45 59 00 29 54 45 4d 00')
# A per-field ``{field: ((Echo index, Switch index), ...)}`` mapping that
# replaces an Echo-S dialogue wholesale with the Switch archive's own.
#
# SUPERSEDED BY `_retarget_pc_button_glyphs`, AND KEPT EMPTY ON PURPOSE.
#
# These two dialogs used to be replaced wholesale with the Switch field's own
# string, because Echo-S's version names PC buttons with PC glyph bytes this
# font does not have. That traded one wrong thing for another:
#
#   * the port's string attributes the md1_1 tutorial to {BARRET}; Echo-S
#     wrote it as narration, and the voice clip that plays over it -- yes,
#     both of these dialogs are voiced -- is the narrator;
#   * and it is four lines where Echo-S's is two. That matters, because
#     Echo-S shows it with `MESSAGE 0,16 ; WAIT 30` and then carries on. The
#     script gives the box half a second, so the longer string never finishes
#     typing and the player sees a fragment.
#
# Substituting only the glyph runs keeps Echo-S's phrasing, line count and
# window, and fixes the same defect in the other 256 places it occurs rather
# than the two that were noticed. The mechanism is retained because it is the
# right shape for a string the port genuinely has to own; nothing needs it
# today.
STOCK_DIALOGUE_OVERRIDES = {}

# PC BUTTON GLYPHS, AND WHAT THIS PORT SHOWS INSTEAD.
#
# Echo-S writes a button as `<glyph bytes> ( NAME )`, e.g.
# `95 08 "CANCEL" 09`. On PC, FFNx draws the glyph and the parenthesised name
# is its fallback. Here the glyph bytes land outside the font and the reader
# gets a broken character followed by "(CANCEL)". Measured over the release:
# 258 occurrences in 15 distinct forms, across the whole game.
#
# THESE ARE SWITCH BUTTONS, NOT PLAYSTATION ONES.
# The names below (✕ 〇 △ ☐) are how a PC-oriented decoder PRINTS these byte
# codes. What the console draws is `data/png/buttons.png`, which ships A, B,
# X, Y, L, R, ZL, ZR, minus and the d-pad. And `data/png/howtoplay_keyassign`
# gives the port's own assignment:
#
#     X = Menu     B = Cancel     A = Ok     Y = Switch
#     L = Previous Page     R = Next Page     ZL = Camera     ZR = Target
#
# Each row is a byte code lifted from the Switch archive's own strings, so it
# renders exactly as that tutorial already renders in this build:
#
#   md1_1    "...pressing ✕ to run"                -> CANCEL  F9     -> B
#   nmkin_3  "Press 〇 in front of a ladder..."     -> CONFIRM F6 10  -> A
#   blin66_1 "Press △ to access the Menu."         -> MENU    F7     -> X
#   elm      "First press 〇2 to change the camera" -> CAMERA  F6 12  -> ZL
#
# SWITCH was the one inference and the key assignment above confirms it: the
# PC keymap binds OK/CANCEL/MENU/SWITCH to 〇/✕/△/☐, and this port maps that
# same set to A/B/X/Y -- so SWITCH is ☐ (F8), which draws as Y.
#
# PGUP and PGDN stay in words. They are L and R by the assignment above, but
# the byte code is not pinned: `anfrst_1` uses "〇3 or 〇1 + ✕" for the two
# shoulder buttons without saying which is which, and a 50/50 guess puts the
# WRONG button in front of the player. 26 occurrences, waiting on a source.
#
# The four directions stay in words too, and that is not a gap: the port
# writes "[Directional button]" itself (`blackbg1`) and ships no d-pad text
# code.
PC_BUTTON_GLYPH_BYTES = frozenset(
    list(range(0x90, 0x9C)) + [0xBE, 0xBF])
PC_BUTTON_OPEN = 0x08
PC_BUTTON_CLOSE = 0x09
# Keyed on the UPPER-CASED name, because Echo-S writes both `(CANCEL)` and
# `(Confirm)`. In this encoding lower case is upper case + 0x20.
PC_BUTTON_GLYPHS = {
    b'\x23\x21\x2e\x23\x25\x2c': b'\xf9',          # CANCEL  -> ✕
    b'\x23\x2f\x2e\x26\x29\x32\x2d': b'\xf6\x10',  # CONFIRM -> 〇
    b'\x2d\x25\x2e\x35': b'\xf7',                  # MENU    -> △
    b'\x33\x37\x29\x34\x23\x28': b'\xf8',          # SWITCH  -> ☐
    b'\x23\x21\x2d\x25\x32\x21': b'\xf6\x12',      # CAMERA  -> ZL
    # Not a glyph: the port's own md1_1 tutorial says "the Directional
    # buttons", so use its wording rather than leaving Echo-S's shouted
    # fallback in the middle of a sentence.
    b'\x24\x29\x32\x25\x23\x34\x29\x2f\x2e':
        b'\x24\x49\x52\x45\x43\x54\x49\x4f\x4e\x41\x4c',  # Directional
}


# A build-time statistic, not state the transform depends on: `build.py`
# reports it once so a change in the table is visible in the log.
BUTTON_GLYPHS_RETARGETED = 0


def _upper_name(name: bytes) -> bytes:
    return bytes(b - 0x20 if 0x41 <= b <= 0x5a else b for b in name)


_SECTION1_FIXED_HEADER = 32
_SECTION1_ROUTINES_PER_ACTOR = 32
_SECTION1_ROUTINE_TABLE_BYTES = _SECTION1_ROUTINES_PER_ACTOR * 2


class EchoFlevelError(ValueError):
    """The supplied archive or Echo-S field set is not structurally usable."""


def _wrapped_field_raw(data: bytes) -> bytes:
    if len(data) < 4 or int.from_bytes(data[:4], 'little') != len(data) - 4:
        raise EchoFlevelError('expected an LZS-wrapped field payload')
    return lgp.lzs_decompress(data[4:])


def _sections_and_trailer(raw: bytes):
    """Split a field while retaining its unsectioned terminal bytes.

    Most FF7 field files end in the literal 14-byte ``FINAL FANTASY7``
    trailer, while valid special fields such as ``blackbg1`` end directly at
    section 9.  ``lgp.split_sections`` intentionally returns only the nine
    declared sections, so recomposing it alone silently changes either form.
    """
    sections = lgp.split_sections(raw)
    pointers = struct.unpack_from('<9I', raw, 6)
    end = max(pointer + 4 + len(section)
              for pointer, section in zip(pointers, sections))
    trailer = raw[end:]
    if trailer not in (b'', lgp.TERMINATOR):
        raise EchoFlevelError('unexpected field trailer %r' % trailer)
    return sections, trailer


def strip_pc_speaker_prefixes(script_section: bytes):
    """Remove Echo-S's PC-only speaker and acquisition markup.

    Echo-S prepends selected speaker names with ``FE D6 <glyph> FE D9``.
    The PC renderer consumes the middle glyph as a coloured name marker; the
    Switch renderer instead displays it as a green, broken first character.
    The literal speaker name immediately following the sequence is already
    correct, so only this five-byte prefix is removed.  Echo-S also puts a
    one-byte PC item-category marker before many ``Obtained`` item names and
    sometimes wraps that name in a PC-only colour sequence.  On Switch the
    marker becomes a stray glyph (for example ``Obtained óPotion``).

    Cleanup is intentionally limited to item-result strings beginning with
    the FF7 field encoding of ``Obtained `` or ``Lost ``.  It must not remove
    ordinary field macros such as ``FE DE`` that supply a dynamic item count
    or item name.

    Field dialogue offsets are relative to the section string table.  Since
    removing prefixes changes all following offsets, rebuild the string area
    and update the complete offset table rather than editing bytes in place.
    """
    if len(script_section) < 8:
        raise EchoFlevelError('short field script section')
    string_offset = struct.unpack_from('<H', script_section, 4)[0]
    if string_offset + 2 > len(script_section):
        raise EchoFlevelError('field string table lies outside script section')
    count = struct.unpack_from('<H', script_section, string_offset)[0]
    table = string_offset + 2
    if table + count * 2 > len(script_section):
        raise EchoFlevelError('truncated field string offset table')
    offsets = list(struct.unpack_from('<%dH' % count, script_section, table))
    if not offsets:
        return script_section, 0
    unique_offsets = sorted(set(offsets))
    starts = [string_offset + value for value in unique_offsets]
    if starts[0] < table + count * 2 or starts[-1] >= len(script_section):
        raise EchoFlevelError('invalid field string offset')

    rebuilt = bytearray(script_section[:starts[0]])
    rewritten_offsets = {}
    removed = 0
    for index, (old_offset, start) in enumerate(zip(unique_offsets, starts)):
        end = starts[index + 1] if index + 1 < len(starts) else len(script_section)
        part = script_section[start:end]
        try:
            terminator = part.index(b'\xff')
        except ValueError as exc:
            raise EchoFlevelError('unterminated field dialogue string') from exc
        rewritten_offsets[old_offset] = len(rebuilt) - string_offset
        cleaned_part, changed = _strip_pc_field_markup(part)
        rebuilt.extend(cleaned_part)
        removed += changed

    for index, old_offset in enumerate(offsets):
        new_offset = rewritten_offsets[old_offset]
        if new_offset > 0xffff:
            raise EchoFlevelError('rebuilt field dialogue offset exceeds u16')
        struct.pack_into('<H', rebuilt, table + index * 2, new_offset)

    removed_bytes = len(script_section) - len(rebuilt)

    # AKAO blocks sit after the string data and their offsets are absolute
    # within section 1.  The original version of this normalizer updated only
    # the string-offset table, leaving MUSIC/BMUSC to dereference a position
    # ``removed_bytes`` into the wrong data.  This is invisible to text tests
    # yet prevents the native music dispatcher from seeing an AKAO header.
    actor_count = script_section[2]
    akao_count = struct.unpack_from('<H', script_section, 6)[0]
    akao_table = _SECTION1_FIXED_HEADER + actor_count * 8
    for index in range(akao_count):
        offset = akao_table + index * 4
        akao = struct.unpack_from('<I', rebuilt, offset)[0]
        if akao >= starts[0]:
            struct.pack_into('<I', rebuilt, offset, akao - removed_bytes)
    return bytes(rebuilt), removed


def _strip_pc_field_markup(part: bytes):
    """Return one complete string record without incompatible PC markup.

    ``part`` includes the trailing ``FF`` and any bytes up to the next string
    record.  The transformation keeps all ordinary control bytes byte-exact;
    only a verified Echo-S speaker prefix or an acquisition-name marker is
    removed.
    """
    changed = 0
    part, retargeted = _retarget_pc_button_glyphs(part)
    changed += retargeted
    if (len(part) >= 5 and part.startswith(PC_SPEAKER_PREFIX)
            and part[3:5] == PC_SPEAKER_SUFFIX):
        part = part[5:]
        changed += 1

    name_start = _acquisition_name_start(part)
    if name_start is None:
        return part, changed

    try:
        terminator = part.index(b'\xff')
    except ValueError as exc:
        raise EchoFlevelError('unterminated field dialogue string') from exc

    # The PC category/name marker is always outside the normal printable FF7
    # text range.  Do not touch lower values: those cover ordinary literal
    # text and dynamic item/quantity macros.
    if name_start < terminator and 0x61 <= part[name_start] <= 0xcf:
        part = part[:name_start] + part[name_start + 1:]
        terminator -= 1
        changed += 1

    # A colour wrapper may follow either that removed marker or the prefix
    # directly.  Strip only the documented PC colour openers (D2..D8), then
    # its first matching white/reset close before the field-string terminator.
    if (name_start + 1 < terminator and part[name_start] == 0xfe
            and part[name_start + 1] in PC_COLOUR_OPENERS):
        closing = part.find(PC_SPEAKER_SUFFIX, name_start + 2, terminator)
        if closing != -1:
            part = (part[:name_start] + part[name_start + 2:closing]
                    + part[closing + 2:])
            changed += 1

    return part, changed


def _retarget_pc_button_glyphs(part: bytes):
    """Replace Echo-S's PC button markup with what this port can draw.

    Rewrites `<glyph bytes> ( NAME )` to this port's glyph when `NAME` is one
    we have evidence for, and otherwise to `NAME` alone -- see the table above
    for why the fallback is words rather than a guessed icon.

    Only runs that are entirely glyph bytes, then `08`, then printable name
    bytes, then `09`, and that lie before the record's terminator, are
    touched. Anything else is left byte-exact: the parenthesis codes are
    ordinary punctuation elsewhere in the script and must not be disturbed.
    """
    end = part.find(b'\xff')
    if end < 0:
        end = len(part)
    out = bytearray()
    index = 0
    changed = 0
    while index < end:
        byte = part[index]
        if byte not in PC_BUTTON_GLYPH_BYTES:
            out.append(byte)
            index += 1
            continue
        cursor = index
        while cursor < end and part[cursor] in PC_BUTTON_GLYPH_BYTES:
            cursor += 1
        if cursor >= end or part[cursor] != PC_BUTTON_OPEN:
            # A glyph byte that is not introducing a button name. Leave it:
            # this range also carries ordinary punctuation.
            out.extend(part[index:cursor])
            index = cursor
            continue
        close = part.find(bytes([PC_BUTTON_CLOSE]), cursor + 1, end)
        if close < 0:
            out.extend(part[index:cursor])
            index = cursor
            continue
        name = bytes(part[cursor + 1:close])
        out.extend(PC_BUTTON_GLYPHS.get(_upper_name(name), name))
        index = close + 1
        changed += 1
    out.extend(part[end:])
    if changed:
        global BUTTON_GLYPHS_RETARGETED
        BUTTON_GLYPHS_RETARGETED += changed
    return bytes(out), changed


def _acquisition_name_start(part: bytes):
    """Return the first item-name byte for an Echo-S result string.

    Echo-S uses both ``Obtained <item>`` and ``Obtained/Lost Key Item
    <item>`` forms.  A few records carry one leading encoded space.  The
    category marker belongs immediately after these literal prefixes.
    """
    cursor = 1 if part.startswith(b'\x00') else 0
    for prefix in (OBTAINED_PREFIX, LOST_PREFIX):
        if part.startswith(prefix, cursor):
            cursor += len(prefix)
            if part.startswith(KEY_ITEM_PREFIX, cursor):
                cursor += len(KEY_ITEM_PREFIX)
            return cursor
    return None


def restore_switch_tutorial_dialogues(echo_section: bytes,
                                      stock_section: bytes,
                                      field_name: str | None):
    """Restore platform-specific tutorial strings from the Switch field.

    The Echo-S event flow remains in place; the two confirmed PC keyboard
    records and their associated window geometry are replaced.  Copying text
    alone is invalid: Echo's shorter PC windows clip the Switch strings and
    can leave MESSAGE waiting forever.  String and AKAO offsets are relocated
    exactly as they are for the PC-markup cleanup.
    """
    rules = STOCK_DIALOGUE_OVERRIDES.get((field_name or '').lower(), ())
    if not rules:
        return echo_section, 0

    echo_section, window_changes = _restore_switch_tutorial_windows(
        echo_section, stock_section, rules, field_name)

    def layout(section):
        if len(section) < 8:
            raise EchoFlevelError('short field script section')
        string_offset = struct.unpack_from('<H', section, 4)[0]
        if string_offset + 2 > len(section):
            raise EchoFlevelError('field string table lies outside script section')
        count = struct.unpack_from('<H', section, string_offset)[0]
        table = string_offset + 2
        if table + count * 2 > len(section):
            raise EchoFlevelError('truncated field string offset table')
        offsets = list(struct.unpack_from('<%dH' % count, section, table))
        return string_offset, table, offsets

    echo_string_offset, echo_table, echo_offsets = layout(echo_section)
    stock_string_offset, _stock_table, stock_offsets = layout(stock_section)
    replacements = {}
    for echo_index, stock_index in rules:
        if echo_index >= len(echo_offsets) or stock_index >= len(stock_offsets):
            raise EchoFlevelError('%s tutorial string index is unavailable' %
                                  field_name)
        stock_start = stock_string_offset + stock_offsets[stock_index]
        try:
            stock_end = stock_section.index(b'\xff', stock_start) + 1
        except ValueError as exc:
            raise EchoFlevelError('%s stock tutorial is unterminated' %
                                  field_name) from exc
        replacements[echo_offsets[echo_index]] = stock_section[stock_start:stock_end]

    unique_offsets = sorted(set(echo_offsets))
    starts = [echo_string_offset + value for value in unique_offsets]
    if (not starts or starts[0] < echo_table + len(echo_offsets) * 2 or
            starts[-1] >= len(echo_section)):
        raise EchoFlevelError('invalid field string offset')
    rebuilt = bytearray(echo_section[:starts[0]])
    rewritten_offsets = {}
    changed = 0
    for index, (old_offset, start) in enumerate(zip(unique_offsets, starts)):
        end = starts[index + 1] if index + 1 < len(starts) else len(echo_section)
        part = echo_section[start:end]
        try:
            terminator = part.index(b'\xff')
        except ValueError as exc:
            raise EchoFlevelError('unterminated field dialogue string') from exc
        rewritten_offsets[old_offset] = len(rebuilt) - echo_string_offset
        replacement = replacements.get(old_offset)
        if replacement is not None:
            rebuilt.extend(replacement)
            rebuilt.extend(part[terminator + 1:])
            changed += 1
        else:
            rebuilt.extend(part)

    for index, old_offset in enumerate(echo_offsets):
        new_offset = rewritten_offsets[old_offset]
        if new_offset > 0xffff:
            raise EchoFlevelError('rebuilt field dialogue offset exceeds u16')
        struct.pack_into('<H', rebuilt, echo_table + index * 2, new_offset)

    delta = len(rebuilt) - len(echo_section)
    actor_count = echo_section[2]
    akao_count = struct.unpack_from('<H', echo_section, 6)[0]
    akao_table = _SECTION1_FIXED_HEADER + actor_count * 8
    for index in range(akao_count):
        offset = akao_table + index * 4
        akao = struct.unpack_from('<I', rebuilt, offset)[0]
        if akao >= starts[0]:
            struct.pack_into('<I', rebuilt, offset, akao + delta)
    return bytes(rebuilt), changed + window_changes


def _dialogue_window_sites(script_section: bytes, dialog_id: int):
    """Return ``(WINDOW offset, window id, geometry)`` for a MESSAGE.

    WINDOW configuration is stateful, so retain the latest WINDOW for each
    window id while decoding section 1 at real instruction boundaries.
    """
    actor_count = script_section[2]
    akao_count = struct.unpack_from('<H', script_section, 6)[0]
    string_offset = struct.unpack_from('<H', script_section, 4)[0]
    cursor = (_SECTION1_FIXED_HEADER + actor_count * 8 + akao_count * 4
              + actor_count * _SECTION1_ROUTINE_TABLE_BYTES)
    windows = {}
    sites = []
    while cursor < string_offset:
        opcode = script_section[cursor]
        try:
            size = instruction_size(script_section, cursor)
        except (IndexError, ValueError) as exc:
            raise EchoFlevelError('cannot decode tutorial field script') from exc
        if cursor + size > string_offset:
            raise EchoFlevelError('tutorial instruction exceeds script code')
        if opcode == 0x50 and size == 10:  # WINDOW n,x,y,w,h
            window_id = script_section[cursor + 1]
            geometry = struct.unpack_from('<4H', script_section, cursor + 2)
            windows[window_id] = (cursor, geometry)
        elif opcode == 0x40 and size == 3:  # MESSAGE window,dialogue
            window_id = script_section[cursor + 1]
            current_dialogue = script_section[cursor + 2]
            if current_dialogue == dialog_id and window_id in windows:
                window_offset, geometry = windows[window_id]
                sites.append((window_offset, window_id, geometry))
        cursor += size
    return sites


def _restore_switch_tutorial_windows(echo_section: bytes,
                                     stock_section: bytes, rules,
                                     field_name):
    """Copy stock WINDOW dimensions for the selected tutorial MESSAGE."""
    result = bytearray(echo_section)
    changed = 0
    for echo_dialogue, stock_dialogue in rules:
        stock_sites = _dialogue_window_sites(stock_section, stock_dialogue)
        echo_sites = _dialogue_window_sites(echo_section, echo_dialogue)
        if not stock_sites or not echo_sites:
            raise EchoFlevelError('%s tutorial window is unavailable' %
                                  field_name)
        # nmkin_3 contains a later reminder at the top of the screen.  The
        # reported story tutorial is the first occurrence, whose bottom
        # window exactly matches the original Switch screenshot.
        stock_geometry = stock_sites[0][2]
        for window_offset, _window_id, old_geometry in echo_sites:
            if old_geometry != stock_geometry:
                struct.pack_into('<4H', result, window_offset + 2,
                                 *stock_geometry)
                changed += 1
    return bytes(result), changed


def disable_pc_time_cycle_actor(script_section: bytes):
    """Turn Echo-S's FFNx-only ``Time`` actor into a harmless no-op.

    Echo-S's default PC configuration enables its Day/Night add-on.  Its field
    files append an actor literally named ``Time`` whose init/main routines
    write into PC-specific field-bank addresses.  FFNx's ``ff7::Time`` service
    reads those addresses every frame.  The Switch has neither that service
    nor the same bank layout, so executing those routines corrupts unrelated
    native field state (including music state).

    Removing an actor would shift every following routine pointer and can also
    invalidate cross-actor ``REQ`` calls.  Keep the actor and all layout
    offsets instead, but replace the entry byte of each routine owned solely
    by it with ``RET``.  Calls to it then safely return immediately.  The
    existing code/string offsets and every non-Time routine remain byte exact.
    """
    if len(script_section) < _SECTION1_FIXED_HEADER:
        raise EchoFlevelError('short field script section')
    actor_count = script_section[2]
    akao_count = struct.unpack_from('<H', script_section, 6)[0]
    string_offset = struct.unpack_from('<H', script_section, 4)[0]
    names_offset = _SECTION1_FIXED_HEADER
    routines_offset = names_offset + actor_count * 8 + akao_count * 4
    script_start = routines_offset + actor_count * _SECTION1_ROUTINE_TABLE_BYTES
    if (routines_offset > len(script_section) or script_start > string_offset or
            string_offset > len(script_section)):
        raise EchoFlevelError('invalid field script layout')

    time_index = None
    for index in range(actor_count):
        name = script_section[names_offset + index * 8:names_offset + (index + 1) * 8]
        if name.rstrip(b'\0') == b'Time':
            time_index = index
            break
    if time_index is None:
        return script_section, 0

    all_routines = []
    for index in range(actor_count):
        start = routines_offset + index * _SECTION1_ROUTINE_TABLE_BYTES
        all_routines.append(struct.unpack_from('<32H', script_section, start))
    time_routines = set(all_routines[time_index])
    shared = set(offset for index, routines in enumerate(all_routines)
                 if index != time_index for offset in routines)
    starts = sorted(offset for offset in time_routines
                    if script_start <= offset < string_offset and offset not in shared)
    if not starts:
        raise EchoFlevelError('Time actor has no private executable routines')

    result = bytearray(script_section)
    targets = set(starts)
    # A field actor's default routine contains its init followed by its main
    # routine.  The latter is entered immediately after the first RET even
    # though it has no separate header-table pointer, so disable that entry
    # too.  Decode instruction lengths instead of searching raw bytes: zero is
    # also a perfectly valid operand in many instructions.
    for start in starts:
        cursor = start
        while cursor < string_offset:
            if script_section[cursor] == 0x00:
                if cursor + 1 < string_offset:
                    targets.add(cursor + 1)
                break
            try:
                cursor += instruction_size(script_section, cursor)
            except (IndexError, ValueError) as exc:
                raise EchoFlevelError('cannot decode Time actor routine') from exc
        else:
            raise EchoFlevelError('Time actor default routine has no RET')
    for offset in sorted(targets):
        result[offset] = 0x00  # RET
    return bytes(result), len(targets)


def remove_appended_pc_time_actor(script_section: bytes):
    """Remove Echo-S's final FFNx-only ``Time`` actor when it is append-only.

    Returning from Time's routines is insufficient for the Switch field
    runtime.  The actor count itself feeds the native field object's actor
    array, and an extra actor changes the state layout despite never running
    its script.  Echo-S appends Time to most fields (including ``elm`` and
    ``mrkt2``); those fields can be repaired structurally without changing a
    single remaining script byte.

    Actor routine entries are section-relative offsets.  Deleting the final
    name (8 bytes) and its 32-entry routine table (64 bytes) moves all script
    and string data back by 72 bytes, so every surviving routine entry and the
    section's string offset need the same adjustment.  Time is deliberately
    accepted only as the final actor: removing a middle actor would alter
    ``REQ`` actor indices and must be handled by a separate, field-audited
    transform.
    """
    if len(script_section) < _SECTION1_FIXED_HEADER:
        raise EchoFlevelError('short field script section')
    actor_count = script_section[2]
    if not actor_count:
        return script_section, False
    akao_count = struct.unpack_from('<H', script_section, 6)[0]
    string_offset = struct.unpack_from('<H', script_section, 4)[0]
    names_offset = _SECTION1_FIXED_HEADER
    routines_offset = names_offset + actor_count * 8 + akao_count * 4
    script_start = routines_offset + actor_count * _SECTION1_ROUTINES_PER_ACTOR * 2
    if script_start > string_offset or string_offset > len(script_section):
        raise EchoFlevelError('invalid field script layout')
    time_name = script_section[names_offset + (actor_count - 1) * 8:
                               names_offset + actor_count * 8]
    if time_name.rstrip(b'\0') != b'Time':
        return script_section, False

    # Delete only metadata.  Time's code remains in the section but becomes
    # unreachable; leaving it in place avoids relocating branch operands.
    result = bytearray(script_section)
    del result[names_offset + (actor_count - 1) * 8:names_offset + actor_count * 8]
    new_routines_offset = names_offset + (actor_count - 1) * 8 + akao_count * 4
    del result[new_routines_offset + (actor_count - 1) * _SECTION1_ROUTINES_PER_ACTOR * 2:
               new_routines_offset + actor_count * _SECTION1_ROUTINES_PER_ACTOR * 2]
    result[2] = actor_count - 1
    struct.pack_into('<H', result, 4, string_offset - 72)

    # AKAO offsets, like actor routine entries, are absolute within section 1.
    # The field MUSIC opcode dereferences this table before it calls the sound
    # interpreter, so leaving these 72 bytes stale makes a perfectly ordinary
    # ``MUSIC 0`` use the middle of an AKAO block as its header.
    new_akao_offset = names_offset + (actor_count - 1) * 8
    for index in range(akao_count):
        offset = new_akao_offset + index * 4
        akao = struct.unpack_from('<I', result, offset)[0]
        if akao >= script_start:
            struct.pack_into('<I', result, offset, akao - 72)

    # The table is now compact.  All routine starts moved back with the code.
    entries = (actor_count - 1) * _SECTION1_ROUTINES_PER_ACTOR
    for index in range(entries):
        offset = new_routines_offset + index * 2
        routine = struct.unpack_from('<H', result, offset)[0]
        if routine >= script_start:
            struct.pack_into('<H', result, offset, routine - 72)
    return bytes(result), True


def validate_akao_offsets(script_section: bytes):
    """Reject a script whose MUSIC/BMUSC AKAO table was not relocated.

    The first entry starts the ``AKAO`` container.  Further entries are valid
    internal cue boundaries, not independently headed AKAO blobs.
    """
    if len(script_section) < _SECTION1_FIXED_HEADER:
        raise EchoFlevelError('short field script section')
    actor_count = script_section[2]
    akao_count = struct.unpack_from('<H', script_section, 6)[0]
    table = _SECTION1_FIXED_HEADER + actor_count * 8
    if table + akao_count * 4 > len(script_section):
        raise EchoFlevelError('truncated AKAO offset table')
    previous = None
    for index in range(akao_count):
        offset = struct.unpack_from('<I', script_section, table + index * 4)[0]
        if offset >= len(script_section) or (previous is not None and
                                             offset <= previous):
            raise EchoFlevelError('AKAO block %d has invalid offset 0x%X' %
                                  (index, offset))
        previous = offset
    if akao_count and script_section[struct.unpack_from('<I', script_section,
                                                          table)[0]:][:4] != b'AKAO':
        raise EchoFlevelError('AKAO container has no AKAO header')


def model_loader_names(section3: bytes):
    """The model names of a field's section 3, in the index order scripts use.

    Layout per `kujata`'s `flevel-loader.js`: name, u16, 8-byte HRC id,
    4-byte scale string, u16 animation count, three 9-byte lights and a
    3-byte global light, then the animation names.
    """
    _blank, count, _scale = struct.unpack_from('<hhh', section3, 0)
    cursor = 6
    names = []
    for _ in range(count):
        length, = struct.unpack_from('<H', section3, cursor)
        cursor += 2
        names.append(bytes(section3[cursor:cursor + length]).rstrip(b'\0')
                     .decode('ascii', 'replace'))
        cursor += length + 2 + 8 + 4
        animations, = struct.unpack_from('<H', section3, cursor)
        cursor += 2 + 30
        for _animation in range(animations):
            length, = struct.unpack_from('<H', section3, cursor)
            cursor += 2 + length + 2
    return names


# Fields whose model loader Echo-S changed in a way this port cannot follow,
# recorded for the build log. See `_model_loader_is_portable`.
UNPORTABLE_MODEL_LOADERS = []

# Fields whose loader was taken in Echo-S's ORDER using this port's own asset
# records, matched by HRC id. See `remap_model_loader`.
REMAPPED_MODEL_LOADERS = []

# Fields where Echo-S's loader names different things but every index already
# means the same asset here, so keeping ours is correct rather than a
# compromise. A sentinel, not bytes, so a caller cannot mistake it for one.
EQUIVALENT_MODEL_LOADER = object()
EQUIVALENT_MODEL_LOADERS = []


def model_loader_records(section3: bytes):
    """
    [(name, hrc id, byte range)] for a field's model loader, in index order.

    Layout per `kujata`'s `flevel-loader.js`: name, u16, 8-byte HRC id,
    4-byte scale, u16 animation count, three 9-byte lights and a 3-byte
    global light, then the animation names.
    """
    _blank, count, _scale = struct.unpack_from('<hhh', section3, 0)
    cursor = 6
    out = []
    for _ in range(count):
        start = cursor
        length, = struct.unpack_from('<H', section3, cursor)
        cursor += 2
        name = bytes(section3[cursor:cursor + length]).rstrip(b'\0') \
            .decode('ascii', 'replace')
        cursor += length + 2
        hrc = bytes(section3[cursor:cursor + 8]).rstrip(b'\0 ').upper()
        cursor += 8 + 4
        animations, = struct.unpack_from('<H', section3, cursor)
        cursor += 2 + 30
        for _animation in range(animations):
            length, = struct.unpack_from('<H', section3, cursor)
            cursor += 2 + length + 2
        out.append((name, hrc, (start, cursor)))
    return out


def remap_model_loader(echo_section3: bytes, stock_section3: bytes):
    """
    The Switch's own model records, reordered into ECHO-S's index order.

    THE PINBALL MACHINE IS A MAN, THE SEQUEL.
    =========================================
    `_model_loader_is_portable` compares model NAMES, and this port keys them
    per field (`mds7pb_2fieldbg_pinbl.char`). Echo-S does not always keep those
    names -- in `mds7pb_2`, the lower floor of the bar, its loader reads:

        0..6  the same seven names the Switch has
        7     Pinball      <- was mds7pb_2fieldbg_pinbl.char (index 9)
        8     Shake        <- was mds7pb_2nible_camera.char   (index 7)

    and it drops `fieldbg_mtra8` entirely. Name comparison sees two assets
    this field does not have, refuses the loader, and keeps the Switch's --
    so Echo-S's script asks for model 7 and gets `nible_camera`, a person.
    Exactly the `mds7pb_1` bug, one floor down, and immune to the fix that
    solved it because there the names still matched.

    THE 8-BYTE HRC ID IS THE MATCH, NOT THE NAME. Every record carries the
    skeleton it loads, and Echo-S keeps it: `Pinball` carries `CZED.HRC`,
    which IS `mds7pb_2fieldbg_pinbl.char`; `Shake` carries `CYIF.HRC`, which
    is `nible_camera`. So the port can honour Echo-S's ORDER while keeping its
    own asset records -- names, scale and animation names complete with the
    extensions Echo-S strips (`CZFF.tor` against its `CZFF`).

    Returns the rebuilt section 3, or None when any Echo-S record's HRC is
    absent from the Switch loader or appears in it twice. A guess here renders
    the wrong model and says nothing, so an unmatched field keeps today's
    behaviour and is reported.
    """
    try:
        echo = model_loader_records(echo_section3)
        stock = model_loader_records(stock_section3)
    except (struct.error, IndexError):
        return None
    if not echo or not stock:
        return None
    by_hrc = {}
    for record in stock:
        by_hrc.setdefault(record[1], []).append(record)
    chosen = []
    for _name, hrc, _span in echo:
        candidates = by_hrc.get(hrc) or []
        if len(candidates) != 1:
            return None
        chosen.append(candidates[0])
    if [r[2] for r in chosen] == [r[2] for r in stock[:len(chosen)]]:
        # SAME ORDER, SAME COUNT OF LEADING RECORDS: every index Echo-S's
        # script can name already means the same asset here, so the Switch
        # loader is EQUIVALENT and keeping it is correct. Say so with a
        # distinct answer -- calling this "unportable" sent me looking for a
        # model bug in `blin67_2` that does not exist.
        return EQUIVALENT_MODEL_LOADER
    out = bytearray(stock_section3[:6])
    struct.pack_into('<h', out, 2, len(chosen))
    for _name, _hrc, (start, end) in chosen:
        out += stock_section3[start:end]
    return bytes(out)


def _model_loader_is_portable(echo_section3: bytes, stock_section3: bytes):
    """
    May this field take Echo-S's model loader?

    AN ENTITY BINDS TO A MODEL BY INDEX, SO SECTION 1 AND SECTION 3 ARE ONE
    UNIT.
    ======================================================================
    Importing the script alone was wrong wherever Echo-S also edited the
    loader. `mds7pb_1` is the case that surfaced it: Echo-S drops the
    `camera` model, so every index above it shifts down by one, and its
    script's "model 7" -- the pinball machine, `fieldbg_pinbl` -- resolves
    against the Switch loader's index 7, which is `nible_camera`, a person.
    The bar's pinball machine renders as a man. Measured over the release,
    the loader differs in 102 of 702 fields.

    Taking Echo-S's loader is safe only while every model it names is one
    this field already had: the port keys models per FIELD
    (`mds7pb_1fieldbg_pinbl.char`), so a name borrowed from another field --
    Echo-S does this in 27 fields, e.g. `blin67_2` wanting
    `md1stinshinra_guard.char` -- is an asset this field cannot load. Those
    keep the Switch loader and are named in the build log; they are the
    remaining known-mismapped set, and fixing them means supplying the
    asset, not choosing a different section.
    """
    try:
        echo = model_loader_names(echo_section3)
        stock = set(model_loader_names(stock_section3))
    except (struct.error, IndexError):
        return False
    return all(name in stock for name in echo if name)


def merge_field_payload(stock_payload: bytes, echo_payload: bytes,
                        sections=SCRIPT_UNIT_SECTIONS, field_name=None):
    """Return a Switch field payload with selected Echo-S field sections.

    The result is encoded through the archive's verified LZS implementation
    by the caller.  The second return value lists the imported sections.
    ``sections`` is explicit because PC model/tile payloads are not portable
    merely because their associated script changed.  The compatibility default
    imports section 1 alone and retains all Switch non-script data.
    """
    sections = frozenset(sections)
    if not sections or any(section < 1 or section > 9 for section in sections):
        raise EchoFlevelError('invalid field section selection: %r' %
                              (tuple(sorted(sections)),))
    stock_sections, stock_trailer = _sections_and_trailer(
        _wrapped_field_raw(stock_payload))
    echo_sections, _echo_trailer = _sections_and_trailer(
        _wrapped_field_raw(echo_payload))
    # Echo-S field strings contain an extra PC renderer control sequence at
    # the beginning of speaker labels.  It must be normalized before section
    # 1 is merged, while sections 3/6 remain byte-identical to the source.
    echo_sections[0], _removed_prefixes = strip_pc_speaker_prefixes(
        echo_sections[0])
    echo_sections[0], _restored_tutorials = restore_switch_tutorial_dialogues(
        echo_sections[0], stock_sections[0], field_name)
    # Echo-S's optional PC Day/Night system is enabled by default upstream.
    # When Time is append-only, remove its metadata completely: the Switch's
    # actor-array layout must remain the stock layout.  A small minority of
    # fields replace/reorder an existing actor; keep the conservative routine
    # neutralization there until that actor mapping has been audited.
    echo_sections[0], _removed_time_actor = remove_appended_pc_time_actor(
        echo_sections[0])
    if not _removed_time_actor:
        echo_sections[0], _disabled_time_routines = disable_pc_time_cycle_actor(
            echo_sections[0])
    validate_akao_offsets(echo_sections[0])
    # Section 3 travels with section 1 when it can: they are one unit, because
    # a script binds an entity to a model by INDEX into this list.
    sections = set(sections)
    if 1 in sections and bytes(echo_sections[2]) != bytes(stock_sections[2]):
        if _model_loader_is_portable(echo_sections[2], stock_sections[2]):
            sections.add(3)
        else:
            # The names are not ones this field has -- but the 8-byte HRC ids
            # still are, so Echo-S's ORDER can be honoured with the Switch's
            # own records. This is what makes `mds7pb_2`'s pinball a pinball.
            remapped = remap_model_loader(echo_sections[2],
                                          stock_sections[2])
            if remapped is EQUIVALENT_MODEL_LOADER:
                if field_name and field_name not in EQUIVALENT_MODEL_LOADERS:
                    EQUIVALENT_MODEL_LOADERS.append(field_name)
            elif remapped is not None:
                echo_sections[2] = remapped
                sections.add(3)
                if field_name and field_name not in REMAPPED_MODEL_LOADERS:
                    REMAPPED_MODEL_LOADERS.append(field_name)
            elif field_name and field_name not in UNPORTABLE_MODEL_LOADERS:
                UNPORTABLE_MODEL_LOADERS.append(field_name)
    imported = []
    for section in sections:
        index = section - 1
        if echo_sections[index] != stock_sections[index]:
            stock_sections[index] = echo_sections[index]
            imported.append(section)
    return (lgp.join_sections(stock_sections) + stock_trailer,
            tuple(sorted(imported)))


def build_archive(stock_archive: str, echo_field_dir: str, destination: str,
                  fields=None, log=print, sections=SCRIPT_UNIT_SECTIONS):
    """Build an flevel.lgp containing Echo-S script data.

    ``fields`` may restrict a hardware test to specific field basenames.  It
    only limits field replacements; the complete vanilla LGP layout remains
    intact, which is necessary for the game's fixed lookup table.
    """
    if not os.path.isfile(stock_archive):
        raise FileNotFoundError(stock_archive)
    if not os.path.isdir(echo_field_dir):
        raise FileNotFoundError(echo_field_dir)
    wanted = None if fields is None else {x.lower() for x in fields}
    archive = lgp.Archive(stock_archive)
    replacements = {}
    stats = {'fields': 0, 'sections': {section: 0 for section in SCRIPT_UNIT_SECTIONS},
             'skipped': 0}
    for name in archive.names():
        if wanted is not None and name not in wanted:
            continue
        source = os.path.join(echo_field_dir, name)
        if not os.path.isfile(source):
            continue
        entry = archive.index[name]
        try:
            with open(source, 'rb') as handle:
                merged_raw, imported = merge_field_payload(
                    entry['payload'], handle.read(), sections=sections,
                    field_name=name)
        except (OSError, ValueError, EchoFlevelError) as exc:
            # Echo-S also carries non-field helper files (e.g. nothing.akao).
            # A real matching field must not be silently ignored.
            if name in archive.index and archive.is_field(entry):
                raise EchoFlevelError('%s: %s' % (name, exc)) from exc
            stats['skipped'] += 1
            continue
        if not imported:
            continue
        # Do not use the project's fast field compressor here.  It round-
        # trips in its paired decoder but has historically emitted streams
        # rejected by the independent FF7 LZS decoder on this script shape.
        # Literal LZS is bigger but format-simple and accepted by both.
        replacements[name] = archive.encode_field(merged_raw, compress=False)
        stats['fields'] += 1
        for section in imported:
            stats['sections'][section] += 1
    if wanted is not None:
        missing = sorted(wanted - set(replacements))
        if missing:
            raise EchoFlevelError('Echo-S supplied no changed field data for: %s'
                                  % ', '.join(missing))
    if not replacements:
        raise EchoFlevelError('no Echo-S field script changes were staged')
    archive.replace(replacements)
    archive.write(destination)
    check = lgp.Archive(destination)
    for name, payload in replacements.items():
        if check.index[name]['payload'] != payload:
            raise EchoFlevelError('archive verification failed for %s' % name)
    log('Echo-S flevel: %d field(s), imported %s'
        % (stats['fields'], ', '.join(
            'section %d=%d' % (section, stats['sections'][section])
            for section in sorted(stats['sections']))))
    return stats
