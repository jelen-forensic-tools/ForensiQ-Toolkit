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
from core.ios_backup_parser import (
    iOSBackupParser,
    IOS_BACKUP_MODULES,
    find_itunes_backups,
)

IOS_ACCENT = "#5AC8FA"
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

    # ── UI construction ────────────────────────────────────────────────────

    def _build(self):
        self.grid_rowconfigure(1, weight=1)
        self._build_topbar()

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3, minsize=380)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        self._build_log(body)
        self._build_config(body)

    def _build_topbar(self):
        top = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        top.grid_columnconfigure(0, weight=1)

        # ── Řádek 0 – nalezené zálohy + ovládací tlačítka ──
        row0 = ctk.CTkFrame(top, fg_color="transparent")
        row0.grid(row=0, column=0, padx=16, pady=(12, 4), sticky="ew")
        row0.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            row0, text=t("ios_bkp.found_label"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_MUTED,
        ).grid(row=0, column=0, padx=(0, 8))

        self._found_var = ctk.StringVar(value=t("ios_bkp.searching"))
        self._found_combo = ctk.CTkComboBox(
            row0, variable=self._found_var,
            values=[t("ios_bkp.searching")],
            state="readonly",
            command=self._on_found_selected,
        )
        self._found_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))

        ctk.CTkButton(
            row0, text="↺", width=36, height=30,
            fg_color="#2a2a4a", hover_color="#3a3a5a",
            command=self._scan_backups,
        ).grid(row=0, column=2, padx=(0, 6))

        ctk.CTkButton(
            row0, text=t("ios_bkp.pick_folder"),
            font=ctk.CTkFont(size=12),
            fg_color="#2a2a4a", hover_color="#3a3a5a",
            height=30, width=150,
            command=self._pick_folder,
        ).grid(row=0, column=3, padx=(0, 6))

        ctk.CTkButton(
            row0, text="📂", width=36, height=30,
            fg_color="#2a2a4a", hover_color="#3a3a5a",
            command=self._pick_output,
        ).grid(row=0, column=4)

        # ── Řádek 1 – cesta / výstup / info zařízení + akční tlačítka ──
        row1 = ctk.CTkFrame(top, fg_color="transparent")
        row1.grid(row=1, column=0, padx=16, pady=(0, 12), sticky="ew")
        row1.grid_columnconfigure(0, weight=1)

        info = ctk.CTkFrame(row1, fg_color="transparent")
        info.grid(row=0, column=0, sticky="w")
        self._path_label = ctk.CTkLabel(
            info, text=t("ios_bkp.no_path"),
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED, anchor="w",
        )
        self._path_label.pack(side="left")
        self._output_label = ctk.CTkLabel(
            info, text="",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED, anchor="w",
        )
        self._output_label.pack(side="left", padx=(12, 0))
        self._device_info_lbl = ctk.CTkLabel(
            info, text="",
            font=ctk.CTkFont(size=11),
            text_color=ACCENT,
        )
        self._device_info_lbl.pack(side="left", padx=(12, 0))

        actions = ctk.CTkFrame(row1, fg_color="transparent")
        actions.grid(row=0, column=1, sticky="e")
        self._status_bar = ctk.CTkLabel(
            actions, text="", font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED, anchor="e", width=140,
        )
        self._status_bar.pack(side="left", padx=(0, 12))
        self._btn_run = ctk.CTkButton(
            actions, text=t("ios_bkp.run"),
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#1D6F42", hover_color="#145230",
            text_color="#fff", width=150, height=36,
            command=self._run,
        )
        self._btn_run.pack(side="left", padx=(0, 4))
        self._btn_stop = ctk.CTkButton(
            actions, text=t("ext.stop"),
            font=ctk.CTkFont(size=13),
            fg_color="#f44336", hover_color="#c62828",
            text_color="#fff", width=100, height=36,
            state="disabled", command=self._stop,
        )
        self._btn_stop.pack(side="left")

    def _build_log(self, parent):
        log_card = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12)
        log_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        log_card.grid_columnconfigure(0, weight=1)
        log_card.grid_rowconfigure(2, weight=1)

        hdr = ctk.CTkFrame(log_card, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))

        ctk.CTkLabel(
            hdr, text=t("bkp.log_title"),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=self.accent,
        ).pack(side="left")

        ctk.CTkFrame(log_card, height=1, fg_color="#2a2a3e").grid(
            row=1, column=0, sticky="ew", padx=14,
        )

        self._log_box = ctk.CTkTextbox(
            log_card,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=BG_LOG, text_color="#cccccc",
            wrap="word", state="disabled", corner_radius=8,
        )
        self._log_box.grid(row=2, column=0, sticky="nsew", padx=10, pady=(6, 10))
        self._log_box.tag_config("ok",    foreground=GREEN)
        self._log_box.tag_config("err",   foreground=RED)
        self._log_box.tag_config("info",  foreground=self.accent)
        self._log_box.tag_config("warn",  foreground=ORANGE)
        self._log_box.tag_config("muted", foreground=TEXT_MUTED)
        self._log_box.tag_config("sep",   foreground="#2a3a5a")

    def _build_config(self, parent):
        right = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)

        for i, (key, label) in enumerate(IOS_BACKUP_MODULES.items()):
            icon = MODULE_ICONS.get(key, "📁")
            card = ctk.CTkFrame(right, fg_color=BG_CARD, corner_radius=10)
            card.grid(row=i + 1, column=0, sticky="ew", padx=5, pady=4)
            card.grid_columnconfigure(1, weight=1)

            _img = module_icon(key, size=30)
            if _img is not None:
                ctk.CTkLabel(card, text="", image=_img, width=36).grid(
                    row=0, column=0, padx=(12, 4), pady=8)
            else:
                ctk.CTkLabel(
                    card, text=icon, font=ctk.CTkFont(size=20), width=36,
                ).grid(row=0, column=0, padx=(12, 4), pady=10)

            ctk.CTkLabel(
                card, text=t(f"ios_bkp.mod.{key}"),
                font=ctk.CTkFont(size=12, weight="bold"), anchor="w",
            ).grid(row=0, column=1, sticky="w", padx=4)

            ctk.CTkCheckBox(
                card, text="",
                variable=self._module_vars[key],
                checkbox_width=20, checkbox_height=20,
                fg_color=self.accent, checkmark_color="#000",
            ).grid(row=0, column=2, padx=12)

        sel_row_idx = len(IOS_BACKUP_MODULES) + 1
        ctk.CTkFrame(right, height=1, fg_color="#2a2a3e").grid(
            row=sel_row_idx, column=0, sticky="ew", padx=8, pady=(10, 6)
        )
        sel = ctk.CTkFrame(right, fg_color="transparent")
        sel.grid(row=sel_row_idx + 1, column=0, padx=8, pady=(0, 12), sticky="ew")
        sel.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(
            sel, text=t("ext.select_all"),
            font=ctk.CTkFont(size=10),
            fg_color="#2a2a4a", hover_color="#3a3a5a",
            height=26,
            command=lambda: [v.set(True) for v in self._module_vars.values()],
        ).grid(row=0, column=0, padx=(0, 3), sticky="ew")
        ctk.CTkButton(
            sel, text=t("ext.deselect_all"),
            font=ctk.CTkFont(size=10),
            fg_color="#2a2a4a", hover_color="#3a3a5a",
            height=26,
            command=lambda: [v.set(False) for v in self._module_vars.values()],
        ).grid(row=0, column=1, padx=(3, 0), sticky="ew")

    # ── Backup discovery ───────────────────────────────────────────────────

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
                if b.get("encrypted"):
                    self._device_info_lbl.configure(
                        text=f"🔒 {t('ios_bkp.encrypted')}", text_color=ORANGE
                    )
                else:
                    self._device_info_lbl.configure(
                        text=f"iOS {b['ios_version']}  |  {b['device_name']}",
                        text_color=ACCENT,
                    )
                break

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

        self._parser = iOSBackupParser(self._backup_path, self._output_dir)
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
        """Překlad na místě – statické popisky řeší globální retranslate walk;
        tady jen znovu vykreslíme dynamický složený odznak o zařízení pro
        aktuálně vybranou zálohu. Žádný re-scan disku ani reset výstupu."""
        sel = self._found_var.get()
        if sel and getattr(self._update_backup_list, "_backups", None):
            self._on_found_selected(sel)
