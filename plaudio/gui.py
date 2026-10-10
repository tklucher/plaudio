"""PlaudIO desktop app: a small window around `plaudio sync`.

Type your keys once, press the big red button, and every new Plaud recording
becomes a Google Doc (or, with the switch on, a Markdown file in a folder).
Run with `plaudio-gui`, `python -m plaudio.gui`, or the Windows PlaudIO.exe.
"""
from __future__ import annotations

import asyncio
import logging
import os
import queue
import subprocess
import sys
import threading
import traceback
import webbrowser
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .config import load_config

HELP_URL = "https://github.com/tklucher/plaudio#3-create-google-credentials"

RED = "#d32f2f"
RED_HOVER = "#e53935"
RED_PRESSED = "#a31515"
RED_RIM = "#7f1010"
GREY = "#b0b0b0"
GREY_RIM = "#8a8a8a"
BEZEL = "#3a3a3a"
SWITCH_OFF = "#c4c4c4"


def _fix_missing_stdio(log_path: Path):
    """A windowed .exe has no console, so sys.stdout/stderr are None and anything
    that prints would crash. Send them to a log file instead."""
    log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = log_file
    if sys.stderr is None:
        sys.stderr = log_file
    logging.basicConfig(stream=log_file, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return log_file


def _open_path(target: str) -> None:
    if target.startswith("http"):
        webbrowser.open(target)
    elif os.name == "nt":
        os.startfile(target)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


# --------------------------------------------------------------------- widgets
class BigRedButton(tk.Canvas):
    """A round, arcade-style red button drawn on a canvas."""

    def __init__(self, master, command, size: int = 170, text: str = "SYNC", bg: str = "#f3f3f3"):
        super().__init__(master, width=size, height=size, highlightthickness=0, bg=bg,
                         cursor="hand2")
        self.command, self.size, self.text = command, size, text
        self.enabled, self.hover, self.pressed = True, False, False
        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._set(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda e: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)
        self._draw()

    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        self._draw()

    def _release(self, event):
        was_pressed = self.pressed
        self._set(pressed=False)
        inside = 0 <= event.x <= self.size and 0 <= event.y <= self.size
        if self.enabled and was_pressed and inside:
            self.command()

    def set_enabled(self, enabled: bool, text: str | None = None):
        self.enabled = enabled
        if text:
            self.text = text
        self.configure(cursor="hand2" if enabled else "watch")
        self._draw()

    def _draw(self):
        self.delete("all")
        s, pad = self.size, 6
        self.create_oval(pad, pad, s - pad, s - pad, fill=BEZEL, outline="")        # bezel
        inset = 16 + (4 if self.pressed and self.enabled else 0)
        drop = 0 if (self.pressed and self.enabled) else 5
        if self.enabled:
            face = RED_PRESSED if self.pressed else (RED_HOVER if self.hover else RED)
            rim = RED_RIM
        else:
            face, rim = GREY, GREY_RIM
        # rim (the button's side), then its top face
        self.create_oval(inset, inset + drop, s - inset, s - inset + drop, fill=rim, outline="")
        self.create_oval(inset, inset, s - inset, s - inset - (0 if drop == 0 else 2),
                         fill=face, outline="")
        # soft highlight
        hl = inset + 14
        self.create_arc(hl, hl, s - hl, s - hl, start=60, extent=70, style="arc",
                        outline="#ffffff" if self.enabled else "#dddddd", width=3)
        font_size = 22 if len(self.text) <= 5 else 15
        self.create_text(s / 2, s / 2 - 2, text=self.text, fill="white",
                         font=("Segoe UI", font_size, "bold"))


class ToggleSwitch(tk.Canvas):
    """An on/off switch bound to a BooleanVar."""

    def __init__(self, master, variable: tk.BooleanVar, command=None, bg: str = "#f3f3f3"):
        super().__init__(master, width=52, height=28, highlightthickness=0, bg=bg, cursor="hand2")
        self.var, self.command = variable, command
        self.bind("<Button-1>", self._toggle)
        self.var.trace_add("write", lambda *a: self._draw())
        self._draw()

    def _toggle(self, _event=None):
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def _draw(self):
        self.delete("all")
        on = self.var.get()
        color = RED if on else SWITCH_OFF
        w, h, r = 52, 28, 14
        self.create_oval(0, 0, h, h, fill=color, outline="")
        self.create_oval(w - h, 0, w, h, fill=color, outline="")
        self.create_rectangle(r, 0, w - r, h, fill=color, outline="")
        x = w - h + 3 if on else 3
        self.create_oval(x, 3, x + h - 6, h - 3, fill="white", outline="")


# ------------------------------------------------------------------------ app
class PlaudIOApp:
    BG = "#f3f3f3"

    def __init__(self, root: tk.Tk, errlog=None):
        from .app import AppSettings

        self.root = root
        self.cfg = load_config()
        self.errlog = errlog
        self.settings = AppSettings.load(self.cfg.settings_path)
        self.events: queue.Queue = queue.Queue()
        self.running = False
        self.last_location = ""

        root.title("PlaudIO")
        root.configure(bg=self.BG)
        root.minsize(500, 640)
        self._style()
        self._build()
        self._load_into_form()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._drain_events)

    # ---- layout ---------------------------------------------------------
    def _style(self):
        st = ttk.Style(self.root)
        if "vista" in st.theme_names():
            st.theme_use("vista")
        elif "clam" in st.theme_names():
            st.theme_use("clam")
        st.configure(".", background=self.BG)
        st.configure("TFrame", background=self.BG)
        st.configure("TLabel", background=self.BG, font=("Segoe UI", 10))
        st.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        st.configure("Sub.TLabel", foreground="#666666", font=("Segoe UI", 9))
        st.configure("Section.TLabel", font=("Segoe UI", 10, "bold"))
        st.configure("Link.TLabel", foreground="#1a5fb4", font=("Segoe UI", 9, "underline"))
        st.configure("Status.TLabel", font=("Segoe UI", 10, "bold"))
        st.configure("TCheckbutton", background=self.BG)

    def _build(self):
        outer = ttk.Frame(self.root, padding=(18, 14, 18, 12))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)

        ttk.Label(outer, text="PlaudIO", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(outer, text="Turns new Plaud recordings into Google Docs or Markdown files.",
                  style="Sub.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 10))

        # Keys
        keys = ttk.Frame(outer)
        keys.grid(row=2, column=0, sticky="ew")
        keys.columnconfigure(1, weight=1)
        head = ttk.Frame(keys)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        ttk.Label(head, text="Keys", style="Section.TLabel").pack(side="left")
        link = ttk.Label(head, text="Where do I get these?", style="Link.TLabel", cursor="hand2")
        link.pack(side="right")
        link.bind("<Button-1>", lambda e: webbrowser.open(HELP_URL))

        self.v_cid = tk.StringVar()
        self.v_secret = tk.StringVar()
        self.v_anthropic = tk.StringVar()
        self.v_show = tk.BooleanVar(value=False)
        self.google_widgets = []
        self.secret_entries = []
        rows = [("Google Client ID", self.v_cid, False, True),
                ("Google Client Secret", self.v_secret, True, True),
                ("Anthropic API key (optional)", self.v_anthropic, True, False)]
        for i, (label, var, secret, google) in enumerate(rows, start=1):
            lab = ttk.Label(keys, text=label)
            lab.grid(row=i, column=0, sticky="w", padx=(0, 10), pady=3)
            ent = ttk.Entry(keys, textvariable=var, show="•" if secret else "")
            ent.grid(row=i, column=1, sticky="ew", pady=3, ipady=2)
            if secret:
                self.secret_entries.append(ent)
            if google:
                self.google_widgets.append(ent)
        ttk.Checkbutton(keys, text="Show keys", variable=self.v_show,
                        command=self._toggle_show).grid(row=4, column=1, sticky="w")

        ttk.Separator(outer).grid(row=3, column=0, sticky="ew", pady=10)

        # The switch
        sw = ttk.Frame(outer)
        sw.grid(row=4, column=0, sticky="ew")
        sw.columnconfigure(1, weight=1)
        self.v_markdown = tk.BooleanVar(value=False)
        ToggleSwitch(sw, self.v_markdown, command=self._mode_changed, bg=self.BG).grid(
            row=0, column=0, padx=(0, 10))
        self.mode_label = ttk.Label(sw, text="")
        self.mode_label.grid(row=0, column=1, sticky="w")

        self.folder_row = ttk.Frame(outer)
        self.folder_row.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        self.folder_row.columnconfigure(1, weight=1)
        self.v_dir = tk.StringVar()
        ttk.Label(self.folder_row, text="Save to").grid(row=0, column=0, padx=(0, 10))
        ttk.Entry(self.folder_row, textvariable=self.v_dir).grid(row=0, column=1, sticky="ew", ipady=2)
        ttk.Button(self.folder_row, text="Browse…", command=self._browse).grid(
            row=0, column=2, padx=(6, 0))

        # The big red button
        mid = ttk.Frame(outer)
        mid.grid(row=6, column=0, pady=(14, 4))
        self.button = BigRedButton(mid, command=self._start_sync, bg=self.BG)
        self.button.pack()
        self.status = ttk.Label(outer, text="Ready.", style="Status.TLabel", anchor="center")
        self.status.grid(row=7, column=0, sticky="ew", pady=(2, 6))

        small = ttk.Frame(outer)
        small.grid(row=8, column=0, pady=(0, 8))
        self.btn_plaud = ttk.Button(small, text="Sign in to Plaud", command=self._sign_in_plaud)
        self.btn_plaud.pack(side="left", padx=4)
        self.btn_open = ttk.Button(small, text="Open results", command=self._open_results,
                                   state="disabled")
        self.btn_open.pack(side="left", padx=4)

        # Log
        log_frame = ttk.Frame(outer)
        log_frame.grid(row=9, column=0, sticky="nsew")
        outer.rowconfigure(9, weight=1)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=9, wrap="word", font=("Consolas", 9),
                           bg="white", relief="solid", borderwidth=1, state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

    # ---- form <-> settings ---------------------------------------------
    def _load_into_form(self):
        s = self.settings
        self.v_cid.set(s.google_client_id)
        self.v_secret.set(s.google_client_secret)
        self.v_anthropic.set(s.anthropic_api_key)
        self.v_markdown.set(s.markdown_mode)
        self.v_dir.set(str(s.output_dir))
        self._mode_changed()

    def _read_form(self):
        s = self.settings
        s.google_client_id = self.v_cid.get().strip()
        s.google_client_secret = self.v_secret.get().strip()
        s.anthropic_api_key = self.v_anthropic.get().strip()
        s.markdown_mode = bool(self.v_markdown.get())
        s.markdown_dir = self.v_dir.get().strip()
        return s

    def _save(self):
        try:
            self._read_form().save(self.cfg.settings_path)
        except OSError as exc:
            self._log(f"Couldn't save settings: {exc}")

    def _toggle_show(self):
        for e in self.secret_entries:
            e.configure(show="" if self.v_show.get() else "•")

    def _mode_changed(self):
        md = self.v_markdown.get()
        self.mode_label.configure(text="Save as Markdown files (.md)" if md
                                  else "Save as Google Docs  (switch on for Markdown files)")
        if md:
            self.folder_row.grid()
        else:
            self.folder_row.grid_remove()
        for w in self.google_widgets:
            w.configure(state="disabled" if md else "normal")

    def _browse(self):
        start = self.v_dir.get() or str(Path.home())
        chosen = filedialog.askdirectory(initialdir=start if Path(start).exists() else str(Path.home()),
                                         title="Choose where to save Markdown files")
        if chosen:
            self.v_dir.set(chosen)

    # ---- running work in the background --------------------------------
    def _log(self, line: str):
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _echo(self, line: str):  # called from the worker thread
        self.events.put(("log", line))

    def _busy(self, busy: bool, text: str = ""):
        self.running = busy
        self.button.set_enabled(not busy, "…" if busy else "SYNC")
        self.btn_plaud.configure(state="disabled" if busy else "normal")
        if text:
            self.status.configure(text=text)

    def _run_in_background(self, coro_factory, done_kind: str):
        def worker():
            try:
                result = asyncio.run(coro_factory())
                self.events.put((done_kind, result))
            except BaseException as exc:  # noqa: BLE001 - report everything to the user
                logging.exception("background job failed")
                self.events.put(("error", exc))
        threading.Thread(target=worker, daemon=True).start()

    def _start_sync(self):
        if self.running:
            return
        from .app import run_job
        self._save()
        settings = self._read_form()
        self._log("─" * 40)
        self._busy(True, "Checking for new recordings…")
        self._run_in_background(lambda: run_job(self.cfg, settings, self._echo, self.errlog),
                                "sync_done")

    def _sign_in_plaud(self):
        if self.running:
            return
        from .app import sign_in_plaud
        self._log("─" * 40)
        self._busy(True, "Signing in to Plaud…")
        self._run_in_background(lambda: sign_in_plaud(self.cfg, self._echo, self.errlog),
                                "plaud_done")

    def _open_results(self):
        if self.last_location:
            try:
                _open_path(self.last_location)
            except OSError as exc:
                messagebox.showerror("PlaudIO", f"Couldn't open {self.last_location}:\n{exc}")

    def _drain_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "sync_done":
                    self._sync_finished(payload)
                elif kind == "plaud_done":
                    self._busy(False, "Signed in to Plaud.")
                elif kind == "error":
                    self._failed(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _sync_finished(self, result):
        rep = result.report
        what = "file" if result.markdown else "doc"
        n = len(rep.created)
        if rep.failed:
            text = f"{n} new {what}(s), {len(rep.failed)} failed. See details below."
        elif n:
            text = f"Done! {n} new {what}{'' if n == 1 else 's'} created."
        else:
            text = "All caught up. No new recordings."
        if rep.skipped_not_ready:
            text += f"  ({len(rep.skipped_not_ready)} still processing in Plaud)"
        if result.location:
            self.last_location = result.location
            self.btn_open.configure(state="normal",
                                    text="Open folder" if result.markdown else "Open Drive folder")
        self._busy(False, text)

    def _failed(self, exc: BaseException):
        from .app import SetupError
        from .lock import AlreadyRunning
        try:
            from .gdocs import GoogleSetupError
        except ImportError:  # pragma: no cover
            GoogleSetupError = SetupError  # type: ignore
        self._busy(False, "Stopped. See the message.")
        if isinstance(exc, (SetupError, AlreadyRunning, GoogleSetupError)):
            msg = str(exc)
        else:
            msg = f"Something went wrong:\n\n{exc}\n\nDetails were saved to {self.cfg.home / 'plaudio-gui.log'}"
            self._log("".join(traceback.format_exception_only(type(exc), exc)).strip())
        self._log(f"! {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}")
        messagebox.showerror("PlaudIO", msg)

    def _on_close(self):
        self._save()
        self.root.destroy()


def self_test(report_path: str) -> int:
    """Used by the build to check the frozen .exe has everything it needs.
    Imports every dependency, opens and closes the window, writes a report."""
    lines, code = [], 0
    try:
        import googleapiclient.discovery
        import google_auth_oauthlib.flow  # noqa: F401
        import mcp.client.stdio  # noqa: F401
        from . import app, gdocs, mdfiles, render, summarize, sync  # noqa: F401
        googleapiclient.discovery.build("drive", "v3", developerKey="self-test",
                                        cache_discovery=False, static_discovery=True)
        lines.append("imports + Drive discovery document: ok")
        root = tk.Tk()
        PlaudIOApp(root)
        root.update()
        root.destroy()
        lines.append("window: ok")
    except BaseException:
        code = 1
        lines.append(traceback.format_exc())
    Path(report_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return code


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "--self-test":
        sys.exit(self_test(sys.argv[2]))
    cfg = load_config()
    errlog = _fix_missing_stdio(cfg.home / "plaudio-gui.log")
    if os.name == "nt":
        try:  # crisp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    icon = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "plaudio.ico"
    if os.name == "nt" and icon.exists():
        try:
            root.iconbitmap(default=str(icon))
        except tk.TclError:
            pass
    PlaudIOApp(root, errlog=errlog)
    root.mainloop()


if __name__ == "__main__":
    main()
