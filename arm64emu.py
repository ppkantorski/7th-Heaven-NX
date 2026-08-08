#!/usr/bin/env python3
"""
arm64emu.py -- a tiny ARM64 interpreter, just wide enough to execute the
dispatcher caves.

The point is to run the ACTUAL ENCODED WORDS rather than an idealised model of
them, so an encoding mistake shows up as a wrong result and not as a passing
test. Any instruction outside the supported subset raises, which also serves as
a check that the emitters never emit anything unexpected.

The translator call at 0x10FC3A0 is intercepted. On return it deliberately
fills every caller-saved register with garbage, which is what makes this a real
test of the AAPCS argument the caves rest on: if a cave depended on x1-x18 or
x30 surviving the call, it would fail here.
"""
import struct

TRANSLATE = 0x10FC3A0
M64 = (1 << 64) - 1
M32 = 0xFFFFFFFF
GARBAGE = 0xDEADBEEFCAFEF00D


class Unsupported(Exception):
    pass


PAGE = 0x1000


class Mem:
    """
    Sparse page-backed memory over a 64-bit address space. Untouched bytes read
    as zero. Paged rather than per-byte because the differential test executes
    the caves tens of thousands of times and a dict-per-byte made that too slow
    to run as part of the normal check.
    """

    __slots__ = ('pages',)

    def __init__(self):
        self.pages = {}

    def _page(self, addr):
        p = addr // PAGE
        pg = self.pages.get(p)
        if pg is None:
            pg = self.pages[p] = bytearray(PAGE)
        return pg, addr - p * PAGE

    def read(self, addr, n):
        pg, o = self._page(addr)
        if o + n <= PAGE:
            return bytes(pg[o:o + n])
        return b''.join(self.read(addr + i, 1) for i in range(n))

    def write(self, addr, data):
        n = len(data)
        pg, o = self._page(addr)
        if o + n <= PAGE:
            pg[o:o + n] = data
            return
        for i, v in enumerate(data):
            p2, o2 = self._page(addr + i)
            p2[o2] = v

    def u(self, addr, n):
        return int.from_bytes(self.read(addr, n), 'little')

    def setu(self, addr, val, n):
        self.write(addr, (val & ((1 << (8 * n)) - 1)).to_bytes(n, 'little'))


def s32(v):
    v &= M32
    return v - (1 << 32) if v & 0x80000000 else v


def s16(v):
    v &= 0xFFFF
    return v - (1 << 16) if v & 0x8000 else v


def s8(v):
    v &= 0xFF
    return v - (1 << 8) if v & 0x80 else v


class Cpu:
    def __init__(self, mem, host_base=0x700000000, native=None, paged=False):
        self.x = [0] * 32
        # S/D register file, raw 32-bit patterns. NOT `self.v` -- that is the
        # overflow flag, and the collision silently broke the flag word.
        self.fp = [0] * 32
        self.mem = mem
        self.n = self.z = self.c = self.v = 0
        self.host_base = host_base
        # See guest_to_host(). Off by default so the existing suites, which
        # were all written against the flat model and only ever translate a
        # complete effective address, keep working unchanged.
        self.paged = paged
        # {address: callable(cpu)} for calls this interpreter does not
        # execute but a cave legitimately makes -- the Switch port's native
        # dispatcher, for one. The callback stands in for the real thing and
        # is free to touch registers and memory exactly as it would.
        self.native = dict(native or {})
        self.translate_calls = 0
        self.sp = 0
        self.executed = 0
        self._block_lo = self._block_hi = None
        self._code = None

    # ---- register access; x31 reads as zero, writes are discarded --------
    def get(self, r, w=False):
        v = self.sp if r == 31 else self.x[r]
        return v & M32 if w else v & M64

    def set(self, r, val, w=False):
        if r == 31:
            return
        self.x[r] = (val & M32) if w else (val & M64)

    def get_sp(self):
        return self.sp

    def _setflags32(self, a, b, res, sub):
        self.n = 1 if res & 0x80000000 else 0
        self.z = 1 if (res & M32) == 0 else 0
        if sub:
            self.c = 1 if (a & M32) >= (b & M32) else 0
            self.v = 1 if (s32(a) - s32(b)) != s32(res) else 0
        else:
            self.c = 1 if (a & M32) + (b & M32) > M32 else 0
            self.v = 1 if (s32(a) + s32(b)) != s32(res) else 0

    def cond(self, c):
        n, z, cc, v = self.n, self.z, self.c, self.v
        base = {0: z, 1: not z, 2: cc, 3: not cc, 4: n, 5: not n,
                6: v, 7: not v, 8: cc and not z, 9: not (cc and not z),
                10: n == v, 11: n != v, 12: not z and n == v,
                13: z or n != v, 14: True, 15: True}[c]
        return bool(base)

    # ------------------------------------------------------------------ run
    def run(self, base, words, max_steps=100000, stop_at=None, start_pc=None,
            code=None):
        """
        Execute `words` placed at `base`. Returns the address branched to once
        control leaves the block (that is how the cave's exit is observed).

        `start_pc`, if given, is where execution actually begins (must still
        lie within [base, base+4*len(words))); it defaults to `base`. This
        exists for testing a real multi-cave layout where one shared
        subroutine sits at a lower address than the site under test: the
        whole region has to be one contiguous `words` array (bl/adrp/b are
        all PC-relative against real addresses), but execution starts at the
        site's own entry point, not at the start of the array.

        `code`, if given, replaces both: a {address: word} map for a cave that
        is NOT contiguous -- one chained through reclaimed alignment padding.
        Execution then follows the real addresses, `b`s between runs and all,
        and leaves when the pc lands on an address the map does not hold. This
        is the only way to test such a cave honestly: the addresses ARE the
        thing under test, since every branch, adrp and label in it resolved
        against them.
        """
        if code is not None:
            pc = base if start_pc is None else start_pc
            if pc not in code:
                raise Unsupported('entry 0x%X is not in the code map' % pc)
            self._block_lo = min(code)
            self._block_hi = max(code) + 4
            self._code = code
            for _ in range(max_steps):
                if pc not in code:
                    return pc
                if stop_at is not None and pc == stop_at:
                    return pc
                self.executed += 1
                nxt = self.step(code[pc], pc)
                pc = nxt if nxt is not None else pc + 4
            raise Unsupported('did not terminate')
        lo, hi = base, base + 4 * len(words)
        self._block_lo, self._block_hi = lo, hi
        self._code = None
        pc = base if start_pc is None else start_pc
        if not (lo <= pc < hi):
            raise Unsupported('start_pc 0x%X is outside the block [0x%X, 0x%X)'
                              % (pc, lo, hi))
        for _ in range(max_steps):
            if not (lo <= pc < hi):
                return pc
            if stop_at is not None and pc == stop_at:
                return pc
            w = words[(pc - lo) // 4]
            self.executed += 1
            nxt = self.step(w, pc)
            pc = nxt if nxt is not None else pc + 4
        raise Unsupported('did not terminate')

    def step(self, w, pc):
        g, s = self.get, self.set

        rd = w & 0x1F
        rn = (w >> 5) & 0x1F
        rm = (w >> 16) & 0x1F

        # In the DATA-PROCESSING register forms below, register 31 is the
        # ZERO register, not SP. `self.get` maps 31 to SP because every form
        # this interpreter previously decoded was an addressing one, where it
        # is. Reading `wzr` as the stack pointer turns `mov wN, wM` (encoded
        # as `orr wN, wzr, wM`) into garbage, so these handlers -- and only
        # these -- use a zero-aware read.
        def gz(r, wide=True):
            return 0 if r == 31 else self.get(r, wide)

        # ---- the handful of forms the 360-degree movement cave needs.
        # Each is decoded exactly, not approximated: an FP load moves a raw
        # 32-bit pattern, `fsub` is real IEEE single arithmetic done through
        # struct, and `fcvtzs` truncates toward zero exactly as the hardware
        # does. test_analog_cave.py checks all three against Python floats.
        if (w & 0xFFC00000) == 0xBD400000:                    # ldr St,[Xn,#i]
            a = self._addr(rn, ((w >> 10) & 0xFFF) * 4)
            self.fp[rd] = self.mem.u(a, 4)
            return None
        if (w & 0xFFE0FC00) == 0x1E203800:                    # fsub Sd,Sn,Sm
            x = struct.unpack('<f', struct.pack('<I', self.fp[rn]))[0]
            y = struct.unpack('<f', struct.pack('<I', self.fp[rm]))[0]
            self.fp[rd] = struct.unpack('<I', struct.pack('<f', x - y))[0]
            return None
        if (w & 0xFFFF0000) == 0x1E180000:                    # fcvtzs Wd,Sn,#f
            fbits = 64 - ((w >> 10) & 0x3F)
            x = struct.unpack('<f', struct.pack('<I', self.fp[rn]))[0]
            v = x * (1 << fbits)
            v = int(v) if v >= 0 else -int(-v)                # toward zero
            v = max(-0x80000000, min(0x7FFFFFFF, v))
            return s(rd, v, True)
        if (w & 0xFFE0FC00) == 0x1AC00C00:                    # sdiv Wd,Wn,Wm
            n_, d_ = s32(gz(rn)), s32(gz(rm))
            if d_ == 0:
                return s(rd, 0, True)
            q = abs(n_) // abs(d_)
            return s(rd, q if (n_ < 0) == (d_ < 0) else -q, True)
        if (w & 0xFFE0FC00) == 0x4A000000:                    # eor Wd,Wn,Wm
            return s(rd, gz(rn) ^ gz(rm), True)
        if (w & 0xFFE0FC00) == 0x2A000000:                    # orr Wd,Wn,Wm
            return s(rd, gz(rn) | gz(rm), True)
        if (w & 0xFFE0FC00) == 0x4B000000:                    # sub Wd,Wn,Wm
            return s(rd, gz(rn) - gz(rm), True)
        # 0xFFC00000, not 0xFF800000: bit 22 is the `lsl #12` flag, and a
        # looser mask silently swallowed `sub Wd,Wn,#imm,lsl #12` and applied
        # the immediate unshifted -- which turned (x-4096) into (x-1) in the
        # limit-aura cave and nowhere else.
        if (w & 0xFFC00000) == 0x51000000:                    # sub Wd,Wn,#imm
            return s(rd, gz(rn) - ((w >> 10) & 0xFFF), True)
        if (w & 0xFFE00000) == 0x0B000000:                    # add Wd,Wn,Wm,lsl
            sh = (w >> 10) & 0x3F
            return s(rd, gz(rn) + ((gz(rm) << sh) & M32), True)
        if (w & 0xFFE00C00) == 0xB8400000:                    # ldur Wt,[Xn,#i]
            imm9 = (w >> 12) & 0x1FF
            if imm9 & 0x100:
                imm9 -= 0x200
            return s(rd, self.mem.u(self._rd64(rn) + imm9, 4), True)
        if (w & 0xFFE00C00) == 0x78C00000:                    # ldursh Wt
            imm9 = (w >> 12) & 0x1FF
            if imm9 & 0x100:
                imm9 -= 0x200
            return s(rd, s16(self.mem.u(self._rd64(rn) + imm9, 2)), True)
        if (w & 0xFFE00C00) == 0x78000000:                    # sturh Wt
            imm9 = (w >> 12) & 0x1FF
            if imm9 & 0x100:
                imm9 -= 0x200
            self.mem.setu(self._rd64(rn) + imm9, g(rd, True), 2)
            return None
        if (w & 0xFFC00000) == 0xA9000000:                    # stp Xa,Xb,[Xn,#i]
            imm7 = (w >> 15) & 0x7F
            if imm7 & 0x40:
                imm7 -= 0x80
            a = self._rd64(rn) + imm7 * 8
            self.mem.setu(a, g(rd), 8)
            self.mem.setu(a + 8, g((w >> 10) & 0x1F), 8)
            return None
        if (w & 0xFFC00000) == 0xA9400000:                    # ldp Xa,Xb,[Xn,#i]
            imm7 = (w >> 15) & 0x7F
            if imm7 & 0x40:
                imm7 -= 0x80
            a = self._rd64(rn) + imm7 * 8
            self._wr64(rd, self.mem.u(a, 8))
            self._wr64((w >> 10) & 0x1F, self.mem.u(a + 8, 8))
            return None
        if (w & 0xFFC00000) == 0x29400000:                    # ldp Wa,Wb,[Xn,#i]
            imm7 = (w >> 15) & 0x7F
            if imm7 & 0x40:
                imm7 -= 0x80
            a = self._rd64(rn) + imm7 * 4
            s(rd, self.mem.u(a, 4), True)
            s((w >> 10) & 0x1F, self.mem.u(a + 4, 4), True)
            return None
        if (w & 0xFFC00000) == 0x29000000:                    # stp Wa,Wb,[Xn,#i]
            imm7 = (w >> 15) & 0x7F
            if imm7 & 0x40:
                imm7 -= 0x80
            a = self._rd64(rn) + imm7 * 4
            self.mem.setu(a, g(rd, True), 4)
            self.mem.setu(a + 4, g((w >> 10) & 0x1F, True), 4)
            return None
        if (w & 0xFFE00C00) == 0x1A800000:                    # csel Wd,Wn,Wm,c
            return s(rd, gz(rn) if self.cond((w >> 12) & 0xF) else gz(rm), True)
        if (w & 0xFF000000) == 0xB4000000:                    # cbz Xt
            if g(rd) == 0:
                off = (w >> 5) & 0x7FFFF
                if off & 0x40000:
                    off -= 0x80000
                return pc + off * 4
            return None

        # ---- loads / stores, 32-bit unsigned-offset immediate ------------
        if (w & 0xFFC00000) == 0xB9400000:                    # ldr Wt,[Xn,#i]
            a = self._addr(rn, ((w >> 10) & 0xFFF) * 4)
            return s(rd, self.mem.u(a, 4), True)
        if (w & 0xFFC00000) == 0xB9000000:                    # str Wt,[Xn,#i]
            a = self._addr(rn, ((w >> 10) & 0xFFF) * 4)
            self.mem.setu(a, gz(rd, True), 4)
            return None
        if (w & 0xFFC00000) == 0xF9400000:                    # ldr Xt,[Xn,#i]
            a = self._addr(rn, ((w >> 10) & 0xFFF) * 8)
            return s(rd, self.mem.u(a, 8))
        if (w & 0xFFC00000) == 0xF9000000:                    # str Xt,[Xn,#i]
            a = self._addr(rn, ((w >> 10) & 0xFFF) * 8)
            self.mem.setu(a, gz(rd, False), 8)
            return None
        if (w & 0xFFC00000) == 0x79400000:                    # ldrh
            a = self._addr(rn, ((w >> 10) & 0xFFF) * 2)
            return s(rd, self.mem.u(a, 2), True)
        if (w & 0xFFC00000) == 0x79000000:                    # strh
            a = self._addr(rn, ((w >> 10) & 0xFFF) * 2)
            self.mem.setu(a, gz(rd, True), 2)
            return None
        if (w & 0xFFC00000) == 0x79C00000:                    # ldrsh -> Wt
            a = self._addr(rn, ((w >> 10) & 0xFFF) * 2)
            return s(rd, s16(self.mem.u(a, 2)), True)
        if (w & 0xFFC00000) == 0x39400000:                    # ldrb
            a = self._addr(rn, (w >> 10) & 0xFFF)
            return s(rd, self.mem.u(a, 1), True)
        if (w & 0xFFC00000) == 0x39000000:                    # strb
            a = self._addr(rn, (w >> 10) & 0xFFF)
            self.mem.setu(a, gz(rd, True), 1)
            return None
        if (w & 0xFFC00000) == 0x39C00000:                    # ldrsb -> Wt
            a = self._addr(rn, (w >> 10) & 0xFFF)
            return s(rd, s8(self.mem.u(a, 1)), True)

        # ---- 32-bit LDR, post-indexed. The throttle registration cave walks
        # its exclusion table with this; the base register is written back even
        # on the iteration that exits, which is exactly the kind of detail that
        # is worth executing rather than reasoning about.
        if (w & 0xFFE00C00) == 0xB8400400:                    # ldr Wt,[Xn],#i
            imm9 = (w >> 12) & 0x1FF
            if imm9 & 0x100:
                imm9 -= 0x200
            a = self._rd64(rn)
            s(rd, self.mem.u(a, 4), True)
            self._wr64(rn, a + imm9)
            return None

        # ---- add / sub immediate ----------------------------------------
        imm12 = (w >> 10) & 0xFFF
        sh = (w >> 22) & 1
        if sh:
            imm12 <<= 12
        if (w & 0x7F800000) in (0x11000000, 0x11800000) and not (w & 0x80000000):
            return s(rd, (g(rn, True) + imm12) & M32, True)   # add Wd,Wn,#i
        if (w & 0xFF800000) in (0x51000000, 0x51800000):      # sub Wd,Wn,#i
            return s(rd, (g(rn, True) - imm12) & M32, True)
        if (w & 0xFF800000) in (0x91000000, 0x91800000):      # add Xd,Xn,#i
            return self._wr64(rd, self._rd64(rn) + imm12)
        if (w & 0xFF800000) in (0xD1000000, 0xD1800000):
            return self._wr64(rd, self._rd64(rn) - imm12)     # sub Xd,Xn,#i
        if (w & 0xFF800000) in (0x71000000, 0x71800000):      # subs Wd,Wn,#i
            a, b = g(rn, True), imm12
            res = (a - b) & M32
            self._setflags32(a, b, res, True)
            s(rd, res, True)
            return None
        if (w & 0xFF800000) in (0x31000000, 0x31800000):      # adds Wd,Wn,#i
            a, b = g(rn, True), imm12
            res = (a + b) & M32
            self._setflags32(a, b, res, False)
            s(rd, res, True)
            return None

        # ---- add / subs shifted register --------------------------------
        if (w & 0xFF200000) == 0x0B000000:                    # add Wd,Wn,Wm,sh
            amt = (w >> 10) & 0x3F
            typ = (w >> 22) & 3
            # LSR is not decoration here: the wrapper's midpoint uses
            # `add Wm, Wm, Wm, LSR #31` to add one when the delta is negative,
            # which is what makes the halving truncate toward zero the way
            # FFNx's C division does rather than toward minus infinity.
            v = g(rm, True)
            if typ == 0:
                v = (v << amt) & M32
            elif typ == 1:
                v = v >> amt
            elif typ == 2:
                v = (s32(v) >> amt) & M32
            else:
                raise Unsupported('add shift type %d at 0x%X' % (typ, pc))
            return s(rd, (g(rn, True) + v) & M32, True)
        if (w & 0xFF200000) == 0x6B000000:                    # subs Wd,Wn,Wm,sh
            amt = (w >> 10) & 0x3F
            a, b = g(rn, True), (g(rm, True) << amt) & M32
            res = (a - b) & M32
            self._setflags32(a, b, res, True)
            s(rd, res, True)
            return None
        if (w & 0xFF200000) == 0x8B000000:                    # add Xd,Xn,Xm
            amt = (w >> 10) & 0x3F
            return self._wr64(rd, self._rd64(rn) + ((self.get(rm) << amt) & M64))
        if (w & 0xFFE0FFE0) == 0x2A0003E0:                    # mov Wd,Wm
            return s(rd, g(rm, True), True)
        if (w & 0xFFE0FC00) == 0x1B007C00:                    # mul Wd,Wn,Wm
            return s(rd, (g(rn, True) * g(rm, True)) & M32, True)

        # ---- shifted-register SUB and AND -------------------------------
        # The recompiler open-codes x86 flag computations with these, so any
        # test that executes a translated compare needs them.
        if (w & 0xFF200000) == 0x4B000000:                    # sub Wd,Wn,Wm,sh
            amt = (w >> 10) & 0x3F
            if (w >> 22) & 3:
                raise Unsupported('sub shift type at 0x%X' % pc)
            return s(rd, (g(rn, True) - ((g(rm, True) << amt) & M32)) & M32,
                     True)
        if (w & 0xFF200000) == 0x2A000000 and ((w >> 10) & 0x3F) != 0:
            # orr Wd,Wn,Wm,LSL #sh -- the SHIFTED form only. Unshifted ORR is
            # `mov Wd,Wm` and is decoded further down; splitting on the shift
            # amount keeps that alias exactly where it was rather than
            # rerouting it through here. The wrapper uses this to pack the
            # phase bit into the tick word: `orr Wd, Wtick, Wphase, LSL #30`.
            amt = (w >> 10) & 0x3F
            if (w >> 22) & 3:
                raise Unsupported('orr shift type at 0x%X' % pc)
            a = 0 if rn == 31 else g(rn, True)
            b = 0 if rm == 31 else g(rm, True)
            return s(rd, a | ((b << amt) & M32), True)
        if (w & 0xFF200000) == 0x0A000000:                    # and Wd,Wn,Wm,sh
            amt = (w >> 10) & 0x3F
            if (w >> 22) & 3:
                raise Unsupported('and shift type at 0x%X' % pc)
            return s(rd, g(rn, True) & ((g(rm, True) << amt) & M32), True)

        # ---- logical immediate: AND / ORR Wd, Wn, #mask -----------------
        # Register 31 is the ZERO register for these, not SP -- `mov Wd,#imm`
        # is `orr Wd, WZR, #imm`, and reading SP there would silently produce a
        # garbage constant instead of the immediate.
        if (w & 0xFF800000) == 0x12000000:
            immr = (w >> 16) & 0x3F
            imms = (w >> 10) & 0x3F
            mask = self._decode_bitmask(immr, imms)
            src = 0 if rn == 31 else g(rn, True)
            return s(rd, src & mask, True)
        if (w & 0xFF800000) == 0x52000000:                    # eor Wd,Wn,#mask
            # The wrapper flips the phase bit with `eor Wd, Wn, #1`. Encoded
            # as a logical immediate, so it needs the same bitmask decode as
            # AND/ORR rather than a special case for the constant 1.
            immr = (w >> 16) & 0x3F
            imms = (w >> 10) & 0x3F
            mask = self._decode_bitmask(immr, imms)
            src = 0 if rn == 31 else g(rn, True)
            return s(rd, src ^ mask, True)
        if (w & 0xFF800000) == 0x32000000:
            immr = (w >> 16) & 0x3F
            imms = (w >> 10) & 0x3F
            mask = self._decode_bitmask(immr, imms)
            src = 0 if rn == 31 else g(rn, True)
            return s(rd, src | mask, True)

        # ---- bitfield moves: LSL / ASR ----------------------------------
        if (w & 0xFFC00000) == 0x53000000:                    # UBFM, 32-bit
            # The general form, not just the lsl/lsr aliases. `ubfx` shows up
            # in the boss-death wobble patch (`ubfx w8,w19,#2,#1`), and the
            # special-cased version raised Unsupported on it -- which would
            # have made a real patch untestable rather than wrong.
            #   imms >= immr :  (Wn >> immr) & mask(imms - immr + 1)   [ubfx/lsr]
            #   imms <  immr :  (Wn & mask(imms + 1)) << (32 - immr)   [lsl/ubfiz]
            immr = (w >> 16) & 0x3F
            imms = (w >> 10) & 0x3F
            v = g(rn, True)
            if imms >= immr:
                width = imms - immr + 1
                return s(rd, (v >> immr) & ((1 << width) - 1), True)
            return s(rd, ((v & ((1 << (imms + 1)) - 1)) << (32 - immr)) & M32,
                     True)
        if (w & 0xFFC00000) == 0x13000000:                    # SBFM (asr)
            immr = (w >> 16) & 0x3F
            imms = (w >> 10) & 0x3F
            if imms != 31:
                raise Unsupported('unsupported SBFM imms=%d at 0x%X' % (imms, pc))
            return s(rd, (s32(g(rn, True)) >> immr) & M32, True)

        # ---- moves -------------------------------------------------------
        if (w & 0xFFE00000) == 0x52800000:                    # movz Wd,#i
            return s(rd, (w >> 5) & 0xFFFF, True)
        if (w & 0xFFE00000) == 0x52A00000:                    # movz Wd,#i,lsl16
            return s(rd, ((w >> 5) & 0xFFFF) << 16, True)
        if (w & 0xFFE00000) == 0x72800000:                    # movk Wd,#i
            return s(rd, (g(rd, True) & 0xFFFF0000) | ((w >> 5) & 0xFFFF), True)
        if (w & 0xFFE00000) == 0x72A00000:                    # movk Wd,#i,lsl16
            return s(rd, (g(rd, True) & 0xFFFF) | (((w >> 5) & 0xFFFF) << 16),
                     True)
        if (w & 0x9F000000) == 0x90000000:                    # adrp Xd,label
            imm = (((w >> 5) & 0x7FFFF) << 2) | ((w >> 29) & 3)
            if imm & 0x100000:
                imm -= 0x200000
            return self._wr64(rd, ((pc & ~0xFFF) + imm * 0x1000) & M64)
        if (w & 0x9F000000) == 0x10000000:                    # adr Xd,label
            imm = (((w >> 5) & 0x7FFFF) << 2) | ((w >> 29) & 3)
            if imm & 0x100000:
                imm -= 0x200000
            return self._wr64(rd, (pc + imm) & M64)

        # ---- branches ----------------------------------------------------
        if (w & 0xFC000000) == 0x14000000:                    # b
            imm = w & 0x3FFFFFF
            if imm & 0x2000000:
                imm -= 0x4000000
            return pc + imm * 4
        if (w & 0xFFC00000) == 0xA9800000:                    # stp pre-index
            imm = (w >> 15) & 0x7F
            if imm & 0x40:
                imm -= 0x80
            rt2 = (w >> 10) & 0x1F
            addr = (self.sp if rn == 31 else self.x[rn]) + imm * 8
            self.mem.setu(addr, self.x[rd] & 0xFFFFFFFFFFFFFFFF, 8)
            self.mem.setu(addr + 8, self.x[rt2] & 0xFFFFFFFFFFFFFFFF, 8)
            if rn == 31:
                self.sp = addr
            else:
                self.x[rn] = addr
            return None
        if (w & 0xFFC00000) == 0xA8C00000:                    # ldp post-index
            imm = (w >> 15) & 0x7F
            if imm & 0x40:
                imm -= 0x80
            rt2 = (w >> 10) & 0x1F
            addr = self.sp if rn == 31 else self.x[rn]
            self.x[rd] = self.mem.u(addr, 8)
            self.x[rt2] = self.mem.u(addr + 8, 8)
            if rn == 31:
                self.sp = addr + imm * 8
            else:
                self.x[rn] = addr + imm * 8
            return None
        if (w & 0xFFFFFC1F) == 0xD65F0000:                    # ret
            return self.x[(w >> 5) & 0x1F]
        if (w & 0xFC000000) == 0x94000000:                    # bl
            imm = w & 0x3FFFFFF
            if imm & 0x2000000:
                imm -= 0x4000000
            tgt = pc + imm * 4
            if tgt in self.native:
                self.x[30] = pc + 4
                self.native[tgt](self)
                return None
            if tgt == TRANSLATE:
                self._do_translate(pc)
                return None
            in_block = (tgt in self._code if self._code is not None
                        else (self._block_lo is not None
                              and self._block_lo <= tgt < self._block_hi))
            if in_block:
                # A real intra-block call -- e.g. a per-site stub calling a
                # shared subroutine placed later in the same cave. Link and
                # jump; the callee's own `ret` reads x30 back out, same as
                # real hardware. Nothing outside the currently executing
                # block can be reached this way, so a bl to a stray/wrong
                # address still falls through to the raise below exactly as
                # it always has.
                self.x[30] = pc + 4
                return tgt
            raise Unsupported('bl to 0x%X, only the translator, modelled '
                              'native calls, and intra-block calls are '
                              'handled' % tgt)
        if (w & 0xFF000000) == 0x54000000:                    # b.cond
            imm = (w >> 5) & 0x7FFFF
            if imm & 0x40000:
                imm -= 0x80000
            return pc + imm * 4 if self.cond(w & 0xF) else None
        if (w & 0x7F000000) == 0x34000000:                    # cbz
            imm = (w >> 5) & 0x7FFFF
            if imm & 0x40000:
                imm -= 0x80000
            return pc + imm * 4 if self.get(rd, True) == 0 else None
        if (w & 0x7F000000) == 0x35000000:                    # cbnz
            imm = (w >> 5) & 0x7FFFF
            if imm & 0x40000:
                imm -= 0x80000
            return pc + imm * 4 if self.get(rd, True) != 0 else None
        if (w & 0x7E000000) == 0x36000000:                    # tbz / tbnz
            bit = ((w >> 26) & 0x20) | ((w >> 19) & 0x1F)
            imm = (w >> 5) & 0x3FFF
            if imm & 0x2000:
                imm -= 0x4000
            wide = bool(w & 0x80000000)
            v = self.get(rd) if wide else self.get(rd, True)
            set_ = (v >> bit) & 1
            taken = set_ if (w & 0x01000000) else not set_
            return pc + imm * 4 if taken else None
        raise Unsupported('unsupported instruction %08X at 0x%X' % (w, pc))

    # ---- helpers ---------------------------------------------------------
    def _addr(self, rn, off):
        return ((self.sp if rn == 31 else self.x[rn]) + off) & M64

    def _rd64(self, rn):
        return (self.sp if rn == 31 else self.x[rn]) & M64

    def _wr64(self, rd, val):
        if rd == 31:
            self.sp = val & M64
        else:
            self.x[rd] = val & M64
        return None

    def guest_to_host(self, va):
        """
        Guest VA -> host address.

        The real translator (0x10FC3A0) is a PAGE TABLE:

            lsr w9, w0, #0xc             ; guest page number
            ldr x8, [x8, w9, uxtw #3]    ; host base of THAT page
            and w9, w0, #0xfff
            add x9, x8, x9

        so consecutive guest pages are NOT consecutive host pages, and
        `translate(p) + n` is the address of `p + n` only while n stays inside
        the same 4 KB page. `paged=True` models that; the mapping is a bit
        reversal of the page number, which is a bijection -- distinct guest
        pages can never alias -- and which puts neighbouring pages far apart,
        so any cave that indexes off a translated pointer reads somewhere it
        obviously should not rather than getting away with it.

        The flat default is what every suite before the 360-movement one was
        written against. It is safe for a cave that only ever translates a
        finished effective address, and it is exactly the assumption that hid
        a real bug in the first version of the analog cave, so a new cave that
        touches guest memory should be tested with paged=True.
        """
        if not self.paged:
            return self.host_base + va
        p = (va >> 12) & 0xFFFFF
        q = int('{:020b}'.format(p)[::-1], 2)
        return self.host_base + q * PAGE + (va & 0xFFF)

    def _do_translate(self, pc):
        """
        Model of the recompiler's guest->host address translation, plus the
        AAPCS reality that x0-x18 and x30 may come back holding anything.
        """
        guest = self.x[0] & M32
        self.translate_calls += 1
        for r in list(range(1, 19)) + [30]:
            self.x[r] = GARBAGE
        self.x[0] = self.guest_to_host(guest)
        # Flags are also unspecified across a call.
        self.n = self.z = self.c = self.v = 1

    @staticmethod
    def _decode_bitmask(immr, imms):
        """
        Logical-immediate decode. Deliberately delegates to the project's
        existing decoder, which test_bitmask.py already checks against capstone
        over all 11,328 legal encodings. Writing a second implementation here
        would just create a second thing that can be wrong -- and the first
        hand-rolled version of that decoder was wrong on 87% of encodings while
        looking entirely plausible.
        """
        from ff7nx_resolve import decode_bitmask
        v = decode_bitmask(0, 0, immr, imms)
        if v is None:
            raise Unsupported('reserved logical immediate immr=%d imms=%d'
                              % (immr, imms))
        return v & M32