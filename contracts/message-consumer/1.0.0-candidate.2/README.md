# Message transport candidate.2

Transport `1.0.0-message-candidate.2`; outcome schemaVersion2 and policy `message-ai-2026-09-21-v1`. Candidate.1 stays immutable and reconciles under its original version. Only the client-selected transport version changes on input; target/input fields, routes, account/device gates and proof shape stay the same. The authority binds version and policy into the message HMAC and stored minimized receipt. Cross-version submit/reconcile fails closed.

New outcome fields: assessmentBasis (distinct list qualified_rules/google_web_risk_lookup/ai_assessment), aiAssessmentStatus (not_assessed/warning/no_warning/abstained/unavailable), aiReasonCodes (five closed AI categories). AI warning can be suspicious, never independently high risk. Independent rules/evidence retain precedence. Warning/no-warning can be complete only when all required stages and qualification gates pass. Withheld links prevent completion; current checked-link matches remain partial until separate joint-stage qualification.

New copy is owner-approved. Show AI label whenever basis includes ai_assessment and show withheld-link copy when WITHHELD_LINKS exists. Never render free model output. message.ai_inconclusive says no deduction and is permitted only in an authoritatively settled zero-charge envelope. Pending/unknown uses existing accounting messages, never that copy.

Persist only minimal proof/account/check/version/expiry recovery metadata, not content, spans or provider output. Legacy unversioned recovery means candidate.1; never silently upgrade receipts. Refresh must preserve independent evidence with its warning. Unsupported versions fail closed. The schema and fixtures are implementation contracts, not measured AI accuracy or activation approval.
