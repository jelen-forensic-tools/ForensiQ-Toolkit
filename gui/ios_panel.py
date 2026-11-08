"""
gui/ios_panel.py — iOS live device extraction panel.
Requires pymobiledevice3 (pip install pymobiledevice3).
"""
from __future__ import annotations

import os
import datetime
import threading
from tkinter import filedialog
from typing import Callable, Optional

import customtkinter as ctk
from core.i18n import t
from core.ios_manager import iOSManager, has_pymobiledevice
from core.ios_extractor import iOSExtractor, IOS_MODULES

ACCENT     = "#00d4ff"
IOS_ACCENT = "#00d4ff"   # iOS blue
BG_CARD    = "#16213e"
BG_PANEL   = "#1a1a2e"
BG_LOG     = "#0a0f1e"
TEXT_MUTED = "#888888"
GREEN      = "#00c853"
RED        = "#ff5252"
ORANGE     = "#ff9800"
WHITE      = "#ffffff"

MODULE_ICONS = {
    "ios_device_info": "📱",
    "ios_apps":        "📦",
    "ios_photos":      "🖼",
}


class iOSPanel(ctk.CTkFrame):
    def __init__(self, master, accent: str = IOS_ACCENT, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.accent = accent
        self._manager = iOSManager()
        self._extractor: Optional[iOSExtractor] = None
        self._running = False
        self._devices: list[dict] = []
        self._selected_udid: Optional[str] = None
        self._checkboxes: dict[str, ctk.BooleanVar] = {}
        self._output_dir = ""
        self._extraction_start: Optional[datetime.datetime] = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()
        self._check_library()
        self._start_polling()

    # ── UI construction ────────────────────────────────────────────────────

    def _build(self):
        self._build_topbar()

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3, minsize=380)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        self._build_log(body)
        self._build_modules(body)

    def _build_topbar(self):
        top = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        top.grid_columnconfigure(1, weight=1)

        # Row 0 – library status + device picker
        row0 = ctk.CTkFrame(top, fg_color="transparent")
        row0.grid(row=0, column=0, columnspan=3, padx=16, pady=(14, 4), sticky="ew")
        row0.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            row0, text=t("ios.device_label"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_MUTED,
        ).grid(row=0, column=0, padx=(0, 8))

        self._device_var = ctk.StringVar(value=t("ios.no_device"))
        self._device_combo = ctk.CTkComboBox(
            row0, variable=self._device_var,
            values=[t("ios.no_device")],
            width=320, state="readonly",
            command=self._on_device_selected,
        )
        self._device_combo.grid(row=0, column=1, padx=(0, 8), sticky="ew")

        ctk.CTkButton(
            row0, text="↺", width=36, height=30,
            fg_color="#2a2a4a", hover_color="#3a3a5a",
            command=self._refresh_devices,
        ).grid(row=0, column=2)

        # Row 1 – library status
        self._lib_label = ctk.CTkLabel(
            top, text="",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
            anchor="w",
        )
        self._lib_label.grid(row=1, column=0, columnspan=3,
                             padx=16, pady=(0, 4), sticky="ew")

        # Row 2 – buttons
        btn_row = ctk.CTkFrame(top, fg_color="transparent")
        btn_row.grid(row=2, column=0, columnspan=3, padx=12, pady=(4, 12), sticky="ew")

        self._select_all_btn = ctk.CTkButton(
            btn_row, text=t("ext.select_all"), width=110, height=32,
            fg_color="#2a2a4a", hover_color="#3a3a5a",
            font=ctk.CTkFont(size=12),
            command=self._select_all,
        )
        self._select_all_btn.pack(side="left", padx=(0, 6))

        self._deselect_all_btn = ctk.CTkButton(
            btn_row, text=t("ext.deselect_all"), width=110, height=32,
            fg_color="#2a2a4a", hover_color="#3a3a5a",
            font=ctk.CTkFont(size=12),
            command=self._deselect_all,
        )
        self._deselect_all_btn.pack(side="left")

        self._status_label = ctk.CTkLabel(
            btn_row, text="", text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=12),
        )
        self._status_label.pack(side="left", padx=(16, 0))

        self._stop_btn = ctk.CTkButton(
            btn_row, text=t("ext.stop"),
            width=90, height=36,
            fg_color="#f44336", text_color="#fff",
            font=ctk.CTkFont(size=13),
            command=self._on_stop,
            state="disabled",
        )
        self._stop_btn.pack(side="right", padx=(4, 0))

        self._start_btn = ctk.CTkButton(
            btn_row, text=t("ios.start"),
            width=160, height=36,
            fg_color=self.accent, text_color="#000",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._on_start,
        )
        self._start_btn.pack(side="right")

        # Progress bar (hidden until running)
        self._progress_frame = ctk.CTkFrame(top, fg_color="transparent")
        self._progress_bar = ctk.CTkProgressBar(
            self._progress_frame, width=300, height=8, progress_color=self.accent,
        )
        self._progress_bar.set(0)
        self._progress_label = ctk.CTkLabel(
            self._progress_frame, text="",
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
        )

    def _build_log(self, parent):
        log_card = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12)
        log_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        log_card.grid_columnconfigure(0, weight=1)
        log_card.grid_rowconfigure(2, weight=1)

        hdr = ctk.CTkFrame(log_card, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        hdr.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            hdr, text=t("ext.log_title"),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=self.accent,
        ).grid(row=0, column=0, sticky="w")

        self._eta_label = ctk.CTkLabel(
            hdr, text="", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
        )
        self._eta_label.grid(row=0, column=1, sticky="e")

        ctk.CTkFrame(log_card, height=1, fg_color="#2a2a3e").grid(
            row=1, column=0, sticky="ew", padx=14,
        )

        self._log_box = ctk.CTkTextbox(
            log_card,
            fg_color=BG_LOG, text_color="#cccccc",
            font=ctk.CTkFont(family="Consolas", size=11),
            corner_radius=8, state="disabled", wrap="word",
        )
        self._log_box.grid(row=2, column=0, sticky="nsew", padx=10, pady=(6, 10))
        self._log_box.tag_config("ok",    foreground=GREEN)
        self._log_box.tag_config("err",   foreground=RED)
        self._log_box.tag_config("info",  foreground=self.accent)
        self._log_box.tag_config("warn",  foreground=ORANGE)
        self._log_box.tag_config("muted", foreground=TEXT_MUTED)
        self._log_box.tag_config("sep",   foreground="#2a3a5a")

    def _build_modules(self, parent):
        right = ctk.CTkFrame(parent, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(right, fg_color="transparent")
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)
        self._modules_scroll = scroll
        self._rebuild_module_cards()

    def _rebuild_module_cards(self):
        for w in self._modules_scroll.winfo_children():
            w.destroy()
        for i, (key, _) in enumerate(IOS_MODULES.items()):
            self._build_card(self._modules_scroll, key, row=i)

    def _build_card(self, parent, key: str, row: int):
        if key not in self._checkboxes:
            var = ctk.BooleanVar(value=True)
            self._checkboxes[key] = var
        else:
            var = self._checkboxes[key]

        card = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=10)
        card.grid(row=row, column=0, sticky="ew", padx=5, pady=5)
        card.grid_columnconfigure(1, weight=1)

        icon = MODULE_ICONS.get(key, "📁")
        ctk.CTkLabel(card, text=icon, font=ctk.CTkFont(size=24), width=45).grid(
            row=0, column=0, rowspan=2, padx=(15, 5), pady=15,
        )
        ctk.CTkLabel(
            card, text=t(f"ios.mod.{key}"),
            font=ctk.CTkFont(size=13, weight="bold"), anchor="w",
        ).grid(row=0, column=1, padx=5, pady=(12, 1), sticky="w")
        ctk.CTkLabel(
            card, text=t(f"ios.desc.{key}"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
            anchor="w", wraplength=260,
        ).grid(row=1, column=1, padx=5, pady=(0, 12), sticky="w")
        ctk.CTkCheckBox(
            card, text="", variable=var, width=24, height=24,
            checkbox_width=22, checkbox_height=22,
            checkmark_color="#000", fg_color=self.accent,
        ).grid(row=0, column=2, rowspan=2, padx=15)

    # ── Device polling ─────────────────────────────────────────────────────

    def _start_polling(self):
        def _poll():
            self._refresh_devices()
            self.after(6000, _poll)
        self.after(1000, _poll)

    def _refresh_devices(self):
        def _worker():
            devices = self._manager.get_devices()
            self.after(0, lambda d=devices: self._update_device_list(d))
        threading.Thread(target=_worker, daemon=True).start()

    def _update_device_list(self, devices: list[dict]):
        self._devices = devices
        if not devices:
            self._device_combo.configure(values=[t("ios.no_device")])
            self._device_var.set(t("ios.no_device"))
            self._selected_udid = None
            return
        labels = [f"{d['name']} ({d['udid'][:8]}…)" for d in devices]
        self._device_combo.configure(values=labels)
        if self._device_var.get() not in labels:
            self._device_var.set(labels[0])
            self._selected_udid = devices[0]["udid"]
            self._manager.connect_device(self._selected_udid)

    def _on_device_selected(self, selection: str):
        for d in self._devices:
            if selection.startswith(d["name"]):
                self._selected_udid = d["udid"]
                self._manager.connect_device(self._selected_udid)
                break

    # ── Library check ──────────────────────────────────────────────────────

    def _check_library(self):
        ok, msg = self._manager.check_library()
        if ok:
            self._lib_label.configure(
                text=f"● {msg}",
                text_color=GREEN,
            )
        else:
            self._lib_label.configure(
                text=f"● {t('ios.no_lib')}  |  pip install pymobiledevice3",
                text_color=ORANGE,
            )

    # ── Controls ───────────────────────────────────────────────────────────

    def _select_all(self):
        for v in self._checkboxes.values():
            v.set(True)

    def _deselect_all(self):
        for v in self._checkboxes.values():
            v.set(False)

    def _on_start(self):
        if not has_pymobiledevice():
            self._log(t("ios.err_no_lib"), "warn")
            return
        if not self._selected_udid:
            self._log(t("ios.err_no_device"), "warn")
            return
        selected = [k for k, v in self._checkboxes.items() if v.get()]
        if not selected:
            return

        base_dir = filedialog.askdirectory(
            title=t("ext.folder_title"),
            initialdir=os.path.expanduser("~"),
        )
        if not base_dir:
            return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._output_dir = os.path.join(base_dir, f"iOS_{ts}")
        os.makedirs(self._output_dir, exist_ok=True)

        self._extractor = iOSExtractor(self._manager, self._output_dir)
        self._set_running(True)
        self._log_raw("─" * 48, "sep")
        self._log(t("ios.log_started"), "info")
        self._log(f"{t('ext.log_modules')} {len(selected)}", "muted")
        self._log_raw("─" * 48, "sep")

        self._extractor.extract_all(
            selected,
            on_progress=lambda lbl, i, n: self.after(
                0, lambda l=lbl, c=i, t=n: self._on_progress(l, c, t)
            ),
            on_done=lambda results: self.after(
                0, lambda r=results: self._on_done(r)
            ),
            on_module_done=lambda res, i, n, el, av: self.after(
                0, lambda r=res, c=i, t=n, e=el, a=av: self._on_module_done(r, c, t, e, a)
            ),
            on_log=lambda msg: self.after(
                0, lambda m=msg: self._log(m, "muted")
            ),
        )

    def _on_stop(self):
        if self._extractor:
            self._extractor.stop()
        self._log(t("ios.log_stopping"), "warn")
        self._status_label.configure(text=t("ios.stopping"), text_color=ORANGE)

    # ── Callbacks ──────────────────────────────────────────────────────────

    def _on_progress(self, label: str, current: int, total: int):
        progress = current / total if total else 0
        self._progress_bar.set(progress)
        self._progress_label.configure(text=f"[{current}/{total}] {label}")
        self._log_raw("", "")
        self._log(f"▶  [{current}/{total}]  {label}", "info")

    def _on_module_done(self, result, current: int, total: int,
                        elapsed: float, avg: float):
        el_str = f"{elapsed:.2f}s"
        if result.success:
            detail = f"{result.count} {t('ext.records')}" if result.count else t("ext.log_no_data")
            self._log(f"✓  {result.label}", "ok")
            self._log(f"{t('ext.log_result')} {detail}  |  {t('ext.log_duration')} {el_str}", "muted")
        else:
            self._log(f"✗  {result.label}", "err")
            self._log(f"{t('ext.log_error_lbl')} {result.error}", "err")
        remaining = total - current
        if remaining > 0:
            eta_sec = int(avg * remaining)
            eta_str = f"{eta_sec // 60}m {eta_sec % 60}s" if eta_sec >= 60 else f"{eta_sec}s"
            self._eta_label.configure(
                text=t("ext.eta").format(eta=eta_str, n=remaining)
            )
        else:
            self._eta_label.configure(text="")

    def _on_done(self, results: list):
        success_count = sum(1 for r in results if r.success)
        self._set_running(False)
        self._log_raw("", "")
        self._log_raw("─" * 48, "sep")
        self._log(t("ext.done").format(n=success_count), "ok")
        self._log_raw("─" * 48, "sep")
        self._status_label.configure(
            text=t("ext.done").format(n=success_count), text_color=GREEN
        )
        if self._output_dir and os.path.exists(self._output_dir):
            os.startfile(self._output_dir)

    # ── Running state ──────────────────────────────────────────────────────

    def _set_running(self, running: bool):
        self._running = running
        if running:
            self._extraction_start = datetime.datetime.now()
            self._start_btn.configure(state="disabled")
            self._stop_btn.configure(state="normal")
            self._status_label.configure(text=t("ext.running"), text_color=TEXT_MUTED)
            self._progress_frame.grid(
                row=3, column=0, columnspan=3, padx=20, pady=(0, 10), sticky="ew",
            )
            self._progress_bar.pack(side="left", padx=(0, 10))
            self._progress_label.pack(side="left")
            self._progress_bar.set(0)
            self._log_clear()
        else:
            self._start_btn.configure(state="normal")
            self._stop_btn.configure(state="disabled")
            self._progress_frame.grid_remove()

    # ── Log helpers ────────────────────────────────────────────────────────

    def _log(self, text: str, tag: str = ""):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._log_box.configure(state="normal")
        self._log_box.insert("end", f"[{ts}] ", "muted")
        if tag:
            self._log_box.insert("end", text + "\n", tag)
        else:
            self._log_box.insert("end", text + "\n")
        self._log_box.configure(state="disabled")
        self._log_box.see("end")

    def _log_raw(self, text: str, tag: str = ""):
        self._log_box.configure(state="normal")
        if tag:
            self._log_box.insert("end", text + "\n", tag)
        else:
            self._log_box.insert("end", text + "\n")
        self._log_box.configure(state="disabled")
        self._log_box.see("end")

    def _log_clear(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")
        self._eta_label.configure(text="")

    # ── Language refresh ───────────────────────────────────────────────────

    def refresh_lang(self):
        self._select_all_btn.configure(text=t("ext.select_all"))
        self._deselect_all_btn.configure(text=t("ext.deselect_all"))
        self._start_btn.configure(text=t("ios.start"))
        self._stop_btn.configure(text=t("ext.stop"))
        self._device_combo.configure(
            values=[t("ios.no_device")] if not self._devices else self._device_combo.cget("values")
        )
        self._rebuild_module_cards()
