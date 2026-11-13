# Streamlit dashboard: one card per deal (next step, terms, risks, reply) plus the classified inbox.
# Run: streamlit run dashboard.py  ->  http://localhost:8501   (read-only view; nothing is ever sent)
import re
from datetime import date
from pathlib import Path

import streamlit as st

from app.core import config
from app.models.schemas import Deal, ReconciledTerms, UsageRights
from app.pipeline import PipelineResult, analyze_emails
from app.services.ai_extractor import providers_from_settings
from app.services.email_parser import load_inputs
from app.services.reply_generator import prose_deliverables
from app.services.term_reconciler import describe_term
from app.utils.helpers import format_money

DATA_DIR = Path(__file__).resolve().parent / "data"

CATEGORY = {
    "deal_offer": ("Deal offer", "green"),
    "needs_more_info": ("Needs more info", "blue"),
    "not_a_fit": ("Not a fit", "orange"),
    "scam_or_phishing": ("Scam / phishing", "red"),
    "spam": ("Spam", "gray"),
}
ACTION = {
    "accept": ("Accept", "green"),
    "negotiate": ("Negotiate", "blue"),
    "request_more_information": ("Ask for details", "violet"),
    "review_manually": ("Review manually", "yellow"),
    "decline": ("Decline", "orange"),
    "flag_for_security_review": ("Report as scam", "red"),
    "ignore": ("Ignore", "gray"),
}
SEVERITY = {"critical": ("Critical", "red"), "high": ("High", "orange"), "medium": ("Medium", "yellow"),
            "low": ("Low", "gray")}
GROUPS = [
    ("Needs attention", ("accept", "negotiate", "review_manually", "request_more_information")),
    ("Decline", ("decline",)),
    ("Scam warnings", ("flag_for_security_review",)),
    ("Ignored", ("ignore",)),
]
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$:])")


def esc(text: object) -> str:
    """Email-derived text is untrusted: escape it so it can never render as links, images or LaTeX."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", str(text))


def tag(mapping: dict, key: str) -> str:
    label, color = mapping[key]
    return f":{color}-badge[{label}]"


@st.cache_resource(show_spinner=False)
def run_inbox(use_ai: bool) -> PipelineResult:
    profile, emails = load_inputs(DATA_DIR)
    settings = config.settings if use_ai else config.settings.model_copy(update={"ai_enabled": False})
    return analyze_emails(emails, profile, settings)


def deal_title(deal: Deal) -> str:
    title = deal.brand or "Unknown sender"
    return f"{title} (via {deal.agency})" if deal.agency else title


def key_terms(terms: ReconciledTerms) -> str:
    """One readable line: Fee · Content · When · Usage."""
    parts = []
    comp, value = terms.current("compensation"), terms.current("product_value")
    if comp:
        parts.append(("Fee", format_money(comp)))
    elif terms.current("payment_type") == "gifting":
        parts.append(("Fee", "none, product only" + (f" (worth {format_money(value)})" if value else "")))
    if terms.current("deliverables"):
        parts.append(("Content", prose_deliverables(terms.current("deliverables"))))
    timeline = terms.current("timeline")
    if timeline:
        if timeline.start_date and timeline.end_date:
            parts.append(("When", f"{day(timeline.start_date, year=False)} to {day(timeline.end_date)}"))
        elif timeline.end_date:
            parts.append(("Due", day(timeline.end_date)))
        else:
            parts.append(("When", timeline.relative or timeline.raw_text))
    usage = terms.current("usage_rights")
    if usage:
        parts.append(("Usage", usage_summary(usage)))
    return "  ·  ".join(f"**{label}** {esc(text)}" for label, text in parts)


def day(value: date, year: bool = True) -> str:
    """'1 Nov 2026' rather than '01 Nov 2026'."""
    return f"{value.day} {value:%b}" + (f" {value.year}" if year else "")


def usage_summary(usage: UsageRights) -> str:
    """Short form of the parsed usage terms, e.g. '3 months, paid ads' or 'perpetual, all media, whitelisting'."""
    parts = []
    if usage.perpetual:
        parts.append("perpetual")
    elif usage.duration_months is not None:
        parts.append(f"{usage.duration_months:g} months")
    parts += [label for flag, label in ((usage.all_media, "all media"), (usage.paid_ads, "paid ads"),
                                        (usage.whitelisting, "whitelisting")) if flag]
    return ", ".join(parts) or usage.raw_text


def details(deal: Deal) -> None:
    """Everything behind the card: term sources, history, grouping evidence and minor risks."""
    rows = []
    for name, label in (("compensation", "Fee"), ("payment_type", "Payment type"), ("product_value", "Product value"),
                        ("deliverables", "Content"), ("timeline", "Timeline"), ("usage_rights", "Usage rights"),
                        ("exclusivity", "Exclusivity asked"), ("payment_terms", "Payment terms")):
        term = getattr(deal.terms, name)
        if term is not None:
            rows.append({"Term": label, "Latest value": describe_term(name, term.value),
                         "From email": term.source_email_id, "Status": term.status.replace("_", " ")})
    if rows:
        st.dataframe(rows, hide_index=True)
    history = [
        {"Term": label, "Email": e.email_id, "Value": describe_term(name, e.value),
         "Change": e.change_type.replace("_", " ")}
        for name, label in (("compensation", "Fee"), ("deliverables", "Content"), ("timeline", "Timeline"))
        if getattr(deal.terms, name) and len(getattr(deal.terms, name).history) > 1
        for e in getattr(deal.terms, name).history
    ]
    if history:
        st.caption("How the terms changed, oldest first")
        st.dataframe(history, hide_index=True)
    if deal.rate_check:
        st.caption(f"Rate check: {esc(deal.rate_check.detail)}")
    for ev in deal.grouping_evidence:
        st.caption(f"Grouped {esc(ev.email_id)} with {esc(ev.linked_email_id)}: {esc(ev.reason)}")
    if deal.missing_information:
        st.caption("Not stated yet: " + ", ".join(m.replace("_", " ") for m in deal.missing_information))
    for risk in (r for r in deal.risks if r.severity == "low"):
        st.caption(f"Minor: {esc(risk.title)}. {esc(risk.explanation)}")


def deal_card(deal: Deal) -> None:
    rec = deal.recommendation
    with st.container(border=True):
        st.markdown(f"**{esc(deal_title(deal))}**  ·  {esc(', '.join(deal.email_ids))}")
        st.markdown(f"{tag(ACTION, rec.action)} {tag(CATEGORY, deal.category)}  {esc(rec.summary)}")
        if deal.category not in ("scam_or_phishing", "spam"):
            line = key_terms(deal.terms)
            if line:
                st.markdown(line)
        for risk in [r for r in deal.risks if r.severity != "low"][:3]:
            st.markdown(f"{tag(SEVERITY, risk.severity)} **{esc(risk.title)}.** {esc(risk.explanation)}")
        steps = rec.next_steps[:3]
        if steps:
            st.markdown("\n".join(f"- {esc(step)}" for step in steps))
        if deal.draft_reply:
            with st.expander("Suggested reply"):
                st.caption(f"To {esc(deal.draft_reply.to)}  ·  {esc(deal.draft_reply.subject)}")
                st.code(deal.draft_reply.body, language=None, wrap_lines=True)
        if deal.category != "spam":
            with st.expander("Details"):
                details(deal)


# ---------------------------------------------------------------- page
st.set_page_config(page_title="Brand Deal Inbox Assistant", layout="wide")

providers = providers_from_settings(config.settings) if config.settings.ai_enabled else []
with st.sidebar:
    options = ["Rules only"] + (["AI: " + " -> ".join(p.name.title() for p in providers) + " -> rules"]
                                if providers else [])
    mode = st.radio("Extraction", options, index=len(options) - 1)
    st.caption("AI answers are cached, so only the first AI run is slow." if providers
               else "Add GROQ_API_KEY or GEMINI_API_KEY to .env to enable AI.")
    if st.button("Re-run analysis"):
        run_inbox.clear()

with st.spinner("Analysing the inbox..."):
    analyses, deals, info = run_inbox(mode != "Rules only")

st.title("Brand Deal Inbox Assistant")
sources = ", ".join(f"{name} {n}" for name, n in info["extraction_sources"].items())
st.caption(f"Reference date {info['reference_date']}  ·  extracted by {sources}  ·  "
           "drafts are for you to review; nothing is ever sent")

grouped = {name: sorted((d for d in deals if d.recommendation.action in actions), key=lambda d: d.first_received_at)
           for name, actions in GROUPS}
cols = st.columns(4)
cols[0].metric("Emails", len(analyses))
cols[1].metric("Deals", len(deals))
cols[2].metric("Need attention", len(grouped["Needs attention"]))
cols[3].metric("Scam warnings", len(grouped["Scam warnings"]))

tabs = st.tabs([f"{name} ({len(grouped[name])})" for name, _ in GROUPS] + [f"All emails ({len(analyses)})"])
for tab, (name, _) in zip(tabs, GROUPS):
    with tab:
        if not grouped[name]:
            st.caption("Nothing here.")
        for deal in grouped[name]:
            deal_card(deal)

with tabs[-1]:
    deal_of = {i: d for d in deals for i in d.email_ids}
    st.dataframe(
        [{"Email": a.email_id, "From": a.sender, "Subject": a.subject, "Category": CATEGORY[a.category][0],
          "Deal": deal_title(deal_of[a.email_id])} for a in analyses],
        hide_index=True,
    )
    by_id = {a.email_id: a for a in analyses}
    chosen = st.selectbox("Read an email", list(by_id), format_func=lambda i: f"{i}  ·  {by_id[i].subject}")
    a = by_id[chosen]
    email = next(e for e in load_inputs(DATA_DIR)[1] if e.id == chosen)
    with st.container(border=True):
        st.markdown(f"{tag(CATEGORY, a.category)} {tag(ACTION, a.recommendation)}  "
                    f"From **{esc(a.sender)}**  ·  {esc(a.received_at)}")
        st.markdown(f"**{esc(a.subject)}**")
        st.text(email.body)  # plain text on purpose: links in emails must never become clickable
        for signal in (s for s in a.security_signals if s.strength == "strong"):
            st.error(f"{esc(signal.explanation)}  \n{esc(signal.evidence)}")
        x = a.extraction
        with st.expander("What was extracted"):
            st.dataframe(
                [{"Field": k, "Value": v} for k, v in (
                    ("Brand", x.brand), ("Agency", x.agency), ("Contact", a.contact),
                    ("Fee", format_money(x.compensation) if x.compensation else None),
                    ("Payment type", x.payment_type),
                    ("Content", ", ".join(d.label() for d in x.deliverables) or None),
                    ("Deadline", a.deadline), ("Usage rights", x.usage_rights.raw_text if x.usage_rights else None),
                ) if v],
                hide_index=True,
            )
            st.caption(f"Extracted by {x.source}" + (f" ({esc(x.fallback_reason)})" if x.fallback_reason else ""))
            for note in x.uncertainties:
                st.caption(f"Note: {esc(note)}")
