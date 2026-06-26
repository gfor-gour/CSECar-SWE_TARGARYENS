import os
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    anthropic_api_key: Optional[str] = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    port: int = Field(default=8000, validation_alias="PORT")

    # ---- AI-layer settings (additive; default OFF) ----
    # The AI layer is an optional augmentation of the deterministic
    # pipeline. It is OFF by default and only activates when
    # LLM_ENABLED is set to a truthy value in the environment.
    llm_enabled: bool = Field(default=False, validation_alias="LLM_ENABLED")
    anthropic_model: str = Field(
        default="claude-opus-4-8",
        validation_alias="ANTHROPIC_MODEL",
    )
    anthropic_fallback_model: str = Field(
        default="claude-3-5-sonnet-latest",
        validation_alias="ANTHROPIC_FALLBACK_MODEL",
    )
    llm_timeout_seconds: float = Field(
        default=8.0,
        validation_alias="LLM_TIMEOUT_SECONDS",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
