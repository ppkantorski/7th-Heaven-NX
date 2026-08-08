"""
FF7 x86 exe patcher for the Switch port.

The Switch runs the genuine x86 ff7 exe through DotEmu's ARM compat layer,
and an unmodified PC ff7.exe boots on it. So x86 code we inject into the
exe RUNS on hardware -- which is how FFNx-style features (60fps, etc.) can
be brought over WITHOUT FFNx: bake the patches/code statically into the
exe and ship it as romfs/ff7/resources/ff7_1.02/ff7_en via LayeredFS.

This module applies a declarative patch spec to a base exe:

  - byte patches (with original-byte VERIFICATION so a wrong build fails
    loudly instead of corrupting the exe), and
  - code-cave injections: relocate a few instructions from a hook site
    into free slack space, run your injected x86 there, then jump back
    (a standard detour) -- the mechanism a real 60fps port needs.

Spec format (JSON):
{
  "name": "FF7 60fps (experimental)",
  "expect_marker": "yama",              // must appear in exe (sanity)
  "patches": [
    { "name": "unlock field cap",
      "va": "0x76D8XX", "expect": "AA BB", "set": "90 90" },
    { "name": "hook frame limiter",
      "hook_va": "0x41A76F", "steal": 6,      // >=5, whole instructions
      "code": "…hex x86 to run before the stolen bytes…" }
  ]
}

Nothing here invents the actual 60fps bytes -- those come from RE / the
FFNx source. This is the tool that applies them and ships the result.
"""
import json
import os
import struct

import exe_patch

JMP32 = 0xE9
INT3 = 0xCC


def _hex(s):
    return bytes.fromhex(''.join(str(s).split()))


def _va(x):
    return int(str(x), 16) if isinstance(x, str) else int(x)


def find_caves(data, min_size=16):
    """Runs of INT3 (0xCC) or 0x00 padding in executable sections, as
    (va, file_off, length). These are safe places to write injected code
    without adding a PE section."""
    pe = exe_patch.parse_pe(data)
    caves = []
    IMAGE_SCN_MEM_EXECUTE = 0x20000000
    # capstone isn't required here; we just scan section raw bytes.
    raw = _section_headers(data, pe)
    for name, va, vsize, ro, rs, chars in raw:
        if not (chars & IMAGE_SCN_MEM_EXECUTE):
            continue
        blob = data[ro:ro + rs]
        i = 0
        while i < len(blob):
            b = blob[i]
            if b in (0xCC, 0x00):
                j = i
                while j < len(blob) and blob[j] == b:
                    j += 1
                if j - i >= min_size:
                    caves.append((va + i, ro + i, j - i))
                i = j
            else:
                i += 1
    caves.sort(key=lambda c: -c[2])
    return caves


def _section_headers(data, pe):
    """(name, va, vsize, raw_off, raw_size, characteristics) per section."""
    peo = pe['pe_off']
    nsec = struct.unpack_from('<H', data, peo + 6)[0]
    size_opt = struct.unpack_from('<H', data, peo + 20)[0]
    sec = peo + 24 + size_opt
    out = []
    for i in range(nsec):
        so = sec + i * 40
        name = data[so:so + 8].split(b'\0')[0].decode('latin1', 'replace')
        vs, va, rs, ro = struct.unpack_from('<IIII', data, so + 8)
        chars = struct.unpack_from('<I', data, so + 36)[0]
        out.append((name, pe['image_base'] + va, vs, ro, rs, chars))
    return out


def apply_spec(data, spec, log=lambda *_: None):
    """Apply a patch spec to exe bytes. Returns (new_bytes, applied, errors)."""
    pe = exe_patch.parse_pe(data)
    out = bytearray(data)
    marker = spec.get('expect_marker')
    if marker and marker.encode() not in data:
        return None, 0, [f'expect_marker {marker!r} not found -- wrong exe?']
    applied = 0
    errors = []
    caves = None
    cave_cursor = 0  # (va, off, remaining) tracking within a chosen cave

    for p in spec.get('patches', []):
        nm = p.get('name', '?')
        if 'set' in p:  # byte patch
            va = _va(p['va'])
            off = exe_patch.va_to_offset(pe, va)
            new = _hex(p['set'])
            if off is None or off + len(new) > len(out):
                errors.append(f'{nm}: VA {hex(va)} unmapped'); continue
            if 'expect' in p:
                exp = _hex(p['expect'])
                cur = bytes(out[off:off + len(exp)])
                if cur != exp:
                    errors.append(
                        f'{nm}: verify FAILED at {hex(va)} '
                        f'(have {cur.hex()}, expected {exp.hex()}) -- skipped')
                    continue
            out[off:off + len(new)] = new
            applied += 1
            log(f'  {nm}: {len(new)} bytes @ {hex(va)}')
        elif 'code' in p:  # code-cave detour
            if caves is None:
                caves = find_caves(bytes(out), 32)
                if not caves:
                    errors.append(f'{nm}: no code cave available'); continue
                cave_cursor = list(caves[0])  # [va, off, remaining]
            hook_va = _va(p['hook_va'])
            steal = int(p.get('steal', 5))
            if steal < 5:
                errors.append(f'{nm}: steal must be >=5'); continue
            hook_off = exe_patch.va_to_offset(pe, hook_va)
            if hook_off is None:
                errors.append(f'{nm}: hook VA unmapped'); continue
            inj = _hex(p['code'])
            stolen = bytes(out[hook_off:hook_off + steal])
            cave_va, cave_off, remaining = cave_cursor
            need = len(inj) + steal + 5  # inj + stolen + jmp back
            if remaining < need:
                errors.append(f'{nm}: cave too small ({remaining}<{need})')
                continue
            # write cave: injected code, stolen bytes, jmp back to hook+steal
            blob = bytearray()
            blob += inj
            blob += stolen
            back_target = hook_va + steal
            rel = back_target - (cave_va + len(blob) + 5)
            blob += bytes([JMP32]) + struct.pack('<i', rel)
            out[cave_off:cave_off + len(blob)] = blob
            # write hook: jmp to cave, pad remaining stolen bytes with NOP
            rel2 = cave_va - (hook_va + 5)
            out[hook_off:hook_off + 5] = bytes([JMP32]) + struct.pack('<i', rel2)
            for k in range(5, steal):
                out[hook_off + k] = 0x90  # NOP
            cave_cursor = [cave_va + len(blob), cave_off + len(blob),
                           remaining - len(blob)]
            applied += 1
            log(f'  {nm}: detour @ {hook_va:#x} -> cave {cave_va:#x} '
                f'({len(inj)}B code, stole {steal})')
        else:
            errors.append(f'{nm}: patch has neither "set" nor "code"')
    return bytes(out), applied, errors


def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('exe', help='base PC ff7 exe')
    ap.add_argument('spec', help='JSON patch spec')
    ap.add_argument('-o', '--out')
    ap.add_argument('--sdout')
    ap.add_argument('--title-id', default='0100A5B00BDC6000')
    ap.add_argument('--list-caves', action='store_true',
                    help='just print available code caves and exit')
    ap.add_argument('--pad', type=int, default=0,
                    help='append N zero bytes after the last PE section '
                         '(safe -- this exe already has ~8KB of unmapped '
                         'trailing data past .FTS). Changes total file size '
                         'without touching any mapped/executed bytes. Use '
                         'to rule out a same-size-gets-cached quirk on the '
                         'Switch side when a code edit alone shows no effect.')
    a = ap.parse_args(argv)
    data = open(a.exe, 'rb').read()
    if not exe_patch.is_ff7_exe(data):
        print('! not a recognizable x86 FF7 exe'); return 1
    if a.list_caves:
        for va, off, ln in find_caves(data, 32)[:20]:
            print(f'  cave VA {hex(va)} size {ln}')
        return 0
    spec = json.load(open(a.spec))
    out, applied, errors = apply_spec(data, spec, lambda m: print(m))
    for e in errors:
        print('  ERROR', e)
    if out is None:
        print('aborted'); return 1
    if a.pad:
        out = out + b'\x00' * a.pad
        print(f'padded +{a.pad} bytes (new size {len(out):,}) -- unmapped, '
              f'does not change any executed byte')
    print(f'{applied} patch(es) applied, {len(errors)} error(s)')
    if a.out:
        open(a.out, 'wb').write(out); print(f'wrote {a.out}')
    if a.sdout:
        dest = os.path.join(a.sdout, 'atmosphere', 'contents', a.title_id,
                            'romfs', 'ff7', 'resources', 'ff7_1.02', 'ff7_en')
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, 'wb').write(out); print(f'placed: {dest}')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(_main())