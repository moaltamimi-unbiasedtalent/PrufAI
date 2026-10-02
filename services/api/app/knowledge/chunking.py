from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable, Optional

CHUNKER_NAME = "prufai_page_chunker"
CHUNKER_VERSION = "1.0"


@dataclass(frozen=True)
class ChunkDraft:
    page_number: int
    chunk_index: int
    text_content: str
    content_hash: str
    character_start: int
    character_end: int
    section_title: Optional[str]
    token_count: int


def _paragraphs(text: str) -> Iterable[tuple[int, int, str]]:
    for match in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|$)", text):
        value = match.group(0).strip()
        if value:
            start = match.start() + len(match.group(0)) - len(match.group(0).lstrip())
            yield start, start + len(value), value


def chunk_page(
    text: str,
    *,
    page_number: int,
    target_size: int = 900,
    max_size: int = 1200,
    overlap: int = 80,
) -> list[ChunkDraft]:
    if target_size <= 0 or max_size < target_size or overlap < 0 or overlap >= target_size:
        raise ValueError("invalid chunking configuration")
    paragraphs = list(_paragraphs(text))
    if not paragraphs:
        return []
    drafts: list[ChunkDraft] = []
    current_start: Optional[int] = None
    current_end = 0
    current_parts: list[str] = []
    for start, end, paragraph in paragraphs:
        proposed = len("\n\n".join(current_parts + [paragraph]))
        if current_parts and proposed > max_size:
            value = "\n\n".join(current_parts)
            drafts.append(_draft(page_number, len(drafts), value, current_start or 0, current_end))
            tail = value[-overlap:] if overlap else ""
            current_parts = [tail, paragraph] if tail else [paragraph]
            current_start = max(0, current_end - len(tail)) if tail else start
        else:
            current_parts.append(paragraph)
            current_start = start if current_start is None else current_start
        current_end = end
        if len("\n\n".join(current_parts)) >= target_size:
            value = "\n\n".join(current_parts)
            drafts.append(_draft(page_number, len(drafts), value, current_start or 0, current_end))
            tail = value[-overlap:] if overlap else ""
            current_parts = [tail] if tail else []
            current_start = max(0, current_end - len(tail)) if tail else None
    if current_parts:
        value = "\n\n".join(current_parts)
        drafts.append(_draft(page_number, len(drafts), value, current_start or 0, current_end))
    return drafts


def _draft(page_number: int, index: int, value: str, start: int, end: int) -> ChunkDraft:
    return ChunkDraft(
        page_number=page_number,
        chunk_index=index,
        text_content=value,
        content_hash=hashlib.sha256(value.encode("utf-8")).hexdigest(),
        character_start=start,
        character_end=end,
        section_title=None,
        token_count=len(value.split()),
    )
