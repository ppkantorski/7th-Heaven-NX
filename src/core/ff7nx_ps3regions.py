#!/usr/bin/env python3
"""ff7nx_ps3regions.py -- BUILD 868. PS3Tweaks options whose x86 rewrites
whole stretches of a function: each stretch (a REGION) is run as C made from
the mod's own x86 (tools/generators/x86c.py, via ps3regions.py, which writes
native/ps3tweaks<blob>.S and _c.c), entered from the translated function at
hooked words and resumed at the translated code of the x86 it leaves to.

A region R(...) (a hook H(...) is below):
  name     C / entry symbol stem: entry k is x3e_<name>_<k>
  blob     which PS3Tweaks blob holds it (3, 4, ...)
  ranges   [(x86 lo, hi)] of the MODDED image the C covers
  entries  [(x86 va, ARM hook)]: every way in from outside, each hooked
  ctx      the translated function's context register at the hooks
  exits    {x86 va: ARM resume}: every way out
  flags    exits whose x86 flags the translated code goes on to read
  post     {x86 exit: C}: extra state the resume point expects, from the
           saved host registers `regs` (x0..x17) -- with `fixed` naming them
  fixed    host registers `post` sets, so the resume check allows them
  native   {x86 call site: NATIVE routine} the port calls there instead

ps3regions.py refuses a region unless, in the stock module:
  * every hook word is in the function body and is what `stock` says;
  * the translated code reachable from the hooks up to the resume points
    (the code the region replaces) is entered from nowhere else;
  * no resume point reads a register (or NZCV) that replaced code wrote,
    before writing it, other than `fixed` ones;
and, in the modded x86, every way into the ranges is a listed entry.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""


# The port's own routines a region or hook may call in place of the x86 it
# replaced: its text drawing, so the native-text renderer (and the port's
# placement for it) is kept. Each takes the x86 function's guest stack
# arguments and pops the return slot as a translated function does, and is
# checked by its first words before a blob branches to it.
NATIVE = {
    # draw a string: the item-name style (w4 = 0)
    0x10E3E30: (0xF81B0FF9, 0xA9015FF8, 0xA90257F6, 0xA9034FF4, 0xA9047BFD,
                0x910103FD, 0xF0000F68, 0x395C4108),
    # draw a string: the label style (w4 = 1)
    0x10E4110: (0xF81C0FF7, 0xA90157F6, 0xA9024FF4, 0xA9037BFD, 0x9100C3FD,
                0xD0000F68, 0x395C4108, 0x34000568),
    # w0 bit 0: the native text renderer is on
    0x10E2720: (0x90000F88, 0x395C4100, 0xD65F03C0),
}


class H(object):
    """A hook: C `c` runs when the translated function reaches ARM `hook`,
    then the stock word there runs as before. The word must follow a `bl`
    and be reached no other way (so no flags are live), and must be one the
    blob can run in its place (no branch, nothing PC-relative, no x29, x30
    or sp). In `c`: ctx, g2h, regs (x0..x17 as saved), and X86(0x...) and
    ARM(0x...) as fn_t: a translated x86 function and a NATIVE routine.
    x86 is the patch the hook stands for (its function holds the hook).
    replay=False: the C stands in for the word, which does not run (for a
    store the mod changes; the C does the store or leaves it out)."""

    def __init__(self, name, blob, x86, hook, ctx, c, replay=True):
        self.name, self.blob, self.x86, self.hook = name, blob, x86, hook
        self.ctx, self.c, self.replay = ctx, c, replay

    def sites(self, module):
        import struct
        return [(self.x86, self.hook,
                 struct.unpack_from('<I', module.img, self.hook)[0],
                 'x3h_%s' % self.name)]


class W(object):
    """A word stub: ARM `arm` becomes a bl to `asm` (lines; a ret is added),
    for a change one word cannot make. It may write `clobbers` (each must be
    dead after the word) and x30 (a `bl` just before it has already)."""

    def __init__(self, name, blob, x86, arm, asm, clobbers=()):
        self.name, self.blob, self.x86, self.arm = name, blob, x86, arm
        self.asm, self.clobbers = list(asm), tuple(clobbers)

    def sites(self, module):
        import struct
        return [(self.x86, self.arm,
                 struct.unpack_from('<I', module.img, self.arm)[0],
                 'x3w_%s' % self.name)]


class R(object):
    def __init__(self, name, blob, ranges, entries, ctx, exits, flags=(),
                 post=None, fixed=(), native=None, subs=(), data=None,
                 pre=None, never=(), tables=(), move=None, bounds=None):
        # bounds: {x86 va: ARM} where the replaced ARM span ends on a way
        # no run of the region's x86 takes (the stock's own branches past
        # the mod's exits); never resumed, so nothing is checked there
        self.bounds = dict(bounds or {})
        # move: {x86 va: x86 va}, see x86c.Region
        self.move = dict(move or {})
        # tables: [(lo, hi)] of the mod's bytes a region reads by index
        # where the port has no copy (over x86 code); see x86c.Region
        self.tables = list(tables)
        # data: {x86 va: (blob data symbol, byte)} -- an option's value the
        # mod keeps in its cave, read from the blob's data area instead; or
        # {x86 va: byte} -- a stock byte of ff7_en's code the x86 reads.
        # pre, never: see x86c.Region
        self.data, self.pre = dict(data or {}), dict(pre or {})
        self.never = tuple(never)
        self.native = dict(native or {})
        self.subs = list(subs)
        self.name, self.blob, self.ranges = name, blob, list(ranges)
        self.entries, self.ctx, self.exits = list(entries), ctx, dict(exits)
        self.flags, self.post = tuple(flags), dict(post or {})
        self.fixed = tuple(fixed)

    def sites(self, module):
        """[(x86, ARM, stock word, entry symbol)] for a Port"""
        import struct
        return [(x86, arm, struct.unpack_from('<I', module.img, arm)[0],
                 'x3e_%s_%d' % (self.name, k))
                for k, (x86, arm) in enumerate(self.entries)]


# ------------------------------------------------------- Two Accessories
# (and ff7nx_ps3tweaks._TA_WORDS: the words that need no cave)
# The second accessory is the byte 3 below the first in the character
# record (+0x1B; the first is +0x1E). x86 regions of TwoAccessories.txt.
_TA_ROW = r"""
    u32 save[10], k, ebp = ctx[5];
    u32 rec = U32(ebp - 8u) * 0x84u + U32(ebp - 4u);
    u32 x = U32(ebp + 8u), y = U32(ebp + 0xCu), a = U8(rec + 0x1Bu);
    (void)regs;
    for (k = 0; k < 10; k++)
        save[k] = ctx[k];
    if (a != 0xFFu) {
        call1(ctx, g2h, X86(0x6C6B1B), a + 0x120u);
        call5(ctx, g2h, ARM(0x10E3E30), x + 0x%(name)Xu, y + 0x%(y)Xu,
              ctx[0], 7u, 0x%(scale)Xu);
    }
    x += (((u32 (*)(void))ARM(0x10E2720))() & 1u) ? (u32)%(nx)d : 0x%(x)Xu;
    call5(ctx, g2h, ARM(0x10E4110), x, y + 0x%(y)Xu, 0x%(label)Xu, 5u,
          0x%(scale)Xu);
    for (k = 0; k < 10; k++)
        ctx[k] = save[k];
"""
TWO_ACCESSORIES = [
    # battle status: both accessories' statuses (x86 0x5D0297 per slot)
    R('ta_bstat', 3, [(0x5CF946, 0x5CF973)], [(0x5CF946, 0x825BD8)], 23,
      {0x5CF973: 0x825C94},
      # the resume point stores 0xFFFF through x0, as the mod's `dec` did
      post={0x5CF973: 'regs[0] = (u64)g2h(eax + 0x52u);'}, fixed=('x0',)),
    # the stats of both
    R('ta_stats', 3, [(0x6C572B, 0x6C57D7)], [(0x6C572B, 0xCDFA70)], 20,
      {0x6C57D7: 0xCDFDF4}),
    # Curse Ring in either slot
    R('ta_curse', 3, [(0x6C5933, 0x6C5988)], [(0x6C5933, 0xCE0268)], 20,
      {0x6C5988: 0xCE03A0}),
    # Cat's Bell: 3 per bell
    # (the stock body's way back to the loop, x86 0x6CC599 `jmp`, is the
    # ARM's branch to the loop's increment)
    R('ta_bell', 3, [(0x6CC4AC, 0x6CC511), (0x6CC599, 0x6CC59E)],
      [(0x6CC4AC, 0xD0BDF0), (0x6CC599, 0xD0BDF4)], 19,
      {0x6CC511: 0xD0BF1C, 0x6CC59E: 0xD0C0C0},
      # the epilogue's `mov esp, ebp` takes ebp from w0, as the loop's
      # compare left it
      post={0x6CC59E: 'regs[0] = ebp;'}, fixed=('x0',)),
    # removing a character's accessories removes both
    R('ta_clear', 3, [(0x717F86, 0x717FD2)], [(0x717F86, 0xD08620)], 19,
      {0x717FD2: 0xD086E8}),
    # PHS: the second accessory's name, and a fourth label "    Acc.", a row
    # below the first (x86 0x70011C), after the third label. The port draws
    # that label at x, not x + 0x1B, with its native text; so does this.
    H('ta_phs', 3, 0x7001C5, 0xDB87B8, 19, _TA_ROW % dict(
        x=0x1B, nx=0, name=0x5D, y=0xA9, label=0x91F44A, scale=0x3DCED917)),
    # status: the same, x86 0x704666 (labels at x + 0, and at x - 0x10 with
    # native text: the port's 0xA there, less the mod's 0x1A)
    H('ta_status', 3, 0x704753, 0xDC8DA8, 19, _TA_ROW % dict(
        x=0, nx=-0x10, name=0x43, y=0x9F, label=0x920933, scale=0x3DCCCCCD)),
    # TwoAccessoriesESUICompat's: the names at x + 0x40 / x + 0x30
    H('ta_phs_e', 4, 0x7001C5, 0xDB87B8, 19, _TA_ROW % dict(
        x=0x1B, nx=0, name=0x40, y=0xA9, label=0x91F44A, scale=0x3DCED917)),
    H('ta_status_e', 4, 0x704753, 0xDC8DA8, 19, _TA_ROW % dict(
        x=0, nx=-0x10, name=0x30, y=0x9F, label=0x920933, scale=0x3DCCCCCD)),
    # equip menu (x86 0x705D16): the slot list has four rows, 0x18 apart
    W('ta_rows', 4, 0x705CB7, 0xDD182C, ['mov w8, #4', 'str w8, [x0]'],
      ('w8',)),                                     # was str w23 (3), [x0]
    W('ta_cursor', 4, 0x705F0C, 0xDD2898, ['mov w9, #0x18',
                                           'mul w8, w8, w9'], ('w9',)),
    # the equipped names: the second accessory under the first, at the
    # port's x for the accessory row (0x148 with native text)
    H('ta_equip_acc2', 4, 0x70639B, 0xDD4644, 24, r"""
    u32 save[10], k, ebp = ctx[5];
    u32 rec = U32(ebp - 0x20u) * 0x84u + U32(ebp - 4u), a = U8(rec + 0x1Bu);
    (void)regs;
    if (a == 0xFFu)
        return;
    for (k = 0; k < 10; k++)
        save[k] = ctx[k];
    call1(ctx, g2h, X86(0x6C6B1B), a + 0x120u);
    call5(ctx, g2h, ARM(0x10E3E30),
          (((u32 (*)(void))ARM(0x10E2720))() & 1u) ? 0x148u : 0x12Fu,
          0x55u, ctx[0], 7u, 0x3E4CCCCDu);
    for (k = 0; k < 10; k++)
        ctx[k] = save[k];
"""),
    # the description of the slot's item: either accessory. It goes on in
    # the port's own code (shared with the other slots from the next word),
    # whose `add w20, w8, #0x120` is the mod's x86 0x706606
    R('ta_eqdesc', 4, [(0x7064F3, 0x7064FD), (0x7065B1, 0x7065E5)],
      [(0x7064F3, 0xDD4B18)], 24, {0x706630: 0xDD4D10, 0x706606: 0xDD4C48},
      post={0x706606: 'regs[8] = ecx;'}, fixed=('x8',)),
    # equipping into either accessory slot
    R('ta_equip', 4, [(0x707568, 0x7075B4)], [(0x707568, 0xDD735C)], 24,
      {0x7075B4: 0xDD74B8}),
    # removing the one in the slot (triangle). The resume point stores w8
    # through x0 (the stock `mov byte [..+0x1E], 0xFF`, which the mod
    # drops): the byte already there, so it stores nothing new.
    R('ta_remove', 4, [(0x70729E, 0x707300)], [(0x70729E, 0xDD7A7C)], 24,
      {0x707322: 0xDD60F0, 0x707300: 0xDD7BFC},
      post={0x707300: 'regs[0] = (u64)g2h(esp); regs[8] = U8(esp);'},
      fixed=('x0', 'x8')),
]


# ------------------------------------------------------- Limit Overhaul
# (and ff7nx_ps3tweaks._LO_WORDS.) LimitOverhaul.txt jumps from 14 sites
# of the battle menu into its cave at x86 0x916A00; each function's sites
# are one region with the cave code they reach. The cave's helpers (the
# limit level's word at 0x916A00, its level at 0x916A1C) are called, so
# they are subs.
_LO_HELP = (0x916A00, 0x916A3C)
LIMIT_OVERHAUL = [
    # the Limit command's sound, [ebp-8] as before
    R('lo_sound', 5, [(0x5CB3FE, 0x5CB404), (0x916A3C, 0x916A51)],
      [(0x5CB3FE, 0x812AA8)], 24, {0x5CB404: 0x812AC8}),
    # the limit list: each limit's name, greyed while its gauge is short;
    # the window's help line only outside it
    R('lo_list', 5, [(0x6E0ABD, 0x6E0AC2), (0x6E0B5D, 0x6E0B62), _LO_HELP,
                     (0x916A51, 0x916B1C), (0x916D26, 0x916D3D)],
      [(0x6E0ABD, 0xD443C4), (0x6E0B5D, 0xD44678)], 20,
      {0x6E0AC2: 0xD443D8, 0x6E0B1F: 0xD44598, 0x6E0B47: 0xD44310,
       0x6E0B62: 0xD44688},
      post={0x6E0AC2: 'regs[8] = eax; regs[9] = edx;'}, fixed=('x8', 'x9'),
      subs=[_LO_HELP]),
    # choosing a limit: refused while its gauge is short. The resume at
    # x86 0x6DA49C loads cx through x0 (the stock `movzx cx, byte [..]`):
    # the cave's cx (0..6), from a byte below esp
    R('lo_pick', 5, [(0x6DA45E, 0x6DA464), (0x6DA4A9, 0x6DA4B6), _LO_HELP,
                     (0x916B1C, 0x916BC1), (0x916CCD, 0x916D26)],
      [(0x6DA45E, 0xD67598), (0x6DA4A9, 0xD678B8)], 22,
      {0x6DA464: 0xD675BC, 0x6DA49C: 0xD67878, 0x6DA4DA: 0xD67654,
       0x6DA4B6: 0xD678D8},
      post={0x6DA49C: 'U8(esp - 0x40u) = (u8)ecx; '
                      'regs[0] = (u64)g2h(esp - 0x40u);'},
      fixed=('x0',), subs=[_LO_HELP]),
    # the command chosen: PHS opens Magic, Limit the limit list or E.Skill
    R('lo_command', 6, [(0x6D9528, 0x6D956A), _LO_HELP,
                        (0x916BC1, 0x916C5B)],
      [(0x6D9528, 0xD63940)], 23, {0x6D956A: 0xD63A30}, subs=[_LO_HELP]),
    # the command menu's Limit entry: drawn in the limit colours when full
    R('lo_name', 6, [(0x6DE55A, 0x6DE560), (0x6DE704, 0x6DE709), _LO_HELP,
                     (0x916C5B, 0x916C7F), (0x916DCB, 0x916E3D)],
      [(0x6DE55A, 0xD41D74), (0x6DE704, 0xD42360)], 20,
      {0x6DE5FC: 0xD42060, 0x6DE560: 0xD41D84, 0x6DE709: 0xD4238C},
      subs=[_LO_HELP]),
    # ... named LIMIT (x86 0x91A938) in place of the command's own name:
    # the `call 0x41963C` is `mov eax, 0x91A938`, so the stub sets eax and
    # takes back the return slot the port pushed for the call
    W('lo_name_text', 6, 0x6DE584, 0xD41E48,
      ['mov     w8, #0xA938', 'movk    w8, #0x91, lsl #16',
       'str     w8, [x20]', 'ldr     w8, [x20, #0x10]',
       'add     w8, w8, #4', 'str     w8, [x20, #0x10]'], clobbers=('w8',)),
    # the limit's name as the battle shows it. The resume at x86 0x6D7768
    # is the port's store of the last push (shared by five paths): x0 and
    # w8 make it store the cave's push again
    R('lo_show', 6, [(0x6D770F, 0x6D7717), (0x6D775A, 0x6D7768),
                     (0x916D3D, 0x916DCB)],
      [(0x6D770F, 0xD3DA8C), (0x6D775A, 0xD3DB64)], 22,
      {0x6D7775: 0xD3E0AC, 0x6D7717: 0xD3DA9C, 0x6D7768: 0xD3E07C},
      post={0x6D7768: 'regs[0] = (u64)g2h(esp); regs[8] = U32(esp);'},
      fixed=('x0', 'x8')),
    # using the limit: its gauge spent by its level, the limit set up
    R('lo_use', 6, [(0x6D87CA, 0x6D87D7), _LO_HELP, (0x916E3D, 0x916F28)],
      [(0x6D87CA, 0xD5EDD4)], 21, {0x6D87D7: 0xD5EE10, 0x6D8943: 0xD5F288},
      subs=[_LO_HELP]),
    # the Attack command turns into Limit only in the final battle. The
    # resume at x86 0x437AE6 stores w19 through x8 + 5 (the stock path's
    # `mov byte [eax+5], 0`): x8 points it below esp, at nothing
    R('lo_attack', 5, [(0x437A1B, 0x437A21), (0x916F28, 0x916F40)],
      [(0x437A1B, 0xFAC70)], 20, {0x437A21: 0xFAC90, 0x437AE6: 0xFAE64},
      post={0x437AE6: 'regs[8] = esp - 0x45u;'}, fixed=('x8',)),
    # the gauge kept between battles: the byte after the stock word the
    # port clears, from the character's saved gauge
    H('lo_gauge_load', 5, 0x4346FD, 0xF13C0, 20, r"""
    (void)fns; (void)regs;
    U8(ctx[1] + 0xDBA4B3u) = U8(U32(ctx[5] + 8u) * 0x34u + 0x9A8DC0u);
""", replay=False),
    # ... and cleared after battle only for character ids 2, 6 and 7 (as
    # the mod has it)
    H('lo_gauge_keep', 5, 0x5C9357, 0x804F9C, 20, r"""
    u32 c = U8(U32(ctx[5] - 4u) + 0xDC0230u);
    (void)fns; (void)regs;
    if (c == 2u || c == 6u || c == 7u)
        U16(ctx[0] + 0x9A8DC0u) = 0;
""", replay=False),
]


# ---------------------------------------------------------- Enemy Scaling
# EnemyScaling/NoRando (with a 10%..100% file for its multiplier): each
# enemy's stats scaled by (num/den)^3 as a battle loads (x86 0x5D1237, after
# the enemy block is copied to 0x9A8E9C), and its level by (num/den)^2
# (x86 0x5C99B8). den/num are the cave's bytes 0x9A7836 / 0x9A783B, which
# the option's files set; the blob keeps them as es_den / es_num.
_ES_DATA = {0x9A7836: ('es_den', 0), 0x9A783B: ('es_num', 0)}
ENEMY_SCALING = [
    # the stats. 0x917141 (an unused jmp) is left out.
    R('es_stats', 7, [(0x5D1237, 0x5D123C), (0x9170A0, 0x917141),
                      (0x917146, 0x917193)],
      [(0x5D1237, 0x82CC28)], 24, {0x5D123C: 0x82CC38},
      subs=[(0x917146, 0x917193)], data=_ES_DATA),
    # the level. The port still holds eax (x86 0x5C99B5) in w8 here; the
    # resume point's store reads w8 as the byte the stock `mov cl` loaded.
    # 0x9A77C4..0x9A77F1 (jumped over) is left out.
    R('es_level', 7, [(0x5C99B8, 0x5C99BD), (0x9A77A0, 0x9A77C4),
                      (0x9A77F1, 0x9A7829)],
      [(0x5C99B8, 0x80791C)], 21, {0x5C99BD: 0x80792C},
      pre={0: 'eax = (u32)regs[8];'},
      post={0x5C99BD: 'regs[8] = ecx & 0xFFu;'}, fixed=('x8',),
      data=_ES_DATA),
]


# ---------------------------------------------------------- Solo Challenge
# SoloChallenge/<who>Solo. sc_char: the forced character, the imm32 at
# 0x916817 (read again as a dword by the new-game cave).
_SC_DATA = {0x916817 + k: ('sc_char', k) for k in range(4)}
# the battle party (x86 0x41B5A5, the loop copying 0xDC0230 into the
# battle's slots): slot 0 forced to the character, set up as on joining,
# the others empty. Its resume point loads `al` through x0 again.
_SC_AL = {0x41B5AB: 'U8(esp - 0x40u) = (u8)eax; '
                    'regs[0] = (u64)g2h(esp - 0x40u);'}
# imprison immunity: the mod calls into its cave in place of x86 0x42A29F's
# `mov eax, [ebp-0xC4]` (the stock word still loads it), which also sets
# bit 7 of 0x9A8B47; its `cl` is dead (0x42A2A5 loads ecx).
_SC_IMPRISON = H('sc_imprison', 8, 0x42A29F, 0xD45C0, 20, r"""
    (void)fns; (void)regs;
    ctx[1] = (ctx[1] & ~0xFFu) | ((U8(0x9A8B47u) | 0x80u) & 0xFFu);
    U8(0x9A8B47u) = (u8)ctx[1];
""")
SOLO = [
    R('sc_party', 8, [(0x41B5A5, 0x41B5AB), (0x916800, 0x9168C6)],
      [(0x41B5A5, 0x8D64C)], 23, {0x41B5AB: 0x8D650},
      post=_SC_AL, fixed=('x0',), data=_SC_DATA),
    _SC_IMPRISON,
    # a new game (x86 0x722283, before the first field): the character
    # joins, with its starting stats, before the stock `push 0xD0`
    R('sc_init', 8, [(0x722283, 0x722288), (0x9168C6, 0x916945)],
      [(0x722283, 0xE4711C)], 25, {0x722288: 0xE47134}, data=_SC_DATA),
]
SOLO_CAIT = [
    R('sc_init_cait', 8, [(0x722283, 0x722288), (0x9168C6, 0x916955)],
      [(0x722283, 0xE4711C)], 25, {0x722288: 0xE47134}),
]
SOLO_VINCENT = [
    R('sc_init_vincent', 8, [(0x722283, 0x722288), (0x9168C6, 0x91694B)],
      [(0x722283, 0xE4711C)], 25, {0x722288: 0xE47134}),
]
SOLO_RANDOM = [
    # 0x916863..0x916869 (nothing reaches it) is left out
    R('sc_party_random', 8, [(0x41B5A5, 0x41B5AB), (0x916800, 0x916863),
                             (0x916869, 0x9168FD)],
      [(0x41B5A5, 0x8D64C)], 23, {0x41B5AB: 0x8D650},
      post=_SC_AL, fixed=('x0',)),
]


# ------------------------------------------------------- Party Randomizer
# Randomizer/PartyRando: Solo (Random)'s party loop, with its own random
# helper at 0x9B4160 (seeded from the game's counter, four rand() bytes).
PARTY_RANDO = [
    R('pr_party', 7, [(0x41B5A5, 0x41B5AB), (0x916800, 0x9168FD),
                      (0x9B4160, 0x9B4196)],
      [(0x41B5A5, 0x8D64C)], 23, {0x41B5AB: 0x8D650},
      post=_SC_AL, fixed=('x0',), subs=((0x9B4160, 0x9B4196),)),
]


# -------------------------------------------------------- Item Randomizer
# Randomizer/ItemRando: field items and materia (the STITM / SMTRA opcodes
# and their "Received" message), shop stock, and steal / morph / drop.
# Shared helpers: 0x9B414B (four rand() bytes), 0x9A765E (is that materia
# id empty?), 0x9A7427 (a random item id), 0x9A769F / 0x9A76BA (a random
# materia id). Each region carries its own copy of the helpers it calls.
_IR_RAND = (0x9B412F, 0x9B4150)
_IR_EMPTY = (0x9A765E, 0x9A766B)
_IR_STEAL = (0x9B4047, 0x9B4150)    # the weighted pick, with _IR_RAND
_IR_TEXT = ' '.join('U8(0x%Xu) = DATA(ir_text, %d);' % (0x916FF8 + k, k)
                    for k in range(8))
ITEM_RANDO = [
    # new game / load: the per-save seeds at 0xDC0E08..0xDC0E0D
    R('ir_init', 11, [(0x722288, 0x72228D), (0x9A766B, 0x9A769D)],
      [(0x722288, 0xE47134)], 25, {0x72228D: 0xE47144}),
    # STITM: a random item for each one picked up
    R('ir_item', 11, [(0x61E66C, 0x61E671), (0x9A73F5, 0x9A7473), _IR_RAND],
      [(0x61E66C, 0x98A3FC)], 20, {0x61E671: 0x98A428},
      subs=((0x9A7427, 0x9A7456), _IR_RAND)),
    # SMTRA: a random materia (its `and eax, 0xFF` became a call)
    R('ir_materia', 12, [(0x61EE07, 0x61EE12), (0x9A769F, 0x9A7712),
                         _IR_EMPTY, _IR_RAND],
      [(0x61EE07, 0x98C06C)], 20, {0x61EE12: 0x98C08C},
      subs=((0x9A769F, 0x9A7712), _IR_EMPTY, _IR_RAND)),
    # the "Received ..." message: the name of what was really given. The
    # text it looks for (0x916FF8, "eceived", or SACRandoCompat's
    # "btained") is the mod's .data, which this port writes on entry.
    R('ir_message', 11, [(0x63172A, 0x631731), (0x9A7478, 0x9A766B),
                         _IR_RAND],
      [(0x63172A, 0x9CECBC)], 20, {0x631731: 0x9CECD0},
      subs=((0x9A7528, 0x9A766B), _IR_RAND),
      pre={0: 'eax = (u32)regs[8]; ' + _IR_TEXT}),
    # shop stock
    R('ir_shop', 10, [(0x61F07D, 0x61F08A), (0x9A7720, 0x9A7799),
                      (0x9A7427, 0x9A7456), _IR_EMPTY, (0x9A76BA, 0x9A76ED),
                      _IR_RAND, (0x9B4175, 0x9B4196)],
      [(0x61F07D, 0x98CD08)], 22, {0x61F08A: 0x98CD3C},
      subs=((0x9A7427, 0x9A7456), _IR_EMPTY, (0x9A76BA, 0x9A76ED),
            _IR_RAND, (0x9B4175, 0x9B4196))),
    # steal
    R('ir_steal', 12, [(0x5CA961, 0x5CA969), (0x9B4000, 0x9B4019),
                       _IR_STEAL, _IR_EMPTY],
      [(0x5CA961, 0x80FB88)], 20, {0x5CA969: 0x80FCA0, 0x5CA972: 0x80FB98},
      post={0x5CA969: 'regs[8] = ebp;', 0x5CA972: 'regs[8] = ebp;'},
      fixed=('x8',), subs=(_IR_STEAL, _IR_EMPTY)),
    # morph
    R('ir_morph', 13, [(0x5CACCC, 0x5CACD7), (0x9B4019, 0x9B402F),
                       _IR_STEAL, _IR_EMPTY],
      [(0x5CACCC, 0x810880)], 20, {0x5CAD6B: 0x810AC8, 0x5CACD7: 0x810888},
      subs=(_IR_STEAL, _IR_EMPTY)),
    # drops
    R('ir_drop', 14, [(0x5C7D38, 0x5C7D4C), (0x9B402F, 0x9B4150), _IR_EMPTY],
      # (0x5C7D51, the stock way out, is not taken: it bounds the span)
      [(0x5C7D38, 0x7FA440)], 19, {0x5C7D4C: 0x7FA228, 0x5C7D51: 0x7FA490},
      subs=(_IR_STEAL, _IR_EMPTY)),
    # the shop's price: the 0.1 depth the mod passes (w8 is dead after)
    W('ir_depth', 13, 0x71B90D, 0xE3143C,
      ['mov     w8, #0xCCCD', 'movk    w8, #0x3DCC, lsl #16',
       'str     w8, [x0]'], clobbers=('w8',)),
]


# ------------------------------------------------------- Enemy Randomizer
# Randomizer/BattleRando: bosses swapped through the table it writes at
# 0x767BB0 (over the world map's encounter code, which it also replaces),
# random encounters on the world map and in fields (the bit tables at
# 0x767B38), and its own enemy scaling. Its new-game seeds are Item
# Randomizer's (the same bytes: ir_init). With EnemyScaling/Rando and a %
# file, the scaling adjuster at 0x9A7830 is called as well.
_BR_TABLE = (0x767B34, 0x767C06)
_BR_RAND = (0x9B4160, 0x9B4191)
_BR_PICK = (0x767ADA, 0x767B37)     # a random encounter from the bit table
_BR_AX = {0x40826E: 'U16(esp - 0x40u) = (u16)eax; '
                    'regs[0] = (u64)g2h(esp - 0x40u);'}
_BR_ADJ = (0x9A7830, 0x9A7853)


def _br_scaling(stats, more, data=None, sfx=''):
    """the scaling regions: `stats`, the stats cave's ranges; `more`, the
    adjuster (with the scaling files) or nothing"""
    return [
        R('br_stats' + sfx, 16, [(0x5D1237, 0x5D123C)] + list(stats) +
          list(more), [(0x5D1237, 0x82CC28)], 24, {0x5D123C: 0x82CC38},
          subs=[(stats[-1][0], 0x917193)] + list(more), data=data),
        R('br_level' + sfx, 9 if sfx else 7, [(0x5C99B8, 0x5C99BD), (0x9A77A0, 0x9A7829)]
          + list(more), [(0x5C99B8, 0x80791C)], 21, {0x5C99BD: 0x80792C},
          pre={0: 'eax = (u32)regs[8];'},
          post={0x5C99BD: 'regs[8] = ecx & 0xFFu;'}, fixed=('x8',),
          subs=list(more), data=data),
    ]


BATTLE_RANDO = [
    # the battle's formation: a boss is swapped for another. The resume
    # point loads ax through x0 again.
    R('br_boss', 15, [(0x408268, 0x40826E), (0x917000, 0x91709A)],
      [(0x408268, 0x35EB0)], 22, {0x40826E: 0x35EB8}, post=_BR_AX,
      fixed=('x0',), subs=((0x91707C, 0x917089),), tables=[_BR_TABLE]),
    R('br_field', 15, [(0x60B620, 0x60B632), _BR_PICK, _BR_RAND],
      [(0x60B620, 0x91F9F4)], 22, {0x60B632: 0x91FA14},
      subs=(_BR_PICK, _BR_RAND), tables=[_BR_TABLE]),
    R('br_world', 15, [(0x767ACD, 0x767B37), _BR_RAND],
      [(0x767ACD, 0xFA0AB0)], 20, {0x767C0B: 0xFA1034},
      subs=(_BR_PICK, _BR_RAND), tables=[_BR_TABLE]),
] + _br_scaling([(0x9170A0, 0x917141), (0x917146, 0x917193)], ())
# with EnemyScaling/Rando and a % file: the stats cave calls the adjuster
# through 0x917141, and so does the level cave
BATTLE_RANDO_ES = _br_scaling([(0x9170A0, 0x917193)], [_BR_ADJ],
                              data=_ES_DATA, sfx='_es')
for _r in BATTLE_RANDO_ES[:1]:
    _r.subs = [(0x917141, 0x917193), _BR_ADJ]


# ----------------------------------------------------------- Double Bosses
# Randomizer/DoubleBosses, over the Enemy Randomizer: each boss is two. Its
# boss cave (0x917000) also picks the second (kept at 0x9B41EE, a .data
# word), loaded through the battle loader (0x430DD0) when the first's
# formation ends; rewards are cleared at the start of a fight instead of
# its end. The battle loader's own edits are words (the port's _DB_WORDS).
DOUBLE_BOSSES = [
    R('db_boss', 17, [(0x408268, 0x40826E), (0x917000, 0x91708C),
                      (0x9B4200, 0x9B423A)],
      [(0x408268, 0x35EB0)], 22, {0x40826E: 0x35EB8}, post=_BR_AX,
      fixed=('x0',), subs=((0x9B4200, 0x9B423A),), tables=[_BR_TABLE]),
    # the fight's start: the rewards byte cleared with the others
    R('db_start', 17, [(0x408299, 0x4082B0)],
      [(0x408299, 0x35F5C)], 22, {0x4082B0: 0x36008}),
    # the formation's second boss
    R('db_second', 17, [(0x41C94E, 0x41C956), (0x9B41A0, 0x9B41EE)],
      [(0x41C94E, 0x93A6C)], 26, {0x41C956: 0x93A80},
      subs=((0x9B41A0, 0x9B41EE),)),
    # the battle's end check: no "all enemies gone" test of the first's
    R('db_check', 17, [(0x429E82, 0x429EEA)],
      [(0x429E82, 0xD59B0)], 23, {0x429EEA: 0xD5ADC}),
    # the loader's last case: 0x430CB0 only for the formation's own cases
    R('db_end', 17, [(0x431831, 0x431839), (0x91708C, 0x9170A0)],
      [(0x431831, 0x9DE18)], 26, {0x431839: 0x9DE28, 0x43186C: 0x9E3A0}),
]


# ------------------------------------------- Character Order Randomizer
# Randomizer/CharacterOrderRando/hext/CharacterOrderRandomizer.txt: each
# character id a field opcode names goes through the mod's map at 0x9B4240
# (a permutation of 0..8 drawn from the save's seed at 0xDC0E08, with
# _IR_RAND), so the story's party joins in another order. The mod keeps one
# variable in .text, at 0x60F92D (the party-leader check's last bank
# offset): the port's guest .text is the read-only image, so it lives at
# 0x9B42A0 instead (the mod's own cave bytes there, which a ported file
# never writes to guest memory).
_CO_MAP = (0x9B4240, 0x9B427C)
_CO_SUBS = (_CO_MAP, _IR_RAND)
_CO_MOVE = {0x60F92D: 0x9B42A0}
_CO_AL = 'U8(esp - 0x40u) = (u8)eax; regs[0] = (u64)g2h(esp - 0x40u);'
CHARACTER_ORDER = [
    # bank 0x100 reads (party slots at 0xDC09E5): the offset kept for the
    # check below, and the stores to the dead [ebp-0xC]/[ebp-4] dropped
    R('co_bank', 18, [(0x60F91D, 0x60F92B)], [(0x60F91D, 0x94EDA4)], 20,
      {0x60F931: 0x94EDD0}, post={0x60F931: _CO_AL}, fixed=('x0',),
      move=_CO_MOVE),
    # the party-leader check: a party slot's member read once, mapped
    R('co_leader', 18, [(0x61182C, 0x611831), (0x9B42A0, 0x9B42C5),
                        _CO_MAP, _IR_RAND],
      [(0x61182C, 0x955680)], 20, {0x611831: 0x955690}, subs=_CO_SUBS,
      move=_CO_MOVE),
    # set character model
    R('co_model', 18, [(0x61BCF4, 0x61BD04), _CO_MAP, _IR_RAND],
      [(0x61BCF4, 0x980738)], 21, {0x61BD04: 0x98076C},
      post={0x61BD04: 'regs[8] = ecx;'}, fixed=('x8',), subs=_CO_SUBS),
    # add character (its loop's step moved: the same, ecx dead after)
    R('co_add', 18, [(0x61BEB2, 0x61BEBF), _CO_MAP, _IR_RAND],
      [(0x61BEB2, 0x980CB4)], 21, {0x61BEBF: 0x980CB8},
      post={0x61BEBF: _CO_AL}, fixed=('x0',), subs=_CO_SUBS),
    # remove character
    R('co_remove', 19, [(0x61C066, 0x61C06F), _CO_MAP, _IR_RAND],
      [(0x61C066, 0x981394)], 20, {0x61C06F: 0x981398},
      post={0x61C06F: _CO_AL}, fixed=('x0',), subs=_CO_SUBS),
    # set party: each member mapped (the movzx before the call in pre)
    R('co_party', 19, [(0x61C14C, 0x61C158), _CO_MAP, _IR_RAND],
      [(0x61C14C, 0x98163C)], 21, {0x61C158: 0x981648},
      pre={0: 'eax &= 0xFFu;'}, subs=_CO_SUBS),
    # the other remove-character opcode
    R('co_remove2', 19, [(0x61C6B8, 0x61C6C1), _CO_MAP, _IR_RAND],
      [(0x61C6B8, 0x983004)], 21, {0x61C6C1: 0x983008},
      post={0x61C6C1: _CO_AL}, fixed=('x0',), subs=_CO_SUBS),
    # check if character exists
    R('co_exists', 20, [(0x61C77A, 0x61C78F), _CO_MAP, _IR_RAND],
      [(0x61C77A, 0x9832E0)], 21, {0x61C78F: 0x983324}, subs=_CO_SUBS),
    # set character availability
    R('co_avail', 20, [(0x61C82F, 0x61C843), (0x61C857, 0x61C85A),
                       _CO_MAP, _IR_RAND],
      [(0x61C82F, 0x983534)], 21, {0x61C85A: 0x9835BC}, subs=_CO_SUBS),
    # lock / unlock character
    R('co_lock', 20, [(0x61C922, 0x61C935), _CO_MAP, _IR_RAND],
      [(0x61C922, 0x9838F8)], 21, {0x61C935: 0x98392C},
      # x9: the resume's `sxth w9, w8` only writes it
      post={0x61C935: 'regs[8] = ecx; regs[9] = 0;'}, fixed=('x8', 'x9'),
      subs=_CO_SUBS),
    R('co_unlock', 21, [(0x61C98A, 0x61C99D), _CO_MAP, _IR_RAND],
      [(0x61C98A, 0x983AA4)], 21, {0x61C99D: 0x983AD8},
      post={0x61C99D: 'regs[8] = ecx;'}, fixed=('x8',), subs=_CO_SUBS),
    # name character / remove its materia: the menu's character mapped
    R('co_name', 21, [(0x61F096, 0x61F0AA), (0x61C843, 0x61C857),
                      (0x9B4280, 0x9B429D), _CO_MAP, _IR_RAND],
      [(0x61F096, 0x98CD88)], 22, {0x61F0AA: 0x98CDC0},
      post={0x61F0AA: 'regs[8] = eax;'}, fixed=('x8',),
      subs=((0x61C843, 0x61C857), (0x9B4280, 0x9B429D)) + _CO_SUBS),
    # the names in text (case 7 of 0x631AF2's switch: bytes 0xEA..0xF2,
    # so 0..8 and the map's 0..8 in [ebp-4]: the stock's word load of it
    # at the resume, shared with 0x6321EC, reads it whole); from the stock
    # `sub` before it
    R('co_text', 22, [(0x631FC5, 0x631FD4), _CO_MAP, _IR_RAND],
      [(0x631FC5, 0x9D033C)], 20, {0x631FD4: 0x9D0354}, subs=_CO_SUBS),
    # the world map's party model
    R('co_world', 22, [(0x767374, 0x7673A3), _CO_MAP, _IR_RAND],
      [(0x767374, 0xF2FEB8)], 20,
      {0x76740E: 0xF2FFC0, 0x7673FF: 0xF2FF94, 0x7673F0: 0xF300A4},
      bounds={0x7673F7: 0xF300A8}, subs=_CO_SUBS),
]
# CharacterOrderRando/hext/StartingLevelFix.txt: a joining member's
# [ebp-0x64] cleared on every path (the level byte's other half: 0x5C7460's
# `jmp` and 0x719B94's nops are words, see ff7nx_ps3tweaks)
STARTING_LEVEL = [
    R('sl_fix', 19, [(0x5C7508, 0x5C7514)], [(0x5C7508, 0x7F879C)], 20,
      {0x5C7514: 0x7F87B8}),
]


# ------------------------------------------------------------- Battle PHS
# BattlePHS/hext/BattlePHS.txt: the PHS as a battle command (0x1D in the
# command at 0xDC3C70) that lists the party's absent members and swaps one
# in. Each region: one `jmp` into the caves at 0x9A7A00 / 0x9A7F00, and
# where the mod's exit is a flag test, the stock jcc after it as well.
BATTLE_PHS = [
    # the magic list's page count: 0 rows scrolled for the PHS list
    R('phs_rows', 9, [(0x6DFA02, 0x6DFA07), (0x9A7A00, 0x9A7A21)],
      [(0x6DFA02, 0xD435A8)], 20, {0x6DFA0A: 0xD435C4}),
    # the magic list's entries: the absent members' names instead
    R('phs_list', 9, [(0x6DFAE5, 0x6DFAED), (0x9A7A21, 0x9A7ACF)],
      [(0x6DFAE5, 0xD43944)], 20,
      {0x6DFB6B: 0xD43860, 0x6DFAED: 0xD43958, 0x6DFB43: 0xD43B00}),
    # no "all" marker on them
    R('phs_all', 9, [(0x6DFBF1, 0x6DFBF8), (0x9A7BC0, 0x9A7BDC)],
      [(0x6DFBF1, 0xD43DA8)], 20, {0x6DFC39: 0xD43C30, 0x6DFBF8: 0xD43DC4}),
    # nor the MP box
    R('phs_mp', 9, [(0x6DFC4F, 0x6DFC54), (0x9A7F00, 0x9A7F13)],
      [(0x6DFC4F, 0xD43F48)], 20, {0x6DFC54: 0xD43F58}),
    # picking one: present, alive and free to join
    R('phs_pick', 9, [(0x6D9C64, 0x6D9C69), (0x9A7B11, 0x9A7B7E)],
      [(0x6D9C64, 0xD653BC)], 23, {0x6D9D6F: 0xD6541C, 0x6D9C6C: 0xD653CC}),
    R('phs_empty', 9, [(0x6D9C77, 0x6D9C83), (0x9A7ACF, 0x9A7AF2)],
      [(0x6D9C77, 0xD65408)], 23, {0x6D9D6F: 0xD6541C, 0x6D9C83: 0xD655CC}),
    R('phs_id', 9, [(0x6D9C93, 0x6D9C9F), (0x9A7AF2, 0x9A7B11)],
      [(0x6D9C93, 0xD6562C)], 23, {0x6D9C9F: 0xD65644}),
    R('phs_target', 9, [(0x6D9CAB, 0x6D9CB0), (0x9A7F32, 0x9A7F49)],
      [(0x6D9CAB, 0xD65688)], 23, {0x6D9CB2: 0xD65690}),
    R('phs_menu', 9, [(0x6D9D4D, 0x6D9D52), (0x9A7F49, 0x9A7F60)],
      [(0x6D9D4D, 0xD65770)], 23, {0x6D9D53: 0xD65778}),
    # the PHS command's own sound
    R('phs_sound', 9, [(0x5CB3D1, 0x5CB3D6), (0x9A7B7E, 0x9A7BA3)],
      [(0x5CB3D1, 0x8129E8)], 24, {0x5CB3D8: 0x812A60}),
    # its name in the command menu
    R('phs_name', 9, [(0x6DE70B, 0x6DE710), (0x9A7BA3, 0x9A7BC0)],
      [(0x6DE70B, 0xD423A4)], 20, {0x6DE710: 0xD423B4}),
    # an actor's targets kept while it is swapped
    R('phs_targets', 9, [(0x433B69, 0x433B6E), (0x9A7BDC, 0x9A7C13)],
      [(0x433B69, 0xEE214)], 19, {0x433B70: 0xEE21C}),
    # the "wait" glyph row
    R('phs_glyph', 9, [(0x41960C, 0x419611), (0x9A7F13, 0x9A7F32)],
      [(0x41960C, 0x84A6C)], 19, {0x419612: 0x84A74}),
    # the command's code as Limit Overhaul writes it (its 0x6D9545 nops)
    R('phs_command', 9, [(0x6D9528, 0x6D956A)], [(0x6D9528, 0xD63940)], 23,
      {0x6D956A: 0xD63A30}),
    # the swap itself, as the command runs (x86 0x4367DB). The calls at
    # 0x9A7C52 / 0x9A7E28 go to another mod's cave, behind a test of the
    # stock byte 0x6DD43F (0x9C, never that mod's 0x9B): never reached.
    R('phs_swap', 10, [(0x4367DB, 0x4367E0), (0x9A7C13, 0x9A7E49)],
      [(0x4367DB, 0xF790C)], 23, {0x4367E0: 0xF7918},
      post={0x4367E0: 'regs[8] = eax;'}, fixed=('x8',),
      data={0x6DD43F: 0x9C}, never=(0x9A7C52, 0x9A7E28)),
    # 0x5CF873: `or` the status bits in, not `mov` (x0 is the record, w22
    # edx; w8 is dead after)
    W('phs_or', 9, 0x5CF873, 0x82576C,
      ['ldr     w8, [x0]', 'orr     w8, w8, w22', 'str     w8, [x0]'],
      clobbers=('w8',)),
]
