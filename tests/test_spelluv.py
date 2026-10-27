"""Per-TEX logical scale and replacement-owned key-mask contracts.

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
import struct
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import lz4.block  # noqa: F401
except ImportError:
    from patch_glerror_sdout import ensure_lz4
    ensure_lz4()

import ff7nx_ddstex as D                                      # noqa: E402
import ff7nx_spelluv as S                                     # noqa: E402
import ff7nx_cave                                             # noqa: E402
import arm64emu as E                                          # noqa: E402
import tex                                                    # noqa: E402

MAIN = os.path.join(HERE, 'game_data_files', 'exefs', 'main')


def tiny_tex():
    base = bytearray(tex.HEADER_LEN)
    struct.pack_into('<I', base, tex.O_VERSION, 1)
    hdr = tex._paletted_header(bytes(base), 2, 2, 1, 256)
    return hdr + bytes(256 * 4) + bytes((0, 1, 1, 0))


def test_marker_round_trip_and_inert_unmarked_data():
    raw = tiny_tex()
    assert tex.logical_scale(raw) is None
    marked = tex.mark_logical_scale(raw, 3)
    assert tex.logical_scale(marked) == (3, 3)
    assert marked[:4] == raw[:4]
    assert marked[8:0x2C] == raw[8:0x2C]
    assert marked[0x30:] == raw[0x30:]


@pytest.mark.parametrize('sx,sy', [(0, 0), (17, 17), (2, 17)])
def test_malformed_marker_is_rejected(sx, sy):
    raw = bytearray(tiny_tex())
    struct.pack_into('<I', raw, tex.O_USER_MARK, tex.SYW_SCALE_MARK)
    struct.pack_into('<I', raw, tex.O_USER_SCALE, (sy << 16) | sx)
    assert tex.logical_scale(bytes(raw)) is None
    with pytest.raises(ValueError):
        tex.mark_logical_scale(tiny_tex(), sx, sy)


def test_boundary_black_keeps_enclosed_black_detail():
    rgba = np.zeros((9, 9, 4), dtype=np.uint8)
    rgba[:, :, 3] = 255
    rgba[2:7, 2:7, :3] = (80, 90, 100)
    rgba[4, 4, :3] = 0                         # enclosed black detail
    mask = D.boundary_connected_black(rgba)
    assert mask[0, 0]
    assert mask[1, 4]
    assert not mask[2, 2]
    assert not mask[4, 4]
    assert mask.sum() == 56                     # outer two-pixel frame


def test_mixed_palette_resolutions_share_the_best_safe_canvas():
    # These are the real two forms found in SYW: some animation palettes are
    # 2x while others are 4x, but both describe the same logical square.
    assert D.shared_canvas_scale([(2, 2), (4, 4)], 3, 256, 256) == (3, 3)
    # Per-axis authored canvases remain valid when every palette agrees on
    # their normalized ratio, even if their source resolutions differ.
    assert D.shared_canvas_scale([(2, 4), (4, 8)], 3, 256, 128) == (3, 6)
    # The best source is 2x, so a 3x cap must not invent a third detail level.
    assert D.shared_canvas_scale([(1, 1), (2, 2)], 3, 256, 256) == (2, 2)


def test_mixed_palette_canvas_aspects_are_refused():
    with pytest.raises(D.ConvertError, match='logical canvas aspect'):
        D.shared_canvas_scale([(2, 2), (4, 8)], 3, 128, 128)


def test_logical_reciprocal_model_handles_mixed_scales():
    # 768x384 is a physical 3x image for a 256x128 logical atlas.
    u, v = S.corrected_steps(768, 384, (3, 3))
    assert u == pytest.approx(1 / 256)
    assert v == pytest.approx(1 / 128)
    # An unmarked texture in the same archive stays physical/native.
    assert S.corrected_steps(256, 128, None) == pytest.approx(
        (1 / 256, 1 / 128))
    # Per-axis metadata preserves an authored canvas whose aspect differs
    # from the native logical canvas.
    assert S.corrected_steps(768, 768, (3, 6)) == pytest.approx(
        (1 / 256, 1 / 128))


def _cave_map(img, entry):
    """Every address the scattered cave can execute, link branches included.

    `_walk_helper` returns only the logical body, skipping the allocator's
    run-to-run `b`s and never seeing a conditional target that lands in a
    later hole. Executing the real thing needs both, and getting this wrong
    made a correct cave look broken twice.
    """
    import a64 as A
    code = {}

    def walk(va):
        seen = set()
        while va not in seen and len(code) < 600:
            seen.add(va)
            word = struct.unpack_from('<I', img, va)[0]
            code[va] = word
            t = S._b_target(word, va)
            if t is not None:
                if t == S.RETURN_VA:
                    return
                va = t
                continue
            if word == A.ret():
                return
            if (word & 0xFF000010) == 0x54000000 or \
               (word & 0x7F000000) in (0x34000000, 0x35000000):
                off = (word >> 5) & 0x7FFFF
                if off & (1 << 18):
                    off -= 1 << 19
                tgt = va + off * 4
                if tgt not in code:
                    walk(tgt)
            va += 4

    walk(entry)
    return code


def _run_recip(img, axis, header, physical, keep=()):
    """Execute the installed reciprocal cave: d0 = 1.0, d1 = the physical size."""
    site = (S.RECIP_U_SITES if axis == 'u' else S.RECIP_V_SITES)[0]
    entry = S._bl_target(struct.unpack_from('<I', img, site)[0], site)
    code = _cave_map(img, entry)
    mem = E.Mem()
    cpu = E.Cpu(mem, host_base=0x700000000, paged=True)
    h = cpu.guest_to_host
    # Put every dereferenced field across a 4 KiB boundary. A cave that
    # translates a guest structure base and then adds the field offset on the
    # host side will read the wrong page under this layout.
    ebp, obj, hun, ts, hdr, ctx = (0x310008, 0x302FF8, 0x403FF0,
                                   0x504F80, 0x605FFD, 0x950000)
    mem.setu(h(ebp - S.RECIP_OBJ), obj, 4)
    mem.setu(h(obj + 0x0C), hun, 4)
    mem.setu(h(hun + 0x14), ts, 4)
    mem.setu(h(ts + 0x84), hdr, 4)
    mem.setu(h(hdr + tex.O_USER_MARK),
             struct.unpack_from('<I', header, tex.O_USER_MARK)[0], 4)
    mem.setu(h(hdr + tex.O_USER_SCALE),
             struct.unpack_from('<I', header, tex.O_USER_SCALE)[0], 4)
    mem.setu(ctx + S.RECIP_EBP, ebp, 4)
    cpu.x[19] = ctx
    cpu.x[30] = 0xB00000
    cpu.sp = 0x800000400
    cpu.fp[0] = struct.unpack('<Q', struct.pack('<d', 1.0))[0]
    cpu.fp[1] = struct.unpack('<Q', struct.pack('<d', float(physical)))[0]
    cpu.n, cpu.z, cpu.c, cpu.v = 1, 0, 1, 0
    original_flags = cpu.n, cpu.z, cpu.c, cpu.v
    for r in keep:
        cpu.x[r] = 0xDEAD0000 + r
    cpu.run(entry, code)
    bad = [r for r in keep if cpu.x[r] != 0xDEAD0000 + r]
    if (cpu.n, cpu.z, cpu.c, cpu.v) != original_flags:
        bad.append('NZCV')
    return struct.unpack('<d', struct.pack('<Q', cpu.fp[0]))[0], bad


def _run_logical(img, header, u_step, v_step, keep=()):
    """Execute the installed common-join cave on non-contiguous guest pages."""
    entry = S._b_target(struct.unpack_from('<I', img, S.HOOK)[0], S.HOOK)
    code = _cave_map(img, entry)
    mem = E.Mem()
    cpu = E.Cpu(mem, host_base=0x700000000, paged=True)
    h = cpu.guest_to_host

    # Every dereferenced field crosses or approaches an unrelated guest page.
    #
    # FINDINGS-265: the header comes from the object's own chain, which is
    # what 0x672C95 itself walks. Build 253 read it from [EBP+0x10] -> +0x28;
    # in _load_texture that word is only ever COMPARED TO ZERO, never
    # dereferenced, so the marker never matched and the hook did nothing.
    ebp, hundred, texset, hdr, obj, ctx = (0x310FF8, 0x402FF0, 0x503FF4,
                                           0x504FFD, 0x605FF0, 0x950000)
    mem.setu(h(obj + 0x0C), hundred, 4)
    mem.setu(h(hundred + 0x14), texset, 4)
    mem.setu(h(texset + 0x84), hdr, 4)
    mem.setu(h(hdr + tex.O_USER_MARK),
             struct.unpack_from('<I', header, tex.O_USER_MARK)[0], 4)
    mem.setu(h(hdr + tex.O_USER_SCALE),
             struct.unpack_from('<I', header, tex.O_USER_SCALE)[0], 4)
    mem.setu(h(ebp - 0x10), obj, 4)
    mem.write(h(obj + 0x24), struct.pack('<f', u_step))
    mem.write(h(obj + 0x28), struct.pack('<f', v_step))
    mem.setu(ctx + S.RECIP_EBP, ebp, 4)

    cpu.x = [0x1000000000000000 + i for i in range(31)]
    cpu.x[19] = ctx
    cpu.x[30] = 0x00B00000
    cpu.fp[0] = 0x11223344
    cpu.fp[1] = 0x55667788
    cpu.sp = 0x8000000F0
    cpu.n, cpu.z, cpu.c, cpu.v = 1, 0, 1, 0
    original_x = list(cpu.x)
    original_fp = list(cpu.fp)
    original_sp = cpu.sp
    original_flags = cpu.n, cpu.z, cpu.c, cpu.v
    for r in keep:
        cpu.x[r] = original_x[r] = 0xDEAD0000 + r

    exit_pc = cpu.run(entry, code)
    got_u = struct.unpack('<f', mem.read(h(obj + 0x24), 4))[0]
    got_v = struct.unpack('<f', mem.read(h(obj + 0x28), 4))[0]
    bad = [r for r in keep if r != 8 and cpu.x[r] != original_x[r]]
    if cpu.x[8] != ebp:
        bad.append('displaced-ldr')
    if cpu.fp != original_fp:
        bad.append('FP')
    if cpu.sp != original_sp:
        bad.append('SP')
    if (cpu.n, cpu.z, cpu.c, cpu.v) != original_flags:
        bad.append('NZCV')
    return got_u, got_v, bad, exit_pc, cpu.translate_calls


def _hdr(width, height, scale=None, marked=True):
    h = bytearray(0xEC)
    struct.pack_into('<I', h, 0x00, 1)
    struct.pack_into('<I', h, 0x3C, width)
    struct.pack_into('<I', h, 0x40, height)
    if marked:
        struct.pack_into('<I', h, tex.O_USER_MARK, tex.SYW_SCALE_MARK)
    sx, sy = scale if scale else (1, 1)
    struct.pack_into('<I', h, tex.O_USER_SCALE, (sy << 16) | sx)
    return bytes(h)


def _run_path3(img, axis, word, physical, lr=None, keep=()):
    """Execute the installed path-3 dimension cave.

    w20 carries the physical extent, x0 the host destination, [x19,#8] the
    guest tex_header, and the FUNCTION's saved LR sits at [sp,#0x18] -- the
    caller gate reads it from there.
    """
    site = S.WRITER_U_SITE if axis == 'u' else S.WRITER_V_SITE
    entry = S._bl_target(struct.unpack_from('<I', img, site)[0], site)
    code = _cave_map(img, entry)
    mem = E.Mem()
    cpu = E.Cpu(mem, host_base=0x700000000, paged=True)
    h = cpu.guest_to_host
    hdr, ctx, dest = 0x605450, 0xA00000, 0x300000
    mem.setu(ctx + S.WRITER_SCRATCH, hdr, 4)
    mem.setu(h(hdr) + tex.O_USER_MARK, word, 4)
    cpu.sp = 0x800000400
    mem.setu(cpu.sp + 0x18,
             S.CALLER3_RETURN if lr is None else lr, 8)
    cpu.x[19] = ctx
    cpu.x[20] = physical
    cpu.x[0] = dest
    cpu.x[30] = 0xB00000
    cpu.n, cpu.z, cpu.c, cpu.v = 1, 0, 1, 0
    flags = cpu.n, cpu.z, cpu.c, cpu.v
    for r in keep:
        cpu.x[r] = 0xDEAD0000 + r
    cpu.run(entry, code)
    bad = [r for r in keep if cpu.x[r] != 0xDEAD0000 + r]
    if (cpu.n, cpu.z, cpu.c, cpu.v) != flags:
        bad.append('NZCV')
    if cpu.x[20] != physical:
        bad.append('w20')
    return mem.u(dest, 4), bad


def _word04(sx, sy):
    return (tex.SYW_SCALE_MAGIC << 16) | (sy << 8) | sx


@pytest.mark.skipif(not os.path.exists(MAIN), reason='needs stock exefs/main')
def test_stock_module_anchor_and_cave_readback():
    import nxmap
    module = nxmap.Main(MAIN)
    assert not S.verify(module)
    patches = S.build_patches(module.img, set(module.arm_starts))
    # The correction lands on 0x672C95's two dimension stores and nowhere
    # else. Every earlier shape is left at stock.
    assert all(patches[va] != S.WRITER_STOCK for va in S.WRITER_SITES)
    assert not any(va in patches for va in S.RECIP_SITES + S.RECIP2_SITES)
    assert not any(va in patches for va in S.DIMSTORE_SITES)
    assert not any(va in patches for va in S.SPT_SITES)
    assert S.HOOK not in patches
    image = bytearray(module.img)
    for va, word in patches.items():
        struct.pack_into('<I', image, va, word)
    assert S.state(image) == 'logical'
    assert S.installed(image)
    assert S.path3_installed(image)
    for site, axis in ((S.WRITER_U_SITE, 'u'), (S.WRITER_V_SITE, 'v')):
        entry = S._bl_target(S._word(image, site), site)
        assert entry is not None
        assert len(S._walk_helper(image, entry)) == S.PATH_DIM_WORDS
        assert S._path3_installed(image, entry, axis)
    # applying again must be a no-op
    assert S.build_patches(bytes(image), set(module.arm_starts)) == {}


@pytest.mark.skipif(not os.path.exists(MAIN), reason='needs stock exefs/main')
def test_path3_dimension_becomes_the_logical_extent():
    """physical in, logical out -- and only for the measured call site.

    FINDINGS-266: a probe divided the height by a different power of two per
    caller and the screen reported TWO stacked moons, which is caller 3.
    """
    import nxmap
    module = nxmap.Main(MAIN)
    image = bytearray(module.img)
    for va, word in S.build_patches(module.img,
                                    set(module.arm_starts)).items():
        struct.pack_into('<I', image, va, word)
    keep = tuple(r for r in range(1, 29) if r not in (19, 20, 30))
    for phys, sx, sy in ((512, 4, 4), (1024, 4, 4), (768, 3, 3),
                         (384, 3, 3), (1024, 16, 16), (768, 3, 6)):
        gu, bu = _run_path3(image, 'u', _word04(sx, sy), phys, keep=keep)
        gv, bv = _run_path3(image, 'v', _word04(sx, sy), phys, keep=keep)
        assert gu == phys // sx, (phys, sx, sy)
        assert gv == phys // sy, (phys, sx, sy)
        assert not bu and not bv


@pytest.mark.skipif(not os.path.exists(MAIN), reason='needs stock exefs/main')
def test_other_callers_and_bad_markers_pass_through_untouched():
    """The SPRITE loader sizes quads; touching it is FINDINGS-262.

    FINDINGS-267: _load_texture's three paths are all corrected -- each of
    their dimensions is used once, by a `fild` feeding the reciprocal, and by
    nothing else. Only 0x672E29 is excluded, because its dimensions are also
    stored raw into the sprite descriptor at +0x10/+0x14.
    """
    import nxmap
    module = nxmap.Main(MAIN)
    image = bytearray(module.img)
    for va, word in S.build_patches(module.img,
                                    set(module.arm_starts)).items():
        struct.pack_into('<I', image, va, word)
    keep = tuple(r for r in range(1, 29) if r not in (19, 20, 30))
    for lr in (S.SPRITE_RETURN, 0x123456):
        got, bad = _run_path3(image, 'u', _word04(4, 4), 512, lr=lr, keep=keep)
        assert got == 512, hex(lr)
        assert not bad
    # every loader path IS corrected
    for lr in S.LOADER_RETURNS:
        got, bad = _run_path3(image, 'u', _word04(4, 4), 512, lr=lr, keep=keep)
        assert got == 128, hex(lr)
        assert not bad
    # the retired SYW1 word must NOT validate: an old archive against a new
    # binary has to do nothing rather than something wrong
    # Each axis validates only its own byte, so a bad sy invalidates v alone.
    for word in (0, tex.SYW_SCALE_MARK, _word04(0, 4), _word04(17, 4),
                 0xFFFFFFFF):
        got, bad = _run_path3(image, 'u', word, 512, keep=keep)
        assert got == 512, hex(word)
        assert not bad
    for word in (0, tex.SYW_SCALE_MARK, _word04(4, 0), _word04(4, 17),
                 0xFFFFFFFF):
        got, bad = _run_path3(image, 'v', word, 512, keep=keep)
        assert got == 512, hex(word)
        assert not bad


@pytest.mark.skipif(not os.path.exists(MAIN), reason='needs stock exefs/main')
def test_every_earlier_shape_is_reverted_on_upgrade():
    """Two shapes at once would compound the scale."""
    import nxmap
    import a64 as A
    module = nxmap.Main(MAIN)
    image = bytearray(module.img)
    # plant the build-253 tail hook and the build-252 reciprocal shape
    pool = ff7nx_cave.HolePool(image, starts=set(module.arm_starts))
    entry, patches = ff7nx_cave.emit_laid_out(pool, S.logical_body,
                                              span=0x80000)
    patches[S.HOOK] = A.b(S.HOOK, entry)
    for va, word in patches.items():
        struct.pack_into('<I', image, va, word)
    assert S.state(image) == 'legacy-tail-only'
    upgrade = S.build_patches(image, set(module.arm_starts))
    assert upgrade[S.HOOK] == S.HOOK_STOCK
    for va, word in upgrade.items():
        struct.pack_into('<I', image, va, word)
    assert S.state(image) == 'logical'


def test_draw_helper_has_the_required_state_preservation_words():
    words = S.helper_body('u', 0, lambda i: i * 4)
    assert len(words) == 56
    assert words[0] == 0xA9B307E0             # stp x0,x1,[sp,#-0xd0]!
    assert words[10] == 0xAD050BE1            # stp q1,q2,[sp,#0xa0]
    assert words[11] == 0xD53B4211            # mrs x17,nzcv
    assert words[41] == 0x1E210800            # fmul s0,s0,s1
    assert words[43] == 0xD51B4211            # msr nzcv,x17
    assert words[-1] == 0xD65F03C0             # ret


@pytest.mark.parametrize('axis,marked,expected', [
    ('u', True, 1 / 256), ('v', True, 1 / 128),
    ('u', False, 1 / 768), ('v', False, 1 / 768),
])
def test_encoded_draw_helper_uses_the_live_header_chain(axis, marked, expected):
    """Run the emitted ARM words across deliberately non-contiguous pages."""
    base = 0x00A00000
    words = S.helper_body(axis, base, lambda i: base + i * 4)
    mem = E.Mem()
    cpu = E.Cpu(mem, host_base=0x700000000, paged=True)

    obj, hundred, texture_set, header = 0x00302120, 0x00403230, 0x00504340, 0x00605450
    host = cpu.guest_to_host
    mem.setu(host(obj) + 0x0C, hundred, 4)
    mem.setu(host(hundred) + 0x14, texture_set, 4)
    mem.setu(host(texture_set) + 0x84, header, 4)
    mem.setu(host(header) + tex.O_USER_MARK,
             tex.SYW_SCALE_MARK if marked else 0, 4)
    mem.setu(host(header) + tex.O_USER_SCALE, (6 << 16) | 3, 4)
    mem.write(host(obj) + 0x24, struct.pack('<f', 1 / 768))
    mem.write(host(obj) + 0x28, struct.pack('<f', 1 / 768))

    field = 0x24 if axis == 'u' else 0x28
    cpu.x = [0x1000000000000000 + i for i in range(31)]
    cpu.x[0] = host(obj) + field
    cpu.x[30] = 0x00B00000
    cpu.fp[1] = 0x3F654321
    cpu.fp[2] = 0x3F765432
    cpu.sp = 0x8000000D0
    cpu.n, cpu.z, cpu.c, cpu.v = 1, 0, 1, 0
    original_x = list(cpu.x)
    original_sp = cpu.sp
    original_flags = cpu.n, cpu.z, cpu.c, cpu.v
    exit_pc = cpu.run(base, words)

    got = struct.unpack('<f', struct.pack('<I', cpu.fp[0]))[0]
    assert got == pytest.approx(expected)
    assert exit_pc == 0x00B00000
    assert cpu.translate_calls == 3
    assert cpu.x == original_x
    assert cpu.fp[1] == 0x3F654321
    assert cpu.fp[2] == 0x3F765432
    assert cpu.sp == original_sp
    assert (cpu.n, cpu.z, cpu.c, cpu.v) == original_flags
    # The renderer consumes an adjusted value; the shared object remains raw.
    assert struct.unpack('<f', mem.read(host(obj) + field, 4))[0] == pytest.approx(1 / 768)


@pytest.mark.parametrize('bad_scale', [0, 17, 0xFFFF])
def test_encoded_draw_helper_rejects_malformed_axis_scale(bad_scale):
    base = 0x00A00000
    words = S.helper_body('u', base, lambda i: base + i * 4)
    mem = E.Mem()
    cpu = E.Cpu(mem, host_base=0x700000000, paged=True)
    obj, hundred, texture_set, header = 0x302120, 0x403230, 0x504340, 0x605450
    host = cpu.guest_to_host
    mem.setu(host(obj) + 0x0C, hundred, 4)
    mem.setu(host(hundred) + 0x14, texture_set, 4)
    mem.setu(host(texture_set) + 0x84, header, 4)
    mem.setu(host(header) + tex.O_USER_MARK, tex.SYW_SCALE_MARK, 4)
    mem.setu(host(header) + tex.O_USER_SCALE, (3 << 16) | bad_scale, 4)
    mem.write(host(obj) + 0x24, struct.pack('<f', 1 / 768))
    cpu.x[0] = host(obj) + 0x24
    cpu.x[30] = 0xB00000
    cpu.sp = 0x8000000D0
    cpu.run(base, words)
    got = struct.unpack('<f', struct.pack('<I', cpu.fp[0]))[0]
    assert got == pytest.approx(1 / 768)
