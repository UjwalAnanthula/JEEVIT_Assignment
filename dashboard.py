# Streamlit dashboard: views + deal list + deal detail + overview, presenting the existing pipeline's results.
# Run: streamlit run dashboard.py  ->  http://localhost:8501   (read-only; drafts are never sent)
from datetime import date
from pathlib import Path

import streamlit as st

from app.core import config
from app.models.schemas import Deal
from app.pipeline import PipelineResult, analyze_emails
from app.services.ai_extractor import providers_from_settings
from app.services.email_parser import load_inputs
from app.services.term_reconciler import describe_term
from app.utils.helpers import format_money
from ui import components as ui

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
PANEL_HEIGHT = 780
VIEWS = [  # (key, label, actions shown, colour); "emails" lists every email instead of deals
    ("attention", "Needs attention", ("accept", "negotiate", "review_manually", "request_more_information"), "amber"),
    ("decline", "Decline", ("decline",), "neutral"),
    ("scams", "Scam warnings", ("flag_for_security_review",), "red"),
    ("ignored", "Ignored", ("ignore",), "neutral"),
    ("emails", "All emails", (), "blue"),
]


# ---------------------------------------------------------------- data (unchanged pipeline)
@st.cache_resource(show_spinner=False)
def run_inbox(use_ai: bool) -> PipelineResult:
    profile, emails = load_inputs(DATA_DIR)
    settings = config.settings if use_ai else config.settings.model_copy(update={"ai_enabled": False})
    return analyze_emails(emails, profile, settings)


@st.cache_data(show_spinner=False)
def email_bodies() -> dict[str, str]:
    return {email.id: email.body for email in load_inputs(DATA_DIR)[1]}


# ---------------------------------------------------------------- state
def set_view(view: str) -> None:
    st.session_state.view = view
    st.session_state.pop("deal", None)
    st.session_state.pop("email", None)
    st.session_state.q = ""


def set_deal(deal_id: str) -> None:
    st.session_state.deal = deal_id


def set_email(email_id: str) -> None:
    st.session_state.email = email_id


def open_deal(deal_id: str, view: str) -> None:
    st.session_state.view, st.session_state.deal, st.session_state.q = view, deal_id, ""


def view_of(deal: Deal) -> str:
    return next(key for key, _, actions, _ in VIEWS if deal.recommendation.action in actions)


def html(fragment: str) -> None:
    """st.html refuses empty strings; optional sections (e.g. no minor risks) simply render nothing."""
    if fragment:
        st.html(fragment)


def clickable(key: str, html: str, label: str, on_click, args: tuple, selected: bool) -> None:
    """Rich HTML row with a real (invisible, full-size) button on top: works with mouse, keyboard and tests."""
    with st.container(key=f"click-{key}{'--sel' if selected else ''}"):
        st.html(html)
        st.button(label, key=f"hit-{key}", on_click=on_click, args=args, width="stretch")


# ---------------------------------------------------------------- page setup
st.set_page_config(page_title="Brand Deal Inbox Assistant", page_icon=":material/inbox:", layout="wide",
                   initial_sidebar_state="expanded")
st.html(ROOT / "ui" / "theme.css")
st.session_state.setdefault("view", "attention")

providers = providers_from_settings(config.settings) if config.settings.ai_enabled else []
with st.sidebar:
    nav_slot = st.container()
    html('<span class="bd-label">Analysis</span>')
    modes = ["Rules only"] + (["AI: " + " → ".join(p.name.title() for p in providers) + " → rules"]
                              if providers else [])
    captions = ["Instant. Plain-code rules, no API calls."] + (
        ["Groq first, Gemini if Groq fails, rules last."] if providers else [])
    mode = st.radio("Extraction mode", modes, index=len(modes) - 1, captions=captions)
    if st.button("Re-run analysis", type="primary", icon=":material/refresh:", width="stretch"):
        run_inbox.clear()
    st.caption("AI answers are cached, so only the first AI run is slow." if providers
               else "Add GROQ_API_KEY or GEMINI_API_KEY to .env to enable AI extraction.")

use_ai = mode != "Rules only"
with st.spinner("Analysing the inbox…"):
    analyses, deals, info = run_inbox(use_ai)
bodies = email_bodies()

by_view = {key: sorted((d for d in deals if d.recommendation.action in actions), key=lambda d: d.first_received_at)
           for key, _, actions, _ in VIEWS if actions}
counts = {**{key: len(items) for key, items in by_view.items()}, "emails": len(analyses)}

with nav_slot:
    html('<span class="bd-label">Views</span>')
    nav_list = st.container(key="navlist")
    for key, label, _, color in VIEWS:
        with nav_list:
            clickable(f"nav-{key}", ui.nav_item_html(label, counts[key], color), f"{label} ({counts[key]})",
                      set_view, (key,), st.session_state.view == key)

# ---------------------------------------------------------------- header
sources = info["extraction_sources"]
fallbacks = {r for r in info["fallback_reasons"].split(", ") if r} - {"security_prefilter", "ai_disabled"}
source_text = " · ".join(f"{name.title()} {count}" for name, count in sources.items())
reference = ui.day(date.fromisoformat(info["reference_date"]))
if use_ai:
    status, color = ("AI extraction", "amber") if fallbacks else ("AI extraction", "green")
else:
    status, color = "Rules only", "neutral"
html(ui.header_html(status, color, f"{source_text} · reference date {reference}"))

list_col, detail_col, side_col = st.columns([1.05, 2.05, 0.95], gap="medium")
view = st.session_state.view
query = st.session_state.get("q", "").strip().lower()
deal_of = {email_id: d for d in deals for email_id in d.email_ids}
sender_of = {d.deal_id: next(a.sender for a in analyses if a.email_id == d.email_ids[0]) for d in deals}


def deal_matches(deal: Deal) -> bool:
    if not query:
        return True
    text = " ".join([deal.brand or "", deal.agency or "", deal.campaign or "", *deal.email_ids,
                     *(a.subject for a in analyses if a.email_id in deal.email_ids)]).lower()
    return query in text


# ---------------------------------------------------------------- list panel
with list_col, st.container(key="panel-list", height=PANEL_HEIGHT, border=False):
    label = next(lbl for key, lbl, _, _ in VIEWS if key == view)
    html(ui.panel_head_html(label, counts[view]))
    st.text_input("Search", key="q", placeholder="Search brand, subject or email ID", label_visibility="collapsed")
    row_list = st.container(key="rowlist")
    if view == "emails":
        rows = [a for a in analyses if not query or query in " ".join(
            [a.email_id, a.sender, a.subject, bodies.get(a.email_id, "")]).lower()]
        selected_email = st.session_state.get("email")
        if rows and selected_email not in {a.email_id for a in rows}:
            selected_email = rows[0].email_id
        for a in rows:
            with row_list:
                clickable(f"email-{a.email_id}", ui.email_row_html(a, bodies.get(a.email_id, "")),
                          f"Open {a.email_id}", set_email, (a.email_id,), a.email_id == selected_email)
    else:
        rows = [d for d in by_view[view] if deal_matches(d)]
        selected_deal = st.session_state.get("deal")
        if rows and selected_deal not in {d.deal_id for d in rows}:
            selected_deal = rows[0].deal_id
        for d in rows:
            with row_list:
                clickable(f"deal-{d.deal_id}", ui.deal_row_html(d, sender_of[d.deal_id]),
                          f"Open {ui.deal_name(d, sender_of[d.deal_id])}",
                          set_deal, (d.deal_id,), d.deal_id == selected_deal)
    if not rows:
        html(ui.empty_html("No matches." if query else "Nothing here."))


# ---------------------------------------------------------------- detail panel
def deal_details(deal: Deal) -> None:
    """Everything behind the main view, preserved from the earlier dashboard."""
    term_rows = []
    for name, label in (("compensation", "Fee"), ("payment_type", "Payment type"), ("product_value", "Product value"),
                        ("deliverables", "Deliverables"), ("platforms", "Platforms"), ("timeline", "Timeline"),
                        ("usage_rights", "Usage rights"), ("exclusivity", "Exclusivity asked"),
                        ("payment_terms", "Payment terms")):
        term = getattr(deal.terms, name)
        if term is not None:
            value = (", ".join(ui.platform_name(p) for p in term.value) if name == "platforms"
                     else describe_term(name, term.value))
            term_rows.append({"Term": label, "Latest value": value,
                              "From email": term.source_email_id, "Status": term.status.replace("_", " ")})
    if term_rows:
        st.table(term_rows, hide_index=True, border="horizontal")
    history = [
        {"Term": label, "Email": event.email_id, "Value": describe_term(name, event.value),
         "Change": event.change_type.replace("_", " ")}
        for name, label in (("compensation", "Fee"), ("deliverables", "Deliverables"), ("timeline", "Timeline"))
        if getattr(deal.terms, name) and len(getattr(deal.terms, name).history) > 1
        for event in getattr(deal.terms, name).history
    ]
    if history:
        st.caption("How the terms changed, oldest first")
        st.table(history, hide_index=True, border="horizontal")
    notes = []
    if deal.rate_check:
        notes.append(f"Rate check: {deal.rate_check.detail}")
    notes += [f"Grouped {ev.email_id} with {ev.linked_email_id}: {ev.reason}" for ev in deal.grouping_evidence]
    notes += [f"Possibly related: {link.email_id} / {link.other_email_id}: {link.reason}" for link in deal.possible_related]
    if deal.missing_information:
        notes.append("Not stated yet: " + ", ".join(m.replace("_", " ") for m in deal.missing_information))
    html(ui.list_html("Why this recommendation", deal.recommendation.reasons))
    html(ui.list_html("Notes", notes))
    html(ui.list_html("Minor risks", [f"{r.title}. {r.explanation}" for r in deal.risks if r.severity == "low"]))


with detail_col, st.container(key="panel-detail", height=PANEL_HEIGHT, border=False):
    if view == "emails":
        if rows:
            a = next(x for x in analyses if x.email_id == selected_email)
            deal = deal_of[a.email_id]
            html(ui.email_head_html(a, deal))
            html(ui.alerts_html(a.security_signals))
            html(ui.email_body_html(bodies.get(a.email_id, "")))
            st.button(f"Open deal: {ui.deal_name(deal, a.sender)}", key="open-deal", icon=":material/arrow_forward:",
                      on_click=open_deal, args=(deal.deal_id, view_of(deal)))
            x = a.extraction
            with st.expander("What was extracted"):
                st.table(
                    [{"Field": k, "Value": v} for k, v in (
                        ("Brand", x.brand), ("Agency", x.agency), ("Contact", a.contact),
                        ("Fee", format_money(x.compensation) if x.compensation else None),
                        ("Payment type", x.payment_type), ("Deliverables", ", ".join(d.label() for d in x.deliverables)),
                        ("Deadline", a.deadline), ("Usage rights", x.usage_rights.raw_text if x.usage_rights else None),
                        ("Extracted by", x.source + (f" ({x.fallback_reason})" if x.fallback_reason else "")),
                    ) if v],
                    hide_index=True, border="horizontal",
                )
                html(ui.list_html("Notes", x.uncertainties))
        else:
            html(ui.empty_html("Select an email to read it."))
    elif rows:
        deal = next(d for d in rows if d.deal_id == selected_deal)
        html(ui.deal_head_html(deal, sender_of[deal.deal_id]))
        if deal.category not in ("scam_or_phishing", "spam"):
            html(ui.terms_html(deal))
        if deal.category != "spam":
            html(ui.risks_html(deal.risks))
        html(ui.actions_html(deal.recommendation.next_steps))
        if deal.draft_reply:
            with st.container(key=f"reply-{deal.deal_id}"):
                html(ui.reply_head_html(deal.draft_reply))
                st.code(deal.draft_reply.body, language=None, wrap_lines=True)
                html(ui.reply_foot_html(deal.draft_reply))
        with st.expander("Deal details"):
            deal_details(deal)
    else:
        html(ui.empty_html("Nothing to show in this view."))

# ---------------------------------------------------------------- overview column
with side_col:
    with st.container(key="panel-side-overview", border=False):
        html(ui.stats_html(len(analyses), len(deals), counts["attention"], counts["scams"]))
    with st.container(key="panel-side-categories", border=False):
        rows_by_category = [(ui.CATEGORY[c][0], sum(a.category == c for a in analyses), ui.CATEGORY[c][1])
                            for c in ui.CATEGORY]
        html(ui.bars_html("Emails by category", rows_by_category, len(analyses)))
    with st.container(key="panel-side-extraction", border=False):
        prefiltered = sum(1 for a in analyses if a.extraction and a.extraction.fallback_reason == "security_prefilter")
        html(ui.kv_html(
            "Extraction",
            [("Mode", "AI" if use_ai else "Rules only")]
            + [(name.title(), f"{count} emails") for name, count in sources.items()]
            + [("Reference date", reference)],
            f"{prefiltered} suspected scams were never sent to an AI model." if use_ai and prefiltered else
            "Rates, rules, deadlines and security are always checked by plain code.",
        ))
