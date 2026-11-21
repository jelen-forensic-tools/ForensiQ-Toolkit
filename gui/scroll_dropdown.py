"""
gui/scroll_dropdown.py — combobox-like selector whose popup shows a limited
number of rows with a scrollbar (CTkComboBox uses a tk Menu that always lists
every item at full height).
"""
from __future__ import annotations

import time
import tkinter as tk
from typing import Callable, Optional

import customtkinter as ctk

BG_FIELD = "#343638"
BG_POPUP = "#1e1e2e"
BORDER = "#565b5e"
HOVER = "#2a2a4a"


class ScrollDropdown(ctk.CTkFrame):
    def __init__(self, master, variable: Optional[ctk.StringVar] = None, values=None,
                 command: Optional[Callable[[str], None]] = None, visible_rows: int = 5,
                 row_height: int = 28, accent: str = "#00d4ff", state: str = "normal", **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._var = variable or ctk.StringVar(value="")
        self._values = list(values or [])
        self._command = command
        self._rows = visible_rows
        self._row_h = row_height
        self._accent = accent
        self._state = state
        self._popup: Optional[tk.Toplevel] = None
        self._closed_at = 0.0
        self.grid_columnconfigure(0, weight=1)
        self._btn = ctk.CTkButton(self, textvariable=self._var, anchor="w", height=36,
                                  fg_color=BG_FIELD, hover_color="#3d3f41", border_width=2,
                                  border_color=BORDER, text_color="#dce4ee",
                                  text_color_disabled="#8a8a8a", font=ctk.CTkFont(size=12),
                                  command=self._toggle)
        self._btn.grid(row=0, column=0, sticky="ew")
        self._arrow = ctk.CTkLabel(self, text="▼", width=20, fg_color=BG_FIELD, text_color="#aaaaaa",
                                   font=ctk.CTkFont(size=10))
        self._arrow.grid(row=0, column=0, sticky="e", padx=(0, 6))
        self._arrow.bind("<Button-1>", lambda e: self._toggle())
        self._apply_state()

    # ── public API (subset of CTkComboBox) ─────────────────────────────

    def configure(self, values=None, state=None, **kwargs):
        if values is not None:
            self._values = list(values)
        if state is not None:
            self._state = state
            self._apply_state()
        if kwargs:
            super().configure(**kwargs)

    def get(self) -> str:
        return self._var.get()

    def set(self, value: str):
        self._var.set(value)

    # ── internals ──────────────────────────────────────────────────────

    def _apply_state(self):
        enabled = self._state in ("normal", "readonly")
        self._btn.configure(state="normal" if enabled else "disabled")
        if not enabled:
            self._close()

    def _toggle(self):
        if self._popup is not None:
            self._close()
        elif time.monotonic() - self._closed_at < 0.3:
            pass                      # the click on the field itself just closed it via focus-out
        elif self._state in ("normal", "readonly") and self._values:
            self._open()

    def _open(self):
        scale = ctk.ScalingTracker.get_widget_scaling(self)
        x, y = self.winfo_rootx(), self.winfo_rooty() + self.winfo_height() + 2
        w_px = self.winfo_width()
        n = min(len(self._values), self._rows)
        h_units = n * (self._row_h + 2) + 10

        top = tk.Toplevel(self)
        top.overrideredirect(True)
        top.configure(bg=BORDER)
        top.attributes("-topmost", True)
        top.geometry(f"{w_px}x{int(h_units * scale) + 2}+{x}+{y}")
        self._popup = top

        frame = ctk.CTkScrollableFrame(top, fg_color=BG_POPUP, corner_radius=0,
                                       width=int(w_px / scale) - 24, height=h_units - 10,
                                       scrollbar_button_color="#3a3a6a",
                                       scrollbar_button_hover_color=self._accent)
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        frame.grid_columnconfigure(0, weight=1)
        cur = self._var.get()
        sel_row = 0
        for i, v in enumerate(self._values):
            is_cur = v == cur
            if is_cur:
                sel_row = i
            ctk.CTkButton(frame, text=v, anchor="w", height=self._row_h, corner_radius=4,
                          fg_color="#1e2a4a" if is_cur else "transparent",
                          text_color=self._accent if is_cur else "#dce4ee", hover_color=HOVER,
                          font=ctk.CTkFont(size=12),
                          command=lambda v=v: self._select(v)).grid(row=i, column=0, sticky="ew", pady=1)
        # scroll so the current item is visible
        if len(self._values) > n and sel_row >= n:
            top.after(30, lambda: frame._parent_canvas.yview_moveto(max(0, sel_row - n + 1) / len(self._values)))

        top.bind("<Escape>", lambda e: self._close())
        top.bind("<FocusOut>", self._on_focus_out)   # click anywhere else (or window move) closes it
        top.after(10, top.focus_force)

    def _on_focus_out(self, _e):
        # focus moving into a child of the popup (scrollbar, item) must not close it
        if self._popup is not None:
            self._popup.after(80, self._close_if_unfocused)

    def _close_if_unfocused(self):
        if self._popup is None:
            return
        f = self._popup.focus_get()
        if f is None or not str(f).startswith(str(self._popup)):
            self._close()

    def _select(self, v: str):
        self._var.set(v)
        self._close()
        if self._command:
            self._command(v)

    def _close(self):
        if self._popup is None:
            return
        top = self._popup
        self._popup = None
        self._closed_at = time.monotonic()
        try:
            top.destroy()
        except tk.TclError:
            pass
