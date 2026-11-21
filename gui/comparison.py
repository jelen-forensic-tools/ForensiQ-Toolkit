import customtkinter as ctk
import os
import json
import threading
from tkinter import filedialog
from typing import Optional
from core.i18n import t
from . import theme as th

ACCENT   = "#00d4ff"
BG_CARD  = "#16213e"
BG_PANEL = "#1a1a2e"
TEXT_MUTED = "#888888"

CLR_NEW     = "#4CAF50"
CLR_REMOVED = "#f44336"
CLR_CHANGED = "#FF9800"
CLR_SAME    = "#888888"


def _tint(color: str, alpha: float = 0.2, bg: str = BG_CARD) -> str:
    """Barva smíchaná s pozadím (Tk nezná průhlednost typu #RRGGBBAA)."""
    c = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(bg[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(bb + (cc - bb) * alpha):02x}" for cc, bb in zip(c, b))

COMPARABLE_FILES = {
    "contacts.json":        "mod.contacts",
    "sms.json":             "mod.sms",
    "call_logs.json":       "mod.call_logs",
    "apps.json":            "mod.apps",
    "wifi_networks.json":   "mod.wifi",
    "accounts.json":        "mod.accounts",
    "browser_history.json": "mod.browser",
    "device_info.json":     "mod.device_info",
}

# Keys used to identify records (primary keys)
RECORD_KEYS = {
    "contacts.json":        "number",
    "sms.json":             None,        # comparison by index
    "call_logs.json":       None,
    "apps.json":            "package",
    "wifi_networks.json":   "ssid",
    "accounts.json":        "name",
    "browser_history.json": "url",
    "device_info.json":     None,        # dict → field comparison
}


def _load_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _compare_lists(data_a: list, data_b: list, key: Optional[str]) -> dict:
    """Compare two lists of records. Returns a dict with new/removed/changed/same."""
    if key:
        map_a = {str(r.get(key, i)): r for i, r in enumerate(data_a)}
        map_b = {str(r.get(key, i)): r for i, r in enumerate(data_b)}
    else:
        map_a = {str(i): r for i, r in enumerate(data_a)}
        map_b = {str(i): r for i, r in enumerate(data_b)}

    keys_a = set(map_a)
    keys_b = set(map_b)

    new_items     = [map_b[k] for k in keys_b - keys_a]
    removed_items = [map_a[k] for k in keys_a - keys_b]
    changed_items = []
    same_items    = []

    for k in keys_a & keys_b:
        if map_a[k] != map_b[k]:
            changed_items.append({"before": map_a[k], "after": map_b[k]})
        else:
            same_items.append(map_a[k])

    return {
        "new":     new_items,
        "removed": removed_items,
        "changed": changed_items,
        "same":    same_items,
    }


def _compare_dicts(a: dict, b: dict) -> dict:
    """Compare two dicts field by field."""
    all_keys = set(a) | set(b)
    changed = {}
    for k in all_keys:
        va, vb = a.get(k, "—"), b.get(k, "—")
        if va != vb:
            changed[k] = {"before": va, "after": vb}
    return changed


def compare_extractions(dir_a: str, dir_b: str) -> dict:
    """Compare two extraction folders. Returns a dict of results."""
    results = {}
    for filename, label_key in COMPARABLE_FILES.items():
        path_a = os.path.join(dir_a, filename)
        path_b = os.path.join(dir_b, filename)

        data_a = _load_json(path_a)
        data_b = _load_json(path_b)

        if data_a is None and data_b is None:
            continue

        if data_a is None:
            data_a = [] if isinstance(data_b, list) else {}
        if data_b is None:
            data_b = [] if isinstance(data_a, list) else {}

        pk = RECORD_KEYS.get(filename)

        if isinstance(data_a, list) and isinstance(data_b, list):
            diff = _compare_lists(data_a, data_b, pk)
        elif isinstance(data_a, dict) and isinstance(data_b, dict):
            diff = {"fields": _compare_dicts(data_a, data_b)}
        else:
            diff = {"error": t("cmp.incompatible")}

        results[filename] = {"label_key": label_key, "diff": diff}

    return results


class ComparisonPanel(ctk.CTkFrame):
    def __init__(self, master, accent: str = ACCENT, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.accent = accent
        self._dir_a = ctk.StringVar(value="")
        self._dir_b = ctk.StringVar(value="")
        self._results: dict = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()

    def _build(self):
        # ── Toolbar: výběr dvou záloh + Porovnat ─────────────────────────────
        tb = th.Toolbar(self)
        tb.grid(row=0, column=0, sticky="ew", pady=(0, th.GAP))

        def _pick(label_key, var, ph_key, which):
            lbl = th.field_label(tb.source, t(label_key))
            lbl.pack(side="left", padx=(0, 8))
            entry = ctk.CTkEntry(tb.source, textvariable=var, placeholder_text=t(ph_key),
                                 state="readonly", height=th.BTN_H, font=th.f_body())
            entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
            btn = th.secondary_button(tb.source, t("cmp.browse"), lambda: self._browse(which),
                                      width=110)
            btn.pack(side="left", padx=(0, 18))
            return lbl, entry, btn

        self._label_a, self._entry_a, self._browse_a_btn = _pick(
            "cmp.backup_a", self._dir_a, "cmp.placeholder_a", "a")
        self._label_b, self._entry_b, self._browse_b_btn = _pick(
            "cmp.backup_b", self._dir_b, "cmp.placeholder_b", "b")

        self._compare_btn = th.primary_button(tb.actions, t("cmp.compare"),
                                              self._run_comparison)
        self._compare_btn.pack(side="left")

        # ── Results ──────────────────────────────────────────────────────────
        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._scroll.grid(row=1, column=0, sticky="nsew")
        self._scroll.grid_columnconfigure(0, weight=1)

        self._placeholder = ctk.CTkLabel(
            self._scroll,
            text=t("cmp.placeholder"),
            text_color=TEXT_MUTED,
            font=th.f_body(),
            wraplength=500, justify="center",
        )
        self._placeholder.grid(row=0, column=0, pady=40)

    def _browse(self, which: str):
        folder = filedialog.askdirectory(title=t("cmp.folder_title"))
        if not folder:
            return
        if which == "a":
            self._dir_a.set(folder)
        else:
            self._dir_b.set(folder)

    def _run_comparison(self):
        dir_a = self._dir_a.get()
        dir_b = self._dir_b.get()
        if not dir_a or not dir_b:
            return

        self._compare_btn.configure(state="disabled", text=t("cmp.comparing"))
        for w in self._scroll.winfo_children():
            w.destroy()

        ctk.CTkLabel(self._scroll, text=t("cmp.running"),
                     text_color=TEXT_MUTED).grid(row=0, column=0, pady=30)

        def _worker():
            results = compare_extractions(dir_a, dir_b)
            self.after(0, lambda: self._show_results(results))

        threading.Thread(target=_worker, daemon=True).start()

    def _show_results(self, results: dict):
        self._results = results
        self._compare_btn.configure(state="normal", text=t("cmp.compare"))

        for w in self._scroll.winfo_children():
            w.destroy()

        if not results:
            ctk.CTkLabel(self._scroll,
                         text=t("cmp.no_data"),
                         text_color=TEXT_MUTED, wraplength=500,
                         justify="center").grid(row=0, column=0, pady=40)
            return

        # Summary at top
        summary_card = ctk.CTkFrame(self._scroll, fg_color=BG_CARD, corner_radius=10)
        summary_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        summary_card.grid_columnconfigure((0, 1, 2, 3), weight=1)

        total_new = total_removed = total_changed = 0
        for fname, item in results.items():
            diff = item["diff"]
            total_new     += len(diff.get("new", []))
            total_removed += len(diff.get("removed", []))
            total_changed += len(diff.get("changed", []))

        for col, (label, count, color) in enumerate([
            (t("cmp.new"),          total_new,     CLR_NEW),
            (t("cmp.removed"),      total_removed, CLR_REMOVED),
            (t("cmp.changed"),      total_changed, CLR_CHANGED),
            (t("cmp.files_compared"), len(results), self.accent),
        ]):
            f = ctk.CTkFrame(summary_card, fg_color="transparent")
            f.grid(row=0, column=col, padx=15, pady=15)
            ctk.CTkLabel(f, text=str(count), font=ctk.CTkFont(size=26, weight="bold"),
                         text_color=color).pack()
            ctk.CTkLabel(f, text=label, font=ctk.CTkFont(size=11),
                         text_color=TEXT_MUTED).pack()

        # Per-file detail
        for row_idx, (fname, item) in enumerate(results.items(), start=1):
            self._build_diff_card(self._scroll, row_idx, fname, item)

    def _build_diff_card(self, parent, row_idx: int, fname: str, item: dict):
        diff      = item["diff"]
        label_key = item.get("label_key", fname)
        label     = t(label_key)

        n_new     = len(diff.get("new", []))
        n_removed = len(diff.get("removed", []))
        n_changed = len(diff.get("changed", []))
        n_fields  = len(diff.get("fields", {}))

        card = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=10)
        card.grid(row=row_idx, column=0, sticky="ew", pady=4)
        card.grid_columnconfigure(0, weight=1)

        # Header
        hdr = ctk.CTkFrame(card, fg_color=BG_PANEL, corner_radius=8)
        hdr.grid(row=0, column=0, sticky="ew", padx=2, pady=2)
        hdr.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(hdr, text=label,
                     font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, padx=15, pady=10, sticky="w"
        )

        badges = ctk.CTkFrame(hdr, fg_color="transparent")
        badges.grid(row=0, column=2, padx=15)

        def _badge(parent, text, color):
            f = ctk.CTkFrame(parent, fg_color=_tint(color), corner_radius=6)
            f.pack(side="left", padx=3)
            ctk.CTkLabel(f, text=text, text_color=color,
                         font=ctk.CTkFont(size=11, weight="bold")).pack(padx=8, pady=3)

        if n_new:
            _badge(badges, f"+{n_new} {t('cmp.new').lower()}", CLR_NEW)
        if n_removed:
            _badge(badges, f"-{n_removed} {t('cmp.removed').lower()}", CLR_REMOVED)
        if n_changed:
            _badge(badges, f"~{n_changed} {t('cmp.changed').lower()}", CLR_CHANGED)
        if n_fields:
            _badge(badges, f"{n_fields} {t('cmp.changed').lower()}", CLR_CHANGED)
        if not (n_new or n_removed or n_changed or n_fields):
            _badge(badges, t("cmp.no_changes"), CLR_SAME)

        # Detailed diff (expandable)
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        body.grid_columnconfigure(0, weight=1)

        # "fields" diff (for device_info)
        if "fields" in diff and diff["fields"]:
            rows = [[t("cmp.col_field"), t("cmp.col_backup_a"), t("cmp.col_backup_b")]]
            for field, vals in diff["fields"].items():
                rows.append([field, str(vals["before"]), str(vals["after"])])
            self._render_table(body, rows, 0)

        # New records
        if diff.get("new"):
            self._render_section(body, t("cmp.new"), diff["new"], CLR_NEW,
                                 len(diff.get("fields", {})) > 0)

        # Removed
        if diff.get("removed"):
            offset = (1 if diff.get("new") else 0) + (1 if diff.get("fields") else 0)
            self._render_section(body, t("cmp.removed"), diff["removed"], CLR_REMOVED, offset > 0)

        # Changed
        if diff.get("changed"):
            for ci, change in enumerate(diff["changed"][:20]):
                self._render_change(body, change, ci)

    def _render_section(self, parent, title: str, records: list, color: str, spacer: bool):
        offset = len(parent.grid_slaves())
        if spacer:
            ctk.CTkFrame(parent, height=8, fg_color="transparent").grid(
                row=offset, column=0, sticky="ew"
            )
            offset += 1

        ctk.CTkLabel(parent, text=title, text_color=color,
                     font=ctk.CTkFont(size=11, weight="bold")).grid(
            row=offset, column=0, sticky="w", pady=(4, 2)
        )

        if not records:
            return

        keys = list(records[0].keys()) if isinstance(records[0], dict) else [t("res.value_col")]
        rows = [keys]
        for rec in records[:30]:
            if isinstance(rec, dict):
                rows.append([str(rec.get(k, "")) for k in keys])
            else:
                rows.append([str(rec)])

        self._render_table(parent, rows, offset + 1)

        if len(records) > 30:
            ctk.CTkLabel(parent,
                         text=t("res.more").format(n=len(records) - 30),
                         text_color=TEXT_MUTED,
                         font=ctk.CTkFont(size=10)).grid(
                row=offset + 2, column=0, sticky="w"
            )

    def _render_change(self, parent, change: dict, idx: int):
        offset = len(parent.grid_slaves())

        before = change.get("before", {})
        after  = change.get("after", {})
        all_keys = list(dict.fromkeys(list(before.keys()) + list(after.keys())))

        rows = [[t("cmp.col_field"), t("cmp.col_before"), t("cmp.col_after")]]
        for k in all_keys:
            vb, va = str(before.get(k, "—")), str(after.get(k, "—"))
            if vb != va:
                rows.append([k, vb, va])

        if len(rows) > 1:
            ctk.CTkLabel(parent, text=t("cmp.change_n").format(n=idx + 1),
                         font=ctk.CTkFont(size=10), text_color=CLR_CHANGED).grid(
                row=offset, column=0, sticky="w", pady=(4, 1)
            )
            self._render_table(parent, rows, offset + 1)

    def _render_table(self, parent, rows: list, base_row: int, col_widths=None):
        """Render a simple grid as a table."""
        frame = ctk.CTkFrame(parent, fg_color="#0d1117", corner_radius=6)
        frame.grid(row=base_row, column=0, sticky="ew", pady=2)

        for ci, header in enumerate(rows[0]):
            ctk.CTkLabel(frame, text=header,
                         font=ctk.CTkFont(size=10, weight="bold"),
                         text_color=self.accent, anchor="w").grid(
                row=0, column=ci, padx=8, pady=5, sticky="w"
            )

        for ri, row in enumerate(rows[1:], start=1):
            bg = "#16213e" if ri % 2 == 0 else "transparent"
            for ci, cell in enumerate(row):
                ctk.CTkLabel(frame, text=str(cell)[:80],
                             font=ctk.CTkFont(size=10),
                             text_color="#cccccc",
                             anchor="w").grid(
                    row=ri, column=ci, padx=8, pady=3, sticky="w"
                )

        for ci in range(len(rows[0])):
            frame.grid_columnconfigure(ci, weight=1)

    # ── Language refresh ──────────────────────────────────────────────────────

    def refresh_lang(self):
        self._label_a.configure(text=t("cmp.backup_a"))
        self._label_b.configure(text=t("cmp.backup_b"))
        self._browse_a_btn.configure(text=t("cmp.browse"))
        self._browse_b_btn.configure(text=t("cmp.browse"))
        self._compare_btn.configure(text=t("cmp.compare"))
        self._placeholder.configure(text=t("cmp.placeholder"))
        # Re-render results if available
        if self._results:
            self._show_results(self._results)
