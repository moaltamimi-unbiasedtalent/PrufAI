from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Protocol


@dataclass(frozen=True)
class ParsedPage:
    page_number: int
    text_content: str
    normalized_text: str
    content_hash: str
    character_count: int
    extraction_status: str
    heading: Optional[str] = None


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value.replace("\r\n", "\n").replace("\r", "\n"))
    value = value.replace("\x00", "")
    return re.sub(r"[ \t]+", " ", value).strip()


def page_from_text(page_number: int, text: str) -> ParsedPage:
    normalized = normalize_text(text)
    return ParsedPage(
        page_number=page_number,
        text_content=text,
        normalized_text=normalized,
        content_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        character_count=len(normalized),
        extraction_status="EXTRACTED" if normalized else "EMPTY",
    )


class DocumentParser(Protocol):
    name: str
    version: str

    def can_parse(self, mime_type: str) -> bool: ...

    def parse(self, content: bytes) -> list[ParsedPage]: ...


class PlainTextParser:
    name = "plain-text"
    version = "1.0"

    def can_parse(self, mime_type: str) -> bool:
        return mime_type in {"text/plain", "text/markdown"}

    def parse(self, content: bytes) -> list[ParsedPage]:
        return [page_from_text(1, content.decode("utf-8", errors="strict"))]


class PdfParser:
    name = "pypdf"
    version = "1.0"

    def can_parse(self, mime_type: str) -> bool:
        return mime_type == "application/pdf"

    def parse(self, content: bytes) -> list[ParsedPage]:
        from io import BytesIO

        from pypdf import PdfReader

        reader = PdfReader(BytesIO(content))
        return [
            page_from_text(index + 1, page.extract_text() or "")
            for index, page in enumerate(reader.pages)
        ]


class ParserRegistry:
    def __init__(self, parsers: Optional[List[DocumentParser]] = None) -> None:
        self.parsers = parsers or [PdfParser(), PlainTextParser()]

    def for_mime(self, mime_type: str) -> DocumentParser:
        for parser in self.parsers:
            if parser.can_parse(mime_type):
                return parser
        raise ValueError("no parser is registered for this MIME type")
