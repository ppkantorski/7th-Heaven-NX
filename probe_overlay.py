#!/usr/bin/env python3
"""
probe_overlay.py -- locate FFNx's widescreen overlay redirects in this port.

STEP 1 of issue 1.  Read-only.  Nothing is patched here.

FFNx's `patch_code_dword(fn + off, &wide_viewport_*)` overwrites the 4-byte
POINTER OPERAND of an x86 instruction that loads one of FF7's own battle
viewport globals.  So for every site we need three things, and each is
measured, never assumed:

  a) the x86 instruction that CONTAINS byte (fn + off) as its disp32 --
     which tells us the guest global being read and the operand width;
  b) the ARM64 body of `fn`, from the recompilation map;
  c) the ARM64 instruction inside that body which performs the same load.

For (c) the recompiled idiom is

    movz wN, #lo ; movk wN, #hi, lsl 16     the guest address
    mov  w0, wN  ; bl  <guest->host>        translate
    ldr* wM, [x0]                           <-- the site

so the search key is the guest address materialised as an immediate pair,
followed by a load.  We report every candidate, not just the first.
"""
import argparse
import collections
import struct
import sys

from capstone import (Cs, CS_ARCH_X86, CS_MODE_32, CS_ARCH_ARM64, CS_MODE_ARM,
                      CS_OP_MEM, CS_OP_IMM, CS_OP_REG)

import nxmap

IMAGE_BASE = 0x400000

# ---------------------------------------------------------------------------
# FFNx src/ff7/widescreen.cpp, ff7_widescreen_hook_init().  Transcribed from
# the tree in repos/FFNx-master, not from memory.  `kind` is the FFNx global
# the operand is redirected to; `op` is which patch_code_* was used.
#
# battle_enter's four are DELIBERATELY ABSENT -- FINDINGS-97 shipped those as
# a device-rect leg instead, and copying FFNx there is measurably wrong.
SPEC = [
    # ---- swirl -----------------------------------------------------------
    ('swirl_loop_sub_4026D4',   0x4026D4, 0x335, 'dword', 'wide_viewport_x'),
    ('swirl_enter_sub_401810',  0x401810, 0x021, 'dword', 'wide_viewport_width'),
    ('swirl_enter_40164E',      0x40164E, 0x0E8, 'int',   '85'),
    ('swirl_enter_40164E',      0x40164E, 0x0EE, 'dword', 'swirl_off_x'),
    ('swirl_enter_40164E',      0x40164E, 0x0FB, 'dword', 'swirl_off_y'),
    ('swirl_enter_40164E',      0x40164E, 0x112, 'dword', 'swirl_off_x'),
    ('swirl_enter_40164E',      0x40164E, 0x11F, 'dword', 'swirl_off_y'),
    # ---- battle overlay quads --------------------------------------------
    ('battle_sub_5BD050',       0x5BD050, 0x04B, 'dword', 'wide_viewport_width'),
    ('battle_sub_5BD050',       0x5BD050, 0x068, 'dword', 'wide_viewport_x'),
    ('battle_sub_5BD050',       0x5BD050, 0x08B, 'dword', 'wide_viewport_width'),
    ('battle_sub_5BD050',       0x5BD050, 0x0B4, 'dword', 'wide_viewport_x'),
    ('battle_sub_5BD050',       0x5BD050, 0x105, 'dword', 'wide_viewport_width'),
    ('battle_sub_5BD050',       0x5BD050, 0x122, 'dword', 'wide_viewport_x'),
    ('battle_sub_5BD050',       0x5BD050, 0x141, 'dword', 'wide_viewport_width'),
    ('battle_sub_5BD050',       0x5BD050, 0x16A, 'dword', 'wide_viewport_x'),
    ('battle_sub_5BD050',       0x5BD050, 0x19F, 'dword', 'wide_viewport_width'),
    ('battle_sub_5BD050',       0x5BD050, 0x1BB, 'dword', 'wide_viewport_x'),
    ('battle_draw_quad_5BD473', 0x5BD473, 0x0DA, 'dword', 'wide_viewport_x'),
    ('battle_draw_quad_5BD473', 0x5BD473, 0x112, 'dword', 'wide_viewport_x'),
    ('battle_sub_58ACB9',       0x58ACB9, 0x055, 'dword', 'wide_viewport_x'),
    ('battle_sub_58ACB9',       0x58ACB9, 0x065, 'dword', 'wide_viewport_x'),
    ('display_battle_damage_5BB410', 0x5BB410, 0x23F, 'dword', 'wide_viewport_x'),
    ('display_battle_damage_5BB410', 0x5BB410, 0x24C, 'dword', 'wide_viewport_x'),
]

# FFNx src/ff7/widescreen.h and widescreen.cpp file scope.
FFNX_VALUES = {
    'wide_viewport_x':      -107,
    'wide_viewport_y':        0,
    'wide_viewport_width':  854,
    'wide_viewport_height': 480,
    'swirl_off_x':          106,
    'swirl_off_y':           64,
}


# --------------------------------------------------------------- the x86 side

class Exe:
    def __init__(self, path):
        with open(path, 'rb') as f:
            self.data = f.read()
        d = self.data
        pe = struct.unpack('<I', d[0x3C:0x40])[0]
        nsec = struct.unpack('<H', d[pe + 6:pe + 8])[0]
        optsz = struct.unpack('<H', d[pe + 20:pe + 22])[0]
        off = pe + 24 + optsz
        self.sections = []
        for i in range(nsec):
            s = d[off + 40 * i: off + 40 * (i + 1)]
            name = s[:8].rstrip(b'\0').decode('ascii', 'replace')
            vsize, va, rsize, raw = struct.unpack('<IIII', s[8:24])
            self.sections.append((name, va + IMAGE_BASE, raw, rsize, vsize))

    def read(self, va, n):
        for name, base, raw, rsize, vsize in self.sections:
            if base <= va < base + max(rsize, vsize):
                o = raw + (va - base)
                return self.data[o:o + n]
        raise KeyError('va 0x%X in no section' % va)


def x86_site(exe, md32, fn, off, want_len):
    """
    The instruction whose IMMEDIATE-OR-DISPLACEMENT field covers fn+off.

    Decoded forward from the function entry so instruction boundaries are the
    real ones, not a guess from a byte scan.
    """
    target = fn + off
    va = fn
    blob = exe.read(fn, 0x1000)
    for ins in md32.disasm(blob, fn):
        if ins.address <= target < ins.address + ins.size:
            body = exe.read(ins.address, ins.size)
            k = target - ins.address
            return {
                'ins': '%s %s' % (ins.mnemonic, ins.op_str),
                'addr': ins.address,
                'size': ins.size,
                'field_at': k,
                'bytes': body.hex(),
                'field': struct.unpack('<i', body[k:k + 4])[0]
                         if k + 4 <= ins.size else None,
                'tail': k + want_len == ins.size,
            }
        if ins.address > target:
            break
    return None


# --------------------------------------------------------------- the ARM side

def imm_pairs(md64, body, base):
    """
    {constant -> [(addr_of_movz, reg)]} for every movz/movk-lsl-16 pair, and
    for a bare movz that is never completed (the high half is zero).
    """
    out = collections.defaultdict(list)
    ins = list(md64.disasm(body, base))
    reg_val = {}
    reg_at = {}
    for i in ins:
        m, ops = i.mnemonic, i.operands
        if m == 'movz' and len(ops) == 2 and ops[1].type == CS_OP_IMM:
            r = i.reg_name(ops[0].reg)
            sh = ops[1].shift.value if ops[1].shift.type else 0
            reg_val[r] = (ops[1].imm << sh) & 0xFFFFFFFF
            reg_at[r] = i.address
            out[reg_val[r]].append((i.address, r, 'movz'))
        elif m == 'movk' and len(ops) == 2 and ops[1].type == CS_OP_IMM:
            r = i.reg_name(ops[0].reg)
            if r in reg_val:
                sh = ops[1].shift.value if ops[1].shift.type else 0
                reg_val[r] = (reg_val[r] | (ops[1].imm << sh)) & 0xFFFFFFFF
                out[reg_val[r]].append((reg_at[r], r, 'movz+movk'))
        elif m in ('mov', 'movn', 'adrp', 'add', 'sub', 'ldr', 'ldrh', 'ldrb',
                   'ldrsh', 'ldrsb', 'bl'):
            # anything that could redefine a register we are tracking
            if ops and ops[0].type == CS_OP_REG:
                r = i.reg_name(ops[0].reg)
                if m != 'mov':
                    reg_val.pop(r, None)
    return out, ins


def arm_loads_after(ins, start_addr, window=24):
    """The loads that follow `start_addr` within `window` instructions."""
    out = []
    idx = next((k for k, i in enumerate(ins) if i.address == start_addr), None)
    if idx is None:
        return out
    for i in ins[idx:idx + window]:
        if i.mnemonic in ('ldr', 'ldrh', 'ldrb', 'ldrsh', 'ldrsb', 'ldrsw'):
            out.append(i)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', default='ff7_en_switch')
    ap.add_argument('--main', default='exefs/main')
    ap.add_argument('--only', default=None)
    args = ap.parse_args()

    exe = Exe(args.exe)
    m = nxmap.Main(args.main)
    md32 = Cs(CS_ARCH_X86, CS_MODE_32)
    md32.detail = True
    md64 = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md64.detail = True

    print('recompilation map: %d records' % len(m.x86_to_arm))
    print()

    # ---- step A: are the seven bases real function entries? --------------
    print('=== A. FFNx symbol -> ARM body ===')
    bodies = {}
    for name, fn in sorted({(s[0], s[1]) for s in SPEC}, key=lambda t: t[1]):
        entry = fn in m.x86_to_arm
        if entry:
            a, b = m.extent(fn)
            bodies[fn] = (a, b)
            print('  %-32s x86 0x%06X  ->  ARM +0x%07X..+0x%07X  (%d words)'
                  % (name, fn, a, b, (b - a) // 4))
        else:
            near = m.containing(fn)
            print('  %-32s x86 0x%06X  ->  NOT A MAP KEY  (inside 0x%06X)'
                  % (name, fn, near[0] if near else 0))
    print()

    # ---- step B: the x86 instruction at each site ------------------------
    print('=== B. the x86 instruction each patch_code_* lands in ===')
    sites = []
    for name, fn, off, op, kind in SPEC:
        if args.only and args.only not in name:
            continue
        w = 4 if op in ('dword', 'int') else 2
        s = x86_site(exe, md32, fn, off, w)
        if s is None:
            print('  %-28s +0x%03X  UNDECODED' % (name, off))
            continue
        s.update(name=name, fn=fn, off=off, op=op, kind=kind)
        sites.append(s)
        print('  %-28s +0x%03X  %-38s field@%d = 0x%X %s'
              % (name, off, s['ins'], s['field_at'],
                 s['field'] & 0xFFFFFFFF if s['field'] is not None else 0,
                 '' if s['tail'] else '(NOT the tail field!)'))
    print()

    # ---- step C: what global does each read ------------------------------
    print('=== C. guest globals referenced ===')
    per_global = collections.Counter()
    for s in sites:
        if s['op'] == 'int':
            print('  %-28s +0x%03X  IMMEDIATE %d -> %s'
                  % (s['name'], s['off'], s['field'], s['kind']))
            continue
        g = s['field'] & 0xFFFFFFFF
        per_global[g] += 1
        print('  %-28s +0x%03X  reads [0x%X]  -> %s'
              % (s['name'], s['off'], g, s['kind']))
    print()
    print('  distinct guest globals: %s'
          % ', '.join('0x%X x%d' % (g, n) for g, n in per_global.most_common()))
    print()

    # ---- step D: find them in the ARM bodies -----------------------------
    #
    # The comparison that matters is not "did we find something" but "does the
    # ARM body contain exactly the accesses the x86 body contains, in order,
    # at the same widths".  Anything else means the walk lost a site or
    # invented one, and either way the patch cannot be written.
    print('=== D. the ARM64 sites, matched against the x86 ===')
    import ff7nx_guestref as gr

    total_ok = total_bad = 0
    for fn, (a, b) in sorted(bodies.items(), key=lambda t: t[1]):
        nm = next(s[0] for s in SPEC if s[1] == fn)
        acc, stats = gr.scan(m.text, a, b, md64)
        want = [s for s in sites if s['fn'] == fn and s['op'] != 'int']
        wanted_g = {s['field'] & 0xFFFFFFFF for s in want}
        got = [x for x in acc if x.guest in wanted_g and x.is_load]

        print('  --- %s   ARM +0x%X..+0x%X   (%d blocks, %d translate, '
              '%d ld, %d st, %d unreached) ---'
              % (nm, a, b, stats['blocks'], stats['translate'], stats['ld'],
                 stats['st'], stats['unreached']))

        # x86 order vs ARM order, per guest address
        for g in sorted(wanted_g):
            xs = [s for s in want if (s['field'] & 0xFFFFFFFF) == g]
            ys = [x for x in got if x.guest == g]
            flag = 'OK ' if len(xs) == len(ys) else '*** MISMATCH ***'
            print('    %s guest 0x%X   x86 %d   ARM %d' % (flag, g, len(xs), len(ys)))
            if len(xs) == len(ys):
                total_ok += len(xs)
            else:
                total_bad += 1
            for k in range(max(len(xs), len(ys))):
                xd = ('+0x%03X %-34s' % (xs[k]['off'], xs[k]['ins'])
                      if k < len(xs) else '%-41s' % '(none)')
                yd = ('+0x%07X %s %s' % (ys[k].addr, ys[k].mnemonic, ys[k].op_str)
                      if k < len(ys) else '(none)')
                xw = (4 if 'dword ptr' in xs[k]['ins'] else 2) if k < len(xs) else 0
                yw = ys[k].width if k < len(ys) else 0
                wflag = '' if xw == yw else '   <-- WIDTH %d vs %d' % (xw, yw)
                print('        %s  ->  %s%s' % (xd, yd, wflag))
        # everything else the body touches on those bases, for context
        others = sorted({x.guest for x in acc} - wanted_g)
        near = [g for g in others if any(abs(g - w) <= 0x20 for w in wanted_g)]
        if near:
            print('    neighbours also touched: %s'
                  % ', '.join('0x%X' % g for g in near))
        print()
    print('  %d site(s) matched, %d guest address(es) mismatched'
          % (total_ok, total_bad))


if __name__ == '__main__':
    main()
