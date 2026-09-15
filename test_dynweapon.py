#!/usr/bin/env python3
"""
Regression tests for dynamic weapons -- both halves.

The asset half (`ff7nx_dynweapon`) ships one mesh per equipped weapon under a
name that carries its own decision; the code half (`ff7nx_dwhook`) rewrites
two bytes of that name when the game opens it. What these tests pin:

  * the two halves agree about the savemap, and both agree with 7th Heaven's
    own `7thHeaven.var` and FFNx's `struct savemap`;
  * the name format round-trips, and the cave reads back exactly what the
    build wrote;
  * a folder gate is read from the mod's RuntimeVar and nothing else --
    including the compound "this weapon, except in these scenes" form;
  * a part every weapon folder ships identically is NOT made dynamic;
  * the referrer rewrite is surgical: only the tokens that moved, with the
    file's own spacing and CRLFs intact;
  * every encoder in the cave matches capstone;
  * the cave, AT ITS REAL SCATTERED ADDRESSES in the shipped module, does
    what it claims -- clamping, idempotence, and leaving every other filename
    in the game alone.

Run: python3 test_dynweapon.py [path/to/exefs/main] [path/to/ff7_en]
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ff7nx_dynweapon as D                                    # noqa: E402
import ff7nx_dwhook as H                                       # noqa: E402
import iro                                                     # noqa: E402
import build                                                   # noqa: E402

FAILED = []


def check(label, cond):
    print(('ok: ' if cond else 'FAIL: ') + label)
    if not cond:
        FAILED.append(label)


# ---------------------------------------------------------------- savemap

def test_savemap():
    # AppUI/Resources/7thHeaven.var, verbatim.
    want = {0: 0xDBFDA8, 1: 0xDBFE2C, 2: 0xDBFEB0, 3: 0xDBFF34, 4: 0xDBFFB8,
            5: 0xDC003C, 6: 0xDC00C0, 7: 0xDC0144, 8: 0xDC01C8}
    check('every character address matches 7th Heaven\'s own variable file',
          all(D.savemap_addr(i) == a for i, a in want.items()))
    # FFNx struct savemap: chars[9] at +0x54, savemap_char is 132 bytes,
    # equipped_weapon at +0x1C.
    check('the address is savemap 0xDBFD38 + 0x54 + 0x84*i + 0x1C',
          D.SAVEMAP_BASE == 0xDBFD38 and D.CHARS_OFFSET == 0x54
          and D.CHAR_STRIDE == 0x84 and D.EQUIPPED_WEAPON == 0x1C)
    check('the cave and the build agree about the savemap',
          H.SAVEMAP_CHAR0 == D.savemap_addr(0)
          and H.CHAR_STRIDE == D.CHAR_STRIDE)
    check('every weapon variable name maps to a character slot',
          sorted(D.VAR_CHAR.values()) == list(range(9)))


# ------------------------------------------------------------------ names

def test_names():
    n = D.variant_name(0, 'a', 0, 16, 5)
    check('name layout is z0 c s bb n vv', n == 'z00a00f05')
    check('the cave reads the character from name[2]',
          int(n[H.N_CHAR]) == 0)
    check('the cave reads the base from name[4:6]',
          int(n[H.N_BASE:H.N_BASE + 2], 16) == 0)
    check('the cave reads count-1 from name[6]',
          int(n[H.N_CNT], 16) + 1 == 16)
    check('the cave writes the value at name[7:9]',
          int(n[H.N_VAL:H.N_VAL + 2], 16) == 5)
    check('base and value are SEPARATE fields, so a rewrite is idempotent',
          H.N_BASE + 2 <= H.N_VAL)
    check('Cid\'s top weapon still fits', D.variant_name(8, 'e', 73, 14, 86)
          == 'z08e49d56')
    for bad in ((9, 'a', 0, 16, 0), (0, 'a', 0, 17, 0), (0, 'a', 0, 16, 256)):
        try:
            D.variant_name(*bad)
            check('out-of-range %r is refused' % (bad,), False)
        except ValueError:
            check('out-of-range %r is refused' % (bad,), True)
    check('the marker is not one any shipped archive uses',
          D.MARKER == 'z0' and (H.MARKER0, H.MARKER1) == (ord('z'), ord('0')))


# ------------------------------------------------------------------ gates

def test_gates():
    bare = ('var', 'CloudWeapon', ('set', {5}), '5')
    check('a bare weapon RuntimeVar is read', D.weapon_var_of(bare) == (0, 5))
    check('case does not matter',
          D.weapon_var_of(('var', 'cidweapon', ('set', {80}), '80')) == (8, 80))
    # The Fixes mod's weapon-0 folder: "the Buster Sword, except in these
    # scenes". The scene half cannot be honoured at load time and is ignored.
    compound = ('and', [
        ('var', 'CloudWeapon', ('set', {0}), '0'),
        ('not', [('or', [('var', 'FieldID', ('range', (723, 727)), '723..727'),
                         ('var', 'PPV', ('set', {293}), '293')])])])
    check('a weapon gate wrapped in scene tests is still read',
          D.weapon_var_of(compound) == (0, 0))
    check('a gate with no weapon test is refused',
          D.weapon_var_of(('or', [('var', 'FieldID', ('set', {502}), '502')]))
          is None)
    check('a gate naming two different weapons is refused',
          D.weapon_var_of(('or', [
              ('var', 'CloudWeapon', ('set', {1}), '1'),
              ('var', 'CloudWeapon', ('set', {2}), '2')])) is None)
    check('a weapon RANGE is refused -- it is not one variant',
          D.weapon_var_of(('var', 'CloudWeapon', ('range', (0, 5)), '0..5'))
          is None)
    check('None is refused', D.weapon_var_of(None) is None)


# -------------------------------------------------------- folder precedence

def test_folder_precedence():
    """7H's first-match order must be reversed for our last-write plan."""
    class Manifest:
        folders = [('plain-first', ''), ('conditional-first', ''),
                   ('plain-last', ''), ('conditional-last', '')]
        conditional_folders = {'conditional-first', 'conditional-last'}
        folder_conditions = {}

    got = iro.active_folders(Manifest(), {})
    check('folder precedence reverses declaration order within each class',
          got == ['plain-last', 'plain-first',
                  'conditional-last', 'conditional-first'])


# ---------------------------------------------------------- gap filling

class _G(D.Group):
    def __init__(self, char, slot, values):
        D.Group.__init__(self, 'char.lgp', 'aaae1.p', char)
        self.slot = slot
        self.by_value = {v: ('/src/%d' % v, None) for v in values}


def test_ranges():
    g = _G(0, 'a', range(16))
    out, base, count = D.plan_variants(g)
    check('a gap-free range emits one entry per value',
          (base, count, len(out)) == (0, 16, 16))
    check('every emitted name carries the SAME base and count',
          {n[4:7] for _v, n, _s, _m in out} == {'00f'})
    check('the value field counts up', [n[7:9] for _v, n, _s, _m in out]
          == ['%02x' % v for v in range(16)])

    g = _G(8, 'a', [73, 74, 76])            # 75 missing
    out, base, count = D.plan_variants(g)
    check('a gap is FILLED, not skipped -- the range stays contiguous',
          (base, count, len(out)) == (73, 4, 4))
    check('the filler is the nearest lower variant',
          [o[2] for o in out] == ['/src/73', '/src/74', '/src/74', '/src/76'])

    g = _G(0, 'a', [0, 20])
    try:
        D.plan_variants(g)
        check('a range wider than one hex digit is refused', False)
    except ValueError:
        check('a range wider than one hex digit is refused', True)


# ------------------------------------------------------- referrer rewrite

def test_repoint(tmp):
    man = [{'archive': 'char.lgp', 'entry': 'aaae1.p', 'stem': 'aaae1',
            'ext': 'p', 'base_stem': 'z00a00f00'},
           {'archive': 'char.lgp', 'entry': 'acjf.rsd', 'stem': 'acjf',
            'ext': 'rsd', 'base_stem': 'z01a20f20'}]
    rsd = (b'@RSD940102\r\nPLY=AAAE1.PLY\r\nMAT=AAAE1.MAT\r\n'
           b'GRP=AAAE1.GRP\r\nNTEX=1\r\nTEX[0]=cl.TIM\r\n')
    hrc = (b':HEADER_BLOCK 2\r\n:SKELETON x_sk\r\n:BONES 2\r\n\r\n'
           b'hip\r\nroot\r\n1.5\r\n2 ACJF ACGE\r\n\r\n'
           b'chest\r\nhip\r\n4.0\r\n1 ZZZZ\r\n')
    quiet = (b':HEADER_BLOCK 2\r\n:SKELETON y_sk\r\n:BONES 1\r\n\r\n'
             b'hip\r\nroot\r\n1.5\r\n1 QQQQ\r\n')
    van = {}
    for nm, blob in (('aaad1.rsd', rsd), ('acgd.hrc', hrc),
                     ('other.hrc', quiet)):
        p = os.path.join(tmp, nm)
        with open(p, 'wb') as f:
            f.write(blob)
        van[nm] = p
    out, n = D.repoint('char.lgp', {}, van, man, os.path.join(tmp, 'c'))
    check('only the referrers that name a dynamic part are rewritten', n == 2)
    got = open(out['aaad1.rsd'][0], 'rb').read()
    check('PLY is repointed at the range base', b'PLY=Z00A00F00.PLY' in got)
    check('MAT and GRP are left alone -- the loader does not read them',
          b'MAT=AAAE1.MAT' in got and b'GRP=AAAE1.GRP' in got)
    check('the rsd keeps its CRLFs', got.count(b'\r\n') == rsd.count(b'\r\n'))
    got = open(out['acgd.hrc'][0], 'rb').read()
    check('the bone token is replaced', b'2 Z01A20F20 ACGE' in got)
    check('the hrc keeps its CRLFs and its other tokens',
          got.count(b'\r\n') == hrc.count(b'\r\n') and b'1 ZZZZ' in got)
    check('an hrc that names nothing dynamic is not touched at all',
          'other.hrc' not in out or out['other.hrc'][0] == van['other.hrc'])


# ----------------------------------------------------- model file layering

def test_model_texture_layering(tmp):
    """Geometry coherence must not eat a later texture-only overlay."""
    def put(name, data):
        path = os.path.join(tmp, name)
        with open(path, 'wb') as f:
            f.write(data)
        return path

    hrc = put('aaaa.hrc', b':HEADER_BLOCK 2\r\n:BONES 1\r\n'
              b'root\r\nroot\r\n1.0\r\n1 AAAD1\r\n')
    rsd = put('aaad1.rsd', b'@RSD940102\r\nPLY=AAAE1.PLY\r\n'
              b'NTEX=1\r\nTEX[0]=AAAA1.TIM\r\n')
    mesh = put('aaae1.p', b'dynamic mesh')
    base_tex = put('aaaa1-base.tex', b'base sheet')
    fixes_tex = put('aaaa1-fixes.tex', b'fixes buster sheet')
    dynamic = object()
    fixes = object()
    plan = build.Plan()
    plan.archive_files['char.lgp'] = {
        'aaaa.hrc': (hrc, dynamic), 'aaad1.rsd': (rsd, dynamic),
        'aaae1.p': (mesh, dynamic), 'aaaa1.tex': (fixes_tex, fixes),
    }
    plan.folder_of['char.lgp'] = {
        'aaaa.hrc': 'dynamic weapons', 'aaad1.rsd': 'dynamic weapons',
        'aaae1.p': 'dynamic weapons', 'aaaa1.tex': 'Buster Sword Field Model',
    }
    versions = {'char.lgp': {
        'aaaa.hrc': [('dynamic weapons', hrc, dynamic)],
        'aaad1.rsd': [('dynamic weapons', rsd, dynamic)],
        'aaae1.p': [('dynamic weapons', mesh, dynamic)],
        'aaaa1.tex': [('dynamic weapons', base_tex, dynamic),
                      ('buster sword field model', fixes_tex, fixes)],
    }}
    build._assemble_models_atomically(plan, versions, lambda *_: None)
    check('a later Buster texture overlay survives dynamic model assembly',
          plan.archive_files['char.lgp']['aaaa1.tex'][0] == fixes_tex)


def test_dynamic_model_root(tmp):
    """A static companion HRC may not disconnect the dynamic RSD branch."""
    def put(name, data):
        path = os.path.join(tmp, name)
        with open(path, 'wb') as f:
            f.write(data)
        return path

    static = put('fixes-aaaa.hrc', b':HEADER_BLOCK 2\r\n:BONES 1\r\n'
                 b'root\r\nroot\r\n1.0\r\n1 AAAD\r\n')
    dynamic = put('chibi-aaaa.hrc', b':HEADER_BLOCK 2\r\n:BONES 1\r\n'
                  b'root\r\nroot\r\n1.0\r\n1 AAAD1\r\n')
    rsd = put('aaad1.rsd', b'@RSD940102\r\nPLY=AAAE1.PLY\r\n')
    base, fixes = object(), object()
    plan = build.Plan()
    plan.archive_files['char.lgp'] = {
        'aaaa.hrc': (static, fixes), 'aaad1.rsd': (rsd, base),
    }
    plan.folder_of['char.lgp'] = {}
    plan.dynweapon = [{'archive': 'char.lgp', 'entry': 'aaae1.p',
                       'stem': 'aaae1', 'ext': 'p'}]
    versions = {'char.lgp': {
        'aaaa.hrc': [('dynamic weapons', dynamic, base),
                     ('buster sword field model', static, fixes)],
    }}
    build._align_dynamic_model_roots(plan, versions, lambda *_: None)
    check('a static companion HRC yields to its compatible dynamic root',
          plan.archive_files['char.lgp']['aaaa.hrc'][0] == dynamic)
    check('the compatible root repair does not replace the selected RSD',
          plan.archive_files['char.lgp']['aaad1.rsd'][0] == rsd)


# ------------------------------------------------------------- encoders

def test_encoders():
    try:
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
    except ImportError:
        print('..: capstone not installed, encoder check skipped')
        return
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)

    def dis(w):
        for i in md.disasm(struct.pack('<I', w), 0):
            return (i.mnemonic + ' ' + i.op_str).replace(' ', '')
        return '??'

    for word, want in (
            (H.orr_imm32(9, 9, 0x20), 'orr w9, w9, #0x20'),
            (H.sub_imm(14, 9, 0x30), 'sub w14, w9, #0x30'),
            (H.subs_imm(4, 4, 1), 'subs w4, w4, #1'),
            (H.sub_reg(9, 4, 2), 'sub w9, w4, w2'),
            (H.csel(4, 4, 2, H.LS), 'csel w4, w4, w2, ls'),
            (H.mul(1, 1, 9), 'mul w1, w1, w9'),
            (H.orr_lsl(2, 3, 2, 4), 'orr w2, w3, w2, lsl #4'),
            (H.lsr_imm(5, 4, 4), 'lsr w5, w4, #4'),
            (H.cmp_imm64(2, 8), 'cmp x2, #8')):
        check('encoder: %s' % want, dis(word) == want.replace(' ', ''))


# ------------------------------------------------------------- the cave

def test_cave(main_path, exe_path):
    import nxmap
    import arm64emu as E

    m = nxmap.Main(main_path)
    patches, notes, problems = H.plan(m, exe_path)
    check('the hook site is found and the cave is placed', not problems
          and bool(patches))
    if problems or not patches:
        return
    code = {int(p['va'], 16): int.from_bytes(bytes.fromhex(p['set']),
                                             'little')
            for p in patches}
    hook = H.find_hook(m.text, *m.extent(H._open_file_x86(exe_path)))[0]
    imm = code[hook] & 0x03FFFFFF
    if imm & 0x02000000:
        imm -= 0x04000000
    entry = hook + imm * 4
    check('the hook replaces exactly one instruction, the rest is padding',
          sum(1 for va in code if va == hook) == 1)
    check('the cave fits in reclaimed padding, not the tail gap',
          all(va < 0x1152660 for va in code))
    print('    %s' % notes[0].strip())

    def run(name, weapons, ebp=0x1000, guest=0x900000):
        mem = E.Mem()
        cpu = E.Cpu(mem, paged=True)
        g = cpu.guest_to_host
        mem.write(g(guest), name.encode() + b'\0')
        for k, v in weapons.items():
            mem.setu(g(D.savemap_addr(k)), v, 1)
        cpu.x[22] = guest
        cpu.x[8] = ebp
        cpu.x[31] = 0x7FFF0000
        out = cpu.run(entry, code, max_steps=20000, start_pc=entry)
        got = bytes(mem.read(g(guest), 24)).split(b'\0')[0].decode('latin1')
        return out, got, cpu.x[0] & 0xFFFFFFFF

    for name, weap, want, label in (
            ('z00a00f00.p', {0: 5}, 'z00a00f05.p', 'Cloud, weapon 5'),
            ('z00a00f00.p', {0: 0}, 'z00a00f00.p', 'Cloud, weapon 0'),
            ('z00a00f00.p', {0: 15}, 'z00a00f0f.p', 'Cloud, top of range'),
            ('z00a00f00.rsd', {0: 5}, 'z00a00f05.rsd',
             'Cloud RSD, weapon 5'),
            ('z00a00f00.p', {0: 16}, 'z00a00f00.p', 'one past the top'),
            ('z00a00f00.p', {0: 99}, 'z00a00f00.p', 'not equipped at all'),
            ('z01b20f20.p', {1: 0x2f}, 'z01b20f2f.p', 'Barret, top'),
            ('z03a3ea3e.p', {3: 0x45}, 'z03a3ea45.p', 'Aerith, mid'),
            ('z08d49d49.p', {8: 0x56}, 'z08d49d56.p', 'Cid, top'),
            ('z08d49d49.p', {8: 0x57}, 'z08d49d49.p', 'Cid, past top'),
            ('Z00A00F00.P', {0: 3}, 'Z00A00F03.P', 'uppercase name'),
            ('aaae1.p', {0: 5}, 'aaae1.p', 'an ordinary part'),
            ('dwaa.p', {0: 5}, 'dwaa.p', "vanilla's own dw* entries"),
            ('flevel.siz', {0: 5}, 'flevel.siz', 'a non-model file'),
            ('z9', {0: 5}, 'z9', 'a name too short to be a variant')):
        out, got, w0 = run(name, weap)
        check('cave: %-22s %s -> %s' % (label, name, got),
              got == want and out == hook + 4 and w0 == (0x1000 - 0x198))

    _o, got, _w = run('z00a00f05.p', {0: 9})
    check('cave: rewriting an already-rewritten name is idempotent',
          got == 'z00a00f09.p')

    # Every character slot, and the savemap arithmetic that reaches it.
    bad = []
    for c in range(9):
        _o, got, _w = run('z0%da00f00.p' % c, {c: 7})
        if got != 'z0%da00f07.p' % c:
            bad.append((c, got))
    check('cave: all nine savemap characters resolve, %r' % (bad,), not bad)
    _o, got, _w = run('z09a00f00.p', {0: 7})
    check('cave: a character digit past 8 is refused', got == 'z09a00f00.p')


def main(argv):
    main_path = argv[0] if argv else os.path.join('dump', 'exefs', 'main')
    exe_path = argv[1] if len(argv) > 1 else os.path.join(
        'dump', 'romfs', 'ff7', 'resources', 'ff7_1.02', 'ff7_en')
    import tempfile
    test_savemap()
    test_names()
    test_gates()
    test_folder_precedence()
    test_ranges()
    with tempfile.TemporaryDirectory() as tmp:
        test_repoint(tmp)
        test_model_texture_layering(tmp)
        test_dynamic_model_root(tmp)
    test_encoders()
    if os.path.exists(main_path) and os.path.exists(exe_path):
        test_cave(main_path, exe_path)
    else:
        print('..: no game dump here, cave tests skipped '
              '(pass exefs/main and ff7_en)')
    print('')
    if FAILED:
        print('%d FAILED' % len(FAILED))
        for f in FAILED:
            print('  ' + f)
        return 1
    print('all good')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
