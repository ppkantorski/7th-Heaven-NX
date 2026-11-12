"""nxpreview -- BUILD 912: the picture 7th Heaven shows for an option value,
in a small floating window beside an open option dropdown.

7th Heaven mods declare a preview per value (<Option PreviewFile="...">,
iro.Option.previews): 423 across the supported mods, PNG/JPG and a few
animated GIFs, up to 1280x896. Nothing is extracted to disk: an entry is read
straight from the .iro (or the folder mod) when first wanted, shrunk once and
kept in a small in-memory cache.

The popup belongs to the dropdown, not to the window: it appears next to the
open list, follows the entry under the pointer (or the one the arrow keys
reach), and goes away when the list closes. The window layout is untouched.

Tk notes. A ttk::combobox's list is its own toplevel, `<combo>.popdown`, with
a listbox `<popdown>.f.l` whose bindtags are `$lb ComboboxListbox`; the
instance bindings added here run before the class ones, so on <Motion> the
entry is found with `nearest y` rather than the selection. Images are read
and decoded on one worker thread; PhotoImages are made on the Tk thread.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import collections
import io
import queue
import threading

MAX_W, MAX_H = 420, 300          # thumbnail box
GIF_FRAMES = 90                  # most frames kept from an animated preview
CACHE = 48                       # decoded previews kept in memory
GAP = 8                          # popup <-> list distance (px)


def load_frames(mod_path, entry, max_w=MAX_W, max_h=MAX_H):
    """[(PIL.Image RGBA, ms)] for a preview inside a mod; [] if it is missing
    or unreadable. Runs on the worker thread (no Tk calls)."""
    from PIL import Image, ImageSequence
    import iro
    try:
        data = iro.read_one(mod_path, entry)
    except Exception:                                       # noqa: BLE001
        return []
    if not data:
        return []
    try:
        im = Image.open(io.BytesIO(data))
        frames = []
        for i, fr in enumerate(ImageSequence.Iterator(im)):
            if i >= GIF_FRAMES:
                break
            f = fr.convert('RGBA')
            f.thumbnail((max_w, max_h), Image.LANCZOS)
            frames.append((f, max(20, int(fr.info.get('duration', 100) or
                                          100))))
        return frames
    except Exception:                                       # noqa: BLE001
        return []


class OptionPreview:
    def __init__(self, root, colours):
        """colours() -> dict(bg=, fg=, muted=, border=) for the live theme."""
        self.root = root
        self.colours = colours
        self.win = None
        self._img = self._cap = None
        self._photos = []
        self._anim = None
        self._want = None                 # (mod_path, entry) shown now
        self._cache = collections.OrderedDict()
        self._jobs = queue.Queue()
        self._done = queue.Queue()
        self._pending = set()
        self._pd = None                   # the open list being previewed
        self._watch_id = None
        threading.Thread(target=self._worker, daemon=True).start()
        self._poll()

    # ---------------------------------------------------------- loading
    def _worker(self):
        while True:
            key = self._jobs.get()
            self._done.put((key, load_frames(*key)))

    def _poll(self):
        try:
            while True:
                key, frames = self._done.get_nowait()
                self._pending.discard(key)
                self._cache[key] = frames
                while len(self._cache) > CACHE:
                    self._cache.popitem(last=False)
                if key == self._want:
                    self._paint(frames)
        except queue.Empty:
            pass
        try:
            self.root.after(40, self._poll)
        except Exception:                                   # noqa: BLE001
            pass

    # ---------------------------------------------------------- the window
    def _ensure(self):
        import tkinter as tk
        if self.win is not None and self.win.winfo_exists():
            return
        c = self.colours()
        w = tk.Toplevel(self.root)
        w.withdraw()
        w.overrideredirect(True)
        try:
            w.attributes('-topmost', True)
        except tk.TclError:
            pass
        try:                    # macOS: a floating window that takes no focus
            w.tk.call('::tk::unsupported::MacWindowStyle', 'style', w._w,
                      'help', 'none')
        except tk.TclError:
            pass
        frame = tk.Frame(w, bg=c['bg'], highlightthickness=1,
                         highlightbackground=c['border'], bd=0)
        frame.pack(fill='both', expand=True)
        self._frame = frame
        self._img = tk.Label(frame, bg=c['bg'], bd=0, fg=c['muted'])
        self._img.pack(padx=8, pady=(8, 4))
        self._cap = tk.Label(frame, bg=c['bg'], fg=c['fg'], bd=0,
                             justify='left', wraplength=MAX_W)
        self._cap.pack(padx=8, pady=(0, 8), anchor='w')
        self.win = w

    def hide(self, *_a):
        self._want = None
        self._pd = None
        if self._watch_id is not None:
            try:
                self.root.after_cancel(self._watch_id)
            except Exception:                               # noqa: BLE001
                pass
            self._watch_id = None
        self._stop_anim()
        if self.win is not None:
            try:
                self.win.withdraw()
            except Exception:                               # noqa: BLE001
                pass

    def _stop_anim(self):
        if self._anim is not None:
            try:
                self.root.after_cancel(self._anim)
            except Exception:                               # noqa: BLE001
                pass
            self._anim = None

    def _paint(self, frames):
        from PIL import ImageTk
        self._stop_anim()
        if not frames:
            self._photos = []
            self._img.configure(image='', text='No preview', width=0,
                                height=0)
            return
        try:
            self._photos = [(ImageTk.PhotoImage(f), ms) for f, ms in frames]
        except Exception:                                   # noqa: BLE001
            self._photos = []           # no Tk bridge in Pillow: no picture
            self._img.configure(image='', text='No preview')
            return
        self._img.configure(image=self._photos[0][0], text='')
        if len(self._photos) > 1:
            def step(i=1):
                if not self._photos:
                    return
                i %= len(self._photos)
                self._img.configure(image=self._photos[i][0])
                self._anim = self.root.after(self._photos[i][1], step, i + 1)
            self._anim = self.root.after(self._photos[0][1], step)

    def _place(self, pd):
        """Beside the open list `pd` (a Tcl path: the popdown is created by
        ttk in Tcl, so tkinter has no widget object for it)."""
        w = self.win
        q = lambda *a: int(w.tk.call('winfo', *a))         # noqa: E731
        w.update_idletasks()
        pw, ph = w.winfo_reqwidth(), w.winfo_reqheight()
        x0, y0 = q('rootx', pd), q('rooty', pd)
        sw, sh = q('screenwidth', pd), q('screenheight', pd)
        x = x0 + q('width', pd) + GAP
        if x + pw > sw:                      # no room on the right: left side
            x = max(0, x0 - pw - GAP)
        y = min(max(0, y0), max(0, sh - ph))
        w.geometry('+%d+%d' % (x, y))

    def _open(self, pd):
        try:
            return bool(int(self.root.tk.call('winfo', 'ismapped', pd)))
        except Exception:                                   # noqa: BLE001
            return False

    def _watch(self):
        """Never left behind: while shown, hide the moment its list is no
        longer open, whatever closed it (a pick, Escape, a click elsewhere,
        another window, the dialog going away)."""
        self._watch_id = None
        if self._pd is None:
            return
        if not self._open(self._pd):
            self.hide()
            return
        self._watch_id = self.root.after(250, self._watch)

    def _restyle(self):
        c = self.colours()             # the live theme (it can change)
        self._frame.configure(bg=c['bg'], highlightbackground=c['border'])
        self._img.configure(bg=c['bg'], fg=c['muted'])
        self._cap.configure(bg=c['bg'], fg=c['fg'])

    def show(self, pd, mod_path, entry, caption):
        if not self._open(pd):
            return
        self._ensure()
        self._restyle()
        self._cap.configure(text=caption)
        key = (mod_path, entry) if entry else None
        self._want = key
        if key is None:
            self._paint([])
        elif key in self._cache:
            self._cache.move_to_end(key)
            self._paint(self._cache[key])
        else:
            self._stop_anim()
            self._photos = []
            self._img.configure(image='', text='Loading…')
            if key not in self._pending:
                self._pending.add(key)
                self._jobs.put(key)
        self._place(pd)
        self.win.deiconify()
        self.win.lift()
        if self._pd != pd or self._watch_id is None:
            self._pd = pd
            if self._watch_id is not None:
                self.root.after_cancel(self._watch_id)
            self._watch_id = self.root.after(250, self._watch)

    # ---------------------------------------------------------- hooking
    def attach(self, combo, mod_path, entries, captions):
        """Preview the open list of `combo`. entries[i] is the preview path
        of the list's i-th value (or None), captions[i] its caption."""
        if not any(entries):
            return False
        try:
            pd = str(combo.tk.call('ttk::combobox::PopdownWindow', combo._w))
        except Exception:                                   # noqa: BLE001
            return False
        lb = pd + '.f.l'
        last = [None]

        def at(i):
            if i is None or not (0 <= i < len(entries)):
                return
            if last[0] == i and self.win is not None and \
                    self.win.winfo_ismapped():
                return
            last[0] = i
            self.show(pd, mod_path, entries[i], captions[i])

        def on_motion(y):
            try:
                at(int(combo.tk.call(lb, 'nearest', int(y))))
            except Exception:                               # noqa: BLE001
                pass

        def on_select(*_a):
            try:
                sel = combo.tk.call(lb, 'curselection')
                if isinstance(sel, str):
                    sel = sel.split()
                at(int(sel[0]) if sel else None)
            except Exception:                               # noqa: BLE001
                pass

        def on_map(*_a):
            last[0] = None
            combo.after(30, on_select)

        def on_unmap(*_a):
            last[0] = None
            self.hide()

        reg = combo.register
        tk_ = combo.tk
        tk_.call('bind', lb, '<Motion>', '+%s %%y' % reg(on_motion))
        for seq in ('<<ListboxSelect>>', '<KeyRelease>'):
            tk_.call('bind', lb, seq, '+%s' % reg(on_select))
        tk_.call('bind', pd, '<Map>', '+%s' % reg(on_map))
        tk_.call('bind', pd, '<Unmap>', '+%s' % reg(on_unmap))
        combo.bind('<Destroy>', lambda e: self.hide()
                   if e.widget is combo else None, add='+')
        combo.bind('<<ComboboxSelected>>', lambda e: self.hide(), add='+')
        return True
