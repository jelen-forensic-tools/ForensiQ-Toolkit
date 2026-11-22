"""Search index + tags: rows built by Results / Timeline must match the index."""
import json
import os

from core import case_tags as ct
from core import search_index as si


def _case(tmp_path):
    d = tmp_path / "case"
    d.mkdir()
    sms = [{"date": "2026-01-02 10:00:00", "address": "+420777111222", "body": "ahoj", "direction": "in"},
           {"date": "2026-01-03 11:00:00", "address": "+420777333444", "body": "čau", "direction": "out",
            "n": 1.5}]
    fa = {"files": [{"filename": "a.jpg", "type": "image", "date_taken": "2026:01:04 12:00:00",
                     "gps_latitude": 50.1, "gps_longitude": 14.4}], "total": 1}
    (d / "sms.json").write_text(json.dumps(sms, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "file_analysis.json").write_text(json.dumps(fa, indent=2), encoding="utf-8")
    return str(d)


def test_tag_without_index_records_source_hash(tmp_path):
    d = _case(tmp_path)
    rows = [ct.make_row("sms.json", r, s, i) for s, i, r in ct.file_records(d, "sms.json")]
    with ct.CaseTags(d) as tg:
        name = tg.list_tags()[0]["name"]
        assert tg.tag(rows[:1], name) == 1
        item = tg.tagged_items()[0]
    assert item["source_sha256"] and len(item["source_sha256"]) == 64


def test_results_and_timeline_rows_match_index(tmp_path):
    from gui.timeline import load_events
    d = _case(tmp_path)
    si.build_index(d)
    with si.SearchIndex(d) as ix:
        idx_rows = ix.search("", limit=100)
    by = {(r["file"], r["section"], r["idx"]): r for r in idx_rows}

    for s, i, rec in ct.file_records(d, "sms.json"):
        row = ct.make_row("sms.json", rec, s, i)
        ir = by[("sms.json", s, i)]
        assert (ir["row_key"], ir["dt"]) == (row["row_key"], row["dt"])

    events = load_events(d)
    assert len(events) == 3
    for e in events:
        f, s, i = e["ref"]
        row = ct.make_row(f, e["raw"], s, i)
        assert by[(f, s, i)]["row_key"] == row["row_key"]
        assert by[(f, s, i)]["dt"] == row["dt"]


def test_tags_visible_in_search_and_untag(tmp_path):
    d = _case(tmp_path)
    si.build_index(d)
    rows = [ct.make_row("sms.json", r, s, i) for s, i, r in ct.file_records(d, "sms.json")]
    with ct.CaseTags(d) as tg:
        tg.tag(rows, "Evidence")
        with si.SearchIndex(d) as ix:
            keys = [r["row_key"] for r in ix.search("ahoj")]
        assert tg.tags_for(keys) == {keys[0]: ["Evidence"]}
        assert tg.untag([rows[0]["row_key"]], "Evidence") == 1
        assert tg.count_tagged() == 1
    audit = open(os.path.join(d, "_case", "audit.jsonl"), encoding="utf-8").read().splitlines()
    assert [json.loads(x)["action"] for x in audit] == ["tagged", "untagged"]


def test_new_tags_get_distinct_colors(tmp_path):
    d = _case(tmp_path)
    with ct.CaseTags(d) as tg:
        tg.add_tag("A", tg.next_color())
        tg.add_tag("B", tg.next_color())
        colors = [t["color"] for t in tg.list_tags()]
    assert len(set(colors)) == len(colors)


def test_record_time_from_header_style_keys():
    _t, dt, _n, _e = si.analyze_record({"Message Body": "x", "Date Sent": "2026-01-02 10:00:00"})
    assert dt == "2026-01-02 10:00:00"
    _t, dt, _n, _e = si.analyze_record({"Last Visit Time": "2026-02-03 04:05:06", "Title": "t"})
    assert dt == "2026-02-03 04:05:06"
    # an exact key wins over a word match
    _t, dt, _n, _e = si.analyze_record({"Modified Time": "2026-01-01 00:00:00",
                                        "date": "2026-05-05 05:05:05"})
    assert dt == "2026-05-05 05:05:05"


def test_keyword_lists_use_isolated_home(isolated_home):
    name = ct.save_keyword_list("test list", "# comment\n*#06#\nahoj\nahoj\n")
    assert ct.load_keyword_list(name) == ["*#06#", "ahoj"]
    assert str(isolated_home) in ct.keyword_dir()


def test_tag_popup_menu_toggles(tk_root, tmp_path, monkeypatch):
    import tkinter as tk
    from gui.tagging import popup_tag_menu
    menus = []
    monkeypatch.setattr(tk.Menu, "tk_popup", lambda self, x, y, entry="": menus.append(self))
    d = str(tmp_path)
    rows = [ct.make_row("a.json", {"x": 1}), ct.make_row("a.json", {"x": 2})]
    msgs = []
    popup_tag_menu(tk_root, d, rows, 0, 0, on_change=msgs.append)
    m = menus[-1]
    assert m.entrycget(m.index("end"), "state") == "disabled"      # note needs a tag first
    m.invoke(0)
    popup_tag_menu(tk_root, d, rows, 0, 0, on_change=msgs.append)
    m = menus[-1]
    assert m._vars[0].get() is True
    m.invoke(0)
    assert msgs == ['Tagged: 2 × "Evidence"', 'Removed: 2 × "Evidence"']


def test_results_panel_tagging(tk_root, tmp_path, monkeypatch):
    import gui.results_panel as rpm
    from gui.timeline import EventDetailDialog, load_events
    d = tmp_path / "case"
    d.mkdir()
    sms = [{"date": "2026-01-02 10:00:00", "address": "+420777111222", "body": "ahoj"},
           {"date": "2026-01-03 11:00:00", "address": "+420777333444", "body": "čau"}]
    p = d / "sms.json"
    p.write_text(json.dumps(sms, ensure_ascii=False), encoding="utf-8")

    class R:
        module, label, success, error = "sms", "SMS", True, ""
        data, count, file_path = sms, 2, str(p)

    calls = []
    monkeypatch.setattr(rpm, "popup_tag_menu",
                        lambda parent, out, rows, x, y, on_change=None: calls.append((out, rows, on_change)))
    panel = rpm.ResultsPanel(tk_root)
    panel.pack()
    panel.update_results([R()])
    tk_root.update()
    panel._tree.selection_set(("0", "1"))
    panel._tag_button()
    out, rows, cb = calls[0]
    assert os.path.normcase(out) == os.path.normcase(str(d)) and len(rows) == 2
    with ct.CaseTags(out) as tg:
        tg.tag(rows[1:], "Evidence")
    cb("x")
    assert panel._row_tags == {1: ["Evidence"]}
    assert "tagged" in panel._tree.item("1", "tags")
    panel._search_var.set("čau")
    tk_root.update()
    assert panel._tree.get_children() == ("1",)

    ev = load_events(str(d))
    dlg = EventDetailDialog(tk_root, ev[1], str(d))
    tk_root.update()
    assert "Evidence" in dlg._tag_lbl.cget("text")
    dlg.destroy()
