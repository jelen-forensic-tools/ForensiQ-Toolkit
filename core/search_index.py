"""
core/search_index.py – full-text index over extraction outputs.

Indexes every *.json in the root of an output folder into
`<output_dir>/_case/index.sqlite`:

  * records      – one row = one record (SMS, call, contact, file…), with a
                   reference to the source file, its position and a stable
                   `row_key` (SHA-1 of the canonical JSON) for tagging,
  * records_fts  – SQLite FTS5 (unicode61, remove_diacritics 2) → search that
                   ignores accents and case, with prefix matching; works for
                   every script that separates words with spaces (Latin,
                   Cyrillic, Greek, Arabic, Hebrew…),
  * records_tri  – FTS5 trigram table for records in scripts WITHOUT spaces
                   between words (Chinese, Japanese, Korean, Thai, Lao,
                   Khmer, Burmese) → substring search,
  * numbers      – phone numbers, country-independent: normalised to the
                   international form (no +/00, no national trunk 0); two
                   numbers match when one is the ending of the other
                   (min. 7 digits) → +44 7700 900123 = 07700 900123,
                   +420 777 123 456 = 777123456, +45 12345678 = 12345678,
  * entities     – e-mails, URLs, IBANs (all countries), crypto addresses,
                   IMEIs, payment cards, IPv4 + regional ones (CZ/SK birth
                   numbers, CZ licence plates).

The index is incremental: files are re-indexed only when they change (size,
mtime, SHA-256); deleted files are removed from the index. The `_case/`
folder is analyst work product and is excluded from the evidence manifest
(core/evidence.py).

Standard library only. Call from a background thread – every call opens its
own database connection.
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
SCHEMA_VERSION = 2

# Files in the output folder that are not device data (not indexed).
_SKIP_FILES = {"evidence_manifest.json"}

# Keys whose value is a phone number (or a list of numbers).
_NUMBER_KEYS = re.compile(
    r"(^|_)(number|numbers|phone|phones|tel|msisdn|address|caller|callee|"
    r"from|to|sender|recipient|remote|handle|jid)($|_)", re.I)
# Date keys – the first one found (in this order) becomes the record time.
_DATE_KEYS = ("date", "time", "timestamp", "datetime", "date_taken",
              "date_sent", "date_received", "start", "created", "modified",
              "last_time_used", "last_visit")

_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y:%m:%d %H:%M:%S",
                 "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d",
                 "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y",
                 "%Y-%m-%d", "%Y%m%d")

# Scripts that do not separate words with spaces → trigram index.
# (CJK, kana, hangul, Thai, Lao, Burmese, Khmer)
_NOSPACE_RE = re.compile(
    "[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯"
    "฀-๿຀-໿က-႟ក-៿]")

_RE_ISO_DT = re.compile(r"(19|20)\d\d-[01]\d-[0-3]\d [0-2]\d:[0-5]\d:[0-5]\d")

MIN_NUMBER_SUFFIX = 7       # min. length of the common ending of two numbers
_CHUNK = 1 << 20

# Letters that the unicode61 tokenizer does not split into base + diacritic
_FOLD_MAP = str.maketrans({
    "ß": "ss", "ẞ": "SS", "æ": "ae", "Æ": "AE", "ø": "o", "Ø": "O", "œ": "oe",
    "Œ": "OE", "ð": "d", "Ð": "D", "þ": "th", "Þ": "TH", "ł": "l", "Ł": "L",
    "đ": "d", "Đ": "D", "ħ": "h", "Ħ": "H", "ı": "i", "ŀ": "l", "Ŀ": "L",
    "ŧ": "t", "Ŧ": "T", "ĸ": "k", "ŋ": "ng", "Ŋ": "NG",
})


# Text contains characters that unicode61 (remove_diacritics) does not fold:
# the special letters above + Greek accents (tonos, dialytika, polytonic)
_NEEDS_FOLD = re.compile("[" + "".join(chr(c) for c in _FOLD_MAP) +
                         "Ͱ-Ͽἀ-῿]")


def needs_fold(s: str) -> bool:
    return bool(s) and not s.isascii() and _NEEDS_FOLD.search(s) is not None


def fold_text(s: str) -> str:
    """Remove diacritics even where the tokenizer cannot (ß, ø, ł, Greek
    accents…). Used for an additional copy of the text in the index and for
    the query, so "strasse" finds "Straße" and "Αθηνα" finds "Αθήνα"."""
    import unicodedata
    s = s.translate(_FOLD_MAP)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    # NFC recomposes Korean syllables (NFKD splits them into jamo)
    return unicodedata.normalize("NFC", s)


# ── Phone numbers (country-independent) ──────────────────────────────────────

def norm_number(raw) -> str:
    """Normalise a phone number to digits in international form.

    * leading '+' or '00' = international prefix → only the prefix is removed,
    * a single leading '0' = national (trunk) prefix (UK, DE, FR, IT…) → removed,
    * otherwise the number is taken as is (CZ/SK/US without a prefix).

    Results are compared by their common ending (see numbers_match), so the
    national and international forms of a number match in any country.
    Returns '' if the value has fewer than 3 digits.
    """
    s = str(raw or "").strip()
    d = re.sub(r"[^0-9]", "", s)
    if len(d) < 3:
        return ""
    if s.startswith("+"):
        n = d
    elif d.startswith("00"):
        n = d[2:]
    elif d.startswith("0"):
        n = d[1:]
    else:
        n = d
    return n if len(n) >= 3 else ""


def numbers_match(a: str, b: str) -> bool:
    """Do two normalised numbers match? (ending ≥ MIN_NUMBER_SUFFIX, or equality)"""
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= MIN_NUMBER_SUFFIX and long_.endswith(short)


def _looks_like_number(v: str) -> bool:
    """The value looks like a phone number (not text, an e-mail or a date)."""
    v = v.strip()
    if not v or len(v) > 32 or "@" in v:
        return False
    if not re.fullmatch(r"[+()\d\s\-./*#]+", v):
        return False
    digits = re.sub(r"\D", "", v)
    if not 3 <= len(digits) <= 15:
        return False
    # A date / time such as 2025-01-01 or 12.03.2025 – not a number
    if re.fullmatch(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}.*", v) or \
            re.fullmatch(r"\d{1,2}[./]\d{1,2}[./]\d{2,4}", v):
        return False
    return True


def parse_dt(value) -> Optional[str]:
    """Convert a date value to ISO 'YYYY-MM-DD HH:MM:SS' (or None)."""
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
    if _RE_ISO_DT.fullmatch(s):          # fast path – the most common format
        return s
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

# Entity kinds; region = None → international, otherwise a regional detector.
ENTITY_KINDS: dict[str, Optional[str]] = {
    "email": None, "url": None, "iban": None, "crypto": None,
    "imei": None, "card": None, "ip": None,
    "rc": "CZ/SK", "spz": "CZ",
}

_RE_EMAIL = re.compile(r"\b[\w.%+\-]+@[\w\-]+(?:\.[\w\-]+)*\.[^\W\d_]{2,24}\b")
_RE_URL = re.compile(r"\b(?:https?://|www\.)[^\s\"'<>]{3,}", re.I)
_RE_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,8}(?: ?[A-Z0-9]{1,4})?\b")
_RE_BTC = re.compile(r"\b(?:bc1[ac-hj-np-z02-9]{11,71}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b")
_RE_ETH = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_RE_IMEI_DASHED = re.compile(r"\b\d{2}-\d{6}-\d{6}-\d\b")
_RE_IMEI = re.compile(r"\bIMEI\s*(?:\d\s*)?[:=#]?\s*(\d{15})\b|\b(\d{2}-\d{6}-\d{6}-\d)\b", re.I)
# A card must not touch a letter/digit (otherwise it would be found inside hashes)
_RE_CARD = re.compile(r"(?<![\w\-])\d[\d \-]{11,22}\d(?![\w\-])")   # no backtracking
# Cheap pre-filters – a detector runs only if the text can contain the entity
_PRE_LONG_DIGITS = re.compile(r"\d[\d \-]{11,}\d")
_PRE_UPPER_DIGIT = re.compile(r"[A-Z]{2}\d{2}")
_PRE_IP = re.compile(r"\d\.\d")
_PRE_SPZ = re.compile(r"[1-9][A-Z]")
_RE_IPV4 = re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])")
_RE_RC = re.compile(r"\b(\d{2})(\d{2})(\d{2}) ?/ ?(\d{3,4})\b")
# Czech licence plate: digit + 2 characters + 4 digits; G, O, Q, W are not used
# (this also excludes false hits such as "5GB 1024").
_RE_SPZ = re.compile(r"\b([1-9][A-FH-NPR-VX-Z][A-FH-NPR-VX-Z0-9]) ?(\d{4})\b")
_RE_INTL_PHONE = re.compile(r"(?<![\w+])\+\d[\d \-]{7,17}\d\b")

# IBAN lengths per country (SWIFT registry)
_IBAN_LEN = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16,
    "BG": 22, "BH": 22, "BI": 27, "BR": 29, "BY": 28, "CH": 21, "CR": 22,
    "CY": 28, "CZ": 24, "DE": 22, "DJ": 27, "DK": 18, "DO": 28, "EE": 20,
    "EG": 29, "ES": 24, "FI": 18, "FK": 18, "FO": 18, "FR": 27, "GB": 22,
    "GE": 22, "GI": 23, "GL": 18, "GR": 27, "GT": 28, "HR": 21, "HU": 28,
    "IE": 22, "IL": 23, "IQ": 23, "IS": 26, "IT": 27, "JO": 30, "KW": 30,
    "KZ": 20, "LB": 28, "LC": 32, "LI": 21, "LT": 20, "LU": 20, "LV": 21,
    "LY": 25, "MC": 27, "MD": 24, "ME": 22, "MK": 19, "MN": 20, "MR": 27,
    "MT": 31, "MU": 30, "NI": 28, "NL": 18, "NO": 15, "OM": 23, "PK": 24,
    "PL": 28, "PS": 29, "PT": 25, "QA": 29, "RO": 24, "RS": 22, "RU": 33,
    "SA": 24, "SC": 31, "SD": 18, "SE": 24, "SI": 19, "SK": 24, "SM": 27,
    "SO": 23, "ST": 25, "SV": 28, "TL": 23, "TN": 24, "TR": 26, "UA": 29,
    "VA": 22, "VG": 24, "XK": 20, "YE": 30,
}


def _iban_ok(s: str) -> bool:
    s = s.replace(" ", "").upper()
    exp = _IBAN_LEN.get(s[:2])
    if exp is None or len(s) != exp:
        return False
    num = "".join(str(int(c, 36)) for c in s[4:] + s[:4])
    return int(num) % 97 == 1


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _card_ok(d: str) -> bool:
    """Payment card: length + IIN prefix (Visa, Mastercard, Amex, …) + Luhn."""
    n = len(d)
    if not 13 <= n <= 19 or len(set(d)) < 4:
        return False
    p2, p4 = int(d[:2]), int(d[:4])
    iin = (d[0] == "4" and n in (13, 16, 19)                 # Visa
           or (51 <= p2 <= 55 or 2221 <= p4 <= 2720) and n == 16   # Mastercard
           or p2 in (34, 37) and n == 15                     # Amex
           or (d.startswith("6011") or d.startswith("65")) and n in (16, 19)  # Discover
           or 3528 <= p4 <= 3589 and n in (16, 19)           # JCB
           or p2 == 62 and 16 <= n <= 19                      # UnionPay
           or d[:2] in ("50", "56", "57", "58", "67") and 12 < n <= 19)  # Maestro
    return bool(iin) and _luhn_ok(d)


def _rc_ok(m: re.Match) -> bool:
    """CZ/SK birth number: valid date + divisibility by 11 (10-digit numbers)."""
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
        return yy < 54          # 9-digit birth numbers exist only up to 1953
    full = int(m.group(1) + m.group(2) + m.group(3) + tail)
    if full % 11 == 0:
        return True
    # Exception: remainder 10 → check digit 0
    base = int(m.group(1) + m.group(2) + m.group(3) + tail[:3])
    return base % 11 == 10 and tail[3] == "0"


def extract_entities(text: str, kinds: Optional[set] = None) -> list[tuple[str, str, str]]:
    """Return [(kind, value, norm)] of the entities found in the text.

    kinds – limits the detectors (e.g. without regional ones); None = all.
    The 'phone' kind (international number in text) is always returned.
    """
    out: list[tuple[str, str, str]] = []
    if not text:
        return out

    def on(k):
        return kinds is None or k in kinds

    long_digits = _PRE_LONG_DIGITS.search(text) is not None
    upper_digit = _PRE_UPPER_DIGIT.search(text) is not None

    if on("email") and "@" in text:
        for m in _RE_EMAIL.finditer(text):
            v = m.group(0).strip(".")
            out.append(("email", v, v.lower()))
    if on("url") and ("://" in text or "www." in text or "WWW." in text):
        for m in _RE_URL.finditer(text):
            v = m.group(0).rstrip(".,;:)]}")
            norm = re.sub(r"^(https?://)?(www\.)?", "", v.lower()).rstrip("/")
            out.append(("url", v, norm))
    if on("iban") and upper_digit:
        for m in _RE_IBAN.finditer(text):
            # The regex may also capture a following digit group (e.g. a year) –
            # try the longest variant first and drop characters from the right.
            v = m.group(0)
            while v:
                if _iban_ok(v):
                    out.append(("iban", v, v.replace(" ", "").upper()))
                    break
                v = v[:-1].rstrip()
    if on("crypto"):
        for m in _RE_BTC.finditer(text):
            v = m.group(0)
            # base58 without digits/upper/lower case letters is more likely an ordinary word
            if v.startswith("bc1") or (re.search(r"\d", v) and re.search(r"[A-Z]", v)
                                       and re.search(r"[a-z]", v)):
                out.append(("crypto", v, v))
        for m in _RE_ETH.finditer(text):
            out.append(("crypto", m.group(0), m.group(0).lower()))
    if on("imei") and long_digits and ("imei" in text.lower() or _RE_IMEI_DASHED.search(text)):
        for m in _RE_IMEI.finditer(text):
            d = re.sub(r"\D", "", m.group(1) or m.group(2))
            if len(d) == 15 and _luhn_ok(d):
                out.append(("imei", d, d))
    if on("card") and long_digits:
        for m in _RE_CARD.finditer(text):
            d = re.sub(r"\D", "", m.group(0))
            if 13 <= len(d) <= 19 and _card_ok(d):
                out.append(("card", m.group(0).strip(), d))
    if on("ip") and _PRE_IP.search(text):
        for m in _RE_IPV4.finditer(text):
            v = m.group(0)
            octets = v.split(".")
            # "1.2.3.4" and similar are usually version numbers, not addresses
            if all(len(o) == 1 for o in octets) or octets[0] == "0":
                continue
            out.append(("ip", v, v))
    if on("rc") and "/" in text:
        for m in _RE_RC.finditer(text):
            if _rc_ok(m):
                norm = m.group(1) + m.group(2) + m.group(3) + "/" + m.group(4)
                out.append(("rc", m.group(0), norm))
    if on("spz") and _PRE_SPZ.search(text):
        for m in _RE_SPZ.finditer(text):
            out.append(("spz", m.group(0), m.group(1) + m.group(2)))
    for m in (_RE_INTL_PHONE.finditer(text) if "+" in text else ()):
        n = norm_number(m.group(0))
        if len(n) >= 8:
            out.append(("phone", m.group(0), n))
    return out


# ── Splitting JSON into records ──────────────────────────────────────────────

def _is_record_list(v) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v[:50])


def iter_records(data) -> Iterator[tuple[str, int, dict]]:
    """Split the content of a JSON file into (section, index, record).

    * list of dicts              → every item is a record (section "")
    * dict with lists of records → records from each list (section = key),
                                   remaining scalar values as one record
    * any other dict             → one record (e.g. device_info)
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
    """(key, scalar value) for every leaf of a nested structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _flatten(v, str(k))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _flatten(v, key)
    elif obj is not None and obj != "":
        yield key, obj


def row_key(rec: dict) -> str:
    """Stable record identifier (SHA-1 of the canonical JSON)."""
    canon = json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()


def analyze_record(rec: dict, kinds: Optional[set] = None
                   ) -> tuple[str, Optional[str], list, list]:
    """Return (fts_text, dt, [(norm_number, raw)], [(kind, value, norm)])."""
    parts: list[str] = []
    numbers: dict[str, str] = {}
    dt: Optional[str] = None
    date_rank = len(_DATE_KEYS)
    for k, v in _flatten(rec):
        if isinstance(v, bool):
            continue
        sv = str(v)
        kl = k.lower()
        if kl == "path" or kl.endswith("_path"):
            parts.append(sv.replace("/", " ").replace("\\", " "))
        parts.append(sv)
        if kl in _DATE_KEYS and _DATE_KEYS.index(kl) < date_rank:
            p = parse_dt(v)
            if p:
                dt, date_rank = p, _DATE_KEYS.index(kl)
        if _NUMBER_KEYS.search(kl) and isinstance(v, (str, int)) and \
                _looks_like_number(sv):
            n = norm_number(sv)
            if n:
                numbers.setdefault(n, sv)
    text = "\n".join(parts)
    entities = extract_entities(text, kinds)
    for kind, raw, norm in entities:
        if kind == "phone":
            numbers.setdefault(norm, raw)
    entities = [e for e in entities if e[0] != "phone"]
    # Additional "folded" copy of the text for letters the tokenizer cannot split
    if needs_fold(text):
        text += "\n" + fold_text(text)
    # Normalised numbers in the FTS text → searching digits also finds formatted forms
    if numbers:
        text += "\n" + " ".join(numbers)
    return text, dt, list(numbers.items()), list(dict.fromkeys(entities))


# ── Database ─────────────────────────────────────────────────────────────────

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
CREATE VIRTUAL TABLE IF NOT EXISTS records_tri USING fts5(
    text, tokenize = 'trigram remove_diacritics 1');
CREATE TABLE IF NOT EXISTS numbers(
    record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    norm TEXT NOT NULL,      -- normalised digits (international form)
    rev TEXT NOT NULL,       -- norm reversed → ending match = prefix match
    raw TEXT
);
CREATE INDEX IF NOT EXISTS ix_numbers_rev ON numbers(rev);
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


def index_exists(output_dir: str) -> bool:
    return os.path.isfile(index_path(output_dir))


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
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
        # Older schema – the index is derived data, so simply rebuild it
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
    """Data JSON files in the root of the output folder (sorted)."""
    try:
        names = os.listdir(output_dir)
    except OSError:
        return []
    return sorted(n for n in names
                  if n.lower().endswith(".json") and n not in _SKIP_FILES
                  and os.path.isfile(os.path.join(output_dir, n)))


def _delete_file_rows(conn: sqlite3.Connection, file_id: int) -> None:
    sub = "(SELECT id FROM records WHERE file_id=?)"
    conn.execute(f"DELETE FROM records_fts WHERE rowid IN {sub}", (file_id,))
    conn.execute(f"DELETE FROM records_tri WHERE rowid IN {sub}", (file_id,))
    conn.execute(f"DELETE FROM numbers WHERE record_id IN {sub}", (file_id,))
    conn.execute(f"DELETE FROM entities WHERE record_id IN {sub}", (file_id,))
    conn.execute("DELETE FROM records WHERE file_id=?", (file_id,))


def build_index(output_dir: str,
                progress: Optional[Callable[[int, int, str], None]] = None,
                cancel: Optional[Callable[[], bool]] = None,
                force: bool = False,
                kinds: Optional[set] = None) -> dict:
    """Create / update the index. Returns statistics.

    progress(done, total, filename) is called after every file.
    cancel() → True stops indexing (files already processed are kept).
    kinds – allowed entity kinds (None = all, see ENTITY_KINDS).
    """
    stats = {"files": 0, "indexed": 0, "unchanged": 0, "removed": 0,
             "records": 0, "errors": [], "cancelled": False}
    conn = _open_db(output_dir)
    try:
        names = list_source_files(output_dir)
        stats["files"] = len(names)
        known = {r["name"]: r for r in conn.execute("SELECT * FROM files")}

        # Files that are no longer on disk
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
                                row["id"] if row is not None else None, kinds)
                stats["indexed"] += 1
                stats["records"] += n
            except Exception as e:      # a corrupt file must not stop the rest
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
                st: os.stat_result, sha: str, file_id: Optional[int],
                kinds: Optional[set]) -> int:
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
        text, dt, numbers, entities = analyze_record(rec, kinds)
        rj = json.dumps(rec, ensure_ascii=False, default=str)
        cur = conn.execute(
            "INSERT INTO records(file_id, section, idx, row_key, dt, json) "
            "VALUES(?,?,?,?,?,?)",
            (file_id, section, idx, row_key(rec), dt, rj))
        rid = cur.lastrowid
        conn.execute("INSERT INTO records_fts(rowid, text) VALUES(?,?)", (rid, text))
        if _NOSPACE_RE.search(text):
            conn.execute("INSERT INTO records_tri(rowid, text) VALUES(?,?)", (rid, text))
        if numbers:
            conn.executemany("INSERT INTO numbers VALUES(?,?,?,?)",
                             [(rid, n, n[::-1], raw) for n, raw in numbers])
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
    """Convert user input into a safe FTS5 query (unicode61 tokenizer).

    Words are combined with AND, each as a prefix ("nov" finds "Novák").
    Text in quotes is a phrase. FTS special characters are not interpreted.
    """
    terms: list[str] = []
    text = text or ""
    if needs_fold(text):
        text = fold_text(text)
    for m in re.finditer(r'"([^"]+)"|(\S+)', text):
        if m.group(1):
            words = re.findall(r"\w+", m.group(1))
            if words:
                terms.append('"' + " ".join(words) + '"')
        else:
            for w in re.findall(r"\w+", m.group(2)):
                terms.append(f'"{w}"*')
    return " AND ".join(terms)


def _tri_terms(text: str) -> list[str]:
    """Search strings for the trigram index (a quoted phrase = one string)."""
    out = []
    for m in re.finditer(r'"([^"]+)"|(\S+)', text or ""):
        s = (m.group(1) or m.group(2)).strip()
        if s:
            out.append(s)
    return out


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _number_condition(n: str) -> tuple[str, list]:
    """SQL condition on the numbers table (alias n) matching the ending of number n."""
    rev = n[::-1]
    if len(n) < MIN_NUMBER_SUFFIX:
        return "n.rev = ?", [rev]
    # stored number is longer or equal (ends with n) → rev starts with rev(n)
    conds, params = ["(n.rev >= ? AND n.rev < ?)"], [rev, rev + ":"]
    # stored number is shorter (n ends with it) → rev is a prefix of rev(n)
    prefixes = [rev[:k] for k in range(MIN_NUMBER_SUFFIX, len(rev))]
    if prefixes:
        conds.append("n.rev IN (%s)" % ",".join("?" * len(prefixes)))
        params += prefixes
    return "(" + " OR ".join(conds) + ")", params


class SearchIndex:
    """Read access to a built index (search, entities, numbers)."""

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

    # -- search ----------------------------------------------------------
    def search(self, text: str = "", files: Optional[Iterable[str]] = None,
               date_from: Optional[str] = None, date_to: Optional[str] = None,
               limit: int = 500, offset: int = 0) -> list[dict]:
        """Full-text search + optional filters.

        * a query that looks like a phone number → searched in the `numbers`
          table (any format, any country), ordered by time,
        * a query in a script without spaces (CJK, Thai…) → trigram index,
        * otherwise FTS5 full text, ordered by relevance (bm25),
        * empty query → all records (by filters), ordered by time.

        Result: [{id, file, section, idx, row_key, dt, record, snippet}]
        """
        sql, params = self._query(text, files, date_from, date_to, count=False)
        if sql is None:
            return []
        sql += " LIMIT ? OFFSET ?"
        params += [int(limit), int(offset)]
        return [self._row(r) for r in self.conn.execute(sql, params)]

    def count(self, text: str = "", files: Optional[Iterable[str]] = None,
              date_from: Optional[str] = None, date_to: Optional[str] = None) -> int:
        """Number of records matching the query (same rules as search)."""
        sql, params = self._query(text, files, date_from, date_to, count=True)
        if sql is None:
            return 0
        return self.conn.execute(sql, params).fetchone()[0]

    def _query(self, text, files, date_from, date_to, count: bool):
        """Build the SQL for search/count → (sql | None, params)."""
        text = (text or "").strip()
        num = norm_number(text) if text and _looks_like_number(text) else ""
        tri = bool(text) and not num and bool(_NOSPACE_RE.search(text))
        q = "" if (num or tri) else fts_query(text)
        if text and not (q or num or tri):
            return None, []         # query without words (punctuation only) – search nothing

        cols = ("COUNT(*)" if count else
                "r.id, f.name AS file, r.section, r.idx, r.row_key, r.dt, r.json")
        where, params = [], []
        order = "ORDER BY r.dt IS NULL, r.dt, r.id"
        if q:
            if not count:
                cols += ", snippet(records_fts, 0, '[', ']', '…', 12) AS snippet"
            # Straight from the FTS table – SQLite can then order by rank efficiently
            sql = (f"SELECT {cols} FROM records_fts "
                   "JOIN records r ON r.id = records_fts.rowid "
                   "JOIN files f ON f.id = r.file_id ")
            where.append("records_fts MATCH ?")
            params.append(q)
            order = "ORDER BY records_fts.rank"
        elif tri:
            terms = _tri_terms(text)
            if all(len(s) >= 3 for s in terms):
                if not count:
                    cols += ", snippet(records_tri, 0, '[', ']', '…', 12) AS snippet"
                where.append("records_tri MATCH ?")
                params.append(" AND ".join('"' + s.replace('"', '""') + '"' for s in terms))
                order = "ORDER BY records_tri.rank"
            else:
                # Trigram needs ≥3 characters – shorter terms (e.g. 2-character CJK words) via LIKE
                for s in terms:
                    where.append("records_tri.text LIKE ? ESCAPE '\\'")
                    params.append("%" + _like_escape(s) + "%")
            sql = (f"SELECT {cols} FROM records_tri "
                   "JOIN records r ON r.id = records_tri.rowid "
                   "JOIN files f ON f.id = r.file_id ")
        elif num:
            cond, p = _number_condition(num)
            sql = f"SELECT {cols} FROM records r JOIN files f ON f.id = r.file_id "
            where.append(f"r.id IN (SELECT n.record_id FROM numbers n WHERE {cond})")
            params += p
        else:
            sql = f"SELECT {cols} FROM records r JOIN files f ON f.id = r.file_id "
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
        return sql, params

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
        """All records containing the given phone number (in any format)."""
        n = norm_number(number)
        if not n:
            return []
        cond, params = _number_condition(n)
        sql = ("SELECT r.id, f.name AS file, r.section, r.idx, r.row_key, r.dt, r.json "
               "FROM records r JOIN files f ON f.id = r.file_id "
               f"WHERE r.id IN (SELECT n.record_id FROM numbers n WHERE {cond}) "
               "ORDER BY r.dt IS NULL, r.dt LIMIT ?")
        return [self._row(r) for r in self.conn.execute(sql, params + [int(limit)])]

    def top_numbers(self, limit: int = 100) -> list[dict]:
        """Most frequent numbers across sources: [{number, raw, count, files}].

        Different forms of the same number (national/international) are merged
        by their common ending. `number` is the most complete (longest) form.
        """
        rows = self.conn.execute(
            "SELECT n.norm, n.raw, n.record_id, f.name FROM numbers n "
            "JOIN records r ON r.id = n.record_id JOIN files f ON f.id = r.file_id"
        ).fetchall()
        if not rows:
            return []
        norms = {r[0] for r in rows}
        parent = {n: n for n in norms}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        # Longer numbers that end with the given (shorter) number
        longer: dict[str, list[str]] = {}
        for n in norms:
            for k in range(MIN_NUMBER_SUFFIX, len(n)):
                suf = n[-k:]
                if suf in parent:
                    longer.setdefault(suf, []).append(n)
        # A shorter form is merged only if UNAMBIGUOUS: all longer candidates must be
        # forms of one number (endings of the longest one). An ambiguous short
        # number (e.g. 12345678 vs. +45 12345678 and +82 10 1234 5678) stays
        # on its own – different foreign numbers are never merged through it.
        for short, cands in longer.items():
            top = max(cands, key=len)
            if all(top.endswith(c) for c in cands):
                for c in cands + [short]:
                    a, b = find(top), find(c)
                    if a != b:
                        parent[b] = a
        groups: dict[str, dict] = {}
        for norm, raw, rid, fname in rows:
            g = groups.setdefault(find(norm), {"norms": set(), "raws": {}, "recs": set(),
                                               "files": set()})
            g["norms"].add(norm)
            g["raws"][norm] = raw
            g["recs"].add(rid)
            g["files"].add(fname)
        out = []
        for g in groups.values():
            best = max(g["norms"], key=lambda x: (len(x), x))
            out.append({"number": best, "raw": g["raws"].get(best, best),
                        "count": len(g["recs"]), "files": sorted(g["files"]),
                        "variants": sorted(g["norms"])})
        out.sort(key=lambda x: (-x["count"], x["number"]))
        return out[:limit]

    def entity_summary(self) -> dict[str, int]:
        """Number of unique entities per kind."""
        return {r[0]: r[1] for r in self.conn.execute(
            "SELECT kind, COUNT(DISTINCT norm) FROM entities GROUP BY kind")}

    def entities(self, kind: str, limit: int = 1000) -> list[dict]:
        """Unique entities of a kind with their number of occurrences."""
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
