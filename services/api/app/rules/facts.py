from __future__ import annotations

from typing import Any

from app.models.standard_pack import StandardPackVersion
from app.rules.engine import EvaluationContext


def pack_evaluation_context(version: StandardPackVersion) -> EvaluationContext:
    pack = version.standard_pack
    modules = [
        {
            "code": module.code,
            "name": module.name,
            "skill": module.skill,
            "sequence": module.sequence,
            "required": module.required,
            "time_limit_seconds": module.time_limit_seconds,
            "preparation_time_seconds": module.preparation_time_seconds,
            "sections": [
                {
                    "code": section.code,
                    "sequence": section.sequence,
                    "time_limit_seconds": section.time_limit_seconds,
                    "tasks": [
                        {
                            "code": task.code,
                            "task_type": task.task_type,
                            "min_items": task.min_items,
                            "max_items": task.max_items,
                            "required_items": task.required_items,
                            "response_mode": task.response_mode.value,
                            "media_requirement": task.media_requirement.value,
                        }
                        for task in section.tasks
                    ],
                }
                for section in module.sections
            ],
        }
        for module in version.modules
    ]
    facts: dict[str, Any] = {
        "pack": {
            "id": str(version.standard_pack_id),
            "version_label": version.version_label,
            "assessment_family": pack.assessment_family.value,
            "language": pack.language,
            "exam_family": pack.exam_family,
            "supported_levels": version.supported_levels,
            "allowed_classifications": version.allowed_classifications,
            "module_count": len(modules),
            "modules": modules,
        }
    }
    return EvaluationContext(
        facts=facts,
        standard_pack_version_id=str(version.id),
        standard_pack_checksum=version.configuration_checksum,
    )
