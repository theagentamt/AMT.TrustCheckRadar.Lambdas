# Account-deletion HTTP contract

[OpenAPI 3.1 document](account-deletion.openapi.json) describes only POST and GET
`/v1/users/account-deletion`. There is no legacy DELETE operation. The embedded
request and status schemas are exact copies of the immutable
[`1.0.0-candidate.1`](../../contracts/account-deletion/1.0.0-candidate.1/README.md)
contract; its files and checksums are unchanged. OpenAPI adds HTTP/authentication,
error and caching documentation, not a new wire version. The frozen 11-component
examples are labeled historical wire fixtures. Separate current examples include
all 12 required components, including PLAY_TOKENS; tests check them against the
current runtime configuration. Historical fixtures are not a current deployment
inventory.

POST accepts only schemaVersion, canonical UUIDv4 operationId and DELETE_ACCOUNT.
The verified access-token subject supplies identity. Successful POST is 202;
GET is 200. Reuse the original operation ID after an ambiguous acknowledgement.
An eligible set of component receipts is still REQUESTED; the public contract has
no overall COMPLETE response. Once identity is removed, GET may return 401 and
continued status access is not promised. Download an export first; acceptance
invalidates unfinished exports. Analysis subscription/allowance is irrelevant.

The JSON schemas cannot express transport byte bounds, duplicate object-key
rejection, exact integer lexical form, unique component names or cross-field time
and completion checks. These requirements remain explicit in the OpenAPI
operation descriptions and the immutable contract README. Localized clients use
fixed error codes and must not display arbitrary server text. Gateway-generated
rejections can have a different envelope and headers from Lambda responses.
HTTP 500/503 may contain a gateway JSON message without an application code, an
empty body, or non-JSON content. No upstream envelope or content type is promised.
Treat those as an unconfirmed outcome: preserve the pending intent and original
operation ID, then recover explicitly. They prove neither deletion success nor
that a request was not accepted. The Lambda envelope remains strict.

## Current deployment scope

The API schema contains no account allowlist. The currently installed handler
separately applies the restricted Dev HTTP subject gate documented in
[account-deletion-http-scope.md](../account-deletion-http-scope.md). It accepts only
explicitly configured Dev qualification subjects and otherwise returns the fixed
503 SERVER_UNAVAILABLE. An empty/malformed/non-Dev scope fails closed. Runtime
feature and inventory checks also remain required. Merely publishing this document
does not create routes or enable deletion, workers, production admission or a
retention change.

## Validation and later release work

Run `python -m pytest tests/contracts/test_account_deletion_openapi.py` with the
repository development dependencies. The tests bind the OpenAPI to immutable
checksums and fixtures, reject invalid wire variants and cross-check source error,
route, authentication and response semantics. The existing account-data handler
and service suites remain the behavioral checks; this documentation change does
not substitute for those tests or claim live qualification.

OpenAPI meta-schema validation can additionally be run with
`python -m openapi_spec_validator docs/api/account-deletion.openapi.json` in an
isolated environment containing openapi-spec-validator 0.9.0. This optional
validation dependency is not part of the deployed Lambda package.

[SECUR4ALL-329](https://andmorethings.youtrack.cloud/issue/SECUR4ALL-329) owns later
release/UAT assembled-system qualification. SECUR4ALL-200 Dev completion still
requires its retained implementation, local/component/Dev evidence and reviewed
release integration. No UAT or production run is claimed here.
