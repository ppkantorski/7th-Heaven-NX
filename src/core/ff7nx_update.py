#!/usr/bin/env python3
"""ff7nx_update.py -- check for, download and install new releases of
7th Heaven NX itself. BUILD 890.

WHERE RELEASES COME FROM
  GitHub's `releases/latest` for ppkantorski/7th-Heaven-NX: the newest
  release that is neither a draft nor a pre-release. Each asset is
  `7th-Heaven-NX-<version>-<os>-<arch>.zip` (tools/release.py) and GitHub
  publishes its SHA-256 as the asset's `digest`.

HOW OFTEN
  At start-up and then every CHECK_INTERVAL (6 h) while the window is open.
  Each check is a CONDITIONAL request: the last ETag goes back as
  If-None-Match, and an unchanged release answers 304 with no body, which
  GitHub does not count against the 60-an-hour anonymous limit. A start-up
  within RECHECK_AFTER (1 h) of the last check uses the saved answer and
  sends nothing. The manual Check for Updates button always asks.

WHICH FILE
  The one for THIS program: the same <os> and <arch> as the running build
  (an x64 build under Rosetta or Windows-on-Arm emulation reports x64, so it
  gets x64 -- exactly what the user is running).

WHAT IS CHECKED BEFORE ANYTHING IS REPLACED
  * the download's size and SHA-256 against the release's own values;
  * no member of the zip may point outside the staging folder (zip-slip);
  * the extracted folder must hold the same program items as this one, and
    its bundled 7th_heaven_nx.py must say VERSION = the release's version.

HOW IT IS INSTALLED
  A running program cannot replace its own files on Windows, and should not
  anywhere. So the new copy is staged in <ROOT>/.nx_update/ (the same disk,
  so every move is a rename) and a small script -- sh on macOS/Linux,
  PowerShell on Windows -- is started detached. It waits for this process to
  exit, moves each program item (the .app, or the exe and _internal/, plus
  README.md and LICENSE) aside into .nx_update/old/, moves the new one in,
  and starts the new program. If any move fails, everything already moved
  is put back and the old program is started. The outcome is written to
  .nx_update/result.txt, which the next launch reports.

  Only program items move. mods/, dump/, cache/, sdout/, modules/ and
  settings.json are never touched.

  macOS: a file downloaded by this program carries no quarantine flag, so
  the updated app opens without the Gatekeeper prompt a browser download
  gets. A translocated app is replaced at its ORIGINAL location (nxpaths).

RUN FROM SOURCE (BUILD 892)
  A folder unpacked from a release's "Source code (zip)" updates from that
  same archive, github.com/<repo>/archive/refs/tags/<tag>.zip. GitHub
  publishes no checksum for it, but it writes the commit the archive was
  made from into the zip's comment; that must equal the commit the API says
  the tag points to, and the archive's 7th_heaven_nx.py must say the
  release's VERSION. The new files are copied over the program's own; local
  data (mods/, dump/, cache/, sdout/, settings.json, .venv/, attic/,
  data/ff7_en_switch ... -- the repository's .gitignore) is never written,
  and .py files the new version no longer has are removed from src/ only
  (a stale module there could shadow a moved one). Every file replaced or
  removed is kept in .nx_update/previous/ until the next update, so a local
  edit is never lost. The copy runs in a standalone Python script after the
  program has exited, with the same interpreter, which then restarts it.
  A git clone (.git/) is left to `git pull`.

WHEN IT CANNOT INSTALL
  A git clone: it says so and offers the release page. A release whose
  folder is not writable, or that was started with SEVENTH_NX_HOME pointing
  elsewhere: the same.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import zipfile

import nxpaths

REPO = 'ppkantorski/7th-Heaven-NX'
API_LATEST = 'https://api.github.com/repos/%s/releases/latest' % REPO
RELEASES_URL = 'https://github.com/%s/releases' % REPO
NAME = '7th Heaven NX'
CHECK_INTERVAL = 6 * 3600       # while the window stays open
RECHECK_AFTER = 3600            # a start-up this soon after a check: no request
TIMEOUT = 15

STATE_DIR = os.path.join(nxpaths.ROOT, 'cache', '_update')
STATE_FILE = os.path.join(STATE_DIR, 'state.json')
WORK = os.path.join(nxpaths.ROOT, '.nx_update')
RESULT = os.path.join(WORK, 'result.txt')


class UpdateError(Exception):
    pass


class Cancelled(UpdateError):
    pass


# ---------------------------------------------------------------- versions

def parse_version(text):
    """'v1.1.0' / '1.1.0' -> (1, 1, 0); None when it is not dotted numbers."""
    m = re.fullmatch(r'\s*v?(\d+(?:\.\d+)*)\s*', text or '')
    if not m:
        return None
    parts = tuple(int(p) for p in m.group(1).split('.'))
    return parts + (0,) * (3 - len(parts)) if len(parts) < 3 else parts


def is_newer(latest, current):
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


def target():
    """('windows' | 'macos' | 'linux', 'x64' | 'arm64') -- the same mapping
    tools/release.py names its zips with."""
    system = {'win32': 'windows', 'darwin': 'macos'}.get(
        sys.platform, 'linux' if sys.platform.startswith('linux')
        else sys.platform)
    machine = platform.machine().lower()
    arch = {'x86_64': 'x64', 'amd64': 'x64', 'arm64': 'arm64',
            'aarch64': 'arm64'}.get(machine, machine)
    return system, arch


def asset_name(version, system=None, arch=None):
    s, a = target()
    return '7th-Heaven-NX-%s-%s-%s.zip' % (version, system or s, arch or a)


# ------------------------------------------------------------------ state

def _load_state():
    try:
        with open(STATE_FILE, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(st):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = STATE_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(st, f, indent=1)
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


class Release(object):
    def __init__(self, data):
        self.tag = data.get('tag_name') or ''
        self.version = (self.tag[1:] if self.tag[:1] in 'vV' else self.tag)
        self.name = data.get('name') or self.tag
        self.notes = (data.get('body') or '').replace('\r\n', '\n')
        self.url = data.get('html_url') or RELEASES_URL
        self.published = data.get('published_at') or ''
        self.assets = {}
        for a in data.get('assets') or ():
            digest = a.get('digest') or ''
            self.assets[a.get('name')] = {
                'name': a.get('name'), 'size': int(a.get('size') or 0),
                'url': a.get('browser_download_url'),
                'sha256': digest[7:].lower()
                if digest.lower().startswith('sha256:') else None}

    def asset(self):
        """This program's own zip in the release, or None."""
        return self.assets.get(asset_name(self.version))


def _slim(data):
    """The part of the API answer worth keeping between runs."""
    keep = ('tag_name', 'name', 'body', 'html_url', 'published_at')
    out = {k: data.get(k) for k in keep}
    out['assets'] = [{k: a.get(k) for k in
                      ('name', 'size', 'digest', 'browser_download_url')}
                     for a in data.get('assets') or ()]
    return out


def _open(url, headers=None, timeout=TIMEOUT):
    import ff7nx_download
    return ff7nx_download._open(url, headers=headers, timeout=timeout)


def check(current, force=False, version_for_agent=None):
    """The latest release if it is newer than `current`, else None.

    force=False (the automatic check) answers from the saved state when the
    last check was under RECHECK_AFTER ago. Raises UpdateError when GitHub
    cannot be reached."""
    st = _load_state()
    now = time.time()
    data = st.get('release')
    fresh = data and now - st.get('checked_at', 0) < RECHECK_AFTER
    if force or not fresh:
        headers = {'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28',
                   'User-Agent': '7th-Heaven-NX/%s' % (version_for_agent
                                                      or current)}
        if data and st.get('etag'):
            headers['If-None-Match'] = st['etag']
        try:
            resp = _open(API_LATEST, headers=headers)
            with resp:
                body = resp.read()
                etag = resp.headers.get('ETag')
            data = _slim(json.loads(body.decode('utf-8')))
            st['etag'] = etag
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and data:
                pass                                # unchanged since last time
            elif exc.code == 404:
                raise UpdateError('no release has been published yet')
            elif exc.code in (403, 429):
                raise UpdateError('GitHub is limiting requests from this '
                                  'network for now; try again later')
            else:
                raise UpdateError('GitHub answered HTTP %d' % exc.code)
        except UpdateError:
            raise
        except Exception as exc:                               # noqa: BLE001
            raise UpdateError('could not reach GitHub (%s)'
                              % (getattr(exc, 'reason', None) or exc))
        st['release'] = data
        st['checked_at'] = now
        _save_state(st)
    rel = Release(data or {})
    return rel if is_newer(rel.version, current) else None


# ------------------------------------------------------------- the install

def program_items(system=None):
    """The top-level names a release puts in the user's folder that belong
    to the PROGRAM (replaced by an update), in the order they are moved."""
    system = system or target()[0]
    if system == 'macos':
        main = [NAME + '.app']
    elif system == 'windows':
        main = [NAME + '.exe', '_internal']
    else:
        main = [NAME, '_internal']
    return main + ['README.md', 'LICENSE']


def _required_items(system=None):
    return program_items(system)[:-2]


def install_mode():
    """How this copy updates: ('auto', None) for a release build, ('source',
    None) for an unpacked source archive, otherwise ('git' | 'manual', why)
    -- those cannot update themselves."""
    if not nxpaths.FROZEN:
        root = nxpaths.ROOT
        if os.path.exists(os.path.join(root, '.git')):
            return ('git', 'This copy is a git clone. Update it with '
                    '`git pull`.')
        if not os.path.isfile(os.path.join(root, '7th_heaven_nx.py')):
            return ('manual', '7th_heaven_nx.py is not in %s.' % root)
        if not os.access(root, os.W_OK):
            return ('manual', '%s cannot be written to.' % root)
        return ('source', None)
    if os.environ.get('SEVENTH_NX_HOME'):
        return ('manual', 'SEVENTH_NX_HOME puts your folder somewhere other '
                'than next to the program, so the program cannot be found '
                'to replace it.')
    root = nxpaths.ROOT
    for item in _required_items():
        if not os.path.exists(os.path.join(root, item)):
            return ('manual', 'The program is not where a release puts it '
                    '(%s is missing from %s).' % (item, root))
    if sys.platform != 'darwin':
        try:
            if not os.path.samefile(sys.executable,
                                    os.path.join(root, _required_items()[0])):
                return ('manual', 'The running program is not the one in '
                        '%s.' % root)
        except OSError:
            return ('manual', 'The running program could not be located.')
    if not os.access(root, os.W_OK):
        return ('manual', '%s cannot be written to.' % root)
    return ('auto', None)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def download(asset, progress=lambda done, total: None, cancelled=lambda: False):
    """Download and verify this program's zip into WORK. Returns its path."""
    if not asset or not asset.get('url'):
        raise UpdateError('this release has no download for %s %s'
                          % target())
    if not asset.get('sha256'):
        raise UpdateError('the release does not publish a checksum for %s, '
                          'so it cannot be verified' % asset['name'])
    os.makedirs(WORK, exist_ok=True)
    dest = os.path.join(WORK, asset['name'])
    part = dest + '.part'
    last = None
    for attempt in range(3):
        if cancelled():
            raise Cancelled('cancelled')
        h = hashlib.sha256()
        done = 0
        try:
            resp = _open(asset['url'], timeout=60)
            with resp, open(part, 'wb') as f:
                total = int(resp.headers.get('Content-Length') or
                            asset['size'] or 0)
                while True:
                    if cancelled():
                        raise Cancelled('cancelled')
                    block = resp.read(1 << 18)
                    if not block:
                        break
                    f.write(block)
                    h.update(block)
                    done += len(block)
                    progress(done, total)
            break
        except Cancelled:
            _remove(part)
            raise
        except Exception as exc:                               # noqa: BLE001
            last = exc
            time.sleep(2 * (attempt + 1))
    else:
        _remove(part)
        raise UpdateError('the download failed (%s)'
                          % (getattr(last, 'reason', None) or last))
    if asset['size'] and done != asset['size']:
        _remove(part)
        raise UpdateError('the download is %d bytes, the release says %d'
                          % (done, asset['size']))
    if h.hexdigest() != asset['sha256']:
        _remove(part)
        raise UpdateError('the download does not match the release\'s '
                          'SHA-256 checksum, so it was thrown away')
    os.replace(part, dest)
    return dest


def _remove(path):
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        elif os.path.lexists(path):
            os.remove(path)
    except OSError:
        pass


def _extract_zip(zip_path, dest):
    """zipfile extraction that refuses paths leaving `dest` and keeps the
    unix permissions and symlinks tools/release.py writes."""
    base = os.path.realpath(dest)
    with zipfile.ZipFile(zip_path) as zf:
        for zi in zf.infolist():
            name = zi.filename.replace('\\', '/')
            target_path = os.path.realpath(os.path.join(dest, name))
            if not (target_path == base or
                    target_path.startswith(base + os.sep)) or \
                    name.startswith('/') or '..' in name.split('/'):
                raise UpdateError('the zip holds an unsafe path: %s' % name)
            mode = zi.external_attr >> 16
            if name.endswith('/'):
                os.makedirs(target_path, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            if stat.S_ISLNK(mode) and os.name == 'posix':
                link = zf.read(zi).decode('utf-8')
                if os.path.isabs(link) or not os.path.realpath(
                        os.path.join(os.path.dirname(target_path), link)
                        ).startswith(base + os.sep):
                    raise UpdateError('the zip holds an unsafe link: %s'
                                      % name)
                os.symlink(link, target_path)
                continue
            with zf.open(zi) as src, open(target_path, 'wb') as out:
                shutil.copyfileobj(src, out, 1 << 20)
            if os.name == 'posix' and mode & 0o777:
                os.chmod(target_path, mode & 0o777)


def _bundled_version(folder, system):
    """VERSION from the staged program's own 7th_heaven_nx.py, or None."""
    if system == 'macos':
        roots = [os.path.join(folder, NAME + '.app', 'Contents')]
    else:
        roots = [os.path.join(folder, '_internal')]
    for root in roots:
        for d, _subs, files in os.walk(root):
            if '7th_heaven_nx.py' in files:
                try:
                    with open(os.path.join(d, '7th_heaven_nx.py'),
                              encoding='utf-8', errors='replace') as f:
                        m = re.search(r"^VERSION = '([^']+)'", f.read(), re.M)
                    return m.group(1) if m else None
                except OSError:
                    return None
    return None


def stage(zip_path, version):
    """Extract the verified zip into WORK/new and check it. Returns the
    folder that holds the new program items."""
    system = target()[0]
    new = os.path.join(WORK, 'new')
    _remove(new)
    os.makedirs(new)
    if system == 'macos':
        # ditto keeps the .app's symlinks, permissions and signature, as the
        # release was made with it (tools/release.py _zip)
        r = subprocess.run(['ditto', '-x', '-k', zip_path, new],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode:
            raise UpdateError('the zip could not be unpacked: %s'
                              % r.stdout.decode(errors='replace').strip())
    else:
        _extract_zip(zip_path, new)
    folder = os.path.join(new, NAME)
    if not os.path.isdir(folder):
        raise UpdateError('the zip does not hold a "%s" folder' % NAME)
    for item in _required_items(system):
        if not os.path.exists(os.path.join(folder, item)):
            raise UpdateError('the new copy is missing %s' % item)
    found = _bundled_version(folder, system)
    if found is not None and found != version:
        raise UpdateError('the download says it is version %s, not %s'
                          % (found, version))
    if system == 'macos':
        subprocess.run(['xattr', '-dr', 'com.apple.quarantine',
                        os.path.join(folder, NAME + '.app')],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _remove(zip_path)
    return folder


def _sh_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def _ps_quote(s):
    return "'" + s.replace("'", "''") + "'"


def write_script(staged, version, pid=None, system=None):
    """The detached installer for this platform. Returns its argv."""
    system = system or target()[0]
    pid = pid or os.getpid()
    root = nxpaths.ROOT
    os.makedirs(WORK, exist_ok=True)
    old = os.path.join(WORK, 'old')
    items = [i for i in program_items(system)
             if os.path.exists(os.path.join(staged, i))]
    if system == 'windows':
        script = os.path.join(WORK, 'apply.ps1')
        body = '''$ErrorActionPreference = 'Stop'
$root = %(root)s; $new = %(new)s; $old = %(old)s; $result = %(result)s
$items = @(%(items)s)
try { Wait-Process -Id %(pid)d -Timeout 180 -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Milliseconds 700
function Move-Retry($src, $dst) {
  for ($i = 0; $i -lt 20; $i++) {
    try { Move-Item -LiteralPath $src -Destination $dst; return }
    catch { Start-Sleep -Milliseconds 500 }
  }
  Move-Item -LiteralPath $src -Destination $dst
}
$moved = @(); $ok = $true; $why = ''
try {
  if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force }
  New-Item -ItemType Directory -Path $old | Out-Null
  foreach ($i in $items) {
    $dst = Join-Path $root $i
    if (Test-Path -LiteralPath $dst) { Move-Retry $dst (Join-Path $old $i) }
    $moved += $i
    Move-Retry (Join-Path $new $i) $dst
  }
} catch {
  $ok = $false; $why = $_.Exception.Message
  foreach ($i in $moved) {
    $dst = Join-Path $root $i
    try {
      if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Recurse -Force }
      if (Test-Path -LiteralPath (Join-Path $old $i)) { Move-Item -LiteralPath (Join-Path $old $i) -Destination $dst }
    } catch {}
  }
}
if ($ok) { Set-Content -LiteralPath $result -Value 'OK %(version)s' }
else { Set-Content -LiteralPath $result -Value ('FAILED ' + $why) }
Start-Process -FilePath (Join-Path $root %(exe)s) -WorkingDirectory $root
if ($ok) {
  Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $new -Recurse -Force -ErrorAction SilentlyContinue
}
''' % {'root': _ps_quote(root), 'new': _ps_quote(staged),
       'old': _ps_quote(old), 'result': _ps_quote(RESULT),
       'items': ', '.join(_ps_quote(i) for i in items), 'pid': pid,
       'version': version.replace("'", ''), 'exe': _ps_quote(NAME + '.exe')}
        with open(script, 'w', encoding='utf-8-sig') as f:
            f.write(body)
        return ['powershell', '-NoProfile', '-NonInteractive',
                '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
                '-File', script]
    script = os.path.join(WORK, 'apply.sh')
    if system == 'macos':
        launch = 'open %s' % _sh_quote(os.path.join(root, NAME + '.app'))
    else:
        launch = ('(cd %s && nohup %s >/dev/null 2>&1 &)'
                  % (_sh_quote(root), _sh_quote(os.path.join(root, NAME))))
    body = '''#!/bin/sh
ROOT=%(root)s
NEW=%(new)s
OLD=%(old)s
RESULT=%(result)s
n=0
while kill -0 %(pid)d 2>/dev/null; do
  sleep 0.5; n=$((n+1)); [ $n -gt 360 ] && break
done
sleep 0.5
rm -rf "$OLD"; mkdir -p "$OLD"
MOVED=""
fail() {
  for i in $MOVED; do
    i=$(printf '%%s' "$i" | tr '\\001' ' ')
    rm -rf "$ROOT/$i"
    [ -e "$OLD/$i" ] && mv "$OLD/$i" "$ROOT/$i"
  done
  printf 'FAILED %%s\\n' "$1" > "$RESULT"
  %(launch)s
  exit 1
}
for i in %(items)s; do
  if [ -e "$ROOT/$i" ] || [ -L "$ROOT/$i" ]; then
    mv "$ROOT/$i" "$OLD/$i" || fail "could not move $i aside"
  fi
  MOVED="$MOVED $(printf '%%s' "$i" | tr ' ' '\\001')"
  mv "$NEW/$i" "$ROOT/$i" || fail "could not move the new $i in"
done
printf 'OK %(version)s\\n' > "$RESULT"
%(launch)s
rm -rf "$OLD" "$NEW"
''' % {'root': _sh_quote(root), 'new': _sh_quote(staged),
       'old': _sh_quote(old), 'result': _sh_quote(RESULT), 'pid': pid,
       'items': ' '.join(_sh_quote(i) for i in items),
       'version': re.sub(r'[^0-9A-Za-z.\-]', '', version), 'launch': launch}
    with open(script, 'w', encoding='utf-8') as f:
        f.write(body)
    os.chmod(script, 0o755)
    return ['/bin/sh', script]


def launch_installer(argv):
    """Start the installer detached; the caller then quits the program."""
    if os.path.exists(RESULT):
        _remove(RESULT)
    if sys.platform == 'win32':
        flags = (getattr(subprocess, 'DETACHED_PROCESS', 0x8)
                 | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x200)
                 | getattr(subprocess, 'CREATE_NO_WINDOW', 0x8000000))
        subprocess.Popen(argv, creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(argv, start_new_session=True, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)


def discard():
    """Throw away a staged update that was not installed."""
    for p in (os.path.join(WORK, 'new'), os.path.join(WORK, 'old'),
              os.path.join(WORK, 'new_src')):
        _remove(p)
    try:
        for n in os.listdir(WORK):
            if n.endswith(('.zip', '.part')):
                _remove(os.path.join(WORK, n))
    except OSError:
        pass


def last_result(current):
    """At start-up: ('ok' | 'failed', text) for an update the previous run
    installed, or None. The leftovers of a finished update are removed."""
    if not os.path.isdir(WORK):
        return None
    out = None
    try:
        with open(RESULT, encoding='utf-8', errors='replace') as f:
            line = f.read().strip()
    except OSError:
        line = ''
    if line.startswith('OK '):
        out = ('ok', line[3:].strip())
    elif line.startswith('FAILED'):
        out = ('failed', line[6:].strip() or 'unknown error')
    # an installer may still be cleaning up for a second or two; whatever it
    # leaves is removed by the next launch instead
    if out is None or out[0] == 'ok':
        # everything but previous/ (a source update's replaced files, kept
        # until the next update)
        try:
            for n in os.listdir(WORK):
                if n != 'previous':
                    _remove(os.path.join(WORK, n))
        except OSError:
            pass
        try:
            os.rmdir(WORK)          # only when nothing (no previous/) is left
        except OSError:
            pass
    else:
        _remove(RESULT)
        discard()
    return out


# ------------------------------------------------- source archives (892)

ARCHIVE_URL = 'https://github.com/%s/archive/refs/tags/%%s.zip' % REPO
COMMIT_API = 'https://api.github.com/repos/%s/commits/%%s' % REPO

# Never written by a source update: the user's data and per-machine files
# (the repository's own .gitignore says the same).
SOURCE_KEEP = ('dump/', 'mods/', 'cache/', 'sdout/', 'sdout_changes/',
               'game_data_files/', 'repos/', 'attic/', 'release/',
               'modules/', 'ffmpeg/', '.venv/', '.nx_update/', '.git/',
               'shaders/stock/', 'settings.json', 'sdout_changes.txt',
               '7th_heaven_nx.log', 'data/ff7_en_switch',
               'data/build_counters.json', '.deps_skipped')
# Folders whose .py files are removed when the new version no longer has
# them (src/ is on sys.path module by module: a leftover could shadow a
# module that moved).
SOURCE_PRUNE = ('src/',)


def source_kept(rel):
    rel = rel.replace('\\', '/')
    return any(rel == k.rstrip('/') or (k.endswith('/') and
                                        rel.startswith(k))
               for k in SOURCE_KEEP)


def tag_commit(tag):
    """The commit SHA `tag` points to, from the GitHub API."""
    try:
        resp = _open(COMMIT_API % tag, headers={
            'Accept': 'application/vnd.github.sha',
            'User-Agent': '7th-Heaven-NX'})
        with resp:
            sha = resp.read().decode('ascii', 'replace').strip()
    except Exception as exc:                                   # noqa: BLE001
        raise UpdateError('could not ask GitHub which commit %s is (%s)'
                          % (tag, getattr(exc, 'reason', None) or exc))
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise UpdateError('GitHub did not name the commit of %s' % tag)
    return sha


def download_source(rel, progress=lambda done, total: None,
                    cancelled=lambda: False):
    """Download the release's source archive into WORK and check that it is
    the tagged commit. Returns its path."""
    sha = tag_commit(rel.tag)
    os.makedirs(WORK, exist_ok=True)
    dest = os.path.join(WORK, 'source-%s.zip' % rel.version)
    part = dest + '.part'
    last = None
    for attempt in range(3):
        if cancelled():
            raise Cancelled('cancelled')
        done = 0
        try:
            resp = _open(ARCHIVE_URL % rel.tag, timeout=60)
            with resp, open(part, 'wb') as f:
                total = int(resp.headers.get('Content-Length') or 0)
                while True:
                    if cancelled():
                        raise Cancelled('cancelled')
                    block = resp.read(1 << 18)
                    if not block:
                        break
                    f.write(block)
                    done += len(block)
                    progress(done, total)
            break
        except Cancelled:
            _remove(part)
            raise
        except Exception as exc:                               # noqa: BLE001
            last = exc
            time.sleep(2 * (attempt + 1))
    else:
        _remove(part)
        raise UpdateError('the download failed (%s)'
                          % (getattr(last, 'reason', None) or last))
    try:
        with zipfile.ZipFile(part) as zf:
            comment = zf.comment.decode('ascii', 'replace').strip()
    except zipfile.BadZipFile:
        _remove(part)
        raise UpdateError('the download is not a zip archive')
    if comment != sha:
        _remove(part)
        raise UpdateError('the archive is not the commit %s points to, so '
                          'it was thrown away' % rel.tag)
    os.replace(part, dest)
    return dest


def stage_source(zip_path, version):
    """Extract a verified source archive into WORK/new_src and check it.
    Returns the folder holding the new tree."""
    new = os.path.join(WORK, 'new_src')
    _remove(new)
    os.makedirs(new)
    _extract_zip(zip_path, new)
    tops = [d for d in os.listdir(new)
            if os.path.isdir(os.path.join(new, d))]
    if len(tops) != 1:
        raise UpdateError('the archive does not hold one top folder')
    folder = os.path.join(new, tops[0])
    entry = os.path.join(folder, '7th_heaven_nx.py')
    if not os.path.isfile(entry) or not os.path.isdir(
            os.path.join(folder, 'src')):
        raise UpdateError('the archive is not a 7th Heaven NX source tree')
    with open(entry, encoding='utf-8', errors='replace') as f:
        m = re.search(r"^VERSION = '([^']+)'", f.read(), re.M)
    if not m or m.group(1) != version:
        raise UpdateError('the archive says it is version %s, not %s'
                          % (m.group(1) if m else '?', version))
    _remove(zip_path)
    return folder


def source_plan(folder, root=None):
    """(files to copy [rel], stale .py files to remove [rel]) for putting the
    tree at `folder` over `root`."""
    root = root or nxpaths.ROOT
    copy = []
    for d, subs, files in os.walk(folder):
        subs[:] = [s for s in subs if s != '__pycache__']
        for f in files:
            rel = os.path.relpath(os.path.join(d, f), folder).replace(
                os.sep, '/')
            if not source_kept(rel):
                copy.append(rel)
    new = set(copy)
    stale = []
    for top in SOURCE_PRUNE:
        base = os.path.join(root, top)
        for d, subs, files in os.walk(base):
            subs[:] = [s for s in subs if s != '__pycache__']
            for f in files:
                if not f.endswith('.py'):
                    continue
                rel = os.path.relpath(os.path.join(d, f), root).replace(
                    os.sep, '/')
                if rel not in new:
                    stale.append(rel)
    return sorted(copy), sorted(stale)


# The installer for a source update: standalone (no project imports), run by
# the same interpreter as the program once the program has exited.
_SOURCE_APPLY = r"""
import json, os, shutil, subprocess, sys, time
plan = json.load(open(sys.argv[1], encoding='utf-8'))
root, new, prev, result = plan['root'], plan['new'], plan['prev'], plan['result']

def alive(pid):
    if os.name == 'nt':
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x00100000, False, pid)        # SYNCHRONIZE
        if not h:
            return False
        try:
            return k.WaitForSingleObject(h, 0) == 0x102  # WAIT_TIMEOUT
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True

for _ in range(720):
    if not alive(plan['pid']):
        break
    time.sleep(0.25)
time.sleep(0.5)

def write(path, text):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)

backed, added = [], []
try:
    if os.path.isdir(prev):
        shutil.rmtree(prev)
    os.makedirs(prev)
    for rel in plan['remove']:
        src = os.path.join(root, rel)
        if os.path.isfile(src):
            dst = os.path.join(prev, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            backed.append(rel)
    for rel in plan['copy']:
        dst = os.path.join(root, rel)
        if os.path.isfile(dst):
            keep = os.path.join(prev, rel)
            os.makedirs(os.path.dirname(keep), exist_ok=True)
            shutil.copy2(dst, keep)
            backed.append(rel)
        else:
            added.append(rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + '.nxupd'
        shutil.copy2(os.path.join(new, rel), tmp)
        os.replace(tmp, dst)
    write(result, 'OK %s\n' % plan['version'])
    ok = True
except Exception as exc:
    ok = False
    for rel in added:
        try:
            os.remove(os.path.join(root, rel))
        except OSError:
            pass
    for rel in backed:
        try:
            dst = os.path.join(root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(prev, rel), dst)
        except OSError:
            pass
    write(result, 'FAILED %s\n' % exc)
if plan.get('relaunch', True):
    kw = {'cwd': root, 'stdin': subprocess.DEVNULL,
          'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
    if os.name == 'nt':
        kw['creationflags'] = 0x00000008 | 0x00000200     # detached
    else:
        kw['start_new_session'] = True
    subprocess.Popen([plan['python'], os.path.join(root, '7th_heaven_nx.py')]
                     + plan.get('args', []), **kw)
if ok:
    shutil.rmtree(os.path.dirname(new), ignore_errors=True)
"""


def write_source_script(folder, version, pid=None, python=None,
                        relaunch=None):
    """The detached installer for a source update. Returns its argv."""
    root = nxpaths.ROOT
    os.makedirs(WORK, exist_ok=True)
    copy, stale = source_plan(folder, root)
    if relaunch is None:
        relaunch = not os.environ.get('SEVENTH_NX_UPDATE_NO_RELAUNCH')
    python = python or sys.executable
    plan = {'root': root, 'new': folder, 'prev': os.path.join(WORK, 'previous'),
            'result': RESULT, 'version': version, 'pid': pid or os.getpid(),
            'copy': copy, 'remove': stale, 'python': python,
            'relaunch': relaunch}
    plan_path = os.path.join(WORK, 'apply_source.json')
    with open(plan_path, 'w', encoding='utf-8') as f:
        json.dump(plan, f)
    script = os.path.join(WORK, 'apply_source.py')
    with open(script, 'w', encoding='utf-8') as f:
        f.write(_SOURCE_APPLY)
    return [python, '-I', script, plan_path]
