import customtkinter as ctk
import threading
import datetime
import os
import sys
from tkinter import filedialog
from .dashboard import DeviceDashboard
from .extraction import ExtractionPanel
from .comparison import ComparisonPanel
from .timeline import TimelinePanel
from .wifi_dialog import WifiConnectDialog
from .bypass_panel import BypassPanel
from .map_panel import MapPanel
from .backup_panel import BackupImportPanel
from .results_panel import ResultsPanel
from .ios_panel import iOSPanel
from .ios_backup_panel import iOSBackupPanel
from .ios_ramdisk_panel import iOSRamdiskPanel
from core.adb_manager import ADBManager
from core.extractor import Extractor, EXTRACTION_MODULES
from core.report import generate_report, generate_pdf_report, generate_excel_report
from core.evidence import generate_manifest, verify_manifest, MANIFEST_TXT
from core.i18n import t, set_lang, get_lang, retranslate


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

ACCENT     = "#00d4ff"
BG_DARK    = "#0f0f1a"
BG_CARD    = "#16213e"
BG_PANEL   = "#1a1a2e"
TEXT_MUTED = "#888888"


def _detect_scale() -> float:
    """Return the scale factor based on the primary monitor resolution."""
    import tkinter as _tk
    root = _tk.Tk()
    root.withdraw()
    w = root.winfo_screenwidth()
    root.destroy()
    if w >= 3840:
        return 1.6
    if w >= 2560:
        return 1.3
    if w >= 1920:
        return 1.1
    return 1.0


class ForenziQApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Scale based on monitor resolution
        self._scale = _detect_scale()
        if self._scale != 1.0:
            ctk.set_widget_scaling(self._scale)
            ctk.set_window_scaling(self._scale)

        self.title("ForenziQ Toolkit")
        self._set_app_icon()
        # Default geometry adapted to resolution
        w = int(1280 * self._scale)
        h = int(800 * self._scale)
        self.geometry(f"{w}x{h}")
        self.minsize(int(1024 * self._scale), int(680 * self._scale))
        self.configure(fg_color=BG_DARK)
        # Spustit maximalizované přes celou obrazovku (horní lišta a ovládání
        # okna zůstávají; výše nastavená velikost slouží jako obnovená velikost).
        self.after(0, lambda: self.state("zoomed"))

        self.adb = ADBManager()
        self.extractor: Extractor | None = None
        self.current_device: dict | None = None
        self.device_info: dict = {}
        self.output_dir = ""
        self._extraction_results: list = []
        self._current_view = "dashboard"
        self._last_device_serials: list = []   # cache pro smart polling
        self._closing = False
        self._poll_after_id = None

        self._build_ui()
        self._check_adb()
        self._start_device_polling()
        # Čisté zavření – zastaví polling i běžící úlohy, pak zničí okno naráz
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        """Zastaví naplánované callbacky a vlákna, pak zavře okno v jednom kroku
        (zabrání „rozpadu" vykreslení při postupném ničení widgetů)."""
        if self._closing:
            return
        self._closing = True
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except Exception:
                pass
        try:
            if self.extractor:
                self.extractor.stop()
        except Exception:
            pass
        try:
            self.quit()      # ukončí mainloop
        finally:
            self.destroy()   # zničí okno naráz

    # ── UI construction ──────────────────────────────────────────────────────

    def _set_app_icon(self):
        """Nastaví ikonu okna – funguje ze zdrojáků i z .exe balíčku."""
        if getattr(sys, "frozen", False):
            base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        else:
            base = os.path.dirname(os.path.dirname(__file__))
        ico = os.path.join(base, "assets", "forenziq.ico")
        try:
            if os.path.exists(ico):
                self.iconbitmap(ico)
        except Exception:
            pass

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_main()

    def _build_sidebar(self):
        sidebar_w = int(230 * self._scale)
        sidebar = ctk.CTkFrame(self, width=sidebar_w, fg_color=BG_PANEL, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(1, weight=1)   # nav scroll expands
        sidebar.grid_columnconfigure(0, weight=1)

        # ── Top: logo + language (fixed) ──────────────────────────────────
        top_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        top_frame.grid(row=0, column=0, sticky="ew")
        top_frame.grid_columnconfigure(0, weight=1)

        logo_frame = ctk.CTkFrame(top_frame, fg_color="transparent")
        logo_frame.grid(row=0, column=0, padx=20, pady=(22, 4), sticky="w")

        ctk.CTkLabel(
            logo_frame, text="ForenziQ",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=ACCENT
        ).pack(anchor="w")
        ctk.CTkLabel(
            logo_frame, text="Toolkit",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#ffffff"
        ).pack(anchor="w")
        ctk.CTkLabel(
            logo_frame, text="v1.0",
            font=ctk.CTkFont(size=10),
            text_color=TEXT_MUTED
        ).pack(anchor="w")

        lang_frame_top = ctk.CTkFrame(top_frame, fg_color="transparent")
        lang_frame_top.grid(row=1, column=0, padx=20, pady=(0, 10), sticky="w")
        lang_frame_top.grid_columnconfigure((0, 1), weight=1)

        self._btn_cs = ctk.CTkButton(
            lang_frame_top, text="CS", width=44, height=24,
            fg_color=ACCENT, text_color="#000",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=lambda: self._set_lang("cs")
        )
        self._btn_cs.grid(row=0, column=0, padx=(0, 4))

        self._btn_en = ctk.CTkButton(
            lang_frame_top, text="EN", width=44, height=24,
            fg_color="#2a2a4a", text_color="#cccccc",
            font=ctk.CTkFont(size=11),
            command=lambda: self._set_lang("en")
        )
        self._btn_en.grid(row=0, column=1)

        ctk.CTkFrame(top_frame, height=1, fg_color="#2a2a3e").grid(
            row=2, column=0, sticky="ew", padx=15, pady=5)

        # ── Middle: scrollable navigation ─────────────────────────────────
        nav_scroll = ctk.CTkScrollableFrame(
            sidebar, fg_color="transparent",
            scrollbar_button_color="#2a2a4a",
            scrollbar_button_hover_color="#3a3a6a",
        )
        nav_scroll.grid(row=1, column=0, sticky="nsew")
        nav_scroll.grid_columnconfigure(0, weight=1)

        nav_items = [
            ("overview",   "nav.overview"),
            ("extraction", "nav.extraction"),
            ("results",    "nav.results"),
            ("backup",     "nav.backup"),
            ("ios",         "nav.ios"),
            ("ios_backup",  "nav.ios_backup"),
            ("ios_ramdisk", "nav.ios_ramdisk"),
            ("comparison",  "nav.comparison"),
            ("timeline",   "nav.timeline"),
            ("map",        "nav.map"),
            ("bypass",     "nav.bypass"),
        ]
        self._nav_key_map = {
            "overview":   "dashboard",
            "extraction": "extraction",
            "results":    "results",
            "comparison": "comparison",
            "timeline":   "timeline",
            "map":        "map",
            "bypass":     "bypass",
            "backup":     "backup",
            "ios":         "ios",
            "ios_backup":  "ios_backup",
            "ios_ramdisk": "ios_ramdisk",
        }
        self._nav_buttons = {}

        row = 0

        for nav_key, label_key in nav_items:
            view_key = self._nav_key_map[nav_key]
            btn = ctk.CTkButton(
                nav_scroll, text=t(label_key),
                font=ctk.CTkFont(size=14),
                fg_color="transparent", text_color="#cccccc",
                hover_color="#2a2a3e", anchor="w", height=42,
                command=lambda k=view_key: self._navigate(k)
            )
            btn.grid(row=row, column=0, padx=8, pady=2, sticky="ew")
            self._nav_buttons[view_key] = btn
            row += 1

        # Wi-Fi – same look as the other items
        self._wifi_btn = ctk.CTkButton(
            nav_scroll, text=t("nav.wifi"),
            font=ctk.CTkFont(size=14),
            fg_color="transparent", text_color="#cccccc",
            hover_color="#2a2a3e", anchor="w", height=42,
            command=self._open_wifi_dialog
        )
        self._wifi_btn.grid(row=row, column=0, padx=8, pady=2, sticky="ew")

        # ── Bottom: ADB status (fixed) ────────────────────────────────────
        ctk.CTkFrame(sidebar, height=1, fg_color="#2a2a3e").grid(
            row=2, column=0, sticky="ew", padx=15)

        status_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        status_frame.grid(row=3, column=0, padx=15, pady=10, sticky="ew")
        self._adb_status_dot = ctk.CTkLabel(status_frame, text="●", text_color="#f44336",
                                             font=ctk.CTkFont(size=14))
        self._adb_status_dot.pack(side="left")
        self._adb_status_label = ctk.CTkLabel(status_frame, text=t("adb.unavailable"),
                                               text_color=TEXT_MUTED, font=ctk.CTkFont(size=11))
        self._adb_status_label.pack(side="left")

    def _build_main(self):
        self._main = ctk.CTkFrame(self, fg_color=BG_DARK, corner_radius=0)
        self._main.grid(row=0, column=1, sticky="nsew")
        self._main.grid_columnconfigure(0, weight=1)
        self._main.grid_rowconfigure(1, weight=1)

        # Topbar
        topbar = ctk.CTkFrame(self._main, height=60, fg_color=BG_PANEL, corner_radius=0)
        topbar.grid(row=0, column=0, sticky="ew")
        topbar.grid_propagate(False)
        topbar.grid_columnconfigure(1, weight=1)

        self._topbar_title = ctk.CTkLabel(
            topbar, text=t("topbar.dashboard"),
            font=ctk.CTkFont(size=16, weight="bold"), text_color="#ffffff"
        )
        self._topbar_title.grid(row=0, column=0, padx=25, pady=18, sticky="w")

        device_frame = ctk.CTkFrame(topbar, fg_color="transparent")
        device_frame.grid(row=0, column=2, padx=15, pady=10)

        self._device_var = ctk.StringVar(value=t("topbar.searching"))
        self._device_combo = ctk.CTkComboBox(
            device_frame, variable=self._device_var,
            values=[t("topbar.searching")], width=230, state="readonly",
            command=self._on_device_selected
        )
        self._device_combo.pack(side="left", padx=(0, 8))

        self._btn_refresh = ctk.CTkButton(
            device_frame, text=t("topbar.refresh"), width=80, height=30,
            fg_color=ACCENT, text_color="#000", font=ctk.CTkFont(size=12),
            command=self._refresh_devices
        )
        self._btn_refresh.pack(side="left")

        # Content
        self._content = ctk.CTkFrame(self._main, fg_color=BG_DARK, corner_radius=0)
        self._content.grid(row=1, column=0, sticky="nsew", padx=20, pady=20)
        self._content.grid_columnconfigure(0, weight=1)
        self._content.grid_rowconfigure(0, weight=1)

        # Lazy panel registry – panely se tvoří až při prvním navštívení záložky.
        # Okamžitě se tvoří pouze dashboard (výchozí pohled).
        self._view_panels: dict = {}
        # Panely čekající na překlad – obnoví se líně až při přepnutí na ně
        self._lang_dirty: set = set()

        self._dashboard = self._build_panel("dashboard")
        self._navigate("dashboard")

    # ── Lazy panel factory ────────────────────────────────────────────────────

    def _build_panel(self, view: str):
        """Vytvoří panel pro daný pohled, zaregistruje ho a vrátí instanci."""
        if view in self._view_panels:
            return self._view_panels[view]

        if view == "dashboard":
            p = DeviceDashboard(self._content, accent=ACCENT)
            self._dashboard = p
        elif view == "extraction":
            p = ExtractionPanel(self._content, accent=ACCENT,
                                on_start=self._start_extraction,
                                on_stop=self._stop_extraction)
            self._extraction_panel = p
        elif view == "results":
            p = ResultsPanel(self._content, accent=ACCENT,
                             on_open_output=self._open_output_dir,
                             on_html=self._open_report,
                             on_pdf=self._export_pdf,
                             on_excel=self._export_excel,
                             on_evidence=self._generate_evidence,
                             on_verify=self._verify_evidence)
            self._results_panel = p
        elif view == "comparison":
            p = ComparisonPanel(self._content, accent=ACCENT)
            self._comparison_panel = p
        elif view == "timeline":
            p = TimelinePanel(self._content, accent=ACCENT)
            self._timeline_panel = p
        elif view == "map":
            p = MapPanel(self._content, accent=ACCENT)
            self._map_panel = p
        elif view == "bypass":
            p = BypassPanel(self._content, adb=self.adb, accent=ACCENT)
            self._bypass_panel = p
        elif view == "backup":
            p = BackupImportPanel(self._content, accent=ACCENT)
            self._backup_panel = p
        elif view == "ios":
            p = iOSPanel(self._content, accent="#5AC8FA")
            self._ios_panel = p
        elif view == "ios_backup":
            p = iOSBackupPanel(self._content, accent="#5AC8FA")
            self._ios_backup_panel = p
        elif view == "ios_ramdisk":
            p = iOSRamdiskPanel(self._content, accent="#5AC8FA")
            self._ios_ramdisk_panel = p
        else:
            return None

        p.grid(row=0, column=0, sticky="nsew")
        self._view_panels[view] = p
        return p

    # ── Navigace ─────────────────────────────────────────────────────────────

    # Statická mapa pohledů – sestavena jednou, nikoli při každém přepnutí
    _TITLES = {
        "dashboard":  "topbar.dashboard",
        "extraction": "topbar.extraction",
        "results":    "topbar.results",
        "comparison": "topbar.comparison",
        "timeline":   "topbar.timeline",
        "map":        "topbar.map",
        "bypass":     "topbar.bypass",
        "backup":     "topbar.backup",
        "ios":         "topbar.ios",
        "ios_backup":  "topbar.ios_backup",
        "ios_ramdisk": "topbar.ios_ramdisk",
    }

    def _navigate(self, view: str):
        prev_view = self._current_view
        self._current_view = view
        self._topbar_title.configure(text=t(self._TITLES.get(view, "topbar.dashboard")))

        # Aktualizuj jen dvě tlačítka (předchozí + nové) místo všech 8
        for key in (prev_view, view):
            btn = self._nav_buttons.get(key)
            if btn:
                btn.configure(
                    fg_color="#1e2a4a" if key == view else "transparent",
                    text_color=ACCENT if key == view else "#cccccc",
                )

        # Lazy init – vytvoř panel pokud ještě neexistuje
        if view not in self._view_panels:
            self._build_panel(view)

        # Líný překlad – pokud panel čeká na obnovu jazyka, obnov ho až teď
        if view in self._lang_dirty:
            self._lang_dirty.discard(view)
            panel = self._view_panels.get(view)
            if panel is not None and hasattr(panel, "refresh_lang"):
                panel.refresh_lang()

        # Flush čekající překreslení před přepnutím → eliminuje trhání
        self.update_idletasks()
        self._view_panels[view].lift()

    # ── Language ──────────────────────────────────────────────────────────────

    def _set_lang(self, lang: str):
        old_lang = get_lang()
        set_lang(lang)
        if old_lang != lang:
            self._retranslate_widgets(self, old_lang, lang)
        # Update switcher button appearance
        if lang == "cs":
            self._btn_cs.configure(fg_color=ACCENT, text_color="#000")
            self._btn_en.configure(fg_color="#2a2a4a", text_color="#cccccc")
        else:
            self._btn_en.configure(fg_color=ACCENT, text_color="#000")
            self._btn_cs.configure(fg_color="#2a2a4a", text_color="#cccccc")
        self._refresh_lang()

    def _retranslate_widgets(self, widget, old_lang: str, new_lang: str):
        """Walk the whole widget tree (incl. open dialogs) and swap every static
        text that is an exact translation in the old language for the new one."""
        stack = [widget]
        while stack:
            w = stack.pop()
            try:
                stack.extend(w.winfo_children())
            except Exception:
                pass
            if not isinstance(w, ctk.CTkBaseClass):
                continue
            for attr in ("text", "placeholder_text"):
                try:
                    cur = w.cget(attr)
                except Exception:
                    continue
                if isinstance(cur, str) and cur:
                    new = retranslate(cur, old_lang, new_lang)
                    if new is not None and new != cur:
                        try:
                            w.configure(**{attr: new})
                        except Exception:
                            pass
            if isinstance(w, (ctk.CTkOptionMenu, ctk.CTkComboBox)):
                try:
                    vals = list(w.cget("values") or [])
                    cur_val = w.get()
                    new_vals = [retranslate(v, old_lang, new_lang) or v for v in vals]
                    if new_vals != vals:
                        w.configure(values=new_vals)
                    nv = retranslate(cur_val, old_lang, new_lang)
                    if nv is not None:
                        w.set(nv)
                except Exception:
                    pass

    def _refresh_lang(self):
        # Nav buttons
        nav_label_keys = {
            "dashboard":  "nav.overview",
            "extraction": "nav.extraction",
            "results":    "nav.results",
            "comparison": "nav.comparison",
            "timeline":   "nav.timeline",
            "map":        "nav.map",
            "bypass":     "nav.bypass",
            "backup":     "nav.backup",
            "ios":         "nav.ios",
            "ios_backup":  "nav.ios_backup",
            "ios_ramdisk": "nav.ios_ramdisk",
        }
        for view_key, btn in self._nav_buttons.items():
            label_key = nav_label_keys.get(view_key, view_key)
            btn.configure(text=t(label_key))
        # Wifi button
        self._wifi_btn.configure(text=t("nav.wifi"))
        # Ostatní panely obnov líně – jen viditelný hned (viz níže)
        self._lang_dirty = set(self._view_panels.keys()) - {self._current_view}
        # Topbar title (re-navigate to current view)
        self._navigate(self._current_view)
        # ADB status — keep current color, just update text
        current_color = self._adb_status_dot.cget("text_color")
        if current_color == "#4CAF50":
            self._adb_status_label.configure(text=t("adb.ok"))
        else:
            self._adb_status_label.configure(text=t("adb.unavailable"))
        # Topbar buttons
        self._btn_refresh.configure(text=t("topbar.refresh"))
        # Obnov pouze viditelný panel; ostatní (_lang_dirty) se obnoví až
        # při přepnutí na ně – přepnutí jazyka tak nestaví všechny panely naráz.
        panel = self._view_panels.get(self._current_view)
        if panel is not None and hasattr(panel, "refresh_lang"):
            panel.refresh_lang()

    # ── ADB & devices ────────────────────────────────────────────────────────

    def _check_adb(self):
        ok, _ = self.adb.check_adb()
        self._adb_status_dot.configure(text_color="#4CAF50" if ok else "#f44336")
        self._adb_status_label.configure(text=t("adb.ok") if ok else t("adb.unavailable"))

    def _start_device_polling(self):
        def _poll():
            if self._closing:
                return
            self._refresh_devices()
            self._poll_after_id = self.after(5000, _poll)
        self._poll_after_id = self.after(500, _poll)

    def _refresh_devices(self):
        def _worker():
            devices = self.adb.get_devices()
            serials = [d["serial"] for d in devices]
            # Aktualizuj UI jen pokud se seznam zařízení skutečně změnil
            if serials != self._last_device_serials and not self._closing:
                self._last_device_serials = serials
                try:
                    self.after(0, lambda d=devices: self._update_device_list(d))
                except Exception:
                    pass
        threading.Thread(target=_worker, daemon=True).start()

    def _update_device_list(self, devices: list[dict]):
        if not devices:
            self._device_combo.configure(values=[t("dash.no_device")])
            self._device_var.set(t("dash.no_device"))
            self._dashboard.show_no_device()
            return

        labels = [f"{d['model'] or d['serial']} ({d['serial']})" for d in devices]
        self._device_combo.configure(values=labels)

        current = self._device_var.get()
        if current not in labels:
            self._device_var.set(labels[0])
            self.adb.connect_device(devices[0]["serial"])
            self.current_device = devices[0]
            self._load_device_info()

    def _on_device_selected(self, selection: str):
        import re
        m = re.search(r"\((.+)\)$", selection)
        if not m:
            return
        serial = m.group(1)
        self.adb.connect_device(serial)
        self.current_device = {"serial": serial, "model": selection.split("(")[0].strip()}
        self._load_device_info()

    def _load_device_info(self):
        self._dashboard.show_loading()
        def _worker():
            info = self.adb.get_device_info()
            self.device_info = info
            self.after(0, lambda: self._dashboard.show_device(info))
        threading.Thread(target=_worker, daemon=True).start()

    def _open_wifi_dialog(self):
        WifiConnectDialog(self, self.adb, on_connected=self._refresh_devices)

    # ── Extraction ───────────────────────────────────────────────────────────

    def _start_extraction(self, modules: list[str]):
        if not self.adb.connected_device:
            self._show_error(t("err.no_device"))
            return

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        device_name = (self.current_device or {}).get("model", "device").replace(" ", "_")
        default_name = f"{device_name}_{ts}"

        base_dir = filedialog.askdirectory(
            title=t("ext.folder_title"),
            initialdir=os.path.expanduser("~"),
        )
        if not base_dir:
            return  # user cancelled the dialog

        self.output_dir = os.path.join(base_dir, default_name)
        os.makedirs(self.output_dir, exist_ok=True)

        self.extractor = Extractor(self.adb, self.output_dir)
        self._extraction_panel.set_running(True)
        device_label = (self.current_device or {}).get("model", t("misc.unknown_device"))
        self._extraction_panel.log_session_start(device_label, len(modules))

        self.extractor.extract_all(
            modules,
            on_progress=self._on_extraction_progress,
            on_done=self._on_extraction_done,
            on_module_done=self._on_extraction_module_done,
            on_disconnected=lambda: self.after(
                0, self._extraction_panel.log_disconnected
            ),
            on_reconnected=lambda: self.after(
                0, self._extraction_panel.log_reconnected
            ),
            on_notify=lambda msg: self.after(
                0, lambda m=msg: self._extraction_panel.log_notify(m)
            ),
            on_log=lambda msg: self.after(
                0, lambda m=msg: self._extraction_panel.log_copy_progress(m)
            ),
        )

    def _stop_extraction(self):
        if self.extractor:
            self.extractor.stop()
            self._extraction_panel.log_stopping()

    def _on_extraction_progress(self, label: str, current: int, total: int):
        self.after(0, lambda: self._extraction_panel.update_progress(label, current, total))

    def _on_extraction_module_done(self, result, current: int, total: int,
                                   elapsed: float, avg: float):
        def _update():
            self._extraction_panel.log_module_done(
                label=result.label,
                current=current,
                total=total,
                elapsed=elapsed,
                avg=avg,
                success=result.success,
                count=result.count,
                error=result.error,
            )
        self.after(0, _update)

    def _on_extraction_done(self, results):
        self._extraction_results = results
        was_stopped = bool(self.extractor and self.extractor._stopped())
        report_path = ""
        try:
            report_path = generate_report(results, self.device_info, self.output_dir)
        except Exception as e:
            print(f"HTML report error: {e}")
        self.after(0, lambda: self._finish_extraction(results, report_path, was_stopped))

    def _finish_extraction(self, results, report_path: str, was_stopped: bool = False):
        success_count = len([r for r in results if r.success])
        self._extraction_panel.set_running(False)
        if was_stopped:
            self._extraction_panel.show_stopped(success_count)
        else:
            self._extraction_panel.show_done(success_count)
        # Panels are created lazily – make sure they exist before we use them,
        # otherwise a first extraction (before these tabs were ever opened)
        # would hit a missing attribute and nothing would be shown.
        self._build_panel("results")
        self._build_panel("timeline")
        self._build_panel("map")
        self._update_results_panel(results)
        if self.output_dir:
            self._timeline_panel.load_dir(self.output_dir)
            self._map_panel.load_dir(self.output_dir)
        self._navigate("results")

    def _update_results_panel(self, results):
        self._results_panel.update_results(results)

    # ── Export PDF ───────────────────────────────────────────────────────────

    def _export_pdf(self):
        if not self._extraction_results or not self.output_dir:
            self._show_error(t("err.no_extraction"))
            return

        def _worker():
            try:
                path = generate_pdf_report(
                    self._extraction_results, self.device_info, self.output_dir
                )
                self.after(0, lambda: self._on_pdf_done(path))
            except Exception as e:
                self.after(0, lambda err=e: self._show_error(t("err.pdf").format(e=err)))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_pdf_done(self, path: str):
        if os.path.exists(path):
            os.startfile(path)

    def _export_excel(self):
        if not self._extraction_results or not self.output_dir:
            self._show_error(t("err.no_extraction"))
            return

        def _worker():
            try:
                path = generate_excel_report(
                    self._extraction_results, self.device_info, self.output_dir
                )
                self.after(0, lambda: os.startfile(path) if os.path.exists(path) else None)
            except Exception as e:
                self.after(0, lambda err=e: self._show_error(t("err.excel").format(e=err)))

        threading.Thread(target=_worker, daemon=True).start()

    # ── Evidence integrity / chain-of-custody ──────────────────────────────────

    def _generate_evidence(self):
        if not self.output_dir or not os.path.isdir(self.output_dir):
            self._show_error(t("err.no_extraction"))
            return
        EvidenceCaseDialog(self, on_submit=self._do_generate_evidence)

    def _do_generate_evidence(self, case_meta: dict):
        def _worker():
            try:
                path, manifest = generate_manifest(
                    self.output_dir, case_meta=case_meta, device_info=self.device_info
                )
                self.after(0, lambda: self._on_evidence_done(path, manifest))
            except Exception as e:
                self.after(0, lambda err=e: self._show_error(str(err)))
        threading.Thread(target=_worker, daemon=True).start()

    def _on_evidence_done(self, path: str, manifest: dict):
        txt = os.path.join(self.output_dir, MANIFEST_TXT)
        msg = t("ev.generated_msg").format(
            n=manifest.get("file_count", 0),
            agg=manifest.get("aggregate_sha256", "")[:16],
        )
        self._show_info(t("ev.generate_btn"), msg)
        if os.path.exists(txt):
            os.startfile(txt)

    def _verify_evidence(self):
        if not self.output_dir or not os.path.isdir(self.output_dir):
            self._show_error(t("err.no_extraction"))
            return

        def _worker():
            report = verify_manifest(self.output_dir)
            self.after(0, lambda: self._on_verify_done(report))
        threading.Thread(target=_worker, daemon=True).start()

    def _on_verify_done(self, report: dict):
        if report.get("error") == "no_manifest":
            self._show_error(t("ev.err_no_manifest"))
            return
        if report.get("error"):
            self._show_error(t("ev.err_bad_manifest"))
            return

        intact = report.get("intact")
        head = t("ev.verify_ok") if intact else t("ev.verify_tampered")
        lines = [
            t("ev.verify_counts").format(
                ok=len(report["ok"]), mod=len(report["modified"]),
                miss=len(report["missing"]), add=len(report["added"]),
            )
        ]
        for label_key, items in (
            ("ev.row_modified", report["modified"]),
            ("ev.row_missing",  report["missing"]),
            ("ev.row_added",    report["added"]),
        ):
            if items:
                shown = ", ".join(items[:8]) + (" …" if len(items) > 8 else "")
                lines.append(f"{t(label_key)}: {shown}")
        self._show_info(head, "\n".join(lines), ok=bool(intact))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _open_output_dir(self):
        if self.output_dir and os.path.exists(self.output_dir):
            os.startfile(self.output_dir)

    def _open_report(self):
        if not self.output_dir:
            return
        report = os.path.join(self.output_dir, "report.html")
        if os.path.exists(report):
            os.startfile(report)

    def _show_error(self, msg: str):
        dialog = ctk.CTkToplevel(self)
        dialog.title(t("err.title"))
        dialog.geometry("380x150")
        dialog.grab_set()
        ctk.CTkLabel(dialog, text=msg, wraplength=330).pack(padx=20, pady=20)
        ctk.CTkButton(dialog, text="OK", command=dialog.destroy).pack(pady=10)

    def _show_info(self, title: str, msg: str, ok: bool = True):
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        dialog.geometry("520x240")
        dialog.grab_set()
        ctk.CTkLabel(
            dialog, text=title,
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#4CAF50" if ok else "#f44336",
        ).pack(padx=20, pady=(18, 6))
        ctk.CTkLabel(
            dialog, text=msg, wraplength=470, justify="left",
        ).pack(padx=20, pady=(0, 10))
        ctk.CTkButton(dialog, text="OK", command=dialog.destroy).pack(pady=8)


class EvidenceCaseDialog(ctk.CTkToplevel):
    """Collect chain-of-custody metadata before generating the evidence manifest."""

    FIELDS = [
        ("case_number", "ev.f_case"),
        ("examiner",    "ev.f_examiner"),
        ("organization", "ev.f_org"),
        ("evidence_id", "ev.f_evidence_id"),
        ("note",        "ev.f_note"),
    ]

    def __init__(self, master, on_submit):
        super().__init__(master)
        self._on_submit = on_submit
        self.title(t("ev.dialog_title"))
        self.geometry("460x360")
        self.grab_set()
        self.configure(fg_color=BG_DARK)

        ctk.CTkLabel(
            self, text=t("ev.dialog_title"),
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(padx=20, pady=(18, 4))
        ctk.CTkLabel(
            self, text=t("ev.dialog_hint"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
            wraplength=410, justify="left",
        ).pack(padx=20, pady=(0, 10))

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(padx=20, fill="x")
        form.grid_columnconfigure(1, weight=1)

        self._entries: dict = {}
        for i, (key, label_key) in enumerate(self.FIELDS):
            ctk.CTkLabel(
                form, text=t(label_key), anchor="w",
                font=ctk.CTkFont(size=12), width=120,
            ).grid(row=i, column=0, sticky="w", pady=4, padx=(0, 8))
            entry = ctk.CTkEntry(form, height=30)
            entry.grid(row=i, column=1, sticky="ew", pady=4)
            self._entries[key] = entry

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(pady=16)
        ctk.CTkButton(
            btns, text=t("ev.cancel"), width=100,
            fg_color="#2a2a4a", hover_color="#3a3a5a",
            command=self.destroy,
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            btns, text=t("ev.generate_btn"), width=140,
            fg_color=ACCENT, text_color="#000",
            command=self._submit,
        ).pack(side="left", padx=6)

    def _submit(self):
        meta = {k: e.get().strip() for k, e in self._entries.items()}
        self.destroy()
        self._on_submit(meta)
