"""Nonpersonal, configuration-owned report population provenance.

The generation is a random UUIDv4 assigned once to a reviewed source definition,
not an owner ID/hash or a per-run retry token. A selected-account mapping can
still make it linkable; selected_dev persistence is intentionally refused.
This validates the closed contract,
not configuration authenticity: trusted deployment wiring must hold selection
fixed for that generation. No endpoint accepts this object or creates scopes.
"""
import re

SUMMARY_V2 = '1.0.0-operations-summary-candidate.2'
REVISION_V2 = '1.0.0-operations-revision-candidate.2'
GENERATION = r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}'
COVERAGE = {'environment_customer': 'environment_completeness_unqualified',
            'selected_dev': 'selected_subset_not_environment_total'}
SCOPE_SCHEMA = {'type': 'object', 'additionalProperties': False,
    'required': ['schemaVersion', 'population', 'generation', 'sourceContract', 'coverage'],
    'properties': {'schemaVersion': {'const': 1},
        'population': {'enum': list(COVERAGE)},
        'generation': {'type': 'string', 'pattern': '^' + GENERATION + '$'},
        'sourceContract': {'const': 'authority-receipt-capture-v2'},
        'coverage': {'enum': list(COVERAGE.values())}}}


class ScopeFault(ValueError):
    def __init__(self):
        super().__init__('INVALID_REPORT_SCOPE')


def validate_scope(value, *, environment):
    try:
        if type(value) is not dict or len(value) != 5:
            raise ValueError()
        # Closed flat primitive snapshot; no caller-defined deep-copy execution.
        value = value.copy()
        if (set(value) != set(SCOPE_SCHEMA['required'])
                or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
                or type(value['population']) is not str or value['population'] not in COVERAGE
                or type(value['generation']) is not str or len(value['generation']) != 36 or re.fullmatch(GENERATION, value['generation']) is None
                or type(value['sourceContract']) is not str or value['sourceContract'] != 'authority-receipt-capture-v2'
                or type(value['coverage']) is not str or value['coverage'] != COVERAGE[value['population']]
                or type(environment) is not str or environment not in ('dev', 'uat', 'prod')
                or value['population'] == 'selected_dev' and environment != 'dev'):
            raise ValueError()
        return value
    except Exception:
        raise ScopeFault() from None
