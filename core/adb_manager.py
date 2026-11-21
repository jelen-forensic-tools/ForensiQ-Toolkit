import subprocess
import re
import os
import sys
import threading
from typing import Optional, Callable

from .i18n import t

# On Windows, prevent every adb/fastboot call from flashing a console window
# (in the "windowed" .exe a black window would otherwise open on every command/poll).
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class ADBManager:
    def __init__(self):
        self.adb_path = self._find_adb()
        self.connected_device: Optional[str] = None

    def _find_adb(self) -> str:
        """Find ADB – in the bundle (.exe), next to the .exe, in the project, or on PATH."""
        candidates = []
        if getattr(sys, "frozen", False):
            # PyInstaller: next to the executable and in the unpacked bundle
            exe_dir = os.path.dirname(sys.executable)
            candidates.append(os.path.join(exe_dir, "adb", "adb.exe"))
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                candidates.append(os.path.join(meipass, "adb", "adb.exe"))
        # Local adb in the project (running from source)
        candidates.append(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "adb", "adb.exe")
        )
        for c in candidates:
            if os.path.exists(c):
                return c
        return "adb"  # assume adb is on PATH

    def _run(self, args: list, timeout: int | None = 30) -> tuple[int, str, str]:
        """Run an ADB command and return (returncode, stdout, stderr).
        timeout=None means no time limit (suitable for large file transfers)."""
        try:
            cmd = [self.adb_path] + args
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout if timeout and timeout > 0 else None,
                encoding="utf-8",
                errors="replace",
                creationflags=_CREATE_NO_WINDOW,
            )
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", t("adbm.timeout").format(s=timeout)
        except FileNotFoundError:
            return -1, "", t("adbm.not_found")
        except Exception as e:
            return -1, "", str(e)

    def check_adb(self) -> tuple[bool, str]:
        """Check whether ADB is available."""
        rc, out, err = self._run(["version"])
        if rc == 0:
            version = out.split("\n")[0] if out else t("val.unknown_version")
            return True, version
        return False, err

    def get_devices(self) -> list[dict]:
        """Return the list of connected devices."""
        rc, out, err = self._run(["devices", "-l"])
        devices = []
        if rc != 0:
            return devices

        lines = out.split("\n")
        for line in lines[1:]:
            line = line.strip()
            if not line or "offline" in line:
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                serial = parts[0]
                model = ""
                for part in parts[2:]:
                    if part.startswith("model:"):
                        model = part.replace("model:", "").replace("_", " ")
                devices.append({"serial": serial, "model": model})
        return devices

    def connect_device(self, serial: str) -> bool:
        self.connected_device = serial
        return True

    def is_connected(self) -> bool:
        """Return True if the currently tracked device is still online."""
        if not self.connected_device:
            return False
        devices = self.get_devices()
        return any(d["serial"] == self.connected_device for d in devices)

    def shell(self, cmd: str, timeout: int = 30) -> str:
        """Run a shell command on the device."""
        if not self.connected_device:
            return ""
        rc, out, err = self._run(["-s", self.connected_device, "shell", cmd], timeout=timeout)
        return out

    def pull(self, remote: str, local: str, timeout: int = 120) -> bool:
        """Pull a file from the device."""
        if not self.connected_device:
            return False
        rc, out, err = self._run(["-s", self.connected_device, "pull", remote, local], timeout=timeout)
        return rc == 0

    def pull_err(self, remote: str, local: str, timeout: int | None = 120) -> tuple[bool, str]:
        """Pull a file from the device; returns (ok, error_msg)."""
        if not self.connected_device:
            return False, t("adbm.not_connected")
        rc, out, err = self._run(["-s", self.connected_device, "pull", remote, local], timeout=timeout)
        if rc == 0:
            return True, ""
        msg = (err or out or "").strip()
        # Keep only the first line if there are several
        msg = msg.splitlines()[0] if msg else f"rc={rc}"
        return False, msg

    def get_device_info(self) -> dict:
        """Get basic information about the device."""
        props = {
            "model":            "ro.product.model",
            "manufacturer":     "ro.product.manufacturer",
            "android":          "ro.build.version.release",
            "sdk":              "ro.build.version.sdk",
            "serial":           "ro.serialno",
            "imei":             "persist.radio.imei",
            "build":            "ro.build.display.id",
            "brand":            "ro.product.brand",
            "device":           "ro.product.device",
            "cpu_abi":          "ro.product.cpu.abi",
            "security_patch":   "ro.build.version.security_patch",
            # Software extras
            "build_type":       "ro.build.type",
            "build_tags":       "ro.build.tags",
            "build_fingerprint":"ro.build.fingerprint",
            "bootloader":       "ro.bootloader",
            "baseband":         "gsm.version.baseband",
            "java_vm":          "ro.dalvik.vm.native.bridge",
            "locale":           "ro.product.locale",
            "timezone":         "persist.sys.timezone",
            "usb_config":       "sys.usb.config",
            "treble":           "ro.treble.enabled",
            "vndk_version":     "ro.vndk.version",
        }
        # All system properties at once – a single `getprop` dump instead of
        # ~23 separate `adb shell getprop <key>` calls (each is a round trip
        # over USB). The output consists of `[key]: [value]` lines.
        prop_dump = self.shell("getprop")
        all_props: dict = {}
        for pm in re.finditer(r"^\[([^\]]+)\]:\s*\[(.*)\]$", prop_dump, re.MULTILINE):
            all_props[pm.group(1)] = pm.group(2)

        info = {}
        for key, prop in props.items():
            val = all_props.get(prop, "").strip()
            info[key] = val if val else "N/A"

        # Format the IMEI from getprop (digits only, a space every 3)
        if info.get("imei") and info["imei"] != "N/A":
            digits = re.sub(r"[^0-9]", "", info["imei"])
            if digits:
                info["imei"] = " ".join(digits[i:i+3] for i in range(0, len(digits), 3))

        # IMEI fallback
        if info.get("imei") == "N/A":
            imei_raw = self.shell("service call iphonesubinfo 1")
            imeis = re.findall(r"'([0-9. ]+)'", imei_raw)
            if imeis:
                digits = re.sub(r"[^0-9]", "", "".join(imeis))
                info["imei"] = " ".join(digits[i:i+3] for i in range(0, len(digits), 3))

        # Encryption – also a plain property, taken from the same dump.
        crypto = all_props.get("ro.crypto.state", "").strip()
        info["encryption"] = crypto if crypto else "N/A"

        # Remaining (non-getprop) queries merged into a single compound command
        # separated by unique markers – instead of ~9 separate shell calls.
        _MARK = "\x1f@FQ@\x1f"
        batch_cmd = (
            f"echo '{_MARK}battery'; dumpsys battery 2>/dev/null | grep level; "
            f"echo '{_MARK}kernel'; uname -r; "
            f"echo '{_MARK}selinux'; getenforce 2>/dev/null; "
            f"echo '{_MARK}root'; which su 2>/dev/null || su -c id 2>/dev/null; "
            f"echo '{_MARK}dev'; settings get global development_settings_enabled 2>/dev/null; "
            f"echo '{_MARK}mem'; cat /proc/meminfo 2>/dev/null | grep MemTotal; "
            f"echo '{_MARK}wsize'; wm size 2>/dev/null; "
            f"echo '{_MARK}wdens'; wm density 2>/dev/null; "
            f"echo '{_MARK}df'; df /data 2>/dev/null | tail -1; "
            f"echo '{_MARK}end'"
        )
        sections: dict = {}
        cur = None
        for ln in self.shell(batch_cmd).split("\n"):
            s = ln.rstrip("\r")
            if s.startswith(_MARK):
                cur = s[len(_MARK):].strip()
                sections[cur] = []
            elif cur is not None:
                sections[cur].append(s)
        def _sec(name: str) -> str:
            return "\n".join(sections.get(name, [])).strip()

        # Battery level
        match = re.search(r"level:\s*(\d+)", _sec("battery"))
        info["battery"] = f"{match.group(1)}%" if match else "N/A"

        # Kernel version
        kernel = _sec("kernel")
        info["kernel"] = kernel if kernel else "N/A"

        # SELinux status
        selinux = _sec("selinux")
        info["selinux"] = selinux if selinux else "N/A"

        # Root check
        root_check = _sec("root")
        info["rooted"] = "Ano" if ("uid=0" in root_check or "/su" in root_check or "/bin/su" in root_check) else "Ne"

        # Developer mode
        dev_mode = _sec("dev")
        info["dev_mode"] = "Zapnuto" if dev_mode.strip() == "1" else "Vypnuto"

        # RAM
        m = re.search(r"MemTotal:\s*(\d+)\s*kB", _sec("mem"))
        if m:
            mb = int(m.group(1)) // 1024
            info["ram"] = f"{mb} MB ({round(mb/1024, 1)} GB)"
        else:
            info["ram"] = "N/A"

        # Screen resolution
        m = re.search(r"Physical size:\s*(\S+)", _sec("wsize"))
        info["screen_resolution"] = m.group(1) if m else "N/A"

        # DPI
        m = re.search(r"Physical density:\s*(\d+)", _sec("wdens"))
        info["screen_dpi"] = f"{m.group(1)} DPI" if m else "N/A"

        # Storage (/data)
        df_out = _sec("df")
        info["storage_raw"] = df_out
        parts = df_out.split()
        if len(parts) >= 4:
            def _to_mb(val: str) -> str:
                try:
                    kb = int(re.sub(r"[^0-9]", "", val))
                    return f"{round(kb/1024)} MB" if kb < 1024*1024 else f"{round(kb/1024/1024, 1)} GB"
                except Exception:
                    return val
            info["storage_total"] = _to_mb(parts[1])
            info["storage_used"]  = _to_mb(parts[2])
            info["storage_free"]  = _to_mb(parts[3])
        else:
            info["storage_total"] = info["storage_used"] = info["storage_free"] = "N/A"

        return info

    def get_installed_apps(self, include_system: bool = False) -> list[str]:
        """Return the list of installed applications."""
        flag = "" if include_system else "-3"
        out = self.shell(f"pm list packages {flag}")
        packages = []
        for line in out.split("\n"):
            line = line.strip()
            if line.startswith("package:"):
                packages.append(line.replace("package:", ""))
        return sorted(packages)

    def pull_async(self, remote: str, local: str,
                   callback: Callable[[bool], None], timeout: int = 300):
        """Pull a file asynchronously."""
        def _worker():
            result = self.pull(remote, local, timeout)
            callback(result)
        threading.Thread(target=_worker, daemon=True).start()

    # ── Wi-Fi ADB ─────────────────────────────────────────────────────────────

    def get_device_ip(self) -> str:
        """Find the device IP address (wlan0)."""
        if not self.connected_device:
            return ""
        # Try ip route
        out = self.shell("ip route | grep wlan0")
        m = re.search(r"src\s+([\d.]+)", out)
        if m:
            return m.group(1)
        # Try ifconfig
        out2 = self.shell("ifconfig wlan0 2>/dev/null | grep 'inet '")
        m2 = re.search(r"inet\s+([\d.]+)", out2)
        if m2:
            return m2.group(1)
        # Try ip addr
        out3 = self.shell("ip addr show wlan0 | grep 'inet '")
        m3 = re.search(r"inet\s+([\d.]+)", out3)
        if m3:
            return m3.group(1)
        return ""

    def enable_tcpip(self, port: int = 5555) -> tuple[bool, str]:
        """Switch the device to TCP/IP mode (requires a USB connection)."""
        if not self.connected_device:
            return False, t("adbm.no_usb")
        rc, out, err = self._run(["-s", self.connected_device, "tcpip", str(port)])
        if rc == 0 or "restarting" in out.lower():
            return True, t("adbm.tcpip_on").format(port=port)
        return False, err or out

    def connect_wifi(self, ip: str, port: int = 5555) -> tuple[bool, str]:
        """Connect to the device over Wi-Fi."""
        rc, out, err = self._run(["connect", f"{ip}:{port}"], timeout=15)
        combined = (out + err).lower()
        if "connected" in combined:
            self.connected_device = f"{ip}:{port}"
            return True, t("adbm.connected").format(addr=f"{ip}:{port}")
        if "already connected" in combined:
            self.connected_device = f"{ip}:{port}"
            return True, t("adbm.already").format(addr=f"{ip}:{port}")
        return False, out or err or t("adbm.connect_failed")

    def disconnect_wifi(self, ip: str, port: int = 5555) -> tuple[bool, str]:
        """Disconnect a Wi-Fi device."""
        rc, out, err = self._run(["disconnect", f"{ip}:{port}"])
        if rc == 0:
            if self.connected_device == f"{ip}:{port}":
                self.connected_device = None
            return True, f"Odpojeno od {ip}:{port}"
        return False, err or out
