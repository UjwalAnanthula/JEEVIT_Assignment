# Loads runtime settings (Groq credentials, model, AI switches, pacing, reference date) from env/.env.
# The API key is a SecretStr so it never appears in logs, reprs or output files.
from datetime import date, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]

# India has no DST, so a fixed +05:30 offset is exact and avoids a tzdata dependency on Windows.
LOCAL_TZ = timezone(timedelta(hours=5, minutes=30), "IST")


class Settings(BaseSettings):
    groq_api_key: SecretStr = SecretStr("")
    # Free-tier production model (Nov 2026). llama-3.3-70b-versatile is now Enterprise-only on Groq.
    groq_model: str = "openai/gpt-oss-20b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    # auto = strict json_schema for models that support it (gpt-oss), json_object otherwise.
    groq_response_format: Literal["auto", "json_schema", "json_object"] = "auto"
    ai_enabled: bool = True
    ai_check_model: bool = True  # verify the model exists via /models before the first call
    ai_skip_threats: bool = True  # emails already flagged as scams by plain code are not sent to the model
    ai_reasoning_effort: Literal["low", "medium", "high"] = "low"
    ai_temperature: float = Field(default=0.2, ge=0, le=2)
    ai_max_completion_tokens: int = Field(default=2048, ge=256, le=32768)
    ai_timeout_seconds: float = Field(default=30.0, gt=0, le=180)
    ai_max_retries: int = Field(default=3, ge=0, le=6)
    ai_max_backoff_seconds: float = Field(default=60.0, ge=0)
    ai_cache_enabled: bool = True
    ai_cache_dir: Path = ROOT_DIR / ".cache" / "ai"
    # Overrides the creator profile's "today" when set (YYYY-MM-DD).
    reference_date: date | None = None

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    @property
    def has_api_key(self) -> bool:
        return bool(self.groq_api_key.get_secret_value().strip())

    @property
    def is_gpt_oss(self) -> bool:
        return self.groq_model.startswith("openai/gpt-oss")

    @property
    def response_format_mode(self) -> str:
        if self.groq_response_format != "auto":
            return self.groq_response_format
        return "json_schema" if self.is_gpt_oss else "json_object"


settings = Settings()
