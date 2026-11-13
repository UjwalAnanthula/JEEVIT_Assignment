# Command-line entry point: processes the creator inbox and writes analyses, deals, drafts and a summary.
# Usage: python -m app.main [--data DIR] [--output DIR] [--no-ai] [--check-ai]
import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from app.core import config
from app.pipeline import run_pipeline
from app.services.ai_extractor import EmailExtractor
from app.services.email_parser import load_inputs


def check_ai(data_dir: Path) -> int:
    """One live Groq call on the first email: proves the key, model and schema work end to end."""
    settings = config.settings.model_copy(update={"ai_cache_enabled": False})
    print(f"Model: {settings.groq_model} | response format: {settings.response_format_mode} | "
          f"key configured: {settings.has_api_key}")
    extractor = EmailExtractor(settings)
    _, emails = load_inputs(data_dir)
    x = extractor.extract(emails[0])
    if x.source != "groq":
        print(f"AI check FAILED: fell back to rules ({x.fallback_reason}). See the warning above.")
        return 1
    usage = dict(extractor.client.usage) if extractor.client else {}
    print(f"AI check OK on {emails[0].id}: brand={x.brand!r}, fee={x.compensation}, "
          f"deliverables={[d.label() for d in x.deliverables]}, tokens={usage}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="JEEVIT Brand Deal Inbox Assistant")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--no-ai", action="store_true", help="Skip Groq and use deterministic rules only.")
    parser.add_argument("--check-ai", action="store_true", help="Make one live Groq call and report the result.")
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
    if info["groq_tokens"]:
        print(f"Groq: {info['groq_http_calls']} calls, tokens {info['groq_tokens']}, "
              f"avg latency {info['groq_avg_latency_seconds']}s")
    print(f"Reference date: {info['reference_date']} ({info['reference_date_source']})")
    print("Next steps: " + ", ".join(f"{a}={n}" for a, n in sorted(actions.items())))
    print(f"Outputs: {args.output.resolve()}")


if __name__ == "__main__":
    main()
