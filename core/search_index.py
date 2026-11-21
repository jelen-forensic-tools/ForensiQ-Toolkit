"""
core/search_index.py – fulltextový index nad výstupy extrakce (fáze 1).

Zaindexuje všechny *.json v kořeni výstupní složky do
`<output_dir>/_case/index.sqlite`:

  * records      – jeden řádek = jeden záznam (SMS, hovor, kontakt, soubor…),
                   s odkazem na zdrojový soubor, pozicí a stabilním klíčem
                   `row_key` (SHA-1 kanonického JSON) pro pozdější štítky,
  * records_fts  – SQLite FTS5 (unicode61, remove_diacritics 2) → hledání
                   bez ohledu na diakritiku a velikost písmen, i prefixem,
  * numbers      – normalizovaná telefonní čísla (posledních 9 číslic),
                   takže +420 777 123 456 = 777123456 = 00420777123456,
  * entities     – e-maily, URL, IBAN, kryptoadresy, rodná čísla, SPZ.

Index je přírůstkový: soubory se přeindexují jen při změně (velikost, mtime,
SHA-256); smazané soubory se z indexu odstraní. Složka `_case/` je pracovní
produkt analytika a je vyloučená z manifestu důkazů (core/evidence.py).

Jen standardní knihovna. Volat z vlákna na pozadí – každé volání si otevírá
vlastní spojení k databázi.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sqlite3
from typing import Callable, Iterable, Iterator, Optional

CASE_DIR = "_case"
INDEX_NAME = "index.sqlite"
SCHEMA_VERSION = 1

# Soubory ve výstupní složce, které nejsou daty zařízení (neindexují se).
_SKIP_FILES = {"evidence_manifest.json"}

# Klíče, jejichž hodnota je telefonní číslo (nebo seznam čísel).
_NUMBER_KEYS = re.compile(
    r"(^|_)(number|numbers|phone|phones|tel|msisdn|address|caller|callee|"
    r"from|to|sender|recipient|remote|handle|jid)($|_)", re.I)
# Klíče s datem – první nalezený se použije jako čas záznamu.
_DATE_KEYS = ("date", "time", "timestamp", "datetime", "date_taken",
              "date_sent", "date_received", "start", "created", "modified",
              "last_time_used", "last_visit")

_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y:%m:%d %H:%M:%S",
                 "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M",
                 "%d.%m.%Y", "%Y-%m-%d", "%Y%m%d")

_CHUNK = 1 << 20


# ── Normalizace ──────────────────────────────────────────────────────────────

def num_key(n: str) -> str:
    """Porovnávací klíč čísla: posledních 9 číslic (národní = mezinárodní tvar).

    Kratší čísla (servisní, zkrácená) se vrací celá; prázdný řetězec, pokud
    hodnota neobsahuje aspoň 3 číslice (není to číslo).
    """
    d = re.sub(r"[^0-9]", "", str(n or ""))
    if len(d) < 3:
        return ""
    return d[-9:] if len(d) >= 9 else d


def _looks_like_number(v: str) -> bool:
    """Hodnota vypadá jako telefonní číslo (ne jako text, e-mail, datum)."""
    v = v.strip()
    if not v or len(v) > 32 or "@" in v:
        return False
    if not re.fullmatch(r"[+()\d\s\-./*#]+", v):
        return False
    digits = re.sub(r"\D", "", v)
    if not 3 <= len(digits) <= 15:
        return False
    # Datum / čas typu 2025-01-01 nebo 12:30 – ne číslo
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", v):
        return False
    return True


def parse_dt(value) -> Optional[str]:
    """Převede hodnotu data na ISO 'YYYY-MM-DD HH:MM:SS' (nebo None)."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ts = float(value)
        if ts > 1e12:          # milisekundy
            ts /= 1000.0
        if 315532800 <= ts <= 4102444800:   # 1980–2100
            try:
                return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            except (OverflowError, OSError, ValueError):
                return None
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit() and len(s) in (10, 13):
        return parse_dt(int(s))
    s2 = s.replace("Z", "").split(".")[0].split("+")[0].strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(s2, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


# ── Entity (regex) ───────────────────────────────────────────────────────────

_RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}\b")
_RE_URL = re.compile(r"\b(?:https?://|www\.)[^\s\"'<>]{3,}", re.I)
_RE_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")
_RE_BTC = re.compile(r"\b(?:bc1[ac-hj-np-z02-9]{11,71}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b")
_RE_ETH = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_RE_RC = re.compile(r"\b(\d{2})(\d{2})(\d{2}) ?/ ?(\d{3,4})\b")
# SPZ ČR: číslice + 2 znaky + 4 číslice; písmena G, O, Q, W se nepoužívají
# (vyloučí i falešné nálezy typu „5GB 1024“).
_RE_SPZ = re.compile(r"\b([1-9][A-FH-NPR-VX-Z][A-FH-NPR-VX-Z0-9]) ?(\d{4})\b")
_RE_INTL_PHONE = re.compile(r"(?<![\w+])\+\d[\d \-]{7,17}\d\b")

_IBAN_LEN = {"CZ": 24, "SK": 24, "DE": 22, "AT": 20, "PL": 28, "GB": 22,
             "FR": 27, "IT": 27, "ES": 24, "NL": 18, "BE": 16, "CH": 21,
             "HU": 28, "LT": 20, "LV": 21, "EE": 20, "SI": 19, "HR": 21,
             "RO": 24, "BG": 22, "IE": 22, "PT": 25, "LU": 20, "UA": 29}


def _iban_ok(s: str) -> bool:
    s = s.replace(" ", "").upper()
    exp = _IBAN_LEN.get(s[:2])
    if exp is None or len(s) != exp:
        return False
    num = "".join(str(int(c, 36)) for c in s[4:] + s[:4])
    return int(num) % 97 == 1


def _rc_ok(m: re.Match) -> bool:
    """Rodné číslo: platné datum + kontrola dělitelnosti 11 (u 10místných)."""
    yy, mm, dd, tail = (int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4))
    if mm > 70:
        mm -= 70
    elif mm > 50:
        mm -= 50
    elif mm > 20:
        mm -= 20
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        return False
    if len(tail) == 3:
        return yy < 54          # 9místná RČ jen do roku 1953
    full = int(m.group(1) + m.group(2) + m.group(3) + tail)
    if full % 11 == 0:
        return True
    # Výjimka: zbytek 10 → kontrolní číslice 0
    base = int(m.group(1) + m.group(2) + m.group(3) + tail[:3])
    return base % 11 == 10 and tail[3] == "0"


def extract_entities(text: str) -> list[tuple[str, str, str]]:
    """Vrátí [(kind, value, norm)] nalezených entit v textu."""
    out: list[tuple[str, str, str]] = []
    if not text:
        return out
    for m in _RE_EMAIL.finditer(text):
        v = m.group(0).strip(".")
        out.append(("email", v, v.lower()))
    for m in _RE_URL.finditer(text):
        v = m.group(0).rstrip(".,;:)]}")
        norm = re.sub(r"^(https?://)?(www\.)?", "", v.lower()).rstrip("/")
        out.append(("url", v, norm))
    for m in _RE_IBAN.finditer(text):
        # Regex může zachytit i následující číselnou skupinu (např. rok) –
        # zkoušíme od nejdelší varianty a odebíráme skupiny zprava.
        v = m.group(0)
        while v:
            if _iban_ok(v):
                out.append(("iban", v, v.replace(" ", "").upper()))
                break
            v = v[:-1].rstrip()
    for m in _RE_BTC.finditer(text):
        v = m.group(0)
        # base58 bez číslic/velkých písmen je spíš běžné slovo
        if v.startswith("bc1") or (re.search(r"\d", v) and re.search(r"[A-Z]", v)
                                   and re.search(r"[a-z]", v)):
            out.append(("crypto", v, v))
    for m in _RE_ETH.finditer(text):
        out.append(("crypto", m.group(0), m.group(0).lower()))
    for m in _RE_RC.finditer(text):
        if _rc_ok(m):
            norm = m.group(1) + m.group(2) + m.group(3) + "/" + m.group(4)
            out.append(("rc", m.group(0), norm))
    for m in _RE_SPZ.finditer(text):
        out.append(("spz", m.group(0), m.group(1) + m.group(2)))
    for m in _RE_INTL_PHONE.finditer(text):
        k = num_key(m.group(0))
        if len(k) == 9:
            out.append(("phone", m.group(0), k))
    return out


# ── Rozklad JSON na záznamy ──────────────────────────────────────────────────

def _is_record_list(v) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v[:50])


def iter_records(data) -> Iterator[tuple[str, int, dict]]:
    """Rozloží obsah JSON souboru na (section, index, record).

    * seznam slovníků            → každý prvek je záznam (section "")
    * slovník se seznamy záznamů → záznamy z každého seznamu (section = klíč),
                                   zbylé skalární hodnoty jako jeden záznam
    * jiný slovník               → jeden záznam (např. device_info)
    """
    if isinstance(data, list):
        for i, rec in enumerate(data):
            if isinstance(rec, dict):
                yield "", i, rec
            elif rec not in (None, ""):
                yield "", i, {"value": rec}
        return
    if not isinstance(data, dict):
        return
    lists = {k: v for k, v in data.items() if _is_record_list(v)}
    if not lists:
        yield "", 0, data
        return
    for k, v in lists.items():
        for i, rec in enumerate(v):
            if isinstance(rec, dict):
                yield k, i, rec
    rest = {k: v for k, v in data.items() if k not in lists}
    if any(not isinstance(v, (dict, list)) or v for v in rest.values()):
        yield "", 0, rest


def _flatten(obj, key: str = "") -> Iterator[tuple[str, object]]:
    """(klíč, skalární hodnota) pro všechny listy vnořené struktury."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _flatten(v, str(k))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _flatten(v, key)
    elif obj is not None and obj != "":
        yield key, obj


def row_key(rec: dict) -> str:
    """Stabilní identifikátor záznamu (SHA-1 kanonického JSON)."""
    canon = json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()


def analyze_record(rec: dict) -> tuple[str, Optional[str], list, list]:
    """Vrátí (fts_text, dt, [(num_key, raw)], [(kind, value, norm)])."""
    parts: list[str] = []
    numbers: dict[str, str] = {}
    dt: Optional[str] = None
    date_rank = len(_DATE_KEYS)
    for k, v in _flatten(rec):
        if isinstance(v, bool):
            continue
        sv = str(v)
        if k.lower() == "path" or k.lower().endswith("_path"):
            parts.append(sv.replace("/", " ").replace("\\", " "))
        parts.append(sv)
        kl = k.lower()
        if kl in _DATE_KEYS and _DATE_KEYS.index(kl) < date_rank:
            p = parse_dt(v)
            if p:
                dt, date_rank = p, _DATE_KEYS.index(kl)
        if _NUMBER_KEYS.search(kl) and isinstance(v, (str, int)) and \
                _looks_like_number(sv):
            nk = num_key(sv)
            if nk:
                numbers.setdefault(nk, sv)
    text = "\n".join(parts)
    entities = extract_entities(text)
    for kind, raw, norm in entities:
        if kind == "phone":
            numbers.setdefault(norm, raw)
    entities = [e for e in entities if e[0] != "phone"]
    # Normalizovaná čísla do FTS textu → hledání "777123456" najde i "+420 777 123 456"
    if numbers:
        text += "\n" + " ".join(numbers)
    return text, dt, list(numbers.items()), list(dict.fromkeys(entities))


# ── Databáze ─────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS files(
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    size INTEGER, mtime REAL, sha256 TEXT,
    records INTEGER DEFAULT 0,
    indexed_at TEXT
);
CREATE TABLE IF NOT EXISTS records(
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    section TEXT NOT NULL DEFAULT '',
    idx INTEGER NOT NULL,
    row_key TEXT NOT NULL,
    dt TEXT,
    json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_records_file ON records(file_id);
CREATE INDEX IF NOT EXISTS ix_records_key ON records(row_key);
CREATE INDEX IF NOT EXISTS ix_records_dt ON records(dt);
CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
    text, tokenize = 'unicode61 remove_diacritics 2', prefix = '2 3');
CREATE TABLE IF NOT EXISTS numbers(
    record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    num_key TEXT NOT NULL, raw TEXT
);
CREATE INDEX IF NOT EXISTS ix_numbers_key ON numbers(num_key);
CREATE INDEX IF NOT EXISTS ix_numbers_rec ON numbers(record_id);
CREATE TABLE IF NOT EXISTS entities(
    record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    kind TEXT NOT NULL, value TEXT, norm TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_entities_kind ON entities(kind, norm);
CREATE INDEX IF NOT EXISTS ix_entities_rec ON entities(record_id);
"""


def case_dir(output_dir: str) -> str:
    return os.path.join(output_dir, CASE_DIR)


def index_path(output_dir: str) -> str:
    return os.path.join(case_dir(output_dir), INDEX_NAME)


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _open_db(output_dir: str) -> sqlite3.Connection:
    os.makedirs(case_dir(output_dir), exist_ok=True)
    conn = _connect(index_path(output_dir))
    ver = None
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        ver = int(row[0]) if row else None
    except sqlite3.Error:
        pass
    if ver is not None and ver != SCHEMA_VERSION:
        conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(index_path(output_dir) + suffix)
            except OSError:
                pass
        conn = _connect(index_path(output_dir))
    conn.executescript(_SCHEMA)
    conn.execute("INSERT OR REPLACE INTO meta VALUES('schema', ?)", (str(SCHEMA_VERSION),))
    conn.commit()
    return conn


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def list_source_files(output_dir: str) -> list[str]:
    """JSON soubory s daty v kořeni výstupní složky (seřazené)."""
    try:
        names = os.listdir(output_dir)
    except OSError:
        return []
    return sorted(n for n in names
                  if n.lower().endswith(".json") and n not in _SKIP_FILES
                  and os.path.isfile(os.path.join(output_dir, n)))


def _delete_file_rows(conn: sqlite3.Connection, file_id: int) -> None:
    conn.execute("DELETE FROM records_fts WHERE rowid IN "
                 "(SELECT id FROM records WHERE file_id=?)", (file_id,))
    conn.execute("DELETE FROM numbers WHERE record_id IN "
                 "(SELECT id FROM records WHERE file_id=?)", (file_id,))
    conn.execute("DELETE FROM entities WHERE record_id IN "
                 "(SELECT id FROM records WHERE file_id=?)", (file_id,))
    conn.execute("DELETE FROM records WHERE file_id=?", (file_id,))


def build_index(output_dir: str,
                progress: Optional[Callable[[int, int, str], None]] = None,
                cancel: Optional[Callable[[], bool]] = None,
                force: bool = False) -> dict:
    """Vytvoří / aktualizuje index. Vrací statistiku.

    progress(done, total, filename) se volá po každém souboru.
    cancel() → True přeruší indexaci (už zpracované soubory zůstanou).
    """
    stats = {"files": 0, "indexed": 0, "unchanged": 0, "removed": 0,
             "records": 0, "errors": [], "cancelled": False}
    conn = _open_db(output_dir)
    try:
        names = list_source_files(output_dir)
        stats["files"] = len(names)
        known = {r["name"]: r for r in conn.execute("SELECT * FROM files")}

        # Soubory, které už na disku nejsou
        for name, row in known.items():
            if name not in names:
                _delete_file_rows(conn, row["id"])
                conn.execute("DELETE FROM files WHERE id=?", (row["id"],))
                stats["removed"] += 1
        conn.commit()

        for i, name in enumerate(names):
            if cancel and cancel():
                stats["cancelled"] = True
                break
            path = os.path.join(output_dir, name)
            try:
                st = os.stat(path)
                row = known.get(name)
                if (not force and row is not None and row["size"] == st.st_size
                        and abs((row["mtime"] or 0) - st.st_mtime) < 1e-6):
                    stats["unchanged"] += 1
                    stats["records"] += row["records"] or 0
                    continue
                sha = _sha256(path)
                if not force and row is not None and row["sha256"] == sha:
                    conn.execute("UPDATE files SET size=?, mtime=? WHERE id=?",
                                 (st.st_size, st.st_mtime, row["id"]))
                    conn.commit()
                    stats["unchanged"] += 1
                    stats["records"] += row["records"] or 0
                    continue
                n = _index_file(conn, name, path, st, sha,
                                row["id"] if row is not None else None)
                stats["indexed"] += 1
                stats["records"] += n
            except Exception as e:      # poškozený soubor nesmí zastavit zbytek
                conn.rollback()
                stats["errors"].append(f"{name}: {e}")
            finally:
                if progress:
                    progress(i + 1, len(names), name)

        conn.execute("INSERT OR REPLACE INTO meta VALUES('built_at', ?)",
                     (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
        conn.commit()
        if stats["indexed"] or stats["removed"]:
            try:
                conn.execute("INSERT INTO records_fts(records_fts) VALUES('optimize')")
                conn.commit()
            except sqlite3.Error:
                pass
    finally:
        conn.close()
    return stats


def _index_file(conn: sqlite3.Connection, name: str, path: str,
                st: os.stat_result, sha: str, file_id: Optional[int]) -> int:
    with open(path, encoding="utf-8", errors="replace") as f:
        data = json.load(f)
    if file_id is not None:
        _delete_file_rows(conn, file_id)
        conn.execute("UPDATE files SET size=?, mtime=?, sha256=? WHERE id=?",
                     (st.st_size, st.st_mtime, sha, file_id))
    else:
        cur = conn.execute("INSERT INTO files(name, size, mtime, sha256) VALUES(?,?,?,?)",
                           (name, st.st_size, st.st_mtime, sha))
        file_id = cur.lastrowid

    count = 0
    for section, idx, rec in iter_records(data):
        text, dt, numbers, entities = analyze_record(rec)
        rj = json.dumps(rec, ensure_ascii=False, default=str)
        cur = conn.execute(
            "INSERT INTO records(file_id, section, idx, row_key, dt, json) "
            "VALUES(?,?,?,?,?,?)",
            (file_id, section, idx, row_key(rec), dt, rj))
        rid = cur.lastrowid
        conn.execute("INSERT INTO records_fts(rowid, text) VALUES(?,?)", (rid, text))
        if numbers:
            conn.executemany("INSERT INTO numbers VALUES(?,?,?)",
                             [(rid, k, raw) for k, raw in numbers])
        if entities:
            conn.executemany("INSERT INTO entities VALUES(?,?,?,?)",
                             [(rid, kind, v, norm) for kind, v, norm in entities])
        count += 1
    conn.execute("UPDATE files SET records=?, indexed_at=? WHERE id=?",
                 (count, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), file_id))
    conn.commit()
    return count


# ── Dotazy ───────────────────────────────────────────────────────────────────

def fts_query(text: str) -> str:
    """Převede uživatelský text na bezpečný FTS5 dotaz.

    Slova se spojí AND, každé jako prefix ("nov" najde "Novák"). Text
    v uvozovkách je fráze. Speciální znaky FTS se neinterpretují.
    """
    terms: list[str] = []
    for m in re.finditer(r'"([^"]+)"|(\S+)', text or ""):
        if m.group(1):
            words = re.findall(r"\w+", m.group(1))
            if words:
                terms.append('"' + " ".join(words) + '"')
        else:
            for w in re.findall(r"\w+", m.group(2)):
                terms.append(f'"{w}"*')
    return " AND ".join(terms)


class SearchIndex:
    """Přístup k hotovému indexu pro čtení (hledání, entity, čísla)."""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        path = index_path(output_dir)
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        self.conn = _connect(path)

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- informace --------------------------------------------------------
    def files(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT name, records, indexed_at FROM files ORDER BY name")]

    def total_records(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]

    # -- hledání ----------------------------------------------------------
    def search(self, text: str = "", files: Optional[Iterable[str]] = None,
               date_from: Optional[str] = None, date_to: Optional[str] = None,
               limit: int = 500, offset: int = 0) -> list[dict]:
        """Fulltext + volitelné filtry.

        * dotaz, který vypadá jako telefonní číslo → hledá se v tabulce
          `numbers` (libovolný tvar čísla), řazeno podle času,
        * jinak fulltext FTS5, řazeno podle relevance (bm25),
        * prázdný dotaz → všechny záznamy (dle filtrů), řazeno podle času.

        Výsledek: [{id, file, section, idx, row_key, dt, record, snippet}]
        """
        sql, params, q = self._query(text, files, date_from, date_to, count=False)
        if sql is None:
            return []
        sql += " LIMIT ? OFFSET ?"
        params += [int(limit), int(offset)]
        return [self._row(r) for r in self.conn.execute(sql, params)]

    def count(self, text: str = "", files: Optional[Iterable[str]] = None,
              date_from: Optional[str] = None, date_to: Optional[str] = None) -> int:
        """Počet záznamů odpovídajících dotazu (stejná pravidla jako search)."""
        sql, params, _q = self._query(text, files, date_from, date_to, count=True)
        if sql is None:
            return 0
        return self.conn.execute(sql, params).fetchone()[0]

    def _query(self, text, files, date_from, date_to, count: bool):
        """Sestaví SQL pro search/count → (sql | None, params, fts_query)."""
        text = (text or "").strip()
        nk = num_key(text) if text and _looks_like_number(text) else ""
        q = "" if nk else fts_query(text)
        if text and not q and not nk:
            return None, [], ""     # dotaz bez slov (jen interpunkce) – nic nehledat

        cols = ("COUNT(*)" if count else
                "r.id, f.name AS file, r.section, r.idx, r.row_key, r.dt, r.json")
        where, params = [], []
        if q:
            if not count:
                cols += ", snippet(records_fts, 0, '[', ']', '…', 12) AS snippet"
            # Přímo z FTS tabulky – SQLite pak umí řadit podle rank efektivně
            sql = (f"SELECT {cols} FROM records_fts "
                   "JOIN records r ON r.id = records_fts.rowid "
                   "JOIN files f ON f.id = r.file_id ")
            where.append("records_fts MATCH ?")
            params.append(q)
            order = "ORDER BY records_fts.rank"
        elif nk:
            sql = (f"SELECT {cols} FROM records r JOIN files f ON f.id = r.file_id ")
            where.append("r.id IN (SELECT record_id FROM numbers WHERE num_key = ?)")
            params.append(nk)
            order = "ORDER BY r.dt IS NULL, r.dt, r.id"
        else:
            sql = (f"SELECT {cols} FROM records r JOIN files f ON f.id = r.file_id ")
            order = "ORDER BY r.dt IS NULL, r.dt, r.id"
        if files:
            files = list(files)
            where.append("f.name IN (%s)" % ",".join("?" * len(files)))
            params += files
        if date_from:
            where.append("r.dt >= ?")
            params.append(date_from)
        if date_to:
            where.append("r.dt <= ?")
            params.append(date_to)
        if where:
            sql += "WHERE " + " AND ".join(where) + " "
        if not count:
            sql += order
        return sql, params, q

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = {k: r[k] for k in ("id", "file", "section", "idx", "row_key", "dt")}
        try:
            d["record"] = json.loads(r["json"])
        except ValueError:
            d["record"] = {}
        d["snippet"] = r["snippet"] if "snippet" in r.keys() else ""
        return d

    def by_number(self, number: str, limit: int = 1000) -> list[dict]:
        """Všechny záznamy obsahující dané telefonní číslo (v libovolném tvaru)."""
        k = num_key(number)
        if not k:
            return []
        sql = ("SELECT DISTINCT r.id, f.name AS file, r.section, r.idx, r.row_key, "
               "r.dt, r.json FROM numbers n JOIN records r ON r.id = n.record_id "
               "JOIN files f ON f.id = r.file_id WHERE n.num_key = ? "
               "ORDER BY r.dt IS NULL, r.dt LIMIT ?")
        return [self._row(r) for r in self.conn.execute(sql, (k, int(limit)))]

    def top_numbers(self, limit: int = 50) -> list[dict]:
        """Nejčastější čísla napříč zdroji: [{num_key, raw, count, files}]."""
        sql = ("SELECT n.num_key, MIN(n.raw) AS raw, COUNT(DISTINCT n.record_id) AS cnt, "
               "GROUP_CONCAT(DISTINCT f.name) AS files FROM numbers n "
               "JOIN records r ON r.id = n.record_id JOIN files f ON f.id = r.file_id "
               "GROUP BY n.num_key ORDER BY cnt DESC LIMIT ?")
        return [{"num_key": r[0], "raw": r[1], "count": r[2],
                 "files": (r[3] or "").split(",")}
                for r in self.conn.execute(sql, (int(limit),))]

    def entity_summary(self) -> dict[str, int]:
        """Počet unikátních entit podle druhu."""
        return {r[0]: r[1] for r in self.conn.execute(
            "SELECT kind, COUNT(DISTINCT norm) FROM entities GROUP BY kind")}

    def entities(self, kind: str, limit: int = 1000) -> list[dict]:
        """Unikátní entity daného druhu s počtem výskytů."""
        sql = ("SELECT norm, MIN(value) AS value, COUNT(DISTINCT record_id) AS cnt "
               "FROM entities WHERE kind = ? GROUP BY norm ORDER BY cnt DESC, norm LIMIT ?")
        return [{"norm": r[0], "value": r[1], "count": r[2]}
                for r in self.conn.execute(sql, (kind, int(limit)))]

    def by_entity(self, kind: str, norm: str, limit: int = 1000) -> list[dict]:
        sql = ("SELECT DISTINCT r.id, f.name AS file, r.section, r.idx, r.row_key, "
               "r.dt, r.json FROM entities e JOIN records r ON r.id = e.record_id "
               "JOIN files f ON f.id = r.file_id WHERE e.kind = ? AND e.norm = ? "
               "ORDER BY r.dt IS NULL, r.dt LIMIT ?")
        return [self._row(r) for r in self.conn.execute(sql, (kind, norm, int(limit)))]
