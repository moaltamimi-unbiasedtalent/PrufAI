"""Writer authority unit invariants; concurrency is covered by real PostgreSQL."""

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.authorization import TenantContext
from app.models.enums import CandidateAttemptStatus
from app.runtime.service import CandidateRuntimeService, RuntimeConflict, RuntimeExpired
from app.speaking_audio.service import SpeakingAudioService


@pytest.fixture
def writer_case(monkeypatch):
    owner = uuid.uuid4()
    context = TenantContext(owner, uuid.uuid4(), None)
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        candidate_user_id=owner,
        organisation_id=context.organisation_id,
        runtime_plan={"lid_practice": {}},
        status=CandidateAttemptStatus.READY,
        deadline_at=None,
        writer_client_hash=None,
        writer_generation=0,
        writer_claimed_at=None,
    )
    receipts = []
    monkeypatch.setattr(SpeakingAudioService, "lock_attempt", lambda *_: attempt)
    monkeypatch.setattr("app.runtime.service.lid.require_attempt_environment", lambda *_: None)
    monkeypatch.setattr("app.runtime.service.pilot.require_attempt", lambda *_: None)
    monkeypatch.setattr(CandidateRuntimeService, "_audit", lambda *a, **k: receipts.append((a, k)))
    return SimpleNamespace(
        attempt=attempt,
        context=context,
        receipts=receipts,
        session=SimpleNamespace(flush=lambda: None),
    )


def claim(case, identity, generation, acknowledge=False):
    return CandidateRuntimeService.claim_writer(
        case.session,
        case.context,
        case.attempt.id,
        client_id=identity,
        expected_generation=generation,
        acknowledge_takeover=acknowledge,
    )


def test_initial_claim_and_lost_reply_retry_are_idempotent_and_private(writer_case):
    identity = uuid.uuid4()
    assert claim(writer_case, identity, 0) == {
        "policy": "SINGLE_WRITER",
        "generation": 1,
        "claimed": True,
    }
    assert claim(writer_case, identity, 0)["generation"] == 1
    assert len(writer_case.receipts) == 1
    assert (
        writer_case.attempt.writer_client_hash == hashlib.sha256(str(identity).encode()).hexdigest()
    )
    assert str(identity) not in json.dumps(
        CandidateRuntimeService.writer_projection(writer_case.attempt)
    )
    assert str(identity) not in str(writer_case.receipts)
    assert writer_case.attempt.writer_client_hash not in str(writer_case.receipts)


def test_other_partition_needs_explicit_current_generation_confirmation(writer_case):
    first, other = uuid.uuid4(), uuid.uuid4()
    claim(writer_case, first, 0)
    for expected, acknowledge in ((1, False), (0, True)):
        with pytest.raises(RuntimeConflict, match="RUNTIME.WRITER_"):
            claim(writer_case, other, expected, acknowledge)
    assert claim(writer_case, other, 1, True)["generation"] == 2
    with pytest.raises(RuntimeConflict, match="WRITER_CHANGED"):
        CandidateRuntimeService._require_writer(writer_case.attempt, first, 1)
    CandidateRuntimeService._require_writer(writer_case.attempt, other, 2)
    assert len(writer_case.receipts) == 2


def test_unclaimed_in_progress_upgrade_needs_confirmation(writer_case):
    writer_case.attempt.status = CandidateAttemptStatus.IN_PROGRESS
    marker = uuid.uuid4()
    with pytest.raises(RuntimeConflict, match="CONFIRMATION_REQUIRED"):
        claim(writer_case, marker, 0)
    assert claim(writer_case, marker, 0, True)["generation"] == 1


@pytest.mark.parametrize("generation", [None, 0, 2])
def test_missing_or_stale_mutation_context_is_denied(writer_case, generation):
    identity = uuid.uuid4()
    claim(writer_case, identity, 0)
    with pytest.raises(RuntimeConflict, match="WRITER_"):
        CandidateRuntimeService._require_writer(writer_case.attempt, identity, generation)
    with pytest.raises(RuntimeConflict, match="CONTEXT_REQUIRED"):
        CandidateRuntimeService._require_writer(writer_case.attempt, None, 1)


@pytest.mark.parametrize(
    "status", [CandidateAttemptStatus.SUBMITTED, CandidateAttemptStatus.EXPIRED]
)
def test_terminal_attempt_cannot_transfer_authority(writer_case, status):
    writer_case.attempt.status = status
    with pytest.raises(RuntimeExpired):
        claim(writer_case, uuid.uuid4(), 0, True)
    assert writer_case.attempt.writer_generation == 0
    assert writer_case.receipts == []


def test_server_deadline_and_generic_non_lid_behavior_are_preserved(writer_case):
    writer_case.attempt.status = CandidateAttemptStatus.IN_PROGRESS
    writer_case.attempt.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(RuntimeExpired):
        claim(writer_case, uuid.uuid4(), 0, True)
    assert writer_case.attempt.status == CandidateAttemptStatus.EXPIRED
    generic = SimpleNamespace(runtime_plan={"modules": []})
    CandidateRuntimeService._require_writer(generic, None, None)
