"""
gui/android_dump_panel.py — Android physical dump (eMMC/UFS/userdata image) analysis panel.
Results are handed over to the main window → Results panel, reports, Timeline, Map.
"""
from __future__ import annotations

import datetime
import os
import threading
from tkinter import filedialog
from typing import Callable, Optional

import customtkinter as ctk
from core.i18n import t
from core.android_dump import ADUMP_MODULES, IMAGE_FILETYPES, AndroidDumpAnalyzer
from .iconset import module_icon
from . import theme as th
from .scroll_dropdown import ScrollDropdown

ACCENT     = "#00d4ff"
BG_CARD    = "#16213e"
BG_LOG     = "#0a0f1e"
TEXT_MUTED = "#888888"
GREEN      = "#00c853"
RED        = "#ff5252"
ORANGE     = "#ff9800"
WHITE      = "#ffffff"

ICONS = {  # module → (icon file key, emoji fallback)
    "device_info": ("device_info", "📱"), "contacts": ("contacts", "👥"), "sms": ("sms", "💬"),
    "call_logs": ("call_logs", "📞"), "whatsapp": ("whatsapp", "💚"), "browser": ("browser", "🌐"),
    "wifi": ("wifi", "📶"), "bluetooth": ("bluetooth", "🔵"), "accounts": ("accounts", "👤"),
    "apps": ("apps", "📦"), "media": ("media", "🖼"),
}
DEFAULT_OFF = {"media"}      # copying the whole internal storage can take long


class AndroidDumpPanel(ctk.CTkFrame):
    def __init__(self, master, accent: str = ACCENT,
                 on_finished: Optional[Callable] = None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.accent = accent
        self._on_finished = on_finished
        self._image_path = ""
        self._output_dir = ""
        self._analyzer: Optional[AndroidDumpAnalyzer] = None
        self._parts: list = []
        self._running = False
        self._mod_vars = {k: ctk.BooleanVar(value=k not in DEFAULT_OFF) for k in ADUMP_MODULES}
        self._hash_var = ctk.BooleanVar(value=True)
        self._xlsx_var = ctk.BooleanVar(value=True)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()

    # ── UI ─────────────────────────────────────────────────────────────

    def _btn(self, parent, text, cmd, width=150):
        return ctk.CTkButton(parent, text=text, font=ctk.CTkFont(size=12), fg_color="#2a2a4a",
                             hover_color="#3a3a5a", height=30, width=width, command=cmd)

    def _build(self):
        tb = th.Toolbar(self, progress=True)
        tb.grid(row=0, column=0, sticky="ew", pady=(0, th.GAP))

        # Zdroj: obraz + výstup + oddíl
        self._btn_pick = th.secondary_button(tb.source, t("adump.pick_image"), self._pick_image,
                                             width=150)
        th.pack_row(
            self._btn_pick,
            th.secondary_button(tb.source, t("ui.output"), self._pick_output, width=150),
        )
        th.field_label(tb.source, t("adump.partition")).pack(side="left", padx=(14, 6))
        self._part_var = ctk.StringVar(value="")
        self._part_combo = ScrollDropdown(tb.source, variable=self._part_var, values=[],
                                          visible_rows=5, accent=self.accent, state="disabled")
        self._part_combo._btn.configure(width=220)
        self._part_combo.pack(side="left")

        # Info: cesta / výstup / stav
        self._path_label = ctk.CTkLabel(tb.info, text=t("adump.no_image"), font=th.f_small(),
                                        text_color=TEXT_MUTED, anchor="w")
        self._path_label.pack(side="left")
        self._output_label = ctk.CTkLabel(tb.info, text="", font=th.f_small(),
                                          text_color=TEXT_MUTED, anchor="w")
        self._output_label.pack(side="left", padx=(12, 0))
        self._status_bar = ctk.CTkLabel(tb.info, text="", font=th.f_body(),
                                        text_color=TEXT_MUTED, anchor="w")
        self._status_bar.pack(side="left", padx=(16, 0))

        self._btn_run = th.primary_button(tb.actions, t("adump.run"), self._run, state="disabled")
        self._btn_stop = th.danger_button(tb.actions, t("ext.stop"), self._stop, state="disabled")
        th.pack_row(self._btn_run, self._btn_stop)
        self._progress = tb.progress

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3, minsize=380)
        body.grid_columnconfigure(1, weight=2, minsize=340)
        body.grid_rowconfigure(0, weight=1)
        self._build_log(body)
        self._build_config(body)

    def _build_log(self, parent):
        self._log_card = th.LogCard(parent, t("ui.log"))
        self._log_card.grid(row=0, column=0, sticky="nsew", padx=(0, th.GAP))
        self._log_box = self._log_card.box
        self._log_card.write(t("adump.hint"), "muted", timestamp=False)

    def _build_config(self, parent):
        ml = th.ModuleList(parent)
        ml.grid(row=0, column=1, sticky="nsew")
        self._modules = ml
        right = ml.scroll

        ml.add_section(t("ui.modules"), all_text=t("ui.all"), none_text=t("ui.none"))
        for key in ADUMP_MODULES:
            icon_key, emoji = ICONS.get(key, (key, "📁"))
            ml.add(key, t(f"adump.mod.{key}"), self._mod_vars[key],
                   image=module_icon(icon_key, size=30), emoji=emoji)

        ml.add_section(t("adump.sec_options"))
        opt = ctk.CTkFrame(right, fg_color=BG_CARD, corner_radius=th.RADIUS_ITEM)
        opt.grid(row=ml.next_row(), column=0, sticky="ew", padx=4, pady=3)
        opts = ((self._hash_var, "adump.opt_hash"), (self._xlsx_var, "adump.opt_xlsx"))
        for i, (var, key) in enumerate(opts):
            th.checkbox(opt, var, text=t(key)).grid(
                row=i, column=0, sticky="w", padx=14,
                pady=(12 if i == 0 else 4, 12 if i == len(opts) - 1 else 4))

        lim = th.hint_label(right, t("adump.limits"), ref=right, offset=30)
        lim.grid(row=ml.next_row(), column=0, sticky="ew", padx=8, pady=(10, 12))

    # ── pickers / opening ──────────────────────────────────────────────

    def _pick_image(self):
        if self._running:
            return
        path = filedialog.askopenfilename(title=t("adump.pick_title"), filetypes=IMAGE_FILETYPES)
        if not path:
            return
        self._image_path = path
        self._path_label.configure(text=path if len(path) <= 70 else "…" + path[-67:], text_color=WHITE)
        if not self._output_dir:
            self._output_dir = os.path.join(os.path.expanduser("~"), "Documents", "ForenziQ_AndroidDump")
            self._output_label.configure(text=self._output_dir, text_color=TEXT_MUTED)
        self._open_image()

    def _pick_output(self):
        path = filedialog.askdirectory(title=t("bkp.pick_output_title"))
        if path:
            self._output_dir = path
            self._output_label.configure(text=path, text_color=WHITE)

    def _open_image(self):
        if self._analyzer:
            self._analyzer.close()
        self._analyzer = AndroidDumpAnalyzer(self._image_path)
        self._parts = []
        self._btn_run.configure(state="disabled")
        self._btn_pick.configure(state="disabled")
        self._part_combo.configure(state="disabled", values=[t("adump.loading")])
        self._part_var.set(t("adump.loading"))
        self._log_clear()
        an = self._analyzer

        def _worker():
            try:
                parts = an.open(log=lambda m: self.after(0, lambda m=m: self._log(m, "muted")),
                                on_scan_progress=lambda f: self.after(0, lambda f=f: self._progress.set(f)))
                err = ""
            except Exception as e:
                parts, err = [], f"{type(e).__name__}: {e}"
            self.after(0, lambda: self._on_opened(an, parts, err))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_opened(self, an, parts, err):
        if an is not self._analyzer:
            return
        self._btn_pick.configure(state="normal")
        self._progress.set(0)
        if err:
            self._log(f"✗  {err}", "err")
            self._part_var.set("")
            return
        self._parts = parts
        self._log(t("adump.log_layout").format(layout=an.layout, n=len(parts)), "info")
        for p in parts:
            self._log(f"   {p.label}", "muted")
        if not parts:
            self._part_combo.configure(values=[t("adump.no_partitions")])
            self._part_var.set(t("adump.no_partitions"))
            return
        labels = [p.label for p in parts]
        self._part_combo.configure(values=labels, state="readonly")
        default = an.default_partition()
        self._part_var.set(default.label if default else labels[0])
        self._btn_run.configure(state="normal")

    # ── run ────────────────────────────────────────────────────────────

    def _selected_partition(self):
        lbl = self._part_var.get()
        return next((p for p in self._parts if p.label == lbl), None)

    def _run(self):
        if self._running or not self._analyzer:
            return
        if not self._image_path:
            self._status_bar.configure(text=t("adump.err_no_image"), text_color=RED)
            return
        part = self._selected_partition()
        if part is None:
            self._status_bar.configure(text=t("adump.err_no_partition"), text_color=RED)
            return
        mods = [k for k in ADUMP_MODULES if self._mod_vars[k].get()]
        if not mods:
            self._status_bar.configure(text=t("adump.err_no_modules"), text_color=RED)
            return
        self._running = True
        self._analyzer._stop.clear()
        self._btn_run.configure(state="disabled", text=t("ext.running"))
        self._btn_stop.configure(state="normal")
        self._btn_pick.configure(state="disabled")
        self._part_combo.configure(state="disabled")
        self._status_bar.configure(text=t("ext.running"), text_color=self.accent)
        self._progress.set(0)
        self._log_raw("─" * 48, "sep")
        self._log(t("adump.log_started"), "info")
        self._log(f"{os.path.basename(self._image_path)}  →  {part.label}", "muted")
        self._log_raw("─" * 48, "sep")
        a = self.after
        self._analyzer.run(
            part, self._output_dir, mods, hash_image=self._hash_var.get(), xlsx=self._xlsx_var.get(),
            on_log=lambda m: a(0, lambda m=m: self._log(m, "warn" if m.startswith("⚠") else "muted")),
            on_progress=lambda lbl, i, n: a(0, lambda l=lbl, i=i, n=n: self._on_progress(l, i, n)),
            on_module_done=lambda r, i, n, el: a(0, lambda r=r, el=el: self._on_module_done(r, el)),
            on_done=lambda res, err, info, tg: a(0, lambda: self._on_done(res, err, info, tg)),
        )

    def _stop(self):
        if self._analyzer:
            self._analyzer.stop()
        self._btn_stop.configure(state="disabled")
        self._status_bar.configure(text=t("ios_bkp.stopping") + "…", text_color=ORANGE)

    def _on_progress(self, label, i, n):
        self._progress.set((i - 1) / max(n, 1))
        self._log_raw("", "")
        self._log(f"▶  [{i}/{n}]  {label}", "info")

    def _on_module_done(self, r, elapsed):
        if r.success:
            self._log(f"✓  {r.label}", "ok")
            self._log(f"{t('ext.log_result')} {r.count} {t('ext.records')}  |  "
                      f"{t('ext.log_duration')} {elapsed:.2f}s", "muted")
        else:
            self._log(f"✗  {r.label}", "err")
            self._log(f"{t('ext.log_error_lbl')} {r.error}", "err")

    def _on_done(self, results, err, info, target):
        self._running = False
        self._btn_run.configure(state="normal", text=t("adump.run"))
        self._btn_stop.configure(state="disabled")
        self._btn_pick.configure(state="normal")
        self._part_combo.configure(state="readonly")
        self._progress.set(1)
        if err:
            self._log(f"✗  {err}", "err")
            self._status_bar.configure(text=t("adump.failed_run"), text_color=RED)
            if not results:
                return
        ok = sum(1 for r in results if r.success)
        self._log_raw("─" * 48, "sep")
        self._log(t("ext.done").format(n=ok), "ok")
        self._log(target, "muted")
        if not err:
            self._status_bar.configure(text="✔ " + t("adump.done"), text_color=GREEN)
        if self._on_finished and results:
            self._on_finished(results, info, target)

    # ── log helpers ────────────────────────────────────────────────────

    def _log(self, text: str, tag: str = ""):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._log_box.configure(state="normal")
        self._log_box.insert("end", f"[{ts}] ", "muted")
        self._log_box.insert("end", text + "\n", tag) if tag else self._log_box.insert("end", text + "\n")
        self._log_box.configure(state="disabled")
        self._log_box.see("end")

    def _log_raw(self, text: str, tag: str = ""):
        self._log_box.configure(state="normal")
        self._log_box.insert("end", text + "\n", tag) if tag else self._log_box.insert("end", text + "\n")
        self._log_box.configure(state="disabled")
        self._log_box.see("end")

    def _log_clear(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    def refresh_lang(self):
        if not self._image_path:
            self._path_label.configure(text=t("adump.no_image"))
        if not self._running:
            self._btn_run.configure(text=t("adump.run"))
