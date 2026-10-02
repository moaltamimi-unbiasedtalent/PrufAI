from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the API boundary."""

    service_name: str = "prufai-api"
    database_url: str = "postgresql+psycopg://prufai:prufai_local_only@localhost:5432/prufai"
    redis_url: str = "redis://localhost:6379/0"
    object_storage_endpoint: str = "http://localhost:9000"
    object_storage_bucket: str = "prufai-local"
    object_storage_backend: Literal["memory", "s3"] = "memory"
    object_storage_access_key: str = ""
    object_storage_secret_key: str = ""
    object_storage_region: str = "us-east-1"
    object_storage_secure: bool = False
    environment: Literal["development", "test", "production"] = "development"
    auth_jwt_secret: str = "prufai-local-development-secret-change-before-production-0123456789"
    auth_jwt_issuer: str = "prufai-api"
    auth_jwt_audience: str = "prufai-web"
    auth_access_token_ttl_minutes: int = 15
    auth_refresh_token_ttl_days: int = 30
    auth_refresh_cookie_name: str = "prufai_refresh"
    auth_csrf_cookie_name: str = "prufai_csrf"
    auth_cookie_secure: bool = False
    auth_cookie_samesite: Literal["strict", "lax", "none"] = "strict"
    auth_cookie_path: str = "/api/v1/auth"
    auth_rate_limit_backend: Literal["redis", "memory"] = "memory"
    auth_allowed_origins: list[str] = []
    auth_rate_limit_login_attempts: int = 5
    auth_rate_limit_window_seconds: int = 300
    auth_rate_limit_reset_attempts: int = 3
    auth_rate_limit_refresh_attempts: int = 30
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    @field_validator("auth_access_token_ttl_minutes")
    @classmethod
    def access_ttl_is_short(cls, value: int) -> int:
        if not 1 <= value <= 60:
            raise ValueError("access token TTL must be between 1 and 60 minutes")
        return value

    @field_validator("auth_jwt_secret")
    @classmethod
    def production_secret_is_strong(cls, value: str, info: object) -> str:
        data = getattr(info, "data", {})
        if data.get("environment") == "production" and len(value) < 32:
            raise ValueError("production AUTH_JWT_SECRET must be at least 32 characters")
        return value

    @field_validator("auth_rate_limit_backend")
    @classmethod
    def production_requires_redis(cls, value: str, info: object) -> str:
        data = getattr(info, "data", {})
        if data.get("environment") == "production" and value != "redis":
            raise ValueError("production authentication rate limiting must use Redis")
        return value

    @field_validator("object_storage_backend")
    @classmethod
    def production_requires_object_storage(cls, value: str, info: object) -> str:
        data = getattr(info, "data", {})
        if data.get("environment") == "production" and value != "s3":
            raise ValueError("production object storage must use an S3-compatible backend")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
