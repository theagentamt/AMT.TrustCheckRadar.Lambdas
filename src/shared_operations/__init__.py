"""Content-free authority receipt projection; not an ingestion authority."""
from .receipts import admission_fields, settlement_fields, receipt_events, retirement_metadata, consumer_terminal_reason

__all__ = ["admission_fields", "settlement_fields", "receipt_events", "retirement_metadata", "consumer_terminal_reason"]
