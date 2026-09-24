# Privacy runtime prerequisites and bounded qualification

This document pins source contracts for operator qualification. It does not approve inventory, enable a runtime, create a marker, store a secret or authorize live account/purchase writes. The five-minute lifecycle checkpoint exception remains pending.

## Whole-account inventory

The authoritative marker is in the deletion ledger at `PK=INVENTORY#dev`, `SK=ACCOUNT_DATA_INVENTORY`. It has exactly these fields:

| Field | Required value / meaning |
| --- | --- |
| PK, SK | Exact keys above |
| recordType | ACCOUNT_DATA_INVENTORY |
| schemaVersion | Integer 1, never boolean |
| revision | Positive integer matching the configured reviewed revision |
| environment | dev |
| coverage | VERIFIED_COMPLETE, only after the underlying assessment is accepted |
| manifestSha256 | Lowercase 64-hex digest of the reviewed purpose/storage/writer/deletion/export/restore inventory; must match runtime pin |
| requiredComponents | Exact ordered list below |
| usernameIsSubVerified | Boolean true supported by actual identity mapping evidence |
| approvedAtEpoch | Positive epoch at or before current time; strictly earlier than each accepted deletion request |

Ordered components: `SESSION_REVOCATION`, `DEVICE_BINDINGS`, `DEVICE_RECOVERY`, `ANALYSIS_ABUSE`, `HISTORY`, `CAMPAIGN`, `CAMPAIGN_OUTBOX`, `ENTITLEMENTS`, `V1_AUTHORITY`, `PLAY_TOKENS`, `USER_PROFILE`, `IDENTITY`.

Runtime source: `shared_account_finalization.service.validate_inventory`; exact transaction conditions guard the marker, request and completion evidence. The marker is a reviewed assertion, not proof created by scanning an empty table. An assessment must enumerate every retained namespace/key, legacy writer and replay path, consent/research/history/receipt data, purchase ownership/usage, token/mapping/cursor rows, restore and backup exceptions, and each component's qualified erasure/export behavior. Authority HMAC, ownership and campaign locator inventories are separate controls, not substitutes for this marker. In particular, campaign period/key retirement and historical linkable data coverage cannot be inferred from the export reader's current/previous-period traversal.

Adding PLAY_TOKENS changes component meaning and invalidates old manifest qualification. Publish a new reviewed manifest/revision/approval epoch; do not rewrite old request timestamps or accept old receipts automatically. Drain/requalify older operations through an explicit migration. Token proof requires all retained account namespaces, both reverse-mapping sides and any owned operational cursor; unknown shapes and mismatched pairs fail closed. USER_PROFILE and IDENTITY remain gated until the exact token component proof exists.

## Cognito mapping evidence

The active implementation uses Cognito `Username=accountId` and requires the returned Username and exactly one `sub` attribute to equal that account ID. It does not resolve email aliases as identity proof. `COGNITO_USERNAME_IS_SUB=true` and `usernameIsSubVerified=true` therefore need a bounded complete audit of relevant existing identities and profile keys, plus the creation/import/federation paths that determine future Username/sub values. Pool sign-in settings or one selected test account alone are insufficient.

A read-only audit can compare projected identity attributes and profile ownership in memory, returning only counts/mismatch categories and an evidence digest; never publish emails, subjects or raw attributes. Any unknown/imported mapping keeps the affected inventory unqualified. Finalizer treats UserNotFound as absence only behind this approved mapping control and still requires every component receipt; AccessDenied is never absence. No AdminDeleteUser call is part of prerequisite qualification.

## Export cursor keyring

The Secrets Manager ARN must match the existing Dev `trustcheckradar/dev/account-export-cursor-<six-character AWS suffix>` path in account107827791950/us-east-1. Runtime requests `VersionStage=AWSCURRENT`. SecretString is UTF-8 JSON of at most4096 bytes with exactly `activeKeyId` and `keys`:

- `activeKeyId`: 1–8 ASCII letters/digits, present in keys.
- `keys`: 1–4 entries; each ID uses the same character/length rule, and each value is canonical standard Base64 of exactly32 independently generated random bytes.
- No duplicate JSON members, extra fields, non-finite values, missing active key, URL-safe/unpadded alternatives or wrong key lengths.
- Key material never appears in logs, validation errors, PRs or reports. The secret holds only cryptographic key material, not account/token data.

`account_export_api.runtime.parse_cursor_keyring(raw)` is pure in-memory validation with no AWS access or activation. All malformed secrets become `SERVICE_UNAVAILABLE`/503; duplicate configuration fields are not user-request errors. Runtime closes its Secrets Manager client even when parsing fails. Operators can call this parser in a guarded local setup procedure and report only success, key count and active key ID. Do not print its returned key dictionary.

Rotation keeps the prior key under its original ID for in-flight capabilities: export continuation lasts exactly15 minutes from START, not from each page. New capabilities use the active key. Removing an old key deliberately invalidates its remaining cursors; replacing bytes under an existing ID also invalidates them. Plan overlap for the issuance window and runtime/cache propagation rather than silently extending the public export window. Existing valid canonical keyrings and public candidate.2/candidate.3 contracts remain compatible.

## Safe work now and isolated live proposal

Safe immediately without customer writes or activation: source/Moto integration tests; read-only installed metadata/IAM/retention checks; in-memory validation of a separately authorized generated secret; read-only identity/inventory comparison. No checkpoint policy assumption is needed.

A subsequent AWS data-plane cleanup qualification should be a separately reviewed, isolated fixture exercise, not a request to turn on customer handlers:

1. Infrastructure owner prepares dedicated synthetic token/ledger/authority tables and narrow fixture-only permissions, with the same PK/SK/index shape and no token-store backups. Never reuse the customer tables or approve their inventory for this exercise.
2. Seed only explicit synthetic namespaces, fake non-provider tokens/ciphertext, and synthetic commands/inventories. Record exact fixture keys locally for bounded cleanup; do not use real account IDs, provider purchases or trial/allowance state.
3. Invoke the existing TokenDeletion class from a controlled harness against those fixture tables. It needs no KMS decrypt, provider or Cognito API, no scheduled worker and no operational cursor. Exercise bounded pages, missing proof, pair mismatch and command races, then verify exact receipt and table removal.
4. Remove fixture resources through the infrastructure owner's reviewed plan; report actual conditional deletion evidence separately from TTL and physical deadline claims. Never mark real whole-account inventory complete from fixture success.

This proposal is not executed or infrastructure-authorized by this source change. The scheduled Worker currently requires its explicit checkpoint-policy gate and must remain disabled while that approval is pending. Live export/finalizer acceptance also remains dependent on reviewed inventories, credentials/configuration and explicit limited activation—not physical mobile hardware.

## Validation of the cursor parser change

57 focused cursor/runtime/reader/service tests pass, including canonical rotation/backward readability, malformed shapes and duplicate fields mapping to503, no secret details, and actual loader AWSCURRENT/client-close behavior with SDK doubles. 50 separate real-SDK/Moto export/finalizer regressions also pass. Compile and diff checks pass. No live secret read/write, account action, provider call or activation was performed. Deployed source39cce616 remains unchanged until a separately coordinated archive update.
