#!/usr/bin/env python3
"""tests/test_reuse_archives.py -- per-archive fast reuse.

BUILD 324. `_archive_fingerprint` hashes every .py in the build folder that is
not in MAIN_ONLY_MODULES, **build.py included**, so editing the build script
invalidates every archive and battle.lgp / magic.lgp rebuild even when nothing
reaching them changed. Correct by default; pure cost during a test cycle.

These switches say "keep the one you already built". The safety rule is that
reuse verifies the file on disk is byte-for-byte the one the build cache wrote
-- not its fingerprint, which is the check being skipped -- and refuses (builds
normally) on anything it cannot vouch for.

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
import importlib.util
import os
import sys
import subprocess

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

BUILD_PY = nxpaths.src_file('build.py')
pytestmark = pytest.mark.skipif(not os.path.exists(BUILD_PY),
                                reason='needs build.py')


@pytest.fixture(scope='module')
def b():
    spec = importlib.util.spec_from_file_location('_build_under_test', BUILD_PY)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, b):
    for name in b.SCHEDULING_ENV:
        monkeypatch.delenv(name, raising=False)


def test_nothing_is_reused_by_default(b):
    for name in list(b.REUSE_ARCHIVE_ENV) + ['flevel.lgp', 'unknown.lgp']:
        assert b._reuse_requested(name) is False, name


def test_each_switch_reuses_only_its_own_archive(b, monkeypatch):
    for name, env in sorted(b.REUSE_ARCHIVE_ENV.items()):
        monkeypatch.setenv(env, '1')
        for other in b.REUSE_ARCHIVE_ENV:
            assert b._reuse_requested(other) is (other == name), (name, other)
        monkeypatch.delenv(env)


def test_the_umbrella_covers_every_archive_including_flevel(b, monkeypatch):
    monkeypatch.setenv(b.REUSE_ALL_ENV, '1')
    for name in b.REUSE_ARCHIVE_ENV:
        assert b._reuse_requested(name) is True, name
    assert b._truthy(b.REUSE_ALL_ENV) is True


def test_the_switches_accept_the_same_spellings_as_the_flevel_one(b,
                                                                 monkeypatch):
    env = b.REUSE_ARCHIVE_ENV['battle.lgp']
    for yes in ('1', 'true', 'TRUE', 'yes', 'on', ' On '):
        monkeypatch.setenv(env, yes)
        assert b._reuse_requested('battle.lgp') is True, yes
    for no in ('0', 'false', 'no', 'off', '', 'banana'):
        monkeypatch.setenv(env, no)
        assert b._reuse_requested('battle.lgp') is False, no


def test_scheduling_flags_are_excluded_from_every_fingerprint(b):
    """THE test. These change build scheduling, not archive bytes. If they
    reached the fingerprint, asking to skip battle.lgp would invalidate
    magic.lgp -- the exact opposite of the point -- and the first fast build
    would rebuild everything it was asked to skip."""
    assert b.REUSE_FLEVEL_ENV in b.SCHEDULING_ENV
    assert b.REUSE_ALL_ENV in b.SCHEDULING_ENV
    for env in b.REUSE_ARCHIVE_ENV.values():
        assert env in b.SCHEDULING_ENV, env
    src = open(BUILD_PY).read()
    # the fingerprint's env loop must filter on SCHEDULING_ENV, not on the
    # single flevel name it used to special-case
    assert "k not in SCHEDULING_ENV" in src
    assert "k != REUSE_FLEVEL_ENV" not in src


def test_reuse_refuses_when_there_is_no_archive(b, tmp_path, monkeypatch):
    monkeypatch.setattr(b, 'ARCHIVE_FP_CACHE', str(tmp_path / 'fp'))
    lines = []
    ok, payload = b._reuse_existing_archive(
        'battle.lgp', str(tmp_path / 'nope.lgp'), lines.append)
    assert ok is False and payload is None
    assert any('no existing archive' in l for l in lines)


def test_reuse_refuses_when_the_metadata_is_missing(b, tmp_path, monkeypatch):
    monkeypatch.setattr(b, 'ARCHIVE_FP_CACHE', str(tmp_path / 'fp'))
    dest = tmp_path / 'battle.lgp'
    dest.write_bytes(b'x' * 16)
    lines = []
    ok, _ = b._reuse_existing_archive('battle.lgp', str(dest), lines.append)
    assert ok is False
    assert any('metadata is missing' in l for l in lines)


def test_reuse_refuses_when_the_archive_is_not_the_cached_file(b, tmp_path,
                                                               monkeypatch):
    """The safety property: reuse skips the FINGERPRINT check, never the
    "is this the file we wrote" check. An archive touched or replaced by hand
    must be rebuilt rather than shipped unverified."""
    fp = tmp_path / 'fp'
    fp.mkdir()
    monkeypatch.setattr(b, 'ARCHIVE_FP_CACHE', str(fp))
    dest = tmp_path / 'battle.lgp'
    dest.write_bytes(b'x' * 16)
    sig = b._stat_sig(str(dest))
    (fp / 'battle.lgp.fp').write_text(
        'deadbeef\n%d\n%d\n' % (sig[0] + 1, sig[1]))    # wrong size
    lines = []
    ok, _ = b._reuse_existing_archive('battle.lgp', str(dest), lines.append)
    assert ok is False
    assert any('not the file the build cache wrote' in l for l in lines)


def test_reuse_accepts_the_exact_cached_file_and_returns_its_payload(
        b, tmp_path, monkeypatch):
    fp = tmp_path / 'fp'
    fp.mkdir()
    monkeypatch.setattr(b, 'ARCHIVE_FP_CACHE', str(fp))
    dest = tmp_path / 'world_us.lgp'
    dest.write_bytes(b'y' * 32)
    sig = b._stat_sig(str(dest))
    (fp / 'world_us.lgp.fp').write_text(
        'deadbeef\n%d\n%d\nsprite=2.0\n' % (sig[0], sig[1]))
    lines = []
    ok, payload = b._reuse_existing_archive(
        'world_us.lgp', str(dest), lines.append)
    assert ok is True
    assert payload == 'sprite=2.0'
    assert any('FAST REUSE' in l for l in lines)


def test_the_loop_restores_world_us_side_effects_on_reuse():
    """world_us.lgp's build publishes per-sprite UV factors that exefs/main
    needs; a skipped build must still restore them, exactly as the cache-hit
    path does. Checked in the source because it is a scheduling branch."""
    src = open(BUILD_PY).read()
    i = src.index('if _reuse_requested(name):')
    block = src[i:i + 1200]
    assert 'WORLD_GAIA_SPECIAL_ENV' in block
    assert 'produced.append(dest_path)' in block


def test_flevel_answers_to_the_umbrella_too():
    src = open(BUILD_PY).read()
    assert '_truthy(REUSE_FLEVEL_ENV) or _truthy(REUSE_ALL_ENV)' in src


# ------------------------------------------------------------------ build 326
# Why battle.lgp rebuilt on EVERY build, switch or no switch.
#
# It is written twice. The archive builder writes it and _archive_cache_store
# records that stat; ~10 s later apply_fps_patches reopens the same file and
# rewrites it in place with the animation waits scaled x4. So the file on disk
# was never the file in the record again. Measured on the operator's tree:
#
#   battle.lgp     MISMATCH  size 500256980/500256980  d = +10.714 s
#   magic.lgp      OK        d = 0.000 s
#   char.lgp       OK        d = 0.000 s
#   world_us.lgp   OK        d = 0.000 s
#   flevel.lgp     OK        d = 0.000 s
#
# Only the archive the 60 FPS pass touches. The record now carries that second
# signature too, and which one matched decides whether the 60 FPS pass may run
# over the archive again -- it may not; scaling is not idempotent.


@pytest.fixture
def fpdir(b, tmp_path, monkeypatch):
    d = tmp_path / 'fp'
    d.mkdir()
    monkeypatch.setattr(b, 'ARCHIVE_FP_CACHE', str(d))
    b.ALREADY_FPS_SCALED.clear()
    return d


def _rewrite(path, blob):
    """Rewrite in place with a different mtime, as the fps pass does."""
    import time
    time.sleep(0.01)
    path.write_bytes(blob)


def test_a_pre_326_record_still_reads(b, fpdir):
    (fpdir / 'battle.lgp.fp').write_text('FP\n100\n200\npayload\n')
    assert b._archive_record('battle.lgp') == ('FP', '100', '200',
                                               'payload', None)


def test_the_fps_signature_round_trips(b, fpdir, tmp_path):
    dest = tmp_path / 'battle.lgp'
    dest.write_bytes(b'a' * 100)
    b._archive_cache_store('battle.lgp', str(dest), 'FP1', 'payload')
    _rewrite(dest, b'b' * 100)
    b._archive_fps_restat('battle.lgp', str(dest))
    fp, size, mtime, payload, fps_sig = b._archive_record('battle.lgp')
    # the fingerprint and the side-effect payload are UNCHANGED: the inputs
    # did not change, only the file's mtime did
    assert (fp, payload) == ('FP1', 'payload')
    assert fps_sig == b._stat_sig(str(dest))
    assert (size, mtime) != (str(fps_sig[0]), str(fps_sig[1]))


def test_which_state_the_archive_is_in(b, fpdir, tmp_path):
    dest = tmp_path / 'battle.lgp'
    dest.write_bytes(b'a' * 100)
    base = b._stat_sig(str(dest))
    args = (str(dest), str(base[0]), str(base[1]))
    assert b._archive_on_disk_is_ours(*args, None) == 'base'
    _rewrite(dest, b'b' * 100)
    fps = b._stat_sig(str(dest))
    assert b._archive_on_disk_is_ours(*args, None) is None
    assert b._archive_on_disk_is_ours(*args, fps) == 'fps'
    dest.write_bytes(b'c' * 101)                    # touched by hand
    assert b._archive_on_disk_is_ours(*args, fps) is None


def test_the_scaled_archive_is_kept_only_for_a_60_fps_build(b, fpdir,
                                                            tmp_path,
                                                            monkeypatch):
    """THE safety property of this build. A 60 FPS-scaled battle.lgp in a
    30 FPS build has animation waits four times too long and nothing
    downstream would notice. FPS_ENV is in battle.lgp's fingerprint so the
    cache path cannot reach that state -- but the reuse switch skips the
    fingerprint by design, so the check lives where both paths pass."""
    dest = tmp_path / 'battle.lgp'
    dest.write_bytes(b'a' * 100)
    b._archive_cache_store('battle.lgp', str(dest), 'FP1')
    _rewrite(dest, b'b' * 100)
    b._archive_fps_restat('battle.lgp', str(dest))
    monkeypatch.setenv(b.REUSE_ALL_ENV, '1')

    monkeypatch.setenv(b.FPS_ENV, '0')
    b.ALREADY_FPS_SCALED.clear()
    lines = []
    assert b._archive_cache_ok('battle.lgp', str(dest), 'FP1',
                               lines.append)[0] is False
    assert b._reuse_existing_archive('battle.lgp', str(dest),
                                     lines.append)[0] is False
    assert sum('not a 60 FPS build' in l for l in lines) == 2
    assert not b.ALREADY_FPS_SCALED

    monkeypatch.setenv(b.FPS_ENV, '1')
    b.ALREADY_FPS_SCALED.clear()
    assert b._archive_cache_ok('battle.lgp', str(dest), 'FP1',
                               lambda *_: None)[0] is True
    assert 'battle.lgp' in b.ALREADY_FPS_SCALED
    b.ALREADY_FPS_SCALED.clear()
    assert b._reuse_existing_archive('battle.lgp', str(dest),
                                     lambda *_: None)[0] is True
    assert 'battle.lgp' in b.ALREADY_FPS_SCALED


def test_the_unscaled_state_still_works_for_both_paths(b, fpdir, tmp_path,
                                                       monkeypatch):
    """A build with 60 FPS off leaves battle.lgp in the 'base' state, and that
    must keep hitting exactly as it did before any of this."""
    monkeypatch.setenv(b.FPS_ENV, '0')
    monkeypatch.setenv(b.REUSE_ALL_ENV, '1')
    dest = tmp_path / 'battle.lgp'
    dest.write_bytes(b'a' * 100)
    b._archive_cache_store('battle.lgp', str(dest), 'FP1', 'payload')
    assert b._archive_cache_ok('battle.lgp', str(dest), 'FP1',
                               lambda *_: None) == (True, 'payload')
    assert b._reuse_existing_archive('battle.lgp', str(dest),
                                     lambda *_: None) == (True, 'payload')
    assert not b.ALREADY_FPS_SCALED       # nothing to tell the fps pass


def test_a_wrong_fingerprint_still_rebuilds(b, fpdir, tmp_path, monkeypatch):
    monkeypatch.setenv(b.FPS_ENV, '1')
    dest = tmp_path / 'battle.lgp'
    dest.write_bytes(b'a' * 100)
    b._archive_cache_store('battle.lgp', str(dest), 'FP1')
    _rewrite(dest, b'b' * 100)
    b._archive_fps_restat('battle.lgp', str(dest))
    assert b._archive_cache_ok('battle.lgp', str(dest), 'FP2',
                               lambda *_: None)[0] is False


def test_the_60fps_setting_reaches_only_battle_lgps_fingerprint(b, tmp_path,
                                                                monkeypatch):
    """It is a GUI checkbox, so the fingerprint was blind to it -- and it is
    the one setting outside that function which changes an archive's bytes.
    It must be in battle.lgp's key and in NO other archive's, because a
    1.4 GB flevel rebuild on every 60 FPS toggle is pure waste."""
    src = open(BUILD_PY).read()
    # excluded from the generic environment sweep ...
    assert "k != FPS_ENV" in src
    # ... and folded into battle.lgp's `extra` at the call site instead
    # (flevel.lgp's too, but only for a field that is not 60 FPS)
    assert "_fps_archive_key(name)," in src
    monkeypatch.delenv(b.FPS_MODE_ENV, raising=False)
    monkeypatch.setenv(b.FPS_ENV, '1')
    # the 60 FPS keys are the ones these archives had before Full 30
    assert b._fps_archive_key('battle.lgp') is True
    assert b._fps_archive_key('flevel.lgp') == 0
    assert b._fps_archive_key('magic.lgp') == 0
    monkeypatch.setenv(b.FPS_MODE_ENV, '30')
    assert b._fps_archive_key('battle.lgp') == 'x2'
    assert b._fps_archive_key('flevel.lgp') == 'field30'
    assert b._fps_archive_key('magic.lgp') == 0
    monkeypatch.setenv(b.FPS_ENV, '0')
    assert b._fps_archive_key('battle.lgp') is False
    assert b._fps_archive_key('flevel.lgp') == 'field30'
    monkeypatch.delenv(b.FPS_MODE_ENV)
    # behaviourally: toggling it changes no archive's generic fingerprint
    van = tmp_path / 'van.lgp'
    van.write_bytes(b'v' * 8)
    monkeypatch.setenv(b.FPS_ENV, '0')
    off = b._archive_fingerprint('magic.lgp', str(van), {}, 0)
    monkeypatch.setenv(b.FPS_ENV, '1')
    assert b._archive_fingerprint('magic.lgp', str(van), {}, 0) == off
    # and 7th_heaven_nx.py is what publishes it
    top = os.path.join(_HERE, '7th_heaven_nx.py')
    assert "os.environ[build.FPS_ENV] = '1' if fps_60 else '0'" in \
        open(top).read()


def test_a_shader_setting_reaches_no_archive_fingerprint(b, tmp_path,
                                                         monkeypatch):
    """
    `ff7nx_shaders.apply` writes .glsl files into romfs/ff7/shaders and
    nothing else, so changing the anti-aliasing setting cannot change a byte
    of any archive.

    It used to change flevel.lgp's INPUTS key, which is the half the reuse
    guard reads as a genuine mod/settings change and refuses outright --
    turning "is FXAA costing me frames?" into a 40-minute 1.4 GB rebuild.
    Both keys are checked: the combined one decides whether the archive
    rebuilds, the inputs one decides whether reuse is even allowed.
    """
    van = tmp_path / 'van.lgp'
    van.write_bytes(b'v' * 8)
    for env in (b.ff7nx_shaders.SCALER_ENV, b.ff7nx_shaders.FXAA_ENV,
                b.ff7nx_shaders.VIDEO_ENV):
        assert env in b.SHADER_ONLY_ENV, env
        assert env in b.ARCHIVE_NEUTRAL_ENV, env
        monkeypatch.setenv(env, '')
        combined = b._archive_fingerprint('flevel.lgp', str(van), {}, 0)
        inputs = b._archive_inputs_fingerprint('flevel.lgp', str(van), {}, 0)
        monkeypatch.setenv(env, 'hd')
        assert b._archive_fingerprint('flevel.lgp', str(van), {}, 0) == \
            combined, '%s moved the combined key' % env
        assert b._archive_inputs_fingerprint('flevel.lgp', str(van), {}, 0) \
            == inputs, '%s moved the INPUTS key -- reuse will refuse' % env
    # and the module that writes them is excluded the same way
    assert 'ff7nx_shaders.py' in b.ARCHIVE_NEUTRAL_MODULES


def test_a_setting_that_does_reach_flevel_still_moves_both_keys(b, tmp_path,
                                                                monkeypatch):
    """
    The complement, and the reason the exclusion is a named list rather than a
    broad rule: a setting that really does change an archive's bytes must
    still invalidate it. A guard that waves everything through is not a guard.
    """
    van = tmp_path / 'van.lgp'
    van.write_bytes(b'v' * 8)
    monkeypatch.delenv(b.FIELD_BG_CAP_ENV, raising=False)
    combined = b._archive_fingerprint('flevel.lgp', str(van), {}, 0)
    inputs = b._archive_inputs_fingerprint('flevel.lgp', str(van), {}, 0)
    monkeypatch.setenv(b.FIELD_BG_CAP_ENV, '512')
    assert b._archive_fingerprint('flevel.lgp', str(van), {}, 0) != combined
    assert b._archive_inputs_fingerprint('flevel.lgp', str(van), {}, 0) \
        != inputs


def test_the_neutral_sets_are_supersets_of_the_main_only_ones(b):
    """The rename must not have dropped anything the old lists excluded."""
    assert b.MAIN_ONLY_ENV <= b.ARCHIVE_NEUTRAL_ENV
    assert b.MAIN_ONLY_MODULES <= b.ARCHIVE_NEUTRAL_MODULES
    src = open(BUILD_PY).read()
    assert 'k not in MAIN_ONLY_ENV' not in src
    assert 'fn not in MAIN_ONLY_MODULES' not in src


def test_the_fps_pass_leaves_an_already_scaled_archive_alone():
    """Scaling is not idempotent -- ff7nx_60fps's own guard ABORTS the build
    on an already-scaled archive -- and the dump fallback would overwrite the
    mods' battle.lgp with a vanilla-derived one. Not touching it is the only
    correct answer, so assert the branch does exactly nothing else."""
    src = open(BUILD_PY).read()
    i = src.index("if 'battle.lgp' in ALREADY_FPS_SCALED:")
    block = src[i:src.index('else:', i)]
    assert 'battle = None' in block
    assert 'dump.workingdir' not in block          # no fallback from here
    # and the restat only fires for the archive this pass actually wrote
    assert 'if battle == built_battle:' in src
    assert "_archive_fps_restat('battle.lgp', built_battle, log)" in src


def test_the_flag_cannot_leak_between_two_builds_in_one_process():
    src = open(BUILD_PY).read()
    i = src.index('def apply_plan(')
    assert 'ALREADY_FPS_SCALED.clear()' in src[i:i + 1500]


def test_field_dds_fingerprint_is_stable_across_python_launches():
    # Real active Cosmos Limit Break folders. A same-process comparison
    # would miss the bug because a set's iteration order stays stable there.
    code = '''
import sys
sys.path.insert(0, %r)
import nxpaths; nxpaths.install()
import build
sources = [('CosmosLimitBreak.iro', {
    'aa removed/', 'limit break aaf/', 'limit break aa/', 'limit break/'})]
extra = build._field_dds_fingerprint(sources)
print(build._archive_fingerprint('flevel.lgp', 'missing', {}, extra))
''' % nxpaths.SRC
    results = []
    for seed in ('1', '2', '3', '4'):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        results.append(subprocess.check_output(
            [sys.executable, '-c', code], env=env, text=True).strip())
    assert len(set(results)) == 1, results


def test_field_dds_fingerprint_retains_filter_changes(b):
    key = b._field_dds_fingerprint
    assert key([('a.iro', {'b/', 'a/'})]) == [('a.iro', ('a/', 'b/'))]
    assert key([('a.iro', None)]) != key([('a.iro', set())])
    assert key([('a.iro', {'a/'})]) != key([('a.iro', {'b/'})])
    assert key([('a.iro', {'a/'})]) != key([('b.iro', {'a/'})])
    src = open(BUILD_PY).read()
    assert '_field_dds_fingerprint(plan.field_dds_sources)' in src


def test_flevel_rebaseline_is_one_shot_and_does_not_change_archive(
        b, fpdir, tmp_path, monkeypatch):
    root = tmp_path / 'sdout'
    dest = root / 'atmosphere' / 'contents' / b.TITLE_ID / b.ROMFS / b.ARCHIVES['flevel.lgp']
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b'unchanged flevel fixture')
    sig = b._stat_sig(str(dest))
    rec = fpdir / 'flevel.lgp.fp'
    rec.write_text('old-key\n%d\n%d\n123456\n' % sig)
    monkeypatch.setenv(b.REBASE_FLEVEL_ENV, '1')
    b._reuse_existing_flevel(str(root), 'stable-key')
    assert b._stat_sig(str(dest)) == sig
    assert dest.read_bytes() == b'unchanged flevel fixture'
    monkeypatch.delenv(b.REBASE_FLEVEL_ENV)
    b._reuse_existing_flevel(str(root), 'stable-key')
    with pytest.raises(RuntimeError, match='inputs or build code'):
        b._reuse_existing_flevel(str(root), 'different-key')
    # Even explicit recovery must refuse an externally modified archive.
    monkeypatch.setenv(b.REBASE_FLEVEL_ENV, '1')
    dest.write_bytes(b'changed archive')
    with pytest.raises(RuntimeError, match='no longer matches'):
        b._reuse_existing_flevel(str(root), 'different-key')


def test_the_dynweapon_hrc_flag_cannot_invalidate_flevel(b, monkeypatch):
    """
    BUILD 439. `SEVENTH_NX_DW_HRC` changes the bytes of the model archives
    Dynamic Weapons writes into and nothing else. It was left in the generic
    environment sweep, so asking for a one-archive experiment invalidated
    flevel.lgp's INPUTS key too and the reuse guard refused with "flevel
    inputs or build code changed" -- demanding the 1.4 GB rebuild the
    experiment exists to avoid.
    """
    def keys():
        return (b._archive_fingerprint('flevel.lgp', '/nonexistent', {}, ()),
                b._archive_inputs_fingerprint('flevel.lgp', '/nonexistent',
                                              {}, ()))

    monkeypatch.delenv(b.DW_HRC_ENV, raising=False)
    off = keys()
    monkeypatch.setenv(b.DW_HRC_ENV, 'static')
    assert keys() == off
    monkeypatch.setenv(b.DW_HRC_ENV, 'variant')
    assert keys() == off


def test_the_dynweapon_hrc_flag_still_reaches_the_archives_it_changes(b):
    """
    Scoped, not ignored: an archive the dynamic-weapons pass writes into must
    still rebuild when the flag flips, or the experiment would silently reuse
    the archive it was meant to replace.
    """
    class _Plan:
        dynweapon = [{'archive': 'char.lgp'}]

    assert b._dynweapon_targets(_Plan()) == frozenset({'char.lgp'})
    assert b._dynweapon_targets(_Plan) is not None

    class _Empty:
        dynweapon = []

    assert b._dynweapon_targets(_Empty()) == frozenset()
    # the per-archive `extra` carries it only for those targets
    src = open(BUILD_PY).read()
    assert '_dynweapon_targets(plan)' in src
    assert "k != DW_HRC_ENV" in src


def test_the_facial_flag_reaches_no_generic_fingerprint(b, tmp_path,
                                                        monkeypatch):
    """
    BUILD 467. `SEVENTH_NX_FACIAL` only moves a DEFAULT, and a value saved in
    settings.json beats it -- so somebody whose saved setting already says On
    gets a byte-identical flevel whether the flag is set or not. Keying on the
    variable meant DROPPING it invalidated a 1.5 GB archive that had not
    changed, which is exactly what it did to the build after 466.

    The resolved answer still has to be in flevel's key, because it really
    does decide whether 994 textures ship. That is checked below.
    """
    van = tmp_path / 'van.lgp'
    van.write_bytes(b'v' * 8)
    monkeypatch.delenv('SEVENTH_NX_FACIAL', raising=False)
    combined = b._archive_fingerprint('flevel.lgp', str(van), {}, 0)
    inputs = b._archive_inputs_fingerprint('flevel.lgp', str(van), {}, 0)
    monkeypatch.setenv('SEVENTH_NX_FACIAL', '1')
    assert b._archive_fingerprint('flevel.lgp', str(van), {}, 0) == combined
    assert b._archive_inputs_fingerprint('flevel.lgp', str(van), {}, 0) \
        == inputs
    # and it is folded in at the call site, the way FPS_ENV is
    src = open(BUILD_PY).read()
    assert 'k != iro.FACIAL_ENV' in src
    assert "bool(getattr(plan, 'facial_art', False))" in src


def test_the_resolved_facial_state_still_moves_flevels_key(b, tmp_path):
    """The thing that really does change the archive has to invalidate it."""
    van = tmp_path / 'van.lgp'
    van.write_bytes(b'v' * 8)
    off = b._archive_inputs_fingerprint('flevel.lgp', str(van), {}, (1, False))
    on = b._archive_inputs_fingerprint('flevel.lgp', str(van), {}, (1, True))
    assert off != on


def test_both_flevel_keys_are_built_from_one_tuple(b):
    """They used to be two hand-maintained copies with a comment promising
    they matched. Now they cannot drift."""
    src = open(BUILD_PY).read()
    assert 'flevel_extra = (' in src
    assert src.count("'flevel.lgp', archive_paths['flevel.lgp'], "
                     "dict(flevel_fields),\n            flevel_extra)") == 2
