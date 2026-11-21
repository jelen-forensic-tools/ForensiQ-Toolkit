"""
core/adump/abx.py — Android Binary XML (ABX, Android 12+) and plain XML loader.

System files such as /data/system/packages.xml or users/0.xml are stored as
ABX ("ABX\\0" magic) on newer Android. `parse_xml(bytes)` returns an
xml.etree.ElementTree.Element for both formats.
"""
from __future__ import annotations

import base64
import struct
import xml.etree.ElementTree as ET

MAGIC = b"ABX\x00"

START_DOCUMENT, END_DOCUMENT, START_TAG, END_TAG, TEXT = 0, 1, 2, 3, 4
CDSECT, ENTITY_REF, IGNORABLE_WS, PROC_INSTR, COMMENT, DOCDECL, ATTRIBUTE = 5, 6, 7, 8, 9, 10, 15

T_NULL, T_STRING, T_INTERNED, T_HEX, T_B64 = 1, 2, 3, 4, 5
T_INT, T_INT_HEX, T_LONG, T_LONG_HEX, T_FLOAT, T_DOUBLE, T_TRUE, T_FALSE = 6, 7, 8, 9, 10, 11, 12, 13


class _Reader:
    def __init__(self, b: bytes):
        self.b = b
        self.o = 4
        self.pool: list[str] = []

    def u8(self):
        v = self.b[self.o]
        self.o += 1
        return v

    def unpack(self, fmt):
        v = struct.unpack_from(fmt, self.b, self.o)
        self.o += struct.calcsize(fmt)
        return v[0]

    def string(self) -> str:
        n = self.unpack(">H")
        s = self.b[self.o:self.o + n].decode("utf-8", "replace")
        self.o += n
        return s

    def interned(self) -> str:
        i = self.unpack(">H")
        if i == 0xFFFF:
            s = self.string()
            self.pool.append(s)
            return s
        return self.pool[i]

    def value(self, typ: int) -> str:
        if typ == T_NULL:
            return ""
        if typ == T_STRING:
            return self.string()
        if typ == T_INTERNED:
            return self.interned()
        if typ in (T_HEX, T_B64):
            n = self.unpack(">H")
            raw = self.b[self.o:self.o + n]
            self.o += n
            return raw.hex() if typ == T_HEX else base64.b64encode(raw).decode()
        if typ == T_INT:
            return str(self.unpack(">i"))
        if typ == T_INT_HEX:
            return format(self.unpack(">I"), "x")
        if typ == T_LONG:
            return str(self.unpack(">q"))
        if typ == T_LONG_HEX:
            return format(self.unpack(">Q"), "x")
        if typ == T_FLOAT:
            return repr(self.unpack(">f"))
        if typ == T_DOUBLE:
            return repr(self.unpack(">d"))
        if typ == T_TRUE:
            return "true"
        if typ == T_FALSE:
            return "false"
        raise ValueError(f"ABX: unknown type {typ}")


def parse_abx(b: bytes) -> ET.Element:
    r = _Reader(b)
    root = ET.Element("_document")
    stack = [root]
    n = len(b)
    while r.o < n:
        tok = r.u8()
        cmd, typ = tok & 0x0F, tok >> 4
        if cmd == START_DOCUMENT:
            continue
        if cmd == END_DOCUMENT:
            break
        if cmd == START_TAG:
            el = ET.SubElement(stack[-1], r.interned())
            stack.append(el)
        elif cmd == END_TAG:
            r.interned()
            if len(stack) > 1:
                stack.pop()
        elif cmd == ATTRIBUTE:
            name = r.interned()
            stack[-1].set(name, r.value(typ))
        elif cmd in (TEXT, CDSECT, ENTITY_REF, IGNORABLE_WS, PROC_INSTR, COMMENT, DOCDECL):
            txt = r.value(typ) if typ != T_NULL else ""
            if cmd in (TEXT, CDSECT, ENTITY_REF):
                el = stack[-1]
                if len(el):
                    el[-1].tail = (el[-1].tail or "") + txt
                else:
                    el.text = (el.text or "") + txt
        else:
            raise ValueError(f"ABX: unknown token {cmd}")
    return root[0] if len(root) else root


def parse_xml(b: bytes) -> ET.Element:
    if b[:4] == MAGIC:
        return parse_abx(b)
    return ET.fromstring(b)
