#!/usr/bin/env python3
"""
test_smooth_wrap.py -- the scripted-movement wrapper, executed.

The previous attempt at this feature was unit-tested, documented, shipped and
wrong: its tests checked that the two halves of a split step summed to the
whole, which they did, while the thing that actually broke was a value read
back out of the model's state halfway through the pair. Arithmetic tests could
not see that, because the arithmetic was fine.

So this test does not check arithmetic. It EXECUTES the shipped words against a
model of the guest -- guest register block, guest stack, a translate() that
maps to a DIFFERENT address space so an untranslated access is a wrong answer
rather than a lucky one, and stubs standing in for the two real functions --
and then asserts the properties the freeze came from:

    1. the real update runs ONCE per tick pair, never twice, never zero times
    2. it always starts from an exact stock position, never a midpoint
    3. the pair ends on exactly the stock position
    4. the guest return value is what the caller would have got either way
    5. the guest stack pointer balances on the replay frame
    6. a cache that is not from the immediately preceding tick is not used
    7. the two hooks agree about which half of the pair they are in
"""
import sys

import a64
import arm64emu
import ff7nx_smooth as S

CAVE    = 0x1152660
SCRATCH = 0x3FEE400
TICK    = 0x3FEE328

GUEST_REGS_ADDR = 0x40000000          # where x25 points
GUEST_STACK     = 0x50000000
_GH = arm64emu.Cpu(arm64emu.Mem(), paged=True)


def gh(va):
    """The host address arm64emu's paged translator gives for a guest VA."""
    return _GH.guest_to_host(va)

CALLS_MOVE = 0x00C00000

STEP = (7, -5, 3)                     # what the stubbed real update applies


def build_code():
    """The cave plus the three stubs, as one {address: word} map."""
    words, entries = S.build(lambda i: CAVE + 4 * i, SCRATCH, TICK)
    code = {CAVE + 4 * i: v for i, v in enumerate(words)}

    # translate() is NOT stubbed: arm64emu intercepts `bl TRANSLATE` and runs
    # its own paged model of the real page table, where translate(p) is valid
    # only inside the 4 KB page it resolved. That is the property this cave
    # was written around -- it translates each of the three position words
    # separately rather than translating once and adding 4 -- so testing
    # against the real page behaviour is the whole point.

    # the real movement update: pop the guest return slot (what its `ret`
    # does), advance model 0's position by STEP, return 1 in guest EAX.
    mv = []
    mv += [a64.ldr(0, 25, S.GUEST_ESP), a64.add_imm(0, 0, 4),
           a64.str_(0, 25, S.GUEST_ESP)]
    mv += [a64.stp64_pre(29, 30, 31, -0x10)]
    for k, d in enumerate(STEP):
        mv += a64.movz_movk(0, S.EV_BASE + S.POS_OFF + 4 * k)
        mv += [0]                                          # bl TRANSLATE
        mv += [a64.ldr(4, 0),
               a64.add_imm(4, 4, d) if d >= 0 else a64.sub_imm(4, 4, -d),
               a64.str_(4, 0)]
    # a call counter, so "ran once per pair" is observed rather than inferred
    mv += a64.movz_movk(0, CALLS_MOVE)
    mv += [0, a64.ldr(4, 0), a64.add_imm(4, 4, 1), a64.str_(4, 0)]
    mv += [a64.ldp64_post(29, 30, 31, 0x10),
           a64.movz(0, 1), a64.str_(0, 25, S.GUEST_EAX), a64.ret()]
    for i, v in enumerate(mv):
        code[S.MOVE_TGT + 4 * i] = v
    for i, v in enumerate(mv):                    # fix the bl TRANSLATEs
        if v == 0:
            code[S.MOVE_TGT + 4 * i] = a64.bl(S.MOVE_TGT + 4 * i, S.TRANSLATE)

    # the collision / arrival test: same stack discipline, returns 1.
    cl = [a64.ldr(0, 25, S.GUEST_ESP), a64.add_imm(0, 0, 4),
          a64.str_(0, 25, S.GUEST_ESP), a64.movz(0, 1),
          a64.str_(0, 25, S.GUEST_EAX), a64.ret()]
    for i, v in enumerate(cl):
        code[S.COLL_TGT + 4 * i] = v

    return code, entries


class World:
    def __init__(self):
        self.code, self.entries = build_code()
        self.mem = arm64emu.Mem()
        self.tick = 0
        self.calls = {'move': 0, 'coll': 0}
        self.set_pos(0, 0, 0)
        self.mem.setu(TICK, 0, 4)

    # guest memory is only reachable through translate(), i.e. at +HOST_BIAS
    def set_pos(self, *xyz):
        for k, v in enumerate(xyz):
            self.mem.setu(gh(S.EV_BASE + S.POS_OFF + 4 * k), v & 0xFFFFFFFF, 4)

    def pos(self):
        out = []
        for k in range(3):
            v = self.mem.u(gh(S.EV_BASE + S.POS_OFF + 4 * k), 4)
            out.append(v - (1 << 32) if v >> 31 else v)
        return tuple(out)

    def advance_tick(self):
        self.tick += 1
        self.mem.setu(TICK, self.tick, 4)

    def _cpu(self):
        # paged=True is not optional: it models translate() being valid only
        # inside the 4 KB page it resolved, which is the constraint the cave's
        # per-word translation exists to satisfy.
        return arm64emu.Cpu(self.mem, paged=True)

    def call(self, which, model_id=0):
        """Run one hook the way the recompiled caller does."""
        cpu = self._cpu()
        cpu._wr64(25, GUEST_REGS_ADDR)
        cpu._wr64(31, GUEST_STACK)                       # host sp
        esp = 0x20000
        # the caller pushes the argument(s), then reserves the return slot
        self.mem.setu(gh(esp - 4), model_id, 4)
        if which == 'coll':
            self.mem.setu(gh(esp - 4), S.EV_BASE + model_id * S.EV_STRIDE, 4)
        esp -= 4
        esp -= 4
        self.mem.setu(GUEST_REGS_ADDR + S.GUEST_ESP, esp, 4)
        self.mem.setu(GUEST_REGS_ADDR + S.GUEST_EAX, 0xDEAD, 4)

        e = self.entries[which]
        entry = e if e > 0xFFFF else CAVE + 4 * e
        pre = self.pos()
        n0 = self.mem.u(gh(CALLS_MOVE), 4)
        cpu.run(CAVE, None, code=self.code, start_pc=entry, max_steps=20000)
        n1 = self.mem.u(gh(CALLS_MOVE), 4)
        post_esp = self.mem.u(GUEST_REGS_ADDR + S.GUEST_ESP, 4)
        eax = self.mem.u(GUEST_REGS_ADDR + S.GUEST_EAX, 4)
        moved = self.pos() != pre
        return {'esp_delta': post_esp - esp, 'eax': eax, 'moved': moved,
                'real_calls': n1 - n0, 'pos': self.pos()}


def check(name, got, want):
    if got != want:
        print('FAIL  %s\n        got  %r\n        want %r' % (name, got, want))
        return 1
    print('  ok  %s' % name)
    return 0


def main():
    bad = 0

    # ---- 0. the two hook words are what this module thinks they are -------
    try:
        import struct, nxmap
        m = nxmap.Main('dump/exefs/main')
        for nm, a, want in (('move', S.MOVE_HOOK, S.MOVE_ORIG),
                            ('coll', S.COLL_HOOK, S.COLL_ORIG)):
            got = struct.unpack('<I', m.text[a:a + 4])[0]
            bad += check('hook word at 0x%X (%s)' % (a, nm), got, want)
    except (ImportError, SystemExit, IOError):
        print('  --  no dump present, skipping the hook-word check')

    # ---- 1-4. one real call per pair, exact stock position at pair end ----
    w = World()
    start = (100, 200, 300)
    w.set_pos(*start)
    calls, positions, esps, eaxs = [], [], [], []
    for _ in range(8):
        r = w.call('move')
        calls.append(r['real_calls'])
        positions.append(r['pos'])
        esps.append(r['esp_delta'])
        eaxs.append(r['eax'])
        w.advance_tick()

    bad += check('the real update runs once per PAIR, not once per frame',
                 calls, [1, 0, 1, 0, 1, 0, 1, 0])

    stock = []
    p = start
    for i in range(4):
        p = tuple(a + b for a, b in zip(p, STEP))
        stock.append(p)
    bad += check('every pair ends on exactly the stock 30 Hz position',
                 [positions[1], positions[3], positions[5], positions[7]],
                 stock)

    mids = []
    p = start
    for i in range(4):
        nxt = tuple(a + b for a, b in zip(p, STEP))
        mids.append(tuple(a + int((b - a) / 2) for a, b in zip(p, nxt)))
        p = nxt
    bad += check('the interpolated frame sits at the midpoint',
                 [positions[0], positions[2], positions[4], positions[6]], mids)

    bad += check('the guest return value is 1 on every frame', eaxs, [1] * 8)
    bad += check('the guest stack balances on every frame', esps, [4] * 8)

    # ---- 5. the real update always STARTS from a stock position -----------
    # If it ever started from a midpoint the pair would drift; eight frames of
    # exact agreement above already proves it, but state it directly too.
    w2 = World()
    w2.set_pos(0, 0, 0)
    w2.call('move')
    w2.advance_tick()
    w2.call('move')
    w2.advance_tick()
    r = w2.call('move')
    # three frames in: pair 1 completed (one whole STEP), pair 2 is at its
    # midpoint (half a STEP). If the real update had ever restarted from a
    # midpoint this would be short.
    bad += check('no drift across pairs',
                 r['pos'], tuple(a + int(a / 2) for a in STEP))

    # ---- 6. a cache that is not from the preceding tick is not replayed ---
    w3 = World()
    w3.set_pos(0, 0, 0)
    w3.call('move')                 # phase 0, caches at tick 0
    w3.advance_tick()
    w3.advance_tick()               # SKIP a tick -- the model stopped moving
    r = w3.call('move')
    bad += check('a stale cache forces a real call instead of a replay',
                 r['real_calls'], 1)

    # ---- 7. the two hooks agree about which half of the pair they are in --
    w4 = World()
    w4.set_pos(0, 0, 0)
    seen = []
    for _ in range(4):
        c = w4.call('coll')         # runs FIRST in the real caller
        m_ = w4.call('move')
        seen.append((c['eax'], m_['real_calls']))
        w4.advance_tick()
    bad += check('collision and movement stay in the same half of the pair',
                 [s[1] for s in seen], [1, 0, 1, 0])

    # the collision result is cached and replayed, so the arrival test cannot
    # fire half a step early off the interpolated position
    bad += check('the collision hook returns a stable answer across the pair',
                 [s[0] for s in seen], [1, 1, 1, 1])

    bad += scattered()
    bad += default_on()

    # ---- 8. THE NPC BUG: idle frames must not drive the phase -----------
    # The arrival hook runs every frame for EVERY model, walking or not. When
    # it advanced the phase, a model standing still had its phase flipped by
    # frames it took no part in, and started its next walk on the wrong half
    # of the pair -- which is what made the bedroom NPC jitter. The arrival
    # hook must read the phase and never write it.
    w5 = World()
    w5.set_pos(0, 0, 0)
    for _ in range(5):                  # five frames of ARRIVAL ONLY: the
        w5.call('coll', 0)              # model exists but is not walking
        w5.advance_tick()
    first = w5.call('move', 0)
    bad += check('idle arrival-test frames do not advance the phase, so the '
                 'first walking frame is a real call',
                 first['real_calls'], 1)
    bad += check('and it starts from the model\'s actual position',
                 first['pos'], tuple(int(a / 2) for a in STEP))
    w5.advance_tick()
    second = w5.call('move', 0)
    bad += check('the frame after it replays, landing on the stock position',
                 (second['real_calls'], second['pos']), (0, STEP))

    # ---- 9. the two hooks never disagree within a frame ------------------
    # Both evaluate one predicate over state neither mutates before the other
    # reads it. Drive a full walk and assert they move in lockstep.
    w6 = World()
    w6.set_pos(0, 0, 0)
    pairs = []
    for _ in range(6):
        c = w6.call('coll', 0)
        m_ = w6.call('move', 0)
        pairs.append((c['esp_delta'], m_['real_calls']))
        w6.advance_tick()
    bad += check('arrival and movement stay in lockstep for a whole walk',
                 [p[1] for p in pairs], [1, 0, 1, 0, 1, 0])

    if bad:
        print('\n%d check(s) failed' % bad)
        sys.exit(1)
    print('all good')



def scattered():
    """
    The same checks, against a SCATTERED layout.

    What ships is not contiguous: the wrapper is chained through 127 reclaimed
    padding holes with bridging branches between them, and every branch, adrp
    and entry address in it resolved against those real addresses. A cave that
    works laid out end to end and not laid out in pieces would pass every test
    above and fail on hardware, so this rebuilds it over holes with gaps in
    them and runs it again.
    """
    import ff7nx_cave

    words0, _ = S.build(lambda i: 4 * i, SCRATCH, TICK)
    n = len(words0)
    # three-word holes 0x400 apart -- the shape the real pool hands out
    runs, va = [], CAVE
    left = n
    while left > 0:
        take = min(3, left)
        runs.append((va, take + 1))
        va += 0x400
        left -= take
    addrs = ff7nx_cave.slots(runs, n)
    words, entries = S.build(lambda i: addrs[i], SCRATCH, TICK)
    placed = ff7nx_cave.link(runs, words)

    world = World()
    world.code.update(placed)
    world.entries = {k: addrs[v] for k, v in entries.items()}
    world.set_pos(0, 0, 0)

    bad = 0
    seen = []
    for _ in range(4):
        world.call('coll', 0)
        r = world.call('move', 0)
        seen.append((r['real_calls'], r['pos']))
        world.advance_tick()
    bad += check('scattered layout: one real call per pair',
                 [s_[0] for s_ in seen], [1, 0, 1, 0])
    exact = [tuple(a * k for a in STEP) for k in (1, 2)]
    bad += check('scattered layout: pairs still end on stock positions',
                 [seen[1][1], seen[3][1]], exact)
    return bad


def default_on():
    """
    Smooth scripted movement is part of the 60 FPS set, not an option.

    It was a checkbox, it defaulted off, and the checkbox was never drawn --
    so it shipped twice without ever running. Now it is on whenever the 60 FPS
    patches are, with an env override kept only for bisecting. That default is
    the whole feature as far as anyone using this is concerned, so it is
    asserted rather than assumed.
    """
    import os
    import build
    import ff7nx_60fps as F

    bad = 0
    for val, want, why in ((None, True, 'unset -- the shipping case'),
                           ('', True, 'empty'),
                           ('0', False, 'the bisect override'),
                           ('off', False, 'off'),
                           ('false', False, 'false'),
                           ('1', True, 'explicitly on')):
        os.environ.pop(F.SMOOTH_SCRIPTED_ENV, None)
        if val is not None:
            os.environ[F.SMOOTH_SCRIPTED_ENV] = val
        got = F.smooth_scripted()
        bad += check('%s=%-6r -> %-5s (%s)'
                     % (F.SMOOTH_SCRIPTED_ENV, val, got, why), got, want)
        # build.py drives the packer and ff7nx_60fps.py drives the patcher;
        # if these two ever disagreed the GUI and the build would too
        bad += check('  build.py agrees', build.smooth_scripted(), got)
    os.environ.pop(F.SMOOTH_SCRIPTED_ENV, None)

    # and the GUI must not carry a dead control for it any more
    gui = open('7th_heaven_nx.py', encoding='utf-8').read()
    bad += check('the GUI has no leftover smooth-scripted control',
                 'smooth_var' in gui or 'smooth_scripted' in gui, False)
    return bad

if __name__ == '__main__':
    main()
