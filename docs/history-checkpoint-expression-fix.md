# History checkpoint expression correction

The first enabled Dev empty-work acceptance stopped at History lifecycle after the account-data, campaign and History bridge cases passed. Sanitized logs identify DynamoDB `ValidationException`: the checkpoint condition used reserved attribute `shard` without an expression alias. This is a source expression defect, not an IAM denial. No account command or synthetic receipt was injected.

Both expiration advancement and shared reconciliation/reset checkpoint updates now alias the same existing `shard` attribute in the condition and update. Record shapes, CAS values, clocks, retention, inventory meaning and worker gates are unchanged. Existing checkpoints require no migration. An uncertain successful advance still rejects the stale retry without progressing twice.

Validation: five new real SDK/Moto cases plus thirteen existing lifecycle tests pass (18 total). The new cases reproduce the reserved-word failure against the original source and cover both table lanes, hourly wrap, reconciliation wrap, reset, exact stale-CAS refusal and unrelated-row preservation. No runtime invocation or deployment is part of this source change. Root owns the independently reviewed deployment and fresh worker acceptance.

Deployment selects Python 3.14 ARM64 History lifecycle and the unchanged History account-deletion bridge from one source pin, as required by the infrastructure artifact contract. The source-boundary change is limited to the checkpoint expression aliases; it neither establishes new inventory evidence nor changes erasure approval. Artifact byte/source comparisons are provided separately after source freeze. The earlier partial runtime result remains evidence, not an all-worker pass.
