from collections.abc import Awaitable, Callable

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app.ai_orchestration.api import router as ai_orchestration_router
from app.auth.api import org_router
from app.auth.api import router as auth_router
from app.config import Settings
from app.control_center.api import router as control_center_router
from app.dependencies import settings_dependency
from app.evaluation.api import router as evaluation_router
from app.evaluation.api import summary_router as evaluation_summary_router
from app.evaluation.expert_api import router as expert_reference_router
from app.evaluation.expert_api import summary_router as expert_reference_summary_router
from app.evaluation.regression_api import router as regression_router
from app.evaluation.regression_api import summary_router as regression_summary_router
from app.exams.api import router as exam_router
from app.feedback.api import router as feedback_router
from app.feedback.api import summary_router as feedback_summary_router
from app.feedback.api import tenant_router as institution_feedback_router
from app.improvement.api import evaluation_router as prompt_policy_router
from app.improvement.api import router as improvement_router
from app.improvement.api import summary_router as improvement_summary_router
from app.knowledge.api import router as knowledge_router
from app.knowledge.catalogue import router as standards_catalogue_router
from app.knowledge.retrieval_api import router as retrieval_router
from app.memory.api import router as memory_router
from app.question_bank.api import router as question_bank_router
from app.result_governance.api import candidate_router as candidate_appeal_router
from app.result_governance.api import institution_router as result_governance_router
from app.result_release.api import candidate_router as candidate_release_router
from app.result_release.api import router as result_release_router
from app.rules.api import router as rules_router
from app.runtime.api import candidate_router
from app.runtime.api import organisation_router as runtime_organisation_router
from app.scoring.api import candidate_result_router, scoring_router
from app.speaking_assessment.api import router as speaking_assessment_router
from app.speech_transcription.api import router as speech_transcription_router
from app.standard_packs.api import router as standard_packs_router
from app.standard_packs.dtz_api import institution_router as dtz_institution_router
from app.standard_packs.dtz_api import router as dtz_standards_router
from app.writing_assessment.api import writing_router
from app.writing_execution.api import router as writing_execution_router

app = FastAPI(title="PrüfAI API", version="0.1.0")


@app.middleware("http")
async def security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


def _configure_cors() -> None:
    settings = Settings()
    if settings.auth_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.auth_allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-PrufAI-CSRF",
                "X-PrufAI-Organisation-ID",
            ],
        )


_configure_cors()
app.include_router(auth_router)
app.include_router(org_router)
app.include_router(knowledge_router)
app.include_router(standards_catalogue_router)
app.include_router(retrieval_router)
app.include_router(standard_packs_router)
app.include_router(dtz_standards_router)
app.include_router(dtz_institution_router)
app.include_router(rules_router)
app.include_router(ai_orchestration_router)
app.include_router(memory_router)
app.include_router(question_bank_router)
app.include_router(exam_router)
app.include_router(candidate_router)
app.include_router(runtime_organisation_router)
app.include_router(scoring_router)
app.include_router(candidate_result_router)
app.include_router(writing_router)
app.include_router(speech_transcription_router)
app.include_router(speaking_assessment_router)
app.include_router(candidate_appeal_router)
app.include_router(result_governance_router)
app.include_router(result_release_router)
app.include_router(candidate_release_router)
app.include_router(control_center_router)
app.include_router(expert_reference_router)
app.include_router(expert_reference_summary_router)
app.include_router(evaluation_router)
app.include_router(evaluation_summary_router)
app.include_router(regression_router)
app.include_router(regression_summary_router)
app.include_router(feedback_router)
app.include_router(prompt_policy_router)
app.include_router(improvement_router)
app.include_router(improvement_summary_router)
app.include_router(feedback_summary_router)
app.include_router(institution_feedback_router)


@app.get("/health")
def health(settings: Settings = Depends(settings_dependency)) -> dict[str, str]:  # noqa: B008
    return {"service": settings.service_name, "status": "ok"}


app.include_router(writing_execution_router)
