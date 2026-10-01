"""Finite, whole-message phrase families; not a general language classifier.

Every slot is a closed literal choice. No wildcard captures, user-controlled
expansions, sentence extraction, semantic model vote or substring verdicts.
"""
from itertools import product

COVERAGE_VERSION = 'qualified-phrases-2026-09-21-v1'

# Each family is (rule, sequential slots). Qualification covers the Cartesian
# product of the explicit slots, not arbitrary text with similar meaning.
FAMILIES = {
    'en': (
        ('DEMAND_GIFT_CARD_PAYMENT', (
            ('', 'please '),
            ('pay this tax fee', 'pay this debt', 'pay this fine', 'pay this utility bill'),
            (' by buying gift cards', ' by purchasing gift cards'),
            (' and send me the numbers and pins.', ' and send us the redemption codes.'),
        )),
        ('REQUEST_SECRET_DISCLOSURE', (
            ('', 'please '),
            ('send me', 'send us', 'give me'),
            (' your account password.', ' your login code.', ' your one-time login code.',
             ' your account password and the login code.'),
        )),
        ('PAYMENT_WITH_SECRECY_PRESSURE', (
            ('', 'please '),
            ('transfer the money', 'send the payment'),
            (' and do not check with anyone.', ' and do not tell your family.',
             ' and do not verify this request with anyone.'),
        )),
    ),
    'es': (
        ('DEMAND_GIFT_CARD_PAYMENT', (
            ('', 'por favor, '),
            ('pague esta tasa', 'pague esta deuda', 'pague esta multa', 'pague esta factura'),
            (' con tarjetas de regalo', ' comprando tarjetas de regalo'),
            (' y envíeme los números y pin.', ' y envíenos los códigos de canje.'),
        )),
        ('REQUEST_SECRET_DISCLOSURE', (
            ('', 'por favor, '),
            ('envíeme', 'envíenos', 'deme'),
            (' la contraseña de su cuenta.', ' el código de acceso de su cuenta.',
             ' el código de acceso de un solo uso de su cuenta.',
             ' la contraseña de su cuenta y el código de acceso.'),
        )),
        ('PAYMENT_WITH_SECRECY_PRESSURE', (
            ('', 'por favor, '),
            ('transfiera el dinero', 'envíe el pago'),
            (' y no lo consulte con nadie.', ' y no se lo diga a su familia.',
             ' y no verifique esta solicitud con nadie.'),
        )),
    ),
}


def build_coverage():
    result = {}
    for language, families in FAMILIES.items():
        rows = {}
        for rule, slots in families:
            for values in product(*slots):
                phrase = ''.join(values)
                if phrase in rows:
                    raise ValueError('Duplicate qualified phrase')
                rows[phrase] = rule
        result[language] = rows
    return result


QUALIFIED_PHRASES = build_coverage()


def match(text, language):
    """Whitespace/case normalization only; all other surrounding text rejects."""
    return QUALIFIED_PHRASES.get(language, {}).get(' '.join(text.casefold().split()))
