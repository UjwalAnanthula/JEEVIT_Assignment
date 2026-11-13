# Renders the Streamlit dashboard headlessly (rules mode, no network) and clicks through every view, deal and email.
# Uses Streamlit's AppTest, so no browser or server is needed. UI-only: backend behaviour is tested elsewhere.
from pathlib import Path

from streamlit.testing.v1 import AppTest

from app.models.schemas import Deal, Risk
from ui import components as ui

DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard.py"
VIEW_COUNTS = {"Needs attention": 6, "Decline": 3, "Scam warnings": 3, "Ignored": 1, "All emails": 15}
VIEW_KEYS = {"Needs attention": "attention", "Decline": "decline", "Scam warnings": "scams", "Ignored": "ignored",
             "All emails": "emails"}


def run_dashboard() -> AppTest:
    return AppTest.from_file(str(DASHBOARD), default_timeout=60).run()


def buttons(app: AppTest, prefix: str) -> list:
    return [b for b in app.button if (b.key or "").startswith(prefix)]


def html_text(app: AppTest) -> str:
    return "\n".join(str(el.value) for el in app.get("html"))


def test_dashboard_renders_with_views_and_overview():
    app = run_dashboard()
    assert not app.exception
    nav = {b.label for b in buttons(app, "hit-nav-")}
    assert nav == {f"{name} ({count})" for name, count in VIEW_COUNTS.items()}
    page = html_text(app)
    assert "Brand Deal Inbox Assistant" in page and "Inbox overview" in page and "Emails by category" in page
    assert ">15</div>" in page and ">13</div>" in page  # emails and deals in the overview
    assert app.radio[0].options == ["Rules only"]  # no API keys in tests


def test_every_view_deal_and_email_opens_without_errors():
    app = run_dashboard()
    for name in VIEW_COUNTS:
        app.button(key=f"hit-nav-{VIEW_KEYS[name]}").click().run()
        assert not app.exception, name
        rows = buttons(app, "hit-deal-") + buttons(app, "hit-email-")
        assert len(rows) == VIEW_COUNTS[name], name
        for key in [b.key for b in rows]:
            app.button(key=key).click().run()
            assert not app.exception, key


def test_dew_lab_detail_shows_terms_risks_and_draft():
    app = run_dashboard()
    page = html_text(app)
    assert "Dew Lab" in page and "INR 1,00,000" in page and "below your rate" in page
    assert "Agency authority not verified" in page and "Suggested actions" in page
    assert len(app.code) == 1 and "INR 1,10,000" in app.code[0].value  # the selected deal's draft
    assert "Draft · not sent" in page


def test_email_reader_escapes_text_and_links_to_deal():
    app = run_dashboard()
    app.button(key="hit-nav-emails").click().run()
    app.button(key="hit-email-E-14").click().run()
    page = html_text(app)
    assert "http://youtube-copyright-centre.net/verify" in page and "<a " not in page  # never a clickable link
    app.button(key="open-deal").click().run()
    assert not app.exception and "Report scam" in html_text(app)


def test_search_filters_the_list():
    app = run_dashboard()
    app.text_input(key="q").input("auralbuds").run()
    assert [b.label for b in buttons(app, "hit-deal-")] == ["Open AuralBuds"]
    app.text_input(key="q").input("no such brand").run()
    assert buttons(app, "hit-deal-") == [] and "No matches." in html_text(app)


def test_rerun_button_reruns_analysis():
    app = run_dashboard()
    app.button[[b.label for b in app.button].index("Re-run analysis")].click().run()
    assert not app.exception and len(buttons(app, "hit-deal-")) == 6


# ---------------------------------------------------------------- pure components
def test_components_escape_untrusted_text():
    risk = Risk(code="x", category="security", severity="critical", title="<img src=x onerror=alert(1)>",
                explanation="<script>alert(1)</script>", mitigation="m")
    out = ui.risks_html([risk])
    assert "<script>" not in out and "<img" not in out and "&lt;script&gt;" in out
    assert "<a " not in ui.email_body_html("Visit http://evil.example/login now")


def test_missing_terms_show_not_stated():
    from app.models.schemas import Recommendation, ReconciledTerms

    deal = Deal(deal_id="d", deal_key="d", brand="A very long brand name " * 4, email_ids=["X-1"],
                latest_email_id="X-1", first_received_at="2026-11-01 10:00", last_received_at="2026-11-01 10:00",
                category="needs_more_info", terms=ReconciledTerms(),
                recommendation=Recommendation(action="request_more_information", summary="Ask for details."))
    out = ui.terms_html(deal)
    assert out.count("Not stated") == 6
    assert "A very long brand name" in ui.deal_row_html(deal)
