"""
gui/iconset.py — shared loading of the brand module icons (assets/icons/<key>.png).
Works from source and from the .exe bundle. Returns a CTkImage, or None if the icon is missing.
"""
from __future__ import annotations

import os
import sys
import customtkinter as ctk
from PIL import Image

_cache: dict = {}


def _icons_dir() -> str:
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(base, "assets", "icons")


def module_icon(key: str, size: int = 34):
    """CTkImage for the given module key, or None (the panel then uses an emoji fallback)."""
    ck = (key, size)
    if ck in _cache:
        return _cache[ck]
    img = None
    p = os.path.join(_icons_dir(), f"{key}.png")
    if os.path.isfile(p):
        try:
            im = Image.open(p)
            img = ctk.CTkImage(light_image=im, dark_image=im, size=(size, size))
        except Exception:
            img = None
    _cache[ck] = img
    return img
