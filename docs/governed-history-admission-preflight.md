# Governed History admission and data-read scope

The approved Dev account was added to History's Lambda subject list, but the
existing read-only role still names only the original tester's data namespaces.
The selected account therefore cannot pass the first projected profile read.
Exact deployed-source offline reproduction maps that denial to
`IDENTITY_READ_UNAVAILABLE`, then public `503 SERVICE_UNAVAILABLE`.

The observed closed window (2026-10-18 14:24:20–14:25:15 UTC) contains one
integrated History list 503. The runtime completed without a timeout, import
error or traceback marker. Those aggregate observations do not identify the
account/request or prove its unique historical exception branch. Independent
source/policy comparison establishes a sufficient selected-account blocker.
The Android client correctly renders generic503 as an unavailable read.

This is independent of funding. History reads require the active account/device
and the admitted read scope, not a funded check allowance. Expired funding must
not become a zero quota or be used to explain this History failure.

## Offline preflight

`scripts/check_governed_history_scope.py` checks whether an input policy declares
exact, constrained read coverage for the required namespaces. An input contains
only `policy` and `requirements`; each requirement has a fixed safe label, exact
read action/resource/leading key, projected attributes and the required Query
select mode. Keep real account/opaque namespace inputs private, outside Git.

```sh
python -B scripts/check_governed_history_scope.py /private/path/scope-input.json
```

Exit0 means only that declared coverage is present; exit1 reports missing fixed
requirement labels; exit2 reports malformed/unqualified input. No SDK, credential,
secret, network, policy mutation or endpoint adapter exists in this checker.
Its output never includes supplied namespace values. Wildcard grants do not
qualify as exact coverage. Explicit denies, permission boundaries, SCPs and live
AWS behavior are not evaluated: a passing result is not effective IAM permission
or an endpoint acceptance pass.

Validate locally with the source suite below. The existing main-only authority
job includes the new suite; triggers, permissions and publishers are unchanged.

```sh
python -B -m pytest -q tests/governed_history_scope
```

## Exact correction prepared; not applied by this source change

The private before/after proposal adds only the selected account to five existing
`ForAllValues:StringEquals` / `dynamodb:LeadingKeys` arrays:

| Existing read | Namespace union |
| --- | --- |
| Projected profile GetItem | Original user plus selected user |
| Projected device GetItem | Original user plus selected user |
| Deletion-marker GetItem | Original account plus selected account |
| Authority/inventory GetItem | Existing control/original partition plus selected partition |
| Governed index Query | Original partition plus selected partition |

All original namespaces, actions, resources, projected attributes, Query
`SPECIFIC_ATTRIBUTES`, secret scope and explicit mutation/scan/provider denies
remain unchanged. There is no new role, policy resource, wildcard, data write,
retention change, grant, trial reset, purchase or broader rollout.

The earlier explicit no-IAM instruction excludes applying this proposal.
Action-time owner approval for this identified security impact remains a
prerequisite. Do not change Lambda authorization to bypass a data-read denial,
mask it as an empty History, or claim that this offline guard fixed the live503.

## Retained live fix and retest steps

1. Obtain the exact scope approval, then independently recheck the private
   before/after policy digests, current role document and protected configuration.
2. Apply only the reviewed selected-namespace union through the established
   coordinated operator. A changed document/namespace/key inventory must stop the
   old plan rather than silently broadening it. Preserve the original document
   for rollback; no IAM mutation is supplied by this offline checker.
3. Read back the exact document and unchanged actions/resources/projections/deny
   clauses. Source coverage is a prerequisite, not the endpoint result.
4. Android remains sole UI operator on the already approved emulator/session.
   Reuse the retained bounded History-only retry approval: one supported list
   read, with detail only if an actual owned result reference is returned. An
   empty list is valid. No duplicate approval, new registration, message Submit,
   funding action, credentials or Google operation is bundled into this retry.
5. Corroborate its closed route/status window without attributing aggregates to a
   request/account. Observe the actual UI, not HTTP200 alone. Keep the prior503
   and all original limits.
6. If rollback is selected, restore the captured original inline document only
   after confirming no intervening drift. Do not overwrite another deployment.
   No data, allowance or device-binding state is reversed by this rollback.

Bug deduplication found the existing legacy read/mutation classification bugs
SECUR4ALL-344/345 do not own this mismatch. A sanitized new bug draft is retained
locally; publication was blocked by automatic approval review. No tracker issue
or completion state is claimed. SECUR4ALL-191 remains the readiness parent;
SECUR4ALL-338/339 remain the existing governed source/component references. The
actual correction and real endpoint retry remain open prerequisites. Later
assembled release/physical qualification remains separate and unperformed.
