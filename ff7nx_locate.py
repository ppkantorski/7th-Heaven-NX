#!/usr/bin/env python3
"""
ff7nx_locate.py -- resolve the dispatcher hook sites and emit them as Python.

    python3 ff7nx_locate.py --exe ff7_en --nso exefs/main \
            --emit ff7nx_dispatch_sites.py

Every address is derived, never typed:

  1. FFNx's own chain (ff7_data.h) is evaluated against ff7_en. Each derived
     function address is compared with the address encoded in its FFNx symbol
     name; a single disagreement aborts.
  2. The containing function is mapped to ARM64 through the table at module
     offset 0x126D3A8, whose pointers come from R_AARCH64_RELATIVE addends.
  3. The hook instruction is found by exact signature match inside that one
     function's ARM64 extent. Anything less than an exact match is refused.

The BSS flag block is placed at the PAGE-ALIGNED end of .data plus the existing
bssSize, so nothing that already exists moves. Using the raw end of .data put
scratch bytes 0x328 into live BSS on an earlier build and corrupted the game
while every verification gate still passed -- hence the explicit assertion here.
"""
import argparse, os, pprint, re, struct, sys

import a64 as A
import nxmap
import ff7nx_dispatch as D


def resolve_chain(exe_path):
    os.environ['FF7_EXE'] = exe_path
    for m in ('ff7nx_chain',):
        sys.modules.pop(m, None)
    import ff7nx_chain
    return ff7nx_chain.S


def bss_base(main):
    """
    Module offset of the first byte past the existing BSS.

    BSS begins at the page-aligned end of .data (empirically confirmed: the raw
    end is 0x328 bytes short of it, and using the raw end corrupted a build).
    """
    data_mem, data_size = main.segs[2][1], main.segs[2][2]
    raw_end = data_mem + data_size
    page_end = (raw_end + 0xFFF) & ~0xFFF
    if page_end - raw_end >= 0x1000:
        raise SystemExit('ABORT  .data end 0x%X page-aligns to 0x%X -- '
                         'unexpected' % (raw_end, page_end))
    return page_end + main.bss, page_end, raw_end


def mask_bits_for(slots):
    """Smallest power-of-two block that contains `slots`, as a bit count."""
    b = 1
    while (1 << b) < slots:
        b += 1
    return b


RE_NAME_ADDR = re.compile(r'_((?:sub_)?)([0-9A-F]{6})$')

# FFNx derives g_is_battle_paused this way in ff7_data.h:
#     ff7_externals.g_is_battle_paused =
#         (byte*)get_absolute_value(ff7_externals.run_animation_script, 0xA);
PAUSED_FROM = ('run_animation_script', 0xA)

# These three field sites were established by separate x86/ARM data-flow
# traces.  They are not children of the battle-dispatcher chain resolved by
# locate_all(), but they live in the same generated module because the build
# consumes one verified site catalogue.  Keeping their source here prevents a
# dispatcher refresh from silently deleting field-wait/blink support.
MANUAL_FIELD_WAIT_SITES = {
    'field-wait': {
        'what': 'field script opcode 0x24 (WAIT) frame counts -- scripted set pieces, alarms, timed NPC business',
        'fn_x86': 0x610818, 'fn_arm': 0x951A80,
        'opcode_table': 0x9055A0, 'opcode_index': 0x24,
        'wait_frames': 0xCC0900, 'x86_store': 0x6108B9,
        'hook': 0x951D00, 'displaced': 0x79000014, 'val_reg': 0x14,
        'sig': [(-40, 0x2A1303E0), (-36, 0x790002A8),
                (-32, 0xB90006BF), (-28, 0x941EA9AF),
                (-24, 0x39400008), (-20, 0x390012A8),
                (-16, 0xB94006A8), (-12, 0x794002B4),
                (-8, 0x0B080700), (-4, 0x941EA9A9),
                (0, 0x79000014)],
    },
}

MANUAL_FIELD_BLINK_SITES = {
    'test': {
        'what': 'blink test: counter == 0 -> counter <= 0',
        'fn_x86': 0x6392BB, 'fn_arm': 0x949B60,
        'counter': 0xCC167A, 'hook': 0x094B5B8,
        'displaced': 0x34000514, 'val_reg': 0x14,
        'blink_arm': 0x0094B658,
        'sig': [(-24, 0x11264260), (-20, 0x7100029F),
                (-16, 0x1A9F17E8), (-12, 0xB9000AB4),
                (-8, 0x39008EA8), (-4, 0x941EC37B),
                (0, 0x34000514), (4, 0x3900001A)],
    },
    'hold': {
        'what': 'blink reload: hold shut one more frame before reloading',
        'fn_x86': 0x6392BB, 'fn_arm': 0x949B60,
        'counter': 0xCC167A, 'hook': 0x094B704,
        'displaced': 0x7900001B, 'val_reg': 0x1B,
        'sig': [(-24, 0x0B081108), (-20, 0x794002BB),
                (-16, 0x531D7108), (-12, 0x0B160100),
                (-8, 0xB90006A8), (-4, 0x941EC328),
                (0, 0x7900001B), (4, 0x2A1403E0)],
    },
}


def exclusion_addresses(names, sym, main, tag):
    """
    Turn FFNx symbol names into guest addresses, with both cross-checks.

    None of the decorator-set members derive through the chain this tree
    evaluates, so the address has to come from the hex suffix FFNx encoded into
    the name. That is weaker footing than a derivation, so it is checked twice:

      * the address must be a KEY in the 0x126D3A8 recompilation map, i.e. a
        real x86 function entry point that has a translated ARM64 body. A
        transcription error lands mid-function or nowhere and is caught here.
      * where the chain DOES derive the same symbol, the two must agree.

    A single failure aborts. An exclusion list that is quietly one entry short
    means one function gets throttled that FFNx runs at full rate, which is the
    hardest possible thing to notice from a play test.
    """
    out = []
    for name in names:
        m = RE_NAME_ADDR.search(name)
        if not m:
            raise SystemExit('REFUSED %s throttle: %s has no address suffix, '
                             'so there is nothing to check it against'
                             % (tag, name))
        va = int(m.group(2), 16)
        if va not in main.x86_to_arm:
            raise SystemExit('REFUSED %s throttle: %s -> 0x%X is not a function '
                             'entry in the recompilation map -- transcription '
                             'error' % (tag, name, va))
        if name in sym and sym[name] != va:
            raise SystemExit('REFUSED %s throttle: %s derives to 0x%X through '
                             'the chain but its name says 0x%X'
                             % (tag, name, sym[name], va))
        out.append((name, va))
    seen = {}
    for name, va in out:
        if va in seen:
            raise SystemExit('REFUSED %s throttle: %s and %s are both 0x%X'
                             % (tag, seen[va], name, va))
        seen[va] = name
    return out


def locate_all(exe_path, nso_path, verbose=True, throttle_one_call=True):
    sym = resolve_chain(exe_path)
    main = nxmap.Main(nso_path)
    text = main.text
    flag0, page_end, raw_end = bss_base(main)

    out = {'flag_base': flag0, 'bss_page_end': page_end,
           'data_raw_end': raw_end, 'text_size': main.segs[0][2],
           'rodata': main.segs[1][1], 'dispatchers': {}}

    # Lay the flag blocks out back to back, each a power-of-two size so the
    # masked index can never leave its own block.
    cursor = flag0
    grow = 0
    for tag in ('effect10', 'effect100', 'camera'):
        spec = D.DISPATCHERS[tag]
        mb = mask_bits_for(spec['slots'])
        size = 1 << mb
        spec['_flag'] = cursor
        spec['_mask_bits'] = mb
        cursor += size
        grow += size
    # Keep bssSize growth 16-byte aligned; the loader zero-fills either way.
    grow = (grow + 0xF) & ~0xF
    out['bss_grow'] = grow

    # The throttle blocks go AFTER the flag blocks, and BSS_GROW keeps its old
    # value. A build with no throttle group therefore grows bssSize by exactly
    # what it grew it by before, and the confirmed baseline still reproduces
    # byte for byte -- which is the only way an A/B test of this change means
    # anything.
    # effect60 gets no first-frame flag block: this tree implements no
    # arithmetic arms for it, so nothing would ever read one.
    D.DISPATCHERS['effect60']['_flag'] = None
    D.DISPATCHERS['effect60']['_mask_bits'] = mask_bits_for(
        D.DISPATCHERS['effect60']['slots'])

    tcursor = flag0 + grow
    if tcursor & 7:
        raise SystemExit('ABORT  throttle scratch base 0x%X is not 8-byte '
                         'aligned; it holds a host pointer' % tcursor)
    tgrow = 0
    for tag in ('effect100', 'effect60'):
        spec = D.DISPATCHERS[tag]
        mb = mask_bits_for(spec['slots'])
        n = 1 << mb                                  # one counter byte per slot
        spec['_throttle_mask_bits'] = mb
        spec['_throttle'] = dict(ctr=tcursor, pptr=tcursor + n,
                                 saved=tcursor + n + 8, did=tcursor + n + 9)
        size = (n + 16 + 0xF) & ~0xF
        spec['_throttle_size'] = size
        tcursor += size
        tgrow += size
    # ff7nx_analog owns the next 0x20 bytes.  Reserve that established block,
    # then place the KOTR FixCounterException state after it so regeneration
    # cannot silently overlap the two independently enabled features.
    tcursor += 0x20
    tgrow += 0x20
    kbase = tcursor
    D.DISPATCHERS['effect100']['_throttle'].update(
        kotr_exc=kbase, kotr_ptr=kbase + 0x80,
        kotr_active=kbase + 0x88, kotr_counter=kbase + 0x8A,
        kotr_disable=kbase + 0x8C)
    tcursor += 0x90
    tgrow += 0x90
    # Ten summon model decorators: one byte phase/final per effect100 slot and
    # two signed-short vector3 endpoints per slot.  The three scalar values
    # hand state from the pre cave to the immediately following post cave.
    mbase = tcursor
    D.DISPATCHERS['effect100']['_throttle'].update(
        model_idx=mbase,
        model_ptr=mbase + 0x08,
        model_active=mbase + 0x10,
        model_phase=mbase + 0x12,
        model_final=mbase + 0x76,
        model_prev=mbase + 0xDA,
        model_next=mbase + 0x332,
        model_pos=0xBE63A2)
    tcursor += 0x590
    tgrow += 0x590
    out['throttle_base'] = flag0 + grow
    out['bss_grow_throttle'] = grow + tgrow
    out['paused_guest'] = sym['g_is_battle_paused']

    for tag in ('effect10', 'effect100', 'camera', 'effect60'):
        spec = D.DISPATCHERS[tag]
        d_x86 = sym[spec['fn']]
        a_x86 = sym[spec['add']]
        arr = sym[spec['arr']]
        data = sym[spec['data']]

        d_start, d_end = main.extent(d_x86)
        a_start, a_end = main.extent(a_x86)
        try:
            dh = D.find_dispatcher_hook(text, d_start, d_end, arr,
                                        spec['slots'])
        except D.Refused as e:
            raise SystemExit('REFUSED %s dispatcher: %s' % (tag, e))
        try:
            ah = D.find_addfn_hook(text, a_start, a_end, arr)
        except D.Refused as e:
            raise SystemExit('REFUSED %s add_fn: %s' % (tag, e))

        cases = []
        for name, ops, guard in spec['cases']:
            if name not in sym:
                raise SystemExit('REFUSED %s: %s did not resolve through the '
                                 'chain' % (tag, name))
            cases.append((name, sym[name], ops, guard))

        thr = None
        if tag in D.THROTTLE:
            t = D.THROTTLE[tag]
            und = sym[t['undecorated']] if t['undecorated'] else None
            if t['undecorated'] and t['undecorated'] not in sym:
                raise SystemExit('REFUSED %s throttle: %s did not resolve '
                                 'through the chain' % (tag, t['undecorated']))
            try:
                ts = D.find_throttle_sites(text, d_start, d_end, arr, und,
                                           t['n_undecorated'])
            except D.Refused as e:
                raise SystemExit('REFUSED %s throttle: %s' % (tag, e))
            names = list(t['exclude'])
            if not throttle_one_call:
                names += list(t['one_call'])
            excl = exclusion_addresses(names, sym, main, tag)
            # Every slot function FFNx names AND throttles. Not needed to build
            # the table -- allow-by-default already covers them -- but these are
            # what a bisect moves across with --throttle-exclude, so they are
            # resolved and map-checked here rather than at build time, where
            # the recompilation map is not available.
            known = [n for n in (list(t['throttled']) + list(t['one_call']))
                     if n not in set(names)]
            ts.update(spec['_throttle'])
            ts['mask_bits'] = spec['_throttle_mask_bits']
            ts['size'] = spec['_throttle_size']
            ts['exclude'] = excl
            ts['candidates'] = exclusion_addresses(known, sym, main, tag)
            # The narrow allow-list: the only effect60 slots the aura system
            # needs throttled. Same map cross-check as everything else.
            ts['aura_allow'] = (exclusion_addresses(D.EFFECT60_AURA_THROTTLE,
                                                    sym, main, tag)
                                if tag == 'effect60' else [])
            ts['one_call_throttled'] = throttle_one_call
            if tag == 'effect100':
                missing = [(n, va) for n, va, _exc in
                           D.KOTR_COUNTER_EXCEPTIONS
                           if va not in main.x86_to_arm]
                if missing:
                    raise SystemExit('REFUSED KOTR counter functions are not '
                                     'entries in the recompilation map: %r'
                                     % missing)
                ts['kotr'] = list(D.KOTR_COUNTER_EXCEPTIONS)
                missing = [(n, va) for n, va, _marker in
                           D.SUMMON_MODEL_INTERPOLATION
                           if va not in main.x86_to_arm]
                if missing:
                    raise SystemExit('REFUSED summon model functions are not '
                                     'entries in the recompilation map: %r'
                                     % missing)
                ts['model'] = list(D.SUMMON_MODEL_INTERPOLATION)
                ts['data_base'] = data
                ts['stride'] = spec['stride']
                # The stock no-registration path joins immediately before the
                # selected-slot return.  It is +0x6C from this exact hook in
                # 1.0.3; verify its full translated signature before emitting.
                ts['kotr_add_skip'] = ah['hook'] + 0x6C
                want = (0xB9401688, 0x51001100, 0x9425E424,
                        0x79400008, 0x17FFFFC2)
                got = tuple(struct.unpack_from('<I', text,
                                               ts['kotr_add_skip'] + 4 * i)[0]
                            for i in range(len(want)))
                if got != want:
                    raise SystemExit('REFUSED KOTR add suppression target '
                                     '+0x%X signature %r != %r'
                                     % (ts['kotr_add_skip'], got, want))
            thr = ts

        out['dispatchers'][tag] = dict(
            tag=tag, what=spec['what'], stride=spec['stride'],
            slots=spec['slots'], mask_bits=spec['_mask_bits'],
            flag=spec['_flag'], unhandled=spec['unhandled'],
            data_base=data, array_fn=arr,
            disp_x86=d_x86, disp_arm=d_start,
            add_x86=a_x86, add_arm=a_start,
            disp_hook=dh, add_hook=ah, cases=cases,
            fields=spec['fields'], throttle=thr)

        if verbose:
            print('%-10s %s' % (tag, spec['what']))
            print('    dispatcher  x86 0x%06X -> arm64 0x%06X'
                  % (d_x86, d_start))
            print('      hook      +0x%06X  displaced %08X  cmp w%d,#0  '
                  'ctx x%d  idx@[ctx+0x%X] (%s)'
                  % (dh['hook'], dh['displaced'], dh['fn_reg'], dh['ctx_reg'],
                     dh['idx_off'], D.CTX_SLOT_NAMES[dh['idx_off']]))
            print('    add_fn      x86 0x%06X -> arm64 0x%06X'
                  % (a_x86, a_start))
            print('      hook      +0x%06X  displaced %08X  str w%d,[x0]  '
                  'ctx x%d  idx@[ctx+0x%X] (%s)'
                  % (ah['hook'], ah['displaced'], ah['fn_val_reg'],
                     ah['ctx_reg'], ah['idx_off'],
                     D.CTX_SLOT_NAMES[ah['idx_off']]))
            print('    array_fn 0x%X  array_data 0x%X  %d slots, stride 0x%X'
                  % (arr, data, spec['slots'], spec['stride']))
            if spec['_flag'] is None:
                print('    no first-frame flag block (no arithmetic arms here)')
            else:
                print('    flags at module +0x%X (%d bytes, mask 0x%X)'
                      % (spec['_flag'], 1 << spec['_mask_bits'],
                         (1 << spec['_mask_bits']) - 1))
            print('    %d arithmetic case(s) covered, %d decorator case(s) '
                  'not expressible' % (len(cases), spec['unhandled']))
            for name, va, ops, guard in cases:
                print('        0x%06X %-38s %s%s'
                      % (va, name,
                         ' '.join('%s%s' % (n, '*' if h == 'mul' else '/')
                                  for n, h in ops),
                         '   [if %s]' % guard if guard else ''))
            if thr:
                print('    pause-throttle')
                print('      indirect call +0x%06X -> thunk 0x%X  ctx x%d  '
                      'push reg w%d' % (thr['call_pc'], thr['thunk'],
                                        thr['ctx_reg'], thr['store_reg']))
                print('      pre  hook   +0x%06X  displaced %08X'
                      % (thr['pre'], thr['pre_displaced']))
                print('      post hook   +0x%06X  displaced %08X'
                      % (thr['post'], thr['post_displaced']))
                if thr['undecorated']:
                    print('      undecorated call site(s) left alone: %s'
                          % ', '.join('+0x%06X' % c
                                      for c in thr['undecorated']))
                print('      counters at module +0x%X (%d bytes, mask 0x%X), '
                      'pptr +0x%X saved +0x%X did +0x%X'
                      % (thr['ctr'], 1 << thr['mask_bits'],
                         (1 << thr['mask_bits']) - 1,
                         thr['pptr'], thr['saved'], thr['did']))
                print('      %d excluded function(s); one_call set is %s'
                      % (len(thr['exclude']),
                         'throttled' if thr['one_call_throttled']
                         else 'excluded'))
                for name, va in thr['exclude']:
                    print('        0x%06X %s' % (va, name))
            print()

    # ---- the camera script wait, opcode 0xF5 ---------------------------
    out['camera_wait'] = {}
    for tag, spec in D.CAMERA_WAIT.items():
        fn = sym[spec['fn']]
        arr = sym[spec['arr']]
        c_start, c_end = main.extent(fn)
        try:
            site = D.find_camera_wait_site(text, c_start, c_end, arr, tag)
        except D.Refused as e:
            raise SystemExit('REFUSED %s: %s' % (tag, e))
        site['what'] = spec['what']
        site['fn_x86'] = fn
        site['fn_arm'] = c_start
        site['array'] = arr
        out['camera_wait'][tag] = site
        if verbose:
            print('%-16s %s' % (tag, spec['what']))
            print('    interpreter x86 0x%06X -> arm64 0x%06X' % (fn, c_start))
            print('    array 0x%X, stride 0xE; current_position 0x%X, '
                  'frames_to_wait 0x%X'
                  % (arr, site['current_position'], site['frames_to_wait']))
            print('    opcode F5 operand fetched at +0x%06X, stored at '
                  '+0x%06X  displaced %08X  strh w%d,[x0]  ctx x%d'
                  % (site['operand_pc'], site['hook'], site['displaced'],
                     site['val_reg'], site['ctx_reg']))
            print()

    if verbose:
        print('g_is_battle_paused 0x%X  (get_absolute_value(%s, 0x%X))'
              % (sym['g_is_battle_paused'], PAUSED_FROM[0], PAUSED_FROM[1]))
        print('BSS: .data raw end 0x%X, page end 0x%X, bssSize 0x%X'
              % (raw_end, page_end, main.bss))
        print('     flag block at 0x%X, growing bssSize by 0x%X'
              % (flag0, grow))
        print('     throttle block at 0x%X, growing bssSize by 0x%X when any '
              'throttle group is on' % (out['throttle_base'],
                                        out['bss_grow_throttle']))
        print('.text 0x%X, .rodata 0x%X, %d bytes free for caves'
              % (main.segs[0][2], main.segs[1][1],
                 main.segs[1][1] - main.segs[0][2]))
    return out


def emit(path, info, exe_path, nso_path):
    with open(path, 'w') as f:
        f.write('# GENERATED by ff7nx_locate.py -- do not edit.\n')
        f.write('# exe %s  nso %s\n' % (exe_path, nso_path))
        f.write('#\n# Battle effect / camera dispatcher hook sites. Every\n'
                '# address here was derived through FFNx\'s chain and located\n'
                '# by exact instruction signature; see ff7nx_dispatch.py.\n\n')
        f.write('FLAG_BASE = 0x%X\n' % info['flag_base'])
        f.write('BSS_GROW  = 0x%X\n' % info['bss_grow'])
        f.write('BSS_PAGE_END = 0x%X\n' % info['bss_page_end'])
        f.write('\n# The pause-throttle scratch sits AFTER the flag blocks, so\n'
                '# FLAG_BASE and BSS_GROW keep the values a build without any\n'
                '# throttle group used, and that build still reproduces its old\n'
                '# md5 exactly. BSS_GROW_THROTTLE is what bssSize grows by when\n'
                '# any throttle group is enabled.\n')
        f.write('THROTTLE_BASE = 0x%X\n' % info['throttle_base'])
        f.write('BSS_GROW_THROTTLE = 0x%X\n' % info['bss_grow_throttle'])
        f.write('PAUSED_GUEST = 0x%X\n' % info['paused_guest'])
        f.write('\nDISPATCH_SITES = {\n')
        for tag, d in info['dispatchers'].items():
            f.write('  %r: {\n' % tag)
            for key in ('what', 'slots', 'stride', 'mask_bits', 'flag',
                        'unhandled', 'data_base', 'array_fn',
                        'disp_x86', 'disp_arm', 'add_x86', 'add_arm'):
                v = d[key]
                f.write('    %-12r: %s,\n'
                        % (key, ('0x%X' % v) if isinstance(v, int) else repr(v)))
            if d.get('throttle') is None:
                f.write('    %-12r: None,\n' % 'throttle')
            else:
                t = d['throttle']
                f.write('    %-12r: {\n' % 'throttle')
                for k in ('pre', 'pre_displaced', 'post', 'post_displaced',
                          'call_pc', 'translate_pc', 'thunk', 'ctx_reg',
                          'store_reg', 'ctr', 'pptr', 'saved', 'did',
                          'mask_bits', 'size'):
                    f.write('        %-16r: 0x%X,\n' % (k, t[k]))
                for k in ('kotr_exc', 'kotr_ptr', 'kotr_active',
                          'kotr_counter', 'kotr_disable', 'kotr_add_skip',
                          'model_idx', 'model_ptr', 'model_active',
                          'model_phase', 'model_final', 'model_prev',
                          'model_next', 'model_pos',
                          'data_base', 'stride'):
                    if k in t:
                        f.write('        %-16r: 0x%X,\n' % (k, t[k]))
                f.write('        %-16r: %r,\n'
                        % ('one_call_throttled', t['one_call_throttled']))
                for k in ('pre_sig', 'post_sig'):
                    f.write('        %-16r: [%s],\n'
                            % (k, ', '.join('(%d, 0x%08X)' % (o, w)
                                            for o, w in t[k])))
                f.write('        %-16r: [%s],\n'
                        % ('undecorated',
                           ', '.join('0x%X' % c for c in t['undecorated'])))
                f.write('        %-16r: [%s],\n'
                        % ('undecorated_words',
                           ', '.join('0x%08X' % c
                                     for c in t['undecorated_words'])))
                for key in ('exclude', 'candidates', 'aura_allow'):
                    f.write('        %-16r: [\n' % key)
                    for name, va in t[key]:
                        f.write('            (%r, 0x%X),\n' % (name, va))
                    f.write('        ],\n')
                if t.get('kotr'):
                    f.write('        %-16r: [\n' % 'kotr')
                    for name, va, exc in t['kotr']:
                        f.write('            (%r, 0x%X, %d),\n'
                                % (name, va, exc))
                    f.write('        ],\n')
                if t.get('model'):
                    f.write('        %-16r: [\n' % 'model')
                    for name, va, marker in t['model']:
                        f.write('            (%r, 0x%X, %d),\n'
                                % (name, va, marker))
                    f.write('        ],\n')
                f.write('    },\n')
            for key in ('disp_hook', 'add_hook'):
                h = d[key]
                f.write('    %-12r: {\n' % key)
                for k, v in sorted(h.items()):
                    if k == 'sig':
                        f.write('        %-11r: [%s],\n'
                                % (k, ', '.join('(%d, 0x%08X)' % (o, w)
                                                for o, w in v)))
                    else:
                        f.write('        %-11r: 0x%X,\n' % (k, v))
                f.write('    },\n')
            f.write('    %-12r: {\n' % 'fields')
            for k, v in sorted(d['fields'].items()):
                f.write('        %-12r: (0x%02X, %d, %r),\n'
                        % (k, v[0], v[1], v[2]))
            f.write('    },\n')
            f.write('    %-12r: [\n' % 'cases')
            for name, va, ops, guard in d['cases']:
                f.write('        (%r, 0x%X, %r, %r),\n'
                        % (name, va, ops, guard))
            f.write('    ],\n  },\n')
        f.write('}\n')

        f.write('\n# Opcode 0xF5 -- "wait N frames" -- in the two battle camera\n'
                '# script interpreters. This is what paces the magic, limit\n'
                '# break, summon and enemy-attack cameras; they are script\n'
                '# driven, not slot-function driven, which is why neither\n'
                '# camera-scale nor the pause-throttle could reach them.\n')
        f.write('CAMERA_WAIT_SITES = {\n')
        for tag, s in info['camera_wait'].items():
            f.write('  %r: {\n' % tag)
            for k in ('what',):
                f.write('    %-18r: %r,\n' % (k, s[k]))
            for k in ('fn_x86', 'fn_arm', 'array', 'current_position',
                      'frames_to_wait', 'hook', 'displaced', 'val_reg',
                      'ctx_reg', 'operand_pc'):
                f.write('    %-18r: 0x%X,\n' % (k, s[k]))
            f.write('    %-18r: [%s],\n'
                    % ('sig', ', '.join('(%d, 0x%08X)' % (o, w)
                                        for o, w in s['sig'])))
            f.write('  },\n')
        f.write('}\n')
        f.write('\n# Separately traced field timing sites; retained by the generator.\n')
        f.write('FIELD_WAIT_SITES = %s\n'
                % pprint.pformat(MANUAL_FIELD_WAIT_SITES, width=100,
                                 sort_dicts=False))
        f.write('\nFIELD_BLINK_SITES = %s\n'
                % pprint.pformat(MANUAL_FIELD_BLINK_SITES, width=100,
                                 sort_dicts=False))
    print('wrote %s' % path)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--exe', required=True)
    ap.add_argument('--nso', required=True)
    ap.add_argument('--emit', metavar='FILE')
    a = ap.parse_args()
    info = locate_all(a.exe, a.nso)
    if a.emit:
        emit(a.emit, info, a.exe, a.nso)
    return 0


if __name__ == '__main__':
    sys.exit(main())
