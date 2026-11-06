"""Reserved identities and delivery boundaries for the S3 LiD programme."""

from app.models.enums import AssessmentFamily, StandardPackStatus
from app.models.standard_pack import StandardPackVersion

LID_EXAM_FAMILY = "LEBEN_IN_DEUTSCHLAND"
LID_DRAFT_PACK_CODE = "PRUFAI_LID_DE_DRAFT"
LID_DRAFT_SCHEMA = "s3-lid-draft-v1"
LID_PRACTICE_SCHEMA = "s3-lid-synthetic-practice-v1"
LID_PRACTICE_CODE_PREFIX = "PRUFAI_LID_SYNTHETIC_"
LID_EDITORIAL_SCHEMA = "s4-lid-original-practice-v1"
LID_EDITORIAL_PACK_CODE_PREFIX = "PRUFAI_LID_ORIGINAL_"
LID_EDITORIAL_ITEM_CODE_PREFIX = "LID_S4_"
LID_ORIGINAL_NOTICE = (
    "Original educational practice questions. Not an official BAMF examination. "
    "NOT OFFICIAL / NOT CERTIFIED."
)
LID_DRAFT_PUBLICATION_BLOCKED = "LID_DRAFT_PUBLICATION_BLOCKED"
LID_DRAFT_DELIVERY_BLOCKED = "LID_DRAFT_DELIVERY_BLOCKED"
LID_RULE_PREFIX = "PRUFAI_LID_S3_"
LID_RULE_PREVIEW_ONLY = "LID_DRAFT_RULE_PREVIEW_ONLY"
LID_NOTICE = (
    "Leben in Deutschland — Practice. ORIGINAL SYNTHETIC TEST CONTENT. "
    "NOT AN OFFICIAL BAMF QUESTION. NOT OFFICIAL / NOT CERTIFIED."
)
LID_STATES = {
    "DE-BW": "Baden-Württemberg",
    "DE-BY": "Bayern",
    "DE-BE": "Berlin",
    "DE-BB": "Brandenburg",
    "DE-HB": "Bremen",
    "DE-HH": "Hamburg",
    "DE-HE": "Hessen",
    "DE-MV": "Mecklenburg-Vorpommern",
    "DE-NI": "Niedersachsen",
    "DE-NW": "Nordrhein-Westfalen",
    "DE-RP": "Rheinland-Pfalz",
    "DE-SL": "Saarland",
    "DE-SN": "Sachsen",
    "DE-ST": "Sachsen-Anhalt",
    "DE-SH": "Schleswig-Holstein",
    "DE-TH": "Thüringen",
}
LID_STRUCTURE = {
    "general_items": 30,
    "state_items": 3,
    "total_items": 33,
    "options_per_item": 4,
    "correct_options_per_item": 1,
    "time_limit_seconds": 3600,
}
LID_PUBLICATION_BLOCKERS = (
    ("LID_CONTENT_RIGHTS_UNRESOLVED", "Official question and asset reuse rights are unresolved."),
    ("LID_EXPERT_VALIDATION_REQUIRED", "Independent subject matter validation is absent."),
    ("LID_RULE_APPROVAL_REQUIRED", "The purpose-specific legal rules are draft previews."),
    ("LID_JURISDICTION_CONTENT_UNAVAILABLE", "State codes do not establish reviewed state pools."),
    ("LID_OFFICIAL_QUESTION_BANK_INCOMPLETE", "No official item content is included."),
    (
        "LID_INDEPENDENT_PUBLICATION_APPROVAL_REQUIRED",
        "No independent publication approval exists.",
    ),
    (
        "LID_OFFICIAL_CONTEXT_UNSUPPORTED",
        "Official provider and examination conditions are not established.",
    ),
)


def is_lid_family(value: str | None) -> bool:
    return bool(value and value.strip().upper() in {LID_EXAM_FAMILY, "LEBEN IN DEUTSCHLAND", "LID"})


def reserved_lid_code(code: str) -> bool:
    code = code.strip().upper()
    return code == LID_DRAFT_PACK_CODE or code.startswith(
        (LID_PRACTICE_CODE_PREFIX, LID_EDITORIAL_PACK_CODE_PREFIX)
    )


def is_lid_practice_pack(version: StandardPackVersion) -> bool:
    pack = version.standard_pack
    return (
        version.configuration_schema_version == LID_PRACTICE_SCHEMA
        and pack.code.startswith(LID_PRACTICE_CODE_PREFIX)
        and pack.exam_family == LID_EXAM_FAMILY
        and pack.assessment_family is AssessmentFamily.INTEGRATION_KNOWLEDGE
        and pack.language == "de"
        and not version.supported_levels
        and version.allowed_classifications == ["PRACTICE"]
    )


def is_lid_editorial_pack(version: StandardPackVersion) -> bool:
    pack = version.standard_pack
    return (
        version.configuration_schema_version == LID_EDITORIAL_SCHEMA
        and pack.code.startswith(LID_EDITORIAL_PACK_CODE_PREFIX)
        and pack.exam_family == LID_EXAM_FAMILY
        and pack.assessment_family is AssessmentFamily.INTEGRATION_KNOWLEDGE
        and pack.language == "de"
        and not version.supported_levels
        and version.allowed_classifications == ["PRACTICE"]
    )


def is_lid_delivery_pack(version: StandardPackVersion) -> bool:
    return is_lid_practice_pack(version) or is_lid_editorial_pack(version)


def is_lid_pack(version: StandardPackVersion) -> bool:
    return (
        version.configuration_schema_version
        in {LID_DRAFT_SCHEMA, LID_PRACTICE_SCHEMA, LID_EDITORIAL_SCHEMA}
        or reserved_lid_code(version.standard_pack.code)
        or is_lid_family(version.standard_pack.exam_family)
    )


def is_lid_draft_pack(version: StandardPackVersion) -> bool:
    return is_lid_pack(version) and not (
        is_lid_practice_pack(version)
        or (
            is_lid_editorial_pack(version)
            and version.status in {StandardPackStatus.PUBLISHED, StandardPackStatus.ACTIVE}
        )
    )


def is_lid_preview_rule_code(code: str) -> bool:
    return code.strip().upper().startswith(LID_RULE_PREFIX)
