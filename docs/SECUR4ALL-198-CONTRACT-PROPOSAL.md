# SEC198 proposed version/service-guidance contract

Status: **proposal for the retained SEC190/SEC198 contract gate**, not an approved
wire contract, implementation, new endpoint or Ready-for-Development decision.
SEC198 remains Draft/To Do. This reconciles the retained V1 requirements; the
historical V2 label does not remove the ATCR49/ITCR49 rollout dependency.

## Reconciliation and proposed exact payload structure

Targeted source searches in the Lambda contracts/source, retained infrastructure
inspection and Android source found no implemented minimum-supported/recommended
build or emergency-guidance delivery contract. Android's generic Home service
state is not evidence of one. No route or auth mode is specified in this proposal.
An existing implementation discovered later must be reconciled before creating a
second mechanism.

Proposed schema identifier: `1.0.0-version-guidance-candidate.1`. A single response
would be a closed object with exactly these fields; unknown fields, duplicate
JSON keys, nonfinite numbers and malformed types must fail validation.

| Field | Proposed exact type and meaning |
| --- | --- |
| `schemaVersion` | Exact schema identifier above |
| `environment` | Enum `uat` or `prod`, must equal the requesting client's built-in environment |
| `revision` | Positive integer, monotonically increasing for accepted configuration, including an authorized rollback of content |
| `issuedAtEpoch` | Nonnegative integer UTC seconds |
| `expiresAtEpoch` | Integer greater than `issuedAtEpoch`; maximum lifetime and allowed clock skew require the cache decision below |
| `applications` | Closed object with `android` and `ios` keys; each value has exactly `minimumSupportedBuild`, `recommendedBuild`, `securityUpgradeRequired`, `messageKey` |
| `applications.*.minimumSupportedBuild` | Nonempty canonical build-identifier string; platform comparison/format is a required native contract decision |
| `applications.*.recommendedBuild` | Same platform type, ordered at or above the supported minimum; recommendation alone does not block |
| `applications.*.securityUpgradeRequired` | Boolean; only approved security-required application-upgrade handling may block the affected app |
| `applications.*.messageKey` | Key in the separately approved, packaged English/Spanish catalogue; no arbitrary markup or remote executable text |
| `serviceGuidance` | Closed object with exactly `state` and `messageKey`; `state` is enum `normal`, `maintenance`, `incident`; key is from the same approved catalogue |
| `osRecommendation` | Null or closed object with exactly `android`, `ios`; each is null or a packaged catalogue key for an optional nonblocking OS recommendation |

This proposal chooses no minimum/recommended build values, message keys, URLs,
expiration duration or app-blocking behavior for absent/expired configuration.
Those cannot be inferred from installed Dev version numbers. Service guidance
never authenticates a user, grants analysis access, changes allowance or claims
successful external analysis. OS advice cannot become an app security requirement.

## Exact contract decisions needed before runtime implementation

These are the story's existing linked contract/security gates, not an expansion
of product scope or a request to weaken security:

1. **Trust and delivery:** select an existing trusted API transport and concrete
   authenticated-or-safely-public route, or signed payload delivery with approved
   key custody/rotation. Define environment binding, replay rejection and the
   operational configuration owner. Do not invent a new issuer/client/role.
2. **Cache and failure:** approve maximum age/clock skew, storage and refresh
   bounds, response to first-use offline/no cache and expired last-known data,
   and whether any previous security requirement may remain blocking. Missing
   configuration must never fabricate a new upgrade requirement or successful
   service result; how an existing requirement ages is still a policy decision.
3. **Native ordering and guidance:** agree Android/iOS build comparison,
   downgrade behavior and approved accessible EN/ES catalogue/calls to action.
   Keep optional OS recommendations separate from required app upgrades.
4. **Rollback:** approve who may publish a higher revision carrying reverted
   content, recovery from a bad minimum and key/config compromise, and how a
   cached client recognizes the authorized rollback. Revision rollback/replay
   cannot be silently accepted as a recovery shortcut.

No choice above is implemented or activated by this document. The retained
story says no additional product decision beyond linked contract gates; these
are precisely those gates. Numerical cache defaults, runtime storage/resources,
API path, auth and publication rights must be attached as reviewed decisions.

## Threat/privacy and acceptance proposal

No account identifiers, demographic or submitted-content fields belong in this
configuration. Treat transport/configuration as untrusted until verified. Bound
body size, catalogue membership, build formats, dates and revision; prevent
cross-environment reuse, rollback/replay and arbitrary remote links/scripts.
Fail closed with respect to authentication and entitlements independently of
whether service guidance can be displayed. Preserve local built-in help.

Before source/runtime acceptance, test wrong issuer/signature or trusted-host
binding, UAT/Production crossover, stale/replayed/conflicting revisions, future
and expired times, malformed/oversized/extended payloads, first-use offline,
last-known offline/expiry, optional recommendations versus security requirements,
approved English/Spanish accessibility and a real rollback from a bad minimum.
Fixtures do not prove delivery trust, operational publication or UAT behavior.
Attach the reviewed contract/design, operational owner and ATCR49/ITCR49 native
integration dependencies. Actual UAT evidence and manual Production approval
remain separate required gates. No runtime deployment or mobile blocker is
inferred from this proposal.
