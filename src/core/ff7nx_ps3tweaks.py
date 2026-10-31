#!/usr/bin/env python3
"""ff7nx_ps3tweaks.py -- BUILD 868. Postscriptthree's Gameplay Tweaks
(PS3Tweaks.iro) options whose x86 jumps into a code cave, ported to ARM64.

ff7nx_hextarm carries the options that only change a constant. The rest
hook an x86 instruction with a `call`/`jmp` into new x86 code in a cave,
which the Switch port cannot run. For each such option this module knows:

  * the exact HEXT bytes it was written for (the hook and the cave code;
    any other bytes and the file is refused, so a different mod or version
    is never mistaken for it), and which bytes are the option's DATA (a
    table, a multiplier) that are read out of the file and carried over;
  * the translated words that do the same job, and the ARM64 entry in
    native/ps3tweaks.S (ps3tweaks.bin) that each one becomes a `bl` to.

Each port was written against the stock 1.02 exefs/main by reading the
recompiled function around the hook: which host register holds which x86
value at that word, and which flags the following words compute. The notes
in ps3tweaks.S give each site.

WHERE THE CODE GOES
===================
The translated body of x86 0x64B16A (6.7 KB of ARM), proven dead the same
way as the other passes' bodies: no branch or relocation reaches it in the
module but the map's own record, and no call, jump or pointer to it in
ff7_en. The whole blob is written by every option (same words each time);
each option's own table is written only by that option, so options combine.

Words come back in ff7nx_hextarm's form, (module offset, stock, new, note),
and go in through build.apply_hextarm with its stock-or-ours check.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import hashlib
import os
import struct

import nxpaths  # BUILD 755: project paths (src/nxpaths.py)

VERSION = 868
NATIVE = os.path.join(nxpaths.APP, 'native')

GUEST_TO_HOST = 0x10FC3A0
OWN_DEAD_X86 = 0x64B16A
# the blobs, each in a dead body of its own: (native/<name>.bin and .entry,
# that body's x86 function). A port uses the blobs its entries are in.
BLOBS = (('ps3tweaks', OWN_DEAD_X86),
         ('ps3tweaks2', 0x621E0E),
         ('ps3tweaks3', 0x6234D3),
         ('ps3tweaks4', 0x673282),
         ('ps3tweaks5', 0x648159),
         ('ps3tweaks6', 0x6CCDA5),
         ('ps3tweaks7', 0x62F3A5),
         ('ps3tweaks8', 0x641F9B),
         ('ps3tweaks9', 0x66EA5C),
         ('ps3tweaks10', 0x6725C7),
         ('ps3tweaks11', 0x418DD4),
         ('ps3tweaks12', 0x69913C),
         ('ps3tweaks13', 0x68D9C0),
         ('ps3tweaks14', 0x65C5D7),
         ('ps3tweaks15', 0x4189C7),
         ('ps3tweaks16', 0x41AD4B),
         ('ps3tweaks17', 0x5F549C),
         ('ps3tweaks18', 0x69649B),
         ('ps3tweaks19', 0x5F51B2),
         ('ps3tweaks20', 0x6ADD1B),
         ('ps3tweaks21', 0x62F822),
         ('ps3tweaks22', 0x62270B))
OWN_GUARD = 0x40
OWN_TAIL = 0x40

# trampolines in the blob: symbol -> the x86 function whose translated
# body it branches to. Resolved by every option (same words each time).
TRAMP = {'ap_orig': 0x5C7260, 'ap_master': 0x6CC0EA, 'nb_world_fn': 0x75E5C0,
         'sw_fn0': 0x433580, 'sw_fn1': 0x6F5B03,
         'tt_fn0': 0x43526A, 'tt_fn1': 0x4351BD, 'tt_fn2': 0x434347,
         'tt_fn3': 0x4320AC, 'tt_fn4': 0x4324B6,
         'md_fn0': 0x41963C, 'md_fn1': 0x6C6B06, 'fp_fn': 0x767218,
         'mp_fn': 0x5CCE7B, 'us_fn0': 0x419457, 'us_fn1': 0x6F5B03,
         'us_fn2': 0x6C6B06, 'us_fn3': 0x419A8F,
         'bd_fn': 0x6F9739}

# data areas in the blob: symbol -> bytes. Written only by their option.
DATA = {'lc_table': 192, 'sm_mult': 4, 'sl_lvl': 4, 'dm_mask': 4, 'pl_mode': 4, 'wm_easy': 4, 'sw_sensed': 4,
        'es_den': 4, 'es_num': 4, 'sc_char': 4, 'ir_text': 8}


class Port(object):
    """One cave option. `code` is {x86 va: exact bytes}; `data` is
    {x86 va: (symbol, parser)}; `sites` is [(x86 va the hook replaces, ARM
    offset, stock word there, entry symbol)]."""

    def __init__(self, name, code, data, sites, inline=None, over=()):
        self.name, self.code, self.data, self.sites = name, code, data, sites
        # over: lines a later file of the group writes again (code, not
        # data: `code` holds the later file's bytes)
        self.over = set(over)
        # inline(values) -> [(x86 va, ARM offset, stock, new)]: words that
        # need no cave
        self.inline = inline


def _h(s):
    return bytes.fromhex(s.replace(' ', ''))


def _level_table(blob):
    """The (s16 moment, u8 cap) list, checked as the x86 walks it."""
    if not blob or len(blob) % 3 or len(blob) > DATA['lc_table']:
        return None
    moments = [struct.unpack_from('<h', blob, i)[0]
               for i in range(0, len(blob), 3)]
    if moments[-1] != 0x7FFF:
        return None
    return blob


def _template(tmpl, size):
    """A parser for a cave line that must match `tmpl` (hex, `??` for the
    option's value bytes) and carries those bytes over."""
    parts = tmpl.split()

    def parse(blob):
        if len(blob) != len(parts):
            return None
        val = bytearray()
        for b, t in zip(blob, parts):
            if t == '??':
                val.append(b)
            elif b != int(t, 16):
                return None
        return bytes(val) if len(val) == size else None
    return parse


def _speed(blob):
    v = _template('3D 88 00 00 00 7E 15 0F 1F 40 00 50 31 C0 66 89 C8 99 B9'
                  ' ?? ?? ?? ?? F7 E1 8B C8 58 66 89 88 12 8B 9A 00 E9 78 7B'
                  ' C7 FF', 4)(blob)
    return v if v and 0 < struct.unpack('<I', v)[0] <= 0xFFFF else None


def _steal_level(blob):
    """`add eax, imm8` (sign-extended): the modifier as s32."""
    if len(blob) != 3 or blob[:2] != b'\x83\xC0':
        return None
    return struct.pack('<i', struct.unpack('<b', blob[2:])[0])


def _steal_divisor(blob):
    """`mov ecx, imm32`, the idiv's divisor."""
    if len(blob) != 5 or blob[0] != 0xB9:
        return None
    d = struct.unpack_from('<I', blob, 1)[0]
    return struct.pack('<I', d) if 0 < d <= 0xFFFF else None


def _steal_inline(values, luck):
    """The level modifier (unless the luck cave applies it) and the divisor.

    x86 0x5CA932 `cdq; idiv ecx` with ecx = 100 is, in ARM, a magic
    multiply by 1/100 at +0x80FA80..+0x80FAA4 that leaves the quotient in
    w22 and 100 in w10 for the `msub` at +0x80FAA8 (the remainder, edx). A
    different divisor: `mov w10, #d; sdiv w22, w9, w10; b +0x80FAA8` (w9 is
    the dividend; edx:eax is its sign extension, so a 32-bit divide is the
    same)."""
    import a64 as A
    out = []
    lvl = struct.unpack('<i', values['sl_lvl'])[0]
    if not luck and lvl != 0x28:
        word = (A.add_imm(8, 8, lvl) if lvl >= 0 else A.sub_imm(8, 8, -lvl))
        out.append((0x5CA8F5, 0x80F980, 0x1100A108, word))
    d = struct.unpack('<I', values['sl_div'])[0]
    if d != 100:
        out += [(0x5CA933, 0x80FA80, 0xD29AE168, A.movz(10, d)),
                (0x5CA933, 0x80FA84, 0xF2AE1468, 0x1ACA0D36),  # sdiv w22,w9,w10
                (0x5CA933, 0x80FA88, 0xF2C147A8, A.b(0x80FA88, 0x80FAA8))]
    return out


AP_SHARE = (
    '83 C0 40 8B 15 C4 E2 99 00 C1 E2 08 50 8B 45 FC 69 C0 84 00 00 00 05 A9'
    ' FD DB 00 8A 28 48 8A 08 8B C1 31 C9 88 E1 6B C9 24 81 C1 F1 CC DB 00 8A'
    ' 09 83 E1 03 50 8B C1 52 F7 EA 5A 8B C8 58 25 FF 00 00 00 6B C0 2C 05 36'
    ' E7 DB 00 8A 00 83 E0 03 F7 EA 8B D0 58 51 8B 08 81 E1 00 FF FF FF 81 F9'
    ' 00 FF FF FF 74 7D 0F 1F 40 00 8B 08 01 D1 52 51 50 31 C0 88 C8 6B C0 14'
    ' 05 60 DF DB 00 31 C9 31 D2 52 66 8B 14 08 66 81 FA FF FF 75 0A 0F 1F 40'
    ' 00 5A EB 0E 0F 1F 00 83 C4 04 80 C1 02 80 F9 08 75 DF 85 D2 75 0C 0F 1F'
    ' 40 00 58 59 5A EB 34 0F 1F 00 69 D2 00 64 00 00 58 59 39 D1 5A 7D 0B 0F'
    ' 1F 40 00 89 08 EB 1C 0F 1F 00 81 C9 00 FF FF FF 89 08 50 52 81 E1 FF 00'
    ' 00 00 51 E8 0A 4E D2 FF 59 5A 58 83 C0 04 8B 4D FC 69 C9 84 00 00 00 81'
    ' C1 0C FE DB 00 29 C1 83 F9 20 75 05 0F 1F 40 00 5A 85 C9 0F 85 4D FF FF'
    ' FF E8 52 FF C1 FF E9 1A FC C1 FF')


NOP = 0xD503201F
A64_B_ED1A8 = 0x14000005                    # b +0xED1BC, from +0xED1A8


def _mash(blob):
    v = _template('85 C0 74 13 0F 1F 40 00 C7 05 C0 11 D0 00 01 00 00 00 E9 56'
                  ' 28 CA FF A1 D4 85 9A 00 25 ?? ?? ?? ?? 3D ?? ?? ?? ?? 0F 85'
                  ' 41 28 CA FF A1 C8 3C DC 00 83 E0 01 A3 C0 11 D0 00 E9 2F 28'
                  ' CA FF', 8)(blob)
    return v[:4] if v and v[:4] == v[4:] else None


_LEADER_TAIL = ('8A 4D F4 C0 E1 03 80 25 96 0C DC 00 E7 08 0D 96 0C DC 00 8B'
                ' 45 F4 8B E5 EB 74')
_LEADER = {
    'EB 78' + ' 90' * 20: 2,                                    # Cid
    'E9 93 00 00 00' + ' 90' * 17: 0,                           # Cloud
    'E9 84 00 00 00' + ' 90' * 17: 1,                           # Tifa
    'A0 30 02 DC 00 3C 02 0F 84 7C 00 00 00 3C 08 74 69 E9 82 00 00 00': 3,
}


def _leader(blob):
    for head, mode in _LEADER.items():
        if blob == _h(head + ' ' + _LEADER_TAIL):
            return struct.pack('<I', mode)
    return None


_NO_BATTLE_MUSIC = {
    0x4093C6: _h('A1 CC 0D CC 00 85 C0 74 1F 31 C9 51 51 51 51 51 51 51 3C 07'
                 ' 75 07 31 C0 48 90 90 90 90 50'),
    0x40827B: _h('66 0F BE 05 4C 16 CC 00 66 09 C8 83 3D 84 0D CC 00 03 74 0B'
                 ' 83 3D CC 0D CC 00 07 75 02 0C 20 66 A3 8A 95 DB 00 90 31 C0'
                 ' A2 4C 16 CC 00' + ' 50' * 8),
}


_WAIT_HEAD = ('0F 85 A9 0D CF FF E8 0F 00 00 00 83 F8 01 75 F0 90 90 E9 E9 0A'
              ' CF FF 31 C0 C3 B9 14 8B 9A 00 66 8B 41 FE 66 3D 00 00 74 2C 8A'
              ' 25 D4 85 9A 00 80 E4 0C 80 FC 0C 75 15 81 F9 9D 8B 9A 00 72 16')
_WAIT_TAIL = ('66 8B 01 66 3D FF FF 74 C2 83 C1 44 81 F9 78 8D 9A 00 75 BF 31 C0'
              ' 40 C3')
_WAIT_EASY = 'A1 A6 88 9A 00 83 E0 08 83 F8 00 74 14'


def _wait(blob):
    """The x86 0x7415FC check: 1 for the Easy Run version, 0 for plain."""
    for easy, mid in ((1, _WAIT_EASY), (0, ' '.join(['90'] * 13))):
        if blob == _h(' '.join((_WAIT_HEAD, mid, _WAIT_TAIL))):
            return struct.pack('<I', easy)
    return None


def _wait_easy(blob):
    v = _wait(blob)
    return v if v == struct.pack('<I', 1) else None


def _digest(sha256, value):
    """A parser for a long cave the port knows by its SHA-256: `value`
    (bytes) for exactly those bytes."""
    return lambda blob: value if hashlib.sha256(
        bytes(blob)).hexdigest() == sha256 else None


def _fixed(words):
    """inline() for an option with no values: these words, as they are."""
    return lambda _v: list(words)


# UnlimitedAutoSense: the words every variant shares
_US_COMMON = {
    0x419BB2: _h('E9 BE C4 4F 00 90 90'),
    0x419BC2: _h('25 FF FF FF FF'),
    0x419BE2: _h('25 FF FF FF FF'),
    0x419BEF: _h('89 45 EC 90'),
    0x419BF6: _h('81 E2 FF FF FF FF'),
    0x5CA10F: _h('81 B9 0C B1 9A 00 FF FF FF 7F'),
}


_US_PLAIN = {
    0x916075: _h('C7 45 A8 00 00 00 00 A1 9C 3C DC 00 83 F8 04 0F 8C 2F'
                 ' 3B B0 FF 83 F9 0A 74 0D 0F 1F 40 00 83 F9 06 0F 85 1D'
                 ' 3B B0 FF 83 E8 04 53 BB 68 00 00 00 F7 E3 05 A8 B2 9A'
                 ' 00 83 F9 06 74 06 0F 1F 00 83 C0 04 8B 18 89 5D EC 5B'
                 ' E9 F7 3A B0 FF'),
}


_US_SHOW = {
    0x916075: _h('C7 45 A8 00 00 00 00 A1 9C 3C DC 00 83 F8 04 0F 8C 2F'
                 ' 3B B0 FF 83 F9 0A 74 0D 0F 1F 40 00 83 F9 06 0F 85 1D'
                 ' 3B B0 FF 83 E8 04 53 BB 68 00 00 00 F7 E3 05 A8 B2 9A'
                 ' 00 83 F9 06 74 06 0F 1F 00 83 C0 04 8B 18 89 5D EC 5B'
                 ' EB 15 90 90 90'),
    0x9160D4: _h('A1 9C 3C DC 00 83 E8 04 6B C0 10 05 94 87 9A 00 31 C9'
                 ' 8A 08 69 C9 B8 00 00 00 81 C1 24 8F 9A 00 A0 D4 85 9A'
                 ' 00 80 7D 04 F5 90 74 11 3C 80 E9 19 03 00 00 84 C0 0F'
                 ' 84 AA 3A B0 FF EB 05 E9 A3 3A B0 FF 24 60 88 C3 3C 40'
                 ' 7D 35 3C 20 0F 85 93 3A B0 FF 51 6A 00 6A 09 6A 08 E8'
                 ' 25 33 B0 FF 83 C4 0C 6A 07 50 68 00 01 00 00 68 50 01'
                 ' 00 00 E8 BC F9 DD FF 83 C4 10 59 51 90 90 83 C1 18 EB'
                 ' 49 51 6A 00 6A 05 6A 08 E8 F8 32 B0 FF 83 C4 0C 6A 07'
                 ' 50 68 00 01 00 00 6A 38 90 90 90 E8 8F F9 DD FF E9 A6'
                 ' 00 00 00 31 D2 8A 04 11 24 C0 3C 80 74 10 0F 1F 40 00'
                 ' 42 83 FA 04 75 ED 5A E9 DF 00 00 00 83 C1 04 01 D1 01'
                 ' D1 5A 31 C0 66 8B 01 31 C9 66 3D FF FF 0F 84 C6 00 00'
                 ' 00 66 3D 80 00 7C 47 0F 1F 40 00 66 2D 80 00 41 66 3D'
                 ' 80 00 7C 38 0F 1F 40 00 66 2D 80 00 41 66 3D 20 00 7C'
                 ' 29 0F 1F 40 00 66 2D 20 00 41 66 3D 20 00 7C 1A 0F 1F'
                 ' 40 00 66 2D 20 00 68 17 D9 CE 3D 6A 07 50 E8 11 09 DB'
                 ' FF EB 0C 0F 1F 00 51 50 6A 0A E8 54 32 B0 FF 83 C4 0C'
                 ' 6A 07 50 68 00 01 00 00 EB 53 90 90 90 E8 EB F8 DD FF'
                 ' 83 C4 10 EB 56 90 90 83 C4 10 59 51 A1 9C 3C DC 00 6B'
                 ' C0 44 8A 80 39 8B 9A 00 3C 01 74 06 52 E9 3D FF FF FF'
                 ' 6A 07 68 54 62 91 00 68 00 01 00 00 6A 7F E8 B4 F8 DD'
                 ' FF 83 C4 10 EB 1F 21 4C 52 45 41 44 59 00 33 54 4F 4C'
                 ' 45 4E FF 80 FB 40 7D 07 68 A5 01 00 00 EB A4 6A 7F EB'
                 ' A0 59 8B C3 31 DB 3C 60 0F 85 39 39 B0 FF E9 A1 FE FF'
                 ' FF'),
    0x916420: _h('77 06 0F 1F 40 00 30 C0 8A 15 70 3C DC 00 80 FA 05 74'
                 ' 12 0F 1F 40 00 80 FA 11 74 09 0F 1F 40 00 EB 05 0F 1F'
                 ' 00 0C 40 80 FA 09 74 09 0F 1F 40 00 EB 05 0F 1F 00 0C'
                 ' 20 50 80 FA 02 74 5F 0F 1F 40 00 80 FA 15 74 56 0F 1F'
                 ' 40 00 8B 1D 7C 3C DC 00 8B C3 69 DB 40 04 00 00 81 C3'
                 ' E9 A4 DB 00 69 C0 00 07 00 00 05 A0 20 DC 00 31 D2 8A'
                 ' 10 6B D2 04 02 50 04 6B D2 06 01 D3 8A 13 80 E2 08 58'
                 ' 84 D2 74 06 0F 1F 40 00 0C 40 8A 13 80 E2 04 84 D2 74'
                 ' 06 0F 1F 40 00 0C 20 E9 4B FC FF FF A1 7C 3C DC 00 8B'
                 ' D8 69 DB 40 04 00 00 81 C3 A7 A5 DB 00 69 C0 00 07 00'
                 ' 00 05 10 21 DC 00 8B 50 04 6B D2 03 03 10 8B 40 14 6B'
                 ' C0 03 01 C2 6B D2 08 01 D3 EB A7'),
}


_US_AUTO = {
    0x4198D2: _h('90 90 90 90 90 90'),
}


def _us(*parts):
    out = {}
    for d in parts:
        out.update(d)
    return out


# the hooked words of UnlimitedAutoSense: the formatter's number field,
# the Sense limit (both every variant), Auto's sense-flag test, and the
# formatter's two calls (the steal / morph texts)
_US_CASE = (0x419BB2, 0x86698, 0x320003E8)               # mov w8, #1
_US_LIMIT = [(0x5CA10F, 0x80A578, 0x53047D08, 'us_limit')]  # lsr w8, w8, #4
_US_CALLS = [
    (0x41996F, 0x8557C, 0x94000355, 'us_call_a'),   # bl x86 0x419A8F (0x41996F)
    (0x4199F0, 0x85A24, 0x9400022B, 'us_call_b'),   # bl x86 0x419A8F (0x4199F0)
]
_US_AUTO_WORD = [(0x4198D2, 0x8530C, 0x363017C8, NOP)]   # tbz w8, #6 -> nop


# BreakDamageHPLimit: the bytes every variant shares
_BD_COMMON = {
    0x5BB6FD: _h('E9 70 B9 3E 00'),
    0x5BB773: _h('8B 4D 10 90'),
    0x5BB9E5: _h('8B 45 08 90'),
    0x5BBA03: _h('8B 45 08 90'),
    0x5BBA0F: _h('89 45 08 90'),
    0x5DA58E: _h('E9 C7 CA 3C 00'),
}
_BD_CONDITIONALNOHP = {
    0x5BB96B: _h('BA 04'),
    0x5BB9DF: _h('83 7D FC 05'),
    0x5BB9F4: _h('B8 04'),
    0x5BBA13: _h('BA 04'),
    0x5BBA27: _h('BA 04'),
    0x5BBA4A: _h('83 7D FC 04'),
    0x5BBA70: _h('B8 05'),
    0x5DAA92: _h('E9 69 C5 3C 00 90 90'),
    0x9A7000: _h('31 C0 31 DB A0 08 E3 99 00 3C 02 7F 3D 0F 1F 40 00 05'
                 ' 30 02 DC 00 8A 18 80 FB 06 74 1D 0F 1F 40 00 B8 84 00'
                 ' 00 00 F7 E3 05 AE FD DB 00 8B 18 66 81 FB 00 02 7C 14'
                 ' 0F 1F 40 00 31 C0 31 DB C7 45 FC 9F 86 01 00 E9 4F 3A'
                 ' C3 FF 31 C0 31 DB C7 45 FC 0F 27 00 00 E9 3F 3A C3 FF'
                 ' A1 1C E5 99 00 C1 E8 10 C1 E0 0F 66 03 45 10 66 89 42'
                 ' 04 E9 21 35 C3 FF 50 66 8B 48 0E 81 E1 FF FF 00 00 8B'
                 ' 40 14 25 FF FF 00 00 C1 E8 0F C1 E0 10 01 C1 58 51 E9'
                 ' 6E 46 C1 FF'),
}
_BD_CONDITIONALANDHP = {
    0x5BB96B: _h('BA 04'),
    0x5BB9DF: _h('83 7D FC 05'),
    0x5BB9F4: _h('B8 04'),
    0x5BBA13: _h('BA 04'),
    0x5BBA27: _h('BA 04'),
    0x5BBA4A: _h('83 7D FC 04'),
    0x5BBA70: _h('B8 05'),
    0x5C7AE5: _h('FF 7F'),
    0x5CE817: _h('FF 7F'),
    0x5D0675: _h('FF 7F'),
    0x5D067C: _h('66 C7 41 16 FF 7F'),
    0x5DAA92: _h('E9 69 C5 3C 00 90 90'),
    0x5F1AA9: _h('FF 7F'),
    0x5F214A: _h('FF 7F'),
    0x653633: _h('FF 7F'),
    0x6C5A78: _h('7D'),
    0x6C5D1B: _h('05'),
    0x6C5D58: _h('05'),
    0x6C5D78: _h('61'),
    0x6C5E70: _h('5A'),
    0x6C62C0: _h('83 C0 1C'),
    0x6C62D1: _h('66 C7 45 F0 7D 00'),
    0x6C64F9: _h('6A 05'),
    0x6C6516: _h('83 C2 1B'),
    0x6C6534: _h('6A 05'),
    0x6C6551: _h('83 C2 5F'),
    0x6C6646: _h('83 C0 58'),
    0x6DC983: _h('66 C7 45 E8 18 01'),
    0x6DC999: _h('66 C7 45 EC 90 00'),
    0x6DCA07: _h('66 C7 45 E8 AC 01'),
    0x6DCA1E: _h('66 C7 45 EC 2C 00'),
    0x6DD0E4: _h('68 28 01 00 00'),
    0x6DD109: _h('68 AE 01 00 00'),
    0x6DD855: _h('6A 05'),
    0x6DD874: _h('68 18 01 00 00'),
    0x6DD8D9: _h('68 5D 01 00 00'),
    0x6DD939: _h('6A 05'),
    0x6DD95B: _h('68 64 01 00 00'),
    0x6E1473: _h('05'),
    0x6E1492: _h('7A 01'),
    0x6E14F7: _h('C0 01'),
    0x6E1518: _h('05'),
    0x6E153B: _h('C8 01'),
    0x6E154B: _h('7C 01'),
    0x6E1561: _h('90'),
    0x6E46EB: _h('FF 7F'),
    0x705717: _h('08 01'),
    0x9A7000: _h('31 C0 31 DB A0 08 E3 99 00 3C 02 7F 3D 0F 1F 40 00 05'
                 ' 30 02 DC 00 8A 18 80 FB 06 74 1D 0F 1F 40 00 B8 84 00'
                 ' 00 00 F7 E3 05 AE FD DB 00 8B 18 66 81 FB 00 02 7C 14'
                 ' 0F 1F 40 00 31 C0 31 DB C7 45 FC 9F 86 01 00 E9 4F 3A'
                 ' C3 FF 31 C0 31 DB C7 45 FC 0F 27 00 00 E9 3F 3A C3 FF'
                 ' A1 1C E5 99 00 C1 E8 10 C1 E0 0F 66 03 45 10 66 89 42'
                 ' 04 E9 21 35 C3 FF 50 66 8B 48 0E 81 E1 FF FF 00 00 8B'
                 ' 40 14 25 FF FF 00 00 C1 E8 0F C1 E0 10 01 C1 58 51 E9'
                 ' 6E 46 C1 FF'),
}
_BD_UNCONDITIONALNOHP = {
    0x5BB96B: _h('BA 04'),
    0x5BB9DF: _h('83 7D FC 05'),
    0x5BB9F4: _h('B8 04'),
    0x5BBA13: _h('BA 04'),
    0x5BBA27: _h('BA 04'),
    0x5BBA4A: _h('83 7D FC 04'),
    0x5BBA70: _h('B8 05'),
    0x5DAA92: _h('C7 45 FC 9F 86 01 00'),
    0x9A7000: _h('31 C0 31 DB A0 08 E3 99 00 3C 02 7F 3D 0F 1F 40 00 05'
                 ' 30 02 DC 00 8A 18 80 FB 06 74 1D 0F 1F 40 00 B8 84 00'
                 ' 00 00 F7 E3 05 AE FD DB 00 8B 18 66 81 FB 00 02 7C 14'
                 ' 0F 1F 40 00 31 C0 31 DB C7 45 FC 9F 86 01 00 E9 4F 3A'
                 ' C3 FF 31 C0 31 DB C7 45 FC 0F 27 00 00 E9 3F 3A C3 FF'
                 ' A1 1C E5 99 00 C1 E8 10 C1 E0 0F 66 03 45 10 66 89 42'
                 ' 04 E9 21 35 C3 FF 50 66 8B 48 0E 81 E1 FF FF 00 00 8B'
                 ' 40 14 25 FF FF 00 00 C1 E8 0F C1 E0 10 01 C1 58 51 E9'
                 ' 6E 46 C1 FF'),
}
_BD_UNCONDITIONALANDHP = {
    0x5BB96B: _h('BA 04'),
    0x5BB9DF: _h('83 7D FC 05'),
    0x5BB9F4: _h('B8 04'),
    0x5BBA13: _h('BA 04'),
    0x5BBA27: _h('BA 04'),
    0x5BBA4A: _h('83 7D FC 04'),
    0x5BBA70: _h('B8 05'),
    0x5C7AE5: _h('FF 7F'),
    0x5CE817: _h('FF 7F'),
    0x5D0675: _h('FF 7F'),
    0x5D067C: _h('66 C7 41 16 FF 7F'),
    0x5DAA92: _h('C7 45 FC 9F 86 01 00'),
    0x5F1AA9: _h('FF 7F'),
    0x5F214A: _h('FF 7F'),
    0x653633: _h('FF 7F'),
    0x6C5A78: _h('7D'),
    0x6C5D1B: _h('05'),
    0x6C5D58: _h('05'),
    0x6C5D78: _h('61'),
    0x6C5E70: _h('5A'),
    0x6C62C0: _h('83 C0 1C'),
    0x6C62D1: _h('66 C7 45 F0 7D 00'),
    0x6C64F9: _h('6A 05'),
    0x6C6516: _h('83 C2 1B'),
    0x6C6534: _h('6A 05'),
    0x6C6551: _h('83 C2 5F'),
    0x6C6646: _h('83 C0 58'),
    0x6DC983: _h('66 C7 45 E8 18 01'),
    0x6DC999: _h('66 C7 45 EC 90 00'),
    0x6DCA07: _h('66 C7 45 E8 AC 01'),
    0x6DCA1E: _h('66 C7 45 EC 2C 00'),
    0x6DD0E4: _h('68 28 01 00 00'),
    0x6DD109: _h('68 AE 01 00 00'),
    0x6DD855: _h('6A 05'),
    0x6DD874: _h('68 18 01 00 00'),
    0x6DD8D9: _h('68 5D 01 00 00'),
    0x6DD939: _h('6A 05'),
    0x6DD95B: _h('68 64 01 00 00'),
    0x6E46EB: _h('FF 7F'),
    0x9A7000: _h('31 C0 31 DB A0 08 E3 99 00 3C 02 7F 3D 0F 1F 40 00 05'
                 ' 30 02 DC 00 8A 18 80 FB 06 74 1D 0F 1F 40 00 B8 84 00'
                 ' 00 00 F7 E3 05 AE FD DB 00 8B 18 66 81 FB 00 02 7C 14'
                 ' 0F 1F 40 00 31 C0 31 DB C7 45 FC 9F 86 01 00 E9 4F 3A'
                 ' C3 FF 31 C0 31 DB C7 45 FC 0F 27 00 00 E9 3F 3A C3 FF'
                 ' A1 1C E5 99 00 C1 E8 10 C1 E0 0F 66 03 45 10 66 89 42'
                 ' 04 E9 21 35 C3 FF 50 66 8B 48 0E 81 E1 FF FF 00 00 8B'
                 ' 40 14 25 FF FF 00 00 C1 E8 0F C1 E0 10 01 C1 58 51 E9'
                 ' 6E 46 C1 FF'),
}
_BD_UNLIMITEDDAMAGE = {
    0x5BB96B: _h('BA 08'),
    0x5BB9DF: _h('83 7D FC 09'),
    0x5BB9F4: _h('B8 08'),
    0x5BBA13: _h('BA 08'),
    0x5BBA27: _h('BA 08'),
    0x5BBA4A: _h('83 7D FC 08'),
    0x5BBA70: _h('B8 09'),
    0x5DAA92: _h('C7 45 FC F0 FF FF 1F'),
    0x9A7000: _h('31 C0 31 DB A0 08 E3 99 00 3C 02 7F 3D 0F 1F 40 00 05'
                 ' 30 02 DC 00 8A 18 80 FB 06 74 1D 0F 1F 40 00 B8 84 00'
                 ' 00 00 F7 E3 05 AE FD DB 00 8B 18 66 81 FB 00 02 7C 14'
                 ' 0F 1F 40 00 31 C0 31 DB C7 45 FC 9F 86 01 00 E9 4F 3A'
                 ' C3 FF 31 C0 31 DB C7 45 FC 0F 27 00 00 E9 3F 3A C3 FF'
                 ' A1 1C E5 99 00 C1 E8 10 C1 E0 03 66 03 45 10 66 89 42'
                 ' 04 E9 21 35 C3 FF 50 66 8B 48 0E 81 E1 FF FF 00 00 8B'
                 ' 40 14 25 FF FF 00 00 C1 E8 03 C1 E0 10 01 C1 58 51 E9'
                 ' 6E 46 C1 FF'),
}
_BD_UNLIMITEDDAMAGEANDHP = {
    0x5BB96B: _h('BA 08'),
    0x5BB9DF: _h('83 7D FC 09'),
    0x5BB9F4: _h('B8 08'),
    0x5BBA13: _h('BA 08'),
    0x5BBA27: _h('BA 08'),
    0x5BBA4A: _h('83 7D FC 08'),
    0x5BBA70: _h('B8 09'),
    0x5C7AE5: _h('FF 7F'),
    0x5CE817: _h('FF 7F'),
    0x5DAA90: _h('90 90'),
    0x5DAA92: _h('C7 45 FC F0 FF FF 1F'),
    0x5F1AA9: _h('FF 7F'),
    0x5F214A: _h('FF 7F'),
    0x653633: _h('FF 7F'),
    0x6C5A78: _h('7D'),
    0x6C5D1B: _h('05'),
    0x6C5D58: _h('05'),
    0x6C5D78: _h('61'),
    0x6C5E70: _h('5A'),
    0x6C62C0: _h('83 C0 1C'),
    0x6C62D1: _h('66 C7 45 F0 7D 00'),
    0x6C64F9: _h('6A 05'),
    0x6C6516: _h('83 C2 1B'),
    0x6C6534: _h('6A 05'),
    0x6C6551: _h('83 C2 5F'),
    0x6C6646: _h('83 C0 58'),
    0x6DC983: _h('66 C7 45 E8 18 01'),
    0x6DC999: _h('66 C7 45 EC 90 00'),
    0x6DCA07: _h('66 C7 45 E8 AC 01'),
    0x6DCA1E: _h('66 C7 45 EC 2C 00'),
    0x6DD0E4: _h('68 28 01 00 00'),
    0x6DD109: _h('68 AE 01 00 00'),
    0x6DD855: _h('6A 05'),
    0x6DD874: _h('68 18 01 00 00'),
    0x6DD8D9: _h('68 5D 01 00 00'),
    0x6DD939: _h('6A 05'),
    0x6DD95B: _h('68 64 01 00 00'),
    0x6E46EB: _h('FF 7F'),
    0x705717: _h('08 01'),
    0x9A7000: _h('31 C0 31 DB A0 08 E3 99 00 3C 02 7F 3D 0F 1F 40 00 05'
                 ' 30 02 DC 00 8A 18 80 FB 06 74 1D 0F 1F 40 00 B8 84 00'
                 ' 00 00 F7 E3 05 AE FD DB 00 8B 18 66 81 FB 00 02 7C 14'
                 ' 0F 1F 40 00 31 C0 31 DB C7 45 FC 9F 86 01 00 E9 4F 3A'
                 ' C3 FF 31 C0 31 DB C7 45 FC 0F 27 00 00 E9 3F 3A C3 FF'
                 ' A1 1C E5 99 00 C1 E8 10 C1 E0 03 66 03 45 10 66 89 42'
                 ' 04 E9 21 35 C3 FF 50 66 8B 48 0E 81 E1 FF FF 00 00 8B'
                 ' 40 14 25 FF FF 00 00 C1 E8 03 C1 E0 10 01 C1 58 51 E9'
                 ' 6E 46 C1 FF'),
}


# Break Damage/HP Limit, by part. Sites are (x86, ARM, stock, entry);
# words (x86, ARM, stock, new).
_BD_SITES = [
    (0x5BB6FD, 0x7C5A90, 0x79400008, 'bd_push'),     # ldrh w8, [x0]
    (0x5DA58E, 0x84E314, 0x79400014, 'bd_store'),    # ldrh w20, [x0]
]
_BD_WORDS = [
    # `mov cx, [ebp+0x10]` -> `mov ecx, [ebp+0x10]`
    (0x5BB773, 0x7C5CDC, 0x79400008, 0xB9400008),    # ldr w8, [x0]
    (0x5BB773, 0x7C5CE0, 0x79000AE8, 0xB90006E8),    # str w8, [x23, #4]
]
_BD_DIGITS = (0x5BB778, 0x7C5D08, 0x940002D2)        # bl x86 0x5BB9C9
_BD_CAP = (0x5DAA92, 0x84B650, 0x5284E1E8)           # mov w8, #0x270F
# 9,999 -> 32,767 in the HP limits: `cmp r, 0x270F` as SF / OF (-0x270F
# and 0x270E), and two where the constant is in a shared register
_BD_HP_WORDS = [
    (0x5C7AE5, 0x7F92D4, 0x1284E1C8, 0x128FFFC8),    # mov w8, #-0x7FFF
    (0x5C7AE5, 0x7F92D8, 0x5284E1CC, 0x528FFFCC),    # mov w12, #0x7FFE
    (0x5CE817, 0x816C54, 0x5284E1CA, 0x528FFFCA),    # mov w10, #0x7FFE
    (0x5F1AA9, 0x8A6BA4, 0x1284E1C9, 0x128FFFC9),    # mov w9, #-0x7FFF
    (0x5F1AA9, 0x8A6BA8, 0x5284E1CB, 0x528FFFCB),    # mov w11, #0x7FFE
    (0x5F214A, 0x89FB2C, 0x1284E1C8, 0x128FFFC8),    # mov w8, #-0x7FFF
    (0x5F214A, 0x89FB30, 0x5284E1CC, 0x528FFFCC),    # mov w12, #0x7FFE
    (0x6E46EB, 0xD82AB8, 0x1284E1C8, 0x128FFFC8),    # mov w8, #-0x7FFF
    (0x6E46EB, 0xD82ABC, 0x5284E1CB, 0x528FFFCB),    # mov w11, #0x7FFE
    # the HP displays: x positions, digit counts, label and bar places
    (0x6C5D78, 0xCE1714, 0x11015D19, 0x11018519),    # add w25, w8, #0x61
    (0x6C5E70, 0xCE1CD0, 0x11013D14, 0x11016914),    # add w20, w8, #0x5A
    (0x6C62C0, 0xCEB3CC, 0x11007917, 0x11007117),    # add w23, w8, #0x1C
    (0x6C6516, 0xCEBDF0, 0x1100791B, 0x11006D1B),    # add w27, w8, #0x1B
    (0x6C6551, 0xCEBF18, 0x11015D1A, 0x11017D1A),    # add w26, w8, #0x5F
    (0x6C6646, 0xCEC4BC, 0x11013D16, 0x11016116),    # add w22, w8, #0x58
    (0x6DC983, 0xD7279C, 0x52802408, 0x52802308),    # mov w8, #0x118
    (0x6DC999, 0xD727DC, 0x321D0FE8, 0x52801208),    # mov w8, #0x90
    (0x6DCA07, 0xD72978, 0x528033C8, 0x52803588),    # mov w8, #0x1AC
    (0x6DCA1E, 0xD729B8, 0x321E0FE8, 0x52800588),    # mov w8, #0x2C
    (0x6DD0E4, 0xD3EE58, 0x52802408, 0x52802508),    # mov w8, #0x128
    (0x6DD109, 0xD3EF38, 0x52803488, 0x528035C8),    # mov w8, #0x1AE
    (0x6DD8D9, 0xD4104C, 0x52802B08, 0x52802BA8),    # mov w8, #0x15D
    (0x6DD939, 0xD41248, 0x321E03E8, 0x528000A8),    # mov w8, #5
    (0x6DD95B, 0xD412EC, 0x52802C08, 0x52802C88),    # mov w8, #0x164
]
_BD_HP_SITES = [
    (0x5CE817, 0x816C48, 0x6B15011F, 'bd_cmp7fff'),  # cmp w8, w21
    (0x5CE817, 0x816C7C, 0x6B15011F, 'bd_cmp7fff'),  # cmp w8, w21
    (0x653633, 0xA49AA0, 0x5284E1E8, 'bd_hp88'),     # mov w8, #0x270F
    (0x6C5A78, 0xCE09BC, 0x79000017, 'bd_sth7d'),    # strh w23, [x0]
    (0x6C5D1B, 0xCE1564, 0xB9000017, 'bd_st5'),      # str w23, [x0]
    (0x6C5D58, 0xCE168C, 0xB9000017, 'bd_st5'),      # str w23, [x0]
    (0x6C62D1, 0xCEB41C, 0x79000019, 'bd_sth7d'),    # strh w25, [x0]
    (0x6C64F9, 0xCEBD70, 0xB9000018, 'bd_st5'),      # str w24, [x0]
    (0x6C6534, 0xCEBE94, 0xB9000018, 'bd_st5'),      # str w24, [x0]
    (0x6DD874, 0xD40F58, 0x97FE8BB2, 'bd_hp_call'),  # bl x86 0x6F9739
]
_BD_MENU = [(0x705717, 0xDCAC80, 0xB9000019, 'bd_st108')]  # str w25, [x0]
# the enemies' 9,999 HP limits (the ARM has the two branches swapped)
_BD_ENEMY_WORDS = [
    (0x5D0675, 0x826D80, 0x5284E1F4, 0x528FFFF4),    # mov w20, #0x7FFF
    (0x5D067C, 0x826D34, 0x5284E1E8, 0x528FFFE8),    # mov w8, #0x7FFF
]
# the battle healing submenu (ConditionalAndHP only)
_BD_HEAL_SITES = [
    (0x6E1491, 0xD47638, 0x97FE71FA, 'bd_heal_call'),  # bl x86 0x6F9739
    (0x6E1518, 0xD477A4, 0xB900001A, 'bd_st5'),        # str w26, [x0]
    (0x6E154B, 0xD47870, 0x79000018, 'bd_sth17c'),     # strh w24, [x0]
]
_BD_HEAL_WORDS = [
    (0x6E14F7, 0xD4772C, 0x528039C8, 0x52803808),    # mov w8, #0x1C0
    (0x6E153B, 0xD47844, 0x52803AC8, 0x52803908),    # mov w8, #0x1C8
    (0x6E1561, 0xD478AC, 0x321D0FE8, 0x52801208),    # mov w8, #0x90
]


# Two Accessories (TwoAccessories.txt; ESUICompat.txt changes a few of
# the same bytes: positions in the ESUI layout)
TWO_ACCESSORIES_CODE = {
    0x707383: _h('E9 E0 01 00 00 90'),
    0x705E4C: _h('EB 39'),
    0x70610F: _h('0F 8D 71 01 00 00'),
    0x707B64: _h('0F 8D B0 00 00 00'),
    0x7063F5: _h('E9 99 00 00 00 90'),
    0x70840D: _h('E9 76 01 00 00 90'),
    0x705CB8: _h('04'),
    0x705F28: _h('8D'),
    0x70629B: _h('04'),
    0x7062B7: _h('18'),
    0x706371: _h('25'),
    0x7063C1: _h('3D'),
    0x705F0E: _h('18'),
    0x7072A5: _h('7C'),
    0x7060D4: _h('18'),
    0x707571: _h('83 F8 02 74 03 83 EA 03 8B 45 FC 31 C9 8D 44 10 1E'
                 ' 8A 08 80 E1 FF 89 4D DC 30 08 90 8A 4D E8 08 08 90'
                 ' 90 8B 55 DC 80 FA FF 74 18'),
    0x7072BF: _h('8D 44 08 1E 90 90 90 83 3D C4 A5 DC 00 02 74 03 83'
                 ' E8 03 80 38 FF 74 18 31 D2 8A 10 C6 00 FF 66 81 C2'
                 ' 20 01'),
    0x7072FB: _h('90 90 90 90 90'),
    0x7064F3: _h('4A 4A 6B D2 03 E9 B4 00 00 00'),
    0x7065BE: _h('8B 04 8D 28 99 91 00 69 C0 84 00 00 00 31 C9 29 D0'
                 ' 8A 88 AA FD DB 00 80 F9 FF 74 56 68 CD CC 4C 3E 6A'
                 ' 07 6A 00 EB 21 90 90 90 90'),
    0x70630E: _h('83 C4 0C 6A 07 90 90 90 90 8B 4D E0 69 C9 84 00 00'
                 ' 00 8B 55 FC 33 C0 8A 44 0A 1C 05 80 00 00 00 50 E8'
                 ' E7 07 FC FF 83 C4 04 50 6A 0D 68 2F 01 00 00 E8 BF'
                 ' F7 FE FF 83 C4 0C EB 0B 8A 48 FD 43 80 F9 FF 75 49'
                 ' EB 7B 8B 4D E0 6B C9 42 D1 E1 8B 55 FC 33 C0 8A 44'
                 ' 0A 1D 66 05 00 01 50 E8 AC 07 FC FF 59 50 6A 25 68'
                 ' 2F 01 00 00 E8 86 F7 FE FF 83 C4 14 8B 4D E0 6B C9'
                 ' 42 D1 E1 8B 55 FC 8D 44 11 1E 31 C9 8A 08 31 DB 80'
                 ' F9 FF 74 2E 50 66 81 C1 20 01 68 CD CC 4C 3E 6A 07'
                 ' 51 E8 6E 07 FC FF 59 50 6B C3 18 83 C0 3D 50 68 2F'
                 ' 01 00 00 E8 43 F7 FE FF 83 C4 14 85 DB 75 08 58 31'
                 ' C9 E9 7A FF FF FF'),
    0x5CF93D: _h('88 4A 0F 8A 48 05 88 4A 4D 8B 55 D0 31 C0 8A 42 1B'
                 ' 50 8A 42 1E 50 FF 75 F8 E8 3C 09 00 00 58 59 50 E8'
                 ' 34 09 00 00 58 58 90 90 8B 45 DC 31 D2 89 50 50 66'
                 ' FF 48 52'),
    0x6C5460: _h('2C'),
    0x6C5731: _h('6B C9 42 D1 E1 81 C1 AA FD DB 00 89 4D D4 31 DB 80'
                 ' 39 FF 0F 84 7D 00 00 00 31 C0 8A 01 50 E8 2A 62 00'
                 ' 00 5A 89 45 DC C7 45 E0 00 00 00 00 EB 09 8B 55 E0'
                 ' 83 C2 01 89 55 E0 83 7D E0 02 7D 25 8B 45 DC 03 45'
                 ' E0 33 C9 8A 48 02 51 8B 55 DC 03 55 E0 33 C0 8A 02'
                 ' 50 8B 4D 08 51 E8 6C FA FF FF 83 C4 0C EB CC 8B 55'
                 ' DC 33 C0 66 8B 42 06 50 8B 4D DC 33 D2 8A 51 04 52'
                 ' 8B 45 08 50 E8 5B FB FF FF 83 C4 0C 8B 4D DC 8B 51'
                 ' 08 52 8B 45 08 50 E8 71 FC FF FF 83 C4 08 85 DB 75'
                 ' 0C 8B 4D D4 83 E9 03 43 E9 6A FF FF FF'),
    0x6C5933: _h('31 DB 8B 4D D4 80 39 19 75 36 8B 45 F0 83 C0 0F 89'
                 ' 45 F0 8B 4D EC 83 C1 0F 89 4D EC 8B 55 F8 83 C2 0F'
                 ' 89 55 F8 8B 45 E4 83 C0 0F 89 45 E4 8B 4D E8 83 C1'
                 ' 0F 89 4D E8 8B 55 FC 83 C2 0A 89 55 FC 85 DB 75 11'
                 ' 43 8B 4D D4 83 E9 03 EB B8 90 90 90 90 90 90 90 90'),
    0x6CC4B3: _h('EB 03 FF 45 FC 83 7D FC 03 0F 8D DC 00 00 00 8B 4D'
                 ' FC 8A 91 30 02 DC 00 80 FA FF 0F 84 C5 00 00 00 8B'
                 ' 45 FC 33 C9 8A 88 30 02 DC 00 8B 14 8D 28 99 91 00'
                 ' 6B D2 42 D1 E2 81 C2 AA FD DB 00 31 C0 80 3A 1B 75'
                 ' 02 04 03 80 7A FD 1B 75 02 04 03 84 C0 0F 84 8F 00'
                 ' 00 00 90 90 90 90 90 90 50'),
    0x717F89: _h('6B C9 42 D1 E1 31 DB 8D 81 AA FD DB 00 31 D2 80 38'
                 ' FF 74 16 8A 10 66 81 C2 20 01 80 CE 02 50 52 E8 4C'
                 ' 40 FB FF 5A 58 C6 00 FF 85 DB 75 1B 83 E8 03 43 EB'
                 ' D9 90 90 90 90 90 90 90 90 90 90 90 90 90 90 90 90'
                 ' 90 90 90 90 90'),
    0x704666: _h('83 C1 43 51 E8 94 14 FF FF 83 C4 10 90 90 90 90 90'
                 ' 6A 07 8B 55 F8 69 D2 84 00 00 00 8B 45 FC 33 C9 8A'
                 ' 4C 10 1D 81 C1 00 01 00 00 51 E8 84 24 FC FF 83 C4'
                 ' 04 50 8B 55 0C 83 C2 40 52 8B 45 08 83 C0 43 50 E8'
                 ' 55 14 FF FF 83 C4 10 31 DB 8B 4D F8 69 C9 84 00 00'
                 ' 00 8B 55 FC 31 C0 29 DA 8A 44 11 1E 3C FF 75 16 85'
                 ' DB 75 47 B3 03 EB E0 90 90 90 90 90 90 90 90 90 90'
                 ' 90 90 90 90 6A 07 05 20 01 00 00 50 E8 2D 24 FC FF'
                 ' 83 C4 04 50 8B 4D 0C 83 C1 7F 85 DB 74 03 83 C1 20'
                 ' 90 90 90 90 90 51 8B 55 08 83 C2 43 52 E8 F2 13 FF'
                 ' FF 83 C4 10 EB B5 6A 05 68 19 09 92 00 8B 45 0C 50'
                 ' FF 75 08 E8 DA 13 FF FF 83 C4 10 6A 05 68 28 09 92'
                 ' 00 8B 55 0C 83 C2 40 52 FF 75 08 E8 C1 13 FF FF 83'
                 ' C4 10 6A 05 68 37 09 92 00 8B 4D 0C 83 C1 7F 51 FF'
                 ' 75 08 E8 A8 13 FF FF 58 FF 75 08 83 44 24 04 20 83'
                 ' 6C 24 08 04'),
    0x703B29: _h('68 19 01 00 00 68 35 01 00 00'),
    0x7047A8: _h('83 C1 33'),
    0x7047EB: _h('83 C0 33'),
    0x701458: _h('68 15 01 00 00'),
    0x70011C: _h('83 C4 10 31 DB 8B 55 F8 69 D2 84 00 00 00 8B 45 FC'
                 ' 33 C9 29 D8 8A 4C 10 1E 80 F9 FF 75 08 85 DB 75 38'
                 ' B3 03 EB DF 6A 07 81 C1 20 01 00 00 51 E8 CB 69 FC'
                 ' FF 83 C4 04 50 8B 55 0C 81 C2 89 00 00 00 85 DB 74'
                 ' 03 83 C2 20 52 8B 45 08 83 C0 5D 50 E8 92 59 FF FF'
                 ' 83 C4 10 EB C4 6A 05 68 22 F4 91 00 8B 4D 0C 51 8B'
                 ' 55 08 83 C2 1B 52 E8 76 59 FF FF 83 C4 10 6A 05 68'
                 ' 38 F4 91 00 8B 45 0C 83 C0 43 50 8B 4D 08 83 C1 1B'
                 ' 51 E8 59 59 FF FF 83 C4 10 6A 05 68 4E F4 91 00 8B'
                 ' 55 0C 81 C2 89 00 00 00 52 8B 45 08 83 C0 1B 50 E8'
                 ' 39 59 FF FF 58 8B 45 08 83 C0 1B 50 83 6C 24 08 04'
                 ' 83 44 24 04 20 90 90 90 90 90 90 90 90 90 E8 19 59'
                 ' FF FF 83 C4 14 8B 4D F8 69 C9 84 00 00 00 8B 55 FC'
                 ' 8D 44 11 40 50'),
    0x5D02DB: _h('09 01'),
}
_TA_ESUI = {
    0x7062BD: _h('02'),
    0x705F14: _h('DA'),
    0x7060DA: _h('DA'),
    0x70016A: _h('40'),
    0x704668: _h('30'),
    0x7046A7: _h('30'),
    0x70470A: _h('30'),
}
# Two Accessories' words that need no cave: (x86, ARM, stock, new)
_TA_WORDS = [
    # battle status: `or` leaves the first accessory's statuses in place
    (0x5D02DB, 0x826F08, 0xB9000014, NOP),           # str w20, [x0]
    # the stats function's frame: 4 bytes more (its push esi folded in)
    (0x6C545E, 0xCDEF64, 0x5100B100, 0x5100C100),    # sub w0, w8, #0x2c/0x30
    # PHS: y 0x135 -> 0x115
    (0x701458, 0xDBEA48, 0x528026A8, 0x528022A8),    # mov w8, #0x135/0x115
    # status: (0x11B, 0x139) -> (0x135, 0x119)
    (0x703B29, 0xDC7958, 0x52802728, 0x52802328),    # mov w8, #0x139/0x119
    (0x703B2E, 0xDC7970, 0x52802368, 0x528026A8),    # mov w8, #0x11b/0x135
    # its names at x + 0x43 (not 0x5D), the accessory at y + 0x7F
    (0x704666, 0xDC88C0, 0x11017516, 0x11010D16),    # add w22, w8, #0x5d/0x43
    (0x7046A5, 0xDC89D8, 0x11017516, 0x11010D16),    # add w22, w8, #0x5d/0x43
    (0x7046F5, 0xDC8B30, 0x11020115, 0x1101FD15),    # add w21, w8, #0x80/0x7f
    (0x704705, 0xDC8B5C, 0x11017515, 0x11010D15),    # add w21, w8, #0x5d/0x43
    # its labels at x + 0 (not 0x1A; the port's native 0xA, -0x10)
    (0x704721, 0xDC8C10, 0x11006917, 0x11000117),    # add w23, w8, #0x1a/0
    (0x70473A, 0xDC8CB8, 0x11006917, 0x11000117),    # add w23, w8, #0x1a/0
    (0x70474F, 0xDC8D34, 0x11020114, 0x1101FD14),    # add w20, w8, #0x80/0x7f
    (0x704753, 0xDC8D6C, 0x52800349, 0x52800009),    # mov w9, #0x1a/0
    (0x704753, 0xDC8D70, 0x5280014A, 0x128001EA),    # mov w10, #0xa/-0x10
    # its materia at x + 0x33 (not 0x4D)
    (0x7047A8, 0xDC8EB0, 0x11013514, 0x1100CD14),    # add w20, w8, #0x4d/0x33
    (0x7047EB, 0xDC8FE4, 0x11013514, 0x1100CD14),    # add w20, w8, #0x4d/0x33
    # equip menu: an accessory slot past the second (3) is the accessory
    # case, in the item list, its description, the item filter, and when
    # equipping
    (0x705E4C, 0xDD238C, 0x54FFEBC1, NOP),           # b.ne
    (0x7063F5, 0xDD4A10, 0x54001801, NOP),           # b.ne
    (0x70840D, 0xDDC9B4, 0x54001661, NOP),           # b.ne
    (0x707383, 0xDD7358, 0x54000F21, NOP),           # b.ne
    # ... and no stats panel for either accessory
    (0x705F27, 0xDD2904, 0x5400BA80, 0x5400BA8A),    # b.eq -> b.ge
    (0x70610F, 0xDD2598, 0x5400D5E0, 0x5400D5EA),    # b.eq -> b.ge
    (0x707B64, 0xDD9010, 0x540014A0, 0x540014AA),    # b.eq -> b.ge
    # the slot cursor and labels 0x18 apart, four labels
    (0x7060D2, 0xDD24B0, 0x52800489, 0x52800309),    # mov w9, #0x24/0x18
    (0x7062B5, 0xDD4064, 0x52800457, 0x52800317),    # mov w23, #0x22/0x18
    (0x706298, 0xDD4058, 0x321F03F4, 0x52800074),    # mov w20, #2/3 (i < 4)
    (0x706298, 0xDD4168, 0x51000D09, 0x51001109),    # sub w9, w8, #3/4
    # the equipped names at y 0xD, 0x25, 0x3D (and 0x55, ta_equip_acc2)
    (0x706371, 0xDD4498, 0x528005E8, 0x528004A8),    # mov w8, #0x2f/0x25
    (0x70639B, 0xDD45F0, 0x52800A28, 0x528007A8),    # mov w8, #0x51/0x3d
]
# TwoAccessoriesESUICompat's: names further left, cursors further right,
# the labels higher
_TA_ESUI_WORDS = [
    (0x70016A, 0xDB8578, 0x11017515, 0x11010115),    # add w21, w8, #0x5d/0x40
    (0x704668, 0xDC88C0, 0x11017516, 0x1100C116),    # add w22, w8, #0x5d/0x30
    (0x7046A7, 0xDC89D8, 0x11017516, 0x1100C116),    # add w22, w8, #0x5d/0x30
    (0x70470A, 0xDC8B5C, 0x11017515, 0x1100C115),    # add w21, w8, #0x5d/0x30
    (0x705F14, 0xDD28C8, 0x528019E8, 0x52801B48),    # mov w8, #0xcf/0xda
    (0x7060DA, 0xDD24E4, 0x528019E8, 0x52801B48),    # mov w8, #0xcf/0xda
    (0x7062BD, 0xDD40DC, 0x1100351C, 0x1100091C),    # add w28, w8, #0xd/2
]


# its sites: ff7nx_ps3regions.TWO_ACCESSORIES' entries, hooks and stubs
_TA_SITES = [
    (0x5CF946, 0x825BD8, 0xB94016E8, 'x3e_ta_bstat_0'),      # ldr w8, [x23, #0x14]
    (0x6C572B, 0xCDFA70, 0x29422688, 'x3e_ta_stats_0'),      # ldp w8, w9, [x20, #0x10]
    (0x6C5933, 0xCE0268, 0xB9401688, 'x3e_ta_curse_0'),      # ldr w8, [x20, #0x14]
    (0x6CC4AC, 0xD0BDF0, 0x14000007, 'x3e_ta_bell_0'),       # b #0xd0be0c
    (0x6CC599, 0xD0BDF4, 0xB9401668, 'x3e_ta_bell_1'),       # ldr w8, [x19, #0x14]
    (0x717F86, 0xD08620, 0x0B140100, 'x3e_ta_clear_0'),      # add w0, w8, w20
    (0x705CB7, 0xDD182C, 0xB9000017, 'x3w_ta_rows'),         # str w23, [x0]
    (0x705F0C, 0xDD2898, 0x1B167D08, 'x3w_ta_cursor'),       # mul w8, w8, w22
    (0x70639B, 0xDD4644, 0xB9400014, 'x3h_ta_equip_acc2'),   # ldr w20, [x0]
    (0x7064F3, 0xDD4B18, 0xB9401708, 'x3e_ta_eqdesc_0'),     # ldr w8, [x24, #0x14]
    (0x707568, 0xDD735C, 0xB9401708, 'x3e_ta_equip_0'),      # ldr w8, [x24, #0x14]
    (0x70729E, 0xDD7A7C, 0x11049260, 'x3e_ta_remove_0'),     # add w0, w19, #0x124
]
# the PHS and status rows, by layout
_TA_ROWS = [
    (0x7001C5, 0xDB87B8, 0x29422668, 'x3h_ta_phs'),          # ldp w8, w9, [x19, #0x10]
    (0x704753, 0xDC8DA8, 0x29422668, 'x3h_ta_status'),       # ldp w8, w9, [x19, #0x10]
]
_TA_ROWS_ESUI = [s[:3] + (s[3] + '_e',) for s in _TA_ROWS]


def _ta_words(esui):
    """Two Accessories' words, the ESUI variant's in place of the rest's"""
    if not esui:
        return list(_TA_WORDS)
    over = {w[1]: w for w in _TA_ESUI_WORDS}
    return [over.pop(w[1], w) for w in _TA_WORDS] + list(over.values())


def _bd(variant, cap, digits, hp, menu=False, enemy=False, heal=False):
    sites = list(_BD_SITES) + [_BD_DIGITS + (digits,)]
    if digits == 'bd_digits9':
        # the Unlimited files keep 3 high bits fewer: >> 3 / << 3
        sites = [s[:3] + (s[3] + '3',) if s[3] in ('bd_push', 'bd_store')
                 else s for s in sites]
    words = list(_BD_WORDS)
    if cap.startswith('bd_'):
        sites.append(_BD_CAP + (cap,))
    else:
        words.append(_BD_CAP + (0x321C63E8,))        # mov w8, #0x1FFFFFF0
    if hp:
        sites += _BD_HP_SITES
        words += _BD_HP_WORDS
    if menu:
        sites += _BD_MENU
    if enemy:
        words += _BD_ENEMY_WORDS
    if heal:
        sites += _BD_HEAL_SITES
        words += _BD_HEAL_WORDS
    if cap == 'unlimited+hp':
        # the party's own caps run on into the 0x1FFFFFF0 one
        words.append((0x5DAA90, 0x84B644, 0x540000E1, NOP))
    code = {'Conditional': _BD_CONDITIONALNOHP,
            'Conditional, HP': _BD_CONDITIONALANDHP,
            'Unconditional': _BD_UNCONDITIONALNOHP,
            'Unconditional, HP': _BD_UNCONDITIONALANDHP,
            'Unlimited': _BD_UNLIMITEDDAMAGE,
            'Unlimited, HP': _BD_UNLIMITEDDAMAGEANDHP}[variant]
    return Port('Break Damage Limit (%s)' % variant,
                _us(_BD_COMMON, code), {}, sites,
                _fixed(words))


# Limit Overhaul: the region entries and hooks (ff7nx_ps3regions), and the
# one word that needs no cave (x86 0x434E11: the gauge is not cleared when
# the limit is used)
_LO_SITES = [
    (0x5CB3FE, 0x812AA8, 0x51001100, 'x3e_lo_sound_0'),      # sub w0, w8, #4
    (0x6E0ABD, 0xD443C4, 0xB9401688, 'x3e_lo_list_0'),       # ldr w8, [x20, #0x14]
    (0x6E0B5D, 0xD44678, 0xB9401288, 'x3e_lo_list_1'),       # ldr w8, [x20, #0x10]
    (0x6DA45E, 0xD67598, 0xB94016C8, 'x3e_lo_pick_0'),       # ldr w8, [x22, #0x14]
    (0x6DA4A9, 0xD678B8, 0x0B090D08, 'x3e_lo_pick_1'),       # add w8, w8, w9, lsl #3
    (0x6D9528, 0xD63940, 0x0B080128, 'x3e_lo_command_0'),    # add w8, w9, w8
    (0x6DE55A, 0xD41D74, 0x7100511F, 'x3e_lo_name_0'),       # cmp w8, #0x14
    (0x6DE704, 0xD42360, 0x0B080128, 'x3e_lo_name_1'),       # add w8, w9, w8
    (0x6DE584, 0xD41E48, 0x97CD0B32, 'x3w_lo_name_text'),    # bl #0x84b10
    (0x6D770F, 0xD3DA8C, 0x7103FD1F, 'x3e_lo_show_0'),       # cmp w8, #0xff
    (0x6D775A, 0xD3DB64, 0xB94016C8, 'x3e_lo_show_1'),       # ldr w8, [x22, #0x14]
    (0x6D87CA, 0xD5EDD4, 0xB94016A8, 'x3e_lo_use_0'),        # ldr w8, [x21, #0x14]
    (0x437A1B, 0xFAC70, 0xB9401688, 'x3e_lo_attack_0'),      # ldr w8, [x20, #0x14]
    (0x4346FD, 0xF13C0, 0x7900001F, 'x3h_lo_gauge_load'),    # strh wzr, [x0]
    (0x5C9357, 0x804F9C, 0x7900001F, 'x3h_lo_gauge_keep'),   # strh wzr, [x0]
]
_LO_WORDS = [(0x434E11, 0xF3D30, 0x7900001F, NOP)]           # strh wzr, [x0]


# LimitOverhaul.txt as it ships: the cave is its one line at 0x916A00
LIMIT_OVERHAUL_CODE = {
    0x6D9528: _h('8A 0C 10 88 0D 70 3C DC 00 8A 44 10 01 80 F9 1D 7C'
                 ' 10 31 C0 88 C8 83 E8 1D 6B C0 03 40 90 90 90 90 90'
                 ' A2 74 3C DC 00'),
    0x434E11: _h('90 90 90 90 90 90 90 90 90'),
    0x6DE584: _h('B8 38 A9 91 00'),
    0x437A1B: _h('E9 08 F5 4D 00 90'),
    0x5CB3FE: _h('E9 39 B6 34 00 90'),
    0x6E0ABD: _h('E9 8F 5F 23 00'),
    0x6DA45E: _h('E9 B9 C6 23 00 90'),
    0x6D9545: _h('E9 77 D6 23 00'),
    0x6DE55A: _h('E9 FC 86 23 00 90'),
    0x5C9357: _h('E9 23 D9 34 00 90 90 90 90'),
    0x4346FD: _h('E9 B4 25 4E 00 90 90 90 90'),
    0x6DA4AF: _h('E9 19 C8 23 00 90 90'),
    0x6E0B5D: _h('E9 C4 61 23 00'),
    0x6D7762: _h('E9 D6 F5 23 00 90'),
    0x6D770F: _h('E9 64 F6 23 00 90'),
    0x6DE704: _h('E9 C2 86 23 00'),
    0x6D87CA: _h('56 E9 6D E6 23 00 5E'),
    0x916A00: _h('51 31 C9 8A 0D 7C 3C DC 00 8A 89 30 02 DC 00 6B C9'
                 ' 38 66 8B 84 81 38 CE 99 00 59 C3 31 C0 A0 7C 3C DC'
                 ' 00 8A 80 30 02 DC 00 69 C0 84 00 00 00 8A 80 9A FD'
                 ' DB 00 25 FF 00 00 00 48 C3 6A 1E E8 A6 82 CB FF 83'
                 ' C4 04 8B 4D FC 89 4D F8 E9 B3 49 CB FF 83 3D 70 3C'
                 ' DC 00 1E 74 0E 0F 1F 40 00 8B 45 D4 31 C9 E9 5A A0'
                 ' DC FF 6A 00 83 FA 06 0F 8F A1 00 00 00 52 8B CA D1'
                 ' E9 01 D1 31 C0 B0 01 D3 E0 8A 0D 7C 3C DC 00 8A 89'
                 ' 30 02 DC 00 69 C9 84 00 00 00 66 23 81 AE FD DB 00'
                 ' 85 C0 5A 74 76 0F 1F 40 00 52 8B C2 D1 E8 E8 54 FF'
                 ' FF FF 69 C8 FF 00 00 00 E8 65 FF FF FF E8 44 FF FF'
                 ' FF 31 D2 8A 15 7C 3C DC 00 6B D2 34 8A 92 C0 8D 9A'
                 ' 00 66 81 E2 FF 00 F7 EA 39 C8 58 7C 0B 0F 1F 40 00'
                 ' 6A 07 EB 05 0F 1F 00 6A 00 6A 08 8B D0 D1 EA 01 D0'
                 ' 50 A0 7C 3C DC 00 8A 80 30 02 DC 00 50 E8 84 1B CB'
                 ' FF 83 C4 08 50 6A 03 E8 31 2B B0 FF 83 C4 0C 50 E9'
                 ' 0B A0 DC FF 83 C4 04 E9 2B A0 DC FF 89 4D F0 83 3D'
                 ' 70 3C DC 00 1E 74 0C 0F 1F 40 00 8B 55 F0 E9 30 39'
                 ' DC FF 83 F9 06 0F 8F 7D 00 00 00 52 51 50 8B D1 D1'
                 ' E9 01 D1 31 C0 B0 01 D3 E0 8A 0D 7C 3C DC 00 8A 89'
                 ' 30 02 DC 00 69 C9 84 00 00 00 66 23 81 AE FD DB 00'
                 ' 85 C0 58 59 5A 74 4E 0F 1F 40 00 52 51 D1 E9 8B C1'
                 ' E8 85 FE FF FF 69 C8 FF 00 00 00 E8 96 FE FF FF E8'
                 ' 75 FE FF FF 31 D2 8A 15 7C 3C DC 00 6B D2 34 8A 92'
                 ' C0 8D 9A 00 66 81 E2 FF 00 F7 EA 39 C8 7C 13 0F 1F'
                 ' 40 00 6A 01 E8 58 EC E2 FF 5A 59 5A E9 E2 38 DC FF'
                 ' 59 5A E9 19 39 DC FF 83 F8 01 0F 84 80 29 DC FF 0F'
                 ' B6 0D 7C 3C DC 00 31 D2 8A 91 30 02 DC 00 80 FA 02'
                 ' 74 55 0F 1F 40 00 80 FA 06 74 4C 0F 1F 40 00 80 FA'
                 ' 07 74 43 0F 1F 40 00 51 50 31 C0 B0 00 E8 01 FE FF'
                 ' FF 69 C8 FF 00 00 00 E8 12 FE FF FF E8 F1 FD FF FF'
                 ' 8B D0 0F B6 05 7C 3C DC 00 6B C0 34 0F B6 80 C0 8D'
                 ' 9A 00 F7 EA 39 C8 58 59 7C 09 0F 1F 40 00 E9 17 29'
                 ' DC FF B0 FF 69 C9 40 04 00 00 8A 91 B3 A4 DB 00 80'
                 ' FA FF 0F 85 00 29 DC FF C7 05 70 3C DC 00 14 00 00'
                 ' 00 B0 06 E9 EF 28 DC FF 80 F9 1E 0F 85 98 79 DC FF'
                 ' A0 7C 3C DC 00 6B C0 34 8B 80 C0 8D 9A 00 3C FF 0F'
                 ' 85 82 79 DC FF E9 E1 78 DC FF 8B 4D FC 8A 89 30 02'
                 ' DC 00 80 F9 02 74 1B 0F 1F 40 00 80 F9 06 74 12 0F'
                 ' 1F 40 00 80 F9 07 74 09 0F 1F 40 00 E9 B8 26 CB FF'
                 ' 66 C7 80 C0 8D 9A 00 00 00 E9 AA 26 CB FF 8B 55 08'
                 ' 6B D2 34 8B 92 C0 8D 9A 00 88 91 B3 A4 DB 00 E9 39'
                 ' DA B1 FF 66 89 0D 84 3C DC 00 31 C0 A0 70 3C DC 00'
                 ' 3C 1E 0F 85 D3 37 DC FF 8B C2 D1 E8 01 D0 50 A0 7C'
                 ' 3C DC 00 8A 80 30 02 DC 00 50 E8 89 19 CB FF 83 C4'
                 ' 08 8B 15 7C 3C DC 00 69 D2 40 04 00 00 89 82 44 A5'
                 ' DB 00 50 6B C0 1C 8A 88 E0 F6 91 00 66 89 0D 84 3C'
                 ' DC 00 E9 90 37 DC FF 83 3D 70 3C DC 00 1E 0F 84 2F'
                 ' 9E DC FF E8 2D 8F DC FF E9 25 9E DC FF 83 3D 70 3C'
                 ' DC 00 1E 74 0F 0F 1F 40 00 8A 14 C1 52 6A 02 E9 13'
                 ' 0A DC FF 8B C8 D1 E9 01 C1 51 A0 7C 3C DC 00 8A 80'
                 ' 30 02 DC 00 50 E8 17 19 CB FF 83 C4 08 50 6A 03 E9'
                 ' F0 09 DC FF 83 3D 70 3C DC 00 1E 74 0F 0F 1F 40 00'
                 ' 81 F9 FF 00 00 00 E9 85 09 DC FF 83 FA 06 0F 8F DC'
                 ' 09 DC FF 8B CA D1 E9 01 D1 31 C0 B0 01 D3 E0 8A 0D'
                 ' 7C 3C DC 00 8A 89 30 02 DC 00 69 C9 84 00 00 00 66'
                 ' 23 81 AE FD DB 00 85 C0 0F 84 AF 09 DC FF E9 4C 09'
                 ' DC FF 8A 4C 10 4C 83 F9 1E 75 63 0F 1F 40 00 A0 7C'
                 ' 3C DC 00 3C 02 74 4A 0F 1F 40 00 3C 06 74 42 0F 1F'
                 ' 40 00 3C 07 74 3A 0F 1F 40 00 31 C0 B0 00 E8 02 FC'
                 ' FF FF 69 C8 FF 00 00 00 E8 13 FC FF FF E8 F2 FB FF'
                 ' FF 8B D0 0F B6 05 7C 3C DC 00 6B C0 34 0F B6 80 C0'
                 ' 8D 9A 00 F7 EA 39 C8 7D 0C 0F 1F 40 00 C7 44 24 04'
                 ' 00 00 00 00 31 C9 B1 14 51 E9 CC 78 DC FF 89 4D F8'
                 ' A0 70 3C DC 00 3C 1E 0F 85 D2 00 00 00 A1 78 3C DC'
                 ' 00 31 D2 66 89 C2 66 D1 EA 52 50 6A 00 6A 01 FF 35'
                 ' 7C 3C DC 00 6A 00 83 C4 10 58 5A 52 50 8B C2 E8 8A'
                 ' FB FF FF 69 C8 FF 00 00 00 E8 9B FB FF FF E8 7A FB'
                 ' FF FF 50 31 D2 8A 15 7C 3C DC 00 6B D2 34 8A 92 C0'
                 ' 8D 9A 00 66 81 E2 FF 00 F7 EA 29 C8 59 F7 F1 0F B6'
                 ' 0D 7C 3C DC 00 51 6B C9 34 88 81 C0 8D 9A 00 59 69'
                 ' C9 40 04 00 00 58 5A 66 01 C2 52 31 C0 A0 7C 3C DC'
                 ' 00 8A 80 30 02 DC 00 50 E8 AF 17 CB FF 83 C4 08 8B'
                 ' 15 7C 3C DC 00 69 D2 40 04 00 00 89 82 44 A5 DB 00'
                 ' 50 6B C0 1C 8D B0 D4 F6 91 00 8B 15 7C 3C DC 00 8B'
                 ' C2 69 C0 40 04 00 00 8D B8 4C A5 DB 00 B9 07 00 00'
                 ' 00 F3 A5 58 C7 05 70 3C DC 00 14 00 00 00 A3 78 3C'
                 ' DC 00 83 7D F8 06 E9 A8 18 DC FF 80 3D 8A 0D CC 00'
                 ' 9D 0F 85 B1 0B B2 FF 8B 4D FC C6 01 14 E9 E1 0A B2'
                 ' FF'),
}


# BUILD 868: Enemy Scaling (EnemyScaling/NoRando, with its 10%..100% file
# layered on: the regions in ff7nx_ps3regions.ENEMY_SCALING). The cave's
# den/num bytes are the option's value, carried to es_den / es_num.
ENEMY_SCALING_CODE = {
    0x5D1237: _h('E9 64 5E 34 00'),
    0x9170A0: _h('E8 5B 0F CB FF 8A 35 36 78 9A 00 90 B9 BC 8E 9A 00 E8'
                 ' CF 00 00 00 52 8A 15 3B 78 9A 00 EB 17 90 90 EB 13 02'
                 ' 10 8A 58 98 02 19 01 DA C1 EA 02 02 11 D1 EA 8A 31 90'
                 ' E8 6B 00 00 00 41 E8 65 00 00 00 83 C1 03 E8 5D 00 00'
                 ' 00 41 41 E8 56 00 00 00 83 C1 7E 8B 01 51 52 85 C0 7C'
                 ' 14 0F B6 CA 53 0F B6 DE F7 E1 F7 F3 F7 E1 F7 F3 F7 E1'
                 ' F7 F3 5B 5A 59 89 01 83 C1 04 E8 4B 00 00 00 83 C1 04'
                 ' E8 43 00 00 00 83 E9 0E E8 3B 00 00 00 83 C1 3A 5A 81'
                 ' F9 2C 90 9A 00 0F 8E 76 FF FF FF 99 E9 FB A0 CB FF E9'
                 ' DE 0D B2 FF 31 C0 8A 01 51 52 8B CA 99 F6 E1 C1 E9 08'
                 ' 66 F7 F1 5A 59 84 E4 74 06 0F 1F 40 00 B0 FF 88 01 C3'
                 ' 0F B7 01 31 DB 51 52 31 C9 88 D1 88 F3 F7 E1 F7 F3 F7'
                 ' E1 F7 F3 F7 E1 F7 F3 5A 59 66 89 01 C3 80 39 FF 74 01'
                 ' C3 66 81 C1 B8 00 58 EB 9C'),
    0x5C99B8: _h('E9 E3 DD 3D 00'),
    0x9A77A0: _h('99 8A 50 0F 8A 41 40 3C 22 75 75 84 D2 74 71 80 39 03'
                 ' 7C 6C 52 8A 35 36 78 9A 00 8A 15 3B 78 9A 00 90 EB 2D'
                 ' 90 6B C0 68 52 8A 90 85 B2 9A 00 31 C0 B8 4D B1 9A 00'
                 ' 53 8A 50 68 02 10 8A 58 98 02 19 01 DA C1 EA 02 31 C0'
                 ' B0 85 02 11 D1 EA 8A 31 5B 90 90 90 90 90 31 C9 88 D1'
                 ' 58 52 F7 E1 F7 E1 59 C1 E9 08 99 F7 F1 99 F7 F1 84 E4'
                 ' 74 06 0F 1F 40 00 B0 FF 84 C0 75 06 0F 1F 40 00 FE C0'
                 ' 88 C2 31 C9 88 D1 E9 94 21 C2 FF'),
}


def _es_byte(blob):
    """den or num of Enemy Scaling: one byte, never 0 (the x86 divides by
    den, and a 0 num would leave every enemy at 0)"""
    return bytes(blob) if len(blob) == 1 and blob[0] else None


_ES_DATA = {0x9A7836: ('es_den', _es_byte), 0x9A783B: ('es_num', _es_byte)}
_ES_SITES = [
    (0x5D1237, 0x82CC28, 0xB9401308, 'x3e_es_stats_0'),      # ldr w8, [x24, #0x10]
    (0x5C99B8, 0x80791C, 0x11003D00, 'x3e_es_level_0'),      # add w0, w8, #0xf
]


# BUILD 868: Solo Challenge (SoloChallenge/<who>Solo; the regions in
# ff7nx_ps3regions.SOLO_*): each file's x86. The forced character, the
# imm32 at 0x916817, is the option's value (sc_char); here it is Cloud's 0
# for Cloud/Sephiroth, Barret's 1 for the six alike, and Cait Sith's 6 /
# Vincent's 7 for theirs (whose files also hard-code it further on).
SOLO_CLOUD_CODE = {
    0x41B5A5: _h('E9 56 B2 4F 00 90'),
    0x42A29F: _h('E8 BC C6 4E 00 90'),
    0x916800: _h('31 C0 83 FA 03 0F 8D A0 4D B0 FF 55 8B EC 83 EC 10 89'
             ' 55 FC C7 45 F4 00 00 00 00 90 90 90 85 D2 74 10 0F 1F'
             ' 40 00 C6 82 30 02 DC 00 FF E9 7D 00 00 00 8A 45 F4 8B'
             ' 55 FC 88 82 30 02 DC 00 C7 45 F8 00 00 00 00 69 C0 84'
             ' 00 00 00 31 D2 8A 90 9A FD DB 00 4A 6B D2 03 89 55 F0'
             ' 8B 55 F0 03 55 F8 52 FF 75 F4 E8 1B 1E CB FF 83 C4 08'
             ' 8B 55 FC 69 D2 40 04 00 00 8B 4D F8 89 84 11 44 A5 DB'
             ' 00 6B C0 1C 8D B0 D4 F6 91 00 8B 55 FC 8B C2 69 C0 40'
             ' 04 00 00 6B C9 1C 8D BC 08 4C A5 DB 00 B9 07 00 00 00'
             ' F3 A5 8B C1 FF 45 F8 83 7D F8 03 7C AB 8B 55 FC 8B 82'
             ' 30 02 DC 00 83 C4 10 8B E5 5D 8B 4D FC E9 E5 4C B0 FF'),
    0x916960: _h('8B 85 3C FF FF FF 8A 0D 47 8B 9A 00 80 C9 80 88 0D 47'
             ' 8B 9A 00 C3'),
}
SOLO_CODE = {
    0x41B5A5: _h('E9 56 B2 4F 00 90'),
    0x42A29F: _h('E8 BC C6 4E 00 90'),
    0x5C7503: _h('B8 58 02 00 00'),
    0x719BAF: _h('90 90 90 90 90'),
    0x722283: _h('E9 3E 46 1F 00'),
    0x916800: _h('31 C0 83 FA 03 0F 8D A0 4D B0 FF 55 8B EC 83 EC 10 89'
             ' 55 FC C7 45 F4 01 00 00 00 90 90 90 85 D2 74 10 0F 1F'
             ' 40 00 C6 82 30 02 DC 00 FF E9 7D 00 00 00 8A 45 F4 8B'
             ' 55 FC 88 82 30 02 DC 00 C7 45 F8 00 00 00 00 69 C0 84'
             ' 00 00 00 31 D2 8A 90 9A FD DB 00 4A 6B D2 03 89 55 F0'
             ' 8B 55 F0 03 55 F8 52 FF 75 F4 E8 1B 1E CB FF 83 C4 08'
             ' 8B 55 FC 69 D2 40 04 00 00 8B 4D F8 89 84 11 44 A5 DB'
             ' 00 6B C0 1C 8D B0 D4 F6 91 00 8B 55 FC 8B C2 69 C0 40'
             ' 04 00 00 6B C9 1C 8D BC 08 4C A5 DB 00 B9 07 00 00 00'
             ' F3 A5 8B C1 FF 45 F8 83 7D F8 03 7C AB 8B 55 FC 8B 82'
             ' 30 02 DC 00 83 C4 10 8B E5 5D 8B 4D FC E9 E5 4C B0 FF'
             ' A1 17 68 91 00 A2 F8 46 DD 00 A1 F8 46 DD 00 8B 0C 85'
             ' 28 99 91 00 69 C9 84 00 00 00 81 C1 8C FD DB 00 51 E8'
             ' 48 0A CB FF 83 C4 04 8B 15 F8 46 DD 00 69 D2 84 00 00'
             ' 00 A1 F8 46 DD 00 69 C0 84 00 00 00 66 8B 8A BA FD DB'
             ' 00 66 89 88 B8 FD DB 00 8B 15 F8 46 DD 00 69 D2 84 00'
             ' 00 00 A1 F8 46 DD 00 69 C0 84 00 00 00 66 8B 8A BE FD'
             ' DB 00 66 89 88 BC FD DB 00 68 D0 00 00 00 E9 43 B9 E0'
             ' FF'),
    0x916960: _h('8B 85 3C FF FF FF 8A 0D 47 8B 9A 00 80 C9 80 88 0D 47'
             ' 8B 9A 00 C3'),
}
SOLO_CAIT_CODE = {
    0x41B5A5: _h('E9 56 B2 4F 00 90'),
    0x42A29F: _h('E8 BC C6 4E 00 90'),
    0x5C7503: _h('B8 58 02 00 00'),
    0x718A81: _h('EB 0A'),
    0x7190CC: _h('EB 12'),
    0x719BAF: _h('90 90 90 90 90'),
    0x722283: _h('E9 3E 46 1F 00'),
    0x916800: _h('31 C0 83 FA 03 0F 8D A0 4D B0 FF 55 8B EC 83 EC 10 89'
             ' 55 FC C7 45 F4 06 00 00 00 90 90 90 85 D2 74 10 0F 1F'
             ' 40 00 C6 82 30 02 DC 00 FF E9 7D 00 00 00 8A 45 F4 8B'
             ' 55 FC 88 82 30 02 DC 00 C7 45 F8 00 00 00 00 69 C0 84'
             ' 00 00 00 31 D2 8A 90 9A FD DB 00 4A 6B D2 03 89 55 F0'
             ' 8B 55 F0 03 55 F8 52 FF 75 F4 E8 1B 1E CB FF 83 C4 08'
             ' 8B 55 FC 69 D2 40 04 00 00 8B 4D F8 89 84 11 44 A5 DB'
             ' 00 6B C0 1C 8D B0 D4 F6 91 00 8B 55 FC 8B C2 69 C0 40'
             ' 04 00 00 6B C9 1C 8D BC 08 4C A5 DB 00 B9 07 00 00 00'
             ' F3 A5 8B C1 FF 45 F8 83 7D F8 03 7C AB 8B 55 FC 8B 82'
             ' 30 02 DC 00 83 C4 10 8B E5 5D 8B 4D FC E9 E5 4C B0 FF'
             ' 31 C0 B0 06 A2 F8 46 DD 00 50 E8 05 22 E0 FF 58 C7 05'
             ' BA 00 DC 00 49 54 48 FF A1 F8 46 DD 00 8B 0C 85 28 99'
             ' 91 00 69 C9 84 00 00 00 81 C1 8C FD DB 00 51 E8 38 0A'
             ' CB FF 83 C4 04 8B 15 F8 46 DD 00 69 D2 84 00 00 00 A1'
             ' F8 46 DD 00 69 C0 84 00 00 00 66 8B 8A BA FD DB 00 66'
             ' 89 88 B8 FD DB 00 8B 15 F8 46 DD 00 69 D2 84 00 00 00'
             ' A1 F8 46 DD 00 69 C0 84 00 00 00 66 8B 8A BE FD DB 00'
             ' 66 89 88 BC FD DB 00 68 D0 00 00 00 E9 33 B9 E0 FF 90'),
    0x916960: _h('8B 85 3C FF FF FF 8A 0D 47 8B 9A 00 80 C9 80 88 0D 47'
             ' 8B 9A 00 C3'),
}
SOLO_VINCENT_CODE = {
    0x41B5A5: _h('E9 56 B2 4F 00 90'),
    0x42A29F: _h('E8 BC C6 4E 00 90'),
    0x5C7503: _h('B8 58 02 00 00'),
    0x718A94: _h('EB 0A'),
    0x719BAF: _h('90 90 90 90 90'),
    0x722283: _h('E9 3E 46 1F 00'),
    0x916800: _h('31 C0 83 FA 03 0F 8D A0 4D B0 FF 55 8B EC 83 EC 10 89'
             ' 55 FC C7 45 F4 07 00 00 00 90 90 90 85 D2 74 10 0F 1F'
             ' 40 00 C6 82 30 02 DC 00 FF E9 7D 00 00 00 8A 45 F4 8B'
             ' 55 FC 88 82 30 02 DC 00 C7 45 F8 00 00 00 00 69 C0 84'
             ' 00 00 00 31 D2 8A 90 9A FD DB 00 4A 6B D2 03 89 55 F0'
             ' 8B 55 F0 03 55 F8 52 FF 75 F4 E8 1B 1E CB FF 83 C4 08'
             ' 8B 55 FC 69 D2 40 04 00 00 8B 4D F8 89 84 11 44 A5 DB'
             ' 00 6B C0 1C 8D B0 D4 F6 91 00 8B 55 FC 8B C2 69 C0 40'
             ' 04 00 00 6B C9 1C 8D BC 08 4C A5 DB 00 B9 07 00 00 00'
             ' F3 A5 8B C1 FF 45 F8 83 7D F8 03 7C AB 8B 55 FC 8B 82'
             ' 30 02 DC 00 83 C4 10 8B E5 5D 8B 4D FC E9 E5 4C B0 FF'
             ' 31 C0 B0 07 A2 F8 46 DD 00 50 E8 05 22 E0 FF 58 A1 F8'
             ' 46 DD 00 8B 0C 85 28 99 91 00 69 C9 84 00 00 00 81 C1'
             ' 8C FD DB 00 51 E8 42 0A CB FF 83 C4 04 8B 15 F8 46 DD'
             ' 00 69 D2 84 00 00 00 A1 F8 46 DD 00 69 C0 84 00 00 00'
             ' 66 8B 8A BA FD DB 00 66 89 88 B8 FD DB 00 8B 15 F8 46'
             ' DD 00 69 D2 84 00 00 00 A1 F8 46 DD 00 69 C0 84 00 00'
             ' 00 66 8B 8A BE FD DB 00 66 89 88 BC FD DB 00 68 D0 00'
             ' 00 00 E9 3D B9 E0 FF 90'),
    0x916960: _h('8B 85 3C FF FF FF 8A 0D 47 8B 9A 00 80 C9 80 88 0D 47'
             ' 8B 9A 00 C3'),
}
ITEM_RANDO_CODE = {
    0x722288: _h('E9 DE 53 28 00'),
    0x61E66C: _h('E9 84 8D 38 00'),
    0x63172A: _h('E9 49 5D 37 00 90 90'),
    0x61EE0A: _h('E8 DE 88 38 00'),
    0x9A73F5: _h('66 81 3D D0 15 CC 00 45 01 74 03 80 F9 55 0F 84 68 72'
             ' C7 FF 31 C0 66 A1 0C 0E DC 00 50 66 40 66 A3 0C 0E DC'
             ' 00 E8 E2 C5 DA FF 58 E8 02 00 00 00 EB 2F 90 90 90 90'
             ' 51 50 E8 19 CD 00 00 31 D2 B9 27 01 00 00 F7 F1 66 83'
             ' FA 55 7C 01 42 66 83 FA 69 7C 03 83 C2 17 66 81 FA FF'
             ' 00 7C 01 42 58 59 C3 8A 0D 25 0E DC 00 FE C1 FE C1 D0'
             ' E9 88 0D 25 0E DC 00 66 81 CA 00 02 52 E9 FE 71 C7 FF'
             ' 90 90 90 90 90 50 89 14 85 78 F5 CB 00 53 8A 0D 25 0E'
             ' DC 00 80 F9 02 75 0D 31 C0 90 90 90 90 90 EB 77 0F 1F'
             ' 40 00 31 DB 31 C0 31 C9 8A 04 11 3A 83 F8 6F 91 00 75'
             ' 17 0F 1F 40 00 43 80 BB F8 6F 91 00 FF 74 24 0F 1F 40'
             ' 00 EB 0D 0F 1F 00 31 DB 3C FF 74 07 0F 1F 40 00 41 EB'
             ' D1 C6 05 25 0E DC 00 00 5B 58 E9 55 A2 C8 FF E8 47 00'
             ' 00 00 85 C0 74 F0 83 F8 02 74 22 0F 1F 40 00 83 F8 03'
             ' 74 27 0F 1F 40 00 E8 9F 00 00 00 31 C9 5B 5A 89 04 95'
             ' 78 F5 CB 00 E9 25 A2 C8 FF E8 EA 00 00 00 90 90 90 90'
             ' 90 90 90 EB E2 E8 DC 00 00 00 C6 05 25 0E DC 00 03 EB'
             ' D4 8A 0D 25 0E DC 00 80 F9 01 74 41 0F 1F 40 00 80 F9'
             ' 02 74 3C 0F 1F 40 00 31 C0 A0 64 09 CC 00 66 8B 04 45'
             ' F8 0C CC 00 03 05 E8 F5 CB 00 31 C9 80 3C 08 58 74 25'
             ' 0F 1F 40 00 80 3C 08 5B 74 16 0F 1F 40 00 41 80 F9 10'
             ' 7C E6 31 C0 C3 31 C0 48 C3 31 C0 40 40 C3 31 C0 40 EB'
             ' F8 80 7C 08 02 55 74 0F 0F 1F 40 00 C6 05 25 0E DC 00'
             ' FF 31 C0 40 C3 31 C0 C3 31 C9 3C 01 74 05 0F 1F 40 00'
             ' 41 31 C0 66 A1 0C 0E DC 00 29 C8 50 E8 4B C4 DA FF 58'
             ' E8 8F CB 00 00 90 90 90 90 31 D2 B9 27 01 00 00 F7 F1'
             ' 66 83 FA 55 7C 05 0F 1F 40 00 42 66 83 FA 69 7C 07 0F'
             ' 1F 40 00 83 C2 17 66 81 FA FF 00 7C 05 0F 1F 40 00 42'
             ' 6A 08 52 6A 04 E8 45 20 A7 FF 83 C4 0C C3 50 31 C0 99'
             ' 80 FA 60 7D 0B E8 55 00 00 00 74 01 40 42 EB F0 59 50'
             ' 66 A1 0A 0E DC 00 85 C9 75 01 48 90 90 90 90 90 50 E8'
             ' DA C3 DA FF 58 E8 1E CB 00 00 31 D2 59 F7 F1 8B C2 40'
             ' 99 E8 23 00 00 00 74 05 0F 1F 40 00 48 85 C0 74 07 0F'
             ' 1F 40 00 42 EB E9 8B C2 C6 05 25 0E DC 00 00 50 E8 AA'
             ' F4 D1 FF 59 C3 50 52 E8 A1 F4 D1 FF 5A 80 38 FF 58 C3'
             ' E8 50 73 E0 FF 50 E8 CC 15 C2 FF 58 31 C9 51 E8 73 15'
             ' C2 FF 59 66 89 04 4D 08 0E DC 00 41 80 F9 03 7C EB B8'
             ' E0 0D DC 00 E8 72 E1 D9 FF E9 F0 AB D7 FF 90 90 31 C0'
             ' 66 A1 0A 0E DC 00 90 90 90 90 90 66 FF 05 0A 0E DC 00'
             ' 50 E8 48 C3 DA FF 58 31 C0 99 80 FA 60 7D 0B E8 97 FF'
             ' FF FF 74 01 40 42 EB F0 50 99 E8 77 CA 00 00 59 F7 F1'
             ' 8B C2 40 99 E8 7E FF FF FF 74 01 48 85 C0 74 03 42 EB'
             ' F1 8B C2 C3 E8 AD FF FF FF 80 3D 25 0E DC 00 03 75 0F'
             ' C6 05 25 0E DC 00 00 66 FF 05 0A 0E DC 00 C3 C6 05 25'
             ' 0E DC 00 02 C3'),
    0x923420: _h('01'),
    0x92442C: _h('02'),
    0x61F083: _h('E9 98 86 38 00 90 90'),
    0x9A7720: _h('66 8B 44 10 02 88 41 01 3C 08 0F 85 5A 79 C7 FF 50 31'
             ' C0 66 A1 08 0E DC 00 90 90 90 90 90 50 E8 BD C2 DA FF'
             ' 58 58 C1 E8 08 50 6B C0 0A 83 F8 00 74 0E 0F 1F 40 00'
             ' 50 E8 35 CA 00 00 58 48 EB ED 58 6B C0 54 05 20 34 92'
             ' 00 31 C9 80 7C C8 FC 00 75 0E 0F 1F 40 00 E8 AC FC FF'
             ' FF EB 0E 0F 1F 00 51 50 E8 33 FF FF FF 8B D0 58 59 89'
             ' 14 C8 41 83 F9 0A 7C D7 E9 F1 78 C7 FF'),
    0x71B90D: _h('68 CD CC CC 3D 90 90 90 90 6A 07 6A 08 FF 75 C0'),
    0x5CA961: _h('E9 9A 96 3E 00 90'),
    0x5CACD1: _h('E9 43 93 3E 00 90'),
    0x5C7D46: _h('E9 E4 C2 3E 00 90'),
    0x9B4000: _h('81 F9 FF FF 00 00 0F 84 5B 69 C1 FF E8 36 00 00 00 89'
             ' 4D F8 E9 59 69 C1 FF 0F 84 4C 6D C1 FF 8B 4D FC E8 20'
             ' 00 00 00 89 4D FC E9 A8 6C C1 FF 31 C9 66 89 D1 E8 0E'
             ' 00 00 00 31 D2 66 89 CA 66 89 55 F8 E9 05 3D C1 FF 55'
             ' 8B EC 81 EC 40 03 00 00 E8 57 00 00 00 8B D0 53 31 DB'
             ' 31 C9 E8 82 00 00 00 F7 DB 66 89 44 5D FE F7 DB 01 C1'
             ' 43 81 FB A0 01 00 00 7C E7 E8 A0 00 00 00 90 90 90 90'
             ' 90 90 90 90 90 90 90 90 90 90 90 90 90 90 90 90 90 31'
             ' D2 90 F7 F1 8B C2 31 C9 41 49 31 D2 66 8B 54 4D FE 29'
             ' D0 7D F4 F7 D9 5B 8B E5 5D C3 81 F9 3F 01 00 00 0F 8F'
             ' 08 00 00 00 8B 04 8D 58 4E 92 00 C3 52 8B D1 66 81 EA'
             ' 40 01 53 52 E8 8F 35 FF FF 59 5B 5A 0F 85 03 00 00 00'
             ' 31 C0 C3 8B 04 8D 58 54 92 00 C3 51 8B CB E8 C1 FF FF'
             ' FF 83 F8 00 7E 26 52 8B CA 39 C1 0F 8D 06 00 00 00 8B'
             ' D1 8B C8 8B C2 C1 E0 0D 31 D2 F7 F1 F7 E0 C1 E8 0D F7'
             ' E0 C1 E8 0D 5A 40 59 C3 31 C0 59 C3 53 51 52 E8 7F 4A'
             ' C1 FF 03 05 B8 08 DC 00 50 E8 D3 F8 D9 FF 58 31 C9 51'
             ' 52 E8 B0 FA D9 FF 5A 59 C1 E2 08 88 C2 41 83 F9 04 7C'
             ' EC 8B C2 5A 59 5B C3 53 51 52 EB DF 00 00 00 00 00 00'
             ' 00 00 00 00 00 00 00 00 00 00 53 51 52 E8 39 4A C1 FF'
             ' 03 05 B8 08 DC 00 50 E8 8D F8 D9 FF 58 31 C9 51 52 E8'
             ' 6A FA D9 FF 5A 59 C1 E2 08 88 C2 41 83 F9 04 7C EC 8B'
             ' C2 5A 59 5B C3 53 51 52 EB DF'),
}
_IR_TEXT = bytes.fromhex('45 43 45 49 56 45 44 FF')     # "eceived", end


def _ir_text(b):
    """the message text the item rando looks for: the mod's 8 bytes, or a
    later file's (SACRandoCompat's 7, "btained") over them"""
    b = bytes(b)
    if not 1 <= len(b) <= 8 or 0xFF in b[:-1]:
        return None
    return b + _IR_TEXT[len(b):]


_IR_SITES = [(0x722288, 0xE47134, 0xB9401328, 'x3e_ir_init_0'),
             (0x61E66C, 0x98A3FC, 0xB9401688, 'x3e_ir_item_0'),
             (0x61EE0A, 0x98C06C, 0x29422688, 'x3e_ir_materia_0'),
             (0x63172A, 0x9CECBC, 0xB9400A97, 'x3e_ir_message_0'),
             (0x61F083, 0x98CD08, 0x941DBDA6, 'x3e_ir_shop_0'),
             (0x5CA961, 0x80FB88, 0xB9401688, 'x3e_ir_steal_0'),
             (0x5CACD1, 0x810880, 0x39008E89, 'x3e_ir_morph_0'),
             (0x5C7D46, 0x7FA440, 0xB9401668, 'x3e_ir_drop_0'),
             (0x71B90D, 0xE3143C, 0xB900001F, 'x3w_ir_depth')]
# the shop's price shown whole: [ebp-0x40] pushed as a dword (no `and ecx,
# 0xFF`, ecx left as it was)
_IR_WORDS = [
    (0x71B913, 0xE3147C, 0x39400014, 0xB9400014),  # ldrb w20 -> ldr w20
    (0x71B913, 0xE31484, 0xB9000734, NOP),         # str w20, [x25, #4]
]


BATTLE_RANDO_CODE = {
    0x722288: _h('E9 DE 53 28 00'),
    0x9A766B: _h('E8 50 73 E0 FF 50 E8 CC 15 C2 FF 58 31 C9 51 E8 73 15'
             ' C2 FF 59 66 89 04 4D 08 0E DC 00 41 80 F9 03 7C EB B8'
             ' E0 0D DC 00 E8 72 E1 D9 FF E9 F0 AB D7 FF'),
    0x767ACD: _h('E8 08 00 00 00 89 55 D8 E9 31 01 00 00 53 E8 80 C6 24'
             ' 00 90 90 90 90 90 90 31 DB 90 90 31 D2 B9 95 01 00 00'
             ' F7 F1 8B C2 40 31 D2 90 52 5A 42 8B 0C 95 34 7B 76 00'
             ' 52 99 42 8B DA 21 CA 85 D2 74 05 0F 1F 40 00 48 85 C0'
             ' 74 0E 0F 1F 40 00 D1 E3 85 DB 74 D9 8B D3 EB E3 31 D2'
             ' 4A 42 D1 EB 85 DB 75 F9 58 C1 E0 05 01 C2 5B C3 90 D7'
             ' 73 13 CC CF 2D 7C 33 53 7C 0E F7 C3 71 77 4C 33 0F 7B'
             ' 7F E7 03 37 73 FF 1F 00 00 01 00 00 00 00 E0 FE 73 07'
             ' 70 77 1F 7F 00 00 37 F0 F0 73 00 3B 1F 73 77 36 00 00'
             ' F7 70 70 FF 72 74 1E 17 F1 1B 00 00 00 F0 07 F0 00 00'
             ' 00 00 1F 07 00 7F 37 3F F7 F1 F3 9E 07 F7 3C 01 77 36'
             ' 70 70 03 F0 70 17 00 F0 C7 F3 00 37 00 70 F7 77 7E 01'
             ' 00 00 00 00 00 30 00 00 00 07 00'),
    0x9B4160: _h('53 51 52 E8 39 4A C1 FF 03 05 B8 08 DC 00 50 E8 8D F8'
             ' D9 FF 58 31 C9 51 52 E8 6A FA D9 FF 5A 59 C1 E2 08 88'
             ' C2 41 83 F9 04 7C EC 8B C2 5A 59 5B C3 53 51 52 EB DF'),
    0x60B625: _h('E8 B0 C4 15 00 90'),
    0x408268: _h('E9 93 ED 50 00 90'),
    0x5D1237: _h('E9 64 5E 34 00'),
    0x767BB0: _h('44 11 6C 19 82 19 9C 19 C8 1D CC 21 D0 15 D4 21 E0 25'
             ' E8 29 0F 1E D5 29 1B 26 2B 2E 40 32 53 32 58 32 6B 32'
             ' 6F 2E 70 2E 71 2E 72 2E 74 3A 8C 3A 84 3E 9F 42 A7 32'
             ' E4 46 E7 4A 22 39 2A 3F 2B 3F 0C 4F 32 43 27 4B D4 53'
             ' 48 53 4C 53 58 5B 1F 6D 8C 5F D6 FF D8 C3'),
    0x917000: _h('66 A1 8A 0D CC 00 31 C9 99 66 8B 14 4D B0 7B 76 00 52'
             ' 66 81 E2 FF 03 66 39 C2 5A 74 0F 0F 1F 40 00 41 83 F9'
             ' 2B 7C E2 EB 60 0F 1F 00 C1 EA 0A 88 15 0E 0E DC 00 51'
             ' 31 C0 66 A1 08 0E DC 00 E8 39 00 00 00 58 31 C9 B1 2B'
             ' 40 48 50 51 E8 A1 1B CB FF 59 99 F7 F1 49 58 39 C2 7F'
             ' EF 7C EC F7 D9 66 8B 04 4D 04 7C 76 00 F7 D9 50 C1 E8'
             ' 0A A2 0F 0E DC 00 58 66 25 FF 03 E9 F2 11 AF FF A3 48'
             ' 67 C0 00 50 E8 BB 1B CB FF 58 C3 99 66 89 15 0E 0E DC'
             ' 00 C6 05 37 09 DC 00 02 EB DD'),
    0x9170A0: _h('E8 5B 0F CB FF B9 BC 8E 9A 00 E8 D6 00 00 00 66 8B 15'
             ' 0E 0E DC 00 85 D2 75 1C B8 4D B1 9A 00 8A 50 68 02 10'
             ' 0F B6 58 98 01 DA 6B D2 2B C1 EA 07 02 11 D1 EA 8A 31'
             ' E8 6B 00 00 00 41 E8 65 00 00 00 83 C1 03 E8 5D 00 00'
             ' 00 41 41 E8 56 00 00 00 83 C1 7E 8B 01 51 52 85 C0 7C'
             ' 14 0F B6 CA 53 0F B6 DE F7 E1 F7 F3 F7 E1 F7 F3 F7 E1'
             ' F7 F3 5B 5A 59 89 01 83 C1 04 E8 4B 00 00 00 83 C1 04'
             ' E8 43 00 00 00 83 E9 0E E8 3B 00 00 00 83 C1 3A 90 81'
             ' F9 2C 90 9A 00 0F 8E 6F FF FF FF 99 E9 FB A0 CB FF E9'
             ' DE 0D B2 FF 31 C0 8A 01 51 52 8B CA 99 F6 E1 C1 E9 08'
             ' 66 F7 F1 5A 59 84 E4 74 06 0F 1F 40 00 B0 FF 88 01 C3'
             ' 0F B7 01 31 DB 51 52 31 C9 88 D1 88 F3 F7 E1 F7 F3 F7'
             ' E1 F7 F3 F7 E1 F7 F3 5A 59 66 89 01 C3 80 39 FF 74 01'
             ' C3 66 81 C1 B8 00 58 EB 9C'),
    0x5C99B8: _h('E9 E3 DD 3D 00'),
    0x9A77A0: _h('99 8A 50 0F 8A 41 40 3C 22 75 75 84 D2 74 71 80 39 03'
             ' 7C 6C 52 66 8B 15 0E 0E DC 00 85 D2 75 31 B8 4D B1 9A'
             ' 00 8A 50 68 02 10 0F B6 58 98 01 DA 6B D2 2B C1 EA 07'
             ' 8B 01 6B C0 68 8A B0 E5 B0 9A 00 90 90 90 90 90 88 F1'
             ' 28 D1 00 CE 84 F6 00 CA 90 90 90 90 90 90 31 C9 88 D1'
             ' 58 52 F7 E1 F7 E1 59 C1 E9 08 99 F7 F1 99 F7 F1 84 E4'
             ' 74 06 0F 1F 40 00 B0 FF 84 C0 75 06 0F 1F 40 00 FE C0'
             ' 88 C2 31 C9 88 D1 E9 94 21 C2 FF'),
}
RANDO_SCALER_CODE = {
    0x9170D6: _h('E8 66 00 00 00'),
    0x917141: _h('E8 EA 06 09 00'),
    0x9A7830: _h('50 0F B6 C6 6B C0 02 30 F6 6B D2 03 EB 04 D1 E8 D1 EA'
             ' 66 3D 80 00 7D F6 66 81 FA 80 00 7D EF 88 C6 58 C3'),
    0x9A77F1: _h('E8 3A 00 00 00'),
    0x9A77E1: _h('E8 4A 00 00 00'),
}
_BR_SITES = [(0x722288, 0xE47134, 0xB9401328, 'x3e_ir_init_0'),
             (0x408268, 0x35EB0, 0x51230A60, 'x3e_br_boss_0'),
             (0x60B625, 0x91F9F4, 0x11000900, 'x3e_br_field_0'),
             (0x767ACD, 0xFA0AB0, 0xB9401688, 'x3e_br_world_0')]


DOUBLE_BOSSES_CODE = {
    0x429E8A: _h('C6 05 EC B2 BF 00 00'),
    0x429E9F: _h('EB 3F'),
    0x917000: _h('66 A1 8A 0D CC 00 31 C9 99 66 8B 14 4D B0 7B 76 00 52'
             ' 66 81 E2 FF 03 66 39 C2 5A 74 08 41 83 F9 2B 7C E6 EB'
             ' 44 C1 EA 0A 88 15 0E 0E DC 00 51 31 C0 66 A1 08 0E DC'
             ' 00 A3 48 67 C0 00 50 E8 00 1C CB FF 58 58 50 E8 B6 D1'
             ' 09 00 66 A3 EE 41 9B 00 58 E8 AA D1 09 00 50 C1 E8 0A'
             ' A2 0F 0E DC 00 58 66 25 FF 03 EB 1A 90 90 90 99 66 89'
             ' 15 0E 0E DC 00 4A 66 89 15 EE 41 9B 00 C6 05 37 09 DC'
             ' 00 02 50 E8 16 FF CA FF 58 E9 E2 11 AF FF 83 7D C4 05'
             ' 0F 87 D6 A7 B1 FF E8 15 9C B1 FF E9 99 A7 B1 FF'),
    0x9B4200: _h('31 C9 B1 2B 40 48 50 51 E8 E5 49 C1 FF 59 99 F7 F1 49'
             ' 58 39 C2 7F EF 7C EC F7 D9 66 8B 04 4D 04 7C 76 00 F7'
             ' D9 50 85 C9 0F 84 0A 00 00 00 51 E8 BE 49 C1 FF 59 49'
             ' EB EE 58 C3'),
    0x41C950: _h('E8 4B 78 59 00 90'),
    0x9B41A0: _h('66 A1 52 87 9A 00 66 3D FF FF 75 25 0F B7 05 EE 41 9B'
             ' 00 66 3D FF FF 74 34 66 C7 05 EE 41 9B 00 FF FF 8B D0'
             ' C1 EA 0A 88 15 0F 0E DC 00 66 25 FF 03 50 C6 05 3C E1'
             ' 9A 00 06 C6 05 1C E1 9A 00 20 E8 EB CB A7 FF C6 05 3C'
             ' E1 9A 00 00 58 C3'),
    0x431045: _h('90 90 90 90 90'),
    0x430DFC: _h('90 90 90 90 90 90 90 90 90 90'),
    0x408299: _h('99 88 15 4C 16 CC 00 52 52 52 52 52 52 52 52 88 15 2C'
             ' E1 9A 00 90 90'),
    0x430DE4: _h('1C 06'),
    0x431834: _h('E9 53 58 4E 00'),
}
_DB_SITES = [(0x722288, 0xE47134, 0xB9401328, 'x3e_ir_init_0'),
             (0x408268, 0x35EB0, 0x51230A60, 'x3e_db_boss_0'),
             (0x60B625, 0x91F9F4, 0x11000900, 'x3e_br_field_0'),
             (0x767ACD, 0xFA0AB0, 0xB9401688, 'x3e_br_world_0'),
             (0x408299, 0x35F5C, 0x2A1303E0, 'x3e_db_start_0'),
             (0x41C950, 0x93A6C, 0x510512C0, 'x3e_db_second_0'),
             (0x429E8A, 0xD59B0, 0x5121A260, 'x3e_db_check_0'),
             (0x431834, 0x9DE18, 0xB9401348, 'x3e_db_end_0')]
# the battle loader (0x430DD0): its case switch's default goes to 0x431404
# (0x430DE4), whose code reads w28, which the stock sets only past the
# `b.hi` -- so the two words that set it move before the branch; the end
# no longer clears the rewards (0x430DFC) or calls 0x5C6F9C (0x431045:
# the return slot's push and the bl)
_DB_WORDS = [
    (0x430DE2, 0x9BFE8, 0x54011DC8, 0x52960E9C),   # mov w28, #0xb074
    (0x430DE2, 0x9BFEC, 0xB9401748, 0x72A0135C),   # movk w28, #0x9a, lsl 16
    (0x430DE2, 0x9BFF0, 0x5100F100, 0x54000808),   # b.hi +0x9C0F0 (0x431404)
    (0x430DE2, 0x9BFF4, 0x52960E9C, 0xB9401748),   # ldr w8, [x26, #0x14]
    (0x430DE2, 0x9BFF8, 0x72A0135C, 0x5100F100),   # sub w0, w8, #0x3c
    (0x430DFC, 0x9C2DC, 0xB900001F, NOP),          # str wzr (0x9AE12C)
    (0x431045, 0x9D2B4, 0xB9001348, NOP),          # str w8, [x26, #0x10]
    (0x431045, 0x9D2B8, 0x941D62CA, NOP),          # bl x86 0x5C6F9C
]


PARTY_RANDO_CODE = {
    0x41B5A5: _h('E9 56 B2 4F 00 90'),
    0x916800: _h('31 C0 8A 82 30 02 DC 00 83 FA 03 0F 8D 9A 4D B0 FF 3C'
             ' FF 0F 84 92 4D B0 FF 55 8B EC 83 EC 10 89 55 FC E8 39'
             ' D9 09 00 83 E0 0F 89 45 F4 83 F8 08 7F F0 8B 0D DE 0D'
             ' DC 00 83 F8 00 74 09 0F 1F 40 00 48 D1 E9 EB F2 83 E1'
             ' 01 85 C9 74 D5 8B 4D FC 83 F9 00 74 14 0F 1F 40 00 49'
             ' 31 C0 8A 81 30 02 DC 00 3A 45 F4 74 BB EB E7 8B 45 F4'
             ' 8B 55 FC 88 82 30 02 DC 00 C7 45 F8 00 00 00 00 69 C0'
             ' 84 00 00 00 31 D2 8A 90 9A FD DB 00 4A 6B D2 03 89 55'
             ' F0 8B 55 F0 03 55 F8 52 FF 75 F4 E8 E4 1D CB FF 83 C4'
             ' 08 8B 55 FC 69 D2 40 04 00 00 8B 4D F8 89 84 11 44 A5'
             ' DB 00 6B C0 1C 8D B0 D4 F6 91 00 8B 55 FC 8B C2 69 C0'
             ' 40 04 00 00 6B C9 1C 8D BC 08 4C A5 DB 00 B9 07 00 00'
             ' 00 F3 A5 8B C1 FF 45 F8 83 7D F8 03 7C AB 8B 55 FC 8B'
             ' 82 30 02 DC 00 83 C4 10 8B E5 5D 8B 4D FC E9 AE 4C B0'
             ' FF'),
    0x9B4160: _h('53 51 52 E8 39 4A C1 FF 03 05 B8 08 DC 00 50 E8 8D F8'
             ' D9 FF 58 31 C9 51 52 E8 6A FA D9 FF 5A 59 C1 E2 08 88'
             ' C2 41 83 F9 04 7C EC 8B C2 5A 59 5B C3 53 51 52 EB DF'),
}
SOLO_RANDOM_CODE = {
    0x41B5A5: _h('E9 56 B2 4F 00 90'),
    0x42A29F: _h('E8 BC C6 4E 00 90'),
    0x916800: _h('31 C0 8A 82 30 02 DC 00 83 FA 03 0F 8D 9A 4D B0 FF 3C'
             ' FF 90 90 90 90 90 90 55 8B EC 83 EC 10 89 55 FC E8 7A'
             ' 23 CB FF 83 E0 0F 89 45 F4 83 F8 08 7F F0 8B 0D DE 0D'
             ' DC 00 83 F8 00 74 09 0F 1F 40 00 48 D1 E9 EB F2 83 E1'
             ' 01 85 C9 74 D5 8B 4D FC 83 F9 00 74 14 31 C0 48 88 81'
             ' 30 02 DC 00 E9 8C 00 00 00 90 90 74 BB EB E7 8B 45 F4'
             ' 8B 55 FC 88 82 30 02 DC 00 C7 45 F8 00 00 00 00 69 C0'
             ' 84 00 00 00 31 D2 8A 90 9A FD DB 00 4A 6B D2 03 89 55'
             ' F0 8B 55 F0 03 55 F8 52 FF 75 F4 E8 E4 1D CB FF 83 C4'
             ' 08 8B 55 FC 69 D2 40 04 00 00 8B 4D F8 89 84 11 44 A5'
             ' DB 00 6B C0 1C 8D B0 D4 F6 91 00 8B 55 FC 8B C2 69 C0'
             ' 40 04 00 00 6B C9 1C 8D BC 08 4C A5 DB 00 B9 07 00 00'
             ' 00 F3 A5 8B C1 FF 45 F8 83 7D F8 03 7C AB 8B 55 FC 8B'
             ' 82 30 02 DC 00 83 C4 10 8B E5 5D 8B 4D FC E9 AE 4C B0'
             ' FF'),
    0x916960: _h('8B 85 3C FF FF FF 8A 0D 47 8B 9A 00 80 C9 80 88 0D 47'
             ' 8B 9A 00 C3'),
}


def _sc_char(full):
    """parser of the cave line of `full` (a SOLO_*_CODE): the same bytes,
    the forced character (imm32 at +0x17) any of the game's 0..10"""
    cave = full[0x916800]

    def parse(blob):
        if len(blob) != len(cave) or blob[:0x17] != cave[:0x17] or \
                blob[0x1B:] != cave[0x1B:]:
            return None
        if struct.unpack_from('<I', blob, 0x17)[0] > 10:
            return None
        return bytes(blob[0x17:0x1B])
    return parse


def _sc_port(name, full, sites, words=()):
    code = {va: b for va, b in full.items() if va != 0x916800}
    return Port(name, code, {0x916800: ('sc_char', _sc_char(full))}, sites,
                _fixed(words) if words else None)


_SC_PARTY = [(0x41B5A5, 0x8D64C, 0x9441BB55, 'x3e_sc_party_0'),    # bl g2h
             (0x42A29F, 0xD45C0, 0xB9400008, 'x3h_sc_imprison')]  # ldr w8, [x0]
_SC_INIT = (0x722283, 0xE4711C, 0xB9401328)                         # ldr w8, [x25, #0x10]
# the words that need no cave: a start of 600 EXP (x86 0x5C7503 `mov eax,
# 0x258` for the call; its return slot is never pushed), no EXP given on
# joining (0x719BAF: the call and its slot gone), and Cait Sith's /
# Vincent's own joins left out (their `jne`s made `jmp`s)
_SC_WORDS = [
    (0x5C7503, 0x7F878C, 0xB9401288, 0x52804B08),  # ldr w8, [x20, #0x10] -> mov w8, #0x258
    (0x5C7503, 0x7F8790, 0x51001108, NOP),         # sub w8, w8, #4
    (0x5C7503, 0x7F8794, 0xB9001288, 0xB9000288),  # str w8, [x20, #0x10] -> str w8, [x20]
    (0x5C7503, 0x7F8798, 0x9400003E, NOP),         # bl x86 0x5C788A
    (0x719BAF, 0xE21E54, 0x51001108, NOP),         # sub w8, w8, #4
    (0x719BAF, 0xE21E5C, 0x97E7587D, NOP),         # bl x86 0x5C7336
]
# BUILD 868: Randomizer/CharacterOrderRando: the character order file, and
# the starting-level file beside it
CHARACTER_ORDER_CODE = {
    0x722288: _h('E9 DE 53 28 00'),
    0x9A766B: _h('E8 50 73 E0 FF 50 E8 CC 15 C2 FF 58 31 C9 51 E8 73 15'
             ' C2 FF 59 66 89 04 4D 08 0E DC 00 41 80 F9 03 7C EB B8'
             ' E0 0D DC 00 E8 72 E1 D9 FF E9 F0 AB D7 FF'),
    0x61C120: _h('EB 03 FF 45 F8 83 7D F8 03 7D 2F 33 C9 8A 0D 64 09 CC'
             ' 00 33 D2 66 8B 14 4D F8 0C CC 00 A1 E8 F5 CB 00 01 C2'
             ' 8B 4D F8 0F B6 44 11 01 E8 EF 80 39 00 90 90 90'),
    0x61F096: _h('8B 0D D8 F9 CB 00 E8 A2 D7 FF FF A1 D8 F9 CB 00 66 89'
             ' 48 02'),
    0x9B4280: _h('85 C0 75 13 80 79 01 11 74 0D B9 27 09 DC 00 80 39 00'
             ' 75 08 80 09 10 E8 A4 FF FF FF C3 90'),
    0x61BEB2: _h('0F B6 44 11 01 E8 84 83 39 00 88 45 FC C7 45 F4 00 00'
             ' 00 00 EB 03 FF 45 F4'),
    0x61BF24: _h('A3'),
    0x61C066: _h('E8 D5 81 39 00 90 88 45 FC C7 45 F8 00 00 00 00 EB 03'
             ' FF 45 F8'),
    0x61C0DA: _h('9D'),
    0x61C82F: _h('0F B6 44 11 01 66 89 45 F4 8A 44 11 02 E8 FF 79 39 00'
             ' EB 14 80 79 01 06 74 06 80 79 01 11 75 05 E8 2C 7A 39'
             ' 00 8B C8 C3 89 45 FC'),
    0x61C922: _h('0F B6 44 11 01 89 45 FC E8 11 79 39 00 90 8B C8 31 D2'
             ' 42'),
    0x61C98A: _h('0F B6 44 11 01 E8 AC 78 39 00 90 89 45 FC 8B C8 31 D2'
             ' 42'),
    0x61BCF4: _h('0F B6 44 11 01 E8 42 85 39 00 89 45 FC 8B C8 90'),
    0x61C77A: _h('0F B6 44 11 01 E8 BC 7A 39 00 89 45 FC 0F B7 15 DE 0D'
             ' DC 00 90'),
    0x61C6B8: _h('E8 83 7B 39 00 90 88 45 FC C7 45 F8 00 00 00 00 EB 03'
             ' FF 45 F8'),
    0x61C714: _h('B5'),
    0x60F91D: _h('8A 81 DC 08 DC 00 89 0D 2D F9 60 00 EB 06 90 90 90 90'
             ' 90 90'),
    0x61182C: _h('E9 6F 2A 3A 00'),
    0x9B42A0: _h('E8 AB B4 C5 FF B9 2D F9 60 00 66 81 39 09 01 7C 0F 66'
             ' 81 39 0B 01 7F 08 C6 01 00 E8 80 FF FF FF E9 6C D5 C5'
             ' FF'),
    0x631FCA: _h('E8 71 22 38 00 89 45 FC 90 90 8B 4D FC'),
    0x9B412F: _h('31 C9 51 52 E8 B0 FA D9 FF 5A 59 C1 E2 08 88 C2 41 83'
             ' F9 04 7C EC 8B C2 5A 59 5B C3 53 51 52 EB DF'),
    0x9B4240: _h('3C 09 0F 82 01 00 00 00 C3 52 51 50 31 C0 66 A1 08 0E'
             ' DC 00 50 E8 A7 F7 D9 FF 58 58 B9 09 00 00 00 40 48 50'
             ' 51 E8 E1 FE FF FF 59 31 D2 F7 F1 49 58 39 C2 7C ED 7F'
             ' EC 8B C1 59 5A C3'),
    0x7190CC: _h('EB'),
    0x767374: _h('31 C9 8A 0D E5 09 DC 00 31 C0 E8 BD CE 24 00 39 C8 0F'
             ' 84 83 00 00 00 B0 02 E8 AE CE 24 00 39 C8 74 69 B0 08'
             ' E8 A3 CE 24 00 39 C8 74 4F EB 6B'),
}
STARTING_LEVEL_CODE = {
    0x5C7460: _h('EB 2E'),
    0x719B94: _h('90 90'),
    0x5C7508: _h('5A 59 89 45 A4 C7 45 9C 00 00 00 00'),
}
_CO_SITES = [(0x722288, 0xE47134, 0xB9401328, 'x3e_ir_init_0'),
             (0x60F91D, 0x94EDA4, 0x941EB57F, 'x3e_co_bank_0'),
             (0x61182C, 0x955680, 0xB9401288, 'x3e_co_leader_0'),
             (0x61BCF4, 0x980738, 0x0B080128, 'x3e_co_model_0'),
             (0x61BEB2, 0x980CB4, 0x941DEDBB, 'x3e_co_add_0'),
             (0x61C066, 0x981394, 0x941DEC03, 'x3e_co_remove_0'),
             (0x61C14C, 0x98163C, 0x51001100, 'x3e_co_party_0'),
             (0x61C6B8, 0x983004, 0x941DE4E7, 'x3e_co_remove2_0'),
             (0x61C77A, 0x9832E0, 0x0B080128, 'x3e_co_exists_0'),
             (0x61C82F, 0x983534, 0x0B080128, 'x3e_co_avail_0'),
             (0x61C922, 0x9838F8, 0x0B080128, 'x3e_co_lock_0'),
             (0x61C98A, 0x983AA4, 0x0B080128, 'x3e_co_unlock_0'),
             (0x61F096, 0x98CD88, 0x394002C8, 'x3e_co_name_0'),
             (0x631FC5, 0x9D033C, 0x5103A91A, 'x3e_co_text_0'),
             (0x767374, 0xF2FEB8, 0xB9400288, 'x3e_co_world_0')]
# the loops' moved steps (0x61BF24, 0x61C0DA, 0x61C714) need no word: the
# stock step does the same, with ecx (dead there) set as well. Cloud's
# naming leaves Cait Sith's name alone: Solo Challenge (Cait Sith)'s word.
_CO_WORDS = [(0x7190CC, 0xE21AA0, 0x35FFF8A8, 0x17FFFFC5)]
_SL_SITES = [(0x5C7508, 0x7F879C, 0x29422688, 'x3e_sl_fix_0')]
_SL_WORDS = [
    (0x5C7460, 0x7F850C, 0x36000108, 0x14000031),  # tbz -> b +0x7F85D0
    (0x719B94, 0xE21E00, 0x34000928, 0xD503201F),  # cbz w8 -> nop
]
_SC_CAIT_WORDS = [
    (0x7190CC, 0xE21AA0, 0x35FFF8A8, 0x17FFFFC5),  # cbnz w8 -> b +0xE219B4
    (0x718A81, 0xE1D52C, 0x540001C1, 0x1400000E),  # b.ne -> b +0xE1D564
]
_SC_VINCENT_WORDS = [
    (0x718A94, 0xE1D57C, 0x540001C1, 0x1400000E),  # b.ne -> b +0xE1D5B4
]


# BUILD 868: Battle PHS (BattlePHS/hext/BattlePHS.txt; the regions in
# ff7nx_ps3regions.BATTLE_PHS)
BATTLE_PHS_CODE = {
    0x41960C: _h('E9 02 E9 58 00 90'),
    0x433B69: _h('E9 6E 40 57 00 90 90'),
    0x4367DB: _h('E9 33 14 57 00'),
    0x4376A7: _h('90 90 90 90 90 90'),
    0x5CB3D1: _h('E9 A8 C7 3D 00'),
    0x5CF873: _h('09 10'),
    0x5CFA17: _h('90 90 90 90 90 90'),
    0x5D02DB: _h('90 90'),
    0x5D03EA: _h('90 90 90 90'),
    0x6D9528: _h('8A 0C 10 88 0D 70 3C DC 00 8A 44 10 01 80 F9 1D 7C 10'
                 ' 31 C0 88 C8 83 E8 1D 6B C0 03 40 90 90 90 90 90 A2 74'
                 ' 3C DC 00'),
    0x6D9C64: _h('E9 A8 DE 2C 00'),
    0x6D9C77: _h('E9 53 DE 2C 00 90'),
    0x6D9C93: _h('E9 5A DE 2C 00'),
    0x6D9CAB: _h('E9 82 E2 2C 00 90 90'),
    0x6D9D4D: _h('E9 F7 E1 2C 00 90'),
    0x6DE70B: _h('E9 93 94 2C 00'),
    0x6DFA02: _h('E9 F9 7F 2C 00'),
    0x6DFAE5: _h('E9 37 7F 2C 00 90'),
    0x6DFBF1: _h('E9 CA 7F 2C 00'),
    0x6DFC4F: _h('E9 AC 82 2C 00'),
    0x7007DE: _h('EB'),
    0x70084E: _h('EB'),
    0x700906: _h('EB'),
    0x700969: _h('EB'),
    0x9A7A00: _h('83 3D 70 3C DC 00 1D 75 0B 0F 1F 40 00 C7 42 14 00 00'
                 ' 00 00 66 8B 42 14 66 89 45 DC E9 E9 7F D3 FF 83 3D 70'
                 ' 3C DC 00 1D 74 0F 0F 1F 40 00 81 F9 FF 00 00 00 E9 B2'
                 ' 80 D3 FF 68 DF 4F CD 3E 0F BF 55 C8 8B 45 E8 01 D0 0F'
                 ' BF 4D EC 6B C9 03 01 C8 83 F8 08 7F 72 0F 1F 40 00 8B'
                 ' C8 A1 DE 0D DC 00 D3 E8 83 E0 01 85 C0 74 5E 0F 1F 40'
                 ' 00 69 D1 84 00 00 00 66 8B 82 B8 FD DB 00 BA 07 00 00'
                 ' 00 85 C0 75 06 0F 1F 40 00 31 D2 3A 0D 30 02 DC 00 74'
                 ' 21 0F 1F 40 00 3A 0D 31 02 DC 00 74 15 0F 1F 40 00 3A'
                 ' 0D 32 02 DC 00 74 09 0F 1F 40 00 EB 05 0F 1F 00 31 D2'
                 ' 52 69 C9 84 00 00 00 81 C1 9C FD DB 00 51 E9 7C 80 D3'
                 ' FF 83 C4 04 E9 9C 80 D3 FF 83 3D 70 3C DC 00 1D 74 0F'
                 ' 0F 1F 40 00 81 FA FF 00 00 00 E9 96 21 D3 FF 81 FA FE'
                 ' 00 00 00 E9 8B 21 D3 FF 83 3D 70 3C DC 00 1D 74 0E 0F'
                 ' 1F 40 00 66 0F B6 14 C1 E9 8F 21 D3 FF 66 89 C2 E9 87'
                 ' 21 D3 FF 83 3D 70 3C DC 00 1D 74 11 0F 1F 40 00 85 D2'
                 ' 0F 85 49 22 D3 FF E9 41 21 D3 FF 69 D0 84 00 00 00 66'
                 ' 8B 8A B8 FD DB 00 66 85 C9 0F 84 2E 22 D3 FF 3A 05 30'
                 ' 02 DC 00 0F 84 22 22 D3 FF 3A 05 31 02 DC 00 0F 84 16'
                 ' 22 D3 FF 3A 05 32 02 DC 00 0F 84 0A 22 D3 FF 8B C8 A1'
                 ' DE 0D DC 00 D3 E8 83 E0 01 85 C0 0F 84 F6 21 D3 FF E9'
                 ' EE 20 D3 FF 66 81 3D DC 08 DC 00 81 01 7C 0E 0F 1F 40'
                 ' 00 6A 1D E8 55 71 C2 FF 83 C4 04 C7 45 E8 00 00 00 00'
                 ' E9 35 38 C2 FF 83 F9 1D 74 0E 0F 1F 40 00 E8 8B 1A A7'
                 ' FF E9 5A 6B D3 FF B8 60 A9 91 00 E9 50 6B D3 FF 83 3D'
                 ' 70 3C DC 00 1D 75 09 0F 1F 40 00 B8 00 00 00 00 83 E0'
                 ' 10 85 C0 E9 1A 80 D3 FF 83 3D 08 E3 99 00 02 7F 22 0F'
                 ' 1F 40 00 83 3D 14 E3 99 00 1D 75 15 0F 1F 40 00 50 8A'
                 ' 45 08 38 05 08 E3 99 00 58 0F 85 69 BF A8 FF 88 84 11'
                 ' 20 8B 9A 00 E9 5D BF A8 FF 80 FA 1D 0F 85 11 02 00 00'
                 ' A0 A0 E3 99 00 3A 05 30 02 DC 00 0F 84 DC 01 00 00 3A'
                 ' 05 31 02 DC 00 0F 84 D0 01 00 00 3A 05 32 02 DC 00 0F'
                 ' 84 C4 01 00 00 80 3D 3F D4 6D 00 9B 75 09 0F 1F 40 00'
                 ' E8 42 C9 F6 FF E8 04 EF C1 FF 31 C0 A0 08 E3 99 00 8B'
                 ' 0D A0 E3 99 00 88 88 30 02 DC 00 88 88 B0 D1 9A 00 C7'
                 ' 05 FA 87 9A 00 00 00 00 00 C7 05 0A 88 9A 00 00 00 00'
                 ' 00 C7 05 1A 88 9A 00 00 00 00 00 55 8B EC 83 EC 10 51'
                 ' C7 45 F8 00 00 00 00 6B D0 44 89 55 FC EB 16 0F 1F 00'
                 ' 8B 55 F8 83 C2 01 89 55 F8 83 7D F8 10 7D 14 0F 1F 40'
                 ' 00 8B 55 FC 8B 4D F8 C6 84 11 20 8B 9A 00 00 EB DD 6B'
                 ' D0 68 C7 82 DC B0 9A 00 00 00 00 00 59 89 45 FC 89 4D'
                 ' F4 C7 45 F8 00 00 00 00 69 C9 84 00 00 00 31 D2 8A 91'
                 ' 9A FD DB 00 4A 6B D2 03 89 55 F0 8B 55 F0 03 55 F8 52'
                 ' FF 75 F4 E8 76 09 C2 FF 83 C4 08 8B 55 FC 69 D2 40 04'
                 ' 00 00 8B 4D F8 89 84 11 44 A5 DB 00 6B C0 1C 8D B0 D4'
                 ' F6 91 00 8B 55 FC 8B C2 69 C0 40 04 00 00 6B C9 1C 8D'
                 ' BC 08 4C A5 DB 00 B9 07 00 00 00 F3 A5 8B C1 FF 45 F8'
                 ' 83 7D F8 03 7C AB 83 C4 10 8B E5 5D 31 C0 FF 35 08 E3'
                 ' 99 00 90 E8 F3 D6 D1 FF E8 5F 35 C2 FF 58 E8 DD 78 C2'
                 ' FF 31 C0 A0 08 E3 99 00 6B C8 34 8A 91 C0 8D 9A 00 80'
                 ' FA FF 74 17 0F 1F 40 00 6A 00 6A 01 50 6A 00 E8 A1 D3'
                 ' A8 FF 83 C4 04 58 83 C4 08 6A 00 E8 1C BC A8 FF 6A 01'
                 ' E8 15 BC A8 FF 6A 02 E8 0E BC A8 FF 83 C4 0C E8 A0 26'
                 ' A8 FF E8 B2 30 A8 FF 8B 0D DC B0 9A 00 81 E1 00 08 00'
                 ' 00 85 C9 74 0E 0F 1F 40 00 C7 05 F2 23 BF 00 06 00 00'
                 ' 00 8B 0D 44 B1 9A 00 81 E1 00 08 00 00 85 C9 74 0E 0F'
                 ' 1F 40 00 C7 05 66 24 BF 00 06 00 00 00 8B 0D AC B1 9A'
                 ' 00 81 E1 00 08 00 00 85 C9 74 0E 0F 1F 40 00 C7 05 DA'
                 ' 24 BF 00 06 00 00 00 80 3D 3F D4 6D 00 9B 75 09 0F 1F'
                 ' 40 00 E8 8F C6 F6 FF A1 08 E3 99 00 69 C0 EC 1A 00 00'
                 ' 80 88 B6 11 BE 00 20 A1 0C CE 99 00 E9 97 E9 A8 FF'),
    0x9A7F00: _h('83 3D 70 3C DC 00 1D 74 05 E8 57 7D D3 FF E9 41 7D D3'
                 ' FF 83 3D 70 3C DC 00 1D 75 0B 83 3D 9C EF 91 00 06 75'
                 ' 02 B2 FF 88 91 F0 7F 9A 00 E9 E0 16 A7 FF 83 3D 70 3C'
                 ' DC 00 1D 75 02 31 D2 66 89 15 84 3C DC 00 E9 69 1D D3'
                 ' FF 83 3D 70 3C DC 00 1D 75 03 31 C0 48 66 A3 88 20 DC'
                 ' 00 E9 F3 1D D3 FF'),
}
_PHS_SITES = [
    (0x6DFA02, 0xD435A8, 0x940EE37E, 'x3e_phs_rows_0'),
    (0x6DFAE5, 0xD43944, 0xB9400688, 'x3e_phs_list_0'),
    (0x6DFBF1, 0xD43DA8, 0xB9400288, 'x3e_phs_all_0'),
    (0x6DFC4F, 0xD43F48, 0xB9401288, 'x3e_phs_mp_0'),
    (0x6D9C64, 0xD653BC, 0x53010509, 'x3e_phs_pick_0'),
    (0x6D9C77, 0xD65408, 0xB9400AE8, 'x3e_phs_empty_0'),
    (0x6D9C93, 0xD6562C, 0x940E5B5D, 'x3e_phs_id_0'),
    (0x6D9CAB, 0xD65688, 0x940E5B46, 'x3e_phs_target_0'),
    (0x6D9D4D, 0xD65770, 0x940E5B0C, 'x3e_phs_menu_0'),
    (0x5CB3D1, 0x8129E8, 0x1400001E, 'x3e_phs_sound_0'),
    (0x6DE70B, 0xD423A4, 0xB9401288, 'x3e_phs_name_0'),
    (0x433B69, 0xEE214, 0x94403863, 'x3e_phs_targets_0'),
    (0x41960C, 0x84A6C, 0x9441DE4D, 'x3e_phs_glyph_0'),
    (0x4367DB, 0xF790C, 0x2A1403E0, 'x3e_phs_swap_0'),
    (0x5CF873, 0x82576C, 0xB9000016, 'x3w_phs_or'),
]
_PHS_COMMAND = (0x6D9528, 0xD63940, 0x0B080128, 'x3e_phs_command_0')
# the patches that need no cave: kept ATBs, statuses and the doom timer on
# a swap (stores dropped), and the party leader swappable in the field
# (a `je` made `jmp`)
_PHS_WORDS = [
    (0x5CFA17, 0x825FD4, 0x7900001F, NOP),           # strh wzr, [x0]
    (0x4376A7, 0xFA2AC, 0x3400322A, NOP),            # cbz w10 (je 0x437800)
    (0x5D02DB, 0x826F08, 0xB9000014, NOP),           # str w20, [x0]
    (0x5D03EA, 0x827224, 0x39000008, NOP),           # strb w8, [x0]
    (0x7007DE, 0xDBA78C, 0x340001EA, 0x1400000F),    # cbz w10 -> b +0xDBA7C8
    (0x70084E, 0xDBA660, 0x3500098A, NOP),           # cbnz w10 (to the je's not-taken)
    (0x700906, 0xDBAAFC, 0x340001EA, 0x1400000F),    # cbz w10 -> b +0xDBAB38
    (0x700969, 0xDBAA08, 0x350007CA, NOP),           # cbnz w10
]


PORTS = (
    Port('Level Cap', {
        0x5C77D0: _h('E8 5B E2 1E 00 90 90'),
        0x5C839D: _h('E8 7E D6 1E 00 90 90'),
        0x7B5A00: _h('8B 15 DC 08 DC 00 B9 3D 5A 7B 00 83 C1 03 66 39 11 7C'
                     ' F8 8A 49 02 38 C8 C3 90'),
        0x7B5A20: _h('8D 44 10 FF E8 D7 FF FF FF C3'),
        0x7B5A30: _h('89 45 FC E8 C8 FF FF FF C3'),
    }, {
        0x7B5A40: ('lc_table', _level_table),
    }, [
        (0x5C77D0, 0x7F7744, 0x51018D09, 'lc_site_a'),   # sub w9, w8, #0x63
        (0x5C839D, 0x7FC720, 0x51019128, 'lc_site_b'),   # sub w8, w9, #0x64
    ]),
    Port('Enemy Speed Multiplier', {
        0x433B21: _h('E9 62 84 38 00 66 90'),
    }, {
        0x7BBF88: ('sm_mult', _speed),
    }, [
        (0x433B21, 0xEDF3C, 0x79000013, 'sm_site'),      # strh w19, [x0]
    ]),
    Port('Steal Modifier', {}, {
        0x5CA8F5: ('sl_lvl', _steal_level),
        0x5CA933: ('sl_div', _steal_divisor),
    }, [], lambda v: _steal_inline(v, False)),
    Port('Steal Modifier', {
        0x5CA8F8: _h('E9 1D 17 1F 00 90'),
        0x7BC01A: _h('8B 0D 0C CE 99 00 8B 11 6B D2 68 8B 92 F1 B0 9A 00 81'
                     ' E2 FF 00 00 00 01 D0 E9 C6 E8 E0 FF'),
    }, {
        0x5CA8F5: ('sl_lvl', _steal_level),
        0x5CA933: ('sl_div', _steal_divisor),
    }, [
        (0x5CA8F8, 0x80F980, 0x1100A108, 'sl_site'),     # add w8, w8, #0x28
    ], lambda v: _steal_inline(v, True)),
) + tuple(
    Port('AP Share',
         {0x5C6F28: _h('E9 D3 02 3E 00'), 0x9A7200: _h(AP_SHARE), **extra},
         {}, [
        (0x5C6F28, 0x7F572C, 0x940000A9, 'ap_site'),     # bl x86 0x5C7260
    ], inline)
    # alone, and with EXP Share's `sar eax, 1` NOPed (as ff7nx_hextarm does)
    for extra, inline in (
        ({}, None),
        ({0x5C6F01: _h('90 90')},
         lambda v: [(0x5C6F01, 0x7F5634, 0x13017D1A, 0x13007D1A)]),
    )) + (
    # x86 0x5DA117 `mov [edx], cl` NOPed: ARM `strb w21, [x0]`
    Port('Animation Skip', {0x5DA117: _h('90 90')}, {}, [],
         _fixed([(0x5DA117, 0x849F6C, 0x39000015, NOP)])),
    Port('Summon Animation Skip', {
        0x5DA117: _h('E9 19 B5 1D 00'),
        0x7B5635: _h('A0 14 E3 99 00 3C 03 74 0A 3C 16 74 06 0F 1F 40 00 88 0A'
                     ' 8B 55 FC E9 CC 4A E2 FF'),
    }, {}, [
        (0x5DA117, 0x849F6C, 0x39000015, 'sa_site'),     # strb w21, [x0]
    ]),
    # x86 0x4362DC `cmp eax, 5; jl` -> `cmp eax, 3; je`. ARM +0xF6730..
    # +0xF674C computed SF and OF of the cmp, stored them, and branched on
    # SF != OF; now: w9 = eax - 3, SF and OF (0) stored, cbz w9. Both paths
    # reload what they use. x86 0x5C985D `jl` NOPed: ARM `b.ne`.
    Port('Counters Add Effects', {
        0x4362DC: _h('83 F8 03 74 1B'),
        0x5C985D: _h('90 90'),
    }, {}, [], _fixed([
        (0x4362DC, 0xF6730, 0x51001509, 0x51000D09),   # sub w9, w8, #3
        (0x4362DC, 0xF6734, 0x0A080148, 0x531F7D2A),   # lsr w10, w9, #31
        (0x4362DC, 0xF6738, 0x531F7D29, 0x52800008),   # mov w8, #0
        (0x4362DC, 0xF673C, 0x531F7D08, 0x390092EA),   # strb w10, [x23, #0x24]
        (0x4362DC, 0xF6740, 0x6B09011F, 0x39009AE8),   # strb w8, [x23, #0x26]
        (0x4362DC, 0xF6744, 0x390092E9, 0x340003C9),   # cbz w9, +0xF67BC
        (0x4362DC, 0xF6748, 0x39009AE8, NOP),
        (0x4362DC, 0xF674C, 0x54000381, NOP),
        (0x5C985D, 0x807400, 0x54000401, NOP),
    ])),
    Port('Dialogue Mash', {
        0x64991F: _h('E9 9C D7 35 00 90 90 90 90 90 90 90 90 90'),
    }, {
        0x9A70C0: ('dm_mask', _mash),
    }, [
        (0x64991F, 0xA1BE6C, 0x340000C9, 'dm_site'),     # cbz w9, +0x18
    ]),
    Port('Fix Overflow', {
        0x5DE667: _h('0F AF C1 F7 62 48 C1 E8 0D 85 D2 74 0E C1 E2 17 01 D0'
                     ' EB 07'),
        0x5DE99D: _h('F7 E1 C1 E8 0C 85 D2 74 05 C1 E2 14 01 D0 85 C0 74 01'
                     ' 40 EB 04 90'),
    }, {}, [
        (0x5DE667, 0x858030, 0x1B097D08, 'fo_dmg'),      # mul w8, w8, w9
        (0x5DE99D, 0x858D0C, 0x1B097D08, 'fo_rng'),      # mul w8, w8, w9
    ]),
    Port('Party Leader Override', {
        0x767415: _h('E9 72 FF FF FF'),
    }, {
        0x767376: ('pl_mode', _leader),
    }, [
        (0x767376, 0xF2FEAC, 0x9407313D, 'pl_site'),     # bl translator
    ]),
) + tuple(
    Port('No Battle Music', {**_NO_BATTLE_MUSIC, **extra}, {}, [
        (0x4093C6, 0x3ACC0, 0xB9000013, 'nb_push'),      # str w19, [x0]
        (0x40827B, 0x35F58, 0x79000015, 'nb_flag'),      # strh w21, [x0]
    ] + sites, inline)
    for extra, sites, inline in (
        ({}, [], None),
        # NoWorldNoField: also call 0x75E5C0(1) after the world map's music
        # setup, and drop the 0x430C95 sound call (its return slot push
        # and the `bl`; eax keeps the pushed [0x9AE0F4], as on PC)
        ({0x74E1A7: _h('E8 E4 74 06 00 90'),
          0x7B5690: _h('6A 01 E8 29 8F FA FF 58 8B 4D 08 83 39 01 C3'),
          0x430C95: _h('90 90 90 90 90')},
         [(0x74E1A7, 0xF2ADE8, 0x9407456E, 'nb_world')],
         _fixed([(0x430C95, 0xE6B10, 0x51001108, NOP),
                 (0x430C95, 0xE6B18, 0x9435A5BE, NOP)])),
    )) + (
    Port('True Wait Mode', {0x4320DC: _h('E9 01 F5 30 00 90')}, {
        0x7415E2: ('wm_easy', _wait),
    }, [
        (0x4320DC, 0xE79F8, 0x350057E8, 'wm_site'),      # cbnz w8, +0xE84F4
    ]),
    # Turbo Turns carries the Easy Run check and uses it as is
    Port('Turbo Turns', {
        0x6CE9F0: _h('E8 C0 6F 0E 00'),
        0x7B59B5: _h('6A 00 EB 12 E8 AC F8 C7 FF 6A 02 E8 F8 F7 C7 FF 58 E8 7C'
                     ' E9 C7 FF E8 DC C6 C7 FF E8 E1 CA C7 FF 85 C0 75 1B 59 41'
                     ' 84 ED 75 16 51 A0 D4 85 9A 00 24 0C 3C 0C 74 09 E8 0C BC'
                     ' F8 FF 85 C0 75 C5 59 C3'),
    }, {
        0x7415E2: ('tt_easy', _wait_easy),
    }, [
        (0x6CE9F0, 0xD140A4, 0x97CF4E1B, 'tt_site'),     # bl x86 0x4320AC
    ]),
) + tuple(
    # x86 0x434671 `jne` -> `jmp` (ARM `tbnz w8, #7` -> `b`), and the
    # target cursor's draw calls the cave (ps3tweaks_c.c pt_show_weakness)
    Port('Show Weakness', {
        0x6D0D57: _h(call),
        0x434671: _h('EB 07'),
    }, {
        cave: ('sw_sensed', _digest(sha, struct.pack('<I', sensed))),
    }, [
        (0x6D0D57, 0xD20C48, 0x940F6DD6, 'sw_site'),     # bl translator
    ], _fixed([(0x434671, 0xED1A8, 0x373800A8, A64_B_ED1A8)]))
    for call, cave, sha, sensed in (
        ('E8 A4 49 0E 00 90', 0x7B56D6,
         '0b86418e3c8a8d854387e6a8bf5607ee23bf6e141885d64e7b5af5bfbcd2b3b9',
         0),
        ('E8 5B 49 0E 00 90', 0x7B56B7,
         '2da5b93aef9bb46e47b9901cb11cfa5d602b89483456ccbc26adab2d6b628813',
         1),
    )) + (
    # Enablers/MorphAsWell: materia type 0x60 takes type 0x5D's cases in two
    # jump tables (each ARM dispatch is steered where it loads the entry),
    # with its own value in the first; and the morph-pair call is also made
    # for the flag at 0x99E3F8.
    Port('Morph As Well', {
        0x5CBC6F: _h('52 BB'),
        0x5CBB52: _h('E9 A9 A7 34 00'),
        0x5DF1E3: _h('E9 2C 71 33 00'),
        0x916300: _h('B2 08 83 F8 0C 74 02 B2 04 88 15 CC 68 C0 00 E9 45 58'
                     ' CB FF 85 C9 75 0F 8B 0D F8 E3 99 00 83 E1 04 85 C9 75'
                     ' 02 EB 05 E8 29 49 CB FF E9 BB 8E CC FF'),
        0x5CDA00: _h('A1'),
    }, {}, [
        (0x5CBAD4, 0x819008, 0xB9400000, 'mw_case'),     # ldr w0, [x0]
        (0x5CBB52, 0x8195E0, 0x321D03E8, 'mw_byte'),     # mov w8, #8
        (0x5CD964, 0x822964, 0xB9400000, 'mw_vis'),      # ldr w0, [x0]
        (0x5DF1E3, 0x84DFD0, 0x366FE6E8, 'mw_pair'),     # tbz w8, #13
    ]),
    # Enablers/APPlus: weapon and armour AP counts linked AP Plus materia
    # (ps3tweaks_c.c pt_ap_plus), and type 0x61 takes 0x5D's visual case.
    Port('AP Plus', {
        0x5CAE0C: _h('80 7D EC FF 74 28 80 7D EC 2C 74 22 81 7D F8 FF FF FF'
                     ' 00 74 19 8B 0D B0 68 C0 00 81 C1 0E 04 00 00 0F B6 01'
                     ' 83 C1 16 E8 07 00 00 00 EB 68 E9 EF 00 00 00 31 D2 66'
                     ' 8B 55 0C F7 EA 50 31 C0 40 8B D1 03 55 E4 80 3A 07 75'
                     ' 20 8B 4D F0 52 31 D2 8A 51 FC 80 FA FF 74 11 6B D2 14'
                     ' 8B 8A 6D DF DB 00 66 81 F9 25 61 75 01 40 5A 80 7A 01'
                     ' 07 75 1E 8B 4D F0 31 D2 8A 51 04 80 FA FF 74 11 6B D2'
                     ' 14 8B 8A 6D DF DB 00 66 81 F9 25 61 75 01 40 59 F7 E9'
                     ' 01 45 F8 C3 90 90 8B 55 F8'),
        0x5CAFF6: _h('83 C1 09 31 C0 8A 41 08 E8 3C FE FF FF EB 47 90'),
        0x5CDA04: _h('A1'),
    }, {}, [
        (0x5CAE0C, 0x810DC0, 0xB9401688, 'ap_weapon'),   # ldr w8, [x20, #0x14]
        (0x5CAFF6, 0x811468, 0xB9401688, 'ap_armour'),   # ldr w8, [x20, #0x14]
        (0x5CD964, 0x822968, 0x5284D2A8, 'ap_vis'),      # mov w8, #0x2695
    ]),
    # Enablers/MateriaDrops: item ids from 0x140 are materia (id - 0x140)
    # when stolen, morphed, dropped or named.
    Port('Materia Drops', {
        0x5CA975: _h('E9 D6 B9 34 00 90'),
        0x419B73: _h('E9 03 C8 4F 00'),
        0x5CAD0C: _h('E9 97 B6 34 00'),
        0x6C716D: _h('0F BF 45 E0 6B C0 06 E8 57 F2 24 00 66 90 51'),
        0x6C71C2: _h('E8 09 F2 24 00 66 90'),
        0x5C8551: _h('E8 98 DE 34 00 66 90'),
        0x916350: _h('81 E2 FF FF 00 00 81 FA 40 01 00 00 0F 8C 19 46 CB FF'
                     ' 81 EA 40 01 00 00 52 E8 7C 5D DB FF 83 C4 04 BA 56 00'
                     ' 00 00 E9 15 46 CB FF 8B 45 EC 25 FF FF 00 00 3D 40 01'
                     ' 00 00 7D 07 6A 08 E9 E7 37 B0 FF 2D 40 01 00 00 68 17'
                     ' D9 CE 3D 6A 07 50 E8 63 07 DB FF E9 DD 37 B0 FF 25 FF'
                     ' FF 00 00 3D 40 01 00 00 0F 8C 59 49 CB FF 2D 40 01 00'
                     ' 00 50 E8 27 5D DB FF 83 C4 04 B8 56 00 00 00 E9 57 49'
                     ' CB FF 0F BF 88 F0 E2 99 00 81 F9 40 01 00 00 7D 01 C3'
                     ' 66 81 E9 40 01 58 51 6A 0D 83 C0 05 FF E0 0F BF 88 F2'
                     ' E2 99 00 81 FA 40 01 00 00 7D 01 C3 66 81 EA 40 01 51'
                     ' 52 E8 E0 5C DB FF 5A 59 49 85 C9 75 F2 58 E9 64 22 CB'
                     ' FF'),
    }, {}, [
        (0x5CA975, 0x80FBA4, 0x79400016, 'md_steal'),    # ldrh w22, [x0]
        (0x5CAD0C, 0x810938, 0x79400016, 'md_morph'),    # ldrh w22, [x0]
        (0x5C8551, 0x7FCE08, 0xB9401668, 'md_drop'),     # ldr w8, [x19, #0x14]
        (0x419B73, 0x86CE0, 0x97FFF78C, 'md_name'),      # bl x86 0x41963C
        (0x6C716D, 0xCEF1D0, 0x97CE5650, 'md_vis1'),     # bl x86 0x41963C
        (0x6C71C2, 0xCEF32C, 0x97CE55F9, 'md_vis2'),     # bl x86 0x41963C
    ]),
    # Misc/FreePartyFix: the party menus let required members be swapped
    # out (four `je` -> `jmp`), and the out-of-battle party is only updated
    # from a party that may be it (ps3tweaks_c.c pt_fp_legal).
    Port('Free Party Fix', {
        0x766DA6: _h('E9 C4 0B 24 00'),
        0x61C57A: _h('E8 DA B3 38 00 90'),
        0x767438: _h('E9 45 05 24 00'),
        0x7007DE: _h('EB'),
        0x70084E: _h('EB'),
        0x700906: _h('EB'),
        0x700969: _h('EB'),
        0x9A7900: _h('52 51 53 66 81 3D DC 08 DC 00 81 01 7C 45 66 8B 15 DC'
                     ' 0D DC 00 66 23 15 DE 0D DC 00 66 81 E2 FF 01 31 DB 8A'
                     ' 8B 30 02 DC 00 80 F9 FF 74 25 31 C0 40 D3 E0 66 23 05'
                     ' DE 0D DC 00 66 85 C0 74 14 F7 D0 21 C2 43 80 FB 03 7C'
                     ' DA 66 85 D2 75 05 31 C0 40 EB 02 31 C0 5B 59 5A C3 E8'
                     ' A2 FF FF FF 85 C0 0F 84 50 4C C7 FF 59 83 EC 08 8D 45'
                     ' FC FF E1 E8 8C FF FF FF 85 C0 74 05 E8 9B F8 DB FF E9'
                     ' 29 F4 DB FF E8 79 FF FF FF 85 C0 74 05 E8 88 F8 DB FF'
                     ' E9 A8 FA DB FF 90'),
    }, {}, [
        (0x766DA6, 0xF2C2E8, 0x9401CC02, 'fp_call_a'),   # bl x86 0x767218
        (0x767438, 0xF46F24, 0x940160F3, 'fp_call_b'),   # bl x86 0x767218
        (0x61C57A, 0x982E0C, 0xB9401268, 'fp_menu'),     # ldr w8, [x19, #0x10]
    ], _fixed([
        # the required-member checks: cbnz -> nop, cbz -> b
        (0x7007DE, 0xDBA660, 0x3500098A, NOP),
        (0x70084E, 0xDBA78C, 0x340001EA, 0x1400000F),    # b +0xDBA7C8
        (0x700906, 0xDBAA08, 0x350007CA, NOP),
        (0x700969, 0xDBAAFC, 0x340001EA, 0x1400000F),    # b +0xDBAB38
    ])),
    # MorePairs: Replacement (type 2) command materia pairs like type 6, in
    # the pair tables, counters and sneak attacks; secondary commands pair
    # whenever there is one; All works on more command types.
    Port('More Pairs', {
        0x5CC7B9: _h('E9 A2 98 34 00 90'),
        0x5CC96A: _h('80 7D F0 FF 75 08'),
        0x916060: _h('83 FA 02 75 05 BA 06 00 00 00 83 EA 06 89 55 F0 E9 4A'
                     ' 67 CB FF'),
        0x5CBE49: _h('3C 02 74 0A 3C 06 74 06 3C 08'),
        0x5CC10D: _h('83 7D F0 03 0F 84 B2 00 00 00'),
        0x5CC122: _h('90 90 90 90 90 90 90'),
        0x5CC1DE: _h('80 7D EC FF 75 0B'),
        0x5CBF97: _h('E9 26 A1 34 00 90'),
        0x9160C2: _h('8B 55 F0 80 FA 02 75 02 B2 06 83 EA 06 E9 C9 5E CB FF'),
        0x5CCFAD: _h('8A 55 FC E8 05 00 00 00 E9 82 00 00 00 8B 0D B0 68 C0'
                     ' 00 83 C1 4C 8B 01 3C FF 74 0A E8 06 00 00 00 83 C1 06'
                     ' EB F0 C3 2C 05 3C 02 72 0B 2C 04 3C 03 72 05 3C 05 74'
                     ' 01 C3 8B 41 04 00 D0 89 41 04 C3 31 C0 8A 45 F4 6B C0'
                     ' 14 50 8A 90 6E DF DB 00 52 E8 78 FE FF FF 6B C0 03 5A'
                     ' 5A 8B 88 21 69 C0 00 03 4D 0C 89 88 21 69 C0 00 8A 82'
                     ' 6F DF DB 00 3C FF 74 19 50 E8 54 FE FF FF 6B C0 03 5A'
                     ' 8B 88 21 69 C0 00 03 4D 0C 89 88 21 69 C0 00 C3 90'),
        0x5CCD66: _h('8B 82 6D DF DB 00 83 E0 0F 3C 06 75 0B E8 76 02 00 00'
                     ' 31 C0 90 90 90 90 3C 09 74 06 3C 0A'),
    }, {}, [
        (0x5CC7B9, 0x819E54, 0x51001914, 'mp_rep'),      # sub w20, w8, #6
        (0x5CBF97, 0x81A5BC, 0x51001915, 'mp_sneak'),    # sub w21, w8, #6
        (0x5CBE49, 0x81AED4, 0x7100191F, 'mp_counter'),  # cmp w8, #6
        (0x5CC10D, 0x81B71C, 0x71001D1F, 'mp_pri'),      # cmp w8, #7
        (0x5CCD66, 0x819B88, 0x529BEDA9, 'mp_all'),      # mov w9, #0xDF6D
        (0x5CCFAD, 0x817784, 0x2A1F03F9, 'mp_mega'),     # mov w25, wzr
    ], _fixed([
        # the secondary command: `cmp 7 / je` -> `cmp 0xFF / jne`
        (0x5CC96A, 0x81D54C, 0x71001D1F, 0x7103FD1F),    # cmp w8, #0xFF
        (0x5CC96E, 0x81D558, 0x54000120, 0x54000121),    # b.ne
        (0x5CC1DE, 0x81BA08, 0x71001D1F, 0x7103FD1F),
        (0x5CC1E2, 0x81BA14, 0x54000120, 0x54000121),
    ])),
    # FixEquipDisplay: the equip menu's stats count the equipment's stat
    # bonuses (ps3tweaks2_c.c pt_fe_*), in a frame widened to 0x6C.
    Port('Fix Equip Display', {
        0x707896: _h('6C'),
        0x707A44: _h('8B 45 BC 89 45 D4'),
        0x707952: _h('81 C2 34 E7 DB 00 8A 02 89 45 E8 89 45 A4 8A 42 04 89'
                     ' 45 F8 89 45 C0 83 C2 10 51 B1 02 E8 05 00 00 00 89 45'
                     ' BC EB 15 31 C0 38 0C 10 74 09 40 83 F8 04 75 F5 31 C0'
                     ' C3 8A 44 10 04 C3 89 45 B0 49 E8 E2 FF FF FF 89 45 F4'
                     ' 89 45 D0 89 45 A0 B1 03 E8 D2 FF FF FF 89 45 CC 89 45'
                     ' E4 89 45 9C 5A 8A 82 A9 FD DB 00 6B C0 24 31 C9 8D 90'
                     ' E2 CC DB 00 8A 0A 01 4D F4 01 4D D0 89 4D 98 8A 4A 02'
                     ' 89 4D EC 89 4D E0 83 C2 16 B1 02 E8 99 FF FF FF 01 45'
                     ' BC 89 45 AC 31 C9 E8 8C FF FF FF 01 45 E8 89 45 A8 31'
                     ' C0 8A 42 EB 89 45 94 01 45 CC 01 45 E4 8B 45 E8 89 45'
                     ' F0 8A 42 ED 89 45 C8 89 45 D8 EB 2B 89 45 F0 B1 02 E8'
                     ' 5D FF FF FF 03 45 AC 89 45 D4 B1 01 E8 50 FF FF FF 03'
                     ' 45 98 89 45 D0 B1 03 E8 43 FF FF FF 03 45 94 89 45 E4'
                     ' C3'),
        0x707BA1: _h('31 C0 8D 91 34 E7 DB 00 8A 42 04 89 45 C0 8A 02 03 45'
                     ' A8 83 C2 10 E8 56 FE FF FF 90'),
        0x707BD6: _h('6B C8 24 8D 91 E2 CC DB 00 8A 02 03 45 A0 89 45 D0 8A'
                     ' 42 02 89 45 E0 8A 42 01 03 45 9C 89 45 E4 90 83 C2 16'
                     ' B1 02 E8 78 FD FF FF 03 45 B0 89 45 D4 8A 42 ED 89 45'
                     ' D8 31 C9 E8 65 FD FF FF 03 45 A4 89 45 F0'),
    }, {}, [
        (0x707952, 0xDD86BC, 0xB9000A88, 'fe_now'),      # str w8, [x20, #8]
        (0x707BA1, 0xDD9228, 0xB9401688, 'fe_weapon'),   # ldr w8, [x20, #0x14]
        (0x707BD6, 0xDD90C8, 0xB9401688, 'fe_armour'),   # ldr w8, [x20, #0x14]
    ], _fixed([
        (0x707894, 0xDD8368, 0x51013108, 0x5101B108),    # sub esp, 0x6C
    ])),
    # Enablers/UnlimitedAutoSense: Sense shows full 32-bit HP / MP and has
    # no HP limit; Auto also senses every enemy; ShowStealAndMorph also
    # draws what can be stolen and what it morphs into (ps3tweaks2_c.c
    # pt_us_case).
    Port('Unlimited Sense', _us(_US_COMMON, _US_PLAIN), {},
         [_US_CASE + ('us_case',)] + _US_LIMIT),
    Port('Unlimited Auto Sense', _us(_US_COMMON, _US_PLAIN, _US_AUTO), {},
         [_US_CASE + ('us_case',)] + _US_LIMIT, _fixed(_US_AUTO_WORD)),
    Port('Show Steal And Morph', _us(_US_COMMON, _US_SHOW, _US_AUTO), {},
         [_US_CASE + ('us_case_show',)] + _US_LIMIT + _US_CALLS,
         _fixed(_US_AUTO_WORD)),
    Port('Show Steal And Morph (No Auto)', _us(_US_COMMON, _US_SHOW), {},
         [_US_CASE + ('us_case_show',)] + _US_LIMIT + _US_CALLS),
    # AnimationSkip/ToggleAnimationSkip: a button turns the battle animation
    # skip on and off (ps3tweaks2.S ta_*).
    Port('Toggle Animation Skip', {
        0x5DA117: _h('E9 19 B5 1D 00'),
        0x41C0C1: _h('E9 8A 95 39 00 90 90'),
        0x7B5635: _h('A0 68 BE 9A 00 83 E0 01 83 F8 00 75 06 0F 1F 40 00 88'
                     ' 0A 8B 55 FC E9 CC 4A E2 FF C7 45 FC 00 00 00 00 A0 68'
                     ' BE 9A 00 8A 0D D4 85 9A 00 83 E1 01 83 F9 01 75 15 0F'
                     ' 1F 40 00 3C 01 7F 0F 0F 1F 40 00 24 01 04 03 EB 05 0F'
                     ' 1F 00 24 01 A2 68 BE 9A 00 E9 3D 6A C6 FF'),
        0x436DE6: _h('A1 98 EA 9A 00 40 83 F8 50 7C 02 31 C0 A3 98 EA 9A 00'),
        0x41CCE0: _h('B9 18 01 00 00'),
    }, {}, [
        (0x5DA117, 0x849F6C, 0x39000015, 'ta_store'),    # strb w21, [x0]
        (0x41C0C1, 0x91B60, 0xB900001F, 'ta_toggle'),    # str wzr, [x0]
        (0x436DE6, 0xF90C8, 0x12001915, 'ta_count'),     # and w21, w8, #0x7F
    ], _fixed([
        # rep stosd from 0x9ABA08: 0x118 dwords (was 0x1C0), short of
        # 0x9ABE68
        (0x41CCE0, 0x8E62C, 0xB25A0BE8, 0xD2C02308),
    ])),
    # MultiLinkedSlots (every variant the same words): the weapon's and the
    # armour's materia pairs run over all seven neighbouring slots, the
    # 2-3 pair is never special, and 6-7's test is any slot whose link
    # bits are both set.
    Port('Multi Linked Slots', {
        0x5CB41D: _h('83 7D E8 07'),
        0x5CB43F: _h('83 C2 00'),
        0x5CB45B: _h('8A 8C 10 25 04 00 00'),
        0x5CB46B: _h('8A 8C 10 24 04 00 00'),
        0x5CB8E4: _h('EB 2A'),
        0x5CB921: _h('83 E2 03 90 90 90 83 FA 03'),
        0x5CB919: _h('39 C9 90'),
        0x5CB496: _h('83 7D E8 07'),
        0x5CB4B8: _h('83 C2 00'),
        0x5CB4DA: _h('83 C2 00'),
    }, {}, [
        (0x5CB921, 0x815718, 0x71001D1F, 'ml_slot'),     # cmp w8, #7
    ], _fixed([
        # `cmp [ebp-0x18], 4; jge` as SF / OF: i - 4, and 3 for OF
        (0x5CB41D, 0x812D1C, 0x51001109, 0x51001D09),    # sub w9, w8, #7
        (0x5CB41D, 0x812ADC, 0x320007F4, 0x528000D4),    # mov w20, #6
        (0x5CB43F, 0x812B74, 0x11001115, 0x11000115),    # add w21, w8, #0
        (0x5CB45B, 0x812C40, 0x0B090508, 0x0B090108),    # add w8, w8, w9
        (0x5CB46B, 0x812C94, 0x0B090508, 0x0B090108),    # add w8, w8, w9
        (0x5CB496, 0x813004, 0x51001109, 0x51001D09),    # sub w9, w8, #7
        (0x5CB496, 0x812D54, 0x320007F4, 0x528000D4),    # mov w20, #6
        (0x5CB4B8, 0x812DFC, 0x11001116, 0x11000116),    # add w22, w8, #0
        (0x5CB4DA, 0x812EAC, 0x11000516, 0x11000116),    # add w22, w8, #0
        (0x5CB8E4, 0x8155CC, 0x540008C1, 0x14000046),    # b +0x8156E4
        (0x5CB919, 0x8156F4, 0x7100191F, 0x6B08011F),    # cmp w8, w8
    ])),
    # BreakDamageHPLimit: damage up to 99,999 (with a level 4 limit, or
    # always) or 0x1FFFFFF0, shown with 5 or 9 digits; "HP" also raises the
    # HP limits to 32,767 and widens the HP displays (ps3tweaks2 bd_*).
    _bd('Conditional', 'bd_cap', 'bd_digits5', False),
    _bd('Conditional, HP', 'bd_cap', 'bd_digits5', True, menu=True,
        enemy=True, heal=True),
    _bd('Unconditional', 'bd_cap99', 'bd_digits5', False),
    _bd('Unconditional, HP', 'bd_cap99', 'bd_digits5', True, enemy=True),
    _bd('Unlimited', 'unlimited', 'bd_digits9', False),
    _bd('Unlimited, HP', 'unlimited+hp', 'bd_digits9', True, menu=True),
) + (
    # BUILD 868: Two Accessories, alone and with its ESUICompat file (one
    # port: ff7nx_pccode.translate_group hands both files over together)
    Port('Two Accessories', TWO_ACCESSORIES_CODE, {}, _TA_SITES + _TA_ROWS,
         _fixed(_ta_words(False))),
    Port('Two Accessories (ESUI layout)', {**TWO_ACCESSORIES_CODE, **_TA_ESUI},
         {}, _TA_SITES + _TA_ROWS_ESUI, _fixed(_ta_words(True))),
    Port('Limit Overhaul', LIMIT_OVERHAUL_CODE, {}, _LO_SITES,
         _fixed(_LO_WORDS)),
    Port('Enemy Scaling', ENEMY_SCALING_CODE, _ES_DATA, _ES_SITES),
    # BUILD 868: Solo Challenge, each character's file
    _sc_port('Solo Challenge (Cloud/Sephiroth)', SOLO_CLOUD_CODE, _SC_PARTY),
    _sc_port('Solo Challenge', SOLO_CODE,
             _SC_PARTY + [_SC_INIT + ('x3e_sc_init_0',)], _SC_WORDS),
    _sc_port('Solo Challenge (Cait Sith)', SOLO_CAIT_CODE,
             _SC_PARTY + [_SC_INIT + ('x3e_sc_init_cait_0',)],
             _SC_WORDS + _SC_CAIT_WORDS),
    _sc_port('Solo Challenge (Vincent)', SOLO_VINCENT_CODE,
             _SC_PARTY + [_SC_INIT + ('x3e_sc_init_vincent_0',)],
             _SC_WORDS + _SC_VINCENT_WORDS),
    # BUILD 868: Battle PHS, alone and with Limit Overhaul (whose folder
    # comes later: its 0x6D9545 `jmp` stands over the PHS's nops there, so
    # its command region runs, not phs_command)
    Port('Battle PHS', BATTLE_PHS_CODE, {}, _PHS_SITES + [_PHS_COMMAND],
         _fixed(_PHS_WORDS)),
    Port('Limit Overhaul + Battle PHS',
         {**BATTLE_PHS_CODE, **LIMIT_OVERHAUL_CODE}, {},
         _LO_SITES + _PHS_SITES, _fixed(_LO_WORDS + _PHS_WORDS)),
    Port('Solo Challenge (Random)', SOLO_RANDOM_CODE, {},
         [(0x41B5A5, 0x8D64C, 0x9441BB55, 'x3e_sc_party_random_0'),
          _SC_PARTY[1]]),
    Port('Party Randomizer', PARTY_RANDO_CODE, {},
         [(0x41B5A5, 0x8D64C, 0x9441BB55, 'x3e_pr_party_0')]),
    # BUILD 868: Enemy Randomizer, alone and with the Rando scaling file and
    # a % file over it
    Port('Enemy Randomizer', BATTLE_RANDO_CODE, {}, _BR_SITES + [
        (0x5D1237, 0x82CC28, 0xB9401308, 'x3e_br_stats_0'),
        (0x5C99B8, 0x80791C, 0x11003D00, 'x3e_br_level_0')]),
    Port('Enemy Randomizer + Enemy Scaling',
         {**BATTLE_RANDO_CODE, **RANDO_SCALER_CODE}, _ES_DATA, _BR_SITES + [
             (0x5D1237, 0x82CC28, 0xB9401308, 'x3e_br_stats_es_0'),
             (0x5C99B8, 0x80791C, 0x11003D00, 'x3e_br_level_es_0')]),
    Port('Enemy Randomizer + Double Bosses',
         {**BATTLE_RANDO_CODE, **DOUBLE_BOSSES_CODE}, {}, _DB_SITES + [
             (0x5D1237, 0x82CC28, 0xB9401308, 'x3e_br_stats_0'),
             (0x5C99B8, 0x80791C, 0x11003D00, 'x3e_br_level_0')],
         _fixed(_DB_WORDS), over={0x917000}),
    Port('Enemy Randomizer + Double Bosses + Enemy Scaling',
         {**BATTLE_RANDO_CODE, **DOUBLE_BOSSES_CODE, **RANDO_SCALER_CODE},
         _ES_DATA, _DB_SITES + [
             (0x5D1237, 0x82CC28, 0xB9401308, 'x3e_br_stats_es_0'),
             (0x5C99B8, 0x80791C, 0x11003D00, 'x3e_br_level_es_0')],
         _fixed(_DB_WORDS), over={0x917000}),
    Port('Item Randomizer', ITEM_RANDO_CODE,
         {0x916FF8: ('ir_text', _ir_text)}, _IR_SITES, _fixed(_IR_WORDS)),
    Port('Character Order Randomizer', CHARACTER_ORDER_CODE, {}, _CO_SITES,
         _fixed(_CO_WORDS)),
    Port('Starting Level Fix', STARTING_LEVEL_CODE, {}, _SL_SITES,
         _fixed(_SL_WORDS)),
)


class Refused(Exception):
    pass


def entries(blob=0):
    """{symbol: offset} of one blob."""
    out = {}
    with open(os.path.join(NATIVE, BLOBS[blob][0] + '.entry')) as f:
        for line in f:
            name, off = line.split()
            out[name] = int(off, 16)
    return out


def tramp_all():
    """TRAMP and the region blobs' own trampolines, x3_fn_<x86 hex>
    (tools/generators/ps3regions.py names them by their target)."""
    out = dict(TRAMP)
    for b in range(len(BLOBS)):
        for name in entries(b):
            if name.startswith('x3_fn_'):
                out[name] = int(name[6:], 16)
    return out


def native_tramps():
    """{x3_arm_<hex>: ARM offset}: the region blobs' trampolines to the
    port's own routines (ff7nx_ps3regions.NATIVE)."""
    out = {}
    for b in range(len(BLOBS)):
        for name in entries(b):
            if name.startswith('x3_arm_'):
                out[name] = int(name[7:], 16)
    return out


def owner(name):
    """The blob holding `name` (any but pt_g2h, which each has)."""
    for b in range(len(BLOBS)):
        if name in entries(b):
            return b
    raise KeyError(name)


def symbols(module, exe):
    """[(blob, symbol, ARM offset)] of every symbol of every blob, placed."""
    out = []
    for b in range(len(BLOBS)):
        lo, _hi = own_region(module, exe, b)
        out += [(b, n, lo + o) for n, o in sorted(entries(b).items())]
    return out


def match(patches):
    """(Port, {symbol: bytes}) for a parsed HEXT patch list, or None."""
    got, rewritten = {}, set()
    for va, blob in patches:
        if va in got and got[va] != bytes(blob):
            rewritten.add(va)       # a later file's value (a data byte only)
        got[va] = bytes(blob)
    for port in PORTS:
        if set(got) != set(port.code) | set(port.data):
            continue
        if not rewritten <= set(port.data) | port.over:
            continue
        if any(got[va] != b for va, b in port.code.items()):
            continue
        data = {}
        for va, (sym, parse) in port.data.items():
            v = parse(got[va])
            if v is None:
                break
            data[sym] = v
        else:
            return port, data
    return None


def own_region(module, exe, blob=0):
    """(lo, hi) of a blob's dead body, proven dead and still stock."""
    import ff7nx_fieldbg
    import ff7nx_fxinterp
    dead = BLOBS[blob][1]
    why = ff7nx_fieldbg._liveness(module, dead)
    if not why:
        why = ff7nx_fxinterp._x86_unreferenced(exe, dead)
    if why:
        raise Refused('x86 0x%X is not dead: %s' % (dead, why))
    lo, hi = module.extent(dead)
    return (lo + OWN_GUARD + 15) & ~15, hi - OWN_TAIL


def _check_site(module, x86, arm, stock):
    span = module.containing(x86)
    a0, a1 = module.extent(span[0]) if span else (0, 0)
    if not a0 <= arm < a1:
        raise Refused('+0x%X is not in the body of x86 0x%X' % (arm, x86))
    have = struct.unpack_from('<I', module.img, arm)[0]
    if have != stock:
        raise Refused('+0x%X is %08X, not the stock %08X it was ported from'
                      % (arm, have, stock))


def _place(module, exe, b, port, data, out):
    """Blob b's words into `out`, its trampolines resolved and `data`
    written to the data areas the port owns; returns where it starts."""
    import a64 as A
    img = module.img
    code = open(os.path.join(NATIVE, BLOBS[b][0] + '.bin'), 'rb').read()
    sym = entries(b)
    lo, hi = own_region(module, exe, b)
    if lo + len(code) > hi:
        raise Refused('%d-byte blob does not fit the %d-byte body'
                      % (len(code), hi - lo))
    skip = set()
    for name, size in DATA.items():
        if name in sym:
            skip.update(range(sym[name], sym[name] + size))
    image = bytearray(code)
    for name, blob in data.items():
        if name not in DATA or name not in sym:
            continue
        image[sym[name]:sym[name] + len(blob)] = blob
        image[sym[name] + len(blob):sym[name] + DATA[name]] = \
            bytes(DATA[name] - len(blob))
        skip.difference_update(range(sym[name], sym[name] + DATA[name]))
    struct.pack_into('<I', image, sym['pt_g2h'],
                     A.b(lo + sym['pt_g2h'], GUEST_TO_HOST))
    for name, x86 in tramp_all().items():
        if name not in sym:
            continue
        if module.containing(x86) is None or module.containing(x86)[0] != x86:
            raise Refused('x86 0x%X is not a mapped function entry' % x86)
        struct.pack_into('<I', image, sym[name],
                         A.b(lo + sym[name], module.extent(x86)[0]))
    for name, arm in native_tramps().items():
        if name not in sym:
            continue
        import ff7nx_ps3regions as S
        want = S.NATIVE.get(arm)
        have = struct.unpack_from('<%dI' % len(want or ()), img, arm)
        if want is None or have != want:
            raise Refused('+0x%X is not the native routine it was ported to'
                          % arm)
        struct.pack_into('<I', image, sym[name], A.b(lo + sym[name], arm))
    for i in range(0, len(image), 4):
        if i in skip:
            continue
        word = struct.unpack_from('<I', image.ljust(i + 4, b'\0'), i)[0]
        out.append((lo + i, struct.unpack_from('<I', img, lo + i)[0], word,
                    '%s: cave' % port.name))
    return lo


def plan(exe, module, patches):
    """ff7nx_hextarm-form words for a cave option, None when `patches` is
    not one this module ports, Refused when it is but cannot be placed."""
    hit = match(patches)
    if hit is None:
        return None
    port, data = hit
    import a64 as A
    out = []
    for x86, arm, stock, word in (port.inline(data) if port.inline else ()):
        _check_site(module, x86, arm, stock)
        out.append((arm, stock, word, '%s: x86 0x%X' % (port.name, x86)))
    if not port.sites:
        if not out:
            raise Refused('%s: every value is the game\'s own' % port.name)
        return out
    for x86, arm, stock, entry in port.sites:
        _check_site(module, x86, arm, stock)
    at = {}
    for b in sorted({owner(e) for *_s, e in port.sites}):
        lo = _place(module, exe, b, port, data, out)
        at.update((n, lo + o) for n, o in entries(b).items())
    for x86, arm, stock, entry in port.sites:
        out.append((arm, stock, A.bl(arm, at[entry]),
                    '%s: x86 0x%X hook' % (port.name, x86)))
    return out
