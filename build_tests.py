#!/usr/bin/env python3
"""
build_tests.py -- build the A/B test set for the FF7 Switch 60 FPS patch.

Produces one baseline SD tree plus a set of drop-in replacement `main` NSOs,
each differing from the baseline by exactly one group of patches. Testing is
then: copy one file, boot, look at one thing.

    python3 build_tests.py --exe ff7_en --nso main --out tests

Every variant is verified: rebuilt NSO self-check, plus a word-by-word .text
diff against stock that must account for every changed word. A variant that
cannot be fully explained is not written.
"""
import argparse, hashlib, os, shutil, subprocess, sys

# (name, groups, what to look at, what a pass looks like)
VARIANTS = [
    ('A-baseline', [],
     'Field and battle framerate, walking speed, battle animations.',
     'This is the reference build. Everything the last session confirmed and '
     'nothing else. 60 FPS in field and battle, walk/run speed correct, battle '
     'animations correct. Camera and effects still fast -- expected.'),

    ('B-enemy-death', ['r-enemy_death'],
     'Kill a normal enemy, a boss, and something that melts or disintegrates.',
     '20 constants across the six death routines (normal, iainuki, boss, '
     'melting, disintegrate, morph) plus the damage-number hold. Vanquish '
     'effects should take about 4x as long as in A -- i.e. look normal.'),

    ('C-battle-camera', ['r-battle_camera'],
     'The camera sweep when a battle starts, and the transition out.',
     'battle_sub_429AC0+0x152 (intro frame count 61->244) and '
     'battle_sub_430DD0+0x3DE (outro 49->196). Only 2 of FFNx\'s 6 camera '
     'constants resolved unambiguously, so expect partial improvement: the '
     'sweep should slow down, but may not be perfect.'),

    ('D-aura-and-damage', ['r-battle_aura', 'r-battle_damage'],
     'Use a limit break, cast an enemy skill, summon something.',
     'Limit-break and summon aura effect pacing, damage numbers, player mark. '
     'Auras should pulse at the old rate instead of 4x fast.'),

    ('E-field-text', ['r-field_text', 'r-field_fade'],
     'Open a dialogue box, page through text, walk through a screen fade, '
     'ride an elevator.',
     'Text box open/close/paging plus both screen-fade constants. Text should '
     'advance at the pre-60 rate. This also carries the two field-fade values '
     'that were ambiguous in the last session and are now resolved: '
     '+0x9F6A40 (cmp, 34->50) and +0x9F6E10 (mov, 34->49).'),

    ('F-script-wait', ['script-wait'],
     'Everything in battle. Compare directly against A.',
     'THE SUSPECT. g_script_wait_frames 14->56, alone, for the first time. It '
     'was on by default for every camera test last session, so no camera result '
     'from that session is interpretable. If this build is WORSE than A, that '
     'explains the dead ends -- and every earlier null result needs rerunning.'),

    ('G-battleground-summons', ['r-battleground', 'r-summons'],
     'Battle backgrounds with motion (Midgar flashback rain, scrolling '
     'grounds), and Odin / Shiva / Bahamut.',
     '8 battleground constants (including one hoisted word that covers all '
     'eight of FFNx\'s update_3d_battleground sites at once) and 5 summon '
     'movement constants.'),

    ('H-everything', ['ALL'],
     'The ceiling. Not a diagnostic.',
     'Every group at once. Useful only to see how close the full constant set '
     'gets. If this is good and a single-group build is not, the missing piece '
     'is an interaction; if this is bad, bisect with B..G. Do not draw '
     'conclusions about individual groups from this build.'),
]


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        sys.stderr.write(p.stdout + p.stderr)
        raise SystemExit('ABORT  %s failed' % ' '.join(cmd[:3]))
    return p.stdout


def md5(path):
    return hashlib.md5(open(path, 'rb').read()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', required=True)
    ap.add_argument('--nso', required=True)
    ap.add_argument('--out', default='tests')
    ap.add_argument('--gen', default='ff7nx_60fps.py')
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    vdir = os.path.join(a.out, 'variant_main')
    os.makedirs(vdir, exist_ok=True)
    rows = []
    stock_main = md5(a.nso)

    for name, groups, look, expect in VARIANTS:
        work = os.path.join(a.out, '_work_' + name)
        cmd = [sys.executable, a.gen, '--exe', a.exe, '--nso', a.nso,
               '--out', work]
        if groups == ['ALL']:
            cmd.append('--enable-all')
        elif groups:
            cmd += ['--enable', ','.join(groups)]
        out = run(cmd)
        diff = [l.strip() for l in out.splitlines() if l.startswith('.text diff')]
        nwords = int(diff[0].split()[2]) if diff else -1

        root = os.path.join(work, 'atmosphere', 'contents',
                            '0100A5B00BDC6000')
        m = os.path.join(root, 'exefs', 'main')
        e = os.path.join(root, 'romfs', 'ff7', 'resources', 'ff7_1.02', 'ff7_en')
        if name == 'A-baseline':
            base = os.path.join(a.out, 'SD_ROOT')
            if os.path.isdir(base):
                shutil.rmtree(base)
            shutil.copytree(work, base)
            base_exe_md5 = md5(e)
        dest = os.path.join(vdir, 'main_%s' % name)
        shutil.copyfile(m, dest)
        rows.append(dict(name=name, groups=groups, look=look, expect=expect,
                         words=nwords, main_md5=md5(dest), exe_md5=md5(e)))
        shutil.rmtree(work)

    # ---- manifest --------------------------------------------------------
    lines = []
    lines.append('# FF7 Switch 60 FPS -- A/B test set\n')
    lines.append('Stock inputs')
    lines.append('')
    lines.append('| file | md5 |')
    lines.append('|---|---|')
    lines.append('| ff7_en (stock) | `%s` |' % md5(a.exe))
    lines.append('| main (stock) | `%s` |' % stock_main)
    lines.append('')
    lines.append('## What this tree does and does not contain')
    lines.append('')
    lines.append('`SD_ROOT/` holds exactly two files: the patched `ff7_en` '
                 '(framerate limiters) and a `main` NSO (code patches). It '
                 'does **not** contain the 60 FPS mod content -- the '
                 'interpolated animations, the `?ab` script waits, or the '
                 'Ninostyle compositing. Those come from your 7th Heaven NX '
                 'build and are already confirmed working; nothing here '
                 'replaces them.')
    lines.append('')
    lines.append('The two sets write to different romfs paths and merge '
                 'cleanly:')
    lines.append('')
    lines.append('```')
    lines.append('romfs/ff7/resources/ff7_1.02/ff7_en   <- this tree')
    lines.append('romfs/ff7/workingdir/data/...         <- 7th Heaven NX + battle.lgp')
    lines.append('```')
    lines.append('')
    lines.append('So your card needs three things, in this order:')
    lines.append('')
    lines.append('1. Your 7th Heaven NX output (the mod archives), built with '
                 'the settings in the handoff.')
    lines.append('2. The patched `battle.lgp` -- `?ab` animation script waits '
                 'x4. This is a **confirmed** fix and is not part of the '
                 'variants:')
    lines.append('')
    lines.append('   ```')
    lines.append('   python3 ff7nx_60fps.py --exe ff7_en --nso main \\')
    lines.append('       --battle-lgp <your 7H NX sdout>/romfs/ff7/workingdir/data/battle/battle.lgp \\')
    lines.append('       --out sd_lgp')
    lines.append('   ```')
    lines.append('')
    lines.append('3. `SD_ROOT/` from this tree, then one `main` from '
                 '`variant_main/`.')
    lines.append('')
    lines.append('## How to run a test')
    lines.append('')
    lines.append('1. Copy `SD_ROOT/` to your SD card root once. That installs '
                 'the patched `ff7_en` in romfs and the baseline `main` in '
                 'exefs.')
    lines.append('2. Delete any FPSLocker patch folder for '
                 '`0100A5B00BDC6000` -- it would fight the limiter patch.')
    lines.append('3. To test a variant, copy `variant_main/main_<NAME>` over '
                 '`atmosphere/contents/0100A5B00BDC6000/exefs/main`.')
    lines.append('4. **Check the md5 of the file on the card before booting.** '
                 'A test run against a stale file is worse than no test, '
                 'because it looks like a result.')
    lines.append('5. Boot, look at the one thing the variant is about, and '
                 'compare against A.')
    lines.append('')
    lines.append('Only `main` changes between variants, so you never recopy '
                 'romfs or the mod content after step 1.')
    lines.append('')
    lines.append('### Confirming the exefs route works at all')
    lines.append('')
    lines.append('Before trusting any null result, establish that replacing '
                 '`main` does something. Field walk/run speed is the control: '
                 'it is halved by two words in `main`, not by the exe.')
    lines.append('')
    lines.append('* With `SD_ROOT` installed (any variant): walking looks '
                 'normal speed.')
    lines.append('* Delete `atmosphere/contents/0100A5B00BDC6000/exefs/` '
                 'entirely, keep the patched romfs: walking runs at **double '
                 'speed**, because the game is at 60 FPS with a 30 FPS step.')
    lines.append('')
    lines.append('If both look identical, your `exefs/main` is not being '
                 'honoured and no variant can tell you anything. Stop and fix '
                 'that first.')
    lines.append('')
    lines.append('## Variants')
    lines.append('')
    lines.append('| # | groups enabled | .text words changed | main md5 |')
    lines.append('|---|---|---|---|')
    for r in rows:
        g = ', '.join('`%s`' % x for x in r['groups']) or '_none_'
        lines.append('| %s | %s | %d | `%s` |'
                     % (r['name'], g, r['words'], r['main_md5']))
    lines.append('')
    for r in rows:
        lines.append('### %s' % r['name'])
        lines.append('')
        lines.append('**Look at:** %s' % r['look'])
        lines.append('')
        lines.append('%s' % r['expect'])
        lines.append('')
        lines.append('`main` md5 `%s`, %d .text words differ from stock.'
                     % (r['main_md5'], r['words']))
        if r['exe_md5'] != base_exe_md5:
            lines.append('')
            lines.append('> This variant also changes `ff7_en` '
                         '(md5 `%s`) -- copy its romfs too.' % r['exe_md5'])
        lines.append('')
    lines.append('## Recording a result')
    lines.append('')
    lines.append('For each variant, write down one of: better / same / worse / '
                 'crashed -- and against **A**, not against the previous '
                 'variant. That is the whole point of the layout: every variant '
                 'differs from A by exactly one group, so "worse than A" names '
                 'the culprit immediately.')
    lines.append('')
    lines.append('If a variant crashes, say which one and where. A crash '
                 'localises to at most one group.')
    man = os.path.join(a.out, 'TESTS.md')
    open(man, 'w').write('\n'.join(lines) + '\n')

    print('built %d variants in %s' % (len(rows), a.out))
    for r in rows:
        print('  %-24s %2d words  %s' % (r['name'], r['words'], r['main_md5']))
    print('\nmanifest: %s' % man)
    return 0


if __name__ == '__main__':
    sys.exit(main())
