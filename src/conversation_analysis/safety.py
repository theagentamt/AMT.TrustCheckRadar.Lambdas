import re


INSTRUCTION_STYLE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"disregard\s+(all\s+)?previous\s+instructions",
        r"you\s+are\s+chatgpt",
        r"act\s+as\s+",
        r"system\s+prompt",
        r"developer\s+message",
        r"override\s+instructions",
        r"do\s+not\s+analy[sz]e",
        r"return\s+exactly\s+",
        r"follow\s+these\s+instructions",
        r"jailbreak",
    ]
]


def is_instruction_style_abuse(sanitized_text: str) -> bool:
    matches = sum(1 for pattern in INSTRUCTION_STYLE_PATTERNS if pattern.search(sanitized_text))
    return matches >= 2


def build_safe_low_confidence_response() -> dict:
    return {
        "scamScore": 5,
        "riskLevel": "low",
        "confidence": 0.2,
        "summary": "The submitted content appears instruction-like or non-analyzable, so the backend returned a safe low-confidence result instead of trusting it as analysis input.",
        "signals": ["instruction_style_abuse_detected"],
        "recommendedActions": [
            "Review the original content carefully before taking action.",
            "Retry only with sanitized conversation content intended for scam analysis.",
        ],
    }
