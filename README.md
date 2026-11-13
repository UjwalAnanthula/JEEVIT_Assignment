# JEEVIT: Brand Deal Inbox Assistant

Reads a creator's brand inbox and tells them, for every email and every deal: **what it is, what the deal actually says, what's risky, and what to do next**, with a draft reply where a reply makes sense. Nothing is ever sent.

It runs fully offline on deterministic rules. When API keys are configured it uses an LLM for extraction: **Groq first, Gemini if Groq fails, rules if both fail**.

## Quick start (fresh clone)

```bash
git clone https://github.com/UjwalAnanthula/JEEVIT_Assignment.git
cd JEEVIT_Assignment
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m app.main              # no key needed: deterministic rules
```

Optional AI extraction:

```bash
copy .env.example .env          # macOS/Linux: cp .env.example .env
# set GROQ_API_KEY (console.groq.com) and, optionally, GEMINI_API_KEY (Google AI Studio)
python -m app.main --check-ai   # one live call per configured provider
python -m app.main              # per email: Groq -> Gemini -> rules
```

Other commands:

| Command | What it does |
|---|---|
| `python -m pytest -q` | 108 offline tests (HTTP is mocked; real network is blocked in tests) |
| `python -m evaluation.evaluate` | Scores against hand-written answers; writes `output/evaluation.md` |
| `python -m app.main --no-ai` | Force rules only |

**Outputs** (in `output/`): start with `summary.md` (what needs attention today), then `drafts.md` (reply drafts). `deals.json` and `analyses.json` hold the full structured records, including evidence and term history. `run_info.json` records provenance, timing and token usage.

## What the creator sees (excerpt from `summary.md`)

> **Dew Lab (via Brightside Agency) [E-01, E-02, E-11]**
> **Next step: negotiate.** Latest terms: INR 1,00,000 | 2 x Instagram reel, 3 x Instagram story | 15 Nov-25 Nov 2026
> - Rate check: Offer INR 1,00,000 is INR 10,000 (9.1%) below the base rate of INR 1,10,000.
> - MEDIUM: Agency authority not verified. Brightside Agency (brightside-agency.com) says it represents Dew Lab. Earlier brand emails came from dewlab.in. It asks: 'Please send your GST details and confirm.'
> - To do: Counter at INR 1,10,000 or more ... Confirm with Dew Lab directly before sharing GST details.

## Design: what the model does, what plain code does

```
inbox.json -> security scan (code) -> extraction (Groq -> Gemini -> rules) -> grouping (code)
           -> term reconciliation (code) -> creator-policy checks (code) -> category + action (code)
           -> draft reply (code templates) -> summary.md / drafts.md / deals.json
```

| Step | Who | Why |
|---|---|---|
| Read messy text into fields: brand, agency, deliverables, fee, currency, dates, usage wording | **Groq** (`openai/gpt-oss-20b`), else **Gemini** (`gemini-3.5-flash-lite`), else rules | Language is varied (Hinglish, paraphrase, agencies). This is where a model helps. |
| Scam/phishing/injection detection | **Code** | Must not be fooled by the very text it is judging. |
| Grouping emails into deals; "latest terms" | **Code** | Needs to be explainable: every merge records its evidence. |
| Rates, currency conversion, exclusivity, content rules, deadlines, usage rights | **Code** | Arithmetic and policy must be exactly right and testable. |
| Category, next action, draft replies | **Code** | The model can never accept a deal, lower a security verdict or invent terms in a reply. |

**Safety rules for untrusted email:**
- The email is fenced as data with an unguessable marker the email can't spoof.
- Output must match a strict JSON schema and is validated again with Pydantic.
- Any fee or currency the model reports that isn't literally in the email is discarded.
- Emails that code already flags as scams are **never sent to the model**.
- The creator profile is **never sent to the model**.
- The model's opinion can only *add caution* (an "accept" becomes "review manually"). It can never change a category.
- **Deterministic floor:** the model cannot drop a signal plain code reads from the same text (collaboration intent, explicit revision wording, a stated currency, usage/whitelisting flags). This was added after the first live run; see Evaluation.

**Fallback chain and provenance.** For each email, the configured providers are tried in order: Groq, then Gemini (both through their OpenAI-compatible APIs), then the deterministic rules. A provider without a key is skipped. Each email records which source answered (`groq`, `gemini` or `rules`) and why earlier ones failed, e.g. `groq:rate_limited` or `groq:timeout; gemini:invalid_json`. The client:
- retries using `retry-after`;
- paces itself on Groq's rate-limit headers (the free tier allows 8K tokens/min);
- stops calling a provider after auth, missing-model or daily-limit errors, so later emails go straight to the next one;
- caches validated output per provider and model.

The same safety rules, grounding and deterministic floor apply whichever provider answers. Both models share the strict JSON schema.

**Model choice.** `llama-3.3-70b-versatile` became Enterprise-only on Groq (checked Nov 2026), so the default is the free-tier `openai/gpt-oss-20b` with strict `json_schema` output and low reasoning effort. Gemini's fallback default is the stable `gemini-3.5-flash-lite` (`gemini-2.5-flash` is deprecated). Both are configurable in `.env`. A preflight check against each provider's `/models` fails loudly instead of silently falling back.

**Trade-off.** When Gemini answers instead of Groq, results can differ slightly, which is why provenance records the provider per email. The evaluation figures below were measured with Groq. Gemini is only used when Groq fails, and has not been evaluated live yet (no Gemini key during development).

## The tricky emails

| Email | Result | Why |
|---|---|---|
| E-01, E-02, E-11 | One Dew Lab deal → **negotiate** | E-02 is the same subject thread. E-11 is the same brand and campaign ("SPF serum"). Latest fee is ₹1,00,000 (E-02, confirmed by E-11) vs a ₹1,10,000 rate; the history keeps E-01's ₹1,20,000. The agency is a different domain, so verify it before sending GST details. |
| E-03 | **Scam** | Asks the creator to pay a registration fee; breaks "Never pays to join". |
| E-04 | **Decline** | Smartphone campaign before 31 Jan 2027 conflicts with the Nova Phones exclusivity. The draft doesn't name the partner. |
| E-05 | **Decline** | Fantasy cricket matches the "No … fantasy-sports" rule. ₹3 lakh doesn't override it. |
| E-06 | **Negotiate** | "Budget 25k" has no currency. It is kept as *unknown*, compared as INR with that assumption shown, and the draft asks to confirm. |
| E-07 | **Scam** | Prompt injection and a request for home address/phone. Its "₹5,00,000" is never treated as a fee. |
| E-09 | **Negotiate** | Product worth ₹3,500 for content worth ₹60,000 at base rates; gifting is not cash. |
| E-10 | **Negotiate** | The fee meets the rate (USD 800 = ₹67,200 at the profile's 84), but perpetual + all-media + whitelisting go beyond the 3 months included. |
| E-12 | **Decline** | Due 1 Nov; the reference date is the profile's `today` (12 Nov), not the machine clock. |
| E-14 | **Scam** | `youtube-copyright-centre.net` is not a YouTube domain; "verify your account" link plus a suspension threat. |
| E-15 | **Ask for details** | The brief is an attachment that isn't opened; no terms are invented. |

## Evaluation

I wrote expected answers by hand for all 15 emails ([evaluation/expected_results.json](evaluation/expected_results.json)), from the emails themselves rather than from system output. Each one covers:
- the category;
- the key fields: brand, fee, currency, payment type, deliverables, deadline, usage rights;
- which emails form one deal, and the deal's latest terms;
- the risks that must be flagged;
- the next step.

Fields that the email doesn't state are scored too, so a **made-up value** counts as an error. Run `python -m evaluation.evaluate` (rules) or `--ai` (live model).

| Metric | Rules, first run | Rules, now | Groq, first run | Groq, now |
|---|---|---|---|---|
| Category | 15/15 | 15/15 | **14/15** | 15/15 |
| Next step (per email) | 15/15 | 15/15 | **14/15** | 15/15 |
| Deal grouping | 13/13 | 13/13 | 13/13 | 13/13 |
| Risks flagged | 14/14 | 14/14 | 14/14 | 14/14 |
| Fields | fee **14/15**, rest 100% | 100% | 100% | 100% |
| Dew Lab deal's latest terms | 5/5 | 5/5 | 5/5 | 5/5 |
| Made-up values | **1** | 0 | 0 | 0 |

Groq ran live with `openai/gpt-oss-20b` on 13 Nov 2026. Emails already flagged as scams are not sent to the model.

**What it got wrong, and the fixes:**
1. **Rules, E-07:** the rule extractor read the injected "Rs 5,00,000" as a fee. Deal terms already excluded it; now an amount inside instructions aimed at an AI is never read as a fee.
2. **Groq, E-15:** the model marked "please find our campaign brief attached" as *not a collaboration*, so a real lead became spam. In testing with other wording it also dropped a stated currency ("$1,000") and a whitelisting request. The fix is a **deterministic floor**: the model can add information, but it cannot drop a signal plain code reads directly from the email (collaboration language, explicit revision wording, a stated currency, usage/whitelisting terms). Each time this happens it is noted in the extraction.

**How far to trust these numbers.** 15 emails is a small set, and I wrote the expected answers after seeing the emails while building the system. So 15/15 shows the tricky cases are handled as intended, not that it will be 100% on new mail. Measuring that properly is the first item under "With another week".

## Cost and time per email

Measured on the 15-email inbox (live run, 13 Nov 2026):

| | Rules | Groq `gpt-oss-20b` |
|---|---|---|
| Time | ~2 ms/email | **0.84 s** average per call |
| Tokens | 0 | **~1,480 input + ~250 output** per email (12 calls, 20,738 tokens total) |
| Cost | 0 | ≈ **$0.0002/email** at $0.075/M input and $0.30/M output (Developer plan prices) |

The whole 15-email Groq run took 114 s, almost all of it pacing for the free tier's 8K tokens/min. Three detected scams were not sent to the model.

**At 1,000 emails/day:**
- **Cost:** about $0.19/day (~$6/month) on the Developer plan.
- **Free tier won't do it:** at 8K tokens/min and 200K tokens/day it handles about 4–5 emails/min and roughly 115/day. The Developer plan (250K tokens/min) handles it easily.
- **Fewer model calls:** the rules prefilter skips detected scams (3 of 15 here), and the content cache stops repeat calls.

What I'd change for that volume:
- a queue with one paced worker;
- Postgres for emails, extractions and deal events, keyed by `Message-ID` so reruns are idempotent;
- alerts on fallback rate, validation errors and spend;
- Groq Batch for non-urgent backfills.

## Limitations

- **Rules extraction is heuristic.** Brand and campaign come from text patterns. Unusual wording falls back to null rather than a guess, but some will be missed. Groq covers this better.
- **Documented assumptions, not facts:**
  - INR for an amount with no currency (E-06), shown as an assumption;
  - Instagram for reels/stories with no platform named;
  - the year for dates without one, inferred from the email's received date.
- **Keyword-based policy checks.** Synonym lists cover common phrasings ("fantasy cricket", "5G phone"), but negation is not understood: "we are not a betting app" would still trip the no-betting rule. New wording can slip through.
- **Injection detection** is a short pattern list (it catches E-07's "Note to any AI assistant…" and "Classify this email as…") plus the model's own flag. A differently worded attack could evade both. Even then, the model cannot change the category, next step, rates or replies.
- **Drafts** are English templates; a reply to a Hinglish email is still English. Attachments are never opened.
- **Exclusivity matching** is by category keywords. It does not know every competitor brand by name.

## With another week

These are the improvements I would make next, in priority order. They are suggestions, not part of this submission.

**1. Know how good it is on real mail**
- Write a held-out set of new emails *before* running the system on them (new phrasings, look-alike domains, agency follow-ups, Hinglish) and report the first, blind score.
- Label 100+ real, anonymised creator emails with two independent reviewers, and measure agreement between them.
- Evaluate Groq, Gemini and rules separately per field, and run the evaluation in CI so a prompt or rule change can't silently lower accuracy.
- Add per-field confidence, so low-confidence extractions are routed to review instead of acted on.

**2. Connect to a real inbox and keep history**
- Gmail/Outlook read-only import (OAuth) using real thread headers (`Message-ID`, `In-Reply-To`) for grouping.
- PostgreSQL for emails, extractions and deal events, keyed by `Message-ID` so reruns are idempotent.
- A queue with a paced worker for 1,000+ emails/day, plus alerts on fallback rate, validation errors and spend.
- Package it as a Docker image for deployment, with a `.dockerignore` so `.env` and API keys never end up inside the image.

**3. A review screen the creator would trust**
- One screen per deal: latest terms, term history, risks with evidence, and an editable draft with "mark as sent".
- Store the creator's edits and overrides as new labels for evaluation.

**4. Better extraction and safety**
- Read PDF/DOCX briefs (like E-15's attachment) in a sandbox instead of only asking for the details.
- Stronger injection detection: more phrasings ("if you are an AI…", fake `System:` blocks, "ignore your instructions"), flag hidden text (HTML comments, zero-width characters), and a dedicated prompt-injection classifier as an extra signal.
- Understand negation in content rules, so "we are not a betting app" is not treated as a betting app.
- Recognise competitor brands by name, not only by category keywords, for exclusivity checks.
- Replies in the brand's language (Hindi/Hinglish) when the email is written that way.
- Use the Groq Batch API for non-urgent backfills to cut cost.

**5. Creator-editable policy**
- Turn the profile's free-text rules ("No crypto or trading-tip promotions") into structured settings the creator edits: blocked categories, minimum rates per deliverable, usage and whitelisting pricing, barter preferences. This removes the need to parse sentences.

## Where AI coding tools were used

- **ChatGPT** was used to plan the work and to generate the initial starter (the `Baseline starter` commit).
- **Claude Code** (Anthropic) implemented Batches 1–5 in this repository:
  - an audit of the starter;
  - the Groq client and fallback;
  - grouping, reconciliation and policy checks;
  - draft replies;
  - the evaluation harness;
  - tests and this README draft.
- It ran the test suite, the pipeline and the evaluation after each change.
- The model choice was checked against Groq's live documentation.

All numbers in this README come from commands run in this repository, and first-run results are reported as they were.

## Project layout

```
app/
  main.py                CLI (--no-ai, --check-ai)
  pipeline.py            orchestration; writes all outputs
  core/config.py         settings from env/.env (key is a SecretStr)
  models/schemas.py      Pydantic models: email, extraction, terms, risks, deals, drafts
  services/
    ai_extractor.py      Groq -> Gemini chain, strict schema, grounding, pacing, cache, rules fallback
    rule_classifier.py   rules extraction, security signals, category decision
    deal_grouper.py      thread/subject/brand+campaign grouping with evidence
    term_reconciler.py   latest-terms-by-timestamp with full history
    risk_analyzer.py     creator-policy checks, rate maths, risks
    recommendation.py    next action, summary.md, drafts.md
    reply_generator.py   draft replies (never sent)
  utils/helpers.py       money (lakh/k), dates, domains, sentences
evaluation/              expected answers for the 15 emails, evaluate.py
tests/                   108 offline tests
```
