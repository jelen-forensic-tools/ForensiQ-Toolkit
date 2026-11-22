"""
core/leapp.py — run ALEAPP / iLEAPP (Alexis Brignoni, MIT licence) as external tools and
import their results into a ForenziQ case.

The tools are NOT bundled. The examiner points ForenziQ at aleapp.exe / ileapp.exe (the
portable release binary) or at aleapp.py / ileapp.py of a source checkout. We run it with:

    <tool> -t <type> -i <input> -o <case>/_leapp --custom_output_folder <TOOL>_<timestamp>

and then convert the report into <case>/aleapp.json or <case>/ileapp.json:

    {"tool": "ALEAPP", "version": ..., "report": <folder>, "input": ..., "times": "UTC",
     "<Category> / <Artifact>": [ {header: value, ...}, ... ], ...}

The JSON file lives in the root of the case folder, so the search index picks it up
(each artifact becomes one section) and its records can be searched, tagged and reported.

Sources of the import, in order of preference:
  * _lava_artifacts.db + _lava_data.lava  (typed: datetime columns are UTC Unix seconds)
  * "_TSV Exports/*.tsv"                   (fallback for older versions)

Notes on the tools' behaviour (as of v2026.4.x):
  * the exit code is 0 even when processing failed → success is read from the log
    ("Processes completed." / "Report generation Completed.");
  * an existing report folder is an argparse error (exit 2) → we always use a new name;
  * an encrypted iTunes backup without --itunes_password makes iLEAPP ask on stdin →
    stdin is closed so it fails instead of hanging; iLEAPP writes the password to its log.
"""
from __future__ import annotations

import csv
import datetime as dt
import glob
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
from typing import Callable, Optional

TOOLS = {
    "aleapp": {"name": "ALEAPP", "types": ["fs", "tar", "zip", "gz"], "json": "aleapp.json"},
    "ileapp": {"name": "iLEAPP", "types": ["fs", "tar", "zip", "gz", "itunes"], "json": "ileapp.json"},
}

OUT_SUBDIR = "_leapp"
_OK_MARKERS = ("Processes completed.", "Report generation Completed.")
_MAX_ROWS_PER_ARTIFACT = 200_000


# ── Tool location ────────────────────────────────────────────────────────────

def tool_command(path: str) -> list[str]:
    """Command prefix that starts the tool at *path* (.exe, or .py run by a Python found
    in PATH – never sys.executable, which is ForenziQ itself in the frozen build)."""
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(path or "-")
    if path.lower().endswith(".py"):
        py = _find_python()
        if not py:
            raise FileNotFoundError("python")
        return [py, path]
    return [path]


_PE_MACHINES = {0x8664: "x64", 0xAA64: "arm64", 0x014C: "x86"}


def exe_arch(path: str) -> str:
    """CPU architecture of a Windows .exe from its PE header ('' when unknown / not an exe)."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
        if head[:2] != b"MZ":
            return ""
        pe = int.from_bytes(head[0x3C:0x40], "little")
        if head[pe:pe + 4] != b"PE\0\0":
            return ""
        return _PE_MACHINES.get(int.from_bytes(head[pe + 4:pe + 6], "little"), "")
    except (OSError, ValueError):
        return ""


def host_arch() -> str:
    """Architecture of this Windows machine (x64 / arm64 / x86)."""
    a = (os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or "").upper()
    return {"AMD64": "x64", "ARM64": "arm64", "X86": "x86"}.get(a, "")


def arch_mismatch(path: str) -> tuple[str, str]:
    """(exe arch, machine arch) when the .exe cannot run here, else ('', '').
    x64 Windows runs x86 binaries; ARM64 Windows emulates x64 and x86."""
    if os.name != "nt" or not path.lower().endswith(".exe"):
        return "", ""
    exe, host = exe_arch(path), host_arch()
    if not exe or not host or exe == host:
        return "", ""
    ok = {"x64": {"x86"}, "arm64": {"x64", "x86"}, "x86": set()}
    return ("", "") if exe in ok.get(host, set()) else (exe, host)


def _find_python() -> str:
    """A real Python interpreter from PATH (the WindowsApps 'python' alias only opens the
    Store and would hang a background run)."""
    for name in ("py", "python", "python3"):
        p = shutil.which(name)
        if p and "windowsapps" not in p.lower():
            return p
    if not getattr(sys, "frozen", False) and sys.executable:
        return sys.executable
    return ""


def find_tool(tool: str, configured: str = "") -> str:
    """Configured path if valid, else <tool>.exe / <tool>.py found in PATH or next to ForenziQ."""
    if configured and os.path.isfile(configured):
        return configured
    for name in (tool + ".exe", tool):
        p = shutil.which(name)
        if p:
            return p
    base = os.path.dirname(sys.executable if getattr(sys, "frozen", False)
                           else os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.path.join(base, "tools", tool, tool + ".exe"),
                 os.path.join(base, tool, tool + ".exe"),
                 os.path.join(base, tool + ".exe")):
        if os.path.isfile(cand):
            return cand
    return ""


# ── Input ────────────────────────────────────────────────────────────────────

def is_itunes_backup(path: str) -> bool:
    return os.path.isdir(path) and any(os.path.isfile(os.path.join(path, n))
                                       for n in ("Manifest.db", "Manifest.mbdb"))


def itunes_encrypted(path: str) -> bool:
    try:
        import plistlib
        with open(os.path.join(path, "Manifest.plist"), "rb") as f:
            return bool(plistlib.load(f).get("IsEncrypted"))
    except Exception:
        return False


def detect_input_type(path: str, tool: str) -> str:
    """fs / tar / zip / gz / itunes for an input file or folder ('' if unsupported)."""
    if os.path.isdir(path):
        if tool == "ileapp" and is_itunes_backup(path):
            return "itunes"
        return "fs"
    low = path.lower()
    if low.endswith(".zip"):
        return "zip"
    if low.endswith((".tar.gz", ".tgz", ".gz")):
        return "gz"
    if low.endswith(".tar"):
        return "tar"
    return ""


def guess_tool(path: str) -> str:
    """ileapp for iTunes backups / iOS-looking inputs, aleapp otherwise."""
    if is_itunes_backup(path):
        return "ileapp"
    low = os.path.basename(path).lower()
    if any(k in low for k in ("ios", "iphone", "ipad", "itunes", "ffs")):
        return "ileapp"
    return "aleapp"


def case_inputs(case_dir: str) -> list[tuple[str, str]]:
    """Inputs found in a ForenziQ case folder: [(tool, path)] – root backup archive,
    user data tree, extracted ADB backup."""
    out = []
    if not case_dir or not os.path.isdir(case_dir):
        return out
    for name, tool in (("root_backup.tar.gz", "aleapp"), ("root_backup_extracted", "aleapp"),
                       ("adb_backup_extracted", "aleapp"), ("user_data_tree", "aleapp")):
        p = os.path.join(case_dir, name)
        if os.path.isfile(p) or (os.path.isdir(p) and _non_empty(p)):
            out.append((tool, p))
    return out


def _non_empty(folder: str) -> bool:
    for _root, _dirs, files in os.walk(folder):
        if files:
            return True
    return False


# ── Run ──────────────────────────────────────────────────────────────────────

class LeappRun:
    """One run of ALEAPP / iLEAPP followed by the import into the case."""

    def __init__(self, tool: str, tool_path: str, input_path: str, input_type: str,
                 case_dir: str, itunes_password: str = ""):
        if tool not in TOOLS:
            raise ValueError(tool)
        self.tool = tool
        self.tool_path = tool_path
        self.input_path = os.path.abspath(input_path)
        self.input_type = input_type
        self.case_dir = os.path.abspath(case_dir)
        self.itunes_password = itunes_password
        self.report_dir = ""
        self._proc: Optional[subprocess.Popen] = None
        self._stopped = False

    def command(self) -> list[str]:
        out_base = os.path.join(self.case_dir, OUT_SUBDIR)
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{TOOLS[self.tool]['name']}_{ts}"
        n = 1
        while os.path.exists(os.path.join(out_base, name)):
            n += 1
            name = f"{TOOLS[self.tool]['name']}_{ts}_{n}"
        self.report_dir = os.path.join(out_base, name)
        cmd = tool_command(self.tool_path) + [
            "-t", self.input_type, "-i", self.input_path, "-o", out_base,
            "--custom_output_folder", name]
        if self.tool == "ileapp" and self.input_type == "itunes" and self.itunes_password:
            cmd += ["--itunes_password", self.itunes_password]
        return cmd

    def masked(self, cmd: list[str]) -> str:
        """Command line for the log, password hidden."""
        out, hide = [], False
        for a in cmd:
            out.append("********" if hide else (f'"{a}"' if " " in a else a))
            hide = a == "--itunes_password"
        return " ".join(out)

    def stop(self):
        self._stopped = True
        p = self._proc
        if p and p.poll() is None:
            try:
                if os.name == "nt":       # the PyInstaller binary runs a child process
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)],
                                   capture_output=True, creationflags=_NO_WINDOW)
                else:
                    p.kill()
            except Exception:
                pass

    def run(self, log: Callable[[str], None], done: Callable[[bool, str, dict], None]):
        """Run in a background thread; done(ok, message, summary)."""
        threading.Thread(target=self._worker, args=(log, done), daemon=True).start()

    def _worker(self, log, done):
        try:
            ok, msg, summary = self._run(log)
        except Exception as e:
            ok, msg, summary = False, str(e), {}
        done(ok, msg, summary)

    def _run(self, log) -> tuple[bool, str, dict]:
        os.makedirs(os.path.join(self.case_dir, OUT_SUBDIR), exist_ok=True)
        cmd = self.command()
        log("$ " + self.masked(cmd))
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        self._proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=os.path.join(self.case_dir, OUT_SUBDIR), env=env,
            creationflags=_NO_WINDOW)
        seen = set()
        for raw in iter(self._proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            if self.itunes_password:
                line = line.replace(self.itunes_password, "********")
            for m in _OK_MARKERS:
                if m in line:
                    seen.add(m)
            if line:
                log(line)
        code = self._proc.wait()
        if self._stopped:
            return False, "stopped", {}
        if not os.path.isdir(self.report_dir):
            return False, f"exit {code}: no report folder", {}
        if not seen:
            seen.update(_log_markers(self.report_dir))
        summary = import_report(self.tool, self.report_dir, self.case_dir,
                                input_path=self.input_path, input_type=self.input_type)
        _scrub_password(self.report_dir, self.itunes_password)
        if len(seen) < len(_OK_MARKERS):
            return False, f"exit {code}: processing did not complete", summary
        return True, "ok", summary


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _log_markers(report_dir: str) -> set:
    found = set()
    for p in glob.glob(os.path.join(report_dir, "**", "Screen_Output.html"), recursive=True) + \
             glob.glob(os.path.join(report_dir, "**", "Screen Output.html"), recursive=True):
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        found.update(m for m in _OK_MARKERS if m in text)
    return found


def _scrub_password(report_dir: str, password: str) -> None:
    """iLEAPP logs the iTunes backup password in plain text – mask it in the report logs."""
    if not password:
        return
    for p in glob.glob(os.path.join(report_dir, "**", "*.html"), recursive=True) + \
             glob.glob(os.path.join(report_dir, "**", "*.txt"), recursive=True):
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
            if password in text:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(text.replace(password, "********"))
        except OSError:
            pass


# ── Import of the report ─────────────────────────────────────────────────────

def find_report_root(path: str) -> str:
    """The report folder itself (contains _lava_artifacts.db or '_TSV Exports'), searched
    up to two levels down so the examiner may pick the parent output folder too."""
    def is_root(p):
        return (os.path.isfile(os.path.join(p, "_lava_artifacts.db"))
                or os.path.isdir(os.path.join(p, "_TSV Exports")))
    if is_root(path):
        return path
    cands = []
    for pat in ("*", os.path.join("*", "*")):
        cands += [p for p in glob.glob(os.path.join(path, pat)) if os.path.isdir(p) and is_root(p)]
    return max(cands, key=os.path.getmtime) if cands else ""


def tool_of_report(report_dir: str) -> str:
    lava = os.path.join(report_dir, "_lava_data.lava")
    try:
        name = json.load(open(lava, encoding="utf-8")).get("parser_info", {}).get("leapp_name", "")
        if name.lower() in TOOLS:
            return name.lower()
    except (OSError, ValueError, AttributeError):
        pass
    return "ileapp" if "ileapp" in os.path.basename(report_dir).lower() else "aleapp"


def _fmt_ts(v) -> str:
    try:
        return dt.datetime.fromtimestamp(int(v), dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError, OSError, TypeError):
        return str(v)


def _clean(v):
    if isinstance(v, bytes):
        return v.hex()
    return v


def read_lava(report_dir: str) -> tuple[dict, dict]:
    """({section: [records]}, parser_info) from the LAVA output; ({}, {}) if absent."""
    db = os.path.join(report_dir, "_lava_artifacts.db")
    meta_path = os.path.join(report_dir, "_lava_data.lava")
    if not (os.path.isfile(db) and os.path.isfile(meta_path)):
        return {}, {}
    try:
        meta = json.load(open(meta_path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}, {}
    arts = meta.get("artifacts") or {}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        # A media cell holds a reference id (_lava_media_references.id) → media item →
        # source_path = the original file (relative to the input). Item ids are accepted too.
        media = {}
        try:
            for r in conn.execute("SELECT id, source_path, extraction_path FROM _lava_media_items"):
                media[str(r[0])] = r[1] or r[2] or ""
            for r in conn.execute("SELECT id, media_item_id FROM _lava_media_references"):
                if str(r[1]) in media:
                    media[str(r[0])] = media[str(r[1])]
        except sqlite3.Error:
            pass
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        out: dict[str, list] = {}
        for category, items in (arts.items() if isinstance(arts, dict) else []):
            if isinstance(items, dict):
                items = list(items.values())
            for a in items or []:
                if not isinstance(a, dict):
                    continue
                table = a.get("tablename")
                if table not in tables:
                    continue
                cmap = a.get("column_map") or {}
                kinds = {}
                for oc in a.get("object_columns") or []:
                    if isinstance(oc, dict):
                        kinds[oc.get("name")] = oc.get("type")
                cur = conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid LIMIT ?',
                                   (_MAX_ROWS_PER_ARTIFACT,))
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur:
                    rec = {}
                    for c, v in zip(cols, r):
                        if v is None or v == "":
                            continue
                        k = kinds.get(c)
                        if k == "datetime" and isinstance(v, (int, float)):
                            v = _fmt_ts(v)
                        elif k == "media":
                            v = _media_value(v, media)
                        rec[cmap.get(c, c)] = _clean(v)
                    if rec:
                        rows.append(rec)
                if rows:
                    name = a.get("name") or table
                    out[_section_name(out, category, name)] = rows
        return out, meta.get("parser_info") or {}
    finally:
        conn.close()


def _media_value(v, media: dict):
    if isinstance(v, str) and v.startswith("["):
        try:
            ids = json.loads(v)
            return [media.get(str(i), str(i)) for i in ids]
        except ValueError:
            return v
    return media.get(str(v), v)


def _section_name(out: dict, category: str, name: str) -> str:
    base = f"{category} / {name}" if category and category != name else name
    s, n = base, 2
    while s in out:
        s, n = f"{base} ({n})", n + 1
    return s


def read_tsv(report_dir: str) -> dict:
    """{section: [records]} from '_TSV Exports' (header row repeated by appends is skipped)."""
    out: dict[str, list] = {}
    folder = os.path.join(report_dir, "_TSV Exports")
    csv.field_size_limit(min(sys.maxsize, 2 ** 31 - 1))
    for path in sorted(glob.glob(os.path.join(folder, "*.tsv"))):
        name = os.path.splitext(os.path.basename(path))[0]
        if name.startswith("_"):
            continue
        try:
            with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
                reader = csv.reader(f, delimiter="\t")
                header = next(reader, None)
                if not header:
                    continue
                rows = []
                for r in reader:
                    if r == header or not any(r):
                        continue
                    rec = {h: v for h, v in zip(header, r) if v != ""}
                    if rec:
                        rows.append(rec)
                    if len(rows) >= _MAX_ROWS_PER_ARTIFACT:
                        break
        except OSError:
            continue
        if rows:
            out[_section_name(out, "", name)] = rows
    return out


def import_report(tool: str, report_dir: str, case_dir: str, input_path: str = "",
                  input_type: str = "") -> dict:
    """Write <case>/<tool>.json from a report folder; returns a summary
    {tool, file, report, source: lava|tsv, artifacts: [(section, count)], records}."""
    root = find_report_root(report_dir) or report_dir
    sections, info = read_lava(root)
    source = "lava"
    if not sections:
        sections, source = read_tsv(root), "tsv"
    doc = {
        "tool": info.get("leapp_name") or TOOLS[tool]["name"],
        "version": info.get("leapp_version", ""),
        "report": root,
        "input": input_path,
        "input_type": input_type,
        "imported": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "times": "UTC",
    }
    doc.update(sections)
    fname = TOOLS[tool]["json"]
    with open(os.path.join(case_dir, fname), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1, default=str)
    arts = [(k, len(v)) for k, v in sections.items()]
    return {"tool": doc["tool"], "file": fname, "report": root, "source": source,
            "artifacts": arts, "records": sum(n for _k, n in arts),
            "html": html_index(root)}


def html_index(report_dir: str) -> str:
    for p in (os.path.join(report_dir, "index.html"),
              os.path.join(report_dir, "_HTML", "index.html")):
        if os.path.isfile(p):
            return p
    return ""


def load_imported(case_dir: str, tool: str) -> tuple[dict, dict]:
    """(meta, {section: [records]}) of a previously imported <tool>.json."""
    p = os.path.join(case_dir, TOOLS[tool]["json"])
    try:
        data = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return {}, {}
    meta = {k: v for k, v in data.items() if not isinstance(v, list)}
    secs = {k: v for k, v in data.items() if isinstance(v, list)}
    return meta, secs
