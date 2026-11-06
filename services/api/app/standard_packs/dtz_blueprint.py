"""DTZ practice blueprint built on the existing Standard Pack domain."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.models.enums import PackMediaRequirement, PackNavigationMode, PackResponseMode
from app.models.standard_pack import (
    StandardPackModule,
    StandardPackSection,
    StandardPackTaskSpecification,
    StandardPackVersion,
)

ORIGINAL = "ORIGINAL SYNTHETIC PRACTICE CONTENT"
RECORDED = "RECORDED_PRACTICE"
PAIR = "INTERACTIVE_PAIR_EXAM"
MEDIATED = "EXAMINER_MEDIATED_EXAM"
UNSUPPORTED = "UNSUPPORTED_FOR_FULL_FIDELITY"
_OBJECTIVE_DOMAINS = {"LISTENING": 20, "READING": 25}
_TIMING = {"LISTENING": 1500, "READING": 2700, "WRITING": 1800, "SPEAKING": 960}


@dataclass(frozen=True)
class TaskShape:
    suffix: str
    title: str
    kind: str
    count: int | None
    response_mode: PackResponseMode
    evidence: str
    interaction: str = "INDEPENDENT_RESPONSE"
    example: str | None = None
    choice_count: int | None = None


@dataclass(frozen=True)
class SectionShape:
    code: str
    title: str
    tasks: tuple[TaskShape, ...]


def _objective(
    code: str,
    title: str,
    kind: str,
    count: int,
    *,
    audio: bool = False,
    example: str | None = None,
) -> TaskShape:
    return TaskShape(
        code,
        title,
        kind,
        count,
        PackResponseMode.SELECT_ONE,
        "ORIGINAL_AUDIO_STIMULUS" if audio else "ORIGINAL_TEXT_STIMULUS",
        example=example,
    )


_GROUPS: dict[str, tuple[SectionShape, ...]] = {
    "LISTENING": (
        SectionShape(
            "H1",
            "Short spoken notices",
            (
                _objective(
                    "MC",
                    "One answer per notice item",
                    "MULTIPLE_CHOICE",
                    4,
                    audio=True,
                    example="Originales Hörskript: Der Scanner steht neben der Treppe. "
                    "Der Drucker wird repariert. Welches Gerät ist jetzt benutzbar?",
                ),
            ),
        ),
        SectionShape(
            "H2",
            "Public information",
            (
                _objective(
                    "MC", "One answer per public information item", "MULTIPLE_CHOICE", 5, audio=True
                ),
            ),
        ),
        SectionShape(
            "H3",
            "Short conversations",
            (
                _objective("TF", "One true or false choice per item", "TRUE_FALSE", 4, audio=True),
                _objective(
                    "MC", "One answer per conversation item", "MULTIPLE_CHOICE", 4, audio=True
                ),
            ),
        ),
        SectionShape(
            "H4",
            "Statements about a topic",
            (_objective("MATCH", "One match per item", "MATCHING", 3, audio=True),),
        ),
    ),
    "READING": (
        SectionShape(
            "L1",
            "Practical information",
            (
                _objective(
                    "MC",
                    "One answer per practical information item",
                    "MULTIPLE_CHOICE",
                    5,
                    example="Am Dienstag nimmt die Werkzeugbibliothek Rückgaben am Fenster an. "
                    "Am Donnerstag öffnet der Hauptschalter für Ausleihen. "
                    "Wo gibt man am Dienstag eine Bohrmaschine zurück?",
                ),
            ),
        ),
        SectionShape(
            "L2",
            "Texts for everyday needs",
            (_objective("MATCH", "One match per item", "MATCHING", 5),),
        ),
        SectionShape(
            "L3",
            "Everyday written messages",
            (
                _objective("TF", "One true or false choice per item", "TRUE_FALSE", 3),
                _objective("MC", "One answer per message item", "MULTIPLE_CHOICE", 3),
            ),
        ),
        SectionShape(
            "L4",
            "Reading for specific information",
            (_objective("TF", "One true or false choice per item", "TRUE_FALSE", 3),),
        ),
        SectionShape(
            "L5",
            "Reading in context",
            (_objective("MC", "One answer per text item", "MULTIPLE_CHOICE", 6),),
        ),
    ),
    "WRITING": (
        SectionShape(
            "W1",
            "Functional written communication",
            (
                TaskShape(
                    "CHOOSE",
                    "Choose one prompt and submit one text",
                    "WRITTEN_INTERACTION",
                    1,
                    PackResponseMode.LONG_TEXT,
                    "EXACT_TEXT_RESPONSE_REVISION",
                    example="Wählen Sie eine Situation. A: Schreiben Sie dem Reparaturcafé: "
                    "Die Inventarkiste braucht ein neues Schild. Schlagen Sie eine Übergabe vor. "
                    "B: Bitten Sie beim Bürgerradio um einen Tausch der Aufnahmezeit "
                    "und erklären Sie den Grund.",
                    choice_count=2,
                ),
            ),
        ),
    ),
    "SPEAKING": (
        SectionShape(
            "S1",
            "Introduction and follow-up",
            (
                TaskShape(
                    "SOLO",
                    "Monologic practice introduction",
                    "MONOLOGUE",
                    None,
                    PackResponseMode.AUDIO,
                    "EXACT_ORIGINAL_AUDIO_WITH_VERIFIED_DURATION",
                    RECORDED,
                    "Stellen Sie eine erfundene Person vor, die einen Stadtteilpodcast moderiert. "
                    "Beschreiben Sie eine Stärke und das Thema ihrer ersten Folge.",
                ),
                TaskShape(
                    "FOLLOWUP",
                    "Examiner follow-up",
                    "DIALOGUE",
                    None,
                    PackResponseMode.LIVE_ORAL,
                    "LIVE_EXAMINER_INTERACTION_REQUIRED",
                    MEDIATED,
                ),
            ),
        ),
        SectionShape(
            "S2",
            "Description and follow-up",
            (
                TaskShape(
                    "SOLO",
                    "Monologic practice description",
                    "MONOLOGUE",
                    None,
                    PackResponseMode.AUDIO,
                    "EXACT_ORIGINAL_AUDIO_WITH_VERIFIED_DURATION",
                    RECORDED,
                    "Beschreiben Sie eine kleine Ausstellung, die Sie sich ausdenken. "
                    "Was sehen Gäste zuerst? Bei welchem Objekt würden Sie länger bleiben?",
                ),
                TaskShape(
                    "FOLLOWUP",
                    "Examiner follow-up",
                    "DIALOGUE",
                    None,
                    PackResponseMode.LIVE_ORAL,
                    "LIVE_EXAMINER_INTERACTION_REQUIRED",
                    MEDIATED,
                ),
            ),
        ),
        SectionShape(
            "S3",
            "Collaborative planning",
            (
                TaskShape(
                    "PAIR",
                    "Plan together and negotiate choices",
                    "COLLABORATIVE_DIALOGUE",
                    None,
                    PackResponseMode.LIVE_ORAL,
                    "LIVE_PARTNER_INTERACTION_REQUIRED",
                    PAIR,
                ),
            ),
        ),
    ),
}
_SKILL_LABEL = {
    "LISTENING": "Hören",
    "READING": "Lesen",
    "WRITING": "Schreiben",
    "SPEAKING": "Sprechen",
}
_DOMAIN = {
    "LISTENING": "LISTENING_READING",
    "READING": "LISTENING_READING",
    "WRITING": "WRITING",
    "SPEAKING": "SPEAKING",
}


def build_dtz_structure(
    version: StandardPackVersion, now: datetime
) -> tuple[StandardPackTaskSpecification, StandardPackTaskSpecification]:
    productive: dict[str, StandardPackTaskSpecification] = {}
    for sequence, (skill, groups) in enumerate(_GROUPS.items(), 1):
        module = StandardPackModule(
            id=uuid.uuid4(),
            code=skill,
            name=_SKILL_LABEL[skill],
            skill=skill,
            sequence=sequence,
            required=True,
            time_limit_seconds=_TIMING[skill],
            preparation_time_seconds=0 if skill == "SPEAKING" else None,
            navigation_mode=PackNavigationMode.LINEAR,
            created_at=now,
        )
        version.modules.append(module)
        for group_order, group in enumerate(groups, 1):
            section = StandardPackSection(
                id=uuid.uuid4(),
                code=group.code,
                name=group.title,
                sequence=group_order,
                navigation_mode=PackNavigationMode.LINEAR,
                created_at=now,
            )
            module.sections.append(section)
            for task_order, shape in enumerate(group.tasks, 1):
                objective = skill in _OBJECTIVE_DOMAINS
                expected_items = shape.count or 0
                if shape.example:
                    example = {"label": ORIGINAL, "prompt": shape.example}
                else:
                    example = None
                task = StandardPackTaskSpecification(
                    id=uuid.uuid4(),
                    code=f"{group.code}_{shape.suffix}",
                    name=shape.title,
                    sequence=task_order,
                    task_type=shape.kind,
                    min_items=expected_items,
                    max_items=expected_items,
                    required_items=shape.count,
                    points_per_item=1 if objective else None,
                    response_mode=shape.response_mode,
                    media_requirement=PackMediaRequirement.AUDIO
                    if skill == "LISTENING"
                    else PackMediaRequirement.NONE,
                    generation_allowed=False,
                    selection_allowed=False,
                    rubric_required=not objective,
                    metadata_json={
                        "item_kind": shape.kind,
                        "item_count": shape.count,
                        "interaction": shape.interaction,
                        "evidence_mode": shape.evidence,
                        "task_choice_count": shape.choice_count,
                        "scoring_domain": _DOMAIN[skill],
                        "structure_claim": f"DTZ_{skill}_STRUCTURE",
                        "fidelity": UNSUPPORTED
                        if shape.interaction in {PAIR, MEDIATED}
                        else "PRACTICE_ONLY",
                        "synthetic_example": example,
                    },
                    created_at=now,
                )
                section.tasks.append(task)
                if not objective and skill not in productive:
                    productive[skill] = task
    verify_dtz_structure(version)
    return productive["WRITING"], productive["SPEAKING"]


def verify_dtz_structure(version: StandardPackVersion) -> None:
    modules = version.modules
    if [m.skill for m in modules] != list(_GROUPS) or [m.sequence for m in modules] != list(
        range(1, len(_GROUPS) + 1)
    ):
        raise ValueError("DTZ_BLUEPRINT_SKILLS_INVALID")
    seen: set[str] = set()
    for module in modules:
        skill = module.skill
        assert skill is not None
        if (
            module.code != skill
            or module.time_limit_seconds != _TIMING[skill]
            or [group.code for group in module.sections] != [group.code for group in _GROUPS[skill]]
            or [group.sequence for group in module.sections]
            != list(range(1, len(_GROUPS[skill]) + 1))
        ):
            raise ValueError("DTZ_BLUEPRINT_SECTION_ORDER_OR_TIMING_INVALID")
        counted = 0
        for section, group in zip(module.sections, _GROUPS[skill], strict=True):
            if [task.sequence for task in section.tasks] != list(range(1, len(group.tasks) + 1)):
                raise ValueError("DTZ_BLUEPRINT_TASK_ORDER_INVALID")
            for task, expected in zip(section.tasks, group.tasks, strict=True):
                item_count = expected.count or 0
                metadata = task.metadata_json
                if (
                    task.code in seen
                    or task.code != f"{section.code}_{expected.suffix}"
                    or task.task_type != expected.kind
                    or task.response_mode != expected.response_mode
                    or metadata.get("interaction") != expected.interaction
                    or metadata.get("evidence_mode") != expected.evidence
                    or metadata.get("scoring_domain") != _DOMAIN[skill]
                    or metadata.get("task_choice_count") != expected.choice_count
                    or metadata.get("item_count") != expected.count
                    or task.min_items != item_count
                    or task.max_items != item_count
                    or task.required_items != expected.count
                    or task.points_per_item != (1 if skill in _OBJECTIVE_DOMAINS else None)
                ):
                    raise ValueError("DTZ_BLUEPRINT_TASK_OR_SCORING_DOMAIN_INVALID")
                seen.add(task.code)
                counted += item_count
        if skill in _OBJECTIVE_DOMAINS and counted != _OBJECTIVE_DOMAINS[skill]:
            raise ValueError("DTZ_BLUEPRINT_OBJECTIVE_ITEM_TOTAL_INVALID")
        if skill == "WRITING" and counted != 1:
            raise ValueError("DTZ_BLUEPRINT_WRITING_SELECTION_INVALID")


def dtz_sections(version: StandardPackVersion) -> list[dict[str, Any]]:
    return [
        {
            "id": str(module.id),
            "skill": module.skill,
            "name": module.name,
            "scoring_domain": _DOMAIN[module.code],
            "time_limit_seconds": module.time_limit_seconds,
            "time_is_approximate": module.code in {"LISTENING", "SPEAKING"},
            "item_total": _OBJECTIVE_DOMAINS.get(
                module.code, 1 if module.code == "WRITING" else None
            ),
            "interaction_fidelity": UNSUPPORTED if module.code == "SPEAKING" else "PRACTICE_ONLY",
            "structure_claim": f"DTZ_{module.code}_STRUCTURE",
            "groups": [
                {
                    "id": str(group.id),
                    "code": group.code,
                    "name": group.name,
                    "tasks": [
                        {
                            "id": str(task.id),
                            "code": task.code,
                            "name": task.name,
                            "response_mode": task.response_mode.value,
                            **task.metadata_json,
                        }
                        for task in group.tasks
                    ],
                }
                for group in module.sections
            ],
        }
        for module in version.modules
    ]
