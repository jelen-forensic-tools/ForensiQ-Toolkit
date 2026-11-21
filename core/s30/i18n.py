"""Translations (Czech / English).

Usage:  from .i18n import t, set_lang;  set_lang('en');  t('log.sms', n=5, recovered=1)
Every key must exist in all languages with the same {placeholders} (checked by tests/test_i18n.py).
"""
import json
import locale
import os

LANGS = ('cs', 'en')
LANG_NAMES = {'cs': 'CZ', 'en': 'EN'}
_lang = 'en'


def set_lang(lang):
    global _lang
    if lang not in LANGS:
        raise ValueError(f'unsupported language: {lang}')
    _lang = lang


def get_lang():
    return _lang


def t(key, **kw):
    s = STRINGS[key][_lang]
    return s.format(**kw) if kw else s


def tn(key, n):
    """Plural form for count n. Czech: 1 / 2-4 / 0,5+; English: 1 / other."""
    forms = PLURALS[key][_lang]
    if _lang == 'cs':
        i = 0 if n == 1 else 1 if 2 <= n <= 4 else 2
    else:
        i = 0 if n == 1 else 1
    return forms[i].format(n=n)


# ---------------------------------------------------------------- persisted preference
def _config_path():
    base = os.environ.get('APPDATA') or os.path.expanduser('~/.config')
    return os.path.join(base, 'SC6531E-Dump-Analyzer', 'config.json')


def system_lang():
    try:
        loc = (locale.getlocale()[0] or '').lower()
    except Exception:
        loc = ''
    return 'cs' if loc.startswith(('cs', 'czech')) else 'en'


def load_lang():
    try:
        with open(_config_path(), encoding='utf-8') as f:
            lang = json.load(f).get('lang')
        if lang in LANGS:
            return lang
    except Exception:
        pass
    return system_lang()


def save_lang(lang):
    try:
        p = _config_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump({'lang': lang}, f)
    except Exception:
        pass


# ---------------------------------------------------------------- strings
STRINGS = {
    # ---- log
    'log.loading': {'cs': 'Načítám {name} ({size} B)', 'en': 'Loading {name} ({size} bytes)'},
    'log.device': {'cs': 'Zařízení: {device}', 'en': 'Device: {device}'},
    'log.unsupported': {'cs': 'UPOZORNĚNÍ: dump nevypadá jako SC6531E/MOCOR.',
                        'en': 'WARNING: the dump does not look like SC6531E/MOCOR.'},
    'log.nv_found': {'cs': 'NV log: bloky {blocks} × {kb} KB, položky: {items}',
                     'en': 'NV log: blocks {blocks} × {kb} KB, items: {items}'},
    'log.nv_fallback': {'cs': 'NV log nerozpoznán – záložní vyhledávání vzoru',
                        'en': 'NV log not recognized – using fallback pattern search'},
    'log.items': {'cs': 'Relevantní NV položky: {items}', 'en': 'Relevant NV items: {items}'},
    'log.contacts': {'cs': 'Kontakty: záznamy {n}, prázdné sloty {empty} (slotů celkem {slots})',
                     'en': 'Contacts: records {n}, empty slots {empty} (total slots {slots})'},
    'log.contacts_none': {'cs': 'Kontakty: nenalezeny', 'en': 'Contacts: not found'},
    'log.sms': {'cs': 'SMS: {n} (z toho obnoveno ze starších kopií: {recovered})',
                'en': 'SMS: {n} (recovered from older copies: {recovered})'},
    'log.calls': {'cs': 'Hovory: {n} (aktuální výpis {current}, obnoveno {recovered})',
                  'en': 'Calls: {n} (current log {current}, recovered {recovered})'},
    'log.fat_found': {'cs': 'FAT12 nalezen: sektory {sectors} × {bps} B, přítomno {present}',
                      'en': 'FAT12 found: sectors {sectors} × {bps} bytes, present {present}'},
    'log.fat_none': {'cs': 'Soubory: FAT nenalezen', 'en': 'Files: FAT not found'},
    'log.files': {'cs': 'Soubory: aktivní {active}, smazané {deleted}',
                  'en': 'Files: active {active}, deleted {deleted}'},
    'log.saved': {'cs': 'Uloženo: {file}', 'en': 'Saved: {file}'},
    'log.pdf_gen': {'cs': 'Generuji PDF report…', 'en': 'Generating PDF report…'},
    'log.pdf_section': {'cs': 'PDF: {section}', 'en': 'PDF: {section}'},
    'log.pdf_saved': {'cs': 'PDF uloženo: {file}', 'en': 'PDF saved: {file}'},
    'log.no_pillow': {'cs': 'Pillow není k dispozici – náhledy fotek přeskočeny.',
                      'en': 'Pillow is not available – photo thumbnails skipped.'},
    'log.warning': {'cs': 'UPOZORNĚNÍ: {msg}', 'en': 'WARNING: {msg}'},
    'log.done': {'cs': 'Hotovo → {path}', 'en': 'Done → {path}'},

    # ---- warnings
    'warn.unsupported': {
        'cs': 'V dumpu nebyl nalezen identifikátor SC6531E – nemusí jít o podporovaný model, výsledky mohou '
              'být neúplné nebo chybné.',
        'en': 'The SC6531E identifier was not found in the dump – it may not be a supported model; results may '
              'be incomplete or wrong.'},
    'warn.nv_fallback': {
        'cs': 'Struktura NV logu nebyla rozpoznána; použito vyhledávání vzoru v celém dumpu (vyšší riziko '
              'falešných nálezů).',
        'en': 'The NV log structure was not recognized; a pattern search over the whole dump was used (higher '
              'risk of false positives).'},
    'warn.no_contacts': {'cs': 'Nebyly nalezeny žádné záznamy telefonního seznamu.',
                         'en': 'No phonebook records were found.'},
    'warn.contact_versions': {
        'cs': 'Kontakt NV {id} má více platných verzí s různým obsahem; použita verze na offsetu {offset}.',
        'en': 'Contact NV {id} has several valid versions with different content; the one at offset {offset} '
              'was used.'},
    'warn.no_fat': {'cs': 'Souborový systém (FAT12) nebyl nalezen.', 'en': 'The file system (FAT12) was not found.'},
    'warn.bad_sector_size': {'cs': 'Neplatný nebo nepodporovaný boot sektor FAT (velikost sektoru {bps}).',
                             'en': 'Invalid or unsupported FAT boot sector (sector size {bps}).'},
    'warn.sector_versions': {'cs': 'Sektor FAT {sector} má více platných verzí; použita poslední v dumpu.',
                             'en': 'FAT sector {sector} has several valid versions; the last one in the dump '
                                   'was used.'},

    # ---- values
    'val.yes': {'cs': 'ano', 'en': 'yes'},
    'val.no': {'cs': 'ne', 'en': 'no'},
    'val.dir.in': {'cs': 'přijatá', 'en': 'received'},
    'val.dir.out': {'cs': 'odeslaná', 'en': 'sent'},
    'val.dir.draft': {'cs': 'neodeslaná / koncept', 'en': 'unsent / draft'},
    'val.src.sms.current': {'cs': 'aktuální', 'en': 'current'},
    'val.src.sms.recovered': {'cs': 'obnovená (starší kopie, pravděpodobně smazaná)',
                              'en': 'recovered (older copy, probably deleted)'},
    'val.src.call.current': {'cs': 'aktuální', 'en': 'current'},
    'val.src.call.recovered': {'cs': 'obnovený (starší kopie)', 'en': 'recovered (older copy)'},
    'val.file.ok': {'cs': 'OK', 'en': 'OK'},
    'val.file.incomplete': {'cs': 'neúplný ({got}/{size} B)', 'en': 'incomplete ({got}/{size} bytes)'},
    'val.file.deleted': {'cs': 'smazaný – obsah nejistý', 'en': 'deleted – content uncertain'},

    # ---- CSV columns
    'col.index': {'cs': 'index', 'en': 'index'},
    'col.name': {'cs': 'jméno', 'en': 'name'},
    'col.number': {'cs': 'číslo', 'en': 'number'},
    'col.type_code': {'cs': 'kód typu', 'en': 'type code'},
    'col.ton': {'cs': 'TON', 'en': 'TON'},
    'col.truncated': {'cs': 'oříznuto', 'en': 'truncated'},
    'col.nv_id': {'cs': 'NV ID', 'en': 'NV ID'},
    'col.offset': {'cs': 'offset', 'en': 'offset'},
    'col.time': {'cs': 'čas', 'en': 'time'},
    'col.tz': {'cs': 'časové pásmo', 'en': 'time zone'},
    'col.direction': {'cs': 'směr', 'en': 'direction'},
    'col.contact': {'cs': 'kontakt', 'en': 'contact'},
    'col.text': {'cs': 'text', 'en': 'text'},
    'col.part': {'cs': 'díl', 'en': 'part'},
    'col.total': {'cs': 'dílů celkem', 'en': 'parts total'},
    'col.ref': {'cs': 'reference', 'en': 'reference'},
    'col.source': {'cs': 'zdroj', 'en': 'source'},
    'col.status_code': {'cs': 'kód stavu', 'en': 'status code'},
    'col.saved_time': {'cs': 'čas uložení', 'en': 'saved time'},
    'col.smsc': {'cs': 'SMSC', 'en': 'SMSC'},
    'col.duration_s': {'cs': 'délka [s]', 'en': 'duration [s]'},
    'col.path': {'cs': 'cesta', 'en': 'path'},
    'col.size': {'cs': 'velikost [B]', 'en': 'size [bytes]'},
    'col.date': {'cs': 'datum', 'en': 'date'},
    'col.deleted': {'cs': 'smazaný', 'en': 'deleted'},
    'col.status': {'cs': 'stav', 'en': 'status'},

    # ---- output file names
    'fn.folder': {'cs': 'analyza', 'en': 'analysis'},
    'fn.contacts': {'cs': 'kontakty', 'en': 'contacts'},
    'fn.sms': {'cs': 'sms', 'en': 'sms'},
    'fn.calls': {'cs': 'hovory', 'en': 'calls'},
    'fn.files': {'cs': 'soubory', 'en': 'files'},
    'fn.files_deleted': {'cs': 'soubory_smazane_NEJISTE', 'en': 'files_deleted_UNCERTAIN'},
    'fn.files_list': {'cs': 'soubory_seznam', 'en': 'files_list'},

    # ---- sections
    'sec.contacts': {'cs': 'Kontakty', 'en': 'Contacts'},
    'sec.calls': {'cs': 'Hovory', 'en': 'Calls'},
    'sec.files': {'cs': 'Soubory', 'en': 'Files'},

    # ---- report
    'rep.title': {'cs': 'Report z analýzy dumpu telefonu', 'en': 'Phone Dump Analysis Report'},
    'rep.case': {'cs': 'Případ', 'en': 'Case'},
    'rep.case_id': {'cs': 'Číslo případu', 'en': 'Case number'},
    'rep.examiner': {'cs': 'Zpracovatel', 'en': 'Examiner'},
    'rep.note': {'cs': 'Poznámka', 'en': 'Note'},
    'rep.analysis_date': {'cs': 'Datum analýzy', 'en': 'Analysis date'},
    'rep.tool': {'cs': 'Nástroj', 'en': 'Tool'},
    'rep.source': {'cs': 'Zdrojový soubor', 'en': 'Source file'},
    'rep.file': {'cs': 'Soubor', 'en': 'File'},
    'rep.size': {'cs': 'Velikost', 'en': 'Size'},
    'rep.bytes': {'cs': '{n} B', 'en': '{n} bytes'},
    'rep.device': {'cs': 'Zařízení', 'en': 'Device'},
    'rep.chipset': {'cs': 'Čipset', 'en': 'Chipset'},
    'rep.unrecognized': {'cs': 'NEROZPOZNÁNO', 'en': 'NOT RECOGNIZED'},
    'rep.platform': {'cs': 'Platforma', 'en': 'Platform'},
    'rep.mmi_hw': {'cs': 'MMI / HW verze', 'en': 'MMI / HW version'},
    'rep.nv_area': {'cs': 'NV oblast (uživatelská data)', 'en': 'NV area (user data)'},
    'rep.nv_value': {'cs': 'bloky {blocks} × {kb} KB, {range}', 'en': 'blocks {blocks} × {kb} KB, {range}'},
    'rep.disk': {'cs': 'Interní disk', 'en': 'Internal disk'},
    'rep.disk_value': {'cs': 'FAT12 „{label}“, sektory {sectors} × {bps} B, přítomno {present}',
                       'en': 'FAT12 "{label}", sectors {sectors} × {bps} bytes, present {present}'},
    'rep.not_found': {'cs': 'nenalezen', 'en': 'not found'},
    'rep.summary': {'cs': 'Souhrn', 'en': 'Summary'},
    'rep.sum_contacts': {
        'cs': 'záznamy {n} (různá jména {names}), prázdné sloty {empty} ze {slots}; jména na limitu 20 bajtů '
              '(pravděpodobně oříznutá): {truncated}',
        'en': 'records {n} (distinct names {names}), empty slots {empty} of {slots}; names at the 20-byte limit '
              '(probably truncated): {truncated}'},
    'rep.sum_sms': {'cs': '{n} (přijaté {inc}, odeslané {out}, neodeslané / koncepty {draft}; '
                          'obnovené ze starších kopií {recovered})',
                    'en': '{n} (received {inc}, sent {out}, unsent / drafts {draft}; '
                          'recovered from older copies {recovered})'},
    'rep.sum_calls': {'cs': '{n} (aktuální výpis {current}, obnovené {recovered})',
                      'en': '{n} (current log {current}, recovered {recovered})'},
    'rep.sum_files': {'cs': 'aktivní {active} (z toho fotografie {photos}), smazané záznamy {deleted}',
                      'en': 'active {active} (incl. photos {photos}), deleted entries {deleted}'},
    'rep.period': {'cs': ', období {a} – {b}', 'en': ', period {a} – {b}'},
    'rep.limitations': {'cs': 'Upozornění a omezení', 'en': 'Warnings and limitations'},
    'rep.lim1': {'cs': 'Časy jsou v místním čase telefonu. U přijatých SMS je uvedeno časové pásmo ústředny.',
                 'en': 'Times are in the phone\'s local time. For received SMS the time zone of the service '
                       'centre is given.'},
    'rep.lim2': {'cs': 'Jména kontaktů jsou v paměti telefonu omezena na 20 bajtů (tj. 20 znaků bez diakritiky, '
                       '10 znaků s diakritikou); delší jména jsou oříznutá již v zařízení.',
                 'en': 'Contact names are limited to 20 bytes in the phone memory (20 characters without '
                       'diacritics, 10 with); longer names are truncated by the device itself.'},
    'rep.lim3': {'cs': 'Kontakty uložené na SIM kartě nejsou součástí dumpu paměti telefonu.',
                 'en': 'Contacts stored on the SIM card are not part of the phone memory dump.'},
    'rep.lim4': {'cs': 'Význam kódů typu čísla (kontakty) a typu hovoru nebyl ověřen; uvádějí se surové hodnoty.',
                 'en': 'The meaning of number type codes (contacts) and call type codes has not been verified; '
                       'raw values are shown.'},
    'rep.lim5': {'cs': 'Záznamy označené „obnovené“ pocházejí ze starších (zneplatněných) kopií v NV logu – mohou '
                       'to být smazané nebo přepsané záznamy.',
                 'en': 'Records marked "recovered" come from older (invalidated) copies in the NV log – they may '
                       'be deleted or overwritten records.'},
    'rep.lim6': {'cs': 'Obsah smazaných souborů je rekonstruován předpokladem souvislých clusterů a může být '
                       'přepsán jinými daty.',
                 'en': 'The content of deleted files is reconstructed assuming contiguous clusters and may have '
                       'been overwritten by other data.'},
    'rep.h_contacts': {'cs': 'Kontakty ({n})', 'en': 'Contacts ({n})'},
    'rep.h_sms': {'cs': 'SMS (zprávy po spojení vícedílných: {n}, záznamy: {records})',
                  'en': 'SMS (messages after joining multi-part: {n}, records: {records})'},
    'rep.h_calls': {'cs': 'Hovory ({n})', 'en': 'Calls ({n})'},
    'rep.h_files': {'cs': 'Soubory ({n})', 'en': 'Files ({n})'},
    'rep.h_photos': {'cs': 'Náhledy fotografií ({n})', 'en': 'Photo thumbnails ({n})'},
    'rep.th_type': {'cs': 'Typ', 'en': 'Type'},
    'rep.th_dir': {'cs': 'Směr', 'en': 'Direction'},
    'rep.th_number_contact': {'cs': 'Číslo / kontakt', 'en': 'Number / contact'},
    'rep.th_note': {'cs': 'Pozn.', 'en': 'Note'},
    'rep.th_duration': {'cs': 'Délka', 'en': 'Duration'},
    'rep.th_size': {'cs': 'Velikost [B]', 'en': 'Size [bytes]'},
    'rep.parts': {'cs': 'díly {parts}', 'en': 'parts {parts}'},
    'rep.missing_parts': {'cs': '[chybí díl(y) {parts} z {total}]', 'en': '[missing part(s) {parts} of {total}]'},
    'rep.recovered_sms': {'cs': 'OBNOVENÁ', 'en': 'RECOVERED'},
    'rep.recovered_call': {'cs': 'OBNOVENÝ', 'en': 'RECOVERED'},
    'rep.cannot_show': {'cs': '[nelze zobrazit]', 'en': '[cannot display]'},
    'rep.page': {'cs': 'Strana {n}', 'en': 'Page {n}'},

    # ---- GUI
    'gui.version': {'cs': 'verze {v}', 'en': 'version {v}'},
    'gui.language': {'cs': 'Jazyk', 'en': 'Language'},
    'gui.side.chipset': {'cs': 'Podporovaný čipset', 'en': 'Supported chipset'},
    'gui.side.firmware': {'cs': 'Firmware', 'en': 'Firmware'},
    'gui.side.extracts': {'cs': 'Extrahuje', 'en': 'Extracts'},
    'gui.side.extracts_val': {'cs': 'kontakty · SMS · hovory\nsoubory · obraz disku',
                              'en': 'contacts · SMS · calls\nfiles · disk image'},
    'gui.side.readonly': {'cs': 'Dump se pouze čte,\nnikdy se nemění.', 'en': 'The dump is only read,\nnever modified.'},
    'gui.new': {'cs': 'Nová analýza', 'en': 'New analysis'},
    'gui.results': {'cs': 'Výsledky', 'en': 'Results'},
    'gui.card.io': {'cs': 'Vstup a výstup', 'en': 'Input and output'},
    'gui.dump': {'cs': 'Dump telefonu', 'en': 'Phone dump'},
    'gui.dump_ph': {'cs': 'Vyberte soubor .bin…', 'en': 'Select a .bin file…'},
    'gui.out': {'cs': 'Výstupní složka', 'en': 'Output folder'},
    'gui.out_ph': {'cs': 'Kam uložit výsledky…', 'en': 'Where to save the results…'},
    'gui.browse': {'cs': 'Procházet', 'en': 'Browse'},
    'gui.card.case': {'cs': 'Údaje do reportu (nepovinné)', 'en': 'Report details (optional)'},
    'gui.case_ph': {'cs': 'Číslo případu', 'en': 'Case number'},
    'gui.exam_ph': {'cs': 'Zpracovatel', 'en': 'Examiner'},
    'gui.note_ph': {'cs': 'Poznámka', 'en': 'Note'},
    'gui.card.extract': {'cs': 'Co extrahovat', 'en': 'What to extract'},
    'gui.opt.contacts': {'cs': 'Kontakty', 'en': 'Contacts'},
    'gui.opt.sms': {'cs': 'SMS', 'en': 'SMS'},
    'gui.opt.calls': {'cs': 'Výpis hovorů', 'en': 'Call log'},
    'gui.opt.files': {'cs': 'Soubory', 'en': 'Files'},
    'gui.opt.disk': {'cs': 'Obraz disku (.img)', 'en': 'Disk image (.img)'},
    'gui.opt.pdf': {'cs': 'PDF report', 'en': 'PDF report'},
    'gui.opt.thumbs': {'cs': 'Náhledy fotek v PDF', 'en': 'Photo thumbnails in PDF'},
    'gui.tile.off': {'cs': 'nevybráno', 'en': 'not selected'},
    'gui.no_analysis': {'cs': 'Zatím nebyla spuštěna žádná analýza.', 'en': 'No analysis has been run yet.'},
    'gui.unknown_device': {'cs': 'neznámé zařízení', 'en': 'unknown device'},
    'gui.card.log': {'cs': 'Průběh', 'en': 'Progress'},
    'gui.ready': {'cs': 'Připraveno', 'en': 'Ready'},
    'gui.open': {'cs': 'Otevřít výsledky', 'en': 'Open results'},
    'gui.run': {'cs': 'Spustit analýzu', 'en': 'Run analysis'},
    'gui.running': {'cs': 'Probíhá analýza…', 'en': 'Analysis in progress…'},
    'gui.done': {'cs': 'Hotovo – uloženo do složky {folder}', 'en': 'Done – saved to folder {folder}'},
    'gui.error': {'cs': 'Chyba: {msg}', 'en': 'Error: {msg}'},
    'gui.err.no_dump': {'cs': 'Vyberte existující soubor s dumpem.', 'en': 'Select an existing dump file.'},
    'gui.err.no_out': {'cs': 'Vyberte výstupní složku.', 'en': 'Select an output folder.'},
    'gui.err.nothing': {'cs': 'Zapněte alespoň jednu položku k extrakci.',
                        'en': 'Enable at least one item to extract.'},
    'gui.dlg.dump': {'cs': 'Vyberte dump', 'en': 'Select dump'},
    'gui.dlg.out': {'cs': 'Vyberte výstupní složku', 'en': 'Select output folder'},
    'gui.dlg.all': {'cs': 'Všechny soubory', 'en': 'All files'},
}

# count-dependent strings, used with tn(key, n): cs = (1, 2-4, 0/5+), en = (1, other)
PLURALS = {
    'gui.tile.empty_slots': {'cs': ('{n} prázdný slot', '{n} prázdné sloty', '{n} prázdných slotů'),
                             'en': ('{n} empty slot', '{n} empty slots')},
    'gui.tile.recovered_sms': {'cs': ('{n} obnovená', '{n} obnovené', '{n} obnovených'),
                               'en': ('{n} recovered', '{n} recovered')},
    'gui.tile.recovered_calls': {'cs': ('{n} obnovený', '{n} obnovené', '{n} obnovených'),
                                 'en': ('{n} recovered', '{n} recovered')},
    'gui.tile.photos': {'cs': ('{n} fotografie', '{n} fotografie', '{n} fotografií'),
                        'en': ('{n} photo', '{n} photos')},
}
