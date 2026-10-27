"""ARM twin of tools/esui/render_x86.run: runs the PORT's ARM64 body of the
main menu (x86 0x6CA346) from a given exefs/main, with every recompiled
callee running for real except the same stubs render_x86 uses, and records
every glyph quad the ARM text code writes.

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
import struct, collections
from unicorn import *
from unicorn.arm64_const import *
import diffemu as D, nxmap

PAGE = 0x1000
FN = 0x6CA346
STUBS = {0x6E7BA1: 'box', 0x6E7D20: 'boxend', 0x6E6C5B: 'ava', 0x6F7BFB: 'bar',
         0x6F7346: 'barbg', 0x6EB3B8: 'cur', 0x6CDBF2: 'ok'}
ALLOC = 0x66E272
COLOUR_ARG = {0x6F564E: 2, 0x6F5C0C: 3, 0x6F9739: 4, 0x6F9C44: 4}
FONT_A, FONT_B = 0x0A000000, 0x0A100000
FONT_PTR_A, FONT_PTR_B = 0xDC100C, 0xDC1010
WIN_B, WIN_PTR_B = 0x0A200000, 0xDC0FCC
WIN_D, WIN_PTR_D = 0x0A300000, 0xDC0FD4
VBUF = 0x0B000000
EXTRA = []
NQ = [False]
MODULE = ['dump/exefs/main']
_M = {}


def mod(path):
    if path not in _M:
        m = nxmap.Main(path)
        img = bytearray(m.img)
        for o, add in m.rel.items():
            if o + 8 <= len(img):
                img[o:o + 8] = struct.pack('<Q', add)
        _M[path] = (m, img)
    return _M[path]


def run(exebuf, widths, pokes, seed=0, names=None, max_ins=60_000_000):
    m, mimg = mod(MODULE[0])
    X2A = m.x86_to_arm
    A2X = {v: k for k, v in X2A.items()}
    stub_arm = {X2A[x]: k for x, k in STUBS.items()}
    alloc_arm = X2A[ALLOC]
    glyph_arm = {X2A[x]: c for x, c in COLOUR_ARG.items()}
    D.POKES = {}
    img_exe, pe = D.exe_image(exebuf, seed)
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    img = bytearray(mimg)
    size = (len(img) + m.bss + 0xfffff) & ~0xfffff
    uc.mem_map(0, size)
    uc.mem_write(0, bytes(img))
    uc.mem_write(0x12CE2B0, struct.pack('<Q', D.CTX))
    uc.mem_write(0x12CE2A8, struct.pack('<Q', D.X87))
    uc.mem_map(D.CTX, 0x20000)
    uc.mem_map(D.HSTACK - 0x100000, 0x101000)
    G = D.GBASE
    lo = min(img_exe); hi = max(img_exe) + PAGE
    blob = bytearray(hi - lo)
    for p, b in img_exe.items():
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
    uc.mem_map(G + lo, hi - lo)
    uc.mem_write(G + lo, bytes(blob))
    gm = set(range(lo, hi, PAGE))
    for base in (FONT_A, FONT_B, WIN_B, WIN_D) + tuple(EXTRA):
        uc.mem_map(G + base, 0x10000); gm.update(range(base, base + 0x10000, PAGE))
    uc.mem_map(G + VBUF, 0x400000); gm.update(range(VBUF, VBUF + 0x400000, PAGE))
    STACK = 0x0FF00000
    uc.mem_map(G + STACK - 0x20000, 0x21000); gm.update(range(STACK - 0x20000, STACK + 0x1000, PAGE))
    esp = STACK - 0x100
    uc.mem_write(D.CTX + 0x10, struct.pack('<I', esp))
    uc.mem_write(D.CTX + 0x14, struct.pack('<I', 0x0FE00000))
    SENT = 0x1_7000_0000
    uc.mem_map(SENT, PAGE)
    uc.mem_write(SENT, b'\x00\x00\x20\xd4' * 4)
    uc.reg_write(UC_ARM64_REG_SP, D.HSTACK - 0x100)
    uc.reg_write(UC_ARM64_REG_X30, SENT)
    ev = []; st = {'v': VBUF, 'glyph': None, 'n': 0, 'disp': collections.Counter()}

    def gmp(g):
        p = g & ~(PAGE - 1)
        if p in gm: return
        uc.mem_map(G + p, PAGE); uc.mem_write(G + p, D.page_bytes(seed, p, img_exe)); gm.add(p)

    def on_unmapped(uc_, access, addr, size, value, ud):
        if G <= addr < G + (1 << 32):
            for a in range(addr & ~(PAGE - 1), addr + size, PAGE):
                gmp(a - G)
            return True
        try:
            uc_.mem_map(addr & ~0xffff, 0x10000); return True
        except UcError:
            return False

    def ctx32(off):
        return struct.unpack('<I', uc.mem_read(D.CTX + off, 4))[0]

    def args(n):
        e = ctx32(0x10)
        return list(struct.unpack('<%di' % n, uc.mem_read(G + e + 4, 4 * n)))

    def ret(val):
        e = ctx32(0x10)
        uc.mem_write(D.CTX, struct.pack('<I', val & 0xffffffff))
        uc.mem_write(D.CTX + 0x10, struct.pack('<I', e + 4))
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))

    def on_code(uc_, addr, size, ud):
        st['n'] += 1
        if addr == SENT:
            uc_.emu_stop(); return
        if addr == D.TRANSLATE:
            w0 = uc_.reg_read(UC_ARM64_REG_X0) & 0xffffffff
            uc_.reg_write(UC_ARM64_REG_X0, G + w0)
            uc_.reg_write(UC_ARM64_REG_PC, uc_.reg_read(UC_ARM64_REG_X30)); return
        if addr == D.YIELD:
            uc_.reg_write(UC_ARM64_REG_PC, uc_.reg_read(UC_ARM64_REG_X30)); return
        if addr == D.DISPATCH:
            t = uc_.reg_read(UC_ARM64_REG_X0) & 0xffffffff
            st['disp'][t] += 1
            ret(0); return
        if addr in stub_arm:
            k = stub_arm[addr]; a = args(6)
            if k == 'ok':
                ret(1); return
            ev.append((k, a)); ret(0); return
        if addr == alloc_arm:
            n, gobj = args(2)
            buf = st['v']; st['v'] += 0x2000
            uc.mem_write(G + (gobj & 0xffffffff) + 0x70, struct.pack('<I', buf))
            ev.append(('quad', [gobj & 0xffffffff, buf] + list(st['glyph'] or []) + [n]))
            ret(1); return
        if addr in glyph_arm:
            a = args(6)
            st['glyph'] = [a[0], a[1], a[glyph_arm[addr]]]
    uc.hook_add(UC_HOOK_MEM_UNMAPPED, on_unmapped)
    hot = set(stub_arm) | {alloc_arm, D.TRANSLATE, D.YIELD, D.DISPATCH, SENT} | set(glyph_arm)
    for a in hot:
        uc.hook_add(UC_HOOK_CODE, on_code, begin=a, end=a)
    status = 'ok'
    try:
        uc.emu_start(X2A[FN], SENT, count=max_ins)
    except UcError as e:
        status = 'err %s at %x' % (e, uc.reg_read(UC_ARM64_REG_PC))
    out = []
    for k, a in ev:
        if k == 'quad':
            gobj, buf = a[0], a[1]
            vs = []
            for i in range(4 * max(1, min(64, a[-1] if NQ[0] else 1))):
                x, y, z, w, c, s, u, v = struct.unpack('<4fII2f', uc.mem_read(G + buf + 0x20 * i, 0x20))
                vs.append((x, y, u, v))
            out.append(('quad', 'a' if gobj == FONT_A else 'b' if gobj == FONT_B else 'winb' if gobj == WIN_B else 'wind' if gobj == WIN_D else hex(gobj), vs, a[2:]))
        else:
            out.append((k, a))
    run.disp = st['disp']
    return out, status
