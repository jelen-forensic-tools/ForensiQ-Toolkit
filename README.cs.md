# ForenziQ Toolkit

Forenzní nástroj pro analýzu a extrakci dat z Android zařízení přes ADB rozhraní.

---

## Co software dělá

ForenziQ je desktopová aplikace určená pro forenzní analýzu Android zařízení. Umožňuje:

- **Přehled zařízení** – zobrazení detailních informací o připojeném zařízení (model, výrobce, Android verze, IMEI, sériové číslo, CPU, RAM, úložiště, šifrování, SELinux, root status a další)
- **Extrakce dat** – modulární extrakce vybraných dat ze zařízení:
  - Kontakty, SMS/MMS zprávy, historie hovorů
  - Nainstalované aplikace
  - Fotografie a videa
  - Dokumenty (PDF, Office, TXT)
  - Uložené Wi-Fi sítě a účty
  - Historie prohlížeče
  - Celá struktura uživatelských dat (/sdcard)
  - EXIF metadata a GPS souřadnice fotografií
  - MD5 a SHA-256 hashe souborů
  - Obnova smazaných dat (vyžaduje root)
- **Mapa GPS lokací** – vizualizace míst pořízení fotografií na interaktivní mapě (OpenStreetMap) včetně data pořízení a detailů fotoaparátu
- **Časová osa** – chronologická vizualizace extrahovaných událostí (SMS, hovory, fotografie, soubory)
- **Porovnání záloh** – porovnání dvou extrakčních složek pro detekci změn
- **Výsledky a reporty** – export do HTML, PDF a Excel formátů
- **Obcházení zámku obrazovky** – forenzní nástroje pro obcházení PIN/gesta (ADB root metoda, TWRP recovery metoda) s diagnostikou zařízení a reboot příkazy
- **Wi-Fi ADB připojení** – bezdrátové připojení k zařízení přes TCP/IP

---

## Na čem je postaven

| Komponenta | Verze | Účel |
|---|---|---|
| Python | 3.11+ | Hlavní jazyk aplikace |
| CustomTkinter | 5.2.2 | Moderní GUI framework (tmavý motiv) |
| Pillow | 11.2.1 | Zpracování obrázků, EXIF metadata |
| ReportLab | 4.2.2 | Generování PDF reportů |
| openpyxl | 3.1.5 | Generování Excel reportů |
| deepdiff | 8.1.1 | Porovnávání datových sad |
| tkintermapview | 1.29 | Interaktivní mapa (OpenStreetMap) |
| ADB | – | Android Debug Bridge (komunikace se zařízením) |

---

## Požadavky

- Windows 10/11 (64-bit)
- Python 3.11 nebo novější
- ADB nainstalované v systému nebo přiložené v složce `adb/`
- Na zařízení povolené **USB ladění** (USB Debugging)

### Instalace závislostí

```bash
pip install -r requirements.txt
```

### Spuštění

```bash
python main.py
```

nebo pomocí přiloženého skriptu:

```
spustit.bat
```

---

## Podporovaná zařízení

Jakékoli Android zařízení s povoleným USB laděním. Některé funkce (obnova smazaných dat, obcházení zámku) vyžadují root přístup nebo TWRP recovery.

---

## Právní upozornění a odmítnutí odpovědnosti

> **DŮLEŽITÉ – PŘEČTĚTE PŘED POUŽITÍM**

Tento software je určen **výhradně pro legální forenzní analýzu** Android zařízení, ke které máte oprávnění – například:
- analýzu vlastního zařízení,
- analýzu zařízení v rámci autorizovaného bezpečnostního testování,
- vzdělávací a výzkumné účely v kontrolovaném prostředí.

**Použití tohoto softwaru bez výslovného souhlasu vlastníka zařízení je nezákonné** a může být v rozporu s trestním zákoníkem vaší země.

Autor tohoto softwaru **nenese absolutně žádnou odpovědnost** za jakékoli přímé, nepřímé, náhodné nebo následné škody vzniklé používáním tohoto nástroje, ani za žádné protiprávní jednání uživatele. Veškerá odpovědnost za způsob použití leží výhradně na uživateli.

Stažením, instalací nebo spuštěním tohoto softwaru potvrzujete, že jste si přečetli toto upozornění a souhlasíte s těmito podmínkami.

---

## Licence

Software je poskytován „tak jak je" (AS IS), bez jakékoli záruky funkčnosti nebo vhodnosti pro konkrétní účel.
