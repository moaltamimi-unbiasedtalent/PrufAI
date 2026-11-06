"""Reserved identity and publication boundary for the DTZ practice preview."""

from app.models.standard_pack import StandardPackVersion

DTZ_PACK_CODE = "PRUFAI_DTZ_DE_A2_B1"
DTZ_SCHEMA = "s2-dtz-preview-v1"
DTZ_EXAM_FAMILY = "DEUTSCH_TEST_FUER_ZUWANDERER"
DTZ_RULE_PREFIX = "PRUFAI_DTZ_S2_"
DTZ_RUBRIC_SCHEMA = "s2-dtz-review-v1"
DTZ_PUBLICATION_BLOCKED = "DTZ_DRAFT_PUBLICATION_BLOCKED"
DTZ_DELIVERY_BLOCKED = "DTZ_DRAFT_DELIVERY_BLOCKED"
DTZ_PUBLICATION_BLOCKERS = (
    ("DTZ_EXTERNAL_CONTENT_RIGHTS_REQUIRED", "External content reuse rights are not approved."),
    ("DTZ_EXPERT_TASK_MAPPING_REQUIRED", "Independent expert task mapping is not approved."),
    ("DTZ_RUBRIC_CONFLICTS_UNRESOLVED", "Writing and Speaking criteria require reconciliation."),
    ("DTZ_SCORING_RULES_UNAPPROVED", "Draft rules cannot be used for a live result."),
    (
        "DTZ_INTERACTIVE_SPEAKING_UNSUPPORTED",
        "The recorded practice mode cannot deliver the interactive oral exam.",
    ),
    (
        "DTZ_INDEPENDENT_PUBLICATION_APPROVAL_REQUIRED",
        "No independent human publication approval exists.",
    ),
)


def is_dtz_family(value: str | None) -> bool:
    return value is not None and value.strip().upper() in {DTZ_EXAM_FAMILY, "DTZ"}


def is_dtz_pack(version: StandardPackVersion) -> bool:
    return (
        version.configuration_schema_version == DTZ_SCHEMA
        or version.standard_pack.code.strip().upper() == DTZ_PACK_CODE
        or is_dtz_family(version.standard_pack.exam_family)
    )
