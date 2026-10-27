#!/usr/bin/env python3
"""Download, install and update mods from 7th Heaven's own catalogs.

7th Heaven PC subscribes to two catalog XMLs (AppCore/Settings.cs) and
installs from the links in them (AppCore/Catalog.cs, Install.cs). This does
the same for the mods the Switch build supports, which are listed in
data/mod_sources.json: each by its catalog ID, with the exact name it has in
mods/. That name matters. settings.json keys every mod's on/off state and
options by it, so an update must land on the same file (or folder) and a
fresh download must land on the name the shipped settings.json expects.

LINKS
=====
`iros://<Kind>/<url>`, with `$` standing for `://` (LocationUtil.TryParse).
Url is a plain download. GDrive is a file id: small files come straight
back, large ones first return Google's "can't scan for viruses" page, whose
form carries the confirm token (FileDownloadTask.cs does the same). A mod
can list several links; they are tried in order. MegaSharedFolder and
ExternalUrl need a browser and are reported, not attempted.

INSTALL
=======
As InstallModProcedure: a download that starts with the IRO signature is
the mod. Anything else is an archive (.7z, .zip): an .iro inside it is the
mod, otherwise the archive holds a mod FOLDER (the one with mod.xml), which
is moved into mods/ under its expected name. Nothing in mods/ is touched
until the new copy is complete and checked; the old one is then swapped out
in one rename.

7z needs one of: the py7zr package, a `7zz`/`7z` command, or bsdtar (the
`tar` macOS ships reads 7z). zip uses the standard library.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import decimal
import html.parser
import http.client
import socket
import threading
import time
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

import iro
import nxpaths

SUBSCRIPTIONS = (
    ('Qhimm Catalog', 'https://github.com/tsunamods-codes/7th-Heaven-Catalogs/'
                      'releases/download/canary/qhimm.xml'),
    ('Sega Catalog', 'https://github.com/tsunamods-codes/7th-Heaven-Catalogs/'
                     'releases/download/canary/sega.xml'),
)
SOURCES_JSON = os.path.join(nxpaths.APP, 'data', 'mod_sources.json')
CATALOG_CACHE = os.path.join(nxpaths.ROOT, 'cache', '_catalogs')
USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
              'AppleWebKit/537.36 (KHTML, like Gecko) 7thHeavenNX')
CHUNK = 1 << 20
# BUILD 832: retry, resume and parallel pieces.
RETRIES = 5                     # per link (per piece when split)
BACKOFF = (2, 4, 8, 16, 30)     # seconds before retry 1, 2, ...
READ_TIMEOUT = 30               # a connection silent this long is dead
SEGMENTS = 4                    # pieces for one big file
SEGMENT_MIN = 64 << 20          # files smaller than this stay one stream
PARALLEL_MODS = 3               # Install all runs this many at once

IRO_SIG = b'IROS'
SEVENZ_SIG = b"7z\xbc\xaf'\x1c"
ZIP_SIG = b'PK\x03\x04'


class Cancelled(Exception):
    pass


class DownloadError(Exception):
    pass


# ------------------------------------------------------------ catalogs

class CatalogMod:
    def __init__(self, el, catalog):
        self.catalog = catalog
        self.id = (el.findtext('ID') or '').strip().lower()
        self.name = (el.findtext('Name') or '').strip()
        self.author = (el.findtext('Author') or '').strip()
        lv = el.find('LatestVersion')
        lv = lv if lv is not None else ET.Element('LatestVersion')
        self.version = (lv.findtext('Version') or '').strip()
        self.release_date = (lv.findtext('ReleaseDate') or '').strip()[:10]
        self.links = [(l.text or '').strip() for l in lv.findall('Link')
                      if (l.text or '').strip()]
        try:
            # 7th Heaven stores it in KB
            self.size = int(lv.findtext('DownloadSize') or 0) * 1024
        except ValueError:
            self.size = 0


def _context():
    if os.environ.get('SSL_CERT_FILE'):
        return ssl.create_default_context()     # the user's own CA bundle
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:                                          # noqa: BLE001
        return ssl.create_default_context()


def _open(url, headers=None, timeout=READ_TIMEOUT):
    req = urllib.request.Request(url, headers=dict(
        {'User-Agent': USER_AGENT}, **(headers or {})))
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=_context())
    except urllib.error.URLError as exc:
        if not isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise
        raise DownloadError(
            'certificate check failed (%s). On macOS with the python.org '
            'installer, run "Install Certificates.command" from the Python '
            'folder in Applications, or pip install certifi.' % exc)


def load_catalogs(refresh=True, log=lambda *_: None):
    """{catalog id: CatalogMod} from both subscriptions. Each catalog is kept
    under cache/_catalogs, so with no connection the last copy is used."""
    os.makedirs(CATALOG_CACHE, exist_ok=True)
    out = {}
    for name, url in SUBSCRIPTIONS:
        path = os.path.join(CATALOG_CACHE, os.path.basename(url))
        if refresh:
            try:
                with _open(url) as resp:
                    data = resp.read()
                ET.fromstring(data)                 # never cache a bad copy
                tmp = path + '.part'
                with open(tmp, 'wb') as f:
                    f.write(data)
                os.replace(tmp, path)
            except Exception as exc:                           # noqa: BLE001
                log('%s: could not refresh (%s)%s' % (
                    name, exc, ' -- using the last copy'
                    if os.path.exists(path) else ''))
        if not os.path.exists(path):
            continue
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            log('%s: unreadable (%s)' % (name, exc))
            continue
        for el in root.iter('Mod'):
            mod = CatalogMod(el, name)
            if mod.id:
                out.setdefault(mod.id, mod)
    return out


def load_sources():
    """The supported mods: [{'id', 'name', 'file'}] in display order."""
    with open(SOURCES_JSON, encoding='utf-8') as f:
        return json.load(f)['mods']


# ------------------------------------------------------------ versions

def _dec(text):
    try:
        return decimal.Decimal(str(text).strip().replace(',', '.'))
    except (decimal.InvalidOperation, ValueError):
        return None


def is_newer(latest, installed):
    """True when the catalog version is newer. 7th Heaven compares them as
    decimals (Mod.cs), so 26.08303 > 26.08302 > 26.0830."""
    if not latest:
        return False
    if not installed:
        return True
    a, b = _dec(latest), _dec(installed)
    if a is not None and b is not None:
        return a > b
    return latest.strip() != installed.strip()


# ------------------------------------------------------------ links

def parse_link(link):
    """(kind, url) for an iros:// link, as LocationUtil.TryParse."""
    if link.lower().startswith('iros://'):
        link = link[7:]
    kind, _, rest = link.partition('/')
    if '$' in rest:
        i = rest.index('$')
        rest = rest[:i] + '://' + rest[i + 1:]
    return kind, rest


class _GDriveForm(html.parser.HTMLParser):
    """The confirm form on Google Drive's virus-scan warning page."""

    def __init__(self):
        super().__init__()
        self.action, self.fields, self._in = None, {}, False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'form' and (a.get('id') == 'download-form'
                              or 'download' in (a.get('action') or '')):
            self.action, self._in = a.get('action'), True
        elif tag == 'input' and self._in and a.get('name'):
            self.fields[a['name']] = a.get('value') or ''

    def handle_endtag(self, tag):
        if tag == 'form':
            self._in = False


def _gdrive_response(file_id):
    url = ('https://drive.google.com/uc?export=download&id=%s'
           % urllib.parse.quote(file_id))
    resp = _open(url)
    if 'text/html' not in (resp.headers.get('Content-Type') or ''):
        return resp
    page = resp.read().decode('utf-8', 'replace')
    resp.close()
    form = _GDriveForm()
    form.feed(page)
    if not form.action or 'confirm' not in form.fields:
        raise DownloadError('Google Drive did not offer the file (it may be '
                            'over its download quota; try again later)')
    url = form.action + '?' + urllib.parse.urlencode(form.fields)
    resp = _open(url, headers={'Referer': 'https://drive.google.com/'})
    if 'text/html' in (resp.headers.get('Content-Type') or ''):
        resp.close()
        raise DownloadError('Google Drive returned a page instead of the '
                            'file (download quota exceeded?)')
    return resp


# Errors worth another try: the network, not the request. A 4xx other than
# 408/429 means the link itself is wrong, and retrying it only wastes time.
def _retryable(exc):
    if isinstance(exc, urllib.error.HTTPError):
        # 403/410 mid-download: a mirror's signed link expired; the retry
        # opens the link afresh
        return exc.code in (403, 408, 410, 429) or exc.code >= 500
    return isinstance(exc, (OSError, urllib.error.URLError, socket.timeout,
                            http.client.HTTPException, _Short))


class _Short(Exception):
    """A response that ended before the bytes it promised."""


def _resolve(link):
    """(response, url, headers) for `link`: the first response, plus the
    final URL (after redirects and Google Drive's confirm page) and the
    headers that fetch it again, so a retry or a range can go straight
    there."""
    kind, url = parse_link(link)
    if kind == 'Url':
        resp = _open(url)
        return resp, resp.geturl(), {}
    if kind == 'GDrive':
        resp = _gdrive_response(url)
        return resp, resp.geturl(), {'Referer': 'https://drive.google.com/'}
    raise DownloadError('%s links need a browser: %s' % (kind, url))


def _open_range(url, headers, start, end=None):
    """A response for bytes start..end (inclusive), or None when the server
    ignores ranges and would send the whole file again."""
    rng = 'bytes=%d-%s' % (start, '' if end is None else end)
    resp = _open(url, headers=dict(headers, Range=rng))
    if resp.status != 206:
        resp.close()
        return None
    return resp


def _supports_ranges(resp, url, headers):
    if (resp.headers.get('Accept-Ranges') or '').lower() == 'bytes':
        return True
    try:                            # many servers serve ranges unannounced
        probe = _open_range(url, headers, 0, 0)
    except Exception:                                          # noqa: BLE001
        return False
    if probe is None:
        return False
    probe.close()
    return True


class _Progress:
    """Bytes received across every piece, reported as one number."""

    def __init__(self, progress, total):
        self.cb, self.total, self.done = progress, total, 0
        self.lock = threading.Lock()

    def add(self, n):
        with self.lock:
            self.done += n
            done = self.done
        self.cb(done, self.total)

    def take_back(self, n):         # a piece restarting from its beginning
        with self.lock:
            self.done -= n


def _wait(seconds, cancel):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancel():
            raise Cancelled()
        time.sleep(0.2)


def _copy(resp, f, want, prog, cancel, pos):
    """Stream resp into f until `want` bytes (None: until it ends).
    `pos` is a one-item list advanced as each chunk is written, so whatever
    arrived before a failure is kept and a retry resumes after it. Raises
    _Short when the response ends before `want`."""
    got = 0
    with resp:
        while want is None or got < want:
            if cancel():
                raise Cancelled()
            chunk = resp.read(CHUNK if want is None
                              else min(CHUNK, want - got))
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            pos[0] += len(chunk)
            prog.add(len(chunk))
    if want is not None and got < want:
        raise _Short('%d of %d bytes' % (got, want))


def _fetch(link, dest, progress, cancel, expect=0,
           log=lambda *_: None, status=lambda *_: None):
    """Download `link` to `dest`, retrying with resume (BUILD 832).

    A file the server sizes, on a server that serves byte ranges, is
    fetched as SEGMENTS pieces at once when it is big -- mirrors cap each
    connection, so this is most of the speed-up -- and every piece resumes
    from its own last byte when its connection drops. Without ranges a
    failed attempt starts over. Each attempt that fails waits BACKOFF and
    says so through `status` ("Retrying (2 of 5)"); after RETRIES the link
    is given up and the next one (if the catalog has one) is tried."""
    resp, url, headers = _resolve(link)
    total = int(resp.headers.get('Content-Length') or 0)
    ranges = bool(total) and _supports_ranges(resp, url, headers)
    prog = _Progress(progress, total or expect)
    try:
        if ranges and total >= SEGMENT_MIN:
            resp.close()
            _fetch_pieces(link, url, headers, dest, total, prog, cancel, log,
                          status)
        else:
            _fetch_stream(link, resp, url, headers, dest, total, ranges,
                          prog, cancel, log, status)
    except (_Short, OSError, http.client.HTTPException) as exc:
        if isinstance(exc, urllib.error.HTTPError):
            raise
        raise DownloadError('the connection kept dropping; gave up after '
                            '%d retries (%s)' % (RETRIES, exc))
    size = os.path.getsize(dest)
    if not size:
        raise DownloadError('empty download')
    if total and size != total:
        raise DownloadError('the download was cut short (%d of %d bytes)'
                            % (size, total))


def _retry_wait(attempt, exc, what, cancel, log, status):
    """Raise `exc` when it is final; otherwise say so and wait."""
    if not _retryable(exc) or attempt > RETRIES:
        raise exc
    delay = BACKOFF[min(attempt, len(BACKOFF)) - 1]
    log('%sconnection lost (%s); retry %d of %d in %ds'
        % (what, exc, attempt, RETRIES, delay))
    status('Retrying (%d of %d)…' % (attempt, RETRIES))
    _wait(delay, cancel)


def _fetch_stream(link, resp, url, headers, dest, total, ranges, prog,
                  cancel, log, status):
    """One connection. A drop resumes from the last byte when the server
    serves ranges, else the file starts over."""
    pos = [0]
    attempt = 0
    expired = False
    while True:
        try:
            if resp is None:
                if expired:
                    fresh, url, headers = _resolve(link)
                    fresh.close()
                    expired = False
                if pos[0] and ranges:
                    resp = _open_range(url, headers, pos[0])
                if resp is None:            # no ranges, or the link expired
                    if pos[0]:
                        log('cannot resume here; starting the file over')
                        prog.take_back(pos[0])
                        pos[0] = 0
                    resp, url, headers = _resolve(link)
            with open(dest, 'r+b' if pos[0] else 'wb') as f:
                f.seek(pos[0])
                _copy(resp, f, (total - pos[0]) if total else None, prog,
                      cancel, pos)
            return
        except Cancelled:
            raise
        except Exception as exc:                               # noqa: BLE001
            resp = None
            expired = (isinstance(exc, urllib.error.HTTPError)
                       and exc.code in (403, 410))
            attempt += 1
            _retry_wait(attempt, exc, '', cancel, log, status)


def _fetch_pieces(link, url, headers, dest, total, prog, cancel, log,
                  status):
    """SEGMENTS byte ranges at once into one preallocated file, each with
    its own retries and resume point."""
    with open(dest, 'wb') as f:
        f.truncate(total)
    step = -(-total // SEGMENTS)
    pieces = [(i * step, min(total, (i + 1) * step) - 1)
              for i in range(SEGMENTS) if i * step < total]
    errors = []
    stop = threading.Event()
    urlref = [url, headers]
    lock = threading.Lock()

    def stopped():
        return stop.is_set() or cancel()

    def refresh_url():
        # a mirror's signed redirect can expire mid-download: one fresh
        # resolve, shared by every piece
        with lock:
            r, u, h = _resolve(link)
            r.close()
            urlref[:] = [u, h]

    def piece(start, end):
        pos, attempt = [start], 0
        while pos[0] <= end:
            try:
                resp = _open_range(urlref[0], urlref[1], pos[0], end)
                if resp is None:
                    refresh_url()
                    resp = _open_range(urlref[0], urlref[1], pos[0], end)
                    if resp is None:
                        raise _Short('the server stopped serving ranges')
                with open(dest, 'r+b') as f:
                    f.seek(pos[0])
                    _copy(resp, f, end - pos[0] + 1, prog, stopped, pos)
            except Cancelled:
                return
            except Exception as exc:                           # noqa: BLE001
                attempt += 1
                try:
                    _retry_wait(attempt, exc, 'piece %d/%d: '
                                % (pieces.index((start, end)) + 1,
                                   len(pieces)), stopped, log, status)
                except Cancelled:
                    return
                except Exception as final:                     # noqa: BLE001
                    errors.append(final)
                    stop.set()
                    return
                if isinstance(exc, urllib.error.HTTPError) and \
                        exc.code in (403, 410):
                    try:
                        refresh_url()
                    except Exception:                          # noqa: BLE001
                        pass

    threads = [threading.Thread(target=piece, args=p, daemon=True)
               for p in pieces]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if cancel():
        raise Cancelled()
    if errors:
        raise errors[0]


# ------------------------------------------------------------ install

def _which(*names):
    return next((shutil.which(n) for n in names if shutil.which(n)), None)


def _extract(archive, dest, log):
    with open(archive, 'rb') as f:
        head = f.read(8)
    if head.startswith(ZIP_SIG):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
        return
    if not head.startswith(SEVENZ_SIG):
        raise DownloadError('not an IRO, 7z or zip file -- the host may '
                            'have sent an error page')
    try:
        import py7zr
        with py7zr.SevenZipFile(archive) as z:
            z.extractall(dest)
        return
    except ImportError:
        pass
    tool = _which('7zz', '7z', '7za')
    if tool:
        r = subprocess.run([tool, 'x', '-y', '-o' + dest, archive],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return
        log('  %s failed: %s' % (os.path.basename(tool),
                                 (r.stderr or r.stdout).strip()[-200:]))
    # macOS's own tar is bsdtar (libarchive), which reads 7z; GNU tar does not
    tar = _which('bsdtar') or ('/usr/bin/tar' if sys.platform == 'darwin'
                               else None)
    if tar:
        r = subprocess.run([tar, '-xf', archive, '-C', dest],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return
        log('  tar failed: %s' % r.stderr.strip()[-200:])
    raise DownloadError('cannot open .7z: pip install py7zr (or install 7-Zip)')


def _valid_iro(path):
    # a readable directory is not enough: it sits near the start of the
    # file, so a download cut short still lists fine
    try:
        with open(path, 'rb') as f:
            _, _, entries = iro.read_entries(f)
        iro.check_complete(path, entries)
        return bool(entries)
    except Exception:                                          # noqa: BLE001
        return False


def _swap_in(src, dest):
    """Move `src` onto `dest`, keeping the old copy until the move is done."""
    old = None
    if os.path.lexists(dest):
        old = dest + '.old-%d' % os.getpid()
        os.replace(dest, old)
    try:
        shutil.move(src, dest)
    except Exception:
        if old:
            os.replace(old, dest)
        raise
    if old:
        if os.path.isdir(old) and not os.path.islink(old):
            shutil.rmtree(old, ignore_errors=True)
        else:
            os.remove(old)


def install(download, mods_dir, file_name, log=lambda *_: None):
    """Put a finished download into mods/ as `file_name`. Returns the path.
    `file_name` ending in .iro is a packed mod; anything else a folder mod.
    The download's own shape wins when it disagrees (a folder where an .iro
    was expected keeps the name without its extension, and vice versa)."""
    stem = file_name[:-4] if file_name.lower().endswith('.iro') else file_name
    with open(download, 'rb') as f:
        head = f.read(8)
    if head.startswith(IRO_SIG):
        if not _valid_iro(download):
            raise DownloadError('the .iro is damaged (incomplete download?)')
        dest = os.path.join(mods_dir, stem + '.iro')
        _swap_in(download, dest)
        return dest
    work = tempfile.mkdtemp(prefix='.extract-', dir=mods_dir)
    try:
        _extract(download, work, log)
        iros, folders = [], []
        for dirpath, dirnames, files in os.walk(work):
            dirnames[:] = [d for d in dirnames if d != '__MACOSX']
            iros += [os.path.join(dirpath, f) for f in files
                     if f.lower().endswith('.iro')]
            if any(f.lower() == 'mod.xml' for f in files):
                folders.append(dirpath)
                dirnames[:] = []                # a mod folder is one unit
        if iros:
            src = max(iros, key=os.path.getsize)
            if not _valid_iro(src):
                raise DownloadError('the .iro inside the archive is damaged')
            dest = os.path.join(mods_dir, stem + '.iro')
            _swap_in(src, dest)
            return dest
        if folders:
            src = min(folders, key=len)         # the outermost mod.xml
            dest = os.path.join(mods_dir, stem)
            _swap_in(src, dest)
            return dest
        raise DownloadError('the archive holds no .iro and no mod.xml')
    finally:
        shutil.rmtree(work, ignore_errors=True)


def download_and_install(cmod, mods_dir, file_name, progress=lambda *_: None,
                         cancel=lambda: False, log=lambda *_: None,
                         status=lambda *_: None):
    """Fetch `cmod` (trying each of its links) and install it as
    `file_name`. progress(done, total) during the download."""
    if not cmod.links:
        raise DownloadError('the catalog lists no download link')
    errors = []
    os.makedirs(mods_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.download-', suffix='.part',
                               dir=mods_dir)
    os.close(fd)
    try:
        for link in cmod.links:
            try:
                _fetch(link, tmp, progress, cancel, cmod.size, log, status)
                return install(tmp, mods_dir, file_name, log)
            except Cancelled:
                raise
            except Exception as exc:                           # noqa: BLE001
                errors.append('%s: %s' % (parse_link(link)[0], exc))
                log('  %s' % errors[-1])
        raise DownloadError('; '.join(errors))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def cleanup(mods_dir):
    """Remove what an interrupted run left in mods/ (partial downloads,
    extraction folders, old copies mid-swap)."""
    try:
        names = os.listdir(mods_dir)
    except OSError:
        return
    for n in names:
        path = os.path.join(mods_dir, n)
        if n.startswith('.download-') or n.startswith('.extract-'):
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
        elif re.search(r'\.old-\d+$', n):
            live = path[:path.rindex('.old-')]
            if not os.path.lexists(live):
                os.replace(path, live)          # the swap never finished
            elif os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
