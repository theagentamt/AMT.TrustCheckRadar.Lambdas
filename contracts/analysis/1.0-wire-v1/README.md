# Legacy analysis wire correction

This supplement describes `POST /analysis`, `schemaVersion: "1.0"`. It is not the
governed message transport, whose complete prepare/submit/reconcile bodies remain
limited to **32768 UTF-8 bytes**, NFC text and candidate-specific identities.
No endpoint, deployment, provider authorization or campaign permissions change.

Legacy `/analysis` permits **65536 actual UTF-8 JSON bytes**, inclusive of keys,
escapes, whitespace, entities and appFeatures; headers and API Gateway wrapper are
excluded. A string body is measured once in its received representation. If
`isBase64Encoded` is true, strict base64 is decoded, decoded bytes are measured,
and strict UTF-8 is decoded before JSON parsing. Invalid encoding, Unicode scalar
sequences, JSON and oversize bodies return nonretryable HTTP400 INVALID_REQUEST.
The flag must be a boolean. A dict body is an internal adapter/test convenience:
measure `json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8')`.
It is not accepted with the base64 flag. This deterministic representation is used
only when no original wire string exists. Existing nonfinite feature errors stay
field-specific; the appFeatures validator rejects them.

Required string fields use Python `str.strip()` on both ends; no NFC normalization
is performed on this legacy path. Strip characters are U+0009–000D, U+001C–0020,
U+0085, U+00A0, U+1680, U+2000–200A, U+2028–2029, U+202F, U+205F and U+3000.
The text is nonblank and at most8000 Unicode scalar/code points after trimming.
Supplementary emoji count one; combining accents and joined emoji count their
individual scalars. Android UTF-16 length and Swift grapheme count are different.
Escaped lone surrogates are rejected. Caller-selected Unicode escaping counts as
transmitted bytes; a raw6000emoji request fits while its escaped equivalent may not.
No truncation is allowed. `request.schema.json` describes the post-trim projection;
runtime byte/scalar/trim checks and appFeatures reference remain authoritative.

Required fields: schemaVersion, requestId, sourceType, localSanitizationApplied=true,
sanitizedText, entities. Source is exactly pasted_text/ocr/mixed. Entity list has
0–100 entries, each with a nonblank token and one of the nine legacy types; duplicate
entries count and remain accepted on this old path. Unknown top-level/entity fields
are ignored by the existing legacy validator. This does not grant permission to
send originals: mobile sends approved opaque placeholders only. Governed input
instead requires closed fields, unique declarations and contiguous per-type numbers.

campaignConsentGranted defaults false and must be boolean. appFeatures may be absent
or null only when consent is false; it remains structurally validated when provided.
The schema lists the full feature bounds; `app_features_reference.py` adds finite
numbers, extractorVersion<=64 UTF-8 bytes and canonical feature bytes<=32768.
No consent is inferred from supplying features. SourceType and unchanged requestId/
payload remain intact through the existing service/idempotency/outbox behavior.
Changed content is a new reviewed request; do not rewrite dispatched identities.

Use the existing verified Cognito bearer access token and active-device binding;
send `x-device-binding-fingerprint` using the existing active binding. Its value
matches `^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$`; the existing token scope is
`aws.cognito.signin.user.admin`. Validation occurs
before identity/device lookups, model/quota/campaign work. Unsupported schema remains
HTTP400 UNSUPPORTED_SCHEMA_VERSION, distinct from INVALID_REQUEST; neither retries.
Authentication/device rejection and throttling retain their existing status mappings.
No raw request/token logging is added. Fixtures are synthetic validation evidence,
not proof of full PII removal, runtime deployment, provider access or physical-device
acceptance. SECUR4ALL-222 remains the deployment/verification gate.
