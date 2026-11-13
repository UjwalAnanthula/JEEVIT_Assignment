# Streamlit dashboard: overview cards, views bar, full-width deal list, and a detail page with a Back button.
# Run: python -m streamlit run dashboard.py  ->  http://localhost:8501   (read-only; drafts are never sent)
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
VIEWS = [  # (key, label, actions shown, colour) in tab order; "emails" lists every email instead of deals
    ("emails", "All emails", (), "blue"),
    ("attention", "Needs attention", ("accept", "negotiate", "review_manually", "request_more_information"), "amber"),
    ("decline", "Decline", ("decline",), "neutral"),
    ("scams", "Scam warnings", ("flag_for_security_review",), "red"),
    ("ignored", "Ignored", ("ignore",), "neutral"),
]
DEFAULT_VIEW = "attention"  # the page always opens on what needs action, even though All emails is listed first
VIEW_LABEL = {key: label for key, label, _, _ in VIEWS}


# ---------------------------------------------------------------- data (unchanged pipeline)
@st.cache_resource(show_spinner=False)
def run_inbox(use_ai: bool) -> PipelineResult:
    profile, emails = load_inputs(DATA_DIR)
    settings = config.settings if use_ai else config.settings.model_copy(update={"ai_enabled": False})
    return analyze_emails(emails, profile, settings)


@st.cache_data(show_spinner=False)
def email_bodies() -> dict[str, str]:
    return {email.id: email.body for email in load_inputs(DATA_DIR)[1]}


# ---------------------------------------------------------------- navigation state
def set_view(view: str) -> None:
    st.session_state.view, st.session_state.query = view, ""
    for key in ("deal", "email", "q"):
        st.session_state.pop(key, None)


def open_item(kind: str, item_id: str) -> None:
    st.session_state[kind] = item_id


def go_back() -> None:
    for key in ("deal", "email", "scrolled_for"):
        st.session_state.pop(key, None)


def open_deal(deal_id: str, view: str) -> None:
    set_view(view)
    st.session_state.deal = deal_id


def keep_query() -> None:
    st.session_state.query = st.session_state.q  # survives the trip to a detail page and back


def view_of(deal: Deal) -> str:
    return next(key for key, _, actions, _ in VIEWS if deal.recommendation.action in actions)


def html(fragment: str) -> None:
    """st.html refuses empty strings; optional sections (e.g. no minor risks) simply render nothing."""
    if fragment:
        st.html(fragment)


def clickable(key: str, fragment: str, label: str, on_click, args: tuple, selected: bool = False) -> None:
    """Rich HTML tab/row with a real (invisible, full-size) button on top: works with mouse, keyboard and tests."""
    with st.container(key=f"click-{key}{'--sel' if selected else ''}"):
        st.html(fragment)
        st.button(label, key=f"hit-{key}", on_click=on_click, args=args, width="stretch")


# ---------------------------------------------------------------- page and header
st.set_page_config(page_title="Brand Deal Inbox Assistant", page_icon=":material/inbox:", layout="wide",
                   initial_sidebar_state="collapsed")
st.html(ROOT / "ui" / "theme.css")
st.session_state.setdefault("view", DEFAULT_VIEW)
st.session_state.setdefault("query", "")

providers = providers_from_settings(config.settings) if config.settings.ai_enabled else []
brand_col, status_col = st.columns([3, 2], vertical_alignment="center")
with brand_col:
    st.html(ui.brand_html())
with status_col, st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center"):
    status_slot = st.empty()
    with st.popover("Analysis", icon=":material/tune:"):
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

sources = info["extraction_sources"]
fallbacks = {r for r in info["fallback_reasons"].split(", ") if r} - {"security_prefilter", "ai_disabled"}
reference = ui.day(date.fromisoformat(info["reference_date"]))
source_text = " · ".join(f"{name.title()} {count}" for name, count in sources.items())
if use_ai:
    status_slot.html(ui.status_html("AI extraction", "amber" if fallbacks else "green", f"{source_text} · {reference}"),
                     width="content")
else:
    status_slot.html(ui.status_html("Rules only", "neutral", f"{source_text} · {reference}"), width="content")

by_view = {key: sorted((d for d in deals if d.recommendation.action in actions), key=lambda d: d.first_received_at)
           for key, _, actions, _ in VIEWS if actions}
counts = {**{key: len(items) for key, items in by_view.items()}, "emails": len(analyses)}
deal_of = {email_id: d for d in deals for email_id in d.email_ids}
sender_of = {d.deal_id: next(a.sender for a in analyses if a.email_id == d.email_ids[0]) for d in deals}

# Which item (if any) is open? Anything no longer in the current view returns to the list.
view = st.session_state.view
open_deal_id = st.session_state.get("deal") if view != "emails" else None
open_email_id = st.session_state.get("email") if view == "emails" else None
if open_deal_id and open_deal_id not in {d.deal_id for d in by_view.get(view, [])}:
    open_deal_id = None
if open_email_id and open_email_id not in {a.email_id for a in analyses}:
    open_email_id = None
in_detail = bool(open_deal_id or open_email_id)

# Overview cards and the views bar belong to the list page; a detail page shows only the item and Back.
if not in_detail:
    top = st.columns(3, gap="medium")
    with top[0], st.container(key="panel-top-overview"):
        st.html(ui.stats_html(len(analyses), len(deals), counts["attention"], counts["scams"]))
    with top[1], st.container(key="panel-top-categories"):
        rows_by_category = [(ui.CATEGORY[c][0], sum(a.category == c for a in analyses), ui.CATEGORY[c][1])
                            for c in ui.CATEGORY]
        st.html(ui.bars_html("Emails by category", rows_by_category, len(analyses)))
    with top[2], st.container(key="panel-top-extraction"):
        prefiltered = sum(1 for a in analyses
                          if a.extraction and a.extraction.fallback_reason == "security_prefilter")
        st.html(ui.kv_html(
            "Extraction",
            [("Mode", "AI" if use_ai else "Rules only")]
            + [(name.title(), f"{count} emails") for name, count in sources.items()]
            + [("Reference date", reference)],
            f"{prefiltered} suspected scams were never sent to an AI model." if use_ai and prefiltered else
            "Rates, rules, deadlines and security are always checked by plain code.",
        ))
    for col, (key, label, _, color) in zip(st.columns(len(VIEWS), gap="small"), VIEWS):
        with col:
            clickable(f"nav-{key}", ui.view_tab_html(label, counts[key], color), f"{label} ({counts[key]})",
                      set_view, (key,), view == key)


# ---------------------------------------------------------------- detail pages
def deal_details(deal: Deal) -> None:
    """Everything behind the main view: term sources, history, grouping evidence and minor risks."""
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


def deal_page(deal: Deal) -> None:
    html(ui.deal_head_html(deal, sender_of[deal.deal_id]))
    if deal.category not in ("scam_or_phishing", "spam"):
        html(ui.terms_html(deal))
    left, right = st.columns([1.35, 1], gap="large")
    with left:
        if deal.category != "spam":
            html(ui.risks_html(deal.risks))
    with right:
        html(ui.actions_html(deal.recommendation.next_steps))
    if deal.draft_reply:
        with st.container(key=f"reply-{deal.deal_id}"):
            html(ui.reply_head_html(deal.draft_reply))
            st.code(deal.draft_reply.body, language=None, wrap_lines=True)
            html(ui.reply_foot_html(deal.draft_reply))
    with st.expander("Deal details"):
        deal_details(deal)


def email_page(email_id: str) -> None:
    a = next(x for x in analyses if x.email_id == email_id)
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


# ---------------------------------------------------------------- list or detail
if in_detail:
    opened = open_email_id or open_deal_id
    if st.session_state.get("scrolled_for") != opened:
        # Streamlit keeps the list's scroll position; start a newly opened detail page at the top (with Back).
        st.session_state.scrolled_for = opened
        st.html(f'<span data-open="{ui.e(opened)}"></span><script>window.scrollTo(0, 0);'
                "document.querySelectorAll('*').forEach(el => { if (el.scrollTop > 0) el.scrollTop = 0; });</script>",
                unsafe_allow_javascript=True)
    st.button(f"Back to {VIEW_LABEL[view]}", key="back", icon=":material/arrow_back:", type="tertiary",
              on_click=go_back)
    with st.container(key="panel-detail"):
        if open_email_id:
            email_page(open_email_id)
        else:
            deal_page(next(d for d in by_view[view] if d.deal_id == open_deal_id))
else:
    with st.container(key="panel-list"):
        head_col, search_col = st.columns([3, 2], vertical_alignment="center")
        with head_col:
            st.html(ui.panel_head_html(VIEW_LABEL[view], counts[view], "email" if view == "emails" else "deal"))
        with search_col:
            st.text_input("Search", key="q", value=st.session_state.query, on_change=keep_query,
                          placeholder="Search brand, subject or email ID", label_visibility="collapsed")
        query = st.session_state.query.strip().lower()
        with st.container(key="rowlist"):
            if view == "emails":
                rows = [a for a in analyses if not query or query in " ".join(
                    [a.email_id, a.sender, a.subject, bodies.get(a.email_id, "")]).lower()]
                for a in rows:
                    clickable(f"email-{a.email_id}", ui.email_row_html(a, bodies.get(a.email_id, "")),
                              f"Open {a.email_id}", open_item, ("email", a.email_id))
            else:
                def matches(d: Deal) -> bool:
                    text = " ".join([ui.deal_name(d, sender_of[d.deal_id]), d.agency or "", d.campaign or "",
                                     *d.email_ids, *(a.subject for a in analyses if a.email_id in d.email_ids)])
                    return not query or query in text.lower()

                rows = [d for d in by_view[view] if matches(d)]
                for d in rows:
                    clickable(f"deal-{d.deal_id}", ui.deal_row_html(d, sender_of[d.deal_id]),
                              f"Open {ui.deal_name(d, sender_of[d.deal_id])}", open_item, ("deal", d.deal_id))
            if not rows:
                html(ui.empty_html("No matches." if query else "Nothing here."))
