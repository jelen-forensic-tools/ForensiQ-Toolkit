"""
gui/bypass_panel.py – Panel for forensic screen-lock bypass.
"""
import threading
import customtkinter as ctk
from core.i18n import t
from core.bypass import BypassManager

ACCENT    = "#00d4ff"
BG_CARD   = "#16213e"
BG_PANEL  = "#1a1a2e"
BG_DARK   = "#0f0f1a"
BG_LEFT   = "#0f1a30"
TEXT_MUTED = "#888888"
TEXT_DIM   = "#555555"
GREEN     = "#00c853"
GREEN_DIM = "#1a3d28"
RED       = "#ff5252"
RED_DIM   = "#3d1a1f"
ORANGE    = "#ff9800"
ORANGE_DIM = "#3d2800"
WHITE     = "#ffffff"

# Method compatibility by Android version
COMPAT = [
    ("Android 4 – 6",  "✔ ADB root",  "✔ TWRP",       GREEN,  GREEN),
    ("Android 7 – 8",  "✔ ADB root",  "✔ TWRP (FDE)", GREEN,  ORANGE),
    ("Android 9 – 10", "✔ ADB root",  "⚠ TWRP+FBE",  GREEN,  ORANGE),
    ("Android 11 – 13","⚠ Magisk",    "⚠ TWRP+FBE",  ORANGE, ORANGE),
    ("Android 14+",    "⚠ Magisk",    "⚠ TWRP+FBE",  ORANGE, ORANGE),
]


class BypassPanel(ctk.CTkFrame):
    def __init__(self, master, adb, accent: str = ACCENT, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.accent = accent
        self.bypass = BypassManager(adb)
        self._selected_method = ctk.StringVar(value="adb")
        self._method_btns: dict = {}   # value → CTkFrame
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build(self):
        # ── Warning banner ────────────────────────────────────────────────────
        warn = ctk.CTkFrame(self, fg_color=ORANGE_DIM, corner_radius=10)
        warn.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        warn.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            warn, text="⚠", font=ctk.CTkFont(size=20), text_color=ORANGE,
        ).grid(row=0, column=0, padx=(16, 8), pady=12)
        ctk.CTkLabel(
            warn,
            text=t("byp.warning"),
            font=ctk.CTkFont(size=12),
            text_color=ORANGE,
            wraplength=800,
            justify="left",
            anchor="w",
        ).grid(row=0, column=1, padx=(0, 16), pady=12, sticky="w")

        # ── Body (left + right columns) ──────────────────────────────────────
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3, minsize=380)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        self._build_left(body)
        self._build_right(body)

    def _build_left(self, parent):
        left = ctk.CTkScrollableFrame(parent, fg_color=BG_LEFT, corner_radius=12)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.grid_columnconfigure(0, weight=1)

        # Section title
        ctk.CTkLabel(
            left, text=t("byp.select_method"),
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=WHITE,
        ).grid(row=0, column=0, padx=16, pady=(18, 10), sticky="w")

        # Method 1 – ADB root
        self._method_btns["adb"] = self._method_btn(
            left, row=1,
            icon="🔓", title=t("byp.m1_title"), sub=t("byp.m1_sub"),
            value="adb",
        )
        # Method 2 – TWRP
        self._method_btns["twrp"] = self._method_btn(
            left, row=2,
            icon="🛠", title=t("byp.m2_title"), sub=t("byp.m2_sub"),
            value="twrp",
        )
        # Method 3 – Keyboard unlock (broken touchscreen)
        self._method_btns["kbd"] = self._method_btn(
            left, row=3,
            icon="⌨", title=t("byp.m3_title"), sub=t("byp.m3_sub"),
            value="kbd",
        )

        # Separator
        ctk.CTkFrame(left, height=1, fg_color="#2a2a3e").grid(
            row=4, column=0, sticky="ew", padx=14, pady=12
        )

        # Reboot section
        ctk.CTkLabel(
            left, text=t("byp.reboot_section"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_MUTED,
        ).grid(row=5, column=0, padx=16, pady=(0, 6), sticky="w")

        for row, (label_key, cmd) in enumerate([
            ("byp.reboot_recovery",   "recovery"),
            ("byp.reboot_bootloader", "bootloader"),
            ("byp.reboot_system",     "system"),
        ], start=6):
            btn = ctk.CTkButton(
                left, text=t(label_key),
                font=ctk.CTkFont(size=12),
                fg_color="#2a2a4a", hover_color="#3a3a5a",
                height=34, anchor="w",
                command=lambda c=cmd: self._do_reboot(c),
            )
            btn.grid(row=row, column=0, padx=12, pady=3, sticky="ew")

        # Separator
        ctk.CTkFrame(left, height=1, fg_color="#2a2a3e").grid(
            row=9, column=0, sticky="ew", padx=14, pady=12
        )

        # Diagnostics / scan
        self._btn_scan = ctk.CTkButton(
            left, text=t("byp.scan"),
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=ACCENT, text_color="#000",
            height=36,
            command=self._run_scan,
        )
        self._btn_scan.grid(row=10, column=0, padx=12, pady=(0, 6), sticky="ew")

        # ── FRP section ────────────────────────────────────────────────────────
        ctk.CTkFrame(left, height=1, fg_color="#2a2a3e").grid(
            row=11, column=0, sticky="ew", padx=14, pady=(12, 6)
        )

        ctk.CTkLabel(
            left, text=t("byp.frp_section"),
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=ORANGE,
        ).grid(row=12, column=0, padx=16, pady=(0, 4), sticky="w")

        # FRP mini-warning
        ctk.CTkLabel(
            left, text=t("byp.frp_warning"),
            font=ctk.CTkFont(size=9),
            text_color=TEXT_DIM,
            wraplength=230,
            justify="left",
        ).grid(row=13, column=0, padx=16, pady=(0, 8), sticky="w")

        frp_methods = [
            ("frp_settings", "🔑", "byp.frp1_title", "byp.frp1_sub"),
            ("frp_twrp",     "💾", "byp.frp2_title", "byp.frp2_sub"),
            ("frp_fastboot", "⚡", "byp.frp3_title", "byp.frp3_sub"),
            ("frp_root",     "🗝", "byp.frp4_title", "byp.frp4_sub"),
        ]
        for i, (value, icon, title_key, sub_key) in enumerate(frp_methods):
            self._method_btns[value] = self._method_btn(
                left, row=14 + i,
                icon=icon, title=t(title_key), sub=t(sub_key),
                value=value,
            )

        # bottom padding
        ctk.CTkLabel(left, text="").grid(row=18, column=0, pady=6)

    def _method_btn(self, parent, row, icon, title, sub, value):
        """Method selection button."""
        is_sel = self._selected_method.get() == value
        frame = ctk.CTkFrame(
            parent,
            fg_color="#1e2a4a" if is_sel else "#16213e",
            corner_radius=8,
            cursor="hand2",
        )
        frame.grid(row=row, column=0, padx=12, pady=4, sticky="ew")
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text=icon, font=ctk.CTkFont(size=22)).grid(
            row=0, column=0, rowspan=2, padx=(12, 8), pady=10
        )
        ctk.CTkLabel(
            frame, text=title,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=ACCENT if is_sel else WHITE,
            anchor="w",
        ).grid(row=0, column=1, sticky="w", pady=(8, 0))
        ctk.CTkLabel(
            frame, text=sub,
            font=ctk.CTkFont(size=10),
            text_color=TEXT_MUTED,
            anchor="w",
        ).grid(row=1, column=1, sticky="w", pady=(0, 8))

        for widget in frame.winfo_children():
            widget.bind("<Button-1>", lambda e, v=value: self._select_method(v))
        frame.bind("<Button-1>", lambda e, v=value: self._select_method(v))
        return frame

    def _build_right(self, parent):
        right = ctk.CTkFrame(parent, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        # ── Info / compatibility table ────────────────────────────────────────
        self._info_card = ctk.CTkFrame(right, fg_color=BG_CARD, corner_radius=12)
        self._info_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self._info_card.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)
        self._build_compat_table(self._info_card)

        # ── Method detail + log ───────────────────────────────────────────────
        detail = ctk.CTkFrame(right, fg_color=BG_CARD, corner_radius=12)
        detail.grid(row=1, column=0, sticky="nsew")
        detail.grid_columnconfigure(0, weight=1)
        detail.grid_rowconfigure(3, weight=1)

        # Title + action button
        header = ctk.CTkFrame(detail, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))
        header.grid_columnconfigure(1, weight=1)

        self._method_title = ctk.CTkLabel(
            header, text=t("byp.m1_title"),
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=WHITE,
        )
        self._method_title.grid(row=0, column=0, sticky="w")

        self._method_desc = ctk.CTkLabel(
            header, text=t("byp.m1_desc"),
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
            wraplength=500,
            justify="left",
        )
        self._method_desc.grid(row=1, column=0, sticky="w", pady=(2, 8))

        self._btn_run = ctk.CTkButton(
            header, text=t("byp.run"),
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=RED, hover_color="#cc1133",
            text_color=WHITE,
            width=140, height=38,
            command=self._run_method,
        )
        self._btn_run.grid(row=0, column=2, rowspan=2, padx=(10, 0))

        # ── Keyboard-unlock input (only shown for the "kbd" method) ───────────
        self._kbd_frame = ctk.CTkFrame(detail, fg_color="transparent")
        self._kbd_frame.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 4))
        self._kbd_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self._kbd_frame, text=t("byp.kbd_input_label"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_MUTED, anchor="w",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))

        self._kbd_mode = ctk.StringVar(value="pin")
        self._kbd_mode_labels = {
            t("byp.kbd_mode_pin"): "pin",
            t("byp.kbd_mode_password"): "password",
            t("byp.kbd_mode_pattern"): "pattern",
        }
        self._kbd_mode_seg = ctk.CTkSegmentedButton(
            self._kbd_frame,
            values=list(self._kbd_mode_labels.keys()),
            command=self._on_kbd_mode_change,
            font=ctk.CTkFont(size=11),
        )
        self._kbd_mode_seg.set(t("byp.kbd_mode_pin"))
        self._kbd_mode_seg.grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 6))

        self._kbd_entry = ctk.CTkEntry(
            self._kbd_frame,
            placeholder_text=t("byp.kbd_placeholder_pin"),
            show="•",
            font=ctk.CTkFont(size=13),
            height=34,
        )
        self._kbd_entry.grid(row=2, column=0, sticky="ew", padx=(0, 8))

        self._kbd_show = ctk.CTkCheckBox(
            self._kbd_frame, text=t("byp.kbd_show"),
            font=ctk.CTkFont(size=11),
            width=20, command=self._toggle_kbd_show,
        )
        self._kbd_show.grid(row=2, column=1, padx=(0, 4))

        self._kbd_frame.grid_remove()   # hidden until the kbd method is selected

        ctk.CTkFrame(detail, height=1, fg_color="#2a2a3e").grid(
            row=2, column=0, sticky="ew", padx=16, pady=(4, 0)
        )

        # Log output
        log_frame = ctk.CTkFrame(detail, fg_color="transparent")
        log_frame.grid(row=3, column=0, sticky="nsew", padx=16, pady=(8, 6))
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            log_frame, text=t("byp.log"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_MUTED,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        self._log_box = ctk.CTkTextbox(
            log_frame,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color="#0a0f1e",
            text_color="#c8ffc8",
            wrap="word",
            state="disabled",
        )
        self._log_box.grid(row=1, column=0, sticky="nsew")

        # Status bar (bottom)
        self._status_bar = ctk.CTkLabel(
            detail, text="",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
            anchor="w",
        )
        self._status_bar.grid(row=4, column=0, sticky="ew", padx=16, pady=(4, 12))

    def _build_compat_table(self, parent):
        """Compatibility table showing which methods work for each Android version."""
        headers = [t("byp.compat_ver"), t("byp.compat_adb"), t("byp.compat_twrp")]
        for col, h in enumerate(headers):
            ctk.CTkLabel(
                parent, text=h,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=ACCENT,
                anchor="w" if col == 0 else "center",
            ).grid(row=0, column=col, padx=16, pady=(12, 4), sticky="ew")

        for row, (ver, adb_label, twrp_label, adb_col, twrp_col) in enumerate(COMPAT, start=1):
            ctk.CTkLabel(
                parent, text=ver,
                font=ctk.CTkFont(size=11),
                text_color=WHITE,
                anchor="w",
            ).grid(row=row, column=0, padx=16, pady=3, sticky="w")
            ctk.CTkLabel(
                parent, text=adb_label,
                font=ctk.CTkFont(size=11),
                text_color=adb_col,
                anchor="center",
            ).grid(row=row, column=1, padx=8, pady=3)
            ctk.CTkLabel(
                parent, text=twrp_label,
                font=ctk.CTkFont(size=11),
                text_color=twrp_col,
                anchor="center",
            ).grid(row=row, column=2, padx=8, pady=3)

        # Legend / footnote
        ctk.CTkLabel(
            parent,
            text=t("byp.compat_note"),
            font=ctk.CTkFont(size=10),
            text_color=TEXT_DIM,
            anchor="w",
        ).grid(row=len(COMPAT) + 1, column=0, columnspan=3,
               padx=16, pady=(4, 10), sticky="w")

    # ── Interactions ─────────────────────────────────────────────────────────

    def _select_method(self, value: str):
        self._selected_method.set(value)

        _titles = {
            "adb":          ("byp.m1_title", "byp.m1_desc"),
            "twrp":         ("byp.m2_title", "byp.m2_desc"),
            "kbd":          ("byp.m3_title", "byp.m3_desc"),
            "frp_settings": ("byp.frp1_title", "byp.frp1_desc"),
            "frp_twrp":     ("byp.frp2_title", "byp.frp2_desc"),
            "frp_fastboot": ("byp.frp3_title", "byp.frp3_desc"),
            "frp_root":     ("byp.frp4_title", "byp.frp4_desc"),
        }
        title_key, desc_key = _titles.get(value, ("byp.m1_title", "byp.m1_desc"))
        self._method_title.configure(text=t(title_key))
        self._method_desc.configure(text=t(desc_key))

        # Show the passcode input only for the keyboard-unlock method
        if value == "kbd":
            self._kbd_frame.grid()
        else:
            self._kbd_frame.grid_remove()

        is_frp = value.startswith("frp_")
        accent = ORANGE if is_frp else ACCENT

        for btn_val, frame in self._method_btns.items():
            selected = btn_val == value
            frame.configure(fg_color="#1e2a4a" if selected else "#16213e")
            for w in frame.winfo_children():
                if isinstance(w, ctk.CTkLabel) and w.cget("font").cget("weight") == "bold":
                    w.configure(text_color=(accent if selected else WHITE))

    def _run_method(self):
        self._log_clear()
        self._btn_run.configure(state="disabled", text=t("byp.running"))
        self._status_bar.configure(text=t("byp.running"), text_color=ACCENT)

        method = self._selected_method.get()

        if method == "kbd":
            self.bypass.method_input_passcode(
                log=self._log_append_threadsafe,
                done=self._on_done,
                passcode=self._kbd_entry.get(),
                mode=self._kbd_mode.get(),
            )
            return

        _dispatch = {
            "adb":          self.bypass.method_adb_root_delete,
            "twrp":         self.bypass.method_twrp_delete,
            "frp_settings": self.bypass.frp_adb_settings,
            "frp_twrp":     self.bypass.frp_twrp_wipe,
            "frp_fastboot": self.bypass.frp_fastboot_erase,
            "frp_root":     self.bypass.frp_root_delete_account,
        }
        fn = _dispatch.get(method)
        if fn:
            fn(log=self._log_append_threadsafe, done=self._on_done)

    def _on_kbd_mode_change(self, label: str):
        mode = self._kbd_mode_labels.get(label, "pin")
        self._kbd_mode.set(mode)
        placeholder = {
            "pin":      t("byp.kbd_placeholder_pin"),
            "password": t("byp.kbd_placeholder_password"),
            "pattern":  t("byp.kbd_placeholder_pattern"),
        }.get(mode, "")
        # Pattern is a visible node order, not a secret → don't mask it
        self._kbd_entry.configure(
            placeholder_text=placeholder,
            show="" if (mode == "pattern" or self._kbd_show.get()) else "•",
        )

    def _toggle_kbd_show(self):
        reveal = bool(self._kbd_show.get()) or self._kbd_mode.get() == "pattern"
        self._kbd_entry.configure(show="" if reveal else "•")

    def _run_scan(self):
        self._log_clear()
        self._btn_scan.configure(state="disabled", text=t("byp.log_scanning_btn"))
        self._log_append(t("byp.log_scanning") + "\n")

        def _worker():
            if not self.bypass.adb.connected_device:
                self._log_append_threadsafe(t("byp.log_no_device"))
                self.after(0, lambda: self._btn_scan.configure(state="normal", text=t("byp.scan")))
                return

            android = self.bypass.get_android_version()
            enc = self.bypass.check_encryption()
            root_ok, root_info = self.bypass.check_root_available()
            twrp_ok, twrp_info = self.bypass.check_twrp_active()

            self._log_append_threadsafe(t("byp.log_android").format(v=android or "?"))
            self._log_append_threadsafe(t("byp.log_enc").format(v=enc))
            self._log_append_threadsafe(
                t("byp.log_root").format(v=('✔ ' + root_info if root_ok else '✗ ' + root_info))
            )
            self._log_append_threadsafe(
                t("byp.log_twrp").format(v=('✔ ' + twrp_info if twrp_ok else '✗ ' + twrp_info))
            )
            self._log_append_threadsafe("")

            if android >= 11 and not root_ok and not twrp_ok:
                self._log_append_threadsafe(t("byp.log_warn11"))
            elif android >= 7 and twrp_ok:
                self._log_append_threadsafe(t("byp.log_info7"))
            elif root_ok:
                self._log_append_threadsafe(t("byp.log_ok_root"))
            elif twrp_ok:
                self._log_append_threadsafe(t("byp.log_ok_twrp"))

            self.after(0, lambda: self._btn_scan.configure(state="normal", text=t("byp.scan")))

        threading.Thread(target=_worker, daemon=True).start()

    def _do_reboot(self, cmd: str):
        def _worker():
            if cmd == "recovery":
                ok, msg = self.bypass.reboot_recovery()
            elif cmd == "bootloader":
                ok, msg = self.bypass.reboot_bootloader()
            else:
                ok, msg = self.bypass.reboot_system()
            self._log_append_threadsafe(("✔ " if ok else "✗ ") + msg)
        threading.Thread(target=_worker, daemon=True).start()

    def _on_done(self, success: bool, msg: str):
        def _update():
            self._btn_run.configure(state="normal", text=t("byp.run"))
            color = GREEN if success else RED
            self._status_bar.configure(
                text=("✔ " if success else "✗ ") + msg.split("\n")[0],
                text_color=color,
            )
            self._log_append("\n" + ("✔ " if success else "✗ ") + msg)
        self.after(0, _update)

    # ── Log helpers ───────────────────────────────────────────────────────────

    def _log_clear(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    def _log_append(self, text: str):
        self._log_box.configure(state="normal")
        self._log_box.insert("end", text + "\n")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _log_append_threadsafe(self, text: str):
        self.after(0, lambda t=text: self._log_append(t))

    # ── Language refresh ──────────────────────────────────────────────────────

    def refresh_lang(self):
        """Překlad na místě – statické popisky řeší globální retranslate walk,
        tady jen znovu vykreslíme dynamický detail vybrané metody (title/desc)."""
        self._select_method(self._selected_method.get())
