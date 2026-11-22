"""
core/android_dump.py — analysis of Android physical dumps (eMMC/UFS images).

Input: full storage image with GPT, single userdata partition image (raw or
Android sparse). The userdata file system (ext4 or F2FS) is read directly from
the image; nothing is mounted and the image is never written.

Results are ExtractionResult objects with the same module keys / JSON file
names as the live ADB extraction, so the Results panel, HTML/PDF/Excel
reports, Timeline and Map work unchanged.

Limitations (reported to the user): file-based encryption (FBE) and full-disk
encryption (FDE) are detected but not decrypted; F2FS-compressed files are skipped.
"""
from __future__ import annotations

import configparser
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from typing import Callable, Optional

from .adump.abx import parse_xml
from .adump.ext4 import Ext4, Ext4Error, fmt_ts
from .adump.f2fs import F2FS
from .adump.image import SubReader, list_partitions, open_image, pick_userdata
from .contacts_xlsx import write_name_numbers_xlsx
from .s30.export import safe_component
from .extractor import ExtractionResult
from .i18n import t

ADUMP_MODULES = ["device_info", "contacts", "sms", "call_logs", "whatsapp", "browser",
                 "wifi", "bluetooth", "accounts", "apps", "media"]

IMAGE_FILETYPES = [("Disk image", "*.bin *.img *.dd *.raw *.dump *.001 *.simg"), ("*", "*.*")]

# userdata root = /data on the phone
P_CONTACTS = ["data/com.android.providers.contacts/databases/contacts2.db",
              "user_de/0/com.android.providers.contacts/databases/contacts2.db"]
P_CALLLOG = ["data/com.android.providers.contacts/databases/calllog.db",
             "user_de/0/com.android.providers.contacts/databases/calllog.db"]
P_SAMSUNG_LOGS = ["data/com.sec.android.provider.logsprovider/databases/logs.db"]
P_MMSSMS = ["user_de/0/com.android.providers.telephony/databases/mmssms.db",
            "data/com.android.providers.telephony/databases/mmssms.db"]
P_WIFI = ["misc/apexdata/com.android.wifi/WifiConfigStore.xml", "misc/wifi/WifiConfigStore.xml"]
P_WPA = ["misc/wifi/wpa_supplicant.conf"]
P_BT = ["misc/bluedroid/bt_config.conf", "misc/bluetooth/bt_config.conf"]
P_ACCOUNTS = ["system_ce/0/accounts_ce.db", "system_de/0/accounts_de.db", "system/users/0/accounts.db"]
P_PACKAGES = ["system/packages.xml"]
BROWSERS = [
    ("Chrome", "data/com.android.chrome/app_chrome/Default/History"),
    ("Samsung Internet", "data/com.sec.android.app.sbrowser/app_sbrowser/Default/History"),
    ("Edge", "data/com.microsoft.emmx/app_chrome/Default/History"),
    ("Opera", "data/com.opera.browser/app_opera/History"),
    ("Brave", "data/com.brave.browser/app_chrome/Default/History"),
]
WHATSAPP_PKGS = [("WhatsApp", "com.whatsapp"), ("WhatsApp Business", "com.whatsapp.w4b")]
MEDIA_SKIP = ("android/data", "android/obb", ".thumbnails", ".trash")


class ADumpResult(ExtractionResult):
    @property
    def label(self) -> str:
        return t(f"adump.mod.{self.module}")


def _ms(ts) -> str:
    try:
        v = int(ts)
    except (TypeError, ValueError):
        return ""
    if v <= 0:
        return ""
    if v > 10 ** 14:          # µs (Chrome-like) – not expected here
        v //= 1000
    if v > 10 ** 11:          # ms
        v //= 1000
    return fmt_ts(v)


def _webkit(ts) -> str:
    try:
        v = int(ts)
    except (TypeError, ValueError):
        return ""
    return fmt_ts(v // 1_000_000 - 11_644_473_600) if v > 0 else ""


def _dur(s) -> str:
    try:
        s = int(s)
    except (TypeError, ValueError):
        return ""
    return f"{s // 60}m {s % 60}s"


def _cols(conn, table) -> set:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _digits(n: str) -> str:
    return re.sub(r"[^0-9+*#]", "", n or "")


def _num_key(n: str) -> str:
    """Comparison key for phone numbers: national and international forms match (last 9 digits)."""
    d = re.sub(r"[^0-9]", "", n or "")
    return d[-9:] if len(d) >= 9 else _digits(n)


class AndroidDumpAnalyzer:
    def __init__(self, image_path: str):
        self.image_path = image_path
        self.img = None
        self.layout = ""
        self.partitions = []
        self._stop = threading.Event()
        self.fs: Optional[Ext4 | F2FS] = None
        self.target = ""
        self._tmp = ""
        self.device_info: dict = {}
        self.fbe = False

    # ── opening ────────────────────────────────────────────────────────

    def open(self, log=lambda m: None, on_scan_progress=None):
        self.img = open_image(self.image_path)
        kind = type(self.img).__name__
        log(t("adump.log_opened").format(size=f"{self.img.size:,}",
                                         kind=t("adump.kind_sparse") if kind == "SparseImage" else t("adump.kind_raw")))
        self.layout, self.partitions = list_partitions(self.img, log=log, stop=self._stop.is_set,
                                                       on_progress=on_scan_progress)
        return self.partitions

    def default_partition(self):
        return pick_userdata(self.partitions)

    def stop(self):
        self._stop.set()

    def _stopped(self) -> bool:
        return self._stop.is_set()

    def close(self):
        if self.img:
            self.img.close()
        if self._tmp and os.path.isdir(self._tmp):
            shutil.rmtree(self._tmp, ignore_errors=True)

    # ── run ────────────────────────────────────────────────────────────

    def run(self, part, out_root: str, modules: list[str], hash_image: bool = True, xlsx: bool = True,
            on_log: Callable[[str], None] = lambda m: None,
            on_progress: Callable[[str, int, int], None] = lambda l, i, n: None,
            on_module_done: Callable = lambda r, i, n, el: None,
            on_done: Callable = lambda results, err, info, target: None) -> threading.Thread:

        def _worker():
            results: list = []
            try:
                results = self._run(part, out_root, modules, hash_image, xlsx, on_log, on_progress, on_module_done)
                on_done(results, "", self.device_info, self.target)
            except Exception as e:      # surface anything to the GUI
                on_done(results, f"{type(e).__name__}: {e}", self.device_info, self.target)
            finally:
                if self._tmp and os.path.isdir(self._tmp):
                    shutil.rmtree(self._tmp, ignore_errors=True)

        th = threading.Thread(target=_worker, daemon=True)
        th.start()
        return th

    def _run(self, part, out_root, modules, hash_image, xlsx, log, on_progress, on_module_done):
        name = os.path.splitext(os.path.basename(self.image_path))[0]
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.target = os.path.normpath(os.path.join(out_root, f"{name}_android_dump_{stamp}"))
        os.makedirs(self.target, exist_ok=True)
        self._tmp = tempfile.mkdtemp(prefix="forenziq_adump_")
        self._xlsx = xlsx
        info = self.device_info = {
            "source": "physical_dump",
            "image_file": os.path.abspath(self.image_path),
            "image_size": self.img.size,
            "partition_layout": self.layout,
            "partitions": len(self.partitions),
            "userdata_partition": part.name if part else "",
            "userdata_fs": part.fs if part else "",
        }
        if hash_image:
            self._hash_image(log)
        self._write_partition_table()

        if part is None:
            raise RuntimeError(t("adump.err_no_userdata"))
        if part.fs == "encrypted?":
            log("⚠ " + t("adump.warn_fde"))
        elif part.fs not in ("ext4", "f2fs"):
            log("⚠ " + t("adump.warn_fs").format(fs=part.fs))
        else:
            sub = SubReader(self.img, part.offset, part.size)
            if part.fs == "f2fs":
                self.fs = F2FS(sub)
                fi = self.fs.info()
                log(t("adump.log_f2fs").format(vol=fi["volume_name"] or "-",
                                                ver=fi["f2fs_version"], cp=fi["checkpoint_version"]))
                if fi["compression_feature"]:
                    log("⚠ " + t("adump.warn_f2fs_compr"))
            else:
                self.fs = Ext4(sub)
                fi = self.fs.info()
                log(t("adump.log_ext4").format(vol=fi["volume_name"] or "-", mounted=fi["last_mount_time"] or "-"))
            info.update({f"userdata_{k}": v for k, v in fi.items()})
            self.fbe = self._detect_fbe()
            info["fbe_encrypted"] = self.fbe
            if self.fbe:
                log("⚠ " + t("adump.warn_fbe"))

        results = []
        n = len(modules)
        for i, mod in enumerate(modules, 1):
            if self._stopped():
                log(t("adump.log_stopped"))
                break
            r = ADumpResult(mod)
            on_progress(r.label, i, n)
            t0 = time.time()
            try:
                if mod != "device_info" and self.fs is None:
                    r.error = t("adump.err_fs_unreadable")
                else:
                    getattr(self, f"_m_{mod}")(r, log)
            except Exception as e:
                r.success = False
                r.error = f"{type(e).__name__}: {e}"
            results.append(r)
            on_module_done(r, i, n, time.time() - t0)
        return results

    # ── helpers: image-level ───────────────────────────────────────────

    def _hash_image(self, log):
        md5, sha = hashlib.md5(), hashlib.sha256()
        size = os.path.getsize(self.image_path)
        done, last = 0, -1
        log(t("adump.log_hashing"))
        with open(self.image_path, "rb") as f:
            while True:
                if self._stopped():
                    log(t("adump.log_hash_stopped"))
                    return
                b = f.read(16 << 20)
                if not b:
                    break
                md5.update(b)
                sha.update(b)
                done += len(b)
                pct = done * 100 // max(size, 1)
                if pct // 10 != last:
                    last = pct // 10
                    log(f"   {pct} %")
        self.device_info["image_md5"] = md5.hexdigest()
        self.device_info["image_sha256"] = sha.hexdigest()
        log(f"MD5:     {md5.hexdigest()}")
        log(f"SHA-256: {sha.hexdigest()}")

    def _write_partition_table(self):
        rows = [{"index": p.index, "name": p.name, "offset": p.offset, "size": p.size, "fs": p.fs,
                 **{k: v for k, v in p.info.items()}} for p in self.partitions]
        with open(os.path.join(self.target, "partitions.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)

    def _detect_fbe(self) -> bool:
        d = self.fs.lookup("data")
        if d is None:
            return self.fs.encrypt_feature
        if d.encrypted:
            return True
        try:
            for e in self.fs.listdir(d)[:20]:
                if e.name != ".." and self.fs.inode(e.ino).encrypted:
                    return True
        except Ext4Error:
            pass
        return False

    # ── helpers: files ─────────────────────────────────────────────────

    def _find(self, paths):
        for p in paths:
            ino = self.fs.lookup(p)
            if ino is not None and ino.is_file and ino.size > 0:
                return p, ino
        return None, None

    def _save(self, fs_path: str, dest: str, ino=None) -> bool:
        ino = ino or self.fs.lookup(fs_path)
        if ino is None or not ino.is_file:
            return False
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in self.fs.iter_data(ino):
                f.write(chunk)
        if ino.mtime:
            try:
                os.utime(dest, (ino.atime or ino.mtime, ino.mtime))
            except OSError:
                pass
        return True

    def _evidence(self, fs_path: str, ino=None) -> str:
        """Copy file (+ SQLite sidecars) to <target>/files/<path>; returns local path."""
        dest = os.path.join(self.target, "files", *fs_path.split("/"))
        if not self._save(fs_path, dest, ino):
            return ""
        for suf in ("-wal", "-shm", "-journal"):
            self._save(fs_path + suf, dest + suf)
        return dest

    def _db(self, fs_path: str, ino=None) -> Optional[sqlite3.Connection]:
        """Evidence copy + separate working copy (opening applies WAL – never on the evidence copy)."""
        ev = self._evidence(fs_path, ino)
        if not ev:
            return None
        work = os.path.join(self._tmp, re.sub(r"[^A-Za-z0-9_.-]", "_", fs_path))
        for suf in ("", "-wal", "-shm", "-journal"):
            if os.path.exists(ev + suf):
                shutil.copy2(ev + suf, work + suf)
        conn = sqlite3.connect(work)
        conn.row_factory = sqlite3.Row
        return conn

    def _read(self, paths, limit=64 << 20) -> tuple[str, bytes]:
        p, ino = self._find(paths)
        if not p:
            return "", b""
        self._evidence(p, ino)
        return p, self.fs.read_file(ino, limit=limit)

    def _out_json(self, name: str, data) -> str:
        path = os.path.join(self.target, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    def _finish(self, r, data, json_name, not_found_key="adump.not_found"):
        r.data = data
        r.count = len(data) if isinstance(data, (list, dict)) else 0
        r.file_path = self._out_json(json_name, data)
        r.success = bool(data)
        if not data:
            r.error = t(not_found_key) + (" " + t("adump.fbe_hint") if self.fbe else "")

    # ── modules ────────────────────────────────────────────────────────

    def _m_device_info(self, r, log):
        info = self.device_info
        props = {}
        # build.prop from an ext4 system/vendor partition, if present
        for p in self.partitions:
            if p.fs != "ext4" or not re.match(r"^(system|vendor|product)(_[ab])?$", p.name.lower()):
                continue
            try:
                fs = Ext4(SubReader(self.img, p.offset, p.size))
                for bp in ("system/build.prop", "build.prop"):
                    ino = fs.lookup(bp)
                    if ino is not None and ino.is_file:
                        for line in fs.read_file(ino, 4 << 20).decode("utf-8", "replace").splitlines():
                            if "=" in line and not line.startswith("#"):
                                k, v = line.split("=", 1)
                                props.setdefault(k.strip(), v.strip())
                        break
            except Ext4Error:
                continue
        if self.fs is not None:
            props.update({k: v for k, v in self._persistent_props().items() if k not in props})
            info.update(self._settings_info())
            users = self._users()
            if users:
                info["users"] = "; ".join(f"{u['id']}: {u['name']}" if u["name"] else u["id"] for u in users)

        def pick(*keys):
            for k in keys:
                if props.get(k):
                    return props[k]
            return ""
        mapping = {
            "manufacturer": ("ro.product.manufacturer", "ro.product.system.manufacturer", "ro.product.vendor.manufacturer"),
            "brand": ("ro.product.brand", "ro.product.system.brand", "ro.product.vendor.brand"),
            "model": ("ro.product.model", "ro.product.system.model", "ro.product.vendor.model"),
            "device": ("ro.product.device", "ro.product.system.device", "ro.product.vendor.device"),
            "android": ("ro.build.version.release", "ro.system.build.version.release"),
            "sdk": ("ro.build.version.sdk", "ro.system.build.version.sdk"),
            "security_patch": ("ro.build.version.security_patch",),
            "build": ("ro.build.display.id", "ro.build.id"),
            "build_fingerprint": ("ro.build.fingerprint", "ro.system.build.fingerprint"),
            "timezone": ("persist.sys.timezone",),
            "locale": ("persist.sys.locale",),
        }
        for k, keys in mapping.items():
            v = pick(*keys)
            if v:
                info[k] = v
        r.data = info
        r.count = len(info)
        r.file_path = self._out_json("device_info.json", info)
        r.success = True

    def _persistent_props(self) -> dict:
        """/data/property/persistent_properties – protobuf {1: {1: name, 2: value}}."""
        p, b = self._read(["property/persistent_properties"], 4 << 20)
        out = {}
        if not b:
            return out

        def varint(buf, i):
            v = s = 0
            while i < len(buf):
                c = buf[i]
                i += 1
                v |= (c & 0x7F) << s
                s += 7
                if not c & 0x80:
                    break
            return v, i

        def fields(buf):
            i = 0
            while i < len(buf):
                key, i = varint(buf, i)
                wt = key & 7
                if wt == 2:
                    ln, i = varint(buf, i)
                    yield key >> 3, buf[i:i + ln]
                    i += ln
                elif wt == 0:
                    _, i = varint(buf, i)
                else:
                    return
        try:
            for fno, rec in fields(b):
                if fno != 1:
                    continue
                kv = {k: v.decode("utf-8", "replace") for k, v in fields(rec)}
                if 1 in kv:
                    out[kv[1]] = kv.get(2, "")
        except Exception:
            pass
        return out

    def _settings_info(self) -> dict:
        res = {}
        for path, keys in (("system/users/0/settings_global.xml", {"device_name": "device_name"}),
                           ("system/users/0/settings_secure.xml", {"bluetooth_name": "bluetooth_name",
                                                                   "bluetooth_address": "bluetooth_mac"})):
            _p, b = self._read([path], 8 << 20)
            if not b:
                continue
            try:
                root = parse_xml(b)
            except Exception:
                continue
            for s in root.iter("setting"):
                k = s.get("name")
                if k in keys and s.get("value"):
                    res[keys[k]] = s.get("value")
        return res

    def _users(self) -> list[dict]:
        users = []
        d = self.fs.lookup("system/users")
        if d is None or not d.is_dir:
            return users
        for e in self.fs.listdir(d):
            m = re.fullmatch(r"(\d+)\.xml", e.name)
            if not m:
                continue
            _p, b = self._read([f"system/users/{e.name}"], 4 << 20)
            try:
                root = parse_xml(b)
            except Exception:
                continue
            name_el = root.find("name")
            users.append({"id": m.group(1),
                          "name": (name_el.text if name_el is not None else "") or "",
                          "created": _ms(root.get("created")),
                          "last_logged_in": _ms(root.get("lastLoggedIn"))})
        return users

    # contacts
    def _m_contacts(self, r, log):
        p, ino = self._find(P_CONTACTS)
        rows, grouped = [], []
        if p:
            log(f"  📄 {p}")
            conn = self._db(p, ino)
            try:
                rows, grouped = self._parse_contacts(conn)
            finally:
                conn.close()
        self._finish(r, rows, "contacts.json")
        if rows and self._xlsx:
            write_name_numbers_xlsx(grouped, os.path.join(self.target, "contacts.xlsx"))
            log(t("adump.log_saved").format(name="contacts.xlsx"))

    def _parse_contacts(self, conn):
        rc = _cols(conn, "raw_contacts")
        acc_join, acc_sel = "", "''"
        if "account_id" in rc and _cols(conn, "accounts"):
            acc_join = "LEFT JOIN accounts a ON a._id = r.account_id"
            acc_sel = "COALESCE(a.account_type, '')"
        elif "account_type" in rc:
            acc_sel = "COALESCE(r.account_type, '')"
        sel = [
            "r._id AS rid",
            "r.contact_id AS cid" if "contact_id" in rc else "r._id AS cid",
            "r.display_name AS name" if "display_name" in rc else "'' AS name",
            "r.deleted AS deleted" if "deleted" in rc else "0 AS deleted",
            "r.times_contacted AS times" if "times_contacted" in rc else "0 AS times",
            "r.last_time_contacted AS last" if "last_time_contacted" in rc else "0 AS last",
            f"{acc_sel} AS account",
        ]
        contacts: dict = {}
        order = []
        for row in conn.execute(f"SELECT {', '.join(sel)} FROM raw_contacts r {acc_join}"):
            key = row["cid"] if row["cid"] is not None else f"r{row['rid']}"
            c = contacts.get(key)
            if c is None:
                c = contacts[key] = {"display_name": row["name"] or "", "phone": [], "email": [],
                                     "organization": "", "account": set(), "deleted": True,
                                     "times_contacted": 0, "last_contacted": 0, "_rids": set()}
                order.append(key)
            c["_rids"].add(row["rid"])
            if not c["display_name"] and row["name"]:
                c["display_name"] = row["name"]
            if row["account"]:
                c["account"].add(row["account"])
            c["deleted"] = c["deleted"] and bool(row["deleted"])
            c["times_contacted"] = max(c["times_contacted"], row["times"] or 0)
            c["last_contacted"] = max(c["last_contacted"], row["last"] or 0)
        rid_to_key = {rid: k for k, c in contacts.items() for rid in c["_rids"]}
        for row in conn.execute("SELECT d.raw_contact_id AS rid, m.mimetype AS mt, d.data1 AS v "
                                "FROM data d JOIN mimetypes m ON m._id = d.mimetype_id WHERE d.data1 IS NOT NULL"):
            c = contacts.get(rid_to_key.get(row["rid"]))
            if c is None:
                continue
            v = str(row["v"]).strip()
            mt = row["mt"] or ""
            if mt.endswith("/phone_v2"):
                if _num_key(v) not in {_num_key(x) for x in c["phone"]}:
                    c["phone"].append(v)
            elif mt.endswith("/email_v2"):
                if v not in c["email"]:
                    c["email"].append(v)
            elif mt.endswith("/organization") and not c["organization"]:
                c["organization"] = v
            elif mt.endswith("/name") and not c["display_name"]:
                c["display_name"] = v
        rows, grouped = [], []
        for k in order:
            c = contacts[k]
            if not (c["display_name"] or c["phone"] or c["email"]):
                continue
            rows.append({
                "display_name": c["display_name"],
                "number": "; ".join(c["phone"]),
                "email": "; ".join(c["email"]),
                "organization": c["organization"],
                "account": ", ".join(sorted(c["account"])),
                "times_contacted": c["times_contacted"],
                "last_contacted": _ms(c["last_contacted"]),
                "deleted": t("val.yes") if c["deleted"] else t("val.no"),
            })
            grouped.append((c["display_name"], c["phone"]))
        return rows, grouped

    # SMS / MMS
    _SMS_TYPES = {1: "in", 2: "out", 3: "draft", 4: "out", 5: "failed", 6: "out"}

    def _direction(self, kind: str) -> str:
        return {"in": t("val.sms_received"), "out": t("val.sms_sent"),
                "draft": t("adump.draft"), "failed": t("adump.failed")}.get(kind, kind)

    def _m_sms(self, r, log):
        p, ino = self._find(P_MMSSMS)
        rows = []
        if p:
            log(f"  📄 {p}")
            conn = self._db(p, ino)
            try:
                rows = self._parse_sms(conn) + self._parse_mms(conn, log)
            finally:
                conn.close()
        rows.sort(key=lambda x: x["date"], reverse=True)
        self._finish(r, rows, "sms.json")

    def _parse_sms(self, conn) -> list[dict]:
        cols = _cols(conn, "sms")
        if not cols:
            return []
        sel = ["address", "date", "type", "body"]
        sel += [c for c in ("date_sent", "read", "thread_id", "service_center") if c in cols]
        out = []
        for row in conn.execute(f"SELECT {', '.join(sel)} FROM sms"):
            d = dict(row)
            out.append({
                "date": _ms(d["date"]),
                "direction": self._direction(self._SMS_TYPES.get(d["type"], str(d["type"]))),
                "address": d["address"] or "",
                "body": d["body"] or "",
                "kind": "SMS",
                "date_sent": _ms(d.get("date_sent")),
                "read": t("val.yes") if d.get("read") else t("val.no"),
                "thread_id": d.get("thread_id", ""),
            })
        return out

    def _parse_mms(self, conn, log) -> list[dict]:
        if not _cols(conn, "pdu"):
            return []
        addrs: dict = {}
        for a in conn.execute("SELECT msg_id, address, type FROM addr"):
            addrs.setdefault(a["msg_id"], []).append((a["type"], a["address"] or ""))
        parts: dict = {}
        pc = _cols(conn, "part")
        if pc:
            sel = ", ".join(c for c in ("mid", "ct", "text", "_data", "cl") if c in pc)
            for prow in conn.execute(f"SELECT {sel} FROM part"):
                parts.setdefault(prow["mid"], []).append(dict(prow))
        out = []
        att_dir = os.path.join(self.target, "mms_parts")
        for m in conn.execute("SELECT _id, date, msg_box, thread_id, sub FROM pdu"):
            box = m["msg_box"]
            kind = {1: "in", 2: "out", 3: "draft", 4: "out"}.get(box, str(box))
            al = addrs.get(m["_id"], [])
            if kind == "in":
                peer = [a for tp, a in al if tp == 137]
            else:
                peer = [a for tp, a in al if tp in (151, 130, 129)]
            texts, atts = [], []
            for prt in parts.get(m["_id"], []):
                ct = prt.get("ct") or ""
                if ct == "text/plain" and prt.get("text"):
                    texts.append(prt["text"])
                elif ct != "application/smil" and prt.get("_data"):
                    fs_p = self._android_path(prt["_data"])
                    name = f"{m['_id']}_{os.path.basename(prt.get('cl') or prt['_data'])}"
                    if fs_p and self._save(fs_p, os.path.join(att_dir, name)):
                        atts.append(name)
            out.append({
                "date": _ms(m["date"]),
                "direction": self._direction(kind),
                "address": ", ".join(dict.fromkeys(a for a in peer if a and a != "insert-address-token")),
                "body": ((m["sub"] or "") + "\n" if m["sub"] else "") + "\n".join(texts),
                "kind": "MMS",
                "date_sent": "",
                "read": "",
                "thread_id": m["thread_id"],
                "attachments": ", ".join(atts),
            })
        if out:
            log(f"  MMS: {len(out)}")
        return out

    @staticmethod
    def _android_path(p: str) -> str:
        """Map an on-device /data/... path to a userdata-relative path."""
        p = p.replace("\\", "/")
        for pre, rep in (("/data/user_de/", "user_de/"), ("/data/user/0/", "data/"),
                         ("/data/data/", "data/"), ("/data/", "")):
            if p.startswith(pre):
                return rep + p[len(pre):]
        return ""

    # calls
    _CALL_TYPES = {1: "val.call_incoming", 2: "val.call_outgoing", 3: "val.call_missed",
                   4: "adump.call_voicemail", 5: "adump.call_rejected", 6: "adump.call_blocked",
                   7: "adump.call_external"}

    def _m_call_logs(self, r, log):
        rows = []
        for paths, table in ((P_CALLLOG, "calls"), (P_CONTACTS, "calls"), (P_SAMSUNG_LOGS, "logs")):
            p, ino = self._find(paths)
            if not p:
                continue
            conn = self._db(p, ino)
            try:
                cols = _cols(conn, table)
                if not {"number", "date"} <= cols:
                    continue
                log(f"  📄 {p} ({table})")
                sel = ["number", "date"] + [c for c in ("duration", "type", "name", "geocoded_location",
                                                        "countryiso", "logtype") if c in cols]
                where = " WHERE logtype IN (100, 500, 800, 900, 1000)" if table == "logs" and "logtype" in cols else ""
                for row in conn.execute(f"SELECT {', '.join(sel)} FROM {table}{where}"):
                    d = dict(row)
                    key = self._CALL_TYPES.get(d.get("type"))
                    rows.append({
                        "date": _ms(d["date"]),
                        "number": d["number"] or "",
                        "name": d.get("name") or "",
                        "type": t(key) if key else str(d.get("type", "")),
                        "duration": _dur(d.get("duration")),
                        "duration_s": d.get("duration") or 0,
                        "location": d.get("geocoded_location") or "",
                    })
            finally:
                conn.close()
            if rows:
                break
        rows.sort(key=lambda x: x["date"], reverse=True)
        self._finish(r, rows, "call_logs.json")

    # WhatsApp
    def _m_whatsapp(self, r, log):
        rows = []
        for app, pkg in WHATSAPP_PKGS:
            p, ino = self._find([f"data/{pkg}/databases/msgstore.db"])
            if not p:
                continue
            log(f"  📄 {p}")
            names = {}
            wp, wino = self._find([f"data/{pkg}/databases/wa.db"])
            if wp:
                wconn = self._db(wp, wino)
                try:
                    if _cols(wconn, "wa_contacts"):
                        for c in wconn.execute("SELECT jid, display_name, wa_name FROM wa_contacts"):
                            names[c["jid"]] = c["display_name"] or c["wa_name"] or ""
                finally:
                    wconn.close()
            conn = self._db(p, ino)
            try:
                rows += self._parse_whatsapp(conn, names, app)
            finally:
                conn.close()
            for extra in ("axolotl.db", "chatsettings.db"):
                self._evidence(f"data/{pkg}/databases/{extra}")
        rows.sort(key=lambda x: x["date"], reverse=True)
        self._finish(r, rows, "whatsapp.json")

    def _parse_whatsapp(self, conn, names, app) -> list[dict]:
        out = []
        if _cols(conn, "message") and _cols(conn, "jid"):
            mc = _cols(conn, "message")
            sender = "LEFT JOIN jid s ON s._id = m.sender_jid_row_id" if "sender_jid_row_id" in mc else ""
            sender_sel = "s.raw_string AS sender" if sender else "'' AS sender"
            q = (f"SELECT m.timestamp AS ts, m.from_me AS from_me, m.text_data AS body, "
                 f"m.message_type AS mtype, j.raw_string AS jid, c.subject AS subject, {sender_sel} "
                 f"FROM message m LEFT JOIN chat c ON c._id = m.chat_row_id "
                 f"LEFT JOIN jid j ON j._id = c.jid_row_id {sender}")
            rows = conn.execute(q)
        elif _cols(conn, "messages"):
            rows = conn.execute("SELECT timestamp AS ts, key_from_me AS from_me, data AS body, "
                                "media_wa_type AS mtype, key_remote_jid AS jid, '' AS subject, "
                                "remote_resource AS sender FROM messages")
        else:
            return out
        for m in rows:
            jid = m["jid"] or ""
            if jid in ("status@broadcast", "-1"):
                continue
            body = m["body"] or ""
            if not body and m["mtype"] not in (0, None):
                body = f"[{t('adump.wa_media')} {m['mtype']}]"
            out.append({
                "date": _ms(m["ts"]),
                "chat": m["subject"] or names.get(jid, "") or jid.split("@")[0],
                "jid": jid,
                "sender": "" if m["from_me"] else (names.get(m["sender"] or "", "") or (m["sender"] or "").split("@")[0]),
                "from_me": t("val.yes") if m["from_me"] else t("val.no"),
                "body": body,
                "app": app,
            })
        return out

    # browser
    def _m_browser(self, r, log):
        rows = []
        for browser, path in BROWSERS:
            p, ino = self._find([path])
            if not p:
                continue
            log(f"  📄 {p}")
            conn = self._db(p, ino)
            try:
                if _cols(conn, "urls"):
                    for u in conn.execute("SELECT url, title, visit_count, last_visit_time FROM urls"):
                        rows.append({"date": _webkit(u["last_visit_time"]), "title": u["title"] or "",
                                     "url": u["url"] or "", "visits": u["visit_count"] or 0, "browser": browser})
            finally:
                conn.close()
            base = path.rsplit("/", 1)[0]
            for extra in ("Bookmarks", "Login Data", "Web Data", "Cookies", "Top Sites"):
                self._evidence(f"{base}/{extra}")
        rows.sort(key=lambda x: x["date"], reverse=True)
        self._finish(r, rows, "browser_history.json")

    # Wi-Fi
    def _m_wifi(self, r, log):
        rows = []
        p, b = self._read(P_WIFI)
        if b:
            log(f"  📄 {p}")
            root = parse_xml(b)
            for net in root.iter("Network"):
                vals = {}
                for el in net.iter():
                    nm = el.get("name")
                    if nm and el.tag in ("string", "boolean", "int", "long"):
                        vals[nm] = el.get("value") if el.get("value") is not None else (el.text or "")
                ssid = (vals.get("SSID") or "").strip('"')
                if not ssid:
                    continue
                cfg = vals.get("ConfigKey", "")
                sec = cfg.rsplit('"', 1)[-1] if '"' in cfg else ""
                psk = vals.get("PreSharedKey", "")
                rows.append({
                    "ssid": ssid,
                    "security": sec or ("PSK" if psk else ""),
                    "password": psk.strip('"') if psk and psk.startswith('"') else (psk or ""),
                    "hidden": t("val.yes") if vals.get("HiddenSSID") == "true" else t("val.no"),
                    "creator": vals.get("CreatorName", ""),
                })
        p2, b2 = self._read(P_WPA)
        if b2:
            log(f"  📄 {p2}")
            known = {x["ssid"] for x in rows}
            for blk in re.findall(r"network=\{(.*?)\}", b2.decode("utf-8", "replace"), re.S):
                kv = dict(re.findall(r"^\s*(\w+)=(.*)$", blk, re.M))
                ssid = kv.get("ssid", "").strip('"')
                if ssid and ssid not in known:
                    rows.append({"ssid": ssid, "security": kv.get("key_mgmt", ""),
                                 "password": kv.get("psk", "").strip('"'), "hidden": "", "creator": ""})
        self._finish(r, rows, "wifi_networks.json")

    # Bluetooth
    def _m_bluetooth(self, r, log):
        rows = []
        p, b = self._read(P_BT)
        if b:
            log(f"  📄 {p}")
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                cp.read_string(b.decode("utf-8", "replace"))
            except configparser.Error:
                pass
            for sec in cp.sections():
                if sec == "Adapter":
                    self.device_info["bluetooth_mac"] = cp[sec].get("Address", "")
                    self.device_info["bluetooth_name"] = cp[sec].get("Name", "")
                    continue
                if not re.fullmatch(r"[0-9a-fA-F:]{17}", sec):
                    continue
                s = cp[sec]
                ts = s.get("Timestamp", "")
                rows.append({
                    "name": s.get("Name", "") or s.get("Aliase", ""),
                    "address": sec,
                    "paired": t("val.yes") if (s.get("LinkKey") or s.get("LE_KEY_PENC")) else t("val.no"),
                    "last_seen": fmt_ts(int(ts)) if ts.isdigit() else "",
                    "device_class": s.get("DevClass", ""),
                })
        self._finish(r, rows, "bluetooth.json")

    # accounts
    def _m_accounts(self, r, log):
        rows, seen = [], set()
        for path in P_ACCOUNTS:
            p, ino = self._find([path])
            if not p:
                continue
            log(f"  📄 {p}")
            conn = self._db(p, ino)
            try:
                if {"name", "type"} <= _cols(conn, "accounts"):
                    for a in conn.execute("SELECT name, type FROM accounts"):
                        k = (a["name"], a["type"])
                        if k not in seen:
                            seen.add(k)
                            rows.append({"name": a["name"] or "", "type": a["type"] or ""})
            finally:
                conn.close()
        self._finish(r, rows, "accounts.json")

    # apps
    def _m_apps(self, r, log):
        rows = []
        p, b = self._read(P_PACKAGES)
        if b:
            log(f"  📄 {p}")
            root = parse_xml(b)

            def hx(v):
                try:
                    return _ms(int(v, 16)) if v else ""
                except ValueError:
                    return ""
            for pkg in root.iter("package"):
                name = pkg.get("name")
                if not name:
                    continue
                code = pkg.get("codePath", "")
                rows.append({
                    "package": name,
                    "version": pkg.get("version", ""),
                    "installed": hx(pkg.get("it") or pkg.get("ft")),
                    "updated": hx(pkg.get("ut")),
                    "installer": pkg.get("installer", "") or pkg.get("installInitiator", ""),
                    "system": t("val.yes") if code.startswith(("/system", "/product", "/vendor", "/apex", "/system_ext")) else t("val.no"),
                    "path": code,
                })
        if not rows:            # fallback: package directories in /data/data
            d = self.fs.lookup("data")
            if d is not None and d.is_dir and not self.fbe:
                for e in self.fs.listdir(d):
                    if e.name != ".." and "." in e.name:
                        rows.append({"package": e.name, "version": "", "installed": "", "updated": "",
                                     "installer": "", "system": "", "path": f"/data/data/{e.name}"})
        rows.sort(key=lambda x: (x["system"] == t("val.yes"), x["package"]))
        self._finish(r, rows, "apps.json")

    # media (internal storage /sdcard)
    def _m_media(self, r, log):
        base = "media/0"
        dest_root = os.path.join(self.target, "media")
        n = size = 0
        files = []
        used: set = set()
        if self.fs.lookup(base) is not None:
            for full, e, ino in self.fs.walk(base):
                if self._stopped():
                    break
                rel = full[len(base) + 1:]
                low = rel.lower()
                if (not ino.is_file or low.startswith(MEDIA_SKIP)
                        or any(c in (".thumbnails", ".trash") for c in low.split("/")[:-1])):
                    continue
                # Windows-safe names (reserved CON/NUL…, invalid chars) and no case-insensitive overwrites
                dest = os.path.join(dest_root, *[safe_component(c) for c in rel.split("/")])
                stem, ext = os.path.splitext(dest)
                k = 1
                while dest.lower() in used:
                    k += 1
                    dest = f"{stem}_{k}{ext}"
                used.add(dest.lower())
                try:
                    self._save(full, dest, ino)
                except (Ext4Error, OSError) as ex:
                    log(f"  ✗ {rel}: {ex}")
                    continue
                n += 1
                size += ino.size
                files.append({"filename": e.name, "path": "/sdcard/" + rel, "size": ino.size,
                              "modified": fmt_ts(ino.mtime), "created": fmt_ts(ino.crtime),
                              "_local": os.path.normcase(os.path.abspath(dest))})
                if n % 200 == 0:
                    log(t("adump.log_media").format(n=n, mb=size // 1_048_576))
        if n:
            log(t("adump.log_media").format(n=n, mb=size // 1_048_576))
            self._file_analysis(r, files)
        else:
            self._finish(r, [], "media_files.json")

    def _file_analysis(self, r, files):
        """Reuse the EXIF/GPS analysis of the live extraction on the copied media."""
        from .extractor import Extractor
        img_ext = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".heic", ".webp", ".bmp"}
        by_local = {f.pop("_local"): f for f in files}
        self._out_json("media_files.json", files)
        if any(os.path.splitext(f["filename"])[1].lower() in img_ext for f in files):
            ex = Extractor(adb=None, output_dir=self.target)
            ex._stop_event = self._stop
            ex._extract_file_analysis(r)
            # timestamps of the local copy are not evidence – use the ones from the image
            for entry in r.data or []:
                src = by_local.get(os.path.normcase(os.path.abspath(entry.get("path", ""))))
                if src:
                    entry["modified"], entry["created"] = src["modified"], src["created"]
                    entry["device_path"] = src["path"]
            if r.file_path and os.path.isfile(r.file_path):
                with open(r.file_path, encoding="utf-8") as f:
                    fa = json.load(f)
                fa["files"] = r.data
                with open(r.file_path, "w", encoding="utf-8") as f:
                    json.dump(fa, f, ensure_ascii=False, indent=2)
        else:
            r.data, r.count, r.success = files, len(files), True
            r.file_path = os.path.join(self.target, "media_files.json")
