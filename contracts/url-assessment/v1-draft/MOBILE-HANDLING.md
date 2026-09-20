# Mobile handling contract — 0.2.0-candidate.1

This candidate defines how an app interprets a future authenticated consumer response. `consumerActivationAllowed=false`, both endpoint fields null, and no legacy or private route may be substituted. Android can validate/copy these fixtures and implement local presentation parsing while external assessment remains disabled.

## Validate before presentation

Match exact `contractVersion` and response `kind`, then validate the appropriate complete schema and nested control fields. Validate closed verdict/status/reason/error/warning/evidence/access/accounting/retry enums and message keys; do not coerce missing fields, booleans into counters, unknown strings into enum defaults, or null allowance/charging into zero. Unknown version/kind/enum, contradictory evidence or incoherent safety copy is an unsupported response, **never a safe result**.

On unsupported public data, use the local EN/ES `compatibility.unsupported` fallback, show no active external-check/navigation/retry control, preserve the locally accepted check identity for later status reconciliation, and never replace an already trusted high-risk result with reassurance. Do not infer a charge or refund. This fallback is local client behavior, not a fabricated server response. A backend *private* schema mismatch maps to `SERVICE_UNAVAILABLE`; it is not evidence that the mobile app needs an upgrade.

Choose safety label and emphasis from validated verdict + processing outcome. `messageKey` can select only approved local EN/ES copy coherent with those fields. The schema rejects `high_risk` paired with `url.no_known_threat` or a permissive action. Never display arbitrary backend/provider text or execute a URL from a response.

## Outcome matrix

| Public outcome | What the app explains | Primary local control | Retry/accounting behavior |
| --- | --- | --- | --- |
| `high_risk`, complete | A known threat was found. | `avoid_link`; keep prominent threat warning. | Show independent ledger state. No automatic recheck/open. |
| `high_risk`, partial | A known threat was found; some checks did not complete. | `avoid_link`; same threat priority as complete. | Missing secondary checks do not downgrade the threat. Receipt reconciliation is separate. |
| `no_known_threat_detected`, complete supported checks | No known threat was found in supported checks; no safety guarantee. | `verify_independently`; never an automatic Open action. | Only backend-authorized future user action may start another check. |
| `unknown`, partial | Inspection was incomplete; no full-link conclusion. | `use_built_in_help` or `verify_independently` as validated. | Retain check identity. Reconcile pending/unknown accounting first. |
| `unknown`, origin-only limitation | Only an origin was checked; omitted path/query and original link remain unassessed. | `verify_independently`. | Never silently resubmit omitted components. |
| `unknown`, blocked private/sensitive target | Destination/data could not be inspected under policy. This is not proof of maliciousness. | `review_input`; show the safe local explanation. | No fallback to a different URL or provider. |
| `unknown`, invalid/unsupported input | Input is invalid or outside supported URL types. | `review_input`; unsupported QR remains local and inert. | Edited input is new intent; don't silently reuse an accepted check ID with changed content. |
| Service/provider unavailable or timeout | The check did not finish or could not be understood. | `use_built_in_help`. | A failure alone makes no statement about charge; consult `accounting` and `retry`. |
| Provider rate limited | External checks temporarily limited. | `use_built_in_help`. | Only use a supplied retry disposition; no invented cooldown or automatic retry. |
| `suspicious` | Reserved engineering shape for an independently approved AMT rule. | `verify_independently`. | Private Google Lookup does not emit this grade; no heuristic is approved by this candidate. |

HTTP adds `UNENCRYPTED_CONNECTION`; a downgrade adds `HTTPS_TO_HTTP_REDIRECT`. Neither is itself a scam verdict. HTTPS does not prove safety. `assessedAt` is observation time, not a guarantee about a later or dynamically selected destination. Browser/JavaScript/meta-refresh/interactive destinations remain outside supported HTTP observation.

## Access snapshot is distinct from a result

`access.observedAt` timestamps server authority; the client never authorizes a provider request from a cached snapshot alone. The server must revalidate every new external operation and explicit replay. The candidate sets no freshness window.

| `access.state` | External analysis | Local/account controls |
| --- | --- | --- |
| `allowed` | Potentially eligible, only through the future server authority. | Respect active-account/device/abuse controls. |
| `sign_in_required` | Disabled. | Sign in; all app access still requires an account. |
| `device_action_required` | Disabled. | Review the active device; account-level status reconciliation remains separate. |
| `subscription_required` | Disabled. | Built-in help and existing-check reconciliation remain available after authentication. |
| `allowance_exhausted` | Disabled, including external reputation checks. | Built-in help and existing-check reconciliation remain available. |
| `temporarily_unavailable` | Disabled. | Use only independently confirmed built-in/reconciliation controls; do not guess eligibility. |

`basis` is paid/trial/complimentary/none/unknown, never legacy Free/Pro balances or research bonuses. `remainingChecks`/`resetsAt` are nullable authority facts: null means not supplied/not applicable, not zero or infinity. Only `unlimited=true` with complimentary basis represents unlimited allowance; it is never a huge numeric balance. Pricing and initial grant amounts are absent.

## Logical check, accounting and reconciliation

| `accounting.state` | `chargedChecks` | UI interpretation |
| --- | --- | --- |
| `not_started` | 0 | Authority confirms no operation admitted. This is never inferred from an HTTP error. |
| `pending` | null | Show confirmation pending; do not adjust a balance. |
| `charged` | 1, with opaque receipt | Authority settled exactly one logical-check deduction. |
| `not_charged` | 0, with opaque receipt | Authority explicitly settled no deduction. |
| `unknown` | null | Charge state unknown; retain identity and reconcile. |

`requiresReconciliation=true` for pending/unknown. A result can be high risk and still await a receipt, or be unavailable with a settled charge; display the two facts independently. Provider attempts, redirect hops, analysis labels, lost responses, app cancellation, duplicate deliveries and HTTP codes must never drive deductions or refunds. Server receipts are the sole charging source. Receipt IDs are opaque; no identifiers/credentials/URLs are embedded.

A reconciliation request contains only `{contractVersion, operation:"reconcile", checkId}`. It authenticates the account's ownership of that logical check, returns the existing permitted result/receipt or a recognized error with authoritative accounting, and **must not** invoke the resolver/Google/AI or create a new charge. It remains possible after subscription/allowance expiry. If a receipt/result cannot be located, do not claim `not_started` or zero charge without authoritative evidence; return unknown/pending accounting and safe status guidance. Availability of historical result content remains a separate approved retention/access gate; status may return `SERVICE_UNAVAILABLE` with a known receipt while result content is unavailable.

No reconciliation URL exists yet. Android must not implement this operation by repeating the analysis endpoint or guessing a REST path. No cancellation endpoint is introduced: leaving a screen does not prove server/provider work stopped.

## Retry and error controls

`retry.automaticRetryAllowed` is always false in this candidate. A disposition describes a **user-confirmed** control, never a background dispatcher. `afterSeconds` may carry an authoritative wait duration later; current fixtures use null and establish no cooldown policy.

- `reconcile_same_check`: retrieve status/receipt for the existing ID; no new provider work.
- `retry_same_check`: requires explicit trusted operation replay authorization, freshly allowed access and a settled zero-charge receipt. The same ID and same accepted input are retained. The future server must provide idempotent replay semantics; zero charge plus access alone does not authorize it.
- `after_sign_in`, `after_device_action`, `after_access_change`: resolve that control boundary first, then reconcile any prior pending operation before considering fresh analysis.
- `after_client_update`: the public contract/client version is incompatible; update first. Never use this for internal provider-auth/config errors.
- `after_input_review`: explicit local review/edit. If content changes, it is different intent and must not be submitted under an already accepted immutable check identity.
- `do_not_retry`: no analysis retry control is offered by this response; built-in help/account controls may remain available.

Recognized errors: AUTHENTICATION_REQUIRED, ACTIVE_DEVICE_REQUIRED, SUBSCRIPTION_REQUIRED, ALLOWANCE_EXHAUSTED, EXTERNAL_ACCESS_UNAVAILABLE, CONTRACT_UNSUPPORTED, INPUT_REJECTED, CHECK_ID_CONFLICT, SERVICE_UNAVAILABLE. The error schema binds each code to safe EN/ES keys. CHECK_ID_CONFLICT never triggers a new-ID retry automatically. Secret/config/provider-auth failures expose only SERVICE_UNAVAILABLE-style user copy, never key names, internal error bodies or provider text. HTTP status binding and route selection remain activation gates; a transport success is not automatically an assessment success.

## Source mapping and acceptance evidence

The backend reference maps every current private reason code through `private-reason-map.json`; every mapping has input, mandatory authority facts and exact expected public payload in `mapping-cases.json`. Only corresponding public files listed in `fixtures/manifest.json` are mobile wire fixtures. Private counters and internal `schemaVersion`, `consumerAccessEnabled`, IAM details and secret/provider configuration do not cross the boundary.

Android should copy an immutable source snapshot with exact Git commit + file checksums, keep local EN/ES label/action mappings exhaustive, and test unknown values, no-threat/high-risk copy mismatches, high-risk-plus-partial, origin-only, null accounting, explicit replay authorization, and expired-access reconciliation. These fixtures enable parser/presentation work only. Server endpoints, authenticated authority, commercial values, privacy policy and mobile network activation are not completed by this contract.
