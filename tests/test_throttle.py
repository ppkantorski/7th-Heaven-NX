#!/usr/bin/env python3
"""
test_throttle.py -- differential test of the pause-throttle caves.

    python3 test_throttle.py [--nso exefs/main]

WHAT IS BEING COMPARED
======================
FFNx's PauseEffectDecorator, which is the pacing skeleton shared by
InterpolationEffectDecorator, ModelInterpolationEffectDecorator and
CameraInterpolationEffectDecorator once the smoothing is stripped out:

    void PauseEffectDecorator::callEffectFunction(uint32_t function)
    {
        byte wasPaused = *this->isBattlePaused;
        if (this->frameCounter % this->frequency != 0)
            *this->isBattlePaused = 1;
        ((void(*)())function)();
        if (this->frameCounter % this->frequency != 0)
            *this->isBattlePaused = wasPaused;
        this->frameCounter++;
    }

is run against the ACTUAL ENCODED WORDS of the pre-call and post-call caves,
executed by arm64emu.py. Not a model of the caves -- the u32s the build writes.
An encoding mistake therefore shows up as a wrong result rather than as a test
that agrees with the same mistake twice.

The emulator fills x1-x18 and x30 with garbage after every modelled translator
call, which is what makes this a real test of the register argument the caves
rest on rather than an assumption written down twice.

WHAT IS COVERED
===============
  1. every slot index, every phase, both values of *isBattlePaused, excluded
     and not -- exhaustively, for both dispatchers;
  2. multi-frame sequences: 40 consecutive frames per slot, checking that
     exactly one frame in `battle_frame_multiplier` is a real step and that
     *isBattlePaused is always back where it started by the time the post cave
     is done;
  3. the registration cave's exclusion-table walk, against every address in the
     table plus addresses that are not in it;
  4. that a throttled slot's phase byte can never reach 0x80 and so can never
     turn itself into an excluded one, over a full wrap;
  5. mutations -- eleven deliberate defects, each of which must be caught. A
     test that cannot fail is not a test.

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
import argparse
import struct
import sys

import a64 as A
import arm64emu as E
import ff7nx_dispatch as D

HOST = 0x700000000
CAVE = 0x1152660                   # a plausible cave address; any works
GUEST_CTX = 0x900000               # host address of the guest register context
FAIL = []


def fail(msg):
    FAIL.append(msg)
    print('  FAIL  %s' % msg)


# --------------------------------------------------------------- the model

class Reference:
    """FFNx's PauseEffectDecorator, per slot, as plain Python."""

    def __init__(self, freq, excluded):
        self.freq = freq
        self.excluded = excluded
        self.counter = 0

    def call(self, paused_before):
        """
        -> (paused_during_the_call, paused_after, real_step)

        `real_step` is FFNx's `frameCounter % frequency == 0`: the frame on
        which the effect actually advances.
        """
        if self.excluded:
            return paused_before, paused_before, True
        real = (self.counter % self.freq) == 0
        during = paused_before if real else 1
        self.counter += 1
        return during, paused_before, real


# ------------------------------------------------------------ the machinery

def make_site(ctx_reg, idx_off, store_reg, hook):
    return dict(ctx_reg=ctx_reg, idx_off=idx_off, store_reg=store_reg,
                hook=hook, displaced=A.str_(store_reg, ctx_reg, 0x10))


def run_pre(words, cpu, cave=CAVE):
    return cpu.run(cave, words)


def new_cpu(mem, ctx, idx, fn_ptr, store_val):
    cpu = E.Cpu(mem, host_base=HOST)
    cpu.sp = 0x800000000
    cpu.x[0] = fn_ptr
    for r in range(1, 31):
        cpu.x[r] = E.GARBAGE
    cpu.x[ctx['reg']] = ctx['base']
    cpu.x[ctx['store_reg']] = store_val
    mem.setu(ctx['base'] + ctx['idx_off'], idx, 4)
    return cpu


def check_survivors(cpu, ctx, fn_ptr, store_val, where):
    """w0 and the displaced store's register must come out untouched."""
    if cpu.x[0] & 0xFFFFFFFF != fn_ptr & 0xFFFFFFFF:
        fail('%s: w0 (the guest function pointer) came back %08X, wanted %08X'
             % (where, cpu.x[0] & 0xFFFFFFFF, fn_ptr))
    got = cpu.x[ctx['store_reg']] & 0xFFFFFFFF
    if got != store_val & 0xFFFFFFFF:
        fail('%s: w%d (the displaced store\'s value) came back %08X, wanted '
             '%08X' % (where, ctx['store_reg'], got, store_val))
    if cpu.sp != 0x800000000:
        fail('%s: sp came back 0x%X, wanted 0x800000000' % (where, cpu.sp))


# ------------------------------------------------------------------ the test

def one_dispatcher(tag, thr, freq, mutate=None, quiet=False, shipped=None):
    """
    Exhaustive single-frame agreement plus a 40-frame sequence per slot.

    `mutate` is a function applied to the emitted word lists, used by the
    mutation suite to inject a defect and prove the test catches it.

    `shipped` is dict(pre, post, cave_pre, cave_post, pre_hook, post_hook) and
    makes this run the words READ OUT OF A BUILT NSO, at the addresses they
    landed on, instead of freshly emitted ones. The caves are position-dependent
    -- every branch inside them, and the branch back, is PC-relative -- so
    verify_throttle_build.py cannot just hand the words over without their
    addresses.
    """
    mask_bits = thr['mask_bits']
    n_slots = 1 << mask_bits
    ctx = dict(reg=thr['ctx_reg'], base=GUEST_CTX, idx_off=thr['idx_off'],
               store_reg=thr['store_reg'])
    if shipped:
        cave_pre, cave_post = shipped['cave_pre'], shipped['cave_post']
        pre_site = make_site(ctx['reg'], ctx['idx_off'], ctx['store_reg'],
                             shipped['pre_hook'])
        post_site = dict(hook=shipped['post_hook'],
                         displaced=shipped['post_displaced'])
        pre, post = shipped['pre'], shipped['post']
    else:
        cave_pre, cave_post = CAVE, CAVE + 0x400
        pre_site = make_site(ctx['reg'], ctx['idx_off'], ctx['store_reg'],
                             0x7D3050)
        post_site = dict(hook=0x7D3058, displaced=A.mov_reg(0, 19))
        pre = D.build_throttle_pre_cave(cave_pre, pre_site, thr,
                                        thr['paused_guest'], mask_bits,
                                        {2: 1, 4: 2, 8: 3}[freq])
        post = D.build_throttle_post_cave(cave_post, post_site, thr)
    if mutate:
        pre, post = mutate(pre, post)

    paused_host = HOST + thr['paused_guest']
    checked = 0

    # ---- 1. exhaustive single frame ------------------------------------
    for idx in range(n_slots):
        for phase in range(freq):
            for excluded in (False, True):
                for was in (0, 1):
                    mem = E.Mem()
                    mem.setu(thr['ctr'] + idx, 0x80 if excluded else phase, 1)
                    mem.setu(paused_host, was, 1)
                    ref = Reference(freq, excluded)
                    ref.counter = phase
                    want_during, want_after, _real = ref.call(was)

                    cpu = new_cpu(mem, ctx, idx, 0x004E3D51, 0x00123456)
                    out = run_pre(pre, cpu, cave_pre)
                    if out != pre_site['hook'] + 4:
                        fail('%s pre: left the cave to 0x%X, wanted 0x%X'
                             % (tag, out, pre_site['hook'] + 4))
                        return
                    check_survivors(cpu, ctx, 0x004E3D51, 0x00123456,
                                    '%s pre idx=%d phase=%d' % (tag, idx, phase))
                    got_during = mem.u(paused_host, 1)
                    if got_during != want_during:
                        fail('%s idx=%d phase=%d excl=%d was=%d: during the '
                             'call *paused=%d, FFNx says %d'
                             % (tag, idx, phase, excluded, was, got_during,
                                want_during))
                        return
                    # the guest function runs here; model it as a clobberer
                    cpu2 = E.Cpu(mem, host_base=HOST)
                    cpu2.sp = 0x800000000
                    for r in range(31):
                        cpu2.x[r] = E.GARBAGE
                    cpu2.x[19] = 0x00BF23B8
                    out = cpu2.run(cave_post, post)
                    if out != post_site['hook'] + 4:
                        fail('%s post: left the cave to 0x%X, wanted 0x%X'
                             % (tag, out, post_site['hook'] + 4))
                        return
                    got_after = mem.u(paused_host, 1)
                    if got_after != want_after:
                        fail('%s idx=%d phase=%d excl=%d was=%d: after the '
                             'call *paused=%d, FFNx says %d'
                             % (tag, idx, phase, excluded, was, got_after,
                                want_after))
                        return
                    if cpu2.x[0] & 0xFFFFFFFF != 0x00BF23B8:
                        fail('%s post: the displaced `mov w0,w19` did not run'
                             % tag)
                        return
                    want_ctr = (0x80 if excluded
                                else (phase + 1) % freq)
                    got_ctr = mem.u(thr['ctr'] + idx, 1)
                    if got_ctr != want_ctr:
                        fail('%s idx=%d phase=%d excl=%d: counter is %02X, '
                             'wanted %02X' % (tag, idx, phase, excluded,
                                              got_ctr, want_ctr))
                        return
                    checked += 1

    # ---- 2. 40-frame sequences ------------------------------------------
    for idx in (0, 1, n_slots // 2, n_slots - 1):
        for was in (0, 1):
            mem = E.Mem()
            mem.setu(thr['ctr'] + idx, 0, 1)
            mem.setu(paused_host, was, 1)
            ref = Reference(freq, False)
            steps = 0
            for frame in range(40):
                want_during, want_after, real = ref.call(was)
                cpu = new_cpu(mem, ctx, idx, 0x00476AFB, 0x0000BEEF)
                run_pre(pre, cpu, cave_pre)
                if mem.u(paused_host, 1) != want_during:
                    fail('%s seq idx=%d frame=%d: *paused=%d, FFNx says %d'
                         % (tag, idx, frame, mem.u(paused_host, 1),
                            want_during))
                    return
                steps += real
                cpu2 = E.Cpu(mem, host_base=HOST)
                cpu2.sp = 0x800000000
                cpu2.run(cave_post, post)
                if mem.u(paused_host, 1) != was:
                    fail('%s seq idx=%d frame=%d: *paused left at %d, not the '
                         '%d it started at'
                         % (tag, idx, frame, mem.u(paused_host, 1), was))
                    return
                checked += 1
            if steps != 40 // freq:
                fail('%s seq idx=%d: %d real steps in 40 frames, wanted %d'
                     % (tag, idx, steps, 40 // freq))
                return

    # ---- 3. the phase byte can never become an exclusion marker ---------
    mem = E.Mem()
    mem.setu(thr['ctr'], 0, 1)
    mem.setu(paused_host, 0, 1)
    for frame in range(1024):
        cpu = new_cpu(mem, ctx, 0, 0x00476AFB, 0)
        run_pre(pre, cpu, cave_pre)
        v = mem.u(thr['ctr'], 1)
        if v & 0x80:
            fail('%s: after %d frames the phase byte is %02X -- bit 7 is the '
                 'exclusion marker and must never be reachable by counting'
                 % (tag, frame, v))
            return
        cpu2 = E.Cpu(mem, host_base=HOST)
        cpu2.sp = 0x800000000
        cpu2.run(cave_post, post)
        checked += 1

    # ---- 4. no slot but `idx` is ever written --------------------------
    mem = E.Mem()
    for i in range(n_slots):
        mem.setu(thr['ctr'] + i, 0x11, 1)
    mem.setu(paused_host, 0, 1)
    touch = 7 % n_slots
    mem.setu(thr['ctr'] + touch, 0, 1)
    cpu = new_cpu(mem, ctx, touch, 0x00476AFB, 0)
    run_pre(pre, cpu, cave_pre)
    for i in range(n_slots):
        if i == touch:
            continue
        if mem.u(thr['ctr'] + i, 1) != 0x11:
            fail('%s: touching slot %d also wrote slot %d' % (tag, touch, i))
            return
    checked += 1

    # ---- 5. the write NEVER leaves the block, whatever idx holds -------
    #
    # The guest register slot idx is read from is guest memory: the dispatcher
    # writes the loop counter there, but nothing in the cave can prove what is
    # there when the cave runs. The mask is what makes the write safe, and this
    # is what makes the mask observable -- without it a stale or garbage index
    # writes a byte at an arbitrary distance into live BSS, which is precisely
    # the failure that corrupted an earlier build while every gate passed.
    for raw_idx in (n_slots, n_slots + 1, 0x1234, 0x7FFF, 0xFFFF, 0xFFFFFFFF,
                    0x80000000):
        mem = E.Mem()
        mem.setu(paused_host, 0, 1)
        cpu = new_cpu(mem, ctx, raw_idx, 0x00476AFB, 0)
        run_pre(pre, cpu, cave_pre)
        written = [a for pg, buf in mem.pages.items()
                   for a in range(pg * E.PAGE, pg * E.PAGE + E.PAGE)
                   if buf[a - pg * E.PAGE] and thr['ctr'] <= a]
        outside = [a for a in written if not (thr['ctr'] <= a
                                              < thr['ctr'] + n_slots)
                   and a not in (thr['saved'], thr['did'])
                   and not (thr['pptr'] <= a < thr['pptr'] + 8)]
        if outside:
            fail('%s: idx=0x%X wrote outside the throttle block at %s'
                 % (tag, raw_idx, ', '.join('0x%X' % a for a in outside[:4])))
            return
        checked += 1

    if not quiet:
        print('  ok    %-10s %d cave execution(s), pre %d words, post %d words'
              % (tag, checked, len(pre), len(post)))
    return checked


def registration(tag, thr, table, quiet=False, allow=False):
    """
    The registration table walk, against every address in it and some not.

    Two modes, and the difference is the whole safety of the narrow fix:

      allow=False  the table lists functions NOT to throttle. A miss means
                   throttle. This is effect100, where FFNx throttles nearly
                   everything.
      allow=True   the table lists the ONLY functions to throttle. A miss means
                   leave alone. This is `aura-throttle`, which needs to touch
                   two effect60 slots and no others -- throttling all sixty
                   makes the whole battle step at 15 Hz.

    Getting the polarity backwards would either throttle nothing or throttle
    everything, so both directions are checked explicitly rather than assumed
    from the one that happens to be exercised elsewhere.
    """
    mask_bits = thr['mask_bits']
    n_slots = 1 << mask_bits
    site = dict(ctx_reg=20, idx_off=8, fn_val_reg=21, hook=0x78329C,
                displaced=A.str_(21, 0, 0))
    t = dict(thr)
    t['table'] = table
    t['allow'] = allow
    words = D.build_addfn_cave(CAVE, site, None, mask_bits, t)
    blob = struct.pack('<%dI' % len(words), *words)
    checked = 0
    probes = [(va, True) for va in table]
    probes += [(va, False) for va in
               (0x400000, 0x42782B, 0x5BBD25, 0x000001, 0x7B5620, 0xFFFFFFFF)
               if va not in table]
    for idx in (0, 1, n_slots - 1):
        for va, want_excluded in probes:
            mem = E.Mem()
            # The exclusion table is DATA inside the cave, so the cave has to be
            # in memory as well as in the instruction list -- `adr`+`ldr` walks
            # it out of .text. Reading it out of the same bytes the build ships
            # is the point: a table emitted at the wrong offset reads zeroes and
            # every function comes out "not excluded", which is silent.
            mem.write(CAVE, blob)
            mem.setu(thr['ctr'] + idx, 0x5A, 1)
            cpu = E.Cpu(mem, host_base=HOST)
            cpu.sp = 0x800000000
            for r in range(31):
                cpu.x[r] = E.GARBAGE
            cpu.x[20] = GUEST_CTX
            cpu.x[21] = va
            cpu.x[0] = HOST + 0x00BF2858 + 4 * idx
            mem.setu(GUEST_CTX + 8, idx, 4)
            out = cpu.run(CAVE, words)
            if out != site['hook'] + 4:
                fail('%s reg: left the cave to 0x%X' % (tag, out))
                return
            if mem.u(HOST + 0x00BF2858 + 4 * idx, 4) != va:
                fail('%s reg: the displaced `str wFN,[x0]` did not run for '
                     '0x%06X' % (tag, va))
                return
            got = mem.u(thr['ctr'] + idx, 1)
            if allow:
                want = 0 if want_excluded else 0x80
            else:
                want = 0x80 if want_excluded else 0
            if got != want:
                fail('%s reg%s 0x%06X: seeded %02X, wanted %02X (%s)'
                     % (tag, ' allow' if allow else '', va, got, want,
                        ('throttled' if allow else 'excluded') if want_excluded
                        else ('left alone' if allow else 'throttled')))
                return
            if cpu.sp != 0x800000000:
                fail('%s reg: sp came back 0x%X' % (tag, cpu.sp))
                return
            checked += 1
    if not quiet:
        print('  ok    %-10s %s %d registration(s), %d table entries, %d words'
              % (tag, 'ALLOW ' if allow else 'exclude', checked, len(table),
                 len(words)))
    return checked


def kotr_counter_hold(thr, quiet=False):
    """Exercise FFNx's FixCounterException path, not the ordinary pause path."""
    if not thr.get('kotr'):
        return 0
    ctx = dict(reg=thr['ctx_reg'], base=GUEST_CTX, idx_off=thr['idx_off'],
               store_reg=thr['store_reg'])
    pre_site = make_site(ctx['reg'], ctx['idx_off'], ctx['store_reg'], 0x7D3050)
    post_site = dict(hook=0x7D3058, displaced=A.mov_reg(0, 19))
    pre = D.build_throttle_pre_cave(CAVE, pre_site, thr,
                                    thr['paused_guest'], thr['mask_bits'], 2)
    post = D.build_throttle_post_cave(CAVE + 0x800, post_site, thr)
    checked = 0

    # All four phases, exceptional and ordinary counters, and both incoming
    # pause states.  A KOTR call must never touch g_is_battle_paused.
    for phase in range(4):
        for has_exc in (False, True):
            for was_paused in (0, 1):
                idx, counter = 7, 26
                mem = E.Mem()
                mem.setu(thr['ctr'] + idx, 0x40 | phase, 1)
                mem.setu(thr['kotr_exc'] + idx, counter if has_exc else 0, 1)
                data_host = HOST + thr['data_base'] + idx * thr['stride']
                mem.setu(data_host + 0, 0x1234, 2)
                mem.setu(data_host + 2, counter, 2)
                mem.setu(HOST + thr['paused_guest'], was_paused, 1)
                cpu = new_cpu(mem, ctx, idx, 0x47EB92, 0x00123456)
                out = cpu.run(CAVE, pre)
                if out != pre_site['hook'] + 4:
                    fail('KOTR pre phase=%d left to 0x%X' % (phase, out))
                    return
                new_phase = (phase + 1) & 3
                repeat = new_phase != 1
                if mem.u(HOST + thr['paused_guest'], 1) != was_paused:
                    fail('KOTR phase=%d changed battle pause' % phase)
                    return
                if mem.u(thr['kotr_disable'], 1) != (1 if repeat else 0):
                    fail('KOTR phase=%d nested-add guard wrong' % phase)
                    return
                during_counter = mem.u(data_host + 2, 2)
                want_counter = counter + 1 if repeat and has_exc else counter
                if during_counter != want_counter:
                    fail('KOTR phase=%d exception=%d exposed counter %d, want %d'
                         % (phase, has_exc, during_counter, want_counter))
                    return

                if repeat:
                    # Model a knight function advancing/retiring the slot.
                    mem.setu(data_host + 2, during_counter + 1, 2)
                    mem.setu(data_host + 0, 0xFFFF, 2)
                cpu2 = E.Cpu(mem, host_base=HOST)
                cpu2.sp = 0x800000000
                for r in range(31):
                    cpu2.x[r] = E.GARBAGE
                cpu2.x[19] = 0x00BF23B8
                out = cpu2.run(CAVE + 0x800, post)
                if out != post_site['hook'] + 4:
                    fail('KOTR post phase=%d left to 0x%X' % (phase, out))
                    return
                if repeat:
                    if mem.u(data_host + 2, 2) != counter:
                        fail('KOTR phase=%d did not restore field_2' % phase)
                        return
                    want_active = 0xFFFF if new_phase == 0 else 0x1234
                    if mem.u(data_host + 0, 2) != want_active:
                        fail('KOTR phase=%d active=%04X, want %04X'
                             % (phase, mem.u(data_host + 0, 2), want_active))
                        return
                    if mem.u(thr['kotr_disable'], 1) != 0:
                        fail('KOTR phase=%d left nested-add guard set' % phase)
                        return
                checked += 1
    if not quiet:
        print('  ok    KOTR      %d counter-hold execution(s)' % checked)
    return checked


def kotr_registration(thr, quiet=False, shipped=None):
    """KOTR table seeding plus FFNx's nested-add suppression contract."""
    if not thr.get('kotr'):
        return 0
    site = dict(ctx_reg=20, idx_off=8, fn_val_reg=21, hook=0x78329C,
                displaced=A.str_(21, 0, 0))
    t = dict(thr)
    t['table'] = [va for _n, va in t['exclude']]
    if shipped:
        cave = shipped['cave']
        site['hook'] = shipped['hook']
        words = shipped['words']
    else:
        cave = CAVE
        words = D.build_addfn_cave(cave, site, None, t['mask_bits'], t)
    blob = (struct.pack('<%dI' % len(words), *words)
            if not isinstance(words, dict) else None)
    checked = 0
    for idx, (_name, va, exc) in enumerate(t['kotr']):
        mem = E.Mem()
        if blob is not None:
            mem.write(cave, blob)
        cpu = E.Cpu(mem, host_base=HOST)
        cpu.sp = 0x800000000
        for r in range(31):
            cpu.x[r] = E.GARBAGE
        cpu.x[20] = GUEST_CTX
        cpu.x[21] = va
        cpu.x[0] = HOST + 0x00BF2858 + 4 * idx
        mem.setu(GUEST_CTX + 8, idx, 4)
        out = cpu.run(cave, words)
        if out != site['hook'] + 4:
            fail('KOTR registration 0x%06X left to 0x%X' % (va, out))
            return
        if mem.u(t['ctr'] + idx, 1) != 0x40:
            fail('KOTR registration 0x%06X did not seed marker' % va)
            return
        if mem.u(t['kotr_exc'] + idx, 1) != exc:
            fail('KOTR registration 0x%06X exception=%d, want %d'
                 % (va, mem.u(t['kotr_exc'] + idx, 1), exc))
            return
        if mem.u(HOST + 0x00BF2858 + 4 * idx, 4) != va:
            fail('KOTR registration 0x%06X skipped stock function store' % va)
            return
        checked += 1

    # During a held repeat, add_fn must return the selected slot without
    # storing/registering anything.  The cave branches to the proven stock
    # common-return path used after registration.
    mem = E.Mem()
    if blob is not None:
        mem.write(cave, blob)
    mem.setu(t['kotr_disable'], 1, 1)
    mem.setu(HOST + 0x00BF2858, 0xDEADBEEF, 4)
    cpu = E.Cpu(mem, host_base=HOST)
    cpu.sp = 0x800000000
    for r in range(31):
        cpu.x[r] = E.GARBAGE
    cpu.x[20] = GUEST_CTX
    cpu.x[21] = t['kotr'][0][1]
    cpu.x[0] = HOST + 0x00BF2858
    mem.setu(GUEST_CTX + 8, 0, 4)
    out = cpu.run(cave, words)
    if out != t['kotr_add_skip']:
        fail('KOTR nested add left to 0x%X, want 0x%X'
             % (out, t['kotr_add_skip']))
        return
    if mem.u(HOST + 0x00BF2858, 4) != 0xDEADBEEF:
        fail('KOTR nested add performed the displaced function store')
        return
    checked += 1
    if not quiet:
        print('  ok    KOTR      %d registration/suppression execution(s)'
              % checked)
    return checked


def model_registration(thr, quiet=False, shipped=None):
    """Every FFNx summon-model function seeds bit5 and its threshold bit."""
    if not thr.get('model'):
        return 0
    site = dict(ctx_reg=20, idx_off=8, fn_val_reg=21, hook=0x78329C,
                displaced=A.str_(21, 0, 0))
    t = dict(thr)
    t['table'] = [va for _n, va in t['exclude']]
    if shipped:
        cave = shipped['cave']
        site['hook'] = shipped['hook']
        words = shipped['words']
    else:
        cave = CAVE
        words = D.build_addfn_cave(cave, site, None, t['mask_bits'], t)
    blob = (struct.pack('<%dI' % len(words), *words)
            if not isinstance(words, dict) else None)
    checked = 0
    for idx, (_name, va, marker) in enumerate(t['model']):
        mem = E.Mem()
        if blob is not None:
            mem.write(cave, blob)
        cpu = E.Cpu(mem, host_base=HOST)
        cpu.sp = 0x800000000
        for r in range(31):
            cpu.x[r] = E.GARBAGE
        cpu.x[20] = GUEST_CTX
        cpu.x[21] = va
        cpu.x[0] = HOST + 0x00BF2858 + 4 * idx
        mem.setu(GUEST_CTX + 8, idx, 4)
        out = cpu.run(cave, words)
        if out != site['hook'] + 4:
            fail('MODEL registration 0x%06X left to 0x%X' % (va, out))
            return
        want = 0x20 | marker
        if mem.u(t['ctr'] + idx, 1) != want:
            fail('MODEL registration 0x%06X seeded %02X, want %02X'
                 % (va, mem.u(t['ctr'] + idx, 1), want))
            return
        if mem.u(HOST + 0x00BF2858 + 4 * idx, 4) != va:
            fail('MODEL registration 0x%06X skipped stock function store' % va)
            return
        checked += 1
    if not quiet:
        print('  ok    MODEL     %d registration execution(s)' % checked)
    return checked


def one_call_balanced(thr, freq=4, quiet=False, shipped=None,
                      shipped_reg=None):
    """Exercise Bahamut ZERO's exact OneCall path and its guest-stack repair."""
    if not thr.get('one_call'):
        return 0
    ctx = dict(reg=thr['ctx_reg'], base=GUEST_CTX, idx_off=thr['idx_off'],
               store_reg=thr['store_reg'])
    if shipped:
        pre_base, post_base = shipped['cave_pre'], shipped['cave_post']
        pre_site = make_site(ctx['reg'], ctx['idx_off'], ctx['store_reg'],
                             shipped['pre_hook'])
        post_site = dict(hook=shipped['post_hook'],
                         displaced=shipped['post_displaced'])
        pre, post = shipped['pre'], shipped['post']
    else:
        pre_base, post_base = CAVE, CAVE + 0x1000
        pre_site = make_site(ctx['reg'], ctx['idx_off'], ctx['store_reg'],
                             0x7D3050)
        post_site = dict(hook=thr['post'], displaced=A.mov_reg(0, 19))
        pre = D.build_throttle_pre_cave(pre_base, pre_site, thr,
                                        thr['paused_guest'], thr['mask_bits'],
                                        {2: 1, 4: 2, 8: 3}[freq])
        post = D.build_throttle_post_cave(post_base, post_site, thr)
    checked = 0

    # Registration must distinguish this callback from both an ordinary
    # throttled callback and every exclusion-table entry.
    site = dict(ctx_reg=20, idx_off=8, fn_val_reg=21, hook=0x78329C,
                displaced=A.str_(21, 0, 0))
    t = dict(thr)
    t['table'] = [va for _n, va in t['exclude']]
    if shipped_reg:
        reg_base, reg = shipped_reg['cave'], shipped_reg['words']
        site['hook'] = shipped_reg['hook']
    else:
        reg_base = CAVE + 0x2000
        reg = D.build_addfn_cave(reg_base, site, None, t['mask_bits'], t)
    reg_blob = struct.pack('<%dI' % len(reg), *reg)
    for idx, (_name, va) in enumerate(t['one_call']):
        mem = E.Mem()
        mem.write(reg_base, reg_blob)
        cpu = E.Cpu(mem, host_base=HOST)
        cpu.sp = 0x800000000
        for r in range(31):
            cpu.x[r] = E.GARBAGE
        cpu.x[20] = GUEST_CTX
        cpu.x[21] = va
        cpu.x[0] = HOST + 0x00BF2858 + 4 * idx
        mem.setu(GUEST_CTX + 8, idx, 4)
        out = cpu.run(reg_base, reg)
        if out != site['hook'] + 4 or mem.u(t['ctr'] + idx, 1) != 0x10:
            fail('ONECALL registration 0x%06X did not seed marker 0x10' % va)
            return
        if mem.u(HOST + 0x00BF2858 + 4 * idx, 4) != va:
            fail('ONECALL registration skipped the stock function store')
            return
        checked += 1

    # The translated hook is after `sub guest_esp,#4`. On held phases the
    # cave must restore exactly four bytes and publish that value before it
    # branches over the thunk; on the real phase it must preserve stock state.
    idx = 9
    guest_esp_after_sub = 0x0012FFF8
    for phase in range(freq):
        mem = E.Mem()
        mem.setu(thr['ctr'] + idx, 0x10 | phase, 1)
        mem.setu(HOST + thr['paused_guest'], 0, 1)
        cpu = new_cpu(mem, ctx, idx, 0x00484A16, guest_esp_after_sub)
        out = cpu.run(pre_base, pre)
        real = phase == 0
        want_out = pre_site['hook'] + 4 if real else thr['post']
        if out != want_out:
            fail('ONECALL phase=%d left to 0x%X, wanted 0x%X'
                 % (phase, out, want_out))
            return
        if cpu.x[0] & 0xFFFFFFFF != 0x00484A16:
            fail('ONECALL phase=%d corrupted callback pointer w0=%08X'
                 % (phase, cpu.x[0] & 0xFFFFFFFF))
            return
        want_esp = (guest_esp_after_sub if real
                    else guest_esp_after_sub + 4)
        got_esp = mem.u(GUEST_CTX + 0x10, 4)
        if got_esp != want_esp:
            fail('ONECALL phase=%d stored guest ESP %08X, wanted %08X'
                 % (phase, got_esp, want_esp))
            return
        if mem.u(thr['ctr'] + idx, 1) != 0x10 | ((phase + 1) % freq):
            fail('ONECALL phase=%d advanced marker incorrectly' % phase)
            return
        if mem.u(HOST + thr['paused_guest'], 1) != 0:
            fail('ONECALL phase=%d touched g_is_battle_paused' % phase)
            return
        if cpu.sp != 0x800000000:
            fail('ONECALL phase=%d changed host SP' % phase)
            return

        # Both routes encounter the post hook in the real program. The skipped
        # route has did=0, so the post cave must only replay mov w0,w19.
        cpu2 = E.Cpu(mem, host_base=HOST)
        cpu2.sp = 0x800000000
        for r in range(31):
            cpu2.x[r] = E.GARBAGE
        cpu2.x[19] = 0x00BF23B8
        out = cpu2.run(post_base, post)
        if out != post_site['hook'] + 4 or cpu2.x[0] != 0x00BF23B8:
            fail('ONECALL phase=%d post hook did not replay displaced mov'
                 % phase)
            return
        checked += 1
    if not quiet:
        print('  ok    ONECALL   %d registration/stack-balance execution(s)'
              % checked)
    return checked


def model_interpolation(thr, quiet=False, shipped=None):
    """Execute the encoded ModelInterpolationEffectDecorator path end to end."""
    if not thr.get('model'):
        return 0
    ctx = dict(reg=thr['ctx_reg'], base=GUEST_CTX, idx_off=thr['idx_off'],
               store_reg=thr['store_reg'])
    if shipped:
        pre_base, post_base = shipped['cave_pre'], shipped['cave_post']
        pre_site = make_site(ctx['reg'], ctx['idx_off'], ctx['store_reg'],
                             shipped['pre_hook'])
        post_site = dict(hook=shipped['post_hook'],
                         displaced=shipped['post_displaced'])
        pre, post = shipped['pre'], shipped['post']
    else:
        pre_base = CAVE
        pre_site = make_site(ctx['reg'], ctx['idx_off'], ctx['store_reg'],
                             0x7D3050)
        post_site = dict(hook=0x7D3058, displaced=A.mov_reg(0, 19))
        pre = D.build_throttle_pre_cave(pre_base, pre_site, thr,
                                        thr['paused_guest'],
                                        thr['mask_bits'], 2)
        post_base = CAVE + 0x1000
        post = D.build_throttle_post_cave(post_base, post_site, thr)

    def signed3(mem, at):
        return tuple(E.s16(mem.u(at + 2 * i, 2)) for i in range(3))

    def put3(mem, at, p):
        for i, v in enumerate(p):
            mem.setu(at + 2 * i, v, 2)

    def interp(a, b, step):
        def q(v):
            return abs(v) // 4 if v >= 0 else -(abs(v) // 4)
        return tuple(x + q((y - x) * step) for x, y in zip(a, b))

    checked = 0
    cases = [
        # normal smooth movement, including negative division toward zero
        (0x20, (0, 0, 0), (-3, 3, -7), False),
        # exactly 1000 is a teleport for the normal threshold
        (0x20, (0, 0, 0), (1000, 0, 0), True),
        # but is smooth for Alexander's 3000 threshold marker
        (0x28, (0, 0, 0), (1000, 0, 0), False),
    ]
    for case_no, (marker, a, b, teleport) in enumerate(cases):
        idx = 11 + case_no
        mem = E.Mem()
        # Paged translation makes adjacent guest pages non-contiguous and
        # therefore catches address arithmetic accidentally done after a
        # translation instead of on the guest effective address.
        probe = E.Cpu(mem, host_base=HOST, paged=True)
        paused_host = probe.guest_to_host(thr['paused_guest'])
        pos_host = probe.guest_to_host(thr['model_pos'])
        data_guest = thr['data_base'] + idx * thr['stride']
        data_host = probe.guest_to_host(data_guest)
        mem.setu(thr['ctr'] + idx, marker, 1)
        mem.setu(paused_host, 0, 1)
        mem.setu(data_host, 0x1234, 2)
        put3(mem, pos_host, (99, 99, 99))

        # Frame zero: real call, capture endpoint A, no interpolation yet.
        endpoints = [a, b, b, b, b]
        wants = [a,
                 a if teleport else interp(a, b, 1),
                 a if teleport else interp(a, b, 2),
                 a if teleport else interp(a, b, 3),
                 b]
        for frame in range(5):
            cpu = new_cpu(mem, ctx, idx, 0x50B1A3, 0x00123456)
            cpu.paged = True
            out = cpu.run(pre_base, pre)
            if out != pre_site['hook'] + 4:
                fail('MODEL pre case=%d frame=%d left to 0x%X'
                     % (case_no, frame, out))
                return
            real = frame in (0, 1)
            during = mem.u(paused_host, 1)
            if during != (0 if real else 1):
                fail('MODEL case=%d frame=%d pause=%d, want %d'
                     % (case_no, frame, during, 0 if real else 1))
                return
            if real:
                put3(mem, pos_host, endpoints[frame])

            cpu2 = E.Cpu(mem, host_base=HOST, paged=True)
            cpu2.sp = 0x800000000
            for r in range(31):
                cpu2.x[r] = E.GARBAGE
            cpu2.x[19] = 0x00BF23B8
            out = cpu2.run(post_base, post)
            if out != post_site['hook'] + 4:
                fail('MODEL post case=%d frame=%d left to 0x%X'
                     % (case_no, frame, out))
                return
            got = signed3(mem, pos_host)
            if got != wants[frame]:
                fail('MODEL case=%d frame=%d position=%r, want %r'
                     % (case_no, frame, got, wants[frame]))
                return
            if mem.u(paused_host, 1) != 0:
                fail('MODEL case=%d frame=%d did not restore pause'
                     % (case_no, frame))
                return
            checked += 1

    # Retirement on the real phase-one call is held through steps 2/3 and
    # becomes FFFF only after step 4.
    idx = 21
    mem = E.Mem()
    probe = E.Cpu(mem, host_base=HOST, paged=True)
    paused_host = probe.guest_to_host(thr['paused_guest'])
    pos_host = probe.guest_to_host(thr['model_pos'])
    data_host = probe.guest_to_host(thr['data_base'] + idx * thr['stride'])
    mem.setu(thr['ctr'] + idx, 0x20, 1)
    mem.setu(data_host, 0x4321, 2)
    mem.setu(paused_host, 0, 1)
    for frame in range(5):
        cpu = new_cpu(mem, ctx, idx, 0x50B1A3, 0x00123456)
        cpu.paged = True
        cpu.run(pre_base, pre)
        if frame == 0:
            put3(mem, pos_host, (0, 0, 0))
        elif frame == 1:
            put3(mem, pos_host, (40, 0, 0))
            mem.setu(data_host, 0xFFFF, 2)
        cpu2 = E.Cpu(mem, host_base=HOST, paged=True)
        cpu2.sp = 0x800000000
        for r in range(31):
            cpu2.x[r] = E.GARBAGE
        cpu2.x[19] = 0x00BF23B8
        cpu2.run(post_base, post)
        want_active = 0xFFFF if frame == 4 else 0x4321
        if mem.u(data_host, 2) != want_active:
            fail('MODEL retirement frame=%d active=%04X, want %04X'
                 % (frame, mem.u(data_host, 2), want_active))
            return
        checked += 1

    if not quiet:
        print('  ok    MODEL     %d interpolation/retirement execution(s)'
              % checked)
    return checked


# ------------------------------------------------------------- mutations

def mutations(tag, thr, freq):
    """
    Each entry breaks the caves in one specific way and MUST be caught.

    A test that cannot fail is not a test. Two of the first mutations written
    for the earlier suites were not caught, because they mutated a source table
    while the suite read the generated one.
    """
    def drop_counter_store(pre, post):
        return [w for w in pre if w != A.strb(16, 17, 0)], post

    def never_restore(pre, post):
        return pre, [w for w in post if w != A.strb(16, 17, 0)]

    def never_pause(pre, post):
        out = []
        for w in pre:
            out.append(A.strb(A.WZR, 0, 0) if w == A.strb(16, 0, 0) else w)
        return out, post

    def ignore_exclusion(pre, post):
        # Break the long-range-safe `cmp w16,#0x80; b.hs OUT` class test.
        # (The old implementation used TBNZ bit 7, whose 32 KiB range is too
        # short once the production allocator scatters a large model cave.)
        return [A.cmp_imm(16, 0xFF) if w == A.cmp_imm(16, 0x80) else w
                for w in pre], post

    def unmasked_index(pre, post):
        # drop the `and w16,w16,#mask` that keeps idx inside the block
        seen = [False]

        def f(w):
            if w == A.and_mask(16, 16, thr['mask_bits']) and not seen[0]:
                seen[0] = True
                return A.mov_reg(16, 16)
            return w
        return [f(w) for w in pre], post

    def wrong_phase(pre, post):
        # `cmp w16,#1` -> `cmp w16,#0`: the real step lands on the wrong frame
        return [A.cmp_imm(16, 0) if w == A.cmp_imm(16, 1) else w
                for w in pre], post

    def counter_not_wrapped(pre, post):
        # drop the `and w16,w16,#(freq-1)` so the phase byte runs to 0x80
        bits = {2: 1, 4: 2, 8: 3}[freq]
        return [A.mov_reg(16, 16) if w == A.and_mask(16, 16, bits) else w
                for w in pre], post

    def saved_never_written(pre, post):
        off = thr['saved'] - thr['ctr']
        return [w for w in pre if w != A.strb(16, 17, off)], post

    def did_never_set(pre, post):
        off = thr['did'] - thr['ctr']
        return [w for w in pre if w != A.strb(16, 17, off)], post

    def did_never_cleared(pre, post):
        off = thr['did'] - thr['ctr']
        return [w for w in pre if w != A.strb(A.WZR, 17, off)], post

    def fn_ptr_clobbered(pre, post):
        # do not restore w0 after the translator call
        return [w for w in pre if w != A.ldr64(0, A.SP, 0)], post

    cases = [('counter never stored', drop_counter_store),
             ('paused never restored', never_restore),
             ('paused never set', never_pause),
             ('exclusion marker ignored', ignore_exclusion),
             ('slot index not masked', unmasked_index),
             ('real step on the wrong phase', wrong_phase),
             ('phase counter not wrapped', counter_not_wrapped),
             ('wasPaused never captured', saved_never_written),
             ('did never set', did_never_set),
             ('did never cleared', did_never_cleared),
             ('function pointer clobbered', fn_ptr_clobbered)]
    caught = 0
    for name, fn in cases:
        before = len(FAIL)
        try:
            one_dispatcher(tag, thr, freq, mutate=fn, quiet=True)
        except Exception as exc:                       # a crash is a catch
            del FAIL[before:]
            print('  ok    caught: %-32s (%s)' % (name, type(exc).__name__))
            caught += 1
            continue
        if len(FAIL) == before:
            print('  FAIL  NOT caught: %s -- this mutation is invisible to the '
                  'suite' % name)
        else:
            del FAIL[before:]
            print('  ok    caught: %s' % name)
            caught += 1
    return caught, len(cases)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--nso', default='dump/exefs/main')
    ap.add_argument('--freq', type=int, default=4)
    a = ap.parse_args()

    try:
        from ff7nx_dispatch_sites import DISPATCH_SITES, PAUSED_GUEST
    except ImportError:
        sys.exit('ff7nx_dispatch_sites.py is missing -- regenerate it:\n'
                 '  python3 ff7nx_locate.py --exe ff7_en --nso %s '
                 '--emit ff7nx_dispatch_sites.py' % a.nso)

    print('pause-throttle differential suite, battle_frame_multiplier = %d'
          % a.freq)
    print('\nsingle-frame exhaustive + sequences + wrap + isolation:')
    total = 0
    tags = [t for t in ('effect100', 'effect60')
            if DISPATCH_SITES.get(t, {}).get('throttle')]
    if not tags:
        sys.exit('no throttle sites in ff7nx_dispatch_sites.py')
    for tag in tags:
        d = DISPATCH_SITES[tag]
        thr = dict(d['throttle'])
        thr['paused_guest'] = PAUSED_GUEST
        thr['idx_off'] = d['disp_hook']['idx_off']
        total += one_dispatcher(tag, thr, a.freq) or 0

    print('\nregistration cave, table walk (both polarities):')
    for tag in tags:
        d = DISPATCH_SITES[tag]
        thr = dict(d['throttle'])
        thr['paused_guest'] = PAUSED_GUEST
        table = [va for _n, va in thr['exclude']]
        total += registration(tag, thr, table) or 0
        if thr.get('aura_allow'):
            # The narrow `aura-throttle` list: these two ARE throttled and
            # every other slot is left completely alone.
            al = [va for _n, va in thr['aura_allow']]
            total += registration(tag, thr, al, allow=True) or 0

    print('\nKnights of the Round counter-hold decorator:')
    d = DISPATCH_SITES.get('effect100', {})
    if d.get('throttle'):
        thr = dict(d['throttle'])
        thr['paused_guest'] = PAUSED_GUEST
        thr['idx_off'] = d['disp_hook']['idx_off']
        total += kotr_registration(thr) or 0
        total += kotr_counter_hold(thr) or 0

    print('\nSummon model interpolation decorator:')
    if d.get('throttle'):
        total += model_registration(thr) or 0
        total += model_interpolation(thr) or 0

    print('\nBahamut ZERO exact OneCall decorator:')
    if d.get('throttle'):
        total += one_call_balanced(thr, a.freq) or 0

    print('\nmutations (each must be caught):')
    d = DISPATCH_SITES[tags[0]]
    thr = dict(d['throttle'])
    thr['paused_guest'] = PAUSED_GUEST
    thr['idx_off'] = d['disp_hook']['idx_off']
    caught, n = mutations(tags[0], thr, a.freq)

    print('\n%d cave execution(s), %d/%d mutations caught, %d failure(s)'
          % (total, caught, n, len(FAIL)))
    if FAIL or caught != n:
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
