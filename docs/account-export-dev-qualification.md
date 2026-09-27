# SEC236 scoped Dev export acceptance

Use the reviewed export artifact with the existing genuine source inventories.
No account/data seeding, marker approval or runtime activation is performed by the
checker. Root coordinates those independent deployment steps. Candidate.3 and
`ACCOUNT_EXPORT_PLAY_TOKENS_ENABLED=true` are required for the full current scope.

Before enabling the scoped route, verify deployed source/hash/runtime, JWT issuer,
client and scope; actual table identities/work marker/locator generation; retained
V1 HMAC and purchase inventories; History generation; enabled current/recoverable
campaign keys; and the existing cursor keyring. Preserve old cursor keys through
their outstanding continuation windows. Invalid/incomplete evidence remains closed.
The export role needs reads/queries, exact Cognito AdminGetUser, secret reads and
campaign GenerateMac only; no export archive, provider purchase lookup or writes.

Enroll a new designated synthetic account through the actual approved lifecycle,
complete onboarding, establish its active device and obtain a fresh access JWT.
Do not use an existing customer's identity. Root's reviewed activation selects its
canonical subject in ACCOUNT_EXPORT_HTTP_SUBJECTS_JSON. An inactive/exhausted grant
must not prevent export. Subscription data is content, never an admission condition.

`scripts/check_scoped_account_export.py` defaults to dry-run, verifying a clean exact
source commit and immutable candidate.3 checksums. Its reviewed plan has exactly:

```json
{"schemaVersion":1,"endpoint":"https://abcdefghij.execute-api.us-east-1.amazonaws.com/v1/users/account-export","subject":"12345678-1234-7234-8234-123456789abc","issuer":"https://cognito-idp.us-east-1.amazonaws.com/us-east-1_example","clientId":"aaaaaaaaaaaaaaaaaaaaaaaaaa","sourceCommit":"<exact reviewed 40-character commit>"}
```

These are examples, never live defaults. Supply the actual independently verified
API/pool/client/subject and frozen source. `--execute` reads only
`{"accessToken":"...","deviceFingerprint":"..."}` from stdin; an authorized
operator should pipe these from in-memory authentication, never shell arguments,
files, logs or a copied command. The checker requires Python with `jsonschema`.
No dependency or credential is installed by the script.

The checker issues START once, then bounded CONTINUE requests without retries.
It validates candidate.3 envelope/field boundaries, immutable operation/start/expiry,
complete family coverage, no-store headers, response/total bytes and current token
freshness before and after each network call. It allows at most 256 pages and 240
seconds; no refresh or new START silently extends the download. It discards page
content and cursors, emitting only page/item counts, fixed status/error categories,
and whether the traversal completed. A lost response is unconfirmed, not erasure
or denial evidence. Gateway validates JWT signatures; local claim checks only bind
routing to the reviewed synthetic subject/pool/client.

A complete traversal proves only the currently observed owned data, possibly empty
families. Local SDK nonempty20-family coverage is separate. The checker is not a
full client download implementation or independent validation of every nested
public summary contract. It cannot prove source inventories complete by itself.

Remaining actual Dev checks: outside-scope/unauthenticated refusal; pending-age and
inactive-device refusal; genuine scoped successful traversal; fresh-auth rejection;
accepted deletion invalidating a retained in-memory continuation; real secret/role
and metrics readback. Never restore/reset a completed deletion or fabricate receipts.
For a controlled deletion interruption, an operator must retain the continuation in
memory and coordinate the independently authorized real deletion; this standalone
checker intentionally does not create that mutation. Fixed900s boundaries and races
are tested locally without waiting or using expired JWTs as export-expiry proof.

No SEC236 cloud execution is claimed here. Release/device acceptance is separately
tracked by SECUR4ALL-331; root owns the final Dev evidence and tracker transition.

## Local validation for this increment

- `AMT_AUTHORITY_INTEGRATION=1 AWS_ACCESS_KEY_ID=testing AWS_SECRET_ACCESS_KEY=testing PYTHONPATH=src /tmp/amt-live-deletion-venv/bin/python -m pytest tests/account_export_api -q`: 96 passed.
- `/tmp/amt-account-openapi-venv/bin/python -m pytest scripts/tests/test_scoped_account_export.py -q`: 21 passed in normal and optimized Python. This separate existing environment contains the operator-only `jsonschema` dependency; production dependencies are unchanged.
- Source diff and immutable contract checks remain independent of actual Dev execution. Full Python3.14 ARM64 dependencies are packaged by the existing single-function builder; native Linux execution is not inferred from host tests.

Packaged local handler checks also pass for disabled export, missing scope and empty
scope, each with zero SDK calls and fixed503 errors. Host cryptography substitution
permits import only; the delivered native binaries remain separately verified ARM64
ELF, with actual Lambda execution reserved for the independently reviewed Dev step.
