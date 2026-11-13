# JEEVIT — Brand Deal Inbox Assistant

Python pipeline starter for classifying creator brand emails, flagging policy risks, grouping related messages and generating next steps. Includes the supplied 15-email dataset.

## Run locally
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m app.main
```

Outputs: `output/analyses.json`, `output/deals.json`, `output/summary.md`.

## Run with Docker
```bash
docker compose up --build
```

## Design and evaluation
The initial implementation is deterministic and runs without an API key. Creator policy checks are plain Python so suspicious requests and restrictions are not delegated to a model. `evaluation/expected_results.json` contains initial expected categories; run `python -m evaluation.evaluate` to measure the current baseline. Review field extraction and grouping manually before submission.

## Cost and scale
Rules-only processing has no model cost. At 1,000 emails/day, measure throughput and add batching, retries, monitoring and persistence if needed. With another week, add PostgreSQL, inbox import, a review UI, authenticated creator workspaces and broader evaluation.

## AI coding tools
Document the AI tools actually used, what they helped generate, and what you reviewed and tested. Do not claim unperformed tests or results.
