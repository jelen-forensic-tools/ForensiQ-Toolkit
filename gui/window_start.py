"""
gui/window_start.py – helpers for starting the main window maximized on Windows.

  • monitors()        – all monitors: rectangle, work area, effective DPI scaling, primary
  • window_monitor()  – index of the monitor the window is on
  • win32_maximize()  – maximize through the Win32 API (fallback when Tk's "zoomed" does
                        not stick, e.g. after CTk re-scales the window on another monitor)
  • StartLog          – short diagnostic log of the first seconds after start-up,
                        written to ~/.forenziq/window_start.log (overwritten every start),
                        so a start-up problem on a user's PC can be diagnosed from the file
"""
from __future__ import annotations

import datetime
import os
import sys


def _hwnd(win) -> int:
    import ctypes
    inner = win.winfo_id()
    return ctypes.windll.user32.GetParent(inner) or inner


def monitors() -> list[dict]:
    """[{rect, work, scaling, primary}] in physical pixels (empty outside Windows)."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    out: list[dict] = []
    user32 = ctypes.windll.user32
    proc_t = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                                ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def cb(hmon, _hdc, _rect, _lp):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            dx, dy = wintypes.UINT(), wintypes.UINT()
            try:
                ctypes.windll.shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy))
                scaling = round((dx.value + dy.value) / 192, 3)
            except Exception:
                scaling = None
            r, w = info.rcMonitor, info.rcWork
            out.append({"handle": hmon,
                        "rect": (r.left, r.top, r.right, r.bottom),
                        "work": (w.left, w.top, w.right, w.bottom),
                        "scaling": scaling, "primary": bool(info.dwFlags & 1)})
        return 1

    try:
        user32.EnumDisplayMonitors(None, None, proc_t(cb), 0)
    except Exception:
        return []
    return out


def window_monitor(win, mons: list[dict] | None = None) -> int:
    """Index into monitors() of the monitor the window is on (-1 if unknown)."""
    if sys.platform != "win32":
        return -1
    try:
        import ctypes
        hmon = ctypes.windll.user32.MonitorFromWindow(_hwnd(win), 2)
        for i, m in enumerate(mons if mons is not None else monitors()):
            if m["handle"] == hmon:
                return i
    except Exception:
        pass
    return -1


def window_rect(win) -> tuple | None:
    """Outer window rectangle in physical pixels (Win32), None outside Windows."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        if ctypes.windll.user32.GetWindowRect(_hwnd(win), ctypes.byref(r)):
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return None


def win32_maximize(win) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.user32.ShowWindow(_hwnd(win), 3))      # SW_MAXIMIZE
    except Exception:
        return False


class StartLog:
    """Diagnostic log of the window start-up (~/.forenziq/window_start.log)."""

    def __init__(self):
        self._f = None
        try:
            d = os.path.join(os.path.expanduser("~"), ".forenziq")
            os.makedirs(d, exist_ok=True)
            self.path = os.path.join(d, "window_start.log")
            self._f = open(self.path, "w", encoding="utf-8")
        except OSError:
            self.path = ""
        self._t0 = datetime.datetime.now()

    def write(self, msg: str) -> None:
        if not self._f:
            return
        dt = (datetime.datetime.now() - self._t0).total_seconds()
        try:
            self._f.write(f"[{dt:6.2f}s] {msg}\n")
            self._f.flush()
        except (OSError, ValueError):
            pass

    def close(self) -> None:
        if self._f:
            try:
                self._f.close()
            except OSError:
                pass
            self._f = None
