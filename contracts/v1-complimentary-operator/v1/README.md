# V1 complimentary-access operator contract

`POST /v1/operator/complimentary-access` is an operator-only AWS_IAM HTTP API
route. It is not exposed to Android or iOS and accepts no Cognito consumer JWT.
The configured role may call only this exact method, stage and path. It cannot
invoke Lambda directly.

The JSON body follows `request.schema.json` and identifies the target only by its
stable account ID. `operationId` makes an identical retry idempotent; reuse with
different content fails. Grant actions use `OWNER_GRANT`, `SUPPORT_GRANT` or
`CORRECTION`. Revoke actions use `REVOKE` or `CORRECTION` and require
`expiresAtEpoch: null`. A grant may use null for no expiry or a future epoch.

The handler derives actor and session evidence from API Gateway's signed IAM
context. Body claims cannot select the operator. Success returns only revision,
effective state and basis; it never returns the account ID, counters, subscription
proof or audit key. Responses use `Cache-Control: no-store`.

The route remains disabled unless the operator gate, exact role allowlist and
31,536,000-second audit-retention setting all match. Account/profile and deletion
fences are checked transactionally. Grant/revoke never changes research consent
or administrative privileges. Complimentary access bypasses subscription and
allowance only; device authorization, attempts, in-flight limits, provider budgets
and other abuse controls continue to apply.
