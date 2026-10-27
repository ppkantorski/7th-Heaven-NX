"""nxpaths -- where 7th Heaven NX keeps its files, for every module.

The code lives under ``src/<area>/``. Each area directory is put on
``sys.path`` (``install()``), so modules keep importing each other by their
flat names (``import build``, ``import ff7nx_esui``) wherever they sit -- the
same idea as a C project's include directories.

``ROOT`` is the project folder: cache/, mods/, dump/, sdout/,
settings.json and everything else the program writes are found from it,
never from a module's own location. ``APP`` is where the program's own
read-only files are: assets/, data/, native/, shaders/ and PyFF7/.

Run from source they are the same folder. In a release build (PyInstaller,
``sys.frozen``) ``APP`` is the bundle's own folder and ``ROOT`` is the folder
the user unzipped: next to the executable on Windows and Linux, next to the
.app on macOS (its original place, even when Gatekeeper runs a translocated
copy). SEVENTH_NX_HOME overrides that ``ROOT``.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os
import sys

FROZEN = bool(getattr(sys, 'frozen', False))


def _original_bundle(bundle):
    """
    BUILD 806. Where a translocated .app really is, or None.

    Gatekeeper runs a quarantined app (one unzipped from a download and
    opened in place) from a random read-only copy under /private/var/.../
    AppTranslocation/, so the folders the zip put next to the .app are not
    next to the running copy. Security.framework's
    SecTranslocateCreateOriginalPathForURL maps the copy back to the
    original; only the copy is read-only, so the original's folder is where
    mods/, dump/ and settings.json belong. Any failure returns None.
    """
    try:
        import ctypes
        cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework'
                         '/CoreFoundation')
        sec = ctypes.CDLL('/System/Library/Frameworks/Security.framework'
                          '/Security')
        vp = ctypes.c_void_p
        cf.CFURLCreateFromFileSystemRepresentation.restype = vp
        cf.CFURLCreateFromFileSystemRepresentation.argtypes = [
            vp, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool]
        cf.CFURLGetFileSystemRepresentation.restype = ctypes.c_bool
        cf.CFURLGetFileSystemRepresentation.argtypes = [
            vp, ctypes.c_bool, ctypes.c_char_p, ctypes.c_long]
        cf.CFRelease.argtypes = [vp]
        sec.SecTranslocateCreateOriginalPathForURL.restype = vp
        sec.SecTranslocateCreateOriginalPathForURL.argtypes = [vp, vp]
        raw = os.fsencode(bundle)
        url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw),
                                                         True)
        if not url:
            return None
        try:
            orig = sec.SecTranslocateCreateOriginalPathForURL(url, None)
        finally:
            cf.CFRelease(url)
        if not orig:
            return None
        try:
            buf = ctypes.create_string_buffer(4096)
            if not cf.CFURLGetFileSystemRepresentation(orig, True, buf,
                                                       len(buf)):
                return None
            return os.fsdecode(buf.value).rstrip('/') or None
        finally:
            cf.CFRelease(orig)
    except Exception:                                           # noqa: BLE001
        return None


def _frozen_root():
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    if sys.platform == 'darwin':
        # <ROOT>/7th Heaven NX.app/Contents/MacOS/<exe>
        bundle = os.path.dirname(os.path.dirname(exe_dir))
        if bundle.endswith('.app'):
            root = os.path.dirname(bundle)
            if '/AppTranslocation/' not in root:
                return root
            # BUILD 806: translocated -- use the folder the user unzipped,
            # not ~/7th Heaven NX, so the zip's own mods/ and dump/ are used.
            orig = _original_bundle(bundle)
            if orig and '/AppTranslocation/' not in orig:
                root = os.path.dirname(orig)
                if os.access(root, os.W_OK):
                    return root
            return os.path.join(os.path.expanduser('~'), '7th Heaven NX')
    return exe_dir


if FROZEN:
    APP = os.path.abspath(getattr(sys, '_MEIPASS', None)
                          or os.path.dirname(sys.executable))
    SRC = os.path.join(APP, 'src')
    ROOT = os.environ.get('SEVENTH_NX_HOME') or _frozen_root()
    ENTRY = os.path.join(APP, '7th_heaven_nx.py')
else:
    SRC = os.path.dirname(os.path.abspath(__file__))
    ROOT = os.path.dirname(SRC)
    APP = os.path.dirname(SRC)
    ENTRY = os.path.join(APP, '7th_heaven_nx.py')
ROOT = os.path.abspath(ROOT)
if FROZEN:
    # the bundle is read-only as far as we are concerned (and signed on
    # macOS): no __pycache__ next to the shipped sources
    sys.dont_write_bytecode = True


def _app_roots():
    """The bundle, as written and as resolved. On macOS that is the whole
    .app/Contents: PyInstaller cross-links Frameworks/ and Resources/ with
    symlinks (base_library.zip, PIL, src ...), so either side may appear."""
    roots = [os.path.abspath(APP)]
    parent = os.path.dirname(roots[0])
    if os.path.basename(parent) == 'Contents':
        roots.append(parent)
    return {r for root in roots for r in (root, os.path.realpath(root))}


def _inside_app(p):
    try:
        forms = {os.path.abspath(p), os.path.realpath(os.path.abspath(p))}
    except (TypeError, ValueError, OSError):
        return False
    return any(f == r or f.startswith(r + os.sep)
               for f in forms for r in _app_roots())


class _FrozenPath(list):
    """
    BUILD 808. In a release, sys.path only ever holds the bundle.

    PyInstaller 6 serves the bundled modules through sys.path, in order, so
    any directory put AHEAD of the bundle can replace one of them. About
    seventy modules still carry the source layout's `sys.path.insert(0,
    nxpaths.ROOT)` -- and diag_common adds ROOT/.., letterbox and camclamp
    ROOT/7th_heaven_nx. In a release ROOT is the user's folder (the one
    the .app sits in), so ROOT/.. is typically ~/Downloads, and any
    `arm64emu.py` lying there was imported instead of the bundled one:
    "'Cpu' object has no attribute 'guest_to_host'" in the movie-bars step
    (hardware 10-26). It is also a door for running whatever .py happens to
    sit next to the app.

    None of those directories hold program code in a release, so adding
    them is refused here, once, for every caller. Source runs are untouched.
    """

    def insert(self, i, p):
        if _inside_app(p):
            list.insert(self, i, p)

    def append(self, p):
        if _inside_app(p):
            list.append(self, p)

    def extend(self, ps):
        list.extend(self, [p for p in ps if _inside_app(p)])

    def __iadd__(self, ps):
        self.extend(ps)
        return self

    def __setitem__(self, k, v):
        if isinstance(k, slice):
            v = [p for p in v if _inside_app(p)]
        elif not _inside_app(v):
            return
        list.__setitem__(self, k, v)


if FROZEN and not isinstance(sys.path, _FrozenPath):
    sys.path = _FrozenPath(p for p in sys.path if _inside_app(p))
TOOLS = os.path.join(APP, 'tools')
TESTS = os.path.join(APP, 'tests')


def _code_dirs(base):
    out = []
    for d, subs, files in os.walk(base):
        subs[:] = sorted(s for s in subs
                         if not s.startswith(('.', '_')) and s != '__pycache__')
        if any(f.endswith('.py') for f in files):
            out.append(d)
    return out


def source_dirs():
    """Every directory under src/ that holds modules."""
    return [d for d in _code_dirs(SRC) if d != SRC]


def tool_dirs():
    return _code_dirs(TOOLS) if os.path.isdir(TOOLS) else []


def install(tools=False):
    """Put the module directories on sys.path (after the running script's
    own directory). Idempotent.

    A release build has every module compiled in already, so this adds
    nothing there; src/ is shipped only as the record of what was built
    (``code_files``)."""
    if FROZEN:
        return []
    want = source_dirs() + [SRC] + (tool_dirs() if tools else [])
    at = 1 if sys.path else 0
    for d in want:
        if d not in sys.path:
            sys.path.insert(at, d)
            at += 1
    return want


_INDEX = None


def _index():
    global _INDEX
    if _INDEX is None:
        idx = {}
        for base in (SRC, TOOLS, TESTS):
            if not os.path.isdir(base):
                continue
            for d, subs, files in os.walk(base):
                subs[:] = [s for s in subs if s != '__pycache__']
                for f in files:
                    if f.endswith('.py'):
                        idx.setdefault(f, os.path.join(d, f))
        idx.setdefault('7th_heaven_nx.py', ENTRY)
        _INDEX = idx
    return _INDEX


def src_file(name):
    """Path of a module's source file by its file name ('build.py')."""
    path = _index().get(name)
    if path is None:
        raise FileNotFoundError('%s is not in src/, tools/ or tests/' % name)
    return path


def code_files():
    """{file name: path} of the program's own code: the entry point and
    every module under src/. What the archive fingerprint hashes."""
    out = {'7th_heaven_nx.py': ENTRY}
    for d in [SRC] + source_dirs():
        for f in os.listdir(d):
            if f.endswith('.py'):
                out.setdefault(f, os.path.join(d, f))
    return out
