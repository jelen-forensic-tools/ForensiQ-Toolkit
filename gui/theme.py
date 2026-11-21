"""
gui/theme.py — unified look of the whole application (colours, fonts, sizes, components).

Rules:
  • Every panel has one toolbar (card) at the top and its content below.
  • The panel's main action = green button (primary), stop = red (danger),
    everything else = dark secondary button. Cyan (ACCENT) only for the active
    state, section titles and check boxes — never for buttons.
  • All toolbar buttons have the same height BTN_H.
  • Log on the left, module list on the right — always the same component.
"""
from __future__ import annotations

import datetime
from typing import Callable, Optional

import customtkinter as ctk

# ── Barvy ─────────────────────────────────────────────────────────────────────
ACCENT      = "#00d4ff"
BG_DARK     = "#0f0f1a"
BG_PANEL    = "#1a1a2e"
BG_CARD     = "#16213e"
BG_LEFT     = "#0f1a30"
BG_LOG      = "#0a0f1e"
BG_INPUT    = "#1e2742"
BORDER      = "#2a2a3e"
TEXT        = "#e6e6e6"
TEXT_MUTED  = "#888888"
TEXT_DIM    = "#555555"
WHITE       = "#ffffff"
GREEN       = "#00c853"
GREEN_DIM   = "#1a3d28"
RED         = "#ff5252"
RED_DIM     = "#3d1a1f"
ORANGE      = "#ff9800"
ORANGE_DIM  = "#3d2800"

BTN_PRIMARY       = "#1D6F42"
BTN_PRIMARY_HOVER = "#145230"
BTN_DANGER        = "#e53935"
BTN_DANGER_HOVER  = "#b71c1c"
BTN_SECONDARY       = "#2a2a4a"
BTN_SECONDARY_HOVER = "#3a3a5a"
BTN_DISABLED        = "#23233a"
TEXT_DIM_BTN        = "#6a6a80"

# ── Sizes ─────────────────────────────────────────────────────────────────────
RADIUS_CARD = 12
RADIUS_ITEM = 10
WRAP_CARD   = 230   # fixed text wrapping in module cards (no auto_wrap)
RADIUS_BTN  = 8
BTN_H       = 36      # all toolbar buttons
BTN_H_SMALL = 28      # small buttons inside cards (All / None, …)
PAD         = 16      # inner card padding
GAP         = 12      # gap between cards


# ── Fonts (lazy – CTkFont needs an existing root) ─────────────────────────────
def f_title():   return ctk.CTkFont(size=15, weight="bold")   # card title
def f_section(): return ctk.CTkFont(size=12, weight="bold")   # section title
def f_body():    return ctk.CTkFont(size=12)
def f_bold():    return ctk.CTkFont(size=13, weight="bold")
def f_small():   return ctk.CTkFont(size=11)
def f_mono():    return ctk.CTkFont(family="Consolas", size=11)


# ── Buttons ───────────────────────────────────────────────────────────────────
class StatefulButton(ctk.CTkButton):
    """CTkButton that greys out when disabled (coloured buttons would otherwise just look faded)."""

    def __init__(self, *a, **kw):
        self._active_fg = kw.get("fg_color")
        self._active_hover = kw.get("hover_color")
        super().__init__(*a, **kw)
        self._apply_state_colors(kw.get("state", "normal"))

    def _apply_state_colors(self, state):
        if state == "disabled":
            super().configure(fg_color=BTN_DISABLED)
        else:
            super().configure(fg_color=self._active_fg, hover_color=self._active_hover)

    def configure(self, require_redraw=False, **kw):
        if "fg_color" in kw:
            self._active_fg = kw["fg_color"]
        if "hover_color" in kw:
            self._active_hover = kw["hover_color"]
        state = kw.get("state")
        super().configure(require_redraw=require_redraw, **kw)
        if state is not None or "fg_color" in kw:
            self._apply_state_colors(state or self.cget("state"))


def _btn(parent, text, command, fg, hover, *, width=None, height=BTN_H,
         font=None, text_color=WHITE, state="normal", image=None, **kw):
    opts = dict(text=text, command=command, height=height,
                fg_color=fg, hover_color=hover, corner_radius=RADIUS_BTN,
                text_color=text_color, text_color_disabled=TEXT_DIM_BTN,
                font=font or f_body(), state=state)
    if width is not None:
        opts["width"] = width
    if image is not None:
        opts["image"] = image
    opts.update(kw)
    return StatefulButton(parent, **opts)


def primary_button(parent, text, command=None, width=150, **kw):
    """The panel's main action (Start, Analyze, Compare, Load…)."""
    kw.setdefault("font", f_bold())
    return _btn(parent, text, command, BTN_PRIMARY, BTN_PRIMARY_HOVER,
                width=width, **kw)


def danger_button(parent, text, command=None, width=100, **kw):
    """Stop / destructive action."""
    kw.setdefault("font", f_bold())
    return _btn(parent, text, command, BTN_DANGER, BTN_DANGER_HOVER,
                width=width, **kw)


def secondary_button(parent, text, command=None, width=130, **kw):
    """Secondary action (choose file, export, open folder…)."""
    return _btn(parent, text, command, BTN_SECONDARY, BTN_SECONDARY_HOVER,
                width=width, **kw)


def icon_button(parent, text, command=None, **kw):
    """Square icon button (↻, 📂) – same height as the others."""
    kw.setdefault("font", ctk.CTkFont(size=14))
    return _btn(parent, text, command, BTN_SECONDARY, BTN_SECONDARY_HOVER,
                width=BTN_H, **kw)


def small_button(parent, text, command=None, width=70, **kw):
    """Small button in list headers (All / None)."""
    kw.setdefault("font", f_small())
    return _btn(parent, text, command, BTN_SECONDARY, BTN_SECONDARY_HOVER,
                width=width, height=BTN_H_SMALL, **kw)


def set_running(start_btn, stop_btn, running: bool):
    """Uniform Start/Stop state switching."""
    if start_btn is not None:
        start_btn.configure(state="disabled" if running else "normal")
    if stop_btn is not None:
        stop_btn.configure(state="normal" if running else "disabled")


# ── Cards and labels ───────────────────────────────────────────────────────────
def card(parent, **kw):
    kw.setdefault("fg_color", BG_CARD)
    kw.setdefault("corner_radius", RADIUS_CARD)
    return ctk.CTkFrame(parent, **kw)


def section_label(parent, text, color=ACCENT):
    return ctk.CTkLabel(parent, text=text, font=f_section(),
                        text_color=color, anchor="w")


def field_label(parent, text):
    """Label of an input field in a toolbar (Device:, Host:, …)."""
    return ctk.CTkLabel(parent, text=text, font=f_body(),
                        text_color=TEXT_MUTED, anchor="w")


def hint_label(parent, text="", color=TEXT_MUTED, ref=None, offset: int = 0, **kw):
    """Grey informational text; wraps to the width of `ref` (default: parent) minus offset."""
    lbl = ctk.CTkLabel(parent, text=text, font=f_small(), text_color=color,
                       anchor="w", justify="left", wraplength=200, **kw)
    auto_wrap(lbl, ref if ref is not None else parent, offset)
    return lbl


def auto_wrap(label, ref, offset: int = 0):
    """
    Set a label's wraplength from the width of a reference widget (card / row),
    so text wraps instead of being cut off. `offset` = space taken in ref by
    other elements (icon, check box, padding) in logical px.
    """
    def _on_cfg(e, _l=label):
        try:
            scale = _l._get_widget_scaling()
        except Exception:
            scale = 1.0
        w = int(max(e.width / scale - offset, 80))
        try:
            if _l.cget("wraplength") != w:
                _l.configure(wraplength=w)
        except Exception:
            pass
    ref.bind("<Configure>", _on_cfg, add="+")


def separator(parent):
    return ctk.CTkFrame(parent, height=1, fg_color=BORDER)


def checkbox(parent, variable, text="", accent=ACCENT, **kw):
    """Check box – always the same size; without text it is only as wide as the box."""
    opts = dict(text=text, variable=variable, checkbox_width=22, checkbox_height=22,
                fg_color=accent, hover_color=accent, checkmark_color="#000",
                font=f_body())
    if not text:
        opts["width"] = 24
    opts.update(kw)
    return ctk.CTkCheckBox(parent, **opts)


# ── Toolbar ───────────────────────────────────────────────────────────────────
class Toolbar(ctk.CTkFrame):
    """
    Unified top bar of a panel – ALWAYS one row of fixed height + a thin bottom strip.

        ┌──────────────────────────────────────────────────────────────────┐
        │ [inputs / selection ……]   [status ……]       [ Primary ] [ Stop ] │
        │ ──────────────────────── progress (or an empty strip) ──────────── │
        └──────────────────────────────────────────────────────────────────┘

    • self.source  – left block (inputs, file selection, combo – may expand)
    • self.info    – middle block for status text (aligned right, next to actions)
    • self.actions – right block with action buttons (Primary/Stop)
    • self.progress – progress bar (only when progress=True; otherwise empty space
      of the same height so all toolbars are equally tall)

    A descriptive hint does NOT belong in the toolbar – it goes to the log as the first line.
    """

    def __init__(self, master, source: bool = True, progress: bool = False, **kw):
        # Note: `source` is kept for backward compatibility; source/info/actions
        # are always created. A descriptive hint belongs in the log, not the toolbar.
        super().__init__(master, fg_color=BG_CARD, corner_radius=RADIUS_CARD, **kw)
        self.grid_columnconfigure(0, weight=1)

        # ── Main row – a single row, height given by the buttons (BTN_H) ──
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD - 6, 0))
        main.grid_columnconfigure(0, weight=1)

        # width/height=1 → an empty frame does not take the default 200 px of CTkFrame;
        # packed children grow the frame (pack_propagate is on).
        self.source = ctk.CTkFrame(main, fg_color="transparent", width=1, height=1)
        self.source.grid(row=0, column=0, sticky="ew")
        self.info = ctk.CTkFrame(main, fg_color="transparent", width=1, height=1)
        self.info.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self.actions = ctk.CTkFrame(main, fg_color="transparent", width=1, height=1)
        self.actions.grid(row=0, column=2, sticky="e", padx=(8, 0))
        # Backward compatibility: older panels filled source_right → right, next to actions
        self.source_right = self.actions

        # ── Bottom strip – progress bar or empty space of the same height ──
        strip = ctk.CTkFrame(self, fg_color="transparent", height=6)
        strip.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(8, PAD - 6))
        strip.grid_propagate(False)
        strip.grid_columnconfigure(0, weight=1)
        self.progress = None
        if progress:
            self.progress = ctk.CTkProgressBar(strip, height=6, progress_color=ACCENT,
                                               fg_color=BORDER)
            self.progress.set(0)
            self.progress.grid(row=0, column=0, sticky="ew")


def pack_row(*widgets, gap: int = 8):
    """Pack widgets left to right with the same gap."""
    for i, w in enumerate(widgets):
        if w is None:
            continue
        w.pack(side="left", padx=(0 if i == 0 else gap, 0))


# ── Log ───────────────────────────────────────────────────────────────────────
class LogCard(ctk.CTkFrame):
    """Log card – same header, font and colour tags in every panel."""

    def __init__(self, master, title: str, **kw):
        super().__init__(master, fg_color=BG_CARD, corner_radius=RADIUS_CARD, **kw)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD - 4, 8))
        hdr.grid_columnconfigure(0, weight=1)
        self.title = section_label(hdr, title)
        self.title.grid(row=0, column=0, sticky="w")
        self.header_right = ctk.CTkFrame(hdr, fg_color="transparent", width=1, height=1)
        self.header_right.grid(row=0, column=1, sticky="e")

        separator(self).grid(row=1, column=0, sticky="ew", padx=PAD)

        self.box = ctk.CTkTextbox(self, fg_color=BG_LOG, text_color="#cccccc",
                                  font=f_mono(), corner_radius=8,
                                  state="disabled", wrap="word")
        self.box.grid(row=2, column=0, sticky="nsew", padx=10, pady=10)
        for tag, color in (("ok", GREEN), ("bold_ok", GREEN), ("err", RED),
                           ("info", ACCENT), ("warn", ORANGE), ("muted", TEXT_MUTED),
                           ("sep", "#2a3a5a"), ("separator", "#2a3a5a")):
            self.box.tag_config(tag, foreground=color)

    def write(self, text: str, tag: str = "", timestamp: bool = True):
        self.box.configure(state="normal")
        if timestamp:
            self.box.insert("end", datetime.datetime.now().strftime("[%H:%M:%S] "), "muted")
        self.box.insert("end", text + "\n", tag or ())
        self.box.configure(state="disabled")
        self.box.see("end")

    def clear(self):
        self.box.configure(state="normal")
        self.box.delete("1.0", "end")
        self.box.configure(state="disabled")


# ── Module list ───────────────────────────────────────────────────────────────
class ModuleList(ctk.CTkFrame):
    """
    Right column with module selection: header "Modules  [All] [None]" + cards.
    Cards always have the same layout: icon | name (+ description) | check box.
    """

    def __init__(self, master, title: str = "", all_text: str = "", none_text: str = "",
                 **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._vars: dict[str, ctk.BooleanVar] = {}
        self.title = self.btn_all = self.btn_none = None

        if title:
            hdr = ctk.CTkFrame(self, fg_color="transparent")
            hdr.grid(row=0, column=0, sticky="ew", padx=(4, 22), pady=(0, 6))
            hdr.grid_columnconfigure(0, weight=1)
            self.title = section_label(hdr, title)
            self.title.grid(row=0, column=0, sticky="w")
            if all_text:
                self.btn_all = small_button(hdr, all_text, lambda: self.set_all(True))
                self.btn_all.grid(row=0, column=1, padx=(0, 6))
                self.btn_none = small_button(hdr, none_text, lambda: self.set_all(False))
                self.btn_none.grid(row=0, column=2)

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=1, column=0, sticky="nsew")
        self.scroll.grid_columnconfigure(0, weight=1)
        self._row = 0

    def set_all(self, value: bool):
        for v in self._vars.values():
            v.set(value)

    def add_section(self, text: str, desc: str = "", color=ACCENT,
                    all_text: str = "", none_text: str = ""):
        """Section heading inside the list; optionally with All / None buttons for its modules."""
        f = ctk.CTkFrame(self.scroll, fg_color="transparent")
        f.grid(row=self._row, column=0, sticky="ew", padx=4,
               pady=(0 if self._row == 0 else 14, 6))
        f.grid_columnconfigure(0, weight=1)
        lbl = section_label(f, text, color=color)
        lbl.grid(row=0, column=0, sticky="w")
        if all_text:
            small_button(f, all_text, lambda: self.set_all(True)).grid(
                row=0, column=1, padx=(0, 6))
            small_button(f, none_text, lambda: self.set_all(False)).grid(row=0, column=2)
        if desc:
            hint_label(f, desc, ref=f, offset=8).grid(row=1, column=0, columnspan=3,
                                                      sticky="ew")
        self._row += 1
        f.label = lbl
        return f

    def next_row(self) -> int:
        """Return a row for a custom card placed in self.scroll."""
        r = self._row
        self._row += 1
        return r

    def add(self, key: str, label: str, var: ctk.BooleanVar, desc: str = "",
            image=None, emoji: str = "📁", accent: str = ACCENT):
        self._vars[key] = var
        c = ctk.CTkFrame(self.scroll, fg_color=BG_CARD, corner_radius=RADIUS_ITEM)
        c.grid(row=self._row, column=0, sticky="ew", padx=4, pady=3)
        c.grid_columnconfigure(1, weight=1)
        self._row += 1
        span = 2 if desc else 1
        pady = 10

        if image is not None:
            icon = ctk.CTkLabel(c, text="", image=image, width=36)
        else:
            icon = ctk.CTkLabel(c, text=emoji, font=ctk.CTkFont(size=20), width=36)
        icon.grid(row=0, column=0, rowspan=span, padx=(12, 8), pady=pady)

        # Fixed wraplength (not auto_wrap) → no <Configure> loop on the cards;
        # for panels with many modules this speeds up building and redrawing a lot.
        title = ctk.CTkLabel(c, text=label, font=f_bold(), anchor="w", justify="left",
                             wraplength=WRAP_CARD)
        title.grid(row=0, column=1, sticky="ew", pady=((pady, 0) if desc else pady))
        if desc:
            d = ctk.CTkLabel(c, text=desc, font=f_small(), text_color=TEXT_MUTED,
                             anchor="w", justify="left", wraplength=WRAP_CARD)
            d.grid(row=1, column=1, sticky="ew", pady=(0, pady))

        checkbox(c, var, accent=accent).grid(row=0, column=2, rowspan=span,
                                             padx=(8, 14))
        # Clicking anywhere on the card toggles the option
        def _toggle(_e=None, v=var):
            v.set(not v.get())
        for w in (c, icon):
            w.bind("<Button-1>", _toggle)
        return c
