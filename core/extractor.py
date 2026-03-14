import os
import json
import threading
import datetime
from typing import Callable, Optional
from .adb_manager import ADBManager


EXTRACTION_MODULES = {
    "device_info":    "Informace o zařízení",
    "contacts":       "Kontakty",
    "sms":            "SMS / MMS",
    "call_logs":      "Historie hovorů",
    "apps":           "Nainstalované aplikace",
    "media":          "Fotky a videa",
    "documents":      "Dokumenty",
    "wifi":           "Wi-Fi sítě",
    "accounts":       "Účty",
    "browser":        "Historie prohlížeče",
    "user_data_tree": "Uživatelská data (se strukturou)",
    "file_analysis":  "Analýza souborů (EXIF a metadata)",
    "file_hashes":    "Hashe souborů (MD5, SHA-256)",
    "deleted_data":   "Smazaná data (root)",
}

# Agentní moduly – rozšířená extrakce pro novější Android
AGENT_MODULES = {
    "adb_backup":    "ADB záloha aplikací",
    "app_providers": "Data aplikací (Content Providers)",
    "usage_stats":   "Statistiky použití aplikací",
    "termux_agent":  "Termux Agent",
}


class ExtractionResult:
    def __init__(self, module: str):
        self.module = module
        self.label = EXTRACTION_MODULES.get(module) or AGENT_MODULES.get(module, module)
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

    def stop(self):
        self._stop_event.set()

    def _stopped(self) -> bool:
        return self._stop_event.is_set()

    def extract_all(self, modules: list[str],
                    on_progress: Callable[[str, int, int], None],
                    on_done: Callable[[list[ExtractionResult]], None],
                    on_module_done: Callable = None):
        """Spustí extrakci vybraných modulů v novém vlákně.

        on_module_done(result, current, total, elapsed_sec, avg_elapsed_sec)
        """
        self._stop_event.clear()

        def _worker():
            results = []
            total = len(modules)
            elapsed_times = []

            for i, module in enumerate(modules):
                if self._stopped():
                    break
                label = EXTRACTION_MODULES.get(module, module)
                on_progress(label, i + 1, total)

                t_start = datetime.datetime.now()
                result = self._extract_module(module)
                elapsed = (datetime.datetime.now() - t_start).total_seconds()
                elapsed_times.append(elapsed)

                results.append(result)

                if on_module_done:
                    avg = sum(elapsed_times) / len(elapsed_times)
                    on_module_done(result, i + 1, total, elapsed, avg)

            on_done(results)

        threading.Thread(target=_worker, daemon=True).start()

    def _extract_module(self, module: str) -> ExtractionResult:
        result = ExtractionResult(module)
        try:
            method = getattr(self, f"_extract_{module}", None)
            if method:
                method(result)
            else:
                result.error = "Modul není implementován"
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
            row["direction"] = "Přijaté" if row.get("type") == "1" else "Odeslané"
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
        type_map = {"1": "Příchozí", "2": "Odchozí", "3": "Zmeškaný"}
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
        apps = []
        for pkg in packages:
            info_raw = self.adb.shell(f"dumpsys package {pkg} | grep -E 'versionName|firstInstallTime|lastUpdateTime'")
            app = {"package": pkg, "version": "N/A", "installed": "N/A", "updated": "N/A"}
            import re
            m = re.search(r"versionName=([^\s]+)", info_raw)
            if m:
                app["version"] = m.group(1)
            apps.append(app)

        path = os.path.join(self.output_dir, "apps.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(apps, f, ensure_ascii=False, indent=2)
        r.data = apps
        r.file_path = path
        r.success = True
        r.count = len(apps)

    # ── Media ────────────────────────────────────────────────────────────────

    def _extract_media(self, r: ExtractionResult):
        media_dir = os.path.join(self.output_dir, "media")
        os.makedirs(media_dir, exist_ok=True)

        # Získej seznam souborů
        paths_raw = self.adb.shell(
            "find /sdcard/DCIM /sdcard/Pictures /sdcard/Movies "
            "-type f 2>/dev/null"
        )
        files = [p.strip() for p in paths_raw.split("\n") if p.strip()]

        pulled = 0
        for remote_path in files:
            if self._stopped():
                break
            filename = os.path.basename(remote_path)
            local_path = os.path.join(media_dir, filename)
            if self.adb.pull(remote_path, local_path):
                pulled += 1

        # Ulož manifest
        manifest = {"total_found": len(files), "total_pulled": pulled, "files": files}
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

        pulled = 0
        for remote_path in files:
            if self._stopped():
                break
            filename = os.path.basename(remote_path)
            local_path = os.path.join(docs_dir, filename)
            if self.adb.pull(remote_path, local_path):
                pulled += 1

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
        Stáhne uživatelská data z /sdcard se zachováním původní adresářové struktury.
        Používá `adb pull /sdcard/ <cíl>` — Android zachová celou hierarchii.
        Systémové složky (Android/data, Android/obb) jsou přeskočeny.
        """
        tree_dir = os.path.join(self.output_dir, "user_data_tree")
        os.makedirs(tree_dir, exist_ok=True)

        # Zjisti seznam složek v /sdcard (jedna úroveň)
        ls_out = self.adb.shell("ls /sdcard/")
        folders = [f.strip() for f in ls_out.split("\n") if f.strip()]

        # Přeskoč systémové složky Androidu
        skip = {"Android"}

        pulled_files = 0
        pulled_folders = 0
        skipped = []
        manifest_entries = []

        for folder in folders:
            if self._stopped():
                break

            if folder in skip:
                skipped.append(folder)
                continue

            remote = f"/sdcard/{folder}"
            local  = os.path.join(tree_dir, folder)
            os.makedirs(local, exist_ok=True)

            ok = self.adb.pull(remote, local, timeout=600)
            if ok:
                pulled_folders += 1
                # Spočítej stažené soubory
                for _, _, files in os.walk(local):
                    pulled_files += len(files)
            manifest_entries.append({
                "remote_path": remote,
                "local_path":  local,
                "status":      "ok" if ok else "error",
            })

        manifest = {
            "source":          "/sdcard",
            "pulled_folders":  pulled_folders,
            "pulled_files":    pulled_files,
            "skipped_folders": skipped,
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
        Analyzuje soubory z již stažených dat (media/ nebo user_data_tree/).
        Pokud žádná stažená data neexistují, stáhne vzorové obrázky z /sdcard/DCIM.
        Extrahuje EXIF metadata (GPS, datum, fotoaparát) a základní stat metadata.
        """
        from PIL import Image, ExifTags, TiffImagePlugin
        import struct

        IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".heic", ".webp", ".bmp"}

        # ── Pomocné funkce ────────────────────────────────────────────────────

        def _gps_to_decimal(vals, ref: str) -> float:
            """Převede GPS souřadnice z DMS na desetinná čísla."""
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

                # Mapování tagů na čitelné názvy
                tag_map = {v: k for k, v in ExifTags.TAGS.items()}

                def _get(name):
                    tid = tag_map.get(name)
                    return raw_exif.get(tid) if tid else None

                # Datum pořízení
                dt_str = _get("DateTimeOriginal") or _get("DateTime")
                if dt_str:
                    meta["date_taken"] = str(dt_str)

                # Výrobce a model fotoaparátu
                make  = _get("Make")
                model = _get("Model")
                if make:  meta["camera_make"]  = str(make).strip()
                if model: meta["camera_model"] = str(model).strip()

                # Software
                sw = _get("Software")
                if sw: meta["software"] = str(sw).strip()

                # Technické parametry
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
            """Základní metadata souboru ze systému."""
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

        # ── Najdi soubory ke analýze ──────────────────────────────────────────

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

        # Pokud nebyla stažena žádná média, stáhni vzorové obrázky z DCIM
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

        # ── Analyzuj soubory ──────────────────────────────────────────────────

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

        for fpath in other_files[:500]:   # limit pro ne-obrázky
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

        # ── Souhrn ────────────────────────────────────────────────────────────

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

        # Pro GUI vrátíme zjednodušený seznam
        r.data    = all_results
        r.file_path = path
        r.success = True
        r.count   = len(all_results)

    # ── File Hashes (MD5 + SHA-256) ───────────────────────────────────────────

    def _extract_file_hashes(self, r: ExtractionResult):
        """
        Vypočítá MD5 a SHA-256 pro všechny již stažené soubory v output_dir.
        Pokud žádné soubory nebyly staženy, stáhne vzorové soubory z /sdcard/DCIM.
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

        # Shromáždi všechny stažené soubory
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

        # Pokud nic nebylo staženo, stáhni malý vzorek z DCIM
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

        # Hashuj soubory
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

    def _extract_deleted_data(self, r: ExtractionResult):
        """
        Obnova smazaných dat z rootovaného zařízení.

        Metody:
        1. Root check přes `su -c id`
        2. Kopírování SQLite databází z /data/data přes `su cp` + adb pull
           - mmssms.db   → SMS s deleted=1
           - contacts2.db → kontakty s deleted=1
           - contacts2.db / calllog.db → hovory s deleted=1
        3. Stažení WAL souborů — binární carving pro textové řetězce
           (telefonní čísla, URL, text zpráv)
        4. Skenování /sdcard/.trash a dalších odpadkových košů
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
            r.error = "Root přístup není dostupný — smazaná data nelze obnovit."
            r.success = False
            r.count = 0
            path = os.path.join(self.output_dir, "deleted_data.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            r.data = results
            r.file_path = path
            return

        # ── Pomocná funkce: kopírování chráněného souboru přes su ─────────────
        def _su_pull(remote: str, local: str) -> bool:
            tmp = f"/sdcard/_pa_tmp_{os.path.basename(remote)}"
            self.adb.shell(f"su -c 'cp \"{remote}\" \"{tmp}\" 2>/dev/null'")
            ok = self.adb.pull(tmp, local)
            self.adb.shell(f"rm -f '{tmp}' 2>/dev/null")
            return ok and os.path.exists(local)

        # ── Pomocná funkce: carving textu z WAL binárního souboru ─────────────
        def _wal_carving(wal_path: str) -> list[dict]:
            """
            Prochází WAL soubor po bytech a hledá UTF-8 textové sekvence
            delší než 6 znaků — typicky obsah SMS, URL, jména.
            """
            found = []
            try:
                with open(wal_path, "rb") as f:
                    data = f.read()
                # Hledej tisknutelné ASCII/UTF-8 řetězce délky >= 6
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
            # Odeber duplicity
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
                    type_map = {"1": "Přijaté", "2": "Odeslané"}
                    for row in cur.fetchall():
                        entry = dict(row)
                        try:
                            ts = int(entry["date"]) // 1000
                            entry["date"] = datetime.datetime.fromtimestamp(ts).strftime(
                                "%Y-%m-%d %H:%M:%S"
                            )
                        except Exception:
                            pass
                        entry["direction"] = type_map.get(str(entry.get("type")), "Neznámý")
                        entry["recovery_method"] = "deleted_flag"
                        results["recovered"]["sms"].append(entry)
                except Exception as e:
                    results["errors"].append(f"SMS deleted query: {e}")
                conn.close()
            except Exception as e:
                results["errors"].append(f"mmssms.db open error: {e}")

            # WAL soubor ke SMS databázi
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

        # ── 2b. Kontakty — contacts2.db ───────────────────────────────────────
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

                # Hovory — contacts2.db nebo samostatný calllog.db
                for calls_table_query in [
                    "SELECT number, date, duration, type FROM calls WHERE deleted=1",
                ]:
                    try:
                        cur.execute(calls_table_query)
                        type_map = {"1": "Příchozí", "2": "Odchozí", "3": "Zmeškaný"}
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

        # Alternativní calllog.db
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
                        type_map = {"1": "Příchozí", "2": "Odchozí", "3": "Zmeškaný"}
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

        # ── 3. Skenování košů na /sdcard ──────────────────────────────────────
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

        # ── Výsledky ──────────────────────────────────────────────────────────
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
        Vytvoří ADB zálohu pomocí 'adb backup' a parsuje ji.
        Záloha je zlib-komprimovaný tar archiv obsahující databáze a soubory aplikací.
        Funguje na Android 4–11. Na Android 12+ vyžaduje potvrzení na zařízení
        a záloha může být prázdná kvůli omezením systému.
        """
        import zlib
        import tarfile
        import io

        backup_path = os.path.join(self.output_dir, "adb_backup.ab")
        extracted_dir = os.path.join(self.output_dir, "adb_backup_extracted")
        os.makedirs(extracted_dir, exist_ok=True)

        # Spustit zálohu – uživatel musí potvrdit na zařízení
        self.adb._run(
            ["-s", self.adb.connected_device,
             "backup", "-nosystem", "-noapk", "-all", "-f", backup_path],
            timeout=300,
        )

        if not os.path.exists(backup_path) or os.path.getsize(backup_path) < 24:
            r.error = (
                "Záloha se nezdařila nebo nebyla potvrzena na zařízení. "
                "Na displeji telefonu klikněte na 'Zálohovat data'."
            )
            return

        # ── Parsování .ab souboru ─────────────────────────────────────────────
        try:
            with open(backup_path, "rb") as f:
                magic = f.readline()
                if b"ANDROID BACKUP" not in magic:
                    r.error = "Neplatný formát zálohy (.ab)"
                    return
                f.readline()                         # verze
                compressed = f.readline().strip() == b"1"
                encryption = f.readline().strip()

                if encryption != b"none":
                    r.error = (
                        "Záloha je šifrována heslem – nelze parsovat bez hesla. "
                        "Zkuste zálohu bez hesla."
                    )
                    return

                raw = f.read()

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
            r.error = "Chyba dekomprese zálohy – záloha je prázdná nebo poškozená."
            return
        except Exception as e:
            r.error = str(e)
            return

        # ── Parsování SQLite databází z zálohy ───────────────────────────────
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

    # ── App Content Providers ─────────────────────────────────────────────────

    def _extract_app_providers(self, r: ExtractionResult):
        """
        Dotazuje se na content providery populárních aplikací a systémové providery.
        Stahuje také záložní soubory z /sdcard (WhatsApp, Telegram, Signal, Viber…).
        Funguje na Android 4–14 bez rootu.
        """
        import re

        results = {
            "content_providers": {},
            "app_backups":       {},
            "errors":            [],
        }

        # ── Systémové a rozšířené content providery ───────────────────────────
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

        # Přidej provider pro WhatsApp (různé verze)
        wa_providers = {
            "whatsapp_messages":  "content://com.whatsapp.provider.media/item",
            "whatsapp_contacts":  "content://com.whatsapp.provider.media/contact",
            "whatsapp_status":    "content://com.whatsapp.provider.media/status",
        }

        # Messenger providery
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

        # ── Záložní soubory na /sdcard ────────────────────────────────────────
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
            # Stáhnout zálohu aplikace
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
        Extrahuje statistiky použití aplikací a přístupu k oprávněním.
        Zahrnuje: doby používání, frekvence spouštění, přístup k poloze/kameře/mikrofonu.
        Funguje na Android 5+ bez rootu.
        """
        import re

        results = {
            "usage_summary":   [],
            "app_ops":         {},
            "recent_activity": [],
            "battery_usage":   [],
            "network_usage":   [],
        }

        # ── Statistiky použití aplikací ───────────────────────────────────────
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

        # ── AppOps – přístupy k oprávněním (kamera, mikrofon, poloha…) ───────
        sensitive_ops = {
            "CAMERA":             "Kamera",
            "RECORD_AUDIO":       "Mikrofon",
            "ACCESS_FINE_LOCATION": "Přesná poloha",
            "ACCESS_COARSE_LOCATION": "Přibližná poloha",
            "READ_CONTACTS":      "Kontakty (čtení)",
            "READ_SMS":           "SMS (čtení)",
            "READ_CALL_LOG":      "Historie hovorů",
            "READ_EXTERNAL_STORAGE": "Úložiště (čtení)",
            "BODY_SENSORS":       "Senzory těla",
            "READ_PHONE_STATE":   "Stav telefonu",
            "SEND_SMS":           "Odesílání SMS",
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

        # Odfiltruj balíčky bez citlivých přístupů
        results["app_ops"] = {
            k: v for k, v in results["app_ops"].items() if v
        }

        # ── Nedávná aktivita ──────────────────────────────────────────────────
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

        # ── Spotřeba baterie ──────────────────────────────────────────────────
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

        # ── Síťová spotřeba ───────────────────────────────────────────────────
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
        Pokud je na zařízení nainstalován Termux + Termux:API, použije jej
        jako privilegovaný agent pro extrakci dat nedostupných běžným ADB.
        Termux:API poskytuje přístup ke kontaktům, SMS, hovorům, poloze,
        notifikacím a dalším datům bez rootu.
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

        # ── Kontrola dostupnosti ──────────────────────────────────────────────
        pkgs = self.adb.shell("pm list packages 2>/dev/null")
        has_termux     = "com.termux" in (pkgs or "")
        has_termux_api = "com.termux.api" in (pkgs or "")

        results["termux_available"]     = has_termux
        results["termux_api_available"] = has_termux_api

        if not has_termux:
            r.error = (
                "Termux není nainstalován. "
                "Nainstalujte Termux a Termux:API z F-Droid pro plnou extrakci."
            )
            return

        if not has_termux_api:
            r.error = (
                "Termux:API není nainstalováno. "
                "Nainstalujte balíček 'termux-api' v Termuxu: pkg install termux-api"
            )
            return

        def _termux(cmd: str, timeout: int = 20) -> str:
            """Spustí příkaz v Termuxu přes ADB."""
            return self.adb.shell(
                f"run-as com.termux sh -c '{cmd}' 2>/dev/null", timeout=timeout
            )

        def _am_broadcast(action: str, extras: str = "") -> str:
            """Alternativa přes broadcast pro Termux:API."""
            return self.adb.shell(
                f"am broadcast -n com.termux.api/.TermuxApiReceiver "
                f"--es method {action} {extras} 2>/dev/null",
                timeout=15,
            )

        # ── Kontakty přes Termux:API ──────────────────────────────────────────
        try:
            raw = _termux("termux-contact-list 2>/dev/null", timeout=30)
            if raw and raw.strip().startswith("["):
                import json as _json
                results["contacts"] = _json.loads(raw)
        except Exception as e:
            results["errors"].append(f"contacts: {e}")

        # ── SMS přes Termux:API ───────────────────────────────────────────────
        try:
            raw = _termux("termux-sms-list -l 500 2>/dev/null", timeout=60)
            if raw and raw.strip().startswith("["):
                import json as _json
                results["sms"] = _json.loads(raw)
        except Exception as e:
            results["errors"].append(f"sms: {e}")

        # ── Historie hovorů ───────────────────────────────────────────────────
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

        # ── Wi-Fi informace ───────────────────────────────────────────────────
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

        # ── Schránka ──────────────────────────────────────────────────────────
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
