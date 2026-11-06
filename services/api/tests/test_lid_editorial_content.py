"""Static inventory checks for the AI-assisted S4 editorial drafts."""

from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.authorization import TenantContext
from app.lid_editorial.content import (
    GENERAL,
    ORIGINAL_DRAFTS,
    S4_ENTRIES,
    STATE,
    editorial_replacement_source,
    editorial_source_snapshot,
    editorial_source_version,
    register_editorial_source,
)
from app.lid_practice.fixture_content import BERLIN_SCENARIOS, GENERAL_SCENARIOS
from app.models import KnowledgeDocumentVersion
from app.question_bank.schemas import MultipleChoiceContent
from app.services.knowledge import KnowledgeRegistryService

_CREATE = frozenset(
    {"standards.authority.manage", "standards.source.manage", "standards.document.manage"}
)
_REVIEW = frozenset({"standards.document.review"})
_RETRIEVED = datetime(2025, 4, 1, tzinfo=timezone.utc)


def test_original_editorial_inventory_has_one_distinct_key_per_four_options() -> None:
    assert Counter((draft.scope, draft.jurisdiction) for draft in ORIGINAL_DRAFTS) == {
        (GENERAL, "DE"): 30,
        (STATE, "DE-BE"): 3,
    }
    assert len({draft.code for draft in ORIGINAL_DRAFTS}) == 33
    assert len({draft.prompt.casefold() for draft in ORIGINAL_DRAFTS}) == 33
    fixture_prompts = {item.prompt.casefold() for item in GENERAL_SCENARIOS + BERLIN_SCENARIOS}
    for draft in ORIGINAL_DRAFTS:
        content = MultipleChoiceContent.model_validate(draft.question_content())
        labels = {option.display_text.casefold() for option in content.options}
        assert len(content.options) == len(labels) == 4
        assert content.correct_option_ids == [content.options[draft.correct_index].option_id]
        assert draft.prompt.casefold() not in fixture_prompts
        assert draft.rationale.strip()


def test_original_editorial_claims_pin_scoped_metadata_and_recheck_dates() -> None:
    assert {draft.claim.source_key for draft in ORIGINAL_DRAFTS} == set(S4_ENTRIES)
    assert len({draft.claim.claim_code for draft in ORIGINAL_DRAFTS}) == 33
    assert len({(draft.claim.source_key, draft.claim.locator) for draft in ORIGINAL_DRAFTS}) == 33
    for draft in ORIGINAL_DRAFTS:
        claim = draft.claim
        entry = S4_ENTRIES[claim.source_key]
        assert claim.reference_url.startswith("https://")
        assert urlparse(claim.reference_url).hostname == urlparse(entry.url).hostname
        assert entry.public()["content_mode"] == "METADATA_ONLY"
        assert entry.public()["external_content_rights"] == "NOT_APPROVED"
        assert entry.retrieved_at.date() == claim.last_verified < claim.recheck_after
        assert (claim.source_key == "vbb") == (draft.scope == STATE)
        assert claim.fact.strip()
        if claim.historical_event_date:
            assert claim.historical_event_date < claim.last_verified
            assert claim.applicability_as_of is None
            assert claim.legal_effective_from is None
        else:
            assert claim.applicability_as_of == claim.last_verified
            assert claim.legal_effective_from is not None
            assert claim.legal_effective_from <= claim.applicability_as_of
            assert claim.effective_date_reference


def _record_source_rights(
    session: Session,
    reviewer: TenantContext,
    version: KnowledgeDocumentVersion,
    decision: str = "METADATA_ONLY",
) -> None:
    assert version.metadata_checksum
    KnowledgeRegistryService().review_rights(
        session,
        reviewer,
        version_id=version.id,
        expected_metadata_checksum=version.metadata_checksum,
        decision=decision,
        basis_reference="Unit fixture for bibliographic review",
        rights_holder="Official publisher",
        licence_type="Bibliographic reference only",
        retention_policy="Bibliographic record",
    )


def test_editorial_replacement_requires_exact_current_reviewed_issuer_lineage(
    session: Session,
) -> None:
    creator = TenantContext(uuid4(), None, None, platform_permissions=_CREATE)
    reviewer = TenantContext(uuid4(), None, None, platform_permissions=_REVIEW)
    registry = KnowledgeRegistryService()
    original = register_editorial_source(session, creator, "gg")
    assert original.metadata_checksum
    original_snapshot = editorial_source_snapshot("gg", original)
    assert original_snapshot["rights_status"] == "REVIEW_REQUIRED"
    assert editorial_source_version(session, "gg", original.id, original.metadata_checksum)

    replacement = registry.create_version(
        session,
        creator,
        source_id=original.source_id,
        supersedes_document_version_id=original.id,
        version_label="Later edition, unit fixture",
        source_url="https://www.gesetze-im-internet.de/gg/",
        retrieved_at=_RETRIEVED,
    )
    assert replacement.metadata_checksum
    with pytest.raises(ValueError, match="LID_EDITORIAL_REVIEWED_METADATA_SOURCE_REQUIRED"):
        editorial_replacement_source(
            session, "gg", original.id, replacement.id, replacement.metadata_checksum
        )
    _record_source_rights(session, reviewer, replacement)
    with pytest.raises(ValueError, match="LID_EDITORIAL_EXACT_SOURCE_PIN_MISMATCH"):
        editorial_replacement_source(session, "gg", original.id, replacement.id, "0" * 64)
    with pytest.raises(ValueError, match="LID_EDITORIAL_SOURCE_REPLACEMENT_LINEAGE_REQUIRED"):
        editorial_replacement_source(
            session, "gg", replacement.id, replacement.id, replacement.metadata_checksum
        )
    with pytest.raises(ValueError, match="LID_EDITORIAL_SOURCE_SUCCESSOR_REVIEWED"):
        editorial_source_version(session, "gg", original.id, original.metadata_checksum)
    with pytest.raises(ValueError, match="LID_EDITORIAL_SOURCE_SUCCESSOR_REVIEWED"):
        register_editorial_source(session, creator, "gg")

    adopted = editorial_replacement_source(
        session, "gg", original.id, replacement.id, replacement.metadata_checksum
    )
    assert adopted.id == replacement.id
    assert editorial_source_version(
        session, "gg", adopted.id, replacement.metadata_checksum, require_reviewed=True
    )
    snapshot = editorial_source_snapshot("gg", adopted)
    assert snapshot["supersedes_version_id"] == str(original.id)
    assert snapshot["edition_url"] == replacement.source_url
    assert snapshot["version_label"] == replacement.version_label
    assert snapshot["rights_status"] == "BIBLIOGRAPHIC_REFERENCE_ONLY"
    assert snapshot["rights_decision_checksum"]
    assert original_snapshot["version_id"] == str(original.id)
    assert original_snapshot["rights_status"] == "REVIEW_REQUIRED"


def test_editorial_replacement_cannot_adopt_unlinked_or_off_issuer_versions(
    session: Session,
) -> None:
    creator = TenantContext(uuid4(), None, None, platform_permissions=_CREATE)
    reviewer = TenantContext(uuid4(), None, None, platform_permissions=_REVIEW)
    registry = KnowledgeRegistryService()
    original = register_editorial_source(session, creator, "gg")
    assert original.metadata_checksum
    _record_source_rights(session, reviewer, original)

    unlinked = registry.create_version(
        session,
        creator,
        source_id=original.source_id,
        version_label="Unlinked edition, unit fixture",
        source_url="https://www.gesetze-im-internet.de/gg/",
        retrieved_at=_RETRIEVED,
    )
    assert unlinked.metadata_checksum
    _record_source_rights(session, reviewer, unlinked)
    with pytest.raises(ValueError, match="LID_EDITORIAL_SOURCE_REPLACEMENT_LINEAGE_REQUIRED"):
        editorial_replacement_source(
            session, "gg", original.id, unlinked.id, unlinked.metadata_checksum
        )

    offsite = registry.create_version(
        session,
        creator,
        source_id=original.source_id,
        supersedes_document_version_id=original.id,
        version_label="Off-issuer edition, unit fixture",
        source_url="https://www.gesetze-im-internet.de.example.invalid/gg/",
        retrieved_at=_RETRIEVED,
    )
    assert offsite.metadata_checksum
    _record_source_rights(session, reviewer, offsite)
    with pytest.raises(ValueError, match="LID_EDITORIAL_SOURCE_REPLACEMENT_ISSUER_MISMATCH"):
        editorial_replacement_source(
            session, "gg", original.id, offsite.id, offsite.metadata_checksum
        )
    assert editorial_source_version(
        session, "gg", original.id, original.metadata_checksum, require_reviewed=True
    )

    official = registry.create_version(
        session,
        creator,
        source_id=original.source_id,
        supersedes_document_version_id=original.id,
        version_label="Official later edition, unit fixture",
        source_url="https://www.gesetze-im-internet.de/gg/",
        retrieved_at=_RETRIEVED,
    )
    assert official.metadata_checksum
    _record_source_rights(session, reviewer, official)
    newest = registry.create_version(
        session,
        creator,
        source_id=original.source_id,
        supersedes_document_version_id=official.id,
        version_label="Official newest edition, unit fixture",
        source_url="https://www.gesetze-im-internet.de/gg/",
        retrieved_at=_RETRIEVED,
    )
    _record_source_rights(session, reviewer, newest)
    _record_source_rights(session, reviewer, newest, "WITHDRAW")
    with pytest.raises(ValueError, match="LID_EDITORIAL_SOURCE_SUCCESSOR_REVIEWED"):
        editorial_source_version(
            session, "gg", official.id, official.metadata_checksum, require_reviewed=True
        )
