# Shared bounded sanitizer profile

`sanitizer-v1-2026-09-21` supplements the immutable governed message candidate.1
and candidate.2 artifacts. It does not replace their schemas, policy, risk or
accounting semantics. Android and iOS consume the same `profile.json`, curated
`fixtures.json` and `SHA256SUMS`; iOS implementation remains a separate task.

The 41 synthetic EN/ES examples are independently curated format expectations,
not a second implementation used as its own oracle. They cover pasted text, OCR,
mixed provenance, nine entity types, normalized repeated values, overlaps,
malformed formats, URL punctuation, Unicode and reserved-placeholder collisions.
Run each input through the local sanitizer and compare exact text and ordered
unique declarations; for `review_required`, do not prepare or submit. A `ready`
local draft with `governedAccepts:false` is not accepted by the governed service:
edit before a successful governed check. Client display readiness does not
authorize provider work; those cases do not add a new client classifier.
The backend deliberately cannot reconstruct original values to verify local
normalization: originals never belong in declarations or logs.

Reject over 12000 input code points before normalization. Follow the exact scalar
normalization and overlap order in the profile; do not substitute UTF-16 units or
grapheme counts. Governed text remains <=8000 code points and the entire encoded
request <=32768 UTF-8 bytes; <=100 unique declarations, with no truncation. Tokens
are per-type first-use numbers, reused only for normalized duplicates within one
submission. Preserve email local-part case and case-sensitive URL components.
Card and cryptocurrency matching are conservative format masking, not validity,
ownership, victim/attacker classification or proof of anonymity. Local matching
is bounded by the input cap; URL punctuation trimming must avoid repeated scans
of a shrinking string. Platform performance/device acceptance belongs to the
mobile story, not these Python contract tests.

Freeze the reviewed text, entities, source and speaker together; edits require a
new review identity. Initial mobile submissions with URL tokens withhold links
and send no reviewedLinks. Preserve the original contract/check/proof when
recovering a dispatched check. Do not renormalize old receipt identities.

## Additional runtime privacy boundary

`shared_message_contract/privacy.py` rejects complete residual IPv6 addresses,
including mapped IPv4 tails and zone suffixes, before new check allocation,
allowance/provider work and direct evaluator/vendor projections. The consumer
still counts authenticated abuse attempts, including privacy failures. Its
immutable validator and receipt hashing are unchanged. This guard is not a full
PII detector: malformed, partial, obfuscated and unrecognized identifiers remain
outside its stated coverage. Ordinary times, hex words and punctuation are
negative controls. Android masks supported complete formats; unsupported mapped
or zone literals require editing, while a whole masked URL is safe to project.

Contentless reconciliation reads historical receipts without revalidating raw
text. Replaying old raw content now rejected by this guard returns a privacy
error with unknown accounting; recover using the original contentless reconcile
identity. The stored outcome/charge is neither rewritten nor charged again.

The guard changes executable evaluation profile hashes, not the prompt/schema.
`evaluation-profile-identities.json` records the new pins. Historical playbook-r1
and compatibility packets stay historical: regenerate any future experiment
proposal from the integrated source. This artifact does not authorize execution:
SECUR4ALL-323 retains the owner-approved compatibility scope pending evidence and
a concrete manifest. `paidExecutionAuthorized:false` applies to this artifact,
not a revocation of that scope. No qualification is granted, and both
approval/qualification registries remain empty.

## Reproduce and verify

From the repository root, use Python 3.14 with the existing test dependencies:

```
python -m pytest -q tests/sanitizer_contract tests/conversation_analysis
AMT_AUTHORITY_INTEGRATION=1 python -m pytest -q tests/message_consumer/test_integration.py
```

Verify each SHA256SUMS relative to its own directory. The separate legacy
`contracts/analysis/1.0-wire-v1` supplement covers `/analysis` and its 65536-byte
wire correction. This source handoff does not deploy it; SECUR4ALL-222 remains
the legacy deployment gate. Governed runtime deployment and real model quality
qualification also remain separate. No infrastructure resource/IAM change is
required by this source increment.
