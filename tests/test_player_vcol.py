#!/usr/bin/env python3
"""Regression checks for the truecolor player summon-fade repair.

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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import p


def part(vcol_type=0, alpha=128):
    """Small valid P part with two vertices and an existing colour array."""
    h = [0] * 32
    h[0] = 1
    h[2] = vcol_type
    h[3] = 2                       # vertices
    h[6] = 2                       # texcoords
    h[15] = 0                      # normal-index flag
    header = struct.pack('<32I', *h)
    vertices = bytes(2 * 12)
    texcoords = bytes(2 * 8)
    colours = bytes((11, 22, 33, alpha, 44, 55, 66, alpha))
    out = header + vertices + texcoords + colours
    assert p.parse_header(out) is not None
    return out


def main():
    source = part()
    fixed, note = p.enable_vertex_colors(source)
    assert fixed is not None, note
    assert struct.unpack_from('<I', fixed, 0x08)[0] == 1
    assert fixed[:0x08] == source[:0x08]
    assert fixed[0x0C:] == source[0x0C:]
    assert len(fixed) == len(source)

    # Especially lock the player-safe part of the policy: do not change the
    # stored colour or its exporter alpha while enabling its visibility.
    h = p.parse_header(fixed)
    voff = (p.HEADER_LEN
            + (h['numVertices'] + h['numNormals'] + h['numUnknown1']) * 12
            + h['numTexCoords'] * 8)
    assert fixed[voff:voff + 8] == bytes((11, 22, 33, 128,
                                         44, 55, 66, 128))

    unchanged, why = p.enable_vertex_colors(part(vcol_type=1))
    assert unchanged is None
    assert why == 'vertex colours already enabled'

    bad, why = p.enable_vertex_colors(b'not a P file')
    assert bad is None
    assert why == 'unparseable P file'
    print('all player vertex-colour checks passed')


if __name__ == '__main__':
    main()
