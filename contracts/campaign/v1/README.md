# TrustCheck Radar Campaign Contracts 1.0.0

This directory is the canonical V1 contract handoff for the app, Lambda, and
infrastructure repositories. Every JSON Schema uses draft 2020-12, rejects
unknown fields, and has an immutable `1.0.0` identifier. Runtime producers and
consumers must also enforce the semantic constraints called out with `$comment`,
including the 32 KiB canonical `appFeatures` limit and retention relationships.

## Compatibility

- Label additions and corrections do not change a machine contract version.
- Adding an optional field or taxonomy identifier requires a minor version.
- Removing a field or identifier, making an optional field required, or changing
  an identifier's meaning requires a major version.
- Existing files under a released version directory are immutable. Corrections
  are published in a new version directory and artifact.
- Internal producers reject unknown fields. App-facing readers render an unknown
  future taxonomy identifier as its stable identifier until localized data is
  updated.

## Artifacts

The source contracts live at `contracts/campaign/v1/`. `make package` and
`make package-no-deps` also produce `dist/campaign-contracts-1.0.0.zip`. The ZIP
contains these source files plus its own `SHA256SUMS`; the release-level
`dist/SHA256SUMS` pins the complete contract artifact beside the Lambda packages.

The schemas cover the app-generated feature object, analysis outbox record,
opaque clustering queue envelope, transient feature record, persistent aggregate,
review transition, and app-facing trends response. `taxonomy.en.json` and
`taxonomy.es.json` carry identical stable IDs with separate localized labels.

## Privacy boundary

Feature extraction is app-only. No contract permits images, attachments, raw OCR,
raw message bodies, account/device/request identifiers outside the short-lived
outbox boundary, IP addresses, full phone or wallet identifiers, usernames,
URLs, or precise event timestamps. Reviewed-domain publication is represented by
the bounded `reviewed_domain` indicator and is allowed only in an aggregate that
has already met the ten-contributor threshold.
