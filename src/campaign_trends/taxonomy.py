LABELS = {
    "advance_fee": {"en": "Advance-fee scam", "es": "Estafa de pago por adelantado"},
    "credential_theft": {"en": "Credential theft", "es": "Robo de credenciales"},
    "impersonation": {"en": "Impersonation", "es": "Suplantación de identidad"},
    "investment": {"en": "Investment scam", "es": "Estafa de inversión"},
    "other": {"en": "Other scam pattern", "es": "Otro patrón de estafa"},
    "unknown": {"en": "Unknown scam pattern", "es": "Patrón de estafa desconocido"},
}


def label(identifier, locale):
    return LABELS.get(identifier, {"en": identifier, "es": identifier}).get(locale, identifier)
