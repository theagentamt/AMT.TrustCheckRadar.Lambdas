# SECUR4ALL-225 Lambda Evidence

Status: **safe disabled implementation complete; story activation pending**

Implemented Lambda scope:

- Server acceptance sequence and generation authorization are persisted with
  the analysis processing record.
- History content and the durable request locator join the existing atomic
  quota/completion transaction.
- `RESULT_READY` recovery reuses the persisted authorization and response.
- Durable same-ID replay returns the original retained assessment without a
  second scan charge; a different payload conflicts.
- Deleted, cleared, expired, or account-erased durable locators return
  `RESULT_UNAVAILABLE` and cannot fall through to model processing.
- Clear/reset generation races are condition-checked at completion.
- Recognition is independent from History deletion and reset counts only
  completions accepted in the current recognition generation.
- Badge thresholds are constrained to 1, 5, and 20 and have no entitlement or
  quota side effects.
- Lambda activation rejects durable-locator retention shorter than History
  retention plus the 24-hour cleanup allowance. Boundary tests reject 89 and 90
  days and accept 91 days as the exact minimum, without selecting a deployment
  default.

Privacy evidence: the content builder copies only the approved assessment,
source type, server times, and control metadata. Sanitized input text, entities,
OCR values, images, attachments, and unknown response fields are rejected or
not copied.

Automated evidence is in
`tests/conversation_analysis/test_history_completion.py`, the existing analysis
replay/atomic-commit suite, and `tests/shared_history`.

The story must remain open until dedup/tombstone retention, pre-activation replay
handling, stable badge identifiers/localization/qualification, new-ID duplicate
policy, account-state bootstrap, and Dev integration evidence are approved.
