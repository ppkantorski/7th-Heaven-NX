#!/usr/bin/env python3
"""tools/release.py -- build a release of 7th Heaven NX that needs no Python.

    python3 tools/release.py              # build the zip for this computer
    python3 tools/release.py --list       # only print what would be shipped

The build runs in a private venv it makes under release/ (kept for the
next build) with only the packages the program needs, so it does not
matter what else the Python you start it with has installed. --no-venv
builds with that Python as it is (add --install to pip-install what is
missing into it).

One run builds one zip, for the system and CPU it runs on, because
PyInstaller cannot cross-compile: a Windows arm64 zip has to be built on
Windows arm64, and so on. .github/workflows/release.yml runs this on all six
(Windows, macOS and Linux, each x64 and arm64) when a version tag is pushed.

The zip lands in release/ as 7th-Heaven-NX-<version>-<os>-<arch>.zip, with
a .sha256 next to it. Unzipped it is one folder:

    Windows / Linux   7th Heaven NX/7th Heaven NX(.exe), _internal/,
                      mods/, dump/, README.md, LICENSE
    macOS             7th Heaven NX/7th Heaven NX.app, mods/, dump/,
                      README.md, LICENSE

and that folder is the user's 7th Heaven NX folder: dump/, mods/, cache/,
sdout/ and settings.json go next to the program exactly as they go next to
7th_heaven_nx.py today (see nxpaths).

WHAT IS SHIPPED. Only the program's own files, chosen here by rule, never
by "everything in the folder": the code (7th_heaven_nx.py and src/), assets/,
the static data/*.json, native/'s built payloads and their sources, PyFF7,
README.md and LICENSE, and of shaders/ exactly the files a build can install
(ff7nx_shaders.release_files()). So the game's own files -- dump/, the
port's stock shaders, data/ff7_en_switch -- and everything else that is
local (mods/, cache/, sdout/, attic/, repos/, tools/, tests/, settings.json)
cannot end up in a zip; `_check` refuses the build if one somehow does, and
refuses any shipped shader that is byte-identical to the game's own (a
native shader is "no file", never a copy).

The LZS library lgp.py otherwise compiles on first run is built here too,
so users need no C compiler. FFmpeg is not shipped: the program fetches it
once on first run into <folder>/modules/ffmpeg, as `python3 7th_heaven_nx.py` does.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install()
import argparse
import hashlib
import importlib.util
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

import ff7nx_deps

ROOT = nxpaths.ROOT
NAME = '7th Heaven NX'
BUNDLE_ID = 'io.github.ppkantorski.seventhheavennx'
OUT = os.path.join(ROOT, 'release')

# Never in a zip, whatever else changes (paths relative to the project).
FORBIDDEN = ('dump/', 'mods/', 'cache/', 'sdout/', 'attic/', 'repos/',
             'tools/', 'tests/', 'docs/', 'game_data_files/', 'release/',
             'shaders/stock/', 'data/ff7_en_switch', 'data/build_counters.json',
             'settings.json', '.venv/')
JUNK = ('.DS_Store', 'Thumbs.db', '__pycache__', '.pytest_cache')


def _say(msg=''):
    print(msg, flush=True)


def version():
    with open(os.path.join(ROOT, '7th_heaven_nx.py'), encoding='utf-8') as f:
        m = re.search(r"^VERSION = '([^']+)'", f.read(), re.M)
    return m.group(1) if m else '0.0.0'


def target():
    """('windows' | 'macos' | 'linux', 'x64' | 'arm64') of this Python."""
    system = {'win32': 'windows', 'darwin': 'macos'}.get(
        sys.platform, 'linux' if sys.platform.startswith('linux')
        else sys.platform)
    machine = platform.machine().lower()
    arch = {'x86_64': 'x64', 'amd64': 'x64', 'arm64': 'arm64',
            'aarch64': 'arm64'}.get(machine, machine)
    return system, arch


# ---------------------------------------------------------------- packages

def _modules():
    """Every package the release has to carry: (import name, pip name,
    required?)."""
    rows = [(i, p, True) for i, p, _ in ff7nx_deps.REQUIRED]
    rows += [(i, p, False) for i, p, _ in ff7nx_deps.OPTIONAL]
    i, p, _ = ff7nx_deps.FFMPEG_PACKAGE
    rows.append((i, p, False))
    rows.append(('PyInstaller', 'pyinstaller', True))
    return rows


def _have(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def check_packages(install=False, strict=False):
    missing = [(i, p, req) for i, p, req in _modules() if not _have(i)]
    if missing and install:
        # required ones together, optional ones each on their own: one with
        # no wheel for this platform must not stop the rest
        need = [p for _, p, req in missing if req]
        batches = ([need] if need else []) + [[p] for _, p, req in missing
                                               if not req]
        for batch in batches:
            _say('installing: %s' % ', '.join(batch))
            rc = subprocess.call([sys.executable, '-m', 'pip', 'install']
                                 + batch)
            if rc and len(batch) > 1:
                # pip installs nothing when one package of a batch fails to
                # build: retry each alone so the rest still go in and the
                # error names only the one that broke
                for p in batch:
                    _say('installing: %s' % p)
                    subprocess.call([sys.executable, '-m', 'pip', 'install',
                                     p])
        importlib.invalidate_caches()
        missing = [(i, p, req) for i, p, req in missing if not _have(i)]
    hard = [p for _, p, req in missing if req or strict]
    for _, p, req in missing:
        _say('%s %s is not installed%s'
             % ('!' if req or strict else '-', p,
                '' if req or strict
                else ' (the release will work without it, like the script)'))
    if hard:
        sys.exit('missing: %s -- run with --install, or pip install them'
                 % ' '.join(hard))
    try:
        import tkinter  # noqa: F401
    except ImportError:
        sys.exit('this Python has no tkinter, so the release would have no '
                 'window; use a Python with Tk')


# ------------------------------------------------------------------ files

def _walk(rel_dir, keep=lambda rel: True):
    base = os.path.join(ROOT, rel_dir)
    for d, subs, files in os.walk(base):
        subs[:] = sorted(s for s in subs if s not in JUNK
                         and not s.startswith('.'))
        for f in sorted(files):
            if f in JUNK or f.startswith('.') or f.endswith(('.pyc', '.pyo')):
                continue
            rel = os.path.relpath(os.path.join(d, f), ROOT).replace(os.sep, '/')
            if keep(rel):
                yield rel


def release_files():
    """Every file the release ships, relative to the project folder."""
    out = ['7th_heaven_nx.py', 'LICENSE', 'README.md']
    out += _walk('src', lambda r: r.endswith('.py'))
    # whitein_paint/ is the tool that MAKES whitein_left_strip.png
    out += _walk('assets', lambda r: not r.startswith('assets/whitein_paint/'))
    out += _walk('data', lambda r: r.endswith('.json')
                 and r != 'data/build_counters.json')
    out += _walk('native', lambda r: (
        r.endswith(('.bin', '.entry', '.src.sha256'))
        or (r.endswith('.c') and not r.endswith('_host_test.c')))
        and not r.startswith('native/lzs/'))
    import ff7nx_shaders
    out += ff7nx_shaders.release_files()
    # BUILD 817: vendored under common/
    out += ['common/PyFF7/LICENSE']
    out += _walk('common/PyFF7/PyFF7', lambda r: r.endswith('.py'))
    return sorted(set(out))


def _stock_shaders():
    """{sha256: name} of the game's own shaders, from the user's dump."""
    import build
    dump = build.find_game_dump(ROOT)
    out = {}
    if dump is None:
        return out
    d = os.path.join(os.path.dirname(dump.workingdir), 'shaders')
    if os.path.isdir(d):
        for f in os.listdir(d):
            p = os.path.join(d, f)
            if os.path.isfile(p):
                out[_sha256(p)] = f
    return out


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def unshipped_imports(files):
    """
    BUILD 808. ``[(module, its home, importer)]`` for every import in a
    shipped module that resolves only to tools/ or tests/ -- which a release
    does not carry. ff7nx_dockres imported tools/probes/nsosyms, so a release
    logged "No module named 'nsosyms'" and kept 720p docked; nothing failed
    until a user's build. Static (ast), function-level imports included.
    """
    import ast

    def names(base):
        out = {}
        for d, subs, fs in os.walk(os.path.join(ROOT, base)):
            subs[:] = [x for x in subs if x not in JUNK]
            for f in fs:
                if f.endswith('.py'):
                    out.setdefault(f[:-3], os.path.relpath(
                        os.path.join(d, f), ROOT).replace(os.sep, '/'))
        return out
    shipped = {os.path.basename(r)[:-3] for r in files if r.endswith('.py')}
    elsewhere = dict(names('tests'), **names('tools'))
    out = []
    for r in files:
        if not (r.endswith('.py') and r.startswith('src/')):
            continue
        try:
            with open(os.path.join(ROOT, r), encoding='utf-8') as f:
                tree = ast.parse(f.read())
        except (OSError, SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name.split('.')[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module \
                    and not node.level:
                mods = [node.module.split('.')[0]]
            else:
                continue
            for m in mods:
                if m not in shipped and m in elsewhere:
                    out.append((m, elsewhere[m], r))
    return sorted(set(out))


#: Under the user's folder ON PURPOSE: written per user, or supplied by them.
USER_DATA = ('build_counters.json', 'ff7_en_switch')
_SHIPPED_DIRS = ('data', 'native', 'shaders', 'assets', 'common', 'tools',
                 'tests')


def shipped_paths_under_root(files):
    """
    BUILD 808c. ``[(file, line, text)]`` where shipped code builds a path to
    a SHIPPED folder from the user's folder (nxpaths.ROOT). Source runs have
    one folder, so it works there; a release keeps the program's files in
    the bundle (nxpaths.APP) and the lookup finds nothing --
    spelltex_overrides.json silently loaded as empty that way.
    """
    import re
    dirs = '|'.join(_SHIPPED_DIRS)
    out = []
    for r in files:
        if not (r.endswith('.py') and (r.startswith('src/')
                                       or r == '7th_heaven_nx.py')):
            continue
        try:
            with open(os.path.join(ROOT, r), encoding='utf-8') as f:
                text = f.read()
        except OSError:
            continue
        roots = set(re.findall(r'^\s*(\w+)\s*=\s*nxpaths\.ROOT\b', text,
                               re.M)) | {'nxpaths.ROOT'}
        pat = re.compile(r"(?:%s)\s*,\s*['\"](?:%s)['\"/]"
                         % ('|'.join(re.escape(x) for x in roots), dirs))
        for n, line in enumerate(text.split('\n'), 1):
            if line.lstrip().startswith('#') or not pat.search(line):
                continue
            if any(u in line for u in USER_DATA):
                continue
            out.append((r, n, line.strip()))
    return out


def _check(files):
    bad = [r for r in files if r.startswith(FORBIDDEN) or r in FORBIDDEN]
    missing = [r for r in files if not os.path.isfile(os.path.join(ROOT, r))]
    stock = _stock_shaders()
    native = [r for r in files if r.startswith('shaders/')
              and _sha256(os.path.join(ROOT, r)) in stock]
    if not stock:
        _say('- no game dump here, so shipped shaders were not compared '
             'with the game\'s own (tests/test_shader_release.py does it)')
    problems = (['%s imports %s, which lives in %s and is not shipped'
                 % (who, m, home) for m, home, who in unshipped_imports(files)]
                + ['%s:%d looks for a shipped file in the user\'s folder '
                   '(use nxpaths.APP): %s' % hit
                   for hit in shipped_paths_under_root(files)]
                + ['not shippable: ' + r for r in bad]
                + ['missing: ' + r for r in missing]
                + ['identical to the game\'s own shader: ' + r
                   for r in native])
    if problems:
        sys.exit('refusing to build:\n  ' + '\n  '.join(problems))


def stage(files, dest):
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    for rel in files:
        out = os.path.join(dest, *rel.split('/'))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copy2(os.path.join(ROOT, rel), out)
        os.chmod(out, 0o644)


# ------------------------------------------------------------------ build

def _pyinstaller(app, work, dist, system):
    src = os.path.join(app, 'src')
    src_dirs = [d for d, _, fs in os.walk(src)
                if any(f.endswith('.py') for f in fs)]
    names = sorted({f[:-3] for d in src_dirs for f in os.listdir(d)
                    if f.endswith('.py')})
    args = [os.path.join(app, '7th_heaven_nx.py'),
            '--name', NAME, '--onedir', '--noconfirm', '--clean',
            '--distpath', dist, '--workpath', work, '--specpath', work,
            '--collect-submodules', 'PyFF7',
            '--collect-binaries', 'capstone',
            '--exclude-module', 'pytest',
            '--exclude-module', 'matplotlib',
            '--exclude-module', 'IPython']
    # static-ffmpeg's package pulls in its own release tooling (twine and
    # friends); the program only ever calls static_ffmpeg.run
    for m in ('twine', 'keyring', 'rich', 'docutils', 'readme_renderer',
              'pygments', 'secretstorage', 'jeepney', 'nh3', 'markdown_it',
              'requests_toolbelt', 'cryptography'):
        args += ['--exclude-module', m]
    # src/ first: PyFF7's clone has top-level names of its own
    for d in src_dirs + [os.path.join(app, 'common', 'PyFF7')]:
        args += ['--paths', d]
    for n in names:
        args += ['--hidden-import', n]
    if _have('certifi'):
        args += ['--collect-data', 'certifi']
    # the program's own files, at the same paths under nxpaths.APP
    for top in sorted(os.listdir(app)):
        p = os.path.join(app, top)
        args += ['--add-data', '%s%s%s' % (p, os.pathsep,
                                           top if os.path.isdir(p) else '.')]
    if system in ('windows', 'macos'):
        args += ['--windowed']
        # macOS: the rounded-square cut (tools/generators/make_macos_icon.py)
        icon = os.path.join(app, 'assets', 'icon_macos.png'
                            if system == 'macos' else 'icon.png')
        if os.path.isfile(icon):
            args += ['--icon', icon]        # converted with Pillow
    if system == 'macos':
        args += ['--osx-bundle-identifier', BUNDLE_ID]
    _say('running PyInstaller (%d modules) ...' % len(names))
    import PyInstaller.__main__
    PyInstaller.__main__.run(args)
    built = os.path.join(dist, NAME + ('.app' if system == 'macos' else ''))
    if not os.path.exists(built):
        sys.exit('PyInstaller produced no %s' % built)
    return built


def _executable(folder, system):
    if system == 'macos':
        return os.path.join(folder, NAME + '.app', 'Contents', 'MacOS', NAME)
    return os.path.join(folder, NAME + ('.exe' if system == 'windows' else ''))


def selftest(folder, system):
    """Run the finished program with --selftest against an empty folder."""
    exe = _executable(folder, system)
    with tempfile.TemporaryDirectory() as outer:
        # BUILD 808: a decoy arm64emu.py in the user's folder and in its
        # parent (~/Downloads, for a release unzipped there). The selftest
        # fails if either is imported instead of the bundled module.
        home = os.path.join(outer, 'home')
        os.makedirs(home)
        for d in (outer, home):
            with open(os.path.join(d, 'arm64emu.py'), 'w') as f:
                f.write('class Cpu:\n    pass\n')
        env = dict(os.environ, SEVENTH_NX_HOME=home)
        r = subprocess.run([exe, '--selftest'], env=env, timeout=600,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        text = r.stdout.decode(errors='replace')
        log = os.path.join(home, '7th_heaven_nx.log')
        if os.path.isfile(log):             # windowed: no console
            with open(log, encoding='utf-8', errors='replace') as f:
                text += f.read()
    _say(text.rstrip())
    if r.returncode != 0:
        sys.exit('the release failed its selftest (exit %d)' % r.returncode)


def _zip(folder, archive, system):
    if os.path.exists(archive):
        os.remove(archive)
    if system == 'macos':
        # ditto keeps the .app's symlinks, permissions and signature intact
        subprocess.check_call(['ditto', '-c', '-k', '--sequesterRsrc',
                               '--keepParent', folder, archive])
        return
    base = os.path.dirname(folder)
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED,
                         compresslevel=9) as zf:
        for d, subs, files in os.walk(folder):
            subs.sort()
            if not subs and not files:          # keep empty folders
                zi = zipfile.ZipInfo(
                    os.path.relpath(d, base).replace(os.sep, '/') + '/')
                zi.create_system = 3
                zi.external_attr = (stat.S_IFDIR | 0o755) << 16
                zf.writestr(zi, b'')
            for f in sorted(files) + [s for s in subs
                                      if os.path.islink(os.path.join(d, s))]:
                p = os.path.join(d, f)
                arc = os.path.relpath(p, base).replace(os.sep, '/')
                st = os.lstat(p)
                if stat.S_ISLNK(st.st_mode):
                    zi = zipfile.ZipInfo(arc)
                    zi.create_system = 3
                    zi.external_attr = (stat.S_IFLNK | 0o777) << 16
                    zf.writestr(zi, os.readlink(p))
                else:
                    zf.write(p, arc)


VENV_ENV = 'SEVENTH_NX_RELEASE_VENV'


def _in_build_venv():
    return os.environ.get(VENV_ENV) == sys.prefix


def build_venv(out, system, arch):
    """
    Re-run this script inside a private venv with only what the release
    needs. Whatever else the calling Python has (a conda base environment
    has hundreds of packages) is invisible there, so PyInstaller cannot
    follow an optional import into it, and every build of a version gets
    the same contents. The venv is kept for the next build.
    """
    venv = os.path.join(out, 'venv-%s-%s' % (system, arch))
    py = os.path.join(venv, 'Scripts', 'python.exe') if system == 'windows' \
        else os.path.join(venv, 'bin', 'python')
    if not os.path.isfile(py):
        _say('making the build environment %s ...' % venv)
        subprocess.check_call([sys.executable, '-m', 'venv', venv])
        subprocess.check_call([py, '-m', 'pip', 'install', '--upgrade',
                               'pip'])
    prefix = subprocess.check_output(
        [py, '-c', 'import sys; print(sys.prefix)'], text=True).strip()
    args = [a for a in sys.argv[1:] if a != '--install']
    env = dict(os.environ, **{VENV_ENV: prefix})
    env.pop('PYTHONPATH', None)
    return subprocess.call([py, os.path.abspath(__file__), '--install']
                           + args, env=env)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--install', action='store_true',
                    help='pip-install missing packages into this Python')
    ap.add_argument('--no-venv', action='store_true',
                    help='build with this Python as it is, instead of a '
                    'private venv under release/')
    ap.add_argument('--strict', action='store_true',
                    help='fail when an optional package is missing too')
    ap.add_argument('--list', action='store_true',
                    help='print the files that would be shipped and stop')
    ap.add_argument('--tag', help='the git tag being released (vX.Y.Z); '
                    'the build fails unless it matches VERSION')
    ap.add_argument('--skip-selftest', action='store_true')
    ap.add_argument('--out', default=OUT)
    opts = ap.parse_args()

    system, arch = target()
    ver = version()
    if opts.tag and opts.tag.lstrip('v') != ver:
        sys.exit('tag %s does not match VERSION %s in 7th_heaven_nx.py'
                 % (opts.tag, ver))
    out = os.path.abspath(opts.out)
    # before anything imports the program's modules, which need its
    # packages: the Python this was started with may have none of them
    if not opts.no_venv and not _in_build_venv():
        return build_venv(out, system, arch)
    check_packages(opts.install, opts.strict)
    # PyFF7 is a clone in common/ (BUILD 817); fetch it like a first run does
    ff7nx_deps._ensure_pyff7(ROOT)
    files = release_files()
    if opts.list:
        for r in files:
            _say(r)
        return 0
    _check(files)

    tmp = os.path.join(out, 'tmp-%s-%s' % (system, arch))
    app = os.path.join(tmp, 'app')
    _say('7th Heaven NX %s for %s %s: %d files' % (ver, system, arch,
                                                   len(files)))
    stage(files, app)
    import lgp
    if lgp.build_native(os.path.join(app, 'native', 'lzs'), log=_say) is None:
        _say('- no C compiler worked: this release uses the Python LZS '
             'routines (same output, slower flevel builds)')

    built = _pyinstaller(app, os.path.join(tmp, 'build'),
                         os.path.join(tmp, 'dist'), system)
    folder = os.path.join(tmp, 'pkg', NAME)
    if os.path.isdir(os.path.dirname(folder)):
        shutil.rmtree(os.path.dirname(folder))
    os.makedirs(folder)
    if system == 'macos':
        shutil.move(built, os.path.join(folder, NAME + '.app'))
    else:
        for f in os.listdir(built):
            shutil.move(os.path.join(built, f), folder)
    for f in ('README.md', 'LICENSE'):
        shutil.copy2(os.path.join(ROOT, f), folder)
        os.chmod(os.path.join(folder, f), 0o644)
    # the folders the user fills, there before the first launch so the mods
    # and the dump can go in first (the program also makes them if missing)
    for d in ('mods', 'dump'):
        os.makedirs(os.path.join(folder, d), exist_ok=True)

    if not opts.skip_selftest:
        selftest(folder, system)

    archive = os.path.join(out, '7th-Heaven-NX-%s-%s-%s.zip'
                           % (ver, system, arch))
    _zip(folder, archive, system)
    digest = _sha256(archive)
    with open(archive + '.sha256', 'w') as f:
        f.write('%s  %s\n' % (digest, os.path.basename(archive)))
    shutil.rmtree(tmp, ignore_errors=True)
    _say('')
    _say('%s  (%.1f MB)' % (archive, os.path.getsize(archive) / 1e6))
    _say('sha256 %s' % digest)
    return 0


if __name__ == '__main__':
    sys.exit(main())
