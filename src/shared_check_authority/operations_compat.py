"""Typed optional admission metadata for the existing paid-closure boundary."""
from .core import AuthorityError, integral

HISTORY_FIELDS = {'governedHistoryAcceptedAtEpoch', 'governedHistorySourceType', 'governedHistoryResultType'}
OPERATIONS_FIELDS = {'operationsReceipt', 'operationsProjection'}


def pending_metadata(row, *, now):
    try:
        present = HISTORY_FIELDS & set(row)
        accepted = None
        if present:
            accepted = integral(row.get('governedHistoryAcceptedAtEpoch'))
            message = row.get('messageTransportVersion') == '1.0.0-message-candidate.3'
            url = row.get('urlTransportVersion') == '1.0.0-candidate.2'
            source = row.get('governedHistorySourceType')
            if (present != HISTORY_FIELDS or message == url or accepted is None
                    or not 0 <= accepted <= min(now, row['settleByEpoch'])
                    or row.get('governedHistoryResultType') != ('message' if message else 'url')
                    or message and (row['projectionScope'] != 'sanitized_message' or source not in ('pasted_text', 'ocr', 'mixed'))
                    or url and (row['projectionScope'] not in ('full_url', 'origin_only') or source not in ('standalone_url', 'message_url', 'qr_url'))):
                raise ValueError()
        metadata = None
        if 'operationsReceipt' in row:
            from shared_operations import retirement_metadata
            metadata = retirement_metadata(row)
            service = {'sanitized_message': 'message_check', 'recovery_clarification': 'recovery_clarification'}.get(row['projectionScope'], 'url_check')
            if (metadata['accessClass'] != 'paid' or metadata['service'] != service
                    or not metadata['acceptedAtEpoch'] <= min(now, row['settleByEpoch'])
                    or metadata['expiresAtEpoch'] > row['retentionDeadlineEpoch']
                    or accepted is not None and (metadata['acceptedAtEpoch'] != accepted
                        or metadata['inputKind'] != row['governedHistorySourceType'])):
                raise ValueError()
        if 'operationsProjection' in row:
            from shared_operations.projection import validate_capture
            captured = validate_capture(row['operationsProjection'])
            if captured['sourceState'] != 'ADMITTED':
                raise ValueError()
            if metadata is None:
                # Only an already-expired orphan may be erased; never restore
                # metadata from a stored snapshot or trust it as new authority.
                if captured['reference']['expiresAtEpoch'] > now:
                    raise ValueError()
            elif captured['reference'] != metadata:
                raise ValueError()
        return metadata
    except Exception:
        raise AuthorityError('PURCHASE_USAGE_RECONCILIATION_REQUIRED') from None
