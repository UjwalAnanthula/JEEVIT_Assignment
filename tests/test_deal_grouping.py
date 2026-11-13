# Batch 2 grouping and reconciliation: threads, subjects, campaigns, duplicates, ambiguity and latest terms.
# The Dew Lab case is checked on the real data; every other rule is exercised with synthetic emails.
from app.models.schemas import Money
from app.services.deal_grouper import group_emails
from app.services.rule_classifier import detect_security_signals, extract_with_rules
from app.services.term_reconciler import reconcile


def groups_for(emails):
    extractions = {e.id: extract_with_rules(e) for e in emails}
    signals = {e.id: detect_security_signals(e) for e in emails}
    return group_emails(emails, extractions, signals), extractions


def membership(groups):
    return sorted(sorted(g.email_ids) for g in groups)


def test_dataset_dew_lab_thread_is_one_deal_and_others_separate(inbox):
    groups, _ = groups_for(list(inbox.values()))
    assert len(groups) == 13
    dew = next(g for g in groups if "E-01" in g.email_ids)
    assert dew.email_ids == ["E-01", "E-02", "E-11"] and dew.deal_id == "deal-dew-lab-e-01"
    reasons = {(ev.email_id, ev.linked_email_id): ev.reason for ev in dew.evidence}
    assert "same subject thread" in reasons[("E-02", "E-01")]
    assert "spf" in reasons[("E-11", "E-01")] and "serum" in reasons[("E-11", "E-01")]
    assert all(len(g.email_ids) == 1 for g in groups if g is not dew)


def test_deal_ids_are_stable_regardless_of_input_order(inbox):
    forward, _ = groups_for(list(inbox.values()))
    backward, _ = groups_for(list(reversed(list(inbox.values()))))
    assert {g.deal_id: g.email_ids for g in forward} == {g.deal_id: g.email_ids for g in backward}


def test_thread_headers_group_even_when_subjects_differ(make_email):
    a = make_email("A", body="We're Nimbus. 1 Instagram reel for INR 45,000.", subject="Launch idea",
                   sender="x@nimbus.io", message_id="<m1@nimbus.io>")
    b = make_email("B", body="Updated: INR 50,000.", subject="Totally different subject",
                   sender="y@other-domain.com", received_at="2026-11-06 10:00", in_reply_to="<m1@nimbus.io>")
    c = make_email("C", body="Any update?", subject="ping", sender="z@third.com",
                   received_at="2026-11-07 10:00", thread_id="t-9", references=["<m1@nimbus.io>"])
    groups, _ = groups_for([a, b, c])
    assert membership(groups) == [["A", "B", "C"]]
    assert all(ev.strength == "strong" for ev in groups[0].evidence)


def test_subject_change_without_headers_grouped_by_brand_and_campaign(make_email):
    a = make_email("A", sender="hi@orbit.in", subject="Collab: Orbit x Riya",
                   body="We're Orbit, launching a new smartwatch. 2 Instagram reels for INR 80,000.")
    b = make_email("B", sender="team@orbit.in", subject="Budget update for the smartwatch campaign",
                   received_at="2026-11-07 09:00",
                   body="Hi Riya, for the smartwatch campaign we have revised the budget to INR 90,000.")
    groups, _ = groups_for([a, b])
    assert membership(groups) == [["A", "B"]]


def test_same_brand_different_campaigns_stay_separate_but_linked(make_email):
    a = make_email("A", sender="hi@orbit.in", subject="Smartwatch launch",
                   body="We're Orbit, launching a new smartwatch. 1 Instagram reel for INR 45,000.")
    b = make_email("B", sender="hi@orbit.in", subject="Earphones campaign", received_at="2026-11-08 09:00",
                   body="We're Orbit, launching a new noise-cancelling earphone. 1 Instagram reel for INR 40,000.")
    groups, _ = groups_for([a, b])
    assert membership(groups) == [["A"], ["B"]]
    assert any("different campaigns" in link.reason for g in groups for link in g.related)


def test_ambiguous_follow_up_is_not_merged(make_email):
    a = make_email("A", sender="hi@orbit.in", subject="Watch", body="We're Orbit, launching a new smartwatch. 1 reel, INR 45,000.")
    b = make_email("B", sender="hi@orbit.in", subject="Phones", received_at="2026-11-06 09:00",
                   body="We're Orbit, launching a new tablet. 1 reel, INR 40,000.")
    c = make_email("C", sender="hi@orbit.in", subject="Following up", received_at="2026-11-09 09:00",
                   body="Hi Riya, following up on our offer. Let us know!")
    groups, _ = groups_for([a, b, c])
    assert ["C"] in membership(groups)
    c_group = next(g for g in groups if g.email_ids == ["C"])
    assert {link.other_email_id for link in c_group.related} == {"A", "B"}


def test_duplicates_grouped_without_double_counting(make_email):
    body = "We're Orbit. 2 Instagram reels for INR 80,000."
    a = make_email("A", sender="hi@orbit.in", subject="Collab", body=body)
    b = make_email("B", sender="hi@orbit.in", subject="Collab", body=body, received_at="2026-11-05 10:05")
    groups, extractions = groups_for([a, b])
    assert membership(groups) == [["A", "B"]] and groups[0].evidence[0].strength == "duplicate"
    terms = reconcile([a, b], extractions)
    assert terms.compensation.value.amount == 80000 and terms.compensation.status == "confirmed"
    assert terms.deliverables.value[0].quantity == 2


def test_scam_using_same_subject_is_not_merged(make_email):
    real = make_email("A", sender="hi@orbit.in", subject="Collab: Orbit x Riya", body="We're Orbit. 1 reel for INR 45,000.")
    fake = make_email("B", sender="orbit.payments@gmail.com", subject="Re: Collab: Orbit x Riya",
                      received_at="2026-11-06 10:00",
                      body="We're Orbit. Pay a refundable security deposit of Rs 5,000 to confirm the collab.")
    groups, _ = groups_for([real, fake])
    assert membership(groups) == [["A"], ["B"]]


def test_freemail_senders_with_generic_subject_are_not_merged(make_email):
    a = make_email("A", sender="tea.one@gmail.com", subject="Collab?", body="Hum Tea One hain. 1 reel, budget 20k.")
    b = make_email("B", sender="snack.two@gmail.com", subject="Collab?", received_at="2026-11-06 10:00",
                   body="Hum Snack Two hain. 1 reel, budget 30k.")
    groups, _ = groups_for([a, b])
    assert membership(groups) == [["A"], ["B"]]


# ---------------------------------------------------------------- reconciliation
def test_latest_terms_use_timestamps_not_ids(make_email):
    first = make_email("Z-9", sender="hi@orbit.in", subject="Collab: Orbit x Riya",
                       body="We're Orbit. 1 Instagram reel for INR 10,000.", received_at="2026-11-01 09:00")
    counter = make_email("M-5", sender="hi@orbit.in", subject="Re: Collab: Orbit x Riya",
                         body="We have revised the budget to INR 15,000.", received_at="2026-11-03 09:00")
    final = make_email("A-1", sender="hi@orbit.in", subject="Re: Collab: Orbit x Riya",
                       body="Confirmed: final fee INR 12,000 for the reel.", received_at="2026-11-05 09:00")
    groups, extractions = groups_for([final, first, counter])
    assert groups[0].email_ids == ["Z-9", "M-5", "A-1"]
    terms = reconcile([final, counter, first], extractions)
    fee = terms.compensation
    assert fee.value.amount == 12000 and fee.source_email_id == "A-1" and fee.status == "revised"
    assert [(e.email_id, e.value.amount, e.change_type) for e in fee.history] == [
        ("Z-9", 10000, "stated"), ("M-5", 15000, "revised"), ("A-1", 12000, "revised")]
    assert terms.deliverables.source_email_id == "Z-9"  # never restated, never lost
    assert terms.ambiguities == []


def test_unexplained_change_is_flagged(make_email, analyze):
    a = make_email("A", sender="hi@orbit.in", subject="Collab: Orbit x Riya",
                   body="We're Orbit. 1 Instagram reel for INR 50,000. Usage rights for 3 months.")
    b = make_email("B", sender="hi@orbit.in", subject="Re: Collab: Orbit x Riya", received_at="2026-11-07 10:00",
                   body="Hi Riya, it's INR 45,000 for 1 Instagram reel.")
    result = analyze([a, b])
    deal = result.deals[0]
    assert deal.terms.compensation.status == "changed_without_cue"
    assert any("confirm which is final" in a for a in deal.terms.ambiguities)
    assert "terms_ambiguous" in {r.code for r in deal.risks}


def test_unrelated_later_message_does_not_overwrite_terms(make_email, analyze):
    a = make_email("A", sender="hi@orbit.in", body="We're Orbit. 1 Instagram reel for INR 50,000.")
    b = make_email("B", sender="hi@zenfit.in", received_at="2026-11-09 10:00",
                   body="We're Zenfit. 1 Instagram reel for INR 20,000.")
    deals = {d.brand: d for d in analyze([a, b]).deals}
    assert deals["Orbit"].terms.compensation.value.amount == 50000
    assert deals["Zenfit"].terms.compensation.value.amount == 20000


def test_currency_carried_forward_with_note(make_email):
    a = make_email("A", sender="hi@orbit.in", subject="Collab: Orbit", body="We're Orbit. 1 reel for USD 600.")
    b = make_email("B", sender="hi@orbit.in", subject="Re: Collab: Orbit", received_at="2026-11-06 10:00",
                   body="We can now do 700 dollars instead.")
    c = make_email("C", sender="hi@orbit.in", subject="Re: Collab: Orbit", received_at="2026-11-07 10:00",
                   body="Revised budget: 750 for the reel.")
    _, extractions = groups_for([a, b, c])
    extractions["C"].compensation = Money(amount=750, currency=None)  # parser ignores bare numbers
    terms = reconcile([a, b, c], extractions)
    assert terms.compensation.value.amount == 750 and terms.compensation.value.currency == "USD"
    assert any("did not restate the currency" in note for note in terms.ambiguities)


def test_suspicious_message_in_thread_does_not_change_terms(make_email, analyze):
    a = make_email("A", sender="hi@orbit.in", subject="Collab", body="We're Orbit. 1 Instagram reel for INR 50,000.",
                   message_id="<a@orbit.in>")
    b = make_email("B", sender="billing@orbit-pay.xyz", subject="Re: Collab", received_at="2026-11-06 10:00",
                   in_reply_to="<a@orbit.in>",
                   body="Updated fee INR 90,000! Pay a processing fee of Rs 2,000 first to unlock it.")
    deal = analyze([a, b]).deals[0]
    assert deal.email_ids == ["A", "B"]
    assert deal.terms.compensation.value.amount == 50000
    assert deal.category == "scam_or_phishing" and deal.recommendation.action == "flag_for_security_review"
