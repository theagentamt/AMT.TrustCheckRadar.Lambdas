# Conversation analysis idempotency

The POST /analysis contract binds each authenticated account and request ID to one canonical payload hash. Reusing the same request ID with different content returns IDEMPOTENCY_CONFLICT and never runs analysis or consumes quota.

## Durable server states

- PROCESSING owns a bounded lease. A concurrent request receives REQUEST_IN_PROGRESS with retryAfterSeconds.
- An expired processing lease can be conditionally taken over by one worker.
- RESULT_READY stores the complete replayable response before any quota is consumed.
- COMPLETED means the response, one consumption event, and the entitlement update committed in one DynamoDB transaction.

A completed request replays its stored response. A request left at RESULT_READY after a timeout or transaction failure skips model processing, obtains a fresh entitlement snapshot, and retries only the atomic commit. DynamoDB transaction conditions prevent concurrent workers from consuming quota twice.

The request record and consumption event use SHA-256 account namespaces. The request record stores a SHA-256 payload identity and the response needed for replay. Raw account identifiers are not used in the record keys.

## Failure behavior

- Model failure before RESULT_READY releases only the lease owned by that invocation.
- Lambda termination leaves a processing lease that becomes eligible for safe takeover.
- Failure after RESULT_READY preserves the result for recovery.
- Transaction cancellation returns a retryable REQUEST_IN_PROGRESS; no partial quota, event, or completion write is possible.
- DynamoDB TTL expiry does not depend on prompt physical deletion. An expired logical record is replaced with a conditional write.

Tests cover payload conflict, active concurrency, expired lease takeover, expired TTL replacement, result-ready recovery, completed replay, owner-safe release, transaction composition, quota conflicts, and result/request binding.
