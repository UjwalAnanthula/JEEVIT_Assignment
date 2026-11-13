# Command-line entry point: processes the creator inbox and writes analyses, deals, drafts and a summary.
# Usage: python -m app.main [--data DIR] [--output DIR] [--no-ai] [--check-ai]
import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from pydantic import SecretStr

from app.core import config
from app.pipeline import run_pipeline
from app.services.ai_extractor import EmailExtractor, providers_from_settings
from app.services.email_parser import load_inputs

_KEY_FIELDS = {"groq": "groq_api_key", "gemini": "gemini_api_key"}


def check_ai(data_dir: Path) -> int:
    """One live call per configured provider on the first email: proves each key, model and schema."""
    base = config.settings.model_copy(update={"ai_cache_enabled": False})
    providers = providers_from_settings(base)
    if not providers:
        print("No AI provider configured: set GROQ_API_KEY and/or GEMINI_API_KEY in .env.")
        return 1
    _, emails = load_inputs(data_dir)
    failed = 0
    for provider in providers:
        # Blank every other key so this extractor talks to one provider only.
        only = base.model_copy(update={field: SecretStr("") for name, field in _KEY_FIELDS.items()
                                       if name != provider.name})
        extractor = EmailExtractor(only)
        x = extractor.extract(emails[0])
        label = f"{provider.name} ({provider.model}, {provider.response_format})"
        if x.source != provider.name:
            print(f"{label}: FAILED, fell back to rules ({x.fallback_reason}). See the warning above.")
            failed += 1
            continue
        usage = dict(extractor.client.usage) if extractor.client else {}
        print(f"{label}: OK on {emails[0].id}: brand={x.brand!r}, fee={x.compensation}, "
              f"deliverables={[d.label() for d in x.deliverables]}, tokens={usage}")
    return 1 if failed == len(providers) else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="JEEVIT Brand Deal Inbox Assistant")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--no-ai", action="store_true", help="Skip AI providers and use deterministic rules only.")
    parser.add_argument("--check-ai", action="store_true", help="Make one live call per configured AI provider.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    if args.check_ai:
        sys.exit(check_ai(args.data))

    settings = config.settings.model_copy(update={"ai_enabled": False}) if args.no_ai else config.settings
    analyses, deals, info = run_pipeline(args.data, args.output, settings)

    sources = ", ".join(f"{name}={count}" for name, count in info["extraction_sources"].items())
    actions = Counter(d.recommendation.action for d in deals)
    print(f"Processed {len(analyses)} emails into {len(deals)} deal groups in {info['total_seconds']}s.")
    print(f"Extraction: {sources}" + (f" (fallback: {info['fallback_reasons']})" if info["fallback_reasons"] else ""))
    for name, calls in info["ai_calls"].items():
        if name in info["ai_disabled"]:
            print(f"{name}: skipped this run ({info['ai_disabled'][name]}); see the warning above")
            continue
        if not calls:
            print(f"{name}: no API calls (answers came from the local cache, or it was not needed)")
            continue
        print(f"{name}: {calls} calls, tokens {info['ai_tokens'].get(name, {})}, "
              f"avg latency {info['ai_avg_latency_seconds'].get(name)}s")
    print(f"Reference date: {info['reference_date']} ({info['reference_date_source']})")
    print("Next steps: " + ", ".join(f"{a}={n}" for a, n in sorted(actions.items())))
    print(f"Outputs: {args.output.resolve()}")


if __name__ == "__main__":
    main()
