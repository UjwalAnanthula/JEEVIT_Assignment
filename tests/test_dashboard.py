# Renders the Streamlit dashboard headlessly (rules mode, no network) and clicks through every view, deal and email.
# Uses Streamlit's AppTest, so no browser or server is needed. UI-only: backend behaviour is tested elsewhere.
from pathlib import Path

from streamlit.testing.v1 import AppTest

from app.models.schemas import Deal, Recommendation, ReconciledTerms, Risk
from ui import components as ui

DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard.py"
VIEWS = {"attention": ("Needs attention", 6), "decline": ("Decline", 3), "scams": ("Scam warnings", 3),
         "ignored": ("Ignored", 1), "emails": ("All emails", 15)}


def run_dashboard() -> AppTest:
    return AppTest.from_file(str(DASHBOARD), default_timeout=60).run()


def keys(app: AppTest, prefix: str) -> list[str]:
    return [b.key for b in app.button if (b.key or "").startswith(prefix)]


def page_html(app: AppTest) -> str:
    return "\n".join(str(el.value) for el in app.get("html"))


def test_overview_cards_views_bar_and_list():
    app = run_dashboard()
    assert not app.exception
    labels = [b.label for b in app.button if (b.key or "").startswith("hit-nav-")]
    assert labels == ["All emails (15)", "Needs attention (6)", "Decline (3)", "Scam warnings (3)", "Ignored (1)"]
    assert "Needs attention" in page_html(app) and len(keys(app, "hit-deal-")) == 6  # opens on Needs attention
    page = page_html(app)
    assert "Inbox overview" in page and "Emails by category" in page and "Extraction" in page
    assert ">15</div>" in page and ">13</div>" in page
    assert len(keys(app, "hit-deal-")) == 6 and not keys(app, "back") and len(app.code) == 0  # list, no detail yet
    assert app.radio[0].options == ["Rules only"]  # no API keys in tests


def test_every_row_opens_a_detail_page_and_back_returns_to_the_list():
    app = run_dashboard()
    for view, (_, count) in VIEWS.items():
        app.button(key=f"hit-nav-{view}").click().run()
        rows = keys(app, "hit-email-" if view == "emails" else "hit-deal-")
        assert len(rows) == count, view
        for row in rows:
            app.button(key=row).click().run()
            assert not app.exception and keys(app, "back") == ["back"], row
            app.button(key="back").click().run()
            assert not app.exception and len(keys(app, "hit-email-" if view == "emails" else "hit-deal-")) == count


def test_dew_lab_detail_shows_terms_risks_actions_and_draft():
    app = run_dashboard()
    app.button(key="hit-deal-deal-dew-lab-e-01").click().run()
    page = page_html(app)
    assert "Dew Lab" in page and "INR 1,00,000" in page and "below your rate" in page
    assert "Agency authority not verified" in page and "Suggested actions" in page and "Draft · not sent" in page
    assert len(app.code) == 1 and "INR 1,10,000" in app.code[0].value
    assert app.button(key="back").label == "Back to Needs attention"


def test_email_reader_escapes_text_and_links_to_its_deal():
    app = run_dashboard()
    app.button(key="hit-nav-emails").click().run()
    app.button(key="hit-email-E-14").click().run()
    page = page_html(app)
    assert "http://youtube-copyright-centre.net/verify" in page and "<a " not in page  # never a clickable link
    app.button(key="open-deal").click().run()
    assert not app.exception and "Report scam" in page_html(app)
    assert app.button(key="back").label == "Back to Scam warnings"


def test_search_filters_and_survives_back_navigation():
    app = run_dashboard()
    app.text_input(key="q").input("auralbuds").run()
    assert keys(app, "hit-deal-") == ["hit-deal-deal-auralbuds-e-10"]
    app.button(key="hit-deal-deal-auralbuds-e-10").click().run()
    app.button(key="back").click().run()
    assert keys(app, "hit-deal-") == ["hit-deal-deal-auralbuds-e-10"] and app.text_input(key="q").value == "auralbuds"
    app.text_input(key="q").input("no such brand").run()
    assert keys(app, "hit-deal-") == [] and "No matches." in page_html(app)


def test_detail_page_hides_overview_and_views_until_back():
    app = run_dashboard()
    app.button(key="hit-deal-deal-dew-lab-e-01").click().run()
    assert not keys(app, "hit-nav-") and "Inbox overview" not in page_html(app)  # focused detail page
    app.button(key="back").click().run()
    assert len(keys(app, "hit-nav-")) == 5 and "Inbox overview" in page_html(app)
    app.button(key="hit-nav-decline").click().run()
    assert not keys(app, "back") and len(keys(app, "hit-deal-")) == 3


def test_rerun_button_reruns_analysis():
    app = run_dashboard()
    app.button[[b.label for b in app.button].index("Re-run analysis")].click().run()
    assert not app.exception and len(keys(app, "hit-deal-")) == 6


# ---------------------------------------------------------------- pure components
def test_components_escape_untrusted_text():
    risk = Risk(code="x", category="security", severity="critical", title="<img src=x onerror=alert(1)>",
                explanation="<script>alert(1)</script>", mitigation="m")
    out = ui.risks_html([risk])
    assert "<script>" not in out and "<img" not in out and "&lt;script&gt;" in out
    assert "<a " not in ui.email_body_html("Visit http://evil.example/login now")


def test_missing_terms_show_not_stated_and_long_names_render():
    deal = Deal(deal_id="d", deal_key="d", brand="A very long brand name " * 4, email_ids=["X-1"],
                latest_email_id="X-1", first_received_at="2026-11-01 10:00", last_received_at="2026-11-01 10:00",
                category="needs_more_info", terms=ReconciledTerms(),
                recommendation=Recommendation(action="request_more_information", summary="Ask for details."))
    assert ui.terms_html(deal).count("Not stated") == 6
    row = ui.deal_row_html(deal)
    assert "A very long brand name" in row and "Fee not stated" in row
