"""
core/case_tags.py — tagging of records, audit trail and keyword lists.

Tags live in <output_dir>/_case/tags.sqlite next to the search index. A tagged
item stores a copy of the record (JSON), its stable row_key, the source file name
and the SHA-256 of that source file at tagging time, so a tagged-only report can
be produced even without the index and every item can be traced to its source.

Every change (tag, untag, note, keyword list run, report) is appended to
<output_dir>/_case/audit.jsonl with time and examiner – an append-only trail.

Keyword lists are case-independent and stored as plain text files (one term per
line, "#" starts a comment) in ~/.forenziq/keyword_lists/, so they can be reused
across cases and shared between examiners.

The _case/ folder is analyst work product and is excluded from the evidence manifest.
"""
from __future__ import annotations

import datetime as dt
import getpass
import json
import os
import re
import sqlite3
from typing import Iterable, Optional

from . import search_index as si

TAGS_NAME = "tags.sqlite"
AUDIT_NAME = "audit.jsonl"

# (i18n key, colour) of the tags created for a new case
DEFAULT_TAGS = [("tag.default.evidence", "#e53935"),
                ("tag.default.suspicious", "#fb8c00"),
                ("tag.default.review", "#1e88e5")]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tags(
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL COLLATE NOCASE,
    color TEXT NOT NULL DEFAULT '#1e88e5',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tagged(
    row_key TEXT NOT NULL,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    file TEXT NOT NULL,
    section TEXT NOT NULL DEFAULT '',
    idx INTEGER NOT NULL DEFAULT 0,
    dt TEXT,
    record_json TEXT NOT NULL,
    source_sha256 TEXT,
    note TEXT NOT NULL DEFAULT '',
    examiner TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY(row_key, tag_id)
);
CREATE INDEX IF NOT EXISTS ix_tagged_tag ON tagged(tag_id);
"""


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def current_examiner() -> str:
    """Examiner name from Settings, else the OS user name."""
    try:
        from . import settings
        name = settings.load().get("report_examiner", "")
    except Exception:
        name = ""
    if not name:
        try:
            name = getpass.getuser()
        except Exception:
            name = ""
    return name


def audit(output_dir: str, action: str, **details) -> None:
    """Append one JSON line to the case audit trail."""
    os.makedirs(si.case_dir(output_dir), exist_ok=True)
    entry = {"time": _now(), "examiner": current_examiner(), "action": action, **details}
    with open(os.path.join(si.case_dir(output_dir), AUDIT_NAME), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class CaseTags:
    """Tags of one case (output folder)."""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        os.makedirs(si.case_dir(output_dir), exist_ok=True)
        self.conn = sqlite3.connect(os.path.join(si.case_dir(output_dir), TAGS_NAME),
                                    check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(_SCHEMA)
        if not self.conn.execute("SELECT 1 FROM tags LIMIT 1").fetchone():
            from .i18n import t
            with self.conn:
                for key, color in DEFAULT_TAGS:
                    self.conn.execute("INSERT OR IGNORE INTO tags(name, color, created_at) "
                                      "VALUES (?, ?, ?)", (t(key), color, _now()))

    def close(self):
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ── tags ────────────────────────────────────────────────────────────────

    def list_tags(self) -> list[dict]:
        """[{id, name, color, count}] ordered by creation."""
        return [dict(r) for r in self.conn.execute(
            "SELECT t.id, t.name, t.color, COUNT(x.row_key) AS count FROM tags t "
            "LEFT JOIN tagged x ON x.tag_id = t.id GROUP BY t.id ORDER BY t.id")]

    def add_tag(self, name: str, color: str = "#1e88e5") -> int:
        name = name.strip()
        if not name:
            raise ValueError("empty tag name")
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO tags(name, color, created_at) VALUES (?, ?, ?)",
                              (name, color, _now()))
        audit(self.output_dir, "tag_created", tag=name)
        return self._tag_id(name)

    def delete_tag(self, name: str) -> int:
        """Delete a tag and its assignments; returns the number of removed assignments."""
        tid = self._tag_id(name)
        if tid is None:
            return 0
        n = self.conn.execute("SELECT COUNT(*) FROM tagged WHERE tag_id = ?", (tid,)).fetchone()[0]
        with self.conn:
            self.conn.execute("DELETE FROM tagged WHERE tag_id = ?", (tid,))
            self.conn.execute("DELETE FROM tags WHERE id = ?", (tid,))
        audit(self.output_dir, "tag_deleted", tag=name, removed=n)
        return n

    def _tag_id(self, name: str) -> Optional[int]:
        r = self.conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        return r[0] if r else None

    # ── assignments ─────────────────────────────────────────────────────────

    def _source_hashes(self) -> dict[str, str]:
        """SHA-256 of the source JSON files as recorded by the search index."""
        try:
            conn = sqlite3.connect(si.index_path(self.output_dir))
            try:
                return {r[0]: r[1] for r in conn.execute("SELECT name, sha256 FROM files")}
            finally:
                conn.close()
        except sqlite3.Error:
            return {}

    def tag(self, rows: Iterable[dict], tag_name: str, note: str = "",
            reason: str = "") -> int:
        """Tag search rows ({row_key, file, section, idx, dt, record}); returns newly tagged count."""
        tid = self._tag_id(tag_name)
        if tid is None:
            tid = self.add_tag(tag_name)
        hashes = self._source_hashes()
        examiner = current_examiner()
        now = _now()
        added = 0
        keys = []
        with self.conn:
            for r in rows:
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO tagged(row_key, tag_id, file, section, idx, dt, "
                    "record_json, source_sha256, note, examiner, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (r["row_key"], tid, r["file"], r.get("section") or "", int(r.get("idx") or 0),
                     r.get("dt"), json.dumps(r.get("record", {}), ensure_ascii=False, default=str),
                     hashes.get(r["file"]), note, examiner, now))
                if cur.rowcount:
                    added += 1
                    keys.append(r["row_key"])
        if added:
            audit(self.output_dir, "tagged", tag=tag_name, count=added,
                  items=keys[:1000], reason=reason or None)
        return added

    def untag(self, row_keys: Iterable[str], tag_name: Optional[str] = None) -> int:
        """Remove a tag (or all tags when tag_name is None) from records."""
        keys = list(row_keys)
        if not keys:
            return 0
        removed = 0
        with self.conn:
            for k in keys:
                if tag_name is None:
                    cur = self.conn.execute("DELETE FROM tagged WHERE row_key = ?", (k,))
                else:
                    cur = self.conn.execute(
                        "DELETE FROM tagged WHERE row_key = ? AND tag_id = "
                        "(SELECT id FROM tags WHERE name = ?)", (k, tag_name))
                removed += cur.rowcount
        if removed:
            audit(self.output_dir, "untagged", tag=tag_name or "*", count=removed, items=keys[:1000])
        return removed

    def set_note(self, row_key: str, note: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE tagged SET note = ? WHERE row_key = ?", (note, row_key))
        audit(self.output_dir, "note", item=row_key, note=note)

    def tags_for(self, row_keys: Iterable[str]) -> dict[str, list[str]]:
        """{row_key: [tag names]} for the given records (only tagged ones are present)."""
        keys = list(dict.fromkeys(row_keys))
        out: dict[str, list[str]] = {}
        for i in range(0, len(keys), 500):
            part = keys[i:i + 500]
            q = ("SELECT x.row_key, t.name FROM tagged x JOIN tags t ON t.id = x.tag_id "
                 "WHERE x.row_key IN (%s) ORDER BY t.id" % ",".join("?" * len(part)))
            for k, name in self.conn.execute(q, part):
                out.setdefault(k, []).append(name)
        return out

    def tagged_items(self, tag_name: Optional[str] = None) -> list[dict]:
        """Tagged records (one entry per record with all its tags) in time order."""
        sql = ("SELECT x.row_key, x.file, x.section, x.idx, x.dt, x.record_json, x.source_sha256, "
               "x.note, x.examiner, x.created_at, t.name AS tag FROM tagged x "
               "JOIN tags t ON t.id = x.tag_id ")
        params: list = []
        if tag_name:
            sql += ("WHERE x.row_key IN (SELECT row_key FROM tagged WHERE tag_id = "
                    "(SELECT id FROM tags WHERE name = ?)) ")
            params.append(tag_name)
        sql += "ORDER BY x.dt IS NULL, x.dt, x.file, x.idx, t.id"
        items: dict[str, dict] = {}
        for r in self.conn.execute(sql, params):
            it = items.get(r["row_key"])
            if it is None:
                try:
                    rec = json.loads(r["record_json"])
                except ValueError:
                    rec = {}
                it = items[r["row_key"]] = {
                    "row_key": r["row_key"], "file": r["file"], "section": r["section"],
                    "idx": r["idx"], "dt": r["dt"], "record": rec,
                    "source_sha256": r["source_sha256"], "tags": [], "notes": [],
                    "examiner": r["examiner"], "tagged_at": r["created_at"], "snippet": "",
                }
            it["tags"].append(r["tag"])
            if r["note"] and r["note"] not in it["notes"]:
                it["notes"].append(r["note"])
        return list(items.values())

    def count_tagged(self) -> int:
        return self.conn.execute("SELECT COUNT(DISTINCT row_key) FROM tagged").fetchone()[0]


# ── Keyword lists ────────────────────────────────────────────────────────────

def keyword_dir() -> str:
    d = os.path.join(os.path.expanduser("~"), ".forenziq", "keyword_lists")
    os.makedirs(d, exist_ok=True)
    return d


def _safe_name(name: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return s[:80] or "list"


def list_keyword_lists() -> list[str]:
    return sorted(os.path.splitext(f)[0] for f in os.listdir(keyword_dir())
                  if f.lower().endswith(".txt"))


def load_keyword_list(name: str) -> list[str]:
    path = os.path.join(keyword_dir(), _safe_name(name) + ".txt")
    if not os.path.isfile(path):
        return []
    return parse_keywords(open(path, encoding="utf-8-sig").read())


def keyword_list_text(name: str) -> str:
    path = os.path.join(keyword_dir(), _safe_name(name) + ".txt")
    return open(path, encoding="utf-8-sig").read() if os.path.isfile(path) else ""


def save_keyword_list(name: str, text: str) -> str:
    name = _safe_name(name)
    with open(os.path.join(keyword_dir(), name + ".txt"), "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")
    return name


def delete_keyword_list(name: str) -> None:
    path = os.path.join(keyword_dir(), _safe_name(name) + ".txt")
    if os.path.isfile(path):
        os.remove(path)


def parse_keywords(text: str) -> list[str]:
    """One term per line; lines starting with '#' and blank lines are ignored; duplicates
    removed. A '#' inside a term is kept (e.g. hashtags or USSD codes like *#06#)."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        if s and s not in out:
            out.append(s)
    return out


def run_keywords(output_dir: str, terms: list[str], limit_per_term: int = 5000,
                 cancel=lambda: False) -> tuple[list[dict], list[dict]]:
    """Search every term in the case index.

    Returns (per_term, rows): per_term = [{term, count}] in list order; rows = unique
    matching records (search rows) with an extra "keywords" list of the terms that hit them.
    """
    per_term: list[dict] = []
    by_key: dict[str, dict] = {}
    with si.SearchIndex(output_dir) as ix:
        for term in terms:
            if cancel():
                break
            try:
                total = ix.count(term)
                rows = ix.search(term, limit=limit_per_term) if total else []
            except sqlite3.Error:
                total, rows = 0, []
            per_term.append({"term": term, "count": total})
            for r in rows:
                cur = by_key.get(r["row_key"])
                if cur is None:
                    r["keywords"] = [term]
                    by_key[r["row_key"]] = r
                elif term not in cur["keywords"]:
                    cur["keywords"].append(term)
    rows = sorted(by_key.values(), key=lambda r: (r["dt"] is None, r["dt"] or "", r["file"]))
    return per_term, rows


# ── Context for reports ──────────────────────────────────────────────────────

def load_case_context(output_dir: str) -> tuple[dict, dict]:
    """(device_info, case_meta) for an output folder: device_info.json (Android) or
    ios_device_info.json, case details from the evidence manifest if present."""
    device: dict = {}
    for name in ("device_info.json", "ios_device_info.json"):
        p = os.path.join(output_dir, name)
        if os.path.isfile(p):
            try:
                data = json.load(open(p, encoding="utf-8"))
                if isinstance(data, dict):
                    device = {k: v for k, v in data.items() if not isinstance(v, (dict, list))}
                    if name.startswith("ios") and "android" not in device:
                        device["android"] = data.get("ios_version") or data.get("ProductVersion", "N/A")
                    break
            except (OSError, ValueError):
                pass
    case: dict = {}
    p = os.path.join(output_dir, "evidence_manifest.json")
    if os.path.isfile(p):
        try:
            case = json.load(open(p, encoding="utf-8")).get("case", {}) or {}
        except (OSError, ValueError):
            pass
    return device, case
