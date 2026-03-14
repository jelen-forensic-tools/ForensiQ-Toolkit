import customtkinter as ctk
import datetime
from typing import Callable
from core.extractor import EXTRACTION_MODULES, AGENT_MODULES
from core.i18n import t

ACCENT     = "#00d4ff"
BG_CARD    = "#16213e"
BG_PANEL   = "#1a1a2e"
BG_LOG     = "#0a0f1e"
TEXT_MUTED = "#888888"
GREEN      = "#00c853"
RED        = "#ff5252"
ORANGE     = "#ff9800"

MODULE_ICONS = {
    "device_info":    "📋",
    "contacts":       "👥",
    "sms":            "💬",
    "call_logs":      "📞",
    "apps":           "📦",
    "media":          "🖼",
    "documents":      "📄",
    "wifi":           "📶",
    "accounts":       "👤",
    "browser":        "🌐",
    "user_data_tree": "🗂",
    "file_analysis":  "🔍",
    "file_hashes":    "#️",
    "deleted_data":   "🗑",
    # Agent
    "adb_backup":     "💾",
    "app_providers":  "🔗",
    "usage_stats":    "📊",
    "termux_agent":   "🤖",
}

AGENT_ACCENT = "#ff9800"   # oranžová pro agentní moduly


class ExtractionPanel(ctk.CTkFrame):
    def __init__(self, master, accent: str = ACCENT,
                 on_start: Callable = None, on_stop: Callable = None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.accent = accent
        self.on_start = on_start
        self.on_stop = on_stop
        self._running = False
        self._checkboxes: dict[str, ctk.BooleanVar] = {}
        self._extraction_start: datetime.datetime | None = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()

    # ── Stavba UI ─────────────────────────────────────────────────────────────

    def _build(self):
        # ── Horní lišta ───────────────────────────────────────────────────────
        action_card = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12)
        action_card.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        action_card.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(action_card, fg_color="transparent")
        left.grid(row=0, column=0, padx=20, pady=14, sticky="w")

        self._select_all_btn = ctk.CTkButton(
            left, text=t("ext.select_all"), width=110, height=32,
            fg_color="#2a2a4a", hover_color="#3a3a5a",
            font=ctk.CTkFont(size=12),
            command=self._select_all,
        )
        self._select_all_btn.pack(side="left", padx=(0, 8))

        self._deselect_all_btn = ctk.CTkButton(
            left, text=t("ext.deselect_all"), width=110, height=32,
            fg_color="#2a2a4a", hover_color="#3a3a5a",
            font=ctk.CTkFont(size=12),
            command=self._deselect_all,
        )
        self._deselect_all_btn.pack(side="left")

        right = ctk.CTkFrame(action_card, fg_color="transparent")
        right.grid(row=0, column=2, padx=20, pady=14)

        self._status_label = ctk.CTkLabel(
            right, text="", text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=12),
        )
        self._status_label.pack(side="left", padx=(0, 15))

        self._start_btn = ctk.CTkButton(
            right, text=t("ext.start"),
            width=150, height=38,
            fg_color=self.accent, text_color="#000",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._on_start_clicked,
        )
        self._start_btn.pack(side="left", padx=(0, 8))

        self._stop_btn = ctk.CTkButton(
            right, text=t("ext.stop"),
            width=90, height=38,
            fg_color="#f44336", text_color="#fff",
            font=ctk.CTkFont(size=13),
            command=self._on_stop_clicked,
            state="disabled",
        )
        self._stop_btn.pack(side="left")

        # Progress bar (skrytý)
        self._progress_frame = ctk.CTkFrame(action_card, fg_color="transparent")
        self._progress_bar = ctk.CTkProgressBar(
            self._progress_frame, width=300, height=8, progress_color=self.accent,
        )
        self._progress_bar.set(0)
        self._progress_label = ctk.CTkLabel(
            self._progress_frame, text="",
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
        )

        # ── Hlavní tělo: log vlevo, moduly vpravo ─────────────────────────────
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3, minsize=380)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        self._build_log(body)
        self._build_modules(body)

    def _build_log(self, parent):
        """Levý panel – log extrakce."""
        log_card = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12)
        log_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        log_card.grid_columnconfigure(0, weight=1)
        log_card.grid_rowconfigure(2, weight=1)

        # Hlavička logu
        hdr = ctk.CTkFrame(log_card, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        hdr.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            hdr, text=t("ext.log_title"),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=ACCENT,
        ).grid(row=0, column=0, sticky="w")

        self._eta_label = ctk.CTkLabel(
            hdr, text="",
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
        )
        self._eta_label.grid(row=0, column=1, sticky="e")

        ctk.CTkFrame(log_card, height=1, fg_color="#2a2a3e").grid(
            row=1, column=0, sticky="ew", padx=14, pady=(0, 0)
        )

        # Textové pole
        self._log_box = ctk.CTkTextbox(
            log_card,
            fg_color=BG_LOG,
            text_color="#cccccc",
            font=ctk.CTkFont(family="Consolas", size=11),
            corner_radius=8,
            state="disabled",
            wrap="word",
        )
        self._log_box.grid(row=2, column=0, sticky="nsew", padx=10, pady=(6, 10))

        # Barevné tagy
        self._log_box.tag_config("ok",        foreground=GREEN)
        self._log_box.tag_config("err",       foreground=RED)
        self._log_box.tag_config("info",      foreground=ACCENT)
        self._log_box.tag_config("warn",      foreground=ORANGE)
        self._log_box.tag_config("muted",     foreground=TEXT_MUTED)
        self._log_box.tag_config("separator", foreground="#2a3a5a")
        self._log_box.tag_config("bold_ok",   foreground=GREEN)

    def _build_modules(self, parent):
        """Pravý panel – výběr modulů."""
        right = ctk.CTkFrame(parent, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)

        self._modules_scroll = ctk.CTkScrollableFrame(right, fg_color="transparent")
        self._modules_scroll.grid(row=0, column=0, sticky="nsew")
        self._modules_scroll.grid_columnconfigure(0, weight=1)

        self._rebuild_module_cards(self._modules_scroll)

    def _rebuild_module_cards(self, scroll):
        for w in scroll.winfo_children():
            w.destroy()

        # ── Standardní moduly ─────────────────────────────────────────────────
        modules = list(EXTRACTION_MODULES.items())
        for i, (key, _) in enumerate(modules):
            self._build_module_card(scroll, key, row=i, col=0)

        # ── Sekce Agent ───────────────────────────────────────────────────────
        section_row = len(modules)

        section = ctk.CTkFrame(scroll, fg_color="#1a1a0a", corner_radius=10)
        section.grid(row=section_row, column=0, columnspan=1,
                     sticky="ew", padx=5, pady=(18, 6))
        section.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            section, text="🤖",
            font=ctk.CTkFont(size=20),
        ).grid(row=0, column=0, rowspan=2, padx=(14, 8), pady=10)

        ctk.CTkLabel(
            section, text=t("agent.section"),
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=AGENT_ACCENT,
            anchor="w",
        ).grid(row=0, column=1, sticky="w", pady=(8, 0))

        ctk.CTkLabel(
            section, text=t("agent.section_desc"),
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
            anchor="w",
        ).grid(row=1, column=1, sticky="w", pady=(0, 8))

        agent_row_start = section_row + 1
        for i, (key, _) in enumerate(AGENT_MODULES.items()):
            self._build_module_card(
                scroll, key,
                row=agent_row_start + i,
                col=0,
                accent=AGENT_ACCENT,
            )

    def _build_module_card(self, parent, key: str, row: int, col: int,
                           accent: str | None = None):
        if key not in self._checkboxes:
            var = ctk.BooleanVar(value=True)
            self._checkboxes[key] = var
        else:
            var = self._checkboxes[key]

        cb_accent = accent or self.accent
        label = t(f"mod.{key}")
        desc  = t(f"desc.{key}")

        card = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=10)
        card.grid(row=row, column=col, sticky="ew", padx=5, pady=5)
        card.grid_columnconfigure(1, weight=1)

        icon = MODULE_ICONS.get(key, "📁")

        ctk.CTkLabel(card, text=icon, font=ctk.CTkFont(size=24), width=45).grid(
            row=0, column=0, rowspan=2, padx=(15, 5), pady=15,
        )
        ctk.CTkLabel(
            card, text=label,
            font=ctk.CTkFont(size=13, weight="bold"), anchor="w",
        ).grid(row=0, column=1, padx=5, pady=(12, 1), sticky="w")

        ctk.CTkLabel(
            card, text=desc,
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
            anchor="w", wraplength=260,
        ).grid(row=1, column=1, padx=5, pady=(0, 12), sticky="w")

        ctk.CTkCheckBox(
            card, text="", variable=var, width=24, height=24,
            checkbox_width=22, checkbox_height=22,
            checkmark_color="#000", fg_color=cb_accent,
        ).grid(row=0, column=2, rowspan=2, padx=15)

    # ── Ovládání ─────────────────────────────────────────────────────────────

    def _select_all(self):
        for var in self._checkboxes.values():
            var.set(True)

    def _deselect_all(self):
        for var in self._checkboxes.values():
            var.set(False)

    def _on_start_clicked(self):
        selected = [key for key, var in self._checkboxes.items() if var.get()]
        if not selected:
            return
        if self.on_start:
            self.on_start(selected)

    def _on_stop_clicked(self):
        if self.on_stop:
            self.on_stop()

    # ── Log ──────────────────────────────────────────────────────────────────

    def _log(self, text: str, tag: str = ""):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._log_box.configure(state="normal")
        # Timestamp v tlumené barvě
        self._log_box.insert("end", f"[{ts}] ", "muted")
        if tag:
            self._log_box.insert("end", text + "\n", tag)
        else:
            self._log_box.insert("end", text + "\n")
        self._log_box.configure(state="disabled")
        self._log_box.see("end")

    def _log_raw(self, text: str, tag: str = ""):
        """Vloží řádek bez timestamp (pro oddělovače apod.)."""
        self._log_box.configure(state="normal")
        if tag:
            self._log_box.insert("end", text + "\n", tag)
        else:
            self._log_box.insert("end", text + "\n")
        self._log_box.configure(state="disabled")
        self._log_box.see("end")

    def log_clear(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")
        self._eta_label.configure(text="")

    def log_session_start(self, device_label: str, selected_count: int):
        """Záhlaví nové extrakce."""
        self._log_raw("─" * 48, "separator")
        self._log(f"EXTRAKCE ZAHÁJENA", "info")
        self._log(f"Zařízení  : {device_label}", "muted")
        self._log(f"Modulů    : {selected_count}", "muted")
        self._log_raw("─" * 48, "separator")

    def log_module_start(self, label: str, current: int, total: int):
        self._log_raw("", "")
        self._log(f"▶  [{current}/{total}]  {label}", "info")
        self._log(f"   Spouštím extrakci dat…", "muted")

    def log_module_done(self, label: str, current: int, total: int,
                        elapsed: float, avg: float, success: bool,
                        count: int, error: str):
        elapsed_str = f"{elapsed:.2f}s"

        if success:
            detail = f"{count} {t('ext.records')}" if count else "OK (žádná data)"
            self._log(f"✓  {label}", "ok")
            self._log(f"   Výsledek  : {detail}", "muted")
            self._log(f"   Trvání    : {elapsed_str}", "muted")
        else:
            self._log(f"✗  {label}", "err")
            self._log(f"   Chyba     : {error}", "err")
            self._log(f"   Trvání    : {elapsed_str}", "muted")

        remaining = total - current
        if remaining > 0:
            eta_sec = int(avg * remaining)
            eta_str = f"{eta_sec // 60}m {eta_sec % 60}s" if eta_sec >= 60 else f"{eta_sec}s"
            self._log(f"   Zbývá     : {remaining} modulů  (~{eta_str})", "muted")
            self._eta_label.configure(text=t("ext.eta").format(eta=eta_str, n=remaining))
        else:
            self._eta_label.configure(text="")

    # ── Stav extrakce ────────────────────────────────────────────────────────

    def set_running(self, running: bool):
        self._running = running
        if running:
            self._extraction_start = datetime.datetime.now()
            self._start_btn.configure(state="disabled")
            self._stop_btn.configure(state="normal")
            self._progress_frame.grid(
                row=1, column=0, columnspan=3, padx=20, pady=(0, 15), sticky="ew",
            )
            self._progress_bar.pack(side="left", padx=(0, 10))
            self._progress_label.pack(side="left")
            self._progress_bar.set(0)
            self._status_label.configure(text=t("ext.running"), text_color=TEXT_MUTED)
            self.log_clear()
        else:
            self._start_btn.configure(state="normal")
            self._stop_btn.configure(state="disabled")
            self._progress_frame.grid_remove()

    def update_progress(self, module_label: str, current: int, total: int):
        progress = current / total if total > 0 else 0
        self._progress_bar.set(progress)
        self._progress_label.configure(text=f"[{current}/{total}] {module_label}")
        self.log_module_start(module_label, current, total)

    def show_done(self, success_count: int):
        elapsed = ""
        if self._extraction_start:
            secs = int((datetime.datetime.now() - self._extraction_start).total_seconds())
            t_str = f"{secs // 60}m {secs % 60}s"
            elapsed = f"  |  {t('ext.done_elapsed').format(t=t_str)}"

        done_msg = t("ext.done").format(n=success_count)
        self._log_raw("", "")
        self._log_raw("─" * 48, "separator")
        self._log(f"═  {done_msg}{elapsed}", "ok")
        self._log_raw("─" * 48, "separator")
        self._eta_label.configure(text="")
        self._status_label.configure(text=done_msg, text_color="#4CAF50")

    # ── Překlad ──────────────────────────────────────────────────────────────

    def refresh_lang(self):
        self._select_all_btn.configure(text=t("ext.select_all"))
        self._deselect_all_btn.configure(text=t("ext.deselect_all"))
        self._start_btn.configure(text=t("ext.start"))
        self._stop_btn.configure(text=t("ext.stop"))
        self._rebuild_module_cards(self._modules_scroll)
