"""
FF7 Switch executable channel.

MAJOR FINDING (verified against the Switch's own ff7_en):
The Switch port does NOT recompile the game to ARM. It runs the genuine
1998 x86 Windows ff7.exe (a DotEmu-wrapped build -- note its custom
`.dotemu` PE section) inside an ARM compatibility + NVN rendering shim
that lives in the title's exefs. The game binary sits in romfs at
    ff7/resources/ff7_1.02/ff7_en   (no extension; it's a PE32 EXE)

Consequences this module implements:
- A patched x86 exe (e.g. a Steam/2013 ff7.exe edited with Scarlet for
  text, or with a mod's code fixes) can be dropped in via LayeredFS by
  routing it to that romfs path. install_exe() does this.
- FFNx "HEXT" patches -- the runtime memory patches mods ship -- can be
  BAKED statically into the exe file, since the target x86 binary is the
  same one the Switch runs. apply_hext() does this: it maps each HEXT
  virtual address to a file offset via the PE section table and writes
  the bytes.

Caveats (documented, enforced where possible):
- HEXT patches that patch around FFNx's own hooks assume FFNx is present;
  baked into a bare exe they may be inert or wrong. Self-contained game
  patches (text limits, bug fixes, cut content) are the safe subset.
- Addresses landing in a section's zero-init tail (virtual size beyond
  raw size) have no file bytes to patch and are skipped with a warning.
- Only x86 PE32 inputs are accepted; anything else is refused.
"""
import struct


class NotAPE(Exception):
    pass


def parse_pe(data):
    """Return dict with image_base and sections [(name, va, vsize, raw_off,
    raw_size)] where va is the absolute virtual address (image_base + RVA)."""
    if data[:2] != b'MZ':
        raise NotAPE('no MZ header')
    # The DotEmu-trimmed exe has a nonstandard DOS stub; locate PE robustly.
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    if data[pe:pe + 4] != b'PE\0\0':
        pe = data.find(b'PE\0\0')
        if pe < 0:
            raise NotAPE('no PE signature')
    machine, nsec = struct.unpack_from('<HH', data, pe + 4)
    if machine != 0x14C:
        raise NotAPE(f'not x86 (machine {machine:#x})')
    size_opt = struct.unpack_from('<H', data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from('<H', data, opt)[0]
    if magic != 0x10B:
        raise NotAPE(f'not PE32 (magic {magic:#x})')
    image_base = struct.unpack_from('<I', data, opt + 28)[0]
    sec = opt + size_opt
    sections = []
    for i in range(nsec):
        so = sec + i * 40
        name = data[so:so + 8].split(b'\0')[0].decode('latin1', 'replace')
        vs, va, rs, ro = struct.unpack_from('<IIII', data, so + 8)
        sections.append((name, image_base + va, vs, ro, rs))
    file_align = struct.unpack_from('<I', data, opt + 36)[0] or 0x200
    return {'image_base': image_base, 'sections': sections, 'pe_off': pe,
            'sec_table': sec, 'file_align': file_align}


def va_to_offset(pe, va):
    """File offset for an absolute virtual address, or None if the address
    is unmapped or falls in a section's zero-init tail (no file bytes)."""
    for name, sva, vsize, raw_off, raw_size in pe['sections']:
        if sva <= va < sva + vsize:
            rel = va - sva
            if rel >= raw_size:
                return None  # in the .bss-like tail; not stored on disk
            return raw_off + rel
    return None


def grow_section_raw(out, va, n, log=lambda *_: None):
    """
    Make `n` bytes at `va` file-backed by raising the owning section's
    SizeOfRawData into file padding that already exists. `out` is a bytearray
    and is edited in place. Returns True if the header changed.

    Nothing moves. A PE pads each section's raw data up to FileAlignment, so
    between one section's raw end and the next section's raw start there is
    already a run of zero bytes on disk that the section is allowed to claim.
    This only ever claims bytes that are (a) inside the section's VirtualSize,
    (b) before the next section's raw offset, and (c) verified zero.

    ff7nx_60fps.py carries the same rule as `ensure_raw_backed` -- both tools
    are meant to run standalone, so neither imports the other.
    """
    pe = parse_pe(bytes(out))
    hit = None
    for i, (name, sva, vsize, raw_off, raw_size) in enumerate(pe['sections']):
        if sva <= va < sva + vsize:
            hit = (i, name, sva, vsize, raw_off, raw_size)
            break
    if hit is None:
        return False
    i, name, sva, vsize, raw_off, raw_size = hit
    rel = va - sva
    if rel + n <= raw_size:
        return False
    align = pe.get('file_align') or 0x200
    need = ((rel + n + align - 1) // align) * align
    if need > vsize:
        return False
    later = [s[3] for s in pe['sections'] if s[3] > raw_off]
    limit = min(later) if later else len(out)
    if raw_off + need > limit:
        return False
    if set(bytes(out[raw_off + raw_size:raw_off + need])) - {0}:
        return False
    if len(out) < raw_off + need:
        out.extend(b'\0' * (raw_off + need - len(out)))
    struct.pack_into('<I', out, pe['sec_table'] + i * 40 + 16, need)
    log(f'  .. {name} SizeOfRawData {raw_size:#x} -> {need:#x} '
        f'(claimed {need - raw_size} zero byte(s) of file padding)')
    return True


def _clean_hex(s):
    return bytes.fromhex(''.join(s.split()))


def parse_hext(text):
    """
    Parse a FFNx HEXT patch file into [(va, bytes)].

    Tolerant of the common dialects:
      # comment
      DC1D58 = 90 90 90
      0x00DC1D58 = 909090
      DC1D58 : 90 90 90
    Lines without an '=' or ':' separator, and comment/blank lines, are
    ignored. Addresses may be absolute VAs or (if below image base)
    treated as RVAs by the caller.
    """
    out = []
    for raw in text.splitlines():
        line = raw.split('#', 1)[0].split(';', 1)[0].strip()
        if not line:
            continue
        sep = '=' if '=' in line else (':' if ':' in line else None)
        if sep is None:
            continue
        addr_s, _, bytes_s = line.partition(sep)
        addr_s = addr_s.strip().lower().replace('0x', '')
        bytes_s = bytes_s.strip()
        # some formats write "orig -> new"; take the right side
        if '->' in bytes_s:
            bytes_s = bytes_s.split('->', 1)[1].strip()
        try:
            va = int(addr_s, 16)
            payload = _clean_hex(bytes_s)
        except ValueError:
            continue
        if payload:
            out.append((va, payload))
    return out


def apply_hext(data, hext_text, log=lambda *_: None):
    """
    Return a copy of the exe with the HEXT patches applied, plus counts.
    Addresses below the image base are treated as RVAs (image_base added).
    """
    pe = parse_pe(data)
    base = pe['image_base']
    out = bytearray(data)
    applied = skipped = 0
    for va, payload in parse_hext(hext_text):
        if va < base:
            va += base
        off = va_to_offset(pe, va)
        if off is None:
            # The address is mapped at runtime but the file stops short of it
            # -- it is in a section's linker padding. The two x86 builds differ
            # here: the PC build stores 320 more bytes of .rdata padding than
            # the Switch build, so the same HEXT would apply on one and be
            # skipped on the other. Claim the padding if the file has room.
            if grow_section_raw(out, va, len(payload), log):
                pe = parse_pe(bytes(out))
                off = va_to_offset(pe, va)
        if off is None or off + len(payload) > len(out):
            log(f'  ! hext {va:#x}: unmapped or in zero-init region, skipped')
            skipped += 1
            continue
        out[off:off + len(payload)] = payload
        applied += 1
    log(f'  hext: {applied} patch(es) applied, {skipped} skipped')
    return bytes(out), applied, skipped


def is_ff7_exe(data):
    try:
        parse_pe(data)
        return data[:2] == b'MZ' and b'FF7' in data[:0x400000]
    except Exception:
        return False


# --------------------------------------------------------------- CLI

def _main(argv=None):
    import argparse
    import os
    import sys
    ap = argparse.ArgumentParser(
        description='Patch a Windows x86 FF7 exe and/or place it into a '
                    'Switch SD tree. The Switch runs the real x86 ff7 exe '
                    '(romfs/ff7/resources/ff7_1.02/ff7_en); a patched Steam/'
                    '2013 exe drops in there via LayeredFS.')
    ap.add_argument('exe', help='base Windows ff7 exe (e.g. 2013 Steam ff7.exe)')
    ap.add_argument('--hext', nargs='*', default=[],
                    help='FFNx .hext/.txt patch files to bake in, in order')
    ap.add_argument('-o', '--out',
                    help='write the patched exe here (raw file)')
    ap.add_argument('--sdout',
                    help='also place it in a Switch SD tree at this root '
                         '(creates atmosphere/contents/<TID>/romfs/ff7/'
                         'resources/ff7_1.02/ff7_en)')
    ap.add_argument('--title-id', default='0100A5B00BDC6000',
                    help='title ID (default: US FF7)')
    a = ap.parse_args(argv)

    with open(a.exe, 'rb') as f:
        data = f.read()
    if not is_ff7_exe(data):
        print('! not a recognizable x86 FF7 exe (need a Windows ff7.exe)')
        return 1
    pe = parse_pe(data)
    print(f'ok: PE32 x86, image base {pe["image_base"]:#x}, '
          f'{len(pe["sections"])} sections'
          + (' [.dotemu present]' if any(s[0] == '.dotemu'
                                         for s in pe['sections']) else ''))
    total = 0
    for hp in a.hext:
        with open(hp, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
        print(f'applying {os.path.basename(hp)} ...')
        data, applied, skipped = apply_hext(data, text, lambda m: print(m))
        total += applied
    if total:
        print(f'{total} HEXT patch(es) baked in')
    if a.out:
        with open(a.out, 'wb') as f:
            f.write(data)
        print(f'wrote {a.out} ({len(data):,} bytes)')
    if a.sdout:
        dest = os.path.join(a.sdout, 'atmosphere', 'contents', a.title_id,
                            'romfs', 'ff7', 'resources', 'ff7_1.02', 'ff7_en')
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'wb') as f:
            f.write(data)
        print(f'placed in SD tree: {dest}')
    if not a.out and not a.sdout:
        print('(nothing written -- pass -o and/or --sdout)')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(_main())
