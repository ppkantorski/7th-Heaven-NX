"""BUILD 747: FFNx's InterpolationEffectDecorator (ff7nx_fxinterp,
native/fxinterp.c), run in unicorn against a transcription of FFNx's C++.

The rig drives the two draw hooks the way the game does inside a decorated
effect slot: the throttle publishes (slot, phase) before the call and clears
it after; the real call draws state k, the paused calls draw state k+1; each
call makes a fixed list of material / sprite draws from distinct guest call
sites. After every hook the guest memory it may touch is compared with what
FFNx's decorator would have left there.

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
import random
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ff7nx_fxinterp as FX                                    # noqa: E402

unicorn = pytest.importorskip('unicorn')
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM            # noqa: E402
from unicorn.arm64_const import (UC_ARM64_REG_X4, UC_ARM64_REG_LR as _LR,  # noqa: E402,F401
                                 UC_ARM64_REG_X0, UC_ARM64_REG_X1,  # noqa: E402
                                 UC_ARM64_REG_X2, UC_ARM64_REG_X3,
                                 UC_ARM64_REG_SP, UC_ARM64_REG_LR,
                                 UC_ARM64_REG_CPACR_EL1)

CODE, ST, CURB, PT, HB, STK, CTX = (0x10000, 0x100000, 0x200000, 0x300000,
                                    0x1000000, 0x400000, 0x500000)
CUR = CURB + 0x100                     # ctr arrays at CUR-0xE0 / CUR-0x50
ENGINE = 0x00A01000
MATCTX = 0x00B00F00                    # straddles nothing; spt at +0x40
ESP = 0x00C80FFC                       # the arg crosses a page: on purpose
RET0 = 0x5C1000

FREQ = 4
POS_JUMP = 1024      # BUILD 749
ROT_JUMP = 2048
F8_JUMP = 64


def lerp(p, n, step):
    num = (n - p) * step
    q = abs(num) // FREQ
    return p + (q if num >= 0 else -q)


def s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def s32(v):
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


class FFNx:
    """InterpolationEffectDecorator for one slot, as effect.cpp has it."""

    def __init__(self):
        self.fc = 0
        self.prev = {}
        self.vel = {}
        self.num_calls = 0
        self.sprites = 0          # 783: sprites the previous real call drew
        self.cur_sprites = 0      # 783: ... the latest real call

    def _lerp(self, kind, p, live, step, key):
        exp = {}
        # BUILD 749d: a palette mode change makes the whole draw live
        modecut = kind == 'mat' and (p['pal'][5] != live['pal'][5]
                                     or p['pal'][3] != live['pal'][3])
        # BUILD 749 guards (see fxinterp.c): a jump is drawn live
        rcut = any(abs(b - a) > ROT_JUMP for a, b in zip(p['m'], live['m']))
        pcut = rcut or modecut or any(abs(b - a) > POS_JUMP
                                      for a, b in zip(p['pos'], live['pos']))
        vel = self.vel.get(key)
        if not pcut:
            self.vel[key] = [b - a for a, b in zip(p['pos'], live['pos'])]
        for k, v in live.items():
            if k == 'm':
                exp[k] = list(v) if rcut else \
                    [s16(lerp(a, b, step)) for a, b in zip(p[k], v)]
            elif k == 'pos':
                if not pcut:
                    exp[k] = [s32(lerp(a, b, step)) for a, b in zip(p[k], v)]
                elif modecut and not rcut and vel:
                    exp[k] = [s32(lerp(b - d, b, step)) for b, d in zip(v, vel)]
                else:
                    exp[k] = list(v)
            elif k == 'pal':
                exp[k] = [s32(lerp(a, b, step))
                          if j < 3 and not modecut and abs(b - a) <= POS_JUMP else b
                          for j, (a, b) in enumerate(zip(p[k], v))]
            elif k == 'col':
                exp[k] = [lerp(a, b, step) & 0xFF for a, b in zip(p[k], v)]
            elif k == 'f8':
                r = 0
                for sh in ((0,) if modecut else (0, 8)):
                    a, b = (p[k] >> sh) & 0xFF, (v >> sh) & 0xFF
                    d = ((b - a + 128) & 0xFF) - 128
                    r |= (((a + lerp(0, d, step)) & 0xFF) if abs(d) <= F8_JUMP
                          else b) << sh
                if modecut:
                    r |= v & 0xFF00
                exp[k] = r
            elif k == 'tr':
                exp[k] = s16(lerp(p[k], v, step))
        return exp

    def call(self, draws, values_for):
        """draws: list of (kind, ret). values_for(i) -> dict of live values.
        Yields (i, expected dict or None if untouched) per draw."""
        real = self.fc % FREQ == 0
        step = self.fc % FREQ
        if real:
            self.prev = {}
        idx = 0
        out = []
        # BUILD 783: a slot whose one draw is a sprite is interpolated too
        # (FFNx's rule -- exactly one counted draw -- is kept); a slot's
        # first real call saves its sprite while nothing says it is not alone
        sn = self.sprites if real else self.cur_sprites
        if real:
            self.cur_sprites = sum(1 for k, _r in draws if k == 'spt')
        for i, (kind, ret) in enumerate(draws):
            live = values_for(i)
            if kind == 'spt':
                n = self.num_calls
                if n == 1:
                    pass
                elif n == 0 and (sn == 1 or (real and sn == 0)):
                    key = (ret, idx)              # keyed, not counted
                    if real:
                        self.prev[key] = live
                        out.append(dict(live))
                    elif key in self.prev:
                        out.append(self._lerp(kind, self.prev[key], live, step, key))
                    else:
                        out.append(dict(live))
                    continue
                else:
                    out.append(None)
                    continue
            key = (ret, idx)
            if real:
                self.prev[key] = live
                out.append(dict(live))
            elif key in self.prev:
                out.append(self._lerp(kind, self.prev[key], live, step, key))
            else:
                out.append(dict(live))
            idx += 1
        if real:
            self.num_calls = idx
            self.sprites = self.cur_sprites
        self.fc += 1
        return out


class Box:
    def __init__(self):
        self.uc = uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        uc.reg_write(UC_ARM64_REG_CPACR_EL1, 0x300000)
        for base, size in ((CODE, 0x10000), (ST, 0x10000), (CURB, 0x1000),
                           (PT, 0x10000), (HB, 0x1000000), (STK, 0x10000),
                           (CTX, 0x1000)):
            uc.mem_map(base, size)
        uc.mem_write(CODE, open(FX.BLOB, 'rb').read())
        uc.mem_write(CODE + 0x8000, struct.pack('<I', 0xD65F03C0))
        uc.mem_write(PT, b''.join(struct.pack('<Q', HB + (i << 12))
                                  for i in range(0x1000)))
        self.fn = {k: CODE + v for k, v in FX.entries().items()}
        self.g(FX_ENGINE_PTR, struct.pack('<I', ENGINE))
        self.uc.mem_write(CTX + 0x10, struct.pack('<I', ESP))

    def g(self, va, data):
        self.uc.mem_write(HB + va, bytes(data))

    def gr(self, va, n):
        return bytes(self.uc.mem_read(HB + va, n))

    def cur(self, slot_host, phase):
        self.uc.mem_write(CUR, struct.pack('<QBBBBI', slot_host, phase, 0, 0,
                                           0, 0))

    def clear(self):
        self.uc.mem_write(CUR, b'\0' * 16)

    def call(self, name):
        for r, v in zip((UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
                         UC_ARM64_REG_X3), (CUR, ST, PT, CTX)):
            self.uc.reg_write(r, v)
        self.uc.reg_write(UC_ARM64_REG_X4, getattr(self, 'ret', 0))
        self.uc.reg_write(UC_ARM64_REG_SP, STK + 0x8000)
        self.uc.reg_write(UC_ARM64_REG_LR, CODE + 0x8000)
        self.uc.emu_start(self.fn[name], CODE + 0x8000, count=200000)

    # ---- the live values a draw sees
    def put(self, kind, v):
        m = b''.join(struct.pack('<h', x) for x in v['m'])
        m += b''.join(struct.pack('<i', x) for x in v['pos'])
        self.g(ENGINE + 0x10, m)
        if kind == 'mat':
            self.g(MATCTX + 8, struct.pack('<Hh', v['f8'], v['tr']))
            self.g(ENGINE + 0x50, bytes(v['col']))
            for off, x in zip(PAL, v['pal']):
                self.g(0xC06A00 + off, struct.pack('<i', x))
        else:
            self.g(MATCTX + 0x40 + 8, bytes(v['col']))

    def got(self, kind):
        b = self.gr(ENGINE + 0x10, 30)
        out = {'m': list(struct.unpack('<9h', b[:18])),
               'pos': list(struct.unpack('<3i', b[18:30]))}
        if kind == 'mat':
            out['f8'], out['tr'] = struct.unpack('<Hh', self.gr(MATCTX + 8, 4))
            out['col'] = list(self.gr(ENGINE + 0x50, 4))
            out['pal'] = [struct.unpack('<i', self.gr(0xC06A00 + o, 4))[0]
                          for o in PAL]
        else:
            out['col'] = list(self.gr(MATCTX + 0x40 + 8, 4))
        return out

    def draw(self, kind, ret, v):
        # BUILD 749d: the caller's identity arrives in w4 (the ARM call site);
        # the guest return slot holds stale stack -- make that obvious here.
        self.ret = ret
        self.g(ESP, struct.pack('<I', 0xDEAD0000 | (ret & 0xFFF)))
        self.g(ESP + 4, struct.pack('<I', MATCTX if kind == 'mat'
                                    else MATCTX + 0x40))
        self.put(kind, v)
        self.call('fx_material' if kind == 'mat' else 'fx_spt')
        return self.got(kind)


FX_ENGINE_PTR = 0x90AAF0
PAL = (0x00, 0x0C, 0x18, 0x24, 0x08, 0x10, 0x14)


def state_values(rng, kind, k, i):
    """Deterministic 'game state k' for draw i: smooth-ish ramps plus noise
    so negative deltas and wraps both occur."""
    r = random.Random(hash((kind, i)) & 0xFFFFFFFF)
    base = [r.randint(-3000, 3000) for _ in range(9)]
    vel = [r.randint(-400, 400) if i % 3 else r.randint(-2600, 2600) for i in range(9)]
    pb = [r.randint(-90000, 90000) for _ in range(3)]
    pv = [r.choice((r.randint(-300, 300), r.randint(-5000, 5000))) for _ in range(3)]
    v = {'m': [s16(b + d * k) for b, d in zip(base, vel)],
         'pos': [p + d * k for p, d in zip(pb, pv)],
         'col': [(r.randint(0, 255) + 37 * k * (j + 1)) & 0xFF for j in range(4)]}
    if kind == 'mat':
        v['f8'] = (r.randint(0, 0xFFFF) + (r.choice((3, 40, 90)) * k) + (r.choice((1, 5, 100)) * k << 8)) & 0xFFFF
        v['tr'] = s16(r.randint(-200, 200) - 9 * k)
        v['pal'] = [r.randint(-10 ** 6, 10 ** 6) + r.choice((77, 700)) * k * (j - 3) for j in range(7)]
        # field_24 / scroll_v are modes: steady, and on some draws they flip
        flip = r.random() < 0.3
        v['pal'][3] = 2
        v['pal'][5] = 1 + ((k // 3) & 1 if flip else 0)
    return v


def run(draw_lists, ticks=6, slot=('e100', 7), seed=1):
    box = Box()
    ref = FFNx()
    base = CUR - 0xE0 if slot[0] == 'e100' else CUR - 0x50
    host_slot = base + slot[1]
    rng = random.Random(seed)
    moved = 0
    for f in range(ticks * FREQ):
        draws = draw_lists[f % len(draw_lists)]
        real = f % FREQ == 0
        k = f // FREQ + (0 if real else 1)        # draw before advancing
        exp = ref.call(draws, lambda i: state_values(rng, draws[i][0], k, i))
        box.cur(host_slot, (f % FREQ + 1) % FREQ)
        for i, (kind, ret) in enumerate(draws):
            got = box.draw(kind, ret, state_values(rng, kind, k, i))
            want = exp[i] if exp[i] is not None else state_values(rng, kind, k, i)
            assert got == want, ('frame %d draw %d (%s): %r != %r'
                                 % (f, i, kind, got, want))
            moved += got != state_values(rng, kind, k, i)
        box.clear()
    # a hook that did nothing would match FFNx only where FFNx does nothing
    assert moved > 0 or not any(d[0] == 'mat' for dl in draw_lists for d in dl), \
        'nothing was interpolated'
    box.moved = moved
    return box


def test_blob_is_current():
    import hashlib
    src = os.path.join(os.path.dirname(FX.BLOB), 'fxinterp.c')
    want = open(os.path.join(os.path.dirname(FX.BLOB),
                             'fxinterp.src.sha256')).read().strip()
    assert hashlib.sha256(open(src, 'rb').read()).hexdigest() == want


def test_one_material_draw_matches_ffnx():
    run([[('mat', RET0)]])


def test_many_material_draws_from_one_site_and_several():
    run([[('mat', RET0)] * 5 + [('mat', RET0 + 0x40), ('mat', RET0 + 0x80)]])


def test_a_lone_sprite_is_interpolated_783():
    # FFNx counts a sprite draw only when the slot's previous real call made
    # exactly one counted draw, so a sprite-only effect never moved between
    # ticks there. BUILD 783 interpolates a slot whose ONE draw is a sprite
    # (Phoenix's drop); run() checks every frame against the updated model.
    assert run([[('spt', RET0 + 0x100)]], ticks=8).moved > 0


def test_two_lone_sprites_still_follow_ffnx():
    # a sprite-only slot with two sprites is not "one draw": untouched
    assert run([[('spt', RET0 + 0x100), ('spt', RET0 + 0x104)]],
               ticks=8).moved == 0


def test_sprite_counting_alternates_with_material_draws_as_in_ffnx():
    run([[('mat', RET0)], [('spt', RET0 + 0x100)], [('mat', RET0)],
         [('spt', RET0 + 0x100), ('mat', RET0 + 8)]], ticks=12)


def test_sprites_are_left_alone_when_the_call_drew_more_than_one():
    run([[('spt', RET0 + 0x100), ('spt', RET0 + 0x100)]], ticks=8)


def test_effect60_slots_too():
    run([[('mat', RET0), ('spt', RET0 + 4)]], slot=('e60', 59))


def test_the_draw_list_changing_between_ticks():
    run([[('mat', RET0)], [('mat', RET0), ('mat', RET0 + 8)], [('spt', 9)],
         [('mat', RET0 + 8)]], ticks=10)


def test_a_real_call_that_draws_nothing_still_clears_the_map():
    # FFNx clears the slot's map on EVERY real call; one that drew nothing
    # leaves nothing to interpolate from on the paused calls after it.
    run([[('mat', RET0)], [('mat', RET0)], [('mat', RET0)], [('mat', RET0)],
         [], [('mat', RET0)], [('mat', RET0)], [('mat', RET0)]], ticks=8)


def test_the_state_size_matches_the_c_struct():
    # 2*gen + numc + pend + lastp per slot, then the table
    assert FX.STATE_BYTES == 160 * 7 + 256 * 132 + 12 * 72


def test_outside_a_decorated_call_nothing_moves():
    box = Box()
    v = state_values(None, 'mat', 3, 0)
    for _ in range(4):
        box.clear()
        assert box.draw('mat', RET0, v) == v
        assert box.draw('spt', RET0, state_values(None, 'spt', 3, 0)) == \
            state_values(None, 'spt', 3, 0)


def test_a_slot_pointer_outside_both_lists_is_ignored():
    box = Box()
    v = state_values(None, 'mat', 1, 0)
    for f in range(8):
        box.cur(CUR + 0x40, (f % 4 + 1) % 4)
        assert box.draw('mat', RET0, state_values(None, 'mat', f // 4 + 1, 0)) \
            == state_values(None, 'mat', f // 4 + 1, 0)


def test_a_full_table_draws_stock_instead_of_overwriting():
    # 40 distinct draws from one site in one slot: more than one probe run
    # can hold, so some are drawn stock, but every one that IS interpolated
    # must still match FFNx exactly (checked inside run()). Run it with a
    # tolerant reference: a draw is either FFNx's value or the live value.
    box = Box()
    ref = FFNx()
    draws = [('mat', RET0 + 4 * i) for i in range(300)]
    for f in range(8):
        real = f % FREQ == 0
        k = f // FREQ + (0 if real else 1)
        exp = ref.call(draws, lambda i: state_values(None, 'mat', k, i))
        box.cur(CUR - 0xE0 + 3, (f % FREQ + 1) % FREQ)
        for i, d in enumerate(draws):
            live = state_values(None, 'mat', k, i)
            got = box.draw(d[0], d[1], live)
            assert got in (exp[i], live), (f, i)
        box.clear()


def test_two_slots_keep_their_own_maps():
    box = Box()
    refs = [FFNx(), FFNx()]
    slots = [CUR - 0xE0 + 1, CUR - 0x50 + 2]
    for f in range(16):
        for si in range(2):
            real = f % FREQ == 0
            k = f // FREQ + (0 if real else 1)
            draws = [('mat', RET0)]                # SAME call site in both
            exp = refs[si].call(draws, lambda i: state_values(None, 'mat', k + 100 * si, i))
            box.cur(slots[si], (f % FREQ + 1) % FREQ)
            got = box.draw('mat', RET0, state_values(None, 'mat', k + 100 * si, 0))
            assert got == exp[0], (f, si)
            box.clear()


# ------------------------------------------------- the throttle caves' side

def _caves(tag):
    import ff7nx_dispatch as D
    import ff7nx_dispatch_sites as S
    import ff7nx_60fps as F
    thr = dict(S.DISPATCH_SITES[tag]['throttle'])
    if tag == 'effect60':
        thr['allow'] = True
    thr['table'] = []
    for k in ('kotr', 'model', 'camera', 'one_call'):
        thr.pop(k, None)
    F._inject_effect_interpolation(tag, thr, log=lambda *_: None)
    dh = S.DISPATCH_SITES[tag]['disp_hook']
    pre_site = {'hook': thr['pre'], 'displaced': thr['pre_displaced'],
                'ctx_reg': thr['ctx_reg'], 'store_reg': thr['store_reg'],
                'idx_off': dh['idx_off']}
    post_site = dict(pre_site, hook=thr['post'],
                     displaced=thr['post_displaced'])
    pre = D.build_throttle_pre_cave(0x900000, pre_site, thr, S.PAUSED_GUEST,
                                    thr['mask_bits'])
    post = D.build_throttle_post_cave(0x901000, post_site, thr)
    return thr, pre_site, post_site, pre, post, S


@pytest.mark.parametrize('tag', ['effect100', 'effect60'])
def test_the_caves_publish_the_running_generic_slot_and_clear_it(tag):
    import arm64emu as E
    thr, pre_site, post_site, pre, post, S = _caves(tag)
    cur = thr['interp_cur']
    mem = E.Mem()
    idx = 5
    mem.setu(thr['ctr'] + idx, 0, 1)                  # a generic slot
    seen = []
    for f in range(8):
        mem.setu(cur + 10, 0x7777, 2)                 # stale idx/seen
        cpu = E.Cpu(mem, host_base=0x40000000)
        cpu.sp = 0x800000000
        cpu.x[0] = 0x45E3CE
        cpu.x[pre_site['ctx_reg']] = 0x30000000
        cpu.x[pre_site['store_reg']] = 0x1234
        mem.setu(0x30000000 + pre_site['idx_off'], idx, 4)
        assert cpu.run(0x900000, pre) == pre_site['hook'] + 4
        assert cpu.x[0] & 0xFFFFFFFF == 0x45E3CE
        assert mem.u(cur, 8) == thr['ctr'] + idx
        seen.append(mem.u(cur + 8, 1))
        assert mem.u(cur + 10, 2) == 0
        cpu2 = E.Cpu(mem, host_base=0x40000000)
        cpu2.sp = 0x800000000
        assert cpu2.run(0x901000, post) == post_site['hook'] + 4
        assert mem.u(cur, 8) == 0, 'the record outlived the call'
    assert seen == [1, 2, 3, 0, 1, 2, 3, 0]


@pytest.mark.parametrize('tag', ['effect100', 'effect60'])
def test_an_excluded_slot_publishes_nothing(tag):
    import arm64emu as E
    thr, pre_site, post_site, pre, post, S = _caves(tag)
    cur = thr['interp_cur']
    mem = E.Mem()
    mem.setu(thr['ctr'] + 3, 0x80, 1)
    cpu = E.Cpu(mem, host_base=0x40000000)
    cpu.sp = 0x800000000
    cpu.x[pre_site['ctx_reg']] = 0x30000000
    mem.setu(0x30000000 + pre_site['idx_off'], 3, 4)
    cpu.run(0x900000, pre)
    assert mem.u(cur, 8) == 0


def test_kotr_curtain_conveyor_wrap_keeps_flowing():
    """BUILD 749g: x86 0x481096's stack position wraps back 2462 and the
    copies' scroll_v / v_offset (a mode and a float) flip on the same tick.
    The frames after that tick show the new (live) picture arriving at the
    speed the stack was moving -- never a slide across the jump, and never
    a held frame."""
    box = Box()
    host = CUR - 0xE0 + 5
    m = [4096, 0, 0, 0, 4096, 0, 0, 0, 4096]
    seen_wrap = seen_flow = 0
    for f in range(40):
        real = f % FREQ == 0
        step = f % FREQ
        k = f // FREQ + (0 if real else 1)
        rem = (k * 600) % 2462
        par = ((k * 600) // 2462) & 1
        v = {'m': m, 'pos': [0, 0, rem - 2462], 'col': [128, 64, 32, 255],
             'f8': (0x200 if par else 0) | ((k * 3) & 0xFF), 'tr': 64,
             'pal': [0, 0, 0, 2, 0x3F7FBE77, 2 if par else 1,
                     0x3F7B0000 if par else 0]}
        box.cur(host, (f % FREQ + 1) % FREQ)
        got = box.draw('mat', RET0, v)
        box.clear()
        if real:
            prev = v
            continue
        assert got['pal'][3:] == v['pal'][3:]          # modes/floats live
        assert got['f8'] >> 8 == v['f8'] >> 8           # parity byte live
        if abs(v['pos'][2] - prev['pos'][2]) > POS_JUMP:
            # new picture, approached at 600/tick: live - 600 + 600*step/4
            assert got['pos'][2] == v['pos'][2] - 600 + 150 * step
            seen_wrap += 1
        else:
            assert got['pos'][2] == prev['pos'][2] + 150 * step
            seen_flow += 1
    assert seen_wrap and seen_flow

def test_a_palette_mode_change_keeps_the_other_fields_flowing():
    """BUILD 749g: on a mode flip only the mode-bound values (palette modes,
    field_8's v byte) are drawn live; u scroll, transparency and colour
    keep interpolating, and the position arrives at its last speed."""
    box = Box()
    host = CUR - 0xE0 + 9
    for f in range(16):
        real = f % FREQ == 0
        step = f % FREQ
        k = f // FREQ + (0 if real else 1)
        v = {'m': [4096, 0, 0, 0, 4096, 0, 0, 0, 4096],
             'pos': [0, 0, 50 * k], 'col': [8 * k & 255, 1, 2, 255],
             'f8': (4 * k) & 0xFF, 'tr': 4 * k,
             'pal': [0, 0, 0, 2, 0, 1 + (k >= 2), 0]}
        box.cur(host, (f % FREQ + 1) % FREQ)
        got = box.draw('mat', 0x23CE28, v)
        box.clear()
        if real or k != 2:
            continue
        assert got['pal'] == v['pal']
        assert got['pos'][2] == 50 * k - 50 + (50 * step) // 4
        assert got['f8'] == (4 * (k - 1) + step) & 0xFF
        assert got['tr'] == 4 * (k - 1) + step
        assert got['col'][0] == 8 * (k - 1) + 2 * step

def test_the_stub_passes_the_arm_call_site():
    """w4 = x30 as a module offset, built from adr at run time."""
    import a64 as A
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    w = FX.stub(0x32F00, 0x3FEC000, 0x32720, 0x7853F0, 0xFC1C0FE8)
    code = b''.join(struct.pack('<I', x) for x in w)
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    base = 0x7000000
    uc.mem_map(base, 0x2000000)
    uc.mem_write(base + 0x32F00, code)
    uc.mem_write(base + 0x32720, struct.pack('<I', 0xD65F03C0))   # fn: ret
    uc.mem_map(0x10000000, 0x10000)
    uc.reg_write(UC_ARM64_REG_SP, 0x10008000)
    uc.reg_write(UC_ARM64_REG_LR, base + 0x23CE28)
    # stop at the bl into fn: run the prologue + w4 computation only
    stop = base + 0x32F00 + 4 * next(i for i, x in enumerate(w)
                                     if (x & 0xFC000000) == 0x94000000)
    uc.mem_map(0x20000000, 0x1000)
    uc.emu_start(base + 0x32F00, stop)
    assert uc.reg_read(UC_ARM64_REG_X4) & 0xFFFFFFFF == 0x23CE28


def test_two_draws_sharing_one_matrix_do_not_compound():
    """BUILD 749c: the game builds one matrix and draws twice (KOTR's two
    curtain copies). The second draw must come out exactly where the first
    did, not interpolated again from the first draw's output."""
    box = Box()
    host = CUR - 0xE0 + 11
    rets = (0x5C2000, 0x5C2000)
    for f in range(16):
        real = f % FREQ == 0
        k = f // FREQ + (0 if real else 1)
        v = {'m': [4096, 0, 0, 0, 4096, 0, 0, 0, 4096],
             'pos': [100 * k, -50 * k, 300 * k], 'col': [10 * k & 255, 0, 0, 255],
             'f8': (3 * k) & 0xFF, 'tr': 8 * k, 'pal': [7 * k, 0, 0, 0, 0, 0, 0]}
        box.cur(host, (f % FREQ + 1) % FREQ)
        first = box.draw('mat', rets[0], v)
        # second copy: the game does NOT rebuild anything in between
        box.ret = rets[1]
        box.call('fx_material')
        second = box.got('mat')
        box.clear()
        assert second == first, (f, first, second)


def test_a_byte_the_game_rewrites_between_copies_does_not_compound_the_other():
    """BUILD 749f: KOTR's curtain xors field_8's v byte (0x200) between its
    two copies; the u byte must come out identical for both copies."""
    box = Box()
    host = CUR - 0xE0 + 13
    for f in range(16):
        real = f % FREQ == 0
        k = f // FREQ + (0 if real else 1)
        v = {'m': [4096, 0, 0, 0, 4096, 0, 0, 0, 4096],
             'pos': [0, 0, 40 * k], 'col': [0, 0, 0, 255],
             'f8': ((7 * k) & 0xFF) | 0x200, 'tr': 64,
             'pal': [0, 0, 0, 2, 0, 1, 0]}
        box.cur(host, (f % FREQ + 1) % FREQ)
        first = box.draw('mat', 0x23CE28, v)
        # the game flips the v byte in the material, nothing else
        f8, tr = struct.unpack('<Hh', box.gr(MATCTX + 8, 4))
        box.g(MATCTX + 8, struct.pack('<Hh', f8 ^ 0x200, tr))
        box.ret = 0x23CE28
        box.call('fx_material')
        second = box.got('mat')
        box.clear()
        assert second['f8'] & 0xFF == first['f8'] & 0xFF, (f, first, second)
        assert second['pos'] == first['pos']


# ------------------------------------------------------------------ BUILD 782
# The camera is interpolated on every frame; an effect's matrix has the view
# multiplied in. A paused draw must show the effect's OWN motion lerped and
# the camera exact -- not 1/16, 4/16, 9/16 of the camera's motion and a jump.
import math                                                       # noqa: E402

VIEW = 0xBFCB30


def _rot(ax, ay, az, scale=4096):
    cx, sx, cy, sy, cz, sz = (math.cos(ax), math.sin(ax), math.cos(ay),
                              math.sin(ay), math.cos(az), math.sin(az))
    rx = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    ry = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    rz = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    m = [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
         for a, b in ((rz, ry),) for i in range(3)]
    m = [[sum(m[i][k] * rx[k][j] for k in range(3)) for j in range(3)]
         for i in range(3)]
    return [m[i][j] * scale for i in range(3) for j in range(3)]


def _compose(a, b, conv):
    """a (x) b in the engine's struct algebra: conv 'row' (D3D row vectors,
    t = ta.Rb + tb) or 'col' (t = Ra.tb + ta). (rot floats 4.12, t floats)"""
    ra, ta = a
    rb, tb = b
    r = [sum(ra[3 * i + k] * rb[3 * k + j] for k in range(3)) / 4096
         for i in range(3) for j in range(3)]
    if conv == 'row':
        t = [sum(ta[i] * rb[3 * i + j] for i in range(3)) / 4096 + tb[j]
             for j in range(3)]
    else:
        t = [sum(ra[3 * j + i] * tb[i] for i in range(3)) / 4096 + ta[j]
             for j in range(3)]
    return r, t


def _q(m):
    r, t = m
    return {'m': [s16(int(round(x))) for x in r],
            'pos': [int(round(x)) for x in t]}


def _view(f):
    """The interpolated camera: a smooth orbit, a new view every frame."""
    return (_rot(0.3 + 0.021 * f, -0.4 + 0.013 * f, 0.05),
            [1500 + 37 * f, -800 + 11 * f, 9000 - 23 * f])


def _local(k):
    """The effect's own state at tick k: a slow spin and drift."""
    return (_rot(0.01 * k, 0.2 + 0.015 * k, -0.1), [200 + 30 * k, 50 - 12 * k, 400])


def _put_view(box, v):
    q = _q(v)
    box.g(VIEW, b''.join(struct.pack('<h', x) for x in q['m'])
          + b''.join(struct.pack('<i', x) for x in q['pos']))


def _camera_run(conv, side='left', ticks=5):
    box = Box()
    # the engine's composition flag (+0x48): 0 = D3D row vectors (6611fb)
    box.g(ENGINE + 0x48, struct.pack('<I', 0 if conv == 'row' else 1))
    host_slot = CUR - 0xE0 + 9
    worst = 0
    for f in range(ticks * FREQ):
        real = f % FREQ == 0
        k = f // FREQ + (0 if real else 1)
        step = f % FREQ
        v = _view(f)
        _put_view(box, v)

        def draw_of(view, loc):
            return (_compose(view, loc, conv) if side == 'left'
                    else _compose(loc, view, conv))
        live = _q(draw_of(v, _local(k)))
        box.cur(host_slot, (f % FREQ + 1) % FREQ)
        live = dict(live, f8=0, tr=0, col=[1, 2, 3, 4], pal=[0, 0, 0, 2, 0, 1, 0])
        got = box.draw('mat', RET0 + 0x40, live)
        box.clear()
        if real:
            assert got['m'] == live['m'] and got['pos'] == live['pos']
            continue
        # what a smooth picture is: this frame's camera, the state lerped
        p, n = _local(k - 1), _local(k)
        lr = [a + (b - a) * step / FREQ for a, b in zip(p[0], n[0])]
        lt = [a + (b - a) * step / FREQ for a, b in zip(p[1], n[1])]
        want = _q(draw_of(v, (lr, lt)))
        err = max(max(abs(a - b) for a, b in zip(got['m'], want['m'])),
                  max(abs(a - b) for a, b in zip(got['pos'], want['pos'])))
        worst = max(worst, err)
    return worst


@pytest.mark.parametrize('conv', ('row', 'col'))
def test_782_the_camera_is_exact_on_paused_frames(conv):
    # the view is always the left operand (all 525 661e85 calls that take
    # 0xBFCB30 take it first)
    assert _camera_run(conv) <= 6


def test_782_without_the_fix_the_camera_lags(monkeypatch):
    # the same scene with the saved view hidden (as 749 had it): the paused
    # frames are far off -- this is the judder the fix removes
    box_cls = Box

    class NoView(box_cls):
        def g(self, va, data):
            if va == VIEW:
                data = b'\0' * len(data)
            box_cls.g(self, va, data)
    monkeypatch.setitem(globals(), 'Box', NoView)
    assert _camera_run('col', 'left') > 60


def test_782_a_draw_without_the_view_is_exactly_749():
    # screen-space draws: the camera moves, the matrix does not carry it --
    # a sprite spinning and drifting on the screen is lerped exactly as 749
    box = Box()
    ref = FFNx()
    host_slot = CUR - 0xE0 + 11

    def scr(i, k):
        # in the screen plane: spin about z, drift across -- uncorrelated
        # with the camera's orbit about x and y
        r = _rot(0, 0, -0.05 * k - 0.4 * i)
        t = [120 * k - 300 * i, -60 * k + 90, 300]
        return dict(_q((r, t)), f8=0x1020 + k,
                    tr=5, col=[9, 9, 9, 9], pal=[0, 0, 0, 2, 0, 1, 0])
    for f in range(6 * FREQ):
        _put_view(box, _view(f))
        real = f % FREQ == 0
        k = f // FREQ + (0 if real else 1)
        draws = [('mat', RET0 + 0x80), ('mat', RET0 + 0x84)]
        exp = ref.call(draws, lambda i: scr(i, k))
        box.cur(host_slot, (f % FREQ + 1) % FREQ)
        for i, (kind, ret) in enumerate(draws):
            got = box.draw(kind, ret, scr(i, k))
            assert got == exp[i], (f, i)
        box.clear()


def test_782_a_singular_view_changes_nothing():
    box = Box()
    host_slot = CUR - 0xE0 + 12
    ref = FFNx()
    rng = random.Random(5)
    for f in range(3 * FREQ):
        box.g(VIEW, struct.pack('<9h3i', *([f, 0, 0, 0, 0, 0, 0, 0, 0] + [f, 0, 0])))
        real = f % FREQ == 0
        k = f // FREQ + (0 if real else 1)
        draws = [('mat', RET0 + 0xA0)]
        exp = ref.call(draws, lambda i: state_values(rng, 'mat', k, i))
        box.cur(host_slot, (f % FREQ + 1) % FREQ)
        got = box.draw('mat', RET0 + 0xA0, state_values(rng, 'mat', k, 0))
        assert got == exp[0], f
        box.clear()


# ------------------------------------------------------------------ BUILD 783
# Phoenix's drop (x86 0x516ACF -> 0x516C64): a billboard. Its matrix is a
# fixed 0x400 scale; only its translation carries the view -- the drop's
# point moved into camera space. Falls 156 units a tick (+0xE = 0x9C).
def _billboard_run(conv, ticks=5):
    box = Box()
    box.g(ENGINE + 0x48, struct.pack('<I', 0 if conv == 'row' else 1))
    host_slot = CUR - 0xE0 + 13
    scale = [0x400, 0, 0, 0, 0x400, 0, 0, 0, 0x400]

    def point(view, p):
        r, t = view
        if conv == 'row':
            return [sum(p[i] * r[3 * i + j] for i in range(3)) / 4096 + t[j]
                    for j in range(3)]
        return [sum(r[3 * j + i] * p[i] for i in range(3)) / 4096 + t[j]
                for j in range(3)]

    def drop(k):
        return [0.0, -3125.0 + 156.0 * k, 0.0]
    worst = 0
    for f in range(ticks * FREQ):
        real = f % FREQ == 0
        k = f // FREQ + (0 if real else 1)
        step = f % FREQ
        v = _view(f)
        _put_view(box, v)
        live = {'m': list(scale), 'pos': [int(round(x)) for x in point(v, drop(k))],
                'col': [9, 9, 9, 9]}
        box.cur(host_slot, (f % FREQ + 1) % FREQ)
        got = box.draw('spt', RET0 + 0x60, live)
        box.clear()
        if real:
            continue
        pk = [a + (b - a) * step / FREQ for a, b in zip(drop(k - 1), drop(k))]
        want = [int(round(x)) for x in point(v, pk)]
        assert got['m'] == scale
        worst = max(worst, max(abs(a - b) for a, b in zip(got['pos'], want)))
    return worst


@pytest.mark.parametrize('conv', ('row', 'col'))
def test_783_a_billboard_follows_the_camera_and_falls_smoothly(conv):
    assert _billboard_run(conv) <= 4


# ------------------------------------------------------------------ BUILD 811
def _glow_run():
    """KOTR's finale glow (x86 0x481E7C's second draw): a local matrix --
    a uniform scale that grows each tick, translation (0, 0, 512), no view in
    it -- whose entry was once judged carrying-and-moving (783's sticky
    verdict). Ticks 0..2 draw it WITH the view composed so the verdict
    sticks; from tick 3 it is the local matrix while the camera keeps
    orbiting. Returns the worst paused-frame distance from the plain lerp of
    its own state (translation fixed, scale lerped)."""
    box = Box()
    box.g(ENGINE + 0x48, struct.pack('<I', 1))
    host_slot = CUR - 0xE0 + 41

    def glow(k):
        sc = 0x80 + 3 * k
        return [sc, 0, 0, 0, sc, 0, 0, 0, sc], [0, 0, 512]
    worst = 0
    for f in range(8 * FREQ):
        real = f % FREQ == 0
        k = f // FREQ + (0 if real else 1)
        step = f % FREQ
        v = _view(f)
        _put_view(box, v)
        if k < 3:
            live = _q(_compose(v, _local(k), 'col'))
        else:
            m, t = glow(k)
            live = {'m': m, 'pos': t}
        live = dict(live, f8=0, tr=0, col=[1, 2, 3, 4], pal=[0, 0, 0, 2, 0, 1, 0])
        box.cur(host_slot, (f % FREQ + 1) % FREQ)
        got = box.draw('mat', RET0 + 0xB68, live)
        box.clear()
        if real or k < 4:
            continue
        pm, _pt = glow(k - 1)
        want_m = [lerp(a, b, step) for a, b in zip(pm, live['m'])]
        err = max(max(abs(a - b) for a, b in zip(got['m'], want_m)),
                  max(abs(a - b) for a, b in zip(got['pos'], live['pos'])))
        worst = max(worst, err)
    return worst


def test_811_a_local_matrix_with_a_fixed_point_is_not_carried_through_the_camera():
    assert _glow_run() <= 1
