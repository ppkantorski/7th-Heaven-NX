#!/usr/bin/env python3
"""
ff7nx_calendar.py -- the date and clock in the main menu, the half of Echo-S's
Day/Night that is a PC executable patch rather than FFNx or field script.

WHAT THIS IS
============
Echo-S's Time Keeper tutorial (field `md1stin`, string 10) says, in its own
words: "You can check the date/time in your menu and plan around it." On PC
that sentence is delivered by `DayNight/hext/03 - Calendar.txt`, a Hext patch
to `ff7.exe`. It does four things to the menu's Time/Gil box:

    * stretches it and moves it up, to make room for a third line;
    * puts Gil on the first line and Time on the second;
    * repoints the Time field at the mod's own clock instead of the play
      timer, and hides the seconds;
    * draws a third line: `Date`, the day, a separator and the month's name.

None of that reached this port, because none of it is script or FFNx: the
Switch build is an ARM64 recompilation of the same x86, so a Hext file has
nothing to patch. This module does the same work on the recompiled code.

WHERE IT ALL LIVES, AND WHY THAT IS NOT A GUESS
===============================================
Every site the Hext file touches falls inside ONE recompiled function --
x86 `0x6CA346`, the menu's Time/Gil box, at ARM `+0xCFFEA0` -- and each one
was located by a constant that appears exactly once in it. `0x15C` is the box
Y and appears once; `0x42`, `0x57`, `0x61`, `0x76`, `0x80` are the five
horizontal offsets of the clock's five pieces, once each; `0xD5` is the colon.
The four drawing helpers were matched the way this project matches call sites
everywhere: by ORDER. Each is called exactly twice in the region, in the same
order as the x86.

    x86 0x6F9739  draw number  -> 0xCE3E20   hours  +D02930, gil     +D02E18
    x86 0x6F9C44  draw number  -> 0xCAF0B0   minutes+D02AEC, seconds +D02C84
    x86 0x6F5C0C  draw char    -> 0xCC6270   colon1 +D029F8, colon2  +D02B90
    x86 0x6F5B03  draw string  -> 0x10E4110  "Time" +D02D4C, "Gil"   +D02EC0

`FINDINGS-489` is the full table and how each was cross-checked.

TWO PLACES THIS DELIBERATELY DIVERGES FROM THE HEXT FILE
========================================================
**THE TWO TEXT HELPERS DO NOT SHARE A CHARACTER SET.** A first version drew
`Date` and the month a character at a time through the char helper, using the
codes the strings in this module use -- `A`=0x21, `a`=0x41, which decode this
build's own "Time" (`34 49 4D 45`) and "Gil" (`27 49 4C`) correctly and match
the mod's config.toml month table byte for byte. On hardware they came out as
Thai glyphs. The char helper indexes a font directly; only the colon and the
slash (`0xD5`, `0xD4`) happen to mean the same thing in both, which is why the
separator was the one piece of that row that looked right.

So the text goes through the STRING helper, like "Time" and "Gil" do, and the
strings are written into guest memory by the cave itself. Where they go is not
a guess either: the recompiled code pushes `0x91AAF8 - 0x120` for "Time",
which is `0x91A9D8` -- the same address the PC executable holds it at. The
guest data layout IS the one the Hext file was written against, so its own two
parking spots are used, and the build verifies they are still zero in the
shipped `ff7_en` before it writes anything.

**The Time label's X is conditional here, and that is why it did not line up
with Gil.** The PC draws it at a constant +2 and the Hext changes that to 6.
This build computes it: `mov w9, #6` then `csel w9, w9, w23, ne` on a runtime
flag, so it is 6 or something else depending on that flag -- and on hardware
it came out at the something else. The `csel` becomes a `nop`, which leaves
the 6 the instruction before it already loaded.

WHAT IS REUSED RATHER THAN HIDDEN, AND WHY IT HAD TO BE
=======================================================
The Hext hides the seconds and the second colon, then draws four new things.
A first cut of this module did the same and cost 355 cave words. There were
155 left in the widest window by the time this pass runs, so it reported the
shortfall and skipped itself -- which is the fail-closed behaviour working,
but it is not a feature.

So the two draws the Hext throws away are reused instead, because they are
already the right shape:

    the seconds  a two-digit number -> the DAY,       value repointed
    the colon 2  one character      -> the SEPARATOR, character repointed

Each costs one replaced instruction and a three-to-seven word cave, and
between them they remove both of the expensive draws from the date cave. What
is left -- `Date` and the month's three letters, seven characters through one
shared subroutine -- is 91 words.
"""

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import a64 as A                                                # noqa: E402
import ff7nx_audio_cave as AC                                  # noqa: E402
import ff7nx_cave                                              # noqa: E402
import ff7nx_tables                                            # noqa: E402

Asm = AC.Asm
GUEST_TRANSLATE = AC.GUEST_TRANSLATE

ENV = 'SEVENTH_NX_CALENDAR'

# ------------------------------------------------------------ the guest side
# The same bytes `ff7nx_daynight` runs the clock in, which are the same bytes
# Echo-S's own config names. Bank 1 of the field script variables, which is
# inside the savemap, which is why the time is part of your save.
G_BANK1 = 0xDC08DC
G_MINUTES = G_BANK1 + 0x0C
G_MONTHS = G_BANK1 + 0x0D
G_DAYS = G_BANK1 + 0x0E
G_HOURS = G_BANK1 + 0x0F

# ------------------------------------------------------- the drawing helpers
DRAW_NUMBER = 0xCAF0B0                # x86 0x6F9C44 (x, y, value, digits, c, s)
DRAW_CHAR = 0xCC6270                  # x86 0x6F5C0C (x, y, char, colour, s)
DRAW_STRING = 0x10E4110               # x86 0x6F5B03 (x, y, ptr, colour, s)

TEXT_COLOUR = 7
TEXT_SCALE = 0x3E4CCCCD               # 0.2f, what every draw here is passed

# ------------------------------------------------------------- the charset
# A=0x21..Z=0x3A, a=0x41..z=0x5A, verified three ways: against this build's own
# "Time" (34 49 4D 45) and "Gil" (27 49 4C) strings, against the Hext file's
# "Date" (24 41 54 45), and against the mod's own config.toml month table,
# whose month 0 is 42/65/78 = 2A 41 4E = "Jan".
SEPARATOR = 0xD4                      # '/', through the CHAR helper, proven
COLON = 0xD5                          # ':', already in this function

# Where the two strings live, which are the Hext file's own two parking spots
# in a 15,576-byte run of zeros in the guest image's .data. `check_guest` is
# what stops this being taken on trust.
LABEL_AT = 0x915000                   # "Date", 5 bytes
MONTH_AT = 0x913FB1                   # three letters and a terminator
TERMINATOR = 0xFF


def _char(letter):
    if 'A' <= letter <= 'Z':
        return 0x21 + ord(letter) - ord('A')
    if 'a' <= letter <= 'z':
        return 0x41 + ord(letter) - ord('a')
    raise ValueError('%r is not a letter this charset covers' % letter)


MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
          'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
LABEL = 'Date'


def month_table():
    """12 x 3 characters, indexed by the month byte. 36 bytes."""
    out = bytearray()
    for name in MONTHS:
        if len(name) != 3:
            raise ValueError('month names are three characters')
        out += bytes(_char(c) for c in name)
    return bytes(out)


TABLE_BYTES = len(month_table())

# --------------------------------------------------------------- the layout
# Rows. The box's own two rows sit at Y+0x0C and Y+0x2A for a label and +4
# below that for its value; a character (the colon) sits 2 below the value.
# The third row continues the same 30-pixel pitch, and these are the offsets
# the Hext file uses for it too.
ROW3_LABEL_Y = 0x46
ROW3_VALUE_Y = ROW3_LABEL_Y + 4       # 0x4A, the day
ROW3_CHAR_Y = ROW3_VALUE_Y + 2        # 0x4C, the separator

# Columns. A character is about 0x0B wide, measured off the clock: hours at
# 0x5E, colon at 0x74, minutes at 0x7F.
#
# The order is the PC build's: `Date`, then the MONTH, then the separator,
# then the day. That is what the mod's own screenshot shows -- `Date Feb/ 2`
# -- and it took a moment to see why, because the Hext file's day is drawn at
# X+0x20, well to the LEFT of its month at X+0x48. It is drawn right-aligned
# in a ten-digit field, exactly as Gil is on its own row, so it lands at the
# right-hand end. Ours is a plain two-digit draw at a fixed column, so it is
# simply put where it ends up there.
CHAR_WIDTH = 0x0B
LABEL_X = 0x06                        # the margin Gil and Time labels use

# The clock row, which is also the ruler. These three are the values the
# constant patches below put into the time row.
CLOCK_HOURS_X = 0x5E
CLOCK_COLON_X = 0x74
CLOCK_MINUTES_X = 0x7F

# THE DAY IS RIGHT-ALIGNED WITH THE CLOCK BY CONSTRUCTION, NOT BY EYE.
# It is the menu's own seconds draw: the same helper as the minutes, with the
# same two digits. Give it the same X and its right edge is necessarily the
# same as theirs, which is the edge Gil's number ends on too. A first cut put
# it at 0x74 and left an 0x0B gap -- one character's worth, which is exactly
# what you get for guessing at this instead of deriving it.
DAY_X = CLOCK_MINUTES_X
SEPARATOR_X = DAY_X - 0x10
MONTH_X = SEPARATOR_X - 0x24

BOX_WIDTH = 0xA8

LAYOUT_ENV = 'SEVENTH_NX_CALENDAR_LAYOUT'


def layout_from_env():
    """`month,separator,day` X offsets, for nudging without a rebuild of me."""
    raw = (os.environ.get(LAYOUT_ENV, '') or '').strip()
    if not raw:
        return MONTH_X, SEPARATOR_X, DAY_X
    try:
        month, sep, day = (int(part, 0) for part in raw.split(','))
    except ValueError:
        raise ValueError('%s=%r is not three numbers: month,separator,day'
                         % (LAYOUT_ENV, raw))
    for value in (month, sep, day):
        if not 0 <= value <= BOX_WIDTH - CHAR_WIDTH:
            raise ValueError('%s: %#x is outside the box (width %#x)'
                             % (LAYOUT_ENV, value, BOX_WIDTH))
    return month, sep, day


# ------------------------------------------------- the plain constant patches
# (address, the word that must be there, the word to write, what it is).
# Every one is an immediate inside one instruction; nothing moves.
def _add_imm(word, imm):
    """The same `add wD, wN, #imm` with a different immediate."""
    return (word & ~(0xFFF << 10)) | ((imm & 0xFFF) << 10)


def _movz_imm(word, imm):
    return (word & ~(0xFFFF << 5)) | ((imm & 0xFFFF) << 5)


CONSTANTS = (
    # the box itself: up by 0x22 and 0x20 taller, so the third line fits
    (0xD02830, 0x52802B88, _movz_imm(0x52802B88, 0x13A), 'box Y 0x15C -> 0x13A'),
    (0xD02ED4, 0x52800908, _movz_imm(0x52800908, 0x68), 'box height 0x48 -> 0x68'),
    # Gil moves to the first row, Time to the second
    (0xD02E6C, 0x1100A915, _add_imm(0x1100A915, 0x0C), 'Gil label Y -> row 1'),
    (0xD02DC4, 0x1100B915, _add_imm(0x1100B915, 0x10), 'gil number Y -> row 1'),
    (0xD02CE0, 0x11003115, _add_imm(0x11003115, 0x2A), 'Time label Y -> row 2'),
    (0xD028DC, 0x11004116, _add_imm(0x11004116, 0x2E), 'hours Y -> row 2'),
    (0xD02A98, 0x11004116, _add_imm(0x11004116, 0x2E), 'minutes Y -> row 2'),
    (0xD029A4, 0x11004916, _add_imm(0x11004916, 0x30), 'colon Y -> row 2'),
    # and the clock spreads out to where the Hext file puts it
    (0xD02908, 0x11010916, _add_imm(0x11010916, CLOCK_HOURS_X), 'hours X'),
    (0xD029D0, 0x11015D16, _add_imm(0x11015D16, CLOCK_COLON_X), 'colon X'),
    (0xD02AC4, 0x11018516, _add_imm(0x11018516, CLOCK_MINUTES_X),
     'minutes X'),
    # the two draws the Hext file throws away move down to the date row and
    # become the day and the separator; their values are repointed below
    (0xD02C30, 0x11004115, _add_imm(0x11004115, ROW3_VALUE_Y), 'the day Y'),
    (0xD02C5C, 0x11020115, _add_imm(0x11020115, DAY_X), 'the day X'),
    (0xD02B3C, 0x11004916, _add_imm(0x11004916, ROW3_CHAR_Y), 'separator Y'),
    (0xD02B68, 0x1101D916, _add_imm(0x1101D916, SEPARATOR_X), 'separator X'),
)

# The Time label's X: `csel w9, w9, w23, ne` picks between the 6 the previous
# instruction loaded and whatever w23 holds. Gil's is an unconditional +6, so
# this is what left the two labels out of line. A `nop` keeps the 6.
TIME_LABEL_X_SITE = 0xD02D20
TIME_LABEL_X_ORIG = 0x1A971129


# ------------------------------------------------------ the clock source sites
# `ldr w22, [x24]` -- the recompiled `mov eax, <play timer field>` that lands
# the extracted hours (and minutes) in w22 just before they are pushed. The
# Hext file replaces the extraction with a byte load from the mod's clock;
# this replaces the one instruction that reads the result.
# `ldr w22, [x24]` for the clock, `ldr w21, [x24]` for the day: the
# recompiled `mov eax, <play timer field>` that lands each extracted value in
# a register just before it is pushed. The Hext file replaces the extraction;
# this replaces the one instruction that reads its result, which is smaller
# and cannot disturb the call around it.
CLOCK_SITES = ((0xD028C0, 0xB9400316, 22, G_HOURS, 'hours'),
               (0xD02A7C, 0xB9400316, 22, G_MINUTES, 'minutes'),
               # the seconds draw, repointed at the day: same helper, same two
               # digits, and it is being drawn on the date row instead
               (0xD02C14, 0xB9400315, 21, G_DAYS, 'the day'))

# The second colon's character push. Both colons share the w27 the first one
# set, so the character cannot be changed by editing an immediate -- the store
# itself becomes the call. x0 is the translated slot being written and x30 is
# already dead here, the instruction before it being the translate.
SEPARATOR_SITE = 0xD02B28
SEPARATOR_ORIG = 0xB900001B           # str w27, [x0]

# ------------------------------------------------------------ the date row
# The Gil label's draw, which the Hext file also hijacks: the cave performs
# the call itself and then draws the third line.
DATE_HOOK = 0xD02EC0
DATE_HOOK_ORIG = 0x940F8494           # bl 0x10E4110

FUNCTION = 0xCFFEA0                   # x86 0x6CA346
FUNCTION_END = 0xD04570

# ------------------------------------------------------- the guest CPU state
# x24 is the recompiled function's guest register block and w25 its data base;
# read straight out of the code at +CFFFF0 and used all through it.
STATE = 24                            # x24
ST_ESP = 0x10
ST_EBP = 0x14


def _push(a, value):
    """One guest push of w<value>, the idiom the recompiled code uses.

    `value` must be one of x19..x28: `GUEST_TRANSLATE` is a call and only
    those survive it. That is not a convention, it is what the surrounding
    code itself relies on at +D029D0 / +D029E8.
    """
    if not 19 <= value <= 28:
        raise ValueError('w%d does not survive GUEST_TRANSLATE' % value)
    a.emit(A.ldr(8, STATE, ST_ESP))
    a.emit(A.sub_imm(0, 8, 4))
    a.emit(A.str_(0, STATE, ST_ESP))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(A.str_(value, 0, 0))


def _call(a, target, args):
    """One guest call: the return slot, the call, and the caller's cleanup.

    The recompiled callee pops its own return slot, which is why the cleanup
    is `4 * args` and not `4 * args + 4`. Measured, not assumed: the hours
    draw pushes six and the code after it adds 0x14.
    """
    a.emit(A.ldr(8, STATE, ST_ESP))
    a.emit(A.sub_imm(8, 8, 4))
    a.emit(A.str_(8, STATE, ST_ESP))
    a.emit(A.bl(a.pc(), target))
    a.emit(A.ldr(8, STATE, ST_ESP))
    a.emit(A.add_imm(0, 8, 4 * args))
    a.emit(A.str_(0, STATE, ST_ESP))


# The cave's registers. All of x19..x28 are saved on entry and restored on
# exit, and x24 is left alone because it is the guest state everything reads.
R_X = 19                              # the box's left edge
R_Y = 20                              # row 3, the label baseline
R_COLOUR = 21
R_SCALE = 22
R_SCRATCH = 23                        # the X of whatever is being drawn
R_MONTH = 25                          # the month, then its row in the table
R_TABLE = 26
R_PTR = 27                            # the guest string being drawn


def _read_guest_byte(a, guest, into):
    AC.guest_ptr(a, guest)
    a.emit(A.ldrb(into, 0, 0))


def _emit_draw_one(a):
    """The shared subroutine: draw the guest string in w<R_PTR> at
    (w<R_SCRATCH>, w<R_Y>).

    Two strings through one copy of this rather than two, because the padding
    pool has 147 usable words in its widest window by the time this pass runs
    and a first version of this module wanted 355.

    It is reached by `bl` and makes calls of its own -- five translates and
    the draw -- so it keeps its own return address on the stack. Leaving it in
    x30 is what the first cut did, and the emulator caught it at once: the
    draw's `bl` overwrote x30, the `ret` went back into the middle of this,
    and the cave never terminated.
    """
    a.emit(A.stp64_pre(29, 30, 31, -16))
    _push(a, R_SCALE)
    _push(a, R_COLOUR)
    _push(a, R_PTR)
    _push(a, R_Y)
    _push(a, R_SCRATCH)
    _call(a, DRAW_STRING, 5)
    a.emit(A.ldp64_post(29, 30, 31, 16))
    a.emit(A.ret())


def _write_guest_bytes(a, guest, values):
    """Put literal bytes at a guest address, through the translator."""
    AC.guest_ptr(a, guest)
    for index, value in enumerate(values):
        a.emit(A.movz(9, value))
        a.emit(A.strb(9, 0, index))


def build_date_cave(cave, addr, table_at, layout):
    """The third line's text: `Date` and the month's three letters.

    The day and the separator are not here. They are the menu's own seconds
    and second colon, moved down to this row and repointed -- see CONSTANTS
    and CLOCK_SITES.
    """
    month_x, _separator_x, _day_x = layout
    a = Asm(cave, addr)
    AC.save_host(a)

    # The call this hook displaced. It has to happen first: it is the Gil
    # label, and everything after it depends on the guest stack being exactly
    # where that callee left it.
    a.emit(A.bl(a.pc(), DRAW_STRING))

    # The box's own X and Y, read out of the guest frame the same way every
    # draw in this function reads them.
    a.emit(A.ldr(8, STATE, ST_EBP))
    a.emit(A.sub_imm(0, 8, 4))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(A.ldr(R_X, 0, 0))
    a.emit(A.ldr(8, STATE, ST_EBP))
    a.emit(A.sub_imm(0, 8, 0x0C))
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))
    a.emit(A.ldr(8, 0, 0))
    a.emit(A.add_imm(R_Y, 8, ROW3_LABEL_Y))

    a.emit(A.movz(R_COLOUR, TEXT_COLOUR))
    AC.mov32(a, R_SCALE, TEXT_SCALE)

    # `Date`, written where the Hext file writes it. Rewriting it every time
    # the box is drawn costs ten instructions and means this pass owns no
    # state that could be stale, half-written or saved into anything.
    _write_guest_bytes(a, LABEL_AT,
                       [_char(c) for c in LABEL] + [TERMINATOR])

    # The month, clamped without a branch. A byte outside 0..11 cannot be
    # allowed to index a 36-byte table, and an uninitialised savemap is
    # exactly where one comes from: `ff7nx_daynight` reseeds such a save, but
    # the menu can be opened before it ever runs.
    _read_guest_byte(a, G_MONTHS, R_MONTH)
    a.emit(A.cmp_imm(R_MONTH, len(MONTHS)))
    a.emit(A.csel(R_MONTH, 31, R_MONTH, A.HS))
    a.emit(A.movz(R_SCRATCH, len(MONTHS[0])))
    a.emit(A.mul(R_MONTH, R_MONTH, R_SCRATCH))
    AC.bss_ptr(a, R_TABLE, table_at)
    a.emit(AC.add_x_uxtw(R_MONTH, R_TABLE, R_MONTH))

    # ... copied out of that table into the guest buffer the Hext file uses.
    AC.guest_ptr(a, MONTH_AT)
    for index in range(len(MONTHS[0])):
        a.emit(A.ldrb(9, R_MONTH, index))
        a.emit(A.strb(9, 0, index))
    a.emit(A.movz(9, TERMINATOR))
    a.emit(A.strb(9, 0, len(MONTHS[0])))

    # Both strings, through one subroutine, on the label baseline.
    # It is emitted here and jumped over rather than tacked on at the end, so
    # its address is known to the `bl`s that follow it. `addr` is the real,
    # scattered address of a word -- the whole point of building against the
    # layout instead of a pretend contiguous block.
    a.b('body')
    draw_one = len(a.words)
    _emit_draw_one(a)
    a.label('body')
    for guest, dx in ((LABEL_AT, LABEL_X), (MONTH_AT, month_x)):
        AC.mov32(a, R_PTR, guest)
        a.emit(A.add_imm(R_SCRATCH, R_X, dx))
        a.emit(A.bl(a.pc(), a.addr(draw_one)))

    AC.restore_host(a)
    a.emit(A.ret())
    return a.resolve()


def build_separator_cave(cave, addr):
    """Push `/` where the second colon pushes `:`.

    Both colons draw the w27 the first one loaded, so this is the one place
    the character can be changed. x0 is the translated stack slot being
    written and must come back untouched; x9 is the translator's own scratch
    and x30 is already dead, the instruction before this being that call.
    """
    a = Asm(cave, addr)
    a.emit(A.movz(9, SEPARATOR))
    a.emit(A.str_(9, 0, 0))
    a.emit(A.ret())
    return a.resolve()


def build_clock_cave(cave, addr, guest, reg):
    """Put one byte of the savemap clock where the play timer's value was.

    x0 holds the guest stack slot the caller is about to translate, so it is
    saved and given back: this replaces a load, and a load clobbers nothing.
    """
    a = Asm(cave, addr)
    a.emit(A.stp64_pre(0, 30, 31, -16))
    AC.guest_ptr(a, guest)
    a.emit(A.ldrb(reg, 0, 0))
    a.emit(A.ldp64_post(0, 30, 31, 16))
    a.emit(A.ret())
    return a.resolve()


# --------------------------------------------------------------- installation
def check_sites(text):
    """Every word this pass will overwrite, before it overwrites any of them."""
    for site, want, _new, what in CONSTANTS:
        AC.expect_word(text, site, want, 'calendar: %s' % what)
    for site, want, _reg, _guest, what in CLOCK_SITES:
        AC.expect_word(text, site, want, 'calendar: the %s source' % what)
    AC.expect_word(text, SEPARATOR_SITE, SEPARATOR_ORIG,
                   'calendar: the second colon\'s character')
    AC.expect_word(text, TIME_LABEL_X_SITE, TIME_LABEL_X_ORIG,
                   'calendar: the Time label\'s conditional X')
    AC.expect_word(text, DATE_HOOK, DATE_HOOK_ORIG,
                   'calendar: the Gil label draw')


# The guest image the port maps, which is where the two strings go. The x86
# section table, so a guest address can be turned into an offset in it.
GUEST_IMAGE = os.path.join('romfs', 'ff7', 'resources', 'ff7_1.02', 'ff7_en')
GUEST_SECTIONS = ((0x401000, 0x3B5000, 0x000200),      # .text
                  (0x7B6000, 0x004000, 0x3B4A00),      # .rdata
                  (0x7BA000, 0x797000, 0x3B8800))      # .data


def guest_offset(address):
    for base, size, where in GUEST_SECTIONS:
        if base <= address < base + size:
            return where + (address - base)
    return None


def check_guest(path):
    """Refuse to write a string anywhere that is not still empty.

    `0x915000` and `0x913FB1` are the Hext file's own parking spots, inside a
    15,576-byte run of zeros in this image. That the mod itself chose them is
    good evidence and not a guarantee, and writing a string over live data
    would be a bug with no symptom until whatever owned it ran.
    """
    with open(path, 'rb') as handle:
        image = handle.read()
    for guest, length, what in ((LABEL_AT, len(LABEL) + 1, 'the Date label'),
                                (MONTH_AT, len(MONTHS[0]) + 1, 'the month')):
        where = guest_offset(guest)
        if where is None or where + length > len(image):
            raise ValueError('calendar: guest %#x (%s) is not in %s'
                             % (guest, what, os.path.basename(path)))
        if any(image[where:where + length]):
            raise ValueError('calendar: guest %#x (%s) is not free in %s -- '
                             'it holds %s'
                             % (guest, what, os.path.basename(path),
                                image[where:where + length].hex()))


def enabled():
    return (os.environ.get(ENV, '') or '1').strip() not in ('0', 'off', 'no')


def apply_to_nso(src, dest, space=None, log=lambda *_: None, guest=None):
    """Install the menu calendar, patching `src` into `dest`."""
    if guest:
        check_guest(guest)
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw, own = ff7nx_tables.open_module(blob)
    space = own if space is None else space
    text = space.text
    check_sites(text)
    layout = layout_from_env()

    table = month_table()
    table_at = space.place('calendar-months', table, align=4)

    import nxmap
    pool = ff7nx_cave.HolePool(text, starts=set(nxmap.Main(src).arm_starts))

    placed = {}
    entry, words = ff7nx_cave.emit_laid_out(
        pool, lambda cave, at: build_date_cave(cave, at, table_at, layout))
    placed.update(words)
    # what the window had to hold, which is the number that matters -- the
    # map also carries the `b` at the end of each run it was chained through
    date_words = len(build_date_cave([], lambda i: 4 * i, table_at, layout))
    chained = len(words)

    clock_entries = {}
    for site, _want, reg, guest, what in CLOCK_SITES:
        where, words = ff7nx_cave.emit_laid_out(
            pool, lambda cave, at, _g=guest, _r=reg:
            build_clock_cave(cave, at, _g, _r))
        placed.update(words)
        clock_entries[what] = where
        placed[site] = A.bl(site, where)

    where, words = ff7nx_cave.emit_laid_out(pool, build_separator_cave)
    placed.update(words)
    clock_entries['separator'] = where
    placed[SEPARATOR_SITE] = A.bl(SEPARATOR_SITE, where)

    placed[DATE_HOOK] = A.bl(DATE_HOOK, entry)
    placed[TIME_LABEL_X_SITE] = A.nop()
    for site, _want, new, _what in CONSTANTS:
        placed[site] = new

    for where, word in placed.items():
        struct.pack_into('<I', text, where, word)

    out = AC.pack(blob, space.commit(), 0)
    _segs, check_raw = AC.segments(out)
    got = struct.unpack_from('<I', check_raw[0], DATE_HOOK)[0]
    if got != A.bl(DATE_HOOK, entry):
        raise ValueError('calendar: the date hook did not survive the repack')

    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, 'wb') as handle:
        handle.write(out)

    log('  the menu Time box grows a third line: Gil, then Time, then Date')
    log('  the Time label\'s X is conditional in this build (+D02D20 picks '
        'between 6 and a runtime value); it is pinned to the 6 Gil uses, '
        'which is what the Hext file does on PC by making it a constant')
    log('  clock: the Time field reads the mod\'s own hours and minutes '
        '(bank 1 +0x0F / +0x0C), not the play timer -- this is what Echo-S\'s '
        'Hext calendar does on PC')
    log('  date: `Date`, the month\'s three letters, the separator and the '
        'day. The text goes through the STRING helper, not the character '
        'one: they do not share a character set, and the character one '
        'renders these codes as Thai. The two strings are written into the '
        'guest image at +0x%X and +0x%X, the Hext file\'s own spots, and the '
        'build checks both are still empty first' % (LABEL_AT, MONTH_AT))
    log('  the day and the separator are the menu\'s own seconds and second '
        'colon, moved to the date row and repointed -- reusing two draws this '
        'function already makes is what fits the date row into %d cave words '
        'instead of 355' % date_words)
    log('  columns: Date at +%#x, the month at +%#x, the separator at +%#x '
        'and the day at +%#x -- which is the column the minutes are in, so '
        'the two rows end on the same edge rather than nearly the same one'
        % (LABEL_X, layout[0], layout[1], layout[2]))
    log('  %d-byte month table at +0x%X; the date row is %d instruction(s) '
        '(%d pool word(s) once chained), and %d constant(s) move'
        % (len(table), table_at, date_words, chained, len(CONSTANTS)))
    return {'entry': entry, 'table': table_at, 'table_bytes': len(table),
            'cave_words': date_words, 'clock': clock_entries,
            'constants': len(CONSTANTS), 'layout': layout}
