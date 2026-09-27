# SECUR4ALL-200 retained Dev acceptance

This increment adds isolated qualification code, failure/retry tests and the
[POST/GET OpenAPI description](api/account-deletion-openapi.md). It changes no
production Lambda, deployed gate, retention policy or account. UAT execution is
tracked separately in [SECUR4ALL-329](https://andmorethings.youtrack.cloud/issue/SECUR4ALL-329)
(Backend V1-5 – Release qualification); this page does not claim UAT or release
readiness. Root owns tracker updates and isolated AWS execution.

## Composed restored-copy suppression

`handler_all_components_restore_quarantine` first runs all real receipt producers
and the finalizer with injected Cognito against twelve isolated SDK tables. Only
after genuine completion does it rehydrate representative synthetic pre-cleanup
rows from ten stores, including a copied ACTIVE profile, retained History content,
allowance data and token metadata. These are application-level stale copies from
different component stages, not a native PITR restore or historical customer data.

The original terminal fence, operation, all twelve receipts and their clocks are
retained. The actual History active-account guard and V1 account authorization
(shared with export) deny reads; an actual authority transaction using account and
inventory conditions rejects a delayed write; the real post-confirmation writer
rejects an old callback. The fixture does not execute encrypted export cursor or
provider verification paths. Copied and absent inventory fail the actual manifest
validator under independently changed expected revision/hash pins. With the old
terminal fence temporarily absent in the isolated ledger, actual account-deletion
admission still refuses copied/absent approval before mutation under those new
pins. The exact original fence is restored afterward.

This relies on the established restore quarantine procedure: restored resources
must not retain serving access with old runtime qualification pins. Runtime does
not infer a restore from arbitrary DynamoDB copies. A restored environment missing
both suppression and the quarantine/configuration boundary is not qualified by
this test. Positive reopening/requalification of a restored environment remains
SECUR4ALL-245 work.

A terminal campaign event cannot create fresh completion evidence. The fixture
asserts all restored rows, including an unknown recovery row, remain unchanged in
quarantine; it does **not** call them erased or reuse historical receipts as proof
of their erasure. Unrelated rows are unchanged.

## Same-email re-registration

Use the separate `cognito_qualification.lambda_handler`, initially with
`identity_complete`, in a newly provisioned disposable pool and twelve owned
fixture tables. The infrastructure operator verifies old-user absence and the
original terminal proof, then creates a new suppressed identity at exactly
`fixture-<runId>@example.invalid`. It records the intent/new subject before changing
only `QUALIFICATION_PREVIOUS_COGNITO_SUBJECT` and
`QUALIFICATION_COGNITO_SUBJECT`. Lambda has no AdminCreateUser permission.

Invoke `identity_reregister_same_email` with the existing fixed four-field event:
`schemaVersion=1`, `operation=qualify-account-deletion-identity`, the same `runId`
and that case. The second stage never resets tables. It verifies exact pool tags,
name/ARN, canonical distinct subject (including UUIDv7), expected email, old-user
absence and all twelve original owned receipts. The genuine profile writer creates
only the new subject's PENDING_AGE_GATE profile. No old History, tokens, purchase
ownership, trial or grant becomes attached. A delayed old profile event is rejected.
The original purchase-linked usage stays used=7/reserved=0 with its original clock.

This proves no **automatic** relinking. It does not perform Google restoration or
invent a purchase. Existing explicit verified-restore tests preserve remaining
usage: `tests/v1_play_handoff/test_service.py` includes
`test_deletion_then_restore_retains_used_releases_pending_and_supports_renewal`
and `test_new_owner_restore_waits_for_original_pending_reservation_cleanup`.
No live provider/charge is part of this fixture. Repeating stage2 cannot overwrite
an already created profile, and stage1 is refused when previous-subject mode is set.

## Local validation and package handoff

At source freeze: SDK/Moto campaign+Cognito suite 130 passed before the final two
Cognito cases; final Cognito suite 48 passed. Six additional simple-producer
committed-receipt acknowledgment-loss tests passed; entitlement lifecycle suite
7 passed (including new receipt-loss recovery), and two new Play token delete/final
receipt acknowledgment-loss cases passed. Tests preserve original operation and
receipt deadlines. OpenAPI slice validation: 76 tests + 56 subtests and OpenAPI3.1
validator 0.9.0 passed. These are local results, not new AWS acceptance.

Build only from the reviewed clean exact commit:

```sh
python scripts/build_campaign_qualification.py --source-sha <exact-40-hex> --output-dir /tmp/sec200-restore
python scripts/build_campaign_qualification.py --source-sha <exact-40-hex> --output-dir /tmp/sec200-identity --real-cognito
```

The builder preserves four production campaign ZIPs separately and verifies all
fixture Python bytes against Git. Additional privacy/profile code is fixture-only.
Root must independently verify each archive/manifest, provision bounded owned
resources and invoke only the new restore case plus the required two identity
stages. No broad rerun of already accepted AWS cases is required. Cleanup must
remove the owned pool (including new subject), tables/function/role and schedule
fixture HMAC key deletion under the existing procedure. Never use production or
existing customer identities. Record actual source/archive/runtime, outcomes,
residual quarantine scope and cleanup evidence before Dev closure. No AWS result
is claimed here until that evidence is linked.
