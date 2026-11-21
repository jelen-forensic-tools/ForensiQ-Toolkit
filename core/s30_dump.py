"""
core/s30_dump.py — Nokia Series 30+ (Spreadtrum SC6531E / MOCOR) flash-dump analysis.

Thin wrapper around the bundled SC6531E Dump Analyzer (core/s30/): runs the full
analysis (contacts, SMS, call log, FAT12 files, PDF report, CSV/vCard exports) and
additionally writes ForenziQ-compatible JSON files (sms.json, call_logs.json,
file_analysis.json) so the result can be opened in the Timeline panel.

The dump is only read, never modified.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Callable, Optional

from .contacts_xlsx import write_name_numbers_xlsx
from .i18n import get_lang
from .s30 import i18n as s30_i18n
from .s30.analyzer import normalize_number
from .s30.pipeline import run_analysis

S30_OPTIONS = ("contacts", "sms", "calls", "files", "xlsx", "disk", "pdf", "thumbs")
DUMP_FILETYPES = [("Flash dump", "*.bin *.img *.dump *.nor *.raw"), ("*", "*.*")]

_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif"}


def _write_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_forenziq_json(an, target: str, options: dict) -> list[str]:
    """Write timeline-compatible JSON files; returns list of written paths."""
    written = []
    lookup = an.contact_lookup()

    if options.get("contacts") and an.contacts:
        p = os.path.join(target, "contacts.json")
        _write_json(p, [{"name": c["name"], "number": c["number"], "index": c["index"],
                         "nv_id": c["nv_id"], "offset": c["offset"]} for c in an.contacts])
        written.append(p)

    if options.get("sms") and an.sms:
        rows = []
        for x in an.sms_messages():
            rows.append({
                "date": x["time"],
                "direction": s30_i18n.t("val.dir." + x["direction"]),
                "address": x["number"],
                "contact": lookup.get(normalize_number(x["number"]), ""),
                "body": x["text"],
                "parts": x.get("parts", ""),
                "source": s30_i18n.t("val.src.sms." + x["source"]),
                "nv_id": x["nv_id"],
                "offset": x["offset"],
            })
        p = os.path.join(target, "sms.json")
        _write_json(p, rows)
        written.append(p)

    if options.get("calls") and an.calls:
        p = os.path.join(target, "call_logs.json")
        _write_json(p, [{
            "date": c["time"],
            "number": c["number"],
            "contact": lookup.get(normalize_number(c["number"]), ""),
            "duration": c["duration_s"],
            "type": c["type_code"],
            "source": s30_i18n.t("val.src.call." + c["source"]),
            "nv_id": c["nv_id"],
            "offset": c["offset"],
        } for c in an.calls])
        written.append(p)

    if options.get("files") and an.files:
        files = []
        for f in an.files:
            ext = os.path.splitext(f["path"])[1].lower()
            files.append({
                "filename": f["path"].rsplit("/", 1)[-1],
                "path": f["path"],
                "size": f["size"],
                "modified": f["date"],
                "deleted": f["deleted"],
                "status": f["status"],
                "type": "image" if ext in _IMAGE_EXT else (ext.lstrip(".") or "unknown"),
            })
        p = os.path.join(target, "file_analysis.json")
        _write_json(p, {"files": files})
        written.append(p)

    return written


def group_contacts(contacts: list[dict]) -> list[tuple[str, list[str]]]:
    """Merge phonebook slots into contacts: [(name, [number, ...]), ...].

    Same rule as the vCard export: consecutive slots with the same name are one
    contact with several numbers (MOCOR stores each extra number in the next slot).
    """
    out: list[tuple[str, list[str]]] = []
    prev = None
    for c in contacts:
        if prev and prev["name"] == c["name"] and c["index"] == prev["index"] + 1:
            nums = out[-1][1]
            if c["number"] and c["number"] not in nums:
                nums.append(c["number"])
        else:
            out.append((c["name"], [c["number"]] if c["number"] else []))
        prev = c
    return out


def write_contacts_xlsx(contacts: list[dict], path: str) -> str:
    """Excel sheet with name + numbers only; extra numbers go into the next columns."""
    return write_name_numbers_xlsx(group_contacts(contacts), path)


def summary(an) -> dict:
    """Key counts for the GUI summary."""
    return {
        "contacts": len(an.contacts),
        "sms": len(an.sms_messages()) if an.sms else 0,
        "sms_recovered": sum(1 for x in an.sms if x["source"] != "current"),
        "calls": len(an.calls),
        "calls_recovered": sum(1 for c in an.calls if c["source"] != "current"),
        "files": sum(1 for f in an.files if not f["deleted"]),
        "files_deleted": sum(1 for f in an.files if f["deleted"]),
        "device": an.info.get("platform") or an.info.get("mmi") or "",
        "supported": bool(an.info.get("supported")),
        "sha256": an.info.get("sha256", ""),
        "warnings": an.warning_texts(),
    }


def run_s30_analysis(
    dump: str,
    out_root: str,
    options: Optional[dict] = None,
    case: Optional[dict] = None,
    on_log: Optional[Callable[[str], None]] = None,
    on_progress: Optional[Callable[[int], None]] = None,
    on_done: Optional[Callable[[Optional[dict], str, str], None]] = None,
) -> threading.Thread:
    """Run the analysis in a background thread.

    on_done(summary_or_None, target_folder, error_message) is called at the end.
    Output language follows the current ForenziQ language.
    """
    opts = {k: True for k in S30_OPTIONS}
    opts.update(options or {})
    log = on_log or (lambda m: None)

    def _worker():
        s30_i18n.set_lang(get_lang() if get_lang() in s30_i18n.LANGS else "en")
        try:
            an, target = run_analysis(dump, out_root, options=opts, case=case,
                                      log=log, progress=on_progress)
            for p in write_forenziq_json(an, target, opts):
                log(s30_i18n.t("log.saved", file=os.path.basename(p)))
            if opts.get("xlsx") and opts.get("contacts") and an.contacts:
                p = write_contacts_xlsx(an.contacts, os.path.join(
                    target, s30_i18n.t("fn.contacts") + ".xlsx"))
                log(s30_i18n.t("log.saved", file=os.path.basename(p)))
            res = summary(an)
        except Exception as e:      # report any failure to the GUI instead of dying silently
            if on_done:
                on_done(None, "", f"{type(e).__name__}: {e}")
            return
        if on_done:
            on_done(res, target, "")

    th = threading.Thread(target=_worker, daemon=True)
    th.start()
    return th
