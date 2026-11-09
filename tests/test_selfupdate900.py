#!/usr/bin/env python3
"""BUILD 900: the program installs its own update (ff7nx_selfupdate), with
the scripts as fallback.

    python3 -m pytest tests/test_selfupdate900.py

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

import pytest

import ff7nx_selfupdate as S
import ff7nx_update as U
from test_update890 import sandbox  # noqa: F401

ITEMS = ['7th Heaven NX', '_internal', 'README.md', 'LICENSE']


def _dead_pid():
    p = subprocess.Popen(['true'])
    p.wait()
    return p.pid


def _layout(root):
    """An installed old copy, a staged new one, and user data beside them."""
    work = os.path.join(root, '.nx_update')
    new = os.path.join(work, 'new', '7th Heaven NX')
    for base, tag in ((root, 'old'), (new, 'new')):
        os.makedirs(os.path.join(base, '_internal', 'lib'))
        open(os.path.join(base, '_internal', 'lib', tag), 'w').close()
        for f in ('7th Heaven NX', 'README.md', 'LICENSE'):
            with open(os.path.join(base, f), 'w') as fh:
                fh.write(tag)
    os.symlink('lib', os.path.join(new, '_internal', 'link'))
    os.makedirs(os.path.join(root, 'mods'))
    open(os.path.join(root, 'mods', 'm.iro'), 'w').write('mod')
    open(os.path.join(root, 'settings.json'), 'w').write('{"k": 1}')
    return work, new


def _plan(root, work, new, pid, launched):
    return {'root': root, 'new': new, 'work': work, 'items': ITEMS,
            'pid': pid, 'version': '1.1.3', 'wait': 2,
            'result': os.path.join(work, 'result.txt'),
            'log': os.path.join(work, 'apply.log'),
            'started': os.path.join(work, 'started'),
            'launch': [launched], 'system': 'linux', 'name': '7th Heaven NX'}


@pytest.fixture
def no_launch(monkeypatch):
    seen = []
    monkeypatch.setattr(S, '_launch', lambda plan: seen.append(plan['launch']))
    return seen


def _state(root):
    return (open(os.path.join(root, 'README.md')).read(),
            sorted(os.listdir(os.path.join(root, '_internal', 'lib'))))


def test_installs_every_item_and_keeps_user_data(tmp_path, no_launch):
    root = str(tmp_path)
    work, new = _layout(root)
    plan = _plan(root, work, new, _dead_pid(), 'x')
    assert S.apply(plan) == 0
    assert _state(root) == ('new', ['new'])
    assert os.readlink(os.path.join(root, '_internal', 'link')) == 'lib'
    assert open(plan['result']).read() == 'OK 1.1.3\n'
    assert os.path.exists(plan['started'])
    assert no_launch == [['x']]
    assert open(os.path.join(root, 'settings.json')).read() == '{"k": 1}'
    assert os.listdir(os.path.join(root, 'mods')) == ['m.iro']
    assert os.path.isdir(new), 'the running copy is left for the next launch'
    assert not os.path.exists(os.path.join(work, 'old'))
    assert not os.path.exists(os.path.join(work, 'incoming'))


def test_a_failed_copy_changes_nothing(tmp_path, no_launch):
    root = str(tmp_path)
    work, new = _layout(root)
    os.remove(os.path.join(new, 'LICENSE'))
    plan = _plan(root, work, new, _dead_pid(), 'x')
    assert S.apply(plan) == 1
    assert _state(root) == ('old', ['old'])
    assert open(os.path.join(root, 'LICENSE')).read() == 'old'
    assert open(plan['result']).read().startswith('FAILED')
    assert no_launch == [['x']], 'the old program is started again'


def test_a_failed_swap_puts_everything_back(tmp_path, no_launch,
                                            monkeypatch):
    root = str(tmp_path)
    work, new = _layout(root)
    real = os.rename
    calls = []

    def rename(src, dst):
        calls.append(dst)
        # the 3rd item's new copy cannot be moved in (the rollback's own
        # rename of the old one back into place still works)
        if src == os.path.join(work, 'incoming', 'README.md'):
            raise PermissionError('held by something')
        return real(src, dst)
    monkeypatch.setattr(S.os, 'rename', rename)
    monkeypatch.setattr(S, 'MOVE_TRIES', 2)
    monkeypatch.setattr(S.time, 'sleep', lambda *_: None)
    plan = _plan(root, work, new, _dead_pid(), 'x')
    assert S.apply(plan) == 1
    monkeypatch.setattr(S.os, 'rename', real)
    assert _state(root) == ('old', ['old'])
    for f in ('7th Heaven NX', 'LICENSE'):
        assert open(os.path.join(root, f)).read() == 'old'
    assert 'held by something' in open(plan['result']).read()
    assert no_launch == [['x']]


def test_an_old_program_that_never_exits_changes_nothing(tmp_path,
                                                         no_launch):
    root = str(tmp_path)
    work, new = _layout(root)
    alive = subprocess.Popen(['sleep', '30'])
    try:
        plan = _plan(root, work, new, alive.pid, 'x')
        plan['wait'] = 1
        assert S.apply(plan) == 1
    finally:
        alive.kill()
    assert _state(root) == ('old', ['old'])
    assert 'did not close' in open(plan['result']).read()
    assert no_launch == [], 'nothing is started while the old one runs'


def test_runs_from_the_command_line_and_starts_the_program(tmp_path):
    root = str(tmp_path)
    work, new = _layout(root)
    launched = os.path.join(root, 'launch.sh')
    with open(launched, 'w') as f:
        f.write('#!/bin/sh\necho "$PYINSTALLER_RESET_ENVIRONMENT" > "%s/env"\n'
                % root)
    os.chmod(launched, 0o755)
    plan = _plan(root, work, new, _dead_pid(), launched)
    path = os.path.join(work, 'plan.json')
    json.dump(plan, open(path, 'w'))
    entry = os.path.join(_nx_root, '7th_heaven_nx.py')
    env = dict(os.environ, _PYI_ARCHIVE_FILE='x')
    r = subprocess.run([sys.executable, entry, S.FLAG, path], env=env,
                       timeout=60)
    assert r.returncode == 0
    for _ in range(50):
        if os.path.exists(os.path.join(root, 'env')):
            break
        import time
        time.sleep(0.1)
    assert open(os.path.join(root, 'env')).read().strip() == '1'
    assert _state(root) == ('new', ['new'])
    assert not os.path.exists(os.path.join(root, '7th_heaven_nx.log'))


def test_clean_env():
    env = S.clean_env({'_PYI_X': '1', 'LD_LIBRARY_PATH': '/app',
                       'LD_LIBRARY_PATH_ORIG': '/u', 'PATH': '/bin'})
    assert env == {'LD_LIBRARY_PATH': '/u', 'PATH': '/bin',
                   'PYINSTALLER_RESET_ENVIRONMENT': '1'}


# --------------------------------------------- choosing the installer
def _staged(root, system):
    new = os.path.join(root, '.nx_update', 'new', '7th Heaven NX')
    if system == 'macos':
        exe = os.path.join(new, '7th Heaven NX.app', 'Contents', 'MacOS',
                           '7th Heaven NX')
    else:
        exe = os.path.join(new, '7th Heaven NX' + ('.exe' if system ==
                                                   'windows' else ''))
    os.makedirs(os.path.dirname(exe), exist_ok=True)
    open(exe, 'w').close()
    os.makedirs(os.path.join(new, '_internal'), exist_ok=True)
    for f in ('README.md', 'LICENSE'):
        open(os.path.join(new, f), 'w').close()
    return new, exe


@pytest.mark.parametrize('system', ['windows', 'linux', 'macos'])
def test_installer_order(sandbox, system):
    new, exe = _staged(str(sandbox), system)
    argvs = U.write_scripts(new, '1.1.3', pid=42, system=system, frozen=True)
    first = [a[0] for a in argvs]
    if system == 'windows':
        assert first == [exe, 'powershell', 'cmd.exe']
    elif system == 'linux':
        assert first == [exe, '/bin/sh']
    else:
        assert first == ['/bin/sh', exe], 'macOS keeps sh first'
    helper = next(a for a in argvs if a[0] == exe)
    assert helper[1] == '--nx-apply-update'
    plan = json.load(open(helper[2]))
    assert plan['pid'] == 42 and plan['version'] == '1.1.3'
    assert plan['root'] == str(sandbox) and plan['new'] == new
    assert plan['started'] == U.STARTED and plan['result'] == U.RESULT


def test_no_helper_from_source_or_for_an_older_release(sandbox):
    new, _exe = _staged(str(sandbox), 'linux')
    assert U.write_helper(new, '1.1.3', system='linux', frozen=False) is None
    assert U.write_helper(new, '1.1.1', system='linux', frozen=True) is None
    assert U.write_helper(new, '1.1.2', system='linux', frozen=True)
    os.remove(_exe)
    assert U.write_helper(new, '1.1.3', system='linux', frozen=True) is None
