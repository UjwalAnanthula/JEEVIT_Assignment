# Pure HTML builders for the dashboard. Presentation only: every value comes from existing pipeline objects,
# and every email-derived string is HTML-escaped (Streamlit's st.html also sanitises the result).
import re
from datetime import date, datetime
from html import escape

from app.models.schemas import Analysis, Deal, DraftReply, Risk, SecuritySignal, Timeline, UsageRights
from app.services.reply_generator import prose_deliverables
from app.utils.helpers import format_amount, format_money

CATEGORY = {
    "deal_offer": ("Deal offer", "green"),
    "needs_more_info": ("Needs info", "blue"),
    "not_a_fit": ("Not a fit", "neutral"),
    "scam_or_phishing": ("Scam / phishing", "red"),
    "spam": ("Spam", "neutral"),
}
ACTION = {
    "accept": ("Accept", "green"),
    "negotiate": ("Negotiate", "amber"),
    "request_more_information": ("Ask for details", "blue"),
    "review_manually": ("Review", "amber"),
    "decline": ("Decline", "neutral"),
    "flag_for_security_review": ("Report scam", "red"),
    "ignore": ("Ignore", "neutral"),
}
SEVERITY = {"critical": ("Critical", "red"), "high": ("High", "orange"), "medium": ("Medium", "amber"),
            "low": ("Low", "neutral")}
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def e(value: object) -> str:
    return escape(str(value), quote=True)


_HIGHLIGHT_RE = re.compile(
    r"(?:INR|USD|EUR|GBP|Rs\.?|\u20b9)\s?\d[\d,]*(?:\.\d+)?"  # amounts
    r"|\d+(?:\.\d+)?%"  # percentages
    r"|\b\d{1,2}\s(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*(?:\s\d{4})?\b"  # dates
)


def emphasize(text: str) -> str:
    """Escape text, then bold the facts a reader scans for: amounts, percentages and dates."""
    out, last = [], 0
    for match in _HIGHLIGHT_RE.finditer(text):
        out.append(e(text[last:match.start()]))
        out.append(f'<strong class="bd-hl">{e(match.group())}</strong>')
        last = match.end()
    out.append(e(text[last:]))
    return "".join(out)


def counted(count: int, noun: str) -> str:
    """'1 risk', '3 actions': a count that cannot be mistaken for a step or section number."""
    return f"{count} {noun}{'' if count == 1 else 's'}"


def section_html(title: str, count: int | None = None, noun: str = "item") -> str:
    count_html = f'<span class="bd-tab-count">{e(counted(count, noun))}</span>' if count is not None else ""
    return f'<div class="bd-section-title"><span>{e(title)}</span>{count_html}</div>'


def chip(text: str, color: str) -> str:
    return f'<span class="bd-chip {color}">{e(text)}</span>'


def wrap(inner: str) -> str:
    return f'<div class="bd">{inner}</div>'


# ---------------------------------------------------------------- formatting helpers
def short_time(received_at: str) -> str:
    try:
        moment = datetime.fromisoformat(received_at.strip())
    except ValueError:
        return received_at
    return f"{moment.day} {moment:%b}, {moment:%H:%M}"


def day(value: date, year: bool = True) -> str:
    return f"{value.day} {value:%b}" + (f" {value.year}" if year else "")


def timeline_text(timeline: Timeline | None) -> str | None:
    if timeline is None:
        return None
    if timeline.start_date and timeline.end_date:
        same_month = (timeline.start_date.year, timeline.start_date.month) == (timeline.end_date.year,
                                                                              timeline.end_date.month)
        start = str(timeline.start_date.day) if same_month else day(timeline.start_date, year=False)
        return f"{start}–{day(timeline.end_date)}"
    if timeline.end_date:
        return f"Due {day(timeline.end_date)}"
    return timeline.relative or timeline.raw_text


def usage_text(usage: UsageRights | None) -> str | None:
    if usage is None:
        return None
    parts = []
    if usage.perpetual:
        parts.append("Perpetual")
    elif usage.duration_months is not None:
        parts.append(f"{usage.duration_months:g} months")
    parts += [label for flag, label in ((usage.all_media, "all media"), (usage.paid_ads, "paid ads"),
                                        (usage.whitelisting, "whitelisting")) if flag]
    return ", ".join(parts) or usage.raw_text


PLATFORM_NAMES = {"instagram": "Instagram", "youtube": "YouTube"}


def platform_name(platform: str) -> str:
    return PLATFORM_NAMES.get(platform, platform.title())


def deal_name(deal: Deal, sender: str | None = None) -> str:
    """Brand if known; otherwise the sender's domain (never a bare 'Unknown')."""
    if deal.brand:
        return deal.brand
    return sender.rpartition("@")[2] if sender else "Unknown sender"


def visible_risks(risks: list[Risk]) -> list[Risk]:
    return sorted((r for r in risks if r.severity != "low"), key=lambda r: SEVERITY_RANK[r.severity])


# ---------------------------------------------------------------- chrome
def brand_html() -> str:
    return wrap(
        '<div class="bd-brand"><div class="bd-mark">B</div><div>'
        '<div class="bd-title">Brand Deal Inbox Assistant</div>'
        '<div class="bd-subtitle">Classify brand emails · group them into deals · check terms against your rules'
        ' · draft replies for review</div></div></div>'
    )


def status_html(status: str, status_color: str, detail: str) -> str:
    return wrap(f'<div class="bd-status"><span class="bd-dot {status_color}"></span><b>{e(status)}</b>'
                f'<span>{e(detail)}</span></div>')


def view_tab_html(label: str, count: int, color: str) -> str:
    return wrap(f'<div class="bd-tab"><span class="bd-dot {color}"></span><span class="bd-tab-label">{e(label)}</span>'
                f'<span class="bd-tab-count">{count}</span></div>')


def panel_head_html(title: str, count: int | None = None, noun: str = "item") -> str:
    count_html = f'<span class="bd-tab-count">{e(counted(count, noun))}</span>' if count is not None else ""
    return wrap(f'<div class="bd-panel-head"><span class="bd-panel-title">{e(title)}</span>{count_html}</div>')


def empty_html(text: str) -> str:
    return wrap(f'<p class="bd-empty-note">{e(text)}</p>')


# ---------------------------------------------------------------- list rows (full width, one per deal or email)
_CHEVRON = '<span class="bd-chev" aria-hidden="true">›</span>'


def deal_row_html(deal: Deal, sender: str | None = None) -> str:
    label, color = ACTION[deal.recommendation.action]
    terms = deal.terms
    money, content = "—", ""
    if deal.category not in ("scam_or_phishing", "spam"):
        comp = terms.current("compensation")
        if comp:
            money = format_money(comp)
        elif terms.current("payment_type") == "gifting":
            money = "Product only"
        else:
            money = "Fee not stated"
        if terms.current("deliverables"):
            content = prose_deliverables(terms.current("deliverables"))
    risks = visible_risks(deal.risks)
    risk_chip = ""
    if risks:
        risk_chip = chip(f"{len(risks)} risk{'s' if len(risks) != 1 else ''}", SEVERITY[risks[0].severity][1])
    emails = f"{len(deal.email_ids)} emails" if len(deal.email_ids) > 1 else deal.email_ids[0]
    who = f"via {deal.agency}" if deal.agency else ", ".join(deal.email_ids)
    return wrap(
        '<div class="bd-hrow">'
        f'<div class="bd-hcell"><div class="bd-row-top"><span class="bd-dot {color}"></span>'
        f'<span class="bd-row-title">{e(deal_name(deal, sender))}</span></div>'
        f'<div class="bd-row-sub">{e(who)}</div></div>'
        f'<div class="bd-hcell bd-hsummary">{e(deal.recommendation.summary)}</div>'
        f'<div class="bd-hcell"><div class="bd-hmoney">{e(money)}</div>'
        f'<div class="bd-row-sub">{e(content)}</div></div>'
        f'<div class="bd-hcell bd-hchips">{chip(label, color)}{risk_chip}</div>'
        f'<div class="bd-hcell bd-hmeta"><span>{e(short_time(deal.last_received_at))}</span><span>{e(emails)}</span></div>'
        f"{_CHEVRON}</div>"
    )


def email_row_html(analysis: Analysis, body: str) -> str:
    label, color = CATEGORY[analysis.category]
    preview = " ".join(body.split())
    return wrap(
        '<div class="bd-hrow">'
        f'<div class="bd-hcell"><div class="bd-row-top"><span class="bd-dot {color}"></span>'
        f'<span class="bd-row-title">{e(analysis.brand or analysis.sender)}</span></div>'
        f'<div class="bd-row-sub">{e(analysis.sender)}</div></div>'
        f'<div class="bd-hcell bd-hsummary"><b>{e(analysis.subject)}</b><br>'
        f'<span class="bd-muted">{e(preview)}</span></div>'
        f'<div class="bd-hcell bd-hchips">{chip(label, color)}</div>'
        f'<div class="bd-hcell bd-hmeta"><span>{e(short_time(analysis.received_at))}</span>'
        f"<span>{e(analysis.email_id)}</span></div>"
        f"{_CHEVRON}</div>"
    )


# ---------------------------------------------------------------- deal detail
def deal_head_html(deal: Deal, sender: str | None = None) -> str:
    action_label, action_color = ACTION[deal.recommendation.action]
    category_label, category_color = CATEGORY[deal.category]
    meta = []
    if deal.agency:
        meta.append(f"via {deal.agency}")
    if deal.campaign:
        meta.append(deal.campaign)
    meta.append(", ".join(deal.email_ids))
    span = short_time(deal.first_received_at)
    if deal.last_received_at != deal.first_received_at:
        span = f"{short_time(deal.first_received_at)} → {short_time(deal.last_received_at)}"
    return wrap(
        f'<div class="bd-detail-chips">{chip(category_label, "outline")}</div>'
        f'<div class="bd-detail-title"><h2>{e(deal_name(deal, sender))}</h2><span class="bd-row-time">{e(span)}</span></div>'
        '<div class="bd-meta-chips">' + "".join(f"<span>{e(m)}</span>" for m in meta) + "</div>"
        f'<div class="bd-callout {action_color}"><div class="bd-label">Recommended next step</div>'
        f'<div class="bd-callout-action">{e(action_label)}</div>'
        f'<div class="bd-callout-text">{emphasize(deal.recommendation.summary)}</div></div>'
    )


def _fee_cell(deal: Deal) -> tuple[str, str, str]:
    """(value, note, note_class) for the fee tile, using the existing rate check."""
    terms, rate = deal.terms, deal.rate_check
    comp = terms.current("compensation")
    note, tone = "", ""
    if comp is None:
        if terms.current("payment_type") == "gifting":
            value = terms.current("product_value")
            return "Product only", f"worth {format_money(value)}" if value else "no cash fee", "warn"
        return "", "", ""
    value = format_amount(comp.amount, comp.currency) if comp.currency else format_amount(comp.amount)
    if comp.currency is None:
        note, tone = "Currency not stated", "warn"
    if rate is not None:
        if rate.status == "below_rate" and rate.shortfall_inr:
            note, tone = f"{format_amount(rate.shortfall_inr, 'INR')} below your rate ({rate.shortfall_pct:g}%)", "warn"
        elif rate.status == "meets_rate":
            note, tone = (note + " · " if note else "") + "Meets your rate", "good"
        elif rate.status == "unpriced_items":
            note, tone = "Some items have no base rate", ""
    return value, note, tone


def terms_html(deal: Deal) -> str:
    terms = deal.terms
    fee, fee_note, fee_tone = _fee_cell(deal)
    platforms = terms.current("platforms")
    deliverables = terms.current("deliverables")
    tiles = [
        ("Offered fee", fee, fee_note, fee_tone, True),
        ("Deliverables", "<br>".join(e(prose_deliverables([d])) for d in deliverables) if deliverables else "",
         "", "", False),
        ("Platform", ", ".join(platform_name(p) for p in platforms) if platforms else "", "", "", False),
        ("Campaign dates", timeline_text(terms.current("timeline")) or "", "", "", False),
        ("Usage rights", usage_text(terms.current("usage_rights")) or "", "", "", False),
        ("Payment terms", terms.current("payment_terms") or "", "", "", False),
    ]
    cells = []
    for label, value, note, tone, big in tiles:
        is_html = label == "Deliverables"
        shown = value if is_html else e(value)
        css = "bd-term-value" + (" big" if big and value else "") + ("" if value else " none")
        note_html = f'<div class="bd-term-note {tone}">{e(note)}</div>' if note else ""
        cells.append(f'<div class="bd-term"><div class="bd-label">{e(label)}</div>'
                     f'<div class="{css}">{shown or "Not stated"}</div>{note_html}</div>')
    return wrap(section_html("Deal terms") + '<div class="bd-terms">' + "".join(cells) + "</div>")


def _evidence(risk: Risk) -> str:
    if not risk.evidence:
        return ""
    ev = risk.evidence[0]
    source = ev.email_id or ("Creator profile" if ev.source == "creator_profile" else "Computed")
    return (f'<div class="bd-risk-evidence"><span class="bd-ev-label">Evidence</span>{e(source)} · '
            f'<q>{e(ev.quote)}</q></div>')


def risks_html(risks: list[Risk]) -> str:
    shown = visible_risks(risks)
    head = section_html("Risks", len(shown), "risk")
    if not shown:
        return wrap(head + '<p class="bd-empty-note">No medium or higher risks found.</p>')
    items = []
    for risk in shown:
        label, color = SEVERITY[risk.severity]
        items.append(
            f'<li class="bd-risk {risk.severity}"><div class="bd-risk-head">{chip(label, color)}'
            f'<span class="bd-risk-title">{e(risk.title)}</span></div>'
            f'<div class="bd-risk-text">{emphasize(risk.explanation)}</div>{_evidence(risk)}</li>'
        )
    return wrap(head + '<ul class="bd-risks">' + "".join(items) + "</ul>")


def actions_html(steps: list[str]) -> str:
    if not steps:
        return ""
    cards = "".join(f'<div class="bd-action"><span class="bd-action-n">{i}</span><span>{emphasize(s)}</span></div>'
                    for i, s in enumerate(steps, 1))
    return wrap(section_html("Suggested actions", len(steps), "action") + '<div class="bd-actions">' + cards + "</div>")


def reply_head_html(draft: DraftReply) -> str:
    return wrap(
        '<div class="bd-reply-head"><span class="bd-reply-title">Suggested reply</span>'
        f'{chip("Draft · not sent", "neutral")}</div>'
        f'<div class="bd-reply-meta">To <b>{e(draft.to)}</b> · {e(draft.subject)}</div>'
    )


def reply_foot_html(draft: DraftReply) -> str:
    notes = [n for n in draft.notes[1:]]
    checks = "".join(f"<li>{e(n)}</li>" for n in notes)
    return wrap(
        '<p class="bd-reply-foot">Review and edit before sending. Use the copy icon at the top right of the draft. '
        "This tool never sends email.</p>"
        + (f'<ul class="bd-list" style="margin-top:6px">{checks}</ul>' if checks else "")
    )


def list_html(title: str, items: list[str]) -> str:
    if not items:
        return ""
    return wrap(f'<div class="bd-section-title">{e(title)}</div><ul class="bd-list">'
                + "".join(f"<li>{e(i)}</li>" for i in items) + "</ul>")


# ---------------------------------------------------------------- email reader
def email_head_html(analysis: Analysis, deal: Deal) -> str:
    category_label, category_color = CATEGORY[analysis.category]
    action_label, action_color = ACTION[analysis.recommendation]
    return wrap(
        f'<div class="bd-detail-chips">{chip(category_label, category_color)}'
        f'{chip("Next step: " + action_label, "outline")}</div>'
        f'<div class="bd-detail-title"><h2>{e(analysis.subject)}</h2>'
        f'<span class="bd-row-time">{e(short_time(analysis.received_at))}</span></div>'
        '<div class="bd-meta-chips">'
        f"<span>{e(analysis.sender)}</span><span>{e(analysis.email_id)}</span>"
        f"<span>Deal: {e(deal_name(deal, analysis.sender))}</span></div>"
    )


def email_body_html(body: str) -> str:
    # Escaped and shown as plain text: links in emails never become clickable.
    return wrap(f'<div class="bd-email-body">{e(body)}</div>')


def alerts_html(signals: list[SecuritySignal]) -> str:
    strong = [s for s in signals if s.strength == "strong"]
    if not strong:
        return ""
    return wrap("".join(
        f'<div class="bd-alert"><span class="bd-dot red" style="margin-top:6px"></span><div>{e(s.explanation)}'
        f'<span class="bd-muted">{e(s.evidence)}</span></div></div>' for s in strong))


# ---------------------------------------------------------------- overview column
def stats_html(emails: int, deals: int, attention: int, scams: int) -> str:
    def stat(label: str, value: int, note: str, tone: str = "") -> str:
        return (f'<div class="bd-stat {tone}"><div class="bd-label">{e(label)}</div>'
                f'<div class="bd-stat-value">{value}</div><div class="bd-stat-note">{e(note)}</div></div>')

    return wrap(
        '<div class="bd-section-title">Inbox overview</div><div class="bd-stats">'
        + stat("Emails", emails, "received")
        + stat("Deals", deals, "after grouping")
        + stat("Need attention", attention, "to act on", "amber")
        + stat("Scam warnings", scams, "do not engage", "red")
        + "</div>"
    )


def bars_html(title: str, rows: list[tuple[str, int, str]], total: int) -> str:
    bars = "".join(
        f'<div><div class="bd-bar-top"><span>{e(label)}</span><b>{count}</b></div>'
        f'<div class="bd-bar"><span class="{color}" style="width:{(100 * count / total) if total else 0:.0f}%"></span>'
        "</div></div>"
        for label, count, color in rows
    )
    return wrap(f'<div class="bd-section-title">{e(title)}</div><div class="bd-bars">{bars}</div>')


def kv_html(title: str, rows: list[tuple[str, str]], note: str = "") -> str:
    body = "".join(f'<div class="bd-kv"><span>{e(k)}</span><b>{e(v)}</b></div>' for k, v in rows)
    note_html = f'<p class="bd-stat-note" style="margin-top:8px">{e(note)}</p>' if note else ""
    return wrap(f'<div class="bd-section-title">{e(title)}</div>{body}{note_html}')
