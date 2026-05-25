"""Runtime configuration. All settings are loaded from environment variables
prefixed ``PIL_``, with a local ``.env`` honored during development.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="PIL_",
        extra="ignore",
        case_sensitive=False,
    )

    # ----- app -------------------------------------------------------------
    env: Literal["local", "ci", "staging", "prod"] = "local"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = Field(default_factory=list)

    # ----- database --------------------------------------------------------
    database_url: str = "postgresql+asyncpg://pil:pil@localhost:5432/pil"
    database_url_sync: str = "postgresql+psycopg://pil:pil@localhost:5432/pil"
    database_pool_size: int = 10
    database_max_overflow: int = 5

    # ----- redis -----------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"

    # ----- observability ---------------------------------------------------
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "pil-api"
    prometheus_enabled: bool = True

    # ----- pii (wired in Phase 2) -----------------------------------------
    pii_mode: Literal["reversible", "one_way"] = "reversible"
    pii_map_ttl_seconds: int = 300
    pii_fail_closed: bool = True

    # ----- cache (wired in Phase 2) ---------------------------------------
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_device: Literal["auto", "cpu", "mps", "cuda"] = "auto"
    cache_similarity_threshold: float = 0.92
    cache_ttl_seconds: int = 86400

    # ----- auth ------------------------------------------------------------
    default_rate_limit_per_hour: int = 1000
    key_rotation_grace_hours: int = 24

    # ----- encryption ------------------------------------------------------
    master_encryption_key: SecretStr = SecretStr("change-me-to-a-32-byte-urlsafe-string")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [o.strip() for o in value.split(",") if o.strip()]
        return value

    @field_validator("cache_similarity_threshold")
    @classmethod
    def _threshold_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("cache_similarity_threshold must be between 0 and 1")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings instance. Cached, so .env is only parsed once."""
    return Settings()
