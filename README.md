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
| `python -m pytest -q` | 112 offline tests (HTTP is mocked; real network is blocked in tests) |
| `python -m evaluation.evaluate` | Scores against hand-written answers; writes `output/evaluation.md` |
| `python -m app.main --no-ai` | Force rules only |
| `docker compose up --build` | Same pipeline in a container (`.env` is read at runtime, never copied into the image) |

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

Expected answers were written by hand from the emails, not from system output. They cover category, extracted fields, grouping, reconciled deal terms, expected risk codes and the next action. Values "not stated" are scored too, so any **fabricated** value counts as an error.

There are two sets:
- **Sample:** the 15 supplied emails ([evaluation/expected_results.json](evaluation/expected_results.json)).
- **Held-out:** 12 synthetic emails ([evaluation/holdout/](evaluation/holdout)). These were written *after* the system was built but *before* it was run on them: new phrasings, a threaded follow-up with a changed subject, a look-alike Instagram domain, a crypto exchange, a "5G phone" competitor, an onboarding deposit, Hinglish, and ad-usage wording without the word "usage".

**Results (rules mode):**

| Metric | Sample (15) | Held-out (12), first run | Held-out after fixes |
|---|---|---|---|
| Category | 15/15 | 12/12 | 12/12 |
| Next action (per email) | 15/15 | **11/12** | 12/12 |
| Grouping (exact) | 13/13 | 11/11 | 11/11 |
| Risk recall | 14/14 | **6/8** | 8/8 |
| Extracted fields | 99% (fee 14/15 at first) | usage **2/3**; others 100% | 100% |
| Reconciled deal terms | 5/5 | deliverables **0/1** | 5/5 |
| Fabricated values | **1** at first, then 0 | 0 | 0 |

**What it got wrong, and the general fixes:**
1. **H-12** said **accept** on *"run it as partnership ads from your handle for 12 months"*. Usage rights were only recognised via words like "usage" or "rights". Ad-usage phrasing is now detected, so whitelisting and 12 > 3 months lead to "negotiate".
2. **H-01/H-02:** "add one more story" was not understood, so the deal stayed at 2 stories. Additions are now extracted and applied during reconciliation, with history.
3. **E-07:** the rules extractor read the injected "Rs 5,00,000" as a fee. Deal terms already excluded it; now amounts inside AI-directed instructions are ignored at extraction too.

**Caveats.** After these fixes the held-out set is no longer a blind test, so the honest blind number is the first run. Both sets are small and written by the same person who built the system. Treat this as regression protection plus an error analysis, not as an accuracy claim.

**Groq mode** (`python -m evaluation.evaluate --ai`, live `openai/gpt-oss-20b`, 13 Nov 2026). Detected scams skip the model by design.

| Metric | Sample: first Groq run | Sample: with floor | Held-out: first Groq run | Held-out: with floor |
|---|---|---|---|---|
| Category | **14/15** | 15/15 | 12/12 | 12/12 |
| Next action | **14/15** | 15/15 | **11/12** | 12/12 |
| Grouping / risk recall | 13/13 / 14/14 | 13/13 / 14/14 | 11/11 / **7/8** | 11/11 / 8/8 |
| Fields | 100% | 100% | currency **7/8**, usage **2/3** | brand 8/9, rest 100% |
| Fabricated values | 0 | 0 | 0 | **1** |

The first Groq run showed the model **under-reporting** things that are plainly in the text:
- **E-15:** "campaign brief attached" was marked *not a collaboration*, so a real lead became spam.
- **H-02:** "we can increase the budget" was given role *initial_offer*, so the deal went to manual review.
- **H-04:** no currency for "$1,000".
- **H-12:** no whitelisting for "partnership ads from your handle".
- **H-08:** one transient HTTP 400.

The fix applies the same principle as the decisions, now at extraction: a **deterministic floor**. The model can add information but cannot drop a signal that plain code reads directly from the email: collaboration intent, explicit revision wording, a stated currency, usage/whitelisting flags. Each reinforcement is written to the extraction's `uncertainties`. A 400 is retried once.

The one remaining "fabrication" is H-08's brand. The model inferred `bluedoor.cafe` from the sender address `bluedoor.cafe@gmail.com`, while the label says the email doesn't name a brand. It is scored as an error under the strict rule rather than relabelled.

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
- **Keyword-based policy checks.** Synonym lists cover common phrasings ("fantasy cricket", "5G phone"); negations like "we are not a betting app" are handled. Truly novel wording can still slip through.
- **Injection detection** is a pattern list plus the model's own flag. A novel phrasing could evade both. Even then, the model cannot change category, action, rates or replies.
- **Drafts** are English templates; a reply to a Hinglish email is still English. Attachments are never opened.
- **Exclusivity matching** is by category keywords. It does not know every competitor brand by name.

## With another week

These are the improvements I would make next, in priority order. They are suggestions, not part of this submission.

**1. Know how good it is on real mail**
- Label 100+ real, anonymised creator emails with two independent reviewers, and measure agreement between them.
- Evaluate Groq, Gemini and rules separately per field, and run the evaluation in CI so a prompt or rule change can't silently lower accuracy.
- Add per-field confidence, so low-confidence extractions are routed to review instead of acted on.

**2. Connect to a real inbox and keep history**
- Gmail/Outlook read-only import (OAuth) using real thread headers (`Message-ID`, `In-Reply-To`) for grouping.
- PostgreSQL for emails, extractions and deal events, keyed by `Message-ID` so reruns are idempotent.
- A queue with a paced worker for 1,000+ emails/day, plus alerts on fallback rate, validation errors and spend.

**3. A review screen the creator would trust**
- One screen per deal: latest terms, term history, risks with evidence, and an editable draft with "mark as sent".
- Store the creator's edits and overrides as new labels for evaluation.

**4. Better extraction and safety**
- Read PDF/DOCX briefs (like E-15's attachment) in a sandbox instead of only asking for the details.
- Add a dedicated prompt-injection classifier as an extra signal alongside the current patterns.
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
  - the evaluation harness and held-out set;
  - tests, Docker hardening and this README draft.
- It ran the test suite, the pipeline and the evaluation after each change.
- The model choice was checked against Groq's live documentation.

All numbers in this README come from commands run in this repository. The expected answers were written before scoring, and the first-run held-out results are reported as they were.

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
evaluation/              expected answers, held-out set, evaluate.py
tests/                   112 offline tests
```
