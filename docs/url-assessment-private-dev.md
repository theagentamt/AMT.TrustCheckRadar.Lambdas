# Private Dev URL assessment: resolver + Google Lookup

This is a separate IAM-only operator test backend for SECUR4ALL-190/233. It does **not** implement or activate the consumer draft `0.1.0-draft.1`, create a mobile route, approve paid/trial allowances, reconcile logical-check charges, or replace the legacy `web_risk_communication` Lambda. The user explicitly chose to leave that legacy Dev function running. Its existing unconditional Evaluate flow and legacy caching remain a separate known access-policy gap.

## Deployment boundary

- Artifact `url_assessment.zip`, handler `app.lambda_handler`, Python **3.14**, ARM64, timeout **35 seconds**, reserved concurrency **2**, recommended memory **256 MiB**.
- `STAGE=dev`; any other/missing value returns `DEV_ONLY_DISABLED` without external work.
- `WEB_RISK_SECRET_ARN`: exact ARN of `trustcheckradar/dev/web-risk-api-key` in account `107827791950`, region `us-east-1`.
- `URL_RESOLVER_FUNCTION_ARN=arn:aws:lambda:us-east-1:107827791950:function:trustcheckradar-dev-url-resolver:live`.
- Execution role: CloudWatch log writes, `secretsmanager:GetSecretValue` on that exact secret, and `lambda:InvokeFunction` on that exact qualified resolver alias. No database/cache, research, history, SES, S3, consumer entitlement or other Lambda grants. Existing AWS-managed Secrets Manager encryption requires no new broad KMS grant; a future custom key would need separate review.
- Private direct synchronous invocation only. No API Gateway integration/permission, function URL, mobile role, identity-pool role or consumer caller. Only the narrow Dev operator test role should receive explicit alias invocation permission; existing account administrators retain their administrative authority.
- Root infrastructure agent owns a separate Terraform root/state, provider compatible with Python 3.14, exact versioned S3 tuple, live alias, logging, alarms, and saved-plan deployment. Lambda agent never mutates the runtime directly.
- Automated main publication is `assessment_manual`: no AWS credentials or uploads. Publish only the reviewed new ZIP manually; do not dispatch the broad legacy uploader.

## Private protocol version 1

The request must contain exactly these fields:

```json
{"schemaVersion":1,"checkId":"operator-synthetic-01","url":"https://example.com/","scope":"full_url"}
```

`checkId` is 1–64 ASCII letters/digits/underscore/hyphen, for transient correlation only. It is not proof of entitlement, a replay receipt or durable idempotency key. `scope` is `full_url` or `origin_only`; origin-only URLs cannot contain non-root paths or a query (including an empty `?`). Fragments are rejected; the caller must consciously keep fragment content local. Client account/paid/complimentary fields and API Gateway envelopes are rejected. No headers, cookies, provider origin, credentials or callback may be supplied.

Response fields are exactly `schemaVersion`, `checkId`, `verdict`, `processingOutcome`, `coverage`, `reasonCodes`, `transportWarnings`, `threatTypes`, `lookupCount`, `providerCallCount`, `observedHopCount`, `scope`, `consumerAccessEnabled`.

```json
{
  "schemaVersion":1,"checkId":"operator-synthetic-01",
  "verdict":"no_known_threat_detected","processingOutcome":"complete",
  "coverage":"supported_checks_complete",
  "reasonCodes":["NO_LIST_MATCH","BROWSER_NAVIGATION_NOT_EVALUATED"],
  "transportWarnings":[],"threatTypes":[],
  "lookupCount":1,"providerCallCount":1,"observedHopCount":1,
  "scope":"HTTP_REDIRECTS_AND_GOOGLE_LOOKUP","consumerAccessEnabled":false
}
```

- `verdict`: `unknown`, `no_known_threat_detected`, `high_risk`. No invented moderate confidence/score.
- `processingOutcome`: `invalid_input`, `blocked`, `unavailable`, `partial`, `complete`. `coverage`: `not_assessed`, `limited`, `supported_checks_complete`.
- Invalid/locally blocked input may have null `checkId`; transport/handler success is not analysis success. Private handled failures return the same DTO, not an HTTP/API Gateway envelope. IAM denial remains an AWS invocation error.
- Google threat types are `MALWARE`, `SOCIAL_ENGINEERING`, `UNWANTED_SOFTWARE`; a known match wins immediately and stops further lookups. That early stop is conservatively `partial/limited`, including when the observed chain itself completed. It still means **avoid the link**, not an unknown verdict.
- Only all successful no-matches over the full eligible observed HTTP chain can produce `no_known_threat_detected`. Origin-only no-match, incomplete resolution, malformed provider data or unavailable checks remain unknown. No-match is not a safety guarantee or evaluation of future JavaScript, meta-refresh, interactive, geographical or changed destinations.
- HTTP and HTTPS-to-HTTP steps produce separate transport warnings and never automatically become scam evidence.

## Privacy, network and failure rules

The handler first runs the deployed resolver's URL parsing policy from a packaged copy of its current source. Metadata/private literals, encoded ambiguity, credentials, unsupported ports/schemes, fragments and recognized sensitive links never invoke AWS dependencies or Google. It then invokes the private resolver once; the resolver enforces DNS/SSRF pinning and its existing network/redirect limits. The response is strictly bounded and checked for matching identity, initial URL, eligible hop URLs, supported status transitions and complete-chain consistency.

Only actually observed eligible HTTP hop URLs are submitted to Google (up to six unique URLs). A redirect destination stopped before observation is never sent. The assessment does not itself fetch arbitrary websites: the existing isolated resolver does. Resolver visits occur **before** reputation lookup and may have website-side effects; a private operator must authorize the specific synthetic target. Sensitive-link heuristics do not prove all remaining input is non-sensitive. No public/customer use is authorized until the separate consent, projection, access, allowance and accounting contracts are implemented. No URL/path/query/hop chain is returned to the caller or stored by this function.

Google endpoint is fixed `GET https://webrisk.googleapis.com/v1/uris:search`; URL and repeated threat-type parameters are encoded, and the key is sent only in `x-goog-api-key`. No `allowScan`, Evaluate, URL submission API, origin-only fallback, API endpoint override, provider redirects or automatic retries. Fixed-host DNS is bounded, every answer must be public IPv4, TLS verifies `webrisk.googleapis.com`, and the socket uses the validated numeric IP. Per-provider attempt budget is four seconds (operations at most two seconds), body four KiB, headers sixteen KiB, complete wire response thirty-two KiB. Declared-length truncation, duplicate/conflicting framing, unsupported compression/transfer and malformed/unknown Lookup payloads fail closed. Total application budget is thirty-two seconds inside the thirty-five-second Lambda timeout. AWS SDK connections/reads are bounded with one attempt; hard Lambda timeout remains the final ceiling.

`{}` is the documented no-match body. A valid `threat` requires recognized nonempty threat types and a parseable timezone-bearing expiration. Expiration is not persisted because this implementation has no cache. Legacy Evaluate-shaped `threats`/`scores` cannot be interpreted as no-match.

Secret is retrieved as `AWSCURRENT` only after eligible resolver observations, never by deployment tooling. Accepted formats: a raw key, JSON string key, or JSON object with `apiKey`. Key stays in invocation memory only; no module cache, output, log or exception serialization. No SecretString read is needed by the operator or infrastructure plan.

## Operations

One log event contains only `event=private_url_assessment`, `status`, `verdict`, `reason`, `lookupCount`, `providerCallCount`, `observedHopCount`, `elapsedMs`. It never includes URL, query, hop, checkId, key, account identifier, raw provider response or exception. Application `print` is separate from standard AWS START/END/REPORT records.

`lookupCount` counts requested lookup attempts (including secret/config failures). `providerCallCount` counts attempts entering fixed-provider transport after key validation; DNS/TLS failures can count without a completed HTTP request. Neither is a billing amount, quota deduction, Google invoice count or exactly-once event. `observedHopCount` is successful resolver HTTP observations. All are bounded 0–6. No durable metrics/reporting ledger or retention policy is invented here; follow SECUR4ALL-178.

Handled operational failure reasons to alert on: `PROVIDER_AUTHORIZATION_FAILED`, `PROVIDER_RATE_LIMITED`, `PROVIDER_UNAVAILABLE`, `PROVIDER_RESPONSE_INVALID`, `SECRET_UNAVAILABLE`, `CONFIGURATION_UNAVAILABLE`, `RESOLVER_UNAVAILABLE`, `RESOLVER_RESPONSE_INVALID`, `TIME_BUDGET_EXCEEDED`, `INTERNAL_ERROR`. Lambda `Errors` alone cannot catch these. Other outcomes include invalid request/URL policy failures, `NO_ELIGIBLE_OBSERVATION`, `HTTP_CHAIN_INCOMPLETE`, `ORIGIN_ONLY_NOT_FULL_LINK`, `KNOWN_THREAT_MATCH`, and `NO_LIST_MATCH` with `BROWSER_NAVIGATION_NOT_EVALUATED`.

## Verification and live smoke

Local tests use synthetic provider bodies, fake DNS/TLS/sockets and mocked AWS clients. Full suite and packaging results belong in the release handoff; passing local tests does not prove the real Google project's API enablement, key restrictions, billing, IAM or network setup.

`python3 scripts/url_assessment_dev_smoke.py --function arn:aws:lambda:us-east-1:107827791950:function:trustcheckradar-dev-url-assessment:live` runs only local-block cases through the deployed handler. Add `--public-https` to explicitly fetch/check `https://example.com/`. Add `--google-test` to explicitly fetch/check Google's documented synthetic malware fixture `http://testsafebrowsing.appspot.com/s/malware.html`. Never use real customer, phishing, login, unsubscribe or token-bearing links. Run with temporary narrow-role AWS environment credentials; no log-tail invocation. The script prints sanitized case/status/counts only and does not retry uncertain invocations.

Official references reviewed September 20, 2026: [Lookup guide](https://docs.cloud.google.com/web-risk/docs/lookup-api), [uris.search reference](https://docs.cloud.google.com/web-risk/docs/reference/rest/v1/uris/search), [API-key header usage](https://docs.cloud.google.com/docs/authentication/api-keys-use). Lookup checks a supplied URL; redirect expansion remains our separate resolver's responsibility.
