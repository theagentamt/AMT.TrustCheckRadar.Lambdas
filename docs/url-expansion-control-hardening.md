# URL control-character and deep-link boundaries

The existing backend isolates website fetches in the private resolver, rechecks
each redirect's public addresses, pins the socket address, verifies TLS, forwards
no customer credentials/cookies and executes no page scripts. It bounds HTTP
redirects, request size, headers and time. Its current assessment order is still
HTTP resolution first, followed by Google Web Risk checks of observed URLs.
This change preserves that order and does not claim zero risk or a demonstrated
exploit.

Offline reproduction found eight literal/encoded control or Unicode format
character cases that the previous parser accepted and passed to a deterministic
test transport. The parser now rejects those characters in raw input and during
bounded policy decoding, including query values. Redirect validation rejects
them before the next DNS/fetch. The original wire URL is never rewritten by
policy decoding. Normal Unicode path text, encoded spaces and query order remain
supported. Ambiguous or deeply encoded query values can now be rejected more
conservatively using existing closed failure codes. No response schema changes.

Additional offline tests cover direct and redirected intent, javascript, data,
file, content, android-app, market, mail, phone, SMS, geo, payment and Wi-Fi
schemes, including an intent browser-fallback field. These schemes were already
unsupported; tests make the no-DNS/no-follow boundary explicit. An embedded
non-web string in a public web URL's query is not unwrapped or executed. The
resolver can still contact that reviewed web URL; it cannot infer what a browser
or that website may subsequently do.

`parse_target` is also used by the assessment and reviewed-link validation shared
by Message/recovery/feedback packages. Rejection is intentionally consistent
across those callers. Authentication, entitlement/accounting, public IP/DNS/TLS,
budgets, IAM/network configuration and logging remain unchanged. This is a local
source candidate. It has not been published, deployed or used for a live call.

## Separate decision: lookup-only or checks before expansion

Lookup-only avoids all target-site requests and uses one Google lookup, but sends
the reviewed URL to Google and cannot establish a short URL's destination. The
current endpoint always resolves first. Its strict private validator requires
`providerCallCount <= lookupCount <= observedHopCount`; its public evidence calls
the result an observed HTTP chain. A lookup without a visit cannot be represented
honestly by fabricating hops or relaxing that validator in place.

Initial/per-hop reputation-before-fetch therefore needs an explicit reviewed mode
and evidence semantics. Keep the resolver without provider secret access; the
assessment could coordinate bounded single-hop fetches with Google checks before
each GET. Reuse each pre-check rather than look up the same URL again. Preserve
the maximum six target requests, six unique lookups, loop checks and one shared
deadline, returning incomplete results on exhaustion. This can increase private
Lambda calls and latency compared with today's one resolver invocation. A lookup
failure must stop expansion without declaring clearance. Google no-match does
not authorize private addresses or override other URL policy.

That interface/default/privacy decision is pending owner selection and is not
implemented here. Sensitive path/query heuristics remain incomplete; GET can
register a visit or trigger an action. Existing application logs omit URLs,
tokens and account identifiers; deployed infrastructure/provider behavior still
needs fresh verification. No provider, browser, credential or AWS sign-in action
was part of this source hardening.
