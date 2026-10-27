"""BUILD 708: who says each field line (ff7nx_speakers), the tag the field
carries, the lip flap's lookup of it (ff7nx_facial, in unicorn), and the
flower-basket Aerith who wears no weapon (ff7nx_dynweapon).

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
import os
import struct
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
pytest.importorskip('PyFF7.field')
import ff7nx_speakers as S                                     # noqa: E402

# ------------------------------------------------------------ a tiny field
_CH = {'\n': 0xE7, '“': 0xB2, '”': 0xB3, '"': 0x02, '(': 0x08, ')': 0x09}


def enc(text):
    out = bytearray()
    for ch in text:
        out.append(_CH[ch] if ch in _CH else ord(ch) - 0x20)
    return bytes(out) + b'\xff'


def field(actors, scripts, strings, akao=b''):
    """
    Section 1 with `actors` (names), `scripts` (actor -> list of opcodes for
    routine 0, each a bytes) and `strings` (bytes, already encoded).
    """
    n = len(actors)
    names = b''.join(a.encode().ljust(8, b'\0') for a in actors)
    akao_n = 1 if akao else 0
    head = 32 + 8 * n + 4 * akao_n
    code_at = head + 64 * n
    code, tables = bytearray(), []
    for i in range(n):
        tables.append(code_at + len(code))
        for op in scripts.get(i, []):
            code += op
        code += b'\x00'                                  # RET
    str_off = code_at + len(code)
    tab = bytearray(struct.pack('<H', len(strings)))
    data = bytearray()
    base = 2 + 2 * len(strings)
    for s in strings:
        tab += struct.pack('<H', base + len(data))
        data += s
    akao_at = str_off + len(tab) + len(data)
    hdr = struct.pack('<HBBHHH6x8s8s', 0x0502, n, 0, str_off, akao_n, 512,
                      b'', b'test')
    routines = b''.join(struct.pack('<32H', *([t] * 32)) for t in tables)
    sec = hdr + names + (struct.pack('<I', akao_at) if akao else b'') + \
        routines + bytes(code) + bytes(tab) + bytes(data) + akao
    assert len(hdr + names) + 4 * akao_n == head
    return sec


def char(model):
    return bytes((0xA1, model))


def mes(sid):
    return bytes((0x40, 0, sid))


LOADER = ['tmain_n_cloud.char', 'tmain_ballet.char',
          'tmidgal_avawoman.char', 'tmidgal_avafat.char']


def sample():
    actors = ['dir', 'cl', 'ba', 'jesy', 'weadge']
    strings = [
        enc('Run by holding CANCEL'),                       # 0 tutorial
        enc('Wedge\n"My ass is on fire!"'),                 # 1
        enc('Cait Sith\n“Cloud!”'),                         # 2 phone
        enc('Barret\n“Let\'s go!”'),                        # 3
        enc('“Hi there,Cloud!\n  It\'s me,Cait Sith.”'),     # 4
        enc('Cloud\n(A barrel.)'),                          # 5 thought
        enc('“Hey.”'),                                      # 6 unnamed
        enc('Cloud\n“Hm.”'),                                # 7 own line
    ]
    scripts = {
        0: [mes(3)],
        1: [char(0), mes(2), mes(4), mes(5), mes(7)],
        2: [char(1), mes(0), mes(6)],
        3: [char(2), mes(1)],
        4: [char(3)],
    }
    return field(actors, scripts, strings, akao=b'AKAO' + bytes(12))


def test_every_boundary_case_gets_the_right_speaker(monkeypatch):
    monkeypatch.setattr(S, 'model_loader_names', lambda _s: LOADER)
    values, report, _s = S.analyse(sample(), b'x', 't')
    v = {sid: val for sid, (val, _w) in values.items()}
    assert v[0] == S.V_NOBODY            # Barret's script, the narrator's line
    assert v[1] == 4 + 1                 # Jessie's script, Wedge's line
    assert v[2] == S.V_NOBODY            # Cait Sith on the phone, not here
    assert v[3] == 2 + 1                 # the director issues Barret's line
    assert v[4] == S.V_NOBODY            # "It's me, Cait Sith." from Cloud's
    assert v[5] == S.V_NOBODY            # a thought is not said aloud
    assert v[6] == S.V_DEFAULT           # an unnamed line stays the issuer's
    assert v[7] == S.V_DEFAULT           # Cloud's own line


def test_the_tag_rides_as_one_more_string_and_moves_nothing(monkeypatch):
    monkeypatch.setattr(S, 'model_loader_names', lambda _s: LOADER)
    sec = sample()
    out, stats = S.tag_section(sec, b'x', 't')
    assert stats['tagged']
    a, b = S.Script(sec), S.Script(out)
    assert b.count == a.count + 1
    assert [a.string(i) for i in range(a.count)] == \
        [b.string(i) for i in range(a.count)]
    tag = b.string(b.count - 1)
    assert tag[:4] == S.MAGIC and tag[-1] == 0xFF
    assert len(tag) == 4 + a.count + 1
    assert all(x < 0xE0 for x in tag[:-1])      # the codec reads it as text
    akao = struct.unpack_from('<I', out, b.akao_at)[0]
    assert out[akao:akao + 4] == b'AKAO'
    assert S.has_tag(out)
    again, st2 = S.tag_section(out, b'x', 't')
    assert again == out and st2.get('already')
    # code and its routine table did not move; only the AKAO offset did
    skip = slice(a.akao_at, a.akao_at + 4)
    assert out[:a.str_off][:skip.start] == sec[:a.str_off][:skip.start]
    assert out[skip.stop:a.str_off] == sec[skip.stop:a.str_off]


def test_a_field_that_agrees_with_the_runtime_gets_no_tag(monkeypatch):
    monkeypatch.setattr(S, 'model_loader_names', lambda _s: LOADER)
    sec = field(['cl'], {0: [char(0), mes(0)]}, [enc('Cloud\n“Hm.”')])
    out, stats = S.tag_section(sec, b'x', 't')
    assert out == sec and not stats['tagged']


def test_the_voice_table(monkeypatch):
    monkeypatch.setattr(S, 'model_loader_names',
                        lambda _s: ['tmain_n_cloud.char', 'tmodify_earithf.char',
                                    'tfarm_boy.char', 'tanother_choko.char'])
    monkeypatch.setitem(S.VOICES, 't', {
        'dic': 'aerith',                # actor-wide: fills nobody only
        '@Billy': 'gaki',               # a header: names the speaker outright
        'cloud#4': '-'})                # this string: nobody
    sec = field(['dic', 'cloud', 'earith', 'gaki', 'choko'],
                {0: [mes(0), mes(1), mes(2)], 1: [char(0), mes(4)],
                 2: [char(1)], 3: [char(2)], 4: [char(3), mes(3)]},
                [enc('“Excuse me.”'), enc('Obtained Flower!'),
                 enc('Cloud\n“Hm.”'), enc('Billy\n“Okay?”'),
                 enc('Cloud\n“...”')])
    v = {sid: val for sid, (val, _w) in S.analyse(sec, b'x', 't')[0].items()}
    assert v[0] == 2 + 1                 # the director's quote is Aerith's
    assert v[1] == S.V_DEFAULT           # narration stays nobody's
    assert v[2] == 1 + 1                 # a named line keeps its speaker
    assert v[3] == 3 + 1                 # not the chocobo that issued it
    assert v[4] == S.V_NOBODY            # '-' silences the issuer
    v = {sid: val for sid, (val, _w) in S.analyse(sec, b'x', 'u')[0].items()}
    assert v[0] == S.V_DEFAULT and v[3] == S.V_DEFAULT   # unlisted field


def test_runtime_default_models_build_613():
    s = None
    assert S.runtime_default(s, {0: 0}, 0, enc('Cloud\n“x”')) == 'issuer'
    assert S.runtime_default(s, {}, 0, enc('Cloud\n“x”')) == 'nobody'
    assert S.runtime_default(s, {0: 0}, 0, enc(' Type A alert')) == 'nobody'
    assert S.runtime_default(s, {0: 0}, 0, enc(' Yuffie\n“x”')) == 'issuer'


def test_identities():
    assert S.model_key('md8_1midgal_avafat.char', 'md8_1') == 'wedge'
    assert S.model_key('chrin_2shinra_reno2.char', 'chrin_2') == 'reno'
    assert S.entity_key('tuon', 'chrin_2shinra_reno2.char', 'chrin_2') == 'reno'
    assert S.entity_key('cl_hei', 'x_shinra_hei.char', 'x') == 'cloud'
    assert S.header_of('{RED XIII}\n“Grandpa…”') == ('Red XIII', 'hdr')
    assert S.header_of('?????\n“Oh…”') == ('?????', 'hdr')
    assert S.header_of('Obtained Potion!')[1] == 'none'


# -------------------------------------------- the real Echo-S, when present
ECHO = os.environ.get('SEVENTH_NX_TEST_ECHO_FIELDS', '')


@pytest.mark.skipif(not os.path.isdir(ECHO), reason='no Echo-S field dir')
@pytest.mark.parametrize('name, line, who', [
    ('md1_1', 'Run by holding', 'nobody'),
    ('md8_1', 'My ass is on fire', 'weadge'),
    ('kuro_9', 'Hi there,Cloud', 'ketcy'),
    ('md8_1', 'rendezvous on the train', 'balette'),
    ('tin_1', 'Type A security alert', 'nobody'),
    ('md8_2', 'Excuse me', 'earith'),
    ('md8_2', 'This is for you', 'earith'),
    ('frcyo', 'only just mated', 'gaki'),            # not the chocobos
    ('southmk2', 'Allow me to introduce', 'shinra'), # not the hovercraft
    ('snmayor', 'already decided', 'gusto2'),
    ('snmayor', 'You are SO selfish', 'ifarna'),
    ('snmayor', 'Don’t forget the child', 'yghojyo'),
    ('convil_1', 'not every day you see', 'jijii'),
    ('crcin_2', 'jockeys for the next race', 'nobody'),  # the announcer
    ('mrkt1', 'do something', 'fw1'),
    ('mrkt1', 'completely worthless', 'oldwm1'),
    ('ncoin3', 'live a little longer', 'oldm2'),
])
def test_the_reported_lines(name, line, who):
    import echo_s_flevel as E
    raw = open(os.path.join(ECHO, name), 'rb').read()
    secs, _t = E._sections_and_trailer(E._wrapped_field_raw(raw))
    s1, _ = E.strip_pc_speaker_prefixes(secs[0])
    s1, _ = E.remove_appended_pc_time_actor(s1)
    _v, report, sc = S.analyse(s1, secs[2], name)
    got = {(sc.actors[w] if w != 'nobody' else 'nobody')
           for _a, sid, w, _y in report if line in S.decode(sc.string(sid))}
    assert got == {who}


@pytest.mark.skipif(not os.path.isdir(ECHO), reason='no Echo-S field dir')
def test_md8_2_cloud_says_his_own_choices():
    """BUILD 734: the three menus Cloud answers Aerith with are the player's,
    not the director's -- which, since 733, meant Aerith's."""
    import echo_s_flevel as E
    raw = open(os.path.join(ECHO, 'md8_2'), 'rb').read()
    secs, _t = E._sections_and_trailer(E._wrapped_field_raw(raw))
    s1, _ = E.strip_pc_speaker_prefixes(secs[0])
    s1, _ = E.remove_appended_pc_time_actor(s1)
    v, _r, sc = S.analyse(s1, secs[2], 'md8_2')
    choices = {sid for sid, (val, _w) in v.items() if val == S.V_PLAYER}
    assert choices == {2, 7, 12}
    assert 'dangerous' in S.decode(sc.string(2))
    assert v[1][0] == sc.actors.index('earith') + 1   # her lines stay hers


# --------------------------------------------- the cave that reads the tag
unicorn = pytest.importorskip('unicorn')
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE   # noqa: E402
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X30, \
    UC_ARM64_REG_PC, UC_ARM64_REG_SP                           # noqa: E402
import ff7nx_facial as F                                       # noqa: E402

G = 0x2_0000_0000
BSS = 0x3FEC000
CAVE = 0x99F350
SECTION = 0x00A00000           # guest address of section 1
STACK = 0x1_9000_0000


def run_cave(sec, entity, models, ip):
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(0x900000, 0x200000)
    uc.mem_map(F.GUEST_TRANSLATE & ~0xFFF, 0x1000)
    uc.mem_map(F.CTX_PAGE, 0x1000)
    uc.mem_map(BSS, 0x4000)
    uc.mem_map(G + 0xCB0000, 0x60000)
    uc.mem_map(G + SECTION, 0x10000)
    uc.mem_map(STACK - 0x10000, 0x11000)
    uc.mem_write(F.CTX_PAGE + F.CTX_SLOT, struct.pack('<Q', 0x1_8000_0000))
    uc.mem_write(G + SECTION, sec)
    uc.mem_write(G + F.G_SCRIPT_CODE, struct.pack('<I', SECTION))
    uc.mem_write(G + F.G_ENTITY_ID, bytes((entity,)))
    uc.mem_write(G + F.G_MODEL_ID_ARRAY, bytes(models))
    uc.mem_write(G + F.G_SCRIPT_IP + 2 * entity, struct.pack('<H', ip))
    uc.mem_write(BSS + F.HDR_SPEAKER, struct.pack('<I', 0xAAAA))

    def tr(uc, addr, size, ud):
        uc.reg_write(UC_ARM64_REG_X0,
                     G + (uc.reg_read(UC_ARM64_REG_X0) & 0xFFFFFFFF))
        for r in range(1, 19):
            uc.reg_write(UC_ARM64_REG_X0 + r, 0xDEAD0000 + r)
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))
    uc.hook_add(UC_HOOK_CODE, tr, begin=F.GUEST_TRANSLATE,
                end=F.GUEST_TRANSLATE)
    words = F.build_speak_cave(CAVE, lambda i: CAVE + 4 * i, BSS)
    uc.mem_write(CAVE, b''.join(struct.pack('<I', w) for w in words))
    uc.reg_write(UC_ARM64_REG_SP, STACK - 0x200)
    uc.emu_start(CAVE, F.SPEAK_RESUME, count=100000)
    assert uc.reg_read(UC_ARM64_REG_SP) == STACK - 0x200 - 0x40   # the str x23
    return struct.unpack('<I', uc.mem_read(BSS + F.HDR_SPEAKER, 4))[0]


def _mes_ip(sec, actor, nth):
    """Offset of the `nth` MESSAGE in `actor`'s routine 0."""
    s = S.Script(sec)
    hits = [o for a, r, o in s.walk() if a == actor and r == 0 and
            sec[o] == 0x40]
    return hits[nth]


MODELS = [0xFF, 0, 1, 2, 3] + [0xFF] * 27


@pytest.mark.parametrize('actor, nth, want', [
    (2, 0, 0),           # md1_1: Barret's script, the tutorial: nobody
    (3, 0, 3 + 1),       # md8_1: Jessie's script, Wedge's model (3)
    (1, 0, 0),           # Cait Sith's call from Cloud's script: nobody
    (0, 0, 1 + 1),       # the director (no model) issues Barret's line
    (1, 3, 0 + 1),       # Cloud's own line, no opinion -> the 613 rule
    (2, 1, 1 + 1),       # an unnamed NPC line stays the issuer's
])
def test_the_cave_follows_the_tag(monkeypatch, actor, nth, want):
    monkeypatch.setattr(S, 'model_loader_names', lambda _s: LOADER)
    sec, _st = S.tag_section(sample(), b'x', 't')
    assert run_cave(sec, actor, MODELS, _mes_ip(sec, actor, nth)) == want


def test_no_tag_is_exactly_build_613():
    sec = sample()                                 # untagged
    assert run_cave(sec, 3, MODELS, _mes_ip(sec, 3, 0)) == 2 + 1
    assert run_cave(sec, 0, MODELS, _mes_ip(sec, 0, 0)) == 0  # no model
    alert = field(['ti'], {0: [char(0), mes(0), mes(1)]},
                  [bytes((0x00,)) + enc('Type A alert'),
                   bytes((0x00,)) + enc('Yuffie\n“x”')])
    assert run_cave(alert, 0, [0] + [0xFF] * 31, _mes_ip(alert, 0, 0)) == 0
    assert run_cave(alert, 0, [0] + [0xFF] * 31, _mes_ip(alert, 0, 1)) == 1


def test_a_last_string_without_the_magic_is_ignored(monkeypatch):
    monkeypatch.setattr(S, 'model_loader_names', lambda _s: LOADER)
    sec, _st = S.tag_section(sample(), b'x', 't')
    s = S.Script(sec)
    at = s.str_off + s.offsets[-1]
    bad = sec[:at] + b'\x33\x30\x2B\x12' + sec[at + 4:]
    assert run_cave(bad, 3, MODELS, _mes_ip(bad, 3, 0)) == 2 + 1   # 613 rule


def test_the_issuer_byte_overrides_the_leading_space_rule():
    sec = field(['ti'], {0: [char(0), mes(0)]},
                [bytes((0x00,)) + enc('Tifa hums')])
    blob = S.MAGIC + bytes((S.V_ISSUER,)) + b'\xff'
    tagged = S.append_string(sec, blob)
    assert run_cave(sec, 0, [0] + [0xFF] * 31, _mes_ip(sec, 0, 0)) == 0
    assert run_cave(tagged, 0, [0] + [0xFF] * 31,
                    _mes_ip(tagged, 0, 0)) == 1


# ------------------------------------------- md8_2's flower-basket Aerith
def test_cahc_loses_the_chibi_weapon_part(tmp_path):
    import ff7nx_dynweapon as D

    def hrc(chest):
        return (b':HEADER_BLOCK 2\r\n:SKELETON earithf_sk\r\n:BONES 2\r\n\r\n'
                b'hip\r\nroot\r\n2.68\r\n1 CAHD\r\n\r\n'
                b'chest\r\nhip\r\n4.74\r\n' + chest + b'\r\n\r\n')
    mod = tmp_path / 'mod.hrc'
    van = tmp_path / 'van.hrc'
    mod.write_bytes(hrc(b'2 CAHF CAHF1'))
    van.write_bytes(hrc(b'1 CAHF'))
    bucket = {'cahc.hrc': (str(mod), 'chibi'), 'aaaa.hrc': ('x', 'm')}
    out, n = D.strip_scene_weapons('char.lgp', bucket,
                                   {'cahc.hrc': str(van)}, str(tmp_path / 'c'))
    assert n == 1 and out['aaaa.hrc'] == ('x', 'm')
    assert open(out['cahc.hrc'][0], 'rb').read() == hrc(b'1 CAHF')
    # a model the mods leave vanilla is not touched
    out2, n2 = D.strip_scene_weapons('char.lgp', {}, {'cahc.hrc': str(van)},
                                     str(tmp_path / 'c'))
    assert n2 == 0 and out2 == {}
