"""Fixed existing consumer contracts; no caller-selected account or partition."""
from dataclasses import dataclass
import re

FAMILIES = {
    'url_check': ('/v1/url-checks', ('full_url', 'origin_only'), 'urlTransportVersion',
        ('1.0.0-candidate.1', '1.0.0-candidate.2'), '1.0.0-candidate.1'),
    'message_check': ('/v1/message-checks', ('sanitized_message',), 'messageTransportVersion',
        ('1.0.0-message-candidate.1', '1.0.0-message-candidate.2', '1.0.0-message-candidate.3'),
        '1.0.0-message-candidate.1'),
    'recovery_clarification': ('/v1/recovery-clarifications', ('recovery_clarification',),
        'recoveryTransportVersion', ('1.0.0-recovery-candidate.1',), None),
}


@dataclass(frozen=True)
class ReceiptBinding:
    family: str
    client_check_id: str
    transport_version: str

    def contract(self):
        if (type(self.family) is not str or self.family not in FAMILIES
                or type(self.client_check_id) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', self.client_check_id)
                or type(self.transport_version) is not str or self.transport_version not in FAMILIES[self.family][3]):
            raise ValueError('OPERATIONS_BINDING_INVALID')
        return FAMILIES[self.family]

    def matches(self, row):
        _, scopes, field, _, fallback = self.contract()
        return (row.get('clientCheckId') == self.client_check_id and row.get('projectionScope') in scopes
                and row.get(field, fallback) == self.transport_version)
