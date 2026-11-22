"""
core/backup_importer.py – Import and extraction of data from mobile backups.

Supported formats:
  • Samsung Smart Switch (folder / .zip) – decrypted (core/smartswitch.py): contacts,
    call log, Google Messages SMS/MMS/RCS, Wi-Fi, apps, Samsung Internet, media
  • Huawei HiSuite / Phone Clone (.hbk = ZIP / folder)
  • MIUI / Xiaomi Backup (.bak proprietary wrapper, AllBackup folder, ZIP)
  • Google Takeout (.zip / folder)
  • Sony Xperia Backup (.bk = ZIP)
  • OnePlus / OxygenOS (.tar.gz / folder)
  • ADB backup (.ab – zlib-deflated tar)

Modules: contacts, SMS/MMS, calls, media, Wi-Fi, calendar, browser history,
notes, installed apps.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import threading
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from core.i18n import t


# ── Formats and modules ───────────────────────────────────────────────────────

BACKUP_FORMATS: dict[str, str] = {
    "auto":    "bkp.fmt_auto",
    "samsung": "bkp.fmt_samsung",
    "huawei":  "bkp.fmt_huawei",
    "miui":    "bkp.fmt_miui",
    "takeout": "bkp.fmt_takeout",
    "sony":    "bkp.fmt_sony",
    "oneplus": "bkp.fmt_oneplus",
    "oppo":    "bkp.fmt_oppo",
    "vivo":    "bkp.fmt_vivo",
    "motorola": "bkp.fmt_motorola",
    "lg":      "bkp.fmt_lg",
    "adb":     "bkp.fmt_adb",
}

# OEM detection markers: substrings (lowercase) in archive/file names → format key.
# Checked before the generic Samsung fallback; order within is specific→generic.
_OEM_MARKERS: list[tuple[str, tuple[str, ...]]] = [
    ("oppo",     ("coloros", "heytap", "com.coloros", "com.oppo",
                  "com.heytap", "realme", ".nbr")),
    ("vivo",     ("bbkcloud", "easyshare", ".vivobackup", "com.vivo", "com.bbk")),
    ("motorola", ("motorola", "motomigrate", "com.motorola")),
    ("lg",       ("lgbackup", ".lbf", "com.lge")),
]


def _classify_oem(names) -> str | None:
    """Return an OEM format key if any name matches its markers, else None."""
    lowered = [str(n).lower() for n in names]
    for fmt_key, markers in _OEM_MARKERS:
        if any(m in n for n in lowered for m in markers):
            return fmt_key
    return None

IMPORT_MODULES: dict[str, str] = {
    "bkp_contacts": "bkp.mod_contacts",
    "bkp_sms":      "bkp.mod_sms",
    "bkp_calls":    "bkp.mod_calls",
    "bkp_media":    "bkp.mod_media",
    "bkp_wifi":     "bkp.mod_wifi",
    "bkp_calendar": "bkp.mod_calendar",
    "bkp_browser":  "bkp.mod_browser",
    "bkp_notes":    "bkp.mod_notes",
    "bkp_apps":     "bkp.mod_apps",
}


def _fmt_label(fmt_key: str) -> str:
    """Return the translated format name."""
    i18n_key = BACKUP_FORMATS.get(fmt_key, fmt_key)
    return t(i18n_key) if i18n_key.startswith("bkp.") else i18n_key


def _mod_label(mod_key: str) -> str:
    """Return the translated module name."""
    i18n_key = IMPORT_MODULES.get(mod_key, mod_key)
    return t(i18n_key) if i18n_key.startswith("bkp.") else i18n_key


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ImportResult:
    key: str
    ok: bool
    records: int = 0
    error: str = ""
    elapsed: float = 0.0


# ── Helper functions ──────────────────────────────────────────────────────────

def _ts_to_str(ts) -> str:
    """Convert a Unix timestamp (ms or s) to a human-readable string."""
    if not ts:
        return ""
    try:
        v = int(ts)
        if v > 1_000_000_000_000:
            v //= 1000
        return datetime.fromtimestamp(v).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def _chrome_ts(ts) -> str:
    """Convert a WebKit/Chrome timestamp (microseconds since 1601-01-01) to a string."""
    if not ts:
        return ""
    try:
        v = int(ts)
        # WebKit epoch starts 1601-01-01; 11644473600 s between then and Unix epoch
        unix = v / 1_000_000 - 11644473600
        if unix <= 0:
            return ""
        return datetime.fromtimestamp(unix).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def _vcf_lines(text: str) -> list[str]:
    """Unfold vCard lines: RFC folding (leading space) and quoted-printable soft breaks."""
    out: list[str] = []
    for raw in text.splitlines():
        if out and raw[:1] in (" ", "\t") and not raw.strip().upper().startswith(("BEGIN:", "END:")):
            out[-1] += raw[1:]
        elif out and out[-1].endswith("=") and "QUOTED-PRINTABLE" in out[-1].upper():
            out[-1] = out[-1][:-1] + raw.strip()
        else:
            out.append(raw)
    return out


def _vcf_value(key_part: str, val: str) -> str:
    """Decode a vCard value (ENCODING=QUOTED-PRINTABLE / B, CHARSET=…)."""
    params = key_part.upper()
    charset = "utf-8"
    m = re.search(r"CHARSET=([\w-]+)", params)
    if m:
        charset = m.group(1).lower()
    try:
        if "QUOTED-PRINTABLE" in params:
            import quopri
            return quopri.decodestring(val.encode("ascii", "replace")).decode(charset, "replace")
        if "ENCODING=B" in params or "ENCODING=BASE64" in params:
            import base64
            return base64.b64decode(val).decode(charset, "replace")
    except (LookupError, ValueError):
        pass
    return val


def _parse_vcf(text: str) -> list[dict]:
    """Minimal vCard 2.1 / 3.0 file parser."""
    contacts: list[dict] = []
    cur: dict | None = None

    for raw_line in _vcf_lines(text):
        line = raw_line.strip()

        if line.upper() == "BEGIN:VCARD":
            cur = {"name": "", "phone": [], "email": []}
        elif line.upper() == "END:VCARD":
            if cur and (cur["name"] or cur["phone"]):
                contacts.append(cur)
            cur = None
        elif cur is None:
            continue
        elif ":" not in line:
            continue
        else:
            key_part, _, val = line.partition(":")
            val = _vcf_value(key_part, val)
            key_upper = key_part.upper().split(";")[0].split(".")[-1]
            if key_upper == "FN":
                cur["name"] = val.strip()
            elif key_upper == "N" and not cur["name"]:
                parts = val.split(";")
                cur["name"] = " ".join(p.strip() for p in parts[:2] if p.strip())
            elif key_upper == "TEL":
                cur["phone"].append(val.strip())
            elif key_upper == "EMAIL":
                cur["email"].append(val.strip())

    return contacts


# ── Android backup (.ab) – also embedded in MIUI .bak files ───────────────────

AB_MAGIC = b"ANDROID BACKUP\n"
_AB_CHUNK = 1 << 20


class AbPasswordRequired(Exception):
    """The Android backup is encrypted and no password was given."""


class AbWrongPassword(Exception):
    """The Android backup password is wrong."""


def find_ab_offset(path: Path, limit: int = 65536) -> int:
    """Offset of the 'ANDROID BACKUP' header within the first bytes of a file, or -1.

    A plain .ab starts with it (offset 0); a MIUI .bak has its own short text
    header first, followed by a regular Android backup stream.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(limit)
    except OSError:
        return -1
    return head.find(AB_MAGIC)


def ab_is_encrypted(path: Path) -> bool:
    """True if the file contains an Android backup stream encrypted with a password."""
    off = find_ab_offset(path)
    if off < 0:
        return False
    try:
        with open(path, "rb") as f:
            f.seek(off)
            lines = [f.readline() for _ in range(4)]
        return lines[3].strip() not in (b"none", b"")
    except OSError:
        return False


def _aes_cbc_decrypt_unpad(key: bytes, iv: bytes, data: bytes) -> bytes:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    raw = dec.update(data) + dec.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(raw) + unpadder.finalize()


def extract_android_backup(src: Path, offset: int, tar_out: Path,
                           password: str = "",
                           stopped: Optional[Callable[[], bool]] = None) -> dict:
    """Convert an Android backup stream (at `offset` in `src`) into a plain tar file.

    Format (Android BackupManagerService):
        ANDROID BACKUP\\n <version>\\n <compressed 0|1>\\n <encryption none|AES-256>\\n
        [AES-256 only: user salt hex, checksum salt hex, PBKDF2 rounds,
                       user key IV hex, encrypted master key blob hex – one per line]
        body = [AES-256-CBC(master key)] ( [zlib deflate] ( tar ) )

    The password key is PBKDF2-HMAC-SHA1(password, user salt, rounds, 32 bytes)
    (UTF-8 password bytes for format version ≥ 2, low 8 bits per char for v1);
    it decrypts the blob [len][master IV][len][master key][len][checksum].
    The body is processed in chunks, so multi-GB backups do not need much memory.
    Raises AbPasswordRequired / AbWrongPassword / ValueError.
    """
    import hashlib
    with open(src, "rb") as f:
        f.seek(offset)
        if f.readline() != AB_MAGIC:
            raise ValueError("not an Android backup stream")
        try:
            version = int(f.readline().strip() or b"0")
        except ValueError:
            version = 0
        compressed = f.readline().strip() == b"1"
        encryption = f.readline().strip().decode("ascii", "replace")

        decryptor = unpadder = None
        if encryption == "AES-256":
            try:
                user_salt = bytes.fromhex(f.readline().strip().decode())
                f.readline()                                   # checksum salt (not needed)
                rounds = int(f.readline().strip())
                user_iv = bytes.fromhex(f.readline().strip().decode())
                mk_blob = bytes.fromhex(f.readline().strip().decode())
            except ValueError as e:
                raise ValueError(f"damaged encryption header: {e}")
            if not password:
                raise AbPasswordRequired()
            if version >= 2:
                pw = password.encode("utf-8")
            else:
                pw = bytes(ord(c) & 0xFF for c in password)
            user_key = hashlib.pbkdf2_hmac("sha1", pw, user_salt, rounds, 32)
            try:
                blob = _aes_cbc_decrypt_unpad(user_key, user_iv, mk_blob)
                iv_len = blob[0]
                mk_iv = blob[1:1 + iv_len]
                mk_len = blob[1 + iv_len]
                mk = blob[2 + iv_len:2 + iv_len + mk_len]
            except (ValueError, IndexError):
                raise AbWrongPassword()
            if iv_len != 16 or mk_len != 32 or len(mk) != 32:
                raise AbWrongPassword()
            from cryptography.hazmat.primitives import padding
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            decryptor = Cipher(algorithms.AES(mk), modes.CBC(mk_iv)).decryptor()
            unpadder = padding.PKCS7(128).unpadder()
        elif encryption not in ("none", ""):
            raise ValueError(f"unsupported encryption: {encryption}")

        inflater = None
        first = True
        with open(tar_out, "wb") as out:
            while True:
                if stopped and stopped():
                    raise ValueError("stopped")
                chunk = f.read(_AB_CHUNK)
                if not chunk:
                    break
                if decryptor:
                    chunk = unpadder.update(decryptor.update(chunk))
                if compressed and chunk:
                    if first:
                        # Android writes a zlib stream (header 0x78); accept raw deflate too
                        inflater = zlib.decompressobj() if chunk[0] == 0x78 else zlib.decompressobj(-15)
                        first = False
                    chunk = inflater.decompress(chunk)
                out.write(chunk)
            tail = b""
            if decryptor:
                try:
                    tail = unpadder.update(decryptor.finalize()) + unpadder.finalize()
                except ValueError:
                    raise ValueError("damaged backup (decryption padding)")
            if compressed:
                if inflater is None:
                    inflater = zlib.decompressobj() if tail[:1] == b"\x78" else zlib.decompressobj(-15)
                tail = inflater.decompress(tail) + inflater.flush()
            out.write(tail)
    return {"version": version, "compressed": compressed, "encryption": encryption or "none"}


def safe_extract_tar(tar: tarfile.TarFile, dest: str) -> int:
    """Extract a tar from an untrusted backup without path traversal or links
    pointing outside `dest` (tarfile 'data' filter). Members that cannot be
    extracted safely are skipped. Returns the number of extracted members."""
    n = 0
    use_filter = hasattr(tarfile, "data_filter")
    for member in tar:
        try:
            if use_filter:
                tar.extract(member, dest, filter="data")
            else:
                name = member.name.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/") or member.issym() or member.islnk():
                    continue
                tar.extract(member, dest)
            n += 1
        except Exception:
            continue
    return n


def _open_db(path: Path) -> Optional[sqlite3.Connection]:
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def _safe_query(conn: sqlite3.Connection, sql: str, params=()) -> list:
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:
        return []


# ── Main class ────────────────────────────────────────────────────────────────

class BackupImporter:
    def __init__(self, backup_path: str, output_dir: str, fmt: str = "auto",
                 password: str = ""):
        self.backup_path = Path(backup_path)
        self.output_dir = Path(output_dir)
        self.fmt = fmt
        self.password = password          # for encrypted Android / MIUI backups
        self._tmp: str | None = None
        self._extracted_root: Path | None = None
        self._extra_roots: list[Path] = []   # e.g. unwrapped MIUI .bak contents
        self._media_roots: list[Path] = []   # original Smart Switch roots (plain media)
        self.smartswitch_info: dict = {}
        self.detected_format: str = ""
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def _stopped(self) -> bool:
        return self._stop_event.is_set()

    def needs_password(self) -> bool:
        """True if the backup is an encrypted Android/MIUI backup (password required)."""
        p = self.backup_path
        try:
            if p.is_file():
                return ab_is_encrypted(p)
            if p.is_dir():
                # MIUI AllBackup folder: all per-app .bak files share the password
                for bak in p.rglob("*.bak"):
                    return ab_is_encrypted(bak)
        except OSError:
            pass
        return False

    # ── Format detection ──────────────────────────────────────────────────────

    def detect_format(self) -> str:
        p = self.backup_path
        suffix = p.suffix.lower()

        if suffix == ".ab":
            return "adb"
        if suffix == ".hbk":
            return "huawei"
        if suffix == ".bk":
            return "sony"
        if suffix == ".bak":
            return "miui"
        if suffix == ".lbf":
            return "lg"
        if suffix == ".nbr":
            return "oppo"

        if suffix == ".zip" or (p.is_file() and zipfile.is_zipfile(str(p))):
            return self._detect_zip(p)

        if suffix in (".gz", ".tar", ".tgz") or (p.is_file() and tarfile.is_tarfile(str(p))):
            return self._detect_tar(p)

        if p.is_dir():
            return self._detect_folder(p)

        return "unknown"

    def _detect_zip(self, path: Path) -> str:
        try:
            with zipfile.ZipFile(str(path)) as z:
                names = [n.lower() for n in z.namelist()]
        except Exception:
            return "samsung"

        if any(n.endswith("backuphistoryinfo.xml") for n in names):
            return "samsung"
        if any("takeout/" in n for n in names):
            return "takeout"
        if any("miui/apps" in n or "descript.xml" in n for n in names):
            return "miui"
        oem = _classify_oem(names)
        if oem:
            return oem
        if any("calllogs.xml" in n or "sms.xml" in n for n in names):
            return "huawei"
        if any(n.endswith(".vcf") and ("contact" in n) for n in names):
            return "huawei"
        # Samsung SmartSwitch markers
        if any(self._is_samsung_name(n) for n in names):
            return "samsung"
        if any("contacts2.db" in n or "mmssms.db" in n for n in names):
            return "samsung"
        if any("sony" in n or "xperia" in n for n in names):
            return "sony"
        return "samsung"

    @staticmethod
    def _is_samsung_name(n: str) -> bool:
        """Heuristic match for Samsung SmartSwitch file/folder names."""
        n = n.lower()
        return (
            n.endswith(".spb") or n.endswith(".sdoc") or n.endswith(".sdocx")
            or "smartswitch" in n or "sec.android" in n or "sbrowser" in n
            or any(f"/{cat}/" in f"/{n}/" for cat in (
                "contact", "calllog", "message", "memo", "snote"))
        )

    def _detect_tar(self, path: Path) -> str:
        try:
            with tarfile.open(str(path)) as t:
                names = [m.name.lower() for m in t.getmembers()[:50]]
        except Exception:
            return "oneplus"

        if any("miui" in n for n in names):
            return "miui"
        oem = _classify_oem(names)
        if oem:
            return oem
        if any("oneplus" in n or "oxygen" in n for n in names):
            return "oneplus"
        return "oneplus"

    def _detect_folder(self, path: Path) -> str:
        try:
            children = {c.name.lower() for c in path.iterdir()}
        except Exception:
            return "unknown"

        if "takeout" in children:
            return "takeout"
        from core import smartswitch as ss
        if ss.find_roots(path, max_depth=3):
            return "samsung"
        if any(n.endswith(".hbk") for n in children):
            return "huawei"
        # MIUI local backup: AllBackup folder has descript.xml + per-app .bak files
        if "miui" in children or "descript.xml" in children:
            return "miui"
        if any(n.endswith(".bak") for n in children) and not any(
                n.endswith(".vcf") for n in children):
            return "miui"
        oem = _classify_oem(children)
        if oem:
            return oem
        if any(n in children for n in ("calllogs.xml", "sms.xml", "contacts.vcf")):
            return "huawei"
        # Samsung SmartSwitch category folders / markers
        samsung_cats = {"contact", "calllog", "message", "memo", "snote",
                        "calendar", "apkfile"}
        if children & samsung_cats or "info.xml" in children:
            return "samsung"
        if any("contacts2.db" in n or "mmssms.db" in n for n in children):
            return "samsung"
        if any(n.endswith((".vcf", ".spb", ".sdoc", ".sdocx")) for n in children):
            return "samsung"
        return "unknown"

    # ── Backup extraction ─────────────────────────────────────────────────────

    def _ensure_extracted(self, log: Callable) -> bool:
        ok = self._ensure_extracted_raw(log)
        if ok and self.detected_format == "samsung":
            self._decrypt_smartswitch(log)
        return ok

    def _decrypt_smartswitch(self, log: Callable) -> None:
        """Decrypt every Smart Switch backup root into <output>/smartswitch_decrypted/ and
        search that readable mirror instead of the encrypted original."""
        from core import smartswitch as ss
        roots = ss.find_roots(self._extracted_root) if self._extracted_root else []
        if not roots:
            return
        base = self.output_dir / "smartswitch_decrypted"
        decrypted: list[Path] = []
        for i, root in enumerate(roots):
            dest = base / (root.name if len(roots) == 1 else f"{i + 1}_{root.name}")
            log(t("bkp.log_ss_decrypt").format(p=root))
            try:
                st = ss.decrypt_backup(root, dest, log, stopped=self._stopped)
            except ss.SmartSwitchError as e:
                log("  ✗ " + t("bkp.log_ss_unsupported").format(e=e))
                continue
            except Exception as e:
                log(f"  ✗ {e}")
                continue
            info = ss.backup_info(root)
            info["decrypted_to"] = str(dest)
            info["stats"] = {k: v for k, v in st.items() if not isinstance(v, list)}
            info["undecoded_files"] = st.get("undecoded_files", [])[:200]
            self.smartswitch_info[str(root)] = info
            if st.get("undecoded") and not st.get("decrypted"):
                log("  ⚠ " + t("bkp.log_ss_nothing"))
            decrypted.append(dest)
            self._media_roots.append(root)
        if decrypted:
            if len(decrypted) == 1:
                self._extracted_root = decrypted[0]
            else:
                self._extracted_root = base
            # the original (encrypted) tree is no longer searched – only its plain media

    def _ensure_extracted_raw(self, log: Callable) -> bool:
        if self._extracted_root:
            return True

        p = self.backup_path
        suffix = p.suffix.lower()

        if p.is_dir():
            self._extracted_root = p
            log(t("bkp.log_source_dir").format(p=p))
            if self.detected_format == "miui":
                self._unwrap_miui_baks(log)
            return True

        self._tmp = tempfile.mkdtemp(prefix="forensiq_bkp_")

        try:
            if suffix == ".ab":
                log(t("bkp.log_extract_ab"))
                return self._extract_ab(p, self._tmp, log)

            if zipfile.is_zipfile(str(p)):
                log(t("bkp.log_extract_zip"))
                with zipfile.ZipFile(str(p)) as z:
                    z.extractall(self._tmp)
                self._extracted_root = Path(self._tmp)
                if self.detected_format == "miui":
                    self._unwrap_miui_baks(log)
                return True

            if suffix in (".gz", ".tar", ".tgz") or tarfile.is_tarfile(str(p)):
                log(t("bkp.log_extract_tar"))
                with tarfile.open(str(p)) as tar:
                    safe_extract_tar(tar, self._tmp)
                self._extracted_root = Path(self._tmp)
                return True

            # Proprietary single-file wrappers (MIUI .bak, OPPO .nbr, LG .lbf):
            # custom header + an embedded gzip/tar/zip stream we scan for.
            if suffix in (".bak", ".nbr", ".lbf"):
                log(t("bkp.log_extract_miui"))
                dest = Path(self._tmp)
                if self._unwrap_one_bak(p, dest, log):
                    self._extracted_root = dest
                    return True
                log(t("bkp.log_miui_fail"))
                return False

            log(t("bkp.log_unsupported_fmt").format(suffix=suffix))
            return False

        except (AbPasswordRequired, AbWrongPassword):
            raise
        except Exception as e:
            log(t("bkp.log_extract_err").format(e=e))
            return False

    # ── MIUI / Xiaomi .bak unwrapping ──────────────────────────────────────────

    def _unwrap_miui_baks(self, log: Callable) -> None:
        """Unwrap every MIUI per-app .bak under the extracted roots into a temp dir."""
        baks = self._find_files("*.bak")
        if not baks:
            return
        unwrap_root = Path(tempfile.mkdtemp(prefix="forensiq_miui_"))
        self._extra_roots.append(unwrap_root)
        done = 0
        for bak in baks:
            if self._stopped():
                break
            dest = unwrap_root / bak.stem
            dest.mkdir(parents=True, exist_ok=True)
            if self._unwrap_one_bak(bak, dest, log):
                done += 1
        if done:
            log(t("bkp.log_miui_unwrapped").format(n=done))

    def _unwrap_one_bak(self, bak_path: Path, dest: Path, log: Callable) -> bool:
        """
        Extract a MIUI .bak (also tried for OPPO .nbr / LG .lbf).
        MIUI .bak = short MIUI text header followed by a regular Android backup
        stream ("ANDROID BACKUP", zlib-compressed tar, optionally AES-256
        encrypted). Other wrappers are tried by scanning for gzip / zip / tar.
        """
        import io
        import gzip
        off = find_ab_offset(bak_path)
        if off >= 0:
            tar_path = dest / "_backup.tar"
            extract_android_backup(bak_path, off, tar_path, self.password, self._stopped)
            with tarfile.open(str(tar_path)) as tar:
                safe_extract_tar(tar, str(dest))
            tar_path.unlink(missing_ok=True)
            return True

        try:
            raw = bak_path.read_bytes()
        except OSError:
            return False

        # 1) gzip member (tar.gz) – most common for MIUI app backups
        gz = raw.find(b"\x1f\x8b\x08")
        if gz >= 0:
            try:
                data = gzip.GzipFile(fileobj=io.BytesIO(raw[gz:])).read()
                with tarfile.open(fileobj=io.BytesIO(data)) as tar:
                    safe_extract_tar(tar, str(dest))
                return True
            except Exception:
                pass

        # 2) embedded ZIP
        pk = raw.find(b"PK\x03\x04")
        if pk >= 0:
            try:
                with zipfile.ZipFile(io.BytesIO(raw[pk:])) as z:
                    z.extractall(str(dest))
                return True
            except Exception:
                pass

        # 3) plain tar (ustar magic lives at offset 257 within a 512-byte block)
        ustar = raw.find(b"ustar")
        if ustar >= 0:
            start = max(0, ustar - 257)
            try:
                with tarfile.open(fileobj=io.BytesIO(raw[start:])) as tar:
                    safe_extract_tar(tar, str(dest))
                return True
            except Exception:
                pass

        return False

    def _extract_ab(self, ab_path: Path, out_dir: str, log: Callable) -> bool:
        """Extract an ADB backup (.ab) – header + (encrypted) zlib-compressed tar."""
        tar_path = os.path.join(out_dir, "_backup.tar")
        try:
            if ab_is_encrypted(ab_path):
                log(t("bkp.log_ab_encrypted"))
            log(t("bkp.log_decompress"))
            extract_android_backup(ab_path, 0, Path(tar_path), self.password, self._stopped)

            log(t("bkp.log_extract_tar2"))
            with tarfile.open(tar_path) as tar:
                safe_extract_tar(tar, out_dir)
            os.remove(tar_path)

            self._extracted_root = Path(out_dir)
            return True
        except (AbPasswordRequired, AbWrongPassword):
            raise
        except Exception as e:
            log(t("bkp.log_ab_err").format(e=e))
            return False

    def cleanup(self):
        if self._tmp and os.path.isdir(self._tmp):
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None
        for root in self._extra_roots:
            shutil.rmtree(str(root), ignore_errors=True)
        self._extra_roots = []
        self._extracted_root = None

    # ── File search ───────────────────────────────────────────────────────────

    def _find_files(self, *patterns: str) -> list[Path]:
        roots = [r for r in ([self._extracted_root] + self._extra_roots) if r]
        if not roots:
            return []
        result: list[Path] = []
        seen: set = set()
        for root in roots:
            for pattern in patterns:
                for p in root.rglob(pattern):
                    # patterns differing only in case match the same file on Windows
                    k = os.path.normcase(str(p))
                    if k not in seen and p.is_file():
                        seen.add(k)
                        result.append(p)
        return result

    def _find_db(self, *names: str) -> Path | None:
        for name in names:
            for f in self._find_files(name):
                if f.stat().st_size > 100:
                    return f
        return None

    # ── Parsers: Contacts ─────────────────────────────────────────────────────

    def _contacts_from_db(self, db: Path) -> list[dict]:
        conn = _open_db(db)
        if not conn:
            return []
        contacts: list[dict] = []
        try:
            # Standard Android contacts2.db
            rows = _safe_query(conn, """
                SELECT r.display_name, d.data1, d.mimetype_id
                FROM raw_contacts r
                JOIN data d ON d.raw_contact_id = r._id
                WHERE d.data1 IS NOT NULL
            """)
            grouped: dict[str, dict] = {}
            for row in rows:
                name = (row["display_name"] or "").strip()
                val = (row["data1"] or "").strip()
                mime = row["mimetype_id"]
                if name not in grouped:
                    grouped[name] = {"name": name, "phone": [], "email": []}
                if mime in (5, 7):    # vnd.android.cursor.item/phone_v2
                    grouped[name]["phone"].append(val)
                elif mime in (1,):    # vnd.android.cursor.item/email_v2
                    grouped[name]["email"].append(val)
            contacts = list(grouped.values())

            if not contacts:
                # Simplified schema (some backup formats)
                rows2 = _safe_query(conn, """
                    SELECT display_name, data1
                    FROM view_data
                    WHERE data1 IS NOT NULL
                    LIMIT 5000
                """)
                for row in rows2:
                    contacts.append({
                        "name": (row[0] or "").strip(),
                        "phone": [(row[1] or "").strip()],
                        "email": [],
                    })
        finally:
            conn.close()
        return contacts

    def _contacts_from_vcf(self, vcf: Path) -> list[dict]:
        try:
            return _parse_vcf(vcf.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return []

    def _contacts_from_huawei_xml(self, xml: Path) -> list[dict]:
        contacts: list[dict] = []
        try:
            root = ET.parse(str(xml)).getroot()
            for contact in root.iter("contact"):
                name = (contact.findtext("name") or contact.get("name", "")).strip()
                phones = [
                    (p.text or p.get("value", "")).strip()
                    for p in contact.findall(".//phone")
                ]
                emails = [
                    (e.text or e.get("value", "")).strip()
                    for e in contact.findall(".//email")
                ]
                if name or phones:
                    contacts.append({"name": name, "phone": phones, "email": emails})
        except Exception:
            pass
        return contacts

    # ── Parsers: SMS ──────────────────────────────────────────────────────────

    def _sms_from_db(self, db: Path) -> list[dict]:
        conn = _open_db(db)
        if not conn:
            return []
        sms: list[dict] = []
        try:
            rows = _safe_query(conn, """
                SELECT address, date, type, body, read
                FROM sms
                ORDER BY date DESC
                LIMIT 10000
            """)
            for row in rows:
                sms.append({
                    "address": (row["address"] or "").strip(),
                    "date":    _ts_to_str(row["date"]),
                    "type":    "outgoing" if row["type"] == 2 else "incoming",
                    "body":    (row["body"] or "").strip(),
                    "read":    bool(row["read"]),
                })
        finally:
            conn.close()
        return sms

    def _sms_from_xml(self, xml: Path) -> list[dict]:
        """Huawei / standard XML SMS format."""
        sms: list[dict] = []
        try:
            root = ET.parse(str(xml)).getroot()
            for item in root.iter("sms"):
                sms.append({
                    "address": item.get("address", ""),
                    "date":    _ts_to_str(item.get("date", "")),
                    "type":    "outgoing" if item.get("type") == "2" else "incoming",
                    "body":    item.get("body", ""),
                    "read":    item.get("read", "1") == "1",
                })
        except Exception:
            pass
        return sms

    def _sms_from_json(self, json_path: Path) -> list[dict]:
        """Google Takeout / JSON SMS format."""
        sms: list[dict] = []
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            items = data if isinstance(data, list) else data.get("messages", data.get("sms", []))
            for item in items:
                # Only SMS-like items – skip e.g. Google Chat / Hangouts messages
                if not isinstance(item, dict) or not (
                        item.get("address") or item.get("sender_name") or item.get("phoneNumber")):
                    continue
                sms.append({
                    "address": item.get("sender_name") or item.get("address", ""),
                    "date":    item.get("date", "") or _ts_to_str(item.get("timestamp", "")),
                    "type":    item.get("type", ""),
                    "body":    item.get("text") or item.get("body", ""),
                    "read":    True,
                })
        except Exception:
            pass
        return sms

    # ── Parsers: Calls ────────────────────────────────────────────────────────

    _CALL_TYPE_MAP = {1: "incoming", 2: "outgoing", 3: "missed",
                      4: "missed", 5: "rejected"}

    def _calls_from_db(self, db: Path) -> list[dict]:
        conn = _open_db(db)
        if not conn:
            return []
        calls: list[dict] = []
        try:
            rows = _safe_query(conn, """
                SELECT number, date, duration, type, name
                FROM calls
                ORDER BY date DESC
                LIMIT 5000
            """)
            for row in rows:
                calls.append({
                    "number":   (row["number"] or "").strip(),
                    "name":     (row["name"] or "").strip(),
                    "date":     _ts_to_str(row["date"]),
                    "duration": str(row["duration"] or 0) + " s",
                    "type":     self._CALL_TYPE_MAP.get(row["type"], str(row["type"])),
                })
        finally:
            conn.close()
        return calls

    def _calls_from_xml(self, xml: Path) -> list[dict]:
        calls: list[dict] = []
        try:
            root = ET.parse(str(xml)).getroot()
            for call in root.iter("call"):
                t_raw = call.get("type", "")
                calls.append({
                    "number":   call.get("number", ""),
                    "name":     call.get("name", ""),
                    "date":     _ts_to_str(call.get("date", "")),
                    "duration": call.get("duration", "0") + " s",
                    "type":     self._CALL_TYPE_MAP.get(int(t_raw) if t_raw.isdigit() else 0, t_raw),
                })
        except Exception:
            pass
        return calls

    def _calls_from_json(self, json_path: Path) -> list[dict]:
        calls: list[dict] = []
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            items = data if isinstance(data, list) else data.get("calls", [])
            type_map = {
                "INCOMING_TYPE": "incoming",
                "OUTGOING_TYPE": "outgoing",
                "MISSED_TYPE":   "missed",
            }
            for item in items:
                calls.append({
                    "number":   item.get("phoneNumber") or item.get("number", ""),
                    "name":     item.get("formattedNumber") or item.get("name", ""),
                    "date":     item.get("startTime") or _ts_to_str(item.get("date", "")),
                    "duration": str(item.get("duration", "0")) + " s",
                    "type":     type_map.get(item.get("type", ""), item.get("type", "")),
                })
        except Exception:
            pass
        return calls

    # ── Parsers: Wi-Fi ────────────────────────────────────────────────────────

    def _wifi_from_db(self, db: Path) -> list[dict]:
        conn = _open_db(db)
        if not conn:
            return []
        nets: list[dict] = []
        for table in ("wifi_settings", "network_list", "configured_networks"):
            rows = _safe_query(conn, f"SELECT * FROM {table} LIMIT 500")
            if rows:
                for row in rows:
                    d = dict(row)
                    nets.append({
                        "ssid":    d.get("ssid", d.get("SSID", "")),
                        "bssid":   d.get("bssid", d.get("BSSID", "")),
                        "type":    d.get("key_mgmt", d.get("keymgmt", "")),
                        "status":  "saved",
                    })
                break
        conn.close()
        return nets

    def _wifi_from_xml(self, xml: Path) -> list[dict]:
        """WifiConfigStore.xml (Android 10+)."""
        nets: list[dict] = []
        try:
            root = ET.parse(str(xml)).getroot()
            for net in root.iter("Network"):
                ssid = ""
                key_mgmt = ""
                for elem in net.iter("string"):
                    if elem.get("name") == "SSID":
                        ssid = (elem.text or "").strip('"')
                    elif elem.get("name") == "KeyMgmt":
                        key_mgmt = elem.text or ""
                if ssid:
                    nets.append({"ssid": ssid, "bssid": "", "type": key_mgmt, "status": "saved"})
        except Exception:
            pass
        return nets

    # ── Parsers: Calendar ─────────────────────────────────────────────────────

    def _calendar_from_db(self, db: Path) -> list[dict]:
        conn = _open_db(db)
        if not conn:
            return []
        events: list[dict] = []
        try:
            rows = _safe_query(conn, """
                SELECT title, description, dtstart, dtend, eventLocation
                FROM Events
                ORDER BY dtstart DESC
                LIMIT 3000
            """)
            for row in rows:
                events.append({
                    "title":       (row["title"] or "").strip(),
                    "description": (row["description"] or "").strip(),
                    "start":       _ts_to_str(row["dtstart"]),
                    "end":         _ts_to_str(row["dtend"]),
                    "location":    (row["eventLocation"] or "").strip(),
                })
        finally:
            conn.close()
        return events

    def _calendar_from_ics(self, ics: Path) -> list[dict]:
        """Minimal ICS parser."""
        events: list[dict] = []
        cur: dict | None = None
        try:
            for line in ics.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line == "BEGIN:VEVENT":
                    cur = {"title": "", "description": "", "start": "", "end": "", "location": ""}
                elif line == "END:VEVENT" and cur:
                    events.append(cur)
                    cur = None
                elif cur:
                    if line.startswith("SUMMARY:"):
                        cur["title"] = line[8:]
                    elif line.startswith("DESCRIPTION:"):
                        cur["description"] = line[12:]
                    elif line.startswith("DTSTART"):
                        cur["start"] = line.partition(":")[2]
                    elif line.startswith("DTEND"):
                        cur["end"] = line.partition(":")[2]
                    elif line.startswith("LOCATION:"):
                        cur["location"] = line[9:]
        except Exception:
            pass
        return events

    # ── Parsers: Browser (history + bookmarks) ────────────────────────────────

    def _browser_from_db(self, db: Path) -> list[dict]:
        conn = _open_db(db)
        if not conn:
            return []
        rows_out: list[dict] = []
        try:
            # Chrome / modern WebView: urls table (WebKit timestamps)
            rows = _safe_query(conn, """
                SELECT url, title, visit_count, last_visit_time
                FROM urls
                ORDER BY last_visit_time DESC
                LIMIT 5000
            """)
            for row in rows:
                rows_out.append({
                    "type":       "history",
                    "url":        (row["url"] or "").strip(),
                    "title":      (row["title"] or "").strip(),
                    "visits":     row["visit_count"] or 0,
                    "last_visit": _chrome_ts(row["last_visit_time"]),
                })

            # AOSP stock browser: history table (Unix ms timestamps)
            if not rows_out:
                rows = _safe_query(conn, """
                    SELECT url, title, visits, date
                    FROM history
                    ORDER BY date DESC
                    LIMIT 5000
                """)
                for row in rows:
                    rows_out.append({
                        "type":       "history",
                        "url":        (row["url"] or "").strip(),
                        "title":      (row["title"] or "").strip(),
                        "visits":     row["visits"] or 0,
                        "last_visit": _ts_to_str(row["date"]),
                    })

            # AOSP bookmarks table (bookmark=1)
            for row in _safe_query(conn, """
                SELECT url, title FROM bookmarks WHERE bookmark=1 LIMIT 2000
            """):
                rows_out.append({
                    "type":  "bookmark",
                    "url":   (row["url"] or "").strip(),
                    "title": (row["title"] or "").strip(),
                    "visits": "", "last_visit": "",
                })
        finally:
            conn.close()
        return rows_out

    def _bookmarks_from_chrome_json(self, path: Path) -> list[dict]:
        """Parse a Chrome 'Bookmarks' JSON file (recursively)."""
        out: list[dict] = []
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return out

        def _walk(node):
            if not isinstance(node, dict):
                return
            if node.get("type") == "url":
                out.append({
                    "type":  "bookmark",
                    "url":   node.get("url", ""),
                    "title": node.get("name", ""),
                    "visits": "",
                    "last_visit": _chrome_ts(node.get("date_added")),
                })
            for child in node.get("children", []) or []:
                _walk(child)

        for root in (data.get("roots") or {}).values():
            _walk(root)
        return out

    def _browser_from_takeout_json(self, path: Path) -> list[dict]:
        """Google Takeout Chrome history: {"Browser History": [{title, url, time_usec}]}."""
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return []
        items = data.get("Browser History") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        out = []
        for it in items:
            if not isinstance(it, dict) or not it.get("url"):
                continue
            usec = it.get("time_usec")
            try:
                when = datetime.fromtimestamp(int(usec) / 1_000_000).strftime(
                    "%Y-%m-%d %H:%M:%S") if usec else ""
            except (ValueError, OverflowError, OSError):
                when = ""
            out.append({"type": "history", "url": it.get("url", ""),
                        "title": it.get("title", ""), "visits": "",
                        "last_visit": when})
        return out

    def _bookmarks_from_html(self, path: Path) -> list[dict]:
        """Netscape bookmark file (Takeout Chrome 'Bookmarks.html', browser exports)."""
        import html as _html
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return []
        if "<A " not in text.upper():
            return []
        out = []
        for m in re.finditer(r'<A\s+([^>]*?)>(.*?)</A>', text, re.I | re.S):
            attrs, title = m.group(1), m.group(2)
            href = re.search(r'HREF="([^"]*)"', attrs, re.I)
            if not href:
                continue
            added = re.search(r'ADD_DATE="(\d+)"', attrs, re.I)
            out.append({"type": "bookmark", "url": _html.unescape(href.group(1)),
                        "title": _html.unescape(re.sub(r"<[^>]+>", "", title)).strip(),
                        "visits": "",
                        "last_visit": _ts_to_str(added.group(1)) if added else ""})
        return out

    # ── Parsers: Notes ─────────────────────────────────────────────────────────

    def _notes_from_keep_json(self, path: Path) -> list[dict]:
        """Parse a Google Keep note exported by Takeout (one JSON per note)."""
        try:
            d = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return []
        if not isinstance(d, dict) or "textContent" not in d and "title" not in d:
            return []
        usec = d.get("userEditedTimestampUsec") or d.get("createdTimestampUsec")
        return [{
            "title":    d.get("title", ""),
            "content":  (d.get("textContent") or "").strip(),
            "created":  _chrome_ts(usec) if usec else "",
            "trashed":  bool(d.get("isTrashed")),
        }]

    def _notes_from_db(self, db: Path) -> list[dict]:
        conn = _open_db(db)
        if not conn:
            return []
        notes: list[dict] = []
        try:
            # Try a range of schemas used by stock/Samsung/MIUI note apps
            for sql in (
                "SELECT title, content, created FROM notes LIMIT 5000",
                "SELECT title, content, createTime AS created FROM notes LIMIT 5000",
                "SELECT title, content, date AS created FROM note LIMIT 5000",
                "SELECT title AS title, content AS content, created FROM memo LIMIT 5000",
                "SELECT '' AS title, content, created FROM memo LIMIT 5000",
            ):
                rows = _safe_query(conn, sql)
                if rows:
                    for row in rows:
                        notes.append({
                            "title":   (row["title"] or "").strip() if "title" in row.keys() else "",
                            "content": (row["content"] or "").strip(),
                            "created": _ts_to_str(row["created"]),
                            "trashed": False,
                        })
                    break
        finally:
            conn.close()
        return notes

    # ── Parsers: Installed apps ────────────────────────────────────────────────

    def _apps_from_packages_xml(self, xml: Path) -> list[dict]:
        """Parse Android /data/system/packages.xml (<package name=... >)."""
        apps: list[dict] = []
        try:
            tree = ET.parse(xml)
        except Exception:
            return apps
        for pkg in tree.getroot().iter("package"):
            name = pkg.get("name")
            if not name:
                continue
            apps.append({
                "package":   name,
                "code_path": pkg.get("codePath", ""),
                "installed": _ts_to_str(pkg.get("ft") and int(pkg.get("ft"), 16)
                                        if pkg.get("ft") else pkg.get("it")),
                "version":   pkg.get("version", ""),
            })
        return apps

    def _apps_from_apk_names(self) -> list[dict]:
        """Fallback: list distinct .apk filenames present in the backup."""
        seen: set = set()
        apps: list[dict] = []
        for f in self._find_files("*.apk"):
            stem = f.stem
            if stem in seen:
                continue
            seen.add(stem)
            apps.append({"package": stem, "code_path": f.name,
                         "installed": "", "version": ""})
        return apps

    # ── Media ─────────────────────────────────────────────────────────────────

    def _copy_media(self, log: Callable) -> int:
        media_dir = self.output_dir / "media_backup"
        media_dir.mkdir(exist_ok=True)
        exts = {".jpg", ".jpeg", ".png", ".mp4", ".3gp", ".mov",
                ".heic", ".gif", ".webp", ".bmp", ".avi", ".mkv"}
        count = 0
        if not self._extracted_root:
            return 0
        roots = [self._extracted_root] + self._media_roots
        for f in (x for r in roots for x in r.rglob("*")):
            if f.suffix.lower() in exts and f.is_file():
                try:
                    dest = media_dir / f.name
                    if dest.exists():
                        dest = media_dir / f"{f.stem}_{count}{f.suffix}"
                    shutil.copy2(str(f), str(dest))
                    count += 1
                    if count % 50 == 0:
                        log(t("bkp.log_media_count").format(count=count))
                except Exception:
                    pass
        return count

    # ── Output writing ────────────────────────────────────────────────────────

    def _write_json(self, filename: str, data: list):
        path = self.output_dir / filename
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Extraction modules ────────────────────────────────────────────────────

    def _run_contacts(self, fmt: str, log: Callable) -> ImportResult:
        contacts: list[dict] = []

        # 1) vCard files (highest priority – standard format)
        for vcf in self._find_files("*.vcf"):
            log(f"  📄 {vcf.name}")
            contacts.extend(self._contacts_from_vcf(vcf))

        # 2) Huawei XML
        if fmt == "huawei" and not contacts:
            for f in self._find_files("Contacts.xml", "contacts.xml", "backup.xml"):
                log(f"  📄 {f.name}")
                contacts.extend(self._contacts_from_huawei_xml(f))

        # 3) SQLite database
        if not contacts:
            db = self._find_db("contacts2.db", "contacts.db")
            if db:
                log(f"  🗄 {db.name}")
                contacts.extend(self._contacts_from_db(db))

        if not contacts:
            return ImportResult("bkp_contacts", False, error=t("bkp.err_no_contacts"))

        # Deduplication
        seen: set = set()
        dedup: list[dict] = []
        for c in contacts:
            key = (c.get("name", ""), tuple(sorted(c.get("phone", []))))
            if key not in seen:
                seen.add(key)
                dedup.append(c)

        self._write_json("contacts.json", dedup)
        return ImportResult("bkp_contacts", True, len(dedup))

    def _sqlite_files(self) -> list[Path]:
        """All SQLite databases in the searched roots (by file header)."""
        out = []
        for f in self._find_files("*"):
            try:
                if f.stat().st_size >= 512:
                    with open(f, "rb") as fh:
                        if fh.read(16) == b"SQLite format 3\x00":
                            out.append(f)
            except OSError:
                pass
        return out

    def _run_sms(self, fmt: str, log: Callable) -> ImportResult:
        sms: list[dict] = []

        if fmt == "samsung":
            from core import smartswitch as ss
            for db in self._sqlite_files():
                if ss.is_bugle_backup(db):
                    log(f"  🗄 {db.name} (Google Messages)")
                    sms.extend(ss.sms_from_bugle(db))

        if fmt == "huawei":
            for f in self._find_files("sms.xml", "SMS.xml", "All_SMS.xml"):
                log(f"  📄 {f.name}")
                sms.extend(self._sms_from_xml(f))

        if fmt == "takeout" or not sms:
            for f in self._find_files("*.json"):
                name = f.name.lower()
                if any(k in name for k in ("sms", "message", "mms")):
                    log(f"  📄 {f.name}")
                    sms.extend(self._sms_from_json(f))

        if not sms:
            db = self._find_db("mmssms.db", "sms.db", "telephony.db")
            if db:
                log(f"  🗄 {db.name}")
                sms.extend(self._sms_from_db(db))

        if not sms:
            return ImportResult("bkp_sms", False, error=t("bkp.err_no_sms"))

        self._write_json("sms.json", sms)
        return ImportResult("bkp_sms", True, len(sms))

    def _run_calls(self, fmt: str, log: Callable) -> ImportResult:
        calls: list[dict] = []

        if fmt == "samsung":
            from core import smartswitch as ss
            for f in self._find_files("*.exml"):
                got = ss.calls_from_exml(f)
                if got:
                    log(f"  📄 {f.name}")
                    calls.extend(got)

        if fmt == "huawei":
            for f in self._find_files("calllogs.xml", "CallLogs.xml", "call_log.xml"):
                log(f"  📄 {f.name}")
                calls.extend(self._calls_from_xml(f))

        if fmt == "takeout" or not calls:
            for f in self._find_files("*.json"):
                if "call" in f.name.lower():
                    log(f"  📄 {f.name}")
                    calls.extend(self._calls_from_json(f))

        if not calls:
            db = self._find_db("calllog.db", "call_log.db", "calls.db",
                                "telephony.db", "contacts2.db")
            if db:
                log(f"  🗄 {db.name}")
                calls.extend(self._calls_from_db(db))

        if not calls:
            return ImportResult("bkp_calls", False, error=t("bkp.err_no_calls"))

        self._write_json("call_logs.json", calls)
        return ImportResult("bkp_calls", True, len(calls))

    def _run_wifi(self, fmt: str, log: Callable) -> ImportResult:
        nets: list[dict] = []

        if fmt == "samsung":
            from core import smartswitch as ss
            for f in self._find_files("*"):
                if "wificonfig" not in str(f).lower() or f.stat().st_size > (8 << 20):
                    continue
                got = ss.wifi_from_text(f)
                if got:
                    log(f"  📄 {f.name}")
                    nets.extend(n for n in got if n not in nets)

        for f in self._find_files("WifiConfigStore.xml", "wifi_config.xml",
                                   "network_history.txt"):
            log(f"  📄 {f.name}")
            if f.suffix == ".xml":
                nets.extend(self._wifi_from_xml(f))

        if not nets:
            db = self._find_db("wifi.db", "wifi_settings.db", "wpa_supplicant.db")
            if db:
                log(f"  🗄 {db.name}")
                nets.extend(self._wifi_from_db(db))

        if not nets:
            return ImportResult("bkp_wifi", False, error=t("bkp.err_no_wifi"))

        self._write_json("wifi.json", nets)
        return ImportResult("bkp_wifi", True, len(nets))

    def _run_calendar(self, fmt: str, log: Callable) -> ImportResult:
        events: list[dict] = []

        for f in self._find_files("*.ics", "*.ical"):
            log(f"  📄 {f.name}")
            events.extend(self._calendar_from_ics(f))

        if not events:
            db = self._find_db("calendar.db", "cal.db")
            if db:
                log(f"  🗄 {db.name}")
                events.extend(self._calendar_from_db(db))

        if not events:
            return ImportResult("bkp_calendar", False, error=t("bkp.err_no_calendar"))

        self._write_json("calendar.json", events)
        return ImportResult("bkp_calendar", True, len(events))

    def _run_browser(self, fmt: str, log: Callable) -> ImportResult:
        entries: list[dict] = []

        # History / bookmarks from SQLite (Chrome 'History', WebView, AOSP browser)
        for db in self._find_files("History", "browser2.db", "browser.db",
                                   "webview.db", "sbrowser.db", "SBrowser.db",
                                   "Browser.db"):
            if db.stat().st_size < 100:
                continue
            log(f"  🗄 {db.name}")
            entries.extend(self._browser_from_db(db))

        # Samsung Internet bookmarks (SBrowser.db, upper-case BOOKMARKS table)
        if fmt == "samsung":
            from core import smartswitch as ss
            for db in self._find_files("SBrowser.db", "sbrowser.db"):
                got = ss.sbrowser_bookmarks(db)
                if got:
                    log(f"  🗄 {db.name} (bookmarks)")
                    entries.extend(got)

        # Chrome bookmarks JSON
        for f in self._find_files("Bookmarks"):
            log(f"  📄 {f.name}")
            entries.extend(self._bookmarks_from_chrome_json(f))

        # Google Takeout: Chrome history JSON + bookmarks HTML (Netscape format)
        for f in self._find_files("*.json"):
            if "history" in f.name.lower():
                got = self._browser_from_takeout_json(f)
                if got:
                    log(f"  📄 {f.name}")
                    entries.extend(got)
        for f in self._find_files("*.html", "*.htm"):
            if "bookmark" in f.name.lower():
                got = self._bookmarks_from_html(f)
                if got:
                    log(f"  📄 {f.name}")
                    entries.extend(got)

        if not entries:
            return ImportResult("bkp_browser", False, error=t("bkp.err_no_browser"))

        # Deduplicate on (type, url)
        seen: set = set()
        dedup: list[dict] = []
        for e in entries:
            key = (e.get("type"), e.get("url"))
            if key not in seen:
                seen.add(key)
                dedup.append(e)

        self._write_json("browser_history.json", dedup)
        return ImportResult("bkp_browser", True, len(dedup))

    def _run_notes(self, fmt: str, log: Callable) -> ImportResult:
        notes: list[dict] = []

        # Google Keep (Takeout) – one JSON per note, usually under a Keep/ folder
        for f in self._find_files("*.json"):
            parent = f.parent.name.lower()
            if "keep" in parent or "keep" in f.name.lower():
                got = self._notes_from_keep_json(f)
                if got:
                    log(f"  📄 {f.name}")
                    notes.extend(got)

        # Note databases (stock / Samsung / MIUI)
        if not notes:
            for db in self._find_files("notes.db", "note.db", "memo.db",
                                       "memos.db", "SmemoBackup.db"):
                if db.stat().st_size < 100:
                    continue
                log(f"  🗄 {db.name}")
                notes.extend(self._notes_from_db(db))

        if not notes:
            return ImportResult("bkp_notes", False, error=t("bkp.err_no_notes"))

        self._write_json("notes.json", notes)
        return ImportResult("bkp_notes", True, len(notes))

    def _run_apps(self, fmt: str, log: Callable) -> ImportResult:
        apps: list[dict] = []

        if fmt == "samsung":
            from core import smartswitch as ss
            for f in self._find_files("AppList.json"):
                got = ss.apps_from_applist(f)
                if got:
                    log(f"  📄 {f.name}")
                    apps.extend(got)

        for xml in self._find_files("packages.xml"):
            log(f"  📄 {xml.name}")
            apps.extend(self._apps_from_packages_xml(xml))

        if not apps:
            apk_apps = self._apps_from_apk_names()
            if apk_apps:
                log(t("bkp.log_apk_scan").format(n=len(apk_apps)))
                apps.extend(apk_apps)

        if not apps:
            return ImportResult("bkp_apps", False, error=t("bkp.err_no_apps"))

        # Deduplicate on package name
        seen: set = set()
        dedup = [a for a in apps if not (a["package"] in seen or seen.add(a["package"]))]

        self._write_json("apps_installed.json", dedup)
        return ImportResult("bkp_apps", True, len(dedup))

    # ── Main runner ───────────────────────────────────────────────────────────

    def run(
        self,
        modules: list[str],
        log: Callable[[str], None],
        done: Callable[[bool, str], None],
        fmt: str = "",
    ):
        """Run the import in a thread. Calls done(ok, msg) when finished."""
        self._stop_event.clear()
        if fmt:
            self.fmt = fmt

        def _worker():
            import time
            t0 = time.time()

            self.output_dir.mkdir(parents=True, exist_ok=True)

            # Format detection
            if self.fmt == "auto":
                self.detected_format = self.detect_format()
            else:
                self.detected_format = self.fmt

            fmt_label = _fmt_label(self.detected_format)
            log(t("bkp.log_format").format(fmt=fmt_label))
            log(f"📂 Source         : {self.backup_path}")
            log(t("bkp.log_output").format(dir=self.output_dir))
            log("─" * 52)

            # Extraction
            try:
                extracted = self._ensure_extracted(log)
            except AbPasswordRequired:
                log("✗ " + t("bkp.err_need_password"))
                self.cleanup()
                done(False, t("bkp.err_need_password"))
                return
            except AbWrongPassword:
                log("✗ " + t("bkp.err_wrong_password"))
                self.cleanup()
                done(False, t("bkp.err_wrong_password"))
                return
            if not extracted:
                self.cleanup()
                done(False, t("bkp.err_extract_fail"))
                return

            log("")
            results: list[ImportResult] = []
            _dispatch = {
                "bkp_contacts": self._run_contacts,
                "bkp_sms":      self._run_sms,
                "bkp_calls":    self._run_calls,
                "bkp_wifi":     self._run_wifi,
                "bkp_calendar": self._run_calendar,
                "bkp_browser":  self._run_browser,
                "bkp_notes":    self._run_notes,
                "bkp_apps":     self._run_apps,
            }

            for module in modules:
                if self._stopped():
                    log("")
                    log("■ " + t("bkp.log_stopped"))
                    done(False, t("bkp.stopped_msg"))
                    return

                t_start = time.time()
                label = _mod_label(module)
                log(f"▶ {label}…")

                if module == "bkp_media":
                    try:
                        count = self._copy_media(log)
                        res = ImportResult("bkp_media", True, count)
                    except Exception as e:
                        res = ImportResult("bkp_media", False, error=str(e))
                elif module in _dispatch:
                    try:
                        res = _dispatch[module](self.detected_format, log)
                    except Exception as e:
                        res = ImportResult(module, False, error=str(e))
                else:
                    res = ImportResult(module, False, error=t("bkp.log_unknown_module"))

                res.elapsed = time.time() - t_start
                results.append(res)

                if res.ok:
                    log(t("bkp.log_records").format(n=res.records, t=res.elapsed))
                else:
                    log(f"  ✗ {res.error}  ({res.elapsed:.1f} s)")

            # Write summary
            summary = {
                "source":      str(self.backup_path),
                "format":      self.detected_format,
                "format_name": fmt_label,
                "imported_at": datetime.now().isoformat(),
                "modules": {
                    r.key: {"ok": r.ok, "records": r.records, "error": r.error}
                    for r in results
                },
            }
            if self.smartswitch_info:
                summary["smartswitch"] = self.smartswitch_info
            (self.output_dir / "import_summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            ok_count = sum(1 for r in results if r.ok)
            total = time.time() - t0
            log("")
            log("─" * 52)
            log(t("bkp.log_done").format(ok=ok_count, total=len(results), t=total))

            self.cleanup()
            done(True, t("bkp.done_msg").format(ok=ok_count, total=len(results)))

        threading.Thread(target=_worker, daemon=True).start()
