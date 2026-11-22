"""Shared pytest fixtures.

Every test runs with a temporary home folder, so settings (~/.forenziq/settings.json) and
keyword lists (~/.forenziq/keyword_lists) of the real user are never touched.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    from core import i18n
    i18n.set_lang("en")
    return home


@pytest.fixture
def tk_root():
    """A CustomTkinter root window for GUI smoke tests (skipped without a display)."""
    ctk = pytest.importorskip("customtkinter")
    try:
        root = ctk.CTk()
    except Exception as e:          # no display (e.g. headless Linux)
        pytest.skip(f"no display: {e}")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:
        pass


def run_mainloop_until(root, done, timeout=60.0):
    """Run the Tk main loop until done() is true (threads use .after(), which needs it)."""
    import time
    t0 = time.time()

    def check():
        if done() or time.time() - t0 > timeout:
            root.quit()
        else:
            root.after(50, check)

    root.after(50, check)
    root.mainloop()
