# URL redirect resolver v1

Private backend component, delivered independently of `web_risk_communication`. No analyzer code or API route is changed. This is HTTP redirect observation, not a safety verdict or a browser simulation.

## Invocation

Deploy `url_redirect_resolver.zip` with `app.lambda_handler`, Python 3.14, ARM64, 256 MiB and a 12-second Lambda timeout. The implementation caps work at 10 seconds (or remaining invocation time minus 500 ms). No environment configuration or provider secrets are required. Terraform's `url-resolver` stack supplies a dedicated role, private subnet, NAT and filtering; it grants selected backend roles access to the published `live` alias. Invoke synchronously with AWS SDK `InvocationType=RequestResponse`, automatic SDK retries disabled, and a read timeout exceeding the Lambda timeout. The caller must check both Invoke transport errors/FunctionError and the returned resolution status. An SDK timeout does not prove the function did not run; do not automatically visit the link again.

```json
{"schemaVersion":1,"checkId":"opaque-check-id","url":"https://example.com/short"}
```

Exactly these fields are accepted. The identifier is 1–64 ASCII letters, digits, underscores or hyphens. API Gateway envelopes, caller-selected headers, methods, limits and identity fields are rejected. IAM controls invocation; the calling assessment service must enforce user authentication, entitlement, allowance, idempotency and per-user abuse controls before calling this component. No mobile access is enabled by this implementation.

## Response

All controlled outcomes return schemaVersion, checkId, resolutionStatus, hops, lastObservedUrl, httpChainComplete, warnings, reasonCodes, requestCount and scope=HTTP_REDIRECTS_ONLY. Each observed hop has url and httpStatus. No body, arbitrary headers, score, risk label, provider result or DNS address is returned. Only completed HTTP responses appear in hops; requestCount includes connection attempts after validation.

- `http_chain_complete`: a 2xx terminal response ended supported HTTP traversal; reason codes explicitly include BROWSER_NAVIGATION_NOT_EVALUATED. A preview/interstitial page can return 200, so this is not an ultimate-destination guarantee.
- `partial`: DNS/network/TLS failure, timeout, loop, redirect limit, unsupported HTTP status, IPv6-only destination, or unsupported browser navigation.
- `blocked`: non-public destinations, mixed public/private DNS, unsupported schemes/ports, known sensitive-link patterns or rejected redirect data.
- `invalid_input`: malformed request or URL. No inference about the submitting person's intent.

HTTP adds UNENCRYPTED_CONNECTION; an HTTPS-to-HTTP hop also adds HTTPS_TO_HTTP_REDIRECT. These warnings are transport facts, never automatic high-risk findings. The caller must preserve risk separately from completeness and retain already-known threat evidence.

## Controls and limits

Standard HTTP redirects 301/302/303/307/308 are supported for any eligible public domain, including TinyURL and custom shorteners. At most five redirects/six requests; only HTTP:80 and HTTPS:443. Unicode hostnames must be supplied as valid ASCII/Punycode; ambiguous authorities, credentials, encoded control characters, backslashes, trailing-dot hosts and nonstandard IP forms are rejected. Existing path escapes, duplicate query parameters, ordering and explicitly empty queries are preserved. Leading double-slash paths are rejected because the standard HTTP client would rewrite them. Fragments are not sent. Embedded URL parameters are not independently fetched.

Resolve both A and AAAA records with bounded dnspython lookups and no search suffixes. Every returned address must pass public-address validation; connect over IPv4 to a validated numeric address without a second DNS lookup. IPv6-only links are explicitly unsupported. TLS uses the original hostname for SNI and certificate validation. Each redirected request repeats validation, including same-host redirects. No fallback after TLS failure, no automatic redirects, no retries, no cookies, no user credentials or proxy environment inheritance.

Use a fixed GET, read at most 16 KiB while finding response headers, then close. A small body prefix may arrive in the same socket read but is discarded; no body is parsed, returned, logged or executed. Each network operation has at most two seconds within the shared deadline, including a repeatedly checked header deadline to stop slow-drip responses. Reject malformed/folded headers and duplicate Location fields. A Refresh header produces an unsupported result. HTML meta-refresh, JavaScript and interactive navigation cannot be detected comprehensively because page bodies are not analyzed.

Known reset/sign-in/action paths and query names indicating tokens, credentials, signatures, codes or secrets stop before fetching. This deliberately conservative heuristic can block legitimate links and cannot identify every sensitive or state-changing URL. GET can register a click or consume a token. The caller's privacy disclosure and sensitive-link policy still require approval before consumer release; absence of a pattern is not a privacy guarantee. Tests use synthetic domains and controlled responses.

## Logging and data

The application emits only bounded outcome/reason/count/timing fields. It never logs the incoming event, caller ID, URL, Location, body, DNS answer or exception text. Returned URLs are transient sensitive data; callers must not log, persist or contribute them to research by default. Raw exception details are suppressed as INTERNAL_ERROR. Lambda invocation payload logging, tracing and asynchronous destinations are not enabled by this stack. No global mutable connection, DNS or URL cache is used.

## Build and verification

```sh
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q tests/url_redirect_resolver
bash scripts/build_lambda_zip.sh --function url_redirect_resolver --python-version 3.14 --arch arm64
```

The resolver defaults to a Python 3.14 target for both single-function and all-functions builds; other functions keep their existing Python 3.13 target unless explicitly overridden. The resolver has dedicated Python 3.14 CI tests and a packaged-handler import check. Runtime configuration is managed by infrastructure; rebuilding a ZIP does not change the deployed runtime.

dnspython is pinned in the function's requirements and packaged into the ZIP. Development requirements include the same version to run DNS tests. The all-functions packaging/upload registries also include this optional artifact. Building a ZIP does not publish or deploy it.

Before release, validate the actual deployed network boundary and IAM permissions in Dev with owned fixtures, verify no raw URLs in CloudWatch, and inspect a real Terraform plan. Unit/mocked-provider tests do not establish live enforcement. The analyzer's later Lookup migration and reputation checks remain a separate delivery.


## Consumer integration notice (proposed EN/ES templates)

These templates are recorded for later mobile review/integration; no UI or customer access is enabled by this resolver deployment. Show the applicable notice before a customer initiates external inspection, alongside the product's privacy policy. Do not treat agreement to inspect a link as research/commercial-use consent.

- EN: “Checking this link contacts its website and may register a visit. Some links can trigger an action when opened. Do not submit password-reset, sign-in, payment-confirmation, or other private one-time links.”
- ES: “Al comprobar este enlace, se contacta con su sitio web y es posible que se registre una visita. Algunos enlaces pueden ejecutar una acción al abrirse. No envíes enlaces para restablecer contraseñas, iniciar sesión, confirmar pagos ni otros enlaces privados de un solo uso.”
- EN blocked result: “We did not open this link because it may contain private information or trigger an account action.”
- ES blocked result: “No abrimos este enlace porque puede contener información privada o ejecutar una acción en tu cuenta.”

Server policy rejects known sensitive action paths and query keys, including up to three nested percent-decoding layers and semicolon-delimited parameters. Additional encoding layers are unsupported. These are conservative heuristics, not proof that every remaining link is non-sensitive. No override is exposed in the resolver request.

## Dev smoke harness

`scripts/url_resolver_dev_smoke.py` invokes only the selected resolver alias. It verifies blocked requests make zero outbound attempts and, when an owned fixture URL is supplied, verifies redirects, limits, malformed headers, and timeouts. Use a narrowly scoped assumed-role environment or an AWS profile authorized to invoke the alias. The harness suppresses payload/URL output and never prints AWS credentials. It does not verify the subnet firewall independently; infrastructure must verify that boundary separately. `scripts/url_resolver_http_fixture.py` is a temporary HTTP-only test server with static synthetic endpoints, no customer data and no arbitrary redirect input. Infrastructure owns its restricted deployment and cleanup.
