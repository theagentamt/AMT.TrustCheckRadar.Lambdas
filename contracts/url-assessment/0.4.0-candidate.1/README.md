# Guarded URL assessment candidate

Source candidate only; activationEnabled remains false. The client reviews the
exact disclosed URL and sends one explicit Check. Decoding alone has no action.
Transport 1.0.0-candidate.3 selects this 0.4.0-candidate.1 assessment contract.
There is no optional expansion flag, second operation or additional user charge.

For full_url, the assessor validates the candidate's public DNS answers, obtains
Google Web Risk Lookup evidence, then invokes the private one-response fetcher.
Each HTTP redirect destination repeats that sequence before it is visited.
The fetcher independently repeats public DNS validation and pins the socket IP.
A match stops before fetching that candidate. Its reputation evidence targetScope
is full_submitted_url or redirect_destination; no HTTP hop is fabricated for it.
Origin-only disclosure performs one origin lookup and no target GET; withheld
path/query/fragment are never reconstructed. No raw destination chain is public.

The private assessment request uses schemaVersion 2; response uses schemaVersion 3.
The private resolver request and response schemas here describe its separate
single-hop schemaVersion 2 protocol. No guarded request falls back to schema 1.
Legacy private schema 1 is retained for existing workers and is not the guarded
profile. Migrating those workers requires their own compatible contracts before
claiming all URL paths use guarded traversal.

Limits: five redirects, six target requests and six Google lookups; 2,048-byte
URL; ten seconds aggregate target/DNS/RPC work, inside the existing eighteen-second
consumer assessment allowance. Each isolated RPC receives only its remaining
allocation and has no SDK retries. Existing TLS, public-IP, credential exclusion,
header-size and no-script/body-processing controls remain. Exhaustion, malformed
responses and provider failure never become a no-threat conclusion.

HTTP GET still contacts a destination and may have side effects. Google receives
the disclosed candidate URLs. Reputation can miss threats; DNS may change; JS,
meta refresh and interactive browser destinations are outside completed HTTP
coverage. This profile does not promise safety or live operational readiness.
