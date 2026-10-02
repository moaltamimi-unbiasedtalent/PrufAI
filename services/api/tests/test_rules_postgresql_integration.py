from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app.models.enums import AssessmentFamily, RuleCategory, RuleSeverity, RuleStatus
from app.rules.engine import EvaluationContext, RulesEngine
from app.rules.service import RulesService
from app.standard_packs import StandardPackService


@pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL"),
    reason="PostgreSQL Rules integration service is configured in CI only",
)
def test_postgresql_rules_schema_and_persistence() -> None:
    engine = create_engine(os.environ["POSTGRES_INTEGRATION_URL"])
    assert {
        "rule_definitions",
        "rule_versions",
        "rule_evidence_bindings",
        "standard_pack_rule_bindings",
        "rule_sets",
    } <= set(inspect(engine).get_table_names())

    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        definition = RulesService.create_definition(
            session,
            code="SYNTHETIC.PG.RULE",
            name="Synthetic PostgreSQL rule",
            category=RuleCategory.STRUCTURE,
        )
        version = RulesService.create_version(
            session,
            definition,
            version_label="1",
            expression={"op": "EQ", "left": {"fact": "module.required"}, "right": {"value": True}},
            severity=RuleSeverity.BLOCKING,
            failure_code="SYNTHETIC.PG.FAIL",
            failure_message_template="synthetic failure",
            effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
            internal_policy=True,
            test_cases=[{"facts": {"module": {"required": True}}, "expected_status": "PASS"}],
        )
        version.status = RuleStatus.ACTIVE
        version.checksum = RulesService.checksum(version)
        pack = StandardPackService.create_pack(
            session,
            code="SYNTHETIC.PG.PACK",
            name="Synthetic PostgreSQL pack",
            assessment_family=AssessmentFamily.LANGUAGE,
        )
        pack_version = StandardPackService.create_version(session, pack, version_label="1")
        RulesService.bind_to_pack(session, pack_version, version)
        session.commit()

        resolved = RulesService.resolve(session, definition.id)
        assert resolved is not None and resolved.id == version.id
        assert (
            RulesEngine()
            .evaluate_rule(definition, version, EvaluationContext({"module": {"required": True}}))
            .status.value
            == "PASS"
        )
        assert len(pack_version.rule_bindings) == 1
    finally:
        session.close()
        engine.dispose()
