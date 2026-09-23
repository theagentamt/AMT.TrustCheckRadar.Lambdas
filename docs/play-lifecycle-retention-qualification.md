# Play token retention and erasure qualification

This increment adds synthetic integration evidence against release-V01 runtime source `39cce61623794a123e61d25f5248b0c081eccf4a`, starting from release merge `b2a84785`. Production code, immutable contracts, runtime artifacts, infrastructure and activation gates are unchanged. It requires no physical device.

## Performed qualification

The isolated Python 3.14 SDK/Moto run passed **66 tests**, including **nine new cases**. Command:

```sh
AMT_AUTHORITY_INTEGRATION=1 AWS_DEFAULT_REGION=us-east-1 \
  /tmp/amt-account-privacy-venv/bin/python -m pytest -q \
  tests/shared_play_lifecycle/test_runtime_workers.py \
  tests/account_export_api/test_dynamodb.py \
  tests/shared_account_finalization/test_finalizer.py
```

The added cases establish:

- Before the exact token deadline, metadata remains readable and cleanup preserves the row. At the deadline, export omits metadata even while the physical row remains; explicit cleanup then deletes the exact observed row. Moto does not implement DynamoDB TTL removal, so these assertions exercise application behavior rather than relying on TTL.
- A refreshed token written between expiry observation and conditional deletion survives with its new ciphertext/deadline. The stale delete fails and does not rewrite the refreshed row.
- Account erasure removes its expired-but-still-stored operational cursor. Another account's token and cursor remain unchanged. These are isolated fixtures; they do not approve or exercise live checkpoint retention.
- Missing or mismatched reverse binding prevents token-component completion and preserves evidence for reconciliation.
- Actual binding preparation, token metadata projection, token erasure and the resulting `PLAY_TOKENS` receipt compose with profile cleanup and identity finalization. Missing token proof blocks profile deletion and identity calls; deleting one page alone does not complete the component. A subsequent strong empty proof permits profile cleanup and a single identity deletion, with terminal retries idempotent. Other component receipts are explicitly synthetic preconditions; this is not a full-account component integration test.
- The real candidate.3 export reader and cursor traverse 27 token metadata records over two pages, finish every included family, exclude stored expired metadata and omit credential fields. A deletion fence introduced after token-family reading but before response validation rejects the page.

Tests use Moto DynamoDB, synthetic identifiers/tokens, a deterministic cipher double and an in-memory identity service. They make no live AWS data writes, Google purchase/order requests, acknowledgments, account deletion or gate changes. They do not test enabled KMS execution, asynchronous TTL timing, Pub/Sub delivery or physical erasure deadlines. Existing deployed disabled-handler evidence remains in `docs/evidence/play-lifecycle-39cce616/`.

## Remaining backend acceptance

These dependencies are concrete backend qualification work; unavailable mobile hardware does not block them.

| Area | Remaining evidence / dependency |
| --- | --- |
| Checkpoint policy | Explicit owner approval for the five-minute pseudonymous operational position is still pending. `PLAY_CHECKPOINT_POLICY_APPROVED` remains false. Synthetic cursor tests are not approval. |
| Token cleanup operation | Reviewed limited activation plus actual scheduled traversal, lag alarms, deadline behavior and crash/retry observation. Source counters and Moto deletion do not demonstrate a live erasure deadline. |
| Account deletion | The source-aligned account-data candidate is now installed with deletion and finalizer gates closed; its empty-event AWS check passed. Requalify the whole-account inventory to require `PLAY_TOKENS`, then qualify actual stream/catch-up composition and component ordering before enabled use. This test supplies other component receipts; it does not certify their cleanup. |
| Export | The source-aligned export candidate is now installed with export/token-export gates closed; its empty-event AWS check passed. Qualify its exact read-only token-table permissions and enable candidate.3 only for explicitly compatible consumers. Authenticated live export/deletion race and full retained-family coverage remain to be qualified. |
| Google delivery | Root reports the keyless push identity and grants configured, closed callback installed (ingress live version 2), unauthenticated/invalid-bearer HTTP 401 and no Terraform drift. Those are separate infrastructure results. Authenticated Google delivery, pending 600-second retention/no retained acknowledgments/no DLQ, transport retry and content-free logging still require operational qualification distinct from these tests. |
| Enabled provider / acknowledgment | Fresh Google test-purchase proof, initial prepared-owner recovery, renewal/grace/shortening, acknowledgment retry and missed-event catch-up need reviewed test-only provider qualification. No real provider mutation was performed here. |
| Retention controls | Dedicated-store backup exclusion, KMS boundaries and deployment state have separate infrastructure evidence. Future role/backup/configuration changes require rechecking. Logical token expiry and disabled runtime smoke do not prove physical erasure or all external copies removed. |

The existing eight closed Dev functions need no rebuild for this test/docs-only change. A subsequent closed install qualified the export/account-data package loading; three other published archives remain undeployed candidates. See `docs/evidence/play-lifecycle-39cce616/aws-privacy-disabled-smoke.json` for the exact limited AWS checks. SECUR4ALL-125 remains In Progress; this increment closes only the stated synthetic qualification gap.
