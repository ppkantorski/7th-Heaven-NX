#!/usr/bin/env python3
"""
build_guard.py -- name every counter that moved when it should not have.

WHY
===
HANDOFF-121 section 3.6, the process failure that cost that session four
builds:

    "Logs read for confirmation, not evidence. The user supplied every log.
     They were grepped for whatever theory was current instead of diffed
     against the previous build. The 3,267 -> 1,199 collapse was in plain text
     for four builds."

A change to one pass should move that pass's counters and nothing else. This
tees the build log, pulls the counters out of it, compares them against the
previous build's, and writes a `!!` line naming anything that moved outside the
set the current change is expected to touch. It never stops the build -- a
20-minute build is too expensive to throw away over a warning, and the whole
point is to put the evidence in front of the reader rather than to judge it.

It reads the log text, not the passes. That is deliberate: the counters come
from a dozen modules as formatted strings, and plumbing structured values out
of all of them is a bigger change than the one being guarded.

USAGE
=====
In the build:

    guard = build_guard.CounterGuard(log, expect={'page cap'})
    ...  run the build with guard.log in place of log  ...
    guard.finish()

Standalone, against two logs you already have:

    python3 build_guard.py latest_log_34.txt latest_log_35.txt
"""
from __future__ import annotations

import json
import os
import re
import sys

# Each entry: name -> (regex with one capture group, which change owns it).
# The owner tag is what `expect` matches against, so a page-cap change can be
# told "these are yours, everything else is a warning".
# ---------------------------------------------------------------------------
# WHAT THIS BUILD IS ALLOWED TO MOVE. SET IT DELIBERATELY, EVERY TIME.
#
# The owner tags are the third field of each COUNTERS entry: 'page cap',
# 'margin art', 'margin palette', 'margin page', 'transparency key',
# 'dense repack', 'field background'.
#
# Empty means "this build should move NOTHING", which is the right setting far
# more often than it looks -- a logging change, a diagnostic, a refactor. Build
# 36 changed one log line and the guard was still told to expect page-cap
# movement, so the one counter that mattered was reported alongside noise.
#
# Declaring it per build is the point. HANDOFF-121 3.6: "one variable per
# build, prediction written first". This is that prediction, in a form the
# build can check itself.
# Build 47: the transparency key may be darkened, never brightened. That is
# ff7nx_palkey's write site, so its counters move -- 'transparency key pages'
# drops by the ~243 keys that were dark in vanilla and were being made
# visible. Nothing else should.
EXPECTED_MOVEMENT = frozenset({'transparency key'})

COUNTERS = (
    ('margin art cells',
     r'margin art: ([\d,]+) cell\(s\) of Cosmos', 'margin art'),
    ('margin art fields',
     r'margin art: [\d,]+ cell\(s\).*? in ([\d,]+) of \d+ field', 'margin art'),
    ('atlas gap texels',
     r'ATLAS GAP: ([\d,]+) texel', 'margin art'),
    ('margin art refused',
     r'([\d,]+) REFUSED as wildly off-colour', 'margin art'),
    ('margin palette pages',
     r'margin palette: ([\d,]+) page\(s\)', 'margin palette'),
    ('margin page split cells',
     r'margin page split: ([\d,]+) cell\(s\) moved', 'margin page'),
    ('margin page split pages',
     r'margin page split: [\d,]+ cell\(s\) moved onto ([\d,]+) new',
     'margin page'),
    ('transparency key pages',
     r'transparency key: entry 0 de-fringed on ([\d,]+) palette page',
     'transparency key'),
    ('transparency key fields',
     r'de-fringed on [\d,]+ palette page\(s\) across ([\d,]+) field',
     'transparency key'),
    ('transparency key bright',
     r'across [\d,]+ field\(s\), ([\d,]+) of them previously a bright',
     'transparency key'),
    # This one moved 3,796 -> 3,847 between builds 33 and 34 and I missed it
    # by hand. It was benign -- the cap added 46 pages and a duplicate in an
    # additive band is skipped for the same reason its original is -- but
    # "benign" was a judgement made after the fact. Counted from now on.
    ('transparency key left alone',
     r'([\d,]+) palette\(s\) were LEFT ALONE', 'transparency key'),
    ('dense repack cells',
     r'DENSE REPACK .*?: ([\d,]+) cell\(s\) packed', 'dense repack'),
    ('dense repack pages',
     r'cell\(s\) packed onto ([\d,]+) page\(s\)', 'dense repack'),
    ('dense repack fields',
     r'packed onto [\d,]+ page\(s\) across ([\d,]+) field', 'dense repack'),
    ('dense repack borrowed',
     r'([\d,]+) borrowed,', 'dense repack'),
    ('dense repack exact',
     r'([\d,]+) exact from the mod', 'dense repack'),
    ('page cap fields',
     r"PAGE CAP .*?: ([\d,]+) field\(s\) had a page split", 'page cap'),
    ('page cap pages',
     r'had a page split, ([\d,]+) page\(s\) added', 'page cap'),
    ('page cap tiles',
     r'page\(s\) added, ([\d,]+) tile\(s\) repointed\. Worst', 'page cap'),
    ('page cap worst',
     r'Worst page held ([\d,]+) tiles', 'page cap'),
    # Build 34 called this "SINGLE-SCREEN HARD CAP"; FINDINGS-123 renamed it.
    # Both spellings are matched so a 34 -> 35 diff still lines up instead of
    # reporting the counter as vanished and reappeared.
    ('window cap fields',
     r'(?:WINDOW CAP|SINGLE-SCREEN HARD CAP): ([\d,]+) field\(s\)',
     'page cap'),
    ('window cap pages',
     r'(?:WINDOW CAP|SINGLE-SCREEN HARD CAP): [\d,]+ field\(s\), '
     r'([\d,]+) page\(s\) added', 'page cap'),
    ('uncappable fields',
     r'page cap: ([\d,]+) field\(s\) could not be capped', 'page cap'),
    ('palette clamp tiles',
     r'PALETTE CLAMP: ([\d,]+) tile\(s\) in', 'palette clamp'),
    ('palette clamp fields',
     r'PALETTE CLAMP: [\d,]+ tile\(s\) in ([\d,]+) field', 'palette clamp'),
    ('green lsb texels',
     r'GREEN-LSB BACKSTOP: ([\d,]+) truecolor texel', 'field background'),
    ('truecolor rescaled',
     r'([\d,]+) truecolor page\(s\) in [\d,]+ field\(s\) rescaled',
     'field background'),
    ('warning lines', None, 'any'),
)


def extract(text):
    """{counter name: int} for everything this log mentions."""
    out = {}
    for name, pat, _owner in COUNTERS:
        if pat is None:
            continue
        m = re.search(pat, text, re.S)
        if m:
            try:
                out[name] = int(m.group(1).replace(',', ''))
            except ValueError:
                pass
    # THE GUARD MUST NOT COUNT ITSELF. Its own findings are written with `!!`,
    # so a build where it fired reported three MORE warning lines than one
    # where it stayed quiet -- it inflated the very metric it monitors, and
    # then flagged the inflation. Seen between builds 35 and 36: 93 -> 96,
    # exactly the three `!!` lines it had just written.
    out['warning lines'] = len([
        ln for ln in re.findall(r'^\s*!.*$', text, re.M)
        if not ln.lstrip().startswith('!!')])
    return out


def _owner(name):
    for n, _p, o in COUNTERS:
        if n == name:
            return o
    return 'any'


def compare(old, new, expect=()):
    """
    (unexpected, expected) -- each a list of (name, old, new).

    A counter whose owner is in `expect` is allowed to move. Everything else
    is reported. A counter that appears or disappears entirely is reported
    either way, because that is usually a renamed log line and the reader
    needs to know the diff is no longer comparing like with like.
    """
    expect = set(expect)
    unexpected, expected = [], []
    for name in sorted(set(old) | set(new)):
        a, b = old.get(name), new.get(name)
        if a == b:
            continue
        (expected if _owner(name) in expect else unexpected).append(
            (name, a, b))
    return unexpected, expected


class CounterGuard:
    """Tees a log callable, then reports on the counters at `finish()`."""

    def __init__(self, log, expect=None, path=None, label=''):
        self._log = log
        self._lines = []
        self.expect = set(EXPECTED_MOVEMENT if expect is None else expect)
        self.path = path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'build_counters.json')
        self.label = label

    def log(self, text=''):
        self._lines.append(str(text))
        self._log(text)

    __call__ = log

    def text(self):
        return '\n'.join(self._lines)

    def finish(self):
        """Compare against the previous build and write this one's counters."""
        new = extract(self.text())
        if not new:
            return
        old = None
        try:
            with open(self.path) as fh:
                old = json.load(fh).get('counters')
        except Exception:                                      # noqa: BLE001
            old = None
        try:
            with open(self.path, 'w') as fh:
                json.dump({'label': self.label, 'counters': new}, fh, indent=1)
        except Exception:                                      # noqa: BLE001
            pass
        if not old:
            self._log('  counter guard: no previous build to compare against; '
                      'this build is now the baseline.')
            return
        unexpected, expected = compare(old, new, self.expect)
        if expected:
            self._log('  counter guard: expected movement -- '
                      + ', '.join(f'{n} {a} -> {b}' for n, a, b in expected))
        if not unexpected:
            self._log('  counter guard: every other counter identical to the '
                      'previous build.')
            return
        self._log('  !! COUNTER GUARD: %d counter(s) moved that this change '
                  'should not have touched.' % len(unexpected))
        for n, a, b in unexpected:
            self._log(f'  !!   {n}: {a} -> {b}')
        self._log('  !! This change is NOT isolated. Read these before '
                  'testing on hardware -- HANDOFF-121 3.6 is exactly this '
                  'failure, and it sat in the log for four builds.')


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print(__doc__.strip().split('USAGE')[-1])
        return 2
    old = extract(open(argv[0], errors='replace').read())
    new = extract(open(argv[1], errors='replace').read())
    unexpected, _ = compare(old, new, expect=())
    w = max((len(n) for n in set(old) | set(new)), default=10)
    print(f'{"counter":<{w}}  {"old":>12}  {"new":>12}')
    for name in sorted(set(old) | set(new)):
        a, b = old.get(name), new.get(name)
        flag = '' if a == b else '   <-- MOVED'
        print(f'{name:<{w}}  {str(a):>12}  {str(b):>12}{flag}')
    print(f'\n{len(unexpected)} counter(s) moved.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
