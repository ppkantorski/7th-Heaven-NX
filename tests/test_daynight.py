#!/usr/bin/env python3
"""
tests/test_daynight.py -- execute the day/night caves.

Built at their real addresses out of the real padding-hole allocator, then
interpreted word for word by `arm64emu` with `paged=True`, so the address
translator behaves the way the module's does.

The check that matters most is `test_the_cave_and_python_agree_on_every_
minute`. `ff7nx_daynight.phase_colour` is a second implementation of FFNx's
float colour chain, in integers, and the cave is a third in ARM64. A
reimplementation of somebody else's maths is only honest if the two are made
to agree on the whole domain, so they are: all 1,440 minutes of the day.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import os
import struct

import pytest
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import capstone                                               # noqa: E402

import a64 as A                                               # noqa: E402
import arm64emu                                               # noqa: E402
import ff7nx_cave                                             # noqa: E402
import ff7nx_daynight as D                                    # noqa: E402
import ff7nx_tables                                           # noqa: E402
import nxmap                                                  # noqa: E402

MODULE = os.path.join(ROOT, 'dump', 'exefs', 'main')
HOST_SP = 0x50000000
BLOCK = HOST_SP + D.BLOCK_BASE           # where `BlockVertex` is assembled

# BUILD 475. What the CAVE stores is not FFNx's `TimeColor`: FFNx multiplies
# that in linear light and re-encodes, this port's shaders multiply in gamma,
# so the cave stores the gamma-space equivalent. `phase_colour` stays FFNx's
# number and every comparison against cave output goes through this.
TINT = D.as_the_shader_multiplies
TINT_PACK = lambda c: (lambda t: t[0] << 16 | t[1] << 8 | t[2])(TINT(c))  # noqa: E731
ULTRA_TEST_IDS = (343,)                 # farm, outdoor in the test bitmap


def _mini_bg(w):
    """BUILD 661: the minigames' background slot (complement), as packed."""
    return ~w.mem.u(w.bss + D.BSS_EXT + D.EXT_MINI_BG_INV, 4) & 0xFFFFFF

# every cave resumes at its own hook + 4
SITE_RESUME = {n: h + 4 for n, h, _o, _w in D.SITES}

# BUILD 480. The real rows come from `sky_plan` against the shipped flevel;
# these are the ones it produces there, pinned so the caves are exercised
# against a table with more than one row in it.
SKY_ROWS = ((68, 4, 14), (69, 4, 14), (70, 4, 14), (71, 4, 14), (72, 4, 14))

_LAYOUT = {}


def layout(force_on=False, freeze_hour=None):
    key = (force_on, freeze_hour)
    if key not in _LAYOUT:
        blob = open(MODULE, 'rb').read()
        segs, _raw, own = ff7nx_tables.open_module(blob)
        text = own.text
        for _n, site, orig, what in D.SITES:
            got = struct.unpack_from('<I', text, site)[0]
            assert got == orig, ('%s: module has %08X, expected %08X'
                                 % (what, got, orig))
        bss = D.AC.scratch_base(blob, segs)
        bitmap = bytearray(D.BITMAP_BYTES)
        for field_id in (151, 343, 1):                # a few marked outdoor
            bitmap[field_id >> 3] |= 1 << (field_id & 7)
        pool = ff7nx_cave.HolePool(text,
                                   starts=set(nxmap.Main(MODULE).arm_starts))
        if os.environ.get('DAYNIGHT_TEST_DEADSPACE'):
            # BUILD 614b: the whole suite again, every cave from the
            # 'daynight' dead-space part -- the layout log 381 needed
            import ff7nx_deadspace as DS
            pool = DS.Bump(*DS.part(MODULE, 'daynight'))
        entries, placed, outdoor_at, phase_at, gamma_at, sky_at = D.build_all(
            pool, own, bss, bytes(bitmap), 40, force_on=force_on,
            freeze_hour=freeze_hour, sky_rows=SKY_ROWS, snow=True,
            ultra_ids=ULTRA_TEST_IDS, ultra=True)
        hooks = {s for _n, s, _o, _w in D.all_sites(True, True)}
        code = {va: w for va, w in placed.items() if va not in hooks}
        # The three world caves re-emit a real `bl` to the recompiled
        # `engine_draw_graphics_object`. We are testing the cave, not the
        # callee, so it is stubbed with a bare `ret` -- which also means the
        # call has to LAND there for these to pass.
        for stub in (D.WORLD_DRAW_OBJ, D.BATTLE_UI, D.BATTLE_MENU_CALLEE,
                     D.CONDOR_ON_CALLEE, D.CONDOR_OFF_CALLEE,
                     D.SNOW_ON_CALLEE, D.SNOW_OFF_CALLEE,
                     D.SNOW_CLEAR_CALLEE, D.CONDOR_UNITS_CALLEE,
                     D.SNOW_MODELS_CALLEE, D.SNOW_ENTRY_CALLEE,
                     D.SNOW_COURSE_CALLEE):
            code[stub] = 0xD65F03C0
        _LAYOUT[key] = (entries, placed, code, bss, outdoor_at,
                        phase_at, bytes(bitmap), own, gamma_at, sky_at)
    return _LAYOUT[key]


class World:
    def __init__(self, options=0x07, minutes=0, hours=12, days=1, months=0,
                 field_id=151, frames=0, force_on=False, freeze_hour=None,
                 mode=D.DRIVER_FIELD):
        (self.entries, self.placed, self.code, self.bss,
         self.outdoor_at, self.phase_at, self.bitmap, space,
         self.gamma_at, self.sky_at) = layout(force_on, freeze_hour)
        self.mem = arm64emu.Mem()
        self.cpu = arm64emu.Cpu(self.mem, paged=True)
        self.cpu.sp = HOST_SP
        # the two tables have to be where the caves will look for them
        self.mem.write(self.outdoor_at, self.bitmap)
        self.mem.write(self.phase_at, D.phase_tables())
        self.mem.write(self.gamma_at, D.GAMMA_LUT)
        self.mem.write(self.sky_at, D.sky_table(SKY_ROWS))
        self.gset8(D.G_OPTIONS, options)
        self.gset8(D.G_MINUTES, minutes)
        self.gset8(D.G_MONTHS, months)
        self.gset8(D.G_DAYS, days)
        self.gset8(D.G_HOURS, hours)
        self.mem.setu(self.bss + D.BSS_FRAMES, frames, 4)
        self.mem.setu(self.bss + D.BSS_TINT_INV, 0, 4)
        self.mem.setu(self.bss + D.BSS_MODE, mode, 4)
        self.mem.setu(self.bss + D.BSS_TICKED, 0, 4)
        self.mem.setu(self.host(D.G_FIELD_ID), field_id, 2)

    def host(self, va):
        return self.cpu.guest_to_host(va)

    def gset8(self, va, val):
        self.mem.setu(self.host(va), val & 0xFF, 1)

    def gget8(self, va):
        return self.mem.u(self.host(va), 1)

    def tint(self):
        # The slot holds the COMPLEMENT, so that a BSS nobody has written --
        # the title screen, before any field runs the tick -- reads as white
        # rather than as black. Build 469 shipped it the other way round and
        # the main menu went black.
        return ~self.mem.u(self.bss + D.BSS_TINT_INV, 4) & 0xFFFFFF

    def rgb(self):
        t = self.tint()
        return ((t >> 16) & 0xFF, (t >> 8) & 0xFF, t & 0xFF)

    def frames(self):
        return self.mem.u(self.bss + D.BSS_FRAMES, 4)

    def armed(self):
        return ~self.mem.u(self.bss + D.BSS_ARMED_INV, 4) & 0xFFFFFF

    def sky_tint(self):
        return ~self.mem.u(self.bss + D.BSS_SKY_INV, 4) & 0xFFFFFF

    def sky_rgb(self):
        t = self.sky_tint()
        return ((t >> 16) & 0xFF, (t >> 8) & 0xFF, t & 0xFF)

    def ticked(self):
        return self.mem.u(self.bss + D.BSS_TICKED, 4)

    def minute_of_day(self):
        hours, minutes, _d, _m = self.clock()
        return hours * 60 + minutes

    def clock(self):
        return (self.gget8(D.G_HOURS), self.gget8(D.G_MINUTES),
                self.gget8(D.G_DAYS), self.gget8(D.G_MONTHS))

    def run(self, which='tick', max_steps=200000):
        return self.cpu.run(self.entries[which], self.code,
                            max_steps=max_steps)

    def fill_block(self, filler=0x11111111):
        """
        The 80 bytes +0x10D9D70 has on its stack when it reaches our word.

        `blendMode.x` and the whole projection matrix are already written; the
        three lanes after `.x` are whatever the stack happened to hold, which
        is precisely why they are free to borrow.
        """
        for off in range(0, D.BLOCK_SIZE, 4):
            self.mem.setu(BLOCK + off, filler + off, 4)

    def block_word(self, off):
        return self.mem.u(BLOCK + off, 4)

    def submit(self):
        """
        Run the transport the way the draw helper reaches it.

        `sp` goes back to HOST_SP first: the tick cave ends on the displaced
        `stp x26, x25, [sp, #-0x50]!` it stole from `field_draw_everything`, so
        a World that has ticked is standing in that function's frame, and
        +0x10D9D70 has its own.
        """
        self.cpu.sp = HOST_SP
        return self.run('block')


class _Probe:
    """Run a cave with the draw it wraps replaced by a recorder.

    The sky selector is cleared again the moment the page draw returns, so
    reading it afterwards always says 0. This puts a stub at the callee that
    copies the flag somewhere durable first -- which also means the call has
    to actually LAND on the callee for the test to say anything.
    """

    SCRATCH = 0x100

    def __init__(self, slot):
        self.slot = slot

    def run(self, world, page, cave='sky_page'):
        import a64 as _A

        class _Emit:
            def __init__(s, at):
                s.words, s.at = [], at

            def emit(s, word):
                s.words.append(word)

            def pc(s):
                return s.at + 4 * len(s.words)

        stub = _Emit(D.WORLD_DRAW_OBJ)
        D.AC.bss_ptr(stub, 9, world.bss)
        stub.emit(_A.ldr(10, 9, self.slot))
        stub.emit(_A.str_(10, 9, self.SCRATCH))
        stub.emit(_A.ret())
        code = dict(world.code)
        code.update({D.WORLD_DRAW_OBJ + 4 * i: w
                     for i, w in enumerate(stub.words)})
        world.cpu.x[19] = world.bss + 0x400
        world.mem.setu(world.cpu.x[19], page, 4)
        world.mem.setu(world.bss + self.SCRATCH, 0xDEAD, 4)
        assert world.cpu.run(world.entries[cave], code,
                             max_steps=8000) == D.SKY_PAGE_RESUME
        return world.mem.u(world.bss + self.SCRATCH, 4)


# ----------------------------------------------------------------- encoders
def test_every_emitted_word_decodes():
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    for force_on in (False, True):
        _e, placed, _c, _b, _o, _p, _bm, _s, _g, _k = layout(force_on)
        bad = [(va, w) for va, w in placed.items()
               if not list(md.disasm(struct.pack('<I', w), va))]
        assert not bad, ['%08X:%08X' % b for b in bad[:8]]


def test_the_new_encoders_round_trip():
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)

    def one(word):
        ins = list(md.disasm(struct.pack('<I', word), 0x1000))
        assert ins, 'word %08X does not decode' % word
        return '%s %s' % (ins[0].mnemonic, ins[0].op_str)

    assert one(A.mvn_reg(10, 10)) == 'mvn w10, w10'
    assert one(D.sdiv(13, 13, 11)) == 'sdiv w13, w13, w11'
    assert one(D.bic_reg(21, 21, 12)) == 'bic w21, w21, w12'
    assert one(D.lsrv(11, 11, 12)) == 'lsr w11, w11, w12'


# ------------------------------------------------------------ the colour
def test_the_cave_and_python_agree_on_every_minute():
    """
    Two reimplementations of FFNx's float chain -- one in Python, one in
    ARM64 -- checked against each other over the whole day. An integer
    rewrite of somebody else's floating point is only trustworthy if it is
    pinned like this.
    """
    for minute in range(D.DAY_MINUTES):
        w = World(hours=minute // 60, minutes=minute % 60, options=0x05)
        assert w.run() == D.TICK_RESUME
        want = TINT(D.phase_colour(minute))
        assert w.rgb() == want, (
            'minute %d: cave %s, python %s' % (minute, w.rgb(), want))


def _srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c):
    return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def test_the_gamma_table_reproduces_ffnx_s_linear_multiply():
    """
    The bug in build 474, pinned numerically.

    FFNx linearises, multiplies by `TimeColor`, and re-encodes:

        FFNx.frag:91   color.rgb = toLinear(color.rgb)
        FFNx.frag:162  if (isTimeFilterEnabled) color.rgb *= TimeColor.rgb
        FFNx.frag:167  color.rgb = toGamma(color.rgb)

    This port multiplies in gamma space and nothing else, so 474 was doing
    `in * 0.15` where FFNx does `toGamma(toLinear(in) * 0.15)` -- a night
    roughly a third as bright in red and green as the PC build's. The table
    turns the one into the other, and this walks both chains over every tint
    the cycle can produce.
    """
    tints = set()
    for minute in range(D.DAY_MINUTES):
        tints.update(D.phase_colour(minute))
    assert len(tints) > 200, 'the cycle should sweep most of the byte range'

    exact = D.gamma_lut(D.FULL_STRENGTH)       # the dial at 100 IS FFNx
    bands = {(192, 256): 2.0, (64, 192): 6.0, (16, 64): 7.0}
    for (lo, hi), allowed in bands.items():
        worst = 0.0
        for t in sorted(tints):
            ours = exact[t] / 255.0
            ffnx_tint = t / 255.0
            for value in range(lo, hi):
                v = value / 255.0
                ffnx = _linear_to_srgb(_srgb_to_linear(v) * ffnx_tint)
                worst = max(worst, abs(ffnx - v * ours) * 255.0)
        assert worst <= allowed, (
            'input %d..%d: %.2f/255 from FFNx, over %.1f'
            % (lo, hi - 1, worst, allowed))


def test_the_table_cannot_change_a_scene_it_should_not():
    """White in, white out -- midday, indoors and every untinted draw in the
    game all depend on the table being exactly identity at the top."""
    assert D.GAMMA_LUT[255] == 255
    assert D.GAMMA_LUT[0] == 255 - round(255 * D.DEFAULT_STRENGTH / 100)
    assert D.as_the_shader_multiplies(D.MIDDAY_RGB) == (255, 255, 255)
    assert all(D.GAMMA_LUT[i] <= D.GAMMA_LUT[i + 1] for i in range(255))
    # at the dial's 100 setting, night is the number the PC build shows
    night = D.as_the_shader_multiplies(D.NIGHT_RGB, D.FULL_STRENGTH)
    assert night[2] == 255, night
    assert 100 <= night[0] <= 115, night
    # and the shipped default is lighter than that, never darker
    shipped = D.as_the_shader_multiplies(D.NIGHT_RGB)
    assert shipped[2] == 255 and shipped[0] > night[0], (shipped, night)


def test_the_cave_applies_the_table_after_the_lerp_not_before():
    """
    FFNx interpolates `TimeColor` and the shader curves the result, so the
    curve belongs after the interpolation. Baking it into the phase table
    instead would interpolate between curved endpoints, which is a different
    and darker answer through the middle of every transition -- 6 points at
    the midpoint of sunrise.
    """
    minute = (D.SUNRISE_MIN + D.MORNING_MIN) // 2
    curve_after = TINT(D.phase_colour(minute))
    a, b = TINT(D.NIGHT_RGB), TINT(D.MORNING_RGB)
    curve_before = tuple(x + (y - x) // 2 for x, y in zip(a, b))
    assert curve_after != curve_before, 'the two orders cannot be compared'
    w = World(options=0x05, hours=minute // 60, minutes=minute % 60)
    assert w.run() == D.TICK_RESUME
    assert w.rgb() == curve_after, (w.rgb(), curve_after, curve_before)


def test_the_phase_boundaries_are_the_mods_own():
    assert D.phase_colour(0) == D.NIGHT_RGB
    assert D.phase_colour(D.SUNRISE_MIN) == D.NIGHT_RGB
    assert D.phase_colour(D.MORNING_MIN) == D.MORNING_RGB
    assert D.phase_colour(D.MIDDAY_MIN) == D.MIDDAY_RGB
    assert D.phase_colour(D.AFTERNOON_MIN) == D.AFTERNOON_RGB
    assert D.phase_colour(D.NIGHT_MIN) == D.NIGHT_RGB
    assert D.phase_colour(D.DAY_MINUTES - 1) == D.NIGHT_RGB
    # and midday really is identity, so a daytime field is untouched
    assert D.MIDDAY_RGB == (255, 255, 255)


# ------------------------------------------------------------ the gating
def test_bit_zero_off_means_no_tint_at_all():
    w = World(options=0x00, hours=2)          # 02:00, deep night
    assert w.run() == D.TICK_RESUME
    assert w.tint() == D.TINT_WHITE
    assert w.clock() == (2, 0, 1, 0), 'the clock advanced with the cycle off'
    # The Highwind exception is owned by the same option bit.  A build made
    # with the option on must still go fully inert if its savemap bit is off.
    sky = World(options=0x00, hours=2, field_id=SKY_ROWS[0][0])
    assert sky.run() == D.TICK_RESUME
    assert sky.tint() == sky.sky_tint() == D.TINT_WHITE


def test_an_indoor_field_is_not_tinted():
    night = World(options=0x05, hours=2, field_id=151)
    night.run()
    assert night.rgb() == TINT(D.NIGHT_RGB)
    indoor = World(options=0x05, hours=2, field_id=500)   # no bit in the map
    assert indoor.run() == D.TICK_RESUME
    assert indoor.tint() == D.TINT_WHITE


def test_the_outdoor_bit_is_written_back_from_the_table():
    """The mod's menu and the Hext calendar read this byte, so the runtime
    has to leave it saying what it decided, not just act on it."""
    w = World(options=0x03, field_id=151)                  # bit 2 clear
    w.run()
    assert w.gget8(D.G_OPTIONS) & D.OPT_OUTDOOR
    w = World(options=0x07, field_id=500)                  # bit 2 set
    w.run()
    assert not w.gget8(D.G_OPTIONS) & D.OPT_OUTDOOR


def test_a_field_id_past_the_table_is_treated_as_indoor():
    w = World(options=0x05, hours=2, field_id=D.MAX_FIELD_ID + 5)
    assert w.run() == D.TICK_RESUME
    assert w.tint() == D.TINT_WHITE


def test_highwind_sky_keeps_its_own_clock_colour_while_the_room_stays_white():
    """
    The Highwind rooms are indoor and stay indoor -- the ordinary filter is
    white on every draw in them. The window sky gets its own slot from the
    same clock, and the page range comes out of the table `sky_plan` built.
    """
    for field, first, last in SKY_ROWS:
        w = World(options=0x05, hours=2, field_id=field)
        assert w.run() == D.TICK_RESUME
        assert w.tint() == D.TINT_WHITE, field
        assert w.sky_rgb() == TINT(D.SKY_NIGHT_RGB), field
        assert w.mem.u(w.bss + D.BSS_SKY_FIRST, 4) == first
        assert w.mem.u(w.bss + D.BSS_SKY_LAST, 4) == last
    # a field that is not in the table gets no range at all
    w = World(options=0x05, hours=2, field_id=151)
    w.run()
    assert w.mem.u(w.bss + D.BSS_SKY_LAST, 4) == 0


def test_the_sky_bracket_arms_for_its_pages_and_nothing_else():
    """
    The test that decides whether the ship gets tinted. `sub_640213` draws one
    page per call; the bracket must arm for layer 3's pages and for nothing
    else -- not the hull, not the deck, not a field that is not in the table.

    Run with a stub in place of the draw that records what the draw ACTUALLY
    saw, because the flag is cleared again the instant the call returns.
    """
    import a64 as _A
    field, first, last = SKY_ROWS[0]
    probe = _Probe(D.BSS_SKY_ACTIVE)
    for field_id, expect in ((field, True), (151, False)):
        w = World(options=0x05, hours=2, field_id=field_id)
        w.run()
        for page in (first - 1, first, (first + last) // 2, last, last + 1):
            saw = probe.run(w, page)
            want = expect and first <= page <= last
            assert saw == (1 if want else 0), (field_id, page, saw)
            assert w.mem.u(w.bss + D.BSS_SKY_ACTIVE, 4) == 0, (
                'the selector outlived the draw')


def test_only_the_highwind_page_bracket_can_select_the_sky_colour():
    """The block transport uses sky colour only while that flag is live."""
    w = World(options=0x05, hours=2, field_id=SKY_ROWS[1][0])
    w.run()
    w.fill_block()
    w.mem.setu(w.bss + D.BSS_SKY_ACTIVE, 1, 4)
    w.submit()
    assert w.block_word(D.TINT_LANE) & 0xFFFFFF == w.sky_tint()
    w.mem.setu(w.bss + D.BSS_SKY_ACTIVE, 0, 4)
    w.fill_block()
    w.submit()
    assert w.block_word(D.TINT_LANE) & 0xFFFFFF == D.TINT_WHITE


# -------------------------------------------------------------- the clock
def test_bit_one_off_freezes_the_clock_but_keeps_the_tint():
    w = World(options=0x05, hours=2, minutes=30)           # advance bit clear
    for _ in range(200):
        w.run()
    assert w.clock() == (2, 30, 1, 0)
    assert w.rgb() == TINT(D.NIGHT_RGB), ('the filter should still apply '
                                          'while paused')


def test_a_minute_takes_exactly_frames_per_minute_frames():
    w = World(options=0x07, hours=0, minutes=0)
    for _ in range(39):
        w.run()
    assert w.clock() == (0, 0, 1, 0), 'rolled early at %d frames' % w.frames()
    w.run()
    assert w.clock() == (0, 1, 1, 0), 'did not roll on the 40th frame'
    assert w.frames() == 0


def test_the_whole_cascade_rolls():
    w = World(options=0x07, hours=23, minutes=59, days=31, months=11)
    for _ in range(40):
        w.run()
    assert w.clock() == (0, 0, 1, 0), w.clock()


def test_days_are_one_based_after_a_month_rolls():
    w = World(options=0x07, hours=23, minutes=59, days=31, months=3)
    for _ in range(40):
        w.run()
    hours, minutes, days, months = w.clock()
    assert (days, months) == (1, 4), (days, months)


# ------------------------------------------------------------ the transport
#
# Four builds were spent trying to reach `blendMode.y/.z` at the GL layer, by
# hooking the shared `glBufferData(GL_UNIFORM_BUFFER, ...)` helper and then
# guessing which upload was the scene's: by size (469/470, which wrote into
# hq4x/2xSaI parameters and blacked the screen out), by a one-shot flag set
# near shader-program setup (472, which tagged an unrelated allocation and
# corrupted textures), and by the GL buffer name captured at the BlockVertex
# bind (473, which did nothing, because that bind asks for `handles[0]` while
# the uploader uses `handles[obj->0x34]`).
#
# The tests below pin the replacement to the only thing that needs to be true:
# the block is assembled on the caller's stack inside ONE function, every path
# converges on the word we hook, and the cave writes two fixed lanes of it.
def test_the_block_is_assembled_on_the_stack_of_one_function():
    """
    `sp+0x18 .. sp+0x68` in +0x10D9D70 IS `BlockVertex`, and the module says
    so three times: `blendMode.x` is stored at `sp+0x18`, the matrix lands at
    `sp+0x28` via `add x2, x8, #0x10` off that same base, and the size handed
    to the uploader is the literal 0x50 = 0x68 - 0x18.
    """
    blob = open(MODULE, 'rb').read()
    _segs, _raw, own = ff7nx_tables.open_module(blob)
    text = own.text
    # blendMode.x, four instructions before the `b.ne` that splits the paths
    assert struct.unpack_from('<I', text, 0x10D9FF4)[0] == A.str_(8, 31, 0x18)
    # and the block base is taken into x8 to reach the matrix at +0x10
    assert struct.unpack_from('<I', text, 0x10DA0A8)[0] == A.add_imm64(8, 31,
                                                                      0x18)
    assert struct.unpack_from('<I', text, 0x10DA0AC)[0] == A.add_imm64(2, 8,
                                                                      0x10)
    # the size, as a literal, one instruction stream later
    D.check_block_shape(text)
    # the two lanes we borrow are inside that window and are not blendMode.x
    assert 0 < D.TINT_LANE < D.MAGIC_LANE < 16 < D.BLOCK_SIZE


def test_every_path_to_the_upload_passes_the_hook():
    """
    The reason no draw can slip past untinted, and no draw the cave has no
    business touching can slip in: +0x10D9D70 funnels to one word.
    """
    blob = open(MODULE, 'rb').read()
    _segs, _raw, own = ff7nx_tables.open_module(blob)
    text = own.text
    join = D.BLOCK_JOINS[0][1]
    assert join < D.BLOCK_HOOK < 0x10DA274, hex(D.BLOCK_HOOK)
    for site, target in D.BLOCK_JOINS:
        assert struct.unpack_from('<I', text, site)[0] == A.b(site, target)
    # nothing between the convergence point and our word branches away
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    for va in range(join, D.BLOCK_HOOK, 4):
        word = struct.unpack_from('<I', text, va)[0]
        ins = next(iter(md.disasm(struct.pack('<I', word), va)))
        assert not ins.mnemonic.startswith(('b', 'cb', 'tb', 'ret')), ins


def test_the_block_cave_writes_the_tint_and_the_magic():
    w = World(options=0x05, hours=2)                 # outdoor, night
    w.run()
    tint = w.tint()
    assert tint != D.TINT_WHITE, 'there has to be a tint to carry'
    w.fill_block()
    assert w.submit() == D.BLOCK_RESUME
    assert w.block_word(D.TINT_LANE) == tint
    assert w.block_word(D.MAGIC_LANE) == D.TINT_MAGIC


def test_the_block_cave_leaves_the_rest_of_the_block_alone():
    """
    `blendMode.x` decides alpha in both scene shaders and the matrix is the
    projection. Touching either is a corrupted frame, which is what 469-472
    each looked like from the sofa.
    """
    w = World(options=0x05, hours=2)
    w.run()
    w.fill_block()
    before = {off: w.block_word(off) for off in range(0, D.BLOCK_SIZE, 4)}
    assert w.submit() == D.BLOCK_RESUME
    for off, was in before.items():
        if off in (D.TINT_LANE, D.MAGIC_LANE):
            continue
        assert w.block_word(off) == was, 'block+0x%X was clobbered' % off


def test_the_block_cave_still_hands_the_block_over():
    """The displaced word is the argument setup; losing it loses every draw."""
    w = World(options=0x05, hours=2)
    w.run()
    w.fill_block()
    assert w.submit() == D.BLOCK_RESUME
    assert w.cpu.x[1] == HOST_SP + D.BLOCK_BASE, hex(w.cpu.x[1])


def test_the_block_cave_touches_nothing_but_our_bss_and_two_lanes():
    """
    Walked word by word out of the real allocator's layout. Every memory
    access must be our own BSS through x3, or one of the two lanes through sp.
    This is the test that fails if anyone ever reaches for a caller's pointer
    in here again -- build 471 crashed the main menu doing exactly that.
    """
    _e, placed, _code, _bss, _o, _p, _b, _s, _g, _k = layout()
    blob = open(MODULE, 'rb').read()
    _segs, _raw, own = ff7nx_tables.open_module(blob)
    text = own.text
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    pc, seen, touched, saw_orig = D.BLOCK_HOOK, set(), [], False
    while True:
        word = placed.get(pc, struct.unpack_from('<I', text, pc)[0])
        ins = next(iter(md.disasm(struct.pack('<I', word), pc)))
        if ins.mnemonic == 'b':
            nxt = int(ins.op_str.lstrip('#'), 16)
            if nxt == D.BLOCK_RESUME:
                break
            assert nxt not in seen, 'the block cave loops'
            seen.add(nxt)
            pc = nxt
            continue
        if ins.mnemonic == 'cbz' and ins.op_str.startswith('x22'):
            pc += 4                  # 617c: walk the HUD test's reads too
            continue
        if ins.mnemonic == 'cbz':
            # Follow the normal path.  The alternate path only reloads our
            # BSS sky slot, which the behavioural sky test executes.
            pc = int(ins.op_str.rsplit('#', 1)[1], 16)
            continue
        if ins.mnemonic in ('cbnz', 'b.ne'):
            # BUILD 617b: the snowboard's 2D gate. Walk its fall-through
            # (every test in it), which reads only our BSS and the matrix.
            pc += 4
            continue
        assert not ins.mnemonic.startswith(('cb', 'tb', 'bl', 'ret')), ins
        if word == D.BLOCK_ORIG:
            saw_orig = True
        elif ins.mnemonic in ('ldr', 'str', 'ldrb', 'strb', 'ldrh', 'strh'):
            touched.append(ins.op_str)
        pc += 4
    assert saw_orig, 'the displaced `add x1, sp, #0x18` never runs'
    assert len(touched) == 11, touched
    assert '[x3]' in touched[0], touched
    assert '[x3, #0x%x]' % D.BSS_SKY_ACTIVE in touched[1], touched
    assert touched[2].endswith('[x3, #%d]' % D.BSS_MODE), touched
    assert '[x3, #0x%x]' % D.BSS_SNOW_SKY in touched[3], touched
    assert touched[4].endswith('[sp, #0x%x]' % (D.BLOCK_BASE + D.MAT_34))
    assert touched[5].endswith('[sp, #0x%x]' % (D.BLOCK_BASE + D.MAT_44))
    assert touched[6:9] == ['w3, [x22, #8]', 'w3, [x22, #0xc]',
                            'w3, [x22, #0xc]'], touched
    assert all(t.startswith('w3') for t in touched[2:9]), touched
    assert touched[9].endswith('[sp, #0x%x]' % (D.BLOCK_BASE + D.TINT_LANE))
    assert touched[10].endswith('[sp, #0x%x]' % (D.BLOCK_BASE + D.MAGIC_LANE))


def test_an_untouched_bss_reaches_the_shader_as_white():
    """
    The regression that blacked out build 469's main menu, pinned down.

    BSS is zero-filled at load and the ONLY thing that writes the tint slot
    is `field_draw_everything`. The transport now runs on EVERY draw in the
    game -- menus, battle, the world map -- so on the title screen, before any
    field has ever run the tick, it is stamping a colour nothing has chosen.
    While the slot held the tint directly that colour was 0x000000, the
    shaders multiplied the whole menu by it, and the screen went black with no
    way to see which save was highlighted.

    Storing the complement is what makes the untouched state mean white, and
    white is identity.
    """
    w = World()
    w.mem.setu(w.bss + D.BSS_TINT_INV, 0, 4)      # exactly as the loader left it
    w.fill_block()
    assert w.submit() == D.BLOCK_RESUME
    assert w.block_word(D.TINT_LANE) & 0xFFFFFF == D.TINT_WHITE
    assert w.block_word(D.MAGIC_LANE) == D.TINT_MAGIC


def test_the_flip_cave_puts_white_back_at_the_frame_boundary():
    """
    `gfx_drv_flip` presents the frame, so a tint still standing there belongs
    to a field draw that did not reach its gray quads. Clearing here bounds
    such a leak to one frame; it can never introduce a tint.
    """
    w = World(options=0x05, hours=2)
    w.run()
    assert w.tint() != D.TINT_WHITE
    sp = w.cpu.sp
    assert w.run('flip') == D.FLIP_RESUME
    assert w.tint() == D.TINT_WHITE
    assert w.cpu.sp == sp, 'the flip cave moved the stack'
    assert w.mem.u(sp + 8, 8) == w.cpu.x[23], 'the displaced store was lost'
def test_white_is_stored_as_zero_not_as_ffffff():
    """The clear cave and the tick's white exit must agree with the loader."""
    for which in ('clear', 'tick'):
        w = World(options=0x05, hours=2, field_id=151)      # outdoor, night
        w.run()
        assert w.tint() != D.TINT_WHITE, which              # a real tint first
        if which == 'clear':
            assert w.run('clear') == D.CLEAR_RESUME
        else:
            w.mem.setu(w.host(D.G_FIELD_ID), 500, 2)        # indoors -> white
            assert w.run() == D.TICK_RESUME
        assert w.mem.u(w.bss + D.BSS_TINT_INV, 4) == 0, which
        assert w.tint() == D.TINT_WHITE, which


def test_the_clear_cave_puts_white_back():
    w = World(options=0x05, hours=2)
    w.run()
    assert w.tint() != D.TINT_WHITE
    assert w.run('clear') == D.CLEAR_RESUME
    assert w.tint() == D.TINT_WHITE


# ----------------------------------------------------------- the invariants
def test_every_cave_restores_the_host_frame_it_borrowed():
    # the displaced word is whatever stood at each site
    pushes = {'tick': 0x50, 'clear': 0x00, 'block': 0x00, 'flip': 0x00}
    for which, push in pushes.items():
        w = World(options=0x07)
        marks = {r: 0x9900000000000000 + r for r in range(19, 29)}
        for r, v in marks.items():
            w.cpu.x[r] = v
        sp = w.cpu.sp
        w.run(which)
        for r, v in marks.items():
            assert w.cpu.x[r] == v, ('%s clobbered x%d' % (which, r))
        assert w.cpu.sp == sp - push, which


def test_the_block_cave_leaves_the_helper_s_live_registers_alone():
    """
    x2 and x3 are the cave's scratch and the stock code reassigns both before
    reading them. Everything else +0x10D9D70 still needs -- x0, the pool and
    index it is about to load into x24/x26, and the callee-saved file -- has
    to come out exactly as it went in.
    """
    w = World()
    marks = {r: 0x7700000000000000 + r for r in range(4, 29)}
    marks[0] = 0x1234
    for r, v in marks.items():
        w.cpu.x[r] = v
    w.fill_block()
    w.submit()
    for r, v in marks.items():
        assert w.cpu.x[r] == v, 'the block cave clobbered x%d' % r


def test_the_displaced_words_are_position_independent():
    """
    A cave re-emits the word it displaced at its own address, so a
    PC-relative one is only safe if it is RE-ENCODED. `adrp`/`adr` never are,
    and nothing here may displace one; a `bl` is, and the next test proves it
    lands where the stock one did.
    """
    reencoded = {hook for _n, hook, _o, _b, _a in D.all_ui_sites()}
    reencoded.add(D.SKY_PAGE_HOOK)
    reencoded.add(D.DEF_ALLOC_HOOK)    # 652: the cave branches to its target
    for _n, site, orig, what in D.SITES:
        assert (orig & 0x1F000000) != 0x10000000, '%s displaces adrp/adr' % what
        if (orig & 0x7C000000) == 0x14000000:
            assert site in reencoded, (
                '%s displaces a branch and does not re-encode it' % what)


def test_a_displaced_call_still_calls_the_same_function():
    """
    `bl` is PC-relative, so copying its word into a cave half a megabyte away
    would call half a megabyte off target. Walk each world cave and require
    the call it makes to be the one the stock word made.
    """
    _e, placed, _c, _b, _o, _p, _bm, _s, _g, _k = layout()
    blob = open(MODULE, 'rb').read()
    _segs, _raw, own = ff7nx_tables.open_module(blob)
    text = own.text
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    for name, hook, orig, _before, _after in D.all_ui_sites():
        if (orig & 0x7C000000) != 0x14000000:
            continue
        imm = orig & 0x3FFFFFF
        if imm & (1 << 25):
            imm -= 1 << 26
        want = hook + (imm << 2)
        assert want in (D.WORLD_DRAW_OBJ, D.BATTLE_UI,
                        D.BATTLE_MENU_CALLEE, D.CONDOR_ON_CALLEE,
                        D.CONDOR_OFF_CALLEE, D.CONDOR_UNITS_CALLEE), (
                            name, hex(want))
        pc, seen, calls = hook, set(), []
        while True:
            word = placed.get(pc, struct.unpack_from('<I', text, pc)[0])
            ins = next(iter(md.disasm(struct.pack('<I', word), pc)))
            if ins.mnemonic == 'bl':
                calls.append(int(ins.op_str.lstrip('#'), 16))
            if ins.mnemonic == 'b':
                nxt = int(ins.op_str.lstrip('#'), 16)
                if nxt == hook + 4:
                    break
                assert nxt not in seen, '%s loops' % name
                seen.add(nxt)
                pc = nxt
                continue
            pc += 4
        assert calls == [want], (name, [hex(c) for c in calls], hex(want))

    # The Highwind wrapper is the same call-reencoding problem, but its
    # before/after work additionally scopes the special layer-3 selector.
    hook, orig = D.SKY_PAGE_HOOK, D.SKY_PAGE_ORIG
    pc, seen, calls = hook, set(), []
    while True:
        word = placed.get(pc, struct.unpack_from('<I', text, pc)[0])
        ins = next(iter(md.disasm(struct.pack('<I', word), pc)))
        if ins.mnemonic == 'bl':
            calls.append(int(ins.op_str.lstrip('#'), 16))
        if ins.mnemonic == 'b':
            nxt = int(ins.op_str.lstrip('#'), 16)
            if nxt == hook + 4:
                break
            assert nxt not in seen, 'sky wrapper loops'
            seen.add(nxt)
            pc = nxt
            continue
        pc += 4
    assert calls == [D.WORLD_DRAW_OBJ]


def test_no_hook_collides_with_the_facial_runtime():
    """Both features ship together and neither may displace the other."""
    import ff7nx_facial as F
    ours = {s for _n, s, _o, _w in D.SITES}
    theirs = {s for _n, s, _o, _w in F.sites(0x1000)}
    assert not (ours & theirs), sorted(ours & theirs)


# ------------------------------------------------------- the build-time half
def test_the_bitmap_is_keyed_by_field_id():
    ids = D.read_maplist(os.path.join(ROOT, 'game_data_files', 'field',
                                      'flevel.lgp'))
    assert len(ids) == 788
    assert ids[0] == 'dummy'
    assert ids[151] == 'mds7', ids[151]
    assert ids[343] == 'farm', ids[343]


def test_a_flevel_with_no_time_actor_refuses_rather_than_shipping_noon():
    """A cycle stuck at midday is a silent no-op, and a silent no-op is worse
    than a build that says why."""
    try:
        D.outdoor_bitmap(['dummy', 'mds7'], {})
    except ValueError as exc:
        assert 'outdoor' in str(exc)
    else:
        raise AssertionError('an empty field source was accepted')


def test_the_bitmap_reads_echo_s_own_loose_fields():
    """End to end on the real mod, with the real maplist: the bit for a field
    has to be what that field's own Time actor says."""
    import glob
    src = os.path.join(ROOT, 'cache', 'Echo-s', 'Echo-S', 'flevel.lgp')
    if not os.path.isdir(src):
        return
    paths = {os.path.basename(p): p for p in glob.glob(os.path.join(src, '*'))
             if os.path.isfile(p)}
    ids = D.read_maplist(os.path.join(ROOT, 'game_data_files', 'field',
                                      'flevel.lgp'))
    bits = D.outdoor_bitmap(ids, paths)
    def outdoor(name):
        i = ids.index(name)
        return bool(bits[i >> 3] & (1 << (i & 7)))
    # the slums and the farm are outside; a train carriage and an inn are not
    assert outdoor('mds7'), 'the Sector 7 slums read as indoor'
    assert outdoor('farm')
    assert not outdoor('tin_1')
    # The forced list is the only difference from Echo-S's own 256.
    assert outdoor('convil_2')
    for name in ('frmin', 'chrin_1a', 'ancnt1', 'fship_5', 'ithos', 'itmin1',
                 'snw_w', 'sninn_1', 'sninn_2', 'sninn_b1', 'snmin1',
                 'snmin2', 'icedun_1', 'icedun_2', 'hyou5_3', 'hyou8_2',
                 'hyou12', 'hyou13_2'):
        assert outdoor(name), name
    own = D.outdoor_bitmap(ids, paths, force=())
    assert sum(bin(b).count('1') for b in own) == 256
    forced = sorted(ids.index(n) for n in D.FORCE_OUTDOOR)
    assert len(forced) == len(set(forced)) == 44
    for i in forced:
        assert not own[i >> 3] & (1 << (i & 7)), ids[i]
    assert sum(bin(b).count('1') for b in bits) == 256 + 44
    diff = [i for i in range(len(bits) * 8)
            if (bits[i >> 3] ^ own[i >> 3]) & (1 << (i & 7))]
    assert diff == forced
    assert not outdoor('convil_1')


def test_the_phase_table_round_trips():
    blob = D.phase_table()
    assert len(blob) == len(D.PHASES) * D.PHASE_ROW_BYTES
    for i, (bound, c0, c1) in enumerate(D.PHASES):
        row = blob[i * 8:(i + 1) * 8]
        assert struct.unpack_from('<H', row, 0)[0] == bound
        assert tuple(row[2:5]) == c0
        assert tuple(row[5:8]) == c1


def test_the_shaders_carry_the_magic():
    """The module half is useless without the vertex half, and the vertex
    half lives in files this repo ships."""
    for name in ('lmain_vv.glsl', 'tlmain_vv.glsl'):
        path = os.path.join(ROOT, 'shaders', 'wide_screen', name)
        text = open(path).read()
        assert D.SHADER_MARK in text, name
        assert 'blendMode.y' in text, name
        # and it must still be guarded, or a card without the module goes black
        assert 'if (blendMode.z ==' in text, name


def _main():
    fails = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith('test_') or not callable(fn):
            continue
        try:
            fn()
            print('  ok    %s' % name)
        except AssertionError as exc:
            fails += 1
            print('  FAIL  %s: %s' % (name, exc))
        except Exception as exc:                              # noqa: BLE001
            fails += 1
            print('  ERROR %s: %s: %s' % (name, type(exc).__name__, exc))
    print('\n%s' % ('PASS' if not fails else '%d FAILURE(S)' % fails))
    return 1 if fails else 0




# ------------------------------------------------- arming an existing save
def test_force_on_arms_a_save_that_never_ran_the_opening():
    """
    Bits 0 and 1 are set by `init` in blackbgh and `dir` in md1stin, both of
    which survive into the shipped flevel. But they are SAVEMAP bits, so a
    save made before this feature existed has never run either script and the
    cycle would stay dead on it. The build turns this on because the mod's own
    "Day Night" option already is.
    """
    w = World(options=0x00, hours=2, force_on=True)
    assert w.run() == D.TICK_RESUME
    assert w.gget8(D.G_OPTIONS) & D.OPT_ENABLED
    assert w.gget8(D.G_OPTIONS) & D.OPT_ADVANCE
    assert w.rgb() == TINT(D.NIGHT_RGB)


def test_force_on_still_honours_the_per_field_outdoor_flag():
    """Arming the cycle must not also tint interiors."""
    w = World(options=0x00, hours=2, field_id=500, force_on=True)
    assert w.run() == D.TICK_RESUME
    assert w.tint() == D.TINT_WHITE


def test_a_clock_that_never_ran_is_seeded_to_morning():
    """Day zero is the never-initialised mark, and starting an existing save
    in a black midnight it cannot explain is worse than starting it at 8am."""
    w = World(options=0x07, hours=0, minutes=0, days=0, months=0)
    assert w.run() == D.TICK_RESUME
    hours, minutes, days, months = w.clock()
    assert (hours, minutes, days, months) == (8, 0, 1, 0), w.clock()
    assert w.rgb() == TINT(D.phase_colour(8 * 60))


def test_a_running_clock_is_never_reseeded():
    w = World(options=0x07, hours=23, minutes=30, days=5, months=2)
    w.run()
    hours, minutes, days, months = w.clock()
    assert (hours, days, months) == (23, 5, 2), w.clock()


# --------------------------------------------- the whole chain, end to end
def _as_the_shader_reads_it(block_y):
    """`lmain_vv.glsl`'s own arithmetic, in Python.

    int dnTint = blendMode.y;
    vColor.rgb *= vec3((dnTint >> 16) & 255, (dnTint >> 8) & 255,
                       dnTint & 255) * (1.0 / 255.0);
    """
    signed = block_y - (1 << 32) if block_y >> 31 else block_y
    return ((signed >> 16) & 255, (signed >> 8) & 255, signed & 255)


def test_the_whole_chain_from_the_clock_to_the_shader():
    """
    Clock -> outdoor bitmap -> phase colour -> BSS -> BlockVertex -> shader,
    decoded with the shader's own expression, at four times of day and at the
    three points where the answer must be white.

    Every earlier build broke somewhere in the middle of this chain and the
    only instrument was the television. This runs the whole of it.
    """
    for hour, want in ((2, TINT(D.NIGHT_RGB)), (15, TINT(D.MIDDAY_RGB)),
                       (22, TINT(D.NIGHT_RGB)),
                       (7, TINT(D.MORNING_RGB)),
                       (12, TINT(D.phase_colour(12 * 60))),
                       (17, TINT(D.phase_colour(17 * 60)))):
        w = World(options=0x05, hours=hour, field_id=151)     # 151 is outdoor
        assert w.run() == D.TICK_RESUME
        w.fill_block()
        assert w.submit() == D.BLOCK_RESUME
        assert _as_the_shader_reads_it(w.block_word(D.TINT_LANE)) == want, hour
        assert w.block_word(D.MAGIC_LANE) == D.TINT_MAGIC

    white = (255, 255, 255)
    # indoors
    w = World(options=0x05, hours=2, field_id=500)
    w.run()
    w.fill_block()
    w.submit()
    assert _as_the_shader_reads_it(w.block_word(D.TINT_LANE)) == white

    # the cycle switched off in the savemap
    w = World(options=0x00, hours=2, field_id=151)
    w.run()
    w.fill_block()
    w.submit()
    assert _as_the_shader_reads_it(w.block_word(D.TINT_LANE)) == white

    # and after the UI point, which is what keeps dialogue boxes unshaded
    for after in ('clear', 'flip'):
        w = World(options=0x05, hours=2, field_id=151)
        w.run()
        assert w.tint() != D.TINT_WHITE
        w.run(after)
        w.fill_block()
        w.submit()
        assert _as_the_shader_reads_it(w.block_word(D.TINT_LANE)) == white, after




# ------------------------------------------------- the clock override
def test_the_pinned_clock_holds_its_hour_and_writes_nothing():
    """
    `SEVENTH_NX_DAYNIGHT_HOUR=22`. A day is 32 real minutes, so without this
    "does night look right" costs a wait in a field for a colour you are not
    sure is coming. Pinned, the colour is night on the first frame -- and the
    savemap is left exactly as it was, so dropping the variable puts the real
    clock back where it stood.
    """
    w = World(options=0x07, hours=4, minutes=17, days=9, months=3,
              freeze_hour=22)
    for _ in range(500):
        assert w.run() == D.TICK_RESUME
    assert w.rgb() == TINT(D.NIGHT_RGB), w.rgb()
    assert w.clock() == (4, 17, 9, 3), 'the pinned clock touched the savemap'
    assert w.frames() == 0, 'the pinned clock ran the frame counter'


def test_the_pinned_clock_still_obeys_indoors_and_the_enable_bit():
    """It pins the hour, not the feature. A diagnostic that also disabled the
    gating would answer a question nobody asked."""
    inside = World(options=0x07, field_id=500, freeze_hour=22)
    assert inside.run() == D.TICK_RESUME
    assert inside.tint() == D.TINT_WHITE
    off = World(options=0x00, field_id=151, freeze_hour=22)
    assert off.run() == D.TICK_RESUME
    assert off.tint() == D.TINT_WHITE


def test_every_pinned_hour_matches_the_free_running_clock_at_that_hour():
    for hour in range(24):
        pinned = World(options=0x05, hours=0, minutes=0, freeze_hour=hour)
        pinned.run()
        free = World(options=0x05, hours=hour, minutes=0)
        free.run()
        assert pinned.rgb() == free.rgb(), hour


def test_the_override_is_off_unless_it_is_asked_for():
    keep = os.environ.pop(D.FREEZE_ENV, None)
    try:
        assert D.freeze_hour_from_env() is None
        os.environ[D.FREEZE_ENV] = '22'
        assert D.freeze_hour_from_env() == 22
        os.environ[D.FREEZE_ENV] = ' 0 '
        assert D.freeze_hour_from_env() == 0
        for bad in ('24', '-1', 'midnight', '1.5'):
            os.environ[D.FREEZE_ENV] = bad
            try:
                D.freeze_hour_from_env()
            except ValueError:
                continue
            raise AssertionError('%r was accepted as an hour' % bad)
    finally:
        os.environ.pop(D.FREEZE_ENV, None)
        if keep is not None:
            os.environ[D.FREEZE_ENV] = keep


def test_the_override_cannot_invalidate_an_archive_cache():
    """
    Build 467's lesson, written down as a test. `SEVENTH_NX_FACIAL` was in the
    generic environment sweep, so dropping a flag that only moved a default
    refused the next build and cost a rebase. This pass writes into
    `exefs/main` and nowhere else, so neither its variable nor its source
    belongs in any archive fingerprint.
    """
    try:
        import build
    except ImportError:                       # a bare checkout, no image libs
        text = open(nxpaths.src_file('build.py')).read()
        env = text.split('MAIN_ONLY_ENV = ')[1].split('))')[0]
        mods = text.split('MAIN_ONLY_MODULES = ')[1].split('))')[0]
        assert 'ff7nx_daynight.FREEZE_ENV' in env
        assert "'ff7nx_daynight.py'" in mods
        return
    assert D.FREEZE_ENV in build.MAIN_ONLY_ENV
    assert 'ff7nx_daynight.py' in build.MAIN_ONLY_MODULES
    assert D.FREEZE_ENV in build.ARCHIVE_NEUTRAL_ENV
    assert 'ff7nx_daynight.py' in build.ARCHIVE_NEUTRAL_MODULES




# ------------------------------------------- the clock outside the field
def test_the_clock_ticks_once_a_frame_in_battle_and_on_the_world_map():
    """
    Why the time of day never matched a PC build.

    FFNx advances the clock from the main loop in MODE_FIELD, MODE_BATTLE and
    MODE_WORLDMAP (time.cpp:180-190). Ours only ran inside
    `field_draw_everything`, so it stopped dead for every battle and every step
    on the world map -- on a real save that is most of the wall clock, and the
    in-game hour ends up nowhere near a PC build's.
    """
    per_minute = 40                            # what `layout` builds with
    for mode in (D.DRIVER_BATTLE, D.DRIVER_WORLD):
        w = World(options=0x07, hours=9, minutes=0, mode=mode)
        for _ in range(per_minute):
            assert w.run('flip') == D.FLIP_RESUME
        assert w.minute_of_day() == 9 * 60 + 1, (mode, w.clock())


def test_the_clock_stops_in_the_modes_ffnx_stops_in():
    """Menus, movies, the title screen -- PC does not advance time in any of
    them, and a diagnostic that ran the clock everywhere would drift away from
    a PC save in the other direction."""
    for mode in (1, D.DRIVER_WORLD + 1, 6, 9, 15, 0):
        w = World(options=0x07, hours=9, minutes=30, mode=mode)
        for _ in range(400):
            assert w.run('flip') == D.FLIP_RESUME
        assert w.clock() == (9, 30, 1, 0), mode


def test_a_field_frame_is_not_counted_twice():
    """
    The field ticks in `field_draw_everything` and the flip cave ticks
    everything else, so a field frame passes through both. `BSS_TICKED` is
    what stops the day running at double speed in the one mode that matters
    most.
    """
    w = World(options=0x07, hours=9, minutes=0, mode=D.DRIVER_FIELD)
    for _ in range(40):
        assert w.run() == D.TICK_RESUME
        assert w.ticked() == 1, 'the field tick did not claim the frame'
        assert w.run('flip') == D.FLIP_RESUME
        assert w.ticked() == 0, 'the flip cave did not release the frame'
    assert w.minute_of_day() == 9 * 60 + 1, w.clock()

    # and the flip cave alone, in field mode, does advance it -- a field frame
    # that never reached the draw is still a frame
    w = World(options=0x07, hours=9, minutes=0, mode=D.DRIVER_FIELD)
    for _ in range(40):
        w.run('flip')
    assert w.minute_of_day() == 9 * 60 + 1, w.clock()


def test_the_flip_tick_obeys_the_enable_and_advance_bits():
    off = World(options=0x00, hours=9, minutes=30, mode=D.DRIVER_BATTLE)
    for _ in range(400):
        off.run('flip')
    assert off.clock() == (9, 30, 1, 0), 'the cycle is off and time moved'
    paused = World(options=0x05, hours=9, minutes=30, mode=D.DRIVER_BATTLE)
    for _ in range(400):
        paused.run('flip')
    assert paused.clock() == (9, 30, 1, 0), 'bit 1 is clear and time moved'


def test_the_flip_tick_never_leaves_a_tint_behind():
    """It advances the clock, it does not arm anything. Battle and the world
    map are not tinted in this build, and the frame boundary is still where
    the tint is put back to white."""
    w = World(options=0x05, hours=2, mode=D.DRIVER_BATTLE)
    w.run()                                    # a field tick armed night
    assert w.tint() != D.TINT_WHITE
    assert w.run('flip') == D.FLIP_RESUME
    assert w.tint() == D.TINT_WHITE


def test_the_mode_cave_records_what_set_driver_mode_was_given():
    w = World()
    for mode in (1, D.DRIVER_FIELD, D.DRIVER_BATTLE, D.DRIVER_WORLD, 15):
        w.cpu.x[0] = mode
        assert w.run('mode') == D.MODE_RESUME
        assert w.mem.u(w.bss + D.BSS_MODE, 4) == mode, mode
        assert w.cpu.x[0] == mode, 'the cave clobbered the mode argument'


def test_field_is_still_driver_mode_two():
    """
    The one number BATTLE and WORLDMAP hang off. `ff7nx_letterbox` identified
    FIELD as driver mode 2 -- the dispatch index that computes the field
    letterbox -- and confirmed it on hardware, so it is checked at install
    rather than remembered.
    """
    blob = open(MODULE, 'rb').read()
    segs, _raw, own = ff7nx_tables.open_module(blob)
    D.check_driver_modes(own.text, own.rodata, segs[1][1])
    table = D.MODE_TABLE - segs[1][1]
    targets = [D.MODE_TABLE + struct.unpack_from('<i', own.rodata,
                                                 table + i * 4)[0]
               for i in range(D.MODE_CASES)]
    assert targets[D.DRIVER_FIELD - 1] == D.MODE_FIELD_CASE
    # battle and the world map are their own cases, not shared fallbacks
    for mode in (D.DRIVER_BATTLE, D.DRIVER_WORLD):
        assert targets.count(targets[mode - 1]) == 1, mode


# ---------------------------------------------------------- the dial
def test_the_strength_dial_only_ever_lifts_toward_white():
    full = D.gamma_lut(D.FULL_STRENGTH)
    assert D.GAMMA_LUT == D.gamma_lut(D.DEFAULT_STRENGTH)
    for strength in (0, 25, 50, 75, 100):
        lut = D.gamma_lut(strength)
        assert lut[255] == 255, 'white stopped being white at %d' % strength
        assert all(lut[i] <= lut[i + 1] for i in range(255)), strength
        for v in range(256):
            assert full[v] <= lut[v] <= 255, (strength, v)
    assert D.gamma_lut(0) == bytes([255]) * 256, 'zero should be no tint'


def test_the_dial_reaches_the_cave():
    soft = 60
    w = World(options=0x05, hours=2, field_id=151)
    w.run()
    assert w.rgb() == TINT(D.NIGHT_RGB)
    lut = D.gamma_lut(soft)
    entries, placed, code, bss, outdoor_at, phase_at, bitmap, space, \
        gamma_at, _sky_at = layout()
    # the table is what carries it, so prove the table is what the cave reads
    w.mem.write(gamma_at, lut)
    w2 = World(options=0x05, hours=2, field_id=151)
    w2.mem.write(w2.gamma_at, lut)
    w2.run()
    assert w2.rgb() == D.as_the_shader_multiplies(D.NIGHT_RGB, soft)
    assert w2.rgb() != w.rgb(), 'the dial changed nothing'
    assert min(w2.rgb()) > min(w.rgb()), 'the dial darkened instead of lifting'


def test_the_dial_is_off_unless_it_is_asked_for():
    keep = os.environ.pop(D.STRENGTH_ENV, None)
    try:
        assert D.strength_from_env() == D.DEFAULT_STRENGTH
        os.environ[D.STRENGTH_ENV] = '70'
        assert D.strength_from_env() == 70
        for bad in ('101', '-1', 'half', '70.5'):
            os.environ[D.STRENGTH_ENV] = bad
            try:
                D.strength_from_env()
            except ValueError:
                continue
            raise AssertionError('%r was accepted as a percentage' % bad)
    finally:
        os.environ.pop(D.STRENGTH_ENV, None)
        if keep is not None:
            os.environ[D.STRENGTH_ENV] = keep




# ------------------------------------------- a clock that was never ours
def test_a_clock_outside_its_own_ranges_is_not_a_time():
    """
    The "my old saves have the wrong time" report, pinned.

    The clock is five ordinary field-script variables at savemap +0x0BAE, so a
    save made before this feature existed has whatever the game left in them.
    Day 0 alone was the old test and it missed every such save whose day byte
    happened to be non-zero: the cycle then ran from a nonsense hour for the
    rest of the playthrough.
    """
    fresh = (8, 0, 1, 0)                       # hours, minutes, days, months
    for bad in (dict(days=0),                  # never initialised
                dict(days=33), dict(days=200),
                dict(months=13), dict(months=0xFF),
                dict(days=77, months=40)):
        args = dict(options=0x07, hours=3, minutes=3, days=3, months=3)
        args.update(bad)
        w = World(**args)
        assert w.run() == D.TICK_RESUME
        assert w.clock() == fresh, (bad, w.clock())

    # ... but an hour or a minute over the top is a SCRIPT, not garbage, and
    # is carried rather than judged. Build 477 seeded on these and that is
    # what stopped you ever sleeping into the night.
    for over, want in ((dict(hours=24), (0, 3, 4, 3)),
                       (dict(hours=31), (7, 3, 4, 3)),
                       (dict(minutes=60), (4, 0, 3, 3))):
        args = dict(options=0x05, hours=3, minutes=3, days=3, months=3)
        args.update(over)
        w = World(**args)
        assert w.run() == D.TICK_RESUME
        assert w.clock() == want, (over, w.clock())


def test_a_clock_this_feature_has_been_running_is_left_alone():
    """The other half: every in-range date has to survive, or the cycle would
    reset itself on some ordinary minute of some ordinary day."""
    for hours, minutes, days, months in ((0, 0, 1, 0), (23, 59, 31, 11),
                                         (12, 30, 15, 6), (0, 0, 31, 11)):
        w = World(options=0x05, hours=hours, minutes=minutes, days=days,
                  months=months)
        assert w.run() == D.TICK_RESUME
        assert w.clock() == (hours, minutes, days, months)


def test_the_scripts_that_set_the_clock_still_reach_it():
    """
    Echo-S really does set the time in places -- every inn's `Sleep` actor
    does `PLUS! bank1[0x0F], +8` and a handful of story fields assign the hour
    outright. Those live in ORDINARY actors, not the `Time` actor
    `echo_s_flevel` strips, so they ship; what matters here is that the
    runtime reads the savemap every frame rather than caching, so a script
    write lands on the very next frame.
    """
    w = World(options=0x05, hours=9, minutes=0, field_id=151)
    w.run()
    assert w.rgb() == TINT(D.phase_colour(9 * 60))
    w.gset8(D.G_HOURS, 22)                     # an inn's +8, or a story scene
    assert w.run() == D.TICK_RESUME
    assert w.rgb() == TINT(D.NIGHT_RGB), w.rgb()
    assert w.clock()[0] == 22, 'the runtime overwrote the scripted hour'


def test_the_pinned_clock_still_ignores_a_broken_save():
    w = World(options=0x07, hours=0xA5, days=0, freeze_hour=22)
    assert w.run() == D.TICK_RESUME
    assert w.rgb() == TINT(D.NIGHT_RGB)




# --------------------------------------------- battle and the world map
def test_the_world_map_is_tinted_without_the_outdoor_bit():
    """
    FFNx gates FIELD and BATTLE on bit 2 and the world map on nothing
    (time.cpp:230-240) -- there is no such thing as an indoor world map, and
    the bit there is whatever the last field left.
    """
    w = World(options=0x03, hours=2, mode=D.DRIVER_WORLD)   # bit 2 CLEAR
    assert w.run('flip') == D.FLIP_RESUME
    assert w.rgb() == TINT(D.NIGHT_RGB), w.rgb()
    assert w.armed() == w.tint()


def test_battle_is_tinted_only_where_the_last_field_was_outdoors():
    outside = World(options=0x07, hours=2, mode=D.DRIVER_BATTLE)  # bit 2 set
    assert outside.run('flip') == D.FLIP_RESUME
    assert outside.rgb() == TINT(D.NIGHT_RGB)
    inside = World(options=0x03, hours=2, mode=D.DRIVER_BATTLE)   # bit 2 clear
    assert inside.run('flip') == D.FLIP_RESUME
    assert inside.tint() == D.TINT_WHITE
    assert inside.armed() == D.TINT_WHITE


def test_a_field_frame_is_put_back_to_white_at_the_boundary():
    """The insurance this cave was originally for survives the change: in
    FIELD mode the flip cave still whitens rather than arming, because the
    field tick is what arms and the gray quads are what disarm."""
    w = World(options=0x05, hours=2, mode=D.DRIVER_FIELD)
    w.run()
    assert w.tint() != D.TINT_WHITE
    assert w.run('flip') == D.FLIP_RESUME
    assert w.tint() == D.TINT_WHITE


def test_the_ui_points_move_the_tint_and_not_the_decision():
    """
    Three world points and two battle ones, and between them they must never
    lose what the frame decided -- that is the whole reason `BSS_ARMED_INV`
    exists. FFNx toggles its flag the same way and recomputes nothing.
    """
    for mode in (D.DRIVER_WORLD, D.DRIVER_BATTLE):
        w = World(options=0x07, hours=2, mode=mode)
        w.run('flip')
        night = TINT(D.NIGHT_RGB)
        assert w.rgb() == night
        packed = night[0] << 16 | night[1] << 8 | night[2]
        # the world map's three: off, back on, off again
        for name, expect_white in (('world_off1', True), ('world_on', False),
                                   ('world_off2', True)):
            assert w.run(name) == SITE_RESUME[name]
            assert (w.tint() == D.TINT_WHITE) is expect_white, name
            assert w.armed() == packed, (
                '%s destroyed the frame\'s decision' % name)
        # battle's two are WRAPPERS: they come back armed on their own
        w.run('world_on')
        for name in ('battle_text', 'battle_box'):
            assert w.run(name) == SITE_RESUME[name]
            assert w.rgb() == night, '%s did not put the tint back' % name
            assert w.armed() == packed, name
        # and the menu marker leaves it off for the rest of the frame
        assert w.run('battle_menu') == SITE_RESUME['battle_menu']
        assert w.tint() == D.TINT_WHITE
        assert w.armed() == packed


def test_the_ui_caves_restore_every_register_they_borrow():
    """
    These five are spliced into RECOMPILED code, where this project's rule is
    that x19..x28 carry everything that has to survive the address translator.
    A cave there may leave behind only what the word it displaced would have
    left behind by itself -- so the comparison is against the stock word, not
    against nothing.
    """
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    for name, _hook, orig, _before, _after in D.all_ui_sites():
        ins = next(iter(md.disasm(struct.pack('<I', orig), 0x1000)))
        # what the STOCK word writes, and is therefore allowed to differ
        allowed = {30} if ins.mnemonic == 'bl' else set()
        moves_sp = False
        if ins.mnemonic in ('ldr', 'ldp'):
            allowed.update(int(r[1:]) for r in ins.op_str.split(',')[:-1]
                           if r.strip().startswith('x'))
            moves_sp = ']' in ins.op_str and ins.op_str.rstrip().endswith(
                tuple('0123456789')) and '],' in ins.op_str

        w = World(options=0x07, hours=2, mode=D.DRIVER_WORLD)
        w.run('flip')
        marks = {r: 0x4400000000000000 + r for r in range(9, 29)
                 if r not in allowed}
        for r, v in marks.items():
            w.cpu.x[r] = v
        sp = w.cpu.sp
        w.run(name)
        for r, v in marks.items():
            assert w.cpu.x[r] == v, ('%s clobbered x%d' % (name, r))
        if not moves_sp:
            assert w.cpu.sp == sp, name
        # and x16/x17, the two it actually borrows, are always put back
        assert 16 not in allowed and 17 not in allowed, name


def test_the_world_and_battle_sites_are_where_ffnx_replaces_them():
    """
    Counted, not remembered. The three world sites are the 8th, 11th and 15th
    call to `engine_draw_graphics_object` inside one function, which is what
    makes them x86 +0x175, +0x1BE and +0x208; battle's exit is the word before
    its only `ret`.
    """
    blob = open(MODULE, 'rb').read()
    _segs, _raw, own = ff7nx_tables.open_module(blob)
    D.check_scene_sites(own.text)
    import nxmap as N
    m = N.Main(MODULE)
    assert m.x86_to_arm[0x74C179] == D.WORLD_DRAW_ALL
    assert m.x86_to_arm[0x66E641] == D.WORLD_DRAW_OBJ
    assert m.x86_to_arm[0x42908C] == D.BATTLE_UI
    # and the map is trustworthy: the two field points it also knows are the
    # two this feature has been shipping since build 465
    assert m.x86_to_arm[0x63A60B] == D.TICK_HOOK
    assert m.x86_to_arm[0x644E90] == 0x9F36D0




# ------------------------------------------- the Highwind window sky, whole
def test_the_sky_table_round_trips():
    rows = [(68, 4, 14), (71, 4, 14)]
    blob = D.sky_table(rows)
    assert len(blob) == (len(rows) + 1) * D.SKY_ROW
    for i, (field, first, last) in enumerate(rows):
        got = struct.unpack_from('<HBB', blob, i * D.SKY_ROW)
        assert got == (field, first, last)
    assert struct.unpack_from('<H', blob, len(rows) * D.SKY_ROW)[0] == 0
    assert D.sky_table([]) == struct.pack('<HBB', 0, 0, 0)


def test_the_sky_range_is_measured_from_the_shipped_flevel():
    """
    The page numbers are NOT a constant. This project repacks field
    background pages, so a range carried from one build could land on the
    hull in another. `sky_plan` reads the archive being shipped and proves,
    per field, that layer 3's pages are contiguous and that no other layer
    binds one of them -- and refuses the field otherwise.

    Skipped where no flevel is reachable; `SEVENTH_NX_SKY_FLEVEL` points it
    at one. It is deliberately not run against the 1.4 GB built archive by
    default: the whole suite is meant to stay fast.
    """
    path = os.environ.get('SEVENTH_NX_SKY_FLEVEL') or os.path.join(
        ROOT, 'game_data_files', 'field', 'flevel.lgp')
    if not os.path.isfile(path):
        return
    maplist = D.read_maplist(path)
    rows = D.sky_plan(path, maplist)
    seen = set()
    for field_id, first, last in rows:
        assert 0 < first <= last, (field_id, first, last)
        assert maplist[field_id] in {n.lower() for n in D.sky_field_names()}
        assert field_id not in seen
        seen.add(field_id)


def test_the_sky_never_reaches_the_ship_or_the_actors():
    """
    The promise, end to end and decoded with the shader's own expression:
    layer 3's pages carry the clock colour, and the pages the hull, the deck
    and the window frame are on carry white -- in the same field, on the same
    frame, at the same hour.
    """
    probe = _Probe(D.BSS_SKY_ACTIVE)
    field, first, last = SKY_ROWS[1]
    w = World(options=0x05, hours=2, field_id=field)
    w.run()
    night = TINT(D.SKY_NIGHT_RGB)
    for page, want in ((first, night), (last, night),
                       (0, (255, 255, 255)), (2, (255, 255, 255)),
                       (26, (255, 255, 255)), (28, (255, 255, 255))):
        # arm exactly as the page loop would, then let the draw happen
        saw = probe.run(w, page)
        w.mem.setu(w.bss + D.BSS_SKY_ACTIVE, saw, 4)
        w.fill_block()
        w.submit()
        got = _as_the_shader_reads_it(w.block_word(D.TINT_LANE))
        assert got == want, ('page %d gave %s, wanted %s' % (page, got, want))
        assert w.block_word(D.MAGIC_LANE) == D.TINT_MAGIC


def test_the_sky_slot_survives_the_frame_s_other_caves():
    """The clear and flip caves put the ORDINARY tint back to white; the
    sky's own slot is not theirs to touch, or the window would go white for
    the rest of the frame."""
    w = World(options=0x05, hours=2, field_id=SKY_ROWS[0][0])
    w.run()
    night = TINT(D.SKY_NIGHT_RGB)
    assert w.sky_rgb() == night
    for cave in ('clear', 'flip'):
        assert w.run(cave) == SITE_RESUME[cave]
        assert w.sky_rgb() == night, cave
        assert w.tint() == D.TINT_WHITE, cave


def test_the_sky_is_off_when_the_cycle_is_off():
    """One option bit owns the whole feature, the Highwind exception too."""
    w = World(options=0x00, hours=2, field_id=SKY_ROWS[0][0])
    assert w.run() == D.TICK_RESUME
    assert w.sky_tint() == D.TINT_WHITE
    assert w.mem.u(w.bss + D.BSS_SKY_LAST, 4) == 0
    probe = _Probe(D.BSS_SKY_ACTIVE)
    assert probe.run(w, SKY_ROWS[0][1]) == 0


def test_the_sky_field_list_can_be_overridden():
    keep = os.environ.pop(D.SKY_FIELDS_ENV, None)
    try:
        assert D.sky_field_names() == D.SKY_FIELD_NAMES
        os.environ[D.SKY_FIELDS_ENV] = 'off'
        assert D.sky_field_names() == ()
        os.environ[D.SKY_FIELDS_ENV] = 'fship_1, fship_12'
        assert D.sky_field_names() == ('fship_1', 'fship_12')
    finally:
        os.environ.pop(D.SKY_FIELDS_ENV, None)
        if keep is not None:
            os.environ[D.SKY_FIELDS_ENV] = keep




# ------------------------------- where a battle asks which rule applies
def _after_field(field_id, scene, mode, hour=2):
    """Stand in a field, then go somewhere and let the flip cave arm."""
    w = World(options=0x05, hours=hour, field_id=field_id,
              mode=D.DRIVER_FIELD)
    w.mem.setu(w.bss + D.BSS_SCENE_MODE, D.DRIVER_FIELD, 4)
    w.run()
    w.mem.setu(w.bss + D.BSS_SCENE_MODE, scene, 4)
    w.mem.setu(w.bss + D.BSS_MODE, mode, 4)
    w.mem.setu(w.bss + D.BSS_TICKED, 0, 4)
    assert w.run('flip') == D.FLIP_RESUME
    return w


def test_a_world_map_encounter_is_not_gated_by_the_last_field():
    """
    The bug, reported from hardware: set the clock to night, fight on the
    world map -- night. Walk into the Highwind, walk out, fight on the world
    map again -- daylight, until you next pass through somewhere outdoors.

    Bit 2 is the OUTDOOR flag and it describes a FIELD. FFNx gates battle on
    it too, so a battle inherits whatever the last field left, and after any
    interior that is "indoor". It is simply not a fact about a world-map
    encounter, so a battle now follows the rule of the scene it was entered
    from.
    """
    night = TINT(D.NIGHT_RGB)
    for field_id, what in ((69, 'the Highwind'), (500, 'a town interior'),
                           (151, 'the slums, outdoors')):
        w = _after_field(field_id, D.DRIVER_WORLD, D.DRIVER_WORLD)
        assert w.rgb() == night, ('the world map itself, after %s' % what)
        w = _after_field(field_id, D.DRIVER_WORLD, D.DRIVER_BATTLE)
        assert w.rgb() == night, ('a world-map encounter after %s' % what)


def test_a_battle_from_a_field_still_honours_that_field_s_bit():
    """The other half, unchanged and deliberately so: a battle fought inside
    an interior is not moonlit, and one in the slums is. That is FFNx's rule
    and it is right -- the bit really is about where you are standing."""
    assert _after_field(151, D.DRIVER_FIELD,
                        D.DRIVER_BATTLE).rgb() == TINT(D.NIGHT_RGB)
    for indoors in (69, 500):
        w = _after_field(indoors, D.DRIVER_FIELD, D.DRIVER_BATTLE)
        assert w.tint() == D.TINT_WHITE, indoors


def test_the_mode_cave_only_remembers_scenes():
    """
    Menus, movies, the swirl and the title screen all come through
    `set_driver_mode`. None of them is where a battle was entered from, so
    none of them may move `BSS_SCENE_MODE` -- otherwise opening the menu
    before an encounter would change how it looks.
    """
    w = World()
    for mode in (D.DRIVER_FIELD, D.DRIVER_WORLD):
        w.mem.setu(w.bss + D.BSS_SCENE_MODE, 0, 4)
        w.cpu.x[0] = mode
        assert w.run('mode') == D.MODE_RESUME
        assert w.mem.u(w.bss + D.BSS_SCENE_MODE, 4) == mode
        assert w.mem.u(w.bss + D.BSS_MODE, 4) == mode
    for mode in (1, D.DRIVER_BATTLE, 5, 6, 9, 13, 15):
        w.mem.setu(w.bss + D.BSS_SCENE_MODE, D.DRIVER_WORLD, 4)
        w.cpu.x[0] = mode
        assert w.run('mode') == D.MODE_RESUME
        assert w.mem.u(w.bss + D.BSS_SCENE_MODE, 4) == D.DRIVER_WORLD, mode
        assert w.mem.u(w.bss + D.BSS_MODE, 4) == mode, mode


if __name__ == '__main__':
    sys.exit(_main())



# ------------------------------------------------------ BUILD 572: Fort Condor
CONDOR_RESUME = {n: h + 4 for n, h, _o, _w in D.CONDOR_SITES}


def test_fort_condor_ticks_and_decides_but_does_not_apply():
    """The flip cave treats mode 13 like the world map -- no outdoor bit --
    ticks the clock, and decides night into ARMED only: TINT stays white so
    nothing outside the scene bracket can pick it up."""
    w = World(options=0x03, hours=2, mode=D.DRIVER_CONDOR)   # bit 2 CLEAR
    assert w.run('flip') == D.FLIP_RESUME
    # BUILD 661: ARMED is the ordinary night (units), the background slot
    # the deeper one (the map pages)
    assert w.armed() == TINT_PACK(D.NIGHT_RGB)
    assert _mini_bg(w) == TINT_PACK(D.ULTRA_NIGHT_RGB)
    assert w.tint() == D.TINT_WHITE
    w = World(options=0x07, hours=9, minutes=0, mode=D.DRIVER_CONDOR)
    for _ in range(40):
        assert w.run('flip') == D.FLIP_RESUME
    assert w.minute_of_day() == 9 * 60 + 1, w.clock()


def test_fort_condor_scene_bracket_tints_map_and_units_only():
    w = World(options=0x07, hours=2, mode=D.DRIVER_CONDOR)
    w.run('flip')
    deep, night = TINT(D.ULTRA_NIGHT_RGB), TINT(D.NIGHT_RGB)
    assert w.run('condor_on') == CONDOR_RESUME['condor_on']
    assert w.rgb() == deep                      # map pages: deeper night
    assert w.run('condor_units') == CONDOR_RESUME['condor_units']
    assert w.rgb() == night                     # units: ordinary night
    assert w.run('condor_off') == CONDOR_RESUME['condor_off']
    assert w.tint() == D.TINT_WHITE             # sprite lists: the UI
    assert w.armed() == TINT_PACK(D.NIGHT_RGB)  # the decision survives
    # a second pass through 0x5F5828 in the same frame tints again
    assert w.run('condor_on') == CONDOR_RESUME['condor_on']
    assert w.rgb() == deep


def test_fort_condor_daytime_and_disabled_cycle_stay_white():
    noon = World(options=0x07, hours=12, mode=D.DRIVER_CONDOR)
    noon.run('flip')
    noon.run('condor_on')
    off = World(options=0x00, hours=2, mode=D.DRIVER_CONDOR)
    off.run('flip')
    off.run('condor_on')
    assert off.tint() == D.TINT_WHITE and off.armed() == D.TINT_WHITE


def test_fort_condor_sites_are_the_scene_calls():
    """The two displaced words are the calls to x86 0x660EC0 and 0x60218E
    inside 0x5F5828, and each cave still calls exactly that function."""
    text = nxmap.Main(MODULE).text
    for _n, site, orig, _w in D.CONDOR_SITES:
        assert struct.unpack_from('<I', text, site)[0] == orig
    assert D.CONDOR_BL_ORIG[D.CONDOR_ON_HOOK] == D.A.bl(D.CONDOR_ON_HOOK,
                                                        D.CONDOR_ON_CALLEE)
    assert D.CONDOR_BL_ORIG[D.CONDOR_OFF_HOOK] == D.A.bl(D.CONDOR_OFF_HOOK,
                                                         D.CONDOR_OFF_CALLEE)
    m = nxmap.Main(MODULE)
    assert m.x86_to_arm[0x660EC0] == D.CONDOR_ON_CALLEE
    assert m.x86_to_arm[0x60218E] == D.CONDOR_OFF_CALLEE
    assert m.extent(m.containing(0x5F5828)[0])[0] <= D.CONDOR_ON_HOOK \
        < D.CONDOR_OFF_HOOK < m.extent(m.containing(0x5F5828)[0])[1]


def test_fort_condor_can_be_left_out():
    assert not D.condor_enabled({D.CONDOR_ENV: '0'})
    assert D.condor_enabled({})
    assert D.all_sites(False) == D.SITES


def test_the_caves_live_in_dead_space_and_need_nothing_from_the_pool():
    """BUILD 614b, log 381: `NoRoom: no 983040-byte window holds 172 usable
    word(s)` left the whole cycle out. The runtime now lives in its own
    dead-space part, so even with the padding pool EMPTY every cave lands
    inside the 'daynight' part and the hooks point at them."""
    import tempfile
    import ff7nx_deadspace as DS
    orig = ff7nx_cave.HolePool.__init__

    def empty(self, img, holes=None, starts=None, named=None):
        orig(self, img, holes=[], starts=starts, named=named)
    ff7nx_cave.HolePool.__init__ = empty
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'main')
            bitmap = bytearray(D.BITMAP_BYTES)
            bitmap[151 >> 3] |= 1 << (151 & 7)
            rep = D.apply_to_nso(MODULE, out, bytes(bitmap), stock=MODULE,
                                 sky_rows=SKY_ROWS, world_sphere=True)
    finally:
        ff7nx_cave.HolePool.__init__ = orig
    lo, hi = DS.part(MODULE, 'daynight')
    assert 'dead-space' in rep['caves_from'], rep['caves_from']
    for name, entry in rep['entries'].items():
        assert lo <= entry < hi, (name, hex(entry))
    assert rep['cave_words'] > 500



# ------------------------------------------------ BUILD 617: the snowboard
SNOW_RESUME = {n: h + 4 for n, h, _o, _w in D.SNOW_SITES}


def _snow_world(story, **kw):
    w = World(mode=D.DRIVER_SNOW, **kw)
    w.mem.setu(w.host(D.G_SNOW_STORY), story, 4)
    return w


def test_snowboard_story_run_ticks_and_brackets_the_scene_only():
    w = _snow_world(1, options=0x03, hours=2)          # outdoor bit CLEAR
    assert w.run('flip') == D.FLIP_RESUME
    night = TINT(D.ULTRA_NIGHT_RGB)
    packed = TINT_PACK(D.NIGHT_RGB)
    assert w.armed() == packed and w.tint() == D.TINT_WHITE
    assert _mini_bg(w) == TINT_PACK(D.ULTRA_NIGHT_RGB)
    assert w.run('snow_on') == SNOW_RESUME['snow_on']
    assert w.rgb() == night                     # sky, mountains, terrain
    assert w.run('snow_off') == SNOW_RESUME['snow_off']
    assert w.tint() == D.TINT_WHITE             # HUD, result screens
    assert w.armed() == packed
    w = _snow_world(1, options=0x07, hours=9, minutes=0)
    for _ in range(40):
        assert w.run('flip') == D.FLIP_RESUME
    assert w.minute_of_day() == 9 * 60 + 1, w.clock()


CTX = 0x48000000                              # host ctx struct
GUEST_ESP = 0x0019F000


def _clear_args(w):
    w.mem.setu(D.GUEST_CTX_PAGE + D.GUEST_CTX_OFF, CTX, 8)
    w.mem.setu(CTX + D.GUEST_ESP, GUEST_ESP, 4)
    one = struct.unpack('<I', struct.pack('<f', 1.0))[0]
    for off in (4, 8, 12):
        w.mem.setu(w.host(GUEST_ESP + off), one, 4)
    w.mem.setu(w.host(GUEST_ESP + 16), 0, 4)
    w.mem.setu(w.host(GUEST_ESP), 0xDEADBEEF, 4)


def _clear_rgba(w):
    return tuple(struct.unpack('<f', struct.pack(
        '<I', w.mem.u(w.host(GUEST_ESP + off), 4)))[0]
        for off in (4, 8, 12, 16))


def test_snowboard_clear_colour_follows_the_armed_tint():
    w = _snow_world(1, options=0x03, hours=2)
    assert w.run('flip') == D.FLIP_RESUME
    r, g, b = TINT(D.ULTRA_NIGHT_RGB)
    _clear_args(w)
    assert w.run('snow_clear') == D.SNOW_CLEAR_HOOK + 4
    got = _clear_rgba(w)
    for v, want in zip(got, (r, g, b)):
        assert abs(v - want / 255.0) < 1e-5, (got, (r, g, b))
    assert got[3] == 0.0
    assert w.mem.u(w.host(GUEST_ESP), 4) == 0xDEADBEEF
    assert w.tint() == D.TINT_WHITE             # the bracket is not touched


@pytest.mark.parametrize('story,hours', [(0, 2), (1, 12)])
def test_snowboard_clear_colour_stays_white_unarmed(story, hours):
    w = _snow_world(story, options=0x07, hours=hours)
    assert w.run('flip') == D.FLIP_RESUME
    _clear_args(w)
    assert w.run('snow_clear') == D.SNOW_CLEAR_HOOK + 4
    if story == 0:
        assert _clear_rgba(w) == (1.0, 1.0, 1.0, 0.0)
    else:                                       # noon: armed, near white
        assert all(v > 0.9 for v in _clear_rgba(w)[:3])


def test_snowboard_arcade_is_never_tinted_and_does_not_tick():
    w = _snow_world(0, options=0x07, hours=2, minutes=0)
    for _ in range(40):
        assert w.run('flip') == D.FLIP_RESUME
    assert w.armed() == D.TINT_WHITE and w.tint() == D.TINT_WHITE
    assert w.run('snow_on') == SNOW_RESUME['snow_on']
    assert w.tint() == D.TINT_WHITE
    assert w.minute_of_day() == 2 * 60, w.clock()


def test_snowboard_sites_are_the_scene_calls():
    m = nxmap.Main(MODULE)
    text = m.text
    for _n, site, orig, _w in D.SNOW_SITES:
        assert struct.unpack_from('<I', text, site)[0] == orig
    assert D.SNOW_BL_ORIG[D.SNOW_ON_HOOK] == D.A.bl(D.SNOW_ON_HOOK,
                                                    D.SNOW_ON_CALLEE)
    assert D.SNOW_BL_ORIG[D.SNOW_OFF_HOOK] == D.A.bl(D.SNOW_OFF_HOOK,
                                                     D.SNOW_OFF_CALLEE)
    assert m.x86_to_arm[0x72DAF0] == D.SNOW_ON_CALLEE
    assert m.x86_to_arm[0x72D0C0] == D.SNOW_SCENE
    assert m.x86_to_arm[0x72D661] == D.SNOW_OFF_CALLEE
    assert D.SNOW_BL_ORIG[D.SNOW_SCENE_HOOK] == D.A.bl(D.SNOW_SCENE_HOOK,
                                                       D.SNOW_SCENE)
    assert D.SNOW_SCENE < D.SNOW_OFF_HOOK < D.SNOW_SCENE_END
    assert m.x86_to_arm[0x72381C] == D.SNOW_LOOP
    D.check_snow_sites(text)


def test_snowboard_can_be_left_out():
    assert not D.snow_enabled({D.SNOW_ENV: '0'})
    assert D.snow_enabled({})
    assert D.all_sites(True, False) == D.SITES + D.CONDOR_SITES


# BUILD 617c: the snowboard HUD -- screen quads (z 0, rhw 1) are white
_ONE = 0x3F800000
VBUF = HOST_SP + 0x4000


def _snow_draw(w, ortho, z=0, rhw=_ONE):
    w.fill_block()
    w.mem.setu(BLOCK + D.MAT_34, 0 if ortho else _ONE, 4)
    w.mem.setu(BLOCK + D.MAT_44, _ONE if ortho else 0, 4)
    for k in range(4):                       # a quad, 32-byte TLVERTEX
        w.mem.setu(VBUF + 32 * k + 8, z, 4)
        w.mem.setu(VBUF + 32 * k + 12, rhw, 4)
    w.cpu.x[D.VERTS_REG] = VBUF


def _lane(w):
    assert w.submit() == D.BLOCK_RESUME
    return w.block_word(D.TINT_LANE) & 0xFFFFFF


def test_snowboard_hud_quads_are_white_everything_else_tinted():
    w = _snow_world(1, options=0x03, hours=2)
    assert w.run('flip') == D.FLIP_RESUME
    night = TINT(D.ULTRA_NIGHT_RGB)
    packed = night[0] << 16 | night[1] << 8 | night[2]
    assert w.run('snow_on') == SNOW_RESUME['snow_on']
    assert w.mem.u(w.bss + D.BSS_SNOW_SKY, 4) == 0      # cleared on return
    assert w.tint() == packed
    _snow_draw(w, ortho=True)                           # TIME / SPEED
    assert _lane(w) == D.TINT_WHITE
    _snow_draw(w, ortho=True, z=0x3F000000, rhw=0x3F4CCCCD)   # a sprite
    assert _lane(w) == packed
    _snow_draw(w, ortho=True, z=0, rhw=0x3F800001)      # rhw not exactly 1
    assert _lane(w) == packed
    _snow_draw(w, ortho=False)                          # course, models
    assert _lane(w) == packed
    w.mem.setu(w.bss + D.BSS_SNOW_SKY, 1, 4)            # the mountains
    _snow_draw(w, ortho=True)
    assert _lane(w) == packed
    w.mem.setu(w.bss + D.BSS_SNOW_SKY, 0, 4)
    _snow_draw(w, ortho=True)
    w.cpu.x[D.VERTS_REG] = 0                            # no vertex array
    assert _lane(w) == packed


def test_hud_gate_is_snowboard_only():
    w = World(options=0x05, hours=2)                    # an outdoor field
    w.run()
    tint = w.tint()
    assert tint != D.TINT_WHITE
    _snow_draw(w, ortho=True)
    assert _lane(w) == tint


def test_the_window_sky_follows_its_own_rows_all_day():
    """BUILD 649: the sky slot is the sky's own day (night, sunrise, sunset
    looks) through the same gamma table, on every 5th minute; the room
    stays white."""
    field = SKY_ROWS[0][0]
    for m in range(0, D.DAY_MINUTES, 5):
        w = World(options=0x05, hours=m // 60, minutes=m % 60, field_id=field)
        w.run()
        assert w.sky_rgb() == TINT(D.sky_phase_colour(m)), m
        assert w.tint() == D.TINT_WHITE, m


def test_the_window_sky_rows_meet_the_clock_by_day():
    """Between the sunrise's end and the sunset's start the sky's rows are
    the clock's own colour, so the window only differs around dawn, dusk
    and night."""
    for m in range(D.SKY_SUNRISE_END_MIN, D.SKY_SUNSET_START_MIN + 1):
        a, b = D.sky_phase_colour(m), D.phase_colour(m)
        assert max(abs(x - y) for x, y in zip(a, b)) <= 1, (m, a, b)
    assert D.sky_phase_colour(3 * 60) == D.SKY_NIGHT_RGB
    assert D.sky_phase_colour(7 * 60) == D.SKY_SUNRISE_RGB
    assert D.sky_phase_colour(19 * 60) == D.SKY_SUNSET_RGB


def test_ultra_bright_fields_take_the_deeper_night():
    """BUILD 650: an ultra-bright outdoor field follows ultra_phases (same
    clock, deeper night) on every 5th minute; an ordinary outdoor field
    keeps the plain phases."""
    for m in range(0, D.DAY_MINUTES, 5):
        w = World(options=0x05, hours=m // 60, minutes=m % 60,
                  field_id=ULTRA_TEST_IDS[0])
        w.run()
        # 650b: models keep the ordinary night; the background pages (the
        # sky slot, bracket open over every page) take the deeper one
        assert w.rgb() == TINT(D.phase_colour(m)), m
        assert w.sky_rgb() == TINT(D.ultra_phase_colour(m)), m
        assert w.mem.u(w.bss + D.BSS_SKY_FIRST, 4) == 0
        assert w.mem.u(w.bss + D.BSS_SKY_LAST, 4) == 0xFFFF
        o = World(options=0x05, hours=m // 60, minutes=m % 60, field_id=151)
        o.run()
        assert o.rgb() == TINT(D.phase_colour(m)), m


def test_ultra_rows_only_differ_at_night():
    for m in range(D.DAY_MINUTES):
        a, b = D.ultra_phase_colour(m), D.phase_colour(m)
        if D.SUNRISE_MIN + 60 <= m < D.AFTERNOON_MIN:
            assert a == b, m
    assert D.ultra_phase_colour(2 * 60) == D.ULTRA_NIGHT_RGB
    assert TINT(D.ULTRA_NIGHT_RGB) == (67, 67, 102)


def test_ultra_ids_come_from_the_maplist():
    ml = ['x'] * 800
    ml[343], ml[77], ml[771] = 'farm', 'HILL', 'hill2'
    assert D.ultra_ids(ml) == (77, 343, 771)


def test_ultra_field_background_pages_take_the_deeper_night_models_do_not():
    """650b, end to end through the page bracket and the block hook: in an
    ultra-bright field at 02:00 every background page draws with the deeper
    night, and a draw outside the page loop (a model) with the ordinary."""
    probe = _Probe(D.BSS_SKY_ACTIVE)
    w = World(options=0x05, hours=2, field_id=ULTRA_TEST_IDS[0])
    w.run()
    for page in (0, 1, 7, 30):
        saw = probe.run(w, page)
        w.mem.setu(w.bss + D.BSS_SKY_ACTIVE, saw, 4)
        w.fill_block()
        w.submit()
        assert _as_the_shader_reads_it(w.block_word(D.TINT_LANE)) == \
            TINT(D.ULTRA_NIGHT_RGB), page
    w.mem.setu(w.bss + D.BSS_SKY_ACTIVE, 0, 4)
    w.fill_block()
    w.submit()
    assert _as_the_shader_reads_it(w.block_word(D.TINT_LANE)) == \
        TINT(D.NIGHT_RGB)
    # leaving for an ordinary field closes the range again
    o = World(options=0x05, hours=2, field_id=151)
    o.run()
    assert o.mem.u(o.bss + D.BSS_SKY_LAST, 4) == 0


def _def_world(**kw):
    w = World(options=0x05, hours=2, field_id=151, **kw)
    w.run()
    return w


def test_deferred_draw_keeps_the_tint_it_was_queued_with():
    """652: an entry queued while tinted draws tinted even after the
    gray-quads point put the tint back to white; one queued after it (the
    UI) draws white; the slots come back after each draw."""
    w = _def_world()
    night = TINT(D.NIGHT_RGB)
    pack = lambda c: c[0] << 16 | c[1] << 8 | c[2]          # noqa: E731
    w.cpu.x[20] = 0x12345670
    assert w.run('def_alloc') == D.DEF_ALLOC_RESUME
    w.run('clear')                                         # tint -> white
    assert w.tint() == D.TINT_WHITE
    w.cpu.x[20] = 0x12345700
    w.run('def_alloc')                                     # a UI entry
    for entry, want in ((0x12345670, pack(night)), (0x12345700, D.TINT_WHITE)):
        w.cpu.x[21] = entry
        assert w.run('def_draw') == D.DEF_DRAW_RESUME
        assert w.tint() == want, hex(entry)
        assert w.run('def_exit') == D.DEF_EXIT_RESUME
        assert w.tint() == D.TINT_WHITE


def test_780_reused_entry_address_takes_this_frames_tint():
    """780: gfx_drv reuses deferred entries' host addresses. A stale slot
    with the same address, queued white in an earlier frame and never drawn,
    must not win over this frame's tinted allocation."""
    w = _def_world()
    night = TINT(D.NIGHT_RGB)
    pack = lambda c: c[0] << 16 | c[1] << 8 | c[2]          # noqa: E731
    # an earlier frame: B tinted, then (after the UI point) A white; only B
    # is drawn, so the old search would resume right before the stale A
    w.cpu.x[20] = 0x0000B000
    w.run('def_alloc')
    w.run('clear')
    w.cpu.x[20] = 0x0000A000
    w.run('def_alloc')
    w.cpu.x[21] = 0x0000B000
    w.run('def_draw')
    w.run('def_exit')
    # this frame: armed again, A queued tinted, then drawn
    w.mem.setu(w.bss + D.BSS_TINT_INV, ~pack(night) & 0xFFFFFFFF, 4)
    w.cpu.x[20] = 0x0000A000
    w.run('def_alloc')
    w.run('clear')
    w.cpu.x[21] = 0x0000A000
    assert w.run('def_draw') == D.DEF_DRAW_RESUME
    assert w.tint() == pack(night)
    w.run('def_exit')
    assert w.tint() == D.TINT_WHITE


def test_deferred_entry_in_a_sky_page_takes_the_sky_tint():
    w = _def_world()
    w.mem.setu(w.bss + D.BSS_SKY_INV, ~0x112233 & 0xFFFFFFFF, 4)
    w.mem.setu(w.bss + D.BSS_SKY_ACTIVE, 1, 4)
    w.cpu.x[20] = 0x55550000
    w.run('def_alloc')
    w.mem.setu(w.bss + D.BSS_SKY_ACTIVE, 0, 4)
    w.cpu.x[21] = 0x55550000
    w.run('def_draw')
    assert w.tint() == 0x112233
    assert w.mem.u(w.bss + D.BSS_SKY_ACTIVE, 4) == 0
    w.run('def_exit')
    assert w.tint() == TINT_PACK(D.NIGHT_RGB)


def test_deferred_entry_the_ring_lost_draws_as_stock():
    w = _def_world()
    w.run('clear')
    w.cpu.x[21] = 0xDEAD0000
    w.run('def_draw')
    assert w.tint() == D.TINT_WHITE
    w.run('def_exit')
    assert w.tint() == D.TINT_WHITE


# ------------------------------- BUILD 661: minigames, background-only night
def test_minigame_flip_decides_both_colours_every_10th_minute():
    """The ordinary night goes to ARMED (units, Cloud, the models), the
    deeper one to the background slot -- on the whole clock, both games."""
    for m in range(0, D.DAY_MINUTES, 10):
        for mode, story in ((D.DRIVER_CONDOR, 0), (D.DRIVER_SNOW, 1)):
            w = World(options=0x03, hours=m // 60, minutes=m % 60, mode=mode)
            w.mem.setu(w.host(D.G_SNOW_STORY), story, 4)
            assert w.run('flip') == D.FLIP_RESUME
            mm = w.minute_of_day()
            assert w.armed() == TINT_PACK(D.phase_colour(mm)), (mode, m)
            assert _mini_bg(w) == TINT_PACK(D.ultra_phase_colour(mm)), (
                mode, m)
            assert w.tint() == D.TINT_WHITE


def test_minigame_background_slot_goes_white_with_armed():
    """Arcade run, a disabled cycle, and a field frame (the frame before a
    minigame starts) all leave the background slot white, as ARMED."""
    w = _snow_world(0, options=0x07, hours=2)
    w.mem.setu(w.bss + D.BSS_EXT + D.EXT_MINI_BG_INV, 0x1234, 4)
    w.run('flip')
    assert _mini_bg(w) == D.TINT_WHITE and w.armed() == D.TINT_WHITE
    w = World(options=0x00, hours=2, mode=D.DRIVER_CONDOR)
    w.mem.setu(w.bss + D.BSS_EXT + D.EXT_MINI_BG_INV, 0x1234, 4)
    w.run('flip')
    assert _mini_bg(w) == D.TINT_WHITE
    w = World(options=0x07, hours=2, mode=D.DRIVER_FIELD)
    w.mem.setu(w.bss + D.BSS_EXT + D.EXT_MINI_BG_INV, 0x1234, 4)
    w.mem.setu(w.bss + D.BSS_TICKED, 1, 4)
    w.run('flip')
    assert _mini_bg(w) == D.TINT_WHITE


def test_fort_condor_units_draw_with_the_ordinary_night_through_the_block():
    """End to end through the block hook: a map page drawn after condor_on
    reaches the shader with the deeper night, a unit drawn after
    condor_units with the ordinary night."""
    w = World(options=0x07, hours=2, mode=D.DRIVER_CONDOR)
    w.run('flip')
    w.run('condor_on')
    w.fill_block()
    w.submit()
    assert _as_the_shader_reads_it(w.block_word(D.TINT_LANE)) == \
        TINT(D.ULTRA_NIGHT_RGB)
    w.run('condor_units')
    w.fill_block()
    w.submit()
    assert _as_the_shader_reads_it(w.block_word(D.TINT_LANE)) == \
        TINT(D.NIGHT_RGB)


def test_fort_condor_deferred_map_page_keeps_the_deeper_night():
    """652 + 661: a see-through map tile queued inside the map loop draws
    later -- after the units switched to the ordinary night -- with the
    deeper night it was queued with."""
    w = World(options=0x07, hours=2, mode=D.DRIVER_CONDOR)
    w.run('flip')
    w.run('condor_on')
    w.cpu.x[20] = 0x24680000
    assert w.run('def_alloc') == D.DEF_ALLOC_RESUME
    w.run('condor_units')
    w.cpu.x[20] = 0x24680100
    w.run('def_alloc')
    w.run('condor_off')
    for entry, want in ((0x24680000, TINT_PACK(D.ULTRA_NIGHT_RGB)),
                        (0x24680100, TINT_PACK(D.NIGHT_RGB))):
        w.cpu.x[21] = entry
        assert w.run('def_draw') == D.DEF_DRAW_RESUME
        assert w.tint() == want, hex(entry)
        w.run('def_exit')


def test_fort_condor_units_site_is_the_one_units_call():
    m = nxmap.Main(MODULE)
    assert m.x86_to_arm[0x6019E7] == D.CONDOR_UNITS_CALLEE
    assert D.CONDOR_BL_ORIG[D.CONDOR_UNITS_HOOK] == D.A.bl(
        D.CONDOR_UNITS_HOOK, D.CONDOR_UNITS_CALLEE)
    assert struct.unpack_from('<I', m.text, D.CONDOR_UNITS_HOOK)[0] == \
        D.CONDOR_BL_ORIG[D.CONDOR_UNITS_HOOK]
    assert m.extent(0x5F5828) == (D.CONDOR_FN, D.CONDOR_FN_END)
    D.check_condor_sites(m.text)


def test_snowboard_model_sites_are_the_queue_calls():
    m = nxmap.Main(MODULE)
    assert m.x86_to_arm[0x735220] == D.SNOW_MODELS_CALLEE
    assert m.x86_to_arm[0x735332] == D.SNOW_ENTRY_CALLEE
    assert m.x86_to_arm[0x726560] == D.SNOW_COURSE_FN
    assert m.x86_to_arm[0x7272DD] == D.SNOW_COURSE_CALLEE
    assert m.extent(0x735220) == (D.SNOW_MODELS_CALLEE, D.SNOW_MODELS_END)
    assert m.extent(0x726560) == (D.SNOW_COURSE_FN, D.SNOW_COURSE_FN_END)
    for hook, callee in ((D.SNOW_MODELS_HOOK, D.SNOW_MODELS_CALLEE),
                         (D.SNOW_ENTRY_HOOK, D.SNOW_ENTRY_CALLEE),
                         (D.SNOW_COURSE_HOOK, D.SNOW_COURSE_CALLEE)):
        assert D.SNOW_BL_ORIG[hook] == D.A.bl(hook, callee)
    D.check_snow_sites(m.text)


# A fake 0x735220 laid out AROUND the real per-entry hook word, so its loop
# runs through the snow_entry cave exactly as the recompiled loop does, and
# a fake 0x735332 that logs (entry, the tint a draw would read now).
LOG = HOST_SP + 0x8000
SNOW_GUEST_ESP = 0x0019E000


def _ctx_esp(w):
    return w.mem.u(CTX + D.GUEST_ESP, 4)


def _snow_queue_code(w):
    import a64 as _A

    class _Em:
        def __init__(s, at):
            s.at, s.words = at, []

        def emit(s, word):
            s.words.append(word)

        def pc(s):
            return s.at + 4 * len(s.words)

    code = dict(w.code)
    code[D.SNOW_ENTRY_HOOK] = w.placed[D.SNOW_ENTRY_HOOK]

    def esp_add(e, delta):
        e.emit(_A.adrp(9, e.pc(), D.GUEST_CTX_PAGE))
        e.emit(_A.ldr64(9, 9, D.GUEST_CTX_OFF))
        e.emit(_A.ldr(10, 9, D.GUEST_ESP))
        e.emit(_A.add_imm(10, 10, delta) if delta > 0
               else _A.sub_imm(10, 10, -delta))
        e.emit(_A.str_(10, 9, D.GUEST_ESP))

    # body before the hook: prologue, count, loop head, guest push --
    # 16 words, so the push ends exactly at the hook word
    start = D.SNOW_ENTRY_HOOK - 4 * 16
    e = _Em(start)
    e.emit(_A.stp64_pre(29, 30, 31, -48))
    e.emit(_A.stp64_off(19, 20, 31, 16))
    e.emit(_A.stp64_off(21, 22, 31, 32))
    D.AC.mov32(e, 0, D.G_SNOW_QCOUNT)
    e.emit(_A.bl(e.pc(), D.GUEST_TRANSLATE))
    e.emit(_A.mov_reg64(19, 0))
    e.emit(_A.ldrh(20, 19, 0))
    e.emit(_A.movz(21, 0))                              # entry index
    loop = e.pc()
    e.emit(_A.cmp_reg(21, 20))
    hs_at = len(e.words)
    e.emit(0)                                           # b.hs end, below
    esp_add(e, -4)                                      # the call's push
    assert e.pc() == D.SNOW_ENTRY_HOOK, 'layout'
    after = _Em(D.SNOW_ENTRY_HOOK + 4)
    after.emit(_A.add_imm(21, 21, 1))
    after.emit(_A.b(after.pc(), loop))
    end = after.pc()
    after.emit(_A.strh(31, 19, 0))
    esp_add(after, 4)                                   # its own `ret`
    after.emit(_A.ldp64_off(21, 22, 31, 32))
    after.emit(_A.ldp64_off(19, 20, 31, 16))
    after.emit(_A.ldp64_post(29, 30, 31, 48))
    after.emit(_A.ret())
    e.words[hs_at] = _A.bcond(start + 4 * hs_at, end, D.HS)
    code.update({start + 4 * i: wd for i, wd in enumerate(e.words)})
    code.update({after.at + 4 * i: wd for i, wd in enumerate(after.words)})
    code[D.SNOW_MODELS_CALLEE] = _A.b(D.SNOW_MODELS_CALLEE, start)

    # fake 0x735332: log (x21 = entry, TINT) and pop its return slot
    lg = _Em(D.SNOW_ENTRY_CALLEE)
    D.AC.bss_ptr(lg, 11, LOG)
    lg.emit(_A.ldr(12, 11, 0))                          # n
    lg.emit(_A.add_imm(13, 12, 1))
    lg.emit(_A.str_(13, 11, 0))
    lg.emit(_A.lsl(12, 12, 3))
    lg.emit(_A.add_reg64(12, 11, 12))
    lg.emit(_A.str_(21, 12, 8))
    D.AC.bss_ptr(lg, 13, w.bss)
    lg.emit(_A.ldr(14, 13, D.BSS_TINT_INV))
    lg.emit(_A.str_(14, 12, 12))
    esp_add(lg, 4)
    lg.emit(_A.ret())
    code.update({lg.at + 4 * i: wd for i, wd in enumerate(lg.words)})

    # fake 0x7272DD: queue three course entries
    cq = _Em(D.SNOW_COURSE_CALLEE)
    cq.emit(_A.stp64_pre(29, 30, 31, -16))
    D.AC.mov32(cq, 0, D.G_SNOW_QCOUNT)
    cq.emit(_A.bl(cq.pc(), D.GUEST_TRANSLATE))
    cq.emit(_A.ldrh(9, 0, 0))
    cq.emit(_A.add_imm(9, 9, 3))
    cq.emit(_A.strh(9, 0, 0))
    esp_add(cq, 4)
    cq.emit(_A.ldp64_post(29, 30, 31, 16))
    cq.emit(_A.ret())
    code.update({cq.at + 4 * i: wd for i, wd in enumerate(cq.words)})
    return code


def _snow_frame(hours, story=1, before=2, after=2):
    """Queue `before` entries, then 0x726560's course call (3 entries),
    then `after` more, and draw the queue through snow_models."""
    w = _snow_world(story, options=0x07, hours=hours)
    w.run('flip')
    w.run('snow_on')
    w.mem.setu(D.GUEST_CTX_PAGE + D.GUEST_CTX_OFF, CTX, 8)
    w.mem.setu(LOG, 0, 4)
    code = _snow_queue_code(w)
    count = w.host(D.G_SNOW_QCOUNT)
    w.mem.setu(count, before, 2)
    w.mem.setu(CTX + D.GUEST_ESP, SNOW_GUEST_ESP - 4, 4)   # caller pushed
    w.cpu.sp = HOST_SP
    assert w.cpu.run(w.entries['snow_course'], code,
                     max_steps=20000) == D.SNOW_COURSE_HOOK + 4
    assert _ctx_esp(w) == SNOW_GUEST_ESP
    assert w.mem.u(count, 2) == before + 3
    w.mem.setu(count, before + 3 + after, 2)
    w.mem.setu(CTX + D.GUEST_ESP, SNOW_GUEST_ESP - 4, 4)
    marks = [w.mem.u(w.bss + D.BSS_EXT + D.EXT_SNOW_MAP + i, 1)
             for i in range(before + 3 + after)]
    w.cpu.sp = HOST_SP
    for r in range(19, 29):
        w.cpu.x[r] = 0x5500000000000000 + r
    assert w.cpu.run(w.entries['snow_models'], code,
                     max_steps=200000) == D.SNOW_MODELS_HOOK + 4
    for r in range(19, 29):
        assert w.cpu.x[r] == 0x5500000000000000 + r, r
    assert w.cpu.sp == HOST_SP
    assert _ctx_esp(w) == SNOW_GUEST_ESP                 # one ret, net
    assert w.mem.u(count, 2) == 0
    n = w.mem.u(LOG, 4)
    log = [(w.mem.u(LOG + 8 + 8 * i, 4),
            ~w.mem.u(LOG + 12 + 8 * i, 4) & 0xFFFFFF) for i in range(n)]
    return w, marks, log


def test_snowboard_course_models_take_the_background_the_rest_armed():
    """02:00 on the story run: the three course-block models 0x7272DD
    queued draw in pass 1 with the deeper night, the other four (Cloud, the
    board, placed objects) in pass 2 with the ordinary night -- each
    exactly once; the guest stack nets one return; the map is cleared and
    0x72D0C0's lists get the background colour back."""
    w, marks, log = _snow_frame(2)
    assert marks == [0, 0, 1, 1, 1, 0, 0]
    deep, night = TINT_PACK(D.ULTRA_NIGHT_RGB), TINT_PACK(D.NIGHT_RGB)
    assert log == [(2, deep), (3, deep), (4, deep),
                   (0, night), (1, night), (5, night), (6, night)], log
    assert w.tint() == deep
    assert w.mem.u(w.bss + D.BSS_EXT + D.EXT_SNOW_PASS, 4) == 0
    assert all(w.mem.u(w.bss + D.BSS_EXT + D.EXT_SNOW_MAP + i, 1) == 0
               for i in range(D.SNOW_QUEUE_MAX))


@pytest.mark.parametrize('story,hours', [(1, 12), (0, 2)])
def test_snowboard_equal_colours_draw_the_queue_once_in_order(story, hours):
    """Noon on the story run and the arcade at night: the two colours are
    equal, so 0x735220 is called once, every entry in queue order."""
    w, _marks, log = _snow_frame(hours, story=story)
    assert [i for i, _t in log] == list(range(7))
    assert len({t for _i, t in log}) == 1
    assert all(w.mem.u(w.bss + D.BSS_EXT + D.EXT_SNOW_MAP + i, 1) == 0
               for i in range(D.SNOW_QUEUE_MAX))


def test_snowboard_queue_entries_past_the_map_are_not_course():
    """A queue longer than the map (it cannot be, stock caps it at 128):
    course marks clamp at 128 and everything past them draws in pass 2."""
    w, marks, log = _snow_frame(2, before=126, after=3)
    assert marks[126:128] == [1, 1] and marks[128:] == [0] * 4
    deep = TINT_PACK(D.ULTRA_NIGHT_RGB)
    assert [i for i, t in log if t == deep] == [126, 127]
    assert sorted(i for i, _t in log) == list(range(132))
