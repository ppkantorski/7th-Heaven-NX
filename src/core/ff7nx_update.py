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

WHEN IT CANNOT INSTALL
  Run from source (git clone): it says so and offers the release page --
  `git pull` is the update there. A release whose folder is not writable,
  or that was started with SEVENTH_NX_HOME pointing elsewhere: the same.

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
    """('auto', None) when this copy can update itself, otherwise
    ('source' | 'manual', why)."""
    if not nxpaths.FROZEN:
        return ('source', 'This copy runs from the source code. Update it '
                'with `git pull`, or download the release.')
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
    for p in (os.path.join(WORK, 'new'), os.path.join(WORK, 'old')):
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
        _remove(WORK)
    else:
        _remove(RESULT)
        discard()
    return out
