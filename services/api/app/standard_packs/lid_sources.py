"""Curated bibliographic evidence and legal observations for the LiD practice draft."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.authorization import TenantContext
from app.knowledge.catalogue import ENTRIES
from app.models import KnowledgeDocumentVersion
from app.standard_packs.dtz_sources import (
    DTZ_ENTRIES,
    _comparison,
    _observation,
    curated_sources,
    register_curated_source,
    resolve_curated_sources,
)
from app.writing_assessment.service import canonical_checksum

BAMF_CATALOGUE = "bamf-lid-catalogue"
BAMF_INTERACTIVE = "bamf-lid-interactive"
INTTESTV = "inttestv-2026-306"
EINBTESTV = "einbtestv-2024-211"
INTV = "intv-2026-218"

_GII = "https://www.gesetze-im-internet.de"
INTTESTV_SCOPE = f"{_GII}/inttestv/__3.html"
INTTESTV_RESULTS = f"{_GII}/inttestv/__10.html"
EINBTESTV_FORMAT = f"{_GII}/einbtestv/__1.html"
EINBTESTV_AGREEMENTS = f"{_GII}/einbtestv/__2.html"
EINBTESTV_ANNEX = f"{_GII}/einbtestv/anlage_1.html"
INTV_COURSE = f"{_GII}/intv/__17.html"
INTV_CENTRES = f"{_GII}/intv/__20a.html"

_BIBLIOGRAPHIC_ONLY = "BIBLIOGRAPHIC_REFERENCE_ONLY"
_REVIEW_REQUIRED = "EXPERT_REVIEW_REQUIRED"
_BERLIN = ZoneInfo("Europe/Berlin")
_LID_PDF = ENTRIES[BAMF_CATALOGUE]
_INTEGRATION_TEST = ENTRIES[INTTESTV]

LID_ENTRIES = {
    entry.key: entry
    for entry in (
        replace(
            _LID_PDF,
            key=BAMF_INTERACTIVE,
            source_code="S3_BAMF_LID_INTERACTIVE",
            title="Test Leben in Deutschland: Fragenkatalog zur Testvorbereitung",
            url="https://oet.bamf.de/ords/oetut/f?p=534:1",
            version_label="Undated official interactive catalogue observed 2026-11-06",
            publication_label="No publication, edition, or effective date stated",
            retrieved_at=datetime(2026, 11, 6, 7, 56, 21, tzinfo=timezone.utc),
            rights_note=(
                "Bibliographic catalogue structure only. No official questions, "
                "answer keys, images, or forms are approved for reuse."
            ),
        ),
        _LID_PDF,
        _INTEGRATION_TEST,
        replace(
            _INTEGRATION_TEST,
            key=EINBTESTV,
            source_code="S3_GERMANY_EINBTESTV",
            title="Einbürgerungstestverordnung (EinbTestV)",
            url=f"{_GII}/einbtestv/BJNR164900008.html",
            version_label="Consolidation amended by Art. 1 of 21.06.2024 (BGBl. 2024 I Nr. 211)",
            publication_label=(
                "Original instrument dated 5 August 2008. Amendment issued 26 June 2024"
            ),
            publication_date=datetime(2024, 6, 26, tzinfo=timezone.utc),
            effective_from=datetime(2024, 7, 1, tzinfo=_BERLIN),
            retrieved_at=datetime(2026, 11, 6, 7, 56, 11, tzinfo=timezone.utc),
            edition_url=(
                "https://www.recht.bund.de/bgbl/1/2024/211/"
                "regelungstext.pdf?__blob=publicationFile&v=3"
            ),
            rights_note=(
                "Legal and structural reference only. The amendment changed Annex 1, "
                "and this registration grants no rights to reproduce questions or images."
            ),
        ),
        DTZ_ENTRIES[INTV],
    )
}


def lid_utc(instant: datetime | None) -> datetime | None:
    if instant is None:
        return None
    if instant.tzinfo is None:
        return instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc)


def _timestamp(instant: datetime | None) -> str | None:
    normalized = lid_utc(instant)
    return normalized.isoformat() if normalized else None


def lid_berlin_date(instant: datetime | None) -> str | None:
    normalized = lid_utc(instant)
    return normalized.astimezone(_BERLIN).date().isoformat() if normalized else None


def lid_sources(session: Session) -> list[dict[str, Any]]:
    sources = curated_sources(session, LID_ENTRIES, retain_reviewed_successors=True)
    for source in sources:
        entry = LID_ENTRIES[source["key"]]
        source.update(
            retrieved_at=_timestamp(entry.retrieved_at),
            effective_from=_timestamp(entry.effective_from),
            effective_date=lid_berlin_date(entry.effective_from),
            publication_date=_timestamp(entry.publication_date),
        )
    return sources


def register_lid_source(
    session: Session, context: TenantContext, key: str
) -> KnowledgeDocumentVersion:
    return register_curated_source(session, context, key, LID_ENTRIES, prefix="LID")


def resolve_lid_sources(
    session: Session, pins: dict[str, tuple[UUID, str]]
) -> dict[str, KnowledgeDocumentVersion]:
    return resolve_curated_sources(
        session,
        pins,
        LID_ENTRIES,
        prefix="LID",
        refresh_after_lock=True,
        retain_reviewed_successors=True,
    )


def lid_source_snapshots(
    documents: dict[str, KnowledgeDocumentVersion],
) -> dict[str, dict[str, Any]]:
    """Project the exact resolved version and its independent bibliographic decision."""
    snapshots = {}
    for key, document in documents.items():
        entry = LID_ENTRIES[key]
        legal = document.legal_metadata
        if legal is None or legal.usage_rights != _BIBLIOGRAPHIC_ONLY:
            raise ValueError("LID_REVIEWED_METADATA_SOURCE_REQUIRED")
        snapshots[key] = {
            "key": key,
            "source_id": str(document.source_id),
            "authority_id": str(document.source.authority_id),
            "document_version_id": str(document.id),
            "source_metadata_checksum": document.metadata_checksum,
            "source_url": document.source_url,
            "canonical_url": entry.url,
            "publisher": entry.publisher,
            "title": entry.title,
            "version_label": document.version_label,
            "publication_label": entry.publication_label,
            "publication_date": _timestamp(document.publication_date),
            "retrieved_at": _timestamp(document.retrieved_at),
            "verified_on": entry.retrieved_at.date().isoformat(),
            "effective_from": _timestamp(document.effective_from),
            "effective_date": lid_berlin_date(document.effective_from),
            "effective_until": _timestamp(document.effective_until),
            "catalogued_rights_holder": entry.rights_holder,
            "rights_holder": legal.rights_holder,
            "licence_type": legal.licence_type,
            "rights_status": legal.usage_rights,
            "legal_review_status": legal.legal_review_status.value,
            "rights_review_actor_id": str(legal.legal_reviewed_by_user_id),
            "rights_reviewed_at": _timestamp(legal.legal_reviewed_at),
            "rights_decision_checksum": legal.decision_checksum,
            "approval_scope": legal.basis_reference,
            "retention_policy": legal.retention_policy,
            "content_mode": document.content_mode,
            "metadata_only": True,
            "metadata_use_allowed": True,
            "ingestion_allowed": False,
            "question_reuse_allowed": False,
            "image_reuse_allowed": False,
            "derivative_use_allowed": False,
            "redistribution_allowed": False,
        }
    return snapshots


def lid_source_citation(
    snapshots: dict[str, dict[str, Any]], key: str, locator: str, reference_url: str
) -> dict[str, Any]:
    source = snapshots[key]
    return {
        "source_key": key,
        "source_version_id": source["document_version_id"],
        "source_metadata_checksum": source["source_metadata_checksum"],
        "rights_decision_checksum": source["rights_decision_checksum"],
        "version_label": source["version_label"],
        "source_url": source["source_url"],
        "reference_url": reference_url,
        "locator": locator,
        "effective_from": source["effective_from"],
        "effective_date": source["effective_date"],
        "effective_until": source["effective_until"],
        "verified_on": source["verified_on"],
        "review_status": _REVIEW_REQUIRED,
        "rights_status": source["rights_status"],
    }


def lid_claims(snapshots: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Seal the S2 ledger format with exact LiD observations and claim checksums."""

    def observe(key: str, locator: str, url: str, value: Any, scope: str) -> dict[str, Any]:
        citation = lid_source_citation(snapshots, key, locator, url)
        observation = {
            **_observation(
                key, locator, value, scope, lid_berlin_date(LID_ENTRIES[key].effective_from)
            ),
            **citation,
        }
        return {**observation, "checksum": canonical_checksum(observation)}

    def claim(
        code: str, category: str, interpretation: str, *observations: dict[str, Any]
    ) -> dict[str, Any]:
        entry = {
            **_comparison(code, category, "SOURCE_VERIFIED", interpretation, *observations),
            "review_status": _REVIEW_REQUIRED,
            "rights_status": _BIBLIOGRAPHIC_ONLY,
        }
        return {**entry, "checksum": canonical_checksum(entry)}

    return [
        claim(
            "LID_CATALOGUE_EDITION",
            "SOURCE_EDITION",
            "The BAMF catalogue edition is dated 7 May 2025.",
            observe(
                BAMF_CATALOGUE,
                "Edition date on PDF cover",
                _LID_PDF.url + "#page=1",
                {"edition_date": "2025-05-07", "publication_date": None},
                "Edition label for the shared BAMF preparation catalogue",
            ),
        ),
        claim(
            "LID_PREPARATION_POOL",
            "QUESTION_POOL",
            "A selected Land uses 300 general items plus its 10 state items. Across all 16 "
            "Länder this gives 160 state entries and 460 entries in aggregate.",
            observe(
                BAMF_INTERACTIVE,
                "Landing-page description and Bundesland selector",
                LID_ENTRIES[BAMF_INTERACTIVE].url,
                {"general": 300, "per_state": 10, "states": 16, "eligible_for_one_state": 310},
                "Public preparation pool and 16 state selector entries",
            ),
            observe(
                EINBTESTV,
                "Annex 1, introductory remarks, Parts I and II",
                EINBTESTV_ANNEX,
                {"general": 300, "per_state": 10, "states": 16},
                "Legal catalogue structure and its 16 state sections",
            ),
        ),
        claim(
            "LID_FORM_COMPOSITION",
            "ASSESSMENT_SCOPE",
            "A practice form follows the 33-item structure, with 30 general and three "
            "from the selected Land. Official questionnaire arrangements are unpublished.",
            observe(
                INTTESTV,
                "IntTestV § 3(2)",
                INTTESTV_SCOPE,
                {"written_test": True, "format_reference": "EinbTestV § 1(1)–(3) and Annex 1"},
                "LiD scope and duration cross-reference",
            ),
            observe(
                EINBTESTV,
                "EinbTestV § 1(2)",
                EINBTESTV_FORMAT,
                {"total": 33, "state_specific": 3, "official_questionnaires_public": False},
                "Official form composition for the participant's residence state",
            ),
        ),
        claim(
            "LID_ANSWER_STRUCTURE",
            "ASSESSMENT_SCOPE",
            "The referenced item format presents four options with one correct answer.",
            observe(
                EINBTESTV,
                "EinbTestV § 1(1), via IntTestV § 3(2)",
                EINBTESTV_FORMAT,
                {"options": 4, "correct_options": 1},
                "Single-selection item format",
            ),
        ),
        claim(
            "LID_DURATION",
            "TIMING",
            "The referenced official duration is 60 minutes under supervision.",
            observe(
                EINBTESTV,
                "EinbTestV § 1(3), via IntTestV § 3(2)",
                EINBTESTV_FORMAT,
                {"minutes": 60, "supervised_official_exam": True},
                "Referenced written-test duration",
            ),
        ),
        claim(
            "LID_ORIENTATION_THRESHOLD",
            "RESULT",
            "The orientation-course numerical benchmark is at least 15 correct out of 33.",
            observe(
                INTTESTV,
                "IntTestV § 10(2)",
                INTTESTV_RESULTS,
                {"minimum_correct": 15, "total": 33},
                "Orientation-course passing requirement",
            ),
        ),
        claim(
            "LID_NATURALIZATION_TEST_THRESHOLD",
            "RESULT",
            "The Einbürgerungstest benchmark is at least 17 correct out of 33 "
            "under supervision within 60 minutes.",
            observe(
                EINBTESTV,
                "EinbTestV § 1(3)",
                EINBTESTV_FORMAT,
                {"minimum_correct": 17, "total": 33, "supervised_minutes": 60},
                "Separate Einbürgerungstest numerical benchmark",
            ),
        ),
        claim(
            "LID_NATURALIZATION_CERTIFICATE_CONDITIONS",
            "ADMINISTRATIVE_CONTEXT",
            "IntTestV § 10(3) refers to 17 of 33 and a qualifying agreement for the Land "
            "of lawful residence. Applicability of an agreement and official recognition "
            "require independent evidence.",
            observe(
                INTTESTV,
                "IntTestV § 10(3)",
                INTTESTV_RESULTS,
                {"correct_cited": 17, "total": 33, "land_bamf_agreement_required": True},
                "Conditional LiD-based certificate under EinbTestV § 1(4)",
            ),
            observe(
                EINBTESTV,
                "EinbTestV § 2(1)",
                EINBTESTV_AGREEMENTS,
                {"administrative_agreement": "Land and BAMF"},
                "Use of designated BAMF test centres under administrative agreement",
            ),
        ),
        claim(
            "LID_INTEGRATION_COURSE_CONTEXT",
            "ADMINISTRATIVE_CONTEXT",
            "Course completion requires both B1 language evidence and the orientation-course "
            "benchmark. BAMF issues the official written completion certificate.",
            observe(
                INTV,
                "IntV § 17(1), (2), (4), and (5)",
                INTV_COURSE,
                {"language_evidence": "B1", "orientation_score_required": True},
                "LiD and DTZ are distinct components of integration-course completion",
            ),
        ),
        claim(
            "LID_TEST_CENTRE_AUTHORIZATION",
            "ADMINISTRATIVE_CONTEXT",
            "Conducting the official LiD test requires its own test-centre authorization.",
            observe(
                INTV,
                "IntV § 20a(1)",
                INTV_CENTRES,
                {"separate_approval_required": True},
                "Authorization of official examination centres",
            ),
        ),
        claim(
            "LID_OCTOBER_2026_AMENDMENT_SCOPE",
            "SOURCE_EDITION",
            "The amendment took effect on 1 November 2026 and replaced the DTZ paragraph "
            "only. It did not amend the LiD provisions.",
            observe(
                INTTESTV,
                "BGBl. 2026 I Nr. 306, Articles 1 and 2",
                str(_INTEGRATION_TEST.edition_url),
                {"replaced": "IntTestV § 10(1)", "effective_date": "2026-11-01"},
                "Scope and effective date of the October 2026 amendment",
            ),
        ),
    ]
