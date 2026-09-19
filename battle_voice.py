"""Echo-S battle-action voice routing and staging.

The PC mod describes battle barks in ``Prob/<percent>/voice/config.toml``.
FFNx builds an exact key from actor kind/id, command id and action id, then
falls back to the command-only key. This module resolves that same table at
build time and gives the Switch runtime compact filenames it can format.

Physical attack grunts are deliberately absent here. They remain Cosmo
Memory SFX routes (``V_Attacks``/``NV_Attacks``); this layer is only Echo-S's
spell, summon, limit and enemy action speech.
"""
import gzip
import io
import os
import re
import shutil
import struct

try:
    import tomllib
except ImportError:  # pragma: no cover - retained for the old GUI runtime
    tomllib = None


ROUTE_RE = re.compile(
    r'^_battle-(char_([0-9a-f]{2})|enemy_([0-9a-f]{4}))-cmd_'
    r'([0-9a-f]{2})(?:_([0-9a-f]{4}))?$', re.I)
TEXT_ROUTE_RE = re.compile(
    r'^_battle-char_([0-9a-f]{2})-cmd_([0-9a-f]{2})_'
    r'(stole|couldnt|nothing)$', re.I)
DIALOGUE_ASSET_RE = re.compile(
    r'(?:^|/)voice/_battle/enemy_[0-9a-f]{4}/([^/]+)\.ogg$', re.I)

FF7_NORMAL_CHARS = (
    " !\"#$%&'()*+,-./01234"
    "56789:;<=>?@ABCDEFGHI"
    "JKLMNOPQRSTUVWXYZ[\\]^"
    "_`abcdefghijklmnopqrs"
    "tuvwxyz{|}~ ÄÅÇÉÑÖÜáà"
    "âäãåçéèêëíìîïñóòôöõúù"
    "ûü♥°¢£↔→♪ßα  ´¨≠ÆØ∞±≤"
    "≥¥µ∂ΣΠπ⌡ªºΩæø¿¡¬√ƒ≈∆«"
    "»… ÀÃÕŒœ–—“”‘’÷◊ÿŸ⁄ ‹"
    "›ﬁﬂ■‧‚„‰ÂÊÁËÈÍÎÏÌÓÔ Ò"
    "ÚÛÙıˆ˜¯˘˙˚¸˝˛ˇ       "
)


def compact_name(kind, actor, command, action=None):
    """Return the filename stem formatted by the ARM64 action hook."""
    suffix = 'ffff' if action is None else '%04x' % action
    if kind == 'char':
        return 'bc%02x%02x%s' % (actor, command, suffix)
    if kind == 'enemy':
        return 'be%04x%02x%s' % (actor, command, suffix)
    raise ValueError('unknown battle actor kind %r' % kind)


def message_hash(raw):
    """32-bit FNV-1a over one FF7 encoded string, terminator included.

    Including ``0xFF`` makes the build-time and injected loops share an
    unambiguous stopping byte. The runtime also caps the scan at 1024 bytes;
    scene messages are much smaller than that.
    """
    value = 0x811C9DC5
    for byte in raw[:1024]:
        value ^= byte
        value = (value * 0x01000193) & 0xFFFFFFFF
        if byte == 0xFF:
            return value
    raise ValueError('unterminated or overlong battle text')


def compact_dialogue_name(raw):
    return 'bd%08x' % message_hash(raw)


def compact_outcome_name(char_id, raw):
    return 'bo%02x%08x' % (char_id, message_hash(raw))


def _decode_ffnx_battle_text(raw):
    """Mirror FFNx ``decode_ff7_text`` for filename matching.

    Echo-S's scripted dialogue is literal text, but retaining FFNx's variable
    spellings makes the matcher useful for command-result strings too.
    """
    variables = {
        0xEB: '{item_name}', 0xEC: '{number}',
        0xED: '{target_name}', 0xEE: '{attack_name}',
        0xEF: '{special_number}', 0xF0: '{target_letter}',
    }
    character_names = (
        'Cloud', 'Barret', 'Tifa', 'Aerith', 'Red XIII', 'Yuffie',
        'Cait Sith', 'Vincent', 'Cid', 'Young Cloud', 'Sephiroth',
    )
    result = []
    index = 0
    while index < len(raw):
        current = raw[index]
        index += 1
        if current == 0xFF:
            break
        if current == 0xEA:
            # {CHAR bank,index}; Echo-S disables interactive renaming, so the
            # expanded runtime value is the canonical character name.
            if index + 1 >= len(raw):
                break
            char_id = raw[index + 1]
            result.append(character_names[char_id]
                          if char_id < len(character_names) else '')
            index += 2
        elif current in variables:
            result.append(variables[current])
            # Kernel variables carry two argument bytes. FFNx receives the
            # expanded string from get_kernel_text; skipping both here gives
            # the same visible/tokenized result for source matching.
            index += 2
        elif current == 0xF8:
            index += 1                 # colour byte is not rendered text
        elif current < len(FF7_NORMAL_CHARS):
            result.append(FF7_NORMAL_CHARS[current])
    return ''.join(result)


def _tokenize(text):
    return ''.join(c.lower() if c.isascii() and c.isalnum()
                   else '_' if c == ' '
                   else c if c in '{}'
                   else '' for c in text)


def _scene_messages(scene_path):
    """Yield raw ``0x93 MES`` strings from all 256 Echo-S battle scenes."""
    with open(scene_path, 'rb') as handle:
        archive = handle.read()
    for block_start in range(0, len(archive), 0x2000):
        block = archive[block_start:block_start + 0x2000]
        if len(block) != 0x2000:
            raise ValueError('scene.bin has a partial 0x2000-byte block')
        pointers = struct.unpack_from('<16I', block)
        offsets = [(pointer << 2) for pointer in pointers
                   if pointer != 0xFFFFFFFF]
        offsets.append(0x2000)
        for pos in range(len(offsets) - 1):
            packed = block[offsets[pos]:offsets[pos + 1]].rstrip(b'\xFF')
            if not packed:
                continue
            scene = gzip.GzipFile(fileobj=io.BytesIO(packed)).read(0x1E80)
            if len(scene) not in (0x1C50, 0x1E80):
                raise ValueError('unexpected decompressed scene size 0x%X'
                                 % len(scene))
            ai_offset = 0xC50 if len(scene) == 0x1C50 else 0xE80
            entity_offsets = struct.unpack_from('<3H', scene, ai_offset)
            for entity_index, entity_rel in enumerate(entity_offsets):
                if entity_rel == 0xFFFF:
                    continue
                table = ai_offset + entity_rel
                script_offsets = struct.unpack_from('<16H', scene, table)
                next_entity = len(scene)
                for later in entity_offsets[entity_index + 1:]:
                    if later != 0xFFFF:
                        next_entity = ai_offset + later
                        break
                for script_index, script_rel in enumerate(script_offsets):
                    if script_rel == 0xFFFF:
                        continue
                    start = table + script_rel
                    end = next_entity
                    for later in script_offsets[script_index + 1:]:
                        if later != 0xFFFF:
                            end = table + later
                            break
                    code = scene[start:end]
                    cursor = 0
                    while cursor < len(code):
                        opcode = code[cursor]
                        if opcode == 0x93:
                            finish = code.find(b'\xFF', cursor + 1)
                            if finish < 0:
                                break
                            yield bytes(code[cursor + 1:finish + 1])
                            cursor = finish + 1
                        elif opcode in (0x60,):
                            cursor += 2
                        elif opcode in (0x61,):
                            cursor += 3
                        elif opcode in (0x62,):
                            cursor += 4
                        elif opcode in (0x00, 0x01, 0x02, 0x03,
                                        0x10, 0x11, 0x12, 0x13,
                                        0x70, 0x71, 0x72):
                            cursor += 3
                        elif opcode == 0xA0:
                            finish = code.find(b'\0', cursor + 1)
                            cursor = len(code) if finish < 0 else finish + 1
                        else:
                            cursor += 1


def _kernel2_battle_strings(kernel2_path):
    """Return section 16 (battle.txt) as raw terminated strings."""
    import echo_s_kernel
    sections = echo_s_kernel._kernel2_sections(open(kernel2_path, 'rb').read())
    section = sections[16]
    offsets = struct.unpack_from('<128H', section)
    result = []
    for offset in offsets:
        finish = section.find(b'\xFF', offset)
        if finish < 0:
            raise ValueError('unterminated kernel battle string')
        result.append(section[offset:finish + 1])
    return result


def _asset_sources(assets):
    sources = {}
    for rel, full, _mod in assets:
        key = _asset_key(rel)
        if key:
            top = rel.replace('\\', '/').split('/', 1)[0].lower()
            priority = 1 if top == 'battle vo' else 0
            prior = sources.get(key)
            if prior is None or priority >= prior[0]:
                sources[key] = (priority, full)
    return {key: value[1] for key, value in sources.items()}


def parse_dialogues(scene_path, assets, log=None):
    """Resolve Echo-S enemy-dialogue filenames to raw-message hash keys."""
    wanted = {}
    for rel, full, _mod in assets:
        norm = rel.replace('\\', '/')
        match = DIALOGUE_ASSET_RE.search(norm)
        if match:
            wanted[match.group(1).lower()] = full
    messages = {}
    for raw in _scene_messages(scene_path):
        messages.setdefault(_tokenize(_decode_ffnx_battle_text(raw)), []).append(raw)
    routes = {}
    missing = []
    collisions = []
    for token, source in sorted(wanted.items()):
        candidates = messages.get(token, [])
        # A few Echo-S takes intentionally omit an on-screen speaker prefix
        # (currently Dyne's ``urgh``). FFNx's exact token would miss it; allow
        # a unique suffix only when no exact scene string exists.
        if not candidates:
            suffix = '_' + token
            suffixed = [raw for message_token, raws in messages.items()
                        if message_token.endswith(suffix) for raw in raws]
            if len(suffixed) == 1:
                candidates = suffixed
        if not candidates:
            missing.append(token)
            continue
        for raw in candidates:
            stem = compact_dialogue_name(raw)
            prior = routes.get(stem)
            if prior is not None and os.path.abspath(prior) != os.path.abspath(source):
                if open(prior, 'rb').read() != open(source, 'rb').read():
                    collisions.append((stem, prior, source))
                    continue
            routes[stem] = source
    if collisions:
        raise ValueError('battle dialogue hash/source collision: %r' %
                         (collisions[0],))
    if log:
        log('battle dialogue: %d/%d Echo-S clips matched scene text; %d missing'
            % (len(routes), len(wanted), len(missing)))
        for token in missing:
            log('  defer (dialogue text absent from selected scene.bin): %s'
                % token)
    return routes


def parse_outcomes(config_path, kernel2_path, assets, log=None):
    """Resolve FFNx's stole/couldnt/nothing command-result routes."""
    if tomllib is None:
        raise ValueError('Python 3.11+ tomllib is required for battle voice')
    config = tomllib.load(open(config_path, 'rb'))
    sources = _asset_sources(assets)
    texts = _kernel2_battle_strings(kernel2_path)
    by_word = {}
    for raw in texts:
        token = _tokenize(_decode_ffnx_battle_text(raw))
        for word in ('stole', 'couldnt', 'nothing'):
            if token.startswith(word):
                by_word.setdefault(word, []).append(raw)
    routes = {}
    missing = 0
    for heading, values in config.items():
        match = TEXT_ROUTE_RE.match(heading)
        if not match:
            continue
        char_id = int(match.group(1), 16)
        word = match.group(3).lower()
        source = None
        for choice in values.get('shuffle', []):
            source = sources.get(str(choice).replace('\\', '/').lower()
                                 .removesuffix('.ogg'))
            if source:
                break
        if source is None:
            missing += 1
            continue
        for raw in by_word.get(word, []):
            routes[compact_outcome_name(char_id, raw)] = source
    if log:
        log('battle command results: %d compact routes; %d missing sources'
            % (len(routes), missing))
    return routes


def _asset_key(rel):
    norm = rel.replace('\\', '/').lower()
    marker = '/voice/_battle/'
    wrapped = '/' + norm
    pos = wrapped.find(marker)
    if pos < 0 or not norm.endswith('.ogg'):
        return None
    tail = wrapped[pos + len('/voice/'):]
    return tail[:-4]


def parse(config_path, assets, log=None):
    """Resolve numeric FFNx battle routes to one real Echo-S source each."""
    if tomllib is None:
        raise ValueError('Python 3.11+ tomllib is required for battle voice')
    with open(config_path, 'rb') as handle:
        config = tomllib.load(handle)
    sources = _asset_sources(assets)

    routes = {}
    missing = 0
    ignored = 0
    for heading, values in config.items():
        match = ROUTE_RE.match(heading)
        if not match:
            ignored += 1
            continue
        kind = 'char' if match.group(2) is not None else 'enemy'
        actor = int(match.group(2) or match.group(3), 16)
        command = int(match.group(4), 16)
        action = int(match.group(5), 16) if match.group(5) else None
        choices = values.get('shuffle', []) if isinstance(values, dict) else []
        source = None
        for choice in choices:
            key = str(choice).replace('\\', '/').lower().removesuffix('.ogg')
            if '/silent/' in ('/' + key + '/'):
                continue
            source = sources.get(key)
            if source:
                break
        if source is None:
            missing += 1
            continue
        routes[compact_name(kind, actor, command, action)] = source
    if log:
        log('battle voice: %d numeric action/command routes resolved; %d '
            'missing-source and %d scripted-text routes deferred'
            % (len(routes), missing, ignored))
    return routes


def probability_from_config(config_path):
    """Read ``Prob/<n>/voice/config.toml``'s selected percentage."""
    parts = config_path.replace('\\', '/').split('/')
    for index, part in enumerate(parts[:-1]):
        if part.lower() == 'prob' and index + 1 < len(parts):
            value = parts[index + 1]
            if value.isdigit() and int(value) in (0, 25, 50, 75, 100):
                return int(value)
    raise ValueError('cannot derive battle voice probability from %s' % config_path)


def stage(routes, output_dir, writer, produced, log=None, verify_existing=None):
    """Normalize each unique source once and copy it to every compact route."""
    os.makedirs(output_dir, exist_ok=True)
    first_target = {}
    total = 0
    import voice_dat
    for stem, source in sorted(routes.items()):
        target = os.path.join(output_dir, stem + '.ogg')
        if os.path.isfile(target) and verify_existing is not None:
            try:
                size, _duration = verify_existing(target)
                produced.append(target)
                total += size
                continue
            except Exception:  # invalid/interrupted output is regenerated
                pass
        canonical = os.path.normcase(os.path.abspath(source))
        prior = first_target.get(canonical)
        if prior is None:
            entry = voice_dat.Entry(0, source_path=source)
            size, _duration = writer(entry, target)
            first_target[canonical] = target
            total += size
        else:
            shutil.copyfile(prior, target)
            total += os.path.getsize(target)
        produced.append(target)
    if log:
        log('battle voice: staged %d compact routes from %d unique takes '
            '(%.1f MB)' % (len(routes), len(first_target), total / 1048576.0))
    return len(routes)
