"""Reading 7th Heaven .iro archives and their mod.xml manifests."""
import lzma
import os
import re
import struct
import xml.etree.ElementTree as ET
import zlib

SIG = b'IROS'


# --------------------------------------------------------------- archive

def _lzma_filters(props):
    """Decode a 5-byte LZMA1 property header into a Python filter spec."""
    d = props[0]
    lc = d % 9
    d //= 9
    return [{
        'id': lzma.FILTER_LZMA1,
        'lc': lc,
        'lp': d % 5,
        'pb': d // 5,
        'dict_size': struct.unpack('<I', props[1:5])[0],
    }]


def _decompress_lzma(block):
    """
    LZMA entries carry a header ahead of the raw stream:
        uint32 uncompressed size, uint32 property length, properties, data
    The stream has no end marker, so output is bounded by the stated size.
    """
    if len(block) < 8:
        raise ValueError('block too short for an LZMA header')
    usize, plen = struct.unpack('<II', block[:8])
    if plen != 5 or len(block) < 8 + plen:
        raise ValueError(f'unexpected LZMA property length {plen}')
    dec = lzma.LZMADecompressor(format=lzma.FORMAT_RAW,
                                filters=_lzma_filters(block[8:8 + plen]))
    out = dec.decompress(block[8 + plen:], max_length=usize)
    if len(out) != usize:
        raise ValueError(f'decoded {len(out)} bytes, expected {usize}')
    return out


def read_entries(f):
    sig, version = struct.unpack('<4si', f.read(8))
    if sig != SIG:
        raise ValueError('not an IRO archive')
    flags = struct.unpack('<i', f.read(4))[0] if version >= 0x10001 else 0
    dir_offset, count = struct.unpack('<ii', f.read(8))
    f.seek(dir_offset + 4)
    entries = []
    for _ in range(count):
        length = struct.unpack('<H', f.read(2))[0]
        raw = f.read(length - 2)
        fnlen = struct.unpack('<H', raw[:2])[0]
        name = raw[2:2 + fnlen].decode('utf-16-le')
        rest = raw[2 + fnlen:]
        eflags = struct.unpack('<I', rest[:4])[0]
        offset, size = struct.unpack('<qi', rest[4:16])
        entries.append((name, eflags, offset, size))
    return version, flags, entries


def read_one(src, wanted):
    """Pull a single entry out of an .iro without extracting the rest."""
    filesize = os.path.getsize(src)
    target = wanted.lower().replace('\\', '/')
    with open(src, 'rb') as f:
        _, _, entries = read_entries(f)
        for name, eflags, offset, size in entries:
            if name.lower().replace('\\', '/') != target:
                continue
            f.seek(offset)
            data = f.read(min(size + 16, filesize - offset))
            if eflags == 1:
                return zlib.decompress(data)
            if eflags == 2:
                return _decompress_lzma(data)
            return data[:size]
    return None


def list_entries(src):
    """Just the entry paths inside an .iro (original case, as stored), read
    from the directory listing only -- no decompression, no disk writes.
    Cheap enough to call for every mod on UI startup."""
    with open(src, 'rb') as f:
        _, _, entries = read_entries(f)
    return [name for name, _eflags, _offset, _size in entries]


def extract(src, dest, progress=None, skip=None):
    """
    Extract an .iro. Skips files already present at the right size.

    `skip(name)` -> True excludes an entry BEFORE it is decompressed or
    written. It is there for payloads the build provably cannot use, where
    the cost of writing them out is not small: Cosmos Limit Break is 3.1 GB
    of which 3.0 GB is FFNx external textures this port has no loader for.
    The count of entries excluded that way is returned separately from
    `skipped` (which means "already on disk"), because the two want very
    different things said about them.
    """
    filesize = os.path.getsize(src)
    written = skipped = excluded = 0
    failures = []
    with open(src, 'rb') as f:
        _, _, entries = read_entries(f)
        for i, (name, eflags, offset, size) in enumerate(entries):
            if progress and i % 200 == 0:
                progress(i, len(entries))
            if skip is not None and skip(name):
                excluded += 1
                continue
            out = os.path.join(dest, name.replace('\\', os.sep))
            if eflags == 0 and os.path.exists(out) \
                    and os.path.getsize(out) == size:
                skipped += 1
                continue
            os.makedirs(os.path.dirname(out), exist_ok=True)
            f.seek(offset)
            data = f.read(min(size + 16, filesize - offset))
            try:
                if eflags == 1:
                    data = zlib.decompress(data)
                elif eflags == 2:
                    data = _decompress_lzma(data)
                else:
                    data = data[:size]
            except Exception as exc:
                failures.append((name, str(exc)))
                continue
            with open(out, 'wb') as o:
                o.write(data)
            written += 1
    return written, skipped, failures, excluded


# ------------------------------------------------------------- manifest

# mod.xml files in the wild contain bare separator lines outside any tag,
# so they are not valid XML. Everything here is regex-based on purpose.

_TAG = r'<{0}\b[^>]*/?>'
_FIELD = re.compile(r'<(\w+)>([^<]*)</\1>')
_CONFIG = re.compile(r'<ConfigOption>(.*?)</ConfigOption>', re.S)
_OPTION = re.compile(r'<Option\s+([^>]*?)/?>')
_ATTRS = re.compile(r'(\w+)\s*=\s*"([^"]*)"')

# A folder declaration is either self-closing:
#
#     <ModFolder Folder="X" ActiveWhen="opt = 1" />
#
# or a container whose gating sits in CHILD ELEMENTS rather than attributes:
#
#     <Conditional Folder="X">
#       <ActiveWhen><Or><Option>dwcloud = 1</Option></Or></ActiveWhen>
#       <Or><RuntimeVar Var="FieldID" Values="502" /></Or>
#     </Conditional>
#
# Matching only the opening tag -- which is what this used to do -- reads the
# second form as having no ActiveWhen at all. Ninostyle Chibi Fixes v2.5 has 25
# of them, and every one was being applied unconditionally with a meaningless
# synthesized On/Off toggle bolted onto it.
_FOLDER = re.compile(
    r'<(ModFolder|Conditional)\s+([^>]*?)(?:/>|>(.*?)</\1\s*>)', re.S)
_ACTIVEWHEN = re.compile(r'<ActiveWhen\s*>(.*?)</ActiveWhen\s*>', re.S)
_RUNTIMEVAR = re.compile(r'<RuntimeVar\s+([^>]*?)/?>')


def _flatten_activewhen(fragment):
    """
    Turn the nested <ActiveWhen> form into the same flat expression string the
    attribute form uses, so there is exactly one condition grammar downstream.

        <Or><Option>a = 1</Option><Option>b = 2</Option></Or>
            ->  '(a = 1 OR b = 2)'

    Returns None if the fragment contains something that cannot be decided at
    build time (a RuntimeVar), because "I could not evaluate this" and "this
    evaluated false" must not be the same answer.
    """
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring('<ActiveWhen>%s</ActiveWhen>' % fragment)
    except ET.ParseError:
        return None

    def walk(node):
        if node.tag == 'Option':
            return (node.text or '').strip() or None
        if node.tag in ('Or', 'And', 'ActiveWhen'):
            parts = [walk(c) for c in node]
            if not parts or any(p is None for p in parts):
                return None
            if len(parts) == 1:
                return parts[0]
            joiner = ' OR ' if node.tag == 'Or' else ' AND '
            return '(%s)' % joiner.join(parts)
        return None                       # RuntimeVar, Not, anything else

    return walk(root)


class Option:
    def __init__(self, ident, name, values, default):
        self.id = ident
        self.name = name
        self.values = values          # [(value:int, label:str), ...]
        self.default = default

    def label_for(self, value):
        for v, label in self.values:
            if v == value:
                return label
        return str(value)


# Shipping defaults that are wrong for a Switch SD build, keyed by the mod's
# own <ID> GUID so this can never land on a different mod. Each entry names the
# option, the value, and the reason -- these are visible in the GUI and can be
# changed there; they only decide what the FIRST run comes up with.
DEFAULT_OVERRIDES = {
    # Ninostyle Chibi Fixes and Additions
    '3b25060c-c60e-426e-ac46-f85479e983e5': {
        # 'Facial Animation' On pulls in 878 MB of re-eyed models built for
        # Shinra Archaeology Cut's Advanced Facial Animation. Without SAC in
        # the load order they are 878 MB that buys nothing.
        'fb': 1,
    },
}


class Manifest:
    def __init__(self, path):
        with open(path, encoding='utf-8', errors='replace') as f:
            text = f.read()
        self.raw = text
        head = text[:text.find('<ConfigOption')] if '<ConfigOption' in text \
            else text
        fields = dict(_FIELD.findall(head))
        self.mod_id = fields.get('ID', '').strip().lower()
        self.name = fields.get('Name', '').strip()
        self.author = fields.get('Author', '').strip()
        self.version = fields.get('Version', '').strip()
        self.category = fields.get('Category', '').strip()

        self.options = []
        for block in _CONFIG.findall(text):
            fields = dict(_FIELD.findall(block))
            ident = fields.get('ID', '').strip()
            if not ident:
                continue
            # A Bool ConfigOption conventionally ships its two <Option>s with
            # Name="" and lets the host draw a checkbox, so `a.get('Name', ...)`
            # returns an EMPTY STRING rather than falling back -- the default
            # only fires when the attribute is absent, and here it is present
            # and blank. Ninostyle Chibi Fixes' dwaeris/dwbarret/dwcid are all
            # Bool, which is why their dropdowns came up with two blank rows.
            kind = fields.get('Type', '').strip().lower()
            values = []
            for attrs in _OPTION.findall(block):
                a = dict(_ATTRS.findall(attrs))
                if 'Value' not in a:
                    continue
                value = int(a['Value'])
                label = a.get('Name', '').strip()
                if not label:
                    label = {0: 'Off', 1: 'On'}.get(value, str(value)) \
                        if kind == 'bool' else str(value)
                values.append((value, label))
            try:
                default = int(fields.get('Default', '0').strip())
            except ValueError:
                default = 0
            self.options.append(
                Option(ident, fields.get('Name', ident).strip(),
                       values, default))

        self.folders = []
        # folder -> [RuntimeVar names] for folders 7th Heaven switches LIVE
        # (equipped weapon, current field, story progress). A static SD build
        # cannot honour those, so they are recorded rather than evaluated and
        # the caller decides what to do about it.
        self.folder_runtime = {}
        # folder -> parsed RuntimeVar condition (see
        # parse_runtime_condition). Evaluated by the caller,
        # which is the only party that has the exe.
        self.folder_conditions = {}
        for _tag, attrs, body in _FOLDER.findall(text):
            a = dict(_ATTRS.findall(attrs))
            if 'Folder' not in a:
                continue
            folder = a['Folder'].replace('\\', os.sep)
            cond = a.get('ActiveWhen', '').strip()
            body = body or ''
            if not cond:
                inner = _ACTIVEWHEN.search(body)
                if inner:
                    cond = _flatten_activewhen(inner.group(1)) or ''
            rt = [dict(_ATTRS.findall(x)).get('Var')
                  for x in _RUNTIMEVAR.findall(body)]
            rt = sorted({v for v in rt if v})
            if rt:
                self.folder_runtime.setdefault(folder, [])
                self.folder_runtime[folder] = sorted(
                    set(self.folder_runtime[folder]) | set(rt))
            parsed = parse_runtime_condition(body)
            if parsed is not None:
                have = self.folder_conditions.get(folder)
                # One folder can be declared more than once with alternative
                # gates -- Wizard Staff declares NTIcon twice, once for each
                # New Threat build. 7th Heaven treats the repeats as
                # alternatives, so OR them together.
                self.folder_conditions[folder] = (
                    parsed if have is None else ('or', [have, parsed]))
            self.folders.append((folder, cond))

        # A Conditional nested inside a declared ModFolder inherits that
        # folder's gate when it has none of its own. Ninostyle Chibi Fixes
        # declares <ModFolder Folder="Buster Sword Only" ActiveWhen="dwcloud =
        # 2"/> and then <Conditional Folder="Buster Sword Only\Cloud\
        # HardEdgeMotorbikeStory"> gated only on RuntimeVars -- without
        # inheritance that child is unconditional, so a Buster-Sword-Only
        # folder would be emplaced with Dynamic Weapons switched off entirely.
        # Longest ancestor wins, so the most specific gate applies.
        gates = {f: c for f, c in self.folders if c}
        inherited = []
        for folder, cond in self.folders:
            if not cond:
                anc = [g for g in gates
                       if folder.startswith(g + os.sep)]
                if anc:
                    cond = gates[max(anc, key=len)]
            inherited.append((folder, cond))
        self.folders = inherited

        # Some mods ship folders with no ActiveWhen -- always applied, with no
        # option to turn them off (NinoStyle Battle's Enemies and Summons are
        # like this). Synthesize a toggle for each so the user can exclude
        # them. Default On, to stay faithful to how the mod ships.
        existing = {o.id for o in self.options}
        path_to_id = {}
        gated = []
        for folder, cond in self.folders:
            if cond:
                gated.append((folder, cond))
                continue
            if folder not in path_to_id:
                # Use the last TWO path components when there is a parent, so
                # a mod with six different "Field" folders does not produce six
                # toggles all called "Field".
                parts = [p for p in folder.split(os.sep) if p]
                label = ' / '.join(parts[-2:]) if len(parts) > 1 else \
                    (parts[-1] if parts else folder)
                ident = label
                n = 1
                while ident in existing:
                    ident = f'{label} {n}'
                    n += 1
                existing.add(ident)
                path_to_id[folder] = ident
                self.options.append(
                    Option(ident, label, [(0, 'Off'), (1, 'On')], 1))
            gated.append((folder, f'{path_to_id[folder]} = 1'))
        self.folders = gated

    def defaults(self):
        d = {o.id: o.default for o in self.options}
        d.update(DEFAULT_OVERRIDES.get(self.mod_id, {}))
        return d


_TERM = re.compile(r'^\s*(.+?)\s*(!=|=)\s*(\d+)\s*$')


def _split_top(text, sep):
    """Split on `sep`, but only where it is not inside parentheses."""
    parts, depth, start, i, n = [], 0, 0, 0, len(text)
    while i < n:
        c = text[i]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
        elif depth == 0 and text.startswith(sep, i):
            parts.append(text[start:i])
            i += len(sep)
            start = i
            continue
        i += 1
    parts.append(text[start:])
    return parts


def _unwrap(text):
    """Strip one fully-enclosing pair of parentheses, if there is one."""
    text = text.strip()
    while len(text) > 1 and text[0] == '(' and text[-1] == ')':
        depth = 0
        for i, c in enumerate(text):
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0 and i != len(text) - 1:
                    return text            # ')' closes early: not enclosing
        text = text[1:-1].strip()
    return text


def evaluate(condition, settings):
    """
    Evaluate an ActiveWhen expression such as 'Barret = 1'.

    Grouping matters now that _flatten_activewhen() emits parenthesised
    subexpressions: '(a = 1 OR b = 1) AND c = 2' must not be split on the OR
    that lives inside the group, and AND binds tighter than OR.
    """
    if not condition:
        return True
    text = _unwrap(condition)
    parts = _split_top(text, ' OR ')
    if len(parts) > 1:
        return any(evaluate(p, settings) for p in parts)
    parts = _split_top(text, ' AND ')
    if len(parts) > 1:
        return all(evaluate(p, settings) for p in parts)
    m = _TERM.match(text)
    if not m:
        return False
    ident, op, value = m.group(1).strip(), m.group(2), int(m.group(3))
    current = settings.get(ident)
    if current is None:
        return False
    return current != value if op == '!=' else current == value


# --------------------------------------------------------------------------
# <Conditional> RuntimeVar gates
#
# A `<Conditional Folder="X" ActiveWhen="opt = 1">` carries, as its BODY, a
# tree of `<RuntimeVar Var="Byte:0x914B1D" Values="00"/>` joined by
# `<And>/<Or>/<Not>`. 7th Heaven evaluates those against the RUNNING game's
# memory, which is how a mod detects that another mod is installed and serves
# a different set of files to suit it.
#
# [Tsunamods] Wizard Staff is the reason this exists here. It ships six
# variants of `kernel/kernel2.bin` -- MSN, "MSN DE", Icon, NT15, NT20,
# NTIcon -- all gated `ActiveWhen="Spells = 1"` and told apart ONLY by their
# RuntimeVars, which test for New Threat and for other Tsunamods UI mods.
# Ignoring the RuntimeVars makes all six active at once and the winner is
# whichever happens to be emplaced last: a New Threat spell table on a
# vanilla game.
#
# A static SD build has no running process, but these particular variables
# are BYTES IN THE EXE IMAGE -- the same flag block the HEXT patches and the
# 60 FPS compatibility flag live in -- so reading them out of the ff7 exe we
# are about to ship gives the same answer 7th Heaven would get on the first
# frame. That is exactly the question these gates are asking: which mods are
# installed. Variables that are not plain memory reads (Sys:, Counter:,
# Random:, and the live game state the equipped-weapon folders watch) cannot
# be answered and are reported as unknown, so the caller can leave those
# folders alone rather than guess.
#
# `Values` follows 7th Heaven's own parser: `0x`-prefixed is hex, everything
# else is DECIMAL, `a,b,c` is a set and `a..b` is an inclusive range.
_MEM_VAR = re.compile(r'^(Byte|Short|Int)\s*:\s*(0x[0-9A-Fa-f]+|\d+)$')


def _parse_values(text):
    def one(s):
        s = s.strip()
        return int(s[2:], 16) if s[:2].lower() == '0x' else int(s)
    if '..' in text:
        lo, hi = [one(p) for p in text.split('..') if p.strip()][:2]
        return ('range', (lo, hi))
    return ('set', {one(p) for p in text.split(',') if p.strip()})


def parse_runtime_condition(body):
    """
    The RuntimeVar tree of one <Conditional> body, or None if it has none.

    Shape: ('and'|'or'|'not', [children]) or ('var', spec, values, raw).
    """
    if not body or '<RuntimeVar' not in body:
        return None
    try:
        root = ET.fromstring('<r>%s</r>' % body)
    except ET.ParseError:
        return None

    def walk(node):
        tag = node.tag.lower()
        if tag == 'runtimevar':
            spec = (node.get('Var') or '').strip()
            raw = (node.get('Values') or '').strip()
            try:
                vals = _parse_values(raw)
            except ValueError:
                return None
            return ('var', spec, vals, raw)
        if tag in ('and', 'or', 'not'):
            kids = [k for k in (walk(c) for c in node) if k is not None]
            return (tag, kids) if kids else None
        return None

    kids = [k for k in (walk(c) for c in root) if k is not None]
    if not kids:
        return None
    return kids[0] if len(kids) == 1 else ('and', kids)


def runtime_vars(cond):
    """Every Var spec mentioned by a parsed condition."""
    if cond is None:
        return []
    if cond[0] == 'var':
        return [cond[1]]
    out = []
    for c in cond[1]:
        out += runtime_vars(c)
    return out


def evaluate_runtime(cond, read):
    """
    Evaluate a parsed RuntimeVar condition.

    `read(spec)` returns the current value of a `Byte:0x...` style variable,
    or None if it cannot be answered. Returns True, False, or None for
    "cannot tell" -- None propagates, so a condition that depends on even one
    unanswerable variable is unknown rather than quietly false.
    """
    if cond is None:
        return True
    if cond[0] == 'var':
        val = read(cond[1])
        if val is None:
            return None
        kind, want = cond[2]
        return (want[0] <= val <= want[1]) if kind == 'range' else (val in want)
    kids = [evaluate_runtime(c, read) for c in cond[1]]
    if cond[0] == 'not':
        return None if kids[0] is None else (not kids[0])
    if cond[0] == 'and':
        if any(k is False for k in kids):
            return False
        return None if any(k is None for k in kids) else True
    if any(k is True for k in kids):
        return True
    return None if any(k is None for k in kids) else False


def exe_var_reader(data, va_to_offset):
    """
    A `read` for evaluate_runtime backed by the ff7 exe image.

    Only `Byte:`, `Short:` and `Int:` at a literal address can be answered;
    anything else returns None.
    """
    sizes = {'byte': 1, 'short': 2, 'int': 4}

    def read(spec):
        m = _MEM_VAR.match(spec.strip())
        if not m:
            return None
        kind, addr = m.group(1).lower(), m.group(2)
        va = int(addr, 16) if addr[:2].lower() == '0x' else int(addr)
        try:
            off = va_to_offset(va)
        except Exception:                                      # noqa: BLE001
            return None
        n = sizes[kind]
        if off is None or off < 0 or off + n > len(data):
            return None
        return int.from_bytes(data[off:off + n], 'little')

    return read


def active_folders(manifest, settings, read=None, log=None):
    """
    Folders whose ActiveWhen passes, in declaration order.

    When `read` is given, a folder that also carries a RuntimeVar condition
    must satisfy it. A condition that cannot be answered (live game state, a
    variable outside the exe image) leaves the folder IN -- the previous
    behaviour -- because those gates pick between cosmetic variants far more
    often than they pick between right and wrong.
    """
    out = []
    for folder, cond in manifest.folders:
        if not evaluate(cond, settings):
            continue
        rt = manifest.folder_conditions.get(folder)
        if read is not None and rt is not None:
            ok = evaluate_runtime(rt, read)
            if ok is False:
                if log:
                    log('    %s: skipped, its RuntimeVar gate does not match '
                        'this build (%s)'
                        % (folder, ', '.join(sorted(set(runtime_vars(rt))))))
                continue
            if ok is None and log:
                log('    %s: RuntimeVar gate could not be evaluated (%s) -- '
                    'kept' % (folder, ', '.join(sorted(set(runtime_vars(rt))))))
        out.append(folder)
    return out