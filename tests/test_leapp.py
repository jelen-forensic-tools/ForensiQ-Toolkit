"""ALEAPP / iLEAPP integration, tested with a fake CLI (tests/fixtures/fake_leapp.py)."""
import json
import os
import threading
import time

import pytest

from conftest import FIXTURES, run_mainloop_until
from core import leapp
from core import search_index as si

FAKE = os.path.join(FIXTURES, "fake_leapp.py")
SLOW = os.path.join(FIXTURES, "slow_tool.py")

pytestmark = pytest.mark.skipif(not leapp._find_python(), reason="no Python interpreter for .py tools")


def _run(r: leapp.LeappRun, timeout=120):
    logs, res, ev = [], {}, threading.Event()
    r.run(logs.append, lambda ok, msg, summ: (res.update(ok=ok, msg=msg, s=summ), ev.set()))
    assert ev.wait(timeout), "tool run did not finish"
    return res, logs


def test_input_detection(tmp_path):
    inp = tmp_path / "in"
    inp.mkdir()
    assert leapp.detect_input_type(str(inp), "aleapp") == "fs"
    assert leapp.detect_input_type("a.tar.gz", "aleapp") == "gz"
    assert leapp.detect_input_type("a.zip", "aleapp") == "zip"
    bk = inp / "bk"
    bk.mkdir()
    (bk / "Manifest.db").write_bytes(b"")
    assert leapp.detect_input_type(str(bk), "ileapp") == "itunes"
    assert leapp.guess_tool(str(bk)) == "ileapp"


def test_run_import_and_index(tmp_path):
    case, inp = tmp_path / "case", tmp_path / "in"
    case.mkdir()
    inp.mkdir()
    (inp / "x.txt").write_text("x")
    res, _logs = _run(leapp.LeappRun("ileapp", FAKE, str(inp), "fs", str(case)))
    assert res["ok"], res
    assert res["s"]["source"] == "lava"
    doc = json.loads((case / "ileapp.json").read_text(encoding="utf-8"))
    sms = doc["SMS & MMS / SMS messages"]
    assert sms[0]["Date Sent"] == "2026-01-02 10:00:00"          # UTC unix seconds → text
    assert sms[0]["Attachment"] == "/sdcard/a.jpg"                # media id → source path
    assert leapp.read_tsv(res["s"]["report"]) == {
        "Wifi Profiles": [{"SSID": "Home", "Key": "wpa"}, {"SSID": "Café", "Key": "open"}]}
    si.build_index(str(case))
    with si.SearchIndex(str(case)) as ix:
        hits = ix.search("Praha")
    assert hits and hits[0]["file"] == "ileapp.json" and hits[0]["dt"].startswith("2026-01-02")


def test_password_is_masked(tmp_path):
    case, bk = tmp_path / "case", tmp_path / "bk"
    case.mkdir()
    bk.mkdir()
    (bk / "Manifest.db").write_bytes(b"")
    res, logs = _run(leapp.LeappRun("ileapp", FAKE, str(bk), "itunes", str(case),
                                    itunes_password="Tajne123"))
    assert res["ok"]
    assert not any("Tajne123" in line for line in logs)
    assert "********" in logs[0]
    so = os.path.join(res["s"]["report"], "_HTML", "_Script_Logs", "Screen_Output.html")
    assert "Tajne123" not in open(so, encoding="utf-8").read()


def test_import_existing_report_from_parent_folder(tmp_path):
    case, inp = tmp_path / "case", tmp_path / "in"
    case.mkdir()
    inp.mkdir()
    _run(leapp.LeappRun("aleapp", FAKE, str(inp), "fs", str(case)))
    summ = leapp.import_report("aleapp", str(case / leapp.OUT_SUBDIR), str(case))
    assert summ["records"] == 2 and (case / "aleapp.json").is_file()


def test_stop_kills_the_tool(tmp_path):
    case = tmp_path / "case"
    case.mkdir()
    r = leapp.LeappRun("aleapp", SLOW, str(case), "fs", str(case))
    logs, res, ev = [], {}, threading.Event()
    r.run(logs.append, lambda ok, msg, summ: (res.update(ok=ok, msg=msg), ev.set()))
    time.sleep(2)
    r.stop()
    assert ev.wait(20), "stop did not end the run"
    assert res == {"ok": False, "msg": "stopped"}


def test_exe_architecture_check(tmp_path):
    def pe(machine: int) -> bytes:
        head = bytearray(512)
        head[:2] = b"MZ"
        head[0x3C:0x40] = (0x80).to_bytes(4, "little")
        head[0x80:0x84] = b"PE\0\0"
        head[0x84:0x86] = machine.to_bytes(2, "little")
        return bytes(head)

    arm = tmp_path / "arm.exe"
    arm.write_bytes(pe(0xAA64))
    x64 = tmp_path / "x64.exe"
    x64.write_bytes(pe(0x8664))
    assert leapp.exe_arch(str(arm)) == "arm64"
    assert leapp.exe_arch(str(x64)) == "x64"
    if os.name == "nt" and leapp.host_arch() == "x64":
        assert leapp.arch_mismatch(str(arm)) == ("arm64", "x64")
        assert leapp.arch_mismatch(str(x64)) == ("", "")


def test_panel_run(tk_root, tmp_path):
    from gui.leapp_panel import LeappPanel
    case = tmp_path / "case"
    case.mkdir()
    (case / "device_info.json").write_text(json.dumps({"model": "X"}))
    inp = case / "user_data_tree"
    inp.mkdir()
    (inp / "a").write_text("a")
    imported = []
    p = LeappPanel(tk_root, on_imported=imported.append, on_open_search=lambda: None)
    p.pack(fill="both", expand=True)
    p.set_case(str(case))
    tk_root.update()
    assert p._suggest_frame.winfo_children()
    p.set_input(str(inp))
    assert (p._tool.get(), p._type.get()) == ("aleapp", "fs")
    p._path_entry.delete(0, "end")
    p._path_entry.insert(0, FAKE)
    p._start()
    run_mainloop_until(tk_root, lambda: str(p._btn_run.cget("state")) == "normal")
    assert imported == [str(case)]
    assert (case / "aleapp.json").is_file()
    assert str(p._btn_html.cget("state")) == "normal"
    from core import settings
    assert settings.load()["aleapp_path"] == FAKE
