# Batch 2 policy tests: profile parsing, rates/currency, barter, content rules, exclusivity, deadlines, usage.
# Also checks that recommendations follow deterministically from category and risks.
import copy
from datetime import date

from app.core.config import Settings
from app.models.schemas import Deliverable, Extraction, Money
from app.services.risk_analyzer import CreatorPolicy


def risk_codes(item):
    return {r.code for r in item.risks}


def single(analyze, email, **kwargs):
    result = analyze([email], **kwargs)
    return result.analyses[0], result.deals[0]


# ---------------------------------------------------------------- profile parsing
def test_profile_is_parsed_without_inventing_preferences(profile):
    policy = CreatorPolicy.from_profile(profile)
    terms = {t for rule in policy.prohibited for t in rule.terms}
    assert {"gambling", "betting", "fantasy sports", "crypto", "trading tip"} <= terms
    assert policy.pay_to_join_rule == "Never pays to join programmes or campaigns."
    assert policy.fx_to_rate_currency == {"INR": 1.0, "USD": 84.0}
    assert policy.accepted_currencies == {"INR", "USD"}
    assert policy.included_usage_months == 3
    assert (policy.today, policy.today_source) == (date(2026, 11, 12), "creator profile 'today'")
    assert policy.platforms == {"instagram", "youtube"}


def test_unrecognised_rule_is_reported_as_unknown(profile, make_email, analyze):
    custom = copy.deepcopy(profile)
    custom["content_rules"].append("Prefers brands with vegan certification.")
    analysis, _ = single(analyze, make_email(body="We're Orbit. 1 Instagram reel for INR 45,000."), profile_override=custom)
    unknown = [c for c in analysis.policy_checks if c.name == "unparsed_rule"]
    assert unknown and unknown[0].status == "unknown" and "vegan" in unknown[0].detail


def test_reference_date_override(profile):
    policy = CreatorPolicy.from_profile(profile, override_today=date(2026, 10, 1))
    assert policy.today == date(2026, 10, 1) and policy.today_source == "REFERENCE_DATE setting"
    assert Settings(_env_file=None, reference_date="2026-10-01").reference_date == date(2026, 10, 1)


# ---------------------------------------------------------------- rates and currency
def test_below_rate_and_counter_offer(make_email, analyze):
    analysis, deal = single(analyze, make_email(body="We're Orbit. 2 Instagram reels and 3 stories for INR 1,00,000."))
    rate = deal.rate_check
    assert (rate.status, rate.minimum_inr, rate.shortfall_inr, rate.shortfall_pct) == ("below_rate", 110000, 10000, 9.1)
    assert deal.category == "deal_offer" and deal.recommendation.action == "negotiate"
    assert any("1,10,000" in step for step in deal.recommendation.next_steps)


def test_usd_converted_with_profile_rate(make_email, analyze):
    _, deal = single(analyze, make_email(body="We're Orbit. 1 Instagram reel for USD 800. Usage rights for 3 months."))
    assert deal.rate_check.status == "meets_rate" and deal.rate_check.offered_inr == 67200
    assert any("1 USD = 84" in a for a in deal.rate_check.assumptions)


def test_unknown_currency_conversion_is_not_guessed(make_email, analyze):
    _, deal = single(analyze, make_email(body="We're Orbit. 1 Instagram reel for EUR 500."))
    assert deal.rate_check.status == "unavailable" and deal.rate_check.offered_inr is None
    assert {"rate_comparison_unavailable", "currency_not_accepted"} <= risk_codes(deal)


def test_unstated_currency_is_flagged_as_assumption(inbox, analyze):
    _, deal = single(analyze, inbox["E-06"])
    assert deal.terms.compensation.value.currency is None
    assert deal.rate_check.status == "below_rate" and deal.rate_check.assumptions
    assert "currency_unstated" in risk_codes(deal)


def test_items_without_base_rate_are_not_priced(inbox, analyze):
    custom_inbox = inbox["E-05"].model_copy(update={"body": "2 reels and a YouTube shoutout for Rs 3,00,000."})
    _, deal = single(analyze, custom_inbox)
    assert deal.rate_check.status == "unpriced_items" and deal.rate_check.unpriced == ["1 x YouTube shoutout"]


# ---------------------------------------------------------------- barter, content rules, platforms
def test_barter_is_not_cash(inbox, analyze):
    analysis, deal = single(analyze, inbox["E-09"])
    assert deal.terms.current("payment_type") == "gifting" and deal.terms.compensation is None
    assert deal.rate_check.status == "not_applicable" and deal.rate_check.minimum_inr == 60000
    assert "barter_only" in risk_codes(deal) and deal.category == "deal_offer"
    assert deal.recommendation.action == "negotiate"
    assert any(c.name == "barter_preference" and c.status == "unknown" for c in deal.policy_checks)


def test_prohibited_category_declined_without_false_positives(inbox, make_email, analyze):
    _, deal = single(analyze, inbox["E-05"])
    assert deal.category == "not_a_fit" and deal.recommendation.action == "decline"
    assert "prohibited_content" in risk_codes(deal)
    crypto = make_email(body="We're CoinNest, a bitcoin exchange. 1 Instagram reel for INR 60,000.")
    assert single(analyze, crypto)[1].category == "not_a_fit"
    window = make_email(body="We're Orbit. 1 Instagram reel between 15 and 25 November, INR 45,000. Better together!")
    assert "prohibited_content" not in risk_codes(single(analyze, window)[1])


def test_unsupported_platform_is_not_a_fit(make_email, analyze, stub_extractor):
    email = make_email(body="Two TikTok dance videos for INR 80,000.")
    x = Extraction(source="groq", model="test", is_brand_collaboration=True, brand="Orbit",
                   deliverables=[Deliverable(platform="other", type="video", quantity=2)],
                   compensation=Money(amount=80000, currency="INR"), payment_type="cash")
    _, deal = single(analyze, email, extractor=stub_extractor({email.id: x}))
    assert deal.category == "not_a_fit" and "platform_unsupported" in risk_codes(deal)


# ---------------------------------------------------------------- exclusivity
def test_competing_smartphone_conflicts_with_exclusivity(inbox, analyze):
    _, deal = single(analyze, inbox["E-04"])
    assert deal.category == "not_a_fit" and "exclusivity_conflict" in risk_codes(deal)
    risk = next(r for r in deal.risks if r.code == "exclusivity_conflict")
    assert any(e.source == "creator_profile" and "Nova Phones" in e.quote for e in risk.evidence)


def test_exclusivity_edge_cases(make_email, analyze, profile):
    partner = make_email(sender="team@novaphones.com", body="We're Nova Phones. 1 Instagram reel on our new smartphone, INR 60,000.")
    assert "exclusivity_conflict" not in risk_codes(single(analyze, partner)[1])
    after = make_email(body="We're Orbit, launching a new smartphone. 1 Instagram reel by 20 February 2027, INR 60,000.")
    assert "exclusivity_conflict" not in risk_codes(single(analyze, after)[1])
    earbuds = make_email(body="We're Orbit. 1 Instagram reel reviewing our new earbuds, INR 60,000.")
    assert "exclusivity_conflict" not in risk_codes(single(analyze, earbuds)[1])
    expired = copy.deepcopy(profile)
    expired["current_exclusivity"][0]["until"] = "2026-01-31"
    phone = make_email(body="We're Orbit, launching a new smartphone. 1 Instagram reel, INR 60,000.")
    assert "exclusivity_conflict" not in risk_codes(single(analyze, phone, profile_override=expired)[1])


def test_brand_requested_exclusivity_is_negotiated(make_email, analyze):
    email = make_email(body="We're Orbit. 1 Instagram reel for INR 60,000. We need category exclusivity for 6 months.")
    _, deal = single(analyze, email)
    assert "exclusivity_requested" in risk_codes(deal) and deal.recommendation.action == "negotiate"


# ---------------------------------------------------------------- deadlines and usage
def test_expired_deadline_uses_reference_date_not_clock(inbox, analyze):
    _, deal = single(analyze, inbox["E-12"])
    risk = next(r for r in deal.risks if r.code == "deadline_expired")
    assert deal.category == "not_a_fit" and "12 Nov 2026" in risk.explanation


def test_tight_and_missing_deadlines(make_email, analyze):
    tight = make_email(body="We're Orbit. 1 Instagram reel for INR 45,000, live by 14 November.",
                       received_at="2026-11-11 10:00")
    assert "deadline_tight" in risk_codes(single(analyze, tight)[1])
    missing = single(analyze, make_email(body="We're Orbit. 1 Instagram reel for INR 45,000."))[1]
    assert "timeline" in missing.missing_information
    assert any(c.name == "deadline" and c.status == "unknown" for c in missing.policy_checks)


def test_usage_rights_rules(inbox, make_email, analyze):
    _, deal = single(analyze, inbox["E-10"])
    assert {"usage_perpetual", "whitelisting_requested"} <= risk_codes(deal)
    assert next(r for r in deal.risks if r.code == "usage_perpetual").severity == "high"
    assert deal.category == "deal_offer" and deal.recommendation.action == "negotiate"
    six = make_email(body="We're Orbit. 1 Instagram reel for INR 60,000 with usage rights for 6 months.")
    assert "usage_exceeds_included" in risk_codes(single(analyze, six)[1])
    three = make_email(body="We're Orbit. 1 Instagram reel for INR 60,000 with usage rights for 3 months.")
    assert not {"usage_exceeds_included", "usage_perpetual"} & risk_codes(single(analyze, three)[1])


# ---------------------------------------------------------------- missing info, scams, recommendations
def test_missing_campaign_details(inbox, analyze):
    analysis, deal = single(analyze, inbox["E-15"])
    assert deal.category == "needs_more_info" and deal.recommendation.action == "request_more_information"
    assert deal.terms.compensation is None and deal.terms.deliverables is None
    assert "attachment_not_reviewed" in risk_codes(deal)


def test_scams_flagged_for_security(inbox, analyze):
    for email_id in ("E-03", "E-07", "E-14"):
        analysis, deal = single(analyze, inbox[email_id])
        assert deal.category == "scam_or_phishing", email_id
        assert deal.recommendation.action == "flag_for_security_review" and analysis.draft_reply is None
        assert any(r.severity == "critical" for r in deal.risks)
    upfront = single(analyze, inbox["E-03"])[1]
    assert any(e.source == "creator_profile" for r in upfront.risks for e in r.evidence)


def test_clean_offer_is_accepted(make_email, analyze):
    email = make_email(body="We're Orbit. 1 Instagram reel for INR 45,000, live by 30 November, usage rights for "
                            "3 months on paid ads. Payment within 15 days of posting.")
    _, deal = single(analyze, email)
    assert deal.category == "deal_offer" and deal.recommendation.action == "accept"
    assert deal.missing_information == []


def test_recommendations_are_consistent_and_explained(inbox, analyze):
    result = analyze(list(inbox.values()))
    allowed = {
        "scam_or_phishing": {"flag_for_security_review"},
        "spam": {"ignore"},
        "not_a_fit": {"decline"},
        "needs_more_info": {"request_more_information"},
        "deal_offer": {"accept", "negotiate", "review_manually"},
    }
    for item in [*result.analyses, *result.deals]:
        rec = item.recommendation_detail if hasattr(item, "recommendation_detail") else item.recommendation
        assert rec.action in allowed[item.category]
        assert rec.summary and (rec.reasons or rec.next_steps)
        for risk in item.risks:
            assert risk.explanation and risk.mitigation
            if risk.blocking:
                assert item.category in ("not_a_fit", "scam_or_phishing")
