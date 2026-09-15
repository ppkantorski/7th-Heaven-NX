#!/usr/bin/env python3
"""
ff7nx_dwhook.py -- pick the equipped weapon's mesh when the game opens it.

WHERE
=====
`open_file(file_context*, char *filename)` -- x86 0x6820D2, reached through
FFNx's own chain:

    main_loop 0x4090E6 -> battle_sub_429AC0 -> battle_b3ddata_sub_428B12
      -> graphics_render_sub_68A638 -> create_dx_sfx_something 0x670DA4
      -> load_p_file 0x69A028 -> open_file 0x6820D2

and specifically the block that decides WHICH name it hands to
`lgp_open_file`:

    006822C2  cmp dword [edx+0xC],0    ; is there a name mangler?
    006822C8  lea eax,[ebp-0x190]      ;   yes: the mangled local buffer
    006822D6  mov ecx,[ebp+0xC]        ;   no:  the caller's own string
    006822D9  mov [ebp-0x198],ecx

Recompiled (x20 = guest CPU context, +0x10 ESP, +0x14 EBP):

    +00B4F420  ldr  w22, [x0]           ; w22 = guest char* filename
    +00B4F424  ldr  w8,  [x20, #0x14]   ; w8  = guest EBP
    +00B4F428  str  w22, [x20, #4]
    +00B4F42C  sub  w0,  w8, #0x198     ; <-- HOOK
    +00B4F430  bl   0x10FC3A0           ; guest addr -> host pointer
    +00B4F434  str  w22, [x0]           ; [ebp-0x198] = the name to open

Everything the rewrite needs is live at that one instruction: the name in
`w22`, the frame in `w8`, and `[ebp-0x190]` -- `open_file`'s own local
scratch, at least 0xC8 bytes, already the destination in the mangler branch
and unread in the other. So the cave needs **no guest allocation**: it
writes the new name into that frame and repoints `w22`.

WHY THE SCRATCH REGISTERS ARE FREE
==================================
The hook is three instructions after a `bl`. A compiler cannot carry a value
across a call in a caller-saved register, so x9..x17 are dead there; the only
live things are x20 (callee-saved context), w22 (callee-saved, and what we
are replacing) and w8, written two instructions back and re-loaded from the
context four instructions later. The cave keeps w8's value in x11 and feeds
it to the displaced instruction, so nothing downstream can tell. x30 goes in
x17 because `bl` clobbers it.

`0x10FC3A0` touches only x0, x8, x9 and x10 and calls nothing, which is what
makes x11..x17 safe to hold values ACROSS it.

WHAT IT DOES
============
    if name[0:2] == "z0":                       # two byte compares
        c    = name[2] - '0'                    # savemap character 0..8
        base = hex(name[4:6])                   # first weapon of the range
        n    = hex(name[6]) + 1                 # how many
        val  = guest_byte(0xDBFDA8 + 0x84*c)    # equipped weapon
        if (unsigned)(val - base) >= n:         # not equipped, not joined
            val = base
        copy name into [ebp-0x190], write hex(val) over [4:6], use that

No table, in .rodata or anywhere: the character, the range and its size are
all in the name `ff7nx_dynweapon` gave the file. A name that is not a weapon
variant leaves after two byte compares.

SIZE
====
69 words. MEASURED on the stock 1.0.3 module, `cave_space` finds 7,531
verified padding holes -- 7,672 usable words, 30,688 bytes -- and the best
512 KB window, which is what an internally-branching cave has to fit inside,
offers 696. This takes a tenth of one window and nothing from the 60 FPS
tail gap.
"""
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

import a64 as A
import cave_space
import ff7nx_cave
import nxmap

TRANSLATE = 0x10FC3A0           # guest addr in w0 -> host ptr in x0
CTX_EBP = 0x14                  # guest context: EBP slot
FRAME_NAME = 0x198              # [ebp-0x198] -- the name lgp_open_file gets
FRAME_SCRATCH = 0x190           # [ebp-0x190] -- open_file's own buffer

# ff7nx_dynweapon.savemap_addr(0) and CHAR_STRIDE. Duplicated rather than
# imported so the asset half and the code half cannot drift apart unnoticed;
# test_dwhook.py asserts the two modules agree.
SAVEMAP_CHAR0 = 0xDBFDA8
CHAR_STRIDE = 0x84

MARKER0, MARKER1 = ord('z'), ord('0')
NAME_MAX = 20                   # bytes copied, NUL included

EQ, NE, LS, HI = 0, 1, 9, 8

# Registers the cave owns. x9/x10 are scratch INSIDE a step only: the
# translator clobbers them.
R_EBP, R_NAME, R_DST, R_BASE, R_CNT, R_VAL, R_LR = 11, 12, 13, 14, 15, 16, 17
R_CH = 13                       # shares R_DST: the character index is
                                # computed, so one register does both.


# ------------------------------------------------------------ encoders
# Each of these is checked against capstone by test_dwhook.py.

def orr_imm32(rd, rn, imm):
    """ORR Wd, Wn, #imm, for the one mask this cave needs."""
    if imm != 0x20:
        raise ValueError('orr_imm32: unsupported immediate 0x%X' % imm)
    return 0x32000000 | (27 << 16) | (0 << 10) | (rn << 5) | rd


def sub_imm(rd, rn, imm):
    return 0x51000000 | (imm << 10) | (rn << 5) | rd


def subs_imm(rd, rn, imm):
    return 0x71000000 | (imm << 10) | (rn << 5) | rd


def sub_reg(rd, rn, rm):
    return 0x4B000000 | (rm << 16) | (rn << 5) | rd


def csel(rd, rn, rm, cond):
    return 0x1A800000 | (rm << 16) | (cond << 12) | (rn << 5) | rd


def mul(rd, rn, rm):
    return 0x1B007C00 | (rm << 16) | (rn << 5) | rd


def orr_lsl(rd, rn, rm, sh):
    """ORR Wd, Wn, Wm, LSL #sh."""
    return 0x2A000000 | (rm << 16) | (sh << 10) | (rn << 5) | rd


def lsr_imm(rd, rn, sh):
    return 0x53000000 | (sh << 16) | (31 << 10) | (rn << 5) | rd


# ------------------------------------------------------------- locating

def _bl_target(word, at):
    if (word & 0xFC000000) != 0x94000000:
        return None
    imm = word & 0x03FFFFFF
    if imm & 0x02000000:
        imm -= 0x04000000
    return at + imm * 4


def find_hook(text, lo, hi):
    """
    (hook va, name reg, ebp reg, ctx reg) inside open_file's ARM64 extent.

    One exact six-instruction match, with the `bl` confirmed to reach the
    address translator and every register read out of the encoding rather
    than assumed. Anything other than exactly one hit is refused: a wrong
    hook rewrites a filename the game then cannot find, and the model stops
    loading with no other symptom.
    """
    def ldr_w(word):
        if (word & 0xFFC00000) != 0xB9400000:
            return None
        return (word & 0x1F, (word >> 5) & 0x1F, ((word >> 10) & 0xFFF) * 4)

    def str_w(word):
        if (word & 0xFFC00000) != 0xB9000000:
            return None
        return (word & 0x1F, (word >> 5) & 0x1F, ((word >> 10) & 0xFFF) * 4)

    def sub_w(word):
        if (word & 0xFFC00000) != 0x51000000:
            return None
        return (word & 0x1F, (word >> 5) & 0x1F, (word >> 10) & 0xFFF)

    out = []
    for va in range(lo, hi - 6 * 4, 4):
        w = struct.unpack_from('<6I', text, va)
        a, b, c, d, f = (ldr_w(w[0]), ldr_w(w[1]), str_w(w[2]),
                         sub_w(w[3]), str_w(w[5]))
        if not (a and b and c and d and f):
            continue
        rname, src, off = a
        if src != 0 or off != 0:                        # ldr wName, [x0]
            continue
        rebp, ctx, off = b
        if off != CTX_EBP or rebp == rname:             # ldr wEbp,[xCtx,#14]
            continue
        if c != (rname, ctx, 4):                        # str wName,[xCtx,#4]
            continue
        if d != (0, rebp, FRAME_NAME):                  # sub w0,wEbp,#0x198
            continue
        if _bl_target(w[4], va + 16) != TRANSLATE:      # bl translate
            continue
        if f != (rname, 0, 0):                          # str wName, [x0]
            continue
        out.append((va + 12, rname, rebp, ctx))
    if len(out) != 1:
        raise SystemExit('ff7nx_dwhook: expected exactly one hook site in '
                         'open_file, found %d' % len(out))
    return out[0]


# --------------------------------------------------------------- the cave
#
# FRAME. Everything the cave needs across a `bl` lives on the host stack, not
# in a register. The AAPCS says x0-x18 and x30 are caller-saved, and
# `arm64emu` enforces exactly that by filling all of them with garbage on
# return -- deliberately, so a cave cannot quietly come to depend on what one
# particular callee happens not to touch. The translator at 0x10FC3A0 does in
# fact leave x11-x17 alone, and an earlier draft of this cave kept its state
# there; that is a dependency on an implementation detail of a function this
# module does not own, and it is not worth the four instructions it saves.
#
# x19-x28 are callee-saved and therefore NOT free: they belong to the
# translated `open_file` body, which saved and is using them.
F_LR = 0x00         # x30
F_HOST = 0x08       # host pointer to the name
F_BASE = 0x10       # first value of the range
F_CNT = 0x14        # count - 1
F_EBP = 0x18        # guest EBP, for the displaced instruction
FRAME = 0x20

# Offsets within the name (see ff7nx_dynweapon):  z0 c s bb n vv
N_CHAR, N_BASE, N_CNT, N_VAL = 2, 4, 6, 7
NAME_SPAN = 8       # last byte the cave touches, name[8]


def cmp_imm64(rn, imm):
    return 0xF100001F | (imm << 10) | (rn << 5)


def build(site, addr, savemap0=SAVEMAP_CHAR0):
    """
    The cave body as a list of words, in emit_laid_out's contract.

    `addr(i)` is the REAL address of word i once the allocator has scattered
    the body across padding holes, so every internal branch resolves against
    the layout it will actually have rather than a pretend contiguous one.
    """
    hook, rname, rebp, _ctx = site
    body = []
    L = {}

    def add(*fns):
        body.extend(fns)

    def hexdigit(dst, off):
        """name[off] as a value 0..15 in `dst`. Branchless, six words."""
        return (
            lambda i: A.ldrb(9, 0, off),
            lambda i: orr_imm32(9, 9, 0x20),
            lambda i: sub_imm(dst, 9, 0x30),
            lambda i: sub_imm(10, 9, 0x57),          # 'a' - 10
            lambda i: A.cmp_imm(dst, 9),
            lambda i: csel(dst, dst, 10, LS),
        )

    def tohex(reg):
        """Value 0..15 in `reg` -> its lowercase ASCII digit, in place."""
        return (
            lambda i: A.cmp_imm(reg, 9),
            lambda i: A.add_imm(6, reg, 0x30),
            lambda i: A.add_imm(7, reg, 0x57),
            lambda i: csel(reg, 6, 7, LS),
        )

    def bail(i):
        return A.bcond(addr(i), addr(L['done']), NE)

    # --- frame, and the two things the displaced tail needs back
    add(lambda i: A.sub_imm64(31, 31, FRAME),
        lambda i: A.str64(30, 31, F_LR),
        lambda i: A.str_(rebp, 31, F_EBP))

    # --- host pointer to the name
    add(lambda i: A.mov_reg(0, rname),
        lambda i: A.bl(addr(i), TRANSLATE),
        lambda i: A.cbz64(0, addr(i), addr(L['done'])),
        lambda i: A.str64(0, 31, F_HOST))

    # --- CONTIGUITY. The translator is a 4 KB page table, so `host(p) + n` is
    #     the address of `p + n` only while both stay in one guest page. The
    #     cave reads name[0..6] and writes name[7..8]; if that span crosses a
    #     page it would read and write somewhere else entirely. Translating
    #     the far end and checking the two host pointers are exactly 8 apart
    #     settles it. A name that straddles is left alone, so the part loads
    #     its range's first weapon instead of the wrong file.
    add(lambda i: A.add_imm(0, rname, NAME_SPAN),
        lambda i: A.bl(addr(i), TRANSLATE),
        lambda i: A.ldr64(1, 31, F_HOST),
        lambda i: A.sub_reg64(2, 0, 1),
        lambda i: cmp_imm64(2, NAME_SPAN),
        bail,
        lambda i: A.mov_reg64(0, 1))

    # --- marker: name[0] == 'z' (either case), name[1] == '0'
    add(lambda i: A.ldrb(9, 0, 0),
        lambda i: orr_imm32(9, 9, 0x20),
        lambda i: A.cmp_imm(9, MARKER0),
        bail,
        lambda i: A.ldrb(9, 0, 1),
        lambda i: A.cmp_imm(9, MARKER1),
        bail)

    # --- savemap character index, 0..8
    add(lambda i: A.ldrb(1, 0, N_CHAR),
        lambda i: sub_imm(1, 1, 0x30),
        lambda i: A.cmp_imm(1, 8),
        lambda i: A.bcond(addr(i), addr(L['done']), HI))

    # --- base = hex(name[4]) << 4 | hex(name[5]);  count-1 = hex(name[6])
    add(*hexdigit(2, N_BASE))
    add(*hexdigit(3, N_BASE + 1))
    add(lambda i: orr_lsl(2, 3, 2, 4))
    add(*hexdigit(3, N_CNT))
    add(lambda i: A.str_(2, 31, F_BASE),
        lambda i: A.str_(3, 31, F_CNT))

    # --- the live equipped weapon, out of guest memory
    add(lambda i: A.movz(9, CHAR_STRIDE),
        lambda i: mul(1, 1, 9),
        lambda i: A.movz(0, savemap0 & 0xFFFF),
        lambda i: A.movk_hi(0, (savemap0 >> 16) & 0xFFFF),
        lambda i: A.add_reg(0, 0, 1),
        lambda i: A.bl(addr(i), TRANSLATE),
        lambda i: A.cbz64(0, addr(i), addr(L['done'])),
        lambda i: A.ldrb(4, 0, 0))

    # --- clamp into [base, base+count): one unsigned compare
    add(lambda i: A.ldr_(2, 31, F_BASE) if False else A.ldr(2, 31, F_BASE),
        lambda i: A.ldr(3, 31, F_CNT),
        lambda i: sub_reg(9, 4, 2),
        lambda i: A.cmp_reg(9, 3),
        lambda i: csel(4, 4, 2, LS))

    # --- write hex(value) over name[7:8], in place
    add(lambda i: A.ldr64(0, 31, F_HOST),
        lambda i: lsr_imm(5, 4, 4))
    add(*tohex(5))
    add(lambda i: A.strb(5, 0, N_VAL),
        lambda i: A.and_mask(5, 4, 4))
    add(*tohex(5))
    add(lambda i: A.strb(5, 0, N_VAL + 1))

    # --- rejoin: LR back, replay the displaced instruction, branch home
    L['done'] = len(body)
    add(lambda i: A.ldr64(30, 31, F_LR),
        lambda i: A.ldr(9, 31, F_EBP),
        lambda i: A.add_imm64(31, 31, FRAME),
        lambda i: sub_imm(0, 9, FRAME_NAME),
        lambda i: A.b(addr(i), hook + 4))

    return [fn(i) for i, fn in enumerate(body)]


# --------------------------------------------------------- applying it

DW_HOOK_ENV = 'SEVENTH_NX_DYNWEAPON'
N_WORDS = 77


def enabled():
    v = os.environ.get(DW_HOOK_ENV)
    return True if v is None else \
        v.strip().lower() not in ('', '0', 'off', 'false', 'no')


def _open_file_x86(exe_path):
    """FFNx's own chain, evaluated against the exe this build ships."""
    os.environ['FF7_EXE'] = exe_path
    for name in ('ff7nx_chain',):
        sys.modules.pop(name, None)
    import ff7nx_chain as C
    f = C.S['battle_sub_429AC0']
    for off in (0x71, 0x10A, 0xD3, 0x144, 0x3A):
        f = C.grc(f, off)
    return f


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def plan(m, exe_path, revert=False):
    """(patches, notes, problems) for nso_patcher."""
    img = m.img
    lo, hi = m.extent(_open_file_x86(exe_path))
    site = find_hook(m.text, lo, hi)
    hook = site[0]
    stock = _word(img, hook)
    displaced = sub_imm(0, site[2], FRAME_NAME)
    live = stock != displaced                 # already hooked?

    if revert or not enabled():
        if not live:
            return [], ['    dynamic weapons: already stock'], []
        return ([{'name': 'restore open_file name select', 'va': hex(hook),
                  'expect': struct.pack('<I', stock).hex(),
                  'set': struct.pack('<I', displaced).hex()}],
                ['    dynamic weapons: hook removed (the base weapon of '
                 'every range is what loads)'], [])
    if live:
        return [], ['    dynamic weapons: hook already installed'], []

    pool = ff7nx_cave.HolePool(
        img, starts=set(m.arm_starts),
        named=cave_space.named_targets(img[cave_space.RODATA:]))
    try:
        entry, placed = ff7nx_cave.emit_laid_out(
            pool, lambda _entry_va, addr: build(site, addr), span=0x80000)
    except ff7nx_cave.NoRoom as exc:
        return [], [], ['dynamic weapons cave: %s' % exc]

    placed[hook] = A.b(hook, entry)
    patches = [{'name': 'dynamic weapon name select', 'va': hex(va),
                'expect': struct.pack('<I', _word(img, va)).hex(),
                'set': struct.pack('<I', wd).hex()}
               for va, wd in sorted(placed.items())]
    notes = ['    dynamic weapons: open_file name select hooked at +0x%X, '
             'cave entry +0x%X, %d word(s) in reclaimed padding'
             % (hook, entry, len(placed) - 1)]
    return patches, notes, []


def apply_all(main, exe_path, revert=False, log=print):
    m = nxmap.Main(str(main))
    patches, notes, problems = plan(m, exe_path, revert=revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to install the dynamic weapon hook.')
        return 1
    log('  dynamic weapons (%s=%s):'
        % (DW_HOOK_ENV, 'off' if (revert or not enabled()) else 'on'))
    for n in notes:
        log(n)
    if not patches:
        return 0
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'dynamic weapons', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.dwhook-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  %d dynamic-weapon word(s) written' % len(patches))
    return 0
