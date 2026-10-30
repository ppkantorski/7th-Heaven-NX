"""BUILD 858 -- the field interpreter's tick budget (8 opcodes an actor a
tick; WAIT and BACK end a tick early) and palette rewrites that keep the
vanilla script's timing.

onna_4 (hardware 10-28, "lights that are rapidly flickering"): BUILD 854's
8-opcode pair switch for palette 14 ran out of the tick lg23+ shares with its
var5[6] step, so for one tick a pass the lamps' layer was off and each pass
took a tick longer. The actors below are onna_4's own vanilla lg1 and lg23+
bytes in a one-actor script section.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import struct

import ff7nx_palauto as PA
import ff7nx_ticksim as TS

# onna_4 vanilla, script 0 of lg1 (palette 9) and lg23+ (palettes 12, 14)
LG1 = bytes.fromhex(
    '805004ff8050000000e50009007f240200e900555000080404047fe700000800e6000809'
    '7f145000000013145004f60207875004011005805000011011145004ff03078550040110'
    '0580500000123c00')
LG23 = bytes.fromhex(
    '805005ff805006ff805001008050020000e5000e20ff240400e600200fffe5000c20ff24'
    '0400e90055502030050505ffe600300cff240100e7002030ffe5000f20ff240100e60030'
    '0fff240100145001000013145005f80207875005021005805001011011145005fe030785'
    '500502100580500100e90055502030060606ffe600300eff240100e7002030ffe5000f20'
    'ff240100e600300fff240100145002000013145006f40207875006011005805002011011'
    '145006ff030785500601100580500200129e00')


def section(body):
    """A one-actor script section: header, name, routine table (script 0 =
    body, the other 31 a bare RET), code, an empty string table."""
    code_start = 32 + 8 + 64
    code = body + b'\x00'
    so = code_start + len(code)
    hdr = struct.pack('<HBBHHH6x8s8s', 0x0502, 1, 0, so, 0, 512,
                      b'test', b'test')
    table = struct.pack('<32H', code_start,
                        *([code_start + len(body)] * 31))
    return hdr + b'lg\0\0\0\0\0\0' + table + code + b'\x00\x00'


def _site(sec0, op_value, pal):
    """(offset, length) of the ADPAL .. LDPAL `pal` run."""
    st = [(o, op, z) for _a, o, op, z in PA._stream(sec0)]
    for i, (o, op, z) in enumerate(st):
        if op == op_value:
            for o2, op2, z2 in st[i + 1:]:
                if op2 == 0xE6 and sec0[o2 + 3] == pal:
                    return o, o2 + z2 - o
                if op2 not in (0xE6, 0xE7, 0xE5):
                    break
    raise AssertionError('no site')


def _plan_multi(sec0, var, site, block, prev, kv_code):
    """renumber var onto `block` and rewrite `site` with `kv_code`
    (site_multi or an old padded encoding)."""
    so, sl = site
    uses = PA.var_uses(sec0, var, skip={so})
    step, _a, lo, hi, _s = PA.lattice(sec0, uses)
    n = (hi - lo) // step + 1
    new0 = PA.renumber(sec0, uses, lo, step, n, base=block[0])
    stream = PA._stream(sec0)
    kv = len(PA._ops_in(stream, so, sl))
    code, later = kv_code(kv, var, prev, sl)
    edits = [(so, sl, code)]
    if later:
        edits.append((PA._after_next_wait(stream, so, so + sl), 0, later))
    out = PA.grow(new0, edits)
    pr = {'q': 14, 'mode': 'multi', 'block': block, 'var': var,
          'sites': [site], 'gate': None,
          'states': [x & 255 for x in range(lo, hi + 1, step)]}
    return out, edits, pr


def _new(kv, var, prev, _sl):
    return PA.site_multi(kv, var, prev)


def _old(_kv, var, prev, sl):
    return PA.bg_multi(sl, var, prev, 7), b''


def test_tick_model_vanilla_lg23_pass_is_seven_ticks():
    s = section(LG23)
    tr = TS.run(s, 104, len(s), 200, watch=[(5, 6)])
    loads = [t for t, (ran, _b) in enumerate(tr)
             if any(op == 0xE6 and s[o + 3] == 0x0E for o, op, _v in ran)]
    gaps = {b - a for a, b in zip(loads, loads[1:])}
    assert gaps == {7}
    # the tick of the palette-14 load is the full 8 opcodes, WAIT last
    t = loads[1]
    assert len(tr[t][0]) == 8 and tr[t][0][-1][1] == 0x24


def test_854_pair_switch_tears_the_tick():
    """Palette 14 in 854's pair mode: the switch's BGCLR runs, the tick
    ends, the lamps' layer is off for a tick; each pass takes 8 ticks."""
    s = section(LG23)
    so, sl = _site(s, 0xE9, 0x0E)
    var, prev, tb, pb = 6, 0xFD, 0xFC, 5
    uses = PA.var_uses(s, var, skip={so})
    step, _a, lo, hi, _x = PA.lattice(s, uses)
    n = (hi - lo) // step + 1
    block = [58, 59, 60]
    new0 = PA.renumber(s, uses, lo, step, n, base=4 * block[0])
    edits = [(so, sl, PA.pair_code(var, prev, tb, pb))]
    out = PA.grow(new0, edits)
    pr = {'q': 14, 'mode': 'pair', 'block': block, 'pb': pb, 'var': var,
          'sites': [(so, sl)], 'gate': None,
          'states': [x & 255 for x in range(lo, hi + 1, step)]}
    why = PA.tick_proof(s, out, edits, pr, ticks=400)
    assert why is not None and not why.startswith('unverified')
    tr = TS.run(out, 104, len(out), 120)
    dark = [t for t, (_r, bg) in enumerate(tr)
            if bg.get(pb) and not any(bg.get(p) for p in block)]
    assert dark and {b - a for a, b in zip(dark, dark[1:])} == {8}


def test_858_two_opcode_multi_site_proves():
    """kv = 2 (ADPAL, LDPAL): BGOFF prev; BGON v at the site, the SET
    after the next WAIT -- every tick shows vanilla's state."""
    s = section(LG23)
    site = _site(s, 0xE9, 0x0E)
    out, edits, pr = _plan_multi(s, 6, site, list(range(42, 54)), 0xFE,
                                 _new)
    assert len(PA._ops_in(PA._stream(s), *site)) == 2
    assert PA.tick_proof(s, out, edits, pr) is None


def test_858_three_opcode_multi_site_proves_and_old_padding_does_not():
    """lg1 runs ADPAL CPPAL LDPAL IF IF MINUS SKIP BACK: 8 opcodes, one
    pass a tick. The 3-opcode multi switch keeps that; 854's padded one
    (5 opcodes) made it two ticks a pass."""
    s = section(LG1)
    site = _site(s, 0xE9, 0x09)
    assert len(PA._ops_in(PA._stream(s), *site)) == 3
    out, edits, pr = _plan_multi(s, 4, site, list(range(54, 64)), 0xFF,
                                 _new)
    assert PA.tick_proof(s, out, edits, pr) is None
    out, edits, pr = _plan_multi(s, 4, site, list(range(54, 64)), 0xFF,
                                 _old)
    why = PA.tick_proof(s, out, edits, pr)
    assert why is not None and not why.startswith('unverified')


def test_site_codes_match_the_vanilla_opcode_count():
    for kv in (2, 3, 4):
        b = PA.site_single(kv, 7, 0x29)
        assert b[-7:] == bytes((0xE4, 0, 7, 0xE0, 5, 7, 0x29))
        assert b[:-7] == bytes((PA.OP_NOP,)) * (kv - 2)
    for kv in (3, 4, 5):
        b, later = PA.site_multi(kv, 4, 0xFF)
        assert later == b'' and len(b) == (kv - 3) + 12
    b, later = PA.site_multi(2, 4, 0xFF)
    assert b == bytes((0xE1, 0x50, 0xFF, 0, 0xE0, 0x50, 4, 0))
    assert later == bytes((0x80, 0x55, 0xFF, 4))


def test_grow_shrinks_and_inserts():
    s = section(LG23)
    so, sl = _site(s, 0xE9, 0x0E)
    stream = PA._stream(s)
    w = PA._after_next_wait(stream, so, so + sl)
    out = PA.grow(s, [(so, sl, b'\x5f\x5f'), (w, 0, b'\x5f')])
    assert len(out) == len(s) - sl + 3
    # the loop's BACK still lands on its first ADPAL
    st = PA._stream(out)
    back = [(o, z) for _a, o, op, z in st if op == 0x12][-1]
    tgt = back[0] - out[back[0] + 1]
    assert out[tgt] == 0xE9


def test_static_tol_constant():
    assert PA.STATIC_TOL == 4.0 and PA.VERSION >= 859


def test_multi_variants_for_a_full_tick_loop():
    """mds7plr2's lights loop: ADPAL2 LDPAL IF IF DEC SKIP IF WAIT -- full
    on the falling path, the variable stepped right after the site, so the
    deferred SET would store the NEXT state and the inline SET slips the
    WAIT. Variants 0..2 fail the exact proof; variant 3 passes the relaxed
    one (every state in order, never empty, a tick slower per pass)."""
    body = bytes.fromhex(
        '805001008050'  # set var1 = 0 ; set var0 = ff
        '00ff8050'
        '02ff805003f0'  # set var2 = ff ; set var3 = f0
        '00'            # ret
        'e5000400ff'    # stpal
        'ef0555000010010000' '00fe'   # adpal2 v0
        'e6001004ff'    # ldpal 4
        '145001000010'  # if var1 == 0 (else +0x10)
        '145500030206'  # if var0 > var3 (else +6)
        '970500'        # dec var0
        '1005'          # skip 5
        '80500101'      # set var1 = 1
        '145001010010'  # if var1 == 1
        '145500020306'  # if var0 < var2
        '950500'        # inc var0
        '1005'          # skip 5
        '80500100'      # set var1 = 0
        '240300'        # wait 3
        '123d')         # back to the adpal2
    s = section(body)
    so = 104 + body.index(bytes.fromhex('ef05'))
    site = (so, 16)
    st = PA._stream(s)
    kv = len(PA._ops_in(st, *site))
    assert kv == 2
    uses = PA.var_uses(s, 0, skip={so})
    step, _a, lo, hi, _x = PA.lattice(s, uses)
    n = (hi - lo) // step + 1
    block = list(range(64 - n, 64))
    pr = {'q': 4, 'mode': 'multi', 'block': block, 'var': 0,
          'sites': [site], 'gate': None,
          'states': [x & 255 for x in range(lo, hi + 1, step)]}
    res = []
    for v in range(PA.MULTI_VARIANTS):
        new0 = PA.renumber(s, uses, lo, step, n, base=block[0])
        edits = PA._multi_variant(st, s, so, 16, kv, 0, 0xFE, v)
        out = PA.grow(new0, edits)
        res.append(PA.tick_proof(s, out, edits, pr, ticks=400,
                                 relaxed=v == PA.MULTI_VARIANTS - 1))
    assert all(r is not None for r in res[:-1]) and res[-1] is None, res
