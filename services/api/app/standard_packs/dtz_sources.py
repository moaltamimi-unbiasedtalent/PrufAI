"""Bibliographic catalogue and observed claims for the DTZ practice draft."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Select, Uuid, and_, select, tuple_, type_coerce
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.orm.interfaces import LoaderOption

from app.authorization import AuthorizationError, AuthorizationService, TenantContext
from app.knowledge.catalogue import DTZ, ENTRIES, CatalogueEntry, _register, _same_time
from app.models import KnowledgeDocumentVersion, KnowledgeSource
from app.models.enums import (
    AssessmentFamily,
    KnowledgeAuthorityType,
    KnowledgeDocumentType,
    KnowledgeScope,
    KnowledgeVersionStatus,
    LegalReviewStatus,
)
from app.services.knowledge import source_legal_decision_valid, source_metadata_checksum

DTZ_EXAM_FAMILY = "DEUTSCH_TEST_FUER_ZUWANDERER"
_OVERVIEW = DTZ + "/der-dtz-auf-einen-blick"
_FAQ = DTZ + "/faq"
_SPECIMEN = "https://www.gast.de/fileadmin/gast.de/GAST/5_DTZ/PDF/gast_DTZ_UEbungssatz_1.pdf"
_CURRICULUM = (
    "https://www.bamf.de/SharedDocs/Anlagen/DE/Integration/Integrationskurse/Kurstraeger/"
    "KonzepteLeitfaeden/rahmencurriculum-integrationskurs.pdf?__blob=publicationFile&v=8"
)
_INTV = "https://www.gesetze-im-internet.de/intv/BJNR337000004.html"
_INTV_AMENDMENT = (
    "https://www.recht.bund.de/bgbl/1/2026/218/regelungstext.pdf?__blob=publicationFile&v=1"
)
_PROVIDER = ENTRIES["gast-dtz-information"]
_WEB_VERSION = "Undated public web page observed 2026-11-06"
_WEB_PUBLICATION = "No edition publication or effective date stated"
_METADATA_RIGHTS = "Bibliographic reference only. No provider content reuse is approved."

DTZ_ENTRIES = {
    entry.key: entry
    for entry in (
        _PROVIDER,
        replace(
            _PROVIDER,
            key="gast-dtz-overview",
            source_code="S2_GAST_DTZ_OVERVIEW",
            title="Der DTZ auf einen Blick",
            url=_OVERVIEW,
            version_label=_WEB_VERSION,
            publication_label=_WEB_PUBLICATION,
            rights_note=_METADATA_RIGHTS,
        ),
        replace(
            _PROVIDER,
            key="gast-dtz-faq",
            source_code="S2_GAST_DTZ_FAQ",
            title="DTZ: Häufig gestellte Fragen",
            url=_FAQ,
            version_label=_WEB_VERSION,
            publication_label=_WEB_PUBLICATION,
            rights_note=_METADATA_RIGHTS,
        ),
        replace(
            _PROVIDER,
            key="gast-dtz-specimen-2024",
            source_code="S2_GAST_DTZ_SPECIMEN_2024",
            title="Deutsch-Test für Zuwanderer A2–B1: Übungssatz 1",
            document_type=KnowledgeDocumentType.PRACTICE_MATERIAL,
            url=_SPECIMEN,
            version_label="June 2024 edition, as stated in the imprint",
            publication_label="June 2024 (day not stated)",
            rights_holder="g.a.s.t. e.V. and credited contributors",
            rights_note=(
                "Reference for structural facts and historical applicability only. "
                "The imprint reserves copyright in the materials."
            ),
        ),
        CatalogueEntry(
            key="bamf-dtz-curriculum-2016",
            authority_code="BAMF",
            publisher="Bundesamt für Migration und Flüchtlinge",
            authority_type=KnowledgeAuthorityType.FEDERAL_AUTHORITY,
            website_url="https://www.bamf.de",
            source_code="S2_BAMF_DTZ_CURRICULUM_2016",
            title="Rahmencurriculum für Integrationskurse Deutsch als Zweitsprache",
            document_type=KnowledgeDocumentType.STANDARD,
            family=AssessmentFamily.LANGUAGE,
            language="de",
            url=_CURRICULUM,
            version_label="2016 revision commissioned by BAMF",
            publication_label="Imprint identifies the 2016 revision (publication day not stated)",
            rights_holder="Goethe-Institut e.V.",
            rights_note="Bibliographic curriculum context only. No curriculum reuse is approved.",
            exam_family=DTZ_EXAM_FAMILY,
        ),
        ENTRIES["inttestv-2026-306"],
        replace(
            ENTRIES["inttestv-2026-306"],
            key="intv-2026-218",
            source_code="S2_GERMANY_INTV",
            title="Integrationskursverordnung (IntV)",
            url=_INTV,
            version_label="Consolidation amended by Art. 1 of 22.07.2026 (BGBl. 2026 I Nr. 218)",
            publication_label=(
                "Original instrument dated 13 December 2004. Amendment issued 27 July 2026"
            ),
            publication_date=datetime(2026, 7, 27, tzinfo=timezone.utc),
            effective_from=datetime(2026, 7, 28, tzinfo=ZoneInfo("Europe/Berlin")),
            edition_url=_INTV_AMENDMENT,
            rights_note=(
                "Legal reference only. The July amendment concerns course admission priority. "
                "The consolidated § 17 defines the role of the two distinct final tests."
            ),
        ),
        ENTRIES["cefr-companion-2020"],
    )
}


def register_dtz_source(
    session: Session, context: TenantContext, key: str
) -> KnowledgeDocumentVersion:
    return register_curated_source(session, context, key, DTZ_ENTRIES, prefix="DTZ")


def register_curated_source(
    session: Session,
    context: TenantContext,
    key: str,
    entries: dict[str, CatalogueEntry],
    *,
    prefix: str,
) -> KnowledgeDocumentVersion:
    """Register curated bibliographic metadata in the caller's transaction."""
    if context.organisation_id is not None or not context.platform_scope:
        raise AuthorizationError("platform standards context required")
    for permission in (
        "standards.authority.manage",
        "standards.source.manage",
        "standards.document.manage",
    ):
        AuthorizationService.require_permission(context, permission)
    entry = entries.get(key)
    if entry is None:
        raise ValueError(f"{prefix}_UNKNOWN_SOURCE")
    return _register(session, context, entry)


def _version_metadata() -> tuple[LoaderOption, LoaderOption]:
    return (
        joinedload(KnowledgeDocumentVersion.source).joinedload(KnowledgeSource.authority),
        joinedload(KnowledgeDocumentVersion.legal_metadata),
    )


def _versions() -> Select[KnowledgeDocumentVersion]:
    return (
        select(KnowledgeDocumentVersion)
        .options(*_version_metadata())
        .execution_options(populate_existing=True)
    )


def _matches_entry(version: KnowledgeDocumentVersion, entry: CatalogueEntry) -> bool:
    source = version.source
    authority = source.authority
    publisher_names = {entry.publisher}
    if entry.authority_code == "GAST":
        publisher_names.add("g.a.s.t.")
    notes = f"{entry.publication_label}. {entry.rights_note}"
    if entry.edition_url:
        notes += f" Exact edition: {entry.edition_url}"
    return (
        authority.code == entry.authority_code
        and authority.name in publisher_names
        and authority.authority_type == entry.authority_type
        and authority.status == "ACTIVE"
        and source.source_code == entry.source_code
        and source.title == entry.title
        and source.canonical_url == entry.url
        and source.scope is KnowledgeScope.PLATFORM_SHARED
        and source.status == "ACTIVE"
        and source.assessment_family == entry.family
        and source.exam_family == entry.exam_family
        and source.provider_code == entry.provider_code
        and source.document_type == entry.document_type
        and source.language == entry.language
        and source.cefr_level is None
        and source.skill is None
        and version.version_label == entry.version_label
        and version.source_url == (entry.edition_url or entry.url)
        and version.content_mode == "METADATA_ONLY"
        and version.notes == notes
        and version.sha256 is None
        and version.storage_key is None
        and version.effective_until is None
        and version.supersedes_document_version_id is None
        and _same_time(version.publication_date, entry.publication_date)
        and _same_time(version.effective_from, entry.effective_from)
        and _same_time(version.retrieved_at, entry.retrieved_at)
        and version.metadata_checksum == source_metadata_checksum(version)
    )


def _reviewed(
    version: KnowledgeDocumentVersion, now: datetime, *, historical_precedence: bool = False
) -> bool:
    legal = version.legal_metadata
    starts_at = version.effective_from
    if starts_at is not None and starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=timezone.utc)
    ends_at = version.effective_until
    if ends_at is not None and ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)
    return (
        (
            version.status is KnowledgeVersionStatus.DRAFT
            or (
                historical_precedence
                and version.status
                in {KnowledgeVersionStatus.RETIRED, KnowledgeVersionStatus.SUPERSEDED}
            )
        )
        and version.content_mode == "METADATA_ONLY"
        and version.sha256 is None
        and version.storage_key is None
        and version.created_by_user_id is not None
        and (starts_at is None or starts_at <= now)
        and (historical_precedence or ends_at is None or now < ends_at)
        and legal is not None
        and legal.usage_rights == "BIBLIOGRAPHIC_REFERENCE_ONLY"
        and legal.ingestion_allowed is False
        and legal.derivative_use_allowed is False
        and legal.redistribution_allowed is False
        and legal.ai_processing_allowed is False
        and legal.commercial_use_allowed is False
        and legal.citation_required is True
        and source_legal_decision_valid(version)
    )


def _reviewed_successors(
    session: Session,
    versions: dict[str, KnowledgeDocumentVersion],
    now: datetime,
    entries: dict[str, CatalogueEntry],
    *,
    lock: bool = False,
    retain_reviewed_successors: bool = False,
) -> set[UUID]:
    if not versions:
        return set()
    document = KnowledgeDocumentVersion
    issuer_hosts = {
        version.id: {urlparse(url).hostname for url in (entry.url, entry.edition_url) if url}
        for key, version in versions.items()
        for entry in (entries[key],)
    }
    lineage = (
        select(document.id.label("root_id"), document.id, document.source_id)
        .where(document.id.in_(issuer_hosts))
        .cte("curated_source_lineage", recursive=True)
    )
    lineage = lineage.union(
        select(lineage.c.root_id, document.id, document.source_id).join(
            lineage,
            and_(
                document.supersedes_document_version_id == lineage.c.id,
                document.source_id == lineage.c.source_id,
            ),
        )
    )
    descendants = (
        select(document.id)
        .join(lineage, lineage.c.id == document.id)
        .where(lineage.c.root_id != document.id)
        .order_by(document.id)
    )
    if lock:
        # Rights review locks the version and updates its separate legal row.
        # Read those rows in a new statement after acquiring the same lock.
        session.scalars(descendants.with_for_update(of=document)).all()
    candidates = (
        select(document, type_coerce(lineage.c.root_id, Uuid()))
        .join(lineage, lineage.c.id == document.id)
        .where(lineage.c.root_id != document.id)
        .options(*_version_metadata())
        .execution_options(populate_existing=True)
    )
    stale: set[UUID] = set()
    for successor, root_id in session.execute(candidates):
        origin = urlparse(successor.source_url or "")
        if (
            origin.scheme == "https"
            and origin.hostname in issuer_hosts[root_id]
            # An effective reviewed successor can disqualify its predecessor
            # without itself remaining eligible for current use.
            and _reviewed(successor, now, historical_precedence=retain_reviewed_successors)
        ):
            stale.add(root_id)
    return stale


def dtz_sources(session: Session) -> list[dict[str, Any]]:
    return curated_sources(session, DTZ_ENTRIES)


def curated_sources(
    session: Session,
    entries: dict[str, CatalogueEntry],
    *,
    retain_reviewed_successors: bool = False,
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    identities = [(entry.source_code, entry.version_label) for entry in entries.values()]
    candidates = session.scalars(
        _versions()
        .join(KnowledgeDocumentVersion.source)
        .where(
            tuple_(KnowledgeSource.source_code, KnowledgeDocumentVersion.version_label).in_(
                identities
            )
        )
    )
    versions = {(item.source.source_code, item.version_label): item for item in candidates}
    exact = {
        entry.key: item
        for entry in entries.values()
        if (item := versions.get((entry.source_code, entry.version_label))) is not None
        and _matches_entry(item, entry)
    }
    stale = _reviewed_successors(
        session, exact, now, entries, retain_reviewed_successors=retain_reviewed_successors
    )
    result: list[dict[str, Any]] = []
    for entry in entries.values():
        item = exact.get(entry.key)
        registered = None
        if item is not None:
            registered = {
                "id": str(item.id),
                "metadata_checksum": item.metadata_checksum,
                "legal_review_status": (
                    item.legal_metadata.legal_review_status.value
                    if item.legal_metadata
                    else LegalReviewStatus.NOT_REVIEWED.value
                ),
                "reviewed": _reviewed(item, now) and item.id not in stale,
                "source_status": item.status.value,
            }
        result.append({**entry.public(), "registered": registered})
    return result


def resolve_dtz_sources(
    session: Session, pins: dict[str, tuple[UUID, str]]
) -> dict[str, KnowledgeDocumentVersion]:
    return resolve_curated_sources(session, pins, DTZ_ENTRIES, prefix="DTZ")


def resolve_curated_sources(
    session: Session,
    pins: dict[str, tuple[UUID, str]],
    entries: dict[str, CatalogueEntry],
    *,
    prefix: str,
    refresh_after_lock: bool = False,
    retain_reviewed_successors: bool = False,
) -> dict[str, KnowledgeDocumentVersion]:
    """Lock the exact source versions before checking their bibliographic decisions."""
    identifiers = {identifier for identifier, _ in pins.values()}
    if pins.keys() != entries.keys() or len(identifiers) != len(entries):
        raise ValueError(f"{prefix}_EXACT_SOURCE_PINS_REQUIRED")
    if refresh_after_lock:
        # The rights review locks the document version before writing its legal row.
        # Load that row in a new statement after acquiring the same document lock.
        session.scalars(
            select(KnowledgeDocumentVersion.id)
            .where(KnowledgeDocumentVersion.id.in_(sorted(identifiers)))
            .order_by(KnowledgeDocumentVersion.id)
            .with_for_update()
        ).all()
    versions = {
        item.id: item
        for item in session.scalars(
            _versions()
            .where(KnowledgeDocumentVersion.id.in_(sorted(identifiers)))
            .order_by(KnowledgeDocumentVersion.id)
            .with_for_update(of=KnowledgeDocumentVersion)
        )
    }
    now = datetime.now(timezone.utc)
    resolved: dict[str, KnowledgeDocumentVersion] = {}
    for key, entry in entries.items():
        identifier, checksum = pins[key]
        version = versions.get(identifier)
        if (
            version is None
            or version.metadata_checksum != checksum
            or not _matches_entry(version, entry)
        ):
            raise ValueError(f"{prefix}_EXACT_SOURCE_PIN_MISMATCH")
        if not _reviewed(version, now):
            raise ValueError(f"{prefix}_REVIEWED_METADATA_SOURCE_REQUIRED")
        resolved[key] = version
    if _reviewed_successors(
        session,
        resolved,
        now,
        entries,
        lock=True,
        retain_reviewed_successors=retain_reviewed_successors,
    ):
        raise ValueError(f"{prefix}_SOURCE_SUCCESSOR_REVIEWED")
    return resolved


def _observation(
    source_key: str,
    locator: str,
    claimed_value: Any,
    scope: str,
    applicable_from: str | None = None,
) -> dict[str, Any]:
    return {
        "source_key": source_key,
        "locator": locator,
        "claimed_value": claimed_value,
        "scope": scope,
        "applicable_from": applicable_from,
    }


def _comparison(
    code: str,
    category: str,
    status: str,
    interpretation: str,
    *claims: dict[str, Any],
) -> dict[str, Any]:
    return {
        "code": code,
        "category": category,
        "status": status,
        "interpretation": interpretation,
        "reviewing_expert": None,
        "resolution_evidence": None,
        "claims": list(claims),
    }


def dtz_claims() -> list[dict[str, Any]]:
    """Return a fresh ledger of observed, attributed claims for the sealed draft."""
    return [
        _comparison(
            "DTZ_SCALED_LEVELS",
            "ASSESSMENT_SCOPE",
            "SOURCE_VERIFIED",
            "The blueprint spans both language proficiency levels across four skills.",
            _observation(
                "gast-dtz-information",
                "Deutsch-Test für Zuwanderer",
                {"language": "de", "levels": ["A2", "B1"], "skills": 4},
                "Public provider description of the scaled language test",
            ),
            _observation(
                "inttestv-2026-306",
                "Consolidated IntTestV § 3(1)",
                {"levels": ["A2", "B1"], "skills": 4},
                "Statutory scope of the DTZ language assessment",
                "2026-11-01",
            ),
            _observation(
                "bamf-dtz-curriculum-2016",
                "§ 5, printed page 24",
                {"levels": ["A2", "B1"], "role": "LANGUAGE_CONSTRUCT_CONTEXT"},
                "Curriculum relation to the scaled test",
            ),
            _observation(
                "cefr-companion-2020",
                "Chapter 3, Communicative language activities and strategies",
                {"role": "GENERAL_PROFICIENCY_FRAMEWORK"},
                "General CEFR proficiency construct reference",
            ),
        ),
        _comparison(
            "DTZ_INTEGRATION_COURSE_CONTEXT",
            "REGULATORY_CONTEXT",
            "SOURCE_VERIFIED",
            "The language test and civic knowledge test have distinct assessment identities.",
            _observation(
                "intv-2026-218",
                "Consolidated IntV § 17(1)–(2)",
                {"language_test": "DTZ", "orientation_test": "LID", "course_target": "B1"},
                "Integration-course completion requirements and separate authorized tests",
                "2026-07-28",
            ),
        ),
        _comparison(
            "DTZ_LISTENING_STRUCTURE",
            "BLUEPRINT",
            "SOURCE_VERIFIED",
            "Keep communicative groups separate from their individual scored items.",
            _observation(
                "gast-dtz-overview",
                "Prüfungsteile / Hören (#c2224)",
                {"groups": 4, "items": 20, "minutes_approx": 25, "audio_plays": 1},
                "Provider section structure and delivery",
            ),
            _observation(
                "gast-dtz-specimen-2024",
                "Test format, printed page 6",
                [
                    {"SINGLE_CHOICE": 4},
                    {"SINGLE_CHOICE": 5},
                    {"TRUE_FALSE": 4, "SINGLE_CHOICE": 4},
                    {"MATCHING": 3},
                ],
                "Published format grouping, June 2024",
            ),
        ),
        _comparison(
            "DTZ_READING_STRUCTURE",
            "BLUEPRINT",
            "SOURCE_VERIFIED",
            "Keep communicative groups separate from their individual scored items.",
            _observation(
                "gast-dtz-overview",
                "Prüfungsteile / Lesen (#c2224)",
                {"groups": 5, "items": 25, "minutes": 45},
                "Provider section structure and delivery",
            ),
            _observation(
                "gast-dtz-specimen-2024",
                "Test format, printed page 6",
                [
                    {"SINGLE_CHOICE": 5},
                    {"MATCHING": 5},
                    {"TRUE_FALSE": 3, "SINGLE_CHOICE": 3},
                    {"TRUE_FALSE": 3},
                    {"SINGLE_CHOICE": 6},
                ],
                "Published format grouping, June 2024",
            ),
        ),
        _comparison(
            "DTZ_WRITTEN_TIMING",
            "TIMING",
            "SOURCE_VERIFIED",
            "Preserve the per-section allocations and the separate 100-minute written total.",
            _observation(
                "gast-dtz-faq",
                "FAQ für Teilnehmende / changes since 2023 (#c2068)",
                {"listening_minutes_approx": 25, "reading_minutes": 45, "writing_minutes": 30},
                "Provider written sequence with a stop between sections",
            ),
            _observation(
                "inttestv-2026-306",
                "Consolidated IntTestV § 3(1)",
                {"written_total_minutes": 100},
                "Statutory written examination duration",
                "2026-11-01",
            ),
        ),
        _comparison(
            "DTZ_WRITING_STRUCTURE",
            "BLUEPRINT",
            "SOURCE_VERIFIED",
            "One submitted text is assessed by a trained human. The response choice is separate.",
            _observation(
                "gast-dtz-overview",
                "Prüfungsteile / Schreiben (#c2224)",
                {"prompt_options": 2, "responses": 1, "minutes": 30},
                "Written interaction in a functional message",
            ),
            _observation(
                "gast-dtz-faq",
                "FAQ für Teilnehmende / assessment (#c2068)",
                {"judgment": "TRAINED_HUMAN"},
                "Assessment of the submitted written text",
            ),
        ),
        _comparison(
            "DTZ_SPEAKING_STRUCTURE",
            "BLUEPRINT",
            "SOURCE_VERIFIED",
            "Recordings can represent monologue practice. Examiner and paired exchanges "
            "require live interaction for full fidelity.",
            _observation(
                "gast-dtz-overview",
                "Prüfungsteile / Sprechen (#c2224)",
                {
                    "normal_mode": "INTERACTIVE_PAIR_EXAM",
                    "individual_exception": True,
                    "examiner_count": 2,
                    "preparation_period": False,
                },
                "Provider speaking delivery, monologue and dialogue",
            ),
            _observation(
                "gast-dtz-specimen-2024",
                "Test format and oral procedure, printed pages 6 and 28",
                [
                    {"part": "1A", "interaction": "MONOLOGUE"},
                    {"part": "1B", "interaction": "EXAMINER_MEDIATED_EXAM"},
                    {"part": "2A", "interaction": "MONOLOGUE"},
                    {"part": "2B", "interaction": "EXAMINER_MEDIATED_EXAM"},
                    {"part": "3", "interaction": "INTERACTIVE_PAIR_EXAM"},
                ],
                "Observed format for structure and interaction, June 2024",
            ),
            _observation(
                "inttestv-2026-306",
                "Consolidated IntTestV § 7(1)",
                {"qualified_examiner_count": 2},
                "Statutory requirement for oral test staffing",
                "2026-11-01",
            ),
        ),
        _comparison(
            "DTZ_SPEAKING_TASK_COUNT",
            "BLUEPRINT",
            "SOURCE_CONFLICT",
            "The specimen supports a distinction between top-level tasks and assessed parts. "
            "An expert must confirm the terminology and its current applicability.",
            _observation(
                "gast-dtz-overview",
                "Aufbau und Dauer der Prüfung (#c2090)",
                {"oral_tasks": 5},
                "Introductory provider description of the oral examination",
            ),
            _observation(
                "gast-dtz-overview",
                "Prüfungsteile / Sprechen (#c2224)",
                {"oral_tasks": 3},
                "Detailed provider description of the oral examination",
            ),
            _observation(
                "gast-dtz-specimen-2024",
                "Test format, printed page 6",
                {"top_level_groups": 3, "assessed_parts": 5},
                "Evidence for the nested interpretation, June 2024",
            ),
        ),
        _comparison(
            "DTZ_SPEAKING_TIMING",
            "TIMING",
            "REVIEW_REQUIRED",
            "The sources give approximate times. The published format separates the "
            "conversation from examiner deliberation. Confirm current operational timing.",
            _observation(
                "inttestv-2026-306",
                "Consolidated IntTestV § 3(1)",
                {"paired_oral_minutes_approx": 15},
                "Statutory oral duration",
                "2026-11-01",
            ),
            _observation(
                "gast-dtz-overview",
                "Prüfungsteile / Sprechen (#c2224)",
                {"oral_minutes_approx": 16},
                "Provider oral duration",
            ),
            _observation(
                "gast-dtz-specimen-2024",
                "Oral procedure, printed page 28",
                {"conversation_parts_minutes_approx": [4, 6, 6], "appointment_minutes_approx": 20},
                "Approximate conversation and deliberation timing, June 2024",
            ),
        ),
        _comparison(
            "DTZ_WRITING_CRITERIA",
            "RUBRIC",
            "SOURCE_CONFLICT",
            "Terminology may describe overlapping constructs. Independent review must "
            "establish the applicable mapping and assessment policy.",
            _observation(
                "gast-dtz-overview",
                "Bewertung / Schreiben (#c2242)",
                ["Kommunikative Gestaltung", "Ausdruck", "Korrektheit", "Inhalt"],
                "Four criterion names in the overview",
            ),
            _observation(
                "gast-dtz-faq",
                "FAQ für Teilnehmende / assessment (#c2068) and teacher FAQ (#c3438)",
                ["Aufgabenbewältigung", "Kommunikative Gestaltung", "Korrektheit", "Wortschatz"],
                "Four criterion names in the FAQ",
            ),
            _observation(
                "gast-dtz-specimen-2024",
                "Writing criteria and domain results, printed pages 42 and 47",
                {"page_42": "Aufgabenbewältigung", "page_47": "Inhalt"},
                "Historical terminology variation within one edition",
            ),
        ),
        _comparison(
            "DTZ_SPEAKING_CRITERIA",
            "RUBRIC",
            "SOURCE_CONFLICT",
            "Independent assessment is documented. The number and mapping of criterion "
            "families need expert confirmation before a rubric can be approved.",
            _observation(
                "gast-dtz-overview",
                "Bewertung / Sprechen (#c2242)",
                ["Aussprache/Intonation", "Korrektheit", "Ausdruck", "Aufgabenbewältigung"],
                "Four criterion names in the overview",
            ),
            _observation(
                "gast-dtz-faq",
                "FAQ für Teilnehmende / assessment (#c2068)",
                [
                    "Aufgabenbewältigung",
                    "Aussprache/Intonation",
                    "Flüssigkeit",
                    "Korrektheit",
                    "Wortschatz",
                ],
                "Five criterion names and two independent examiners in the FAQ",
            ),
            _observation(
                "gast-dtz-specimen-2024",
                "Speaking criteria and domain results, printed pages 44–48",
                {"criterion_families": 5, "independent_judgments": 2},
                "Historical corroboration of the FAQ count, June 2024",
            ),
        ),
        _comparison(
            "DTZ_OBJECTIVE_SCORING",
            "SCORING",
            "SOURCE_VERIFIED",
            "The objective items feed the combined Listening and Reading domain. "
            "Unverified missing or invalid responses require separate review.",
            _observation(
                "gast-dtz-overview",
                "Bewertung and Ergebnisse (#c2242, #c2248)",
                {"listening_max": 20, "reading_max": 25, "combined_max": 45},
                "One-point objective item and combined results structure",
            ),
            _observation(
                "gast-dtz-specimen-2024",
                "Listening and Reading domain results, printed page 47",
                {"correct_answer_points": 1, "combined_max": 45},
                "Published objective scoring summary, June 2024",
            ),
        ),
        _comparison(
            "DTZ_DOMAIN_BANDS",
            "SCORING",
            "SOURCE_VERIFIED",
            "These are the provider's displayed point bands. Expert review must establish "
            "applicability, score precision, and productive assessment criteria.",
            _observation(
                "gast-dtz-overview",
                "Ergebnisse (#c2248)",
                {
                    "LISTENING_READING": {"BELOW_A2": [0, 19], "A2": [20, 32], "B1": [33, 45]},
                    "WRITING": {"BELOW_A2": [0, 6], "A2": [7, 14], "B1": [15, 20]},
                    "SPEAKING": {"BELOW_A2": [0, 34.5], "A2": [35, 74.5], "B1": [75, 100]},
                },
                "Publicly reported per-domain bands",
            ),
        ),
        _comparison(
            "DTZ_OVERALL_B1",
            "OUTCOME",
            "SOURCE_VERIFIED",
            "The current legal text and both provider pages agree. The predicate can be "
            "exercised with complete synthetic domain levels in the draft preview.",
            _observation(
                "inttestv-2026-306",
                "Art. 1, BGBl. 2026 I Nr. 306 and consolidated IntTestV § 10(1)",
                {"speaking": "B1", "written_one": "B1", "remaining_written_at_least": "A2"},
                "Effective statutory B1 condition",
                "2026-11-01",
            ),
            _observation(
                "gast-dtz-overview",
                "Ergebnisse (#c2248)",
                {"speaking": "B1", "written_one": "B1", "remaining_written_at_least": "A2"},
                "Current public provider B1 description",
            ),
            _observation(
                "gast-dtz-faq",
                "FAQ für Teilnehmende / overall B1 (#c2068)",
                {"speaking": "B1", "written_one": "B1", "remaining_written_at_least": "A2"},
                "Current public participant guidance",
            ),
        ),
        _comparison(
            "DTZ_AGGREGATE_A2",
            "OUTCOME",
            "REVIEW_REQUIRED",
            "The historical provider A2 rule needs confirmation for the current edition. "
            "An unconfirmed overall outcome remains UNDETERMINED.",
            _observation(
                "gast-dtz-specimen-2024",
                "Zertifikat A2, printed page 49",
                {"speaking_at_least": "A2", "one_written_at_least": "A2"},
                "Historical provider description, June 2024",
            ),
            _observation(
                "inttestv-2026-306",
                "Consolidated IntTestV § 10(1)",
                {"current_aggregate_a2_formula": None},
                "The amended paragraph specifies the B1 condition",
                "2026-11-01",
            ),
        ),
        _comparison(
            "DTZ_REGULATORY_CHANGE",
            "REGULATORY_APPLICABILITY",
            "SOURCE_VERIFIED",
            "The June 2024 provider guide allowed a below-A2 result in the remaining written "
            "domain. The 2026 amendment requires at least A2 in that domain. Keep the "
            "historical evidence separate and require human approval for applied rules.",
            _observation(
                "gast-dtz-specimen-2024",
                "Zertifikat B1, printed page 49",
                {"remaining_written_below_a2_allowed": True},
                "Historical examples from the June 2024 edition",
            ),
            _observation(
                "inttestv-2026-306",
                "Arts. 1–2, BGBl. 2026 I Nr. 306",
                {"affected_provision": "IntTestV § 10(1)", "remaining_written_at_least": "A2"},
                "October 2026 legal change to overall B1",
                "2026-11-01",
            ),
        ),
    ]
