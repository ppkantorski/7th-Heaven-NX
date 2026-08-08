
"""
Place a code cave in the dead alignment padding between functions, so a new
patch costs the 60 FPS budget nothing at all.

WHY
---
Caves are appended into the 2,464-byte gap between .text and .rodata. The
shipping 60 FPS preset uses 2,460. There is no room for anything else and no
way to enlarge the gap -- .rodata's address is baked into every adrp that
reaches it.

But the recompiled functions are 16-byte aligned, so nearly all of them end
in 1-3 words of zero padding: ~62 KB of it that passes every safety test
cave_space.py applies. A cave chained through those holes takes nothing from
the tail gap, which is the point: 60 FPS keeps every byte it has today and
does not have to be touched, weakened or re-verified.

THE COST
--------
Holes are 2-3 words, so a cave is cut into runs with a `b` at the end of
each. `b` is PC-relative +/-128 MB, reaches anywhere in an 18 MB .text, and
alters no register or flag -- so a chained cave behaves exactly like a
contiguous one. Overhead is one word per hole.

CAVES WITH INTERNAL CONTROL FLOW
--------------------------------
A straight-line cave does not care where its runs land. One with its own
branches does: `b.cond`, `cbz` and `adr` reach only +/-1 MB, and holes are
spread over 18 MB of .text. `take(n, span=...)` therefore confines a cave to
one address window, and `emit_laid_out()` hands the builder the REAL address
of each of its words so labels resolve against the scattered layout rather
than a pretend contiguous one. a64's branch encoders range-check, so a cave
whose window was still too wide fails the build instead of encoding a branch
into the wrong function.

A window of 512 KB keeps every internal displacement under half the +/-1 MB
limit and, measured on the stock 1.0.3 module, still offers 696 usable words
-- about six times what the largest cave here needs.

WHAT A CHAINED CAVE MAY NOT CONTAIN
-----------------------------------
Data. A lookup table has to be contiguous to be indexed, and the biggest hole
is three words. Tables belong in .rodata, reached with adrp+add (+/-4 GB), the
way the shared-prologue descriptor table already is.

SAFETY
------
Allocation only ever uses holes that:

  * are entirely zero IN THE MODULE BEING PATCHED, re-checked at allocation
    time rather than trusted from an earlier scan;
  * are preceded by `ret`;
  * are followed by a known function start;
  * have no direct branch anywhere in .text landing inside them.

The first is the one that matters for correctness across builds: if another
patch got there first, the hole is not zero any more and is skipped.
"""
import bisect
import struct

import a64 as A
import cave_space

RET = 0xD65F03C0


class NoRoom(Exception):
    pass


class HolePool:
    """Verified padding holes in one module image, allocated front to back."""

    def __init__(self, img, holes=None, starts=None, named=None):
        self.img = img
        if holes is None:
            holes, _ = cave_space.find_holes_in(img, starts, named)
        # Biggest first: a 3-word hole wastes proportionally less on its
        # branch than a 2-word one, so spending them first keeps the chain
        # short and leaves the least useful holes for last.
        self.free = sorted(holes, key=lambda h: (-h[1], h[0]))
        self.used = []

    def _still_zero(self, va, words):
        return all(struct.unpack('<I', self.img[va + 4 * k:va + 4 * k + 4])[0] == 0
                   for k in range(words))

    def _window(self, n_words, span):
        """
        The lowest-addressed `span`-byte window holding enough usable words.

        Lowest rather than densest so a build is reproducible: the same module
        and the same cave always land in the same place, which is what makes
        the .text diff report readable across builds.
        """
        free = sorted(self.free)
        vas = [va for va, _ in free]
        for i, (start, _) in enumerate(free):
            j = bisect.bisect_right(vas, start + span)
            usable = sum(max(0, ln - 1) for _, ln in free[i:j])
            if usable >= n_words:
                return set(free[i:j])
        raise NoRoom('no %d-byte window holds %d usable word(s); the widest '
                     'has fewer' % (span, n_words))

    def take(self, n_words, span=None):
        """
        Reserve room for `n_words` instructions and return the runs as
        [(va, capacity), ...]. Capacity excludes the word each run spends on
        its outgoing branch, except the last run which needs no branch out
        (the cave's own final instruction is already a branch back).

        `span`, if given, confines the whole cave to one window that many
        bytes wide, so its own `b.cond`/`cbz` stay inside their +/-1 MB reach.
        """
        window = self._window(n_words, span) if span else None
        runs, need = [], n_words
        for va, ln in list(self.free):
            if need <= 0:
                break
            if window is not None and (va, ln) not in window:
                continue
            if not self._still_zero(va, ln):
                self.free.remove((va, ln))
                continue
            # every run but the last gives up one word to `b next`
            cap = ln - 1
            if cap <= 0:
                continue
            self.free.remove((va, ln))
            self.used.append((va, ln))
            take = min(cap, need)
            runs.append((va, ln))
            need -= take
            if need <= 0:
                # the final run may not need its whole branch word
                break
        if need > 0:
            raise NoRoom('need %d more word(s); %d hole(s) left'
                         % (need, len(self.free)))
        return runs


def slots(runs, n_words):
    """
    The address of each of a cave's `n_words` words under a run layout.

    Every run but the last keeps its final word for the `b` to the next one;
    the last run has no branch out because the cave's own last instruction is
    already a branch back to the game.
    """
    out = []
    for k, (va, ln) in enumerate(runs):
        cap = ln if k == len(runs) - 1 else ln - 1
        for j in range(min(cap, n_words - len(out))):
            out.append(va + 4 * j)
        if len(out) >= n_words:
            break
    if len(out) != n_words:
        raise NoRoom('layout holds %d of %d word(s)' % (len(out), n_words))
    return out


def link(runs, words):
    """{address: word} for `words` laid out over `runs`, chaining included."""
    out, i = {}, 0
    for k, (va, ln) in enumerate(runs):
        last_run = (k == len(runs) - 1)
        cap = ln if last_run else ln - 1
        take = min(cap, len(words) - i)
        for j in range(take):
            out[va + 4 * j] = words[i + j]
        i += take
        if not last_run:
            out[va + 4 * take] = A.b(va + 4 * take, runs[k + 1][0])
        if i >= len(words):
            break
    if i != len(words):
        raise NoRoom('laid out %d of %d words' % (i, len(words)))
    return out


def emit_laid_out(pool, build, span=0x80000):
    """
    Place a cave that has its own internal branches.

    `build(entry_va, addr)` must return the cave's word list, using `addr(i)`
    as the address of its i'th word so every label resolves against the real
    scattered layout. It is called twice with an identical instruction
    sequence: once on a pretend contiguous layout purely to count the words,
    then again on the layout it actually got. A builder whose word COUNT
    depends on the addresses would break that, and is caught -- the second
    call's length is checked against the first.

    Returns (entry_va, {address: word}).
    """
    probe = build(0, lambda i: 4 * i)
    n = len(probe)
    runs = pool.take(n, span=span)
    addrs = slots(runs, n)
    words = build(addrs[0], lambda i: addrs[i])
    if len(words) != n:
        raise ValueError('cave builder emitted %d words on the real layout '
                         'and %d on the probe -- its instruction count must '
                         'not depend on where it lands' % (len(words), n))
    return addrs[0], link(runs, words)


def emit_chained(pool, words):
    """
    Lay `words` (a straight-line instruction list whose LAST word is already
    a branch out) across padding holes.

    Returns (entry_va, {va: word}) -- the address to branch to, and every
    word to write. Raises NoRoom rather than silently truncating.
    """
    if not words:
        raise ValueError('empty cave')
    runs = pool.take(len(words))
    return runs[0][0], link(runs, words)


def hook(site_va, displaced, body):
    """
    Build the full word list for a cave that replaces ONE instruction at
    `site_va`.

    `displaced` must be the original word at `site_va`, and must not be
    PC-relative -- an adrp/adr/b/bl executed from a hole computes a
    different address than it did at the site. Callers pick a site whose
    instruction is position-independent; this refuses the obvious offenders
    rather than trusting them to.
    """
    if (displaced & 0x1F000000) == 0x10000000:
        raise ValueError('displaced instruction %08X is adrp/adr -- '
                         'PC-relative, pick another hook site' % displaced)
    if (displaced & 0x7C000000) == 0x14000000:
        raise ValueError('displaced instruction %08X is a branch' % displaced)
    return list(body) + [displaced, 0]      # final 0 patched by finish()


def finish(words, entry_va, site_va):
    """Patch the placeholder tail branch now that the layout is known."""
    words = list(words)
    # The tail branch sits at whatever address the last word landed on, which
    # emit_chained knows and we do not -- so it is emitted as a `b` computed
    # from the LAST run. Callers use emit_hooked() instead of doing this by
    # hand.
    raise NotImplementedError('use emit_hooked')


def emit_hooked(pool, site_va, displaced, body, return_va=None):
    """
    The whole job: build a cave that runs `body`, then the displaced
    instruction, then returns to `return_va` (default: the word after the
    hook site). Returns (patches, entry_va) where `patches` maps address ->
    word and already includes the `b cave` written over the hook site.
    """
    return_va = site_va + 4 if return_va is None else return_va
    tail = hook(site_va, displaced, body)          # body + displaced + [0]
    # Lay it out once to learn where the final word lands, then rewrite that
    # word as the real return branch. Laying out twice against a fresh pool
    # would pick different holes, so the pool is snapshotted and restored.
    snapshot = (list(pool.free), list(pool.used))
    entry, out = emit_chained(pool, tail)
    last_va = max(out)
    # `last_va` is the placeholder 0 only if no branch was appended after it;
    # emit_chained never appends past the final word, so it is.
    pool.free, pool.used = snapshot
    tail[-1] = A.b(last_va, return_va)
    entry, out = emit_chained(pool, tail)
    out[site_va] = A.b(site_va, entry)
    return out, entry
