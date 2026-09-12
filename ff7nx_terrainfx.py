"""Kujata's projected floor is 2D content and needs the 4:3 -> 16:9 widening.

Kujata submits its animated field as PRE-TRANSFORMED screen-space primitives
from two of its own producers:

    x86 0x4FC048  / ARM +0x45E144   the animated grid -- the surface that
                                    appears the moment the field "becomes an
                                    animated field" (0x4FB963 calls it for the
                                    first 0x2D ticks)
    x86 0x4FBBAC  / ARM +0x460234   the captured-terrain triangles

Under `ws-3d` the port's vertex shader multiplies gl_Position.x by
WS_SCALE = 0.75, so anything authored in the 640-unit space lands in the
central 75% of an 854-unit frame.  That is the same mechanism `ff7nx_battlewide`
corrects for the full-screen summon quads, and these two producers need it for
the same reason: the surrounding battlefield is drawn through the widened 3D
frustum and reaches the frame edges, while Kujata's own floor stops 25% short.
Multiplying X by 4/3 after projection cancels the shader's 0.75 exactly.

    HOW WE KNOW, AND WHY IT WAS ONCE RETIRED
    ----------------------------------------
    Build 266 installed this and the report was "no change" -- so it was
    retired as a no-op and build 267 shipped without it.  That report was
    about the BLACK TILES, which build 266 still had (FINDINGS-308) and which
    dominated the picture.  Build 267 removed the black tiles and the missing
    coverage became visible on its own: the field "extends neatly and far" to
    one side and stops at a straight world-space line on the other.  The two
    builds together isolate it -- 266 = wide + black, 267 = narrow + clean --
    so this is kept and the capture fix is kept, and neither substitutes for
    the other.

FINDINGS-308 is the capture half.  Nothing in this file touches the capture,
the UVs, depth, culling, clipping or any other summon.
"""
from __future__ import annotations

import os
import shutil
import struct
import tempfile
from pathlib import Path

import a64 as A
import nxmap

TERRAIN_FX_ENV = 'SEVENTH_NX_TERRAIN_FX'

# First normal instructions after the two private projectors.  The calls
# themselves are PC-relative and must not be moved into a cave.
GRID_HOOK, GRID_HOOK_STOCK = 0x0045E148, 0xB94012E8
MESH_HOOK, MESH_HOOK_STOCK = 0x00460238, 0xB9401668

# x86's homogeneous vertex bank.  The grid writes all four slots.  The mesh
# writes 0, 16, and 48; +32 is a stale fourth-vector slot and is deliberately
# not modified.
VERTEX_BANK = 0x00D8F5F8
GRID_X_OFFSETS = (0, 16, 32, 48)
MESH_X_OFFSETS = (0, 16, 48)
X_SCALE_BITS = 0x3FAAAAAB          # float32(4.0 / 3.0)
TRANSLATE = 0x010FC3A0

# Hardware proved this one ineffective: suppressing Kujata's second, original
# -material submission at x86 0x4FBBAC changed nothing (build 265).  Recognise
# and restore it so no output keeps carrying it.
RETIRED_SECOND_SUBMIT = 0x004608B4
RETIRED_SECOND_SUBMIT_STOCK, RETIRED_SECOND_SUBMIT_FIXED = 0x9419B2AB, 0xD503201F

# KOTR's opening circle, x86 0x478EDF / ARM +0x21A640.  DESTRUCTIVE.  The
# stock word writes w19 into the routine's output; zeroing it does not "make
# the darkening even", it stops the dip being produced at all:
#
#     "that 'fix' to KOTR's circle effect makes the actual field dipping
#      effect 100% vanish. thats why i wasnt seeing uneven darkening. it
#      wasnt darkening at all, wasnt dipping."
#
# Build 265's "the circle looks like it fades evenly" was the effect being
# absent, not being fixed, and the unshaded side wedges it appeared to cure
# are simply what the real effect looks like on this port.  Build 268
# reinstalled it on that misreading and the TV caught it.  Recognise and
# restore, never install.
RETIRED_KOTR_BLEND = 0x0021A640
RETIRED_KOTR_BLEND_STOCK, RETIRED_KOTR_BLEND_FIXED = 0xB9000013, A.str_(A.WZR, 0)

ANCHORS = {
    GRID_HOOK - 4: 0x94195FFB, GRID_HOOK: GRID_HOOK_STOCK,
    GRID_HOOK + 4: 0x11006108,
    MESH_HOOK - 4: 0x94195297, MESH_HOOK: MESH_HOOK_STOCK,
    MESH_HOOK + 4: 0x5100C100,
}


def enabled():
    """OFF by default: X * 4/3 has never been separately observed to do
    anything.

    Build 266 had it and build 268 had it; build 267 did not.  268 changed
    nothing the TV could see over 267, so the coverage difference between 266
    and 267 was the CAPTURE, not this.  It stays in the tree because the cave
    is verified and cheap to A/B -- SEVENTH_NX_TERRAIN_FX=1 turns it on -- but
    it is not shipped on a theory.
    """
    v = os.environ.get(TERRAIN_FX_ENV)
    if v is not None:
        return v not in ('', '0', 'off', 'false', 'no')
    return False


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def _fmt(word):
    return struct.pack('<I', word).hex()


def _b_target(word, va):
    if (word & 0xFC000000) != 0x14000000:
        return None
    off = word & 0x03FFFFFF
    if off & 0x02000000:
        off -= 0x04000000
    return va + off * 4


def _scale_body(offsets, addr):
    """Address-aware, straight-line post-projection X correction."""
    words = [
        A.stp64_pre(29, 30, A.SP, -0x20),
        A.mrs_nzcv(8),
        A.str64(8, A.SP, 0x10),
        A.movz(0, VERTEX_BANK & 0xFFFF),
        A.movk_hi(0, VERTEX_BANK >> 16),
        None,                              # BL TRANSLATE, actual cave address
        A.movz(1, X_SCALE_BITS & 0xFFFF),
        A.movk_hi(1, X_SCALE_BITS >> 16),
        A.fmov_s_from_w(1, 1),
    ]
    words[5] = A.bl(addr(5), TRANSLATE)
    for off in offsets:
        words += [A.ldr_s(0, 0, off), A.fmul_s(0, 0, 1),
                  A.str_s(0, 0, off)]
    # TRANSLATE may clobber LR and NZCV.  The surrounding generated code
    # keeps neither volatile GPRs nor scalar FP registers across its call.
    words += [A.ldr64(8, A.SP, 0x10), A.msr_nzcv(8),
              A.ldp64_post(29, 30, A.SP, 0x20)]
    return words


def _logical_words(offsets, displaced, addr):
    return _scale_body(offsets, addr) + [displaced]


def _walk_installed(img, hook, displaced, offsets):
    """Read back one chained cave and accept only our exact instruction body."""
    entry = _b_target(_word(img, hook), hook)
    if entry is None or not 0 <= entry < len(img):
        return None, 'hook branch is invalid'
    count = len(_logical_words(offsets, displaced, lambda i: 4 * i))
    logical, all_addrs, cur = [], [], entry
    for _ in range(256):
        if len(logical) == count:
            break
        if not 0 <= cur <= len(img) - 4:
            return None, 'cave leaves module'
        got = _word(img, cur)
        target = _b_target(got, cur)
        if target is not None:
            if target == hook + 4:
                return None, 'cave returns before its body'
            all_addrs.append(cur)
            cur = target
        else:
            logical.append(cur)
            all_addrs.append(cur)
            cur += 4
    else:
        return None, 'cave body walk exceeded limit'
    if len(logical) != count:
        return None, 'cave body is truncated'
    expected = _logical_words(offsets, displaced, lambda i: logical[i])
    for va, want in zip(logical, expected):
        if _word(img, va) != want:
            return None, 'cave word +0x%X does not match' % va
    for _ in range(256):
        if not 0 <= cur <= len(img) - 4:
            return None, 'cave tail leaves module'
        target = _b_target(_word(img, cur), cur)
        if target is None:
            return None, 'cave has no return branch'
        all_addrs.append(cur)
        if target == hook + 4:
            return all_addrs, None
        cur = target
    return None, 'cave tail walk exceeded limit'


def _site_state(img, hook, displaced, offsets):
    have = _word(img, hook)
    if have == displaced:
        return 'stock', None, None
    if _b_target(have, hook) is None:
        return 'unknown', None, 'hook is %08X, neither stock nor B' % have
    words, why = _walk_installed(img, hook, displaced, offsets)
    return ('installed', words, None) if words is not None else ('unknown', None, why)


def _patch(name, va, have, want):
    return {'name': name, 'va': hex(va), 'expect': _fmt(have), 'set': _fmt(want)}


def _retire_old_words(img, patches, notes, problems):
    """Restore every word hardware rejected, wherever an output still has it."""
    for va, stock, fixed, name in (
            (RETIRED_SECOND_SUBMIT, RETIRED_SECOND_SUBMIT_STOCK,
             RETIRED_SECOND_SUBMIT_FIXED, 'restore retired Kujata second pass'),
            (RETIRED_KOTR_BLEND, RETIRED_KOTR_BLEND_STOCK,
             RETIRED_KOTR_BLEND_FIXED, 'restore KOTR circle dip (undo 265/268)')):
        have = _word(img, va)
        if have not in (stock, fixed):
            problems.append('%s +0x%X is %08X, expected %08X or %08X' %
                            (name, va, have, stock, fixed))
        elif have == fixed:
            patches.append(_patch(name, va, have, stock))
            notes.append('    %s' % name)


def _install_cave(pool, img, hook, displaced, offsets):
    """Allocate a chained cave, encoding BL from each real slot address."""
    import ff7nx_cave
    n = len(_scale_body(offsets, lambda i: 4 * i)) + 2
    runs = pool.take(n)
    slots = ff7nx_cave.slots(runs, n)
    words = _logical_words(offsets, displaced, lambda i: slots[i])
    words.append(A.b(slots[-1], hook + 4))
    placed = ff7nx_cave.link(runs, words)
    placed[hook] = A.b(hook, slots[0])
    return placed, slots[0]


def plan(m, revert=False):
    """Return the exact NSO patch plan without writing it."""
    import cave_space
    import ff7nx_cave

    img = m.img
    patches, notes, problems = [], [], []
    for va, expected in ANCHORS.items():
        if va not in (GRID_HOOK, MESH_HOOK) and _word(img, va) != expected:
            problems.append('anchor +0x%X is %08X, expected %08X' %
                            (va, _word(img, va), expected))
    gs, gw, ge = _site_state(img, GRID_HOOK, GRID_HOOK_STOCK, GRID_X_OFFSETS)
    ms, mw, me = _site_state(img, MESH_HOOK, MESH_HOOK_STOCK, MESH_X_OFFSETS)
    if gs == 'unknown':
        problems.append('Kujata animated-grid cave: %s' % ge)
    if ms == 'unknown':
        problems.append('Kujata captured-mesh cave: %s' % me)
    if gs != ms:
        problems.append('Kujata projection caves are in mixed states')
    _retire_old_words(img, patches, notes, problems)
    if problems:
        return [], [], problems

    if revert:
        if gs == 'installed':
            for hook, stock, words, name in (
                    (GRID_HOOK, GRID_HOOK_STOCK, gw, 'animated-grid'),
                    (MESH_HOOK, MESH_HOOK_STOCK, mw, 'captured-mesh')):
                patches.append(_patch('restore Kujata %s hook' % name, hook,
                                      _word(img, hook), stock))
                for va in words:
                    patches.append(_patch('clear Kujata %s cave' % name, va,
                                          _word(img, va), 0))
            notes.append('    Kujata projected-floor coverage restored to stock')
        return patches, notes, []

    if gs == 'installed':
        notes.append('    Kujata projected-floor coverage already installed')
        return patches, notes, []

    pool = ff7nx_cave.HolePool(
        img, starts=set(m.arm_starts),
        named=cave_space.named_targets(img[cave_space.RODATA:]))
    try:
        gp, ge = _install_cave(pool, img, GRID_HOOK, GRID_HOOK_STOCK,
                               GRID_X_OFFSETS)
        mp, me = _install_cave(pool, img, MESH_HOOK, MESH_HOOK_STOCK,
                               MESH_X_OFFSETS)
    except ff7nx_cave.NoRoom as exc:
        return [], [], ['Kujata coverage cave allocation: %s' % exc]
    for label, patchset in (('animated-grid', gp), ('captured-mesh', mp)):
        for va, want in sorted(patchset.items()):
            suffix = ' hook' if va in (GRID_HOOK, MESH_HOOK) else ''
            patches.append(_patch('Kujata %s cave%s' % (label, suffix), va,
                                  _word(img, va), want))
    notes += [
        '    Kujata animated grid X: 4/3 after projection (cave +0x%X)' % ge,
        '    Kujata captured terrain X: 4/3 after projection (cave +0x%X)' % me,
    ]
    return patches, notes, []


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'Kujata projected-floor coverage correction',
                  'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.terrainfx-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def apply_all(main, revert=False, log=print):
    m = nxmap.Main(str(main))
    patches, notes, problems = plan(m, revert=revert)
    if problems:
        for problem in problems:
            log('  ! ' + problem)
        log('  refusing to write Kujata projected-floor correction.')
        return 1
    log('  Kujata projected-floor coverage correction:')
    for note in notes:
        log(note)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    _write(main, patches, log)
    log('  %d Kujata coverage word(s) written' % len(patches))
    return 0


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('main')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--revert', action='store_true')
    args = parser.parse_args()
    raise SystemExit(apply_all(args.main, revert=args.revert))
