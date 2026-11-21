"""
gui/ios_backup_panel.py — iOS iTunes/Finder backup import panel.
No extra dependencies required – uses only stdlib (sqlite3, plistlib).
"""
from __future__ import annotations

import os
import datetime
from pathlib import Path
from tkinter import filedialog
from typing import Optional

import customtkinter as ctk
from core.i18n import t
from .iconset import module_icon
from . import theme as th
from core.ios_backup_parser import (
    iOSBackupParser,
    IOS_BACKUP_MODULES,
    find_itunes_backups,
)

IOS_ACCENT = "#00d4ff"
ACCENT     = "#00d4ff"
BG_CARD    = "#16213e"
BG_PANEL   = "#1a1a2e"
BG_LOG     = "#0a0f1e"
TEXT_MUTED = "#888888"
GREEN      = "#00c853"
RED        = "#ff5252"
ORANGE     = "#ff9800"
WHITE      = "#ffffff"

MODULE_ICONS = {
    "contacts":          "👥",
    "messages":          "💬",
    "sms_attachments":   "📎",
    "call_logs":         "📞",
    "notes":             "📝",
    "calendar":          "📅",
    "safari_history":    "🌐",
    "photos":            "🖼",
    "photos_gps":        "📍",
    "voicemail":         "📳",
    "wifi_networks":     "📶",
    "apps_installed":    "📦",
    "app_usage":         "📊",
    "whatsapp":          "💚",
    "whatsapp_contacts": "👤",
    "viber":             "🟣",
}


class iOSBackupPanel(ctk.CTkFrame):
    def __init__(self, master, accent: str = IOS_ACCENT, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.accent = accent
        self._backup_path: str = ""
        self._output_dir: str = ""
        self._running = False
        self._parser: Optional[iOSBackupParser] = None
        self._module_vars: dict[str, ctk.BooleanVar] = {
            k: ctk.BooleanVar(value=True) for k in IOS_BACKUP_MODULES
        }
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()
        # Scan for iTunes backups right after the panel opens (otherwise the list
        # would show "Searching…" until the user clicks ↻)
        self.after(300, self._scan_backups)

    # ── UI construction ────────────────────────────────────────────────────

    def _build(self):
        self.grid_rowconfigure(1, weight=1)
        self._build_topbar()

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3, minsize=380)
        body.grid_columnconfigure(1, weight=2, minsize=340)
        body.grid_rowconfigure(0, weight=1)

        self._build_log(body)
        self._build_config(body)

    def _build_topbar(self):
        tb = th.Toolbar(self)
        tb.grid(row=0, column=0, sticky="ew", pady=(0, th.GAP))

        # Source: found backups / manual selection / output
        th.field_label(tb.source, t("ios_bkp.found_label")).pack(side="left", padx=(0, 8))
        self._found_var = ctk.StringVar(value=t("ios_bkp.searching"))
        self._found_combo = ctk.CTkComboBox(
            tb.source, variable=self._found_var,
            values=[t("ios_bkp.searching")], height=th.BTN_H,
            state="readonly", command=self._on_found_selected,
        )
        self._found_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))
        th.pack_row(
            th.icon_button(tb.source, "↻", self._scan_backups),
            th.secondary_button(tb.source, t("ios_bkp.pick_folder"), self._pick_folder,
                                width=150),
            th.secondary_button(tb.source, t("ui.output"), self._pick_output, width=150),
        )
        # Password of an encrypted backup (shown only for encrypted backups)
        self._pw_label = th.field_label(tb.source, t("ios_bkp.password"))
        self._password_entry = ctk.CTkEntry(tb.source, show="•", width=150,
                                            height=th.BTN_H, font=th.f_body())

        # Info: path / output / device / status
        self._output_label = ctk.CTkLabel(tb.info, text="", font=th.f_small(),
                                          text_color=TEXT_MUTED, anchor="w")
        self._output_label.pack(side="left")
        self._path_label = ctk.CTkLabel(tb.info, text=t("ios_bkp.no_path"), font=th.f_small(),
                                        text_color=TEXT_MUTED, anchor="w")
        self._path_label.pack(side="left", padx=(12, 0))
        self._device_info_lbl = ctk.CTkLabel(tb.info, text="", font=th.f_small(),
                                             text_color=ACCENT, anchor="w")
        self._device_info_lbl.pack(side="left", padx=(12, 0))
        self._status_bar = ctk.CTkLabel(tb.info, text="", font=th.f_body(),
                                        text_color=TEXT_MUTED, anchor="w")
        self._status_bar.pack(side="left", padx=(16, 0))

        self._btn_run = th.primary_button(tb.actions, t("ios_bkp.run"), self._run)
        self._btn_stop = th.danger_button(tb.actions, t("ext.stop"), self._stop,
                                          state="disabled")
        th.pack_row(self._btn_run, self._btn_stop)

    def _build_log(self, parent):
        self._log_card = th.LogCard(parent, t("ui.log"))
        self._log_card.grid(row=0, column=0, sticky="nsew", padx=(0, th.GAP))
        self._log_box = self._log_card.box

    def _build_config(self, parent):
        self._modules = th.ModuleList(parent, t("ui.modules"), t("ui.all"), t("ui.none"))
        self._modules.grid(row=0, column=1, sticky="nsew")
        for key in IOS_BACKUP_MODULES:
            self._modules.add(key, t(f"ios_bkp.mod.{key}"), self._module_vars[key],
                              image=module_icon(key, size=30),
                              emoji=MODULE_ICONS.get(key, "📁"))

    def _scan_backups(self):
        self._found_combo.configure(values=[t("ios_bkp.searching")])
        self._found_var.set(t("ios_bkp.searching"))
        import threading
        def _worker():
            backups = find_itunes_backups()
            self.after(0, lambda b=backups: self._update_backup_list(b))
        threading.Thread(target=_worker, daemon=True).start()

    def _update_backup_list(self, backups: list[dict]):
        if not backups:
            self._found_combo.configure(values=[t("ios_bkp.none_found")])
            self._found_var.set(t("ios_bkp.none_found"))
            return
        labels = []
        for b in backups:
            enc = "🔒" if b.get("encrypted") else ""
            lbl = f"{enc}{b['device_name']} – iOS {b['ios_version']} ({b['udid'][:8]}…)"
            labels.append(lbl)
        self._update_backup_list._backups = backups   # store reference
        self._found_combo.configure(values=labels)
        self._found_var.set(labels[0])
        self._on_found_selected(labels[0])

    def _on_found_selected(self, selection: str):
        backups = getattr(self._update_backup_list, "_backups", [])
        for b in backups:
            short = b["udid"][:8]
            if short in selection:
                self._set_backup_path(b["path"])
                pc = b.get("was_passcode_set")
                pc_txt = ""
                if pc is True:
                    pc_txt = f"  |  🔑 {t('ios_bkp.passcode_set')}"
                elif pc is False:
                    pc_txt = f"  |  {t('ios_bkp.passcode_unset')}"
                if b.get("encrypted"):
                    self._device_info_lbl.configure(
                        text=f"🔒 {t('ios_bkp.encrypted')}{pc_txt}", text_color=ORANGE
                    )
                    self._show_password(True)
                else:
                    self._device_info_lbl.configure(
                        text=f"iOS {b['ios_version']}  |  {b['device_name']}{pc_txt}",
                        text_color=ACCENT,
                    )
                    self._show_password(False)
                break

    def _show_password(self, show: bool):
        if show:
            self._pw_label.pack(side="left", padx=(12, 6))
            self._password_entry.pack(side="left")
        else:
            self._pw_label.pack_forget()
            self._password_entry.pack_forget()

    # ── File pickers ───────────────────────────────────────────────────────

    def _pick_folder(self):
        path = filedialog.askdirectory(title=t("ios_bkp.pick_title"))
        if path:
            self._set_backup_path(path)

    def _pick_output(self):
        path = filedialog.askdirectory(title=t("bkp.pick_output_title"))
        if path:
            self._output_dir = path
            self._output_label.configure(text=path, text_color=WHITE)

    def _set_backup_path(self, path: str):
        self._backup_path = path
        label = path
        if len(label) > 60:
            label = "…" + label[-57:]
        self._path_label.configure(text=label, text_color=WHITE)

        # Check whether the selected folder is an encrypted backup → show the password field
        try:
            enc = iOSBackupParser(path, "").is_encrypted()
        except Exception:
            enc = False
        self._show_password(enc)
        if enc:
            self._device_info_lbl.configure(text=f"🔒 {t('ios_bkp.encrypted')}",
                                            text_color=ORANGE)

        # Suggest output directory
        if not self._output_dir:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            name = Path(path).name
            default_out = os.path.join(
                os.path.expanduser("~"), "Documents",
                f"ForenziQ_iOS_{name[:20]}_{ts}",
            )
            self._output_dir = default_out
            short = "…" + default_out[-55:] if len(default_out) > 58 else default_out
            self._output_label.configure(text=short, text_color=TEXT_MUTED)

    # ── Run / Stop ─────────────────────────────────────────────────────────

    def _run(self):
        if not self._backup_path:
            self._status_bar.configure(text=t("ios_bkp.err_no_source"), text_color=RED)
            return
        selected = [k for k, v in self._module_vars.items() if v.get()]
        if not selected:
            self._status_bar.configure(text=t("ios_bkp.err_no_modules"), text_color=RED)
            return
        if not self._output_dir:
            self._status_bar.configure(text=t("bkp.err_no_output"), text_color=RED)
            return

        self._running = True
        self._btn_run.configure(state="disabled", text=t("ext.running"))
        self._btn_stop.configure(state="normal")
        self._status_bar.configure(text=t("ext.running"), text_color=self.accent)
        self._log_clear()

        pw = self._password_entry.get() if self._password_entry.winfo_ismapped() else ""
        self._parser = iOSBackupParser(self._backup_path, self._output_dir, password=pw)
        device_info = self._parser.get_device_info()
        device_lbl = device_info.get("device_name") or device_info.get("model", "iOS")

        self._log_raw("─" * 48, "sep")
        self._log(t("ios_bkp.log_started"), "info")
        self._log(f"{t('ext.log_device')} {device_lbl}", "muted")
        self._log(f"{t('ext.log_modules')} {len(selected)}", "muted")
        self._log_raw("─" * 48, "sep")

        self._parser.run(
            modules=selected,
            on_progress=lambda lbl, i, n: self.after(
                0, lambda l=lbl, c=i, tot=n: self._on_progress(l, c, tot)
            ),
            on_done=lambda results, err: self.after(
                0, lambda r=results, e=err: self._on_done(r, e)
            ),
            on_module_done=lambda res, i, n, el, av: self.after(
                0, lambda r=res, c=i, tot=n, e=el, a=av: self._on_module_done(r, c, tot, e, a)
            ),
            on_log=lambda msg: self.after(
                0, lambda m=msg: self._log(m, "muted")
            ),
        )

    def _stop(self):
        if self._parser:
            self._parser.stop()
        self._btn_stop.configure(state="disabled")
        self._status_bar.configure(text=t("ios_bkp.stopping") + "…", text_color=ORANGE)

    # ── Callbacks ──────────────────────────────────────────────────────────

    def _on_progress(self, label: str, current: int, total: int):
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

    def _on_done(self, results: list, err: str):
        self._running = False
        self._btn_run.configure(state="normal", text=t("ios_bkp.run"))
        self._btn_stop.configure(state="disabled")

        if err:
            self._log(f"✗  {err}", "err")
            self._status_bar.configure(text=f"✗ {err}", text_color=RED)
            return

        success_count = sum(1 for r in results if r.success)
        self._log_raw("", "")
        self._log_raw("─" * 48, "sep")
        self._log(t("ext.done").format(n=success_count), "ok")
        self._log_raw("─" * 48, "sep")
        self._status_bar.configure(
            text=f"✔ {t('ext.done').format(n=success_count)}", text_color=GREEN
        )
        if self._output_dir and os.path.exists(self._output_dir):
            os.startfile(self._output_dir)

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

    # ── Language refresh ───────────────────────────────────────────────────

    def refresh_lang(self):
        """Retranslate in place – static labels are handled by the global retranslate walk;
        here we only re-render the dynamic combined device badge for the
        currently selected backup. No disk re-scan and no output reset."""
        sel = self._found_var.get()
        if sel and getattr(self._update_backup_list, "_backups", None):
            self._on_found_selected(sel)
