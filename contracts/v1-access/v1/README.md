# V1 access and explicit trial contract

Candidate deployment contract, schema version 1. Routes are implemented but default
disabled; trial additionally requires explicit retention approval; no public paid/complimentary write operation is supplied.

- `GET /v1/access`: empty body/query; verified Cognito access JWT. Send the raw
  registered fingerprint in `x-device-binding-fingerprint` to establish this
  device's eligibility. Missing/different fingerprint returns a snapshot with
  `activeDevice=false`; this never registers or switches devices.
- `POST /v1/access/trial`: the same JWT and active-device header, body exactly
  `{"schemaVersion":1,"activate":true}`. Invoke only after explicit user action.
  First activation starts seven days/ten completed checks; retries recover the
  original trial without resetting its clock or counters. Returns the snapshot.
- Both success responses: HTTP 200 and `snapshot.schema.json`. Errors:
  `error.schema.json`; all exact codes/statuses appear in `fixtures.json`.

Snapshots are advisory; every admission rechecks authoritative account/device,
subscription/trial/complimentary state, remaining allowance and operational caps.
`externalChecksAllowed` describes entitlement eligibility, not availability of
Google or the consumer route. Deployment/client feature gates remain separate.
`remaining` subtracts reservations as well as completed usage; reservations are
not charges. Null limits/counters for complimentary mean unlimited subscription
allowance, while account/device and abuse controls still apply. `basis=none`
means no currently valid authority; built-in functionality remains available to
an authenticated account. An expired complimentary overlay restores the actual
unexpired underlying paid/trial period without resetting it.

Only `TRANSACTION_UNCERTAIN` and `AUTHORITY_SNAPSHOT_CHANGED` advertise retryable
true. A lost trial response may be recovered via GET or the same explicit trial
activation; never infer that the trial was not activated from a failed response.
No automatic activation on app launch, login, URL submission or device switch.
Unknown schema/enums fail closed; no automatic purchase, trial activation or
registration. Translate code-based guidance in both English and Spanish.
No account ID, raw purchase reference, secret, URL or device identifier appears
in the new response. Every response sends `Cache-Control: no-store`.
