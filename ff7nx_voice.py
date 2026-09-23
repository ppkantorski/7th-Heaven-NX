"""
ff7nx_voice.py -- Echo-S's dialogue runtime: the ARM64 that decides which clip
plays, and when it stops.

WHAT THIS IS
============
`voicemod` works out what each of Echo-S's 15,505 clips is called; `voice_ogg`
puts them on the SD card in the one shape the native player accepts. This is
the half that makes the game ask for them.

There is no lookup table anywhere in the module. The MESSAGE opcode publishes
a key -- the field's own basename, the dialogue id, the page letter -- into a
small BSS block, and a per-frame service picks it up, builds the filename and
hands it to the same `NativeOggPlayer` the ambient bridge uses. A clip that
does not exist is an ordinary no-op, because the service probes the path and
closes the temporary decoder before it ever constructs a stateful player. That
is why the whole feature costs nothing from the contiguous table budget that
ambience and the SFX bridges compete over: 0xBC0 bytes of BSS and some
padding, and not one byte of .rodata tail.

WHAT CAME FROM WHERE
====================
The caves are the author's, carried over rather than rewritten. They are the
end of roughly a hundred hardware revisions -- v113 through v124 in his own
notes are all bug fixes to THESE sequences -- and the things they get right
are not things a reading can re-derive: which registers the recompiler leaves
live across each site, that the native OGG worker retains the filename pointer
after the hook returns (so the name lives in the player allocation, not on the
stack), and that a `ambient/NNNN` thread title overruns Horizon's buffer and
aborts in SetThreadNamePointer.

What the port changed is everything around them. His file is 5,954 lines, of
which 3,240 are hardware probes and rejected experiments -- deliberate UDF
traps to read a pointer out of a crash dump, six different player-lifecycle
theories, a dozen `patch_main_*_probe` entry points. Those were how the thing
got built and they are not how it ships. What is here is the closure of
`patch_main_clocked_dialogue`: 37 functions, and nothing that is not reachable
from the one call the build makes.

THE THREE CONTENDED SITES, AND WHY THEY ARE NOT A PROBLEM
=========================================================
Sixteen sites are hooked. Thirteen of them still hold their stock word in this
project's fully patched module and are taken outright. Three do not:

    0x947CF0   analog-360's field hook
    0x8FB20    the Cosmo Memory ambient battle entry
    0xF1E0EC   the Cosmo Memory ambient world stop

All three are "once per frame in this mode" services -- exactly what the voice
service is -- so they compose rather than conflict, and the author had already
built for it: each site is read before it is written, and if it holds a direct
branch rather than the stock word, that branch's target becomes the cave's
`resume_target`. The voice cave then ends by jumping to whoever was there
instead of replaying a displaced instruction. At 0x947CF0 the result is

    voice service -> analog-360 -> (replays its ldp, joins 0x947CF4)
                  -> ambient field tick -> stock code

with analog-360 and the ambient bridge unchanged, byte for byte.

The ordering that makes that work is not optional and it is not conventional:
`ff7nx_ambient` verifies the STOCK word at its three sites and refuses to
install over anything else, so ambient must be installed before this and this
must be installed after it. `7th_heaven_nx.py` runs them in that order and
says so; `test_voice_runtime.py` installs them in the wrong order and asserts
that the build stops rather than composing something silently wrong.

WHAT IS NOT HERE YET
====================
Battle barks and battle text are `battle_probability` and `battle_text`, both
off. They need `battle_voice`'s action-id table staged alongside them, and
that is the next piece; the producers are in this file and are installed the
moment those arguments are non-zero.
"""
import os
import struct

import a64 as A
import ff7nx_cave
import nxmap

import ff7nx_ambient as AM
import ff7nx_audio_cave as AC
import voice_ogg

# The shared assembler layer. The fork reached into its own ambient probe for
# these; this project already has them in one place, and they are the same
# helpers -- `ff7nx_audio_cave` exists precisely because five bridges had each
# grown a private copy.
Asm = AC.Asm
_pack = AC.pack
_segments = AC.segments
_ldp_off = AC.ldp_off
_stp_off = AC.stp_off
_mov64 = AC.mov64

# The native OGG player, shared with the ambient bridge. Same objects, same
# constructor, same 0xA0-byte allocation: a voice line and a location loop are
# two instances of the one thing, which is why they can duck against each
# other rather than fight.
MALLOC = AM.MALLOC
OGG_CTOR = AM.OGG_CTOR
OGG_PLAY = AM.OGG_PLAY
OGG_VOLUME = AM.OGG_VOLUME
OGG_DELETE = AM.OGG_DELETE
PLAYER_BYTES = AM.PLAYER_BYTES

# HOW LOUD A VOICE CLIP IS PLAYED, AND WHY IT IS NOT 1.0 BY ACCIDENT.
#
# FFNx does not play a voice at unity. `set_voice_volume()` in src/voice.cpp:
#
#     voice_volume = 2.0f + (100 - external_voice_music_fade_volume) / 100.0f
#
# which with the shipped fade of 25 is **2.75**, and it is passed to
# `nxAudioEngine.playVoice(name, window, voice_volume, ...)` UNCONDITIONALLY
# -- it is computed outside the `enable_voice_music_fade` branch, so it
# applies whether or not the music ducks. Echo-S is mixed against that.
#
# We played 1.0. Every line in every build so far has therefore been 8.8 dB
# below the level the mod was authored for, uniformly, which is why the
# encoder could measure every clip at exactly its target and the lines could
# still be hard to hear.
#
# We CANNOT simply copy 2.75. SoLoud runs a soft clipper (CLIP_ROUNDOFF), so
# on PC that gain saturates rather than crackles; this port's mixer is a
# float path with an unknown ceiling, and a clip that already peaks at
# -1.5 dBFS would be 7 dB over full scale. So the gain is a SETTING with a
# safe default, and finding the port's real headroom is one exefs/main
# rebuild per try rather than a guess baked into 22,000 files.
VOICE_GAIN_DEFAULT = 1.0
VOICE_GAIN_MAX = 4.0

# -> the stock MusicManager singleton, for ducking the BGM under speech.
# Relocated, so it is a pointer to a holder rather than the object.
MUSIC_MANAGER_HOLDER = 0x12CE238

# ---------------------------------------------------------------------------
# THE THREE CONTENDED SITES.
#
# Deliberately NOT imported from `ff7nx_ambient`, even though two of the three
# names exist there, because one of them would be wrong in a way that is very
# hard to see: the ambient field tick is 0x947CF4, ONE INSTRUCTION LATER than
# this one, precisely so it could chain behind analog-360. Importing that name
# here would silently move the voice service off the site its cave was written
# for -- W8/W9 are not yet loaded at 0x947CF0 and are live at 0x947CF4, so the
# same cave is correct at one and corrupting at the other.
#
# Each of these is checked against its stock word OR an existing direct
# branch; see `patch_main_clocked_dialogue`.
# ---------------------------------------------------------------------------
FIELD_HOOK = 0x947CF0
FIELD_ORIG = 0x29422728               # ldp w8, w9, [x25, #0x10]
BATTLE_HOOK = 0x8FB20
BATTLE_ORIG = 0xF00091FC              # adrp x28, #0x12CE000

# The translated 1.0.3 MESSAGE opcode.  The replaced word is the first
# instruction after the native message-window update returns; the cave emits
# it again before joining at MESSAGE_HOOK + 4.
MESSAGE_HOOK = 0x970398
MESSAGE_ORIG = 0xB94012A8             # ldr w8, [x21, #0x10]
MESSAGE_PRE_HOOK = 0x970394
MESSAGE_PRE_ORIG = 0x94000033         # bl 0x970460
MESSAGE_PRE_TARGET = 0x970460
# ASK uses the same per-window state machine as MESSAGE, but its script
# layout is opcode,+1 unknown,+2 window,+3 dialog,+4/+5 question range.
# Observe it before its native update just as FFNx's opcode_voice_ask does.
ASK_PRE_HOOK = 0x971B58
ASK_PRE_ORIG = 0x9400005E             # bl 0x971CD0
ASK_PRE_TARGET = 0x971CD0
# Every translated cdecl caller explicitly performs ``sub ESP,ESP,4`` and
# stores it immediately before the native BL.  Besides validating the module
# version, these two words prove that the live BL entry includes the x86
# return-address slot used by the +4/+8/+20 argument offsets below.
ASK_CALL_SETUP = (0x51001108, 0xB90012A8)
# Translated display_battle_action_text_42782A, immediately after it tests
# effect100 field6. Nonzero is the stock countdown; the first zero is FFNx's
# action-voice event. The cave only publishes a filename/command and leaves
# every NativeOggPlayer call to the native battle-frame hook.
BATTLE_ACTION_HOOK = 0xC56CC
BATTLE_ACTION_ORIG = 0x34000388         # cbz w8, +0x70 (to 0xC573C)
BATTLE_ACTION_ZERO = 0xC573C
BATTLE_ACTION_NONZERO = 0xC56D0
BATTLE_ACTIVE_ACTOR = 0xBE1170
BATTLE_CHAR_INDEX = 0x9AB0E4           # actor_vars[actor].index
BATTLE_FORMATION_ID = 0x9AB100          # actor_vars[actor].formation_id
BATTLE_ACTOR_STRIDE = 0x68
BATTLE_COMMAND = 0xBE119B               # model_state[actor].commandID
BATTLE_COMMAND_STRIDE = 0x1AEC
BATTLE_ACTION = 0xBF23FE                # small_state[actor].actionIdx
BATTLE_ACTION_STRIDE = 0x74
# Translated update_display_text_queue, reached only after the front entry's
# wait_frames becomes zero. The stock instruction starts the field_2 branch;
# the observer re-emits it and rejoins at +4.
BATTLE_TEXT_HOOK = 0x7CF4A4
BATTLE_TEXT_ORIG = 0x11000A74            # add w20, w19, #2
BATTLE_TEXT_RESUME = BATTLE_TEXT_HOOK + 4
BATTLE_TEXT_QUEUE = 0xBF1EB8             # 64 x 6-byte battle_text_data
BATTLE_SCENE_TEXT_OFFSETS = 0x9AD9E0     # u16 offsets for dynamic IDs >=256
BATTLE_SCENE_TEXT_DATA = 0x9AD1E0
BATTLE_KERNEL_SECTION_OFFSETS = 0x9A7FC8
BATTLE_KERNEL_DATA = 0x9A13C8
BATTLE_KERNEL_TEXT_SECTION = 16
# World-map MESSAGE/ASK are invoked both by event-script wrappers and by the
# persistent world loop. FFNx replaces all four calls. The ARM64 translation
# preserves the guest cdecl stack, so the observer caves read the same
# arguments before forwarding to the original translated targets.
WORLD_MESSAGE_HOOKS = (
    (0xF87AEC, 0x94000015, 0xF87B40, 20),
    (0xF378A8, 0x940140A6, 0xF87B40, 21),
)
WORLD_ASK_HOOKS = (
    (0xF88660, 0x94000014, 0xF886B0, 21),
    (0xF37800, 0x940143AC, 0xF886B0, 21),
)
WORLD_SERVICE_HOOK = 0xF1E0EC
WORLD_SERVICE_ORIG = 0x90001D98       # adrp x24, #0x12CE000
WORLD_CALL_SETUPS = {
    0xF87AEC: (0x51001108, 0xB9001288),
    0xF378A8: (0x51001108, 0xB90012A8),
    0xF88660: (0x51001108, 0xB90012A8),
    0xF37800: (0x51001108, 0xB90012A8),
}
# Echo-S's PC HEXT "Disable Name Change" replaces the x86 write at 0x719C60.
# These are the matching translated instructions: materialize DD46FC, write
# the current name-menu state, resolve it, then store it.
NAME_CHANGE_HOOK = 0xD06C94
NAME_CHANGE_ORIG = 0x2A1403E0          # mov w0, w20
# The stock state gate precedes the initialization that reads the current
# SPCNM index and source from the field opcode's guest stack. Echo-S's PC
# process resets DD46FC between opcode instances. The Switch translation
# retains the terminal copy cursor instead, so a later SPCNM (Barret after
# the initial Cloud event) jumps around initialization and waits forever on
# stale state. Automatic naming has no interactive multi-frame state to
# resume: always enter initialization for each opcode invocation.
NAME_CHANGE_STATE_GATE = 0xD06BBC
NAME_CHANGE_STATE_GATE_ORIG = 0x35000748  # cbnz w8, 0xD06CA4
NAME_CHANGE_RESUME = 0xD06CA4
# Echo-S's HEXT does not return to 0x719C66 after it copied a valid name. It
# jumps to x86 0x719CD3, the final name-menu close/finalize CALL. ARM64 expands
# that CALL into an emulated-ESP decrement/store followed by the native BL, so
# rejoin at the beginning of the full translated sequence.
NAME_CHANGE_FINALIZE = 0xD06DE8
RESOLVE_PAGED = 0x10FC3A0

# sub_6499F7 refreshes the physical controller at 0x9F7594, then begins
# assembling FF7's input mask from the guest button-status globals.  FFNx's
# auto-text path feeds a synthetic press into D011C0 *before* this assembly so
# the routine also updates its current/previous/pressed edge tables.  Writing
# only the returned EAX mask at FIELD_HOOK is too late for dialogue consumers.
INPUT_POST_REFRESH_HOOK = 0x9F7598
INPUT_POST_REFRESH_ORIG = 0x52823513   # mov w19, #0x11a8
INPUT_POST_REFRESH_RESUME = INPUT_POST_REFRESH_HOOK + 4
INPUT_OK_BUTTON_STATUS = 0xD011C0

# The first three calls are the exact path/open sequence inside
# NativeOggPlayer's constructor at 0x2fc4.  Probe it before allocating that
# stateful player: a missing loose file is a normal unvoiced MESSAGE, not a
# partially constructed player that must be destroyed on an error path.
OGG_WORKDIR = 0x10FAEE0
OGG_PATH_FORMAT = 0x1150FC0
OGG_OPEN = 0x10FE090
OGG_DECODER_DELETE = 0x10FD710
OGG_PATH_FORMAT_STRING = 0x11A89B3
OGG_PATH_STACK_BYTES = 0x160

# ``NativeOggPlayer`` passes its requested name twice: once to the Ogg-path
# formatter above and once into ``music stream thread '%s'`` before calling
# Horizon's SetThreadNamePointer.  The latter is diagnostic-only, but has a
# short SDK limit; it was the direct cause of the long-name abort in v61.
# Folder paths are useful for Echo-S (and mirror FFNx), so folder builds may
# replace this format with a short constant.  The decoder still receives the
# untouched, full name and therefore opens ``music_ogg/<field>/<line>.ogg``.
RODATA_START = 0x1153000
MUSIC_THREAD_NAME_FORMAT = 0x11A9ABC
MUSIC_THREAD_NAME_FORMAT_STOCK = b"music stream thread '%s'\0"

# NativeOggPlayer's worker checks this branch before deciding whether a
# decoder-owned LOOPSTART stream may pass its real end.  The Switch decoder
# treats LOOPSTART as a boolean and rewinds to the beginning even when the Ogg
# names a final-sample loop.  Normal playback keeps this branch's exact two
# routes; only a MAPJUMP-latched field voice is diverted to the worker's own
# renderer-stop path.
WORKER_LOOP_HOOK = 0x34B4
WORKER_LOOP_ORIG = 0x3500020A          # cbnz w10, 0x34f4
WORKER_ENDCHECK = WORKER_LOOP_HOOK + 4
WORKER_LOOP_CONTINUE = 0x34F4
WORKER_STOP_UNLOCK = 0x3568

# Opcode 0x60 from the original execute_opcode_table resolves through the
# recompilation map to this exact ARM64 function entry.  chrin_1b proves why
# the event matters: Reno executes MAPJUMP while MESSAGE 75, owned by another
# entity, is intentionally still open.  Window-close and field-frame service
# callbacks therefore cannot own this boundary.
MAPJUMP_HOOK = 0x95B8B0
MAPJUMP_ORIG = 0xF81B0FF9              # str x25, [sp, #-0x50]!
MAPJUMP_RESUME = MAPJUMP_HOOK + 4

# This is reached after the worker has released the native renderer at +0x80,
# but while it still owns the player's mutex.  It is the only point at which
# the worker can safely replace its decoder/ring payload and then restart its
# *existing* task.  The displaced instruction loads that mutex for the stock
# idle-state epilogue.
WORKER_RELOAD_HOOK = 0x3630
WORKER_RELOAD_ORIG = 0xF9402E60         # ldr x0, [x19, #0x58]
WORKER_RELOAD_STOCK = WORKER_RELOAD_HOOK + 4
WORKER_REENTER = 0x3358                 # worker initialization, with x0=P

RENDERER_PLAY_CURSOR_VTBL_OFF = 0x68

# Guest globals, derived from FFNx's field data chain and cross-checked in
# the translated MESSAGE body itself.
CURRENT_WINDOW = 0xCC0964             # u8
SCRIPT_CURSOR = 0xCC0CF8              # u16[window index]
FIELD_SCRIPT_PTR = 0xCBF5E8           # guest pointer value (u32)
# Guest inline character buffer holding the canonical loader path, e.g.
# ``field\\ancnt1``.  The original field loader copies its input directly
# into this exact buffer at 0x60BA46--0x60BA66 before loading the script.
# It is the same map-name source FFNx uses for Voice folder lookup and is
# stable across field-table reorderings that make CurrentFieldId unsuitable
# for portable loose-file names.
FIELD_FILE_NAME = 0xCC1EF0
CURRENT_FIELD_ID = 0xCC15D0           # u16; retained for diagnostics only
MESSAGE_STATE = 0xCFF5E4              # u16[window id * 24]
FIELD_GLOBAL_OBJECT_PTR = 0xCBF9D8    # guest pointer; world OK bit at +0x80
# Echo-S NoCloud/mod.xml selects its conditional ASK overlay from this byte:
# 0x02 = Tifa leads, 0x08 = Cid leads, all other values use normal VO.
NOCLOUD_LEADER = 0xDC09E5

# Guest globals used by Echo-S's original name-menu HEXT.  The port below
# preserves its exact data flow rather than baking character names into ARM.
NAME_MENU_INDEX = 0xDD46F8
NAME_MENU_ACTIVE = 0xDD46FC
NAME_MENU_SOURCE = 0xDD45F0
NAME_MENU_DEST = 0xDBFD9C
NAME_MENU_SKIP_FLAG = 0xDC00BD

# One active NativeOggPlayer, then the exact per-window state FFNx uses for
# MESSAGE page transitions.  BSS is zero-filled by the loader, matching the
# required initial ``last opcode == 0`` state.
PLAYER_OFF = 0
OWNER_OFF = 8
LAST_OPCODE_OFF = 0x10                # 256 * u16
PAGE_OFF = LAST_OPCODE_OFF + 0x200     # 256 * u8
KEY_OFF = 0x320                        # permanent 9-byte constructor name
SAVE_OFF = 0x340                       # saved X24..X28, five u64 values
SAVE_LR_OFF = SAVE_OFF + 5 * 8         # caller's translated-handler LR
SCRATCH_BYTES = 0x400
RESOLVER_PROBE_BSS_BYTES = 0x30
CTOR_PROBE_BSS_BYTES = 0x38
CTOR_PROBE_PLAYER_OFF = 0
CTOR_PROBE_SAVE_OFF = 8
CTOR_PROBE_LR_OFF = CTOR_PROBE_SAVE_OFF + 5 * 8
PLAY_PROBE_NAME = b'04200100\0'  # same clip under compact field key
TIMED_PROBE_TICK_OFF = 8
TIMED_PROBE_LIMIT_OFF = 12
TIMED_PROBE_DONE_OFF = 16
TIMED_PROBE_BSS_BYTES = 0x40
TIMED_PROBE_LEAD_TICKS = 0

# Fixed two-clip worker-reload experiment.  ``player`` remains allocated for
# the session; ``pending`` is a never-started native temporary; ``reap`` is
# that temporary after its payload has been moved by the worker.  The field
# frame deletes only ``reap``, whose decoder/rings have already been cleared.
# This deliberately does not touch translated MESSAGE yet.
RELOAD_PLAYER_OFF = 0x00
RELOAD_PENDING_OFF = 0x08
RELOAD_REAP_OFF = 0x10
RELOAD_BSS_BYTES = 0x40
RELOAD_FIRST_NAME = b'04200100\0'
RELOAD_SECOND_NAME = b'04200101\0'

# Timed-stop / joined-reload experiment.  The native task publishes player
# state 1 just before it returns, so it must be joined through the task
# wrapper at player+8 before its decoder payload is changed or restarted.
JOIN_PLAYER_OFF = 0x00
JOIN_PHASE_OFF = 0x08
JOIN_LIMIT_OFF = 0x0C
JOIN_BSS_BYTES = 0x40

# Two sequential, independently-owned NativeOggPlayers.  A completed task is
# never repurposed: this mirrors the stock one-source-per-player lifecycle.
SEQ_FIRST_OFF = 0x00
SEQ_SECOND_OFF = 0x08
SEQ_PHASE_OFF = 0x10
SEQ_LIMIT_OFF = 0x14
SEQ_BSS_BYTES = 0x40

# Native one-shot probe: retain independently owned players, but let the
# stock worker detect EOF after its parsed VGMSTREAM loop flag is cleared.
NATIVE_FIRST_OFF = 0x00
NATIVE_SECOND_OFF = 0x08
NATIVE_PHASE_OFF = 0x10
NATIVE_BSS_BYTES = 0x40

# The recompiler's own monotonic-clock wrappers.  The translated RDTSC helper
# uses NOW every call and obtains FREQUENCY once during its first calibration.
HOST_TICK_FREQUENCY = 0x10FB0F0
HOST_TICK_NOW = 0x10FB110

# Real-time, nonblocking-stop voice probe.  This is the lifecycle required by
# NativeOggPlayer: tagged files are retained for decoder loading, then stopped
# on a host-clock deadline without calling the synchronous OGG_STOP wrapper.
CLOCK_FIRST_OFF = 0x00
CLOCK_SECOND_OFF = 0x08
CLOCK_PHASE_OFF = 0x10
CLOCK_DEADLINE_OFF = 0x18
CLOCK_BSS_BYTES = 0x40

# Production bridge state.  MESSAGE is translated guest code and must never
# call the native player directly; it only publishes one command.  The field
# callback is a normal native-safe call site and owns every player operation.
# Keeping that boundary is what separates the v26-proven lifecycle from the
# early MESSAGE-thread crashes.
VOICE_PLAYER_OFF = 0x000              # NativeOggPlayer *
VOICE_LIMIT_OFF = 0x008               # legacy cursor-probe boundary (unused)
VOICE_DEADLINE_OFF = 0x008            # u64 host-tick stop deadline
VOICE_PROBE_NAME_TOGGLE_OFF = 0x00C   # u32 alternating-name test only
VOICE_OWNER_OFF = 0x010               # u32 active MESSAGE window
VOICE_PENDING_CMD_OFF = 0x014         # u32: NONE/PLAY/STOP
VOICE_PENDING_OWNER_OFF = 0x018       # u32 window for the queued command
VOICE_RECLAIM_TICKS_OFF = 0x01C       # u32 completed-worker grace counter
VOICE_KEY_OFF = 0x020                 # queued native Ogg filename
# An FFNx-compatible field candidate can be ``<field>/w255_255z``.  It is
# longer than the old flat hash, so reserve a real 32-byte C string both in
# BSS and at the tail of our voice-owned player allocation.
VOICE_KEY_BYTES = 0x20
VOICE_KEY_MAX_CHARS = VOICE_KEY_BYTES - 1
BATTLE_KEY_PREFIX = b'battle/'
FIELD_NAME_HASH_MULTIPLIER = 93       # collision-free across all 787 maps
VOICE_LAST_OPCODE_OFF = 0x040         # u16[256]
VOICE_PAGE_OFF = VOICE_LAST_OPCODE_OFF + 0x200  # u8[256]
VOICE_MESSAGE_SAVE_OFF = 0x440        # x24..x28, then LR
VOICE_MESSAGE_LR_OFF = VOICE_MESSAGE_SAVE_OFF + 5 * 8
# The stock MusicManager owns all BGM players in a 128-slot array at +0x20.
# Its per-frame Tick applies ``master_volume (+0x448) * track_volume`` to
# every active player.  Keep our temporary field-voice duck state separate
# from the MESSAGE transition tables, in the unused tail of this BSS block.
VOICE_BGM_DUCKED_OFF = 0x470           # u32: 1 ducked, 2 releasing
VOICE_BGM_MASTER_OFF = 0x474           # float: original manager +0x448
VOICE_BGM_RELEASE_FRAMES_OFF = 0x478   # u32 field callbacks remaining
VOICE_BGM_TARGET_OFF = 0x47C           # float: active attack-ramp target
VOICE_WAITING_WINDOW_OFF = 0x480       # u32: voice ended; await MESSAGE close
VOICE_AUTO_OK_PENDING_OFF = 0x484      # u32: inject raw OK after input refresh
VOICE_LAST_OPTION_OFF = 0x488          # u8[256], ASK highlighted option
VOICE_CURRENT_OPTION_OFF = 0x588       # u32, ASK hook's live option scratch
VOICE_WORLD_LAST_OPCODE_OFF = 0x590    # u16[256]
VOICE_WORLD_PAGE_OFF = 0x790           # u8[256]
# Field and world MESSAGE dispatchers are mutually exclusive, so their ASK
# highlight history can share this 256-byte table. World opcode/page state is
# deliberately separate; those values can survive a field/world handoff.
VOICE_WORLD_LAST_OPTION_OFF = VOICE_LAST_OPTION_OFF
# THE COMPACT LAYOUT, AND WHAT IS AND IS NOT KNOWN ABOUT WHY IT IS NEEDED.
# ========================================================================
# Measured, on hardware, one field at a time: with the block based at
# +0x3FEF660, writing `PENDING_AUTO` at +0xA90 -- i.e. +0x3FF00F0, in the page
# after the one the pre-existing BSS ended in -- aborted on the first voiced
# line. Writing `PENDING_OWNER` at +0x018 did not, and neither did the same
# probe after the fields were moved down here. That is the whole of what was
# established. The layout below respects it: every live record ends at or
# before +0x98F, so the block stays inside the page the composed BSS already
# ended in.
#
# The explanation first written here -- that raising `bssSize` in a
# replacement NSO does not get the next page mapped -- is WRONG, and it is
# worth saying so rather than leaving a plausible story in place. The stock
# module's BSS ends at +0x3FEC328, in the page +0x3FEC000..+0x3FED000. The
# Cosmo Memory bridges and `ff7nx_ambient` push that end to +0x3FEF65C, and
# they read and write their own blocks in +0x3FED000, +0x3FEE000 and
# +0x3FEF000 on hardware every frame. Three new BSS pages are therefore
# already being mapped by exactly this mechanism, in this build, working. A
# fourth cannot be the thing that is impossible.
#
# So the real cause of the +0xA90 abort is not established. The other
# candidate is cave placement rather than BSS: that probe was the only one
# whose producer was 163 words, and the hole allocator picks a different set
# of padding runs for every word count. The `footprint` diagnostic exists to
# separate those two -- it executes NOPs through exactly the physical layout
# of a given producer size -- so if this ever recurs, build `footprint` at the
# failing size first and the answer is one test rather than six.
#
# The guard in `apply_to_nso` therefore enforces the MEASURED rule and says it
# is empirical. It is a hard build error, not a warning, because the failure
# mode it prevents is a one-instruction abort on the first line of dialogue.
# Current slack is 16 bytes, so any upstream BSS growth will trip it; when it
# does, compact further here rather than raising the ceiling on a guess.
#
# The world tables stay separate from the field tables so a field/world
# transition cannot inherit a stale per-window opcode, while the former low
# 0x340--0x43F gap holds the small cross-mode records and their aligned
# spills.
VOICE_WORLD_DIALOG_OFF = 0x890         # u8[256], event id retained for loop
VOICE_PENDING_AUTO_OFF = 0x340         # u32 queued PLAY may synthesize OK
VOICE_ACTIVE_AUTO_OFF = 0x344          # u32 current player may synthesize OK
VOICE_INITIAL_OPTION_OFF = 0x348       # u32 ASK initial-option fallback/chain
VOICE_VARIANT_FALLBACK_OFF = 0x34C     # u32 NoCloud key may fall back to VO
MUSIC_MANAGER_MASTER_VOLUME_OFF = 0x448
# Name-menu auto-fill needs its own translated-guest spill area: it can run
# while the player BSS is live, so it must not borrow the MESSAGE save slots.
NAME_CHANGE_SAVE_OFF = 0x350
NAME_CHANGE_LR_OFF = NAME_CHANGE_SAVE_OFF + 5 * 8
VOICE_INITIAL_KEY_OFF = 0x380          # 0x20-byte initial ASK option filename
VOICE_VARIANT_FALLBACK_KEY_OFF = 0x3A0 # 0x20-byte ordinary VO fallback key

# MESSAGE diagnostics can retain the production request-path control flow
# while selecting individual mailbox writes. Production always uses all three.
VOICE_PUBLISH_OWNER = 0x1
VOICE_PUBLISH_AUTO = 0x2
VOICE_PUBLISH_COMMAND = 0x4
VOICE_PUBLISH_ALL = (VOICE_PUBLISH_OWNER | VOICE_PUBLISH_AUTO |
                     VOICE_PUBLISH_COMMAND)
BATTLE_ACTION_SAVE_OFF = 0x3C0          # x24..x28, then translated LR
BATTLE_ACTION_LR_OFF = BATTLE_ACTION_SAVE_OFF + 5 * 8
BATTLE_ACTION_ARMED_OFF = 0x3F0         # action-text field6 reached zero
BATTLE_ACTION_COUNTER_OFF = 0x3F4       # deterministic percentage counter
# Low bits of that counter select the bark variant. Three bits = eight slots,
# and `echo_s_battlevo.VARIANTS` stages exactly eight for every key: change
# one and the other must change with it, which `tests/test_battle_vo.py`
# asserts rather than trusting a comment.
BATTLE_VARIANT_BITS = 3
BATTLE_ACTION_KIND_OFF = 0x3F8          # u8: 'c' character, 'e' enemy
BATTLE_TEXT_SAVE_OFF = 0x400            # x24..x28, then translated LR
BATTLE_TEXT_LR_OFF = BATTLE_TEXT_SAVE_OFF + 5 * 8
BATTLE_TEXT_LAST_OFF = 0x430            # u32 buffer/character signature
# `WORLD_DIALOG` is the highest 256-byte record and ends exactly at 0x990.
# THE REPLAY GUARD, AT THE DRAIN.
# ==============================
# Four builds guarded the PUBLISH side -- per-window records, per-window key
# hashes, a global last-key slot -- and the recording of the scene falsifies
# all of them at once. Measured from `repeated_dialogue.mp4` by matched filter
# against the staged clip:
#
#     chrin_1b/74.ogg  (Reno)    -0.67s .. 2.24s   natural end
#     chrin_1b/75.ogg  (grunts)   2.49s .. 5.35s   natural end, complete
#     chrin_1b/75.ogg  AGAIN      5.44s ..         cut off by the map change
#
# 0.09 SECONDS. The clip restarts five or six frames after it ends, which is
# the length of `completed` -> `commands` -> `create`. So whatever published
# it, the fact that matters is the one this can act on: AT THE MOMENT A PLAYER
# RETIRES, THE REQUEST SITTING IN THE MAILBOX IS THE LINE THAT JUST PLAYED.
#
# So the identity kept is the key the LIVE PLAYER was built from, and a
# request matching it is refused for a short window after that player is
# retired. It cannot be clobbered by the other speakers in a burst (there is
# one player, not one per window), it does not care which window or which code
# path published the duplicate, and the key carries the field name so a new
# scene is never confused with a repeat.
#
# The comparison is against the last key a player was BUILT from, and the
# counter is what makes it a guard rather than a permanent ban: while that
# player is still speaking the counter is zero, so a same-window re-request
# still replaces it, exactly as before.
#
# Both live in `VOICE_PAGE`'s dead tail -- a u8[256] indexed by window id,
# and `audit_voice_windows.py` walked every MESSAGE and ASK site in all 702
# Echo-S fields to establish that the ids used are 0, 1, 2 and 3. Byte 64 up is
# unreachable, so this costs NO new BSS; the block has 8 bytes of page slack.
VOICE_ACTIVE_KEY_HASH_OFF = VOICE_PAGE_OFF + 64    # u32, last key constructed
VOICE_REPLAY_GUARD_OFF = VOICE_PAGE_OFF + 68       # u32, frames still guarded
VOICE_MAPJUMP_OFF = VOICE_PAGE_OFF + 72             # u32, worker stop latch
# Frames after a retirement during which that same line will not be rebuilt.
# The measured gap is 5-6 frames; a map transition is about 60. Half a second
# is comfortably outside the time it takes to close a box and talk to an NPC
# again, which is the only thing this can wrongly refuse.
VOICE_REPLAY_GUARD_FRAMES = 30
VOICE_SCRATCH_BYTES = 0x990
VOICE_CMD_NONE = 0
VOICE_CMD_PLAY = 1
VOICE_CMD_STOP = 2
PLAYER_NAME_OFF = PLAYER_BYTES
PLAYER_ALLOC_BYTES = PLAYER_BYTES + VOICE_KEY_BYTES  # object + immutable name


def _add_x_uxtw(dst, base, index):
    """``add Xdst, Xbase, Windex, uxtw`` for BSS table addressing."""
    return 0x8B204000 | (index << 16) | (base << 5) | dst


def _udiv(dst, left, right):
    """``udiv Wdst, Wleft, Wright`` for a decoded clip-duration countdown."""
    return 0x1AC00800 | (right << 16) | (left << 5) | dst


def _guest(a, address):
    """Put a guest address in W0 without relying on a host mapping."""
    a.emit(A.movz(0, address & 0xFFFF))
    a.emit(A.movk_hi(0, address >> 16))


def _bss_ptr(a, dst, address):
    a.emit(A.adrp(dst, a.pc(), address & ~0xFFF))
    a.emit(A.add_imm64(dst, dst, address & 0xFFF))


def _direct_branch_target(pc, word):
    """Decode an AArch64 unconditional B, or return None."""
    if word & 0xFC000000 != 0x14000000:
        return None
    imm26 = word & 0x03FFFFFF
    if imm26 & 0x02000000:
        imm26 -= 0x04000000
    return pc + (imm26 << 2)


def _verified_hole_pool(src, text):
    """Allocate caves using the complete NSO's indirect-target evidence.

    ``cave_space.find_holes_in`` scans .rodata/.data for addresses that point
    into apparent alignment padding.  Passing only ``text`` silently makes
    that scan empty and admits 128 indirectly referenced holes in this main.
    Overlay the current text on the complete module image so both the zero
    recheck and all direct/indirect reachability checks remain effective.
    """
    module = nxmap.Main(src)
    image = bytearray(module.img)
    image[:len(text)] = text
    return ff7nx_cave.HolePool(image, starts=set(module.arm_starts))


def _ldr_s(rt, rn, imm=0):
    """``LDR St, [Xn, #imm]`` for an aligned single-precision member."""
    if imm % 4 or not 0 <= imm <= 0x3ffc:
        raise ValueError('single-precision load offset out of range: %#x' % imm)
    return 0xBD400000 | ((imm >> 2) << 10) | (rn << 5) | rt


def _str_s(rt, rn, imm=0):
    """``STR St, [Xn, #imm]`` for an aligned single-precision member."""
    if imm % 4 or not 0 <= imm <= 0x3ffc:
        raise ValueError('single-precision store offset out of range: %#x' % imm)
    return 0xBD000000 | ((imm >> 2) << 10) | (rn << 5) | rt


def _emit_bgm_duck(a, bss, duck_percent, attack_frames, label):
    """Reduce MusicManager's master multiplier while a voice is audible.

    This changes the manager's own multiplier rather than one Ogg player's
    gain.  Its existing Tick therefore continues to honour game BGM fades and
    applies the duck to a newly changed field track as well.  Native dialogue
    players are not manager slots, so their gain remains at 1.0.
    """
    a.emit(A.ldr(8, bss, VOICE_BGM_DUCKED_OFF))
    a.emit(A.cmp_imm(8, 1))
    a.bcond(label + '_done', A.EQ)
    a.emit(A.cmp_imm(8, 3))
    a.bcond(label + '_done', A.EQ)
    # State 0 captures the pre-voice level.  State 2 is a release in
    # progress; preserve that baseline and reverse smoothly toward ducked.
    a.emit(A.cmp_imm(8, 2))
    a.bcond(label + '_resume', A.EQ)
    a.emit(A.adrp(8, a.pc(), MUSIC_MANAGER_HOLDER & ~0xFFF))
    a.emit(A.ldr64(8, 8, MUSIC_MANAGER_HOLDER & 0xFFF))
    a.cbz64(8, label + '_done')
    a.emit(A.ldr64(8, 8, 0))             # holder -> MusicManager
    a.cbz64(8, label + '_done')
    a.emit(_ldr_s(0, 8, MUSIC_MANAGER_MASTER_VOLUME_OFF))
    a.emit(_str_s(0, bss, VOICE_BGM_MASTER_OFF))
    a.b(label + '_target')
    a.label(label + '_resume')
    a.emit(A.adrp(8, a.pc(), MUSIC_MANAGER_HOLDER & ~0xFFF))
    a.emit(A.ldr64(8, 8, MUSIC_MANAGER_HOLDER & 0xFFF))
    a.cbz64(8, label + '_done')
    a.emit(A.ldr64(8, 8, 0))             # holder -> MusicManager
    a.cbz64(8, label + '_done')
    a.label(label + '_target')
    # Turn the integer build parameter into an arbitrary float percentage.
    # This avoids relying on the small AArch64 FMOV-immediate value subset.
    a.emit(_ldr_s(0, bss, VOICE_BGM_MASTER_OFF))
    a.emit(A.movz(9, duck_percent))
    a.emit(0x1E230121)                  # ucvtf s1, w9
    a.emit(A.movz(9, 100))
    a.emit(0x1E230122)                  # ucvtf s2, w9
    a.emit(0x1E221821)                  # fdiv s1, s1, s2
    a.emit(0x1E210800)                  # fmul s0, s0, s1
    a.emit(_str_s(0, bss, VOICE_BGM_TARGET_OFF))
    a.emit(A.movz(8, 3))                 # smooth attack in progress
    a.emit(A.str_(8, bss, VOICE_BGM_DUCKED_OFF))
    a.emit(A.movz(8, attack_frames))
    a.emit(A.str_(8, bss, VOICE_BGM_RELEASE_FRAMES_OFF))
    a.label(label + '_done')


def _emit_bgm_release(a, bss, release_frames, label):
    """Begin a gradual return to the pre-voice MusicManager multiplier."""
    a.emit(A.ldr(8, bss, VOICE_BGM_DUCKED_OFF))
    a.emit(A.cmp_imm(8, 0))
    a.bcond(label + '_done', A.EQ)
    a.emit(A.cmp_imm(8, 2))
    a.bcond(label + '_done', A.EQ)
    a.emit(A.movz(8, 2))
    a.emit(A.str_(8, bss, VOICE_BGM_DUCKED_OFF))
    a.emit(A.movz(8, release_frames))
    a.emit(A.str_(8, bss, VOICE_BGM_RELEASE_FRAMES_OFF))
    a.label(label + '_done')


def _emit_bgm_release_step(a, bss, label):
    """Advance one release-ramp step from the field callback.

    The manager's stored master multiplier is deliberately used as the ramp
    source.  Its own per-track fades remain managed by MusicManager Tick; this
    only changes the outer multiplier that VO temporarily owns.
    """
    a.emit(A.ldr(8, bss, VOICE_BGM_DUCKED_OFF))
    a.emit(A.cmp_imm(8, 2))
    a.bcond(label + '_release', A.EQ)
    a.emit(A.cmp_imm(8, 3))
    a.bcond(label + '_attack', A.EQ)
    a.b(label + '_done')
    a.label(label + '_release')
    a.emit(A.adrp(8, a.pc(), MUSIC_MANAGER_HOLDER & ~0xFFF))
    a.emit(A.ldr64(8, 8, MUSIC_MANAGER_HOLDER & 0xFFF))
    a.cbz64(8, label + '_clear')
    a.emit(A.ldr64(8, 8, 0))             # holder -> MusicManager
    a.cbz64(8, label + '_clear')
    a.emit(A.ldr(9, bss, VOICE_BGM_RELEASE_FRAMES_OFF))
    a.emit(A.cmp_imm(9, 0))
    a.bcond(label + '_finish', A.EQ)
    a.emit(_ldr_s(0, 8, MUSIC_MANAGER_MASTER_VOLUME_OFF))
    a.emit(_ldr_s(1, bss, VOICE_BGM_MASTER_OFF))
    a.b(label + '_step')
    a.label(label + '_attack')
    a.emit(A.adrp(8, a.pc(), MUSIC_MANAGER_HOLDER & ~0xFFF))
    a.emit(A.ldr64(8, 8, MUSIC_MANAGER_HOLDER & 0xFFF))
    a.cbz64(8, label + '_clear')
    a.emit(A.ldr64(8, 8, 0))             # holder -> MusicManager
    a.cbz64(8, label + '_clear')
    a.emit(A.ldr(9, bss, VOICE_BGM_RELEASE_FRAMES_OFF))
    a.emit(A.cmp_imm(9, 0))
    a.bcond(label + '_finish_attack', A.EQ)
    a.emit(_ldr_s(0, 8, MUSIC_MANAGER_MASTER_VOLUME_OFF))
    a.emit(_ldr_s(1, bss, VOICE_BGM_TARGET_OFF))
    a.label(label + '_step')
    a.emit(0x1E203821)                  # fsub s1, s1, s0
    a.emit(0x1E230122)                  # ucvtf s2, w9
    a.emit(0x1E221821)                  # fdiv s1, s1, s2
    a.emit(0x1E212800)                  # fadd s0, s0, s1
    a.emit(_str_s(0, 8, MUSIC_MANAGER_MASTER_VOLUME_OFF))
    a.emit(A.sub_imm(9, 9, 1))
    a.emit(A.str_(9, bss, VOICE_BGM_RELEASE_FRAMES_OFF))
    a.emit(A.cmp_imm(9, 0))
    a.bcond(label + '_done', A.NE)
    # Keep X8 as the live MusicManager pointer through both finish paths.
    # Loading the state into W8 zero-extends into X8; v113 consequently wrote
    # the final ramp value through address ``state + 0x448`` (0x44b for the
    # attack state 3) and raised a User Break on the first voiced dialogue.
    a.emit(A.ldr(10, bss, VOICE_BGM_DUCKED_OFF))
    a.emit(A.cmp_imm(10, 3))
    a.bcond(label + '_finish_attack', A.EQ)
    a.label(label + '_finish')
    a.emit(_ldr_s(0, bss, VOICE_BGM_MASTER_OFF))
    a.emit(_str_s(0, 8, MUSIC_MANAGER_MASTER_VOLUME_OFF))
    a.label(label + '_clear')
    a.emit(A.str_(A.WZR, bss, VOICE_BGM_DUCKED_OFF))
    a.b(label + '_done')
    a.label(label + '_finish_attack')
    a.emit(_ldr_s(0, bss, VOICE_BGM_TARGET_OFF))
    a.emit(_str_s(0, 8, MUSIC_MANAGER_MASTER_VOLUME_OFF))
    a.emit(A.movz(8, 1))
    a.emit(A.str_(8, bss, VOICE_BGM_DUCKED_OFF))
    a.label(label + '_done')


def _emit_player_name_copy(a, player, bss):
    """Give a NativeOggPlayer an immutable, allocation-local filename.

    NativeOggPlayer's asynchronous task retains the ``char *`` supplied to
    its constructor.  ``VOICE_KEY_OFF`` is only a queued MESSAGE key and is
    overwritten for the following line, so it must never be that pointer.
    The player allocation has a 32-byte tail solely for its NUL-terminated
    compact filename.
    """
    # Copy the entire reserved buffer.  The formatter writes an explicit NUL
    # before its end, so stale bytes after it are immaterial and no unsafe
    # variable-length copy is needed at this native boundary.
    for off in range(0, VOICE_KEY_BYTES, 8):
        a.emit(A.ldr64(8, bss, VOICE_KEY_OFF + off))
        a.emit(A.str64(8, player, PLAYER_NAME_OFF + off))
    a.emit(A.add_imm64(1, player, PLAYER_NAME_OFF))


def _emit_bss_key_copy(a, bss, source_off, target_off):
    """Copy one fixed-size queued filename between BSS buffers."""
    for off in range(0, VOICE_KEY_BYTES, 8):
        a.emit(A.ldr64(8, bss, source_off + off))
        a.emit(A.str64(8, bss, target_off + off))


def _emit_key_hash(a, bss, dest, label_prefix, cursor=11, counter=10, word=8):
    """Fold ``VOICE_KEY`` into a u32 in ``dest``.

    A loop, not 32 unrolled words: build 449 put the unrolled form in the
    SHARED service builder, took world_service to 689 words against a 696-word
    window, and the allocator could not place it -- which silenced the entire
    runtime on hardware.  Eight words, whatever the key length.
    """
    a.emit(A.add_imm64(cursor, bss, VOICE_KEY_OFF))
    a.emit(A.movz(dest, 0))
    a.emit(A.movz(counter, VOICE_KEY_BYTES // 4))
    label = label_prefix + '_key_hash_word'
    a.label(label)
    a.emit(A.ldr_post(word, cursor, 4))
    a.emit(A.eor_reg(dest, dest, word))
    a.emit(A.sub_imm(counter, counter, 1))
    a.emit(A.cmp_imm(counter, 0))
    a.bcond(label, A.NE)


def _emit_ogg_preflight(a, bss, miss_label):
    """Open and release the exact native Ogg path before player construction.

    The native constructor calls this same sequence after it has initialized
    its task and mutex.  Keeping the failure on this side of construction
    makes a missing field voice an ordinary no-op and avoids exercising that
    partially-initialised object's destructor.
    """
    a.emit(A.bl(a.pc(), OGG_WORKDIR))
    a.emit(_mov64(2, 0))
    a.emit(A.adrp(1, a.pc(), OGG_PATH_FORMAT_STRING & ~0xFFF))
    a.emit(A.add_imm64(1, 1, OGG_PATH_FORMAT_STRING & 0xFFF))
    a.emit(A.add_imm64(0, A.SP, 8))
    a.emit(A.add_imm64(3, bss, VOICE_KEY_OFF))
    a.emit(A.bl(a.pc(), OGG_PATH_FORMAT))
    a.emit(A.add_imm64(0, A.SP, 8))
    a.emit(A.bl(a.pc(), OGG_OPEN))
    a.cbz64(0, miss_label)
    a.emit(A.bl(a.pc(), OGG_DECODER_DELETE))


def _hex_nibble(a, source, shift, dest_off):
    """Store one lower-case hexadecimal digit at ``[x1, #dest_off]``.

    The running field IDs fit in three hexadecimal digits (0..786), but the
    key still has its fixed eight-digit primary field.  The generated output
    is therefore exactly ``01 00000<field:03x> 00 <dialog:02x> <page:02x>``.
    """
    if shift:
        a.emit(A.lsr(8, source, shift))
    else:
        a.emit(A.mov_reg(8, source))
    a.emit(A.and_mask(8, 8, 4))
    a.emit(A.add_imm(9, 8, ord('0')))
    a.emit(A.add_imm(10, 8, ord('a') - 10))
    a.emit(A.cmp_imm(8, 10))
    a.emit(A.csel(8, 9, 10, A.LT))
    a.emit(A.strb(8, 1, dest_off))


def _emit_battle_actor_separator(a, dest_off):
    """
    Put the actor's own subdirectory into the battle key at ``[x1, #off]``.

    ONE DIRECTORY PER ACTOR AND COMMAND, AND IT IS NOT COSMETIC.
    853 action keys in eight variant slots is 6,728 files, and the first
    version put every one of them in `music_ogg/battle/`. Field voice never
    does this -- its largest directory is 344 files, because the directory IS
    the field. A bark therefore paid for a lookup in a directory twenty times
    larger than anything else the port opens, on the frame the action fires,
    on the main thread, through LayeredFS.

    Splitting on the actor cut that to a 728-file median, which was measured
    on hardware as most of the stall gone and a little left. Splitting on the
    command as well takes it to 54 directories with a 112-file median and a
    376 worst case -- at or under what field voice already does without a
    hitch. Each level costs one byte in the key and one store here.
    """
    a.emit(A.movz(8, ord('/')))
    a.emit(A.strb(8, 1, dest_off))


def _emit_battle_key_prefix(a, bss, key_off=VOICE_KEY_OFF):
    """Write ``battle/`` and leave X1 at the compact stem position."""
    a.emit(A.add_imm64(1, bss, key_off))
    a.emit(A.movz(8, 0x6162))            # "ba"
    a.emit(A.strh(8, 1, 0))
    a.emit(A.movz(8, 0x7474))            # "tt"
    a.emit(A.strh(8, 1, 2))
    a.emit(A.movz(8, 0x656C))            # "le"
    a.emit(A.strh(8, 1, 4))
    a.emit(A.movz(8, ord('/')))
    a.emit(A.strb(8, 1, 6))
    a.emit(A.add_imm64(1, 1, len(BATTLE_KEY_PREFIX)))


def _emit_native_task_join(a, player):
    """Wait for ``player``'s NativeOggPlayer scheduler task to return.

    ``player + 0x98`` becomes one in the worker's cleanup path *before* the
    task returns through its wrapper.  NativeOggPlayer's destructor follows
    this exact virtual call through ``player + 8`` before releasing decoder
    memory, so a payload reload must do the same.  This helper deliberately
    does not destroy the wrapper: OGG_PLAY's normal task-launch method owns
    replacing its completed task control on the next start.
    """
    a.emit(A.ldr64(0, player, 0x08))
    a.emit(A.ldr64(8, 0, 0x00))
    a.emit(A.ldr64(8, 8, 0x18))
    a.emit(0xD63F0100)                  # blr x8


def _build_mapjump_voice_latch(cave, addr, scratch):
    """Latch a field transition without calling audio from the script VM."""
    a = Asm(cave, addr)
    _bss_ptr(a, 8, scratch)
    a.emit(A.ldr64(9, 8, VOICE_PLAYER_OFF))
    a.cbz64(9, 'resume')
    a.emit(A.movz(9, 1))
    a.emit(A.str_(9, 8, VOICE_MAPJUMP_OFF))
    a.label('resume')
    a.emit(MAPJUMP_ORIG)
    a.emit(A.b(a.pc(), MAPJUMP_RESUME))
    return a.resolve()


def _build_mapjump_worker_stop(cave, addr, scratch):
    """Stop only our field voice, inside its worker, after MAPJUMP.

    This hook does not infer EOF and does not alter decoder state.  With no
    transition latch it reproduces the stock CBNZ exactly.  Once MAPJUMP has
    latched a live field voice, it clears the one-shot latch and enters the
    worker's existing externally-stopped cleanup path before another loop
    buffer can be submitted.
    """
    a = Asm(cave, addr)
    _bss_ptr(a, 11, scratch)
    a.emit(A.ldr(12, 11, VOICE_MAPJUMP_OFF))
    a.cbz(12, 'stock')
    a.emit(A.ldr64(12, 11, VOICE_PLAYER_OFF))
    a.emit(A.cmp_reg64(19, 12))
    a.bcond('stock', A.NE)
    a.emit(A.str_(A.WZR, 11, VOICE_MAPJUMP_OFF))
    # We are already inside the player's locked worker section. Reproduce the
    # state written by OGG_STOP's nonblocking half, then join the worker's
    # existing unlock/cleanup route. Do not branch to +0x34D8: that EOF path
    # still submits a final buffer and expects X22 to hold a computed clamp,
    # which is not live at this hook.
    a.emit(A.strb(A.WZR, 19, 0x50))
    a.emit(A.movz(12, 8))
    a.emit(A.str_(12, 19, 0x98))
    a.emit(A.b(a.pc(), WORKER_STOP_UNLOCK))
    a.label('stock')
    a.cbnz(10, 'loop_continue')
    a.emit(A.b(a.pc(), WORKER_ENDCHECK))
    a.label('loop_continue')
    a.emit(A.b(a.pc(), WORKER_LOOP_CONTINUE))
    return a.resolve()


def _emit_host_deadline(a, player, bss, deadline_off=CLOCK_DEADLINE_OFF):
    """Set a BSS deadline from decoder duration in host ticks."""
    # These are the exact wrappers used by the translated RDTSC path.  Unlike
    # FIELD_HOOK cadence they remain real-time at every configured frame rate.
    a.emit(A.bl(a.pc(), HOST_TICK_NOW))
    a.emit(_mov64(22, 0))
    a.emit(A.bl(a.pc(), HOST_TICK_FREQUENCY))
    a.emit(_mov64(9, 0))
    a.emit(A.ldr64(8, player, 0x60))
    a.emit(A.ldr(10, 8, 0x00))          # num_samples, zero-extends X10
    a.emit(A.ldr(11, 8, 0x04))          # sample_rate, zero-extends X11
    a.emit(A.mul64(10, 10, 9))
    a.emit(A.udiv64(10, 10, 11))
    a.emit(A.add_reg64(0, 22, 10))
    a.emit(A.str64(0, bss, deadline_off))


def _emit_native_stop_async(a, player):
    """Apply OGG_STOP's state transition without its blocking task join.

    Stock OGG_STOP at 0x32B0 locks P+0x58, clears P+0x50, sets P+0x98=8,
    unlocks, *then joins P+8*.  Field update must perform only the first,
    nonblocking half; later state-1 polling joins after the worker returned.
    """
    a.emit(A.ldr64(0, player, 0x58))
    a.emit(A.ldr64(8, 0, 0x00))
    a.emit(A.ldr64(8, 8, 0x10))
    a.emit(0xD63F0100)                  # blr x8, mutex lock
    a.emit(A.ldr64(0, player, 0x58))
    a.emit(A.movz(8, 8))
    a.emit(A.strb(A.WZR, player, 0x50))
    a.emit(A.str_(8, player, 0x98))
    a.emit(A.ldr64(8, 0, 0x00))
    a.emit(A.ldr64(8, 8, 0x18))
    a.emit(0xD63F0100)                  # blr x8, mutex unlock


def _emit_deadline_stop(a, player, bss, out_label,
                        deadline_off=CLOCK_DEADLINE_OFF,
                        voice_gain=VOICE_GAIN_DEFAULT):
    """Refresh independent gain and asynchronously stop at its host deadline.

    The refresh has to carry the SAME gain the player was created with. It
    runs every field callback, so a 1.0 here would silently undo the boost
    one tick after the line started -- the line would begin at the right
    level and drop.
    """
    a.emit(A.fmov_s_imm(0, voice_gain))
    a.emit(_mov64(0, player))
    a.emit(A.bl(a.pc(), OGG_VOLUME))
    a.emit(A.bl(a.pc(), HOST_TICK_NOW))
    a.emit(A.ldr64(9, bss, deadline_off))
    a.emit(A.cmp_reg64(0, 9))
    a.bcond(out_label, A.LT)
    # The staged 100-ms leading silence absorbs one delayed field callback
    # after the tagged decoder loops back to zero.
    a.emit(0x1E2703E0)                  # fmov s0, wzr
    a.emit(_mov64(0, player))
    a.emit(A.bl(a.pc(), OGG_VOLUME))
    _emit_native_stop_async(a, player)


def _emit_fixed_voice_name(a, name):
    """Store a bounded music-relative filename through X1 and return length.

    The compact eight-hex form remains the normal diagnostic form.  The
    broader spelling is deliberately restricted to safe FFNx-style relative
    paths for the folder-layout probe, such as ``elm/24``.
    """
    allowed = 'abcdefghijklmnopqrstuvwxyz0123456789_/-'
    if (not name or len(name) > VOICE_KEY_MAX_CHARS or
            any(c not in allowed for c in name)):
        raise ValueError('forced field-voice probe name must be a bounded '
                         'lower-case music-relative path')
    # Keep the old compact stores for the established probes.  Longer folder
    # candidates use byte stores because slash-delimited components need not
    # have even length.
    if len(name) == 8 and all(c in '0123456789abcdef' for c in name):
        for off in range(0, 8, 2):
            pair = name[off:off + 2].encode('ascii')
            a.emit(A.movz(8, pair[0] | (pair[1] << 8)))
            a.emit(A.strh(8, 1, off))
    else:
        for off, char in enumerate(name.encode('ascii')):
            a.emit(A.movz(8, char))
            a.emit(A.strb(8, 1, off))
    return len(name)


def _emit_fixed_voice_suffix(a, dialog_id, page):
    """Replace the last four compact-key digits without changing map hash."""
    if not (0 <= dialog_id <= 0xFF and 0 <= page <= 0xFF):
        raise ValueError('field-voice suffix values must be bytes')
    suffix = '%02x%02x' % (dialog_id, page)
    for off in (4, 6):
        pair = suffix[off - 4:off - 2].encode('ascii')
        a.emit(A.movz(8, pair[0] | (pair[1] << 8)))
        a.emit(A.strh(8, 1, off))


def _emit_append_byte(a, source):
    """Append ``Wsource`` as one ASCII byte at X1 and advance X1."""
    a.emit(A.strb(source, 1, 0))
    a.emit(A.add_imm64(1, 1, 1))


def _emit_append_decimal(a, source, label_prefix):
    """
    Append a 0..255 value as an unpadded decimal component at X1.

    A ZERO IN THE TENS PLACE IS A DIGIT, NOT A LEADING ZERO.
    =======================================================
    The single tens block this used to have skipped itself whenever the
    remainder was below ten. That is right for 8 ("8", not "08") and wrong for
    108, which came out "18" -- the hundreds digit had already been written, so
    the zero was no longer leading. Every dialog id from 100 to 109 asked for
    the clip of 10 to 19 instead.

    Measured on the Highwind: fship_25 dialog 108 is Tifa's "Hey, Cloud, tell
    me it'll be all right?" and it played `18a.ogg`, Barret's "Well, you're
    pretty damn optimistic!"; 109 played 19. Where the field had no clip in
    10..19 the line was simply silent, which is the other half of the
    "some lines just aren't voiced" reports.

    So the tens digit is emitted unconditionally on the three-digit path and
    suppressed only on the path where nothing has been written yet. The block
    is spelled twice rather than guarded by a flag register: every register
    this helper may touch is already spoken for by its callers, and eight
    words is cheaper than finding a fifth scratch register that is free at all
    four call sites.
    """
    a.emit(A.mov_reg(10, source))
    a.emit(A.cmp_imm(10, 100))
    a.bcond(label_prefix + '_below_hundred', A.LT)
    a.emit(A.movz(11, 100))
    a.emit(_udiv(12, 10, 11))
    a.emit(A.add_imm(8, 12, ord('0')))
    _emit_append_byte(a, 8)
    a.emit(A.mul(13, 12, 11))
    a.emit(A.sub_reg(10, 10, 13))
    # No test: the hundreds digit is written, so 0 here must print.
    a.emit(A.movz(11, 10))
    a.emit(_udiv(12, 10, 11))
    a.emit(A.add_imm(8, 12, ord('0')))
    _emit_append_byte(a, 8)
    a.emit(A.mul(13, 12, 11))
    a.emit(A.sub_reg(10, 10, 13))
    a.b(label_prefix + '_ones')
    a.label(label_prefix + '_below_hundred')
    a.emit(A.cmp_imm(10, 10))
    a.bcond(label_prefix + '_ones', A.LT)
    a.emit(A.movz(11, 10))
    a.emit(_udiv(12, 10, 11))
    a.emit(A.add_imm(8, 12, ord('0')))
    _emit_append_byte(a, 8)
    a.emit(A.mul(13, 12, 11))
    a.emit(A.sub_reg(10, 10, 13))
    a.label(label_prefix + '_ones')
    a.emit(A.add_imm(8, 10, ord('0')))
    _emit_append_byte(a, 8)


def _emit_field_folder_key(a, scratch, forced_suffix=None,
                           choice_option_reg=None, label_prefix='folder',
                           key_off=VOICE_KEY_OFF, nocloud_prefix=False):
    """Build an FFNx field line or highlighted-option live lookup.

    The original ``open_field_file`` handler copies its input directly to the
    inline guest buffer at 0xCC1EF0 (x86 0x60BA46).  One ResolvePaged call
    therefore yields the characters themselves; treating the leading
    ``'fiel'`` bytes as a second guest pointer produces an arbitrary path.
    The caller may supply a bare name, a ``field\\`` path, or an extension,
    so mirror FFNx's last-backslash normalization and stop before ``.``.
    """
    # The suffix value must survive ResolvePaged. Ordinary lines receive the
    # page in W10; ASK option-change events pass their highlighted option.
    if nocloud_prefix:
        if choice_option_reg is None:
            raise ValueError('NoCloud prefixes are valid only for ASK options')
        # Preserve the option through both resolver calls in existing BSS;
        # X19..X23 are live translated-register state and must not be borrowed.
        a.emit(A.str_(choice_option_reg, 24, VOICE_CURRENT_OPTION_OFF))
        _guest(a, NOCLOUD_LEADER)
        a.emit(A.bl(a.pc(), RESOLVE_PAGED))
        # X25 is one of this cave's explicitly saved scratch registers and is
        # callee-saved by ResolvePaged. It carries the leader through lookup.
        a.emit(A.ldrb(25, 0, 0))
    else:
        a.emit(A.mov_reg(25, (10 if choice_option_reg is None
                              else choice_option_reg)))
    _guest(a, FIELD_FILE_NAME)
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    if nocloud_prefix:
        a.emit(A.ldr(14, 24, VOICE_CURRENT_OPTION_OFF))
    # Locate the basename in a bounded loader string.  FFNx calls strrchr on
    # this buffer; retain the last separator so both bare and prefixed field
    # names work.  The copy below additionally removes an optional extension.
    a.emit(_mov64(11, 0))
    a.emit(A.add_imm64(1, 24, key_off))
    if nocloud_prefix:
        a.emit(A.cmp_imm(25, 2))
        a.bcond(label_prefix + '_prefix_tifa', A.EQ)
        a.emit(A.cmp_imm(25, 8))
        a.bcond(label_prefix + '_prefix_cid', A.EQ)
        a.b(label_prefix + '_prefix_done')
        a.label(label_prefix + '_prefix_tifa')
        for char in b'nocloud/tifa/':
            a.emit(A.movz(8, char))
            _emit_append_byte(a, 8)
        a.b(label_prefix + '_prefix_done')
        a.label(label_prefix + '_prefix_cid')
        for char in b'nocloud/cid/':
            a.emit(A.movz(8, char))
            _emit_append_byte(a, 8)
        a.label(label_prefix + '_prefix_done')
    a.emit(A.movz(9, 0))
    a.label(label_prefix + '_field_find_separator')
    a.emit(A.ldrb(8, 0, 0))
    a.emit(A.cmp_imm(8, 0))
    a.bcond(label_prefix + '_field_separator_done', A.EQ)
    a.emit(A.cmp_imm(8, ord('\\')))
    a.bcond(label_prefix + '_field_separator_next', A.NE)
    a.emit(A.add_imm64(11, 0, 1))
    a.label(label_prefix + '_field_separator_next')
    a.emit(A.add_imm64(0, 0, 1))
    a.emit(A.add_imm(9, 9, 1))
    a.emit(A.cmp_imm(9, 31))
    a.bcond(label_prefix + '_field_find_separator', A.LT)
    a.label(label_prefix + '_field_separator_done')
    a.emit(_mov64(0, 11))
    a.emit(A.movz(9, 0))
    a.label(label_prefix + '_field_copy')
    a.emit(A.ldrb(8, 0, 0))
    a.emit(A.cmp_imm(8, 0))
    a.bcond(label_prefix + '_field_done', A.EQ)
    a.emit(A.cmp_imm(8, ord('.')))
    a.bcond(label_prefix + '_field_done', A.EQ)
    _emit_append_byte(a, 8)
    a.emit(A.add_imm64(0, 0, 1))
    a.emit(A.add_imm(9, 9, 1))
    a.emit(A.cmp_imm(9, 8))
    a.bcond(label_prefix + '_field_copy', A.LT)
    a.label(label_prefix + '_field_done')
    a.emit(A.movz(8, ord('/')))
    _emit_append_byte(a, 8)
    if forced_suffix is not None:
        suffix = forced_suffix.encode('ascii')
        if not suffix or len(suffix) > 8 or not suffix.isalnum():
            raise ValueError('forced field suffix must be a short ASCII alnum string')
        # Preserve the live field prefix and replace the complete line suffix.
        # This is a diagnostic boundary: it can establish whether the loader
        # path is correct independently of the live MESSAGE dialog/page bytes.
        for char in suffix:
            a.emit(A.movz(8, char))
            _emit_append_byte(a, 8)
    else:
        _emit_append_decimal(a, 28, label_prefix + '_dialog')
        if choice_option_reg is None:
            # FFNx names the first message page ``<dialog>a`` rather than
            # using an underscore/numeric suffix.
            a.emit(A.add_imm(8, 25, ord('a')))
            _emit_append_byte(a, 8)
        else:
            a.emit(A.movz(8, ord('_')))
            _emit_append_byte(a, 8)
            _emit_append_decimal(a, (14 if nocloud_prefix else 25),
                                 label_prefix + '_option')


def _emit_field_hash_key(a, scratch):
    """Build the compact, collision-free ``hhhhddpp`` field-voice key.

    This uses the same live loader buffer normalization as the FFNx-shaped
    folder builder above, but hashes the resulting basename rather than
    passing it verbatim to ``NativeOggPlayer``.  Eight ASCII characters keep
    the stock ``music stream thread '%s'`` title within Horizon's limit, so
    production playback does not need to modify the shared MusicStream
    diagnostic format used by BGM.
    """
    a.emit(A.mov_reg(25, 10))           # page survives ResolvePaged
    _guest(a, FIELD_FILE_NAME)
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    # FFNx's loader can expose a bare name, ``field\\name``, or a filename
    # with an extension.  Find the final separator first, exactly as the
    # folder-layout path does.
    a.emit(_mov64(11, 0))
    a.emit(A.movz(9, 0))
    a.label('hash_field_find_separator')
    a.emit(A.ldrb(8, 0, 0))
    a.emit(A.cmp_imm(8, 0))
    a.bcond('hash_field_separator_done', A.EQ)
    a.emit(A.cmp_imm(8, ord('\\')))
    a.bcond('hash_field_separator_next', A.NE)
    a.emit(A.add_imm64(11, 0, 1))
    a.label('hash_field_separator_next')
    a.emit(A.add_imm64(0, 0, 1))
    a.emit(A.add_imm(9, 9, 1))
    a.emit(A.cmp_imm(9, 31))
    a.bcond('hash_field_find_separator', A.LT)
    a.label('hash_field_separator_done')

    # Hash at most the eight canonical field-name bytes, stopping before an
    # optional extension.  This is the same base-93 u16 polynomial used by
    # voice_dat.field_hash_key_to_filename and is injective over all 787 maps.
    a.emit(_mov64(0, 11))
    a.emit(A.movz(11, 0))
    a.emit(A.movz(12, FIELD_NAME_HASH_MULTIPLIER))
    a.emit(A.movz(9, 0))
    a.label('hash_field_name')
    a.emit(A.ldrb(8, 0, 0))
    a.emit(A.cmp_imm(8, 0))
    a.bcond('hash_field_done', A.EQ)
    a.emit(A.cmp_imm(8, ord('.')))
    a.bcond('hash_field_done', A.EQ)
    a.emit(A.mul(11, 11, 12))
    a.emit(A.add_reg(11, 11, 8))
    a.emit(A.add_imm64(0, 0, 1))
    a.emit(A.add_imm(9, 9, 1))
    a.emit(A.cmp_imm(9, 8))
    a.bcond('hash_field_name', A.LT)
    a.label('hash_field_done')

    a.emit(A.add_imm64(1, 24, VOICE_KEY_OFF))
    _hex_nibble(a, 11, 12, 0)
    _hex_nibble(a, 11, 8, 1)
    _hex_nibble(a, 11, 4, 2)
    _hex_nibble(a, 11, 0, 3)
    _hex_nibble(a, 28, 4, 4)
    _hex_nibble(a, 28, 0, 5)
    _hex_nibble(a, 25, 4, 6)
    _hex_nibble(a, 25, 0, 7)


def _emit_world_folder_key(a, choice_option_reg=None,
                           label_prefix='world'):
    """Build ``_world/<dialog><page>`` or ``_world/<dialog>_<option>``."""
    a.emit(A.mov_reg(25, (10 if choice_option_reg is None
                          else choice_option_reg)))
    a.emit(A.add_imm64(1, 24, VOICE_KEY_OFF))
    for char in b'_world/':
        a.emit(A.movz(8, char))
        _emit_append_byte(a, 8)
    _emit_append_decimal(a, 28, label_prefix + '_dialog')
    if choice_option_reg is None:
        a.emit(A.add_imm(8, 25, ord('a')))
        _emit_append_byte(a, 8)
    else:
        a.emit(A.movz(8, ord('_')))
        _emit_append_byte(a, 8)
        _emit_append_decimal(a, 25, label_prefix + '_option')


def _build_world_command_cave(cave, addr, scratch, call_target, is_ask=False,
                              auto_advance=True):
    """Observe one world MESSAGE/ASK call and forward it unchanged.

    World script wrappers supply the real dialog id only while opening; the
    persistent loop later supplies zero. Retain every nonzero event id and use
    it for the shared window-state transitions, matching FFNx's intent while
    avoiding its historical wrong-world-dialog-id bug.
    """
    a = Asm(cave, addr)
    a.emit(A.stp64_pre(29, 30, A.SP, -0x60))
    a.emit(_stp_off(19, 20, 0x10))
    a.emit(_stp_off(21, 22, 0x20))
    a.emit(_stp_off(23, 24, 0x30))
    a.emit(_stp_off(25, 26, 0x40))
    a.emit(_stp_off(27, 28, 0x50))
    _bss_ptr(a, 24, scratch)
    a.emit(A.adrp(23, a.pc(), 0x12CE000))
    a.emit(A.ldr64(23, 23, 0x2B0))

    # Guest cdecl arguments at the replaced BL: window, dialog, and for ASK
    # first/last option plus WORD *current_question_id.  The translator has
    # already reserved the four-byte x86 return-address slot immediately
    # before its native BL, so argument 1 starts at ESP+4 (not ESP+0).
    a.emit(A.ldr(25, 23, 0x10))
    a.emit(A.add_imm(0, 25, 4))
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldr(26, 0, 0))
    a.emit(A.add_imm(0, 25, 8))
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldr(28, 0, 0))
    if is_ask:
        a.emit(A.add_imm(0, 25, 20))
        a.emit(A.bl(a.pc(), RESOLVE_PAGED))
        a.emit(A.ldr(0, 0, 0))
        a.emit(A.bl(a.pc(), RESOLVE_PAGED))
        a.emit(A.ldrh(8, 0, 0))
        a.emit(A.str_(8, 24, VOICE_CURRENT_OPTION_OFF))

    # Preserve the nonzero script/event id across the loop's zero-id updates.
    a.emit(A.add_imm64(13, 24, VOICE_WORLD_DIALOG_OFF))
    a.emit(_add_x_uxtw(13, 13, 26))
    a.emit(A.cmp_imm(28, 0))
    a.bcond('world_dialog_retained', A.EQ)
    a.emit(A.strb(28, 13, 0))
    a.label('world_dialog_retained')
    a.emit(A.ldrb(28, 13, 0))

    a.emit(A.lsl(8, 26, 4))
    a.emit(A.lsl(9, 26, 5))
    a.emit(A.add_reg(8, 8, 9))
    _guest(a, MESSAGE_STATE)
    a.emit(A.add_reg(0, 0, 8))
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldrh(27, 0, 0))
    a.emit(A.add_imm64(17, 24, VOICE_WORLD_LAST_OPCODE_OFF))
    a.emit(_add_x_uxtw(17, 17, 26))
    a.emit(A.lsl(9, 26, 1))
    a.emit(A.add_reg64(17, 17, 9))
    a.emit(A.ldrh(8, 17, 0))
    a.emit(A.add_imm64(16, 24, VOICE_WORLD_PAGE_OFF))
    a.emit(_add_x_uxtw(16, 16, 26))
    a.emit(A.ldrb(10, 16, 0))
    if is_ask:
        a.emit(A.add_imm64(14, 24, VOICE_WORLD_LAST_OPTION_OFF))
        a.emit(_add_x_uxtw(14, 14, 26))
        a.emit(A.ldrb(12, 14, 0))
        a.emit(A.ldr(15, 24, VOICE_CURRENT_OPTION_OFF))

    a.emit(A.cmp_imm(27, 0))
    a.bcond('world_opening', A.EQ)
    a.emit(A.cmp_imm(8, 0))
    a.bcond('world_starting', A.EQ)
    a.emit(A.cmp_imm(8, 14))
    a.bcond('world_page_four', A.NE)
    a.emit(A.cmp_imm(27, 2))
    a.bcond('world_paging', A.EQ)
    a.label('world_page_four')
    a.emit(A.cmp_imm(8, 4))
    a.bcond('world_option_check' if is_ask else 'world_closing_check', A.NE)
    a.emit(A.cmp_imm(27, 8))
    a.bcond('world_paging', A.EQ)
    if is_ask:
        a.label('world_option_check')
        a.emit(A.cmp_reg(12, 15))
        a.bcond('world_queue_changed_choice', A.NE)
    a.label('world_closing_check')
    a.emit(A.cmp_imm(27, 7))
    a.bcond('world_update', A.NE)
    a.emit(A.cmp_reg(8, 27))
    a.bcond('world_update', A.EQ)
    a.b('world_closing')

    a.label('world_opening')
    a.emit(A.strb(A.WZR, 16, 0))
    if is_ask:
        a.emit(A.movz(8, 0xFF))
        a.emit(A.strb(8, 14, 0))
        a.emit(A.str_(A.WZR, 24, VOICE_INITIAL_OPTION_OFF))
        a.emit(A.str_(A.WZR, 24, VOICE_VARIANT_FALLBACK_OFF))
        a.b('world_update_opcode')
    a.b('world_update')
    a.label('world_starting')
    a.emit(A.strb(A.WZR, 16, 0))
    a.emit(A.movz(10, 0))
    if is_ask:
        # Capture the initial option key. The service first probes the normal
        # ASK prompt, falls back immediately if it is absent, or chains this
        # option after a real prompt completes.
        a.emit(A.movz(8, 1))
        a.emit(A.str_(8, 24, VOICE_INITIAL_OPTION_OFF))
        a.b('world_queue_choice')
    a.b('world_queue_line')
    a.label('world_paging')
    a.emit(A.add_imm(10, 10, 1))
    a.emit(A.strb(10, 16, 0))
    a.b('world_queue_line')

    a.label('world_closing')
    a.emit(A.ldr64(9, 24, VOICE_PLAYER_OFF))
    a.cbz64(9, 'world_pending_close')
    a.emit(A.ldr(8, 24, VOICE_OWNER_OFF))
    a.emit(A.cmp_reg(8, 26))
    a.bcond('world_queue_stop', A.EQ)
    a.b('world_pending_close')
    a.label('world_pending_close')
    if auto_advance:
        a.emit(A.ldr(8, 24, VOICE_WAITING_WINDOW_OFF))
        a.emit(A.cmp_imm(8, 1))
        a.bcond('world_pending_command', A.NE)
        a.emit(A.ldr(8, 24, VOICE_OWNER_OFF))
        a.emit(A.cmp_reg(8, 26))
        a.bcond('world_queue_stop', A.EQ)
    a.label('world_pending_command')
    a.emit(A.ldr(8, 24, VOICE_PENDING_CMD_OFF))
    a.emit(A.cmp_imm(8, VOICE_CMD_PLAY))
    a.bcond('world_update', A.NE)
    a.emit(A.ldr(8, 24, VOICE_PENDING_OWNER_OFF))
    a.emit(A.cmp_reg(8, 26))
    a.bcond('world_update', A.NE)
    a.label('world_queue_stop')
    if is_ask:
        a.emit(A.str_(A.WZR, 24, VOICE_INITIAL_OPTION_OFF))
        a.emit(A.str_(A.WZR, 24, VOICE_VARIANT_FALLBACK_OFF))
    a.emit(A.movz(8, VOICE_CMD_STOP))
    a.emit(A.str_(8, 24, VOICE_PENDING_CMD_OFF))
    a.b('world_update')

    a.label('world_queue_line')
    _emit_world_folder_key(a, label_prefix='world_line')
    a.b('world_publish_play')
    if is_ask:
        a.label('world_queue_changed_choice')
        a.emit(A.str_(A.WZR, 24, VOICE_INITIAL_OPTION_OFF))
        a.label('world_queue_choice')
        a.emit(A.str_(A.WZR, 24, VOICE_VARIANT_FALLBACK_OFF))
        _emit_world_folder_key(a, choice_option_reg=15,
                               label_prefix='world_choice')
        a.emit(A.ldr(8, 24, VOICE_INITIAL_OPTION_OFF))
        a.cbz64(8, 'world_publish_play')
        a.emit(A.strb(A.WZR, 1, 0))
        _emit_bss_key_copy(a, 24, VOICE_KEY_OFF, VOICE_INITIAL_KEY_OFF)
        a.b('world_queue_line')
    a.label('world_publish_play')
    a.emit(A.strb(A.WZR, 1, 0))
    a.emit(A.str_(26, 24, VOICE_PENDING_OWNER_OFF))
    a.emit(A.movz(8, 0 if is_ask else 1))
    a.emit(A.str_(8, 24, VOICE_PENDING_AUTO_OFF))
    a.emit(A.movz(8, VOICE_CMD_PLAY))
    a.emit(A.str_(8, 24, VOICE_PENDING_CMD_OFF))

    a.label('world_update')
    if is_ask:
        a.emit(A.ldr(8, 24, VOICE_CURRENT_OPTION_OFF))
        a.emit(A.add_imm64(14, 24, VOICE_WORLD_LAST_OPTION_OFF))
        a.emit(_add_x_uxtw(14, 14, 26))
        a.emit(A.strb(8, 14, 0))
        a.label('world_update_opcode')
    a.emit(A.add_imm64(17, 24, VOICE_WORLD_LAST_OPCODE_OFF))
    a.emit(_add_x_uxtw(17, 17, 26))
    a.emit(A.lsl(8, 26, 1))
    a.emit(A.add_reg64(17, 17, 8))
    a.emit(A.strh(27, 17, 0))

    # Forward through the original translated callee while the caller's LR is
    # still protected by this native frame, then return like the replaced BL.
    a.emit(A.bl(a.pc(), call_target))
    a.emit(_ldp_off(27, 28, 0x50))
    a.emit(_ldp_off(25, 26, 0x40))
    a.emit(_ldp_off(23, 24, 0x30))
    a.emit(_ldp_off(21, 22, 0x20))
    a.emit(_ldp_off(19, 20, 0x10))
    a.emit(A.ldp64_post(29, 30, A.SP, 0x60))
    a.emit(0xD65F03C0)                  # ret
    return a.resolve()


def _build_message_command_cave(cave, addr, scratch, forced_name=None,
                                forced_alt_name=None,
                                build_key_then_forced=False,
                                forced_suffix=None,
                                field_name_prefix_bytes=6,
                                key_mode='hash',
                                folder_forced_suffix=None,
                                construct_key=True,
                                stop_on_close=True,
                                publish_on_dialog_change=True,
                                first_in_burst_wins=True,
                                publication_mask=VOICE_PUBLISH_ALL,
                                window_param_offset=1,
                                dialog_param_offset=2,
                                publish_commands=True,
                                choice_options=False,
                                allow_auto_advance=True,
                                hold_until_window_close=False,
                                resume_instruction=MESSAGE_ORIG,
                                resume_address=MESSAGE_HOOK + 4,
                                resume_call_target=None):
    """Observe MESSAGE transitions and publish a command for FIELD_HOOK.

    This is intentionally the old transition reader with all NativeOggPlayer
    calls removed.  MESSAGE executes in the translated guest dispatcher,
    whereas the field callback is a conventional native call boundary.  The
    BSS command is the only communication between them.
    """
    if forced_alt_name is not None and forced_name is None:
        raise ValueError('alternate voice name requires a primary name')
    if key_mode not in ('hash', 'folder'):
        raise ValueError('field voice key mode must be hash or folder')
    if folder_forced_suffix is not None and key_mode != 'folder':
        raise ValueError('folder suffix diagnostic requires folder key mode')
    if choice_options and key_mode != 'folder':
        raise ValueError('ASK option routing requires folder key mode')
    if choice_options and not construct_key:
        raise ValueError('MESSAGE publication diagnostic does not support ASK')
    if publication_mask & ~VOICE_PUBLISH_ALL:
        raise ValueError('unknown MESSAGE publication mask %r'
                         % publication_mask)
    if not (0 < window_param_offset < 16 and
            0 < dialog_param_offset < 16):
        raise ValueError('script parameter offsets must be positive small bytes')
    forced_name_length = 8
    if forced_name is not None:
        # Validate both paths before emitting either branch, and keep their
        # explicit terminator at the same offset in the toggle diagnostic.
        if forced_alt_name is not None and len(forced_alt_name) != len(forced_name):
            raise ValueError('alternating forced voice names must have equal length')
        forced_name_length = len(forced_name)

    a = Asm(cave, addr)
    _bss_ptr(a, 16, scratch)
    for i, reg in enumerate(range(24, 29)):
        a.emit(A.str64(reg, 16, VOICE_MESSAGE_SAVE_OFF + i * 8))
    a.emit(A.str64(30, 16, VOICE_MESSAGE_LR_OFF))
    a.emit(_mov64(24, 16))

    if choice_options:
        # At ASK_PRE_HOOK the translated x86 caller has pushed five guest
        # arguments and reserved its four-byte return-address slot. Therefore
        # [guest ESP+20], not +16, is WORD *current_question_id. FFNx reads
        # that value before calling the original ASK updater, so do the same.
        a.emit(A.ldr(8, 21, 0x10))
        a.emit(A.add_imm(0, 8, 20))
        a.emit(A.bl(a.pc(), RESOLVE_PAGED))
        a.emit(A.ldr(0, 0, 0))
        a.emit(A.bl(a.pc(), RESOLVE_PAGED))
        a.emit(A.ldrh(8, 0, 0))
        a.emit(A.str_(8, 24, VOICE_CURRENT_OPTION_OFF))

    # Derive the opcode parameters from the live script cursor.  MESSAGE is
    # ``+1 window,+2 dialog``; ASK is ``+2 window,+3 dialog``.  Both feed the
    # same vanilla state array, exactly as FFNx's voice wrappers do.
    _guest(a, CURRENT_WINDOW)
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldrb(26, 0, 0))
    _guest(a, SCRIPT_CURSOR)
    a.emit(A.add_reg_lsl(0, 0, 26, 1))
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldrh(27, 0, 0))
    _guest(a, FIELD_SCRIPT_PTR)
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldr(28, 0, 0))
    a.emit(A.add_reg(0, 28, 27))
    a.emit(A.add_imm(0, 0, window_param_offset))
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldrb(26, 0, 0))
    a.emit(A.add_reg(0, 28, 27))
    a.emit(A.add_imm(0, 0, dialog_param_offset))
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldrb(28, 0, 0))
    a.emit(A.lsl(8, 26, 4))
    a.emit(A.lsl(9, 26, 5))
    a.emit(A.add_reg(8, 8, 9))
    _guest(a, MESSAGE_STATE)
    a.emit(A.add_reg(0, 0, 8))
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldrh(27, 0, 0))

    a.emit(A.add_imm64(17, 24, VOICE_LAST_OPCODE_OFF))
    a.emit(_add_x_uxtw(17, 17, 26))
    a.emit(A.lsl(9, 26, 1))
    a.emit(A.add_reg64(17, 17, 9))
    a.emit(A.ldrh(8, 17, 0))
    if publish_on_dialog_change:
        # The entry is packed <dialog:8><state:8> -- see `update`. Split it
        # so the state tests below are unchanged and the dialog is available
        # for the "different line" test.
        a.emit(A.lsr(9, 8, 8))
        a.emit(A.and_mask(8, 8, 8))
    a.emit(A.add_imm64(16, 24, VOICE_PAGE_OFF))
    a.emit(_add_x_uxtw(16, 16, 26))
    a.emit(A.ldrb(10, 16, 0))
    if choice_options:
        a.emit(A.add_imm64(14, 24, VOICE_LAST_OPTION_OFF))
        a.emit(_add_x_uxtw(14, 14, 26))
        a.emit(A.ldrb(12, 14, 0))
        a.emit(A.ldr(15, 24, VOICE_CURRENT_OPTION_OFF))

    a.emit(A.cmp_imm(27, 0))
    a.bcond('opening', A.EQ)
    # A RECORDED STATE OF 0 STILL MEANS "THIS WINDOW IS FRESH".
    # ========================================================
    # Build 448 narrowed this to a virgin record, to stop a window that
    # closes and reopens from requesting the same line twice. That cured the
    # duplicate and caused a worse bug, which the emulator caught before it
    # shipped: the per-window record is NOT cleared between fields, so
    #
    #     chrin_1b w1/d75 plays, closes   -> record <75, 0>
    #     the next field uses w1 for d75  -> dialog "unchanged" -> SILENT
    #
    # Those are two sides of one missing fact. Nothing here can tell "the same
    # window reopening the same line" from "a new scene's line that happens to
    # match", because the record does not know which field wrote it. Guessing
    # either way trades a repeat for a missing line, and a missing line is the
    # bug this whole runtime exists to avoid.
    #
    # So the publish rule is back as it was, and the duplicate is stopped
    # where it can be identified without that fact -- see `completed`, which
    # refuses to drain a pending request for the line that just finished.
    a.emit(A.cmp_imm(8, 0))
    a.bcond('starting', A.EQ)
    if publish_on_dialog_change:
        # The dialog id is the thing that identifies the line, and it is what
        # makes a non-blocking MESSAGE audible: the handler runs once, so
        # there is no earlier frame in which the state was zero.
        a.emit(A.cmp_reg(9, 28))
        a.bcond('starting', A.NE)
    a.emit(A.cmp_imm(8, 14))
    a.bcond('page_four', A.NE)
    a.emit(A.cmp_imm(27, 2))
    a.bcond('paging', A.EQ)
    a.label('page_four')
    a.emit(A.cmp_imm(8, 4))
    a.bcond('option_check' if choice_options else 'closing_check', A.NE)
    a.emit(A.cmp_imm(27, 8))
    a.bcond('paging', A.EQ)
    if choice_options:
        a.label('option_check')
        a.emit(A.cmp_reg(12, 15))
        a.bcond('queue_changed_choice', A.NE)
    a.label('closing_check')
    a.emit(A.cmp_imm(27, 7))
    a.bcond('update', A.NE)
    a.emit(A.cmp_reg(8, 27))
    a.bcond('update', A.EQ)
    a.b('closing')

    a.label('opening')
    a.emit(A.strb(A.WZR, 16, 0))
    if choice_options:
        a.emit(A.movz(8, 0xFF))
        a.emit(A.strb(8, 14, 0))
        a.emit(A.str_(A.WZR, 24, VOICE_INITIAL_OPTION_OFF))
        a.emit(A.str_(A.WZR, 24, VOICE_VARIANT_FALLBACK_OFF))
        a.b('update_opcode')
    a.b('update')
    a.label('starting')
    a.emit(A.strb(A.WZR, 16, 0))
    a.emit(A.movz(10, 0))
    if choice_options and publish_commands:
        a.emit(A.movz(8, 1))
        a.emit(A.str_(8, 24, VOICE_INITIAL_OPTION_OFF))
        a.b('queue_choice')
    a.b('queue_play' if publish_commands else 'update')
    a.label('paging')
    a.emit(A.add_imm(10, 10, 1))
    a.emit(A.strb(10, 16, 0))
    a.b('queue_play' if publish_commands else 'update')

    a.label('closing')
    if not publish_commands:
        a.b('update')
    # Do not let an unrelated window close interrupt the active speaker or
    # erase another window's not-yet-consumed PLAY command.  OWNER is
    # zero-filled with the rest of BSS, so it is meaningful only while an
    # actual player exists; treating that default zero as a live window-0
    # owner let an idle window-0 close cancel first lines opened on window 1
    # or 2 before FIELD_HOOK had a chance to construct their player.
    # A CLOSE THIS CAVE CAN SEE IS ALWAYS A PLAYER DISMISSAL.
    # =======================================================
    # This cave lives inside the MESSAGE OPCODE HANDLER, and that decides
    # which closes it is even capable of observing:
    #
    #   blocking MESSAGE      the script parks on the opcode, so the handler
    #                         re-runs every frame until the message ends.
    #                         `closing` (state 7) is reached, at the moment
    #                         the player advanced the box.
    #
    #   non-blocking MESSAGE  `WINDOW; MESSAGE; WAIT n; WCLS`. The handler
    #                         runs once; the window is closed later by WCLS,
    #                         a different opcode that does not run this cave.
    #                         `closing` is NEVER reached for it.
    #
    # So stopping here cannot cut off a line that outlasts a script-timed
    # window -- md8_1's 7.3-second Wedge scream behind a 39-frame window is
    # invisible to this path either way. Build 427 turned the stop off to
    # save that line, but the line was silent for the reason build 428 fixed:
    # the request was never published at all. Turning the stop off as well
    # was over-broad, and it showed -- one dialogue's audio ran on into the
    # next conversation.
    #
    # Stopping is therefore the default again, and it is the one that feels
    # right: dismiss a box and its voice stops with it.
    # `SEVENTH_NX_VOICE_CLOSE=play` restores the run-on in one ExeFS-only
    # rebuild.
    #
    # Worth knowing: with Echo-S's own `Auto` option on, this barely fires.
    # The runtime advances the window when the clip ends, so the close comes
    # after the audio rather than interrupting it.
    a.emit(A.ldr64(9, 24, VOICE_PLAYER_OFF))
    a.cbz64(9, 'closed_player')
    a.emit(A.ldr(8, 24, VOICE_OWNER_OFF))
    a.emit(A.cmp_reg(8, 26))
    a.bcond('queue_stop' if stop_on_close else 'update', A.EQ)
    a.b('pending_close_check')
    a.label('closed_player')
    if hold_until_window_close:
        # Natural completion deletes the native player before the synthetic
        # OK reaches MESSAGE. Keep the owning window independently so its
        # subsequent close can release the BGM duck and clear that ownership.
        a.emit(A.ldr(8, 24, VOICE_WAITING_WINDOW_OFF))
        a.emit(A.cmp_imm(8, 1))
        a.bcond('pending_close_check', A.NE)
        a.emit(A.ldr(8, 24, VOICE_OWNER_OFF))
        a.emit(A.cmp_reg(8, 26))
        a.bcond('queue_stop', A.EQ)
    a.label('pending_close_check')
    a.emit(A.ldr(8, 24, VOICE_PENDING_CMD_OFF))
    a.emit(A.cmp_imm(8, VOICE_CMD_PLAY))
    a.bcond('update', A.NE)
    a.emit(A.ldr(8, 24, VOICE_PENDING_OWNER_OFF))
    a.emit(A.cmp_reg(8, 26))
    a.bcond('update', A.NE)
    if not stop_on_close:
        # `SEVENTH_NX_VOICE_CLOSE=play`: keep the queued request instead of
        # cancelling it when the window shuts before the service could build
        # the player.
        a.b('update')
    # Emitted only while something can still branch here: the close-cancel
    # itself, or auto-advance's ownership release after a natural end. With
    # neither, leaving the block in would be unreachable words spent out of
    # the padding pool -- and would make "does a close cancel the line?"
    # unanswerable by reading the cave.
    if stop_on_close or hold_until_window_close:
        a.label('queue_stop')
        if choice_options:
            a.emit(A.str_(A.WZR, 24, VOICE_INITIAL_OPTION_OFF))
            a.emit(A.str_(A.WZR, 24, VOICE_VARIANT_FALLBACK_OFF))
        if publication_mask & VOICE_PUBLISH_COMMAND:
            a.emit(A.movz(8, VOICE_CMD_STOP))
            a.emit(A.str_(8, 24, VOICE_PENDING_CMD_OFF))
        a.b('update')

    a.label('queue_play')
    if first_in_burst_wins:
        # THREE ENTITIES, ONE SCRIPT TICK, ONE SLOT.
        # =========================================
        # `chrin_1b` is the measured case -- Reno tells the grunts not to step
        # on the flowers and all three answer at once. Each grunt is its own
        # ENTITY and its whole routine is three opcodes:
        #
        #     WINDOW 1 ; MESSAGE 1,75 ; RET      <- 75a.ogg, 2.85s, all three
        #     WINDOW 2 ; MESSAGE 2,76 ; RET         voices in one recording
        #     WINDOW 3 ; MESSAGE 3,77 ; RET
        #
        # Echo-S voices only 75, because that clip IS the overlap. All three
        # routines run in the same tick, so this cave published three times
        # before the service built anything: 75's key was overwritten by 76's
        # and then 77's, the last one has no clip, its preflight failed, and
        # the scene was silent.
        #
        # `SEVENTH_NX_VOICE_FOREIGN` could not help. Every setting there
        # decides what a foreign window does to an AUDIBLE line, and there was
        # never an audible line to protect -- which is exactly why `defer`
        # changed nothing on hardware.
        #
        # So: while a PLAY is pending and UNCONSUMED, a publish from a
        # DIFFERENT window leaves it alone. Same window still overwrites,
        # because that is a page advance and the newest line is the right one.
        # The guard is live only inside one burst -- the service clears
        # PENDING_CMD the moment it takes the request -- so ordinary
        # conversation is untouched.
        a.emit(A.ldr(8, 24, VOICE_PENDING_CMD_OFF))
        a.emit(A.cmp_imm(8, VOICE_CMD_PLAY))
        a.bcond('burst_clear', A.NE)
        a.emit(A.ldr(8, 24, VOICE_PENDING_OWNER_OFF))
        a.emit(A.cmp_reg(8, 26))
        a.bcond('burst_clear', A.EQ)
        a.b('update')
        a.label('burst_clear')
    if construct_key and (forced_name is None or build_key_then_forced):
        if key_mode == 'folder':
            _emit_field_folder_key(a, scratch,
                                   forced_suffix=folder_forced_suffix)
        else:
            _emit_field_hash_key(a, scratch)
            if forced_suffix is not None:
                _emit_fixed_voice_suffix(a, *forced_suffix)
    if construct_key and forced_name is not None:
        # Dynamic-key diagnostics build the real string first, then replace
        # it with a fixed known-good filename.  Restore the buffer base after
        # the variable-length field basename before those fixed stores.
        a.emit(A.add_imm64(1, 24, VOICE_KEY_OFF))
    if construct_key and forced_name is not None and forced_alt_name is None:
        # Two ASCII bytes per little-endian halfword: no rodata tail is
        # needed, so the test package still contains only main + one Ogg.
        _emit_fixed_voice_name(a, forced_name)
    elif construct_key and forced_name is not None:
        a.emit(A.ldr(8, 24, VOICE_PROBE_NAME_TOGGLE_OFF))
        a.emit(A.mov_reg(9, 8))
        # Skip the primary store (8 words) and its branch (1 word).
        a.emit(A.tbnz(8, 0, a.pc(), a.pc() + 44))
        _emit_fixed_voice_name(a, forced_name)
        a.b('fixed_name_done')
        _emit_fixed_voice_name(a, forced_alt_name)
        a.label('fixed_name_done')
        a.emit(A.eor_imm1(9, 9))
        a.emit(A.str_(9, 24, VOICE_PROBE_NAME_TOGGLE_OFF))
    if construct_key and choice_options:
        a.b('publish_play')
        a.label('queue_changed_choice')
        a.emit(A.str_(A.WZR, 24, VOICE_INITIAL_OPTION_OFF))
        a.label('queue_choice')
        # Always retain the normal VO key. Echo-S's NoCloud overlays are
        # sparse, so a Tifa/Cid leader-specific miss must transparently retry
        # this base path rather than silence the highlighted option.
        _emit_field_folder_key(
            a, scratch, choice_option_reg=15,
            label_prefix='choice_base',
            key_off=VOICE_VARIANT_FALLBACK_KEY_OFF)
        a.emit(A.strb(A.WZR, 1, 0))
        # The first ResolvePaged call may clobber caller-saved W15; reload the
        # live option before constructing the leader-qualified primary key.
        a.emit(A.ldr(15, 24, VOICE_CURRENT_OPTION_OFF))
        _emit_field_folder_key(a, scratch, choice_option_reg=15,
                               label_prefix='choice', nocloud_prefix=True)
        a.emit(A.cmp_imm(25, 2))
        a.bcond('choice_has_variant', A.EQ)
        a.emit(A.cmp_imm(25, 8))
        a.bcond('choice_has_variant', A.EQ)
        a.emit(A.str_(A.WZR, 24, VOICE_VARIANT_FALLBACK_OFF))
        a.b('choice_variant_flag_done')
        a.label('choice_has_variant')
        a.emit(A.movz(8, 1))
        a.emit(A.str_(8, 24, VOICE_VARIANT_FALLBACK_OFF))
        a.label('choice_variant_flag_done')
        a.emit(A.ldr(8, 24, VOICE_INITIAL_OPTION_OFF))
        a.cbz64(8, 'publish_play')
        a.emit(A.strb(A.WZR, 1, 0))
        _emit_bss_key_copy(a, 24, VOICE_KEY_OFF, VOICE_INITIAL_KEY_OFF)
        a.b('queue_play')
        a.label('publish_play')
    # Folder construction leaves X1 at the end of its variable-length key;
    # flat keys and fixed diagnostics retain the older known offset.
    if construct_key and key_mode == 'folder' and forced_name is None:
        a.emit(A.strb(A.WZR, 1, 0))
    elif construct_key:
        a.emit(A.strb(A.WZR, 1, forced_name_length))
    # NO REPLAY GUARD LIVES HERE ANY MORE, AND THAT IS DELIBERATE.
    # ============================================================
    # Builds 448, 451 and 452 each refused the duplicate on this side, and the
    # recording of the scene shows why none of them could work: the clip
    # restarts SIX FRAMES after it ends, which is the service's retirement
    # path, not a publish. Worse, a refusal here is not free -- the key buffer
    # has already been rewritten by the time any test on it can run, so
    # refusing leaves the shared mailbox describing a request nobody made.
    # The guard now lives at the drain, in `_build_field_clocked_voice_service`,
    # where the request and the line that just played can be compared directly.
    if publication_mask & VOICE_PUBLISH_OWNER:
        a.emit(A.str_(26, 24, VOICE_PENDING_OWNER_OFF))
    if publication_mask & VOICE_PUBLISH_AUTO:
        a.emit(A.movz(8, 1 if allow_auto_advance else 0))
        a.emit(A.str_(8, 24, VOICE_PENDING_AUTO_OFF))
    if publication_mask & VOICE_PUBLISH_COMMAND:
        a.emit(A.movz(8, VOICE_CMD_PLAY))
        a.emit(A.str_(8, 24, VOICE_PENDING_CMD_OFF))

    a.label('update')
    if choice_options:
        a.emit(A.ldr(8, 24, VOICE_CURRENT_OPTION_OFF))
        a.emit(A.add_imm64(14, 24, VOICE_LAST_OPTION_OFF))
        a.emit(_add_x_uxtw(14, 14, 26))
        a.emit(A.strb(8, 14, 0))
        a.label('update_opcode')
    a.emit(A.add_imm64(17, 24, VOICE_LAST_OPCODE_OFF))
    a.emit(_add_x_uxtw(17, 17, 26))
    a.emit(A.lsl(8, 26, 1))
    a.emit(A.add_reg64(17, 17, 8))
    if publish_on_dialog_change:
        # Pack <dialog:8><state:8>. The state is one of 0/2/4/7/8/14 and the
        # dialog id was read with LDRB, so both fit and the halfword this
        # table already held is enough for the pair.
        a.emit(A.lsl(9, 28, 8))
        a.emit(A.add_reg(9, 9, 27))
        a.emit(A.strh(9, 17, 0))
    else:
        a.emit(A.strh(27, 17, 0))
    a.emit(_mov64(16, 24))
    for i, reg in enumerate(range(24, 29)):
        a.emit(A.ldr64(reg, 16, VOICE_MESSAGE_SAVE_OFF + i * 8))
    a.emit(A.ldr64(30, 16, VOICE_MESSAGE_LR_OFF))
    # The production route enters immediately before the stock MESSAGE
    # window-update BL, matching FFNx's ``opcode_voice_message`` ordering:
    # observe the old transition, publish a request, then let the game
    # mutate the window state.  The historical post-update splice remains
    # available for narrow diagnostics through these explicit resume values.
    if resume_call_target is None:
        a.emit(resume_instruction)
    else:
        # A raw BL encodes a PC-relative displacement and can only be reused
        # at its original site.  The pre-MESSAGE cave must call the same
        # window-update target from its own address.
        a.emit(A.bl(a.pc(), resume_call_target))
    a.emit(A.b(a.pc(), resume_address))
    return a.resolve()


def _build_disable_name_change_cave(cave, addr, scratch):
    """ARM64 port of Echo-S's PC ``Disable Name Change`` HEXT cave.

    The PC patch auto-copies the field-provided default name from DD45F0 to
    the selected 0x84-byte party-name slot, sets the menu skip byte, and then
    resumes the vanilla name-menu state machine.  This reproduces those guest
    memory operations through ResolvePaged; it does not hard-code any names.
    """
    a = Asm(cave, addr)
    _bss_ptr(a, 16, scratch)
    for i, reg in enumerate(range(24, 29)):
        a.emit(A.str64(reg, 16, NAME_CHANGE_SAVE_OFF + i * 8))
    a.emit(A.str64(30, 16, NAME_CHANGE_LR_OFF))
    a.emit(_mov64(24, 16))

    # Replay the original translated store (W21 is the live DC12E4 value),
    # including its guest-EDX shadow at context+8, before adding Echo-S's
    # automatic copy path.
    a.emit(A.str_(21, 22, 8))
    _guest(a, NAME_MENU_ACTIVE)
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.str_(21, 0, 0))

    _guest(a, NAME_MENU_INDEX)
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldrb(25, 0, 0))
    a.emit(A.cmp_imm(25, 9))
    a.bcond('name_change_resume_normal', A.GE)

    a.label('name_change_copy_target')
    _guest(a, NAME_MENU_DEST)
    a.emit(A.movz(9, 0x84))
    a.emit(A.mul(9, 25, 9))
    a.emit(A.add_reg(27, 0, 9))
    a.emit(A.movz(28, 0))
    a.label('name_change_copy_byte')
    # The PC cave publishes its copy cursor to DD46FC on every iteration.
    # Preserve that side effect; the downstream finalizer reads the same
    # name-menu state byte.
    _guest(a, NAME_MENU_ACTIVE)
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.strb(28, 0, 0))
    _guest(a, NAME_MENU_SOURCE)
    a.emit(A.add_reg(0, 0, 28))
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    # W26 is callee-saved and belongs to this cave. W9 was incorrect here:
    # ResolvePaged is allowed to clobber it before the destination store.
    a.emit(A.ldrb(26, 0, 0))
    a.emit(A.mov_reg(0, 27))
    a.emit(A.add_reg(0, 0, 28))
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.strb(26, 0, 0))
    a.emit(A.cmp_imm(26, 0xFF))
    a.bcond('name_change_copy_done', A.EQ)
    a.emit(A.cmp_imm(28, 8))
    a.bcond('name_change_copy_done', A.EQ)
    a.emit(A.add_imm(28, 28, 1))
    a.b('name_change_copy_byte')

    a.label('name_change_copy_done')
    _guest(a, NAME_MENU_SKIP_FLAG)
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.movz(9, 0xFF))
    a.emit(A.strb(9, 0, 0))
    # Echo-S's original HEXT has one intentional special case: once index 0
    # is copied it also fills slot 6 before rejoining the vanilla update.
    a.emit(A.cmp_imm(25, 0))
    a.bcond('name_change_resume_final', A.NE)
    a.emit(A.movz(25, 6))
    a.b('name_change_copy_target')

    a.label('name_change_resume_normal')
    # The PC cave executes ``xor edx,edx; mov dl,[DD46F8]`` before its
    # out-of-range branch.  Publish the same guest EDX state before resuming
    # the vanilla block.
    a.emit(A.str_(25, 22, 8))
    _bss_ptr(a, 16, scratch)
    for i, reg in enumerate(range(24, 29)):
        a.emit(A.ldr64(reg, 16, NAME_CHANGE_SAVE_OFF + i * 8))
    a.emit(A.ldr64(30, 16, NAME_CHANGE_LR_OFF))
    a.emit(A.b(a.pc(), NAME_CHANGE_RESUME))

    a.label('name_change_resume_final')
    # FF7's translator keeps guest EAX/ECX/EDX at context +0/+4/+8.  The PC
    # HEXT reaches 719CD3 with EAX = the copied name slot, ECX = the terminal
    # cursor, and EDX = (slot * 0x84) with DL replaced by the final source
    # byte.  The index-0 finalizer preserves EAX and EDX, so merely copying
    # guest memory leaves the opcode's completion state stale and blocks the
    # field script at the first Cloud SPCNM.  Reproduce the complete register
    # state before entering the translated finalizer.
    a.emit(A.movz(9, 0x84))
    a.emit(A.mul(9, 25, 9))
    a.emit(A.bfi(9, 26, 0, 8))
    a.emit(A.str_(27, 22, 0))
    a.emit(A.str_(28, 22, 4))
    a.emit(A.str_(9, 22, 8))
    _bss_ptr(a, 16, scratch)
    for i, reg in enumerate(range(24, 29)):
        a.emit(A.ldr64(reg, 16, NAME_CHANGE_SAVE_OFF + i * 8))
    a.emit(A.ldr64(30, 16, NAME_CHANGE_LR_OFF))
    a.emit(A.b(a.pc(), NAME_CHANGE_FINALIZE))
    return a.resolve()


def _build_auto_ok_input_cave(cave, addr, scratch):
    """Inject one FFNx-style raw OK press after the native input refresh.

    Voice completion happens later in ``field_loop_sub_63C17F``, after
    ``sub_6499F7`` has already derived FF7's edge-triggered input tables.  It
    therefore publishes a BSS flag for the next input pass.  This splice sits
    immediately after ``sub_649876`` refreshes/clears the physical controller
    globals and before D011C0 is shifted into bit 5 of the input mask.
    """
    a = Asm(cave, addr)
    _bss_ptr(a, 8, scratch)
    a.emit(A.ldr(9, 8, VOICE_AUTO_OK_PENDING_OFF))
    a.cbz64(9, 'resume')
    a.emit(A.str_(A.WZR, 8, VOICE_AUTO_OK_PENDING_OFF))
    # This is a B-spliced translated function, so preserve its existing LR
    # while ResolvePaged maps the raw guest status address.
    a.emit(A.stp64_pre(29, 30, A.SP, -0x10))
    _guest(a, INPUT_OK_BUTTON_STATUS)
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.movz(9, 1))
    a.emit(A.str_(9, 0, 0))
    a.emit(A.ldp64_post(29, 30, A.SP, 0x10))
    a.label('resume')
    a.emit(INPUT_POST_REFRESH_ORIG)
    a.emit(A.b(a.pc(), INPUT_POST_REFRESH_RESUME))
    return a.resolve()


def _emit_world_auto_ok(a):
    """Set the world loop's consumed OK bit, matching FFNx's FF7 path."""
    _guest(a, FIELD_GLOBAL_OBJECT_PTR)
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldr(0, 0, 0))
    a.emit(A.add_imm(0, 0, 0x80))
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldr(8, 0, 0))
    a.emit(A.movz(9, 0x20))
    a.emit(A.orr_lsl(8, 8, 9, 0))
    a.emit(A.str_(8, 0, 0))


def _build_battle_action_command_cave(cave, addr, scratch, probability):
    """Publish FFNx's exact battle action key from translated guest state.

    This splice is intentionally producer-only. Calling the native OGG
    object from translated battle code caused the early crash series; the
    native battle-frame service consumes this command on its next callback.
    """
    a = Asm(cave, addr)
    _bss_ptr(a, 16, scratch)
    for i, reg in enumerate(range(24, 29)):
        a.emit(A.str64(reg, 16, BATTLE_ACTION_SAVE_OFF + i * 8))
    a.emit(A.str64(30, 16, BATTLE_ACTION_LR_OFF))
    a.emit(_mov64(24, 16))

    # field6 is nonzero while the stock action-name delay counts down. Clear
    # the one-shot latch there so an identical immediately repeated action
    # can publish again when its own counter reaches zero.
    a.emit(A.cmp_imm(8, 0))
    a.bcond('battle_action_zero', A.EQ)
    a.emit(A.str_(A.WZR, 24, BATTLE_ACTION_ARMED_OFF))
    a.b('battle_action_resume_nonzero')

    a.label('battle_action_zero')
    # X0 still points at this effect100 record's field_6 (record + 2) from
    # the translated load immediately before BATTLE_ACTION_HOOK.  FFNx's
    # one-shot state belongs to that effect lifetime, not to the whole battle.
    # Retire our latch when this record's own n_frames at +0 reaches zero.
    # The previous global latch waited for field_6 to become positive; many
    # actions initialize it directly to zero, so one unvoiced physical attack
    # could suppress every later spell for the rest of the battle.
    a.emit(A.sub_imm64(9, 0, 2))
    a.emit(A.ldrh(9, 9, 0))
    a.emit(A.cmp_imm(9, 0))
    a.bcond('battle_action_retire', A.EQ)
    a.emit(A.ldr(9, 24, BATTLE_ACTION_ARMED_OFF))
    a.cbz64(9, 'battle_action_first_zero')
    a.b('battle_action_resume_zero')
    a.label('battle_action_retire')
    a.emit(A.str_(A.WZR, 24, BATTLE_ACTION_ARMED_OFF))
    a.b('battle_action_resume_zero')
    a.label('battle_action_first_zero')
    a.emit(A.movz(9, 1))
    a.emit(A.str_(9, 24, BATTLE_ACTION_ARMED_OFF))

    # Deterministic 100-step gate. It preserves the selected Echo-S percent
    # without needing its silent placeholder files at runtime.
    a.emit(A.ldr(8, 24, BATTLE_ACTION_COUNTER_OFF))
    a.emit(A.add_imm(9, 8, 1))
    a.emit(A.cmp_imm(9, 100))
    a.emit(A.csel(9, A.WZR, 9, A.GE))
    a.emit(A.str_(9, 24, BATTLE_ACTION_COUNTER_OFF))
    a.emit(A.cmp_imm(8, probability))
    a.bcond('battle_action_resume_zero', A.GE)

    _guest(a, BATTLE_ACTIVE_ACTOR)
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldrb(25, 0, 0))
    a.emit(A.cmp_imm(25, 3))
    a.bcond('battle_action_char', A.LT)
    a.emit(A.cmp_imm(25, 4))
    a.bcond('battle_action_resume_zero', A.LT)
    a.emit(A.cmp_imm(25, 10))
    a.bcond('battle_action_resume_zero', A.GE)
    a.b('battle_action_enemy')

    a.label('battle_action_char')
    a.emit(A.movz(9, BATTLE_ACTOR_STRIDE))
    a.emit(A.mul(9, 25, 9))
    _guest(a, BATTLE_CHAR_INDEX)
    a.emit(A.add_reg(0, 0, 9))
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldrb(26, 0, 0))
    a.emit(A.movz(8, ord('c')))
    a.emit(A.strb(8, 24, BATTLE_ACTION_KIND_OFF))
    a.b('battle_action_common')

    a.label('battle_action_enemy')
    a.emit(A.movz(9, BATTLE_ACTOR_STRIDE))
    a.emit(A.mul(9, 25, 9))
    _guest(a, BATTLE_FORMATION_ID)
    a.emit(A.add_reg(0, 0, 9))
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldrh(26, 0, 0))
    a.emit(A.movz(8, ord('e')))
    a.emit(A.strb(8, 24, BATTLE_ACTION_KIND_OFF))

    a.label('battle_action_common')
    a.emit(A.movz(9, BATTLE_COMMAND_STRIDE))
    a.emit(A.mul(9, 25, 9))
    _guest(a, BATTLE_COMMAND)
    a.emit(A.add_reg(0, 0, 9))
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldrb(27, 0, 0))
    a.emit(A.movz(9, BATTLE_ACTION_STRIDE))
    a.emit(A.mul(9, 25, 9))
    _guest(a, BATTLE_ACTION)
    a.emit(A.add_reg(0, 0, 9))
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldrh(28, 0, 0))

    # Exact key: battle/bc<character:02x><cmd:02x><action:04x><variant:01x>,
    # or be<formation:04x><cmd:02x><action:04x><variant:01x> for enemies.
    #
    # THE VARIANT DIGIT.
    # Echo-S gives each action a POOL of clips -- one to twenty-one of them,
    # nine typically -- and on PC FFNx shuffles within it. Without this digit
    # the key is one name, so Cloud says the same line every time he casts
    # Cure, which is not what the mod sounds like.
    #
    # The selector is the existing deterministic action counter, masked to
    # three bits, so this costs one load, one AND and one digit: no new BSS
    # (the block has 16 bytes of page slack and they are not being spent on
    # this) and no per-key pool size in the module. `echo_s_battlevo.VARIANTS`
    # is 8 to match, and it stages every key in all eight slots by repeating a
    # short pool -- so a slot can never be missing, which on hardware would be
    # silence with nothing in any log to explain it.
    _emit_battle_key_prefix(a, 24)
    a.emit(A.movz(8, ord('b')))
    a.emit(A.strb(8, 1, 0))
    a.emit(A.ldrb(11, 24, BATTLE_ACTION_KIND_OFF))
    a.emit(A.strb(11, 1, 1))
    a.emit(A.cmp_imm(11, ord('c')))
    a.bcond('battle_exact_char', A.EQ)
    _hex_nibble(a, 26, 12, 2)
    _hex_nibble(a, 26, 8, 3)
    _hex_nibble(a, 26, 4, 4)
    _hex_nibble(a, 26, 0, 5)
    _emit_battle_actor_separator(a, 6)
    _hex_nibble(a, 27, 4, 7)
    _hex_nibble(a, 27, 0, 8)
    _emit_battle_actor_separator(a, 9)
    _hex_nibble(a, 28, 12, 10)
    _hex_nibble(a, 28, 8, 11)
    _hex_nibble(a, 28, 4, 12)
    _hex_nibble(a, 28, 0, 13)
    a.emit(A.ldr(11, 24, BATTLE_ACTION_COUNTER_OFF))
    a.emit(A.and_mask(11, 11, BATTLE_VARIANT_BITS))
    _hex_nibble(a, 11, 0, 14)
    a.emit(A.strb(A.WZR, 1, 15))
    a.b('battle_exact_done')
    a.label('battle_exact_char')
    _hex_nibble(a, 26, 4, 2)
    _hex_nibble(a, 26, 0, 3)
    _emit_battle_actor_separator(a, 4)
    _hex_nibble(a, 27, 4, 5)
    _hex_nibble(a, 27, 0, 6)
    _emit_battle_actor_separator(a, 7)
    _hex_nibble(a, 28, 12, 8)
    _hex_nibble(a, 28, 8, 9)
    _hex_nibble(a, 28, 4, 10)
    _hex_nibble(a, 28, 0, 11)
    a.emit(A.ldr(11, 24, BATTLE_ACTION_COUNTER_OFF))
    a.emit(A.and_mask(11, 11, BATTLE_VARIANT_BITS))
    _hex_nibble(a, 11, 0, 12)
    a.emit(A.strb(A.WZR, 1, 13))
    a.label('battle_exact_done')

    # Command-only fallback is identical except for the ffff action suffix.
    _emit_bss_key_copy(a, 24, VOICE_KEY_OFF,
                       VOICE_VARIANT_FALLBACK_KEY_OFF)
    a.emit(A.add_imm64(1, 24, VOICE_VARIANT_FALLBACK_KEY_OFF +
                       len(BATTLE_KEY_PREFIX)))
    a.emit(A.ldrb(11, 24, BATTLE_ACTION_KIND_OFF))
    a.emit(A.cmp_imm(11, ord('c')))
    a.bcond('battle_fallback_char', A.EQ)
    a.emit(A.movz(8, ord('f')))
    for off in (10, 11, 12, 13):
        a.emit(A.strb(8, 1, off))
    a.b('battle_publish')
    a.label('battle_fallback_char')
    a.emit(A.movz(8, ord('f')))
    for off in (8, 9, 10, 11):
        a.emit(A.strb(8, 1, off))

    a.label('battle_publish')
    a.emit(A.movz(8, 1))
    a.emit(A.str_(8, 24, VOICE_VARIANT_FALLBACK_OFF))
    a.emit(A.add_imm(8, 25, 0x100))
    a.emit(A.str_(8, 24, VOICE_PENDING_OWNER_OFF))
    a.emit(A.str_(A.WZR, 24, VOICE_PENDING_AUTO_OFF))
    a.emit(A.movz(8, VOICE_CMD_PLAY))
    a.emit(A.str_(8, 24, VOICE_PENDING_CMD_OFF))

    a.label('battle_action_resume_zero')
    _bss_ptr(a, 16, scratch)
    for i, reg in enumerate(range(24, 29)):
        a.emit(A.ldr64(reg, 16, BATTLE_ACTION_SAVE_OFF + i * 8))
    a.emit(A.ldr64(30, 16, BATTLE_ACTION_LR_OFF))
    a.emit(A.b(a.pc(), BATTLE_ACTION_ZERO))
    a.label('battle_action_resume_nonzero')
    _bss_ptr(a, 16, scratch)
    for i, reg in enumerate(range(24, 29)):
        a.emit(A.ldr64(reg, 16, BATTLE_ACTION_SAVE_OFF + i * 8))
    a.emit(A.ldr64(30, 16, BATTLE_ACTION_LR_OFF))
    a.emit(A.b(a.pc(), BATTLE_ACTION_NONZERO))
    return a.resolve()


def _build_battle_text_command_cave(cave, addr, scratch):
    """Publish scripted dialogue and command-result text by raw-text hash.

    Battle buffer IDs >=256 point into the dynamic scene-message pool; lower
    IDs point into kernel2 section 16 (battle.txt). A compact FNV-1a hash is
    stable across both representations and avoids reimplementing FFNx's text
    expansion/tokenizer in injected code. Only staged hashes can construct a
    player, so every unrelated battle string remains an ordinary no-op.
    """
    a = Asm(cave, addr)
    _bss_ptr(a, 16, scratch)
    for index, reg in enumerate(range(24, 29)):
        a.emit(A.str64(reg, 16, BATTLE_TEXT_SAVE_OFF + index * 8))
    a.emit(A.str64(30, 16, BATTLE_TEXT_LR_OFF))
    a.emit(_mov64(24, 16))

    # The final zero frame retires this queue entry. Clear the one-shot latch
    # there so an identical line can be voiced when the slot is reused.
    _guest(a, BATTLE_TEXT_QUEUE + 5)
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldrb(8, 0, 0))
    a.emit(A.cmp_imm(8, 0))
    a.bcond('battle_text_clear', A.EQ)

    _guest(a, BATTLE_TEXT_QUEUE)
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldrsh(25, 0, 0))
    a.emit(A.cmp_imm(25, 0x100))
    a.bcond('battle_text_dialogue', A.GE)

    # Kernel battle text can carry Steal/Mug/Manipulate/Sense outcomes. Keep
    # character identity in the filename because Echo-S supplies a different
    # take for every party member.
    _guest(a, BATTLE_ACTIVE_ACTOR)
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldrb(26, 0, 0))
    a.emit(A.cmp_imm(26, 3))
    a.bcond('battle_text_clear', A.GE)
    a.emit(A.movz(9, BATTLE_ACTOR_STRIDE))
    a.emit(A.mul(9, 26, 9))
    _guest(a, BATTLE_CHAR_INDEX)
    a.emit(A.add_reg(0, 0, 9))
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldrb(26, 0, 0))             # Echo-S character id
    a.emit(A.lsl(8, 26, 16))
    a.emit(A.orr_lsl(27, 8, 25, 0))      # signature char<<16 | buffer
    a.b('battle_text_latch')

    a.label('battle_text_dialogue')
    a.emit(A.movz(26, 0xFFFF))            # no character component
    a.emit(A.movz(8, 2))
    a.emit(A.lsl(8, 8, 28))               # distinguish dynamic dialogue
    a.emit(A.orr_lsl(27, 8, 25, 0))

    a.label('battle_text_latch')
    a.emit(A.ldr(8, 24, BATTLE_TEXT_LAST_OFF))
    a.emit(A.cmp_reg(8, 27))
    a.bcond('battle_text_resume', A.EQ)
    a.emit(A.str_(27, 24, BATTLE_TEXT_LAST_OFF))

    # Resolve the encoded string pointer. Dynamic scene text is a compact
    # u16-offset pool; kernel text uses the same shape behind section 16.
    a.emit(A.cmp_imm(25, 0x100))
    a.bcond('battle_text_scene_pointer', A.GE)
    _guest(a, BATTLE_KERNEL_SECTION_OFFSETS +
           BATTLE_KERNEL_TEXT_SECTION * 2)
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldrh(8, 0, 0))
    a.emit(A.movz(9, BATTLE_KERNEL_DATA & 0xFFFF))
    a.emit(A.movk_hi(9, BATTLE_KERNEL_DATA >> 16))
    a.emit(A.add_reg(27, 9, 8))           # guest section base
    a.emit(A.lsl(8, 25, 1))
    a.emit(A.add_reg(0, 27, 8))
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldrh(8, 0, 0))
    a.emit(A.add_reg(0, 27, 8))
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(_mov64(28, 0))
    a.b('battle_text_hash_setup')

    a.label('battle_text_scene_pointer')
    a.emit(A.sub_imm(8, 25, 0x100))
    _guest(a, BATTLE_SCENE_TEXT_OFFSETS)
    a.emit(A.add_reg64_lsl(0, 0, 8, 1))
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(A.ldrh(8, 0, 0))
    a.emit(A.movz(9, BATTLE_SCENE_TEXT_DATA & 0xFFFF))
    a.emit(A.movk_hi(9, BATTLE_SCENE_TEXT_DATA >> 16))
    a.emit(A.add_reg(0, 9, 8))
    a.emit(A.bl(a.pc(), RESOLVE_PAGED))
    a.emit(_mov64(28, 0))

    a.label('battle_text_hash_setup')
    a.emit(A.movz(27, 0x9DC5))
    a.emit(A.movk_hi(27, 0x811C))         # FNV-1a offset basis
    a.emit(A.movz(9, 0x0193))
    a.emit(A.movk_hi(9, 0x0100))          # FNV-1a multiplier
    a.emit(A.movz(10, 0))
    a.label('battle_text_hash_loop')
    a.emit(A.ldrb(8, 28, 0))
    a.emit(A.eor_reg(27, 27, 8))
    a.emit(A.mul(27, 27, 9))
    a.emit(A.add_imm64(28, 28, 1))
    a.emit(A.add_imm(10, 10, 1))
    a.emit(A.cmp_imm(8, 0xFF))
    a.bcond('battle_text_hash_done', A.EQ)
    a.emit(A.cmp_imm(10, 0x400))
    a.bcond('battle_text_hash_loop', A.LT)
    a.b('battle_text_clear')

    a.label('battle_text_hash_done')
    _emit_battle_key_prefix(a, 24)
    a.emit(A.cmp_imm(25, 0x100))
    a.bcond('battle_text_dialogue_key', A.GE)
    a.emit(A.movz(8, 0x6F62))             # little-endian "bo"
    a.emit(A.strh(8, 1, 0))
    _hex_nibble(a, 26, 4, 2)
    _hex_nibble(a, 26, 0, 3)
    for index, shift in enumerate(range(28, -1, -4)):
        _hex_nibble(a, 27, shift, 4 + index)
    a.emit(A.strb(A.WZR, 1, 12))
    a.b('battle_text_publish')

    a.label('battle_text_dialogue_key')
    a.emit(A.movz(8, 0x6462))             # little-endian "bd"
    a.emit(A.strh(8, 1, 0))
    for index, shift in enumerate(range(28, -1, -4)):
        _hex_nibble(a, 27, shift, 2 + index)
    a.emit(A.strb(A.WZR, 1, 10))

    a.label('battle_text_publish')
    a.emit(A.str_(A.WZR, 24, VOICE_VARIANT_FALLBACK_OFF))
    a.emit(A.ldr(8, 24, BATTLE_TEXT_LAST_OFF))
    a.emit(A.str_(8, 24, VOICE_PENDING_OWNER_OFF))
    a.emit(A.str_(A.WZR, 24, VOICE_PENDING_AUTO_OFF))
    a.emit(A.movz(8, VOICE_CMD_PLAY))
    a.emit(A.str_(8, 24, VOICE_PENDING_CMD_OFF))
    a.b('battle_text_resume')

    a.label('battle_text_clear')
    a.emit(A.str_(A.WZR, 24, BATTLE_TEXT_LAST_OFF))
    a.label('battle_text_resume')
    _bss_ptr(a, 16, scratch)
    for index, reg in enumerate(range(24, 29)):
        a.emit(A.ldr64(reg, 16, BATTLE_TEXT_SAVE_OFF + index * 8))
    a.emit(A.ldr64(30, 16, BATTLE_TEXT_LR_OFF))
    a.emit(BATTLE_TEXT_ORIG)
    a.emit(A.b(a.pc(), BATTLE_TEXT_RESUME))
    return a.resolve()


def _check_voice_gain(voice_gain):
    """Refuse a gain before it can assemble as something else.

    Only 256 values are `FMOV Sd, #imm` immediates. An unencodable one must
    not be rounded here -- the caller that wants rounding asks `a64` for the
    nearest and says so.
    """
    if not 0.0 < voice_gain <= VOICE_GAIN_MAX:
        raise ValueError('voice_gain must be above 0 and at most %g, not %r'
                         % (VOICE_GAIN_MAX, voice_gain))
    A.fmov_s_imm(0, voice_gain)
    return voice_gain


def _build_field_clocked_voice_service(cave, addr, scratch,
                                       retain_completed=False,
                                       voice_gain=VOICE_GAIN_DEFAULT,
                                       duck_percent=25,
                                       duck_attack_frames=12,
                                       duck_release_frames=45,
                                       auto_advance=False,
                                       world_mode=False,
                                       battle_mode=False,
                                       resume_target=None,
                                       allow_foreign_replace=False,
                                       defer_foreign_play=True,
                                       replay_guard=False,
                                       stream_once=True):
    """Consume MESSAGE commands through the v49-proven voice lifecycle.

    The MESSAGE cave only writes a compact filename and command.  This native
    field callback owns construction, real-time stopping and reclamation.  A
    playback ends through the nonblocking half of OGG_STOP, is joined only
    after the worker reaches state 1, and is then deleted by its own native
    destructor before the next queued source is constructed.
    """
    _check_voice_gain(voice_gain)
    # Build 449's lesson, enforced here rather than trusted to every caller:
    # the duplicate is a FIELD dialogue defect, and the world and battle
    # services are built from this same function. Asking for the guard in
    # either of those is refused silently, so no future call site can repeat
    # the change that took world_service to 689 words and silenced the game.
    if replay_guard not in (False, 'window', 'always'):
        raise ValueError('replay_guard must be False, "window" or "always"')
    if world_mode or battle_mode:
        replay_guard = False
    a = Asm(cave, addr)
    a.emit(A.stp64_pre(29, 30, 31, -0x30))
    a.emit(_stp_off(19, 20, 0x10))
    a.emit(_stp_off(21, 22, 0x20))
    # Match the constructor's 0x160-byte path-format scratch area.  The
    # field callback is a native AAPCS64 boundary, so SP remains aligned.
    a.emit(A.sub_imm64(A.SP, A.SP, OGG_PATH_STACK_BYTES))
    _bss_ptr(a, 19, scratch)
    # 100 means the entire duck subsystem is absent, not merely that new
    # players never start an attack. Keep ramp/release code out as well so
    # a disabled mix cannot dereference MusicManager on any service path.
    if duck_percent < 100:
        _emit_bgm_release_step(a, 19, 'release_step')
    if replay_guard == 'window':
        # One tick of the retirement guard. It runs before anything else so a
        # frame in which nothing happens still expires it.
        a.emit(A.ldr(8, 19, VOICE_REPLAY_GUARD_OFF))
        a.cbz(8, 'replay_guard_idle')
        a.emit(A.sub_imm(8, 8, 1))
        a.emit(A.str_(8, 19, VOICE_REPLAY_GUARD_OFF))
        a.label('replay_guard_idle')
    a.emit(A.ldr64(20, 19, VOICE_PLAYER_OFF))
    a.cbz64(20, 'idle')
    a.emit(A.ldr(8, 20, 0x98))
    a.emit(A.cmp_imm(8, 1))
    a.bcond('completed', A.EQ)
    a.emit(A.cmp_imm(8, 8))
    a.bcond('stopping', A.EQ)

    # A pending play supersedes the live line; a pending close stops it and
    # discards the command.  Both use the same asynchronous state transition.
    a.emit(A.ldr(22, 19, VOICE_PENDING_CMD_OFF))
    a.emit(A.cmp_imm(22, VOICE_CMD_STOP))
    a.bcond('stop_discard', A.EQ)
    a.emit(A.cmp_imm(22, VOICE_CMD_PLAY))
    a.bcond('play_pending', A.EQ)
    _emit_deadline_stop(a, 20, 19, 'out', VOICE_DEADLINE_OFF,
                        voice_gain=voice_gain)
    a.b('out')

    a.label('play_pending')
    # FFNx owns one voice slot per dialogue window.  The Switch port has one
    # proven NativeOggPlayer slot, so a simultaneous second window must not
    # cancel the already-audible first one.  This matters in cargoin: Wedge's
    # window has 10.ogg while Biggs's simultaneous window intentionally has
    # no 12.ogg.  A same-window page/line still replaces immediately.
    # A LINE THAT OUTLIVES ITS BOX MUST NOT DELAY THE NEXT SPEAKER.
    # ==============================================================
    # `allow_foreign_replace` decides what a line opened on a DIFFERENT
    # window does to one that is already audible. Holding it sounds right in
    # the abstract and is wrong in practice, because Echo-S's clips routinely
    # outlast their window and scenes mix window ids freely. fship_25 is the
    # measured case -- the Highwind conversation runs on window 1 and drops
    # to window 0 for a single line:
    #
    #     +0x0E4C  win=1 dlg=107
    #     +0x0E67  win=0 dlg=108    Tifa, "tell me it'll be all right?"
    #     +0x0ECD  win=1 dlg=115
    #
    # With the hold, Barret's still-running window-1 clip kept the slot and
    # Tifa's line waited behind it: you read Tifa and heard Barret, and the
    # backlog only drained at the end of the conversation.
    if allow_foreign_replace:
        a.b('stop_keep')
    else:
        a.emit(A.ldr(8, 19, VOICE_PENDING_OWNER_OFF))
        a.emit(A.ldr(9, 19, VOICE_OWNER_OFF))
        a.emit(A.cmp_reg(8, 9))
        a.bcond('defer_foreign_play', A.NE)
        a.b('stop_keep')

    a.label('defer_foreign_play')
    # HOLD IT, DO NOT THROW IT AWAY.
    # ==============================
    # This used to clear the pending command, which protected the audible
    # line and silently discarded the second window's. Measured on the
    # composed flevel that is not a rare case: 342 of 544 fields have voiced
    # lines on more than one window, and it is why Wedge's md8_1 line (window
    # 3, behind Barret's window 1) and parts of the first Aerith scene never
    # played.
    #
    # Leaving the command pending costs nothing and needs no new state. The
    # request is already complete -- key in VOICE_KEY, owner in PENDING_OWNER
    # -- and `completed` below already looks for exactly this: "a queued,
    # existing line will be constructed in `commands` below while the same
    # duck remains in effect." So the second window's line plays a beat after
    # the first instead of never.
    #
    # It cannot resurrect the cargoin problem either. Biggs's silent window
    # queues, Wedge finishes uninterrupted, and the queued key then fails its
    # preflight at `create` and is a no-op -- which is the same outcome as
    # dropping it, reached without cancelling anything.
    #
    # A later MESSAGE overwriting VOICE_KEY simply means the newest line
    # wins, which is the right resolution; a window close publishes STOP and
    # supersedes it, which is also right.
    #
    # The deadline stop is repeated here on purpose. It is the safety net for
    # a worker that never reports completion, and skipping it while a request
    # is held would let one stuck player suppress every later line.
    #
    # `defer_foreign_play=False` restores the historical discard, so which of
    # the two is responsible for any change on hardware is one rebuild.
    if defer_foreign_play:
        _emit_deadline_stop(a, 20, 19, 'out', VOICE_DEADLINE_OFF,
                        voice_gain=voice_gain)
    else:
        a.emit(A.str_(A.WZR, 19, VOICE_PENDING_CMD_OFF))
    a.b('out')

    a.label('stop_keep')
    # NativeOggPlayer's decoder open/probe is stable before construction, but
    # its temporary decoder path is not re-entrant beside a live worker.  Do
    # not preflight a replacement here: retire the old player using the
    # v49-proven lifecycle, then preflight the queued name at ``create``.
    # ASK is an observer-only transition until choice clips have their own
    # safe filename/option hook, so an unstaged ASK cannot reach this path.
    a.emit(0x1E2703E0)                  # fmov s0, wzr
    a.emit(_mov64(0, 20))
    a.emit(A.bl(a.pc(), OGG_VOLUME))
    _emit_native_stop_async(a, 20)
    a.b('out')

    a.label('stop_discard')
    a.emit(0x1E2703E0)                  # fmov s0, wzr
    a.emit(_mov64(0, 20))
    a.emit(A.bl(a.pc(), OGG_VOLUME))
    _emit_native_stop_async(a, 20)
    if duck_percent < 100:
        _emit_bgm_release(a, 19, duck_release_frames, 'stop_discard')
    # Keep STOP published until the worker reaches state 1. Completed then
    # knows this was a manual/window close and must not synthesize another OK.
    a.b('out')

    a.label('stopping')
    a.b('out')

    a.label('completed')
    _emit_native_task_join(a, 20)
    if replay_guard == 'window':
        # ARM THE GUARD AT RETIREMENT, FROM BSS -- NOT FROM THE PLAYER.
        # ============================================================
        # Build 449 compared the pending request against the retiring player's
        # own filename, which lives in the tail of its allocation. That object
        # is about to be freed a few instructions below, and the comparison
        # only ever worked while it was alive. The hash was taken at `create`
        # instead, so this is a BSS-to-BSS copy that outlives the player and
        # is unaffected by when the destructor runs.
        a.emit(A.movz(8, VOICE_REPLAY_GUARD_FRAMES))
        a.emit(A.str_(8, 19, VOICE_REPLAY_GUARD_OFF))
    # The 449 guard that lived here -- comparing the pending key against the
    # retiring player's own filename -- is GONE, and its removal is the point.
    # It only ran when a player existed, so it could not see the replay that
    # actually happens: dismissal DELETES the player, and the stale request is
    # then constructed from `idle`. The replay debounce at the top of this
    # callback covers this drain as well, because every construction goes
    # through `create`.
    if not retain_completed:
        a.emit(_mov64(0, 20))
        a.emit(A.bl(a.pc(), OGG_DELETE))
        a.emit(A.str64(A.XZR, 19, VOICE_PLAYER_OFF))
    # Do not release between pages or contiguous MESSAGE/ASK windows.  A
    # queued, existing line will be constructed in ``commands`` below while
    # the same duck remains in effect.
    a.emit(A.ldr(22, 19, VOICE_PENDING_CMD_OFF))
    a.emit(A.cmp_imm(22, VOICE_CMD_PLAY))
    if auto_advance:
        a.bcond('completed_play', A.EQ)
        a.emit(A.cmp_imm(22, VOICE_CMD_STOP))
        a.bcond('completed_stop', A.EQ)
        a.emit(A.ldr(8, 19, VOICE_ACTIVE_AUTO_OFF))
        a.cbz64(8, 'completed_no_auto')
        # No MESSAGE transition stopped/replaced this player: the worker
        # reached its natural end. FFNx responds by simulating one OK press.
        # sub_6499F7 has already computed its edge-triggered input tables in
        # this frame, so publish a one-shot for the post-refresh input cave on
        # the next pass instead of modifying its already-consumed return mask.
        a.emit(A.ldr(8, 19, VOICE_WAITING_WINDOW_OFF))
        a.emit(A.cmp_imm(8, 1))
        a.bcond('out', A.EQ)
        a.emit(A.movz(8, 1))
        a.emit(A.str_(8, 19, VOICE_WAITING_WINDOW_OFF))
        if world_mode:
            _emit_world_auto_ok(a)
        else:
            a.emit(A.str_(8, 19, VOICE_AUTO_OK_PENDING_OFF))
        # Keep the duck at its target until MESSAGE reports that this owning
        # window closed (or queues the next voiced page).
        a.b('out')
        a.label('completed_no_auto')
        # A starting ASK first attempts its ordinary prompt filename. If that
        # real prompt played, chain the captured initial option now; if the
        # prompt was absent, create's preflight fallback already consumed the
        # same key. Neither path may synthesize OK/select an answer.
        a.emit(A.ldr(8, 19, VOICE_INITIAL_OPTION_OFF))
        a.cbz64(8, 'completed_ask_wait')
        _emit_bss_key_copy(a, 19, VOICE_INITIAL_KEY_OFF, VOICE_KEY_OFF)
        a.emit(A.str_(A.WZR, 19, VOICE_INITIAL_OPTION_OFF))
        a.emit(A.ldr(8, 19, VOICE_OWNER_OFF))
        a.emit(A.str_(8, 19, VOICE_PENDING_OWNER_OFF))
        a.emit(A.str_(A.WZR, 19, VOICE_PENDING_AUTO_OFF))
        a.emit(A.movz(8, VOICE_CMD_PLAY))
        a.emit(A.str_(8, 19, VOICE_PENDING_CMD_OFF))
        a.emit(A.str_(A.WZR, 19, VOICE_WAITING_WINDOW_OFF))
        a.emit(A.str_(A.WZR, 19, VOICE_AUTO_OK_PENDING_OFF))
        a.b('commands')
        a.label('completed_ask_wait')
        # Completed option audio keeps ownership and ducking until the choice
        # closes or another highlighted option publishes a replacement.
        a.emit(A.movz(8, 1))
        a.emit(A.str_(8, 19, VOICE_WAITING_WINDOW_OFF))
        a.emit(A.str_(A.WZR, 19, VOICE_AUTO_OK_PENDING_OFF))
        a.b('out')
        a.label('completed_play')
        a.emit(A.str_(A.WZR, 19, VOICE_WAITING_WINDOW_OFF))
        a.emit(A.str_(A.WZR, 19, VOICE_AUTO_OK_PENDING_OFF))
        a.b('commands')
        a.label('completed_stop')
        a.emit(A.str_(A.WZR, 19, VOICE_WAITING_WINDOW_OFF))
        a.emit(A.str_(A.WZR, 19, VOICE_AUTO_OK_PENDING_OFF))
        a.emit(A.str_(A.WZR, 19, VOICE_OWNER_OFF))
        if duck_percent < 100:
            _emit_bgm_release(a, 19, duck_release_frames, 'completed_stop')
        a.b('commands')
    else:
        a.bcond('commands', A.EQ)
        a.emit(A.str_(A.WZR, 19, VOICE_OWNER_OFF))
        if duck_percent < 100:
            _emit_bgm_release(a, 19, duck_release_frames, 'completed')
        a.b('commands')

    a.label('idle')
    a.emit(A.ldr(22, 19, VOICE_PENDING_CMD_OFF))
    a.emit(A.cmp_imm(22, VOICE_CMD_PLAY))
    a.bcond('commands', A.EQ)
    if auto_advance:
        a.emit(A.cmp_imm(22, VOICE_CMD_STOP))
        a.bcond('idle_release', A.EQ)
        a.emit(A.ldr(8, 19, VOICE_WAITING_WINDOW_OFF))
        a.emit(A.cmp_imm(8, 1))
        a.bcond('out', A.EQ)
        a.b('idle_release')
        a.label('idle_release')
        a.emit(A.str_(A.WZR, 19, VOICE_WAITING_WINDOW_OFF))
        a.emit(A.str_(A.WZR, 19, VOICE_AUTO_OK_PENDING_OFF))
        a.emit(A.str_(A.WZR, 19, VOICE_OWNER_OFF))
    if duck_percent < 100:
        _emit_bgm_release(a, 19, duck_release_frames, 'idle')
    a.b('commands')

    a.label('commands')
    a.emit(A.ldr(22, 19, VOICE_PENDING_CMD_OFF))
    a.emit(A.cmp_imm(22, VOICE_CMD_PLAY))
    a.bcond('create', A.EQ)
    a.emit(A.cmp_imm(22, VOICE_CMD_STOP))
    a.bcond('clear_command', A.EQ)
    a.b('out')

    a.label('create')
    if auto_advance:
        a.emit(A.str_(A.WZR, 19, VOICE_WAITING_WINDOW_OFF))
        a.emit(A.str_(A.WZR, 19, VOICE_AUTO_OK_PENDING_OFF))
        a.emit(A.str_(A.WZR, 19, VOICE_OWNER_OFF))
    a.label('create_preflight')
    if replay_guard:
        # THE LINE THAT JUST FINISHED IS NOT REBUILT.
        # ===========================================
        # Every construction in this runtime passes through here, which is why
        # the guard is here and not on the publish side: it does not matter
        # whether the duplicate arrived as a deferred foreign request drained
        # by `completed`, as a late publish picked up from `idle`, or as a key
        # left in the mailbox by a refused publish. All three end at `create`
        # asking for the clip that was playing a moment ago, and all three are
        # refused by one comparison.
        #
        # The hash is taken on this side of the preflight so the ASK option
        # fallbacks, which rewrite the key and branch back here, are measured
        # as the clip they end up playing. W21 is one of the two registers
        # this cave saves for itself and nothing else in the body uses it, so
        # it carries the value through malloc and the constructor to the
        # single store at `create_keep_variant_fallback` -- one hash, not two.
        #
        # It is a debounce and worth saying so plainly: it is armed for
        # VOICE_REPLAY_GUARD_FRAMES service ticks after a retirement and says
        # nothing about anything older. What it gives up is a script that
        # deliberately plays the SAME line twice inside half a second with
        # nothing in between. convil_2 repeats dialog 49 nine times and every
        # pair has another line between them, so it is unaffected -- an
        # intervening line retires with its own hash and disarms this one.
        _emit_key_hash(a, 19, 21, 'create_guard')
        if replay_guard == 'window':
            a.emit(A.ldr(8, 19, VOICE_REPLAY_GUARD_OFF))
            a.cbz(8, 'create_unguarded')
        a.emit(A.ldr(8, 19, VOICE_ACTIVE_KEY_HASH_OFF))
        a.emit(A.cmp_reg(8, 21))
        a.bcond('clear_command', A.EQ)
        if replay_guard == 'window':
            a.label('create_unguarded')
    _emit_ogg_preflight(a, 19, 'initial_option_fallback')
    a.emit(A.movz(0, PLAYER_ALLOC_BYTES))
    a.emit(A.bl(a.pc(), MALLOC))
    a.cbz64(0, 'clear_command')
    a.emit(_mov64(20, 0))
    _emit_player_name_copy(a, 20, 19)
    a.emit(_mov64(0, 20))
    a.emit(A.bl(a.pc(), OGG_CTOR))
    a.emit(A.ldr64(8, 20, 0x60))
    a.cbz64(8, 'delete_failed')
    a.emit(A.str64(20, 19, VOICE_PLAYER_OFF))
    a.emit(A.ldr(8, 19, VOICE_PENDING_OWNER_OFF))
    a.emit(A.str_(8, 19, VOICE_OWNER_OFF))
    a.emit(A.ldr(8, 19, VOICE_PENDING_AUTO_OFF))
    a.emit(A.str_(8, 19, VOICE_ACTIVE_AUTO_OFF))
    # Defensive reset for the next field.  A MAPJUMP latch is normally
    # consumed by the retiring player's worker; it must never be inherited by
    # a later player if a transition occurred with no worker refill pending.
    a.emit(A.str_(A.WZR, 19, VOICE_MAPJUMP_OFF))
    a.emit(_mov64(0, 20))
    a.emit(A.movz(1, 0))
    a.emit(A.bl(a.pc(), OGG_PLAY))
    # A successful prompt must retain the pending option's base fallback;
    # every other successful create has consumed the routed key completely.
    a.emit(A.ldr(8, 19, VOICE_INITIAL_OPTION_OFF))
    a.emit(A.cmp_imm(8, 0))
    a.bcond('create_keep_variant_fallback', A.NE)
    a.emit(A.str_(A.WZR, 19, VOICE_VARIANT_FALLBACK_OFF))
    a.label('create_keep_variant_fallback')
    if replay_guard:
        # The hash computed at `create_preflight`, stored only now that this
        # key really is the one a player was built from.
        a.emit(A.str_(21, 19, VOICE_ACTIVE_KEY_HASH_OFF))
    _emit_host_deadline(a, 20, 19, VOICE_DEADLINE_OFF)
    a.emit(A.fmov_s_imm(0, voice_gain))
    a.emit(_mov64(0, 20))
    a.emit(A.bl(a.pc(), OGG_VOLUME))
    if duck_percent < 100:
        _emit_bgm_duck(a, 19, duck_percent, duck_attack_frames,
                       'create_duck')
    a.b('clear_command')

    a.label('initial_option_fallback')
    a.emit(A.ldr(8, 19, VOICE_INITIAL_OPTION_OFF))
    a.cbz64(8, 'variant_option_fallback')
    _emit_bss_key_copy(a, 19, VOICE_INITIAL_KEY_OFF, VOICE_KEY_OFF)
    a.emit(A.str_(A.WZR, 19, VOICE_INITIAL_OPTION_OFF))
    a.b('create_preflight')

    a.label('variant_option_fallback')
    a.emit(A.ldr(8, 19, VOICE_VARIANT_FALLBACK_OFF))
    a.cbz64(8, 'clear_command')
    _emit_bss_key_copy(a, 19, VOICE_VARIANT_FALLBACK_KEY_OFF, VOICE_KEY_OFF)
    a.emit(A.str_(A.WZR, 19, VOICE_VARIANT_FALLBACK_OFF))
    a.b('create_preflight')

    a.label('delete_failed')
    a.emit(_mov64(0, 20))
    a.emit(A.bl(a.pc(), OGG_DELETE))
    a.label('clear_command')
    a.emit(A.str_(A.WZR, 19, VOICE_PENDING_CMD_OFF))

    a.label('out')
    a.emit(A.add_imm64(A.SP, A.SP, OGG_PATH_STACK_BYTES))
    a.emit(_ldp_off(21, 22, 0x20))
    a.emit(_ldp_off(19, 20, 0x10))
    a.emit(A.ldp64_post(29, 30, 31, 0x30))
    if resume_target is not None:
        a.emit(A.b(a.pc(), resume_target))
    elif world_mode:
        # WORLD_SERVICE_ORIG is PC-relative and must be re-encoded here.
        a.emit(A.adrp(24, a.pc(), 0x12CE000))
        a.emit(A.b(a.pc(), WORLD_SERVICE_HOOK + 4))
    elif battle_mode:
        a.emit(A.adrp(28, a.pc(), 0x12CE000))
        a.emit(A.b(a.pc(), BATTLE_HOOK + 4))
    else:
        a.emit(FIELD_ORIG)
        a.emit(A.b(a.pc(), FIELD_HOOK + 4))
    return a.resolve()


def _replace_music_thread_name(raw, name):
    """Replace only MusicStream's diagnostic title format in ``raw``.

    The native constructor separately formats the Ogg path, so the full
    requested name still reaches ``data/music_ogg/<name>.ogg``.  This removes
    the short Horizon thread-name limit without changing decoder input or the
    player lifecycle.
    """
    if not isinstance(name, str) or not name:
        raise ValueError('music thread name must be a non-empty ASCII string')
    try:
        replacement = name.encode('ascii') + b'\0'
    except UnicodeEncodeError as exc:
        raise ValueError('music thread name must be ASCII') from exc
    if (b'%' in replacement or
            len(replacement) > len(MUSIC_THREAD_NAME_FORMAT_STOCK)):
        raise ValueError('music thread name must fit the stock literal and contain no %')
    rodata = bytearray(raw[1])
    offset = MUSIC_THREAD_NAME_FORMAT - RODATA_START
    width = len(MUSIC_THREAD_NAME_FORMAT_STOCK)
    current = bytes(rodata[offset:offset + width])
    want = replacement.ljust(width, b'\0')
    if current == want:
        # ALREADY DONE, AND BY SOMEBODY ELSE. `ff7nx_ambient` makes exactly
        # this replacement for exactly this reason -- an over-long Horizon
        # thread title aborts in SetThreadNamePointer, and both features
        # construct the same NativeOggPlayer, so whichever installs first has
        # already fixed it for the other. The literal is verified to be
        # byte-for-byte what this call would have written, so this is
        # idempotence and not a silently accepted conflict: anything else
        # still raises.
        return False
    if current != MUSIC_THREAD_NAME_FORMAT_STOCK:
        raise ValueError('MusicStream thread format is neither the stock '
                         "literal nor %r -- somebody else owns it" % want)
    rodata[offset:offset + width] = want
    raw[1] = bytes(rodata)
    return True



# ---------------------------------------------------------------------------
# DIAGNOSTIC STUBS FOR THE MESSAGE SITE.
#
# The producer cave executes correctly under emulation -- real bytes, real
# scattered addresses, adversarial guest state, paged translation -- and still
# crashes the console. That leaves two possibilities the body itself cannot
# distinguish: something in the body that emulation does not model, or the
# HOOK at this site being unsafe in this composed module regardless of what
# the cave contains.
#
# These two stubs answer that. `passthrough` puts a cave at the site that does
# nothing at all except return control the way the displaced instruction would
# have; `saveonly` adds the BSS save/restore pair and nothing else. If
# passthrough crashes, the cave body is irrelevant and the site or the
# placement is the problem.
# ---------------------------------------------------------------------------

def _build_message_stub_cave(cave, addr, scratch, save=False, reads=False,
                             tables=False, transitions=False,
                             footprint_words=None,
                             resume_instruction=None, resume_address=None,
                             resume_call_target=None):
    """A MESSAGE cave that does nothing but hand control back."""
    a = Asm(cave, addr)
    if footprint_words is not None:
        # Allocation depends only on the logical instruction count.  Fill the
        # producer's exact footprint with architectural NOPs, then use the
        # same two-word resume tail.  This executes through every scattered
        # padding run the real producer would receive, without touching BSS,
        # guest memory, flags, or registers.  A smaller passthrough stub cannot
        # test holes that are allocated only after its own final run.
        if save or reads or tables or transitions:
            raise ValueError('footprint MESSAGE stub cannot combine with '
                             'another diagnostic body')
        if footprint_words < 2:
            raise ValueError('MESSAGE footprint must include its resume tail')
        for _ in range(footprint_words - 2):
            a.emit(A.nop())
    if save or reads or tables or transitions:
        _bss_ptr(a, 16, scratch)
        for n, off in ((24, 0x00), (25, 0x08), (26, 0x10),
                       (27, 0x18), (28, 0x20), (30, 0x28)):
            a.emit(A.str64(n, 16, VOICE_MESSAGE_SAVE_OFF + off))
    if reads:
        # Exactly the producer's guest reads, results discarded. The game's
        # own MESSAGE handler performs five of these before our hook, so they
        # are expected to be safe -- but "expected" is what the last four
        # theories were, and this measures it instead.
        a.emit(_mov64(24, 16))
        def read(guest_lo, guest_hi):
            a.emit(A.movz(0, guest_lo))
            a.emit(A.movk_hi(0, guest_hi))
            a.emit(A.bl(a.pc(), RESOLVE_PAGED))
        read(CURRENT_WINDOW & 0xFFFF, CURRENT_WINDOW >> 16)
        a.emit(A.ldrb(26, 0, 0))
        a.emit(A.movz(0, SCRIPT_CURSOR & 0xFFFF))
        a.emit(A.movk_hi(0, SCRIPT_CURSOR >> 16))
        a.emit(A.lsl(8, 26, 1))
        a.emit(A.add_reg(0, 0, 8))
        a.emit(A.bl(a.pc(), RESOLVE_PAGED))
        a.emit(A.ldrh(27, 0, 0))
        read(FIELD_SCRIPT_PTR & 0xFFFF, FIELD_SCRIPT_PTR >> 16)
        a.emit(A.ldr(28, 0, 0))
        a.emit(A.add_reg(0, 28, 27))
        a.emit(A.add_imm(0, 0, 1))
        a.emit(A.bl(a.pc(), RESOLVE_PAGED))
        a.emit(A.ldrb(26, 0, 0))
        a.emit(A.add_reg(0, 28, 27))
        a.emit(A.add_imm(0, 0, 2))
        a.emit(A.bl(a.pc(), RESOLVE_PAGED))
        a.emit(A.ldrb(28, 0, 0))
        # MESSAGE_STATE + window_id * 48 -- the one read the game has NOT
        # already done at this point.
        a.emit(A.lsl(8, 26, 4))
        a.emit(A.lsl(9, 26, 5))
        a.emit(A.add_reg(8, 8, 9))
        a.emit(A.movz(0, MESSAGE_STATE & 0xFFFF))
        a.emit(A.movk_hi(0, MESSAGE_STATE >> 16))
        a.emit(A.add_reg(0, 0, 8))
        a.emit(A.bl(a.pc(), RESOLVE_PAGED))
        a.emit(A.ldrh(27, 0, 0))
        read(FIELD_FILE_NAME & 0xFFFF, FIELD_FILE_NAME >> 16)
        a.emit(A.ldrb(8, 0, 0))
    if tables or transitions:
        # The per-window bookkeeping pair, exactly as the producer addresses
        # it. Two things here are worth measuring rather than assuming: the
        # last-opcode entry is at block + 0x40 + window_id*3, so for two out
        # of every three window ids it is an UNALIGNED 16-bit access; and the
        # table is 0x200 bytes for 256 entries of stride 3, so a window id at
        # or above 171 writes into the page table that follows it.
        #
        # w26 holds the window id and w27 the message state from the reads.
        a.emit(A.add_imm64(17, 24, VOICE_LAST_OPCODE_OFF))
        a.emit(_add_x_uxtw(17, 17, 26))
        a.emit(A.lsl(9, 26, 1))
        a.emit(A.add_reg64(17, 17, 9))
        a.emit(A.ldrh(8, 17, 0))
        a.emit(A.add_imm64(16, 24, VOICE_PAGE_OFF))
        a.emit(_add_x_uxtw(16, 16, 26))
        a.emit(A.ldrb(10, 16, 0))
        if transitions:
            # Retain the production transition tree and its page mutation,
            # but stop immediately before every ownership/key/command write.
            # The preceding diagnostics have independently cleared all guest
            # reads, table accesses and every physical padding run.  This one
            # therefore isolates internal control flow and state mutation
            # from publication of a PLAY/STOP request.
            a.emit(A.cmp_imm(27, 0))
            a.bcond('stub_opening', A.EQ)
            a.emit(A.cmp_imm(8, 0))
            a.bcond('stub_starting', A.EQ)
            a.emit(A.cmp_imm(8, 14))
            a.bcond('stub_page_four', A.NE)
            a.emit(A.cmp_imm(27, 2))
            a.bcond('stub_paging', A.EQ)
            a.label('stub_page_four')
            a.emit(A.cmp_imm(8, 4))
            a.bcond('stub_closing_check', A.NE)
            a.emit(A.cmp_imm(27, 8))
            a.bcond('stub_paging', A.EQ)
            a.label('stub_closing_check')
            a.emit(A.cmp_imm(27, 7))
            a.bcond('stub_update', A.NE)
            a.emit(A.cmp_reg(8, 27))
            a.bcond('stub_update', A.EQ)
            a.b('stub_update')

            a.label('stub_opening')
            a.emit(A.strb(A.WZR, 16, 0))
            a.b('stub_update')
            a.label('stub_starting')
            a.emit(A.strb(A.WZR, 16, 0))
            a.emit(A.movz(10, 0))
            a.b('stub_update')
            a.label('stub_paging')
            a.emit(A.add_imm(10, 10, 1))
            a.emit(A.strb(10, 16, 0))
            a.label('stub_update')
            a.emit(A.strh(27, 17, 0))
        else:
            a.emit(A.strb(10, 16, 0))
            a.emit(A.strh(27, 17, 0))
    if reads or tables or transitions:
        _bss_ptr(a, 16, scratch)
    if save or reads or tables or transitions:
        for n, off in ((24, 0x00), (25, 0x08), (26, 0x10),
                       (27, 0x18), (28, 0x20), (30, 0x28)):
            a.emit(A.ldr64(n, 16, VOICE_MESSAGE_SAVE_OFF + off))
    if resume_call_target is not None:
        a.emit(A.bl(a.pc(), resume_call_target))
    elif resume_instruction is not None:
        a.emit(resume_instruction)
    a.emit(A.b(a.pc(), resume_address))
    return a.resolve()

def patch_main_clocked_dialogue(src, dest, forced_name=None,
                                defer_foreign_play=True,
                                allow_foreign_field_replace=False,
                                stop_on_close=True,
                                publish_on_dialog_change=True,
                                first_in_burst_wins=True,
                                replay_guard=False,
                                stream_once=True,
                                forced_alt_name=None,
                                retain_completed=False,
                                build_key_then_forced=False,
                                forced_suffix=None,
                                field_name_prefix_bytes=6,
                                music_thread_name=None,
                                key_mode='hash',
                                folder_forced_suffix=None,
                                voice_gain=VOICE_GAIN_DEFAULT,
                                duck_percent=25,
                                duck_attack_frames=12,
                                duck_release_frames=45,
                                message_phase='pre', hook_ask=True,
                                hook_message=True, hook_field=True,
                                message_stub=None,
                                hook_world=False,
                                disable_name_change=True,
                                auto_advance=False,
                                battle_probability=0,
                                battle_text=False):
    """Install FFNx-ordered field VO and Echo-S name-menu compatibility.

    ``forced_name`` exists only for the any-NPC bridge test.  Omit it for the
    real Echo-S field key generated from FFNx's MESSAGE transition data.
    """
    if not isinstance(duck_percent, int) or not 0 <= duck_percent <= 100:
        raise ValueError('duck_percent must be an integer from 0 through 100')
    _check_voice_gain(voice_gain)
    if (not isinstance(duck_attack_frames, int) or
            not 1 <= duck_attack_frames <= 600):
        raise ValueError('duck_attack_frames must be an integer from 1 through 600')
    if (not isinstance(duck_release_frames, int) or
            not 1 <= duck_release_frames <= 600):
        raise ValueError('duck_release_frames must be an integer from 1 through 600')
    if message_phase not in ('pre', 'post'):
        raise ValueError("message_phase must be 'pre' or 'post'")
    if battle_probability not in (0, 25, 50, 75, 100):
        raise ValueError('battle_probability must be 0/25/50/75/100')
    if auto_advance and retain_completed:
        raise ValueError('auto_advance requires completed players to be reclaimed')
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw = _segments(blob)
    if music_thread_name is not None:
        _replace_music_thread_name(raw, music_thread_name)
    text = bytearray(raw[0])
    message_hook = MESSAGE_PRE_HOOK if message_phase == 'pre' else MESSAGE_HOOK
    message_orig = MESSAGE_PRE_ORIG if message_phase == 'pre' else MESSAGE_ORIG
    message_resume = MESSAGE_HOOK if message_phase == 'pre' else MESSAGE_HOOK + 4
    message_call_target = MESSAGE_PRE_TARGET if message_phase == 'pre' else None
    message_word, = struct.unpack_from('<I', text, message_hook)
    ask_word, = struct.unpack_from('<I', text, ASK_PRE_HOOK)
    name_word, = struct.unpack_from('<I', text, NAME_CHANGE_HOOK)
    name_gate_word, = struct.unpack_from('<I', text, NAME_CHANGE_STATE_GATE)
    field_word, = struct.unpack_from('<I', text, FIELD_HOOK)
    field_chain = (_direct_branch_target(FIELD_HOOK, field_word)
                   if field_word != FIELD_ORIG else None)
    worker_loop_word, = struct.unpack_from('<I', text, WORKER_LOOP_HOOK)
    if worker_loop_word != WORKER_LOOP_ORIG:
        raise ValueError('voice worker loop site +0x%X is %08X, expected '
                         '%08X' % (WORKER_LOOP_HOOK, worker_loop_word,
                                   WORKER_LOOP_ORIG))
    mapjump_word, = struct.unpack_from('<I', text, MAPJUMP_HOOK)
    if hook_field and mapjump_word != MAPJUMP_ORIG:
        raise ValueError('field MAPJUMP hook +0x%X is %08X, expected %08X'
                         % (MAPJUMP_HOOK, mapjump_word, MAPJUMP_ORIG))
    input_word, = struct.unpack_from('<I', text, INPUT_POST_REFRESH_HOOK)
    if hook_ask:
        ask_setup = struct.unpack_from('<II', text, ASK_PRE_HOOK - 8)
        if ask_setup != ASK_CALL_SETUP:
            raise ValueError('ASK translated cdecl setup differs at +0x%X: '
                             '%08X %08X' % (ASK_PRE_HOOK - 8, *ask_setup))
    if hook_world:
        for where, expected, _target, _context in (WORLD_MESSAGE_HOOKS +
                                                    WORLD_ASK_HOOKS):
            word, = struct.unpack_from('<I', text, where)
            if word != expected:
                raise ValueError('world dialogue hook +0x%X is %08X, expected '
                                 '%08X' % (where, word, expected))
            setup = struct.unpack_from('<II', text, where - 8)
            if setup != WORLD_CALL_SETUPS[where]:
                raise ValueError('world translated cdecl setup differs at '
                                 '+0x%X: %08X %08X' % (where - 8, *setup))
        world_service_word, = struct.unpack_from('<I', text,
                                                 WORLD_SERVICE_HOOK)
        world_service_chain = (
            _direct_branch_target(WORLD_SERVICE_HOOK, world_service_word)
            if world_service_word != WORLD_SERVICE_ORIG else None)
        if world_service_word != WORLD_SERVICE_ORIG and world_service_chain is None:
            raise ValueError('world voice service hook +0x%X is %08X, '
                             'expected %08X or an existing direct bridge'
                             % (WORLD_SERVICE_HOOK, world_service_word,
                                WORLD_SERVICE_ORIG))
    else:
        world_service_chain = None
    if message_word != message_orig:
        raise ValueError('clocked dialogue %s-MESSAGE hook +0x%X is %08X, '
                         'expected %08X' % (message_phase, message_hook,
                                             message_word, message_orig))
    if hook_ask and ask_word != ASK_PRE_ORIG:
        raise ValueError('clocked dialogue ASK hook +0x%X is %08X, expected %08X'
                         % (ASK_PRE_HOOK, ask_word, ASK_PRE_ORIG))
    if disable_name_change and name_word != NAME_CHANGE_ORIG:
        raise ValueError('name-change hook +0x%X is %08X, expected %08X'
                         % (NAME_CHANGE_HOOK, name_word, NAME_CHANGE_ORIG))
    if (disable_name_change and
            name_gate_word != NAME_CHANGE_STATE_GATE_ORIG):
        raise ValueError('name-change state gate +0x%X is %08X, expected %08X'
                         % (NAME_CHANGE_STATE_GATE, name_gate_word,
                            NAME_CHANGE_STATE_GATE_ORIG))
    if field_word != FIELD_ORIG and field_chain is None:
        raise ValueError('clocked dialogue FIELD hook +0x%X is %08X, expected '
                         '%08X or an existing direct bridge' %
                         (FIELD_HOOK, field_word, FIELD_ORIG))
    battle_chain = None
    if battle_probability or battle_text:
        battle_word, = struct.unpack_from('<I', text, BATTLE_HOOK)
        battle_chain = (_direct_branch_target(BATTLE_HOOK, battle_word)
                        if battle_word != BATTLE_ORIG else None)
        if battle_word != BATTLE_ORIG and battle_chain is None:
            raise ValueError('battle voice service hook +0x%X is %08X, '
                             'expected %08X or an existing direct bridge'
                             % (BATTLE_HOOK, battle_word, BATTLE_ORIG))
    if battle_probability:
        action_word, = struct.unpack_from('<I', text, BATTLE_ACTION_HOOK)
        if action_word != BATTLE_ACTION_ORIG:
            raise ValueError('battle action hook +0x%X is %08X, expected %08X'
                             % (BATTLE_ACTION_HOOK, action_word,
                                BATTLE_ACTION_ORIG))
    if battle_text:
        battle_text_word, = struct.unpack_from('<I', text, BATTLE_TEXT_HOOK)
        if battle_text_word != BATTLE_TEXT_ORIG:
            raise ValueError('battle text hook +0x%X is %08X, expected %08X'
                             % (BATTLE_TEXT_HOOK, battle_text_word,
                                BATTLE_TEXT_ORIG))
    if auto_advance and input_word != INPUT_POST_REFRESH_ORIG:
        raise ValueError('auto-OK input hook +0x%X is %08X, expected %08X'
                         % (INPUT_POST_REFRESH_HOOK, input_word,
                            INPUT_POST_REFRESH_ORIG))
    data_end = (segs[2][1] + segs[2][2] + 0xFFF) & ~0xFFF
    old_bss, = struct.unpack_from('<I', blob, 0x3C)
    old_bss_end = data_end + old_bss
    scratch = old_bss_end
    # ALIGN THE BLOCK. Every structure in it is addressed by an 8-byte-multiple
    # offset and reached with 64-bit LDR/STR and LDP/STP: the player pointer,
    # the saved x24..x28 sets, the host deadline. The base is wherever the
    # previous pass left BSS, and nothing upstream promises that is aligned --
    # on the stock module it happens to land on 0x3FEC328, but composed after
    # the five Cosmo Memory bridges it lands on 0x3FEF65C, which is 4 mod 8.
    # Every 64-bit access in the runtime is then unaligned, and LDP/STP in
    # particular is not guaranteed for an unaligned address.
    #
    # This is exactly the kind of difference that makes a cave proven on a
    # bare module misbehave on a composed one, so the block is aligned to 16
    # here rather than being left to chance. The padding is added to the BSS
    # growth, so the block is still entirely inside what the loader allocates.
    align_pad = (-scratch) % 16
    scratch += align_pad
    # EMPIRICAL, not derived -- see the long note by the layout above. A store
    # in the page after the one the pre-existing BSS ended in aborted on
    # hardware; the same store below that boundary did not. The cause is not
    # established, so this enforces the measurement rather than a theory, and
    # it is an error rather than a warning because the failure it prevents is
    # an abort on the first line of dialogue.
    bss_page_end = (old_bss_end + 0xFFF) & ~0xFFF
    if scratch + VOICE_SCRATCH_BYTES > bss_page_end:
        raise ValueError(
            'Echo-S BSS needs +0x%X..+0x%X but the page the pre-existing BSS '
            'ended in stops at +0x%X (%d byte(s) short). A store past that '
            'boundary aborted on hardware in build 325. Compact the layout in '
            'ff7nx_voice (it is %d bytes and has been smaller) rather than '
            'raising this ceiling -- and if you do raise it, prove it with '
            'the `footprint` diagnostic first.'
            % (scratch, scratch + VOICE_SCRATCH_BYTES - 1, bss_page_end - 1,
               scratch + VOICE_SCRATCH_BYTES - bss_page_end,
               VOICE_SCRATCH_BYTES))
    bss_growth = align_pad + VOICE_SCRATCH_BYTES
    pool = _verified_hole_pool(src, text)
    mapjump_entry, mapjump_placed = (None, {})
    transition_worker_entry, transition_worker_placed = (None, {})
    if hook_field:
        mapjump_entry, mapjump_placed = ff7nx_cave.emit_laid_out(
            pool, lambda cave, address: _build_mapjump_voice_latch(
                cave, address, scratch))
        transition_worker_entry, transition_worker_placed = \
            ff7nx_cave.emit_laid_out(
                pool, lambda cave, address: _build_mapjump_worker_stop(
                    cave, address, scratch))
    message_entry, message_placed = (None, {})
    def build_message(cave, address):
        return _build_message_command_cave(
            cave, address, scratch, forced_name=forced_name,
            forced_alt_name=forced_alt_name,
            build_key_then_forced=build_key_then_forced,
            forced_suffix=forced_suffix,
            field_name_prefix_bytes=field_name_prefix_bytes,
            key_mode=key_mode,
            folder_forced_suffix=folder_forced_suffix,
            hold_until_window_close=auto_advance,
            stop_on_close=stop_on_close,
            publish_on_dialog_change=publish_on_dialog_change,
            first_in_burst_wins=first_in_burst_wins,
            resume_instruction=message_orig,
            resume_address=message_resume,
            resume_call_target=message_call_target)

    publication_masks = {
        'closing': 0,
        'pubowner': VOICE_PUBLISH_OWNER,
        'pubauto': VOICE_PUBLISH_AUTO,
        'pubcmd': VOICE_PUBLISH_COMMAND,
        'publication': VOICE_PUBLISH_ALL,
    }

    def build_message_publication(cave, address):
        # The exact production state machine and request mailbox, deliberately
        # omitting only the key-buffer writer and its terminator. At the
        # `message` level no service consumes the request, so this remains an
        # ExeFS-only behavioral probe with no audio playback.
        return _build_message_command_cave(
            cave, address, scratch, key_mode=key_mode,
            construct_key=False,
            publication_mask=publication_masks[message_stub],
            hold_until_window_close=auto_advance,
            resume_instruction=message_orig,
            resume_address=message_resume,
            resume_call_target=message_call_target)

    if hook_message and message_stub in publication_masks:
        message_entry, message_placed = ff7nx_cave.emit_laid_out(
            pool, build_message_publication)
    elif hook_message and message_stub:
        footprint_words = None
        if message_stub == 'footprint':
            # Probe the real builder exactly as emit_laid_out does. The pool's
            # chosen runs are a pure function of this count, so an equal-size
            # NOP body receives the same entry and every same padding run.
            footprint_words = len(build_message(0, lambda i: i * 4))
        message_entry, message_placed = ff7nx_cave.emit_laid_out(
            pool, lambda cave, address: _build_message_stub_cave(
                cave, address, scratch,
                save=(message_stub == 'saveonly'),
                reads=(message_stub in ('reads', 'tables', 'transitions')),
                tables=(message_stub in ('tables', 'transitions')),
                transitions=(message_stub == 'transitions'),
                footprint_words=footprint_words,
                resume_instruction=message_orig,
                resume_address=message_resume,
                resume_call_target=message_call_target))
    elif hook_message:
        message_entry, message_placed = ff7nx_cave.emit_laid_out(
            pool, build_message)
    ask_entry = None
    ask_placed = {}
    if hook_ask:
        route_ask_choices = (key_mode == 'folder' and forced_name is None and
                             folder_forced_suffix is None)
        ask_entry, ask_placed = ff7nx_cave.emit_laid_out(
            pool, lambda cave, address: _build_message_command_cave(
                cave, address, scratch, forced_name=forced_name,
                forced_alt_name=forced_alt_name,
                build_key_then_forced=build_key_then_forced,
                forced_suffix=forced_suffix,
                field_name_prefix_bytes=field_name_prefix_bytes,
                key_mode=key_mode,
                folder_forced_suffix=folder_forced_suffix,
                stop_on_close=stop_on_close,
                publish_on_dialog_change=publish_on_dialog_change,
                first_in_burst_wins=first_in_burst_wins,
                window_param_offset=2, dialog_param_offset=3,
                # The live highlighted option is read from ASK's fifth guest
                # argument before the stock updater, matching FFNx's wrapper.
                publish_commands=route_ask_choices,
                choice_options=route_ask_choices,
                allow_auto_advance=False,
                hold_until_window_close=(auto_advance and route_ask_choices),
                resume_instruction=ASK_PRE_ORIG,
                resume_address=ASK_PRE_HOOK + 4,
                resume_call_target=ASK_PRE_TARGET))
    name_entry = None
    name_placed = {}
    if disable_name_change:
        name_entry, name_placed = ff7nx_cave.emit_laid_out(
            pool, lambda cave, address: _build_disable_name_change_cave(
                cave, address, scratch))
    field_entry, field_placed = (None, {})
    if hook_field:
        field_entry, field_placed = ff7nx_cave.emit_laid_out(
            pool, lambda cave, address: _build_field_clocked_voice_service(
                cave, address, scratch, retain_completed=retain_completed,
                voice_gain=voice_gain,
                duck_percent=duck_percent,
                duck_attack_frames=duck_attack_frames,
                duck_release_frames=duck_release_frames,
                auto_advance=auto_advance, resume_target=field_chain,
                allow_foreign_replace=allow_foreign_field_replace,
                defer_foreign_play=defer_foreign_play,
                # FIELD ONLY. The default is off and this is the one caller
                # that turns it on: build 449 put a duplicate guard in this
                # SHARED builder, world_service went 625 -> 689 words against
                # a 696-word window, the allocator failed and the whole
                # runtime went silent. The repeat is a field-dialogue defect.
                replay_guard=replay_guard,
                stream_once=stream_once))
    battle_action_entry = None
    battle_action_placed = {}
    battle_text_entry = None
    battle_text_placed = {}
    battle_service_entry = None
    battle_service_placed = {}
    if battle_probability:
        battle_action_entry, battle_action_placed = ff7nx_cave.emit_laid_out(
            pool, lambda cave, address: _build_battle_action_command_cave(
                cave, address, scratch, battle_probability))
    if battle_text:
        battle_text_entry, battle_text_placed = ff7nx_cave.emit_laid_out(
            pool, lambda cave, address: _build_battle_text_command_cave(
                cave, address, scratch))
    if battle_probability or battle_text:
        battle_service_entry, battle_service_placed = ff7nx_cave.emit_laid_out(
            pool, lambda cave, address: _build_field_clocked_voice_service(
                cave, address, scratch, retain_completed=retain_completed,
                voice_gain=voice_gain,
                duck_percent=duck_percent,
                duck_attack_frames=duck_attack_frames,
                duck_release_frames=duck_release_frames,
                auto_advance=False, battle_mode=True,
                resume_target=battle_chain, allow_foreign_replace=True,
                stream_once=stream_once))
    world_message_entry = None
    world_message_placed = {}
    world_ask_entry = None
    world_ask_placed = {}
    world_service_entry = None
    world_service_placed = {}
    if hook_world:
        world_message_entry, world_message_placed = ff7nx_cave.emit_laid_out(
            pool, lambda cave, address: _build_world_command_cave(
                cave, address, scratch, WORLD_MESSAGE_HOOKS[0][2],
                is_ask=False, auto_advance=auto_advance))
        world_ask_entry, world_ask_placed = ff7nx_cave.emit_laid_out(
            pool, lambda cave, address: _build_world_command_cave(
                cave, address, scratch, WORLD_ASK_HOOKS[0][2],
                is_ask=True, auto_advance=auto_advance))
        world_service_entry, world_service_placed = \
            ff7nx_cave.emit_laid_out(
                pool, lambda cave, address: _build_field_clocked_voice_service(
                    cave, address, scratch, retain_completed=retain_completed,
                    voice_gain=voice_gain,
                    duck_percent=duck_percent,
                    duck_attack_frames=duck_attack_frames,
                    duck_release_frames=duck_release_frames,
                    auto_advance=auto_advance, world_mode=True,
                    resume_target=world_service_chain,
                    allow_foreign_replace=allow_foreign_field_replace,
                    defer_foreign_play=defer_foreign_play,
                    stream_once=stream_once))
    input_entry = None
    input_placed = {}
    if auto_advance:
        input_entry, input_placed = ff7nx_cave.emit_laid_out(
            pool, lambda cave, address: _build_auto_ok_input_cave(
                cave, address, scratch))
    placed = {}
    placed.update(mapjump_placed)
    placed.update(transition_worker_placed)
    placed.update(message_placed)
    placed.update(ask_placed)
    placed.update(name_placed)
    placed.update(field_placed)
    placed.update(battle_action_placed)
    placed.update(battle_text_placed)
    placed.update(battle_service_placed)
    placed.update(world_message_placed)
    placed.update(world_ask_placed)
    placed.update(world_service_placed)
    placed.update(input_placed)
    if hook_message:
        placed[message_hook] = A.b(message_hook, message_entry)
    if hook_field:
        placed[MAPJUMP_HOOK] = A.b(MAPJUMP_HOOK, mapjump_entry)
        placed[WORKER_LOOP_HOOK] = A.b(
            WORKER_LOOP_HOOK, transition_worker_entry)
    if hook_ask:
        placed[ASK_PRE_HOOK] = A.b(ASK_PRE_HOOK, ask_entry)
    if disable_name_change:
        placed[NAME_CHANGE_STATE_GATE] = A.nop()
        placed[NAME_CHANGE_HOOK] = A.b(NAME_CHANGE_HOOK, name_entry)
    if hook_field:
        placed[FIELD_HOOK] = A.b(FIELD_HOOK, field_entry)
    if battle_probability:
        placed[BATTLE_ACTION_HOOK] = A.b(BATTLE_ACTION_HOOK,
                                         battle_action_entry)
    if battle_text:
        placed[BATTLE_TEXT_HOOK] = A.b(BATTLE_TEXT_HOOK,
                                       battle_text_entry)
    if battle_probability or battle_text:
        placed[BATTLE_HOOK] = A.b(BATTLE_HOOK, battle_service_entry)
    if hook_world:
        for where, _expected, _target, _context in WORLD_MESSAGE_HOOKS:
            placed[where] = A.bl(where, world_message_entry)
        for where, _expected, _target, _context in WORLD_ASK_HOOKS:
            placed[where] = A.bl(where, world_ask_entry)
        placed[WORLD_SERVICE_HOOK] = A.b(WORLD_SERVICE_HOOK,
                                         world_service_entry)
    if auto_advance:
        placed[INPUT_POST_REFRESH_HOOK] = A.b(
            INPUT_POST_REFRESH_HOOK, input_entry)
    for where, word in placed.items():
        struct.pack_into('<I', text, where, word)
    raw[0] = bytes(text)
    out = _pack(blob, raw, bss_growth)
    check_segs, check_raw = _segments(out)
    assert check_segs[0][2] == len(raw[0])
    if hook_message:
        assert struct.unpack_from('<I', check_raw[0], message_hook)[0] == \
            A.b(message_hook, message_entry)
    if hook_field:
        assert struct.unpack_from('<I', check_raw[0], MAPJUMP_HOOK)[0] == \
            A.b(MAPJUMP_HOOK, mapjump_entry)
        assert struct.unpack_from('<I', check_raw[0],
                                  WORKER_LOOP_HOOK)[0] == \
            A.b(WORKER_LOOP_HOOK, transition_worker_entry)
    # Native MESSAGE/ASK updates are PC-relative BLs.  Their original words
    # cannot be copied into caves; prove every emitted replacement reaches
    # the same native routine from its new PC.
    def _bl_target(pc, word):
        imm26 = word & 0x03FFFFFF
        if imm26 & 0x02000000:
            imm26 -= 0x04000000
        return pc + (imm26 << 2)
    if message_phase == 'pre' and hook_message and not message_stub:
        assert any((word & 0xFC000000) == 0x94000000 and
                   _bl_target(where, word) == MESSAGE_PRE_TARGET
                   for where, word in message_placed.items())
    if hook_ask:
        assert struct.unpack_from('<I', check_raw[0], ASK_PRE_HOOK)[0] == \
            A.b(ASK_PRE_HOOK, ask_entry)
        assert any((word & 0xFC000000) == 0x94000000 and
                   _bl_target(where, word) == ASK_PRE_TARGET
                   for where, word in ask_placed.items())
    if disable_name_change:
        assert struct.unpack_from('<I', check_raw[0],
                                  NAME_CHANGE_STATE_GATE)[0] == A.nop()
        assert struct.unpack_from('<I', check_raw[0], NAME_CHANGE_HOOK)[0] == \
            A.b(NAME_CHANGE_HOOK, name_entry)
    if hook_field:
        assert struct.unpack_from('<I', check_raw[0], FIELD_HOOK)[0] == \
            A.b(FIELD_HOOK, field_entry)
    if battle_probability:
        assert struct.unpack_from('<I', check_raw[0], BATTLE_ACTION_HOOK)[0] == \
            A.b(BATTLE_ACTION_HOOK, battle_action_entry)
    if battle_text:
        assert struct.unpack_from('<I', check_raw[0], BATTLE_TEXT_HOOK)[0] == \
            A.b(BATTLE_TEXT_HOOK, battle_text_entry)
    if battle_probability or battle_text:
        assert struct.unpack_from('<I', check_raw[0], BATTLE_HOOK)[0] == \
            A.b(BATTLE_HOOK, battle_service_entry)
    if hook_world:
        for where, _expected, _target, _context in WORLD_MESSAGE_HOOKS:
            assert struct.unpack_from('<I', check_raw[0], where)[0] == \
                A.bl(where, world_message_entry)
        for where, _expected, _target, _context in WORLD_ASK_HOOKS:
            assert struct.unpack_from('<I', check_raw[0], where)[0] == \
                A.bl(where, world_ask_entry)
        assert struct.unpack_from('<I', check_raw[0],
                                  WORLD_SERVICE_HOOK)[0] == \
            A.b(WORLD_SERVICE_HOOK, world_service_entry)
    if auto_advance:
        assert struct.unpack_from('<I', check_raw[0],
                                  INPUT_POST_REFRESH_HOOK)[0] == \
            A.b(INPUT_POST_REFRESH_HOOK, input_entry)
    if not hook_field:
        assert struct.unpack_from('<I', check_raw[0],
                                  WORKER_LOOP_HOOK)[0] == WORKER_LOOP_ORIG
    assert struct.unpack_from('<I', out, 0x3C)[0] == \
        old_bss + bss_growth
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, 'wb') as handle:
        handle.write(out)
    return {'main': dest, 'scratch': scratch, 'bss_bytes': bss_growth,
            'scratch_align_pad': align_pad,
            'message_entry': message_entry, 'ask_entry': ask_entry,
            'name_change_entry': name_entry, 'field_entry': field_entry,
            'battle_action_entry': battle_action_entry,
            'battle_text_entry': battle_text_entry,
            'battle_service_entry': battle_service_entry,
            'world_message_entry': world_message_entry,
            'world_ask_entry': world_ask_entry,
            'world_service_entry': world_service_entry,
            'input_entry': input_entry,
            'mapjump_entry': mapjump_entry,
            'transition_worker_entry': transition_worker_entry,
            'mapjump_words': len(mapjump_placed),
            'transition_worker_words': len(transition_worker_placed),
            'message_words': len(message_placed), 'ask_words': len(ask_placed),
            'name_change_words': len(name_placed), 'field_words': len(field_placed),
            'battle_action_words': len(battle_action_placed),
            'battle_text_words': len(battle_text_placed),
            'battle_service_words': len(battle_service_placed),
            'world_message_words': len(world_message_placed),
            'world_ask_words': len(world_ask_placed),
            'world_service_words': len(world_service_placed),
            'input_words': len(input_placed),
            'forced_name': forced_name, 'forced_alt_name': forced_alt_name,
            'retain_completed': retain_completed,
            'build_key_then_forced': build_key_then_forced,
            'forced_suffix': forced_suffix,
            'field_name_prefix_bytes': field_name_prefix_bytes,
            'music_thread_name': music_thread_name,
            'key_mode': key_mode,
            'folder_forced_suffix': folder_forced_suffix,
            'voice_gain': voice_gain,
            'duck_percent': duck_percent,
            'duck_attack_frames': duck_attack_frames,
            'duck_release_frames': duck_release_frames,
            'message_phase': message_phase, 'hook_ask': hook_ask,
            'hook_world': hook_world,
            'disable_name_change': disable_name_change,
            'auto_advance': auto_advance,
            'battle_probability': battle_probability,
            'battle_text': battle_text}


# WHAT EACH LEVEL ADDS, SMALLEST FIRST.
#
# The runtime is not one feature. It is a core -- the MESSAGE producer and the
# per-frame field service, which is what "Echo-S speaks in a field" actually
# means -- plus four independent layers bolted to their own hook sites, three
# of which write GUEST memory:
#
#   ask         the ASK producer, for highlighted dialogue options
#   name        the name-entry bypass: NOPs a branch in the name menu and
#               writes the default names into guest memory
#   world       the world-map message/ask producers and their own per-frame
#               service, chained behind the ambient world stop
#   auto        auto text advance: writes the OK-button status in guest memory
#               every input refresh
#
# The author shipped all of them at once, and they worked -- on a module with
# nothing else in it. Composed onto this project's eighteen existing caves they
# do not, and the crash report puts the fault in the field script VM's own
# opcode dispatch, which is reached by every one of these paths.
#
# So the levels exist to answer that with one variable instead of a guess.
# Each is a two-minute ExeFS-only rebuild.
# `core` is itself two independent caves, and splitting them is the whole
# point of the two smallest levels: `service` and `message` each install ONE
# of them, so a single hardware test says which. Neither plays anything --
# with only the producer nothing consumes its command, and with only the
# service nothing ever publishes one -- but a crash does not need playback,
# and that is exactly what we are chasing.
VOICE_LEVELS = ('none', 'service', 'message', 'core', 'ask', 'name', 'world',
                'full')


def _level_flags(level):
    if level not in VOICE_LEVELS:
        raise ValueError('voice level must be one of %s, not %r'
                         % (', '.join(VOICE_LEVELS), level))
    rank = VOICE_LEVELS.index(level)
    return {
        # `service` installs the field service; `message` installs the MESSAGE
        # producer INSTEAD; `core` and above install both.
        'hook_field': rank in (VOICE_LEVELS.index('service'),)
                      or rank >= VOICE_LEVELS.index('core'),
        'hook_message': rank in (VOICE_LEVELS.index('message'),)
                        or rank >= VOICE_LEVELS.index('core'),
        'hook_ask': rank >= VOICE_LEVELS.index('ask'),
        'disable_name_change': rank >= VOICE_LEVELS.index('name'),
        'hook_world': rank >= VOICE_LEVELS.index('world'),
        'auto_advance': rank >= VOICE_LEVELS.index('full'),
    }


def apply_to_nso(src, dest, auto_advance=True, voice_gain=VOICE_GAIN_DEFAULT,
                 duck_percent=85,
                 duck_attack_frames=12, duck_release_frames=45,
                 battle_probability=0, battle_text=False, level='full',
                 forced_name=None, message_phase='pre',
                 message_stub=None, defer_foreign_play=True,
                 allow_foreign_field_replace=False,
                 stop_on_close=True, publish_on_dialog_change=True,
                 first_in_burst_wins=True,
                 replay_guard=False,
                 stream_once=True):
    """
    Install the production dialogue runtime, `src` -> `dest`, in this
    project's idiom.

    The fork exposed roughly twenty entry points here, nearly all of them
    hardware probes with a UDF in them. This is the one the build calls, and
    the arguments it does not take are the ones there was never a reason to
    vary: `key_mode` is always FFNx's folder layout, because that is what
    `voicemod` stages and what makes the lookup table unnecessary;
    `message_phase` is always 'pre', because the post-update site cannot see
    the transition FFNx keys off; and the diagnostic thread title is always
    shortened, because leaving it long aborts in SetThreadNamePointer.

    The report adds one thing the fork's did not: `chained`, naming each site
    this had to install in front of and the cave it now jumps to. That list
    is how a build log shows composition happened, rather than leaving it to
    be inferred from the absence of a crash.
    """
    # `_emplace_voice` calls `voice_ogg.have_ffmpeg` before it calls us.  A
    # failed encode or an unrecognised scoped set must therefore leave the
    # game on its ordinary dialogue path, never install a player that probes
    # stale/missing clips.  Unit callers that patch a module in isolation do
    # not attempt staging, and deliberately remain supported.
    if voice_ogg.stage_attempted() and not voice_ogg.stage_succeeded():
        raise ValueError('Echo-S voice staging did not complete; refusing to '
                         'install the dialogue runtime')

    with open(src, 'rb') as handle:
        _segs, raw = _segments(handle.read())
    text = raw[0]

    chained = []
    for site, stock, who in (
            (FIELD_HOOK, FIELD_ORIG, 'analog-360\'s field hook'),
            (BATTLE_HOOK, BATTLE_ORIG, 'the ambient battle entry'),
            (WORLD_SERVICE_HOOK, WORLD_SERVICE_ORIG,
             'the ambient world stop')):
        word, = struct.unpack_from('<I', text, site)
        if word == stock:
            continue
        target = _direct_branch_target(site, word)
        if target is None:
            raise ValueError(
                'voice service site +0x%X holds %08X -- neither the stock '
                '%08X nor a direct branch this can chain behind' %
                (site, word, stock))
        chained.append((site, who, target))

    flags = _level_flags(level)
    # `auto_advance` is both a level and an explicit argument; the caller's
    # value can only turn it OFF, never on below the level that includes it.
    flags['auto_advance'] = flags['auto_advance'] and auto_advance
    # `forced_name` replaces the live key builder with one fixed filename:
    # 49 words and the FIELD_FILE_NAME read disappear, and the producer stops
    # walking a guest string altogether. Nothing useful plays, but it splits
    # the producer in half for a bisect -- everything up to and including the
    # MESSAGE state read stays, and only the key construction goes.
    report = patch_main_clocked_dialogue(
        src, dest, music_thread_name='music stream', key_mode='folder',
        message_phase=message_phase, forced_name=forced_name,
        message_stub=message_stub,
        voice_gain=voice_gain,
        duck_percent=duck_percent, duck_attack_frames=duck_attack_frames,
        duck_release_frames=duck_release_frames,
        battle_probability=battle_probability, battle_text=battle_text,
        defer_foreign_play=defer_foreign_play,
        stop_on_close=stop_on_close,
        allow_foreign_field_replace=allow_foreign_field_replace,
        publish_on_dialog_change=publish_on_dialog_change,
        first_in_burst_wins=first_in_burst_wins,
        replay_guard=replay_guard,
        stream_once=stream_once,
        **flags)
    report['level'] = level
    report['defer_foreign_play'] = (defer_foreign_play
                                    and not allow_foreign_field_replace)
    report['allow_foreign_field_replace'] = allow_foreign_field_replace
    report['stop_on_close'] = stop_on_close
    report['publish_on_dialog_change'] = publish_on_dialog_change
    report['first_in_burst_wins'] = first_in_burst_wins
    report['replay_guard'] = replay_guard
    report['stream_once'] = stream_once
    report['forced_name'] = forced_name
    report['message_phase'] = message_phase
    report['message_stub'] = message_stub
    # Battle is reported only when it was asked for: with battle_probability
    # 0 and battle_text off, BATTLE_HOOK is never written, so listing it as
    # chained would be a lie the log tells every build.
    if not (battle_probability or battle_text):
        chained = [c for c in chained if c[0] != BATTLE_HOOK]
    report['chained'] = chained
    return report
