# Loads runtime settings (Groq credentials, AI switches, timeouts, reference date) from env/.env.
# The API key is a SecretStr so it never appears in logs, reprs or output files.
from datetime import date, timedelta, timezone
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]

# India has no DST, so a fixed +05:30 offset is exact and avoids a tzdata dependency on Windows.
LOCAL_TZ = timezone(timedelta(hours=5, minutes=30), "IST")


class Settings(BaseSettings):
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = "llama-3.3-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    ai_enabled: bool = True
    ai_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    ai_max_retries: int = Field(default=2, ge=0, le=5)
    ai_max_backoff_seconds: float = Field(default=10.0, ge=0)
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


settings = Settings()
