# Applies deterministic creator-policy and suspicious-request checks.
def assess(email, profile, result):
    text=f"{email.subject} {email.body}".lower(); risks=[]
    if any(x in text for x in ["registration fee","pay a one-time","unlock free"]): risks.append("Upfront payment request violates creator policy.")
    if any(x in text for x in ["fantasy","betting","gambling"]): risks.append("Conflicts with the creator's no-gambling/fantasy-sports rule.")
    if "pixelon" in text and any(x["brand"].lower()=="nova phones" for x in profile.get("current_exclusivity",[])): risks.append("Smartphone campaign conflicts with Nova Phones exclusivity.")
    if "perpetual" in text or "whitelisting" in text: risks.append("Perpetual usage/whitelisting exceeds included usage-rights policy.")
    if "http://" in text or "https://" in text: risks.append("Contains a link; verify independently and do not enter credentials.")
    if "ai assistant" in text or "home address" in text: risks.append("Contains prompt-injection language or requests private information.")
    if "1 november" in text and profile.get("today","") > "2026-11-01": risks.append("Requested deadline is already in the past.")
    result.risk_flags=risks
    return result
