import customtkinter as ctk
import threading
import datetime
import os
from tkinter import filedialog
from .dashboard import DeviceDashboard
from .extraction import ExtractionPanel
from .comparison import ComparisonPanel
from .timeline import TimelinePanel
from .wifi_dialog import WifiConnectDialog
from .bypass_panel import BypassPanel
from .map_panel import MapPanel
from .result_detail import ResultDetailDialog
from core.adb_manager import ADBManager
from core.extractor import Extractor, EXTRACTION_MODULES
from core.report import generate_report, generate_pdf_report, generate_excel_report
from core.i18n import t, set_lang, get_lang


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

ACCENT     = "#00d4ff"
BG_DARK    = "#0f0f1a"
BG_CARD    = "#16213e"
BG_PANEL   = "#1a1a2e"
TEXT_MUTED = "#888888"


def _detect_scale() -> float:
    """Vrátí scale factor podle rozlišení primárního monitoru."""
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

        # Škálování podle rozlišení monitoru
        self._scale = _detect_scale()
        if self._scale != 1.0:
            ctk.set_widget_scaling(self._scale)
            ctk.set_window_scaling(self._scale)

        self.title("Paranoid-Android-User-toolkit")
        # Výchozí geometrie přizpůsobená rozlišení
        w = int(1280 * self._scale)
        h = int(800 * self._scale)
        self.geometry(f"{w}x{h}")
        self.minsize(int(1024 * self._scale), int(680 * self._scale))
        self.configure(fg_color=BG_DARK)

        self.adb = ADBManager()
        self.extractor: Extractor | None = None
        self.current_device: dict | None = None
        self.device_info: dict = {}
        self.output_dir = ""
        self._extraction_results: list = []
        self._current_view = "dashboard"

        self._build_ui()
        self._check_adb()
        self._start_device_polling()

    # ── UI stavba ────────────────────────────────────────────────────────────

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
        sidebar.grid_rowconfigure(14, weight=1)
        sidebar.grid_columnconfigure(0, weight=1)

        # Logo
        logo_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        logo_frame.grid(row=0, column=0, padx=20, pady=(22, 4), sticky="w")

        ctk.CTkLabel(
            logo_frame, text="Paranoid-Android-",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#ffffff"
        ).pack(anchor="w")
        ctk.CTkLabel(
            logo_frame, text="User-toolkit",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#ffffff"
        ).pack(anchor="w")
        ctk.CTkLabel(
            logo_frame, text="v1.0",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=TEXT_MUTED
        ).pack(anchor="w")

        # Language switcher pod názvem
        lang_frame_top = ctk.CTkFrame(sidebar, fg_color="transparent")
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

        ctk.CTkFrame(sidebar, height=1, fg_color="#2a2a3e").grid(
            row=2, column=0, sticky="ew", padx=15, pady=5)

        # Navigace
        nav_items = [
            ("overview",   "nav.overview"),
            ("extraction", "nav.extraction"),
            ("results",    "nav.results"),
            ("comparison", "nav.comparison"),
            ("timeline",   "nav.timeline"),
            ("map",        "nav.map"),
            ("bypass",     "nav.bypass"),
        ]
        # Map view keys (used internally) to nav keys
        self._nav_key_map = {
            "overview":   "dashboard",
            "extraction": "extraction",
            "results":    "results",
            "comparison": "comparison",
            "timeline":   "timeline",
            "map":        "map",
            "bypass":     "bypass",
        }
        self._nav_buttons = {}

        for i, (nav_key, label_key) in enumerate(nav_items):
            view_key = self._nav_key_map[nav_key]
            btn = ctk.CTkButton(
                sidebar, text=t(label_key),
                font=ctk.CTkFont(size=13),
                fg_color="transparent", text_color="#cccccc",
                hover_color="#2a2a3e", anchor="w", height=42,
                command=lambda k=view_key: self._navigate(k)
            )
            btn.grid(row=i + 3, column=0, padx=10, pady=3, sticky="ew")
            self._nav_buttons[view_key] = btn

        ctk.CTkFrame(sidebar, height=1, fg_color="#2a2a3e").grid(
            row=10, column=0, sticky="ew", padx=15, pady=10)

        # Wi-Fi tlačítko
        self._wifi_btn = ctk.CTkButton(
            sidebar, text=t("nav.wifi"),
            font=ctk.CTkFont(size=12),
            fg_color="transparent", text_color="#cccccc",
            hover_color="#2a2a3e", anchor="w", height=38,
            command=self._open_wifi_dialog
        )
        self._wifi_btn.grid(row=11, column=0, padx=10, pady=3, sticky="ew")

        ctk.CTkFrame(sidebar, height=1, fg_color="#2a2a3e").grid(
            row=13, column=0, sticky="ew", padx=15, pady=5)

        # ADB status
        status_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        status_frame.grid(row=14, column=0, padx=15, pady=15, sticky="sw")
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

        self._dashboard = DeviceDashboard(self._content, accent=ACCENT)
        self._dashboard.grid(row=0, column=0, sticky="nsew")

        self._extraction_panel = ExtractionPanel(
            self._content, accent=ACCENT,
            on_start=self._start_extraction,
            on_stop=self._stop_extraction
        )

        self._results_panel = self._build_results_panel()

        self._comparison_panel = ComparisonPanel(self._content, accent=ACCENT)

        self._timeline_panel = TimelinePanel(self._content, accent=ACCENT)

        self._bypass_panel = BypassPanel(self._content, adb=self.adb, accent=ACCENT)
        self._map_panel = MapPanel(self._content, accent=ACCENT)

        self._navigate("dashboard")

    def _build_results_panel(self):
        frame = ctk.CTkFrame(self._content, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(frame, fg_color=BG_CARD, corner_radius=10)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 15))
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(header, text=t("topbar.results"),
                     font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, padx=20, pady=15, sticky="w"
        )

        btn_frame = ctk.CTkFrame(header, fg_color="transparent")
        btn_frame.grid(row=0, column=2, padx=15)

        self._btn_open_output = ctk.CTkButton(
            btn_frame, text=t("topbar.open_output"),
            width=120, height=30,
            fg_color="#2a2a4a", hover_color="#3a3a5a",
            command=self._open_output_dir
        )
        self._btn_open_output.pack(side="left", padx=4)

        self._btn_html = ctk.CTkButton(
            btn_frame, text=t("topbar.html_report"),
            width=110, height=30,
            fg_color="#2a2a4a", hover_color="#3a3a5a",
            command=self._open_report
        )
        self._btn_html.pack(side="left", padx=4)

        self._btn_pdf = ctk.CTkButton(
            btn_frame, text=t("topbar.pdf_report"),
            width=110, height=30,
            fg_color=ACCENT, text_color="#000",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._export_pdf
        )
        self._btn_pdf.pack(side="left", padx=4)

        self._btn_excel = ctk.CTkButton(
            btn_frame, text=t("topbar.excel_report"),
            width=110, height=30,
            fg_color="#1D6F42", hover_color="#145230",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._export_excel
        )
        self._btn_excel.pack(side="left", padx=4)

        self._results_list = ctk.CTkScrollableFrame(frame, fg_color="transparent")
        self._results_list.grid(row=1, column=0, sticky="nsew")
        self._results_list.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self._results_list,
            text="Zatím nebyla provedena žádná extrakce.",
            text_color=TEXT_MUTED, font=ctk.CTkFont(size=13)
        ).grid(row=0, column=0, pady=40)

        return frame

    # ── Navigace ─────────────────────────────────────────────────────────────

    def _navigate(self, view: str):
        self._current_view = view
        titles = {
            "dashboard":  t("topbar.dashboard"),
            "extraction": t("topbar.extraction"),
            "results":    t("topbar.results"),
            "comparison": t("topbar.comparison"),
            "timeline":   t("topbar.timeline"),
            "map":        t("topbar.map"),
            "bypass":     t("topbar.bypass"),
        }
        panels = {
            "dashboard":  self._dashboard,
            "extraction": self._extraction_panel,
            "results":    self._results_panel,
            "comparison": self._comparison_panel,
            "timeline":   self._timeline_panel,
            "map":        self._map_panel,
            "bypass":     self._bypass_panel,
        }
        self._topbar_title.configure(text=titles.get(view, ""))

        for key, btn in self._nav_buttons.items():
            btn.configure(
                fg_color="#1e2a4a" if key == view else "transparent",
                text_color=ACCENT if key == view else "#cccccc"
            )

        for key, panel in panels.items():
            if key == view:
                panel.grid(row=0, column=0, sticky="nsew")
            else:
                panel.grid_remove()

    # ── Jazyk ─────────────────────────────────────────────────────────────────

    def _set_lang(self, lang: str):
        set_lang(lang)
        # Update switcher button appearance
        if lang == "cs":
            self._btn_cs.configure(fg_color=ACCENT, text_color="#000")
            self._btn_en.configure(fg_color="#2a2a4a", text_color="#cccccc")
        else:
            self._btn_en.configure(fg_color=ACCENT, text_color="#000")
            self._btn_cs.configure(fg_color="#2a2a4a", text_color="#cccccc")
        self._refresh_lang()

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
        }
        for view_key, btn in self._nav_buttons.items():
            label_key = nav_label_keys.get(view_key, view_key)
            btn.configure(text=t(label_key))
        # Wifi button
        self._wifi_btn.configure(text=t("nav.wifi"))
        # Topbar title (re-navigate to current view)
        self._navigate(self._current_view)
        # ADB status — keep current color, just update text
        current_color = self._adb_status_dot.cget("text_color")
        if current_color == "#4CAF50":
            self._adb_status_label.configure(text=t("adb.ok"))
        else:
            self._adb_status_label.configure(text=t("adb.unavailable"))
        # Topbar buttons
        self._btn_open_output.configure(text=t("topbar.open_output"))
        self._btn_html.configure(text=t("topbar.html_report"))
        self._btn_pdf.configure(text=t("topbar.pdf_report"))
        self._btn_excel.configure(text=t("topbar.excel_report"))
        self._btn_refresh.configure(text=t("topbar.refresh"))
        # Panels
        self._dashboard.refresh_lang()
        self._extraction_panel.refresh_lang()
        self._timeline_panel.refresh_lang()
        self._comparison_panel.refresh_lang()

    # ── ADB & zařízení ───────────────────────────────────────────────────────

    def _check_adb(self):
        ok, _ = self.adb.check_adb()
        self._adb_status_dot.configure(text_color="#4CAF50" if ok else "#f44336")
        self._adb_status_label.configure(text=t("adb.ok") if ok else t("adb.unavailable"))

    def _start_device_polling(self):
        def _poll():
            self._refresh_devices()
            self.after(5000, _poll)
        self.after(500, _poll)

    def _refresh_devices(self):
        def _worker():
            devices = self.adb.get_devices()
            self.after(0, lambda: self._update_device_list(devices))
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

    # ── Extrakce ─────────────────────────────────────────────────────────────

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
            return  # uživatel zrušil dialog

        self.output_dir = os.path.join(base_dir, default_name)
        os.makedirs(self.output_dir, exist_ok=True)

        self.extractor = Extractor(self.adb, self.output_dir)
        self._extraction_panel.set_running(True)
        device_label = (self.current_device or {}).get("model", "neznámé zařízení")
        self._extraction_panel.log_session_start(device_label, len(modules))

        self.extractor.extract_all(
            modules,
            on_progress=self._on_extraction_progress,
            on_done=self._on_extraction_done,
            on_module_done=self._on_extraction_module_done,
        )

    def _stop_extraction(self):
        if self.extractor:
            self.extractor.stop()

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
        report_path = ""
        try:
            report_path = generate_report(results, self.device_info, self.output_dir)
        except Exception as e:
            print(f"HTML report error: {e}")
        self.after(0, lambda: self._finish_extraction(results, report_path))

    def _finish_extraction(self, results, report_path: str):
        self._extraction_panel.set_running(False)
        self._extraction_panel.show_done(len([r for r in results if r.success]))
        self._update_results_panel(results)
        if self.output_dir:
            self._timeline_panel.load_dir(self.output_dir)
            self._map_panel.load_dir(self.output_dir)
        self._navigate("results")

    def _update_results_panel(self, results):
        for widget in self._results_list.winfo_children():
            widget.destroy()

        if not results:
            ctk.CTkLabel(self._results_list, text=t("misc.no_results"),
                         text_color=TEXT_MUTED).grid(row=0, column=0, pady=40)
            return

        for i, r in enumerate(results):
            card = ctk.CTkFrame(self._results_list, fg_color=BG_CARD, corner_radius=8,
                                cursor="hand2")
            card.grid(row=i, column=0, sticky="ew", pady=4)
            card.grid_columnconfigure(1, weight=1)

            color = "#4CAF50" if r.success else "#f44336"
            ctk.CTkLabel(card, text="✓" if r.success else "✗",
                         text_color=color, font=ctk.CTkFont(size=16, weight="bold"),
                         width=35).grid(row=0, column=0, padx=(15, 5), pady=12)
            ctk.CTkLabel(card, text=r.label, font=ctk.CTkFont(size=13),
                         anchor="w").grid(row=0, column=1, sticky="w", pady=12)

            info_text = f"{r.count} {t('res.records')}" if r.success else r.error
            ctk.CTkLabel(card, text=info_text, text_color=TEXT_MUTED,
                         font=ctk.CTkFont(size=12)).grid(row=0, column=2, padx=(0, 10))

            # Tlačítko detail (jen u úspěšných s daty)
            detail_btn = ctk.CTkButton(
                card, text=t("res.detail_btn"),
                width=80, height=28,
                fg_color=ACCENT if r.success else "#2a2a4a",
                text_color="#000" if r.success else TEXT_MUTED,
                font=ctk.CTkFont(size=12),
                state="normal" if r.success and r.data is not None else "disabled",
                command=lambda res=r: ResultDetailDialog(self, res)
            )
            detail_btn.grid(row=0, column=3, padx=15, pady=8)

            # Klik na celou kartu (ne na tlačítko, to má vlastní command)
            if r.success and r.data is not None:
                for widget in card.winfo_children():
                    if widget is not detail_btn:
                        widget.bind("<Button-1>",
                                    lambda e, res=r: ResultDetailDialog(self, res))

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

    # ── Pomocné ───────────────────────────────────────────────────────────────

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
