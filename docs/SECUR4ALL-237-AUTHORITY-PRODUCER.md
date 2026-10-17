# SEC237 authority-bound check facts

This adds an optional source producer inside the existing authority admission
and settlement transactions, plus a content-free projection helper in
`shared_operations`. It follows the accepted operations event schema used by
the [kernel](SECUR4ALL-237-OPERATIONS-KERNEL.md). It creates no handler, endpoint,
notification, second subject locator or standalone linkable operations store.
The runtime setting `OPERATIONS_PRODUCER_ENABLED` defaults false and rejects
unknown Boolean text. No deployed configuration is changed or enabled here.
SEC237/243 remain open.

## Source identity and accounting

Only a new admitted receipt receives one random `logicalCheckRef` and two random
accepted/terminal event identities. They use server cryptographic randomness,
not subject, client/check/receipt IDs, payload or URL hashes. The references sit
on the existing account-owned receipt in the same transaction that reserves
capacity. Preparation, rejection and client intake create no accepted fact.
Admission replay and a lost admission response reuse the committed receipt;
they cannot mint a second counted check or reservation. Public receipt fields
remain unchanged and do not disclose the operations references.

The minimized metadata contains fixed environment/service/input kind/access
class and acceptance/deadline fields. Input kind comes from the already
validated declared source, preserving QR, message/URL and server clarification;
missing/unknown admitted source is rejected, not guessed from content. The
built-in local recovery selector never admits a check and stays outside this
producer. No raw input, outcome narrative, fingerprint, account/receipt key,
client identity, provider response or recipient is copied into events.

Settlement retains a finite reason in the same transaction as the authoritative
outcome and deduction. An equality condition binds the exact captured reference
metadata, so a change between read and transaction cannot associate the ledger
with a swapped reference. The normal account/deletion, execution-owner, policy,
lease and accounting conditions remain present. The projection reads only an
actually committed SETTLED row before producing terminal accounting; processing
complete is separate from charge (paid/trial complete1, complimentary complete0,
all other outcomes0). Replayed settlement preserves event identities and facts.

The existing trusted-worker method accepts an optional fixed reason argument;
no public API, client JSON or exception-string parser is added. Complete,
inconclusive, blocked, invalid and unsupported have unique source outcome
reasons. Expired recovery proves failed/zero/OPERATION_EXPIRED. Partial, failed
and unavailable can have multiple causes; an omitted cause is stored as unknown
and projects `TERMINAL_REASON_NOT_RECORDED`, with accepted-only facts. It does
not invent a provider failure or SERVICE_DISABLED classification. Future
component adapters must supply their actual fixed cause before complete
operational coverage can be claimed.

Both recovery writers are covered: the authenticated authority method and the
separate HMAC-only lease worker. The latter stamps its actual assessment time
and known expiry reason alongside accounting, only for an already-existing,
unexpired operations reference, with the same captured-metadata equality fence.
It mints no new reference. Its existing `expired_` receipt-ID form is accepted
only with failed/OPERATION_EXPIRED, and is never copied into an event. Without
operations metadata, that worker's row/transaction/clock behavior is compared
exactly with pinned release and remains unchanged.

## Separate seven-day operations deadline

The operations deadline is fixed at the earlier of the original receipt
retention deadline and acceptance plus7days. Settlement, replay, projection and
History cannot extend it. Governed History may retain the same receipt until
seven days after settlement; the operations projection still refuses references
at its original acceptance deadline. Expired and absent metadata return explicit
EXPIRED/NOT_RECORDED coverage, never a healthy zero. Malformed or inconsistent
state/accounting/source metadata fails with one finite code.

**Physical privacy enforcement remains an activation blocker.** The logical
projection fence does not remove the references from a longer-lived History
receipt. Dedicated removal, deletion/restore integration and backup enforcement
still need implementation and tests before this flag may be enabled. The
ordinary expiry sweep must not delete a still-valid History record merely to
remove operations fields. No additional retention, indefinite map, tombstone,
PITR or resource permission is approved by this code. No producer flag or actor
scope is enabled in this increment.

## Validation and preserved behavior

Local tests exercise actual Moto authority transactions, lost-response replay,
source reference immutability, paid/trial/complimentary deductions, ambiguous
reasons, failed creation, metadata swap, lease recovery and History's different
retention deadline. Generated facts are validated against the existing event
schema. Default-off row/transaction/ticking-clock comparisons against pinned
release remain exact. The auth/account/transaction fence AST and billing,
prepare-auth diagnostics, ownership and publisher sources remain protected.

The reconciliation test also accounts for PR139's deliberately added public
`validate_revision` wrapper: its exact AST is allowed while every original
planner statement remains identical to the earlier pinned baseline. This fixes
a stale whole-file assertion exposed by the combined local suite; the planner
is not rewritten and no expectation is relaxed for other code.

Authority archives include only the small standard-library `shared_operations`
module. The operations reducer/store remains outside the Lambda package
registry. Local skip-dependency archives and host imports are inspection only,
not native ARM64, distribution, IAM or live behavior qualification. Companion
JSON and independent reviews retain the exact tests, hashes and source findings.

Remaining implementation includes durable pre-dispatch attempt facts and
provider-finish/cost coverage, atomic receipt+deletion/expiry-fenced ingestion,
physical reference cleanup, pending daily corrections, alert/delivery state
machines and actual report/alarm/recovery/failure evidence. The current helper
accepts an in-process receipt dictionary; it is not an authentication or
attestation API, and invoking it on fabricated input proves nothing about an
actual authority. Producer coverage in the report remains `not_qualified`.
Resources/IAM/scheduling/cost review and actual Dev qualification remain
SEC243; assembled release stays SEC180, physical-only cases ATCR148. No real
AWS/auth/service/API, provider, emulator, purchase, deployment or main action
occurs in this increment.
