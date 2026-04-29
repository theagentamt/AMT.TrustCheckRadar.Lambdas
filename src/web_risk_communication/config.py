import os

TABLE_NAME = os.environ.get("WEB_RISK_TABLE_NAME") or os.environ.get("TABLE_NAME") or os.environ["USERS_TABLE_NAME"]
SECRET_NAME = os.environ["WEB_RISK_SECRET_NAME"]
SECRET_FIELD = os.environ.get("WEB_RISK_SECRET_FIELD", "apiKey")
WEB_RISK_ENDPOINT = os.environ.get("WEB_RISK_ENDPOINT", "https://webrisk.googleapis.com/v1eap1:evaluateUri")
THREAT_TYPES = ["SOCIAL_ENGINEERING", "MALWARE", "UNWANTED_SOFTWARE"]

CONFIDENCE_SCORES = {
    "SAFE": 0,
    "LOW": 15,
    "MEDIUM": 35,
    "HIGH": 55,
    "HIGHER": 75,
    "VERY_HIGH": 90,
    "EXTREMELY_HIGH": 100,
}

FULL_URL_TTL_SECONDS = {
    "SAFE": 24 * 60 * 60,
    "LOW": 6 * 60 * 60,
    "MEDIUM": 60 * 60,
    "HIGH": 30 * 60,
    "HIGHER": 15 * 60,
    "VERY_HIGH": 15 * 60,
    "EXTREMELY_HIGH": 15 * 60,
}

DOMAIN_TTL_SECONDS = {
    "SAFE": 7 * 24 * 60 * 60,
    "LOW": 24 * 60 * 60,
    "MEDIUM": 6 * 60 * 60,
    "HIGH": 2 * 60 * 60,
    "HIGHER": 60 * 60,
    "VERY_HIGH": 60 * 60,
    "EXTREMELY_HIGH": 60 * 60,
}

THREAT_MULTIPLIERS = {
    "SOCIAL_ENGINEERING": 1.15,
    "MALWARE": 1.10,
    "UNWANTED_SOFTWARE": 0.85,
}

SOURCE_MULTIPLIERS = {
    "full_url": 1.00,
    "domain": 0.60,
}

SEVERITY_ORDER = ["LOW", "GUARDED", "SUSPICIOUS", "HIGH_RISK", "CRITICAL"]
SEVERITY_MINIMUM_SCORES = {
    "LOW": 0,
    "GUARDED": 15,
    "SUSPICIOUS": 35,
    "HIGH_RISK": 65,
    "CRITICAL": 85,
}

SEVERITY_MESSAGES = {
    "LOW": {
        "title": "No strong risk detected",
        "body": "We did not detect strong signs that this link is malicious. That said, no automated check can guarantee a link is completely safe.",
        "action": "Proceed only if you trust the sender and were expecting this link.",
        "shortBody": "No strong risk detected. Still verify the sender before continuing.",
        "recommendation": "PROCEED_WITH_CAUTION",
    },
    "GUARDED": {
        "title": "Use caution with this link",
        "body": "This link shows some warning signs, but not enough to strongly classify it as malicious. Be careful before opening it or sharing any personal information.",
        "action": "Only continue if you can independently verify the sender or website.",
        "shortBody": "Some warning signs detected. Proceed carefully and verify first.",
        "recommendation": "VERIFY_BEFORE_PROCEEDING",
    },
    "SUSPICIOUS": {
        "title": "This link looks suspicious",
        "body": "This link has multiple indicators associated with scams, phishing, or deceptive activity. While this is not a confirmed block-level result, we recommend treating it as unsafe until verified.",
        "action": "Do not enter passwords, verification codes, payment details, or personal information unless you confirm the link is legitimate through another trusted source.",
        "shortBody": "This link looks suspicious. Avoid entering personal or payment information.",
        "recommendation": "DO_NOT_ENTER_SENSITIVE_INFORMATION",
    },
    "HIGH_RISK": {
        "title": "High-risk link detected",
        "body": "This link appears strongly associated with phishing, malware, or other malicious behavior. Opening it or interacting with it could put your account, device, or personal information at risk.",
        "action": "We recommend not proceeding. Do not log in, download files, send money, or share sensitive information.",
        "shortBody": "High-risk link detected. Do not proceed.",
        "recommendation": "ADVISE_AGAINST_PROCEEDING",
    },
    "CRITICAL": {
        "title": "Do not proceed",
        "body": "This link is highly likely to be malicious. It may be used to steal passwords, collect payment information, install harmful software, or impersonate a trusted service.",
        "action": "Do not open the link. Do not enter information, download anything, or continue to the site.",
        "shortBody": "This link is highly likely malicious. Do not open it.",
        "recommendation": "DO_NOT_PROCEED",
    },
}
