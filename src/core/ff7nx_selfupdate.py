#!/usr/bin/env python3
"""
ff7nx_selfupdate.py -- the update installer that ships INSIDE the program.
BUILD 900.

    <new program> --nx-apply-update <plan.json>

WHY IT EXISTS
=============
A running program cannot replace itself on Windows: its .exe and every DLL
it has loaded are locked until it exits. Something else has to do the swap
after it is gone. Until 1.1.2 that was a script (sh, PowerShell, and from
BUILD 898 a cmd fallback), and a script is exactly what security software
and execution policy stop.

So the NEW copy installs itself. The old program downloads and unpacks the
release into .nx_update/new/, starts the new executable with
--nx-apply-update, waits until this has created .nx_update/started (the
handshake, ff7nx_update.start_installer) and quits. This then:

  1. waits for the old program to exit (refusing to touch anything if it has
     not within WAIT_EXIT seconds);
  2. COPIES each program item from new/ into .nx_update/incoming/ -- copied,
     not moved, because this process is running from new/, and on Windows a
     running program's folder cannot be moved;
  3. for each item, renames the installed one aside into .nx_update/old/ and
     renames the incoming copy into place -- the same disk, so these are
     renames, and the window in which an item is missing is one rename long;
  4. on any failure puts every item back as it was;
  5. writes result.txt, starts the installed program and exits. The next
     launch reports the result and removes .nx_update/ (new/ included).

It is standard library only and imports nothing from the project, so the
program can hand control to it before anything else starts -- no window, no
log file in new/, no module that might be half-replaced. 7th_heaven_nx.py
checks for the flag as its very first statement.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import json
import os
import shutil
import subprocess
import sys
import time

FLAG = '--nx-apply-update'
WAIT_EXIT = 180
MOVE_TRIES = 120                # x 0.5 s: security software may hold a file


def _log(plan, text):
    try:
        with open(plan['log'], 'a', encoding='utf-8') as f:
            f.write('%s [helper] %s\n' % (time.strftime('%H:%M:%S'), text))
    except OSError:
        pass


def _write(path, text):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def _alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x00100000, False, pid)            # SYNCHRONIZE
        if not h:
            return False
        try:
            return k.WaitForSingleObject(h, 0) == 0x102      # WAIT_TIMEOUT
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _remove(path):
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        elif os.path.lexists(path):
            os.remove(path)
    except OSError:
        pass


def _retry(fn, *args):
    """Run fn(*args), retrying while something briefly holds a file."""
    last = None
    for _ in range(MOVE_TRIES):
        try:
            return fn(*args)
        except OSError as exc:
            last = exc
            time.sleep(0.5)
    raise last


def _copy(src, dst):
    if os.path.isdir(src) and not os.path.islink(src):
        # symlinks kept as links: a macOS .app cross-links its Frameworks
        # and Resources, and the code signature covers that layout
        shutil.copytree(src, dst, symlinks=True)
    elif os.path.islink(src):
        os.symlink(os.readlink(src), dst)
    else:
        shutil.copy2(src, dst)


def clean_env(base=None):
    """The environment for the program this starts: PyInstaller's own
    bookkeeping removed (this is itself a frozen program), library paths
    back to the user's, and PYINSTALLER_RESET_ENVIRONMENT=1."""
    env = dict(os.environ if base is None else base)
    for k in list(env):
        if k.startswith('_PYI_') or k in ('_MEIPASS2', '_MEIPASS'):
            del env[k]
    for var in ('LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH', 'DYLD_FRAMEWORK_PATH',
                'LIBPATH'):
        orig = env.pop(var + '_ORIG', None)
        if orig is not None:
            env[var] = orig
        elif getattr(sys, 'frozen', False) and var in env:
            del env[var]
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return env


def _launch(plan):
    """Start the installed program, detached, with a clean environment."""
    argv, root = plan['launch'], plan['root']
    env = clean_env()
    try:
        if plan.get('system') == 'macos':
            r = subprocess.run(['open', argv[0]], env=env,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            if r.returncode == 0:
                _log(plan, 'program started')
                return
            argv = [os.path.join(argv[0], 'Contents', 'MacOS',
                                 plan['name'])]
        kw = dict(cwd=root, env=env, stdin=subprocess.DEVNULL,
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  close_fds=True)
        if os.name == 'nt':
            kw['creationflags'] = 0x00000008 | 0x00000200   # detached, group
        else:
            kw['start_new_session'] = True
        subprocess.Popen(argv, **kw)
        _log(plan, 'program started')
    except Exception as exc:                                # noqa: BLE001
        _log(plan, 'could not start the program: %s' % exc)


def apply(plan):
    """The whole install. Returns 0 on success, 1 on failure."""
    root, new, work = plan['root'], plan['new'], plan['work']
    old = os.path.join(work, 'old')
    incoming = os.path.join(work, 'incoming')
    try:
        _write(plan['started'], 'helper\n')
    except OSError:
        pass
    _log(plan, 'waiting for process %s to exit' % plan['pid'])
    deadline = time.time() + plan.get('wait', WAIT_EXIT)
    while _alive(plan['pid']):
        if time.time() > deadline:
            _log(plan, 'it did not exit; nothing was changed')
            _write(plan['result'], 'FAILED the old program did not close '
                   'within %d seconds, so nothing was changed\n'
                   % plan.get('wait', WAIT_EXIT))
            return 1
        time.sleep(0.25)
    time.sleep(0.5)

    swapped = []                                   # items already in place
    try:
        for d in (old, incoming):
            _remove(d)
            os.makedirs(d)
        # 1. everything copied first: a failure here has changed nothing
        for item in plan['items']:
            _copy(os.path.join(new, item), os.path.join(incoming, item))
        _log(plan, 'copied %d item(s)' % len(plan['items']))
        # 2. then the swap, one rename pair per item
        for item in plan['items']:
            dst = os.path.join(root, item)
            if os.path.lexists(dst):
                _retry(os.rename, dst, os.path.join(old, item))
            swapped.append(item)
            _retry(os.rename, os.path.join(incoming, item), dst)
            _log(plan, 'installed %s' % item)
    except Exception as exc:                                # noqa: BLE001
        _log(plan, 'failed: %s' % exc)
        lost = []
        for item in reversed(swapped):
            dst = os.path.join(root, item)
            try:
                if os.path.lexists(os.path.join(old, item)):
                    if os.path.lexists(dst):
                        _remove(dst)
                    # retried like the install: what blocked the swap may
                    # still be holding the file for a moment
                    _retry(os.rename, os.path.join(old, item), dst)
            except OSError as exc2:
                lost.append(item)
                _log(plan, 'could not restore %s: %s' % (item, exc2))
        if lost:
            exc = OSError('%s; and %s could not be put back -- the previous '
                          'copy is in %s' % (exc, ', '.join(lost), old))
        _remove(incoming)
        try:
            _write(plan['result'], 'FAILED %s\n' % exc)
        except OSError:
            pass
        _launch(plan)
        return 1
    _write(plan['result'], 'OK %s\n' % plan['version'])
    _launch(plan)
    _remove(old)
    _remove(incoming)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if FLAG not in argv or argv.index(FLAG) + 1 >= len(argv):
        return 2
    with open(argv[argv.index(FLAG) + 1], encoding='utf-8') as f:
        plan = json.load(f)
    return apply(plan)


if __name__ == '__main__':
    sys.exit(main())
