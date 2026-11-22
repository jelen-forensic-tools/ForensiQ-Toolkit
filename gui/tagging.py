# -*- coding: utf-8 -*-
"""
Dialogs for tagging and keyword lists (used by the Search panel).

  • ask_text()          – small modal text input (tag name, note)
  • confirm()           – yes/no question
  • KeywordListDialog   – manage case-independent keyword lists and run one
"""
import os
from tkinter import filedialog

import customtkinter as ctk

from core import case_tags as ct
from core.i18n import t
from . import theme as th


def _modal(parent, title: str, geometry: str) -> ctk.CTkToplevel:
    d = ctk.CTkToplevel(parent)
    d.title(title)
    d.geometry(geometry)
    d.configure(fg_color=th.BG_PANEL)
    d.transient(parent.winfo_toplevel())
    d.after(50, d.grab_set)
    d.after(60, d.lift)
    return d


def ask_text(parent, title: str, prompt: str, initial: str = "") -> str | None:
    """Modal one-line input; returns the text or None when cancelled."""
    d = _modal(parent, title, "420x170")
    res = {"v": None}
    ctk.CTkLabel(d, text=prompt, font=th.f_body(), anchor="w").pack(fill="x", padx=th.PAD,
                                                                   pady=(th.PAD, 6))
    e = ctk.CTkEntry(d, height=th.BTN_H, fg_color=th.BG_INPUT, border_color=th.BORDER,
                     font=th.f_body())
    e.pack(fill="x", padx=th.PAD)
    e.insert(0, initial)
    e.focus_set()

    def ok(_=None):
        res["v"] = e.get().strip()
        d.destroy()

    row = ctk.CTkFrame(d, fg_color="transparent")
    row.pack(fill="x", padx=th.PAD, pady=th.PAD)
    th.primary_button(row, "OK", ok, width=110).pack(side="right")
    th.secondary_button(row, t("tag.cancel"), d.destroy, width=110).pack(side="right", padx=8)
    e.bind("<Return>", ok)
    d.bind("<Escape>", lambda _e: d.destroy())
    parent.wait_window(d)
    return res["v"] or None


def confirm(parent, title: str, msg: str) -> bool:
    d = _modal(parent, title, "440x170")
    res = {"v": False}
    th.hint_label(d, msg, color=th.TEXT, wraplength=400).pack(fill="x", padx=th.PAD,
                                                              pady=(th.PAD, 6))

    def yes():
        res["v"] = True
        d.destroy()

    row = ctk.CTkFrame(d, fg_color="transparent")
    row.pack(fill="x", side="bottom", padx=th.PAD, pady=th.PAD)
    th.danger_button(row, t("tag.yes"), yes, width=110).pack(side="right")
    th.secondary_button(row, t("tag.cancel"), d.destroy, width=110).pack(side="right", padx=8)
    d.bind("<Escape>", lambda _e: d.destroy())
    parent.wait_window(d)
    return res["v"]


class KeywordListDialog(ctk.CTkToplevel):
    """Edit / import / delete keyword lists and run the selected one.

    on_run(name, terms) is called when the user starts the search; the dialog closes.
    """

    def __init__(self, parent, on_run):
        super().__init__(parent)
        self.title(t("kw.title"))
        self.geometry("640x560")
        self.minsize(520, 440)
        self.configure(fg_color=th.BG_PANEL)
        self.transient(parent.winfo_toplevel())
        self.after(50, self.grab_set)
        self.after(60, self.lift)
        self._on_run = on_run
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=th.PAD, pady=(th.PAD, 6))
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(top, text=t("kw.list"), font=th.f_body()).grid(row=0, column=0, padx=(0, 8))
        self._var = ctk.StringVar()
        self._menu = ctk.CTkOptionMenu(top, variable=self._var, values=[""], height=th.BTN_H,
                                       fg_color=th.BTN_SECONDARY, button_color=th.BTN_SECONDARY,
                                       button_hover_color=th.BTN_SECONDARY_HOVER,
                                       font=th.f_body(), dynamic_resizing=False,
                                       command=self._load)
        self._menu.grid(row=0, column=1, sticky="ew")
        th.secondary_button(top, t("kw.new"), self._new, width=90).grid(row=0, column=2, padx=(8, 0))
        th.secondary_button(top, t("kw.import"), self._import, width=110).grid(row=0, column=3, padx=(8, 0))

        th.hint_label(self, t("kw.hint"), ref=self, offset=40).grid(
            row=1, column=0, sticky="ew", padx=th.PAD, pady=(0, 6))
        self._text = ctk.CTkTextbox(self, fg_color=th.BG_INPUT, border_color=th.BORDER,
                                    border_width=1, font=th.f_mono(), wrap="none")
        self._text.grid(row=2, column=0, sticky="nsew", padx=th.PAD)

        bot = ctk.CTkFrame(self, fg_color="transparent")
        bot.grid(row=3, column=0, sticky="ew", padx=th.PAD, pady=th.PAD)
        self._status = ctk.CTkLabel(bot, text="", text_color=th.TEXT_MUTED, font=th.f_small())
        self._status.pack(side="left")
        th.primary_button(bot, t("kw.run"), self._run, width=150).pack(side="right")
        th.secondary_button(bot, t("kw.save"), self._save, width=100).pack(side="right", padx=8)
        th.danger_button(bot, t("kw.delete"), self._delete, width=100).pack(side="right")
        self._refresh()

    def _refresh(self, select: str | None = None):
        names = ct.list_keyword_lists()
        self._menu.configure(values=names or [""])
        if select and select in names:
            self._var.set(select)
        elif names and self._var.get() not in names:
            self._var.set(names[0])
        elif not names:
            self._var.set("")
        self._load(self._var.get())

    def _load(self, name):
        self._text.delete("1.0", "end")
        if name:
            self._text.insert("1.0", ct.keyword_list_text(name))
        self._count()

    def _count(self):
        n = len(ct.parse_keywords(self._text.get("1.0", "end")))
        self._status.configure(text=t("kw.terms").format(n=n))

    def _new(self):
        name = ask_text(self, t("kw.new"), t("kw.name_prompt"))
        if name:
            name = ct.save_keyword_list(name, "# " + name + "\n")
            self._refresh(select=name)

    def _import(self):
        path = filedialog.askopenfilename(parent=self, title=t("kw.import"),
                                          filetypes=[(t("kw.txt_files"), "*.txt"), ("*", "*.*")])
        if not path:
            return
        try:
            text = open(path, encoding="utf-8-sig").read()
        except UnicodeDecodeError:
            text = open(path, encoding="cp1250", errors="replace").read()
        name = ct.save_keyword_list(os.path.splitext(os.path.basename(path))[0], text)
        self._refresh(select=name)

    def _save(self) -> str | None:
        name = self._var.get()
        if not name:
            name = ask_text(self, t("kw.new"), t("kw.name_prompt"))
            if not name:
                return None
        name = ct.save_keyword_list(name, self._text.get("1.0", "end"))
        self._refresh(select=name)
        self._status.configure(text=t("kw.saved"))
        return name

    def _delete(self):
        name = self._var.get()
        if name and confirm(self, t("kw.delete"), t("kw.delete_q").format(name=name)):
            ct.delete_keyword_list(name)
            self._refresh()

    def _run(self):
        terms = ct.parse_keywords(self._text.get("1.0", "end"))
        if not terms:
            self._status.configure(text=t("kw.empty"), text_color=th.RED)
            return
        name = self._save() or self._var.get()
        self.destroy()
        self._on_run(name, terms)
