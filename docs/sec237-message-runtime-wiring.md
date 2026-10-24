# SEC237 Message execution and cleanup wiring

This increment connects the permanent operations journal to the normal private
Message evaluator invocation and to the existing maintenance/deletion handlers.
It does not activate operations storage, reporting or AI.

## Private execution boundary

- The winning Message admission returns a private context bound to account,
  partition, operation proof, execution token, original acceptance, operations
  generation, retained-key fingerprint, client check ID, transport/policy, exact
  reviewed intent and execution mode.
- A new random per-admission nonce is included in the commitment but never
  persisted beside it. A lost admission acknowledgement returns no capability.
  Reconciliation does not recreate that capability or dispatch work again.
- With operations enabled, the evaluator requires this context for candidate.2
  and candidate.3, rejects candidate.1, and claims the private invocation before
  policy evaluation or any linked lookup. The existing CHECK condition verifies
  the commitment; current account/deletion, retained inventory revision/map,
  generation and lookup state are in the same conditional transaction.
- OpenAI has a separate conditional provider attempt. Duplicate or uncertain
  claims cannot obtain another dispatch permit. Existing provider deadlines,
  qualification, monetary reservations and no-retry behavior remain enforced.
- The context cannot select tables, credentials, provider, model or cost policy.
  The evaluator loads no authority HMAC keyring. Public candidate.3/schema-3,
  original accounting and History contracts remain unchanged. Neither context
  nor its nonce is passed to the model or public response/report.

## Cleanup wiring

The existing maintenance worker constructs its journal adapter from its existing
DynamoDB resource and trusted settings. Recovery and source expiry use that same
adapter. Operations expiry has a separate checkpoint/cursor and reserved time;
earlier queues cannot consume its entire allotted pass. Its original seven-day
deadline is independent of a later source receipt deadline.

The deletion handler reuses its existing verified keyring/resource and passes a
matching adapter to account cleanup. It does not perform a second secret load.
Operations children drain before their sole lookup and source receipt; original
History retention and deletion/restore fences are preserved.

The evaluator package now includes the existing authority/inventory primitives
and their module dependencies. No new credential source or package dependency is
introduced. `OPERATIONS_ENABLED` remains absent/false by default; malformed
enabled configuration fails closed.

## Evidence and remaining gates

Validation results and exact source pins are in the accompanying evidence JSON.
The regressions use isolated DynamoDB and in-memory HTTP, with external network
blocked. They are source validation, not proof of deployed storage, IAM,
provider connectivity, actual cleanup or application acceptance.

This increment does not construct a Google provider journal observer; Message
linked lookups are invocation-fenced, but Google provider coverage remains
unavailable. The daily reducer is not orchestrated, coverage is not promoted to
complete, and `daily_reporting_ready` remains false. Storage/backup/restore,
least-privilege IAM, runtime configuration, complete provider coverage, scheduled
aggregation, SEC243 delivery, accepted model qualification and live deployment
remain separate gates. No runtime configuration, customer record, provider key,
complimentary grant or paid/trial state is changed by this source increment.
