# Guarded URL History reader

GET the existing list/detail routes with
`x-history-contract-version: 1.0.0-governed-history-candidate.2`.
The response envelope version is candidate.2. Its item union includes unchanged
candidate.1 stored Message/URL items and candidate.2 stored guarded URL items.
The latter retains assessmentTransportVersion 1.0.0-candidate.3 and schemaVersion 3
content-free private summary, including truthful lookup/hop/contact counts.
No raw URL, destination, chain, operation proof or input text is added.

No header preserves candidate.1. Encountering a new item through that reader
returns CONTRACT_UNSUPPORTED; it is never silently omitted or downgraded. A new
client should preserve both item versions and use the existing presentation layer
for provider evidence expiry. Cursors bind to the requested History version and
cannot move between versions. Seven-day retention and deletion fences are intact.
Fixtures are offline examples only. Deployment/CORS compatibility and real client
integration remain separate validation gates; no API configuration was changed.
