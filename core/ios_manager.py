"""
core/ios_manager.py — iOS device manager via pymobiledevice3.

Optional dependency: pip install pymobiledevice3
Falls back gracefully if the library is not installed.
"""
from __future__ import annotations

import os
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
            devices = select_devices_by_connection_type(connection_type="USB")
            result = []
            for dev in devices:
                entry = {
                    "udid": dev.serial,
                    "name": "iOS Device",
                    "model": "",
                    "ios_version": "",
                }
                try:
                    ld = create_using_usbmux(serial=dev.serial)
                    vals = ld.all_values
                    entry.update({
                        "name": vals.get("DeviceName", "iOS Device"),
                        "model": vals.get("ProductType", ""),
                        "ios_version": vals.get("ProductVersion", ""),
                    })
                except Exception:
                    pass
                result.append(entry)
            return result
        except Exception:
            return []

    # ── Connection ─────────────────────────────────────────────────────────

    def connect_device(self, udid: str) -> bool:
        if not _HAS_LIB:
            return False
        try:
            self._lockdown = create_using_usbmux(serial=udid)
            self._connected_udid = udid
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
            vals = self._lockdown.all_values
            total = vals.get("TotalDiskCapacity", 0)
            free = vals.get("TotalDataAvailable", 0)
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
        except Exception as e:
            return {"error": str(e)}

    def get_installed_apps(self) -> list[dict]:
        if not self._lockdown:
            return []
        try:
            from pymobiledevice3.services.installation_proxy import InstallationProxyService
            svc = InstallationProxyService(lockdown=self._lockdown)
            apps = svc.get_apps(application_type="User")
            result = []
            for bundle_id, info in apps.items():
                result.append({
                    "bundle_id": bundle_id,
                    "name":      info.get("CFBundleDisplayName") or info.get("CFBundleName", bundle_id),
                    "version":   info.get("CFBundleShortVersionString", ""),
                    "build":     info.get("CFBundleVersion", ""),
                })
            return result
        except Exception:
            return []

    def pull_photos(self, dest_dir: str, progress_cb=None) -> list[str]:
        """Copy DCIM folder via AFC. Returns list of copied local paths."""
        if not self._lockdown:
            return []
        try:
            from pymobiledevice3.services.afc import AfcService
            afc = AfcService(lockdown=self._lockdown)
            os.makedirs(dest_dir, exist_ok=True)
            copied: list[str] = []
            _afc_copy_dir(afc, "DCIM", dest_dir, copied, progress_cb)
            return copied
        except Exception:
            return []


# ── AFC helper ──────────────────────────────────────────────────────────────

def _afc_copy_dir(afc, remote_path: str, local_dir: str,
                  copied: list, progress_cb):
    try:
        items = afc.listdir(remote_path)
    except Exception:
        return
    for item in items:
        remote_child = f"{remote_path}/{item}"
        try:
            stat = afc.stat(remote_child)
            if stat.get("st_ifmt") == "S_IFDIR":
                local_child = os.path.join(local_dir, item)
                os.makedirs(local_child, exist_ok=True)
                _afc_copy_dir(afc, remote_child, local_child, copied, progress_cb)
            else:
                local_path = os.path.join(local_dir, item)
                with afc.open(remote_child, "rb") as f:
                    data = f.read()
                with open(local_path, "wb") as out:
                    out.write(data)
                copied.append(local_path)
                if progress_cb:
                    progress_cb(remote_child)
        except Exception:
            pass
