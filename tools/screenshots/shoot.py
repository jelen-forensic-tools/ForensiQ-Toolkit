"""Vyfotí panely ForenziQ s FIKTIVNÍMI daty (bez reálného zařízení i sítě).

Použití (z kořene projektu):
    python tools/screenshots/make_demo.py tools/screenshots/cases/Demo_Phone_X1_20250521_093000
    python tools/screenshots/make_demo.py tools/screenshots/cases/Demo_Phone_X1_20250528_141500
      (druhou složku pro Porovnání záloh pak ručně trochu upravte – smažte/změňte pár záznamů)
    python tools/screenshots/shoot.py en
    python tools/screenshots/shoot.py cs
Výstup: docs/screenshots/*.png  (okno aplikace musí být na obrazovce viditelné)

* ADB je nahrazeno falešným zařízením „Demo Phone X1“ (nic se nevolá),
* iOS panel nehledá zařízení,
* nastavení reportů se čte z dočasného souboru s ukázkovými hodnotami,
* mapa (stahuje dlaždice z internetu) se NEotevírá,
* cesty ve štítcích se nahradí neutrální ukázkovou cestou.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(HERE))
CASE_A = os.path.join(HERE, "cases", "Demo_Phone_X1_20250521_093000")
CASE_B = os.path.join(HERE, "cases", "Demo_Phone_X1_20250528_141500")
OUT = os.path.join(PROJECT, "docs", "screenshots")
FAKE_ROOT = r"D:\Cases\DEMO-001"
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, PROJECT)

# ── Falešné ADB zařízení ─────────────────────────────────────────────────────
import core.adb_manager as am

DEVICE = json.load(open(os.path.join(CASE_A, "device_info.json"), encoding="utf-8"))
am.ADBManager.check_adb = lambda self: (True, "Android Debug Bridge (demo)")
am.ADBManager.get_devices = lambda self: [{"serial": DEVICE["serial"], "model": DEVICE["model"]}]


def _connect(self, serial):
    self.connected_device = serial
    return True


am.ADBManager.connect_device = _connect
am.ADBManager.get_device_info = lambda self: dict(DEVICE)

# ── Ukázkové nastavení reportů (dočasný soubor) ──────────────────────────────
import core.settings as cs_settings

_SET = os.path.join(HERE, "demo_settings.json")
json.dump({"report_examiner": "J. Doe", "report_organization": "Example Forensics Lab"},
          open(_SET, "w", encoding="utf-8"))
cs_settings._settings_path = lambda: _SET

# ── iOS panel bez hledání zařízení ───────────────────────────────────────────
import gui.ios_panel as iosp
iosp.iOSPanel._refresh_devices = lambda self: None

# ── iOS záloha: neprohledávat skutečné zálohy iTunes v tomto PC ─────────────
import gui.ios_backup_panel as iosbp
iosbp.find_itunes_backups = lambda *a, **k: []

from PIL import ImageGrab
from core.extractor import ExtractionResult
from core.i18n import t
from gui.app import ForenziQApp

app = ForenziQApp()
LANG = sys.argv[1] if len(sys.argv) > 1 else "en"
SUFFIX = "" if LANG == "en" else "_" + LANG


def load(name):
    with open(os.path.join(CASE_A, name), encoding="utf-8") as f:
        return json.load(f)


def results():
    spec = [("device_info", "device_info.json"), ("contacts", "contacts.json"),
            ("sms", "sms.json"), ("call_logs", "call_logs.json"), ("apps", "apps.json"),
            ("wifi", "wifi_networks.json"), ("accounts", "accounts.json"),
            ("browser", "browser_history.json"), ("file_analysis", "file_analysis.json")]
    out = []
    for mod, fname in spec:
        r = ExtractionResult(mod)
        data = load(fname)
        if mod == "file_analysis":
            data = data["files"]
        r.data, r.success = data, True
        r.count = len(data) if isinstance(data, list) else 1
        r.file_path = os.path.join(CASE_A, fname)
        out.append(r)
    return out


def shot(name):
    app.update_idletasks()
    app.update()
    x, y = app.winfo_rootx(), app.winfo_rooty()
    img = ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height()),
                         all_screens=True)
    img.save(os.path.join(OUT, f"{name}{SUFFIX}.png"), optimize=True)
    print("saved", name + SUFFIX)


steps = []


def step(delay):
    def deco(fn):
        steps.append((delay, fn))
        return fn
    return deco


@step(2500)
def s_dashboard():
    app._navigate("dashboard")


@step(1500)
def s_dashboard_shot():
    shot("01_dashboard")
    app._navigate("extraction")


@step(2500)
def s_extraction_shot():
    shot("02_extraction")
    app._build_panel("results")
    app.output_dir = CASE_A
    res = results()
    app._extraction_results = res
    app._results_panel.update_results(res)
    app._results_panel._show_result(res[2])          # SMS
    app._navigate("results")


@step(2000)
def s_results_shot():
    shot("03_results")
    app._set_search_dir(CASE_A)
    app._navigate("search")


@step(3000)
def s_search_query():
    p = app._search_panel
    p._dir_label.configure(text=os.path.join(FAKE_ROOT, os.path.basename(CASE_A)))
    p._query_var.set("meeting")
    p._run_search()


@step(1500)
def s_search_shot():
    shot("04_search")
    p = app._search_panel
    kids = p._ov_tree.get_children()
    if kids:
        p._ov_tree.selection_set(kids[0])            # nejčastější číslo


@step(1500)
def s_search_number_shot():
    shot("05_search_number")
    app._build_panel("timeline")
    app._timeline_panel.load_dir(CASE_A)
    app._navigate("timeline")


@step(2500)
def s_timeline_shot():
    shot("06_timeline")
    app._build_panel("comparison")
    cp = app._comparison_panel
    cp._dir_a.set(CASE_A)
    cp._dir_b.set(CASE_B)
    cp._run_comparison()
    app._navigate("comparison")


@step(3000)
def s_comparison_shot():
    cp = app._comparison_panel
    cp._dir_a.set(os.path.join(FAKE_ROOT, os.path.basename(CASE_A)))
    cp._dir_b.set(os.path.join(FAKE_ROOT, os.path.basename(CASE_B)))
    app.after(300, lambda: (shot("07_comparison"), app._navigate("backup")))


for i, (view, name) in enumerate([("backup", "08_backup_import"), ("ios", "09_ios"),
                                  ("ios_backup", "10_ios_backup"),
                                  ("ios_ramdisk", "11_ios_ramdisk"),
                                  ("adump", "12_android_dump"), ("s30", "13_nokia_s30"),
                                  ("bypass", "14_lock_bypass"), ("settings", "15_settings")]):
    def _mk(view=view, name=name):
        @step(2200)
        def _s():
            app._navigate(view)
            app.after(1800, lambda: shot(name))
    _mk()


@step(2500)
def s_end():
    app._on_close()


def run(i=0):
    if i >= len(steps):
        return
    delay, fn = steps[i]

    def go():
        try:
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
        run(i + 1)
    app.after(delay, go)


if LANG != "en":
    app.after(500, lambda: app._set_lang(LANG))
run()
app.mainloop()
