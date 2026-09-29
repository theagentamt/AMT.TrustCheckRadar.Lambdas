# URL transport candidate.2

Disabled engineering contract paired only with URL assessment `0.3.0-candidate.1`. New preparation binds this transport version inside the existing payload HMAC and records it on the preparation/receipt. Reusing the same proof under the other transport version is a conflict. Candidate.1 new preparation/submission is rejected before refresh, reservation or provider invocation; existing candidate.1 reconciliation remains available.

Google evidence uses exactly source, outcome, targetScope, threatTypes, observedAt, validUntil and freshness. UTC provider deadlines retain up to nanosecond precision; observation is captured at response receipt and represented at whole-second precision. A match is current only before the original provider deadline. `no_match` is `observation_only`, with no cache-validity promise. Unknown or legacy validity is `unverified`, never a fresh safe result.

Expiry at or before initial settlement prevents charging for an expired finding. A later replay is a display projection: original charge, receipt, assessment time and stored provider evidence do not change. Expired/unverified Google evidence becomes unknown/partial, with explicit expired/unverified reason, built-in help and `do_not_retry`. Access for a new check remains independently accurate. No provider re-invocation, paid automatic refresh or refund is introduced.

Use fixtures.json and the assessment reference mapper plus JSON schemas. Deployment must pair private assessment, consumer, compatible entitlement reader and export reader/client. Existing alias gates remain closed. This is not cache-obligation, provider-live or general activation acceptance.
