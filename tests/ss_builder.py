"""Builds a synthetic Samsung Smart Switch backup following the documented format
(encrypted <Dummy>, AES-128-CBC files with the IV in front, nested ZIP wrappers).

Used by test_smartswitch.py. Real backups have not been available for testing yet.
"""
import hashlib
import io
import json
import os
import sqlite3
import zipfile

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SESSION = "9f3c2a71be04d58e6a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef"
KEY = hashlib.sha256(SESSION.encode()).digest()[:16]
DUMMY_KEY = b"0b1e96db05d64ea4"


def _ecb(data: bytes) -> bytes:
    e = Cipher(algorithms.AES(DUMMY_KEY), modes.ECB()).encryptor()
    return e.update(data) + e.finalize()


def enc(data: bytes, pad: str = "pkcs7") -> bytes:
    iv = os.urandom(16)
    if pad == "pkcs7":
        n = 16 - len(data) % 16
        data = data + bytes([n]) * n
    else:
        data = data + b"\0" * (-len(data) % 16)
    e = Cipher(algorithms.AES(KEY), modes.CBC(iv)).encryptor()
    return iv + e.update(data) + e.finalize()


def zbytes(files: dict) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    return b.getvalue()


def dummy_hex() -> str:
    return _ecb(SESSION.encode() + b"\0" * (-len(SESSION) % 16)).hex().upper()


def _sqlite(path: str, script: str) -> bytes:
    c = sqlite3.connect(path)
    c.executescript(script)
    c.commit()
    c.close()
    data = open(path, "rb").read()
    os.remove(path)
    return data


VCF = ("BEGIN:VCARD\r\nVERSION:2.1\r\nN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:=C4=8Cern=C3=BD;Jan;;;\r\n"
       "FN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:Jan =C4=8Cern=C3=BD\r\nTEL;CELL:+420777111222\r\n"
       "END:VCARD\r\nBEGIN:VCARD\r\nVERSION:2.1\r\nFN:Alice\r\nTEL;HOME:+44 20 7946 0000\r\n"
       "EMAIL:a@example.com\r\nEND:VCARD\r\n").encode()

EXML = (b'<?xml version="1.0" encoding="UTF-8"?><CallLogs><CallLog><number>+420777111222</number>'
        b'<date>1767348000000</date><duration>65</duration><type>2</type><name>Jan</name></CallLog>'
        b'<CallLog><number>+420602000000</number><date>1767434400000</date><duration>0</duration>'
        b'<type>3</type></CallLog></CallLogs>')

BUGLE = """
CREATE TABLE messages_backup(_id INTEGER, conversation_id INTEGER, sender_participant_id INTEGER,
  message_status INTEGER, read INTEGER, received_timestamp INTEGER, sent_timestamp INTEGER, protocol INTEGER);
CREATE TABLE parts_backup(_id INTEGER, message_id INTEGER, text TEXT, content_type TEXT);
CREATE TABLE participants_backup(_id INTEGER, is_self INTEGER, send_destination TEXT,
  normalized_destination TEXT, display_destination TEXT, full_name TEXT);
CREATE TABLE conversation_participants_backup(conversation_id INTEGER, participant_id INTEGER);
INSERT INTO participants_backup VALUES(1,1,'','','',''),(2,0,'+420777111222','+420777111222','777 111 222','Jan Černý'),
  (3,0,'+420602000000','','','');
INSERT INTO conversation_participants_backup VALUES(10,1),(10,2),(20,1),(20,2),(20,3);
INSERT INTO messages_backup VALUES(100,10,2,100,1,1767348000000,1767347999000,0),
  (101,10,1,1,1,1767348060000,1767348060000,0),(102,20,3,100,0,1767348120000,0,2),
  (103,10,1,250,1,1767348180000,0,0);
INSERT INTO parts_backup VALUES(1,100,'Ahoj, sraz v 5?','text/plain'),(2,101,'Jo, platí','text/plain'),
  (3,102,'Skupina','text/plain'),(4,102,NULL,'image/jpeg'),(5,103,'system','text/plain');
"""

WIFI = (b'network={\n\tssid="DomaWiFi"\n\tpsk="tajneheslo"\n\tkey_mgmt=WPA-PSK\n}\n'
        b'network={\n\tssid=436166c3a9\n\tkey_mgmt=NONE\n\tscan_ssid=1\n}\n'
        b'New_Version_Style\n<WifiConfigStoreData><NetworkList><Network><WifiConfiguration>'
        b'<string name="SSID">&quot;Prace&quot;</string><string name="PreSharedKey">&quot;x1y2&quot;</string>'
        b'<string name="KeyMgmt">WPA_PSK</string></WifiConfiguration></Network></NetworkList>'
        b'</WifiConfigStoreData>')


def build(base: str) -> str:
    """Create <base>/SmartSwitch/backup/SM-A137F/SM-A137F_20261120; returns the SmartSwitch folder."""
    root = os.path.join(base, "SmartSwitch", "backup", "SM-A137F", "SM-A137F_20261120")
    os.makedirs(root)
    with open(os.path.join(root, "backupHistoryInfo.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?><BackupHistoryInfo><ModelName>SM-A137F</ModelName>'
                f'<OsVersion>14</OsVersion><BackupDate>20261120</BackupDate><Dummy>{dummy_hex()}</Dummy>'
                '</BackupHistoryInfo>')

    def put(rel, data):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)

    tmp = os.path.join(base, "tmp.db")
    put("CONTACT/Contact.spbm", enc(zbytes({"Contact.bk": enc(zbytes({"profile.vcf": enc(VCF)}))})))
    put("CALLLOG/CALLLOG.zip", enc(zbytes({"call_log.exml": enc(EXML, pad="zero")})))
    put("GMMESSAGE/GMMESSAGE.bk", enc(_sqlite(tmp, BUGLE)))
    put("WIFICONFIG/WIFICONFIG.zip", zbytes({"wifiConfig.txt": enc(WIFI)}))
    put("APKFILE/AppList.json", enc(json.dumps({"Apks": [
        {"ApkPkgName": "org.telegram.messenger", "ApkName": "Telegram", "VersionName": "11.2",
         "InstPkgName": "com.android.vending", "LastTimeUsed": 1767348000000}]}).encode()))
    sbrowser = _sqlite(tmp, """CREATE TABLE BOOKMARKS(_ID INTEGER, URL TEXT, TITLE TEXT, FOLDER INTEGER,
        PARENT INTEGER, POSITION INTEGER, DELETED INTEGER);
        INSERT INTO BOOKMARKS VALUES(1,'https://seznam.cz','Seznam',0,0,0,0),(2,NULL,'Folder',1,0,1,0),
        (3,'https://old.example','Old',0,0,2,1);""")
    history = _sqlite(tmp, "CREATE TABLE urls(id INTEGER, url TEXT, title TEXT, visit_count INTEGER, "
                           "last_visit_time INTEGER); INSERT INTO urls VALUES(1,'https://mapy.cz','Mapy',3,"
                           "13409821600000000);")
    put("SBROWSER/SBROWSER.zip", enc(zbytes({"databases/SBrowser.db": enc(sbrowser),
                                             "app_sbrowser/Default/History": enc(history)})))
    put("Photo/DCIM/Camera/20261101_101010.jpg", b"\xff\xd8\xff\xe0" + os.urandom(2000))
    put("SAMSUNGNOTE/sdoc.snmm", enc(os.urandom(500)))      # not parsed → stays undecoded
    return os.path.join(base, "SmartSwitch")


def backup_root(smartswitch_dir: str) -> str:
    return os.path.join(smartswitch_dir, "backup", "SM-A137F", "SM-A137F_20261120")
