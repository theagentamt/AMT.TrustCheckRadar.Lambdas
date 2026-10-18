# Governed History delete/clear: design and preparation boundary

This increment implements an internal **non-dispatching preparation adapter** at
`src/governed_history/erasure_preparation.py`. It performs acknowledged strong
reads, validates the owned settled receipt and captures an immutable private
snapshot. It has no handler wiring, transaction method, mutation result or new
route. `PREPARED_NOT_AUTHORIZED` is a source-only summary, not a wire response.
No real deletion, deployment, runtime change or retained marker is performed.

The existing capability candidate and both existing wire contracts remain
unchanged. A legacy requestId may lexically contain a governed gh1 ID; a legacy
no-op COMPLETE receipt still does not erase a governed result. This adapter does
not route requests through that incompatible legacy mutation path. Current
Android generation zero is a local read shim, not a server erasure fence.

## Independently reviewed, implemented portion

The first design review explicitly allowed a preparation primitive only with
ownership, accounting, atomic-fence and privacy requirements. The final source
and handoff reviews accompany the validation record. No mutation dispatcher was
implemented ahead of review.

`prepare_receipt_erasure(authority, event, server_lookup_key, expected_result_id)`:

1. Reads current authenticated account/profile, absence of account deletion,
   active device pointer and binding, and complete verified HMAC-key inventory.
   Each read must be strongly consistent, acknowledged HTTP200 and match its
   requested key. Identity projections omit contact/credential attributes.
2. Derives the owned partition from the authenticated subject and retained key
   version. The reference must come from trusted server resolution; it is not an
   HTTP input contract. It nevertheless verifies the full check proof against
   the stored payload HMAC, exact PK/SK/checkId and canonical resultId. A resultId
   or client-supplied partition alone never grants ownership.
3. Reads the canonical receipt strongly and validates the exact captured bytes'
   normalized values, not a later mutable SDK response. It joins the governed
   digest/index/projection to actual receiptId, derived receipt HMAC, accounting,
   outcome, accepted/settled epochs, family/source, summary and original expiry.
   Missing, expired, removed, unknown-schema or inconsistent records are **not
   prepared**, never a previous-success acknowledgment.
4. Captures the minimized identity snapshots, full supported receipt snapshot,
   known absent optional fields and source capture time in a frozen opaque plan.
   Its repr and public summary contain finite constants only. Pickling/export is
   refused; there is no public SDK-action, execute or serialization method. It
   still contains inherently identifying material privately in memory and must
   never be logged, persisted or treated as a durable capability.

The exact intended selective removal is only `governedHistory`,
`governedHistoryDigest`, `GSI2PK`, `GSI2SK` and `resultSummary`. Accounting,
SETTLED state, receipt/proof identity, payload HMAC, execution dedup, access basis,
period counters, operations obligations, GSI1 and original TTL/deadline remain
unchanged. There is no paid-access prerequisite for removing an owned result.
Operations fields serve a separate purpose and are not implicitly erased; the
adapter recognizes the existing earlier operations cleanup index schedule.

The source plan makes no freshness guarantee. A future executor must freshly
prepare and authorize at dispatch, then atomically bind inventory revision and
key set, ACTIVE profile/age/subject, deletion absence, active pointer version and
fingerprint, ACTIVE owned device, and canonical receipt snapshot/deadline. It must
compare captured fields and absence of known optional fields. DynamoDB cannot
compare a whole top-level item: an unknown field inserted after preparation is
not fenced by that expression and must be preserved. Unknown fields at initial
preparation are rejected. This limitation forbids an all-copy erasure claim.

The tests' hypothetical CAS compiler is **test-only** and uses local Moto. It is
not a source dispatcher or approved commit protocol. It demonstrates atomic
rejection on stale identity/inventory/receipt/deadline and preservation of every
non-removed field, including an unknown later-added field. No future executor is
authorized by those fixtures.

## Delete/clear invariants and linearization design

These are engineering obligations, independent of recognition choices G1/G2.
The abstract fixtures are executable specifications, not implemented service
acceptance or a newly approved stored schema.

| Boundary | Required behavior |
| --- | --- |
| Owner | Authenticate server request context, resolve only owned canonical results across the complete verified key inventory. Never accept a client owner selector. Operation results are owner-scoped. |
| Identity change | Profile deletion/age state, device move/version and key-inventory changes must invalidate an uncommitted plan in the same transaction as any removal. No payment grant is required for History erasure. |
| Delete one | Target an immutable canonical result; remove its assessment copies and sparse History index while retaining accounting/dedup. Unrelated results, accounts, badges, quota and campaign data are unchanged. |
| Clear | Advance a server-owned **per-account History generation** at its linearization point; do not enumerate a snapshot and call it clear. Atomically capture/compare that generation at admission and settlement. No client timestamp orders these operations. |
| In-flight work | A pre-clear admission may settle accounting once, but its old generation cannot write History after clear. Settlement committed before clear is erased by clear; settlement after clear suppresses projection. Both orders converge. |
| New admission | A post-clear admission uses the new server generation. An admission racing clear must fail its stale CAS and reread, rather than silently inheriting a cached epoch. |
| Duplicate settlement | Preserve SETTLED accounting and never reconstruct an erased result from worker output, old responses or stale indexes. No read/retry extends retention. |
| Same operation retry | Owner + logical operation identity + immutable intent must return its original committed outcome, without advancing generation twice. Reusing the identity for a changed target/intent must conflict. |
| Concurrency | Delete/delete, delete/clear, clear/clear and settlement/clear need atomic ordering; independent blind read/write batches are insufficient. Other owners' pending completions remain unaffected. |
| Unknown commit | Timeout/read failure is not success, not a reason to create a new operation or rerun a destructive write. A reviewed same-operation receipt/readback contract must distinguish committed, absent and unresolved outcomes. |
| Recognition | Clear does not reset badges; badge reset does not clear History. Generation domains and dedup purposes must remain distinct, with no automatic historical backfill. |

Current source replay evidence is deliberately narrower: after a hypothetical
local removal, stale GSI2 candidates are suppressed by canonical reads; late
`settle()` and `reconcile()` retain SETTLED accounting and return no summary.
Existing message/URL envelope mapping uses generic SERVICE_UNAVAILABLE for a
SETTLED receipt without summary. This does **not** define an explicit erased
replay status or a final user experience. No invented public error code is added.

## Remaining T1/P1 implementation contracts

**T1:** specify compatible existing-route/version capability selection, owned
result/operation identity, server generation bootstrap/capture, atomic admission
and completion interaction, same-operation receipt/readback, erased replay and
Android draft/list/detail/cache invalidation. Preserve old clients explicitly;
do not infer a bridge from ID shape or add guessed routes/headers/payload fields.

**P1:** review the exact minimal per-account generation, per-result erasure and
operation-receipt records and their separate privacy purposes before storing
them. Candidate markers should contain only the ownership/intent/order/dedup
facts needed for the bounded replay/restore obligation, not assessment content,
free text, input fingerprints or contact details. Their expiry must cover the
reviewed retry/in-flight/backup restoration windows; no numeric lifetime is
selected here and legacy120/90/7-day retention is not adopted by analogy.

Before an erasure-success contract, inventory every assessment copy and join:
canonical summary and projection, stale GSI discovery, preparation/worker
messages, response/export caches, client caches and restored backups. Define
invalidate/purge/expiry behavior and how a restored pre-erasure backup is fenced
before it can serve or regenerate an erased result. If a separate operations or
accounting record must remain, state its distinct purpose and minimized fields;
do not relabel retaining it as deleting it. Account deletion remains the global
identity fence and must cover every retained HMAC partition and new marker.

The current plan provides no committed operation receipt, erasure marker,
generation store, physical purge, backup fence or success response. Full delete
and clear dispatch remain blocked by these exact engineering/lifecycle gaps,
not by mobile activation, physical devices or recognition owner decisions.

## Recognition owner bundle: held, not assumed

| Decision | Candidate recommendation | Impact until approved |
| --- | --- | --- |
| G1 | Count one future typed-complete logical check across permitted message/URL/QR sources, independent of risk/payment; exclude partial/inconclusive/blocked/failed/pending. Count deliberate new-ID submissions without retaining content fingerprints. Same-ID dedup is already settled. | Award eligibility/wiring remains held; delete/clear design proceeds. Risk classification is not proof of independently confirmed fraud. |
| G2 | Reuse existing checks_1/checks_5/checks_20 IDs, EN/ES catalog and progress fields, with no new per-result award/unlock timestamp fields. | Governed catalog/progress wiring remains held; existing legacy text is not approval. |

Already approved and not reopened: milestones1/5/20, no points, same-ID once,
clear/reset independence, server admission ordering, no quota/cash/campaign effect
and no automatic historical backfill. This is an impact/recommendation packet
for a later owner bundle, not a request to approve by silence.

## Validation and later target acceptance

Companion validation pins exact source, initial review finding/correction and
disjoint local test results. Existing History, settlement, replay, account
deletion, operations and contract suites are preserved. Two source-only ZIPs
check import/packaging closure; no complete deployed artifact is qualified.
Existing main-only CI already runs the governed History directory; no workflow,
dependency, configuration, IAM, route, permission, handler or flag changes.

SEC185/186/224/225/226 remain open. This is a preparation/design milestone only.
Future Dev completion needs implemented reviewed T1/P1 and its local atomic,
privacy, replay and lifecycle acceptance. Separately authorized target tests must
cover two disposable owners/devices, moved device, missing/deleted profile,
same-operation lost-ACK, races, all-copy erasure, original expiry and backup
restore before mobile invalidate/retry and integrated acceptance. Physical-only
cases remain linked to ATCR148; no physical hardware or paused iOS is needed for
current source work. Deployment, activation and release remain separate gates.
