import customtkinter as ctk
import tkinter as tk
import os, json, datetime
from collections import defaultdict
from core.i18n import t

ACCENT     = "#00d4ff"
BG_CARD    = "#16213e"
BG_PANEL   = "#1a1a2e"
BG_DARK    = "#0f0f1a"
TEXT_MUTED = "#888888"

SOURCE_CFG = {
    "sms":     {"color": "#4CAF50", "label_key": "src.sms"},
    "call":    {"color": "#2196F3", "label_key": "src.call"},
    "browser": {"color": "#FF9800", "label_key": "src.browser"},
    "photo":   {"color": "#E91E63", "label_key": "src.photo"},
    "file":    {"color": "#9C27B0", "label_key": "src.file"},
    "app":     {"color": "#00BCD4", "label_key": "src.app"},
}

SOURCES      = list(SOURCE_CFG.keys())
AXIS_H       = 40      # výška osy X dole
DOT_R        = 7       # poloměr tečky události
PAD_L        = 10
PAD_R        = 10
LANE_H_MIN   = 48      # minimální výška dráhy
LABEL_W      = 120     # šířka panelu s názvy drah


def _parse_dt(s) -> datetime.datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.datetime.strptime(str(s).strip(), fmt)
        except Exception:
            pass
    return None


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def load_events(output_dir: str) -> list[dict]:
    events = []

    def _add(dt, source, title, detail="", raw=None):
        if dt:
            events.append({"dt": dt, "source": source,
                           "title": title, "detail": detail,
                           "raw": raw or {}})

    for msg in _load(os.path.join(output_dir, "sms.json")):
        dt = _parse_dt(msg.get("date", ""))
        _add(dt, "sms",
             f"{msg.get('direction','')} — {msg.get('address','?')}",
             msg.get("body", "")[:80], msg)

    for c in _load(os.path.join(output_dir, "call_logs.json")):
        dt = _parse_dt(c.get("date", ""))
        _add(dt, "call",
             f"{c.get('type','')} — {c.get('number','?')}",
             c.get("duration", ""), c)

    for h in _load(os.path.join(output_dir, "browser_history.json")):
        dt = _parse_dt(h.get("date", ""))
        _add(dt, "browser",
             h.get("title", "") or h.get("url", "")[:60],
             h.get("url", "")[:100], h)

    fa = _load(os.path.join(output_dir, "file_analysis.json"))
    files = fa.get("files", fa) if isinstance(fa, dict) else fa
    if isinstance(files, list):
        for f in files:
            dt = (_parse_dt(f.get("date_taken", "")) or
                  _parse_dt(f.get("modified", "")))
            src = "photo" if f.get("type") == "image" else "file"
            gps = (f" [{f['gps_latitude']:.4f}, {f['gps_longitude']:.4f}]"
                   if "gps_latitude" in f else "")
            _add(dt, src, f.get("filename", ""),
                 f.get("camera_model", "") + gps, f)

    events.sort(key=lambda e: e["dt"])
    return events


# ── Detail dialog ─────────────────────────────────────────────────────────────

class EventDetailDialog(ctk.CTkToplevel):
    def __init__(self, master, event: dict):
        super().__init__(master)
        src = SOURCE_CFG.get(event["source"], {"color": ACCENT, "label_key": "src.sms"})
        src_label = t(src["label_key"])
        self.title(f"Detail — {src_label}")
        self.geometry("580x500")
        self.resizable(True, True)
        self.configure(fg_color=BG_DARK)
        self.grab_set()

        hdr = ctk.CTkFrame(self, fg_color=src["color"], corner_radius=0, height=46)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr,
                     text=f"  {src_label}  —  "
                          f"{event['dt'].strftime('%d. %m. %Y  %H:%M:%S')}",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color="#ffffff").pack(side="left", padx=15)

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=15, pady=12)
        scroll.grid_columnconfigure(1, weight=1)

        base = [
            ("Zdroj",  src_label),
            ("Datum",  event["dt"].strftime("%d. %m. %Y")),
            ("Čas",    event["dt"].strftime("%H:%M:%S")),
            ("Popis",  event["title"]),
        ]
        if event.get("detail"):
            base.append(("Detail", event["detail"]))

        raw = event.get("raw", {})
        all_fields = base + [(k, str(v)) for k, v in raw.items()
                              if str(v).strip() and k != "path"]

        if "gps_latitude" in raw:
            all_fields.append(("GPS odkaz",
                f"https://www.google.com/maps?q="
                f"{raw['gps_latitude']},{raw['gps_longitude']}"))

        for i, (label, value) in enumerate(all_fields):
            bg = BG_CARD if i % 2 == 0 else BG_PANEL
            rf = ctk.CTkFrame(scroll, fg_color=bg, corner_radius=6)
            rf.grid(row=i, column=0, columnspan=2, sticky="ew", pady=1)
            rf.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(rf, text=label,
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color=ACCENT, width=130, anchor="w").grid(
                row=0, column=0, padx=(12, 6), pady=5, sticky="w")
            ctk.CTkLabel(rf, text=str(value),
                         font=ctk.CTkFont(size=11),
                         text_color="#dddddd", anchor="w",
                         wraplength=380).grid(
                row=0, column=1, padx=(0, 12), pady=5, sticky="w")

        # Náhled obrázku
        lp = raw.get("path", "")
        if lp and os.path.isfile(lp):
            ext = os.path.splitext(lp)[1].lower()
            if ext in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
                try:
                    from PIL import Image, ImageTk
                    img = Image.open(lp)
                    img.thumbnail((520, 280))
                    self._photo = ImageTk.PhotoImage(img)
                    tk.Label(scroll, image=self._photo,
                             bg="#0d1117").grid(
                        row=len(all_fields) + 1, column=0,
                        columnspan=2, pady=8)
                except Exception:
                    pass

        ctk.CTkButton(self, text=t("res.close"), height=32,
                      fg_color="#2a2a4a", hover_color="#3a3a5a",
                      command=self.destroy).pack(pady=(0, 10))


# ── Hlavní panel ──────────────────────────────────────────────────────────────

class TimelinePanel(ctk.CTkFrame):
    def __init__(self, master, accent: str = ACCENT, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.accent = accent
        self._events: list[dict] = []
        self._output_dir = ""

        # Pohled (datetime hranice viditelné oblasti)
        self._view_start: datetime.datetime | None = None
        self._view_end:   datetime.datetime | None = None
        self._data_start: datetime.datetime | None = None
        self._data_end:   datetime.datetime | None = None

        # Tažení (pan)
        self._pan_x0: int | None = None
        self._pan_vs: datetime.datetime | None = None
        self._pan_ve: datetime.datetime | None = None

        # Výběr rozsahu (Shift+drag)
        self._sel_x0: int | None = None
        self._sel_rect = None

        # Aktivní filtry
        self._source_vars: dict[str, ctk.BooleanVar] = {}

        # Mapa canvas_id → event pro klikání
        self._dot_map: dict[int, dict] = {}

        # Filter checkbox refs for lang refresh
        self._filter_checks: dict[str, ctk.CTkCheckBox] = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build(self):
        # Horní panel
        top = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(top, fg_color="transparent")
        left.grid(row=0, column=0, padx=12, pady=10)

        self._load_btn = ctk.CTkButton(
            left, text=t("tl.load"), width=120, height=32,
            fg_color=self.accent, text_color="#000",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._load_from_dialog
        )
        self._load_btn.pack(side="left", padx=(0, 8))

        self._dir_label = ctk.CTkLabel(left, text=t("tl.no_folder"),
                                        text_color=TEXT_MUTED,
                                        font=ctk.CTkFont(size=11))
        self._dir_label.pack(side="left")

        # Filtry zdrojů
        self._filter_frame = ctk.CTkFrame(top, fg_color="transparent")
        self._filter_frame.grid(row=0, column=1, padx=10, pady=10, sticky="e")
        self._build_filter_checkboxes()

        # Tlačítka zoom / reset
        btn_f = ctk.CTkFrame(top, fg_color="transparent")
        btn_f.grid(row=0, column=2, padx=10)

        self._reset_btn = ctk.CTkButton(
            btn_f, text=t("tl.full_range"), width=90, height=28,
            fg_color="#2a2a4a", hover_color="#3a3a5a",
            font=ctk.CTkFont(size=11),
            command=self._reset_view
        )
        self._reset_btn.pack(side="left", padx=3)

        self._zoom_in_btn = ctk.CTkButton(
            btn_f, text=t("tl.zoom_in"), width=90, height=28,
            fg_color="#2a2a4a", hover_color="#3a3a5a",
            font=ctk.CTkFont(size=11),
            command=self._zoom_in
        )
        self._zoom_in_btn.pack(side="left", padx=3)

        self._zoom_out_btn = ctk.CTkButton(
            btn_f, text=t("tl.zoom_out"), width=90, height=28,
            fg_color="#2a2a4a", hover_color="#3a3a5a",
            font=ctk.CTkFont(size=11),
            command=self._zoom_out
        )
        self._zoom_out_btn.pack(side="left", padx=3)

        self._stats_lbl = ctk.CTkLabel(top, text="",
                                        text_color=TEXT_MUTED,
                                        font=ctk.CTkFont(size=10))
        self._stats_lbl.grid(row=1, column=0, columnspan=3,
                              padx=12, pady=(0, 8), sticky="w")

        # ── Hlavní oblast: dráhy + osa ────────────────────────────────────────
        self._main = ctk.CTkFrame(self, fg_color="transparent")
        self._main.grid(row=1, column=0, sticky="nsew")
        self._main.grid_columnconfigure(1, weight=1)
        self._main.grid_rowconfigure(0, weight=1)

        # Levý panel s názvy drah
        self._lane_labels = ctk.CTkFrame(self._main,
                                          fg_color=BG_PANEL, corner_radius=0,
                                          width=LABEL_W)
        self._lane_labels.grid(row=0, column=0, sticky="nsew")
        self._lane_labels.grid_propagate(False)

        # Canvas pro osu + dráhy
        canvas_wrap = ctk.CTkFrame(self._main, fg_color=BG_DARK, corner_radius=0)
        canvas_wrap.grid(row=0, column=1, sticky="nsew")
        canvas_wrap.grid_columnconfigure(0, weight=1)
        canvas_wrap.grid_rowconfigure(0, weight=1)

        self._canvas = tk.Canvas(canvas_wrap, bg="#0d1117",
                                  highlightthickness=0, cursor="crosshair")
        self._canvas.grid(row=0, column=0, sticky="nsew")

        # Scrollbar X (posun v čase)
        self._hbar = tk.Scrollbar(canvas_wrap, orient="horizontal",
                                   command=self._on_scroll)
        self._hbar.grid(row=1, column=0, sticky="ew")

        # Tooltip
        self._tip_win: tk.Toplevel | None = None
        self._tip_id  = None

        # Bindování
        self._canvas.bind("<Configure>",      lambda e: self._on_canvas_resize(e))
        self._canvas.bind("<MouseWheel>",     self._on_wheel)
        self._canvas.bind("<ButtonPress-1>",  self._on_press)
        self._canvas.bind("<B1-Motion>",      self._on_motion)
        self._canvas.bind("<ButtonRelease-1>",self._on_release)
        self._canvas.bind("<ButtonPress-3>",  lambda e: self._reset_view())
        self._canvas.bind("<Motion>",         self._on_hover)
        self._canvas.bind("<Leave>",          self._hide_tip)

        # Placeholder
        self._placeholder = ctk.CTkLabel(
            self._main,
            text=t("tl.placeholder"),
            text_color=TEXT_MUTED, font=ctk.CTkFont(size=13))
        self._placeholder.grid(row=0, column=0, columnspan=2, pady=60)

    def _build_filter_checkboxes(self):
        for w in self._filter_frame.winfo_children():
            w.destroy()
        self._filter_checks.clear()
        for src, cfg in SOURCE_CFG.items():
            if src not in self._source_vars:
                var = ctk.BooleanVar(value=True)
                self._source_vars[src] = var
            else:
                var = self._source_vars[src]
            cb = ctk.CTkCheckBox(
                self._filter_frame,
                text=t(cfg["label_key"]),
                variable=var,
                width=20, height=20,
                checkbox_width=16, checkbox_height=16,
                fg_color=cfg["color"], checkmark_color="#fff",
                hover_color=cfg["color"],
                font=ctk.CTkFont(size=11),
                command=self._redraw
            )
            cb.pack(side="left", padx=5)
            self._filter_checks[src] = cb

    # ── Data ──────────────────────────────────────────────────────────────────

    def _load_from_dialog(self):
        from tkinter import filedialog
        folder = filedialog.askdirectory(title=t("tl.folder_title"))
        if folder:
            self.load_dir(folder)

    def load_dir(self, output_dir: str):
        self._output_dir = output_dir
        self._dir_label.configure(text=os.path.basename(output_dir))
        self._events = load_events(output_dir)
        if self._events:
            self._placeholder.grid_remove()
            self._data_start = self._events[0]["dt"]
            self._data_end   = self._events[-1]["dt"]
            self._reset_view()
            self._build_lane_labels(self._get_lane_h())
        self._update_stats()

    def _active_events(self) -> list[dict]:
        active = {s for s, v in self._source_vars.items() if v.get()}
        return [e for e in self._events if e["source"] in active]

    def _visible_events(self) -> list[dict]:
        if not self._view_start or not self._view_end:
            return []
        return [e for e in self._active_events()
                if self._view_start <= e["dt"] <= self._view_end]

    def _update_stats(self):
        ev = self._active_events()
        if not ev:
            self._stats_lbl.configure(text=t("tl.no_events"))
            return
        vis = self._visible_events()
        by_src = defaultdict(int)
        for e in vis:
            by_src[e["source"]] += 1
        parts = [f"{t(SOURCE_CFG[s]['label_key'])}: {n}"
                 for s, n in sorted(by_src.items())]
        span = ""
        if self._view_start and self._view_end:
            span = (f"{self._view_start.strftime('%d.%m.%Y %H:%M')}"
                    f"  →  {self._view_end.strftime('%d.%m.%Y %H:%M')}")
        self._stats_lbl.configure(
            text=f"{t('tl.visible')} {len(vis)} / {len(ev)}  •  {span}"
                 + (("  •  " + "  |  ".join(parts)) if parts else ""))

    # ── Dráhy (levý panel) ────────────────────────────────────────────────────

    def _get_lane_h(self) -> int:
        H = self._canvas.winfo_height()
        if H < 50:
            return LANE_H_MIN
        available = H - AXIS_H
        return max(LANE_H_MIN, available // len(SOURCES))

    def _on_canvas_resize(self, event):
        new_h = self._get_lane_h()
        if getattr(self, "_lane_h", None) != new_h:
            self._lane_h = new_h
            if self._events:
                self._build_lane_labels(new_h)
        self._redraw()

    def _build_lane_labels(self, lane_h: int | None = None):
        if lane_h is None:
            lane_h = self._get_lane_h()
        self._lane_h = lane_h
        for w in self._lane_labels.winfo_children():
            w.destroy()
        # Prázdné místo nahoře odpovídá výšce drah (osa X je dole)
        ctk.CTkLabel(self._lane_labels, text="",
                     height=lane_h * len(SOURCES), fg_color="transparent").pack(fill="x")
        # Osa X placeholder
        ctk.CTkLabel(self._lane_labels, text="",
                     height=AXIS_H, fg_color="transparent").pack(fill="x")
        # Překresli přes place s absolutními pozicemi
        for i, src in enumerate(SOURCES):
            cfg = SOURCE_CFG[src]
            f = ctk.CTkFrame(self._lane_labels, fg_color="transparent",
                             height=lane_h, width=LABEL_W)
            f.place(x=0, y=i * lane_h)
            f.pack_propagate(False)
            dot = ctk.CTkFrame(f, fg_color=cfg["color"],
                               width=10, height=10, corner_radius=5)
            dot.place(x=12, rely=0.5, anchor="w")
            ctk.CTkLabel(f, text=t(cfg["label_key"]),
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color=cfg["color"]).place(x=30, rely=0.5, anchor="w")

    # ── Pohled ────────────────────────────────────────────────────────────────

    def _reset_view(self):
        ev = self._active_events()
        if not ev:
            return
        self._view_start = ev[0]["dt"]
        self._view_end   = ev[-1]["dt"]
        # Přidej malý okraj
        span = self._view_end - self._view_start
        margin = span * 0.02 + datetime.timedelta(minutes=5)
        self._view_start -= margin
        self._view_end   += margin
        self._redraw()

    def _zoom(self, factor: float, center_x: int | None = None):
        if not self._view_start or not self._view_end:
            return
        W = self._canvas.winfo_width() or 800
        span = (self._view_end - self._view_start).total_seconds()
        new_span = span * factor

        # Střed zoomu (pixelová pozice → čas)
        if center_x is not None:
            frac = (center_x - PAD_L) / max(1, W - PAD_L - PAD_R)
        else:
            frac = 0.5
        frac = max(0.0, min(1.0, frac))

        center_ts = self._view_start + datetime.timedelta(seconds=span * frac)
        new_start = center_ts - datetime.timedelta(seconds=new_span * frac)
        new_end   = center_ts + datetime.timedelta(seconds=new_span * (1 - frac))

        # Neklesej pod 1 minutu celkového rozsahu
        if (new_end - new_start).total_seconds() < 60:
            return

        self._view_start = new_start
        self._view_end   = new_end
        self._redraw()

    def _zoom_in(self):  self._zoom(0.5)
    def _zoom_out(self): self._zoom(2.0)

    def _on_wheel(self, event):
        factor = 0.7 if event.delta > 0 else 1.43
        self._zoom(factor, event.x)

    # ── Posun (pan) ───────────────────────────────────────────────────────────

    def _on_press(self, event):
        self._pan_x0 = event.x
        self._pan_vs = self._view_start
        self._pan_ve = self._view_end
        self._sel_x0 = None
        if self._sel_rect:
            self._canvas.delete(self._sel_rect)
            self._sel_rect = None

    def _on_motion(self, event):
        if self._pan_x0 is None:
            return
        W = self._canvas.winfo_width() or 800
        if not self._pan_vs or not self._pan_ve:
            return
        span = (self._pan_ve - self._pan_vs).total_seconds()
        px_per_sec = (W - PAD_L - PAD_R) / max(1, span)
        delta_sec = (self._pan_x0 - event.x) / px_per_sec
        self._view_start = self._pan_vs + datetime.timedelta(seconds=delta_sec)
        self._view_end   = self._pan_ve + datetime.timedelta(seconds=delta_sec)
        self._redraw()

    def _on_release(self, event):
        self._pan_x0 = None

    def _on_scroll(self, *args):
        """Horizontální scrollbar."""
        if not self._data_start or not self._data_end:
            return
        total = (self._data_end - self._data_start).total_seconds()
        span  = (self._view_end - self._view_start).total_seconds()
        if args[0] == "moveto":
            frac = float(args[1])
            self._view_start = self._data_start + datetime.timedelta(seconds=frac * total)
            self._view_end   = self._view_start + datetime.timedelta(seconds=span)
            self._redraw()

    # ── Kreslení ──────────────────────────────────────────────────────────────

    def _dt_to_x(self, dt: datetime.datetime, W: int) -> float:
        span = (self._view_end - self._view_start).total_seconds()
        if span == 0:
            return PAD_L
        frac = (dt - self._view_start).total_seconds() / span
        return PAD_L + frac * (W - PAD_L - PAD_R)

    def _redraw(self):
        c = self._canvas
        c.delete("all")
        self._dot_map.clear()

        if not self._view_start or not self._view_end:
            return

        W       = c.winfo_width()  or 900
        H       = c.winfo_height() or 400
        lane_h  = max(LANE_H_MIN, (H - AXIS_H) // len(SOURCES))
        n_lanes = len(SOURCES)
        H_lanes = n_lanes * lane_h
        axis_y  = H_lanes

        # Pozadí drah
        for i, src in enumerate(SOURCES):
            y0 = i * lane_h
            y1 = y0 + lane_h
            bg = "#0d1117" if i % 2 == 0 else "#111827"
            c.create_rectangle(0, y0, W, y1, fill=bg, outline="")
            c.create_line(0, y1, W, y1, fill="#1a2233", width=1)

        # Osa X (časová) — dole
        c.create_rectangle(0, axis_y, W, H, fill="#080e18", outline="")
        c.create_line(PAD_L, axis_y, W - PAD_R, axis_y, fill="#334455", width=2)

        # Svislé mřížkové čáry a popisky osy
        ticks = self._compute_ticks(W)
        for tick_dt, label in ticks:
            x = self._dt_to_x(tick_dt, W)
            if x < PAD_L or x > W - PAD_R:
                continue
            c.create_line(x, 0, x, axis_y, fill="#1a2a3a", width=1, dash=(3, 6))
            c.create_line(x, axis_y, x, axis_y + 6, fill="#445566", width=1)
            c.create_text(x, axis_y + 8, text=label,
                          fill="#778899", font=("Segoe UI", 9), anchor="n")

        # Tečky událostí
        active    = {s for s, v in self._source_vars.items() if v.get()}
        lane_idx  = {src: i for i, src in enumerate(SOURCES)}
        dot_r     = min(DOT_R, lane_h // 4)

        for ev in self._events:
            if ev["source"] not in active:
                continue
            x = self._dt_to_x(ev["dt"], W)
            if x < PAD_L - dot_r or x > W - PAD_R + dot_r:
                continue
            i   = lane_idx.get(ev["source"], 0)
            y   = i * lane_h + lane_h // 2
            clr = SOURCE_CFG[ev["source"]]["color"]

            oid = c.create_oval(x - dot_r, y - dot_r,
                                x + dot_r, y + dot_r,
                                fill=clr, outline="#ffffff", width=0.8)
            self._dot_map[oid] = ev
            c.tag_bind(oid, "<Button-1>",
                       lambda e, ev=ev: EventDetailDialog(self, ev))
            c.tag_bind(oid, "<Enter>",
                       lambda e, ev=ev, x=x, y=y: self._show_tip(e, ev, x, y))
            c.tag_bind(oid, "<Leave>",
                       lambda e: self._hide_tip(e))

        # Scrollbar
        if self._data_start and self._data_end:
            total = (self._data_end - self._data_start).total_seconds()
            if total > 0:
                lo = max(0.0, (self._view_start - self._data_start).total_seconds() / total)
                hi = min(1.0, (self._view_end   - self._data_start).total_seconds() / total)
                self._hbar.set(lo, hi)

        self._update_stats()

    def _compute_ticks(self, W: int) -> list[tuple]:
        """Vypočítá vhodné časové záchyty pro osu X podle aktuálního zoomu."""
        span_sec = (self._view_end - self._view_start).total_seconds()
        px_avail = W - PAD_L - PAD_R
        min_px_between = 80

        # Kandidáti (sekundy, formát)
        candidates = [
            (60,            "%H:%M"),
            (5 * 60,        "%H:%M"),
            (15 * 60,       "%H:%M"),
            (30 * 60,       "%H:%M"),
            (3600,          "%d.%m %H:%M"),
            (3 * 3600,      "%d.%m %H:%M"),
            (6 * 3600,      "%d.%m %H:%M"),
            (12 * 3600,     "%d.%m"),
            (86400,         "%d.%m.%Y"),
            (7 * 86400,     "%d.%m.%Y"),
            (30 * 86400,    "%m/%Y"),
            (90 * 86400,    "%m/%Y"),
            (365 * 86400,   "%Y"),
        ]

        step_sec, fmt = candidates[-1]
        for s, f in candidates:
            n_ticks = span_sec / s
            if n_ticks == 0:
                continue
            px_between = px_avail / n_ticks
            if px_between >= min_px_between:
                step_sec, fmt = s, f
                break

        # Zaokrouhli první tick na násobek step_sec
        epoch = datetime.datetime(1970, 1, 1)
        start_sec = (self._view_start - epoch).total_seconds()
        first_sec = (int(start_sec / step_sec) + 1) * step_sec
        first_dt  = epoch + datetime.timedelta(seconds=first_sec)

        ticks = []
        dt = first_dt
        while dt <= self._view_end:
            ticks.append((dt, dt.strftime(fmt)))
            dt += datetime.timedelta(seconds=step_sec)
        return ticks

    # ── Tooltip ───────────────────────────────────────────────────────────────

    def _show_tip(self, event, ev: dict, x: float, y: float):
        self._hide_tip(None)
        src = SOURCE_CFG.get(ev["source"], {"label_key": "src.sms"})
        src_label = t(src["label_key"])
        text = (f"{src_label}  •  "
                f"{ev['dt'].strftime('%d. %m. %Y  %H:%M:%S')}\n"
                f"{ev['title'][:60]}")

        self._tip_win = tw = tk.Toplevel(self._canvas)
        tw.wm_overrideredirect(True)
        tw.configure(bg="#1a2233")

        tk.Label(tw, text=text, bg="#1a2233", fg="#dddddd",
                 font=("Segoe UI", 9), justify="left",
                 padx=10, pady=6).pack()

        # Pozice tooltipu vedle kurzoru
        rx = self._canvas.winfo_rootx() + int(x) + 12
        ry = self._canvas.winfo_rooty() + int(y) - 30
        tw.wm_geometry(f"+{rx}+{ry}")

    def _hide_tip(self, event):
        if self._tip_win:
            try:
                self._tip_win.destroy()
            except Exception:
                pass
            self._tip_win = None

    def _on_hover(self, event):
        # Změna kurzoru při přejezdu nad tečkou
        items = self._canvas.find_overlapping(
            event.x - DOT_R, event.y - DOT_R,
            event.x + DOT_R, event.y + DOT_R)
        if any(i in self._dot_map for i in items):
            self._canvas.configure(cursor="hand2")
        else:
            self._canvas.configure(cursor="crosshair")

    # ── Překlad ───────────────────────────────────────────────────────────────

    def refresh_lang(self):
        self._load_btn.configure(text=t("tl.load"))
        self._reset_btn.configure(text=t("tl.full_range"))
        self._zoom_in_btn.configure(text=t("tl.zoom_in"))
        self._zoom_out_btn.configure(text=t("tl.zoom_out"))
        self._placeholder.configure(text=t("tl.placeholder"))
        if not self._output_dir:
            self._dir_label.configure(text=t("tl.no_folder"))
        # Rebuild filter checkboxes with new labels
        self._build_filter_checkboxes()
        # Rebuild lane labels if data is loaded
        if self._events:
            self._build_lane_labels()
        self._update_stats()
