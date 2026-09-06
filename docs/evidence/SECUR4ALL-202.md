# SECUR4ALL-202 Lambda Evidence

Status: **No standalone Lambda; approved decisions consumed, external handoffs open**

This architecture story does not map to a deployable Lambda artifact. Its approved
V1 decisions are enforced across the Lambda implementation:

- fixed 14-day UTC period IDs and seven-day recovery;
- environment/period-specific KMS HMAC contributor tokens;
- explicit consent with fail-closed default;
- maximum 72-hour observations, 21-day transient state, and 400-day aggregates;
- minimum 10-contributor publication threshold;
- one centroid vector and three counted submissions per contributor/campaign/period;
- approved count bands and suppression of invalid/small cells;
- stable language-neutral taxonomy IDs with English/Spanish labels and safe unknowns;
- no automatic publication and separately authorized audited review;
- opaque environment-bound queue envelopes and content-free operational logging.

Evidence is distributed by implementation story under `docs/evidence/` and is
reproduced together by `make campaign-evidence`.

The architecture itself still records three external approval handoffs:
authoritative schemas/taxonomy (`SECUR4ALL-213`), approved model calibration
(`SECUR4ALL-214`), and privacy/security approval (`SECUR4ALL-215`). This Lambda
repository cannot accurately mark those approvals complete.
