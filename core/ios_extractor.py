"""
core/ios_extractor.py — iOS live device extraction engine.
Uses iOSManager (pymobiledevice3) for USB-connected devices.
"""
from __future__ import annotations

import os
import json
import threading
import datetime
from typing import Callable, Optional

from .ios_manager import iOSManager
from .i18n import t, has

IOS_MODULES = {
    "ios_device_info": "Device Information",
    "ios_apps":        "Installed Apps",
    "ios_photos":      "Photos (DCIM)",
}


class iOSExtractionResult:
    @property
    def label(self) -> str:
        k = f"ios.mod.{self.module}"
        return t(k) if has(k) else IOS_MODULES.get(self.module, self.module)

    def __init__(self, module: str):
        self.module = module
        self.success = False
        self.data = None
        self.error = ""
        self.file_path = ""
        self.count = 0


class iOSExtractor:
    """Extracts data from a live iOS device via pymobiledevice3."""

    def __init__(self, manager: iOSManager, output_dir: str):
        self.manager = manager
        self.output_dir = output_dir
        self._stop_event = threading.Event()
        self._on_log: Optional[Callable] = None

    def stop(self):
        self._stop_event.set()

    def _stopped(self) -> bool:
        return self._stop_event.is_set()

    def _log(self, msg: str):
        if self._on_log:
            self._on_log(msg)

    # ── Main orchestrator ──────────────────────────────────────────────────

    def extract_all(
        self,
        modules: list[str],
        on_progress: Callable[[str, int, int], None],
        on_done: Callable[[list], None],
        on_module_done: Callable = None,
        on_log: Callable = None,
        **kwargs,
    ):
        self._on_log = on_log
        self._stop_event.clear()

        def _worker():
            results = []
            total = len(modules)
            elapsed_list: list[float] = []

            for i, module in enumerate(modules, 1):
                if self._stopped():
                    break
                label = t(f"ios.mod.{module}") if has(f"ios.mod.{module}") else IOS_MODULES.get(module, module)
                on_progress(label, i, total)

                t0 = datetime.datetime.now()
                result = self._extract_module(module)
                elapsed = (datetime.datetime.now() - t0).total_seconds()
                elapsed_list.append(elapsed)
                avg = sum(elapsed_list) / len(elapsed_list)

                results.append(result)
                if on_module_done:
                    on_module_done(result, i, total, elapsed, avg)

            on_done(results)

        threading.Thread(target=_worker, daemon=True).start()

    # ── Module dispatcher ──────────────────────────────────────────────────

    def _extract_module(self, module: str) -> iOSExtractionResult:
        res = iOSExtractionResult(module)
        try:
            if module == "ios_device_info":
                self._do_device_info(res)
            elif module == "ios_apps":
                self._do_apps(res)
            elif module == "ios_photos":
                self._do_photos(res)
            else:
                res.error = f"Neznámý modul: {module}"
        except Exception as e:
            res.error = str(e)
        return res

    # ── Extractors ────────────────────────────────────────────────────────

    def _do_device_info(self, res: iOSExtractionResult):
        info = self.manager.get_device_info()
        if not info:
            res.error = t("iosx.info_failed")
            return
        path = os.path.join(self.output_dir, "ios_device_info.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
        res.data = info
        res.file_path = path
        res.count = len(info)
        res.success = True

    def _do_apps(self, res: iOSExtractionResult):
        apps = self.manager.get_installed_apps()
        path = os.path.join(self.output_dir, "ios_apps.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(apps, f, ensure_ascii=False, indent=2)
        res.data = apps
        res.file_path = path
        res.count = len(apps)
        res.success = True

    def _do_photos(self, res: iOSExtractionResult):
        photos_dir = os.path.join(self.output_dir, "ios_photos")
        os.makedirs(photos_dir, exist_ok=True)

        def _progress(remote_path: str):
            self._log(t("x.copying").format(name=os.path.basename(remote_path)))

        copied = self.manager.pull_photos(photos_dir, progress_cb=_progress)
        res.data = [{"filename": os.path.basename(p)} for p in copied]
        res.file_path = photos_dir
        res.count = len(copied)
        res.success = True
