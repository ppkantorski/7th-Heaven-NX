#!/usr/bin/env python3
"""
test_summarise.py -- CALL every log-line builder in the project.

WHY THIS EXISTS
===============
Build 54 died 20 minutes in with

    TypeError: not all arguments converted during string formatting
    ff7nx_marginpal.py, in summarise

because a suffix was appended to a `%`-formatted string as

    'base ... %s ...' + (' suffix %s' % x if cond else '') % (args)

`%` binds tighter than `+`, so the suffix folded into the format string, its
own %s was consumed by its own operand, and the outer tuple had nothing left
to fill. `python3 -m py_compile` passes that file. It is valid syntax. It
just cannot run.

Every one of these functions is called ONCE per build, at the very end of a
pass, with data no offline check ever produces. That makes them the single
most under-tested code in the tree and the cheapest possible thing to break
a 20-minute build. This calls all of them with plausible stats and asserts
they return a string.

It does NOT check the wording. It checks that they RUN.

    python3 test_summarise.py
"""
from __future__ import annotations

import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


# (module, stats) -- stats are shaped like the real thing, values arbitrary.
CASES = []


def _add(mod, st, label=''):
    CASES.append((mod, st, label))


_MARGINPAL = {
    'slots_repointed': 135, 'fields': 99, 'tiles': 13121, 'cells': 13121,
    'remapped': 2, 'err_before': [11.27], 'err_after': [3.43],
    'idx_before': [18.8], 'idx_after': [24.6],
}
_add('ff7nx_marginpal', dict(_MARGINPAL), 'no constraint')
_add('ff7nx_marginpal', dict(_MARGINPAL, layer1_constrained=72), 'constrained')
# FINDINGS-148. Every combination the hue gate can produce, because the
# summarise() line branches on `esc`, `pen` and `hg` INDEPENDENTLY and a
# missing key in any one of them is a mid-build TypeError.
_add('ff7nx_marginpal', dict(_MARGINPAL, layer1_constrained=230,
                             layer1_escaped=76, layer1_escaped_hue=70,
                             layer1_penalty=[0.0, 1.3, 78.8],
                             layer1_hue_gap=[0.0, 0.031, 0.108]), 'hue escape')
_add('ff7nx_marginpal', dict(_MARGINPAL, layer1_escaped=6,
                             layer1_penalty=[1.0]), 'error escape, no hue')
_add('ff7nx_marginpal', dict(_MARGINPAL, layer1_hue_gap=[0.02]), 'hue, no esc')
# FINDINGS-149: the HUE VETO branch, alone and alongside the escape.
_add('ff7nx_marginpal', dict(_MARGINPAL, hue_vetoed=37,
                             hue_veto_dist=[0.05, 0.249]), 'hue veto')
_add('ff7nx_marginpal', dict(_MARGINPAL, layer1_constrained=230,
                             layer1_escaped=76, layer1_escaped_hue=70,
                             layer1_penalty=[1.3], layer1_hue_gap=[0.031],
                             hue_vetoed=37, hue_veto_dist=[0.249]), 'both')
_add('ff7nx_marginpal', {}, 'empty')

_MARGINART = {
    'read': 709, 'changed': 667, 'cells': 600000, 'filled': 519470,
    'black': 28988, 'no_dds': 36191, 'borrowed': 0, 'wild': 1414,
    'darkened': 6588, 'far_borrow': 0, 'detail': 0, 'uncovered': 16419687,
    'keep0_kept': 0, 'keep0_dropped': 0, 'keep0_cells': 0, 'refused': [],
    'pal': dict(_MARGINPAL),
}
_add('ff7nx_marginart', dict(_MARGINART), 'typical')
_add('ff7nx_marginart', dict(_MARGINART, keep0_dropped=3808905,
                             keep0_cells=145422, keep0_kept=13985378),
     'keep0 active')
_add('ff7nx_marginart', {}, 'empty')

_add('ff7nx_palkey', {'read': 709, 'changed': 581, 'pages': 2667,
                      'bright': 1118, 'blend_skipped': 3278, 'refused': []},
     'typical')
_add('ff7nx_palkey', {}, 'empty')


def main():
    fails = []
    ran = 0
    for mod_name, st, label in CASES:
        try:
            mod = __import__(mod_name)
        except Exception as exc:                               # noqa: BLE001
            fails.append((mod_name, label, 'import: %r' % exc))
            continue
        fn = getattr(mod, 'summarise', None)
        if fn is None:
            continue
        try:
            out = fn(st)
            ran += 1
        except Exception:                                      # noqa: BLE001
            fails.append((mod_name, label, traceback.format_exc(limit=2)))
            continue
        if not isinstance(out, str):
            fails.append((mod_name, label,
                          'returned %s, not str' % type(out).__name__))
            continue
        print('  ok  %-20s %-14s %d chars' % (mod_name, label, len(out)))

    for fn, label in ((test_pal_counter_propagates, 'counter propagation'),
                      (test_no_percent_on_concatenation, 'precedence trap')):
        try:
            fn()
            ran += 1
        except Exception:
            fails.append(('tree-wide', label, traceback.format_exc(limit=2)))

    print()
    if fails:
        print('%d FAILURE(S) -- these would have died mid-build:' % len(fails))
        for mod_name, label, why in fails:
            print('  !! %s [%s]' % (mod_name, label))
            for line in str(why).rstrip().splitlines():
                print('       %s' % line)
        return 1
    print('%d summarise() call(s) executed, all returned a string.' % ran)
    return 0


# --------------------------------------------------------------------------
# COUNTER PROPAGATION -- the second way a fix hides itself.
#
# Build 54's LAYER-1 CONSTRAINT counter was computed per field and then
# DROPPED, because the `pal` sub-dict was merged through a hardcoded key
# list. The constraint worked and the reported field was visibly fixed; its
# log line never printed, so the only evidence was two unrelated-looking
# counters moving.
#
# This calls the REAL `ff7nx_marginart.merge_pal`, not a copy of it.
# --------------------------------------------------------------------------
def test_pal_counter_propagates():
    import ff7nx_marginart as MA
    P = {'fields': 0, 'slots': 0, 'slots_repointed': 0, 'tiles': 0,
         'cells': 0, 'remapped': 0, 'layer1_constrained': 0,
         'err_before': [], 'err_after': [], 'idx_before': [], 'idx_after': []}
    ps = {'slots': 2, 'slots_repointed': 1, 'cells': 10, 'tiles': 4,
          'layer1_constrained': 3, 'err_before': [1.0], 'err_after': [0.5],
          'idx_before': [8.0], 'idx_after': [9.0]}
    MA.merge_pal(P, ps, {'pal_tiles': 4, 'pal_remapped': 0})
    assert P['layer1_constrained'] == 3, 'counter DROPPED: %r' % P
    # FINDINGS-148: the hue counters must survive the SAME merge, and the
    # aggregate in marginart must DECLARE them -- `merge_pal` skips any key
    # absent from `P`, which is how build 54 lost `layer1_constrained`.
    import inspect
    _src = inspect.getsource(MA)
    for _k in ('layer1_escaped_hue', 'layer1_hue_gap'):
        assert "'%s'" % _k in _src, (
            '%s is not declared in the marginart aggregate -- merge_pal will '
            'silently drop it and the log line will read 0' % _k)
    P3 = {'layer1_escaped_hue': 0, 'layer1_hue_gap': [], 'tiles': 0}
    MA.merge_pal(P3, {'layer1_escaped_hue': 70,
                      'layer1_hue_gap': [0.031, 0.108]}, {'pal_tiles': 0})
    assert P3['layer1_escaped_hue'] == 70, 'hue escape dropped: %r' % P3
    assert P3['layer1_hue_gap'] == [0.031, 0.108], (
        'hue gaps must EXTEND, not add: %r' % P3['layer1_hue_gap'])
    assert P['tiles'] == 4, 'tiles double-counted: %r' % P['tiles']
    assert P['cells'] == 10 and P['slots_repointed'] == 1 and P['fields'] == 1
    # and a counter nobody has invented yet must survive too
    P2 = dict(P); P2['a_future_counter'] = 0
    MA.merge_pal(P2, dict(ps, a_future_counter=7), {'pal_tiles': 0})
    assert P2['a_future_counter'] == 7, 'generic merge is not generic'
    print('  ok  merge_pal            new counters survive; tiles not doubled')


# --------------------------------------------------------------------------
# THE PRECEDENCE TRAP, FOUND STRUCTURALLY. FINDINGS-146.
#
#     'base %s' + (' suffix %s' % x) % (args)
#
# `%` binds tighter than `+`, so the suffix folds INTO the format string, its
# own specifier is consumed by its own operand, and the outer tuple has
# nothing left to fill -> "TypeError: not all arguments converted".
#
# py_compile passes it. It is valid syntax that cannot run. It killed build
# 54 in ff7nx_marginpal.summarise, and then killed build 56 in build.py one
# message after the summarise() test was written -- because that test only
# calls module summarise() functions and build.py's log lines are inline.
#
# So this checks the SHAPE instead of the call: any `X % Y` whose left side
# is a `+` concatenation involving a string literal. That is the bug and
# almost nothing else looks like it.
# --------------------------------------------------------------------------
def _fmt_specifiers(node):
    """True if any string literal in `node` carries a %-format specifier."""
    import ast, re
    pat = re.compile(r'%[-+ #0-9.*]*[sdifgeouxXcr%]')
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            if [m for m in pat.findall(n.value) if m != '%%']:
                return True
    return False


def test_no_percent_on_concatenation(paths=None):
    """
    Flag `"...%s..." + (X % args)`.

    THE SHAPE, GOT WRONG ONCE ALREADY. The first version of this check looked
    for `Mod(left=Add(...))`, which never matches: `%` binds tighter than `+`,
    so `'a %s' + (x % y)` parses as Add(left='a %s', right=Mod(x, y)) and the
    Mod's left is whatever the suffix was -- an IfExp in both real cases. The
    check passed the very file it was written for. Verified against a known-
    bad and known-good file below before being trusted.

    The tell is a format specifier in the LEFT literal: the author meant the
    outer tuple to fill it, and it never will.
    """
    import ast, glob
    paths = paths or sorted(glob.glob(os.path.join(_HERE, '*.py')))
    bad = []
    for path in paths:
        try:
            tree = ast.parse(open(path, encoding='utf-8').read(), path)
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if (isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add)
                    and isinstance(n.right, ast.BinOp)
                    and isinstance(n.right.op, ast.Mod)
                    and _fmt_specifiers(n.left)):
                bad.append((os.path.basename(path), n.lineno))
    if bad:
        print('  !! %d site(s) concatenate a FORMAT STRING with a %% expr:'
              % len(bad))
        for f, ln in bad:
            print('  !!   %s:%d' % (f, ln))
        raise AssertionError('percent-on-concatenation: %r' % (bad,))
    print('  ok  precedence trap     clean across %d file(s)' % len(paths))


if __name__ == '__main__':
    raise SystemExit(main())
