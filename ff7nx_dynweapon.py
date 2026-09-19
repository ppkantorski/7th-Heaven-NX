#!/usr/bin/env python3
"""
ff7nx_dynweapon.py -- ship every Dynamic Weapons mesh and let the port pick.

WHAT 7TH HEAVEN DOES, AND WHY A STATIC BUILD CANNOT COPY IT
===========================================================
Dynamic Weapons is not a folder of art. It is sixteen folders that all
contain the SAME filenames -- `char/AAAE1.P`, `world/BCD1.P`, `high/BSCA1.p`
-- and a `<Conditional>` gate per folder that names the equipped weapon:

    <Conditional Folder="Dynamic Weapons\\Cloud\\03 - Hardedge" ... >
        <RuntimeVar Var="CloudWeapon" Values="2" />
    </Conditional>

7th Heaven resolves that gate against live process memory every time the game
opens a file (`AppWrapper/VFile.cs:292`), so the sixteen folders collapse to
one at the moment of the read. An SD card cannot: the archive has to hold one
byte sequence per name, forever.

WHAT THIS DOES INSTEAD
======================
Ship all of them, under names that carry their own decision, and let a hook
in the port rewrite the requested name at open time.

    z0 c s bb n vv . ext
    |  | |  |  | +---- the savemap value THIS variant is for, two hex digits
    |  | |  |  +------ count-1 of the range, one hex digit
    |  | |  +--------- first value of the range, two hex digits
    |  | +------------ part slot letter, unique within the character
    |  +-------------- savemap character index, 0..8
    +----------------- marker

`Dynamic Weapons/Cloud/03 - Hardedge/char/AAAE1.P` becomes `z00a00f02.p`, and
the `.rsd` that names AAAE1 is rewritten to ask for `z00a00f00` -- the range's
first member. The cave reads the character index out of the name, reads that
character's equipped weapon out of the savemap, clamps it into [base, base+n)
and overwrites the LAST two digits. See `ff7nx_dwhook`.

THE RANGE AND THE VALUE ARE SEPARATE FIELDS ON PURPOSE. They were one, and
the cave then had to copy the name into scratch before patching it, because
overwriting the only copy of `base` would make the next load read a range
that had moved. Keeping them apart makes the rewrite IDEMPOTENT -- base is at
[4:5] and never written, the live value is at [7:8] and always written -- so
the cave can patch the string where it lies. No scratch buffer, no copy loop,
and no chance of a 20-byte copy running off the end of a guest page.

The consequence is that the whole mapping lives in the filename: the port
needs no table of parts, no table of characters and no build-time agreement
about which mod supplied what. A part this pass does not understand simply
keeps its original name and behaves exactly as it does today.

WHY `z0`
========
MEASURED across every shipped and vanilla archive -- char, world_us,
high-us, battle, magic, flevel, menu, sub, chocobo, condor, coaster,
snowboard, 61,264 entries -- **no name begins with `z0`**, and none contains
a `~`. `dw`, the obvious marker, is not available: vanilla `char.lgp`
already holds `dwaa.p`, `dwab.rsd`, `dwac.p` and friends, and world_us.lgp
holds `dwa.rsd`. `emplace` re-checks the archive it is writing into and
refuses rather than colliding.

THE VALUES ARE THE MOD'S, NOT OURS
==================================
Nothing here parses a folder name. The weapon value comes from the
`<RuntimeVar>` the manifest declares, so a mod that renumbers its folders or
adds a weapon is followed automatically. The character index comes from
`VAR_CHAR`, which is checked against 7th Heaven's own `7thHeaven.var`
addresses by `test_dynweapon.py`.
"""
import os
import re
import struct

import iro

# savemap 0xDBFD38 + chars[9] at +0x54 + equipped_weapon at +0x1C, stride
# 0x84 (FFNx `struct savemap` / `struct savemap_char`). The addresses this
# produces are exactly 7th Heaven's own AppUI/Resources/7thHeaven.var:
#
#   CloudWeapon=Byte:0xDBFDA8   BarretWeapon=0xDBFE2C  TifaWeapon=0xDBFEB0
#   AerisWeapon=0xDBFF34        RedWeapon=0xDBFFB8     YuffieWeapon=0xDC003C
#   CaitSithWeapon=0xDC00C0     VincentWeapon=0xDC0144 CidWeapon=0xDC01C8
#
# `AppWrapper/Profile.cs` line 92 is a bare comment: "//Base: DBFD38".
SAVEMAP_BASE = 0xDBFD38
CHARS_OFFSET = 0x54
CHAR_STRIDE = 0x84
EQUIPPED_WEAPON = 0x1C

VAR_CHAR = {
    'cloudweapon': 0, 'barretweapon': 1, 'tifaweapon': 2,
    'aerisweapon': 3, 'redweapon': 4, 'yuffieweapon': 5,
    'caitsithweapon': 6, 'vincentweapon': 7, 'cidweapon': 8,
}

MARKER = 'z0'
MAX_SLOTS = 26          # 'a'..'z' part slots per character
MAX_RANGE = 16          # count-1 has to fit in one hex digit

# BARRET IS THE ONLY CHARACTER WHOSE .HRC HAS TO BE REWRITTEN.
# ===========================================================
# For everyone else the varying part is a `.p`, named by a `.rsd` that does
# NOT vary, so the only rewrite is a `PLY=` line and the cave sees a `.p`
# open. Measured in the built char.lgp:
#
#   Cloud  AAAA.HRC   16 bone tokens, longest 5, no z0 token
#   Barret ACGD.HRC   15 bone tokens, longest 9, token Z01A20F20
#
# Barret's `ACJF.RSD` differs between folders -- only in `TEX[0]`, BR.TIM for
# the Gatling Gun and SBAD.TIM for the other fifteen -- so the RSD itself
# became a variant, and his field model's bone token had to become a
# nine-character name. Vanilla char.lgp has 4,195 bone tokens and every one
# of them is four characters. Cloud, who works, never exercises this.
#
# `SEVENTH_NX_DW_HRC=static` leaves bone tokens alone: the `.hrc` keeps
# naming `ACJF`, that one static `.rsd` is still repointed at the `.p` range,
# and Barret then uses exactly the path Cloud's weapons already prove. The
# cost is that one texture serves all sixteen. It is a diagnostic first --
# if Barret's weapons change under it, the bone token was the fault.
HRC_MODE_ENV = 'SEVENTH_NX_DW_HRC'


def hrc_rewrite_enabled():
    """False when the build is asked to leave .hrc bone tokens alone."""
    return os.environ.get(HRC_MODE_ENV, '').strip().lower() != 'static'

_RE_PLY = re.compile(rb'(PLY\s*=\s*)([A-Za-z0-9_.]+)(\.\w+)', re.I)
# An .hrc bone line: "<n parts> TOKEN [TOKEN ...]", possibly with a trailing
# \r. The three groups keep the line's own spacing intact so a rewrite can
# put back exactly what it took.
_RE_BONE = re.compile(rb'^(\s*[1-9][0-9]*[ \t]+)([A-Za-z0-9_. \t]+?)([ \t]*\r?)$')
_RE_TOKEN = re.compile(rb'[A-Za-z0-9_.]+')


def weapon_var_of(cond):
    """
    (character index, value) if `cond` names exactly one equipped weapon.

    The test is on the WEAPON part of the condition only. Ninostyle Chibi
    writes the simple form:

        <RuntimeVar Var="CloudWeapon" Values="5" />

    but the Fixes mod's weapon-0 folder is an And of that with a Not of a
    list of FieldID and PPV tests -- "the Buster Sword, except in these
    scenes, where another folder supplies it". Requiring a bare var refused
    it, and Cloud's world sword came out with a range of 1..15: equipping the
    Buster Sword clamped to the Mythril Saber.

    The scene half cannot be honoured here at all -- FieldID and PPV change
    while a map is loaded, and this resolves once at load -- so it is
    ignored, and the folder is taken for the weapon it names. A condition
    with no weapon test, or with two different ones, returns None and the
    folder keeps the behaviour it has today.
    """
    found = set()

    def walk(node):
        if not node:
            return
        if node[0] == 'var':
            spec = (node[1] or '').strip().lower()
            if spec in VAR_CHAR:
                kind, want = node[2]
                if kind == 'set' and len(want) == 1:
                    found.add((VAR_CHAR[spec], next(iter(want))))
                else:
                    found.add((VAR_CHAR[spec], None))
            return
        for child in node[1]:
            walk(child)

    walk(cond)
    if len(found) != 1:
        return None
    char_index, value = next(iter(found))
    return None if value is None else (char_index, value)


def savemap_addr(char_index):
    return (SAVEMAP_BASE + CHARS_OFFSET + CHAR_STRIDE * char_index
            + EQUIPPED_WEAPON)


def variant_name(char_index, slot, base, count, value=None):
    """`z0` + char + slot + base(hex2) + (count-1)(hex1) + value(hex2)."""
    if value is None:
        value = base
    if not 0 <= char_index <= 8:
        raise ValueError('character index %d out of range' % char_index)
    for label, v in (('base', base), ('value', value)):
        if not 0 <= v <= 0xFF:
            raise ValueError('%s %d does not fit in two hex digits'
                             % (label, v))
    if not 1 <= count <= MAX_RANGE:
        raise ValueError('range of %d does not fit in one hex digit' % count)
    return '%s%d%s%02x%x%02x' % (MARKER, char_index, slot, base,
                                 count - 1, value)


# ---------------------------------------------------------------- collect

class Group:
    """One archive entry that several weapon folders disagree about."""

    def __init__(self, target, low, char_index):
        self.target = target
        self.low = low                  # 'aaae1.p'
        self.char = char_index
        self.by_value = {}              # value -> (src path, mod)
        self.slot = None

    @property
    def stem(self):
        return self.low.rsplit('.', 1)[0]

    @property
    def ext(self):
        return self.low.rsplit('.', 1)[1] if '.' in self.low else ''


def collect(plan, candidates, mods, log=lambda *_: None):
    """
    {(target, lowname): Group} for every per-weapon variant in the plan.

    `candidates` is build_plan's list with every target resolved. A file
    counts when the folder that selected it carries exactly one
    equipped-weapon RuntimeVar -- see `weapon_var_of`.

    THE VARIANT FOLLOWS THE PART, NOT THE FOLDER. A weapon folder like
    `Dynamic Weapons/Cloud/03 - Hardedge/char` holds nothing but new names --
    `AAAE1.P`, `AGCD1.p`, `BHJC1.p` are in no vanilla archive -- so it gets
    no name match, the folder-majority vote has nothing to count, and
    `_reroute_by_folder` finds no .hrc in it to follow. Those files route
    NOWHERE on their own.

    They do not have to. Every one of them is another copy of an entry the
    plan has already placed: whichever archive ended up holding `aaae1.p` is
    where its fifteen siblings belong. So the base name is looked up in
    `plan.archive_files` and the variant is filed beside it. A part whose
    base name is in no archive is skipped and said so out loud, because a
    variant with nowhere to go is a model that will not load.
    """
    where = {}
    for target, bucket in plan.archive_files.items():
        for low in bucket:
            where.setdefault(low, target)

    # PER MOD. Two mods declare the same folder names with DIFFERENT values:
    # Ninostyle Chibi numbers Cid 73..85 (and gives 81 to two folders), the
    # Fixes mod numbers the same fourteen 73..86. One shared dict keyed on
    # the folder string silently let the later mod's numbering overwrite the
    # earlier one's for BOTH mods' files, which lost two of Cid's weapons and
    # put a hole in the middle of his range.
    gate = {}
    for mod in mods:
        man = getattr(mod, 'manifest', None)
        if not man:
            continue
        g = {}
        for folder, cond in (man.folder_conditions or {}).items():
            hit = weapon_var_of(cond)
            if hit is not None:
                g[folder.replace('\\', '/').lower()] = hit
        gate[id(mod)] = g

    # Which archive an `archive subfolder` name means, voted across every
    # candidate in the plan that has a real name match.
    #
    # A weapon folder is all new names and routes nowhere by itself: no name
    # match, nothing for the folder-majority vote to count, and no .hrc in it
    # for _reroute_by_folder to follow. But `char`, `world` and `high` are
    # the mod set's own convention for "this piece belongs in char.lgp /
    # world_us.lgp / high-us.lgp", and thousands of OTHER files -- `fb/char`,
    # `fb/world`, `fb/high`, `Dynamic Weapons/Chibi/Char` -- say so by
    # matching. Voting over all of them is what distinguishes
    # `.../03 - Hardedge/char/BSCA1.p` from `.../03 - Hardedge/high/BSCA1.p`,
    # which are the same NAME in two different archives.
    tree_vote = {}
    for c in candidates:
        rel = c['rel'].replace('\\', '/').lower()
        if not c.get('direct'):
            continue
        leaf = os.path.basename(os.path.dirname(rel))
        tree_vote.setdefault(leaf, {}).setdefault(c['direct'], 0)
        tree_vote[leaf][c['direct']] += 1
    tree_target = {k: max(v, key=v.get) for k, v in tree_vote.items()}

    groups = {}
    homeless = set()
    for c in candidates:
        rel = c['rel'].replace('\\', '/').lower()
        # The selecting folder is a prefix of the file's path inside the mod.
        hit = None
        for folder, cv in gate.get(id(c['mod']), {}).items():
            if rel.startswith(folder + '/'):
                if hit is None or len(folder) > len(hit[0]):
                    hit = (folder, cv)
        if hit is None:
            continue
        # The tree's own subfolder vote comes FIRST. `bsca1.p` is shipped
        # both as `.../char/BSCA1.p` and `.../high/BSCA1.p` -- the same name
        # in two archives -- and a plan lookup keyed on the name alone sent
        # both to whichever one it found, leaving the Highwind deck copy
        # homeless.
        target = (tree_target.get(os.path.basename(os.path.dirname(rel)))
                  or where.get(c['low'])
                  or c.get('target'))
        if not target:
            homeless.add(c['low'])
            continue
        char_index, value = hit[1]
        key = (target, c['low'])
        g = groups.get(key)
        if g is None:
            g = groups[key] = Group(target, c['low'], char_index)
        elif g.char != char_index:
            log('  ! dynamic weapons: %s is claimed by two characters '
                '(%d and %d); left alone'
                % (c['low'], g.char, char_index))
            g.char = -1
        # Later mod wins, the same rule as every other collision here.
        g.by_value[value] = (c['full'], c['mod'])
    if homeless:
        log('  ! dynamic weapons: %d part name(s) have no archive because no '
            'enabled folder places the base entry (%s); they stay static'
            % (len(homeless), ', '.join(sorted(homeless)[:6])))

    # A part every weapon folder ships IDENTICALLY is not dynamic, whatever
    # its gate says. MEASURED: Barret's `acgd.hrc` is byte-identical across
    # all sixteen and Aerith's `rvac.tex` across both that carry it. Shipping
    # sixteen copies of one file and rewriting a referrer to reach them would
    # cost the archive real bytes and change nothing on screen.
    out, flat = {}, []
    for key, g in groups.items():
        if g.char < 0 or len(g.by_value) < 2:
            continue
        digests = {_digest(p) for p, _m in g.by_value.values()}
        if len(digests) < 2:
            flat.append(g.low)
            continue
        out[key] = g
    if flat:
        log('  dynamic weapons: %d part(s) are identical in every weapon '
            'folder and stay static (%s)'
            % (len(flat), ', '.join(sorted(flat))))
    return out


def _digest(path):
    import hashlib
    try:
        with open(path, 'rb') as f:
            return hashlib.sha1(f.read()).hexdigest()
    except OSError:
        return path


# ---------------------------------------------------------------- emplace

def assign_slots(groups, log=lambda *_: None):
    """Give every (character, entry) a stable letter. Sorted, so it is fixed."""
    per_char = {}
    for (target, low), g in sorted(groups.items()):
        per_char.setdefault(g.char, []).append(g)
    out = []
    for char_index, gs in sorted(per_char.items()):
        for i, g in enumerate(sorted(gs, key=lambda x: (x.target, x.low))):
            if i >= MAX_SLOTS:
                log('  ! dynamic weapons: character %d has more than %d '
                    'dynamic parts; %s left alone'
                    % (char_index, MAX_SLOTS, g.low))
                continue
            g.slot = chr(ord('a') + i)
            out.append(g)
    return out


def plan_variants(g, log=lambda *_: None):
    """
    [(value, name, src, mod)] over a GAP-FREE range, plus (base, count).

    The cave's in-range test is one unsigned compare, so the range it clamps
    into must have no holes. A value the mod does not ship -- Ninostyle Chibi
    numbers two of Cid's folders 81 and stops at 85, while the Fixes mod goes
    73..86 -- is filled with the nearest lower variant rather than left out,
    because a name the rewritten rsd can ask for and the archive does not
    hold is a model that fails to load.
    """
    values = sorted(g.by_value)
    base, top = values[0], values[-1]
    count = top - base + 1
    if count > MAX_RANGE:
        raise ValueError('%s spans %d values (max %d)'
                         % (g.low, count, MAX_RANGE))
    out = []
    last = g.by_value[base]
    filled = 0
    for v in range(base, top + 1):
        if v in g.by_value:
            last = g.by_value[v]
        else:
            filled += 1
        out.append((v, variant_name(g.char, g.slot, base, count, v)
                    + '.' + g.ext, last[0], last[1]))
    if filled:
        log('  dynamic weapons: %s has %d gap(s) in %d..%d; filled with the '
            'nearest lower variant' % (g.low, filled, base, top))
    return out, base, count


def repoint(target, bucket, vanilla, manifest, cache_dir,
            log=lambda *_: None):
    """
    Point every .rsd/.hrc that names a dynamic part at the range's base.

    A dynamic `.p` is named by an `.rsd` (`PLY=AAAE1.PLY`); a dynamic `.rsd`
    is named by a bone line in an `.hrc`. Both the mod's own copy and, if the
    mod does not replace it, the vanilla one are candidates -- the referrer
    has to be rewritten wherever it is coming from, so this runs at archive
    build time where the unpacked vanilla entries exist.

    Returns (new bucket, number of referrer entries rewritten).
    """
    want_p = {}                         # stem -> base variant stem
    want_rsd = {}
    for e in manifest:
        if e['archive'] != target:
            continue
        if e['ext'] == 'p':
            want_p[e['stem']] = e['base_stem']
        elif e['ext'] == 'rsd' and hrc_rewrite_enabled():
            want_rsd[e['stem']] = e['base_stem']
    if not (want_p or want_rsd):
        return bucket, 0

    bucket = dict(bucket)
    os.makedirs(cache_dir, exist_ok=True)
    names = set(bucket) | set(vanilla)
    done = 0
    for low in sorted(names):
        is_rsd = low.endswith('.rsd') and want_p
        is_hrc = low.endswith('.hrc') and want_rsd
        if not (is_rsd or is_hrc):
            continue
        src = bucket[low][0] if low in bucket else vanilla.get(low)
        if not src:
            continue
        try:
            with open(src, 'rb') as f:
                blob = f.read()
        except OSError:
            continue
        out = blob
        if is_rsd:
            def _ply(m):
                stem = m.group(2).decode('ascii', 'replace').lower()
                new = want_p.get(stem)
                return (m.group(1) + new.upper().encode() + m.group(3)
                        if new else m.group(0))
            out = _RE_PLY.sub(_ply, out)
        if is_hrc:
            # SURGICAL. Only the bone lines that actually name a dynamic rsd
            # are touched, and only the token itself is replaced -- the
            # line's own spacing and the file's line endings are left exactly
            # as they were.
            #
            # The first version split on '\n' after collapsing '\r\n',
            # rebuilt every bone line with single spaces and rejoined with
            # '\n'. It "rewrote" 1,000 .hrc files in char.lgp, none of which
            # mention a weapon, by stripping their CRLFs and retabbing them.
            def _bone(m):
                out_toks, hit = [], False
                for t in _RE_TOKEN.findall(m.group(2)):
                    new = want_rsd.get(t.decode('ascii', 'replace').lower())
                    if new is None:
                        out_toks.append(t)
                    else:
                        out_toks.append(new.upper().encode())
                        hit = True
                if not hit:
                    return m.group(0)
                body = m.group(2)
                for old, new in zip(_RE_TOKEN.findall(m.group(2)), out_toks):
                    if old != new:
                        body = re.sub(rb'\b' + re.escape(old) + rb'\b',
                                      new, body, count=1)
                return m.group(1) + body + m.group(3)
            out = b'\n'.join(_RE_BONE.sub(_bone, ln)
                             for ln in out.split(b'\n'))
        if out == blob:
            continue
        dest = os.path.join(cache_dir, low + '.dwref')
        tmp = dest + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(out)
        os.replace(tmp, dest)
        bucket[low] = (dest, bucket[low][1] if low in bucket else None)
        done += 1
    if done:
        log('  %s: dynamic weapons -- %d referrer(s) repointed at the '
            'weapon range' % (target, done))
    return bucket, done


def _majority_variant(variants):
    """
    (source, mod) of the content the most members of a range share.

    Ties break on the lowest value in the range, so a build is reproducible
    and a genuine 8/8 split still picks the range's first member.
    """
    counts = {}
    for value, _name, src, mod in variants:
        try:
            with open(src, 'rb') as handle:
                digest = handle.read()
        except OSError:
            continue
        entry = counts.setdefault(digest, [0, value, src, mod])
        entry[0] += 1
        entry[1] = min(entry[1], value)
    if not counts:
        return variants[0][2], variants[0][3]
    best = max(counts.values(), key=lambda e: (e[0], -e[1]))
    return best[2], best[3]


def emplace(plan, groups, catalogs, log=lambda *_: None):
    """
    Add every variant to its archive under its `z0...` name.

    Returns a manifest -- one dict per dynamic part -- which `repoint` uses
    at archive build time, `ff7nx_dwhook` logs a count from, and
    `verify_dynweapon.py` checks the packed archives against.
    """
    placed = assign_slots(groups, log)
    if not placed:
        return []

    if not hrc_rewrite_enabled():
        log('  dynamic weapons: %s=static -- .hrc bone tokens are left alone. '
            'Any part whose .rsd varies keeps ONE static .rsd (so one texture '
            'serves the whole range) and only its .p follows the equipped '
            'weapon. This is the path every other character already uses.'
            % HRC_MODE_ENV)
    per_archive = {}
    manifest = []
    for g in placed:
        try:
            variants, base, count = plan_variants(g, log)
        except ValueError as exc:
            log('  ! dynamic weapons: %s skipped -- %s' % (g.low, exc))
            continue
        base_stem = variant_name(g.char, g.slot, base, count)
        per_archive.setdefault(g.target, []).append((g, variants))
        manifest.append({
            'archive': g.target, 'entry': g.low, 'stem': g.stem,
            'ext': g.ext, 'base_stem': base_stem,
            'char': g.char, 'slot': g.slot, 'base': base, 'count': count,
            'addr': savemap_addr(g.char),
            'names': [n for _v, n, _s, _m in variants],
        })

    for target, items in sorted(per_archive.items()):
        bucket = plan.archive_files.setdefault(target, {})
        # The marker has to be unique in the archive we are writing into, not
        # merely in the archives measured when it was chosen.
        clash = sorted(n for n in
                       (set(bucket) | set(catalogs.get(target, ())))
                       if n.startswith(MARKER))
        if clash:
            log('  ! dynamic weapons: %s already has %d entr(y|ies) starting '
                '"%s" (e.g. %s) -- the marker is not unique in this archive, '
                'so NOTHING was emplaced anywhere'
                % (target, len(clash), MARKER, clash[0]))
            return []
        added = 0
        for g, variants in items:
            for _value, name, src, mod in variants:
                bucket[name] = (src, mod)
                plan.folder_of.setdefault(target, {})[name] = \
                    'Dynamic Weapons'
                added += 1
            # The base name keeps the range's first member, so a build with
            # the module patch turned off still renders a sensible weapon
            # rather than whichever folder happened to be emplaced last.
            bucket[g.low] = (variants[0][2], variants[0][3])
            if not hrc_rewrite_enabled() and g.ext == 'rsd':
                # In static mode this ONE .rsd serves the whole range, so the
                # range's first member is the wrong choice: for Barret it is
                # the Gatling Gun, the only arm of sixteen that names
                # `BR.TIM`, and the other fifteen then render with a texture
                # meant for something else. Take whichever content the most
                # weapons agree on -- fifteen right instead of one.
                bucket[g.low] = _majority_variant(variants)
        # CLOSURE. The cave can ask for any value in [base, base+count); a
        # name it asks for and the archive does not hold is a model part that
        # fails to load, with nothing on screen to say why. Every one of them
        # has to be present before the archive is packed.
        missing = [n for g, variants in items
                   for _v, n, _s, _m in variants if n not in bucket]
        if missing:
            log('  ! dynamic weapons: %d variant name(s) the rewritten '
                'referrers can ask for are not in %s (e.g. %s); NOTHING was '
                'emplaced' % (len(missing), target, missing[0]))
            return []
        log('  %s: dynamic weapons -- %d dynamic part(s), %d variant '
            'entr(y|ies) added' % (target, len(items), added))
    return manifest

