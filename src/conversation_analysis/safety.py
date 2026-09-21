import re

from errors import AppError


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
        r"ignora\s+(todas\s+)?(las\s+)?instrucciones\s+(anteriores|previas)",
        r"eres\s+chatgpt",
        r"act[uú]a\s+como",
        r"mensaje\s+del\s+(sistema|desarrollador)",
        r"devuelve\s+exactamente",
    ]
]


def is_instruction_style_abuse(sanitized_text: str) -> bool:
    matches = sum(1 for pattern in INSTRUCTION_STYLE_PATTERNS if pattern.search(sanitized_text))
    return matches >= 2


def reject_instruction_style_input(sanitized_text: str) -> None:
    """A stop is an error, never a low-risk result or a fresh charge.

    Historical requests may already have been charged. The legacy error envelope
    deliberately makes no accounting/refund claim. This heuristic is not a full
    injection detector and is not evidence that the submitting person is abusive.
    """
    if is_instruction_style_abuse(sanitized_text):
        raise AppError(
            "HOSTILE_INPUT_STOP",
            "Analysis stopped because instructions in the content could interfere with the check.",
            retryable=False,
        )
