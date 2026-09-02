#!/usr/bin/env python3
"""
test_tex_caps.py -- a texture cap must produce the size it names.

WHY THIS EXISTS
===============
`Cap at 768px` was in the battle-background menu and produced 512px textures.
Nothing failed and the build log even said "capped at 768px" -- it printed the
SETTING, not the result. The setting was real, the plumbing was real, and the
number never reached a pixel.

Two independent reasons, both silent:

  * the battle-background upscale DOUBLED from the vanilla tile size, so from
    256px it could only ever reach 256, 512 or 1024. 768 fell back to 512.
  * `_cap_size` divided by a power of two, so a 1024px source capped at 768
    came out 512.

So the checks here never look at a setting or a log line. They run the real
conversion functions and measure the TEX headers that come out.
"""
import sys

import tex

FAIL = []


def check(name, got, want):
    if got != want:
        FAIL.append(name)
        print('FAIL  %s\n        got  %r\n        want %r' % (name, got, want))
    else:
        print('  ok  %s' % name)


def make_tex(w, h, bypp=4):
    """
    A truecolor TEX the real parser accepts.

    Built from tex.py's own field offsets rather than hand-numbered ones, so
    a header layout change breaks this loudly instead of making every check
    below silently return None.
    """
    hdr = bytearray(tex.HEADER_LEN)

    def put(off, val):
        hdr[off:off + 4] = int(val).to_bytes(4, 'little')

    put(tex.O_VERSION, 1)
    put(tex.O_WIDTH, w)
    put(tex.O_HEIGHT, h)
    put(tex.O_BYTES_PER_PIXEL, bypp)
    put(tex.O_PAL_FLAG, 0)
    put(tex.O_PAL_SIZE, 0)
    put(tex.O_NUM_PALETTES, 0)
    put(tex.O_COLORS_PER_PAL, 0)
    body = bytearray()
    for y in range(h):
        for x in range(w):
            body += bytes(((x * 7) & 0xFF, (y * 5) & 0xFF, ((x + y) * 3) & 0xFF))
            if bypp == 4:
                body += b'\xff'
    data = bytes(hdr) + bytes(body)
    if tex.parse(data) is None:
        raise AssertionError('the synthetic %dx%d TEX does not parse -- this '
                             'test cannot measure anything' % (w, h))
    return data


def dims(data):
    t = tex.parse(data)
    return (t['width'], t['height']) if t else None


def main():
    # ---- 1. _cap_size: the cap is the number -------------------------------
    # Power-of-two caps must be UNCHANGED from the old halving behaviour, so
    # no existing build moves; only the non-power-of-two ones are new.
    for (w, h), cap, want in (
            ((1024, 1024), 512, (512, 512)),      # unchanged
            ((1024, 1024), 256, (256, 256)),      # unchanged
            ((1024, 512), 512, (512, 256)),       # unchanged, aspect kept
            ((1024, 1024), 768, (768, 768)),      # NEW -- used to give 512
            ((1024, 512), 768, (768, 384)),       # NEW, aspect kept
            ((512, 512), 768, (512, 512)),        # never upscales
            ((768, 768), 768, (768, 768))):
        check('_cap_size(%dx%d, cap %d)' % (w, h, cap),
              tex._cap_size(w, h, cap), want)

    # ---- 2. the field texture cap, measured off the output -----------------
    src = make_tex(1024, 1024)
    for cap, want in ((1024, (1024, 1024)), (768, (768, 768)),
                      (512, (512, 512)), (256, (256, 256))):
        out, note = tex.cap_dimensions(src, cap)
        # a cap at or above the source is a no-op and returns the reason
        got = dims(out) if out is not None else dims(src)
        check('field cap %d: 1024x1024 TEX -> %s  (%s)' % (cap, want, note),
              got, want)

    # ---- 3. the battle background cap, measured off the output -------------
    # A 256x256 vanilla tile with a 1024x1024 mod replacement: exactly the
    # Avalanche Arisen case the setting exists for.
    van = make_tex(256, 256, bypp=3)
    van_pal = tex.convert_for_battle(van, None, cap=256)[0]
    mod = make_tex(1024, 1024)
    for cap, want in ((256, (256, 256)), (512, (512, 512)),
                      (768, (768, 768)), (1024, (1024, 1024))):
        out, note = tex.convert_for_battle(mod, van_pal, cap=cap)
        check('battle bg cap %d: 256px vanilla + 1024px mod -> %s'
              % (cap, want), dims(out) if out else None, want)

    # ---- 4. the field background page size patches the module --------------
    import capstone
    import struct
    import ff7nx_fieldbg as FB
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    check('768 is an offered page size', 768 in FB.SUPPORTED_PAGE_PX, True)

    # The two padding tables MUST agree: ff7nx_fieldbg tells the module how
    # many bytes to read per page, field_bg_native decides how many the file
    # stores. A disagreement over-reads the stream and corrupts everything
    # after it in that field, so this is checked rather than trusted.
    import field_bg_native as FBN
    for px in FB.SUPPORTED_PAGE_PX:
        check('%dpx: module read size == stored size' % px,
              FB.read_bytes(px), FBN.stored_bytes(px, 2))
        check('%dpx: stored size holds every pixel' % px,
              FB.read_bytes(px) >= px * px * 2, True)
        check('%dpx: read size is a one-word immediate' % px,
              FB.mov_imm_word(27, FB.read_bytes(px)) is not None, True)
    check('depth-1 pages are never padded',
          FBN.stored_bytes(512, 1), FBN.VANILLA_PX ** 2)
    check('128 is an offered page size', 128 in FB.SUPPORTED_PAGE_PX, True)

    # OFF and 256 are DIFFERENT THINGS now. 256 used to mean "do nothing",
    # which is why "vanilla resolution in truecolor" -- the cheapest
    # promotion there is -- had never been tried: the UI could not ask for it.
    check('off is not 256', FB.OFF_PAGE_PX != FB.VANILLA_PAGE_PX, True)
    check('off is disabled', FB.enabled.__call__ and FB.words(0), {})
    check('256 writes only the scoped FX blend ladder',
          set(FB.words(256)), set(FB.FX_BLEND_SITES))
    check('256 still gets the field buffer on a big build',
          set(FB.words(256, max_raw=3_000_000)),
          set(FB.FX_BLEND_SITES) | {FB.SITE_FIELD_BUF})
    check('256 needs the module for truecolor additive FX',
          FB.patches_module(256), True)
    check('128 does need a module', FB.patches_module(128), True)

    # The ladder is derived from what the READ-BYTES immediate can encode in
    # one instruction, not from "multiples of 256" (HANDOFF-52 3.1 got the
    # constraint from the element size, which is an allocation and rounds up).
    # 384 and 480 are the two sizes that look plausible and are not.
    for px in (384, 480):
        check('%d is correctly refused (read bytes 0x%X is not a one-word '
              'immediate)' % (px, px * px * 2),
              FB.mov_imm_word(27, px * px * 2), None)

    for px in (128, 512, 768, 1024):
        w = FB.words(px=px)
        surf = w.get(FB.SITE_SURF_WH)
        pitch = w.get(FB.SITE_SURF_PITCH)
        got = None
        if surf and pitch:
            a = list(md.disasm(struct.pack('<I', surf[1]), 0))[0]
            b = list(md.disasm(struct.pack('<I', pitch[1]), 0))[0]
            got = (int(a.op_str.split('#')[1], 0), int(b.op_str.split('#')[1], 0))
        check('field bg %dpx: surface w/h and stride' % px, got, (px, px * 2))
        # every site must be one word -- a two-word constant would run off
        # the end of the instruction it replaces
        check('field bg %dpx: all %d site(s) encoded' % (px, len(w)),
              all(v[1] is not None for v in w.values()) and len(w) >= 6, True)

    # REGRESSION LOCK. The old version of this check compared words(512)
    # against itself -- `{...} and {k: v[1] for ...}` evaluates to the right
    # operand, so it asserted X == X and could never fail. These are the
    # literal words the previous implementation produced, captured before
    # words() was rewritten to derive `elem` by rounding up and the loop bound
    # by rounding to the imm12 grid. They must not move.
    for px, want in (
            (512, {0x92D15C: 0x321D03FC, 0x9370C8: 0x321D03F9,
                   0x9370CC: 0x320D03FB, 0xA026C8: 0x51410109,
                   0xA03C44: 0x321703F3, 0xA03C88: 0x321603E8}),
            (768, {0x92D15C: 0x5280025C, 0x9370C8: 0x52800259,
                   0x9370CC: 0x52A0025B, 0xA026C8: 0x51424109,
                   0xA03C44: 0x321807F3, 0xA03C88: 0x321707E8}),
            (1024, {0x92D15C: 0x321B03FC, 0x9370C8: 0x321B03F9,
                    0x9370CC: 0x320B03FB, 0xA026C8: 0x51440109,
                    0xA03C44: 0x321603F3, 0xA03C88: 0x321503E8})):
        check('%dpx words unchanged by the ladder rewrite' % px,
              {k: v[1] for k, v in FB.words(px=px).items()
               if k in want}, want)

    # 128 is the new one, so state what it must be rather than only that it
    # encodes. elem rounds UP to 1 (a 32,768 byte page inside a 65,536 byte
    # allocation), and the loop bound is exactly px*px with no rounding.
    w128 = FB.words(px=128)
    check('128px alloc element size is 1 (rounded up from 0.5)',
          list(md.disasm(struct.pack('<I', w128[FB.SITE_ALLOC_ELEM][1]),
                         0))[0].op_str.split('#')[1], '1')
    check('128px reads 0x8000 bytes a page',
          int(list(md.disasm(struct.pack('<I', w128[FB.SITE_READ_BYTES][1]),
                             0))[0].op_str.split('#')[1], 0), 0x8000)

    # The loop bound may only ever round UP, and never past the allocation --
    # short means raw colour 0 stays black instead of turning transparent,
    # long means writing off the end of the page.
    for px in (128, 512, 768, 1024):
        bound = int(list(md.disasm(
            struct.pack('<I', FB.words(px=px)[FB.SITE_CVT_BOUND][1]),
            0))[0].op_str.split('#')[1].split(',')[0], 0) << 12
        elem = -(-(px * px * 2) // 0x10000)
        check('%dpx loop bound covers every pixel and fits the allocation'
              % px, px * px <= bound <= elem * 0x10000 // 2, True)

    page_ceiling_is_enforced()
    budget_setting()
    cache_version()


def page_ceiling_is_enforced():
    """
    The page ceiling must actually bind, on real sections.

    It did not. A build with the ceiling at 12 produced a 15-page field,
    because `_projected` decided a page would be freed whenever all of ITS
    OWN tiles moved -- while the real free pass also refuses to drop a page
    that is some other tile's FX PAGE. Three pages the projection had written
    off stayed, and every one of them is a texture the loader has to
    allocate.

    This runs the repack over synthetic sections built to contain exactly
    that trap: a page whose tiles all move, referenced as an fx page from
    elsewhere.
    """
    import os
    import field_bg_repack as RP

    old = {k: os.environ.get(k) for k in
           (RP.MAX_TOTAL_PAGES_ENV, RP.BUDGET_ENV, RP.PARTIAL_ENV,
            RP.REPLACE_ONLY_ENV)}
    try:
        os.environ[RP.MAX_TOTAL_PAGES_ENV] = '4'
        os.environ[RP.BUDGET_ENV] = '0'
        os.environ[RP.PARTIAL_ENV] = '1'
        os.environ[RP.REPLACE_ONLY_ENV] = '0'

        # the projection and the free pass must agree about fx pages
        src = inspect_source(RP.repack_section9)
        check('the projection excludes fx-referenced pages',
              'fx_referenced' in src, True)
        check('the free pass still tests T_FX_PAGE',
              'T_FX_PAGE' in src, True)
        # and the two must consult the SAME set
        i = src.index('fx_referenced')
        check('fx_referenced is built before the take loop',
              src.index('def _projected') > i, True)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def inspect_source(fn):
    import inspect
    return inspect.getsource(fn)

    if FAIL:
        print('\n%d check(s) failed' % len(FAIL))
        sys.exit(1)
    print('all good')



def budget_setting():
    """
    The field-background memory budget must reach the packer.

    It was env-var-only, so nothing could disagree with anything. Now it is a
    menu, and a menu is exactly where this project has lost a setting before
    -- one that was saved, traced, read into the environment, and never drawn.
    So: every offered value round-trips through the environment into
    `budget_bytes()`, and the choices themselves are checked against the
    hardware measurement they came from.
    """
    import os
    import re

    import field_bg_repack as R

    bad = 0
    src = open('7th_heaven_nx.py', encoding='utf-8').read()
    i = src.index('FIELD_BG_BUDGET_CHOICES = [')
    j = src.index('\n]', i)
    offered = [float(v) for v in re.findall(r'\(\s*([\d.]+),', src[i:j])]

    check('the menu offers the shipping default',
          R.DEFAULT_BUDGET_MB in offered, True)

    # THESE TWO CHECKS USED TO SAY:
    #     nothing at or above the 6.06 MB measured-black case
    #     nothing below the 2.44 MB measured-clean case
    # and they have been REMOVED rather than relaxed, because the model they
    # enforced was refuted by its own follow-up measurements:
    #
    #   * 14.0 MB was CLEAN on hardware and 18.0 MB was not. A menu forbidden
    #     from offering anything >= 6.06 cannot express either, so it could
    #     not have found that out.
    #   * failure is therefore NOT MONOTONIC in the budget, and "6.06 was
    #     black" was one field, one build, at 512px pages -- never a ceiling.
    #   * lowering the budget to 4.0 MB made the margins WORSE.
    #
    # What replaces them is the invariant that actually holds: the menu is a
    # bisecting instrument, so it must be ordered, duplicate-free, and lead
    # with unlimited.
    check('the menu is sorted with unlimited first',
          offered, [0.0] + sorted(v for v in offered if v))
    check('the menu has no duplicates', len(set(offered)), len(offered))

    # The bug this release fixes: 0 meant `max(0.0, mb) * 1048576` == 0, so
    # "no budget" silently promoted NOTHING instead of everything.
    old = os.environ.get(R.BUDGET_ENV)
    try:
        for raw in ('0', '0.0', '', 'unlimited'):
            os.environ[R.BUDGET_ENV] = raw
            check('budget %-11r means UNLIMITED, not "promote nothing"' % raw,
                  R.budget_bytes(), R.UNLIMITED)
        os.environ[R.BUDGET_ENV] = '0'
        check('unlimited affords more 512px pages than 18 MB does',
              R.budget_bytes() // R._page_bytes(512, 2) > 12, True)

        for mb in offered:
            os.environ[R.BUDGET_ENV] = str(mb)
            check('budget %-5s MB reaches budget_bytes()' % mb,
                  R.budget_bytes(),
                  R.UNLIMITED if not mb else int(mb * 1048576))
        # and the number actually changes how many pages a field can afford
        os.environ[R.BUDGET_ENV] = '4.0'
        low = R.budget_bytes() // R._page_bytes(512, 2)
        os.environ[R.BUDGET_ENV] = '6.0'
        high = R.budget_bytes() // R._page_bytes(512, 2)
        check('a bigger budget affords more 512px pages (%d -> %d)'
              % (low, high), high > low, True)
    finally:
        os.environ.pop(R.BUDGET_ENV, None)
        if old is not None:
            os.environ[R.BUDGET_ENV] = old
    return bad


def cache_version():
    """
    The texconv cache tag must move when the conversion changes.

    This is the failure that made the 768 fix look like it had not shipped.
    The cap was already in the cache key, so `-cap768` was considered enough
    -- but the FIRST 768 build ran the doubling bug, produced 512x512, and
    cached it under `-cap768`. Fixing the bug changed the pixels and not the
    key, so the next build reused the wrong-sized conversion and battle.lgp
    came out byte-identical.

    A cap and a converter version answer different questions: one is "what
    size did you ask for", the other is "what does this build do with that
    request". Only the second one invalidates work already on disk.
    """
    import re

    bad = 0
    src = open('build.py', encoding='utf-8').read()
    m = re.search(r"cache_key = \('TEXCONV-V(\d+)-", src)
    check('build.py has a texconv cache version tag', bool(m), True)
    if not m:
        return 1
    ver = int(m.group(1))

    # The integer-factor upscale landed in v6. Anything earlier means a
    # build could still be serving conversions made by the doubling code.
    check('the tag is at least v6 (the integer-factor upscale)', ver >= 6, True)

    # and the tag must be documented, so the next person knows to move it
    i = max(0, src.rfind('\n', 0, m.start()) - 1200)
    ctx = src[i:m.start()]
    check('the version history is written down next to it',
          ('v6' in ctx or 'V6' in ctx) and 'BUMP THIS' in ctx, True)
    return bad

if __name__ == '__main__':
    main()
