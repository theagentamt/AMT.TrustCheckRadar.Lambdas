# Recovery checkpoint namespace isolation

The URL lease/expiry worker now reads and updates its two durable cursors under `PK=V1#CHECKPOINT`, with unchanged sort keys `LEASE_SWEEP_CURSOR` and `EXPIRY_SWEEP_CURSOR`. Approval evidence remains under `V1#CONTROL/HMAC_KEY_INVENTORY`. The worker no longer needs any write permission on the inventory partition.

IAM must grant strong GetItem and transactional UpdateItem on the dedicated checkpoint partition. Existing exact inventory reads and transaction condition checks remain read-only. Account and purchase usage mutations retain their separate bounded families. There is no runtime configuration override for the checkpoint partition.

A read-only Dev audit found both old cursor rows present and both new rows absent. Their contents were not output. Deployment occurs with schedules disabled. The first enabled pass will intentionally start a bounded traversal from the beginning and establish the new cursors; it will not copy or mutate old cursor rows. Existing conditional receipt settlement and expiry deletion make revisits idempotent. This is a one-time scheduling restart, not a usage/account reset or a claim that pending work is complete. Normal durable continuation then resumes under the new namespace. The inert old cursor rows remain for separately scoped operational cleanup.

Local validation: 20 real SDK/Moto expiry/recovery cases pass, including independent pass fairness, poison-row visibility, continuation after deadlines, disabled handler, inventory preservation and refusal to reuse the legacy cursor namespace. The Lambda stays disabled until the orchestrator's coordinated runtime/IAM qualification. No data, provider, runtime or scheduling mutation is part of this source change.
