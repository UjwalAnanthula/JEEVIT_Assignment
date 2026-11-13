# Classifies sample emails with deterministic rules and extracts basic deal fields.
import re
from app.models.schemas import Analysis
from app.services.risk_analyzer import assess
def classify(email, profile):
    text=f"{email.subject} {email.body}".lower()
    if any(x in text for x in ["copyright strikes","verify your account","registration fee","pay a one-time"]) or "quickcash-media" in email.from_.lower(): cat="scam_or_phishing"
    elif "newsletter" in email.from_.lower() or "unsubscribe anytime" in text: cat="spam"
    elif any(x in text for x in ["fantasy","betting","gambling"]) or "pixelon" in text or ("1 november" in text and profile.get("today", "") > "2026-11-01"): cat="not_a_fit"
    elif "campaign brief attached" in text or ("collab" in text and not any(x in text for x in ["budget","fee","inr","rs "])): cat="needs_more_info"
    else: cat="deal_offer"
    brands=[("dew lab","Dew Lab"),("pixelon","Pixelon"),("luckyplay11","LuckyPlay11"),("chai & co","Chai & Co"),("urban thread","Urban Thread"),("brewbox","BrewBox"),("auralbuds","AuralBuds"),("snapmeal","SnapMeal"),("fitnest","FitNest")]
    brand=next((name for key,name in brands if key in text),None)
    m=re.search(r"(?:INR|Rs\.?|USD)\s*([\d,]+(?:\.\d+)?)",email.body,re.I)
    fee=float(m.group(1).replace(",","")) if m else None
    currency="USD" if "usd" in text else ("INR" if m else None)
    payment="gifting" if any(x in text for x in ["free coffee","worth rs","gift","barter"]) else ("cash" if fee else "unknown")
    deliverables=[x for x in ["Instagram reel","Instagram stories","YouTube integration","YouTube shoutout"] if x.lower().split()[0] in text]
    recommendation={"scam_or_phishing":"report","spam":"ignore","not_a_fit":"decline","needs_more_info":"ask for details","deal_offer":"negotiate"}[cat]
    reply=None if cat in ["spam","scam_or_phishing"] else ("Thanks for reaching out. Please share the complete brief, timeline, deliverables and usage terms." if cat=="needs_more_info" else "Thanks for the offer. Could you confirm the final scope, payment schedule and usage rights?")
    item=Analysis(email_id=email.id,category=cat,brand=brand,platform="Instagram" if "instagram" in text or "reel" in text else ("YouTube" if "youtube" in text else None),deliverables=deliverables,fee=fee,currency=currency,payment_type=payment,recommendation=recommendation,draft_reply=reply)
    return assess(email,profile,item)
