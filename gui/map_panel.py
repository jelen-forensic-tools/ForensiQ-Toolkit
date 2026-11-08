"""
gui/map_panel.py – Map of GPS locations of photos from extraction.
Shows photo capture locations from file_analysis.json with date and details.
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

# OpenStreetMap blocks the generic "TkinterMapView" User-Agent that the library
# hard-codes ("Access blocked" tiles). Inject an application-specific UA.
TILE_USER_AGENT = "ForenziQ/1.0 (Android forensic toolkit; tkintermapview)"
OSM_TILE_URL    = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

# Persistent on-disk tile cache (OSM tile policy asks clients to cache tiles).
TILE_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".forenziq", "tile_cache")
TILE_CACHE_MAX_AGE = 30 * 24 * 3600   # re-download tiles older than 30 days

if MAP_AVAILABLE:
    try:
        import io
        import time
        import hashlib
        import requests as _requests
        from requests.adapters import HTTPAdapter
        from tkintermapview import map_widget as _mw, offline_loading as _ol

        # One shared session = keep-alive connections, no TLS handshake per tile.
        # tkintermapview runs 25 loader threads, so size the pool accordingly.
        _session = _requests.Session()
        _session.headers["User-Agent"] = TILE_USER_AGENT
        _adapter = HTTPAdapter(pool_connections=4, pool_maxsize=32, max_retries=2)
        _session.mount("https://", _adapter)
        _session.mount("http://", _adapter)

        class _CachedResponse:
            """Minimal response object compatible with how tkintermapview uses it."""
            status_code = 200

            def __init__(self, content: bytes):
                self.content = content
                self.raw = io.BytesIO(content)

        def _cache_path(url: str) -> str:
            h = hashlib.sha1(url.encode("utf-8")).hexdigest()
            return os.path.join(TILE_CACHE_DIR, h[:2], h + ".png")

        class _RequestsWithUA:
            """Proxy for the requests module: custom User-Agent, pooled
            connections and a persistent disk cache for map tiles."""
            def __getattr__(self, name):
                return getattr(_requests, name)

            @staticmethod
            def get(url, *args, **kwargs):
                kwargs.pop("headers", None)
                kwargs.pop("stream", None)
                kwargs.setdefault("timeout", 15)

                path = _cache_path(url)
                try:
                    if time.time() - os.path.getmtime(path) < TILE_CACHE_MAX_AGE:
                        with open(path, "rb") as f:
                            return _CachedResponse(f.read())
                except OSError:
                    pass

                resp = _session.get(url, *args, **kwargs)
                content = resp.content
                ctype = resp.headers.get("Content-Type", "")
                if resp.status_code == 200 and ctype.startswith("image/") and content:
                    try:
                        os.makedirs(os.path.dirname(path), exist_ok=True)
                        tmp = f"{path}.{threading.get_ident()}.tmp"
                        with open(tmp, "wb") as f:
                            f.write(content)
                        os.replace(tmp, path)
                    except OSError:
                        pass
                return _CachedResponse(content) if resp.status_code == 200 else resp

        _mw.requests = _RequestsWithUA()
        _ol.requests = _RequestsWithUA()
    except Exception:
        pass

ACCENT     = "#00d4ff"
BG_CARD    = "#16213e"
BG_PANEL   = "#1a1a2e"
BG_DARK    = "#0f0f1a"
TEXT_MUTED = "#888888"
TEXT_DIM   = "#444466"
GREEN      = "#00c853"
WHITE      = "#ffffff"
MARKER_COLOR = "#00d4ff"

MAX_LIST_ITEMS = 250   # max widgets in the photo list
MAX_MARKERS    = 300   # max markers on the map
PREVIEW_MAX_W  = 260   # photo preview size in the detail card
PREVIEW_MAX_H  = 140
THUMB_CACHE_SIZE = 60  # thumbnails kept in memory


def _parse_date(raw: str) -> str:
    """Convert EXIF date '2024:01:15 14:30:00' to a human-readable format."""
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
        self._photos: list[dict] = []       # records with GPS
        self._markers: list = []            # tkintermapview markers
        self._selected_idx: int | None = None
        self._list_rows: list[ctk.CTkFrame] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build(self):
        # ── Top bar ───────────────────────────────────────────────────────────
        bar = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        bar.grid_columnconfigure(1, weight=1)

        btn_frame = ctk.CTkFrame(bar, fg_color="transparent")
        btn_frame.grid(row=0, column=0, padx=16, pady=14, sticky="w")

        self._load_btn = ctk.CTkButton(
            btn_frame, text=t("map.load"),
            width=160, height=38,
            fg_color=ACCENT, text_color="#000",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._pick_folder,
        )
        self._load_btn.pack(side="left", padx=(0, 10))

        self._center_btn = ctk.CTkButton(
            btn_frame, text=t("map.center"),
            width=110, height=38,
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

        # ── Main body ─────────────────────────────────────────────────────────
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3, minsize=380)
        body.grid_columnconfigure(1, weight=2)
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
                text=t("map.no_module"),
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
        self._map.set_tile_server(OSM_TILE_URL, max_zoom=19)
        self._map.set_position(50.08, 14.42)   # default: Prague
        self._map.set_zoom(5)

    def _build_list(self, parent):
        list_card = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12)
        list_card.grid(row=0, column=1, sticky="nsew")
        list_card.grid_columnconfigure(0, weight=1)
        list_card.grid_rowconfigure(2, weight=1)

        # List header
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

        # Scrollable list
        self._scroll = ctk.CTkScrollableFrame(
            list_card, fg_color="transparent",
        )
        self._scroll.grid(row=2, column=0, sticky="nsew", padx=6, pady=6)
        self._scroll.grid_columnconfigure(0, weight=1)

        # Selected photo detail
        self._detail_card = ctk.CTkFrame(list_card, fg_color=BG_PANEL, corner_radius=8)
        self._detail_card.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 8))
        self._detail_card.grid_columnconfigure(0, weight=1)

        # Photo preview (hidden until a photo is selected); click → open full image
        self._preview_lbl = ctk.CTkLabel(
            self._detail_card, text="",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
            fg_color=BG_DARK, corner_radius=6,
            height=PREVIEW_MAX_H,
        )
        self._preview_lbl.bind("<Button-1>", lambda e: self._open_full_image())
        self._preview_path: str | None = None
        self._preview_token = 0
        self._thumb_cache: dict[str, "ctk.CTkImage | None"] = {}

        self._detail_lbl = ctk.CTkLabel(
            self._detail_card,
            text=t("map.detail_empty"),
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
            wraplength=260,
            justify="left",
            anchor="w",
        )
        self._detail_lbl.grid(row=1, column=0, padx=12, pady=10, sticky="w")

    # ── Data loading ──────────────────────────────────────────────────────────

    def _pick_folder(self):
        folder = filedialog.askdirectory(
            title=t("map.folder_title"),
            initialdir=os.path.expanduser("~"),
        )
        if folder:
            self.load_dir(folder)

    def load_dir(self, folder: str):
        """Load GPS data from file_analysis.json in the given extraction folder."""
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
        self._count_lbl.configure(text=f"{len(photos)} photos")
        self._rebuild_list()
        self._rebuild_markers()
        self._fit_map()

    def _rebuild_list(self):
        for w in self._scroll.winfo_children():
            w.destroy()
        self._list_rows.clear()

        total = len(self._photos)
        display = self._photos[:MAX_LIST_ITEMS]

        for i, photo in enumerate(display):
            row = ctk.CTkFrame(
                self._scroll, fg_color=BG_CARD, corner_radius=8, cursor="hand2",
            )
            row.grid(row=i, column=0, sticky="ew", padx=2, pady=3)
            row.grid_columnconfigure(1, weight=1)

            # Pin icon
            ctk.CTkLabel(
                row, text="📍",
                font=ctk.CTkFont(size=16),
            ).grid(row=0, column=0, rowspan=2, padx=(10, 6), pady=8)

            # Filename
            ctk.CTkLabel(
                row,
                text=photo.get("filename", "—"),
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=WHITE,
                anchor="w",
            ).grid(row=0, column=1, sticky="w", pady=(7, 0))

            # Date
            date_str = _parse_date(photo.get("date_taken", ""))
            ctk.CTkLabel(
                row,
                text=date_str,
                font=ctk.CTkFont(size=10),
                text_color=TEXT_MUTED,
                anchor="w",
            ).grid(row=1, column=1, sticky="w", pady=(0, 7))

            # GPS coordinates
            lat = photo.get("gps_latitude", 0)
            lon = photo.get("gps_longitude", 0)
            ctk.CTkLabel(
                row,
                text=f"{lat:.5f}, {lon:.5f}",
                font=ctk.CTkFont(size=10),
                text_color=TEXT_DIM,
                anchor="e",
            ).grid(row=0, column=2, rowspan=2, padx=(4, 10))

            # Click → select
            for widget in row.winfo_children():
                widget.bind("<Button-1>", lambda e, idx=i: self._select(idx))
            row.bind("<Button-1>", lambda e, idx=i: self._select(idx))

            self._list_rows.append(row)

        # Info row if there are more photos than the limit
        if total > MAX_LIST_ITEMS:
            info = ctk.CTkLabel(
                self._scroll,
                text=t("map.list_capped").format(shown=MAX_LIST_ITEMS, total=total),
                font=ctk.CTkFont(size=10),
                text_color=TEXT_MUTED,
            )
            info.grid(row=MAX_LIST_ITEMS, column=0, pady=(4, 2), sticky="ew")

    def _rebuild_markers(self):
        if not self._map:
            return
        for m in self._markers:
            m.delete()
        self._markers.clear()

        total   = len(self._photos)
        display = self._photos[:MAX_MARKERS]

        for i, photo in enumerate(display):
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

        if total > MAX_MARKERS:
            self._status_lbl.configure(
                text=t("map.found").format(n=total)
                     + t("map.markers_capped").format(n=MAX_MARKERS),
                text_color=GREEN,
            )

    def _fit_map(self):
        """Fit the map view so that all markers are visible."""
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

    # ── Selection ─────────────────────────────────────────────────────────────

    def _select(self, idx: int):
        # Deselect previous
        if self._selected_idx is not None and self._selected_idx < len(self._list_rows):
            self._list_rows[self._selected_idx].configure(fg_color=BG_CARD)

        self._selected_idx = idx
        if idx < len(self._list_rows):
            self._list_rows[idx].configure(fg_color="#1e2a4a")
            # Scroll into view
            self._list_rows[idx].update_idletasks()

        if idx < len(self._photos):
            photo = self._photos[idx]
            # Centre map on the selected marker
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
        self._show_preview(photo)

    # ── Photo preview ─────────────────────────────────────────────────────────

    def _show_preview(self, photo: dict):
        path = photo.get("_local_path")
        self._preview_path = path
        self._preview_token += 1
        token = self._preview_token

        self._preview_lbl.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 0))

        if not path:
            self._set_preview(None, t("map.preview_missing"))
            return
        if path in self._thumb_cache:
            img = self._thumb_cache[path]
            self._set_preview(img, "" if img else t("map.preview_unavail"))
            return

        self._set_preview(None, t("map.preview_loading"))

        def _worker():
            pil = _make_thumbnail(path)
            self.after(0, lambda: self._preview_ready(token, path, pil))

        threading.Thread(target=_worker, daemon=True).start()

    def _preview_ready(self, token: int, path: str, pil):
        img = None
        if pil is not None:
            img = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
        if len(self._thumb_cache) >= THUMB_CACHE_SIZE:
            self._thumb_cache.pop(next(iter(self._thumb_cache)))
        self._thumb_cache[path] = img
        if token == self._preview_token:      # ignore stale results
            self._set_preview(img, "" if img else t("map.preview_unavail"))

    def _set_preview(self, img, text: str):
        self._preview_lbl.configure(image=img, text=text,
                                    cursor="hand2" if img else "")
        # CTkLabel keeps a reference to the previous image unless cleared
        if img is None:
            try:
                self._preview_lbl._label.configure(image="")
            except Exception:
                pass

    def _open_full_image(self):
        path = self._preview_path
        if not path or not os.path.isfile(path):
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(path)                        # Windows
            else:
                import subprocess, sys
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])
        except Exception:
            pass

    # ── Language refresh ─────────────────────────────────────────────────────

    def refresh_lang(self):
        pass


# ── Helper: parse GPS data from extraction folder ─────────────────────────────

def _load_gps_from_folder(folder: str) -> list[dict]:
    """
    Read GPS records from the extraction folder (Android file_analysis.json and
    iOS ios_photos_gps.json), merge them and sort by capture date.
    """
    photos: list[dict] = []
    photos.extend(_load_android_gps(folder))
    photos.extend(_load_ios_gps(folder))

    # Sort by capture date
    def _sort_key(e):
        raw = e.get("date_taken", "")
        try:
            return datetime.datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
        except Exception:
            return datetime.datetime.min

    photos.sort(key=_sort_key)
    _resolve_local_paths(folder, photos)
    return photos


def _load_android_gps(folder: str) -> list[dict]:
    """Read file_analysis.json (Android EXIF) records with GPS coordinates."""
    json_path = os.path.join(folder, "file_analysis.json")
    if not os.path.isfile(json_path):
        return []
    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []

    files = data.get("files", []) if isinstance(data, dict) else []
    if not files:
        # Older format – flat list
        files = data if isinstance(data, list) else []

    return [
        entry for entry in files
        if "gps_latitude" in entry and "gps_longitude" in entry
    ]


def _load_ios_gps(folder: str) -> list[dict]:
    """Read ios_photos_gps.json and normalize it to the map's record shape."""
    json_path = os.path.join(folder, "ios_photos_gps.json")
    if not os.path.isfile(json_path):
        return []
    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    if not isinstance(data, list):
        return []

    out: list[dict] = []
    for e in data:
        lat = e.get("latitude")
        lon = e.get("longitude")
        if lat is None or lon is None:
            continue
        try:
            lat = float(lat)
            lon = float(lon)
        except Exception:
            continue
        # iOS date "YYYY-MM-DD HH:MM:SS" → EXIF "YYYY:MM:DD HH:MM:SS"
        raw = e.get("date", "") or ""
        date_taken = raw
        if len(raw) >= 10 and raw[4] == "-":
            date_taken = raw[:10].replace("-", ":") + raw[10:]
        out.append({
            "filename":      e.get("filename", ""),
            "date_taken":    date_taken,
            "gps_latitude":  lat,
            "gps_longitude": lon,
            "source":        "iOS",
        })
    return out


def _resolve_local_paths(folder: str, photos: list[dict]) -> None:
    """Find the image file on disk for each record (stored as "_local_path").
    Uses the stored absolute path; if the extraction folder was moved/copied,
    falls back to searching the folder by filename."""
    index: dict[str, str] | None = None
    for p in photos:
        path = p.get("path") or p.get("local_path") or ""
        if path and os.path.isfile(path):
            p["_local_path"] = path
            continue
        if index is None:
            index = {}
            for root, _, files in os.walk(folder):
                for fname in files:
                    index.setdefault(fname.lower(), os.path.join(root, fname))
        found = index.get((p.get("filename") or os.path.basename(path)).lower())
        if found:
            p["_local_path"] = found


def _make_thumbnail(path: str):
    """Return a PIL thumbnail (EXIF-rotated) or None if the image can't be read."""
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            im.draft("RGB", (PREVIEW_MAX_W * 2, PREVIEW_MAX_H * 2))  # fast JPEG decode
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGB")
            im.thumbnail((PREVIEW_MAX_W, PREVIEW_MAX_H))
            return im.copy()
    except Exception:
        return None
