# Folds a deal's emails, in received-time order, into current terms while keeping every earlier value.
# Later explicit values win (TASK: "use the latest terms"); unexplained changes are flagged, not hidden.
from typing import Any, get_args

from app.models.schemas import (
    Deliverable,
    EmailRecord,
    Extraction,
    Money,
    ReconciledTerms,
)
from app.utils.helpers import format_money, normalize_key

TERM_FIELDS = (
    "compensation",
    "payment_type",
    "product_value",
    "deliverables",
    "platforms",
    "timeline",
    "usage_rights",
    "exclusivity",
    "payment_terms",
)
# Messages that explicitly revise, counter or confirm terms may change a value without being flagged.
_REVISION_ROLES = frozenset({"revision", "counter_offer", "confirmation"})


def _stated_terms(x: Extraction) -> dict[str, Any]:
    platforms = sorted({d.platform for d in x.deliverables if d.platform not in ("unknown", "other")})
    return {
        "compensation": x.compensation,
        "payment_type": x.payment_type if x.payment_type != "unknown" else None,
        "product_value": x.product_value,
        "deliverables": list(x.deliverables) or None,
        "platforms": platforms or None,
        "timeline": x.timeline,
        "usage_rights": x.usage_rights,
        "exclusivity": x.exclusivity,
        "payment_terms": x.payment_terms,
    }


def _comparable(name: str, value: Any) -> Any:
    """Normalize a term so that rewording the same value counts as a confirmation, not a change."""
    if value is None:
        return None
    if name in ("compensation", "product_value"):
        return (round(value.amount, 2), value.currency)
    if name == "deliverables":
        return tuple(sorted((d.platform, d.type, d.quantity) for d in value))
    if name == "timeline":
        return (value.start_date, value.end_date, normalize_key(value.relative))
    if name == "usage_rights":
        return (value.duration_months, value.perpetual, value.paid_ads, value.whitelisting, value.all_media)
    if name == "exclusivity":
        return (normalize_key(value.raw_text), value.duration_months)
    if name == "payment_terms":
        return normalize_key(value)
    return value


def describe_term(name: str, value: Any) -> str:
    if name in ("compensation", "product_value"):
        return format_money(value)
    if name == "deliverables":
        return ", ".join(d.label() for d in value)
    if name == "timeline":
        return value.raw_text
    if name in ("usage_rights", "exclusivity"):
        return value.raw_text or "stated"
    return str(value)


def _apply_additions(state: dict[str, dict[str, Any]], email: EmailRecord, additions: list[Deliverable]) -> None:
    """'Add one more story' increases the matching item (or appends it); recorded as an explicit revision."""
    existing = state.get("deliverables")
    merged = [d.model_copy() for d in (existing["value"] if existing else [])]
    for extra in additions:
        target = next((d for d in merged if d.type == extra.type
                       and (d.platform == extra.platform or "unknown" in (d.platform, extra.platform))), None)
        if target is not None and target.quantity and extra.quantity:
            target.quantity += extra.quantity
        else:
            merged.append(extra)
    event = {"email_id": email.id, "received_at": email.received_at, "value": merged,
             "change_type": "revised" if existing else "stated"}
    state["deliverables"] = {
        "value": merged,
        "source_email_id": email.id,
        "source_received_at": email.received_at,
        "status": event["change_type"],
        "history": (existing["history"] if existing else []) + [event],
    }
    platforms = sorted({d.platform for d in merged if d.platform not in ("unknown", "other")})
    previous = state.get("platforms")
    if platforms and (previous is None or previous["value"] != platforms):
        state["platforms"] = {
            "value": platforms, "source_email_id": email.id, "source_received_at": email.received_at,
            "status": "revised" if previous else "stated",
            "history": (previous["history"] if previous else []) + [{**event, "value": platforms}],
        }


def _term_model(name: str) -> type:
    annotation = ReconciledTerms.model_fields[name].annotation
    return next(arg for arg in get_args(annotation) if arg is not type(None))


def reconcile(
    emails: list[EmailRecord],
    extractions: dict[str, Extraction],
    excluded: set[str] | None = None,
) -> ReconciledTerms:
    """`emails` may arrive in any order; they are sorted by received time (id only breaks exact ties)."""
    excluded = excluded or set()
    ordered = sorted(emails, key=lambda e: (e.received_dt, e.id))
    state: dict[str, dict[str, Any]] = {}
    identity = {"brand": None, "agency": None, "campaign": None}
    ambiguities: list[str] = []

    for prev, email in zip([None, *ordered[:-1]], ordered):
        if prev is not None and prev.received_dt == email.received_dt:
            ambiguities.append(f"{prev.id} and {email.id} have the same timestamp; order between them is uncertain.")
        x = extractions[email.id]
        if email.id in excluded:
            # Keep the claimed sender for display only; never let it replace a legitimate brand.
            identity["brand"] = identity["brand"] or x.brand
            ambiguities.append(f"{email.id} was flagged as suspicious; its terms were not applied.")
            continue

        identity["brand"] = x.brand or identity["brand"]
        identity["agency"] = x.agency or identity["agency"]
        identity["campaign"] = identity["campaign"] or x.campaign_name or x.product

        stated = _stated_terms(x)
        current_comp = state.get("compensation", {}).get("value")
        new_comp = stated["compensation"]
        if new_comp is not None and new_comp.currency is None and current_comp and current_comp.currency:
            stated["compensation"] = Money(amount=new_comp.amount, currency=current_comp.currency)
            ambiguities.append(
                f"{email.id} did not restate the currency; assumed {current_comp.currency} as in earlier messages."
            )

        for name in TERM_FIELDS:
            value = stated[name]
            if value is None:
                continue  # silence never overwrites an earlier term
            existing = state.get(name)
            if existing is None:
                change = "stated"
            elif _comparable(name, existing["value"]) == _comparable(name, value):
                change = "confirmed"
            elif x.message_role in _REVISION_ROLES:
                change = "revised"
            else:
                change = "changed_without_cue"
                ambiguities.append(
                    f"{name.replace('_', ' ').capitalize()} changed from '{describe_term(name, existing['value'])}' "
                    f"({existing['source_email_id']}) to '{describe_term(name, value)}' ({email.id}) without explicit "
                    "revision wording; confirm which is final."
                )
            event = {"email_id": email.id, "received_at": email.received_at, "value": value, "change_type": change}
            history = (existing["history"] if existing else []) + [event]
            if change == "confirmed" and existing is not None:
                existing.update(status="confirmed", history=history)  # original source is kept
            else:
                state[name] = {
                    "value": value,
                    "source_email_id": email.id,
                    "source_received_at": email.received_at,
                    "status": change,
                    "history": history,
                }

        if x.deliverable_additions:
            _apply_additions(state, email, x.deliverable_additions)

    return ReconciledTerms(
        as_of_email_id=ordered[-1].id if ordered else None,
        **identity,
        **{name: _term_model(name).model_validate(data) for name, data in state.items()},
        ambiguities=ambiguities,
    )


def term_history_summary(terms: ReconciledTerms, name: str) -> str | None:
    """Human-readable trail such as 'INR 1,20,000 (E-01) -> INR 1,00,000 (E-02, revised)'."""
    term = getattr(terms, name)
    if term is None or len(term.history) < 2:
        return None
    parts = []
    for event in term.history:
        label = event.email_id if event.change_type == "stated" else f"{event.email_id}, {event.change_type.replace('_', ' ')}"
        parts.append(f"{describe_term(name, event.value)} ({label})")
    return " -> ".join(parts)
