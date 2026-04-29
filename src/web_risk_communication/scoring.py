from typing import Any

from config import CONFIDENCE_SCORES, SEVERITY_MESSAGES, SEVERITY_MINIMUM_SCORES, SEVERITY_ORDER, SOURCE_MULTIPLIERS, THREAT_MULTIPLIERS


def highest_confidence(threats: list[dict[str, str]]) -> str:
    if not threats:
        return "SAFE"
    return max(threats, key=lambda threat: CONFIDENCE_SCORES[threat["confidenceLevel"]])["confidenceLevel"]


def is_critical_fast_fail(full_result: dict[str, Any]) -> bool:
    for threat in full_result["threats"]:
        if threat["threatType"] in {"SOCIAL_ENGINEERING", "MALWARE"} and threat["confidenceLevel"] in {"VERY_HIGH", "EXTREMELY_HIGH"}:
            return True
    return False


def build_critical_decision(full_result: dict[str, Any]) -> dict[str, Any]:
    top_threat = pick_top_threat(
        [
            {
                "source": "full_url",
                "threatType": threat["threatType"],
                "confidenceLevel": threat["confidenceLevel"],
                "score": 100,
            }
            for threat in full_result["threats"]
        ]
    )
    message = build_message("CRITICAL", top_threat)
    return {
        "finalScore": 100,
        "severity": "CRITICAL",
        "recommendation": "DO_NOT_PROCEED",
        "topThreat": top_threat,
        "message": message,
    }


def build_decision(full_result: dict[str, Any], domain_result: dict[str, Any]) -> dict[str, Any]:
    signals = []
    signals.extend(build_signals(full_result["threats"], "full_url"))
    signals.extend(build_signals(domain_result["threats"], "domain"))

    highest_signal = max((signal["score"] for signal in signals), default=0)
    final_score = highest_signal + multi_threat_bonus(signals) + cross_source_bonus(full_result["threats"], domain_result["threats"])
    final_score = min(100, int(round(final_score)))

    severity = severity_from_score(final_score)
    severity = apply_guardrails(severity, full_result["threats"], domain_result["threats"])
    top_threat = pick_top_threat(signals)
    message = build_message(severity, top_threat)

    return {
        "finalScore": max(final_score, SEVERITY_MINIMUM_SCORES[severity]),
        "severity": severity,
        "recommendation": message["recommendation"],
        "topThreat": top_threat,
        "message": {
            "title": message["title"],
            "body": message["body"],
            "action": message["action"],
            "shortBody": message["shortBody"],
        },
    }


def build_signals(threats: list[dict[str, str]], source: str) -> list[dict[str, Any]]:
    signals = []
    for threat in threats:
        base_score = CONFIDENCE_SCORES[threat["confidenceLevel"]]
        signal_score = base_score * THREAT_MULTIPLIERS[threat["threatType"]] * SOURCE_MULTIPLIERS[source]
        signals.append(
            {
                "source": source,
                "threatType": threat["threatType"],
                "confidenceLevel": threat["confidenceLevel"],
                "score": min(100, int(round(signal_score))),
            }
        )
    return signals


def multi_threat_bonus(signals: list[dict[str, Any]]) -> int:
    distinct_types = {signal["threatType"] for signal in signals}
    if len(distinct_types) >= 3:
        return 15
    if len(distinct_types) == 2:
        return 8
    return 0


def cross_source_bonus(full_threats: list[dict[str, str]], domain_threats: list[dict[str, str]]) -> int:
    bonus = 0
    full_types = {threat["threatType"] for threat in full_threats}
    domain_types = {threat["threatType"] for threat in domain_threats}
    if full_types & domain_types:
        bonus += 10

    full_medium_plus = any(CONFIDENCE_SCORES[threat["confidenceLevel"]] >= CONFIDENCE_SCORES["MEDIUM"] for threat in full_threats)
    domain_medium_plus = any(CONFIDENCE_SCORES[threat["confidenceLevel"]] >= CONFIDENCE_SCORES["MEDIUM"] for threat in domain_threats)
    if full_medium_plus and domain_medium_plus:
        bonus += 5
    return bonus


def severity_from_score(score: int) -> str:
    if score >= 85:
        return "CRITICAL"
    if score >= 65:
        return "HIGH_RISK"
    if score >= 35:
        return "SUSPICIOUS"
    if score >= 15:
        return "GUARDED"
    return "LOW"


def apply_guardrails(current_severity: str, full_threats: list[dict[str, str]], domain_threats: list[dict[str, str]]) -> str:
    severity = current_severity

    if any(threat["threatType"] in {"SOCIAL_ENGINEERING", "MALWARE"} and threat["confidenceLevel"] in {"HIGHER", "VERY_HIGH", "EXTREMELY_HIGH"} for threat in full_threats):
        severity = max_severity(severity, "HIGH_RISK")

    for threat_type in THREAT_MULTIPLIERS:
        full_confidence = confidence_for_type(full_threats, threat_type)
        domain_confidence = confidence_for_type(domain_threats, threat_type)
        if full_confidence == "MEDIUM" and confidence_at_least(domain_confidence, "MEDIUM"):
            severity = max_severity(severity, "SUSPICIOUS")

    full_is_safe_low = all(threat["confidenceLevel"] in {"SAFE", "LOW"} for threat in full_threats) or not full_threats
    if full_is_safe_low and any(confidence_at_least(threat["confidenceLevel"], "MEDIUM") for threat in domain_threats):
        severity = min_severity(severity, "GUARDED")

    return severity


def confidence_for_type(threats: list[dict[str, str]], threat_type: str) -> str | None:
    matching = [threat["confidenceLevel"] for threat in threats if threat["threatType"] == threat_type]
    if not matching:
        return None
    return max(matching, key=lambda confidence: CONFIDENCE_SCORES[confidence])


def confidence_at_least(value: str | None, minimum: str) -> bool:
    if value is None:
        return False
    return CONFIDENCE_SCORES[value] >= CONFIDENCE_SCORES[minimum]


def max_severity(left: str, right: str) -> str:
    return left if SEVERITY_ORDER.index(left) >= SEVERITY_ORDER.index(right) else right


def min_severity(current: str, maximum: str) -> str:
    return maximum if SEVERITY_ORDER.index(current) > SEVERITY_ORDER.index(maximum) else current


def pick_top_threat(signals: list[dict[str, Any]]) -> str:
    if not signals:
        return "NONE"
    return max(signals, key=lambda signal: signal["score"])["threatType"]


def build_message(severity: str, top_threat: str) -> dict[str, str]:
    message = dict(SEVERITY_MESSAGES[severity])
    if top_threat == "SOCIAL_ENGINEERING":
        if severity in {"HIGH_RISK", "CRITICAL"}:
            message["title"] = "High-risk phishing link detected" if severity == "HIGH_RISK" else "Do not proceed"
        message["body"] = (
            "This link appears strongly associated with phishing or scam activity. It may try to trick you into entering passwords, verification codes, payment details, or other sensitive information."
            if severity in {"HIGH_RISK", "CRITICAL"}
            else "This link may be trying to trick you into sharing passwords, verification codes, or payment information. Be especially cautious if the message creates urgency or pretends to be from a trusted company or person."
        )
        message["action"] = "We recommend not proceeding. Do not sign in, share personal information, or continue unless you can verify the link through a trusted source." if severity in {"HIGH_RISK", "CRITICAL"} else message["action"]
        message["shortBody"] = "High-risk phishing link detected. Do not sign in or share sensitive information." if severity in {"HIGH_RISK", "CRITICAL"} else message["shortBody"]
    elif top_threat == "MALWARE":
        message["body"] = "This link may expose your device to malicious software. Do not download files, install apps, or allow browser prompts from this site."
    elif top_threat == "UNWANTED_SOFTWARE":
        message["body"] = "This link may lead to deceptive or unwanted software. Avoid downloads, fake update prompts, and pop-ups asking for permissions."
    return message
