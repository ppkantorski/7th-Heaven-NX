#!/usr/bin/env python3
"""BUILD 890: ff7nx_update -- checking for, verifying and installing a new
release of 7th Heaven NX. No network: GitHub is replaced by a stub.

    python3 -m pytest tests/test_update890.py

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)

import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import time
import urllib.error
import zipfile

import pytest

import ff7nx_update as U


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    root = tmp_path / 'root'
    root.mkdir()
    monkeypatch.setattr(nxpaths, 'ROOT', str(root))
    monkeypatch.setattr(U, 'STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setattr(U, 'STATE_FILE', str(tmp_path / 'state' / 's.json'))
    monkeypatch.setattr(U, 'WORK', str(root / '.nx_update'))
    monkeypatch.setattr(U, 'RESULT', str(root / '.nx_update' / 'result.txt'))
    # BUILD 898's installer handshake and its log
    monkeypatch.setattr(U, 'STARTED', str(root / '.nx_update' / 'started'))
    monkeypatch.setattr(U, 'PENDING', str(root / '.nx_update' / 'pending.json'))
    monkeypatch.setattr(U, 'APPLY_LOG', str(root / '.nx_update' / 'apply.log'))
    monkeypatch.setattr(U, 'LAST_LOG', str(tmp_path / 'state' / 'last.log'))
    return root


class _Resp(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(body)
        self.headers = headers or {}


def _release(version='1.2.0', data=b'zip', name=None):
    name = name or U.asset_name(version)
    return {'tag_name': 'v' + version, 'name': 'v' + version,
            'body': '## Changes', 'html_url': 'https://example/r',
            'assets': [{'name': name, 'size': len(data),
                        'digest': 'sha256:' + hashlib.sha256(data).hexdigest(),
                        'browser_download_url': 'https://example/' + name}]}


def test_versions():
    assert U.parse_version('v1.1.0') == (1, 1, 0)
    assert U.parse_version('1.2') == (1, 2, 0)
    assert U.parse_version('beta') is None
    assert U.is_newer('1.1.1', '1.1.0') and U.is_newer('v1.10.0', '1.9.9')
    assert not U.is_newer('1.1.0', '1.1.0') and not U.is_newer('1.0.9', '1.1.0')
    assert not U.is_newer('garbage', '1.0.0')


def test_asset_name_matches_release_tool():
    assert U.asset_name('1.1.0', 'macos', 'arm64') == \
        '7th-Heaven-NX-1.1.0-macos-arm64.zip'
    sys.path.insert(0, os.path.join(nxpaths.ROOT, 'tools'))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'nxrelease', os.path.join(_nx_root, 'tools', 'release.py'))
    rel = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rel)
    assert rel.target() == U.target()


def test_check_conditional_and_cached(sandbox, monkeypatch):
    calls = []
    body = json.dumps(_release()).encode()

    def fake_open(url, headers=None, timeout=None):
        calls.append(dict(headers or {}))
        if headers and headers.get('If-None-Match') == '"e1"':
            raise urllib.error.HTTPError(url, 304, 'Not Modified', {}, None)
        return _Resp(body, {'ETag': '"e1"'})
    monkeypatch.setattr(U, '_open', fake_open)
    r = U.check('1.1.0', force=True)
    assert r.version == '1.2.0' and r.asset()['sha256']
    assert 'If-None-Match' not in calls[0]
    assert U.check('1.1.0', force=False).version == '1.2.0'
    assert len(calls) == 1, 'a check within RECHECK_AFTER sends nothing'
    assert U.check('1.1.0', force=True).version == '1.2.0'
    assert calls[1]['If-None-Match'] == '"e1"', 'rechecks are conditional'
    assert U.check('1.2.0', force=True) is None
    assert all('@' not in v for c in calls for v in c.values())


def test_check_errors(sandbox, monkeypatch):
    def rate_limited(url, headers=None, timeout=None):
        raise urllib.error.HTTPError(url, 403, 'rate', {}, None)
    monkeypatch.setattr(U, '_open', rate_limited)
    with pytest.raises(U.UpdateError):
        U.check('1.0.0', force=True)


def test_download_verifies_checksum(sandbox, monkeypatch):
    data = b'x' * 100000
    rel = U.Release(_release(data=data))
    monkeypatch.setattr(U, '_open', lambda url, headers=None, timeout=None:
                        _Resp(data, {'Content-Length': str(len(data))}))
    path = U.download(rel.asset())
    assert open(path, 'rb').read() == data
    bad = dict(rel.asset(), sha256='0' * 64)
    monkeypatch.setattr(U.time, 'sleep', lambda s: None)
    with pytest.raises(U.UpdateError, match='SHA-256'):
        U.download(bad)
    with pytest.raises(U.UpdateError, match='checksum'):
        U.download(dict(rel.asset(), sha256=None))


def _make_release_zip(path, version, evil=None):
    """A Linux/Windows-layout release zip, as tools/release.py writes it."""
    with zipfile.ZipFile(path, 'w') as zf:
        def add(name, data, mode=0o644):
            zi = zipfile.ZipInfo(name)
            zi.create_system = 3
            zi.external_attr = (stat.S_IFREG | mode) << 16
            zf.writestr(zi, data)
        exe = U.program_items('linux')[0]
        add('7th Heaven NX/' + exe, b'#!/bin/sh\n', 0o755)
        add('7th Heaven NX/_internal/7th_heaven_nx.py',
            "VERSION = '%s'\n" % version)
        add('7th Heaven NX/_internal/libz.so.1.2', b'lib')
        zi = zipfile.ZipInfo('7th Heaven NX/_internal/libz.so')
        zi.create_system = 3
        zi.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(zi, 'libz.so.1.2')
        add('7th Heaven NX/README.md', b'r')
        add('7th Heaven NX/LICENSE', b'l')
        if evil:
            add(evil, b'boom')


@pytest.mark.skipif(os.name != 'posix' or sys.platform == 'darwin',
                    reason='zipfile staging is the Linux/Windows path')
def test_stage_keeps_modes_links_and_refuses_escape(sandbox):
    os.makedirs(U.WORK)
    z = os.path.join(U.WORK, 'a.zip')
    _make_release_zip(z, '1.2.0')
    folder = U.stage(z, '1.2.0')
    exe = os.path.join(folder, U.program_items('linux')[0])
    assert os.access(exe, os.X_OK)
    assert os.readlink(os.path.join(folder, '_internal', 'libz.so')) == \
        'libz.so.1.2'
    _make_release_zip(z, '1.2.0')
    with pytest.raises(U.UpdateError, match='version 1.2.0, not 1.3.0'):
        U.stage(z, '1.3.0')
    _make_release_zip(z, '1.2.0', evil='../../escape.txt')
    with pytest.raises(U.UpdateError, match='unsafe'):
        U.stage(z, '1.2.0')
    assert not os.path.exists(os.path.join(str(sandbox), '..', 'escape.txt'))


def _old_install(root):
    exe = os.path.join(root, '7th Heaven NX')
    with open(exe, 'w') as f:
        f.write('#!/bin/sh\necho old > "%s/launched"\n' % root)
    os.chmod(exe, 0o755)
    os.makedirs(os.path.join(root, '_internal'))
    open(os.path.join(root, '_internal', 'old'), 'w').close()
    for d in ('mods', 'dump', 'sdout', 'cache'):
        os.makedirs(os.path.join(root, d))
    open(os.path.join(root, 'mods', 'm.iro'), 'w').write('mod')
    open(os.path.join(root, 'settings.json'), 'w').write('{"k": 1}')


def _new_copy(root):
    new = os.path.join(root, '.nx_update', 'new', '7th Heaven NX')
    os.makedirs(os.path.join(new, '_internal'))
    open(os.path.join(new, '_internal', 'new'), 'w').close()
    exe = os.path.join(new, '7th Heaven NX')
    with open(exe, 'w') as f:
        f.write('#!/bin/sh\necho new > "%s/launched"\n' % root)
    os.chmod(exe, 0o755)
    open(os.path.join(new, 'LICENSE'), 'w').close()
    return new


@pytest.mark.skipif(os.name != 'posix' or sys.platform == 'darwin',
                    reason='the sh installer, Linux layout')
@pytest.mark.parametrize('broken', [False, True])
def test_installer_replaces_only_the_program(sandbox, broken):
    root = str(sandbox)
    _old_install(root)
    new = _new_copy(root)
    p = subprocess.Popen(['true'])
    p.wait()
    argv = U.write_script(new, '1.2.0', pid=p.pid, system='linux')
    if broken:
        import shutil
        shutil.rmtree(os.path.join(new, '_internal'))
    subprocess.run(argv, timeout=60)
    for _ in range(50):
        if os.path.exists(os.path.join(root, 'launched')):
            break
        time.sleep(0.1)
    launched = open(os.path.join(root, 'launched')).read().strip()
    inside = os.listdir(os.path.join(root, '_internal'))
    if broken:
        assert launched == 'old' and inside == ['old']
        assert U.last_result('1.1.0')[0] == 'failed'
    else:
        assert launched == 'new' and inside == ['new']
        assert U.last_result('1.2.0') == ('ok', '1.2.0')
        assert not os.path.exists(U.WORK)
    assert open(os.path.join(root, 'settings.json')).read() == '{"k": 1}'
    assert os.listdir(os.path.join(root, 'mods')) == ['m.iro']
    for d in ('dump', 'sdout', 'cache'):
        assert os.path.isdir(os.path.join(root, d))


def test_powershell_script_quotes_paths(sandbox, monkeypatch):
    monkeypatch.setattr(nxpaths, 'ROOT', "C:\\Users\\O'Brien\\7th Heaven NX")
    argv = U.write_script("C:\\x\\new\\7th Heaven NX", '1.2.0', pid=42,
                          system='windows')
    text = open(argv[-1], encoding='utf-8-sig').read()
    assert "'C:\\Users\\O''Brien\\7th Heaven NX'" in text
    assert 'Wait-Process -Id 42' in text and "'OK 1.2.0'" in text


# ---------------------------------------------- BUILD 892: source archives

def test_install_mode_from_source(sandbox):
    root = str(sandbox)
    open(os.path.join(root, '7th_heaven_nx.py'), 'w').close()
    assert U.install_mode() == ('source', None)
    os.makedirs(os.path.join(root, '.git'))
    assert U.install_mode()[0] == 'git'


def test_source_kept_paths():
    for rel in ('mods/a.iro', 'settings.json', 'data/ff7_en_switch',
                '.venv/bin/python', 'shaders/stock/x.glsl', 'cache/x'):
        assert U.source_kept(rel), rel
    for rel in ('src/core/build.py', 'data/mod_sources.json',
                'shaders/hd.glsl', '7th_heaven_nx.py', 'settings.json.bak'):
        assert not U.source_kept(rel), rel


def _source_zip(path, version, commit, extra=None):
    with zipfile.ZipFile(path, 'w') as zf:
        top = '7th-Heaven-NX-%s/' % version
        zf.writestr(top + '7th_heaven_nx.py',
                    "VERSION = '%s'\nopen('launched', 'w').write('%s')\n"
                    % (version, version))
        zf.writestr(top + 'src/core/mod.py', 'NEW = 1\n')
        zf.writestr(top + 'data/mod_sources.json', '{}')
        zf.writestr(top + 'settings.json', 'must not be copied')
        for name, data in (extra or {}).items():
            zf.writestr(top + name, data)
        zf.comment = commit.encode()


def test_download_source_checks_the_commit(sandbox, monkeypatch):
    rel = U.Release(_release())
    good, bad = 'a' * 40, 'b' * 40
    z = str(sandbox / 'src.zip')
    _source_zip(z, '1.2.0', good)
    blob = open(z, 'rb').read()

    def fake_open(url, headers=None, timeout=None):
        if 'commits' in url:
            return _Resp(good.encode())
        return _Resp(blob)
    monkeypatch.setattr(U, '_open', fake_open)
    path = U.download_source(rel)
    folder = U.stage_source(path, '1.2.0')
    assert os.path.isfile(os.path.join(folder, 'src', 'core', 'mod.py'))
    _source_zip(z, '1.2.0', bad)
    blob = open(z, 'rb').read()
    with pytest.raises(U.UpdateError, match='not the commit'):
        U.download_source(rel)


def _old_source_tree(root):
    os.makedirs(os.path.join(root, 'src', 'core'))
    open(os.path.join(root, '7th_heaven_nx.py'), 'w').write(
        "VERSION = '1.1.0'\nopen('launched', 'w').write('1.1.0')\n")
    open(os.path.join(root, 'src', 'core', 'mod.py'), 'w').write('EDIT = 1\n')
    open(os.path.join(root, 'src', 'core', 'gone.py'), 'w').write('OLD\n')
    os.makedirs(os.path.join(root, 'mods'))
    open(os.path.join(root, 'mods', 'm.iro'), 'w').write('mod')
    open(os.path.join(root, 'settings.json'), 'w').write('{"k": 1}')


def _stage_new(root, extra=None):
    os.makedirs(U.WORK, exist_ok=True)
    z = os.path.join(U.WORK, 's.zip')
    _source_zip(z, '1.2.0', 'c' * 40, extra)
    return U.stage_source(z, '1.2.0')


@pytest.mark.parametrize('broken', [False, True])
def test_source_installer(sandbox, broken):
    root = str(sandbox)
    _old_source_tree(root)
    folder = _stage_new(root, {'src/core/newmod.py': 'X = 1\n'})
    if broken:
        # a folder where the update wants a file: that copy fails
        os.makedirs(os.path.join(root, 'data', 'mod_sources.json'))
    p = subprocess.Popen([sys.executable, '-c', 'pass'])
    p.wait()
    argv = U.write_source_script(folder, '1.2.0', pid=p.pid, relaunch=True)
    subprocess.run(argv, timeout=60)
    for _ in range(100):
        if os.path.exists(os.path.join(root, 'launched')):
            break
        time.sleep(0.1)
    launched = open(os.path.join(root, 'launched')).read()
    src = open(os.path.join(root, 'src', 'core', 'mod.py')).read()
    if broken:
        assert launched == '1.1.0', 'the old version is started again'
        assert U.last_result('1.1.0')[0] == 'failed'
        assert src == 'EDIT = 1\n'
        assert os.path.exists(os.path.join(root, 'src', 'core', 'gone.py'))
        assert not os.path.exists(os.path.join(root, 'src', 'core',
                                               'newmod.py'))
    else:
        assert launched == '1.2.0'
        assert src == 'NEW = 1\n'
        assert not os.path.exists(os.path.join(root, 'src', 'core', 'gone.py'))
        prev = os.path.join(U.WORK, 'previous', 'src', 'core')
        assert open(os.path.join(prev, 'mod.py')).read() == 'EDIT = 1\n'
        assert os.path.exists(os.path.join(prev, 'gone.py'))
        assert U.last_result('1.2.0') == ('ok', '1.2.0')
        assert os.listdir(U.WORK) == ['previous']
    assert open(os.path.join(root, 'settings.json')).read() == '{"k": 1}'
    assert os.listdir(os.path.join(root, 'mods')) == ['m.iro']
