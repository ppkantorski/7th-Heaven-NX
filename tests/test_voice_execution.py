"""Execute voice caves, including scattered branches and hostile callees.

Native audio calls are stubs: these tests prove register/layout contracts,
not that the console's decoder or scheduler accepts a clip.

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
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import arm64emu as EM
import ff7nx_cave as C
import ff7nx_voice as V

BSS = 0x3FEF65C                 # the actual, four-byte-aligned build-313 base
ENTRY = 0x600000
PLAYER = 0x720000000
DECODER = 0x720010000
CONTEXT = 0x710000000


def laid_out(builder, scattered):
    probe = builder(ENTRY, lambda i: ENTRY + i * 4)
    if not scattered:
        return ENTRY, {ENTRY + i * 4: w for i, w in enumerate(probe)}
    runs = [(ENTRY + i * 0x100, 3) for i in range((len(probe) + 1) // 2)]
    slots = C.slots(runs, len(probe))
    words = builder(slots[0], lambda i: slots[i])
    return slots[0], C.link(runs, words)


def hostile(result=0):
    def call(cpu):
        for reg in range(19):
            cpu.x[reg] = EM.GARBAGE
        cpu.x[30] = EM.GARBAGE
        cpu.x[0] = result
        cpu.n, cpu.z, cpu.c, cpu.v = 1, 0, 0, 1
    return call


class GuardedMem(EM.Mem):
    def u(self, address, size):
        if V.MUSIC_MANAGER_HOLDER <= address < V.MUSIC_MANAGER_HOLDER + 8:
            raise AssertionError('disabled ducking dereferenced MusicManager')
        return super().u(address, size)


class VoiceCpu(EM.Cpu):
    """Voice-only forms not needed by the shared dispatcher interpreter."""
    def step(self, word, pc):
        rd, rn, rm = word & 31, (word >> 5) & 31, (word >> 16) & 31
        if word == V.MAPJUMP_ORIG:  # str x25,[sp,#-0x50]!
            self.sp -= 0x50
            self.mem.setu(self.sp, self.x[25], 8)
            return None
        if word & 0xFFE01FE0 == 0x1E201000:  # FMOV S0,#imm
            # ANY encodable immediate, not just 1.0: the voice playback gain
            # is a setting now, and an interpreter that only knew 1.0 would
            # fail on exactly the builds worth testing.
            imm8 = (word >> 13) & 0xFF
            a, b = (imm8 >> 7) & 1, (imm8 >> 6) & 1
            self.fp[rd] = ((a << 31) | ((b ^ 1) << 30)
                           | ((0x1F if b else 0) << 25)
                           | ((imm8 & 0x3F) << 19))
            return None
        if word & 0xFFE0FC00 == 0x9AC00800:  # UDIV Xd,Xn,Xm
            divisor = self.get(rm)
            self.set(rd, self.get(rn) // divisor if divisor else 0)
            return None
        if word & 0xFFE0FC00 == 0x8B204000:  # ADD Xd,Xn,Wm,UXTW
            self.set(rd, self.get(rn) + self.get(rm, True))
            return None
        if word == 0x1E2703E0:  # FMOV S0,WZR
            self.fp[0] = 0
            return None
        if word & 0xFFFFFC1F == 0xD63F0000:  # BLR Xn
            # the player's own vtable, reached by the retirement join. It is a
            # native callee like any other here: hostile on return.
            hostile()(self)
            return None
        return super().step(word, pc)


class Execution(unittest.TestCase):
    def cpu(self, memory, native=None):
        cpu = VoiceCpu(memory, native=native)
        cpu.x = [0x730000000 + i * 0x100 for i in range(32)]
        cpu.x[25] = CONTEXT
        cpu.sp = 0x740001000
        return cpu

    def test_compact_bss_never_crosses_the_composed_final_page(self):
        # Cosmo Memory's preceding bridges leave this exact raw BSS end.
        # Horizon maps its remaining page slack, but build 325 proved that a
        # VOICE_PENDING_AUTO write in the following page aborts on hardware.
        old_bss_end = 0x3FEF65C
        scratch = (old_bss_end + 15) & ~15
        page_end = (old_bss_end + 0xFFF) & ~0xFFF
        self.assertLessEqual(scratch + V.VOICE_SCRATCH_BYTES, page_end)
        # BUILD 452: the replay guard added NO BSS -- it reuses VOICE_PAGE's
        # unreachable tail -- so the block still ends at the world dialog table
        # and the page-guard slack is unchanged.
        self.assertEqual(V.VOICE_WORLD_DIALOG_OFF + 0x100,
                         V.VOICE_SCRATCH_BYTES)

    def test_the_replay_guard_state_sits_in_unreachable_page_bytes(self):
        """
        The guard's two words live in `VOICE_PAGE`'s tail, which is only safe
        because no field uses a window id that high.
        `audit_voice_windows.py` walked every MESSAGE and ASK site in all 702
        Echo-S fields: the window ids are 0, 1, 2 and 3.
        """
        for off in (V.VOICE_ACTIVE_KEY_HASH_OFF, V.VOICE_REPLAY_GUARD_OFF):
            # beyond any window id a field can index
            self.assertGreaterEqual(off - V.VOICE_PAGE_OFF, 64)
            # and not off the end of VOICE_PAGE into the next record
            self.assertLessEqual(off + 4, V.VOICE_PAGE_OFF + 256)
        self.assertNotEqual(V.VOICE_ACTIVE_KEY_HASH_OFF,
                            V.VOICE_REPLAY_GUARD_OFF)
        self.assertLess(V.VOICE_PENDING_AUTO_OFF + 4,
                        V.VOICE_SCRATCH_BYTES)
        self.assertLess(V.BATTLE_TEXT_LAST_OFF + 4,
                        V.VOICE_SCRATCH_BYTES)

    def test_disabled_duck_service_preserves_field_state_idle_and_create(self):
        for scattered in (False, True):
            for play in (False, True):
                for duck_state in (0, 2, 3):
                    with self.subTest(scattered=scattered, play=play,
                                      duck_state=duck_state):
                        mem = GuardedMem()
                        mem.setu(BSS + V.VOICE_BGM_DUCKED_OFF, duck_state, 4)
                        mem.setu(CONTEXT + 0x10, 0x100100, 4)
                        mem.setu(CONTEXT + 0x14, 0x100200, 4)
                        original_context = mem.read(CONTEXT, 0x30)
                        native = {address: hostile() for address in (
                            V.OGG_WORKDIR, V.OGG_PATH_FORMAT,
                            V.OGG_DECODER_DELETE, V.OGG_PLAY, V.OGG_VOLUME)}
                        native[V.OGG_OPEN] = hostile(DECODER)
                        native[V.MALLOC] = hostile(PLAYER)
                        native[V.HOST_TICK_NOW] = hostile(1000)
                        native[V.HOST_TICK_FREQUENCY] = hostile(19200000)

                        def constructor(cpu):
                            self.assertEqual(cpu.x[0], PLAYER)
                            self.assertEqual(cpu.x[1], PLAYER + V.PLAYER_NAME_OFF)
                            mem.setu(PLAYER + 0x60, DECODER, 8)
                            mem.setu(DECODER, 48000, 4)
                            mem.setu(DECODER + 4, 48000, 4)
                            hostile()(cpu)
                        native[V.OGG_CTOR] = constructor
                        if play:
                            mem.write(BSS + V.VOICE_KEY_OFF, b'md1stin/0a\0')
                            mem.setu(BSS + V.VOICE_PENDING_CMD_OFF,
                                     V.VOICE_CMD_PLAY, 4)
                            mem.setu(BSS + V.VOICE_PENDING_OWNER_OFF, 2, 4)
                        cpu = self.cpu(mem, native)
                        before, stack = list(cpu.x), cpu.sp
                        entry, code = laid_out(
                            lambda at, addr: V._build_field_clocked_voice_service(
                                at, addr, BSS, duck_percent=100), scattered)
                        self.assertEqual(cpu.run(entry, code), V.FIELD_HOOK + 4)
                        self.assertEqual(cpu.x[19:31], before[19:31])
                        self.assertEqual(cpu.sp, stack)
                        self.assertEqual(cpu.x[8:10], [0x100100, 0x100200])
                        self.assertEqual(mem.read(CONTEXT, 0x30), original_context)
                        self.assertEqual(mem.u(BSS + V.VOICE_PLAYER_OFF, 8),
                                         PLAYER if play else 0)

    def test_message_key_builder_preserves_translated_registers_and_context(self):
        for scattered in (False, True):
            for field in (b'md1stin\0', b'field\\md1stin.dat\0'):
                with self.subTest(scattered=scattered, field=field):
                    mem = EM.Mem()
                    cpu = self.cpu(mem, {V.MESSAGE_PRE_TARGET: hostile()})
                    cpu.x[21] = CONTEXT
                    guest = cpu.host_base
                    mem.setu(guest + V.CURRENT_WINDOW, 1, 1)
                    mem.setu(guest + V.SCRIPT_CURSOR + 2, 0, 2)
                    mem.setu(guest + V.FIELD_SCRIPT_PTR, 0x200000, 4)
                    mem.write(guest + 0x200000, bytes((0x40, 2, 17)))
                    mem.setu(guest + V.MESSAGE_STATE + 2 * 48, 1, 2)
                    mem.write(guest + V.FIELD_FILE_NAME, field)
                    mem.write(CONTEXT, bytes(range(0x30)))
                    before, stack = list(cpu.x), cpu.sp
                    entry, code = laid_out(
                        lambda at, addr: V._build_message_command_cave(
                            at, addr, BSS, key_mode='folder',
                            resume_address=V.MESSAGE_HOOK,
                            resume_call_target=V.MESSAGE_PRE_TARGET), scattered)
                    self.assertEqual(cpu.run(entry, code), V.MESSAGE_HOOK)
                    self.assertEqual(cpu.x[19:30], before[19:30])
                    self.assertEqual(cpu.sp, stack)
                    self.assertEqual(mem.read(CONTEXT, 0x30), bytes(range(0x30)))
                    self.assertEqual(mem.read(BSS + V.VOICE_KEY_OFF, 12),
                                     b'md1stin/17a\0')
                    self.assertEqual(mem.u(BSS + V.VOICE_PENDING_OWNER_OFF, 4), 2)
                    self.assertEqual(mem.u(BSS + V.VOICE_PENDING_CMD_OFF, 4),
                                     V.VOICE_CMD_PLAY)

    def test_footprint_stub_uses_every_full_producer_run_but_changes_nothing(self):
        def full(at, addr):
            return V._build_message_command_cave(
                at, addr, BSS, key_mode='folder',
                resume_instruction=V.MESSAGE_ORIG,
                resume_address=V.MESSAGE_HOOK + 4)

        logical_words = len(full(ENTRY, lambda i: ENTRY + i * 4))

        def footprint(at, addr):
            return V._build_message_stub_cave(
                at, addr, BSS, footprint_words=logical_words,
                resume_instruction=V.MESSAGE_ORIG,
                resume_address=V.MESSAGE_HOOK + 4)

        full_entry, full_code = laid_out(full, True)
        stub_entry, stub_code = laid_out(footprint, True)
        self.assertEqual(stub_entry, full_entry)
        self.assertEqual(set(stub_code), set(full_code))
        self.assertEqual(len(stub_code), len(full_code))

        mem = EM.Mem()
        mem.setu(CONTEXT + 0x10, 0x100100, 4)
        before_context = mem.read(CONTEXT, 0x30)
        cpu = self.cpu(mem)
        cpu.x[21] = CONTEXT
        before, stack = list(cpu.x), cpu.sp
        self.assertEqual(cpu.run(stub_entry, stub_code), V.MESSAGE_HOOK + 4)
        # The post-phase displaced instruction is intentionally replayed:
        # ldr w8,[x21,#0x10]. Everything else remains untouched.
        self.assertEqual(cpu.x[8], 0x100100)
        self.assertEqual(cpu.x[:8], before[:8])
        self.assertEqual(cpu.x[9:], before[9:])
        self.assertEqual(cpu.sp, stack)
        self.assertEqual(mem.read(CONTEXT, 0x30), before_context)

    def test_transition_stub_runs_state_machine_without_publishing(self):
        for scattered in (False, True):
            with self.subTest(scattered=scattered):
                mem = EM.Mem()
                cpu = self.cpu(mem)
                cpu.x[21] = CONTEXT
                guest = cpu.host_base
                mem.setu(guest + V.CURRENT_WINDOW, 1, 1)
                mem.setu(guest + V.SCRIPT_CURSOR + 2, 0, 2)
                mem.setu(guest + V.FIELD_SCRIPT_PTR, 0x200000, 4)
                mem.write(guest + 0x200000, bytes((0x40, 2, 17)))
                mem.setu(guest + V.MESSAGE_STATE + 2 * 48, 1, 2)
                mem.setu(CONTEXT + 0x10, 0x100100, 4)

                entry, code = laid_out(
                    lambda at, addr: V._build_message_stub_cave(
                        at, addr, BSS, save=True, reads=True, tables=True,
                        transitions=True, resume_instruction=V.MESSAGE_ORIG,
                        resume_address=V.MESSAGE_HOOK + 4), scattered)
                self.assertEqual(cpu.run(entry, code), V.MESSAGE_HOOK + 4)
                # Window 2 has transitioned from the zero-filled old state
                # to state 1.  It resets page zero and records that new state.
                self.assertEqual(mem.u(BSS + V.VOICE_PAGE_OFF + 2, 1), 0)
                self.assertEqual(
                    mem.u(BSS + V.VOICE_LAST_OPCODE_OFF + 2 * 3, 2), 1)
                # The diagnostic must not manufacture a request or filename.
                self.assertEqual(mem.u(BSS + V.VOICE_PENDING_CMD_OFF, 4), 0)
                self.assertEqual(mem.read(BSS + V.VOICE_KEY_OFF, 16), b'\0' * 16)

    def test_message_publication_without_a_key_writer(self):
        for scattered in (False, True):
            with self.subTest(scattered=scattered):
                mem = EM.Mem()
                cpu = self.cpu(mem)
                cpu.x[21] = CONTEXT
                guest = cpu.host_base
                mem.setu(guest + V.CURRENT_WINDOW, 1, 1)
                mem.setu(guest + V.SCRIPT_CURSOR + 2, 0, 2)
                mem.setu(guest + V.FIELD_SCRIPT_PTR, 0x200000, 4)
                mem.write(guest + 0x200000, bytes((0x40, 2, 17)))
                mem.setu(guest + V.MESSAGE_STATE + 2 * 48, 1, 2)
                mem.setu(CONTEXT + 0x10, 0x100100, 4)

                entry, code = laid_out(
                    lambda at, addr: V._build_message_command_cave(
                        at, addr, BSS, key_mode='folder',
                        construct_key=False, allow_auto_advance=False,
                        resume_instruction=V.MESSAGE_ORIG,
                        resume_address=V.MESSAGE_HOOK + 4), scattered)
                self.assertEqual(cpu.run(entry, code), V.MESSAGE_HOOK + 4)
                self.assertEqual(mem.u(BSS + V.VOICE_PENDING_OWNER_OFF, 4), 2)
                self.assertEqual(mem.u(BSS + V.VOICE_PENDING_AUTO_OFF, 4), 0)
                self.assertEqual(mem.u(BSS + V.VOICE_PENDING_CMD_OFF, 4),
                                 V.VOICE_CMD_PLAY)
                self.assertEqual(mem.read(BSS + V.VOICE_KEY_OFF, 16), b'\0' * 16)

    def test_message_close_path_without_a_mailbox_write(self):
        for scattered in (False, True):
            with self.subTest(scattered=scattered):
                mem = EM.Mem()
                cpu = self.cpu(mem)
                cpu.x[21] = CONTEXT
                guest = cpu.host_base
                mem.setu(guest + V.CURRENT_WINDOW, 1, 1)
                mem.setu(guest + V.SCRIPT_CURSOR + 2, 0, 2)
                mem.setu(guest + V.FIELD_SCRIPT_PTR, 0x200000, 4)
                mem.write(guest + 0x200000, bytes((0x40, 2, 17)))
                mem.setu(guest + V.MESSAGE_STATE + 2 * 48, 7, 2)
                mem.setu(BSS + V.VOICE_LAST_OPCODE_OFF + 2 * 3, 1, 2)
                mem.setu(BSS + V.VOICE_PENDING_CMD_OFF, V.VOICE_CMD_PLAY, 4)
                mem.setu(BSS + V.VOICE_PENDING_OWNER_OFF, 2, 4)
                mem.setu(CONTEXT + 0x10, 0x100100, 4)

                entry, code = laid_out(
                    lambda at, addr: V._build_message_command_cave(
                        at, addr, BSS, key_mode='folder', construct_key=False,
                        publication_mask=0, allow_auto_advance=False,
                        resume_instruction=V.MESSAGE_ORIG,
                        resume_address=V.MESSAGE_HOOK + 4), scattered)
                self.assertEqual(cpu.run(entry, code), V.MESSAGE_HOOK + 4)
                # A matching pending PLAY reaches queue_stop, but this mask
                # deliberately omits the otherwise-normal STOP store.
                self.assertEqual(mem.u(BSS + V.VOICE_PENDING_CMD_OFF, 4),
                                 V.VOICE_CMD_PLAY)


if __name__ == '__main__':
    unittest.main()


class DecimalComponent(unittest.TestCase):
    """
    The dialog id in `<field>/<dialog><page>.ogg`, executed for every value.

    This was wrong for 100..109 and nothing else, which is why it survived so
    long: almost every field has fewer than a hundred dialogues. On the
    Highwind (fship_25, 201 of them) Tifa's dialog 108 asked for `18a.ogg` and
    got Barret.
    """

    BUF = 0x700000
    AT = 0x600000

    class Asm:
        def __init__(self, base):
            self.base, self.words, self.labels, self.fix = base, [], {}, []

        def pc(self):
            return self.base + len(self.words) * 4

        def emit(self, word):
            self.words.append(word)

        def label(self, name):
            self.labels[name] = self.pc()

        def bcond(self, name, cond):
            self.fix.append((len(self.words), name, cond))
            self.words.append(0)

        def b(self, name):
            self.fix.append((len(self.words), name, None))
            self.words.append(0)

        def link(self):
            import a64 as A
            for index, name, cond in self.fix:
                here, target = self.base + index * 4, self.labels[name]
                self.words[index] = (A.b(here, target) if cond is None
                                     else A.bcond(here, target, cond))
            return self.words

    def render(self, value):
        import a64 as A
        a = self.Asm(self.AT)
        a.emit(A.movz(28, value))
        V._emit_append_decimal(a, 28, 'component')
        words = a.link()
        memory = EM.Mem()
        cpu = VoiceCpu(memory)
        cpu.x = [0] * 32
        cpu.x[1] = self.BUF
        cpu.x[30] = 0
        cpu.run(self.AT, words)
        return memory.read(self.BUF, cpu.x[1] - self.BUF).decode()

    def test_every_value_a_field_can_hold(self):
        wrong = [(v, self.render(v)) for v in range(256)
                 if self.render(v) != str(v)]
        self.assertEqual(wrong, [])

    def test_a_zero_in_the_tens_place_is_printed(self):
        """The regression itself: 108 is "108", never "18"."""
        self.assertEqual(self.render(108), '108')
        self.assertEqual(self.render(109), '109')
        self.assertEqual(self.render(100), '100')

    def test_a_leading_zero_is_still_suppressed(self):
        self.assertEqual(self.render(8), '8')
        self.assertEqual(self.render(0), '0')


class SameTickBurst(Execution):
    """
    Three entities publishing in one script tick, and only one slot.

    `chrin_1b`: Reno tells the grunts not to step on the flowers and all three
    answer at once. Each is its own ENTITY whose whole routine is
    `WINDOW n ; MESSAGE n,d ; RET`, so all three run in the same tick. Echo-S
    voices only the first -- `75a.ogg` is 2.85 s and holds all three voices --
    and publishing "last wins" handed the slot to a window with no clip, so the
    scene was silent. `SEVENTH_NX_VOICE_FOREIGN` could not help: every setting
    there decides what a foreign window does to an AUDIBLE line, and nothing
    was audible yet.
    """

    def _publish(self, mem, cpu, window, dialog, burst):
        guest = cpu.host_base
        mem.setu(guest + V.CURRENT_WINDOW, window, 1)
        mem.setu(guest + V.SCRIPT_CURSOR + window * 2, 0, 2)
        mem.setu(guest + V.FIELD_SCRIPT_PTR, 0x200000, 4)
        mem.write(guest + 0x200000, bytes((0x40, window, dialog)))
        mem.setu(guest + V.MESSAGE_STATE + window * 48, 1, 2)
        mem.write(guest + V.FIELD_FILE_NAME, b'chrin_1b\0')
        entry, code = laid_out(
            lambda at, addr: V._build_message_command_cave(
                at, addr, BSS, key_mode='folder',
                first_in_burst_wins=burst,
                resume_address=V.MESSAGE_HOOK,
                resume_call_target=V.MESSAGE_PRE_TARGET), False)
        self.assertEqual(cpu.run(entry, code), V.MESSAGE_HOOK)

    def _run(self, burst):
        mem = EM.Mem()
        cpu = self.cpu(mem, {V.MESSAGE_PRE_TARGET: hostile()})
        cpu.x[21] = CONTEXT
        for window, dialog in ((1, 75), (2, 76), (3, 77)):
            self._publish(mem, cpu, window, dialog, burst)
        key = bytes(mem.read(BSS + V.VOICE_KEY_OFF, 20)).split(b'\0')[0]
        return key.decode(), mem.u(BSS + V.VOICE_PENDING_OWNER_OFF, 4)

    def test_the_first_window_of_a_burst_keeps_the_slot(self):
        key, owner = self._run(True)
        self.assertEqual(key, 'chrin_1b/75a')
        self.assertEqual(owner, 1)

    def test_last_wins_is_what_lost_the_scene(self):
        """The old behaviour, kept selectable, and the reason it was silent."""
        key, owner = self._run(False)
        self.assertEqual(key, 'chrin_1b/77a')
        self.assertEqual(owner, 3)

    def test_a_page_advance_on_the_SAME_window_still_wins(self):
        """
        The guard must not freeze a conversation: a new line on the window that
        already owns the pending request is a page advance, and the newest is
        the right one.
        """
        mem = EM.Mem()
        cpu = self.cpu(mem, {V.MESSAGE_PRE_TARGET: hostile()})
        cpu.x[21] = CONTEXT
        self._publish(mem, cpu, 1, 75, True)
        self._publish(mem, cpu, 1, 76, True)
        key = bytes(mem.read(BSS + V.VOICE_KEY_OFF, 20)).split(b'\0')[0]
        self.assertEqual(key.decode(), 'chrin_1b/76a')
        self.assertEqual(mem.u(BSS + V.VOICE_PENDING_OWNER_OFF, 4), 1)

    def test_once_the_service_takes_the_request_a_new_window_publishes(self):
        """
        The guard is live only inside a burst. Clearing PENDING_CMD is exactly
        what the service does when it constructs the player, and after that a
        foreign window must be able to publish again -- otherwise the first
        line of a scene would own the slot forever.
        """
        mem = EM.Mem()
        cpu = self.cpu(mem, {V.MESSAGE_PRE_TARGET: hostile()})
        cpu.x[21] = CONTEXT
        self._publish(mem, cpu, 1, 75, True)
        mem.setu(BSS + V.VOICE_PENDING_CMD_OFF, 0, 4)      # service consumed it
        self._publish(mem, cpu, 2, 76, True)
        key = bytes(mem.read(BSS + V.VOICE_KEY_OFF, 20)).split(b'\0')[0]
        self.assertEqual(key.decode(), 'chrin_1b/76a')
        self.assertEqual(mem.u(BSS + V.VOICE_PENDING_OWNER_OFF, 4), 2)


class TheLineThatJustPlayedIsNotRebuilt(Execution):
    """
    chrin_1b, and what four earlier attempts all missed -- because all four
    guarded the PUBLISH side and the recording of the scene says the duplicate
    is built at the DRAIN.

    `repeated_dialogue.mp4`, matched by cross-correlation against the staged
    clips rather than by listening:

        chrin_1b/74.ogg  (Reno)    -0.67s .. 2.24s   natural end
        chrin_1b/75.ogg  (grunts)   2.49s .. 5.35s   natural end, complete
        chrin_1b/75.ogg  AGAIN      5.44s ..         cut off by the map change

    0.09 seconds -- five or six frames, which is `completed` -> `commands` ->
    `create`. So the fact to act on is not which window published what: it is
    that AT THE MOMENT A PLAYER RETIRES, THE REQUEST IN THE MAILBOX IS THE LINE
    THAT JUST PLAYED. These tests set up exactly that measured state and drive
    the real service cave through it.

    448 compared the per-window dialog record, which cannot see a field change
    and swallowed the next scene's line. 449 compared the retiring player's own
    filename, which lives in an allocation it then frees. 451 kept one global
    last-published key, which the other two speakers overwrote. 452 made that
    per window, which cannot see a duplicate that arrives on a different
    window -- and, worse, rewrote the shared key buffer before refusing, so a
    refusal left the mailbox describing a request nobody made.
    """

    STAGED = ('chrin_1b/75a', 'chrin_1b/90a', 'nextfld/75a')

    def _service(self, replay_guard, clip_frames=12):
        mem = EM.Mem()
        state = {'creates': [], 'left': None, 'frame': 0}

        def key():
            return bytes(mem.read(BSS + V.VOICE_KEY_OFF, 32)).split(
                b'\0')[0].decode('latin-1')

        def opened(cpu):
            hostile(DECODER if key() in self.STAGED else 0)(cpu)

        def ctor(cpu):
            mem.setu(PLAYER + 0x60, DECODER, 8)
            mem.setu(DECODER, 48000, 4)
            mem.setu(DECODER + 4, 48000, 4)
            mem.setu(PLAYER + 0x98, 0, 4)
            mem.setu(PLAYER + 0x08, PLAYER + 0x200, 8)
            mem.setu(PLAYER + 0x200, PLAYER + 0x300, 8)
            mem.setu(PLAYER + 0x318, V.MESSAGE_PRE_TARGET, 8)
            state['creates'].append(key())
            state['left'] = clip_frames
            hostile()(cpu)

        def delete(cpu):
            state['left'] = None
            hostile()(cpu)

        native = {a: hostile() for a in (
            V.OGG_WORKDIR, V.OGG_PATH_FORMAT, V.OGG_DECODER_DELETE,
            V.OGG_PLAY, V.OGG_VOLUME, V.MESSAGE_PRE_TARGET)}
        native[V.OGG_OPEN] = opened
        native[V.MALLOC] = hostile(PLAYER)
        native[V.HOST_TICK_NOW] = hostile(1000)
        native[V.HOST_TICK_FREQUENCY] = hostile(19200000)
        native[V.OGG_CTOR] = ctor
        native[V.OGG_DELETE] = delete
        cpu = self.cpu(mem, native)
        cpu.x[21] = CONTEXT
        mem.setu(CONTEXT + 0x10, 0x100100, 4)
        mem.setu(CONTEXT + 0x14, 0x100200, 4)
        entry, code = laid_out(
            lambda at, addr: V._build_field_clocked_voice_service(
                at, addr, BSS, duck_percent=100, auto_advance=False,
                defer_foreign_play=True, replay_guard=replay_guard), False)

        def tick():
            if state['left'] is not None:
                if mem.u(PLAYER + 0x98, 4) == 8:
                    mem.setu(PLAYER + 0x98, 1, 4)
                else:
                    state['left'] -= 1
                    if state['left'] <= 0:
                        mem.setu(PLAYER + 0x98, 1, 4)
            cpu.run(entry, code)

        return mem, tick, state

    def _held(self, pending, replay_guard, ticks=40, first='chrin_1b/75a'):
        """Play `first`, then hold `pending` in the mailbox until it retires."""
        mem, tick, state = self._service(replay_guard)
        mem.write(BSS + V.VOICE_KEY_OFF,
                  first.encode() + b'\0' * (32 - len(first)))
        mem.setu(BSS + V.VOICE_PENDING_CMD_OFF, V.VOICE_CMD_PLAY, 4)
        mem.setu(BSS + V.VOICE_PENDING_OWNER_OFF, 1, 4)
        tick()
        # a second window's request, deferred: the command stays PLAY and
        # unconsumed for the whole of the first clip
        mem.write(BSS + V.VOICE_KEY_OFF,
                  pending.encode() + b'\0' * (32 - len(pending)))
        mem.setu(BSS + V.VOICE_PENDING_CMD_OFF, V.VOICE_CMD_PLAY, 4)
        mem.setu(BSS + V.VOICE_PENDING_OWNER_OFF, 2, 4)
        for _ in range(ticks):
            tick()
        return state['creates']

    def test_the_measured_bug_reproduces_without_the_guard(self):
        """
        A test that only passes after the fix proves nothing about the fix.
        This is the same harness with the guard off, and it must show the
        defect the recording shows.
        """
        self.assertEqual(self._held('chrin_1b/75a', False),
                         ['chrin_1b/75a', 'chrin_1b/75a'])

    def test_the_line_that_just_retired_is_refused(self):
        for mode in ('window', 'always'):
            with self.subTest(mode=mode):
                self.assertEqual(self._held('chrin_1b/75a', mode),
                                 ['chrin_1b/75a'])

    def test_always_needs_no_arming_at_all(self):
        """
        THE reason 455 exists. `window` only refuses while a counter armed at
        RETIREMENT is running, and the field callback may not run at all across
        a map change -- so the retirement is never observed, the counter is
        zero, and the duplicate walks through. `always` has no counter to miss.
        """
        mem, tick, state = self._service('always')
        mem.write(BSS + V.VOICE_KEY_OFF, b'chrin_1b/75a\0' + b'\0' * 19)
        mem.setu(BSS + V.VOICE_PENDING_CMD_OFF, V.VOICE_CMD_PLAY, 4)
        mem.setu(BSS + V.VOICE_PENDING_OWNER_OFF, 1, 4)
        tick()
        # the player is torn down WITHOUT the service ever seeing it -- the
        # shape of a map change
        mem.setu(BSS + V.VOICE_PLAYER_OFF, 0, 8)
        mem.setu(BSS + V.VOICE_REPLAY_GUARD_OFF, 0, 4)
        mem.setu(BSS + V.VOICE_PENDING_CMD_OFF, V.VOICE_CMD_PLAY, 4)
        for _ in range(120):
            tick()
        self.assertEqual(state['creates'], ['chrin_1b/75a'])

    def test_window_mode_is_exactly_what_that_skips(self):
        """The negative control for the test above, so the difference between
        the two modes is measured and not asserted."""
        mem, tick, state = self._service('window')
        mem.write(BSS + V.VOICE_KEY_OFF, b'chrin_1b/75a\0' + b'\0' * 19)
        mem.setu(BSS + V.VOICE_PENDING_CMD_OFF, V.VOICE_CMD_PLAY, 4)
        mem.setu(BSS + V.VOICE_PENDING_OWNER_OFF, 1, 4)
        tick()
        mem.setu(BSS + V.VOICE_PLAYER_OFF, 0, 8)
        mem.setu(BSS + V.VOICE_REPLAY_GUARD_OFF, 0, 4)
        mem.setu(BSS + V.VOICE_PENDING_CMD_OFF, V.VOICE_CMD_PLAY, 4)
        for _ in range(120):
            tick()
        self.assertEqual(state['creates'],
                         ['chrin_1b/75a', 'chrin_1b/75a'])

    def test_a_different_line_pending_at_the_same_moment_still_plays(self):
        """The thing this must not become: a guard that swallows the next
        speaker. Only the identical key is refused."""
        self.assertEqual(self._held('chrin_1b/90a', 'always'),
                         ['chrin_1b/75a', 'chrin_1b/90a'])

    def test_the_same_dialog_in_the_NEXT_field_still_plays(self):
        """What build 448 broke. The key carries the field name."""
        self.assertEqual(self._held('nextfld/75a', 'always'),
                         ['chrin_1b/75a', 'nextfld/75a'])

    def test_the_guard_expires_so_an_npc_can_be_asked_again(self):
        """
        A debounce, not a ban. It is armed for VOICE_REPLAY_GUARD_FRAMES
        service ticks; after that the same line plays again, which is what
        walking back to an NPC has to do.
        """
        mem, tick, state = self._service('window')
        for round_ in range(2):
            mem.write(BSS + V.VOICE_KEY_OFF, b'chrin_1b/75a' + b'\0' * 20)
            mem.setu(BSS + V.VOICE_PENDING_CMD_OFF, V.VOICE_CMD_PLAY, 4)
            mem.setu(BSS + V.VOICE_PENDING_OWNER_OFF, 1, 4)
            for _ in range(V.VOICE_REPLAY_GUARD_FRAMES + 20):
                tick()
        self.assertEqual(state['creates'],
                         ['chrin_1b/75a', 'chrin_1b/75a'])

    def test_an_intervening_line_disarms_it(self):
        """
        `convil_2` repeats dialog 49 nine times and every pair has another
        line between them. The guard remembers only the LAST key built, so an
        intervening line is what lets the repeat through.
        """
        mem, tick, state = self._service('always')
        for name in ('chrin_1b/75a', 'chrin_1b/90a', 'chrin_1b/75a'):
            mem.write(BSS + V.VOICE_KEY_OFF,
                      name.encode() + b'\0' * (32 - len(name)))
            mem.setu(BSS + V.VOICE_PENDING_CMD_OFF, V.VOICE_CMD_PLAY, 4)
            mem.setu(BSS + V.VOICE_PENDING_OWNER_OFF, 1, 4)
            for _ in range(20):
                tick()
        self.assertEqual(state['creates'],
                         ['chrin_1b/75a', 'chrin_1b/90a', 'chrin_1b/75a'])

    def test_a_same_window_replacement_while_it_is_still_speaking_is_not_refused(self):
        """
        The counter is zero while the player is alive, so a page advance or a
        new line on the owning window still takes over immediately. That is
        the behaviour the runtime is built on and the guard must not touch it.
        """
        mem, tick, state = self._service('always', clip_frames=1000)
        for name in ('chrin_1b/75a', 'chrin_1b/90a'):
            mem.write(BSS + V.VOICE_KEY_OFF,
                      name.encode() + b'\0' * (32 - len(name)))
            mem.setu(BSS + V.VOICE_PENDING_CMD_OFF, V.VOICE_CMD_PLAY, 4)
            mem.setu(BSS + V.VOICE_PENDING_OWNER_OFF, 1, 4)
            for _ in range(6):
                tick()
        self.assertEqual(state['creates'],
                         ['chrin_1b/75a', 'chrin_1b/90a'])

    def test_the_world_and_battle_services_never_carry_it(self):
        """Build 449 put a guard in the shared builder and silenced the game.
        The builder refuses the flag for those two rather than trusting the
        call sites."""
        for kw in ({'world_mode': True}, {'battle_mode': True}):
            off = V._build_field_clocked_voice_service(
                0, lambda i: i * 4, BSS, **kw)
            on = V._build_field_clocked_voice_service(
                0, lambda i: i * 4, BSS, replay_guard='always', **kw)
            self.assertEqual(off, on, kw)


class AVoiceClipIsNotMusicAndMustNotStreamForever(Execution):
    """
    The chrin_1b boundary case and the build-456 regression.

    `NativeOggPlayer` is this port's MUSIC streamer. Its worker at 0x3474 is
    the only place a stream can end, and it is skipped outright when the
    decoder's +0x18 is set:

        0x34AC  ldr  x9, [x19, #0x60]     ; the decoder
        0x34B0  ldr  w10, [x9, #0x18]
        0x34B4  cbnz w10, 0x34F4          ; <- never look for the end
        ...     remaining = total_samples * bytes_per_sample - fed
        0x34D4  b.ls 0x34F4
        0x34D8  strb wzr, [x19, #0x50]    ; past the end: stop feeding

    That bypass is how the music loops, and our clips are opened by the same
    factory (`OGG_OPEN`) with the same flag. Every voice line in the game has
    been looping; it was invisible because dismissing a box publishes STOP and
    the next line replaces the player, so no clip had ever reached its own end
    untouched. In chrin_1b the boxes are never dismissed -- Reno's entity REQs
    the three grunts and then does its own WAIT and MAPJUMP, and the boxes fade
    out with the screen. The recording shows 75.ogg running its full 2.851s and
    the same file restarting 0.09s later.

    Build 456 tried to turn the decoder into a non-looping decoder by mutating
    both its live state and its rewind image. That froze normal dialogue.
    Build 458 then proved the Switch treats the Ogg loop tags as a boolean and
    still rewinds to the beginning. The decoder remains untouched. MAPJUMP now
    latches the real boundary, and only the private field voice is diverted
    through the worker's existing renderer-stop path.
    """

    def _create(self, **kw):
        """Run the service through a successful create; return the decoder."""
        mem = EM.Mem()
        native = {a: hostile() for a in (
            V.OGG_WORKDIR, V.OGG_PATH_FORMAT, V.OGG_DECODER_DELETE,
            V.OGG_PLAY, V.OGG_VOLUME)}
        native[V.OGG_OPEN] = hostile(DECODER)
        native[V.MALLOC] = hostile(PLAYER)
        native[V.HOST_TICK_NOW] = hostile(1000)
        native[V.HOST_TICK_FREQUENCY] = hostile(19200000)

        def ctor(cpu):
            mem.setu(PLAYER + 0x60, DECODER, 8)
            mem.setu(DECODER, 48000, 4)          # total samples
            mem.setu(DECODER + 4, 48000, 4)      # sample rate
            # A loop tag remains required; its final-sample bounds live in the
            # Ogg comments and do not change this parsed boolean.
            mem.setu(DECODER + 0x18, 1, 4)
            hostile()(cpu)
        native[V.OGG_CTOR] = ctor
        mem.setu(CONTEXT + 0x10, 0x100100, 4)
        mem.setu(CONTEXT + 0x14, 0x100200, 4)
        mem.write(BSS + V.VOICE_KEY_OFF, b'chrin_1b/75a\0')
        mem.setu(BSS + V.VOICE_PENDING_CMD_OFF, V.VOICE_CMD_PLAY, 4)
        mem.setu(BSS + V.VOICE_PENDING_OWNER_OFF, 1, 4)
        cpu = self.cpu(mem, native)
        entry, code = laid_out(
            lambda at, addr: V._build_field_clocked_voice_service(
                at, addr, BSS, duck_percent=100, **kw), False)
        cpu.run(entry, code)        # the resume target differs per mode
        self.assertEqual(mem.u(BSS + V.VOICE_PLAYER_OFF, 8), PLAYER)
        return mem

    def test_the_decoder_loop_flag_is_not_mutated(self):
        for mode in ({}, {'world_mode': True}, {'battle_mode': True}):
            with self.subTest(**mode):
                mem = self._create(**mode)
                self.assertEqual(mem.u(DECODER + 0x18, 4), 1)

    def test_it_leaves_the_fields_the_runtime_actually_reads(self):
        """+0x00 and +0x04 feed the host deadline; only +0x18 may change."""
        mem = self._create()
        self.assertEqual(mem.u(DECODER, 4), 48000)
        self.assertEqual(mem.u(DECODER + 4, 4), 48000)

    def test_stream_once_does_not_change_any_service_cave(self):
        for mode in ({}, {'world_mode': True}, {'battle_mode': True}):
            with self.subTest(**mode):
                off = V._build_field_clocked_voice_service(
                    0, lambda i: i * 4, BSS, stream_once=False, **mode)
                on = V._build_field_clocked_voice_service(
                    0, lambda i: i * 4, BSS, stream_once=True, **mode)
                self.assertEqual(on, off)

    def test_both_legacy_modes_install_the_same_transition_only_worker(self):
        import struct
        main = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'dump', 'exefs', 'main')
        if not os.path.exists(main):
            return
        import tempfile
        import ff7nx_audio_cave as AC
        with tempfile.TemporaryDirectory() as tmp:
            on = os.path.join(tmp, 'once')
            off = os.path.join(tmp, 'loop')
            on_report = V.apply_to_nso(main, on, stream_once=True)
            off_report = V.apply_to_nso(main, off, stream_once=False)
            with open(on, 'rb') as handle:
                _segs, once_raw = AC.segments(handle.read())
            with open(off, 'rb') as handle:
                _segs, loop_raw = AC.segments(handle.read())
        once = struct.unpack_from('<I', once_raw[0], V.WORKER_LOOP_HOOK)[0]
        loop = struct.unpack_from('<I', loop_raw[0], V.WORKER_LOOP_HOOK)[0]
        self.assertEqual(once, loop)
        self.assertEqual(once, V.A.b(V.WORKER_LOOP_HOOK,
                                     on_report['transition_worker_entry']))
        self.assertEqual(loop, V.A.b(V.WORKER_LOOP_HOOK,
                                     off_report['transition_worker_entry']))

    def _worker_route(self, latched, active_player, worker_player, loop_flag):
        mem = EM.Mem()
        mem.setu(BSS + V.VOICE_MAPJUMP_OFF, latched, 4)
        mem.setu(BSS + V.VOICE_PLAYER_OFF, active_player, 8)
        mem.setu(worker_player + 0x50, 1, 1)
        mem.setu(worker_player + 0x98, 2, 4)
        cpu = self.cpu(mem)
        cpu.x[19] = worker_player
        cpu.x[10] = loop_flag
        entry, code = laid_out(
            lambda at, addr: V._build_mapjump_worker_stop(at, addr, BSS),
            False)
        return (cpu.run(entry, code),
                mem.u(BSS + V.VOICE_MAPJUMP_OFF, 4),
                mem.u(worker_player + 0x50, 1),
                mem.u(worker_player + 0x98, 4))

    def test_unlatched_worker_routes_are_exactly_stock(self):
        self.assertEqual(self._worker_route(0, PLAYER, PLAYER, 0),
                         (V.WORKER_ENDCHECK, 0, 1, 2))
        self.assertEqual(self._worker_route(0, PLAYER, PLAYER, 1),
                         (V.WORKER_LOOP_CONTINUE, 0, 1, 2))

    def test_mapjump_stops_only_the_private_field_voice(self):
        self.assertEqual(self._worker_route(1, PLAYER, PLAYER, 1),
                         (V.WORKER_STOP_UNLOCK, 0, 0, 8))
        music = PLAYER + 0x10000
        self.assertEqual(self._worker_route(1, PLAYER, music, 1),
                         (V.WORKER_LOOP_CONTINUE, 1, 1, 2))

    def test_mapjump_opcode_only_latches_and_replays_its_prologue(self):
        mem = EM.Mem()
        mem.setu(BSS + V.VOICE_PLAYER_OFF, PLAYER, 8)
        cpu = self.cpu(mem)
        old_sp = cpu.sp
        incoming_x25 = cpu.x[25]
        entry, code = laid_out(
            lambda at, addr: V._build_mapjump_voice_latch(at, addr, BSS),
            False)
        self.assertEqual(cpu.run(entry, code), V.MAPJUMP_RESUME)
        self.assertEqual(mem.u(BSS + V.VOICE_MAPJUMP_OFF, 4), 1)
        self.assertEqual(cpu.sp, old_sp - 0x50)
        self.assertEqual(mem.u(cpu.sp, 8), incoming_x25)


class ThePublisherStillResolvesABurst(Execution):
    """
    The burst rule is unchanged and still belongs on the publish side: it
    decides which of several windows publishing in ONE script tick gets the
    slot, before any key is built. `chrin_1b`'s three grunts are the measured
    case, and only the first of them is voiced.
    """

    FIELD = b'chrin_1b'

    def _session(self, steps):
        mem = EM.Mem()
        cpu = self.cpu(mem, {V.MESSAGE_PRE_TARGET: hostile()})
        cpu.x[21] = CONTEXT
        guest = cpu.host_base
        entry, code = laid_out(
            lambda at, addr: V._build_message_command_cave(
                at, addr, BSS, key_mode='folder',
                resume_address=V.MESSAGE_HOOK,
                resume_call_target=V.MESSAGE_PRE_TARGET), False)
        out = []
        for field, window, dialog, state in steps:
            mem.setu(guest + V.CURRENT_WINDOW, window, 1)
            mem.setu(guest + V.SCRIPT_CURSOR + window * 2, 0, 2)
            mem.setu(guest + V.FIELD_SCRIPT_PTR, 0x200000, 4)
            mem.write(guest + 0x200000, bytes((0x40, window, dialog)))
            mem.setu(guest + V.MESSAGE_STATE + window * 48, state, 2)
            mem.write(guest + V.FIELD_FILE_NAME, field + b'\0' * 13)
            mem.setu(BSS + V.VOICE_PENDING_CMD_OFF, 0, 4)
            cpu.run(entry, code)
            if mem.u(BSS + V.VOICE_PENDING_CMD_OFF, 4) == V.VOICE_CMD_PLAY:
                out.append(bytes(mem.read(BSS + V.VOICE_KEY_OFF, 24))
                           .split(b'\0')[0].decode())
        return out

    def test_every_speaker_still_reaches_the_mailbox(self):
        self.assertEqual(
            self._session([(self.FIELD, 1, 75, 2), (self.FIELD, 2, 76, 2),
                           (self.FIELD, 3, 77, 2)]),
            ['chrin_1b/75a', 'chrin_1b/76a', 'chrin_1b/77a'])

    def test_the_next_page_is_never_refused(self):
        self.assertEqual(
            self._session([(self.FIELD, 1, 75, 2), (self.FIELD, 1, 75, 14),
                           (self.FIELD, 1, 75, 2)]),
            ['chrin_1b/75a', 'chrin_1b/75b'])

    def test_a_reopened_window_still_publishes(self):
        """
        The producer no longer refuses anything: a window that closes and
        reopens on the same line publishes again, and the service decides
        whether that is a duplicate. Keeping the refusal here is what made
        448 swallow the next field's line.
        """
        self.assertEqual(
            self._session([(self.FIELD, 1, 75, s) for s in (2, 8, 7, 0)]
                          + [(self.FIELD, 1, 75, 2)]),
            ['chrin_1b/75a', 'chrin_1b/75a'])
