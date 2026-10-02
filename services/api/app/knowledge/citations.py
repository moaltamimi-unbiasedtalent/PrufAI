from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Optional

from app.services.retrieval import CitationReadyResult


@dataclass(frozen=True)
class Citation:
    citation_id: str
    authority_name: str
    source_title: str
    version_label: str
    page_number: int
    section_title: Optional[str]
    publication_date: object
    effective_from: object
    effective_until: object
    source_url: Optional[str]
    document_version_id: uuid.UUID
    checksum: Optional[str]
    citation_allowed: bool
    text_display_allowed: bool
    redistribution_status: str

    @property
    def display_text(self) -> str:
        parts = [self.authority_name, self.source_title, f"Version {self.version_label}"]
        if self.section_title:
            parts.append(self.section_title)
        parts.append(f"p. {self.page_number}")
        return ", ".join(parts)


class CitationBuilder:
    def build(self, result: CitationReadyResult) -> Citation:
        legal = result.legal_summary
        redistribution = legal.get("redistribution_allowed")
        citation_allowed = redistribution is not False
        text_display_allowed = (
            redistribution is True and legal.get("ai_processing_allowed") is not False
        )
        return Citation(
            citation_id=self.identifier(result),
            authority_name=result.authority_name,
            source_title=result.source_title,
            version_label=result.version_label,
            page_number=result.page_number,
            section_title=result.section_title,
            publication_date=result.publication_date,
            effective_from=result.effective_from,
            effective_until=result.effective_until,
            source_url=result.source_url,
            document_version_id=result.document_version_id,
            checksum=result.document_checksum,
            citation_allowed=citation_allowed,
            text_display_allowed=text_display_allowed,
            redistribution_status="UNKNOWN"
            if redistribution is None
            else ("ALLOWED" if redistribution else "RESTRICTED"),
        )

    def identifier(self, result: CitationReadyResult) -> str:
        raw = f"{result.document_version_id}:{result.chunk_id}:{result.page_number}"
        return "evidence-" + hashlib.sha256(raw.encode()).hexdigest()[:24]
