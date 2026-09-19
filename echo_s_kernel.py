"""Structured Echo-S kernel merge for Switch language data.

PC and Switch kernel files are compressed containers.  Copying Echo-S's PC
files over Switch files discards the Switch container layout.  We therefore
replace only the decoded logical sections Echo-S changes, then rebuild a
valid Switch-format container.  ``window.bin`` is intentionally not a normal
kernel input: it carries PC font/width data and remains Switch stock.
"""
from __future__ import annotations

import gzip
import os
import struct

import lgp


class EchoKernelError(ValueError):
    pass


# KERNEL.BIN section 5 is the 128-entry weapon table.  Echo-S's PC
# "Vanilla" compatibility kernel changes byte 36 (normal-hit SFX) on the
# Barret and Vincent firearms so FFNx can route them through old numeric
# replacements.  The Switch port uses Cosmo Memory's modern character-aware
# battle_char routes instead, whose native requests come from the stock bytes.
# Preserve just that byte while still importing every other Echo-S weapon
# property from the section.
_WEAPON_SECTION_KIND = 5
_WEAPON_RECORD_SIZE = 44
_WEAPON_RECORD_COUNT = 128
_WEAPON_NORMAL_HIT_SFX_OFFSET = 36


def _preserve_switch_weapon_sfx(stock: bytes, echo: bytes):
    """Return Echo's weapon table with stock normal-hit SFX bytes restored."""
    expected = _WEAPON_RECORD_SIZE * _WEAPON_RECORD_COUNT
    if len(stock) != expected or len(echo) != expected:
        raise EchoKernelError('weapon section has unexpected size (%d vs %d; '
                              'expected %d)' % (len(stock), len(echo), expected))
    merged = bytearray(echo)
    restored = []
    for weapon_id in range(_WEAPON_RECORD_COUNT):
        offset = (weapon_id * _WEAPON_RECORD_SIZE
                  + _WEAPON_NORMAL_HIT_SFX_OFFSET)
        if merged[offset] != stock[offset]:
            restored.append((weapon_id, merged[offset], stock[offset]))
            merged[offset] = stock[offset]
    return bytes(merged), tuple(restored)


def _gzip_sections(data: bytes):
    """Decode the counted gzip records used by KERNEL.BIN and WINDOW.BIN."""
    result = []
    offset = 0
    while offset < len(data):
        # KERNEL.BIN and WINDOW.BIN end their record stream with a u16 zero
        # sentinel.  It is not a sixth-byte section header.
        if data[offset:] == b'\0\0':
            return result, b'\0\0'
        if offset + 6 > len(data):
            raise EchoKernelError('truncated gzip section header')
        packed, unpacked, kind = struct.unpack_from('<HHH', data, offset)
        offset += 6
        end = offset + packed
        if end > len(data):
            raise EchoKernelError('truncated gzip section payload')
        raw = gzip.decompress(data[offset:end])
        if len(raw) != unpacked:
            raise EchoKernelError('gzip section length mismatch')
        result.append((kind, raw))
        offset = end
    if not result:
        raise EchoKernelError('kernel has no gzip sections')
    return result, b''


def _pack_gzip_sections(sections, suffix=b''):
    result = bytearray()
    for kind, raw in sections:
        # mtime=0 makes builds reproducible; the game reads the gzip payload,
        # not the timestamp/header OS byte.
        packed = gzip.compress(raw, mtime=0)
        if len(packed) > 0xffff or len(raw) > 0xffff:
            raise EchoKernelError('kernel section exceeds 16-bit container limit')
        result += struct.pack('<HHH', len(packed), len(raw), kind)
        result += packed
    return bytes(result) + suffix


def _kernel2_sections(data: bytes):
    if len(data) < 4 or struct.unpack_from('<I', data)[0] != len(data) - 4:
        raise EchoKernelError('kernel2 LZS length prefix is invalid')
    raw = lgp.lzs_decompress(data[4:])
    sections = []
    offset = 0
    for _ in range(18):
        if offset + 4 > len(raw):
            raise EchoKernelError('truncated kernel2 section table')
        size, = struct.unpack_from('<I', raw, offset)
        offset += 4
        end = offset + size
        if end > len(raw):
            raise EchoKernelError('truncated kernel2 section')
        sections.append(raw[offset:end])
        offset = end
    if offset != len(raw):
        raise EchoKernelError('kernel2 has trailing bytes or wrong section count')
    return sections


def _pack_kernel2_sections(sections):
    raw = bytearray()
    for section in sections:
        raw += struct.pack('<I', len(section))
        raw += section
    packed = lgp.lzs_compress(bytes(raw))
    if lgp.lzs_decompress(packed) != raw:
        raise EchoKernelError('kernel2 LZS round-trip failed')
    return struct.pack('<I', len(packed)) + packed


def merge_gzip_container(stock: bytes, echo: bytes,
                         preserve_weapon_sfx=False):
    """Merge decoded records while preserving Switch-native weapon SFX IDs.

    For Echo-S KERNEL.BIN callers enable ``preserve_weapon_sfx``: its weapon
    substitutions are PC/FFNx routing data rather than gameplay balance
    changes, and importing them makes Barret and Vincent silent on Switch
    before the character-aware Cosmo Memory remapper can see them.  It stays
    opt-in so this general container helper does not alter unrelated mods.
    """
    base, base_suffix = _gzip_sections(stock)
    mod, _mod_suffix = _gzip_sections(echo)
    if len(base) != len(mod):
        raise EchoKernelError('section count differs (%d vs %d)' %
                              (len(base), len(mod)))
    changed = []
    merged = []
    for index, ((base_kind, base_raw), (mod_kind, mod_raw)) in enumerate(zip(base, mod)):
        if base_kind != mod_kind:
            raise EchoKernelError('section %d type differs (%d vs %d)' %
                                  (index, base_kind, mod_kind))
        selected = mod_raw
        if preserve_weapon_sfx and base_kind == _WEAPON_SECTION_KIND:
            selected, _restored = _preserve_switch_weapon_sfx(base_raw,
                                                               mod_raw)
        if base_raw != selected:
            merged.append((base_kind, selected))
            changed.append(index)
        else:
            merged.append((base_kind, base_raw))
    result = _pack_gzip_sections(merged, base_suffix)
    _gzip_sections(result)  # structural verification
    return result, tuple(changed)


def merge_kernel2(stock: bytes, echo: bytes):
    base = _kernel2_sections(stock)
    mod = _kernel2_sections(echo)
    changed = tuple(i for i, (a, b) in enumerate(zip(base, mod)) if a != b)
    if len(base) != len(mod):  # kept explicit if the format changes later
        raise EchoKernelError('kernel2 section count differs')
    result = _pack_kernel2_sections([b if a != b else a for a, b in zip(base, mod)])
    if _kernel2_sections(result) != [b if a != b else a for a, b in zip(base, mod)]:
        raise EchoKernelError('kernel2 verification failed')
    return result, changed


def merge_directory(stock_dir: str, echo_dir: str, destination_dir: str,
                    log=print, include_window=False):
    """Merge portable Echo-S kernel records into Switch lang-en/kernel.

    Echo-S's ``window.bin`` record is PC font/width data.  It makes the
    Switch renderer advance incorrectly (notably after ``W``), so it is
    deliberately excluded unless a future Switch-native font conversion
    proves it safe.
    """
    def merge_echo_kernel(stock, echo):
        return merge_gzip_container(stock, echo, preserve_weapon_sfx=True)

    pairs = (('kernel.bin', 'KERNEL.BIN', merge_echo_kernel),
             ('kernel2.bin', 'kernel2.bin', merge_kernel2))
    if include_window:
        pairs += (('window.bin', 'window.bin', merge_gzip_container),)
    os.makedirs(destination_dir, exist_ok=True)
    report = {}
    for switch_name, echo_name, merger in pairs:
        stock_path = os.path.join(stock_dir, switch_name)
        echo_path = os.path.join(echo_dir, echo_name)
        if not os.path.isfile(stock_path) or not os.path.isfile(echo_path):
            raise FileNotFoundError('%s or %s' % (stock_path, echo_path))
        with open(stock_path, 'rb') as handle:
            stock = handle.read()
        with open(echo_path, 'rb') as handle:
            echo = handle.read()
        merged, changed = merger(stock, echo)
        target = os.path.join(destination_dir, switch_name)
        with open(target, 'wb') as handle:
            handle.write(merged)
        report[switch_name] = changed
        log('Echo-S %s: merged %d changed section(s): %s'
            % (switch_name, len(changed), ', '.join(map(str, changed)) or 'none'))
    return report
