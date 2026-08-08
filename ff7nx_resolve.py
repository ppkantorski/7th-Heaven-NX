#!/usr/bin/env python3
"""
ff7nx_resolve.py -- resolve FFNx 60 FPS constant patches to `main` NSO offsets.

This is the tool HANDOFF 5e asks for. It replaces manual per-constant passes.

Pipeline
--------
  1. Parse FFNx's ff7_data.h derivation chain and evaluate it against the real
     ff7_en PE, reproducing every `ff7_externals.*` address the way FFNx does
     at runtime (get_absolute_value / get_relative_call).
  2. Parse patch specs (`patch_multiply_code<T>`, `patch_divide_code<T>`,
     `patch_code_byte`, ...) out of FFNx's .cpp sources.
  3. For each spec, read the STOCK operand at that x86 address from ff7_en.
     That value is the search key.
  4. Map the containing x86 function through the recompilation map at module
     offset 0x126D3A8 to its ARM64 body in `main`.
  5. Scan the ARM64 body for instructions carrying that immediate.
  6. Emit UNAMBIGUOUS matches only. Everything else goes to a review queue
     with full context. Nothing is ever guessed.

Validation gates (a failure is reported, never silently patched around):
  - every derived address is cross-checked against the hex suffix FFNx
    encoded into the symbol name, where present;
  - the derived function base must be a key in the recompilation map, i.e. a
    real x86 function entry point;
  - base+offset must lie inside that same function (before the next key);
  - the ARM64 candidate's immediate must equal the stock x86 operand exactly,
    and its instruction class must be consistent with the x86 instruction.

Usage
-----
    python3 ff7nx_resolve.py --exe ff7_en --nso main [--json out.json]
                             [--group battle_death] [--mult 4] [--show-queue]
"""
import argparse, json, re, struct, sys, os

try:
    import lz4.block
except ImportError:
    sys.exit('need lz4:  pip install lz4 --break-system-packages')
try:
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32, CS_ARCH_ARM64, CS_MODE_ARM
except ImportError:
    sys.exit('need capstone:  pip install capstone --break-system-packages')

IMAGE_BASE = 0x400000
RECOMP_MAP = 0x126D3A8          # module offset of the x86->ARM64 map
X86_TEXT = (0x401000, 0x7B562B)  # ff7_en .text, = map key span

TYPE_W = {'byte': 1, 'char': 1, 'unsigned char': 1,
          'WORD': 2, 'short': 2, 'uint16_t': 2, 'int16_t': 2,
          'DWORD': 4, 'int': 4, 'uint32_t': 4, 'float': 4,
          'double': 8}
TYPE_SIGNED = {'char', 'short', 'int', 'int16_t'}


# ---------------------------------------------------------------- ff7_en (PE)

class Exe:
    """ff7_en as a VA-addressable image."""

    def __init__(self, data):
        self.data = data
        pe = struct.unpack('<I', data[0x3C:0x40])[0]
        nsec = struct.unpack('<H', data[pe + 6:pe + 8])[0]
        optsz = struct.unpack('<H', data[pe + 20:pe + 22])[0]
        off = pe + 24 + optsz
        self.sections = []
        for i in range(nsec):
            s = data[off + 40 * i: off + 40 * (i + 1)]
            name = s[:8].rstrip(b'\0').decode('ascii', 'replace')
            vsize, va, rsize, raw = struct.unpack('<IIII', s[8:24])
            self.sections.append((name, va + IMAGE_BASE, raw, rsize, vsize))

    def sect(self, va):
        for name, base, raw, rsize, vsize in self.sections:
            if base <= va < base + max(rsize, vsize):
                return name, base, raw, rsize
        return None

    def off(self, va):
        s = self.sect(va)
        if s is None:
            raise ValueError('VA 0x%X is in no section' % va)
        name, base, raw, rsize = s
        if va - base >= rsize:
            raise ValueError('VA 0x%X is past raw data of %s' % (va, name))
        return raw + (va - base)

    def read(self, va, n):
        o = self.off(va)
        return self.data[o:o + n]

    def u32(self, va):
        return struct.unpack('<I', self.read(va, 4))[0]

    def u16(self, va):
        return struct.unpack('<H', self.read(va, 2))[0]

    def scalar(self, va, width, signed):
        b = self.read(va, width)
        if width == 4 and signed is float:
            return struct.unpack('<f', b)[0]
        if width == 8 and signed is float:
            return struct.unpack('<d', b)[0]
        return int.from_bytes(b, 'little', signed=bool(signed))

    # ---- FFNx primitives, byte-for-byte semantics ------------------------
    def get_absolute_value(self, base, off):
        return self.u32(base + off)

    def get_relative_call(self, base, off):
        insn = self.u16(base + off)
        if (insn & 0xFF) not in (0xE8, 0xE9) and insn != 0x15FF:
            raise ValueError('not a call/jmp at 0x%X+0x%X: %04X'
                             % (base, off, insn))
        size = 2 if insn == 0x15FF else 1
        disp = struct.unpack('<i', self.read(base + off + size, 4))[0]
        return (base + disp + off + 4 + size) & 0xFFFFFFFF


# ---------------------------------------------------------------- main (NSO)

class Nso:
    """`main` as a module-offset-addressable image plus the recompilation map."""

    def __init__(self, data):
        if data[:4] != b'NSO0':
            raise SystemExit('not an NSO')
        segs = [struct.unpack('<III', data[b:b + 12]) for b in (0x10, 0x20, 0x30)]
        comp = struct.unpack('<III', data[0x60:0x6C])
        flags = struct.unpack('<I', data[0x0C:0x10])[0]
        raw = []
        for i, (fo, mo, ds) in enumerate(segs):
            blob = data[fo:fo + comp[i]]
            raw.append(lz4.block.decompress(blob, uncompressed_size=ds)
                       if flags & (1 << i) else blob[:ds])
        self.segs, self.raw = segs, raw
        self.text = raw[0]
        end = segs[2][1] + segs[2][2]
        img = bytearray(end)
        for i, (fo, mo, ds) in enumerate(segs):
            img[mo:mo + ds] = raw[i]
        self.img = bytes(img)
        self._relocs()
        self._map()

    def _relocs(self):
        img = self.img
        mod0 = struct.unpack('<I', img[4:8])[0]
        dyn = mod0 + struct.unpack('<i', img[mod0 + 4:mod0 + 8])[0]
        want = {7: 'RELA', 8: 'RELASZ', 9: 'RELAENT'}
        v = {}
        p = dyn
        while True:
            tag, val = struct.unpack('<qQ', img[p:p + 16])
            if tag == 0:
                break
            if tag in want:
                v[want[tag]] = val
            p += 16
        self.rel = {}
        n = v['RELASZ'] // v['RELAENT']
        for i in range(n):
            o, info, add = struct.unpack(
                '<QQq', img[v['RELA'] + i * v['RELAENT']:
                            v['RELA'] + (i + 1) * v['RELAENT']])
            if (info & 0xFFFFFFFF) == 1027:      # R_AARCH64_RELATIVE
                self.rel[o] = add

    def _map(self):
        """x86 function entry -> ARM64 body, from the 16-byte record table."""
        self.x86_to_arm = {}
        p = RECOMP_MAP
        while True:
            va = struct.unpack('<I', self.img[p:p + 4])[0]
            if not (X86_TEXT[0] <= va <= X86_TEXT[1]):
                break
            ptr = self.rel.get(p + 8)
            if ptr is None:
                raise SystemExit('map record at %#x has no reloc' % p)
            self.x86_to_arm[va] = ptr
            p += 16
        self.x86_keys = sorted(self.x86_to_arm)
        self.arm_starts = sorted(self.x86_to_arm.values())

    def containing(self, va):
        """(func_x86_start, func_x86_end) for an address inside .text."""
        import bisect
        i = bisect.bisect_right(self.x86_keys, va) - 1
        if i < 0:
            return None
        start = self.x86_keys[i]
        end = self.x86_keys[i + 1] if i + 1 < len(self.x86_keys) else X86_TEXT[1]
        return start, end

    def body(self, x86_start):
        """(arm64_start, arm64_end) of a translated function body."""
        import bisect
        a = self.x86_to_arm[x86_start]
        j = bisect.bisect_right(self.arm_starts, a)
        end = self.arm_starts[j] if j < len(self.arm_starts) else len(self.text)
        return a, end


# ------------------------------------------------- ff7_data.h interpreter

RE_SUFFIX = re.compile(r'_((?:sub_)?)([0-9A-F]{6})$')

# A parenthesised group that is a C cast rather than a subexpression:
#   (uint32_t)  (DWORD*)  (armor_data *)  (void (*)(void*, void*))
RE_TYPEISH = re.compile(
    r'^\s*(?:struct\s+|unsigned\s+|const\s+)*[A-Za-z_]\w*'
    r'(?:\s*::\s*\w+)*'
    r'(?:\s*\*)*'
    r'(?:\s*\(\s*\*\s*\)\s*\([^()]*\))?'
    r'(?:\s*\*)*\s*$')
RE_ASSIGN = re.compile(
    r'^\s*(?:(ff7_externals|common_externals)\s*\.\s*)?'
    r'([A-Za-z_]\w*)\s*(?:\[\s*\w+\s*\])?\s*=\s*(.+)$', re.S)


def strip_casts(s):
    """Remove C cast expressions, leaving calls and arithmetic intact."""
    changed = True
    while changed:
        changed = False
        depth = 0
        stack = []
        for i, ch in enumerate(s):
            if ch == '(':
                stack.append(i)
            elif ch == ')':
                if not stack:
                    break
                j = stack.pop()
                inner = s[j + 1:i]
                # a call's '(' is preceded by an identifier character
                prev = s[j - 1] if j else ''
                if prev.isalnum() or prev == '_':
                    continue
                if '(' in inner and ')' in inner and not RE_TYPEISH.match(inner):
                    continue
                if not RE_TYPEISH.match(inner):
                    continue
                nxt = s[i + 1:i + 2]
                if nxt and (nxt.isalnum() or nxt in '_&('):
                    s = s[:j] + ' ' * (i - j + 1) + s[i + 1:]
                    changed = True
                    break
    return s


class Externals:
    """
    Evaluates FFNx's ff7_data.h / common.cpp derivation chain against the real
    ff7_en image, reproducing exactly what FFNx computes at runtime.

    Roots that FFNx reads out of a live game_object are resolved statically
    from the exe instead, and each is verified before use.
    """

    def __init__(self, exe, sources):
        self.exe = exe
        self.sym = {}
        self.warnings = []
        self.suffix_ok = 0
        self.suffix_bad = []
        self.roots = self._seed()
        for _ in range(4):                    # iterate to a fixpoint
            before = len(self.sym)
            for text in sources:
                self._run(text)
            if len(self.sym) == before:
                break

    # ---- roots ----------------------------------------------------------
    def _seed(self):
        """
        game_object->engine_loop_obj is populated by the initialiser at
        0x40A3C1..0x40A3FF:
            [obj+0x9F0] = init        [obj+0x9FC] = exit_main
            [obj+0x9F4] = cleanup     [obj+0xA00] = main_loop
            [obj+0x9F8] = enter_main
        struct main_obj puts main_loop at +0x10, so the struct base is +0x9F0.
        We read the four immediates straight out of that code, then check the
        main_loop prologue the way ff7_data.h does.
        """
        exe = self.exe
        fields = {}
        for va, name in ((0x40A3C1, 'init'), (0x40A3DB, 'cleanup'),
                         (0x40A3E8, 'enter_main'), (0x40A3F5, 'exit_main'),
                         (0x40A3CE, 'main_loop')):
            # mov dword [reg+disp32], imm32  ->  C7 80 <disp32> <imm32>
            b = exe.read(va, 10)
            if b[0] != 0xC7:
                raise SystemExit('engine_loop_obj initialiser moved: '
                                 '0x%X starts %02X' % (va, b[0]))
            fields[name] = struct.unpack('<I', b[6:10])[0]
        if exe.u32(fields['main_loop']) != 0x81EC8B55:
            raise SystemExit('odd main loop prologue at 0x%X -- wrong exe'
                             % fields['main_loop'])
        pe = struct.unpack('<I', exe.data[0x3C:0x40])[0]
        start = struct.unpack('<I', exe.data[pe + 40:pe + 44])[0] + IMAGE_BASE
        self.sym['main_loop'] = fields['main_loop']
        self.sym['main_init_loop'] = fields['init']
        self.sym['main_cleanup_loop'] = fields['cleanup']
        self.sym['common_externals.start'] = start
        self.sym['start'] = start
        try:
            wm = exe.get_relative_call(start, 0x14D)
            self.sym['common_externals.winmain'] = wm
            self.sym['winmain'] = wm
        except Exception as e:
            self.warnings.append('winmain: %s' % e)
        return dict(main_loop=fields['main_loop'], main_init_loop=fields['init'],
                    main_cleanup_loop=fields['cleanup'], start=start,
                    winmain=self.sym.get('winmain'))

    # ---- symbol table ---------------------------------------------------
    def _put(self, scope, name, val):
        if not isinstance(val, int) or not (0 <= val <= 0xFFFFFFFF):
            return
        keys = [name] if not scope else ['%s.%s' % (scope, name), name]
        fresh = keys[0] not in self.sym
        for k in keys:
            if k not in self.sym:
                self.sym[k] = val
        if not fresh:
            return
        m = RE_SUFFIX.search(name)
        if m:
            want = int(m.group(2), 16)
            if val == want:
                self.suffix_ok += 1
            else:
                self.suffix_bad.append((name, val, want))

    def _lookup(self, name):
        return self.sym.get(name)

    # ---- evaluation -----------------------------------------------------
    def _eval(self, expr):
        """Evaluate a C address expression. Raises on anything unmodelled."""
        e = strip_casts(expr)
        e = e.replace('get_absolute_value', 'GA').replace('get_relative_call', 'GR')
        e = re.sub(r'\b(?:ff7_externals|common_externals)\s*\.\s*(\w+)',
                   lambda m: 'S("%s.%s")' % ('X', m.group(1)), e)
        e = re.sub(r'&\s*', '', e)
        # bare identifiers -> symbol lookups (leave GA/GR/S alone)
        def sub_ident(m):
            n = m.group(0)
            if n in ('GA', 'GR', 'S'):
                return n
            return 'S("%s")' % n
        e = re.sub(r'(?<![\w."])[A-Za-z_]\w*', sub_ident, e)
        # table[i] -> IDX(table, i). Every indexed external in this codebase is
        # a table of u32 (execute_opcode_table, and the opcode_* entries FFNx
        # reads out of it), so the stride is 4.
        for _ in range(4):
            new = re.sub(r'(S\("[^"]+"\)|IDX\([^()]*\))\s*\[\s*([^\[\]]+?)\s*\]',
                         r'IDX(\1, \2)', e)
            if new == e:
                break
            e = new
        if not re.fullmatch(r'[\w\s\(\)\+\-\*/",\.]+', e):
            raise ValueError('unmodelled expression: %s' % expr.strip())
        env = {'GA': self.exe.get_absolute_value,
               'GR': self.exe.get_relative_call,
               'IDX': lambda base, i: self.exe.u32(base + 4 * i),
               'S': self._sym_or_raise}
        return eval(e, {'__builtins__': {}}, env)

    def _sym_or_raise(self, name):
        if name.startswith('X.'):
            bare = name[2:]
            for k in ('ff7_externals.%s' % bare, 'common_externals.%s' % bare, bare):
                if k in self.sym:
                    return self.sym[k]
            raise KeyError(bare)
        if name in self.sym:
            return self.sym[name]
        raise KeyError(name)

    def _run(self, text):
        text = re.sub(r'//[^\n]*', '', text)
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
        for stmt in text.split(';'):
            if '=' not in stmt or 'get_absolute_value' not in stmt \
                    and 'get_relative_call' not in stmt and '+' not in stmt:
                continue
            if '==' in stmt or '!=' in stmt or '<=' in stmt or '>=' in stmt:
                continue
            m = RE_ASSIGN.match(stmt.strip())
            if not m:
                continue
            scope, name, rhs = m.groups()
            if name in ('i', 'j', 'ret'):
                continue
            key = '%s.%s' % (scope, name) if scope else name
            if key in self.sym:
                continue
            try:
                val = self._eval(rhs)
            except Exception:
                continue
            self._put(scope, name, val)

    # ---- public ---------------------------------------------------------
    def get(self, qualified):
        bare = qualified.split('.')[-1]
        for k in (qualified, bare):
            if k in self.sym:
                return self.sym[k]
        return None

    def get_or_suffix(self, qualified):
        v = self.get(qualified)
        if v is not None:
            return v, 'derived'
        m = RE_SUFFIX.search(qualified.split('.')[-1])
        if m:
            return int(m.group(2), 16), 'name-suffix'
        return None, None


# ------------------------------------------------------ FFNx spec scraping

RE_SPEC = re.compile(
    r'patch_(?:(multiply|divide)_code\s*<\s*([\w\s]+?)\s*>|code_(\w+))\s*\(\s*'
    r'(?:\(\s*uint32_t\s*\)\s*)?'
    r'(?:&\s*)?(ff7_externals\.\w+|common_externals\.\w+)\s*'
    r'(?:\+\s*(0x[0-9A-Fa-f]+|\d+)\s*)?,\s*([^;]+?)\s*\)\s*;')

GROUPS = {
    'ff7/battle/animations.cpp': 'battle_anim',
    'ff7/battle/camera.cpp': 'battle_camera',
    'ff7/field/field.cpp': 'field',
    'ff7/world/world.cpp': 'world',
    'ff7_opengl.cpp': 'core',            # swirl, limiters, FPS menu multiplier
}

# --------------------------------------------------------------------------
# NOT FRAMERATE PATCHES
#
# This scraper is a regex over five .cpp files. It has no idea what `if` a
# spec sits inside, and those files contain plenty of `patch_*_code` calls
# that have nothing to do with 60 FPS -- they are other FFNx features, gated
# at runtime on options we are not reproducing and, in several cases, paired
# with whole functions FFNx replaces. Applying one of them is not "a bit of
# extra FFNx", it is turning on an unrelated feature at half strength.
#
# The one that matters most: field_init_viewport_values +0x35/+0x6E is FFNx's
# "field vertical center" option (`if (ff7_field_center || widescreen_enabled)`
# in ff7_opengl.cpp). It moves the field viewport's Y origin from 0 to 16 and
# its height from 224 to 240 -- it re-frames every field screen in the game.
# The resolver had it filed under `p-battle_misc` purely because that is the
# group its ARM64 neighbours landed in, one `--enable p-battle_misc` away from
# a build that looks "zoomed in" with the player half off the bottom.
#
# Refused at scrape time so a regenerated ff7nx_patchgroups.py cannot contain
# them at all. ff7nx_60fps.py repeats the check at load time against the
# checked-in file.
NON_FPS_SYMBOLS = {
    # ff7_opengl.cpp, outside any fps conditional
    'field_init_viewport_values',                        # field vertical center
    'field_draw_everything',                             # paired with replaced
                                                         # field_layer*_pick_tiles
    'kernel_load_kernel2',                               # kernel2 buffer size
    'coaster_sub_5EE150',                                # coaster aim fix
    'world_sub_75C283',                                  # switch (version)
    'highway_exit_address_location',                     # highway exit bugfix
    # world.cpp, inside enable_worldmap_external_mesh (renderer replacement)
    'world_submit_draw_clouds_and_meteor_7547A6',
    'world_init_load_map_meshes_graphics_objects_75A283',
    'world_wm0_overworld_draw_all_74C179',
    'world_wm2_underwater_draw_all_74C3F0',
    'world_wm3_snowstorm_draw_all_74C589',
}


def scrape(src_root, log=None):
    out, refused = [], []
    for rel, group in GROUPS.items():
        path = os.path.join(src_root, rel)
        if not os.path.isfile(path):
            continue
        txt = open(path, encoding='utf-8', errors='replace').read()
        for m in RE_SPEC.finditer(txt):
            kind, tmpl, plain, sym, off, arg = m.groups()
            line = txt[:m.start()].count('\n') + 1
            bare = sym.split('.')[-1]
            if bare in NON_FPS_SYMBOLS:
                refused.append('%s:%d %s+0x%X (not a framerate patch)'
                               % (rel, line, bare, int(off, 0) if off else 0))
                continue
            if kind:
                op, ctype = kind, tmpl
            else:
                op, ctype = 'set', plain
            out.append(dict(group=group, file=rel, line=line, op=op,
                            ctype=ctype.strip(), sym=sym,
                            off=int(off, 0) if off else 0, arg=arg.strip()))
    if refused and log:
        log('refused %d spec(s) that are not framerate patches:' % len(refused))
        for r in refused:
            log('    ' + r)
    return out


def eval_arg(arg, mults):
    """
    Evaluate an FFNx literal argument like `0x2 - common_frame_multiplier / 2`
    or `common_frame_multiplier * 2`.

    Each multiplier variable gets its own value -- battle is natively 15 FPS
    (x4) while field and world are natively 30 FPS (x2) -- so a single global
    multiplier would be wrong for any file that mentions both. C++ integer
    division truncates, so `/` becomes `//`.
    """
    e = arg
    for name, val in mults.items():
        e = re.sub(r'\b%s\b' % name, str(val), e)
    if not re.fullmatch(r'[\dxXa-fA-F\s\+\-\*/\(\)]+', e):
        return None
    e = e.replace('/', '//')
    try:
        return eval(e, {'__builtins__': {}}, {})       # arithmetic only
    except Exception:
        return None


def spec_mult(arg, mults):
    """Which multiplier this spec is governed by, for reporting."""
    for name, val in mults.items():
        if re.search(r'\b%s\b' % name, arg):
            return name, val
    return 'literal', None


# ------------------------------------------------------- ARM64 immediates

def decode_bitmask(sf, n, immr, imms):
    """
    AArch64 logical (bitmask) immediate decoder -- ARM ARM `DecodeBitMasks`,
    immediate variant. Returns the value, or None if the encoding is reserved.

    An earlier hand-rolled version of this got the element size wrong and
    disagreed with capstone on 3,518 of 4,032 encodings, which made the
    and/orr/eor matching unreliable. This version is checked against capstone
    over every legal encoding by test_bitmask.py.
    """
    width = 64 if sf else 32
    if n and not sf:
        return None
    # len = HighestSetBit(N : NOT(imms))
    bits = (n << 6) | ((~imms) & 0x3F)
    ln = bits.bit_length() - 1
    if ln < 1:
        return None
    esize = 1 << ln
    if esize > width:
        return None
    levels = esize - 1
    s = imms & levels
    r = immr & levels
    if s == levels:                      # all-ones element is reserved
        return None
    welem = (1 << (s + 1)) - 1
    if r:                                # ROR within the element
        welem = ((welem >> r) | (welem << (esize - r))) & ((1 << esize) - 1)
    val = 0
    for i in range(0, width, esize):
        val |= welem << i
    return val & ((1 << width) - 1)


def arm64_immediates(word):
    """
    Every immediate this instruction word could be carrying, with a class tag.
    Only the encodings the recompiler actually emits for x86 literals.

    MOVK is deliberately excluded: it is always one half of a multi-instruction
    constant build, its imm16 is shifted, and rewriting it in isolation is
    wrong. Reporting its raw imm16 as a match produced a false positive
    (world_init_variables +0x15D matched `movk w9,#2,lsl#16` on the value 2).
    """
    out = []
    op = word
    # MOVZ / MOVN : sf opc 100101 hw imm16 Rd
    if (op >> 23) & 0x3F == 0x25:
        opc = (op >> 29) & 3
        hw = (op >> 21) & 3
        imm16 = (op >> 5) & 0xFFFF
        sf = (op >> 31) & 1
        if opc == 2:
            out.append(('movz', imm16 << (16 * hw)))
        elif opc == 0:
            out.append(('movn', (~(imm16 << (16 * hw)))
                        & (0xFFFFFFFFFFFFFFFF if sf else 0xFFFFFFFF)))
    # ADD/ADDS/SUB/SUBS immediate : sf op S 10001 0 sh imm12 Rn Rd
    if (op >> 24) & 0x1F == 0x11:
        sh = (op >> 22) & 1
        imm12 = (op >> 10) & 0xFFF
        val = imm12 << 12 if sh else imm12
        sub = (op >> 30) & 1
        s = (op >> 29) & 1
        tag = ('subs' if s else 'sub') if sub else ('adds' if s else 'add')
        out.append((tag, val))
    # AND/ORR/EOR/ANDS immediate : sf opc 100100 N immr imms Rn Rd
    if (op >> 23) & 0x3F == 0x24:
        sf = (op >> 31) & 1
        n = (op >> 22) & 1
        immr = (op >> 16) & 0x3F
        imms = (op >> 10) & 0x3F
        v = decode_bitmask(sf, n, immr, imms)
        if v is not None:
            opc = (op >> 29) & 3
            rn = (op >> 5) & 0x1F
            tag = ('and', 'orr', 'eor', 'ands')[opc]
            # ORR Rd, WZR, #imm is the MOV-immediate alias -- a pure constant
            # materialisation, and the only bitmask form safe to re-encode.
            out.append(('movi' if (tag == 'orr' and rn == 31) else tag, v))
    # SBFM/UBFM : sf opc 100110 N immr imms Rn Rd.
    # x86 shift counts land here as ASR/LSR/LSL aliases, not as ALU
    # immediates. The shipped `>>8 -> >>9` walk-speed patch and the ladder/jump
    # `/4 -> /2` patches are all this encoding.
    if (op >> 23) & 0x3F == 0x26:
        sf = (op >> 31) & 1
        opc = (op >> 29) & 3
        n = (op >> 22) & 1
        immr = (op >> 16) & 0x3F
        imms = (op >> 10) & 0x3F
        top = 63 if sf else 31
        if n == sf:
            if imms == top and opc == 0:
                out.append(('asr', immr))
            elif imms == top and opc == 2:
                out.append(('lsr', immr))
            elif opc == 2:
                shift = top - imms
                if 0 < shift <= top and immr == ((top + 1 - shift) % (top + 1)):
                    out.append(('lsl', shift))
    return out


def encode_bitmask(sf, value):
    """
    Inverse of decode_bitmask: (N, immr, imms) for `value`, or None if it is
    not a legal AArch64 logical immediate. Brute force over the 5,334 legal
    encodings -- small, exact, and it cannot produce a wrong answer.
    """
    width = 64 if sf else 32
    key = value & ((1 << width) - 1)
    for n in (0, 1):
        if n and not sf:
            continue
        for immr in range(64):
            for imms in range(64):
                if decode_bitmask(sf, n, immr, imms) == key:
                    return n, immr, imms
    return None


# x86 mnemonic -> ARM64 classes that can carry the same literal.
# NARROW is the direct ALU correspondence; BROAD also allows the recompiler to
# have materialised the constant into a register first. Narrow is tried first,
# so a `cmp` prefers the `subs` that encodes the comparison over an unrelated
# `movz` of the same number elsewhere in the body.
X86_NARROW = {
    'cmp':   {'subs', 'ands'},
    'mov':   {'movz', 'movi', 'movn'},
    'movzx': {'movz', 'movi', 'movn'},
    'movsx': {'movz', 'movi', 'movn'},
    'add':   {'add', 'adds'},
    'sub':   {'sub', 'subs'},
    'and':   {'and', 'ands'},
    'or':    {'orr'},
    'xor':   {'eor'},
    'test':  {'ands'},
    'push':  {'movz', 'movi', 'movn'},
    'imul':  {'movz', 'movi', 'movn'},
    'sar':   {'asr'},
    'shr':   {'lsr'},
    'shl':   {'lsl'},
    'sal':   {'lsl'},
}
X86_BROAD = {k: (v if v & {'asr', 'lsr', 'lsl'} else v | {'movz', 'movi', 'movn'})
             for k, v in X86_NARROW.items()}

# Tags whose immediate can be re-encoded in place without changing semantics.
REWRITABLE = {'movz', 'movi', 'movn', 'add', 'adds', 'sub', 'subs',
              'and', 'ands', 'orr', 'eor', 'asr', 'lsr', 'lsl'}


def rewrite(orig, tag, want):
    """
    New ARM64 word carrying `want` in the same slot, or None if it cannot be
    encoded without changing the instruction's semantics.

      movz / movi (ORR,WZR)  -> MOVZ,  new imm16          (or MOVN if negative)
      movn                   -> MOVN,  new inverted imm16 (or MOVZ if positive)
      add/adds/sub/subs      -> same instruction, new imm12
      and/ands/orr/eor       -> same instruction, re-encoded bitmask immediate

    MOVK is never rewritten: it is one half of a multi-word constant build.
    """
    rd = orig & 0x1F
    if tag in ('movz', 'movi', 'movn'):
        sf = (orig >> 31) & 1 if tag != 'movi' else (orig >> 31) & 1
        mask = 0xFFFFFFFFFFFFFFFF if sf else 0xFFFFFFFF
        v = want & mask
        if v <= 0xFFFF:                                  # MOVZ
            return (sf << 31) | 0x52800000 | (v << 5) | rd
        inv = (~want) & mask
        if inv <= 0xFFFF:                                # MOVN
            return (sf << 31) | 0x12800000 | (inv << 5) | rd
        return None
    if tag in ('add', 'adds', 'sub', 'subs'):
        if not (0 <= want <= 0xFFF):
            return None
        return (orig & ~(0xFFF << 10) & ~(1 << 22)) | (want << 10)
    if tag in ('and', 'ands', 'orr', 'eor'):
        sf = (orig >> 31) & 1
        enc = encode_bitmask(sf, want)
        if enc is None:
            return None
        n, immr, imms = enc
        return ((orig & ~((1 << 22) | (0x3F << 16) | (0x3F << 10)))
                | (n << 22) | (immr << 16) | (imms << 10))
    if tag in ('asr', 'lsr', 'lsl'):
        sf = (orig >> 31) & 1
        top = 63 if sf else 31
        if not (0 <= want <= top):
            return None
        if tag in ('asr', 'lsr'):
            immr, imms = want, top
        else:
            immr, imms = (top + 1 - want) % (top + 1), top - want
        return ((orig & ~((0x3F << 16) | (0x3F << 10)))
                | (immr << 16) | (imms << 10))
    return None


# ------------------------------------------------------------------ x86 scan

def x86_immediate_sites(exe, md, start, end):
    """
    Every immediate operand in an x86 function body, as
    {(operand_va, width): (value, mnemonic)}.

    Used to answer the question that matters for a recompiled binary: when the
    recompiler hoists a constant into one register, how many x86 instructions
    share it? Patching the hoisted word changes all of them at once.
    """
    try:
        code = exe.read(start, end - start)
    except Exception:
        return {}
    sites = {}
    for insn in md.disasm(code, start):
        b = bytes(insn.bytes)
        for op in insn.operands:
            if op.type != 2:                      # X86_OP_IMM
                continue
            for w in (4, 2, 1):
                for v in (op.imm & ((1 << (8 * w)) - 1),):
                    enc = v.to_bytes(w, 'little')
                    idx = b.rfind(enc)
                    if idx >= 0 and idx + w == insn.size:
                        sites[(insn.address + idx, w)] = (v, insn.mnemonic)
                        break
                else:
                    continue
                break
    return sites


def _bucket(nso, rs, mnemonics, x86_all, ffnx_vas, key, stock, a0, a1):
    """
    Resolve one (function, value, x86-instruction-class) group.

    Three outcomes are accepted, everything else is queued:

      1:1        as many ARM64 sites as x86 sites -> the k-th x86 site is the
                 k-th ARM64 site, because the recompiler emits a function body
                 in x86 order.
      hoisted    exactly one ARM64 site, shared by every x86 site, and FFNx
                 patches every one of them -> one word, no collateral damage.
      shared     exactly one ARM64 site but FFNx patches only some of the x86
                 sites -> REFUSED. Patching it would silently change the rest.
                 This is the failure mode that made the in-battle stats menu
                 4x slower in an earlier attempt.
    """
    def scan(classmap):
        allowed = set()
        for mn in mnemonics:
            allowed |= classmap.get(mn, set(REWRITABLE))
        out = []
        for off in range(a0, a1, 4):
            w = struct.unpack('<I', nso.text[off:off + 4])[0]
            for tag, val in arm64_immediates(w):
                if val == key and tag in allowed:
                    out.append((off, w, tag))
                    break
        return out

    def good(c):
        return len(c) == len(x86_all) or (len(c) == 1
                                          and set(ffnx_vas) == set(x86_all))

    tier = 'narrow'
    cands = scan(X86_NARROW)
    if not good(cands):
        broad = scan(X86_BROAD)
        if good(broad) or not cands:
            cands, tier = broad, 'broad'
    for r in rs:
        r['cands'] = cands
        r['tier'] = tier
        r['x86_group'] = ['0x%X' % v for v in x86_all]

    if len(cands) == len(x86_all):
        for r in rs:
            k = x86_all.index(r['va'])
            off, w, tag = cands[k]
            r.update(status='ok', hook=off, orig=w, tag=tag, covers=[r['va']],
                     evidence='1:1 %s tier (%d x86 %s sites, %d ARM64 sites, '
                              'position %d)'
                              % (tier, len(x86_all), '/'.join(sorted(mnemonics)),
                                 len(cands), k))
    elif len(cands) == 1 and set(ffnx_vas) == set(x86_all):
        off, w, tag = cands[0]
        rs[0].update(status='ok', hook=off, orig=w, tag=tag, covers=x86_all,
                     evidence='hoisted constant shared by all %d x86 sites, '
                              'and FFNx patches all %d'
                              % (len(x86_all), len(ffnx_vas)))
        for r in rs[1:]:
            r.update(status='folded', hook=off,
                     why='same hoisted ARM64 word as 0x%X' % rs[0]['va'])
    elif len(cands) == 1:
        off, w, tag = cands[0]
        for r in rs:
            r.update(status='review', hook=off, orig=w, tag=tag,
                     why='SHARED CONSTANT: one hoisted ARM64 word serves %d x86 '
                         'sites but FFNx patches only %d -- patching it would '
                         'change the %d it does not'
                         % (len(x86_all), len(ffnx_vas),
                            len(x86_all) - len(ffnx_vas)))
    elif not cands:
        for r in rs:
            r.update(status='none',
                     why='value %s is not present as a rewritable ARM64 '
                         'immediate in the body' % stock)
    else:
        for r in rs:
            r.update(status='ambiguous',
                     why='%d ARM64 candidates for %d x86 sites -- counts do not '
                         'match, cannot assign' % (len(cands), len(x86_all)))

# ------------------------------------------------------------------ resolve

def resolve(exe, nso, specs, mults, md_x86):
    """
    Two passes. First compute each spec's stock value and target value; then
    resolve the ARM64 site per (function, value) so that hoisted constants and
    repeated values are handled as a set rather than one spec at a time.
    """
    results = []
    for sp in specs:
        r = dict(sp)
        r['mult_var'], r['mult'] = spec_mult(sp['arg'], mults)
        base, va = sp['_base'], sp['_base'] + sp['off']
        width = TYPE_W.get(sp['ctype'])
        r.update(va=va, width=width)
        if width is None:
            r.update(status='skip', why='unknown C type %r' % sp['ctype'])
            results.append(r)
            continue
        sect = exe.sect(va)
        r['section'] = sect[0] if sect else None
        if sect is None:
            r.update(status='fail', why='address is in no section')
            results.append(r)
            continue
        is_float = sp['ctype'] in ('float', 'double')
        signed = float if is_float else (sp['ctype'] in TYPE_SIGNED)
        try:
            stock = exe.scalar(va, width, signed)
        except Exception as e:
            r.update(status='fail', why=str(e))
            results.append(r)
            continue
        r['stock'] = stock
        # The second argument of multiply/divide is an EXPRESSION, not always
        # the bare multiplier: field_update_models_positions uses
        # `common_frame_multiplier * 2`, i.e. a divisor of 4, not 2. Evaluating
        # it is the difference between -8000 and the correct -4000.
        if sp['op'] in ('multiply', 'divide'):
            factor = eval_arg(sp['arg'], mults)
            if not factor:
                r.update(status='skip',
                         why='multiplier is not a plain expression: %s' % sp['arg'])
                results.append(r)
                continue
            r['factor'] = factor
            want = (stock * factor if sp['op'] == 'multiply'
                    else (stock / factor if is_float else int(stock / factor)))
        else:
            want = eval_arg(sp['arg'], mults)
        r['want'] = want
        if want is None:
            r.update(status='skip',
                     why='argument is not a plain literal: %s' % sp['arg'])
            results.append(r)
            continue
        if want == stock:
            r.update(status='skip', why='no change at %s=%s'
                     % (r['mult_var'], r['mult']))
            results.append(r)
            continue
        if not is_float:
            bits = 8 * width
            lo, hi = ((-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed
                      else (0, (1 << bits) - 1))
            if not (lo <= want <= hi):
                r.update(status='review',
                         why='target %s does not fit a %s %d-byte field '
                             '(range %d..%d)'
                             % (want, 'signed' if signed else 'unsigned',
                                width, lo, hi))
                results.append(r)
                continue

        # data, not code -> patch ff7_en directly, no ARM64 lookup needed
        if not (X86_TEXT[0] <= va <= X86_TEXT[1]):
            r.update(status='exe', exe_off=exe.off(va))
            results.append(r)
            continue
        if is_float:
            r.update(status='review',
                     why='float operand inside .text: needs a literal-pool '
                         'lookup, not an immediate search')
            results.append(r)
            continue
        fn = nso.containing(va)
        if fn is None or not (fn[0] <= va < fn[1]):
            r.update(status='fail', why='not inside a mapped function')
            results.append(r)
            continue
        r['fn_x86'], r['fn_x86_end'] = fn
        if fn[0] != base:
            r['note'] = ('operand lies in 0x%X, not the named base 0x%X'
                         % (fn[0], base))
        r['fn_arm'], r['fn_arm_end'] = nso.body(fn[0])
        r['status'] = 'pending'
        results.append(r)

    # ---- per (function, value) resolution -------------------------------
    from collections import defaultdict
    buckets = defaultdict(list)
    for r in results:
        if r['status'] == 'pending':
            buckets[(r['fn_x86'], r['stock'], r['width'])].append(r)

    x86_cache = {}
    for (fn_x86, stock, width), rs in buckets.items():
        fn_end = rs[0]['fn_x86_end']
        a0, a1 = rs[0]['fn_arm'], rs[0]['fn_arm_end']
        if fn_x86 not in x86_cache:
            x86_cache[fn_x86] = x86_immediate_sites(exe, md_x86, fn_x86, fn_end)
        sites = x86_cache[fn_x86]

        # every x86 instruction in this function carrying this value
        key0 = stock & ((1 << (8 * width)) - 1)
        all_sites = sorted(va for (va, w), (v, mn) in sites.items()
                           if w == width and v == key0)
        mnemonics = {sites[(va, width)][1] for va in all_sites}
        ffnx_all = sorted(r['va'] for r in rs)
        for r in rs:
            r['x86_all'] = ['0x%X' % v for v in all_sites]
            r['x86_mnemonics'] = sorted(mnemonics)
        if not all_sites:
            for r in rs:
                r.update(status='review',
                         why='could not locate the operand as an x86 immediate '
                             '(FFNx may be patching a non-immediate byte)')
            continue
        if not set(ffnx_all) <= set(all_sites):
            for r in rs:
                r.update(status='review',
                         why='FFNx target is not among the x86 immediates found')
            continue

        # Split by x86 instruction class. A `cmp` and a `sar` that happen to
        # share the number 2 translate to completely different ARM64 forms, so
        # counting or position-matching them together is meaningless. Classes
        # whose ARM64 tag sets overlap are kept in one group, because a scan
        # for one would also pick up the other.
        comps = []
        for mn in sorted(mnemonics):
            s = set(X86_NARROW.get(mn, set(REWRITABLE)))
            hit = [c for c in comps if c[0] & s]
            if hit:
                hit[0][0] |= s
                hit[0][1].add(mn)
                for extra in hit[1:]:
                    hit[0][0] |= extra[0]
                    hit[0][1] |= extra[1]
                    comps.remove(extra)
            else:
                comps.append([s, {mn}])

        for _classes, mns in comps:
            x86_all = [va for va in all_sites if sites[(va, width)][1] in mns]
            group = [r for r in rs if r['va'] in x86_all]
            if not group:
                continue
            ffnx_vas = sorted(r['va'] for r in group)
            _bucket(nso, group, mns, x86_all, ffnx_vas, key0, stock, a0, a1)
        continue
    # ---- encode, and refuse anything that cannot be encoded cleanly -----
    for r in results:
        if r['status'] != 'ok':
            continue
        if r['tag'] not in REWRITABLE:
            r.update(status='review',
                     why='%s immediates are not safely rewritable' % r['tag'])
            continue
        new = rewrite(r['orig'], r['tag'], r['want'])
        if new is None:
            r.update(status='review',
                     why='%s does not fit the %s immediate slot'
                         % (r['want'], r['tag']))
            continue
        r['new'] = new

    # ---- final gate: no two emitted patches may target the same word ----
    seen = {}
    for r in results:
        if r['status'] != 'ok':
            continue
        if r['hook'] in seen:
            other = seen[r['hook']]
            for x in (r, other):
                x.update(status='review',
                         why='hook collision at +0x%X with another spec'
                             % r['hook'])
            continue
        seen[r['hook']] = r
    return results


# ------------------------------------------- post-call return-value scalers

# FFNx does not reimplement these opcode handlers. It wraps ONE call inside
# each of them -- the call to get_bank_value -- and scales the returned value:
#
#   short ff7_opcode_multiply_get_bank_value(short bank, short address) {
#       int16_t ret = ff7_externals.get_bank_value(bank, address);
#       if (is_fps_running_more_than_original()) ret *= get_frame_multiplier();
#       return ret;
#   }
#
# get_frame_multiplier() returns common_frame_multiplier, i.e. 2 at 60 FPS.
#
# On Switch that needs no function replacement: the guest return value lives in
# the guest CPU context, so scaling it AFTER the translated `bl` returns is
# equivalent. That is the safe cave pattern -- branch out, adjust, replay the
# displaced instruction, branch back -- with control flow untouched. Nothing is
# skipped, so no guest return address is left on the guest stack.
#
# Opcode numbers are from FFNx's FieldOpcode enum, cross-checked against the
# four table indices FFNx hardcodes ([0x21]=TUTOR, [0x39]=GOLDu, [0x59]=DLITM,
# [0x5B]=SMTRA).
OPCODE_NUM = {'NFADE': 0x25, 'SCRLA': 0x63, 'SCR2DC': 0x66, 'SCR2DL': 0x68,
              'VWOFT': 0x6A, 'SCRLP': 0x6F, 'JUMP': 0xC0, 'OFST': 0xC3}

# (opcode, offset of the call inside the handler, operation, what it governs)
OPCODE_SCALERS = [
    ('JUMP',   0x1F1, 'mul', 'field jump arc: lands before the animation ends'),
    ('SCRLA',  0x072, 'mul', 'scripted scroll / elevator speed'),
    ('SCR2DC', 0x03C, 'mul', 'scripted 2D scroll with constant speed'),
    ('SCR2DL', 0x03C, 'mul', 'scripted 2D scroll, linear'),
    ('SCRLP',  0x0A7, 'mul', 'scripted scroll to party'),
    ('OFST',   0x046, 'mul', 'model offset movement'),
    ('VWOFT',  0x0CC, 'mul', 'view offset'),
    ('NFADE',  0x089, 'div', 'screen fade frame count'),
]

# The instruction we displace must be exactly this shape: the guest ESP reload
# that follows every translated call. It is relocatable, and its base register
# identifies the guest CPU context register for that function (which varies --
# x20, x21 and x22 all occur). Requiring the exact shape turns an assumption
# into a check.
CTX_ESP_OFFSET = 0x10
_LDR_W8_CTX_ESP = 0xB9401008          # ldr w8, [xN, #0x10] with Rn cleared


def decode_ldr_ctx(word):
    """Return the context register if `word` is `ldr w8, [xN, #0x10]`."""
    if (word & ~(0x1F << 5)) != _LDR_W8_CTX_ESP:
        return None
    return (word >> 5) & 0x1F


def resolve_opcode_scalers(exe, nso, ext, mx, ma):
    """
    Locate the ARM64 instruction immediately after each translated
    `call get_bank_value`, and record the context register with it.

    Validation, all of which must pass or the site is refused:
      * the x86 bytes at handler+offset really are `call get_bank_value`;
      * the number of x86 calls to get_bank_value in the function equals the
        number of ARM64 `bl` to its translation (so position identifies which);
      * the FFNx-specified call is among them;
      * the instruction after the matched `bl` is `ldr w8, [xN, #0x10]`.
    """
    tbl = ext.get('common_externals.execute_opcode_table')
    gbv = ext.get('common_externals.get_bank_value')
    if not tbl or not gbv or gbv not in nso.x86_to_arm:
        return [], ['execute_opcode_table or get_bank_value did not resolve']
    gbv_arm = nso.x86_to_arm[gbv]
    out, problems = [], []
    for name, off, op, what in OPCODE_SCALERS:
        handler = exe.u32(tbl + 4 * OPCODE_NUM[name])
        fn = nso.containing(handler)
        if fn is None:
            problems.append('%s: handler 0x%X not in the map' % (name, handler))
            continue
        fs, fe = fn
        try:
            if exe.read(handler + off, 1)[0] != 0xE8:
                problems.append('%s+0x%X is not a call' % (name, off))
                continue
            if exe.get_relative_call(handler, off) != gbv:
                problems.append('%s+0x%X does not call get_bank_value'
                                % (name, off))
                continue
        except Exception as e:
            problems.append('%s: %s' % (name, e))
            continue
        x86calls = [i.address for i in mx.disasm(exe.read(fs, fe - fs), fs)
                    if i.mnemonic == 'call' and i.op_str == '0x%x' % gbv]
        a0, a1 = nso.body(fs)
        ins = list(ma.disasm(nso.text[a0:a1], a0))
        bls = [k for k, i in enumerate(ins)
               if i.mnemonic == 'bl' and i.op_str == '#0x%x' % gbv_arm]
        target = handler + off
        if len(x86calls) != len(bls) or target not in x86calls:
            problems.append('%s: %d x86 calls vs %d ARM64 bl -- cannot assign'
                            % (name, len(x86calls), len(bls)))
            continue
        k = x86calls.index(target)
        nxt = ins[bls[k] + 1]
        word = struct.unpack('<I', nso.text[nxt.address:nxt.address + 4])[0]
        ctx = decode_ldr_ctx(word)
        if ctx is None:
            problems.append('%s: displaced insn is %s %s (%08X), not the '
                            'expected guest-ESP reload'
                            % (name, nxt.mnemonic, nxt.op_str, word))
            continue
        out.append(dict(name=name, opcode=OPCODE_NUM[name], handler=handler,
                        x86_call=target, op=op, what=what, hook=nxt.address,
                        displaced=word, ctx=ctx, position=k,
                        of=len(x86calls)))
    return out, problems


# ------------------------------------------------------- symptom grouping

# Group by the symptom a tester can actually SEE, not by which FFNx file the
# constant came from. Each group becomes one independently toggleable flag, so
# a hardware test moves exactly one variable (HANDOFF 5g).
SYMPTOM_RULES = [
    ('enemy_death',   ('battle_enemy_death', 'battle_iainuki_death',
                       'battle_boss_death', 'battle_melting_death',
                       'battle_disintegrate', 'battle_morph_death')),
    ('battle_camera', ('battle_sub_430DD0', 'battle_sub_429AC0',
                       'battle_sub_429D8A')),
    ('battle_aura',   ('limit_break_aura', 'enemy_skill_aura', 'summon_aura',
                       'run_summon_animations', 'tifa_limit', 'aerith_limit',
                       'vincent_limit')),
    ('summons',       ('run_odin', 'run_shiva', 'run_bahamut', 'run_ramuh',
                       'run_alexander', 'run_kotr', 'run_phoenix',
                       'run_hades', 'run_typhon', 'run_leviathan',
                       'run_ifrit', 'run_titan', 'run_kujata', 'run_neobahamut')),
    ('battleground',  ('update_3d_battleground', 'battleground_')),
    ('battle_damage', ('display_battle_damage', 'battle_handle_player_mark',
                       'battle_handle_status_effect')),
    ('battle_escape', ('battle_escape_magic',)),
    ('field_text',    ('field_text_box', 'field_opcode_message',
                       'field_opcode_ask')),
    ('field_fade',    ('field_handle_screen_fading', 'field_initialize_variables')),
    ('field_models',  ('field_update_models_positions',)),
    ('world',         ('world_',)),
    ('swirl',         ('swirl_',)),
]


def symptom_group(name):
    for group, prefixes in SYMPTOM_RULES:
        if any(p in name for p in prefixes):
            return group
    return 'battle_misc'


# ------------------------------------------- FFNx code-replacement detection

# Symbols FFNx does not merely re-tune but REPLACES or REPOINTS. Scaling the
# constants of a subsystem whose logic FFNx also rewrites is the second way to
# desynchronise it -- distinct from partial constant coverage, and invisible to
# the coverage count. Boss death and disintegrate-1 death each pair four
# constants with a replace_function; applying only the constants is what made
# "some of the enemy vanquish effects look screwed up".
# Only two kinds of FFNx work actually invalidate a function's constants:
#
#   replace_function(X, ours)          -- X's whole body is gone; every constant
#                                         inside it is timing logic we no longer
#                                         run. Compromised.
#   patch_code_dword(X + off, &table)  -- a data pointer INSIDE X is repointed at
#                                         an FFNx-allocated table the constants
#                                         are calibrated against. Compromised.
#
# replace_call_function(X + off, ours) and memcpy_code are NOT included. They
# change one call site, not the function's timing. Treating them as fatal would
# have excluded field_update_models_positions -- whose ladder/jump constants are
# confirmed working on hardware, while the replaced call at +0x7C is model
# rotation, an unrelated concern in the same function.
RE_REPLACE = re.compile(
    r'replace_function\s*\(\s*(?:\(\s*uint32_t\s*\)\s*)?(?:&\s*)?'
    r'(?:ff7_externals|common_externals)\s*\.\s*(\w+)\s*,')
# The second argument must be a SYMBOL, not a literal: repointing at an
# FFNx-owned table is fatal, while patch_code_dword(x, 0x000B9585) is just a
# constant. An array name decays without `&`, which is how
# display_battle_damage's y_pos_offset table repoint first slipped through.
RE_REPOINT = re.compile(
    r'patch_code_dword\s*\(\s*(?:\(\s*uint32_t\s*\)\s*)?&?\s*'
    r'(?:ff7_externals|common_externals)\s*\.\s*(\w+)[^;]*?,\s*'
    r'\(\s*DWORD\s*\)\s*&?\s*[A-Za-z_]\w*')
RE_WRAPPED_CALL = re.compile(
    r'(?:replace_call_function|memcpy_code)\s*\(\s*(?:\(\s*uint32_t\s*\)\s*)?'
    r'(?:&\s*)?(?:ff7_externals|common_externals)\s*\.\s*(\w+)\s*\+\s*'
    r'(0x[0-9A-Fa-f]+|\d+)')

# Stems too generic to identify a subsystem: `battle_sub_42A5EB` and
# `battle_sub_5BD050` are unrelated functions that merely lack real names, so
# they must not be collapsed into one family.
GENERIC_STEMS = {'battle_sub', 'field_sub', 'world_sub', 'battle', 'field',
                 'world', 'run', 'display', 'battle_menu', 'menu', 'sub'}
RE_FAMILY = re.compile(r'_(?:sub_)?[0-9A-F]{6}$')


def family(name):
    """Subsystem a symbol belongs to, or the symbol itself if it stands alone."""
    stem = RE_FAMILY.sub('', name)
    if stem == name or stem in GENERIC_STEMS or stem.count('_') < 2:
        return name
    return stem


def code_replaced_families(src_root):
    """
    Families where FFNx replaces or repoints code, so constants alone are not
    the whole fix.

    Opcode-table entries are excluded: those are the post-call return-value
    scalers, which we now implement natively.
    """
    detail, wrapped = {}, {}
    for rel in GROUPS:
        path = os.path.join(src_root, rel)
        if not os.path.isfile(path):
            continue
        txt = open(path, encoding='utf-8', errors='replace').read()
        txt = re.sub(r'//[^\n]*', '', txt)
        for rx, kind in ((RE_REPLACE, 'body replaced'),
                         (RE_REPOINT, 'data pointer repointed')):
            for m in rx.finditer(txt):
                sym = m.group(1)
                if sym == 'execute_opcode_table':
                    continue
                detail.setdefault(family(sym), set()).add('%s %s' % (sym, kind))
        for m in RE_WRAPPED_CALL.finditer(txt):
            sym, off = m.group(1), int(m.group(2), 0)
            if sym == 'execute_opcode_table':
                continue
            wrapped.setdefault(sym, set()).add(off)
    return ({f: sorted(v) for f, v in detail.items()},
            {k: sorted(v) for k, v in wrapped.items()})


def coverage(res):
    """
    Per-symbol coverage: how many of FFNx's constants for a function we can
    apply, out of how many it patches.

    This is the property that decides whether a patch is safe to ship, and it
    is not visible from any single spec. `battle_sub_5B9EC2` controls the
    colour ramp on character models; FFNx scales five constants there. Applying
    three of them scaled the counter wrap and the timing but not the two ramp
    divides, and enemies rendered white until the counter wrapped.

    A function is only safe when every constant FFNx touches in it is
    accounted for -- either applied, or provably unchanged at this multiplier.
    """
    from collections import defaultdict
    cov = defaultdict(lambda: [0, 0])
    for r in res:
        sym = r['sym'].split('.')[-1]
        cov[sym][1] += 1
        done = (r['status'] in ('ok', 'folded', 'exe')
                or (r['status'] == 'skip'
                    and 'no change' in (r.get('why') or '')))
        if done:
            cov[sym][0] += 1
    return {k: tuple(v) for k, v in cov.items()}


def write_groups(path, ok, battle_mult, common_mult, npass, ntotal, res=None,
                 scalers=None, ffnx_root=None):
    """Emit a Python module of flag-gated patch groups, generated not typed."""
    from collections import defaultdict
    cov = coverage(res or ok)
    replaced, _wrapped = code_replaced_families(ffnx_root or '/var/tmp/ffnx/src')
    groups = defaultdict(list)
    partial = defaultdict(list)
    paired = defaultdict(list)
    for r in ok:
        sym = r['sym'].split('.')[-1]
        got, tot = cov.get(sym, (1, 1))
        r['coverage'] = '%d/%d' % (got, tot)
        fam = family(sym)
        r['family'] = fam
        r['ffnx_code'] = replaced.get(fam, [])
        if r['ffnx_code']:
            paired[symptom_group(sym)].append(r)
        elif got == tot:
            groups[symptom_group(sym)].append(r)
        else:
            partial[symptom_group(sym)].append(r)
    with open(path, 'w') as f:
        f.write('"""\nff7nx_patchgroups.py -- GENERATED by ff7nx_resolve.py. '
                'Do not hand-edit.\n\n'
                'Each entry is (label, module_offset, expected_stock_word, '
                'new_word).\nEvery offset was resolved from FFNx\'s own patch '
                'specs through the\nx86->ARM64 recompilation map, and every '
                'stock word is verified\nbefore it is written.\n\n'
                'battle_frame_multiplier = %d, common_frame_multiplier = %d\n'
                'Resolver self-validation against hand-derived, '
                'hardware-confirmed\npatches at generation time: %d/%d '
                'reproduced.\n\n'
                'NOTHING HERE IS HARDWARE-CONFIRMED. Every group is off by '
                'default and\nmust be enabled explicitly, one at a time, so a '
                'test result means\nsomething. See HANDOFF 5g.\n\n'
                'PATCH_GROUPS holds only functions where EVERY constant FFNx\n'
                'scales is accounted for. PARTIAL_GROUPS holds the rest --\n'
                'functions we can only partly scale. Partial is not "less\n'
                'benefit", it is desynchronisation: three of the five\n'
                'battle_sub_5B9EC2 colour constants made enemies render white.\n'
                '"""\n\n'
                % (battle_mult, common_mult, npass, ntotal))
        for var, src in (('PATCH_GROUPS', groups), ('PARTIAL_GROUPS', partial),
                         ('CODE_PAIRED_GROUPS', paired)):
            f.write('%s = {\n' % var)
            for g in sorted(src):
                rs = sorted(src[g], key=lambda r: r['hook'])
                f.write("    %r: [\n" % g)
                for r in rs:
                    extra = (' covers %d x86 sites' % len(r['covers'])
                             if len(r.get('covers', [])) > 1 else '')
                    f.write("        (%r,\n         0x%08X, 0x%08X, 0x%08X),"
                            "  # %s %s %s->%s  [%s]%s\n"
                            % ('%s+0x%X' % (r['sym'].split('.')[-1], r['off']),
                               r['hook'], r['orig'], r['new'],
                               r['tag'], r['ctype'], r['stock'], r['want'],
                               r['coverage'], extra))
                f.write("    ],\n")
            f.write('}\n\n')
        f.write('# post-call return-value scalers: FFNx wraps the call to\n'
                '# get_bank_value inside these opcode handlers and scales the\n'
                '# result. hook is the instruction AFTER the translated bl; ctx\n'
                '# is the guest CPU context register for that function.\n')
        f.write('OPCODE_SITES = [\n')
        for s_ in (scalers or []):
            f.write('    dict(name=%r, op=%r, hook=0x%08X, displaced=0x%08X,\n'
                    '         ctx=%d, handler=0x%06X, x86_call=0x%06X, what=%r),\n'
                    % (s_['name'], s_['op'], s_['hook'], s_['displaced'],
                       s_['ctx'], s_['handler'], s_['x86_call'], s_['what']))
        f.write(']\n\n')
        f.write('# symbol -> (constants we can apply, constants FFNx scales)\n')
        f.write('COVERAGE = {\n')
        for k in sorted(cov):
            f.write('    %r: %r,\n' % (k, cov[k]))
        f.write('}\n\n')
        f.write('# provenance: why each hook is believed correct\n')
        f.write('EVIDENCE = {\n')
        for g in sorted(groups):
            for r in sorted(groups[g], key=lambda r: r['hook']):
                f.write("    0x%08X: %r,\n" % (r['hook'], r.get('evidence', '')))
        f.write('}\n\n')
        # hook -> (arm64 tag, stock operand, intended operand). Lets an
        # independent checker confirm the patched instruction really carries
        # the value FFNx asks for, not merely a different value.
        f.write('INTENT = {\n')
        for g in sorted(groups):
            for r in sorted(groups[g], key=lambda r: r['hook']):
                f.write("    0x%08X: (%r, %d, %d),\n"
                        % (r['hook'], r['tag'], r['stock'], r['want']))
        f.write('}\n')


# ---------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--exe', required=True)
    ap.add_argument('--nso', required=True)
    ap.add_argument('--ffnx', default='/var/tmp/ffnx/src',
                    help='FFNx src/ checkout')
    ap.add_argument('--battle-mult', type=int, default=4,
                    help='battle_frame_multiplier (4 at 60 FPS)')
    ap.add_argument('--common-mult', type=int, default=2,
                    help='common_frame_multiplier (2 at 60 FPS)')
    ap.add_argument('--group', action='append',
                    help='restrict to a group (battle_anim, battle_camera, '
                         'field, world); repeatable')
    ap.add_argument('--json', help='write full machine-readable results')
    ap.add_argument('--emit-py', metavar='FILE',
                    help='write flag-gated patch groups as a Python module')
    ap.add_argument('--show-queue', action='store_true',
                    help='print the ambiguous/none review queue in detail')
    a = ap.parse_args()

    exe = Exe(open(a.exe, 'rb').read())
    nso = Nso(open(a.nso, 'rb').read())
    sources = []
    for f in ('ff7_data.h', 'common.cpp'):
        p = os.path.join(a.ffnx, f)
        if os.path.isfile(p):
            sources.append(open(p, encoding='utf-8', errors='replace').read())
    ext = Externals(exe, sources)

    print('== derivation roots (read out of the exe, not guessed)')
    for k, v in ext.roots.items():
        print('   %-18s 0x%X' % (k, v))
    print('\n== derivation chain')
    print('   %d symbols resolved from ff7_en' % len(
        [k for k in ext.sym if '.' in k]))
    print('   name-suffix cross-check: %d agree, %d disagree'
          % (ext.suffix_ok, len(ext.suffix_bad)))
    for name, got, want in ext.suffix_bad[:12]:
        print('   !! %s derived 0x%X, name says 0x%X' % (name, got, want))
    for probe, want in (('ff7_externals.field_sub_6388EE', 0x6388EE),
                        ('ff7_externals.battle_sub_429AC0', 0x429AC0),
                        ('ff7_externals.battle_sub_42A5D0', 0x42A5D0),
                        ('ff7_externals.swirl_loop_sub_4026D4', 0x4026D4),
                        ('ff7_externals.world_loop_74BE49', 0x74BE49),
                        ('ff7_externals.field_update_models_positions', 0x6342C6),
                        ('ff7_externals.field_handle_screen_fading', 0x63B84B),
                        ('ff7_externals.field_initialize_variables', 0x63BDA8),
                        ('ff7_externals.update_3d_battleground', None),
                        ('ff7_externals.battle_update_3d_model_data', None)):
        got = ext.get(probe)
        tail = ''
        if want is not None:
            tail = 'OK' if got == want else '<- expected 0x%X' % want
        print('   %-46s %-10s %s' % (probe.split('.')[-1],
                                     ('0x%X' % got) if got else '?', tail))

    specs = scrape(a.ffnx, log=print)
    if a.group:
        specs = [s for s in specs if s['group'] in a.group]
    keep = []
    unresolved = []
    for s in specs:
        b, src = ext.get_or_suffix(s['sym'])
        if b is None:
            s['_why'] = 'symbol not derivable and name carries no address'
            unresolved.append(s)
            continue
        s['_base'] = b
        s['_base_src'] = src
        # Most bases are function entries, but a few externals are addresses
        # *inside* a function (battle_fps_menu_multiplier). Those are fine --
        # the containing function is what we need, and the map gives it.
        if X86_TEXT[0] <= b <= X86_TEXT[1]:
            if nso.containing(b) is None:
                s['_why'] = 'base 0x%X has no containing function in the map' % b
                unresolved.append(s)
                continue
            s['_base_entry'] = (b in nso.x86_to_arm)
        keep.append(s)

    print('\n== specs')
    print('   %d scraped, %d with a validated base, %d unresolved'
          % (len(specs), len(keep), len(unresolved)))
    nsuf = sum(1 for s in keep if s['_base_src'] == 'name-suffix')
    print('   %d bases derived through the chain, %d taken from the name suffix'
          % (len(keep) - nsuf, nsuf))
    for s in unresolved[:15]:
        print('   -- %s %s+0x%X: %s' % (s['group'], s['sym'].split('.')[-1],
                                        s['off'], s.get('_why', '')))

    mults = {'battle_frame_multiplier': a.battle_mult,
             'common_frame_multiplier': a.common_mult,
             'world_frame_multiplier': a.common_mult}
    print('\n== multipliers (FFNx ff7_opengl.cpp at FPS_LIMITER_60FPS)')
    print('   battle_frame_multiplier = %d   (battle is natively 15 FPS)'
          % a.battle_mult)
    print('   common_frame_multiplier = %d   (field/world are natively 30 FPS)'
          % a.common_mult)

    md_x86 = Cs(CS_ARCH_X86, CS_MODE_32)
    md_x86.detail = True
    res = resolve(exe, nso, keep, mults, md_x86)

    ma = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    scalers, scaler_problems = resolve_opcode_scalers(exe, nso, ext, md_x86, ma)
    print('\n== post-call return-value scalers (opcode handlers)')
    print('   get_bank_value  x86 0x%X -> arm 0x%X'
          % (ext.get('common_externals.get_bank_value') or 0,
             nso.x86_to_arm.get(ext.get('common_externals.get_bank_value'), 0)))
    for s_ in scalers:
        print('   %-7s %s  hook +0x%06X  ctx x%-2d  (call %d of %d)  %s'
              % (s_['name'], s_['op'], s_['hook'], s_['ctx'],
                 s_['position'] + 1, s_['of'], s_['what']))
    for p_ in scaler_problems:
        print('   !! %s' % p_)
    print('   %d of %d resolved' % (len(scalers), len(OPCODE_SCALERS)))

    order = ['ok', 'folded', 'exe', 'ambiguous', 'none', 'review', 'skip', 'fail']
    by = {k: [r for r in res if r['status'] == k] for k in order}
    print('\n== resolution')
    for k in order:
        print('   %-10s %d' % (k, len(by[k])))

    reps, wrapped = code_replaced_families(a.ffnx)
    print('\n== families where FFNx also replaces or repoints code')
    print('   constants alone are not the whole fix for these')
    touched = {family(r['sym'].split('.')[-1]) for r in by['ok']}
    for f in sorted(set(reps) & touched):
        print('   %-38s %s' % (f, ', '.join(reps[f])))
    print('   %d of %d families we patch are affected'
          % (len(set(reps) & touched), len(touched)))
    tsyms = {r['sym'].split('.')[-1] for r in by['ok']}
    near = {k: v for k, v in wrapped.items() if k in tsyms}
    if near:
        print('\n   FYI -- single call sites FFNx wraps inside functions we also')
        print('   patch. Not fatal (the ladder/jump constants are confirmed good')
        print('   despite one), but worth knowing if a symptom persists:')
        for k in sorted(near):
            print('     %-38s wrapped at %s'
                  % (k, ', '.join('+0x%X' % o for o in near[k])))

    print('\n== READY: NSO words (paste into ff7nx_60fps.py) ==')
    emitted = sorted(by['ok'], key=lambda r: (r['group'], r['hook']))
    cur = None
    for r in emitted:
        if r['group'] != cur:
            cur = r['group']
            print('    # ---- %s ----' % cur)
        label = '%s+0x%X' % (r['sym'].split('.')[-1], r['off'])
        extra = ' [covers %d x86 sites]' % len(r['covers']) \
            if len(r.get('covers', [])) > 1 else ''
        print("    ('%s %s -> %s', 0x%08X, 0x%08X, 0x%08X),%s"
              % (label, r['stock'], r['want'], r['hook'], r['orig'], r['new'],
                 extra))
    if by['exe']:
        print('\n== READY: ff7_en data bytes ==')
        for r in by['exe']:
            print("    ('%s+0x%X %s %s -> %s', 0x%06X, %s)"
                  % (r['sym'].split('.')[-1], r['off'], r['ctype'],
                     r['stock'], r['want'], r['va'], r['section']))

    if a.show_queue:
        print('\n== REVIEW QUEUE (nothing here is applied) ==')
        for k in ('review', 'ambiguous', 'none', 'skip', 'fail'):
            if not by[k]:
                continue
            print('\n  --- %s (%d) ---' % (k, len(by[k])))
            for r in by[k]:
                print('  %s %s+0x%X  va=0x%X %s stock=%s want=%s'
                      % (r['group'], r['sym'].split('.')[-1], r['off'],
                         r['va'], r['ctype'], r.get('stock'), r.get('want')))
                if r.get('why'):
                    print('      %s' % r['why'])
                if r.get('x86_mnemonics'):
                    print('      x86: %s at %s' % (','.join(r['x86_mnemonics']),
                                                   ' '.join(r.get('x86_all', []))))
                for off, w, tag in (r.get('cands') or [])[:6]:
                    print('      cand +0x%06X %08X %s' % (off, w, tag))

    # ---- self-validation against patches already found by hand -----------
    # The strongest available check on this tool: patches the previous session
    # derived manually and confirmed on hardware must come back out of the
    # automated pipeline, byte for byte. A regression here means the pipeline
    # is wrong, regardless of how plausible its other output looks.
    KNOWN = [
        ('swirl fade count 46 -> 50',        0x000130B4, 0x7100B928, 0x7100C928),
        ('swirl clamp 78 -> 127',            0x000133B8, 0x71013928, 0x7101FD28),
        ('battle FPS menu multiplier 4 -> 1', 0x00090AE8, 0x321E03E8, 0x52800028),
        ('field ladder/jump mult -16000 -> -4000',
         0x009D8720, 0x1287CFFC, 0x1281F3FC),
        ('field ladder/jump step /4 -> /2 (1)', 0x009D9B30, 0x13027D08, 0x13017D08),
        ('field ladder/jump step /4 -> /2 (2)', 0x009DB0CC, 0x13027D08, 0x13017D08),
    ]
    print('\n== self-validation against hand-derived, hardware-confirmed patches')
    found = {r['hook']: r for r in by['ok']}
    npass = 0
    for label, hook, orig, new in KNOWN:
        r = found.get(hook)
        if r is None:
            q = [x for x in res if x.get('hook') == hook]
            where = ('queued as %s: %s' % (q[0]['status'], q[0].get('why', ''))
                     if q else 'not produced at all')
            print('   MISS  +0x%06X  %-38s %s' % (hook, label, where))
        elif r['orig'] == orig and r['new'] == new:
            print('   PASS  +0x%06X  %-38s %08X -> %08X'
                  % (hook, label, orig, new))
            npass += 1
        else:
            print('   FAIL  +0x%06X  %-38s got %08X -> %08X, want %08X -> %08X'
                  % (hook, label, r['orig'], r['new'], orig, new))
    print('   %d/%d reproduced' % (npass, len(KNOWN)))

    if a.emit_py:
        write_groups(a.emit_py, by['ok'], a.battle_mult, a.common_mult, npass,
                     len(KNOWN), res=res, scalers=scalers, ffnx_root=a.ffnx)
        print('\nwrote %s' % a.emit_py)

    if a.json:
        json.dump(res, open(a.json, 'w'), indent=1, default=str)
        print('\nwrote %s' % a.json)
    print('\n%d NSO words ready (%d specs covered), %d exe bytes ready, '
          '%d queued for review.'
          % (len(emitted), len(emitted) + len(by['folded']), len(by['exe']),
             len(by['review']) + len(by['ambiguous']) + len(by['none'])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
