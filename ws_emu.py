#!/usr/bin/env python3
"""
Execute gfx_drv_setviewport's REAL ENCODED WORDS and read out every value it
writes -- so a candidate patch is judged by what actually moves, not by what
the disassembly looks like it should do.

This is the tool that would have caught the last two failures. The `probe`
patch changed the device rect and left `_11` at 1.0; running it here shows
that in one line instead of one hardware build.

`arm64emu.Cpu` covers the integer subset the 60 FPS caves need. This subclass
adds the float and 64-bit multiply/bitfield forms this particular function
uses, and nothing else -- anything unrecognised still raises, so a silent
mis-decode is not possible.

Usage:  PYTHONPATH=. python3 ws_emu.py
"""
import struct
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import arm64emu                                                 # noqa: E402
import nxmap                                                    # noqa: E402

SETVIEWPORT = 0x10D6760
M32 = 0xFFFFFFFF


def f2b(x):
    return struct.unpack('<I', struct.pack('<f', x))[0]


def b2f(b):
    return struct.unpack('<f', struct.pack('<I', b & M32))[0]


def _logical_imm64(w):
    """ARM DecodeBitMasks for the 64-bit logical-immediate forms."""
    n = (w >> 22) & 1
    immr = (w >> 16) & 0x3F
    imms = (w >> 10) & 0x3F
    if n:
        size, length = 64, imms
    else:
        size = 32
        while size > 2 and (imms & (size >> 1)):
            size >>= 1
        length = imms & ((size >> 1) - 1)
    ones = length + 1
    pattern = (1 << ones) - 1
    rot = immr % size
    pattern = ((pattern >> rot) | (pattern << (size - rot))) & ((1 << size) - 1)
    out = 0
    for i in range(0, 64, size):
        out |= pattern << i
    return out & arm64emu.M64


class Cpu(arm64emu.Cpu):
    """arm64emu.Cpu plus the forms gfx_drv_setviewport uses."""

    def step(self, w, pc):
        rd = w & 0x1F
        rn = (w >> 5) & 0x1F
        rm = (w >> 16) & 0x1F
        ra = (w >> 10) & 0x1F

        # arm64emu.Cpu.get(r, w) takes w=True to mean the 32-BIT view. That
        # reads backwards, and getting it backwards here silently truncated
        # every 64-bit source to 32 bits -- `lsr x11, x11, #41` returned 0
        # instead of 1920, which is exactly the class of mistake this whole
        # harness exists to catch. Two explicit helpers instead of one
        # ambiguous flag.
        def g32(r):
            return 0 if r == 31 else self.get(r, True)

        def g64(r):
            return 0 if r == 31 else self.get(r, False)

        gz = g32                      # every remaining plain use is 32-bit

        # ---- integer ------------------------------------------------------
        if (w & 0xFFE0FC00) == 0x1B007C00:                     # mul Wd,Wn,Wm
            self.set(rd, (g32(rn) * g32(rm)) & M32, True)
            return None
        if (w & 0xFFE0FC00) == 0x9BA07C00:                     # umull Xd,Wn,Wm
            self.set(rd, (g32(rn) * g32(rm)) & arm64emu.M64)
            return None
        if (w & 0xFFE0FC00) == 0x9B207C00:                     # smull Xd,Wn,Wm
            a, b = arm64emu.s32(g32(rn)), arm64emu.s32(g32(rm))
            self.set(rd, (a * b) & arm64emu.M64)
            return None
        if (w & 0xFFC00000) == 0xD3400000:                     # UBFM Xd,Xn(64)
            immr, imms = (w >> 16) & 0x3F, (w >> 10) & 0x3F
            src = g64(rn)
            if imms >= immr:
                val = (src >> immr) & ((1 << (imms - immr + 1)) - 1)
            else:
                val = (src & ((1 << (imms + 1)) - 1)) << (64 - immr)
            self.set(rd, val & arm64emu.M64)
            return None
        if (w & 0xFF800000) == 0x92000000:                     # AND Xd,Xn,#imm
            self.set(rd, g64(rn) & _logical_imm64(w))
            return None
        if (w & 0xFFC00000) == 0xB3400000:                     # BFM Xd,Xn (bfi/bfxil)
            immr, imms = (w >> 16) & 0x3F, (w >> 10) & 0x3F
            src, dst = g64(rn), g64(rd)
            if imms >= immr:                                   # bfxil
                width = imms - immr + 1
                val = (src >> immr) & ((1 << width) - 1)
                mask = (1 << width) - 1
                self.set(rd, ((dst & ~mask) | val) & arm64emu.M64)
            else:                                              # bfi
                width = imms + 1
                lsb = 64 - immr
                val = (src & ((1 << width) - 1)) << lsb
                mask = ((1 << width) - 1) << lsb
                self.set(rd, ((dst & ~mask) | val) & arm64emu.M64)
            return None

        # ---- float --------------------------------------------------------
        if (w & 0xFFFFFC00) == 0x1E230000:                     # ucvtf Sd,Wn
            self.fp[rd] = f2b(float(g32(rn) & M32))
            return None
        if (w & 0xFFFFFC00) == 0x1E220000:                     # scvtf Sd,Wn
            self.fp[rd] = f2b(float(arm64emu.s32(g32(rn))))
            return None
        if (w & 0xFFE0FC00) == 0x1E201800:                     # fdiv Sd,Sn,Sm
            d = b2f(self.fp[rm])
            self.fp[rd] = f2b(b2f(self.fp[rn]) / d) if d else f2b(float('inf'))
            return None
        if (w & 0xFFE0FC00) == 0x1E200800:                     # fmul Sd,Sn,Sm
            self.fp[rd] = f2b(b2f(self.fp[rn]) * b2f(self.fp[rm]))
            return None
        if (w & 0xFFE0FC00) == 0x1E202800:                     # fadd Sd,Sn,Sm
            self.fp[rd] = f2b(b2f(self.fp[rn]) + b2f(self.fp[rm]))
            return None
        if (w & 0xFF201FE0) == 0x1E201000:                     # fmov Sd,#imm8
            imm8 = (w >> 13) & 0xFF
            sign = -1.0 if imm8 & 0x80 else 1.0
            e3 = (imm8 >> 4) & 0x7
            frac = imm8 & 0xF
            # ARM VFPExpandImm. The 3-bit field is NOT a plain biased
            # exponent: it wraps, so 7 -> 2^0 and 0 -> 2^1, giving the
            # documented 2^-3 .. 2^4 range. Reading it as (e3 - 3) turns
            # `fmov s6, #0.5` (imm8 0x60) into 8.0 and quietly corrupts
            # every offset computed from it. Checked against the four
            # encodings this function actually contains: 0x70 = 1.0,
            # 0x78 = 1.5, 0x60 = 0.5, 0xE0 = -0.5.
            exp = (e3 - 7) if e3 >= 4 else (e3 + 1)
            self.fp[rd] = f2b(sign * (2.0 ** exp) * (1.0 + frac / 16.0))
            return None
        if (w & 0xFFE08000) == 0x1F000000:                     # fmadd Sd,Sn,Sm,Sa
            self.fp[rd] = f2b(b2f(self.fp[ra])
                              + b2f(self.fp[rn]) * b2f(self.fp[rm]))
            return None
        if (w & 0xFFE08000) == 0x1F208000:                     # fnmsub Sd,Sn,Sm,Sa
            self.fp[rd] = f2b(b2f(self.fp[rn]) * b2f(self.fp[rm])
                              - b2f(self.fp[ra]))
            return None
        if (w & 0xFFFFFC00) == 0x1E380000:                     # fcvtzs Wd,Sn
            x = b2f(self.fp[rn])
            v = int(x) if x >= 0 else -int(-x)
            self.set(rd, max(-0x80000000, min(0x7FFFFFFF, v)) & M32, True)
            return None
        if (w & 0xFFC00000) == 0xBD000000:                     # str St,[Xn,#i]
            self.mem.setu(self._addr(rn, ((w >> 10) & 0xFFF) * 4),
                          self.fp[rd], 4)
            return None
        if (w & 0xFFC00000) == 0x2D000000:                     # stp St1,St2,[Xn,#i]
            imm7 = (w >> 15) & 0x7F
            if imm7 & 0x40:
                imm7 -= 128
            a = self._addr(rn, imm7 * 4)
            self.mem.setu(a, self.fp[rd], 4)
            self.mem.setu(a + 4, self.fp[ra], 4)
            return None
        if (w & 0xFFE00C00) in (0xF8000000, 0xB8000000):        # stur Xt/Wt,[Xn,#s9]
            imm9 = (w >> 12) & 0x1FF
            if imm9 & 0x100:
                imm9 -= 512
            wide = (w & 0x40000000) != 0
            self.mem.setu(self._addr(rn, imm9),
                          g64(rd) if wide else g32(rd),
                          8 if wide else 4)
            return None
        if (w & 0xFFC00000) == 0xA9000000:                      # stp Xt1,Xt2,[Xn,#i]
            imm7 = (w >> 15) & 0x7F
            if imm7 & 0x40:
                imm7 -= 128
            a = self._addr(rn, imm7 * 8)
            self.mem.setu(a, g64(rd), 8)
            self.mem.setu(a + 8, g64(ra), 8)
            return None
        if (w & 0xFFC00000) == 0x29000000:                      # stp Wt1,Wt2,[Xn,#i]
            imm7 = (w >> 15) & 0x7F
            if imm7 & 0x40:
                imm7 -= 128
            a = self._addr(rn, imm7 * 4)
            self.mem.setu(a, g32(rd), 4)
            self.mem.setu(a + 4, g32(ra), 4)
            return None
        if w == 0xD65F03C0:                                    # ret
            return 0xDEAD0000
        return super().step(w, pc)


# ------------------------------------------------------------------ harness

# Globals the function dereferences, and the scratch addresses we point them
# at. Values are chosen so every output is traceable back to its input.
PTRS = {
    0x12CE578: 0x40000000,   # -> scaleX
    0x12CE580: 0x40000100,   # -> scaleY
    0x12CE3E8: 0x40000200,   # -> -> game object
    0x12CE548: 0x40001000,   # -> dst   (the device rect block)
    0x12CE668: 0x40002000,   # -> mtx   (the viewport matrix)
    0x12CE1F8: 0x40000300,   # -> mode
}
OBJ = 0x40003000


def run(x, y, w, h, scale_x, scale_y, game_w, game_h, mode, patches=None):
    img = nxmap.Main(os.path.join(HERE, 'exefs', 'main')).img
    lo, hi = SETVIEWPORT, SETVIEWPORT + 0x154
    words = list(struct.unpack('<%dI' % ((hi - lo) // 4), img[lo:hi]))
    for va, word in (patches or {}).items():
        words[(va - lo) // 4] = word

    mem = arm64emu.Mem()
    for slot, target in PTRS.items():
        mem.setu(slot, target, 8)
    mem.setu(PTRS[0x12CE578], scale_x, 4)
    mem.setu(PTRS[0x12CE580], scale_y, 4)
    mem.setu(PTRS[0x12CE3E8], OBJ, 8)
    mem.setu(PTRS[0x12CE1F8], mode, 4)
    mem.setu(OBJ + 0x954, game_w, 4)
    mem.setu(OBJ + 0x958, game_h, 4)

    cpu = Cpu(mem)
    cpu.set(0, x & M32, True); cpu.set(1, y & M32, True)
    cpu.set(2, w & M32, True); cpu.set(3, h & M32, True)
    cpu.sp = 0x50000000
    cpu.run(lo, words, max_steps=4000)

    dst, mtx = 0x40001000, 0x40002000
    # dst+0x800 / +0x808 are a rect as (x1,y1) and (x2,y2) -- the second pair
    # is the FAR CORNER, not a size: the code adds the scaled origin back in
    # at +0x10D67C4. Naming them vw/vh would invite exactly the misreading
    # this harness exists to prevent.
    return {
        'x1': mem.u(dst + 0x800, 4), 'y1': mem.u(dst + 0x804, 4),
        'x2': mem.u(dst + 0x808, 4), 'y2': mem.u(dst + 0x80C, 4),
        '_11': round(b2f(mem.u(mtx + 0xA8, 4)), 6),
        '_22': round(b2f(mem.u(mtx + 0xBC, 4)), 6),
        '_41': round(b2f(mem.u(mtx + 0xD8, 4)), 6),
        '_42': round(b2f(mem.u(mtx + 0xDC, 4)), 6),
    }


def _selftest():
    """The decode checks that caught two bugs in this file. Run them first."""
    ok = True

    def chk(cond, label):
        nonlocal ok
        print(('  ok    ' if cond else '  FAIL  ') + label)
        ok = ok and bool(cond)

    print('instruction decode')
    for word, want in ((0x1E2F1000, 1.5), (0x1E2E1006, 1.0),
                       (0x1E2C1006, 0.5), (0x1E3C1005, -0.5)):
        c = Cpu(arm64emu.Mem())
        c.step(word, 0)
        got = b2f(c.fp[word & 0x1F])
        chk(got == want, f'fmov #{want} decodes to {got}')
    c = Cpu(arm64emu.Mem())
    c.x[11] = 0xF00000003C000
    c.step(0xD369FD6B, 0)                       # lsr x11, x11, #41
    chk(c.x[11] == 1920, f'lsr x11,x11,#41 -> {c.x[11]} (64-bit, not 32)')

    print('\nthe stock engine reproduces a plain 4:3 viewport')
    r = run(x=0, y=0, w=640, h=480, scale_x=1440, scale_y=1080,
            game_w=640, game_h=480, mode=1)
    chk(r['x1'] == 0 and r['x2'] == 1440, f'rect spans the 4:3 target: {r}')
    chk(r['_11'] == 1.0 and r['_41'] == 0.0, 'identity scale and offset')

    print('\nbattle forces _22 = 1.0 (the carve-out FFNx also has)')
    a = run(x=0, y=0, w=640, h=240, scale_x=1440, scale_y=1080,
            game_w=640, game_h=480, mode=1)['_22']
    b = run(x=0, y=0, w=640, h=240, scale_x=1440, scale_y=1080,
            game_w=640, game_h=480, mode=3)['_22']
    chk(a == 0.5 and b == 1.0, f'mode 1 -> {a}, mode 3 (battle) -> {b}')
    return ok


if __name__ == '__main__':
    sys.exit(0 if _selftest() else 1)
