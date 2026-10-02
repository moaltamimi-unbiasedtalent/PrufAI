from fastapi import Depends, FastAPI

from app.config import Settings
from app.dependencies import settings_dependency

app = FastAPI(title="PrüfAI API", version="0.1.0")


@app.get("/health")
def health(settings: Settings = Depends(settings_dependency)) -> dict[str, str]:  # noqa: B008
    return {"service": settings.service_name, "status": "ok"}
