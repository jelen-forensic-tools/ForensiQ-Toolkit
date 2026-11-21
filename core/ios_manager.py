"""
core/ios_manager.py — iOS device manager via pymobiledevice3.

Optional dependency: pip install pymobiledevice3
Falls back gracefully if the library is not installed.

pymobiledevice3 ≥ 5 is asyncio-based (usbmux, lockdown, services are coroutines,
AFC must be used as an async context manager). All library calls therefore run
on one persistent event loop in a background thread – a lockdown connection is
bound to the loop it was created in. `_aw()` accepts both the old synchronous
and the new asynchronous API. Public methods stay synchronous and are meant to
be called from worker threads (never from the Tk main thread).
"""
from __future__ import annotations

import asyncio
import inspect
import os
import threading
from typing import Optional

from .i18n import t

# ── Optional import ────────────────────────────────────────────────────────
try:
    from pymobiledevice3.usbmux import select_devices_by_connection_type
    from pymobiledevice3.lockdown import create_using_usbmux
    _HAS_LIB = True
except ImportError:
    _HAS_LIB = False


def has_pymobiledevice() -> bool:
    return _HAS_LIB


def _fmt_size(n: int) -> str:
    if n >= 1_073_741_824:
        return f"{n / 1_073_741_824:.1f} GB"
    if n >= 1_048_576:
        return f"{n / 1_048_576:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


# ── Async bridge ───────────────────────────────────────────────────────────

_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_lock = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, name="ios-asyncio", daemon=True).start()
        return _loop


def _run(coro, timeout: Optional[float] = None):
    """Run a coroutine on the shared loop and wait for its result (call from a worker thread)."""
    return asyncio.run_coroutine_threadsafe(coro, _get_loop()).result(timeout)


async def _aw(value):
    """Await `value` if the library returned an awaitable (new API), else return it (old API)."""
    if inspect.isawaitable(value):
        return await value
    return value


class _Service:
    """`async with` for services that support it (AFC in new versions), plain object otherwise."""

    def __init__(self, svc):
        self.svc = svc

    async def __aenter__(self):
        if hasattr(self.svc, "__aenter__"):
            res = await self.svc.__aenter__()
            return res if res is not None else self.svc
        return self.svc

    async def __aexit__(self, *exc):
        if hasattr(self.svc, "__aexit__"):
            await self.svc.__aexit__(*exc)
        elif hasattr(self.svc, "close"):
            try:
                await _aw(self.svc.close())
            except Exception:
                pass
        return False


async def _all_values(ld) -> dict:
    if hasattr(ld, "get_value"):
        vals = await _aw(ld.get_value())
    else:                                   # very old versions
        vals = getattr(ld, "all_values", {})
    return dict(vals or {})


class iOSManager:
    """Wrapper around pymobiledevice3 for iOS device communication."""

    def __init__(self):
        self._lockdown = None
        self._connected_udid: Optional[str] = None

    # ── Library check ──────────────────────────────────────────────────────

    def check_library(self) -> tuple[bool, str]:
        if _HAS_LIB:
            return True, "pymobiledevice3 OK"
        return False, t("iosx.no_pmd3")

    # ── Device discovery ───────────────────────────────────────────────────

    def get_devices(self) -> list[dict]:
        """Return list of connected iOS devices via USB."""
        if not _HAS_LIB:
            return []
        try:
            return _run(self._get_devices(), timeout=60)
        except Exception:
            return []

    async def _get_devices(self) -> list[dict]:
        devices = await _aw(select_devices_by_connection_type(connection_type="USB"))
        result = []
        for dev in devices:
            entry = {"udid": dev.serial, "name": "iOS Device", "model": "", "ios_version": ""}
            ld = None
            try:
                ld = await _aw(create_using_usbmux(serial=dev.serial))
                vals = await _all_values(ld)
                entry.update({
                    "name": vals.get("DeviceName", "iOS Device"),
                    "model": vals.get("ProductType", ""),
                    "ios_version": vals.get("ProductVersion", ""),
                })
            except Exception:
                pass
            finally:
                if ld is not None and hasattr(ld, "close"):
                    try:
                        await _aw(ld.close())
                    except Exception:
                        pass
            result.append(entry)
        return result

    # ── Connection ─────────────────────────────────────────────────────────

    def connect_device(self, udid: str) -> bool:
        if not _HAS_LIB:
            return False
        try:
            old = self._lockdown
            self._lockdown = _run(_aw(create_using_usbmux(serial=udid)), timeout=60)
            self._connected_udid = udid
            if old is not None and hasattr(old, "close"):
                try:
                    _run(_aw(old.close()), timeout=10)
                except Exception:
                    pass
            return True
        except Exception:
            self._lockdown = None
            self._connected_udid = None
            return False

    def is_connected(self) -> bool:
        return self._lockdown is not None

    @property
    def connected_udid(self) -> Optional[str]:
        return self._connected_udid

    # ── Data access ────────────────────────────────────────────────────────

    def get_device_info(self) -> dict:
        if not self._lockdown:
            return {}
        try:
            return _run(self._get_device_info(), timeout=60)
        except Exception as e:
            return {"error": str(e)}

    async def _get_device_info(self) -> dict:
        ld = self._lockdown
        vals = await _all_values(ld)
        total = vals.get("TotalDiskCapacity", 0)
        free = vals.get("TotalDataAvailable", 0)
        if not total and hasattr(ld, "get_value"):
            try:   # capacity lives in its own lockdown domain
                disk = await _aw(ld.get_value(domain="com.apple.disk_usage")) or {}
                total = disk.get("TotalDiskCapacity", 0)
                free = disk.get("TotalDataAvailable", 0)
            except Exception:
                pass
        return {
            "device_name":      vals.get("DeviceName", ""),
            "model":            vals.get("ProductType", ""),
            "ios_version":      vals.get("ProductVersion", ""),
            "build":            vals.get("BuildVersion", ""),
            "udid":             vals.get("UniqueDeviceID", ""),
            "serial":           vals.get("SerialNumber", ""),
            "imei":             vals.get("InternationalMobileEquipmentIdentity", ""),
            "wifi_mac":         vals.get("WiFiAddress", ""),
            "bluetooth_mac":    vals.get("BluetoothAddress", ""),
            "cpu_arch":         vals.get("CPUArchitecture", ""),
            "hardware_model":   vals.get("HardwareModel", ""),
            "passcode":         str(vals.get("PasswordProtected", False)),
            "timezone":         vals.get("TimeZone", ""),
            "storage_total":    _fmt_size(total) if total else "",
            "storage_free":     _fmt_size(free) if free else "",
            "phone":            vals.get("PhoneNumber", ""),
            "region":           vals.get("RegionInfo", ""),
            "color":            vals.get("DeviceColor", ""),
        }

    def get_installed_apps(self) -> list[dict]:
        if not self._lockdown:
            return []
        try:
            return _run(self._get_installed_apps(), timeout=300)
        except Exception:
            return []

    async def _get_installed_apps(self) -> list[dict]:
        from pymobiledevice3.services.installation_proxy import InstallationProxyService
        async with _Service(InstallationProxyService(lockdown=self._lockdown)) as svc:
            apps = await _aw(svc.get_apps(application_type="User"))
        result = []
        for bundle_id, info in (apps or {}).items():
            result.append({
                "bundle_id": bundle_id,
                "name":      info.get("CFBundleDisplayName") or info.get("CFBundleName", bundle_id),
                "version":   info.get("CFBundleShortVersionString", ""),
                "build":     info.get("CFBundleVersion", ""),
            })
        return result

    def pull_photos(self, dest_dir: str, progress_cb=None) -> list[str]:
        """Copy DCIM folder via AFC. Returns list of copied local paths."""
        if not self._lockdown:
            return []
        os.makedirs(dest_dir, exist_ok=True)
        copied: list[str] = []
        try:
            _run(self._pull_photos(dest_dir, copied, progress_cb))
        except Exception:
            pass
        return copied

    async def _pull_photos(self, dest_dir: str, copied: list, progress_cb):
        from pymobiledevice3.services.afc import AfcService
        async with _Service(AfcService(lockdown=self._lockdown)) as afc:
            await _afc_copy_dir(afc, "DCIM", dest_dir, copied, progress_cb)


# ── AFC helper ──────────────────────────────────────────────────────────────

async def _afc_read(afc, path: str) -> bytes:
    if hasattr(afc, "get_file_contents"):
        return await _aw(afc.get_file_contents(path))
    f = await _aw(afc.open(path, "rb"))          # very old API
    try:
        return await _aw(f.read())
    finally:
        await _aw(f.close())


async def _afc_copy_dir(afc, remote_path: str, local_dir: str,
                        copied: list, progress_cb):
    try:
        items = await _aw(afc.listdir(remote_path))
    except Exception:
        return
    for item in items:
        if item in (".", ".."):
            continue
        remote_child = f"{remote_path}/{item}"
        try:
            stat = await _aw(afc.stat(remote_child))
            if stat.get("st_ifmt") == "S_IFDIR":
                local_child = os.path.join(local_dir, item)
                os.makedirs(local_child, exist_ok=True)
                await _afc_copy_dir(afc, remote_child, local_child, copied, progress_cb)
            else:
                data = await _afc_read(afc, remote_child)
                local_path = os.path.join(local_dir, item)
                with open(local_path, "wb") as out:
                    out.write(data)
                mt = stat.get("st_mtime")
                ts = mt.timestamp() if hasattr(mt, "timestamp") else (mt / 1e9 if isinstance(mt, int) and mt > 1e12 else mt)
                if isinstance(ts, (int, float)) and ts > 0:
                    os.utime(local_path, (ts, ts))      # keep the device timestamp
                copied.append(local_path)
                if progress_cb:
                    progress_cb(remote_child)
        except Exception:
            pass
