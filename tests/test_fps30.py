#!/usr/bin/env python3
"""tests/test_fps30.py -- the Full 30 FPS rate.

Full 30 FPS is FFNx's 30 FPS mode: battle_frame_multiplier 2, field and
world stock. Three things change shape at that rate and are checked here:

  * the x2 constant tables in ff7nx_30fps sit at exactly the offsets of the
    x4 ones, keep their stock words, and land half way between stock and x4;
  * the summon camera decorator, run in the emulator with two sub-steps per
    battle tick, shows what FFNx's CameraInterpolationEffectDecorator shows
    with n = 2 -- the same differential test_camerainterp runs at 4;
  * fxinterp30.bin, the effect interpolation built with FREQ 2, draws what
    FFNx's InterpolationEffectDecorator draws at 2 -- the differential
    test_fxinterp runs at 4.

The 60 FPS output is unchanged by all of this; tests/test_camerainterp and
tests/test_fxinterp still cover it at 4.

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
import functools
import importlib.util
import os
import types

import pytest

import ff7nx_30fps as R
import ff7nx_60fps as F

HERE = os.path.dirname(os.path.abspath(__file__))


def _copy_of(name):
    """A private copy of another test module, so its constants can be set
    to the 30 FPS values without touching the module the 60 FPS run uses."""
    spec = importlib.util.spec_from_file_location(
        name + '_fps30', os.path.join(HERE, name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------ the tables

def _tables(widescreen=False):
    want = set(F.RECOMMENDED_ENABLE) | {
        'r-battle_aura', 'r-battle_camera', 'r-battle_damage', 'r-battle_misc',
        'r-battleground', 'r-enemy_death', 'r-summons', 'r-field_models',
        'r-field_text', 'r-world', 'r-swirl', 'swirl', 'fps-menu',
        'script-wait', 'analog-360', 'no-autorun', 'movie-fps', 'movie-poll'}
    return want, R.rate_tables('30', want, F.EXE_GATED, F.NSO_GATED,
                               widescreen=widescreen, log=lambda *_: None)


def test_no_field_or_world_group_survives_at_30():
    _want, (want, _ec, _nc, exe, nso) = _tables()
    assert not want & R.FIELD_60_ONLY
    field = {off for g in R.FIELD_60_ONLY for _l, off, _o, _n
             in F.NSO_GATED.get(g, ())}
    assert not field & {off for _l, off, _o, _n in nso}
    # the field and world divisors and the 60 FPS mod flag stay stock
    assert {va for _l, va, _o, _n in _ec} == {0x7C0B00}


def test_every_x2_word_keeps_the_stock_word_of_its_x4_entry():
    _want, (_w, _ec, _nc, _exe, nso) = _tables()
    stock = {}
    for g, ps in F.NSO_GATED.items():
        for _l, off, old, _n in ps:
            stock.setdefault(off, old)
    for label, off, old, new in nso:
        if (label, off, old, new) in R.MODEL_STEP:
            continue
        assert stock[off] == old, label
        assert new != old or label, label


def _imm(word):
    """The value an instruction scales: movz/cmp/add/sub immediate, or the
    shift amount of lsl/asr/shifted-register forms, as (kind, value)."""
    if word & 0x7F800000 == 0x52800000:                 # movz w
        return 'mov', (word >> 5) & 0xFFFF
    if word & 0x7F000000 in (0x11000000, 0x51000000, 0x71000000):
        return 'imm', (word >> 10) & 0xFFF
    return None, None


def test_x2_values_sit_half_way_between_stock_and_x4():
    """Every plain immediate the 30 FPS set writes is stock*2 or stock/2,
    and its 60 FPS counterpart is the same direction twice over."""
    _want, (_w, _ec, _nc, _exe, nso) = _tables()
    x4 = {}
    for g, ps in F.NSO_GATED.items():
        for _l, off, _o, new in ps:
            x4.setdefault(off, new)
    checked = 0
    for label, off, old, new in nso:
        k0, v0 = _imm(old)
        k2, v2 = _imm(new)
        k4, v4 = _imm(x4.get(off, 0))
        # v2 == v4: an FFNx literal (Shiva, Bahamut, Odin), the same at both
        if None in (k0, k2, k4) or v0 == v4 or not v0 or v2 == v4:
            continue
        if v4 > v0:      # multiplied: 30 is x2, 60 is x4 (FFNx rounds none)
            assert (v2, v4) == (v0 * 2, v0 * 4) or abs(v2 * 2 - v4) <= 2, label
        else:            # divided
            assert v2 * 2 >= v4 and v2 <= v0, label
        checked += 1
    assert checked >= 40


def test_the_widescreen_only_constant_follows_ffnx():
    _w, (_a, _b, _c, _d, off) = _tables(widescreen=False)
    _w, (_a, _b, _c, _d, on) = _tables(widescreen=True)
    site = 0x007CBA94
    assert site not in {p[1] for p in off}
    assert site in {p[1] for p in on}


def test_the_battle_menu_closing_box_is_60_only():
    _w, (_a, _b, _c, _d, nso) = _tables()
    assert not {0x00D6B7AC, 0x00D6B884} & {p[1] for p in nso}


def test_the_model_frame_step_is_added_only_at_30():
    _w, (want, _b, _c, _d, nso) = _tables()
    assert R.MODEL_STEP_GROUP in want
    assert all(p in nso for p in R.MODEL_STEP)
    assert R.MODEL_STEP_GROUP not in F.NSO_GATED


def test_a_group_without_30_values_aborts():
    with pytest.raises(SystemExit):
        R.rate_tables('30', {'p-battle_aura'}, F.EXE_GATED, F.NSO_GATED,
                      log=lambda *_: None)


def test_stock_rate_keeps_only_the_input_tweaks():
    want = set(F.RECOMMENDED_ENABLE) | {'analog-360', 'no-autorun',
                                        'movie-fps', 'movie-poll'}
    w, ec, nc, exe, nso = R.rate_tables('stock', want, F.EXE_GATED,
                                        F.NSO_GATED, log=lambda *_: None)
    assert w == {'analog-360', 'no-autorun', 'movie-fps'}
    assert not ec and not exe


def test_damage_table_is_ffnx_30():
    assert list(R.DAMAGE_TABLE_30) == [0, 1, 2, 3, 4, 5, 6, 6, 7, 7, 8, 8, 8,
                                       8, 7, 7, 6, 6, 5, 4, 3, 2]


# -------------------------------------------- the camera decorator at 2

@pytest.fixture(scope='module')
def cam30():
    ci = _copy_of('test_camerainterp')
    if ci.S is None:
        pytest.skip('needs the sites file')
    D = ci.D
    ci.FREQ = 2
    ci.D = types.SimpleNamespace(**{k: getattr(D, k) for k in dir(D)
                                    if not k.startswith('__')})
    ci.D.build_throttle_pre_cave = functools.partial(
        D.build_throttle_pre_cave, freq_bits=1)
    ci.D.build_throttle_post_cave = functools.partial(
        D.build_throttle_post_cave, freq_bits=1)
    return ci


@pytest.mark.parametrize('name', [
    'test_a_smooth_pan_matches_ffnx_every_frame',
    'test_a_leftward_pan_matches_too',
    'test_a_cut_past_the_threshold_snaps_instead_of_sliding',
    'test_a_cut_in_the_focal_point_alone_still_snaps_both',
    'test_the_elevated_threshold_class_interpolates_a_wider_jump',
    'test_the_endpoint_frame_lands_exactly_on_the_routines_value',
    'test_the_first_cycle_holds_still_instead_of_using_uninitialised_memory',
    'test_the_differential_passes_on_the_real_builder',
])
def test_camera_decorator_at_two_substeps(cam30, name):
    getattr(cam30, name)()


def test_camera_shows_half_way_then_the_endpoint(cam30):
    rig = cam30.Rig(interp=True)
    rig.frame((0, 0, 0), (0, 0, 0))            # first advance seeds
    rig.frame((0, 0, 0), (0, 0, 0))            # endpoint
    got, paused = rig.frame((200, -100, 0), (0, 0, 0))
    assert not paused and got[0] == (100, -50, 0)
    got, paused = rig.frame((200, -100, 0), (0, 0, 0))
    assert paused and got[0] == (200, -100, 0)


# ------------------------------------------- effect interpolation at 2

@pytest.fixture(scope='module')
def fx30():
    fx = _copy_of('test_fxinterp')
    FX = fx.FX
    fx.FREQ = 2
    fx.FX = types.SimpleNamespace(**{k: getattr(FX, k) for k in dir(FX)
                                     if not k.startswith('__')})
    fx.FX.BLOB = FX.BLOB30
    fx.FX.entries = lambda: FX.entries(FX.ENTRY30)
    return fx


@pytest.mark.parametrize('name', [
    'test_one_material_draw_matches_ffnx',
    'test_many_material_draws_from_one_site_and_several',
    'test_a_lone_sprite_is_interpolated_783',
    'test_two_lone_sprites_still_follow_ffnx',
    'test_sprites_are_left_alone_when_the_call_drew_more_than_one',
    'test_effect60_slots_too',
    'test_the_draw_list_changing_between_ticks',
    'test_a_real_call_that_draws_nothing_still_clears_the_map',
    'test_two_slots_keep_their_own_maps',
])
def test_effect_interpolation_at_two_substeps(fx30, name):
    getattr(fx30, name)()


def test_sprite_counting_alternates_with_material_draws_at_two(fx30):
    # test_fxinterp's pattern changes the draw list every frame, which at 2
    # sub-steps puts every sprite list on a paused frame; hold each list for
    # one tick (two frames) instead so the counting is actually exercised.
    R0 = fx30.RET0
    lists = [[('mat', R0)], [('spt', R0 + 0x100)], [('mat', R0)],
             [('spt', R0 + 0x100), ('mat', R0 + 8)]]
    fx30.run([dl for dl in lists for _ in range(2)], ticks=12)


def test_fxinterp30_is_the_same_source():
    """Both blobs come from fxinterp.c (native/build_fxinterp.sh); the 30
    one is not a stale copy of the 60 one."""
    FX = __import__('ff7nx_fxinterp')
    a, b = open(FX.BLOB, 'rb').read(), open(FX.BLOB30, 'rb').read()
    assert a != b
    assert set(FX.entries()) == set(FX.entries(FX.ENTRY30))


# ------------------------------------------------- the GUI's FPS Mode rule

def _fps_patch_mode():
    """_fps_patch_mode from 7th_heaven_nx.py, without importing the GUI."""
    import ast
    path = os.path.join(os.path.dirname(HERE), '7th_heaven_nx.py')
    tree = ast.parse(open(path, encoding='utf-8').read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == '_fps_patch_mode')
    ns = {'FPS_MOD_ID': 'fps', 'FPS_MODE_OPTION': 'FPSMode',
          '_manifest_id': lambda m: m.ident,
          '_option_value': lambda m, s, o: s.get(o)}
    exec(compile(ast.Module([fn], []), path, 'exec'), ns)
    return ns['_fps_patch_mode']


@pytest.mark.parametrize('on, mode, want', [
    (True, None, 60), (False, None, 0),          # no 60/30 mod enabled
    (True, 3, 60), (True, 2, 30), (True, 1, 0),
    (False, 3, 0), (False, 2, 0),
])
def test_the_mods_fps_mode_picks_the_rate(on, mode, want):
    mod = types.SimpleNamespace(ident='fps', filename='fps.iro',
                                display_name='60/30 FPS Gameplay')
    active = [mod] if mode else []
    settings = {'fps.iro': {'FPSMode': mode}} if mode else {}
    assert _fps_patch_mode()(on, active, settings, lambda *_: None) == want


# ------------------------------------------- summon model positions at 2

class _ModelRig:
    """One model-decorated effect100 slot, driven through the real pre and
    post caves in the emulator, at `freq_bits` (2 = 60 FPS, 1 = 30 FPS)."""

    PRE, POST, HOST, CTX, FN = 0x900000, 0x902000, 0x40000000, 0x30000000, \
        0x0050B1A3

    def __init__(self, freq_bits, idx=5):
        import arm64emu as E
        import ff7nx_dispatch as D
        import ff7nx_dispatch_sites as S
        self.E = E
        thr = dict(S.DISPATCH_SITES['effect100']['throttle'])
        thr['table'] = []
        F._inject_model_interpolation('effect100', thr, log=lambda *_: None)
        dh = S.DISPATCH_SITES['effect100']['disp_hook']
        self.pre_site = {'hook': thr['pre'], 'displaced': thr['pre_displaced'],
                         'ctx_reg': thr['ctx_reg'],
                         'store_reg': thr['store_reg'],
                         'idx_off': dh['idx_off']}
        post_site = dict(self.pre_site, hook=thr['post'],
                         displaced=thr['post_displaced'])
        self.post_hook = thr['post']
        thr['post'] = self.POST          # the one-call skip joins here
        self.pre = D.build_throttle_pre_cave(
            self.PRE, self.pre_site, thr, S.PAUSED_GUEST, thr['mask_bits'],
            freq_bits)
        self.post = D.build_throttle_post_cave(self.POST, post_site, thr,
                                               None, freq_bits)
        self.thr, self.idx = thr, idx
        self.mem = E.Mem()
        self.mem.setu(thr['ctr'] + idx, 0x20, 1)       # model, uninitialised
        probe = E.Cpu(self.mem, host_base=self.HOST)
        self.paused_h = probe.guest_to_host(S.PAUSED_GUEST)
        self.pos_h = probe.guest_to_host(thr['model_pos'])
        self.f0_h = probe.guest_to_host(thr['data_base'] + 0x20 * idx)
        self.mem.setu(self.paused_h, 0, 1)
        self.mem.setu(self.f0_h, 1, 2)                  # the effect is live

    def frame(self, live):
        E = self.E
        cpu = E.Cpu(self.mem, host_base=self.HOST)
        cpu.sp = 0x800000000
        cpu.x[0] = self.FN
        cpu.x[self.pre_site['ctx_reg']] = self.CTX
        cpu.x[self.pre_site['store_reg']] = 0x1234
        self.mem.setu(self.CTX + self.pre_site['idx_off'], self.idx, 4)
        assert cpu.run(self.PRE, self.pre) == self.pre_site['hook'] + 4
        assert cpu.x[0] & 0xFFFFFFFF == self.FN
        paused = self.mem.u(self.paused_h, 1)
        if not paused:                      # only an unpaused call moves it
            for i, v in enumerate(live):
                self.mem.setu(self.pos_h + 2 * i, v & 0xFFFF, 2)
        cpu2 = E.Cpu(self.mem, host_base=self.HOST)
        cpu2.sp = 0x800000000
        assert cpu2.run(self.POST, self.post) == self.post_hook + 4
        assert self.mem.u(self.paused_h, 1) == 0, 'the pause was not undone'
        got = []
        for i in range(3):
            x = self.mem.u(self.pos_h + 2 * i, 2)
            got.append(x - 0x10000 if x & 0x8000 else x)
        return tuple(got), paused


def _ffnx_model(track, n, frames):
    """ModelInterpolationEffectDecorator: the first call shows the routine's
    own position; after that each tick shows 1/n .. n/n of the way."""
    out, prev, nxt, t = [], None, None, 0
    for f in range(frames):
        if f == 0:
            nxt = track[0]
            out.append(nxt)
            continue
        step = (f - 1) % n + 1
        if step == 1:
            t += 1
            prev, nxt = nxt, track[t % len(track)]
        q = [abs((b - a) * step) // n * (1 if b >= a else -1)
             for a, b in zip(prev, nxt)]
        out.append(tuple(a + d for a, d in zip(prev, q)))
    return out


@pytest.mark.parametrize('freq_bits', [2, 1])
def test_summon_model_positions_match_ffnx(freq_bits):
    pytest.importorskip('ff7nx_dispatch_sites')
    n = 1 << freq_bits
    track = [(0, 0, 0), (300, -150, 90), (-120, 400, 7), (-500, -333, 61),
             (10, 20, -30)]
    rig = _ModelRig(freq_bits)
    want = _ffnx_model(track, n, 4 * n + 1)
    t = 0
    for f, exp in enumerate(want):
        if f and (f - 1) % n == 0:
            t += 1
        got, paused = rig.frame(track[t % len(track)])
        assert bool(paused) == (f > 0 and (f - 1) % n != 0), f
        assert got == exp, ('frame %d at %d sub-steps: cave %r, FFNx %r'
                            % (f, n, got, exp))


# --------------------------------------------------------------------------
# Widescreen battle fade: FFNx scales battle_sub_5BCF9D+0x3A by the battle
# multiplier only with widescreen on, after widescreen has made it 30. The
# Full 30 pass runs first and leaves 42, so widescreen must compose 42 -> 60
# and revert to 42.
# --------------------------------------------------------------------------
def _fade_img(count):
    import ff7nx_battlewide as B
    img = bytearray(0x1200000)  # the whole text; the other fade sites read as zeros and are only reported
    img[B.FADE_COUNT:B.FADE_COUNT + 4] = (0x52800008 | count << 5).to_bytes(4, 'little')
    return types.SimpleNamespace(img=bytes(img), arm_starts=()), B


@pytest.mark.parametrize('enter,leave', [(21, 30), (42, 60), (84, 120)])
def test_battlewide_fade_count_composes(enter, leave):
    m, B = _fade_img(enter)
    ps, _notes, problems = B.fade_animation_plan(m)
    rows = [p for p in ps if p['va'] == hex(B.FADE_COUNT)]
    assert not [p for p in problems if 'strip count' in p]
    assert len(rows) == 1
    assert rows[0]['set'] == B._fmt_word(0x52800008 | leave << 5)
    m, B = _fade_img(leave)
    ps, _notes, problems = B.fade_animation_plan(m, revert=True)
    rows = [p for p in ps if p['va'] == hex(B.FADE_COUNT)]
    assert len(rows) == 1 and rows[0]['set'] == B._fmt_word(0x52800008 | enter << 5)


def test_full30_fade_word_matches_battlewide():
    row = [r for r in R.RESOLVED_X2['r-battle_misc'] if r[1] == 0x007CBA94]
    assert row and row[0][3] == 0x52800008 | 42 << 5


# --------------------------------------------------------------------------
# Idle Animations 60fps: at a 30 FPS field each clip keeps every other frame.
# --------------------------------------------------------------------------
def _clip(frames, bones):
    import struct
    head = struct.pack('<III', 1, frames, bones) + bytes(range(24))
    size = 24 + 12 * bones
    body = b''.join(bytes([i & 0xFF]) * size for i in range(frames))
    return head + body, size


@pytest.mark.parametrize('frames', [2, 3, 1504])
def test_idleanim_half_rate(frames):
    import struct
    import ff7nx_idleanim as I
    data, size = _clip(frames, 21)
    out = I.half_rate(data)
    keep = (frames + 1) // 2
    assert struct.unpack_from('<III', out) == (1, keep, 21)
    assert out[12:I.ANIM_HEAD] == data[12:I.ANIM_HEAD]
    assert len(out) == I.ANIM_HEAD + keep * size
    for j in range(keep):
        assert out[I.ANIM_HEAD + j * size:I.ANIM_HEAD + (j + 1) * size] \
            == bytes([(2 * j) & 0xFF]) * size


def test_idleanim_half_rate_rejects_bad_clip():
    import ff7nx_idleanim as I
    data, _ = _clip(4, 21)
    with pytest.raises(ValueError):
        I.half_rate(data[:-1])
    with pytest.raises(ValueError):
        I.half_rate(_clip(1, 21)[0])
