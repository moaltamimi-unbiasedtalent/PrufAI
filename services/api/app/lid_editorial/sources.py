"""Metadata-only official references for the original Berlin editorial draft."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.authorization import TenantContext
from app.knowledge.catalogue import ENTRIES, CatalogueEntry
from app.models import KnowledgeDocumentVersion, KnowledgeSource
from app.models.enums import (
    AssessmentFamily,
    KnowledgeAuthorityType,
    KnowledgeDocumentType,
    KnowledgeVersionStatus,
    LegalReviewStatus,
)
from app.services.knowledge import source_metadata_checksum
from app.standard_packs.dtz_sources import (
    _matches_entry,
    _reviewed,
    _reviewed_successors,
    _versions,
    curated_sources,
    register_curated_source,
)
from app.standard_packs.lid_sources import lid_utc

GII = "https://www.gesetze-im-internet.de"
BERLIN = "https://www.berlin.de/rbmskzl/politik/senat/verfassung"
ARCHIVE = "https://www.bundesarchiv.de/themen-entdecken/online-entdecken"
BUNDESTAG = "https://www.bundestag.de"
BULLETIN = "https://www.bundesregierung.de/breg-de/service/newsletter-und-abos/bulletin"

_BERLIN_TZ = ZoneInfo("Europe/Berlin")
_FAMILY = AssessmentFamily.INTEGRATION_KNOWLEDGE
_EXAM = "LEBEN_IN_DEUTSCHLAND"
_PREFIX = "LID_EDITORIAL"
_FEDERAL_LEGAL = ENTRIES["inttestv-2026-306"]
_METADATA_ONLY = (
    "Bibliographic support for paraphrased facts. No source text, images, or other "
    "assets are registered for reuse. No rights in the draft items are established."
)
_OBSERVED = "Consolidated official text observed 2026-11-06"
_NO_EDITION_DATE = "Public institutional page observed 2026-11-06. No edition date stated."


def _federal_law(
    key: str,
    title: str,
    path: str,
    edition: str,
    *,
    publication_label: str = _OBSERVED,
    publication_date: datetime | None = None,
    effective_from: datetime | None = None,
    edition_url: str | None = None,
) -> CatalogueEntry:
    return CatalogueEntry(
        key,
        _FEDERAL_LEGAL.authority_code,
        _FEDERAL_LEGAL.publisher,
        _FEDERAL_LEGAL.authority_type,
        _FEDERAL_LEGAL.website_url,
        f"S4_GERMANY_{key.upper()}",
        title,
        KnowledgeDocumentType.REGULATION,
        _FAMILY,
        "de",
        f"{GII}/{path}",
        edition,
        publication_label,
        _FEDERAL_LEGAL.rights_holder,
        _METADATA_ONLY,
        publication_date=publication_date,
        effective_from=effective_from,
        edition_url=edition_url,
        exam_family=_EXAM,
    )


def _historical_reference(
    key: str,
    title: str,
    url: str,
    *,
    authority_code: str = "GERMAN_FEDERAL_ARCHIVES",
    publisher: str = "Bundesarchiv",
    website: str = "https://www.bundesarchiv.de",
    publication_date: datetime | None = None,
    publication_label: str = _NO_EDITION_DATE,
) -> CatalogueEntry:
    return CatalogueEntry(
        key,
        authority_code,
        publisher,
        KnowledgeAuthorityType.FEDERAL_AUTHORITY,
        website,
        f"S4_{key.upper().replace('-', '_')}",
        title,
        KnowledgeDocumentType.OTHER,
        _FAMILY,
        "de",
        url,
        publication_label,
        publication_label,
        f"{publisher} and any credited contributors (reuse rights not established)",
        _METADATA_ONLY,
        publication_date=publication_date,
        exam_family=_EXAM,
    )


def _bundestag_reference(
    key: str,
    title: str,
    path: str,
    *,
    publication_date: datetime | None = None,
    publication_label: str = _NO_EDITION_DATE,
) -> CatalogueEntry:
    return _historical_reference(
        key,
        title,
        f"{BUNDESTAG}/{path}",
        authority_code="GERMAN_BUNDESTAG",
        publisher="Deutscher Bundestag",
        website=BUNDESTAG,
        publication_date=publication_date,
        publication_label=publication_label,
    )


S4_ENTRIES: dict[str, CatalogueEntry] = {
    entry.key: entry
    for entry in (
        _federal_law(
            "gg",
            "Grundgesetz für die Bundesrepublik Deutschland (GG)",
            "gg/BJNR000010949.html",
            "Consolidation amended by Act of 22 March 2025 (BGBl. 2025 I Nr. 94)",
            publication_label="Most recent amendment promulgated 24 March 2025",
            publication_date=datetime(2025, 3, 24, tzinfo=timezone.utc),
            effective_from=datetime(2025, 3, 25, tzinfo=_BERLIN_TZ),
            edition_url=(
                "https://www.recht.bund.de/bgbl/1/2025/94/"
                "regelungstext.pdf?__blob=publicationFile&v=3"
            ),
        ),
        _federal_law(
            "bgb",
            "Bürgerliches Gesetzbuch (BGB)",
            "bgb/BJNR001950896.html",
            "Consolidation citing Art. 6 of Act of 23 July 2026 (BGBl. 2026 I Nr. 226)",
        ),
        _federal_law(
            "betrvg",
            "Betriebsverfassungsgesetz (BetrVG)",
            "betrvg/BJNR000130972.html",
            "Consolidation amended by Art. 1 of Act of 19 July 2024 (BGBl. 2024 I Nr. 248)",
            publication_label="Most recent amendment promulgated 24 July 2024",
            publication_date=datetime(2024, 7, 24, tzinfo=timezone.utc),
            effective_from=datetime(2024, 7, 25, tzinfo=_BERLIN_TZ),
            edition_url=(
                "https://www.recht.bund.de/bgbl/1/2024/248/"
                "regelungstext.pdf?__blob=publicationFile&v=3"
            ),
        ),
        _federal_law(
            "bbig",
            "Berufsbildungsgesetz (BBiG)",
            "bbig_2005/BJNR093110005.html",
            "Consolidation citing Art. 9 of Act of 28 October 2025 (BGBl. 2025 I Nr. 259)",
        ),
        _federal_law(
            "agg",
            "Allgemeines Gleichbehandlungsgesetz (AGG)",
            "agg/BJNR189710006.html",
            "Consolidation citing Art. 15 of Act of 22 December 2023 (BGBl. 2023 I Nr. 414)",
        ),
        _federal_law(
            "entgfg",
            "Entgeltfortzahlungsgesetz (EntgFG)",
            "entgfg/BJNR106500994.html",
            "Consolidation citing Art. 2(7) of Act of 12 May 2026 (BGBl. 2026 I Nr. 143)",
        ),
        _federal_law(
            "sgb_v",
            "Fünftes Buch Sozialgesetzbuch – Gesetzliche Krankenversicherung (SGB V)",
            "sgb_5/BJNR024820988.html",
            "Consolidation citing Art. 2 of Act of 6 October 2026 (BGBl. 2026 I Nr. 289)",
        ),
        _federal_law(
            "bmg",
            "Bundesmeldegesetz (BMG)",
            "bmg/BJNR108410013.html",
            "Consolidation citing Art. 10 of Act of 22 July 2026 (BGBl. 2026 I Nr. 222)",
        ),
        _federal_law(
            "krwg",
            "Kreislaufwirtschaftsgesetz (KrWG)",
            "krwg/BJNR021210012.html",
            "Consolidation citing Art. 3 of Act of 13 October 2026 (BGBl. 2026 I Nr. 304)",
        ),
        _historical_reference(
            "berlin-airlift",
            "Währungsreform und Berlin-Blockade",
            f"{ARCHIVE}/dokumente-zur-zeitgeschichte/waehrungsreform-und-berlin-blockade/",
        ),
        _historical_reference(
            "gdr-1953",
            "Volksaufstand in den Bezirken: Berlin",
            "https://www.bundesarchiv.de/geschichten/volksaufstand/aufstand-in-den-bezirken/berlin/",
        ),
        _bundestag_reference(
            "auschwitz-liberation",
            "Erinnerung an die Befreiung – Mahnung für die Demokratie",
            "besuche/ausstellungen/pol_parl/befreiung-auschwitz-1029262",
        ),
        _historical_reference(
            "white-rose",
            "Weiße Rose – Das siebte Flugblatt",
            f"{ARCHIVE}/dokumente-zur-zeitgeschichte/weisse-rose/",
        ),
        _historical_reference(
            "basic-treaty",
            "Normale gutnachbarliche Beziehungen",
            f"{ARCHIVE}/themenbeitraege/normale-gutnachbarliche-beziehungen/",
        ),
        _historical_reference(
            "warsaw-1970",
            "Rede zum 50. Jahrestag des Kniefalls von Willy Brandt in Warschau",
            f"{BULLETIN}/rede-von-bundespraesident-dr-frank-walter-steinmeier-1825914",
            authority_code="GERMAN_FEDERAL_GOVERNMENT_BULLETIN",
            publisher="Presse- und Informationsamt der Bundesregierung",
            website="https://www.bundesregierung.de",
            publication_date=datetime(2020, 12, 7, tzinfo=timezone.utc),
            publication_label="Bulletin 137-2 of 7 December 2020",
        ),
        _bundestag_reference(
            "rome-treaties",
            "Römische Verträge",
            "services/glossar/roem_vertr-245532",
        ),
        _bundestag_reference(
            "war-end-europe",
            "Steinmeier: Schützen wir unsere Freiheit und Demokratie",
            "dokumente/textarchiv/2025/kw19-de-gedenkstunde-8-mai-1059530",
            publication_label="Parliamentary commemoration dated 8 May 2025",
        ),
        CatalogueEntry(
            "vbb",
            "BERLIN_SENATSKANZLEI",
            "Senatskanzlei Berlin",
            KnowledgeAuthorityType.PUBLIC_AGENCY,
            "https://www.berlin.de/rbmskzl/",
            "S4_BERLIN_VVB",
            "Verfassung von Berlin (VvB)",
            KnowledgeDocumentType.REGULATION,
            _FAMILY,
            "de",
            f"{BERLIN}/",
            "Consolidation amended by Nineteenth Act of 29 April 2026 (GVBl. 2026 p. 186)",
            "Most recent amendment promulgated 12 May 2026",
            "Official legal publication of the Land of Berlin",
            _METADATA_ONLY,
            publication_date=datetime(2026, 5, 12, tzinfo=timezone.utc),
            effective_from=datetime(2026, 5, 13, tzinfo=_BERLIN_TZ),
            edition_url=(
                "https://www.berlin.de/sen/justiz/service/gesetze-und-verordnungen/2026/"
                "ausgabe-nr-14-vom-1252026-s-185-204.pdf?ts=1786600619"
            ),
            exam_family=_EXAM,
        ),
    )
}


def editorial_sources(session: Session) -> list[dict[str, Any]]:
    return curated_sources(session, S4_ENTRIES, retain_reviewed_successors=True)


def register_editorial_source(
    session: Session, context: TenantContext, key: str
) -> KnowledgeDocumentVersion:
    version = register_curated_source(session, context, key, S4_ENTRIES, prefix=_PREFIX)
    if version.metadata_checksum is None:
        raise ValueError(f"{_PREFIX}_EXACT_SOURCE_PIN_MISMATCH")
    return editorial_source_version(session, key, version.id, version.metadata_checksum)


def _issuer_url(entry: CatalogueEntry, url: str | None) -> bool:
    hosts = {urlparse(value).hostname for value in (entry.url, entry.edition_url) if value}
    try:
        origin = urlparse(url or "")
        return (
            origin.scheme == "https"
            and origin.hostname in hosts
            and origin.port in {None, 443}
            and origin.username is None
            and origin.password is None
        )
    except ValueError:
        return False


def _resolve_editorial_source(
    session: Session,
    key: str,
    version_id: UUID,
    metadata_checksum: str,
    *,
    require_reviewed: bool,
    previous_version_id: UUID | None = None,
    lock_for_review: bool = True,
) -> KnowledgeDocumentVersion:
    entry = S4_ENTRIES.get(key)
    if entry is None:
        raise ValueError(f"{_PREFIX}_UNKNOWN_SOURCE")
    document = KnowledgeDocumentVersion
    root_query = (
        select(document.id, document.source_id)
        .join(document.source)
        .where(
            KnowledgeSource.source_code == entry.source_code,
            document.version_label == entry.version_label,
        )
    )
    lock = require_reviewed and lock_for_review
    if lock:
        # Serialize consequential checks at the curated root before locking a descendant.
        # Read legal metadata only after acquiring the lock used by rights review.
        root_query = root_query.with_for_update(of=document)
    identity = session.execute(root_query).one_or_none()
    if identity is None:
        raise ValueError(f"{_PREFIX}_EXACT_SOURCE_PIN_MISMATCH")
    root_id, source_id = identity
    if lock and version_id != root_id:
        locked = session.scalar(
            select(document.id)
            .where(document.id == version_id, document.source_id == source_id)
            .with_for_update(of=document)
        )
        if locked is None:
            raise ValueError(f"{_PREFIX}_EXACT_SOURCE_PIN_MISMATCH")
    lineage = (
        select(document.id, document.supersedes_document_version_id)
        .where(document.id == version_id, document.source_id == source_id)
        .cte("editorial_source_ancestors", recursive=True)
    )
    lineage = lineage.union(
        select(document.id, document.supersedes_document_version_id).join(
            lineage,
            and_(
                document.id == lineage.c.supersedes_document_version_id,
                document.source_id == source_id,
            ),
        )
    )
    versions = {
        version.id: version
        for version in session.scalars(_versions().where(document.id.in_(select(lineage.c.id))))
    }
    root = versions.get(root_id)
    version = versions.get(version_id)
    if (
        version is None
        or version.metadata_checksum != metadata_checksum
        or source_metadata_checksum(version) != metadata_checksum
    ):
        raise ValueError(f"{_PREFIX}_EXACT_SOURCE_PIN_MISMATCH")
    if previous_version_id is not None and (
        root is None or previous_version_id not in versions or previous_version_id == version_id
    ):
        raise ValueError(f"{_PREFIX}_SOURCE_REPLACEMENT_LINEAGE_REQUIRED")
    if root is None or not _matches_entry(root, entry):
        raise ValueError(f"{_PREFIX}_EXACT_SOURCE_PIN_MISMATCH")
    replacement = version_id != root_id
    if replacement and not _issuer_url(entry, version.source_url):
        raise ValueError(f"{_PREFIX}_SOURCE_REPLACEMENT_ISSUER_MISMATCH")
    now = datetime.now(timezone.utc)
    if (
        version.status is not KnowledgeVersionStatus.DRAFT
        or (replacement and version.retrieved_at is None)
        or ((require_reviewed or replacement) and not _reviewed(version, now))
    ):
        raise ValueError(f"{_PREFIX}_REVIEWED_METADATA_SOURCE_REQUIRED")
    if _reviewed_successors(
        session,
        {key: version},
        now,
        {key: entry},
        lock=lock,
        retain_reviewed_successors=True,
    ):
        raise ValueError(f"{_PREFIX}_SOURCE_SUCCESSOR_REVIEWED")
    return version


def editorial_source_version(
    session: Session,
    key: str,
    version_id: UUID,
    metadata_checksum: str,
    *,
    require_reviewed: bool = False,
    lock_for_review: bool = True,
) -> KnowledgeDocumentVersion:
    return _resolve_editorial_source(
        session,
        key,
        version_id,
        metadata_checksum,
        require_reviewed=require_reviewed,
        lock_for_review=lock_for_review,
    )


def editorial_replacement_source(
    session: Session,
    key: str,
    previous_version_id: UUID,
    version_id: UUID,
    metadata_checksum: str,
) -> KnowledgeDocumentVersion:
    """Adopt a reviewed edition from the previous pin's explicit issuer lineage."""
    return _resolve_editorial_source(
        session,
        key,
        version_id,
        metadata_checksum,
        require_reviewed=True,
        previous_version_id=previous_version_id,
    )


def _timestamp(value: datetime | None) -> str | None:
    utc = lid_utc(value)
    return utc.isoformat() if utc else None


def editorial_source_snapshot(key: str, version: KnowledgeDocumentVersion) -> dict[str, Any]:
    entry = S4_ENTRIES[key]
    original = _matches_entry(version, entry)
    if not original and (
        version.source.source_code != entry.source_code
        or version.supersedes_document_version_id is None
        or not _issuer_url(entry, version.source_url)
        or version.metadata_checksum != source_metadata_checksum(version)
    ):
        raise ValueError(f"{_PREFIX}_EXACT_SOURCE_PIN_MISMATCH")
    legal = version.legal_metadata
    bibliographic_only = _reviewed(version, datetime.now(timezone.utc))
    return {
        **entry.public(),
        "source_key": key,
        "source_id": str(version.source_id),
        "version_id": str(version.id),
        "metadata_checksum": version.metadata_checksum,
        "version_label": version.version_label,
        "edition_url": entry.edition_url if original else version.source_url,
        "publication_label": (
            entry.publication_label if original else "Source Registry successor version"
        ),
        "publication_date": _timestamp(version.publication_date),
        "effective_from": _timestamp(version.effective_from),
        "effective_until": _timestamp(version.effective_until),
        "retrieved_at": _timestamp(version.retrieved_at),
        "verified_on": entry.retrieved_at.date().isoformat() if original else None,
        "supersedes_version_id": (
            str(version.supersedes_document_version_id)
            if version.supersedes_document_version_id
            else None
        ),
        "source_status": version.status.value,
        "legal_review_status": (
            legal.legal_review_status.value if legal else LegalReviewStatus.NOT_REVIEWED.value
        ),
        "rights_status": (
            "BIBLIOGRAPHIC_REFERENCE_ONLY" if bibliographic_only else "REVIEW_REQUIRED"
        ),
        "rights_decision_checksum": legal.decision_checksum
        if bibliographic_only and legal
        else None,
    }
