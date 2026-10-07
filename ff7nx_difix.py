#!/usr/bin/env python3
"""ff7nx_difix.py -- "Double Input Bugfix (and menu input QoL)" on the
Switch. BUILD 620.

The user (10-06): "if you click down and a at the same time for instance, it
triggers down 2x. can you review the mod and figure out how we can safely get
it to apply with our build when enabled?"

WHAT THE MOD IS (Dig / potus-barret, v1.2, mod.xml ID 22ed7aef-...)
Three kinds of HEXT file, all patching ff7.exe CODE (.text):
  difix/hext/difix.txt     0041B09E = 34 87      0041B0AA = C6
  rate/<n>/hext/<n>.txt    0041B15F = BA <imm32> 90     (mov edx, n)
  delay/<n>/hext/<n>.txt   0041B128 = B8 <imm32>        (mov eax, n)
The default rate (50) and delay (200) ship no folder: stock.

WHY IT CANNOT APPLY AS SHIPPED. exefs/main is a prebuilt ARM64 recompilation
of the x86 .text; a .text byte changes nothing (build_exe skips such files
whole). So each patch is translated to the equivalent ARM64 word, the way
every other code constant in this project is changed.

WHAT THE x86 DOES (disassembled from the dump's ff7_en)
  0x41AB67 held(mask)    = [0x9A85D4] & mask     (buttons down)
  0x41AB74 pressed(mask) = [0x9A85E0] & mask     (went down this frame)
  0x41B099 check(mask):  if [0x9A85E4] == 0:  return held(mask)
                         else: return held(mask) ? [0x9A872C] : 0
  0x41B108 repeat tick:  fresh press -> [0x9A8734] = 0, timer = delay
                         ([0x9A85C8], the 0x41B128 load); each repeat ->
                         [0x9A8734] = 1, timer = rate ([0x9A85E4], 0x41B15F)
The fix makes check() test [0x9A8734] ("a repeat has fired") instead of the
rate, and call pressed() instead of held() in that branch: until the key
repeat actually starts, a button counts once, on the frame it goes down --
a held button no longer reads as a new press every frame, which is the
double input.

THE ARM64 (translated 0x41B099 at +0x8C360, 0x41B108 at +0x895F0; held()
is INLINED into check(), with the port's own `== 3` selector between the
held and pressed words)
  difix  +0x8C39C  add w0, w19, #0x10   -> add w0, w19, #0x160
                   (w19 = 0x9A85D4: the compared word 0x9A85E4 -> 0x9A8734)
         +0x8C520  mov w0, w19          -> add w0, w19, #0xC
                   (the inlined held() in the no-repeat branch reads
                    0x9A85E0, pressed, on both of its paths)
  delay  +0x8966C  ldr w22, [x0]        -> mov w22, #delay   (x86 eax)
  rate   +0x89730  ldr w22, [x0]        -> mov w22, #rate    (x86 edx)
                   ("Infinity" = 0xFFFFFFFF -> movn w22, #0)
Every site is found by its instruction signature inside the mapped function
(base register materialised as 0x9A85D4 / 0x9A8714, the guest-address `add`/
`sub`, the translate `bl`, the load) and its current word must be stock or
already this patch; anything else refuses the whole patch. In-place words
only: no cave, no BSS, no archive.

SEVENTH_NX_NO_DIFIX=1 disables.
"""
from __future__ import annotations

import os
import re
import struct

OFF_ENV = 'SEVENTH_NX_NO_DIFIX'
MOD_ID = '22ed7aef-254c-49cf-923f-32c146f1597f'
X86_CHECK = 0x41B099
X86_TICK = 0x41B108
TRANSLATE = 0x10FC3A0
HELD_BASE = 0x9A85D4
TICK_BASE = 0x9A8714
INF = 0xFFFFFFFF

CURRENT = None        # {'difix': bool, 'rate': int|None, 'delay': int|None,
#                        'files': [paths handled]}  set by build_plan


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def handles(mod):
    m = getattr(mod, 'manifest', None)
    return bool(m) and getattr(m, 'mod_id', '') == MOD_ID


# ------------------------------------------------------------------ HEXT
def _patches(text):
    import exe_patch
    return {va: bytes(b) for va, b in exe_patch.parse_hext(text)}


def classify(text):
    """('difix', None) / ('rate', n) / ('delay', n) for a file of this mod;
    raises on anything else (an unknown patch is not guessed at)."""
    p = _patches(text)
    if p == {0x41B09E: b'\x34\x87', 0x41B0AA: b'\xC6'}:
        return 'difix', None
    if set(p) == {0x41B15F} and len(p[0x41B15F]) == 6 \
            and p[0x41B15F][0] == 0xBA and p[0x41B15F][5] == 0x90:
        return 'rate', struct.unpack_from('<I', p[0x41B15F], 1)[0]
    if set(p) == {0x41B128} and len(p[0x41B128]) == 5 \
            and p[0x41B128][0] == 0xB8:
        return 'delay', struct.unpack_from('<I', p[0x41B128], 1)[0]
    raise ValueError('not a patch this module knows: %s'
                     % ', '.join('%X=%s' % (va, b.hex()) for va, b in
                                 sorted(p.items())))


def plan_from_hext(paths, log=lambda *_: None):
    """CURRENT from the mod's active HEXT files (build_plan collects them in
    7th Heaven order, so a later file wins as it would there)."""
    out = {'difix': False, 'rate': None, 'delay': None, 'files': []}
    for path in paths:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            kind, val = classify(f.read())
        if kind == 'difix':
            out['difix'] = True
        else:
            out[kind] = val
        out['files'].append(path)
    return out


# ----------------------------------------------------------------- ARM64
def _w(img, a):
    return struct.unpack_from('<I', img, a)[0]


def _add_imm(rd, rn, imm):
    return 0x11000000 | (imm << 10) | (rn << 5) | rd


def _sub_imm(rd, rn, imm):
    return 0x51000000 | (imm << 10) | (rn << 5) | rd


def _mov_reg(rd, rm):
    return 0x2A0003E0 | (rm << 16) | rd


def _movz(rd, imm):
    return 0x52800000 | (imm << 5) | rd


def _movk16(rd, imm):
    return 0x72A00000 | (imm << 5) | rd


def _ldr0(rt):
    return 0xB9400000 | rt                     # ldr wT, [x0]


def _mov_imm(rd, val):
    if val == INF:
        return 0x12800000 | rd                 # movn wD, #0  (= -1)
    if not 0 <= val <= 0xFFFF:
        raise ValueError('value %d does not fit one movz' % val)
    return _movz(rd, val)


def _is_bl_to(w, pc, target):
    if w >> 26 != 0b100101:
        return False
    imm = w & 0x3FFFFFF
    if imm & 0x2000000:
        imm -= 0x4000000
    return pc + imm * 4 == target


def _base_reg(img, a, e, value):
    """The register a movz/movk pair sets to `value` inside [a, e)."""
    lo, hi = value & 0xFFFF, value >> 16
    for pc in range(a, e - 4, 4):
        w = _w(img, pc)
        rd = w & 31
        if w == _movz(rd, lo) and _w(img, pc + 4) == _movk16(rd, hi):
            return rd
    raise ValueError('no %X base in +0x%X' % (value, a))


def _load_after(img, pc, e):
    """The `ldr wT, [x0]` right after the translate bl that follows pc."""
    q = pc + 4
    while q < e and q < pc + 16:
        if _is_bl_to(_w(img, q), q, TRANSLATE):
            ld = q + 4
            w = _w(img, ld)
            if w & 0xFFFFFFE0 == 0xB9400000:
                return ld, w & 31
            break
        q += 4
    raise ValueError('no translate+load after +0x%X' % pc)


def sites(module):
    """{'difix': [(va, stock, patched-builder)], 'rate': (va, reg),
    'delay': (va, reg)} found by signature."""
    img = module.img
    a, e = module.extent(X86_CHECK)
    r = _base_reg(img, a, e, HELD_BASE)
    cmp_add = [pc for pc in range(a, e, 4) if _w(img, pc) == _add_imm(0, r, 0x10)]
    patched_add = [pc for pc in range(a, e, 4)
                   if _w(img, pc) == _add_imm(0, r, 0x160)]
    if len(cmp_add) + len(patched_add) != 1:
        raise ValueError('check(): %d candidates for the rate test'
                         % (len(cmp_add) + len(patched_add)))
    s_a = (cmp_add or patched_add)[0]
    # the compared value, then cbz to the no-repeat branch
    ld, reg = _load_after(img, s_a, e)
    cbz = None
    for pc in range(ld + 4, min(e, ld + 40), 4):
        w = _w(img, pc)
        if w >> 24 == 0x34 and w & 31 == reg:                # cbz wReg
            imm = (w >> 5) & 0x7FFFF
            cbz = (pc, pc + (imm - (1 << 19) if imm & 0x40000 else imm) * 4)
            break
    if cbz is None:
        raise ValueError('check(): no cbz on the compared value')
    t = cbz[1]
    # the no-repeat branch's two address picks: its b.ne goes to the held
    # pick `mov w0, wB` (patched to `add w0, wB, #0xC`); the fall-through is
    # `add w0, wB, #0xC; b <held pick + 4>` (pressed). Followed by branch,
    # not by address order: the repeat branch's own picks sit in between.
    s_b = None
    for pc in range(t, min(e, t + 0x80), 4):
        w = _w(img, pc)
        if w >> 24 == 0x54 and w & 0xF == 1:                 # b.ne
            imm = (w >> 5) & 0x7FFFF
            x = pc + (imm - (1 << 19) if imm & 0x40000 else imm) * 4
            k = pc + 4
            while k < pc + 24 and _w(img, k) != _add_imm(0, r, 0xC):
                k += 4
            jb = _w(img, k + 4)
            if jb >> 26 == 0b000101:
                ji = jb & 0x3FFFFFF
                jt = k + 4 + (ji - (1 << 26) if ji & 0x2000000 else ji) * 4
            else:
                jt = None
            if _w(img, x) in (_mov_reg(0, r), _add_imm(0, r, 0xC)) \
                    and jt == x + 4 \
                    and _is_bl_to(_w(img, x + 4), x + 4, TRANSLATE):
                s_b = x
            break
    if s_b is None:
        raise ValueError('check(): no-repeat branch shape')
    out = {'difix': [(s_a, _add_imm(0, r, 0x10), _add_imm(0, r, 0x160)),
                     (s_b, _mov_reg(0, r), _add_imm(0, r, 0xC))]}
    a2, e2 = module.extent(X86_TICK)
    r2 = _base_reg(img, a2, e2, TICK_BASE)
    for kind, off in (('delay', TICK_BASE - 0x9A85C8),
                      ('rate', TICK_BASE - 0x9A85E4)):
        subs = [pc for pc in range(a2, e2, 4)
                if _w(img, pc) == _sub_imm(0, r2, off)]
        if len(subs) != 1:
            raise ValueError('tick(): %d candidates for the %s load'
                             % (len(subs), kind))
        q = subs[0] + 4
        if not _is_bl_to(_w(img, q), q, TRANSLATE):
            raise ValueError('tick(): %s load shape' % kind)
        ld = q + 4
        w = _w(img, ld)
        rt = w & 31
        if not (w == _ldr0(rt) or w & 0xFF800000 == 0x52800000
                or w & 0xFFFFFFE0 == 0x12800000):
            raise ValueError('tick(): %s word %08X' % (kind, w))
        out[kind] = (ld, rt)
    return out


def build_patches(module, cur):
    """{va: (expect stock word, new word)} for `cur`."""
    s = sites(module)
    words = {}
    if cur.get('difix'):
        for va, stock, new in s['difix']:
            words[va] = (stock, new)
    for kind in ('delay', 'rate'):
        if cur.get(kind) is None:
            continue
        va, rt = s[kind]
        words[va] = (_ldr0(rt), _mov_imm(rt, cur[kind]))
    return words


def apply_to_nso(src, dest, cur, log=lambda *_: None):
    """Write `dest` from `src`. False when nothing was written."""
    from pathlib import Path as _P
    import nxmap
    import nso_patcher
    module = nxmap.Main(str(src))
    try:
        words = build_patches(module, cur)
    except Exception as exc:                                  # noqa: BLE001
        log('! double-input fix: %s -- nothing was written' % exc)
        return False
    if not words:
        return False
    todo = {}
    for va, (stock, new) in sorted(words.items()):
        have = _w(module.img, va)
        if have == new:
            continue
        if have != stock:
            log('! double-input fix: +0x%X is %08X, neither stock %08X nor '
                '%08X -- nothing was written' % (va, have, stock, new))
            return False
        todo[va] = (stock, new)
    if not todo:
        log('  double-input fix: already in place')
        return False

    def hx(v):
        return ' '.join('%02X' % b for b in struct.pack('<I', v))
    spec = {'name': 'difix: Double Input Bugfix (ARM64 translation)',
            'patches': [{'name': '+0x%X' % va, 'va': hex(va),
                         'expect': hx(st), 'set': hx(nw)}
                        for va, (st, nw) in sorted(todo.items())]}
    nso = nso_patcher.read_nso(_P(str(src)))
    nso_patcher.apply_spec(nso, spec)
    data = nso_patcher.rebuild(nso)
    os.makedirs(os.path.dirname(os.path.abspath(str(dest))), exist_ok=True)
    with open(str(dest), 'wb') as f:
        f.write(data)
    return True


def describe(cur):
    def ms(v):
        return 'Infinity' if v == INF else '%d' % v
    bits = []
    if cur.get('difix'):
        bits.append('double-input fix ON')
    if cur.get('rate') is not None:
        bits.append('repeat rate %s' % ms(cur['rate']))
    if cur.get('delay') is not None:
        bits.append('repeat delay %s' % ms(cur['delay']))
    return ', '.join(bits) or 'nothing selected (stock input)'
