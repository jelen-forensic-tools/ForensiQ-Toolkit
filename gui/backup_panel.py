"""
gui/backup_panel.py – Panel for importing and extracting data from mobile backups.
"""
from __future__ import annotations

import os
import sys
import datetime
from pathlib import Path
from tkinter import filedialog
import customtkinter as ctk
from PIL import Image
from core.i18n import t
from .iconset import module_icon
from . import theme as th
from core.backup_importer import BackupImporter, BACKUP_FORMATS, IMPORT_MODULES, _fmt_label, _mod_label

ACCENT      = "#00d4ff"
BG_CARD     = "#16213e"
BG_PANEL    = "#1a1a2e"
BG_LEFT     = "#0f1a30"
BG_DARK     = "#0a0f1e"
TEXT_MUTED  = "#888888"
TEXT_DIM    = "#555555"
GREEN       = "#00c853"
GREEN_DIM   = "#1a3d28"
RED         = "#ff5252"
ORANGE      = "#ff9800"
ORANGE_DIM  = "#3d2800"
WHITE       = "#ffffff"

# Format metadata for UI: key → (icon, i18n_sub_key, icon_color)
SEL_BG = "#1e2a4a"
# ikony modulů importu (assets/icons/<key>.png)
MOD_ICON_KEYS = {
    "bkp_contacts": "contacts", "bkp_sms": "sms", "bkp_calls": "call_logs",
    "bkp_media": "media", "bkp_wifi": "wifi", "bkp_calendar": "calendar",
    "bkp_browser": "browser", "bkp_notes": "notes", "bkp_apps": "apps",
}

FORMAT_META: dict[str, tuple[str, str, str]] = {
    "auto":    ("🔍", "bkp.auto_sub",    ACCENT),
    "samsung": ("🔷", "bkp.samsung_sub", "#1e90ff"),
    "huawei":  ("🔶", "bkp.huawei_sub",  ORANGE),
    "miui":    ("🟠", "bkp.miui_sub",    ORANGE),
    "takeout": ("🟢", "bkp.takeout_sub", GREEN),
    "sony":    ("🟣", "bkp.sony_sub",    "#9c27b0"),
    "oneplus": ("🔴", "bkp.oneplus_sub", RED),
    "oppo":    ("🟩", "bkp.oppo_sub",    "#2e7d32"),
    "vivo":    ("🔵", "bkp.vivo_sub",    "#1565c0"),
    "motorola": ("🟦", "bkp.motorola_sub", "#0d47a1"),
    "lg":      ("🩷", "bkp.lg_sub",      "#d81b60"),
    "adb":     ("⚫", "bkp.adb_sub",     TEXT_MUTED),
}


class BackupImportPanel(ctk.CTkFrame):
    def __init__(self, master, accent: str = ACCENT, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.accent = accent
        self._selected_fmt = ctk.StringVar(value="auto")
        self._fmt_btns: dict[str, ctk.CTkFrame] = {}
        self._backup_path: str = ""
        self._output_dir: str = ""
        self._running = False
        self._importer: BackupImporter | None = None
        self._module_vars: dict[str, ctk.BooleanVar] = {
            k: ctk.BooleanVar(value=True) for k in IMPORT_MODULES
        }
        self._fmt_icons = self._load_fmt_icons()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()

    def _load_fmt_icons(self) -> dict:
        """Načte značkové ikony formátů (funguje ze zdrojáků i z .exe balíčku)."""
        if getattr(sys, "frozen", False):
            base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        else:
            base = os.path.dirname(os.path.dirname(__file__))
        folder = os.path.join(base, "assets", "formats")
        icons: dict = {}
        for key in BACKUP_FORMATS:
            p = os.path.join(folder, f"{key}.png")
            if os.path.isfile(p):
                try:
                    im = Image.open(p)
                    icons[key] = ctk.CTkImage(light_image=im, dark_image=im, size=(34, 34))
                except Exception:
                    pass
        return icons

    # ── UI construction ───────────────────────────────────────────────────────

    def _build(self):
        self.grid_rowconfigure(1, weight=1)

        self._build_topbar(self)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3, minsize=380)
        body.grid_columnconfigure(1, weight=2, minsize=340)
        body.grid_rowconfigure(0, weight=1)

        self._build_log(body)
        self._build_config(body)

    def _build_topbar(self, parent):
        """Horní lišta – výběr zdroje + akce (výstupní složka se volí automaticky)."""
        tb = th.Toolbar(parent)
        tb.grid(row=0, column=0, sticky="ew", pady=(0, th.GAP))

        th.pack_row(
            th.secondary_button(tb.source, t("bkp.pick_file"), self._pick_file),
            th.secondary_button(tb.source, t("bkp.pick_folder"), self._pick_folder),
        )
        self._detected_badge = ctk.CTkLabel(tb.source, text="", font=th.f_small(),
                                            text_color=ACCENT, anchor="w")
        self._detected_badge.pack(side="left", padx=(16, 0))

        self._status_bar = ctk.CTkLabel(tb.info, text="", font=th.f_body(),
                                        text_color=TEXT_MUTED, anchor="w")
        self._status_bar.pack(side="left")

        self._btn_run = th.primary_button(tb.actions, t("bkp.run"), self._run_import)
        self._btn_stop = th.danger_button(tb.actions, t("ext.stop"), self._stop_import,
                                          state="disabled")
        th.pack_row(self._btn_run, self._btn_stop)

    def _build_log(self, parent):
        """Levý panel – log importu."""
        self._log_card = th.LogCard(parent, t("ui.log"))
        self._log_card.grid(row=0, column=0, sticky="nsew", padx=(0, th.GAP))
        self._log_box = self._log_card.box

    def _build_config(self, parent):
        """Pravý panel – výběr formátu a modulů."""
        self._modules = th.ModuleList(parent)
        self._modules.grid(row=0, column=1, sticky="nsew")
        right = self._modules.scroll

        self._modules.add_section(t("bkp.format_title"))
        for fmt_key in BACKUP_FORMATS:
            icon, sub_key, _ = FORMAT_META.get(fmt_key, ("📦", "", ACCENT))
            frame = self._format_btn(right, row=self._modules.next_row(), icon=icon,
                                     title=_fmt_label(fmt_key), sub=t(sub_key), value=fmt_key)
            self._fmt_btns[fmt_key] = frame

        self._modules.add_section(t("ui.modules"), all_text=t("ui.all"),
                                  none_text=t("ui.none"))
        for key in IMPORT_MODULES:
            self._modules.add(key, _mod_label(key), self._module_vars[key],
                              image=module_icon(MOD_ICON_KEYS.get(key, key), size=30),
                              emoji="📁")

    def _format_btn(self, parent, row, icon, title, sub, value) -> ctk.CTkFrame:
        is_sel = self._selected_fmt.get() == value
        frame = ctk.CTkFrame(
            parent, fg_color=SEL_BG if is_sel else BG_CARD,
            border_width=1, border_color=ACCENT if is_sel else BG_CARD,
            corner_radius=th.RADIUS_ITEM, cursor="hand2",
        )
        frame.grid(row=row, column=0, padx=4, pady=3, sticky="ew")
        frame.grid_columnconfigure(1, weight=1)

        img = self._fmt_icons.get(value)
        if img is not None:
            ctk.CTkLabel(frame, text="", image=img, width=36).grid(
                row=0, column=0, rowspan=2, padx=(12, 8), pady=10)
        else:
            ctk.CTkLabel(frame, text=icon, font=ctk.CTkFont(size=20), width=36).grid(
                row=0, column=0, rowspan=2, padx=(12, 8), pady=10)
        ctk.CTkLabel(frame, text=title, font=th.f_bold(), text_color=WHITE,
                     anchor="w").grid(row=0, column=1, sticky="w", pady=(10, 0), padx=(0, 8))
        ctk.CTkLabel(frame, text=sub, font=th.f_small(), text_color=TEXT_MUTED,
                     anchor="w").grid(row=1, column=1, sticky="w", pady=(0, 10), padx=(0, 8))

        for w in frame.winfo_children():
            w.bind("<Button-1>", lambda e, v=value: self._select_format(v))
        frame.bind("<Button-1>", lambda e, v=value: self._select_format(v))
        return frame

    # ── Interactions ──────────────────────────────────────────────────────────

    def _select_format(self, value: str):
        self._selected_fmt.set(value)
        for fmt_key, frame in self._fmt_btns.items():
            sel = fmt_key == value
            frame.configure(fg_color=SEL_BG if sel else BG_CARD,
                            border_color=ACCENT if sel else BG_CARD)

    def _pick_file(self):
        path = filedialog.askopenfilename(
            title=t("bkp.pick_file_title"),
            filetypes=[
                (t("bkp.all_backups"), "*.zip *.hbk *.bak *.bk *.ab *.gz *.tar *.tgz"),
                ("Samsung SmartSwitch", "*.zip"),
                ("Huawei HiSuite",      "*.hbk"),
                ("MIUI Backup",         "*.bak"),
                ("Sony Xperia",         "*.bk"),
                ("ADB Backup",          "*.ab"),
                ("TAR/GZ",              "*.gz *.tar *.tgz"),
                (t("bkp.all_files"),    "*.*"),
            ],
        )
        if path:
            self._set_backup_path(path)

    def _pick_folder(self):
        path = filedialog.askdirectory(title=t("bkp.pick_folder_title"))
        if path:
            self._set_backup_path(path)

    def _set_backup_path(self, path: str):
        self._backup_path = path
        p = Path(path)
        name = p.name if p.is_file() else p.name or str(p)

        # Auto-detect backup format – shown as the only on-screen feedback
        tmp = BackupImporter(path, "", "auto")
        detected = tmp.detect_format()
        self._detected_fmt = detected
        if detected and detected != "unknown":
            self._detected_badge.configure(
                text=f"{name}  →  {_fmt_label(detected)}",
                text_color=ACCENT,
            )
        else:
            self._detected_badge.configure(text=name, text_color=TEXT_MUTED)

        # Always pick a fresh default output directory (chosen automatically)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._output_dir = os.path.join(
            os.path.expanduser("~"), "Documents",
            f"ForenziQ_Import_{p.stem}_{ts}",
        )

    def _run_import(self):
        if not self._backup_path:
            self._status_bar.configure(
                text=t("bkp.err_no_source"), text_color=RED
            )
            return

        selected = [k for k, v in self._module_vars.items() if v.get()]
        if not selected:
            self._status_bar.configure(
                text=t("bkp.err_no_modules"), text_color=RED
            )
            return

        if not self._output_dir:
            self._status_bar.configure(
                text=t("bkp.err_no_output"), text_color=RED
            )
            return

        self._running = True
        self._btn_run.configure(state="disabled", text=t("bkp.running"))
        self._btn_stop.configure(state="normal")
        self._status_bar.configure(text=t("bkp.running"), text_color=ACCENT)
        self._log_clear()

        fmt = self._selected_fmt.get()
        self._importer = BackupImporter(self._backup_path, self._output_dir, fmt)
        self._importer.run(
            modules=selected,
            log=self._log_append_ts,
            done=self._on_done,
            fmt=fmt,
        )

    def _stop_import(self):
        if self._importer:
            self._importer.stop()
        self._btn_stop.configure(state="disabled")
        self._status_bar.configure(text=t("bkp.stopped_msg") + "…", text_color=ORANGE)

    def _on_done(self, success: bool, msg: str):
        def _update():
            self._running = False
            self._btn_run.configure(state="normal", text=t("bkp.run"))
            self._btn_stop.configure(state="disabled")
            color = GREEN if success else ORANGE if "zastaven" in msg.lower() or "stopped" in msg.lower() else RED
            self._status_bar.configure(
                text=("✔ " if success else "■ " if "zastaven" in msg.lower() or "stopped" in msg.lower() else "✗ ") + msg,
                text_color=color,
            )
        self.after(0, _update)

    # ── Log helpers ──────────────────────────────────────────────────────────

    def _log_clear(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    def _log_append(self, text: str):
        self._log_box.configure(state="normal")
        self._log_box.insert("end", text + "\n")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _log_append_ts(self, text: str):
        """Thread-safe log append with timestamp prefix."""
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.after(0, lambda t=text, s=ts: self._log_append(f"[{s}] {t}"))

    def refresh_lang(self):
        """Překlad na místě – statické popisky (formáty, moduly, nadpisy) řeší
        globální retranslate walk; tady jen znovu vykreslíme dynamický detekční
        odznak, bez nové detekce a bez resetu výstupní složky."""
        if self._backup_path:
            name = Path(self._backup_path).name or self._backup_path
            detected = getattr(self, "_detected_fmt", None)
            if detected and detected != "unknown":
                self._detected_badge.configure(
                    text=f"{name}  →  {_fmt_label(detected)}", text_color=ACCENT)
            else:
                self._detected_badge.configure(text=name, text_color=TEXT_MUTED)
