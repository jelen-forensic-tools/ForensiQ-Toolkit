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
from .iconset import module_icon
from . import theme as th

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

# Mapování modulů na sdílené značkové ikony (assets/icons/<name>.png)
ICON_KEYS = {
    "ios_device_info": "device_info",
    "ios_apps":        "apps_installed",
    "ios_photos":      "photos",
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
        body.grid_columnconfigure(1, weight=2, minsize=340)
        body.grid_rowconfigure(0, weight=1)

        self._build_log(body)
        self._build_modules(body)

    def _build_topbar(self):
        tb = th.Toolbar(self, progress=True)
        tb.grid(row=0, column=0, sticky="ew", pady=(0, th.GAP))

        # Zdroj: výběr zařízení
        self._device_lbl = th.field_label(tb.source, t("ios.device_label"))
        self._device_lbl.pack(side="left", padx=(0, 8))
        self._device_var = ctk.StringVar(value=t("ios.no_device"))
        self._device_combo = ctk.CTkComboBox(
            tb.source, variable=self._device_var,
            values=[t("ios.no_device")], height=th.BTN_H,
            state="readonly", command=self._on_device_selected,
        )
        self._device_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))
        th.icon_button(tb.source, "↻", self._refresh_devices).pack(side="left")

        # Stav + akce
        self._lib_label = ctk.CTkLabel(tb.info, text="", font=th.f_small(),
                                       text_color=TEXT_MUTED, anchor="w")
        self._lib_label.pack(side="left")
        self._status_label = ctk.CTkLabel(tb.info, text="", text_color=TEXT_MUTED,
                                          font=th.f_body(), anchor="w")
        self._status_label.pack(side="left", padx=(16, 0))
        self._progress_label = ctk.CTkLabel(tb.info, text="", font=th.f_small(),
                                            text_color=TEXT_MUTED, anchor="w")
        self._progress_label.pack(side="left", padx=(12, 0))
        self._progress_bar = tb.progress

        self._start_btn = th.primary_button(tb.actions, t("ios.start"), self._on_start)
        self._stop_btn = th.danger_button(tb.actions, t("ext.stop"), self._on_stop,
                                          state="disabled")
        th.pack_row(self._start_btn, self._stop_btn)

    def _build_log(self, parent):
        self._log_card = th.LogCard(parent, t("ui.log"))
        self._log_card.grid(row=0, column=0, sticky="nsew", padx=(0, th.GAP))
        self._log_box = self._log_card.box
        self._eta_label = ctk.CTkLabel(self._log_card.header_right, text="",
                                       font=th.f_small(), text_color=TEXT_MUTED)
        self._eta_label.pack(side="right")

    def _build_modules(self, parent):
        self._modules = th.ModuleList(parent, t("ui.modules"), t("ui.all"), t("ui.none"))
        self._modules.grid(row=0, column=1, sticky="nsew")
        self._modules_scroll = self._modules.scroll
        self._select_all_btn = self._modules.btn_all
        self._deselect_all_btn = self._modules.btn_none
        self._rebuild_module_cards()

    def _rebuild_module_cards(self):
        for w in self._modules_scroll.winfo_children():
            w.destroy()
        self._modules._row = 0
        for key in IOS_MODULES:
            var = self._checkboxes.setdefault(key, ctk.BooleanVar(value=True))
            self._modules.add(key, t(f"ios.mod.{key}"), var, desc=t(f"ios.desc.{key}"),
                              image=module_icon(ICON_KEYS.get(key, key), size=30),
                              emoji=MODULE_ICONS.get(key, "📁"))

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
            self._progress_bar.set(0)
            self._log_clear()
        else:
            self._start_btn.configure(state="normal")
            self._stop_btn.configure(state="disabled")
            self._progress_label.configure(text="")

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
        self._select_all_btn.configure(text=t("ui.all"))
        self._deselect_all_btn.configure(text=t("ui.none"))
        self._modules.title.configure(text=t("ui.modules"))
        self._log_card.title.configure(text=t("ui.log"))
        self._device_lbl.configure(text=t("ios.device_label"))
        self._start_btn.configure(text=t("ios.start"))
        self._stop_btn.configure(text=t("ext.stop"))
        self._device_combo.configure(
            values=[t("ios.no_device")] if not self._devices else self._device_combo.cget("values")
        )
        self._rebuild_module_cards()
