"""
gui/s30_panel.py — Nokia Series 30+ (Spreadtrum SC6531E / MOCOR) flash-dump analysis panel.
The dump is acquired externally (flasher / programmer); this panel only parses the file.
"""
from __future__ import annotations

import datetime
import os
from tkinter import filedialog
from typing import Callable, Optional

import customtkinter as ctk
from core.i18n import t
from core.s30_dump import run_s30_analysis, DUMP_FILETYPES
from .iconset import module_icon
from . import theme as th

ACCENT     = "#00d4ff"
BG_CARD    = "#16213e"
BG_LOG     = "#0a0f1e"
TEXT_MUTED = "#888888"
GREEN      = "#00c853"
RED        = "#ff5252"
ORANGE     = "#ff9800"
WHITE      = "#ffffff"

# (option key, i18n key, icon file key, emoji fallback)
DATA_OPTIONS = [
    ("contacts", "s30.opt.contacts", "contacts",      "👥"),
    ("sms",      "s30.opt.sms",      "sms",           "💬"),
    ("calls",    "s30.opt.calls",    "call_logs",     "📞"),
    ("files",    "s30.opt.files",    "file_analysis", "📁"),
]
OUTPUT_OPTIONS = [
    ("xlsx",   "s30.opt.xlsx"),
    ("disk",   "s30.opt.disk"),
    ("pdf",    "s30.opt.pdf"),
    ("thumbs", "s30.opt.thumbs"),
]


class S30Panel(ctk.CTkFrame):
    def __init__(self, master, accent: str = ACCENT,
                 on_finished: Optional[Callable[[str], None]] = None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.accent = accent
        self._on_finished = on_finished
        self._dump_path = ""
        self._output_dir = ""
        self._target = ""
        self._running = False
        self._last_summary: Optional[dict] = None
        self._vars = {k: ctk.BooleanVar(value=True)
                      for k in [o[0] for o in DATA_OPTIONS] + [o[0] for o in OUTPUT_OPTIONS]}
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()

    # ── UI construction ────────────────────────────────────────────────────

    def _build(self):
        self._build_topbar()
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3, minsize=380)
        body.grid_columnconfigure(1, weight=2, minsize=340)
        body.grid_rowconfigure(0, weight=1)
        self._build_log(body)
        self._build_config(body)

    def _build_topbar(self):
        tb = th.Toolbar(self, progress=True)
        tb.grid(row=0, column=0, sticky="ew", pady=(0, th.GAP))

        # Source: dump + output folder
        th.pack_row(
            th.secondary_button(tb.source, t("s30.pick_dump"), self._pick_dump, width=150),
            th.secondary_button(tb.source, t("ui.output"), self._pick_output, width=150),
        )
        self._path_label = ctk.CTkLabel(tb.source, text=t("s30.no_dump"), font=th.f_small(),
                                        text_color=TEXT_MUTED, anchor="w")
        self._path_label.pack(side="left", padx=(12, 0))
        self._output_label = ctk.CTkLabel(tb.source, text="", font=th.f_small(),
                                          text_color=TEXT_MUTED, anchor="w")
        self._output_label.pack(side="left", padx=(12, 0))

        # Status + actions
        self._status_bar = ctk.CTkLabel(tb.info, text="", font=th.f_body(),
                                        text_color=TEXT_MUTED, anchor="w")
        self._status_bar.pack(side="left")

        self._btn_open = th.secondary_button(tb.actions, t("s30.open_output"),
                                             self._open_output, state="disabled")
        self._btn_run = th.primary_button(tb.actions, t("s30.run"), self._run)
        th.pack_row(self._btn_open, self._btn_run)
        self._progress = tb.progress

    def _build_log(self, parent):
        self._log_card = th.LogCard(parent, t("ui.log"))
        self._log_card.grid(row=0, column=0, sticky="nsew", padx=(0, th.GAP))
        self._log_box = self._log_card.box
        self._log_card.write(t("s30.hint"), "muted", timestamp=False)

    def _build_config(self, parent):
        ml = th.ModuleList(parent)
        ml.grid(row=0, column=1, sticky="nsew")
        self._modules = ml
        right = ml.scroll

        ml.add_section(t("ui.modules"), all_text=t("ui.all"), none_text=t("ui.none"))
        for key, label_key, icon_key, emoji in DATA_OPTIONS:
            ml.add(key, t(label_key), self._vars[key],
                   image=module_icon(icon_key, size=30), emoji=emoji)

        ml.add_section(t("s30.sec_outputs"))
        out = ctk.CTkFrame(right, fg_color=BG_CARD, corner_radius=th.RADIUS_ITEM)
        out.grid(row=ml.next_row(), column=0, sticky="ew", padx=4, pady=3)
        for i, (key, label_key) in enumerate(OUTPUT_OPTIONS):
            th.checkbox(out, self._vars[key], text=t(label_key)).grid(
                row=i, column=0, sticky="w", padx=14,
                pady=(12 if i == 0 else 4, 12 if i == len(OUTPUT_OPTIONS) - 1 else 4))

        ml.add_section(t("s30.sec_case"))
        case = ctk.CTkFrame(right, fg_color=BG_CARD, corner_radius=th.RADIUS_ITEM)
        case.grid(row=ml.next_row(), column=0, sticky="ew", padx=4, pady=3)
        case.grid_columnconfigure(1, weight=1)
        self._case_entries = {}
        fields = (("case_id", "ev.f_case"), ("examiner", "ev.f_examiner"), ("note", "ev.f_note"))
        for i, (key, label_key) in enumerate(fields):
            th.field_label(case, t(label_key)).grid(
                row=i, column=0, sticky="w", padx=(14, 8),
                pady=(12 if i == 0 else 4, 12 if i == len(fields) - 1 else 4))
            e = ctk.CTkEntry(case, height=32, font=th.f_body())
            e.grid(row=i, column=1, sticky="ew", padx=(0, 14),
                   pady=(12 if i == 0 else 4, 12 if i == len(fields) - 1 else 4))
            self._case_entries[key] = e

        ml.add_section(t("s30.summary"))
        self._summary_card = ctk.CTkFrame(right, fg_color=BG_CARD, corner_radius=th.RADIUS_ITEM)
        self._summary_card.grid(row=ml.next_row(), column=0, sticky="ew", padx=4, pady=3)
        self._summary_card.grid_columnconfigure(0, weight=1)
        self._summary_title = None
        self._summary_lbl = ctk.CTkLabel(self._summary_card, text=t("s30.summary_empty"),
                                         font=th.f_small(), text_color="#cccccc",
                                         anchor="w", justify="left")
        self._summary_lbl.grid(row=0, column=0, sticky="w", padx=14, pady=12)

        lim = th.hint_label(right, t("s30.limits"), ref=right, offset=30)
        lim.grid(row=ml.next_row(), column=0, sticky="ew", padx=8, pady=(10, 12))

    # ── Pickers ────────────────────────────────────────────────────────────

    def _pick_dump(self):
        path = filedialog.askopenfilename(title=t("s30.pick_title"), filetypes=DUMP_FILETYPES)
        if not path:
            return
        self._dump_path = path
        label = path if len(path) <= 60 else "…" + path[-57:]
        self._path_label.configure(text=label, text_color=WHITE)
        if not self._output_dir:
            self._output_dir = os.path.join(os.path.expanduser("~"), "Documents", "ForenziQ_S30")
            short = self._output_dir if len(self._output_dir) <= 58 else "…" + self._output_dir[-55:]
            self._output_label.configure(text=short, text_color=TEXT_MUTED)

    def _pick_output(self):
        path = filedialog.askdirectory(title=t("bkp.pick_output_title"))
        if path:
            self._output_dir = path
            self._output_label.configure(text=path, text_color=WHITE)

    # ── Run ────────────────────────────────────────────────────────────────

    def _run(self):
        if self._running:
            return
        if not self._dump_path or not os.path.isfile(self._dump_path):
            self._status_bar.configure(text=t("s30.err_no_dump"), text_color=RED)
            return
        opts = {k: v.get() for k, v in self._vars.items()}
        if not any(opts[k] for k, *_ in DATA_OPTIONS):
            self._status_bar.configure(text=t("s30.err_no_modules"), text_color=RED)
            return
        if not self._output_dir:
            self._status_bar.configure(text=t("bkp.err_no_output"), text_color=RED)
            return

        self._running = True
        self._target = ""
        self._btn_run.configure(state="disabled", text=t("ext.running"))
        self._btn_open.configure(state="disabled")
        self._status_bar.configure(text=t("ext.running"), text_color=self.accent)
        self._progress.set(0)
        self._log_clear()
        self._log_raw("─" * 48, "sep")
        self._log(t("s30.log_started"), "info")
        self._log(os.path.basename(self._dump_path), "muted")
        self._log_raw("─" * 48, "sep")

        case = {k: e.get().strip() for k, e in self._case_entries.items()}
        run_s30_analysis(
            self._dump_path, self._output_dir, options=opts, case=case,
            on_log=lambda m: self.after(0, lambda m=m: self._log(m, self._tag_for(m))),
            on_progress=lambda p: self.after(0, lambda p=p: self._progress.set(p / 100)),
            on_done=lambda s, tg, err: self.after(0, lambda: self._on_done(s, tg, err)),
        )

    @staticmethod
    def _tag_for(msg: str) -> str:
        up = msg.upper()
        if up.startswith(("UPOZORNĚNÍ", "WARNING")):
            return "warn"
        if msg.startswith(("Hotovo", "Done")):
            return "ok"
        return ""

    def _on_done(self, summary: Optional[dict], target: str, err: str):
        self._running = False
        self._btn_run.configure(state="normal", text=t("s30.run"))
        if err:
            self._log(f"✗  {err}", "err")
            self._status_bar.configure(text=t("s30.failed"), text_color=RED)
            return
        self._target = target
        self._last_summary = summary
        self._progress.set(1)
        self._btn_open.configure(state="normal")
        self._render_summary()
        self._log_raw("─" * 48, "sep")
        self._status_bar.configure(text="✔ " + t("s30.done"), text_color=GREEN)
        if self._on_finished:
            self._on_finished(target)
        self._open_output()

    def _render_summary(self):
        s = self._last_summary
        if not s:
            self._summary_lbl.configure(text=t("s30.summary_empty"))
            return
        lines = []
        if s["device"]:
            lines.append(f"{t('s30.sum_device')}: {s['device']}")
        lines += [
            f"{t('s30.opt.contacts')}: {s['contacts']}",
            f"SMS: {s['sms']}  ({t('s30.sum_recovered')}: {s['sms_recovered']})",
            f"{t('s30.opt.calls')}: {s['calls']}  ({t('s30.sum_recovered')}: {s['calls_recovered']})",
            f"{t('s30.sum_files')}: {s['files']}  ({t('s30.sum_deleted')}: {s['files_deleted']})",
        ]
        if not s["supported"]:
            lines.append("⚠ " + t("s30.sum_unsupported"))
        if s["sha256"]:
            lines.append(f"SHA-256: {s['sha256'][:32]}…")
        self._summary_lbl.configure(text="\n".join(lines))

    def _open_output(self):
        if self._target and os.path.isdir(self._target):
            os.startfile(self._target)

    # ── Log helpers ────────────────────────────────────────────────────────

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

    # ── Language refresh ───────────────────────────────────────────────────

    def refresh_lang(self):
        """Static labels are handled by the global retranslate walk; re-render dynamic ones."""
        if not self._dump_path:
            self._path_label.configure(text=t("s30.no_dump"))
        if not self._running:
            self._btn_run.configure(text=t("s30.run"))
        self._render_summary()
