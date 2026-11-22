import customtkinter as ctk
import tkinter as tk
import os, json, datetime, threading
from collections import defaultdict
from core.i18n import t, field_label
from . import theme as th

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
AXIS_H       = 40
DOT_R        = 7
PAD_L        = 10
PAD_R        = 10
LANE_H_MIN   = 48
LABEL_W      = 120
CLUSTER_PX   = 10   # points closer than this px → merge into cluster
MAX_DOTS     = 4000 # max canvas objects at once (more = more clustering)


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
    """Parse extraction output files – call in a background thread."""
    events = []

    def _add(dt, source, title, detail="", raw=None, ref=None):
        # ref = (file, section, idx) of the record – used for tagging
        if dt:
            events.append({"dt": dt, "source": source,
                           "title": title, "detail": detail,
                           "raw": raw or {}, "ref": ref})

    def _list(name):
        data = _load(os.path.join(output_dir, name))
        return list(enumerate(data)) if isinstance(data, list) else []

    for i, msg in _list("sms.json"):
        if not isinstance(msg, dict):
            continue
        dt = _parse_dt(msg.get("date", ""))
        _add(dt, "sms",
             f"{msg.get('direction','')} — {msg.get('address','?')}",
             msg.get("body", "")[:80], msg, ("sms.json", "", i))

    for i, c in _list("call_logs.json"):
        if not isinstance(c, dict):
            continue
        dt = _parse_dt(c.get("date", ""))
        _add(dt, "call",
             f"{c.get('type','')} — {c.get('number','?')}",
             c.get("duration", ""), c, ("call_logs.json", "", i))

    for i, h in _list("browser_history.json"):
        if not isinstance(h, dict):
            continue
        dt = _parse_dt(h.get("date", ""))
        _add(dt, "browser",
             h.get("title", "") or h.get("url", "")[:60],
             h.get("url", "")[:100], h, ("browser_history.json", "", i))

    fa = _load(os.path.join(output_dir, "file_analysis.json"))
    files = fa.get("files", fa) if isinstance(fa, dict) else fa
    # same section naming as the search index: list in a dict → section = key
    fa_section = "files" if isinstance(fa, dict) and files is not fa else ""
    if isinstance(files, list):
        for i, f in enumerate(files):
            if not isinstance(f, dict):
                continue
            dt = (_parse_dt(f.get("date_taken", "")) or
                  _parse_dt(f.get("modified", "")))
            src = "photo" if f.get("type") == "image" else "file"
            gps = (f" [{f['gps_latitude']:.4f}, {f['gps_longitude']:.4f}]"
                   if "gps_latitude" in f else "")
            _add(dt, src, f.get("filename", ""),
                 f.get("camera_model", "") + gps, f, ("file_analysis.json", fa_section, i))

    events.sort(key=lambda e: e["dt"])
    return events


def _display_value(key: str, value) -> str:
    """Field value for display in the detail (translation of typical values)."""
    if isinstance(value, bool):
        return t("val.yes") if value else t("val.no")
    if key == "type" and value == "image":
        return t("tl.val_image")
    return str(value)


# ── Detail dialog ─────────────────────────────────────────────────────────────

class EventDetailDialog(ctk.CTkToplevel):
    def __init__(self, master, event: dict, output_dir: str = "", on_tags_changed=None):
        super().__init__(master)
        self._event = event
        self._output_dir = output_dir
        self._on_tags_changed = on_tags_changed
        src = SOURCE_CFG.get(event["source"], {"color": ACCENT, "label_key": "src.sms"})
        src_label = t(src["label_key"])
        self.title(f"{t('tl.det_title')} — {src_label}")
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
            (t("tl.det_source"), src_label),
            (t("tl.det_date"),   event["dt"].strftime("%d. %m. %Y")),
            (t("tl.det_time"),   event["dt"].strftime("%H:%M:%S")),
            (t("tl.det_desc"),   event["title"]),
        ]
        if event.get("detail"):
            base.append((t("tl.det_detail"), event["detail"]))

        raw = event.get("raw", {})
        all_fields = base + [(field_label(k), _display_value(k, v))
                             for k, v in raw.items()
                             if str(v).strip() and k != "path"]

        if "gps_latitude" in raw:
            all_fields.append((t("tl.det_gps"),
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

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=15, pady=(0, 10))
        ctk.CTkButton(bottom, text=t("res.close"), height=32, width=110,
                      fg_color="#2a2a4a", hover_color="#3a3a5a",
                      command=self.destroy).pack(side="right")
        self._tag_lbl = ctk.CTkLabel(bottom, text="", font=ctk.CTkFont(size=11),
                                     text_color="#ffcc80", anchor="w")
        if self._tag_row() is not None:
            self._tag_btn = ctk.CTkButton(bottom, text=t("res.tag_btn"), height=32, width=110,
                                          fg_color="#2a2a4a", hover_color="#3a3a5a",
                                          command=self._tag_menu)
            self._tag_btn.pack(side="left")
            self._tag_lbl.pack(side="left", padx=10, fill="x", expand=True)
            self._show_tags()

    def _tag_row(self):
        ref = self._event.get("ref")
        if not (self._output_dir and ref and self._event.get("raw")):
            return None
        from core.case_tags import make_row
        file, section, idx = ref
        return make_row(file, self._event["raw"], section, idx)

    def _show_tags(self):
        from core.case_tags import CaseTags
        row = self._tag_row()
        try:
            with CaseTags(self._output_dir) as tg:
                names = tg.tags_for([row["row_key"]]).get(row["row_key"], [])
        except Exception:
            names = []
        self._tag_lbl.configure(text=("🏷 " + ", ".join(names)) if names else "")

    def _tag_menu(self):
        from .tagging import popup_tag_menu
        b = self._tag_btn

        def changed(msg):
            self._show_tags()
            if self._on_tags_changed:
                self._on_tags_changed(msg)

        popup_tag_menu(self, self._output_dir, [self._tag_row()],
                       b.winfo_rootx(), b.winfo_rooty() + b.winfo_height(), on_change=changed)


# ── Main panel ────────────────────────────────────────────────────────────────

class TimelinePanel(ctk.CTkFrame):
    def __init__(self, master, accent: str = ACCENT, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.accent = accent
        self._fam = ctk.CTkFont().cget("family")   # match app-wide font on canvas text
        self._events: list[dict] = []
        self._output_dir = ""
        self.on_tags_changed = None      # set by the app (refreshes the Search panel)

        self._view_start: datetime.datetime | None = None
        self._view_end:   datetime.datetime | None = None
        self._data_start: datetime.datetime | None = None
        self._data_end:   datetime.datetime | None = None

        self._pan_x0: int | None = None
        self._pan_vs: datetime.datetime | None = None
        self._pan_ve: datetime.datetime | None = None
        self._pan_moved = False

        self._sel_rect = None
        self._source_vars: dict[str, ctk.BooleanVar] = {}
        self._filter_checks: dict[str, ctk.CTkCheckBox] = {}

        # Shared map canvas_id → event (used by canvas-level binding)
        self._dot_map: dict[int, dict] = {}

        # Debounce: wait 40 ms after the last call before redrawing
        self._redraw_after_id = None

        self._loading = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build(self):
        tb = th.Toolbar(self)
        tb.grid(row=0, column=0, sticky="ew", pady=(0, th.GAP))

        # Source: extraction folder
        self._load_btn = th.secondary_button(tb.source, t("tl.load"), self._load_from_dialog,
                                             width=150)
        self._load_btn.pack(side="left", padx=(0, 10))
        self._dir_label = ctk.CTkLabel(tb.source, text=t("tl.no_folder"),
                                       text_color=TEXT_MUTED, font=th.f_small(), anchor="w")
        self._dir_label.pack(side="left", padx=(0, 16))

        # Source filters (left, next to the source)
        self._filter_frame = ctk.CTkFrame(tb.source, fg_color="transparent")
        self._filter_frame.pack(side="left")
        self._build_filter_checkboxes()

        # Statistika
        self._stats_lbl = ctk.CTkLabel(tb.info, text="", text_color=TEXT_MUTED,
                                       font=th.f_small())
        self._stats_lbl.pack(side="right")

        # View controls (right, next to the actions)
        self._reset_btn = th.secondary_button(tb.actions, t("tl.full_range"),
                                              self._reset_view, width=110)
        self._zoom_in_btn = th.secondary_button(tb.actions, t("tl.zoom_in"),
                                                self._zoom_in, width=90)
        self._zoom_out_btn = th.secondary_button(tb.actions, t("tl.zoom_out"),
                                                 self._zoom_out, width=90)
        th.pack_row(self._reset_btn, self._zoom_in_btn, self._zoom_out_btn)

        # ── Main area ─────────────────────────────────────────────────────────
        self._main = ctk.CTkFrame(self, fg_color="transparent")
        self._main.grid(row=1, column=0, sticky="nsew")
        self._main.grid_columnconfigure(0, weight=1)
        self._main.grid_rowconfigure(0, weight=1)

        canvas_wrap = ctk.CTkFrame(self._main, fg_color=BG_DARK, corner_radius=0)
        canvas_wrap.grid(row=0, column=0, sticky="nsew")
        canvas_wrap.grid_columnconfigure(0, weight=1)
        canvas_wrap.grid_rowconfigure(0, weight=1)

        self._canvas = tk.Canvas(canvas_wrap, bg="#0d1117",
                                  highlightthickness=0, cursor="crosshair")
        self._canvas.grid(row=0, column=0, sticky="nsew")

        self._hbar = ctk.CTkScrollbar(canvas_wrap, orientation="horizontal",
                                       command=self._on_scroll)
        self._hbar.grid(row=1, column=0, sticky="ew", padx=2, pady=(2, 2))

        self._tip_win: tk.Toplevel | None = None

        # ── Bindings – canvas-level (not per-item) ───────────────────────────
        self._canvas.bind("<Configure>",      self._on_canvas_resize)
        self._canvas.bind("<MouseWheel>",     self._on_wheel)
        self._canvas.bind("<ButtonPress-1>",  self._on_press)
        self._canvas.bind("<B1-Motion>",      self._on_motion)
        self._canvas.bind("<ButtonRelease-1>",self._on_release)
        self._canvas.bind("<ButtonPress-3>",  lambda e: self._reset_view())
        self._canvas.bind("<Motion>",         self._on_hover)
        self._canvas.bind("<Leave>",          self._hide_tip)
        # Note: <Button-1> is the same as <ButtonPress-1> – a click on a dot is therefore
        # evaluated on button release, if the mouse was not dragged (_on_release).

        self._placeholder = ctk.CTkLabel(
            self._main,
            text=t("tl.placeholder"),
            text_color=TEXT_MUTED, font=ctk.CTkFont(size=13),
            wraplength=480, justify="center")
        self._placeholder.grid(row=0, column=0, pady=60)

    def _build_filter_checkboxes(self):
        for w in self._filter_frame.winfo_children():
            w.destroy()
        self._filter_checks.clear()
        for src, cfg in SOURCE_CFG.items():
            if src not in self._source_vars:
                self._source_vars[src] = ctk.BooleanVar(value=True)
            cb = ctk.CTkCheckBox(
                self._filter_frame,
                text=t(cfg["label_key"]),
                variable=self._source_vars[src],
                width=20, height=20,
                checkbox_width=20, checkbox_height=20,
                fg_color=cfg["color"], checkmark_color="#fff",
                hover_color=cfg["color"],
                font=ctk.CTkFont(size=11),
                command=self._redraw_debounced
            )
            cb.pack(side="left", padx=5)
            self._filter_checks[src] = cb

    # ── Data loading (background thread) ─────────────────────────────────────

    def _load_from_dialog(self):
        from tkinter import filedialog
        folder = filedialog.askdirectory(title=t("tl.folder_title"))
        if folder:
            self.load_dir(folder)

    def load_dir(self, output_dir: str):
        """Start loading in a background thread – does not block the UI."""
        if self._loading:
            return
        self._loading = True
        self._output_dir = output_dir
        self._dir_label.configure(text=os.path.basename(output_dir))
        self._load_btn.configure(state="disabled", text=t("tl.loading_btn"))
        self._stats_lbl.configure(text=t("tl.loading_data"))

        def _worker():
            events = load_events(output_dir)
            self.after(0, lambda: self._apply_events(events))

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_events(self, events: list[dict]):
        self._loading = False
        self._load_btn.configure(state="normal", text=t("tl.load"))
        self._events = events
        if events:
            self._placeholder.grid_remove()
            self._data_start = events[0]["dt"]
            self._data_end   = events[-1]["dt"]
            self._reset_view()
        self._update_stats()

    # ── Filters and view ─────────────────────────────────────────────────────

    def _active_events(self) -> list[dict]:
        active = {s for s, v in self._source_vars.items() if v.get()}
        return [e for e in self._events if e["source"] in active]

    def _visible_events(self) -> list[dict]:
        if not self._view_start or not self._view_end:
            return []
        vs, ve = self._view_start, self._view_end
        return [e for e in self._active_events() if vs <= e["dt"] <= ve]

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

    # ── Lanes (left panel) ────────────────────────────────────────────────────

    def _get_lane_h(self) -> int:
        H = self._canvas.winfo_height()
        if H < 50:
            return LANE_H_MIN
        return max(LANE_H_MIN, (H - AXIS_H) // len(SOURCES))

    def _on_canvas_resize(self, event):
        new_h = self._get_lane_h()
        if getattr(self, "_lane_h", None) != new_h:
            self._lane_h = new_h
        self._redraw_debounced()

    # ── View and navigation ───────────────────────────────────────────────────

    def _reset_view(self):
        ev = self._active_events()
        if not ev:
            return
        self._view_start = ev[0]["dt"]
        self._view_end   = ev[-1]["dt"]
        span = self._view_end - self._view_start
        margin = span * 0.02 + datetime.timedelta(minutes=5)
        self._view_start -= margin
        self._view_end   += margin
        self._redraw_debounced()

    def _zoom(self, factor: float, center_x: int | None = None):
        if not self._view_start or not self._view_end:
            return
        W = self._canvas.winfo_width() or 800
        span = (self._view_end - self._view_start).total_seconds()
        new_span = span * factor
        frac = max(0.0, min(1.0,
                   (center_x - PAD_L) / max(1, W - PAD_L - PAD_R)
                   if center_x is not None else 0.5))
        center_ts = self._view_start + datetime.timedelta(seconds=span * frac)
        self._view_start = center_ts - datetime.timedelta(seconds=new_span * frac)
        self._view_end   = center_ts + datetime.timedelta(seconds=new_span * (1 - frac))
        if (self._view_end - self._view_start).total_seconds() < 60:
            return
        self._redraw_debounced()

    def _zoom_in(self):  self._zoom(0.5)
    def _zoom_out(self): self._zoom(2.0)

    def _on_wheel(self, event):
        self._zoom(0.7 if event.delta > 0 else 1.43, event.x)

    def _on_press(self, event):
        self._hide_tip(None)
        self._pan_x0 = event.x
        self._pan_moved = False
        self._pan_vs = self._view_start
        self._pan_ve = self._view_end
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
        if abs(event.x - self._pan_x0) > 3:
            if not self._pan_moved:
                self._pan_moved = True
                self._canvas.configure(cursor="fleur")
                self._hide_tip(None)
        if not self._pan_moved:
            return
        delta_sec = (self._pan_x0 - event.x) / px_per_sec
        self._view_start = self._pan_vs + datetime.timedelta(seconds=delta_sec)
        self._view_end   = self._pan_ve + datetime.timedelta(seconds=delta_sec)
        self._redraw_debounced()

    def _on_release(self, event):
        was_click = self._pan_x0 is not None and not self._pan_moved
        self._pan_x0 = None
        self._pan_moved = False
        self._canvas.configure(cursor="crosshair")
        if was_click:
            self._on_canvas_click(event)

    def _on_scroll(self, *args):
        if not self._data_start or not self._data_end:
            return
        total = (self._data_end - self._data_start).total_seconds()
        span  = (self._view_end - self._view_start).total_seconds()
        if args[0] == "moveto":
            frac = float(args[1])
            self._view_start = self._data_start + datetime.timedelta(seconds=frac * total)
            self._view_end   = self._view_start + datetime.timedelta(seconds=span)
            self._redraw_debounced()

    # ── Drawing with debounce ─────────────────────────────────────────────────

    def _redraw_debounced(self):
        """Delay redraw by 40 ms – merges rapid consecutive requests."""
        if self._redraw_after_id is not None:
            self.after_cancel(self._redraw_after_id)
        self._redraw_after_id = self.after(40, self._redraw)

    def _dt_to_x(self, dt: datetime.datetime, W: int) -> float:
        span = (self._view_end - self._view_start).total_seconds()
        if span == 0:
            return PAD_L
        frac = (dt - self._view_start).total_seconds() / span
        return PAD_L + frac * (W - PAD_L - PAD_R)

    def _redraw(self):
        self._redraw_after_id = None
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

        # Lane backgrounds
        for i, src in enumerate(SOURCES):
            y0 = i * lane_h
            y1 = y0 + lane_h
            c.create_rectangle(0, y0, W, y1,
                               fill="#0d1117" if i % 2 == 0 else "#111827",
                               outline="")
            c.create_line(0, y1, W, y1, fill="#1a2233", width=1)
            # Lane label (top-left of the lane) – replaces the old side panel
            cfg = SOURCE_CFG[src]
            c.create_oval(PAD_L, y0 + 6, PAD_L + 9, y0 + 15,
                          fill=cfg["color"], outline="")
            c.create_text(PAD_L + 15, y0 + 11, text=t(cfg["label_key"]),
                          fill=cfg["color"], font=(self._fam, 9, "bold"),
                          anchor="w")

        # Osa X
        c.create_rectangle(0, axis_y, W, H, fill="#080e18", outline="")
        c.create_line(PAD_L, axis_y, W - PAD_R, axis_y, fill="#334455", width=2)

        # Grid lines and labels
        for tick_dt, label in self._compute_ticks(W):
            x = self._dt_to_x(tick_dt, W)
            if x < PAD_L or x > W - PAD_R:
                continue
            c.create_line(x, 0, x, axis_y, fill="#1a2a3a", width=1, dash=(3, 6))
            c.create_line(x, axis_y, x, axis_y + 6, fill="#445566", width=1)
            c.create_text(x, axis_y + 8, text=label,
                          fill="#778899", font=(self._fam, 9), anchor="n")

        # ── Dots / clusters ───────────────────────────────────────────────────
        active   = {s for s, v in self._source_vars.items() if v.get()}
        lane_idx = {src: i for i, src in enumerate(SOURCES)}
        dot_r    = min(DOT_R, lane_h // 4)

        # Sort visible events and cluster nearby points
        visible_by_lane: dict[str, list[dict]] = defaultdict(list)
        for ev in self._events:
            if ev["source"] not in active:
                continue
            x = self._dt_to_x(ev["dt"], W)
            if x < PAD_L - dot_r or x > W - PAD_R + dot_r:
                continue
            ev["_x"] = x
            visible_by_lane[ev["source"]].append(ev)

        for src, lane_events in visible_by_lane.items():
            color = SOURCE_CFG[src]["color"]
            i     = lane_idx.get(src, 0)
            cy    = i * lane_h + lane_h // 2

            clusters = _cluster_events(lane_events, CLUSTER_PX)

            for cluster in clusters:
                x   = cluster["x"]
                cnt = cluster["count"]
                rep = cluster["rep"]          # representative event for click

                if cnt == 1:
                    oid = c.create_oval(x - dot_r, cy - dot_r,
                                        x + dot_r, cy + dot_r,
                                        fill=color, outline="#ffffff", width=0.8)
                    self._dot_map[oid] = rep
                else:
                    # Larger dot with count
                    r2 = dot_r + min(6, cnt // 5)
                    oid = c.create_oval(x - r2, cy - r2,
                                        x + r2, cy + r2,
                                        fill=color, outline="#ffffff", width=1.2)
                    c.create_text(x, cy, text=str(cnt) if cnt < 1000 else "999+",
                                  fill="#ffffff", font=(self._fam, 7, "bold"))
                    self._dot_map[oid] = rep

        # Scrollbar
        if self._data_start and self._data_end:
            total = (self._data_end - self._data_start).total_seconds()
            if total > 0:
                lo = max(0.0, (self._view_start - self._data_start).total_seconds() / total)
                hi = min(1.0, (self._view_end   - self._data_start).total_seconds() / total)
                self._hbar.set(lo, hi)

        self._update_stats()

    # ── Canvas-level clicking and hovering ───────────────────────────────────

    def _on_canvas_click(self, event):
        """Single binding for the whole canvas – finds the nearest dot."""
        r = DOT_R + 8
        items = self._canvas.find_overlapping(
            event.x - r, event.y - r, event.x + r, event.y + r)
        for item in items:
            if item in self._dot_map:
                EventDetailDialog(self, self._dot_map[item], self._output_dir,
                                  on_tags_changed=self.on_tags_changed)
                return

    def _on_hover(self, event):
        if self._pan_x0 is not None:
            return
        r = DOT_R + 4
        items = self._canvas.find_overlapping(
            event.x - r, event.y - r, event.x + r, event.y + r)
        hit = next((i for i in items if i in self._dot_map), None)
        if hit:
            self._canvas.configure(cursor="hand2")
            ev = self._dot_map[hit]
            x  = ev.get("_x", event.x)
            self._show_tip(event, ev, x, event.y)
        else:
            self._canvas.configure(cursor="crosshair")
            self._hide_tip(None)

    # ── Tooltip ───────────────────────────────────────────────────────────────

    def _show_tip(self, event, ev: dict, x: float, y: float):
        self._hide_tip(None)
        src = SOURCE_CFG.get(ev["source"], {"label_key": "src.sms"})
        text = (f"{t(src['label_key'])}  •  "
                f"{ev['dt'].strftime('%d. %m. %Y  %H:%M:%S')}\n"
                f"{ev['title'][:60]}")
        self._tip_win = tw = tk.Toplevel(self._canvas)
        tw.wm_overrideredirect(True)
        tw.configure(bg="#1a2233")
        tk.Label(tw, text=text, bg="#1a2233", fg="#dddddd",
                 font=(self._fam, 9), justify="left",
                 padx=10, pady=6).pack()
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

    # ── X-axis ticks ─────────────────────────────────────────────────────────

    def _compute_ticks(self, W: int) -> list[tuple]:
        span_sec = (self._view_end - self._view_start).total_seconds()
        px_avail = W - PAD_L - PAD_R
        candidates = [
            (60,          "%H:%M"),   (5*60,       "%H:%M"),
            (15*60,       "%H:%M"),   (30*60,      "%H:%M"),
            (3600,        "%d.%m %H:%M"), (3*3600, "%d.%m %H:%M"),
            (6*3600,      "%d.%m %H:%M"), (12*3600,"%d.%m"),
            (86400,       "%d.%m.%Y"),    (7*86400,"%d.%m.%Y"),
            (30*86400,    "%m/%Y"),        (90*86400,"%m/%Y"),
            (365*86400,   "%Y"),
        ]
        step_sec, fmt = candidates[-1]
        for s, f in candidates:
            if span_sec / s > 0 and px_avail / (span_sec / s) >= 80:
                step_sec, fmt = s, f
                break
        epoch = datetime.datetime(1970, 1, 1)
        start_sec = (self._view_start - epoch).total_seconds()
        first_dt  = epoch + datetime.timedelta(
            seconds=(int(start_sec / step_sec) + 1) * step_sec)
        ticks, dt = [], first_dt
        while dt <= self._view_end:
            ticks.append((dt, dt.strftime(fmt)))
            dt += datetime.timedelta(seconds=step_sec)
        return ticks

    # ── Language refresh ──────────────────────────────────────────────────────

    def refresh_lang(self):
        self._load_btn.configure(text=t("tl.load"))
        self._reset_btn.configure(text=t("tl.full_range"))
        self._zoom_in_btn.configure(text=t("tl.zoom_in"))
        self._zoom_out_btn.configure(text=t("tl.zoom_out"))
        self._placeholder.configure(text=t("tl.placeholder"))
        if not self._output_dir:
            self._dir_label.configure(text=t("tl.no_folder"))
        self._build_filter_checkboxes()
        if self._events:
            self._redraw_debounced()
        self._update_stats()


# ── Clustering ────────────────────────────────────────────────────────────────

def _cluster_events(events: list[dict], px_threshold: float) -> list[dict]:
    """
    Merge events closer than px_threshold pixels (within a single lane).
    Returns a list of clusters: {"x": float, "count": int, "rep": dict}
    """
    if not events:
        return []
    events = sorted(events, key=lambda e: e["_x"])
    clusters: list[dict] = []
    cur_x    = events[0]["_x"]
    cur_rep  = events[0]
    cur_cnt  = 1

    for ev in events[1:]:
        if ev["_x"] - cur_x <= px_threshold:
            cur_cnt += 1
        else:
            clusters.append({"x": cur_x, "count": cur_cnt, "rep": cur_rep})
            cur_x   = ev["_x"]
            cur_rep = ev
            cur_cnt = 1

    clusters.append({"x": cur_x, "count": cur_cnt, "rep": cur_rep})
    return clusters
