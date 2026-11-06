"""Curated S1 bibliographic records. Registration never fetches source content."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, AuthorizationService, TenantContext
from app.db import get_db_session
from app.models import KnowledgeAuthority, KnowledgeDocumentVersion, KnowledgeSource
from app.models.enums import (
    AssessmentFamily,
    KnowledgeAuthorityType,
    KnowledgeDocumentType,
    KnowledgeScope,
)
from app.services.knowledge import KnowledgeRegistryService, source_metadata_checksum

router = APIRouter(prefix="/api/v1/platform/standards", tags=["standards"])
# Bibliographic verification date, at day precision. Not a publication date or
# an assertion that a copy of the original bytes was retained.
VERIFIED_ON = datetime(2026, 11, 6, tzinfo=timezone.utc)
COE = "https://www.coe.int/en/web/common-european-framework-reference-languages"
DTZ = (
    "https://www.gast.de/de/forschung-entwicklung/entwicklung/auftraege/"
    "deutsch-test-fuer-zuwanderer-dtz"
)
LID = (
    "https://www.bamf.de/SharedDocs/Anlagen/DE/Integration/Einbuergerung/"
    "gesamtfragenkatalog-lebenindeutschland.pdf?__blob=publicationFile&v=22"
)
INTTESTV = "https://www.gesetze-im-internet.de/inttestv/BJNR080100013.html"
INTTESTV_AMENDMENT = (
    "https://www.recht.bund.de/bgbl/1/2026/306/regelungstext.pdf?__blob=publicationFile&v=1"
)


@dataclass(frozen=True)
class CatalogueEntry:
    key: str
    authority_code: str
    publisher: str
    authority_type: KnowledgeAuthorityType
    website_url: str
    source_code: str
    title: str
    document_type: KnowledgeDocumentType
    family: AssessmentFamily
    language: str
    url: str
    version_label: str
    publication_label: str
    rights_holder: str
    rights_note: str
    publication_date: Optional[datetime] = None
    effective_from: Optional[datetime] = None
    exam_family: Optional[str] = None
    provider_code: Optional[str] = None
    edition_url: Optional[str] = None

    def public(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "source_code": self.source_code,
            "publisher": self.publisher,
            "title": self.title,
            "source_url": self.url,
            "edition_url": self.edition_url,
            "version_label": self.version_label,
            "publication_label": self.publication_label,
            "publication_date": self.publication_date,
            "effective_from": self.effective_from,
            "verified_on": VERIFIED_ON.date().isoformat(),
            "assessment_family": self.family.value,
            "exam_family": self.exam_family,
            "language": self.language,
            "cefr_level": None,
            "rights_holder": self.rights_holder,
            "rights_note": self.rights_note,
            "content_mode": "METADATA_ONLY",
            "external_content_rights": "NOT_APPROVED",
        }


ENTRIES = {
    entry.key: entry
    for entry in (
        CatalogueEntry(
            "cefr-companion-2020",
            "COUNCIL_OF_EUROPE",
            "Council of Europe",
            KnowledgeAuthorityType.SUPRANATIONAL_STANDARD_BODY,
            COE,
            "S1_COE_CEFR_COMPANION_2020",
            "Common European Framework of Reference for Languages: Companion volume",
            KnowledgeDocumentType.STANDARD,
            AssessmentFamily.LANGUAGE,
            "en",
            COE + "/cefr-companion-volume-and-its-language-versions",
            "2020 English edition (ISBN 978-92-871-8621-8)",
            "April 2020 (month stated in the English edition)",
            "Council of Europe",
            "The English edition reserves reproduction and translation rights. "
            "S1 records the bibliographic reference only.",
            edition_url="https://rm.coe.int/cefr-companion-volume-with-new-descriptors-2020/16809ea0d4",
        ),
        CatalogueEntry(
            "gast-dtz-information",
            "GAST",
            "Gesellschaft für Akademische Studienvorbereitung und Testentwicklung e.V. (g.a.s.t.)",
            KnowledgeAuthorityType.EXAM_PROVIDER,
            "https://www.gast.de",
            "S1_GAST_DTZ_INFORMATION",
            "Deutsch-Test für Zuwanderer (DTZ) — official A2–B1 information",
            KnowledgeDocumentType.EXAM_SPECIFICATION,
            AssessmentFamily.LANGUAGE,
            "de",
            DTZ,
            "Official information page observed 2026-11-06",
            "Undated web page; administration since 1 January 2023 is not a publication date",
            "g.a.s.t. e.V. (site publisher; test material rights not established)",
            "Public information only. No permission to reproduce or ingest test material recorded.",
            exam_family="DEUTSCH_TEST_FUER_ZUWANDERER",
            provider_code="GAST",
        ),
        CatalogueEntry(
            "bamf-lid-catalogue",
            "BAMF",
            "Bundesamt für Migration und Flüchtlinge",
            KnowledgeAuthorityType.FEDERAL_AUTHORITY,
            "https://www.bamf.de",
            "S1_BAMF_LID_CATALOGUE",
            "Gesamtfragenkatalog zum Test Leben in Deutschland und zum Einbürgerungstest",
            KnowledgeDocumentType.QUESTION_CATALOGUE,
            AssessmentFamily.INTEGRATION_KNOWLEDGE,
            "de",
            LID,
            "Stand 07.05.2025 (official PDF revision 22)",
            "Catalogue revision dated 7 May 2025; separate publication date not stated",
            "BAMF and credited contributors (reuse rights not established)",
            "Reference only. No questions, images or answers were registered.",
            exam_family="LEBEN_IN_DEUTSCHLAND",
        ),
        CatalogueEntry(
            "inttestv-2026-306",
            "GERMAN_FEDERAL_LEGAL_PUBLICATION",
            "Gesetze im Internet (BMJ/BfJ)",
            KnowledgeAuthorityType.FEDERAL_AUTHORITY,
            "https://www.gesetze-im-internet.de",
            "S1_GERMANY_INTTESTV",
            "Integrationskurstestverordnung (IntTestV)",
            KnowledgeDocumentType.REGULATION,
            AssessmentFamily.INTEGRATION_KNOWLEDGE,
            "de",
            INTTESTV,
            "Consolidation amended by Art. 1 of 13.10.2026 (BGBl. 2026 I Nr. 306)",
            "Original instrument dated 9 April 2013; amendment published 20 October 2026",
            "Federal official legal publication",
            "Legal reference only. The October 2026 change is recorded for human review; "
            "no rules or assessment criteria are activated.",
            publication_date=datetime(2026, 10, 20, tzinfo=timezone.utc),
            effective_from=datetime(2026, 11, 1, tzinfo=ZoneInfo("Europe/Berlin")),
            edition_url=INTTESTV_AMENDMENT,
        ),
    )
}


def _context(principal: Principal, *permissions: str) -> TenantContext:
    context = principal.tenant_context
    if context is None or not context.platform_scope or context.organisation_id is not None:
        raise HTTPException(status_code=403, detail="platform standards context required")
    try:
        for permission in permissions:
            AuthorizationService.require_permission(context, permission)
    except AuthorizationError as exc:
        raise HTTPException(
            status_code=403, detail="platform standards permission required"
        ) from exc
    return context


def _same_time(left: Optional[datetime], right: Optional[datetime]) -> bool:
    def normalize(value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    return normalize(left) == normalize(right)


def _register(
    session: Session, context: TenantContext, entry: CatalogueEntry
) -> KnowledgeDocumentVersion:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(6010036)"))
    registry = KnowledgeRegistryService()
    authority = session.scalar(
        select(KnowledgeAuthority).where(KnowledgeAuthority.code == entry.authority_code)
    )
    if authority is None:
        authority = registry.create_authority(
            session,
            context,
            code=entry.authority_code,
            name=entry.publisher,
            authority_type=entry.authority_type,
            jurisdiction="DE" if entry.authority_code != "COUNCIL_OF_EUROPE" else "EUROPE",
            website_url=entry.website_url,
            commit=False,
        )
    else:
        names = {entry.publisher}
        if entry.authority_code == "GAST":
            # The existing registry seeds this same publisher under its brand.
            names.add("g.a.s.t.")
        if (
            authority.name not in names
            or authority.authority_type != entry.authority_type
            or authority.status != "ACTIVE"
        ):
            raise ValueError("curated authority identity conflicts with the existing record")
    source = session.scalar(
        select(KnowledgeSource).where(KnowledgeSource.source_code == entry.source_code)
    )
    if source is None:
        source = registry.create_source(
            session,
            context,
            authority_id=authority.id,
            source_code=entry.source_code,
            title=entry.title,
            document_type=entry.document_type,
            assessment_family=entry.family,
            scope=KnowledgeScope.PLATFORM_SHARED,
            language=entry.language,
            provider_code=entry.provider_code,
            exam_family=entry.exam_family,
            canonical_url=entry.url,
            commit=False,
        )
    elif (
        source.authority_id != authority.id
        or source.title != entry.title
        or source.canonical_url != entry.url
        or source.scope != KnowledgeScope.PLATFORM_SHARED
        or source.assessment_family != entry.family
        or source.exam_family != entry.exam_family
        or source.provider_code != entry.provider_code
        or source.cefr_level is not None
        or source.skill is not None
        or source.status != "ACTIVE"
        or source.document_type != entry.document_type
        or source.language != entry.language
    ):
        raise ValueError("curated source identity conflicts with the existing record")
    version_url = entry.edition_url or entry.url
    notes = f"{entry.publication_label}. {entry.rights_note}"
    if entry.edition_url:
        notes += f" Exact edition: {entry.edition_url}"
    version = session.scalar(
        select(KnowledgeDocumentVersion).where(
            KnowledgeDocumentVersion.source_id == source.id,
            KnowledgeDocumentVersion.version_label == entry.version_label,
        )
    )
    if version is None:
        version = registry.create_version(
            session,
            context,
            source_id=source.id,
            version_label=entry.version_label,
            publication_date=entry.publication_date,
            effective_from=entry.effective_from,
            retrieved_at=VERIFIED_ON,
            source_url=version_url,
            notes=notes,
            content_mode="METADATA_ONLY",
            rights_holder=entry.rights_holder,
            licence_type="NO_CONTENT_REUSE_APPROVAL",
            commit=False,
        )
    elif (
        version.source_url != version_url
        or version.content_mode != "METADATA_ONLY"
        or version.sha256 is not None
        or version.storage_key is not None
        or version.notes != notes
        or version.effective_until is not None
        or version.supersedes_document_version_id is not None
        or not _same_time(version.publication_date, entry.publication_date)
        or not _same_time(version.effective_from, entry.effective_from)
        or not _same_time(version.retrieved_at, VERIFIED_ON)
    ):
        raise ValueError("curated version identity conflicts with the existing record")
    if version.metadata_checksum != source_metadata_checksum(version):
        raise ValueError("curated version metadata checksum does not match")
    return version


@router.get("/catalogue")
def official_catalogue(principal: Principal = Depends(get_principal)) -> list[dict[str, Any]]:
    _context(principal, "standards.source.read")
    return [item.public() for item in ENTRIES.values()]


@router.post("/catalogue/{key}/register")
def register_catalogue_entry(
    key: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(
        principal,
        "standards.authority.manage",
        "standards.source.manage",
        "standards.document.manage",
    )
    entry = ENTRIES.get(key)
    if entry is None:
        raise HTTPException(status_code=404, detail="catalogue entry not found")
    try:
        row = _register(session, context, entry)
        result = {
            **entry.public(),
            "source_id": str(row.source_id),
            "document_version_id": str(row.id),
            "metadata_checksum": row.metadata_checksum,
            "sha256": row.sha256,
            "status": row.status.value,
        }
        session.commit()
        return result
    except AuthorizationError as exc:
        session.rollback()
        raise HTTPException(
            status_code=403, detail="platform standards permission required"
        ) from exc
    except (ValueError, IntegrityError) as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="curated source identity conflict") from exc
