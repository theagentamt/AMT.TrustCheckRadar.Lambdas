# SECUR4ALL-191 component reconciliation

This is a source and retained-evidence manifest for the Android-first V1 plan.
It does not enable a feature, select a new artifact, prove current credentials,
or authorize a deployment. Its machine-readable companion is
[`SECUR4ALL-191-COMPONENT-MANIFEST.json`](SECUR4ALL-191-COMPONENT-MANIFEST.json).
The service observations were retained at **2026-10-17 21:22:59 UTC**. The
reconciliation performs no service, emulator, provider or application request.

## Three separate baselines

| Baseline | Exact source | Meaning |
| --- | --- | --- |
| Lambda release | `d15f5bcb5efbc76008f0d0e7c09f1ac086865236` | Current integration source, including SEC341's docs-only publication correction. Its merge does not run main CI or change the deployed publisher. |
| Lambda main | `8ecc79678fd1f87c575c43f7f4ffb856869db48d` | Contains governed History/rules source absent from release; it is not an interchangeable whole-tree runtime source. |
| Infrastructure main | `f1c5d36d565d1f6eb937d957605dc235f7867780` | Reviewed infrastructure history and contracts; checked-in defaults are not the selected live workflow inputs. |
| Infrastructure release | `353303b657dfe3e44eda59cd8ed343284880cf69` | A divergent integration history; no blind merge or apply is proposed. |
| Android audit release | `2c8f97548d13fb8b5503bd22cf54b5543136310a` | Audit source before the independent native-fix batch. |
| Installed Store13 source | `a61bae79be9c8a587c87d2fc87aa00f3d6a48e83` | Frozen canonical internal billing build; later helpers and documents are not new installed app code. |

The JSON records merge bases and unique-commit counts as history facts, not
component completeness. Android's `app/` subtree is byte-equivalent between
the installed build source and the audit release. This says nothing about
future native changes. Main-only governed History/rules code must be reviewed
against the release's billing overlays before source integration. Copying an
older publisher or rebuilding every component from either head could discard
qualified changes.

## Retained Dev runtime

The JSON records all 13 function names, unweighted live versions, exact Lambda
CodeSha256 values, private-configuration fingerprints and fixed Boolean flags.
It omits raw environment values, selected subjects and private resource IDs.

| Component | Retained live version | Qualification and limit |
| --- | --- | --- |
| Google Play handoff | 10 | Exact published archive downloaded and matched. Foreground expired-resubscription source overlay; actual new-root/ACK/paid-access acceptance remains open. |
| Authoritative access | 18 | Admission-diagnostics overlay preserved; this does not qualify every access or lifecycle case. |
| Play token deletion | 17 | Existing retention/deletion-clock overlay retained; no cleanup selection changed here. |
| V1 authority deletion | 24 | Existing deletion-clock overlay retained, with a different archive and handler. |
| Governed History | 2 | Retained qualified Dev list/detail/authority; dedicated account scope remains private and unchanged. |
| Message consumer / evaluator | 7 / 7 | Retained rules-only path and governed settlement; AI/proposer qualification remains closed. |
| URL assessment / resolver | 3 / 2 | Runtime fingerprints known; full archive composition is not revalidated in this reconciliation. |
| URL consumer / lease recovery | 9 / 11 | Consumer/authority and lease sweep remain closed in the retained configuration. |
| Play lifecycle ingress / worker | 2 / 1 | Lifecycle/authority/preparation remain closed; no background Google lifecycle acceptance. |

Handoff10 has preparation, authority, foreground handoff, test-only and catalog
flags true; lifecycle is false. Those flags are not global account admission.
The selected private lists and ownership predicates still constrain each
component independently. A combined Android build must not union those lists.

Handoff10 is a **mixed-source archive**, SHA-256
`e91716c17d9dc66f3956452e5003d75e6c6e65ffc43c2f77d89c606c81cd8206`:
six changed entries and 603 preserved entries. It is not an archive wholly
rebuilt from implementation head `55f42bdd9bcb18e2c2966ff02aba481f4dfd92e3`.
[The deployed record](dev-play-expired-resubscription-deployed.json) identifies
the exact readback, rollback9 and protection evidence. No rollback ran.

Access18 is also an overlay, archive SHA-256
`ba9d1efbe25cdf2e6854e43da1a3c1df0d74462a2275f3d58355038a5255865a`.
[Its deployment record](dev-purchase-admission-diagnostics-deployed.json)
supersedes the earlier local-only candidate status without changing unrelated
entries. Cleanup17 and authority-deletion24 combine baseline
`39cce61623794a123e61d25f5248b0c081eccf4a` with the narrowly reviewed service
override `9123459a5c2bc9503d56235b2cb1f1e162c00da1`. The infrastructure
`docs/evidence/sec332-deletion-packages.json` records both archives. Older
readback version numbers must not replace the current 17/24 selection merely
because their code hashes match.

History and message provenance points to Lambda
`6d9ae503b7d4e36a16a4cfc6bee2c939b988c723`, main CI `37142525442`, publication
`37142703658` and the retained infrastructure journey evidence. No archive was
downloaded again for that group in this reconciliation. Remaining components
have explicit fingerprint-only provenance rather than an invented full-source
commit. Private workflow artifact versions and overlays must be resolved before
any later deployment; stale default tfvars are not a substitute.

## Android profile and contract packet

Store13 is non-debuggable, installed by `com.android.vending`, with four
previously verified APK signatures. Its referenced authentication resource has
SHA-256 `bec0ae5acf3dc4a804281ce34caaaf5759ba41e47039ea19c99bc8c72869bf52`.
The JSON pins the unsigned/signed bundle, provider APK, delivered DEX and public
certificate. Those are retained installation observations, not a current device
or session read. The Dev test control is compiled in; its earlier normal
startup and separate visible Unarmed observation are not billing acceptance.

The canonical internal billing profile intentionally rejects combining current
authority, governed History and demographic research switches. Keep that guard.
The native picker/QR/share/rotation fixes can proceed without changing it. A
later integrated Dev profile requires separate reviewed composition and actual
adapter/contract checks. Do not enable every flag, reuse this profile as a UAT
candidate, or select a new version here.

Eight source-file pins in the JSON anchor message candidate3, URL transport
candidate2 and assessment candidate1, governed History candidate1, access
schema1, Play handoff candidate1 and built-in recovery selection1.0.0. History's
contract is currently pinned to main because it is absent from release. These
are source contracts, not a claim that every client adapter or route is enabled.
The standalone URL path does not require a prior message. Rules-only behavior
does not prove AI/provider quality. Built-in recovery is not an analysis check.

## Work still required

1. Reconcile main-only governed implementation and release overlays by component;
   retain exact contract tests and packaged-entry provenance. A whole-tree merge
   or whole-stack redeployment is not the plan.
2. Review the later integrated Android Dev profile with genuine native adapters,
   fixed source/artifact/configuration pins and private scope isolation. Validate
   locally before separately authorized signing/distribution and emulator tests.
3. Inventory UAT/Production resources through an authorized fresh service session
   before proposing only missing or changed dependencies. Neither target is
   qualified here. Missing mobile adapters do not prove absent AWS resources.
4. Review immutable deployment selection, environment-specific auth/API/data,
   secrets references, throttles, ownership, rollout gates and rollback before
   promotion. This document introduces no new secret or permission.
5. Keep URL-retention approval and provider/AI quality decisions explicit; do not
   activate those experiments or infer approval from their existing source.
6. Preserve the five spent subscription journals. No live transaction follows
   from this manifest. ATCR112/114/SEC195 retained acceptance remains open;
   physical-only testing stays with ATCR148. iOS is paused for this Android-first
   plan, not an Android launch prerequisite.

SEC191 remains unfinished: retained Dev component evidence and this source
reconciliation do not establish isolated UAT inventory, assembled Dev/UAT
behavior or Production readiness. Existing release acceptance remains tracked;
no criterion is waived and no new deployment is performed.
