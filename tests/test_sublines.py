"""BUILD 552: GL_LINES drawn 2 px wide (three viewport-nudged draws).

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
import os, struct, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import ff7nx_sublines as S  # noqa: E402
import ff7nx_audio_cave as AC  # noqa: E402
import nxmap  # noqa: E402
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM  # noqa: E402

STOCK = os.path.join(HERE, 'dump', 'exefs', 'main')


def _patched():
    blob = open(STOCK, 'rb').read()
    _segs, raw = AC.segments(blob)
    t = bytearray(raw[0])
    stock = bytes(t)
    w = S.patch_text(t, set(nxmap.Main(STOCK).arm_starts))
    return stock, bytes(t), w


def _walk(t, va, n=80):
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    out = []
    for _ in range(n):
        i = next(md.disasm(t[va:va + 4], va))
        if i.mnemonic == 'b':
            va = int(i.op_str.lstrip('#'), 16)
            continue
        out.append((i.mnemonic, i.op_str))
        if i.mnemonic in ('ret', 'br'):
            break
        va += 4
    return out


def test_stock_state():
    blob = open(STOCK, 'rb').read()
    _s, raw = AC.segments(blob)
    assert S.check_text(raw[0]) == [] and S.state_text(raw[0]) == 'stock'


def test_only_sites_and_zero_holes_change():
    stock, t, w = _patched()
    assert S.state_text(t) == 'patched'
    for va in range(0, len(t), 4):
        if t[va:va + 4] != stock[va:va + 4]:
            assert va in w
            if (va not in dict(S.SITES) and va not in dict(S.zsites())
                    and va not in (S.PACE_SITE, S.FLIP_MASK[0])):
                assert stock[va:va + 4] == b'\0\0\0\0'


def test_executed_stream():
    _stock, t, _w = _patched()
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    for site, stub in S.SITES:
        i = next(md.disasm(t[site:site + 4], site))
        assert i.mnemonic == 'bl'
        disp = _walk(t, int(i.op_str.lstrip('#'), 16))
        assert [m for m, _ in disp] == ['cmp', 'adrp', 'add', 'adrp', 'add',
                                        'csel', 'br']
        assert disp[0][1] == 'w0, #1'
        # resolve the body address the dispatcher computes
        va = int(i.op_str.lstrip('#'), 16)
        body = _walk(t, va)
        # follow adrp/add by hand
        seq = []
        v = va
        while len(seq) < 3:
            ins = next(md.disasm(t[v:v + 4], v))
            if ins.mnemonic == 'b':
                v = int(ins.op_str.lstrip('#'), 16)
                continue
            seq.append(ins)
            v += 4
        page = int(seq[1].op_str.split('#')[1], 16)
        lo = int(seq[2].op_str.split('#')[1], 16)
        b = _walk(t, page + lo)
        calls = [o for m, o in b if m == 'bl']
        assert calls == ['#%#x' % S.STUB_GET_INTEGERV, '#%#x' % stub,
                         '#%#x' % S.STUB_VIEWPORT, '#%#x' % stub,
                         '#%#x' % S.STUB_VIEWPORT, '#%#x' % stub,
                         '#%#x' % S.STUB_VIEWPORT, '#%#x' % stub,
                         '#%#x' % S.STUB_VIEWPORT]
        assert b[-1][0] == 'ret'


def test_env():
    assert S.enabled({}) and not S.enabled({S.ENV: '1'})


def test_z_sites_call_the_bias_cave():
    stock, t, _w = _patched()
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    targets = set()
    for va, _s in S.zsites():
        i = next(md.disasm(t[va:va + 4], va))
        assert i.mnemonic == 'bl'
        targets.add(int(i.op_str.lstrip('#'), 16))
        # the z load before and the store after are untouched
        assert t[va - 8:va - 4] == stock[va - 8:va - 4]
        assert t[va + 8:va + 12] == stock[va + 8:va + 12]
    assert len(targets) == 1
    body = _walk(t, targets.pop())
    ops = [m for m, _ in body]
    assert ops == ['stp', 'fmov', 'mov', 'movk', 'fmov', 'fmul', 'mov',
                   'movk', 'fmov', 'fsub', 'fmov', 'ldp', 'ret']


def test_zbias_math():
    import struct as st
    k = 0.02
    for z in (0.5, 0.9, 0.99, 0.9985):
        zp = z * (1 + k) - k
        assert abs((1 - zp) - (1 - z) * (1 + k)) < 1e-12 and zp < z


def test_bias_env():
    assert S.bias_pct({}) == 2.0
    assert S.bias_pct({S.BIAS_ENV: '0'}) == 0.0
    assert S.bias_pct({S.BIAS_ENV: '50'}) == 2.0


def test_pace_cave_replaces_the_limiter():
    stock, t, _w = _patched()
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    i = next(md.disasm(t[S.PACE_SITE:S.PACE_SITE + 4], S.PACE_SITE))
    assert i.mnemonic == 'b'
    body = _walk(t, int(i.op_str.lstrip('#'), 16), 200)
    calls = [o for m, o in body if m == 'bl']
    assert calls == ['#%#x' % S.TRANSLATE, '#%#x' % S.TRANSLATE,
                     '#%#x' % S.CLOCK]
    ops = [m for m, _ in body]
    assert ops[-1] == 'br' and 'csel' in ops and 'fcvtzu' in ops


def test_pace_cave_tail():
    # the part after the wait loop: store, esp += 4, sync, restore, ret
    import struct as st
    w = S.build_pace()(0x100000, lambda i: 0x100000 + 4 * i)
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    code = b''.join(st.pack('<I', x) for x in w)
    ins = [(i.mnemonic, i.op_str) for i in md.disasm(code, 0x100000)]
    tail = ins[ins.index(('br', 'x16')) + 1:]
    assert tail[0] == ('cmp', 'x9, x20, lsl #1')
    assert tail[1] == ('add', 'x10, x8, x20')
    assert tail[2] == ('csel', 'x10, x10, x0, lo')
    assert tail[3] == ('str', 'x10, [x19]')
    assert ('add', 'w8, w8, #4') in tail
    assert ('bl', '#%#x' % S.SYNC) in tail
    assert tail[-1] == ('ret', '')
    # the csel picks the loop head while (now - last) < frame
    csel = [o for m, o in ins if m == 'csel'][0]   # the loop's
    assert csel == 'x16, x16, x17, lo'


def test_pace_env():
    assert S.pace_enabled({}) and not S.pace_enabled({S.PACE_ENV: '0'})


def test_flip_wait_kept_for_highway_only():
    # BUILD 562: port mode numbers -- 10 snowboard, 12 coaster, 14 highway
    _stock, t, _w = _patched()
    import struct as st
    assert st.unpack_from('<I', t, S.FLIP_MASK[0])[0] == S.FLIP_MASK[2]
    mask_old, mask_new = 0x5400, 0x4000
    assert S.FLIP_MASK[1] == 0x528A8009 and S.FLIP_MASK[2] == 0x52880009
    for mode in range(0, 29):
        old = mode <= 14 and (mask_old >> mode) & 1
        new = mode <= 14 and (mask_new >> mode) & 1
        assert bool(new) == (bool(old) and mode not in (10, 12))
    assert S.PORT_MODES[12] == 'coaster' and S.PORT_MODES[14] == 'highway'


def test_port_mode_numbers_measured():
    # the native set_mode (+0x10F3D00) callers, each `mov w0, #mode; bl`
    import nxmap
    m = nxmap.Main(STOCK)
    t = m.text
    import struct as st
    for x86, mode in ((0x76D597, 9), (0x722C10, 10), (0x77D030, 11),
                      (0x5E8D49, 12), (0x5F47B5, 13), (0x650310, 14)):
        a = m.x86_to_arm[x86]
        found = False
        for va in range(a, a + 0x80, 4):
            w = st.unpack_from('<I', t, va)[0]
            if (w >> 26) == 0x25:
                imm = w & 0x3FFFFFF
                if va + imm * 4 == 0x10F3D00:
                    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
                    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
                    for ins in md.disasm(t[va - 0x20:va], va - 0x20):
                        if ins.mnemonic == 'mov' and ins.op_str in (
                                'w0, #%#x' % mode, 'w0, #%d' % mode):
                            found = True
                    break
        assert found, hex(x86)


def test_dead_space_install():
    # BUILD 557: the build places every cave in ff7nx_deadspace 'sub'
    import tempfile, shutil
    import ff7nx_deadspace as DS
    d = tempfile.mkdtemp()
    try:
        out = os.path.join(d, 'main')
        r = S.apply_to_nso(STOCK, out, lambda *a: None, True, 2.0, True,
                           stock=STOCK)
        lo, hi = DS.part(STOCK, 'sub')
        _s, raw = AC.segments(open(out, 'rb').read())
        t = raw[0]
        _s0, raw0 = AC.segments(open(STOCK, 'rb').read())
        t0 = raw0[0]
        outside = {a for a in range(0, len(t), 4)
                   if t[a:a + 4] != t0[a:a + 4] and not lo <= a < hi}
        want = ({S.PACE_SITE, S.FLIP_MASK[0]} | {va for va, _ in S.SITES}
                | {va for va, _ in S.zsites()})
        assert outside == want
        assert r['words'] > 150
    finally:
        shutil.rmtree(d)
