"""BUILD 904: Auto-Text Advance delay (Settings > Voice).

After a voiced line reaches its natural end, the auto OK press is held back
for `auto_delay_frames` frames of the voice service while the window stays
open. The count lives in the existing VOICE_AUTO_OK_PENDING word (no new BSS):
the service stores delay+1, counts down one per waiting frame, and the press
goes in at 1. Anything that closes or replaces the window first clears it.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_voice_execution as T  # noqa: E402  (shares the harness/sys.path)

EM, V = T.EM, T.V
BSS, PLAYER, DECODER, CONTEXT = T.BSS, T.PLAYER, T.DECODER, T.CONTEXT
GUEST = 0x780000000
WORLD_OBJ = 0x00D00000


def resolve(cpu):
    guest = cpu.x[0] & 0xFFFFFFFF
    T.hostile(GUEST + guest)(cpu)


class Harness:
    def __init__(self, delay, world=False, clip_frames=6):
        self.mem = mem = EM.Mem()
        self.left = None
        self.injected = []          # frame numbers the OK press went in
        self.frame = 0
        self.world = world

        def ctor(cpu):
            mem.setu(PLAYER + 0x60, DECODER, 8)
            mem.setu(DECODER, 48000, 4)
            mem.setu(DECODER + 4, 48000, 4)
            mem.setu(PLAYER + 0x98, 0, 4)
            mem.setu(PLAYER + 0x08, PLAYER + 0x200, 8)
            mem.setu(PLAYER + 0x200, PLAYER + 0x300, 8)
            mem.setu(PLAYER + 0x318, V.MESSAGE_PRE_TARGET, 8)
            self.left = clip_frames
            T.hostile()(cpu)

        def delete(cpu):
            self.left = None
            T.hostile()(cpu)

        native = {a: T.hostile() for a in (
            V.OGG_WORKDIR, V.OGG_PATH_FORMAT, V.OGG_DECODER_DELETE,
            V.OGG_PLAY, V.OGG_VOLUME, V.MESSAGE_PRE_TARGET)}
        native[V.OGG_OPEN] = T.hostile(DECODER)
        native[V.MALLOC] = T.hostile(PLAYER)
        native[V.HOST_TICK_NOW] = T.hostile(1000)
        native[V.HOST_TICK_FREQUENCY] = T.hostile(19200000)
        native[V.OGG_CTOR] = ctor
        native[V.OGG_DELETE] = delete
        native[V.RESOLVE_PAGED] = resolve
        self.native = native
        self.cpu = self._cpu()
        mem.setu(CONTEXT + 0x10, 0x100100, 4)
        mem.setu(CONTEXT + 0x14, 0x100200, 4)
        # world: guest object pointer -> object whose +0x80 holds the OK bits
        mem.setu(GUEST + V.FIELD_GLOBAL_OBJECT_PTR, WORLD_OBJ, 4)
        self.service = T.laid_out(
            lambda at, addr: V._build_field_clocked_voice_service(
                at, addr, BSS, duck_percent=100, auto_advance=True,
                world_mode=world, auto_delay_frames=delay,
                defer_foreign_play=True), False)
        self.input = T.laid_out(
            lambda at, addr: V._build_auto_ok_input_cave(
                at, addr, BSS, delay_frames=delay), False)

    def _cpu(self):
        cpu = T.VoiceCpu(self.mem, native=self.native)
        cpu.x = [0x730000000 + i * 0x100 for i in range(32)]
        cpu.x[25] = cpu.x[21] = CONTEXT
        cpu.sp = 0x740001000
        return cpu

    def pending(self):
        return self.mem.u(BSS + V.VOICE_AUTO_OK_PENDING_OFF, 4)

    def ok(self):
        if self.world:
            return self.mem.u(GUEST + WORLD_OBJ + 0x80, 4) & 0x20
        return self.mem.u(GUEST + V.INPUT_OK_BUTTON_STATUS, 4)

    def play(self, key='chrin_1b/75a', owner=1):
        self.mem.write(BSS + V.VOICE_KEY_OFF,
                       key.encode() + b'\0' * (32 - len(key)))
        self.mem.setu(BSS + V.VOICE_PENDING_AUTO_OFF, 1, 4)
        self.mem.setu(BSS + V.VOICE_PENDING_OWNER_OFF, owner, 4)
        self.mem.setu(BSS + V.VOICE_PENDING_CMD_OFF, V.VOICE_CMD_PLAY, 4)

    def stop(self):
        self.mem.setu(BSS + V.VOICE_PENDING_CMD_OFF, V.VOICE_CMD_STOP, 4)

    def tick(self):
        self.frame += 1
        if not self.world:
            # the input refresh runs before the field loop's voice service
            self.mem.setu(GUEST + V.INPUT_OK_BUTTON_STATUS, 0, 4)
            cpu = self._cpu()
            cpu.run(*self.input)
            if self.ok():
                self.injected.append(self.frame)
        else:
            self.mem.setu(GUEST + WORLD_OBJ + 0x80, 0, 4)
        if self.left is not None:
            if self.mem.u(PLAYER + 0x98, 4) == 8:
                self.mem.setu(PLAYER + 0x98, 1, 4)
            else:
                self.left -= 1
                if self.left <= 0:
                    self.mem.setu(PLAYER + 0x98, 1, 4)
        self.cpu = self._cpu()
        self.cpu.run(*self.service)
        if self.world and self.ok():
            self.injected.append(self.frame)


def ended_at(h, limit=60):
    """Tick until the service records the natural end; return that frame."""
    for _ in range(limit):
        h.tick()
        if h.mem.u(BSS + V.VOICE_WAITING_WINDOW_OFF, 4) == 1:
            return h.frame
    raise AssertionError('line never reached its natural end')


class AutoDelay(unittest.TestCase):
    def run_line(self, delay, world=False, ticks=80):
        h = Harness(delay, world)
        h.play()
        end = ended_at(h)
        for _ in range(ticks):
            h.tick()
        return h, end

    def test_no_delay_presses_on_the_next_input_pass(self):
        for world in (False, True):
            with self.subTest(world=world):
                h, end = self.run_line(0, world)
                # field: the input cave on the following frame; world: the
                # service itself in the end frame (both exactly as before 904)
                self.assertEqual(h.injected, [end] if world else [end + 1])

    def test_the_press_is_held_back_by_exactly_the_delay(self):
        for world in (False, True):
            for delay in (1, 3, 33, 66):
                with self.subTest(world=world, delay=delay):
                    h, end = self.run_line(0, world)
                    base = h.injected[0] - end
                    h, end = self.run_line(delay, world, ticks=delay + 20)
                    self.assertEqual(len(h.injected), 1, h.injected)
                    self.assertEqual(h.injected[0] - end, base + delay)
                    self.assertEqual(h.pending(), 0)

    def test_countdown_starts_at_delay_plus_one(self):
        h = Harness(33)
        h.play()
        ended_at(h)
        self.assertEqual(h.pending(), 34)
        h.tick()
        self.assertEqual(h.pending(), 33)
        self.assertEqual(h.injected, [])

    def test_closing_the_window_first_cancels_the_press(self):
        for world in (False, True):
            with self.subTest(world=world):
                h = Harness(33, world)
                h.play()
                ended_at(h)
                for _ in range(5):
                    h.tick()
                h.stop()                 # the player pressed OK themselves
                for _ in range(80):
                    h.tick()
                self.assertEqual(h.injected, [])
                self.assertEqual(h.pending(), 0)

    def test_next_line_before_the_delay_never_gets_the_old_press(self):
        h = Harness(33)
        h.play()
        ended_at(h)
        for _ in range(5):
            h.tick()
        h.play('chrin_1b/90a', owner=2)  # the next box opened and voiced
        h.tick()
        self.assertEqual(h.pending(), 0)
        self.assertEqual(h.injected, [])
        # and the new line gets its own full delay after its own end
        end = ended_at(h)
        for _ in range(60):
            h.tick()
        self.assertEqual(len(h.injected), 1)
        self.assertEqual(h.injected[0] - end, 1 + 33)

    def test_delay_zero_emits_the_pre_904_words(self):
        for kw in ({}, {'world_mode': True}):
            with self.subTest(**kw):
                a = V._build_field_clocked_voice_service(
                    0x600000, lambda i: 0x600000 + i * 4, BSS,
                    auto_advance=True, **kw)
                b = V._build_field_clocked_voice_service(
                    0x600000, lambda i: 0x600000 + i * 4, BSS,
                    auto_advance=True, auto_delay_frames=0, **kw)
                self.assertEqual(a, b)
        self.assertEqual(
            V._build_auto_ok_input_cave(0x600000, lambda i: 0x600000 + i * 4,
                                        BSS),
            V._build_auto_ok_input_cave(0x600000, lambda i: 0x600000 + i * 4,
                                        BSS, delay_frames=0))


if __name__ == '__main__':
    unittest.main()
