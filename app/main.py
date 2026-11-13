# Command-line entry point: processes the creator inbox and writes analyses, deals and a summary.
# Usage: python -m app.main [--data DIR] [--output DIR] [--no-ai]
import argparse
import logging
from collections import Counter
from pathlib import Path

from app.core import config
from app.pipeline import run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="JEEVIT Brand Deal Inbox Assistant")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--no-ai", action="store_true", help="Skip Groq and use deterministic rules only.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    settings = config.settings.model_copy(update={"ai_enabled": False}) if args.no_ai else config.settings
    analyses, deals, info = run_pipeline(args.data, args.output, settings)

    sources = ", ".join(f"{name}={count}" for name, count in info["extraction_sources"].items())
    actions = Counter(d.recommendation.action for d in deals)
    print(f"Processed {len(analyses)} emails into {len(deals)} deal groups.")
    print(f"Extraction: {sources}" + (f" (fallback: {info['fallback_reasons']})" if info["fallback_reasons"] else ""))
    print(f"Reference date: {info['reference_date']} ({info['reference_date_source']})")
    print("Next steps: " + ", ".join(f"{a}={n}" for a, n in sorted(actions.items())))
    print(f"Outputs: {args.output.resolve()}")


if __name__ == "__main__":
    main()
