import customtkinter as ctk
import datetime
from typing import Callable
from core.extractor import EXTRACTION_MODULES, AGENT_MODULES
from core.i18n import t
from .iconset import module_icon
from . import theme as th

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
    "whatsapp":       "💚",
    "viber":          "🟣",
    # Agent
    "adb_backup":     "💾",
    "root_backup":    "🔓",
    "app_providers":  "🔗",
    "usage_stats":    "📊",
    "termux_agent":   "🤖",
}

AGENT_ACCENT = "#ff9800"   # orange for agent modules


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

    # ── UI construction ───────────────────────────────────────────────────────

    def _build(self):
        # ── Toolbar ───────────────────────────────────────────────────────────
        tb = th.Toolbar(self, source=False, progress=True)
        tb.grid(row=0, column=0, sticky="ew", pady=(0, th.GAP))
        self._toolbar = tb

        self._status_label = ctk.CTkLabel(tb.info, text="", text_color=TEXT_MUTED,
                                          font=th.f_body(), anchor="w")
        self._status_label.pack(side="left")
        self._progress_label = ctk.CTkLabel(tb.info, text="", text_color=TEXT_MUTED,
                                            font=th.f_small(), anchor="w")
        self._progress_label.pack(side="left", padx=(12, 0))
        self._progress_bar = tb.progress

        self._start_btn = th.primary_button(tb.actions, t("ext.start"),
                                            self._on_start_clicked)
        self._stop_btn = th.danger_button(tb.actions, t("ext.stop"),
                                          self._on_stop_clicked, state="disabled")
        th.pack_row(self._start_btn, self._stop_btn)

        # ── Main body: log on the left, modules on the right ─────────────────
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3, minsize=380)
        body.grid_columnconfigure(1, weight=2, minsize=340)
        body.grid_rowconfigure(0, weight=1)

        self._build_log(body)
        self._build_modules(body)

    def _build_log(self, parent):
        """Left panel – extraction log."""
        self._log_card = th.LogCard(parent, t("ui.log"))
        self._log_card.grid(row=0, column=0, sticky="nsew", padx=(0, th.GAP))
        self._log_box = self._log_card.box
        self._eta_label = ctk.CTkLabel(self._log_card.header_right, text="",
                                       font=th.f_small(), text_color=TEXT_MUTED)
        self._eta_label.pack(side="right")

    def _build_modules(self, parent):
        """Right panel – module selection."""
        self._modules = th.ModuleList(parent, t("ui.modules"), t("ui.all"), t("ui.none"))
        self._modules.grid(row=0, column=1, sticky="nsew")
        self._modules_scroll = self._modules.scroll
        self._select_all_btn = self._modules.btn_all
        self._deselect_all_btn = self._modules.btn_none
        self._rebuild_module_cards(self._modules_scroll)

    def _rebuild_module_cards(self, scroll):
        for w in scroll.winfo_children():
            w.destroy()
        self._modules._row = 0

        for key in EXTRACTION_MODULES:
            self._build_module_card(key)

        self._modules.add_section(t("agent.section"), t("agent.section_desc"),
                                  color=AGENT_ACCENT)
        for key in AGENT_MODULES:
            self._build_module_card(key, accent=AGENT_ACCENT)

    def _build_module_card(self, key: str, accent: str | None = None):
        var = self._checkboxes.setdefault(key, ctk.BooleanVar(value=True))
        self._modules.add(key, t(f"mod.{key}"), var, desc=t(f"desc.{key}"),
                          image=module_icon(key, size=30),
                          emoji=MODULE_ICONS.get(key, "📁"),
                          accent=accent or self.accent)

    # ── Controls ─────────────────────────────────────────────────────────────

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
        # Timestamp in muted colour
        self._log_box.insert("end", f"[{ts}] ", "muted")
        if tag:
            self._log_box.insert("end", text + "\n", tag)
        else:
            self._log_box.insert("end", text + "\n")
        self._log_box.configure(state="disabled")
        self._log_box.see("end")

    def _log_raw(self, text: str, tag: str = ""):
        """Insert a line without a timestamp (for separators etc.)."""
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
        """Header for a new extraction session."""
        self._log_raw("─" * 48, "separator")
        self._log(t("ext.log_started"), "info")
        self._log(f"{t('ext.log_device')} {device_label}", "muted")
        self._log(f"{t('ext.log_modules')} {selected_count}", "muted")
        self._log_raw("─" * 48, "separator")

    def log_module_start(self, label: str, current: int, total: int):
        self._log_raw("", "")
        self._log(f"▶  [{current}/{total}]  {label}", "info")
        self._log(t("ext.log_running"), "muted")

    def log_module_done(self, label: str, current: int, total: int,
                        elapsed: float, avg: float, success: bool,
                        count: int, error: str):
        elapsed_str = f"{elapsed:.2f}s"

        if success:
            detail = f"{count} {t('ext.records')}" if count else t("ext.log_no_data")
            self._log(f"✓  {label}", "ok")
            self._log(f"{t('ext.log_result')} {detail}", "muted")
            self._log(f"{t('ext.log_duration')} {elapsed_str}", "muted")
        else:
            self._log(f"✗  {label}", "err")
            self._log(f"{t('ext.log_error_lbl')} {error}", "err")
            self._log(f"{t('ext.log_duration')} {elapsed_str}", "muted")

        remaining = total - current
        if remaining > 0:
            eta_sec = int(avg * remaining)
            eta_str = f"{eta_sec // 60}m {eta_sec % 60}s" if eta_sec >= 60 else f"{eta_sec}s"
            self._log(t("ext.log_remaining").format(n=remaining, eta=eta_str), "muted")
            self._eta_label.configure(text=t("ext.eta").format(eta=eta_str, n=remaining))
        else:
            self._eta_label.configure(text="")

    # ── Extraction state ─────────────────────────────────────────────────────

    def set_running(self, running: bool):
        self._running = running
        if running:
            self._extraction_start = datetime.datetime.now()
            self._start_btn.configure(state="disabled")
            self._stop_btn.configure(state="normal")
            self._progress_bar.set(0)
            self._status_label.configure(text=t("ext.running"), text_color=TEXT_MUTED)
            self.log_clear()
        else:
            self._start_btn.configure(state="normal")
            self._stop_btn.configure(state="disabled")
            self._progress_label.configure(text="")

    def update_progress(self, module_label: str, current: int, total: int):
        progress = current / total if total > 0 else 0
        self._progress_bar.set(progress)
        self._progress_label.configure(text=f"[{current}/{total}] {module_label}")
        self.log_module_start(module_label, current, total)

    def log_disconnected(self):
        """Warn the user that the device was disconnected and extraction is paused."""
        self._log_raw("", "")
        self._log_raw("⚠  " + "─" * 42, "warn")
        self._log(t("ext.disconnected_warn"), "warn")
        self._log(t("ext.waiting_reconnect"), "warn")
        self._log_raw("⚠  " + "─" * 42, "warn")
        self._status_label.configure(
            text=t("ext.disconnected_short"), text_color=ORANGE
        )

    def log_reconnected(self):
        """Notify the user that the device reconnected and extraction continues."""
        self._log_raw("", "")
        self._log(t("ext.reconnected"), "ok")
        self._status_label.configure(text=t("ext.running"), text_color=TEXT_MUTED)

    def log_notify(self, msg: str):
        """Show an action-required notification in the log (e.g. confirm ADB backup on device)."""
        self._log_raw("", "")
        self._log_raw("⚠  " + "─" * 42, "warn")
        for line in msg.splitlines():
            self._log_raw(f"   {line}", "warn")
        self._log_raw("⚠  " + "─" * 42, "warn")
        self._status_label.configure(
            text=t("ext.waiting_confirm"), text_color=ORANGE
        )

    def log_copy_progress(self, msg: str):
        """Running log of file copying (called from the extractor thread)."""
        self._log(msg, "muted")

    def log_stopping(self):
        """Message shown immediately after clicking Stop."""
        self._log_raw("", "")
        self._log_raw("⚠  " + "─" * 42, "warn")
        self._log(t("ext.stopping_log1"), "warn")
        self._log(t("ext.stopping_log2"), "warn")
        self._log_raw("⚠  " + "─" * 42, "warn")
        self._status_label.configure(text=t("ext.stopping"), text_color=ORANGE)

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

    def show_stopped(self, success_count: int):
        """Final message after stopping – shows what was extracted in time."""
        elapsed = ""
        if self._extraction_start:
            secs = int((datetime.datetime.now() - self._extraction_start).total_seconds())
            t_str = f"{secs // 60}m {secs % 60}s"
            elapsed = f"  |  {t('ext.done_elapsed').format(t=t_str)}"

        self._log_raw("", "")
        self._log_raw("─" * 48, "separator")
        self._log(f"■  {t('ext.stopped_log').format(n=success_count)}{elapsed}", "warn")
        self._log("   " + t("ext.stopped_saved"), "muted")
        self._log_raw("─" * 48, "separator")
        self._eta_label.configure(text="")
        self._status_label.configure(text=t("ext.stopped_status").format(n=success_count), text_color=ORANGE)

    # ── Language refresh ─────────────────────────────────────────────────────

    def refresh_lang(self):
        self._select_all_btn.configure(text=t("ui.all"))
        self._deselect_all_btn.configure(text=t("ui.none"))
        self._modules.title.configure(text=t("ui.modules"))
        self._log_card.title.configure(text=t("ui.log"))
        self._start_btn.configure(text=t("ext.start"))
        self._stop_btn.configure(text=t("ext.stop"))
        self._rebuild_module_cards(self._modules_scroll)
