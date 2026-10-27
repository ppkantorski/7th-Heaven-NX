"""BUILD 585/587: the world map far field -- data, blob, module words, logic.

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
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import numpy as np                                             # noqa: E402
import pytest                                                  # noqa: E402

import a64 as A                                                # noqa: E402
import ff7nx_audio_cave as AC                                  # noqa: E402
import ff7nx_worldfar as W                                     # noqa: E402
import ff7nx_worldfar_data as F                                # noqa: E402
import ff7nx_worldsphere as WS                                 # noqa: E402

MODULE = os.path.join(ROOT, 'dump', 'exefs', 'main')
WM0 = os.path.join(ROOT, 'dump', 'romfs', 'ff7', 'workingdir', 'data', 'wm',
                   'wm0.map')
NATIVE = os.path.join(ROOT, 'native')
need_dump = pytest.mark.skipif(not (os.path.exists(MODULE)
                                    and os.path.exists(WM0)),
                               reason='needs the dump')

_far = {}


def far():
    if 'b' not in _far:
        _far['b'], _far['st'] = F.build(WM0)
    return _far['b']


def entries(b):
    n = struct.unpack_from('<I', b, 8)[0] + struct.unpack_from('<I', b, 12)[0]
    return [struct.unpack_from(F.ENTRY_FMT, b, 32 + 32 * i) for i in range(n)]


def tile_arrays(b, e):
    """v2: verts s16 x,y,z; normals s8 x,y,z (unit * 127)."""
    at, vat, nat, ntri = e[0], e[1], e[2], e[3]
    total = ntri + sum(e[8:12])
    nv = e[12]
    tris = np.frombuffer(b[at:at + 12 * total], np.uint8).reshape(total, 12)
    v = np.frombuffer(b[vat:vat + 6 * nv], '<i2').reshape(nv, 3)
    n = np.frombuffer(b[nat:nat + 3 * nv], np.int8).reshape(nv, 3)
    return tris, v, n


# ------------------------------------------------------------------ source
def _c_define(name):
    m = re.search(r'#define\s+%s\s+(0x[0-9A-Fa-f]+|\d+)u?' % name,
                  open(os.path.join(NATIVE, 'worldfar.c')).read())
    return int(m.group(1), 0)


def test_c_and_python_agree_on_layout():
    assert _c_define('PRIM_BYTES') == W.PRIM_BYTES
    assert _c_define('FAR_MAX') == W.FAR_MAX
    assert _c_define('FAR_IO') == W.FAR_IO
    assert _c_define('FAR_IO_BYTES') == W.FAR_IO_BYTES
    assert _c_define('NAT_MAX') == W.NAT_MAX
    assert _c_define('NSLOT') == W.NSLOT
    assert _c_define('LZ_BYTES') == W.LZ_BYTES
    assert W.FAR_OFF == 0xA6800 + 2 * W.PRIM_BYTES
    # the world allocation (guest heap) is back near stock: data is in BSS
    assert W.WORLD_ALLOC < 0x120000
    assert W.BSS_BYTES >= W.STATE_BYTES + 16


def test_blob_is_built_from_this_source():
    src = open(os.path.join(NATIVE, 'worldfar.c'), 'rb').read()
    want = open(os.path.join(NATIVE, 'worldfar.src.sha256')).read().split()[0]
    assert hashlib.sha256(src).hexdigest() == want, \
        'native/worldfar.c changed: run native/build_worldfar.sh'
    code = W.blob()
    assert len(code) % 4 == 0 and len(code) <= W.BLOB_MAX


def test_blob_has_no_absolute_addresses():
    """Position independent by construction: no literal pools, no adrp/ldr
    of a fixed address outside the blob (every call is through a pointer)."""
    code = W.blob()
    for i in range(0, len(code), 4):
        w = struct.unpack_from('<I', code, i)[0]
        if (w & 0x9F000000) == 0x90000000:      # adrp
            raise AssertionError('adrp at +%#x' % i)
        if (w & 0xFC000000) == 0x94000000:      # bl
            off = w & 0x3FFFFFF
            if off & 0x2000000:
                off -= 0x4000000
            assert 0 <= i + 4 * off < len(code), 'bl out of the blob'


# ------------------------------------------------------------------ data
@need_dump
def test_tiles_fit_the_native_limits():
    b = far()
    assert b[:4] == b'WFAR'
    es = entries(b)
    assert len(es) == W.N_TILES == F.N1 + F.N2
    for e in es:
        tris, v, _n = tile_arrays(b, e)
        assert len(v) <= 120
        assert tris[:, :3].max() < len(v)
    assert len(b) <= W.FAR_MAX


@need_dump
def test_l1_neighbours_share_their_edges_and_match_the_native_mesh():
    b = far()
    es = entries(b)
    M = F.read_meshes(WM0)

    def lattice(i):
        _t, v, _n = tile_arrays(b, es[i])
        return v[:25, 1].reshape(5, 5)
    for mz in range(28):
        for mx in range(36):
            h = lattice(mz * 36 + mx)
            if mx < 35:
                assert (h[:, 4] == lattice(mz * 36 + mx + 1)[:, 0]).all()
            if mz < 27:
                assert (h[4, :] == lattice((mz + 1) * 36 + mx)[0, :]).all()
    # the lattice corners are native vertices: same height
    bad = 0
    for mz in range(0, 28, 3):
        for mx in range(0, 36, 3):
            tris, verts = F.mesh_at(M, mx, mz)
            h = lattice(mz * 36 + mx)
            for (x, z, y) in ((0, 0, h[0, 0]), (8192, 8192, h[4, 4])):
                sel = (verts[:, 0] == x) & (verts[:, 2] == z)
                if sel.any():
                    bad += int(abs(verts[sel][0, 1] - y) > 1)
    assert bad == 0


@need_dump
def test_winding_and_normals_follow_the_native_convention():
    """(b-a) x (c-a) and the stored normal both point -y, as in wm0.map."""
    b = far()
    for e in entries(b)[::37]:
        tris, v, n = tile_arrays(b, e)
        for t in tris[:e[3]]:
            p = v[t[:3], :3].astype(float)
            c = np.cross(p[1] - p[0], p[2] - p[0])
            if np.linalg.norm(c) > 1:
                assert c[1] < 0
        assert (n[:, 1] < 0).all()


@need_dump
def test_skirts_hang_below_their_edge():
    b = far()
    for e in entries(b)[::11]:
        tris, v, _n = tile_arrays(b, e)
        for k in range(4):
            first, cnt = e[4 + k], e[8 + k]
            assert cnt % 4 == 0 and cnt > 0
            sk = tris[first:first + cnt]
            ys = v[sk[:, :3].ravel(), 1]
            assert ys.min() < ys.max()


@need_dump
def test_mesh_blocks_are_the_native_meshes_inside_wm0_far():
    """BUILD 588: each full-detail mesh is the game's own LZS block, carried
    in wm0.far (so nothing reads a file while the world map runs)."""
    import lgp
    b = far()
    assert struct.unpack_from('<I', b, 4)[0] == 4
    refs_at = struct.unpack_from('<I', b, 28)[0]
    M = F.read_meshes(WM0)
    for cell in range(36 * 28):
        off, ln = struct.unpack_from('<II', b, refs_at + 8 * cell)
        assert 0 < ln <= W.LZ_BYTES and off + ln <= len(b)
        d = lgp.lzs_decompress(b[off:off + ln])
        nt, nv = struct.unpack_from('<HH', d)
        tris, verts = F.mesh_at(M, cell % 36, cell // 36)
        assert (nt, nv) == (len(tris), len(verts))
        assert nt <= 256 and nv <= 120
        assert 4 + nt * 12 + nv * 16 <= len(d) <= W.LZ_BYTES
    # BUILD 591: the six story replacements (wm0.map sectors 63..68)
    raw = open(WM0, 'rb').read()
    for a, (bs, alt, _p) in enumerate(F.VARIANTS):
        for k in range(16):
            off, ln = struct.unpack_from('<II', b, refs_at + 8 * (36 * 28 + 16 * a + k))
            sec = raw[alt * F.SECTOR:(alt + 1) * F.SECTOR]
            o = struct.unpack_from('<16I', sec)[k]
            assert b[off:off + ln] == sec[o + 4:o + 4 + ln]


@need_dump
def test_variant_tiles_are_the_replacement_ground():
    """BUILD 591: the crater (sector 50 -> 63) and the others: the L1
    tiles of a variant follow the replacement sector's surface."""
    b = far()
    es = entries(b)
    M = F.read_meshes(WM0)
    assert len(M) == 69
    differ = crater = 0
    for a, (bs, alt, _p) in enumerate(F.VARIANTS):
        for k in range(16):
            _t, v, _n = tile_arrays(b, es[36 * 28 + 16 * a + k])
            surf = F.MeshSurface(*M[alt][k])
            for x, y, z in v[:25, :3]:
                assert abs(y - round(surf.height(x, z))) <= 1
            base = es[((bs // 9) * 4 + (k >> 2)) * 36 + (bs % 9) * 4 + (k & 3)]
            _t2, v2, _n2 = tile_arrays(b, base)
            ch = int((v[:25, 1] != v2[:25, 1]).any())
            differ += ch
            crater += ch if bs == 50 else 0
    assert differ >= 10 and crater >= 1   # the Temple of the Ancients pit


# ------------------------------------------------------------------ module
def _sphere_module(tmp):
    blob = open(MODULE, 'rb').read()
    segs, raw = AC.segments(blob)
    t = bytearray(raw[0])
    struct.pack_into('<I', t, WS.SINK_SITE, WS.SINK_NEW)
    raw[0] = bytes(t)
    p = os.path.join(tmp, 'main_sph')
    open(p, 'wb').write(AC.pack(blob, raw, 0))
    return p


@need_dump
def test_module_pass_words():
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ValueError):          # no sphere, no far field
            W.apply_to_nso(MODULE, os.path.join(tmp, 'x'), stock=MODULE)
        src = _sphere_module(tmp)
        out = os.path.join(tmp, 'main_far')
        r = W.apply_to_nso(src, out, stock=MODULE)
        nso = open(out, 'rb').read()
        _s, raw = AC.segments(nso)
        text = raw[0]
        word = lambda va: struct.unpack_from('<I', text, va)[0]
        assert word(W.HOOK_SITE) == A.bl(W.HOOK_SITE, r['stub'])
        assert word(W.ALLOC_CALL) == A.bl(W.ALLOC_CALL, r['alloc'])
        assert word(W.CAMERA_CALL) == A.bl(W.CAMERA_CALL, r['camera'])
        for site in W.TRANSFORM_CALLS:
            assert word(site) == A.bl(site, r['bend'])
        for site in W.SINK_CALLS:                  # models, Zolom, effects
            assert word(site) == A.bl(site, r['sink'])
        for site in W.SKY_CALLS:
            assert word(site) == A.bl(site, r['sky'])
        for site in W.HEIGHT_CALLS:                # BUILD 593: ground heights
            assert word(site) == A.bl(site, r['height'])
        # BUILD 598: the minimap in 16:9; the full map does not move
        (x_at, _a, x316, _w), (m_at, _b, m11, _v) = W.MINIMAP_WORDS
        assert word(x_at) == A.movz(9, x316) and word(m_at) == A.movz(9, m11)
        assert x316 - m11 * 24 == 316 - 11 * 24          # full map: x 52
        assert x316 - m11 * 8 - (316 - 11 * 8) == 48     # minimap: 48 right
        # BUILD 715: the minimap at the top, 28 units down (it sat 28 above
        # the bottom: 240 - (220 - 8*8) - 7*8 = 28); the full map stays at 28
        (y_at, _c, y_new, _x), (s_at, _d, s_new, _y) = W.MINIMAP_Y_WORDS
        assert word(y_at) == A.movz(9, 25) == y_new      # BUILD 729: = the right margin
        assert abs(25 - (320 * 16 / 12 - 53.33 - (388 - 14 * 8 + 9 * 8))) < 0.5
        assert word(s_at) == A.mov_reg(23, 9) == s_new
        assert 240 - (220 - 8 * 8) - 7 * 8 == 28 == 220 - 8 * 24
        assert word(W.PITCH_SITE) == A.b(W.PITCH_SITE, r['pitch'])
        # BUILD 700: the Highwind's 4/256 level-flight slant is gone, its
        # quarter turn (the model's own orientation) stays
        assert word(W.HW_LEVEL_SITE) == A.sub_imm(20, 8, 0x44)
        assert word(W.MOVE_CALL) == A.bl(W.MOVE_CALL, r['move'])
        assert word(W.INPUT_CALL) == A.bl(W.INPUT_CALL, r['input'])
        assert word(W.DI_ZL_SITE) == A.b(W.DI_ZL_SITE, r['di_zl'])
        assert word(W.DI_ZR_SITE) == A.b(W.DI_ZR_SITE, r['di_zr'])
        assert word(0xF32014) == A.movz(8, 30767)
        assert word(0xF32018) == A.movz(9, 0)
        assert word(0xF320FC) == A.movz(8, 30767)
        ent = W.entries()
        for va0, words in (
                (r['bend'], W.bend_words(r['bend'], r['entry'] + ent['bend'], r['state'])),
                (r['stub'], W.stub_words(r['stub'], r['entry'] + ent['draw'], r['state'], r['uv'])),
                (r['camera'], W.camera_words(r['camera'], r['entry'] + ent['camera'], r['state'])),
                (r['input'], W.input_words(r['input'], r['entry'] + ent['input'], r['state'])),
                (r['alloc'], W.alloc_words(r['alloc'], r['entry'] + ent['alloc'], r['state'])),
                (r['sink'], W.sink_words(r['sink'], r['entry'] + ent['sink'], r['state'])),
                (r['sky'], W.sky_words(r['sky'], r['entry'] + ent['sky'], r['state'])),
                (r['height'], W.height_words(r['height'], r['entry'] + ent['height'], r['state'])),
                (r['move'], W.move_words(r['move'], r['entry'] + ent['move'], r['state'])),
                (r['pitch'], W.pitch_words(r['pitch'])),
                (r['di_zl'], W.di_zl_words(r['di_zl'])),
                (r['di_zr'], W.di_zr_words(r['di_zr']))):
            for i, w in enumerate(words):
                assert word(va0 + 4 * i) == w
        assert text[r['entry']:r['entry'] + r['code']] == W.blob()
        assert (word(0xF21CE0), word(0xF21CE4)) == (A.movz(9, 0), A.movk_hi(9, 3))
        assert (word(0xF2B2A8), word(0xF2B2AC)) == (A.movz(9, 0), A.movk_hi(9, 3))
        assert (word(0xF799B8), word(0xF799BC)) == (
            A.movz(8, W.WORLD_ALLOC & 0xFFFF), A.movk_hi(8, W.WORLD_ALLOC >> 16))
        old_bss = struct.unpack_from('<I', open(src, 'rb').read(), 0x3C)[0]
        assert struct.unpack_from('<I', nso, 0x3C)[0] == old_bss + r['bss']
        # a second pass over its own output is refused, never stacked
        with pytest.raises(ValueError):
            W.apply_to_nso(out, os.path.join(tmp, 'y'), stock=MODULE)


# ------------------------------------------------------------------ logic
@need_dump
def test_host_harness_places_tiles_without_holes_or_overlap():
    cc = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
    if not cc:
        pytest.skip('no C compiler')
    with tempfile.TemporaryDirectory() as tmp:
        fp = os.path.join(tmp, 'wm0.far')
        open(fp, 'wb').write(far())
        exe = os.path.join(tmp, 'wfhost')
        subprocess.check_call([cc, '-O2', '-o', exe,
                               os.path.join(NATIVE, 'worldfar_host_test.c'),
                               '-lm'], cwd=NATIVE)
        r = subprocess.run([exe, fp], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-2000:]
        assert 'total errors 0' in r.stdout
        m = re.search(r'state (\d+) zoom (\d+) tilt (\d+)', r.stdout)
        assert int(m.group(1)) == W.STATE_BYTES
        assert int(m.group(1)) <= W.BSS_BYTES
        assert int(m.group(2)) == W.STATE_ZOOM
        assert int(m.group(3)) == W.STATE_TILT


def test_gaia_master_switch_turns_every_world_change_off():
    import ff7nx_widescreen as WSC
    env = {'SEVENTH_NX_WORLD_GAIA': '0'}
    assert not WS.enabled(env)
    assert not W.enabled(env)
    assert WSC.world_sky_lower(env) == WSC.WORLD_SKY_LOWER_STOCK
    assert WS.enabled({}) and W.enabled({})
    assert WSC.world_sky_lower({}) == WSC.WORLD_SKY_LOWER
    assert not W.enabled({'SEVENTH_NX_WORLD_FAR': '0'})


def test_pitch_cave_clamps_the_camera_height():
    """min(w8, 4000) >> 5, then back to the add."""
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    at = 0x100000
    code = b''.join(struct.pack('<I', w) for w in W.pitch_words(at))
    ins = list(Cs(CS_ARCH_ARM64, CS_MODE_ARM).disasm(code, at))
    text = ['%s %s' % (i.mnemonic, i.op_str) for i in ins]
    assert text[0] == 'mov w9, #0xfa0'
    assert text[1] == 'cmp w8, w9'
    assert text[2] == 'csel w8, w8, w9, lt'
    assert text[3] == 'asr w8, w8, #5'
    assert text[4] == 'b #%#x' % (W.PITCH_SITE + 4)


def test_sink_stub_passes_the_register_block():
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    at = 0x99E000
    code = b''.join(struct.pack('<I', w) for w in W.sink_words(at, at + 0x1000, 0x3FEC330))
    ins = ['%s %s' % (i.mnemonic, i.op_str)
           for i in Cs(CS_ARCH_ARM64, CS_MODE_ARM).disasm(code, at)]
    assert 'adrp x3, #0x12ce000' in ins and 'ldr x3, [x3, #0x2b0]' in ins
    assert ins[-1].startswith('ret')


@need_dump
def test_zooming_never_changes_the_level_of_a_cell():
    """BUILD 605: full detail no longer depends on how big a cell is on the
    screen, so zooming out (from any height, any lens) never swaps a cell
    between the game's mesh and the coarse tile, the horizon stays complete,
    and the frame stays well inside the triangle budget."""
    cc = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
    if not cc:
        pytest.skip('no C compiler')
    with tempfile.TemporaryDirectory() as tmp:
        fp = os.path.join(tmp, 'wm0.far')
        open(fp, 'wb').write(far())
        exe = os.path.join(tmp, 'wfbench')
        subprocess.check_call([cc, '-O2', '-o', exe,
                               os.path.join(NATIVE, 'worldfar_host_test.c'),
                               '-lm'], cwd=NATIVE)
        for lens in ('500', '250'):
            r = subprocess.run([exe, fp, 'bench', lens], capture_output=True, text=True)
            assert r.returncode == 0, r.stderr[-2000:]
            m = re.search(r'tris avg (\d+) max (\d+).*level flips between zoom steps (\d+) of (\d+)',
                          r.stdout)
            assert m and int(m.group(3)) == 0 and int(m.group(4)) > 10000
            assert int(m.group(2)) < W.MAX_TRIS_FRAME * 2 // 3
            m = re.search(r'horizon tiles (\d+) of (\d+) drawn, (\d+) frames short', r.stdout)
            assert m.group(1) == m.group(2) and m.group(3) == '0'
            assert 'fail 0 errors 0' in r.stdout


@need_dump
def test_the_state_ends_inside_the_mapped_bss():
    """BUILD 605b: the loader maps BSS from the RAW end of .data, so the
    module's last byte is page_align(data_end + bssSize). 605's State ended
    0x130 bytes past it and the world map's load faulted on its tail."""
    with tempfile.TemporaryDirectory() as tmp:
        src = _sphere_module(tmp)
        out = os.path.join(tmp, 'main_far')
        r = W.apply_to_nso(src, out, stock=MODULE)
        nso = open(out, 'rb').read()
        segs, _raw = AC.segments(nso)
        bss = struct.unpack_from('<I', nso, 0x3C)[0]
        raw_end = segs[2][1] + segs[2][2] + bss         # not page-rounded: strict
        assert r['state'] + W.STATE_BYTES <= raw_end
        assert AC.bss_tail_slack(segs) == ((segs[2][1] + segs[2][2] + 0xFFF) & ~0xFFF) - (segs[2][1] + segs[2][2])


@need_dump
def test_the_window_caves_skip_or_pass_the_projection_and_submit():
    """BUILD 606: the four window calls go through two caves that mark the
    caves present, then skip the call (popping its return slot) while
    State.own_window is set, else tail-call the stock routine."""
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    cs = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    with tempfile.TemporaryDirectory() as tmp:
        src = _sphere_module(tmp)
        out = os.path.join(tmp, 'main_far')
        r = W.apply_to_nso(src, out, stock=MODULE, own=True)
        _s, raw = AC.segments(open(out, 'rb').read())
        text = raw[0]
        word = lambda va: struct.unpack_from('<I', text, va)[0]
        own = r['state'] + W.STATE_BYTES - W.HW_TAIL - 4
        for site, target, nxt in W.WINDOW_CALLS:
            cave = r['win263'] if target == W.WINDOW_CALLS[0][1] else r['win68c']
            assert word(site) == A.bl(site, cave) and word(site + 4) == nxt
            ins = ['%s %s' % (i.mnemonic, i.op_str) for i in cs.disasm(text[cave:cave + 100], cave)]
            assert ins[0] == 'adrp x16, #%#x' % (own & ~0xFFF)
            assert ins[1] == 'add x16, x16, #%#x' % (own & 0xFFF)
            assert ins[3] == 'str w17, [x16, #0x28]'      # hw_pad2[0] = own + 40
            assert ins[4] == 'ldr w17, [x16]'
            skip = cave + 4 * 21
            assert ins[5] == 'cbnz w17, #%#x' % skip
            # 606b: the call path times the stock routine into a running total
            assert ins[6] == 'stp x29, x30, [sp, #-0x10]!'
            assert ins[7] == 'mrs x17, cntpct_el0'
            assert ins[9] == 'bl #%#x' % target
            assert ins[11] == 'mrs x16, cntpct_el0' and ins[12] == 'sub x17, x16, x17'
            off = 0x34 if target == W.WINDOW_CALLS[0][1] else 0x2c   # 606c offsets
            assert ins[15] == 'sub x16, x16, #%#x' % off
            assert ins[16:19] == ['ldr x15, [x16]', 'add x15, x15, x17', 'str x15, [x16]']
            assert ins[19] == 'ldp x29, x30, [sp], #0x10' and ins[20].startswith('ret')
            assert ins[21:24] == ['ldr w16, [x24, #0x10]', 'add w16, w16, #4', 'str w16, [x24, #0x10]']
            assert ins[24].startswith('ret')
        # off: the stock calls stay
        out2 = os.path.join(tmp, 'main_far2')
        W.apply_to_nso(src, out2, stock=MODULE, own=False)
        _s, raw2 = AC.segments(open(out2, 'rb').read())
        for site, target, _n in W.WINDOW_CALLS:
            assert struct.unpack_from('<I', raw2[0], site)[0] == A.bl(site, target)


@need_dump
def test_the_terrain_draw_is_timed_transparently():
    """BUILD 606c: the world render's two terrain draw loops call 0x66E641
    through one cave that times the call into State.pf_t_terr."""
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    cs = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    with tempfile.TemporaryDirectory() as tmp:
        src = _sphere_module(tmp)
        out = os.path.join(tmp, 'main_far')
        r = W.apply_to_nso(src, out, stock=MODULE, own=False)
        _s, raw = AC.segments(open(out, 'rb').read())
        text = raw[0]
        for site, target, nxt in W.TERR_CALLS:
            assert struct.unpack_from('<II', text, site) == (A.bl(site, r['terr']), nxt)
        ins = ['%s %s' % (i.mnemonic, i.op_str) for i in cs.disasm(text[r['terr']:r['terr'] + 60], r['terr'])]
        assert ins[0] == 'stp x29, x30, [sp, #-0x10]!' and ins[1] == 'mrs x17, cntpct_el0'
        assert ins[3] == 'bl #%#x' % W.TERR_CALLS[0][1]
        assert ins[9] == 'sub x16, x16, #0x24'            # pf_t_terr = own_window - 36
        assert ins[13] == 'ldp x29, x30, [sp], #0x10' and ins[14].startswith('ret')
