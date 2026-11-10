#!/usr/bin/env python3
"""No module defines the same top-level function or class twice.

BUILD 899 added `apply_echo_music` to build.py, a name build.py already used
for Echo-S's music-compatibility pass. Python keeps the LAST definition, so
the new one silently replaced the old: the compatibility patches were never
installed, the existing call ran the new pass early, and its BSS took the
space the Echo-S voice runtime needs -- no voice acting, and nothing failed
loudly enough to stop the build. This makes that a test failure.

    python3 -m pytest tests/test_no_shadowed_defs.py

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Redefinitions that predate this test (several deliberate, e.g. lgp.py
# replacing its own LZS codec, marked noqa: F811). Only a NEW one fails.
KNOWN = {
    ('src/core/lgp.py', 'lzs_compress'),
    ('src/core/lgp.py', 'lzs_decompress'),
    ('src/engine/ff7nx_spelluv.py', '_helper_installed'),
    ('src/engine/ff7nx_spelluv.py', '_recip_installed'),
    ('src/engine/ff7nx_spelluv.py', '_recip_v1_installed'),
    ('src/engine/ff7nx_spelluv.py', '_walk_helper'),
    ('src/engine/ff7nx_spelluv.py', '_writer_installed'),
    ('src/engine/ff7nx_spelluv.py', 'dimstore_installed'),
    ('src/engine/ff7nx_spelluv.py', 'helper_body'),
    ('src/engine/ff7nx_spelluv.py', 'recip_body'),
    ('src/engine/ff7nx_spelluv.py', 'reciprocal_installed'),
    ('src/engine/ff7nx_spelluv.py', 'writer_body'),
}


def _sources():
    yield os.path.join(ROOT, '7th_heaven_nx.py')
    for d, subs, files in os.walk(os.path.join(ROOT, 'src')):
        subs[:] = [s for s in subs if s != '__pycache__']
        for f in files:
            if f.endswith('.py'):
                yield os.path.join(d, f)


def test_no_top_level_name_is_defined_twice():
    clashes = []
    for path in _sources():
        with open(path, encoding='utf-8') as f:
            tree = ast.parse(f.read(), path)
        seen = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                rel = os.path.relpath(path, ROOT).replace(os.sep, '/')
                if node.name in seen and (rel, node.name) not in KNOWN:
                    clashes.append('%s: %s (lines %d and %d)'
                                   % (os.path.relpath(path, ROOT), node.name,
                                      seen[node.name], node.lineno))
                seen[node.name] = node.lineno
    assert not clashes, 'redefined, the first is dead code:\n' + \
        '\n'.join(clashes)
