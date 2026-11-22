# Test helper: fake ALEAPP/iLEAPP CLI writing LAVA + TSV output like v2026.4.x
import argparse, json, os, sqlite3, sys, codecs, csv
ap = argparse.ArgumentParser()
ap.add_argument("-t"); ap.add_argument("-i", nargs="+"); ap.add_argument("-o")
ap.add_argument("--custom_output_folder"); ap.add_argument("--itunes_password")
a = ap.parse_args()
base = os.path.join(a.o, a.custom_output_folder)
if os.path.exists(base):
    ap.error("exists")
os.makedirs(os.path.join(base, "_HTML", "_Script_Logs"))
open(os.path.join(base, "index.html"), "w").write("<meta>")
db = sqlite3.connect(os.path.join(base, "_lava_artifacts.db"))
db.execute("CREATE TABLE _lava_media_items(id TEXT, source_path TEXT, extraction_path TEXT, type TEXT, metadata TEXT, created_at INT, updated_at INT, is_embedded INT)")
db.execute("INSERT INTO _lava_media_items VALUES('abc','/sdcard/a.jpg','data/sdcard/a.jpg','image','',0,0,0)")
db.execute('CREATE TABLE "sms_mmssms" ("date_sent" INTEGER, "address" TEXT, "body" TEXT, "attachment" TEXT)')
db.execute("INSERT INTO sms_mmssms VALUES(1767348000, '+420777111222', 'ahoj Praha', 'abc')")
db.execute("INSERT INTO sms_mmssms VALUES(1767434400, '+420777333444', 'čau', NULL)")
db.commit(); db.close()
lava = {"lava_schema_version": 2, "parser_info": {"leapp_name": "ALEAPP", "leapp_version": "2026.4.5"},
        "artifacts": {"SMS & MMS": [{"artifact_key": "sms_mmssms", "name": "SMS messages", "tablename": "sms_mmssms",
                      "module": "sms", "column_map": {"date_sent": "Date Sent", "address": "Address", "body": "Message Body", "attachment": "Attachment"},
                      "object_columns": [{"name": "date_sent", "type": "datetime"}, {"name": "attachment", "type": "media"}]}]}}
json.dump(lava, open(os.path.join(base, "_lava_data.lava"), "w"), indent=4)
os.makedirs(os.path.join(base, "_TSV Exports"))
with codecs.open(os.path.join(base, "_TSV Exports", "Wifi Profiles.tsv"), "a", "utf-8-sig") as f:
    w = csv.writer(f, delimiter="\t"); w.writerow(["SSID", "Key"]); w.writerow(["Home", "wpa"])
    w.writerow(["SSID", "Key"]); w.writerow(["Café", "open"])
print("Processing started. Please wait. This may take a few minutes...")
print("input:", a.t, a.i, "pw:", a.itunes_password)
print("Processes completed.")
print("Report generation Completed.")
open(os.path.join(base, "_HTML", "_Script_Logs", "Screen_Output.html"), "w", encoding="utf-8").write(
    f"Processes completed. Report generation Completed. passcode {a.itunes_password}")
