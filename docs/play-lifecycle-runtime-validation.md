# Closed lifecycle runtime validation

This increment adds authenticated RTDN refresh, scheduled proof/ack recovery, explicit account preparation for interrupted first purchases, token/account-mapping cleanup, and optional metadata export. It integrates independently reviewed shortening accounting (original helper ae0bccf6d844a46c0ce76133f38b0b62686e78ba) without changing the existing purchase handoff candidate.1 or export candidate.2 bytes.

Local Python 3.14.7 evidence:

- Affected real-SDK/Moto suite: 564 passed,18 subtests (shared authority, lifecycle, handoff, export, finalizer and account-data suites). After final canonical-checkpoint/erasure/contract refinements, the entire lifecycle suite passed78 tests, including the added preservation regressions.
- Final ordinary suite:1870 passed,232 subtests,31 explicitly skipped isolated SDK modules.
- New scoped publisher:14 synthetic cases passed (also included in ordinary count), covering exact13 scope, checksums, version conflicts, conditional puts and no runtime mutation.
- Compileall, shell syntax and git diff checks passed. New preparation and export candidate.3 fixtures validate against their schemas. Predecessor contract files are unchanged.
- Build composition exercised13 source-only packages and4 full Linux aarch64/Python3.14 dependency packages (new3 handlers and handoff). These are build checks before source freeze, not final publication hashes. Final source-pinned13 full archives, source-byte/dependency architecture checks and immutable S3 readback will be recorded after reviewed release integration. macOS cannot execute Linux native wheels; only an actual AWS closed-handler smoke can establish that runtime boundary.

Meaningful synthetic cases cover initial prepared-account recovery without fabricated user sessions; duplicate fresh acknowledgment recovery without allowance reset; deletion during external acknowledgment without account-data resurrection; strict shortened deadlines and original-period settlement; exhausted retries continuing hourly;55 due live/poison rows preceding an expired token across checkpointed invocations with providers disabled; canonical cursor-only metadata; account cursor removal;4 retained namespaces plus paired reverse-map erasure; command-race rollback; and token metadata export excluding credentials.

Independent review covers the owner-authored runtime/provider/cleanup/contract/publisher changes; root independently reviewed the accounting helper. Earlier review findings (preparation constructor, audience alignment, acknowledgment exhaustion, fair sweep and cursor key validation) are resolved. All runtime gates remain false by default. The five-minute pseudonymous operational-checkpoint exception still awaits owner approval and has a separate false policy gate.

No Google purchase, order or acknowledgment request was sent during validation. No Lambda deployment, provider activation, account mutation, inventory approval, physical-device test or whole-story completion is asserted. The remaining activation and supported-transition limits are in play-lifecycle-runtime-contract.md and purchase-shortening-accounting-closure.md.
