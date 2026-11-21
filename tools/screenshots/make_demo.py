"""Generate a FICTITIOUS demo case for the ForenziQ screenshots.

All data is made up:
  * phone numbers from ranges reserved for fiction (UK 07700 900xxx,
    US 555-01xx, AU 0491 570 xxx),
  * e-mails and websites on example.com / example.org / example.net (RFC 2606),
  * IP addresses from the documentation ranges 192.0.2.0/24 and 198.51.100.0/24 (RFC 5737),
  * IBAN DE89 3704 0044 0532 0130 00 = the official example from the standard,
  * BTC bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4 = BIP-173 test vector,
  * IMEI 490154203237518 = a commonly used sample IMEI,
  * photos = generated abstract images.
"""
import json
import os
import random
import sys

from PIL import Image, ImageDraw

random.seed(42)
ROOT = sys.argv[1]

CONTACTS = [
    ("Alice Martin",   "+44 7700 900123", "alice.martin@example.com"),
    ("Bob Fischer",    "+1 202-555-0143", "bob.fischer@example.net"),
    ("Carmen López",   "+1 312-555-0178", "carmen.lopez@example.org"),
    ("David Chen 陈大卫", "+44 7700 900456", "david.chen@example.com"),
    ("Elena Petrova",  "+61 491 570 156", "elena.petrova@example.com"),
    ("Frank Nowak",    "+1 415-555-0199", ""),
    ("Grace Kim",      "+61 491 570 110", "grace.kim@example.org"),
    ("Office Reception", "+1 202-555-0100", "reception@example.com"),
]

SMS = [
    (0, "in",  "Hi! Are we still on for the meeting tomorrow at 10?"),
    (0, "out", "Yes, see you at the café on Main Street."),
    (1, "in",  "Please send the invoice to bob.fischer@example.net"),
    (1, "out", "Sent. Payment to IBAN DE89 3704 0044 0532 0130 00 please."),
    (2, "in",  "The new server is at 192.0.2.44, login details on https://intranet.example.org/setup"),
    (3, "in",  "明天在北京见面吗？我们在酒店大堂等你。"),
    (3, "out", "OK, see you in the hotel lobby 👍"),
    (4, "in",  "Встретимся в аэропорту в пятницу вечером."),
    (4, "out", "Sounds good, I land at 18:40."),
    (5, "in",  "Transfer done – wallet bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"),
    (6, "in",  "Don't forget the meeting notes!"),
    (6, "out", "Uploading them now to https://files.example.com/notes"),
    (7, "in",  "Your parcel will be delivered between 9:00 and 12:00."),
    (0, "in",  "Running 10 minutes late, sorry."),
    (2, "out", "Thanks, the meeting room is booked for Thursday."),
]


def write(name, obj):
    with open(os.path.join(ROOT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def ts(day, h, m):
    return f"2025-05-{day:02d} {h:02d}:{m:02d}:{random.randint(0, 59):02d}"


def make_photos():
    pdir = os.path.join(ROOT, "media", "DCIM", "Camera")
    os.makedirs(pdir, exist_ok=True)
    files = []
    palettes = [((30, 60, 120), (0, 200, 255)), ((90, 20, 60), (255, 120, 80)),
                ((20, 80, 40), (160, 230, 120)), ((60, 40, 100), (220, 180, 255)),
                ((100, 70, 20), (255, 210, 90)), ((10, 50, 70), (90, 220, 200))]
    places = [(51.5079, -0.0877), (40.7580, -73.9855), (48.8584, 2.2945),
              (35.6595, 139.7005), (-33.8568, 151.2153), (52.5163, 13.3777)]
    for i, ((c1, c2), (lat, lon)) in enumerate(zip(palettes, places)):
        w, h = 800, 600
        img = Image.new("RGB", (w, h), c1)
        d = ImageDraw.Draw(img)
        for y in range(h):
            k = y / h
            d.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * k) for a, b in zip(c1, c2)))
        for _ in range(7):
            x, y, r = random.randint(0, w), random.randint(0, h), random.randint(30, 140)
            d.ellipse([x - r, y - r, x + r, y + r], outline=(255, 255, 255), width=3)
        name = f"IMG_202505{4 + i:02d}_1{i}3015.jpg"
        path = os.path.join(pdir, name)
        img.save(path, quality=85)
        files.append({
            "filename": name, "path": "/sdcard/DCIM/Camera/" + name, "type": "image",
            "size_kb": round(os.path.getsize(path) / 1024, 1),
            "modified": ts(4 + i, 10 + i, 30), "date_taken": f"2025:05:{4 + i:02d} 1{i}:30:15",
            "camera_make": "DemoCorp", "camera_model": "Demo Phone X1",
            "width": w, "height": h,
            "gps_latitude": lat, "gps_longitude": lon,
            "gps_maps_url": f"https://www.google.com/maps?q={lat},{lon}",
        })
    return files


def main():
    os.makedirs(ROOT, exist_ok=True)
    write("device_info.json", {
        "manufacturer": "DemoCorp", "brand": "DemoCorp", "model": "Demo Phone X1",
        "device": "demo_x1", "serial": "DEMO0000000001", "imei": "490154203237518",
        "android": "14", "sdk": "34", "security_patch": "2025-04-05",
        "build": "DEMO.250405.001", "build_type": "user", "build_tags": "release-keys",
        "cpu_abi": "arm64-v8a", "ram": "8 GB", "storage_total": "128 GB",
        "storage_used": "41 GB", "storage_free": "87 GB", "battery": "82 %",
        "encryption": "encrypted (FBE)", "selinux": "Enforcing", "rooted": "No",
        "kernel": "5.15.110-demo", "locale": "en-GB", "timezone": "Europe/London",
        "screen_resolution": "1080x2400", "screen_dpi": "420",
    })
    write("contacts.json", [
        {"name": n, "phone": [p], "email": [e] if e else []} for n, p, e in CONTACTS])
    sms = []
    for i, (ci, d, body) in enumerate(SMS):
        sms.append({"address": CONTACTS[ci][1], "contact": CONTACTS[ci][0],
                    "body": body, "date": ts(1 + i, 8 + i % 12, (i * 7) % 60),
                    "direction": "Received" if d == "in" else "Sent",
                    "type": "1" if d == "in" else "2"})
    write("sms.json", sms)
    calls = []
    kinds = ["Incoming", "Outgoing", "Missed"]
    for i in range(24):
        ci = random.randrange(len(CONTACTS))
        dur = 0 if i % 5 == 4 else random.randint(15, 900)
        calls.append({"number": CONTACTS[ci][1], "contact": CONTACTS[ci][0],
                      "date": ts(1 + i % 20, 7 + i % 14, (i * 13) % 60),
                      "duration": f"{dur // 60}m {dur % 60}s",
                      "type": "Missed" if dur == 0 else kinds[i % 2]})
    write("call_logs.json", calls)
    write("browser_history.json", [
        {"title": t_, "url": u, "date": ts(d, h, 5)} for t_, u, d, h in [
            ("Example Domain", "https://www.example.com/", 2, 9),
            ("Flight status – Example Air", "https://flights.example.org/status/EX123", 9, 17),
            ("Hotel booking", "https://booking.example.net/hotel/lobby-suite", 6, 21),
            ("Weather forecast", "https://weather.example.com/london", 11, 7),
            ("Online banking – login", "https://bank.example.com/login", 4, 12),
            ("Maps – Main Street café", "https://maps.example.org/?q=main+street+cafe", 1, 18),
        ]])
    write("wifi_networks.json", [
        {"ssid": "DemoCafe_Guest", "security": "WPA2-PSK", "hidden": "No"},
        {"ssid": "Home-Demo-5G", "security": "WPA3-SAE", "hidden": "No"},
        {"ssid": "Example-Airport-Free", "security": "Open", "hidden": "No"},
        {"ssid": "Office-Example", "security": "WPA2-Enterprise", "hidden": "Yes"},
    ])
    write("accounts.json", [
        {"name": "demo.user@example.com", "type": "com.google"},
        {"name": "demo_user", "type": "org.telegram.messenger"},
        {"name": "+44 7700 900999", "type": "com.whatsapp"},
    ])
    write("apps.json", [
        {"package": p, "name": n, "version": v, "system": s} for p, n, v, s in [
            ("com.android.chrome", "Chrome", "124.0.6367.82", "No"),
            ("com.whatsapp", "WhatsApp", "2.25.9.78", "No"),
            ("org.telegram.messenger", "Telegram", "11.1.3", "No"),
            ("com.google.android.apps.maps", "Maps", "11.128.0101", "No"),
            ("com.example.notes", "Example Notes", "3.2.1", "No"),
            ("com.android.settings", "Settings", "14", "Yes"),
            ("com.android.camera", "Camera", "14.0.2", "Yes"),
        ]])
    photos = make_photos()
    write("file_analysis.json", {"summary": {"total_files": len(photos), "images": len(photos),
                                             "with_gps": len(photos)}, "files": photos})
    print("demo case written to", ROOT)


if __name__ == "__main__":
    main()
