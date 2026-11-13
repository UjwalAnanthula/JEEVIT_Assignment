# Scores the pipeline against hand-written answers: categories, fields, grouping, deal terms, risks, actions.
# Usage: python -m evaluation.evaluate [--set sample|holdout|all] [--ai]  (default: all sets, rules only)
import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from app.core import config
from app.models.schemas import Analysis, Deal, EmailRecord, ReconciledTerms
from app.pipeline import PipelineResult, analyze_emails
from app.utils.helpers import brand_keys_match

ROOT = Path(__file__).resolve().parents[1]
SETS = {
    "sample": (ROOT / "data" / "inbox.json", ROOT / "evaluation" / "expected_results.json"),
    "holdout": (ROOT / "evaluation" / "holdout" / "inbox.json", ROOT / "evaluation" / "holdout" / "expected.json"),
}
FIELDS = ("brand", "fee", "currency", "payment_type", "deliverables", "deadline", "usage")


@dataclass
class Score:
    correct: int = 0
    total: int = 0

    def add(self, ok: bool) -> None:
        self.correct += int(ok)
        self.total += 1

    def __str__(self) -> str:
        pct = f"{100 * self.correct / self.total:.0f}%" if self.total else "n/a"
        return f"{self.correct}/{self.total} ({pct})"


@dataclass
class Report:
    name: str
    scores: dict[str, Score] = field(default_factory=lambda: defaultdict(Score))
    mismatches: list[str] = field(default_factory=list)
    fabricated: list[str] = field(default_factory=list)

    def check(self, metric: str, item: str, expected, got, ok: bool) -> None:
        self.scores[metric].add(ok)
        if not ok:
            self.mismatches.append(f"{item} {metric}: expected {expected!r}, got {got!r}")


# ---------------------------------------------------------------- normalisers
def deliverable_keys(items) -> list[str]:
    return sorted(f"{d.platform}:{d.type}:{d.quantity}" for d in items or [])


def deadline_value(timeline) -> str | None:
    if timeline is None:
        return None
    if timeline.end_date:
        return timeline.end_date.isoformat()
    return "relative" if timeline.relative else (timeline.start_date.isoformat() if timeline.start_date else None)


def usage_matches(expected: dict, usage) -> tuple[bool, dict]:
    got = {"months": usage.duration_months if usage else None,
           "perpetual": bool(usage and usage.perpetual), "whitelisting": bool(usage and usage.whitelisting)}
    return all(got[k] == v for k, v in expected.items()), {k: got[k] for k in expected}


def field_value(name: str, analysis: Analysis):
    x = analysis.extraction
    return {
        "brand": x.brand,
        "fee": x.compensation.amount if x.compensation else None,
        "currency": x.compensation.currency if x.compensation else None,
        "payment_type": x.payment_type,
        "deliverables": deliverable_keys(x.deliverables),
        "deadline": deadline_value(x.timeline),
        "usage": x.usage_rights,
    }[name]


def field_ok(name: str, expected, got) -> tuple[bool, object]:
    if name == "brand":
        return (expected is None and got is None) or brand_keys_match(expected, got), got
    if name == "fee":
        return (expected is None and got is None) or (
            expected is not None and got is not None and abs(expected - got) < 0.5), got
    if name == "deliverables":
        return sorted(expected) == got, got
    if name == "usage":
        return usage_matches(expected, got)
    return expected == got, got


# ---------------------------------------------------------------- scoring
def score(name: str, result: PipelineResult, expected: dict) -> Report:
    report = Report(name)
    analyses = {a.email_id: a for a in result.analyses}
    for email_id, exp in expected["emails"].items():
        a = analyses[email_id]
        report.check("category", email_id, exp["category"], a.category, a.category == exp["category"])
        for fname in FIELDS:
            if fname not in exp:
                continue
            ok, got = field_ok(fname, exp[fname], field_value(fname, a))
            report.check(f"field:{fname}", email_id, exp[fname], got, ok)
            if exp[fname] in (None, []) and not ok:
                report.fabricated.append(f"{email_id} {fname}: expected not stated, got {got!r}")
        codes = {r.code for r in a.risks}
        for code in exp.get("risks", []):
            report.check("risk_recall", email_id, code, sorted(codes), code in codes)
        unexpected_blocking = {r.code for r in a.risks if r.blocking} - set(exp.get("risks", []))
        report.check("no_false_blocking_risk", email_id, [], sorted(unexpected_blocking), not unexpected_blocking)
        if "action" in exp:
            report.check("email_action", email_id, exp["action"], a.recommendation, a.recommendation == exp["action"])

    # Grouping: exact partition match over all emails in the set.
    expected_groups = {frozenset(g) for g in expected.get("groups", [])}
    grouped = {i for g in expected_groups for i in g}
    expected_groups |= {frozenset([i]) for i in expected["emails"] if i not in grouped}
    got_groups = {frozenset(d.email_ids) for d in result.deals}
    for group in expected_groups:
        report.check("grouping", "+".join(sorted(group)), sorted(group),
                     sorted(next((g for g in got_groups if group & g), set())), group in got_groups)

    deals_by_email: dict[str, Deal] = {i: d for d in result.deals for i in d.email_ids}
    for email_id, exp in expected.get("deals", {}).items():
        deal = deals_by_email[email_id]
        terms: ReconciledTerms = deal.terms
        comp = terms.current("compensation")
        checks = {
            "action": deal.recommendation.action,
            "fee": comp.amount if comp else None,
            "currency": comp.currency if comp else None,
            "deliverables": deliverable_keys(terms.current("deliverables")),
            "deadline": deadline_value(terms.current("timeline")),
        }
        for key, want in exp.items():
            got = checks[key]
            ok = sorted(want) == got if key == "deliverables" else got == want
            report.check(f"deal:{key}", f"deal({email_id})", want, got, ok)
    return report


def run_set(name: str, use_ai: bool) -> tuple[Report, dict]:
    inbox_path, expected_path = SETS[name]
    profile = json.loads((ROOT / "data" / "creator_profile.json").read_text(encoding="utf-8"))
    emails = [EmailRecord.model_validate(e) for e in json.loads(inbox_path.read_text(encoding="utf-8"))]
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    settings = config.settings if use_ai else config.settings.model_copy(update={"ai_enabled": False})
    result = analyze_emails(emails, profile, settings)
    return score(name, result, expected), result.run_info


def render(reports: list[tuple[Report, dict]]) -> str:
    lines = []
    for report, info in reports:
        sources = ", ".join(f"{k}={v}" for k, v in info["extraction_sources"].items())
        lines += [f"## {report.name} ({info['emails']} emails, extraction: {sources})", "",
                  "| Metric | Score |", "|---|---|"]
        lines += [f"| {metric} | {s} |" for metric, s in sorted(report.scores.items())]
        lines += ["", f"Fabricated values (a value where the email states none): {len(report.fabricated)}"]
        lines += [f"- {f}" for f in report.fabricated]
        lines += ["", "Mismatches:" if report.mismatches else "Mismatches: none"]
        lines += [f"- {m}" for m in report.mismatches]
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the inbox assistant against hand-written answers.")
    parser.add_argument("--set", choices=["sample", "holdout", "all"], default="all")
    parser.add_argument("--ai", action="store_true", help="Use Groq if configured (default: rules only).")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "evaluation.md")
    args = parser.parse_args()
    names = ["sample", "holdout"] if args.set == "all" else [args.set]
    reports = [run_set(name, args.ai) for name in names]
    text = render(reports)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("# Evaluation\n\n" + text, encoding="utf-8")
    for report, _ in reports:
        print(f"[{report.name}] category {report.scores['category']} | "
              f"email action {report.scores['email_action']} | grouping {report.scores['grouping']} | "
              f"fabricated {len(report.fabricated)} | mismatches {len(report.mismatches)}")
    print(f"Full report: {args.output}")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
