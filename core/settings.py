"""
core/settings.py — persistent user settings of the application (JSON in ~/.forenziq).

Used mainly for report branding: company/investigator logo, default examiner
and organisation names, switches for what the report shows. Read both by the
Settings panel in the GUI and by the report generator (core/report.py).
"""
from __future__ import annotations

import json
import os
import threading

_LOCK = threading.Lock()

_DEFAULTS: dict = {
    # Report branding
    "report_examiner":           "",      # default examiner name
    "report_organization":       "",      # default organisation
    "report_company_logo":       "",      # path to the company/investigator logo (PNG/JPG)
    "report_show_company_logo":  True,    # show the company logo on the cover page
    "report_show_case_details":  True,    # show the "Case details" block
    "report_show_tool_logo":     True,    # show the ForenziQ logo on the cover page
    # External analysis tools (not bundled)
    "aleapp_path":               "",      # aleapp.exe or aleapp.py
    "ileapp_path":               "",      # ileapp.exe or ileapp.py
}


def _settings_path() -> str:
    base = os.path.join(os.path.expanduser("~"), ".forenziq")
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        pass
    return os.path.join(base, "settings.json")


def load() -> dict:
    """Return all settings (defaults completed with the saved values)."""
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
    """A single setting value."""
    return load().get(key, _DEFAULTS.get(key, default))


def save(values: dict) -> None:
    """Save the given values (merged with the existing ones)."""
    with _LOCK:
        data = load()
        data.update({k: v for k, v in values.items() if k in _DEFAULTS})
        try:
            with open(_settings_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
