# -*- coding: utf-8 -*-
"""
ResultsPanel – two-column panel with extraction results.
Left: inline data preview (key/value or table).
Right: list of module cards to choose from.
"""
import os
import shutil
import threading
from tkinter import ttk, filedialog
from typing import Callable, Optional

import customtkinter as ctk

try:
    from PIL import Image
    _PIL_OK = True
except Exception:
    _PIL_OK = False

from core import case_tags as ct
from core.i18n import t, field_label
from . import theme as th
from .tagging import popup_tag_menu
from .iconset import module_icon

# Media file types recognised by the thumbnail gallery
_IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".heif"}
_VID_EXT = {".mp4", ".3gp", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".3g2"}
_GALLERY_MAX = 500          # cap widgets created at once
_THUMB_PX = 108

ACCENT     = "#00d4ff"
BG_CARD    = "#16213e"
BG_LOG     = "#0a0f1e"
BG_PANEL   = "#1a1a2e"
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
    "file_analysis":  "🔍",
    "file_hashes":    "#️",
    "deleted_data":   "🗑",
    "adb_backup":     "💾",
    "root_backup":    "🔓",
    "app_providers":  "🔗",
    "usage_stats":    "📊",
    "termux_agent":   "🤖",
}


def _label(key: str) -> str:
    return field_label(key)


def _ellipsize(name: str, maxlen: int = 22) -> str:
    """Shorten a filename for display, keeping the extension."""
    if len(name) <= maxlen:
        return name
    stem, ext = os.path.splitext(name)
    keep = max(4, maxlen - len(ext) - 1)
    return stem[:keep] + "…" + ext


class ResultsPanel(ctk.CTkFrame):
    def __init__(self, master,
                 accent: str = ACCENT,
                 on_open_output: Callable = None,
                 on_html: Callable = None,
                 on_pdf: Callable = None,
                 on_excel: Callable = None,
                 on_evidence: Callable = None,
                 on_verify: Callable = None,
                 **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.accent = accent
        self._on_open_output = on_open_output
        self._on_html = on_html
        self._on_pdf = on_pdf
        self._on_excel = on_excel
        self._on_evidence = on_evidence
        self._on_verify = on_verify

        self._results: list = []
        self._selected: Optional[object] = None
        self._selected_card: Optional[ctk.CTkFrame] = None

        # Called with a message after tags were changed here (app refreshes Search)
        self.on_tags_changed: Optional[Callable] = None

        # Table state
        self._all_rows: list[dict] = []
        self._tag_src: Optional[tuple] = None      # (output_dir, file name) when taggable
        self._row_tags: dict = {}                  # row index → [tag names]
        self._tag_rows_cache: Optional[list] = None
        self._columns: list[str] = []
        self._tree: Optional[ttk.Treeview] = None
        self._gallery_token = None
        self._thumb_refs: list = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build(self):
        # ── Toolbar: output + reports on the left, evidence manifest on the right ─────────
        tb = th.Toolbar(self, source=False)
        tb.grid(row=0, column=0, sticky="ew", pady=(0, th.GAP))

        def _cb(fn):
            return lambda: fn and fn()

        self._btn_open = th.secondary_button(tb.info, t("topbar.open_output"),
                                             _cb(self._on_open_output), width=140)
        self._report_lbl = th.field_label(tb.info, t("res.reports"))
        self._btn_html = th.secondary_button(tb.info, "HTML", _cb(self._on_html), width=80)
        self._btn_pdf = th.secondary_button(tb.info, "PDF", _cb(self._on_pdf), width=80)
        self._btn_excel = th.secondary_button(tb.info, "Excel", _cb(self._on_excel), width=80)
        self._btn_open.pack(side="left")
        self._report_lbl.pack(side="left", padx=(24, 8))
        th.pack_row(self._btn_html, self._btn_pdf, self._btn_excel)
        # pack_row starts without a gap – the first button follows the label
        self._btn_html.pack_configure(padx=(0, 0))

        self._btn_evidence = th.secondary_button(tb.actions, t("ev.generate_btn"),
                                                 _cb(self._on_evidence), width=160)
        self._btn_verify = th.secondary_button(tb.actions, t("ev.verify_btn"),
                                               _cb(self._on_verify), width=140)
        th.pack_row(self._btn_evidence, self._btn_verify)

        # ── Body: detail on the left, cards on the right ─────────────────────────────────
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3, minsize=380)
        body.grid_columnconfigure(1, weight=2, minsize=340)
        body.grid_rowconfigure(0, weight=1)

        self._build_detail(body)
        self._build_cards(body)

    def _build_detail(self, parent):
        """Left panel – inline preview of a result."""
        self._detail_card = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=th.RADIUS_CARD)
        self._detail_card.grid(row=0, column=0, sticky="nsew", padx=(0, th.GAP))
        self._detail_card.grid_propagate(False)
        self._detail_card.grid_columnconfigure(0, weight=1)
        self._detail_card.grid_rowconfigure(2, weight=1)

        # Header row (name, count, search, open JSON)
        hdr = ctk.CTkFrame(self._detail_card, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=th.PAD, pady=(th.PAD - 4, 8))
        hdr.grid_columnconfigure(1, weight=1)

        self._detail_title = th.section_label(hdr, t("res.preview"))
        self._detail_title.grid(row=0, column=0, sticky="w")

        self._detail_count = ctk.CTkLabel(hdr, text="", font=th.f_small(),
                                          text_color=TEXT_MUTED)
        self._detail_count.grid(row=0, column=1, sticky="w", padx=(12, 0))

        right_hdr = ctk.CTkFrame(hdr, fg_color="transparent", width=1, height=1)
        right_hdr.grid(row=0, column=2, sticky="e")
        self._right_hdr = right_hdr

        self._search_var = ctk.StringVar()
        self._search_entry = ctk.CTkEntry(
            right_hdr, textvariable=self._search_var,
            placeholder_text=t("res.search"),
            width=180, height=th.BTN_H_SMALL, font=th.f_body(),
        )
        self._search_entry.pack(side="left", padx=(0, 6))
        self._search_var.trace_add("write", lambda *_: self._filter())

        self._btn_tag = th.small_button(right_hdr, t("res.tag_btn"), self._tag_button,
                                        width=90, state="disabled")
        self._btn_tag.pack(side="left", padx=(0, 6))

        self._btn_json = th.small_button(right_hdr, t("res.open_json"), self._open_json,
                                         width=100, state="disabled")
        self._btn_json.pack(side="left")

        th.separator(self._detail_card).grid(row=1, column=0, sticky="ew", padx=th.PAD)

        # Body – content is rendered here dynamically
        self._detail_body = ctk.CTkFrame(
            self._detail_card, fg_color="transparent",
        )
        self._detail_body.grid(row=2, column=0, sticky="nsew", padx=6, pady=6)
        self._detail_body.grid_columnconfigure(0, weight=1)
        self._detail_body.grid_rowconfigure(0, weight=1)

        self._show_placeholder()

    def _build_cards(self, parent):
        """Right panel – list of module cards."""
        right = ctk.CTkFrame(parent, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        self._cards_title = th.section_label(right, t("ui.modules"))
        self._cards_title.grid(row=0, column=0, sticky="w", padx=(4, 0), pady=(4, 10))

        self._cards_scroll = ctk.CTkScrollableFrame(right, fg_color="transparent")
        self._cards_scroll.grid(row=1, column=0, sticky="nsew")
        self._cards_scroll.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self._cards_scroll,
            text=t("res.no_extraction_yet"),
            text_color=TEXT_MUTED, font=th.f_body(),
        ).grid(row=0, column=0, pady=40)

    # ── Updating results ──────────────────────────────────────────────────────

    # Modules that are extracted/used internally but not shown as result cards
    _HIDDEN_MODULES = {"user_data_tree"}

    def update_results(self, results: list):
        results = [r for r in results if getattr(r, "module", "") not in self._HIDDEN_MODULES]
        self._results = results
        self._selected = None
        self._selected_card = None
        self._tree = None

        # Clear the cards
        for w in self._cards_scroll.winfo_children():
            w.destroy()

        if not results:
            ctk.CTkLabel(
                self._cards_scroll,
                text=t("misc.no_results"),
                text_color=TEXT_MUTED, font=ctk.CTkFont(size=12),
            ).grid(row=0, column=0, pady=40)
            self._show_placeholder()
            return

        for i, r in enumerate(results):
            self._build_card(r, i)

        # Automatically select the first successful result
        first_ok = next((r for r in results if r.success and r.data is not None), None)
        if first_ok:
            self._show_result(first_ok)

    def _build_card(self, result, row: int):
        is_ok = result.success and result.data is not None

        card = ctk.CTkFrame(
            self._cards_scroll,
            fg_color=BG_CARD, corner_radius=th.RADIUS_ITEM,
            cursor="hand2" if is_ok else "",
        )
        card.grid(row=row, column=0, sticky="ew", padx=4, pady=3)
        card.grid_columnconfigure(1, weight=1)

        # Branded PNG icon (same as in the other modules), with an emoji fallback
        img = module_icon(result.module, size=30)
        if img is not None:
            icon_lbl = ctk.CTkLabel(card, text="", image=img, width=36)
        else:
            icon_lbl = ctk.CTkLabel(card, text=MODULE_ICONS.get(result.module, "📁"),
                                    font=ctk.CTkFont(size=20), width=36)
        icon_lbl.grid(row=0, column=0, rowspan=2, padx=(12, 8), pady=10)

        ctk.CTkLabel(
            card, text=result.label,
            font=th.f_bold(), anchor="w",
        ).grid(row=0, column=1, sticky="w", pady=(10, 1), padx=(0, 8))

        if is_ok:
            info = f"{result.count} {t('res.records')}"
            info_color = TEXT_MUTED
        else:
            info = result.error[:60] + ("…" if len(result.error) > 60 else "")
            info_color = RED

        ctk.CTkLabel(
            card, text=info,
            font=ctk.CTkFont(size=11), text_color=info_color,
            anchor="w", wraplength=220,
        ).grid(row=1, column=1, sticky="w", pady=(0, 10), padx=(0, 8))

        # Status dot on the right
        ctk.CTkLabel(
            card, text="●",
            font=ctk.CTkFont(size=10),
            text_color=GREEN if is_ok else RED, width=20,
        ).grid(row=0, column=2, padx=(0, 14), pady=(10, 0), sticky="ne")

        if is_ok:
            def _click(r=result, c=card):
                self._show_result(r)
                self._highlight_card(c)
            card.bind("<Button-1>", lambda e, fn=_click: fn())
            for child in card.winfo_children():
                child.bind("<Button-1>", lambda e, fn=_click: fn())

    def _highlight_card(self, card: ctk.CTkFrame):
        if self._selected_card and self._selected_card.winfo_exists():
            self._selected_card.configure(fg_color=BG_CARD)
        card.configure(fg_color="#1e2d50")
        self._selected_card = card

    # ── Rendering the detail ──────────────────────────────────────────────────

    def _show_result(self, result):
        self._selected = result
        self._all_rows = []
        self._columns = []
        self._tree = None
        self._tag_src = None
        self._row_tags = {}
        self._tag_rows_cache = None
        self._btn_tag.configure(state="disabled")

        # Header
        self._detail_title.configure(text=result.label)
        self._right_hdr.grid()
        if result.success:
            self._detail_count.configure(
                text=f"{result.count} {t('res.records')}", text_color=TEXT_MUTED
            )
        else:
            self._detail_count.configure(text=result.error[:80], text_color=RED)

        self._btn_json.configure(
            state="normal" if result.file_path and os.path.exists(result.file_path)
            else "disabled"
        )
        self._search_var.set("")

        # Clear the body
        for w in self._detail_body.winfo_children():
            w.destroy()

        data = result.data
        if data is None or (isinstance(data, (list, dict)) and not data):
            self._show_placeholder(t("res.no_data"))
            self._search_entry.configure(state="disabled")
            return

        self._search_entry.configure(state="normal")

        # Media module → thumbnail gallery (falls back to KV if no local files)
        if getattr(result, "module", "") == "media" and result.file_path \
                and os.path.isdir(result.file_path):
            if self._render_media_gallery(result.file_path):
                self._search_entry.configure(state="disabled")
                return

        if isinstance(data, dict):
            self._search_entry.configure(state="disabled")
            self._render_kv(data)
        elif isinstance(data, list):
            first = data[0] if data else None
            if isinstance(first, dict):
                self._all_rows = data
                fp = result.file_path or ""
                if fp.lower().endswith(".json") and os.path.isfile(fp):
                    self._tag_src = (os.path.dirname(os.path.abspath(fp)), os.path.basename(fp))
                    self._btn_tag.configure(state="normal")
                self._render_table(data)
            else:
                self._all_rows = [{t("res.value_col"): str(v)} for v in data]
                self._render_table(self._all_rows)

    def _render_media_gallery(self, folder: str) -> bool:
        """Render a thumbnail + filename grid for the media folder.
        Returns False (so the caller falls back to KV) if there are no media files."""
        try:
            names = sorted(
                f for f in os.listdir(folder)
                if os.path.splitext(f)[1].lower() in (_IMG_EXT | _VID_EXT)
                and os.path.isfile(os.path.join(folder, f))
            )
        except OSError:
            return False
        if not names:
            return False

        truncated = len(names) > _GALLERY_MAX
        shown_names = names[:_GALLERY_MAX]

        # Container: toolbar on top, thumbnail grid below
        container = ctk.CTkFrame(self._detail_body, fg_color="transparent")
        container.grid(row=0, column=0, sticky="nsew")
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(container, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(0, 6))
        bar.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(
            bar, text=t("res.export_media"), height=30, width=170,
            fg_color=ACCENT, text_color="#000",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=lambda f=folder: self._export_media_originals(f),
        ).grid(row=0, column=0, sticky="w")

        self._gallery_status = ctk.CTkLabel(
            bar, text=t("res.gallery_hint").format(total=len(names)),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED, anchor="w",
        )
        self._gallery_status.grid(row=0, column=1, sticky="w", padx=(12, 0))

        scroll = ctk.CTkScrollableFrame(container, fg_color="transparent")
        scroll.grid(row=1, column=0, sticky="nsew")
        names = shown_names
        cols = 4
        for c in range(cols):
            scroll.grid_columnconfigure(c, weight=1)

        token = object()
        self._gallery_token = token
        self._thumb_refs = []          # keep CTkImage refs alive
        to_load: list[tuple[str, ctk.CTkLabel]] = []

        for i, name in enumerate(names):
            full = os.path.join(folder, name)
            is_video = os.path.splitext(name)[1].lower() in _VID_EXT

            card = ctk.CTkFrame(scroll, fg_color=BG_CARD, corner_radius=8,
                                width=150, height=150)
            card.grid(row=i // cols, column=i % cols, padx=6, pady=6, sticky="nsew")
            card.grid_propagate(False)

            thumb = ctk.CTkLabel(
                card, text="🎞" if is_video else "🖼",
                font=ctk.CTkFont(size=34), width=_THUMB_PX, height=_THUMB_PX,
                text_color=TEXT_MUTED,
            )
            thumb.pack(pady=(8, 2))

            lbl = ctk.CTkLabel(
                card, text=_ellipsize(name, 22),
                font=ctk.CTkFont(size=10), width=140, wraplength=140,
            )
            lbl.pack(pady=(0, 6))

            for w in (card, thumb, lbl):
                w.configure(cursor="hand2")
                w.bind("<Button-1>", lambda e, p=full: self._open_path(p))

            if not is_video and _PIL_OK:
                to_load.append((full, thumb))

        if truncated:
            note = ctk.CTkLabel(
                scroll, text=t("res.gallery_truncated").format(n=_GALLERY_MAX),
                font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
            )
            note.grid(row=(len(names) // cols) + 1, column=0, columnspan=cols, pady=8)

        if to_load:
            threading.Thread(
                target=self._load_thumbnails, args=(to_load, token), daemon=True
            ).start()
        return True

    def _load_thumbnails(self, items: list, token) -> None:
        """Decode and downscale image thumbnails off the UI thread."""
        for path, label in items:
            if self._gallery_token is not token:
                return   # user switched module
            try:
                img = Image.open(path)
                img.draft("RGB", (_THUMB_PX, _THUMB_PX))
                img.thumbnail((_THUMB_PX, _THUMB_PX))
                img = img.convert("RGB")
            except Exception:
                continue
            self.after(0, lambda lb=label, im=img: self._apply_thumbnail(lb, im, token))

    def _apply_thumbnail(self, label: ctk.CTkLabel, pil_img, token) -> None:
        if self._gallery_token is not token:
            return
        try:
            if not label.winfo_exists():
                return
            ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img,
                                   size=pil_img.size)
            self._thumb_refs.append(ctk_img)
            label.configure(image=ctk_img, text="")
        except Exception:
            pass

    def _open_path(self, path: str):
        if os.path.exists(path):
            os.startfile(path)

    def _export_media_originals(self, folder: str):
        """Copy the original, full-resolution media files to a chosen folder."""
        try:
            files = [f for f in os.listdir(folder)
                     if os.path.isfile(os.path.join(folder, f))]
        except OSError:
            files = []

        # Nothing on disk to export → tell the user plainly instead of a silent no-op
        if not files:
            self._set_gallery_status(
                t("res.export_empty").format(folder=folder), "#f44336")
            return

        dest = filedialog.askdirectory(title=t("res.export_media"))
        if not dest:
            return
        self._set_gallery_status(t("res.export_running").format(n=len(files)), ACCENT)

        def _worker():
            copied = 0
            failed = 0
            for name in files:
                src = os.path.join(folder, name)
                try:
                    target = os.path.join(dest, name)
                    if os.path.exists(target) and os.path.abspath(src) != os.path.abspath(target):
                        stem, ext = os.path.splitext(name)
                        target = os.path.join(dest, f"{stem}_{copied}{ext}")
                    shutil.copy2(src, target)   # preserves bytes + timestamps
                    copied += 1
                except OSError:
                    failed += 1
            self.after(0, lambda c=copied, fl=failed: self._on_media_exported(c, fl, dest))

        threading.Thread(target=_worker, daemon=True).start()

    def _set_gallery_status(self, text: str, color: str):
        if hasattr(self, "_gallery_status") and self._gallery_status.winfo_exists():
            self._gallery_status.configure(text=text, text_color=color)

    def _on_media_exported(self, copied: int, failed: int, dest: str):
        msg = t("res.export_done").format(n=copied)
        if failed:
            msg += "  " + t("res.export_failed").format(n=failed)
        self._set_gallery_status(msg, "#4CAF50" if copied else "#f44336")
        if copied and os.path.isdir(dest):
            os.startfile(dest)

    def _show_placeholder(self, text: str = ""):
        for w in self._detail_body.winfo_children():
            w.destroy()
        if text:
            ctk.CTkLabel(
                self._detail_body,
                text=text,
                text_color=TEXT_MUTED,
                font=ctk.CTkFont(size=13),
            ).grid(row=0, column=0, pady=60)
        self._search_entry.configure(state="disabled")
        self._btn_json.configure(state="disabled")
        self._detail_title.configure(text=t("res.preview"))
        self._detail_count.configure(text="")
        self._right_hdr.grid_remove()

    def _render_kv(self, data: dict):
        scroll = ctk.CTkScrollableFrame(self._detail_body, fg_color="transparent")
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(1, weight=1)

        for i, (k, v) in enumerate(data.items()):
            bg = BG_CARD if i % 2 == 0 else "#1c2244"
            row_f = ctk.CTkFrame(scroll, fg_color=bg, corner_radius=6)
            row_f.grid(row=i, column=0, columnspan=2, sticky="ew", pady=2, padx=2)
            row_f.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(
                row_f, text=_label(k),
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=ACCENT, width=200, anchor="w",
            ).grid(row=0, column=0, padx=(15, 10), pady=8, sticky="w")

            ctk.CTkLabel(
                row_f, text=str(v),
                font=ctk.CTkFont(size=12), anchor="w", wraplength=480,
            ).grid(row=0, column=1, padx=(0, 15), pady=8, sticky="ew")

    def _render_table(self, data: list[dict]):
        self._columns = list(dict.fromkeys(k for row in data for k in row.keys()))
        col_labels = [_label(c) for c in self._columns]

        # Match the CustomTkinter look used across the app (same font family,
        # dark surfaces, and CTk scrollbars instead of native ttk ones).
        fam = ctk.CTkFont().cget("family")

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Res.Treeview",
            background=BG_CARD, foreground="#e0e0e0",
            fieldbackground=BG_CARD, rowheight=28,
            borderwidth=0, font=(fam, 12),
        )
        style.configure(
            "Res.Treeview.Heading",
            background="#0d1b3e", foreground=ACCENT,
            font=(fam, 12, "bold"), relief="flat", borderwidth=0,
        )
        style.map("Res.Treeview",
                  background=[("selected", "#1e4080")],
                  foreground=[("selected", "#ffffff")])
        style.map("Res.Treeview.Heading",
                  background=[("active", "#13264f")])

        wrapper = ctk.CTkFrame(self._detail_body, fg_color=BG_CARD, corner_radius=8)
        wrapper.grid(row=0, column=0, sticky="nsew")
        wrapper.grid_columnconfigure(0, weight=1)
        wrapper.grid_rowconfigure(0, weight=1)

        self._tree = ttk.Treeview(
            wrapper, columns=self._columns, show="headings",
            style="Res.Treeview", selectmode="extended",
        )
        vsb = ctk.CTkScrollbar(wrapper, orientation="vertical", command=self._tree.yview)
        hsb = ctk.CTkScrollbar(wrapper, orientation="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=(8, 0))
        vsb.grid(row=0, column=1, sticky="ns", padx=(2, 6), pady=8)
        hsb.grid(row=1, column=0, sticky="ew", padx=(8, 0), pady=(2, 6))

        for col, lbl in zip(self._columns, col_labels):
            self._tree.heading(col, text=lbl, command=lambda c=col: self._sort(c))
            self._tree.column(col, width=100, minwidth=40, stretch=True)

        self._tree.tag_configure("odd",  background="#16213e")
        self._tree.tag_configure("even", background="#1c2850")
        self._tree.tag_configure("tagged", foreground="#ffcc80")
        self._tree.bind("<Double-1>", self._on_row_double_click)
        if self._tag_src:
            self._tree.bind("<Button-3>", self._on_right_click)
            self._load_row_tags()

        self._populate(list(enumerate(data)))

    def _populate(self, rows: list[tuple[int, dict]]):
        """rows = [(index in self._all_rows, row)]; the index is used as the item id."""
        if not self._tree:
            return
        for item in self._tree.get_children():
            self._tree.delete(item)
        for i, (idx, row) in enumerate(rows):
            vals = [str(row.get(c, "")) for c in self._columns]
            self._tree.insert("", "end", iid=str(idx), values=vals, tags=self._row_style(i, idx))

    def _row_style(self, pos: int, idx: int) -> tuple:
        base = "even" if pos % 2 == 0 else "odd"
        return (base, "tagged") if self._row_tags.get(idx) else (base,)

    # ── Tagging ───────────────────────────────────────────────────────────────

    def _tag_rows(self) -> list[dict]:
        """Taggable rows for all records of the shown result (index-aligned with _all_rows).

        Records are taken from the JSON file itself when it matches the shown data, so the
        row key is the same one the search index computes."""
        if self._tag_rows_cache is None:
            out_dir, fname = self._tag_src
            recs = ct.file_records(out_dir, fname)
            if len(recs) == len(self._all_rows) and all(s == "" for s, _i, _r in recs):
                self._tag_rows_cache = [ct.make_row(fname, r, s, i) for s, i, r in recs]
            else:
                self._tag_rows_cache = [ct.make_row(fname, r, "", i)
                                        for i, r in enumerate(self._all_rows)]
        return self._tag_rows_cache

    def _load_row_tags(self):
        try:
            rows = self._tag_rows()
            with ct.CaseTags(self._tag_src[0]) as tg:
                by_key = tg.tags_for(r["row_key"] for r in rows)
        except Exception:
            self._row_tags = {}
            return
        self._row_tags = {i: by_key[r["row_key"]] for i, r in enumerate(rows)
                          if r["row_key"] in by_key}
        n = len(self._row_tags)
        if self._selected is not None and self._selected.success:
            text = f"{self._selected.count} {t('res.records')}"
            if n:
                text += "  ·  " + t("res.tagged_count").format(n=n)
            self._detail_count.configure(text=text)

    def _selected_tag_rows(self) -> list[dict]:
        if not self._tree or not self._tag_src:
            return []
        rows = self._tag_rows()
        out = []
        for iid in self._tree.selection():
            try:
                out.append(rows[int(iid)])
            except (ValueError, IndexError):
                pass
        return out

    def _popup_tags(self, x: int, y: int):
        rows = self._selected_tag_rows()
        if not rows:
            self._detail_count.configure(text=t("tag.select_rows"), text_color=ORANGE)
            return
        popup_tag_menu(self, self._tag_src[0], rows, x, y, on_change=self._after_tagging)

    def _tag_button(self):
        b = self._btn_tag
        self._popup_tags(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    def _on_right_click(self, event):
        iid = self._tree.identify_row(event.y)
        if iid and iid not in self._tree.selection():
            self._tree.selection_set(iid)
        self._popup_tags(event.x_root, event.y_root)

    def _after_tagging(self, msg: str):
        self._load_row_tags()
        if self._tree:
            for pos, iid in enumerate(self._tree.get_children("")):
                self._tree.item(iid, tags=self._row_style(pos, int(iid)))
        self._detail_count.configure(text_color=TEXT_MUTED)
        if self.on_tags_changed:
            self.on_tags_changed(msg)

    # ── Filtering and sorting ─────────────────────────────────────────────────

    def _filter(self):
        if not self._tree or not self._all_rows:
            return
        q = self._search_var.get().lower().strip()
        filtered = [(i, r) for i, r in enumerate(self._all_rows)
                    if not q or any(q in str(v).lower() for v in r.values())]
        self._populate(filtered)

    def _sort(self, col: str):
        if not self._tree:
            return
        rows = [(self._tree.set(child, col), child)
                for child in self._tree.get_children("")]
        rows.sort(key=lambda x: x[0].lower())
        for i, (_, child) in enumerate(rows):
            self._tree.move(child, "", i)
            self._tree.item(child, tags=self._row_style(i, int(child)))

    def _on_row_double_click(self, event):
        if not self._tree:
            return
        sel = self._tree.selection()
        if not sel:
            return
        iid = self._tree.identify_row(event.y) or sel[0]
        vals = self._tree.item(iid, "values")
        row = dict(zip(self._columns, vals))
        _RowDetailDialog(self, row, self._selected.label if self._selected else "")

    def _open_json(self):
        if self._selected and self._selected.file_path:
            os.startfile(self._selected.file_path)

    # ── Language refresh ──────────────────────────────────────────────────────

    def refresh_lang(self):
        self._btn_open.configure(text=t("topbar.open_output"))
        self._report_lbl.configure(text=t("res.reports"))
        self._btn_evidence.configure(text=t("ev.generate_btn"))
        self._btn_verify.configure(text=t("ev.verify_btn"))
        self._search_entry.configure(placeholder_text=t("res.search"))
        self._btn_json.configure(text=t("res.open_json"))
        self._btn_tag.configure(text=t("res.tag_btn"))
        self._cards_title.configure(text=t("ui.modules"))


class _RowDetailDialog(ctk.CTkToplevel):
    """Small window with the detail of one record (double-click in the table)."""

    def __init__(self, master, row: dict, module_label: str):
        super().__init__(master)
        self.title(f"{t('res.record_title')} – {module_label}")
        self.geometry("520x460")
        self.minsize(400, 300)
        self.configure(fg_color="#0f0f1a")
        self.grab_set()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.grid(row=0, column=0, sticky="nsew", padx=15, pady=15)
        scroll.grid_columnconfigure(1, weight=1)

        for i, (k, v) in enumerate(row.items()):
            bg = BG_CARD if i % 2 == 0 else "#1c2244"
            f = ctk.CTkFrame(scroll, fg_color=bg, corner_radius=6)
            f.grid(row=i, column=0, columnspan=2, sticky="ew", pady=2)
            f.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(
                f, text=_label(k),
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=ACCENT, width=160, anchor="w",
            ).grid(row=0, column=0, padx=(12, 8), pady=7, sticky="w")

            val_str = str(v)
            if k == "gps_maps_url" and val_str.startswith("http"):
                link = ctk.CTkLabel(
                    f, text=t("res.open_maps"), text_color=ACCENT,
                    font=ctk.CTkFont(size=12, underline=True),
                    cursor="hand2", anchor="w",
                )
                link.grid(row=0, column=1, padx=(0, 12), pady=7, sticky="w")
                link.bind("<Button-1>", lambda e, u=val_str: os.startfile(u))
            else:
                ctk.CTkLabel(
                    f, text=val_str, font=ctk.CTkFont(size=12),
                    anchor="w", wraplength=300, justify="left",
                ).grid(row=0, column=1, padx=(0, 12), pady=7, sticky="ew")

        ctk.CTkButton(
            self, text=t("res.close"), width=100, height=32,
            fg_color=ACCENT, text_color="#000",
            command=self.destroy,
        ).grid(row=1, column=0, pady=(0, 15))
