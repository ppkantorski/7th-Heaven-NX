"""Render a main-menu frame from the x86 code itself (unicorn): every glyph
quad the game's own text code builds, every box/bar/avatar call, at the
positions the code computes. Used for the BUILD 621 preview.

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
import struct, collections, hashlib
from unicorn import *
from unicorn.x86_const import *
import diffemu as D

PAGE = 0x1000
FN = 0x6CA346
GLYPH = 0x6F564E            # (x, y, colour, char, scale) -> new x
ALLOC = 0x66E272            # (count, gobj) -> vertex slot
STUBS = {0x6E7BA1: 'box', 0x6E7D20: 'boxend', 0x6E6C5B: 'ava', 0x6F7BFB: 'bar',
         0x6F7346: 'barbg', 0x6EB3B8: 'cur', 0x6CDBF2: 'ok'}
FONT_A, FONT_B = 0x0A000000, 0x0A100000
FONT_PTR_A, FONT_PTR_B = 0xDC100C, 0xDC1010
WIN_B, WIN_PTR_B = 0x0A200000, 0xDC0FCC
WIN_D, WIN_PTR_D = 0x0A300000, 0xDC0FD4
COLOUR_ARG = {0x6F564E: 2, 0x6F5C0C: 3, 0x6F9739: 4, 0x6F9C44: 4}
VBUF = 0x0B000000


def run(exebuf, widths, pokes, seed=0, names=None, max_ins=30_000_000):
    img, pe = D.exe_image(exebuf, seed)
    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    mapped = set()

    def mp(va):
        p = va & ~(PAGE - 1)
        if p in mapped:
            return
        uc.mem_map(p, PAGE)
        uc.mem_write(p, D.page_bytes(seed, p, img))
        mapped.add(p)
    lo = min(img); hi = max(img) + PAGE
    uc.mem_map(lo, hi - lo)
    blob = bytearray(hi - lo)
    for p, b in img.items():
        blob[p - lo:p - lo + PAGE] = b
    for a, v in pokes.items():
        if lo <= a < hi:
            blob[a - lo:a - lo + 4] = struct.pack('<I', v & 0xffffffff)
    blob[0x99DDA8 - lo:0x99DDA8 - lo + 256] = widths
    struct.pack_into('<I', blob, 0xDB958C - lo, 0x99DDA8)
    struct.pack_into('<I', blob, FONT_PTR_A - lo, FONT_A)
    struct.pack_into('<I', blob, FONT_PTR_B - lo, FONT_B)
    struct.pack_into('<I', blob, WIN_PTR_B - lo, WIN_B)
    struct.pack_into('<I', blob, WIN_PTR_D - lo, WIN_D)
    for a, s in (names or {}).items():
        blob[a - lo:a - lo + len(s)] = s
    uc.mem_write(lo, bytes(blob))
    mapped.update(range(lo, hi, PAGE))
    for base in (FONT_A, FONT_B, WIN_B, WIN_D):
        uc.mem_map(base, 0x10000)
    uc.mem_map(VBUF, 0x400000)
    STACK = 0x0FF00000
    uc.mem_map(STACK - 0x20000, 0x21000)
    SENT = 0x0FFF0000
    uc.mem_map(SENT, PAGE)
    esp = STACK - 0x100
    uc.mem_write(esp, struct.pack('<I', SENT))
    uc.reg_write(UC_X86_REG_ESP, esp)
    uc.reg_write(UC_X86_REG_EBP, 0x0FE00000)
    ev = []
    st = {'v': VBUF, 'glyph': None}

    def on_unmapped(uc_, access, addr, size, value, ud):
        for a in range(addr & ~(PAGE - 1), addr + size, PAGE):
            mp(a)
        return True

    def args(n):
        sp = uc.reg_read(UC_X86_REG_ESP)
        return list(struct.unpack('<%di' % n, uc.mem_read(sp + 4, 4 * n)))

    def ret(val):
        sp = uc.reg_read(UC_X86_REG_ESP)
        r = struct.unpack('<I', uc.mem_read(sp, 4))[0]
        uc.reg_write(UC_X86_REG_EAX, val & 0xffffffff)
        uc.reg_write(UC_X86_REG_ESP, sp + 4)
        uc.reg_write(UC_X86_REG_EIP, r)

    def on_stub(uc_, addr, size, ud):
        k = STUBS[addr]
        a = args(6)
        if k == 'ok':
            ret(1); return
        ev.append((k, a))
        ret(0)

    def on_glyph(uc_, addr, size, ud):
        a = args(6)
        st['glyph'] = [a[0], a[1], a[COLOUR_ARG[addr]]]
        st.setdefault('stack', []).append(addr)

    def on_alloc(uc_, addr, size, ud):
        n, gobj = args(2)
        buf = st['v']; st['v'] += 0x100
        uc.mem_write((gobj & 0xffffffff) + 0x70, struct.pack('<I', buf))
        ev.append(('quad', [gobj & 0xffffffff, buf] + list(st['glyph'] or [])))
        ret(1)

    def on_sent(uc_, addr, size, ud):
        uc_.emu_stop()
    uc.hook_add(UC_HOOK_MEM_UNMAPPED, on_unmapped)
    for a in STUBS:
        uc.hook_add(UC_HOOK_CODE, on_stub, begin=a, end=a)
    for g in COLOUR_ARG:
        uc.hook_add(UC_HOOK_CODE, on_glyph, begin=g, end=g)
    uc.hook_add(UC_HOOK_CODE, on_alloc, begin=ALLOC, end=ALLOC)
    uc.hook_add(UC_HOOK_CODE, on_sent, begin=SENT, end=SENT)
    status = 'ok'
    try:
        uc.emu_start(FN, SENT, count=max_ins)
    except UcError as e:
        status = 'err %s at %x' % (e, uc.reg_read(UC_X86_REG_EIP))
    out = []
    for k, a in ev:
        if k == 'quad':
            gobj, buf = a[0], a[1]
            vs = []
            for i in range(4):
                x, y, z, w, c, s, u, v = struct.unpack('<4fII2f', uc.mem_read(buf + 0x20 * i, 0x20))
                vs.append((x, y, u, v))
            out.append(('quad', 'a' if gobj == FONT_A else 'b' if gobj == FONT_B else 'winb' if gobj == WIN_B else 'wind' if gobj == WIN_D else hex(gobj), vs, a[2:]))
        else:
            out.append((k, a))
    return out, status
