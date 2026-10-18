# History transaction failure classification

SECUR4ALL-344 is a bounded Dev bug under open History story SECUR4ALL-226.
The duplicate check found no matching existing bug. The actual service mapped
every DynamoDB `TransactionCanceledException`, including a documented throughput
reason, to `409 CONFLICT` / “History state changed.” Bootstrap used the same
unconditional cancellation test. The retained probe is source-only; no deployed
incident or live fault was observed.

## Change and public contract

Both transaction boundaries now require the exact complete reason vector for
the submitted action count, including account/deletion condition checks. At least
one reason must be `ConditionalCheckFailed`; every other reason must be that code
or the literal SDK code `None`. Only this proven conditional case returns the
existing retryable `409 CONFLICT`.

Throughput, transaction contention, validation/service errors, mixed reasons,
missing or malformed metadata and transport exceptions return the existing
retryable `503 SERVER_UNAVAILABLE` with the fixed message “The History transaction
could not be confirmed.” Unknown reasons do not prove a state conflict. SDKs
which omit cancellation reasons therefore receive unavailable, not an invented
conditional diagnosis. No error code, schema, route, SDK configuration, condition
expression, authorization check, stored field, retention policy or permission
changes. SDK messages, reason messages and returned items are never included in
these errors or their transaction-failure logs.

There is one application-level SDK transaction invocation per attempt and no
new retry loop. Existing SDK-internal retry/token behavior is unchanged, not
claimed to be one network attempt. A transport failure can follow a committed
write; `retryable=true` is not proof of rollback. A caller must keep the original
operationId and explicitly reconcile/retry that same operation. It must not
generate a new action or operation ID because of this error. Bootstrap has no
operationId and retains its existing account-scoped state idempotency.

Conditional bootstrap races still revalidate foundation eligibility and read
the committed state before returning success. Service/unknown cancellation does
not enter that conditional readback path. A caught transaction exception does
not fabricate a missing receipt, successful mutation or conflicting state.

## Dev validation and limits

The companion validation and reviews pin exact source and results. Tests cover
positive/negative reason vectors, all five actual handler routes, finite public
errors and private canaries, real local Moto conditional transactions, two callers
with the same pre-write state, independent clear/reset generations, bootstrap
races and late account fences. Committed-but-lost acknowledgement cases cover
bootstrap, delete-one with assessment/replay erasure, clear, reset and History
account deletion; explicit replay leaves original receipts, deadlines, jobs and
generation changes unchanged. No accounting or grant behavior is added.

The first local concurrency fixture incorrectly waited on later verification
reads as well as the two intended pre-write reads. Its two failures are retained;
the fixture was bounded to the first two reads, without changing production code
or relaxing assertions. Final passing evidence is distinct from that initial run.

Moto and injected SDK-shaped failures are automated component/contract evidence,
not AWS, provider, production, deployed authorization or distributed-service
qualification. The inspection ZIP excludes dependencies and is not a deployable
artifact. No live fault injection, AWS read, endpoint call, actor/session lookup,
emulator, runtime/gate/retention change, deployment or main promotion occurred.

## Open release qualification: SECUR4ALL-226

This versioned page is linked from the narrow bug and the existing open SEC226
qualification scope; it does not create another umbrella. Under the owner's
release-test workflow, later assembled target execution stays pending there and
does not turn unfinished Dev criteria into passes. SEC344 can be Dev Done only
after retained checks, review, publication and verified release integration.
SEC226's broader governed mutation/lifecycle acceptance remains open.

The ready local entry point, from the reviewed repository, is:

```sh
PYTHONPATH=src AWS_EC2_METADATA_DISABLED=true \
AWS_ACCESS_KEY_ID=synthetic AWS_SECRET_ACCESS_KEY=synthetic \
AWS_SESSION_TOKEN=synthetic AWS_DEFAULT_REGION=us-east-1 \
python -B -m pytest -q tests/history_mutation_api
```

It requires the existing pinned requirements-dev.txt and runs synthetic/Moto
fixtures only. It is not a command for a deployed environment.

Later authorized target qualification must:

1. Pin the merged source, dependency-complete artifact/runtime and reviewed
   environment configuration through the established deployment process. Confirm
   target/role/account identity and unchanged auth, binding and mutation gates.
   Source-only ZIP hashes are not deployment pins. Record the actual release
   scheduling item/sprint when assigned; no sprint or date is invented here.
2. Use dedicated disposable, populated fixtures and an independently unaffected
   account baseline. Validate success, denied auth/binding, and same-operation
   replay for bootstrap, delete-one, clear, reset and History-only account deletion.
   Complete receipts are HTTP200, accepted pending erasure is HTTP202. Compare
   original metadata/deadlines and independent generations without recording
   assessments, tokens, account identifiers or raw database rows.
3. Confirm true conditional races yield409 only when the complete SDK vector is
   known; missing/service/unknown vectors yield503. For uncertain write outcomes,
   a deliberate retry with the original operationId must reconcile once, never
   duplicate a job, receipt, deletion or generation transition. Bootstrap uses
   its original state lookup. Do not treat a transport error as rollback.
4. Verify the fixed response and fixed failure diagnostics contain no arbitrary
   SDK text/items. No live-safe fault-injection runner is currently established.
   A target fault case therefore remains not run until a disposable-target-only
   harness and its scope are reviewed and authorized; do not throttle existing
   customers/tables or falsify a production response to manufacture this evidence.
5. Clean up only authorized fixtures using the reviewed lifecycle and verify the
   unaffected baseline. Record exact timestamps/artifacts, finite outcome codes,
   transaction/replay counts and metadata-only checks. Bound retries; an unknown
   result remains unknown until reconciled. Unperformed/blocked cases stay open.
   Physical-only cases remain with ATCR-148, not a blocker to this backend fix.

## Next independent gap

SECUR4ALL-345 records a separately deduped, source-confirmed read-boundary bug.
An SDK-shaped read failure from `_receipt/get_item` reaches the handler's generic
exception logger, returns nonretryable500 and includes a synthetic SDK diagnostic
canary in logs. The public body remains fixed; no actual secret exposure or
deployed incident is claimed. Raw logs were not retained. This transaction fix
does not implement that read-boundary fix; SEC345 remains To Do. Its categorical
probe is committed with this handoff so the next owner has a concrete entry point.
