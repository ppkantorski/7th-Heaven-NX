#!/usr/bin/env python3
"""tests/test_camerainterp.py -- the camera cave against FFNx's C++, frame by frame.

BUILD 384. The hold killed the strobe but left the camera stepping at 15 Hz.
This is the rest of `CameraInterpolationEffectDecorator`.

The tests that matter here do not check properties of the cave -- they run it
under arm64emu and compare every camera value it writes against a direct
Python transcription of FFNx's `callEffectFunction`, over many frames, for
smooth motion, for cuts past the teleport threshold, and for both threshold
classes. If the two ever disagree on any component of either vector, the test
names the frame.

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

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import arm64emu as E                                           # noqa: E402
import ff7nx_dispatch as D                                     # noqa: E402

try:
    import ff7nx_dispatch_sites as S
except ImportError:                                            # pragma: no cover
    S = None

pytestmark = pytest.mark.skipif(S is None, reason='needs the sites file')

HOST = 0x40000000
PRE, POST = 0x900000, 0x901000
RAMUH = 0x00597206
CAM_BASE = 0x3FECAE8
FREQ = 4


# ----------------------------------------------------------- FFNx, in Python

def interpolate(prev, nxt, step, n):
    """FFNx: previous + ((next - previous) * step) / n_steps.

    C++ integer division truncates toward zero; Python's // floors, so a
    negative delta would round the wrong way and every leftward camera move
    would be one unit out. That is exactly the bug the cave's +3 bias exists
    to avoid, so the reference has to get it right too or they would agree by
    being wrong together.
    """
    num = (nxt - prev) * step
    q = abs(num) // n
    return prev + (q if num >= 0 else -q)


def smooth(prev, nxt, threshold):
    d = sum((b - a) ** 2 for a, b in zip(prev, nxt))
    return d < threshold * threshold


class FFNxCamera:
    """CameraInterpolationEffectDecorator, transcribed.

    Phase numbering is this port's, not FFNx's frameCounter: the throttle has
    no equivalent of FFNx's frameCounter == 0 bootstrap frame, so `previous`
    is seeded from `next` on the first advance instead. Everything after the
    first cycle is the same program.
    """

    def __init__(self, threshold):
        self.threshold = threshold
        self.prev = self.next = None

    def frame(self, phase, live_pos, live_foc):
        """Returns what should be on screen, or None if nothing is written."""
        if phase == 1:                                   # advancing
            self.prev = self.next
            self.next = (tuple(live_pos), tuple(live_foc))
            if self.prev is None:
                self.prev = self.next
            step = 1
        elif phase == 0:                                 # the endpoint
            return self.next
        else:
            step = phase                                 # 2 or 3

        (pp, pf), (np_, nf) = self.prev, self.next
        if smooth(pp, np_, self.threshold) and smooth(pf, nf, self.threshold):
            return (tuple(interpolate(a, b, step, FREQ) for a, b in zip(pp, np_)),
                    tuple(interpolate(a, b, step, FREQ) for a, b in zip(pf, nf)))
        # BUILD 811: a cut shows `next` (FFNx holds `previous`): the summon's
        # scenery switches on the same tick, and the old camera on the new
        # scene was KOTR's two bad shots (kotrcutprobe, ticks 54 and 84).
        return self.next


# ------------------------------------------------------------- the cave rig

def _thr(interp=True, thresh=D.CAM_THRESH_DEFAULT):
    thr = dict(S.DISPATCH_SITES['effect100']['throttle'])
    thr['table'] = []
    thr['camera'] = [(n, int(n.rsplit('_', 1)[-1], 16),
                      thresh // D.CAM_THRESH_UNIT)
                     for n in D.EFFECT100_CAMERA]
    thr.update(cam_pos_guest=0xBF2158, cam_foc_guest=0xBFB1A0,
               cam_pos_ptr=CAM_BASE, cam_foc_ptr=CAM_BASE + 8,
               cam_next=CAM_BASE + 16, cam_prev=CAM_BASE + 28,
               cam_init=CAM_BASE + 40, cam_interp=interp,
               cam_slot_ptr=CAM_BASE + 48, cam_active=CAM_BASE + 56,
               cam_final=CAM_BASE + 58, cam_thr=CAM_BASE + 60)
    return thr


def _sites(thr):
    dh = S.DISPATCH_SITES['effect100']['disp_hook']
    pre = {'hook': thr['pre'], 'displaced': thr['pre_displaced'],
           'ctx_reg': thr['ctx_reg'], 'store_reg': thr['store_reg'],
           'idx_off': dh['idx_off']}
    return pre, dict(pre, hook=thr['post'], displaced=thr['post_displaced'])


def _put(mem, host, v):
    for i, x in enumerate(v):
        mem.setu(host + 2 * i, x & 0xFFFF, 2)


def _get(mem, host):
    out = []
    for i in range(3):
        x = mem.u(host + 2 * i, 2)
        out.append(x - 0x10000 if x & 0x8000 else x)
    return tuple(out)


class Rig:
    """One registered camera slot, driven a frame at a time."""

    def __init__(self, interp=True, thresh=D.CAM_THRESH_DEFAULT, idx=9):
        self.thr = _thr(interp, thresh)
        self.pre_site, self.post_site = _sites(self.thr)
        self.pre = D.build_throttle_pre_cave(
            PRE, self.pre_site, self.thr, S.PAUSED_GUEST,
            self.thr['mask_bits'])
        self.post = D.build_throttle_post_cave(POST, self.post_site, self.thr)
        self.mem = E.Mem()
        self.idx = idx
        self.mem.setu(self.thr['ctr'] + idx, D.CAM_MARK, 1)
        # The registration cave resolves the threshold into BSS; the rig is
        # driving pre/post directly, so it seeds that byte itself.
        self.mem.setu(self.thr['cam_thr'], thresh, 2)
        probe = E.Cpu(self.mem, host_base=HOST)
        self.paused_h = probe.guest_to_host(S.PAUSED_GUEST)
        self.pos_h = probe.guest_to_host(self.thr['cam_pos_guest'])
        self.foc_h = probe.guest_to_host(self.thr['cam_foc_guest'])
        self.mem.setu(self.paused_h, 0, 1)

    def frame(self, real_pos, real_foc, default=(-999, -888, -777)):
        """Run pre, model the two camera writers, run post. Returns what is
        on screen and whether the frame was paused."""
        cpu = E.Cpu(self.mem, host_base=HOST)
        cpu.sp = 0x800000000
        cpu.x[0] = RAMUH
        cpu.x[self.pre_site['ctx_reg']] = 0x30000000
        cpu.x[self.pre_site['store_reg']] = 0x1234
        self.mem.setu(0x30000000 + self.pre_site['idx_off'], self.idx, 4)
        assert cpu.run(PRE, self.pre) == self.pre_site['hook'] + 4
        assert cpu.x[0] & 0xFFFFFFFF == RAMUH, 'the fn pointer was clobbered'
        paused = self.mem.u(self.paused_h, 1)
        # The default battle camera aims every frame; the summon routine only
        # aims when it is not paused.
        _put(self.mem, self.pos_h, default)
        _put(self.mem, self.foc_h, default)
        if not paused:
            _put(self.mem, self.pos_h, real_pos)
            _put(self.mem, self.foc_h, real_foc)
        cpu2 = E.Cpu(self.mem, host_base=HOST)
        cpu2.sp = 0x800000000
        assert cpu2.run(POST, self.post) == self.post_site['hook'] + 4
        return (_get(self.mem, self.pos_h), _get(self.mem, self.foc_h)), paused


# --------------------------------------------------- the differential itself

def _run_against_ffnx(track, thresh=D.CAM_THRESH_DEFAULT, frames=24):
    """`track` yields the camera the routine would compute on each advance."""
    threshold = thresh
    rig = Rig(interp=True, thresh=thresh)
    ref = FFNxCamera(threshold)
    live = None
    advance = 0
    for f in range(frames):
        phase = (f % FREQ) + 1
        phase %= FREQ                       # 1,2,3,0,1,2,3,0...
        if phase == 1:
            live = track[advance % len(track)]
            advance += 1
        got, paused = rig.frame(*live)
        want = ref.frame(phase, *live)
        assert (phase == 1) != bool(paused), (
            'frame %d: phase %d but paused=%s' % (f, phase, paused))
        assert got == want, (
            'frame %d (phase %d): cave shows %r, FFNx would show %r'
            % (f, phase, got, want))
    return rig


def test_a_smooth_pan_matches_ffnx_every_frame():
    track = [((100 + 40 * i, 200 + 10 * i, 300 - 20 * i),
              (11 + 5 * i, 22, 33 + 3 * i)) for i in range(6)]
    _run_against_ffnx(track)


def test_a_leftward_pan_matches_too():
    """Negative deltas are where C++ truncation and a shift disagree."""
    track = [((500 - 37 * i, -200 - 13 * i, 90 - 7 * i),
              (-11 - 9 * i, -22 - 3 * i, -33 - 1 * i)) for i in range(6)]
    _run_against_ffnx(track)


def test_a_cut_past_the_threshold_snaps_instead_of_sliding():
    """A jump larger than 1500 units must land on `next`, not slide (811)."""
    track = [((0, 0, 0), (0, 0, 0)),
             ((30, 10, 5), (1, 2, 3)),
             ((9000, -9000, 4000), (1, 2, 3)),     # the cut
             ((9010, -8990, 4005), (2, 3, 4))]
    _run_against_ffnx(track, frames=20)


def test_a_cut_in_the_focal_point_alone_still_snaps_both():
    """FFNx requires BOTH vectors smooth; either failing snaps both."""
    track = [((0, 0, 0), (0, 0, 0)),
             ((20, 20, 20), (10, 10, 10)),
             ((25, 25, 25), (9000, 9000, 9000)),   # only the focal jumps
             ((30, 30, 30), (9005, 9005, 9005))]
    _run_against_ffnx(track, frames=20)


def test_the_elevated_threshold_class_interpolates_a_wider_jump():
    """A 3000-unit move snaps at 1500 and interpolates at 5000."""
    track = [((0, 0, 0), (0, 0, 0)),
             ((3000, 0, 0), (0, 0, 0)),
             ((6000, 0, 0), (0, 0, 0))]
    _run_against_ffnx(track, thresh=1500, frames=16)
    _run_against_ffnx(track, thresh=5000, frames=16)

    # ...and prove the two classes actually diverge, or the test above would
    # pass for an implementation that ignored the class entirely.
    small = Rig(interp=True, thresh=1500)
    big = Rig(interp=True, thresh=5000)
    seen_small, seen_big = [], []
    for f in range(12):
        phase = ((f % FREQ) + 1) % FREQ
        live = track[(f // FREQ) % len(track)]
        seen_small.append(small.frame(*live)[0])
        seen_big.append(big.frame(*live)[0])
    assert seen_small != seen_big, 'the threshold changed nothing'


def test_the_endpoint_frame_lands_exactly_on_the_routines_value():
    """Phase 0 must show `next` verbatim -- no accumulated rounding."""
    rig = Rig(interp=True)
    track = [((1000, -2000, 3000), (7, -8, 9)),
             ((1333, -2777, 3111), (70, -80, 90))]
    last = None
    for f in range(8):
        phase = ((f % FREQ) + 1) % FREQ
        live = track[(f // FREQ) % len(track)]
        got, _ = rig.frame(*live)
        if phase == 0:
            last = (got, live)
    assert last is not None
    got, live = last
    assert got == (tuple(live[0]), tuple(live[1])), (
        'the endpoint frame shows %r, the routine computed %r' % (got, live))


def test_the_first_cycle_holds_still_instead_of_using_uninitialised_memory():
    rig = Rig(interp=True)
    first = ((1234, -5678, 4321), (11, 22, 33))
    seen = [rig.frame(*first)[0] for _ in range(4)]
    assert all(s == (first[0], first[1]) for s in seen), seen


def test_hold_mode_still_reproduces_build_380():
    """`=hold` must be the old behaviour exactly: the pair, unchanged."""
    rig = Rig(interp=False)
    real = ((100, 200, 300), (11, 22, 33))
    seen = [rig.frame(*real) for _ in range(8)]
    for i, (shown, _paused) in enumerate(seen):
        assert shown == (real[0], real[1]), 'frame %d shows %r' % (i, shown)
    assert sum(1 for _s, p in seen if p) == 6


def test_a_non_camera_slot_is_untouched_by_either_mode():
    for interp in (True, False):
        rig = Rig(interp=interp, idx=3)
        rig.mem.setu(rig.thr['ctr'] + 3, 0, 1)          # generic slot
        for _ in range(4):
            got, _ = rig.frame((7, 8, 9), (1, 2, 3), default=(7, 8, 9))
            assert got == ((7, 8, 9), (1, 2, 3)) or got == ((7, 8, 9), (7, 8, 9))


@pytest.mark.parametrize('thresh', [1500, 4000, 5000])
@pytest.mark.parametrize('phase', [0, 1, 2, 3])
def test_the_function_pointer_and_store_register_survive(thresh, phase):
    """The build-214 lesson, re-applied to the arm that grew the most."""
    rig = Rig(interp=True, thresh=thresh)
    rig.mem.setu(rig.thr['ctr'] + rig.idx, D.CAM_MARK | phase, 1)
    cpu = E.Cpu(rig.mem, host_base=HOST)
    cpu.sp = 0x800000000
    cpu.x[0] = RAMUH
    cpu.x[rig.pre_site['ctx_reg']] = 0x30000000
    cpu.x[rig.pre_site['store_reg']] = 0x1234
    rig.mem.setu(0x30000000 + rig.pre_site['idx_off'], rig.idx, 4)
    assert cpu.run(PRE, rig.pre) == rig.pre_site['hook'] + 4
    assert cpu.x[0] & 0xFFFFFFFF == RAMUH
    assert cpu.x[rig.pre_site['store_reg']] & 0xFFFFFFFF == 0x1234


def test_the_marker_keeps_CAM_MARK_across_the_phase_wrap():
    """
    The marker carries CAM_MARK and the phase, and the phase update has to
    survive the 3 -> 0 wrap, where an increment carries straight into bit 2.
    BUILD 385 moved the threshold out of the marker precisely so this is the
    only thing that has to hold.
    """
    rig = Rig(interp=True, thresh=4000)
    for f in range(9):
        rig.frame((10 * f, 0, 0), (0, 0, 0))
        marker = rig.mem.u(rig.thr['ctr'] + rig.idx, 1)
        assert marker & D.CAM_MARK, (
            'frame %d: marker 0x%02X lost CAM_MARK' % (f, marker))
        assert marker & ~(D.CAM_MARK | 3) == 0, (
            'frame %d: marker 0x%02X grew a bit it should not have'
            % (f, marker))
        assert rig.mem.u(rig.thr['cam_thr'], 2) == 4000, (
            'frame %d: the resolved threshold was clobbered' % f)


# ------------------------------------------------------------- mutations
#
# A differential test is only worth what it catches. These apply a source
# mutation to ff7nx_dispatch, rebuild the cave from the mutated builder, and
# require the frame-by-frame comparison above to notice. Every one of them is
# a mistake that was genuinely available while writing this arm.

MUTATIONS = {
    # Both interpolation arms divide through ff7nx_dispatch._div_toward_zero.
    'drop the truncate-toward-zero bias': (
        "        w.append(A.add_reg_lsl(10, 10, 11, 1))\n"
        "        w.append(A.add_reg(10, 10, 11))\n"
        "    elif freq_bits == 1:",
        "        pass\n"
        "    elif freq_bits == 1:"),
    'wrong divisor': (
        "    w.append(A.asr(10, 10, freq_bits))",
        "    w.append(A.asr(10, 10, freq_bits - 1))"),
    'ignore the resolved threshold': (
        "            w.append(A.ldrh(6, 17, throttle['cam_thr'] - base))",
        "            w.append(A.movz(6, 1500))"),
    'never snap a cut': (
        "            for bi, cond in teleport_branches:\n"
        "                w[bi] = A.bcond(pc(bi), teleport, cond)",
        "            for bi, cond in teleport_branches:\n"
        "                w[bi] = A.movz(31, 0)"),
    'only check the position for smoothness': (
        "            for half in (0, 6):\n                w.append(A.movz(8, 0))",
        "            for half in (0,):\n                w.append(A.movz(8, 0))"),
    'phase 0 stops being the endpoint': (
        "            w[endpoint_i] = A.cbz(16, pc(endpoint_i), camera_endpoint)",
        "            w[endpoint_i] = A.movz(31, 0)"),
    'the advance forgets to refresh previous': (
        "            for k in range(6):\n"
        "                w.append(A.ldrh(3, 17, cs + 2 * k))\n"
        "                w.append(A.strh(3, 17, cprev + 2 * k))\n"
        "            for off in (0, 2, 4):\n"
        "                w.append(A.ldrh(3, 1, off))",
        "            for off in (0, 2, 4):\n"
        "                w.append(A.ldrh(3, 1, off))"),
    'previous is never seeded on the first advance': (
        "            seeded_i = len(w)\n"
        "            w.append(0)                              # cbnz w3, SEEDED",
        "            w.append(A.movz(3, 1))\n"
        "            seeded_i = len(w)\n"
        "            w.append(0)"),
}


def _mutant(old, new):
    import types
    src = open(D.__file__).read()
    assert src.count(old) == 1, 'mutation anchor is stale'
    mod = types.ModuleType('_mutant_dispatch')
    mod.__file__ = D.__file__
    exec(compile(src.replace(old, new, 1), D.__file__, 'exec'), mod.__dict__)
    return mod


def _differential(mod):
    """Run the smooth pan, the leftward pan and a cut against FFNx."""
    tracks = [
        [((100 + 40 * i, 200 + 10 * i, 300 - 20 * i),
          (11 + 5 * i, 22, 33 + 3 * i)) for i in range(6)],
        [((500 - 37 * i, -200 - 13 * i, 90 - 7 * i),
          (-11 - 9 * i, -22 - 3 * i, -33 - 1 * i)) for i in range(6)],
        [((0, 0, 0), (0, 0, 0)), ((30, 10, 5), (1, 2, 3)),
         ((9000, -9000, 4000), (1, 2, 3)), ((0, 0, 0), (9000, 0, 0))],
        # A 3000-unit step: past the 1500 default, inside the 5000 elevated
        # class. Without this the two classes behave identically on every
        # track above -- 9000 is a cut under both -- and a mutation that threw
        # the class away went unnoticed. That gap was real and this closes it.
        [((0, 0, 0), (0, 0, 0)), ((3000, 0, 0), (0, 0, 0)),
         ((6000, 0, 0), (0, 0, 0)), ((6000, 2800, 0), (0, 0, 0))],
    ]
    for thresh in (1500, 4000, 5000):
        for track in tracks:
            thr = _thr(True, thresh)
            thr['camera'] = [(n, int(n.rsplit('_', 1)[-1], 16),
                              thresh // mod.CAM_THRESH_UNIT)
                             for n in mod.EFFECT100_CAMERA]
            pre_site, post_site = _sites(thr)
            rig = Rig.__new__(Rig)
            rig.thr, rig.pre_site, rig.post_site = thr, pre_site, post_site
            rig.pre = mod.build_throttle_pre_cave(
                PRE, pre_site, thr, S.PAUSED_GUEST, thr['mask_bits'])
            rig.post = mod.build_throttle_post_cave(POST, post_site, thr)
            rig.mem, rig.idx = E.Mem(), 9
            rig.mem.setu(thr['ctr'] + 9, mod.CAM_MARK, 1)
            rig.mem.setu(thr['cam_thr'], thresh, 2)
            probe = E.Cpu(rig.mem, host_base=HOST)
            rig.paused_h = probe.guest_to_host(S.PAUSED_GUEST)
            rig.pos_h = probe.guest_to_host(thr['cam_pos_guest'])
            rig.foc_h = probe.guest_to_host(thr['cam_foc_guest'])
            rig.mem.setu(rig.paused_h, 0, 1)

            ref = FFNxCamera(thresh)
            live, adv = None, 0
            for f in range(20):
                phase = ((f % FREQ) + 1) % FREQ
                if phase == 1:
                    live = track[adv % len(track)]
                    adv += 1
                got, _ = rig.frame(*live)
                if got != ref.frame(phase, *live):
                    return False
    return True


def test_the_differential_passes_on_the_real_builder():
    assert _differential(D)


@pytest.mark.parametrize('name', sorted(MUTATIONS))
def test_each_mutation_is_caught(name):
    old, new = MUTATIONS[name]
    assert not _differential(_mutant(old, new)), (
        '%s: the camera differential did not notice' % name)


# ------------------------------------------------- FFNx's deferred retirement

def _slot_field0(rig):
    """Host address of effect100_array_data[idx].field_0."""
    probe = E.Cpu(rig.mem, host_base=HOST)
    guest = rig.thr['data_base'] + rig.thr['stride'] * rig.idx
    return probe.guest_to_host(guest)


def test_a_routine_that_retires_mid_advance_keeps_its_slot_for_one_cycle():
    """
    FFNx's `finalFrame`. A camera routine retires by writing 0xFFFF into its
    slot's field_0. If that is allowed to stand on the advancing frame, the
    dispatcher drops the slot and the three interpolated frames never play --
    the camera hands back to the battle camera a quarter of the way through
    its last move. FFNx puts field_0 back and retires at the endpoint frame
    instead, and so does this.
    """
    rig = Rig(interp=True)
    f0 = _slot_field0(rig)
    rig.mem.setu(f0, 0x0001, 2)                 # live

    # Two normal cycles, then the routine retires during the advance.
    for f in range(4):
        rig.frame((100 * (f // 4), 0, 0), (0, 0, 0))

    seen = []
    for phase_i in range(4):
        # The routine retires on its advancing call (phase 1, the unpaused
        # one), which is frame 0 of this cycle.
        if phase_i == 0:
            rig.mem.setu(f0, 0x0001, 2)
        got, paused = rig.frame((400, 0, 0), (0, 0, 0))
        if phase_i == 0 and not paused:
            pass
        seen.append((got, rig.mem.u(f0, 2)))

    # Simulate the retirement properly: the routine sets 0xFFFF during the
    # unpaused call. Drive one more cycle with that happening.
    rig2 = Rig(interp=True)
    f0b = _slot_field0(rig2)
    rig2.mem.setu(f0b, 0x0001, 2)
    rig2.frame((0, 0, 0), (0, 0, 0))            # phase 1
    for _ in range(3):
        rig2.frame((0, 0, 0), (0, 0, 0))        # phases 2,3,0

    states = []
    for phase_i in range(4):
        cpu = E.Cpu(rig2.mem, host_base=HOST)
        cpu.sp = 0x800000000
        cpu.x[0] = RAMUH
        cpu.x[rig2.pre_site['ctx_reg']] = 0x30000000
        cpu.x[rig2.pre_site['store_reg']] = 0x1234
        rig2.mem.setu(0x30000000 + rig2.pre_site['idx_off'], rig2.idx, 4)
        cpu.run(PRE, rig2.pre)
        paused = rig2.mem.u(rig2.paused_h, 1)
        _put(rig2.mem, rig2.pos_h, (-999, -888, -777))
        _put(rig2.mem, rig2.foc_h, (-999, -888, -777))
        if not paused:
            _put(rig2.mem, rig2.pos_h, (800, 0, 0))
            _put(rig2.mem, rig2.foc_h, (0, 0, 0))
            rig2.mem.setu(f0b, 0xFFFF, 2)       # the routine retires HERE
        cpu2 = E.Cpu(rig2.mem, host_base=HOST)
        cpu2.sp = 0x800000000
        cpu2.run(POST, rig2.post)
        states.append((paused, rig2.mem.u(f0b, 2),
                       _get(rig2.mem, rig2.pos_h)))

    assert states[0][0] == 0, 'frame 0 of the cycle should be the advance'
    assert states[0][1] != 0xFFFF, (
        'the retirement was allowed to stand on the advancing frame -- the '
        'slot would be dropped before the interpolation played')
    for i in (1, 2):
        assert states[i][1] != 0xFFFF, 'retired early at phase %d' % (i + 1)
    assert states[3][1] == 0xFFFF, (
        'the deferred retirement never happened; the slot would never be '
        'released')
    # ...and the camera arrives exactly on the routine's final value.
    assert states[3][2] == (800, 0, 0), states[3][2]


def test_a_slot_that_was_already_dead_is_not_resurrected():
    """
    If field_0 was ALREADY 0xFFFF before the call, this is not a fresh
    retirement and putting it back would keep a dead effect alive forever.
    """
    rig = Rig(interp=True)
    f0 = _slot_field0(rig)
    rig.mem.setu(f0, 0xFFFF, 2)
    for _ in range(4):
        rig.frame((10, 0, 0), (0, 0, 0))
    assert rig.mem.u(f0, 2) == 0xFFFF, 'a dead slot was brought back to life'


def test_registration_clears_the_endpoints_for_a_new_owner():
    """
    prev/next are global -- only one summon camera is ever live -- so the
    registration cave has to clear `cam_init`, or the first cycle of the NEXT
    summon slides out of the previous summon's final camera. Nothing else
    clears them, and the bug would look like a one-cycle drift at the start of
    every summon after the first.
    """
    import struct
    CAVE = 0x902000
    thr = _thr(True, 0)
    thr['camera'] = [(n, int(n.rsplit('_', 1)[-1], 16), 0)
                     for n in D.EFFECT100_CAMERA]
    site = dict(S.DISPATCH_SITES['effect100']['add_hook'])
    words = D.build_addfn_cave(CAVE, site, None, thr['mask_bits'], thr)
    blob = struct.pack('<%dI' % len(words), *words)

    idx = 9
    for va, is_camera in ((RAMUH, True), (0x42782B, False)):
        mem = E.Mem()
        # The tables are DATA inside the cave, so the cave has to be in memory
        # as well as in the instruction list -- `adr` + `ldr` walks it out of
        # .text. A table emitted at the wrong offset reads zeroes and every
        # function comes out unmatched, silently.
        mem.write(CAVE, blob)
        mem.setu(thr['cam_init'], 1, 1)        # left over from a past summon
        mem.setu(thr['cam_final'], 1, 1)
        cpu = E.Cpu(mem, host_base=HOST)
        cpu.sp = 0x800000000
        for r in range(31):
            cpu.x[r] = E.GARBAGE
        cpu.x[site['ctx_reg']] = 0x30000000
        cpu.x[site['fn_val_reg']] = va
        cpu.x[0] = HOST + 0x00BF2858 + 4 * idx
        mem.setu(0x30000000 + site['idx_off'], idx, 4)
        cpu.run(CAVE, words)

        marker = mem.u(thr['ctr'] + idx, 1)
        if is_camera:
            assert marker & D.CAM_MARK, 'the camera was not marked (0x%02X)' % marker
            assert mem.u(thr['cam_init'], 1) == 0, (
                'cam_init survived registration -- the next summon would '
                'interpolate out of the previous one')
            assert mem.u(thr['cam_final'], 1) == 0, 'cam_final survived'
        else:
            assert not (marker & D.CAM_MARK), 'a non-camera got CAM_MARK'
            assert mem.u(thr['cam_init'], 1) == 1, (
                'a non-camera registration cleared the camera endpoints')


def test_the_threshold_reaches_bss_through_registration():
    """End to end: ramuh 5000, bahamut zero 4000, hades the 1500 default."""
    import struct
    CAVE = 0x902000
    WANT = {0x597206: 5000, 0x483866: 4000, 0x4B65A8: 1500}
    thr = _thr(True)
    thr['camera'] = [(n, int(n.rsplit('_', 1)[-1], 16),
                      WANT.get(int(n.rsplit('_', 1)[-1], 16),
                               D.CAM_THRESH_DEFAULT) // D.CAM_THRESH_UNIT)
                     for n in D.EFFECT100_CAMERA]
    site = dict(S.DISPATCH_SITES['effect100']['add_hook'])
    words = D.build_addfn_cave(CAVE, site, None, thr['mask_bits'], thr)
    blob = struct.pack('<%dI' % len(words), *words)
    idx = 9
    for va, want in WANT.items():
        mem = E.Mem()
        mem.write(CAVE, blob)
        mem.setu(thr['cam_thr'], 0xDEAD & 0xFFFF, 2)
        cpu = E.Cpu(mem, host_base=HOST)
        cpu.sp = 0x800000000
        for r in range(31):
            cpu.x[r] = E.GARBAGE
        cpu.x[site['ctx_reg']] = 0x30000000
        cpu.x[site['fn_val_reg']] = va
        cpu.x[0] = HOST + 0x00BF2858 + 4 * idx
        mem.setu(0x30000000 + site['idx_off'], idx, 4)
        cpu.run(CAVE, words)
        marker = mem.u(thr['ctr'] + idx, 1)
        assert marker & D.CAM_MARK, '0x%06X: marker 0x%02X' % (va, marker)
        assert mem.u(thr['cam_thr'], 2) == want, (
            '0x%06X resolved to %d, FFNx says %d'
            % (va, mem.u(thr['cam_thr'], 2), want))


# The registration cave is not reachable from the differential above -- that
# rig drives pre/post directly and seeds `cam_thr` itself -- so the mutations
# that live on the registration side are checked against what registration
# actually writes.

REGISTRATION_MUTATIONS = {
    'the wrong threshold unit': (
        "        w.append(A.movz(1, CAM_THRESH_UNIT))",
        "        w.append(A.movz(1, CAM_THRESH_UNIT * 2))"),
    'the threshold is never stored': (
        "        w.append(A.strh(16, 17, throttle['cam_thr'] - ctr))",
        "        w.append(A.movz(31, 0))"),
    'registration forgets to clear cam_init': (
        "        w.append(A.strb(A.WZR, 17, throttle['cam_init'] - ctr))",
        "        w.append(A.movz(31, 0))"),
}


def _registered(mod, va, thresholds):
    """(marker, resolved threshold, cam_init) after `va` registers."""
    import struct
    CAVE = 0x902000
    thr = _thr(True)
    thr['camera'] = [(n, int(n.rsplit('_', 1)[-1], 16),
                      thresholds.get(int(n.rsplit('_', 1)[-1], 16),
                                     mod.CAM_THRESH_DEFAULT)
                      // mod.CAM_THRESH_UNIT)
                     for n in mod.EFFECT100_CAMERA]
    site = dict(S.DISPATCH_SITES['effect100']['add_hook'])
    words = mod.build_addfn_cave(CAVE, site, None, thr['mask_bits'], thr)
    mem = E.Mem()
    mem.write(CAVE, struct.pack('<%dI' % len(words), *words))
    mem.setu(thr['cam_init'], 1, 1)
    idx = 9
    cpu = E.Cpu(mem, host_base=HOST)
    cpu.sp = 0x800000000
    for r in range(31):
        cpu.x[r] = E.GARBAGE
    cpu.x[site['ctx_reg']] = 0x30000000
    cpu.x[site['fn_val_reg']] = va
    cpu.x[0] = HOST + 0x00BF2858 + 4 * idx
    mem.setu(0x30000000 + site['idx_off'], idx, 4)
    cpu.run(CAVE, words)
    return (mem.u(thr['ctr'] + idx, 1), mem.u(thr['cam_thr'], 2),
            mem.u(thr['cam_init'], 1))


def _registration_ok(mod):
    want = {0x597206: 5000, 0x483866: 4000, 0x4B65A8: 1500}
    for va, thresh in want.items():
        marker, got, init = _registered(mod, va, want)
        if not marker & mod.CAM_MARK or got != thresh or init != 0:
            return False
    return True


def test_registration_is_correct_on_the_real_builder():
    assert _registration_ok(D)


@pytest.mark.parametrize('name', sorted(REGISTRATION_MUTATIONS))
def test_each_registration_mutation_is_caught(name):
    old, new = REGISTRATION_MUTATIONS[name]
    assert not _registration_ok(_mutant(old, new)), (
        '%s: registration did not notice' % name)


# --------------------------------------- our set against FFNx's, from source
#
# BUILD 387. The set used to be a hand-kept list of routines nobody had
# complained about, and that was wrong twice running: Bahamut ZERO was on it
# and stepping, and once fixed, regular Bahamut was stepping too. A list built
# from strobe reports cannot predict which cameras step, so the set is now
# derived from the same place the thresholds are -- FFNx -- and this test
# re-derives it from FFNx's source so the two cannot drift.

FFNX_ANIM = os.path.join(_HERE, 'repos', 'FFNx-master', 'src', 'ff7',
                         'battle', 'animations.cpp')
needs_ffnx = pytest.mark.skipif(not os.path.exists(FFNX_ANIM),
                                reason='no FFNx checkout')

# Shiva and Odin gunge get the same behaviour from their own code:
# `summon-runtime` NOPs a second g_is_battle_paused test sitting directly in
# front of their camera-apply block, so the routine re-applies the camera on
# paused frames by itself. Decorating them too would overwrite what they just
# computed. See the HOLD_EXCLUDE comment in ff7nx_60fps.py.
NATIVE = ('run_shiva_camera_58E60D', 'run_odin_gunge_camera_4A0F52')


def _ffnx_cameras():
    import re
    src = open(FFNX_ANIM).read()
    names = re.findall(
        r'camera_effect100_addresses\.insert\(ff7_externals\.(\w+)\)', src)
    thr = {}
    for fn, val in re.findall(
            r'camera_thresholds_by_address\[ff7_externals\.(\w+)\]\s*=\s*(\d+)',
            src):
        thr[fn] = int(val)
    return {n: thr.get(n, 1500) for n in names}


def _our_cameras(mode=None):
    import ff7nx_60fps as F
    old = os.environ.get('SEVENTH_NX_FX_CAMERAHOLD')
    if mode is None:
        os.environ.pop('SEVENTH_NX_FX_CAMERAHOLD', None)
    else:
        os.environ['SEVENTH_NX_FX_CAMERAHOLD'] = mode
    try:
        t = dict(S.DISPATCH_SITES['effect100']['throttle'])
        F._inject_camera_hold('effect100', t, log=lambda *a: None)
        return {n: u * D.CAM_THRESH_UNIT for n, _va, u in t['camera']}
    finally:
        if old is None:
            os.environ.pop('SEVENTH_NX_FX_CAMERAHOLD', None)
        else:
            os.environ['SEVENTH_NX_FX_CAMERAHOLD'] = old


@needs_ffnx
def test_the_default_set_is_exactly_ffnx():
    # BUILD 784: Shiva and Odin gunge are decorated too (their released pause
    # test re-applies the tick's camera, which stepped at 15 Hz).
    ffnx = _ffnx_cameras()
    ours = _our_cameras()
    assert len(ffnx) == 25, len(ffnx)
    assert set(ours) == set(ffnx), (
        'missing %s; extra %s'
        % (sorted(set(ffnx) - set(ours)), sorted(set(ours) - set(ffnx))))


@needs_ffnx
def test_every_threshold_matches_ffnx_exactly():
    ffnx = _ffnx_cameras()
    for name, thresh in sorted(_our_cameras().items()):
        assert thresh == ffnx[name], (
            '%s: ours %d, FFNx %d' % (name, thresh, ffnx[name]))


@needs_ffnx
def test_the_two_native_routines_are_decorated_unless_switched_off():
    """BUILD 784; SEVENTH_NX_SHIVA_CAM_STEP=1 restores the BUILD 381 skip."""
    for mode in (None, 'summons'):
        ours = _our_cameras(mode)
        for n in NATIVE:
            assert n in ours, '%s not decorated in mode %r' % (n, mode)
    os.environ['SEVENTH_NX_SHIVA_CAM_STEP'] = '1'
    try:
        for mode in (None, 'summons'):
            ours = _our_cameras(mode)
            for n in NATIVE:
                assert n not in ours, '%s decorated in mode %r' % (n, mode)
    finally:
        os.environ.pop('SEVENTH_NX_SHIVA_CAM_STEP', None)


@needs_ffnx
def test_summons_mode_drops_only_the_limits_and_enemy_attacks():
    full, narrow = _our_cameras(), _our_cameras('summons')
    dropped = set(full) - set(narrow)
    assert set(narrow) < set(full)
    assert all(('limit' in n) or ('enemy_atk' in n) for n in dropped), dropped
    assert len(narrow) == 18, len(narrow)


@needs_ffnx
def test_every_ffnx_model_routine_is_covered():
    """The model list has no gaps; this fails if FFNx ever grows one."""
    import re
    import ff7nx_60fps as F
    src = open(FFNX_ANIM).read()
    ffnx = set(re.findall(
        r'model_effect100_addresses\.insert\(ff7_externals\.(\w+)\)', src))
    t = dict(S.DISPATCH_SITES['effect100']['throttle'])
    F._inject_model_interpolation('effect100', t, log=lambda *a: None)
    ours = {n for n, _va, _m in t['model']}
    # BUILD 784: plus Chocobuckle's main loop, which FFNx decorates by name
    # (usePauseTrick false, threshold 10000), and two more hops of actor 3.
    named = {'run_chocobuckle_main_loop_560C32',
             'run_confu_main_loop_5600BE', 'enemy_skill_sub_56470F'}
    assert re.search(r'run_chocobuckle_main_loop_560C32\)\s*\{[^}]*'
                     r'g_is_battle_paused, false,[^}]*, 3, 10000\)', src)
    assert ffnx | named == ours, 'missing %s' % sorted((ffnx | named) - ours)


# ------------------------------------------------------------------ BUILD 783
def _kotr_frame(rig, pos, foc):
    """KOTR's camera routine aims on EVERY call -- its paused tail re-aims
    from its own state -- unlike the other summon cameras."""
    cpu = E.Cpu(rig.mem, host_base=HOST)
    cpu.sp = 0x800000000
    cpu.x[0] = RAMUH
    cpu.x[rig.pre_site['ctx_reg']] = 0x30000000
    cpu.x[rig.pre_site['store_reg']] = 0x1234
    rig.mem.setu(0x30000000 + rig.pre_site['idx_off'], rig.idx, 4)
    assert cpu.run(PRE, rig.pre) == rig.pre_site['hook'] + 4
    paused = rig.mem.u(rig.paused_h, 1)
    _put(rig.mem, rig.pos_h, pos)
    _put(rig.mem, rig.foc_h, foc)
    cpu2 = E.Cpu(rig.mem, host_base=HOST)
    cpu2.sp = 0x800000000
    assert cpu2.run(POST, rig.post) == rig.post_site['hook'] + 4
    return (_get(rig.mem, rig.pos_h), _get(rig.mem, rig.foc_h)), paused


def test_783_kotr_first_tick_is_left_to_the_routine():
    """Hardware 10-22 (kotr_beginning.mp4 532-534): the initialising call's
    camera (inside the stage) was held for the whole first tick. With
    cam_init = KOTR_CAM_FIRST the arm writes nothing that tick, seeds at the
    second advance, and is FFNx's decorator from then on."""
    rig = Rig(interp=True, thresh=5000)
    rig.mem.setu(rig.thr['cam_init'], D.KOTR_CAM_FIRST, 1)
    inside = ((-7000, 9000, 31000), (0, 0, 0))
    tail = ((100, -2000, 4000), (0, -500, 0))         # its paused re-aim
    seen, pauses = [], []
    # tick 1: real (the init call), then three paused re-aims
    for f, cam in enumerate([inside, tail, tail, tail]):
        got, paused = _kotr_frame(rig, *cam)
        assert got == cam, ('frame %d: the arm wrote %r over the routine\'s %r'
                            % (f, got, cam))
        pauses.append(paused)
    assert pauses == [0, 1, 1, 1]
    assert rig.mem.u(rig.thr['cam_init'], 1) == 1 - 1     # 0: seed pending
    # tick 2 onwards: exactly FFNx, starting from this advance
    ref = FFNxCamera(5000)
    track = [((120 + 30 * i, -1990 + 8 * i, 4010 - 6 * i), (2 * i, -500, i))
             for i in range(5)]
    adv = 0
    for f in range(4, 4 + 16):
        phase = ((f % FREQ) + 1) % FREQ
        if phase == 1:
            live = track[adv]
            adv += 1
        # the routine's own output on a paused call is its stale re-aim; the
        # arm must replace it
        routine = live if phase == 1 else tail
        got, paused = _kotr_frame(rig, *routine)
        want = ref.frame(phase, *live)
        assert got == want, (f, phase, got, want)
    assert rig.mem.u(rig.thr['cam_init'], 1) == 1


def test_783_other_cameras_are_unchanged_on_their_first_tick():
    # registration clears cam_init: the first advance seeds and holds, as
    # before (the differential tests above run through it too)
    rig = Rig(interp=True)
    a = ((10, 20, 30), (1, 2, 3))
    got, paused = rig.frame(*a)
    assert not paused and got == a
    for _ in range(3):
        got, paused = rig.frame(*a)
        assert paused and got == a
