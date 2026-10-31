"""tests/test_ps3tweaks.py -- PS3Tweaks cave options ported to ARM64 (BUILD 868).

Every variant of every ported option must translate, and each ARM64 cave must
do exactly what the mod's own x86 cave does: both are run (unicorn) on the
same guest memory, from many random states, and the results compared.

Needs the dump (ff7_en + exefs/main), capstone and unicorn; skipped without.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install()
import os
import random
import struct

import pytest

import a64 as A
import build
import exe_patch
import ff7nx_hextarm as H
import ff7nx_ps3tweaks as P

try:
    import unicorn as U
    from unicorn import arm64_const as UA, x86_const as UX
except ImportError:                                          # pragma: no cover
    U = None

DUMP = build.find_game_dump(nxpaths.ROOT)
pytestmark = pytest.mark.skipif(
    DUMP is None or not DUMP.exe or not DUMP.nso or not H.available()
    or U is None, reason='needs the game dump, capstone and unicorn')

_TOOLS = []
_PLACES = []               # P.symbols, proven once


def _tools():
    if not _TOOLS:
        import nxmap
        _TOOLS.append((open(DUMP.exe, 'rb').read(), nxmap.Main(DUMP.nso)))
    return _TOOLS[0]


def _hext(**over):
    return '\n'.join('%08X = %s' % (va, ' '.join('%02X' % b for b in blob))
                     for va, blob in over.items())


def _port_text(port, data_bytes):
    lines = ['%08X = %s' % (va, b.hex(' ').upper())
             for va, b in port.code.items()]
    for va, blob in data_bytes.items():
        lines.append('%08X = %s' % (va, blob.hex(' ').upper()))
    return '\n'.join(lines)


LEVEL_50 = bytes.fromhex('EC000A4F010FD20114290219E7031EAD04234E0628FF7F32')
SPEED = ('3D 88 00 00 00 7E 15 0F 1F 40 00 50 31 C0 66 89 C8 99 B9 %s F7 E1'
         ' 8B C8 58 66 89 88 12 8B 9A 00 E9 78 7B C7 FF')

# ------------------------------------------------------------------ ARM side
GUEST = 0x10_0000_0000      # host address of guest 0
CTX = 0x20_0000_0000
STUB = 0x30_0000_0000
SENT_ORIG, SENT_FN = STUB + 0x40, STUB + 0x200   # SENT_FN: 128 trampolines
SENT_END = STUB + 0x400


class Arm(object):
    """exefs/main with `words` applied, guest memory at GUEST, the x86
    context at CTX. pt_g2h is pointed at `add x0, x0, GUEST` (clobbering x8-x10
    and the flags, as the real one does); the trampolines at sentinels."""

    def __init__(self, words, guest):
        exe, m = _tools()
        img = bytearray(m.img)
        for off, _s, new, _n in words:
            struct.pack_into('<I', img, off, new)
        # every blob's symbols where the build places them (a port writes
        # only the blobs it uses; the rest are never reached)
        if not _PLACES:
            _PLACES.extend(P.symbols(m, exe))
        places = _PLACES
        self.at = {n: a for _b, n, a in places if n != 'pt_g2h'}
        mu = U.Uc(U.UC_ARCH_ARM64, U.UC_MODE_ARM)
        size = (len(img) + 0xFFFF) & ~0xFFFF
        mu.mem_map(0, size)
        mu.mem_write(0, bytes(img))
        mu.mem_map(GUEST, 0x100_0000)
        mu.mem_write(GUEST, bytes(guest))
        mu.mem_map(CTX, 0x1000)
        mu.mem_map(STUB, 0x2000)
        # x8 = GUEST; x0 = x8 + w0 (zero-extended); ret
        g2h = [0xD28175A9,                                  # movz x9, #0xBAD
               0xD28175AA,                                  # movz x10, #0xBAD
               0xF100013F,                                  # cmp x9, #0
               0xD2C00208,                                  # movz x8, #0x10, lsl #32
               0x8B204100,                                  # add x0, x8, w0, uxtw
               0xD65F03C0]
        mu.mem_write(STUB, b''.join(struct.pack('<I', w) for w in g2h))
        for at in list(range(SENT_END, SENT_END + 0xC00, 4)) + [
                SENT_ORIG] + list(range(SENT_FN, SENT_FN + 0x200, 4)):
            mu.mem_write(at, struct.pack('<I', 0xD65F03C0))
        # the stubs are out of `b` range: the trampolines are hooked by
        # address instead
        self.mu = mu
        self.tramp = {a: STUB for _b, n, a in places if n == 'pt_g2h'}
        self.tramp[P.GUEST_TO_HOST] = STUB
        self.tramp[self.at['ap_orig']] = SENT_ORIG
        # game functions a cave calls: logged as (x86, first argument), and
        # `fake[x86](arm)` runs in their place and gives eax
        self.fake, self.fake_at = {}, {}
        for i, (name, x86) in enumerate(sorted(P.tramp_all().items())):
            if name != 'ap_orig':
                # a region blob's trampoline name is in each blob using it
                for _b, n, a in places:
                    if n == name:
                        self.tramp[a] = SENT_FN + 4 * i
                self.fake_at[SENT_FN + 4 * i] = x86
        self.calls, self.stops, self.stopped = [], set(), None

        def code(uc, address, size, _u):
            if address in self.tramp:
                uc.reg_write(UA.UC_ARM64_REG_PC, self.tramp[address])
            elif address in self.fake_at:
                x86 = self.fake_at[address]
                sp = self.ctx(4)
                arg = struct.unpack('<I', uc.mem_read(GUEST + sp + 4, 4))[0]
                self.calls.append((x86, arg))
                self.ctx(0, self.fake.get(x86, lambda _a: 0)(self))
                self.ctx(4, sp + 4)
            elif address == SENT_ORIG or SENT_END <= address < \
                    SENT_END + 0xC00 or address in self.stops:
                self.stopped = address
                uc.emu_stop()
        mu.hook_add(U.UC_HOOK_CODE, code)
        mu.mem_map(0x40_0000_0000, 0x10000)      # host stack
        mu.reg_write(UA.UC_ARM64_REG_SP, 0x40_0000_0000 + 0x8000)

    def ctx(self, i, v=None):
        if v is None:
            return struct.unpack('<I', self.mu.mem_read(CTX + 4 * i, 4))[0]
        self.mu.mem_write(CTX + 4 * i, struct.pack('<I', v & 0xFFFFFFFF))

    def call(self, entry, regs):
        mu = self.mu
        for r, v in regs.items():
            mu.reg_write(getattr(UA, 'UC_ARM64_REG_' + r.upper()), v)
        mu.reg_write(UA.UC_ARM64_REG_X30, SENT_END)
        mu.emu_start(self.at[entry], SENT_END + 4,
                     count=2000000)

    def run(self, start, stops, regs, count=10000):
        """Run the patched game code itself from `start` to one of
        `stops`; returns the one reached."""
        self.stops = set(stops)
        self.stopped = None
        for r, v in regs.items():
            self.mu.reg_write(getattr(UA, 'UC_ARM64_REG_' + r.upper()), v)
        self.mu.emu_start(start, 0, count=count)
        return self.stopped

    def reg(self, r):
        return self.mu.reg_read(getattr(UA, 'UC_ARM64_REG_' + r.upper()))

    def guest(self, va, n):
        return bytes(self.mu.mem_read(GUEST + va, n))


# ------------------------------------------------------------------ x86 side
class X86(object):
    """The mod's own x86 code run on the same guest memory (0..16 MB)."""

    def __init__(self, guest, code):
        mu = U.Uc(U.UC_ARCH_X86, U.UC_MODE_32)
        mu.mem_map(0, 0x100_0000)
        mu.mem_write(0, bytes(guest))
        for va, blob in code.items():
            mu.mem_write(va, blob)
        self.mu, self.calls, self.stops = mu, [], set()

        def code_hook(uc, address, size, _u):
            if address == 0x6CC0EA:
                esp = uc.reg_read(UX.UC_X86_REG_ESP)
                self.calls.append(struct.unpack(
                    '<I', uc.mem_read(esp + 4, 4))[0])
                ret = struct.unpack('<I', uc.mem_read(esp, 4))[0]
                uc.reg_write(UX.UC_X86_REG_ESP, esp + 4)
                uc.reg_write(UX.UC_X86_REG_EIP, ret)
            elif address in self.stops:
                uc.emu_stop()
        mu.hook_add(U.UC_HOOK_CODE, code_hook)

    def run(self, start, stop, **regs):
        for r, v in regs.items():
            self.mu.reg_write(getattr(UX, 'UC_X86_REG_' + r.upper()), v)
        self.stops.add(stop)
        self.mu.emu_start(start, 0, count=2000000)

    def reg(self, r):
        return self.mu.reg_read(getattr(UX, 'UC_X86_REG_' + r.upper()))

    def guest(self, va, n):
        return bytes(self.mu.mem_read(va, n))


def _patches(text):
    return list(exe_patch.parse_hext(text))


# ===================================================================== tests
def test_every_variant_translates():
    exe, m = _tools()
    root = _nx_os.path.join(nxpaths.ROOT, 'mods', 'PS3Tweaks.iro')
    if not _nx_os.path.isfile(root):
        pytest.skip('PS3Tweaks.iro not in mods/')
    import iro
    done = set()
    for e in iro.list_entries(root):
        e = e.replace('\\', '/')
        top = e.split('/')[0]
        if '/hext/' not in e.lower() or top not in (
                'LevelCap', 'SpeedMultiplier', 'StealModifier', 'EXPShare'):
            continue
        H.plan(exe, m, iro.read_one(root, e).decode('utf-8', 'replace'))
        done.add(e)
    assert len(done) == 18


def test_other_versions_refused():
    exe, m = _tools()
    port = P.PORTS[0]
    text = _port_text(port, {0x7B5A40: LEVEL_50})
    assert P.match(_patches(text)) is not None
    bad = text.replace('E8 5B E2 1E 00 90 90', 'E8 5B E2 1E 00 90 91')
    assert P.match(_patches(bad)) is None
    # a table that does not end with moment 0x7FFF
    assert P.match(_patches(_port_text(port, {0x7B5A40: LEVEL_50[:-3]}))) \
        is None


def test_options_combine():
    """Every pair of options writes the same word, or different words."""
    exe, m = _tools()
    texts = [_port_text(P.PORTS[0], {0x7B5A40: LEVEL_50}),
             '00433B21 = E9 62 84 38 00 66 90\n'
             '007BBF88 = ' + SPEED % '05 00 00 00',
             '005CA8F5 = 83 C0 28\n005CA933 = B9 32 00 00 00\n'
             '005CA8F8 = E9 1D 17 1F 00 90\n007BC01A = 8B 0D 0C CE 99 00 8B'
             ' 11 6B D2 68 8B 92 F1 B0 9A 00 81 E2 FF 00 00 00 01 D0 E9 C6 E8'
             ' E0 FF',
             '005C6F28 = E9 D3 02 3E 00\n009A7200 = ' + P.AP_SHARE]
    seen = {}
    for t in texts:
        for off, stock, new, _n in H.plan(exe, m, t):
            assert seen.setdefault(off, new) == new, hex(off)


def _guest(seed):
    rnd = random.Random(seed)
    g = bytearray(0x100_0000)
    g[0x99_0000:0xDD_0000] = rnd.randbytes(0xDD_0000 - 0x99_0000)
    return g, rnd


def test_level_cap_matches_x86():
    exe, m = _tools()
    port = P.PORTS[0]
    words = H.plan(exe, m, _port_text(port, {0x7B5A40: LEVEL_50}))
    code = dict(port.code)
    code[0x7B5A40] = LEVEL_50
    for seed in _seeds(40):
        g, rnd = _guest(seed)
        moment = rnd.choice([0, 0xEB, 0xEC, 0xED, 0x14F, 0x150, 0x1D2,
                             0x400, 0x64E, 0x64F, 0x7FFE,
                             rnd.getrandbits(16)])
        struct.pack_into('<I', g, 0xDC08DC, moment)
        arm = Arm(words, g)
        x86 = X86(g, code)
        level = rnd.randrange(0, 100)
        # site A: x86 `call 0x7B5A30; nop; nop; jge` at 0x5C77D0
        x86.mu.mem_write(0x5C77D7, b'\xF4')            # hlt as the stop
        x86.run(0x5C77D0, 0x5C77D7, eax=level, ebp=0x80000, esp=0x7FF00)
        eflags = x86.reg('eflags')
        ge = bool(eflags >> 7 & 1) == bool(eflags >> 11 & 1)
        arm.call('lc_site_a', {'x8': level, 'x10': 0x1234, 'x0': 7})
        w9 = arm.reg('x9') & 0xFFFFFFFF
        assert (w9 >> 31 == 0) == ge, (seed, moment, level)
        assert arm.reg('x10') == 0x1234 and arm.reg('x0') == 7
        assert arm.reg('x8') == level
        # site B: `call 0x7B5A20` at 0x5C839D (eax + edx - 1), then `jl`
        gain = rnd.randrange(0, 4)
        x86.mu.mem_write(0x5C83A4, b'\xF4')
        x86.run(0x5C839D, 0x5C83A4, eax=level, edx=gain, esp=0x7FF00)
        eflags = x86.reg('eflags')
        lt = bool(eflags >> 7 & 1) != bool(eflags >> 11 & 1)
        arm.call('lc_site_b', {'x10': level + gain - 1, 'x9': 99})
        w8 = arm.reg('x8') & 0xFFFFFFFF
        assert (w8 >> 31 == 1) == lt, (seed, moment, level, gain)
        assert arm.reg('x9') == 99


@pytest.mark.parametrize('mult', [2, 3, 10, 20])
def test_speed_matches_x86(mult):
    exe, m = _tools()
    text = ('00433B21 = E9 62 84 38 00 66 90\n007BBF88 = '
            + SPEED % struct.pack('<I', mult).hex(' ').upper())
    words = H.plan(exe, m, text)
    cave = bytes.fromhex((SPEED % struct.pack('<I', mult).hex(' '))
                         .replace(' ', ''))
    for seed in _seeds(30):
        g, rnd = _guest(seed)
        actor = rnd.randrange(0, 10)
        cx = rnd.getrandbits(16)
        x86 = X86(g, {0x7BBF88: cave, 0x433B28: b'\xF4'})
        x86.run(0x7BBF88, 0x433B28, eax=actor * 0x44, ecx=cx, esp=0x7FF00)
        arm = Arm(words, g)
        arm.ctx(0, actor * 0x44)
        arm.call('sm_site', {'x19': cx, 'x0': GUEST + 0x9A8B12 + actor * 0x44,
                             'x20': CTX, 'x9': 5, 'x10': 6})
        assert arm.guest(0x9A8B12 + actor * 0x44, 2) == \
            x86.guest(0x9A8B12 + actor * 0x44, 2)
        assert arm.reg('x9') == 5 and arm.reg('x10') == 6
        assert arm.reg('x19') == cx


def test_steal_luck_matches_x86():
    exe, m = _tools()
    port = [p for p in P.PORTS if p.name == 'Steal Modifier' and p.sites][0]
    text = '005CA8F5 = 83 C0 28\n005CA933 = B9 64 00 00 00\n' + \
        '\n'.join('%08X = %s' % (va, b.hex(' ').upper())
                  for va, b in port.code.items())
    words = H.plan(exe, m, text)
    cave = port.code[0x7BC01A]
    for seed in _seeds(30):
        g, rnd = _guest(seed)
        ptr = 0x9A0000 + 4 * rnd.randrange(0, 0x100)
        struct.pack_into('<I', g, 0x99CE0C, ptr)
        struct.pack_into('<I', g, ptr, rnd.randrange(0, 10))
        level = rnd.randrange(1, 100)
        x86 = X86(g, {0x7BC01A: cave, 0x5CA8FE: b'\xF4'})
        x86.run(0x7BC01A, 0x5CA8FE, eax=level + 0x28)
        arm = Arm(words, g)
        arm.call('sl_site', {'x8': level, 'x0': 0x99CE0C, 'x9': 1, 'x10': 2})
        assert arm.reg('x8') & 0xFFFFFFFF == x86.reg('eax')
        assert (arm.reg('x0'), arm.reg('x9'), arm.reg('x10')) == \
            (0x99CE0C, 1, 2)


def test_steal_divisor():
    exe, m = _tools()
    w = H.plan(exe, m, '005CA8F5 = 83 C0 14\n005CA933 = B9 19 00 00 00')
    assert {o: n for o, _s, n, _t in w} == {
        0x80F980: A.add_imm(8, 8, 0x14), 0x80FA80: A.movz(10, 0x19),
        0x80FA84: 0x1ACA0D36, 0x80FA88: A.b(0x80FA88, 0x80FAA8)}


def test_ap_share_matches_x86():
    exe, m = _tools()
    words = H.plan(exe, m, '005C6F28 = E9 D3 02 3E 00\n009A7200 = '
                   + P.AP_SHARE)
    cave = bytes.fromhex(P.AP_SHARE.replace(' ', ''))
    mastered = 0
    for seed in _seeds(60):
        g, rnd = _guest(seed)
        idx = rnd.randrange(0, 9)
        rec = 0xDBFD8C + idx * 0x84
        for i in range(16):
            kind = rnd.random()
            if kind < 0.2:
                v = 0xFFFFFFFF
            elif kind < 0.3:
                v = 0xFFFFFF00 | rnd.getrandbits(8)
            else:
                v = rnd.getrandbits(8) | rnd.randrange(0, 0x30000) << 8
            struct.pack_into('<I', g, rec + 0x40 + 4 * i, v)
        for mid in range(256):
            n = rnd.randrange(0, 5)
            for k in range(4):
                w = rnd.randrange(1, 0x200) if k < n else 0xFFFF
                if rnd.random() < 0.05:
                    w = 0
                struct.pack_into('<H', g, 0xDBDF60 + mid * 0x14 + 2 * k, w)
        struct.pack_into('<I', g, 0x99E2C4, rnd.randrange(0, 3000))
        esp, ebp = 0x7FF00, 0x80000
        struct.pack_into('<I', g, ebp - 4, idx)
        struct.pack_into('<III', g, esp, rec, 123, 0xFFFFFFFF)
        x86 = X86(g, {0x9A7200: cave, 0x5C7260: b'\xF4'})
        x86.run(0x9A7200, 0x5C7260, eax=rec, esp=esp, ebp=ebp)
        arm = Arm(words, g)
        arm.ctx(4, esp - 4)                     # the call's return slot
        arm.ctx(5, ebp)
        arm.call('ap_site', {'x20': CTX})
        assert arm.mu.reg_read(UA.UC_ARM64_REG_PC) == SENT_ORIG
        assert arm.reg('x30') == SENT_END
        assert arm.ctx(4) == esp - 4
        assert arm.guest(rec + 0x40, 0x40) == x86.guest(rec + 0x40, 0x40), seed
        assert [a for _x, a in arm.calls] == x86.calls, seed
        mastered += len(x86.calls)
    assert mastered          # the states did reach a master


def test_summon_skip_matches_x86():
    exe, m = _tools()
    port = [p for p in P.PORTS if p.name == 'Summon Animation Skip'][0]
    words = H.plan(exe, m, _port_text(port, {}))
    for seed in _seeds(30):
        g, rnd = _guest(seed)
        g[0x99E314] = rnd.choice([3, 0x16, 2, 4, 0x15, 0x17, 0x83,
                                  rnd.getrandbits(8)])
        dst = 0x9B0000 + rnd.randrange(0, 0x1000)
        cl = rnd.getrandbits(8)
        x86 = X86(g, {0x7B5635: port.code[0x7B5635], 0x5DA11C: b'\xF4'})
        x86.run(0x7B5635, 0x5DA11C, edx=dst, ecx=cl, ebp=0x80000)
        arm = Arm(words, g)
        arm.call('sa_site', {'x0': GUEST + dst, 'x21': cl, 'x1': 11,
                             'x8': 12})
        assert arm.guest(dst, 1) == x86.guest(dst, 1), seed
        assert (arm.reg('x0'), arm.reg('x1'), arm.reg('x8')) == \
            (GUEST + dst, 11, 12)


@pytest.mark.parametrize('mask', [1, 0x80, 0xC0])
def test_dialogue_mash_matches_x86(mask):
    exe, m = _tools()
    cave = bytes.fromhex(
        '85C074130F1F4000C705C011D00001000000E95628CAFFA1D4859A0025%s3D%s'
        '0F854128CAFFA1C83CDC0083E001A3C011D000E92F28CAFF'
        % (struct.pack('<I', mask).hex(), struct.pack('<I', mask).hex()))
    text = '0064991F = E9 9C D7 35 00 90 90 90 90 90 90 90 90 90\n' \
        '009A70C0 = ' + cave.hex(' ').upper()
    words = H.plan(exe, m, text)
    for seed in _seeds(40):
        g, rnd = _guest(seed)
        eax = rnd.choice([0, 0, 1, rnd.getrandbits(32)])
        held = rnd.choice([mask, 0, mask | 0x300, mask & 0x40,
                           rnd.getrandbits(32)])
        struct.pack_into('<I', g, 0x9A85D4, held)
        struct.pack_into('<I', g, 0xD011C0, 0x55)
        x86 = X86(g, {0x9A70C0: cave, 0x64992D: b'\xF4'})
        x86.run(0x9A70C0, 0x64992D, eax=eax)
        arm = Arm(words, g)
        arm.call('dm_site', {'x9': eax, 'x8': 0x7FF00, 'x1': 3, 'x2': 4})
        if arm.stopped == SENT_END:          # into the stock `= 1` store
            assert eax
            arm.mu.mem_write(GUEST + 0xD011C0, struct.pack('<I', 1))
        else:
            assert arm.stopped == SENT_END + 0x14 and not eax
        assert arm.guest(0xD011C0, 4) == x86.guest(0xD011C0, 4), seed
        assert (arm.reg('x8'), arm.reg('x9') & 0xFFFFFFFF, arm.reg('x1'),
                arm.reg('x2')) == (0x7FF00, eax & 0xFFFFFFFF, 3, 4)


def test_counters_branch():
    """`cmp eax, 3; je` in the patched game code itself."""
    exe, m = _tools()
    words = H.plan(exe, m, '004362DC = 83 F8 03 74 1B\n005C985D = 90 90')
    for eax in (0, 1, 2, 3, 4, 5, 6, 0xFF):
        arm = Arm(words, bytearray(0x100_0000))
        got = arm.run(0xF6730, (0xF6750, 0xF67BC),
                      {'x8': eax, 'x23': CTX})
        assert got == (0xF67BC if eax == 3 else 0xF6750), eax
        assert arm.mu.mem_read(CTX + 0x26, 1) == b'\0'
        assert arm.mu.mem_read(CTX + 0x24, 1)[0] == (eax < 3)
    arm = Arm(words, bytearray(0x100_0000))
    assert arm.run(0x8073F4, (0x807404, 0x807480), {'x21': CTX}) == 0x807404


def test_fix_overflow_matches_x86():
    exe, m = _tools()
    port = [p for p in P.PORTS if p.name == 'Fix Overflow'][0]
    words = H.plan(exe, m, _port_text(port, {}))
    rnd = random.Random(7)
    g = bytearray(0x100_0000)
    arm = Arm(words, g)
    for _ in range(300):
        p = rnd.choice([rnd.getrandbits(32), rnd.getrandbits(20),
                        rnd.getrandbits(26)])
        mul = rnd.choice([rnd.getrandbits(16), rnd.getrandbits(32), 1, 0])
        ptr = 0x9A0000
        struct.pack_into('<I', g, ptr + 0x48, mul)
        x86 = X86(g, {0x5DE667: port.code[0x5DE667], 0x5DE682: b'\xF4'})
        # ecx * eax is the product the ARM sees; give it as eax=p, ecx=1
        x86.run(0x5DE667, 0x5DE682, eax=p, ecx=1, edx=ptr)
        arm.call('fo_dmg', {'x8': mul, 'x9': p, 'x20': CTX})
        assert arm.stopped == SENT_END + 0x14
        assert arm.reg('x21') & 0xFFFFFFFF == x86.reg('eax'), (p, mul)
        assert arm.ctx(2) == x86.reg('edx')
        # the random variance
        val = rnd.choice([rnd.getrandbits(32), rnd.getrandbits(16), 0, 1])
        eax = 0xF01 + rnd.getrandbits(8)
        x86 = X86(g, {0x5DE99D: port.code[0x5DE99D], 0x5DE9B6: b'\xF4'})
        x86.run(0x5DE99D, 0x5DE9B6, eax=eax, ecx=val)
        arm.call('fo_rng', {'x8': val, 'x9': eax, 'x19': CTX})
        assert arm.stopped == SENT_END + 0x5C
        assert arm.reg('x8') & 0xFFFFFFFF == x86.reg('eax'), (val, eax)
        assert (arm.ctx(1), arm.ctx(2)) == (x86.reg('ecx'), x86.reg('edx'))


def _with_text(g):
    """Guest memory with ff7_en's .text in place (for whole x86 functions)."""
    exe, _m = _tools()
    pe = exe_patch.parse_pe(exe)
    t = next(s for s in pe['sections'] if s[0] == '.text')
    va, size, raw = t[1], t[2], t[3]
    g[va:va + size] = exe[raw:raw + size]
    return g


@pytest.mark.parametrize('variant', ['Cid', 'Cloud', 'Tifa', 'Variable'])
def test_party_leader_matches_x86(variant):
    exe, m = _tools()
    port = [p for p in P.PORTS if p.name == 'Party Leader Override'][0]
    head = [h for h, v in P._LEADER.items()
            if v == {'Cid': 2, 'Cloud': 0, 'Tifa': 1, 'Variable': 3}[variant]][0]
    blob = P._h(head + ' ' + P._LEADER_TAIL)
    words = H.plan(exe, m, _port_text(port, {0x767376: blob}))
    for seed in _seeds(20):
        g, rnd = _guest(seed)
        g = _with_text(g)
        for a in (0xDC09E5, 0xDC09E6, 0xDC09E7, 0xDC0230):
            g[a] = rnd.choice([0, 1, 2, 3, 8, 0xFF])
        g[0xDC0C96] = rnd.getrandbits(8)
        esp = 0x7FF00
        struct.pack_into('<I', g, esp, 0x401000)        # return address
        x86 = X86(g, {0x767376: blob, 0x767415: port.code[0x767415],
                      0x401000: b'\xF4'})
        x86.run(0x76736E, 0x401000, esp=esp, ebp=0x80000)
        arm = Arm(words, g)
        arm.call('pl_site', {'x20': CTX, 'x19': 77})
        assert arm.stopped == SENT_END + 0x114
        assert arm.reg('x19') == x86.reg('eax'), seed
        assert arm.guest(0xDC0C96, 1) == x86.guest(0xDC0C96, 1), seed
        assert arm.ctx(1) & 0xFF == x86.reg('ecx') & 0xFF


def test_no_battle_music_matches_x86():
    exe, m = _tools()
    port = [p for p in P.PORTS if p.name == 'No Battle Music'][1]
    words = H.plan(exe, m, _port_text(port, {}))
    for seed in _seeds(40):
        g, rnd = _guest(seed)
        snd = rnd.choice([7, 0x107, 0, 1, 0x10, 8, rnd.getrandbits(32)])
        mod = rnd.choice([3, 1, 2, 0])
        struct.pack_into('<I', g, 0xCC0DCC, snd)
        struct.pack_into('<I', g, 0xCC0D84, mod)
        g[0xCC164C] = rnd.getrandbits(8)
        cx = rnd.getrandbits(16)
        esp = 0x7FF00
        # the pushed sound argument
        x86 = X86(g, {0x4093C6: port.code[0x4093C6], 0x4093E4: b'\xF4'})
        x86.run(0x4093C6, 0x4093E4, esp=esp)
        arm = Arm(words, g)
        if snd:
            pushed = x86.guest(esp - 0x20, 4)
            arm.call('nb_push', {'x0': GUEST + 0x500000, 'x19': snd,
                                 'x23': CTX})
            assert arm.guest(0x500000, 4) == pushed, seed
            assert arm.ctx(0) == x86.reg('eax') and arm.ctx(1) == 0
        # the battle flags
        x86 = X86(g, {0x40827B: port.code[0x40827B], 0x4082A0: b'\xF4'})
        x86.run(0x40827B, 0x4082A0, ecx=cx, esp=esp)
        sx = struct.unpack('<b', bytes([g[0xCC164C]]))[0] & 0xFFFF
        arm.call('nb_flag', {'x0': GUEST + 0xDB958A, 'x21': sx | cx,
                             'x22': CTX, 'x8': 1, 'x9': 2, 'x10': 3})
        assert arm.guest(0xDB958A, 2) == x86.guest(0xDB958A, 2), seed
        assert (arm.reg('x8'), arm.reg('x9'), arm.reg('x10')) == (1, 2, 3)
    # the world map call: 0x75E5C0(1), esp restored, then the translator
    g = bytearray(0x100_0000)
    arm = Arm(words, g)
    arm.ctx(4, 0x7FF00)
    arm.call('nb_world', {'x25': CTX, 'x0': 0x80008, 'x3': 33, 'x12': 44})
    assert arm.calls == [(0x75E5C0, 1)]
    assert arm.ctx(4) == 0x7FF00 and arm.ctx(0) == 1
    assert arm.reg('x0') == GUEST + 0x80008
    assert (arm.reg('x3'), arm.reg('x12')) == (33, 44)


def _actors(g, rnd):
    for k in range(9):
        a = 0x9A8B14 + 0x44 * k
        struct.pack_into('<H', g, a - 2, rnd.choice([0, 0, 1, 0x1234]))
        struct.pack_into('<H', g, a, rnd.choice([0xFFFF, 0, 5, 0xFFFE]))
    g[0x9A85D4] = rnd.choice([0, 0xC, 0x4, 0xFF, 0x8])
    struct.pack_into('<I', g, 0x9A88A6, rnd.choice([0, 8, 0xFF, 0xF7]))


@pytest.mark.parametrize('variant', ['WaitMode', 'EasyRunWaitMode'])
def test_wait_mode_matches_x86(variant):
    exe, m = _tools()
    easy = variant == 'EasyRunWaitMode'
    blob = P._h(' '.join((P._WAIT_HEAD, P._WAIT_EASY if easy else
                          ' '.join(['90'] * 13), P._WAIT_TAIL)))
    words = H.plan(exe, m, '004320DC = E9 01 F5 30 00 90\n007415E2 = '
                   + blob.hex(' ').upper())
    for seed in _seeds(80):
        g, rnd = _guest(seed)
        _actors(g, rnd)
        esp = 0x7FF00
        struct.pack_into('<I', g, esp, 0x401000)
        x86 = X86(g, {0x7415E2: blob, 0x401000: b'\xF4'})
        x86.run(0x7415FC, 0x401000, esp=esp)
        arm = Arm(words, g)
        arm.call('wm_site', {'x8': 0, 'x26': CTX, 'x3': 9})
        want = SENT_END if x86.reg('eax') == 1 else SENT_END + 0xAF8
        assert arm.stopped == want, seed
        assert arm.ctx(0) == x86.reg('eax') and arm.ctx(1) == x86.reg('ecx')
        assert arm.reg('x3') == 9 and arm.reg('x8') == 0
        arm.call('wm_site', {'x8': 5, 'x26': CTX})
        assert arm.stopped == SENT_END + 0xAF8


def test_turbo_turns_matches_x86():
    exe, m = _tools()
    port = [p for p in P.PORTS if p.name == 'Turbo Turns'][0]
    blob = P._h(' '.join((P._WAIT_HEAD, P._WAIT_EASY, P._WAIT_TAIL)))
    words = H.plan(exe, m, _port_text(port, {0x7415E2: blob}))
    names = {0x43526A, 0x4351BD, 0x434347, 0x4320AC, 0x4324B6}
    for seed in _seeds(40):
        g, rnd = _guest(seed)
        _actors(g, rnd)
        stop_after = rnd.choice([0, 1, 3, 300])
        g[0x9A85D4] = rnd.choice([0, 0, 0xC, 4])

        def ret_4324b6(n=[0]):
            n[0] += 1
            return 1 if n[0] > stop_after else 0
        # x86: the game functions as stubs that log and return
        x86 = X86(g, {0x7B59B5: port.code[0x7B59B5], 0x7415E2: blob,
                      0x401000: b'\xF4'})
        log86, n86 = [], [0]

        def hook(uc, address, size, _u):
            if address in names:
                esp = uc.reg_read(UX.UC_X86_REG_ESP)
                arg = struct.unpack('<I', uc.mem_read(esp + 4, 4))[0]
                log86.append((address, arg if address == 0x4351BD else 0))
                eax = 0
                if address == 0x4324B6:
                    n86[0] += 1
                    eax = 1 if n86[0] > stop_after else 0
                uc.reg_write(UX.UC_X86_REG_EAX, eax)
                ret = struct.unpack('<I', uc.mem_read(esp, 4))[0]
                uc.reg_write(UX.UC_X86_REG_ESP, esp + 4)
                uc.reg_write(UX.UC_X86_REG_EIP, ret)
        x86.mu.hook_add(U.UC_HOOK_CODE, hook)
        esp = 0x7FF00
        struct.pack_into('<I', g, esp, 0x401000)
        x86.mu.mem_write(esp, struct.pack('<I', 0x401000))
        x86.run(0x7B59B5, 0x401000, esp=esp)
        arm = Arm(words, g)
        narm = [0]

        def fake_4324b6(_a):
            narm[0] += 1
            return 1 if narm[0] > stop_after else 0
        arm.fake[0x4324B6] = fake_4324b6
        arm.ctx(4, esp)                         # the call's return slot
        arm.call('tt_site', {'x23': CTX, 'x5': 55})
        got = [(x, a if x == 0x4351BD else 0) for x, a in arm.calls]
        assert got == log86, seed
        assert arm.ctx(4) == esp + 4
        assert arm.ctx(0) == x86.reg('eax') and arm.ctx(1) == x86.reg('ecx')
        assert arm.reg('x5') == 55


def _ff7_text(mem, va):
    out = bytearray()
    while mem(va, 1) != b'\xFF' and len(out) < 64:
        out += mem(va, 1)
        va += 1
    return bytes(out)


@pytest.mark.parametrize('variant', ['Always', 'SenseRequired'])
def test_show_weakness_matches_x86(variant):
    exe, m = _tools()
    root = _nx_os.path.join(nxpaths.ROOT, 'mods', 'PS3Tweaks.iro')
    if not _nx_os.path.isfile(root):
        pytest.skip('PS3Tweaks.iro not in mods/')
    import iro
    text = iro.read_one(root, 'ShowWeakness/%s/hext/%s.txt' % (
        variant, 'ShowWeakness' if variant == 'Always' else 'SenseRequired')
    ).decode('utf-8', 'replace')
    words = H.plan(exe, m, text)
    code = {va: bytes(b) for va, b in exe_patch.parse_hext(text)}
    entry = 0x7B5700 if variant == 'Always' else 0x7B56B7
    for seed in _seeds(60):
        g, rnd = _guest(seed)
        cmd = rnd.choice([1, 9, 0xA, 0x11, 0x18, 0x19, 0x1B, 2, 0x15, 3,
                          0x16, 0x1E, 0x14, 0xD, 0, 5, 0x40])
        struct.pack_into('<I', g, 0xDC3C70, cmd)
        struct.pack_into('<I', g, 0xDC3C7C, rnd.randrange(0, 10))
        struct.pack_into('<I', g, 0xDC3C78, rnd.randrange(0, 40))
        struct.pack_into('<I', g, 0xDC3C9C, rnd.randrange(0, 10))
        for k in range(10):                     # the enemy's formation index
            struct.pack_into('<I', g, 0xDBA544 + 0x440 * k,
                             rnd.randrange(0, 300))
        flags = [rnd.choice([0, 0, 0, 1]) for _ in range(16)]
        ebp, esp = 0x80000, 0x7FE00
        x, y = rnd.randrange(-50, 400), rnd.randrange(-50, 300)
        struct.pack_into('<h', g, ebp + 0xC, x)
        struct.pack_into('<h', g, ebp + 0x10, y)
        x86 = X86(g, code)
        x86.mu.mem_write(0x401000, b'\xF4')
        draws86, args86 = [], []

        def hook(uc, address, size, _u):
            if address in (0x433580, 0x6F5B03):
                sp = uc.reg_read(UX.UC_X86_REG_ESP)
                a = struct.unpack('<6I', uc.mem_read(sp + 4, 24))
                if address == 0x433580:
                    args86.append(a)
                    uc.mem_write(a[3], struct.pack('<16I', *flags))
                else:
                    draws86.append((a[0], a[1], _ff7_text(
                        lambda v, n: bytes(uc.mem_read(v, n)), a[2]), a[3]))
                ret = struct.unpack('<I', uc.mem_read(sp, 4))[0]
                uc.reg_write(UX.UC_X86_REG_ESP, sp + 4)
                uc.reg_write(UX.UC_X86_REG_EIP, ret)
        x86.mu.hook_add(U.UC_HOOK_CODE, hook)
        x86.mu.mem_write(esp, struct.pack('<I', 0x401000))
        x86.run(entry, 0x401000, esp=esp, ebp=ebp, eax=y & 0xFFFFFFFF,
                ecx=x & 0xFFFFFFFF, ebx=0x1234)
        arm = Arm(words, g)
        draws, args = [], []

        def fake_calc(a):
            sp = a.ctx(4)
            v = struct.unpack('<6I', a.mu.mem_read(GUEST + sp + 4, 24))
            args.append(v)
            a.mu.mem_write(GUEST + v[3], struct.pack('<16I', *flags))
            return 0

        def fake_draw(a):
            sp = a.ctx(4)
            v = struct.unpack('<4I', a.mu.mem_read(GUEST + sp + 4, 16))
            draws.append((v[0], v[1], _ff7_text(
                lambda va, n: bytes(a.mu.mem_read(GUEST + va, n)), v[2]),
                v[3]))
            return 0
        arm.fake[0x433580] = fake_calc
        arm.fake[0x6F5B03] = fake_draw
        arm.ctx(4, esp)
        arm.ctx(5, ebp)
        arm.ctx(3, 0x1234)
        arm.call('sw_site', {'x23': CTX, 'x0': ebp - 0x5C, 'x7': 77})
        assert arm.stopped == SENT_END
        assert arm.reg('x0') == GUEST + ebp - 0x5C and arm.reg('x7') == 77
        assert draws == draws86, (seed, cmd)
        assert [a[:3] + a[4:] for a in args] == \
            [a[:3] + a[4:] for a in args86], (seed, cmd)
        assert arm.ctx(3) == x86.reg('ebx'), seed
        assert arm.ctx(4) == esp


def _port(name):
    return [p for p in P.PORTS if p.name == name][0]


def _table(g, base, n):
    return [struct.unpack_from('<I', g, base + 4 * i)[0] for i in range(n)]


def test_morph_as_well_matches_x86():
    exe, m = _tools()
    port = _port('Morph As Well')
    words = H.plan(exe, m, _port_text(port, {}))
    g = _with_text(bytearray(0x100_0000))
    patched = bytearray(g)
    for va, blob in port.code.items():
        patched[va:va + len(blob)] = blob
    # the two jump tables, as the mod leaves them, against the case the
    # ARM dispatch is steered to
    for base, n, at, entry in ((0x5CBC33, 19, 0x819008, 'mw_case'),
                               (0x5CD9D0, 17, 0x822964, 'mw_vis')):
        want = _table(patched, base, n)
        assert want != _table(g, base, n)
        for i in range(n):
            arm = Arm(words, g)
            arm.ctx(0, i)
            arm.call(entry, {'x20': CTX, 'x0': GUEST + base + 4 * i})
            assert arm.reg('w0') == want[i], (hex(base), i)
    # the value the shared case stores
    for case in (12, 15):
        x86 = X86(bytearray(0x100_0000), port.code)
        x86.run(0x5CBB52, 0x5CBB59, eax=case)
        arm = Arm(words, g)
        arm.ctx(0, case)
        assert arm.run(0x8195E0, {0x8195E4}, {'x20': CTX}) == 0x8195E4
        assert arm.reg('w8') == x86.guest(0xC068CC, 1)[0], case
    # the morph-pair call
    for seed in _seeds(40):
        rnd = random.Random(seed)
        ecx = rnd.choice([0, 0x2000])
        flag = rnd.randrange(0, 16)
        g2 = bytearray(0x100_0000)
        struct.pack_into('<I', g2, 0x99E3F8, flag)
        x86 = X86(g2, port.code)
        x86.stops.add(0x5CAC55)
        x86.run(0x5DF1E3, 0x5DF1EC, ecx=ecx, esp=0x80000)
        called = x86.reg('eip') == 0x5CAC55
        arm = Arm(words, g2)
        arm.ctx(1, ecx)
        got = arm.run(0x84DFD0, {0x84DFD4, 0x84DCAC},
                      {'x20': CTX, 'x8': ecx})
        assert got == (0x84DFD4 if called else 0x84DCAC), seed
        assert arm.ctx(1) == x86.reg('ecx'), seed


def _ap_plus_guest(seed):
    g, rnd = _guest(seed)
    ebp = 0x80000
    weapon = rnd.randrange(0xA00000, 0xD00000)
    armour = rnd.randrange(0xA00000, 0xD00000)
    struct.pack_into('<I', g, 0xC068B0, weapon)
    struct.pack_into('<I', g, ebp - 0x24, armour)
    g[weapon + 0x40E] = rnd.choice([0, 1, 2, 3, 4, 0xFF])
    g[armour + 0x11] = rnd.choice([0, 1, 2, 3, 4, 0xFF])
    for base in (weapon + 0x424, armour + 9):
        for k in range(9):
            g[base + k] = rnd.choice([7, 7, 0, 1, 5])
    i = rnd.randrange(0, 8)
    struct.pack_into('<I', g, ebp - 0x1C, i)
    mat = rnd.randrange(0xA00000, 0xD00000) & ~3
    struct.pack_into('<I', g, ebp - 0x10, mat)
    for at in (mat - 4, mat, mat + 4):
        mid = rnd.choice([0xFF, 0x2C, rnd.randrange(0, 0x60)] + [0x30] * 3)
        g[at] = mid
    struct.pack_into('<H', g, 0xDBDF6D + 0x30 * 0x14, 0x6125)
    g[ebp - 0x14] = g[mat]
    struct.pack_into('<I', g, ebp - 8, rnd.choice(
        [0xFFFFFF, rnd.randrange(0, 0x1000000)]))
    struct.pack_into('<I', g, ebp + 0xC, rnd.randrange(0, 0x100000))
    return g, ebp


@pytest.mark.parametrize('slot', ['weapon', 'armour'])
def test_ap_plus_matches_x86(slot):
    exe, m = _tools()
    port = _port('AP Plus')
    words = H.plan(exe, m, _port_text(port, {}))
    for seed in _seeds(80):
        g, ebp = _ap_plus_guest(seed)
        x86 = X86(g, port.code)
        if slot == 'weapon':
            x86.stops.add(0x5CAF2E)
            x86.run(0x5CAE0C, 0x5CAEA2, ebp=ebp, esp=ebp - 0x40, eax=1,
                    ecx=2)
            done = x86.reg('eip') == 0x5CAEA2
            start, stops = 0x810DC0, (0x810F54, 0x810C94)
        else:
            # from 0x5CAFF3 `mov ecx, [ebp-0x24]`
            x86.run(0x5CAFF6, 0x5CB04C, ebp=ebp, esp=ebp - 0x40, eax=1,
                    ecx=struct.unpack_from('<I', g, ebp - 0x24)[0])
            done = True
            start, stops = 0x811468, (0x811598,)
        arm = Arm(words, g)
        arm.ctx(0, 1)
        arm.ctx(1, 2)
        arm.ctx(5, ebp)
        got = arm.run(start, set(stops), {'x20': CTX})
        assert got == stops[0 if done else 1], seed
        assert arm.guest(ebp - 8, 4) == x86.guest(ebp - 8, 4), seed
        assert arm.ctx(0) == x86.reg('eax'), seed
        if slot == 'weapon':
            assert arm.ctx(1) == x86.reg('ecx'), seed


def test_ap_plus_visual_case():
    exe, m = _tools()
    mw = H.plan(exe, m, _port_text(_port('Morph As Well'), {}))
    ap = H.plan(exe, m, _port_text(_port('AP Plus'), {}))
    g = _with_text(bytearray(0x100_0000))
    want = _table(g, 0x5CD9D0, 17)
    want[12] = want[13] = 0x5CD9A1
    for words, cases in ((ap, (13,)), (mw + ap, (12, 13))):
        for i in range(17):
            arm = Arm(words, g)
            arm.ctx(0, i)
            assert arm.run(0x822964, {0x82296C}, {
                'x20': CTX, 'x0': GUEST + 0x5CD9D0 + 4 * i}) == 0x82296C
            assert arm.reg('w0') == (want[i] if i in cases else
                                     _table(g, 0x5CD9D0, 17)[i]), i
            assert arm.reg('w8') == 0x2695


def _x86_fakes(x86, fns, nargs=3, eax=lambda _args: 0):
    """x86 calls to `fns` logged as (x86, args) and returned from with
    eax(args)."""
    log = []

    def hook(uc, address, size, _u):
        if address in fns:
            sp = uc.reg_read(UX.UC_X86_REG_ESP)
            args = struct.unpack('<%dI' % nargs,
                                 uc.mem_read(sp + 4, 4 * nargs))
            log.append((address, args))
            uc.reg_write(UX.UC_X86_REG_EAX, eax(args))
            ret = struct.unpack('<I', uc.mem_read(sp, 4))[0]
            uc.reg_write(UX.UC_X86_REG_ESP, sp + 4)
            uc.reg_write(UX.UC_X86_REG_EIP, ret)
    x86.mu.hook_add(U.UC_HOOK_CODE, hook)
    return log


def _arm_args(arm, nargs=3):
    """Arm.fake for a game function: logs its arguments like _x86_fakes."""
    log = []

    def fake(a):
        sp = a.ctx(4)
        log.append(struct.unpack('<%dI' % nargs,
                                 a.mu.mem_read(GUEST + sp + 4, 4 * nargs)))
        return 0
    return log, fake


@pytest.mark.parametrize('where', ['steal', 'morph'])
def test_materia_drops_take_matches_x86(where):
    exe, m = _tools()
    port = _port('Materia Drops')
    words = H.plan(exe, m, _port_text(port, {}))
    x86_at, reg, slot = {'steal': (0x5CA975, 'edx', 8),
                         'morph': (0x5CAD0C, 'eax', 4)}[where]
    arm_at, done, resume = {'steal': (0x80FBA4, 0x80FBA8, 0x80FC28),
                            'morph': (0x810938, 0x81093C, 0x8109BC)}[where]
    for seed in _seeds(40):
        rnd = random.Random(seed)
        ebp, esp = 0x80000, 0x7FF00
        v = rnd.choice([rnd.randrange(0, 0x140), rnd.randrange(0x140, 0x200),
                        0x13F, 0x140, rnd.randrange(0, 1 << 32)])
        g = bytearray(0x100_0000)
        struct.pack_into('<I', g, ebp - slot, v)
        x86 = X86(g, port.code)
        x86.stops.update({0x5CA990, 0x5CAD27})
        x86.run(x86_at, {'steal': 0x5CA97B, 'morph': 0x5CAD11}[where],
                ebp=ebp, esp=esp, **{reg: v})
        arm = Arm(words, g)
        arm.ctx(4, esp)
        arm.ctx(5, ebp)
        got = arm.run(arm_at, {done, resume},
                      {'x20': CTX, 'x0': GUEST + ebp - slot, 'x8': esp})
        materia = (v & 0xFFFF) >= 0x140
        assert got == (resume if materia else done), seed
        assert [a for x, a in arm.calls if x == 0x6CC0EA] == x86.calls, seed
        if materia:
            assert arm.ctx(4) == esp - 0x10
            assert arm.ctx({'edx': 2, 'eax': 0}[reg]) == 0x56
        else:
            assert arm.reg('w22') == x86.reg(reg), seed
            assert arm.reg('w8') == esp


def test_materia_drops_drop_matches_x86():
    exe, m = _tools()
    port = _port('Materia Drops')
    words = H.plan(exe, m, _port_text(port, {}))
    for seed in _seeds(40):
        rnd = random.Random(seed)
        esp = 0x7FF00
        row = rnd.randrange(0, 12) * 6
        item = rnd.choice([rnd.randrange(0, 0x140), rnd.randrange(0x140, 0x200),
                           0x13F, 0x140])
        count = rnd.randrange(1, 5)
        g = bytearray(0x100_0000)
        struct.pack_into('<h', g, 0x99E2F2 + row, count)
        x86 = X86(g, port.code)
        x86.stops.add(0x5C867B)
        x86.run(0x5C8551, 0x5C8558, esp=esp, eax=row, edx=item, ecx=77)
        arm = Arm(words, g)
        arm.ctx(0, row)
        arm.ctx(1, 77)
        arm.ctx(2, item)
        arm.ctx(4, esp)
        got = arm.run(0x7FCE08, {0x7FCE0C, 0x7FCCC0},
                      {'x19': CTX, 'x27': count})
        if item >= 0x140:
            assert got == 0x7FCCC0 and x86.reg('eip') == 0x5C867B, seed
            assert [a for x, a in arm.calls if x == 0x6CC0EA] == x86.calls
            assert len(x86.calls) == count
            assert [arm.ctx(i) for i in (0, 1, 2, 4)] == [
                x86.reg(r) for r in ('eax', 'ecx', 'edx', 'esp')], seed
        else:
            assert got == 0x7FCE0C and x86.reg('eip') == 0x5C8558, seed
            assert not arm.calls and not x86.calls
            assert x86.reg('ecx') == count


def test_materia_drops_names_match_x86():
    exe, m = _tools()
    port = _port('Materia Drops')
    words = H.plan(exe, m, _port_text(port, {}))
    # (x86 from, x86 to, ARM call, context register): the stock code pushes
    # (4, id, 8) for each; the mod's code what it calls instead
    sites = ((0x419B73, 0x419B85, 0x86CE0, 'x21'),
             (0x6C716D, 0x6C7183, 0xCEF1D0, 'x23'),
             (0x6C71BB, 0x6C71D1, 0xCEF32C, 'x23'))
    for seed in _seeds(60):
        rnd = random.Random(seed)
        ebp, esp = 0x80000, 0x7FF00
        item = rnd.choice([rnd.randrange(0, 0x140),
                           rnd.randrange(0x140, 0x200), 0x13F, 0x140, 0xFFFF])
        row = rnd.randrange(0, 12)
        g = _with_text(bytearray(0x100_0000))
        struct.pack_into('<I', g, ebp - 0x14, item)
        struct.pack_into('<h', g, ebp - 0x20, row)
        sid = rnd.choice([item & 0x7FFF, -1, rnd.randrange(0x140, 0x200)])
        struct.pack_into('<h', g, 0x99E2F0 + row * 6, sid)
        for x86_at, x86_stop, arm_at, base in sites:
            x86 = X86(g, port.code)
            log86 = _x86_fakes(x86, {0x41963C, 0x6C6B06})
            sp86 = esp
            if x86_at != 0x419B73:          # `push 8` is before the site
                sp86 -= 4
                x86.mu.mem_write(sp86, struct.pack('<I', 8))
            x86.run(x86_at, x86_stop, ebp=ebp, esp=sp86,
                    eax=row * 6 if x86_at == 0x6C71BB else 0)
            arg = item if arm_at == 0x86CE0 else sid & 0xFFFFFFFF
            arm = Arm(words, g)
            sp = esp - 0x10
            arm.mu.mem_write(GUEST + sp + 4, struct.pack('<3I', 4, arg, 8))
            arm.ctx(4, sp)
            arm.ctx(0, arg if arm_at == 0x86CE0 else row * 6)
            arm.ctx(1, row * 6 if arm_at == 0xCEF1D0 else arg)
            arm.ctx(2, arg)
            l0, f0 = _arm_args(arm)
            l1, f1 = _arm_args(arm)
            arm.fake[0x41963C], arm.fake[0x6C6B06] = f0, f1
            got = arm.run(arm_at, {arm_at + 4}, {base: CTX})
            assert got == arm_at + 4
            have = [(0x41963C, a) for a in l0] + [(0x6C6B06, a) for a in l1]
            assert have == log86, (seed, hex(x86_at))
            if arm_at != 0x86CE0:
                assert arm.ctx(1) == x86.reg('ecx'), (seed, hex(x86_at))
                assert arm.ctx(0) == x86.reg('eax'), (seed, hex(x86_at))


def _fp_guest(seed):
    rnd = random.Random(seed)
    g = bytearray(0x100_0000)
    struct.pack_into('<h', g, 0xDC08DC, rnd.choice(
        [0x180, 0x181, 0x500, -5, rnd.randrange(-0x8000, 0x8000)]))
    avail = rnd.choice([0x1FF, rnd.randrange(0, 0x10000)])
    party = [rnd.choice([rnd.randrange(0, 9), 0xFF, rnd.randrange(0, 40)])
             for _ in range(3)]
    req = 0
    for c in party:
        if c < 9 and rnd.random() < 0.7:
            req |= 1 << c
    if rnd.random() < 0.3:
        req |= rnd.randrange(0, 0x10000)
    struct.pack_into('<H', g, 0xDC0DDC, req)
    struct.pack_into('<H', g, 0xDC0DDE, avail)
    g[0xDC0230:0xDC0233] = bytes(party)
    return g


def test_free_party_fix_matches_x86():
    exe, m = _tools()
    port = _port('Free Party Fix')
    words = H.plan(exe, m, _port_text(port, {}))
    seen = set()
    for seed in _seeds(200):
        g = _fp_guest(seed)
        x86 = X86(g, port.code)
        esp = 0x7FF00
        x86.mu.mem_write(esp, struct.pack('<I', 0x401000))
        x86.run(0x9A7900, 0x401000, esp=esp, ecx=11, edx=22, ebx=33)
        legal = x86.reg('eax')
        assert (x86.reg('ecx'), x86.reg('edx'), x86.reg('ebx')) == (11, 22, 33)
        seen.add(legal)
        # the field menu: on, or straight to the routine's return
        arm = Arm(words, g)
        arm.ctx(0, 5)
        arm.ctx(4, 0x7FF00)
        arm.ctx(5, 0x80000)
        got = arm.run(0x982E0C, {0x982E10, 0x982F08}, {'x19': CTX})
        if legal:
            assert got == 0x982E10 and arm.ctx(0) == 5
            assert arm.reg('w8') == 0x7FF00
        else:
            assert got == 0x982F08, seed
            assert (arm.ctx(0), arm.ctx(5)) == (0, 0x7FF00)
        # the two update calls
        for at, base in ((0xF2C2E8, 'x21'), (0xF46F24, 'x19')):
            arm = Arm(words, g)
            arm.ctx(0, 5)
            arm.ctx(4, 0x7FEF0)
            assert arm.run(at, {at + 4}, {base: CTX}) == at + 4
            assert [x for x, _a in arm.calls] == ([0x767218] if legal else [])
            assert arm.ctx(4) == 0x7FEF4
            if not legal:
                assert arm.ctx(0) == 0
    assert seen == {0, 1}


def test_free_party_fix_branches():
    exe, m = _tools()
    words = H.plan(exe, m, _port_text(_port('Free Party Fix'), {}))
    got = {o: n for o, _s, n, note in words if 'cave' not in note}
    assert A.b(0xDBA78C, 0xDBA7C8) == got[0xDBA78C]
    assert A.b(0xDBAAFC, 0xDBAB38) == got[0xDBAAFC]
    assert got[0xDBA660] == got[0xDBAA08] == P.NOP


def _more_pairs():
    exe, m = _tools()
    port = _port('More Pairs')
    return port, H.plan(exe, m, _port_text(port, {}))


def test_more_pairs_small_sites_match_x86():
    port, words = _more_pairs()
    g0 = _with_text(bytearray(0x100_0000))
    ebp, esp = 0x80000, 0x7FF00
    for v in list(range(0, 20)) + [0x102, 0x1FF, 0x106, 0xFFFFFFFE]:
        # Replacement: edx (and [ebp-0x10]) after the cave
        x86 = X86(g0, port.code)
        x86.run(0x5CC7B9, 0x5CC7BF, edx=v, ebp=ebp, esp=esp)
        arm = Arm(words, g0)
        assert arm.run(0x819E54, {0x819E58}, {'x8': v, 'x19': CTX}) \
            == 0x819E58
        assert arm.reg('w20') == x86.reg('edx') == struct.unpack(
            '<I', x86.guest(ebp - 0x10, 4))[0], v
        # Sneak Attack: from [ebp-0x10]
        g = bytearray(g0)
        struct.pack_into('<I', g, ebp - 0x10, v)
        x86 = X86(g, port.code)
        x86.run(0x5CBF97, 0x5CBF9D, ebp=ebp, esp=esp)
        arm = Arm(words, g)
        assert arm.run(0x81A5BC, {0x81A5C0}, {'x8': v, 'x20': CTX}) \
            == 0x81A5C0
        assert arm.reg('w21') == x86.reg('edx'), v
        # Counter: which case
        if v < 0x100:
            struct.pack_into('<I', g, ebp - 0x10, v)
            x86 = X86(g, port.code)
            x86.stops.update({0x5CBE6D, 0x5CBE7E})
            x86.run(0x5CBE49, 0x5CBE57, eax=v, ebp=ebp, esp=esp)
            arm = Arm(words, g)
            arm.ctx(5, ebp)
            got = arm.run(0x81AED4, {0x81AEE8, 0x81AF7C},
                          {'x8': v, 'x19': CTX})
            assert (got == 0x81AEE8) == (x86.reg('eip') == 0x5CBE57), v
            assert arm.ctx(0x23 // 4) >> 24 == (got == 0x81AEE8)
        # Primary: case 3 skips, every other type is the first case
        struct.pack_into('<I', g, ebp - 0x10, v)
        x86 = X86(g, port.code)
        x86.stops.add(0x5CC129)
        x86.run(0x5CC10D, 0x5CC1C9, ebp=ebp, esp=esp, eax=0x55, edx=0x66)
        arm = Arm(words, g)
        arm.ctx(0, 0x55)
        arm.ctx(2, 0x66)
        got = arm.run(0x81B71C, {0x81B9B8, 0x81B7AC},
                      {'x8': v, 'x21': CTX})
        assert got == {0x5CC1C9: 0x81B9B8,
                       0x5CC129: 0x81B7AC}[x86.reg('eip')], v
        assert (arm.ctx(0), arm.ctx(2)) == (x86.reg('eax'), x86.reg('edx'))


def _pair_index(args):
    return (args[0] * 7 + 3) & 0x1F


def test_more_pairs_regular_all_matches_x86():
    port, words = _more_pairs()
    for seed in _seeds(60):
        g, rnd = _guest(seed)
        ebp, esp = 0x80000, 0x7FF00
        mid = rnd.randrange(0, 0x100)
        g[ebp - 0xC] = mid
        kind = rnd.choice([6, 6, 9, 0xA, rnd.randrange(0, 0x10)])
        g[0xDBDF6D + mid * 0x14] = (g[0xDBDF6D + mid * 0x14] & 0xF0) | kind
        if rnd.random() < 0.4:
            g[0xDBDF6F + mid * 0x14] = 0xFF
        edx = mid * 0x14
        g = _with_text(g)                       # the stock `je` after it
        x86 = X86(g, port.code)
        x86.stops.update({0x5CCD88, 0x5CCD9E})
        log86 = _x86_fakes(x86, {0x5CCE7B}, 1, _pair_index)
        x86.run(0x5CCD66, 0x5CCDAA, ebp=ebp, esp=esp, edx=edx, ecx=0x77,
                eax=0x99)
        arm = Arm(words, g)
        for r, v in ((0, 0x99), (1, 0x77), (4, esp), (5, ebp)):
            arm.ctx(r, v)
        arm.fake[0x5CCE7B] = lambda a: _pair_index(
            struct.unpack('<I', a.mu.mem_read(GUEST + a.ctx(4) + 4, 4)))
        got = arm.run(0x819B88, {0x819C00, 0x819CB8, 0x819D04},
                      {'x8': edx, 'x19': CTX})
        assert got == {0x5CCD88: 0x819C00, 0x5CCD9E: 0x819CB8,
                       0x5CCDAA: 0x819D04}[x86.reg('eip')], seed
        assert [a for x, a in arm.calls if x == 0x5CCE7B] == \
            [a[0] for _x, a in log86], seed
        assert [arm.ctx(i) for i in (0, 1, 2, 4)] == [
            x86.reg(r) for r in ('eax', 'ecx', 'edx', 'esp')], seed
        assert arm.guest(0xC06921, 0x80) == x86.guest(0xC06921, 0x80)
        # the cave's return address and last push (the game calls' return
        # slots below are the translation's own business)
        assert arm.guest(esp - 8, 8) == x86.guest(esp - 8, 8)


def test_more_pairs_mega_all_matches_x86():
    port, words = _more_pairs()
    for seed in _seeds(60):
        g, rnd = _guest(seed)
        ebp, esp = 0x80000, 0x7FF00
        wp = rnd.randrange(0xA00000, 0xD00000)
        struct.pack_into('<I', g, 0xC068B0, wp)
        n = rnd.randrange(0, 16)
        for i in range(n):
            g[wp + 0x4C + 6 * i] = rnd.choice(
                [rnd.randrange(0, 0xFF)] + list(range(4, 0x14)))
        g[wp + 0x4C + 6 * n] = 0xFF
        g[ebp - 4] = rnd.randrange(0, 0x100)
        x86 = X86(g, port.code)
        x86.run(0x5CCFAD, 0x5CD03C, ebp=ebp, esp=esp, edx=0x12345678)
        arm = Arm(words, g)
        for r, v in ((2, 0x12345678), (4, esp), (5, ebp)):
            arm.ctx(r, v)
        assert arm.run(0x817784, {0x817A10}, {'x20': CTX}) == 0x817A10
        assert arm.guest(wp, 0x200) == x86.guest(wp, 0x200), seed
        assert [arm.ctx(i) for i in (0, 1, 2, 4)] == [
            x86.reg(r) for r in ('eax', 'ecx', 'edx', 'esp')], seed
        assert arm.guest(esp - 8, 8) == x86.guest(esp - 8, 8), seed


def _fe_guest(seed):
    g, rnd = _guest(seed)
    g = _with_text(g)
    ebp = 0x80000
    g[ebp - 0x70:ebp] = rnd.randbytes(0x70)
    for w in range(256):
        for k in range(4):
            g[0xDBE734 + 0x2C * w + 0x10 + k] = rnd.choice([0, 1, 2, 3, 9])
            g[0xDBCCE2 + 0x24 * w + 0x16 + k] = rnd.choice([0, 1, 2, 3, 9])
    return g, rnd, ebp


def test_fix_equip_display_matches_x86():
    exe, m = _tools()
    port = _port('Fix Equip Display')
    words = H.plan(exe, m, _port_text(port, {}))
    for seed in _seeds(40):
        g, rnd, ebp = _fe_guest(seed)
        esp = ebp - 0x6C
        g[ebp - 0x3C:ebp - 0x38] = struct.pack('<I', rnd.randrange(0, 256))
        c, w = rnd.randrange(0, 10) * 0x84, rnd.randrange(0, 256) * 0x2C
        for x86_at, x86_to, arm_at, arm_to, regs in (
                (0x707952, 0x707A4A, 0xDD86BC, 0xDD8A38,
                 {'ecx': c, 'edx': w, 'eax': 0}),
                (0x707B9B, 0x707C1A, 0xDD9228, 0xDD92A4, {}),
                (0x707BD6, 0x707C1A, 0xDD90C8, 0xDD92A4,
                 {'eax': g[ebp - 0x3C]})):
            x86 = X86(g, port.code)
            x86.run(x86_at, x86_to, ebp=ebp, esp=esp, **regs)
            arm = Arm(words, g)
            arm.ctx(4, esp)
            arm.ctx(5, ebp)
            for r, v in regs.items():
                arm.ctx({'eax': 0, 'ecx': 1, 'edx': 2}[r], v)
            got = arm.run(arm_at, {arm_to}, {'x20': CTX, 'x8': w})
            assert got == arm_to
            # the whole widened frame (below it, the x86's return slots)
            assert arm.guest(esp, 0x6C) == x86.guest(esp, 0x6C), \
                (seed, hex(x86_at))
            assert [arm.ctx(i) for i in (0, 1, 2)] == [
                x86.reg(r) for r in ('eax', 'ecx', 'edx')], (seed, hex(x86_at))


def test_fix_equip_display_frame():
    exe, m = _tools()
    words = H.plan(exe, m, _port_text(_port('Fix Equip Display'), {}))
    got = {o: n for o, _s, n, note in words if 'cave' not in note}
    assert got[0xDD8368] == 0x5101B108          # sub w8, w8, #0x6c


# ---------------------------------------------------- Unlimited Auto Sense
US_NARGS = {0x419457: 3, 0x6C6B06: 3, 0x6F5B03: 4}


def _us_ret(fn, args):
    """What a faked text function gives: a guest address of its own."""
    return 0xE00000 + ((fn ^ args[0] * 7 ^ args[1] * 131) & 0xFFFF) * 16


def _us_text(read, ptr):
    out = bytearray()
    while len(out) < 24:
        b = read(ptr + len(out), 1)[0]
        out.append(b)
        if b == 0xFF:
            break
    return bytes(out)


def _us_x86(x86):
    log = []

    def hook(uc, address, size, _u):
        if address in US_NARGS:
            n = US_NARGS[address]
            sp = uc.reg_read(UX.UC_X86_REG_ESP)
            args = struct.unpack('<%dI' % n, uc.mem_read(sp + 4, 4 * n))
            if address == 0x6F5B03:
                args = args[:2] + (_us_text(uc.mem_read, args[2]),) + args[3:]
            log.append((address, args))
            uc.reg_write(UX.UC_X86_REG_EAX, _us_ret(address, args))
            ret = struct.unpack('<I', uc.mem_read(sp, 4))[0]
            uc.reg_write(UX.UC_X86_REG_ESP, sp + 4)
            uc.reg_write(UX.UC_X86_REG_EIP, ret)
    x86.mu.hook_add(U.UC_HOOK_CODE, hook)
    return log


def _us_arm(arm):
    log = []
    for fn, n in US_NARGS.items():
        def fake(a, fn=fn, n=n):
            sp = a.ctx(4)
            args = struct.unpack('<%dI' % n,
                                 a.mu.mem_read(GUEST + sp + 4, 4 * n))
            if fn == 0x6F5B03:
                args = args[:2] + (_us_text(
                    lambda va, k: a.mu.mem_read(GUEST + va, k), args[2]),) \
                    + args[3:]
            log.append((fn, args))
            return _us_ret(fn, args)
        arm.fake[fn] = fake
    return log


def _us_guest(seed):
    rnd = random.Random(seed)
    g = _with_text(bytearray(0x100_0000))
    for lo, hi in ((0x9A8000, 0x9AC000), (0xDB9000, 0xDC5000),
                   (0xE00000, 0xF00000)):
        g[lo:hi] = bytes(rnd.getrandbits(8) for _ in range(hi - lo)) \
            if hi - lo < 0x20000 else rnd.randbytes(hi - lo)
    t = rnd.choice([0, 1, 3, 4, 5, 7, 9])
    struct.pack_into('<I', g, 0xDC3C9C, t)
    a = rnd.randrange(0, 10)
    struct.pack_into('<I', g, 0xDC3C7C, a)
    for o in (0, 4, 0x14):          # the magic branch's indices: small
        struct.pack_into('<I', g, a * 0x700 + 0xDC2110 + o, rnd.randrange(4))
    g[0xDC3C70] = rnd.choice([1, 2, 5, 9, 0x11, 0x15, rnd.randrange(256)])
    g[0x9A85D4] = rnd.choice([0, 0x80, 0x81, 0xC0, rnd.randrange(256)])
    if t >= 4:
        g[0x9A8B39 + t * 0x44] = rnd.choice([0, 1, 2])
        en = g[(t - 4) * 16 + 0x9A8794] * 0xB8 + 0x9A8F24
        for i in range(4):
            g[en + i] = rnd.choice([0x80, 0x00, 0xC0, 0x40, rnd.randrange(256)])
        for o in (4, 6, 8, 10, 0x18):
            struct.pack_into('<H', g, en + o, rnd.choice([
                0xFFFF, rnd.randrange(0x80), rnd.randrange(0x80, 0x100),
                rnd.randrange(0x100, 0x120), rnd.randrange(0x120, 0x140),
                rnd.randrange(0x140, 0x200), rnd.randrange(0x8000, 0xFFFF)]))
        for o in (0, 4):
            struct.pack_into('<I', g, (t - 4) * 0x68 + 0x9AB2A8 + o,
                             rnd.choice([rnd.randrange(0x10000),
                                         rnd.randrange(0x10000, 1 << 31),
                                         rnd.randrange(1 << 32), 0, 9, 10]))
    return rnd, g


@pytest.mark.parametrize('name', ['Unlimited Sense', 'Show Steal And Morph'])
def test_unlimited_sense_matches_x86(name):
    exe, m = _tools()
    port = _port(name)
    words = H.plan(exe, m, _port_text(port, {}))
    for seed in _seeds(60):
        rnd, g = _us_guest(seed)
        ebp, esp = 0x80000, 0x80000 - 0x60
        pos = rnd.choice([6, 0xA, 6, 0xA, 3, 9])
        struct.pack_into('<I', g, ebp - 0xC, pos)
        struct.pack_into('<I', g, ebp - 0x14, rnd.choice(
            [rnd.randrange(0x10000), 0, 7, 0xFFFF]))
        g[ebp + 4] = rnd.choice([0xF5, 0x74])
        x86 = X86(g, port.code)
        log86 = _us_x86(x86)
        x86.run(0x419BB2, 0x419C0F, ebp=ebp, esp=esp, ecx=pos, ebx=0x1234)
        arm = Arm(words, g)
        log = _us_arm(arm)
        arm.ctx(4, esp)
        arm.ctx(5, ebp)
        arm.ctx(3, 0x1234)
        arm.ctx(1, pos)
        got = arm.run(0x86698, {0x86868}, {'x21': CTX})
        assert got == 0x86868, seed
        assert log == log86, seed
        assert arm.guest(ebp - 0x58, 0x58) == x86.guest(ebp - 0x58, 0x58), seed
        assert [arm.ctx(i) for i in (0, 1, 2, 4)] == [
            x86.reg(r) for r in ('eax', 'ecx', 'edx', 'esp')], seed
        f = arm.mu.mem_read(CTX + 0x20, 8)
        assert (f[0], f[3], f[4], f[6]) == (0, 1, 0, 0)
        assert arm.ctx(3) == 0x1234         # the caller keeps its ebx
        if name == 'Unlimited Sense':
            assert not log


def test_unlimited_sense_reads_16_bits_elsewhere():
    """A number that is not a Sense HP / MP is the stock 16-bit one."""
    exe, m = _tools()
    words = H.plan(exe, m, _port_text(_port('Unlimited Sense'), {}))
    ebp = 0x80000
    g = bytearray(0x100_0000)
    struct.pack_into('<I', g, ebp - 0x14, 0xABCD0000 + 1234)
    struct.pack_into('<I', g, ebp - 0xC, 3)
    arm = Arm(words, g)
    arm.ctx(4, ebp - 0x60)
    arm.ctx(5, ebp)
    assert arm.run(0x86698, {0x86868}, {'x21': CTX}) == 0x86868
    assert struct.unpack_from('<5I', arm.guest(ebp - 0x58, 20)) == \
        (4, 4, 3, 2, 1)


@pytest.mark.parametrize('site,ret', [(0x8557C, 0x419974),
                                      (0x85A24, 0x4199F5)])
def test_show_steal_and_morph_return_slot(site, ret):
    exe, m = _tools()
    words = H.plan(exe, m, _port_text(_port('Show Steal And Morph'), {}))
    arm = Arm(words, bytearray(0x100_0000))
    arm.ctx(4, 0x7FF00)
    got = arm.run(site, {site + 4}, {'x20': CTX, 'x16': 0x1616,
                                     'x17': 0x1717})
    assert got == site + 4
    assert [x for x, _a in arm.calls] == [0x419A8F]
    assert struct.unpack('<I', arm.guest(0x7FF00, 4))[0] == ret
    assert (arm.reg('x16'), arm.reg('x17')) == (0x1616, 0x1717)


def test_unlimited_sense_limit_and_auto():
    exe, m = _tools()
    for name, auto in (('Unlimited Sense', 0), ('Unlimited Auto Sense', 1),
                       ('Show Steal And Morph', 1),
                       ('Show Steal And Morph (No Auto)', 0)):
        words = H.plan(exe, m, _port_text(_port(name), {}))
        assert any(o == 0x8530C and w == P.NOP
                   for o, _s, w, _n in words) == bool(auto), name
    arm = Arm(words, bytearray(0x100_0000))
    for x in (0, 1, 0x7530, 0xFFFF, 0x7FFFFFFE, 0x7FFFFFFF, 0x80000000,
              0xFFFFFFFF, 0x12345678):
        arm.call('us_limit', {'x8': x})
        assert (arm.reg('w8') < 0x753) == (x < 0x7FFFFFFF), hex(x)


# ------------------------------------------------- Toggle Animation Skip
def test_toggle_animation_skip_matches_x86():
    exe, m = _tools()
    port = _port('Toggle Animation Skip')
    words = H.plan(exe, m, _port_text(port, {}))
    assert (0x8E62C, 0xB25A0BE8, 0xD2C02308) in [w[:3] for w in words]
    ebp = 0x80000
    for flag in range(8):
        for button in (0, 1, 2, 0xFF):
            for old_eax, old_ecx in ((0x12345678, 0x9ABCDEF0), (0, 0)):
                g = bytearray(0x100_0000)
                g[0x9ABE68], g[0x9A85D4] = flag, button
                struct.pack_into('<I', g, ebp - 4, 0xDEADBEEF)
                # the toggle, at the battle routine's start
                x86 = X86(g, port.code)
                x86.run(0x41C0C1, 0x41C0C8, ebp=ebp, esp=ebp - 0x40,
                        eax=old_eax, ecx=old_ecx)
                arm = Arm(words, g)
                arm.ctx(0, old_eax)
                arm.ctx(1, old_ecx)
                got = arm.run(0x91B60, {0x91B64}, {
                    'x26': CTX, 'x0': GUEST + ebp - 4, 'x8': 8, 'x9': 9,
                    'x10': 10, 'x11': 11})
                assert got == 0x91B64
                for va, n in ((0x9ABE68, 1), (ebp - 4, 4)):
                    assert arm.guest(va, n) == x86.guest(va, n), (flag, button)
                assert (arm.ctx(0), arm.ctx(1)) == (
                    x86.reg('eax'), x86.reg('ecx')), (flag, button)
                assert [arm.reg('x%d' % r) for r in (0, 8, 9, 10, 11)] == \
                    [GUEST + ebp - 4, 8, 9, 10, 11]
        # the copy the skip holds back
        g = bytearray(0x100_0000)
        g[0x9ABE68] = flag
        struct.pack_into('<I', g, ebp - 4, 0x60000)
        g[0x60000] = 0x11
        x86 = X86(g, port.code)
        x86.run(0x5DA117, 0x5DA11C, ebp=ebp, esp=ebp - 0x40, edx=0x60000,
                ecx=0x77)
        arm = Arm(words, g)
        got = arm.run(0x849F6C, {0x849F70}, {
            'x20': CTX, 'x0': GUEST + 0x60000, 'x21': 0x77, 'x8': 8,
            'x9': 9, 'x10': 10})
        assert got == 0x849F70
        assert arm.guest(0x60000, 1) == x86.guest(0x60000, 1), flag
        assert [arm.reg('x%d' % r) for r in (0, 8, 9, 10)] == \
            [GUEST + 0x60000, 8, 9, 10]
    # the counter
    for v in range(0x80):
        g = bytearray(0x100_0000)
        struct.pack_into('<I', g, 0x9AEA98, v)
        x86 = X86(g, port.code)
        x86.run(0x436DE6, 0x436DF8, ebp=ebp, esp=ebp - 0x40)
        arm = Arm(words, g)
        assert arm.run(0xF90C8, {0xF90CC}, {'x8': v + 1}) == 0xF90CC
        assert arm.reg('w21') == struct.unpack(
            '<I', x86.guest(0x9AEA98, 4))[0], v


# ---------------------------------------------------- Multi Linked Slots
def _intercept(arm, arm_at, x86, nargs, k=0):
    """A translated game function at `arm_at` faked like the trampolines'
    functions: [(args)] logged, eax 0."""
    slot = SENT_FN + 4 * (len(P.tramp_all()) + 1 + k)
    arm.tramp[arm_at] = slot
    arm.fake_at[slot] = x86
    log, fake = _arm_args(arm, nargs)
    arm.fake[x86] = fake
    return log


def test_multi_linked_slots_loops_match_x86():
    exe, m = _tools()
    port = _port('Multi Linked Slots')
    words = H.plan(exe, m, _port_text(port, {}))
    ebp = 0x80000
    for seed in _seeds(12):
        rnd = random.Random(seed)
        g = _with_text(bytearray(0x100_0000))
        g[0xC068D0:0xC06920] = rnd.randbytes(0x50)
        struct.pack_into('<I', g, 0xC068B0, 0x500000)
        g[0x500424:0x500440] = rnd.randbytes(0x1C)
        g[0x600000:0x600020] = rnd.randbytes(0x20)
        struct.pack_into('<I', g, ebp - 8, 0x600000)
        x86 = X86(g, port.code)
        log86 = _x86_fakes(x86, {0x5CB8D6}, nargs=7)
        x86.run(0x5CB404, 0x5CB500, ebp=ebp, esp=ebp - 0x40,
                eax=0x11111111, ecx=0x22222222, edx=0x33333333)
        arm = Arm(words, g)
        log = _intercept(arm, 0x815580, 0x5CB8D6, 7)
        for i, v in enumerate((0x11111111, 0x22222222, 0x33333333)):
            arm.ctx(i, v)
        arm.ctx(4, ebp - 0x40)
        arm.ctx(5, ebp)
        arm.mu.reg_write(UA.UC_ARM64_REG_X19, 0xC068B0)
        got = arm.run(0x812AC8, {0x813028}, {'x24': CTX})
        assert got == 0x813028, seed
        assert len(log86) == 14
        assert [(0x5CB8D6, a) for a in log] == log86, seed
        assert arm.guest(ebp - 0x40, 0x40) == x86.guest(ebp - 0x40, 0x40)


def test_multi_linked_slots_pairs_match_x86():
    exe, m = _tools()
    port = _port('Multi Linked Slots')
    words = H.plan(exe, m, _port_text(port, {}))
    esp = 0x7FF00
    rnd = random.Random(5)
    for a in (2, 6, 0, 3, 7, 0x102):
        for b in (0, 1, 2, 3, 6, 7, 0xB, 0xF, 0xFF, 0x107):
            args = (a, b, rnd.getrandbits(32), rnd.getrandbits(32),
                    rnd.getrandbits(32), rnd.getrandbits(32),
                    rnd.getrandbits(32))
            g = _with_text(bytearray(0x100_0000))
            struct.pack_into('<8I', g, esp, 0xDEAD00, *args)
            x86 = X86(g, port.code)
            log86 = _x86_fakes(x86, {0x5CB94A}, nargs=5)
            x86.run(0x5CB8D6, 0xDEAD00, esp=esp, ebp=0x90000)
            arm = Arm(words, g)
            log = _intercept(arm, 0x8189B0, 0x5CB94A, 5)
            arm.mu.mem_write(0x12CE2B0, struct.pack('<Q', CTX))  # its context
            arm.ctx(4, esp)
            arm.ctx(5, 0x90000)
            got = arm.run(0x815580, {SENT_END}, {'x30': SENT_END})
            assert got == SENT_END
            assert [(0x5CB94A, x) for x in log] == log86, (a, b)
            assert (b & 3 == 3) == any(
                x[0] == args[2] for x in log), (a, b)


# ------------------------------------------------ Break Damage/HP Limit
_BD_VARIANTS = ['Conditional', 'Conditional, HP', 'Unconditional',
                'Unconditional, HP', 'Unlimited', 'Unlimited, HP']


def _bd_port(variant):
    exe, m = _tools()
    port = _port('Break Damage Limit (%s)' % variant)
    return port, H.plan(exe, m, _port_text(port, {}))


@pytest.mark.parametrize('variant', ['Conditional', 'Unlimited'])
def test_break_damage_digits_match_x86(variant):
    port, words = _bd_port(variant)
    esp, ebp, arr = 0x7FF00, 0x7FF80, 0x60000
    rnd = random.Random(variant)
    vals = [0, 1, 9, 10, 99, 9999, 10000, 99999, 123456, 999999999,
            0x7FFFFFFF, 0xFFFFFFFF, 0x80000000, 0xFFFF8000] + \
        [rnd.getrandbits(32) for _ in range(40)]
    for v in vals:
        g = _with_text(bytearray(0x100_0000))
        struct.pack_into('<3I', g, esp, 0xDEAD00, v, arr)
        g[arr:arr + 0x20] = rnd.randbytes(0x20)
        x86 = X86(g, port.code)
        x86.run(0x5BB9C9, 0xDEAD00, esp=esp, ebp=ebp, ecx=0x1234,
                edx=0x5678, eax=0x9ABC)
        arm = Arm(words, g)
        for i, r in enumerate((0x9ABC, 0x1234, 0x5678)):
            arm.ctx(i, r)
        arm.ctx(4, esp)
        arm.ctx(5, ebp)
        got = arm.run(0x7C5D08, {0x7C5D0C}, {'x23': CTX})
        assert got == 0x7C5D0C
        assert arm.guest(arr, 0x20) == x86.guest(arr, 0x20), hex(v)
        assert arm.guest(esp - 8, 8) == x86.guest(esp - 8, 8), hex(v)
        assert [arm.ctx(i) for i in (0, 1, 2, 4, 5)] == [
            x86.reg(r) for r in ('eax', 'ecx', 'edx', 'esp', 'ebp')], hex(v)


@pytest.mark.parametrize('variant', _BD_VARIANTS)
def test_break_damage_cap_matches_x86(variant):
    port, words = _bd_port(variant)
    ebp = 0x80000
    for who in range(5):
        for char in (0, 3, 6, 8):
            for limit in (0, 0x1FF, 0x200, 0x8000):
                g = _with_text(bytearray(0x100_0000))
                g[0x99E308] = who
                g[0xDC0230:0xDC0233] = bytes((char, char, char))
                struct.pack_into('<H', g, 0xDBFDAE + char * 0x84, limit)
                x86 = X86(g, port.code)
                x86.stops.update({0x5DAA99, 0x5DAAA0})
                x86.run(0x5DAA92, 0x5DAA99, ebp=ebp, esp=ebp - 0x40,
                        eax=0x11, ebx=0x22, ecx=0x33, edx=0x44)
                arm = Arm(words, g)
                for i, r in enumerate((0x11, 0x33, 0x44, 0x22)):
                    arm.ctx(i, r)
                got = arm.run(0x84B650, {0x84B654},
                              {'x21': CTX, 'x8': 8, 'x9': 9, 'x10': 10})
                assert got == 0x84B654
                want = struct.unpack('<I', x86.guest(ebp - 4, 4))[0]
                key = (who, char, limit)
                assert arm.reg('w8') == want, key
                assert [arm.ctx(i) for i in range(4)] == [
                    x86.reg(r) for r in ('eax', 'ecx', 'edx', 'ebx')], key


@pytest.mark.parametrize('variant', _BD_VARIANTS)
def test_break_damage_push_and_store_match_x86(variant):
    port, words = _bd_port(variant)
    ebp, obj = 0x80000, 0x60000
    rnd = random.Random(variant)
    for _ in range(30):
        g = _with_text(bytearray(0x100_0000))
        g[obj:obj + 0x20] = rnd.randbytes(0x20)
        struct.pack_into('<I', g, ebp - 4, obj)
        struct.pack_into('<I', g, ebp + 0x10, rnd.getrandbits(32))
        struct.pack_into('<I', g, 0x99E51C, rnd.getrandbits(32))
        # the damage pushed for the digits
        x86 = X86(g, port.code)
        x86.run(0x5BB6FA, 0x5BB702, ebp=ebp, esp=ebp - 0x40, ecx=0xAAAA5555)
        arm = Arm(words, g)
        arm.ctx(1, 0xAAAA5555)
        arm.ctx(4, ebp - 0x40)
        arm.ctx(5, ebp)
        assert arm.run(0x7C5A74, {0x7C5AB0}, {'x22': CTX}) == 0x7C5AB0
        assert arm.guest(ebp - 0x44, 4) == x86.guest(ebp - 0x44, 4)
        assert [arm.ctx(i) for i in (0, 1, 4)] == [
            x86.reg(r) for r in ('eax', 'ecx', 'esp')]
        # the stored damage
        x86 = X86(g, port.code)
        x86.run(0x5DA58B, 0x5DA596, ebp=ebp, esp=ebp - 0x40, eax=0x12345678)
        arm = Arm(words, g)
        arm.ctx(0, 0x12345678)
        arm.ctx(4, ebp - 0x40)
        arm.ctx(5, ebp)
        assert arm.run(0x84E2F0, {0x84E328}, {'x19': CTX}) == 0x84E328
        # the mod returns into the middle of `mov [edx+4], ax`, a 32-bit
        # store: +6 is junk until the function stores it at 0x5DA5BC
        a, b = arm.guest(obj, 0x20), x86.guest(obj, 0x20)
        assert a[:6] + a[8:] == b[:6] + b[8:]
        assert [arm.ctx(i) for i in (0, 2)] == [
            x86.reg(r) for r in ('eax', 'edx')]


def _bd_text_args(code, start, stops, ebp, regs):
    g = _with_text(bytearray(0x100_0000))
    struct.pack_into('<I', g, ebp - 4, 0x60000)
    struct.pack_into('<I', g, ebp - 0x2C, regs.pop('branch'))
    struct.pack_into('<I', g, 0x60000 + 0x440 + 0x12, 0x40)
    struct.pack_into('<I', g, 0xDC3AA4, 0x3000)
    struct.pack_into('<H', g, ebp - 0x18, 1)
    struct.pack_into('<H', g, ebp - 8, 1)
    x86 = X86(g, code)
    log = _x86_fakes(x86, {0x6F9739}, nargs=6)
    x86.stops.update(stops)
    x86.run(start, min(stops), ebp=ebp, esp=ebp - 0x40, **regs)
    return log


@pytest.mark.parametrize('variant', _BD_VARIANTS)
def test_break_damage_hp_text_args_match_x86(variant):
    port, words = _bd_port(variant)
    names = {w[0] for w in words}
    ebp = 0x80000
    cases = [(0xD40F58, 'x21', 0x6DD814, {0x6DD87E, 0x6DD8B3}, {}),
             (0xD47638, 'x20', 0x6E1442, {0x6E149B}, {'edx': 1})]
    for arm_at, reg, start, stops, regs in cases:
        hooked = arm_at in names
        for branch in (1, 0):
            if start == 0x6E1442 and not branch:
                continue
            stock = _bd_text_args({}, start, stops, ebp,
                                  dict(regs, branch=branch))
            mod = _bd_text_args(port.code, start, stops, ebp,
                                dict(regs, branch=branch))
            assert len(stock) == len(mod) == 1
            args = stock[0][1]
            if not hooked:
                assert mod == stock, (variant, hex(arm_at))
                continue
            assert (mod == stock) == (branch == 0), (variant, hex(arm_at))
            # the ARM given the stock arguments gives the mod's
            for colour in ({args[4], 2} if start == 0x6E1442 else
                           {args[4]}):
                a = list(args)
                a[4] = colour
                g = _with_text(bytearray(0x100_0000))
                sp = 0x7FF00
                struct.pack_into('<7I', g, sp, 0, *a)
                arm = Arm(words, g)
                log, arm.fake[0x6F9739] = _arm_args(arm, 6)
                arm.ctx(4, sp)
                got = arm.run(arm_at, {arm_at + 4}, {reg: CTX})
                assert got == arm_at + 4
                assert arm.ctx(4) == sp + 4
                if colour == args[4]:
                    want = mod[0][1]
                else:
                    want = tuple(a)
                assert log == [want], (hex(arm_at), branch, colour)


def test_break_damage_constant_entries():
    port, words = _bd_port('Conditional, HP')
    arm = Arm(words, bytearray(0x100_0000))
    for entry, want, n in (('bd_st5', 5, 4), ('bd_st108', 0x108, 4),
                           ('bd_sth7d', 0x7D, 2), ('bd_sth17c', 0x17C, 2)):
        arm.mu.mem_write(GUEST + 0x60000, b'\xEE' * 8)
        arm.call(entry, {'x0': GUEST + 0x60000, 'x9': 0x99})
        assert arm.guest(0x60000, 8) == struct.pack('<I', want)[:n] + \
            b'\xEE' * (8 - n), entry
        assert arm.reg('x9') == 0x99 and arm.reg('x0') == GUEST + 0x60000
    arm.call('bd_hp88', {})
    assert arm.reg('w8') == 0x7FFF0F
    arm.call('bd_cap99', {})
    assert arm.reg('w8') == 99999
    for v in (0, 0x7FFE, 0x7FFF, 0x8000, 0x270F, 0xFFFFFFFF):
        arm.call('bd_cmp7fff', {'x8': v, 'x9': 0x99})
        nzcv = arm.reg('nzcv')
        x = U.Uc(U.UC_ARCH_ARM64, U.UC_MODE_ARM)
        x.mem_map(0, 0x1000)
        x.mem_write(0, struct.pack('<2I', 0x6B09011F, 0xD65F03C0))
        x.reg_write(UA.UC_ARM64_REG_X8, v)
        x.reg_write(UA.UC_ARM64_REG_X9, 0x7FFF)
        x.emu_start(0, 4)
        assert nzcv == x.reg_read(UA.UC_ARM64_REG_NZCV), hex(v)
        assert arm.reg('x9') == 0x99


# ------------------------------------------------------- Two Accessories
# Each region of ff7nx_ps3regions (x86c's C) against the mod's x86: entered
# at its hook, run to its resume point; the whole guest memory, the calls the
# region makes (faked on both sides) and the context must match.
import ff7nx_ps3regions as S  # noqa: E402

X86_REGS = ('eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi')


def _region(name):
    return [r for r in S.TWO_ACCESSORIES + S.LIMIT_OVERHAUL +
            S.ENEMY_SCALING + S.SOLO + S.SOLO_CAIT + S.SOLO_VINCENT +
            S.SOLO_RANDOM + S.BATTLE_PHS + S.PARTY_RANDO +
            S.ITEM_RANDO + S.BATTLE_RANDO + S.BATTLE_RANDO_ES +
            S.DOUBLE_BOSSES + S.CHARACTER_ORDER + S.STARTING_LEVEL
            if r.name == name][0]



def _seeds(n):
    """range(n), or the slice SEEDS=lo:hi of it (a long test run in parts)"""
    lo, _c, hi = os.environ.get('SEEDS', '').partition(':')
    return range(n)[int(lo or 0):int(hi) if hi else n]

_RWORDS = {}


def _region_words(regions, data=None):
    key = (tuple(r.name for r in regions), tuple(sorted((data or {}).items())))
    if key not in _RWORDS:
        _RWORDS[key] = _region_words1(regions, data or {})
    return _RWORDS[key]


def _region_words1(regions, data):
    exe, m = _tools()
    out = []
    port = P.Port('test', {}, {}, [])
    for b in sorted({P.owner(site[3]) for r in regions
                     for site in r.sites(m)}):
        P._place(m, exe, b, port, data, out)
    syms = {n: a for _b, n, a in P.symbols(m, exe)}
    for r in regions:
        for _x86, arm, stock, sym in r.sites(m):
            out.append((arm, stock, A.bl(arm, syms[sym]), 'hook'))
    return out


class X86Fault(Exception):
    """the x86 side faulted (a random pointer): no run to compare"""


def _run_region(region, k, g, code, regs, ret=lambda fn, args: 0,
                nargs=5, scratch=(), data=None, host=None):
    """(x86, arm, x86 call log, arm call log); asserts they end at the
    same exit with the same memory and context. scratch: {exit: [offsets
    from esp]} the blob writes on its way out (a byte its resume reads),
    which the x86 does not. data: the blob's data areas ({symbol: bytes});
    host: host registers the entry starts with (as the port leaves them)."""
    exe, m = _tools()
    words = _region_words([region], data)
    x86_at, hook = region.entries[k]
    g = bytearray(g)
    for va, blob in code.items():           # the mod's bytes on both sides
        g[va:va + len(blob)] = blob
    x86 = X86(g, code)
    fns = set(P.tramp_all().values()) | {fn for fn in
                                         _region_calls(region, code)}
    log86, slots = [], set()

    def hook86(uc, address, size, _u):
        if address in fns:
            sp = uc.reg_read(UX.UC_X86_REG_ESP)
            slots.add(sp)
            args = struct.unpack('<%dI' % nargs,
                                 uc.mem_read(sp + 4, 4 * nargs))
            log86.append((address, args))
            uc.reg_write(UX.UC_X86_REG_EAX, ret(address, args))
            back = struct.unpack('<I', uc.mem_read(sp, 4))[0]
            uc.reg_write(UX.UC_X86_REG_ESP, sp + 4)
            uc.reg_write(UX.UC_X86_REG_EIP, back)
    x86.mu.hook_add(U.UC_HOOK_CODE, hook86)
    stops = set(v for v in region.exits if v != 'ret')
    x86.stops.update(stops)
    try:
        x86.run(x86_at, min(stops), **regs)
    except U.UcError as e:
        raise X86Fault(e)
    eip = x86.reg('eip')
    assert eip in stops, hex(eip)
    getattr(ret, 'reset', lambda: None)()
    # a variable the port moves (out of .text): the ARM side's copy where
    # it lives, as the x86's was
    ga = bytearray(g)
    for old, new in getattr(region, 'move', {}).items():
        ga[new:new + 4] = g[old:old + 4]
    arm = Arm(words, ga)
    for i, r in enumerate(X86_REGS):
        arm.ctx(i, regs.get(r, 0))
    logarm = []
    for fn in fns:
        def fake(a, fn=fn):
            sp = a.ctx(4)
            args = struct.unpack('<%dI' % nargs,
                                 a.mu.mem_read(GUEST + sp + 4, 4 * nargs))
            logarm.append((fn, args))
            return ret(fn, args)
        arm.fake[fn] = fake
    got = arm.run(hook, set(region.exits.values()),
                  dict(host or {}, **{'x%d' % region.ctx: CTX}),
                  count=2000000)
    assert got == region.exits[eip], (hex(eip), got and hex(got))
    assert logarm == log86
    a = bytearray(arm.guest(0, 0x100_0000))
    b = bytearray(x86.guest(0, 0x100_0000))
    # the return slots: the x86 `call` writes its return address there,
    # the port's calls leave the slot as it was
    for sp in slots:
        b[sp:sp + 4] = a[sp:sp + 4]
    for old, new in getattr(region, 'move', {}).items():
        assert a[new:new + 4] == b[old:old + 4], (hex(old), a[new:new + 4],
                                                  b[old:old + 4])
        a[new:new + 4], b[old:old + 4] = b[new:new + 4], a[old:old + 4]
    for off in dict(scratch).get(eip, ()):
        at = (x86.reg('esp') + off) & 0xFFFFFFFF
        b[at] = a[at]
    if a != b:
        at = next(i for i in range(len(a)) if a[i] != b[i])
        assert a == b, 'guest differs from 0x%X: %s vs %s' % (
            at, a[at:at + 8].hex(), b[at:at + 8].hex())
    assert [arm.ctx(i) for i in range(8)] == [x86.reg(r) for r in X86_REGS]
    return x86, arm, log86


_CALLS = {}


def _region_calls(region, code):
    key = (region.name, id(code))
    if key not in _CALLS:
        import capstone
        md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        exe, _m = _tools()
        pe = exe_patch.parse_pe(exe)
        img = bytearray(exe)
        for va, blob in code.items():
            if 0x401000 <= va < 0x7B6000:
                o = exe_patch.va_to_offset(pe, va)
                img[o:o + len(blob)] = blob
        cave = {}
        for va, blob in code.items():
            if not 0x401000 <= va < 0x7B6000:
                cave.update((va + n, c) for n, c in enumerate(blob))
        out = set()
        subs = getattr(region, 'subs', ())
        for lo, hi in region.ranges:
            if lo in cave:
                buf = bytes(cave[a] for a in range(lo, hi))
            else:
                o = exe_patch.va_to_offset(pe, lo)
                buf = bytes(img[o:o + hi - lo])
            for i in md.disasm(buf, lo):
                if i.mnemonic == 'call':
                    t = int(i.op_str, 16)
                    if not any(a <= t < b for a, b in subs):
                        out.add(t)
        _CALLS[key] = out
    return _CALLS[key]


def _acc(rnd):
    return rnd.choice((0xFF, 0xFF, 0x05, 0x19, 0x1B, rnd.randrange(0x20)))


def test_two_accessories_battle_status_matches_x86():
    region = _region('ta_bstat')
    ebp = 0x80000
    for seed in _seeds(12):
        rnd = random.Random(seed)
        g = _with_text(bytearray(0x100_0000))
        rec, sts, chr_ = 0x500000, 0x510000, 0x520000
        g[rec:rec + 0x60] = rnd.randbytes(0x60)
        g[sts:sts + 0x10] = rnd.randbytes(0x10)
        g[chr_:chr_ + 0x84] = rnd.randbytes(0x84)
        g[chr_ + 0x1B], g[chr_ + 0x1E] = _acc(rnd), _acc(rnd)
        for off, v in ((-0x24, rec), (-0x3C, sts), (-0x30, chr_),
                       (-8, rnd.randrange(3))):
            struct.pack_into('<I', g, ebp + off, v)
        regs = dict(eax=sts, ecx=0x1100 | g[sts + 5], edx=rec,
                    ebx=0x77, esp=ebp - 0x60, ebp=ebp, esi=1, edi=2)
        x86, arm, log = _run_region(region, 0, g, P.TWO_ACCESSORIES_CODE,
                                    regs)
        assert [a[:2] for _f, a in log] == [
            (g[ebp - 8], g[chr_ + 0x1E]), (g[ebp - 8], g[chr_ + 0x1B])]
        # the resume point's store goes through x0
        assert arm.reg('x0') == GUEST + x86.reg('eax') + 0x52


def test_two_accessories_stats_match_x86():
    stats, curse = _region('ta_stats'), _region('ta_curse')
    ebp = 0x80000
    seen = set()
    for seed in _seeds(12):
        rnd = random.Random(seed)
        g = _with_text(bytearray(0x100_0000))
        idx = rnd.randrange(9)
        rec = 0xDBFD8C + idx * 0x84
        g[rec:rec + 0x84] = rnd.randbytes(0x84)
        g[rec + 0x1B], g[rec + 0x1E] = _acc(rnd), _acc(rnd)
        if seed % 4 == 0:
            g[rec + 0x1B] = g[rec + 0x1E] = 0xFF
        g[0x530000:0x531000] = rnd.randbytes(0x1000)
        g[ebp - 0x40:ebp] = rnd.randbytes(0x40)
        struct.pack_into('<I', g, ebp - 0x28, idx)
        struct.pack_into('<I', g, ebp + 8, rnd.randrange(3))
        regs = dict(eax=1, ecx=2, edx=3, ebx=0x77, esp=ebp - 0x40,
                    ebp=ebp, esi=1, edi=2)
        _x86, _arm, log = _run_region(
            stats, 0, g, P.TWO_ACCESSORIES_CODE, regs,
            ret=lambda fn, a: 0x530000 + (a[0] & 0xFF) * 0x10
            if fn == 0x6CB97E else 0)
        seen.add(sum(fn == 0x6CB97E for fn, _a in log))
        struct.pack_into('<I', g, ebp - 0x2C, rec + 0x1E)
        _run_region(curse, 0, g, P.TWO_ACCESSORIES_CODE, regs)
    assert seen == {0, 1, 2}          # no accessory, one, both


def test_two_accessories_cats_bell_matches_x86():
    bell = _region('ta_bell')
    ebp = 0x80000
    seen = set()
    for seed in _seeds(16):
        rnd = random.Random(seed)
        g = _with_text(bytearray(0x100_0000))
        g[0xDC0230:0xDC0233] = bytes(rnd.choice((0xFF, rnd.randrange(9)))
                                     for _ in range(3))
        for c in range(9):
            struct.pack_into('<I', g, 0x919928 + 4 * c, rnd.randrange(9))
        for c in range(9):
            g[0xDBFDAA + c * 0x84] = _acc(rnd)
            g[0xDBFDA7 + c * 0x84] = _acc(rnd)
        for k, at in ((0, None), (1, rnd.randrange(3))):
            if at is not None:
                struct.pack_into('<I', g, ebp - 4, at)
            regs = dict(eax=5, ecx=6, edx=7, ebx=8, esp=ebp - 4, ebp=ebp,
                        esi=1, edi=2)
            x86, arm, _log = _run_region(bell, k, g,
                                         P.TWO_ACCESSORIES_CODE, regs)
            seen.add((x86.reg('eip'), x86.reg('eax') & 0xFF))
            if x86.reg('eip') == 0x6CC59E:
                assert arm.reg('x0') == x86.reg('ebp')
    # the loop's end, and one bell and two bells pushed
    assert {(0x6CC511, 3), (0x6CC511, 6)} <= seen and 0x6CC59E in {
        e for e, _n in seen}


def test_two_accessories_unequip_matches_x86():
    clear = _region('ta_clear')
    ebp = 0x80000
    for seed in _seeds(12):
        rnd = random.Random(seed)
        g = _with_text(bytearray(0x100_0000))
        idx = rnd.randrange(9)
        g[0xDBFDA7 + idx * 0x84] = _acc(rnd)
        g[0xDBFDAA + idx * 0x84] = _acc(rnd)
        struct.pack_into('<I', g, ebp + 8, idx)
        regs = dict(eax=5, ecx=6, edx=7, ebx=8, esp=ebp - 8, ebp=ebp,
                    esi=1, edi=2)
        _x86, _arm, log = _run_region(clear, 0, g, P.TWO_ACCESSORIES_CODE,
                                      regs)
        assert len(log) == sum(g[a + idx * 0x84] != 0xFF
                               for a in (0xDBFDA7, 0xDBFDAA))


# Screens: a whole menu function, the mod's x86 against the port's function
# with the plan's words; every call faked on both sides (the port's native
# text drawing as the x86 6F5B03 it replaces), and the calls compared.
DRAW = 0x6F5B03
CTX_PTR = 0x12CE2B0                     # the port's pointer to its context
X86_RET = 0x100


def _ta_words(names, esui=False):
    exe, m = _tools()
    hooks = [r for r in S.TWO_ACCESSORIES if r.name in names]
    return _region_words(hooks) + [(arm, stock, new, 'word')
                                   for _x, arm, stock, new in
                                   P._ta_words(esui)]


def _ta_code(esui):
    return {**P.TWO_ACCESSORIES_CODE, **(P._TA_ESUI if esui else {})}


_ENTRIES = {}


def _arm_entries():
    """{ARM entry: x86 function} of every translated function"""
    if not _ENTRIES:
        _exe, m = _tools()
        for x in m.x86_keys:
            try:
                _ENTRIES[m.extent(x)[0]] = x
            except Exception:                       # pragma: no cover
                pass
    return _ENTRIES


# the port's own calls where the x86 makes others: its input check for the
# game's (same arguments), and a function it has inlined
PORT_ALIAS = {0x41B099: 0x6F53F1}
PORT_INLINED = {0x6C9808, 0x6D0AE8, 0x6D0ADE, 0x6D0AE3}
# the port's native routines, as the x86 function each stands for
PORT_NATIVE = {0x10E4B00: 0x6F54A2,             # string width
               0x10E4D20: 0x6F54A2,             # (another of them)
               0x10E3C90: 0x6F564E}             # a coloured string


def _screen(x86_fn, words, g, code, args, native=0, ret=None, esp=0x80000,
            full=False, watch=()):
    """(x86 calls, ARM calls): each [(x86 function, 5 stack words)]"""
    exe, m = _tools()
    ret = ret or (lambda fn, a: 0x530000 + (a[0] & 0xFF) * 0x10)
    g = _with_text(bytearray(g))
    for va, blob in code.items():
        g[va:va + len(blob)] = blob
    fs, fe = m.containing(x86_fn)
    struct.pack_into('<%dI' % (1 + len(args)), g, esp, X86_RET, *args)
    x86 = X86(g, code)
    x86.slots = set()       # where its calls wrote their return addresses
    log86 = []
    # a mod's cave outside .text runs as part of the function
    caves = [(va, va + len(b)) for va, b in code.items()
             if not 0x401000 <= va < 0x7B6000]

    def hook86(uc, address, size, _u):
        if not fs <= address < fe and address != X86_RET and \
                not any(a <= address < b for a, b in caves):
            sp = uc.reg_read(UX.UC_X86_REG_ESP)
            a = struct.unpack('<5I', uc.mem_read(sp + 4, 20))
            log86.append((address, a))
            x86.slots.add(sp)
            uc.reg_write(UX.UC_X86_REG_EAX, ret(address, a))
            back = struct.unpack('<I', uc.mem_read(sp, 4))[0]
            uc.reg_write(UX.UC_X86_REG_ESP, sp + 4)
            uc.reg_write(UX.UC_X86_REG_EIP, back)
    x86.mu.hook_add(U.UC_HOOK_CODE, hook86)
    x86.run(fs, X86_RET, esp=esp, ebp=0x12345)
    assert x86.reg('eip') == X86_RET
    # the ARM side
    arm = Arm(words, g)
    lo, hi = m.extent(fs)
    blobs = [P.own_region(m, exe, b) for b in range(len(P.BLOBS))]
    entry = _arm_entries()
    logarm = []

    def args(a):
        return struct.unpack('<5I', a.mu.mem_read(GUEST + a.ctx(4) + 4, 20))

    for fn in P.tramp_all().values():
        def fake(a, fn=fn):
            v = args(a)
            logarm.append((fn, v))
            return ret(fn, v)
        arm.fake[fn] = fake

    def back(uc, sp_pop=True):
        if sp_pop:
            arm.ctx(4, arm.ctx(4) + 4)
        uc.reg_write(UA.UC_ARM64_REG_PC, uc.reg_read(UA.UC_ARM64_REG_X30))

    arm.hit = set()
    watch = set(watch)

    def hooka(uc, address, size, _u):
        if address in watch:
            arm.hit.add(address)
        if lo <= address < hi or address >= STUB or address in arm.tramp \
                or any(b0 <= address < b1 for b0, b1 in blobs):
            return
        if address in S.NATIVE and address != 0x10E2720:
            logarm.append((DRAW, args(arm)))
            back(uc)
        elif address == 0x10E2720:
            uc.reg_write(UA.UC_ARM64_REG_X0, native)
            back(uc, False)
        elif address == 0xA550:
            back(uc, False)
        elif address in entry or address in PORT_NATIVE:
            fn = PORT_NATIVE.get(address) or entry[address]
            v, fn = args(arm), PORT_ALIAS.get(fn, fn)
            logarm.append((fn, v))
            arm.ctx(0, ret(fn, v))
            back(uc)
        else:
            raise AssertionError('ARM +0x%X called' % address)
    arm.mu.hook_add(U.UC_HOOK_CODE, hooka)
    arm.mu.mem_write(CTX_PTR, struct.pack('<Q', CTX))
    arm.ctx(4, esp)
    arm.ctx(5, 0x12345)
    got = arm.run(lo, {SENT_END}, {'x30': SENT_END}, count=3000000)
    assert got == SENT_END
    assert arm.ctx(4) == esp + 4
    log86 = [c for c in log86 if c[0] not in PORT_INLINED]
    if full:
        return log86, logarm, x86, arm
    return log86, logarm


def _same_draws(a):
    """a call log as compared: draws whole, other calls by their first
    argument (the rest is whatever the stack held), in any order (a hook
    draws its rows after the function's own)."""
    return sorted((f, v if f == DRAW else v[:1]) for f, v in a)


def _draw_diff(a, b):
    """what only one side of two call logs has, for a failure message"""
    from collections import Counter
    ca, cb = Counter(_same_draws(a)), Counter(_same_draws(b))
    f = lambda c: ['%X %s' % (fn, ' '.join('%X' % v for v in args))
                   for (fn, args), n in (c).items() for _ in range(n)]
    return 'ARM only: %s; x86 only: %s' % (f(ca - cb), f(cb - ca))


def _ta_record(g, rnd, idx):
    rec = 0xDBFD8C + idx * 0x84
    g[rec:rec + 0x84] = rnd.randbytes(0x84)
    g[rec + 0x1B], g[rec + 0x1E] = _acc(rnd), _acc(rnd)
    return rec


@pytest.mark.parametrize('esui', [False, True])
def test_two_accessories_phs_matches_x86(esui):
    words = _ta_words({'ta_phs' + ('_e' if esui else '')}, esui)
    for seed in _seeds(6):
        rnd = random.Random(seed)
        g = bytearray(0x100_0000)
        idx = rnd.randrange(9)
        struct.pack_into('<I', g, 0xDCA16C, idx)
        _ta_record(g, rnd, idx)
        x, y = 0x10 + rnd.randrange(0x40), 0x115
        log86, logarm = _screen(0x700090, words, g, _ta_code(esui),
                                (x, y))
        assert _same_draws(logarm) == _same_draws(log86)
        # native text: the port's own placement of the third label (x, not
        # x + 0x1B), the fourth with it
        _l, logn = _screen(0x700090, words, g, _ta_code(esui),
                           (x, y), native=1)
        moved = [(f, a) for f, a in log86 if f == DRAW and a[2] in (
            0x91F44A, 0x91F44E)]
        assert len(moved) == 2
        want = [(f, (a[0] - 0x1B,) + a[1:]) if (f, a) in moved else (f, a)
                for f, a in log86]
        assert _same_draws(logn) == _same_draws(want)


@pytest.mark.parametrize('esui', [False, True])
def test_two_accessories_status_matches_x86(esui):
    words = _ta_words({'ta_status' + ('_e' if esui else '')}, esui)
    for seed in _seeds(6):
        rnd = random.Random(seed)
        g = bytearray(0x100_0000)
        idx, k = rnd.randrange(9), rnd.randrange(3)
        struct.pack_into('<I', g, 0xDCA478, 0x40)
        g[0x40 + 0xDC0230] = k
        struct.pack_into('<I', g, 0x919928 + 4 * k, idx)
        _ta_record(g, rnd, idx)
        x, y = 0x135, 0x119
        log86, logarm = _screen(0x704612, words, g, _ta_code(esui),
                                (x, y))
        assert _same_draws(logarm) == _same_draws(log86)
        _l, logn = _screen(0x704612, words, g, _ta_code(esui),
                           (x, y), native=1)
        moved = [(f, a) for f, a in log86 if f == DRAW and a[2] in (
            0x920933, 0x920937)]
        assert len(moved) == 2
        want = [(f, ((a[0] - 0x10) & 0xFFFFFFFF,) + a[1:]) if (f, a) in moved
                else (f, a) for f, a in log86]
        assert _same_draws(logn) == _same_draws(want)


_EQUIP = ('ta_rows', 'ta_cursor', 'ta_equip_acc2', 'ta_eqdesc', 'ta_equip',
          'ta_remove')


@pytest.mark.parametrize('mode', [0, 1])
@pytest.mark.parametrize('slot', [0, 1, 2, 3])
def test_two_accessories_equip_menu_matches_x86(mode, slot):
    """The equip menu (x86 0x705D16), each slot, idle and with each button:
    its calls (draws included) and the character's equipment after."""
    words = _ta_words(set(_EQUIP))
    for seed, button in [(k, b) for k, b in enumerate(
            (0, 0x10, 0x20, 0x40, 0x80)) if k in _seeds(5)]:
        rnd = random.Random(seed)
        g = bytearray(0x100_0000)
        g[0xDC0000:0xDD0000] = rnd.randbytes(0x10000)
        idx, k = rnd.randrange(9), rnd.randrange(3)
        struct.pack_into('<I', g, 0xDCA4A4, k)
        g[k + 0xDC0230] = k
        struct.pack_into('<I', g, 0x919928 + 4 * k, idx)
        rec = _ta_record(g, rnd, idx)
        struct.pack_into('<I', g, 0xDCA5C4, slot)
        struct.pack_into('<I', g, 0xDCA6A0, mode)
        struct.pack_into('<I', g, 0xDCA4A0, 0x540000)
        for va in (0xDCA5FC, 0xDCA60C, 0xDCA61C):
            struct.pack_into('<I', g, va, rnd.randrange(4))
        g[0xDC0B4B] &= 0xFE
        # 0x6C9808 (inlined by the port): the menu is not closing
        struct.pack_into('<I', g, 0xDC1294, 0)

        def ret(fn, a):
            if fn == 0x6C6B1B:
                return 0x530000 + (a[0] & 0xFF) * 0x10
            if fn == 0x6F53F1:
                return int(a[0] == button)
            return 0
        log86, logarm, x86, arm = _screen(
            0x705D16, words, g, P.TWO_ACCESSORIES_CODE, (0,), ret=ret,
            full=True)
        # where the port's own menu already differs from the game's (its
        # input handling), the mod must leave it differing the same way
        s86, sarm, sx86, sa = _screen(0x705D16, [], g, {}, (0,), ret=ret,
                                      full=True)
        assert _draw_diff(logarm, log86) == _draw_diff(sarm, s86), (
            seed, slot, button, _draw_diff(logarm, log86))
        if sa.guest(rec, 0x84) == sx86.guest(rec, 0x84):
            assert arm.guest(rec, 0x84) == x86.guest(rec, 0x84), (seed, slot)


# ---------------------------------------------------------- Limit Overhaul
def _lo_guest(rnd):
    """Battle state for LimitOverhaul.txt's cave: the command chosen, the
    acting slot and character, the limit level, learned limits and gauge,
    and a frame whose locals are small numbers or pointers."""
    g = _with_text(bytearray(0x100_0000))
    g[0x990000:0x9B0000] = rnd.randbytes(0x20000)
    g[0xDB0000:0xDD0000] = rnd.randbytes(0x20000)
    for va in range(0xDC2000, 0xDC3C00, 4):     # menu cursors, counts
        struct.pack_into('<I', g, va, rnd.randrange(4))
    struct.pack_into('<I', g, 0xDC35AC, 0)
    struct.pack_into('<H', g, 0x91EF9C, rnd.choice((1, 4, rnd.randrange(8))))
    g[0xCC0D8A] = rnd.choice((0x9D, 0x9D, rnd.randrange(0x100)))
    # the battle's after-battle record (x86 0x5C930F): its slot, its count
    struct.pack_into('<I', g, 0x99CE0C, 0x7E000)
    struct.pack_into('<I', g, 0x7E000, rnd.randrange(3))
    struct.pack_into('<I', g, 0x7E010, rnd.choice((0, 0x20, 0x70)))
    struct.pack_into('<I', g, 0xDC3C70, rnd.choice(
        (0x1E, 0x1E, 0x14, 0x13, rnd.randrange(0x30))))
    slot = rnd.randrange(3)
    struct.pack_into('<I', g, 0xDC3C7C, slot)
    for k in range(3):
        g[0xDC0230 + k] = rnd.randrange(9)
    who = g[0xDC0230 + slot]
    g[0xDBFD8C + who * 0x84 + 0xE] = rnd.randrange(1, 5)       # level
    g[0x9A8DC0 + slot * 0x34] = rnd.choice((0xFF, 0, rnd.randrange(0x100)))
    for va in range(0x7F000, 0x81000, 4):
        struct.pack_into('<I', g, va, rnd.choice(
            (rnd.randrange(8), rnd.randrange(3), 0x100000 +
             rnd.randrange(0x400000))))
    return g


def _lo_ret(fn, a):
    return (a[0] + a[1]) & 0x3F


@pytest.mark.parametrize('name', ['lo_sound', 'lo_list', 'lo_pick',
                                  'lo_command', 'lo_name', 'lo_show',
                                  'lo_use', 'lo_attack'])
def test_limit_overhaul_regions_match_x86(name):
    """Each region from each entry, against the mod's own x86 (its cave
    included), on random battle state: the same exit, calls, memory and
    registers; and every exit is taken."""
    region = _region(name)
    code = P.LIMIT_OVERHAUL_CODE
    scratch = {0x6DA49C: [-0x40]} if name == 'lo_pick' else {}
    seen, runs = set(), 0
    for seed in _seeds(600):
        rnd = random.Random(seed)
        k = seed % len(region.entries)
        g = _lo_guest(rnd)
        regs = {r: rnd.choice((rnd.randrange(8), rnd.randrange(0x100),
                               0x1E))
                for r in ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi')}
        regs.update(esp=0x7FE00, ebp=0x80000)
        try:
            x86, _arm, _log = _run_region(region, k, g, code, regs,
                                          ret=_lo_ret, scratch=scratch)
        except X86Fault:
            continue                # a random pointer the x86 faults on
        seen.add(x86.reg('eip'))
        runs += 1
        if runs >= 60 and seen == set(region.exits):
            break
    assert seen == set(region.exits), (name, sorted(map(hex, seen)))
    assert runs >= 60, runs


def _lo_words():
    regions = S.LIMIT_OVERHAUL
    return _region_words(regions) + [(arm, stock, new, 'word') for
                                     _x, arm, stock, new in P._LO_WORDS]


def _mem_diff(a, b, spans):
    out = []
    for lo, hi in spans:
        x, y = a.guest(lo, hi - lo), b.guest(lo, hi - lo)
        out += [lo + i for i in range(hi - lo) if x[i] != y[i]]
    return out


# each patched function, and what its state must be for the patch to run:
# (battle menu state, setup(g, rnd, seed))
def _lo_cursor0(g, rnd, seed):
    if seed % 3:
        struct.pack_into('<I', g, 0xDC2180 + g[0xDC3C7C] * 0x700 + 0x30, 0)


def _lo_limit_menu(g, rnd, seed):
    if seed % 3:
        g[0xDC206C] = 2
    if seed % 3 == 1:               # not the limit command: the stock path
        struct.pack_into('<I', g, 0xDC3C70, 0x13)


def _lo_cmd_menu(g, rnd, seed):
    # x86 0x6D0AE8 (inlined by the port) reads these: no window is open
    g[0xDC2068:0xDC2088] = bytes(0x20)


_LO_FNS = {0x5CB2CC: (None, None), 0x6E09BB: (None, None),
           0x6DA3A8: (4, _lo_cursor0), 0x6D91FA: (1, None),
           0x6DE3DB: (None, _lo_cmd_menu), 0x6D7245: (None, _lo_limit_menu),
           0x6D86D2: (None, None), 0x4379BE: (None, None),
           0x4346DF: (None, None), 0x5C930F: (None, None),
           0x434DF3: (None, None)}
_LO_MEM = ((0x990000, 0x9B0000), (0xDB0000, 0xDD0000))
_BUTTONS = (0x20, 0x40, 0x10, 0x80, 0x1, 0x2, 0x4, 0x8, 0x100, 0x200, 0x400,
            0x800, 0x1000, 0x2000, 0x4000, 0x8000)


def _frame(fn):
    """the guest span of an x86 function's frame (locals, saved ebp,
    return address, arguments) as _screen runs it: not the pushes and
    return slots below it, which the port leaves as they were"""
    exe, _m = _tools()
    pe = exe_patch.parse_pe(exe)
    o = exe_patch.va_to_offset(pe, fn)
    b = exe[o:o + 9]
    assert b[:3] == bytes.fromhex('558BEC'), hex(fn)
    n = b[5] if b[3:5] == bytes.fromhex('83EC') else (
        struct.unpack_from('<I', b, 5)[0] if b[3:5] == bytes.fromhex('81EC')
        else 0)
    return (0x80000 - 4 - n, 0x80000 + 0x10)


@pytest.mark.parametrize('fn', sorted(_LO_FNS))
def test_limit_overhaul_functions_match_x86(fn):
    """Each patched function whole, the port's with the blobs against the
    mod's x86, on battle states that reach its patches (each is reached):
    the same calls (draws whole) and battle memory and frame, where the
    port's own function matches the game's on that state."""
    exe, m = _tools()
    words = _lo_words()
    code = P.LIMIT_OVERHAUL_CODE
    state, setup = _LO_FNS[fn]
    hooks = {a for r in S.LIMIT_OVERHAUL for x, a, _w, _s in r.sites(m)
             if m.containing(x)[0] == fn}
    hooks |= {a for x, a, _s, _n in P._LO_WORDS if m.containing(x)[0] == fn}
    spans = _LO_MEM + (_frame(fn),)
    hit, runs = set(), 0
    part = os.environ.get("SEEDS")   # a part of the seeds: no totals
    for seed in (_seeds(10) if part else
                 range(int(os.environ.get("LO_SEEDS", 10)))):
        rnd = random.Random(seed)
        g = _lo_guest(rnd)
        struct.pack_into('<I', g, 0xDC1294, 0)
        if state is not None:
            struct.pack_into('<H', g, 0x91EF9C, state)
        if setup:
            setup(g, rnd, seed)
        button = 0x20 if seed % 2 else rnd.choice(_BUTTONS)

        def ret(f, a):
            if f == 0x6F53F1:
                return int(a[0] == button)
            return 0 if f == 0x6D0AE8 else _lo_ret(f, a)
        args = (seed % 3, 0, 0)
        s86, sarm, sx, sa = _screen(fn, [], g, {}, args, ret=ret, full=True)
        if _draw_diff(sarm, s86) != _draw_diff([], []) or \
                _mem_diff(sa, sx, spans):
            continue                # the port's own function differs here
        m86, marm, mx, ma = _screen(fn, words, g, code, args, ret=ret,
                                    full=True, watch=hooks)
        hit |= ma.hit
        assert _same_draws(marm) == _same_draws(m86), (
            seed, _draw_diff(marm, m86)[:400])
        # a return slot inside the frame (the mod pops past its pushes on
        # one path): the x86 call writes its return address there, the
        # port's leaves the slot as it was
        slots = {sp + k for sp in mx.slots for k in range(4)}
        bad = [a for a in _mem_diff(ma, mx, spans) if a not in slots]
        assert not bad, (seed, [hex(a) for a in bad][:8])
        runs += 1
    if part:                        # the caller adds the parts up
        print('LO_PART', runs, sorted(map(hex, hit)), sorted(map(hex, hooks)))
        return
    assert runs >= min(6, int(os.environ.get("LO_SEEDS", 10))), runs
    if fn != 0x434DF3:              # a word: no hook to watch
        assert hit == hooks, sorted(map(hex, hooks - hit))


# ---------------------------------------------------------- Enemy Scaling
_ES_PCT = {'10%': (0x0A, 0x0B), '20%': (0x05, 0x06), '30%': (0x0A, 0x0D),
           '50%': (0x02, 0x03), '100%': (0x01, 0x02)}


def _es_guest(rnd):
    """A battle's enemy blocks (0x9A8E9C on) and enemy records, with stats
    from small to past what the multiplier keeps in a byte or word."""
    g = _with_text(bytearray(0x100_0000))
    for va in range(0x9A8000, 0x9AC000):
        g[va] = rnd.choice((rnd.randrange(0x100), rnd.randrange(0x40),
                            0xFF, 3, 0x22))
    for va in range(0x9A8EBC, 0x9A9100, 0xB8):
        if rnd.random() < 0.2:
            g[va] = 0xFF                    # no enemy in this slot
        struct.pack_into('<i', g, va + 0x8A, rnd.choice((
            rnd.randrange(30000), rnd.randrange(1 << 24), -5,
            rnd.randrange(1 << 31))))
    return g


@pytest.mark.parametrize('name', ['es_stats', 'es_level'])
def test_enemy_scaling_regions_match_x86(name):
    """Each region against NoRando's x86 with each multiplier file's bytes
    (and odd ones): the same exit, calls, memory and registers."""
    region = _region(name)
    runs = 0
    for seed in _seeds(int(os.environ.get('ES_SEEDS', 30))):
        rnd = random.Random(seed)
        den, num = rnd.choice(list(_ES_PCT.values()) + [
            (rnd.randrange(1, 0x100), rnd.randrange(1, 0x100))])
        code = dict(P.ENEMY_SCALING_CODE)
        code[0x9A7836] = bytes([den])
        code[0x9A783B] = bytes([num])
        g = _es_guest(rnd)
        regs = {r: rnd.choice((rnd.randrange(0x100), rnd.randrange(1 << 32),
                               0x9A9000 + rnd.randrange(0x1000)))
                for r in ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi')}
        regs.update(esp=0x7FE00, ebp=0x80000)
        host = {}
        if name == 'es_level':
            # x86 0x5C99B5's eax (the enemy's block), held in w8
            regs['eax'] = 0x9A8EBC + rnd.randrange(0x200)
            regs['ecx'] = 0x9AB000 + rnd.randrange(0x100)
            g[regs['ecx'] + 0x40] = rnd.choice((0x22, 0x22, 0x21))
            host = {'x8': regs['eax']}
        try:
            _run_region(region, 0, g, code, regs,
                        ret=lambda fn, a: rnd.randrange(1 << 32),
                        data={'es_den': bytes([den]), 'es_num': bytes([num])},
                        host=host)
        except X86Fault:
            continue                # an x86 divide fault (no PC run either)
        runs += 1
    assert runs >= (20 if not os.environ.get('SEEDS') else 1), runs


def test_enemy_scaling_files_pair():
    """NoRando alone (its own 10%), and with each multiplier file layered
    on: one port, the file's den/num carried to the blob."""
    import ff7nx_pccode

    def scaling(den, num):
        return ('#Denominator\n009A7836 = %02X\n#Numerator\n009A783B = %02X\n'
                % (den, num))
    nr = ''.join('%08X = %s\n' % (va, b.hex(' ').upper())
                 for va, b in P.ENEMY_SCALING_CODE.items()) + scaling(10, 11)
    hit = P.match(_patches(nr))
    assert hit and hit[0].name == 'Enemy Scaling'
    assert hit[1] == {'es_den': b'\x0a', 'es_num': b'\x0b'}
    for pct, (den, num) in _ES_PCT.items():
        sc = scaling(den, num)
        hit = P.match(_patches(nr + '\n' + sc))
        assert hit[1] == {'es_den': bytes([den]), 'es_num': bytes([num])}
        got = ff7nx_pccode.groups([('nr', nr), ('pct', sc)])
        assert len(got) == 1 and got[0][1], pct
    # a code byte rewritten is not the option
    assert P.match(_patches(nr + '\n009170A5 = 8B\n')) is None
    assert P.match(_patches(nr + '\n009A7836 = 00\n')) is None


# --------------------------------------------------------- Solo Challenge
class _Seq(object):
    """call results, the same on both sides: the k-th call of a run gives
    the k-th value (reset before the ARM run). 0x5C8684 (a level's stat)
    gives a small one, as its row index is used."""

    def __init__(self, rnd):
        self.vals = [rnd.randrange(1 << 32) for _ in range(4000)]
        self.k = 0

    def reset(self):
        self.k = 0

    def __call__(self, fn, args):
        v = self.vals[self.k % len(self.vals)]
        self.k += 1
        return v & 0xFF if fn == 0x5C8684 else v


def _sc_guest(rnd):
    """Party (0xDC0230), character records (0xDBFD8C), the battle's records
    (0xDBA544), the stat tables, and a frame."""
    g = _with_text(bytearray(0x100_0000))
    g[0xDB0000:0xDD0000] = rnd.randbytes(0x20000)
    g[0x910000:0x920000] = rnd.randbytes(0x10000)
    g[0x9A8000:0x9B0000] = rnd.randbytes(0x8000)
    for k in range(4):
        g[0xDC0230 + k] = rnd.choice((0xFF, rnd.randrange(11)))
    for k in range(11):
        struct.pack_into('<I', g, 0x919928 + 4 * k, rnd.randrange(9))
    struct.pack_into('<I', g, 0xDD46F8, rnd.randrange(11))
    struct.pack_into('<I', g, 0xDC0DDE, rnd.randrange(1, 0x200))
    for va in range(0x7F000, 0x81000, 4):
        struct.pack_into('<I', g, va, rnd.randrange(16))
    return g


_SC_CODES = {'sc_party': 'SOLO_CODE', 'sc_init': 'SOLO_CODE',
             'sc_init_cait': 'SOLO_CAIT_CODE',
             'sc_init_vincent': 'SOLO_VINCENT_CODE',
             'sc_party_random': 'SOLO_RANDOM_CODE',
             'pr_party': 'PARTY_RANDO_CODE'}


@pytest.mark.parametrize('name', sorted(_SC_CODES))
def test_solo_challenge_regions_match_x86(name):
    """Each region against its file's x86, the forced character any of the
    eleven: the same exit, calls, memory and registers."""
    region = _region(name)
    runs = 0
    for seed in range(int(os.environ.get('SC_SEEDS', 24))):
        rnd = random.Random(seed)
        who = rnd.randrange(11)
        code = dict(getattr(P, _SC_CODES[name]))
        cave = bytearray(code[0x916800])
        data = None
        if region.data:
            struct.pack_into('<I', cave, 0x17, who)
            data = {'sc_char': struct.pack('<I', who)}
        code[0x916800] = bytes(cave)
        g = _sc_guest(rnd)
        regs = {r: rnd.randrange(1 << 32)
                for r in ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi')}
        regs.update(esp=0x7FE00, ebp=0x80000)
        if 'party' in name:  # (pr_party too)
            i = rnd.choice((0, 0, 1, 2, 3))         # the loop's slot
            regs.update(ecx=i, edx=i)
            struct.pack_into('<I', g, 0x80000 - 4, i)
        try:
            _run_region(region, 0, g, code, regs, ret=_Seq(rnd),
                        scratch={0x41B5AB: [-0x40]}, data=data)
        except X86Fault:
            continue
        runs += 1
    assert runs >= 16, runs


def test_solo_challenge_imprison_matches_x86():
    """x86 0x42A29F's call into the cave against the hook: eax, cl and
    0x9A8B47's bit 7."""
    words = _region_words([S.SOLO[1]])
    for seed in _seeds(8):
        rnd = random.Random(seed)
        g = _sc_guest(rnd)
        for va, blob in P.SOLO_CODE.items():    # the mod's bytes, both sides
            g[va:va + len(blob)] = blob
        regs = {r: rnd.randrange(1 << 32) for r in ('eax', 'ecx', 'edx')}
        regs.update(esp=0x7FE00, ebp=0x80000)
        x86 = X86(g, P.SOLO_CODE)
        x86.run(0x42A29F, 0x42A2A5, **regs)
        arm = Arm(words, g)
        for i, r in enumerate(X86_REGS):
            arm.ctx(i, regs.get(r, 0))
        assert arm.run(0xD45B4, {0xD45C8}, {'x20': CTX}) == 0xD45C8
        assert arm.ctx(0) == x86.reg('eax')
        assert arm.ctx(1) == x86.reg('ecx')
        a = bytearray(arm.guest(0, 0x100_0000))
        b = bytearray(x86.guest(0, 0x100_0000))
        b[0x7FDFC:0x7FE00] = a[0x7FDFC:0x7FE00]     # the call's return slot
        assert a == b


def test_solo_challenge_words():
    """The words with no cave, and the ports' files."""
    import capstone
    exe, m = _tools()
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)

    def dis(word, at):
        i = next(md.disasm(struct.pack('<I', word), at))
        return ('%s %s' % (i.mnemonic, i.op_str)).strip()
    want = {0x7F878C: 'mov w8, #0x258', 0x7F8794: 'str w8, [x20]',
            0xE21AA0: 'b #0xe219b4', 0xE1D52C: 'b #0xe1d564',
            0xE1D57C: 'b #0xe1d5b4'}
    for x86, arm, stock, new in (P._SC_WORDS + P._SC_CAIT_WORDS +
                                 P._SC_VINCENT_WORDS):
        assert struct.unpack_from('<I', m.img, arm)[0] == stock
        assert dis(new, arm) == want.get(arm, 'nop')
    # nothing branches into the replaced runs but their own first word
    for lo, hi in ((0x7F878C, 0x7F879C), (0xE21E54, 0xE21E60)):
        for a in range(m.extent(m.containing(lo)[0])[0],
                       m.extent(m.containing(lo)[0])[1], 4):
            i = next(md.disasm(bytes(m.img[a:a + 4]), a), None)
            if i is None or not (i.mnemonic == 'b' or
                                 i.mnemonic.startswith(('b.', 'cb', 'tb'))):
                continue
            t = int(i.op_str.split('#')[-1], 16)
            assert not lo < t < hi, (hex(a), hex(t))
    # each file matches its port, its character carried over
    for name, full, who in (('Solo Challenge', P.SOLO_CODE, 4),
                            ('Solo Challenge (Cloud/Sephiroth)',
                             P.SOLO_CLOUD_CODE, 10),
                            ('Solo Challenge (Cait Sith)', P.SOLO_CAIT_CODE,
                             6),
                            ('Solo Challenge (Vincent)', P.SOLO_VINCENT_CODE,
                             7)):
        cave = bytearray(full[0x916800])
        struct.pack_into('<I', cave, 0x17, who)
        hit = P.match(list({**full, 0x916800: bytes(cave)}.items()))
        assert hit and hit[0].name == name
        assert hit[1] == {'sc_char': struct.pack('<I', who)}
        struct.pack_into('<I', cave, 0x17, 11)
        assert P.match(list({**full, 0x916800: bytes(cave)}.items())) \
            is None
    hit = P.match(list(P.SOLO_RANDOM_CODE.items()))
    assert hit and hit[0].name == 'Solo Challenge (Random)'


# ------------------------------------------------------------- Battle PHS
def _phs_guest(rnd):
    """Battle and menu state with the PHS command (0x1D) chosen or not, the
    party and the characters' records, and a frame of small numbers and
    pointers into the battle's tables."""
    g = _with_text(bytearray(0x100_0000))
    for lo, hi in ((0x990000, 0x9B0000), (0xBE0000, 0xBF3000),
                   (0xDB0000, 0xDE0000)):
        g[lo:hi] = rnd.randbytes(hi - lo)
    struct.pack_into('<I', g, 0xDC3C70, rnd.choice((0x1D, 0x1D, 0x13, 2)))
    struct.pack_into('<I', g, 0x99CE0C, 0x9AB000 + rnd.randrange(0x100))
    struct.pack_into('<I', g, 0x99E308, rnd.randrange(4))
    struct.pack_into('<I', g, 0x99E314, rnd.choice((0x1D, 0x1D, 1)))
    struct.pack_into('<I', g, 0x99E3A0, rnd.randrange(10))
    struct.pack_into('<I', g, 0x91EF9C, rnd.choice((6, 6, 1)))
    struct.pack_into('<H', g, 0xDC08DC, rnd.choice((0x181, 0x200, 0x100)))
    for k in range(4):
        g[0xDC0230 + k] = rnd.choice((0xFF, rnd.randrange(9)))
    for va in range(0x7F000, 0x81000, 4):
        struct.pack_into('<I', g, va, rnd.choice((
            rnd.randrange(8), rnd.randrange(3), 0x1D,
            0xDC2000 + rnd.randrange(0x1000), 0x9A8000 + rnd.randrange(0x2000))))
    return g


_PHS_REGIONS = ['phs_rows', 'phs_list', 'phs_all', 'phs_mp', 'phs_pick',
                'phs_empty', 'phs_id', 'phs_target', 'phs_menu', 'phs_sound',
                'phs_name', 'phs_targets', 'phs_glyph', 'phs_command',
                'phs_swap']


@pytest.mark.parametrize('name', _PHS_REGIONS)
def test_battle_phs_regions_match_x86(name):
    """Each region against BattlePHS.txt's x86 on random battle state: the
    same exit, calls, memory and registers; and every exit is taken."""
    region = _region(name)
    seen, runs = set(), 0
    for seed in range(int(os.environ.get('PHS_SEEDS', 200))):
        rnd = random.Random(seed)
        g = _phs_guest(rnd)
        regs = {r: rnd.choice((rnd.randrange(0x20), 0x1D, 0xFF, 0xFE,
                               0xDC2000 + rnd.randrange(0x1000)))
                for r in ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi')}
        regs.update(esp=0x7FE00, ebp=0x80000)
        try:
            x86, _a, _l = _run_region(region, 0, g, P.BATTLE_PHS_CODE, regs,
                                      ret=_Seq(rnd))
        except X86Fault:
            continue
        seen.add(x86.reg('eip'))
        runs += 1
        if runs >= 30 and seen == set(region.exits):
            break
    assert seen == set(region.exits), (name, sorted(map(hex, seen)))
    assert runs >= 30, runs


def test_battle_phs_or_matches_x86():
    """x86 0x5CF873 `or [eax], edx` (the mod's) against the word stub."""
    words = _region_words([[r for r in S.BATTLE_PHS if r.name == 'phs_or'][0]])
    for seed in _seeds(6):
        rnd = random.Random(seed)
        g = _phs_guest(rnd)
        for va, blob in P.BATTLE_PHS_CODE.items():
            g[va:va + len(blob)] = blob
        rec = 0xDC2000 + 4 * rnd.randrange(0x100)
        struct.pack_into('<I', g, 0x80000 - 0x24, rec)
        regs = {'eax': 0, 'ecx': 0, 'edx': rnd.randrange(1 << 32),
                'esp': 0x7FE00, 'ebp': 0x80000}
        x86 = X86(g, {})
        x86.run(0x5CF870, 0x5CF875, **regs)
        arm = Arm(words, g)
        for i, r in enumerate(X86_REGS):
            arm.ctx(i, regs.get(r, 0))
        assert arm.run(0x825750, {0x825770}, {'x23': CTX}) == 0x825770
        assert arm.ctx(0) == x86.reg('eax')
        assert arm.guest(rec, 4) == x86.guest(rec, 4)


def test_battle_phs_words_and_files():
    """The words with no cave; the file alone and with Limit Overhaul's."""
    import capstone
    import ff7nx_pccode
    exe, m = _tools()
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    want = {0xDBA78C: 'b #0xdba7c8', 0xDBAAFC: 'b #0xdbab38'}
    for x86, arm, stock, new in P._PHS_WORDS:
        assert struct.unpack_from('<I', m.img, arm)[0] == stock
        i = next(md.disasm(struct.pack('<I', new), arm))
        assert ('%s %s' % (i.mnemonic, i.op_str)).strip() == \
            want.get(arm, 'nop')
    for x86, arm, stock, _e in P._PHS_SITES + [P._PHS_COMMAND]:
        assert struct.unpack_from('<I', m.img, arm)[0] == stock

    def text(code):
        return ''.join('%08X = %s\n' % (va, b.hex(' ').upper())
                       for va, b in code.items())
    phs, lo = text(P.BATTLE_PHS_CODE), text(P.LIMIT_OVERHAUL_CODE)
    assert P.match(_patches(phs))[0].name == 'Battle PHS'
    assert P.match(_patches(lo + phs))[0].name == \
        'Limit Overhaul + Battle PHS'
    for words in (ff7nx_pccode.translate(phs),
                  ff7nx_pccode.groups([('lo', lo), ('phs', phs)])[0][1]):
        assert words
        at = [w[0] for w in words]
        assert len(at) == len(set(at))          # one word per offset


# -------------------------------------------------------- Item Randomizer
class _IrRet(_Seq):
    """_Seq, but x86 0x6C6B06 (a materia's record) gives a pointer into a
    table of empty (0xFF) and used entries, by the id asked for"""

    def __call__(self, fn, args):
        if fn == 0x6C6B06:
            return 0xC40000 + (args[0] & 0xFFF)
        return _Seq.__call__(self, fn, args)


def _ir_guest(rnd, name):
    """Field script and its pointers (0xCBF5E8, 0xCC0964, 0xCC0CF8), the
    rando's seeds and state (0xDC0E08.., 0xDC0E25), the price and shop
    tables, materia records, and a message for the text scan."""
    g = _with_text(bytearray(0x100_0000))
    for lo, hi in ((0x910000, 0x930000), (0x9A8000, 0x9B0000),
                   (0xCB0000, 0xCD0000), (0xDB0000, 0xDD0000)):
        g[lo:hi] = rnd.randbytes(hi - lo)
    g[0x9A7000:0x9B4200] = bytes(0x9B4200 - 0x9A7000)
    script = bytearray(rnd.randbytes(0x20000))
    for _k in range(0x800):
        script[rnd.randrange(len(script))] = rnd.choice((0x58, 0x5B, 0x55))
    g[0xC00000:0xC20000] = script
    struct.pack_into('<I', g, 0xCBF5E8, 0xC00000)
    struct.pack_into('<I', g, 0xCBF9D8, 0xC30000 + rnd.randrange(0x100))
    g[0xC40000:0xC41000] = bytes(rnd.choice((0xFF, 0xFF, rnd.randrange(255)))
                                 for _k in range(0x1000))
    g[0xDC0E25] = rnd.choice((0, 1, 2, 3, 0xFF))
    struct.pack_into('<H', g, 0xCC15D0, rnd.choice((0x145, 0x40)))
    text = bytearray(rnd.choice((0x21, 0x41, 0x45)) for _k in range(40))
    if rnd.randrange(3):
        at = rnd.randrange(30)
        text[at:at + 7] = P._IR_TEXT[:7]
    g[0xC80000:0xC80029] = bytes(text) + b'\xFF'
    for va in range(0x7F000, 0x81000, 4):
        struct.pack_into('<I', g, va, rnd.choice((
            rnd.randrange(0x200), rnd.randrange(4), 0xFFFF,
            0xC00000 + rnd.randrange(0x1000))))
    return g


_IR_REGIONS = ['ir_init', 'ir_item', 'ir_materia', 'ir_message', 'ir_shop',
               'ir_steal', 'ir_morph', 'ir_drop']


@pytest.mark.parametrize('name', _IR_REGIONS)
def test_item_rando_regions_match_x86(name):
    """Each region against ItemRando.txt's x86 (with its message text, or
    SACRandoCompat's) on random field / battle state: the same exit, calls,
    memory and registers; every exit but ir_drop's stock one is taken."""
    region = _region(name)
    want = set(region.exits) - {0x5C7D51}
    seen, runs = set(), 0
    for seed in range(int(os.environ.get('IR_SEEDS', 60))):
        rnd = random.Random(seed)
        g = _ir_guest(rnd, name)
        text = P._ir_text(rnd.choice((P._IR_TEXT, b'\x42\x54\x41\x49\x4E\x45\x44')))
        code = {**P.ITEM_RANDO_CODE, 0x916FF8: text}
        regs = {r: rnd.choice((rnd.randrange(0x200), 0xFFFF, 0x55,
                               rnd.randrange(1 << 32)))
                for r in ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi')}
        regs.update(esp=0x7FE00, ebp=0x80000)
        host = None
        if name == 'ir_message':
            regs.update(eax=rnd.randrange(8), edx=0xC80000)
            host = {'x8': regs['eax']}
        if name == 'ir_shop':
            regs['eax'] = rnd.randrange(0x1000)
        try:
            x86, _a, _l = _run_region(region, 0, g, code, regs,
                                      ret=_IrRet(rnd), host=host,
                                      data={'ir_text': text})
        except X86Fault:
            continue
        seen.add(x86.reg('eip'))
        runs += 1
        if runs >= 20 and seen == want:
            break
    assert seen == want, (name, sorted(map(hex, seen)))
    assert runs >= 20, runs


def test_item_rando_files():
    """The file alone and with SACRandoCompat's text; the shop words."""
    import ff7nx_pccode
    full = {**P.ITEM_RANDO_CODE, 0x916FF8: P._IR_TEXT}
    hit = P.match(list(full.items()))
    assert hit and hit[0].name == 'Item Randomizer'
    assert hit[1] == {'ir_text': P._IR_TEXT}
    sac = list(full.items()) + [(0x916FF8, bytes.fromhex('42544149 4E4544'))]
    hit = P.match(sac)
    assert hit and hit[1] == {'ir_text': bytes.fromhex('42544149 4E4544FF')}
    assert P.match(list({**full, 0x9A73F5: b'\x67'}.items())) is None
    def hext(d):
        return '\n'.join('%08X = %s' % (va, b.hex(' ').upper())
                         for va, b in d.items()) + '\n'
    got = ff7nx_pccode.groups([('ir', hext(full)), ('sac', hext(
        {0x916FF8: bytes.fromhex('42544149 4E4544')}))])
    assert len(got) == 1 and got[0][1]
    _exe, m = _tools()
    port = hit[0]
    for x86, off, stock, _new, _n in [(w[0], w[1], w[2], w[3], 0)
                                      for w in port.inline({})]:
        assert struct.unpack_from('<I', m.img, off)[0] == stock


# ------------------------------------------------------- Enemy Randomizer
def _br_guest(rnd):
    """_es_guest's battle, plus the formation (0xCC0D8A: often one of the
    swap table's bosses), the seeds and boss state (0xDC0E08..0xDC0E0F),
    an encounter table and a frame."""
    g = _es_guest(rnd)
    g[0xDB0000:0xDD0000] = rnd.randbytes(0x20000)
    g[0xC00000:0xC10000] = rnd.randbytes(0x10000)
    table = P.BATTLE_RANDO_CODE[0x767BB0]
    boss = struct.unpack_from('<H', table, 2 * rnd.randrange(43))[0] & 0x3FF
    struct.pack_into('<H', g, 0xCC0D8A, rnd.choice((boss, boss,
                                                    rnd.randrange(0x400))))
    struct.pack_into('<H', g, 0xDC0E0E, rnd.choice((0, 0, rnd.randrange(
        1 << 16))))
    for va in range(0x7F000, 0x81000, 4):
        struct.pack_into('<I', g, va, rnd.choice((
            rnd.randrange(0x400), rnd.randrange(4), 0xFFFFFFFF,
            0xC00000 + rnd.randrange(0x1000))))
    return g


class _BrRet(_Seq):
    """_Seq, with the game's random numbers (0x5C8BF2, 0x5C8BA1, rand())
    in their real range"""

    def __call__(self, fn, args):
        v = _Seq.__call__(self, fn, args)
        return v & 0x7FFF if fn in (0x5C8BF2, 0x5C8BA1, 0x753BE8) else v


_BR_REGIONS = ['br_boss', 'br_field', 'br_world', 'br_stats', 'br_level',
               'br_stats_es', 'br_level_es']


@pytest.mark.parametrize('name', _BR_REGIONS)
def test_enemy_rando_regions_match_x86(name):
    """Each region against BossAndEncounterRando.txt's x86 (with
    RandoScaler's and a multiplier's bytes for the _es ones): the same
    exit, calls, memory and registers."""
    region = _region(name)
    runs = 0
    for seed in range(int(os.environ.get('BR_SEEDS', 30))):
        rnd = random.Random(seed)
        code = dict(P.BATTLE_RANDO_CODE)
        data = None
        if name.endswith('_es'):
            den, num = rnd.choice(list(_ES_PCT.values()) + [
                (rnd.randrange(1, 0x100), rnd.randrange(1, 0x100))])
            code = {**code, **P.RANDO_SCALER_CODE,
                    0x9A7836: bytes([den]), 0x9A783B: bytes([num])}
            data = {'es_den': bytes([den]), 'es_num': bytes([num])}
        g = _br_guest(rnd)
        regs = {r: rnd.choice((rnd.randrange(0x100), rnd.randrange(1 << 32),
                               0x9A9000 + rnd.randrange(0x1000)))
                for r in ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi')}
        regs.update(esp=0x7FE00, ebp=0x80000)
        host = {}
        scratch = {}
        if name.startswith('br_level'):
            regs['eax'] = 0x9A8EBC + rnd.randrange(0x200)
            regs['ecx'] = 0x9AB000 + rnd.randrange(0x100)
            g[regs['ecx'] + 0x40] = rnd.choice((0x22, 0x22, 0x21))
            host = {'x8': regs['eax']}
        if name == 'br_field':
            regs.update(ecx=0xC00000 + rnd.randrange(0x1000),
                        eax=rnd.randrange(0x100))
        if name == 'br_boss':
            scratch = {0x40826E: [-0x40, -0x3F]}
        try:
            _run_region(region, 0, g, code, regs, ret=_BrRet(rnd),
                        data=data, host=host, scratch=scratch)
        except X86Fault:
            continue
        runs += 1
    # (a run the x86 faults on, a divide past 32 bits, has no PC run)
    assert runs >= int(os.environ.get('BR_SEEDS', 30)) // 3, runs


def test_enemy_rando_files():
    """The file alone, and with RandoScaler and each multiplier file."""
    import ff7nx_pccode

    def hext(d):
        return '\n'.join('%08X = %s' % (va, b.hex(' ').upper())
                         for va, b in d.items()) + '\n'
    hit = P.match(list(P.BATTLE_RANDO_CODE.items()))
    assert hit and hit[0].name == 'Enemy Randomizer'
    for pct, (den, num) in _ES_PCT.items():
        files = [('br', hext(P.BATTLE_RANDO_CODE)),
                 ('rs', hext(P.RANDO_SCALER_CODE)),
                 ('pct', hext({0x9A7836: bytes([den]),
                               0x9A783B: bytes([num])}))]
        got = ff7nx_pccode.groups(files)
        assert len(got) == 1 and len(got[0][0]) == 3 and got[0][1], pct
        hit = P.match([p for _k, t in files
                       for p in __import__('exe_patch').parse_hext(t)])
        assert hit[0].name == 'Enemy Randomizer + Enemy Scaling'
        assert hit[1] == {'es_den': bytes([den]), 'es_num': bytes([num])}
    # with Item Randomizer: the shared new-game hook, the same entry
    ir = [p for p in P.PORTS if p.name == 'Item Randomizer'][0]
    br = [p for p in P.PORTS if p.name == 'Enemy Randomizer'][0]
    assert [s for s in ir.sites if s[0] == 0x722288] == \
        [s for s in br.sites if s[0] == 0x722288]


# ----------------------------------------------------------- Double Bosses
_DB_REGIONS = ['db_boss', 'db_start', 'db_second', 'db_check', 'db_end']


@pytest.mark.parametrize('name', _DB_REGIONS)
def test_double_bosses_regions_match_x86(name):
    """Each region against BossAndEncounterRando.txt with DoubleBosses.txt
    over it: the same exit, calls, memory and registers; every exit taken."""
    region = _region(name)
    code = {**P.BATTLE_RANDO_CODE, **P.DOUBLE_BOSSES_CODE}
    seen, runs = set(), 0
    for seed in range(int(os.environ.get('DB_SEEDS', 30))):
        rnd = random.Random(seed)
        g = _br_guest(rnd)
        g[0xBF0000:0xBF3000] = rnd.randbytes(0x3000)
        struct.pack_into('<H', g, 0x9A8752, rnd.choice((0xFFFF, 0xFFFF,
                                                        rnd.randrange(0x400))))
        struct.pack_into('<H', g, 0x9B41EE, rnd.choice((0xFFFF, rnd.randrange(
            1 << 16))))
        struct.pack_into('<I', g, 0x80000 - 0x3C, rnd.choice((
            rnd.randrange(8), 5, 6)))
        regs = {r: rnd.choice((rnd.randrange(0x100), rnd.randrange(1 << 32),
                               0x9A9000 + rnd.randrange(0x1000)))
                for r in ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi')}
        regs.update(esp=0x7FE00, ebp=0x80000)
        scratch = {0x40826E: [-0x40, -0x3F]} if name == 'db_boss' else {}
        try:
            x86, _a, _l = _run_region(region, 0, g, code, regs,
                                      ret=_BrRet(rnd), scratch=scratch)
        except X86Fault:
            continue
        seen.add(x86.reg('eip'))
        runs += 1
    assert seen == set(region.exits), (name, sorted(map(hex, seen)))
    assert runs >= int(os.environ.get('DB_SEEDS', 30)) // 3, runs


_CO_REGIONS = ['co_bank', 'co_leader', 'co_model', 'co_add', 'co_remove',
               'co_party', 'co_remove2', 'co_exists', 'co_avail', 'co_lock',
               'co_unlock', 'co_name', 'co_text', 'co_world', 'sl_fix']
_CO_AL = ('co_bank', 'co_add', 'co_remove', 'co_remove2')


class _CoRet(_Seq):
    """_Seq, but x86 0x60F750 (a field value) is often a character id"""

    def __call__(self, fn, args):
        v = _Seq.__call__(self, fn, args)
        if fn == 0x60F750 and v & 1:
            return v >> 8 & 0xFFFFFF00 | (v >> 1) % 12
        return v


def _co_guest(rnd):
    """_ir_guest's field script (bytes often a character id), the party
    (0xDC09E5, ids), the menu's character (0xCBF9D8 -> its +1 often 6 or
    0x11), the leader check's variable (0x60F92D: a party slot's offset or
    not) and the text pointers (0xCBF578)."""
    g = _ir_guest(rnd, 'co')
    script = bytes(rnd.choice((rnd.randrange(9), rnd.randrange(9),
                               rnd.randrange(256))) for _k in range(0x2000))
    g[0xC00000:0xC02000] = script
    g[0xDC09E5:0xDC09E8] = bytes(rnd.choice((rnd.randrange(9), 0xFF))
                                 for _k in range(3))
    g[0xC30000:0xC30200] = bytes(rnd.choice((6, 0x11, 0, rnd.randrange(256)))
                                 for _k in range(0x200))
    struct.pack_into('<H', g, 0x60F92D, rnd.choice((0x109, 0x10A, 0x10B,
                                                     0x108, 0x10C, 0x9090)))
    g[0xDC0927] = rnd.choice((0, 0, 0x10, rnd.randrange(256)))
    for k in range(8):
        struct.pack_into('<I', g, 0xCBF578 + 4 * k, 0xC00000 + rnd.randrange(
            0x2000))
    return g


@pytest.mark.parametrize('name', _CO_REGIONS)
def test_character_order_regions_match_x86(name):
    """Each region against CharacterOrderRandomizer.txt's (or
    StartingLevelFix.txt's) x86 on random field state: the same exit,
    calls, memory and registers, every exit taken."""
    region = _region(name)
    code = {**P.CHARACTER_ORDER_CODE, **P.STARTING_LEVEL_CODE}
    want = set(region.exits)
    seen, runs = set(), 0
    for seed in range(int(os.environ.get('CO_SEEDS', 60))):
        rnd = random.Random(seed)
        g = _co_guest(rnd)
        regs = {r: rnd.choice((rnd.randrange(0x200), 0xFFFF, 0x55,
                               rnd.randrange(1 << 32)))
                for r in ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi')}
        regs.update(esp=0x7FE00, ebp=0x80000)
        if name == 'co_bank':
            regs['ecx'] = 0x100 + rnd.randrange(0x20)
        if name in ('co_model', 'co_add', 'co_exists', 'co_avail',
                    'co_lock', 'co_unlock'):
            regs.update(ecx=rnd.randrange(0x1000), edx=0xC00000)
        if name in ('co_remove', 'co_remove2', 'co_party'):
            # (co_party: the movzx before it, in the port's pre)
            regs['eax'] = rnd.choice((rnd.randrange(9), rnd.randrange(256)))
        if name == 'co_text':
            regs['eax'] = 0xEA + rnd.randrange(9)
        if name == 'co_name':
            regs['eax'] = rnd.choice((0, 0, rnd.randrange(9),
                                      rnd.randrange(1 << 32)))
        scratch = {x: [-0x40] for x in region.exits} if name in _CO_AL \
            else ()
        try:
            x86, _a, _l = _run_region(region, 0, g, code, regs,
                                      ret=_CoRet(rnd), scratch=scratch)
        except X86Fault:
            continue
        seen.add(x86.reg('eip'))
        runs += 1
        if runs >= 20 and seen == want:
            break
    assert seen == want, (name, sorted(map(hex, seen)))
    assert runs >= 20, runs


def test_character_order_files():
    """Both files of the folder, alone and as one group, with the Item
    Randomizer's (the same seeds' init); their words as the stock has them."""
    import ff7nx_pccode
    hit = P.match(list(P.CHARACTER_ORDER_CODE.items()))
    assert hit and hit[0].name == 'Character Order Randomizer'
    hit2 = P.match(list(P.STARTING_LEVEL_CODE.items()))
    assert hit2 and hit2[0].name == 'Starting Level Fix'
    _exe, m = _tools()
    for port in (hit[0], hit2[0]):
        for _x, off, stock, _new in port.inline({}):
            assert struct.unpack_from('<I', m.img, off)[0] == stock
        for _x, off, stock, _sym in port.sites:
            assert struct.unpack_from('<I', m.img, off)[0] == stock

    def hext(d):
        return '\n'.join('%08X = %s' % (va, b.hex(' ').upper())
                         for va, b in d.items()) + '\n'
    # each file translates alone (no group: neither needs the other), and
    # with the Item Randomizer's the words agree where they meet (the
    # shared new-game hook)
    co, sl, ir = (hext(P.CHARACTER_ORDER_CODE), hext(P.STARTING_LEVEL_CODE),
                  hext({**P.ITEM_RANDO_CODE, 0x916FF8: P._IR_TEXT}))
    assert ff7nx_pccode.groups([('co', co), ('sl', sl)]) == []
    final = {}
    for text in (co, sl, ir):
        words = ff7nx_pccode.translate(text)
        assert words
        for off, _stock, new, _what in words:
            assert final.setdefault(off, new) == new, hex(off)


def test_double_bosses_words_and_files():
    """The battle loader's words (w28 set before the default's branch, now
    to 0x431404's case), and the four files grouped as one option."""
    import capstone
    import ff7nx_pccode
    _exe, m = _tools()
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    img = bytearray(m.img)
    for _x, off, stock, new in P._DB_WORDS:
        assert struct.unpack_from('<I', img, off)[0] == stock
        struct.pack_into('<I', img, off, new)
    got = [(i.mnemonic, i.op_str) for i in md.disasm(
        bytes(img[0x9BFE0:0x9C000]), 0x9BFE0)]
    assert got[2:7] == [('mov', 'w28, #0xb074'),
                        ('movk', 'w28, #0x9a, lsl #16'),
                        ('b.hi', '#0x9c0f0'),
                        ('ldr', 'w8, [x26, #0x14]'),
                        ('sub', 'w0, w8, #0x3c')]
    # 0x9C0F0 is the case the stock dispatch sends 0x431404 to
    case = [(i.mnemonic, i.op_str) for i in md.disasm(
        bytes(img[0x9C0E0:0x9C0F0]), 0x9C0E0)]
    assert case[:2] == [('mov', 'w8, #0x1404'), ('movk', 'w8, #0x43, lsl #16')]
    assert case[3] == ('b.ne', '#0x9c478')

    def hext(d):
        return '\n'.join('%08X = %s' % (va, b.hex(' ').upper())
                         for va, b in d.items()) + '\n'
    files = [('br', hext(P.BATTLE_RANDO_CODE)),
             ('db', hext(P.DOUBLE_BOSSES_CODE))]
    got = ff7nx_pccode.groups(files)
    assert len(got) == 1 and len(got[0][0]) == 2
    files += [('rs', hext(P.RANDO_SCALER_CODE)),
              ('pct', hext({0x9A7836: b'\x05', 0x9A783B: b'\x06'}))]
    got = ff7nx_pccode.groups(files)
    assert len(got) == 1 and len(got[0][0]) == 4 and got[0][1]
