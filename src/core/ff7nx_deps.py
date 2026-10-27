"""src/core/ff7nx_deps.py

First-run setup: make sure the Python packages 7th Heaven NX needs are
there before anything imports them.

`python3 7th_heaven_nx.py` is the only instruction a user gets, so this has
to work on a bare Python from python.org, Homebrew, a Linux distro or the
Microsoft Store, on Windows, macOS and Linux alike. The rules:

  * Everything already importable: do nothing. No venv, no pip, no delay.
  * Running inside a virtual environment (ours or the user's own): pip
    installs what is missing straight into it.
  * Otherwise: make a private venv at `<project>/.venv` (with
    --system-site-packages, so whatever the system already has is reused),
    install what is missing there, and run the program again with that venv's
    Python. Nothing outside the project folder changes, and it side-steps
    PEP 668 -- Homebrew and current Debian/Ubuntu Pythons refuse a plain
    `pip install` outright.

Required packages stop the program with a clear message when they cannot be
installed. Optional ones only warn, because the program has a fallback for
each: Pillow decodes the DDS art when texture2ddecoder is missing, 7-Zip or
tar extract .7z downloads when py7zr is, and so on.

FFmpeg is not a Python package, but movies, Echo-S voice and the sound
effects all need it. When it is not on PATH and SEVENTH_NX_FFMPEG does not
name one, `static-ffmpeg` fetches a build for this platform once and the
build is pointed at it through SEVENTH_NX_FFMPEG / SEVENTH_NX_FFPROBE.

PyFF7 (the LGP packer and field reader) is vendored as a clone at
`<project>/common/PyFF7` (BUILD 817; it was `<project>/PyFF7`) and is imported at startup, so a fresh copy of the project
fetches it here too: GitHub's zip of it, which needs no git.

A release build (tools/release.py) has all of this inside it already, so
`ensure` only fetches FFmpeg there.

tkinter cannot be installed with pip at all, so a missing one gets the
command for this platform instead.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import importlib
import importlib.util
import os
import shutil
import subprocess
import sys

MIN_PYTHON = (3, 9)

# (import name, pip requirement, why). Import names are what is checked, so a
# package installed any way at all -- pip, conda, a distro package -- counts.
REQUIRED = (
    ('PIL', 'Pillow', 'textures and images'),
    ('numpy', 'numpy', 'field and effect art'),
    ('scipy', 'scipy', 'field art clean-up'),
    ('lz4', 'lz4', 'the Switch executable is LZ4 compressed'),
    ('capstone', 'capstone', 'reading the game executable'),
)
OPTIONAL = (
    ('texture2ddecoder', 'texture2ddecoder', 'faster DDS decoding'),
    ('py7zr', 'py7zr', '.7z mod downloads'),
    ('certifi', 'certifi', 'HTTPS certificates for mod downloads'),
)
if sys.platform == 'darwin':
    OPTIONAL += (('AppKit', 'pyobjc-framework-Cocoa', 'the Dock icon'),)
FFMPEG_PACKAGE = ('static_ffmpeg', 'static-ffmpeg', 'movies, voice and sound')

# Optional packages that failed to install for a given Python, so a package
# with no wheel for it is not retried (and pip's errors not shown) on every
# launch. Delete the file to try again.
SKIPPED_FILE = '.deps_skipped'

PYFF7_ZIP = 'https://github.com/niemasd/PyFF7/archive/HEAD.zip'
PYFF7_GIT = 'https://github.com/niemasd/PyFF7.git'

FFMPEG_ENV = 'SEVENTH_NX_FFMPEG'
FFPROBE_ENV = 'SEVENTH_NX_FFPROBE'
# Set on the copy of the program started inside the venv, so a venv that
# still cannot import something reports it instead of starting itself again.
CHILD_ENV = 'SEVENTH_NX_DEPS_CHILD'


def _say(msg):
    sys.stderr.write('[setup] %s\n' % msg)
    sys.stderr.flush()


def _have(module):
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _missing(table):
    return [row for row in table if not _have(row[0])]


def _in_venv():
    return sys.prefix != getattr(sys, 'base_prefix', sys.prefix)


def _venv_python(venv):
    if os.name == 'nt':
        return os.path.join(venv, 'Scripts', 'python.exe')
    for name in ('python3', 'python'):
        path = os.path.join(venv, 'bin', name)
        if os.path.exists(path):
            return path
    return os.path.join(venv, 'bin', 'python3')


def _pip(python, requirements):
    """True when pip installed every requirement."""
    if not requirements:
        return True
    cmd = [python, '-m', 'pip', 'install', '--disable-pip-version-check',
           '--retries', '2', '--timeout', '30'] + list(requirements)
    _say('Installing ' + ', '.join(requirements) + ' ...')
    try:
        return subprocess.call(cmd) == 0
    except OSError as exc:
        _say('Could not run pip: %s' % exc)
        return False


def _install(python, rows):
    """
    Install `rows` with `python`'s pip. One call for all of them first, the
    quick path; if that fails, one at a time, so a package with no wheel for
    this Python does not take the others down with it. Returns the rows that
    still did not install.
    """
    if _pip(python, [r[1] for r in rows]):
        return []
    return [r for r in rows if not _pip(python, [r[1]])]


def _tk_help():
    if sys.platform == 'darwin':
        ver = '%d.%d' % sys.version_info[:2]
        return ('If this Python is from Homebrew:  brew install python-tk@%s\n'
                'Otherwise install Python from https://www.python.org/downloads/ '
                '(it includes tkinter).' % ver)
    if os.name == 'nt':
        return ('Run the python.org installer again, choose Modify, and tick '
                '"tcl/tk and IDLE".')
    return ('Install your distribution\'s Tk package for Python, e.g.\n'
            '  Debian/Ubuntu:  sudo apt install python3-tk\n'
            '  Fedora:         sudo dnf install python3-tkinter\n'
            '  Arch:           sudo pacman -S tk')


def _venv_help():
    if sys.platform.startswith('linux'):
        return ('Install the venv and pip packages for this Python, e.g.\n'
                '  Debian/Ubuntu:  sudo apt install python3-venv python3-pip\n'
                '  Fedora:         sudo dnf install python3-pip\n'
                'then run 7th_heaven_nx.py again.')
    return ('Make sure this Python includes pip (the python.org installer '
            'does), then run 7th_heaven_nx.py again.')


def _fail(msg):
    _say(msg)
    sys.exit(1)


def _make_venv(venv):
    """Create the private venv. False when this Python cannot make one."""
    _say('Setting up a private Python environment in %s (first run only) ...'
         % venv)
    try:
        import venv as _venv
        _venv.create(venv, system_site_packages=True, with_pip=True,
                     clear=os.path.isdir(venv))
    except Exception as exc:                                    # noqa: BLE001
        _say('Could not create it: %s' % exc)
        shutil.rmtree(venv, ignore_errors=True)
        return False
    return os.path.exists(_venv_python(venv))


def _relaunch(python, script):
    env = dict(os.environ)
    env[CHILD_ENV] = '1'
    try:
        rc = subprocess.call([python, script] + sys.argv[1:], env=env)
    except KeyboardInterrupt:
        rc = 130
    sys.exit(rc)


def _python_key():
    return '%s %d.%d' % ((sys.executable,) + sys.version_info[:2])


def _skipped(root):
    try:
        with open(os.path.join(root, SKIPPED_FILE)) as fh:
            lines = fh.read().splitlines()
    except OSError:
        return set()
    key = _python_key() + '\t'
    return {line[len(key):] for line in lines if line.startswith(key)}


def _remember_skipped(root, rows):
    if not rows:
        return
    path = os.path.join(root, SKIPPED_FILE)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a') as fh:
            for r in rows:
                fh.write('%s\t%s\n' % (_python_key(), r[1]))
    except OSError:
        pass


def _ensure_packages(root, script):
    optional = OPTIONAL
    if not _ffmpeg_on_system():
        optional += (FFMPEG_PACKAGE,)
    skipped = _skipped(root)
    missing = _missing(REQUIRED) + [r for r in _missing(optional)
                                    if r[1] not in skipped]
    if not missing:
        return

    no_venv = False
    if not _in_venv() and not os.environ.get(CHILD_ENV):
        venv = os.path.join(root, '.venv')
        python = _venv_python(venv)
        if os.path.exists(python) or _make_venv(venv):
            # The venv's own copy of this function installs what is missing.
            _relaunch(python, script)
        # No venv on this Python: fall back to its own pip, which works
        # unless the Python is externally managed (and then says so).
        no_venv = True
        _say('Falling back to installing into this Python itself.')

    _say('Missing: ' + ', '.join('%s (%s)' % (r[1], r[2]) for r in missing))
    failed = _install(sys.executable, missing)
    importlib.invalidate_caches()
    failed = [r for r in failed if not _have(r[0])]
    hard = [r for r in failed if r in REQUIRED]
    soft = [r for r in failed if r not in REQUIRED]
    for r in soft:
        _say('Optional package %s did not install; %s will fall back or '
             'be unavailable.' % (r[1], r[2]))
    # Only once something did install: a failure with no network at all
    # says nothing about the package, and should be retried next time.
    if len(failed) < len(missing):
        _remember_skipped(root, soft)
    if hard:
        _fail('Could not install: %s.\n%s\nOr install them yourself:\n'
              '  %s -m pip install %s'
              % (', '.join(r[1] for r in hard),
                 _venv_help() if no_venv else
                 'Check your internet connection and run 7th_heaven_nx.py '
                 'again.',
                 '"%s"' % sys.executable,
                 ' '.join(r[1] for r in hard)))
    if not failed:
        _say('All packages installed.')


def _ffmpeg_on_system():
    explicit = os.environ.get(FFMPEG_ENV)
    if explicit and os.path.isfile(explicit):
        return True
    return bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))


def _ensure_ffmpeg(download_root=None):
    """Point the build at static-ffmpeg's binaries when the system has none.

    `download_root` (release builds) puts the download, and static-ffmpeg's
    lock file, there instead of inside its own package folder, which in a
    release build is part of the read-only bundle."""
    if _ffmpeg_on_system() or not _have('static_ffmpeg'):
        return
    try:
        from static_ffmpeg import run
        _say('Checking FFmpeg (downloaded once on first run) ...')
        download_dir = None
        if download_root:
            os.makedirs(download_root, exist_ok=True)
            run.LOCK_FILE = os.path.join(download_root, 'lock.file')
            # the zip unpacks into <root>/<platform key>/
            download_dir = os.path.join(download_root,
                                        run.get_platform_key())
        ffmpeg, ffprobe = run.get_or_fetch_platform_executables_else_raise(
            download_dir=download_dir)
    except Exception as exc:                                    # noqa: BLE001
        _say('FFmpeg is not available (%s). Movies and voice will be skipped '
             'until it is installed and on PATH.' % exc)
        return
    os.environ[FFMPEG_ENV] = ffmpeg
    os.environ[FFPROBE_ENV] = ffprobe


def _ssl_context():
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:                                           # noqa: BLE001
        return ssl.create_default_context()


def _fetch_pyff7_zip(dest):
    import tempfile
    import urllib.request
    import zipfile
    with tempfile.TemporaryDirectory(dir=os.path.dirname(dest)) as tmp:
        archive = os.path.join(tmp, 'pyff7.zip')
        req = urllib.request.Request(PYFF7_ZIP,
                                     headers={'User-Agent': '7th-Heaven-NX'})
        with urllib.request.urlopen(req, timeout=60,
                                    context=_ssl_context()) as resp, \
                open(archive, 'wb') as out:
            shutil.copyfileobj(resp, out)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(os.path.join(tmp, 'x'))
        # One top-level folder, named after the commit: PyFF7-<sha>/.
        (top,) = os.listdir(os.path.join(tmp, 'x'))
        shutil.move(os.path.join(tmp, 'x', top), dest)


def _ensure_pyff7(root):
    # BUILD 817: common/PyFF7. A pre-817 clone at the root is MOVED there, not
    # fetched again (nxpaths.pyff7_root reads either until this has run).
    dest = os.path.join(root, 'common', 'PyFF7')
    legacy = os.path.join(root, 'PyFF7')
    if (not os.path.isfile(os.path.join(dest, 'PyFF7', '__init__.py'))
            and os.path.isfile(os.path.join(legacy, 'PyFF7', '__init__.py'))):
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if os.path.isdir(dest):
                shutil.rmtree(dest, ignore_errors=True)
            shutil.move(legacy, dest)
            _say('Moved PyFF7 into common/PyFF7.')
        except Exception as exc:                                # noqa: BLE001
            _say('Could not move PyFF7 into common/ (%s); using %s.'
                 % (exc, legacy))
            return
    if os.path.isfile(os.path.join(dest, 'PyFF7', '__init__.py')):
        return
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    _say('Fetching PyFF7 (first run only) ...')
    if os.path.isdir(dest):
        shutil.rmtree(dest, ignore_errors=True)
    try:
        _fetch_pyff7_zip(dest)
    except Exception as exc:                                    # noqa: BLE001
        shutil.rmtree(dest, ignore_errors=True)
        git = shutil.which('git')
        if not git or subprocess.call(
                [git, 'clone', '--depth', '1', PYFF7_GIT, dest]) != 0:
            _fail('Could not download PyFF7 (%s).\nDownload %s, unzip it, '
                  'and rename the folder to %s.' % (exc, PYFF7_ZIP, dest))
    if not os.path.isfile(os.path.join(dest, 'PyFF7', '__init__.py')):
        _fail('PyFF7 downloaded, but %s has no PyFF7 package in it.' % dest)


def _frozen(root):
    """
    A release build (PyInstaller) carries its own Python, every package and
    PyFF7, so there is nothing to install and nothing to re-run. A windowed
    build has no console, so print() would fail on a None stream: those go
    to <root>/7th_heaven_nx.log instead. FFmpeg is still fetched once, into
    <root>/modules/ffmpeg (not by --selftest, which only checks this copy).
    """
    os.makedirs(root, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        try:
            log = open(os.path.join(root, '7th_heaven_nx.log'), 'a',
                       encoding='utf-8', buffering=1)
        except OSError:
            log = open(os.devnull, 'w')
        if sys.stdout is None:
            sys.stdout = log
        if sys.stderr is None:
            sys.stderr = log
    if '--selftest' not in sys.argv:
        dest = os.path.join(root, 'modules', 'ffmpeg')
        # Earlier release builds fetched it into <root>/ffmpeg: move that
        # copy rather than download it again.
        old = os.path.join(root, 'ffmpeg')
        if os.path.isdir(old) and not os.path.exists(dest):
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                os.replace(old, dest)
            except OSError:
                pass
        _ensure_ffmpeg(dest)


def ensure(root, script, gui=True):
    """
    Call once from the entry point, before any third-party import. Returns
    normally when everything is ready; otherwise installs what is missing
    (possibly re-running the program inside the private venv and exiting
    with its status), or exits with instructions.
    """
    if getattr(sys, 'frozen', False):
        _frozen(root)
        return
    if sys.version_info < MIN_PYTHON:
        _fail('7th Heaven NX needs Python %d.%d or newer; this is %d.%d.'
              % (MIN_PYTHON + sys.version_info[:2]))
    if gui and not _have('tkinter'):
        _fail('This Python has no tkinter, which the window needs. pip cannot '
              'install it.\n' + _tk_help() +
              '\n(Or build without the window: python3 7th_heaven_nx.py --cli)')
    _ensure_packages(root, script)
    _ensure_pyff7(root)
    _ensure_ffmpeg()
