"""
core/ios_backup_parser.py — Parses iTunes/Finder iOS backups (unencrypted and encrypted).

Backup folder structure:
  <root>/<UDID>/
    Manifest.db     – SQLite index  (fileID, domain, relativePath)
    Manifest.plist  – metadata      (encryption flag, device info)
    Info.plist      – device info
    Status.plist    – backup status
    XX/XXXXXXX…     – actual files  (first 2 chars = subfolder name)
"""
from __future__ import annotations

import os
import json
import shutil
import sqlite3
import plistlib
import tempfile
import threading
import datetime
from pathlib import Path
from typing import Callable, Optional

from .i18n import t, has

# Default iTunes backup locations on Windows
_BACKUP_DIRS = [
    os.path.expandvars(r"%APPDATA%\Apple Computer\MobileSync\Backup"),
    os.path.expandvars(r"%USERPROFILE%\Apple\MobileSync\Backup"),
]

IOS_BACKUP_MODULES = {
    "contacts":          "Contacts",
    "messages":          "Messages (iMessage / SMS)",
    "sms_attachments":   "Message Attachments",
    "call_logs":         "Call History",
    "notes":             "Notes",
    "calendar":          "Calendar",
    "safari_history":    "Safari History",
    "photos":            "Photos",
    "photos_gps":        "Photos with GPS",
    "voicemail":         "Voicemail",
    "wifi_networks":     "Wi-Fi Networks",
    "apps_installed":    "Installed Apps",
    "app_usage":         "App Usage",
    "whatsapp":          "WhatsApp Messages",
    "whatsapp_contacts": "WhatsApp Contacts",
    "viber":             "Viber Messages",
}


# ── Backup discovery ───────────────────────────────────────────────────────

def find_itunes_backups() -> list[dict]:
    """Find all iTunes/Finder backups on this Windows system."""
    backups: list[dict] = []
    for base in _BACKUP_DIRS:
        if not os.path.isdir(base):
            continue
        for udid in os.listdir(base):
            path = os.path.join(base, udid)
            if not os.path.isfile(os.path.join(path, "Manifest.db")):
                continue
            entry: dict = {"path": path, "udid": udid,
                           "device_name": udid, "product": "",
                           "ios_version": "", "last_backup": "",
                           "serial": "", "encrypted": False,
                           "was_passcode_set": None}
            # Info.plist – device info
            info_plist = os.path.join(path, "Info.plist")
            if os.path.isfile(info_plist):
                try:
                    with open(info_plist, "rb") as f:
                        info = plistlib.load(f)
                    entry.update({
                        "device_name": info.get("Device Name", udid),
                        "product":     info.get("Product Type", ""),
                        "ios_version": info.get("Product Version", ""),
                        "last_backup": str(info.get("Last Backup Date", "")),
                        "serial":      info.get("Serial Number", ""),
                    })
                except Exception:
                    pass
            # Manifest.plist – encryption flag
            manifest_plist = os.path.join(path, "Manifest.plist")
            if os.path.isfile(manifest_plist):
                try:
                    with open(manifest_plist, "rb") as f:
                        mf = plistlib.load(f)
                    entry["encrypted"] = bool(mf.get("IsEncrypted", False))
                    if "WasPasscodeSet" in mf:
                        entry["was_passcode_set"] = bool(mf.get("WasPasscodeSet"))
                    else:
                        ld = mf.get("Lockdown") or {}
                        if isinstance(ld, dict) and "PasswordProtected" in ld:
                            entry["was_passcode_set"] = bool(ld.get("PasswordProtected"))
                except Exception:
                    pass
            backups.append(entry)
    return backups


# ── Result object ──────────────────────────────────────────────────────────

class iOSBackupResult:
    @property
    def label(self) -> str:
        k = f"ios_bkp.mod.{self.module}"
        return t(k) if has(k) else IOS_BACKUP_MODULES.get(self.module, self.module)

    def __init__(self, module: str):
        self.module = module
        self.success = False
        self.data: list = []
        self.error = ""
        self.file_path = ""
        self.count = 0


# ── Parser ─────────────────────────────────────────────────────────────────

class _WrongPassword(Exception):
    pass


class iOSBackupParser:
    """Parses iTunes/Finder iOS backups. Šifrované zálohy dešifruje heslem
    zálohy (core.ios_backup_crypto)."""

    def __init__(self, backup_path: str, output_dir: str, password: str = ""):
        self.backup_path = backup_path
        self.output_dir = output_dir
        self.password = password or ""
        self._stop_event = threading.Event()
        self._on_log: Optional[Callable] = None
        self._crypto = None          # EncryptedBackup instance pro šifrované zálohy
        self._dec_dir: Optional[str] = None   # temp složka s dešifrovanými soubory
        self._dec_cache: dict = {}   # fileID → cesta k dešifrovanému souboru

    def stop(self):
        self._stop_event.set()

    def _stopped(self) -> bool:
        return self._stop_event.is_set()

    def _log(self, msg: str):
        if self._on_log:
            self._on_log(msg)

    # ── Info ───────────────────────────────────────────────────────────────

    def get_device_info(self) -> dict:
        info_path = os.path.join(self.backup_path, "Info.plist")
        if not os.path.isfile(info_path):
            return {}
        try:
            with open(info_path, "rb") as f:
                info = plistlib.load(f)
            out = {
                "device_name":     info.get("Device Name", ""),
                "model":           info.get("Product Type", ""),
                "ios_version":     info.get("Product Version", ""),
                "serial":          info.get("Serial Number", ""),
                "imei":            info.get("IMEI", ""),
                "phone":           info.get("Phone Number", ""),
                "guid":            info.get("GUID", ""),
                "last_backup":     str(info.get("Last Backup Date", "")),
                "itunes_version":  info.get("iTunes Version", ""),
            }
            # Manifest.plist → šifrování + zda mělo zařízení nastavený zámek
            try:
                with open(os.path.join(self.backup_path, "Manifest.plist"), "rb") as f:
                    mf = plistlib.load(f)
                out["encrypted"] = bool(mf.get("IsEncrypted", False))
                if "WasPasscodeSet" in mf:
                    out["was_passcode_set"] = bool(mf.get("WasPasscodeSet"))
            except Exception:
                pass
            return out
        except Exception as e:
            return {"error": str(e)}

    def is_encrypted(self) -> bool:
        try:
            with open(os.path.join(self.backup_path, "Manifest.plist"), "rb") as f:
                mf = plistlib.load(f)
            return bool(mf.get("IsEncrypted", False))
        except Exception:
            return False

    # ── Main run ───────────────────────────────────────────────────────────

    def run(
        self,
        modules: list[str],
        on_progress: Callable[[str, int, int], None],
        on_done: Callable,
        on_module_done: Callable = None,
        on_log: Callable = None,
    ):
        self._on_log = on_log
        self._stop_event.clear()

        def _worker():
            os.makedirs(self.output_dir, exist_ok=True)

            manifest_db = os.path.join(self.backup_path, "Manifest.db")
            if not os.path.isfile(manifest_db):
                on_done([], t("iosx.no_manifest"))
                return

            # Šifrovaná záloha → dešifruj Manifest.db heslem zálohy
            if self.is_encrypted():
                if not self.password:
                    on_done([], t("iosx.need_password"))
                    return
                try:
                    manifest_db = self._init_decryption(manifest_db)
                except _WrongPassword:
                    on_done([], t("iosx.wrong_password"))
                    return
                except Exception as e:
                    on_done([], t("iosx.decrypt_fail").format(e=e))
                    return

            results: list[iOSBackupResult] = []
            total = len(modules)
            elapsed_list: list[float] = []

            for i, module in enumerate(modules, 1):
                if self._stopped():
                    break
                label = t(f"ios_bkp.mod.{module}") if has(f"ios_bkp.mod.{module}") else IOS_BACKUP_MODULES.get(module, module)
                on_progress(label, i, total)

                t0 = datetime.datetime.now()
                result = self._extract_module(module, manifest_db)
                elapsed = (datetime.datetime.now() - t0).total_seconds()
                elapsed_list.append(elapsed)
                avg = sum(elapsed_list) / len(elapsed_list)

                results.append(result)
                if on_module_done:
                    on_module_done(result, i, total, elapsed, avg)

            self._cleanup_decryption()
            on_done(results, "")

        threading.Thread(target=_worker, daemon=True).start()

    # ── Dešifrování šifrované zálohy ─────────────────────────────────────────

    def _init_decryption(self, manifest_db: str) -> str:
        """Odemkne keybag heslem, dešifruje Manifest.db do temp a vrátí jeho cestu."""
        from .ios_backup_crypto import EncryptedBackup
        self._crypto = EncryptedBackup(self.backup_path, self.password)
        if not self._crypto.password_ok:
            raise _WrongPassword()
        with open(manifest_db, "rb") as f:
            enc = f.read()
        dec = self._crypto.decrypt_manifest_db(enc)
        self._dec_dir = tempfile.mkdtemp(prefix="forenziq_ios_")
        out = os.path.join(self._dec_dir, "Manifest.db")
        with open(out, "wb") as f:
            f.write(dec)
        self._log(t("iosx.decrypt_ok"))
        return out

    def _cleanup_decryption(self):
        if self._dec_dir and os.path.isdir(self._dec_dir):
            try:
                shutil.rmtree(self._dec_dir, ignore_errors=True)
            except Exception:
                pass
        self._dec_dir = None
        self._dec_cache = {}

    def _fid_to_path(self, manifest_db: str, fid: str) -> Optional[str]:
        """Lokální cesta k souboru podle fileID. U šifrované zálohy ho nejdřív
        dešifruje do temp a vrátí dešifrovanou kopii."""
        if not fid:
            return None
        if self._crypto is None:
            path = os.path.join(self.backup_path, fid[:2], fid)
            return path if os.path.isfile(path) else None
        if fid in self._dec_cache:
            return self._dec_cache[fid]
        enc_path = os.path.join(self.backup_path, fid[:2], fid)
        if not os.path.isfile(enc_path):
            return None
        try:
            conn = sqlite3.connect(manifest_db)
            row = conn.execute("SELECT file FROM Files WHERE fileID=?", (fid,)).fetchone()
            conn.close()
            if not row or not row[0]:
                return None
            with open(enc_path, "rb") as f:
                enc = f.read()
            dec = self._crypto.decrypt_file(bytes(row[0]), enc)
            out = os.path.join(self._dec_dir, fid)
            with open(out, "wb") as f:
                f.write(dec)
            self._dec_cache[fid] = out
            return out
        except Exception:
            return None

    # ── Module dispatcher ──────────────────────────────────────────────────

    def _extract_module(self, module: str, manifest_db: str) -> iOSBackupResult:
        res = iOSBackupResult(module)
        try:
            dispatch = {
                "contacts":          self._do_contacts,
                "messages":          self._do_messages,
                "sms_attachments":   self._do_sms_attachments,
                "call_logs":         self._do_call_logs,
                "notes":             self._do_notes,
                "calendar":          self._do_calendar,
                "safari_history":    self._do_safari,
                "photos":            self._do_photos,
                "photos_gps":        self._do_photos_gps,
                "voicemail":         self._do_voicemail,
                "wifi_networks":     self._do_wifi,
                "apps_installed":    self._do_apps,
                "app_usage":         self._do_app_usage,
                "whatsapp":          self._do_whatsapp,
                "whatsapp_contacts": self._do_whatsapp_contacts,
                "viber":             self._do_viber,
            }
            fn = dispatch.get(module)
            if fn:
                fn(res, manifest_db)
            else:
                res.error = f"Neznámý modul: {module}"
        except Exception as e:
            res.error = str(e)
        return res

    # ── SQLite helpers ─────────────────────────────────────────────────────

    def _find_file(self, manifest_db: str, domain: str, path_like: str) -> Optional[str]:
        """Locate a backup file by domain + LIKE pattern. Returns local path or None."""
        try:
            conn = sqlite3.connect(manifest_db)
            cur = conn.cursor()
            cur.execute(
                "SELECT fileID FROM Files WHERE domain=? AND relativePath LIKE ?",
                (domain, path_like),
            )
            row = cur.fetchone()
            conn.close()
            if not row:
                return None
            return self._fid_to_path(manifest_db, row[0])
        except Exception:
            return None

    def _find_files(self, manifest_db: str, domain: str,
                    path_like: str) -> list[tuple[str, str]]:
        """Find all files matching domain + LIKE. Returns [(fileID, relativePath)]."""
        try:
            conn = sqlite3.connect(manifest_db)
            cur = conn.cursor()
            cur.execute(
                "SELECT fileID, relativePath FROM Files "
                "WHERE domain=? AND relativePath LIKE ?",
                (domain, path_like),
            )
            rows = cur.fetchall()
            conn.close()
            out = []
            for r in rows:
                lp = self._fid_to_path(manifest_db, r[0])
                if lp:
                    out.append((lp, r[1]))
            return out
        except Exception:
            return []

    def _find_file_like(self, manifest_db: str, path_like: str,
                        domain_like: Optional[str] = None) -> Optional[str]:
        """Locate a backup file by relativePath LIKE across any domain."""
        try:
            conn = sqlite3.connect(manifest_db)
            cur = conn.cursor()
            if domain_like:
                cur.execute(
                    "SELECT fileID FROM Files WHERE domain LIKE ? AND relativePath LIKE ?",
                    (domain_like, path_like),
                )
            else:
                cur.execute(
                    "SELECT fileID FROM Files WHERE relativePath LIKE ?",
                    (path_like,),
                )
            row = cur.fetchone()
            conn.close()
            if not row:
                return None
            return self._fid_to_path(manifest_db, row[0])
        except Exception:
            return None

    def _open_db(self, file_path: Optional[str]) -> Optional[sqlite3.Connection]:
        """Copy a DB to a temp file and open it (avoids file locking)."""
        if not file_path or not os.path.isfile(file_path):
            return None
        try:
            tmp = tempfile.mktemp(suffix=".sqlite")
            shutil.copy2(file_path, tmp)
            conn = sqlite3.connect(tmp)
            conn.row_factory = sqlite3.Row
            conn._tmp_path = tmp  # type: ignore[attr-defined]
            return conn
        except Exception:
            return None

    def _close_db(self, conn: Optional[sqlite3.Connection]):
        if not conn:
            return
        try:
            tmp = getattr(conn, "_tmp_path", None)
            conn.close()
            if tmp and os.path.isfile(tmp):
                os.unlink(tmp)
        except Exception:
            pass

    def _save_json(self, name: str, data: list) -> str:
        path = os.path.join(self.output_dir, f"ios_{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    @staticmethod
    def _ios_ts(ts) -> str:
        """Convert iOS CoreData timestamp (seconds since 2001-01-01) to string."""
        if not ts:
            return ""
        try:
            dt = datetime.datetime(2001, 1, 1) + datetime.timedelta(seconds=float(ts))
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(ts)

    # ── Extractors ─────────────────────────────────────────────────────────

    def _do_contacts(self, res: iOSBackupResult, manifest_db: str):
        db = self._find_file(manifest_db, "HomeDomain",
                             "%AddressBook/AddressBook.sqlitedb%")
        conn = self._open_db(db)
        if not conn:
            res.error = "AddressBook.sqlitedb nenalezena"
            return
        data: list[dict] = []
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT ROWID, First, Last, Organization, Note "
                "FROM ABPerson ORDER BY Last, First"
            )
            persons = {r["ROWID"]: dict(r) for r in cur.fetchall()}
            cur.execute(
                "SELECT record_id, property, value FROM ABMultiValue"
            )
            for mv in cur.fetchall():
                rid = mv["record_id"]
                if rid not in persons:
                    continue
                if mv["property"] == 3:   # phone
                    persons[rid].setdefault("phone", []).append(mv["value"])
                elif mv["property"] == 4: # email
                    persons[rid].setdefault("email", []).append(mv["value"])
            for p in persons.values():
                data.append({
                    "first_name": (p.get("First") or ""),
                    "last_name":  (p.get("Last") or ""),
                    "organization": (p.get("Organization") or ""),
                    "phone":      ", ".join(p.get("phone", [])),
                    "email":      ", ".join(p.get("email", [])),
                    "note":       (p.get("Note") or ""),
                })
        finally:
            self._close_db(conn)
        res.data = data
        res.file_path = self._save_json("contacts", data)
        res.count = len(data)
        res.success = True

    def _do_messages(self, res: iOSBackupResult, manifest_db: str):
        db = self._find_file(manifest_db, "HomeDomain", "%SMS/sms.db%")
        conn = self._open_db(db)
        if not conn:
            res.error = "sms.db nenalezena"
            return
        data: list[dict] = []
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT m.date, m.text, m.is_from_me, m.service, h.id AS address
                FROM message m
                LEFT JOIN handle h ON m.handle_id = h.ROWID
                ORDER BY m.date DESC
                LIMIT 5000
            """)
            for row in cur.fetchall():
                ts = row["date"]
                # iOS 11+ stores nanoseconds, older stores seconds
                if ts and ts > 1_000_000_000_000:
                    ts_str = self._ios_ts(ts / 1_000_000_000)
                else:
                    ts_str = self._ios_ts(ts)
                data.append({
                    "date":      ts_str,
                    "address":    row["address"] or "",
                    "body":      row["text"] or "",
                    "from_me":   t("val.yes") if row["is_from_me"] else t("val.no"),
                    "service":   row["service"] or "",
                })
        finally:
            self._close_db(conn)
        res.data = data
        res.file_path = self._save_json("messages", data)
        res.count = len(data)
        res.success = True

    def _do_call_logs(self, res: iOSBackupResult, manifest_db: str):
        db = self._find_file(manifest_db, "HomeDomain",
                             "%CallHistoryDB/CallHistory.storedata%")
        conn = self._open_db(db)
        if not conn:
            res.error = "CallHistory.storedata nenalezena"
            return
        data: list[dict] = []
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT ZDATE, ZDURATION, ZADDRESS, ZANSWERED,
                       ZORIGINATED, ZSERVICE_PROVIDER
                FROM ZCALLRECORD
                ORDER BY ZDATE DESC
                LIMIT 2000
            """)
            for row in cur.fetchall():
                dur = row["ZDURATION"]
                if dur:
                    m, s = divmod(int(float(dur)), 60)
                    dur_str = f"{m}:{s:02d}"
                else:
                    dur_str = ""
                if row["ZORIGINATED"]:
                    direction = t("val.call_outgoing")
                elif row["ZANSWERED"]:
                    direction = t("val.call_incoming")
                else:
                    direction = t("val.call_missed")
                data.append({
                    "date":   self._ios_ts(row["ZDATE"]),
                    "number": row["ZADDRESS"] or "",
                    "duration": dur_str,
                    "direction": direction,
                    "service": row["ZSERVICE_PROVIDER"] or "",
                })
        finally:
            self._close_db(conn)
        res.data = data
        res.file_path = self._save_json("call_logs", data)
        res.count = len(data)
        res.success = True

    def _do_notes(self, res: iOSBackupResult, manifest_db: str):
        db = self._find_file(manifest_db, "HomeDomain", "%NoteStore.sqlite%")
        conn = self._open_db(db)
        if not conn:
            res.error = "NoteStore.sqlite nenalezena"
            return
        data: list[dict] = []
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT ZTITLE, ZSNIPPET, ZCREATIONDATE, ZMODIFICATIONDATE
                FROM ZICCLOUDSYNCINGOBJECT
                WHERE ZTITLE IS NOT NULL
                ORDER BY ZMODIFICATIONDATE DESC
            """)
            for row in cur.fetchall():
                data.append({
                    "title":    row["ZTITLE"] or "",
                    "preview":  row["ZSNIPPET"] or "",
                    "created":  self._ios_ts(row["ZCREATIONDATE"]),
                    "modified": self._ios_ts(row["ZMODIFICATIONDATE"]),
                })
        except Exception:
            pass
        finally:
            self._close_db(conn)
        res.data = data
        res.file_path = self._save_json("notes", data)
        res.count = len(data)
        res.success = bool(data)
        if not data:
            res.error = t("iosx.no_notes")

    def _do_calendar(self, res: iOSBackupResult, manifest_db: str):
        db = self._find_file(manifest_db, "HomeDomain",
                             "%Calendar/Calendar.sqlitedb%")
        conn = self._open_db(db)
        if not conn:
            res.error = "Calendar.sqlitedb nenalezena"
            return
        data: list[dict] = []
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT ZSUMMARY, ZNOTES, ZDTSTART, ZDTEND, ZLOCATION
                FROM ZCALENDARITEM
                WHERE ZSUMMARY IS NOT NULL
                ORDER BY ZDTSTART DESC
                LIMIT 2000
            """)
            for row in cur.fetchall():
                data.append({
                    "title":    row["ZSUMMARY"] or "",
                    "notes":    row["ZNOTES"] or "",
                    "start":    self._ios_ts(row["ZDTSTART"]),
                    "end":      self._ios_ts(row["ZDTEND"]),
                    "location": row["ZLOCATION"] or "",
                })
        except Exception:
            pass
        finally:
            self._close_db(conn)
        res.data = data
        res.file_path = self._save_json("calendar", data)
        res.count = len(data)
        res.success = bool(data)
        if not data:
            res.error = t("iosx.no_events")

    def _do_safari(self, res: iOSBackupResult, manifest_db: str):
        db = self._find_file(manifest_db, "HomeDomain",
                             "%Safari/History.db%")
        conn = self._open_db(db)
        if not conn:
            res.error = "Safari History.db nenalezena"
            return
        data: list[dict] = []
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT hv.visit_time, hi.url, hi.title
                FROM history_visits hv
                JOIN history_items hi ON hv.history_item = hi.ROWID
                ORDER BY hv.visit_time DESC
                LIMIT 5000
            """)
            for row in cur.fetchall():
                data.append({
                    "date":    self._ios_ts(row["visit_time"]),
                    "url":     row["url"] or "",
                    "title":   row["title"] or "",
                })
        finally:
            self._close_db(conn)
        res.data = data
        res.file_path = self._save_json("safari_history", data)
        res.count = len(data)
        res.success = True

    def _do_photos(self, res: iOSBackupResult, manifest_db: str):
        photos_dir = os.path.join(self.output_dir, "ios_photos")
        os.makedirs(photos_dir, exist_ok=True)
        files = self._find_files(manifest_db, "CameraRollDomain", "Media/DCIM/%")
        data: list[dict] = []
        for src_path, rel_path in files:
            if self._stopped():
                break
            src = src_path
            if not os.path.isfile(src):
                continue
            filename = os.path.basename(rel_path)
            subdir = os.path.dirname(rel_path.replace("Media/DCIM/", "", 1))
            dest_dir = os.path.join(photos_dir, subdir)
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, filename)
            try:
                shutil.copy2(src, dest)
                data.append({"filename": filename, "path": rel_path})
                self._log(t("x.copying").format(name=filename))
            except Exception:
                pass
        res.data = data
        res.file_path = photos_dir
        res.count = len(data)
        res.success = True

    def _do_voicemail(self, res: iOSBackupResult, manifest_db: str):
        db = self._find_file(manifest_db, "HomeDomain",
                             "%voicemail/voicemail.db%")
        conn = self._open_db(db)
        if not conn:
            res.error = "voicemail.db nenalezena"
            return
        data: list[dict] = []
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT date, sender, duration, callback_num FROM voicemail "
                "ORDER BY date DESC"
            )
            for row in cur.fetchall():
                ts = row["date"]
                try:
                    ts_str = datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    ts_str = str(ts)
                dur = row["duration"]
                data.append({
                    "date":      ts_str,
                    "sender":    row["sender"] or "",
                    "duration":  f"{int(float(dur or 0))}s",
                    "callback":  row["callback_num"] or "",
                })
        finally:
            self._close_db(conn)
        res.data = data
        res.file_path = self._save_json("voicemail", data)
        res.count = len(data)
        res.success = bool(data)
        if not data:
            res.error = t("iosx.no_voicemail")

    def _do_wifi(self, res: iOSBackupResult, manifest_db: str):
        plist_path = self._find_file(
            manifest_db, "RootDomain",
            "%Library/Preferences/com.apple.wifi.plist%",
        )
        data: list[dict] = []
        if plist_path:
            try:
                with open(plist_path, "rb") as f:
                    plist = plistlib.load(f)
                for net in plist.get("List of known networks", []):
                    data.append({
                        "ssid":              net.get("SSID_STR", ""),
                        "security":          net.get("SecurityType", ""),
                        "added":             str(net.get("AddedAt", "")),
                        "last_used":         str(net.get("LastConnectedAt", "")),
                    })
            except Exception as e:
                res.error = str(e)
        res.data = data
        res.file_path = self._save_json("wifi_networks", data)
        res.count = len(data)
        res.success = True

    def _do_apps(self, res: iOSBackupResult, manifest_db: str):
        info_path = os.path.join(self.backup_path, "Info.plist")
        data: list[dict] = []
        if os.path.isfile(info_path):
            try:
                with open(info_path, "rb") as f:
                    info = plistlib.load(f)
                for bundle_id in info.get("Installed Applications", []):
                    data.append({"bundle_id": bundle_id})
            except Exception as e:
                res.error = str(e)
        res.data = data
        res.file_path = self._save_json("apps_installed", data)
        res.count = len(data)
        res.success = bool(data)
        if not data:
            res.error = t("iosx.no_apps")

    def _do_sms_attachments(self, res: iOSBackupResult, manifest_db: str):
        db = self._find_file(manifest_db, "HomeDomain", "%SMS/sms.db%")
        conn = self._open_db(db)
        if not conn:
            res.error = "sms.db nenalezena"
            return
        data: list[dict] = []
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT filename, mime_type, total_bytes, created_date, transfer_name
                FROM attachment
                ORDER BY created_date DESC
                LIMIT 5000
            """)
            for row in cur.fetchall():
                ts = row["created_date"]
                if ts and ts > 1_000_000_000_000:
                    ts_str = self._ios_ts(ts / 1_000_000_000)
                else:
                    ts_str = self._ios_ts(ts)
                size = row["total_bytes"] or 0
                data.append({
                    "date":      ts_str,
                    "name":      row["transfer_name"] or os.path.basename(row["filename"] or ""),
                    "mime_type": row["mime_type"] or "",
                    "size":      f"{int(size):,}".replace(",", " ") if size else "",
                    "path":      row["filename"] or "",
                })
        except Exception as e:
            res.error = str(e)
        finally:
            self._close_db(conn)
        res.data = data
        res.file_path = self._save_json("sms_attachments", data)
        res.count = len(data)
        res.success = bool(data)
        if not data and not res.error:
            res.error = t("iosx.no_attachments")

    def _do_photos_gps(self, res: iOSBackupResult, manifest_db: str):
        db = self._find_file(manifest_db, "CameraRollDomain",
                             "%PhotoData/Photos.sqlite%")
        conn = self._open_db(db)
        if not conn:
            res.error = "Photos.sqlite nenalezena"
            return
        data: list[dict] = []
        try:
            cur = conn.cursor()
            # Table is ZASSET on modern iOS, ZGENERICASSET on older versions.
            table = "ZASSET"
            try:
                cur.execute("SELECT 1 FROM ZASSET LIMIT 1")
            except Exception:
                table = "ZGENERICASSET"
            cur.execute(f"""
                SELECT ZFILENAME, ZDIRECTORY, ZDATECREATED, ZLATITUDE, ZLONGITUDE
                FROM {table}
                WHERE ZLATITUDE IS NOT NULL AND ZLATITUDE <> -180.0
                ORDER BY ZDATECREATED DESC
                LIMIT 10000
            """)
            for row in cur.fetchall():
                data.append({
                    "filename":  row["ZFILENAME"] or "",
                    "directory": row["ZDIRECTORY"] or "",
                    "date":      self._ios_ts(row["ZDATECREATED"]),
                    "latitude":  row["ZLATITUDE"],
                    "longitude": row["ZLONGITUDE"],
                })
        except Exception as e:
            res.error = str(e)
        finally:
            self._close_db(conn)
        res.data = data
        res.file_path = self._save_json("photos_gps", data)
        res.count = len(data)
        res.success = bool(data)
        if not data and not res.error:
            res.error = t("iosx.no_gps")

    def _do_app_usage(self, res: iOSBackupResult, manifest_db: str):
        db = self._find_file_like(manifest_db, "%knowledgeC.db%")
        conn = self._open_db(db)
        if not conn:
            res.error = "knowledgeC.db nenalezena"
            return
        data: list[dict] = []
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT ZVALUESTRING, ZSTARTDATE, ZENDDATE
                FROM ZOBJECT
                WHERE ZSTREAMNAME = '/app/usage'
                ORDER BY ZSTARTDATE DESC
                LIMIT 5000
            """)
            for row in cur.fetchall():
                start = row["ZSTARTDATE"]
                end = row["ZENDDATE"]
                dur = ""
                try:
                    secs = int(float(end) - float(start))
                    if secs > 0:
                        m, s = divmod(secs, 60)
                        dur = f"{m}:{s:02d}"
                except Exception:
                    pass
                data.append({
                    "app":      row["ZVALUESTRING"] or "",
                    "start":    self._ios_ts(start),
                    "end":      self._ios_ts(end),
                    "duration": dur,
                })
        except Exception as e:
            res.error = str(e)
        finally:
            self._close_db(conn)
        res.data = data
        res.file_path = self._save_json("app_usage", data)
        res.count = len(data)
        res.success = bool(data)
        if not data and not res.error:
            res.error = t("iosx.no_usage")

    def _do_whatsapp(self, res: iOSBackupResult, manifest_db: str):
        db = self._find_file(
            manifest_db,
            "AppDomainGroup-group.net.whatsapp.WhatsApp.shared",
            "%ChatStorage.sqlite%",
        ) or self._find_file_like(manifest_db, "%ChatStorage.sqlite%", "%whatsapp%")
        conn = self._open_db(db)
        if not conn:
            res.error = t("iosx.no_whatsapp")
            return
        data: list[dict] = []
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT m.ZTEXT, m.ZMESSAGEDATE, m.ZISFROMME, m.ZFROMJID,
                       cs.ZPARTNERNAME, cs.ZCONTACTJID
                FROM ZWAMESSAGE m
                LEFT JOIN ZWACHATSESSION cs ON m.ZCHATSESSION = cs.Z_PK
                ORDER BY m.ZMESSAGEDATE DESC
                LIMIT 5000
            """)
            for row in cur.fetchall():
                data.append({
                    "date":    self._ios_ts(row["ZMESSAGEDATE"]),
                    "chat":    row["ZPARTNERNAME"] or row["ZCONTACTJID"] or "",
                    "sender":  row["ZFROMJID"] or "",
                    "from_me": t("val.yes") if row["ZISFROMME"] else t("val.no"),
                    "body":    row["ZTEXT"] or "",
                })
        except Exception as e:
            res.error = str(e)
        finally:
            self._close_db(conn)
        res.data = data
        res.file_path = self._save_json("whatsapp", data)
        res.count = len(data)
        res.success = bool(data)
        if not data and not res.error:
            res.error = t("iosx.no_whatsapp")

    def _do_whatsapp_contacts(self, res: iOSBackupResult, manifest_db: str):
        db = self._find_file(
            manifest_db,
            "AppDomainGroup-group.net.whatsapp.WhatsApp.shared",
            "%ContactsV2.sqlite%",
        ) or self._find_file_like(manifest_db, "%ContactsV2.sqlite%", "%whatsapp%")
        conn = self._open_db(db)
        if not conn:
            res.error = t("iosx.no_whatsapp")
            return
        data: list[dict] = []
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT ZFULLNAME, ZPHONENUMBER, ZWHATSAPPID
                FROM ZWAADDRESSBOOKCONTACT
                WHERE ZFULLNAME IS NOT NULL
                ORDER BY ZFULLNAME
            """)
            for row in cur.fetchall():
                data.append({
                    "name":     row["ZFULLNAME"] or "",
                    "phone":    row["ZPHONENUMBER"] or "",
                    "whatsapp": row["ZWHATSAPPID"] or "",
                })
        except Exception as e:
            res.error = str(e)
        finally:
            self._close_db(conn)
        res.data = data
        res.file_path = self._save_json("whatsapp_contacts", data)
        res.count = len(data)
        res.success = bool(data)
        if not data and not res.error:
            res.error = t("iosx.no_whatsapp")

    def _do_viber(self, res: iOSBackupResult, manifest_db: str):
        db = self._find_file(
            manifest_db,
            "AppDomainGroup-group.viber.share.container",
            "%Contacts.data%",
        ) or self._find_file_like(manifest_db, "%Contacts.data%", "%viber%")
        conn = self._open_db(db)
        if not conn:
            res.error = t("iosx.no_viber")
            return
        data: list[dict] = []
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT ZTEXT, ZDATE, ZSTATE
                FROM ZVIBERMESSAGE
                WHERE ZTEXT IS NOT NULL
                ORDER BY ZDATE DESC
                LIMIT 5000
            """)
            for row in cur.fetchall():
                data.append({
                    "date":    self._ios_ts(row["ZDATE"]),
                    "body":    row["ZTEXT"] or "",
                    "from_me": t("val.yes") if (row["ZSTATE"] or 0) else t("val.no"),
                })
        except Exception as e:
            res.error = str(e)
        finally:
            self._close_db(conn)
        res.data = data
        res.file_path = self._save_json("viber", data)
        res.count = len(data)
        res.success = bool(data)
        if not data and not res.error:
            res.error = t("iosx.no_viber")
