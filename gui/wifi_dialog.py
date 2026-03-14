import customtkinter as ctk
import threading
from core.adb_manager import ADBManager
from core.i18n import t

ACCENT    = "#00d4ff"
BG_CARD   = "#16213e"
BG_PANEL  = "#1a1a2e"
BG_DARK   = "#0f0f1a"
TEXT_MUTED = "#888888"


class WifiConnectDialog(ctk.CTkToplevel):
    """Dialog pro připojení k zařízení přes Wi-Fi (ADB over TCP/IP)."""

    def __init__(self, master, adb: ADBManager, on_connected=None):
        super().__init__(master)
        self.adb = adb
        self.on_connected = on_connected

        self.title(t("wifi.title"))
        self.geometry("480x420")
        self.resizable(False, False)
        self.configure(fg_color=BG_DARK)
        self.grab_set()

        self._build()

    def _build(self):
        pad = {"padx": 25, "pady": 8}

        # Nadpis
        ctk.CTkLabel(self, text=t("wifi.heading"),
                     font=ctk.CTkFont(size=17, weight="bold"),
                     text_color=ACCENT).pack(padx=25, pady=(22, 4), anchor="w")

        ctk.CTkLabel(self,
                     text=t("wifi.subtitle"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED,
                     wraplength=430).pack(padx=25, pady=(0, 15), anchor="w")

        # Krok 1 – aktivace TCP/IP
        step1 = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=10)
        step1.pack(fill="x", padx=20, pady=4)

        ctk.CTkLabel(step1, text=t("wifi.step1_title"),
                     font=ctk.CTkFont(size=12, weight="bold")).pack(padx=15, pady=(12, 4), anchor="w")
        ctk.CTkLabel(step1, text=t("wifi.step1_desc"),
                     font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(padx=15, anchor="w")

        self._step1_btn = ctk.CTkButton(
            step1, text=t("wifi.step1_btn"),
            fg_color=ACCENT, text_color="#000",
            height=32, command=self._enable_tcpip
        )
        self._step1_btn.pack(padx=15, pady=(8, 12), anchor="w")

        # IP autodetect
        ip_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=10)
        ip_frame.pack(fill="x", padx=20, pady=4)
        ip_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(ip_frame, text=t("wifi.step2_title"),
                     font=ctk.CTkFont(size=12, weight="bold")).grid(
            row=0, column=0, columnspan=3, padx=15, pady=(12, 4), sticky="w"
        )

        self._ip_var = ctk.StringVar()
        ctk.CTkEntry(ip_frame, textvariable=self._ip_var,
                     placeholder_text=t("wifi.placeholder"),
                     height=34).grid(row=1, column=0, columnspan=2, padx=15, sticky="ew", pady=(0, 4))

        ctk.CTkButton(ip_frame, text=t("wifi.detect_ip"), width=90, height=34,
                      fg_color="#2a2a4a", hover_color="#3a3a5a",
                      command=self._detect_ip).grid(row=1, column=2, padx=(4, 15), pady=(0, 4))

        ctk.CTkLabel(ip_frame, text=t("wifi.port"),
                     font=ctk.CTkFont(size=10), text_color=TEXT_MUTED).grid(
            row=2, column=0, padx=15, pady=(0, 12), sticky="w"
        )

        # Krok 3 – připojit
        step3 = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=10)
        step3.pack(fill="x", padx=20, pady=4)

        ctk.CTkLabel(step3, text=t("wifi.step3_title"),
                     font=ctk.CTkFont(size=12, weight="bold")).pack(padx=15, pady=(12, 4), anchor="w")

        self._connect_btn = ctk.CTkButton(
            step3, text=t("wifi.connect_btn"),
            fg_color=ACCENT, text_color="#000",
            height=32, command=self._connect
        )
        self._connect_btn.pack(padx=15, pady=(4, 12), anchor="w")

        # Status
        self._status_label = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12),
            wraplength=430
        )
        self._status_label.pack(padx=25, pady=(10, 5), anchor="w")

        # Zavřít
        ctk.CTkButton(self, text=t("wifi.close"), width=90, height=32,
                      fg_color="#2a2a4a", hover_color="#3a3a5a",
                      command=self.destroy).pack(side="right", padx=20, pady=(0, 15))

    def _set_status(self, msg: str, color: str = "#cccccc"):
        self._status_label.configure(text=msg, text_color=color)

    def _enable_tcpip(self):
        self._step1_btn.configure(state="disabled", text=t("wifi.step1_busy"))

        def _worker():
            ok, msg = self.adb.enable_tcpip(5555)
            color = "#4CAF50" if ok else "#f44336"
            self.after(0, lambda: self._set_status(msg, color))
            self.after(0, lambda: self._step1_btn.configure(
                state="normal", text=t("wifi.step1_btn")
            ))

        threading.Thread(target=_worker, daemon=True).start()

    def _detect_ip(self):
        self._set_status(t("wifi.detecting"), TEXT_MUTED)

        def _worker():
            ip = self.adb.get_device_ip()
            if ip:
                self.after(0, lambda: self._ip_var.set(ip))
                self.after(0, lambda: self._set_status(
                    t("wifi.ip_found").format(ip=ip), "#4CAF50"
                ))
            else:
                self.after(0, lambda: self._set_status(
                    t("wifi.ip_fail"), "#FF9800"
                ))

        threading.Thread(target=_worker, daemon=True).start()

    def _connect(self):
        ip = self._ip_var.get().strip()
        if not ip:
            self._set_status(t("wifi.enter_ip"), "#f44336")
            return

        self._connect_btn.configure(state="disabled", text=t("wifi.connect_busy"))
        self._set_status(t("wifi.connecting").format(ip=ip), TEXT_MUTED)

        def _worker():
            ok, msg = self.adb.connect_wifi(ip, 5555)
            color = "#4CAF50" if ok else "#f44336"
            self.after(0, lambda: self._set_status(msg, color))
            self.after(0, lambda: self._connect_btn.configure(
                state="normal", text=t("wifi.connect_btn")
            ))
            if ok and self.on_connected:
                self.after(500, self.on_connected)

        threading.Thread(target=_worker, daemon=True).start()
