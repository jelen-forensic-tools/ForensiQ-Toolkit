"""
core/ios_ramdisk.py — Forensic acquisition from a booted SSH ramdisk
(checkm8 / usbliter8 + ICH_A12_plus_Ramdisk or compatible).

Usage model (division of work):
  1. Pwned DFU + booting the ramdisk are done EXTERNALLY (macOS + hardware):
       ./build.sh ; ./boot.sh ; iproxy 2222 22
  2. This module connects to the running ramdisk over SSH (default root@localhost:2222,
     password 'alpine'), runs `mount_ich`, finds and pulls forensic artefacts over SFTP
     and computes hashes. The results feed the same results/evidence pipeline as
     the other extractors.

Legitimate forensic use on authorised devices (owner consent / court order).
The Data partition of a locked (BFU) device may be encrypted and unreadable.

paramiko is an optional dependency – if it is missing, the module reports it cleanly.
"""
from __future__ import annotations

import os
import json
import stat
import hashlib
import threading
import datetime
from typing import Callable, Optional

from .i18n import t, has

try:
    import paramiko
    _HAS_PARAMIKO = True
except Exception:
    _HAS_PARAMIKO = False


def paramiko_available() -> bool:
    return _HAS_PARAMIKO


# ── Modules (artefacts) ──────────────────────────────────────────────────────
# Each: list of candidate paths relative to the Data partition + type ("file"/"dir"),
# or a find spec (search for a file under a folder – app containers have random UUIDs).
IOS_RAMDISK_MODULES = {
    "sms":        "Messages (SMS / iMessage)",
    "contacts":   "Contacts",
    "call_logs":  "Call History",
    "safari":     "Safari History",
    "notes":      "Notes",
    "accounts":   "Accounts",
    "photos":     "Photos (DCIM)",
    "whatsapp":   "WhatsApp",
    "keychain":   "Keychain (encrypted)",
}

# paths relative to the Data partition (Data root corresponds to /private/var on live iOS)
_SPECS = {
    "sms":       {"type": "file", "paths": ["mobile/Library/SMS/sms.db"]},
    "contacts":  {"type": "file", "paths": ["mobile/Library/AddressBook/AddressBook.sqlitedb"]},
    "call_logs": {"type": "file", "paths": ["mobile/Library/CallHistoryDB/CallHistory.storedata"]},
    "safari":    {"type": "file", "paths": ["mobile/Library/Safari/History.db"]},
    "notes":     {"type": "file", "paths": [
        "mobile/Library/Notes/notes.sqlite",
        "mobile/Containers/Shared/AppGroup/*/NoteStore.sqlite",
    ]},
    "accounts":  {"type": "file", "paths": ["mobile/Library/Accounts/Accounts3.sqlite"]},
    "photos":    {"type": "dir",  "paths": ["mobile/Media/DCIM"]},
    "whatsapp":  {"type": "find", "root": "mobile/Containers/Shared/AppGroup",
                  "name": "ChatStorage.sqlite"},
    "keychain":  {"type": "file", "paths": ["Keychains/keychain-2.db"]},
}


class RamdiskResult:
    @property
    def label(self) -> str:
        k = f" rd.mod.{self.module}".strip()
        return t(k) if has(k) else IOS_RAMDISK_MODULES.get(self.module, self.module)

    def __init__(self, module: str):
        self.module = module
        self.success = False
        self.data: list = []        # [{filename, path, size, sha256}]
        self.error = ""
        self.file_path = ""         # local path/folder
        self.count = 0


class iOSRamdiskAcquirer:
    """Pull forensic artefacts from a running SSH ramdisk over SFTP."""

    def __init__(self, output_dir: str,
                 host: str = "localhost", port: int = 2222,
                 user: str = "root", password: str = "alpine",
                 data_root: str = "/mnt2"):
        self.output_dir = output_dir
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        # Mount point of the Data partition after `mount_ich` (ramdisk convention: /mnt2).
        self.data_root = data_root.rstrip("/")
        self._stop = threading.Event()
        self._on_log: Optional[Callable] = None
        self._client = None
        self._sftp = None

    # ── Control ─────────────────────────────────────────────────────────────
    def stop(self):
        self._stop.set()

    def _stopped(self) -> bool:
        return self._stop.is_set()

    def _log(self, msg: str):
        if self._on_log:
            self._on_log(msg)

    # ── SSH/SFTP ────────────────────────────────────────────────────────────
    def test_connection(self) -> tuple[bool, str]:
        """Quick SSH connection check (for the Test button)."""
        if not _HAS_PARAMIKO:
            return False, t("rd.no_paramiko")
        try:
            cli = self._connect()
            out = self._exec(cli, "uname -a")
            cli.close()
            return True, out.strip() or "OK"
        except Exception as e:
            return False, str(e)

    def _connect(self):
        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        cli.connect(
            self.host, port=self.port, username=self.user,
            password=self.password, timeout=15,
            allow_agent=False, look_for_keys=False,
            banner_timeout=20, auth_timeout=20,
        )
        return cli

    @staticmethod
    def _exec(cli, cmd: str, timeout: int = 60) -> str:
        stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        return (out + err)

    # ── Main run ────────────────────────────────────────────────────────────
    def run(self, modules: list[str],
            on_progress: Callable[[str, int, int], None],
            on_done: Callable,
            on_module_done: Callable = None,
            on_log: Callable = None,
            do_mount: bool = True):
        self._on_log = on_log
        self._stop.clear()

        def _worker():
            if not _HAS_PARAMIKO:
                on_done([], t("rd.no_paramiko"))
                return
            os.makedirs(self.output_dir, exist_ok=True)
            try:
                self._log(t("rd.log_connecting").format(h=self.host, p=self.port))
                self._client = self._connect()
                self._sftp = self._client.open_sftp()
            except Exception as e:
                on_done([], f"{t('rd.err_connect')}: {e}")
                return

            try:
                if do_mount:
                    self._log(t("rd.log_mounting"))
                    out = self._exec(self._client, "mount_ich 2>&1")
                    for line in out.splitlines():
                        if line.strip():
                            self._log("  " + line.strip())

                results: list[RamdiskResult] = []
                total = len(modules)
                elapsed: list[float] = []
                for i, module in enumerate(modules, 1):
                    if self._stopped():
                        break
                    label = (t(f"rd.mod.{module}") if has(f"rd.mod.{module}")
                             else IOS_RAMDISK_MODULES.get(module, module))
                    on_progress(label, i, total)
                    t0 = datetime.datetime.now()
                    res = self._pull_module(module)
                    dt = (datetime.datetime.now() - t0).total_seconds()
                    elapsed.append(dt)
                    avg = sum(elapsed) / len(elapsed)
                    results.append(res)
                    if on_module_done:
                        on_module_done(res, i, total, dt, avg)
                on_done(results, "")
            finally:
                try:
                    if self._sftp:
                        self._sftp.close()
                    if self._client:
                        self._client.close()
                except Exception:
                    pass

        threading.Thread(target=_worker, daemon=True).start()

    # ── Pull one module ─────────────────────────────────────────────────
    def _pull_module(self, module: str) -> RamdiskResult:
        res = RamdiskResult(module)
        spec = _SPECS.get(module)
        if not spec:
            res.error = f"Neznámý modul: {module}"
            return res
        try:
            remote_paths = self._resolve(spec)
            if not remote_paths:
                res.error = t("rd.not_found")
                return res
            out_sub = os.path.join(self.output_dir, f"ios_{module}")
            if spec["type"] == "dir":
                os.makedirs(out_sub, exist_ok=True)
                for rp in remote_paths:
                    self._pull_dir(rp, out_sub, res)
                res.file_path = out_sub
            else:
                os.makedirs(out_sub, exist_ok=True)
                for rp in remote_paths:
                    self._pull_file(rp, out_sub, res)
                res.file_path = out_sub
            res.count = len(res.data)
            res.success = bool(res.data)
            if not res.data:
                res.error = t("rd.not_found")
        except Exception as e:
            res.error = str(e)
        return res

    def _resolve(self, spec: dict) -> list[str]:
        """Return the absolute remote paths that exist on the device."""
        found: list[str] = []
        if spec["type"] == "find":
            root = f"{self.data_root}/{spec['root']}"
            out = self._exec(
                self._client,
                f"find '{root}' -name '{spec['name']}' 2>/dev/null",
            )
            found = [l.strip() for l in out.splitlines() if l.strip()]
            return found
        for p in spec["paths"]:
            full = f"{self.data_root}/{p}"
            if "*" in full:
                # simple glob via the shell
                out = self._exec(self._client, f"ls -d {full} 2>/dev/null")
                found += [l.strip() for l in out.splitlines() if l.strip()]
            else:
                if self._remote_exists(full):
                    found.append(full)
        return found

    def _remote_exists(self, path: str) -> bool:
        try:
            self._sftp.stat(path)
            return True
        except Exception:
            return False

    def _pull_file(self, remote: str, out_dir: str, res: RamdiskResult):
        name = os.path.basename(remote)
        local = os.path.join(out_dir, name)
        self._log(t("x.copying").format(name=name))
        self._sftp.get(remote, local)
        res.data.append(self._file_meta(local, remote))

    def _pull_dir(self, remote_dir: str, out_dir: str, res: RamdiskResult,
                  depth: int = 0):
        if self._stopped() or depth > 12:
            return
        try:
            entries = self._sftp.listdir_attr(remote_dir)
        except Exception:
            return
        for e in entries:
            if self._stopped():
                return
            rpath = f"{remote_dir}/{e.filename}"
            if stat.S_ISDIR(e.st_mode):
                sub = os.path.join(out_dir, e.filename)
                os.makedirs(sub, exist_ok=True)
                self._pull_dir(rpath, sub, res, depth + 1)
            else:
                local = os.path.join(out_dir, e.filename)
                try:
                    self._log(t("x.copying").format(name=e.filename))
                    self._sftp.get(rpath, local)
                    res.data.append(self._file_meta(local, rpath))
                except Exception:
                    pass

    @staticmethod
    def _file_meta(local: str, remote: str) -> dict:
        size = os.path.getsize(local) if os.path.isfile(local) else 0
        h = hashlib.sha256()
        try:
            with open(local, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            digest = h.hexdigest()
        except Exception:
            digest = ""
        return {
            "filename": os.path.basename(local),
            "remote_path": remote,
            "size": size,
            "sha256": digest,
        }
