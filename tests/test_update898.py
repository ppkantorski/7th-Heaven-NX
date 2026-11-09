#!/usr/bin/env python3
"""BUILD 898: the self-updater proves its installer is running before the
program quits, reports an installer that never finished, starts the next
copy with a clean environment, and has a batch fallback on Windows.

    python3 -m pytest tests/test_update898.py

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'tests'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)

import json
import os
import subprocess
import sys
import time

import pytest

import ff7nx_update as U
from test_update890 import sandbox, _old_install, _new_copy  # noqa: F401

posix_linux = pytest.mark.skipif(os.name != 'posix' or sys.platform == 'darwin',
                                 reason='the sh installer, Linux layout')


def _dead_pid():
    p = subprocess.Popen(['true'])
    p.wait()
    return p.pid


def _wait_for(path, secs=10):
    for _ in range(int(secs * 10)):
        if os.path.exists(path):
            return True
        time.sleep(0.1)
    return False


@posix_linux
def test_handshake_then_install(sandbox):
    root = str(sandbox)
    _old_install(root)
    new = _new_copy(root)
    argvs = U.write_scripts(new, '1.2.0', pid=_dead_pid(), system='linux')
    proc = U.start_installer(argvs, '1.2.0', handshake=10)
    proc.wait(30)
    assert _wait_for(os.path.join(root, 'launched'))
    assert open(os.path.join(root, 'launched')).read().strip() == 'new'
    assert U.last_result('1.2.0') == ('ok', '1.2.0')
    assert not os.path.exists(U.WORK)


@posix_linux
def test_installer_that_never_starts_is_stopped_and_the_next_is_used(sandbox):
    root = str(sandbox)
    _old_install(root)
    new = _new_copy(root)
    good = U.write_script(new, '1.2.0', pid=_dead_pid(), system='linux')
    t0 = time.time()
    proc = U.start_installer([['sleep', '30'], good], '1.2.0', handshake=1.5)
    assert time.time() - t0 < 10
    proc.wait(30)
    assert _wait_for(os.path.join(root, 'launched'))
    assert open(os.path.join(root, 'launched')).read().strip() == 'new'
    log = open(U.APPLY_LOG).read() if os.path.exists(U.APPLY_LOG) else ''
    assert 'sleep did not start' in log or U.last_result('1.2.0')[0] == 'ok'


def test_no_installer_starts_raises_and_leaves_nothing_pending(sandbox):
    os.makedirs(U.WORK, exist_ok=True)
    with pytest.raises(U.UpdateError, match='could not be started'):
        U.start_installer([['sleep', '30'], ['false']], '1.2.0',
                          handshake=1)
    assert not os.path.exists(U.PENDING)
    assert not os.path.exists(U.STARTED)


def test_an_installer_that_stopped_part_way_is_reported(sandbox):
    os.makedirs(os.path.join(U.WORK, 'new'))
    open(U.APPLY_LOG, 'w').write('12:00:00 [sh] waiting\n')
    json.dump({'version': '1.2.0', 'pid': _dead_pid(), 'at': time.time()},
              open(U.PENDING, 'w'))
    out = U.last_result('1.1.0')
    assert out[0] == 'failed' and '1.1.0 is still installed' in out[1]
    assert U.LAST_LOG in out[1] and os.path.isfile(U.LAST_LOG)
    assert not os.path.exists(os.path.join(U.WORK, 'new'))


def test_an_installer_still_running_is_left_alone(sandbox):
    os.makedirs(os.path.join(U.WORK, 'new'))
    p = subprocess.Popen(['sleep', '20'])
    try:
        json.dump({'version': '1.2.0', 'pid': p.pid, 'at': time.time()},
                  open(U.PENDING, 'w'))
        assert U.last_result('1.1.0') == ('busy', '1.2.0')
        assert os.path.isdir(os.path.join(U.WORK, 'new'))
    finally:
        p.kill()


@posix_linux
def test_old_program_that_never_exits_changes_nothing(sandbox, monkeypatch):
    root = str(sandbox)
    _old_install(root)
    new = _new_copy(root)
    monkeypatch.setattr(U, 'WAIT_EXIT', 1)
    alive = subprocess.Popen(['sleep', '30'])
    try:
        argv = U.write_script(new, '1.2.0', pid=alive.pid, system='linux')
        subprocess.run(argv, timeout=30)
    finally:
        alive.kill()
    assert os.listdir(os.path.join(root, '_internal')) == ['old']
    assert not os.path.exists(os.path.join(root, 'launched'))
    assert 'did not close' in open(U.RESULT).read()


def test_installer_env_is_clean(monkeypatch):
    base = {'PATH': '/usr/bin', '_PYI_ARCHIVE_FILE': 'x',
            '_PYI_PARENT_PROCESS_LEVEL': '1', '_MEIPASS2': 'y',
            'LD_LIBRARY_PATH': '/app/_internal', 'LD_LIBRARY_PATH_ORIG': '/u',
            'SEVENTH_NX_TITLE_SCREEN': '2', 'SEVENTH_NX_HOME_USER': 'kept'}
    monkeypatch.setattr(nxpaths, 'LAUNCH_ENV_KEYS',
                        frozenset({'PATH', 'SEVENTH_NX_HOME_USER'}))
    env = U.installer_env(base)
    assert env['PYINSTALLER_RESET_ENVIRONMENT'] == '1'
    assert not any(k.startswith('_PYI') or k == '_MEIPASS2' for k in env)
    assert env['LD_LIBRARY_PATH'] == '/u' and 'LD_LIBRARY_PATH_ORIG' not in env
    assert 'SEVENTH_NX_TITLE_SCREEN' not in env, 'set by the old copy'
    assert env['SEVENTH_NX_HOME_USER'] == 'kept', 'the user set this one'


def test_windows_gets_powershell_then_an_ascii_batch(sandbox, monkeypatch):
    monkeypatch.setattr(nxpaths, 'ROOT', 'C:\\Users\\Jos\u00e9\\7th Heaven NX')
    new = os.path.join(U.WORK, 'new', '7th Heaven NX')
    os.makedirs(os.path.join(new, '_internal'))
    for f in ('7th Heaven NX.exe', 'README.md', 'LICENSE'):
        open(os.path.join(new, f), 'w').close()
    argvs = U.write_scripts(new, '1.2.0', pid=42, system='windows')
    assert argvs[0][0] == 'powershell' and argvs[1] == [
        'cmd.exe', '/d', '/c', 'apply.cmd']
    ps = open(argvs[0][-1], encoding='utf-8-sig').read()
    assert ps.index("Set-Content -LiteralPath") < ps.index('Wait-Process')
    assert 'did not close within' in ps
    raw = open(os.path.join(U.WORK, 'apply.cmd'), 'rb').read()
    assert raw.isascii() and b'\r\n' in raw
    text = raw.decode()
    assert 'Jos' not in text, 'paths come from %~dp0, not literals'
    assert text.index('started') < text.index(':wait')
    assert 'set "NEW=%W%new\\7th Heaven NX"' in text
    assert '"\\"42\\""' in text and '>"%RESULT%" echo OK 1.2.0' in text
    # every item is put back on failure, the directory with rmdir
    assert 'if defined M1 if exist "%ROOT%\\_internal" rmdir /s /q' in text
    assert 'call :mv "' not in text, 'paths never pass through CALL'
