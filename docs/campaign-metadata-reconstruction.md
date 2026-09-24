# Campaign contributor metadata reconstruction

SECUR4ALL-207 candidate; no runtime deployment, activation, approval marker or
completion receipt is introduced. The preceding coverage assessor remains unwired
and cannot authorize erasure completion.

A candidate summary previously retained lexical fingerprints, signals and indicator
IDs from a deleted contributor: paginated repair replaced only the centroid and
counts. New contributions now retain their original bounded lexical fingerprints
alongside the existing signals and indicators, allowing all three summary fields
to be rebuilt from remaining contributions before repair is acknowledged.

## Storage and compatibility

`metadataSchemaVersion: 1` identifies the reconstruction contract on `SUMMARY`,
`CONTRIB#...` and `DELETION_RECOMPUTE` rows under `CANDIDATE#...`. Its three fields are:

- `lexicalFingerprint`: up to 32 unique lowercase 16-hex feature hashes.
- `signalIds`: up to 16 unique existing contract signal identifiers.
- `indicatorIds`: up to 16 unique existing contract indicator identifiers.

These remain derived research features, not anonymous data. No original text,
account identifier, token or new purpose is introduced. Each contribution keeps
its original expiry (at most the existing 21-day configuration); a repeat never
extends it or contributes a second feature set. Repair accumulators use the
summary's original deadline and expiry index, never a new retention period.
No live row is backfilled or rewritten by installation.

A new summary and selected-contributor addition use the same deterministic bounded
unions as repair, including indicators. Before matching current-purpose summaries
or updating repeat contributions the producer validates reconstruction metadata;
missing/unknown versions or malformed lists require reviewed migration. An old
summary may be repaired only from individually qualified remaining contributions;
repair does not infer missing per-contributor lexical data from the old summary.
Expired contributions at the fixed repair cutoff are not copied, even if physical
DynamoDB TTL deletion has not happened.

Checkpoints include the version and all three bounded accumulators. Legacy or
unknown checkpoints fail closed **before** any version-restart reset. A recognized
checkpoint can restart when the summary version changes, resetting counts,
centroid sums and metadata together under its previous revision condition. It
retains the original deadline. Runtime code does not migrate old checkpoints.

## Transaction and completion boundaries

Every repair page keeps the existing summary-version/lifecycle fence and durable
command guard. Only after the final page does one transaction replace centroid,
counts and all metadata and remove the repair checkpoint. A concurrent summary
change or completed/deviating command cancels that transaction. No partial union
is published. A lost response can resume/recompute without increasing retention.

Repeat producers compare the metadata they read at commit and also require the
observed summary version. Capped writes require the observed summary version.
Existing consent, deletion, locator and inventory guards remain in place.

The deterministic smallest bounded union is associative across pages. It uses
constant accumulator bounds; this does not prove all history was erased or that
a finalized aggregate is unlinkable. Candidate lifecycle freeze, period/key
retirement, restore/replay coverage, tombstone retirement and complete-receipt
eligibility remain separate acceptance requirements. CAMPAIGN completion remains
hard-disabled. No existing inventory marker is automatically upgraded by this
schema change; rollout must qualify legacy data and all participating writers.

## Operational handoff

Only the cluster and deletion bridge behavior changes. Bridge packaging now also
includes `shared_campaign_contracts`; the public mobile contract is unchanged.
No extra DynamoDB/KMS operation or namespace grant is required. Root owns artifact
selection and any coordinated paused-worker deployment. No upload is authorized
by this document.

Root's aggregate-only Dev audit observed zero current contribution/checkpoint rows
in a bounded complete scan. This is current-shape evidence only, not historical
absence, retained-period/key proof, restore qualification or marker approval.
The scan field is `metadataSchemaVersion` (not `reconstructionSchema`).

## Validation

- 20 focused real SDK/Moto cases: paginated metadata replacement, later-page
  bounded unions, expired-row exclusion, malformed/legacy preservation, safe
  known-version restart, commit races, lost responses, actual producer metadata
  and deadline preservation, and producer metadata race cancellation.
- 114 combined SDK/Moto cases pass across coverage, progress, metadata repair,
  locator/producer and producer-metadata suites. Separately, 17 lifecycle
  publication SDK cases pass. An initial combined run exposed the existing flat
  `service` module-name collision between independently packaged handlers (12
  import failures); running lifecycle in its own process resolves that harness
  collision without changing runtime source.
- 21 ordinary cluster/deletion cases plus 22 subtests pass. Compile and diff
  checks pass.
- Full local Python 3.14 ARM64 build for the two affected pure-Python packages:
  cluster SHA256 `ca053ea9df73e43607bbd375d14829f93e56482e4afdcc36974a5a4c068ff10e`
  (14,973 bytes, 12 distinct Python source files); deletion bridge SHA256
  `120017d376d37257a4feed38655483f29e41d7ac4d7e8eb5e7c2d6386eaebe71`
  (20,295 bytes, 14 distinct Python source files). Every Python member matches
  the local source and compiles; neither package has a requirements file.
  These checks are local package evidence, not deployed AWS invocation evidence.
- All tests use synthetic data; no AWS, provider, customer-data or live erasure
  requests were made.
