"""
gui/settings_panel.py — panel Nastavení.

Zatím řeší branding reportů: výchozí examinátor/organizace, logo firmy nebo
vyšetřovatele a přepínače, co se v reportu zobrazí. Hodnoty se ukládají přes
core.settings (JSON v ~/.forenziq) a čte je generátor reportů.
"""
from __future__ import annotations

import os
from tkinter import filedialog

import customtkinter as ctk

from core.i18n import t
from core import settings as app_settings
from . import theme as th

try:
    from PIL import Image
    _PIL = True
except Exception:
    _PIL = False


class SettingsPanel(ctk.CTkFrame):
    def __init__(self, master, accent: str = th.ACCENT, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.accent = accent
        self._logo_path = ""
        self._logo_preview_img = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build()
        self._load()

    # ── UI ─────────────────────────────────────────────────────────────────────
    def _build(self):
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        card = th.card(scroll)
        card.grid(row=0, column=0, sticky="ew", pady=(0, th.GAP))
        card.grid_columnconfigure(1, weight=1)

        th.section_label(card, t("set.report_branding")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=th.PAD, pady=(th.PAD, 2))
        th.hint_label(card, t("set.report_desc"), ref=card, offset=2 * th.PAD).grid(
            row=1, column=0, columnspan=3, sticky="ew", padx=th.PAD, pady=(0, 10))

        # Examinátor
        th.field_label(card, t("set.examiner")).grid(
            row=2, column=0, sticky="w", padx=(th.PAD, 10), pady=6)
        self._examiner = ctk.CTkEntry(card, height=th.BTN_H, font=th.f_body())
        self._examiner.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, th.PAD), pady=6)

        # Organizace
        th.field_label(card, t("set.organization")).grid(
            row=3, column=0, sticky="w", padx=(th.PAD, 10), pady=6)
        self._organization = ctk.CTkEntry(card, height=th.BTN_H, font=th.f_body())
        self._organization.grid(row=3, column=1, columnspan=2, sticky="ew", padx=(0, th.PAD), pady=6)

        th.separator(card).grid(row=4, column=0, columnspan=3, sticky="ew",
                                padx=th.PAD, pady=(10, 10))

        # Logo firmy / vyšetřovatele
        th.field_label(card, t("set.company_logo")).grid(
            row=5, column=0, sticky="nw", padx=(th.PAD, 10), pady=6)
        logo_box = ctk.CTkFrame(card, fg_color="transparent")
        logo_box.grid(row=5, column=1, columnspan=2, sticky="ew", padx=(0, th.PAD), pady=6)
        logo_box.grid_columnconfigure(0, weight=1)

        self._preview = ctk.CTkLabel(logo_box, text="", height=70,
                                     fg_color=th.BG_LOG, corner_radius=th.RADIUS_BTN,
                                     text_color=th.TEXT_MUTED, font=th.f_small())
        self._preview.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6), ipady=6)

        self._logo_label = ctk.CTkLabel(logo_box, text=t("set.logo_none"),
                                        font=th.f_small(), text_color=th.TEXT_MUTED, anchor="w")
        self._logo_label.grid(row=1, column=0, sticky="ew")
        btns = ctk.CTkFrame(logo_box, fg_color="transparent")
        btns.grid(row=1, column=1, sticky="e")
        th.pack_row(
            th.secondary_button(btns, t("set.browse"), self._pick_logo, width=140),
            th.secondary_button(btns, t("set.clear"), self._clear_logo, width=90),
        )
        th.hint_label(logo_box, t("set.logo_hint"), ref=logo_box, offset=4).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        th.separator(card).grid(row=6, column=0, columnspan=3, sticky="ew",
                                padx=th.PAD, pady=(10, 10))

        # Přepínače
        self._v_company = ctk.BooleanVar(value=True)
        self._v_tool = ctk.BooleanVar(value=True)
        self._v_case = ctk.BooleanVar(value=True)
        th.checkbox(card, self._v_company, text=t("set.show_company_logo")).grid(
            row=7, column=0, columnspan=3, sticky="w", padx=th.PAD, pady=4)
        th.checkbox(card, self._v_tool, text=t("set.show_tool_logo")).grid(
            row=8, column=0, columnspan=3, sticky="w", padx=th.PAD, pady=4)
        th.checkbox(card, self._v_case, text=t("set.show_case_details")).grid(
            row=9, column=0, columnspan=3, sticky="w", padx=th.PAD, pady=(4, th.PAD))

        # Uložit
        bar = ctk.CTkFrame(scroll, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew")
        self._status = ctk.CTkLabel(bar, text="", font=th.f_body(), text_color=th.GREEN)
        self._status.pack(side="left")
        th.primary_button(bar, t("set.save"), self._save, width=180).pack(side="right")

    # ── Logo ───────────────────────────────────────────────────────────────────
    def _pick_logo(self):
        path = filedialog.askopenfilename(
            title=t("set.browse"),
            filetypes=[("Image", "*.png *.jpg *.jpeg *.bmp *.gif"), ("All", "*.*")],
        )
        if path:
            self._logo_path = path
            self._render_logo()

    def _clear_logo(self):
        self._logo_path = ""
        self._render_logo()

    def _render_logo(self):
        name = os.path.basename(self._logo_path) if self._logo_path else t("set.logo_none")
        self._logo_label.configure(text=name)
        self._logo_preview_img = None
        if self._logo_path and _PIL and os.path.isfile(self._logo_path):
            try:
                im = Image.open(self._logo_path)
                im.thumbnail((260, 60))
                self._logo_preview_img = ctk.CTkImage(light_image=im, dark_image=im,
                                                      size=im.size)
                self._preview.configure(image=self._logo_preview_img, text="")
                return
            except Exception:
                pass
        self._preview.configure(image=None, text="—")

    # ── Načtení / uložení ───────────────────────────────────────────────────────
    def _load(self):
        cfg = app_settings.load()
        self._examiner.delete(0, "end")
        self._examiner.insert(0, cfg.get("report_examiner", ""))
        self._organization.delete(0, "end")
        self._organization.insert(0, cfg.get("report_organization", ""))
        self._logo_path = cfg.get("report_company_logo", "") or ""
        self._v_company.set(bool(cfg.get("report_show_company_logo", True)))
        self._v_tool.set(bool(cfg.get("report_show_tool_logo", True)))
        self._v_case.set(bool(cfg.get("report_show_case_details", True)))
        self._render_logo()

    def _save(self):
        app_settings.save({
            "report_examiner": self._examiner.get().strip(),
            "report_organization": self._organization.get().strip(),
            "report_company_logo": self._logo_path,
            "report_show_company_logo": self._v_company.get(),
            "report_show_tool_logo": self._v_tool.get(),
            "report_show_case_details": self._v_case.get(),
        })
        self._status.configure(text="✓ " + t("set.saved"))
        self.after(2500, lambda: self._status.configure(text=""))

    def refresh_lang(self):
        # Statické popisky řeší globální retranslate; dynamický jen stav loga.
        self._render_logo()
