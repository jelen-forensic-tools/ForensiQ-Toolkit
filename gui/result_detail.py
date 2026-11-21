# -*- coding: utf-8 -*-
import customtkinter as ctk
from tkinter import ttk
import os
import json
from core.i18n import t, field_label

ACCENT     = "#00d4ff"
BG_DARK    = "#0f0f1a"
BG_CARD    = "#16213e"
BG_PANEL   = "#1a1a2e"
TEXT_MUTED = "#888888"

def _label(key: str) -> str:
    return field_label(key)


class ResultDetailDialog(ctk.CTkToplevel):
    def __init__(self, master, result):
        super().__init__(master)
        self.result = result
        self.title(f"Detail – {result.label}")  # "Detail" is same in both languages
        self.geometry("960x620")
        self.minsize(700, 420)
        self.configure(fg_color=BG_DARK)
        self.grab_set()

        self._all_rows: list[dict] = []
        self._columns: list[str] = []

        self._build()

    # ── UI construction ──────────────────────────────────────────────────────

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=10)
        hdr.grid(row=0, column=0, sticky="ew", padx=15, pady=(15, 8))
        hdr.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            hdr, text=self.result.label,
            font=ctk.CTkFont(size=15, weight="bold")
        ).grid(row=0, column=0, padx=20, pady=12, sticky="w")

        if self.result.success:
            ctk.CTkLabel(
                hdr, text=f"{self.result.count} {t('res.records')}",
                text_color=TEXT_MUTED, font=ctk.CTkFont(size=12)
            ).grid(row=0, column=1, padx=5, sticky="w")
        else:
            ctk.CTkLabel(
                hdr, text=f"{t('res.error_prefix')}{self.result.error}",
                text_color="#f44336", font=ctk.CTkFont(size=12)
            ).grid(row=0, column=1, padx=5, sticky="w")

        btn_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        btn_frame.grid(row=0, column=2, padx=15, pady=8)

        # Search field (table results only)
        self._search_var = ctk.StringVar()
        self._search_entry = ctk.CTkEntry(
            btn_frame, textvariable=self._search_var,
            placeholder_text=t("res.search"), width=180, height=30
        )
        self._search_var.trace_add("write", lambda *_: self._filter())
        self._search_entry.pack(side="left", padx=(0, 8))

        if self.result.file_path and os.path.exists(self.result.file_path):
            ctk.CTkButton(
                btn_frame, text=t("res.open_json"),
                width=100, height=30,
                fg_color="#2a2a4a", hover_color="#3a3a5a",
                font=ctk.CTkFont(size=12),
                command=lambda: os.startfile(self.result.file_path)
            ).pack(side="left")

        # Obsah
        self._body = ctk.CTkFrame(self, fg_color="transparent")
        self._body.grid(row=1, column=0, sticky="nsew", padx=15, pady=(0, 15))
        self._body.grid_columnconfigure(0, weight=1)
        self._body.grid_rowconfigure(0, weight=1)

        self._render_data()

    # ── Data rendering ───────────────────────────────────────────────────────

    def _render_data(self):
        data = self.result.data

        if data is None or (isinstance(data, (list, dict)) and not data):
            ctk.CTkLabel(
                self._body, text=t("res.no_data"),
                text_color=TEXT_MUTED, font=ctk.CTkFont(size=13),
                wraplength=500, justify="center"
            ).grid(row=0, column=0, pady=60)
            self._search_entry.configure(state="disabled")
            return

        if isinstance(data, dict):
            self._search_entry.configure(state="disabled")
            self._render_kv(data)
        elif isinstance(data, list):
            first = data[0]
            if isinstance(first, dict):
                self._all_rows = data
                self._render_table(data)
            else:
                # list of strings
                self._all_rows = [{t("res.value_col"): str(v)} for v in data]
                self._render_table(self._all_rows)

    # ---- key-value (device_info) --------------------------------------------

    def _render_kv(self, data: dict):
        scroll = ctk.CTkScrollableFrame(self._body, fg_color="transparent")
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
                text_color=ACCENT, width=200, anchor="w"
            ).grid(row=0, column=0, padx=(15, 10), pady=8, sticky="w")

            ctk.CTkLabel(
                row_f, text=str(v),
                font=ctk.CTkFont(size=12), anchor="w", wraplength=550
            ).grid(row=0, column=1, padx=(0, 15), pady=8, sticky="ew")

    # ---- table (ttk.Treeview) -----------------------------------------------

    def _render_table(self, data: list[dict]):
        self._columns = list(dict.fromkeys(k for row in data for k in row.keys()))
        col_labels = [_label(c) for c in self._columns]

        # Dark style for Treeview – match the CustomTkinter look (font + surfaces)
        fam = ctk.CTkFont().cget("family")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Dark.Treeview",
            background=BG_CARD,
            foreground="#e0e0e0",
            fieldbackground=BG_CARD,
            rowheight=28,
            borderwidth=0,
            font=(fam, 12),
        )
        style.configure(
            "Dark.Treeview.Heading",
            background="#0d1b3e",
            foreground=ACCENT,
            font=(fam, 12, "bold"),
            relief="flat",
            borderwidth=0,
        )
        style.map("Dark.Treeview",
                  background=[("selected", "#1e4080")],
                  foreground=[("selected", "#ffffff")])
        style.map("Dark.Treeview.Heading",
                  background=[("active", "#13264f")])

        # Wrapper with CTk scrollbars (consistent with the rest of the app)
        wrapper = ctk.CTkFrame(self._body, fg_color=BG_CARD, corner_radius=8)
        wrapper.grid(row=0, column=0, sticky="nsew")
        wrapper.grid_columnconfigure(0, weight=1)
        wrapper.grid_rowconfigure(0, weight=1)

        self._tree = ttk.Treeview(
            wrapper, columns=self._columns, show="headings",
            style="Dark.Treeview", selectmode="browse"
        )
        vsb = ctk.CTkScrollbar(wrapper, orientation="vertical", command=self._tree.yview)
        hsb = ctk.CTkScrollbar(wrapper, orientation="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=(8, 0))
        vsb.grid(row=0, column=1, sticky="ns", padx=(2, 6), pady=8)
        hsb.grid(row=1, column=0, sticky="ew", padx=(8, 0), pady=(2, 6))

        # Column headers
        for col, lbl in zip(self._columns, col_labels):
            self._tree.heading(col, text=lbl,
                               command=lambda c=col: self._sort(c))
            self._tree.column(col, width=150, minwidth=60, stretch=True)

        # Alternating row colours
        self._tree.tag_configure("odd",  background="#16213e")
        self._tree.tag_configure("even", background="#1c2850")

        # Detail on double-click
        self._tree.bind("<Double-1>", self._on_row_double_click)

        self._populate(data)

    def _populate(self, rows: list[dict]):
        for item in self._tree.get_children():
            self._tree.delete(item)

        for i, row in enumerate(rows):
            vals = [str(row.get(c, "")) for c in self._columns]
            tag = "even" if i % 2 == 0 else "odd"
            self._tree.insert("", "end", values=vals, tags=(tag,))

    # ── Filtering ────────────────────────────────────────────────────────────

    def _filter(self):
        q = self._search_var.get().lower().strip()
        if not q:
            self._populate(self._all_rows)
        else:
            filtered = [r for r in self._all_rows
                        if any(q in str(v).lower() for v in r.values())]
            self._populate(filtered)

    # ── Column sorting ───────────────────────────────────────────────────────

    def _sort(self, col: str):
        col_idx = self._columns.index(col)
        rows = [(self._tree.set(child, col), child)
                for child in self._tree.get_children("")]
        rows.sort(key=lambda x: x[0].lower())
        for i, (_, child) in enumerate(rows):
            self._tree.move(child, "", i)
            tag = "even" if i % 2 == 0 else "odd"
            self._tree.item(child, tags=(tag,))

    # ── Row detail (double-click) ─────────────────────────────────────────────

    def _on_row_double_click(self, event):
        sel = self._tree.selection()
        if not sel:
            return
        vals = self._tree.item(sel[0], "values")
        row = dict(zip(self._columns, vals))
        _RowDetailDialog(self, row, self.result.label)


class _RowDetailDialog(ctk.CTkToplevel):
    """Small window showing the detail of a single record."""

    def __init__(self, master, row: dict, module_label: str):
        super().__init__(master)
        self.title(f"{t('res.record_title')} – {module_label}")
        self.geometry("520x460")
        self.minsize(400, 300)
        self.configure(fg_color=BG_DARK)
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
                text_color=ACCENT, width=160, anchor="w"
            ).grid(row=0, column=0, padx=(12, 8), pady=7, sticky="w")

            val_str = str(v)
            # GPS link
            if k == "gps_maps_url" and val_str.startswith("http"):
                link = ctk.CTkLabel(
                    f, text=t("res.open_maps"), text_color=ACCENT,
                    font=ctk.CTkFont(size=12, underline=True), cursor="hand2",
                    anchor="w"
                )
                link.grid(row=0, column=1, padx=(0, 12), pady=7, sticky="w")
                link.bind("<Button-1>", lambda e, u=val_str: os.startfile(u))
            else:
                ctk.CTkLabel(
                    f, text=val_str, font=ctk.CTkFont(size=12),
                    anchor="w", wraplength=300, justify="left"
                ).grid(row=0, column=1, padx=(0, 12), pady=7, sticky="ew")

        ctk.CTkButton(
            self, text=t("res.close"), width=100, height=32,
            fg_color=ACCENT, text_color="#000",
            command=self.destroy
        ).grid(row=1, column=0, pady=(0, 15))
