#!/usr/bin/env python3
"""
test_gui_settings.py -- a persisted setting must have a control that sets it.

WHY THIS EXISTS
===============
"Smooth cutscene movement" was written, unit-tested, documented and shipped,
and it could not be turned on. The Tk variable was created, saved to
settings.json, traced for autosave, and read into the environment on build --
four of the five things a setting needs. The fifth, the row in
SETTINGS_SECTIONS that draws the checkbox, was missing.

Nothing failed. The feature's own tests passed, because the feature was fine.
The build log looked correct, because from the builder's point of view the
option simply had not been requested. The only way to find out was a hardware
test that came back "no change" -- twice.

The bug lives in the SEAM between persistence and presentation, so that is
what this checks, and it checks it in the direction the bug ran:

    everything snapshot_settings() persists must be reachable from a control.

Persistence is the right side to start from. A setting exists the moment it is
written to settings.json -- that is what makes it a setting rather than a
widget's private state -- and it is the side that is easy to add to. Starting
from the controls instead would only catch the opposite mistake, a control
wired to nothing, which is loud at runtime anyway.

The source is parsed, not imported: importing means constructing a Tk root,
which needs a display, so it would not run anywhere useful.
"""
import ast
import sys

GUI = '7th_heaven_nx.py'
SNAPSHOT = 'snapshot_settings'
GLOBAL_KEY = '__global__'


def fail(msg):
    print('FAIL  ' + msg)
    sys.exit(1)


def find_func(tree, name):
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name):
            return node
    return None


def global_dict(fn):
    """The dict literal assigned to persist['__global__']."""
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        tgt = node.targets[0]
        if (isinstance(tgt, ast.Subscript)
                and isinstance(tgt.slice, ast.Constant)
                and tgt.slice.value == GLOBAL_KEY
                and isinstance(node.value, ast.Dict)):
            return node.value
    return None


def vars_read(node):
    """Names X where X.get() is called anywhere under `node`."""
    out = set()
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == 'get'
                and isinstance(sub.func.value, ast.Name)
                and not sub.args):
            out.add(sub.func.value.id)
    return out


def helpers_called(node):
    """Bare function names called under `node` -- the current_*() accessors."""
    return {sub.func.id for sub in ast.walk(node)
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)}


def settings_vars(tree, gdict):
    """
    Every Tk var a persisted setting is read from.

    Some entries read their var directly (`bool(fps_var.get())`), others go
    through an accessor (`current_limiter_fps()`) that maps a combo's label
    back to a value. Both are settings; only the indirection differs, so the
    accessors are followed one level.
    """
    found = {}                      # var -> the settings key it backs
    for key, val in zip(gdict.keys, gdict.values):
        name = key.value if isinstance(key, ast.Constant) else '?'
        direct = vars_read(val)
        for v in direct:
            found.setdefault(v, name)
        if direct:
            continue
        for h in helpers_called(val):
            fn = find_func(tree, h)
            if fn is None:
                continue
            for v in vars_read(fn):
                found.setdefault(v, name)
    return found


def placed_vars(tree):
    """
    Vars bound to a control by a SETTINGS_SECTIONS row.

    Rows are ('check'|'combo', <label>, <var>, ...) tuples, so this reads the
    third element of every such tuple in the assignment -- structurally, so a
    label containing a comma or a quote cannot throw it off.
    """
    out = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name)
                        and t.id == 'SETTINGS_SECTIONS'
                        for t in node.targets)):
            continue
        for sub in ast.walk(node.value):
            if not isinstance(sub, ast.Tuple) or len(sub.elts) < 3:
                continue
            head = sub.elts[0]
            if (isinstance(head, ast.Constant)
                    and head.value in ('check', 'combo')
                    and isinstance(sub.elts[2], ast.Name)):
                out.add(sub.elts[2].id)
    return out


def main():
    try:
        src = open(GUI, encoding='utf-8').read()
    except IOError as e:
        sys.exit('cannot read %s: %s' % (GUI, e))
    tree = ast.parse(src)

    snap = find_func(tree, SNAPSHOT)
    if snap is None:
        fail('%s has no %s() -- the GUI was restructured and this test needs '
             'updating' % (GUI, SNAPSHOT))
    gdict = global_dict(snap)
    if gdict is None:
        fail("%s() no longer assigns a dict literal to persist['%s'] -- this "
             'test needs updating' % (SNAPSHOT, GLOBAL_KEY))

    settings = settings_vars(tree, gdict)
    placed = placed_vars(tree)
    if not settings:
        fail('found no persisted settings at all -- the parser is broken, '
             'not the GUI')
    if not placed:
        fail('found no SETTINGS_SECTIONS rows at all -- the parser is broken, '
             'not the GUI')

    orphan = sorted((v, k) for v, k in settings.items() if v not in placed)
    if orphan:
        print('FAIL')
        print('  these settings are saved to settings.json but have NO '
              'CONTROL in the')
        print('  window, so nothing can ever turn them on:')
        for v, k in orphan:
            print('      %-16s (settings key %r)' % (v, k))
        print()
        print('  add a row to SETTINGS_SECTIONS for each.')
        sys.exit(1)

    untraced = sorted(v for v in settings
                      if ('%s.trace_add' % v) not in src)
    if untraced:
        print('FAIL')
        print('  these settings have no trace_add, so changing them is not '
              'saved:')
        for v in untraced:
            print('      ' + v)
        sys.exit(1)

    check_use_before_assignment(src)

    print('  %d persisted setting(s), all drawn and all autosaved:'
          % len(settings))
    for v, k in sorted(settings.items()):
        print('      %-16s %s' % (v, k))
    print('all good')


def check_use_before_assignment(src):
    """
    A nested helper called DURING launch_ui must not read a local that
    launch_ui has not bound yet.

    This exists because that bug shipped. `_match_preset()` was called to
    initialise the preset dropdown, and it read `current_field_bg_budget_mb`,
    which is defined forty lines further down -- so the window died with

        NameError: cannot access free variable
        'current_field_bg_budget_mb' where it is not associated with a value

    `python -m py_compile` cannot see this (the name IS bound, just later)
    and there is no tkinter in CI to run the window, so nothing caught it.
    A static ordering check does, and costs nothing.
    """
    import ast

    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == 'launch_ui'),
              None)
    if fn is None:
        return

    # first line at which each local name becomes bound
    bound = {}

    def note(name, lineno):
        if name not in bound or lineno < bound[name]:
            bound[name] = lineno

    helpers = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.FunctionDef) and node is not fn:
            note(node.name, node.lineno)
            helpers[node.name] = node
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for sub in ast.walk(t):
                    # ctx MUST be Store. In `os.environ[k] = v` the Name `os`
                    # is a Load inside the target, and counting it as a
                    # binding made the check claim `os` is defined at that
                    # line -- a false positive on every helper using it.
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx,
                                                                ast.Store):
                        note(sub.id, node.lineno)

    def reads(node):
        """
        Names the helper reads from the ENCLOSING scope.

        Its own parameters and locals must be subtracted, or every `for key in
        ...` and every argument reads as a free variable and the check drowns
        in false positives.
        """
        own = set()
        a = node.args
        for arg in (list(a.args) + list(a.posonlyargs) + list(a.kwonlyargs)
                    + [a.vararg, a.kwarg]):
            if arg is not None:
                own.add(arg.arg)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                own.add(sub.id)
            elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                own.add(sub.name)
            elif isinstance(sub, ast.ExceptHandler) and sub.name:
                own.add(sub.name)
        out = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                out.add(sub.id)
        return out - own

    # calls that run immediately -- i.e. NOT inside another def
    # functions nested INSIDE another helper. `d is not h` matters: ast.walk
    # yields the node it was given, so without it every helper lands in here
    # and the whole check silently passes.
    inner = {id(d) for h in helpers.values()
             for d in ast.walk(h)
             if isinstance(d, ast.FunctionDef) and d is not h}
    bad = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not isinstance(f, ast.Name) or f.id not in helpers:
            continue
        target = helpers[f.id]
        if id(target) in inner:
            continue
        # is this call itself inside a nested def? then it runs later
        enclosing = [d for d in ast.walk(fn)
                     if isinstance(d, ast.FunctionDef) and d is not fn
                     and d.lineno <= node.lineno
                     and (d.end_lineno or d.lineno) >= node.lineno]
        if enclosing:
            continue
        for name in reads(target):
            if name in bound and bound[name] > node.lineno:
                bad.append((f.id, node.lineno, name, bound[name]))

    if bad:
        print('FAIL')
        print('  a helper is CALLED before a local it reads is defined, so '
              'the window will')
        print('  die with NameError at startup:')
        for fname, call_line, name, def_line in sorted(set(bad)):
            print('      %s() called at line %d reads %r, '
                  'which is not defined until line %d'
                  % (fname, call_line, name, def_line))
        print()
        print('  move the call (or the definition) so the name is bound '
              'first.')
        sys.exit(1)


if __name__ == '__main__':
    main()
