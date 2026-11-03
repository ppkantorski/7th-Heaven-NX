"""BUILD 880: a missing FFmpeg is fetched again before a build, and the build
stops with the reason instead of finishing without movies, sound and voice.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)

import ff7nx_deps as D  # noqa: E402


def test_retry_reports_why(monkeypatch):
    monkeypatch.delenv(D.FFMPEG_ENV, raising=False)
    monkeypatch.setattr(D.shutil, 'which', lambda _n: None)
    monkeypatch.setattr(D, '_have', lambda _m: False)
    ok, why = D.retry_ffmpeg('/nonexistent')
    assert not ok and 'static-ffmpeg' in why


def test_retry_ok_when_on_path(monkeypatch):
    monkeypatch.setattr(D.shutil, 'which', lambda n: '/usr/bin/' + n)
    assert D.retry_ffmpeg('/nonexistent') == (True, '')


def test_retry_download_failure_message(monkeypatch):
    monkeypatch.delenv(D.FFMPEG_ENV, raising=False)
    monkeypatch.setattr(D.shutil, 'which', lambda _n: None)
    monkeypatch.setattr(D, '_have', lambda _m: True)
    import types
    fake = types.ModuleType('static_ffmpeg')
    run = types.ModuleType('static_ffmpeg.run')

    def boom(**_kw):
        raise OSError('connection refused')
    run.get_or_fetch_platform_executables_else_raise = boom
    fake.run = run
    monkeypatch.setitem(_nx_sys.modules, 'static_ffmpeg', fake)
    monkeypatch.setitem(_nx_sys.modules, 'static_ffmpeg.run', run)
    ok, why = D.retry_ffmpeg('/nonexistent')
    assert not ok and 'connection refused' in why


def test_gate_runs_before_the_build():
    src = open(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')).read()
    body = src[src.index('def run_build('):src.index('def _run_build_steps(')]
    assert body.index('_ffmpeg_gate(log)') < body.index('_BuildClock(log)')
    assert "ALLOW_NO_FFMPEG_ENV = 'SEVENTH_NX_ALLOW_NO_FFMPEG'" in src


def test_tutorial_redirect_needs_its_movie():
    src = open(_nx_os.path.join(_nx_root, 'src', 'core', 'build.py')).read()
    body = src[src.index('def apply_echo_tutorial('):
               src.index('def apply_battle_sfx(')]
    assert body.index('if not os.path.exists(movie):') < body.index(
        'os.replace(tmp, dest)')
