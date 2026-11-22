"""
core/smartswitch.py — Samsung Smart Switch (PC / external storage) backups.

Own implementation from the publicly documented format (no third-party code):

  * backupHistoryInfo.xml in the backup root holds a <Dummy> element: hex of an
    AES-128-ECB ciphertext under a key built into Smart Switch. Decrypted (trailing NULs
    removed) it is a hex string; the file key is SHA-256(that string as UTF-8)[:16].
  * Data files are AES-128-CBC: the first 16 bytes are the IV, the rest the ciphertext,
    PKCS#7 padding (not always present). Files that already are ZIP / SQLite / media /
    text are stored unencrypted.
  * Categories are folders (CONTACT, CALLLOG, GMMESSAGE, WIFICONFIG, SBROWSER, APKFILE,
    SAMSUNGNOTE, Photo, Video, …). Many hold an encrypted ZIP wrapper (CALLLOG.zip,
    Contact.spbm, SBROWSER.zip, …) whose members may be encrypted again.

decrypt_backup() writes a readable mirror of the backup (decrypted files, unpacked
wrappers) into a folder; the parsers below read call logs (.exml), Google Messages
(bugle) backup databases, Wi-Fi configuration, the app list and Samsung Internet data.

Backups protected with a user password / PIN use a different (password-based) scheme
and are not supported – they are reported as such.

Times are converted to local time strings like the rest of the backup importer.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

MARKER = "backupHistoryInfo.xml"
# Key Smart Switch uses for the <Dummy> value (an interoperability constant of the format).
_DUMMY_KEY = b"0b1e96db05d64ea4"

_MAX_DECRYPT = 512 << 20          # larger unknown files are copied as they are
_ZIP_DEPTH = 4
# ZIP-based formats that are documents/apps, not Smart Switch wrappers – kept as files
_NO_UNZIP = {".apk", ".jar", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".epub",
             ".xps", ".kmz", ".aar"}


class SmartSwitchError(Exception):
    pass


# ── Crypto ───────────────────────────────────────────────────────────────────

def _aes(key: bytes, mode, data: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
    dec = Cipher(algorithms.AES(key), mode).decryptor()
    return dec.update(data) + dec.finalize()


def session_key_from_dummy(dummy_hex: str) -> bytes:
    """16-byte file key from the <Dummy> value of backupHistoryInfo.xml."""
    from cryptography.hazmat.primitives.ciphers import modes
    s = re.sub(r"\s+", "", dummy_hex or "")
    try:
        ct = bytes.fromhex(s)
    except ValueError:
        raise SmartSwitchError("Dummy is not hex")
    if not ct or len(ct) % 16:
        raise SmartSwitchError("Dummy has a wrong length")
    plain = _aes(_DUMMY_KEY, modes.ECB(), ct).rstrip(b"\0")
    try:
        text = plain.decode("ascii")
    except UnicodeDecodeError:
        raise SmartSwitchError("Dummy does not decrypt to text")
    if not re.fullmatch(r"[0-9a-fA-F]{32,128}", text) or len(text) % 2:
        raise SmartSwitchError("Dummy does not decrypt to a session value")
    return hashlib.sha256(text.encode("utf-8")).digest()[:16]


def decrypt_blob(key: bytes, data: bytes) -> Optional[bytes]:
    """AES-128-CBC with the IV in front; PKCS#7 removed when valid. None if too short."""
    from cryptography.hazmat.primitives.ciphers import modes
    if len(data) < 32:
        return None
    iv, body = data[:16], data[16:]
    body = body[:len(body) // 16 * 16]
    out = _aes(key, modes.CBC(iv), body)
    n = out[-1] if out else 0
    if 1 <= n <= 16 and out[-n:] == bytes([n]) * n:
        out = out[:-n]
    return out


# ── Content sniffing ─────────────────────────────────────────────────────────

def sniff(head: bytes) -> str:
    """zip / sqlite / media / text / '' (unknown – maybe encrypted)."""
    if head[:4] == b"PK\x03\x04" or head[:4] == b"PK\x05\x06":
        return "zip"
    if head[:16] == b"SQLite format 3\x00":
        return "sqlite"
    if (head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n" or head[:4] == b"GIF8"
            or head[:4] == b"%PDF" or head[4:8] == b"ftyp" or head[:3] == b"ID3"
            or (head[:4] == b"RIFF" and head[8:12] in (b"WEBP", b"WAVE", b"AVI "))
            or head[:4] == b"OggS" or head[:5] == b"#!AMR" or head[:4] == b"fLaC"
            or head[:2] == b"BM" and len(head) > 26 and head[6:10] == b"\0\0\0\0"):
        return "media"
    if head and _is_text(head):
        return "text"
    return ""


def _is_text(head: bytes) -> bool:
    sample = head[:4096]
    if sample.startswith(b"\xef\xbb\xbf"):
        sample = sample[3:]
    if b"\0" in sample:
        return False
    try:
        s = sample.decode("utf-8")
    except UnicodeDecodeError as e:
        if e.start < len(sample) - 4:     # a cut multi-byte char at the end is fine
            return False
        s = sample[:e.start].decode("utf-8", errors="ignore")
    if not s:
        return False
    bad = sum(1 for c in s if ord(c) < 32 and c not in "\r\n\t\f")
    return bad <= len(s) * 0.01


# ── Backup structure ─────────────────────────────────────────────────────────

def find_roots(path: str | Path, max_depth: int = 4) -> list[Path]:
    """Backup roots (folders with backupHistoryInfo.xml) at or below *path*."""
    p = Path(path)
    if not p.is_dir():
        return []
    out = []
    base_depth = len(p.parts)
    for dirpath, dirs, files in os.walk(p):
        if len(Path(dirpath).parts) - base_depth > max_depth:
            dirs[:] = []
            continue
        if any(f.lower() == MARKER.lower() for f in files):
            out.append(Path(dirpath))
            dirs[:] = []
    return out


def read_dummy(root: Path) -> str:
    """Text of the single <Dummy> element of backupHistoryInfo.xml ('' if missing)."""
    xml = next((root / f for f in os.listdir(root) if f.lower() == MARKER.lower()), None)
    if xml is None:
        return ""
    try:
        tree = ET.parse(str(xml))
    except ET.ParseError:
        data = xml.read_bytes()
        m = re.search(rb"<(?:\w+:)?Dummy[^>]*>\s*([0-9A-Fa-f\s]+?)\s*</", data)
        return m.group(1).decode() if m else ""
    vals = [(e.text or "").strip() for e in tree.iter()
            if e.tag.rsplit("}", 1)[-1] == "Dummy" and (e.text or "").strip()]
    return vals[0] if len(vals) == 1 else ""


def backup_info(root: Path) -> dict:
    """Device / backup details from backupHistoryInfo.xml (best effort, flat text elements)."""
    info: dict = {}
    xml = next((root / f for f in os.listdir(root) if f.lower() == MARKER.lower()), None)
    if xml is None:
        return info
    try:
        for e in ET.parse(str(xml)).iter():
            tag = e.tag.rsplit("}", 1)[-1]
            txt = (e.text or "").strip()
            if txt and not len(e) and tag != "Dummy" and tag not in info and len(txt) < 200:
                info[tag] = txt
    except ET.ParseError:
        pass
    return info


# ── Decryption of the whole backup ───────────────────────────────────────────

class _Stats(dict):
    def add(self, k, n=1):
        self[k] = self.get(k, 0) + n


def decrypt_backup(root: Path, out_dir: Path, log: Callable[[str], None] = lambda m: None,
                   stopped: Callable[[], bool] = lambda: False,
                   copy_media: bool = False) -> dict:
    """Write a readable mirror of the backup *root* into *out_dir*.

    Encrypted files are decrypted, ZIP wrappers unpacked into '<name>.unzipped/'.
    Plain media files are only listed (not copied) unless copy_media is set – they stay
    readable in the original backup. Returns statistics incl. the list of undecoded files.
    """
    dummy = read_dummy(root)
    if not dummy:
        raise SmartSwitchError("no Dummy in backupHistoryInfo.xml")
    key = session_key_from_dummy(dummy)
    out_dir.mkdir(parents=True, exist_ok=True)
    st = _Stats(undecoded_files=[], media_files=[])
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if stopped():
                return st
            src = Path(dirpath) / name
            rel = src.relative_to(root)
            try:
                size = src.stat().st_size
                with open(src, "rb") as f:
                    head = f.read(4096)
            except OSError:
                st.add("errors")
                continue
            kind = sniff(head)
            if kind == "media" and not copy_media:
                st["media_files"].append(str(src))
                st.add("media")
                continue
            if kind or size > _MAX_DECRYPT:
                if kind == "zip" and size <= _MAX_DECRYPT:
                    _emit(src.read_bytes(), out_dir / rel, key, st, 0)
                else:
                    (out_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, out_dir / rel)
                    st.add("plain" if kind else "undecoded")
                    if not kind:
                        st["undecoded_files"].append(str(rel))
                continue
            _emit(src.read_bytes(), out_dir / rel, key, st, 0)
    log(f"  Smart Switch: {st.get('decrypted', 0)} decrypted, {st.get('plain', 0)} plain, "
        f"{st.get('unzipped', 0)} archives unpacked, {st.get('undecoded', 0)} undecoded")
    return st


def _emit(data: bytes, dest: Path, key: bytes, st: _Stats, depth: int) -> None:
    kind = sniff(data[:4096])
    if kind:
        st.add("plain")
    else:
        dec = decrypt_blob(key, data)
        dkind = sniff(dec[:4096]) if dec else ""
        if not dkind and dec and dec.endswith(b"\0"):
            # zero-padded instead of PKCS#7: trailing NULs are padding only for text payloads
            # (binary formats such as SQLite are block-aligned and keep their zeros)
            stripped = dec.rstrip(b"\0")
            if sniff(stripped[:4096]) == "text":
                dec, dkind = stripped, "text"
        if dkind:
            st.add("decrypted")
            data, kind = dec, dkind
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            st.add("undecoded")
            st["undecoded_files"].append(str(dest))
            return
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    if kind == "zip" and depth < _ZIP_DEPTH and dest.suffix.lower() not in _NO_UNZIP:
        _unzip(data, dest.with_name(dest.name + ".unzipped"), key, st, depth + 1)


def _unzip(data: bytes, folder: Path, key: bytes, st: _Stats, depth: int) -> None:
    import io
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return
    st.add("unzipped")
    with z:
        for info in z.infolist():
            if info.is_dir() or info.file_size > _MAX_DECRYPT:
                continue
            parts = [p for p in PurePosixPath(info.filename.replace("\\", "/")).parts
                     if p not in ("", ".", "..") and not p.endswith(":")]
            if not parts:
                continue
            try:
                member = z.read(info)
            except Exception:
                st.add("errors")
                continue
            _emit(member, folder.joinpath(*parts), key, st, depth)


# ── Parsers ──────────────────────────────────────────────────────────────────

def _ts(ms) -> str:
    try:
        v = int(float(ms))
    except (TypeError, ValueError):
        return ""
    if v <= 0:
        return ""
    if v > 10 ** 12:
        v //= 1000
    try:
        return datetime.fromtimestamp(v).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


CALL_TYPES = {1: "incoming", 2: "outgoing", 3: "missed", 4: "voicemail",
              5: "rejected", 6: "blocked", 7: "answered_externally"}


def calls_from_exml(path: Path) -> list[dict]:
    """<CallLogs><CallLog><number/><date/><duration/><type/><name/>… (values as child
    elements or attributes)."""
    try:
        root = ET.parse(str(path)).getroot()
    except (ET.ParseError, OSError):
        return []
    out = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1].lower() != "calllog":
            continue
        f = {k.lower(): v for k, v in el.attrib.items()}
        for c in el:
            f[c.tag.rsplit("}", 1)[-1].lower()] = (c.text or "").strip()
        t = f.get("type", "")
        out.append({
            "number": f.get("number", ""),
            "name": f.get("name", ""),
            "date": _ts(f.get("date")),
            "duration": (f.get("duration") or "0") + " s",
            "type": CALL_TYPES.get(int(t), t) if t.lstrip("-").isdigit() else t,
        })
    return out


def is_bugle_backup(db: Path) -> bool:
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    return {"messages_backup", "parts_backup", "participants_backup",
            "conversation_participants_backup"} <= names


def _cols(conn, table) -> set:
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def sms_from_bugle(db: Path) -> list[dict]:
    """Messages from a Google Messages backup database (Smart Switch category GMMESSAGE).

    Direction from message_status (1–99 outgoing, 100–199 incoming, ≥200 system → skipped);
    the other party = participants of the conversation that are not 'self'; group
    conversations list all other parties. Text = text parts joined; other parts are
    listed as attachments (content type)."""
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    conn.row_factory = sqlite3.Row
    out = []
    try:
        pcols = _cols(conn, "participants_backup")
        mcols = _cols(conn, "messages_backup")
        partcols = _cols(conn, "parts_backup")
        self_expr = "is_self" if "is_self" in pcols else ("(sub_id != -2)" if "sub_id" in pcols else "0")
        people = {}
        for r in conn.execute(f"SELECT * , {self_expr} AS _self FROM participants_backup"):
            d = dict(r)
            num = next((d.get(k) for k in ("send_destination", "normalized_destination",
                                            "display_destination") if d.get(k)), "")
            people[d["_id"]] = {"self": bool(d.get("_self")), "number": str(num or ""),
                                "name": d.get("full_name") or ""}
        conv: dict = {}
        for r in conn.execute("SELECT conversation_id, participant_id FROM conversation_participants_backup"):
            conv.setdefault(r[0], []).append(r[1])
        texts: dict = {}
        atts: dict = {}
        ctype = "content_type" if "content_type" in partcols else "NULL"
        for r in conn.execute(f"SELECT message_id, text, {ctype} AS ct FROM parts_backup ORDER BY _id"):
            if r["text"]:
                texts.setdefault(r["message_id"], []).append(str(r["text"]))
            elif r["ct"] and not str(r["ct"]).startswith("text/"):
                atts.setdefault(r["message_id"], []).append(str(r["ct"]))
        sel = ["_id", "conversation_id", "message_status"]
        sel += [c for c in ("received_timestamp", "sent_timestamp", "read", "protocol")
                if c in mcols]
        for r in conn.execute(f"SELECT {', '.join(sel)} FROM messages_backup ORDER BY _id"):
            d = dict(r)
            status = d.get("message_status")
            try:
                status = int(status)
            except (TypeError, ValueError):
                continue
            if status <= 0 or status >= 200:
                continue
            others = [people[p] for p in conv.get(d["conversation_id"], [])
                      if p in people and not people[p]["self"]]
            if status == 3:
                kind = "draft"
            elif status in (8, 9):
                kind = "failed"
            elif status < 100:
                kind = "outgoing"
            else:
                kind = "incoming"
            rec = {
                "address": ", ".join(o["number"] for o in others if o["number"]),
                "name": ", ".join(o["name"] for o in others if o["name"]),
                "date": _ts(d.get("received_timestamp") or d.get("sent_timestamp")),
                "type": kind,
                "body": "\n".join(texts.get(d["_id"], [])),
                "read": bool(d.get("read")),
            }
            if d.get("sent_timestamp") and kind != "incoming":
                rec["date_sent"] = _ts(d["sent_timestamp"])
            if len(others) > 1:
                rec["group"] = True
            if d.get("protocol"):
                rec["protocol"] = {0: "SMS", 1: "MMS", 2: "RCS"}.get(d["protocol"], str(d["protocol"]))
            if atts.get(d["_id"]):
                rec["attachments"] = ", ".join(atts[d["_id"]])
            out.append(rec)
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return out


def wifi_from_text(path: Path) -> list[dict]:
    """WIFICONFIG payload: wpa_supplicant-style 'network={…}' blocks, optionally followed
    by Android WifiConfigStore XML. Passwords are included (psk / wep_key)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    nets = []
    for block in re.findall(r"network\s*=\s*\{(.*?)\n\s*\}", text, re.S):
        f = dict(re.findall(r"^\s*(\w+)\s*=\s*(.*?)\s*$", block, re.M))
        ssid = _wpa_value(f.get("ssid", ""))
        if not ssid:
            continue
        nets.append({"ssid": ssid, "bssid": "", "type": f.get("key_mgmt", ""),
                     "password": _wpa_value(f.get("psk", "") or f.get("wep_key0", "")),
                     "hidden": f.get("scan_ssid") == "1", "status": "saved"})
    i = text.find("<")
    xml_part = text[i:] if i >= 0 and "<Network" in text[i:] else ""
    if xml_part:
        try:
            root = ET.fromstring(xml_part[: xml_part.rfind(">") + 1])
            for net in root.iter("Network"):
                vals = {e.get("name"): (e.text or "") for e in net.iter("string")}
                ssid = vals.get("SSID", "").strip('"')
                if ssid and not any(n["ssid"] == ssid for n in nets):
                    nets.append({"ssid": ssid, "bssid": "", "type": vals.get("KeyMgmt", ""),
                                 "password": vals.get("PreSharedKey", "").strip('"'),
                                 "status": "saved"})
        except ET.ParseError:
            pass
    return nets


def _wpa_value(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return v[1:-1]
    if re.fullmatch(r"(?:[0-9a-fA-F]{2})+", v) and len(v) != 64:   # hex SSID, not a raw PSK
        try:
            return bytes.fromhex(v).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return v
    return v


def apps_from_applist(path: Path) -> list[dict]:
    """APKFILE/AppList.json: {"Apks": [{ApkPkgName, ApkName, VersionName, InstPkgName,
    LastTimeUsed}]}."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, ValueError):
        return []
    items = data.get("Apks") if isinstance(data, dict) else data
    out = []
    for a in items or []:
        if not isinstance(a, dict) or not a.get("ApkPkgName"):
            continue
        out.append({"package": a["ApkPkgName"], "name": a.get("ApkName", ""),
                    "version": a.get("VersionName", ""), "installer": a.get("InstPkgName", ""),
                    "last_used": _ts(a.get("LastTimeUsed")), "code_path": "", "installed": ""})
    return out


def sbrowser_bookmarks(db: Path) -> list[dict]:
    """Samsung Internet SBrowser.db – BOOKMARKS (not deleted, not folders)."""
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    out = []
    try:
        cols = {c.upper() for c in _cols(conn, "BOOKMARKS")}
        if not {"URL", "TITLE"} <= cols:
            return []
        where = []
        if "DELETED" in cols:
            where.append("COALESCE(DELETED,0)=0")
        if "FOLDER" in cols:
            where.append("COALESCE(FOLDER,0)=0")
        sql = "SELECT URL, TITLE FROM BOOKMARKS" + (" WHERE " + " AND ".join(where) if where else "")
        for url, title in conn.execute(sql):
            if url:
                out.append({"type": "bookmark", "url": url, "title": title or "",
                            "visits": "", "last_visit": ""})
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return out
