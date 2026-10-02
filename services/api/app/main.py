from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app.auth.api import org_router
from app.auth.api import router as auth_router
from app.config import Settings
from app.dependencies import settings_dependency

app = FastAPI(title="PrüfAI API", version="0.1.0")


@app.middleware("http")
async def security_headers(request: Request, call_next: object) -> Response:
    response = await call_next(request)  # type: ignore[operator]
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


@app.get("/health")
def health(settings: Settings = Depends(settings_dependency)) -> dict[str, str]:  # noqa: B008
    return {"service": settings.service_name, "status": "ok"}
