"""
core/ios_backup_crypto.py — dešifrování šifrovaných iTunes/Finder iOS záloh.

Pouze logická cesta (žádný jailbreak): uživatel zadá heslo zálohy, z keybagu
v Manifest.plist se odvodí klíče a dešifruje se Manifest.db i jednotlivé soubory.

Postup (iOS 10.2+):
  1. Manifest.plist → BackupKeyBag (TLV keybag), ManifestKey, IsEncrypted.
  2. Odvození klíče z hesla: PBKDF2-SHA256(heslo, DPSL, DPIC) → PBKDF2-SHA1(SALT, ITER).
  3. Odemčení class-klíčů v keybagu přes AES key-unwrap (RFC 3394). Když se žádný
     neodemkne, heslo je špatné (unwrap selže na integritní kontrole).
  4. ManifestKey → class + wrapped klíč → AES-CBC(IV=0) dešifruje Manifest.db.
  5. Každý soubor: jeho „file" blob (NSKeyedArchiver) nese ProtectionClass, Size a
     wrapped EncryptionKey → class klíč → AES-CBC(IV=0), oříznout na Size.

Kryptoprimitiva používá ověřená knihovna `cryptography` (AES-CBC, RFC 3394 unwrap)
a stdlib `hashlib.pbkdf2_hmac`. Nic se neimplementuje ručně.
"""
from __future__ import annotations

import hashlib
import plistlib
import struct
from typing import Optional

from cryptography.hazmat.primitives.keywrap import aes_key_unwrap, InvalidUnwrap
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_WRAP_PASSCODE = 2
_ZERO_IV = b"\x00" * 16


def _loop_tlv(blob: bytes):
    """Projde TLV blob keybagu: 4B tag, 4B délka (big-endian), hodnota."""
    i, n = 0, len(blob)
    while i + 8 <= n:
        tag = blob[i:i + 4].decode("ascii", "replace")
        length = struct.unpack(">I", blob[i + 4:i + 8])[0]
        val = blob[i + 8:i + 8 + length]
        i += 8 + length
        yield tag, val


def _aes_cbc_decrypt(key: bytes, data: bytes) -> bytes:
    dec = Cipher(algorithms.AES(key), modes.CBC(_ZERO_IV)).decryptor()
    return dec.update(data) + dec.finalize()


class Keybag:
    """Backup keybag z Manifest.plist → odemčení class-klíčů heslem zálohy."""

    def __init__(self, blob: bytes):
        self.attrs: dict = {}
        self.class_keys: dict[int, dict] = {}
        self.uuid: Optional[bytes] = None
        self.wrap: Optional[int] = None
        cur: Optional[dict] = None

        for tag, val in _loop_tlv(blob):
            if tag == "UUID" and self.uuid is None:
                self.uuid = val
            elif tag == "WRAP" and cur is None:
                self.wrap = struct.unpack(">I", val)[0]
            elif tag == "UUID":
                cur = {"UUID": val}
            elif tag == "CLAS" and cur is not None:
                cur["CLAS"] = struct.unpack(">I", val)[0]
            elif tag == "WRAP" and cur is not None:
                cur["WRAP"] = struct.unpack(">I", val)[0]
            elif tag == "KTYP" and cur is not None:
                cur["KTYP"] = struct.unpack(">I", val)[0]
            elif tag == "WPKY" and cur is not None:
                cur["WPKY"] = val
                if "CLAS" in cur:
                    self.class_keys[cur["CLAS"]] = cur
            elif tag in ("SALT", "DPSL", "HMCK"):
                self.attrs[tag] = val                       # surová sůl (bytes)
            elif tag in ("ITER", "DPIC"):
                self.attrs[tag] = struct.unpack(">I", val)[0]  # počet iterací (int)

    def unlock(self, password: bytes) -> bool:
        """Odemkne class-klíče heslem. Vrátí True při správném hesle."""
        if "SALT" not in self.attrs or "ITER" not in self.attrs:
            return False
        if "DPSL" in self.attrs and "DPIC" in self.attrs:
            key = hashlib.pbkdf2_hmac("sha256", password, self.attrs["DPSL"],
                                      self.attrs["DPIC"], 32)
        else:
            key = password
        passcode_key = hashlib.pbkdf2_hmac("sha1", key, self.attrs["SALT"],
                                           self.attrs["ITER"], 32)
        ok = False
        for ck in self.class_keys.values():
            wrapped = ck.get("WPKY")
            if not wrapped or not (ck.get("WRAP", 0) & _WRAP_PASSCODE):
                continue
            try:
                ck["KEY"] = aes_key_unwrap(passcode_key, wrapped)
                ok = True
            except InvalidUnwrap:
                pass
            except Exception:
                pass
        return ok

    def unwrap_key_for_class(self, protection_class: int, wrapped: bytes) -> bytes:
        ck = self.class_keys.get(protection_class)
        if not ck or "KEY" not in ck:
            raise ValueError(f"class key {protection_class} není dostupný")
        return aes_key_unwrap(ck["KEY"], wrapped)


def _parse_mbfile(blob: bytes):
    """Z NSKeyedArchiver „file" blobu vytáhne (ProtectionClass, wrapped_key, Size).
    Vrátí (None, None, size) když soubor nemá EncryptionKey (není šifrovaný)."""
    pl = plistlib.loads(blob)
    objs = pl["$objects"]
    root_uid = pl["$top"]["root"]
    root = objs[root_uid.data] if isinstance(root_uid, plistlib.UID) else objs[root_uid]
    size = int(root.get("Size", 0) or 0)
    prot = root.get("ProtectionClass")
    enc = root.get("EncryptionKey")
    if enc is None or prot is None:
        return None, None, size
    enc_obj = objs[enc.data] if isinstance(enc, plistlib.UID) else objs[enc]
    nsdata = enc_obj["NS.data"] if isinstance(enc_obj, dict) else enc_obj
    wrapped = bytes(nsdata)[4:]      # prvních 2 bajty/4 bajty = délka třídy → odstranit
    return int(prot), wrapped, size


class EncryptedBackup:
    """Dešifrování šifrované zálohy. Po vytvoření zkontroluj `password_ok`."""

    def __init__(self, backup_path: str, password: str):
        import os
        with open(os.path.join(backup_path, "Manifest.plist"), "rb") as f:
            self.manifest_plist = plistlib.load(f)
        self.backup_path = backup_path
        self.keybag = Keybag(self.manifest_plist["BackupKeyBag"])
        self.password_ok = self.keybag.unlock((password or "").encode("utf-8"))

    def decrypt_manifest_db(self, enc_bytes: bytes) -> bytes:
        mk = self.manifest_plist.get("ManifestKey")
        if not mk:
            raise ValueError("Manifest.plist nemá ManifestKey")
        clas = struct.unpack("<I", mk[:4])[0]
        key = self.keybag.unwrap_key_for_class(clas, mk[4:])
        return _aes_cbc_decrypt(key, enc_bytes)

    def decrypt_file(self, file_blob: bytes, enc_bytes: bytes) -> bytes:
        prot, wrapped, size = _parse_mbfile(file_blob)
        if prot is None or wrapped is None:
            return enc_bytes[:size] if size else enc_bytes   # nešifrovaný soubor
        key = self.keybag.unwrap_key_for_class(prot, wrapped)
        dec = _aes_cbc_decrypt(key, enc_bytes)
        return dec[:size] if size else dec
