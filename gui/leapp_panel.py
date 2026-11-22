# -*- coding: utf-8 -*-
"""
gui/leapp_panel.py – run ALEAPP / iLEAPP on an extraction and import the result into the case.

Top bar   : input file / folder (or a suggestion from the case), Run / Stop
Left      : log of the tool
Right     : tool + path (saved in Settings), input type, case folder, iTunes password,
            "Import existing report", summary of imported artifacts
"""
from __future__ import annotations

import os
import webbrowser
from tkinter import filedialog
from typing import Callable, Optional

import customtkinter as ctk

from core import leapp, settings
from core.i18n import t
from . import theme as th

RELEASES = {"aleapp": "https://github.com/abrignoni/ALEAPP/releases",
            "ileapp": "https://github.com/abrignoni/iLEAPP/releases"}
_SETTING = {"aleapp": "aleapp_path", "ileapp": "ileapp_path"}


class LeappPanel(ctk.CTkFrame):
    def __init__(self, master, accent: str = th.ACCENT,
                 on_imported: Optional[Callable[[str], None]] = None,
                 on_open_search: Optional[Callable[[], None]] = None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.accent = accent
        self._on_imported = on_imported
        self._on_open_search = on_open_search
        self._input = ""
        self._case_dir = ""
        self._run: Optional[leapp.LeappRun] = None
        self._summary: dict = {}
        self._tool = ctk.StringVar(value="aleapp")
        self._type = ctk.StringVar(value="")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()
        self._on_tool_change()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build(self):
        tb = th.Toolbar(self)
        tb.grid(row=0, column=0, sticky="ew", pady=(0, th.GAP))
        self._btn_file = th.secondary_button(tb.source, t("leapp.pick_file"), self._pick_file)
        self._btn_dir = th.secondary_button(tb.source, t("leapp.pick_folder"), self._pick_folder)
        th.pack_row(self._btn_file, self._btn_dir)
        self._input_lbl = ctk.CTkLabel(tb.source, text="", font=th.f_small(),
                                       text_color=th.TEXT_MUTED, anchor="w")
        self._input_lbl.pack(side="left", padx=(16, 0))
        self._status = ctk.CTkLabel(tb.info, text="", font=th.f_body(),
                                    text_color=th.TEXT_MUTED, anchor="w")
        self._status.pack(side="left")
        self._btn_run = th.primary_button(tb.actions, t("leapp.run"), self._start)
        self._btn_stop = th.danger_button(tb.actions, t("ext.stop"), self._stop, state="disabled")
        th.pack_row(self._btn_run, self._btn_stop)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3, minsize=380)
        body.grid_columnconfigure(1, weight=2, minsize=360)
        body.grid_rowconfigure(0, weight=1)

        self._log = th.LogCard(body, t("ui.log"))
        self._log.grid(row=0, column=0, sticky="nsew", padx=(0, th.GAP))
        self._log.write(t("leapp.intro"), "info", timestamp=False)

        right = th.card(body)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(99, weight=1)
        P = th.PAD
        r = 0

        th.section_label(right, t("leapp.tool")).grid(row=r, column=0, sticky="w", padx=P, pady=(P, 6))
        r += 1
        seg = ctk.CTkSegmentedButton(right, values=["ALEAPP (Android)", "iLEAPP (iOS)"],
                                     command=self._on_seg, font=th.f_body(),
                                     selected_color=th.BTN_PRIMARY,
                                     selected_hover_color=th.BTN_PRIMARY_HOVER)
        seg.set("ALEAPP (Android)")
        seg.grid(row=r, column=0, sticky="ew", padx=P)
        self._seg = seg
        r += 1

        th.field_label(right, t("leapp.path")).grid(row=r, column=0, sticky="w", padx=P, pady=(10, 2))
        r += 1
        prow = ctk.CTkFrame(right, fg_color="transparent")
        prow.grid(row=r, column=0, sticky="ew", padx=P)
        prow.grid_columnconfigure(0, weight=1)
        self._path_entry = ctk.CTkEntry(prow, height=th.BTN_H_SMALL, fg_color=th.BG_INPUT,
                                        border_color=th.BORDER, font=th.f_small())
        self._path_entry.grid(row=0, column=0, sticky="ew")
        self._path_entry.bind("<FocusOut>", lambda _e: self._save_path())
        th.small_button(prow, "…", self._browse_tool, width=34).grid(row=0, column=1, padx=(6, 0))
        self._dl_btn = th.small_button(prow, t("leapp.download"), self._open_releases, width=90)
        self._dl_btn.grid(row=0, column=2, padx=(6, 0))
        r += 1
        self._path_hint = th.hint_label(right, "", ref=right, offset=2 * P)
        self._path_hint.grid(row=r, column=0, sticky="ew", padx=P, pady=(2, 0))
        r += 1

        th.field_label(right, t("leapp.input_type")).grid(row=r, column=0, sticky="w", padx=P, pady=(10, 2))
        r += 1
        self._type_menu = ctk.CTkOptionMenu(right, variable=self._type, values=["fs"],
                                            height=th.BTN_H_SMALL, font=th.f_small(),
                                            fg_color=th.BTN_SECONDARY, button_color=th.BTN_SECONDARY,
                                            button_hover_color=th.BTN_SECONDARY_HOVER,
                                            command=lambda _v: self._update_password())
        self._type_menu.grid(row=r, column=0, sticky="w", padx=P)
        r += 1

        self._pw_frame = ctk.CTkFrame(right, fg_color="transparent")
        self._pw_frame.grid(row=r, column=0, sticky="ew", padx=P, pady=(10, 0))
        self._pw_frame.grid_columnconfigure(0, weight=1)
        self._pw_entry = ctk.CTkEntry(self._pw_frame, height=th.BTN_H_SMALL, show="•",
                                      placeholder_text=t("leapp.itunes_pw"), fg_color=th.BG_INPUT,
                                      border_color=th.BORDER, font=th.f_small())
        self._pw_entry.grid(row=0, column=0, sticky="ew")
        th.hint_label(self._pw_frame, t("leapp.itunes_pw_hint"), color=th.ORANGE,
                      ref=right, offset=2 * P).grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self._pw_frame.grid_remove()
        r += 1

        th.field_label(right, t("leapp.case")).grid(row=r, column=0, sticky="w", padx=P, pady=(10, 2))
        r += 1
        crow = ctk.CTkFrame(right, fg_color="transparent")
        crow.grid(row=r, column=0, sticky="ew", padx=P)
        crow.grid_columnconfigure(0, weight=1)
        self._case_lbl = ctk.CTkLabel(crow, text=t("leapp.case_none"), font=th.f_small(),
                                      text_color=th.TEXT_MUTED, anchor="w")
        self._case_lbl.grid(row=0, column=0, sticky="ew")
        th.small_button(crow, t("leapp.change"), self._pick_case, width=80).grid(row=0, column=1)
        r += 1

        self._suggest_frame = ctk.CTkFrame(right, fg_color="transparent")
        self._suggest_frame.grid(row=r, column=0, sticky="ew", padx=P, pady=(8, 0))
        r += 1

        th.separator(right).grid(row=r, column=0, sticky="ew", padx=P, pady=12)
        r += 1
        hdr = ctk.CTkFrame(right, fg_color="transparent")
        hdr.grid(row=r, column=0, sticky="ew", padx=P)
        hdr.grid_columnconfigure(0, weight=1)
        th.section_label(hdr, t("leapp.imported")).grid(row=0, column=0, sticky="w")
        th.small_button(hdr, t("leapp.import_existing"), self._import_existing,
                        width=150).grid(row=0, column=1)
        r += 1
        self._sum_lbl = th.hint_label(right, t("leapp.nothing"), ref=right, offset=2 * P)
        self._sum_lbl.grid(row=r, column=0, sticky="ew", padx=P, pady=(6, 4))
        r += 1
        self._arts = ctk.CTkScrollableFrame(right, fg_color=th.BG_LOG, height=120)
        self._arts.grid(row=99, column=0, sticky="nsew", padx=P, pady=(0, 6))
        self._arts.grid_columnconfigure(0, weight=1)
        brow = ctk.CTkFrame(right, fg_color="transparent")
        brow.grid(row=100, column=0, sticky="ew", padx=P, pady=(0, P))
        self._btn_html = th.small_button(brow, t("leapp.open_html"), self._open_html,
                                         width=150, state="disabled")
        self._btn_search = th.small_button(brow, t("leapp.open_search"), self._open_search,
                                           width=150, state="disabled")
        th.pack_row(self._btn_html, self._btn_search)

    # ── Tool / path ──────────────────────────────────────────────────────────

    def _on_seg(self, value: str):
        self._tool.set("ileapp" if value.startswith("iLEAPP") else "aleapp")
        self._on_tool_change()

    def _on_tool_change(self):
        tool = self._tool.get()
        self._seg.set("iLEAPP (iOS)" if tool == "ileapp" else "ALEAPP (Android)")
        path = leapp.find_tool(tool, settings.get(_SETTING[tool], ""))
        self._path_entry.delete(0, "end")
        self._path_entry.insert(0, path)
        self._update_path_hint()
        self._type_menu.configure(values=leapp.TOOLS[tool]["types"])
        if self._input:
            self._type.set(leapp.detect_input_type(self._input, tool) or "fs")
        elif self._type.get() not in leapp.TOOLS[tool]["types"]:
            self._type.set("fs")
        self._update_password()

    def _update_path_hint(self):
        p = self._path_entry.get().strip()
        exe, host = leapp.arch_mismatch(p) if p and os.path.isfile(p) else ("", "")
        if exe:
            self._path_hint.configure(text=t("leapp.wrong_arch").format(exe=exe, host=host),
                                      text_color=th.RED)
        elif p and os.path.isfile(p):
            self._path_hint.configure(text=t("leapp.path_ok"), text_color=th.GREEN)
        else:
            self._path_hint.configure(text=t("leapp.path_missing").format(
                tool=leapp.TOOLS[self._tool.get()]["name"]), text_color=th.ORANGE)

    def _save_path(self):
        settings.save({_SETTING[self._tool.get()]: self._path_entry.get().strip()})
        self._update_path_hint()

    def _browse_tool(self):
        name = leapp.TOOLS[self._tool.get()]["name"]
        p = filedialog.askopenfilename(title=name, filetypes=[
            (name, "*.exe *.py"), (t("bkp.all_files"), "*.*")])
        if p:
            self._path_entry.delete(0, "end")
            self._path_entry.insert(0, os.path.normpath(p))
            self._save_path()

    def _open_releases(self):
        webbrowser.open(RELEASES[self._tool.get()])

    # ── Input / case ─────────────────────────────────────────────────────────

    def _pick_file(self):
        p = filedialog.askopenfilename(title=t("leapp.pick_file"), filetypes=[
            (t("leapp.archives"), "*.zip *.tar *.gz *.tgz"), (t("bkp.all_files"), "*.*")])
        if p:
            self.set_input(p)

    def _pick_folder(self):
        p = filedialog.askdirectory(title=t("leapp.pick_folder"))
        if p:
            self.set_input(p)

    def set_input(self, path: str, tool: str = ""):
        self._input = os.path.normpath(path)
        self._input_lbl.configure(text=_short(self._input), text_color=th.ACCENT)
        self._tool.set(tool or leapp.guess_tool(self._input))
        self._on_tool_change()
        if not self._case_dir:
            # input inside a ForenziQ case → import into that case
            parent = os.path.dirname(self._input)
            if os.path.isfile(os.path.join(parent, "device_info.json")) or \
                    os.path.isfile(os.path.join(parent, "ios_device_info.json")):
                self.set_case(parent)

    def _update_password(self):
        show = (self._tool.get() == "ileapp" and self._type.get() == "itunes"
                and self._input and leapp.itunes_encrypted(self._input))
        if show:
            self._pw_frame.grid()
        else:
            self._pw_frame.grid_remove()

    def _pick_case(self):
        p = filedialog.askdirectory(title=t("leapp.case"))
        if p:
            self.set_case(p)

    def set_case(self, folder: str):
        """Case folder (results of an extraction) where the import is written."""
        if not folder or not os.path.isdir(folder):
            return
        self._case_dir = os.path.normpath(folder)
        self._case_lbl.configure(text=_short(self._case_dir), text_color=th.TEXT)
        for w in self._suggest_frame.winfo_children():
            w.destroy()
        for tool, path in leapp.case_inputs(self._case_dir):
            th.small_button(self._suggest_frame, "→ " + os.path.basename(path),
                            lambda p=path, tl=tool: self.set_input(p, tl),
                            width=170).pack(side="left", padx=(0, 6))
        self._show_existing()

    def _show_existing(self):
        """Show what was imported into the case earlier (aleapp.json / ileapp.json)."""
        for tool in leapp.TOOLS:
            meta, secs = leapp.load_imported(self._case_dir, tool)
            if secs:
                self._show_summary({
                    "tool": meta.get("tool", leapp.TOOLS[tool]["name"]),
                    "file": leapp.TOOLS[tool]["json"], "report": meta.get("report", ""),
                    "artifacts": [(k, len(v)) for k, v in secs.items()],
                    "records": sum(len(v) for v in secs.values()),
                    "html": leapp.html_index(meta.get("report", "")) if meta.get("report") else "",
                })
                return
        self._show_summary({})

    # ── Run ──────────────────────────────────────────────────────────────────

    def _set_status(self, text: str, color=th.TEXT_MUTED):
        self._status.configure(text=text, text_color=color)

    def _start(self):
        tool = self._tool.get()
        path = self._path_entry.get().strip()
        if not path or not os.path.isfile(path):
            self._set_status(t("leapp.err_tool"), th.RED)
            return
        exe, host = leapp.arch_mismatch(path)
        if exe:
            msg = t("leapp.wrong_arch").format(exe=exe, host=host)
            self._set_status("✗ " + t("leapp.err_tool"), th.RED)
            self._log.write(msg, "err")
            return
        if not self._input or not os.path.exists(self._input):
            self._set_status(t("leapp.err_input"), th.RED)
            return
        if not self._case_dir:
            self._set_status(t("leapp.err_case"), th.RED)
            return
        itype = self._type.get() or leapp.detect_input_type(self._input, tool)
        if not itype:
            self._set_status(t("leapp.err_type"), th.RED)
            return
        self._save_path()
        self._run = leapp.LeappRun(tool, path, self._input, itype, self._case_dir,
                                   itunes_password=self._pw_entry.get() if self._pw_frame.winfo_ismapped() else "")
        self._log.clear()
        self._log.write(t("leapp.started").format(tool=leapp.TOOLS[tool]["name"]), "info")
        th.set_running(self._btn_run, self._btn_stop, True)
        self._set_status(t("leapp.running"), th.ACCENT)
        self._run.run(log=lambda s: self.after(0, lambda s=s: self._log.write(s, _tag(s))),
                      done=lambda ok, msg, summ: self.after(0, lambda: self._done(ok, msg, summ)))

    def _stop(self):
        if self._run:
            self._run.stop()
        self._btn_stop.configure(state="disabled")
        self._set_status(t("leapp.stopped"), th.ORANGE)

    def _done(self, ok: bool, msg: str, summary: dict):
        th.set_running(self._btn_run, self._btn_stop, False)
        if msg == "stopped":
            self._set_status("■ " + t("leapp.stopped"), th.ORANGE)
            self._log.write(t("leapp.stopped"), "warn")
            return
        if summary:
            self._show_summary(summary)
            self._log.write(t("leapp.imported_msg").format(
                n=summary.get("records", 0), a=len(summary.get("artifacts", [])),
                file=summary.get("file", "")), "ok")
            if self._on_imported:
                self._on_imported(self._case_dir)
        if ok:
            self._set_status("✔ " + t("leapp.done"), th.GREEN)
        else:
            self._set_status("✗ " + t("leapp.failed"), th.RED)
            self._log.write(t("leapp.failed") + f" ({msg})", "err")

    def _import_existing(self):
        if not self._case_dir:
            self._set_status(t("leapp.err_case"), th.RED)
            return
        p = filedialog.askdirectory(title=t("leapp.import_existing"),
                                    initialdir=os.path.join(self._case_dir, leapp.OUT_SUBDIR)
                                    if os.path.isdir(os.path.join(self._case_dir, leapp.OUT_SUBDIR))
                                    else self._case_dir)
        if not p:
            return
        root = leapp.find_report_root(p)
        if not root:
            self._set_status(t("leapp.err_report"), th.RED)
            return
        try:
            summ = leapp.import_report(leapp.tool_of_report(root), root, self._case_dir)
        except Exception as e:
            self._set_status(str(e), th.RED)
            return
        self._done(True, "ok", summ)

    # ── Summary ──────────────────────────────────────────────────────────────

    def _show_summary(self, summary: dict):
        self._summary = summary
        for w in self._arts.winfo_children():
            w.destroy()
        if not summary:
            self._sum_lbl.configure(text=t("leapp.nothing"))
            self._btn_html.configure(state="disabled")
            self._btn_search.configure(state="disabled")
            return
        self._sum_lbl.configure(text=t("leapp.summary").format(
            tool=summary.get("tool", ""), n=summary.get("records", 0),
            a=len(summary.get("artifacts", [])), file=summary.get("file", "")))
        for i, (name, n) in enumerate(sorted(summary.get("artifacts", []))):
            ctk.CTkLabel(self._arts, text=name, font=th.f_small(), anchor="w").grid(
                row=i, column=0, sticky="ew", padx=(6, 4))
            ctk.CTkLabel(self._arts, text=str(n), font=th.f_small(),
                         text_color=th.TEXT_MUTED).grid(row=i, column=1, sticky="e", padx=6)
        self._btn_html.configure(state="normal" if summary.get("html") else "disabled")
        self._btn_search.configure(state="normal" if self._on_open_search else "disabled")

    def _open_html(self):
        p = self._summary.get("html")
        if p and os.path.isfile(p):
            os.startfile(p)

    def _open_search(self):
        if self._on_open_search:
            self._on_open_search()

    def refresh_lang(self):
        self._log.title.configure(text=t("ui.log"))
        self._update_path_hint()
        if self._summary:
            self._show_summary(self._summary)
        elif not self._case_dir:
            self._case_lbl.configure(text=t("leapp.case_none"))


def _short(p: str, limit: int = 60) -> str:
    return p if len(p) <= limit else "…" + p[-(limit - 1):]


def _tag(line: str) -> str:
    low = line.lower()
    if "error" in low or "failed" in low or "traceback" in low:
        return "err"
    if "completed" in low:
        return "ok"
    if line.startswith("$ "):
        return "muted"
    return ""
