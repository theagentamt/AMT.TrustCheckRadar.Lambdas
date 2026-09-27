# Authoritative period work and retirement candidate

SECUR4ALL-207 remains in progress. This source establishes strict, default-disabled
work accounting and proof-bound key retirement. It does not create production
approval markers, initialize controls, enable research, or approve historical
coverage. The separate withdrawal fixture exercises real producer/cleanup calls
with injected queue delivery; it does not qualify native queue redrive.

Each qualified writer transaction allocates a fixed-width ordinal under an exact
period control CAS and writes both `PERIOD_WORK#n/WORK#ordinal` and a deterministic,
domain-separated `WORK_LOOKUP#digest/RECORD`. The lookup is verified against the
work row's exact target identity. These records are paired discovery metadata,
not anonymous logs: their deadline is the target's original logical deadline.
Shortening is atomic across the target and both records; extension is refused.
Cleanup atomically deletes the exact target, both records and decrements the
count once. A target removed by TTL is reconciled through the remaining work
record. There is no independent work-record TTL or automatic legacy adoption.

`PERIOD_WORK_CONTROL#n/STATE` must already exist under separately qualified
bootstrap. Ordinary writers cannot initialize missing controls. Its monotonic
ordinal, count and pass fields are strict and bounded. Work schema version 1
requires admission registry schema version 2 with work manifest/revision pins;
old registry shapes cannot silently admit modern writes. The exact pipeline and
outbox TableIds are checked against runtime pins and the externally approved
work manifest. A restored table requires a new qualified generation.

The retirement helper requires its own `CAMPAIGN_PERIOD_RETIREMENT_ENABLED=true`
before any SDK call. It requires an exact SEALED registry, zero authoritative
control count, a strongly empty entire work partition, current table identities
and the exact work marker. The intent is durably recorded before disabling the
exact tagged HMAC key. Only DescribeKey, ListResourceTags, DisableKey and
ScheduleKeyDeletion with seven days are used. Missing proof/key, wrong metadata,
changed generation, unsupported shapes or an exhausted SDK budget refuse work.
Ambiguous acknowledgments are reconciled from fresh persisted/provider state;
no EnableKey, CancelKeyDeletion, key creation or automatic compensation exists.
A scheduled deletion is not destruction. A later NotFound is acknowledged only
with the stored scheduled proof and after its recorded deletion date.

The isolated retirement fixture uses two independently provisioned, run-specific
empty tables and one separately described/tagged HMAC key. Its SEALED marker and
control are synthetic assumptions, not evidence that a production period was
erased. Local tests use real SDK/Moto transactions and injected KMS responses.
Root owns any separately reviewed AWS execution and cleanup.

The all-writer scheduler, stable sealing, retired-prefix consumers, outbox/export
compatibility and deployment qualification are being integrated separately.
No source-only helper or zero counter establishes completion of the full story.
Current deployed code and flags remain separate from this candidate. The existing
21-day transient maximum and period-end-plus-seven-day recovery maximum are not
extended. Aggregate and audit retention are unchanged. SECUR4ALL-330 owns later
release/UAT evidence; SECUR4ALL-245 owns native restore/reopening qualification.
