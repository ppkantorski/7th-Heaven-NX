#!/usr/bin/env python3
"""
7th Heaven NX -- apply 7th Heaven .iro mods to the Switch version of FF7.

Layout expected in this script's directory:

    7th_heaven_nx.py
    workingdir/          your ripped game data (data/field/..., data/battle/...)
    mods/                .iro files
    cache/               created automatically, extracted mods
    sdout/               created automatically, copy onto your SD card

Run with no arguments for the UI, or --cli to build with saved settings.
"""
import os
import queue
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build  # noqa: E402
import lgp    # noqa: E402

WORKINGDIR = os.path.join(HERE, 'workingdir')
MODS_DIR = os.path.join(HERE, 'mods')
CACHE_DIR = os.path.join(HERE, 'cache')
SDOUT_DIR = os.path.join(HERE, 'sdout')
SETTINGS = os.path.join(HERE, 'settings.json')


def discover_mods():
    if not os.path.isdir(MODS_DIR):
        return []
    found = []
    for fn in sorted(os.listdir(MODS_DIR)):
        if fn.lower().endswith('.iro'):
            found.append(build.Mod(os.path.join(MODS_DIR, fn), CACHE_DIR))
    return found


def run_build(mods, enabled, settings_by_mod, log, progress):
    if not os.path.isdir(WORKINGDIR):
        log('ERROR: no workingdir/ folder found.')
        log(f'Put your ripped game data at {WORKINGDIR}')
        return False

    log('reading your archives ...')
    catalogs, paths = build.load_catalogs(WORKINGDIR, log)
    if not catalogs:
        log('ERROR: no LGP archives found under workingdir/.')
        log('Expected e.g. workingdir/data/field/char.lgp')
        return False

    active = [m for m in mods if enabled.get(m.filename)]
    if not active:
        log('nothing enabled.')
        return False

    log('')
    for mod in active:
        mod.ensure_extracted(log, lambda i, n: progress(i, n, 'extracting'))

    log('')
    plan = build.build_plan(active, settings_by_mod, catalogs, log)

    log('')
    log(f'portable files : {plan.total_portable()}')
    if plan.skipped_ffnx:
        log(f'FFNx textures  : {plan.skipped_ffnx} (skipped, no Switch loader)')
    if plan.unmatched:
        log(f'unrecognised   : {len(plan.unmatched)}')
        for f in plan.unmatched[:5]:
            log(f'    {f}')
    for archive, name, first, second in plan.conflicts[:10]:
        log(f'override: {archive}/{name}  {first} -> {second}')
    if plan.total_portable() == 0:
        log('')
        log('Nothing to do -- these mods are FFNx-only.')
        return False

    log('')
    produced = build.apply_plan(plan, paths, SDOUT_DIR, log,
                               lambda i, n, name: progress(i, n, name))
    log('')
    if produced:
        log(f'done. {len(produced)} files in sdout/')
        log('copy the contents of sdout/ onto the root of your SD card.')
    else:
        log('nothing was written.')
    return bool(produced)


# ------------------------------------------------------------------- UI

def _reveal(path):
    """Open a folder in the OS file browser."""
    import subprocess
    if not os.path.isdir(path):
        return
    if sys.platform == 'darwin':
        subprocess.Popen(['open', path])
    elif sys.platform.startswith('win'):
        os.startfile(path)  # noqa
    else:
        subprocess.Popen(['xdg-open', path])


def launch_ui():
    import tkinter as tk
    from tkinter import ttk, messagebox

    mods = discover_mods()
    saved = build.load_settings(SETTINGS)

    # Cheap (header-only, no decompression) so it's fine to do at startup.
    # Lets the "modifies:" annotations below be exact archive-name matches
    # instead of folder-name guesses.
    catalogs = {}
    if os.path.isdir(WORKINGDIR):
        try:
            catalogs, _ = build.load_catalogs(WORKINGDIR)
        except Exception:
            catalogs = {}

    root = tk.Tk()
    root.title('7th Heaven NX')
    root.geometry('1000x720')
    root.minsize(880, 600)

    # ---------- dark blue theme -----------------------------------------
    # This app always renders the same dark theme, regardless of the OS's
    # own light/dark setting. 'aqua' (macOS's native ttk theme) was the
    # source of the jarring white-selection-bar bug: aqua draws its own
    # native chrome per-widget and mostly ignores colors we set on it, so a
    # hardcoded "light blue" selection color rendered as a stark white/pale
    # bar with no way to fix it from here. 'clam' is fully style-driven --
    # nothing native, no OS-dependent surprises -- so every color below is
    # exactly what renders, on every platform, every time.
    BG_APP        = '#0b1220'   # window + all panel backgrounds
    BG_ROW        = '#151d30'   # unselected mod-row background
    BG_ROW_HOVER  = '#1a2438'   # mod-row background on mouse-over
    BG_ROW_SEL    = '#1c3a5e'   # selected mod-row background (blue tint)
    BORDER        = '#232e46'
    ACCENT        = '#4f9dff'
    ACCENT_SOFT   = '#8ec2ff'
    TEXT_PRIMARY  = '#e8ecf4'
    TEXT_SECONDARY = '#8b93a8'
    TEXT_MUTED    = '#5c6579'
    ARCHIVE_EXACT = ACCENT_SOFT
    ARCHIVE_EST   = TEXT_MUTED
    LOG_BG        = '#070b14'

    root.configure(bg=BG_APP)

    style = ttk.Style()
    try:
        style.theme_use('clam')
    except tk.TclError:
        pass

    style.configure('.', background=BG_APP, foreground=TEXT_PRIMARY,
                    fieldbackground=BG_ROW, bordercolor=BORDER,
                    darkcolor=BG_APP, lightcolor=BG_APP,
                    troughcolor=BG_ROW, focuscolor=ACCENT,
                    font=('Helvetica', 11))
    style.configure('TFrame', background=BG_APP)
    style.configure('TLabel', background=BG_APP, foreground=TEXT_PRIMARY)
    style.configure('TLabelframe', background=BG_APP, bordercolor=BORDER,
                    relief='solid', borderwidth=1)
    style.configure('TLabelframe.Label', background=BG_APP,
                    foreground=TEXT_SECONDARY, font=('Helvetica', 10, 'bold'))
    style.configure('TPanedwindow', background=BG_APP)
    style.configure('TSeparator', background=BORDER)

    style.configure('Header.TLabel', font=('Helvetica', 18, 'bold'),
                    foreground=TEXT_PRIMARY)
    style.configure('Sub.TLabel', foreground=TEXT_SECONDARY)
    style.configure('ModName.TLabel', font=('Helvetica', 12),
                    background=BG_ROW, foreground=TEXT_PRIMARY)
    style.configure('ModNameSelected.TLabel', font=('Helvetica', 12, 'bold'),
                    background=BG_ROW_SEL, foreground=ACCENT_SOFT)
    style.configure('ColHeader.TLabel', font=('Helvetica', 9, 'bold'),
                    foreground=TEXT_MUTED)
    style.configure('Archive.TLabel', font=('Menlo', 10),
                    foreground=ARCHIVE_EXACT)
    style.configure('ArchiveEst.TLabel', font=('Menlo', 10),
                    foreground=ARCHIVE_EST)

    style.configure('TButton', background=BG_ROW, foreground=TEXT_PRIMARY,
                    bordercolor=BORDER, padding=(10, 6))
    style.map('TButton',
              background=[('active', BG_ROW_HOVER), ('disabled', BG_APP)],
              foreground=[('disabled', TEXT_MUTED)])
    style.configure('Build.TButton', font=('Helvetica', 13, 'bold'),
                    padding=(18, 9), background=ACCENT, foreground='#08111e',
                    bordercolor=ACCENT)
    style.map('Build.TButton',
              background=[('active', ACCENT_SOFT), ('disabled', BG_ROW)],
              foreground=[('disabled', TEXT_MUTED)])

    style.configure('TCombobox', fieldbackground=BG_ROW, background=BG_ROW,
                    foreground=TEXT_PRIMARY, arrowcolor=TEXT_SECONDARY,
                    bordercolor=BORDER, selectbackground=BG_ROW,
                    selectforeground=TEXT_PRIMARY)
    style.map('TCombobox',
              fieldbackground=[('readonly', BG_ROW), ('focus', BG_ROW)],
              foreground=[('readonly', TEXT_PRIMARY)],
              background=[('readonly', BG_ROW)],
              bordercolor=[('focus', ACCENT)])
    # The Combobox's dropdown list is a separate, un-styled Tk Listbox --
    # without this it stays white-on-black regardless of the ttk style set
    # above, which would be its own jarring mismatch against a dark app.
    root.option_add('*TCombobox*Listbox.background', BG_ROW)
    root.option_add('*TCombobox*Listbox.foreground', TEXT_PRIMARY)
    root.option_add('*TCombobox*Listbox.selectBackground', ACCENT)
    root.option_add('*TCombobox*Listbox.selectForeground', '#08111e')
    root.option_add('*TCombobox*Listbox.font', ('Helvetica', 11))

    for pbstyle in ('TProgressbar', 'Horizontal.TProgressbar'):
        style.configure(pbstyle, background=ACCENT, troughcolor=BG_ROW,
                        bordercolor=BORDER, lightcolor=ACCENT,
                        darkcolor=ACCENT)

    style.configure('Vertical.TScrollbar', background=BORDER,
                    troughcolor=BG_APP, bordercolor=BG_APP,
                    arrowcolor=TEXT_SECONDARY, relief='flat', gripcount=0)
    style.map('Vertical.TScrollbar',
              background=[('active', ACCENT), ('pressed', ACCENT)])

    settings_by_mod = {}
    enabled = {}
    messages = queue.Queue()

    outer = ttk.Frame(root, padding=14)
    outer.pack(fill='both', expand=True)

    # ---------- header (top) ----------
    header = ttk.Frame(outer)
    header.pack(side='top', fill='x')
    ttk.Label(header, text='7th Heaven NX', style='Header.TLabel'
              ).pack(side='left')
    status = 'workingdir found' if os.path.isdir(WORKINGDIR) \
        else 'workingdir/ MISSING — put your ripped data there'
    ttk.Label(header, text=f'{len(mods)} mods  ·  {status}',
              style='Sub.TLabel').pack(side='left', padx=12)

    # ---------- action bar (bottom, packed FIRST so it is never clipped) ----
    actions = ttk.Frame(outer)
    actions.pack(side='bottom', fill='x', pady=(12, 0))

    build_btn = ttk.Button(actions, text='► Build SD Output',
                           style='Build.TButton')
    build_btn.pack(side='right')
    open_btn = ttk.Button(actions, text='Open output folder',
                          command=lambda: _reveal(SDOUT_DIR))
    open_btn.pack(side='right', padx=(0, 8))
    open_btn.state(['disabled'])

    bar = ttk.Progressbar(actions, mode='determinate')
    bar.pack(side='left', fill='x', expand=True, pady=6)
    statuslabel = ttk.Label(actions, text='ready', style='Sub.TLabel')
    statuslabel.pack(side='left', padx=10)

    # ---------- resizable split: mods/options area above, log below --------
    # A vertical PanedWindow gives a real drag handle on the divider right
    # above "Log" -- the same mechanism as the existing Mods/Options
    # divider -- so the log can be dragged taller or shorter at will instead
    # of being stuck at a fixed height.
    vsplit = ttk.PanedWindow(outer, orient='vertical')
    vsplit.pack(side='top', fill='both', expand=True, pady=(10, 0))

    content = ttk.Frame(vsplit)
    vsplit.add(content, weight=4)

    logframe = ttk.Labelframe(vsplit, text='Log', padding=(2, 2))
    vsplit.add(logframe, weight=1)

    logbox = tk.Text(logframe, height=8, wrap='none', font=('Menlo', 11),
                     relief='flat', background=LOG_BG, foreground='#c9d2e3',
                     insertbackground=ACCENT, selectbackground=BG_ROW_SEL,
                     highlightthickness=0, state='disabled')
    logscroll = ttk.Scrollbar(logframe, orient='vertical',
                              command=logbox.yview)
    logbox.configure(yscrollcommand=logscroll.set)
    logscroll.pack(side='right', fill='y')
    logbox.pack(side='left', fill='both', expand=True)

    def log_write(text):
        logbox.configure(state='normal')
        logbox.insert('end', text + '\n')
        logbox.see('end')
        logbox.configure(state='disabled')

    def log_clear():
        logbox.configure(state='normal')
        logbox.delete('1.0', 'end')
        logbox.configure(state='disabled')

    # ---------- centre: mods | options (fills remaining space) ----------
    panes = ttk.PanedWindow(content, orient='horizontal')
    panes.pack(fill='both', expand=True)

    def make_scrollable(parent, inner_width_tracks_canvas=False):
        """
        A canvas+frame scroll area that:
          - only responds to the mouse wheel when there's actually something
            to scroll (fixes being able to drag content past its own bottom
            with nothing left to show -- there was previously no bound on
            how far a wheel event could move the view);
          - shows/hides its own scrollbar depending on whether the content
            overflows, instead of always reserving the space.
        Returns (canvas, inner_frame).
        """
        canvas = tk.Canvas(parent, highlightthickness=0, bd=0,
                          background=BG_APP)
        vscroll = ttk.Scrollbar(parent, orient='vertical',
                                command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner_id = canvas.create_window((0, 0), window=inner, anchor='nw')
        canvas.configure(yscrollcommand=vscroll.set)
        canvas.pack(side='left', fill='both', expand=True)

        def sync(_evt=None):
            canvas.configure(scrollregion=canvas.bbox('all'))
            canvas.update_idletasks()
            bbox = canvas.bbox('all')
            content_h = (bbox[3] - bbox[1]) if bbox else 0
            overflow = content_h > canvas.winfo_height()
            if overflow and not vscroll.winfo_ismapped():
                vscroll.pack(side='right', fill='y')
            elif not overflow and vscroll.winfo_ismapped():
                vscroll.pack_forget()
                canvas.yview_moveto(0)

        inner.bind('<Configure>', sync)
        if inner_width_tracks_canvas:
            canvas.bind('<Configure>',
                        lambda e: (canvas.itemconfigure(inner_id,
                                                         width=e.width),
                                  sync()))
        else:
            canvas.bind('<Configure>', sync)

        def on_wheel(event):
            top, bottom = canvas.yview()
            if top <= 0.0 and bottom >= 1.0:
                return  # everything already fits -- nothing to scroll
            step = -1 if event.delta > 0 else 1
            if step < 0 and top <= 0.0:
                return
            if step > 0 and bottom >= 1.0:
                return
            canvas.yview_scroll(step, 'units')

        canvas.bind('<Enter>',
                   lambda e: canvas.bind_all('<MouseWheel>', on_wheel))
        canvas.bind('<Leave>', lambda e: canvas.unbind_all('<MouseWheel>'))
        return canvas, inner

    left = ttk.Labelframe(panes, text='Mods', padding=8)
    panes.add(left, weight=1)
    _canvas, listframe = make_scrollable(left, inner_width_tracks_canvas=True)

    # Options pane, scrollable (mods can have a dozen-plus options).
    right = ttk.Labelframe(panes, text='Options', padding=6)
    panes.add(right, weight=2)
    _opt_canvas, options_body = make_scrollable(
        right, inner_width_tracks_canvas=True)

    selected = tk.StringVar(value=mods[0].filename if mods else '')

    def archive_label(parent, archives, exact, wraplength=220, **grid_kw):
        text = build.archives_text(archives, exact)
        if not text:
            text = '—'
        lbl = ttk.Label(parent, text=text, wraplength=wraplength,
                        justify='left',
                        style='Archive.TLabel' if exact else 'ArchiveEst.TLabel')
        lbl.grid(**grid_kw)
        return lbl

    def show_options(mod):
        for w in options_body.winfo_children():
            w.destroy()
        if mod is None:
            return
        if not mod.manifest:
            ttk.Label(options_body,
                      text=(mod.error or 'Not extracted yet.') +
                      '\n\nOptions appear the first time you build.',
                      style='Sub.TLabel', justify='left').pack(anchor='w')
            return
        m = mod.manifest
        ttk.Label(options_body, text=m.name or mod.stem,
                  font=('Helvetica', 13, 'bold')).pack(anchor='w')
        meta = ' · '.join(x for x in (m.author, m.version, m.category) if x)
        if meta:
            ttk.Label(options_body, text=meta, style='Sub.TLabel'
                      ).pack(anchor='w', pady=(0, 2))
        store = settings_by_mod.setdefault(mod.filename, {})
        mod_arch, mod_exact = build.mod_archives(mod, store, catalogs)
        summary = ttk.Frame(options_body)
        summary.pack(anchor='w', pady=(2, 10))
        ttk.Label(summary, text='Modifies (as configured): ',
                  style='Sub.TLabel').pack(side='left')
        summary_text = build.archives_text(mod_arch, mod_exact) or '—'
        ttk.Label(summary, text=summary_text, wraplength=460, justify='left',
                  style='Archive.TLabel' if mod_exact else 'ArchiveEst.TLabel'
                  ).pack(side='left')
        if not mod_exact and mod_arch:
            ttk.Label(options_body,
                      text='(estimated from folder names — point this app '
                      'at your workingdir/ for exact matches)',
                      style='Sub.TLabel', font=('Helvetica', 9)
                      ).pack(anchor='w', pady=(0, 8))
        if not m.options:
            ttk.Label(options_body, text='No configurable options.',
                      style='Sub.TLabel').pack(anchor='w')
            return
        # Fixed column widths (not content-driven) so the table stays
        # neatly aligned left-to-right instead of shifting around based on
        # whichever option happens to have the longest name; long text
        # wraps within its own column rather than stretching it.
        OPTION_COL, VALUE_COL, MODIFIES_COL = 190, 180, 200
        grid = ttk.Frame(options_body)
        grid.pack(fill='x', pady=(4, 0))
        grid.columnconfigure(0, weight=0, minsize=OPTION_COL)
        grid.columnconfigure(1, weight=0, minsize=VALUE_COL)
        grid.columnconfigure(2, weight=1, minsize=MODIFIES_COL)
        ttk.Label(grid, text='OPTION', style='ColHeader.TLabel').grid(
            row=0, column=0, sticky='w', padx=(0, 12))
        ttk.Label(grid, text='VALUE', style='ColHeader.TLabel').grid(
            row=0, column=1, sticky='w')
        ttk.Label(grid, text='MODIFIES', style='ColHeader.TLabel').grid(
            row=0, column=2, sticky='w', padx=(14, 0))
        ttk.Separator(grid, orient='horizontal').grid(
            row=1, column=0, columnspan=3, sticky='ew', pady=(2, 6))
        for i, opt in enumerate(m.options):
            row = i + 2
            ttk.Label(grid, text=opt.name, wraplength=OPTION_COL - 16,
                     justify='left').grid(
                row=row, column=0, sticky='w', pady=3, padx=(0, 12))
            labels = [label for _, label in opt.values]
            current = store.get(opt.id, opt.default)
            var = tk.StringVar(value=opt.label_for(current))
            combo = ttk.Combobox(grid, textvariable=var, values=labels,
                                 state='readonly', width=16)
            combo.grid(row=row, column=1, sticky='ew', pady=3, padx=(0, 14))

            opt_arch, opt_exact = build.option_archives(mod, opt, catalogs)
            archive_label(grid, opt_arch, opt_exact,
                         wraplength=MODIFIES_COL - 10,
                         row=row, column=2, sticky='w', padx=(14, 0))

            def on_pick(_evt, o=opt, v=var, mf=mod.filename):
                for value, label in o.values:
                    if label == v.get():
                        settings_by_mod.setdefault(mf, {})[o.id] = value
                        break

            combo.bind('<<ComboboxSelected>>', on_pick)
            store.setdefault(opt.id, current)

    rows = {}  # filename -> (row, indicator, checkbutton, label)

    def _paint_row(filename, bg, fg, bold, indicator_bg):
        row, indicator, chk, label = rows[filename]
        row.configure(bg=bg)
        indicator.configure(bg=indicator_bg)
        chk.configure(bg=bg, activebackground=bg, selectcolor=bg,
                      highlightbackground=bg)
        label.configure(bg=bg, fg=fg,
                        font=('Helvetica', 12, 'bold' if bold else 'normal'))

    def _restyle(filename):
        if filename not in rows:
            return
        if filename == selected.get():
            _paint_row(filename, BG_ROW_SEL, ACCENT_SOFT, True, ACCENT)
        else:
            _paint_row(filename, BG_ROW, TEXT_PRIMARY, False, BG_ROW)

    def select(mod):
        prev = selected.get()
        selected.set(mod.filename)
        if prev and prev != mod.filename:
            _restyle(prev)
        _restyle(mod.filename)
        show_options(mod)

    def on_row_enter(filename):
        if filename != selected.get():
            _paint_row(filename, BG_ROW_HOVER, TEXT_PRIMARY, False, BG_ROW_HOVER)

    def on_row_leave(filename):
        if filename != selected.get():
            _restyle(filename)

    for i, mod in enumerate(mods):
        row = tk.Frame(listframe, bg=BG_ROW, highlightthickness=0, bd=0)
        row.pack(fill='x', pady=(0, 4))

        indicator = tk.Frame(row, width=4, bg=BG_ROW, highlightthickness=0,
                             bd=0)
        indicator.pack(side='left', fill='y')
        indicator.pack_propagate(False)

        mod._load_manifest()
        var = tk.BooleanVar(
            value=saved.get(mod.filename, {}).get('enabled', True))
        enabled[mod.filename] = var
        chk = tk.Checkbutton(
            row, variable=var, bg=BG_ROW, activebackground=BG_ROW,
            selectcolor=BG_ROW, highlightthickness=0, bd=0,
            fg=TEXT_PRIMARY, activeforeground=TEXT_PRIMARY, cursor='hand2')
        chk.pack(side='left', padx=(10, 6), pady=9)

        label = tk.Label(row, text=mod.display_name, cursor='hand2',
                         bg=BG_ROW, fg=TEXT_PRIMARY, anchor='w',
                         font=('Helvetica', 12))
        label.pack(side='left', fill='x', expand=True, pady=9, padx=(0, 8))

        rows[mod.filename] = (row, indicator, chk, label)

        for widget in (row, indicator, label):
            widget.bind('<Button-1>', lambda e, m=mod: select(m))
            widget.bind('<Enter>', lambda e, f=mod.filename: on_row_enter(f))
            widget.bind('<Leave>', lambda e, f=mod.filename: on_row_leave(f))

        settings_by_mod[mod.filename] = dict(
            saved.get(mod.filename, {}).get('options', {})
            or (mod.manifest.defaults() if mod.manifest else {}))

    if mods:
        select(mods[0])

    # ---------- queue pump ----------
    def pump():
        try:
            while True:
                kind, payload = messages.get_nowait()
                if kind == 'log':
                    log_write(payload)
                elif kind == 'progress':
                    i, n, label = payload
                    bar['maximum'] = max(n, 1)
                    bar['value'] = i
                    statuslabel.configure(text=label or '')
                elif kind == 'done':
                    build_btn.state(['!disabled'])
                    bar['value'] = bar['maximum'] if payload else 0
                    statuslabel.configure(text='done' if payload else 'failed')
                    if payload:
                        open_btn.state(['!disabled'])
                        # refresh options: manifests now exist post-extract
                        for m in mods:
                            m._load_manifest()
                        cur = next((m for m in mods
                                    if m.filename == selected.get()), None)
                        if cur:
                            show_options(cur)
                        messagebox.showinfo(
                            '7th Heaven NX',
                            'Build complete.\n\nCopy everything inside the '
                            'sdout folder onto the root of your SD card.')
        except queue.Empty:
            pass
        root.after(80, pump)

    def start():
        log_clear()
        build_btn.state(['disabled'])
        open_btn.state(['disabled'])
        statuslabel.configure(text='working…')
        persist = {}
        for mod in mods:
            persist[mod.filename] = {
                'enabled': bool(enabled[mod.filename].get()),
                'options': settings_by_mod.get(mod.filename, {}),
            }
        build.save_settings(SETTINGS, persist)

        def worker():
            ok = False
            try:
                ok = run_build(
                    mods,
                    {k: v.get() for k, v in enabled.items()},
                    settings_by_mod,
                    lambda s: messages.put(('log', s)),
                    lambda i, n, label='': messages.put(
                        ('progress', (i, n, label))))
            except Exception as exc:
                import traceback
                messages.put(('log', 'ERROR: ' + str(exc)))
                messages.put(('log', traceback.format_exc()))
            finally:
                messages.put(('done', ok))

        threading.Thread(target=worker, daemon=True).start()

    build_btn.configure(command=start)
    pump()

    if not mods:
        log_write(f'No .iro files found in {MODS_DIR}')
        log_write('Drop your 7th Heaven mods there and restart.')
    else:
        log_write('Ready. Tick the mods you want, choose options, '
                  'then Build SD Output.')

    root.mainloop()


def main():
    if '--cli' in sys.argv:
        mods = discover_mods()
        saved = build.load_settings(SETTINGS)
        enabled = {m.filename: saved.get(m.filename, {}).get('enabled', True)
                   for m in mods}
        settings = {}
        for m in mods:
            m.ensure_extracted(print)
            settings[m.filename] = (saved.get(m.filename, {}).get('options')
                                    or (m.manifest.defaults()
                                        if m.manifest else {}))
        ok = run_build(mods, enabled, settings, print,
                       lambda *a: None)
        return 0 if ok else 1
    launch_ui()
    return 0


if __name__ == '__main__':
    sys.exit(main())