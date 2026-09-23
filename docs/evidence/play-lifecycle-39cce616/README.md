# Reviewed Dev lifecycle artifact publication

Source `39cce61623794a123e61d25f5248b0c081eccf4a` was independently reviewed and integrated into release-V01 by PR42, merge `d3eb738a10dc40e60012bf4c4d20104a13a2f36e`.

All13 artifacts were rebuilt with dependencies (where declared), Python3.14/arm64, from the clean reviewed checkout. Included application/shared source bytes were compared with that checkout; every Python module compiled. Bundled native shared objects have ELF64 little-endian AArch64 headers. Thirteen isolated disabled-handler invocations passed without network. macOS used explicitly listed host native substitutions for import checks; this does not establish execution of Linux native dependencies on AWS.

`publication.json` is the scoped publisher result: versioned immutable S3 keys, SHA256/base64 hashes, byte lengths, bundled dependency metadata and actual configured handler names. `s3-download-verification.json` records independent exact-VersionId downloads and checksum/size comparison for all13 archives. `local-package-verification.json` records source-byte, compile, architecture and disabled-response checks. No runtime, IAM, table, provider, account or gate changes were performed by the publication workflow.

Runtime installation is a separate orchestrator-controlled step. The intended immediate closed Dev scope is new3 lifecycle handlers plus existing handoff and4 modern authority/URL functions; the other5 artifacts remain candidates until their infrastructure/deletion/export inventories are qualified. Empty-event AWS smoke and infrastructure no-drift evidence belong to that deployment, not this publication.

The source validation record remains docs/play-lifecycle-runtime-validation.md. No real Google purchase/order/ack request was sent. No story-wide live acceptance, five-minute checkpoint policy approval, physical erasure deadline or release readiness is inferred from these artifacts.

## Closed Dev runtime verification

`aws-disabled-smoke.json` records the eight authorized empty-event invocations on AWS at 2026-09-23T05:04:25Z. Before invoking, the harness read every selected `live` alias and its immutable version configuration, rejected weighted aliases, and required exact published code hashes, Python 3.14, arm64, configured handlers, successful update state, closed feature gates and empty subject allowlists where present. Each response executed the expected immutable version with no `FunctionError`.

| Function | Live version | Expected and observed |
| --- | --- | --- |
| play-lifecycle-ingress | 1 | HTTP 503 |
| play-lifecycle-worker | 1 | enabled=false |
| play-token-deletion | 1 | enabled=false |
| v1-play-handoff | 2 | HTTP 503 |
| url-consumer | 7 | HTTP 503 |
| url-lease-recovery | 7 | enabled=false |
| v1-entitlements | 7 | HTTP 503 |
| v1-authority-deletion | 6 | enabled=false |

The harness sent only `{}` to these eight aliases, requested no execution logs, and used one SDK attempt per invocation. The reviewed closed branches return before provider or account operations. This verifies package loading and those disabled paths in AWS; it does not exercise enabled Google verification/acknowledgment, token encryption, accounting, export, deletion or cleanup. It does not prove physical erasure deadlines or approve the checkpoint exception. Infrastructure permissions, schedules, stream mappings, retention controls and Terraform no-drift checks are separately owned deployment evidence.

The remaining five published artifacts were not deployed in this increment. SECUR4ALL-125 remains open for the applicable enabled-flow and operational acceptance; source integration, publication and closed deployment are distinct from full lifecycle readiness.
