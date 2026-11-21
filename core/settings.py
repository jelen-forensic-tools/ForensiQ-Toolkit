"""
core/settings.py — trvalé uživatelské nastavení aplikace (JSON v ~/.forenziq).

Používá se hlavně pro branding reportů: logo firmy/vyšetřovatele, výchozí jméno
examinátora a organizace, přepínače co se v reportu zobrazí. Čte se jak z GUI
panelu Nastavení, tak z generátoru reportů (core/report.py).
"""
from __future__ import annotations

import json
import os
import threading

_LOCK = threading.Lock()

_DEFAULTS: dict = {
    # Branding reportů
    "report_examiner":           "",      # výchozí jméno examinátora
    "report_organization":       "",      # výchozí organizace
    "report_company_logo":       "",      # cesta k logu firmy/vyšetřovatele (PNG/JPG)
    "report_show_company_logo":  True,    # zobrazit firemní logo na titulní straně
    "report_show_case_details":  True,    # zobrazit blok „Údaje o případu"
    "report_show_tool_logo":     True,    # zobrazit logo ForenziQ na titulní straně
}


def _settings_path() -> str:
    base = os.path.join(os.path.expanduser("~"), ".forenziq")
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        pass
    return os.path.join(base, "settings.json")


def load() -> dict:
    """Vrátí všechna nastavení (výchozí hodnoty doplněné o uložené)."""
    data = dict(_DEFAULTS)
    path = _settings_path()
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                data.update({k: v for k, v in saved.items() if k in _DEFAULTS})
    except Exception:
        pass
    return data


def get(key: str, default=None):
    """Jedna hodnota nastavení."""
    return load().get(key, _DEFAULTS.get(key, default))


def save(values: dict) -> None:
    """Uloží předané hodnoty (sloučí s existujícími)."""
    with _LOCK:
        data = load()
        data.update({k: v for k, v in values.items() if k in _DEFAULTS})
        try:
            with open(_settings_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
