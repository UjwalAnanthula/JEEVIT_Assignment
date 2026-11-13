# Renders the Streamlit dashboard headlessly (rules mode, no network) and checks each section has content.
# Uses Streamlit's AppTest, so no browser or server is needed.
from pathlib import Path

from streamlit.testing.v1 import AppTest

DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard.py"


def run_dashboard() -> AppTest:
    return AppTest.from_file(str(DASHBOARD), default_timeout=60).run()


def test_dashboard_renders_deal_cards_and_inbox():
    app = run_dashboard()
    assert not app.exception
    assert app.title[0].value == "Brand Deal Inbox Assistant"
    assert {m.label: m.value for m in app.metric} == {
        "Emails": "15", "Deals": "13", "Need attention": "6", "Scam warnings": "3"}
    assert [t.label for t in app.tabs] == [
        "Needs attention (6)", "Decline (3)", "Scam warnings (3)", "Ignored (1)", "All emails (15)"]
    assert len(app.code) == 9  # one copyable reply per deal that needs one; none for scams or spam
    assert any("INR 1,10,000" in block.value for block in app.code)
    assert any("Dew Lab \\(via Brightside Agency\\)" in m.value for m in app.markdown)


def test_dashboard_shows_email_text_as_plain_text():
    app = run_dashboard()
    app.selectbox[0].set_value("E-14").run()
    assert not app.exception
    assert any("http://youtube-copyright-centre.net/verify" in t.value for t in app.text)  # not a clickable link
    assert any("youtube" in e.value.lower() for e in app.error)


def test_dashboard_offers_rules_only_without_keys():
    app = run_dashboard()
    assert app.radio[0].options == ["Rules only"]
