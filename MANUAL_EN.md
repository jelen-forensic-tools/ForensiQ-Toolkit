# ForenziQ – Practical User Guide

> **Version 1.0 · For authorised forensic analysis only**

---

## Contents

1. [Introduction and Legal Notice](#1-introduction-and-legal-notice)
2. [System Requirements](#2-system-requirements)
3. [Installation and Launch](#3-installation-and-launch)
4. [Preparing the Device](#4-preparing-the-device)
5. [Interface Overview](#5-interface-overview)
6. [Tab: Overview (Dashboard)](#6-tab-overview-dashboard)
7. [Tab: Extraction](#7-tab-extraction)
8. [Tab: Results](#8-tab-results)
9. [Tab: Backup Import](#9-tab-backup-import)
10. [Tab: Comparison](#10-tab-comparison)
11. [Tab: Timeline](#11-tab-timeline)
12. [Tab: Map](#12-tab-map)
13. [Tab: Lock Bypass](#13-tab-lock-bypass)
14. [Wireless Connection (Wi-Fi ADB)](#14-wireless-connection-wi-fi-adb)
15. [Recommended Workflow](#15-recommended-workflow)
16. [Common Issues and Solutions](#16-common-issues-and-solutions)

---

## 1. Introduction and Legal Notice

**ForenziQ** is a forensic tool for extracting and analysing data from Android devices via ADB (Android Debug Bridge). It supports:

- live data extraction from a connected device
- importing data from backup files created by various manufacturers
- visualising events on a timeline and a map
- comparing two consecutive extractions to detect changes
- generating reports (HTML, PDF, Excel)
- forensic screen-lock and FRP bypass

### ⚠ Legal Notice

> This software is intended **exclusively for lawful forensic analysis** of Android devices for which you have **explicit authorisation** (your own device, owner consent, court order, or equivalent legal basis). Using this software without the device owner's consent is illegal and may constitute a criminal offence. The author accepts **no responsibility** for unlawful use. By running this software you confirm your agreement with these terms.

---

## 2. System Requirements

| Requirement | Minimum |
|---|---|
| Operating System | Windows 10 / 11 (64-bit) |
| Python | 3.11 or later |
| USB port | USB-A or USB-C |
| RAM | 4 GB (8 GB recommended) |
| Disk space | 500 MB + space for extracted data |
| Internet | Only required for map tile loading (OpenStreetMap) |

**Python dependencies:**

```
customtkinter>=5.2.2
Pillow>=11.2.1
reportlab>=4.2.2
openpyxl>=3.1.5
deepdiff>=8.1.1
tkintermapview>=1.29
```

---

## 3. Installation and Launch

### 3.1 Install dependencies

```bash
pip install -r requirements.txt
```

### 3.2 ADB (Android Debug Bridge)

ADB must be available on the system PATH or inside the `adb/` folder within the project. Verify with:

```bash
adb version
```

If the command fails, download Android Platform Tools from Google and add the path to the `PATH` environment variable.

### 3.3 Launch the application

```bash
python main.py
```

---

## 4. Preparing the Device

Before connecting the device to your computer you must enable **USB Debugging**.

### 4.1 How to enable USB Debugging

1. Open **Settings → About phone** (or "About device")
2. Find **Build number** and tap it **7 times** — this unlocks Developer Options
3. Go to **Settings → Developer Options**
4. Toggle **USB Debugging** on
5. Connect the device to the computer via USB
6. A prompt will appear on the phone — tap **Allow**

> **Tip:** On some devices (Samsung, Xiaomi, etc.) Developer Options may be in a different location. Search the Settings app for "USB Debugging".

### 4.2 USB troubleshooting

- Use a data-capable USB cable (not a charge-only cable)
- Try a different USB port on the computer
- On the phone, set the USB connection mode to **File Transfer (MTP)**, not charging
- Install the device manufacturer's USB driver (e.g. Samsung Kies, Huawei HiSuite, etc.)

---

## 5. Interface Overview

The application window consists of two areas:

```
┌─────────────────────────────────────────────────────┐
│  [Overview] [Extraction] [Results] [Backup]          │  ← navigation tabs
│  [Comparison] [Timeline] [Map] [Bypass]              │
│                                                      │
│  [CS / EN]  [Wi-Fi]  ●  Device name                 │  ← language switch + ADB status
├─────────────────────────────────────────────────────┤
│                                                      │
│              Main working area                       │
│                                                      │
└─────────────────────────────────────────────────────┘
```

**Language switch (CS / EN)** — switches all UI text instantly, no restart required.

**ADB indicator** — shows the connection state:
- 🟢 Device connected (shows model name)
- 🔴 No device
- 🟡 Connecting…

---

## 6. Tab: Overview (Dashboard)

Once a device is connected, the dashboard shows:

| Field | Description |
|---|---|
| Model | Commercial device name |
| Android | OS version |
| IMEI | Unique network identifier |
| Serial number | Hardware identifier |
| CPU | Processor and architecture |
| RAM | Total memory |
| Storage | Total / free space |
| Encryption | /data partition encryption status |
| Battery | Current charge in % |

---

## 7. Tab: Extraction

### 7.1 Extraction modules

Select which data types to extract:

| Module | Extracted data |
|---|---|
| **Device info** | System properties, model, IMEI, CPU, RAM, storage |
| **Contacts** | Phone numbers and email addresses |
| **SMS/MMS** | Text and multimedia messages |
| **Call logs** | Incoming / outgoing / missed calls |
| **Apps** | List of installed applications |
| **Media** | Photos and videos (file copies) |
| **Documents** | PDF, Office documents, text files |
| **Wi-Fi networks** | Saved SSIDs and security types |
| **Accounts** | Configured email and social accounts |
| **Browser** | Browsing history with URLs and visit dates |
| **File tree** | Directory structure of /sdcard |
| **File analysis** | EXIF metadata and GPS coordinates from media |
| **File hashes** | MD5 and SHA-256 checksums |
| **Deleted data** | Deleted file recovery *(requires root)* |

**Agent modules** (extended Android features):

| Module | Description |
|---|---|
| ADB Backup | Application data packages |
| App Providers | Data via content providers |
| Usage stats | Application launch statistics |
| Termux Agent | Extended terminal-based capabilities |

### 7.2 Extraction procedure

1. Open the **Extraction** tab
2. Tick the desired modules (or click **Select all**)
3. Choose an **output folder** for the extracted data
4. Click **Start extraction**
5. Monitor progress in the real-time log window
6. When complete, switch to the **Results** tab

> **Tip:** Media extraction (photos and videos) can take longer on devices with large libraries. Always run the **File hashes** module last — it computes checksums for all previously extracted files.

---

## 8. Tab: Results

Shows a summary after extraction is complete:

- Result for each module (✔ Success / ✗ Error)
- Number of extracted records
- Option to view a detail view for each data file (table or key-value)

### 8.1 Report export

| Format | Description | Use case |
|---|---|---|
| **HTML** | Interactive web page | Browser viewing, sharing |
| **PDF** | Professional document | Print output, case files |
| **Excel (.xlsx)** | Spreadsheet | Further data analysis |

Reports are saved to the extraction output folder (`report.html`, `report.pdf`, `report.xlsx`).

### 8.2 Record detail

Double-clicking any row in a result table opens a detail window with the full record. For records containing GPS coordinates a Google Maps link is shown.

---

## 9. Tab: Backup Import

Allows importing data from backup files without a physically connected device.

### 9.1 Supported formats

| Format | Extension | Manufacturer |
|---|---|---|
| **Auto-detect** | – | Automatically identifies the format |
| Samsung SmartSwitch | `.zip` / folder | Samsung |
| Huawei HiSuite | `.hbk` | Huawei |
| MIUI Backup | `.bak` | Xiaomi |
| Google Takeout | `.zip` / folder | Google |
| Sony Xperia Backup | `.bk` | Sony |
| OnePlus / OxygenOS | `.tar.gz` / folder | OnePlus |
| ADB Backup | `.ab` | Standard Android |

### 9.2 Importable data types

Contacts · SMS · Call logs · Media files · Wi-Fi networks · Calendar events

### 9.3 Import procedure

1. Click **Pick file** (for a single backup file) or **Pick folder** (for an extracted backup)
2. The format is detected automatically and shown as a badge
3. If auto-detection fails, select the format manually in the left panel
4. Tick the desired import modules
5. Choose an output folder
6. Click **Run import**

---

## 10. Tab: Comparison

Compares the output of two extractions to show what changed between them — useful for tracking changes over time.

### 10.1 Comparable data types

Contacts · SMS · Call logs · Apps · Wi-Fi networks · Accounts · Browser history · Device info

### 10.2 Comparison results

| Category | Description |
|---|---|
| 🟢 New records | Records added after the first extraction |
| 🔴 Removed records | Records deleted since the first extraction |
| 🟡 Changed records | Records with modified field values |
| ⚪ Unchanged | Records identical in both extractions |

### 10.3 Procedure

1. Select the **Backup A** folder (older extraction)
2. Select the **Backup B** folder (newer extraction)
3. Click **Compare**
4. Review the summary and expand each category for details

---

## 11. Tab: Timeline

Visualises all events chronologically — ideal for reconstructing device activity.

### 11.1 Event sources

| Colour | Type |
|---|---|
| 🟢 Green | SMS messages |
| 🔵 Blue | Phone calls |
| 🟠 Orange | Browser history |
| 🩷 Pink | Photos |
| 🟣 Purple | Files |
| 🩵 Cyan | Apps |

### 11.2 Controls

| Action | Description |
|---|---|
| **Drag with mouse** | Pan through time |
| **Mouse wheel** | Zoom in / zoom out |
| **Full range button** | Show all events at once |
| **Click a dot** | Event detail (date, time, content) |
| **Checkboxes** | Filter by event source |

### 11.3 Loading data

1. Click the folder load button
2. Select the extraction output folder
3. Data loads in the background without blocking the UI

> **Tip:** Clusters (larger circles with a number) represent multiple events at the same point on the axis. Scroll the mouse wheel over a cluster to zoom in and see individual events.

---

## 12. Tab: Map

Displays GPS coordinates from photos on an interactive map (OpenStreetMap).

### 12.1 Prerequisites

- Photos must contain GPS data in their EXIF metadata (not all devices or camera settings record this)
- An internet connection is required to load map tiles

### 12.2 Map controls

| Action | Description |
|---|---|
| **Drag with mouse** | Pan the map |
| **Mouse wheel** | Zoom in / zoom out |
| **Click a marker** | Highlight the photo in the list |
| **Click in the list** | Centre the map on the selected photo |

### 12.3 Photo detail

Clicking a marker or a list item shows a detail panel with:
- Filename
- Date and time taken
- GPS coordinates (latitude / longitude)
- Altitude
- Camera make and model
- Resolution and file size

> **Limit:** The map shows a maximum of 300 markers; the list shows a maximum of 250 photos. If more photos with GPS data exist, a count summary is displayed.

---

## 13. Tab: Lock Bypass

> ⚠ **Use this tool only on devices you are legally authorised to analyse.**

### 13.1 Device diagnostics

Always run the **Diagnostics** scan (button in the left panel) before selecting a method. It reports:

- Android version
- Encryption type (/data partition)
- Root access availability
- TWRP recovery presence

### 13.2 Screen-lock bypass methods

#### Method 1: ADB root – delete
- **Requirements:** Root access (Magisk, SuperSU, or adbd root)
- **Android:** 4 – 10
- **Description:** Deletes lock files (`gesture.key`, `password.key`, `locksettings.db`) via root ADB shell

#### Method 2: TWRP – delete
- **Requirements:** Device must be in TWRP recovery
- **Android:** 4 – 13
- **Description:** Mounts the /data partition in recovery and deletes lock files. Suitable for encrypted devices (FDE/FBE)

### 13.3 FRP (Factory Reset Protection) bypass methods

> FRP is Google account protection activated after a factory reset. It requires signing in with the original Google account.

| Method | Requirements | Android |
|---|---|---|
| **FRP-1: Settings (ADB)** | ADB connection | 5 – 6 (unpatched) |
| **FRP-2: TWRP wipe** | Device in TWRP | 5+ |
| **FRP-3: Fastboot erase** | Unlocked bootloader | 5+ |
| **FRP-4: Root delete** | Root access | 5+ |

### 13.4 Compatibility table

| Android version | ADB root | TWRP |
|---|---|---|
| Android 4 – 6 | ✔ Full support | ✔ Full support |
| Android 7 – 8 | ✔ Full support | ⚠ FDE challenges |
| Android 9 – 10 | ✔ Full support | ⚠ FBE challenges |
| Android 11 – 13 | ⚠ Magisk only | ⚠ FBE challenges |
| Android 14+ | ⚠ Magisk only | ⚠ FBE challenges |

### 13.5 Reboot commands

The left panel provides quick reboot shortcuts:
- **Reboot to recovery** — for TWRP
- **Reboot to bootloader** — for fastboot
- **Reboot to system** — normal restart

---

## 14. Wireless Connection (Wi-Fi ADB)

The application supports connecting a device over Wi-Fi without a USB cable.

### Procedure

1. First connect the device with a USB cable and confirm USB Debugging is active
2. Click the **Wi-Fi** button in the top application bar
3. Enter the device IP address and port (default: `5555`)
4. Confirm the connection
5. The cable can be disconnected — communication continues over the network

> **Requirement:** The computer and the device must be on the same Wi-Fi network.

---

## 15. Recommended Workflow

```
1. Preparation
   └─ Enable USB Debugging on the device
   └─ Connect the USB cable

2. Device identification
   └─ Review the Overview tab
   └─ Record IMEI, serial number, OS version

3. Data extraction
   └─ Select modules
   └─ Choose an output folder
   └─ Start extraction
   └─ Wait for completion

4. Report export
   └─ Results tab → HTML / PDF / Excel

5. Analysis (optional)
   ├─ Timeline → chronological activity overview
   ├─ Map → photo location mapping
   └─ Comparison → detect changes between two extractions

6. Backup import (optional)
   └─ Backup tab
   └─ Select the backup file / folder
   └─ Import the required data

7. Lock bypass (authorised cases only)
   └─ Run Diagnostics
   └─ Select method based on results
   └─ Run and monitor the log
```

---

## 16. Common Issues and Solutions

### Device not detected

| Cause | Solution |
|---|---|
| USB Debugging not enabled | Enable it in Developer Options |
| Cable does not support data | Use a data-capable cable |
| Confirmation prompt on phone not accepted | Unlock the screen and tap "Allow debugging" |
| Missing USB driver | Install the manufacturer's driver |
| Stale ADB server | Run `adb kill-server` then `adb start-server` |

### Extraction fails

| Cause | Solution |
|---|---|
| Screen is locked | Unlock the device before extracting |
| Insufficient permissions | Confirm all ADB permission prompts on the device |
| Device disconnected mid-extraction | Check the cable and retry |
| Insufficient disk space | Check free space on the computer |

### "Deleted data" module finds nothing

Deleted data recovery requires **root access**. Without root the module cannot retrieve any files.

### Map does not display

Check your internet connection. The `tkintermapview` library fetches tiles from OpenStreetMap.

### Timeline tab is empty

Load the correct extraction output folder — it must contain the module JSON files (`sms.json`, `calls.json`, etc.).

### Some text remains in Czech after switching language

Restart the application if the language switch does not take effect immediately. In the current version the UI language switches fully when the toggle is activated.

---

*ForenziQ v1.0 · Practical User Guide (EN)*
