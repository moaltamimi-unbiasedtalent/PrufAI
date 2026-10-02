from fastapi import Depends

from app.config import Settings, get_settings


def settings_dependency(settings: Settings = Depends(get_settings)) -> Settings:  # noqa: B008
    """Dependency injection seam for API configuration."""

    return settings
