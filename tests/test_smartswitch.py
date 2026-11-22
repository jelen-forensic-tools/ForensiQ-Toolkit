"""Samsung Smart Switch decryption and import (synthetic backups, see ss_builder.py)."""
import json
import os
import re
import shutil
import threading

import pytest

import ss_builder
from core import smartswitch as ss
from core.backup_importer import IMPORT_MODULES, BackupImporter, _parse_vcf


def _import(src, out, modules=None):
    imp = BackupImporter(str(src), str(out))
    logs, res, ev = [], {}, threading.Event()
    imp.run(modules or list(IMPORT_MODULES), logs.append,
            lambda ok, msg: (res.update(ok=ok, msg=msg), ev.set()))
    assert ev.wait(120)
    return imp, logs, res


def _load(out, name):
    p = os.path.join(out, name)
    return json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else None


def test_session_key():
    assert ss.session_key_from_dummy(ss_builder.dummy_hex()) == ss_builder.KEY
    with pytest.raises(ss.SmartSwitchError):
        ss.session_key_from_dummy("AB" * 32)


def test_decrypt_blob_paddings():
    for pad in ("pkcs7", "zero"):
        dec = ss.decrypt_blob(ss_builder.KEY, ss_builder.enc(b"<a>hello</a>", pad=pad))
        assert dec.rstrip(b"\0") == b"<a>hello</a>"


def test_full_import(tmp_path):
    src = ss_builder.build(str(tmp_path))
    out = tmp_path / "out"
    imp, logs, res = _import(src, out)
    assert imp.detect_format() == "samsung"
    assert res["ok"], logs

    con = _load(out, "contacts.json")
    assert {"name": "Jan Černý", "phone": ["+420777111222"], "email": []} in con

    calls = _load(out, "call_logs.json")
    assert [(c["number"], c["type"], c["duration"]) for c in calls] == [
        ("+420777111222", "outgoing", "65 s"), ("+420602000000", "missed", "0 s")]

    sms = _load(out, "sms.json")
    assert [m["type"] for m in sms] == ["incoming", "outgoing", "incoming"]   # system row skipped
    assert sms[0]["address"] == "+420777111222" and sms[0]["body"] == "Ahoj, sraz v 5?"
    assert sms[2]["group"] and sms[2]["protocol"] == "RCS" and sms[2]["attachments"] == "image/jpeg"
    assert sms[2]["address"] == "+420777111222, +420602000000"

    wifi = {w["ssid"]: w for w in _load(out, "wifi.json")}
    assert set(wifi) == {"DomaWiFi", "Café", "Prace"}
    assert wifi["DomaWiFi"]["password"] == "tajneheslo" and wifi["Café"]["hidden"]

    assert _load(out, "apps_installed.json")[0]["package"] == "org.telegram.messenger"
    assert {b["url"] for b in _load(out, "browser_history.json")} == {"https://seznam.cz",
                                                                      "https://mapy.cz"}
    assert (out / "media_backup" / "20261101_101010.jpg").is_file()

    info = next(iter(_load(out, "import_summary.json")["smartswitch"].values()))
    assert info["ModelName"] == "SM-A137F"
    assert info["stats"]["undecoded"] == 1          # the random Samsung Notes blob
    assert os.path.isdir(info["decrypted_to"])


def test_zipped_backup(tmp_path):
    src = ss_builder.build(str(tmp_path))
    zp = shutil.make_archive(str(tmp_path / "ssbackup"), "zip", src)
    out = tmp_path / "out"
    imp, logs, _res = _import(zp, out, ["bkp_calls", "bkp_sms"])
    assert imp.detect_format() == "samsung"
    assert len(_load(out, "call_logs.json")) == 2, logs


def test_unsupported_dummy_is_reported(tmp_path):
    src = ss_builder.build(str(tmp_path))
    x = os.path.join(ss_builder.backup_root(src), "backupHistoryInfo.xml")
    text = open(x, encoding="utf-8").read()
    open(x, "w", encoding="utf-8").write(re.sub(r"<Dummy>.*?</Dummy>", "<Dummy>" + "AB" * 32 + "</Dummy>", text))
    _imp, logs, _res = _import(src, tmp_path / "out", ["bkp_calls"])
    assert any("password/PIN protected backups are not supported" in line for line in logs)


def test_vcard_quoted_printable_and_folding():
    text = ("BEGIN:VCARD\r\nVERSION:2.1\r\nFN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:=C5=BDlu=C5=A5ou=\r\n"
            "=C4=8Dk=C3=BD k=C5=AF=C5=88\r\nTEL:123\r\nEND:VCARD\r\n"
            "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Long\r\n  Name\r\nitem1.TEL:456\r\nEND:VCARD\r\n")
    assert _parse_vcf(text) == [{"name": "Žluťoučký kůň", "phone": ["123"], "email": []},
                                {"name": "Long Name", "phone": ["456"], "email": []}]
