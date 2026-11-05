"""Real PostgreSQL acceptance for promoted original synthetic regressions."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from app.auth.api import auth_settings_dependency
from app.authorization import AuthorizationError
from app.config import Settings, get_settings
from app.db import get_db_session
from app.evaluation import regression_registry as registry
from app.evaluation import service as foundation
from app.evaluation.expert_service import create_reference
from app.evaluation.regression_comparison import (
    RegressionComparisonInput,
    execute_regression_comparison,
)
from app.evaluation.regression_review import ComparisonReviewInput, comparison_detail, record_review
from app.evaluation.schemas import DatasetVersionInput, RunConfiguration, RunRequest
from app.main import app
from app.models import (
    EvaluationDatasetVersion,
    EvaluationReview,
    Organisation,
)
from app.models.enums import OrganisationType
from tests.alembic_helpers import repository_alembic_head
from tests.test_evaluation_foundation_postgresql_integration import (
    EvaluationScenario,
    dataset_body,
    technical_case,
)
from tests.test_expert_references_postgresql_integration import PASSWORD, ExpertScenario
from tests.test_expert_references_postgresql_integration import expert as expert

REQUIRED = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRED,
    reason="Dynamic regression acceptance requires guarded Alembic PostgreSQL",
)
CONTROL = "/api/v1/platform/control-center/evaluation"
EVALUATION = "/api/v1/platform/evaluation"


def _case(*, check="audio_identity", split="VALIDATION") -> dict[str, Any]:
    marker = uuid.uuid4().hex
    case = technical_case(case_id="synthetic-regression-" + marker)
    digest = hashlib.sha256(marker.encode()).hexdigest()
    identity = {"audio_sha256": digest, "known_lineage_reference": "synthetic:lineage:" + marker}
    case["split"] = split
    case["input"].update(
        text="Original private synthetic test evidence " + marker,
        audio_sha256=digest,
        evidence_identity=identity,
    )
    case["reference"].update(evidence_identity=identity, text="Synthetische Referenz " + marker)
    case["synthetic_observation"].update(
        audio_sha256="0" * 64 if check == "audio_identity" else digest,
        text=case["reference"]["text"],
        evidence_identity=identity,
        language="fr" if check == "language_compliance" else "de",
    )
    return case


def _good(case):
    item = copy.deepcopy(case)
    item["synthetic_observation"].update(audio_sha256=item["input"]["audio_sha256"], language="de")
    return item


def _pin(row):
    return registry.ArtifactPin(id=row.id, checksum=row.checksum)


def _source(
    expert,
    cases,
    *,
    owner=None,
    skill="TRANSCRIPTION",
    normalization="NFC_WHITESPACE",
):
    with expert.factory() as session:
        creator = expert.context(session, "creator")
        approver = expert.context(session, "approver")
        payload = dataset_body(cases=cases)
        payload.update(skill=skill, organisation_id=owner)
        version = foundation.create_dataset_version(
            session,
            creator,
            DatasetVersionInput.model_validate(payload),
        )
        for action, context in (("submit", creator), ("approve", approver), ("publish", creator)):
            foundation.transition_dataset(
                session, context, version.id, action, version.checksum, "synthetic:source-review"
            )
        run = foundation.execute_run(
            session,
            expert.context(session, "adjudicator"),
            RunRequest(
                operation_id=uuid.uuid4(),
                dataset_version_id=version.id,
                dataset_checksum=version.checksum,
                configuration=RunConfiguration.model_validate(
                    {
                        "dataset_split": cases[0]["split"],
                        "standard_pack_version_id": str(expert.pack_id),
                        "standard_pack_checksum": expert.pack_checksum,
                        "evaluation_method": "DETERMINISTIC_REFERENCE_COMPARISON",
                        "evaluation_method_version": "1",
                        "execution_adapter": "SYNTHETIC_OBSERVATION_REPLAY_V1",
                        "metric_configuration": {"normalization": normalization},
                    }
                ),
            ),
        )
        results = {row.case_id: row for row in foundation.verify_run(session, run)}
        session.commit()
        return version, run, results


def _promotion(run, result, check="audio_identity"):
    return registry.PromotionRequest(
        operation_id=uuid.uuid4(),
        source_run=_pin(run),
        source_result=_pin(result),
        failed_check=check,
    )


def _transition(expert, session, row, action, actor, reference=None):
    return registry.transition_regression_version(
        session,
        expert.context(session, actor),
        row.id,
        action,
        registry.RegistryTransitionRequest(
            expected_checksum=row.checksum, review_reference=reference
        ),
    )


def _publish(expert, session, row):
    _transition(expert, session, row, "submit", "creator")
    _transition(expert, session, row, "approve", "approver", "synthetic:independent-review")
    return _transition(expert, session, row, "publish", "creator")


def _suite(expert, session, rows, predecessor=None):
    code = "synthetic-regression-suite-" + uuid.uuid4().hex
    if predecessor is not None:
        code = foundation.version_summary(session, predecessor)["code"]
    request = registry.SuiteVersionRequest(
        operation_id=uuid.uuid4(),
        code=code,
        case_pins=[_pin(row) for row in rows],
        predecessor=_pin(predecessor) if predecessor is not None else None,
    )
    version = registry.create_suite(
        session,
        expert.context(session, "creator"),
        request,
        predecessor.dataset_id if predecessor is not None else None,
    )
    return request, _publish(expert, session, version)


def _compare(expert, session, suite, baseline, candidate):
    return execute_regression_comparison(
        session,
        expert.context(session, "examiner-a"),
        RegressionComparisonInput(
            operation_id=uuid.uuid4(),
            suite_version=_pin(suite),
            baseline_run=_pin(baseline),
            candidate_run=_pin(candidate),
            dimension="TECHNICAL_FIXTURE",
        ),
    )


def test_dynamic_head_promotion_publication_multicase_suite_and_measured_comparison(expert):
    first, second = _case(), _case(check="language_compliance")
    source, baseline, failures = _source(expert, [first, second])
    _, candidate, _ = _source(expert, [_good(first), _good(second)])
    assert baseline.hard_gate == "FAIL" and candidate.hard_gate == "PASS"
    assert failures[first["case_id"]].result["metrics"]["audio_identity_rate"]["numerator"] == 0
    assert failures[first["case_id"]].result["metrics"]["audio_identity_rate"]["denominator"] == 1
    with expert.factory() as session:
        assert session.scalars(text("SELECT version_num FROM alembic_version")).all() == [
            repository_alembic_head()
        ]
        promoted = []
        for raw, key in ((first, "audio_identity"), (second, "language_compliance")):
            payload = _promotion(baseline, failures[raw["case_id"]], key)
            row = registry.promote_case(session, expert.context(session), payload)
            assert row.status == "DRAFT" and row.evidence_class == "SYNTHETIC_TEST"
            assert row.provenance["source_basis"] == "MEASURED_TECHNICAL_FAILURE"
            assert row.provenance["source"]["dataset_version_id"] == str(source.id)
            assert row.provenance["source"]["result_id"] == str(failures[raw["case_id"]].id)
            assert row.cases[0]["input"]["audio_sha256"] == raw["input"]["audio_sha256"]
            assert row.cases[0]["reference"]["text"] == raw["reference"]["text"]
            assert (
                "synthetic_observation" not in row.cases[0]
                and "execution_job_id" not in row.cases[0]
            )
            promoted.append(_publish(expert, session, row))
            assert _transition(expert, session, row, "submit", "creator").status == "PUBLISHED"
            assert (
                _transition(
                    expert, session, row, "approve", "approver", "synthetic:independent-review"
                ).status
                == "PUBLISHED"
            )
        _, v1 = _suite(expert, session, promoted[:1])
        request, v2 = _suite(expert, session, promoted, predecessor=v1)
        session.commit()
        assert v2.dataset_id == v1.dataset_id and v2.version_number == 2
        assert v1.case_count == 1 and v2.case_count == 2
        assert v2.provenance["predecessor"] == _pin(v1).model_dump(mode="json")
        assert (
            registry.create_suite(session, expert.context(session), request, v1.dataset_id).id
            == v2.id
        )
        with pytest.raises(foundation.EvaluationConflict, match="PREDECESSOR_CONFLICT"):
            registry.create_suite(
                session,
                expert.context(session),
                request.model_copy(update={"operation_id": uuid.uuid4()}),
                v1.dataset_id,
            )
        session.rollback()
        registry.verify_regression_suite(session, v1)
        with pytest.raises(AuthorizationError, match="INDEPENDENT_EXECUTOR"):
            registry.resolve_regression_suite(
                session, expert.context(session, "creator"), v2.id, v2.checksum
            )
        session.rollback()
        run = _compare(expert, session, v2, baseline, candidate)
        session.commit()
        detail = comparison_detail(session, expert.context(session, "examiner-b"), run)
        comparison = detail["regression"]
        assert comparison["comparison_status"] == "COMPARABLE"
        assert comparison["technical_status"] == "PASS"
        assert comparison["empirical_status"] == "UNCONFIGURED"
        assert comparison["reference_quality_status"] == "NOT_AVAILABLE"
        assert comparison["paired_case_count"] == comparison["expected_case_count"] == 2
        assert comparison["execution_class"] == "TEST_ONLY"
        assert comparison["evidence_class"] == "SYNTHETIC_TEST"
        assert detail["production_activation"] is False
        failed_keys = set()
        for finding in comparison["case_findings"]:
            # Findings are sorted by stable case ID, independent of suite insertion order.
            assert finding["baseline_status"] == "FAIL" and finding["candidate_status"] == "PASS"
            failed = [check for check in finding["checks"] if check["baseline"]["status"] == "FAIL"]
            assert len(failed) == 1
            failed_keys.add(failed[0]["code"])
            assert (
                failed[0]["baseline"]["numerator"] == 0
                and failed[0]["baseline"]["denominator"] == 1
            )
            assert failed[0]["candidate"]["numerator"] == failed[0]["candidate"]["denominator"] == 1
        assert failed_keys == {"audio_identity", "language_compliance"}
        rendered = json.dumps(detail)
        assert first["input"]["text"] not in rendered and first["reference"]["text"] not in rendered
        assert first["input"]["audio_sha256"] not in rendered


def test_grouped_comparison_retains_each_exact_source_run_version_and_result(expert):
    baselines, candidates = [], []
    for check in ("audio_identity", "language_compliance"):
        raw = _case(check=check)
        source_version, source_run, failures = _source(expert, [raw])
        candidate_version, candidate_run, successes = _source(expert, [_good(raw)])
        assert source_run.hard_gate == "FAIL" and candidate_run.hard_gate == "PASS"
        baselines.append((source_version, source_run, failures[raw["case_id"]], check))
        candidates.append((candidate_version, candidate_run, successes[raw["case_id"]]))
    with expert.factory() as session:
        promoted = [
            _publish(
                expert,
                session,
                registry.promote_case(
                    session, expert.context(session), _promotion(source_run, failure, check)
                ),
            )
            for _, source_run, failure, check in baselines
        ]
        _, suite = _suite(expert, session, promoted)
        executor = expert.context(session, "examiner-a")
        request = RegressionComparisonInput(
            operation_id=uuid.uuid4(),
            suite_version=_pin(suite),
            baseline_run=_pin(baselines[0][1]),
            baseline_additional_runs=[_pin(baselines[1][1])],
            candidate_run=_pin(candidates[0][1]),
            candidate_additional_runs=[_pin(candidates[1][1])],
            dimension="TECHNICAL_FIXTURE",
        )
        run = execute_regression_comparison(session, executor, request)
        session.commit()
        assert execute_regression_comparison(session, executor, request).id == run.id
        comparison = comparison_detail(session, expert.context(session, "examiner-b"), run)[
            "regression"
        ]
        assert comparison["paired_case_count"] == comparison["expected_case_count"] == 2
        assert comparison["comparison_status"] == "COMPARABLE"
        assert comparison["technical_status"] == "PASS"
        assert comparison["empirical_status"] == "UNCONFIGURED"
        assert comparison["evidence_class"] == "SYNTHETIC_TEST"
        for side, components in (("baseline", baselines), ("candidate", candidates)):
            assert {
                (item["run"]["id"], item["dataset_version"]["id"], item["case_count"])
                for item in comparison[side + "_components"]
            } == {(str(component[1].id), str(component[0].id), 1) for component in components}
            for finding in comparison["case_findings"]:
                assert finding["baseline_status"] == "FAIL"
                assert finding["candidate_status"] == "PASS"
                version, execution, result = next(
                    component[:3]
                    for component in components
                    if str(component[2].id) == finding[side + "_result"]["id"]
                )
                assert finding[side + "_result"] == _pin(result).model_dump(mode="json")
                provenance = finding[side + "_execution"]
                assert provenance["run"] == _pin(execution).model_dump(mode="json")
                assert provenance["dataset_version"] == _pin(version).model_dump(mode="json")
                assert provenance["input_checksum"] == result.input_checksum
                assert provenance["observation_checksum"] == result.observation_checksum
        assert "Original private synthetic test evidence" not in json.dumps(comparison)


def test_suite_cannot_count_two_cases_with_the_same_known_lineage(expert):
    first, second = _case(), _case()
    lineage = first["input"]["evidence_identity"]["known_lineage_reference"]
    for key in ("input", "reference", "synthetic_observation"):
        second[key]["evidence_identity"]["known_lineage_reference"] = lineage
    _, baseline, failures = _source(expert, [first, second])
    with expert.factory() as session:
        promoted = [
            _publish(
                expert,
                session,
                registry.promote_case(
                    session,
                    expert.context(session),
                    _promotion(baseline, failures[raw["case_id"]]),
                ),
            )
            for raw in (first, second)
        ]
        assert (
            promoted[0].provenance["cohort_fingerprint"]
            != promoted[1].provenance["cohort_fingerprint"]
        )
        with pytest.raises(foundation.EvaluationConflict, match="DUPLICATE_LINEAGE"):
            _suite(expert, session, promoted)


def test_quality_recommendation_is_independent_idempotent_immutable_and_not_release(expert):
    raw = _case()
    _, baseline, failures = _source(expert, [raw])
    _, candidate, _ = _source(expert, [_good(raw)])
    with expert.factory() as session:
        row = registry.promote_case(
            session, expert.context(session), _promotion(baseline, failures[raw["case_id"]])
        )
        _, suite = _suite(expert, session, [_publish(expert, session, row)])
        run = _compare(expert, session, suite, baseline, candidate)
        session.commit()
        payload = ComparisonReviewInput(
            operation_id=uuid.uuid4(),
            expected_checksum=run.checksum,
            decision="ACCEPTED",
            review_reference="synthetic:technical-evidence-reviewed",
        )
        for actor in ("creator", "approver", "adjudicator", "examiner-a", "candidate"):
            with pytest.raises(AuthorizationError):
                record_review(session, expert.context(session, actor), run.id, payload)
            session.rollback()
        reviewer = expert.context(session, "examiner-b")
        review = record_review(session, reviewer, run.id, payload)
        session.commit()
        assert record_review(session, reviewer, run.id, payload).id == review.id
        with pytest.raises(foundation.EvaluationConflict, match="OPERATION_CONFLICT"):
            record_review(
                session, reviewer, run.id, payload.model_copy(update={"decision": "REJECTED"})
            )
        session.rollback()
        assert (
            session.scalar(
                select(func.count())
                .select_from(EvaluationReview)
                .where(EvaluationReview.run_id == run.id)
            )
            == 1
        )
        detail = comparison_detail(session, reviewer, run)
        assert detail["run"]["review_status"] == "ACCEPTED"
        assert detail["release_recommendation"] == "NOT_READY"
        assert detail["production_activation"] is False
    with pytest.raises(DBAPIError), expert.engine.begin() as connection:
        connection.execute(
            update(EvaluationReview.__table__)
            .where(EvaluationReview.id == review.id)
            .values(decision="REJECTED")
        )


def test_concurrent_promotion_retry_and_semantic_duplicate_failure(expert):
    raw = _case()
    _, baseline, failures = _source(expert, [raw])
    _, rerun, rerun_failures = _source(expert, [raw])
    payload = _promotion(baseline, failures[raw["case_id"]])

    def attempt(_):
        with expert.factory() as session:
            row = registry.promote_case(session, expert.context(session), payload)
            session.commit()
            return row.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(attempt, range(2))
    assert first == second
    with expert.factory() as session:
        with pytest.raises(foundation.EvaluationConflict, match="OPERATION_CONFLICT"):
            registry.promote_case(
                session,
                expert.context(session),
                payload.model_copy(update={"failed_check": "schema_compliance"}),
            )
        session.rollback()
        with pytest.raises(foundation.EvaluationConflict, match="ALREADY_PROMOTED"):
            registry.promote_case(
                session, expert.context(session), _promotion(rerun, rerun_failures[raw["case_id"]])
            )


def test_wrong_source_stale_approval_rejection_and_immutable_history(expert):
    raw = _case()
    _, baseline, failures = _source(expert, [raw])
    _, candidate, successes = _source(expert, [_good(raw)])
    payload = _promotion(baseline, failures[raw["case_id"]])
    with expert.factory() as session:
        context = expert.context(session)
        for change, category in (
            (
                {"source_run": registry.ArtifactPin(id=baseline.id, checksum="0" * 64)},
                "FAILED_RUN_REQUIRED",
            ),
            ({"source_result": _pin(successes[raw["case_id"]])}, "RESULT_PIN_MISMATCH"),
            ({"source_run": _pin(candidate)}, "FAILED_RUN_REQUIRED"),
            ({"failed_check": "schema_compliance"}, "MEASURED_TECHNICAL_FAILURE_REQUIRED"),
            (
                {
                    "source_run": _pin(candidate),
                    "source_result": _pin(successes[raw["case_id"]]),
                    "failed_check": None,
                },
                "APPROVED_REFERENCE_OBSERVATION_REQUIRED",
            ),
        ):
            with pytest.raises(ValueError, match=category):
                registry.promote_case(session, context, payload.model_copy(update=change))
            session.rollback()
        row = registry.promote_case(session, context, payload)
        _transition(expert, session, row, "submit", "creator")
        session.commit()
        with pytest.raises(AuthorizationError, match="INDEPENDENT_REVIEW"):
            _transition(expert, session, row, "approve", "creator", "synthetic:self")
        session.rollback()
        with pytest.raises(foundation.EvaluationConflict, match="CHECKSUM_MISMATCH"):
            registry.transition_regression_version(
                session,
                expert.context(session, "approver"),
                row.id,
                "approve",
                registry.RegistryTransitionRequest(
                    expected_checksum="0" * 64, review_reference="synthetic:stale"
                ),
            )
        session.rollback()
        rejected = _transition(expert, session, row, "reject", "approver", "synthetic:rejection")
        session.commit()
        rejected_id = rejected.id
        timestamp = rejected.rejected_at
        assert rejected.status == "REJECTED" and rejected.rejected_by == expert.actors["approver"]
        assert (
            _transition(
                expert, session, rejected, "reject", "approver", "synthetic:rejection"
            ).rejected_at
            == timestamp
        )
        with pytest.raises(foundation.EvaluationConflict, match="STATE_CONFLICT"):
            _transition(expert, session, rejected, "publish", "creator")
        session.rollback()
        with pytest.raises(foundation.EvaluationConflict, match="PUBLISHED_CASE_PIN_REQUIRED"):
            registry.create_suite(
                session,
                context,
                registry.SuiteVersionRequest(
                    operation_id=uuid.uuid4(),
                    code="rejected-regression-" + uuid.uuid4().hex,
                    case_pins=[_pin(rejected)],
                ),
            )
        session.rollback()
        rejected.provenance["source"]["observation_checksum"] = "0" * 64
        with pytest.raises(ValueError, match="IMMUTABLE"):
            session.commit()
        session.rollback()
    with pytest.raises(DBAPIError), expert.engine.begin() as connection:
        connection.execute(
            update(EvaluationDatasetVersion.__table__)
            .where(EvaluationDatasetVersion.id == rejected_id)
            .values(status="APPROVED")
        )


def test_held_out_content_and_known_lineage_rejected_on_legacy_and_expert_ingress(expert):
    raw = _case(split="HELD_OUT")
    _, baseline, failures = _source(expert, [raw])
    with expert.factory() as session:
        row = registry.promote_case(
            session, expert.context(session), _promotion(baseline, failures[raw["case_id"]])
        )
        _publish(expert, session, row)
        session.commit()
        assert row.provenance["split"] == row.cases[0]["split"] == "HELD_OUT"
        renamed = copy.deepcopy(raw)
        renamed.update(case_id="renamed-" + uuid.uuid4().hex, split="TRAIN")
        with pytest.raises(ValueError, match="SPLIT_LEAKAGE"):
            foundation.create_dataset_version(
                session,
                expert.context(session),
                DatasetVersionInput.model_validate(dataset_body(cases=[renamed])),
            )
        session.rollback()
        changed_audio = _case(split="TRAIN")
        changed_audio["input"]["text"] = raw["input"]["text"]
        with pytest.raises(ValueError, match="SPLIT_LEAKAGE"):
            foundation.create_dataset_version(
                session,
                expert.context(session),
                DatasetVersionInput.model_validate(dataset_body(cases=[changed_audio])),
            )
        session.rollback()
        lineage = raw["input"]["evidence_identity"]["known_lineage_reference"]
        fresh = _case(split="CALIBRATION")
        fresh["input"]["evidence_identity"]["known_lineage_reference"] = lineage
        with pytest.raises(ValueError, match="SPLIT_LEAKAGE"):
            foundation.create_dataset_version(
                session,
                expert.context(session),
                DatasetVersionInput.model_validate(dataset_body(cases=[fresh])),
            )
        session.rollback()
        with pytest.raises(ValueError, match="SPLIT_LEAKAGE"):
            create_reference(
                session,
                expert.context(session),
                expert.source_payload(split="TRAIN", known_lineage_reference=lineage),
            )
        session.rollback()


def test_cross_tenant_and_candidate_cannot_promote_private_source(expert):
    with expert.factory() as session:
        organisation = Organisation(
            name="Synthetic private scope",
            slug="regression-" + uuid.uuid4().hex,
            organisation_type=OrganisationType.LANGUAGE_SCHOOL,
        )
        session.add(organisation)
        session.commit()
    raw = _case()
    _, baseline, failures = _source(expert, [raw], owner=organisation.id)
    with expert.factory() as session:
        for actor in ("creator", "candidate", "observer"):
            with pytest.raises(AuthorizationError):
                registry.promote_case(
                    session,
                    expert.context(session, actor),
                    _promotion(baseline, failures[raw["case_id"]]),
                )
            session.rollback()


def test_synthetic_origin_does_not_authorize_candidate_evidence_identifiers(expert):
    raw = _case()
    identity = {
        **raw["input"]["evidence_identity"],
        "candidate_response_revision_id": str(uuid.uuid4()),
    }
    for component in ("input", "reference", "synthetic_observation"):
        raw[component]["evidence_identity"] = identity
    _, baseline, failures = _source(expert, [raw])
    with expert.factory() as session:
        with pytest.raises(AuthorizationError, match="APPROVED_SOURCE_PROCESSING"):
            registry.promote_case(
                session, expert.context(session), _promotion(baseline, failures[raw["case_id"]])
            )


def test_incompatible_metric_configuration_is_not_comparable(expert):
    raw = _case()
    _, baseline, failures = _source(expert, [raw])
    _, candidate, _ = _source(expert, [_good(raw)], normalization="NFC_WHITESPACE_CASEFOLD")
    with expert.factory() as session:
        row = registry.promote_case(
            session, expert.context(session), _promotion(baseline, failures[raw["case_id"]])
        )
        _, suite = _suite(expert, session, [_publish(expert, session, row)])
        run = _compare(expert, session, suite, baseline, candidate)
        session.commit()
        detail = comparison_detail(session, expert.context(session, "examiner-b"), run)[
            "regression"
        ]
        assert detail["comparison_status"] == "NOT_COMPARABLE"
        assert (
            detail["technical_status"] == "NOT_EVALUATED" and detail["decision"] == "NOT_COMPARABLE"
        )
        assert detail["paired_case_count"] == 0


def test_required_unmeasured_retrieval_check_cannot_pass(expert):
    marker = uuid.uuid4().hex
    identity = {
        "organisation_id": "synthetic-tenant-a",
        "known_lineage_reference": "synthetic:" + marker,
    }
    source = {
        "id": "synthetic-" + marker,
        "version_id": "synthetic-version",
        "checksum": "a" * 64,
        "organisation_id": "synthetic-tenant-a",
    }
    raw = {
        "case_id": "synthetic-retrieval-" + marker,
        "kind": "RETRIEVAL",
        "split": "VALIDATION",
        "input": {
            "language": "de",
            "text": "Original synthetic query " + marker,
            "modality": "TEXT",
            "evidence_identity": identity,
        },
        "reference": {
            "label_kind": "SYNTHETIC_EXPECTATION",
            "evidence_identity": identity,
            "sources": [{**source, "authorized": True, "relevant": True}],
        },
        "synthetic_observation": {
            "evidence_identity": identity,
            "citations": [],
            "retrieved": [{**source, "organisation_id": "synthetic-tenant-b"}],
        },
    }
    no_result = copy.deepcopy(raw)
    no_result["synthetic_observation"]["retrieved"] = []
    _, baseline, failures = _source(expert, [raw], skill="RETRIEVAL")
    _, candidate, _ = _source(expert, [no_result], skill="RETRIEVAL")
    assert baseline.hard_gate == "FAIL" and candidate.hard_gate == "PASS"
    with expert.factory() as session:
        row = registry.promote_case(
            session,
            expert.context(session),
            _promotion(baseline, failures[raw["case_id"]], "tenant_isolation"),
        )
        _, suite = _suite(expert, session, [_publish(expert, session, row)])
        comparison = _compare(expert, session, suite, baseline, candidate)
        session.commit()
        detail = comparison_detail(session, expert.context(session, "examiner-b"), comparison)[
            "regression"
        ]
        assert detail["comparison_status"] == "COMPARABLE"
        assert detail["technical_status"] == "NOT_EVALUATED"
        assert detail["decision"] == "NOT_EVALUATED"
        checks = {entry["code"]: entry for entry in detail["case_findings"][0]["checks"]}
        assert checks["tenant_isolation"]["baseline"]["status"] == "FAIL"
        assert checks["tenant_isolation"]["candidate"]["denominator"] is None


def test_authenticated_control_centre_case_suite_and_held_out_redaction(
    expert: ExpertScenario, monkeypatch
):
    raw = _case(split="HELD_OUT")
    _, baseline, failures = _source(expert, [raw])
    with expert.factory() as session:
        row = registry.promote_case(
            session, expert.context(session), _promotion(baseline, failures[raw["case_id"]])
        )
        _, suite = _suite(expert, session, [_publish(expert, session, row)])
        session.commit()

    def database_session():
        with expert.factory() as session:
            yield session

    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=expert.database_url,
        auth_rate_limit_backend="memory",
        auth_rate_limit_login_attempts=100,
        auth_jwt_secret="synthetic-regression-acceptance-fixture-" + uuid.uuid4().hex,
    )
    monkeypatch.setitem(app.dependency_overrides, get_db_session, database_session)
    monkeypatch.setitem(app.dependency_overrides, get_settings, lambda: settings)
    monkeypatch.setitem(app.dependency_overrides, auth_settings_dependency, lambda: settings)
    with TestClient(app) as client:
        scenario = EvaluationScenario(
            expert.engine,
            expert.factory,
            client,
            settings,
            expert.actors,
            [],
            passwords={name: PASSWORD for name in expert.actors},
        )
        for kind, version in (("regression-cases", row), ("regression-suites", suite)):
            details = scenario.request("GET", f"{CONTROL}/{kind}/{version.id}", actor="observer")
            assert details["id"] == str(version.id) and details["split"] == "HELD_OUT"
            assert details["allowed_actions"] == []
            serialized = json.dumps(details)
            for secret in (
                raw["input"]["text"],
                raw["reference"]["text"],
                raw["input"]["audio_sha256"],
                "synthetic:independent-review",
                "source_rights",
                "split_keys",
                "synthetic_observation",
            ):
                assert secret not in serialized
            scenario.request(
                "GET", f"{CONTROL}/{kind}/{version.id}", actor="candidate", expected=403
            )
            scenario.request(
                "GET",
                f"{EVALUATION}/dataset-versions/{version.id}/cases",
                actor="creator",
                expected=403,
            )
