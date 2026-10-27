#!/usr/bin/env python3
"""
ff7nx_speakers.py -- who is actually saying each line of field dialogue.

BUILD 708. The lip flap (ff7nx_facial) moves the mouth of whoever the
MESSAGE opcode handler says opened the window: the entity whose script ran
the opcode. That is right for most lines and wrong in a way nobody can miss
for the rest, because FF7 does not issue a line from its speaker's script
when the speaker is busy, absent, or not a person:

    md1_1     the run tutorial is issued by Barret's script ('ba'), so
              Barret mouthed the narrator
    md8_1     "My ass is on fire!" is Wedge's, issued from Jessie's script
    ancnt2,   Cait Sith on the PHS: issued from Cloud's script, and Cloud
    loslake1  mouthed the whole call
    tin_1     the train's "Type A security alert" from Tifa's script (BUILD
              613 patched this one case with the leading-space rule)

Every one of those is answerable from the line itself. Echo-S writes the
speaker's name as a header on its own line -- ``Wedge\\n"My ass..."`` -- and
the field script says which entity is which person (its actor names and the
model each one loads). So the answer is computed HERE, at build time, from the
exact section 1 that ships, and the runtime only looks it up.

THE RULES, IN ORDER
===================
    no header, no quote         narration, a tutorial, "Obtained Potion!",
                                a sign, a menu: NOBODY
    header, then "(...)"        a thought: NOBODY -- nothing is said aloud
    quote with no header        an unnamed NPC: the issuing entity
    header names the issuer     the issuing entity
    header names another entity of this field that has a model
                                THAT entity (Wedge's line -> 'weadge')
    header names a known character with no model in this field
                                NOBODY (Cait Sith on the phone, Shera on the
                                radio, a letter read aloud)
    a minor header ("Guard") issued by a known character's script
                                NOBODY (Cloud's script voicing a tunnel
                                guard); otherwise the issuing entity

An entity is identified by its MODEL first (``main_n_cloud``, ``midgal_
avafat`` is Wedge) and its actor name second, and an entity neither names is
identified by the header of the lines its own script issues, when that is
unambiguous (tin_3's disguised 'avaw' is Jessie). Where one character has
several entities (a young and an adult Cloud), the one whose own script voices
that character most in the field is chosen.

HOW THE ANSWER TRAVELS
======================
As one more string in the field's own string table. Its index is the old
count, so no MESSAGE or ASK can name it, and every string the scripts DO name keeps
its index and bytes. The runtime reads the table's count, takes the last
entry, and trusts it only behind a 4-byte magic:

    'S' 'P' 'K' '1'   one byte per string id   0xFF

    0x00        no opinion: the runtime's own rule (BUILD 613)
    0x01..0xBF  entity + 1 -- that entity's model speaks
    0xC0        nobody
    0xC1        the issuing entity, even past the leading-space rule
    0xC2        the player's model (BUILD 734): an ASK's choices

Every byte is below 0xE0, which FF7's text codec reads as printable, so a
tool that decodes every string in the table sees text, not an error. A
field whose lines all agree with the runtime's own rule gets no tag at all.

The tag travels with the field. A field this pass did not see (no Echo-S, or
a field Echo-S does not ship) has no tag and keeps BUILD 613's behaviour.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import collections
import re
import struct

import pyff7_path                                           # noqa: F401
from PyFF7.field import instruction_size                    # noqa: E402

try:                                                        # pragma: no cover
    from PyFF7.text import decode_field_text as _decode
except Exception:                                           # noqa: BLE001
    _decode = None

MAGIC = bytes((0x33, 0x30, 0x2B, 0x11))     # "SPK1" in FF7's field codec
V_DEFAULT = 0x00
V_NOBODY = 0xC0
V_ISSUER = 0xC1
V_PLAYER = 0xC2
V_ENTITY_MAX = 0xBF
TERMINATOR = 0xFF

OP_MESSAGE = 0x40
OP_ASK = 0x48                               # +2 window, +3 dialog
CHOICE = 0xE0                               # {CHOICE}, the option tab
OP_CHAR = 0xA1
OP_RET = 0x00

HEADER = 32                                  # section 1's fixed header
ROUTINES = 32
MAX_MODELS = 32                              # what the runtime's slots hold
NAME_SCAN = 26                               # ff7nx_facial's leading-space scan
MAX_HEADER = 24                              # longest name header Echo-S writes

# key: (header names, actor names, model tokens). Model tokens are matched
# against the loader name with the field prefix removed ("main_n_cloud").
CHARS = {
    'cloud': (('cloud',),
              ('cl', 'cloud', 'cloud2', 'cl2', 'f_cloud', 'r_cloud', 'ycl',
               'ycloud', 'dcl', 'bcloud', 'cls', 'clh', 'cl_hei', 'n_cloud',
               'realclou', 'cloudswo'),
              ('cloud', 'n_cloud', 'cloudw', 'clouds', 'pcloud', 'cloudup')),
    'tifa': (('tifa',),
             ('ti', 'tifa', 'tifa2', 'tifa7', 'yti', 'n_tifa', 'tift',
              'tift2'),
             ('tifa', 'n_tifa', 'ptifa')),
    'barret': (('barret', 'barrett', 'barrret'),
               ('ba', 'ballet', 'balette', 'ballet2', 'ballete', 'barret',
                'barrett', 'cballet'),
               ('ballet', 'c_ballet')),
    'aerith': (('aerith', 'aeris'),
               ('ea', 'earith', 'earith2', 'fearith', 'aerith', 'aeris'),
               ('earith', 'n_earith')),
    'red': (('red xiii', 'nanaki', 'red'),
            ('red', 're', 'red13', 'rd', 'nanaki'),
            ('red',)),
    'yuffie': (('yuffie',), ('yufi', 'yuf', 'yufi2', 'yuffie'), ('yufi',)),
    'cait': (('cait sith',), ('ketcy', 'ket', 'cait'), ('ketcy',)),
    'vincent': (('vincent',), ('vincent', 'vin', 'vince'), ('vincent',)),
    'cid': (('cid',), ('cid', 'ycid'), ('cid', 'ysid')),
    'sephiroth': (('sephiroth',),
                  ('cef', 'cef2', 'cefi', 'cefi2', 'cefs', 'cefiros',
                   'cefirth', 'sefiro'),
                  ('sefiro', 'n_sefiro')),
    'zack': (('zack',), ('zax', 'zack'), ('zacks', 'lzacks')),
    'biggs': (('biggs',), ('bigs', 'biggs', 'big', 'av_b'), ('avaman',)),
    'jessie': (('jessie',), ('jesy', 'jessie', 'je', 'av_j'), ('avawoman',)),
    'wedge': (('wedge',), ('weadge', 'wedge', 'av_w'), ('avafat',)),
    'reno': (('reno',), ('reno',), ('reno',)),
    'rude': (('rude',), ('lude', 'rude'), ('lude',)),
    'elena': (('elena',), ('irena', 'elena'), ('irena',)),
    'tseng': (('tseng',), ('tuon', 'thuon', 'tseng'), ('tuon',)),
    'rufus': (('rufus',), ('lufas', 'lufus', 'rufus'), ('lufas',)),
    'heidegger': (('heidegger',), ('hyde', 'hyd', 'heidegger'), ('hyde',)),
    'scarlet': (('scarlet',), ('sca', 'scarlet', 'skaret'), ('scarlet',)),
    'hojo': (('hojo',), ('hojo',), ('hojyo',)),
    'palmer': (('palmer',), ('pal', 'palmer'), ('palmer',)),
    'bugenhagen': (('bugenhagen',), ('bugen', 'bugen2', 'boo'), ('boo',)),
    'corneo': (('don corneo', 'corneo'), ('colneo', 'corneo'), ('korneo',)),
    'dio': (('dio',), ('dio',), ('dio',)),
    'dyne': (('dyne',), ('dyn', 'dyne'), ('dainy',)),
    'marlene': (('marlene',), ('marin', 'marlene'), ('marine',)),
    'elmyra': (('elmyra',), ('emother', 'elmyra'), ('emother',)),
    'priscilla': (('priscilla',), ('prisl',), ('pricilla',)),
    'shera': (('shera',), ('siera', 'shera'), ('siera',)),
    'zangan': (('zangan',), ('zangan',), ('zangan',)),
    'ester': (('ester',), ('esto',), ('esto',)),
    'johnny': (('johnny',), ('johnny',), ()),
    'tsuna': (('tsuna',), ('tsuna',), ()),
}
# Whose absence means "nobody" even when the issuer is an unknown entity:
# the cast, whose lines from anyone else's script are never that entity's.
MAIN = frozenset(('cloud', 'tifa', 'barret', 'aerith', 'red', 'yuffie',
                  'cait', 'vincent', 'cid', 'biggs', 'jessie', 'wedge',
                  'sephiroth', 'zack'))
# A part played by whoever issues it (astage_b: the party plays the Princess).
ROLES = frozenset(('princess', '?????', 'party'))
# "It's me, Cait Sith." -- the speaker naming themself in a headerless line
SELF_INTRO = re.compile(r'[Ii]t[’\']s me,\s*([A-Z][A-Za-z]+(?: [A-Z][a-z]+)?)'
                        r'\s*[.!]')
NAME_VARS = {'{CLOUD}': 'Cloud', '{BARRET}': 'Barret', '{TIFA}': 'Tifa',
             '{AERITH}': 'Aerith', '{RED XIII}': 'Red XIII',
             '{YUFFIE}': 'Yuffie', '{CAIT SITH}': 'Cait Sith',
             '{VINCENT}': 'Vincent', '{CID}': 'Cid'}
# BUILD 733. Lines the rules above leave with NOBODY although a person on
# screen says them aloud: a modelless director issuing a shopkeeper's lines,
# a header ("Choco Billy", "Scotch") that names an NPC by a name the field's
# actor and model do not carry, an unnamed ????? who is in the room. Nothing
# in the field can answer these, so they are answered here, by reading each
# voiced line in its scene.
#
#   field -> {selector: speaker}
#
#   selector   'actor#sid'   that string, issued from that actor's script
#              '@Header'     every line whose header names this person
#              'actor'       every line that actor's script issues
#   speaker    an actor name of the field (or '#n', entity n), a CHARS key,
#              or '-' for nobody (a prop or an off-screen voice the rules
#              gave to whoever issued it)
#
# A string or header entry names the speaker outright, so it wins over the
# rules (frcyo: every chocobo's own script issues Choco Billy's lines, and
# the chocobo mouthed them). An actor-wide entry only fills a line the rules
# leave with NOBODY. Narration ("Obtained ...") and thoughts stay silent
# whatever the table says. Narrators, announcers, computers, TVs,
# letters, the party off screen and voices with no model stay NOBODY by
# simply not being listed.
VOICES = {
    'astage_b': {'@Knight': 'sol', '@Evil Dragon King': 'dragon'},
    'blin2_i': {'LINEC': 'URIKO'},
    'blin59': {'KLINEA': 'KEIBA', 'KLINEB': 'KEIBA'},  # one string
    'blin60_1': {a: 'WATCHA' for a in ('LINEU', 'LINED', 'OLINEA', 'OLINEB',
                 'OLINEC', 'OLINED', 'OLINEE', 'OLINEF', 'BUZ')},
    'blin66_5': {'@President Shinra': 'shin'},
    'blin68_2': {'dir#21': 're'},               # Red XIII, not yet named
    'blin70_4': {'event2': 'scarlet'},
    'bonevil2': {'keyc': '#14'},                # the dig foreman
    'bugin1c': {'MES': 'BUGEN'},
    'chrin_1b': {'dreamea': 'earith'},          # Aerith over the fallen Cloud
    'coloin1': {'lent1': 's2'},                  # the BP exchange
    'colne_1': {'MES': 'DOORMAN'},
    'colne_2': {'MES': 'CHECK'},
    'colne_3': {'@Scotch': 'SOTCH'},
    'colne_4': {'@Kotch': 'COTCH', 'AD#45': 'BUKA', 'AD#47': 'BUKA'},
    'colne_5': {'@Kotch': 'COTCH'},
    'convil_1': {'event': 'jijii'},
    'convil_2': {'event': 'mihari', 'event2': 'mihari', 'event3': 'mihari',
                 'itemget': 'mihari', 'line11': 'mihari'},
    'crcin_2': {'esto#19': '-'},                # the PA, not Ester
    # the two walking girls: the boxes alternate right and left, and the
    # one in front (18) is on the right of the one following her (20)
    'del2': dict([('talk#%d' % n, '#18') for n in
                  (18, 20, 22, 24, 32, 34, 36, 38, 39, 41, 43, 45)] +
                 [('talk#%d' % n, '#20') for n in
                  (19, 21, 23, 33, 35, 37, 40, 42, 44)]),
    'frcyo': {'@Choco Billy': 'gaki'},
    'frmin': {'@Choco Bill': 'jijii'},
    'fship_3': {'evt1': 'crew'},
    'gaiin_6': {'drctr': 'black'},
    'gldst': {'ev#0': 'man1'},
    'games': {'dic': 'cait'},                   # the fortune-telling machine
    'gldgate': {'dic': 'hito'},
    'gnmk': {'@PARTY': 'cloud'},
    'holu_1': {'drctr': 'holuzof'},
    'holu_2': {'drctr': 'holuzof'},
    'itmin2': {'out': 'baba'},
    'itown_w': {'line02': 'syonin'},
    'jail2': {"@Coates’ Lackey": 'goei1'},
    'jumin': {'ad#31': 'tifa'},                 # Tifa wakes him
    'jun_i2': {'border1': 'master', 'border2': 'master'},
    'junbin4': {'dir#13': 'hei0', 'dir#14': 'hei1'},
    'jundoc1a': {'mesAD': 'taityo'},
    'junin1': {'AD': 'hei1', 'AD2': 'hei1', 'AD3': 'hei1'},
    'junin1a': {'@Captain Rex': 'taityo', 'AD2': 'taityo', 'AD3': 'hei1'},
    'md8_2': {'dic': 'aerith'},                 # the flower girl meets Cloud
    'md8_52': {'dir1': 'man0', 'dir2': 'man1'},
    'md8brdg': {'@Sector 8 Unit': 'hei1'},
    'md8brdg2': {'line_rb#8': 'hyde', 'line_rb#9': 'scarlet'},
    'mds5_dk': {'@Scruffy Man': 'man'},
    'mds5_w': {'LINEB': 'kid'},                 # the boy playing trains
    'min51_2': {'BLINE': 'habitat', 'Kid#1': 'habitat'},  # he talks in his sleep
    # Big Bro is okama: the scene animates him before each of his lines
    'mkt_mens': dict([('event#%d' % n, 'okama') for n in
                      (9, 12, 16, 20, 21, 22, 23, 24, 25, 26, 28, 29, 30, 31,
                       32, 33, 34, 35, 36, 37, 38, 40, 42, 44, 46, 48)] +
                     [('event#%d' % n, 'aniki1') for n in (13, 27, 43, 47)] +
                     [('event#14', 'aniki2'), ('event#15', 'aniki2'),
                      ('event#17', 'aniki3')]),
    'mkt_s1': {'event#2': 'man1', 'event#4': 'man1', 'event#6': 'man1',
               'event#12': 'man1', 'event#16': 'man1', 'event#44': 'man1',
               'event#46': 'man1', 'event#23': 'oldm3', 'event#37': 'oldm3',
               'event#38': 'oldm3', 'event#39': 'oldm3', 'event#45': 'oldm3',
               'event#47': 'oldm3'},            # the son, and his father
    'mkt_s2': {'event': 'tenin', 'line01': 'tenin', 'line01#7': 'tensyu',
               'line00': 'tensyu'},
    'mkt_w': {'event': 'oyaji02'},
    'mktinn': {'event': 'syujinn', 'event2': 'syujinn'},
    # the trash gossips: top boxes for the upper woman, bottom for the lower
    'mrkt1': dict([('event#%d' % n, 'fw1') for n in (1, 3, 5, 7, 9, 11)] +
                  [('event#%d' % n, 'oldwm1') for n in (2, 4, 6, 8, 10)]),
    'mtcrl_2': {'@Shinra Troop': 'g_sol_1'},
    'ncoin3': {'ad#4': 'oldm2', 'ad#7': 'oldm2'},  # only his talk spot runs these
    'ncorel2': {'kamotu': 'boy1'},
    'niv_ti2': {'@PARTY': 'cloud'},
    'nvmkin22': {'@Sephiroth': 'Seph'},
    'nvmkin32': {'dir#3': 'cl2'},
    'onna_52': {'@Mukki': 'muki1'},             # not the dancer
    'onna_5': {'@Old Man': 'oldm', '@Old Woman': 'oldw'},
    'rcktbas1': {'@Captain': 'leader', '@Guard': 'hei1'},
    'rcktin2': {'crew1#3': 'cid'},              # Cid, not yet named
    'rktmin2': {'jisan#14': '-', 'jisan#15': '-'},  # the party's silence
    'ropest': {'ad': 'mogiri'},
    'sinin1_1': {'@Hojo’s Letter': '-'},        # read aloud, not by the prop
    'semkin_3': {'@Shinra Troop': 'g_sol_1'},
    'semkin_7': {'@Kix': 'g_sol_1'},
    'sininb2': {'dir#1': 'vin', 'dir#2': 'vin', 'dir#57': 'vin'},
    'sninn_1': {'@Innkeeper': 'innman1', 'ad#2': 'innman1',
                'gestm1r': 'gesm1', 'gestm1l': 'gesm1',
                'gestm2r': 'gesm2', 'gestm2l': 'gesm2'},
    'snmayor': {'ad2#11': 'gusto', 'ad2#71': 'gusto2', 'ad2#72': 'gusto2',
                'ad2#73': 'gusto2', 'ad2#74': 'gusto2', 'ad2#75': 'gusto2',
                'ad2#44': 'gusto2', 'ad2#48': 'gusto2',
                'ad2#76': 'ifarna', 'ad2#77': 'ifarna', 'ad2#78': 'ifarna',
                'ad2#79': 'ifarna', 'ad2#67': 'ifarna', 'ad2#58': 'ifarna',
                'ad2#59': 'yghojyo', 'ad2#66': 'yghojyo',
                'ad2#68': 'yghojyo', 'ad2#69': 'yghojyo'},  # the video tapes
    'southmk2': {'@President Shinra': 'shinra'},  # not the hovercraft
    'snw_w': {'@Shopkeeper': 'wepsp'},
    'subin_1b': {'direct#5': 'hei1'},
    'subin_2a': {'produce#1': 'hei2'},          # Fixer
    'tower5': {'@Gorky': 'goriki', '@Chekhov': 'tiehofu',
               '@Staniv': 'sutanif', 'exit#2': 'goriki', 'exit#3': 'goriki',
               'exit#4': 'goriki', 'exit#5': 'goriki'},
    'trnad_4': {'discver#15': 'black1'},        # Sephiroth, unmasked
    'tunnel_3': {'@Shinra Tunnel Guard': 'gard2'},
    'ujun_w': {'@Shopkeeper': 'wepsp'},
    'ujunon2': {'ad': 'prisl'},
    'ujunon4': {'ad': 'oldm1', 'counter': 'oldm1'},
    'utmin2': {'AD#9': 'MAGO'},
    'whitebg3': {'@Brian Lockhart': 'tpa'},
    'yougan2': {'BYE#2': 'YUFI'},
    'yougan': {'@Shinra Troop': 'SINRAA', 'AD#49': 'SINRAB'},
    'yougan3': {'@Shinra Troop': 'SINRAA', 'AD#49': 'SINRAB'},
    'zcoal_3': {'@Shinra Troop': 'soldier'},
    'zz1': {'l1': 'm1'},
    'zz4': {'l1': 'luk'},                       # Lucrecia
}


def voice_entry(field, actor, sid, name):
    """(speaker, sure) for this line, or (None, False). `sure` -- a string
    or a header entry -- names the speaker outright and overrides the rules;
    an actor-wide entry only fills a line the rules leave with nobody."""
    t = VOICES.get(field.lower())
    if not t:
        return None, False
    hit = t.get('%s#%d' % (actor, sid))
    if hit is None and name:
        low = name.lower()
        hit = next((v for k, v in t.items()
                    if k[:1] == '@' and k[1:].lower() == low), None)
    if hit is not None:
        return hit, True
    return t.get(actor), False


_HDR = {h: k for k, v in CHARS.items() for h in v[0]}
_ACT = {a: k for k, v in CHARS.items() for a in v[1]}
_MOD = {m: k for k, v in CHARS.items() for m in v[2]}


class SpeakerError(ValueError):
    pass


# ------------------------------------------------------------------ text
def decode(raw):
    if _decode is not None:
        try:
            return _decode(bytes(raw))
        except Exception:                                   # noqa: BLE001
            pass
    out = []
    for c in raw:                                           # letters, \n, quotes
        if c == 0xFF:
            break
        out.append('\n' if c == 0xE7 else
                   '“' if c == 0xB2 else '"' if c == 0x02 else
                   chr(c + 0x20) if c < 0x5F else '?')
    return ''.join(out)


def header_of(text):
    """(name, kind) with kind 'hdr', 'thought', 'quote' or 'none'."""
    for var, name in NAME_VARS.items():
        text = text.replace(var, name)
    text = re.sub(r'\{PARTY #\d\}', 'PARTY', text)
    c = re.sub(r'\{[^}]*\}', '', text).lstrip(' \t')
    c = re.sub(r'^[^A-Za-z“"‘(?\n]+', '', c)    # a stray marker glyph
    if c[:1] in '“"':
        # a line with no header that names its own speaker -- Cait Sith's
        # PHS call in kuro_9, issued from Cloud's script
        intro = SELF_INTRO.search(c)
        if intro:
            return intro.group(1), 'intro'
        return None, 'quote'
    if '\n' in c:
        head, rest = c.split('\n', 1)
        rest = rest.lstrip(' \t')
        name = head.strip().rstrip(':').strip()
        if len(head) <= MAX_HEADER and name:
            if rest[:1] in '“"‘':
                return name, 'hdr'
            if rest[:1] == '(':
                return name, 'thought'
    return None, 'none'


def header_key(name):
    n = re.sub(r'\s*\(.*\)$', '', name.lower()).strip()
    return _HDR.get(n)


def _letters(s):
    return re.sub(r'[^a-z]', '', s.lower())


def model_key(model, field=''):
    b = model.lower().rsplit('.', 1)[0]
    if field and b.startswith(field.lower()):
        b = b[len(field):]
    toks = b.split('_')
    for i in range(len(toks)):
        if '_'.join(toks[i:]) in _MOD:
            return _MOD['_'.join(toks[i:])]
    for t in toks:
        t = re.sub(r'\d+$', '', t)
        if t in _MOD:
            return _MOD[t]
    return None


def entity_key(actor, model, field=''):
    return (model_key(model, field) if model else None) or \
        _ACT.get(actor.lower())


# ------------------------------------------------------------ section 1
class Script:
    """The parts of a section 1 this pass reads, and nothing it rewrites."""

    def __init__(self, sec):
        sec = bytes(sec)
        if len(sec) < HEADER:
            raise SpeakerError('short field script section')
        self.sec = sec
        self.actors_n = sec[2]
        self.str_off = struct.unpack_from('<H', sec, 4)[0]
        self.akao_n = struct.unpack_from('<H', sec, 6)[0]
        names = HEADER
        self.actors = [sec[names + 8 * i:names + 8 * i + 8].split(b'\0')[0]
                       .decode('latin1') for i in range(self.actors_n)]
        self.akao_at = names + 8 * self.actors_n
        routines = self.akao_at + 4 * self.akao_n
        self.code_at = routines + 2 * ROUTINES * self.actors_n
        self.routines = [struct.unpack_from('<32H', sec, routines + 64 * i)
                         for i in range(self.actors_n)]
        if self.str_off + 2 > len(sec) or self.code_at > self.str_off:
            raise SpeakerError('field string table lies outside the section')
        self.count = struct.unpack_from('<H', sec, self.str_off)[0]
        tab = self.str_off + 2
        if tab + 2 * self.count > len(sec):
            raise SpeakerError('truncated field string offset table')
        self.offsets = list(struct.unpack_from('<%dH' % self.count, sec, tab))

    def string(self, sid):
        at = self.str_off + self.offsets[sid]
        end = self.sec.find(b'\xff', at)
        return self.sec[at:end + 1 if end >= 0 else len(self.sec)]

    def walk(self):
        """Yield (actor, routine, opcode offset) over every routine."""
        starts = sorted({e for r in self.routines for e in r
                         if self.code_at <= e < self.str_off})
        for ai, table in enumerate(self.routines):
            seen = set()
            for ri, entry in enumerate(table):
                if entry in seen or not self.code_at <= entry < self.str_off:
                    continue
                seen.add(entry)
                later = [s for s in starts if s > entry]
                end = later[0] if later else self.str_off
                o = entry
                while o < end:
                    try:
                        n = instruction_size(self.sec, o)
                    except Exception:                       # noqa: BLE001
                        break
                    if n <= 0:
                        break
                    yield ai, ri, o
                    o += n


def model_loader_names(sec3):
    """The loader's model names in index order ([] when unreadable)."""
    try:
        import echo_s_flevel
        return list(echo_s_flevel.model_loader_names(sec3))
    except Exception:                                       # noqa: BLE001
        return []


# --------------------------------------------------------------- analysis
def runtime_default(script, ent_model, issuer, raw):
    """What ff7nx_facial decides with no tag: 'issuer' or 'nobody'."""
    if ent_model.get(issuer, 0xFF) >= MAX_MODELS:
        return 'nobody'
    if not raw or raw[0] != 0x00:
        return 'issuer'
    for k in range(1, NAME_SCAN):
        if k >= len(raw) or raw[k] == 0xFF:
            return 'nobody'
        if raw[k] == 0xE7:
            return ('issuer' if k + 1 < len(raw) and raw[k + 1] == 0xB2
                    else 'nobody')
    return 'nobody'


def analyse(sec1, sec3=b'', field=''):
    """
    {sid: (value, why)} for every string a MESSAGE names, plus the lines.
    `value` is what the tag byte would be (V_DEFAULT when the runtime's own
    rule already gives the right speaker).
    """
    s = Script(sec1)
    models = model_loader_names(sec3) if sec3 else []
    ent_model, msgs, asks = {}, [], set()
    for ai, ri, o in s.walk():
        op = s.sec[o]
        if op == OP_CHAR and ri == 0:
            ent_model.setdefault(ai, s.sec[o + 1])
        elif op == OP_MESSAGE and s.sec[o + 2] < s.count:
            msgs.append((ai, s.sec[o + 2]))
        elif op == OP_ASK and s.sec[o + 3] < s.count:
            asks.add(s.sec[o + 3])

    def mname(e):
        m = ent_model.get(e)
        return models[m] if m is not None and m < len(models) else ''

    keys = {e: entity_key(s.actors[e], mname(e), field) for e in ent_model
            if ent_model[e] < MAX_MODELS}
    lines = {}
    for ai, sid in msgs:
        if sid not in lines:
            raw = s.string(sid)
            lines[sid] = (raw, header_of(decode(raw)))
    # an entity nothing names is who its own lines say it is
    votes = collections.defaultdict(collections.Counter)
    for ai, sid in msgs:
        name, kind = lines[sid][1]
        if ai in keys and keys[ai] is None and kind in ('hdr', 'thought'):
            k = header_key(name)
            if k:
                votes[ai][k] += 1
    taken = {v for v in keys.values() if v}
    for e, c in sorted(votes.items(),
                       key=lambda ec: (-ec[1].most_common(1)[0][1], ec[0])):
        k, n = c.most_common(1)[0]
        if k not in taken and 2 * n > sum(c.values()):
            keys[e] = k
            taken.add(k)
    own = collections.Counter()
    for ai, sid in msgs:
        name, kind = lines[sid][1]
        if kind == 'hdr' and ai in keys and keys[ai] and \
                header_key(name) == keys[ai]:
            own[ai] += 1

    def decide(ai, name, kind):
        issuer = ai if ai in keys else None
        dflt = 'issuer' if issuer is not None else 'nobody'
        if kind in ('none', 'thought'):
            return 'nobody', kind
        if kind == 'quote':
            return dflt, 'quote'
        if kind == 'intro':
            kind = 'hdr'
        hl = _letters(name)
        k = header_key(name)
        if issuer is not None and (hl and _letters(s.actors[ai]) == hl or
                                   k and keys[ai] == k):
            return 'issuer', 'self'
        if k is None:
            exact = [e for e in keys if hl and _letters(s.actors[e]) == hl]
            if exact:
                return exact[0], 'named'
            if name.lower() in ROLES:
                return dflt, 'role'
            if issuer is not None and keys[issuer] is not None:
                return 'nobody', 'minor voiced by a known character'
            return dflt, 'minor'
        cands = sorted((e for e, v in keys.items() if v == k),
                       key=lambda e: (-own[e], e))
        if cands:
            return cands[0], 'named'
        if k in MAIN or (issuer is not None and keys[issuer] is not None):
            return 'nobody', 'absent'
        return dflt, 'absent minor'

    def voiced(ai, sid, name):
        target, sure = voice_entry(field, s.actors[ai], sid, name)
        if target is None:
            return None, False
        if target == '-':
            return 'nobody', sure
        if target.startswith('#'):
            e = int(target[1:])
            return (e if e in keys else None), sure
        exact = [e for e in sorted(keys) if s.actors[e] == target]
        if exact:
            return exact[0], sure
        cands = sorted((e for e, v in keys.items() if v == target),
                       key=lambda e: (-own[e], e))
        return (cands[0] if cands else None), sure

    per_sid = collections.defaultdict(set)
    report = []
    for ai, sid in msgs:
        raw, (name, kind) = lines[sid]
        who, why = decide(ai, name, kind)
        if kind not in ('none', 'thought'):
            heard, sure = voiced(ai, sid, name)
            if heard is not None and (sure or who == 'nobody'):
                who, why = heard, 'voice table'
        if who == 'issuer':
            who = ai
        default = runtime_default(s, ent_model, ai, raw)
        default = ai if default == 'issuer' else 'nobody'
        per_sid[sid].add((ai, who, default))
        report.append((ai, sid, who, why))

    out = {}
    for sid, uses in per_sid.items():
        if all(who == dflt for _ai, who, dflt in uses):
            out[sid] = (V_DEFAULT, 'agrees')
            continue
        whos = {who for _ai, who, _d in uses}
        if whos == {'nobody'}:
            out[sid] = (V_NOBODY, 'nobody')
        elif len(whos) == 1 and isinstance(next(iter(whos)), int) and \
                next(iter(whos)) + 1 <= V_ENTITY_MAX:
            out[sid] = (next(iter(whos)) + 1, 'entity')
        elif all(who == ai for ai, who, _d in uses):
            out[sid] = (V_ISSUER, 'issuer')
        else:
            out[sid] = (V_DEFAULT, 'conflict')

    # BUILD 734: an ASK that is only options ("{CHOICE}It's dangerous...")
    # is the player choosing, and Echo-S voices each option in the player's
    # voice (md8_2's 2_0/2_1). Every voiced ASK in the game is one of these.
    # An ASK with a question first is left to the rule; none is voiced but
    # cosin1_1's, which is a MESSAGE too and keeps that line's answer.
    for sid in sorted(asks - set(out)):
        if s.string(sid).lstrip(b'\x00')[:1] == bytes((CHOICE,)):
            out[sid] = (V_PLAYER, 'choice')
    return out, report, s


def tag_bytes(values, count):
    """The tag string for a table of `count` strings, or None if not needed."""
    body = bytearray(count)
    for sid, (v, _why) in values.items():
        if sid < count:
            body[sid] = v
    if not any(body):
        return None
    return MAGIC + bytes(body) + bytes((TERMINATOR,))


def has_tag(sec1):
    try:
        s = Script(sec1)
    except SpeakerError:
        return False
    return s.count >= 1 and s.string(s.count - 1).startswith(MAGIC)


def append_string(sec1, blob):
    """
    Section 1 with `blob` added as string number `count`. The offset table
    grows by one entry, so every string moves 2 bytes and every AKAO offset
    past the table moves by 2 + len(blob) (the blob sits at the end of the
    string area, before the first AKAO block).
    """
    s = Script(sec1)
    sec = s.sec
    tab = s.str_off + 2
    akao = [struct.unpack_from('<I', sec, s.akao_at + 4 * i)[0]
            for i in range(s.akao_n)]
    strings_end = min([a for a in akao if a > s.str_off] or [len(sec)])
    data_at = tab + 2 * s.count
    new_off = strings_end - s.str_off + 2
    if new_off > 0xFFFF:
        raise SpeakerError('string table would pass 64 KB')
    out = bytearray(sec[:s.str_off])
    out += struct.pack('<H', s.count + 1)
    out += struct.pack('<%dH' % s.count, *[o + 2 for o in s.offsets])
    out += struct.pack('<H', new_off)
    out += sec[data_at:strings_end]
    out += blob
    out += sec[strings_end:]
    grow = 2 + len(blob)
    for i, a in enumerate(akao):
        if a > s.str_off:
            struct.pack_into('<I', out, s.akao_at + 4 * i, a + grow)
    return bytes(out)


def tag_section(sec1, sec3=b'', field=''):
    """(section 1, stats). Unchanged, with stats['tagged'] False, if no line
    needs a speaker the runtime would not already pick."""
    if has_tag(sec1):
        return bytes(sec1), {'tagged': False, 'already': True}
    values, report, s = analyse(sec1, sec3, field)
    blob = tag_bytes(values, s.count)
    stats = collections.Counter(why for _v, why in values.values())
    stats = {'tagged': blob is not None, 'lines': dict(stats),
             'changed': sum(1 for v, _w in values.values() if v)}
    if blob is None:
        return bytes(sec1), stats
    return append_string(sec1, blob), stats
