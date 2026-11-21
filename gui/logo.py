"""
gui/logo.py — sidebar logo: "Forenzi" + magnifying glass (in place of the Q),
"Toolkit" and the version, rendered as one image so all lines share the exact
same left ink edge. Uses the Roboto font bundled with CustomTkinter (also
present in the .exe), falling back to Segoe UI / Arial.
"""
from __future__ import annotations

import os

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont

SS = 4          # supersampling factor (rendered large, CTkImage downsizes smoothly)


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    ctk_fonts = os.path.join(os.path.dirname(ctk.__file__), "assets", "fonts", "Roboto")
    win = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    cands = ([os.path.join(ctk_fonts, "Roboto-Medium.ttf" if bold else "Roboto-Regular.ttf")]
             + [os.path.join(win, f) for f in (("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf"))])
    for p in cands:
        if os.path.isfile(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


def render_logo(accent: str = "#00d4ff", title_px: int = 30, sub_px: int = 23, ver_px: int = 11,
                version: str = "v1.0", sub_color: str = "#ffffff", ver_color: str = "#888888") -> Image.Image:
    """Return the logo as a supersampled RGBA image (SS × logical size)."""
    f1, f2, f3 = _font(title_px * SS), _font(sub_px * SS), _font(ver_px * SS, bold=False)
    col1, col2, col3 = _rgb(accent), _rgb(sub_color), _rgb(ver_color)

    # metrics (baseline-anchored)
    cap = f1.getbbox("F", anchor="ls")              # (l, t, r, b) relative to baseline
    cap_h = -cap[1]
    word = "Forenzi"
    wb = f1.getbbox(word, anchor="ls")
    stem = max(2, round(cap_h * 0.15))              # stroke ≈ letter stem of Roboto Medium

    # magnifier geometry: ring sits on the x-height/cap area, handle dips below baseline like Q's tail
    r_out = cap_h * 0.44
    gap = cap_h * 0.10
    cx = wb[2] + gap + r_out
    cy = -cap_h * 0.54
    import math
    ang = math.radians(45)
    h_start = (cx + (r_out - stem * 0.3) * math.cos(ang), cy + (r_out - stem * 0.3) * math.sin(ang))
    h_len = cap_h * 0.36
    h_end = (h_start[0] + h_len * math.cos(ang), h_start[1] + h_len * math.sin(ang))
    line1_right = h_end[0] + stem

    sub_b = f2.getbbox("Toolkit", anchor="ls")
    ver_b = f3.getbbox(version, anchor="ls")
    # vertical layout (baselines), in supersampled px
    pad = 2 * SS
    base1 = pad + cap_h
    base2 = base1 + max(h_end[1], f1.getbbox("g", anchor="ls")[3] * 0.4) + (-sub_b[1]) + 6 * SS
    base3 = base2                                   # version sits next to "Toolkit", same baseline
    # left ink alignment: shift each line so its first glyph's ink starts at x = pad
    x1, x2 = pad - wb[0], pad - sub_b[0]
    x3 = x2 + sub_b[2] + 7 * SS - ver_b[0]

    w = int(max(x1 + line1_right, x3 + ver_b[2]) + pad)
    h = int(base2 + max(sub_b[3], ver_b[3]) + pad + 10 * SS)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((x1, base1), word, font=f1, fill=col1, anchor="ls")
    # ring
    ox, oy = x1 + cx, base1 + cy
    d.ellipse((ox - r_out, oy - r_out, ox + r_out, oy + r_out), outline=col1, width=stem)
    # handle (slightly thicker, rounded ends)
    hw = round(stem * 1.25)
    p0 = (x1 + h_start[0], base1 + h_start[1])
    p1 = (x1 + h_end[0], base1 + h_end[1])
    d.line((p0, p1), fill=col1, width=hw)
    for px, py in (p1,):
        d.ellipse((px - hw / 2, py - hw / 2, px + hw / 2, py + hw / 2), fill=col1)
    # small glass highlight
    hr = r_out - stem * 1.6
    d.arc((ox - hr, oy - hr, ox + hr, oy + hr), start=200, end=255, fill=col1, width=max(1, stem // 2))

    d.text((x2, base2), "Toolkit", font=f2, fill=col2, anchor="ls")
    d.text((x3, base3), version, font=f3, fill=col3, anchor="ls")
    return img


def logo_ctk_image(**kw) -> ctk.CTkImage:
    img = render_logo(**kw)
    return ctk.CTkImage(light_image=img, dark_image=img, size=(img.width // SS, img.height // SS))
