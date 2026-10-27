#!/usr/bin/env python3
"""tests/test_cave_relocation.py -- a relocated cave must execute identically.

BUILD 383. The dispatcher first-frame scalers used to be appended to the
2,464-byte tail gap between .text and .rodata, because build_cave's docstring
said ".text has no internal padding to borrow from". That stopped being true
when the verified hole allocator landed: there are ~7,600 usable words of
inter-function padding, twelve times the whole tail gap, and the throttle
wrappers have been chained through it for builds.

Moving them means the cave's words are no longer adjacent, so every internal
label has to resolve against the REAL scattered address of each word. The
failure mode if one does not is silent and terrible: a `b.eq` computed against
a pretend contiguous layout lands in the middle of an unrelated function.

These tests are the guard. The central one takes the SAME builder, runs it on a
contiguous layout and on a deliberately hostile scattered one, and asserts the
two decode to the same instruction sequence -- same mnemonics, same registers,
and every branch pointing at the same LOGICAL word index. A `cave + 4 * i` that
escaped the conversion fails it immediately.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import a64 as A                                                # noqa: E402
import ff7nx_cave as C                                         # noqa: E402
import ff7nx_dispatch as D                                     # noqa: E402

capstone = pytest.importorskip('capstone')

try:
    import ff7nx_dispatch_sites as S
except ImportError:                                            # pragma: no cover
    S = None

try:
    import ff7nx_shared_prologue as SP
except ImportError:                                            # pragma: no cover
    SP = None

pytestmark = pytest.mark.skipif(S is None, reason='needs the sites file')

BASE = 0x0800_0000          # a plausible .text address for the contiguous run


def _md():
    return capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)


WINDOW = 0x80000            # emit_laid_out's span -- the allocator's guarantee


def _scatter(n, start=BASE, window=WINDOW):
    """
    The most hostile layout the allocator can actually produce: every word in
    its own hole, spread as far apart as the `window`-byte span allows.

    Real holes are 2-3 words and average ~2.4 KB apart, so one word per hole
    at maximum spacing is strictly worse than anything a build will see. It
    must still be LEGAL, though: emit_laid_out confines a cave to one
    `window`-byte window precisely so its `b.cond`/`cbz` stay inside their
    +/-1 MB reach. Spreading wider than that tests a layout the allocator
    would never hand out, and a64's range check rightly refuses it -- as this
    function found out when it first used a flat 8 KB stride and pushed
    effect10's 162-word cave over 1.3 MB.
    """
    stride = ((window // max(n, 1)) & ~3) or 4
    addrs = [start + stride * i for i in range(n)]
    assert addrs[-1] - addrs[0] < window, (n, stride)
    return addrs


def _decode(words, addrs):
    """
    (mnemonic, operands-with-branch-targets-as-word-indices) per word.

    Branch targets are rewritten from absolute addresses to the index of the
    word they land on, so a contiguous and a scattered layout of the SAME
    logical cave compare equal.
    """
    md = _md()
    where = {va: i for i, va in enumerate(addrs)}
    out = []
    for i, (w, va) in enumerate(zip(words, addrs)):
        ins = list(md.disasm(w.to_bytes(4, 'little'), va))
        if not ins:
            out.append(('<undecodable>', '%08X' % w))
            continue
        x = ins[0]
        ops = x.op_str
        # Any absolute #0x... operand that is a branch target inside the cave
        # becomes "word[k]"; one that points OUTSIDE the cave keeps its
        # distance from the entry, which must also match between layouts.
        toks = []
        for t in ops.replace(',', ' , ').split(' '):
            if t.startswith('#0x') and x.mnemonic[0] in 'bct':
                tv = int(t[1:], 16)
                toks.append('word[%d]' % where[tv] if tv in where
                            else 'extern:0x%X' % tv)
            else:
                toks.append(t)
        out.append((x.mnemonic, ' '.join(toks)))
    return out


def _dispatcher_args(tag):
    d = S.DISPATCH_SITES[tag]
    sym = {n: va for n, va, _v, _g in d['cases']}
    cases = [(n, s, g) for n, _v, s, g in d['cases']]
    return d, sym, cases


DISPATCHERS = [t for t in ('effect10', 'effect100', 'camera')
               if S and t in S.DISPATCH_SITES
               and S.DISPATCH_SITES[t].get('flag') is not None]


# ------------------------------------------------------ the central property

@pytest.mark.parametrize('tag', DISPATCHERS)
def test_build_cave_is_layout_independent(tag):
    d, sym, cases = _dispatcher_args(tag)

    def build(entry, addr):
        return D.build_cave(entry, addr, d['disp_hook'], d['flag'],
                            d['mask_bits'], d['data_base'], d['stride'],
                            d['fields'], cases, sym, 4)

    flat = build(BASE, None)
    n = len(flat)
    addrs = _scatter(n)
    spread = build(addrs[0], lambda i: addrs[i])

    assert len(spread) == n, 'word count must not depend on placement'
    assert _decode(flat, [BASE + 4 * i for i in range(n)]) \
        == _decode(spread, addrs), \
        '%s: contiguous and scattered layouts differ' % tag


@pytest.mark.parametrize('tag', DISPATCHERS)
def test_no_word_is_byte_identical_by_accident(tag):
    """
    Sanity on the test itself: the two layouts must NOT produce identical
    words. If they did, the comparison above would pass even for a builder
    that ignores `addr` entirely.
    """
    d, sym, cases = _dispatcher_args(tag)

    def build(entry, addr):
        return D.build_cave(entry, addr, d['disp_hook'], d['flag'],
                            d['mask_bits'], d['data_base'], d['stride'],
                            d['fields'], cases, sym, 4)

    flat = build(BASE, None)
    addrs = _scatter(len(flat))
    spread = build(addrs[0], lambda i: addrs[i])
    assert flat != spread, ('%s: the scattered build produced identical words '
                           '-- addr is being ignored' % tag)


@pytest.mark.skipif(SP is None, reason='needs the shared prologue')
@pytest.mark.parametrize('tag', DISPATCHERS)
def test_build_cave_shared_is_layout_independent(tag):
    d, sym, cases = _dispatcher_args(tag)
    entry_va, shared_va = 0x1160000, 0x0900000

    def build(entry, addr):
        return SP.build_cave_shared(entry, addr, d['disp_hook'],
                                    d['mask_bits'], entry_va, shared_va,
                                    d['data_base'], d['stride'], d['fields'],
                                    cases, sym, 4)

    flat = build(BASE, None)
    n = len(flat)
    addrs = _scatter(n)
    spread = build(addrs[0], lambda i: addrs[i])

    assert len(spread) == n
    assert flat != spread, 'addr is being ignored'
    assert _decode(flat, [BASE + 4 * i for i in range(n)]) \
        == _decode(spread, addrs), \
        '%s: shared-prologue cave differs between layouts' % tag


@pytest.mark.skipif(SP is None, reason='needs the shared prologue')
def test_the_stub_proceed_path_follows_the_scattered_layout(tag='effect100'):
    """
    The one place the conversion could go wrong without any branch looking
    odd: the stub's PROCEED fall-through used to be `cave + 4*stub_len`.
    Scattered, that address belongs to some other function.
    """
    d, sym, cases = _dispatcher_args(tag)
    addrs = _scatter(200)
    w = SP.build_cave_shared(addrs[0], lambda i: addrs[i], d['disp_hook'],
                             d['mask_bits'], 0x1160000, 0x0900000,
                             d['data_base'], d['stride'], d['fields'],
                             cases, sym, 4)
    stub_len = SP.stub_word_count()
    md = _md()
    last = list(md.disasm(w[stub_len - 1].to_bytes(4, 'little'),
                          addrs[stub_len - 1]))[0]
    assert last.mnemonic == 'b'
    assert int(last.op_str[1:], 16) == addrs[stub_len], \
        'PROCEED jumped to the contiguous address, not the real next word'


# ------------------------------------------------------- allocator contract

def test_link_chains_every_run_so_fall_through_is_preserved():
    """
    build_cave relies on fall-through (`_plan_dispatch` drops the last `b OUT`
    of each run on purpose). Chaining must therefore make the runs execute in
    order with nothing skipped and nothing else clobbered.
    """
    runs = [(0x1000, 3), (0x8000, 3), (0x20000, 2)]
    words = [0xAAAA0000 + i for i in range(6)]
    out = C.link(runs, words)
    # 2 + 2 + 2 payload words, plus one chaining branch after each of the
    # first two runs.
    assert len(out) == 8
    md = _md()
    order = []
    for va in sorted(out):
        ins = list(md.disasm(out[va].to_bytes(4, 'little'), va))
        if ins and ins[0].mnemonic == 'b':
            order.append(('b', int(ins[0].op_str[1:], 16)))
        else:
            order.append(('w', out[va]))
    # Walking from the entry must visit all six payload words in order.
    seen, va = [], runs[0][0]
    while True:
        w = out[va]
        ins = list(md.disasm(w.to_bytes(4, 'little'), va))
        if ins and ins[0].mnemonic == 'b':
            va = int(ins[0].op_str[1:], 16)
            continue
        seen.append(w)
        if len(seen) == len(words):
            break
        va += 4
    assert seen == words


def test_chaining_branches_never_overwrite_a_payload_word():
    runs = [(0x1000, 3), (0x8000, 3)]
    words = [1, 2, 3, 4]
    out = C.link(runs, words)
    assert out[0x1000] == 1 and out[0x1004] == 2
    assert out[0x1008] == A.b(0x1008, 0x8000)     # the chain, not a payload
    assert out[0x8000] == 3 and out[0x8004] == 4


def test_a_builder_that_ignores_addr_is_rejected_by_emit_laid_out():
    """emit_laid_out re-checks the word count; prove the guard fires."""
    class FakePool:
        free = [(0x1000 + 0x40 * i, 3) for i in range(40)]
        used = []

        def take(self, n, span=None):
            runs, need = [], n
            for va, ln in self.free:
                if need <= 0:
                    break
                runs.append((va, ln))
                need -= ln - 1
            return runs

    def unstable(entry, addr):
        # word count depends on where it lands -- illegal
        return [0] * (8 if entry == 0 else 9)

    with pytest.raises(ValueError, match='must not depend on where it lands'):
        C.emit_laid_out(FakePool(), unstable)


# --------------------------------------- against the REAL allocator output

needs_nso = pytest.mark.skipif(
    not any(os.path.exists(os.path.join(_HERE, *p))
            for p in (('exefs', 'main'), ('dump', 'exefs', 'main'))),
    reason='needs the stock module')


def _real_pool():
    import nxmap
    import cave_space
    for p in (('exefs', 'main'), ('dump', 'exefs', 'main')):
        path = os.path.join(_HERE, *p)
        if os.path.exists(path):
            break
    m = nxmap.Main(path)
    pool = C.HolePool(m.raw[0], starts=set(m.arm_starts),
                      named=cave_space.named_targets(m.img[cave_space.RODATA:]))
    return m, pool


@needs_nso
@pytest.mark.parametrize('tag', DISPATCHERS)
def test_walking_the_real_placed_layout_executes_the_contiguous_stream(tag):
    """
    The end-to-end property, against holes the allocator actually chose.

    Place the cave for real, then start at its entry and FOLLOW it the way the
    CPU would -- stepping word by word and taking each chaining `b` -- and
    assert the stream that comes out is the contiguous build's, in order, with
    nothing skipped and nothing executed twice.

    The layout is reproduced here step by step rather than read back out of
    emit_laid_out, because that is the only way to know for certain which
    addresses hold PAYLOAD and which hold a chaining branch. Telling them
    apart by disassembling is not possible -- the cave emits plenty of its own
    `b`s, and one of them is indistinguishable from a chain word.
    """
    _m, pool = _real_pool()
    d, sym, cases = _dispatcher_args(tag)

    def build(entry, addr):
        return D.build_cave(entry, addr, d['disp_hook'], d['flag'],
                            d['mask_bits'], d['data_base'], d['stride'],
                            d['fields'], cases, sym, 4)

    # Exactly what emit_laid_out does, with the intermediate layout kept.
    n = len(build(0, lambda i: 4 * i))
    runs = pool.take(n, span=WINDOW)
    addrs = C.slots(runs, n)
    words = build(addrs[0], lambda i: addrs[i])
    placed = C.link(runs, words)

    assert len(words) == n
    payload = set(addrs)
    chain = set(placed) - payload
    assert payload <= set(placed), 'a payload word was not emitted'
    assert len(addrs) == len(payload), 'the same address was used twice'

    # Walk it like the CPU: payload words execute in sequence; stepping onto a
    # chain word takes its branch.
    md = _md()
    seen, va = [], addrs[0]
    for i in range(n):
        assert va == addrs[i], (
            '%s: word %d should execute at 0x%X, the walk reached 0x%X'
            % (tag, i, addrs[i], va))
        seen.append(placed[va])
        va += 4
        hops = 0
        while va in chain:
            hops += 1
            assert hops < 4, 'chain words must not run into each other'
            ins = list(md.disasm(placed[va].to_bytes(4, 'little'), va))
            assert ins and ins[0].mnemonic == 'b', (
                'chain word at 0x%X is %08X, not a branch' % (va, placed[va]))
            va = int(ins[0].op_str[1:], 16)

    assert seen == words, '%s: the walked stream is not the emitted one' % tag
    flat = build(BASE, None)
    # Each stream is decoded at ITS OWN addresses. Decoding the scattered
    # words as if they sat at BASE would resolve every PC-relative operand
    # against the wrong PC -- which is how this assertion first "failed",
    # reporting an adrp page that no build ever emits.
    assert _decode(seen, addrs) \
        == _decode(flat, [BASE + 4 * i for i in range(n)]), \
        '%s: the walked stream differs from the contiguous build' % tag


@needs_nso
@pytest.mark.parametrize('tag', DISPATCHERS)
def test_every_word_lands_in_verified_zero_padding(tag):
    """
    The safety property the allocator exists for: nothing is written over
    live code. Re-checked against the stock image, not trusted from the scan.
    """
    m, pool = _real_pool()
    d, sym, cases = _dispatcher_args(tag)
    _entry, placed = C.emit_laid_out(
        pool, lambda cave, addr: D.build_cave(
            cave, addr, d['disp_hook'], d['flag'], d['mask_bits'],
            d['data_base'], d['stride'], d['fields'], cases, sym, 4))
    import struct as _s
    nonzero = [hex(va) for va in placed
               if _s.unpack_from('<I', m.raw[0], va)[0] != 0]
    assert nonzero == [], '%s would overwrite live words at %s' % (tag, nonzero)


@needs_nso
def test_the_pool_has_room_left_for_echo_s():
    """
    The whole point of the move. After the three scalers and the four
    throttle wrappers, the padding pool must still hold far more than the
    2,464-byte tail gap it replaced.
    """
    _m, pool = _real_pool()
    usable = sum(max(0, n - 1) for _, n in pool.free)
    assert usable > 5000, 'only %d usable words left in the pool' % usable


def test_a_cave_with_no_short_branch_needs_no_window():
    """ANY_SPAN -- the menu calendar regression, as a test.

    The 960 KB window exists for `b.cond`, `cbz` and `adr`, which reach
    +/-1 MB. A cave built only from `bl`, `b` and `ret` reaches +/-128 MB and
    never needed one.

    MEASURED on the shipped module after every other pass had run: 2884 free
    words in 2689 runs, 2534 of them single words, 195 usable -- and no
    960 KB window holding the 105 the menu calendar needs. The day/night
    cycle started installing in build 355 (the facial leak fix freed 46
    words and let it in at 252) and took the dense low runs with it, so the
    calendar began failing with

        NoRoom: no 983040-byte window holds 105 usable word(s)

    and the menu lost its date and time. Unconstrained, those same 195 words
    are reachable.
    """
    import a64 as A
    import ff7nx_cave

    # 30 three-word runs, one every 256 KB: 60 usable words spread over
    # 7.5 MB, so no single 960 KB window holds the 40 this cave needs.
    STEP, N = 0x40000, 30
    holes = [STEP + i * STEP for i in range(N)]
    text = bytearray(holes[-1] + 0x1000)

    def fresh():
        pool = ff7nx_cave.HolePool.__new__(ff7nx_cave.HolePool)
        pool.img = text                   # take() re-checks the words are 0
        pool.free = [(va, 3) for va in holes]
        pool.used = []
        return pool

    def straight(cave, addr):
        return [A.nop()] * 39 + [A.ret()]

    try:
        ff7nx_cave.emit_laid_out(fresh(), straight)
        raise AssertionError('a windowed cave should not have fitted')
    except ff7nx_cave.NoRoom:
        pass

    entry, words = ff7nx_cave.emit_laid_out(fresh(), straight,
                                            span=ff7nx_cave.ANY_SPAN)
    assert entry in holes, hex(entry)
    assert len(words) >= 40, len(words)
    print('  ok    a branch-free cave places with ANY_SPAN and not without')
