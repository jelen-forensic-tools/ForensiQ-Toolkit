import os
import re
import json
import threading
import datetime
from typing import Callable, Optional
from .adb_manager import ADBManager
from .i18n import t, has


EXTRACTION_MODULES = {
    "device_info":    "Device Information",
    "contacts":       "Contacts",
    "sms":            "SMS / MMS",
    "call_logs":      "Call History",
    "apps":           "Installed Apps",
    "media":          "Photos & Videos",
    "documents":      "Documents",
    "wifi":           "Wi-Fi Networks",
    "accounts":       "Accounts",
    "browser":        "Browser History",
    "user_data_tree": "User Data (with structure)",
    "file_analysis":  "File Analysis (EXIF & metadata)",
    "file_hashes":    "File Hashes (MD5, SHA-256)",
    "deleted_data":   "Deleted Data (root)",
    "whatsapp":       "WhatsApp (root)",
    "viber":          "Viber (root)",
}

# Agent modules – extended extraction for newer Android versions
AGENT_MODULES = {
    "adb_backup":    "ADB App Backup",
    "root_backup":   "Root Backup /data/data (Android 12+)",
    "app_providers": "App Data (Content Providers)",
    "usage_stats":   "App Usage Statistics",
    "termux_agent":  "Termux Agent",
}


class ExtractionResult:
    @property
    def label(self) -> str:
        if has(f"mod.{self.module}"):
            return t(f"mod.{self.module}")
        return EXTRACTION_MODULES.get(self.module) or AGENT_MODULES.get(self.module, self.module)

    def __init__(self, module: str):
        self.module = module
        self.success = False
        self.data = None
        self.error = ""
        self.file_path = ""
        self.count = 0


class Extractor:
    def __init__(self, adb: ADBManager, output_dir: str):
        self.adb = adb
        self.output_dir = output_dir
        self._stop_event = threading.Event()
        self._on_notify: Optional[Callable[[str], None]] = None
        self._on_log: Optional[Callable[[str], None]] = None

    def _log(self, msg: str):
        if self._on_log:
            self._on_log(msg)

    @staticmethod
    def _fmt_size(n: int) -> str:
        if n >= 1_073_741_824:
            return f"{n / 1_073_741_824:.1f} GB"
        if n >= 1_048_576:
            return f"{n / 1_048_576:.1f} MB"
        if n >= 1024:
            return f"{n / 1024:.1f} KB"
        return f"{n} B"

    def stop(self):
        self._stop_event.set()

    def _stopped(self) -> bool:
        return self._stop_event.is_set()

    def extract_all(self, modules: list[str],
                    on_progress: Callable[[str, int, int], None],
                    on_done: Callable[[list[ExtractionResult]], None],
                    on_module_done: Callable = None,
                    on_disconnected: Callable = None,
                    on_reconnected: Callable = None,
                    on_notify: Callable[[str], None] = None,
                    on_log: Callable[[str], None] = None):
        """Run extraction of selected modules in a new thread.

        on_module_done(result, current, total, elapsed_sec, avg_elapsed_sec)
        on_disconnected() – called when device loss is detected (from worker thread)
        on_reconnected()  – called when device comes back   (from worker thread)
        """
        self._stop_event.clear()
        self._on_notify = on_notify
        self._on_log = on_log

        def _ensure_connected() -> bool:
            """Block until the device is back online or stop is requested.
            Returns True on reconnection, False if the user stopped extraction."""
            import time
            if self.adb.is_connected():
                return True
            if on_disconnected:
                on_disconnected()
            while not self._stopped():
                time.sleep(2)
                if self.adb.is_connected():
                    if on_reconnected:
                        on_reconnected()
                    return True
            return False

        def _worker():
            results = []
            total = len(modules)
            elapsed_times = []
            i = 0

            while i < total:
                if self._stopped():
                    break

                # Pause here if device is not reachable
                if not _ensure_connected():
                    break

                module = modules[i]
                label = t(f"mod.{module}") if has(f"mod.{module}") else module
                on_progress(label, i + 1, total)

                t_start = datetime.datetime.now()
                result = self._extract_module(module)
                elapsed = (datetime.datetime.now() - t_start).total_seconds()

                # If the module failed, check whether it was caused by a disconnect.
                # If so, wait for reconnection and retry the same module.
                if not result.success and not self.adb.is_connected():
                    if not _ensure_connected():
                        break
                    continue  # retry this module index

                elapsed_times.append(elapsed)
                results.append(result)

                if on_module_done:
                    avg = sum(elapsed_times) / len(elapsed_times)
                    on_module_done(result, i + 1, total, elapsed, avg)

                i += 1

            on_done(results)

        threading.Thread(target=_worker, daemon=True).start()

    def _extract_module(self, module: str) -> ExtractionResult:
        result = ExtractionResult(module)
        try:
            method = getattr(self, f"_extract_{module}", None)
            if method:
                method(result)
            else:
                result.error = t("x.not_implemented")
        except Exception as e:
            result.error = str(e)
        return result

    # ── Device Info ──────────────────────────────────────────────────────────

    def _extract_device_info(self, r: ExtractionResult):
        info = self.adb.get_device_info()
        path = os.path.join(self.output_dir, "device_info.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
        r.data = info
        r.file_path = path
        r.success = True
        r.count = len(info)

    # ── Contacts ─────────────────────────────────────────────────────────────

    def _extract_contacts(self, r: ExtractionResult):
        out = self.adb.shell(
            "content query --uri content://contacts/phones "
            "--projection display_name:number:type"
        )
        contacts = []
        for line in out.split("\n"):
            line = line.strip()
            if not line or "Row:" not in line:
                continue
            row = {}
            for field in ["display_name", "number", "type"]:
                m = __import__("re").search(rf"{field}=([^,\n]+)", line)
                if m:
                    row[field] = m.group(1).strip()
            if row:
                contacts.append(row)

        path = os.path.join(self.output_dir, "contacts.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(contacts, f, ensure_ascii=False, indent=2)
        r.data = contacts
        r.file_path = path
        r.success = True
        r.count = len(contacts)

    # ── SMS ──────────────────────────────────────────────────────────────────

    def _extract_sms(self, r: ExtractionResult):
        import re
        out = self.adb.shell(
            "content query --uri content://sms "
            "--projection address:date:body:type",
            timeout=60
        )
        messages = []
        for line in out.split("\n"):
            line = line.strip()
            if "Row:" not in line:
                continue
            row = {}
            for field in ["address", "body", "type"]:
                m = re.search(rf"{field}=([^,\n]+)", line)
                if m:
                    row[field] = m.group(1).strip()
            m_date = re.search(r"date=(\d+)", line)
            if m_date:
                ts = int(m_date.group(1)) // 1000
                row["date"] = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            row["direction"] = t("val.sms_received") if row.get("type") == "1" else t("val.sms_sent")
            if row:
                messages.append(row)

        path = os.path.join(self.output_dir, "sms.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)
        r.data = messages
        r.file_path = path
        r.success = True
        r.count = len(messages)

    # ── Call Logs ────────────────────────────────────────────────────────────

    def _extract_call_logs(self, r: ExtractionResult):
        import re
        out = self.adb.shell(
            "content query --uri content://call_log/calls "
            "--projection number:date:duration:type"
        )
        calls = []
        type_map = {"1": t("val.call_incoming"), "2": t("val.call_outgoing"), "3": t("val.call_missed")}
        for line in out.split("\n"):
            line = line.strip()
            if "Row:" not in line:
                continue
            row = {}
            m = re.search(r"number=([^,\n]+)", line)
            if m:
                row["number"] = m.group(1).strip()
            m_date = re.search(r"date=(\d+)", line)
            if m_date:
                ts = int(m_date.group(1)) // 1000
                row["date"] = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            m_dur = re.search(r"duration=(\d+)", line)
            if m_dur:
                secs = int(m_dur.group(1))
                row["duration"] = f"{secs // 60}m {secs % 60}s"
            m_type = re.search(r"type=(\d+)", line)
            if m_type:
                row["type"] = type_map.get(m_type.group(1), m_type.group(1))
            if row:
                calls.append(row)

        path = os.path.join(self.output_dir, "call_logs.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(calls, f, ensure_ascii=False, indent=2)
        r.data = calls
        r.file_path = path
        r.success = True
        r.count = len(calls)

    # ── Apps ─────────────────────────────────────────────────────────────────

    def _extract_apps(self, r: ExtractionResult):
        packages = self.adb.get_installed_apps(include_system=False)

        # A single `dumpsys package` dump for all packages instead of one call
        # per package (each is a round trip over USB). The output contains blocks
        # "Package [<pkg>] (...):" with a "versionName=..." line.
        version_map: dict = {}
        dump = self.adb.shell("dumpsys package packages")
        cur_pkg = None
        for line in dump.split("\n"):
            pm = re.search(r"Package \[([^\]]+)\]", line)
            if pm:
                cur_pkg = pm.group(1)
                continue
            if cur_pkg and "versionName=" in line:
                vm = re.search(r"versionName=([^\s]+)", line)
                if vm and cur_pkg not in version_map:
                    version_map[cur_pkg] = vm.group(1)

        apps = []
        for pkg in packages:
            apps.append({
                "package": pkg,
                "version": version_map.get(pkg, "N/A"),
                "installed": "N/A",
                "updated": "N/A",
            })

        path = os.path.join(self.output_dir, "apps.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(apps, f, ensure_ascii=False, indent=2)
        r.data = apps
        r.file_path = path
        r.success = True
        r.count = len(apps)

    # ── Media ────────────────────────────────────────────────────────────────

    # Image/video extensions pulled by the media module
    MEDIA_EXTS = (
        "jpg", "jpeg", "png", "gif", "webp", "bmp", "heic", "heif", "dng",
        "tiff", "tif", "mp4", "3gp", "3g2", "mov", "avi", "mkv", "webm", "m4v", "mpg",
    )

    def _extract_media(self, r: ExtractionResult):
        media_dir = os.path.join(self.output_dir, "media")
        os.makedirs(media_dir, exist_ok=True)

        # Also search /sdcard/Android/media (apps such as WhatsApp or Telegram
        # store media there). Filter by extension so that databases and caches
        # are not pulled from app folders; skip system thumbnails.
        roots = "/sdcard/DCIM /sdcard/Pictures /sdcard/Movies /sdcard/Android/media"
        name_clause = " -o ".join(f"-iname '*.{ext}'" for ext in self.MEDIA_EXTS)
        paths_raw = self.adb.shell(
            f"find {roots} -type f \\( {name_clause} \\) "
            "! -path '*/.thumbnails/*' ! -path '*/.thumbnail/*' "
            "! -path '*/cache/*' ! -path '*/.cache/*' 2>/dev/null"
        )
        files = [p.strip() for p in paths_raw.split("\n") if p.strip()]
        total = len(files)
        self._log(f"Nalezeno {total} mediálních souborů – zahajuji kopírování…")

        pulled = 0
        failed: list[str] = []
        used_names: set[str] = set()
        for remote_path in files:
            if self._stopped():
                break
            # Keep the name unique (files with the same name in different
            # folders would otherwise overwrite each other).
            filename = os.path.basename(remote_path)
            if filename in used_names:
                stem, ext = os.path.splitext(filename)
                parent = os.path.basename(os.path.dirname(remote_path))
                filename = f"{stem}_{parent}{ext}"
                n = 1
                while filename in used_names:
                    filename = f"{stem}_{parent}_{n}{ext}"
                    n += 1
            used_names.add(filename)
            local_path = os.path.join(media_dir, filename)
            if self.adb.pull(remote_path, local_path):
                pulled += 1
            else:
                failed.append(remote_path)
            if pulled % 20 == 0 or pulled == total:
                remaining = total - pulled
                self._log(f"  Zkopírováno {pulled}/{total} souborů  (zbývá {remaining})")

        if failed:
            self._log(f"  ⚠ {len(failed)} souborů se nepodařilo stáhnout (originály).")

        # Save the manifest
        manifest = {"total_found": len(files), "total_pulled": pulled,
                    "total_failed": len(failed), "failed": failed, "files": files}
        path = os.path.join(self.output_dir, "media_manifest.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        r.data = manifest
        r.file_path = media_dir
        r.success = True
        r.count = pulled

    # ── Documents ────────────────────────────────────────────────────────────

    def _extract_documents(self, r: ExtractionResult):
        docs_dir = os.path.join(self.output_dir, "documents")
        os.makedirs(docs_dir, exist_ok=True)

        exts = "pdf doc docx xls xlsx ppt pptx txt csv"
        find_cmds = " ".join([f'-name "*.{e}"' for e in exts.split()])
        paths_raw = self.adb.shell(
            f"find /sdcard /sdcard/Download \\( {find_cmds} \\) -type f 2>/dev/null",
            timeout=60
        )
        files = [p.strip() for p in paths_raw.split("\n") if p.strip()]
        total = len(files)
        self._log(f"Nalezeno {total} dokumentů – zahajuji kopírování…")

        pulled = 0
        for remote_path in files:
            if self._stopped():
                break
            filename = os.path.basename(remote_path)
            local_path = os.path.join(docs_dir, filename)
            if self.adb.pull(remote_path, local_path):
                pulled += 1
            remaining = total - pulled
            self._log(f"  [{pulled}/{total}] {filename}  (zbývá {remaining})")

        manifest = {"total_found": len(files), "total_pulled": pulled, "files": files}
        path = os.path.join(self.output_dir, "documents_manifest.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        r.data = manifest
        r.file_path = docs_dir
        r.success = True
        r.count = pulled

    # ── Wi-Fi ────────────────────────────────────────────────────────────────

    def _extract_wifi(self, r: ExtractionResult):
        import re
        out = self.adb.shell("dumpsys wifi | grep -A2 'SSID'")
        networks = []
        for line in out.split("\n"):
            m = re.search(r'SSID: "?([^",\n]+)"?', line)
            if m:
                ssid = m.group(1).strip()
                if ssid and ssid not in [n.get("ssid") for n in networks]:
                    networks.append({"ssid": ssid})

        path = os.path.join(self.output_dir, "wifi_networks.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(networks, f, ensure_ascii=False, indent=2)
        r.data = networks
        r.file_path = path
        r.success = True
        r.count = len(networks)

    # ── Accounts ─────────────────────────────────────────────────────────────

    def _extract_accounts(self, r: ExtractionResult):
        import re
        out = self.adb.shell("dumpsys account")
        accounts = []
        for line in out.split("\n"):
            m = re.search(r"Account \{name=([^,]+), type=([^\}]+)\}", line)
            if m:
                accounts.append({"name": m.group(1).strip(), "type": m.group(2).strip()})

        path = os.path.join(self.output_dir, "accounts.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(accounts, f, ensure_ascii=False, indent=2)
        r.data = accounts
        r.file_path = path
        r.success = True
        r.count = len(accounts)

    # ── Browser History ──────────────────────────────────────────────────────

    def _extract_browser(self, r: ExtractionResult):
        import re
        out = self.adb.shell(
            "content query --uri content://browser/bookmarks "
            "--projection title:url:date"
        )
        history = []
        for line in out.split("\n"):
            if "Row:" not in line:
                continue
            row = {}
            for field in ["title", "url"]:
                m = re.search(rf"{field}=([^,\n]+)", line)
                if m:
                    row[field] = m.group(1).strip()
            m_date = re.search(r"date=(\d+)", line)
            if m_date:
                try:
                    ts = int(m_date.group(1)) // 1000
                    row["date"] = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
            if row.get("url"):
                history.append(row)

        path = os.path.join(self.output_dir, "browser_history.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        r.data = history
        r.file_path = path
        r.success = True
        r.count = len(history)

    # ── User Data Tree ────────────────────────────────────────────────────────

    def _extract_user_data_tree(self, r: ExtractionResult):
        """
        Pull the COMPLETE content of /sdcard with the folder structure preserved,
        including the Android folder (Android/media and whatever is accessible in
        Android/data, Android/obb). Nothing is skipped on purpose.

        Works through the top-level items one by one for a continuous log and
        robustness (an error in one folder does not stop the others). The final
        count is computed from the files actually pulled to disk, so it is deterministic.
        """
        tree_dir = os.path.join(self.output_dir, "user_data_tree")
        os.makedirs(tree_dir, exist_ok=True)

        # List of all items in /sdcard including hidden ones (-A), without . and ..
        ls_out = self.adb.shell("ls -A /sdcard/ 2>/dev/null")
        entries = [f.strip() for f in ls_out.split("\n")
                   if f.strip() and f.strip() not in (".", "..")]

        total = len(entries)
        self._log(f"Nalezeno {total} položek v /sdcard – stahuji KOMPLETNÍ strukturu…")

        manifest_entries = []
        errors = []

        for i, name in enumerate(entries, 1):
            if self._stopped():
                break

            remote = f"/sdcard/{name}"
            local = os.path.join(tree_dir, name)

            self._log(f"  [{i}/{total}] Kopíruji: {name}")
            ok, err_msg = self.adb.pull_err(remote, local, timeout=None)
            if ok:
                self._log(f"    ✓ {name}")
            else:
                # adb pull may pull part of a folder even on error (e.g. Android/data
                # with restricted access) – so this is NOT treated as a reason to skip.
                errors.append({"path": remote, "error": err_msg})
                self._log(f"    ⚠ {name} – částečně/nedostupné: {err_msg}")
            manifest_entries.append({
                "remote_path": remote,
                "local_path":  local,
                "status":      "ok" if ok else "partial",
            })

        # Deterministic count: count what is actually on disk
        pulled_files = sum(len(files) for _, _, files in os.walk(tree_dir))
        pulled_folders = sum(1 for _, dirs, _ in os.walk(tree_dir) for _ in dirs)

        if errors:
            self._log(f"  ⚠ {len(errors)} položek se nepodařilo stáhnout kompletně "
                      f"(obvykle Android/data a Android/obb kvůli omezením systému).")

        manifest = {
            "source":          "/sdcard",
            "complete_tree":   True,
            "top_level_items": total,
            "pulled_folders":  pulled_folders,
            "pulled_files":    pulled_files,
            "errors":          errors,
            "entries":         manifest_entries,
        }
        manifest_path = os.path.join(self.output_dir, "user_data_tree_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        r.data = manifest
        r.file_path = tree_dir
        r.success = True
        r.count = pulled_files

    # ── File Analysis (EXIF + metadata) ──────────────────────────────────────

    def _extract_file_analysis(self, r: ExtractionResult):
        """
        Analyse files from already pulled data (media/ or user_data_tree/).
        If no pulled data exists, pull sample images from /sdcard/DCIM.
        Extracts EXIF metadata (GPS, date, camera) and basic stat metadata.
        """
        from PIL import Image, ExifTags, TiffImagePlugin
        import struct

        IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".heic", ".webp", ".bmp"}

        # ── Helper functions ──────────────────────────────────────────────────────

        def _gps_to_decimal(vals, ref: str) -> float:
            """Convert GPS coordinates from DMS to decimal degrees."""
            try:
                def _ratio(v):
                    if isinstance(v, TiffImagePlugin.IFDRational):
                        return float(v)
                    if isinstance(v, tuple) and len(v) == 2:
                        return v[0] / v[1] if v[1] else 0.0
                    return float(v)
                d, m, s = [_ratio(x) for x in vals]
                dec = d + m / 60 + s / 3600
                if ref in ("S", "W"):
                    dec = -dec
                return round(dec, 7)
            except Exception:
                return None

        def _read_exif(path: str) -> dict:
            meta = {}
            try:
                img = Image.open(path)
                meta["format"]      = img.format or ""
                meta["mode"]        = img.mode or ""
                meta["width"]       = img.width
                meta["height"]      = img.height
                meta["megapixels"]  = round(img.width * img.height / 1_000_000, 2)

                raw_exif = img.getexif()
                if not raw_exif:
                    return meta

                # Map tags to readable names
                tag_map = {v: k for k, v in ExifTags.TAGS.items()}

                def _get(name):
                    tid = tag_map.get(name)
                    return raw_exif.get(tid) if tid else None

                # Date taken
                dt_str = _get("DateTimeOriginal") or _get("DateTime")
                if dt_str:
                    meta["date_taken"] = str(dt_str)

                # Camera make and model
                make  = _get("Make")
                model = _get("Model")
                if make:  meta["camera_make"]  = str(make).strip()
                if model: meta["camera_model"] = str(model).strip()

                # Software
                sw = _get("Software")
                if sw: meta["software"] = str(sw).strip()

                # Technical parameters
                for tag_name, key in [
                    ("ExposureTime",  "exposure_time"),
                    ("FNumber",       "f_number"),
                    ("ISOSpeedRatings", "iso"),
                    ("FocalLength",   "focal_length_mm"),
                    ("Flash",         "flash"),
                    ("Orientation",   "orientation"),
                    ("WhiteBalance",  "white_balance"),
                ]:
                    val = _get(tag_name)
                    if val is not None:
                        try:
                            if isinstance(val, TiffImagePlugin.IFDRational):
                                meta[key] = float(val)
                            elif isinstance(val, tuple) and len(val) == 2:
                                meta[key] = val[0] / val[1] if val[1] else 0
                            else:
                                meta[key] = val
                        except Exception:
                            meta[key] = str(val)

                # GPS
                gps_tid = tag_map.get("GPSInfo")
                gps_raw = raw_exif.get_ifd(gps_tid) if gps_tid else {}
                if gps_raw:
                    gps_tags = {v: k for k, v in ExifTags.GPSTAGS.items()}
                    def _gps(name):
                        tid2 = gps_tags.get(name)
                        return gps_raw.get(tid2) if tid2 else None

                    lat_val = _gps("GPSLatitude")
                    lat_ref = _gps("GPSLatitudeRef")
                    lon_val = _gps("GPSLongitude")
                    lon_ref = _gps("GPSLongitudeRef")
                    alt_val = _gps("GPSAltitude")

                    if lat_val and lat_ref:
                        lat = _gps_to_decimal(lat_val, str(lat_ref))
                        if lat is not None:
                            meta["gps_latitude"] = lat
                    if lon_val and lon_ref:
                        lon = _gps_to_decimal(lon_val, str(lon_ref))
                        if lon is not None:
                            meta["gps_longitude"] = lon
                    if alt_val is not None:
                        try:
                            if isinstance(alt_val, TiffImagePlugin.IFDRational):
                                meta["gps_altitude_m"] = round(float(alt_val), 1)
                            elif isinstance(alt_val, tuple) and len(alt_val) == 2:
                                meta["gps_altitude_m"] = round(alt_val[0] / alt_val[1], 1) if alt_val[1] else 0
                        except Exception:
                            pass
                    if "gps_latitude" in meta and "gps_longitude" in meta:
                        meta["gps_maps_url"] = (
                            f"https://www.google.com/maps?q="
                            f"{meta['gps_latitude']},{meta['gps_longitude']}"
                        )

            except Exception as e:
                meta["exif_error"] = str(e)
            return meta

        def _file_stat(path: str) -> dict:
            """Basic file metadata from the file system."""
            try:
                st = os.stat(path)
                return {
                    "size_bytes": st.st_size,
                    "size_kb":    round(st.st_size / 1024, 1),
                    "modified":   datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "created":    datetime.datetime.fromtimestamp(st.st_ctime).strftime("%Y-%m-%d %H:%M:%S"),
                }
            except Exception:
                return {}

        # ── Find files to analyse ─────────────────────────────────────────────

        search_dirs = [
            os.path.join(self.output_dir, "media"),
            os.path.join(self.output_dir, "user_data_tree"),
        ]

        image_files = []
        other_files = []

        for base in search_dirs:
            if not os.path.isdir(base):
                continue
            for root, _, files in os.walk(base):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    ext   = os.path.splitext(fname)[1].lower()
                    if ext in IMAGE_EXTS:
                        image_files.append(fpath)
                    else:
                        other_files.append(fpath)

        # If no media was pulled, pull sample images from DCIM
        temp_dir = None
        if not image_files:
            temp_dir = os.path.join(self.output_dir, "_exif_tmp")
            os.makedirs(temp_dir, exist_ok=True)
            paths_raw = self.adb.shell(
                "find /sdcard/DCIM /sdcard/Pictures -type f "
                r"\( -iname '*.jpg' -o -iname '*.jpeg' \) 2>/dev/null",
                timeout=30
            )
            remote_paths = [p.strip() for p in paths_raw.split("\n") if p.strip()][:100]
            for rp in remote_paths:
                if self._stopped():
                    break
                fname = os.path.basename(rp)
                lp    = os.path.join(temp_dir, fname)
                if self.adb.pull(rp, lp):
                    image_files.append(lp)

        # ── Analyse the files ──────────────────────────────────────────────────

        results_img   = []
        results_other = []

        for fpath in image_files:
            if self._stopped():
                break
            entry = {
                "filename": os.path.basename(fpath),
                "path":     fpath,
                "type":     "image",
            }
            entry.update(_file_stat(fpath))
            entry.update(_read_exif(fpath))
            results_img.append(entry)

        for fpath in other_files[:500]:   # limit for non-images
            if self._stopped():
                break
            ext = os.path.splitext(fpath)[1].lower()
            entry = {
                "filename": os.path.basename(fpath),
                "path":     fpath,
                "type":     ext.lstrip(".") or "unknown",
            }
            entry.update(_file_stat(fpath))
            results_other.append(entry)

        all_results = results_img + results_other

        # ── Summary ───────────────────────────────────────────────────────────

        with_gps  = [e for e in results_img if "gps_latitude" in e]
        with_exif = [e for e in results_img if "date_taken" in e or "camera_make" in e]

        summary = {
            "total_files":    len(all_results),
            "images":         len(results_img),
            "other_files":    len(results_other),
            "with_exif":      len(with_exif),
            "with_gps":       len(with_gps),
            "gps_locations":  [
                {
                    "file": e["filename"],
                    "lat":  e["gps_latitude"],
                    "lon":  e["gps_longitude"],
                    "url":  e.get("gps_maps_url", ""),
                }
                for e in with_gps
            ],
        }

        out = {"summary": summary, "files": all_results}
        path = os.path.join(self.output_dir, "file_analysis.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)

        # Return a simplified list for the GUI
        r.data    = all_results
        r.file_path = path
        r.success = True
        r.count   = len(all_results)

    # ── File Hashes (MD5 + SHA-256) ───────────────────────────────────────────

    def _extract_file_hashes(self, r: ExtractionResult):
        """
        Compute MD5 and SHA-256 for all files already pulled into output_dir.
        If no files were pulled, pull sample files from /sdcard/DCIM.
        """
        import hashlib

        CHUNK = 65536  # 64 KB

        def _hash_file(path: str) -> dict[str, str]:
            md5    = hashlib.md5()
            sha256 = hashlib.sha256()
            try:
                with open(path, "rb") as f:
                    while chunk := f.read(CHUNK):
                        md5.update(chunk)
                        sha256.update(chunk)
                return {"md5": md5.hexdigest(), "sha256": sha256.hexdigest()}
            except Exception as e:
                return {"md5": "error", "sha256": str(e)}

        # Collect all pulled files
        search_dirs = [
            os.path.join(self.output_dir, "media"),
            os.path.join(self.output_dir, "documents"),
            os.path.join(self.output_dir, "user_data_tree"),
        ]

        local_files = []
        for base in search_dirs:
            if not os.path.isdir(base):
                continue
            for root, _, files in os.walk(base):
                for fname in files:
                    local_files.append(os.path.join(root, fname))

        # If nothing was pulled, pull a small sample from DCIM
        temp_dir = None
        if not local_files:
            temp_dir = os.path.join(self.output_dir, "_hash_tmp")
            os.makedirs(temp_dir, exist_ok=True)
            paths_raw = self.adb.shell(
                "find /sdcard/DCIM /sdcard/Download -type f 2>/dev/null",
                timeout=30
            )
            remote_paths = [p.strip() for p in paths_raw.split("\n") if p.strip()][:50]
            for rp in remote_paths:
                if self._stopped():
                    break
                lp = os.path.join(temp_dir, os.path.basename(rp))
                if self.adb.pull(rp, lp):
                    local_files.append(lp)

        # Hash the files
        records = []
        for fpath in local_files:
            if self._stopped():
                break
            stat = os.stat(fpath)
            hashes = _hash_file(fpath)
            records.append({
                "filename":  os.path.basename(fpath),
                "path":      fpath,
                "size_bytes": stat.st_size,
                "md5":       hashes["md5"],
                "sha256":    hashes["sha256"],
                "modified":  datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })

        path = os.path.join(self.output_dir, "file_hashes.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

        r.data    = records
        r.file_path = path
        r.success = True
        r.count   = len(records)

    # ── Deleted Data (root) ───────────────────────────────────────────────────

    # ── Messaging apps (WhatsApp / Viber) – root required ────────────────────

    def _is_rooted(self) -> bool:
        out = self.adb.shell("su -c 'id' 2>/dev/null || su 0 id 2>/dev/null")
        return "uid=0" in out

    def _su_pull_file(self, remote: str, local: str) -> bool:
        """Copy a root-protected file to /sdcard via su, then adb pull it."""
        tmp = f"/sdcard/_pa_tmp_{os.path.basename(remote)}"
        self.adb.shell(f"su -c 'cp \"{remote}\" \"{tmp}\" 2>/dev/null'")
        self.adb.shell(f"su -c 'chmod 666 \"{tmp}\" 2>/dev/null'")
        ok = self.adb.pull(tmp, local)
        self.adb.shell(f"rm -f '{tmp}' 2>/dev/null")
        return ok and os.path.exists(local)

    @staticmethod
    def _unix_ms(ts) -> str:
        """Format a Unix timestamp (seconds or milliseconds) as a string."""
        if not ts:
            return ""
        try:
            val = float(ts)
            if val > 1_000_000_000_000:   # milliseconds
                val /= 1000.0
            return datetime.datetime.fromtimestamp(val).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(ts)

    def _extract_whatsapp(self, r: ExtractionResult):
        import sqlite3 as _sqlite3
        if not self._is_rooted():
            r.error = t("x.no_root_whatsapp")
            r.success = False
            return
        local = os.path.join(self.output_dir, "msgstore.db")
        self._log(t("x.copying").format(name="msgstore.db"))
        if not self._su_pull_file(
            "/data/data/com.whatsapp/databases/msgstore.db", local
        ):
            r.error = t("x.whatsapp_pull_failed")
            r.success = False
            return
        data: list[dict] = []
        try:
            conn = _sqlite3.connect(local)
            conn.row_factory = _sqlite3.Row
            cur = conn.cursor()
            # Modern schema (WhatsApp 2021+): message + chat + jid
            try:
                cur.execute("""
                    SELECT m.timestamp AS ts, m.from_me AS from_me,
                           m.text_data AS body, j.user AS jid
                    FROM message m
                    LEFT JOIN chat c ON m.chat_row_id = c._id
                    LEFT JOIN jid  j ON c.jid_row_id  = j._id
                    WHERE m.text_data IS NOT NULL
                    ORDER BY m.timestamp DESC LIMIT 5000
                """)
                rows = cur.fetchall()
            except Exception:
                # Legacy schema: messages table
                cur.execute("""
                    SELECT timestamp AS ts, key_from_me AS from_me,
                           data AS body, key_remote_jid AS jid
                    FROM messages
                    WHERE data IS NOT NULL
                    ORDER BY timestamp DESC LIMIT 5000
                """)
                rows = cur.fetchall()
            for row in rows:
                data.append({
                    "date":    self._unix_ms(row["ts"]),
                    "chat":    row["jid"] or "",
                    "from_me": t("val.yes") if row["from_me"] else t("val.no"),
                    "body":    row["body"] or "",
                })
            conn.close()
        except Exception as e:
            r.error = str(e)
        path = os.path.join(self.output_dir, "whatsapp.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        r.data = data
        r.file_path = path
        r.count = len(data)
        r.success = bool(data)
        if not data and not r.error:
            r.error = t("x.no_whatsapp")

    def _extract_viber(self, r: ExtractionResult):
        import sqlite3 as _sqlite3
        if not self._is_rooted():
            r.error = t("x.no_root_viber")
            r.success = False
            return
        local = os.path.join(self.output_dir, "viber_messages.db")
        self._log(t("x.copying").format(name="viber_messages"))
        if not self._su_pull_file(
            "/data/data/com.viber.voip/databases/viber_messages", local
        ):
            r.error = t("x.viber_pull_failed")
            r.success = False
            return
        data: list[dict] = []
        try:
            conn = _sqlite3.connect(local)
            conn.row_factory = _sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT date, body, send_type
                FROM messages
                WHERE body IS NOT NULL AND body <> ''
                ORDER BY date DESC LIMIT 5000
            """)
            for row in cur.fetchall():
                data.append({
                    "date":    self._unix_ms(row["date"]),
                    "body":    row["body"] or "",
                    "from_me": t("val.yes") if (row["send_type"] or 0) else t("val.no"),
                })
            conn.close()
        except Exception as e:
            r.error = str(e)
        path = os.path.join(self.output_dir, "viber.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        r.data = data
        r.file_path = path
        r.count = len(data)
        r.success = bool(data)
        if not data and not r.error:
            r.error = t("x.no_viber")

    def _extract_deleted_data(self, r: ExtractionResult):
        """
        Recovery of deleted data from a rooted device.

        Methods:
        1. Root check via `su -c id`
        2. Copy SQLite databases from /data/data via `su cp` + adb pull
           - mmssms.db   → SMS with deleted=1
           - contacts2.db → contacts with deleted=1
           - contacts2.db / calllog.db → calls with deleted=1
        3. Pull WAL files — binary carving of text strings
           (phone numbers, URLs, message text)
        4. Scan /sdcard/.trash and other recycle bins
        """
        import re
        import sqlite3 as _sqlite3

        deleted_dir = os.path.join(self.output_dir, "deleted_data")
        os.makedirs(deleted_dir, exist_ok=True)

        results = {
            "root_available": False,
            "methods_used": [],
            "recovered": {
                "sms":        [],
                "contacts":   [],
                "call_logs":  [],
                "trash_files": [],
                "wal_strings": [],
            },
            "errors": [],
        }

        # ── 1. Root check ─────────────────────────────────────────────────────
        root_out = self.adb.shell("su -c 'id' 2>/dev/null || su 0 id 2>/dev/null")
        is_rooted = "uid=0" in root_out
        results["root_available"] = is_rooted

        if not is_rooted:
            r.error = t("x.no_root_deleted")
            r.success = False
            r.count = 0
            path = os.path.join(self.output_dir, "deleted_data.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            r.data = results
            r.file_path = path
            return

        # ── Helper: copy a protected file via su ──────────────────────────────
        def _su_pull(remote: str, local: str) -> bool:
            tmp = f"/sdcard/_pa_tmp_{os.path.basename(remote)}"
            self.adb.shell(f"su -c 'cp \"{remote}\" \"{tmp}\" 2>/dev/null'")
            ok = self.adb.pull(tmp, local)
            self.adb.shell(f"rm -f '{tmp}' 2>/dev/null")
            return ok and os.path.exists(local)

        # ── Helper: carve text from a binary WAL file ─────────────────────────
        def _wal_carving(wal_path: str) -> list[dict]:
            """
            Walk the WAL file byte by byte and look for UTF-8 text sequences
            longer than 6 characters — typically SMS content, URLs, names.
            """
            found = []
            try:
                with open(wal_path, "rb") as f:
                    data = f.read()
                # Look for printable ASCII/UTF-8 strings of length >= 6
                for m in re.finditer(rb"[\x20-\x7e\xc0-\xfd]{6,}", data):
                    try:
                        text = m.group(0).decode("utf-8", errors="ignore").strip()
                        if len(text) >= 6:
                            found.append({
                                "offset": m.start(),
                                "text": text[:300],
                            })
                    except Exception:
                        pass
            except Exception as e:
                results["errors"].append(f"WAL carving error: {e}")
            # Remove duplicates
            seen = set()
            unique = []
            for item in found:
                if item["text"] not in seen:
                    seen.add(item["text"])
                    unique.append(item)
            return unique[:500]  # limit

        # ── 2a. SMS — mmssms.db ───────────────────────────────────────────────
        sms_db = os.path.join(deleted_dir, "mmssms.db")
        if _su_pull(
            "/data/data/com.android.providers.telephony/databases/mmssms.db",
            sms_db
        ):
            results["methods_used"].append("SQLite scan: mmssms.db (deleted=1)")
            try:
                conn = _sqlite3.connect(sms_db)
                conn.row_factory = _sqlite3.Row
                cur = conn.cursor()
                try:
                    cur.execute(
                        "SELECT address, date, body, type "
                        "FROM sms WHERE deleted=1"
                    )
                    type_map = {"1": t("val.sms_received"), "2": t("val.sms_sent")}
                    for row in cur.fetchall():
                        entry = dict(row)
                        try:
                            ts = int(entry["date"]) // 1000
                            entry["date"] = datetime.datetime.fromtimestamp(ts).strftime(
                                "%Y-%m-%d %H:%M:%S"
                            )
                        except Exception:
                            pass
                        entry["direction"] = type_map.get(str(entry.get("type")), t("val.unknown"))
                        entry["recovery_method"] = "deleted_flag"
                        results["recovered"]["sms"].append(entry)
                except Exception as e:
                    results["errors"].append(f"SMS deleted query: {e}")
                conn.close()
            except Exception as e:
                results["errors"].append(f"mmssms.db open error: {e}")

            # WAL file of the SMS database
            sms_wal = os.path.join(deleted_dir, "mmssms.db-wal")
            if _su_pull(
                "/data/data/com.android.providers.telephony/databases/mmssms.db-wal",
                sms_wal
            ):
                results["methods_used"].append("WAL carving: mmssms.db-wal")
                carved = _wal_carving(sms_wal)
                results["recovered"]["wal_strings"].extend(
                    [{**c, "source": "mmssms.db-wal"} for c in carved]
                )

        # ── 2b. Contacts — contacts2.db ───────────────────────────────────────
        contacts_db = os.path.join(deleted_dir, "contacts2.db")
        if _su_pull(
            "/data/data/com.android.providers.contacts/databases/contacts2.db",
            contacts_db
        ):
            results["methods_used"].append("SQLite scan: contacts2.db (deleted=1)")
            try:
                conn = _sqlite3.connect(contacts_db)
                conn.row_factory = _sqlite3.Row
                cur = conn.cursor()
                try:
                    cur.execute(
                        "SELECT rc._id, rc.display_name, d.data1 AS phone "
                        "FROM raw_contacts rc "
                        "LEFT JOIN data d ON d.raw_contact_id = rc._id "
                        "WHERE rc.deleted = 1 "
                        "GROUP BY rc._id"
                    )
                    for row in cur.fetchall():
                        entry = dict(row)
                        entry["recovery_method"] = "deleted_flag"
                        results["recovered"]["contacts"].append(entry)
                except Exception as e:
                    results["errors"].append(f"Contacts deleted query: {e}")

                # Calls — contacts2.db or a separate calllog.db
                for calls_table_query in [
                    "SELECT number, date, duration, type FROM calls WHERE deleted=1",
                ]:
                    try:
                        cur.execute(calls_table_query)
                        type_map = {"1": t("val.call_incoming"), "2": t("val.call_outgoing"), "3": t("val.call_missed")}
                        for row in cur.fetchall():
                            entry = dict(row)
                            try:
                                ts = int(entry["date"]) // 1000
                                entry["date"] = datetime.datetime.fromtimestamp(ts).strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                )
                            except Exception:
                                pass
                            secs = entry.get("duration", 0)
                            try:
                                entry["duration"] = f"{int(secs) // 60}m {int(secs) % 60}s"
                            except Exception:
                                pass
                            entry["type"] = type_map.get(str(entry.get("type")), str(entry.get("type")))
                            entry["recovery_method"] = "deleted_flag"
                            results["recovered"]["call_logs"].append(entry)
                    except Exception:
                        pass

                conn.close()
            except Exception as e:
                results["errors"].append(f"contacts2.db open error: {e}")

        # Alternative calllog.db
        calllog_db = os.path.join(deleted_dir, "calllog.db")
        if _su_pull(
            "/data/data/com.android.providers.calllogbackup/databases/calllog.db",
            calllog_db
        ):
            results["methods_used"].append("SQLite scan: calllog.db (deleted=1)")
            try:
                conn = _sqlite3.connect(calllog_db)
                cur = conn.cursor()
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='calls'"
                )
                if cur.fetchone():
                    try:
                        cur.execute(
                            "SELECT number, date, duration, type "
                            "FROM calls WHERE deleted=1"
                        )
                        type_map = {"1": t("val.call_incoming"), "2": t("val.call_outgoing"), "3": t("val.call_missed")}
                        for row in cur.fetchall():
                            entry = {
                                "number": row[0], "date": row[1],
                                "duration": row[2], "type": type_map.get(str(row[3]), str(row[3])),
                                "recovery_method": "deleted_flag",
                            }
                            try:
                                ts = int(entry["date"]) // 1000
                                entry["date"] = datetime.datetime.fromtimestamp(ts).strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                )
                            except Exception:
                                pass
                            results["recovered"]["call_logs"].append(entry)
                    except Exception as e:
                        results["errors"].append(f"calllog.db calls query: {e}")
                conn.close()
            except Exception as e:
                results["errors"].append(f"calllog.db open error: {e}")

        # ── 3. Scan recycle bins on /sdcard ───────────────────────────────────
        trash_candidates = [
            "/sdcard/.trash",
            "/sdcard/.Trash",
            "/sdcard/.recycle",
            "/sdcard/DCIM/.trash",
            "/sdcard/.thumbnails/.trash",
        ]
        trash_dir = os.path.join(deleted_dir, "trash")
        for trash_path in trash_candidates:
            if self._stopped():
                break
            ls_out = self.adb.shell(f"ls '{trash_path}' 2>/dev/null")
            if not ls_out or "No such file" in ls_out or "not found" in ls_out:
                continue
            results["methods_used"].append(f"Koš: {trash_path}")
            os.makedirs(trash_dir, exist_ok=True)
            for fname in [f.strip() for f in ls_out.split("\n") if f.strip()]:
                if self._stopped():
                    break
                remote = f"{trash_path}/{fname}"
                local = os.path.join(trash_dir, fname)
                if self.adb.pull(remote, local):
                    results["recovered"]["trash_files"].append({
                        "filename": fname,
                        "source_path": remote,
                        "local_path": local,
                    })

        # ── Results ───────────────────────────────────────────────────────────
        total = sum(
            len(v) for v in results["recovered"].values()
        )
        path = os.path.join(self.output_dir, "deleted_data.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        r.data = results
        r.file_path = path
        r.success = True
        r.count = total

    # ── ADB Backup ────────────────────────────────────────────────────────────

    def _extract_adb_backup(self, r: ExtractionResult):
        """
        Create an ADB backup with 'adb backup' and parse it.
        The backup is a zlib-compressed tar archive with app databases and files.
        Works on Android 4–11. On Android 12+ it needs confirmation on the device
        and the backup may be empty because of system restrictions.
        """
        import zlib
        import tarfile
        import io

        backup_path = os.path.join(self.output_dir, "adb_backup.ab")
        extracted_dir = os.path.join(self.output_dir, "adb_backup_extracted")
        os.makedirs(extracted_dir, exist_ok=True)

        # Ask the user to confirm the backup on the device
        if self._on_notify:
            self._on_notify(t("x.ab_confirm"))

        # Start the backup – the user must confirm it on the device
        self.adb._run(
            ["-s", self.adb.connected_device,
             "backup", "-nosystem", "-noapk", "-all", "-f", backup_path],
            timeout=300,
        )

        if not os.path.exists(backup_path) or os.path.getsize(backup_path) < 4:
            r.error = t("x.ab_failed")
            return

        # ── Parse the .ab file ────────────────────────────────────────────────
        try:
            with open(backup_path, "rb") as f:
                magic = f.readline()
                if b"ANDROID BACKUP" not in magic:
                    r.error = t("x.ab_invalid")
                    return
                f.readline()                         # verze
                compressed = f.readline().strip() == b"1"
                encryption = f.readline().strip()

                if encryption != b"none":
                    r.error = t("x.ab_encrypted")
                    return

                raw = f.read()

            # Empty backup – Android 12+ blocks adb backup for most apps
            if not raw:
                msg = t("x.ab_empty") + "\n" + t("x.skip")
                if self._on_notify:
                    self._on_notify(msg)
                r.error = msg
                return

            data = zlib.decompress(raw) if compressed else raw
            tar = tarfile.open(fileobj=io.BytesIO(data))

            extracted_files = []
            db_files = []
            for member in tar.getmembers():
                if self._stopped():
                    break
                try:
                    tar.extract(member, extracted_dir, set_attrs=False)
                    extracted_files.append(member.name)
                    if member.name.endswith(".db"):
                        db_files.append(os.path.join(extracted_dir, member.name))
                except Exception:
                    pass

        except zlib.error:
            msg = t("x.ab_corrupt") + "\n" + t("x.skip")
            if self._on_notify:
                self._on_notify(msg)
            r.error = msg
            return
        except Exception as e:
            r.error = str(e)
            return

        # ── Parse SQLite databases from the backup ───────────────────────────
        import sqlite3 as _sqlite3

        db_results = {}
        for db_path in db_files:
            db_name = os.path.relpath(db_path, extracted_dir)
            try:
                conn = _sqlite3.connect(db_path)
                conn.row_factory = _sqlite3.Row
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in cur.fetchall()]
                db_results[db_name] = {"tables": tables, "rows": {}}
                for table in tables:
                    try:
                        cur.execute(f"SELECT * FROM [{table}] LIMIT 500")
                        rows = [dict(row) for row in cur.fetchall()]
                        db_results[db_name]["rows"][table] = rows
                    except Exception:
                        pass
                conn.close()
            except Exception:
                pass

        summary = {
            "backup_file":       backup_path,
            "extracted_dir":     extracted_dir,
            "total_files":       len(extracted_files),
            "db_files":          len(db_files),
            "databases":         db_results,
            "file_list":         extracted_files[:1000],
        }

        path = os.path.join(self.output_dir, "adb_backup_summary.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        r.data = summary
        r.file_path = path
        r.success = True
        r.count = len(extracted_files)

    # ── Root Backup /data/data ────────────────────────────────────────────────

    def _extract_root_backup(self, r: ExtractionResult):
        """
        Backup of the complete /data/data via 'adb shell su -c tar'.
        Requires a rooted device. Works on Android 5–15 including Android 12+,
        where 'adb backup' does not work.
        Steps:
          1. Verify root access (su -c id).
          2. Create a tar.gz on the device at /sdcard/forenziq_root.tar.gz.
          3. Pull the file via adb pull.
          4. Extract it, list the files and parse the SQLite databases found.
          5. Delete the temporary file from the device.
        """
        import tarfile
        import io
        import sqlite3 as _sqlite3

        REMOTE_ARCHIVE = "/sdcard/forenziq_root.tar.gz"
        local_archive  = os.path.join(self.output_dir, "root_backup.tar.gz")
        extracted_dir  = os.path.join(self.output_dir, "root_backup_extracted")
        os.makedirs(extracted_dir, exist_ok=True)

        # ── 1. Verify root access ─────────────────────────────────────────────
        if self._on_notify:
            self._on_notify(t("x.root_checking"))

        rc, out, err = self.adb._run(
            ["-s", self.adb.connected_device, "shell", "su -c id"],
            timeout=15,
        )
        if rc != 0 or "uid=0" not in out:
            msg = t("x.root_missing") + "\n" + t("x.skip")
            if self._on_notify:
                self._on_notify(msg)
            r.error = msg
            return

        if self._on_notify:
            self._on_notify(t("x.root_ok"))

        # ── 2. Create the tar archive on the device ───────────────────────────
        # Ignore read errors (some files are locked by the system)
        rc, out, err = self.adb._run(
            ["-s", self.adb.connected_device, "shell",
             f"su -c 'tar -czf {REMOTE_ARCHIVE} /data/data/ 2>/dev/null; echo DONE'"],
            timeout=600,
        )
        if "DONE" not in out and rc != 0:
            msg = t("x.tar_failed").format(err=err or out or t("val.unknown_error")) + "\n" + t("x.skip")
            if self._on_notify:
                self._on_notify(msg)
            r.error = msg
            return

        # ── 3. Pull the archive ───────────────────────────────────────────────
        if self._on_notify:
            self._on_notify(t("x.pulling"))

        pulled = self.adb.pull(REMOTE_ARCHIVE, local_archive, timeout=600)
        if not pulled or not os.path.exists(local_archive) or os.path.getsize(local_archive) < 32:
            msg = t("x.pull_failed") + "\n" + t("x.skip")
            if self._on_notify:
                self._on_notify(msg)
            r.error = msg
            # Cleanup remote even on failure
            self.adb._run(
                ["-s", self.adb.connected_device, "shell",
                 f"su -c 'rm -f {REMOTE_ARCHIVE}'"],
                timeout=15,
            )
            return

        # ── 4. Extract and parse ──────────────────────────────────────────────
        if self._on_notify:
            self._on_notify(t("x.parsing"))

        extracted_files = []
        db_files = []
        try:
            with tarfile.open(local_archive, "r:gz") as tar:
                for member in tar.getmembers():
                    if self._stopped():
                        break
                    try:
                        tar.extract(member, extracted_dir, set_attrs=False)
                        extracted_files.append(member.name)
                        if member.name.endswith(".db"):
                            db_files.append(os.path.join(extracted_dir, member.name))
                    except Exception:
                        pass
        except Exception as e:
            msg = t("x.extract_failed").format(e=e) + "\n" + t("x.skip")
            if self._on_notify:
                self._on_notify(msg)
            r.error = msg
            return
        finally:
            # ── 5. Delete the temporary file from the device ──────────────────
            self.adb._run(
                ["-s", self.adb.connected_device, "shell",
                 f"su -c 'rm -f {REMOTE_ARCHIVE}'"],
                timeout=15,
            )

        # ── Parse SQLite databases ────────────────────────────────────────────
        db_results = {}
        for db_path in db_files:
            if self._stopped():
                break
            db_name = os.path.relpath(db_path, extracted_dir)
            try:
                conn = _sqlite3.connect(db_path)
                conn.row_factory = _sqlite3.Row
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in cur.fetchall()]
                db_results[db_name] = {"tables": tables, "rows": {}}
                for table in tables:
                    try:
                        cur.execute(f"SELECT * FROM [{table}] LIMIT 500")
                        db_results[db_name]["rows"][table] = [dict(row) for row in cur.fetchall()]
                    except Exception:
                        pass
                conn.close()
            except Exception:
                pass

        summary = {
            "archive_file":  local_archive,
            "extracted_dir": extracted_dir,
            "total_files":   len(extracted_files),
            "db_files":      len(db_files),
            "databases":     db_results,
            "file_list":     extracted_files[:2000],
        }

        path = os.path.join(self.output_dir, "root_backup_summary.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        r.data = summary
        r.file_path = path
        r.success = True
        r.count = len(extracted_files)

    # ── App Content Providers ─────────────────────────────────────────────────

    def _extract_app_providers(self, r: ExtractionResult):
        """
        Query the content providers of popular apps and system providers.
        Also pulls backup files from /sdcard (WhatsApp, Telegram, Signal, Viber…).
        Works on Android 4–14 without root.
        """
        import re

        results = {
            "content_providers": {},
            "app_backups":       {},
            "errors":            [],
        }

        # ── System and extended content providers ─────────────────────────────
        providers = {
            "calendar_events":  "content://com.android.calendar/events --projection title:dtstart:dtend:description:eventLocation",
            "calendar_reminders": "content://com.android.calendar/reminders --projection eventId:minutes:method",
            "downloads":        "content://downloads/my_downloads --projection title:uri:totalBytes:lastModified",
            "media_images":     "content://media/external/images/media --projection _display_name:date_taken:_size:latitude:longitude",
            "media_video":      "content://media/external/video/media --projection _display_name:date_taken:_size:duration",
            "media_audio":      "content://media/external/audio/media --projection _display_name:date_added:_size:artist:album",
            "user_dict":        "content://user_dictionary/words --projection word:frequency:locale",
            "call_log_ext":     "content://call_log/calls --projection number:date:duration:type:geocoded_location:subscription_id",
        }

        # Add the WhatsApp provider (different versions)
        wa_providers = {
            "whatsapp_messages":  "content://com.whatsapp.provider.media/item",
            "whatsapp_contacts":  "content://com.whatsapp.provider.media/contact",
            "whatsapp_status":    "content://com.whatsapp.provider.media/status",
        }

        # Messenger providers
        other_providers = {
            "signal_recipients":  "content://org.thoughtcrime.securesms/recipient",
            "viber_calls":        "content://com.viber.voip/calls",
            "skype_contacts":     "content://com.skype.raider/contacts",
        }

        def _query(uri_with_proj: str) -> list[dict]:
            out = self.adb.shell(
                f"content query --uri {uri_with_proj} 2>/dev/null", timeout=20
            )
            rows = []
            for line in (out or "").split("\n"):
                if "Row:" not in line:
                    continue
                row = {}
                for kv in re.finditer(r"(\w+)=([^,\n]+)", line):
                    row[kv.group(1)] = kv.group(2).strip()
                if row:
                    rows.append(row)
            return rows

        for name, uri in {**providers, **wa_providers, **other_providers}.items():
            if self._stopped():
                break
            rows = _query(uri)
            if rows:
                results["content_providers"][name] = rows

        # ── Backup files on /sdcard ───────────────────────────────────────────
        backup_paths = {
            "WhatsApp":         "/sdcard/WhatsApp",
            "Telegram":         "/sdcard/Telegram",
            "Signal":           "/sdcard/Signal",
            "Viber":            "/sdcard/viber/media",
            "MIUI_Backup":      "/sdcard/MIUI/backup",
            "HuaweiBackup":     "/sdcard/HuaweiBackup",
            "SamsungBackup":    "/sdcard/backup",
            "GoogleBackup":     "/sdcard/Android/data/com.google.android.apps.backup",
            "LINEBackup":       "/sdcard/LINE",
        }

        for app_name, remote_dir in backup_paths.items():
            if self._stopped():
                break
            ls = self.adb.shell(f"ls '{remote_dir}' 2>/dev/null")
            if not ls or "No such" in ls:
                continue
            items = [i.strip() for i in ls.split("\n") if i.strip()]
            # Pull the app backup
            local_dir = os.path.join(self.output_dir, "app_backups", app_name)
            os.makedirs(local_dir, exist_ok=True)
            pulled = 0
            for item in items:
                if self._stopped():
                    break
                remote = f"{remote_dir}/{item}"
                local  = os.path.join(local_dir, item)
                if self.adb.pull(remote, local, timeout=180):
                    pulled += 1
            results["app_backups"][app_name] = {
                "remote_dir": remote_dir,
                "files":      items,
                "pulled":     pulled,
            }

        path = os.path.join(self.output_dir, "app_providers.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        total = (
            sum(len(v) for v in results["content_providers"].values())
            + sum(v["pulled"] for v in results["app_backups"].values())
        )
        r.data = results
        r.file_path = path
        r.success = True
        r.count = total

    # ── Usage Stats ───────────────────────────────────────────────────────────

    def _extract_usage_stats(self, r: ExtractionResult):
        """
        Extract app usage statistics and permission access.
        Includes: usage time, launch frequency, location/camera/microphone access.
        Works on Android 5+ without root.
        """
        import re

        results = {
            "usage_summary":   [],
            "app_ops":         {},
            "recent_activity": [],
            "battery_usage":   [],
            "network_usage":   [],
        }

        # ── App usage statistics ──────────────────────────────────────────────
        raw = self.adb.shell("dumpsys usagestats 2>/dev/null", timeout=30)
        current_pkg = None
        for line in (raw or "").split("\n"):
            line = line.strip()
            pkg_m = re.match(r"package=([^\s]+)", line)
            if pkg_m:
                current_pkg = pkg_m.group(1)
            if current_pkg:
                time_m = re.search(r"totalTime=(\d+)", line)
                count_m = re.search(r"count=(\d+)", line)
                last_m  = re.search(r"lastTime=(\d+)", line)
                if time_m and count_m:
                    total_ms = int(time_m.group(1))
                    results["usage_summary"].append({
                        "package":      current_pkg,
                        "total_hours":  round(total_ms / 3_600_000, 2),
                        "launch_count": int(count_m.group(1)),
                        "last_used":    (
                            datetime.datetime.fromtimestamp(int(last_m.group(1)) / 1000)
                            .strftime("%Y-%m-%d %H:%M:%S")
                            if last_m else ""
                        ),
                    })
                    current_pkg = None

        results["usage_summary"].sort(key=lambda x: x.get("total_hours", 0), reverse=True)

        # ── AppOps – permission access (camera, microphone, location…) ───────
        sensitive_ops = {
            op: t(f"perm.{op}") for op in (
                "CAMERA", "RECORD_AUDIO", "ACCESS_FINE_LOCATION",
                "ACCESS_COARSE_LOCATION", "READ_CONTACTS", "READ_SMS",
                "READ_CALL_LOG", "READ_EXTERNAL_STORAGE", "BODY_SENSORS",
                "READ_PHONE_STATE", "SEND_SMS",
            )
        }

        appops_raw = self.adb.shell("dumpsys appops 2>/dev/null", timeout=30)
        current_pkg = None
        for line in (appops_raw or "").split("\n"):
            pkg_m = re.match(r"\s*Package\s+([^\s:]+)", line)
            if pkg_m:
                current_pkg = pkg_m.group(1)
                if current_pkg not in results["app_ops"]:
                    results["app_ops"][current_pkg] = []
            if current_pkg:
                for op_key, op_label in sensitive_ops.items():
                    if op_key in line:
                        access_m = re.search(r"time=([^\s;]+)", line)
                        mode_m   = re.search(r"mode=(\d+)", line)
                        results["app_ops"][current_pkg].append({
                            "operation": op_label,
                            "last_access": access_m.group(1) if access_m else "",
                            "mode": mode_m.group(1) if mode_m else "",
                        })

        # Filter out packages without sensitive access
        results["app_ops"] = {
            k: v for k, v in results["app_ops"].items() if v
        }

        # ── Recent activity ───────────────────────────────────────────────────
        activity_raw = self.adb.shell(
            "dumpsys activity recents 2>/dev/null | head -80", timeout=15
        )
        for line in (activity_raw or "").split("\n"):
            pkg_m = re.search(r"pkg=([^\s]+)", line)
            act_m = re.search(r"act=([^\s]+)", line)
            if pkg_m:
                results["recent_activity"].append({
                    "package":  pkg_m.group(1),
                    "activity": act_m.group(1) if act_m else "",
                })

        # ── Battery usage ─────────────────────────────────────────────────────
        battery_raw = self.adb.shell(
            "dumpsys batterystats --charged 2>/dev/null | grep 'Uid u0a' | head -40",
            timeout=20,
        )
        for line in (battery_raw or "").split("\n"):
            uid_m = re.search(r"Uid u0a(\d+):\s+([\d.]+)%", line)
            if uid_m:
                results["battery_usage"].append({
                    "uid":     f"u0a{uid_m.group(1)}",
                    "percent": uid_m.group(2),
                })

        # ── Network usage ─────────────────────────────────────────────────────
        net_raw = self.adb.shell(
            "dumpsys netstats 2>/dev/null | grep 'ident' | head -30", timeout=15
        )
        for line in (net_raw or "").split("\n"):
            pkg_m = re.search(r"package=([^\s\]]+)", line)
            rx_m  = re.search(r"rxBytes=(\d+)", line)
            tx_m  = re.search(r"txBytes=(\d+)", line)
            if pkg_m and (rx_m or tx_m):
                results["network_usage"].append({
                    "package":  pkg_m.group(1),
                    "rx_mb":    round(int(rx_m.group(1)) / 1_048_576, 2) if rx_m else 0,
                    "tx_mb":    round(int(tx_m.group(1)) / 1_048_576, 2) if tx_m else 0,
                })

        path = os.path.join(self.output_dir, "usage_stats.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        total = (
            len(results["usage_summary"])
            + len(results["app_ops"])
            + len(results["recent_activity"])
        )
        r.data = results
        r.file_path = path
        r.success = True
        r.count = total

    # ── Termux Agent ──────────────────────────────────────────────────────────

    def _extract_termux_agent(self, r: ExtractionResult):
        """
        If Termux + Termux:API are installed on the device, use them as a
        privileged agent to extract data that is not available through plain ADB.
        Termux:API gives access to contacts, SMS, calls, location,
        notifications and other data without root.
        """
        results = {
            "termux_available":     False,
            "termux_api_available": False,
            "contacts":             [],
            "sms":                  [],
            "call_log":             [],
            "location":             {},
            "battery":              {},
            "wifi_info":            {},
            "notifications":        [],
            "clipboard":            "",
            "sensor_list":          [],
            "errors":               [],
        }

        # ── Availability check ──────────────────────────────────────────────
        pkgs = self.adb.shell("pm list packages 2>/dev/null")
        has_termux     = "com.termux" in (pkgs or "")
        has_termux_api = "com.termux.api" in (pkgs or "")

        results["termux_available"]     = has_termux
        results["termux_api_available"] = has_termux_api

        if not has_termux:
            r.error = t("x.no_termux")
            return

        if not has_termux_api:
            r.error = t("x.no_termux_api")
            return

        def _termux(cmd: str, timeout: int = 20) -> str:
            """Run a command in Termux over ADB."""
            return self.adb.shell(
                f"run-as com.termux sh -c '{cmd}' 2>/dev/null", timeout=timeout
            )

        def _am_broadcast(action: str, extras: str = "") -> str:
            """Alternative via a broadcast for Termux:API."""
            return self.adb.shell(
                f"am broadcast -n com.termux.api/.TermuxApiReceiver "
                f"--es method {action} {extras} 2>/dev/null",
                timeout=15,
            )

        # ── Contacts via Termux:API ───────────────────────────────────────────
        try:
            raw = _termux("termux-contact-list 2>/dev/null", timeout=30)
            if raw and raw.strip().startswith("["):
                import json as _json
                results["contacts"] = _json.loads(raw)
        except Exception as e:
            results["errors"].append(f"contacts: {e}")

        # ── SMS via Termux:API ────────────────────────────────────────────────
        try:
            raw = _termux("termux-sms-list -l 500 2>/dev/null", timeout=60)
            if raw and raw.strip().startswith("["):
                import json as _json
                results["sms"] = _json.loads(raw)
        except Exception as e:
            results["errors"].append(f"sms: {e}")

        # ── Call history ──────────────────────────────────────────────────────
        try:
            raw = _termux("termux-call-log -l 200 2>/dev/null", timeout=30)
            if raw and raw.strip().startswith("["):
                import json as _json
                results["call_log"] = _json.loads(raw)
        except Exception as e:
            results["errors"].append(f"call_log: {e}")

        # ── Poloha ────────────────────────────────────────────────────────────
        try:
            raw = _termux("termux-location -p network 2>/dev/null", timeout=20)
            if raw and "{" in raw:
                import json as _json
                results["location"] = _json.loads(raw)
        except Exception as e:
            results["errors"].append(f"location: {e}")

        # ── Baterie ───────────────────────────────────────────────────────────
        try:
            raw = _termux("termux-battery-status 2>/dev/null", timeout=10)
            if raw and "{" in raw:
                import json as _json
                results["battery"] = _json.loads(raw)
        except Exception as e:
            results["errors"].append(f"battery: {e}")

        # ── Wi-Fi information ───────────────────────────────────────────────────
        try:
            raw = _termux("termux-wifi-connectioninfo 2>/dev/null", timeout=10)
            if raw and "{" in raw:
                import json as _json
                results["wifi_info"] = _json.loads(raw)
        except Exception as e:
            results["errors"].append(f"wifi: {e}")

        # ── Notifikace ────────────────────────────────────────────────────────
        try:
            raw = _termux("termux-notification-list 2>/dev/null", timeout=10)
            if raw and raw.strip().startswith("["):
                import json as _json
                results["notifications"] = _json.loads(raw)
        except Exception as e:
            results["errors"].append(f"notifications: {e}")

        # ── Clipboard ─────────────────────────────────────────────────────────
        try:
            results["clipboard"] = _termux("termux-clipboard-get 2>/dev/null", timeout=8)
        except Exception as e:
            results["errors"].append(f"clipboard: {e}")

        # ── Senzory ───────────────────────────────────────────────────────────
        try:
            raw = _termux("termux-sensor -l 2>/dev/null", timeout=10)
            if raw:
                results["sensor_list"] = [l.strip() for l in raw.split("\n") if l.strip()]
        except Exception as e:
            results["errors"].append(f"sensors: {e}")

        path = os.path.join(self.output_dir, "termux_agent.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        total = (
            len(results["contacts"])
            + len(results["sms"])
            + len(results["call_log"])
            + len(results["notifications"])
        )
        r.data = results
        r.file_path = path
        r.success = True
        r.count = total
