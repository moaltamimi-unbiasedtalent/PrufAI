"""Seal environment-scoped prompt selections and their audit events."""

from alembic import op

revision = "0035_prompt_promotion"
down_revision = "0034_improvement_candidates"
branch_labels = None
depends_on = None

_TABLE = "feedback_operations"
_KIND = "ck_feedback_operation_kind"
_SCOPE = "ck_feedback_operation_prompt_scope"
_AUDIT = "prompt_promotion_audit_immutable"
_PREVIOUS = "'FEEDBACK','PATTERN','PROPOSAL','CHANGE_CANDIDATE'"
_PROMOTION = "'PROMPT_SELECTION','PROMOTION_DENIAL','PROMPT_VALIDATE'"


def _kinds(values: str) -> None:
    op.drop_constraint(_KIND, _TABLE, type_="check")
    op.create_check_constraint(_KIND, _TABLE, f"entity_kind IN ({values})")


def upgrade() -> None:
    _kinds(f"{_PREVIOUS},{_PROMOTION}")
    op.create_check_constraint(
        _SCOPE, _TABLE, f"entity_kind NOT IN ({_PROMOTION}) OR organisation_id IS NULL"
    )
    op.execute(
        f"CREATE TRIGGER {_AUDIT} BEFORE UPDATE OR DELETE ON audit_events "
        "FOR EACH ROW WHEN (starts_with(OLD.event_type, 'feedback.prompt_')) "
        "EXECUTE FUNCTION prufai_feedback_immutable()"
    )


def downgrade() -> None:
    # The earlier schema cannot represent this history. The old constraint
    # rejects downgrade when any of the new operation receipts exist.
    _kinds(_PREVIOUS)
    op.drop_constraint(_SCOPE, _TABLE, type_="check")
    op.execute(f"DROP TRIGGER {_AUDIT} ON audit_events")
