# History mutation request validation

This is a bounded source increment for SECUR4ALL-226 in workstream S10. The
existing History v1 contract requires numeric `schemaVersion: 1`. The bootstrap
and mutation parsers accepted JSON `true` because Python compares `True == 1`.
An authenticated, correctly bound request could therefore pass an invalid
version to bootstrap, delete-one, clear-History, reset-progress or History-account
deletion. This is a source/contract finding, not an observed deployed incident.

Both parsers now reject booleans explicitly. Valid JSON numeric `1` and `1.0`
remain accepted, matching the existing JSON Schema `const: 1` semantics. No
contract, route, error code, authorization predicate, generation, receipt,
retention setting, transaction or deployment configuration changes.

The new regression suite uses the retained schema as its oracle and exercises
the actual handler for all five routes. Invalid versions return the existing
400 `INVALID_REQUEST` envelope without a service mutation call. Valid numeric
versions still dispatch once to the expected method with the authenticated
account. Auth/active-account/binding and SDK access are test substitutes; these
tests do not establish deployed authorization or service behavior.

Validation counts, source pins, package limits and independent review are in the
companion JSON and review record. The source-only inspection ZIP excludes
dependencies; it is not a qualified deployable artifact. No AWS read, endpoint,
actor/session operation, runtime mutation, activation or mobile operation was
performed for this fix.

## Scope and remaining work

SECUR4ALL-226 stays open. This request-validation fix does not implement the
governed History mutation/recognition bridge. The current governed contract is
GET-only and does not grant legacy mutations authority over governed receipts.
Its migration/capability contract, complete mutation/deletion/recognition and
target-environment acceptance remain separate. Physical-device cases stay with
ATCR-148; they are not passes and do not block this independent source fix.

## Retention recommendation and workstream disposition

The existing SEC178 `1.0.0-policy.1` already permits approved linkable operational
reconciliation facts for **at most seven days from acceptance**, fixed-code
diagnostics for fourteen days and genuinely unlinkable aggregate revisions for
ninety days. No new approval of the seven-day ceiling, reporting timezone,
recipient or caps is requested. Read this with the existing receipt-local
retirement/deletion rules; it does not approve extra copies by implication.

A selected-account-to-generation join is linkable even when the generation is a
random UUID. A new selected-account report store, discovery map or backup needs
its own technical provenance, deduplication, retirement and deletion proof. A
random identifier and a TTL alone do not establish unlinkability or physical
erasure. The minimum safe V1 path keeps selected Dev validation noncommitting,
uses existing approved receipt facts and fences, and preserves the environment
report's environment-wide meaning. The selected aggregate path remains refused.

The remaining work is technical when it implements those already approved
purposes within their existing limits. A new owner decision is required only if
the product asks for a persistent per-account/selected-test report, a different
report meaning or retained fields/purpose/linkage beyond the existing approval.
No such policy change is selected here. Aggregation and SECUR4ALL-198 policy work
remain held; this independent existing-contract fix proceeds instead of adding
another reporting abstraction. SECUR4ALL-237 remains In Progress.
