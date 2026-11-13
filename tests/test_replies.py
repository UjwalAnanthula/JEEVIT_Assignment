# Batch 3: draft replies are grounded in reconciled terms, never leak private data and never go to scams.
# Drafts are templates filled by plain code; nothing is ever sent.
from app.models.schemas import Deliverable
from app.services.reply_generator import prose_deliverables


def deals_by_email(result):
    return {i: d for d in result.deals for i in d.email_ids}


def test_no_drafts_for_scams_or_spam(inbox, analyze):
    result = analyze(list(inbox.values()))
    deals = deals_by_email(result)
    for email_id in ("E-03", "E-07", "E-13", "E-14"):
        assert deals[email_id].draft_reply is None
    assert all(a.draft_reply is None for a in result.analyses if a.category in ("scam_or_phishing", "spam"))


def test_drafts_never_leak_private_or_injected_content(inbox, analyze, profile):
    result = analyze(list(inbox.values()))
    bodies = [d.draft_reply.body for d in result.deals if d.draft_reply]
    assert len(bodies) == 9
    for body in bodies:
        assert profile["email"] not in body and "5,00,000" not in body
        assert "Nova Phones" not in body  # exclusivity partner stays confidential
        assert body.rstrip().endswith("Riya")


def test_dew_lab_draft_counters_and_verifies_agency(inbox, analyze):
    result = analyze(list(inbox.values()))
    deal = deals_by_email(result)["E-11"]
    draft = deal.draft_reply
    assert draft.to == "talent@brightside-agency.com" and draft.subject.startswith("Re: ")
    assert "INR 1,10,000" in draft.body and "INR 1,20,000" in draft.body
    assert "GST" in draft.body and draft.body.startswith("Hi Arjun,")
    assert any("agency" in note for note in draft.notes)
    by_id = {a.email_id: a for a in result.analyses}
    assert by_id["E-01"].draft_reply is None and by_id["E-02"].draft_reply is None  # reply to the latest only
    assert by_id["E-11"].draft_reply == draft.body


def test_request_info_draft_asks_instead_of_inventing(inbox, analyze):
    deals = deals_by_email(analyze([inbox["E-08"], inbox["E-15"]]))
    urban, fitnest = deals["E-08"].draft_reply.body, deals["E-15"].draft_reply.body
    assert "INR" not in urban and "deliverables" in urban and "budget" in urban
    assert "attachments" in fitnest


def test_decline_drafts_give_the_right_reason(inbox, analyze):
    deals = deals_by_email(analyze([inbox["E-04"], inbox["E-05"], inbox["E-12"]]))
    assert "exclusivity until January 2027" in deals["E-04"].draft_reply.body
    assert "isn't something I promote" in deals["E-05"].draft_reply.body
    assert "1 November" in deals["E-12"].draft_reply.body


def test_accept_draft_restates_only_stated_terms(make_email, analyze):
    email = make_email(body="We're Orbit. 1 Instagram reel for INR 45,000, live by 30 November, usage rights for "
                            "3 months on paid ads. Payment within 15 days of posting.")
    draft = analyze([email]).deals[0].draft_reply
    assert "happy to go ahead" in draft.body and "INR 45,000" in draft.body and "share the contract" in draft.body


def test_prose_deliverables():
    items = [Deliverable(platform="instagram", type="reel", quantity=2),
             Deliverable(platform="instagram", type="story", quantity=1),
             Deliverable(platform="youtube", type="integration", quantity=1)]
    assert prose_deliverables(items) == "2 Instagram reels, 1 Instagram story and 1 YouTube integration"
    assert prose_deliverables([]) == ""


def test_draft_never_greets_the_creator(inbox, profile, stub_extractor, analyze):
    from app.services.rule_classifier import extract_with_rules

    email = inbox["E-06"]
    x = extract_with_rules(email).model_copy(update={"contact_name": "Riya ji"})  # a wrong extraction
    draft = analyze([email], extractor=stub_extractor({email.id: x})).deals[0].draft_reply
    assert draft.body.startswith("Hi Chai & Co team,")
