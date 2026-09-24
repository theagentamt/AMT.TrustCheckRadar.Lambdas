# Single export cursor-fix artifact publication

Reviewed source `e1651f86e30fc1478f69ba16a4049be8baf0e5f3` was integrated by PR47 at release merge `6101d989ef1ed98e425f789c7b60e337c63a60e8`. Only `account_export_api.zip` was rebuilt/published. Other artifacts, Lambda runtimes and activation gates were not changed.

`publication.json` records the exact immutable Dev S3 key, VersionId, SHA256/base64 hash, byte length, handler and full dependency inventory. A separately downloaded exact VersionId matches every byte of the reviewed local archive and S3 checksum; `download-verification.json` records this check. Conditional S3 creation prevents replacement of conflicting bytes at the source-pinned key.

`local-verification.json` records56 distinct source archive members matching the clean reviewed checkout, all Python modules compiling, and2 native ELF64 little-endian AArch64 objects. The generated root handler shim was separately compared exactly. Local disabled HTTP503 smoke used host cryptography substitution on macOS and forbade network; this does not qualify native Linux execution or enabled behavior on AWS.

`initializer-compatibility.json` records the actual infrastructure initializer generating a32-byte keyring using a fake SDK only, then passing the new packaged parser and a synthetic capability roundtrip in memory. No generated key was persisted or printed; no AWS request occurred. Host cryptography substitution is disclosed. This establishes format compatibility, not successful live secret initialization.

Source validation remains57 focused parser/loader/reader/service tests plus50 SDK/Moto export/finalizer regressions. The new parser is still undeployed at publication time. Root coordinates any reviewed infrastructure selection/apply, live key setup and post-deploy checks separately. No inventory approval, identity mapping proof, checkpoint-policy approval or enabled export/erasure acceptance is inferred. SECUR4ALL-125 remains In Progress.

## Closed Dev installation verified

Infrastructure PR67 (`d4d5e79f4b9a00984efeb64eeea79e4fa77987fe`) selected this exact versioned export archive and applied its reviewed single-function plan. `aws-disabled-smoke.json` records the subsequent authorized empty-event check against `trustcheckradar-dev-account-export-api` at unqualified `$LATEST`, revision `c1ab92bc-20d6-4b25-a287-08c00a488361`.

The harness verified the published hash, Python3.14/arm64, handler, successful configuration state and both export gates false before and after invoking `{}` once. It returned HTTP503/SERVICE_NOT_ENABLED with no FunctionError and unchanged revision. Unqualified Invoke has no atomic revision precondition; this is explicitly a pre/post drift check. No key, account or provider data was requested.

The cursor parser fix is now installed, while export remains disabled. Root separately reports cursor secret initialization and infrastructure configuration checks; this Lambda invocation does not read the key or exercise enabled parser/key retrieval. Identity mapping remains false and inventory pending. No enabled export, data deletion, retention deadline or checkpoint-policy approval is inferred. Other artifacts were not redeployed in this increment.
