#!/usr/bin/env python3
"""
tests/test_facial.py -- execute the facial-animation caves.

The caves are built at their REAL addresses, out of the real padding-hole
allocator, and then interpreted word for word by `arm64emu` with `paged=True`
-- so the translator behaves the way the module's does, a pointer indexed past
a 4 KB boundary lands somewhere obviously wrong instead of getting away with
it, and every guest call goes through a stub that reads the cdecl frame the
cave actually built rather than one the test assumed.

What each check is for is written on it. The two that matter most:

  * `test_steady_state_makes_no_guest_call` is the framerate case. This cave
    runs once per model per frame -- 1,920 times a second with a full field at
    60 FPS -- and the whole design rests on the second and every later frame
    doing nothing but compare bytes. If a refactor ever puts a texture load
    back on that path, this fails.
  * `test_teardown_restores_and_frees_exactly_once` is the ownership case. A
    pointer freed twice or dropped is the failure that would not show up for
    an hour of play.

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
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import capstone                                               # noqa: E402

import a64 as A                                               # noqa: E402
import arm64emu                                               # noqa: E402
import ff7nx_cave                                             # noqa: E402
import ff7nx_facial as F                                      # noqa: E402
import ff7nx_tables                                           # noqa: E402
import nxmap                                                  # noqa: E402

MODULE = os.path.join(ROOT, 'dump', 'exefs', 'main')

GUEST_STACK = 0x00C00000                 # somewhere with room below it
GUEST_ANIM_BASE = 0x02000000
GUEST_MODELS = 0x02400000
GUEST_CODE = 0x02800000
HOST_SP = 0x50000000

# Fake texture handles the stubs hand back. Distinct and recognisable so a
# pointer that ends up in the wrong slot is obvious in a failure message.
TEX_L = 0x0AA00001
TEX_R = 0x0AA00002
TEX_M = 0x0AA00003


# --------------------------------------------------------------- the layout
_LAYOUT = {}
VOICE_BSS = 0x03F00000                   # stands in for ff7nx_voice's block


def layout(voice_bss=None):
    """Build the caves once per configuration, at their real addresses."""
    if voice_bss not in _LAYOUT:
        blob = open(MODULE, 'rb').read()
        segs, _raw, own = ff7nx_tables.open_module(blob)
        text = own.text
        for _n, site, orig, what in F.sites(voice_bss):
            got = struct.unpack_from('<I', text, site)[0]
            assert got == orig, ('%s: module has %08X, expected %08X'
                                 % (what, got, orig))
        bss = F.AC.scratch_base(blob, segs)
        pool = ff7nx_cave.HolePool(text,
                                   starts=set(nxmap.Main(MODULE).arm_starts))
        entries, placed = F.build_all(pool, bss, voice_bss=voice_bss,
                                      flap_shift=1)
        hooks = {s for _n, s, _o, _w in F.sites(voice_bss)}
        code = {va: w for va, w in placed.items() if va not in hooks}
        _LAYOUT[voice_bss] = (entries, placed, code, bss)
    return _LAYOUT[voice_bss]


class World:
    """Guest memory, the register file, and stubs for the four guest calls."""

    def __init__(self, archive=(), eye_idx=3, model_id=1, model_name='AAAA',
                 modes=(2, 2), voice_bss=None):
        entries, placed, code, bss = layout(voice_bss)
        self.voice_bss = voice_bss
        self.entries, self.placed, self.code, self.bss = entries, placed, \
            code, bss
        self.archive = {n.lower() for n in archive}
        self.model_id = model_id
        self.mem = arm64emu.Mem()
        self.unloaded = []
        self.loaded = []
        self.names = []
        self.cpu = arm64emu.Cpu(self.mem, paged=True, native={
            F.LOAD_EYE_TEX: self._load_eye_tex,
            F.LOAD_MODEL_TEX: self._load_model_tex,
            F.UNLOAD_MODEL_TEX: self._unload_model_tex,
            F.CREATE_STRUC3: self._create_struc3,
        })
        self.cpu.sp = HOST_SP

        # the guest register file, reached through adrp 0x12CE000 + 0x2B0
        self.ctx = 0x30000000
        self.mem.setu(0x12CE2B0, self.ctx, 8)

        self.anim = GUEST_ANIM_BASE + model_id * F.G_ANIM_STRIDE
        self.gset32(F.G_ANIM_PTR, GUEST_ANIM_BASE)
        self.gset32(F.G_MODELS_DATA, GUEST_MODELS)
        self.gset8(self.anim + F.A_EYE_IDX, eye_idx)
        self.gset32(self.anim + F.A_CUSTOM_L, 0x11110001)
        self.gset32(self.anim + F.A_STATIC_L, 0x11110002)
        self.gset32(self.anim + F.A_CUSTOM_R, 0x11110003)
        self.gset32(self.anim + F.A_STATIC_R, 0x11110004)
        self.stock = (0x11110001, 0x11110002, 0x11110003, 0x11110004)

        name = (GUEST_MODELS + model_id * F.G_MODELS_STRIDE
                + F.G_MODEL_NAME_OFF)
        self.gstr(name, model_name + '.HRC')

        # the nine-entry eye buffer: only the set under test is populated
        self.gen = {3: ('b_eye2.tim', 'b_eye2r.tim'),
                    0: ('c_eye2.tim', 'c_eye2r.tim')}
        for idx, (left, right) in self.gen.items():
            base = F.G_EYE_BUFFER + idx * F.G_EYE_STRIDE
            lp = 0x02900000 + idx * 0x100
            rp = lp + 0x40
            self.gstr(lp, left)
            self.gstr(rp, right)
            self.gset32(base + 0, 1)
            self.gset32(base + 8, lp)
            self.gset32(base + 16, rp)

        # the blink data the engine passes, and the cdecl frame it sits in
        self.blink = 0x02A00000
        self.gset8(self.blink + 0, modes[0])
        self.gset8(self.blink + 1, modes[1])
        self.gset8(self.blink + 2, 0)
        self.gset8(self.blink + 3, model_id)
        self.esp = GUEST_STACK
        self.gset32(self.esp + 0, 0xDEAD0000)
        self.gset32(self.esp + 4, self.anim)
        self.gset32(self.esp + 8, self.blink)
        self.set_esp(self.esp)

    # ------------------------------------------------------- guest accessors
    def host(self, va):
        return self.cpu.guest_to_host(va)

    def gset32(self, va, val):
        self.mem.setu(self.host(va), val & 0xFFFFFFFF, 4)

    def gget32(self, va):
        return self.mem.u(self.host(va), 4)

    def gset8(self, va, val):
        self.mem.setu(self.host(va), val & 0xFF, 1)

    def gget8(self, va):
        return self.mem.u(self.host(va), 1)

    def gstr(self, va, text):
        for i, ch in enumerate(text.encode('ascii')):
            self.gset8(va + i, ch)
        self.gset8(va + len(text), 0)

    def gcstr(self, va):
        out = bytearray()
        while len(out) < 64:
            ch = self.gget8(va + len(out))
            if not ch:
                break
            out.append(ch)
        return out.decode('latin1')

    def set_esp(self, val):
        self.mem.setu(self.ctx + F.CTX_ESP, val & 0xFFFFFFFF, 4)

    def get_esp(self):
        return self.mem.u(self.ctx + F.CTX_ESP, 4)

    def set_ebp(self, val):
        self.mem.setu(self.ctx + F.CTX_EBP, val & 0xFFFFFFFF, 4)

    def eax(self):
        return self.mem.u(self.ctx, 4)

    # ------------------------------------------------------------ our state
    def slot(self, model=None):
        model = self.model_id if model is None else model
        return self.bss + F.SLOTS_OFF + model * F.SLOT_BYTES

    def slot32(self, off, model=None):
        return self.mem.u(self.slot(model) + off, 4)

    def slot8(self, off, model=None):
        return self.mem.u(self.slot(model) + off, 1)

    def set_slot8(self, off, val, model=None):
        self.mem.setu(self.slot(model) + off, val, 1)

    def set_slot32(self, off, val, model=None):
        self.mem.setu(self.slot(model) + off, val & 0xFFFFFFFF, 4)

    def slot_name(self, model=None):
        base = self.slot(model) + F.SL_NAME
        out = bytearray()
        while len(out) < 12:
            ch = self.mem.u(base + len(out), 1)
            if not ch:
                break
            out.append(ch)
        return out.decode('latin1')

    # ----------------------------------------------------------- call stubs
    def _args(self, n):
        esp = self.get_esp()
        return [self.gget32(esp + 4 * (i + 1)) for i in range(n)]

    def _finish(self, cpu):
        """What a translated callee's epilogue leaves behind."""
        cpu.x[0:19] = [arm64emu.GARBAGE] * 19
        cpu.x[30] = arm64emu.GARBAGE
        self.set_esp(self.get_esp() + 4)

    def _load_eye_tex(self, cpu):
        eye_data, anim = self._args(2)
        left = self.gcstr(self.gget32(eye_data + 8))
        right = self.gcstr(self.gget32(eye_data + 16))
        self.names.append((left, right))
        for off in (F.A_CUSTOM_L, F.A_STATIC_L, F.A_CUSTOM_R, F.A_STATIC_R):
            self.gset32(anim + off, 0)
        if self.gget32(eye_data + 0):
            if self._base(left) in self.archive:
                self.gset32(anim + F.A_STATIC_L, TEX_L)
                self.loaded.append(TEX_L)
            if self._base(right) in self.archive:
                self.gset32(anim + F.A_STATIC_R, TEX_R)
                self.loaded.append(TEX_R)
        self._finish(cpu)

    def _load_model_tex(self, cpu):
        _a, _b, name, info, game = self._args(5)
        text = self.gcstr(name)
        self.names.append(text)
        assert self.gget32(info + F.S3_USE_LGP) == 1, 'use_lgp not set'
        assert self.gget32(info + F.S3_LGP_NUM) == 1, 'lgp_num not set'
        assert self.gget32(info + F.S3_BASE_DIR) == F.G_STRUC3_DIR
        assert self.gget32(info + F.S3_MANGLER) == 0
        assert game == 0xABCDEF01, 'game object argument is %08X' % game
        got = TEX_M if self._base(text) in self.archive else 0
        self._finish(cpu)
        self.mem.setu(self.ctx, got, 4)         # EAX
        if got:
            self.loaded.append(got)

    def _unload_model_tex(self, cpu):
        self.unloaded.append(self._args(1)[0])
        self._finish(cpu)

    def _create_struc3(self, cpu):
        info, = self._args(1)
        for off in range(0, F.S3_BYTES, 4):
            self.gset32(info + off, 0)
        self._finish(cpu)

    @staticmethod
    def _base(name):
        return name.rsplit('.', 1)[0].lower()

    # ---------------------------------------------------------------- drive
    def run(self, which='blink', max_steps=400000):
        entry = self.entries[which]
        return self.cpu.run(entry, self.code, max_steps=max_steps)


# ----------------------------------------------------------------- encoders
def test_every_emitted_word_decodes():
    """An encoding typo produces a valid module that misbehaves. Not here."""
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    for voice in (None, VOICE_BSS):
        _entries, placed, _code, _bss = layout(voice)
        bad = [(va, w) for va, w in placed.items()
               if not list(md.disasm(struct.pack('<I', w), va))]
        assert not bad, 'undecodable: %s' % ['%08X:%08X' % b for b in bad[:8]]


def test_new_encoders_round_trip():
    """The four forms a64.py does not carry, checked by a second decoder."""
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)

    def one(word):
        ins = list(md.disasm(struct.pack('<I', word), 0x1000))
        assert ins, 'word %08X does not decode' % word
        return '%s %s' % (ins[0].mnemonic, ins[0].op_str)

    assert one(F.lsrv(9, 10, 11)) == 'lsr w9, w10, w11'
    assert one(F.lslv(9, 10, 11)) == 'lsl w9, w10, w11'
    assert one(F.orr_imm_bit(1, 1, 5)) == 'orr w1, w1, #0x20'
    assert one(F.orr_reg(9, 10, 11)) == 'orr w9, w10, w11'
    assert one(F.tbz(3, 0, 0x1000, 0x1008)) == 'tbz w3, #0, #0x1008'


def test_lowercasing_leaves_digits_alone():
    """`orr #0x20` is the lowercaser, and HRC ids contain digits."""
    for ch in b'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        assert (ch | 0x20) == ord(chr(ch).lower())
    for ch in b'0123456789':
        assert (ch | 0x20) == ch


# ------------------------------------------------------------- the fast path
def test_first_sight_captures_stock_pointers_and_the_name():
    w = World(archive=['eye_aaaa_2', 'eye_aaaar_2'])
    out = w.run()
    assert out == F.BLINK_RESUME, 'left at 0x%X' % out
    assert w.slot8(F.SL_FLAGS) & F.FLAG_STOCK
    assert (w.slot32(F.SL_STOCK_CL), w.slot32(F.SL_STOCK_L),
            w.slot32(F.SL_STOCK_CR), w.slot32(F.SL_STOCK_R)) == w.stock
    assert w.slot_name() == 'aaaa', w.slot_name()
    assert w.mem.u(w.slot() + F.SL_NAMELEN, 4) == 4


def test_the_model_name_is_lowercased_and_stripped_of_its_extension():
    w = World(archive=[], model_name='BK2H')
    w.run()
    assert w.slot_name() == 'bk2h', w.slot_name()


def test_candidate_zero_is_the_per_model_pair():
    w = World(archive=['eye_aaaa_2', 'eye_aaaar_2'])
    w.run()
    assert w.names[0] == ('eye_aaaa_2.tim', 'eye_aaaar_2.tim'), w.names
    assert w.slot32(F.SL_ART_L) == TEX_L
    assert w.slot32(F.SL_ART_R) == TEX_R
    assert w.slot8(F.SL_ART_IDX) == 2
    assert w.slot8(F.SL_APPLIED) == 2
    # the art is parked where the stock closed-eye path reads it
    assert w.gget32(w.anim + F.A_STATIC_L) == TEX_L
    assert w.gget32(w.anim + F.A_STATIC_R) == TEX_R


def test_the_two_custom_pointers_are_put_back():
    """
    `field_load_model_eye_tex` clears all four slots. Eye set 4 (Red XIII) has
    a real custom pointer, so losing them would leak a texture the stock
    teardown was going to free.
    """
    w = World(archive=['eye_aaaa_2', 'eye_aaaar_2'])
    w.run()
    assert w.gget32(w.anim + F.A_CUSTOM_L) == w.stock[0]
    assert w.gget32(w.anim + F.A_CUSTOM_R) == w.stock[2]


def test_candidate_one_is_the_generic_pair_from_the_eye_buffer():
    """
    The cast's own indexed art is not named after an HRC id -- it is
    `b_eye2_2`, `t_eye2_2`, `chi_red5_2`. The fallback name is read from the
    game's own blink buffer rather than hard-coded.
    """
    w = World(archive=['b_eye2_2', 'b_eye2r_2'], eye_idx=3)
    w.run()
    assert w.names[0] == ('eye_aaaa_2.tim', 'eye_aaaar_2.tim'), w.names
    assert w.names[1] == ('b_eye2_2.tim', 'b_eye2r_2.tim'), w.names
    assert w.slot8(F.SL_ART_IDX) == 2
    assert w.gget32(w.anim + F.A_STATIC_L) == TEX_L


def test_a_missing_index_is_probed_once_and_then_never_again():
    w = World(archive=[])
    w.run()
    assert w.slot8(F.SL_MISS_EYE) == (1 << 2), bin(w.slot8(F.SL_MISS_EYE))
    assert w.slot8(F.SL_APPLIED) == 0
    # the stock pointers go back, so the model blinks exactly as it did
    assert w.gget32(w.anim + F.A_STATIC_L) == w.stock[1]
    assert w.gget32(w.anim + F.A_STATIC_R) == w.stock[3]
    tries = len(w.names)
    assert tries == 2, 'expected both candidates, got %d' % tries
    w.set_esp(w.esp)
    out = w.run()
    assert out == F.BLINK_RESUME
    assert len(w.names) == tries, 'the absent name was asked for again'


def test_a_half_loaded_pair_is_given_back_rather_than_half_used():
    w = World(archive=['eye_aaaa_2'])          # left only
    w.run()
    assert w.slot8(F.SL_APPLIED) == 0
    assert w.slot32(F.SL_ART_L) == 0 and w.slot32(F.SL_ART_R) == 0
    assert TEX_L in w.unloaded, w.unloaded
    assert w.gget32(w.anim + F.A_STATIC_L) == w.stock[1]


def test_steady_state_makes_no_guest_call():
    """
    The framerate case. Once the index is parked, every later frame has to be
    byte comparisons only -- no probe, no load, no allocation.
    """
    w = World(archive=['eye_aaaa_2', 'eye_aaaar_2'])
    w.run()
    before = (len(w.names), len(w.loaded), len(w.unloaded))
    for _ in range(8):
        w.set_esp(w.esp)
        assert w.run() == F.BLINK_RESUME
    assert (len(w.names), len(w.loaded), len(w.unloaded)) == before
    assert w.gget32(w.anim + F.A_STATIC_L) == TEX_L


def test_an_open_eye_frame_leaves_the_parked_texture_alone():
    """
    Blink mode 1 does not read static_*_eye_tex at all -- it draws the
    model's own group -- so an open frame has nothing to choose and nothing
    to put back. Swapping the stock pointer in here instead made the one-deep
    art cache thrash between index 1 and index 2, which is a texture load on
    every blink of every model. That is the whole framerate story, so it gets
    a test of its own.
    """
    w = World(archive=['eye_aaaa_2', 'eye_aaaar_2'])
    w.run()
    names = len(w.names)
    for modes in ((1, 1), (2, 2), (1, 1), (2, 2), (1, 1)):
        w.gset8(w.blink + 0, modes[0])
        w.gset8(w.blink + 1, modes[1])
        w.set_esp(w.esp)
        assert w.run() == F.BLINK_RESUME
        assert len(w.names) == names, 'a blink cycle re-loaded the texture'
    assert w.slot8(F.SL_APPLIED) == 2
    assert w.gget32(w.anim + F.A_STATIC_L) == TEX_L


def test_mismatched_left_and_right_modes_fall_through_to_stock():
    w = World(archive=['eye_aaaa_2', 'eye_aaaar_2'], modes=(2, 1))
    assert w.run() == F.BLINK_RESUME
    assert w.names == [], w.names
    assert w.slot8(F.SL_APPLIED) == 0


def test_a_model_id_out_of_range_is_refused():
    w = World(archive=[], model_id=1)
    w.gset8(w.blink + 3, 0xFF)
    assert w.run() == F.BLINK_RESUME
    assert w.names == []


def test_eye_set_nine_resolves_to_cloud_and_eight_is_refused():
    npc = World(archive=['c_eye2_2', 'c_eye2r_2'], eye_idx=9)
    npc.run()
    assert npc.names[1] == ('c_eye2_2.tim', 'c_eye2r_2.tim'), npc.names
    blind = World(archive=['eye_aaaa_2'], eye_idx=8)
    assert blind.run() == F.BLINK_RESUME
    assert blind.names == []


# ------------------------------------------------------------- expressions
def test_a_latched_expression_outranks_the_blink_loop_every_frame():
    """
    The per-frame loop rewrites both modes before every call, so an expression
    that is not re-asserted survives one frame. It has to be forced back to
    mode 2 -- the mode that displays the texture it was parked in.
    """
    w = World(archive=['eye_aaaa_4', 'eye_aaaar_4'], modes=(1, 1))
    w.set_slot8(F.SL_EXPR, 4)
    assert w.run() == F.BLINK_RESUME
    assert w.names[0] == ('eye_aaaa_4.tim', 'eye_aaaar_4.tim'), w.names
    assert w.gget32(w.anim + F.A_STATIC_L) == TEX_L
    assert w.gget8(w.blink + 0) == 2 and w.gget8(w.blink + 1) == 2
    # and again on the next frame, after the loop has reset the modes
    w.gset8(w.blink + 0, 1)
    w.gset8(w.blink + 1, 1)
    w.set_esp(w.esp)
    assert w.run() == F.BLINK_RESUME
    assert w.gget8(w.blink + 0) == 2 and w.gget8(w.blink + 1) == 2


def test_clearing_the_expression_returns_the_model_to_normal_blinking():
    """A later EYETX with an index of 1 or 2 drops the latch, and the model
    goes back to blinking on its own closed-eye art."""
    w = World(archive=['eye_aaaa_2', 'eye_aaaar_2',
                       'eye_aaaa_4', 'eye_aaaar_4'], modes=(2, 2))
    w.set_slot8(F.SL_EXPR, 4)
    w.run()
    assert w.names[-1] == ('eye_aaaa_4.tim', 'eye_aaaar_4.tim'), w.names
    assert w.slot8(F.SL_APPLIED) == 4

    w.set_slot8(F.SL_EXPR, 0)
    w.set_esp(w.esp)
    assert w.run() == F.BLINK_RESUME
    assert w.names[-1] == ('eye_aaaa_2.tim', 'eye_aaaar_2.tim'), w.names
    assert w.slot8(F.SL_APPLIED) == 2
    # and with the latch gone the open frames are left to the stock body
    w.gset8(w.blink + 0, 1)
    w.gset8(w.blink + 1, 1)
    w.set_esp(w.esp)
    assert w.run() == F.BLINK_RESUME
    assert w.gget8(w.blink + 0) == 1, 'mode 1 was overwritten with no latch'


def test_an_expression_whose_art_is_missing_does_not_shut_the_eyes():
    """
    Forcing mode 2 is what displays the expression. Forcing it when the art
    could not be loaded would leave the model sitting with its eyes closed,
    which is worse than ignoring the expression.
    """
    w = World(archive=[], modes=(1, 1))
    w.set_slot8(F.SL_EXPR, 5)
    assert w.run() == F.BLINK_RESUME
    assert w.slot8(F.SL_APPLIED) == 0
    assert w.gget8(w.blink + 0) == 1 and w.gget8(w.blink + 1) == 1
    assert w.gget32(w.anim + F.A_STATIC_L) == w.stock[1]


# ------------------------------------------------------------------- mouth
def test_the_mouth_is_loaded_through_a_hand_built_file_context():
    w = World(archive=['mouth_aaaa_2'])
    w.gset32(F.G_GAME_OBJECT, 0xABCDEF01)
    w.set_slot8(F.SL_MOUTH_SCRIPT, 2)
    assert w.run() == F.BLINK_RESUME
    assert 'mouth_aaaa_2.tim' in w.names, w.names
    assert w.slot32(F.SL_MOUTH_TEX) == TEX_M
    assert w.slot8(F.SL_MOUTH_IDX) == 2


def test_the_mouth_falls_back_to_the_characters_generic_name():
    w = World(archive=['b_mouth_2'], eye_idx=3)
    w.gset32(F.G_GAME_OBJECT, 0xABCDEF01)
    w.set_slot8(F.SL_MOUTH_SCRIPT, 2)
    w.run()
    assert 'mouth_aaaa_2.tim' in w.names, w.names
    assert 'b_mouth_2.tim' in w.names, w.names
    assert w.slot32(F.SL_MOUTH_TEX) == TEX_M


def test_an_npc_does_use_the_generic_mouth():
    """AN NPC LIP-FLAPS AGAIN, AND THAT IS THE POINT.

    The previous contract here was the opposite: eye set 9 was banned from
    Cloud's mouth outright, to stop `oldm3`'s head flashing pink. It worked,
    and it cost EVERY NPC its lip flap, because `npc_mouth_<n>` is not in the
    shipped art -- MEASURED over the built flevel: npc_mouth_2 ABSENT,
    c_mouth_2 PRESENT. Only the eight cast members with their own generic set
    kept moving, which is exactly what was reported from hardware.

    The pink head is a property of oldm3's GEOMETRY, not of being an NPC, and
    it is refused on that basis now -- see the MOUTH_MAX_VERTS tests above.
    """
    w = World(archive=['c_mouth_2'], eye_idx=9)
    w.gset32(F.G_GAME_OBJECT, 0xABCDEF01)
    w.set_slot8(F.SL_MOUTH_SCRIPT, 2)
    w.run()
    assert w.slot8(F.SL_FLAGS) & F.FLAG_NPC
    assert 'mouth_aaaa_2.tim' in w.names, w.names      # its own art first
    assert 'c_mouth_2.tim' in w.names, w.names         # then the generic
    assert w.slot32(F.SL_MOUTH_TEX) == TEX_M


def test_an_npc_still_prefers_its_own_model_mouth():
    w = World(archive=['mouth_aaaa_2', 'c_mouth_2'], eye_idx=9)
    w.gset32(F.G_GAME_OBJECT, 0xABCDEF01)
    w.set_slot8(F.SL_MOUTH_SCRIPT, 2)
    w.run()
    assert 'mouth_aaaa_2.tim' in w.names, w.names
    assert 'c_mouth_2.tim' not in w.names, w.names
    assert w.slot32(F.SL_MOUTH_TEX) == TEX_M


def test_an_absent_mouth_is_asked_for_once():
    w = World(archive=[], eye_idx=3)
    w.gset32(F.G_GAME_OBJECT, 0xABCDEF01)
    w.set_slot8(F.SL_MOUTH_SCRIPT, 2)
    w.run()
    assert w.slot8(F.SL_MISS_MOUTH) == (1 << 2)
    assert w.slot8(F.SL_MOUTH_IDX) == 2, 'the request was not settled'
    tries = len(w.names)
    w.set_esp(w.esp)
    w.run()
    assert len(w.names) == tries, 'the absent mouth was asked for again'


def test_dropping_the_mouth_request_releases_the_texture():
    w = World(archive=['mouth_aaaa_2'])
    w.gset32(F.G_GAME_OBJECT, 0xABCDEF01)
    w.set_slot8(F.SL_MOUTH_SCRIPT, 2)
    w.run()
    w.set_slot8(F.SL_MOUTH_SCRIPT, 0)
    w.set_esp(w.esp)
    assert w.run() == F.BLINK_RESUME
    assert w.unloaded == [TEX_M], w.unloaded
    assert w.slot32(F.SL_MOUTH_TEX) == 0
    assert w.slot8(F.SL_MOUTH_IDX) == 0


def test_the_mouth_apply_cave_repoints_group_three():
    w = World(archive=[])
    poly = 0x02B00000
    group = 0x02B01000
    ebp = GUEST_STACK - 0x100
    w.set_ebp(ebp)
    w.gset32(ebp + 0xC, w.blink)
    w.gset32(ebp - 0x14, poly)
    w.gset32(poly + F.P_NUMGROUPS, 6)
    w.gset32(poly + F.P_GROUP_ARRAY, group)
    w.gset32(poly + F.P_PER_GROUP, 0)
    _give_mouth_geometry(w, poly, verts=4)
    w.set_slot32(F.SL_MOUTH_TEX, TEX_M)
    out = w.run('mouth')
    assert out == F.MOUTH_RESUME, 'left at 0x%X' % out
    assert w.gget32(group + 4 * F.MOUTH_GROUP) == TEX_M
    assert w.gget32(poly + F.P_PER_GROUP) == 1


PGROUPS = 0x02B02000


def _give_mouth_geometry(w, poly, verts):
    """Point the polygon_set at a group array whose group 3 has `verts`.

    A mouth is a quad. `oldm3`'s group 3 is the whole head -- 162 polygons on
    NPC20.TEX -- and pointing that at a 32x32 mouth texture is what made the
    head flash pink while he spoke.
    """
    w.gset32(poly + F.P_POLY_GROUPS, PGROUPS)
    w.gset32(PGROUPS + F.PG_STRIDE * F.MOUTH_GROUP + F.PG_NUMVERT, verts)


def test_a_head_sized_group_three_is_never_given_a_mouth():
    """The oldm3 bug, as geometry rather than as a rule about NPCs."""
    w = World(archive=[])
    poly = 0x02B00000
    group = 0x02B01000
    ebp = GUEST_STACK - 0x100
    w.set_ebp(ebp)
    w.gset32(ebp + 0xC, w.blink)
    w.gset32(ebp - 0x14, poly)
    w.gset32(poly + F.P_NUMGROUPS, 6)
    w.gset32(poly + F.P_GROUP_ARRAY, group)
    w.gset32(poly + F.P_PER_GROUP, 0)
    _give_mouth_geometry(w, poly, verts=162)      # the whole head
    w.set_slot32(F.SL_MOUTH_TEX, TEX_M)
    out = w.run('mouth')
    assert out == F.MOUTH_RESUME, 'left at 0x%X' % out
    assert w.gget32(group + 4 * F.MOUTH_GROUP) == 0, 'the head was recoloured'
    assert w.gget32(poly + F.P_PER_GROUP) == 0


def test_a_mouth_sized_group_three_still_gets_one():
    w = World(archive=[])
    poly = 0x02B00000
    group = 0x02B01000
    ebp = GUEST_STACK - 0x100
    w.set_ebp(ebp)
    w.gset32(ebp + 0xC, w.blink)
    w.gset32(ebp - 0x14, poly)
    w.gset32(poly + F.P_NUMGROUPS, 6)
    w.gset32(poly + F.P_GROUP_ARRAY, group)
    w.gset32(poly + F.P_PER_GROUP, 0)
    _give_mouth_geometry(w, poly, verts=F.MOUTH_MAX_VERTS)   # the boundary
    w.set_slot32(F.SL_MOUTH_TEX, TEX_M)
    out = w.run('mouth')
    assert out == F.MOUTH_RESUME, 'left at 0x%X' % out
    assert w.gget32(group + 4 * F.MOUTH_GROUP) == TEX_M


def test_a_model_with_too_few_groups_is_left_alone():
    """
    FFNx reads `hundred_data_group_array[3]` to decide, which is past the end
    on a three-group model. numgroups is the test that cannot be.
    """
    w = World(archive=[])
    poly = 0x02B00000
    group = 0x02B01000
    ebp = GUEST_STACK - 0x100
    w.set_ebp(ebp)
    w.gset32(ebp + 0xC, w.blink)
    w.gset32(ebp - 0x14, poly)
    w.gset32(poly + F.P_NUMGROUPS, 3)
    w.gset32(poly + F.P_GROUP_ARRAY, group)
    w.set_slot32(F.SL_MOUTH_TEX, TEX_M)
    assert w.run('mouth') == F.MOUTH_RESUME
    assert w.gget32(group + 4 * F.MOUTH_GROUP) == 0
    assert w.gget32(poly + F.P_PER_GROUP) == 0


# ------------------------------------------------------------------- KAWAI
def _kawai_world(left, right, mouth, size=6, subcode=0, entity=2, model=1):
    w = World(archive=[], model_id=model)
    w.gset8(F.G_ENTITY_ID, entity)
    w.gset32(F.G_SCRIPT_CODE, GUEST_CODE)
    ip = 0x40
    w.mem.setu(w.host(F.G_SCRIPT_IP + entity * 2), ip, 2)
    w.gset8(F.G_MODEL_ID_ARRAY + entity, model)
    for i, b in enumerate((0x28, size, subcode, left, right, mouth)):
        w.gset8(GUEST_CODE + ip + i, b)
    return w


def test_kawai_latches_the_mouth_index_the_stock_handler_throws_away():
    w = _kawai_world(1, 1, 2)
    assert w.run('kawai') == F.KAWAI_RESUME
    assert w.slot8(F.SL_MOUTH_SCRIPT) == 2
    assert w.slot8(F.SL_EXPR) == 0


def test_kawai_latches_an_expression_the_stock_guard_would_drop():
    w = _kawai_world(4, 4, 0)
    assert w.run('kawai') == F.KAWAI_RESUME
    assert w.slot8(F.SL_EXPR) == 4
    assert w.slot8(F.SL_MOUTH_SCRIPT) == 0


def test_kawai_index_two_clears_a_standing_expression():
    w = _kawai_world(2, 2, 0)
    w.set_slot8(F.SL_EXPR, 5)
    w.run('kawai')
    assert w.slot8(F.SL_EXPR) == 0


def test_kawai_ignores_other_subcodes_and_short_instructions():
    other = _kawai_world(4, 4, 3, subcode=6)
    other.run('kawai')
    assert other.slot8(F.SL_EXPR) == 0 and other.slot8(F.SL_MOUTH_SCRIPT) == 0
    short = _kawai_world(4, 4, 3, size=4)
    short.run('kawai')
    assert short.slot8(F.SL_EXPR) == 0 and short.slot8(F.SL_MOUTH_SCRIPT) == 0


# --------------------------------------------------------- the lip flap
def _speaking(archive, model_id=1, playing=True, eye_idx=3):
    w = World(archive=archive, voice_bss=VOICE_BSS, model_id=model_id,
              eye_idx=eye_idx)
    w.gset32(F.G_GAME_OBJECT, 0xABCDEF01)
    w.mem.setu(w.bss + F.HDR_SPEAKER, model_id + 1, 4)
    w.mem.setu(VOICE_BSS + F.VOICE_PLAYER_OFF, 0x900D0000 if playing else 0, 8)
    return w


def test_the_flap_only_runs_while_a_line_is_actually_sounding():
    """
    The gate is ff7nx_voice's live player pointer, not the window being open.
    A window with no clip -- most of them, outside Echo-S's coverage -- must
    leave the mouth exactly where the script put it.
    """
    silent = _speaking(['mouth_aaaa_2'], playing=False)
    for _ in range(6):
        silent.set_esp(silent.esp)
        assert silent.run() == F.BLINK_RESUME
        assert silent.slot8(F.SL_MOUTH_REQ) == 0
    assert silent.slot32(F.SL_MOUTH_TEX) == 0


def test_the_flap_opens_and_closes_the_mouth_over_frames():
    w = _speaking(['mouth_aaaa_2'])
    seen = set()
    for _ in range(8):
        w.set_esp(w.esp)
        assert w.run() == F.BLINK_RESUME
        seen.add(w.slot8(F.SL_MOUTH_REQ))
    assert seen == {0, F.MOUTH_FLAP_INDEX}, seen
    assert 'mouth_aaaa_2.tim' in w.names, w.names


def test_the_voice_flap_reaches_an_npc():
    """The flap is what makes NPCs speak at all; see the note above."""
    w = _speaking(['c_mouth_2'], eye_idx=9)
    opened = False
    for _ in range(8):
        w.set_esp(w.esp)
        assert w.run() == F.BLINK_RESUME
        opened = opened or w.slot32(F.SL_MOUTH_TEX) == TEX_M
    # The flap alternates, so the texture is released again on the closed
    # half of the cycle -- assert it was OPEN at some point, not at the end.
    # The old test here asserted the end state was 0, which was true for that
    # reason as well as for the ban it meant to check.
    assert 'c_mouth_2.tim' in w.names, w.names
    assert opened, 'the NPC never got a mouth texture: %s' % (w.names,)


def test_the_flap_leaves_every_other_model_alone():
    w = _speaking(['mouth_aaaa_2'], model_id=1)
    w.mem.setu(w.bss + F.HDR_SPEAKER, 5 + 1, 4)       # somebody else speaks
    for _ in range(8):
        w.set_esp(w.esp)
        w.run()
        assert w.slot8(F.SL_MOUTH_REQ) == 0


def test_a_scripted_mouth_index_outranks_the_flap():
    w = _speaking(['mouth_aaaa_3'])
    w.set_slot8(F.SL_MOUTH_SCRIPT, 3)
    for _ in range(6):
        w.set_esp(w.esp)
        w.run()
        assert w.slot8(F.SL_MOUTH_REQ) == 3
    assert 'mouth_aaaa_3.tim' in w.names, w.names


TIFA_LINE = b'Tifa' + bytes([0xE7, 0xB2]) + b'Hi' + bytes([0xB3, 0xFF])
ALERT_LINE = bytes([0x00] * 6) + b'Type A' + bytes([0xE7]) + b'x' + bytes([0xFF])
YUFFIE_LINE = bytes([0x00]) + b'Yuffie' + bytes([0xE7, 0xB2]) + b'Yo' + bytes([0xFF])
ASIDE_LINE = bytes([0x00]) + b'Obtained' + bytes([0xFF])


def _speak_world(model, text, entity=7, string_id=3, speaker_before=0):
    """A MESSAGE opcode (0x40, window, string) at the entity's ip, and a
    section-1 string table whose entry `string_id` holds `text`."""
    w = World(archive=[], voice_bss=VOICE_BSS, model_id=max(model, 0) & 0x1F)
    w.mem.setu(w.bss + F.HDR_SPEAKER, speaker_before, 4)
    w.gset8(F.G_ENTITY_ID, entity)
    w.gset8(F.G_MODEL_ID_ARRAY + entity, model)
    w.gset32(F.G_SCRIPT_CODE, GUEST_CODE)
    ip, table = 0x40, 0x200
    w.mem.setu(w.host(F.G_SCRIPT_IP + entity * 2), ip, 2)
    for i, b in enumerate((0x40, 0, string_id)):
        w.gset8(GUEST_CODE + ip + i, b)
    w.gset8(GUEST_CODE + 4, table & 0xFF)
    w.gset8(GUEST_CODE + 5, table >> 8)
    off = 0x40
    ent = table + 2 + 2 * string_id
    w.gset8(GUEST_CODE + ent, off & 0xFF)
    w.gset8(GUEST_CODE + ent + 1, off >> 8)
    for i, b in enumerate(text):
        w.gset8(GUEST_CODE + table + off + i, b)
    return w


def test_the_speak_cave_records_the_model_that_opened_the_window():
    w = _speak_world(4, TIFA_LINE)
    assert w.run('speak') == F.SPEAK_RESUME
    assert w.mem.u(w.bss + F.HDR_SPEAKER, 4) == 5, 'stored as model + 1'


def test_the_speak_cave_refuses_an_entity_with_no_model():
    w = _speak_world(0xFF, TIFA_LINE)
    assert w.run('speak') == F.SPEAK_RESUME
    assert w.mem.u(w.bss + F.HDR_SPEAKER, 4) == 0


def test_a_line_from_an_entity_with_no_model_clears_the_last_speaker():
    w = _speak_world(0xFF, TIFA_LINE, entity=18, speaker_before=3 + 1)
    assert w.run('speak') == F.SPEAK_RESUME
    assert w.mem.u(w.bss + F.HDR_SPEAKER, 4) == 0


def test_tin_1_the_security_alert_in_tifas_own_script_is_not_hers():
    """BUILD 613b: tin_1 issues 'Type A security alert.' from Tifa's own
    entity.  The line is centred with leading spaces and has no name/quote
    header, so it is system text -- nobody's mouth moves."""
    w = _speak_world(3, ALERT_LINE, speaker_before=3 + 1)
    assert w.run('speak') == F.SPEAK_RESUME
    assert w.mem.u(w.bss + F.HDR_SPEAKER, 4) == 0


def test_a_leading_space_aside_without_a_quote_is_nobodys():
    w = _speak_world(2, ASIDE_LINE)
    assert w.run('speak') == F.SPEAK_RESUME
    assert w.mem.u(w.bss + F.HDR_SPEAKER, 4) == 0


def test_a_padded_name_followed_by_a_quote_is_still_spoken():
    """mtcrl_0: ' Yuffie' then newline then an opening quote -- a real line."""
    w = _speak_world(6, YUFFIE_LINE)
    assert w.run('speak') == F.SPEAK_RESUME
    assert w.mem.u(w.bss + F.HDR_SPEAKER, 4) == 7


# ------------------------------------------- BUILD 734: ASK, and the tag
CHOICE_LINE = bytes([0xE0]) + b'Go' + bytes([0xE7, 0xE0]) + b'Stay' + bytes([0xFF])


def _tagged_world(model, op, strings, tag=None, entity=7, at=3, models=None,
                  player=None, speaker_before=0):
    """An opcode `op` at the entity's ip, its dialog operand at +2 (MESSAGE)
    or +3 (ASK) naming string `at`; `strings` fills the table, and `tag`, a
    {string id: byte} map, becomes the SPK1 string at the table's end."""
    w = World(archive=[], voice_bss=VOICE_BSS, model_id=max(model, 0) & 0x1F)
    w.mem.setu(w.bss + F.HDR_SPEAKER, speaker_before, 4)
    w.gset8(F.G_ENTITY_ID, entity)
    w.gset8(F.G_MODEL_ID_ARRAY + entity, model)
    for e, m in (models or {}).items():
        w.gset8(F.G_MODEL_ID_ARRAY + e, m)
    if player is not None:
        w.gset8(F.G_PLAYER_MODEL, player & 0xFF)
        w.gset8(F.G_PLAYER_MODEL + 1, player >> 8)
    w.gset32(F.G_SCRIPT_CODE, GUEST_CODE)
    ip, table = 0x40, 0x200
    w.mem.setu(w.host(F.G_SCRIPT_IP + entity * 2), ip, 2)
    code = (0x40, 0, at) if op == 0x40 else (0x48, 0, 0, at, 0, 1)
    for i, b in enumerate(code):
        w.gset8(GUEST_CODE + ip + i, b)
    w.gset8(GUEST_CODE + 4, table & 0xFF)
    w.gset8(GUEST_CODE + 5, table >> 8)
    strings = list(strings)
    if tag is not None:
        body = bytearray(len(strings))
        for sid, v in tag.items():
            body[sid] = v
        strings.append(bytes(F.SPEAKER_MAGIC) + bytes(body) + bytes([0xFF]))
    w.gset8(GUEST_CODE + table, len(strings) & 0xFF)
    w.gset8(GUEST_CODE + table + 1, len(strings) >> 8)
    off = 2 + 2 * len(strings)
    for n, text in enumerate(strings):
        w.gset8(GUEST_CODE + table + 2 + 2 * n, off & 0xFF)
        w.gset8(GUEST_CODE + table + 3 + 2 * n, off >> 8)
        for i, b in enumerate(text):
            w.gset8(GUEST_CODE + table + off + i, b)
        off += len(text)
    return w


def test_an_ask_names_its_string_one_byte_further_than_a_message():
    """ASK is opcode, +1, +2 window, +3 dialog. Reading +2 like MESSAGE would
    take string 0 -- system text here, so the wrong read stores nobody."""
    w = _tagged_world(4, 0x48, [ALERT_LINE, ALERT_LINE, ALERT_LINE, TIFA_LINE])
    assert w.run('askspeak') == F.ASK_SPEAK_RESUME
    assert w.mem.u(w.bss + F.HDR_SPEAKER, 4) == 5


def test_md8_2_cloud_says_his_own_choices_not_aerith():
    """The choice is issued by the director, which has no model; the tag
    names the player, whose model id the game keeps at G_PLAYER_MODEL. The
    last speaker before it (Aerith, model 1) must not survive the choice."""
    w = _tagged_world(0xFF, 0x48, [TIFA_LINE] * 3 + [CHOICE_LINE],
                      tag={3: F.SPEAKER_PLAYER}, player=5,
                      speaker_before=1 + 1)
    assert w.run('askspeak') == F.ASK_SPEAK_RESUME
    assert w.mem.u(w.bss + F.HDR_SPEAKER, 4) == 5 + 1


def test_a_player_with_no_model_is_nobody():
    w = _tagged_world(0xFF, 0x48, [TIFA_LINE] * 3 + [CHOICE_LINE],
                      tag={3: F.SPEAKER_PLAYER}, player=0xFFFF,
                      speaker_before=1 + 1)
    assert w.run('askspeak') == F.ASK_SPEAK_RESUME
    assert w.mem.u(w.bss + F.HDR_SPEAKER, 4) == 0


def test_a_tagged_message_speaks_through_the_named_entity():
    """md8_1: the director issues Barret's line; the tag says entity 9."""
    w = _tagged_world(0xFF, 0x40, [TIFA_LINE] * 4, tag={3: 9 + 1},
                      models={9: 2})
    assert w.run('speak') == F.SPEAK_RESUME
    assert w.mem.u(w.bss + F.HDR_SPEAKER, 4) == 2 + 1


def test_a_message_after_an_ask_resumes_the_message_handler():
    """The entry flag is set on every entry, so an ASK never leaks into the
    next MESSAGE's resume or operand offset."""
    w = _tagged_world(4, 0x48, [TIFA_LINE] * 4)
    assert w.run('askspeak') == F.ASK_SPEAK_RESUME
    for i, b in enumerate((0x40, 0, 3)):
        w.gset8(GUEST_CODE + 0x40 + i, b)
    assert w.run('speak') == F.SPEAK_RESUME
    assert w.mem.u(w.bss + F.HDR_SPEAKER, 4) == 5


def test_without_a_voice_base_not_one_flap_word_is_emitted():
    """
    The coupling is optional and has to stay that way: with no voice pass in
    the build this is exactly the FFNx port, and the MESSAGE handler is not
    touched at all.
    """
    plain = layout(None)[1]
    flap = layout(VOICE_BSS)[1]
    assert F.SPEAK_HOOK not in plain
    assert F.SPEAK_HOOK in flap
    assert len(flap) > len(plain)


# ---------------------------------------------------------------- teardown
def test_teardown_restores_and_frees_exactly_once():
    """
    The ownership case. After this cave the stock unload loop must see the
    four pointers the stock model load created, and every texture we allocated
    must have been handed back once.
    """
    w = World(archive=['eye_aaaa_2', 'eye_aaaar_2', 'mouth_aaaa_2'])
    w.gset32(F.G_GAME_OBJECT, 0xABCDEF01)
    w.set_slot8(F.SL_MOUTH_SCRIPT, 2)
    w.run()
    w.run()                                    # second frame: the mouth
    assert w.slot32(F.SL_ART_L) == TEX_L
    assert w.slot32(F.SL_MOUTH_TEX) == TEX_M
    ours = sorted(w.loaded)
    w.unloaded = []
    out = w.run('free')
    assert out == F.FREE_RESUME, 'left at 0x%X' % out
    assert sorted(w.unloaded) == ours, (w.unloaded, ours)
    assert (w.gget32(w.anim + F.A_CUSTOM_L), w.gget32(w.anim + F.A_STATIC_L),
            w.gget32(w.anim + F.A_CUSTOM_R),
            w.gget32(w.anim + F.A_STATIC_R)) == w.stock
    assert w.slot8(F.SL_FLAGS) == 0 and w.slot32(F.SL_ART_L) == 0
    assert w.slot8(F.SL_MOUTH_IDX) == 0


def test_teardown_after_the_game_replaced_the_textures_itself():
    """THE LEAK (FINDINGS-515), as a test.

    Our slot parks OUR art in `anim[A_STATIC_*]` and keeps the stock pointer
    in `SL_STOCK_*`, to be put back at the next field's model teardown. That
    contract holds only if NOTHING re-creates the model's textures in
    between.

    A BATTLE DOES. So does exiting to the title. Those tear the models down
    and reload them without ever passing through `FREE_HOOK`, so the game's
    anim entry ends up holding FRESH pointers while our slot still holds the
    old ones with FLAG_STOCK set.

    Then the next field change runs this cave, which:
      * writes the STALE captured pointers over the fresh ones -- the fresh
        textures are now unreachable and never freed, and
      * hands back our art pointer, which the game already freed.

    That is the leak, and it scales with fields walked, which is what was
    measured on hardware.
    """
    w = World(archive=['eye_aaaa_2', 'eye_aaaar_2'])
    w.gset32(F.G_GAME_OBJECT, 0xABCDEF01)
    w.run()
    assert w.slot32(F.SL_ART_L) == TEX_L, 'our art was never parked'
    assert w.gget32(w.anim + F.A_STATIC_L) == TEX_L

    # A battle happens: the game frees and re-creates the model's textures.
    FRESH_L, FRESH_R = 0x0BAD0001, 0x0BAD0002
    w.gset32(w.anim + F.A_STATIC_L, FRESH_L)
    w.gset32(w.anim + F.A_STATIC_R, FRESH_R)

    w.unloaded = []
    assert w.run('free') == F.FREE_RESUME

    assert w.gget32(w.anim + F.A_STATIC_L) == FRESH_L, (
        'the cave wrote a stale pointer over the texture the game just made; '
        'that texture is now unreachable -- got %#x'
        % w.gget32(w.anim + F.A_STATIC_L))
    assert w.gget32(w.anim + F.A_STATIC_R) == FRESH_R
    assert TEX_L not in w.unloaded, (
        'our art was freed although the game had already replaced it')


def test_teardown_touches_nothing_it_never_captured():
    w = World(archive=[])
    assert w.run('free') == F.FREE_RESUME
    assert w.unloaded == []


# ----------------------------------------------------------- the invariants
def _callee_saved_survive(which, setup=None):
    w = World(archive=['eye_aaaa_2', 'eye_aaaar_2'])
    w.gset32(F.G_GAME_OBJECT, 0xABCDEF01)
    if setup:
        setup(w)
    marks = {r: 0x1234000000000000 + r for r in range(19, 29)}
    for r, v in marks.items():
        w.cpu.x[r] = v
    sp = w.cpu.sp
    w.run(which)
    return w, marks, sp


def test_every_cave_restores_the_host_frame_it_borrowed():
    """
    Every hook here is a `b` spliced into recompiler output, not a call into
    code we wrote, so whatever the surrounding block held in x19..x28 has to
    come back -- and the stack pointer with it, net of the stock prologue the
    cave replays on its way out (each of these sites displaces a pre-indexed
    store, which is the real function's own frame push).
    """
    pushes = {'blink': 0x30, 'kawai': 0x50, 'free': 0x50, 'mouth': 0}
    for which, push in pushes.items():
        w, marks, sp = _callee_saved_survive(which)
        for r, v in marks.items():
            assert w.cpu.x[r] == v, ('%s clobbered x%d: %016X' %
                                     (which, r, w.cpu.x[r]))
        assert w.cpu.sp == sp - push, (
            '%s left SP at %X, wanted %X' % (which, w.cpu.sp, sp - push))


def test_the_blink_cave_puts_guest_esp_back():
    """
    The cave lowers ESP to build its call frames. The stock body resumes with
    its own locals addressed off it, so leaving it moved is a stack smash.
    """
    w = World(archive=['eye_aaaa_2', 'eye_aaaar_2'])
    assert w.run() == F.BLINK_RESUME
    assert w.get_esp() == w.esp, '0x%X, wanted 0x%X' % (w.get_esp(), w.esp)


def test_the_guest_window_never_straddles_a_page():
    """
    The translator is a page table, so a block indexed off one translation has
    to lie in one page. Both blocks are 0x100-aligned and 0x100 long, which
    makes that true for every possible ESP -- checked rather than argued.
    """
    for esp in range(0x1000, 0x3000, 4):
        gcall = (esp - F.GUEST_WINDOW) & ~0xFF
        gscratch = gcall + F.GSCRATCH_BYTES
        for base in (gcall, gscratch):
            assert base >> 12 == (base + F.GSCRATCH_BYTES - 1) >> 12, \
                'esp 0x%X puts 0x%X across a page' % (esp, base)
        assert gscratch >= gcall + 0x100, 'the frames overlap'


def test_the_scratch_layout_fits_the_block():
    assert F.GS_STRUC3 + F.S3_BYTES <= F.GSCRATCH_BYTES
    assert F.GS_NAME_M + 24 <= F.GS_STRUC3
    assert F.SL_NAMELEN + 4 <= F.SLOT_BYTES
    assert F.BSS_BYTES == F.SLOTS_OFF + F.MAX_MODELS * F.SLOT_BYTES


def test_no_hook_collides_with_the_60_fps_blink_patches():
    """
    The 60 FPS group already owns two constants and a cave inside
    `field_animate_3d_models` -- the blink interval and the closed-eye hold.
    They are the OTHER half of blinking at 60 FPS and this feature must not be
    able to land on top of them; `expect_word` would catch it at build time,
    but naming the addresses here says which patch would have been destroyed.
    """
    import ff7nx_60fps as SIXTY                                # noqa: E402
    owned = {va for _what, va, _stock, _want in
             SIXTY.NSO_GATED['field-blink']}
    owned |= {0x94B704, 0x94B708}         # the field-blink-hold cave site
    ours = {site for _n, site, _o, _w in F.sites(VOICE_BSS)}
    assert not (owned & ours), sorted(owned & ours)


def test_the_flap_rate_follows_the_frame_rate():
    """
    Everything else here is frame-count independent -- the runtime reacts to
    an index, not to a clock. The lip flap is the exception, so its divisor is
    derived from the limiter this build actually sets rather than assumed.
    """
    for fps, shift in ((30, F.FLAP_SHIFT_30), (60, F.FLAP_SHIFT_60),
                       (66, F.FLAP_SHIFT_60)):
        got = F.FLAP_SHIFT_60 if fps >= 60 else F.FLAP_SHIFT_30
        assert got == shift
        openings = fps / (2 << shift)
        assert 3.0 <= openings <= 5.0, ('%g Hz gives %.2f openings a second'
                                        % (fps, openings))


def test_the_displaced_words_are_position_independent():
    """
    A cave replays the word it displaced from a reclaimed hole somewhere else
    in .text, so an adrp, adr or branch would compute a different address
    there than it did at the site.
    """
    for _n, _site, orig, what in F.SITES:
        assert (orig & 0x1F000000) != 0x10000000, '%s displaces adrp/adr' % what
        assert (orig & 0x7C000000) != 0x14000000, '%s displaces a branch' % what


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


if __name__ == '__main__':
    sys.exit(_main())
