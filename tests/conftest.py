# Shared fixtures: forced-offline settings, real-network blocking, the real profile and an email factory.
# Guarantees no test reaches Groq even when a developer has a real GROQ_API_KEY in .env.
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.core import config
from app.core.config import Settings
from app.models.schemas import EmailRecord, Extraction
from app.pipeline import PipelineResult, analyze_emails

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    monkeypatch.chdir(ROOT)  # relative paths such as Path("data") work from any launch directory
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    settings = Settings(_env_file=None, groq_api_key="", gemini_api_key="", ai_enabled=False,
                        ai_cache_enabled=False, ai_cache_dir=tmp_path / "ai-cache")
    monkeypatch.setattr(config, "settings", settings)

    def no_network(*_args, **_kwargs):
        raise RuntimeError("Real network access is disabled in tests; use httpx.MockTransport.")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    return settings


@pytest.fixture(scope="session")
def profile() -> dict:
    return json.loads((ROOT / "data" / "creator_profile.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def inbox() -> dict[str, EmailRecord]:
    raw = json.loads((ROOT / "data" / "inbox.json").read_text(encoding="utf-8"))
    return {item["id"]: EmailRecord.model_validate(item) for item in raw}


def build_email(
    id: str = "T-1",
    body: str = "",
    subject: str = "Collab",
    sender: str = "team@brand.com",
    received_at: str = "2026-11-05 10:00",
    **headers,
) -> EmailRecord:
    return EmailRecord.model_validate(
        {"id": id, "from": sender, "subject": subject, "body": body, "received_at": received_at, **headers}
    )


@pytest.fixture
def make_email() -> Callable[..., EmailRecord]:
    return build_email


@pytest.fixture
def analyze(profile: dict, offline: Settings) -> Callable[..., PipelineResult]:
    def _run(emails: list[EmailRecord], profile_override: dict | None = None, **kwargs) -> PipelineResult:
        return analyze_emails(emails, profile_override or profile, offline, **kwargs)

    return _run


class StubExtractor:
    """Returns pre-built extractions so policy tests do not depend on text parsing."""

    def __init__(self, extractions: dict[str, Extraction]) -> None:
        self.extractions = extractions
        self.clients: list = []
        self.stats: dict[str, int] = {}

    def extract(self, email: EmailRecord) -> Extraction:
        return self.extractions[email.id]


@pytest.fixture
def stub_extractor() -> type[StubExtractor]:
    return StubExtractor
