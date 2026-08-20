#!/usr/bin/env python3
"""
test_wsclamp.py -- prove the tile window actually moved, by execution.

The point of this file is that it does not trust the reading of the
disassembly that produced ff7nx_wsclamp. It runs the REAL words -- the
eleven-instruction flag-emulation block out of the module, and the cave
chained through the real padding addresses -- on the project's ARM64
interpreter, for a matrix of (tile, camera) pairs, and asks only one
question: was this tile drawn or culled?

A stock module must cull exactly `tile >= cam`. A patched one must cull
exactly `tile >= cam + R`. If the bias had gone the wrong way -- the failure
HANDOFF-48 §4.1 explicitly warned about, because it marked the operand roles
as inferred -- the window would move the other way and this would say so.

    python3 test_wsclamp.py <stock main>
"""
import os
import struct
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import a64 as A
import arm64emu
import ff7nx_wsclamp as C
import nxmap
import nso_patcher

MAIN = os.environ.get('FF7NX_MAIN', 'dump/exefs/main')
REGFILE = 0x50000000        # where the emulated x86 register file is parked
SKIP = 'culled'
KEEP = 'drawn'

_CACHE = {}


def module(values=None):
    """A (possibly patched) module image, memoised."""
    key = tuple(sorted((values or {}).items()))
    if key not in _CACHE:
        m = nxmap.Main(MAIN)
        if not values:
            _CACHE[key] = m.img
        else:
            from pathlib import Path
            import tempfile
            sp = C.spec(m.img, values, starts=set(m.arm_starts))
            nso = nso_patcher.read_nso(Path(MAIN))
            nso_patcher.apply_spec(nso, sp)
            fd, tmp = tempfile.mkstemp(suffix='.main')
            os.close(fd)
            try:
                Path(tmp).write_bytes(nso_patcher.rebuild(nso))
                _CACHE[key] = nxmap.Main(tmp).img
            finally:
                os.unlink(tmp)
    return _CACHE[key]


def block(img, name):
    """
    {address: word} for one cull's decision block, cave included.

    Runs from the hook site to the conditional branch that ends the test, so
    the exit address IS the verdict. The cave is added by following the hook
    branch, which is the only honest way to include it -- its words are
    scattered through padding and their addresses are what every branch in
    it resolved against.
    """
    site = C.CAVE_SITES[name]
    lo = site['va']
    # The block ends at the first b.cond at or after the `cmp` -- that IS the
    # cull decision. Found rather than hardcoded, because the recompiler puts
    # a variable number of flag-byte stores between the two.
    cmp_va = max(va for va, _ in site['sig'])
    hi = None
    for va in range(cmp_va + 4, cmp_va + 0x40, 4):
        w = struct.unpack_from('<I', img, va)[0]
        if (w & 0xFF000010) == 0x54000000:         # B.cond
            hi = va + 4
            break
    if hi is None:
        raise AssertionError('%s: no conditional branch after the cmp' % name)
    code = {va: struct.unpack_from('<I', img, va)[0]
            for va in range(lo, hi, 4)}
    w = code[lo]
    if (w & 0xFC000000) == 0x14000000:             # hooked -- pull the cave in
        off = w & 0x03FFFFFF
        if off & 0x02000000:
            off -= 0x04000000
        pc = lo + off * 4
        for _ in range(32):
            word = struct.unpack_from('<I', img, pc)[0]
            code[pc] = word
            if (word & 0xFC000000) == 0x14000000:
                o = word & 0x03FFFFFF
                if o & 0x02000000:
                    o -= 0x04000000
                tgt = pc + o * 4
                if lo <= tgt < hi:                 # returned to the game
                    break
                pc = tgt
                continue
            pc += 4
        else:
            raise AssertionError('cave at %s did not return' % name)
    return code, lo, hi


def verdict(img, name, tile, cam):
    """Run one cull decision. Returns 'drawn' or 'culled'."""
    code, lo, hi = block(img, name)
    mem = arm64emu.Mem()
    cpu = arm64emu.Cpu(mem)
    site = C.CAVE_SITES[name]
    # Which emulated x86 register holds the tile coordinate at this site is
    # baked into the block's own `ldr`; read it back out rather than restate
    # it, so the test cannot drift from the site table.
    ldr = struct.unpack_from('<I', img, lo - 4)[0]
    off = ((ldr >> 10) & 0xFFF) * 4
    regbase = REGFILE
    mem.setu(regbase + off, tile & 0xFFFFFFFF, 4)
    cpu.set(22, regbase)
    cpu.set(23, regbase)
    cpu.set(9, tile & 0xFFFFFFFF, w=True)
    cpu.set(8, cam & 0xFFFFFFFF, w=True)
    out = cpu.run(lo, None, code=code, start_pc=lo)
    # Falling off the end of the block is "kept"; the b.cond jumps backwards
    # to the loop's continue label, which is "culled".
    return KEEP if out == hi else SKIP


class TestOperandRoles(unittest.TestCase):
    """The thing HANDOFF-48 §4.1 could not settle."""

    def test_stock_culls_at_the_camera(self):
        img = module()
        for name in C.CULL_CAVE_SITES:
            for cam in (100, 300, 1000):
                self.assertEqual(verdict(img, name, cam - 1, cam), KEEP,
                                 '%s: tile just inside must be drawn' % name)
                self.assertEqual(verdict(img, name, cam, cam), SKIP,
                                 '%s: tile at the camera must be culled' % name)
                self.assertEqual(verdict(img, name, cam + 1, cam), SKIP,
                                 '%s: tile past the camera must be culled'
                                 % name)

    def test_bias_moves_the_edge_outward(self):
        """The sign check. A wrong-way bias fails here, loudly."""
        vals = C.defaults()
        img = module(vals)
        want = {'right1': vals['right'], 'right2': vals['right'],
                'bottom1': vals['bottom'], 'bottom2': vals['bottom']}
        for name, r in want.items():
            for cam in (100, 300, 1000):
                edge = cam + r
                self.assertEqual(verdict(img, name, edge - 1, cam), KEEP,
                                 '%s: tile at cam+%d-1 must now be drawn'
                                 % (name, r))
                self.assertEqual(verdict(img, name, edge, cam), SKIP,
                                 '%s: the new edge is cam+%d' % (name, r))

    def test_tiles_the_stock_window_dropped_are_now_drawn(self):
        """The whole point: strictly MORE tiles, never fewer."""
        stock = module()
        wide = module(C.shipped_values())
        r = C.shipped_values()
        for name in C.CULL_CAVE_SITES:
            bias = (r[name] if name in r else
                    r['right'] if name.startswith('right') else r['bottom'])
            gained = 0
            for cam in (64, 320, 800):
                for tile in range(cam - 40, cam + bias + 8):
                    s = verdict(stock, name, tile, cam)
                    w = verdict(wide, name, tile, cam)
                    if s == KEEP:
                        self.assertEqual(w, KEEP,
                                         '%s lost tile %d at cam %d'
                                         % (name, tile, cam))
                    elif w == KEEP:
                        gained += 1
            self.assertEqual(gained, bias * 3,
                             '%s should gain exactly %d tile positions per '
                             'camera' % (name, bias))

    def test_negative_and_wrapping_cameras(self):
        """Field coordinates are signed 16-bit; the compare must stay signed."""
        img = module(C.shipped_values())
        r = C.shipped_values()
        for name in C.CULL_CAVE_SITES:
            bias = (r[name] if name in r else
                    r['right'] if name.startswith('right') else r['bottom'])
            for cam in (-2000, -1, 0):
                self.assertEqual(verdict(img, name, cam + bias - 1, cam), KEEP,
                                 '%s at negative camera %d' % (name, cam))
                self.assertEqual(verdict(img, name, cam + bias, cam), SKIP,
                                 '%s at negative camera %d' % (name, cam))


def flip(img, name, cam=1000, lo=-400, hi=400):
    """
    Every `tile` offset from `cam` at which this site's branch changes answer.

    THE ONLY HONEST ORACLE FOR A WRAP. `verdict()` reports KEEP/SKIP, which is
    a CULL's vocabulary -- at a wrap site "taken" means "apply the wrap", not
    "skip this tile", so counting gained/lost tiles there measures nothing.
    This file already records shipping a decision made on that mistake. WHERE
    the answer flips is well-defined at both kinds of site, so that is all
    this reads.
    """
    out, prev = [], verdict(img, name, cam + lo, cam)
    for d in range(lo + 1, hi):
        cur = verdict(img, name, cam + d, cam)
        if cur != prev:
            out.append(d)
            prev = cur
    return out


class TestParallaxVertical(unittest.TestCase):
    """
    FINDINGS-205. The vertical twin of the Mt Corel fix.

    `bottom_offset` is 0 in stock and the picture runs to bg.y+16, so every
    parallax tile in that band tests as outside the wrap window and is
    teleported a whole layer height away. These three sites are the constant.
    """

    def test_stock_wraps_at_the_camera(self):
        img = module()
        for name in C.PARALLAX_BOTTOM_KNOBS:
            with self.subTest(name):
                self.assertEqual(
                    flip(img, name), [0],
                    '%s should have exactly one boundary, at the camera' % name)

    def test_bias_moves_the_wrap_point_by_exactly_the_bias(self):
        # FINDINGS-273: the WRAP BOUND is not the picture's bottom extent.
        # This used to read `parallax_bottom()` -- 16, "the same number
        # bottom1/bottom2 already ship on this axis" -- which compared a wrap
        # against a CULL. The bound has to cover where the ART can sit
        # relative to bg.y (junonl2 measures 264), not where the picture ends.
        # `parallax_half_height` still reads `parallax_bottom`, so the 120
        # below is untouched.
        want = C.parallax_bottom_bound()
        img = module(C.shipped_values())
        for name in C.PARALLAX_BOTTOM_KNOBS:
            with self.subTest(name):
                # The sweep must reach the bound: `flip`'s default window is
                # -400..400 and FINDINGS-273 puts the bound at 512, so a
                # default sweep reports [] -- no boundary found -- which is a
                # range limit and not a failure.
                self.assertEqual(
                    flip(img, name, lo=-64, hi=want + 64), [want],
                    '%s must wrap at cam+%d, which is where the ART can sit '
                    'relative to bg.y -- NOT the picture extent that '
                    'bottom1/bottom2 use (FINDINGS-273)' % (name, want))

    def test_all_three_move_together(self):
        """
        It is ONE `bottom_offset` in the C -- FFNx background.cpp:138 and :260,
        and :261 uses it a second time as layer 4's wrap DIRECTION test. Moving
        some and not others is the mistake that left Mt Corel half fixed.
        """
        v = C.shipped_values()
        self.assertEqual({v[k] for k in C.PARALLAX_BOTTOM_KNOBS},
                         {C.parallax_bottom_bound()})

    def test_the_x_axis_is_undisturbed(self):
        """Adding the y set must not move where the shipped x set wraps."""
        img = module(C.shipped_values())
        want = C.parallax_right(C.WS_SCALE)
        for name in C.PARALLAX_RIGHT_KNOBS:
            with self.subTest(name):
                self.assertEqual(flip(img, name), [want])

    def test_there_is_no_vertical_cull_to_pair_these_with(self):
        """
        The premise HANDOFF-204 s3.3 blocked on, tested rather than asserted.

        Every branch -- conditional or unconditional -- reaching either
        pick-loop head, on the stock module. If a y-axis one ever appears here
        the fix above is incomplete and this test is how that gets noticed.
        """
        img = module()

        def word(va):
            return struct.unpack_from('<I', img, va)[0]

        def target(va):
            w = word(va)
            if (w & 0xFF000010) == 0x54000000 or (w & 0x7E000000) == 0x34000000:
                imm = (w >> 5) & 0x7FFFF
                return va + (imm - 0x80000 if imm & 0x40000 else imm) * 4
            if (w & 0xFC000000) == 0x14000000:
                imm = w & 0x3FFFFFF
                return va + (imm - 0x4000000 if imm & 0x2000000 else imm) * 4
            return None

        # The loop's own back edge is in each set; every OTHER entry is a
        # `continue`, i.e. a cull, and not one of them is on the y axis.
        for head, lo, hi, want in (
                (0xA079E0, 0xA07780, 0xA08624,
                 {0xA07F80,          # anim_group
                  0xA085B0}),        # back edge
                (0xA0882C, 0xA08630, 0xA095AC,
                 {0xA08D10, 0xA08D14,   # x <= bg.x - 352
                  0xA08D68,             # x >= bg.x + 0   (pright4b)
                  0xA08E80,             # anim_group
                  0xA09000,             # palette_index
                  0xA09538})):          # back edge
            got = {va for va in range(lo, hi, 4) if target(va) == head}
            self.assertEqual(got, want,
                             'the set of branches reaching pick-loop head '
                             '%#x changed; re-run _ycull2.py before trusting '
                             'the parallax vertical fix' % head)


class TestSites(unittest.TestCase):

    def test_every_signature_matches_the_stock_module(self):
        C.check_all(module())

    def test_optional_sites_are_verified_without_raising(self):
        """
        Build 97 lost the ENTIRE 16:9 stage -- viewport, scissor, fade quad,
        every camera cave -- because one optional extra could raise inside
        `apply_module`'s transaction. HANDOFF-204 s4b.
        """
        img = module()
        self.assertEqual(C.verified_optional(img, C.PARALLAX_BOTTOM_KNOBS),
                         list(C.PARALLAX_BOTTOM_KNOBS))
        broken = bytearray(img)
        struct.pack_into('<I', broken, 0xA07BC4, 0xD503201F)    # nop the ldr
        self.assertNotIn('pbottom3',
                         C.verified_optional(bytes(broken),
                                             C.PARALLAX_BOTTOM_KNOBS))
        # ...and check_all, which DOES raise, must not see them at all.
        C.check_all(bytes(broken))

    def test_the_parallax_vertical_immediates_are_the_derived_values(self):
        """
        FINDINGS-208 corrected these. `ff7nx_uncrop` moves all four layers'
        tile origin 224 -> 232 -- it is in every build log -- so the parallax
        picture is `bg.y-232 .. bg.y+8`, and FFNx's own constants (256+8, 8,
        120) were right from the start.

        FINDINGS-205 s4 derived them against `ORIGIN_Y` = 224 instead and got
        `half_height` right BY COINCIDENCE: (224+16)/2 and (232+8)/2 are both
        120. That coincidence is what made three "independent" derivations
        look like they agreed, and it is asserted here so it cannot pass for
        agreement again.
        """
        v = C.shipped_values()
        self.assertEqual(C.PARALLAX_ORIGIN_Y, 232)
        self.assertEqual(v['ptop3'], C.STOCK_TOP + C.PARALLAX_UNCROP)
        self.assertEqual(v['ptop4'], C.STOCK_TOP + C.PARALLAX_UNCROP)
        self.assertEqual(v['ptop3'], 264)
        self.assertEqual(v['phalf3'], 120)
        self.assertEqual(
            (C.PARALLAX_ORIGIN_Y + C.PARALLAX_UNCROP) // 2, 120,
            'the parallax half_height is half the CENTRED picture')
        self.assertEqual((C.ORIGIN_Y + C.parallax_bottom()) // 2, 120,
                         'and the layer-1/2 arithmetic lands on the same '
                         'number, which is the coincidence FINDINGS-205 read '
                         'as corroboration')
        img = module(v)
        for name in ('ptop3', 'phalf3', 'ptop4'):
            self.assertEqual(C.read_value(img, name), v[name])

    def test_a_wrong_module_is_refused(self):
        img = bytearray(module())
        struct.pack_into('<I', img, 0xA072CC, 0xD503201F)      # nop the ldr
        with self.assertRaises(C.SiteMismatch):
            C.check_site(bytes(img), 'right1')

    def test_readback_round_trips(self):
        img = module(C.defaults())
        d = C.defaults()
        for name in ('left1', 'left2'):
            self.assertEqual(C.read_value(img, name), d['left'])
        for name in ('right1', 'right2'):
            self.assertEqual(C.read_value(img, name), d['right'])
        for name in ('bottom1', 'bottom2'):
            self.assertEqual(C.read_value(img, name), d['bottom'])

    def test_rehooking_is_refused(self):
        """Caves are not idempotent; the module must say so, not stack them."""
        img = module(C.defaults())
        with self.assertRaises(C.SiteMismatch):
            C.cave_patches(img, {'right1': 64},
                           starts=set(nxmap.Main(MAIN).arm_starts))

    def test_only_the_intended_words_change(self):
        stock, wide = module(), module(C.defaults())
        diff = [va for va in range(0, len(stock) - 3, 4)
                if stock[va:va + 4] != wide[va:va + 4]]
        planned = set(C.build(stock, C.defaults(),
                              starts=set(nxmap.Main(MAIN).arm_starts)))
        self.assertEqual(set(diff), planned)


class TestGeometry(unittest.TestCase):

    def test_defaults_cover_the_frame(self):
        d = C.defaults()
        lo, hi = C.visible_x()
        x0, x1 = C.span(C.ORIGIN_X, d['left'], d['right'])
        self.assertLessEqual(x0, lo)
        self.assertGreaterEqual(x1, hi)
        y0, y1 = C.span(C.ORIGIN_Y, d['top'], d['bottom'])
        self.assertLessEqual(y0, 0)
        self.assertGreaterEqual(y1, C.GAME_H)

    def test_minima_are_actually_minimal(self):
        r = C.required()
        lo, hi = C.visible_x()
        self.assertGreater(C.span(C.ORIGIN_X, r['left'] - 1, 0)[0], lo)
        self.assertLess(C.span(C.ORIGIN_X, 0, r['right'] - 1)[1], hi)

    def test_stock_leaves_exactly_the_reported_bars(self):
        """The bars HANDOFF-48 §0 measured, reproduced from the arithmetic."""
        lo, hi = C.visible_x()
        x0, x1 = C.span(C.ORIGIN_X, C.STOCK_LEFT, 0)
        self.assertAlmostEqual(x0 - lo, 74.67, places=1)   # left, before 376
        self.assertAlmostEqual(hi - x1, 106.67, places=1)  # right bar
        y0, y1 = C.span(C.ORIGIN_Y, C.STOCK_TOP, 0)
        self.assertEqual(C.GAME_H - y1, 32)                # bottom bar
        self.assertLessEqual(y0, 0)                        # top already covered

    def test_scale_of_one_needs_no_widening(self):
        r = C.required(scale=1.0)
        self.assertLessEqual(r['left'], C.STOCK_LEFT)
        self.assertLessEqual(r['right'], 0)

    def test_defaults_reproduce_the_stock_module_at_4_3(self):
        """
        The strongest check there is on the low-side tile margin: run the
        shipping formula at scale 1.0 and it must produce the numbers the
        original developers actually put in the module.

        STOCK_LEFT is 336 = required(320) + one 16-unit tile, and stock right
        is 0 = required(0) + nothing. If the tile term is dropped, left comes
        out 320 and this fails.
        """
        d = C.defaults(scale=1.0)
        self.assertEqual(d['left'], C.STOCK_LEFT)
        self.assertEqual(d['right'], 0)
        self.assertEqual(d['top'], C.STOCK_TOP)

    def test_low_side_admits_every_partially_visible_tile(self):
        """
        The left cull tests the tile's ORIGIN, so a tile whose origin is just
        outside the window still reaches TILE units back into the frame.
        Nothing that overlaps the frame may be culled.

        This is the regression that put a black band down the left: the
        shipped 376 leaves a 14-unit band of tile positions visible but
        culled, and the high side has no equivalent because culling by origin
        is conservative there.
        """
        for scale in (1.0, 0.75, 320 / 428.0):
            lo, hi = C.visible_x(scale)
            d = C.defaults(scale)
            # a tile at `u` units left of the camera spans dst_x
            # [(320-u)*2, (320-u)*2 + TILE*2]; it is visible when its RIGHT
            # edge is inside the frame
            u_max = (C.ORIGIN_X * C.MULT + C.TILE * C.MULT - lo) / C.MULT
            self.assertGreaterEqual(
                d['left'], u_max,
                'scale %.4f: tiles %g..%g units out are visible but culled'
                % (scale, d['left'], u_max))
            # and the high side genuinely does NOT need the margin
            self.assertGreaterEqual(C.span(C.ORIGIN_X, 0, d['right'])[1], hi)

    def test_the_shipped_376_was_short(self):
        """
        Records the bug so it cannot quietly come back.

        The shortfall depends on the shader scale, and both values matter
        because the build moved from a fixed 0.75 to WS_SCALE derived from
        the field buffer width (HANDOFF-51):

            0.75      -> 389.33 needed, 376 shipped, 13.33 units short
            320/428   -> 390.00 needed, 376 shipped, 14.00 units short
        """
        for scale, short in ((0.75, 13.33), (320 / 428.0, 14.0)):
            lo, _hi = C.visible_x(scale)
            u_max = (C.ORIGIN_X * C.MULT + C.TILE * C.MULT - lo) / C.MULT
            self.assertGreater(u_max, 376)      # 376 did not cover
            self.assertAlmostEqual(u_max - 376, short, places=1)
            self.assertLessEqual(u_max, C.defaults(scale)['left'])


if __name__ == '__main__':
    if len(sys.argv) > 1 and not sys.argv[1].startswith('-'):
        MAIN = sys.argv.pop(1)
    if not os.path.exists(MAIN):
        sys.exit('need the stock module: test_wsclamp.py <path to exefs/main>')
    unittest.main(verbosity=2)
