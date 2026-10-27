#!/usr/bin/env python3
"""diffemu.py -- differential check of in-place ARM word edits.

For one x86 function F:
  x86 side : run stock F and ESUI-patched F (unicorn, x86-32)
  ARM side : run the port's stock body and the word-edited body (unicorn)
Each run records every call F makes (callee, 8 stack args) and every guest
memory write. Callees are stubbed: they return a value derived from
(callee, how many times it was called) and pop nothing (cdecl) / their
return slot (port ABI). Guest memory starts identical in all four runs.

The check: the CHANGES ESUI makes on x86 (stock -> patched) must equal the
changes the word edits make on ARM (port -> edited), call by call, arg by
arg, and in memory writes.

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
import sys, struct, random, hashlib, collections
sys.path.insert(0, '.')
from unicorn import *
from unicorn.x86_const import *
from unicorn.arm64_const import *
import nxmap, exe_patch

import os
M = nxmap.Main(os.environ.get('ESUI_BASE_MAIN','dump/exefs/main'))
X2A = M.x86_to_arm
A2X = {v: k for k, v in X2A.items()}
PAGE = 0x1000
TRANSLATE = 0x10FC3A0
YIELD = 0xA550
DISPATCH = 0xA1A0
WATCH = set()
LAST_COV = set()
LAST_ACOV = set()
GBASE = 0x2_0000_0000         # host address of guest address 0
CTX = 0x1_8000_0000
X87 = 0x1_8001_0000
HSTACK = 0x1_9000_0000
EXE_LO, EXE_HI = 0x400000, 0x2000000


def page_bytes(seed, va, img):
    va &= ~(PAGE - 1)
    if EXE_LO <= va < EXE_HI:
        return img.get(va, b'\0' * PAGE)
    import numpy as np
    h = int.from_bytes(hashlib.sha256(b'%d:%x' % (seed, va)).digest()[:8], 'little')
    g = np.random.default_rng(h)
    kind = g.integers(0, 7, PAGE // 4)
    small = g.integers(0, 256, PAGE // 4); mid = g.integers(0, 0x10000, PAGE // 4)
    v = np.where(kind < 3, 0, np.where(kind == 3, 1, np.where(kind == 4, 2, np.where(kind == 5, small, mid))))
    return v.astype('<u4').tobytes()


def exe_image(buf, seed=0):
    pe = exe_patch.parse_pe(buf)
    img = {}
    for name, va, vs, ro, rs in pe['sections']:
        data = buf[ro:ro + min(vs, rs)]
        if seed % 2 and vs > len(data):
            data = data + b'\0' * (-len(data) % 4)
            import numpy as np
            g = np.random.default_rng(seed * 7919 + va)
            n = (vs - len(data) + 3) // 4
            kind = g.integers(0, 5, n)
            tail = np.where(kind < 2, 0, np.where(kind == 2, 1, np.where(kind == 3, 2, g.integers(0, 256, n)))).astype('<u4')
            data = (data + tail.tobytes())[:vs]
        data = bytearray(data.ljust(vs, b'\0'))
        for pa, pv in POKES.items():
            if va <= pa < va + vs - 3:
                struct.pack_into('<I', data, pa - va, pv & 0xffffffff)
        data = bytes(data)
        for p in range(va & ~(PAGE - 1), va + vs, PAGE):
            pg = bytearray(img.get(p, b'\0' * PAGE))
            lo = max(p, va); hi = min(p + PAGE, va + vs)
            pg[lo - p:hi - p] = data[lo - va:hi - va]
            img[p] = bytes(pg)
    return img, pe


RETMODE = [0]
# callees that take a struct by pointer: (args, struct bytes) -- the struct's
# halfwords are compared as extra args (BUILD 628: the HP/MP bars' rect)
STRUCT_ARGS = {0x6F638C: (2, 0x18), 0x6E7D20: (2, 8)}
def _with_struct(target, a, read):
    sa = STRUCT_ARGS.get(target)
    if not sa:
        return a
    n, size = sa
    try:
        raw = read(a[0], size)
    except Exception:
        return a
    return tuple(a[:n]) + struct.unpack('<%dH' % (size // 2), raw)
TRACK_STACK = [False]   # also compare writes into the guest stack window (frames, pushes)
LAST_SITES = []
LAST_WRITERS = []
LAST_MEMW = {}      # guest addr -> (pc, size, value) of the ARM store that wrote it last
MEMWATCH = set(); LAST_MEMLOG = []   # guest addrs -> [(pc, 'r'/'w', addr, size, value)]   # per ARM call: the pc of the instruction that wrote each arg slot


ARGDEP = [True]


def retval(target, n, a=()):
    # BUILD 623: the value also depends on the call's arguments, so a chain
    # (x = f(x, ...)) can never look like a constant shift of every call.
    key = b'%x:%d:%d' % (target, n, RETMODE[0])
    if ARGDEP[0]:
        key += b':' + b','.join(b'%x' % (v & 0xffffffff) for v in a[:2])
    h = hashlib.sha256(key).digest()[0]
    return h % 3 if RETMODE[0] % 4 in (0, 1) else h & 0x3f


# ------------------------------------------------------------------ x86
NATIVE = set()
NATIVE_ARM = set()
POKES = {}


def run_x86(buf, fn, seed, args, max_ins=2_000_000):
    nat = [n if isinstance(n, tuple) else M.containing(n) for n in NATIVE]
    img, pe = exe_image(buf, seed)
    fs, fe = M.containing(fn)
    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    mapped = set()

    def mp(va):
        p = va & ~(PAGE - 1)
        if p in mapped:
            return
        uc.mem_map(p, PAGE)
        uc.mem_write(p, page_bytes(seed, p, img))
        mapped.add(p)
    lo = min(img); hi = max(img) + PAGE
    uc.mem_map(lo, hi - lo)
    blob = bytearray(hi - lo)
    for p, b in img.items():
        blob[p - lo:p - lo + PAGE] = b
    uc.mem_write(lo, bytes(blob))
    mapped.update(range(lo, hi, PAGE))
    STACK = 0x0FF00000
    for p in range(STACK - 0x10000, STACK + 0x1000, PAGE):
        if p not in mapped:
            uc.mem_map(p, PAGE); mapped.add(p)
    SENT = 0x0FFF0000
    mp(SENT)
    esp = STACK - 0x100
    for k, a in enumerate(args):
        uc.mem_write(esp + 4 + 4 * k, struct.pack('<I', a & 0xffffffff))
    uc.mem_write(esp, struct.pack('<I', SENT))
    uc.reg_write(UC_X86_REG_ESP, esp)
    uc.reg_write(UC_X86_REG_EBP, 0x0FE00000)
    calls = []; writes = {}; cnt = collections.Counter()
    state = {'n': 0}

    def on_unmapped(uc_, access, addr, size, value, ud):
        for a in range(addr & ~(PAGE - 1), addr + size, PAGE):
            mp(a)
        return True

    def on_write(uc_, access, addr, size, value, ud):
        if STACK - 0x10000 <= addr < STACK + 0x1000:
            if not TRACK_STACK[0]:
                return
            pc = uc_.reg_read(UC_X86_REG_EIP)
            op = bytes(uc_.mem_read(pc, 2))
            if op[0] == 0xE8 or (op[0] == 0xFF and (op[1] >> 3) & 7 == 2):
                return                       # a call's return address: the port has none
        for k in range(size):
            writes[addr + k] = (value >> (8 * k)) & 0xff

    def on_code(uc_, addr, size, ud):
        state['n'] += 1
        if state['n'] > max_ins:
            uc_.emu_stop(); state['timeout'] = True; return
        if addr == SENT:
            uc_.emu_stop(); return
        if not (fs <= addr < fe):
            for s_, e_ in nat:
                if s_ <= addr < e_:
                    return
            sp = uc_.reg_read(UC_X86_REG_ESP)
            a = struct.unpack('<8I', uc_.mem_read(sp + 4, 32))
            ecx = uc_.reg_read(UC_X86_REG_ECX); edx = uc_.reg_read(UC_X86_REG_EDX)
            calls.append((addr, _with_struct(addr, a, lambda p, n: bytes(uc_.mem_read(p, n)))))
            cnt[addr] += 1
            ret = struct.unpack('<I', uc_.mem_read(sp, 4))[0]
            uc_.reg_write(UC_X86_REG_EAX, retval(addr, cnt[addr], a))
            uc_.reg_write(UC_X86_REG_ESP, sp + 4)
            uc_.reg_write(UC_X86_REG_EIP, ret)
    uc.hook_add(UC_HOOK_MEM_UNMAPPED, on_unmapped)
    uc.hook_add(UC_HOOK_MEM_WRITE, on_write)
    global LAST_COV
    LAST_COV = set()

    def on_watch(uc_, addr, size, ud):
        LAST_COV.add(addr)
    for w in WATCH:
        if fs <= w < fe:
            uc.hook_add(UC_HOOK_CODE, on_watch, begin=w, end=w)
    uc.hook_add(UC_HOOK_CODE, on_code, begin=0, end=fs - 1)
    uc.hook_add(UC_HOOK_CODE, on_code, begin=fe, end=0xFFFFFFFF)
    try:
        uc.emu_start(fs, SENT, count=max_ins)
    except UcError as e:
        return calls, writes, 'err %s at %x' % (e, uc.reg_read(UC_X86_REG_EIP))
    writes[-1] = uc.reg_read(UC_X86_REG_EAX)
    return calls, writes, 'timeout' if state.get('timeout') else 'ok'


# ------------------------------------------------------------------ ARM
_IMG = None


def arm_image():
    global _IMG
    if _IMG is None:
        img = bytearray(M.img)
        for o, add in M.rel.items():
            if o + 8 <= len(img):
                img[o:o + 8] = struct.pack('<Q', add)
        _IMG = img
    return _IMG


_MODS = {}


def module_image(path):
    if path not in _MODS:
        m = nxmap.Main(path)
        img = bytearray(m.img)
        for o, add in m.rel.items():
            if o + 8 <= len(img):
                img[o:o + 8] = struct.pack('<Q', add)
        _MODS[path] = img
    return _MODS[path]


def run_arm(text_edits, buf, fn, seed, args, max_ins=8_000_000, module=None):
    del LAST_SITES[:]; del LAST_WRITERS[:]; LAST_MEMW.clear()
    stackw = {}
    """text_edits: {arm_addr: word}"""
    img_exe, pe = exe_image(buf, seed)
    fs, fe = M.containing(fn)
    a0, a1 = M.extent(fs)
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    img = bytearray(module_image(module) if module else arm_image())
    for a, w in text_edits.items():
        img[a:a + 4] = struct.pack('<I', w)
    size = (len(img) + M.bss + 0xfffff) & ~0xfffff
    uc.mem_map(0, size)
    uc.mem_write(0, bytes(img))
    # context and x87 pointers
    struct.pack_into('<Q', img, 0x12CE2B0, CTX)
    uc.mem_write(0x12CE2B0, struct.pack('<Q', CTX))
    uc.mem_write(0x12CE2A8, struct.pack('<Q', X87))
    uc.mem_map(CTX, 0x20000)
    uc.mem_map(HSTACK - 0x100000, 0x101000)
    gmapped = set()

    def gmp(gva):
        p = gva & ~(PAGE - 1)
        if p in gmapped:
            return
        uc.mem_map(GBASE + p, PAGE)
        uc.mem_write(GBASE + p, page_bytes(seed, p, img_exe))
        gmapped.add(p)
    lo = min(img_exe); hi = max(img_exe) + PAGE
    uc.mem_map(GBASE + lo, hi - lo)
    blob = bytearray(hi - lo)
    for p, b in img_exe.items():
        blob[p - lo:p - lo + PAGE] = b
    uc.mem_write(GBASE + lo, bytes(blob))
    gmapped.update(range(lo, hi, PAGE))
    STACK = 0x0FF00000
    for p in range(STACK - 0x10000, STACK + 0x1000, PAGE):
        if p not in gmapped:
            uc.mem_map(GBASE + p, PAGE); gmapped.add(p)
    esp = STACK - 0x100
    for k, a in enumerate(args):
        uc.mem_write(GBASE + esp + 4 + 4 * k, struct.pack('<I', a & 0xffffffff))
    # port ABI: caller did esp -= 4 (return slot) then bl
    uc.mem_write(CTX + 0x10, struct.pack('<I', esp))
    uc.mem_write(CTX + 0x14, struct.pack('<I', 0x0FE00000))
    SENT = 0x1_7000_0000
    uc.mem_map(SENT, PAGE)
    uc.mem_write(SENT, b'\x00\x00\x20\xd4' * 4)   # brk
    uc.reg_write(UC_ARM64_REG_SP, HSTACK - 0x100)
    uc.reg_write(UC_ARM64_REG_X30, SENT)
    calls = []; writes = {}; cnt = collections.Counter(); state = {'n': 0}

    def on_unmapped(uc_, access, addr, size, value, ud):
        if GBASE <= addr < GBASE + (1 << 32):
            for a in range(addr & ~(PAGE - 1), addr + size, PAGE):
                gmp(a - GBASE)
            return True
        try:
            p = addr & ~0xffff
            uc_.mem_map(p, 0x10000)
            return True
        except UcError:
            return False

    def on_write(uc_, access, addr, size, value, ud):
        if GBASE <= addr < GBASE + (1 << 32):
            g = addr - GBASE
            if STACK - 0x10000 <= g < STACK + 0x1000:
                if size == 4:
                    stackw[g] = uc_.reg_read(UC_ARM64_REG_PC)
                if TRACK_STACK[0]:
                    for k in range(size):
                        writes[g + k] = (value >> (8 * k)) & 0xff
                return
            pc = uc_.reg_read(UC_ARM64_REG_PC)
            for k in range(size):
                LAST_MEMW.pop(g + k, None)
            LAST_MEMW[g] = (pc, size, value & ((1 << (8 * size)) - 1))
            for k in range(size):
                writes[g + k] = (value >> (8 * k)) & 0xff

    def ctx32(off):
        return struct.unpack('<I', uc.mem_read(CTX + off, 4))[0]

    def stub_call(uc_, target):
        esp_ = ctx32(0x10)
        a = struct.unpack('<8I', uc_.mem_read(GBASE + esp_ + 4, 32))
        calls.append((target, _with_struct(target, a, lambda p, n: bytes(uc_.mem_read(GBASE + p, n)))))
        LAST_SITES.append(uc_.reg_read(UC_ARM64_REG_X30) - 4)
        LAST_WRITERS.append([stackw.get(esp_ + 4 + 4 * j) for j in range(8)])
        cnt[target] += 1
        uc_.mem_write(CTX, struct.pack('<I', retval(target, cnt[target], a)))
        uc_.mem_write(CTX + 0x10, struct.pack('<I', esp_ + 4))
        uc_.reg_write(UC_ARM64_REG_PC, uc_.reg_read(UC_ARM64_REG_X30))

    def on_code(uc_, addr, size, ud):
        state['n'] += 1
        if state['n'] > max_ins:
            uc_.emu_stop(); state['timeout'] = True; return
        if addr == SENT:
            uc_.emu_stop(); return
        if addr == TRANSLATE:
            w0 = uc_.reg_read(UC_ARM64_REG_X0) & 0xffffffff
            uc_.reg_write(UC_ARM64_REG_X0, GBASE + w0)
            for r in (UC_ARM64_REG_X8, UC_ARM64_REG_X9, UC_ARM64_REG_X10):
                uc_.reg_write(r, 0xDEADBEEFCAFEF00D)
            uc_.reg_write(UC_ARM64_REG_PC, uc_.reg_read(UC_ARM64_REG_X30))
            return
        if addr == YIELD:
            uc_.reg_write(UC_ARM64_REG_PC, uc_.reg_read(UC_ARM64_REG_X30)); return
        if addr == DISPATCH:
            t = uc_.reg_read(UC_ARM64_REG_X0) & 0xffffffff
            stub_call(uc_, t); return
        if addr in A2X and addr != a0 and A2X[addr] not in NATIVE_ARM:
            stub_call(uc_, A2X[addr]); return
    uc.hook_add(UC_HOOK_MEM_UNMAPPED, on_unmapped)
    uc.hook_add(UC_HOOK_MEM_WRITE, on_write)
    del LAST_MEMLOG[:]
    if MEMWATCH:
        def on_mw(uc_, access, addr, size, value, ud):
            g = addr - GBASE
            if any(g <= m < g + size for m in MEMWATCH):
                v = value if access == UC_MEM_WRITE else int.from_bytes(uc_.mem_read(addr, size), 'little')
                LAST_MEMLOG.append((uc_.reg_read(UC_ARM64_REG_PC), 'w' if access == UC_MEM_WRITE else 'r', g, size, v))
        uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, on_mw)
    global LAST_ACOV
    LAST_ACOV = set()

    def on_awatch(uc_, addr, size, ud):
        LAST_ACOV.add(addr)
    for w in text_edits:
        uc.hook_add(UC_HOOK_CODE, on_awatch, begin=w, end=w)
    uc.hook_add(UC_HOOK_CODE, on_code, begin=0, end=a0 - 1)
    uc.hook_add(UC_HOOK_CODE, on_code, begin=a1, end=0x1200000)
    uc.hook_add(UC_HOOK_CODE, on_code, begin=SENT, end=SENT + 4)
    try:
        uc.emu_start(a0, SENT, count=max_ins)
    except UcError as e:
        return calls, writes, 'err %s at %x' % (e, uc.reg_read(UC_ARM64_REG_PC))
    writes[-1] = ctx32(0)
    return calls, writes, 'timeout' if state.get('timeout') else 'ok'


def diff(c0, w0, c1, w1):
    """changes stock->patched: list of ('call', k, target, argidx, old, new) and
    ('mem', addr, old, new). Calls compared index by index (same count needed)."""
    out = []
    if [t for t, _ in c0] != [t for t, _ in c1]:
        out.append(('callseq', len(c0), len(c1)))
    for k, ((t0, a0), (t1, a1)) in enumerate(zip(c0, c1)):
        for j in range(8):
            if a0[j] != a1[j]:
                out.append(('call', k, t0, j, a0[j], a1[j]))
    for a in sorted(set(w0) | set(w1)):
        if w0.get(a) != w1.get(a):
            out.append(('mem', a, w0.get(a), w1.get(a)))
    return out
