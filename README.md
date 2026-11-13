# JEEVIT: Brand Deal Inbox Assistant

Reads a creator's brand inbox and tells them, for every email and deal, **what it is, what the deal says, what's risky and what to do next**, with a draft reply where one makes sense. Nothing is ever sent.

It works offline using deterministic rules. With API keys set, it uses an LLM for extraction: **Groq first, then Gemini, then rules**.

## Quick start

```bash
git clone https://github.com/UjwalAnanthula/JEEVIT_Assignment.git
cd JEEVIT_Assignment
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m app.main              # no key needed: deterministic rules
```

Results go to `output/`. Start with `summary.md` (what needs attention today), then `drafts.md` (reply drafts). `deals.json` and `analyses.json` hold the full records, with evidence and term history. `run_info.json` records provenance, timing and token usage.

### Dashboard

```bash
python -m streamlit run dashboard.py   # opens http://localhost:8501
```

A read-only view of the same results:
- It opens on **Needs attention**. The tabs switch to All emails, Decline, Scam warnings or Ignored.
- Click a row to see the deal's next step, terms checked against your rate, risks with evidence, suggested actions and a draft reply you can copy. **← Back** returns to the list.
- The **Analysis** menu switches between rules and AI (when keys are set) and re-runs the analysis.

It runs on localhost only and sends no usage statistics. Email text is shown as plain text, so phishing links are never clickable.

### Optional: AI extraction

```bash
copy .env.example .env          # macOS/Linux: cp .env.example .env
# set GROQ_API_KEY (console.groq.com) and, optionally, GEMINI_API_KEY (Google AI Studio)
python -m app.main --check-ai   # one live call per configured provider
python -m app.main              # per email: Groq -> Gemini -> rules
```

### Other commands

| Command | What it does |
|---|---|
| `python -m pytest -q` | 119 offline tests (network is blocked in tests) |
| `python -m evaluation.evaluate` | Scores the system against hand-written answers and writes `output/evaluation.md`. Add `--ai` to score the live model. |
| `python -m app.main --no-ai` | Rules only, even when keys are set |

## What the creator sees (excerpt from `summary.md`)

> **Dew Lab (via Brightside Agency) [E-01, E-02, E-11]**
> **Next step: negotiate.** Latest terms: INR 1,00,000 | 2 x Instagram reel, 3 x Instagram story | 15 Nov-25 Nov 2026
> - Rate check: Offer INR 1,00,000 is INR 10,000 (9.1%) below the base rate of INR 1,10,000.
> - MEDIUM: Agency authority not verified. Brightside Agency (brightside-agency.com) says it represents Dew Lab. Earlier brand emails came from dewlab.in. It asks: 'Please send your GST details and confirm.'
> - To do: Counter at INR 1,10,000 or more ... Confirm with Dew Lab directly before sharing GST details.

## Project layout

```
dashboard.py             Streamlit dashboard (localhost:8501)
ui/                      dashboard components (components.py) and styles (theme.css)
.streamlit/config.toml   dashboard settings: localhost only, no usage statistics
app/
  main.py                CLI (--no-ai, --check-ai)
  pipeline.py            orchestration; writes all outputs
  core/config.py         settings from env/.env (keys are SecretStr)
  models/schemas.py      Pydantic models: email, extraction, terms, risks, deals, drafts
  services/
    ai_extractor.py      Groq -> Gemini chain, strict schema, grounding, pacing, cache, rules fallback
    rule_classifier.py   rules extraction, security signals, category decision
    deal_grouper.py      thread/subject/brand+campaign grouping with evidence
    term_reconciler.py   latest terms by timestamp, with full history
    risk_analyzer.py     creator-policy checks, rate maths, risks
    recommendation.py    next step, summary.md, drafts.md
    reply_generator.py   draft replies (never sent)
  utils/helpers.py       money (lakh/k), dates, domains, sentences
evaluation/              expected answers for the 15 emails, evaluate.py
tests/                   119 offline tests, including a click-through of the dashboard
```

## Design

```
inbox.json -> security scan -> extraction (Groq -> Gemini -> rules) -> grouping -> term reconciliation
           -> creator-policy checks -> category + next step -> draft reply -> summary.md / drafts.md / deals.json
```

Only the extraction step uses a model. Everything else is plain code.

| Step | Who | Why |
|---|---|---|
| Read messy text into fields (brand, agency, deliverables, fee, currency, dates, usage) | **Groq** `openai/gpt-oss-20b`, else **Gemini** `gemini-3.5-flash-lite`, else rules | The wording varies (Hinglish, paraphrase, agencies), and that is where a model helps. |
| Scam, phishing and injection detection | Code | It must not be fooled by the text it is judging. |
| Grouping emails into deals; latest terms | Code | Every merge records its evidence. |
| Rates, currency, exclusivity, content rules, deadlines, usage rights | Code | Arithmetic and policy must be exact and testable. |
| Category, next step, draft replies | Code | The model can never accept a deal, lower a security verdict or invent terms. |

**Safety with untrusted email**
- The email is fenced as data with a random marker the email can't spoof.
- The model's output must match a strict JSON schema and is validated again with Pydantic.
- If the model reports a fee or currency that isn't in the email, it is discarded.
- Emails already flagged as scams are never sent to the model, and neither is the creator profile.
- The model can only add caution. It can never change a category.
- **Deterministic floor:** the model can't drop a signal that plain code reads from the same text: collaboration intent, revision wording, a stated currency, usage or whitelisting terms.

**Fallback.** Each email goes to Groq, then Gemini (both through their OpenAI-compatible APIs), then rules. A provider without a key is skipped. Each email records which source answered and why earlier ones failed, e.g. `groq:rate_limited`. The client:
- retries using `retry-after`;
- paces itself using Groq's rate-limit headers;
- stops calling a provider after an auth, missing-model or daily-limit error;
- caches validated output.

To test the fallback, Groq was pointed at a model that doesn't exist. Groq was skipped and Gemini answered every email.

**Model choice.** `llama-3.3-70b-versatile` is Enterprise-only on Groq (checked Nov 2026), so the default is the free-tier `openai/gpt-oss-20b`, with strict JSON output and low reasoning effort. Gemini's default is `gemini-3.5-flash-lite`. Both can be changed in `.env`. Before the first call, each model is checked against its provider's `/models` list, so a wrong name fails loudly.

## The tricky emails

| Email | Result | Why |
|---|---|---|
| E-01, E-02, E-11 | One Dew Lab deal → **negotiate** | E-02 is in the same subject thread; E-11 has the same brand and campaign ("SPF serum"). The latest fee is ₹1,00,000 (E-02, confirmed by E-11) against a ₹1,10,000 rate; the history keeps E-01's ₹1,20,000. The agency writes from a different domain, so verify it before sending GST details. |
| E-03 | **Scam** | Asks the creator to pay a registration fee, which breaks the profile's "Never pays to join" rule. |
| E-04 | **Decline** | A smartphone campaign before 31 Jan 2027 conflicts with the Nova Phones exclusivity. The draft doesn't name the partner. |
| E-05 | **Decline** | Fantasy cricket matches the "No … fantasy-sports" rule. ₹3 lakh doesn't override it. |
| E-06 | **Negotiate** | "Budget 25k" has no currency. It is compared as INR, the assumption is shown, and the draft asks the brand to confirm. |
| E-07 | **Scam** | Prompt injection plus a request for home address and phone. Its "₹5,00,000" is never treated as a fee. |
| E-09 | **Negotiate** | A ₹3,500 product in exchange for content worth ₹60,000 at base rates. Gifting is not cash. |
| E-10 | **Negotiate** | The fee meets the rate (USD 800 = ₹67,200 at the profile's rate of 84), but perpetual, all-media usage with whitelisting goes beyond the 3 months included. |
| E-12 | **Decline** | Due 1 Nov. "Today" is the profile's date (12 Nov), not the machine clock. |
| E-14 | **Scam** | `youtube-copyright-centre.net` is not a YouTube domain; "verify your account" link plus a suspension threat. |
| E-15 | **Ask for details** | The brief is in an attachment, which isn't opened. No terms are invented. |

## Evaluation

I wrote the expected answers for all 15 emails by hand, from the emails themselves rather than from system output ([evaluation/expected_results.json](evaluation/expected_results.json)). They cover the category, key fields, deal grouping and latest terms, required risks and the next step. Fields an email doesn't state are scored too, so a **made-up value** counts as an error.

| Metric | Rules, first run | Rules, now | Groq, first run | Groq, now | Gemini (fallback) |
|---|---|---|---|---|---|
| Category | 15/15 | 15/15 | **14/15** | 15/15 | 15/15 |
| Next step (per email) | 15/15 | 15/15 | **14/15** | 15/15 | 15/15 |
| Deal grouping | 13/13 | 13/13 | 13/13 | 13/13 | 13/13 |
| Risks flagged | 14/14 | 14/14 | 14/14 | 14/14 | 14/14 |
| Fields | fee **14/15**, rest 100% | 100% | 100% | 100% | 100% |
| Dew Lab deal's latest terms | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |
| Made-up values | **1** | 0 | 0 | 0 | 0 |

The live runs were on 13 Nov 2026. For the Gemini column Groq was forced to fail, so Gemini answered all 12 non-scam emails.

**What went wrong, and the fixes**
1. **Rules, E-07:** the rules read the injected "Rs 5,00,000" as a fee. Now an amount inside instructions aimed at an AI is never treated as a fee.
2. **Groq, E-15:** the model decided "please find our campaign brief attached" was not a collaboration, so a real lead became spam. With other wording it also dropped a stated currency and a whitelisting request. The deterministic floor (see Design) fixes this.

**How far to trust this.** 15 emails is a small set, and I wrote the answers after seeing them. 15/15 shows that the tricky cases work as intended, not that new mail will score 100%.

## Cost and time per email

Measured on the 15-email inbox (13 Nov 2026):

| | Rules | Groq `gpt-oss-20b` | Gemini `gemini-3.5-flash-lite` |
|---|---|---|---|
| Time | ~2 ms per email | 0.84 s per call | 1.9 s per call |
| Tokens per email | 0 | ~1,480 in + ~250 out | ~680 in + ~360 out |
| Cost per email | 0 | ≈ $0.0002 (Developer plan: $0.075/M in, $0.30/M out) | not measured |

The full Groq run took 114 s, mostly waiting to stay under the free tier's 8K tokens/min. The Gemini run took 24 s.

**At 1,000 emails/day**
- **Cost:** about $0.19/day (~$6/month) on Groq's Developer plan.
- **Free tier:** too small; 8K tokens/min and 200K/day covers about 115 emails/day.
- **Fewer calls:** detected scams (3 of 15 here) and cached emails never reach the model.
- **What I'd add:**
  - a paced worker queue;
  - Postgres keyed by `Message-ID`, so reruns are idempotent;
  - alerts on fallback rate and spend;
  - Groq Batch for backfills.

## Limitations

- **Rules extraction is heuristic.** When the wording is unusual it leaves a field empty rather than guessing, so some details are missed.
- **Some values are assumed:**
  - INR when an amount has no currency (E-06);
  - Instagram when reels or stories name no platform;
  - the year, from the received date, when a date has none.

  Each assumption is shown in the output.
- **Policy checks use keywords.** Synonyms are covered, but negation isn't: "we are not a betting app" still trips the no-betting rule.
- **Injection detection** is a short pattern list plus the model's own flag, so new wording could slip through. Even then, the model can't change the category, next step, rates or replies.
- **Drafts are English templates,** even for Hinglish emails. Attachments are never opened.
- **Exclusivity is matched by category keywords,** not competitor brand names.

## With another week

In priority order. These are suggestions, not part of this submission.

1. **Measure it on real mail.**
   - A held-out set of emails written before running the system on them.
   - 100+ labelled real emails, checked by two reviewers.
   - Scores for each provider, run in CI.
   - Confidence for each field, so unsure extractions go to review.
2. **Real inbox and history.**
   - Read-only Gmail/Outlook import, grouped by thread headers.
   - Postgres keyed by `Message-ID`.
   - A paced worker queue with alerts.
   - A Docker image, with a `.dockerignore` so `.env` never ships.
3. **Dashboard as a review screen.**
   - Edit drafts and mark them as sent.
   - Correct a category or term.
   - Add login so it can be hosted.
   - Save each correction as a new evaluation label.
4. **Better extraction and safety.**
   - Read PDF/DOCX briefs in a sandbox.
   - Catch more injection wording and hidden text.
   - Understand negation in content rules.
   - Recognise competitor brands by name.
   - Use Groq Batch for backfills.
5. **AI-written replies with plain-code guardrails.**
   - Code decides what the reply must say: counter amount, usage, missing details, agency check.
   - Groq writes it naturally, in Hindi or Hinglish when the email is.
   - A check rejects any draft that changes an amount, adds a commitment or drops a point.
   - Today's template stays as the fallback.
6. **Creator-editable policy.** Turn the profile's free-text rules into settings the creator edits: blocked categories, minimum rates, usage and whitelisting prices, barter preferences.

## Where AI coding tools were used

- **ChatGPT** planned the work and generated the initial starter (the `Baseline starter` commit).
- **Claude Code** (Anthropic) did the rest in this repository:
  - the starter audit;
  - the Groq/Gemini client and fallback;
  - grouping, reconciliation and policy checks;
  - draft replies;
  - the evaluation harness;
  - the Streamlit dashboard;
  - the tests and this README draft.

  It ran the tests, pipeline and evaluation after each change, and checked the model choice against Groq's live docs.

All numbers here come from commands run in this repository. First-run results are reported as they were.