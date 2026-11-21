"""
core/evidence.py – Evidence integrity & chain-of-custody.

After an extraction/import, hashes every produced file (MD5 + SHA-256),
records case metadata (case number, examiner, device, tool version, timestamp)
and writes a tamper-evident manifest. The manifest can be re-verified later to
detect any modification, deletion or addition of evidence files.

Pure standard library. For authorised forensic use.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
from typing import Callable, Optional

TOOL_NAME = "ForenziQ"
TOOL_VERSION = "1.0"

MANIFEST_NAME = "evidence_manifest.json"
MANIFEST_TXT = "evidence_manifest.txt"

# Outputs of this step itself – never hashed into the manifest.
_SELF_FILES = {MANIFEST_NAME, MANIFEST_TXT}
# Pracovní produkt analytika (index hledání, štítky, poznámky) – vzniká až po
# zajištění dat a mění se během analýzy, proto nepatří do manifestu důkazů.
_SELF_DIRS = ("_case",)

_CHUNK = 1 << 20  # 1 MiB


def _hash_file(path: str) -> tuple[int, str, str]:
    """Return (size_bytes, md5_hex, sha256_hex) for a file, streamed."""
    md5 = hashlib.md5()
    sha = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while True:
            block = f.read(_CHUNK)
            if not block:
                break
            size += len(block)
            md5.update(block)
            sha.update(block)
    return size, md5.hexdigest(), sha.hexdigest()


def _iter_files(root: str):
    """Yield (abs_path, rel_path) for every file under root, excluding the manifest."""
    for dirpath, dirs, files in os.walk(root):
        if os.path.normcase(os.path.abspath(dirpath)) == os.path.normcase(os.path.abspath(root)):
            dirs[:] = [d for d in dirs if d not in _SELF_DIRS]
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if rel in _SELF_FILES:
                continue
            yield full, rel


def generate_manifest(
    output_dir: str,
    case_meta: Optional[dict] = None,
    device_info: Optional[dict] = None,
    log: Optional[Callable[[str], None]] = None,
) -> tuple[str, dict]:
    """
    Hash every file in output_dir and write the evidence manifest.
    Returns (manifest_path, manifest_dict).
    """
    case_meta = case_meta or {}
    entries: list[dict] = []
    combined = hashlib.sha256()

    for full, rel in sorted(_iter_files(output_dir), key=lambda pr: pr[1]):
        try:
            size, md5, sha = _hash_file(full)
        except OSError as e:
            if log:
                log(f"  ! {rel}: {e}")
            continue
        mtime = datetime.datetime.fromtimestamp(
            os.path.getmtime(full)
        ).isoformat(timespec="seconds")
        entries.append({
            "path": rel, "size": size,
            "md5": md5, "sha256": sha, "modified": mtime,
        })
        combined.update(sha.encode("ascii"))
        if log:
            log(f"  ✓ {rel}")

    manifest = {
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "case": {k: v for k, v in case_meta.items() if v},
        "device": _trim_device(device_info),
        "file_count": len(entries),
        "total_bytes": sum(e["size"] for e in entries),
        "aggregate_sha256": combined.hexdigest(),
        "files": entries,
    }

    path = os.path.join(output_dir, MANIFEST_NAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    _write_txt(output_dir, manifest)
    return path, manifest


def verify_manifest(
    output_dir: str,
    manifest_path: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Re-hash the files in output_dir and compare against a stored manifest.
    Returns a report dict with lists: ok / modified / missing / added,
    plus `aggregate_match` (bool) and `manifest` metadata.
    """
    manifest_path = manifest_path or os.path.join(output_dir, MANIFEST_NAME)
    if not os.path.isfile(manifest_path):
        return {"error": "no_manifest", "manifest_path": manifest_path}
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError) as e:
        return {"error": "bad_manifest", "detail": str(e)}

    recorded = {e["path"]: e for e in manifest.get("files", [])}
    ok, modified, missing, added = [], [], [], []
    combined = hashlib.sha256()

    # present-on-disk files
    on_disk = {rel: full for full, rel in _iter_files(output_dir)}

    for rel, entry in sorted(recorded.items()):
        full = on_disk.get(rel)
        if full is None:
            missing.append(rel)
            if log:
                log(f"  ✗ {rel} (missing)")
            continue
        try:
            _size, _md5, sha = _hash_file(full)
        except OSError as e:
            modified.append(rel)
            if log:
                log(f"  ! {rel}: {e}")
            continue
        if sha == entry.get("sha256"):
            ok.append(rel)
            combined.update(sha.encode("ascii"))
        else:
            modified.append(rel)
            if log:
                log(f"  ✗ {rel} (modified)")

    for rel in sorted(on_disk):
        if rel not in recorded:
            added.append(rel)
            if log:
                log(f"  + {rel} (added)")

    # aggregate matches only when every recorded file is present & unchanged
    aggregate_match = (
        not modified and not missing
        and combined.hexdigest() == manifest.get("aggregate_sha256")
    )

    return {
        "manifest": {k: manifest.get(k) for k in ("tool", "generated", "case", "device")},
        "ok": ok,
        "modified": modified,
        "missing": missing,
        "added": added,
        "aggregate_match": aggregate_match,
        "intact": aggregate_match and not added,
    }


def _trim_device(device_info: Optional[dict]) -> dict:
    """Keep only identifying device fields in the manifest record."""
    if not device_info:
        return {}
    keep = ("manufacturer", "model", "brand", "serial", "imei",
            "android", "device_name", "ios_version")
    return {k: device_info[k] for k in keep if device_info.get(k)}


def _write_txt(output_dir: str, manifest: dict) -> str:
    """Write a human-readable companion to the JSON manifest."""
    lines: list[str] = []
    tool = manifest.get("tool", {})
    lines.append("=" * 70)
    lines.append(f"  {tool.get('name', TOOL_NAME)} {tool.get('version', '')} — EVIDENCE MANIFEST")
    lines.append("=" * 70)
    lines.append(f"Generated : {manifest.get('generated', '')}")

    case = manifest.get("case", {})
    if case:
        lines.append("")
        lines.append("CASE")
        for k, v in case.items():
            lines.append(f"  {k:<16}: {v}")

    device = manifest.get("device", {})
    if device:
        lines.append("")
        lines.append("DEVICE")
        for k, v in device.items():
            lines.append(f"  {k:<16}: {v}")

    lines.append("")
    lines.append(f"Files           : {manifest.get('file_count', 0)}")
    lines.append(f"Total size      : {manifest.get('total_bytes', 0):,} B")
    lines.append(f"Aggregate SHA256: {manifest.get('aggregate_sha256', '')}")
    lines.append("")
    lines.append("-" * 70)
    lines.append(f"{'SHA-256':<64}  SIZE  PATH")
    lines.append("-" * 70)
    for e in manifest.get("files", []):
        lines.append(f"{e.get('sha256', ''):<64}  {e.get('size', 0)}  {e.get('path', '')}")

    path = os.path.join(output_dir, MANIFEST_TXT)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path
