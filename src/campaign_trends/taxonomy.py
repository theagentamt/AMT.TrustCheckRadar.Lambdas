LABELS = {
    "advance_fee": {"en": "Advance-fee scam", "es": "Estafa de pago por adelantado"},
    "credential_theft": {"en": "Credential theft", "es": "Robo de credenciales"},
    "impersonation": {"en": "Impersonation", "es": "Suplantación de identidad"},
    "investment": {"en": "Investment scam", "es": "Estafa de inversión"},
    "romance": {"en": "Romance scam", "es": "Estafa romántica"},
    "employment": {"en": "Employment scam", "es": "Estafa de empleo"},
    "marketplace": {"en": "Marketplace scam", "es": "Estafa de compraventa"},
    "extortion": {"en": "Extortion", "es": "Extorsión"},
    "tech_support": {"en": "Tech-support scam", "es": "Estafa de soporte técnico"},
    "other": {"en": "Other scam pattern", "es": "Otro patrón de estafa"},
    "unknown": {"en": "Unknown scam pattern", "es": "Patrón de estafa desconocido"},
}


def label(identifier, locale):
    return LABELS.get(identifier, {"en": identifier, "es": identifier}).get(locale, identifier)
