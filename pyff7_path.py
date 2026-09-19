"""
pyff7_path.py -- make the name ``PyFF7`` mean the package, for everybody.

THE TRAP THIS EXISTS TO CLOSE
=============================
PyFF7 is vendored as a git clone, so the tree is::

    7th_heaven_nx/
        PyFF7/                  <- the CLONE ROOT. No __init__.py.
            LICENSE  README.md  lgp_pack.py  lgp_info.py  ...
            PyFF7/              <- the actual package
                __init__.py  lgp.py  field.py  ...

Both directories are called `PyFF7`, and only the inner one is a package. The
project directory is on `sys.path`, so a bare ``import PyFF7`` finds the clone
root first -- and under PEP 420 a directory with no ``__init__.py`` is still
importable, as a NAMESPACE package. It has no ``lgp`` and no ``field`` in it,
so the import fails; but by then Python has cached the namespace package in
``sys.modules['PyFF7']``, and every later import of a PyFF7 submodule --
anywhere in the process, by anybody -- looks in the clone root and fails too.

`build.ensure_pyff7` avoids this by putting the clone root on `sys.path` first,
so the name resolves to the inner package. That works only if it runs before
anything else touches the name. It did not: `echo_s_flevel` imported
``PyFF7.field`` at module scope, `build.py` imports `echo_s_flevel` at the top,
and `ensure_pyff7` does not run until the first model archive is packed. The
symptom was a build that got all the way to char.lgp and then died with

    ModuleNotFoundError: No module named 'PyFF7.lgp'

with the package sitting right there on disk.

So the rule is: any module that imports from PyFF7 at import time calls
``ensure()`` first, and it is safe to call as often as you like.

WHY IT ALSO EVICTS
==================
Putting the path in front is not enough on its own once somebody has already
got it wrong, because the bad entry is cached. `ensure` therefore drops a
`PyFF7` that resolved to a namespace package before fixing the path. It only
ever discards a namespace -- a real package, wherever it came from, is left
exactly as it is.
"""
import os
import sys


PACKAGE = 'PyFF7'


def _is_namespace(module):
    """True for a PEP 420 namespace package -- a directory with no code."""
    return getattr(module, '__file__', None) is None and hasattr(module,
                                                                 '__path__')


def ensure(project_dir=None):
    """
    Make ``import PyFF7`` resolve to the vendored package. Returns its path,
    or None when the clone is not present (the caller's own import will then
    raise, which is the right way to find out).

    Idempotent, and safe to call from module scope.
    """
    if project_dir is None:
        project_dir = os.path.dirname(os.path.abspath(__file__))
    clone_root = os.path.join(project_dir, PACKAGE)
    if not os.path.isfile(os.path.join(clone_root, PACKAGE, '__init__.py')):
        return None

    cached = sys.modules.get(PACKAGE)
    if cached is not None and _is_namespace(cached):
        # Cached as the clone root. Drop it and anything hanging off it, so
        # the next import resolves properly.
        for name in [n for n in sys.modules
                     if n == PACKAGE or n.startswith(PACKAGE + '.')]:
            del sys.modules[name]

    if clone_root not in sys.path:
        sys.path.insert(0, clone_root)
    return clone_root
