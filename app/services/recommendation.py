# Creates a plain-language Markdown summary for the creator to review.
def build_summary(items):
    lines=["# Brand inbox summary","",f"Processed **{len(items)} emails**.","","## Needs attention"]
    for x in items:
        if x.category!="spam":
            risks="; ".join(x.risk_flags) if x.risk_flags else "No deterministic risk flag detected."
            lines.append(f"- **{x.email_id} — {x.brand or 'Unknown brand'}:** {x.category}; next step: {x.recommendation}. {risks}")
    return "\n".join(lines)+"\n"
