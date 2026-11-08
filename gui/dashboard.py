import customtkinter as ctk
from core.i18n import t
from PIL import Image, ImageDraw

ACCENT      = "#00d4ff"
BG_CARD     = "#16213e"
BG_PANEL    = "#1a1a2e"
BG_LEFT     = "#0f1a30"
TEXT_MUTED  = "#888888"
TEXT_DIM    = "#555555"
GREEN       = "#00c853"
GREEN_DIM   = "#1a3d28"
RED         = "#ff5252"
RED_DIM     = "#3d1a1f"
WHITE       = "#ffffff"


_SUPERSAMPLE = 4   # render 4× větší → CTkImage vždy downscaluje (Lanczos) → ostrá na HiDPI
_icon_cache: dict[tuple, ctk.CTkImage] = {}  # (size, color) → CTkImage


def _make_phone_icon(size: int = 160,
                     color: tuple = (210, 210, 210)) -> ctk.CTkImage:
    """
    Ikona telefonu s PIL supersamplingem + cache.
    Bitmapa se generuje jen jednou pro každou kombinaci (size, color);
    při opakovaném volání se vrací cached objekt.
    """
    key = (size, color)
    if key in _icon_cache:
        return _icon_cache[key]

    s = _SUPERSAMPLE
    W, H = size * s, int(size * 1.9) * s

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)

    stroke = max(2, size // 28) * s
    r      = (size // 6) * s
    pad    = stroke + s

    outline = (*color, 255)
    speaker = (*color, 180)

    d.rounded_rectangle(
        [pad, pad, W - pad - 1, H - pad - 1],
        radius=r, fill=(0, 0, 0, 0), outline=outline, width=stroke,
    )

    sp_w = (size // 4) * s
    sp_y = pad + r // 2
    sp_x0 = W // 2 - sp_w // 2
    sp_x1 = W // 2 + sp_w // 2
    d.rounded_rectangle(
        [sp_x0, sp_y - stroke, sp_x1, sp_y + stroke],
        radius=stroke, fill=speaker,
    )

    result = ctk.CTkImage(light_image=img, dark_image=img, size=(size, int(size * 1.9)))
    _icon_cache[key] = result
    return result


class DeviceDashboard(ctk.CTkFrame):
    def __init__(self, master, accent: str = ACCENT, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.accent = accent
        self._current_info = None
        # Levý panel ~1/3 šířky, pravý ~2/3
        self.grid_columnconfigure(0, weight=1, minsize=260)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(0, weight=1)
        self._build()

    # ------------------------------------------------------------------
    def _build(self):
        # ── Levý panel ──────────────────────────────────────────────
        self._left = ctk.CTkFrame(self, fg_color=BG_LEFT, corner_radius=12)
        self._left.grid(row=0, column=0, sticky="nsew", padx=(5, 3), pady=5)
        self._left.grid_propagate(False)
        self._left.grid_columnconfigure(0, weight=1)
        self._left.grid_rowconfigure(2, weight=1)   # volné místo mezi ikonou a textem

        # Ikona telefonu
        self._phone_img = _make_phone_icon(size=140)
        self._phone_icon = ctk.CTkLabel(
            self._left, text="", image=self._phone_img,
        )
        self._phone_icon.grid(row=0, column=0, pady=(20, 8))

        # Název zařízení
        self._device_name = ctk.CTkLabel(
            self._left, text=t("dash.no_device"),
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=TEXT_MUTED,
            wraplength=180,
            justify="center",
        )
        self._device_name.grid(row=1, column=0, padx=16)

        # Status badge (pill)
        self._badge_frame = ctk.CTkFrame(self._left, fg_color=RED_DIM, corner_radius=20)
        self._badge_frame.grid(row=3, column=0, pady=(12, 6))

        self._badge_dot = ctk.CTkLabel(
            self._badge_frame, text="●",
            font=ctk.CTkFont(size=11),
            text_color=RED,
        )
        self._badge_dot.grid(row=0, column=0, padx=(10, 4), pady=5)

        self._badge_text = ctk.CTkLabel(
            self._badge_frame, text=t("dash.disconnected"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=RED,
        )
        self._badge_text.grid(row=0, column=1, padx=(0, 10), pady=5)

        # Typ připojení
        self._conn_type = ctk.CTkLabel(
            self._left, text="",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_DIM,
        )
        self._conn_type.grid(row=4, column=0, pady=(0, 30))

        # ── Pravý panel ─────────────────────────────────────────────
        self._right = ctk.CTkFrame(self, fg_color="transparent")
        self._right.grid(row=0, column=1, sticky="nsew", padx=(0, 5), pady=5)
        self._right.grid_columnconfigure(0, weight=1)
        self._right.grid_rowconfigure(1, weight=1)

        # Placeholder (když není zařízení)
        self._placeholder = ctk.CTkFrame(self._right, fg_color=BG_CARD, corner_radius=12)
        self._placeholder.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self._placeholder.grid_rowconfigure(0, weight=1)
        self._placeholder.grid_columnconfigure(0, weight=1)

        self._placeholder_lbl = ctk.CTkLabel(
            self._placeholder,
            text=t("dash.placeholder"),
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=13),
            justify="center",
            wraplength=460,   # zabrání, aby delší text (CS vs EN) měnil šířku sloupce
        )
        self._placeholder_lbl.grid(row=0, column=0, padx=30, pady=40)

        # Quick-info karty (3 dlaždice) – skryté do připojení
        self._quick_frame = ctk.CTkFrame(self._right, fg_color="transparent")
        self._quick_frame.grid_columnconfigure((0, 1, 2), weight=1)

        # Detail sekce – scrollovatelná, skrytá do připojení
        self._detail_card = ctk.CTkScrollableFrame(
            self._right, fg_color="transparent", corner_radius=0
        )
        self._detail_card.grid_columnconfigure(0, weight=1)

    # ------------------------------------------------------------------
    def _set_badge(self, connected: bool, loading: bool = False):
        if loading:
            self._badge_frame.configure(fg_color="#1a2a3d")
            self._badge_dot.configure(text_color=self.accent)
            self._badge_text.configure(text=t("dash.loading_badge"), text_color=self.accent)
            self._conn_type.configure(text="")
        elif connected:
            self._badge_frame.configure(fg_color=GREEN_DIM)
            self._badge_dot.configure(text_color=GREEN)
            self._badge_text.configure(text=t("dash.connected"), text_color=GREEN)
        else:
            self._badge_frame.configure(fg_color=RED_DIM)
            self._badge_dot.configure(text_color=RED)
            self._badge_text.configure(text=t("dash.disconnected"), text_color=RED)
            self._conn_type.configure(text="")

    def _set_phone_color(self, color: tuple):
        if getattr(self, "_current_phone_color", None) == color:
            return
        self._current_phone_color = color
        img = _make_phone_icon(size=140, color=color)
        self._phone_icon.configure(image=img)
        self._phone_icon._image = img  # udržet referenci

    def show_no_device(self):
        self._quick_frame.grid_remove()
        self._detail_card.grid_remove()
        self._placeholder.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self._device_name.configure(text=t("dash.no_device"), text_color=TEXT_MUTED)
        self._set_phone_color((220, 50, 50))   # červená
        self._set_badge(connected=False)

    def show_loading(self):
        self._quick_frame.grid_remove()
        self._detail_card.grid_remove()
        self._placeholder.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self._device_name.configure(text=t("dash.loading"), text_color=TEXT_MUTED)
        self._set_phone_color((210, 210, 210))  # šedá
        self._set_badge(connected=False, loading=True)

    def show_device(self, info: dict):
        # Přeskočit plný rebuild pokud se info nezměnilo (device-polling každých 5 s)
        if info == self._current_info:
            return
        self._current_info = info
        self._placeholder.grid_remove()
        self._set_phone_color((0, 200, 83))     # zelená

        # Název zařízení – levý panel
        manufacturer = info.get("manufacturer", "")
        model        = info.get("model", "N/A")
        device_name  = f"{manufacturer} {model}".strip() if manufacturer else model
        self._device_name.configure(text=device_name, text_color=WHITE)
        self._set_badge(connected=True)
        self._conn_type.configure(text=t("dash.conn_type"))

        # ── Quick karty ─────────────────────────────────────────────
        for w in self._quick_frame.winfo_children():
            w.destroy()

        quick = [
            ("🤖", t("dash.android"), info.get("android", "N/A")),
            ("🔋", t("dash.battery"), info.get("battery", "N/A")),
            ("⚙️",  t("dash.sdk"),     info.get("sdk", "N/A")),
        ]

        for i, (icon, label, value) in enumerate(quick):
            card = ctk.CTkFrame(self._quick_frame, fg_color=BG_CARD, corner_radius=10)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 5, 0), pady=(0, 8))
            card.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(card, text=icon, font=ctk.CTkFont(size=20)).grid(
                row=0, column=0, pady=(12, 2)
            )
            ctk.CTkLabel(card, text=value,
                         font=ctk.CTkFont(size=14, weight="bold"),
                         text_color=WHITE).grid(row=1, column=0, padx=10)
            ctk.CTkLabel(card, text=label,
                         font=ctk.CTkFont(size=11),
                         text_color=TEXT_MUTED).grid(row=2, column=0, padx=10, pady=(2, 12))

        self._quick_frame.grid(row=0, column=0, sticky="ew")

        # ── Detail sekce se sekcemi ─────────────────────────────────
        for w in self._detail_card.winfo_children():
            w.destroy()

        sections = [
            (t("sec.identity"), [
                (t("f.serial"),       info.get("serial", "N/A")),
                (t("f.imei"),         info.get("imei", "N/A")),
                (t("f.manufacturer"), info.get("manufacturer", "N/A")),
                (t("f.brand"),        info.get("brand", "N/A")),
                (t("f.model"),        info.get("model", "N/A")),
                (t("f.device_code"),  info.get("device", "N/A")),
            ]),
            (t("sec.software"), [
                (t("f.android"),        info.get("android", "N/A")),
                (t("f.sdk"),            info.get("sdk", "N/A")),
                (t("f.build_id"),       info.get("build", "N/A")),
                (t("f.build_type"),     info.get("build_type", "N/A")),
                (t("f.build_tags"),     info.get("build_tags", "N/A")),
                (t("f.bootloader"),     info.get("bootloader", "N/A")),
                (t("f.baseband"),       info.get("baseband", "N/A")),
                (t("f.kernel"),         info.get("kernel", "N/A")),
                (t("f.security_patch"), info.get("security_patch", "N/A")),
                (t("f.fingerprint"),    info.get("build_fingerprint", "N/A")),
                (t("f.vndk"),           info.get("vndk_version", "N/A")),
                (t("f.treble"),         info.get("treble", "N/A")),
            ]),
            (t("sec.security"), [
                (t("f.selinux"),    info.get("selinux", "N/A")),
                (t("f.encryption"), info.get("encryption", "N/A")),
                (t("f.rooted"),     info.get("rooted", "N/A")),
                (t("f.dev_mode"),   info.get("dev_mode", "N/A")),
                (t("f.usb_config"), info.get("usb_config", "N/A")),
            ]),
            (t("sec.hardware"), [
                (t("f.cpu_abi"),       info.get("cpu_abi", "N/A")),
                (t("f.ram"),           info.get("ram", "N/A")),
                (t("f.resolution"),    info.get("screen_resolution", "N/A")),
                (t("f.dpi"),           info.get("screen_dpi", "N/A")),
                (t("f.storage_total"), info.get("storage_total", "N/A")),
                (t("f.storage_used"),  info.get("storage_used", "N/A")),
                (t("f.storage_free"),  info.get("storage_free", "N/A")),
            ]),
            (t("sec.locale"), [
                (t("f.locale"),   info.get("locale", "N/A")),
                (t("f.timezone"), info.get("timezone", "N/A")),
            ]),
        ]

        for sec_row, (sec_title, rows) in enumerate(sections):
            sec_card = ctk.CTkFrame(self._detail_card, fg_color=BG_CARD, corner_radius=10)
            sec_card.grid(row=sec_row, column=0, sticky="ew", pady=(0, 8))
            sec_card.grid_columnconfigure((1, 3), weight=1)

            ctk.CTkLabel(
                sec_card, text=sec_title,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=self.accent,
            ).grid(row=0, column=0, columnspan=4, padx=16, pady=(12, 4), sticky="w")

            ctk.CTkFrame(sec_card, fg_color=BG_PANEL, height=1).grid(
                row=1, column=0, columnspan=4, sticky="ew", padx=12, pady=(0, 6)
            )

            for i, (label, value) in enumerate(rows):
                r = i // 2 + 2
                c = (i % 2) * 2
                ctk.CTkLabel(
                    sec_card, text=label,
                    font=ctk.CTkFont(size=11), text_color=TEXT_MUTED, anchor="w",
                ).grid(row=r, column=c, padx=(16, 6), pady=3, sticky="w")
                ctk.CTkLabel(
                    sec_card, text=value,
                    font=ctk.CTkFont(size=11, weight="bold"),
                    text_color=WHITE, anchor="w", wraplength=230,
                ).grid(row=r, column=c + 1, padx=(0, 16), pady=3, sticky="w")

            ctk.CTkLabel(sec_card, text="").grid(
                row=len(rows) // 2 + 3, column=0, pady=2
            )

        self._detail_card.grid(row=1, column=0, sticky="nsew")

    # ------------------------------------------------------------------
    def refresh_lang(self):
        self._placeholder_lbl.configure(text=t("dash.placeholder"))
        if self._current_info:
            self.show_device(self._current_info)
        else:
            self._device_name.configure(text=t("dash.no_device"))
            self._badge_text.configure(text=t("dash.disconnected"))
