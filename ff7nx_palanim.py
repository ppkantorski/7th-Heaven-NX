#!/usr/bin/env python3
"""Which palettes a field's scripts animate at runtime. BUILD 581.

FF7 animates field colour (the ghost hotel fog's pulse, sinbil_1's green
glow, the Chocobo Square lights) by copying a palette into the palette
BUFFER (STPAL), modulating or rotating the buffer (MPPAL/ADPAL/RTPAL/...),
and loading it back (LDPAL / LDPLS). Only the LOAD writes a live palette,
so the destination palettes of every LDPAL/LDPLS in the field's scripts are
exactly the palettes whose colours change while the field runs.

A truecolor page has no palette. Converting a page that such a palette
draws freezes the effect -- BUILD 579 did exactly that to ghotel's and
sinbil_1's fog once the `AA REMOVED` fix made their pages look "static" to
the FX passes. `animated_palettes` is the veto those passes now consult.

A load whose destination comes from a variable (bank nibble set) cannot be
resolved statically; the field is then reported as `ALL` and every palette
counts as animated -- the conservative answer.
"""
from __future__ import annotations

import echo_s_flevel as ES

OP_LDPAL = 0xE6          # banks, buffer start, palette, count
OP_LDPLS = 0xEC          # banks, buffer start, palette, count
ALL = frozenset(range(256))


def animated_palettes(script_section):
    """frozenset of palette ids written at runtime (ALL if unresolvable)."""
    out = set()
    try:
        blocks = ES._routine_blocks(script_section)
    except Exception:                                          # noqa: BLE001
        return frozenset()
    for start, end in blocks:
        stream, _trunc = ES._decode_block(script_section, start, end)
        for off, op, size in stream:
            if op not in (OP_LDPAL, OP_LDPLS) or size < 5:
                continue
            banks = script_section[off + 1]
            # LDPAL's destination palette is the third operand; its bank is
            # the high nibble of the second banks byte in the 2-byte forms,
            # but LDPAL carries one banks byte for (src, dst): the LOW
            # nibble belongs to the destination.
            if banks & 0x0F:
                return ALL
            out.add(script_section[off + 3])
    return frozenset(out)
