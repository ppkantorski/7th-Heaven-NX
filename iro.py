"""Reading 7th Heaven .iro archives and their mod.xml manifests."""
import lzma
import os
import re
import struct
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


def extract(src, dest, progress=None):
    """Extract an .iro. Skips files already present at the right size."""
    filesize = os.path.getsize(src)
    written = skipped = 0
    failures = []
    with open(src, 'rb') as f:
        _, _, entries = read_entries(f)
        for i, (name, eflags, offset, size) in enumerate(entries):
            if progress and i % 200 == 0:
                progress(i, len(entries))
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
    return written, skipped, failures


# ------------------------------------------------------------- manifest

# mod.xml files in the wild contain bare separator lines outside any tag,
# so they are not valid XML. Everything here is regex-based on purpose.

_TAG = r'<{0}\b[^>]*/?>'
_FIELD = re.compile(r'<(\w+)>([^<]*)</\1>')
_CONFIG = re.compile(r'<ConfigOption>(.*?)</ConfigOption>', re.S)
_OPTION = re.compile(r'<Option\s+([^>]*?)/?>')
_ATTRS = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
_FOLDER = re.compile(r'<(?:ModFolder|Conditional)\s+([^>]*?)/?>')


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


class Manifest:
    def __init__(self, path):
        with open(path, encoding='utf-8', errors='replace') as f:
            text = f.read()
        self.raw = text
        head = text[:text.find('<ConfigOption')] if '<ConfigOption' in text \
            else text
        fields = dict(_FIELD.findall(head))
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
            values = []
            for attrs in _OPTION.findall(block):
                a = dict(_ATTRS.findall(attrs))
                if 'Value' in a:
                    values.append((int(a['Value']), a.get('Name', a['Value'])))
            try:
                default = int(fields.get('Default', '0').strip())
            except ValueError:
                default = 0
            self.options.append(
                Option(ident, fields.get('Name', ident).strip(),
                       values, default))

        self.folders = []
        for attrs in _FOLDER.findall(text):
            a = dict(_ATTRS.findall(attrs))
            if 'Folder' in a:
                self.folders.append((a['Folder'].replace('\\', os.sep),
                                     a.get('ActiveWhen', '').strip()))

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
                label = folder.split(os.sep)[-1] or folder
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
        return {o.id: o.default for o in self.options}


_TERM = re.compile(r'^\s*(.+?)\s*(!=|=)\s*(\d+)\s*$')


def evaluate(condition, settings):
    """Evaluate an ActiveWhen expression such as 'Barret = 1'."""
    if not condition:
        return True
    text = condition.strip()
    if ' OR ' in text:
        return any(evaluate(p, settings) for p in text.split(' OR '))
    if ' AND ' in text:
        return all(evaluate(p, settings) for p in text.split(' AND '))
    text = text.strip('() ')
    m = _TERM.match(text)
    if not m:
        return False
    ident, op, value = m.group(1).strip(), m.group(2), int(m.group(3))
    current = settings.get(ident)
    if current is None:
        return False
    return current != value if op == '!=' else current == value


def active_folders(manifest, settings):
    """Folders whose ActiveWhen passes, in declaration order."""
    return [folder for folder, cond in manifest.folders
            if evaluate(cond, settings)]