# Restricted Dev account-deletion HTTP qualification

`ACCOUNT_DELETION_HTTP_SUBJECTS_JSON` is a JSON array of zero to ten distinct,
canonical lowercase UUID subjects (including UUIDv7), bounded to 512 UTF-8 bytes.
It defaults to `[]`. Only `APP_ENVIRONMENT=dev` may admit an HTTP request. There
is no unrestricted fallback or alternate enable flag.

Both GET and POST first validate the existing runtime configuration and signed
JWT identity, then apply this scope before constructing the account-data service.
An empty, malformed, duplicate, noncanonical, oversized, non-Dev or nonmatching
scope returns the existing HTTP 503 `SERVER_UNAVAILABLE` response with fixed
message `Account deletion is not available.`, `retryable=true` and empty details.
The response and logs do not disclose the selected subjects. Existing disabled
feature precedence, request schema, UUIDv4 operation IDs and POST reauthentication
requirements are unchanged. This is a Dev qualification constraint, not a general
production admission design.

DynamoDB stream and scheduled reconciliation entry points do not consult this
HTTP-only scope. Worker-only infrastructure must keep JWT routes absent and the
list empty; the later restricted API phase must bind this list to the same
reviewed canonical subjects used by V1 and Play cleanup. The list does not prove
inventory coverage, authorize activation or waive any producer/finalizer gate.

Local validation: 49 account-data handler/service tests plus 56 subtests and eight
real SDK/Moto admission tests pass. The handler cases cover selected UUIDv4/v7,
both routes, fixed refusal before service or cleanup, malformed configuration,
worker routing and disabled-feature precedence. Only `app.py` and `config.py`
change in the account-data runtime package. No cloud invocation, data mutation,
publication, deployment or gate activation is part of this source increment.
