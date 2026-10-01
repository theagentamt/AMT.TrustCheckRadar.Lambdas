# Optional demographic research — Phase A source contract

Phase A supplies a closed, default-disabled backend contract for an optional age
band and US jurisdiction profile. It does not enroll anyone, expose a route, or
authorize campaign enrichment or commercial use. Protection, account access,
subscription, allowances, devices, and ordinary campaign participation are
independent of this choice.

## Public choice and wire contract

The authenticated route is `GET|PUT /v1/users/demographic-research-profile`.
Identity comes only from a Cognito access-token `sub`. PUT accepts schema 1,
canonical UUIDv4 operation ID, reviewed state version, current notice, and action
`enroll`, `update`, or `withdraw`. Enroll/update require exactly one closed age
band and state code; withdrawal must contain neither value.

Age bands are `18_24`, `25_34`, `35_44`, `45_54`, `55_64`, `65_74`, `75_PLUS`,
and `PREFER_NOT_TO_SAY`. State codes are the 50 USPS state abbreviations, `DC`,
`OTHER_US_JURISDICTION`, and `PREFER_NOT_TO_SAY`. The backend never derives this
profile from telephone number, IP address, ZIP code, message, URL, QR code, or
provider response.

The purpose is `optional-demographic-protection-research`; its purpose version is
`consumer-protection-research-v1`; notice is
`demographic-research-2026-09-30-v1`; policy is
`optional-demographic-research-v1`. Current consent and values expire
400 days after enrollment. Updates correct values without extending consent.
Expired consent returns `review_required`, suppresses values, and requires a new
enrollment epoch.

## Storage, correction, withdrawal, and restore behavior

Current values live only at `USER#<sub>/DEMOGRAPHIC_RESEARCH` and the newest
seven-day operation receipt. A correction or withdrawal transaction deletes the
prior operation receipt and atomically replaces/removes current values. The
value-free audit records a 24-hour cleanup deadline and immediate completion.
It contains no selected values, value hash, derived segment, or campaign key.

The deletion-ledger authority contains no demographic values. Every read and
write requires it to match current state, version, consent epoch, last operation,
and validity. This suppresses a stale users-table copy. Full account deletion
removes current, operation, and authority records under the `USER_PROFILE`
component and preserves only validated value-free audits until their original
400-day expiry.

DynamoDB PITR may keep historical table blocks for 35 days. TTL is not proof of
prompt erasure. Restores remain isolated and cannot serve traffic. Promotion must
reapply the current account-deletion fence, compare with the current value-free
authority, and repeat account-data inventory qualification. A ledger restored
with the same old snapshot is not current authority.

## Activation and downstream limits

Service, enrollment, exact-subject allowlist, export candidate.5, and deletion
policy are independent default-closed gates. Phase A performs no campaign write,
aggregate contribution, model prompt augmentation, commercial use, entitlement
lookup, allowance charge, device mutation, or provider call. Any future campaign
use needs a later contract and separate qualification; this source does not grant
that use.

Metrics use `TrustCheckRadar/DemographicResearch` and only bounded environment
and operation dimensions. Logs and metrics must never include the selected age
band, selected jurisdiction, subject, operation ID, request body, or record.

Source tests and package checks do not constitute deployment or release
acceptance. Scoped Dev activation, export/client interoperability, account-delete
E2E, PITR restore requalification, and retention observation remain release-test
work.
