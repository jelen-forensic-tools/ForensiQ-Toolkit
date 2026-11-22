# -*- coding: utf-8 -*-
"""
SearchPanel – search across all data of one extraction.

Top: output folder selection + index status (indexing runs in the background).
Below: search bar (text / number / phrase, source, date from–to).
Left: results (source, time, tags, snippet with the match) – double-click opens the detail.
Right: overview of entities found (numbers, e-mails, IBANs, …) – click to filter;
after a keyword list run it shows the hits per term instead.
Tag bar: tag selected / all results, remove a tag, add a note, show tagged items,
keyword lists and the report of tagged items (core/case_tags.py).

All database work runs in threads; results return to the UI via after().
"""
import os
import threading
from tkinter import ttk, filedialog

import customtkinter as ctk

from core import search_index as si
from core import case_tags as ctags
from core.i18n import t, has
from . import theme as th
from .tagging import KeywordListDialog, ask_text, confirm

PAGE = 300                      # records per results page
OVERVIEW_LIMIT = 500            # max. items in the entity overview
KINDS = ["phone"] + list(si.ENTITY_KINDS)

# File names whose module key differs from the file name
_FILE_MODULE = {
    "browser_history": "mod.browser",
    "wifi_networks":   "mod.wifi",
    "wifi":            "mod.wifi",
    "media_files":     "mod.media",
    "apps_installed":  "mod.apps",
    "documents_manifest": "mod.documents",
    "user_data_tree_manifest": "mod.user_data_tree",
}

# Keys that best describe a record (for the "Match" column without a snippet)
_SUMMARY_KEYS = ("name", "display_name", "contact", "address", "number", "phone",
                 "from", "to", "title", "subject", "filename", "body", "text",
                 "message", "url", "package", "app", "ssid", "account", "type")


def source_label(fname: str) -> str:
    """Readable source name based on the JSON file name."""
    stem = os.path.splitext(fname)[0]
    cands = [_FILE_MODULE.get(stem, ""), f"mod.{stem}"]
    if stem.startswith("ios_"):
        cands += [f"ios_bkp.mod.{stem[4:]}", f"ios.mod.{stem}"]
    for k in cands:
        if k and has(k):
            return t(k).strip()
    return stem.replace("_", " ").capitalize()


def _one_line(s: str, limit: int = 220) -> str:
    s = " · ".join(p.strip() for p in str(s).splitlines() if p.strip())
    return s if len(s) <= limit else s[:limit - 1] + "…"


def _short_path(p: str, limit: int = 60) -> str:
    """Shorten a long path from the left (the end of a path is the most telling part)."""
    return p if len(p) <= limit else "…" + p[-(limit - 1):]


def record_summary(rec: dict) -> str:
    """Short summary of a record from its most telling fields."""
    vals = []
    for k in _SUMMARY_KEYS:
        v = rec.get(k)
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v if x not in (None, ""))
        if v not in (None, "") and not isinstance(v, (dict, bool)):
            vals.append(str(v))
        if len(vals) >= 3:
            break
    if not vals:
        vals = [str(v) for v in rec.values()
                if v not in (None, "") and not isinstance(v, (dict, list, bool))][:3]
    return _one_line("  ·  ".join(vals))


def _parse_date_input(s: str, end: bool):
    """Date from a filter field → ISO string for comparison; None = empty; False = invalid."""
    s = (s or "").strip()
    if not s:
        return None
    p = si.parse_dt(s)
    if not p:
        return False
    if end and len(s) <= 10:            # jen datum → do konce dne
        p = p[:10] + " 23:59:59"
    return p


class SearchPanel(ctk.CTkFrame):
    def __init__(self, master, accent: str = th.ACCENT, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.accent = accent
        self._dir = ""
        self._files: list[str] = []             # indexed files
        self._index_thread = None
        self._cancel_index = False
        self._indexing = False
        self._closing = False
        self._query_gen = 0                     # discards late results
        self._rows: list[dict] = []             # displayed results
        self._total = 0
        self._last_query = None                 # (mode, args) for "Load more"
        self._entity_filter = None              # (kind, norm, label)
        self._kind = "phone"
        self._overview_rows: list[dict] = []
        self._summary: dict = {}
        self._last_stats = None                 # last indexing result
        self._row_tags: dict[str, list] = {}    # row_key → tag names (displayed rows)
        self._tag_names: list[str] = []
        self._kw = None                         # (list name, per_term, rows) after a keyword run

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build(self):
        self._style_tree()

        # Toolbar: folder + index status + re-index
        tb = th.Toolbar(self, progress=True)
        tb.grid(row=0, column=0, sticky="ew", pady=(0, th.GAP))
        self._progress = tb.progress
        self._open_btn = th.secondary_button(tb.source, t("srch.open"), self._choose_dir,
                                             width=150)
        self._open_btn.pack(side="left", padx=(0, 10))
        self._dir_label = ctk.CTkLabel(tb.source, text=t("srch.no_folder"),
                                       text_color=th.TEXT_MUTED, font=th.f_small(),
                                       anchor="w")
        self._dir_label.pack(side="left", fill="x", expand=True)
        self._status = ctk.CTkLabel(tb.info, text="", text_color=th.TEXT_MUTED,
                                    font=th.f_small(), anchor="e")
        self._status.pack(side="right")
        self._reindex_btn = th.secondary_button(tb.actions, t("srch.reindex"),
                                                lambda: self._start_index(force=True),
                                                width=130, state="disabled")
        self._reindex_btn.pack(side="left")

        # Search bar
        bar = th.card(self)
        bar.grid(row=1, column=0, sticky="ew", pady=(0, th.GAP))
        bar.grid_columnconfigure(0, weight=1)
        self._query_var = ctk.StringVar()
        self._entry = ctk.CTkEntry(bar, textvariable=self._query_var, height=th.BTN_H,
                                   placeholder_text=t("srch.placeholder"),
                                   fg_color=th.BG_INPUT, border_color=th.BORDER,
                                   font=th.f_body())
        self._entry.grid(row=0, column=0, sticky="ew", padx=(th.PAD, 8), pady=th.PAD - 4)
        self._entry.bind("<Return>", lambda e: self._run_search())
        self._source_var = ctk.StringVar(value=t("srch.all_sources"))
        self._source_menu = ctk.CTkOptionMenu(
            bar, variable=self._source_var, values=[t("srch.all_sources")],
            width=200, height=th.BTN_H, fg_color=th.BTN_SECONDARY,
            button_color=th.BTN_SECONDARY, button_hover_color=th.BTN_SECONDARY_HOVER,
            font=th.f_body(), dynamic_resizing=False,
            command=lambda _v: self._run_search())
        self._source_menu.grid(row=0, column=1, padx=(0, 8))
        self._from_entry = ctk.CTkEntry(bar, width=150, height=th.BTN_H,
                                        placeholder_text=t("srch.date_from"),
                                        fg_color=th.BG_INPUT, border_color=th.BORDER,
                                        font=th.f_body())
        self._from_entry.grid(row=0, column=2, padx=(0, 8))
        self._to_entry = ctk.CTkEntry(bar, width=150, height=th.BTN_H,
                                      placeholder_text=t("srch.date_to"),
                                      fg_color=th.BG_INPUT, border_color=th.BORDER,
                                      font=th.f_body())
        self._to_entry.grid(row=0, column=3, padx=(0, 8))
        for e in (self._from_entry, self._to_entry):
            e.bind("<Return>", lambda ev: self._run_search())
        self._search_btn = th.primary_button(bar, t("srch.search"), self._run_search,
                                             width=120, state="disabled")
        self._search_btn.grid(row=0, column=4, padx=(0, 8))
        self._kw_btn = th.secondary_button(bar, t("kw.btn"), self._open_keywords, width=170)
        self._kw_btn.grid(row=0, column=5, padx=(0, th.PAD))

        # Body: results on the left, overview on the right
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0, minsize=360)
        body.grid_rowconfigure(0, weight=1)

        # -- Results --
        res = th.card(body)
        res.grid(row=0, column=0, sticky="nsew", padx=(0, th.GAP))
        res.grid_columnconfigure(0, weight=1)
        res.grid_rowconfigure(3, weight=1)
        hdr = ctk.CTkFrame(res, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=th.PAD, pady=(th.PAD - 4, 8))
        hdr.grid_columnconfigure(1, weight=1)
        self._res_title = th.section_label(hdr, t("srch.results"))
        self._res_title.grid(row=0, column=0, sticky="w")
        self._count_lbl = ctk.CTkLabel(hdr, text="", text_color=th.TEXT_MUTED,
                                       font=th.f_small(), anchor="w")
        self._count_lbl.grid(row=0, column=1, sticky="w", padx=(12, 0))
        self._filter_btn = th.small_button(hdr, t("srch.clear_filter"),
                                           self._clear_entity_filter, width=120)
        self._build_tagbar(res)
        th.separator(res).grid(row=2, column=0, sticky="ew", padx=th.PAD)

        self._stack = ctk.CTkFrame(res, fg_color="transparent")
        self._stack.grid(row=3, column=0, sticky="nsew", padx=10, pady=(10, 0))
        self._stack.grid_columnconfigure(0, weight=1)
        self._stack.grid_rowconfigure(0, weight=1)

        tree_wrap = ctk.CTkFrame(self._stack, fg_color=th.BG_CARD, corner_radius=8)
        tree_wrap.grid(row=0, column=0, sticky="nsew")
        tree_wrap.grid_columnconfigure(0, weight=1)
        tree_wrap.grid_rowconfigure(0, weight=1)
        self._tree = ttk.Treeview(tree_wrap, columns=("source", "time", "tags", "match"),
                                  show="headings", style="Srch.Treeview",
                                  selectmode="extended")
        vsb = ctk.CTkScrollbar(tree_wrap, orientation="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns", padx=(2, 0))
        self._tree.column("source", width=170, minwidth=100, stretch=False)
        self._tree.column("time", width=175, minwidth=110, stretch=False)
        self._tree.column("tags", width=200, minwidth=60, stretch=False)
        self._tree.column("match", width=600, minwidth=200, stretch=True)
        self._tree.tag_configure("odd", background="#16213e")
        self._tree.tag_configure("even", background="#1c2850")
        self._tree.tag_configure("tagged", foreground="#ffcc80")
        self._tree.bind("<Double-1>", self._open_detail)
        self._tree.bind("<Return>", self._open_detail)
        self._set_tree_headings()

        # Intro text (until a folder is selected)
        self._intro = th.hint_label(self._stack, t("srch.intro"), ref=self._stack, offset=40)
        self._intro.grid(row=0, column=0, padx=30, pady=40, sticky="n")
        self._intro.lift()

        foot = ctk.CTkFrame(res, fg_color="transparent")
        foot.grid(row=4, column=0, sticky="ew", padx=th.PAD, pady=(8, th.PAD - 4))
        foot.grid_columnconfigure(0, weight=1)
        self._hint = ctk.CTkLabel(foot, text=t("srch.hint_dbl"), text_color=th.TEXT_DIM,
                                  font=th.f_small(), anchor="w")
        self._hint.grid(row=0, column=0, sticky="w")
        self._tag_status = ctk.CTkLabel(foot, text="", text_color=th.TEXT_MUTED,
                                        font=th.f_small(), anchor="e")
        self._tag_status.grid(row=0, column=1, sticky="e", padx=(12, 12))
        self._more_btn = th.small_button(foot, t("srch.more"), self._load_more, width=130)
        self._more_btn.grid(row=0, column=2, sticky="e")
        self._more_btn.grid_remove()

        # -- Entity overview --
        ov = th.card(body, width=360)
        ov.grid(row=0, column=1, sticky="nsew")
        ov.grid_columnconfigure(0, weight=1)
        ov.grid_rowconfigure(3, weight=1)
        self._ov_title = th.section_label(ov, t("srch.overview"))
        self._ov_title.grid(row=0, column=0, sticky="w", padx=th.PAD, pady=(th.PAD - 4, 8))
        th.separator(ov).grid(row=1, column=0, sticky="ew", padx=th.PAD)
        self._kind_var = ctk.StringVar(value=self._kind_label("phone"))
        self._kind_menu = ctk.CTkOptionMenu(
            ov, variable=self._kind_var, values=[self._kind_label(k) for k in KINDS],
            height=th.BTN_H, fg_color=th.BTN_SECONDARY, button_color=th.BTN_SECONDARY,
            button_hover_color=th.BTN_SECONDARY_HOVER, font=th.f_body(),
            dynamic_resizing=False, command=self._on_kind_selected)
        self._kind_menu.grid(row=2, column=0, sticky="ew", padx=th.PAD, pady=(10, 8))

        ov_wrap = ctk.CTkFrame(ov, fg_color=th.BG_CARD, corner_radius=8)
        ov_wrap.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 10))
        ov_wrap.grid_columnconfigure(0, weight=1)
        ov_wrap.grid_rowconfigure(0, weight=1)
        self._ov_tree = ttk.Treeview(ov_wrap, columns=("value", "count"), show="headings",
                                     style="Srch.Treeview", selectmode="browse")
        ovsb = ctk.CTkScrollbar(ov_wrap, orientation="vertical", command=self._ov_tree.yview)
        self._ov_tree.configure(yscrollcommand=ovsb.set)
        self._ov_tree.grid(row=0, column=0, sticky="nsew")
        ovsb.grid(row=0, column=1, sticky="ns", padx=(2, 0))
        self._ov_tree.column("value", width=250, minwidth=120, stretch=True)
        self._ov_tree.column("count", width=70, minwidth=50, stretch=False, anchor="e")
        self._ov_tree.tag_configure("odd", background="#16213e")
        self._ov_tree.tag_configure("even", background="#1c2850")
        self._ov_tree.bind("<<TreeviewSelect>>", self._on_overview_select)
        self._set_ov_headings()

    def _style_tree(self):
        fam = ctk.CTkFont().cget("family")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Srch.Treeview", background=th.BG_CARD, foreground="#e0e0e0",
                        fieldbackground=th.BG_CARD, rowheight=28, borderwidth=0,
                        bordercolor=th.BG_CARD, lightcolor=th.BG_CARD,
                        darkcolor=th.BG_CARD, font=(fam, 12))
        style.configure("Srch.Treeview.Heading", background="#0d1b3e",
                        foreground=th.ACCENT, font=(fam, 12, "bold"), relief="flat",
                        borderwidth=0)
        style.map("Srch.Treeview", background=[("selected", "#1e4080")],
                  foreground=[("selected", "#ffffff")])
        style.map("Srch.Treeview.Heading", background=[("active", "#13264f")])

    def _set_tree_headings(self):
        self._tree.heading("source", text=t("srch.col_source"), anchor="w")
        self._tree.heading("time", text=t("srch.col_time"), anchor="w")
        self._tree.heading("tags", text=t("tag.col_tags"), anchor="w")
        self._tree.heading("match", text=t("srch.col_match"), anchor="w")

    def _set_ov_headings(self):
        self._ov_tree.heading("value", text=t("srch.col_value"), anchor="w")
        self._ov_tree.heading("count", text=t("srch.col_count"), anchor="e")

    def _kind_label(self, kind: str) -> str:
        base = t(f"srch.kind.{kind}")
        n = self._summary.get(kind) if hasattr(self, "_summary") else None
        return f"{base} ({n})" if n else base

    # ── Folder and indexing ──────────────────────────────────────────────────

    def _choose_dir(self):
        folder = filedialog.askdirectory(title=t("srch.folder_title"))
        if folder:
            self.set_dir(folder)

    def set_dir(self, output_dir: str):
        """Set the output folder and index it (incrementally) in the background."""
        if not output_dir or not os.path.isdir(output_dir):
            return
        output_dir = os.path.normpath(output_dir)
        if output_dir == self._dir and self._indexing:
            return
        self._dir = output_dir
        self._dir_label.configure(text=_short_path(output_dir))
        self._intro.grid_remove()
        self._clear_results()
        self._entity_filter = None
        self._filter_btn.grid_remove()
        self._exit_keyword_mode()
        self._load_tags()
        self._start_index()

    def _start_index(self, force: bool = False):
        if not self._dir:
            return
        if self._indexing:
            # Indexing of another folder / again is running → cancel and restart once it ends
            self._cancel_index = True
            self.after(200, lambda: self._start_index(force))
            return
        self._indexing = True
        self._cancel_index = False
        self._reindex_btn.configure(state="disabled")
        self._search_btn.configure(state="disabled")
        if self._progress is not None:
            self._progress.set(0)
        self._status.configure(text=t("srch.indexing").format(done=0, total="…", file=""))
        target = self._dir

        def progress(done, total, name):
            if not self._closing:
                self.after(0, lambda: self._on_index_progress(target, done, total, name))

        def worker():
            try:
                stats = si.build_index(target, progress=progress,
                                       cancel=lambda: self._cancel_index or self._closing,
                                       force=force)
                err = None
            except Exception as e:          # e.g. a read-only folder
                stats, err = None, str(e)
            if not self._closing:
                self.after(0, lambda: self._on_index_done(target, stats, err))

        self._index_thread = threading.Thread(target=worker, daemon=True)
        self._index_thread.start()

    def _on_index_progress(self, target, done, total, name):
        if target != self._dir or self._closing:
            return
        if self._progress is not None and total:
            self._progress.set(done / total)
        self._status.configure(text=t("srch.indexing").format(
            done=done, total=total, file=name))

    def _on_index_done(self, target, stats, err):
        self._indexing = False
        if self._closing:
            return
        if target != self._dir:
            return                       # another folder was selected meanwhile (a new indexing run is in progress)
        self._reindex_btn.configure(state="normal")
        if err:
            self._status.configure(text=t("srch.index_failed").format(err=err),
                                   text_color=th.RED)
            return
        if self._progress is not None:
            self._progress.set(1)
        self._last_stats = stats
        self._show_stats()
        if stats["errors"]:
            bad = ", ".join(e.split(":", 1)[0] for e in stats["errors"][:5])
            self._count_lbl.configure(text=t("srch.index_errors").format(files=bad),
                                      text_color=th.ORANGE)
        self._search_btn.configure(state="normal")
        self._load_meta()
        self._run_search()

    def _show_stats(self):
        st = self._last_stats
        if not st:
            return
        self._status.configure(text=t("srch.indexed").format(
            n=f"{st['records']:,}".replace(",", " "), f=st["files"]),
            text_color=th.TEXT_MUTED)

    def _load_meta(self):
        """Source list for the filter + entity summary for the overview (in a thread)."""
        target = self._dir

        def worker():
            try:
                with si.SearchIndex(target) as ix:
                    files = [f["name"] for f in ix.files() if f["records"]]
                    summary = ix.entity_summary()
                    summary["phone"] = len(ix.top_numbers(limit=100000))
            except Exception:
                files, summary = [], {}
            if not self._closing:
                self.after(0, lambda: self._apply_meta(target, files, summary))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_meta(self, target, files, summary):
        if target != self._dir:
            return
        self._files = files
        self._summary = summary
        self._source_map = {source_label(f): f for f in files}
        labels = [t("srch.all_sources")] + sorted(self._source_map)
        self._source_menu.configure(values=labels)
        if self._source_var.get() not in labels:
            self._source_var.set(t("srch.all_sources"))
        self._refresh_kind_menu()
        self._load_overview()

    def _refresh_kind_menu(self):
        self._kind_menu.configure(values=[self._kind_label(k) for k in KINDS])
        self._kind_var.set(self._kind_label(self._kind))

    # ── Search ───────────────────────────────────────────────────────────────

    def _selected_files(self):
        lbl = self._source_var.get()
        f = getattr(self, "_source_map", {}).get(lbl)
        return [f] if f else None

    def _run_search(self):
        if not self._dir or not si.index_exists(self._dir):
            return
        d_from = _parse_date_input(self._from_entry.get(), end=False)
        d_to = _parse_date_input(self._to_entry.get(), end=True)
        for raw, val in ((self._from_entry.get(), d_from), (self._to_entry.get(), d_to)):
            if val is False:
                self._count_lbl.configure(text=t("srch.bad_date").format(v=raw.strip()),
                                          text_color=th.RED)
                return
        self._entity_filter = None
        self._filter_btn.grid_remove()
        self._res_title.configure(text=t("srch.results"))
        self._exit_keyword_mode()
        self._last_query = ("text", (self._query_var.get(), self._selected_files(),
                                     d_from, d_to))
        self._execute(offset=0)

    def _execute(self, offset: int):
        """Run the last query (self._last_query) in a thread from the given offset."""
        if not self._last_query:
            return
        self._query_gen += 1
        gen = self._query_gen
        mode, args = self._last_query
        target = self._dir

        def worker():
            rows, total, err = [], 0, None
            try:
                with si.SearchIndex(target) as ix:
                    if mode == "text":
                        q, files, d_from, d_to = args
                        rows = ix.search(q, files=files, date_from=d_from, date_to=d_to,
                                         limit=PAGE, offset=offset)
                        total = (ix.count(q, files=files, date_from=d_from, date_to=d_to)
                                 if offset == 0 else self._total)
                    elif mode == "tagged":
                        with ctags.CaseTags(target) as tg:
                            allrows = tg.tagged_items(args or None)
                        total, rows = len(allrows), allrows[offset:offset + PAGE]
                    elif mode == "keywords":
                        total, rows = len(args), args[offset:offset + PAGE]
                    elif mode == "number":
                        allrows = ix.by_number(args, limit=100000)
                        total, rows = len(allrows), allrows[offset:offset + PAGE]
                    else:                        # entity
                        kind, norm = args
                        allrows = ix.by_entity(kind, norm, limit=100000)
                        total, rows = len(allrows), allrows[offset:offset + PAGE]
            except Exception as e:
                err = str(e)
            if not self._closing:
                self.after(0, lambda: self._apply_results(gen, offset, rows, total, err))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_results(self, gen, offset, rows, total, err):
        if gen != self._query_gen or self._closing:
            return                      # a newer query was started meanwhile
        if err:
            self._count_lbl.configure(text=err, text_color=th.RED)
            return
        if offset == 0:
            self._clear_results()
        self._total = total
        start = len(self._rows)
        self._rows.extend(rows)
        self._row_tags.update(self._fetch_tags([r["row_key"] for r in rows]))
        for i, r in enumerate(rows, start=start):
            match = _one_line(r["snippet"]) if r.get("snippet") else record_summary(r["record"])
            if r.get("keywords"):
                match = "🔑 " + ", ".join(r["keywords"]) + "  ·  " + match
            if r.get("notes"):
                match = "📝 " + " | ".join(r["notes"]) + "  ·  " + match
            self._tree.insert("", "end", iid=str(i),
                              values=(source_label(r["file"]), r["dt"] or "",
                                      self._tags_text(r["row_key"]), match),
                              tags=self._row_style(i, r["row_key"]))
        self._has_results = True
        self._show_count()
        if len(self._rows) < total:
            self._more_btn.grid()
        else:
            self._more_btn.grid_remove()

    def _show_count(self):
        if not getattr(self, "_has_results", False):
            return
        shown, total = len(self._rows), self._total
        if total == 0:
            txt = t("srch.empty")
        elif shown < total:
            txt = t("srch.shown").format(n=total, shown=shown)
        else:
            txt = t("srch.found").format(n=total)
        self._count_lbl.configure(text=txt, text_color=th.TEXT_MUTED)

    def _load_more(self):
        self._execute(offset=len(self._rows))

    def _clear_results(self):
        self._has_results = False
        self._rows = []
        self._row_tags = {}
        self._total = 0
        self._tree.delete(*self._tree.get_children())
        self._more_btn.grid_remove()
        self._count_lbl.configure(text="")

    def _open_detail(self, event=None):
        sel = self._tree.selection()
        if not sel:
            return
        try:
            r = self._rows[int(sel[0])]
        except (ValueError, IndexError):
            return
        from .results_panel import _RowDetailDialog
        _RowDetailDialog(self, r["record"], source_label(r["file"]))

    # ── Entity overview ──────────────────────────────────────────────────────

    def _on_kind_selected(self, label: str):
        for k in KINDS:
            if self._kind_label(k) == label:
                self._kind = k
                break
        self._load_overview()

    def _load_overview(self):
        if not self._dir or not si.index_exists(self._dir):
            return
        kind, target = self._kind, self._dir

        def worker():
            try:
                with si.SearchIndex(target) as ix:
                    if kind == "phone":
                        rows = [{"value": n["raw"], "norm": n["number"], "count": n["count"]}
                                for n in ix.top_numbers(limit=OVERVIEW_LIMIT)]
                    else:
                        rows = [{"value": e["value"], "norm": e["norm"], "count": e["count"]}
                                for e in ix.entities(kind, limit=OVERVIEW_LIMIT)]
            except Exception:
                rows = []
            if not self._closing:
                self.after(0, lambda: self._apply_overview(target, kind, rows))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_overview(self, target, kind, rows):
        if target != self._dir or kind != self._kind or self._kw:
            return
        self._overview_rows = rows
        self._ov_tree.delete(*self._ov_tree.get_children())
        for i, r in enumerate(rows):
            self._ov_tree.insert("", "end", iid=str(i), values=(r["value"], r["count"]),
                                 tags=("even" if i % 2 == 0 else "odd",))

    def _on_overview_select(self, event=None):
        sel = self._ov_tree.selection()
        if not sel:
            return
        try:
            r = self._overview_rows[int(sel[0])]
        except (ValueError, IndexError):
            return
        if self._kw:                              # keyword mode → rows hit by this term
            rows = [x for x in self._kw[2] if r["value"] in x.get("keywords", [])]
            self._last_query = ("keywords", rows)
            self._res_title.configure(text=t("kw.results_term").format(term=r["value"]))
            self._filter_btn.grid(row=0, column=2, sticky="e")
            self._execute(offset=0)
            return
        if self._kind == "phone":
            self._last_query = ("number", r["norm"])
        else:
            self._last_query = ("entity", (self._kind, r["norm"]))
        self._entity_filter = (self._kind, r["norm"], r["value"])
        self._res_title.configure(text=t("srch.filter").format(v=r["value"]))
        self._filter_btn.grid(row=0, column=2, sticky="e")
        self._execute(offset=0)

    def _clear_entity_filter(self):
        self._entity_filter = None
        self._filter_btn.grid_remove()
        self._ov_tree.selection_remove(*self._ov_tree.selection())
        if self._kw:                              # back to all hits of the keyword list
            self._show_keyword_rows()
            return
        self._res_title.configure(text=t("srch.results"))
        self._run_search()

    # ── Tags ─────────────────────────────────────────────────────────────────

    def _build_tagbar(self, parent):
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=th.PAD, pady=(0, 8))
        self._tag_var = ctk.StringVar(value="")
        self._tag_menu = ctk.CTkOptionMenu(
            bar, variable=self._tag_var, values=[""], width=170, height=th.BTN_H_SMALL,
            fg_color=th.BTN_SECONDARY, button_color=th.BTN_SECONDARY,
            button_hover_color=th.BTN_SECONDARY_HOVER, font=th.f_small(),
            dynamic_resizing=False, command=self._on_tag_menu)
        self._tag_menu.pack(side="left", padx=(0, 6))
        self._tag_btns = {}
        for key, cmd, w in (("tag.btn_tag", self._tag_selected, 120),
                            ("tag.btn_tag_all", self._tag_all, 120),
                            ("tag.btn_untag", self._untag_selected, 90),
                            ("tag.btn_note", self._note_selected, 90)):
            b = th.small_button(bar, t(key), cmd, width=w)
            b.pack(side="left", padx=(0, 6))
            self._tag_btns[key] = b
        for key, cmd, w in (("tag.btn_report", self._tagged_report, 160),
                            ("tag.btn_show", self._show_tagged, 140)):
            b = th.small_button(bar, t(key), cmd, width=w)
            b.pack(side="right", padx=(6, 0))
            self._tag_btns[key] = b

    def _refresh_tagbar_lang(self):
        for key, b in self._tag_btns.items():
            b.configure(text=t(key))
        self._kw_btn.configure(text=t("kw.btn"))
        self._load_tags()

    def _selected_tag(self) -> str:
        return self._tag_label_map.get(self._tag_var.get(), "") if hasattr(self, "_tag_label_map") else ""

    def _load_tags(self, select: str | None = None):
        """Fill the tag menu ("name (count)", + new, − delete)."""
        if not self._dir:
            return
        try:
            with ctags.CaseTags(self._dir) as tg:
                tags = tg.list_tags()
        except Exception as e:
            self._tag_status.configure(text=str(e), text_color=th.RED)
            return
        cur = select or self._selected_tag()
        self._tag_names = [x["name"] for x in tags]
        self._tag_label_map = {f'{x["name"]} ({x["count"]})': x["name"] for x in tags}
        labels = list(self._tag_label_map) + [t("tag.new"), t("tag.delete")]
        self._tag_menu.configure(values=labels)
        pick = next((lbl for lbl, n in self._tag_label_map.items() if n == cur), None)
        self._tag_var.set(pick or (labels[0] if self._tag_label_map else ""))

    def _on_tag_menu(self, label: str):
        if label == t("tag.new"):
            name = ask_text(self, t("tag.new"), t("tag.name_prompt"))
            if name:
                with ctags.CaseTags(self._dir) as tg:
                    tg.add_tag(name, tg.next_color())
            self._load_tags(select=name)
        elif label == t("tag.delete"):
            self._load_tags()                  # restore the previous selection first
            name = self._selected_tag()
            if name and confirm(self, t("tag.delete"), t("tag.delete_q").format(name=name)):
                with ctags.CaseTags(self._dir) as tg:
                    tg.delete_tag(name)
                self._load_tags()
                self._refresh_row_tags()

    def _fetch_tags(self, keys) -> dict:
        if not self._dir or not keys:
            return {}
        try:
            with ctags.CaseTags(self._dir) as tg:
                return tg.tags_for(keys)
        except Exception:
            return {}

    def _tags_text(self, key: str) -> str:
        return ", ".join(self._row_tags.get(key, []))

    def _row_style(self, i: int, key: str) -> tuple:
        base = "even" if i % 2 == 0 else "odd"
        return (base, "tagged") if self._row_tags.get(key) else (base,)

    def _refresh_row_tags(self):
        """Re-read tags of all displayed rows and update the Tags column."""
        self._row_tags = self._fetch_tags([r["row_key"] for r in self._rows])
        for i, r in enumerate(self._rows):
            iid = str(i)
            if self._tree.exists(iid):
                self._tree.set(iid, "tags", self._tags_text(r["row_key"]))
                self._tree.item(iid, tags=self._row_style(i, r["row_key"]))

    def _selected_rows(self) -> list[dict]:
        out = []
        for iid in self._tree.selection():
            try:
                out.append(self._rows[int(iid)])
            except (ValueError, IndexError):
                pass
        return out

    def _tag_feedback(self, text: str, ok: bool = True):
        self._tag_status.configure(text=text, text_color=th.GREEN if ok else th.ORANGE)

    def _require_tag(self) -> str:
        name = self._selected_tag()
        if not name:
            self._tag_feedback(t("tag.pick_tag"), ok=False)
        return name

    def _tag_selected(self):
        rows, name = self._selected_rows(), self._require_tag()
        if not name:
            return
        if not rows:
            self._tag_feedback(t("tag.select_rows"), ok=False)
            return
        with ctags.CaseTags(self._dir) as tg:
            n = tg.tag(rows, name)
        self._tag_feedback(t("tag.done").format(n=n, tag=name))
        self._load_tags()
        self._refresh_row_tags()

    def _tag_all(self):
        name = self._require_tag()
        if not name or not self._last_query or not self._total:
            return
        if not confirm(self, t("tag.btn_tag_all"),
                       t("tag.all_q").format(n=self._total, tag=name)):
            return
        mode, args = self._last_query
        target = self._dir
        self._tag_feedback(t("tag.working"))

        def worker():
            try:
                if mode in ("keywords",):
                    rows = list(args)
                elif mode == "tagged":
                    with ctags.CaseTags(target) as tg:
                        rows = tg.tagged_items(args or None)
                else:
                    with si.SearchIndex(target) as ix:
                        if mode == "text":
                            q, files, d_from, d_to = args
                            rows = ix.search(q, files=files, date_from=d_from, date_to=d_to,
                                             limit=max(self._total, 1))
                        elif mode == "number":
                            rows = ix.by_number(args, limit=100000)
                        else:
                            rows = ix.by_entity(*args, limit=100000)
                reason = None
                if mode == "keywords" and self._kw:
                    reason = f"keyword list: {self._kw[0]}"
                elif mode == "text":
                    reason = f"search: {args[0]}"
                with ctags.CaseTags(target) as tg:
                    n = tg.tag(rows, name, reason=reason or "")
                err = None
            except Exception as e:
                n, err = 0, str(e)
            if not self._closing:
                self.after(0, lambda: self._after_tag_all(target, n, name, err))

        threading.Thread(target=worker, daemon=True).start()

    def _after_tag_all(self, target, n, name, err):
        if target != self._dir:
            return
        if err:
            self._tag_feedback(err, ok=False)
            return
        self._tag_feedback(t("tag.done").format(n=n, tag=name))
        self._load_tags()
        self._refresh_row_tags()

    def _untag_selected(self):
        rows, name = self._selected_rows(), self._require_tag()
        if not name:
            return
        if not rows:
            self._tag_feedback(t("tag.select_rows"), ok=False)
            return
        with ctags.CaseTags(self._dir) as tg:
            n = tg.untag([r["row_key"] for r in rows], name)
        self._tag_feedback(t("tag.removed").format(n=n, tag=name))
        self._load_tags()
        if self._last_query and self._last_query[0] == "tagged":
            self._execute(offset=0)            # the item may no longer belong to the view
        else:
            self._refresh_row_tags()

    def _note_selected(self):
        rows = [r for r in self._selected_rows() if self._row_tags.get(r["row_key"])]
        if not rows:
            self._tag_feedback(t("tag.note_needs_tag"), ok=False)
            return
        first = rows[0].get("notes") or []
        note = ask_text(self, t("tag.btn_note"), t("tag.note_prompt"),
                        initial=first[0] if first else "")
        if note is None:
            return
        with ctags.CaseTags(self._dir) as tg:
            for r in rows:
                tg.set_note(r["row_key"], note)
        self._tag_feedback(t("tag.note_saved").format(n=len(rows)))
        if self._last_query and self._last_query[0] == "tagged":
            self._execute(offset=0)

    def _show_tagged(self):
        if not self._dir:
            return
        self._exit_keyword_mode()
        self._entity_filter = None
        self._last_query = ("tagged", None)
        self._res_title.configure(text=t("tag.view_title"))
        self._filter_btn.grid(row=0, column=2, sticky="e")
        self._execute(offset=0)

    def _tagged_report(self):
        if not self._dir:
            return
        target = self._dir
        provider = getattr(self, "context_provider", None)
        dev, case = provider(target) if provider else (None, None)
        self._tag_feedback(t("tag.report_working"))

        def worker():
            try:
                from core.report import generate_tagged_reports
                pdf, _html, n = generate_tagged_reports(target, device_info=dev, case_meta=case)
                err = None
            except Exception as e:
                pdf, n, err = "", 0, str(e)
            if not self._closing:
                self.after(0, lambda: self._after_report(pdf, n, err))

        threading.Thread(target=worker, daemon=True).start()

    def _after_report(self, pdf, n, err):
        if err:
            self._tag_feedback(err, ok=False)
            return
        self._tag_feedback(t("tag.report_done").format(n=n))
        if pdf and os.path.exists(pdf):
            os.startfile(pdf)

    # ── Keyword lists ────────────────────────────────────────────────────────

    def _open_keywords(self):
        if not self._dir or not si.index_exists(self._dir):
            self._count_lbl.configure(text=t("kw.need_index"), text_color=th.ORANGE)
            return
        KeywordListDialog(self, self._run_keywords)

    def _run_keywords(self, name: str, terms: list[str]):
        target = self._dir
        self._count_lbl.configure(text=t("kw.running").format(n=len(terms)),
                                  text_color=th.TEXT_MUTED)

        def worker():
            try:
                per_term, rows = ctags.run_keywords(target, terms, cancel=lambda: self._closing)
                ctags.audit(target, "keyword_list", list=name, terms=len(terms),
                            hits=len(rows), per_term=per_term)
                err = None
            except Exception as e:
                per_term, rows, err = [], [], str(e)
            if not self._closing:
                self.after(0, lambda: self._apply_keywords(target, name, per_term, rows, err))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_keywords(self, target, name, per_term, rows, err):
        if target != self._dir:
            return
        if err:
            self._count_lbl.configure(text=err, text_color=th.RED)
            return
        self._kw = (name, per_term, rows)
        # Overview → hits per term (click = rows of that term)
        self._ov_title.configure(text=t("kw.overview").format(name=name))
        self._kind_menu.grid_remove()
        ov = sorted(per_term, key=lambda x: -x["count"])
        self._overview_rows = [{"value": x["term"], "norm": x["term"], "count": x["count"]}
                               for x in ov]
        self._ov_tree.delete(*self._ov_tree.get_children())
        for i, r in enumerate(self._overview_rows):
            self._ov_tree.insert("", "end", iid=str(i), values=(r["value"], r["count"]),
                                 tags=("even" if i % 2 == 0 else "odd",))
        self._show_keyword_rows()

    def _show_keyword_rows(self):
        name, per_term, rows = self._kw
        hit_terms = sum(1 for x in per_term if x["count"])
        self._res_title.configure(text=t("kw.results").format(
            name=name, terms=hit_terms, total=len(per_term)))
        self._filter_btn.grid_remove()
        self._last_query = ("keywords", rows)
        self._execute(offset=0)

    def _exit_keyword_mode(self):
        """Leave the keyword-list view: entity overview comes back (reloaded)."""
        if self._kw:
            self._kw = None
            self._ov_title.configure(text=t("srch.overview"))
            self._kind_menu.grid()
            self._ov_tree.delete(*self._ov_tree.get_children())
            self._overview_rows = []
            self._load_overview()

    # ── Other ────────────────────────────────────────────────────────────────

    def refresh_tags(self, folder: str = ""):
        """Tags were changed elsewhere (Results / Timeline) – update menu and rows."""
        if not self._dir or (folder and os.path.normcase(os.path.abspath(folder))
                             != os.path.normcase(os.path.abspath(self._dir))):
            return
        self._load_tags()
        if self._last_query and self._last_query[0] == "tagged":
            self._execute(offset=0)
        elif self._rows:
            self._refresh_row_tags()

    def shutdown(self):
        """Stop background indexing (called when the application closes)."""
        self._closing = True
        self._cancel_index = True

    def refresh_lang(self):
        self._open_btn.configure(text=t("srch.open"))
        self._reindex_btn.configure(text=t("srch.reindex"))
        self._search_btn.configure(text=t("srch.search"))
        self._more_btn.configure(text=t("srch.more"))
        self._filter_btn.configure(text=t("srch.clear_filter"))
        self._hint.configure(text=t("srch.hint_dbl"))
        self._ov_title.configure(text=t("srch.overview"))
        self._intro.configure(text=t("srch.intro"))
        self._entry.configure(placeholder_text=t("srch.placeholder"))
        self._from_entry.configure(placeholder_text=t("srch.date_from"))
        self._to_entry.configure(placeholder_text=t("srch.date_to"))
        if not self._dir:
            self._dir_label.configure(text=t("srch.no_folder"))
        if self._entity_filter:
            self._res_title.configure(text=t("srch.filter").format(v=self._entity_filter[2]))
        elif not self._kw and not (self._last_query and self._last_query[0] == "tagged"):
            self._res_title.configure(text=t("srch.results"))
        self._set_tree_headings()
        self._set_ov_headings()
        self._refresh_tagbar_lang()
        if self._files:
            self._apply_meta(self._dir, self._files, self._summary)
        else:
            self._source_menu.configure(values=[t("srch.all_sources")])
            self._source_var.set(t("srch.all_sources"))
            self._refresh_kind_menu()
        # Translate the source names in the displayed results
        for i, r in enumerate(self._rows):
            if self._tree.exists(str(i)):
                self._tree.set(str(i), "source", source_label(r["file"]))
        if not self._indexing:
            self._show_stats()
        self._show_count()
