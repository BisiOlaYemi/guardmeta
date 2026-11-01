from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Secrets are never logged."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Keep both names as separate fields so an empty line for one name cannot
    # mask a populated value for the other in .env files.
    model_api_key: SecretStr = Field(default=SecretStr(""), exclude=True, repr=False)
    meta_api_key: SecretStr = Field(default=SecretStr(""), exclude=True, repr=False)
    open_weight_api_key: SecretStr = Field(default=SecretStr(""), exclude=True, repr=False)
    meta_base_url: str = Field(
        default="https://api.meta.ai/v1",
        validation_alias=AliasChoices("OPEN_WEIGHT_BASE_URL", "META_BASE_URL", "MODEL_BASE_URL"),
    )
    model_name: str = Field(
        default="muse-spark-1.3",
        validation_alias=AliasChoices("OPEN_WEIGHT_MODEL", "MODEL_NAME", "META_MODEL_NAME"),
    )
    model_provider: str = Field(
        default="auto",
        description="Infrastructure serving the model weights (modal, baseten, self_hosted, ...).",
    )
    require_open_weight_model: bool = False
    upstream_operational_verified: bool = False
    guardmeta_api_key: SecretStr = Field(default=SecretStr(""))
    log_level: str = "INFO"
    max_correction_attempts: int = Field(default=3, ge=1, le=8)
    request_timeout_seconds: float = Field(default=90.0, gt=1, le=300)
    max_prompt_chars: int = Field(default=32_000, ge=256)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=10_000)
    financial_max_amount: Decimal = Field(default=Decimal("250000.00"), gt=0)
    environment: Literal["development", "staging", "production"] = "development"
    reasoning_effort: str = "low"
    demo_mode: bool = False
    demo_rate_limit_per_minute: int = Field(default=6, ge=1, le=120)

    @field_validator("meta_base_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def has_upstream_key(self) -> bool:
        return bool(self.upstream_api_key)

    @property
    def upstream_api_key(self) -> str:
        return (
            self.open_weight_api_key.get_secret_value().strip()
            or self.model_api_key.get_secret_value().strip()
            or self.meta_api_key.get_secret_value().strip()
        )

    @property
    def inbound_auth_required(self) -> bool:
        return bool(self.guardmeta_api_key.get_secret_value().strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
