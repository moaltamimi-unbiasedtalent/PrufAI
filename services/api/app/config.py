from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
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
    audio_media_runtime: Literal["native", "docker"] = "native"
    audio_media_image: str = "prufai-media:ffmpeg-6.1.1"
    audio_ffprobe_executable: str = "ffprobe"
    audio_ffmpeg_executable: str = "ffmpeg"
    audio_probe_timeout_seconds: int = Field(default=15, ge=1, le=60)
    audio_probe_memory_mb: int = Field(default=512, ge=256, le=1024)
    knowledge_embedding_backend: Literal["deterministic", "openai"] = "deterministic"
    knowledge_embedding_model: str = "text-embedding-3-small"
    knowledge_embedding_dimension: int = 384
    knowledge_embedding_api_key: str = ""
    knowledge_embedding_timeout_seconds: int = 20
    knowledge_vector_metric: Literal["cosine"] = "cosine"
    knowledge_reranker_backend: Literal["deterministic", "openai"] = "deterministic"
    knowledge_reranker_model: str = "rerank-v1"
    knowledge_reranker_api_key: str = ""
    knowledge_reranker_timeout_seconds: int = 20
    environment: Literal["development", "test", "production"] = "development"
    speech_transcription_test_adapter_enabled: bool = False
    speech_transcription_provider_timeout_seconds: int = Field(default=45, ge=1, le=120)
    # Deployment approval is separate from a platform actor approving a profile.
    # These are exact immutable profile fingerprints, never API credentials.
    speech_transcription_approved_profile_fingerprints: list[str] = []
    writing_test_adapter_enabled: bool = False
    writing_provider_timeout_seconds: int = Field(default=60, ge=1, le=120)
    writing_approved_profile_fingerprints: list[str] = []
    certification_issuance_enabled: bool = False
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

    @model_validator(mode="after")
    def transcription_deployment_boundary(self) -> "Settings":
        import re

        if self.environment == "production" and self.speech_transcription_test_adapter_enabled:
            raise ValueError("TEST ONLY speech transcription is forbidden in production")
        if self.environment == "production" and self.writing_test_adapter_enabled:
            raise ValueError("TEST ONLY Writing execution is forbidden in production")
        if any(
            re.fullmatch(r"[a-f0-9]{64}", value) is None
            for value in (
                self.speech_transcription_approved_profile_fingerprints
                + self.writing_approved_profile_fingerprints
            )
        ):
            raise ValueError("approved execution profiles require exact SHA256 fingerprints")
        return self

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

    @field_validator("knowledge_embedding_backend")
    @classmethod
    def production_requires_real_embeddings(cls, value: str, info: object) -> str:
        data = getattr(info, "data", {})
        if data.get("environment") == "production" and value == "deterministic":
            raise ValueError("production embeddings cannot use the deterministic test backend")
        return value

    @field_validator("knowledge_embedding_dimension")
    @classmethod
    def embedding_dimension_is_supported(cls, value: int) -> int:
        if value != 384:
            raise ValueError("the MVP pgvector dimension is fixed at 384")
        return value

    @field_validator("knowledge_reranker_backend")
    @classmethod
    def production_requires_real_reranking(cls, value: str, info: object) -> str:
        data = getattr(info, "data", {})
        if data.get("environment") == "production" and value == "deterministic":
            raise ValueError("production reranking cannot use the deterministic test backend")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
