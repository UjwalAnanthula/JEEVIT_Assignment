# Loads optional model credentials and centralizes project paths.
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
ROOT_DIR = Path(__file__).resolve().parents[2]
class Settings(BaseSettings):
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
settings = Settings()
