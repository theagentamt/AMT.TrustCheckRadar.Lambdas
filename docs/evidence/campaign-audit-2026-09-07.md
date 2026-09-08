# Lambda Campaign Story Audit — 2026-09-07

Source baseline: deployed `main` release `e0d3811`. Scope is this Lambda and
canonical-contract repository only. Story states were read from YouTrack and were
not changed. Infrastructure, Android, deployment, and YouTrack mutations are
excluded.

## SECUR4ALL-204

| Acceptance criterion | Evidence | Result |
|---|---|---|
| One logical completed scan creates one eligible observation across retries. | Retry-stable UUIDv4 is persisted at `RESULT_READY`; the atomic completion transaction conditionally writes one outbox item; publisher and cluster dedupe records absorb redelivery. Conversation, publisher, and cluster replay tests. | Lambda pass |
| Forbidden fields and raw content never reach queues, durable aggregates, logs, traces, metrics, or errors. | Strict outbox/feature/envelope allowlists; aggregate schemas exclude identity/content/vector fields; request IDs were removed from analysis logs; shared-entitlement account IDs were removed; static log validator now checks both. | Lambda pass; deployed trace configuration remains external |
| Invalid `appFeatures` never reach clustering. | Shared runtime validator is invoked at request, publisher, and persisted-feature boundaries. JSON Schema and malformed/non-finite/oversized/poisoned input tests. | Pass |
| Account deletion can remove current active-period observations. | Contributor-period GSI, period-token derivation, tombstone-before-delete, feature deletion, and sibling `DEDUPE`/`CLUSTERED` removal tests. | Pass |
| Publisher/clustering failure never delays the analysis response. | Analysis only commits the asynchronous outbox; stream and SQS work run after the HTTP transaction. Recovery tests cover commit boundaries. | Pass |
| Publisher sends accepted records directly to the environment clustering queue. | Five-field opaque `campaign.cluster.requested` envelope and direct `CLUSTER_QUEUE_URL` send; no extractor source/runtime exists. | Pass |

## SECUR4ALL-206

| Acceptance criterion | Evidence | Result |
|---|---|---|
| Duplicate events do not increment twice. | Event-level `CLUSTERED` conditional record and duplicate no-op test. | Pass |
| One account cannot promote a campaign. | One contribution/vector per period token, three-submission cap, and minimum ten distinct contributions at finalization. | Pass |
| English and Spanish variants can join when features/tactics match. | Language is not a score penalty; bilingual matching test and English/Spanish fixtures. | Lambda pass; Android recall evidence external |
| Required negative/race matrix. | Tests cover unsupported versions, malformed/non-finite/dimension inputs, category false merge, indicator collision, bilingual false split, optimistic candidate updates, serialized concurrent creation, sparse dimension cohorts, poisoned submissions, replay, stale records, and deletion tombstones. | Lambda pass; measured 95% precision/80% recall external |
| Persistent records exclude tokens, vectors, request IDs, content, and precise timestamps. | Finalizer creates aggregate-only records; canonical aggregate/app response schemas and privacy-negative tests. | Pass |
| Lambda consumes the environment clustering queue directly; no extractor exists. | Strict environment-bound SQS envelope, partial batch response handler, static absence check. | Pass |

## SECUR4ALL-207

| Acceptance criterion | Evidence | Result |
|---|---|---|
| Time-travel tests prove every transient class expires within policy. | Unit tests prove 14+7-day key lifecycle, bounded TTL assignment, sparse environment-bound expiration keys, bounded index queries, batched deletion, and failure on unprocessed writes. | Lambda and infrastructure contract pass; deployed time-travel evidence remains |
| Account deletion removes active contributions and cannot relink later signup. | Active/recovery token derivation, tombstones, contribution/feature/dedupe deletion, candidate recomputation, and new consent epochs on re-enrollment. | Lambda pass; authenticated environment test external |
| Expired contributor tokens cannot be regenerated. | KMS key disabled after recovery and scheduled for deletion; publisher requires enabled registry key. | Lambda pass; deployed KMS evidence external |
| Backup/restore and DLQ cannot resurrect deleted/expired content. | Tombstone wins over delayed cluster work and completed ledger updates do not loop. | Infrastructure/UAT backup and DLQ evidence required |
| Reconciliation detects stuck/partial purge and alarms privacy-safely. | Lambda failures remain retryable and log only bounded counts; partial explicit expiry fails and remains alarmable. | CloudWatch alarm/reconciliation deployment evidence required |
| UAT evidence and operational runbook cover normal/failure/repair. | `docs/campaign-lifecycle-runbook.md` covers Lambda operations and safe repair. | UAT execution external |

## SECUR4ALL-211

| Acceptance criterion | Evidence | Result |
|---|---|---|
| Canonical schemas and English/Spanish fixtures are versioned. | `campaign-contracts-1.0.0.zip`, response schema, bilingual taxonomy, and fixtures. | Pass |
| Authorization, filters, pagination, localization, stale behavior, suppression, and privacy-negative tests pass. | JWT requirement; category/tactic/channel/risk/language/trend/week filters; Spanish labels; live-table reads only; count-band and dimension-evidence suppression; HMAC-signed expiring pagination tests. | Lambda pass |
| Rare-campaign enumeration/inference fails. | Published-only GSI, approved bands, minimum cohort, dimension values emitted only with `dimensionSchemaVersion=1`, and tamper-resistant page token. | Pass |
| Package is immutable, reproducible, inspected, and deployable. | Deterministic ZIP, release checksum manifest, package/content validator. | Lambda artifact pass; external vulnerability scan/deployment gate remains |
| No AWS infrastructure changes in this repository. | No Terraform/CloudFormation resources added. | Pass |

## SECUR4ALL-213

| Acceptance criterion | Evidence | Result |
|---|---|---|
| Seven required versioned schemas exist. | Draft 2020-12 schemas for app features, outbox, queue, transient feature, aggregate, review transition, and app response. | Pass |
| English/Spanish taxonomy and valid/invalid app examples exist. | Locale files cover all ten dimensions with `other`/`unknown`; five fixtures included. | Pass |
| Compatibility/versioning rules are documented and tested. | Contract README and immutable `1.0.0` IDs checked by tests. | Pass |
| Payload sizes and required/optional fields are explicit. | Strict schemas plus 32 KiB canonical runtime bound and per-field limits. | Pass |
| Tests reject prohibited fields, versions, dimensions, non-finite values, and oversized inputs. | JSON Schema and production-validator contract suite. | Pass |
| Artifact locations and immutable versions are available to consumers. | Repository path and `campaign-contracts-1.0.0.zip` are in docs, CI upload, and SHA-256 manifests. | Pass; consumer adoption external |

## SECUR4ALL-216

| Acceptance criterion | Evidence | Result |
|---|---|---|
| One opted-in success creates one valid feature-bearing outbox item. | Atomic completion/outbox transaction tests and outbox JSON Schema. | Pass |
| Retry creates no duplicate and consumes no extra scan. | `RESULT_READY` UUID recovery plus entitlement/outbox conditional transaction tests. | Pass |
| Missing/false consent creates no outbox item. | Validation defaults false and completion omits outbox; tests. | Pass |
| Images, attachments, binary/raw OCR, unknown fields are not written. | Exact request/app-feature/outbox allowlists and privacy-negative tests. | Pass |
| API does not wait for stream/publisher/cluster. | Asynchronous outbox architecture. | Pass |
| Outbox expires within 72 hours and content is not logged. | TTL cap assertions; request-ID/content-free logging regression and static validator. | Pass |
| IAM is environment-scoped and DynamoDB/KMS constrained. | Required transaction/condition-check contract documented. | Infrastructure verification required |
| Lambda and Terraform tests pass. | Lambda suite passes. | Terraform tests external |
| Dev synthetic activation is observed with kill switch. | No deployment performed by this audit. | Authenticated Dev smoke test external |

## SECUR4ALL-217

| Acceptance criterion | Evidence | Result |
|---|---|---|
| Terraform validation/contract tests pass. | Lambda dependency contract updated for publisher users-table condition and trends token secret. | Infrastructure-owned and external |
| Lambda unit/contract and packaging pass. | Participation, entitlement, analysis, publisher, deletion, canonical contract, and package suites. | Pass |
| `campaign_participation.zip` is released. | Packager and campaign evidence validator include the artifact. | Local package pass; CI release external |
| Dev smoke covers join, 15 scans, publish, withdrawal block, pending/completion. | Unit tests cover all state/quota/race transitions; publisher now suppresses withdrawn/stale epochs atomically. | Authenticated Dev smoke external |
| Logs do not expose content/account identifiers. | Content-free participation logs; shared-entitlement account identifiers removed; static log scan. | Lambda pass; deployed log inspection external |
| UAT/Production remain gated. | No push, deploy, or promotion was performed. | Pass |
| Notice and 400-day retention receive legal/privacy approval. | Exact notice/policy validation and 400-day receipt TTL exist. | Legal/privacy approval external |
