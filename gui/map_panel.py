"""
gui/map_panel.py – Mapa GPS lokací fotografií z extrakce.
Zobrazuje místa pořízení fotek z file_analysis.json s datem a detaily.
"""
import os
import json
import datetime
import threading
import customtkinter as ctk
from tkinter import filedialog
from core.i18n import t

try:
    import tkintermapview
    MAP_AVAILABLE = True
except ImportError:
    MAP_AVAILABLE = False

ACCENT     = "#00d4ff"
BG_CARD    = "#16213e"
BG_PANEL   = "#1a1a2e"
BG_DARK    = "#0f0f1a"
TEXT_MUTED = "#888888"
TEXT_DIM   = "#444466"
GREEN      = "#00c853"
WHITE      = "#ffffff"
MARKER_COLOR = "#00d4ff"


def _parse_date(raw: str) -> str:
    """Převede EXIF datum '2024:01:15 14:30:00' na čitelný formát."""
    if not raw:
        return "—"
    try:
        dt = datetime.datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
        return dt.strftime("%d. %m. %Y  %H:%M")
    except Exception:
        return raw


class MapPanel(ctk.CTkFrame):
    def __init__(self, master, accent: str = ACCENT, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.accent = accent
        self._photos: list[dict] = []       # záznamy s GPS
        self._markers: list = []            # tkintermapview markery
        self._selected_idx: int | None = None
        self._list_rows: list[ctk.CTkFrame] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()

    # ── Stavba UI ─────────────────────────────────────────────────────────────

    def _build(self):
        # ── Horní lišta ───────────────────────────────────────────────────────
        bar = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        bar.grid_columnconfigure(1, weight=1)

        btn_frame = ctk.CTkFrame(bar, fg_color="transparent")
        btn_frame.grid(row=0, column=0, padx=16, pady=12, sticky="w")

        self._load_btn = ctk.CTkButton(
            btn_frame, text=t("map.load"),
            width=160, height=34,
            fg_color=ACCENT, text_color="#000",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._pick_folder,
        )
        self._load_btn.pack(side="left", padx=(0, 10))

        self._center_btn = ctk.CTkButton(
            btn_frame, text=t("map.center"),
            width=110, height=34,
            fg_color="#2a2a4a", hover_color="#3a3a5a",
            font=ctk.CTkFont(size=12),
            command=self._fit_map,
        )
        self._center_btn.pack(side="left")

        self._status_lbl = ctk.CTkLabel(
            bar, text=t("map.placeholder"),
            font=ctk.CTkFont(size=12),
            text_color=TEXT_MUTED,
        )
        self._status_lbl.grid(row=0, column=1, padx=16, sticky="w")

        # ── Hlavní tělo ───────────────────────────────────────────────────────
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=0, minsize=300)
        body.grid_rowconfigure(0, weight=1)

        self._build_map(body)
        self._build_list(body)

    def _build_map(self, parent):
        map_card = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12)
        map_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        map_card.grid_columnconfigure(0, weight=1)
        map_card.grid_rowconfigure(0, weight=1)

        if not MAP_AVAILABLE:
            ctk.CTkLabel(
                map_card,
                text="⚠  Modul tkintermapview není nainstalován.\n\n"
                     "Spusťte:  pip install tkintermapview",
                font=ctk.CTkFont(size=13),
                text_color=TEXT_MUTED,
                justify="center",
            ).grid(row=0, column=0, padx=40, pady=40)
            self._map = None
            return

        self._map = tkintermapview.TkinterMapView(
            map_card,
            corner_radius=10,
        )
        self._map.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        self._map.set_tile_server(
            "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png"
        )
        self._map.set_position(50.08, 14.42)   # výchozí: Praha
        self._map.set_zoom(5)

    def _build_list(self, parent):
        list_card = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12)
        list_card.grid(row=0, column=1, sticky="nsew")
        list_card.grid_columnconfigure(0, weight=1)
        list_card.grid_rowconfigure(2, weight=1)

        # Hlavička seznamu
        hdr = ctk.CTkFrame(list_card, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        hdr.grid_columnconfigure(0, weight=1)

        self._list_title = ctk.CTkLabel(
            hdr, text=t("map.list_title"),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=ACCENT,
            anchor="w",
        )
        self._list_title.grid(row=0, column=0, sticky="w")

        self._count_lbl = ctk.CTkLabel(
            hdr, text="",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
            anchor="e",
        )
        self._count_lbl.grid(row=0, column=1, sticky="e")

        ctk.CTkFrame(list_card, height=1, fg_color="#2a2a3e").grid(
            row=1, column=0, sticky="ew", padx=14, pady=(0, 0)
        )

        # Scrollovatelný seznam
        self._scroll = ctk.CTkScrollableFrame(
            list_card, fg_color="transparent",
        )
        self._scroll.grid(row=2, column=0, sticky="nsew", padx=6, pady=6)
        self._scroll.grid_columnconfigure(0, weight=1)

        # Detail vybrané fotky
        self._detail_card = ctk.CTkFrame(list_card, fg_color=BG_PANEL, corner_radius=8)
        self._detail_card.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 8))
        self._detail_card.grid_columnconfigure(0, weight=1)

        self._detail_lbl = ctk.CTkLabel(
            self._detail_card,
            text=t("map.detail_empty"),
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
            wraplength=260,
            justify="left",
            anchor="w",
        )
        self._detail_lbl.grid(row=0, column=0, padx=12, pady=10, sticky="w")

    # ── Načítání dat ──────────────────────────────────────────────────────────

    def _pick_folder(self):
        folder = filedialog.askdirectory(
            title=t("map.folder_title"),
            initialdir=os.path.expanduser("~"),
        )
        if folder:
            self.load_dir(folder)

    def load_dir(self, folder: str):
        """Načte GPS data z file_analysis.json v dané složce extrakce."""
        self._status_lbl.configure(text=t("map.loading"), text_color=ACCENT)
        self._load_btn.configure(state="disabled")

        def _worker():
            photos = _load_gps_from_folder(folder)
            self.after(0, lambda: self._apply_data(photos))

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_data(self, photos: list[dict]):
        self._photos = photos
        self._load_btn.configure(state="normal")

        if not photos:
            self._status_lbl.configure(
                text=t("map.no_gps"), text_color=TEXT_MUTED
            )
            return

        self._status_lbl.configure(
            text=t("map.found").format(n=len(photos)),
            text_color=GREEN,
        )
        self._count_lbl.configure(text=f"{len(photos)} fotek")
        self._rebuild_list()
        self._rebuild_markers()
        self._fit_map()

    def _rebuild_list(self):
        for w in self._scroll.winfo_children():
            w.destroy()
        self._list_rows.clear()

        for i, photo in enumerate(self._photos):
            row = ctk.CTkFrame(
                self._scroll, fg_color=BG_CARD, corner_radius=8, cursor="hand2",
            )
            row.grid(row=i, column=0, sticky="ew", padx=2, pady=3)
            row.grid_columnconfigure(1, weight=1)

            # Pin ikona
            ctk.CTkLabel(
                row, text="📍",
                font=ctk.CTkFont(size=16),
            ).grid(row=0, column=0, rowspan=2, padx=(10, 6), pady=8)

            # Název souboru
            ctk.CTkLabel(
                row,
                text=photo.get("filename", "—"),
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=WHITE,
                anchor="w",
            ).grid(row=0, column=1, sticky="w", pady=(7, 0))

            # Datum
            date_str = _parse_date(photo.get("date_taken", ""))
            ctk.CTkLabel(
                row,
                text=date_str,
                font=ctk.CTkFont(size=10),
                text_color=TEXT_MUTED,
                anchor="w",
            ).grid(row=1, column=1, sticky="w", pady=(0, 7))

            # GPS souřadnice
            lat = photo.get("gps_latitude", 0)
            lon = photo.get("gps_longitude", 0)
            ctk.CTkLabel(
                row,
                text=f"{lat:.5f}, {lon:.5f}",
                font=ctk.CTkFont(size=10),
                text_color=TEXT_DIM,
                anchor="e",
            ).grid(row=0, column=2, rowspan=2, padx=(4, 10))

            # Klik → vyber
            for widget in row.winfo_children():
                widget.bind("<Button-1>", lambda e, idx=i: self._select(idx))
            row.bind("<Button-1>", lambda e, idx=i: self._select(idx))

            self._list_rows.append(row)

    def _rebuild_markers(self):
        if not self._map:
            return
        for m in self._markers:
            m.delete()
        self._markers.clear()

        for i, photo in enumerate(self._photos):
            lat = photo.get("gps_latitude")
            lon = photo.get("gps_longitude")
            if lat is None or lon is None:
                continue
            date_str = _parse_date(photo.get("date_taken", ""))
            label = f"{photo.get('filename', '?')}\n{date_str}"
            marker = self._map.set_marker(
                lat, lon,
                text=label,
                marker_color_circle=MARKER_COLOR,
                marker_color_outside="#0a0f1e",
                text_color=WHITE,
                command=lambda m, idx=i: self._select(idx),
            )
            self._markers.append(marker)

    def _fit_map(self):
        """Nastaví pohled mapy tak, aby byly vidět všechny markery."""
        if not self._map or not self._photos:
            return
        lats = [p["gps_latitude"]  for p in self._photos if "gps_latitude"  in p]
        lons = [p["gps_longitude"] for p in self._photos if "gps_longitude" in p]
        if not lats:
            return
        if len(lats) == 1:
            self._map.set_position(lats[0], lons[0])
            self._map.set_zoom(13)
        else:
            self._map.fit_bounding_box(
                (max(lats), min(lons)),
                (min(lats), max(lons)),
            )

    # ── Výběr ─────────────────────────────────────────────────────────────────

    def _select(self, idx: int):
        # Odznač předchozí
        if self._selected_idx is not None and self._selected_idx < len(self._list_rows):
            self._list_rows[self._selected_idx].configure(fg_color=BG_CARD)

        self._selected_idx = idx
        if idx < len(self._list_rows):
            self._list_rows[idx].configure(fg_color="#1e2a4a")
            # Scrolluj do pohledu
            self._list_rows[idx].update_idletasks()

        if idx < len(self._photos):
            photo = self._photos[idx]
            # Střed mapy na vybraný marker
            if self._map and "gps_latitude" in photo:
                self._map.set_position(photo["gps_latitude"], photo["gps_longitude"])
                self._map.set_zoom(14)
            # Detail panel
            self._show_detail(photo)

    def _show_detail(self, photo: dict):
        date_str  = _parse_date(photo.get("date_taken", ""))
        lat       = photo.get("gps_latitude", "—")
        lon       = photo.get("gps_longitude", "—")
        alt       = photo.get("gps_altitude_m")
        cam_make  = photo.get("camera_make", "")
        cam_model = photo.get("camera_model", "")
        camera    = f"{cam_make} {cam_model}".strip() or "—"
        size_kb   = photo.get("size_kb", "")
        mp        = photo.get("megapixels", "")

        lines = [
            f"📁 {photo.get('filename', '—')}",
            f"📅 {date_str}",
            f"📍 {lat:.6f}, {lon:.6f}" if isinstance(lat, float) else f"📍 {lat}, {lon}",
        ]
        if alt is not None:
            lines.append(f"⛰  {alt} m n.m.")
        if camera != "—":
            lines.append(f"📷 {camera}")
        if mp:
            lines.append(f"🖼  {mp} Mpx" + (f"  ·  {size_kb} KB" if size_kb else ""))

        self._detail_lbl.configure(text="\n".join(lines), text_color=WHITE)

    # ── Překlad ──────────────────────────────────────────────────────────────

    def refresh_lang(self):
        pass


# ── Pomocná funkce: parsování GPS z extrakční složky ─────────────────────────

def _load_gps_from_folder(folder: str) -> list[dict]:
    """
    Přečte file_analysis.json z extrakční složky a vrátí záznamy s GPS souřadnicemi,
    seřazené podle data pořízení.
    """
    json_path = os.path.join(folder, "file_analysis.json")
    if not os.path.isfile(json_path):
        return []

    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []

    files = data.get("files", [])
    if not files:
        # Starší formát – flat list
        files = data if isinstance(data, list) else []

    photos = [
        entry for entry in files
        if "gps_latitude" in entry and "gps_longitude" in entry
    ]

    # Seřadit podle data pořízení
    def _sort_key(e):
        raw = e.get("date_taken", "")
        try:
            return datetime.datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
        except Exception:
            return datetime.datetime.min

    photos.sort(key=_sort_key)
    return photos
