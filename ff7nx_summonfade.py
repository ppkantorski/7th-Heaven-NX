"""Summon party fade -- the tint that puts it on the enemy-death render path.

WHAT IS MEASURED, NOT INFERRED
==============================
Both halves of the game-side fade were EXECUTED out of the shipped `main` with
arm64emu against a synthetic guest memory, so none of the following is a
reading of the disassembly -- it is what the module actually does:

  battle_sub_42C31C(actor 0), g_script_wait_frames = 56
      registers battle_sub_42C66D, slot n_frames = 56,
      alpha step = 73, RGB steps = 0/0/0, field_14 = 0,
      field_3F blend bit set, battle_sub_42C2D7 called once.

  battle_sub_42C66D x60
      frame  1  field_14=  73  alpha=251
      frame 28  field_14=2044  alpha=127
      frame 56  field_14=4088  alpha=  0
      frame 57  hidden (field_25 |= 4), slot retired.

  run_summon_animations_5C0E4B x70
      phase 1 counts 56 -> 0 and hides the party (field_25 |= 2) at frame 58.

So the ramp is a smooth 56-step 251 -> 0, the party stays drawable for every
one of those frames, and the hide lands two frames after the ramp ends. **No
timing constant can be responsible for the abrupt vanish.** That closes off the
whole family of window/divisor/throttle explanations for good.

WHAT THE RAMP DRIVES
====================
  battle_sub_42F3E8              draws the battle model, every frame
      alpha = get_alpha_from_transparency_429343(field_14)   ; 255 - (t>>4)
      ctx->byte[0xB] = alpha
      ctx->byte[0]   = a RENDER PRESET, from three tests:
                         blend bit (field_3F & 8) / tinted / actorID == 3

  battle_sub_68D2B8              switch (ctx->byte[0]), table at 0x68D643
      9    tinted,   actor 3  -> 68F19E(a, 1-a, tint, ...)     LERP
      0x0A untinted, actor 3  -> 68F0F8(a, ...)                MULTIPLY
      0x0B tinted,   other    -> 68F413(a, 1-a, tint, ...)     LERP
      0x0C untinted, other    -> 68F2D6(a, ...)                MULTIPLY

  the leaves
      untinted: dst[0..2] = src[0..2] * a                      (68EFCE)
      tinted:   dst[0..2] = src[0..2] * a + tint[0..2] * (1-a) (68F079/68F00F)

`dst` is the live byte colour the renderer shades from; `src` is the material's
pristine float colour, so the write is idempotent and re-done every frame.
There is no fourth byte in either leaf -- the fade has never been an alpha
blend.

TWO THEORIES ALREADY DISPROVED ON HARDWARE. DO NOT RETRY THEM.
==============================================================
1. "The RGB steps truncate to zero" (build 216). They do -- but the numerators
   ARE zero for a party member (battle-init loop at x86 0x42CA34 zeroes
   field_28/29/2A), so 0/56 and 0/14 are both 0. The correction also gated
   itself on g_is_battle_paused, which the effect100/effect60 pause-throttle
   only sets around ITS OWN slot calls, while 42C66D is an effect10 slot
   (DISPATCH_SITES['effect10']['throttle'] is None). Dead code over a no-op.

2. "Preset 0x0C's vertex-buffer path does not reach the screen" (build 218).
   Moving the fading model to preset 0x0A -- the same handler minus that path
   -- changed nothing on hardware. And it could not have been right anyway:
   battle_sub_68F413, the preset 0x0B handler that every enemy death uses
   successfully, has the IDENTICAL two-path structure. If the vertex-buffer
   path were broken, enemy deaths would be broken too.

THE ONE STRUCTURAL DIFFERENCE LEFT
==================================
Against the working control the user keeps pointing at -- the enemy vanquish --
the party fade differs in exactly one mechanical way, and it is not timing:

  enemy death (5BBD24)   field_28/29/2A = 0xF8,0,0   TINTED   -> preset 0x0B
                         field_14 0x800 -> 0xF00              -> LERP leaf
  party fade  (42C31C)   field_28/29/2A = 0,0,0      UNTINTED -> preset 0x0C
                         field_14 0 -> 0x1000                 -> MULTIPLY leaf

The multiply leaf writes `dst = src * a`. If a model's materials carry no
colour of their own -- pure-texture materials, which is exactly what a
replacement model pack like Ninostyle Battle tends to author -- then `src` is
already ~0, `src * a` is 0 for every a, and the multiply is invisible no matter
how correct the ramp is. The LERP leaf writes `dst = src*a + tint*(1-a)`, whose
second term does not depend on `src` at all, so it moves `dst` regardless.

That single hypothesis is the only one that accounts for ALL of the evidence,
including the two failed builds:

  * enemy deaths fade (LERP, bright tint) but party members do not (MULTIPLY);
  * "small parts of Red XIII fade properly" -- the meshes that DO carry a
    material colour;
  * "other characters just instantly vanish" -- pure-texture materials;
  * moving between two MULTIPLY presets (0x0C -> 0x0A) changed nothing, which
    is precisely what it should do if the multiply itself is the problem.

WHAT THIS MODULE DOES
=====================
Gives the fading model a small non-zero tint, so it takes preset 0x0B and the
LERP leaf -- the same preset and the same leaf the enemy vanquish already uses
on this hardware -- and converges on near-black rather than on the enemy
death's red.

The tint is written by a padding cave at both fade branches of 42C31C,
immediately after field_14 is initialised. FADE_TINT is deliberately under 56
so that `field_28 / g_script_wait_frames` truncates to a step of zero and
battle_sub_42C66D's `sub al, step` leaves it exactly where the cave put it for
all 56 frames -- no drift, and none of the 8-bit wrap-around that made the
build-216 correction dangerous.

Blast radius: the caves sit inside 42C31C's two fade branches, so they run only
when a model is actually starting a fade. Enemy deaths do not go through
42C31C's stepper at all -- they have their own inline steppers in 5BBE32 and
friends -- so they are untouched. Worst case if the hypothesis is wrong: a
fading model carries tint 8/255, and preset 0x0B with alpha 255 computes
`dst = src*1.0 + 8*0.0 = src`, i.e. no visible change from today.

`--summon-fade-frames` (below) is unrelated and stays available; the measured
runs above say the default 56 is already correct, so leave it alone.
"""
from __future__ import annotations

import a64 as A


#: The LERP target the fading model converges on. Small and non-zero:
#:
#:   * non-zero puts `colour & 0xFFFFFF != 0`, which is what selects the TINTED
#:     preset -- 9 for actor 3, 0x0B otherwise -- and 0x0B is the preset every
#:     enemy death already uses successfully on this hardware;
#:   * small makes the LERP target near-black, so the model converges on black
#:     the way the stock fade intends rather than on the enemy death's red;
#:   * under 56 it makes `field_28 / g_script_wait_frames` truncate to a step
#:     of ZERO, so battle_sub_42C66D's `sub al, step` leaves the tint exactly
#:     where this cave put it for all 56 frames. No drift, and no 8-bit
#:     wrap-around -- the failure mode of the correction two sessions ago.
FADE_TINT = 8

#: `battle_sub_42C31C`, both fade branches, immediately after `field_14` is
#: initialised (x86 0x42C3B1 writes 0 for the fade-out, 0x42C504 writes 0x1000
#: for the fade-in). The displaced word is the same position-independent
#: `ldr w8, [x21, #0x14]` -- a reload of the guest frame pointer -- at both.
FADE_TINT_SITES = (
    dict(name='out', hook=0x000DB324, displaced=0xB94016A8,
         sig=((-0x08, 0x94408421),   # bl  translate      (-> &field_14)
              (-0x04, 0x7900001F),   # strh wzr, [x0]     ; field_14 = 0
              (0x00, 0xB94016A8),    # ldr  w8, [x21,#0x14]   <- displaced
              (0x04, 0x11002100))),  # add  w0, w8, #8    ; &[ebp+8]
    dict(name='in', hook=0x000DB774, displaced=0xB94016A8,
         sig=((-0x08, 0x321403E8),   # mov  w8, #0x1000
              (-0x04, 0x79000008),   # strh w8, [x0]      ; field_14 = 0x1000
              (0x00, 0xB94016A8),    # ldr  w8, [x21,#0x14]   <- displaced
              (0x04, 0x11002100))),  # add  w0, w8, #8
)

#: guest battle_model_state[0].field_28, and the array stride.
GUEST_FIELD_28 = 0xBE11A0
GUEST_STRIDE = 0x1AEC

#: the recompiler's guest -> host address translator.
TRANSLATE = 0x10FC3A0


def build_tint(cave, site, tint=FADE_TINT, addr=None):
    """Write field_28/29/2A = `tint` for the actor this fade is setting up.

    The actor id is re-read from the guest frame (`[ebp+8]`) rather than
    inferred from a live register, and each of the three bytes is translated
    separately, so nothing here assumes two guest addresses one byte apart
    share a host page -- the recompiler's translator is a page table, and
    `translate(p) + 1` is only `p + 1` while p stays inside its own 4 KB page.

    x19 is callee-saved, which is what lets the guest address survive the three
    translator calls; it is saved and restored here regardless. x30 likewise.
    Both exits leave SP where they found it.
    """
    if addr is None:
        addr = lambda i: cave + 4 * i
    w = [
        A.stp64_pre(19, 30, A.SP, -16),
        A.ldr(0, 21, 0x14),              # w0 = guest EBP
        A.add_imm(0, 0, 8),              # w0 = &[ebp+8]
        0,                               # bl translate
        A.ldrsh(19, 0),                  # w19 = actorID
        A.movz(0, GUEST_STRIDE),
        A.mul(19, 19, 0),                # w19 = actorID * 0x1AEC
        A.movz(0, GUEST_FIELD_28 & 0xFFFF),
        A.movk_hi(0, GUEST_FIELD_28 >> 16),
        A.add_reg(19, 19, 0),            # w19 = guest &field_28
        A.mov_reg(0, 19),
        0,                               # bl translate
        A.movz(1, tint),
        A.strb(1, 0),                    # field_28 = tint
        A.add_imm(0, 19, 1),
        0,                               # bl translate
        A.movz(1, tint),
        A.strb(1, 0),                    # field_29 = tint
        A.add_imm(0, 19, 2),
        0,                               # bl translate
        A.movz(1, tint),
        A.strb(1, 0),                    # field_2A = tint
        A.ldp64_post(19, 30, A.SP, 16),
        site['displaced'],
        0,                               # b hook+4
    ]
    for i in (3, 11, 15, 19):
        w[i] = A.bl(addr(i), TRANSLATE)
    w[24] = A.b(addr(24), site['hook'] + 4)
    return w


def tint_caves(tint=FADE_TINT):
    """The two padding-cave descriptors ff7nx_60fps hands to patch_nso."""
    out = []
    for src in FADE_TINT_SITES:
        site = dict(src)
        site['sig'] = [(rel, word) for rel, word in src['sig']]
        site['place'] = 'padding'
        out.append(dict(
            tag='summon-runtime', kind='summon_fade_tint', site=site,
            label='summon party fade-%s: tint %d so the fade takes the '
                  'enemy-death render path' % (src['name'], tint),
            build=lambda cave, addr=None, s=site, t=tint:
                build_tint(cave, s, t, addr)))
    return out


#: FFNx's value: the stock 14 times battle_frame_multiplier (4).
DEFAULT_FRAMES = 56

#: `battle_sub_42C31C` sign-extends the byte at guest 0xBFD0F0.  See the module
#: docstring -- 128 and above is not "a longer fade", it is a broken one.
MAX_FRAMES = 127

#: Stock word at all three sites: `orr w19, wzr, #14`.  The register is w19 at
#: each of them, which is why one encoder covers all three.
STOCK_WORD = 0x321F0BF3
_REG = 19

#: The three merged `#14` sites.  Each feeds a `strh` (the summon controller's
#: own slot lifetime) and a `strb` (g_script_wait_frames, which battle_sub_42C31C
#: reads as the fade window AND as the divisor for every fade step).  They are
#: supplied by the `script-wait` / `legacy` groups; this module only retunes the
#: value those groups write, so no second patch is ever created for the same word.
FADE_WINDOW_SITES = (
    0x007DF1FC,   # run_summon_animations_5C0E4B  -- party fade-out window
    0x007E1054,   # battle_sub_5C18BC             -- party fade-in window
    0x007E1D40,   # battle_sub_5C1C8F             -- summon-side fade window
)

#: Deliberately NOT retuned: x86 0x5D42A8 (Vincent's limit fade, +0x833800, a
#: different register) and x86 0x42A841 (battle init, +0x0D3994).  Those set the
#: default for every OTHER fade in the battle; moving them would change enemy
#: and limit-break fades that are not being investigated.
UNTOUCHED_SITES = (0x00833800, 0x000D3994)


def duration_word(frames: int) -> int:
    """The replacement word for a fade window of `frames` display frames."""
    check_frames(frames)
    return A.movz(_REG, frames)


def check_frames(frames: int) -> None:
    if not isinstance(frames, int):
        raise SystemExit('ABORT  --summon-fade-frames must be an integer')
    if not (1 <= frames <= MAX_FRAMES):
        raise SystemExit(
            'ABORT  --summon-fade-frames %d is outside 1..%d.\n'
            '       battle_sub_42C31C reads g_script_wait_frames with movsx '
            '(x86 0x42C377\n'
            '       and the four divisors at 0x42C3CC/0x42C3FA/0x42C428/'
            '0x42C444), so 128\n'
            '       and above read as NEGATIVE: the fade counter never '
            'retires and the\n'
            '       transparency step goes backwards, which shows on screen as '
            'the model\n'
            '       disappearing on the first frame.'
            % (frames, MAX_FRAMES))


def retune(nso_patches, frames: int, log=print):
    """Rewrite the three summon fade-window words in an assembled patch list.

    Returns a new list.  Every copy of a site is rewritten -- `script-wait` and
    `legacy` both carry these offsets and patch_nso aborts if two enabled
    patches disagree about one word, so they must move together.
    """
    check_frames(frames)
    if frames == DEFAULT_FRAMES:
        return list(nso_patches)
    new_word = duration_word(frames)
    out = []
    hit = 0
    for label, off, old, new in nso_patches:
        if off in FADE_WINDOW_SITES:
            if old != STOCK_WORD:
                raise SystemExit(
                    'ABORT  summon fade window at +0x%06X has stock word '
                    '%08X, expected %08X.\n'
                    '       The site moved -- refusing to retune it.'
                    % (off, old, STOCK_WORD))
            label = '%s  [fade window -> %d frames]' % (label, frames)
            new = new_word
            hit += 1
        out.append((label, off, old, new))
    if not hit:
        raise SystemExit(
            'ABORT  --summon-fade-frames needs the `script-wait` group (or '
            '--legacy):\n'
            '       none of +0x7DF1FC / +0x7E1054 / +0x7E1D40 is being '
            'patched, so there\n'
            '       is no fade window to retune.')
    log('  ok  summon party fade window %d -> %d frames (%.2f s at 60 FPS) '
        'at %d site(s)'
        % (DEFAULT_FRAMES, frames, frames / 60.0, hit))
    return out
