"""
FF7 PC .P (battle/field model part) minimal parser + render-state transplant.

Layout (validated against all 2794 battle parts in NinoStyle Battle.iro --
front-block math is exact for every one):

    0x80 header, then:
    vertices        numVertices  x 12
    normals         numNormals   x 12
    unknown1        numUnknown1  x 12
    texcoords       numTexCoords x  8
    vertexColors    numVertices  x  4
    polyColors      numPolys     x  4
    edges           numEdges     x  4
    polys           numPolys     x 24
    hundreds        numHundreds  x 100   <- per-group RENDER STATE
    groups          numGroups    x 56
    boundingBox / normal-index table (tail)

Why transplant: the enemy death "red dissolve" re-renders parts with a
special blend state derived from the part's "hundreds" block. NinoStyle
parts ship exporter-default hundreds; vanilla parts carry the state the
Switch engine's dissolve path expects. Copying the vanilla hundreds into
the mod part changes NO geometry -- only render state.
"""
import struct

HEADER_LEN = 0x80
HUNDRED_LEN = 100


def parse_header(d):
    if len(d) < HEADER_LEN or struct.unpack_from('<I', d, 0)[0] != 1:
        return None
    h = struct.unpack_from('<16I', d, 0)
    info = {
        'numVertices': h[3], 'numNormals': h[4], 'numUnknown1': h[5],
        'numTexCoords': h[6], 'numNormalIndices': h[7],
        'numEdges': h[8], 'numPolys': h[9],
        'numHundreds': h[12], 'numGroups': h[13], 'numBoundingBoxes': h[14],
    }
    off = (HEADER_LEN
           + (info['numVertices'] + info['numNormals']
              + info['numUnknown1']) * 12
           + info['numTexCoords'] * 8
           + info['numVertices'] * 4
           + info['numPolys'] * 4
           + info['numEdges'] * 4
           + info['numPolys'] * 24)
    info['hundreds_offset'] = off
    end = off + info['numHundreds'] * HUNDRED_LEN
    if end + info['numGroups'] * 56 > len(d):
        return None
    return info


def normalize_part(d):
    """
    Fix the NinoStyle exporter anomalies that (per full diff against
    vanilla battle.lgp) distinguish every mod part from every vanilla
    part beyond geometry itself:

    - header vcolType (+0x08): vanilla always 1, mod 0. The death-effect
      path re-renders parts via their VERTEX COLORS; a part flagged as
      having no vertex colors gives it nothing to draw -> instant vanish.
      The parts DO carry a vertex-color block, so setting the flag is
      safe and makes the effect path see it.
    - header normal-index flag (+0x3C): vanilla always 1, mod 0; the
      normal-index table exists in both.
    - vertex-color alpha: vanilla 255, mod 128. Matched to vanilla so any
      alpha-driven effect starts from full opacity.

    Returns (new_bytes, changed_description) or (None, reason).
    """
    h = parse_header(d)
    if h is None:
        return None, 'unparseable P file'
    b = bytearray(d)
    changes = []
    if struct.unpack_from('<I', b, 0x08)[0] != 1:
        struct.pack_into('<I', b, 0x08, 1)
        changes.append('vcolType=1')
    if struct.unpack_from('<I', b, 0x3C)[0] != 1:
        struct.pack_into('<I', b, 0x3C, 1)
        changes.append('normIdxFlag=1')
    voff = (HEADER_LEN + (h['numVertices'] + h['numNormals']
                          + h['numUnknown1']) * 12
            + h['numTexCoords'] * 8)
    fixed_alpha = 0
    for i in range(h['numVertices']):
        a = b[voff + i * 4 + 3]
        if a != 255:
            b[voff + i * 4 + 3] = 255
            fixed_alpha += 1
    if fixed_alpha:
        changes.append(f'{fixed_alpha} vertex alphas -> 255')
    if not changes:
        return None, 'already normal'
    return bytes(b), ', '.join(changes)


def transplant_hundreds(mod_bytes, van_bytes):
    """
    Return mod_bytes with its hundreds block replaced by the vanilla
    part's, or (None, reason) when the two parts are not compatible.
    Only performed when both parts have the same numHundreds and
    numGroups, so group<->state pairing stays valid.
    """
    m = parse_header(mod_bytes)
    v = parse_header(van_bytes)
    if m is None or v is None:
        return None, 'unparseable P file'
    if m['numHundreds'] != v['numHundreds'] \
            or m['numGroups'] != v['numGroups']:
        return None, (f"group mismatch (mod {m['numGroups']}g/"
                      f"{m['numHundreds']}h, vanilla {v['numGroups']}g/"
                      f"{v['numHundreds']}h)")
    n = m['numHundreds'] * HUNDRED_LEN
    mo, vo = m['hundreds_offset'], v['hundreds_offset']
    van_block = bytearray(van_bytes[vo:vo + n])
    # Hardware bracketing (v6..v9 boots) established: full vanilla
    # hundreds -> dissolve works, untextured-vanilla parts white; mod
    # words -> textures, no dissolve; and v9 proved that OR-ing the mod's
    # broader sampling bits (0x176) on top of vanilla BLOCKS the dissolve
    # (for single-texture parts v9 differed from working-v6 only by those
    # bits). So this is v6's proven state with the two smallest possible
    # deviations: the mod's texture-slot ID (+0x10, equal to vanilla's for
    # single-texture models anyway) and ONLY the V_TEXTURE bit (0x2) OR'd
    # into +0x08/+0x0C so formerly-untextured vanilla state samples the
    # mod's texture instead of rendering flat white.
    for i in range(m['numHundreds']):
        base = i * HUNDRED_LEN
        van_block[base + 0x10:base + 0x14] = \
            mod_bytes[mo + base + 0x10:mo + base + 0x14]
        for woff in (0x08, 0x0C):
            vw = struct.unpack_from('<I', van_block, base + woff)[0]
            mw = struct.unpack_from('<I', mod_bytes, mo + base + woff)[0]
            struct.pack_into('<I', van_block, base + woff,
                             vw | (mw & 0x2))
    van_block = bytes(van_block)
    if mod_bytes[mo:mo + n] == van_block:
        return None, 'already identical'
    out = mod_bytes[:mo] + van_block + mod_bytes[mo + n:]
    assert len(out) == len(mod_bytes)
    return out, 'transplanted'
