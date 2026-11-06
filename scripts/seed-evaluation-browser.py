"""Owned disposable-browser fixtures; every examiner and judgment is SYNTHETIC_TEST.

This is never product bootstrap. It does not create genuine qualifications,
rights approvals, candidate evidence, provider executions or calibrated claims.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api"))

from app.evaluation.disposable import assert_disposable_database

NAMESPACE = uuid.UUID("81333bbb-4fc7-4ae3-a8c0-8e83d090fbc4")
ACTORS = (
    "source",
    "reviewer",
    "examiner-a",
    "examiner-b",
    "adjudicator",
    "release-authorizer",
)


def identifier(key: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, key)


def main() -> None:
    url = os.environ.get("DATABASE_URL", "")
    assert_disposable_database(url)
    if not (
        os.environ.get("ENVIRONMENT") == "development"
        and os.environ.get("PRUFAI_DEMO_ENABLED") == "1"
        and os.environ.get("PRUFAI_E2E_ALLOW_DEMO") == "1"
    ):
        raise SystemExit(
            "Synthetic examiner fixtures require the owned browser acceptance setup."
        )
    manifest_path = os.environ.get("PRUFAI_E2E_REFERENCE_MANIFEST", "")
    if not manifest_path:
        raise SystemExit(
            "Set PRUFAI_E2E_REFERENCE_MANIFEST to the browser artifact manifest path."
        )

    # All DB-capable imports and connections follow the fail-closed target check.
    from app.ai_orchestration.execution_profiles import qualify_test_execution_profile
    from app.auth.service import AuthenticationService
    from app.authorization import build_tenant_context
    from app.config import Settings
    from app.db import create_session_factory
    from app.evaluation import expert_service
    from app.evaluation.expert_requests import QualificationInput
    from app.evaluation.expert_schemas import SourceInput
    from app.models import (
        AssessmentRubric,
        MembershipRole,
        Organisation,
        OrganisationMembership,
        PlatformRoleAssignment,
        Role,
        RubricVersion,
        StandardPackVersion,
        User,
    )
    from app.models.enums import (
        MembershipStatus,
        PlatformAssignmentStatus,
        RoleScope,
        UserStatus,
    )
    from app.writing_assessment.governance import RubricGovernanceService
    from app.writing_assessment.service import RubricService
    from app.writing_execution.registry import (
        SPEC as WRITING_PROFILE_SPEC,
    )
    from app.writing_execution.registry import (
        approved_profile_record,
        create_execution_profile,
        snapshot_execution_profile,
    )
    from sqlalchemy import select

    settings = Settings(database_url=url)
    password = os.environ.get("PRUFAI_DEMO_PASSWORD", "Demo-PrufAI-2026!")
    factory = create_session_factory(settings)
    with factory.begin() as session:
        organisation = session.scalar(
            select(Organisation).where(Organisation.slug == "prufai-demo-academy-v2")
        )
        platform_role = session.scalar(
            select(Role).where(
                Role.key == "PRUFAI_SUPER_ADMIN", Role.scope == RoleScope.PLATFORM
            )
        )
        tenant_role = session.scalar(
            select(Role).where(
                Role.key == "EXAM_MANAGER", Role.scope == RoleScope.TENANT
            )
        )
        rubric = session.scalar(
            select(RubricVersion)
            .join(AssessmentRubric, AssessmentRubric.id == RubricVersion.rubric_id)
            .where(
                AssessmentRubric.code == "PRUFAI_HUMAN_WRITING_B1",
                RubricVersion.version_label == "human-writing-b1-v1",
            )
        )
        if (
            organisation is None
            or platform_role is None
            or tenant_role is None
            or rubric is None
        ):
            raise RuntimeError(
                "Run the official synthetic demo seed before evaluation browser fixtures."
            )
        pack = session.get(StandardPackVersion, rubric.standard_pack_version_id)
        if pack is None:
            raise RuntimeError(
                "The synthetic Writing rubric has no exact Standard Pack."
            )

        scoped_reviewer_email = "scoped-source.reviewer@demo.prufai.local"
        scoped_reviewer = User(
            id=identifier("actor:scoped-source-reviewer"),
            email=scoped_reviewer_email,
            normalized_email=scoped_reviewer_email,
            display_name="SYNTHETIC_TEST scoped source reviewer",
            status=UserStatus.ACTIVE,
        )
        # This account has no membership or role. The browser must issue its
        # exact temporary grant through the authenticated administrative API.
        session.add(scoped_reviewer)
        session.flush()
        AuthenticationService(settings).set_password(session, scoped_reviewer, password)

        users = {}
        for actor in ACTORS:
            email = f"evaluation.{actor}@demo.prufai.local"
            user_id = identifier(f"actor:{actor}")
            if (
                session.scalar(select(User.id).where(User.normalized_email == email))
                is not None
            ):
                raise RuntimeError(
                    "Synthetic browser actor already exists; use a fresh owned database."
                )
            user = User(
                id=user_id,
                email=email,
                normalized_email=email,
                display_name=f"SYNTHETIC_TEST Evaluation {actor}",
                status=UserStatus.ACTIVE,
            )
            session.add(user)
            session.flush()
            AuthenticationService(settings).set_password(session, user, password)
            session.add(
                PlatformRoleAssignment(
                    id=identifier(f"platform-role:{actor}"),
                    user_id=user_id,
                    role_id=platform_role.id,
                    status=PlatformAssignmentStatus.ACTIVE,
                )
            )
            membership = OrganisationMembership(
                id=identifier(f"membership:{actor}"),
                organisation_id=organisation.id,
                user_id=user_id,
                status=MembershipStatus.ACTIVE,
            )
            session.add(membership)
            session.flush()
            session.add(
                MembershipRole(membership_id=membership.id, role_id=tenant_role.id)
            )
            users[actor] = user
        session.flush()
        contexts = {
            actor: build_tenant_context(session, user.id)
            for actor, user in users.items()
        }
        # The official Writing browser journey owns its original DRAFT rubric.
        # Publish a separate explicitly synthetic technical fixture through the
        # same governed lifecycle; never change that journey's template.
        template = rubric
        technical_rubric = RubricService.create_rubric(
            session,
            code="BROWSER_SYNTHETIC_TEST_EVALUATION_WRITING",
            name="SYNTHETIC_TEST evaluation browser rubric; no genuine expert approval",
            organisation_id=organisation.id,
        )
        rubric = RubricService.create_version(
            session,
            rubric=technical_rubric,
            standard_pack=pack,
            version_label="synthetic-test-evaluation-browser-v1",
            criteria=copy.deepcopy(template.criteria),
            performance_levels=copy.deepcopy(template.performance_levels),
            aggregation_policy=copy.deepcopy(template.aggregation_policy),
            evidence_policy=copy.deepcopy(template.evidence_policy),
            ai_assessment_policy={"enabled": False, "mode": "HUMAN_ONLY"},
            human_review_policy=copy.deepcopy(template.human_review_policy),
            task_spec_id=template.task_spec_id,
            evidence_bindings=[
                {
                    "knowledge_document_version_id": str(
                        binding.knowledge_document_version_id
                    ),
                    "source_checksum": binding.source_checksum,
                    "purpose": "SYNTHETIC_TEST_EVALUATION_BROWSER",
                    "reference": {"fixture": "SYNTHETIC_TEST"},
                    "required": binding.required,
                }
                for binding in template.evidence_bindings
            ],
        )
        for action, actor in (
            ("request_review", "source"),
            ("approve", "reviewer"),
            ("publish", "reviewer"),
        ):
            getattr(RubricGovernanceService, action)(
                session,
                build_tenant_context(session, users[actor].id, organisation.id),
                rubric.id,
                operation_id=identifier(f"synthetic-rubric-{action}"),
                expected_checksum=rubric.checksum,
            )
        source = expert_service.create_reference(
            session,
            contexts["source"],
            SourceInput(
                operation_id=identifier("source-operation"),
                code="BROWSER_SYNTHETIC_REFERENCE_WRITING",
                organisation_id=organisation.id,
                kind="WRITING",
                split="CALIBRATION",
                origin="ORIGINAL_SYNTHETIC",
                language="de",
                assessment_family="LANGUAGE",
                cefr_level="B1",
                retention_classification="SYNTHETIC_TEST",
                source_reference="synthetic:browser-original-writing",
                rights_approval_reference="synthetic:test-only-original-rights",
                known_lineage_reference="synthetic:browser-reference-lineage",
                standard_pack_version_id=pack.id,
                standard_pack_checksum=pack.configuration_checksum,
                rubric_version_id=rubric.id,
                rubric_checksum=rubric.checksum,
                input={
                    "modality": "TEXT",
                    "language": "de",
                    "text": "Liebe Frau Sommer, am Dienstag bin ich krank. Bitte schicken Sie mir die Hausaufgaben. Am Donnerstag komme ich wieder. Viele Grüße, Alex.",
                    "audio_kind": "NONE",
                },
            ),
        )
        expert_service.approve_reference(
            session,
            contexts["reviewer"],
            source.id,
            source.checksum,
            "synthetic:independent-source-provenance-review",
        )
        qualifications = {}
        for actor in ("examiner-a", "examiner-b", "adjudicator"):
            qualification = expert_service.create_qualification(
                session,
                contexts["source"],
                QualificationInput(
                    operation_id=identifier(f"qualification-operation:{actor}"),
                    examiner_id=users[actor].id,
                    organisation_id=organisation.id,
                    kind="WRITING",
                    language="de",
                    rubric_version_id=rubric.id,
                    rubric_checksum=rubric.checksum,
                    standard_pack_version_id=pack.id,
                    standard_pack_checksum=pack.configuration_checksum,
                    origin="SYNTHETIC_TEST",
                    credential_reference=f"synthetic:technical-browser-actor-{actor}",
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                ),
            )
            expert_service.approve_qualification(
                session,
                contexts["reviewer"],
                qualification.id,
                qualification.checksum,
                f"synthetic:technical-qualification-review-{actor}",
            )
            qualifications[actor] = {
                "id": str(qualification.id),
                "checksum": qualification.checksum,
            }

        def seed_prompt_fixture(code: str, canary_key: str) -> dict[str, object]:
            # A separate technical-only profile supplies a genuine registry baseline.
            # Its old optional instructions deliberately contain original synthetic
            # canaries. The profile is never attached to an exam or deployed worker.
            canaries = [
                f"PRUFAI_SYNTHETIC_CANARY_{identifier(f'{canary_key}-{index}').hex}"
                for index in (1, 2)
            ]
            profile = create_execution_profile(
                session,
                context=contexts["source"],
                code=code,
                name="TEST ONLY prompt channel isolation fixture",
                provider="DETERMINISTIC_TEST",
                model_name="writing-fixture-v1",
                configuration={
                    "schema_version": "1",
                    "adapter_version": "1",
                    "scenario": "success",
                },
                system_template="Return the structured technical Writing proposal. Human review is required.",
                instruction_template="Original synthetic legacy instruction fixture: "
                + " ".join(canaries),
            )
            snapshot = snapshot_execution_profile(
                session,
                **{
                    key: uuid.UUID(str(profile[key]))
                    for key in (
                        "model_profile_id",
                        "prompt_version_id",
                        "agent_version_id",
                        "workflow_version_id",
                    )
                },
            )
            snapshot = qualify_test_execution_profile(
                session,
                snapshot=snapshot,
                operator=users["source"],
                settings=settings,
                spec=replace(
                    WRITING_PROFILE_SPEC,
                    approval_event="evaluation.prompt_policy.fixture_qualified",
                ),
            )
            if approved_profile_record(session, snapshot) is not None:
                raise RuntimeError(
                    "The offline prompt fixture must not qualify for Writing execution."
                )
            return {
                "prompt": {
                    "id": snapshot["prompt_version_id"],
                    "checksum": snapshot["prompt_checksum"],
                },
                "canaries": canaries,
            }

        manifest = {
            "prompt_policy_fixture": seed_prompt_fixture(
                "prufai.browser.synthetic.prompt-policy-baseline.v1", "prompt-canary"
            ),
            "prompt_promotion_fixture": seed_prompt_fixture(
                "prufai.browser.synthetic.prompt-promotion-baseline.v1",
                "promotion-canary",
            ),
            "reference_id": str(source.id),
            "reference_checksum": source.checksum,
            "actor_ids": {actor: str(user.id) for actor, user in users.items()},
            "qualifications": qualifications,
            "scoped_source_review": {
                "organisation_id": str(organisation.id),
                "reviewer_id": str(scoped_reviewer.id),
                "reviewer_email": scoped_reviewer_email,
            },
        }
    path = Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    print(
        "Created SYNTHETIC_TEST reference fixtures; no genuine expert calibration is established."
    )


if __name__ == "__main__":
    main()
