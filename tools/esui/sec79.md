## 79. BUILD 621 — Enhanced Stock UI, screen 1: the main menu

**What's in it.** ESUI's main-menu set (`00-Main.txt` + `00-Spacing.txt`), carried onto the Switch. It is behind a switch, `SEVENTH_NX_ESUI=main`. The ESUI mod itself stays **off** in the mod list.

- **Code: 106 ARM64 words, edited in place, no caves** (new `ff7nx_esui.py`).
  - Every changed x86 instruction was found in the port's ARM64 body of the same function, in 10 functions: 0x6C62A2, 0x6C98A6, the three time-cap helpers, 0x6CA346, 0x6F62F7, 0x6F9739, 0x70212A, 0x703176.
  - Most are a single immediate. Where the port folded or shared a constant, a few neighbouring words change:
    - `(x−1)*12` was folded to `x*12−12`, so both words change.
    - `imul 0x22` was strength-reduced; it becomes `mov w10,#0x1a ; mul`.
    - A `push 2` shares its register with later pushes. It becomes store 3, restore 2; the slot comes from re-deriving a global address.
    - A blinking-colon `csel` becomes a plain `mov`.
    - `shl edx,1` becomes `×61>>5`.
    - Two copies of one duplicated block both change.
  - One more word is in `.rodata`. The location box's X is read from a per-language table `[en, fr, de, es]`; only the English entry changes, 0x1A4 → 0x1B0. Its only reader is +DC534C.
  - Each word is verified before it is written. A different module is refused, not corrupted.
- **Verification (unicorn, differential).** Stock vs ESUI x86, and port vs edited ARM64, were run side by side over randomised guest memory. ESUI's changes on x86 must equal the edits' changes on ARM64: every call argument, every memory write, the return value. Result: 0 differing runs in every function where the paths line up.
  - Six instructions are behind a menu state the harness could not reach. Their ARM words were checked by reading.
  - Where the port replaced a call with its own code, the paths differ between x86 and ARM (0x6F9739 calls its own helper there). The ARM change was checked against the formula.
- **Data, into ff7_en (10 patches, 15 bytes):**
  - texture names `usfont/btl_win*_h.tim` → `_l` (8);
  - menu font scale 1.667 → 2.0;
  - "Next Level" / "Limit Level" in mixed case.
  - The calendar's two parking spots stay free.
- **The Echo-S calendar owns the Time/Gil box.** On PC, ESUI resolves the overlap with `ECHOS/MAIN/05 - ESUI Theme.txt`. `ff7nx_calendar` now carries that layout as an **ESUI profile**:
  - box Y 0x155, height 0x5A;
  - Gil row, Time row, clock at 0x4E/0x6E/0x78, hours with three digits;
  - date row at 0x3A, Date label X 3, month 0x52, separator 0x6D, day 0x78.
  - The ESUI pass runs after the calendar. It leaves the calendar's 15 words to it whenever the calendar is installed, which it checks in the module itself. Without the calendar, 00-Main's own box values apply.
  - Checked end to end: x86 with 00-Main + Echo-S's calendar hext + the Theme, against ARM64 with calendar(ESUI) + ESUI. All main-menu draws match except three deliberate differences:
    - The day is the repurposed seconds draw, at the same place.
    - One colon is drawn instead of two stacked ones.
    - **The separator sits 2 below the day** (the Theme leaves Echo-S's 0x4C, which on its raised row would be 0x0D under the day).
- **Not in this build:**
  - **ESUI's HD font/frame art.** The menus draw the stock `_l` sheets at 2.0, which is ESUI's own design with stock art.
  - **00-Spacing's width table.** It belongs to that HD font and goes in with it. `patch_window_bin` is written and tested.
  - The label colour file (colour 6). The port shares one register for every colour-7 push in that box.
  - 01-Base_1 (opening-music cue, not a menu) and 01-DLPB (helper caves for other screens).
  - Controller options: never used. The Switch button glyphs come from the port's own `data/png/buttons.png`, which is untouched.
- **Preview:** `PREVIEW-621-esui-main-menu.png`. It is laid out from the emulated draw calls of both modules: boxes, text anchors, numbers and bars at their real coordinates. The art is a placeholder; text is drawn 1.2x larger after, for the 1.667 → 2.0 scale.

**Files:** `ff7nx_esui.py` (NEW), `ff7nx_calendar.py` (ESUI profile), `build.py` (`apply_esui`, cache lists), `7th_heaven_nx.py` (call after `apply_calendar`), `tests/test_build621.py` (NEW). Tests: test_build621 + test_calendar + test_build619 = 85 passed.

**Build** (exefs/main and ff7_en only, so every archive can be reused):

    SEVENTH_NX_ESUI=main SEVENTH_NX_REUSE_FLEVEL=1 SEVENTH_NX_REUSE_BATTLE=1 SEVENTH_NX_REUSE_MAGIC=1 SEVENTH_NX_REUSE_WORLD=1 SEVENTH_NX_REUSE_HIGH=1 SEVENTH_NX_REUSE_CHAR=1 SEVENTH_NX_REUSE_MENU=1 python3 7th_heaven_nx.py --cli

**Log lines:**
- `ESUI profile (BUILD 621)` (calendar);
- `Enhanced Stock UI (BUILD 621): main ...`;
- `the Echo-S calendar owns the Time/Gil box ... 15 of its words are left to it`;
- `91 words in exefs/main`;
- `10 data patch(es) in ff7_en`.

**To test:** open the main menu. Check:
- the command list (narrower);
- the party block spacing (HP/MP digits closer, bars and "Next Level" moved);
- the location box (narrower, lower);
- the Time/Gil/Date box (lower, shorter);
- the slide-in animation.

Leave `SEVENTH_NX_ESUI` unset to get the current menu back.
