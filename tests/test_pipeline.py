# End-to-end regression tests on the supplied 15-email dataset, CLI and output files.
# The first test is the original baseline test, kept unchanged to protect existing behaviour.
import json
import sys
from pathlib import Path

from app.main import main
from app.pipeline import run

ROOT = Path(__file__).resolve().parents[1]


def test_sample_inbox(tmp_path):
    items,groups=run(Path("data"),tmp_path)
    assert len(items)==15
    assert all((tmp_path/name).exists() for name in ["analyses.json","deals.json","summary.md"])


def test_categories_match_hand_labels(tmp_path):
    expected = json.loads((ROOT / "evaluation" / "expected_results.json").read_text(encoding="utf-8"))
    analyses, _ = run(ROOT / "data", tmp_path)
    assert {a.email_id: a.category for a in analyses} == expected


def test_dew_lab_deal_reconciles_latest_terms(tmp_path):
    analyses, deals = run(ROOT / "data", tmp_path)
    dew = next(d for d in deals if "E-01" in d.email_ids)
    assert dew.email_ids == ["E-01", "E-02", "E-11"] and dew.agency == "Brightside Agency"
    fee = dew.terms.compensation
    assert (fee.value.amount, fee.value.currency, fee.source_email_id, fee.status) == (100000, "INR", "E-02", "confirmed")
    assert [e.value.amount for e in fee.history] == [120000, 100000, 100000]
    assert {"below_rate", "agency_unverified", "fee_reduced"} <= {r.code for r in dew.risks}
    assert dew.recommendation.action == "negotiate"
    # Email-level records keep each email's own values and context.
    by_id = {a.email_id: a for a in analyses}
    assert by_id["E-01"].fee == 120000 and not by_id["E-01"].is_latest_in_deal
    assert by_id["E-01"].recommendation == "accept"  # sensible at the time, superseded by E-02
    assert by_id["E-02"].deliverables == [] and by_id["E-02"].terms_in_context.deliverables is not None
    assert by_id["E-11"].is_latest_in_deal and by_id["E-11"].deal_id == dew.deal_id


def test_provenance_and_outputs(tmp_path):
    run(ROOT / "data", tmp_path)
    analyses = json.loads((tmp_path / "analyses.json").read_text(encoding="utf-8"))
    info = json.loads((tmp_path / "run_info.json").read_text(encoding="utf-8"))
    assert {a["extraction"]["source"] for a in analyses} == {"rules"}
    assert all(a["extraction"]["fallback_reason"] == "ai_disabled" for a in analyses)
    assert info["extraction_sources"] == {"rules": 15} and info["groq_http_calls"] == 0
    assert info["reference_date"] == "2026-11-12"
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "Needs your attention today" in summary and "Security warnings" in summary
    assert "5,00,000" not in summary  # injected 'fee' from E-07 is never presented as an offer


def test_cli_runs_offline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["app.main", "--data", str(ROOT / "data"), "--output", str(tmp_path), "--no-ai"])
    main()
    out = capsys.readouterr().out
    assert "Processed 15 emails into 13 deal groups." in out and "rules=15" in out


def test_classify_compat_wrapper(inbox, profile):
    from app.services.rule_classifier import classify

    assert classify(inbox["E-14"], profile).category == "scam_or_phishing"
