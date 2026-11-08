# ForenziQ – Praktická příručka uživatele

> **Verze 1.0 · Určeno výhradně pro oprávněnou forenzní analýzu**

---

## Obsah

1. [Úvod a právní upozornění](#1-úvod-a-právní-upozornění)
2. [Systémové požadavky](#2-systémové-požadavky)
3. [Instalace a spuštění](#3-instalace-a-spuštění)
4. [Příprava zařízení](#4-příprava-zařízení)
5. [Přehled rozhraní](#5-přehled-rozhraní)
6. [Záložka: Přehled (Dashboard)](#6-záložka-přehled-dashboard)
7. [Záložka: Extrakce](#7-záložka-extrakce)
8. [Záložka: Výsledky](#8-záložka-výsledky)
9. [Záložka: Záloha (Backup import)](#9-záložka-záloha-backup-import)
10. [Záložka: Porovnání](#10-záložka-porovnání)
11. [Záložka: Časová osa](#11-záložka-časová-osa)
12. [Záložka: Mapa](#12-záložka-mapa)
13. [Záložka: Lock bypass](#13-záložka-lock-bypass)
14. [Bezdrátové připojení (Wi-Fi ADB)](#14-bezdrátové-připojení-wi-fi-adb)
15. [Doporučený pracovní postup](#15-doporučený-pracovní-postup)
16. [Časté problémy a řešení](#16-časté-problémy-a-řešení)

---

## 1. Úvod a právní upozornění

**ForenziQ** je forenzní nástroj pro extrakci a analýzu dat z Android zařízení prostřednictvím rozhraní ADB (Android Debug Bridge). Umožňuje:

- extrakci dat přímo z připojeného zařízení
- import dat ze záložních souborů různých výrobců
- vizualizaci událostí na časové ose a na mapě
- porovnání dvou po sobě jdoucích extrakcí
- generování reportů (HTML, PDF, Excel)
- obcházení zámku obrazovky v rámci forenzní analýzy

### ⚠ Právní upozornění

> Tento software je určen **výhradně pro zákonnou forenzní analýzu** zařízení, ke které máte **výslovné oprávnění** (vlastní zařízení, souhlas majitele, soudní příkaz nebo jiný zákonný podklad). Použití bez souhlasu majitele zařízení je nelegální a může zakládat trestní odpovědnost. Autor nenese žádnou odpovědnost za nezákonné použití.

---

## 2. Systémové požadavky

| Požadavek | Minimálně |
|---|---|
| Operační systém | Windows 10 / 11 (64-bit) |
| Python | 3.11 nebo novější |
| USB port | USB-A nebo USB-C |
| RAM | 4 GB (doporučeno 8 GB) |
| Místo na disku | 500 MB + prostor pro extrahovaná data |
| Internet | Pouze pro zobrazení mapy (OpenStreetMap) |

**Závislosti (Python balíčky):**

```
customtkinter>=5.2.2
Pillow>=11.2.1
reportlab>=4.2.2
openpyxl>=3.1.5
deepdiff>=8.1.1
tkintermapview>=1.29
```

---

## 3. Instalace a spuštění

### 3.1 Instalace závislostí

```bash
pip install -r requirements.txt
```

### 3.2 ADB (Android Debug Bridge)

ADB musí být dostupné v systémové cestě nebo ve složce `adb/` uvnitř projektu. Ověření:

```bash
adb version
```

Pokud příkaz selže, stáhněte Android Platform Tools z webu Googlu a přidejte cestu do systémové proměnné `PATH`.

### 3.3 Spuštění aplikace

```bash
python main.py
```

---

## 4. Příprava zařízení

Před připojením zařízení k počítači je nutné povolit **Ladění přes USB (USB Debugging)**.

### 4.1 Jak povolit USB Debugging

1. Otevřete **Nastavení → O telefonu** (nebo „O zařízení")
2. Najděte položku **Číslo sestavení** (Build number) a klepněte na ni **7×** — tím odemknete Možnosti pro vývojáře
3. Přejděte do **Nastavení → Možnosti pro vývojáře**
4. Zapněte přepínač **Ladění přes USB**
5. Připojte zařízení USB kabelem k počítači
6. Na displeji telefonu se zobrazí výzva — klepněte na **Povolit (Allow)**

> **Tip:** Na některých zařízeních (Samsung, Xiaomi atd.) se Možnosti pro vývojáře nacházejí pod jiným umístěním. Vyhledejte v Nastavení frázi „Ladění přes USB".

### 4.2 Řešení problémů s USB

- Použijte datový USB kabel (ne kabel pouze pro nabíjení)
- Zkuste jiný USB port počítače
- Na telefonu vyberte režim připojení **Přenos souborů (MTP)**, ne nabíjení
- Nainstalujte ovladače pro vaše zařízení (zejm. Samsung Kies, Huawei HiSuite apod.)

---

## 5. Přehled rozhraní

Okno aplikace se skládá ze dvou částí:

```
┌─────────────────────────────────────────────────────┐
│  [Přehled] [Extrakce] [Výsledky] [Záloha]           │  ← navigační záložky
│  [Porovnání] [Čas. osa] [Mapa] [Bypass]             │
│                                                      │
│  [CS / EN]  [Wi-Fi]  ●  Název zařízení              │  ← jazykový přepínač + status ADB
├─────────────────────────────────────────────────────┤
│                                                      │
│              Hlavní pracovní plocha                  │
│                                                      │
└─────────────────────────────────────────────────────┘
```

**Jazykový přepínač (CS / EN)** — přepíná veškerý text rozhraní okamžitě, bez restartu.

**Indikátor ADB** — zobrazuje stav připojení:
- 🟢 Zařízení připojeno (zobrazí model)
- 🔴 Žádné zařízení
- 🟡 Probíhá připojování

---

## 6. Záložka: Přehled (Dashboard)

Po připojení zařízení se automaticky zobrazí informace:

| Informace | Popis |
|---|---|
| Model | Obchodní název zařízení |
| Android | Verze operačního systému |
| IMEI | Jedinečný identifikátor sítě |
| Sériové číslo | Hardware identifikátor |
| CPU | Procesor a architektura |
| RAM | Celková operační paměť |
| Úložiště | Celkové / volné místo |
| Šifrování | Stav šifrování /data oddílu |
| Baterie | Aktuální nabití v % |

---

## 7. Záložka: Extrakce

### 7.1 Moduly extrakce

Vyberte, které typy dat chcete extrahovat:

| Modul | Extrahovaná data |
|---|---|
| **Informace o zařízení** | Systémové vlastnosti, model, IMEI, CPU, RAM, úložiště |
| **Kontakty** | Telefonní čísla a e-mailové adresy |
| **SMS/MMS** | Textové a multimediální zprávy |
| **Záznamy hovorů** | Příchozí / odchozí / zmeškané hovory |
| **Aplikace** | Seznam nainstalovaných aplikací |
| **Média** | Fotografie a videa (kopie souborů) |
| **Dokumenty** | PDF, Office dokumenty, textové soubory |
| **Wi-Fi sítě** | Uložená SSID hesla a typ zabezpečení |
| **Účty** | Konfigurované e-mailové a sociální účty |
| **Prohlížeč** | Historie navštívených stránek s URL a datem |
| **Struktura souborů** | Adresářový strom /sdcard |
| **Analýza souborů** | EXIF metadata a GPS souřadnice z fotek |
| **Hashe souborů** | MD5 a SHA-256 kontrolní součty |
| **Smazaná data** | Obnovení smazaných souborů *(vyžaduje root)* |

**Agentní moduly** (rozšířené funkce):

| Modul | Popis |
|---|---|
| ADB Backup | Záloha dat aplikací |
| App Providers | Data přes content providers |
| Statistiky využití | Statistiky spouštění aplikací |
| Termux Agent | Rozšířené terminálové funkce |

### 7.2 Postup extrakce

1. Otevřete záložku **Extrakce**
2. Zaškrtněte požadované moduly (nebo klikněte **Vybrat vše**)
3. Vyberte **výstupní složku** pro uložení dat
4. Klikněte na **Spustit extrakci**
5. Sledujte průběh v reálném čase v logovacím okně
6. Po dokončení přejděte na záložku **Výsledky**

> **Tip:** Extrakce médií (fotek a videí) může trvat déle u zařízení s velkým množstvím souborů. Modul **Hashe souborů** spusťte vždy jako poslední — počítá kontrolní součty všech extrahovaných souborů.

---

## 8. Záložka: Výsledky

Po dokončení extrakce zobrazuje souhrn:

- Výsledek každého modulu (✔ Úspěch / ✗ Chyba)
- Počet extrahovaných záznamů
- Možnost zobrazit detail každého souboru (tabulka nebo klíč-hodnota)

### 8.1 Export reportů

| Formát | Popis | Použití |
|---|---|---|
| **HTML** | Interaktivní webová stránka | Prohlížení v prohlížeči, sdílení |
| **PDF** | Profesionální dokument | Tiskový výstup, spisy |
| **Excel (.xlsx)** | Tabulkový procesor | Další analýza dat |

Reporty se uloží do výstupní složky extrakce (soubory `report.html`, `report.pdf`, `report.xlsx`).

### 8.2 Detail záznamu

Dvojklikem na jakýkoli řádek v tabulce se otevře okno s kompletními detaily daného záznamu. U souborů s GPS souřadnicemi se zobrazí odkaz na Google Maps.

---

## 9. Záložka: Záloha (Backup import)

Umožňuje importovat data ze záložních souborů i bez fyzicky připojeného telefonu.

### 9.1 Podporované formáty

| Formát | Přípona | Výrobce |
|---|---|---|
| **Auto-detekce** | – | Automaticky rozpozná formát |
| Samsung SmartSwitch | `.zip` / složka | Samsung |
| Huawei HiSuite | `.hbk` | Huawei |
| MIUI Backup | `.bak` | Xiaomi |
| Google Takeout | `.zip` / složka | Google |
| Sony Xperia Backup | `.bk` | Sony |
| OnePlus / OxygenOS | `.tar.gz` / složka | OnePlus |
| ADB Backup | `.ab` | Standardní Android |

### 9.2 Importovatelné datové typy

Kontakty · SMS · Záznamy hovorů · Média · Wi-Fi sítě · Kalendář

### 9.3 Postup importu

1. Klikněte na **Vybrat soubor** (záloha jako soubor) nebo **Vybrat složku** (rozbalená záloha)
2. Formát se automaticky detekuje a zobrazí se odznak detekce
3. Zvolte formát ručně v levém panelu, pokud auto-detekce selhala
4. Zaškrtněte importovací moduly
5. Vyberte výstupní složku
6. Klikněte na **Spustit import**

---

## 10. Záložka: Porovnání

Porovná výstupy dvou extrakcí a ukáže, co se mezi nimi změnilo — vhodné pro sledování změn v čase.

### 10.1 Porovnávané typy dat

Kontakty · SMS · Záznamy hovorů · Aplikace · Wi-Fi sítě · Účty · Prohlížeč · Informace o zařízení

### 10.2 Výsledky porovnání

| Kategorie | Popis |
|---|---|
| 🟢 Nové záznamy | Záznamy přidané po první extrakci |
| 🔴 Odstraněné záznamy | Záznamy smazané od první extrakce |
| 🟡 Změněné záznamy | Záznamy, u nichž se změnila hodnota pole |
| ⚪ Beze změn | Záznamy shodné v obou extrakcích |

### 10.3 Postup

1. Vyberte složku **Záloha A** (starší extrakce)
2. Vyberte složku **Záloha B** (novější extrakce)
3. Klikněte **Porovnat**
4. Prohlédněte si souhrn a rozbalte detail každé kategorie

---

## 11. Záložka: Časová osa

Vizualizuje všechny události chronologicky — ideální pro rekonstrukci aktivit zařízení.

### 11.1 Zdroje událostí

| Barva | Typ |
|---|---|
| 🟢 Zelená | SMS zprávy |
| 🔵 Modrá | Hovory |
| 🟠 Oranžová | Historie prohlížeče |
| 🩷 Růžová | Fotografie |
| 🟣 Fialová | Soubory |
| 🩵 Tyrkysová | Aplikace |

### 11.2 Ovládání

| Akce | Popis |
|---|---|
| **Táhnout myší** | Posun v čase (panoramování) |
| **Kolečko myši** | Přiblížení / oddálení časové osy |
| **Tlačítko Celý rozsah** | Zobrazení všech událostí najednou |
| **Klik na tečku** | Detail události (datum, čas, obsah) |
| **Zaškrtávací políčka** | Filtrování podle zdroje události |

### 11.3 Načtení dat

1. Klikněte na tlačítko načtení složky
2. Vyberte výstupní složku extrakce
3. Data se načtou v pozadí bez blokování rozhraní

> **Tip:** Shluky (větší kroužky s číslem) označují více událostí na jednom místě osy. Přibližte oblast kolečkem myši pro zobrazení jednotlivých událostí.

---

## 12. Záložka: Mapa

Zobrazuje GPS souřadnice fotek na interaktivní mapě (OpenStreetMap).

### 12.1 Podmínky zobrazení

- Fotografie musí mít GPS metadata v EXIF (ne všechna zařízení nebo nastavení tato metadata ukládají)
- Pro zobrazení mapy je vyžadováno internetové připojení

### 12.2 Ovládání mapy

| Akce | Popis |
|---|---|
| **Táhnout myší** | Posun mapy |
| **Kolečko myši** | Přiblížení / oddálení |
| **Klik na marker** | Zvýraznění fotografie v seznamu |
| **Klik v seznamu** | Vycentrování mapy na vybranou fotku |

### 12.3 Detail fotografie

Po kliknutí na marker nebo položku v seznamu se zobrazí panel s:
- Název souboru
- Datum a čas pořízení
- GPS souřadnice (zeměpisná šířka / délka)
- Nadmořská výška
- Výrobce a model fotoaparátu
- Rozlišení a velikost souboru

> **Limit:** Mapa zobrazuje max. 300 markerů, seznam max. 250 fotek. Pokud máte více fotografií s GPS, zobrazí se přehledová informace o celkovém počtu.

---

## 13. Záložka: Lock bypass

> ⚠ **Tento nástroj používejte výhradně na zařízeních, k jejichž analýze máte zákonné oprávnění.**

### 13.1 Diagnostika zařízení

Před výběrem metody vždy spusťte **Diagnostiku** (tlačítko v levém panelu). Zobrazí:

- Verze Androidu
- Typ šifrování (/data oddílu)
- Dostupnost root přístupu
- Přítomnost TWRP recovery

### 13.2 Metody obcházení zámku

#### Metoda 1: ADB root – smazání
- **Požadavky:** Root přístup (Magisk, SuperSU nebo adbd root)
- **Android:** 4 – 10
- **Popis:** Smaže soubory zámku (`gesture.key`, `password.key`, `locksettings.db`) přes ADB shell s rootem

#### Metoda 2: TWRP – smazání
- **Požadavky:** Zařízení musí být v TWRP recovery
- **Android:** 4 – 13
- **Popis:** Připojí oddíl /data v recovery a smaže soubory zámku. Vhodné pro šifrovaná zařízení (FDE/FBE)

### 13.3 Metody obcházení FRP (Factory Reset Protection)

> FRP je ochrana Google účtu aktivovaná po továrním resetu. Vyžaduje přihlášení k původnímu Google účtu.

| Metoda | Požadavky | Android |
|---|---|---|
| **FRP-1: Nastavení (ADB)** | ADB připojení | 5 – 6 (bez záplaty) |
| **FRP-2: TWRP wipe** | Zařízení v TWRP | 5+ |
| **FRP-3: Fastboot erase** | Odemčený bootloader | 5+ |
| **FRP-4: Root smazání** | Root přístup | 5+ |

### 13.4 Tabulka kompatibility

| Verze Androidu | ADB root | TWRP |
|---|---|---|
| Android 4 – 6 | ✔ Plná podpora | ✔ Plná podpora |
| Android 7 – 8 | ✔ Plná podpora | ⚠ FDE výzvy |
| Android 9 – 10 | ✔ Plná podpora | ⚠ FBE výzvy |
| Android 11 – 13 | ⚠ Pouze Magisk | ⚠ FBE výzvy |
| Android 14+ | ⚠ Pouze Magisk | ⚠ FBE výzvy |

### 13.5 Příkazy restartu

V levém panelu jsou dostupné rychlé příkazy pro restart zařízení:
- **Restart do recovery** — pro TWRP
- **Restart do bootloaderu** — pro fastboot
- **Restart systému** — normální restart

---

## 14. Bezdrátové připojení (Wi-Fi ADB)

Aplikace podporuje připojení zařízení přes Wi-Fi bez USB kabelu.

### Postup

1. Nejdříve připojte zařízení USB kabelem a povolte USB Debugging
2. Klikněte na tlačítko **Wi-Fi** v horní liště aplikace
3. Zadejte IP adresu zařízení a port (výchozí: `5555`)
4. Potvrďte připojení
5. Kabel lze odpojit — komunikace probíhá přes síť

> **Podmínka:** Počítač i zařízení musí být ve stejné Wi-Fi síti.

---

## 15. Doporučený pracovní postup

```
1. Příprava
   └─ Povolte USB Debugging na zařízení
   └─ Připojte kabel

2. Identifikace zařízení
   └─ Zkontrolujte záložku Přehled
   └─ Zaznamenejte IMEI, sériové číslo, verzi OS

3. Extrakce dat
   └─ Vyberte moduly
   └─ Zvolte výstupní složku
   └─ Spusťte extrakci
   └─ Počkejte na dokončení

4. Export reportu
   └─ Záložka Výsledky → HTML / PDF / Excel

5. Analýza (volitelné)
   ├─ Časová osa → chronologický přehled aktivit
   ├─ Mapa → lokalizace fotografií
   └─ Porovnání → sledování změn mezi dvěma extrakcemi

6. Import zálohy (volitelné)
   └─ Záložka Záloha
   └─ Vyberte soubor / složku zálohy
   └─ Importujte požadovaná data

7. Obcházení zámku (pouze při oprávnění)
   └─ Spusťte Diagnostiku
   └─ Vyberte metodu dle výsledků
   └─ Spusťte a sledujte log
```

---

## 16. Časté problémy a řešení

### Zařízení není detekováno

| Příčina | Řešení |
|---|---|
| USB Debugging není povoleno | Povolte v Možnostech pro vývojáře |
| Kabel nepodporuje data | Použijte datový kabel |
| Chybí potvrzení na telefonu | Odemkněte displej a potvrďte výzvu „Povolit ladění" |
| Chybí ovladač USB | Nainstalujte ovladač výrobce zařízení |
| Jiné ADB servery | Spusťte `adb kill-server` a znovu `adb start-server` |

### Extrakce se nezdaří

| Příčina | Řešení |
|---|---|
| Zařízení není odemčeno | Odemkněte displej před extrakcí |
| Nedostatek oprávnění | Potvrďte všechna ADB povolení na zařízení |
| Zařízení odpojeno v průběhu | Zkontrolujte kabel, opakujte extrakci |
| Nedostatek místa | Zkontrolujte volné místo na disku počítače |

### Modul „Smazaná data" nenajde nic

Obnovení smazaných dat vyžaduje **root přístup**. Bez rootu modul nenalezne žádná data.

### Mapa se nezobrazuje

Zkontrolujte internetové připojení. Knihovna `tkintermapview` načítá dlaždice z OpenStreetMap.

### Záložka Časová osa je prázdná

Načtěte správnou výstupní složku extrakce (musí obsahovat JSON soubory modulů jako `sms.json`, `calls.json` atd.).

### Při přepnutí jazyka zůstávají česká slova

Restartujte aplikaci, pokud se přepínač neprojeví okamžitě. V aktuální verzi se jazyk plně přepíná při přepnutí přepínače.

---

*ForenziQ v1.0 · Praktická příručka uživatele (CZ)*
